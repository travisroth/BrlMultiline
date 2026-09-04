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
	for index, entry in enumerate(said[:count]):
		column, text = entry[0], entry[1]
		infos[index].text = text
		infos[index].columnNumber = column
		# The merge area's address, which is what the real helper reports and what tells a
		# merged cell from a blank one. Its own where a test does not say.
		infos[index].address = entry[2] if len(entry) > 2 else f"R1C{column or index + 1}"
	fetched._obj.value = len(said[:count])
	return 0


built: list = []
"""Which class each object was built from, so a test can say it was not the composed one."""

_composed: dict = {}
"""Composed classes by their bases, which is what makes two of them the same class."""


class DynamicType(type):
	"""What NVDA's `DynamicNVDAObjectType` does to every object it builds.

	**NVDA's objects are composed, not subclassed**, and reproducing that here is the whole
	point of this stand-in. An object is made from an API class, the application module is
	asked which overlay classes belong on it, and the object is then *mutated* into a new type
	whose bases are the overlays in front of the API class.

	Which means asking for another object of an already-composed class asks for the
	composition to happen a second time, and the bases come out as (overlay, (overlay, cell))
	— a pair with no consistent method resolution order. Python raises, and on hardware that
	turned into a laid-out table with a blank display. A stand-in that simply constructed
	whatever class it was handed could not have shown that, and did not.
	"""

	def __call__(cls, **kwargs):
		obj = cls.__new__(cls)
		obj.__init__(**kwargs)
		obj.APIClass = cls
		built.append(cls)
		classes = [cls]
		excelModule.AppModule().chooseNVDAObjectOverlayClasses(obj, classes)
		bases = tuple(
			found
			for index, found in enumerate(classes)
			if index == 0 or not issubclass(classes[index - 1], found)
		)
		if len(bases) > 1:
			# Cached by its bases, as NVDA's `_dynamicClassCache` is, so that two cells
			# composed the same way are of the same class and can be compared.
			composed = _composed.get(bases)
			if composed is None:
				name = "Dynamic_" + "".join(found.__name__ for found in classes)
				composed = type(name, bases, {"__module__": __name__})
				_composed[bases] = composed
			obj.__class__ = composed
		return obj


class FakeCell(metaclass=DynamicType):
	"""NVDA's `ExcelCell`: it wraps one of Excel's cells and answers about it.

	Built the way the real one is — the class is called with the three keyword arguments, and
	the metaclass composes the overlay on afterwards — so that a test sees whichever cells the
	module constructs, what it gave them, and what class it asked for.
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
	def columnHeaderText(self):
		""":return: what this cell says its column is called, as NVDA's own cell does.

		Resolved through the worksheet, which is where NVDA resolves it: the reader marks a
		header row and every cell of the column answers for it. This is the answer the flow
		uses, and the one an empty header cell tracker made it stop asking for."""
		said = getattr(self.parent, "marked", {}).get(self.columnNumber)
		if said:
			return said
		return self.parent.headingFor(self.rowNumber, self.columnNumber)

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
	nvdaExcel.NVCELLINFOFLAG_ADDRESS = 0x1
	nvdaExcel.NVCELLINFOFLAG_TEXT = 0x2
	nvdaExcel.NVCELLINFOFLAG_COORDS = 0x10
	nvdaExcel.xlA1 = 1
	# NVDA's own turns an invariant address into the application's notation by asking it for
	# its list separator. Recorded rather than reproduced: what matters here is that the
	# address the helper is given went through it.
	nvdaExcel.convertAddressToLocal = lambda application, address: address
	# The API class the module builds cells from, and the one it tells a COM model cell by.
	nvdaExcel.ExcelCell = FakeCell
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

	def __init__(self, values=None, used=None, name="Sheet1", book=r"C:\\books\\sales.xlsx"):
		self.values = values if values is not None else SALES
		self.used = used if used is not None else FakeUsedRange(1, 1, 3, 3)
		self.asked: list = []
		self.spans: list = []
		self.timesAskedTheUsedRange = 0
		self.Application = "an Excel"
		self.name = name
		self.parent = types.SimpleNamespace(fullName=book, name="sales.xlsx")
		"""The workbook, which is what tells one worksheet from another with the same
		headings. See `ExcelSheet.whereIsIt`."""

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


class FakeHeaderCellInfo:
	"""One entry of NVDA's header cell tracker: a cell that heads a run of columns."""

	def __init__(
		self,
		rowNumber,
		columnNumber,
		rowSpan=1,
		colSpan=1,
		minColumnNumber=None,
		maxColumnNumber=None,
		minRowNumber=None,
		maxRowNumber=None,
		isColumnHeader=True,
		isRowHeader=False,
	):
		self.rowNumber = rowNumber
		self.columnNumber = columnNumber
		self.rowSpan = rowSpan
		self.colSpan = colSpan
		self.minColumnNumber = minColumnNumber
		self.maxColumnNumber = maxColumnNumber
		self.minRowNumber = minRowNumber
		self.maxRowNumber = maxRowNumber
		self.isColumnHeader = isColumnHeader
		self.isRowHeader = isRowHeader


