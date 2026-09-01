# BrlMultiline: reading a table as rows of cells.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Blocks that are table rows, read cell by cell out of a browse mode document.

Everything about finding the cells is NVDA's. `documentBase.DocumentWithTableNavigation`
already knows how to say which cell a position is in, how big the table is, and how to fetch
the cell at any coordinate — it is what NVDA's own control+alt+arrows move through, and what
the Easy Table Navigator add-on binds the plain arrow keys to. This module walks that API and
turns its answers into blocks. It reimplements none of it, and the one place it was tempted
to — deriving the table's shape by reading fields itself — is exactly what `_getTableDimensions`
already does better.

**A block is a row.** Not a cell: a cell on its own is what a single line display shows and
what the reader is trying to get away from. Not the whole table either, because a table is
longer than any band and the window's whole design is a stream of blocks. One row, holding
its cells, drawn at the column plan's offsets by `FlowRenderer._asColumns`.

**A cell's braille is its text.** The cells are read as plain text regions rather than as
text-info regions over the document. That is deliberate and it is the difference between a
cell and a line: a text-info region reads a *reading unit* around a position, so a cell in a
one-line row would come back as the whole row, and a cell in a wrapped row as whatever
fraction of it the unit happened to cover. What a column holds is the cell's own text, so the
cell's own text is what is translated.

Routing keeps the connection to the document that plain text would lose: a routing key over a
cell puts the caret in that cell, which is where the reader wants to be if they are about to
do anything with it.

**A row that is missing a cell is normal.** `_getTableCellAt` raises `LookupError` for a
coordinate with no cell, which is what a merged cell looks like from the outside and what a
half-built row looks like from anywhere. The row is built without it, that column is left
blank, and every other column stays where the reader's finger left it.
"""

import dataclasses
import functools
from typing import Any, Optional

import documentBase
from braille.regions.base import Region, TextRegion
from logHandler import log

from . import flowObjectTable
from .flow import BlockId, FetchResult, SourceBlock
from .flowSources import FetchBudget
from .flowTable import SEPARATOR_CELL, Measurement, RowCell, cellPosition, positionParts

UNIT = "row"
"""What a block is here, for the log and for the dry run's report."""

HEADER_ROW = 1
"""Which row of a table to fall back to for its column headers.

**The fallback, not the answer.** The answer is L{declaredHeader}: a document that marks its
headers up says what each column's header is, per cell, and NVDA has already worked it out —
see that function for where from. Row one is what is left for a table that declares nothing,
which is a table whose headers this cannot know and can only guess at.

Still row one for the other two questions that need *a* row rather than a header: the sample
`measure` reads, and the probe `hasGrown` makes past the last known column.
"""

HEADER_ATTRIBUTES = {"column": "table-columnheadertext", "row": "table-rowheadertext"}
"""What NVDA calls a cell's header text, per axis, on the control field it hands out."""

HEADER_TRIES = 3
"""How many of a column's cells are asked what its header is, before the column has none.

More than one, because the cell that happens to be read first is not always a witness: a
half-built row of a virtualised list, or a merged cell where its neighbours hold real ones,
answered for the whole column and the column then had no name at all. Few, because a table
that declares nothing pays this on every column of the sample and the fields are a read of
the document.
"""

MEASURE_ROWS = 8
"""How many rows are read to decide the column widths.

A band's worth. The same principle the indent plan uses — plan from what the reader is about
to feel — and the same reason not to read more: a table of four thousand rows would cost four
thousand reads to answer a question a bandful answers well enough. Where it is wrong it is
wrong by a cell or two and truncation covers it, which is what truncation is for.
"""


def tableDocumentFor(obj) -> Optional[Any]:
	"""What can navigate the table this object is in.

	The tree interceptor where there is one and browse mode is presenting the page, the object
	itself where it navigates its own tables, and otherwise a list view presented as though it
	did. Taken from Easy Table Navigator, which uses the same dispatch for the same reason:
	it is what makes a list view the same code as a web page, and the bet it was making turned
	out to be worth making — everything above this line is unchanged by the message list
	arriving.

	The order matters. A list view inside a browse mode document is part of the page, and the
	page is what the reader is reading; a list view is asked about only where nothing else
	claims the object.

	:param obj: the object the reader is on.
	:return: something answering `DocumentWithTableNavigation`'s three questions, or None.
	"""
	if obj is None:
		return None
	try:
		interceptor = getattr(obj, "treeInterceptor", None)
		if isinstance(interceptor, documentBase.DocumentWithTableNavigation):
			# `passThrough` is browse mode standing aside for a control the reader is working
			# in. The page's table navigation is not what is wanted while they are typing in a
			# text box inside one of its cells.
			return None if getattr(interceptor, "passThrough", False) else interceptor
		if isinstance(obj, documentBase.DocumentWithTableNavigation):
			return obj
	except Exception:
		log.debugWarning("Could not tell whether this document navigates tables", exc_info=True)
		return None
	return flowObjectTable.tableFor(obj)


@dataclasses.dataclass(frozen=True)
class TableHandle:
	"""A table the reader is in, and where in it they are."""

	document: Any
	"""What navigates it. See `tableDocumentFor`."""

	tableID: Any
	"""NVDA's identifier for this table within its document."""

	numRows: int
	numCols: int
	row: int
	"""The row the caret is in, one based, as the table numbers it."""

	col: int
	"""The column the caret is in, one based."""

	@property
	def key(self) -> tuple:
		"""What names this table, and names it apart from every other one.

		The document **and** the identifier, because NVDA's table identifier is only
		documented as unique within a document: a virtual buffer numbers its tables from one,
		so the first table of every page is table 1. Comparing identifiers alone said the
		watchlist on one page and the layout table on the next were the same table, and a
		focus change straight from one into the other carried the reader's column layout with
		it.

		:return: a key to compare with `sameTable`, which knows how to compare each half.
		"""
		return (self.document, self.tableID)

	def __repr__(self) -> str:
		return f"<TableHandle {self.numRows}x{self.numCols} at row {self.row} column {self.col}>"


