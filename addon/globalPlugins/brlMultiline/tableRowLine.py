# BrlMultiline: a table's row on one line, for a display that has only one.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The row the caret is in, drawn as one long line of its cells with a bar between each.

For a one row display, where there is no band to lay a table out in columns. The whole row
goes on the line, panned like a long line of text::

	AAPL | 310.34 | +1.2% | 48,201,300

1. On request, per table, as the column layout's request is; pressing again, or outside a
   table, takes it back. See `flowTableSource.sameTable`.
2. The caret is moved by `tableArrows`; this only reads the row the caret is in.
3. `TableRowLineRegion` is a `CursorManagerRegion` that behaves exactly as NVDA's outside the
   table asked for. NVDA's own region becomes one through `adopt`, from the patch on
   `TextInfoRegion.update`, only while a request stands.
4. Columns follow a saved layout (see `flowTableLayouts.layoutFor`), else all in order. An
   empty cell keeps its place between bars, which are how the reader counts columns.
5. Panning is NVDA's; past either end of the line comes the next or previous row, and past
   the first or last row, NVDA's own answer.
6. A list view read through `flowObjectTable`, such as File Explorer's, is shown by NVDA through
   the focus's object region, or its text region where it has text. `adoptObject` makes the
   last of those an `ObjectRowLineRegion` or `ObjectRowLineTextRegion`. The list's own arrow
   keys move the focus, so the column is remembered across rows (see `cursorColumn`), and
   past the first or last row panning stops.
7. A worksheet's cell is drawn by the Excel module's own region, a subclass of NVDA's, which
   opts in through `ROW_LINE_SEAM`. Only a single cell's row is read: a selected range is
   NVDA's to show. Hidden columns and rows are passed over, as the column layout does.
"""

import dataclasses
import functools

from braille.regions.base import TextRegion
from braille.regions.NVDAObject import NVDAObjectRegion
from braille.regions.textInfo import CursorManagerRegion, TextInfoRegion
from logHandler import log

from . import flowObjectTable, glyphFlow
from .flow import CallCancelled
from .flowTableSource import (
	TableCellRegion,
	TableHandle,
	_readCell,
	cellRegion,
	columnsShowingIn,
	rowBeside,
	rowTextOf,
	sameTable,
	tableAt,
)

ROW_LINE_SEAM = "brlMultilineReadsARow"
"""A class attribute by which a subclass of NVDA's object region says it may read a row.

True means the region is always the focus's last, as a worksheet cell's is. The region keeps
its own class underneath and its own behaviour wherever no row is read. Its update may call
NVDA's, which is where adoption happens, so the row is drawn by `_ObjectRowLine.showRow` and
never by calling `update` again."""

SEPARATOR = " | "
"""What goes between two cells.

A bar with a space either side. The spaces are what stop a cell's last word and the next cell's
first running together under the finger, and the bar is what says the gap is a cell boundary
rather than a space inside a value — a company name has spaces in it and a price does not."""


@dataclasses.dataclass(frozen=True)
class Request:
	"""A table the reader asked to read a row at a time on one line."""

	key: tuple
	"""The table, as `flowTableSource.TableHandle.key` names it."""

	columns: tuple = ()
	"""The table's own column numbers, in the order to show them, or empty for every column."""

	isObjectTable: bool = False
	"""Whether the table is a list view read through `flowObjectTable`, which NVDA shows through
	its object region rather than its browse mode line."""


_request: Request | None = None
"""What the reader asked for, or None. One table at a time, as the column layout's request is."""

_column: int | None = None
"""The column the reader was last on in an object table, for the row the focus moves to next.

A list's arrow keys are the application's, and the row they focus says nothing about a column.
See `cursorColumn`."""


def wanted() -> Request | None:
	""":return: the table asked for, or None."""
	return _request


def request(handle: TableHandle, columns=()) -> Request:
	"""Read the rows of a table on one line from now on.

	:param handle: the table, as `flowTableSource.tableAt` found it.
	:param columns: the table's own column numbers to show, in order, or empty for all of them.
	:return: what was asked for.
	"""
	global _request, _column
	_request = Request(
		key=handle.key,
		columns=tuple(int(column) for column in columns if column),
		isObjectTable=isinstance(handle.document, flowObjectTable.ObjectTable),
	)
	_column = handle.col
	return _request


