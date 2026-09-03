# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for reading an Excel worksheet by coordinate.

The one file of the add-on that knows Excel exists, and until now the one with nothing behind
it: the generic sheet shape was tested over a stand-in and the application module itself was
not imported at all. A review named five things that could only go wrong in here — the overlay
never being inserted, a UI Automation cell being claimed, the used range being converted
wrongly, a missing coordinate becoming A1, and a routing key doing nothing — and every one of
them is reachable with fake COM objects.

**Nothing here needs Excel.** What the module actually depends on is small and can be stood
in for: a cell object with `row` and `column`, a worksheet answering `cells(row, column)` and
`usedRange`, and NVDA's own `ExcelCell` to wrap the result in. The stand-ins below are the
shape of those, and they record what was asked so a test can say what was *not* read as well
as what was.
"""

import ctypes
import os
import sys
import types
import unittest

from ._stubs import CallCancelled, installStubs, log

installStubs()

ADDON = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "addon"))

firedEvents: list = []
"""What `eventHandler.executeEvent` was told, so a test can see the focus arrive."""

fetches: list = []
"""Every batch cell fetch asked of NVDA's in-process helper, as (address, count)."""

batch: dict = {}
"""What the fake helper answers with, by address: a list of (column, text). An address it
does not know is answered for by position, which is what a helper that reports no coordinates
looks like."""

helperFails = [0]
"""What the fake helper returns as its status, so a test can make the call fail."""


class EXCEL_CELLINFO(ctypes.Structure):
	"""The four fields of NVDA's own struct that this module reads or fills in.

	A real `ctypes.Structure`, so that the array the module allocates and hands to the helper
	is a real array and the fake helper writes into it the way the real one does. Standing in
	for it with a dictionary would have tested the test.
	"""

	_fields_ = [
		("text", ctypes.c_wchar_p),
		("address", ctypes.c_wchar_p),
		("rowNumber", ctypes.c_long),
		("columnNumber", ctypes.c_long),
	]


def _getCellInfos(binding, window, address, flags, count, infos, fetched):
	"""Stand in for `nvdaInProcUtils_excel_getCellInfos`, filling the caller's array."""
	fetches.append((str(address), int(count), int(flags)))
	if helperFails[0]:
		return helperFails[0]
	said = batch.get(str(address))
	if said is None:
		fetched._obj.value = 0
		return 0
	for index, (column, text) in enumerate(said[:count]):
		infos[index].text = text
		infos[index].columnNumber = column
	fetched._obj.value = len(said[:count])
	return 0


def _install() -> None:
	"""Register what the application module imports, and put the add-on on the path."""
	if "nvdaBuiltin.appModules.excel" in sys.modules:
		return
	sys.modules["eventHandler"] = types.ModuleType("eventHandler")
	# The batch fetch, which is how a row is read in one call. See `ExcelSheet.textRow`.
	helper = types.ModuleType("NVDAHelper")
	helper.localLib = types.SimpleNamespace(nvdaInProcUtils_excel_getCellInfos=_getCellInfos)
	localLib = types.ModuleType("NVDAHelper.localLib")
	localLib.EXCEL_CELLINFO = EXCEL_CELLINFO
	sys.modules["NVDAHelper"] = helper
	sys.modules["NVDAHelper.localLib"] = localLib
	sys.modules["comtypes"] = types.ModuleType("comtypes")
	sys.modules["comtypes"].BSTR = str
	objects = types.ModuleType("NVDAObjects")
	objects.__path__ = []
	window = types.ModuleType("NVDAObjects.window")
	window.__path__ = []
	nvdaExcel = types.ModuleType("NVDAObjects.window.excel")
	nvdaExcel.NVCELLINFOFLAG_TEXT = 0x2
	nvdaExcel.NVCELLINFOFLAG_COORDS = 0x10
	nvdaExcel.xlA1 = 1
	# NVDA's own turns an invariant address into the application's notation by asking it for
	# its list separator. Recorded rather than reproduced: what matters here is that the
	# address the helper is given went through it.
	nvdaExcel.convertAddressToLocal = lambda application, address: address
	sys.modules["NVDAObjects"] = objects
	sys.modules["NVDAObjects.window"] = window
	sys.modules["NVDAObjects.window.excel"] = nvdaExcel
	sys.modules["eventHandler"].executeEvent = lambda name, obj, **kwargs: firedEvents.append(
		(name, obj),
	)
	builtin = types.ModuleType("nvdaBuiltin")
	builtin.__path__ = []
	modules = types.ModuleType("nvdaBuiltin.appModules")
	modules.__path__ = []
	excel = types.ModuleType("nvdaBuiltin.appModules.excel")

	class AppModule:
		"""NVDA's own Excel application module, as far as this add-on touches it."""

		def __init__(self):
			self.chosenFor = []

		def chooseNVDAObjectOverlayClasses(self, obj, clsList):
			# Recorded rather than ignored: the add-on's module must extend NVDA's rather
			# than stand in for it, and calling `super` is the whole of how that happens.
			self.chosenFor.append(obj)

	excel.AppModule = AppModule
	builtin.appModules = modules
	modules.excel = excel
	sys.modules["nvdaBuiltin"] = builtin
	sys.modules["nvdaBuiltin.appModules"] = modules
	sys.modules["nvdaBuiltin.appModules.excel"] = excel
	sys.modules.setdefault("logHandler", types.ModuleType("logHandler")).log = log
	if ADDON not in sys.path:
		sys.path.insert(0, ADDON)


