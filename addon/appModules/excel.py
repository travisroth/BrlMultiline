# BrlMultiline: reading an Excel worksheet as a flow of rows.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Excel, and the only file in this add-on that knows Excel exists.

**Why this is an application module and not part of the global plugin.** A global plugin is
loaded for the whole session and asked about every object in every application. Excel is one
application; the code that knows how to read its worksheets has no business being resident
while the reader is in their mail, and its questions have no business being asked of every
list item in Windows. An application module is loaded when its application runs and unloaded
when it stops, which is exactly the lifetime this code wants.

What makes that possible is that the flow needs *one* thing from an application that it
cannot work out for itself: the cell at a coordinate. Everything else a table is asked — what
a cell says, which row and column it is, what its column's header is, where the caret goes
when a routing key lands on it — NVDA already answers on the object, once per accessibility
API and tuned per application, and the flow asks the object exactly as it does for a list view
or a message list. So the seam is one method: an overlay class on a spreadsheet cell offers
`brlMultilineSheet`, the flow asks every object it meets whether it has that name, and what
comes back answers four questions. See `flowObjectTable.Sheet` and
`flowObjectTable.SheetTable`, neither of which mentions Excel.

**This module replaces NVDA's own Excel application module**, because an add-on's module of a
given name is loaded instead of the built-in one. So it extends it rather than standing beside
it: `nvdaBuiltin` is the package NVDA keeps for exactly this, and everything NVDA's module
does — the formula bar redirect, the broken data validation list, the UI Automation window
choices — goes on happening through `super`.

**Two object models, and only one of them can be read this way today.** By default NVDA
reaches Excel through its COM object model, and `NVDAObjects.window.excel.ExcelCell` carries
`excelCellObject`, whose worksheet answers `cells(row, column)` — the same call NVDA's own
`ExcelWorksheet._get_firstChild` makes. That is an exact, cheap coordinate lookup and it is
what this reads. With "Use UI Automation to access Microsoft Excel spreadsheet controls when
available" turned on — off by default, `config.conf["UIA"]["useInMSExcelWhenAvailable"]` — the
cells are `NVDAObjects.UIA.excel.ExcelCell` instead, and there is no equivalent: NVDA does not
wrap the grid pattern's item lookup, and a spreadsheet's cells cannot be walked as children.
Such a cell is left alone, so the reader keeps NVDA's ordinary reading of it rather than a
layout drawn from numbers this could not check.
"""

import ctypes
from typing import Optional

import config
import eventHandler
import NVDAHelper
from braille.constants import TEXT_SEPARATOR
from braille.regions.NVDAObject import NVDAObjectRegion, ReviewNVDAObjectRegion
from comtypes import BSTR
from config.configFlags import ReportTableHeaders
from logHandler import log
from NVDAHelper.localLib import EXCEL_CELLINFO
from NVDAObjects.window.excel import (
	NVCELLINFOFLAG_ADDRESS,
	NVCELLINFOFLAG_COORDS,
	NVCELLINFOFLAG_TEXT,
	ExcelCell,
	convertAddressToLocal,
	xlA1,
)
from nvdaBuiltin.appModules.excel import AppModule as ExcelAppModule

try:
	from exceptions import CallCancelled
except ImportError:  # Outside NVDA, and on an NVDA old enough not to have it.

	class CallCancelled(Exception):  # type: ignore[no-redef]
		"""Stand-in for NVDA's own. See `globalPlugins.brlMultiline.flow`."""


try:
	from tableUtils import HeaderCellTracker
except ImportError:  # pragma: no cover - an NVDA without it answers no headings at all.
	HeaderCellTracker = None
"""Where NVDA records the header rows and columns a reader has marked. See
`ExcelSheet._trackerNow`, which fills one rather than reading the worksheet's."""


_UNASKED = object()
"""Stands for a question not yet put, where None is one of the answers."""

MAX_COLUMNS = 250
"""How wide a sheet may be before it is left to NVDA's ordinary reading.

**Because Excel's used range lies, and lies large.** It grows to whatever has ever been
written in or *formatted* and does not shrink when the content is deleted, so a sheet with a
fill colour once applied to a whole row reports itself as sixteen thousand columns wide. The
measurement reads a bandful of rows across every column of the table — see
`flowTableSource.measure` — so an honest reading of that sheet is a hundred and fifty thousand
COM calls and translations, in one go, on the thread NVDA answers the reader on. The display
would stop and the reader would have no way to know why.

So a sheet wider than this is refused, and refusing means the reader keeps NVDA's ordinary
cell-at-a-time reading rather than waiting for a layout that was never going to arrive. The
number is generous for anything anybody arranges by hand — two hundred and fifty columns is
already forty pages on a Monarch — and it bounds the measurement at a couple of thousand
reads.
"""


