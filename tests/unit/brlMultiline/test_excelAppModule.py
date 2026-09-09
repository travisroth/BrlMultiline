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

from ._stubs import CallCancelled, FORMAT_CONFIG, ReportTableHeaders, installStubs, log

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


class COMError(Exception):
	"""`comtypes.COMError`, which is how Excel's own refusals reach NVDA.

	The real one carries the HRESULT, its text and whatever details came back, and this
	carries the same three. It is a real exception class rather than a stand-in for one
	because the module tells this failure from every other by catching this and nothing
	else: 0x800A01A8, "object required", is what a cell answers once its workbook is gone,
	while a COM call NVDA gave up waiting for arrives as a plain `CallCancelled`.
	"""

	def __init__(self, hresult=-2146827864, text=None, details=None):
		super().__init__(hresult, text, details)
		self.hresult = hresult


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
	def name(self):
		""":return: what NVDA calls a cell, which is what the cell says."""
		return self.excelCellObject.text if self.excelCellObject else ""

	def _get_excelCellInfo(self):
		""":return: what Excel says about this cell, or None where it will not say.

		NVDA's own, as far as this add-on touches it: no helper means no information, and
		otherwise the cell is asked where it is before anything is fetched. Reproduced to
		that depth rather than summarised, because what the overlay has to be right about is
		that the failure comes up out of `super`."""
		if not self.appModule.helperLocalBindingHandle:
			return None
		self.excelCellObject.address(True, True, 1, True)
		return types.SimpleNamespace(text=self.excelCellObject.text)

	@property
	def excelCellInfo(self):
		""":return: the same, reached as NVDA's auto properties reach it.

		NVDA's objects turn `_get_excelCellInfo` into an `excelCellInfo` attribute, and it
		matters that the attribute is what resolves the method: that is how an overlay in
		front of the cell gets to answer at all."""
		return self._get_excelCellInfo()

	@property
	def cellCoordsText(self):
		""":return: where the cell is, as NVDA writes it on the display: "B2"."""
		letters = ""
		column = self.columnNumber or 0
		while column:
			column, remainder = divmod(column - 1, 26)
			letters = chr(ord("A") + remainder) + letters
		return f"{letters}{self.rowNumber}"

	@property
	def rowHeaderText(self):
		""":return: what this cell says its row is called, through the same worksheet and the
		same tracker its column's name comes from. See `columnHeaderText`."""
		return self.parent.rowHeadingFor(self.rowNumber, self.columnNumber)

	@property
	def columnHeaderText(self):
		""":return: what this cell says its column is called, as NVDA's own cell does.

		Resolved through the worksheet, which is where NVDA resolves it: the reader marks a
		header row and every cell of the column answers for it. This is the answer the flow
		uses, and the one an empty header cell tracker made it stop asking for."""
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


class HeaderCellInfo:
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


class HeaderCellTracker:
	"""`tableUtils.HeaderCellTracker`, which is where what a reader marked ends up.

	NVDA's own, as far as this add-on touches it: the entries, their keys in the order NVDA
	walks them, and the one method that adds one. Reproduced rather than summarised, because
	the whole question is what an entry says about which columns — a summary would have
	answered it for the test rather than for the module.
	"""

	def __init__(self):
		self.infosDict = {}
		self.listByRow = []

	def addHeaderCellInfo(self, **kwargs):
		info = HeaderCellInfo(**kwargs)
		key = (info.rowNumber, info.columnNumber)
		self.infosDict[key] = info
		self.listByRow.append(key)
		self.listByRow.sort(reverse=True)


class FakeSelection:
	"""NVDA's `ExcelSelection`: what the focus becomes while a range is selected.

	Shaped the way the app module reads one — a range of its own, a worksheet parent, and
	the coordinates of its top left corner — and nothing more, because nothing more is
	asked of it. Deliberately not a `FakeCell` and not a subclass of one: the whole point of
	the real class is that it is *not* a cell, which is what made the seam vanish the moment
	a reader selected something to chart.
	"""

	def __init__(self, worksheet, row=1, column=1, values=None):
		"""
		:param worksheet: the `FakeWorksheet` this selection is in.
		:param row: the sheet row its top left corner is on.
		:param column: the sheet column it is in.
		:param values: what `Value2` answers for the range.
		"""
		self.excelRangeObject = FakeSelectedRange(worksheet, row, column, values)
		self.excelWindowObject = object()
		self.rowNumber = row
		self.columnNumber = column
		self.parent = worksheet