def clear() -> None:
	"""Stop. Every region this module adopted goes back to reading as NVDA's own."""
	global _request, _column
	_request = None
	_column = None


def adopt(region) -> bool:
	"""Make one of NVDA's browse mode regions able to read a row, if a row is wanted anywhere.

	The class is changed in place, since NVDA holds the region in several places. The subclass
	adds no state until updated and behaves as NVDA's outside the table asked for.

	:param region: a region NVDA is about to update.
	:return: whether it was adopted, in which case the caller should update it again as what it
		now is.
	"""
	if _request is None or _request.isObjectTable or type(region) is not CursorManagerRegion:
		# Exactly NVDA's class and nothing derived from it. The flow's regions and the document
		# lines' derive from it and each has a reading of its own, which this would overturn.
		return False
	return _becomes(region, TableRowLineRegion)


def adoptObject(region) -> bool:
	"""Make NVDA's region for the focus able to read a row, if a list's row is wanted.

	The object table's twin of `adopt`, for the last of the regions NVDA builds for the focus:
	its object region, or its text region where it has text. Only the focus's own: the regions
	NVDA puts before it for the focus's ancestors are the same class, and the row a focused cell
	is in is one of them. A subclass is taken only where it opts in; see `ROW_LINE_SEAM`.

	:param region: a region NVDA is about to update.
	:return: whether it was adopted, in which case the caller should update it again.
	"""
	if _request is None or not _request.isObjectTable or isinstance(region, _ObjectRowLine):
		return False
	if getattr(type(region), ROW_LINE_SEAM, False) and isinstance(region, NVDAObjectRegion):
		cls = _rowLineClassFor(type(region))
	elif type(region) is NVDAObjectRegion:
		if getattr(region, "appendText", ""):
			# NVDA's text region for the focus follows, and is the last region, which is
			# where the cursor and the panning keys go. The row is read there instead.
			return False
		cls = ObjectRowLineRegion
	elif type(region) is TextInfoRegion:
		cls = ObjectRowLineTextRegion
	else:
		return False
	if not _isTheFocus(getattr(region, "obj", None)):
		return False
	return _becomes(region, cls)


_rowLineClasses: dict = {}
"""The row reading classes made for regions that opt in, by the class each was made for."""


def _rowLineClassFor(base: type) -> type:
	""":return: a class that reads a row and is otherwise `base`, made once per base."""
	cls = _rowLineClasses.get(base)
	if cls is None:
		cls = type(f"RowLine{base.__name__}", (_ObjectRowLine, base), {})
		_rowLineClasses[base] = cls
	return cls


def _becomes(region, cls) -> bool:
	""":return: whether a region's class could be changed to one of this module's."""
	try:
		region.__class__ = cls
	except TypeError:
		log.debugWarning("BrlMultiline: a region could not be made to read table rows", exc_info=True)
		return False
	return True


def _isTheFocus(obj) -> bool:
	""":return: whether an object is the one with the focus."""
	if obj is None:
		return False
	try:
		import api

		focus = api.getFocusObject()
		return obj is focus or bool(obj == focus)
	except Exception:
		log.debugWarning("BrlMultiline: could not compare an object with the focus", exc_info=True)
		return False


def redraw(handler) -> None:
	"""Have NVDA read its line again now, rather than at the next caret move.

	As a caret move in a document, and as the focus arriving again on an object, so the display
	is brought to the cursor's cell on the longer line either way.

	:param handler: NVDA's braille handler.
	"""
	try:
		regions = handler.mainBuffer.regions
		region = regions[-1] if regions else None
		if isinstance(region, CursorManagerRegion):
			handler.handleCaretMove(region.obj, shouldAutoTether=False)
		elif isinstance(region, (NVDAObjectRegion, TextInfoRegion)):
			import api

			handler.handleGainFocus(api.getFocusObject(), shouldAutoTether=False)
	except Exception:
		log.debugWarning("BrlMultiline: could not redraw after a change to table rows", exc_info=True)