class ExcelSheet:
	"""One worksheet, addressed by coordinate. Answers `flowObjectTable.Sheet`.

	Built afresh for each reading rather than held, because it is a couple of attribute reads
	over a COM object the cell already has, and because a worksheet held across a sheet change
	is a worksheet the reader has left.
	"""

	def __init__(self, cell) -> None:
		"""
		:param cell: the `NVDAObjects.window.excel.ExcelCell` the reader is on.
		"""
		self.cell = cell
		self._headers = _UNASKED
		"""What the columns are called, worked out once. See `columnHeaders`."""

		self._used = None
		"""How far Excel considers this sheet used, asked once. See `shape`."""

		self.obj = cell.parent
		"""The worksheet, which is what tells one sheet from another. NVDA builds it from the
		cell's own `Worksheet`, and compares two of them by the sheet's index — see
		`ExcelWorksheet._isEqual`, which is what stops a layout following the reader onto the
		next sheet."""

	@property
	def _sheet(self):
		""":return: Excel's own worksheet object, which is what answers by coordinate."""
		return self.obj.excelWorksheetObject

	def where(self) -> tuple:
		""":return: the row and column the reader is on, as Excel numbers them.

		NVDA's answer first: `rowNumber` and `columnNumber` are on the cell, and they are the
		same numbers `cells(row, column)` takes, which is the reason to keep the table's
		coordinates as the sheet's own.

		**Excel's own answer second, and nothing third.** NVDA's two come from its Excel
		helper, and that can be silent — not yet started, or unable to reach the sheet — while
		the COM range beside it still knows perfectly well where it is. Reading a silence as
		A1 would attach the whole reading to the wrong place and say nothing had gone wrong,
		which is the worst of the three outcomes. See `sheetFor`, which declines the reading
		when neither answers.
		"""
		return (self._coordinate("rowNumber", "row"), self._coordinate("columnNumber", "column"))

	def _coordinate(self, said: str, asked: str) -> int:
		""":return: one of the reader's coordinates, or 0 if nothing will say.

		:param said: what NVDA calls it on the cell.
		:param asked: what Excel calls it on the range.
		"""
		for thing, name in ((self.cell, said), (self.cell.excelCellObject, asked)):
			try:
				found = int(getattr(thing, name, 0) or 0)
			except CallCancelled:
				# Not "this cell will not say where it is", which declines the whole sheet.
				raise
			except Exception:
				log.debugWarning(f"Could not read a cell's {name}", exc_info=True)
				continue
			if found > 0:
				return found
		return 0

	def shape(self) -> tuple:
		""":return: how far the sheet is used, as (rows, columns), counted from A1.

		From A1 rather than from the corner of the used range, so that a row number here is
		Excel's row number and a column number is Excel's column number. A sheet whose data
		starts at C5 has four empty rows above it and they are read as four empty rows; the
		alternative is an offset that every coordinate in the add-on would have to carry, to
		save the reader a scroll they can make in one keystroke.

		A sheet is a million rows by sixteen thousand columns and nearly all of it is empty,
		so the used range is what makes a layout possible at all: it is what Excel considers
		written in, and it is one call. **One call per adapter**, which is what the caching is
		for: recognising the table asks the shape twice on its own — once to find out whether
		the sheet is too wide, once for the table's dimensions — and a review counted eight
		used range reads for one move between cells. Where the reader is, though, is asked
		afresh every time, since that is what the answer is stretched to cover and it costs
		nothing to ask.

		**And the cell the reader is standing in, whether or not Excel counts it as used.**
		Arrowing about a blank sheet does not enlarge the used range: on an empty workbook the
		active cell can be D20 while the used range is still A1, and a table of one row by one
		column with the reader at row 20 column 4 is a reading with nowhere to put them. The
		table therefore reaches at least as far as they do.
		"""
		row, column = self.where()
		if self._used is None:
			try:
				used = self._sheet.usedRange
				self._used = (
					int(used.row) + int(used.rows.count) - 1,
					int(used.column) + int(used.columns.count) - 1,
				)
			except CallCancelled:
				raise
			except Exception:
				# **Unknown rather than nothing.** Falling back to zero left the reader's own
				# coordinate as the whole of the answer, so a worksheet that would not say how
				# far it goes was presented as a table one cell bigger than wherever they were
				# standing — a truncated sheet, offered as though it were the sheet. Refused
				# instead, which leaves them NVDA's ordinary reading of the cell. See
				# `sheetFor`, which is what turns this into a no.
				log.debugWarning("Could not ask a worksheet how far it is used", exc_info=True)
				raise LookupError("The worksheet would not say how far it is used") from None
		rows, columns = self._used
		return (max(1, rows, row), max(1, columns, column))

	def whereIsIt(self) -> Optional[str]:
		""":return: the workbook and the sheet, which is what tells one worksheet from another.

		The ordinary answer for a control is its application and window class, and every
		worksheet of every workbook is "excel" and "EXCEL7" — so two workbooks with the same
		headings were each other's saved layout. A review found it; the fix is that a sheet
		can say. Written down as a digest and never in full. See
		`flowObjectTable.Sheet.whereIsIt` and `flowTableLayouts.keyFor`.

		The workbook's own name where the path cannot be had — an unsaved workbook has no path
		— which is still a great deal better than naming every sheet the same thing.
		"""
		try:
			sheet = self._sheet
			book = sheet.parent
			where = str(getattr(book, "fullName", "") or getattr(book, "name", "") or "").strip()
			return f"{where}/{str(sheet.name or '').strip()}" if where else None
		except CallCancelled:
			raise
		except Exception:
			log.debugWarning("Could not ask a worksheet which workbook it is in", exc_info=True)
			return None

	def usedShape(self) -> tuple:
		""":return: how far Excel considers this sheet *used*, as (rows, columns).

		`shape` stretches its answer to wherever the reader is standing, because a table that
		stops short of them is a reading with nowhere to put them. That makes it the wrong
		number to notice a change by: on a blank sheet, arrowing from D20 to D21 moves it, and
		a layout rebuilt on every keypress is a display that will not settle. This is the half
		that only moves when the sheet does. See `flowObjectTable.Sheet.usedShape`.
		"""
		self.shape()
		return self._used

	def tooWide(self) -> bool:
		""":return: whether this sheet is too wide to measure without stopping NVDA.

		See `MAX_COLUMNS`, which is about what Excel's used range says rather than about what
		anybody wrote.
		"""
		return self.shape()[1] > MAX_COLUMNS

	def textRow(self, row: int, first: int, last: int) -> Optional[list]:
		""":return: a row's text across a span of columns, in one call, or None.

		**One cross-process call for the whole row, where asking cell by cell was eight or so
		per column.** This is the batch fetch NVDA's own quick navigation uses —
		`ExcelCellInfoQuicknavIterator.iterate` — pointed at a contiguous range instead of a
		sparse collection: the helper is given an address and a count and fills in one
		`EXCEL_CELLINFO` per cell, of which this wants two fields.

		The measurement is what needs it. Reading twenty-one columns of two rows a cell at a
		time took over ten seconds on hardware, because each cell was a coordinate lookup, an
		`NVDAObject` built with its overlay classes chosen, and a separate helper call
		fetching that one cell's text, address, states, comments and formula. NVDA never does
		that: it fetches ranges. Neither does this, now.

		**The text is what is displayed**, which is the point of going through the helper
		rather than through `Range.Value2`. A one-call value read exists, but it hands back
		what is stored — a date as a serial number, a percentage as a fraction — and a column
		sized from that is a column sized for something the reader will never feel.

		None wherever this cannot be sure, and None means the caller reads the row cell by
		cell as it always did. See `flowObjectTable.Sheet.textRow`.

		:param row: the row to read.
		:param first: the first column of the span.
		:param last: the last column, inclusive.
		"""
		count = last - first + 1
		if count < 1:
			return None
		try:
			sheet = self._sheet
			span = sheet.range(sheet.cells(row, first), sheet.cells(row, last))
			address = convertAddressToLocal(
				self.cell.excelCellObject.Application,
				span.address(True, True, xlA1, True),
			)
			said = _cellInfosFor(self.cell, address, count)
		except CallCancelled:
			raise
		except Exception:
			log.debugWarning(f"Could not read row {row} in one call", exc_info=True)
			return None
		if said is None:
			return None
		texts = [""] * count
		merged: dict = {}
		for index, (column, text, address) in enumerate(said):
			# By the coordinate it came back with, and by its place in the answer only where
			# it came back without one. A cell that says which column it is in cannot be put
			# in the wrong one by a reordered answer.
			place = column - first if column else index
			if not 0 <= place < count:
				continue
			# Stripped, because the object path strips — see `flowTableSource._textOf` — and
			# a column measured from padding is a column wider than what is drawn in it.
			texts[place] = (text or "").strip()
			if address:
				merged.setdefault(address, []).append(place)
		for places in merged.values():
			# **A merged cell is one cell under several columns.** The helper reads `.text`
			# off each cell of the range, and only the top left member of a merge holds it,
			# so the rest came back empty. The address it reports is the *merge area's* — all
			# the members of one merge answer with the same address and nothing else does —
			# so they are the group that shares whichever of them has the text.
			if len(places) < 2:
				continue
			said = next((texts[place] for place in places if texts[place]), "")
			if said:
				for place in places:
					texts[place] = said
		return texts

	def columnHeaders(self, first: int, last: int) -> Optional[dict]:
		""":return: what each column of a span is called, {} where none is, or None to ask cells.

		**The question that nearly froze a worksheet.** Asked cell by cell it costs an
		`ExcelCell` built and a header search per column, three times over for a column that
		says nothing — sixty three cell objects on a twenty one column sheet nobody has marked
		up, before a single row the reader can feel has been read. Forty two of them was
		already enough to pass the watchdog on hardware.

		Where a reader marks a header row, NVDA writes it into the workbook as a defined name
		and keeps what it found in the worksheet's header cell tracker. The tracker says which
		cell heads which columns; what that cell *says* is the text at (its row, the column),
		which is `fetchAssociatedHeaderCellText` exactly — and a row of text is one batch
		call. So a marked sheet is answered by one read of its header row rather than one read
		per column, and an unmarked one is answered without reading anything at all.

		**An empty answer is only given after a walk that finished.** This is the second time
		the tracker has been asked and the first time cost the reader their header row: NVDA
		builds one lazily by walking every defined name in the workbook and keeps whatever
		that walk produced, an empty one included where it failed part way, while every cell
		of that same sheet could name its column perfectly well. Read off the worksheet, an
		empty tracker cannot tell "no headings" from "the walk did not finish". Filled here,
		the difference is the difference between returning and raising. See `_trackerNow`.

		None for anything this cannot work out exactly, which sends the caller back to asking
		the cells: a heading several rows tall, which NVDA joins together itself, and one that
		applies to a run of rows rather than to the column.

		:param first: the first column of the span, one based.
		:param last: the last column, inclusive.
		"""
		if self._headers is _UNASKED:
			self._headers = self._askedHeaders()
		if self._headers is None:
			return None
		return {column: said for column, said in self._headers.items() if first <= column <= last}

	def _askedHeaders(self) -> Optional[dict]:
		"""Work out what every column of the sheet is called, once. See `columnHeaders`.

		The whole sheet rather than the span in hand, because the answer is cached and a later
		page asks about columns this one did not.
		"""
		try:
			marked = self._markedHeaders()
		except CallCancelled:
			raise
		except Exception:
			log.debugWarning("Could not ask a worksheet where its headings are", exc_info=True)
			return None
		if marked is None:
			return None
		if not marked:
			return {}
		width = self.shape()[1]
		found: dict = {}
		for row, low, high in marked:
			high = min(high, width)
			if high < low:
				continue
			said = self.textRow(row, low, high)
			if said is None:
				# The heading row could not be read in one go, and guessing which of these
				# columns it would have named is worse than asking their cells.
				return None
			for index, text in enumerate(said):
				# The first entry that answers, in the tracker's own order, which is what
				# `fetchAssociatedHeaderCellText` returns for a cell.
				if text and not found.get(low + index):
					found[low + index] = text
		return found

	def _markedHeaders(self) -> Optional[list]:
		""":return: (row, first column, last column) per marked column heading, or None.

		Read off NVDA's own tracker, which is where what the reader marked ends up. An entry
		heads the columns from its own rightwards, bounded by whatever the defined name said —
		see `tableUtils.HeaderCellTracker.iterPossibleHeaderCellInfosFor`, which is the rule
		this follows.

		None where the tracker could not be finished, and where an entry is a shape that cannot
		be read off one row: a heading several rows tall, which NVDA joins together, or one
		bounded to a run of rows, which is not a property of the column at all.
		"""
		tracker = self._trackerNow()
		if tracker is None:
			return None
		infos = getattr(tracker, "infosDict", None) or {}
		marked = []
		for key in list(getattr(tracker, "listByRow", ()) or ()):
			info = infos.get(key)
			if info is None or not getattr(info, "isColumnHeader", False):
				continue
			if getattr(info, "minRowNumber", None) or getattr(info, "maxRowNumber", None):
				return None
			if int(getattr(info, "rowSpan", 1) or 1) != 1:
				return None
			low = max(int(info.columnNumber), int(getattr(info, "minColumnNumber", 0) or 0) or 1)
			high = int(getattr(info, "maxColumnNumber", 0) or 0) or self.shape()[1]
			if high >= low:
				marked.append((int(info.rowNumber), low, high))
		return marked

	def _trackerNow(self):
		""":return: a header cell tracker filled here and now, or None if it cannot be.

		**Filled rather than read off the worksheet, and that is the whole of the safeguard.**
		NVDA builds one lazily, by walking every defined name in the workbook, and keeps
		whatever the walk produced — so a walk interrupted part way leaves an empty tracker
		cached on the worksheet, and reading it back cannot tell that from a sheet with no
		headings. That is what took the reader's header row off the display, and asking a cell
		instead would not have caught it either: a cell resolves `columnHeaderText` through
		its worksheet's tracker, and the cells this hands out are given this same worksheet.
		One poisoned answer, asked twice.

		Filling one here makes the question answerable: the walk either finishes, and an empty
		tracker then means the sheet has no marked headings, or it raises, and this says so by
		answering None.

		The finished tracker is put on the worksheet, which is where NVDA keeps its own and
		what every cell built from this sheet resolves its column's header through — so the
		two paths cannot answer differently, and a worksheet whose tracker was left empty by a
		failed walk is mended rather than worked around.
		"""
		populate = getattr(self.obj, "populateHeaderCellTrackerFromNames", None)
		if populate is None or HeaderCellTracker is None:
			return None
		tracker = HeaderCellTracker()
		populate(tracker)
		self.obj.headerCellTracker = tracker
		return tracker

	def cellAt(self, row: int, column: int):
		""":return: the cell at one coordinate, or None if it cannot be reached.

		**`ExcelCell` itself, and never the class of the cell in hand.** That is exactly what
		`ExcelWorksheet._get_firstChild` and `navigationHelper` do, and the reason is the one
		that cost a whole reading: NVDA's objects are *composed*. `DynamicNVDAObjectType`
		builds an object from an API class, asks the application module for overlay classes,
		and mutates the object into a new type made of both — so the cell the reader is
		standing on is a `Dynamic_SpreadsheetCellExcelCell`, whose bases are this module's
		overlay and NVDA's cell.

		Ask for another of *those* and the metaclass does it all again: it offers the
		composite as the only class, this module inserts its overlay in front of it, and the
		bases come out as (overlay, (overlay, cell)) — which cannot be linearised. Python
		raises `TypeError: Cannot create a consistent method resolution order`, this returned
		None, and every cell of the band became "no cell at this coordinate". The layout was
		planned, the command said the columns were showing, and the display was blank.

		So the API class, which is what NVDA composes *from*, and the overlay is added to the
		result exactly as it is to any other cell Excel hands over. A merged coordinate reads
		as its top left content, which is what NVDA's own first child does.

		**For the cells that are drawn and routed into**, which after `textRow` is what this
		is for. Measuring a bandful of a sheet no longer comes through here.
		"""
		try:
			found = ExcelCell(
				windowHandle=self.cell.windowHandle,
				excelWindowObject=self.cell.excelWindowObject,
				excelCellObject=self._sheet.cells(row, column),
			)
		except CallCancelled:
			# Not "there is no cell here": NVDA has stopped waiting on Excel, and a reader
			# that hears "empty" instead is told the sheet is blank. See
			# `globalPlugins.brlMultiline.flow.CallCancelled`.
			raise
		except Exception:
			log.debugWarning(f"Could not reach row {row} column {column}", exc_info=True)
			return None
		if found is None:
			# The metaclass answers None rather than raising when it will not build one.
			log.debugWarning(f"NVDA would not build a cell at row {row} column {column}")
			return None
		# **The worksheet already in hand, given to the cell rather than left to be built.**
		# `ExcelCell._get_parent` makes a new `ExcelWorksheet` for every cell that is asked,
		# and a worksheet's header tracker is populated by walking every defined name in the
		# workbook — so asking a bandful of cells what their column's header is would have
		# walked the workbook once per cell. `parent` is an auto-property, so assigning it is
		# what stops the getter ever running.
		found.parent = self.obj
		return found