class FakeHeaderCellTracker:
	"""`tableUtils.HeaderCellTracker`, which is where what a reader marked ends up.

	The two attributes NVDA fills in and this add-on reads: the entries, and their keys in
	the order NVDA walks them. Built here rather than summarised, because the whole question
	is what an entry says about which columns, and a summary would have answered it for the
	test rather than for the module.
	"""

	def __init__(self, headings=()):
		self.infosDict = {}
		self.listByRow = []
		for heading in headings:
			info = FakeHeaderCellInfo(**heading)
			key = (info.rowNumber, info.columnNumber)
			self.infosDict[key] = info
			self.listByRow.append(key)
		self.listByRow.sort(reverse=True)


class FakeWorksheet:
	"""NVDA's `ExcelWorksheet`, as far as this add-on touches it."""

	def __init__(self, sheet, marked=None, headings=None):
		self.excelWorksheetObject = sheet
		self.built = 0
		self.marked = dict(marked or {})
		"""What a cell of this sheet answers `columnHeaderText` with, by column number.

		Set on its own, it stands for the case that cost the reader their header row: the
		tracker says nothing and the cells say plenty. NVDA resolves the two from the same
		place, so they disagree only when the tracker was not built — which happens, because
		NVDA keeps whatever the walk of the workbook's defined names produced."""

		self.headings = list(headings or [])
		"""What the reader marked, as the tracker's own entries. See `headerCellTracker`."""

		self.timesAskedTheTracker = 0

	@property
	def headerCellTracker(self):
		"""NVDA's, built on being asked and kept. Counted, because asking is a walk of every
		defined name in the workbook and doing it per cell is what this seam is avoiding."""
		self.timesAskedTheTracker += 1
		return FakeHeaderCellTracker(self.headings)

	def headingFor(self, row, column):
		""":return: what a cell's column is called, the way `fetchAssociatedHeaderCellText`
		works it out: the first entry that covers the column and sits above the cell, read as
		the text at (its row, this column) and joined down its rows."""
		for key in sorted(FakeHeaderCellTracker(self.headings).infosDict, reverse=True):
			info = FakeHeaderCellTracker(self.headings).infosDict[key]
			if not info.isColumnHeader or column < info.columnNumber:
				continue
			if info.maxColumnNumber and column > info.maxColumnNumber:
				continue
			if row < info.rowNumber + info.rowSpan:
				return None
			said = " ".join(
				self.excelWorksheetObject.cells(at, column).text
				for at in range(info.rowNumber, info.rowNumber + info.rowSpan)
			).strip()
			if said:
				return said
		return None