_install()

from appModules import excel as excelModule  # noqa: E402

SALES = [
	["Region", "Q1", "Q2"],
	["North", "1200", "1310"],
	["South", "980", "1024"],
]


class FakeRange:
	"""Excel's own cell, which knows where it is and what it says."""

	def __init__(self, row, column, text=""):
		self.row = row
		self.column = column
		self.text = text
		self.selected = False
		self.activated = False
		self.Application = "an Excel"
		"""What an address is turned into the application's own notation through."""

	def Select(self):
		self.selected = True

	def Activate(self):
		self.activated = True


class FakeSpan:
	"""Several of Excel's cells at once, which is what a row is read through in one call."""

	def __init__(self, first, last):
		self.first = first
		self.last = last

	def address(self, rowAbsolute, columnAbsolute, style, external):
		return f"Sheet1!R{self.first.row}C{self.first.column}:C{self.last.column}"


class FakeUsedRange:
	"""What Excel considers written in, which is not the same as what is written in."""

	def __init__(self, row, column, rows, columns):
		self.row = row
		self.column = column
		self.rows = types.SimpleNamespace(count=rows)
		self.columns = types.SimpleNamespace(count=columns)


class FakeWorksheetObject:
	"""Excel's own worksheet: it answers by coordinate, and it records what it was asked."""

	def __init__(self, values=None, used=None):
		self.values = values if values is not None else SALES
		self.used = used if used is not None else FakeUsedRange(1, 1, 3, 3)
		self.asked: list = []
		self.spans: list = []
		self.timesAskedTheUsedRange = 0
		self.Application = "an Excel"

	def range(self, first, last):
		"""Excel's `Range(cell1, cell2)`, which is the span a whole row is fetched over."""
		self.spans.append(((first.row, first.column), (last.row, last.column)))
		return FakeSpan(first, last)

	@property
	def usedRange(self):
		self.timesAskedTheUsedRange += 1
		return self.used

	def cells(self, row, column):
		self.asked.append((row, column))
		said = ""
		if 1 <= row <= len(self.values) and 1 <= column <= len(self.values[row - 1]):
			said = self.values[row - 1][column - 1]
		return FakeRange(row, column, said)


class FakeWorksheet:
	"""NVDA's `ExcelWorksheet`, as far as this add-on touches it."""

	def __init__(self, sheet, marked=None):
		self.excelWorksheetObject = sheet
		self.built = 0
		# NVDA's record of what the reader has marked as a header row or column, which on
		# the real thing is populated by walking every defined name in the workbook. Empty on
		# every sheet nobody has marked up, which is what makes it worth asking.
		self.headerCellTracker = types.SimpleNamespace(infosDict=dict(marked or {}))