def tableAt(obj) -> Optional[TableHandle]:
	"""Whether the reader is in a table, and which one.

	The test Easy Table Navigator uses, for the reason it uses it: `_getTableCellCoords`
	answers the question by doing the work, so a test that agreed with it would be a second
	implementation to keep in step and a test that disagreed would offer a layout the fetches
	then could not fill.

	**A layout table answers no, and that is NVDA's doing rather than ours.**
	`_getTableCellCoords` consults `_maybeGetLayoutTableIds` and skips any table marked as
	layout unless the reader has turned NVDA's own "include layout tables" setting on. Most
	tables on a web page are page scaffolding, and this is what stops a column layout being
	aimed at one by accident.

	:param obj: the object the reader is on.
	:return: the table, or None if they are not in one.
	"""
	document = tableDocumentFor(obj)
	if document is None:
		return None
	try:
		where = document.selection
		cell = document._getTableCellCoords(where)
		numRows, numCols = document._getTableDimensions(where)
	except (LookupError, OSError):
		# Not in a table, or the document could not be asked. `OSError` covers the
		# `WindowsError` that a document going away underneath the question raises, which
		# Easy Table Navigator catches for the same reason.
		return None
	except Exception:
		log.debugWarning("Could not read the table at the cursor", exc_info=True)
		return None
	if numRows < 1 or numCols < 1:
		return None
	return TableHandle(
		document=document,
		tableID=cell.tableID,
		numRows=numRows,
		numCols=numCols,
		row=cell.row,
		col=cell.col,
	)


def explain(obj) -> list:
	"""Why `tableAt` answered as it did, in the terms it answered in.

	`tableAt` swallows the three ways of saying no — nothing navigates a table here, the
	cursor is not in a cell of it, it will not say how big it is — because for the ordinary
	redraw they are all the same answer and none of them is news. For the reader pressing the
	column command and being told they are not in a table they are the only news there is, so
	the same questions are asked again, one at a time, with nothing swallowed.

	Asked again rather than recorded on the way past, deliberately: the answers are what the
	reading itself asks for and a record kept beside them is a record that can go stale. It
	costs a second look at a control the reader is standing still in, which is a price only
	paid when something has already gone wrong.

	:param obj: the object the reader is on.
	:return: one line per question, for the log.
	"""
	lines = [f"Looking for a table at {flowObjectTable.describeThing(obj)}"]
	try:
		document = tableDocumentFor(obj)
	except Exception as error:
		lines.append(f"  nothing could be asked about it: {error!r}")
		return lines
	if document is None:
		lines.append(
			"  nothing here navigates a table: not a browse mode document, not a document"
			" that navigates its own, and not a control this can present as one",
		)
		lines.extend(flowObjectTable.explain(obj))
		return lines
	lines.append(f"  navigated by {document!r}")
	if isinstance(document, flowObjectTable.ObjectTable):
		# The tree around the object, which a browse mode document has no equivalent of and
		# does not need: NVDA's own answer is the whole of that one. A stand-in was *chosen*
		# out of a tree that allowed more than one reading of it, and the reader's Outlook
		# report was a wrong choice that every number below this line then described
		# faithfully. Which reading was taken is the half that was missing.
		lines.extend(flowObjectTable.describeTree(obj))
	where = None
	try:
		where = document.selection
	except Exception as error:
		lines.append(f"  which will not say where the cursor is: {error!r}")
	if where is not None:
		try:
			cell = document._getTableCellCoords(where)
			lines.append(f"  the cursor is in row {cell.row}, column {cell.col} of {cell.tableID!r}")
		except Exception as error:
			lines.append(f"  the cursor is not in a cell of it: {error!r}")
		try:
			numRows, numCols = document._getTableDimensions(where)
			lines.append(f"  it says it is {numRows} rows by {numCols} columns")
		except Exception as error:
			lines.append(f"  it will not say how big it is: {error!r}")
	describe = getattr(document, "describe", None)
	if callable(describe):
		# An object table only. A document is NVDA's own answer throughout and has nothing to
		# add about how it arrived at one. See `flowObjectTable.ObjectTable.describe`.
		try:
			lines.extend(describe())
		except Exception as error:
			lines.append(f"  it could not describe itself: {error!r}")
	return lines


def logExplanation(obj, why: str) -> None:
	"""Write down why a table could not be laid out, since the reader is being told it cannot.

	At `info` rather than `debug`, and that is the point of it: the reader who hits this is
	standing in front of hardware with a command that has just refused them, and asking them
	to turn debug logging on and do it all again is asking them to reproduce something they
	have already reproduced.

	:param obj: the object they are on.
	:param why: what was being attempted, for the first line.
	"""
	try:
		log.info("\n".join([f"BrlMultiline: {why}.", *explain(obj)]))
	except Exception:
		log.debugWarning("Could not say why a table could not be laid out", exc_info=True)


