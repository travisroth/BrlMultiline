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

import eventHandler
from logHandler import log
from nvdaBuiltin.appModules.excel import AppModule as ExcelAppModule

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

	def cellAt(self, row: int, column: int):
		""":return: the cell at one coordinate, or None if it cannot be reached.

		Wrapped in NVDA's own `ExcelCell`, exactly as `ExcelWorksheet._get_firstChild` does
		it, so that everything asked of the cell afterwards is NVDA's answer and not this
		add-on's guess at one.
		"""
		try:
			found = type(self.cell)(
				windowHandle=self.cell.windowHandle,
				excelWindowObject=self.cell.excelWindowObject,
				excelCellObject=self._sheet.cells(row, column),
			)
		except Exception:
			log.debugWarning(f"Could not reach row {row} column {column}", exc_info=True)
			return None
		# **The worksheet already in hand, given to the cell rather than left to be built.**
		# `ExcelCell._get_parent` makes a new `ExcelWorksheet` for every cell that is asked,
		# and a worksheet's header tracker is populated by walking every defined name in the
		# workbook — so asking a bandful of cells what their column's header is would have
		# walked the workbook once per cell. `parent` is an auto-property, so assigning it is
		# what stops the getter ever running.
		found.parent = self.obj
		return found


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

	The COM model's cell and only that one. It is recognised by what makes the reading
	possible — a cell object whose worksheet can be asked for another cell — rather than by
	its class, so a cell missing either is left alone rather than half read. The UI Automation
	model's cell has neither; see the module docstring.

	:param obj: the object NVDA has just built.
	"""
	if not hasattr(obj, "excelCellObject") or not hasattr(obj, "excelWindowObject"):
		return False
	return hasattr(obj.parent, "excelWorksheetObject")


class AppModule(ExcelAppModule):
	"""NVDA's Excel module, with one overlay class added.

	Everything NVDA's own module does goes on happening: this subclasses it and calls `super`
	rather than standing in for it, which is what `nvdaBuiltin` exists for.
	"""

	def chooseNVDAObjectOverlayClasses(self, obj, clsList):
		super().chooseNVDAObjectOverlayClasses(obj, clsList)
		try:
			if readsByCoordinate(obj):
				clsList.insert(0, SpreadsheetCell)
		except Exception:
			log.debugWarning("Could not tell whether this is a worksheet cell", exc_info=True)