class FakeCell:
	"""NVDA's `ExcelCell`: it wraps one of Excel's cells and answers about it.

	Built the way the real one is — the class is called with the three keyword arguments — so
	that a test sees whichever cells the module constructs and what it gave them.
	"""

	made: list = []

	def __init__(self, windowHandle=None, excelWindowObject=None, excelCellObject=None):
		self.windowHandle = windowHandle
		self.excelWindowObject = excelWindowObject
		self.excelCellObject = excelCellObject
		self.rowNumber = excelCellObject.row if excelCellObject else None
		self.columnNumber = excelCellObject.column if excelCellObject else None
		self._parent = None
		self.appModule = types.SimpleNamespace(helperLocalBindingHandle=7)
		"""What the in-process helper is reached through. Falsy where NVDA has not injected
		it, and then there is no batch fetch to be had."""

		FakeCell.made.append(self)

	@property
	def parent(self):
		if self._parent is None:
			# What NVDA's own `_get_parent` does, and what costs a walk of every defined name
			# in the workbook when it is left to happen per cell.
			self._parent = FakeWorksheet(FakeWorksheetObject())
			self._parent.built = 1
		return self._parent

	@parent.setter
	def parent(self, value):
		self._parent = value


def aCell(row=2, column=1, sheet=None, values=None, used=None, marked=None):
	""":return: a worksheet cell with the add-on's overlay on it, as NVDA would build one."""

	class SpreadsheetCell(excelModule.SpreadsheetCell, FakeCell):
		"""The overlay over NVDA's cell, which is the order NVDA composes them in."""

	worksheet = FakeWorksheet(
		sheet if sheet is not None else FakeWorksheetObject(values, used),
		marked=marked,
	)
	cell = SpreadsheetCell(
		windowHandle=42,
		excelWindowObject=object(),
		excelCellObject=FakeRange(row, column),
	)
	cell.parent = worksheet
	return cell


