# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for reading a table's row on NVDA's one line, its cells divided by bars.

What is held to: the row the caret is in is the whole line, every column keeps its place
between the bars, the cursor is on the cell the caret is in, and routing and panning move the
caret by cell and by row. And the half that matters more — **everywhere else the region is
NVDA's own**, so a page read with a request standing reads exactly as it did before.
"""

import unittest

from ._stubs import (
	CursorManagerRegion,
	FakeGrid,
	FakeNavigatorObject,
	FakeTableDocument,
	FakeTextInfo,
	NVDAObjectRegion,
	TextInfoRegion,
	installStubs,
)

installStubs()

from brlMultiline import flowObjectTable, patches, tableRowLine  # noqa: E402
from brlMultiline.flowTableSource import tableAt  # noqa: E402

GRID = [
	["Symbol", "Last", "Change"],
	["AAPL", "310.34", "+1.2"],
	["MSFT", None, "-0.4"],
	["IBM", "201.10", "+0.1"],
]


class CollapsedTextInfo(FakeTextInfo):
	"""A cursor with nothing selected, which is what the row line asks about first."""

	isCollapsed = True


class Page(FakeTableDocument):
	"""A browse mode page with the table in it, whose cursor has nothing selected."""

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.positionType = CollapsedTextInfo
		self.activated = 0

	@property
	def selection(self):
		info = FakeTableDocument.selection.fget(self)
		page = self

		def activate():
			page.activated += 1

		info.activate = activate
		return info

	@selection.setter
	def selection(self, info):
		FakeTableDocument.selection.fset(self, info)


def caretCells(page):
	""":return: the cells the caret has been put in, as (row, column), in order."""
	return [where for where, holder in page.carets if holder["caret"]]


class RowLineTestCase(unittest.TestCase):
	def setUp(self):
		tableRowLine.clear()
		self.addCleanup(tableRowLine.clear)

	def page(self, row=2, col=2, **kwargs):
		return Page(GRID, row=row, col=col, **kwargs)

	def ask(self, page, columns=()):
		tableRowLine.request(tableAt(page), columns)

	def region(self, page):
		region = CursorManagerRegion(page)
		self.assertTrue(tableRowLine.adopt(region))
		region.update()
		return region


class TestAdopting(RowLineTestCase):
	def test_nothingIsAdoptedWhileNothingIsAsked(self):
		self.assertFalse(tableRowLine.adopt(CursorManagerRegion(self.page())))

	def test_onlyNvdasOwnClassIsAdopted(self):
		"""A flow's region derives from NVDA's and has a reading of its own to keep."""
		page = self.page()
		self.ask(page)

		class FlowLike(CursorManagerRegion):
			pass

		self.assertFalse(tableRowLine.adopt(FlowLike(page)))
		self.assertTrue(tableRowLine.adopt(CursorManagerRegion(page)))

	def test_thePatchedUpdateAdoptsAndReadsTheRow(self):
		page = self.page()
		self.ask(page)
		region = CursorManagerRegion(page)
		original = patches._originals.get("textInfoUpdate")
		patches._originals["textInfoUpdate"] = CursorManagerRegion.__mro__[1].update
		self.addCleanup(
			lambda: patches._originals.pop("textInfoUpdate")
			if original is None
			else patches._originals.__setitem__("textInfoUpdate", original),
		)
		patches._textInfoUpdateForgettingProvenance(region)
		self.assertIsInstance(region, tableRowLine.TableRowLineRegion)
		self.assertEqual(region.rawText, "AAPL | 310.34 | +1.2")


class TestReadingTheRow(RowLineTestCase):
	def test_theRowIsOneLineWithBarsBetweenTheCells(self):
		page = self.page()
		self.ask(page)
		region = self.region(page)
		self.assertEqual(region.rawText, "AAPL | 310.34 | +1.2")
		self.assertEqual(len(region.brailleCells), len(region.rawText))
		self.assertEqual(len(region.rawToBraillePos), len(region.rawText))
		self.assertEqual(len(region.brailleToRawPos), len(region.brailleCells))

	def test_theCursorIsAtTheStartOfTheCaretsCell(self):
		page = self.page(col=3)
		self.ask(page)
		region = self.region(page)
		self.assertEqual(region.brailleCursorPos, region.rawText.index("+1.2"))
		self.assertTrue(region.hidePreviousRegions)

	def test_aMissingCellKeepsItsPlaceBetweenTheBars(self):
		page = self.page(row=3, col=1)
		self.ask(page)
		self.assertEqual(self.region(page).rawText, "MSFT |  | -0.4")

	def test_aSavedLayoutsColumnsAndOrderAreKept(self):
		page = self.page(col=3)
		self.ask(page, columns=(3, 1))
		region = self.region(page)
		self.assertEqual(region.rawText, "+1.2 | AAPL")
		self.assertEqual(region.brailleCursorPos, 0)

	def test_aColumnTheLayoutLeftOutHasNoCursor(self):
		page = self.page(col=2)
		self.ask(page, columns=(1, 3))
		self.assertIsNone(self.region(page).brailleCursorPos)

	def test_outsideTheTableTheLineIsNvdas(self):
		page = self.page()
		self.ask(page)
		page.inTable = False
		page.lines = ["a heading", "some prose"]
		region = self.region(page)
		self.assertEqual(region.rawText, "a heading")

	def test_anotherTableIsNotTheOneAskedFor(self):
		page = self.page()
		self.ask(page)
		page.tableID = 2
		page.lines = ["a heading"]
		self.assertEqual(self.region(page).rawText, "a heading")

	def test_takingTheRequestBackGivesNvdasLineBack(self):
		page = self.page()
		self.ask(page)
		region = self.region(page)
		tableRowLine.clear()
		page.lines = ["a heading"]
		region.update()
		self.assertEqual(region.rawText, "a heading")


class TestRouting(RowLineTestCase):
	def test_routingToAnotherCellPutsTheCaretThere(self):
		page = self.page(col=1)
		self.ask(page)
		region = self.region(page)
		region.routeTo(region.rawText.index("+1.2") + 1)
		self.assertEqual(caretCells(page), [(2, 3)])

	def test_theSpaceAfterABarBelongsToTheNextCell(self):
		page = self.page(col=1)
		self.ask(page)
		region = self.region(page)
		region.routeTo(region.rawText.index("| 310") + 1)
		self.assertEqual(caretCells(page), [(2, 2)])

	def test_routingOntoTheCaretsCellActivatesIt(self):
		page = self.page(col=2)
		self.ask(page)
		region = self.region(page)
		region.routeTo(region.brailleCursorPos)
		self.assertEqual(page.activated, 1)
		self.assertEqual(caretCells(page), [])

	def test_anEmptyCellCannotBeRoutedTo(self):
		page = self.page(row=3, col=1)
		self.ask(page)
		region = self.region(page)
		region.routeTo(region.rawText.index("|  |") + 2)
		self.assertEqual(caretCells(page), [])


class TestPanningPastTheRow(RowLineTestCase):
	def test_pastTheEndIsTheNextRowsFirstCell(self):
		page = self.page(row=2, col=3)
		self.ask(page)
		self.region(page).nextLine()
		self.assertEqual(caretCells(page), [(3, 1)])

	def test_pastTheStartIsTheRowBeforesLastCell(self):
		page = self.page(row=3, col=1)
		self.ask(page)
		self.region(page).previousLine()
		self.assertEqual(caretCells(page), [(2, 3)])

	def test_nvdasStartOfLineAsksForTheFirstCell(self):
		page = self.page(row=3, col=1)
		self.ask(page)
		self.region(page).previousLine(start=True)
		self.assertEqual(caretCells(page), [(2, 1)])

	def test_aRowWithNoneOfTheColumnsIsPassedOver(self):
		page = Page([*GRID[:2], [None, None, None], GRID[3]], row=2, col=1)
		self.ask(page)
		self.region(page).nextLine()
		self.assertEqual(caretCells(page), [(4, 1)])

	def test_theLastRowHandsTheKeyToNvda(self):
		"""Nothing is trapped: panning on from the last row walks out of the table."""
		page = self.page(row=4, col=1)
		self.ask(page)
		region = self.region(page)
		fromWhere = []
		original = CursorManagerRegion._moveLine
		CursorManagerRegion._moveLine = lambda self, count: fromWhere.append((self._readingInfo.text, count))
		self.addCleanup(setattr, CursorManagerRegion, "_moveLine", original)
		region.nextLine()
		self.assertEqual(region.panned, "next")
		# From the last cell of the row, which is where the page resumes.
		self.assertEqual(fromWhere, [("+0.1", 1)])
		self.assertEqual(caretCells(page), [])


FILES = [
	["report.docx", "09:14", "Word document"],
	["notes.md", "11:02", "Markdown"],
	["photo.jpg", "12:30", "JPEG image"],
]


class FakeBrailleHandler:
	"""The handler a redraw is asked of, holding the regions NVDA last built."""

	def __init__(self, *regions):
		self.mainBuffer = type("Buffer", (), {})()
		self.mainBuffer.regions = list(regions)
		self.gainedFocus = []

	def handleGainFocus(self, obj, shouldAutoTether=True):
		self.gainedFocus.append(obj)


class ObjectRowLineTestCase(unittest.TestCase):
	"""A list whose cells are objects, which is File Explorer's file list and Outlook's."""

	def setUp(self):
		import api
		import braille

		tableRowLine.clear()
		self.addCleanup(tableRowLine.clear)
		self.grid = FakeGrid(FILES, headers=["Name", "Date modified", "Type"], columnCount=3)
		for row in self.grid.items:
			row.actions = 0
			row.doAction = lambda row=row: setattr(row, "actions", row.actions + 1)
		self.focus = self.grid.item(1)
		self.navigator = None
		for name, answer in (
			("getFocusObject", lambda: self.focus),
			("getNavigatorObject", lambda: self.navigator or self.focus),
		):
			self.addCleanup(setattr, api, name, getattr(api, name))
			setattr(api, name, answer)
		self.handler = FakeBrailleHandler()
		self.addCleanup(setattr, braille, "handler", braille.handler)
		braille.handler = self.handler

	def ask(self, columns=()):
		tableRowLine.request(tableAt(self.focus), columns)

	def region(self, obj=None, cls=NVDAObjectRegion):
		region = cls(obj or self.focus)
		self.assertTrue(tableRowLine.adoptObject(region))
		region.update()
		self.handler.mainBuffer.regions = [region]
		return region

	def focusedCells(self):
		return [
			(row.positionInfo["indexInGroup"], cell.columnNumber)
			for row in self.grid.items
			for cell in row.cellObjects
			if cell.focused
		]


class TestAdoptingAListsRegion(ObjectRowLineTestCase):
	def test_nothingIsAdoptedWhileNothingIsAsked(self):
		self.assertFalse(tableRowLine.adoptObject(NVDAObjectRegion(self.focus)))

	def test_aBrowseModeRequestLeavesObjectRegionsAlone(self):
		tableRowLine.request(tableAt(Page(GRID, row=2, col=2)))
		self.assertFalse(tableRowLine.adoptObject(NVDAObjectRegion(self.focus)))

	def test_aListRequestLeavesBrowseModeRegionsAlone(self):
		self.ask()
		self.assertFalse(tableRowLine.adopt(CursorManagerRegion(Page(GRID))))

	def test_onlyTheFocusesRegionIsAdopted(self):
		"""The list above it is one of the regions NVDA puts before the focus."""
		self.ask()
		self.assertFalse(tableRowLine.adoptObject(NVDAObjectRegion(self.grid)))

	def test_onlyNvdasOwnClassIsAdopted(self):
		self.ask()

		class FlowLike(NVDAObjectRegion):
			pass

		self.assertFalse(tableRowLine.adoptObject(FlowLike(self.focus)))

	def test_aFocusWithTextHasTheRowOnItsTextRegion(self):
		"""NVDA follows the object region with a text region, which is the last one."""
		self.ask()
		self.assertFalse(tableRowLine.adoptObject(NVDAObjectRegion(self.focus, appendText=" ")))
		region = TextInfoRegion(self.focus)
		self.assertTrue(tableRowLine.adoptObject(region))
		region.update()
		self.assertIsInstance(region, tableRowLine.ObjectRowLineTextRegion)
		self.assertEqual(region.rawText, "report.docx | 09:14 | Word document")
		self.assertTrue(region.hidePreviousRegions)

	def test_thePatchedUpdateAdoptsAndReadsTheRow(self):
		self.ask()
		region = NVDAObjectRegion(self.focus)
		original = patches._originals.get("objectRegionUpdate")
		patches._originals["objectRegionUpdate"] = NVDAObjectRegion.update
		self.addCleanup(
			lambda: patches._originals.pop("objectRegionUpdate")
			if original is None
			else patches._originals.__setitem__("objectRegionUpdate", original),
		)
		patches._objectRegionUpdateAdoptingRows(region)
		self.assertIsInstance(region, tableRowLine.ObjectRowLineRegion)
		self.assertEqual(region.rawText, "report.docx | 09:14 | Word document")


class TestReadingAListsRow(ObjectRowLineTestCase):
	def test_theRowIsOneLineWithBarsBetweenTheCells(self):
		self.ask()
		region = self.region()
		self.assertEqual(region.rawText, "report.docx | 09:14 | Word document")
		self.assertEqual(region.brailleCursorPos, 0)
		self.assertTrue(region.hidePreviousRegions)

	def test_theCursorIsOnTheCellTheNavigatorIsOn(self):
		"""Where NVDA's table commands leave it in Outlook, the row keeping the focus."""
		self.ask()
		self.navigator = self.focus.cellObjects[2]
		region = self.region()
		self.assertEqual(region.brailleCursorPos, region.rawText.index("Word"))

	def test_aFocusedCellIsWhereTheCursorIs(self):
		"""File Explorer, where the focus is one property of the file."""
		self.focus = self.grid.item(1).cellObjects[1]
		self.ask()
		region = self.region()
		self.assertEqual(region.rawText, "report.docx | 09:14 | Word document")
		self.assertEqual(region.brailleCursorPos, region.rawText.index("09:14"))

	def test_theNextRowKeepsTheColumn(self):
		"""An arrow key focuses a row and says nothing about a column."""
		self.ask()
		self.navigator = self.focus.cellObjects[2]
		self.region()
		self.focus, self.navigator = self.grid.item(2), None
		region = self.region()
		self.assertEqual(region.rawText, "notes.md | 11:02 | Markdown")
		self.assertEqual(region.brailleCursorPos, region.rawText.index("Markdown"))

	def test_aNavigatorInAnotherRowIsNotTheColumn(self):
		self.ask()
		self.navigator = self.grid.item(3).cellObjects[2]
		self.assertEqual(self.region().brailleCursorPos, 0)

	def test_aSavedLayoutsColumnsAndOrderAreKept(self):
		self.ask(columns=(3, 1))
		self.assertEqual(self.region().rawText, "Word document | report.docx")

	def test_anotherListIsNotTheOneAskedFor(self):
		self.ask()
		other = FakeGrid(FILES, columnCount=3)
		self.focus = other.item(1)
		region = self.region()
		self.assertFalse(region.isReadingARow)
		self.assertNotIn(" | ", region.rawText)

	def test_takingTheRequestBackGivesNvdasReadingBack(self):
		self.ask()
		region = self.region()
		tableRowLine.clear()
		region.update()
		self.assertFalse(region.isReadingARow)
		self.assertNotIn(" | ", region.rawText)


