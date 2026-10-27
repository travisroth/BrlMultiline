# BrlMultiline: reading a table as rows of cells.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Blocks that are table rows, read cell by cell out of a browse mode document.

Finding cells is NVDA's: this module walks `documentBase.DocumentWithTableNavigation`, the
API NVDA's control+alt+arrows use, and turns its answers into blocks.

1. A block is a row, holding its cells, drawn at the column plan's offsets by
   `FlowRenderer._asColumns`.
2. A cell is read as a plain text region, not a text-info region, which would read a whole
   reading unit around the position. Routing to a cell still puts the caret in it.
3. A missing cell, which `_getTableCellAt` reports with `LookupError` for a merged or
   half-built row, leaves that column blank.
"""

import dataclasses
import functools
from typing import Any, Optional

import documentBase
from braille.regions.base import Region, TextRegion
from logHandler import log

from . import flowObjectTable
from .flow import BlockId, CallCancelled, FetchResult, SourceBlock
from .flowSources import FetchBudget
from .flowTable import SEPARATOR_CELL, Measurement, RowCell, cellPosition, positionParts

UNIT = "row"
"""What a block is here, for the log and for the dry run's report."""

HEADER_ROW = 1
"""Which row of a table to fall back to for its column headers.

Only a fallback for a table that declares no headers; see `declaredHeader`. Also the row
`measure` samples and `hasGrown` probes.
"""

HEADER_ATTRIBUTES = {"column": "table-columnheadertext", "row": "table-rowheadertext"}
"""What NVDA calls a cell's header text, per axis, on the control field it hands out."""

HEADER_TRIES = flowObjectTable.HEADER_TRIES
"""How many of a column's cells are asked what its header is, before the column has none.

Shared with `flowObjectTable.HEADER_TRIES`, which applies the same rule to cells.
"""

MEASURE_ROWS = 8
"""How many rows are read to decide the column widths.

A band's worth, as the indent plan reads. Truncation covers the rare wider cell.
"""


def tableDocumentFor(obj) -> Optional[Any]:
	"""What can navigate the table this object is in.

	In order: the tree interceptor in browse mode, the object itself where it navigates its own
	tables, else a list view presented as one. The same dispatch as Easy Table Navigator
	(https://github.com/josephsl/easyTableNavigator). A list view inside a page is part of
	the page, hence the order.

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

	contentRows: int = 0
	"""How many rows the table had content in when this handle was made, or 0 for unknown.

	A snapshot, for comparing. Unlike `numRows` it moves only when the table does. See
	`flowObjectTable.SheetTable.contentRows` and `flowBand.FlowBand._tableChangedShape`.
	"""

	@property
	def key(self) -> tuple:
		"""What names this table, and names it apart from every other one.

		The document and the identifier, since NVDA's table identifier is unique only within a
		document.

		:return: a key to compare with `sameTable`, which knows how to compare each half.
		"""
		return (self.document, self.tableID)

	def __repr__(self) -> str:
		return f"<TableHandle {self.numRows}x{self.numCols} at row {self.row} column {self.col}>"


def tableAt(obj) -> Optional[TableHandle]:
	"""Whether the reader is in a table, and which one.

	By `_getTableCellCoords`, as Easy Table Navigator does, so the test cannot disagree with
	the fetches. Layout tables answer no unless NVDA's "include layout tables" setting is on.

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
	except CallCancelled:
		# **Not "there is no table here".** NVDA has stopped waiting on the application, and
		# an answer of "not a table" sends the reader an explanation of something that never
		# happened. It goes up to whoever asked. See `flow.CallCancelled`.
		raise
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
		# Asked now rather than later: two handles are compared to notice a table that has
		# changed, and a number read from the document at comparing time is the same number
		# twice. See `TableHandle.contentRows`.
		contentRows=_contentRowsOf(document, numRows),
	)


def _contentRowsOf(document, numRows: int) -> int:
	""":return: how many rows a table has content in, or its row count where it will not say."""
	said = getattr(document, "contentRows", None)
	if not isinstance(said, int) or said < 1:
		return numRows
	return said


def explain(obj) -> list:
	"""Why `tableAt` answered as it did, in the terms it answered in.

	`tableAt` swallows its three ways of saying no. This asks the same questions again, one at
	a time, rather than keeping a record that could go stale.

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

	At `info`, so it is there without turning debug logging on.

	:param obj: the object they are on.
	:param why: what was being attempted, for the first line.
	"""
	try:
		log.info("\n".join([f"BrlMultiline: {why}.", *explain(obj)]))
	except Exception:
		log.debugWarning("Could not say why a table could not be laid out", exc_info=True)