class FakeSelectedRange:
	"""Excel's own range, as much of it as a selection is asked for."""

	def __init__(self, worksheet, row, column, values):
		self.Row = row
		self.Column = column
		self.Value2 = values
		self.Application = worksheet.excelWorksheetObject.Application


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
	tableUtils = types.ModuleType("tableUtils")
	tableUtils.HeaderCellTracker = HeaderCellTracker
	tableUtils.HeaderCellInfo = HeaderCellInfo
	sys.modules["tableUtils"] = tableUtils
	sys.modules["comtypes"] = types.ModuleType("comtypes")
	sys.modules["comtypes"].BSTR = str
	sys.modules["comtypes"].COMError = COMError
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
	# The class NVDA swaps in while several cells are selected, and the one the module tells
	# a selection by. A different branch of NVDA's hierarchy, which is exactly why the cell
	# overlay does not apply to it and why the seam needed one of its own.
	nvdaExcel.ExcelSelection = FakeSelection
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

	def __init__(self, row, column, text="", gone=False):
		self.row = row
		self.column = column
		self.text = text
		self.selected = False
		self.activated = False
		self.Application = "an Excel"
		"""What an address is turned into the application's own notation through."""

		self.gone = gone
		"""Whether Excel has let go of this cell, which is what a closed workbook leaves
		behind: the object NVDA holds is still there and every question put to it fails."""

	def address(self, rowAbsolute, columnAbsolute, style, external):
		"""Where this cell is, which is the first thing NVDA asks a cell for its info.

		And the question that fails on a cell whose workbook has closed, which is the whole
		of the race this add-on covers. See `CellOutlivingItsWorkbook`."""
		if self.gone:
			raise COMError(-2146827864, None, (None, None, None, 0, None))
		return f"Sheet1!R{self.row}C{self.column}"

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


class FakeVisibleRange:
	"""What `SpecialCells(xlCellTypeVisible)` hands back: a range of one area per run of rows.

	Only its address is asked for, and only the row numbers in that are read — which is the
	whole reason the address is what the module asks Excel for. See `excelModule.rowSpansIn`.
	"""

	def __init__(self, areas, separator=","):
		self.areas = areas
		self.separator = separator

	def address(self, rowAbsolute, columnAbsolute, style, external):
		return self.separator.join(self.areas)


class FakeUsedRange:
	"""What Excel considers written in, which is not the same as what is written in."""

	def __init__(self, row, column, rows, columns, showing=None, refuses=False, areas=None):
		self.row = row
		self.column = column
		self.rows = types.SimpleNamespace(count=rows)
		self.columns = types.SimpleNamespace(count=columns)
		self.showing = showing
		"""Which runs of rows a filter has left on show, or None for an unfiltered sheet."""

		self.refuses = refuses
		"""Whether Excel will not answer at all, which is what it does for a range holding
		nothing — it raises rather than handing back an empty one."""

		self.areas = areas
		"""The addresses Excel answers with, written out, for a sheet hiding columns as well
		as rows. A hidden column splits every area of the answer sideways, which is a shape
		`showing` cannot express."""

		self.timesAskedWhatIsShowing = 0

	def SpecialCells(self, kind):
		self.timesAskedWhatIsShowing += 1
		if self.refuses:
			raise COMError(-2146827284, None, (None, None, None, 0, None))
		if self.areas is not None:
			return FakeVisibleRange(self.areas)
		if self.showing is None:
			# An unfiltered sheet answers with the whole of itself, in one area.
			last = self.row + self.rows.count - 1
			return FakeVisibleRange([f"$A${self.row}:$F${last}"])
		return FakeVisibleRange([f"$A${first}:$F${last}" for first, last in self.showing])


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