def _cellInfosFor(cell, address: str, count: int) -> Optional[list]:
	""":return: the column and text of each cell of a range, in one call, or None.

	The call itself, kept apart from `ExcelSheet.textRow` because it is the only ctypes in
	this file and because what it does is one sentence: hand NVDA's in-process helper an
	address and a count, and read back what it filled in.

	Only two of the twelve fields of an `EXCEL_CELLINFO` are asked for. The flags matter as
	much as the batching does — `NVCELLINFOFLAG_ALL`, which is what building a cell object
	fetches, gathers comments and formulas and states nobody is going to read.

	The address is asked for as well as the text and the coordinates, and it is what tells a
	merged cell from a blank one: the helper reports a cell's *merge area's* address, so every
	member of one merge answers with the same string. See `ExcelSheet.textRow`.

	:param cell: any cell of the sheet, for the window and the helper's binding handle.
	:param address: the range, already in the application's own notation.
	:param count: how many cells the range holds.
	:return: one (column number, text, address) triple per cell of the range, or None for
		anything short of the whole range.

	**Short is incomplete, not empty.** The helper walks the range with an enumerator and
	stops at the first cell it cannot get — `IEnumVARIANT::Next` failing breaks the loop and
	leaves the count where it got to — so the cells it did not reach are cells nobody has
	read. Padded with empty strings they measured as a table whose columns hold nothing,
	which is the failure this seam exists to prevent. The whole row goes back to being read
	cell by cell instead, which is slow and right.
	"""
	binding = cell.appModule.helperLocalBindingHandle
	if not binding:
		return None
	infos = (EXCEL_CELLINFO * count)()
	fetched = ctypes.c_long()
	result = NVDAHelper.localLib.nvdaInProcUtils_excel_getCellInfos(
		binding,
		cell.windowHandle,
		BSTR(address),
		NVCELLINFOFLAG_TEXT | NVCELLINFOFLAG_COORDS | NVCELLINFOFLAG_ADDRESS,
		count,
		infos,
		ctypes.byref(fetched),
	)
	if result != 0 or fetched.value != count:
		if result == 0 and fetched.value:
			log.debugWarning(
				f"Excel answered for {fetched.value} of {count} cells of {address}, "
				"so the row is being read cell by cell instead",
			)
		return None
	return [
		(int(infos[index].columnNumber or 0), infos[index].text or "", infos[index].address or "")
		for index in range(count)
	]