class TableRow(Region):
	"""One row of a table, read as its cells rather than as a line of text.

	A real `Region` subclass, because NVDA's buffer asks a region for attributes beyond any
	stand-in's guess. That is why it lives here and not in the NVDA-free `flowTable`. It adds
	the cells, which `FlowRenderer._asColumns` draws; anything else reads `rawText` and
	`brailleCells` as one line.
	"""

	def __init__(self, cells, separator: str = "  ", obj=None, caretColumn=None) -> None:
		"""
		:param cells: the row's cells, in the table's own column order.
		:param separator: what goes between them in the flat reading. Two spaces, as NVDA's
			own table reading uses.
		:param obj: the document this row was read out of. Required: `BrailleHandler.
			handleCaretMove` ignores a caret move unless the last region's `obj` is the object
			whose caret moved, and without that redraw the band never rechecks.
		:param caretColumn: asked on each update which column of this row the caret is in, or
			None. Asked rather than told, since NVDA re-reads a row at moments of its choosing.
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

		A merged or half-built row may lack cells; the column is then left blank.
		"""
		for cell in self.cells:
			if cell.index == index:
				return cell
		return None

	def update(self) -> None:
		"""Read every cell and run them together into this region's own text and braille.

		Assembled from the cells' own translations, so they agree with the drawn columns. The
		position maps are concatenated too, since NVDA's buffer needs them for text and routing.
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

		A packed position (column and place in it), the same space as this region's positions,
		not an index into `brailleCells`. The band computes its own cursor from it. At the start
		of the cell, since `_getTableCellCoords` gives only the cell.

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

		Only as much of each column as was drawn, so the report can check the widths.

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
		# Cells that read as nothing are left out rather than joined as empty strings. Every
		# column of a row now marks a position even when it holds nothing — see
		# `flowRender.FlowRenderer._laidOut`, which is what gives an empty cell a cursor and a
		# routing key — and a report that answered "AAPL  " for a row with one value in it
		# would be describing separators rather than content.
		return self.separator.join(
			said
			for said in (_sliceByBraille(self.cellFor(column).region, offsets) for column, offsets in runs)
			if said
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

	Through the region's `brailleToRawPos`, which accounts for contraction.
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

	Maps are rebuilt as straight runs where unusable, so their lengths always match, since a
	map of the wrong length maps to the wrong place.

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

	A `TextRegion`; see the module docstring. Keeps the cell's position for routing.
	"""

	def __init__(self, document, info, live: bool = False, plainCase: bool = False) -> None:
		"""
		:param document: what navigates the table, for moving the caret.
		:param info: the cell's position, as `_getTableCellAt` returned it.
		:param live: whether this flow moves the reader's own cursor. A viewer routes nowhere.
		:param plainCase: whether this column is drawn without capital signs. See
			`withoutCapitals`. Routing is unaffected, since it goes to the cell, not a character.
		"""
		said = _textOf(info)
		super().__init__(withoutCapitals(said) if plainCase else said)
		self.document = document
		self.info = info
		self.live = live
		self.obj = document

	def routeTo(self, braillePos: int) -> None:
		"""Put the caret in this cell.

		In the cell, not at the character, since offsets inside it are this region's, not the
		document's.

		:param braillePos: where in this region's cells the finger landed. Unused; part of the
			region interface.
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

	The `table-columnheadertext` attribute, which NVDA resolves from `<th>`, `scope=` and
	`headers=` through IAccessible2, and from UIA and Word by other routes. It handles
	multi-row headers, header columns and tables whose first row is data, which row one cannot.

	:param info: a cell's position, as `_getTableCellAt` returned it.
	:param axis: "column" for the header above the cell, "row" for the one beside it.
	:return: the header text, or "" where the document declares none.
	"""
	attribute = HEADER_ATTRIBUTES.get(axis)
	if attribute is None or info is None:
		return ""
	# An object table's cell already holds its column header as a string
	# (`flowObjectTable.ObjectCellInfo.header`), so it is taken directly rather than decoded
	# from the control field. Column axis only.
	if axis == "column":
		said = getattr(info, "header", None)
		if isinstance(said, str):
			return " ".join(said.split())
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

	Leading and trailing space is layout, and would waste column cells.
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

	Any cell of a column can answer, but one cell can be a bad witness, so up to `tries` rows
	are asked, only for columns still silent. This matters most when naming a table for a
	saved layout. Shared by all three callers.

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

	The reader's row, which certainly exists, then the first row and the one after theirs.

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


def widthOf(text: str) -> int:
	""":return: how wide a piece of text is in braille cells, by translating it.

	A character count is not a braille width. For text that arrives as a string, such as a
	declared header or a row from `rowTextOf`.

	:param text: what will be drawn.
	"""
	if not text:
		return 0
	try:
		region = TextRegion(text)
		region.update()
		return len(region.brailleCells)
	except Exception:
		log.debugWarning("Could not measure a piece of text", exc_info=True)
		return len(text)


