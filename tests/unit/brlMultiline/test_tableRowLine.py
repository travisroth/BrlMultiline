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
	FakeTableDocument,
	FakeTextInfo,
	installStubs,
)

installStubs()

from brlMultiline import patches, tableRowLine  # noqa: E402
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


if __name__ == "__main__":
	unittest.main()