def sheetFor(cell):
	""":return: the worksheet a cell is in, or None if it cannot be read by coordinate.

	Three ways to answer no, and each of them leaves the reader with NVDA's ordinary reading
	of the cell rather than with a layout that is wrong or a display that has stopped:

	- **Nothing will say where the reader is.** See `ExcelSheet.where`.
	- **The sheet is too wide to measure.** See `MAX_COLUMNS`.
	- **The sheet cannot be reached at all**, which is any other failure.

	:param cell: the cell the reader is on.
	"""
	try:
		sheet = ExcelSheet(cell)
		if 0 in sheet.where():
			log.debugWarning("An Excel cell would not say which row and column it is")
			return None
		if sheet.tooWide():
			log.debugWarning(
				f"An Excel sheet says it is {sheet.shape()[1]} columns wide, which is more "
				f"than the {MAX_COLUMNS} that can be measured without stopping NVDA. "
				"Reading it as NVDA ordinarily would.",
			)
			return None
	except CallCancelled:
		# **Not "this is not a worksheet".** NVDA has stopped waiting on Excel, and answering
		# no here tells the reader they are not in a table at all — an explanation of
		# something that never happened, and one they cannot act on. It goes up to whoever
		# asked. See `globalPlugins.brlMultiline.flow.CallCancelled`.
		raise
	except Exception:
		log.debugWarning("Could not reach the worksheet behind a cell", exc_info=True)
		return None
	return sheet