def aCell(row=2, column=1, sheet=None, values=None, used=None, marked=None, headings=None):
	""":return: a worksheet cell with the add-on's overlay on it, as NVDA would build one.

	Through the metaclass rather than by naming a composed class, because that is the order
	NVDA does it in and the order is what matters. See `DynamicType`.
	"""
	worksheet = FakeWorksheet(
		sheet if sheet is not None else FakeWorksheetObject(values, used),
		marked=marked,
		headings=headings,
	)
	cell = FakeCell(
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

	def test_andItAsksForThreeFieldsAndNothingElse(self):
		"""Text, coordinates and the address. `NVCELLINFOFLAG_ALL`, which is what building a
		cell fetches, gathers comments and formulas and states that nobody measuring a column
		is going to read."""
		sheet = self._sheet()
		batch[self._address()] = [(1, "Region"), (2, "Q1"), (3, "Q2")]
		sheet.textRow(1, 1, 3)
		self.assertEqual(fetches[0][2], 0x1 | 0x2 | 0x10)

	def test_andOverTheSpanItWasAskedFor(self):
		sheet = self._sheet()
		batch[self._address(row=2, first=2, last=3)] = [(2, "1200"), (3, "1310")]
		self.assertEqual(sheet.textRow(2, 2, 3), ["1200", "1310"])
		self.assertEqual(
			sheet.obj.excelWorksheetObject.spans,
			[((2, 2), (2, 3))],
		)

	def test_eachCellGoesInTheColumnItSaysItIsIn(self):
		"""By its own coordinate, so a reordered answer cannot put a value under the wrong
		heading."""
		sheet = self._sheet()
		batch[self._address()] = [(3, "Q2"), (2, ""), (1, "Region")]
		self.assertEqual(sheet.textRow(1, 1, 3), ["Region", "", "Q2"])

	def test_andWhereItSaysNothingItGoesWhereItArrived(self):
		sheet = self._sheet()
		batch[self._address()] = [(0, "Region"), (0, "Q1"), (0, "Q2")]
		self.assertEqual(sheet.textRow(1, 1, 3), ["Region", "Q1", "Q2"])

	def test_aShortAnswerIsAnUnfinishedOneAndIsRefused(self):
		"""**The helper stops at the first cell it cannot reach.** It walks the range with an
		enumerator and breaks out of the loop when `Next` fails, so the cells it did not get
		to are cells nobody has read. Padded with empty strings they measured as columns that
		hold nothing — which is a table read as blank, the exact failure this seam was added
		to prevent — and the values were sitting there in the ordinary cell objects."""
		sheet = self._sheet()
		batch[self._address()] = [(1, "Region")]
		self.assertIsNone(sheet.textRow(1, 1, 3))

	def test_aMergedCellReadsAsItsContentUnderEveryColumnItSpans(self):
		"""The helper reads `.text` off each cell of the range and only the top left member
		of a merge holds it, so the rest came back empty and the column measured as one that
		holds nothing. The address it reports is the merge area's, so the members of one
		merge are exactly the cells that answer with the same string."""
		sheet = self._sheet()
		batch[self._address()] = [
			(1, "Region", "R1C1"),
			(2, "First half", "R1C2:R1C3"),
			(3, "", "R1C2:R1C3"),
		]
		self.assertEqual(sheet.textRow(1, 1, 3), ["Region", "First half", "First half"])

	def test_andTwoBlankCellsSideBySideStayBlank(self):
		"""They are not a merge: each answers with an address of its own."""
		sheet = self._sheet()
		batch[self._address()] = [(1, "Region"), (2, ""), (3, "")]
		self.assertEqual(sheet.textRow(1, 1, 3), ["Region", "", ""])

	def test_theTextIsStrippedTheWayACellsOwnIs(self):
		"""A column measured from the space Excel pads a value with is a column wider than
		what will be drawn in it. See `flowTableSource._textOf`, which is the other path."""
		sheet = self._sheet()
		batch[self._address()] = [(1, "  Region  "), (2, "Q1"), (3, "Q2")]
		self.assertEqual(sheet.textRow(1, 1, 3), ["Region", "Q1", "Q2"])

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


class TestWhichWorksheetThisIs(unittest.TestCase):
	"""What a saved layout is remembered against. For a control that is the application and
	the window class, and every worksheet of every workbook is "excel" and "EXCEL7" — so two
	workbooks with the same headings were each other's saved layout, which a review found.
	"""

	def _sheet(self, **kwargs):
		return excelModule.ExcelSheet(aCell(sheet=FakeWorksheetObject(**kwargs)))

	def test_aSheetIsNamedByItsWorkbookAndItsOwnName(self):
		self.assertEqual(self._sheet().whereIsIt(), r"C:\\books\\sales.xlsx/Sheet1")

	def test_soTwoSheetsOfOneWorkbookAreDifferentTables(self):
		self.assertNotEqual(self._sheet().whereIsIt(), self._sheet(name="Sheet2").whereIsIt())

	def test_andSoAreTwoWorkbooksWithTheSameSheetName(self):
		self.assertNotEqual(
			self._sheet().whereIsIt(),
			self._sheet(book=r"C:\\books\\other.xlsx").whereIsIt(),
		)

	def test_anUnsavedWorkbookIsStillNamedByWhatItHas(self):
		"""It has no path yet. Its name is still better than naming every sheet alike."""
		sheet = self._sheet(book="")
		self.assertEqual(sheet.whereIsIt(), "sales.xlsx/Sheet1")

	def test_andASheetThatWillNotSayIsNamedTheOrdinaryWay(self):
		sheet = FakeWorksheetObject()
		del sheet.parent
		self.assertIsNone(excelModule.ExcelSheet(aCell(sheet=sheet)).whereIsIt())


class TestAskingExcelHowFarTheSheetGoes(unittest.TestCase):
	"""The used range is one call, and it was being made over and over.

	Recognising a table asks the shape twice on its own — once to find out whether the sheet
	is too wide to measure, once for the table's own dimensions — and the table is resolved
	several times for one move between cells. A review counted eight used range reads before
	any new content was fetched.
	"""

	def test_theUsedRangeIsAskedForOnce(self):
		sheet = excelModule.ExcelSheet(aCell())
		sheet.shape()
		sheet.shape()
		sheet.shape()
		self.assertEqual(sheet.obj.excelWorksheetObject.timesAskedTheUsedRange, 1)

	def test_butWhereTheReaderIsIsAskedEveryTime(self):
		"""It is what the answer is stretched to cover — a reader arrowing about a blank sheet
		does not enlarge the used range — and asking costs nothing."""
		cell = aCell(row=2, column=1)
		sheet = excelModule.ExcelSheet(cell)
		self.assertEqual(sheet.shape(), (3, 3))
		cell.rowNumber = 20
		cell.columnNumber = 4
		self.assertEqual(sheet.shape(), (20, 4))


class TestWhatTheColumnsOfASheetAreCalled(unittest.TestCase):
	"""**The question that nearly froze a worksheet, and the one that has now been got wrong
	in both directions.**

	Asked cell by cell it costs an `ExcelCell` built and a header search per column, three
	times over for a column that says nothing: sixty three cell objects on a twenty one column
	sheet nobody has marked up, before a row the reader can feel has been read. Forty two was
	already enough to pass the watchdog on hardware.

	Answered from an empty `ExcelWorksheet.headerCellTracker` it cost the reader their header
	row, which is the other direction. The tracker is built by walking every defined name in
	the workbook and NVDA keeps whatever that walk produced — including nothing, where it
	failed part way — so an empty one can mean "I know of none just now" while every cell of
	that same sheet names its column perfectly well.

	So the tracker is read for what it positively says, and its silence is checked against one
	cell before it is believed.
	"""

	def setUp(self):
		fetches.clear()
		batch.clear()
		helperFails[0] = 0

	ACROSS = [{"rowNumber": 1, "columnNumber": 1, "maxColumnNumber": 3}]
	"""A header row marked across the sheet, which is what a reader marks."""

	def _sheet(self, **kwargs):
		return excelModule.ExcelSheet(aCell(**kwargs))

	def test_aMarkedHeaderRowIsReadInOneCall(self):
		sheet = self._sheet(headings=self.ACROSS)
		batch["Sheet1!R1C1:C3"] = [(1, "Region"), (2, "Q1"), (3, "Q2")]
		self.assertEqual(sheet.columnHeaders(1, 3), {1: "Region", 2: "Q1", 3: "Q2"})
		self.assertEqual(len(fetches), 1)

	def test_andNoCellIsBuiltToGetIt(self):
		"""Which is the whole saving: one call rather than one cell object per column, three
		times over for the columns that say nothing."""
		sheet = self._sheet(headings=self.ACROSS)
		batch["Sheet1!R1C1:C3"] = [(1, "Region"), (2, "Q1"), (3, "Q2")]
		before = len(FakeCell.made)
		sheet.columnHeaders(1, 3)
		self.assertEqual(len(FakeCell.made), before)

	def test_andTheAnswerIsWorkedOutOnce(self):
		"""Asking is a walk of every defined name in the workbook."""
		sheet = self._sheet(headings=self.ACROSS)
		batch["Sheet1!R1C1:C3"] = [(1, "Region"), (2, "Q1"), (3, "Q2")]
		sheet.columnHeaders(1, 3)
		sheet.columnHeaders(1, 3)
		sheet.columnHeaders(2, 3)
		self.assertEqual(len(fetches), 1)
		self.assertEqual(sheet.obj.timesAskedTheTracker, 1)

	def test_aColumnOutsideWhatWasMarkedIsNotNamed(self):
		"""A heading marked from column two does not name column one — which is
		`iterPossibleHeaderCellInfosFor`'s own rule, and the reader's partial header range."""
		sheet = self._sheet(headings=[{"rowNumber": 1, "columnNumber": 2, "maxColumnNumber": 3}])
		batch["Sheet1!R1C2:C3"] = [(2, "Q1"), (3, "Q2")]
		self.assertEqual(sheet.columnHeaders(1, 3), {2: "Q1", 3: "Q2"})

	def test_aSheetNobodyHasMarkedSaysSoOutright(self):
		"""An empty mapping, which is an answer: this sheet declares no headers. It is what
		saves an unmarked sheet a cell per column for a question whose answer is nothing."""
		self.assertEqual(self._sheet().columnHeaders(1, 3), {})

	def test_andProvesItWithOneCellRatherThanNone(self):
		"""The check that the last attempt did not make. One cell, not one per column."""
		sheet = self._sheet()
		before = len(FakeCell.made)
		sheet.columnHeaders(1, 3)
		self.assertEqual(len(FakeCell.made) - before, 1)

	def test_soATrackerThatIsWrongIsNotBelieved(self):
		"""**The reader's own fault, in one test.** The tracker says nothing and the cells say
		plenty; NVDA resolves both from the same place, so they disagree only when the walk
		that fills the tracker did not finish — and NVDA keeps the empty one it made first."""
		sheet = self._sheet(marked={1: "Date", 2: "Al"})
		self.assertIsNone(sheet.columnHeaders(1, 3))

	def test_aHeadingSeveralRowsTallIsLeftToNvda(self):
		"""NVDA joins the rows of the span together itself; this reads one row."""
		sheet = self._sheet(headings=[{"rowNumber": 1, "columnNumber": 1, "rowSpan": 2}])
		self.assertIsNone(sheet.columnHeaders(1, 3))

	def test_asIsAHeadingThatOnlyAppliesToSomeRows(self):
		"""It is not a property of the column, so it cannot be answered per column."""
		sheet = self._sheet(
			headings=[{"rowNumber": 1, "columnNumber": 1, "maxColumnNumber": 3, "maxRowNumber": 9}],
		)
		self.assertIsNone(sheet.columnHeaders(1, 3))

	def test_andARowHeaderIsNotAColumnHeader(self):
		sheet = self._sheet(
			headings=[{"rowNumber": 1, "columnNumber": 1, "isColumnHeader": False, "isRowHeader": True}],
		)
		self.assertEqual(sheet.columnHeaders(1, 3), {})

	def test_aHeaderRowThatCannotBeReadInOneGoIsLeftToTheCells(self):
		"""Guessing which of those columns it would have named is worse than asking them."""
		sheet = self._sheet(headings=self.ACROSS)
		self.assertIsNone(sheet.columnHeaders(1, 3))

	def test_andACellStillNamesItsColumn(self):
		"""Which is what the flow falls back to, through `flowObjectTable.headerTextOf`."""
		sheet = self._sheet(marked={2: "Q1"})
		self.assertEqual(sheet.cellAt(3, 2).columnHeaderText, "Q1")

	def test_andACellNamesItFromTheMarkedRowToo(self):
		"""The stand-in resolves a header the way NVDA does — the text at (the marked row,
		this column) — so the two paths cannot quietly answer differently."""
		sheet = self._sheet(headings=self.ACROSS)
		self.assertEqual(sheet.cellAt(3, 2).columnHeaderText, "Q1")

	def test_butACellOfTheMarkedRowHasNoHeaderAboveIt(self):
		"""Which is what made the first cell asked the worst possible witness, and why the
		one cell this asks is never a cell of row one."""
		sheet = self._sheet(headings=self.ACROSS)
		self.assertIsNone(sheet.cellAt(1, 2).columnHeaderText)


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

	def test_norACellMissingWhatTheReadingIsMadeOf(self):
		cell = aCell()
		del cell.excelCellObject
		self.assertFalse(excelModule.readsByCoordinate(cell))

	def test_andTheCellIsNeverAskedForItsParentToFindOut(self):
		"""This runs inside NVDA's object construction, for every object it builds, and
		`ExcelCell._get_parent` makes a whole `ExcelWorksheet` — whose header tracker is
		populated by walking every defined name in the workbook. Asking it here undid the
		sharing of the worksheet that `ExcelSheet.cellAt` arranges, once per cell of a band."""
		cell = FakeCell(
			windowHandle=42,
			excelWindowObject=object(),
			excelCellObject=FakeRange(2, 1),
		)
		self.assertTrue(excelModule.readsByCoordinate(cell))
		self.assertIsNone(cell._parent)


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

	def test_aSheetThatWillNotSayHowFarItGoesIsLeftToNvda(self):
		"""**Unknown rather than nothing**, which a review asked for. The fallback was zero,
		and zero leaves the reader's own coordinate as the whole of the answer — so a
		worksheet that would not say was presented as a table one cell bigger than wherever
		they were standing, a truncated sheet offered as though it were the sheet. Declining
		it leaves them NVDA's ordinary reading of the cell, which is right and says so."""
		cell = aCell(row=7, column=2)
		cell.parent.excelWorksheetObject.used = None
		self.assertIsNone(cell.brlMultilineSheet())

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


class TestBuildingACellTheWayNvdaComposesThem(unittest.TestCase):
	"""The fault that made a laid-out table come out blank.

	NVDA's objects are composed rather than subclassed: `DynamicNVDAObjectType` builds one
	from an API class, asks the application module for overlay classes, and mutates the object
	into a type made of the overlays in front of that API class. So the cell the reader stands
	on is a `Dynamic_SpreadsheetCellExcelCell`.

	`cellAt` asked for another of *those*, by writing `type(self.cell)`. The metaclass composed
	it a second time — offering the composite as the only class, this module putting its
	overlay in front of it — and (overlay, (overlay, cell)) has no consistent method
	resolution order. Python raised, the broad catch turned it into None, and every cell of the
	band came back as "no cell at this coordinate". The command reported the columns were
	showing, the log listed their widths, and the display was empty.
	"""

	def setUp(self):
		built.clear()

	def test_aCellIsBuiltFromTheApiClassAndNotFromTheComposedOne(self):
		sheet = aCell().brlMultilineSheet()
		built.clear()
		sheet.cellAt(2, 2)
		self.assertEqual(built, [FakeCell])

	def test_andWhatComesBackStillCarriesTheOverlay(self):
		"""Because the metaclass puts it there, which is the whole reason not to ask for it."""
		found = aCell().brlMultilineSheet().cellAt(2, 2)
		self.assertTrue(hasattr(found, "brlMultilineSheet"))
		self.assertTrue(hasattr(found, "setFocus"))

	def test_composingAnAlreadyComposedClassHasNoResolutionOrder(self):
		"""The mechanism itself, so that what the fix avoids is written down rather than
		assumed. Nothing in the add-on does this; it is what `type(self.cell)` came to."""
		composed = type(aCell())
		with self.assertRaises(TypeError):
			type("Again", (excelModule.SpreadsheetCell, composed), {})

	def test_soTheOverlayIsNotOfferedToAClassAlreadyMadeOfIt(self):
		"""Insurance for any other path that hands over a composed class: nothing is added,
		the bases stay a single class, and there is nothing to linearise."""
		composed = type(aCell())
		classes = [composed]
		excelModule.AppModule().chooseNVDAObjectOverlayClasses(aCell(), classes)
		self.assertEqual(classes, [composed])


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