class TestReadingAWholeRowInOneCall(unittest.TestCase):
	"""Measuring a table reads a bandful of rows across every column and keeps only the
	widths. Asked cell by cell, one cell of a worksheet is a coordinate lookup, an
	`NVDAObject` built with its overlay classes chosen, and a cross-process fetch of that
	cell's text, address, states, comments and formula.

	Twenty-one columns of two rows came to over ten seconds on hardware — past the watchdog's
	patience, so every read after that was cancelled and the sheet measured as empty. NVDA
	itself never reads a cell at a time when it wants many: `ExcelCellInfoQuicknavIterator`
	hands the helper an address and a count. So does this now.
	"""

	def setUp(self):
		fetches.clear()
		batch.clear()
		helperFails[0] = 0

	def _sheet(self, **kwargs):
		return excelModule.ExcelSheet(aCell(**kwargs))

	def _address(self, row=1, first=1, last=3):
		return f"Sheet1!R{row}C{first}:C{last}"

	def test_theRowComesBackAsTextAndInOneFetch(self):
		sheet = self._sheet()
		batch[self._address()] = [(1, "Region"), (2, "Q1"), (3, "Q2")]
		self.assertEqual(sheet.textRow(1, 1, 3), ["Region", "Q1", "Q2"])
		self.assertEqual(len(fetches), 1)

	def test_andNoCellObjectIsBuiltToGetIt(self):
		"""Which is the whole saving. Objects are still built for the cells that are drawn."""
		sheet = self._sheet()
		batch[self._address()] = [(1, "Region"), (2, "Q1"), (3, "Q2")]
		before = len(FakeCell.made)
		sheet.textRow(1, 1, 3)
		self.assertEqual(len(FakeCell.made), before)

	def test_andItAsksForTextAndCoordinatesAndNothingElse(self):
		"""`NVCELLINFOFLAG_ALL`, which is what building a cell fetches, gathers comments and
		formulas and states that nobody measuring a column is going to read."""
		sheet = self._sheet()
		batch[self._address()] = [(1, "Region"), (2, "Q1"), (3, "Q2")]
		sheet.textRow(1, 1, 3)
		self.assertEqual(fetches[0][2], 0x2 | 0x10)

	def test_andOverTheSpanItWasAskedFor(self):
		sheet = self._sheet()
		batch[self._address(row=2, first=2, last=3)] = [(2, "1200"), (3, "1310")]
		self.assertEqual(sheet.textRow(2, 2, 3), ["1200", "1310"])
		self.assertEqual(
			sheet.obj.excelWorksheetObject.spans,
			[((2, 2), (2, 3))],
		)

	def test_eachCellGoesInTheColumnItSaysItIsIn(self):
		"""By its own coordinate, so a short or reordered answer cannot put a value under
		the wrong heading."""
		sheet = self._sheet()
		batch[self._address()] = [(3, "Q2"), (1, "Region")]
		self.assertEqual(sheet.textRow(1, 1, 3), ["Region", "", "Q2"])

	def test_andWhereItSaysNothingItGoesWhereItArrived(self):
		sheet = self._sheet()
		batch[self._address()] = [(0, "Region"), (0, "Q1")]
		self.assertEqual(sheet.textRow(1, 1, 3), ["Region", "Q1", ""])

	def test_aHelperThatAnswersForNothingSendsTheReaderBackToTheCells(self):
		"""None means "ask me the ordinary way". A silence read as a row of empty cells is a
		table read as blank, which is the failure this whole seam was added after."""
		self.assertIsNone(self._sheet().textRow(1, 1, 3))

	def test_asDoesOneThatFailsOutright(self):
		helperFails[0] = 5
		batch[self._address()] = [(1, "Region")]
		self.assertIsNone(self._sheet().textRow(1, 1, 3))

	def test_asDoesOneNvdaHasNotInjected(self):
		cell = aCell()
		cell.appModule = types.SimpleNamespace(helperLocalBindingHandle=0)
		self.assertIsNone(excelModule.ExcelSheet(cell).textRow(1, 1, 3))

	def test_aSpanOfNoColumnsIsNotAsked(self):
		self.assertIsNone(self._sheet().textRow(1, 3, 1))
		self.assertEqual(fetches, [])

	def test_aCancelledFetchIsNotAnEmptyRow(self):
		"""It goes up to whoever asked for the reading, which is the only place that can tell
		the reader the sheet could not be read."""
		sheet = self._sheet()
		real = excelModule._cellInfosFor

		def cancelled(*args, **kwargs):
			raise CallCancelled("COM call cancelled")

		excelModule._cellInfosFor = cancelled
		self.addCleanup(setattr, excelModule, "_cellInfosFor", real)
		with self.assertRaises(CallCancelled):
			sheet.textRow(1, 1, 3)

	def test_anythingElseGoingWrongIsJustARowThisCannotRead(self):
		class Awkward(FakeWorksheetObject):
			def range(self, first, last):
				raise RuntimeError("no")

		self.assertIsNone(self._sheet(sheet=Awkward()).textRow(1, 1, 3))


class TestWhatTheColumnsOfASheetAreCalled(unittest.TestCase):
	"""A worksheet has no headers until a reader marks a row or a column as one, and NVDA
	keeps what they marked in the worksheet's header cell tracker.

	An empty tracker is a first-hand "no column of this sheet declares anything", and it is
	worth asking for because the alternative was building a cell per column to be told
	nothing — on a sheet twenty-one columns wide, the entire cost of the answer.
	"""

	def test_aSheetNobodyHasMarkedUpSaysSoWithoutTouchingACell(self):
		sheet = excelModule.ExcelSheet(aCell())
		self.assertEqual(sheet.columnHeaders(1, 3), {})
		self.assertEqual(sheet.obj.excelWorksheetObject.asked, [])

	def test_andOneWithMarkedHeadersSendsTheQuestionToTheCells(self):
		"""Which marked range applies to which column is NVDA's own work and it wants a cell
		to do it. None is how this says "ask them", and they are then asked once each."""
		sheet = excelModule.ExcelSheet(aCell(marked={(1, 1): "a header"}))
		self.assertIsNone(sheet.columnHeaders(1, 3))

	def test_asDoesAWorksheetThatWillNotSay(self):
		cell = aCell()

		class Silent:
			@property
			def headerCellTracker(self):
				raise RuntimeError("no")

			excelWorksheetObject = FakeWorksheetObject()

		cell.parent = Silent()
		self.assertIsNone(excelModule.ExcelSheet(cell).columnHeaders(1, 3))