def headerWidth(text: str) -> int:
	""":return: how wide a header is in braille cells. See `widthOf`."""
	return widthOf(text)


def _offeredBy(handle: TableHandle, name: str):
	""":return: one of the optional methods of a table stand-in, or None if there is not one.

	Only of this add-on's own stand-ins, since on a foreign document the same name could mean
	anything.

	:param handle: the table.
	:param name: the method wanted.
	"""
	if not isinstance(handle.document, flowObjectTable.ObjectTable):
		return None
	offered = getattr(handle.document, name, None)
	return offered if callable(offered) else None


def columnsShowingIn(handle: TableHandle):
	""":return: which of a table's columns are on show, or None for all of them.

	See `flowObjectTable.Sheet.columnsShowing`.

	:param handle: the table.
	"""
	offered = _offeredBy(handle, "columnsShowing")
	if offered is None:
		return None
	try:
		return offered()
	except CallCancelled:
		raise
	except Exception:
		log.debugWarning("Could not ask a table which columns it is showing", exc_info=True)
		return None


def rowTextOf(handle: TableHandle, row: int, first: int, last: int):
	""":return: a row's text across a span of columns in one read, or None to read it cell by cell.

	Only a grid that can read itself in bulk answers; see `flowObjectTable.Sheet.textRow`. All
	of the span or none: a short answer means unread cells, not empty ones, so the row is then
	read cell by cell.

	:param handle: the table.
	:param row: the row to read.
	:param first: the first column of the span.
	:param last: the last column, inclusive.
	:return: one string per column of the span, or None.
	"""
	offered = _offeredBy(handle, "rowText")
	if offered is None:
		return None
	wanted = last - first + 1
	if wanted <= 0:
		return None
	try:
		said = offered(row, first, last)
	except CallCancelled:
		raise
	except Exception:
		log.debugWarning(f"Could not read row {row} in one go", exc_info=True)
		return None
	if said is None:
		return None
	said = [str(text or "") for text in said]
	if len(said) != wanted:
		log.debugWarning(
			f"Row {row} came back with {len(said)} of {wanted} columns, "
			"so it is being read cell by cell instead",
		)
		return None
	return said


def columnHeadersOf(handle: TableHandle, first: int, last: int):
	""":return: what the columns declare, from a document that can say without reading cells.

	None sends the caller to the cells; an empty mapping means none are declared. See
	`flowObjectTable.Sheet.columnHeaders`.

	:param handle: the table.
	:param first: the first column of the span.
	:param last: the last column, inclusive.
	"""
	offered = _offeredBy(handle, "columnHeaders")
	if offered is None:
		return None
	try:
		said = offered(first, last)
	except CallCancelled:
		raise
	except Exception:
		log.debugWarning("Could not ask a table what its columns are called", exc_info=True)
		return None
	if said is None:
		return None
	return {int(column): str(text or "") for column, text in dict(said).items()}


def firstRowIsHeadings(document) -> bool:
	""":return: whether row one of a table may be read as its headings.

	Asked of whatever navigates the table. A document says nothing and gets yes, as NVDA
	assumes; a list view says no. One function, so `TableFlowSource` and `measure` agree.

	:param document: whatever is navigating the table.
	"""
	return bool(getattr(document, "hasHeaderRow", True))


def withoutCapitals(text: str) -> str:
	""":return: text that will translate without capital signs.

	Capital indicators make `AAPL` six cells instead of four. liblouis has no mode to suppress
	them, and stripping cells would break the position maps, so the text is lowered. In
	`en-ueb-g2` this removes only the capital indicators and changes no contractions.

	:param text: the cell's text.
	"""
	return text.lower()


def cellRegion(
	handle: TableHandle,
	row: int,
	column: int,
	live: bool = False,
	plainCase: bool = False,
):
	""":return: a region for one cell of a table, or None where there is no such cell.

	None is ordinary rather than exceptional: a merged cell occupies one coordinate and leaves
	the others empty, and `_getTableCellAt` says so by raising.

	:param handle: the table.
	:param row: the row to read.
	:param column: the column to read.
	:param live: whether the region may move the reader's cursor.
	:param plainCase: whether this column is drawn without capital signs. Applied here, where
		the text becomes a region and is translated, so every re-read applies it again.
	"""
	try:
		info = handle.document._getTableCellAt(handle.tableID, handle.document.selection, row, column)
	except CallCancelled:
		# **Not "there is no cell here".** A cancelled call is NVDA saying it has stopped
		# waiting, and swallowing it here made a whole worksheet measure empty. It goes up to
		# whoever asked for the reading, which is the only place that can tell the reader the
		# table could not be read. See `flow.CallCancelled`.
		raise
	except (LookupError, OSError):
		return None
	except Exception:
		log.debugWarning(f"Could not read row {row} column {column}", exc_info=True)
		return None
	if info is None:
		return None
	region = TableCellRegion(handle.document, info, live=live, plainCase=plainCase)
	region.update()
	return region