class TestRoutingInAList(ObjectRowLineTestCase):
	def test_routingToAnotherCellGoesThereAndRedraws(self):
		self.ask()
		region = self.region()
		region.routeTo(region.rawText.index("Word") + 1)
		self.assertEqual(self.focusedCells(), [(1, 3)])
		self.assertEqual(self.handler.gainedFocus, [self.focus])
		# The row kept the focus and NVDA's navigator stayed on it: the column is remembered.
		self.assertEqual(self.region().brailleCursorPos, region.rawText.index("Word"))

	def test_routingOntoTheCursorsCellActivatesTheFocus(self):
		self.ask()
		region = self.region()
		region.routeTo(0)
		self.assertEqual(self.focus.actions, 1)
		self.assertEqual(self.focusedCells(), [])


class TestPanningPastAListsRow(ObjectRowLineTestCase):
	def test_pastTheEndIsTheNextRowsFirstCell(self):
		self.ask()
		self.region().nextLine()
		self.assertEqual(self.focusedCells(), [(2, 1)])

	def test_pastTheStartIsTheRowBeforesLastCell(self):
		self.focus = self.grid.item(2)
		self.ask()
		self.region().previousLine()
		self.assertEqual(self.focusedCells(), [(1, 3)])

	def test_theColumnPannedToIsWhereTheCursorGoes(self):
		self.focus = self.grid.item(2)
		self.ask()
		self.region().previousLine()
		self.focus = self.grid.item(1)
		region = self.region()
		self.assertEqual(region.brailleCursorPos, region.rawText.index("Word"))

	def test_theLastRowStops(self):
		self.focus = self.grid.item(3)
		self.ask()
		self.region().nextLine()
		self.assertEqual(self.focusedCells(), [])