def wantedHeaders() -> tuple:
	""":return: which of a cell's headers to show, as the attribute names to read them from.

	The reader's own setting, and the one speech obeys: "report table headers" in Document
	Formatting, which is one setting with two axes — rows, columns, both, or off. The order
	is speech's order too: the row's header, then the column's. See
	`speech.getPropertiesSpeech`, which appends them in that order after the coordinates.

	Read on each call rather than kept, because the reader can change it at any moment and
	the next cell they land on should be drawn under the answer that is true then.
	"""
	try:
		setting = config.conf["documentFormatting"]["reportTableHeaders"]
	except Exception:
		log.debugWarning("Could not read NVDA's reportTableHeaders setting", exc_info=True)
		setting = ReportTableHeaders.ROWS_AND_COLUMNS.value
	wanted = []
	if setting in (ReportTableHeaders.ROWS_AND_COLUMNS.value, ReportTableHeaders.ROWS.value):
		wanted.append("rowHeaderText")
	if setting in (ReportTableHeaders.ROWS_AND_COLUMNS.value, ReportTableHeaders.COLUMNS.value):
		wanted.append("columnHeaderText")
	return tuple(wanted)


def headerTextFor(cell) -> str:
	""":return: what a cell's headers say, or "" where there are none to say.

	The cell is asked exactly as speech asks it — `columnHeaderText` and `rowHeaderText` are
	NVDA's own properties, and on a worksheet cell they resolve through the header rows and
	columns the reader marked with NVDA+shift+c. Nothing here works out what a header is.

	:param cell: the `NVDAObjects.window.excel.ExcelCell` being drawn.
	"""
	said = []
	for name in wantedHeaders():
		try:
			answer = getattr(cell, name)
		except Exception:
			# A cancelled COM call among them, and it is caught here rather than handed
			# upward on purpose: braille is drawn from here, there is no boundary above
			# this to hand a failure to, and a header that could not be read this time
			# would cost the reader the cell as well as its header. The next update asks
			# again.
			log.debugWarning(f"Could not read a cell's {name}", exc_info=True)
			answer = None
		if answer:
			said.append(answer.strip())
	return TEXT_SEPARATOR.join(text for text in said if text)


