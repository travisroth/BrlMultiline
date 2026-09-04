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

import eventHandler
import NVDAHelper
from comtypes import BSTR
from logHandler import log
from NVDAHelper.localLib import EXCEL_CELLINFO
from NVDAObjects.window.excel import (
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
		written in, and it is one call.

		**And the cell the reader is standing in, whether or not Excel counts it as used.**
		Arrowing about a blank sheet does not enlarge the used range: on an empty workbook the
		active cell can be D20 while the used range is still A1, and a table of one row by one
		column with the reader at row 20 column 4 is a reading with nowhere to put them. The
		table therefore reaches at least as far as they do.
		"""
		row, column = self.where()
		try:
			used = self._sheet.usedRange
			rows = int(used.row) + int(used.rows.count) - 1
			columns = int(used.column) + int(used.columns.count) - 1
		except Exception:
			log.debugWarning("Could not ask a worksheet how far it is used", exc_info=True)
			rows = columns = 0
		return (max(1, rows, row), max(1, columns, column))

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
		for index, (column, text) in enumerate(said):
			# By the coordinate it came back with, and by its place in the answer only where
			# it came back without one. A cell that says which column it is in cannot be put
			# in the wrong one by a short or reordered answer.
			place = column - first if column else index
			if 0 <= place < count:
				texts[place] = text
		return texts

	# **There is deliberately no `columnHeaders` here**, and the reason is worth keeping.
	# It answered "no column of this sheet declares a header" from an empty
	# `ExcelWorksheet.headerCellTracker`, to save building a cell per column to be told
	# nothing — on a sheet twenty-one columns wide, the whole cost of that answer.
	#
	# It took the reader's header row off the display. The tracker is NVDA's own, private,
	# and populated lazily by walking every defined name in the workbook; an empty one means
	# "I know of none just now" and not "there are none", and every cell of that very sheet
	# could name its column through `columnHeaderText`. Answering the seam suppressed the
	# per-column ask outright, so the layout was built believing the table had no headings
	# and the band spent no row on them.
	#
	# The saving was real and small — one read per column, once per layout — and it is not
	# worth a guess about somebody else's private state. `flowTableSource.measure` no longer
	# lets an empty answer stop the ask either, but the cheapest way not to be wrong here is
	# not to answer.

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

	:param cell: any cell of the sheet, for the window and the helper's binding handle.
	:param address: the range, already in the application's own notation.
	:param count: how many cells the range holds.
	:return: one (column number, text) pair per cell the helper answered for, or None where
		it answered for none — which sends the caller back to reading cell by cell rather
		than letting a silence be read as a row of empty cells.
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
		NVCELLINFOFLAG_TEXT | NVCELLINFOFLAG_COORDS,
		count,
		infos,
		ctypes.byref(fetched),
	)
	if result != 0 or not fetched.value:
		return None
	return [
		(int(infos[index].columnNumber or 0), infos[index].text or "")
		for index in range(min(fetched.value, count))
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
	except Exception:
		log.debugWarning("Could not reach the worksheet behind a cell", exc_info=True)
		return None
	return sheet


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


class AppModule(ExcelAppModule):
	"""NVDA's Excel module, with one overlay class added.

	Everything NVDA's own module does goes on happening: this subclasses it and calls `super`
	rather than standing in for it, which is what `nvdaBuiltin` exists for.
	"""

	def chooseNVDAObjectOverlayClasses(self, obj, clsList):
		super().chooseNVDAObjectOverlayClasses(obj, clsList)
		try:
			# Not twice, and a class *made of* this overlay counts as having it. Such a
			# class is one NVDA already composed being offered for composing again, and the
			# bases would come out as (overlay, (overlay, cell)) — a pair with no consistent
			# method resolution order, so `type` raises and NVDA is left with no object at
			# all. See `ExcelSheet.cellAt` for what that looked like from the display.
			if any(issubclass(found, SpreadsheetCell) for found in clsList):
				return
			if readsByCoordinate(obj):
				clsList.insert(0, SpreadsheetCell)
		except Exception:
			log.debugWarning("Could not tell whether this is a worksheet cell", exc_info=True)