class TestNvdasColumnCommands(ObjectRowLineTestCase):
	def test_movingToAColumnRedrawsTheRow(self):
		self.ask()
		self.region()
		tableRowLine.columnMoved(self.handler)
		self.assertEqual(self.handler.gainedFocus, [self.focus])

	def test_withNothingAskedNothingIsRedrawn(self):
		self.handler.mainBuffer.regions = [NVDAObjectRegion(self.focus)]
		tableRowLine.columnMoved(self.handler)
		self.assertEqual(self.handler.gainedFocus, [])

	def test_thePatchMovesAsNvdaDoesFirst(self):
		self.ask()
		self.region()
		moved = []
		original = patches._originals.get("_moveToColumn")
		patches._originals["_moveToColumn"] = lambda row, obj: moved.append(obj)
		self.addCleanup(
			lambda: patches._originals.pop("_moveToColumn")
			if original is None
			else patches._originals.__setitem__("_moveToColumn", original),
		)
		patches._moveToColumnShowingIt(self.focus, self.focus.cellObjects[1])
		self.assertEqual(moved, [self.focus.cellObjects[1]])
		self.assertEqual(self.handler.gainedFocus, [self.focus])


class TestColumnInRow(unittest.TestCase):
	def setUp(self):
		self.grid = FakeGrid(FILES, columnCount=3)

	def test_aCellOfTheRowSaysItsColumn(self):
		row = self.grid.item(1)
		self.assertEqual(flowObjectTable.columnInRow(row.cellObjects[1], row), 2)

	def test_aCellOfAnotherRowIsNot(self):
		self.assertIsNone(flowObjectTable.columnInRow(self.grid.item(2).cellObjects[1], self.grid.item(1)))

	def test_somethingThatIsNotACellIsNot(self):
		self.assertIsNone(flowObjectTable.columnInRow(FakeNavigatorObject(), self.grid.item(1)))