def cellHasContent(handle: TableHandle, row: int, column: int, live: bool = False) -> bool:
	""":return: whether one cell of a table holds anything the reader could read.

	One cell, deliberately. See `FlowBand._omittedColumnHasContent`.

	:param handle: the table.
	:param row: the row to look at.
	:param column: the column to look at.
	:param live: whether the region built here may move the reader's cursor. It does not.
	"""
	region = cellRegion(handle, row, column, live=live)
	if region is None:
		return False
	return bool((region.rawText or "").strip())


def measure(
	handle: TableHandle,
	live: bool = False,
	sample: int = MEASURE_ROWS,
	plainCase=(),
) -> list[Measurement]:
	"""Read a bandful of rows and find out how wide each column needs to be.

	In cells, by translating; see `flowTable`. The sample is about the caret's row (see
	`_sampleRows`), plus the header row, which a pinned header must fit.

	:param handle: the table.
	:param live: whether the regions built here move the reader's cursor. They do not; the
		parameter keeps measuring the same as reading.
	:param sample: how many rows to read.
	:param plainCase: the table's numbers for the columns drawn without capital signs,
		measured as drawn. See `withoutCapitals`.
	:return: one measurement per column of the table. A column found empty is only
		tentatively empty; see `cellHasContent`.
	"""
	showing = columnsShowingIn(handle)
	columns = [
		column
		for column in range(1, handle.numCols + 1)
		if showing is None or column in showing or column == handle.col
	]
	"""The columns this table is measured across: every one it has, less the ones it is
	hiding.

	Left out here, so a hidden column gets no width, place, count or heading downstream. The
	reader's own column is always kept, so they keep a cursor."""

	theirs = (
		handle.col
		if getattr(handle.document, "emptyColumnsArePlaces", False) and 1 <= handle.col <= handle.numCols
		else 0
	)
	"""The reader's own column, where an empty one of this table's is still a place.

	Without it the reader's cursor would have no cell to be drawn in. Asked of the table; see
	`flowObjectTable.ObjectTable.emptyColumnsArePlaces`.
	"""

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
	plain = frozenset(plainCase)
	# **What the columns are called, asked once for the table rather than once per cell.**
	# Where the document can say without reading anything it is asked here and the per-cell
	# question below never runs; where it cannot, and rows are being read in bulk so there
	# are no cells to ask, one cell per column is read now instead of one per cell later.
	# See `columnHeadersOf` and `flowObjectTable.Sheet.columnHeaders`.
	stated = columnHeadersOf(handle, 1, handle.numCols)
	if stated is None and _offeredBy(handle, "rowText") is not None:
		# A grid read in bulk never reaches the per-cell header question below, so its headers
		# are asked here. `{}` means none are declared; None means it could not say. See
		# `flowObjectTable.Sheet.columnHeaders`.
		stated = declaredHeaders(handle, columns)
	for column, said in (stated or {}).items():
		if said and column in labels:
			labels[column] = said
			headers[column] = headerWidth(said)
			declared.add(column)
	for row in _sampleRows(handle, sample):
		# One read for the whole row where the document offers it, and the ordinary cell at a
		# time where it does not. Only the source of the text differs; everything below this
		# is the same measurement either way. See `rowTextOf`.
		saidRow = rowTextOf(handle, row, 1, handle.numCols)
		for column in columns:
			info = None
			if saidRow is not None:
				text = saidRow[column - 1]
				if column in plain:
					text = withoutCapitals(text)
				size = widthOf(text)
			else:
				region = cellRegion(handle, row, column, live=live, plainCase=column in plain)
				if region is None:
					continue
				text = region.rawText
				size = len(region.brailleCells)
				info = region.info
			found.add(column)
			widths[column] = max(widths[column], size)
			if (
				info is not None
				and stated is None
				and not labels[column]
				and asked[column] < HEADER_TRIES
				and column not in declared
			):
				# Asked of up to `HEADER_TRIES` cells, stopping at the first answer, as in
				# `declaredHeaders`.
				asked[column] += 1
				said = declaredHeader(info)
				if said:
					labels[column] = said
					headers[column] = headerWidth(said)
					declared.add(column)
			if row == HEADER_ROW and borrowRowOne and not labels[column]:
				# Nothing declared, so the guess. See `HEADER_ROW`.
				labels[column] = text
				headers[column] = size
			else:
				# Row one included, where the header was declared rather than borrowed from
				# it: the row is data then, and a width planned as though it were a heading
				# would be a width short by one row of the table.
				seen[column].append(size)
	return [
		Measurement(
			index=column,
			# One cell for the reader's own empty column, because `planFor` refuses a column
			# that asks for nothing whoever measured it, and widens what it keeps to
			# `MIN_COLUMN_CELLS`.
			width=widths[column] or (1 if column == theirs and column in found else 0),
			typicalWidth=_typicalOf(seen[column]) or widths[column],
			label=labels[column],
			declared=column in declared,
			labelWidth=headers[column],
			# Not a column this table is showing: either no sampled row, header included, had a
			# cell there (a merged span), or every cell was empty, such as a column of
			# unreadable icons. Kept for the reader's own column of a grid; see `theirs`.
			hidden=column not in found or (widths[column] == 0 and column != theirs),
		)
		for column in columns
	]