class TableRow(Region):
	"""One row of a table, read as its cells rather than as a line of text.

	**A real region, and that is the whole of the lesson here.** It began as a stand-in — an
	object carrying the handful of attributes the code that draws a flow happens to ask for —
	and that worked until the row reached NVDA's own buffer, which asked it for
	`hidePreviousRegions` and got an `AttributeError` in the middle of a display update. The
	list of things a region has is NVDA's to know and it is longer than any guess. Subclassing
	means never guessing again, and it is why this class lives here rather than beside the
	column arithmetic in `flowTable`, which imports nothing from NVDA on purpose.

	What it adds to a region is the cells: `FlowRenderer._asColumns` asks for them and draws
	each at its column's offset. Everything that has *not* been taught about columns — NVDA's
	buffer, the dry run, a log line — reads `rawText` and `brailleCells` and gets the row run
	together as one line, which is reading order and is the default a table gets.
	"""

	def __init__(self, cells, separator: str = "  ", obj=None, caretColumn=None) -> None:
		"""
		:param cells: the row's cells, in the table's own column order.
		:param separator: what goes between them in the flat reading. Two spaces, which is
			what a reader expects between fields and what NVDA's own table reading uses.
		:param obj: the document this row was read out of. **Load bearing**, and the least
			obvious thing in this file: `BrailleHandler.handleCaretMove` looks at the last
			region in the buffer and does nothing at all unless its `obj` equals the object
			whose caret moved. A row that did not know its document was a row NVDA never
			queued, so the caret moved and no redraw followed — and with no redraw there was
			no `recheck`, so the band could not notice the reader had walked out of the
			table either. It sat on the first table it was given until NVDA redrew for some
			other reason.
		:param caretColumn: asked, on each update, which column of *this* row the caret is
			in, or None for a row it is not on. Asked rather than told because a row is
			re-read by NVDA at moments this source does not choose, and a cursor set once
			and remembered is a cursor that stays where the caret used to be.
		"""
		super().__init__()
		self.cells = tuple(cells)
		self.separator = separator
		self.hidden = False
		self.obj = obj
		self.caretColumn = caretColumn
		self.update()

	def cellFor(self, index: int):
		""":return: the cell in one column of the table, or None if the row has not got it.

		A row genuinely may not: a table with a merged cell, or one still being built, has
		rows with fewer cells than the header promised. Drawing nothing in that column is the
		honest answer, and it keeps every other column where the reader left it.
		"""
		for cell in self.cells:
			if cell.index == index:
				return cell
		return None

	def update(self) -> None:
		"""Read every cell and run them together into this region's own text and braille.

		Assembled from the cells rather than translated afresh. They have been translated
		already by whatever read them, and translating the joined text a second time would be
		a second answer that could disagree with the first about where a cell begins — which
		is the same reason the columns are drawn from each cell's own translation.

		The position maps are concatenated with them, because NVDA's buffer uses them to turn
		a window of cells back into text and a routing press into a place. A row that carried
		cells and no maps read as an empty window.
		"""
		rawText = ""
		brailleCells: list[int] = []
		rawToBraillePos: list[int] = []
		brailleToRawPos: list[int] = []
		for index, cell in enumerate(self.cells):
			if index:
				rawStart, cellStart = len(rawText), len(brailleCells)
				for offset in range(len(self.separator)):
					rawToBraillePos.append(cellStart + offset)
					brailleToRawPos.append(rawStart + offset)
				rawText += self.separator
				brailleCells.extend([SEPARATOR_CELL] * len(self.separator))
			rawStart, cellStart = len(rawText), len(brailleCells)
			text, drawn, forward, back = _readCell(cell.region)
			rawToBraillePos.extend(cellStart + position for position in forward)
			brailleToRawPos.extend(rawStart + position for position in back)
			rawText += text
			brailleCells.extend(drawn)
		self.rawText = rawText
		self.brailleCells = brailleCells
		self.rawToBraillePos = rawToBraillePos
		self.brailleToRawPos = brailleToRawPos
		self.brailleCursorPos = self._caretPosition()

	def _caretPosition(self):
		"""Where to draw the cursor in this row, in this region's own position space.

		**Packed, not an index into `brailleCells`**, which is what `brailleCursorPos`
		usually is. This region's positions are packed — which column, and where in it — and
		the cursor has to be in the same space as them or nothing can find it: the flow looks
		for the band cell whose position equals this one, and the flat reading's indices name
		cells of a row nobody is looking at. Nothing hands this number to NVDA's own cursor
		arithmetic, because the band computes its own cursor from the drawn rows.

		At the start of the cell rather than at the character the caret is on. What the
		reader lost and asked for is *which cell*, the offset inside it is not something
		`_getTableCellCoords` reports, and working it out would mean measuring the caret
		against the cell's own start on every redraw.

		:return: the packed position, or None if the caret is not on this row.
		"""
		if self.caretColumn is None:
			return None
		try:
			column = self.caretColumn()
		except Exception:
			log.debugWarning("Could not tell which cell the caret is in", exc_info=True)
			return None
		if column is None or self.cellFor(column) is None:
			return None
		return cellPosition(column, 0)

	def textForPositions(self, positions) -> str:
		"""What a run of drawn positions reads as, for the log and the dry run.

		Column by column, and only as much of each column as was drawn — which is the point.
		A truncated column is the reader's whole complaint about a table too wide for the
		band, and a report that showed the text a column *holds* rather than the text it
		*shows* could not be used to check the widths at all.

		:param positions: the packed positions on one drawn row, padding already dropped.
		:return: the row as it reads, cells separated as they are in the flat reading.
		"""
		runs: list[tuple[int, list[int]]] = []
		for position in positions:
			column, offset = positionParts(position)
			if self.cellFor(column) is None:
				continue
			if not runs or runs[-1][0] != column:
				runs.append((column, []))
			runs[-1][1].append(offset)
		return self.separator.join(
			_sliceByBraille(self.cellFor(column).region, offsets) for column, offsets in runs
		)

	def routeTo(self, braillePos: int) -> None:
		"""Put the cursor where a finger landed.

		:param braillePos: a packed position from a drawn row. See `flowTable.cellPosition`.
		"""
		column, offset = positionParts(braillePos)
		cell = self.cellFor(column)
		if cell is None:
			return
		route = getattr(cell.region, "routeTo", None)
		if route is not None:
			route(offset)

	def __repr__(self) -> str:
		return f"<TableRow {len(self.cells)} cells>"


def _sliceByBraille(region, positions) -> str:
	""":return: the text a run of one region's braille positions stands for.

	Through the region's own `brailleToRawPos`, which is where contraction is accounted for:
	six cells of a contracted word are not six characters, and slicing the text by cell count
	would report something the reader is not feeling.
	"""
	text = getattr(region, "rawText", "") or ""
	if not text or not positions:
		return ""
	mapping = getattr(region, "brailleToRawPos", None)
	if not mapping:
		return text[positions[0] : positions[-1] + 1]
	try:
		start = mapping[positions[0]]
		after = positions[-1] + 1
		end = mapping[after] if after < len(mapping) else len(text)
	except IndexError:
		return text
	return text[start:end]