if __name__ == "__main__":
	unittest.main()


SALES = [
	["Region", "Q1", "Q2"],
	["North", "1200", "1310"],
	["South", "980", "1024"],
	["East", "1750", "1690"],
]


class Workbook:
	"""A worksheet addressed by coordinate, as the Excel module hands one over.

	Its cells record being gone to, which is what a routing key or a pan asks of them.
	"""

	def __init__(self, rows, at):
		self.rows = rows
		self.at = at
		self.obj = FakeNavigatorObject("Sheet1", role="TABLE")
		self.goneTo = []

	def where(self):
		return self.at

	def shape(self):
		return (len(self.rows), len(self.rows[0]))

	def cellAt(self, row, column):
		if not (1 <= row <= len(self.rows) and 1 <= column <= len(self.rows[0])):
			return None
		cell = FakeNavigatorObject(self.rows[row - 1][column - 1], role="TABLECELL")
		cell.rowNumber = row
		cell.columnNumber = column
		cell.brlMultilineSheet = lambda: self
		cell.setFocus = lambda: self.goneTo.append((row, column))
		return cell


class SheetCellRegion(NVDAObjectRegion):
	"""The Excel module's region for a cell: a header added through `appendText`, then NVDA's
	own update, which is where the patch adopts it."""

	brlMultilineReadsARow = True

	def update(self):
		theirs = self.appendText
		self.appendText = " Region" + theirs
		try:
			patches._objectRegionUpdateAdoptingRows(self)
		finally:
			self.appendText = theirs