def columnMoved(handler) -> None:
	"""Show the column NVDA's table commands have just moved the navigator object to.

	They move only the navigator object, which braille tethered to the focus does not follow.

	:param handler: NVDA's braille handler.
	"""
	if _request is None or not _request.isObjectTable:
		return
	regions = handler.mainBuffer.regions
	region = regions[-1] if regions else None
	if isinstance(region, _ObjectRowLine) and region.isReadingARow:
		redraw(handler)


@dataclasses.dataclass(frozen=True)
class LineCell:
	"""One cell of the line, and where it sits in it."""

	column: int
	"""The table's own number for the column."""

	region: object | None
	"""The cell's region, which is what routing goes through, or None for a cell the row has not
	got."""

	rawStart: int
	brailleStart: int
	reachFrom: int = 0
	"""The first braille position that routes to this cell: the space after the bar before it.

	Not `brailleStart`, which is after that space. A finger lands on the gap beside a value as
	often as on the value, and the gap after a bar is the next cell's; the space before a bar,
	and the bar, stay the cell they follow."""


@dataclasses.dataclass
class RowLine:
	"""A row read and run together: the region's text, its braille and the maps between them."""

	handle: TableHandle
	cells: list
	rawText: str = ""
	brailleCells: list = dataclasses.field(default_factory=list)
	rawToBraillePos: list = dataclasses.field(default_factory=list)
	brailleToRawPos: list = dataclasses.field(default_factory=list)
	columnsBefore: list = dataclasses.field(default_factory=list)
	"""The columns shown before the cells read, where only part of the row was read."""
	columnsAfter: list = dataclasses.field(default_factory=list)
	"""The columns shown after the cells read, where only part of the row was read.

	Panning goes to these before it goes to another row, so no column is passed over. See
	`CELLS_READ_ONE_AT_A_TIME`."""

	def cellFor(self, column: int) -> LineCell | None:
		""":return: the cell of one column, or None where the line does not show that column."""
		return next((cell for cell in self.cells if cell.column == column), None)

	def cellAt(self, braillePos: int) -> LineCell | None:
		""":return: the cell a braille position falls in.

		A bar belongs to the cell before it and the space after a bar to the cell after, which
		gives an empty cell a place.
		"""
		found = None
		for cell in self.cells:
			if cell.reachFrom > braillePos:
				break
			found = cell
		return found

	def add(self, text: str, drawn: list, forward: list, back: list) -> tuple:
		"""Append one piece to the line, carrying its maps into the line's own positions.

		:return: where it began, in text and in braille.
		"""
		rawStart, brailleStart = len(self.rawText), len(self.brailleCells)
		self.rawToBraillePos.extend(brailleStart + position for position in forward)
		self.brailleToRawPos.extend(rawStart + position for position in back)
		self.rawText += text
		self.brailleCells.extend(drawn)
		return rawStart, brailleStart


def _separator() -> tuple:
	""":return: the separator's text, cells and maps, translated as the reader's table translates it.

	Translated afresh for every line, since a bar differs between braille tables.
	"""
	region = TextRegion(SEPARATOR)
	region.update()
	return _readCell(region)


def readRow(obj) -> RowLine | None:
	""":return: the row the caret is in, as one line, or None where NVDA's own line should show.

	None whenever nothing was asked for, the caret is not in the table that was, or something is
	selected — a selection is about the text, and NVDA's line is what shows it.

	:param obj: the document the region reads.
	"""
	asked = _request
	if asked is None or getattr(obj, "passThrough", False):
		return None
	try:
		if not obj.selection.isCollapsed:
			return None
		handle = tableAt(obj)
	except Exception:
		# Including a cancelled call. NVDA's own line is the safe answer to a question that
		# could not be asked, and the next caret move asks again.
		log.debugWarning("BrlMultiline: could not tell which table row the caret is in", exc_info=True)
		return None
	if handle is None or not sameTable(handle.key, asked.key):
		return None
	try:
		return buildLine(handle, asked)
	except CallCancelled:
		log.debugWarning("BrlMultiline: reading a table row was cancelled", exc_info=True)
		return None


