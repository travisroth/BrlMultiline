# BrlMultiline: a table's row on one line, for a display that has only one.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The row the caret is in, drawn as one long line of its cells with a bar between each.

**What it is for.** A flow lays a table out in columns, and a flow needs rows to do it in — so
on a Focus 80 there is no band and NVDA's own line is what the reader gets. In a table that
line is one cell: its header, then its value. Comparing a row means arrowing along it and
holding every value in the head. This puts the whole row on the line instead, as the column
layout puts it across a band, so the reader pans across the row the way they would pan across
a long line of text::

	AAPL | 310.34 | +1.2% | 48,201,300

**On request, per table.** The command asks for the table the caret is in, and while it stands
every visit to that table reads this way — arrowing out of it and back in again keeps it, as
the column layout's request does. Pressing it again in that table, or anywhere outside a
table, is how it is taken back. A table on another page is a different table; see
`flowTableSource.sameTable`.

**What moves the caret is `tableArrows`, unchanged.** Up and down change row and keep the
column, left and right change cell; the caret lands in a cell, NVDA asks the region it is
showing to update, and this region reads the row the caret is now in. Nothing here moves
anything on a keypress, so nothing here can disagree with where NVDA's own table movement put
the caret.

**The region is NVDA's own until it is in the table asked for.** `TableRowLineRegion` is a
`CursorManagerRegion` and falls back to being exactly that — NVDA's line, NVDA's routing,
NVDA's next line — whenever the caret is anywhere else, so the rest of the page reads as it
always has. An ordinary region becomes one by `adopt`, called from the patch on
`TextInfoRegion.update` (see `patches`), because NVDA builds its region afresh on every focus
change and there is no other moment to catch it at. Only while a request stands, and only a
region that is exactly NVDA's, so a flow's regions and the document lines' are never touched.

**Which columns, and in what order.** A layout the reader saved for this table decides, as it
would for the columns — see `flowTableLayouts.layoutFor`. Otherwise every column, in the
table's own order. An empty cell keeps its place between two bars, because the bars are how
the reader counts which column they are in.

**Panning is NVDA's.** The line is longer than any display, and NVDA's buffer already pans a
long region a display at a time and brings the window to the cursor when the caret moves. Past
the end of the line NVDA asks the region for its next line, and here that is the next row; past
the start, the row before. At the first and last rows the question goes back to NVDA's own
answer, so the reader pans out of the table exactly as the arrow keys let them walk out of it.
"""

import dataclasses
from typing import Optional

from braille.regions.base import TextRegion
from braille.regions.textInfo import CursorManagerRegion
from logHandler import log

from . import glyphFlow
from .flowTableSource import (
	TableHandle,
	_readCell,
	cellRegion,
	sameTable,
	tableAt,
)

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


_request: Optional[Request] = None
"""What the reader asked for, or None. One table at a time, as the column layout's request is."""


def wanted() -> Optional[Request]:
	""":return: the table asked for, or None."""
	return _request


def request(handle: TableHandle, columns=()) -> Request:
	"""Read the rows of a table on one line from now on.

	:param handle: the table, as `flowTableSource.tableAt` found it.
	:param columns: the table's own column numbers to show, in order, or empty for all of them.
	:return: what was asked for.
	"""
	global _request
	_request = Request(key=handle.key, columns=tuple(int(column) for column in columns if column))
	return _request


def clear() -> None:
	"""Stop. Every region this module adopted goes back to reading as NVDA's own."""
	global _request
	_request = None


def adopt(region) -> bool:
	"""Make one of NVDA's browse mode regions able to read a row, if a row is wanted anywhere.

	**The class of the region is changed in place**, which is not something done lightly. It is
	done here because the region is NVDA's — built in `getFocusRegions` on every focus change,
	appended to the buffer, found again by `handleCaretMove` by what it is showing — and
	replacing it with another object would mean finding every place that holds it. The subclass
	adds no state NVDA's class lacks until it is updated, and falls back to NVDA's behaviour
	whenever the caret is not in the table asked for, so the change is invisible everywhere a row
	is not being read.

	:param region: a region NVDA is about to update.
	:return: whether it was adopted, in which case the caller should update it again as what it
		now is.
	"""
	if _request is None or type(region) is not CursorManagerRegion:
		# Exactly NVDA's class and nothing derived from it. The flow's regions and the document
		# lines' derive from it and each has a reading of its own, which this would overturn.
		return False
	try:
		region.__class__ = TableRowLineRegion
	except TypeError:
		log.debugWarning(
			"BrlMultiline: a browse mode region could not be made to read table rows", exc_info=True
		)
		return False
	return True