class FakeWorksheet:
	"""NVDA's `ExcelWorksheet`, as far as this add-on touches it."""

	def __init__(self, sheet, marked=None, headings=None, poisoned=False, namesFail=False):
		self.excelWorksheetObject = sheet
		self.built = 0
		self.marked = dict(marked or {})
		"""What the marked heading row says, by column number, where a test wants to name it
		rather than put the text in the sheet."""

		self.headings = list(headings or [])
		"""What the reader marked, as the tracker's entries. See `headerCellTracker`."""

		self.poisoned = poisoned
		"""**Whether the tracker NVDA already keeps came out of a walk that failed.**

		The reader's own fault, and the reason the add-on fills a tracker of its own rather
		than reading this one: NVDA builds it by walking every defined name in the workbook
		and keeps whatever the walk produced. An empty one is then indistinguishable from a
		sheet with no headings — and a cell asked instead answers through this same tracker,
		so asking one is asking the same silence twice."""

		self.namesFail = namesFail
		"""Whether walking the workbook's names raises, which is the other half of it: a walk
		that cannot finish must not come back as "this sheet has no headings"."""

		self.timesWalked = 0
		self._tracker = None

	@property
	def headerCellTracker(self):
		"""NVDA's, built lazily and kept — poisoned and all. See `poisoned`."""
		if self._tracker is None:
			self._tracker = HeaderCellTracker()
			if not self.poisoned:
				self.populateHeaderCellTrackerFromNames(self._tracker)
		return self._tracker

	@headerCellTracker.setter
	def headerCellTracker(self, tracker):
		"""Assigned, which is how NVDA's own getter caches it and how the add-on mends one."""
		self._tracker = tracker

	def populateHeaderCellTrackerFromNames(self, tracker):
		"""Fill a tracker from the workbook's defined names, which is where NVDA reads the
		reader's marked headings from. Counted, because it is a walk of the workbook."""
		self.timesWalked += 1
		if self.namesFail:
			raise RuntimeError("the walk over the workbook's names did not finish")
		for heading in self.headings:
			tracker.addHeaderCellInfo(**heading)

	def rowHeadingFor(self, row, column):
		""":return: what a cell's row is called, which is the other branch of
		`fetchAssociatedHeaderCellText`: the first marked header column at or to the left of
		the cell, read as the text at (this row, its column) and joined across its columns.

		A cell inside the header column itself is named by nothing, which is
		`iterPossibleHeaderCellInfosFor`'s own rule — a header does not head itself."""
		tracker = self.headerCellTracker
		for key in tracker.listByRow:
			info = tracker.infosDict[key]
			if not info.isRowHeader or row < info.rowNumber:
				continue
			if info.maxRowNumber and row > info.maxRowNumber:
				continue
			if column < info.columnNumber + info.colSpan:
				return None
			said = " ".join(
				self.excelWorksheetObject.cells(row, at).text
				for at in range(info.columnNumber, info.columnNumber + info.colSpan)
			).strip()
			if said:
				return said
		return None

	def headingFor(self, row, column):
		""":return: what a cell's column is called, the way `fetchAssociatedHeaderCellText`
		works it out: through **the tracker this worksheet is keeping**, taking the first
		entry that covers the column and sits above the cell, read as the text at (its row,
		this column) and joined down its rows.

		The same tracker the add-on reads and mends, which is the point: in NVDA a cell and
		the tracker are not two opinions, and a stand-in where they were let a witness cell
		answer a question the tracker could not."""
		tracker = self.headerCellTracker
		for key in tracker.listByRow:
			info = tracker.infosDict[key]
			if not info.isColumnHeader or column < info.columnNumber:
				continue
			if info.maxColumnNumber and column > info.maxColumnNumber:
				continue
			if row < info.rowNumber + info.rowSpan:
				return None
			if self.marked:
				return self.marked.get(column)
			said = " ".join(
				self.excelWorksheetObject.cells(at, column).text
				for at in range(info.rowNumber, info.rowNumber + info.rowSpan)
			).strip()
			if said:
				return said
		return None


