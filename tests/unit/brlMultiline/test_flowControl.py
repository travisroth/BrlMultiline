# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for laying blocks out and driving a band of them.

The renderer is exercised in fill mode, where the row cutting is the add-on's own pure
arithmetic and so runs under the test harness. Word wrapping delegates to NVDA's buffer
and is a hardware question rather than a unit test one.

One cell per character, which is what the harness's regions produce, so a row of eight
cells is eight characters and a test can say what it expects to feel.
"""

import unittest

from ._stubs import (
	CursorManagerRegion,
	FakeNavigatorObject,
	FakeTreeInterceptor,
	installStubs,
)

installStubs()

from brlMultiline.flow import Edge, EdgeState, RowKind  # noqa: E402
from brlMultiline.flowControl import FlowController  # noqa: E402
from brlMultiline.flowRender import FlowRenderer  # noqa: E402
from brlMultiline.flowSources import DocumentFlowSource, FetchBudget, regionFactoryFor  # noqa: E402


class FakeHandler:
	"""Enough of the braille handler for a layout buffer to read its settings through."""

	def __init__(self):
		self.buffer = None


def renderer(numCols=8, **kwargs) -> FlowRenderer:
	return FlowRenderer(FakeHandler(), numCols=numCols, fillRows=True, **kwargs)


def controllerOver(lines, caretIndex=0, numRows=4, numCols=8, live=False, budget=None):
	"""Build a controller over a browse mode document of the given lines."""
	interceptor = FakeTreeInterceptor(lines, caretIndex=caretIndex)
	source = DocumentFlowSource(
		interceptor,
		regionFactoryFor(CursorManagerRegion(interceptor), live=live),
		generation=1,
		budget=budget,
	)
	control = FlowController(source, renderer(numCols), numRows=numRows, live=live)
	control.enterAtCursor()
	return control


def rowTexts(control: FlowController) -> list[str]:
	"""What each row of the band reads, as characters rather than cells."""
	numCols = control.renderer.numCols
	cells = control.cells()
	rows = []
	for index in range(control.window.numRows):
		row = cells[index * numCols : (index + 1) * numCols]
		rows.append("".join(chr(cell) if cell else " " for cell in row))
	return rows


class TestRenderer(unittest.TestCase):
	def test_aShortBlockIsOneRowPaddedToTheBand(self):
		control = controllerOver(["abc"], numCols=8)
		rendered = control.window.blocks[0]
		self.assertEqual(rendered.numRows, 1)
		self.assertEqual(len(rendered.rows[0]), 3)

	def test_aLongBlockIsCutIntoRows(self):
		control = controllerOver(["a" * 20], numCols=8)
		rendered = control.window.blocks[0]
		self.assertEqual(rendered.numRows, 3)
		self.assertEqual([len(row) for row in rendered.rows], [8, 8, 4])

	def test_aBlockLongerThanOnePassIsStillWhole(self):
		# The layout buffer holds a few rows at a time and is walked; a paragraph longer
		# than one pass must not be cut off at the end of the first.
		control = controllerOver(["a" * 200], numCols=8, numRows=2)
		rendered = control.window.blocks[0]
		self.assertEqual(rendered.numRows, 25)

	def test_aBlockIsNotLaidOutForever(self):
		control = controllerOver(["a" * 5000], numCols=8, numRows=2)
		self.assertLessEqual(control.window.blocks[0].numRows, 64)

	def test_anEmptyBlockIsOneBlankRow(self):
		control = controllerOver([""], numCols=8)
		rendered = control.window.blocks[0]
		self.assertEqual(rendered.numRows, 1)
		self.assertEqual(rendered.rows[0], ())

	def test_everyCellKnowsWhereItCameFrom(self):
		control = controllerOver(["abcdefghij"], numCols=8)
		rendered = control.window.blocks[0]
		self.assertEqual(rendered.positions[0], tuple(range(8)))
		self.assertEqual(rendered.positions[1][:2], (8, 9))

	def test_aRowIsLeftAtItsNaturalLength(self):
		# Padding is added once, when the window is assembled. Doing it here as well would
		# bury cells belonging to no position inside a block's own rows.
		control = controllerOver(["abc"], numCols=8)
		rendered = control.window.blocks[0]
		self.assertEqual(len(rendered.positions[0]), 3)
		self.assertEqual(len(control.cells()), control.window.numRows * 8)

	def test_theRenderKeyRecordsTheWidth(self):
		self.assertEqual(renderer(numCols=12).renderKey.numCols, 12)


class TestPackingOnTheBand(unittest.TestCase):
	"""The rule the whole design rests on, now with real cells behind it."""

	def test_aHeadingDoesNotShareItsRowWithWhatFollows(self):
		control = controllerOver(["Heading", "body text here"], numCols=8, numRows=4)
		self.assertEqual(rowTexts(control), ["Heading ", "body tex", "t here  ", "        "])

	def test_theDisplayKeepsGoingAfterAShortBlock(self):
		control = controllerOver(["Hi", "there", "you"], numCols=8, numRows=4)
		self.assertEqual(rowTexts(control)[:3], ["Hi      ", "there   ", "you     "])

	def test_blankRowsAtTheEndMeanTheEndOfTheDocument(self):
		control = controllerOver(["only"], numCols=8, numRows=4)
		self.assertEqual(control.window.edges[Edge.AFTER], EdgeState.END)
		self.assertEqual(control.window.visibleRows()[-1].kind, RowKind.BLANK)

	def test_aBudgetStopDoesNotLookLikeTheEnd(self):
		# The reader must not be told, spatially, that a page ended when it merely took too
		# long to read.
		budget = FetchBudget(maxBlocks=1, maxSeconds=10.0, clock=lambda: 0.0)
		control = controllerOver(["a"] + [""] * 40 + ["b"], numCols=8, numRows=4, budget=budget)
		self.assertEqual(control.window.edges[Edge.AFTER], EdgeState.DEFERRED)
		self.assertEqual(control.window.visibleRows()[-1].kind, RowKind.PENDING)


class TestFetching(unittest.TestCase):
	def test_thebandFillsFromTheCursorDownward(self):
		control = controllerOver(["a", "b", "c", "d", "e", "f"], caretIndex=0, numRows=4)
		self.assertEqual(rowTexts(control)[:4], ["a       ", "b       ", "c       ", "d       "])

	def test_onlyWhatTheBandNeedsIsFetched(self):
		# Nothing reads ahead: a four row band over a long document holds the blocks it
		# shows and its margin, not the document.
		control = controllerOver([str(number) for number in range(50)], caretIndex=0, numRows=4)
		self.assertLess(len(control.window.blocks), 12)

	def test_panningFetchesWhatItNeedsAndMoves(self):
		control = controllerOver(["a", "b", "c", "d", "e", "f", "g", "h"], caretIndex=0, numRows=4)
		self.assertTrue(control.panForward())
		self.assertEqual(rowTexts(control)[0], "e       ")

	def test_panningStopsAtTheEnd(self):
		control = controllerOver(["a", "b"], caretIndex=0, numRows=4)
		self.assertFalse(control.panForward())

	def test_panningBackUndoesPanningForward(self):
		control = controllerOver([str(number) for number in range(20)], caretIndex=0, numRows=4)
		before = rowTexts(control)
		control.panForward()
		control.panForward()
		control.panBack()
		control.panBack()
		self.assertEqual(rowTexts(control), before)


class TestCursor(unittest.TestCase):
	def test_panningALiveFlowMovesTheBrowseModeCursor(self):
		control = controllerOver([str(number) for number in range(20)], caretIndex=0, numRows=4, live=True)
		self.assertEqual(control.source.obj.caretIndex, 0)
		control.panForward()
		# The cursor goes to the top block of the new window, so the arrow keys carry on
		# from what is under the reader's hands.
		self.assertEqual(control.source.obj.caretIndex, 4)

	def test_panningAViewerMovesNothing(self):
		control = controllerOver([str(number) for number in range(20)], caretIndex=0, numRows=4, live=False)
		control.panForward()
		self.assertEqual(control.source.obj.caretIndex, 0)

	def test_onlyOneBlockIsEverActive(self):
		control = controllerOver(["a", "b", "c", "d"], caretIndex=0, numRows=4)
		control.panForward()
		active = [
			block.region.isActive for block in control.blocks.values() if hasattr(block.region, "isActive")
		]
		self.assertEqual(active.count(True), 1)

	def test_steppingABlockMovesFromTheActiveBlockNotTheLastRendered(self):
		# NVDA's own line commands act on regions[-1], which here is the block at the
		# bottom of the window rather than the one the cursor is in.
		control = controllerOver(["a", "b", "c", "d"], caretIndex=0, numRows=4)
		self.assertNotEqual(control.activeBlockId, control.window.bottomBlockId())
		self.assertTrue(control.stepBlock(forward=True))
		self.assertEqual(control.activeBlockId, control.window.blocks[1].blockId)

	def test_steppingOntoABlockAlreadyShownDoesNotMoveTheWindow(self):
		control = controllerOver(["a", "b", "c", "d", "e"], caretIndex=0, numRows=4)
		before = rowTexts(control)
		control.stepBlock(forward=True)
		self.assertEqual(rowTexts(control), before)

	def test_steppingPastTheBottomScrollsByTheSmallestAmount(self):
		control = controllerOver(["a", "b", "c", "d", "e"], caretIndex=0, numRows=4)
		for _ in range(4):
			control.stepBlock(forward=True)
		rows = rowTexts(control)
		self.assertEqual(rows[-1], "e       ")
		self.assertEqual(rows[0], "b       ")


class TestRouting(unittest.TestCase):
	def test_aPressOnContentReachesItsBlock(self):
		control = controllerOver(["abc", "def"], numCols=8, numRows=4, live=True)
		self.assertTrue(control.routeTo(9))
		second = control.window.blocks[1].blockId
		self.assertEqual(control.regionFor(second).routedTo, 1)

	def test_aPressMakesTheBlockItReachedActive(self):
		control = controllerOver(["abc", "def"], numCols=8, numRows=4)
		control.routeTo(9)
		self.assertEqual(control.activeBlockId, control.window.blocks[1].blockId)

	def test_aViewerRoutesWithoutActivating(self):
		# NVDA activates the position when a press lands where the region already thinks
		# its cursor is, which in a document the reader is only watching would follow a
		# link.
		control = controllerOver(["abc", "def"], numCols=8, numRows=4, live=False)
		self.assertTrue(control.routeTo(9))
		second = control.window.blocks[1].blockId
		self.assertFalse(hasattr(control.regionFor(second), "routedTo"))

	def test_aPressInPaddingDoesNothing(self):
		control = controllerOver(["abc", "def"], numCols=8, numRows=4)
		self.assertFalse(control.routeTo(5))

	def test_aPressOnABlankRowDoesNothing(self):
		control = controllerOver(["abc"], numCols=8, numRows=4)
		self.assertFalse(control.routeTo(20))


class TestDescribing(unittest.TestCase):
	def test_everyRowIsDescribed(self):
		control = controllerOver(["Heading", "body"], numCols=8, numRows=4)
		lines = control.describeRows()
		self.assertEqual(len(lines), 4)
		self.assertIn("Heading", lines[0])
		self.assertIn("end of content", lines[-1])

	def test_theActiveBlockIsMarked(self):
		control = controllerOver(["a", "b"], numCols=8, numRows=4)
		self.assertIn("*", control.describeRows()[0])


class TestDryRun(unittest.TestCase):
	"""The diagnostic that reads a document into the log, with nothing on a display."""

	def setUp(self):
		import api

		self.previous = api.getNavigatorObject
		self.addCleanup(setattr, api, "getNavigatorObject", self.previous)

	def _navigateTo(self, lines, caretIndex=0):
		import api

		interceptor = FakeTreeInterceptor(lines, caretIndex=caretIndex)
		api.getNavigatorObject = lambda: FakeNavigatorObject("a page", treeInterceptor=interceptor)
		return interceptor

	def test_itReportsWhatTheBandWouldHold(self):
		from brlMultiline.flowDryRun import dryRun

		self._navigateTo([str(number) for number in range(20)])
		lines = dryRun(handler=FakeHandler())
		self.assertTrue(any("On arrival" in line for line in lines))
		self.assertTrue(any("After panning forward" in line for line in lines))

	def test_itChecksThatPanningBackReturns(self):
		from brlMultiline.flowDryRun import dryRun

		self._navigateTo([str(number) for number in range(40)])
		lines = dryRun(handler=FakeHandler())
		self.assertIn("Reversibility: panning back returned to the arrival rows.", lines)

	def test_itMovesNothingInTheDocument(self):
		# A diagnostic must not move the reader's place in the page it is diagnosing.
		from brlMultiline.flowDryRun import dryRun

		interceptor = self._navigateTo([str(number) for number in range(40)], caretIndex=3)
		dryRun(handler=FakeHandler())
		self.assertEqual(interceptor.caretIndex, 3)

	def test_somethingWithNoTextIsSaidToHaveNone(self):
		from brlMultiline.flowDryRun import dryRun

		import api

		api.getNavigatorObject = lambda: FakeNavigatorObject("a button")
		lines = dryRun(handler=FakeHandler())
		self.assertIn("nothing here can be flowed", lines[0])

	def test_aFailureSaysWhichStepFailed(self):
		# One message for four different failures said nothing about which had happened,
		# which is how a real failure went undiagnosed.
		from brlMultiline.flowDryRun import dryRun

		import api

		api.getNavigatorObject = lambda: FakeNavigatorObject("a button")
		lines = dryRun(handler=FakeHandler())
		self.assertTrue(any("getFocusRegions gave" in line for line in lines))
		self.assertTrue(any("Nothing to flow" in line for line in lines))


if __name__ == "__main__":
	unittest.main()