def redraw(handler) -> None:
	"""Have NVDA read its line again now, rather than at the next caret move.

	The request changes what a region shows without the caret moving, so nothing else would
	tell NVDA to look. Asked as a caret move rather than an update because that is what brings
	the display to the cursor afterwards — the cell the reader is in, on a line that has just
	grown to the width of a row.

	:param handler: NVDA's braille handler.
	"""
	try:
		regions = handler.mainBuffer.regions
		region = regions[-1] if regions else None
		if isinstance(region, CursorManagerRegion):
			handler.handleCaretMove(region.obj, shouldAutoTether=False)
	except Exception:
		log.debugWarning("BrlMultiline: could not redraw after a change to table rows", exc_info=True)


@dataclasses.dataclass(frozen=True)
class LineCell:
	"""One cell of the line, and where it sits in it."""

	column: int
	"""The table's own number for the column."""

	region: Optional[object]
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

	def cellFor(self, column: int) -> Optional[LineCell]:
		""":return: the cell of one column, or None where the line does not show that column."""
		return next((cell for cell in self.cells if cell.column == column), None)

	def cellAt(self, braillePos: int) -> Optional[LineCell]:
		""":return: the cell a braille position falls in.

		The last cell reaching from at or before it, so a bar belongs to the cell before it and
		the space after a bar to the cell after. That is also what gives an empty cell somewhere
		to be: it holds no cells of its own, and the space after its bar is its.
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

	Translated rather than written as dots, because a bar is not the same cells in every braille
	table and the reader's is the one they read. Asked afresh for every line: a reader who changes
	table between two lines gets the new bar on the next one, and it is three characters.
	"""
	region = TextRegion(SEPARATOR)
	region.update()
	return _readCell(region)


def readRow(obj) -> Optional[RowLine]:
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
	columns = [column for column in asked.columns if column <= handle.numCols] or list(
		range(1, handle.numCols + 1),
	)
	line = RowLine(handle=handle, cells=[])
	separator = _separator()
	text, drawn, forward, _back = separator
	# Where the bar ends inside the separator, in braille: the space after it is the next cell's.
	bar = len(text.rstrip())
	afterBar = forward[bar] if bar < len(forward) else len(drawn)
	for index, column in enumerate(columns):
		reachFrom = 0
		if index:
			reachFrom = line.add(*separator)[1] + afterBar
		try:
			region = cellRegion(handle, handle.row, column, live=True)
		except Exception:
			log.debugWarning(f"BrlMultiline: could not read column {column} of a table row", exc_info=True)
			region = None
		if region is None:
			# A merged cell, or a row still being built. It keeps its place between the bars.
			rawStart, brailleStart = len(line.rawText), len(line.brailleCells)
		else:
			rawStart, brailleStart = line.add(*_readCell(region))
		line.cells.append(LineCell(column, region, rawStart, brailleStart, reachFrom))
	return line


class TableRowLineRegion(CursorManagerRegion):
	"""NVDA's browse mode line, which reads the caret's whole table row while one is asked for.

	See the module docstring. Every method checks whether the last update read a row and, where
	it did not, is NVDA's own.
	"""

	_rowLine: Optional[RowLine] = None
	"""The row the last update read, or None when it read NVDA's line instead."""

	def update(self):
		line = readRow(self.obj)
		self._rowLine = line
		if line is None:
			super().update()
			return
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
		if self._toRow(1):
			return None
		# The last row. NVDA's own next line, from the last cell, walks out of the table.
		self._readFrom(self._rowLine.cells[-1:])
		return super().nextLine()

	def previousLine(self, start=False):
		"""Panning back past the start of the row: the last cell of the row before, or its first.

		The last, because that is where a reader panning backwards reads on from, as NVDA puts
		the caret at the end of the line before. The first where NVDA's own command asks for the
		start of the line.
		"""
		if self._rowLine is None:
			return super().previousLine(start)
		if self._toRow(-1, fromTheEnd=not start):
			return None
		# The first row. NVDA's own previous line, from the first cell, walks out of the table.
		self._readFrom(self._rowLine.cells[:1])
		return super().previousLine(start)

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