def aCell(
	row=2,
	column=1,
	sheet=None,
	values=None,
	used=None,
	marked=None,
	headings=None,
	poisoned=False,
	namesFail=False,
):
	""":return: a worksheet cell with the add-on's overlay on it, as NVDA would build one.

	Through the metaclass rather than by naming a composed class, because that is the order
	NVDA does it in and the order is what matters. See `DynamicType`.
	"""
	worksheet = FakeWorksheet(
		sheet if sheet is not None else FakeWorksheetObject(values, used),
		marked=marked,
		headings=headings,
		poisoned=poisoned,
		namesFail=namesFail,
	)
	# The cell carries what the sheet says at its coordinate, which is where NVDA's own gets
	# it. Taken from the values rather than through `cells`, so that a test counting what the
	# worksheet was asked counts only what the add-on asked it.
	values = worksheet.excelWorksheetObject.values
	said = ""
	if 1 <= row <= len(values) and 1 <= column <= len(values[row - 1]):
		said = values[row - 1][column - 1]
	cell = FakeCell(
		windowHandle=42,
		excelWindowObject=object(),
		excelCellObject=FakeRange(row, column, said),
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
	failed part way — so an empty one can mean "I know of none just now".

	**And a cell is no second opinion**, which a review had to point out: a cell resolves its
	column's header through its worksheet's tracker, and the cells this hands out are given
	this same worksheet. Asking one was asking the same silence twice.

	So a tracker is filled here instead of read. The walk either finishes — and an empty
	tracker then means what it says — or it raises, and that is a different answer. The
	finished tracker is put on the worksheet, so the cells and this cannot disagree.
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
		self.assertEqual(sheet.obj.timesWalked, 1)

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

	def test_andItCostsNoCellsToSayIt(self):
		"""Not even one. The check is the walk finishing, not a cell's opinion of it."""
		sheet = self._sheet()
		before = len(FakeCell.made)
		sheet.columnHeaders(1, 3)
		self.assertEqual(len(FakeCell.made), before)

	def test_aTrackerLeftEmptyByAFailedWalkIsNotWhatIsRead(self):
		"""**The reader's own fault, in one test.** NVDA already holds an empty tracker for
		this sheet, from a walk that did not finish, and the headings are there to be found.
		Reading the tracker back answers "no headings"; asking a cell answers the same, since
		a cell resolves its column's header through that very tracker. Filling one here finds
		them."""
		sheet = self._sheet(headings=self.ACROSS, poisoned=True)
		batch["Sheet1!R1C1:C3"] = [(1, "Region"), (2, "Q1"), (3, "Q2")]
		self.assertEqual(sheet.obj.headerCellTracker.infosDict, {})
		self.assertEqual(sheet.columnHeaders(1, 3), {1: "Region", 2: "Q1", 3: "Q2"})

	def test_andTheWorksheetIsMendedSoItsCellsAgree(self):
		"""The finished tracker is put where NVDA keeps its own, which is what every cell
		built from this sheet resolves its column's header through."""
		sheet = self._sheet(headings=self.ACROSS, poisoned=True)
		batch["Sheet1!R1C1:C3"] = [(1, "Region"), (2, "Q1"), (3, "Q2")]
		self.assertIsNone(sheet.cellAt(3, 2).columnHeaderText)
		sheet.columnHeaders(1, 3)
		self.assertEqual(sheet.cellAt(3, 2).columnHeaderText, "Q1")

	def test_aWalkThatCannotFinishIsNotAnEmptySheet(self):
		"""The other half of it: an answer of "no headings" is only worth giving after a walk
		that got to the end. This one raises, so the cells are asked as they always were."""
		self.assertIsNone(self._sheet(headings=self.ACROSS, namesFail=True).columnHeaders(1, 3))

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
		sheet = self._sheet(headings=self.ACROSS, marked={2: "Q1"})
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


class TestTheRowsExcelIsShowing(unittest.TestCase):
	"""**A filter hides rows; it does not remove them.**

	Ask a filtered worksheet for row 40 and it hands over row 40's cells whether or not the
	filter left it on show, so a band walking by adding one to a row number reads out of what
	the reader filtered to and into what they filtered away. The reader met that at both ends
	of a filtered block.
	"""

	def _sheet(self, showing=None, refuses=False, rows=20):
		worksheet = FakeWorksheetObject(
			values=[[f"row {number}"] for number in range(1, rows + 1)],
			used=FakeUsedRange(1, 1, rows, 1, showing=showing, refuses=refuses),
		)
		return excelModule.ExcelSheet(aCell(sheet=worksheet)), worksheet

	def test_theNextRowIsTheNextOneShowing(self):
		sheet, _worksheet = self._sheet(showing=[(1, 3), (9, 12)])
		self.assertEqual(sheet.rowAfter(3, 1), 9)

	def test_andGoingBackIsTheSame(self):
		sheet, _worksheet = self._sheet(showing=[(1, 3), (9, 12)])
		self.assertEqual(sheet.rowAfter(9, -1), 3)

	def test_withinARunItIsSimplyTheNextRow(self):
		sheet, _worksheet = self._sheet(showing=[(1, 3), (9, 12)])
		self.assertEqual(sheet.rowAfter(1, 1), 2)
		self.assertEqual(sheet.rowAfter(12, -1), 11)

	def test_andThereIsNoneBeyondTheLastRunEitherWay(self):
		"""Which is what stops the band reading on into the rows the filter took away."""
		sheet, _worksheet = self._sheet(showing=[(1, 3), (9, 12)])
		self.assertIsNone(sheet.rowAfter(12, 1))
		self.assertIsNone(sheet.rowAfter(1, -1))

	def test_anUnfilteredSheetWalksRowByRow(self):
		sheet, _worksheet = self._sheet()
		self.assertEqual(sheet.rowAfter(5, 1), 6)
		self.assertEqual(sheet.rowAfter(5, -1), 4)

	def test_excelIsAskedOnceForAWholeReading(self):
		"""A band walks a row at a time and the answer is the same for all of them. It changes
		when the reader changes the filter, which builds the sheet afresh anyway."""
		sheet, worksheet = self._sheet(showing=[(1, 3), (9, 12)])
		for row in (1, 2, 3, 9, 10):
			sheet.rowAfter(row, 1)
		self.assertEqual(worksheet.used.timesAskedWhatIsShowing, 1)

	def test_aSheetThatWillNotSayIsWalkedRowByRow(self):
		"""None ends the walk, and ending it because a question failed would cost the reader
		the rest of the sheet over something that will very likely answer next time."""
		sheet, _worksheet = self._sheet(refuses=True)
		log.messages.clear()
		self.assertEqual(sheet.rowAfter(5, 1), 6)
		self.assertEqual([level for level, message in log.messages], ["debugWarning"])


class TestWhetherARowIsStillShowing(unittest.TestCase):
	"""**A row already on the band is never walked to again.**

	The walk steps over the rows a filter took away, so no hidden row is ever fetched. A row
	fetched *before* the filter was applied is held in the band's cache and answers every
	question put to it, which is how a filtered-away row stayed on the display above the one
	row the reader had filtered down to.
	"""

	def _sheet(self, showing=None, refuses=False, rows=20):
		worksheet = FakeWorksheetObject(
			values=[[f"row {number}"] for number in range(1, rows + 1)],
			used=FakeUsedRange(1, 1, rows, 1, showing=showing, refuses=refuses),
		)
		return excelModule.ExcelSheet(aCell(sheet=worksheet)), worksheet

	def test_aRowTheFilterTookAwayIsNotShowing(self):
		sheet, _worksheet = self._sheet(showing=[(1, 1), (9, 9)])
		self.assertIs(sheet.rowShowing(5), False)

	def test_aRowTheFilterLeftIsShowing(self):
		sheet, _worksheet = self._sheet(showing=[(1, 1), (9, 9)])
		self.assertIs(sheet.rowShowing(9), True)
		self.assertIs(sheet.rowShowing(1), True)

	def test_everyRowOfAnUnfilteredSheetIsShowing(self):
		sheet, _worksheet = self._sheet()
		self.assertIs(sheet.rowShowing(5), True)

	def test_aSheetThatWillNotSayAnswersNothing(self):
		"""None is "I cannot say", which is read as showing: a row nothing objects to stays."""
		sheet, _worksheet = self._sheet(refuses=True)
		self.assertIsNone(sheet.rowShowing(5))

	def test_aRowPastWhatExcelCallsUsedIsNotCalledHidden(self):
		"""It is empty, not hidden. A sheet is read from A1 to at least as far as the reader
		stands, and a reader arrowing about a blank sheet stands well past the used range —
		so called hidden, those rows would have the reading built again on every arrow key."""
		sheet, _worksheet = self._sheet(showing=[(1, 12)], rows=12)
		self.assertIsNone(sheet.rowShowing(40))

	def test_aBandfulOfRowsCostsWhatOneCosts(self):
		"""The same answer the walk uses, asked once for the reading."""
		sheet, worksheet = self._sheet(showing=[(1, 1), (9, 9)])
		for row in range(1, 12):
			sheet.rowShowing(row)
		self.assertEqual(worksheet.used.timesAskedWhatIsShowing, 1)


class TestTheColumnsExcelIsShowing(unittest.TestCase):
	"""**A hidden column is still a column.**

	Ask a worksheet for column 5 with column 5 hidden and it hands over column 5's cells like
	any other, so the layout drew a column the reader could not arrow to, counted it among the
	columns, and put its heading in the pinned row.
	"""

	def _sheet(self, areas=None, refuses=False):
		worksheet = FakeWorksheetObject(
			values=[[f"row {number}"] for number in range(1, 21)],
			used=FakeUsedRange(1, 1, 20, 6, areas=areas, refuses=refuses),
		)
		return excelModule.ExcelSheet(aCell(sheet=worksheet))

	def test_aHiddenColumnIsNotAmongThem(self):
		"""Excel splits its answer sideways at the hidden column, which is what says so."""
		sheet = self._sheet(["$A$1:$D$20", "$F$1:$F$20"])
		self.assertEqual(sheet.columnsShowing(), {1, 2, 3, 4, 6})

	def test_aSheetHidingNothingSaysSoBySayingAllOfThem(self):
		sheet = self._sheet()
		self.assertEqual(sheet.columnsShowing(), {1, 2, 3, 4, 5, 6})

	def test_aSheetThatWillNotSayAnswersNothing(self):
		"""None is every column, which is the answer that changes nothing."""
		self.assertIsNone(self._sheet(refuses=True).columnsShowing())

	def test_theSameAnswerServesBothAxes(self):
		"""One call, and the rows and the columns both come out of it: a filter and a hidden
		column split the same address, one way each."""
		sheet = self._sheet(["$A$1:$D$3", "$F$1:$F$3", "$A$9:$D$20", "$F$9:$F$20"])
		self.assertEqual(sheet.columnsShowing(), {1, 2, 3, 4, 6})
		self.assertEqual(sheet.rowAfter(3, 1), 9)


class TestReadingAnAddressForItsColumns(unittest.TestCase):
	"""The letters of the same address the rows are read out of."""

	def test_theAreasAreRunsOfColumns(self):
		self.assertEqual(excelModule.columnSpansIn("$A$1:$D$129,$F$1:$F$129"), [(1, 4), (6, 6)])

	def test_aSingleCellIsARunOfOne(self):
		self.assertEqual(excelModule.columnSpansIn("$B$3"), [(2, 2)])

	def test_lettersPastZKeepCounting(self):
		self.assertEqual(excelModule.columnSpansIn("$AA$1:$AB$2"), [(27, 28)])

	def test_aSemicolonSeparatesAreasHereToo(self):
		self.assertEqual(excelModule.columnSpansIn("$A$1:$D$3;$F$1:$F$3"), [(1, 4), (6, 6)])

	def test_anAreaNamingNoColumnMeansEveryColumn(self):
		"""A reference to whole rows names no column, and every column is showing in it. An
		answer built from the other areas alone would hide columns the sheet is perfectly
		willing to show."""
		self.assertIsNone(excelModule.columnSpansIn("$A$1:$D$3,$9:$20"))

	def test_andNothingIsNoAnswerAtAll(self):
		self.assertIsNone(excelModule.columnSpansIn(""))
		self.assertIsNone(excelModule.columnSpansIn(None))


class TestReadingAnAddressForItsRows(unittest.TestCase):
	"""What Excel says when it is asked which cells are showing, and what is taken from it.

	Parsed from the address rather than walked as `Areas`, because the address is one call
	and the walk is two per area: a filter leaving fifty scattered rows would cost a hundred
	calls to learn what one string already said.
	"""

	def test_severalAreasAreSeveralRunsOfRows(self):
		self.assertEqual(excelModule.rowSpansIn("$A$1:$F$3,$A$9:$F$20"), [(1, 3), (9, 20)])

	def test_aSingleCellIsARunOfOne(self):
		self.assertEqual(excelModule.rowSpansIn("$A$9"), [(9, 9)])

	def test_wholeRowsAreReadTheSameWay(self):
		"""Excel writes a range of entire rows as "$9:$20", with no column in it at all."""
		self.assertEqual(excelModule.rowSpansIn("$9:$20"), [(9, 20)])

	def test_aSemicolonSeparatesAreasWhereThatIsTheListSeparator(self):
		"""Excel writes an address in the reader's own conventions, and in much of Europe the
		list separator is a semicolon."""
		self.assertEqual(excelModule.rowSpansIn("$A$1:$F$3;$A$9:$F$20"), [(1, 3), (9, 20)])

	def test_theRunsComeBackInOrder(self):
		self.assertEqual(excelModule.rowSpansIn("$A$9:$F$20,$A$1:$F$3"), [(1, 3), (9, 20)])

	def test_andNothingIsNoAnswerAtAll(self):
		"""Told apart from an empty list on purpose: "no rows" would end every walk, and what
		this means is that the walk should carry on as it did before."""
		self.assertIsNone(excelModule.rowSpansIn(""))
		self.assertIsNone(excelModule.rowSpansIn(None))
		self.assertIsNone(excelModule.rowSpansIn("nothing with a number in it"))


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

	def test_theOverlaysAreAddedToAWorksheetCell(self):
		module = excelModule.AppModule()
		classes = []
		module.chooseNVDAObjectOverlayClasses(aCell(), classes)
		self.assertEqual(
			classes,
			[
				excelModule.CellOutlivingItsWorkbook,
				excelModule.SpreadsheetCell,
				excelModule.HeadersInBraille,
			],
		)

	def test_andACellThatCannotBeReadByCoordinateStillGetsItsHeaderInBraille(self):
		"""The two overlays are two claims, and only one of them is about laying a sheet out.
		What a cell's column is called belongs to NVDA's own line of braille."""
		module = excelModule.AppModule()
		cell = aCell()
		del cell.excelCellObject
		classes = []
		module.chooseNVDAObjectOverlayClasses(cell, classes)
		self.assertEqual(
			classes,
			[excelModule.CellOutlivingItsWorkbook, excelModule.HeadersInBraille],
		)

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


class TestACellWhoseWorkbookHasClosed(unittest.TestCase):
	"""**A race of NVDA's own, covered here because it is logged under this add-on's name.**

	Close a workbook with control+W and NVDA raises a typed character event for that same
	keystroke on the cell that had the focus. Deciding whether to echo the character asks that
	cell whether typing is protected, which asks for its states, which asks Excel for the
	cell's address — and the cell's workbook has just gone, so Excel answers "object required"
	and NVDA logs a traceback made entirely of its own frames.

	Entirely its own, and yet the object named at the top of it is
	`Dynamic_SpreadsheetCellHeadersInBrailleExcelCell`, because NVDA names a composed class
	after the overlays in it. A reader reading their log sees this add-on over a crash it did
	not cause. Answering None instead is what these tests hold in place.
	"""

	def setUp(self):
		log.messages.clear()

	def _cell(self, gone=False):
		""":return: a worksheet cell, with or without a workbook still behind it."""
		return FakeCell(
			windowHandle=42,
			excelWindowObject=object(),
			excelCellObject=FakeRange(2, 2, "1200", gone=gone),
		)

	def test_aCellExcelStillHasIsDescribedByNvdasOwn(self):
		"""Which is the half that must not be lost: this stands in front of NVDA's method and
		hands back what it answers, unread and unchanged."""
		self.assertEqual(self._cell().excelCellInfo.text, "1200")

	def test_aCellWhoseWorkbookHasGoneAnswersNothing(self):
		"""Rather than raising. None is not an invention: it is what NVDA's own method answers
		when there is nothing to fetch, and every caller of it in NVDA is written for it."""
		self.assertIsNone(self._cell(gone=True).excelCellInfo)

	def test_andNvdaIsNotToldAnythingWentWrong(self):
		"""A debug line and not an error, because nothing did go wrong: a cell of a closed
		workbook has no information to give and saying so is the truthful answer. An error
		here would be the very log line this exists to stop."""
		self._cell(gone=True).excelCellInfo
		self.assertEqual([level for level, message in log.messages], ["debugWarning"])

	def test_butACallNvdaGaveUpWaitingForStillGoesUp(self):
		"""**Not the same thing as a dead cell, and it must not be answered as one.** NVDA
		raises `CallCancelled` when it stops waiting on Excel; the cell is still there and the
		reader is still in it. Answering None would describe a cell nobody has left. It is a
		plain exception rather than a `COMError`, which is why catching that and nothing else
		is what lets it past. See `sheetFor`."""
		cell = self._cell()

		def gaveUp(*args):
			raise CallCancelled("COM call cancelled")

		cell.excelCellObject.address = gaveUp
		with self.assertRaises(CallCancelled):
			cell.excelCellInfo


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

	def test_butACancelledReadIsNotASheetThatIsNotThere(self):
		"""**The reviewer's own repro.** Recognising the sheet is the first read of the whole
		operation, and a cancelled one used to come back as None — which the flow reports as
		"you are not in a table", to a reader looking straight at one. It goes up instead, and
		the command says the table could not be read."""

		class Cancelling:
			@property
			def row(self):
				raise excelModule.CallCancelled("NVDA stopped waiting")

		cell = aCell()
		cell.parent.excelWorksheetObject.used = Cancelling()
		with self.assertRaises(excelModule.CallCancelled):
			cell.brlMultilineSheet()

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


class TestTheHeaderInNvdasOwnBraille(unittest.TestCase):
	"""**Speech says it and braille did not.**

	Land on a cell in a sheet whose header row the reader has marked and NVDA speaks
	"9/4/2026  A2  Date". The same cell in braille was "9/4/2026  A2", and the only way to
	find out what the column was called was to leave the cell and come back — so the reader
	who cannot hear the header cannot have it at all.

	It is not that braille does not know how. `getPropertiesBraille` has a `columnHeaderText`
	and puts it exactly where speech does; it is only ever given one beside a `columnNumber`,
	and the region that draws an object sends neither. Nothing decided this.
	"""

	ACROSS = [{"rowNumber": 1, "columnNumber": 1, "maxColumnNumber": 3}]
	"""A header row marked across the sheet, which is what NVDA+shift+c leaves behind."""

	DOWN = [
		{
			"rowNumber": 2,
			"columnNumber": 1,
			"maxRowNumber": 3,
			"isColumnHeader": False,
			"isRowHeader": True,
		},
	]
	"""And a header column marked down it, from A2, which is what NVDA+shift+r leaves behind.

	From A2 and not from A1 because that is where a reader marks it — row one is already the
	header row — and because NVDA keys a tracker entry by (row, column): a header row and a
	header column both anchored on the same cell are one entry, not two.
	"""

	def setUp(self):
		settings = dict(FORMAT_CONFIG)
		self.addCleanup(FORMAT_CONFIG.update, settings)

	def _region(self, review=False, **kwargs):
		""":return: the region NVDA would draw a cell from, updated as `getFocusRegions`
		updates whatever an object hands it."""
		cell = kwargs.pop("cell", None) or aCell(**kwargs)
		regions = list(cell.getBrailleRegions(review=review))
		self.assertEqual(len(regions), 1, "a cell is drawn from one region")
		regions[0].update()
		return regions[0]

	def test_theColumnHeaderIsShownAfterTheCoordinates(self):
		"""Where speech says it, and after everything else the line already carried."""
		self.assertEqual(self._region(row=2, column=2, headings=self.ACROSS).rawText, "1200 B2 Q1")

	def test_aSheetNobodyHasMarkedIsDrawnExactlyAsNvdaDrewIt(self):
		self.assertEqual(self._region(row=2, column=2).rawText, "1200 B2")

	def test_norIsAHeaderCellNamedByItself(self):
		"""`iterPossibleHeaderCellInfosFor`'s own rule: a header does not head itself."""
		self.assertEqual(self._region(row=1, column=2, headings=self.ACROSS).rawText, "Q1 B1")

	def test_theReadersOwnSettingTurnsItOff(self):
		"""The same setting speech obeys. A reader who turned table headers off is not given
		them here either — it is one decision, not two."""
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.OFF.value
		self.assertEqual(self._region(row=2, column=2, headings=self.ACROSS).rawText, "1200 B2")

	def test_andRowsOnlyMeansTheColumnsHeaderIsNotShown(self):
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.ROWS.value
		region = self._region(row=2, column=2, headings=self.ACROSS + self.DOWN)
		self.assertEqual(region.rawText, "1200 B2 North")

	def test_andColumnsOnlyMeansTheRowsIsNot(self):
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.COLUMNS.value
		region = self._region(row=2, column=2, headings=self.ACROSS + self.DOWN)
		self.assertEqual(region.rawText, "1200 B2 Q1")

	def test_andBothComeInSpeechsOrder(self):
		"""The row's header, then the column's. See `speech.getPropertiesSpeech`."""
		region = self._region(row=2, column=2, headings=self.ACROSS + self.DOWN)
		self.assertEqual(region.rawText, "1200 B2 North Q1")

	def test_theCoordinatesCanBeTurnedOffAndTheHeaderStillArrives(self):
		"""Two settings, and the header is not smuggled in beside the coordinates."""
		FORMAT_CONFIG["reportTableCellCoords"] = False
		self.assertEqual(self._region(row=2, column=2, headings=self.ACROSS).rawText, "1200 Q1")

	def test_drawingTheSameRegionAgainDoesNotShowTheHeaderTwice(self):
		"""A region is updated again whenever the cell changes underneath it, and the header
		is appended to what the base concatenates *before* translating — so it has to be put
		back afterwards or it accumulates."""
		region = self._region(row=2, column=2, headings=self.ACROSS)
		region.update()
		region.update()
		self.assertEqual(region.rawText, "1200 B2 Q1")

	def test_andWhatFollowsTheRegionStaysAtTheEnd(self):
		"""NVDA appends a separator to a region that precedes another one. The header goes
		before it, not after: it belongs to this cell."""
		region = excelModule.CellRegion(aCell(row=2, column=2, headings=self.ACROSS), appendText="!")
		region.update()
		self.assertEqual(region.rawText, "1200 B2 Q1!")

	def test_aHeaderThatCannotBeReadCostsTheHeaderAndNotTheCell(self):
		"""Braille is drawn from here and there is nowhere above to hand a failure to. A walk
		of the workbook's names that did not finish must not take the line down with it."""
		region = self._region(row=2, column=2, headings=self.ACROSS, namesFail=True)
		self.assertEqual(region.rawText, "1200 B2")

	def test_followingTheReviewCursorGivesTheRegionThatFocusesOnRouting(self):
		"""NVDA's review region differs in what a routing key does, and that difference is
		not this add-on's to drop."""
		region = self._region(row=2, column=2, headings=self.ACROSS, review=True)
		self.assertIsInstance(region, excelModule.ReviewCellRegion)
		self.assertEqual(region.rawText, "1200 B2 Q1")

	def test_andFollowingTheFocusGivesTheOrdinaryOne(self):
		region = self._region(row=2, column=2, headings=self.ACROSS)
		self.assertNotIsInstance(region, excelModule.ReviewCellRegion)
		self.assertIsInstance(region, excelModule.CellRegion)


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


class TestASelectedRangeOffersTheSeam(unittest.TestCase):
	"""Selecting cells must not take the seam away.

	NVDA builds an `ExcelSelection` rather than an `ExcelCell` for as long as more than one
	cell is selected — a different class on a different branch, so the cell overlay does not
	apply to it. The seam therefore vanished at exactly the moment a reader had selected
	something to do with, and charting a selection failed on hardware with "charts need a
	spreadsheet cell" *while the reader was in a spreadsheet with cells selected*.

	The log line that found it is worth keeping in view:

		BrlMultiline: charting from ExcelSelection role=TABLECELL
		name='A1  June through B4  5000', grid=False
	"""

	def setUp(self):
		self.sheet = FakeWorksheet(FakeWorksheetObject())

	def test_aSelectionIsRecognised(self):
		self.assertTrue(excelModule.isASelectedRange(FakeSelection(self.sheet)))

	def test_aPlainCellIsNotASelection(self):
		"""The two predicates must not both claim the same object, or the same overlay would
		be offered twice and NVDA would be left with no object at all."""
		cell = FakeCell(excelCellObject=self.sheet.excelWorksheetObject.cells(1, 1))
		self.assertFalse(excelModule.isASelectedRange(cell))

	def test_aSelectionIsNotOfferedTheCellOverlay(self):
		"""`setFocus` on a selection is not a thing — a routing key lands on a cell — so the
		cell overlay must keep away from it."""
		self.assertFalse(excelModule.readsByCoordinate(FakeSelection(self.sheet)))

	def test_theSelectionOverlayIsRegistered(self):
		pairs = {overlay: belongs for overlay, belongs in excelModule.OVERLAYS}
		self.assertIn(excelModule.SpreadsheetSelection, pairs)
		self.assertIs(pairs[excelModule.SpreadsheetSelection], excelModule.isASelectedRange)

	def test_theOverlayOffersTheSeamAndNotNavigation(self):
		self.assertTrue(hasattr(excelModule.SpreadsheetSelection, "brlMultilineSheet"))
		self.assertFalse(hasattr(excelModule.SpreadsheetSelection, "setFocus"))

	def test_aSheetBuiltFromASelectionReachesExcel(self):
		"""The one real difference between the two objects: a cell carries `excelCellObject`
		and a selection carries `excelRangeObject`, and everything else on `ExcelSheet` already
		worked from either."""
		sheet = excelModule.ExcelSheet(FakeSelection(self.sheet, row=2, column=3))
		self.assertEqual(sheet.where(), (2, 3))
		self.assertIsNotNone(sheet._application())

	def test_aSelectionsOwnRangeIsPreferredToAskingExcelWhatIsSelected(self):
		"""They are the same range when they agree, and when they do not it is because the
		selection moved after NVDA built the object — in which case the object the reader is on
		is the one they meant."""
		selection = FakeSelection(self.sheet, values=((1.0,), (2.0,)))
		sheet = excelModule.ExcelSheet(selection)
		self.assertIs(sheet._selectedRange(), selection.excelRangeObject)

	def test_theValuesComeBackAsRowsOfTextAndNumber(self):
		selection = FakeSelection(self.sheet, values=((5.0,), (10.0,)))
		rows = excelModule.ExcelSheet(selection).selectedValues()
		self.assertEqual(len(rows), 2)
		self.assertEqual([cell[1] for row in rows for cell in row], [5.0, 10.0])
		for row in rows:
			for text, _value in row:
				self.assertTrue(text)

	def test_aSingleCellSelectionIsStillAGrid(self):
		"""`Value2` answers a bare value rather than a tuple for one cell."""
		rows = excelModule.ExcelSheet(FakeSelection(self.sheet, values=42.0)).selectedValues()
		self.assertEqual([cell[1] for row in rows for cell in row], [42.0])


if __name__ == "__main__":
	unittest.main()