def _readCell(region) -> tuple:
	"""What one cell contributes to its row's flat reading.

	The maps are rebuilt as straight runs where a region does not offer usable ones, so that
	their lengths always match the text and the cells they map. NVDA reads them by index and
	guards against running off the end, but a map of the wrong length maps to the wrong place
	rather than to nowhere, which is worse.

	:param region: the cell's region.
	:return: its text, its cells, its raw to braille map, and its braille to raw map.
	"""
	text = getattr(region, "rawText", "") or ""
	drawn = list(getattr(region, "brailleCells", None) or ())
	forward = list(getattr(region, "rawToBraillePos", None) or ())
	back = list(getattr(region, "brailleToRawPos", None) or ())
	if len(forward) != len(text):
		forward = [min(position, max(0, len(drawn) - 1)) for position in range(len(text))]
	if len(back) != len(drawn):
		back = [min(position, max(0, len(text) - 1)) for position in range(len(drawn))]
	return text, drawn, forward, back


class TableCellRegion(TextRegion):
	"""One cell of a table, translated as the text it holds.

	A `TextRegion` rather than a `TextInfoRegion`, which is the difference between a cell and
	a line — see the module docstring. What it adds to a plain one is the way back: it keeps
	the cell's position, so a routing key over the cell can put the caret in it.
	"""

	def __init__(self, document, info, live: bool = False) -> None:
		"""
		:param document: what navigates the table, for moving the caret.
		:param info: the cell's position, as `_getTableCellAt` returned it.
		:param live: whether this flow moves the reader's own cursor. A viewer routes
			nowhere, exactly as it reads nothing else into the document.
		"""
		super().__init__(_textOf(info))
		self.document = document
		self.info = info
		self.live = live
		self.obj = document

	def routeTo(self, braillePos: int) -> None:
		"""Put the caret in this cell.

		In the cell rather than at the character the finger landed on. A table cell is a place
		in the document and its text is a rendering of what is there; the offsets inside it are
		this region's, not the document's, and pretending otherwise would put the caret
		somewhere arbitrary in a merged or reformatted cell. What the reader means by routing
		into a column is "take me to this value".

		:param braillePos: where in this region's cells the finger landed. Not used, for the
			reason above; taken so that this is the region interface everything else expects.
		"""
		if not self.live:
			return
		try:
			where = self.info.copy()
			where.collapse()
			where.updateCaret()
		except Exception:
			log.debugWarning("Could not put the caret in a table cell", exc_info=True)

	def __repr__(self) -> str:
		return f"<TableCellRegion {self.rawText!r}>"


def declaredHeader(info, axis: str = "column") -> str:
	""":return: the header a document declares for a cell's column or row, or "" if it declares none.

	**Browse mode knows this, and it is better than anything that can be worked out from the
	table's shape.** The virtual buffer backend asks IAccessible2 for a cell's header cells —
	`IAccessibleTableCell::get_columnHeaderCells`, which is what `<th>`, `scope=` and
	`headers=` produce — and records their identifiers on the cell's node;
	`VirtualBuffer._normalizeControlField` then resolves those to text as
	`table-columnheadertext`. The same attribute arrives from UIA and from Word by other
	routes, so a document, a spreadsheet and a page all answer the same question the same way.

	What that buys over reading row one is every case row one gets wrong: headers two rows
	deep, a header column rather than a header row, a `headers=` attribute pointing somewhere
	else entirely, and a table whose first row is data. None of those can be guessed at from
	the outside, and all of them are already answered here.

	:param info: a cell's position, as `_getTableCellAt` returned it.
	:param axis: "column" for the header above the cell, "row" for the one beside it.
	:return: the header text, or "" where the document declares none.
	"""
	attribute = HEADER_ATTRIBUTES.get(axis)
	if attribute is None or info is None:
		return ""
	try:
		fields = info.getTextWithFields()
	except Exception:
		log.debugWarning("Could not read a table cell's fields", exc_info=True)
		return ""
	found = ""
	for item in fields:
		# The innermost wins. A cell sits inside a row and a table and the attribute is only
		# ever put on the cell, but taking the last one asked and letting the loop run is the
		# rule that stays right if that ever stops being true.
		if getattr(item, "command", None) != "controlStart":
			continue
		text = (getattr(item, "field", None) or {}).get(attribute)
		if text:
			found = text
	# Written as one line whatever the document did with it. A cell with two header cells
	# above it comes back separated by newlines, and a pinned header row is one row.
	return " ".join(found.split())


def _textOf(info) -> str:
	""":return: a cell's text, stripped of the whitespace the document pads it with.

	Stripped because a cell's leading and trailing space is layout rather than content, and a
	column that spends two of its six cells on the space a page put around a ticker is a
	column two cells narrower than it looks.
	"""
	try:
		return (info.text or "").strip()
	except Exception:
		log.debugWarning("Could not read a table cell", exc_info=True)
		return ""


def declaredHeaders(
	handle: TableHandle,
	columns,
	row: Optional[int] = None,
	tries: int = HEADER_TRIES,
) -> dict:
	""":return: the header each column declares, by column number, leaving out those that do not.

	A declared header belongs to the column, so any cell of it answers — **but only a cell that
	answers at all**, and the first row read is not always a witness. A row of a virtualised
	list may be half built and a row of a document may hold a merged cell where its neighbours
	hold real ones, and asked once, such a cell was the whole of what the column had said.
	`measure` learnt that on hardware and took a few rows instead of one; a review found this
	still asking a single row, which matters most where it is used to *name* a table — a
	transient silence there saves or looks up a layout under a different identity. So the same
	bounded strategy, in the one place all three callers share.

	Only the columns that have not answered are asked again, and the walk stops as soon as they
	all have — which for a table that declares its headings is the first row. A table that
	declares nothing pays a read per column per try, which is what `HEADER_TRIES` is small for.

	:param handle: the table.
	:param columns: the table's own numbers for the columns to ask about.
	:param row: which row to ask through first. The reader's own by default, which is a row
		that certainly exists.
	:param tries: how many rows to ask before a column has no header.
	"""
	wanted = list(columns)
	found: dict = {}
	for at in _rowsToAsk(handle, row, tries):
		for column in wanted:
			if column in found:
				continue
			region = cellRegion(handle, at, column, live=False)
			if region is None:
				continue
			said = declaredHeader(region.info)
			if said:
				found[column] = said
		if len(found) == len(wanted):
			break
	return found