class TestRecognisingAWorksheetCell(unittest.TestCase):
	"""Which cells the overlay goes on, and — as much — which it does not."""

	def test_aCellWithAWorksheetBehindItIsRead(self):
		self.assertTrue(excelModule.readsByCoordinate(aCell()))

	def test_aUiAutomationCellIsNot(self):
		"""It carries neither the cell object nor the window object, and NVDA has no
		coordinate lookup for it. Left alone rather than half read."""
		other = types.SimpleNamespace(rowNumber=2, columnNumber=1)
		self.assertFalse(excelModule.readsByCoordinate(other))

	def test_norIsSomethingElseInExcelAltogether(self):
		"""The formula bar, a dialog, a chart."""
		self.assertFalse(excelModule.readsByCoordinate(types.SimpleNamespace()))

	def test_norACellWhoseParentIsNotAWorksheet(self):
		cell = aCell()
		cell.parent = types.SimpleNamespace()
		self.assertFalse(excelModule.readsByCoordinate(cell))


class TestTheApplicationModuleExtendsNvdasOwn(unittest.TestCase):
	"""An add-on's module of a given name is loaded *instead of* the built-in one, so this has
	to extend NVDA's rather than stand beside it — or every Excel fix NVDA has would be lost
	the moment this add-on is installed."""

	def test_theOverlayIsAddedToAWorksheetCell(self):
		module = excelModule.AppModule()
		classes = []
		module.chooseNVDAObjectOverlayClasses(aCell(), classes)
		self.assertEqual(classes, [excelModule.SpreadsheetCell])

	def test_andNvdasOwnChoosingStillHappens(self):
		module = excelModule.AppModule()
		cell = aCell()
		module.chooseNVDAObjectOverlayClasses(cell, [])
		self.assertEqual(module.chosenFor, [cell])

	def test_andNothingIsAddedToAnythingElse(self):
		module = excelModule.AppModule()
		classes = []
		module.chooseNVDAObjectOverlayClasses(types.SimpleNamespace(), classes)
		self.assertEqual(classes, [])


class TestWhereTheReaderIs(unittest.TestCase):
	"""A missing coordinate must not quietly become A1: the whole reading would attach to the
	wrong place and nothing would say so."""

	def test_nvdasOwnAnswerIsTaken(self):
		self.assertEqual(aCell(row=5, column=3).brlMultilineSheet().where(), (5, 3))

	def test_andExcelsWhereNvdasIsSilent(self):
		"""NVDA's two come from its Excel helper, which can be silent while the COM range
		beside it knows perfectly well where it is."""
		cell = aCell(row=5, column=3)
		cell.rowNumber = None
		cell.columnNumber = None
		self.assertEqual(cell.brlMultilineSheet().where(), (5, 3))

	def test_andTheReadingIsDeclinedWhenNeitherWillSay(self):
		cell = aCell(row=5, column=3)
		cell.rowNumber = None
		cell.columnNumber = None
		cell.excelCellObject.row = 0
		cell.excelCellObject.column = 0
		self.assertIsNone(cell.brlMultilineSheet())


class TestHowFarTheSheetGoes(unittest.TestCase):
	"""The used range is what makes a layout possible at all — a sheet is a million rows — and
	it is also the thing most likely to be wrong in both directions."""

	def test_itIsCountedFromA1(self):
		"""So that a row number here is Excel's row number and nothing has to be translated."""
		used = FakeUsedRange(row=5, column=3, rows=4, columns=2)
		self.assertEqual(aCell(used=used).brlMultilineSheet().shape(), (8, 4))

	def test_andReachesTheReaderWhereverTheyAre(self):
		"""Arrowing about a blank sheet does not enlarge the used range: the active cell can
		be D20 while the used range is still A1, and a table of one by one with the reader at
		row 20 column 4 is a reading with nowhere to put them."""
		used = FakeUsedRange(row=1, column=1, rows=1, columns=1)
		sheet = aCell(row=20, column=4, used=used).brlMultilineSheet()
		self.assertEqual(sheet.shape(), (20, 4))

	def test_andIsTheReadersOwnPlaceWhenExcelWillNotSay(self):
		cell = aCell(row=7, column=2)
		cell.parent.excelWorksheetObject.used = None
		self.assertEqual(cell.brlMultilineSheet().shape(), (7, 2))

	def test_aSheetTooWideToMeasureIsLeftToNvda(self):
		"""Excel's used range grows to whatever has ever been *formatted* and never shrinks,
		so a fill colour once applied to a row reports sixteen thousand columns. Measuring
		that is a hundred and fifty thousand calls on the thread NVDA answers on, and the
		display would simply stop."""
		used = FakeUsedRange(row=1, column=1, rows=1, columns=excelModule.MAX_COLUMNS + 1)
		self.assertIsNone(aCell(used=used).brlMultilineSheet())

	def test_andOneMerelyWideIsNot(self):
		used = FakeUsedRange(row=1, column=1, rows=1, columns=excelModule.MAX_COLUMNS)
		self.assertIsNotNone(aCell(used=used).brlMultilineSheet())

	def test_andADistantCellCannotMakeAWideOneEither(self):
		"""The reader is reached, but reaching them must not be a way round the bound."""
		used = FakeUsedRange(row=1, column=1, rows=1, columns=1)
		self.assertIsNone(aCell(column=excelModule.MAX_COLUMNS + 1, used=used).brlMultilineSheet())