class CellHeaders:
	"""A cell's one line of braille, with the header the reader marked after the coordinates.

	**Speech says it and braille did not.** Land on a cell in a sheet whose header row has
	been marked and NVDA speaks "9/4/2026  A2  Date"; the same cell in braille is "9/4/2026
	A2", and the only way to find out what the column was called was to leave the cell and
	come back. That is not a decision anybody took. `getPropertiesBraille` knows perfectly
	well what to do with a `columnHeaderText` — it is simply never given one, because it only
	looks for it beside a `columnNumber`, and `NVDAObjectRegion.update` sends neither. It
	sends the coordinates and stops.

	So the headers are put where speech puts them, after the coordinates, on the region that
	draws the cell.

	**Through `appendText` rather than into `rawText` afterwards.** `rawText` is what has
	already been handed to liblouis; changing it means translating the line a second time.
	`appendText` is what the base concatenates *before* translating, so this costs nothing —
	and it is put back afterwards, because a region is updated again whenever the cell
	changes underneath it and a header appended twice would be shown twice.

	Unlike speech, this does not go quiet on the second cell of a column. Speech is a stream
	of announcements and repeating the header on every cell of a row would be unbearable;
	braille is a standing description of where the reader is, and a header that vanished
	after the first cell would be a header the reader could not read.
	"""

	def update(self):
		theirs = self.appendText
		said = headerTextFor(self.obj)
		if said:
			self.appendText = TEXT_SEPARATOR + said + theirs
		try:
			super().update()
		finally:
			self.appendText = theirs