def _rowsToAsk(handle: TableHandle, row: Optional[int], tries: int) -> list:
	""":return: which rows to ask for a declared header, the reader's own first.

	Theirs first because it certainly exists and is certainly built — they are standing on it.
	Then the first row and the one after theirs, which between them cover the two ways a single
	cell goes quiet: a header row that is not where the reader is, and a half-built neighbour.

	:param handle: the table.
	:param row: the row to start from, or None for the reader's.
	:param tries: the most rows to name.
	"""
	at = handle.row if row is None else row
	rows = [at]
	for candidate in (1, at + 1, at - 1):
		if len(rows) >= max(1, tries):
			break
		if 1 <= candidate <= max(1, handle.numRows) and candidate not in rows:
			rows.append(candidate)
	return rows


def headerWidth(text: str) -> int:
	""":return: how wide a header is in braille cells, by translating it.

	The only honest measurement, and the same one `measure` makes of a cell — see the module
	docstring. A declared header is a string rather than a cell of the table, so there is no
	region to ask; one is made and thrown away.
	"""
	if not text:
		return 0
	try:
		region = TextRegion(text)
		region.update()
		return len(region.brailleCells)
	except Exception:
		log.debugWarning("Could not measure a table header", exc_info=True)
		return len(text)


def firstRowIsHeadings(document) -> bool:
	""":return: whether row one of a table may be read as its headings.

	Asked of whatever is navigating the table rather than assumed. A document says nothing and
	gets the assumption `HEADER_ROW` is named for, which is what NVDA's own table navigation
	assumes too. **A list view says no**, and its first row is a file or a message.

	One reader, because two things ask it and they were disagreeing. `TableFlowSource` asked
	before pinning a header row, and so never pinned one over a list; `measure` did not ask at
	all, and took row one's text as each column's *name*. So the layout knew a message list had
	no headings while the report of it called the columns "report.docx" and "Yesterday, 4:32
	PM" — the reader's own first row, read back to them as the names of the things it is one of.

	:param document: whatever is navigating the table.
	"""
	return bool(getattr(document, "hasHeaderRow", True))


def cellRegion(handle: TableHandle, row: int, column: int, live: bool = False):
	""":return: a region for one cell of a table, or None where there is no such cell.

	None is ordinary rather than exceptional: a merged cell occupies one coordinate and leaves
	the others empty, and `_getTableCellAt` says so by raising.
	"""
	try:
		info = handle.document._getTableCellAt(handle.tableID, handle.document.selection, row, column)
	except (LookupError, OSError):
		return None
	except Exception:
		log.debugWarning(f"Could not read row {row} column {column}", exc_info=True)
		return None
	if info is None:
		return None
	region = TableCellRegion(handle.document, info, live=live)
	region.update()
	return region


def cellHasContent(handle: TableHandle, row: int, column: int, live: bool = False) -> bool:
	""":return: whether one cell of a table holds anything the reader could read.

	One cell, deliberately. `measure` reads a bounded sample and a column empty throughout it
	is left out of the layout, which is right for a column of icons and wrong for a column
	that happens to be blank in the eight rows that were looked at. Proving the difference
	needs reading the whole column, which a wide table cannot afford; asking about the one
	cell the reader has just arrived at costs a single search and answers the only case that
	matters, because a column nobody visits does not need to be drawn.

	:param handle: the table.
	:param row: the row to look at.
	:param column: the column to look at.
	:param live: whether the region built here may move the reader's cursor. It does not.
	"""
	region = cellRegion(handle, row, column, live=live)
	if region is None:
		return False
	return bool((region.rawText or "").strip())


def measure(handle: TableHandle, live: bool = False, sample: int = MEASURE_ROWS) -> list[Measurement]:
	"""Read a bandful of rows and find out how wide each column needs to be.

	In cells, by translating, because that is the only honest measurement — see `flowTable`.
	The sample starts at the caret's own row and reads forward, so that what the widths are
	decided from is what the reader is about to feel, and the header row is read whatever the
	caret's row is because it is the widest thing in many a column and it is what a pinned
	header will have to fit.

	:param handle: the table.
	:param live: whether the regions built here move the reader's cursor. They do not, and
		they are thrown away; the parameter is here so that measuring cannot quietly differ
		from reading.
	:param sample: how many rows to read.
	:return: one measurement per column of the table.

	A column found empty here is *tentatively* empty: the sample is bounded, so this cannot
	prove a column holds nothing anywhere. See `cellHasContent`, which is how the band asks
	again about the one cell that turns out to matter.
	"""
	columns = range(1, handle.numCols + 1)
	widths: dict[int, int] = {column: 0 for column in columns}
	labels: dict[int, str] = {column: "" for column in columns}
	headers: dict[int, int] = {column: 0 for column in columns}
	seen: dict[int, list[int]] = {column: [] for column in columns}
	found: set[int] = set()
	declared: set[int] = set()
	"""Which columns the table said its header for, as against which were read off row one.

	Kept because the difference matters to the source that pins a header row: what a table
	declares is drawn above the band, and what was borrowed from row one is already on it."""

	asked: dict[int, int] = {column: 0 for column in columns}
	# Asked once, before anything is read. A table that says row one is not its headings has
	# none to borrow, and borrowing them anyway names every column after the reader's own
	# first row. See `firstRowIsHeadings`.
	borrowRowOne = firstRowIsHeadings(handle.document)
	for row in _sampleRows(handle, sample):
		for column in columns:
			region = cellRegion(handle, row, column, live=live)
			if region is None:
				continue
			found.add(column)
			size = len(region.brailleCells)
			widths[column] = max(widths[column], size)
			if not labels[column] and asked[column] < HEADER_TRIES and column not in declared:
				# **Asked of more than one cell**, and that is the difference from asking
				# once. A declared header is a property of the column, so any cell of it
				# answers — but only if the cell that was asked is one that answers at all,
				# and the first row read is not always a good witness: a row of a virtualised
				# list may be half built, and a row of a document may hold a merged cell
				# where its neighbours hold real ones. Asked once, one such cell was the
				# whole of what the column had said, and the column then had no name.
				#
				# Bounded, because a table that declares nothing must not cost a read of the
				# fields on every cell of the sample. Stopped the moment one answers, which
				# for a table that does declare is the first cell.
				asked[column] += 1
				said = declaredHeader(region.info)
				if said:
					labels[column] = said
					headers[column] = headerWidth(said)
					declared.add(column)
			if row == HEADER_ROW and borrowRowOne and not labels[column]:
				# Nothing declared, so the guess. See `HEADER_ROW`.
				labels[column] = region.rawText
				headers[column] = size
			else:
				# Row one included, where the header was declared rather than borrowed from
				# it: the row is data then, and a width planned as though it were a heading
				# would be a width short by one row of the table.
				seen[column].append(size)
	return [
		Measurement(
			index=column,
			width=widths[column],
			typicalWidth=_typicalOf(seen[column]) or widths[column],
			label=labels[column],
			declared=column in declared,
			labelWidth=headers[column],
			# Not a column this table is showing, in either of the two ways that happens.
			#
			# The first is a coordinate nothing answers to: a merged cell occupies one and
			# leaves the others raising, so no sampled row had a cell there at all.
			#
			# The second is a column that answers and holds nothing, and it took the reader's
			# own watchlist to find it. Its leftmost column is icons that NVDA cannot read —
			# there is a cell, it is simply empty, in the header and in every row. Measured it
			# came to nothing, was drawn at `MIN_COLUMN_CELLS` anyway, and cost four cells of a
			# thirty-two cell band on every row for a column with nothing in it. Worse, it is
			# the *first* column, so it was also what the key column pinned to every page: the
			# thing repeated to say which row the reader was on was a blank.
			#
			# The header row is always sampled, which is what keeps the first test honest: a
			# spanning cell looks the same from outside, and a real column has a header even
			# where its body is merged away.
			hidden=column not in found or widths[column] == 0,
		)
		for column in columns
	]