class TestReachingACell(unittest.TestCase):
	def setUp(self):
		FakeCell.made = []

	def test_aCellIsFetchedByCoordinate(self):
		sheet = aCell().brlMultilineSheet()
		found = sheet.cellAt(2, 3)
		self.assertEqual(found.excelCellObject.text, "1310")
		self.assertEqual(sheet.obj.excelWorksheetObject.asked, [(2, 3)])

	def test_andIsWrappedInNvdasOwnCellClass(self):
		"""So that everything asked of it afterwards is NVDA's answer rather than a guess."""
		cell = aCell()
		found = cell.brlMultilineSheet().cellAt(2, 1)
		self.assertIsInstance(found, type(cell))

	def test_andIsGivenTheWorksheetAlreadyInHand(self):
		"""`ExcelCell._get_parent` builds a new worksheet per cell, and a worksheet's header
		tracker is populated by walking every defined name in the workbook. Left to happen,
		asking a bandful of cells for their column's header would walk the workbook once per
		cell."""
		cell = aCell()
		found = cell.brlMultilineSheet().cellAt(3, 2)
		self.assertIs(found.parent, cell.parent)
		self.assertEqual(found.parent.built, 0)

	def test_andACellThatCannotBeReachedIsNone(self):
		cell = aCell()

		def refuses(row, column):
			raise RuntimeError("no")

		cell.parent.excelWorksheetObject.cells = refuses
		self.assertIsNone(cell.brlMultilineSheet().cellAt(2, 1))


class TestGoingToACell(unittest.TestCase):
	"""What a routing key over a column means. `NVDAObject.setFocus` does nothing at all by
	default and an Excel cell inherits that, so routing did exactly nothing — silently, which
	is the worst way for a core interaction to fail."""

	def setUp(self):
		firedEvents.clear()
		self.addCleanup(firedEvents.clear)

	def test_theCellIsSelectedAndActivated(self):
		"""NVDA's own Excel navigation says what going to a cell means, and this is it."""
		cell = aCell()
		cell.setFocus()
		self.assertTrue(cell.excelCellObject.selected)
		self.assertTrue(cell.excelCellObject.activated)

	def test_andNvdaIsToldTheFocusArrived(self):
		cell = aCell()
		cell.setFocus()
		self.assertEqual(firedEvents, [("gainFocus", cell)])

	def test_andACellExcelRefusesSaysNothingAndMovesNothing(self):
		cell = aCell()

		def refuses():
			raise RuntimeError("no")

		cell.excelCellObject.Select = refuses
		cell.setFocus()
		self.assertEqual(firedEvents, [])

	def test_andACellFetchedByCoordinateCanBeGoneToAsWell(self):
		"""Which is the case that matters: the reader routes into a cell the band fetched,
		not into the one they were already on."""
		found = aCell().brlMultilineSheet().cellAt(3, 2)
		found.setFocus()
		self.assertTrue(found.excelCellObject.activated)
		self.assertEqual(firedEvents, [("gainFocus", found)])


if __name__ == "__main__":
	unittest.main()