class CellRegion(CellHeaders, NVDAObjectRegion):
	"""A worksheet cell as braille draws it while following the focus."""


class ReviewCellRegion(CellHeaders, ReviewNVDAObjectRegion):
	"""The same cell while braille follows the review cursor.

	NVDA's review region differs in one thing — a routing key focuses the object first — and
	that difference is worth keeping, so there are two classes rather than one.
	"""


class HeadersInBraille:
	"""The overlay that gives a worksheet cell its header in braille.

	Separate from `SpreadsheetCell`, and not folded into it, because it is a different claim:
	`SpreadsheetCell` says this cell can be read by coordinate and is the seam the flow uses,
	while this says only that NVDA is about to draw a cell on one line and should say what
	its column is called. A cell this add-on will not lay out still deserves its header.
	"""

	def getBrailleRegions(self, review: bool = False):
		"""Yield the regions braille should draw this cell from, which is one region.

		**One is the whole answer for a cell**, and not an abbreviation of NVDA's own
		`braille.regions.focus.getFocusRegions`: that yields a second, text-reading region
		for an object with navigable text, and a worksheet cell has none — its role is a
		table cell, it is not editable text, and it carries no tree interceptor. So what NVDA
		makes for a cell is exactly one `NVDAObjectRegion`, and this is that region with the
		header added.

		The region is not updated here: `getFocusRegions` updates whatever this yields.

		:param review: whether braille is following the review cursor rather than the focus.
		"""
		yield (ReviewCellRegion if review else CellRegion)(self)