CELLS_READ_ONE_AT_A_TIME = 64
"""The most cells a row reads one at a time, around the reader's own, where it cannot be read in
one call. Each is a call to the application, and a row is read on every move."""


def buildLine(handle: TableHandle, asked: Request) -> RowLine:
	""":return: the handle's row read and run together, in the columns asked for.

	:param handle: the table, on the row to read.
	:param asked: the request, for its columns.
	:raises CallCancelled: where NVDA stopped waiting on the application. The cells not read
		would show as empty ones, so the caller shows NVDA's own presentation instead.
	"""
	columns = lineColumns(handle, asked)
	regions = _regionsInOneCall(handle, columns)
	line = RowLine(handle=handle, cells=[])
	if regions is None and len(columns) > CELLS_READ_ONE_AT_A_TIME:
		here = columns.index(handle.col) if handle.col in columns else 0
		start = max(0, min(here - CELLS_READ_ONE_AT_A_TIME // 2, len(columns) - CELLS_READ_ONE_AT_A_TIME))
		end = start + CELLS_READ_ONE_AT_A_TIME
		line.columnsBefore = columns[:start]
		line.columnsAfter = columns[end:]
		columns = columns[start:end]
	separator = _separator()
	text, drawn, forward, _back = separator
	# Where the bar ends inside the separator, in braille: the space after it is the next cell's.
	bar = len(text.rstrip())
	afterBar = forward[bar] if bar < len(forward) else len(drawn)
	for index, column in enumerate(columns):
		reachFrom = 0
		if index:
			reachFrom = line.add(*separator)[1] + afterBar
		region = regions.get(column) if regions is not None else _cellRegionAt(handle, column)
		if region is None:
			# A merged cell, or a row still being built. It keeps its place between the bars.
			rawStart, brailleStart = len(line.rawText), len(line.brailleCells)
		else:
			rawStart, brailleStart = line.add(*_readCell(region))
		line.cells.append(LineCell(column, region, rawStart, brailleStart, reachFrom))
	return line


def lineColumns(handle: TableHandle, asked: Request) -> list:
	""":return: the table's own column numbers the line shows, in order.

	A saved layout's, else every column. A column the table is hiding is left out of both, as
	the column layout leaves it out, but never the reader's own, so they keep a cursor. A
	layout whose columns are all hidden gives every column on show.

	:param handle: the table, on the row to read.
	:param asked: the request, for its columns.
	"""
	showing = columnsShowingIn(handle)

	def isShown(column: int) -> bool:
		return showing is None or column in showing or column == handle.col

	saved = [column for column in asked.columns if column <= handle.numCols and isShown(column)]
	return saved or [column for column in range(1, handle.numCols + 1) if isShown(column)]


def _cellRegionAt(handle: TableHandle, column: int):
	""":return: one cell of the handle's row, read on its own, or None where it cannot be read."""
	try:
		return cellRegion(handle, handle.row, column, live=True)
	except CallCancelled:
		raise
	except Exception:
		log.debugWarning(f"BrlMultiline: could not read column {column} of a table row", exc_info=True)
		return None


def _regionsInOneCall(handle: TableHandle, columns: list) -> dict | None:
	""":return: the handle's row as cell regions by column, from one read, or None.

	None where the table cannot read a row in one call, and the cells are read one at a time.
	Only a worksheet can. The object behind a cell is fetched only when a routing key lands on
	it; see `flowObjectTable.ObjectCellInfo`.

	:param handle: the table, on the row to read.
	:param columns: the columns wanted.
	"""
	document = handle.document
	fetch = getattr(document, "cellObject", None)
	rows = getattr(document, "rowObject", None)
	if not columns or not callable(fetch) or not callable(rows):
		return None
	texts = {}
	for first, last in spansOf(columns):
		said = rowTextOf(handle, handle.row, first, last)
		if said is None:
			return None
		texts.update(zip(range(first, last + 1), said, strict=True))
	item = rows(handle.row)
	if item is None:
		return None
	regions = {}
	for column in columns:
		info = flowObjectTable.ObjectCellInfo(
			texts[column],
			fetch=functools.partial(fetch, item, column),
		)
		region = TableCellRegion(document, info, live=True)
		region.update()
		regions[column] = region
	return regions


def goToRestOfRow(line: RowLine, forward: bool) -> int | None:
	"""Put the reader in the nearest column of the row that was not read, if there is one.

	:param line: the row on show, of which only part may have been read.
	:param forward: whether to go to the columns after the cells read, or those before.
	:return: the column gone to, or None where the whole row was read that way.
	:raises CallCancelled: where NVDA stopped waiting on the application.
	"""
	handle = line.handle
	columns = line.columnsAfter if forward else list(reversed(line.columnsBefore))
	for column in columns:
		region = cellRegion(handle, handle.row, column, live=True)
		if region is not None:
			region.routeTo(0)
			return column
	return None


_PRESENTATION = ("cursorPos", "brailleCursorPos", "hidePreviousRegions", "focusToHardLeft")
"""What drawing a row sets on a region and NVDA's own update does not always set back."""


def keepNvdasPresentation(region) -> None:
	"""Remember what NVDA had set of `_PRESENTATION`, before a row is first drawn over it."""
	held = vars(region)
	if "_nvdaPresentation" not in held:
		region._nvdaPresentation = {name: held[name] for name in _PRESENTATION if name in held}


def restoreNvdasPresentation(region) -> None:
	"""Put back what `keepNvdasPresentation` remembered, so NVDA's update starts from its own."""
	held = vars(region)
	saved = held.pop("_nvdaPresentation", None)
	if saved is None:
		return
	for name in _PRESENTATION:
		if name in saved:
			setattr(region, name, saved[name])
		else:
			held.pop(name, None)


LONGEST_GAP_READ = 16
"""The most unwanted columns a read in one call spans to reach the next wanted one. A longer
gap, such as the empty columns between the data and a reader far to the right of it, starts
another read."""


def spansOf(columns: list) -> list:
	""":return: the (first, last) spans that read the columns, joined across short gaps.

	:param columns: the column numbers wanted.
	"""
	spans = []
	for column in sorted(set(columns)):
		if spans and column - spans[-1][1] - 1 <= LONGEST_GAP_READ:
			spans[-1][1] = column
		else:
			spans.append([column, column])
	return [tuple(span) for span in spans]


class TableRowLineRegion(CursorManagerRegion):
	"""NVDA's browse mode line, which reads the caret's whole table row while one is asked for.

	See the module docstring. Every method checks whether the last update read a row and, where
	it did not, is NVDA's own.
	"""

	_rowLine: RowLine | None = None
	"""The row the last update read, or None when it read NVDA's line instead."""

	def update(self):
		line = readRow(self.obj)
		self._rowLine = line
		if line is None:
			restoreNvdasPresentation(self)
			super().update()
			return
		keepNvdasPresentation(self)
		# What NVDA's own update would have recorded about control fields, which is nothing:
		# a shape must never be drawn over a cell's text on the strength of a reading before it.
		glyphFlow.restartFields(self)
		self._readingInfo = self._getSelection()
		self.rawText = line.rawText
		self.rawTextTypeforms = []
		self.brailleCells = line.brailleCells
		self.rawToBraillePos = line.rawToBraillePos
		self.brailleToRawPos = line.brailleToRawPos
		self.selectionStart = self.selectionEnd = None
		self.brailleSelectionStart = self.brailleSelectionEnd = None
		self._brailleInputIndStart = None
		# A row is never the start of the page, and what came before it on the display — the
		# document's name — would push the row a display to the right.
		self.hidePreviousRegions = True
		self.focusToHardLeft = True
		# At the start of the cell the caret is in, which is the question the reader cannot
		# answer by feel: which cell. The same answer the column layout gives; see
		# `flowTableSource.TableRow._caretPosition`.
		here = line.cellFor(line.handle.col)
		self.cursorPos = here.rawStart if here is not None else None
		self.brailleCursorPos = here.brailleStart if here is not None else None

	def routeTo(self, braillePos: int):
		"""Put the caret in the cell a finger landed on, or activate the one it is already in.

		The second half is NVDA's rule for routing onto the cursor, kept because a cell may hold
		a link or a button and this is how a braille reader presses it.
		"""
		line = self._rowLine
		if line is None:
			return super().routeTo(braillePos)
		cell = line.cellAt(braillePos)
		if cell is None or cell.region is None:
			return None
		if cell.column == line.handle.col:
			try:
				self._getSelection().activate()
			except NotImplementedError:
				pass
			except Exception:
				log.debugWarning("BrlMultiline: could not activate a table cell", exc_info=True)
			return None
		cell.region.routeTo(0)
		_speakOnRouting(getattr(cell.region, "info", None))
		return None

	def getTextInfoForBraillePos(self, braillePos: int):
		"""The start of the cell under a position, which is what a cell of this line points at."""
		line = self._rowLine
		if line is None:
			return super().getTextInfoForBraillePos(braillePos)
		cell = line.cellAt(braillePos)
		info = getattr(cell.region, "info", None) if cell is not None and cell.region is not None else None
		where = (info if info is not None else self._getSelection()).copy()
		where.collapse()
		return where

	def nextLine(self):
		"""Panning on past the end of the row: the first cell of the next one."""
		if self._rowLine is None:
			return super().nextLine()
		if self._toRestOfRow(forward=True) or self._toRow(1):
			return None
		# The last row. NVDA's own next line, from the last cell, walks out of the table.
		self._readFrom(self._rowLine.cells[-1:])
		return super().nextLine()

	def previousLine(self, start=False):
		"""Panning back past the start of the row: the last cell of the row before, or its first.

		The last, as NVDA puts the caret at the end of the line before; the first where NVDA's
		command asks for the line's start.
		"""
		if self._rowLine is None:
			return super().previousLine(start)
		if self._toRestOfRow(forward=False) or self._toRow(-1, fromTheEnd=not start):
			return None
		# The first row. NVDA's own previous line, from the first cell, walks out of the table.
		self._readFrom(self._rowLine.cells[:1])
		return super().previousLine(start)

	def _toRestOfRow(self, forward: bool) -> bool:
		""":return: whether the caret went to a column of this row that was not read. See
		`goToRestOfRow`."""
		try:
			return goToRestOfRow(self._rowLine, forward) is not None
		except Exception:
			log.debugWarning("BrlMultiline: could not go on along a table row", exc_info=True)
			return False

	def _toRow(self, by: int, fromTheEnd: bool = False) -> bool:
		"""Put the caret in the row beside this one, in the first cell it has of those shown.

		:param by: which way, as 1 or -1.
		:param fromTheEnd: whether to look for its last cell rather than its first.
		:return: whether there was a row that way with a cell in it.
		"""
		line = self._rowLine
		handle = line.handle
		columns = [cell.column for cell in line.cells]
		if fromTheEnd:
			columns.reverse()
		row = handle.row + by
		while 1 <= row <= handle.numRows:
			for column in columns:
				try:
					region = cellRegion(handle, row, column, live=True)
				except Exception:
					log.debugWarning(f"BrlMultiline: could not read row {row} of a table", exc_info=True)
					return False
				if region is not None:
					region.routeTo(0)
					return True
			# A row with none of these columns in it, which is a row of merged cells or a
			# caption. Nothing to show the reader, so on to the one beyond.
			row += by
		return False

	def _readFrom(self, cells) -> None:
		"""Have NVDA's own next and previous line start from one cell of this row.

		NVDA moves from the reading unit it last read, and what it last read here was a row it
		did not read. The cell at the edge the reader is leaving by is where the page resumes.
		"""
		info = next((getattr(cell.region, "info", None) for cell in cells if cell.region is not None), None)
		if info is not None:
			self._readingInfo = info.copy()


def readObjectRow(obj, canReadASheet: bool = False) -> RowLine | None:
	""":return: the row the focus is in, as one line, or None where NVDA's own reading should show.

	:param obj: the focus, which is a row of the list or, in File Explorer, one of its cells.
	:param canReadASheet: whether the region is a worksheet cell's own, which is the only one a
		worksheet's row is read in. A selected range is drawn by NVDA's plain object region.
	"""
	asked = _request
	if asked is None or not asked.isObjectTable:
		return None
	try:
		handle = tableAt(obj)
		if handle is None or not sameTable(handle.key, asked.key):
			return None
		if isinstance(handle.document, flowObjectTable.SheetTable) and not canReadASheet:
			return None
		handle = dataclasses.replace(handle, col=cursorColumn(handle, obj))
		return buildLine(handle, asked)
	except Exception:
		# Including a cancelled call. NVDA's own reading is the safe answer, and the next
		# focus change asks again.
		log.debugWarning("BrlMultiline: could not read the list row the focus is in", exc_info=True)
		return None


def cursorColumn(handle: TableHandle, focus) -> int:
	""":return: the column the cursor goes on in an object table's row, which is remembered.

	In order: the cell the navigator object is on, where it is in this row, as NVDA's table
	commands leave it; the focused cell, where the focus is a cell; the column the reader was
	last on, since the row an arrow key focuses says nothing about one.

	:param handle: the table, on the focused row.
	:param focus: the object with the focus.
	"""
	global _column
	row = getattr(handle.document, "focused", None)
	column = None
	if not isinstance(row, int):
		# A worksheet's row is a number, and asking a cell for its parent there builds a
		# worksheet. Its focus is always a cell, which answers below.
		try:
			import api

			column = flowObjectTable.columnInRow(api.getNavigatorObject(), row)
		except Exception:
			log.debugWarning("BrlMultiline: could not ask which column the navigator is in", exc_info=True)
	if column is None and flowObjectTable.columnNumberOf(focus) is not None:
		column = handle.col
	if column is None:
		column = _column or handle.col
	_column = column
	return column


class _ObjectRowLine:
	"""Reading the focused list row in place of NVDA's own region for the focus.

	The object table's `TableRowLineRegion`, mixed into whichever of NVDA's regions is the
	focus's last. Every method checks whether the last update read a row and, where it did not,
	is NVDA's own.
	"""

	_rowLine: RowLine | None = None
	"""The row the last update read, or None when it read NVDA's presentation instead."""

	@property
	def isReadingARow(self) -> bool:
		""":return: whether the last update read a row rather than NVDA's presentation."""
		return self._rowLine is not None

	def update(self):
		if not self.showRow():
			super().update()

	def showRow(self) -> bool:
		"""Draw the focused row in place of NVDA's presentation, if there is one to draw.

		:return: whether a row was drawn. Where not, the caller draws NVDA's presentation.
		"""
		line = readObjectRow(self.obj, canReadASheet=bool(getattr(type(self), ROW_LINE_SEAM, False)))
		self._rowLine = line
		if line is None:
			restoreNvdasPresentation(self)
			return False
		keepNvdasPresentation(self)
		glyphFlow.restartFields(self)
		self.rawText = line.rawText
		self.rawTextTypeforms = []
		self.brailleCells = line.brailleCells
		self.rawToBraillePos = line.rawToBraillePos
		self.brailleToRawPos = line.brailleToRawPos
		self.selectionStart = self.selectionEnd = None
		self.brailleSelectionStart = self.brailleSelectionEnd = None
		self._brailleInputIndStart = None
		# The list's ancestors, and the focus's own object region where its text follows,
		# would push the row a display to the right. See `TableRowLineRegion.update`.
		self.hidePreviousRegions = True
		self.focusToHardLeft = True
		here = line.cellFor(line.handle.col)
		self.cursorPos = here.rawStart if here is not None else None
		self.brailleCursorPos = here.brailleStart if here is not None else None
		return True

	def routeTo(self, braillePos: int):
		"""Go to the cell a finger landed on, or do the focus's default action on the one it is on.

		The second half is what NVDA's object region does with every routing key. Going to a cell
		in the row that already has the focus moves only the navigator object, so the line is
		drawn again rather than left to a focus event that does not come.
		"""
		global _column
		line = self._rowLine
		if line is None:
			return super().routeTo(braillePos)
		cell = line.cellAt(braillePos)
		if cell is None or cell.region is None:
			return None
		if cell.column == line.handle.col:
			try:
				self.obj.doAction()
			except NotImplementedError:
				pass
			except Exception:
				log.debugWarning("BrlMultiline: could not activate a list row", exc_info=True)
			return None
		_column = cell.column
		cell.region.routeTo(0)
		import braille

		redraw(braille.handler)
		return None

	def nextLine(self):
		"""Panning on past the end of the row: the first cell of the next one.

		A list has nothing after its last row to pan into, so there it stops.
		"""
		if self._rowLine is None:
			return super().nextLine()
		if not self._toRestOfRow(forward=True):
			self._toRow(1)
		return None

	def previousLine(self, start=False):
		"""Panning back past the start of the row: the last cell of the row before, or its first.

		See `TableRowLineRegion.previousLine`.
		"""
		if self._rowLine is None:
			return super().previousLine(start)
		if not self._toRestOfRow(forward=False):
			self._toRow(-1, fromTheEnd=not start)
		return None

	def _toRestOfRow(self, forward: bool) -> bool:
		""":return: whether the reader went to a column of this row that was not read.

		Drawn again, since in a list the focus stays on the row. See `routeTo` and
		`goToRestOfRow`.
		"""
		global _column
		try:
			column = goToRestOfRow(self._rowLine, forward)
		except Exception:
			log.debugWarning("BrlMultiline: could not go on along a list's row", exc_info=True)
			return False
		if column is None:
			return False
		_column = column
		import braille

		redraw(braille.handler)
		return True

	def _toRow(self, by: int, fromTheEnd: bool = False) -> bool:
		"""Go to the row beside this one, in the first cell it has of those shown.

		The column is remembered before the focus moves, since the focus event is what draws
		the row and it says nothing about a column.

		:param by: which way, as 1 or -1.
		:param fromTheEnd: whether to look for its last cell rather than its first.
		:return: whether there was a row that way with a cell in it.
		"""
		global _column
		line = self._rowLine
		handle = line.handle
		columns = [cell.column for cell in line.cells]
		if fromTheEnd:
			columns.reverse()
		try:
			row = rowBeside(handle, handle.row, by)
			while row is not None and 1 <= row <= handle.numRows:
				for column in columns:
					region = cellRegion(handle, row, column, live=True)
					if region is not None:
						_column = column
						region.routeTo(0)
						return True
				# A row with none of these columns in it. See `TableRowLineRegion._toRow`.
				row = rowBeside(handle, row, by)
		except Exception:
			log.debugWarning("BrlMultiline: could not read the row beside a list's row", exc_info=True)
		return False


class ObjectRowLineRegion(_ObjectRowLine, NVDAObjectRegion):
	"""NVDA's object region for the focus, reading the focused list row while one is asked for."""


class ObjectRowLineTextRegion(_ObjectRowLine, TextInfoRegion):
	"""NVDA's text region for a focus that has text, reading the focused list row instead.

	NVDA puts this after the focus's object region, so the row goes here and the object region
	is hidden. See `adoptObject`.
	"""

	def getTextInfoForBraillePos(self, braillePos: int):
		"""The focus's caret, since no position in the row is a position in the focus's text."""
		if self._rowLine is None:
			return super().getTextInfoForBraillePos(braillePos)
		import textInfos

		return self.obj.makeTextInfo(textInfos.POSITION_CARET)


def _speakOnRouting(info) -> None:
	"""Say where routing landed, when NVDA's braille settings ask for that.

	NVDA's own routing does this and a cell reached by a routing key is no different. Guarded,
	since it is NVDA's private function and the only loss without it is the speech.
	"""
	if info is None:
		return
	try:
		from braille.regions import textInfo

		speak = getattr(textInfo, "_speakOnRouting", None)
		if speak is not None:
			where = info.copy()
			where.collapse()
			speak(where)
	except Exception:
		log.debugWarning("BrlMultiline: could not say where routing landed", exc_info=True)