class SheetRowLineTestCase(unittest.TestCase):
	def setUp(self):
		import api
		import braille

		tableRowLine.clear()
		self.addCleanup(tableRowLine.clear)
		original = patches._originals.get("objectRegionUpdate")
		patches._originals["objectRegionUpdate"] = NVDAObjectRegion.update
		self.addCleanup(
			lambda: patches._originals.pop("objectRegionUpdate")
			if original is None
			else patches._originals.__setitem__("objectRegionUpdate", original),
		)
		self.sheet = Workbook(SALES, at=(2, 2))
		self.focus = self.sheet.cellAt(2, 2)
		for name, answer in (
			("getFocusObject", lambda: self.focus),
			("getNavigatorObject", lambda: self.focus),
		):
			self.addCleanup(setattr, api, name, getattr(api, name))
			setattr(api, name, answer)
		self.handler = FakeBrailleHandler()
		self.addCleanup(setattr, braille, "handler", braille.handler)
		braille.handler = self.handler

	def ask(self, columns=()):
		tableRowLine.request(tableAt(self.focus), columns)

	def moveTo(self, row, column):
		self.sheet.at = (row, column)
		self.focus = self.sheet.cellAt(row, column)

	def region(self, cls=SheetCellRegion):
		region = cls(self.focus)
		region.update()
		self.handler.mainBuffer.regions = [region]
		return region