def _typicalOf(sizes: list[int]) -> int:
	"""What a cell of a column usually holds, from the body rows that were read.

	The three-quarter point of the sample: the widest lets one long cell decide the layout,
	and the median leaves half the rows wrapping. The header is carried separately as
	`Measurement.labelWidth`, since it is drawn once.

	:param sizes: what was found in the body rows sampled, in cells.
	:return: the typical width, or zero if no body row was read.
	"""
	if not sizes:
		return 0
	ordered = sorted(sizes)
	return ordered[min(len(ordered) - 1, (len(ordered) * 3) // 4)]


def rowBeside(handle: TableHandle, row: int, by: int) -> Optional[int]:
	""":return: the row beside one, or None where the table says there is none beside it.

	The next row, unless the table hides rows and can say which. See
	`flowObjectTable.Sheet.rowAfter`.

	:param handle: the table.
	:param row: the row to step from, one based.
	:param by: which way, as 1 or -1.
	"""
	beside = _offeredBy(handle, "rowAfter")
	if beside is None:
		return row + by
	try:
		found = beside(row, by)
	except CallCancelled:
		# Not "the table ends here". See `flow.CallCancelled`.
		raise
	except Exception:
		log.debugWarning("Could not ask a table which row comes next", exc_info=True)
		return row + by
	return found


def rowShowingIn(handle: TableHandle, row: int) -> Optional[bool]:
	""":return: whether a table is still showing one row, or None where it will not say.

	For rows fetched before a filter hid them. See `flowObjectTable.Sheet.rowShowing`.

	:param handle: the table.
	:param row: the row asked about, one based.
	"""
	offered = _offeredBy(handle, "rowShowing")
	if offered is None:
		return None
	try:
		return offered(row)
	except CallCancelled:
		raise
	except Exception:
		log.debugWarning("Could not ask a table whether it is still showing a row", exc_info=True)
		return None


def _sampleRows(handle: TableHandle, sample: int) -> list[int]:
	""":return: which rows to measure: the header, then a bandful about the caret.

	Forward from the caret, and backwards too when there is not a bandful ahead, so the
	widths do not depend on where the reader stands. The sample is the same size either way.
	"""
	last = max(1, handle.numRows)
	first = max(1, min(handle.row, last))
	rows = [first]
	# Through the table's own idea of what comes next, not by counting: a column of a filtered
	# sheet measured from the rows the filter took away is a column sized for text the reader
	# will never be shown, and the widths are what the whole layout is built from.
	ahead = first
	while len(rows) < sample:
		ahead = rowBeside(handle, ahead, 1)
		if ahead is None or not first < ahead <= last:
			break
		rows.append(ahead)
	behind = first
	while len(rows) < sample:
		behind = rowBeside(handle, behind, -1)
		if behind is None or behind < 1:
			break
		rows.insert(0, behind)
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
		plainCase=(),
	) -> None:
		"""
		:param handle: the table, and where in it the reader was when it was recognised.
		:param columns: the table's own numbers for the columns to read, in drawing order.
			Only these are fetched.
		:param declared: what each column's header is, as the measurement found it, leaving
			out the ones the table did not declare. None for a caller with no measurement to
			hand, which asks the table here instead.
		:param generation: bumped by the caller when the run is replaced, so that a row
			number from one table can never match one from another.
		:param budget: how much work a fetch may do. One is made if none is given.
		:param live: whether this flow is the reader's own.
		:param pinHeaders: whether a header row is held above the window. It decides
			`firstRow`, since only this source knows whether the pinned row is row one.
		:param plainCase: the table's numbers for the columns drawn without capital signs,
			for the whole table rather than this page. See `withoutCapitals`.
		"""
		self.handle = handle
		self.columns = tuple(columns)
		self.plainCase = frozenset(plainCase)
		"""Which columns are drawn without capital signs, by the table's own numbers."""

		self._declared = {}
		"""The header each drawn column declares. Empty where the table declares none."""

		self._headersAsked = set()
		"""Which columns have been asked what their header is, answer or no answer.

		Separate from the answers, which hold nothing for a column with no header, so such a
		column is not asked again.
		"""

		if pinHeaders and declared is not None:
			# From the measurement, which asked several rows and knows which headers were
			# declared. See `measure` and `flowTable.Measurement`.
			self._declared = {column: said for column, said in declared.items() if said}
			self._headersAsked = set(declared)
		elif pinHeaders:
			self._declared = self._askColumns(self.columns)
			self._headersAsked = set(self.columns)

		self.pinnedIsRowOne = bool(pinHeaders) and (
			self._declaredRowIsRowOne() if self._declared else self._firstRowIsHeadings()
		)
		"""Whether the row held above the band is row one of the table itself.

		With nothing declared, row one is borrowed if `firstRowIsHeadings` allows. With declared
		headers, row one is compared against them. See `_declaredRowIsRowOne`.
		"""

		self._askedAboutRowOne = bool(self._declared)
		"""Whether the row one question has been put to real headers yet.

		`_headersForThisPage` may find headers later, so this is settled only once some are in
		hand.
		"""

		# Row one is skipped only when row one is what is pinned. Dropping it otherwise would
		# lose a row of the table to a guess the document had already contradicted — and
		# *not* dropping it when it is pinned draws the headings twice, which is what an
		# Excel worksheet with a marked header row did the moment the reader arrowed up onto
		# row one and the flow put that row on the band beside the copy above it.
		self.firstRow = HEADER_ROW + 1 if self.pinnedIsRowOne else HEADER_ROW
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

		Only the page being drawn, since every cell is a search of the document. The rows
		already read are then stale; the caller re-reads them. See
		`FlowController.setColumnPlan`.

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

		One cell read, on the header row, after the last known column. For a pin, which cannot
		use the band's caret-based check.
		"""
		try:
			return cellRegion(self.handle, HEADER_ROW, self.handle.numCols + 1, live=False) is not None
		except Exception:
			log.debugWarning("Could not look for a column past the end of the table", exc_info=True)
			return False

	def blockAt(self, blockId: BlockId) -> FetchResult:
		""":return: one row again, by the identity it already has.

		For re-reading the band, as on a page turn.
		"""
		return self._rowAt(blockId.bookmark)

	def moveTo(self, handle: TableHandle) -> None:
		"""Say where in the table the reader has moved to.

		Takes the handle the caller just looked up, to avoid a second read.

		:param handle: where they are now, in the same table.
		"""
		self.handle = handle

	def setCurrent(self, obj) -> None:
		"""Say where the reader has moved to.

		From the document's selection, since in a table the object does not move.
		"""
		found = tableAt(obj if obj is not None else self.obj)
		if found is not None and sameTable(found.key, self.handle.key):
			self.handle = found

	def stillReading(self, handle: Optional[TableHandle]) -> bool:
		""":return: whether a table just looked up is the one this source is reading.

		`isStillHere` without the lookup, for a caller that has the handle.

		:param handle: the table the reader is in now.
		"""
		return handle is not None and sameTable(handle.key, self.handle.key)

	def isStillHere(self, obj) -> bool:
		""":return: whether the reader is still in the table this source is reading.

		Asked before each redraw, so a layout never outlives its table.
		"""
		found = tableAt(obj)
		return found is not None and sameTable(found.key, self.handle.key)

	# Reading.

	def headerBlock(self) -> Optional[SourceBlock]:
		""":return: the table's header row, built for the page being drawn, or None.

		Not part of the stream when pinned; see `firstRow`. Built for each page.
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
		except CallCancelled:
			# Not "this table has no headings". See `flow.CallCancelled`.
			raise
		except Exception:
			log.debugWarning("Could not read the table's header row", exc_info=True)
			return None

	def _firstRowIsHeadings(self) -> bool:
		""":return: whether row one of this table may be read as its headings. See
		`firstRowIsHeadings`, which is where the question is answered."""
		return firstRowIsHeadings(self.handle.document)

	def _declaredRowIsRowOne(self) -> bool:
		""":return: whether what this table declares as its headers is row one's own text.

		Nothing reports which row declared headers come from, so row one is read: if every
		column on the page holds exactly its heading there, row one is the header row and is not
		drawn twice. Every column, including those without a heading, so no data is dropped.
		False when it cannot tell, since a duplicated row is better than a lost one.
		"""
		wanted = {column: self._declared.get(column, "") for column in self.columns}
		if not any(wanted.values()):
			return False
		try:
			texts = rowTextOf(self.handle, HEADER_ROW, 1, self.handle.numCols)
			matched = 0
			for column, heading in wanted.items():
				if texts is not None:
					said = texts[column - 1] if 1 <= column <= len(texts) else None
				else:
					region = cellRegion(self.handle, HEADER_ROW, column)
					said = None if region is None else region.rawText
				if said is None:
					# No cell there at all — a merged coordinate. Nothing to match and
					# nothing to lose.
					continue
				said = " ".join(said.split())
				if heading:
					if said != heading:
						return False
					matched += 1
				elif said:
					# Something of the reader's, in a column that names itself nothing. This
					# is a row of the table that happens to sit under the headings.
					return False
		except CallCancelled:
			# Neither answer is safe to guess at from a read that never happened: one draws
			# the headings twice, the other drops a row of the table.
			raise
		except Exception:
			log.debugWarning("Could not tell whether row one is the header row", exc_info=True)
			return False
		return matched > 0

	def _headersForThisPage(self) -> dict:
		""":return: the declared header of each column being drawn, leaving out those without one.

		Asks once for each column not yet seen, as a page turn brings, even if the measurement
		found none. `_headersAsked` keeps it to once.
		"""
		missing = [column for column in self.columns if column not in self._headersAsked]
		if missing:
			self._headersAsked.update(missing)
			self._declared.update(self._askColumns(missing))
		if self._declared and not self._askedAboutRowOne:
			# Headers in hand for the first time, so the question that could not be settled
			# without them can be. Once, and before the row is built below, so that a header
			# found late does not end up drawn twice. See `pinnedIsRowOne`.
			self._askedAboutRowOne = True
			if self._declaredRowIsRowOne():
				self.pinnedIsRowOne = True
				self.firstRow = HEADER_ROW + 1
		return {column: self._declared[column] for column in self.columns if column in self._declared}

	def _askColumns(self, columns) -> dict:
		""":return: what a run of columns declare, by the cheapest way this table will answer.

		The table first, then a few rows' cells only where it will not say. See
		`columnHeadersOf` and `declaredHeaders`.

		:param columns: the table's own numbers for the columns to ask about.
		"""
		wanted = list(columns)
		if not wanted:
			return {}
		stated = columnHeadersOf(self.handle, min(wanted), max(wanted))
		if stated is None:
			return declaredHeaders(self.handle, wanted)
		return {column: said for column, said in stated.items() if said and column in wanted}

	def _declaredRow(self, said: dict) -> SourceBlock:
		"""Build the pinned row out of what the table says its headers are.

		Text, since a declared header may come from anywhere or from several cells. A column
		that declares nothing is left blank rather than filled from row one, so guesses and
		answers are not mixed.

		:param said: the declared header of each column, by column number.
		"""
		cells = []
		for column in self.columns:
			# The heading with the column, because it is one column and one decision: the
			# heading is cut to the same width, so the capitals cost it the same cells.
			heading = said.get(column, "")
			region = TextRegion(withoutCapitals(heading) if column in self.plainCase else heading)
			region.update()
			cells.append(RowCell(index=column, region=region))
		content = TableRow(
			cells,
			obj=self.obj,
			# Only where the pinned row is row one, so the reader's cursor can be shown there.
			# See `FlowController._pinnedCursor`.
			caretColumn=functools.partial(self._caretColumn, HEADER_ROW) if self.pinnedIsRowOne else None,
		)
		return SourceBlock(
			blockId=BlockId(generation=self.generation, bookmark=HEADER_ROW, unit=self.unit),
			region=content,
			isBlank=not content.rawText.strip(),
		)

	def describeHeader(self) -> str:
		""":return: where the pinned row came from, for the log.

		Which of the two ways the header row was built.
		"""
		if self.handle.numRows < 1:
			return "nothing: the table has no rows"
		try:
			said = self._headersForThisPage()
		except Exception as error:
			return f"could not be worked out: {error!r}"
		if said:
			# Which row it came off, where that could be told. The reader cannot see that the
			# pinned row and row one are the same row, and the report could not say so either
			# — which is how a doubled header row went unexplained.
			where = ", which is row one itself" if self.pinnedIsRowOne else ""
			return f"declared by the table's own cells{where}: {said}"
		if not self._firstRowIsHeadings():
			return "nothing: this table declares no headers and its first row is not headings"
		return f"row {HEADER_ROW}, since this table declares none"

	def rowNoLongerShown(self, rows) -> Optional[int]:
		""":return: the first of these rows the table has stopped showing, or None if none has.

		For rows read before a filter was applied, which `rowBeside` cannot catch. Never judged:
		the reader's own row, and the pinned header row.

		:param rows: the row numbers the band is holding, in any order.
		"""
		for row in rows:
			if not isinstance(row, int) or row == self.handle.row or row < self.firstRow:
				continue
			if rowShowingIn(self.handle, row) is False:
				return row
		return None

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

		Bounded by the table's dimensions rather than by trying and failing.
		"""
		self.budget.startUnlessActive()
		row = rowBeside(self.handle, blockId.bookmark, by)
		if row is None:
			return FetchResult.endOfStream("this table shows no row beyond this one")
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

		The row number is the bookmark: it survives a re-read and is what `_getTableCellAt` takes.
		"""
		cells = self._readInOneCall(row)
		if cells is None:
			cells = []
			for column in self.columns:
				region = cellRegion(
					self.handle,
					row,
					column,
					live=self.live,
					plainCase=column in self.plainCase,
				)
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

	def _readInOneCall(self, row: int) -> Optional[list]:
		""":return: the drawn cells of one row from a single read, or None to read them singly.

		The batch seam for drawn rows, as well as for measuring. Cell objects are not fetched
		until a routing key needs one; see `flowObjectTable.ObjectCellInfo`. Headers come from
		what the table declared.

		:param row: the row to read.
		"""
		wanted = sorted(self.columns)
		if not wanted:
			return None
		fetch = _offeredBy(self.handle, "cellObject")
		rows = _offeredBy(self.handle, "rowObject")
		if fetch is None or rows is None:
			return None
		said = rowTextOf(self.handle, row, wanted[0], wanted[-1])
		if said is None:
			return None
		# What a cell is fetched by, which is a row object for a list and the number itself
		# for a grid. Asked rather than assumed: `cellObject` takes one or the other and only
		# the table knows which.
		item = rows(row)
		if item is None:
			return None
		cells = []
		for column in wanted:
			text = said[column - wanted[0]]
			info = flowObjectTable.ObjectCellInfo(
				text,
				header=self._declared.get(column, ""),
				fetch=functools.partial(fetch, item, column),
			)
			region = TableCellRegion(
				self.handle.document,
				info,
				live=self.live,
				plainCase=column in self.plainCase,
			)
			region.update()
			cells.append(RowCell(index=column, region=region))
		# In the table's own order, which is the plan's: the row is drawn by each cell's
		# column number and read in reading order by their order here.
		return [cell for column in self.columns for cell in cells if cell.index == column]

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

	One function for the band, pinning and the column command, so they agree, including on a
	display with no band.

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

	The document by identity, which errs towards giving a layout back, unless it offers
	`sameAs`, as list view stand-ins do since they are rebuilt on every ask. The identifier by
	value, since it is an integer or a tuple.
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


class TableRequest:
	"""The reader's request to see a table in columns, and what goes with it.

	Held by the plugin rather than the band, because a one row display has no band and the
	reader still wants to ask — see `flowBand.MIN_BAND_ROWS`. The band, the plugin's commands
	and pinning all read this one object, so there is one answer whether or not a band is
	showing the table.
	"""

	def __init__(self) -> None:
		self.wanted = None
		"""The key of the table asked for, or None. See `TableHandle.key` and `wantsColumns`."""

		self.refused = None
		"""A table whose saved layout the reader has just turned off, for as long as they are
		in it.

		A saved layout applies itself the moment the reader arrives, so turning it off has to
		outlast the redraw that follows, or the toggle does nothing. Cleared when they leave
		the table. See `FlowBand._refusedTable`, and `script_forgetTableLayout` for the way to
		make it stop for good.
		"""

		self._arranged: tuple | None = None
		"""The table a layout was arranged for, and the layout. See `layout`."""

	@property
	def layout(self):
		""":return: the layout the reader arranged for the table asked for, or None.

		None means "however this table reads by itself". Stored with the table it was arranged
		for and answered only for that table: the request is dropped on several paths, and an
		arrangement that outlived one of them would be applied to the next table laid out.
		"""
		if self._arranged is None:
			return None
		key, layout = self._arranged
		return layout if sameTable(key, self.wanted) else None

	@layout.setter
	def layout(self, layout) -> None:
		self._arranged = None if layout is None else (self.wanted, layout)

	def forget(self) -> None:
		"""Drop the request and the arrangement that went with it, which must go together."""
		self.wanted = None
		self._arranged = None

	def __repr__(self) -> str:
		return f"<TableRequest wanted={self.wanted!r} refused={self.refused!r}>"


__all__ = [
	"MEASURE_ROWS",
	"TableCellRegion",
	"TableFlowSource",
	"TableHandle",
	"TableRequest",
	"cellRegion",
	"explain",
	"logExplanation",
	"sameTable",
	"measure",
	"tableAt",
	"tableDocumentFor",
]