class SpreadsheetCell:
	"""The overlay on a worksheet cell: it offers a sheet, and it can be gone to.

	Two methods, and they are the whole interface between this application and the rest of the
	add-on. See the module docstring.
	"""

	def brlMultilineSheet(self):
		""":return: the worksheet this cell is in, as `flowObjectTable.Sheet`, or None."""
		return sheetFor(self)

	def setFocus(self):
		"""Go to this cell, which is what a routing key over it means.

		**`NVDAObject.setFocus` does nothing at all by default**, and an Excel cell inherits
		that — so routing into a column did exactly nothing, silently, which is the worst way
		for a core interaction to fail. NVDA's own Excel navigation says what going to a cell
		means and this is that sequence: select it, activate it, and tell NVDA the focus has
		arrived. See `ExcelBrowseModeTreeInterceptor.navigationHelper`, which is what the arrow
		keys do inside a sheet.

		The event is fired on this object rather than on a fresh one, since this object is
		already the cell being gone to.
		"""
		try:
			cell = self.excelCellObject
			cell.Select()
			cell.Activate()
		except Exception:
			log.debugWarning("Excel would not go to a cell", exc_info=True)
			return
		eventHandler.executeEvent("gainFocus", self)


def isAWorksheetCell(obj) -> bool:
	""":return: whether this object is a cell of Excel's COM object model.

	Which is the model whose cells carry the headers a reader marks: the marking scripts and
	`ExcelWorksheet.fetchAssociatedHeaderCellText` are on this branch. A UI Automation cell
	is a different class on a different branch and is left to NVDA, as it is for reading —
	see the module docstring.

	:param obj: the object NVDA has just built.
	"""
	return isinstance(obj, ExcelCell)


def readsByCoordinate(obj) -> bool:
	""":return: whether this object is a worksheet cell that can be read by coordinate.

	The COM model's cell and only that one. `NVDAObjects.UIA.excel.ExcelCell` is a different
	class on a different branch — it derives from `UIA`, not from `Window` — so this tells the
	two models apart exactly, and the two attributes are what the reading is actually made of.
	See the module docstring for why the UI Automation model is left alone.

	**And nothing here may ask the cell for its parent**, which is what this used to do to
	prove the worksheet was reachable. This runs inside `DynamicNVDAObjectType.__call__` —
	NVDA asks the application module to choose overlay classes for *every* object it builds —
	and `ExcelCell._get_parent` makes a whole `ExcelWorksheet`, whose header tracker is
	populated by walking every defined name in the workbook. So the one question asked to
	recognise a cell undid the sharing of the worksheet that `ExcelSheet.cellAt` arranges
	afterwards, once per cell of every band. It proved nothing either: a COM model cell's
	parent is an `ExcelWorksheet` by construction.

	:param obj: the object NVDA has just built.
	"""
	if not isinstance(obj, ExcelCell):
		return False
	return hasattr(obj, "excelCellObject") and hasattr(obj, "excelWindowObject")


OVERLAYS = (
	(HeadersInBraille, isAWorksheetCell),
	(SpreadsheetCell, readsByCoordinate),
)
"""The overlay classes this module adds, and what each of them is for.

Two, and deliberately not one: what a cell says its column is called is worth showing in
braille whether or not this add-on can lay the sheet out, and the two questions are answered
by different things about a cell. See `HeadersInBraille`.
"""


class AppModule(ExcelAppModule):
	"""NVDA's Excel module, with the overlay classes in L{OVERLAYS} added.

	Everything NVDA's own module does goes on happening: this subclasses it and calls `super`
	rather than standing in for it, which is what `nvdaBuiltin` exists for.
	"""

	def chooseNVDAObjectOverlayClasses(self, obj, clsList):
		super().chooseNVDAObjectOverlayClasses(obj, clsList)
		try:
			for overlay, belongs in OVERLAYS:
				# Not twice, and a class *made of* an overlay counts as having it. Such a
				# class is one NVDA already composed being offered for composing again, and
				# the bases would come out as (overlay, (overlay, cell)) — a pair with no
				# consistent method resolution order, so `type` raises and NVDA is left with
				# no object at all. See `ExcelSheet.cellAt` for what that looked like from
				# the display.
				if any(issubclass(found, overlay) for found in clsList):
					continue
				if belongs(obj):
					clsList.insert(0, overlay)
		except Exception:
			log.debugWarning("Could not tell whether this is a worksheet cell", exc_info=True)