def _typicalOf(sizes: list[int]) -> int:
	"""What a cell of a column usually holds, from the body rows that were read.

	The three-quarter point of the sample rather than the widest or the middle. The widest is
	what the widths used to be planned from and one long cell in ten then decided the layout
	for all ten — a VPAT remarks column where nine rows hold a few words and the tenth holds
	a paragraph laid the whole table out for the paragraph. The middle goes too far the other
	way and leaves half the rows wrapping.

	The header is not in this. It is often the widest thing in a column of numbers and it is
	drawn once, where the body is drawn on every row; it is carried separately as
	`Measurement.labelWidth`.

	:param sizes: what was found in the body rows sampled, in cells.
	:return: the typical width, or zero if no body row was read.
	"""
	if not sizes:
		return 0
	ordered = sorted(sizes)
	return ordered[min(len(ordered) - 1, (len(ordered) * 3) // 4)]


def _sampleRows(handle: TableHandle, sample: int) -> list[int]:
	""":return: which rows to measure: the header, then a bandful from the caret."""
	first = max(1, min(handle.row, max(1, handle.numRows)))
	rows = list(range(first, min(handle.numRows, first + sample - 1) + 1))
	if 1 not in rows:
		rows.insert(0, 1)
	return rows


class TableFlowSource:
	"""Blocks read out of a table, one row at a time.

	The same shape as every other source — `blockAtCursor`, `blockAfter`, `blockBefore`, a
	budget — so the window, the controller and the band do not know a table from a document.
	"""

	def __init__(
		self,
		handle: TableHandle,
		columns: tuple,
		declared: Optional[dict] = None,
		generation: int = 0,
		budget: Optional[FetchBudget] = None,
		live: bool = False,
		pinHeaders: bool = False,
	) -> None:
		"""
		:param handle: the table, and where in it the reader was when it was recognised.
		:param columns: the table's own numbers for the columns to read, in drawing order.
			Only these are fetched: reading a column the plan has dropped is a call into the
			document for something nobody will feel.
		:param declared: what each column's header is, as the measurement found it, leaving
			out the ones the table did not declare. None for a caller with no measurement to
			hand, which asks the table here instead.
		:param generation: bumped by the caller when the run is replaced, so that a row
			number from one table can never match one from another.
		:param budget: how much work a fetch may do. One is made if none is given.
		:param live: whether this flow is the reader's own.
		:param pinHeaders: whether a header row is held above the window. It decides
			L{firstRow}, and only this source can decide that, because only this source
			knows whether the pinned row is row one or something the table declared.
		"""
		self.handle = handle
		self.columns = tuple(columns)
		self._declared = {}
		"""The header each drawn column declares. Empty where the table declares none."""

		self._headersAsked = set()
		"""Which columns have been asked what their header is, answer or no answer.

		Separate from the answers because a column with no header leaves no trace in them, so
		reading the two off one dictionary asked such a column again on every refresh — a
		search of the document apiece, for a question already answered "nothing".
		"""

		if pinHeaders and declared is not None:
			# **Measured a moment ago, over a bandful of rows.** Asking here instead read one
			# cell per column, at the caret's row, and took its silence for the column's
			# answer — so a table whose first row was half built had headers everywhere in the
			# layout and no header row over them. The measurement already asks several rows
			# and already knows which answers the table *declared*, as against which it read
			# off row one; those are what arrive here. See `measure` and `flowTable.Measurement`.
			self._declared = {column: said for column, said in declared.items() if said}
			self._headersAsked = set(declared)
		elif pinHeaders:
			self._declared = declaredHeaders(handle, self.columns)
			self._headersAsked = set(self.columns)

		# Row one is skipped only when row one is what is pinned. A table that declares its
		# headers may have them two rows deep, in a column rather than a row, or nowhere near
		# the top at all — and then row one is data, and dropping it would lose a row of the
		# table to a guess the document had already contradicted.
		self.firstRow = (
			HEADER_ROW + 1 if pinHeaders and not self._declared and self._firstRowIsHeadings() else HEADER_ROW
		)
		self.generation = generation
		self.budget = budget if budget is not None else FetchBudget()
		self.live = live
		self.unit = UNIT
		self.obj = getattr(handle.document, "obj", handle.document)
		"""The object this table lives in, which is what NVDA compares a region against.

		The document itself for a page, and the list for a list view — where the thing
		answering the table questions is a stand-in presenting one, and the stand-in is not
		anything NVDA has ever heard of. See `TableRow`."""

	@property
	def row(self) -> int:
		""":return: the row the reader is on, as this source last knew it."""
		return self.handle.row

	@property
	def column(self) -> int:
		""":return: the column the reader is on, as this source last knew it."""
		return self.handle.col

	def setColumns(self, columns) -> bool:
		"""Say which columns to read from now on.

		The page being drawn, and only it. Every cell is a search of the document, so reading
		a column that is on another page is a search for something nobody will feel — and a
		twenty-nine column table read at four columns a page was doing that twenty-five times
		per row, on every row, forever.

		The rows already read are stale afterwards: they hold the cells of the old page. The
		caller re-reads them — see `FlowController.setColumnPlan`.

		:param columns: the table's own numbers for the columns to read, in drawing order.
		:return: whether they changed.
		"""
		columns = tuple(columns)
		if columns == self.columns:
			return False
		self.columns = columns
		return True

	def hasGrown(self) -> bool:
		""":return: whether this table has a column beyond the ones it was measured with.

		One cell read, on the header row, at the column after the last one known. A table that
		gained a column answers it and a table that did not raises, which is the same test
		`cellRegion` already makes for every cell it fetches.

		Asked by a pin, which cannot use the band's check: that one compares the caret's
		position against the plan, and the caret is somewhere else entirely — being somewhere
		else is what a pin is for. This asks the table rather than the reader.
		"""
		try:
			return cellRegion(self.handle, HEADER_ROW, self.handle.numCols + 1, live=False) is not None
		except Exception:
			log.debugWarning("Could not look for a column past the end of the table", exc_info=True)
			return False

	def blockAt(self, blockId: BlockId) -> FetchResult:
		""":return: one row again, by the identity it already has.

		What re-reading the band needs: the window knows which rows it is holding and wants
		those same rows read again, which for a table is the whole of what changing page
		means.
		"""
		return self._rowAt(blockId.bookmark)

	def moveTo(self, handle: TableHandle) -> None:
		"""Say where in the table the reader has moved to.

		Takes the handle rather than looking it up, because the caller that notices the move
		has just looked it up to notice it, and `_getTableCellCoords` is a read of the
		document's fields — the kind of cost that is fine once per redraw and not fine twice.

		:param handle: where they are now, in the same table.
		"""
		self.handle = handle

	def setCurrent(self, obj) -> None:
		"""Say where the reader has moved to.

		Taken from the document rather than from the object, because in a table the object
		does not move: the caret moves between cells of the same page, and asking the document
		where its selection is now is the only thing that answers.
		"""
		found = tableAt(obj if obj is not None else self.obj)
		if found is not None and sameTable(found.key, self.handle.key):
			self.handle = found

	def isStillHere(self, obj) -> bool:
		""":return: whether the reader is still in the table this source is reading.

		What the band asks before drawing again. A layout must not outlive its table: Easy
		Table Navigator clears its key bindings on every focus change for the same reason, and
		a column layout carried onto the next page is worse than no layout, because it is a
		layout of something that is not there.
		"""
		found = tableAt(obj)
		return found is not None and sameTable(found.key, self.handle.key)

	# Reading.

	def headerBlock(self) -> Optional[SourceBlock]:
		""":return: the table's header row, built for the page being drawn, or None.

		Asked for separately because it is not part of the stream: when it is pinned it is
		held above the window and this source does not serve it as content — see `firstRow`.
		Built rather than remembered, because the page decides which columns are in it.
		"""
		if self.handle.numRows < 1:
			return None
		try:
			said = self._headersForThisPage()
			if said:
				return self._declaredRow(said)
			if not self._firstRowIsHeadings():
				# A list view's first item is a file, not a heading. Pinning it would draw one
				# of the reader's own rows above the rest and claim it said what the columns
				# were, which is worse than saying nothing.
				return None
			return self._buildRow(HEADER_ROW)
		except Exception:
			log.debugWarning("Could not read the table's header row", exc_info=True)
			return None

	def _firstRowIsHeadings(self) -> bool:
		""":return: whether row one of this table may be read as its headings. See
		`firstRowIsHeadings`, which is where the question is answered."""
		return firstRowIsHeadings(self.handle.document)

	def _headersForThisPage(self) -> dict:
		""":return: the declared header of each column being drawn, leaving out those without one.

		Asked again for a column this source has not seen, which is what turning the page
		brings: the columns change and their headers are a property of the columns. Nothing
		is asked twice — including a column whose answer was that it has no header, which is
		what L{_headersAsked} is for — and nothing is asked at all for a table that declares
		nothing, since then there is nothing to look up and row one is what will be pinned.
		"""
		if not self._declared:
			return {}
		missing = [column for column in self.columns if column not in self._headersAsked]
		if missing:
			self._headersAsked.update(missing)
			self._declared.update(declaredHeaders(self.handle, missing))
		return {column: self._declared[column] for column in self.columns if column in self._declared}

	def _declaredRow(self, said: dict) -> SourceBlock:
		"""Build the pinned row out of what the table says its headers are.

		Text rather than cells, because that is what a declared header is: NVDA resolved it
		from the header cells the markup points at, and those may be anywhere in the table or
		may be several cells run together. A column that declares nothing is left blank rather
		than filled in from row one, since mixing the two would put a guess beside an answer
		and give the reader no way to tell them apart.

		:param said: the declared header of each column, by column number.
		"""
		cells = []
		for column in self.columns:
			region = TextRegion(said.get(column, ""))
			region.update()
			cells.append(RowCell(index=column, region=region))
		content = TableRow(cells, obj=self.obj)
		return SourceBlock(
			blockId=BlockId(generation=self.generation, bookmark=HEADER_ROW, unit=self.unit),
			region=content,
			isBlank=not content.rawText.strip(),
		)

	def describeHeader(self) -> str:
		""":return: where the pinned row came from, for the log.

		The line the last report needed and did not have. It showed a header row reading
		"Column left  Position" over columns the same report said were headed "Name" and
		"Status", and there was no way to tell which of the two ways a header can be built had
		produced it.
		"""
		if self.handle.numRows < 1:
			return "nothing: the table has no rows"
		try:
			said = self._headersForThisPage()
		except Exception as error:
			return f"could not be worked out: {error!r}"
		if said:
			return f"declared by the table's own cells: {said}"
		if not self._firstRowIsHeadings():
			return "nothing: this table declares no headers and its first row is not headings"
		return f"row {HEADER_ROW}, since this table declares none"

	def blockAtCursor(self, atObject=None) -> FetchResult:
		""":return: the row the reader is on.

		Clamped to the first row this source serves. With the header pinned that is row two,
		and a reader whose caret is in the header row is looking at the pinned copy of it.
		"""
		self.budget.startUnlessActive()
		return self._rowAt(max(self.firstRow, self.handle.row))

	def blockAfter(self, blockId: BlockId) -> FetchResult:
		""":return: the row below one already fetched."""
		return self._step(blockId, 1)

	def blockBefore(self, blockId: BlockId) -> FetchResult:
		""":return: the row above one already fetched."""
		return self._step(blockId, -1)

	def _step(self, blockId: BlockId, by: int) -> FetchResult:
		"""Walk one row up or down.

		Bounded by the table's own dimensions rather than by trying and failing: a table knows
		how many rows it has, and walking off the end to find out costs a search of the whole
		buffer per column to learn something already known.
		"""
		self.budget.startUnlessActive()
		row = blockId.bookmark + by
		if not self.firstRow <= row <= self.handle.numRows:
			return FetchResult.endOfStream(f"row {row} is outside this table")
		return self._rowAt(row)

	def _rowAt(self, row: int) -> FetchResult:
		""":return: a result carrying one row of the table."""
		began = self.budget.clock()
		try:
			return FetchResult.found(self._buildRow(row))
		except Exception as error:
			log.debugWarning(f"Could not read row {row}", exc_info=True)
			return FetchResult.failed(f"could not read a table row: {error!r}")
		finally:
			self.budget.observe(self.budget.clock() - began)

	def _buildRow(self, row: int) -> SourceBlock:
		"""Build one block from one row of the table.

		The row number is the bookmark, which is what makes a table cheap to walk: rows are
		numbered by the table, the numbering survives a re-read, and it is what
		`_getTableCellAt` takes. Nothing has to be remembered between fetches.
		"""
		cells = []
		for column in self.columns:
			region = cellRegion(self.handle, row, column, live=self.live)
			if region is not None:
				cells.append(RowCell(index=column, region=region))
		content = TableRow(
			cells,
			obj=self.obj,
			caretColumn=functools.partial(self._caretColumn, row),
		)
		return SourceBlock(
			blockId=BlockId(generation=self.generation, bookmark=row, unit=self.unit),
			region=content,
			isBlank=not content.rawText.strip(),
		)

	def _caretColumn(self, row: int):
		""":return: which column the caret is in on one row of the table, or None.

		None for every row but the one it is on, which is what stops each of a bandful of
		rows claiming the cursor.
		"""
		return self.handle.col if row == self.handle.row else None

	def forget(self) -> None:
		"""Kept for the shape of a source. There is nothing cached here to drop."""

	def __repr__(self) -> str:
		return f"<TableFlowSource {self.handle!r} generation {self.generation}>"


def wantsColumns(request, obj) -> bool:
	""":return: whether a request to see a table in columns is about the table an object is in.

	One place, because three callers ask it and they must not disagree: the band as it decides
	what to draw, the plugin as it pins an object, and the command as it decides whether a
	press means "on" or "off". It used to live on the band, which meant it could not be asked
	at all on a one row display — where there is no band and the reader still wants to say
	"this table, in columns" so that pinning it elsewhere carries the layout.

	:param request: the key of the table asked for, or None for no request.
	:param obj: the object the reader is on.
	"""
	if request is None:
		return False
	try:
		handle = tableAt(obj)
	except Exception:
		log.debugWarning("Could not look for the table an object is in", exc_info=True)
		return False
	return handle is not None and sameTable(handle.key, request)


def sameTable(first, second) -> bool:
	""":return: whether two table keys name the same table. See `TableHandle.key`.

	The two halves are compared differently and deliberately. The **document** is compared by
	identity, because a tree interceptor is one object for as long as its page is loaded and
	because being wrong in the safe direction here means giving a layout back rather than
	carrying it into a page it was not made for. The **identifier** is compared by value,
	because it is an integer for a virtual buffer and a tuple for UIA and neither survives
	being the same object across two reads.

	Identity is not everybody's answer, and a thing that has a better one says so by offering
	`sameAs`. A list view is presented by a stand-in built afresh each time it is looked at,
	over an `NVDAObject` that NVDA also builds afresh each time it is asked — so identity says
	"a different table" every time the reader moves to the next message, and the layout would
	be given back on every arrow key.
	"""
	if first is None or second is None:
		return False
	try:
		if not _sameContainer(first[0], second[0]):
			return False
		return bool(first[1] == second[1])
	except Exception:
		log.debugWarning("Could not compare two tables", exc_info=True)
		return False


def _sameContainer(first, second) -> bool:
	""":return: whether two table keys name the same document, page or control.

	Identity, unless the thing being compared knows better and says so. See `sameTable`.
	"""
	if first is second:
		return True
	sameAs = getattr(first, "sameAs", None)
	return bool(sameAs(second)) if callable(sameAs) else False


__all__ = [
	"MEASURE_ROWS",
	"TableCellRegion",
	"TableFlowSource",
	"TableHandle",
	"cellRegion",
	"explain",
	"logExplanation",
	"sameTable",
	"measure",
	"tableAt",
	"tableDocumentFor",
]