class TestReadingAWorksheetsRow(SheetRowLineTestCase):
	def test_theCellsRowIsReadInTheExcelModulesRegion(self):
		self.ask()
		region = self.region()
		self.assertEqual(region.rawText, "North | 1200 | 1310")
		self.assertEqual(region.brailleCursorPos, region.rawText.index("1200"))
		self.assertIsInstance(region, SheetCellRegion)
		self.assertIsInstance(region, tableRowLine._ObjectRowLine)

	def test_leftAndRightArrowMoveTheCursorAlongTheLine(self):
		"""Excel's own arrow keys focus the next cell, which says its column."""
		self.ask()
		self.region()
		self.moveTo(2, 3)
		region = self.region()
		self.assertEqual(region.rawText, "North | 1200 | 1310")
		self.assertEqual(region.brailleCursorPos, region.rawText.index("1310"))

	def test_downArrowShowsTheRowBelow(self):
		self.ask()
		self.region()
		self.moveTo(3, 2)
		region = self.region()
		self.assertEqual(region.rawText, "South | 980 | 1024")
		self.assertEqual(region.brailleCursorPos, region.rawText.index("980"))

	def test_takingTheRequestBackGivesTheModulesReadingBack(self):
		"""Header and all, and the header once."""
		self.ask()
		region = self.region()
		tableRowLine.clear()
		region.update()
		self.assertFalse(region.isReadingARow)
		self.assertEqual(region.rawText.count("Region"), 1)
		self.assertNotIn(" | ", region.rawText)

	def test_aSelectedRangeIsNvdasToShow(self):
		"""Several cells selected are drawn by NVDA's plain region, which reads no row."""
		self.ask()
		region = NVDAObjectRegion(self.focus)
		patches._objectRegionUpdateAdoptingRows(region)
		self.assertFalse(region.isReadingARow)
		self.assertNotIn(" | ", region.rawText)

	def test_aHiddenColumnIsLeftOut(self):
		self.sheet.columnsShowing = lambda: {1, 3}
		self.moveTo(2, 1)
		self.ask()
		self.assertEqual(self.region().rawText, "North | 1310")

	def test_butNeverTheReadersOwnColumn(self):
		self.sheet.columnsShowing = lambda: {1, 3}
		self.ask()
		self.assertEqual(self.region().rawText, "North | 1200 | 1310")


class TestMovingInAWorksheetsRow(SheetRowLineTestCase):
	def test_routingToAnotherCellGoesToIt(self):
		self.ask()
		region = self.region()
		region.routeTo(region.rawText.index("1310"))
		self.assertEqual(self.sheet.goneTo, [(2, 3)])

	def test_panningPastTheEndGoesToTheNextRow(self):
		self.ask()
		self.region().nextLine()
		self.assertEqual(self.sheet.goneTo, [(3, 1)])

	def test_panningPassesOverHiddenRows(self):
		self.sheet.rowAfter = lambda row, by: 4 if (row, by) == (2, 1) else row + by
		self.ask()
		self.region().nextLine()
		self.assertEqual(self.sheet.goneTo, [(4, 1)])

	def test_panningStopsAtTheLastRow(self):
		self.moveTo(4, 1)
		self.ask()
		self.region().nextLine()
		self.assertEqual(self.sheet.goneTo, [])
