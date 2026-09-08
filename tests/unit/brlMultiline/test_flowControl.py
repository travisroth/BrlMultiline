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
	OffsetDocument,
	FakeNavigatorObject,
	FakeTreeInterceptor,
	installStubs,
)

installStubs()

from brlMultiline.flow import Edge, EdgeState, RowKind  # noqa: E402
from brlMultiline.flowControl import FlowController  # noqa: E402
from brlMultiline.flowRender import FlowRenderer  # noqa: E402
from brlMultiline.flowSources import DocumentFlowSource, FetchBudget, regionFactoryFor  # noqa: E402


class FakeClock:
	"""A clock a test drives by hand, so a budget can run out without anything sleeping."""

	def __init__(self, step=0.0):
		self.now = 0.0
		self.step = step

	def __call__(self):
		self.now += self.step
		return self.now


class FakeHandler:
	"""Enough of the braille handler for a layout buffer to read its settings through."""

	def __init__(self):
		self.buffer = None


def renderer(numCols=8, **kwargs) -> FlowRenderer:
	return FlowRenderer(FakeHandler(), numCols=numCols, fillRows=True, **kwargs)


def controllerOver(
	lines,
	caretIndex=0,
	numRows=4,
	numCols=8,
	live=False,
	budget=None,
	enter=True,
	atObject=None,
	interactive=False,
	bookmarks=True,
	unit="line",
	expandsBackAt=None,
	paragraphBreaks=None,
	expandsOnAt=None,
):
	"""Build a controller over a browse mode document of the given lines."""
	interceptor = FakeTreeInterceptor(
		lines,
		caretIndex=caretIndex,
		bookmarks=bookmarks,
		expandsBackAt=expandsBackAt,
		paragraphBreaks=paragraphBreaks,
		expandsOnAt=expandsOnAt,
	)
	source = DocumentFlowSource(
		interceptor,
		regionFactoryFor(CursorManagerRegion(interceptor), live=live),
		unit=unit,
		generation=1,
		budget=budget,
		interactive=interactive,
	)
	control = FlowController(source, renderer(numCols), numRows=numRows, live=live)
	if enter:
		control.enterAtCursor(atObject=atObject)
	return control


def control(index: int, role: str = "EDITABLETEXT") -> FakeNavigatorObject:
	""":return: a form control the document can place on a given line."""
	return FakeNavigatorObject(name="a field", role=role, documentIndex=index)


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

	def test_theCacheDoesNotGrowAsTheReaderPansOn(self):
		# The margin rule is a window's worth either side. Without trimming, a long read
		# accumulates every block it has passed.
		control = controllerOver([str(number) for number in range(200)], caretIndex=0, numRows=4)
		for _ in range(20):
			control.panForward()
		self.assertLessEqual(len(control.window.blocks), 16)
		self.assertLessEqual(len(control.blocks), 17)

	def test_trimmingKeepsTheDisplayIntact(self):
		control = controllerOver([str(number) for number in range(200)], caretIndex=0, numRows=4)
		control.panForward()
		control.panForward()
		self.assertEqual(rowTexts(control)[0], "8       ")

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


class TestLongBlocks(unittest.TestCase):
	"""A block longer than one rendering is read in chunks, not cut off."""

	def _paragraph(self, characters=400, numCols=8, numRows=4, chunkRows=8, budget=None):
		interceptor = FakeTreeInterceptor(["".join(chr(ord("a") + (n % 26)) for n in range(characters))])
		source = DocumentFlowSource(
			interceptor,
			regionFactoryFor(CursorManagerRegion(interceptor), live=False),
			generation=1,
			budget=budget,
		)
		render = FlowRenderer(FakeHandler(), numCols=numCols, fillRows=True, maxRows=chunkRows)
		control = FlowController(source, render, numRows=numRows)
		control.enterAtCursor()
		return control

	def test_aChunkHoldsOnlyItsOwnRows(self):
		control = self._paragraph()
		self.assertEqual(control.window.blocks[0].numRows, 8)
		self.assertTrue(control.window.blocks[0].moreRows)

	def test_panningReadsPastTheEndOfAChunk(self):
		# The whole point: 400 characters over an 8 cell band is 50 rows, and the chunk
		# holds 8 of them. Everything past the first chunk used to be unreachable.
		control = self._paragraph()
		seen = [rowTexts(control)[0]]
		for _ in range(10):
			if not control.panForward():
				break
			seen.append(rowTexts(control)[0])
		self.assertEqual(len(seen), 11)
		self.assertEqual(len(set(seen)), 11)

	def test_aContinuedChunkCountsAsSomethingComingBack(self):
		"""A chunk is as much a step forward as a whole block, and the count of blocks does
		not move for it. A fill that reported new blocks alone would call a paragraph still
		being read a document that will not answer, and the pass that comes back to finish a
		band the budget cut short would stop on it."""
		control = self._paragraph()
		before = len(control.window.blocks)
		with control.operation():
			self.assertTrue(control._fetchOne(Edge.AFTER))
		self.assertEqual(len(control.window.blocks), before)

	def test_theRowsAreContinuousAcrossAChunkBoundary(self):
		control = self._paragraph(characters=400)
		rows = rowTexts(control)
		control.panForward()
		self.assertEqual(rowTexts(control)[0], "".join(chr(ord("a") + ((32 + n) % 26)) for n in range(8)))
		self.assertNotEqual(rowTexts(control)[0], rows[0])

	def test_panningBackAcrossAChunkBoundaryReturns(self):
		control = self._paragraph()
		before = rowTexts(control)
		for _ in range(4):
			control.panForward()
		for _ in range(4):
			control.panBack()
		self.assertEqual(rowTexts(control), before)

	def test_aBlockShorterThanAChunkSaysThereIsNoMore(self):
		control = self._paragraph(characters=20)
		self.assertFalse(control.window.blocks[0].moreRows)

	def test_describingANonzeroChunkUsesItsLocalRows(self):
		control = self._paragraph()
		for _ in range(3):
			control.panForward()
		self.assertGreater(control.window.blocks[0].rowOffset, 0)
		lines = control.describeRows()
		self.assertTrue(all("[8/8 cells]" in line for line in lines), lines)
		self.assertTrue(all(": ''" not in line for line in lines), lines)
		self.assertTrue(all(" of 8:" not in line for line in lines), lines)

	def test_fillStatsCountRowsInANonzeroChunk(self):
		control = self._paragraph()
		for _ in range(3):
			control.panForward()
		self.assertEqual(control.fillStats(), (32, 32))

	def test_continuingALongBlockSpendsTheOperationBudget(self):
		budget = FetchBudget(maxBlocks=1, maxSeconds=10.0, clock=lambda: 0.0)
		control = self._paragraph(budget=budget)
		control.panForward()
		self.assertTrue(control.panForward())
		self.assertEqual(budget.blocks, 1)


class TestBudgetCoversAnOperation(unittest.TestCase):
	"""One budget for what the reader asked for, not one per call underneath it."""

	def test_fillingIsBoundedByOneBudget(self):
		# Reset per source call, a budget of one block still filled a four row band with
		# four blocks, which is no bound at all.
		budget = FetchBudget(maxBlocks=1, maxSeconds=10.0, clock=lambda: 0.0)
		control = controllerOver([str(number) for number in range(20)], numRows=4, budget=budget)
		self.assertLessEqual(len(control.window.blocks), 2)

	def test_aBoundedFillSaysThereIsMore(self):
		budget = FetchBudget(maxBlocks=1, maxSeconds=10.0, clock=lambda: 0.0)
		control = controllerOver([str(number) for number in range(20)], numRows=4, budget=budget)
		self.assertEqual(control.window.edges[Edge.AFTER], EdgeState.DEFERRED)
		self.assertEqual(control.window.visibleRows()[-1].kind, RowKind.PENDING)

	def test_aFreshOperationGetsTheBudgetAgain(self):
		# A bound on one pan is not a bound on every pan afterwards.
		budget = FetchBudget(maxBlocks=2, maxSeconds=10.0, clock=lambda: 0.0)
		control = controllerOver([str(number) for number in range(40)], numRows=2, budget=budget)
		self.assertTrue(control.panForward())
		self.assertTrue(control.panForward())

	def test_layingOutIsChargedForAsWellAsReading(self):
		clock = FakeClock(step=0.004)
		budget = FetchBudget(maxBlocks=100, maxSeconds=0.02, clock=clock)
		control = controllerOver([str(number) for number in range(40)], numRows=8, budget=budget)
		# The clock only advances when something asks it the time, so a run that charged
		# for reading alone would get further than one that charges for both.
		self.assertLess(len(control.window.blocks), 8)

	def test_panningCannotFetchPastTheSharedBudget(self):
		budget = FetchBudget(maxBlocks=1, maxSeconds=10.0, clock=lambda: 0.0)
		control = controllerOver([str(number) for number in range(20)], numRows=4, budget=budget)
		before = len(control.window.blocks)
		self.assertFalse(control.panForward())
		self.assertLessEqual(len(control.window.blocks) - before, 1)
		self.assertLessEqual(budget.blocks, 1)
		self.assertIs(control.window.edges[Edge.AFTER], EdgeState.DEFERRED)


class TestAFillSaysWhetherItAddedAnything(unittest.TestCase):
	"""What the caller that comes back for the rest needs to know, and the only thing it can
	act on: a pass that added nothing is a document that will not answer, and asking that one
	again is how a refusal turns into a loop."""

	def test_aFillWithNothingToFetchSaysSo(self):
		control = controllerOver(["a", "b"], numRows=4)
		self.assertFalse(control.fill())

	def test_aFillCutShortSaysItAddedWhatItGot(self):
		"""**Not whether the band is full.** The band is still short afterwards — that is what
		being cut short means — and a fill that reported only "full or not" would be answering
		"no" to a document that is answering perfectly well, one block at a time."""
		budget = FetchBudget(maxBlocks=1, maxSeconds=10.0, clock=lambda: 0.0)
		control = controllerOver([str(number) for number in range(20)], numRows=4, budget=budget)
		self.assertTrue(control.fill())
		self.assertTrue(control.hasMoreToFetch)


class TestPlacingTheReaderWhenTheReachRanOut(unittest.TestCase):
	"""**Outlook's grouped inbox, and the reader who arrowed up out of the band.**

	An arrival first tries to extend the band to the block the reader moved to, and on a slow
	application that walk can spend the whole allowance and arrive nowhere: one step through
	that list took twice what a whole operation is given. The fallback that places the band
	afresh around them was already there and could not afford to run — it placed the one block
	it is handed for nothing and was refused every fetch after it, so the reader who arrowed up
	one row got that row and blank rows under it, until they left the folder and came back,
	which builds a controller with a budget of its own.
	"""

	def _reachedTooFar(self):
		""":return: a controller whose reader has jumped further than one allowance reaches."""
		budget = FetchBudget(maxBlocks=1, maxSeconds=10.0, clock=lambda: 0.0)
		control = controllerOver(
			[str(number) for number in range(40)],
			caretIndex=0,
			numRows=4,
			budget=budget,
		)
		control.source.obj.caretIndex = 30
		return control, budget

	def test_theReaderIsPlacedWhereTheyAre(self):
		control, _budget = self._reachedTooFar()
		control.followCursor()
		self.assertIn("30", "".join(rowTexts(control)))

	def test_andTheBandAroundThemIsFilled(self):
		"""The half that was missing. One block on a display of four is not being placed."""
		control, budget = self._reachedTooFar()
		control.followCursor()
		self.assertEqual(budget.renewals, 1)
		self.assertGreater(len(control.window.blocks), 1)

	def test_andTheStopIsStillCounted(self):
		"""An operation that hid its own stop would leave the numbers saying the budget fits
		when the reader felt it not fitting. The renewal is counted separately.

		Read on a document whose end the placing reaches, so that nothing after the renewal
		is refused: with a refusal after it the stop would be recorded again whatever the
		renewal did with the first one, and the test would pass without the rule."""
		budget = FetchBudget(maxBlocks=1, maxSeconds=10.0, clock=lambda: 0.0)
		control = controllerOver(
			[str(number) for number in range(9)],
			caretIndex=0,
			numRows=4,
			budget=budget,
		)
		control.source.obj.caretIndex = 8
		before = budget.stops
		control.followCursor()
		self.assertEqual(budget.renewals, 1)
		self.assertEqual(budget.stops, before + 1)

	def test_anArrivalThatReachedTheReaderIsNotRenewed(self):
		"""The ordinary move, which is nearly every move: one allowance, as it always was."""
		control = controllerOver([str(number) for number in range(40)], caretIndex=0, numRows=4)
		control.source.obj.caretIndex = 2
		control.followCursor()
		self.assertEqual(control.source.budget.renewals, 0)


class TestALineDrawnTwiceWhileWriting(unittest.TestCase):
	"""**The duplicate at the bottom of a multi-line edit.**

	More lines than the display holds, the caret on the last of them, and a keystroke: the
	band is read again from the caret and the rows above it are fetched by walking back. A
	rich editor answering during that keystroke expands the unit above the caret over the
	line the caret is on, so that block holds the reader's own line as well — and the band
	draws it once there and once in the caret's own block below.
	"""

	LINES = ["one", "two", "three", "four", "five"]

	def _writingBand(self, expandsOnAt=None):
		return controllerOver(
			self.LINES,
			caretIndex=3,
			numRows=4,
			numCols=16,
			live=True,
			interactive=True,
			expandsOnAt=expandsOnAt,
		)

	def test_theCaretsLineIsDrawnOnce(self):
		control = self._writingBand(expandsOnAt={2: 3})
		control.followCursor()
		self.assertEqual("".join(rowTexts(control)).count("four"), 1)

	def test_andTheRowAboveIsStillFetchedWhenTheEditorAnswersProperly(self):
		"""The guard must cost nothing in the ordinary case: refusing a walk back that is
		perfectly good would leave the reader typing on the top row with what they wrote
		above them gone, which is the bug the context above the caret exists to heal."""
		control = self._writingBand()
		control.followCursor()
		self.assertIn("three", "".join(rowTexts(control)))


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

	def test_eachRowOfABlockReportsItsOwnText(self):
		# Reporting the whole block's text on each of its rows reads as a repeated
		# paragraph rather than as one paragraph laid out across the band.
		control = controllerOver(["abcdefghijklmnop"], numCols=8, numRows=4)
		lines = control.describeRows()
		self.assertIn("'abcdefgh'", lines[0])
		self.assertIn("'ijklmnop'", lines[1])

	def test_theBandFillIsMeasured(self):
		# A block always starts a new row, so what a reading unit costs in empty cells is
		# worth measuring rather than guessing at.
		control = controllerOver(["abc", "de"], numCols=8, numRows=4)
		self.assertEqual(control.fillStats(), (5, 32))

	def test_aFullRowCountsAsFull(self):
		control = controllerOver(["abcdefgh"], numCols=8, numRows=1)
		self.assertEqual(control.fillStats(), (8, 8))

	def test_theRowCountIsSaidPerRow(self):
		control = controllerOver(["abcdefghijklmnop"], numCols=8, numRows=4)
		self.assertIn("block row 2 of 2", control.describeRows()[1])

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


class TestAFormControlsPrompt(unittest.TestCase):
	"""A control's prompt is context, and context goes above it.

	Arriving at a form field on a single line display shows the field, with whatever said
	what it was for now behind the reader. A flow has rows to spend on that, and this is
	what they are spent on.

	The reader arrives at the *object*, and the document is read at that object's own place.
	That matters and is not ceremony: tabbing into an edit field or a combo box drops browse
	mode into focus mode, so the cursor is inside the control and the line it is on is the
	value being edited rather than the control. Reading at the object is what gets the line
	browse mode would show — the one carrying the name, the role and the state.
	"""

	FORM = ["Name", "f: Ada", "Town", "f: Kent"]
	"""A form as browse mode lays it out: a prompt, then the field, then the next prompt.

	Short enough to be one row each at eight cells, so that a row of the display is a line
	of the form and a test can say what the reader would feel.
	"""

	def form(self, at=1, caretIndex=0, numRows=4, lines=None, live=False):
		"""Arrive at the control on one line of the form, as tabbing to it does.

		:param at: which line of the form holds the control, or None to read from the cursor.
		:param caretIndex: where the browse mode cursor is, which for a control the reader
			has entered is not where the control is.
		"""
		return controllerOver(
			lines or self.FORM,
			caretIndex=caretIndex,
			numRows=numRows,
			live=live,
			atObject=control(at) if at is not None else None,
		)

	def test_thePromptIsOnTheRowAboveTheControl(self):
		flow = self.form()
		rows = rowTexts(flow)
		self.assertEqual(rows[0], "Name    ")
		self.assertEqual(rows[1], "f: Ada  ")

	def test_theControlIsReadEvenWithTheCursorInsideIt(self):
		# Focus mode: the cursor is somewhere else entirely, and the control is still what
		# is shown. This is the case the field-reading version got wrong.
		flow = self.form(at=1, caretIndex=3)
		self.assertEqual(rowTexts(flow)[1], "f: Ada  ")

	def test_theFormGoesOnBelowIt(self):
		flow = self.form()
		# The control asks for a blank row after it, so the next prompt is separated from
		# the answer rather than sitting against it.
		rows = rowTexts(flow)
		self.assertEqual(rows[2], "        ")
		self.assertEqual(rows[3], "Town    ")

	def test_arrivingAtProsePutsItAtTheTop(self):
		"""Nothing changes for anything that is not a control."""
		flow = self.form(at=None, caretIndex=2)
		self.assertEqual(rowTexts(flow)[0], "Town    ")

	def test_aControlAtTheVeryTopStaysAtTheTop(self):
		flow = self.form(at=0, lines=["f: Ada", "Town"])
		self.assertEqual(rowTexts(flow)[0], "f: Ada  ")

	def test_aLongPromptIsShownByItsLastRows(self):
		# Half the band at most, so the control is still on the display. Eight rows of
		# prompt, four row band: two rows of prompt, then the control.
		flow = self.form(at=1, numRows=4, lines=["a" * 64, "f: Ada", "Town"])
		rows = rowTexts(flow)
		self.assertEqual(rows[0], "a" * 8)
		self.assertEqual(rows[2], "f: Ada  ")

	def test_theControlIsTheActiveBlock(self):
		"""The window moved, not the cursor: the commands still act on the field."""
		flow = self.form()
		self.assertEqual(flow.activeBlockId, flow.window.blocks[1].blockId)

	def test_theCursorIsOnTheControlNotOnThePrompt(self):
		flow = self.form(live=True)
		self.assertIsNotNone(flow.cursorCell())
		self.assertGreaterEqual(flow.cursorCell(), flow.renderer.numCols)

	def test_thePromptCostsOneFetchAndNoMore(self):
		# It used to ask for half a band of rows, which on an eight row Monarch fetched four
		# blocks to use one and spent a third of the operation's budget doing it.
		budget = FetchBudget(maxBlocks=20, maxSeconds=10.0, clock=lambda: 0.0)
		flow = controllerOver(self.FORM, caretIndex=0, numRows=4, budget=budget, enter=False)
		flow.enterAtCursor(atObject=control(1))
		self.assertEqual(rowTexts(flow)[0], "Name    ")
		self.assertLessEqual(budget.blocks, 4)

	def test_anExplicitContextRowCountIsObeyed(self):
		"""A caller that knows what it wants is not overruled by the forms policy."""
		flow = self.form(at=None, caretIndex=2)
		flow.enterAtCursor(contextRows=1)
		self.assertEqual(rowTexts(flow)[0], "f: Ada  ")

	def test_anObjectTheDocumentCannotPlaceReadsFromTheCursor(self):
		flow = controllerOver(
			self.FORM,
			caretIndex=2,
			numRows=4,
			atObject=FakeNavigatorObject(name="somewhere else", role="EDITABLETEXT"),
		)
		self.assertEqual(rowTexts(flow)[0], "Town    ")


class TestReachingBackForContext(unittest.TestCase):
	"""Rows above the window are read by count, since the window itself never wants them."""

	def test_aFullWindowStillHasNothingAboveIt(self):
		control = controllerOver(["one", "two", "three", "four"], caretIndex=0)
		self.assertEqual(control.window.rowsAbove(), 0)

	def test_readingBackwardsPutsRowsAboveIt(self):
		control = controllerOver(["one", "two", "three", "four"], caretIndex=2)
		control._reachBack(2)
		self.assertGreaterEqual(control.window.rowsAbove(), 2)

	def test_theStartOfTheDocumentIsAsFarAsItGoes(self):
		control = controllerOver(["one", "two"], caretIndex=0)
		control._reachBack(4)
		self.assertEqual(control.window.rowsAbove(), 0)

	def test_aSpentBudgetStopsItAndSaysSo(self):
		# Not the end of the document: the display must not read as though it were.
		budget = FetchBudget(maxBlocks=0, clock=FakeClock())
		control = controllerOver(
			["one", "two", "three", "four", "five"],
			caretIndex=4,
			budget=budget,
		)
		budget.start()
		budget.blocks = budget.maxBlocks
		control._reachBack(3)
		self.assertIs(control.window.edges[Edge.BEFORE], EdgeState.DEFERRED)


class TestMovingTheWindowAlone(unittest.TestCase):
	"""What the line commands mean where the cursor must not move: a run of objects."""

	def test_itMovesTheWindowOnByABlock(self):
		flow = controllerOver([str(number) for number in range(20)], numRows=4)
		first = rowTexts(flow)[0]
		self.assertTrue(flow.shiftWindow(True))
		self.assertNotEqual(rowTexts(flow)[0], first)

	def test_andBackAgain(self):
		flow = controllerOver([str(number) for number in range(20)], caretIndex=8, numRows=4)
		before = rowTexts(flow)
		flow.shiftWindow(True)
		flow.shiftWindow(False)
		self.assertEqual(rowTexts(flow), before)

	def test_theActiveBlockDoesNotMoveWithIt(self):
		# The whole point: in a run of objects the active block is the reader's selection,
		# and moving it would arrow through a list they are only reading.
		flow = controllerOver([str(number) for number in range(20)], numRows=4)
		active = flow.activeBlockId
		flow.shiftWindow(True)
		self.assertEqual(flow.activeBlockId, active)

	def test_theEndOfTheDocumentStopsIt(self):
		flow = controllerOver(["one", "two"], numRows=4)
		flow.shiftWindow(True)
		self.assertFalse(flow.shiftWindow(True))

	def test_itTellsTheBandToRedraw(self):
		flow = controllerOver([str(number) for number in range(20)], numRows=4)
		drawn = []
		flow.onChanged = lambda: drawn.append(True)
		flow.shiftWindow(True)
		self.assertEqual(drawn, [True])


class TestAGrowingEdit(unittest.TestCase):
	"""A multi line edit grows into the space and pushes the rest off.

	The rule behind it is the general one — the cursor's own row must stay visible within
	the block it is in — and it is the same rule that keeps a reader at the bottom of a long
	paragraph from being thrown back to its top. A field being typed into is where it shows
	most, because there the block changes under the window on every keystroke.
	"""

	def field(self, text, numRows=2, numCols=8, caretOffset=None):
		control = controllerOver(["Notes", text], caretIndex=1, numRows=numRows, numCols=numCols, live=True)
		control.source.obj.caretOffset = len(text) if caretOffset is None else caretOffset
		# Where the caret is arrives with the next read of the block, as it does in NVDA.
		control.refreshActive()
		return control

	def grow(self, control, text, caretOffset=None):
		"""Type into the field, as the reader would, and let the band answer."""
		control.source.obj.lines[1] = text
		control.source.obj.caretOffset = len(text) if caretOffset is None else caretOffset
		control.refreshActive()

	def test_theCursorsRowStaysOnTheDisplay(self):
		control = self.field("abc")
		self.grow(control, "a" * 40)
		self.assertIsNotNone(control.cursorCell())

	def test_whatWasJustWrittenIsWhatIsShown(self):
		# Five rows of field in a two row band: the last two, not the first two.
		control = self.field("abc")
		self.grow(control, "a" * 32 + "end")
		self.assertEqual(rowTexts(control)[-1], "end     ")

	def test_aFieldThatStillFitsDoesNotMoveTheWindow(self):
		"""Nothing scrolls until what is being written runs off the bottom."""
		control = self.field("abc", numRows=4)
		self.grow(control, "abcdef")
		self.assertTrue(control.window.isVisible(control.activeBlockId, 0))
		self.assertEqual(rowTexts(control)[0], "abcdef  ")

	def test_theCursorRowIsWhereTheCaretIs(self):
		control = self.field("a" * 40, caretOffset=20)
		self.assertEqual(control.cursorRow(), 2)

	def test_aCaretAtTheEndOfWhatWasTypedIsOnTheLastRow(self):
		"""Where a reader writing always is, and the row that has to stay on the display."""
		control = self.field("a" * 16, caretOffset=16)
		self.assertEqual(control.cursorRow(), 1)

	def test_aCursorPastWhatIsRenderedIsWhereAWriterIs(self):
		# The gap between the caret moving and the flow re-rendering to catch it. Past the
		# end is where a reader writing always is, so the last row keeps what they have just
		# typed on the display.
		flow = controllerOver(["Name", "a" * 20], caretIndex=1, numCols=8, live=True)
		flow.activeRegion().brailleCursorPos = 99
		self.assertEqual(flow.cursorRow(), 2)

	def test_aCursorBeforeWhatIsRenderedIsNotGuessedAt(self):
		# The other direction has no such answer, and naming a row would move the window to
		# the wrong end of a long edit on no evidence at all.
		flow = controllerOver(["Name", "a" * 20], caretIndex=1, numCols=8, live=True)
		block = flow.blocks.get(flow.activeBlockId)
		flow.window.replaceBlock(flow.renderer.render(block, fromRow=2))
		flow.activeRegion().brailleCursorPos = 0
		self.assertIsNone(flow.cursorRow())

	def test_aViewerHasNoCursorRow(self):
		control = controllerOver(["Notes", "abc"], caretIndex=1, live=False)
		self.assertIsNone(control.cursorRow())

	def test_aCaretPastTheFirstRenderingChunkIsStillShown(self):
		# Sixty-four rows is the renderer's memory working set, not the longest field the
		# reader may edit. At eight cells this caret is on row 74.
		control = self.field("a" * 600, numRows=4, caretOffset=599)
		self.assertEqual(control.cursorRow(), 74)
		self.assertIsNotNone(control.cursorCell())
		self.assertGreater(
			control.window.blocks[control.window.blockIndex(control.activeBlockId)].rowOffset, 0
		)


class TestAllOfWhatTheReaderHasArrivedAt(unittest.TestCase):
	"""A block that wraps is one item and the reader is standing on all of it.

	Bringing it on by the line the cursor is on is the right rule for keeping the cursor in
	view and the wrong amount to show when the block has just become theirs: scrolling down
	onto a record two lines tall put its first line on the bottom row and left the second off
	the band, so the values that had wrapped were the ones they could not read. The same on a
	list of files whose names run to two lines. Reported from a spreadsheet and asked for
	across the board.
	"""

	def _rows(self, control):
		""":return: which line, and which of its wrapped rows, is on each row of the band."""
		found = []
		for row in control.window.visibleRows():
			mark = getattr(row, "blockId", None)
			if mark is None:
				continue
			found.append((getattr(mark.bookmark, "index", mark.bookmark), row.rowIndex))
		return found

	def _flow(self, numRows=4):
		"""A document of two line items, each too long for the band."""
		return controllerOver(
			["one two three", "four five six", "seven eight nine", "ten eleven twelve"],
			caretIndex=0,
			numRows=numRows,
			numCols=8,
			live=True,
		)

	def test_bothLinesOfTheRowArriveTogether(self):
		control = self._flow()
		control.source.obj.caretIndex = 2
		control.followCursor()
		self.assertIn((2, 0), self._rows(control))
		self.assertIn((2, 1), self._rows(control))

	def test_andTheCursorIsStillOnTheBand(self):
		control = self._flow()
		control.source.obj.caretIndex = 2
		control.followCursor()
		self.assertIsNotNone(control.cursorCell())

	def test_oneAlreadyWhollyOnTheBandMovesNothing(self):
		control = self._flow()
		control.source.obj.caretIndex = 1
		control.followCursor()
		before = self._rows(control)
		control.source.obj.caretIndex = 0
		control.followCursor()
		self.assertEqual(self._rows(control), before)

	def test_aBlockTallerThanTheBandIsNotDraggedOn(self):
		"""There the far end is not somewhere to go: reaching it would scroll the cursor's
		own row off, and panning is what a block taller than the band is read by."""
		control = controllerOver(
			["short", "a b c d e f g h i j k l m n o p"],
			caretIndex=0,
			numRows=2,
			numCols=8,
			live=True,
		)
		control.source.obj.caretIndex = 1
		control.followCursor()
		self.assertIsNotNone(control.cursorCell())
		self.assertIn((1, 0), self._rows(control))


class TestTheCursorWithinItsBlock(unittest.TestCase):
	"""The block the reader is in is read from where they are; every other from its start.

	Both halves were wrong on hardware. A block was identified by the cursor's own offset, so
	the flow could not recognise the block it was already showing once the reader moved
	within it; and every block was read from its stored position, so the one they were in
	showed a cursor on its first cell and left it there.
	"""

	def test_aBlockKeepsItsIdentityAsTheCaretMovesWithinIt(self):
		flow = controllerOver(["Name", "a note"], caretIndex=1, live=True)
		before = flow.activeBlockId
		flow.source.obj.caretOffset = 4
		flow.followCursor()
		self.assertEqual(flow.activeBlockId, before)

	def test_soAFieldKeepsItsPromptWhileItIsTypedInto(self):
		# A rebuild puts the cursor's block on the top row, which throws away the label the
		# window was placed to show above it.
		flow = controllerOver(
			["Name", "f: Ada", "Town"],
			caretIndex=1,
			numRows=3,
			live=True,
			atObject=control(1),
		)
		top = flow.window.topBlockId()
		flow.source.obj.caretOffset = 5
		flow.followCursor()
		self.assertEqual(flow.window.topBlockId(), top)

	def test_theCursorIsWhereTheCaretIs(self):
		flow = controllerOver(["Name", "a note"], caretIndex=1, live=True)
		flow.source.obj.caretOffset = 3
		flow.refreshActive()
		self.assertEqual(flow.activeRegion().brailleCursorPos, 3)

	def test_andMovesWithIt(self):
		flow = controllerOver(["Name", "a note"], caretIndex=1, live=True)
		flow.source.obj.caretOffset = 5
		flow.refreshActive()
		self.assertEqual(flow.activeRegion().brailleCursorPos, 5)

	def test_aBlockTheCursorHasLeftStillReadsItsOwnLine(self):
		# The reader's cursor belongs to one block. Reading another from it would show that
		# block's line under this block's identity, for as long as it took to notice.
		flow = controllerOver(["Name", "a note"], caretIndex=0, live=True)
		region = flow.activeRegion()
		flow.source.obj.caretIndex = 1
		region.update()
		self.assertEqual(region.rawText, "Name")

	def test_aViewerReadsEveryBlockFromItsOwnStart(self):
		flow = controllerOver(["Name", "a note"], caretIndex=1, live=False)
		flow.source.obj.caretOffset = 3
		flow.refreshActive()
		self.assertIsNone(flow.activeRegion().brailleCursorPos)


class TestWritingReadsTheWholeBandAgain(unittest.TestCase):
	"""A block's position is an offset, and typing moves offsets.

	Insert a line and every position after it has moved; delete a selection and most of what
	the band was holding does not exist any more. Re-rendering only the block the caret is in
	leaves the rest reading at offsets that have gone — on hardware, lines removed by
	select-all and overtype stayed under the reader's fingers until the focus changed.
	"""

	def flow(self, lines, caretIndex=0, numRows=4):
		return controllerOver(
			lines,
			caretIndex=caretIndex,
			numRows=numRows,
			live=True,
			interactive=True,
		)

	def written(self, control):
		""":return: the rows that hold something."""
		return [row.strip() for row in rowTexts(control) if row.strip()]

	def test_linesThatNoLongerExistLeaveTheDisplay(self):
		control = self.flow(["one", "two", "three"])
		self.assertEqual(self.written(control), ["one", "two", "three"])
		# Select all, then overtype: one line where there were three.
		control.source.obj.lines[:] = ["Hello"]
		control.source.obj.caretIndex = 0
		control.source.obj.caretOffset = 5
		control.followCursor()
		self.assertEqual(self.written(control), ["Hello"])

	def test_aNewLineIsNotAlsoShownAbove(self):
		control = self.flow(["one", "two"])
		control.source.obj.lines[:] = ["one", "", "two"]
		control.source.obj.caretIndex = 1
		control.source.obj.caretOffset = 0
		control.followCursor()
		self.assertEqual(self.written(control), ["one", "two"])

	def test_theCaretKeepsTheRowItWasOn(self):
		# The band must not jump under the hands of somebody typing: what was above them
		# stays above them.
		control = self.flow(["one", "two", "three", "four"], caretIndex=0)
		control.source.obj.caretIndex = 2
		control.followCursor()
		before = control.cursorCell() // control.renderer.numCols
		control.source.obj.lines[2] = "three and more"
		control.followCursor()
		self.assertEqual(control.cursorCell() // control.renderer.numCols, before)

	def test_aTransientRefusalHealsOnTheNextKeystroke(self):
		"""The third-line failure two rich editors showed, in both of its shapes.

		Typing a third line, the walk back that restores the band's top row landed on a
		fresh boundary where the editor transiently expands back to the start — so the
		restore either anchored on a block holding the reader's first lines merged into
		one, or failed and pinned the line being typed to the top row, and stayed pinned,
		because the next re-read found the caret's block on the top row and kept it.
		"""
		control = self.flow(["one", "two", "three"], caretIndex=0)
		self.assertEqual(self.written(control), ["one", "two", "three"])
		# The caret moves to the third line while the editor is answering transiently.
		control.source.obj.caretIndex = 2
		control.source.obj.expandsBackAt = 1
		control.followCursor()
		# The walk back is refused rather than trusted: nothing merged, nothing repeated.
		self.assertEqual(self.written(control), ["three"])
		# A keystroke later the editor answers properly, and the band heals.
		control.source.obj.expandsBackAt = None
		control.followCursor()
		self.assertEqual(self.written(control), ["one", "two", "three"])

	def test_theCaretDoesNotStickToTheTopRow(self):
		"""A caret block on the top row reaches back for its context rather than keeping it."""
		control = self.flow(["one", "two", "three"], caretIndex=2)
		self.assertEqual(self.written(control), ["three"])
		control.followCursor()
		self.assertEqual(self.written(control), ["one", "two", "three"])
		# And the caret sits below its context rather than on the top row.
		self.assertEqual(rowTexts(control)[2].strip(), "three")

	def test_theRestoreDoesNotBelieveATransientTop(self):
		"""A merged caret block must not become the window the reader is put back to.

		The probe caught a textarea answering the paragraph at the caret, the moment return
		was pressed, as a unit starting partway back into the document. That block took the
		top row under an identity that was never really a top, and a restore that trusted
		the top row then anchored the band there on every later keystroke — the reader's
		first lines scrolled off an eight row band with four lines in it.
		"""
		lines = ["l1", "l2", "l3", "l4", "l5", "l6", "l7", ""]
		control = controllerOver(lines, caretIndex=0, numRows=8, numCols=8, live=True, interactive=True)
		# The reader has typed down to the seventh line; the band settles with l3 on top.
		control.source.obj.caretIndex = 6
		control.followCursor()
		control.followCursor()
		self.assertEqual(self.written(control)[0], "l3")
		# Return: the caret lands on the empty last line while the editor transiently says
		# the paragraph there starts back at l5, and refuses to expand cleanly behind it.
		control.source.obj.caretIndex = 7
		control.source.obj.expandsBackAt = {7: 4, 3: 0}
		control.followCursor()
		# The next keystroke reads cleanly, and the reader gets their window back — the one
		# they had, not one re-derived from wherever the transient block sat.
		control.source.obj.lines[7] = "l8"
		control.source.obj.expandsBackAt = None
		control.followCursor()
		self.assertEqual(self.written(control), ["l3", "l4", "l5", "l6", "l7", "l8"])

	def test_readingRatherThanWritingStillRefreshesOneBlock(self):
		"""The rebuild is for a document being typed into, and costs a band of reads."""
		control = controllerOver(["one", "two", "three"], numRows=4, live=True)
		control.source.obj.lines[:] = ["Hello"]
		control.followCursor()
		self.assertEqual(rowTexts(control)[1].strip(), "two")


class TestADocumentWithNoBookmarks(unittest.TestCase):
	"""Chromium's editable text has none, and a bare position compares by identity.

	Every reading of the same line was then a different block. Nothing the flow had already
	read could be recognised: the window was rebuilt from the cursor on each keystroke, one
	block at a time, and a rendering made a moment earlier could not be found to refresh —
	which on hardware was a letter appearing only once the next one had been typed.
	"""

	def flow(self, lines, caretIndex=0, numRows=4):
		return controllerOver(
			lines,
			caretIndex=caretIndex,
			numRows=numRows,
			live=True,
			bookmarks=False,
		)

	def test_theSameLineReadTwiceIsTheSameBlock(self):
		control = self.flow(["one", "two", "three"])
		first = control.activeBlockId
		control.followCursor()
		self.assertEqual(control.activeBlockId, first)

	def test_andADifferentLineIsNot(self):
		control = self.flow(["one", "two", "three"])
		first = control.activeBlockId
		control.source.obj.caretIndex = 1
		control.followCursor()
		self.assertNotEqual(control.activeBlockId, first)

	def test_theWindowKnowsWhatItIsAlreadyShowing(self):
		control = self.flow(["one", "two", "three"])
		control.source.obj.caretIndex = 2
		control.followCursor()
		self.assertTrue(control.window.hasBlock(control.activeBlockId))

	def test_soMovingWithinTheBandMovesNothing(self):
		control = self.flow(["one", "two", "three", "four"])
		top = control.window.topBlockId()
		control.source.obj.caretIndex = 2
		control.followCursor()
		self.assertEqual(control.window.topBlockId(), top)

	def test_andTheBandStillFills(self):
		control = self.flow([str(number) for number in range(8)])
		self.assertEqual(rowTexts(control), ["0       ", "1       ", "2       ", "3       "])

	def test_theBlockTheCursorIsInCanStillBeRefreshed(self):
		# `refreshActive` finds the block by its identity. Without one it found nothing, so
		# what the reader had just typed was never laid out again.
		control = self.flow(["one", "two"])
		control.source.obj.lines[0] = "one more"
		control.refreshActive()
		self.assertEqual(rowTexts(control)[0], "one more")


class TestALineThatSwallowedTheNextOne(unittest.TestCase):
	"""A line holds one line, and a rich editor sometimes says otherwise.

	For a moment after a return at the end of the text, Chromium answers "the line at the
	caret" with everything from the start of the document up to it. NVDA's own region shows
	the same, so the band is reading faithfully — but walking on from such a block fetches
	the line it already contains, and the display showed it twice.
	"""

	def test_nothingIsFetchedAfterIt(self):
		control = controllerOver(["one" + chr(10) + "two", "two", "three"], numRows=4)
		self.assertEqual(len(control.window.blocks), 1)
		self.assertEqual(control.window.edges[Edge.AFTER], EdgeState.END)

	def test_soTheRowsBelowItAreBlankRatherThanARepeat(self):
		control = controllerOver(["one" + chr(10) + "two", "two"], numRows=4)
		self.assertEqual(control.window.visibleRows()[1].kind, RowKind.BLANK)

	def test_anOrdinaryLineStillWalksOn(self):
		control = controllerOver(["one", "two", "three"], numRows=4)
		self.assertEqual(len(control.window.blocks), 3)

	def test_aNonBreakingHyphenIsNotABreak(self):
		"""Word writes one as 0x1E, `str.splitlines` splits on it, and a reader's Outlook
		message ended at the word "trade-in": the walk decided the block had swallowed the
		line after it and stopped, three pans in, on a message NVDA read to the end."""
		control = controllerOver(["trade" + chr(0x1E) + "in", "two", "three"], numRows=4)
		self.assertEqual(len(control.window.blocks), 3)

	def test_norAreTheOtherSeparatorsNoDocumentUses(self):
		"""The file and group separators go the same way, and for the same reason: they are
		what `splitlines` splits on rather than what a document ends a line with."""
		for code in (0x1C, 0x1D, 0x1E):
			with self.subTest(code=code):
				control = controllerOver(["a" + chr(code) + "b", "two"], numRows=4)
				self.assertEqual(len(control.window.blocks), 2)

	def test_aManualLineBreakStillCounts(self):
		"""Word puts a vertical tab in for one, and it does end a line."""
		control = controllerOver(["one" + chr(0x0B) + "two", "two"], numRows=4)
		self.assertEqual(control.window.visibleRows()[1].kind, RowKind.BLANK)

	def test_aTerminatorAtTheEndIsNotSwallowing(self):
		"""One line and a terminator is one line, which is what counting them got right and
		a plain search for a break character would not."""
		control = controllerOver(["one" + chr(10), "two", "three"], numRows=4)
		self.assertEqual(len(control.window.blocks), 3)

	def test_aParagraphMayHoldOneWithoutBeingWrong(self):
		"""Asking this of a paragraph would end every reading at its first soft break."""
		control = controllerOver(["one" + chr(10) + "two", "three"], numRows=4, unit="paragraph")
		self.assertEqual(len(control.window.blocks), 2)

	def test_theMarkDoesNotOutliveTheSwallowing(self):
		"""The swallowing is transient, and the mark it left was not.

		A moment after the return the editor answers properly again, but the mark was kept
		by bookmark and the walk forward went on ending at it: on hardware, an edit that
		read as its first line and nothing else until the focus changed. The re-read that
		every keystroke performs while writing must read the document as it now is.
		"""
		control = controllerOver(
			["one" + chr(10) + "two", "two", "three"],
			numRows=4,
			live=True,
			interactive=True,
		)
		self.assertEqual(len(control.window.blocks), 1)
		control.source.obj.lines[0] = "one"
		control.followCursor()
		self.assertEqual(
			[row.strip() for row in rowTexts(control) if row.strip()],
			["one", "two", "three"],
		)


class TestReadingByParagraph(unittest.TestCase):
	"""A paragraph break is the row boundary, so it must not also be a row.

	Reading a comment box by paragraph gave the right text for every unit where reading by
	line sometimes did not — but the walk landed on the break between two paragraphs, so an
	empty unit appeared between each pair and one return read as two rows.
	"""

	def written(self, control):
		return [row.strip() for row in rowTexts(control) if row.strip()]

	def test_theBreakBetweenTwoParagraphsIsNotARow(self):
		control = controllerOver(["one", "", "two"], numRows=4, unit="paragraph", interactive=True)
		self.assertEqual(rowTexts(control)[1].strip(), "two")
		self.assertEqual(self.written(control), ["one", "two"])

	def test_butAnEmptyParagraphTheReaderMadeKeepsItsRow(self):
		# One return leaves one empty unit and it goes; two leave two, and the second is a
		# paragraph they meant.
		control = controllerOver(["one", "", "", "two"], numRows=4, unit="paragraph", interactive=True)
		rows = rowTexts(control)
		self.assertEqual(rows[0].strip(), "one")
		self.assertEqual(rows[1].strip(), "")
		self.assertEqual(rows[2].strip(), "two")

	def test_readingByLineStillKeepsEveryBlankWhileWriting(self):
		"""An empty line is a line. Decision 6 is about those, and is unchanged."""
		control = controllerOver(["one", "", "two"], numRows=4, interactive=True)
		self.assertEqual(rowTexts(control)[1].strip(), "")

	def test_aBreakAtTheEndIsStillWhereTheReaderIs(self):
		control = controllerOver(["one", ""], numRows=4, unit="paragraph", interactive=True)
		self.assertEqual(rowTexts(control)[1].strip(), "")


class TestAWrappedParagraph(unittest.TestCase):
	"""A paragraph is one block however many lines the control's wrapping made of it.

	The source walked by paragraph and the regions rendered by NVDA's own reading unit,
	which reads the reader's read by paragraph setting — a different answer. Every block of
	a paragraph flow then showed only the line at its paragraph's start, the wrapped rest
	was silently gone, and a caret on a wrapped line was ruled outside its own block.
	"""

	def flow(self, caretIndex=0, live=False):
		# One paragraph wrapped over two lines, then a one line paragraph.
		return controllerOver(
			["first ", "half", "next"],
			caretIndex=caretIndex,
			numRows=4,
			numCols=16,
			live=live,
			unit="paragraph",
			paragraphBreaks={1, 2},
		)

	def test_theWholeParagraphIsRendered(self):
		control = self.flow()
		self.assertEqual(rowTexts(control)[0].strip(), "first half")

	def test_everyRegionReadsTheUnitTheSourceWalksBy(self):
		# The stub's own answer is the line, standing in for NVDA's setting saying line.
		control = self.flow()
		for block in control.window.blocks:
			region = control.blocks.get(block.blockId).region
			self.assertEqual(region._getReadingUnit(), "paragraph")

	def test_walkingOnStartsAtTheNextParagraph(self):
		control = self.flow()
		self.assertEqual(
			[row.strip() for row in rowTexts(control) if row.strip()],
			["first half", "next"],
		)

	def test_theCaretOnAWrappedLineIsStillInItsBlock(self):
		control = self.flow(live=True)
		control.source.obj.caretIndex = 1
		control.followCursor()
		self.assertEqual(control.activeRegion().rawText, "first half")


class TestAStepThatGoesNowhere(unittest.TestCase):
	"""Walking forward lands after where it started. The invariant, stated at last.

	A rich editor answers otherwise at a position just past a break: asked to expand the
	unit there it reaches back across it, so the block found starts where the block already
	on the display starts. Reading a comment box showed the same text twice after every
	return, and stepping over the break made it worse rather than better — the blank row it
	removed was replaced by a copy of the line above.
	"""

	def test_aBlockThatDoesNotAdvanceIsNotFetched(self):
		control = controllerOver(["one", "two", "three"], numRows=4, expandsBackAt=1)
		self.assertEqual(len(control.window.blocks), 1)
		self.assertEqual(control.window.edges[Edge.AFTER], EdgeState.END)

	def test_soTheRowBelowIsBlankRatherThanARepeat(self):
		control = controllerOver(["one", "two"], numRows=4, expandsBackAt=1)
		self.assertEqual(rowTexts(control)[0].strip(), "one")
		self.assertEqual(rowTexts(control)[1].strip(), "")

	def test_aDocumentThatWalksProperlyIsUntouched(self):
		control = controllerOver(["one", "two", "three"], numRows=4)
		self.assertEqual(len(control.window.blocks), 3)

	def test_walkingBackHasTheSameRule(self):
		control = controllerOver(["one", "two", "three"], caretIndex=2, numRows=4, expandsBackAt=1)
		self.assertEqual(
			[row.strip() for row in rowTexts(control) if row.strip()],
			["three"],
		)


if __name__ == "__main__":
	unittest.main()


class TestReadingTheBandAgain(unittest.TestCase):
	"""A page whose values change under the reader is re-read in place: the window keeps its
	position, every block keeps its identity, and what changes is what the document now says
	is in them. NVDA refreshes the line the caret is on because that is all it shows."""

	def test_theBandShowsWhatTheDocumentSaysNow(self):
		lines = ["309.48", "second", "third"]
		flow = controllerOver(lines, caretIndex=0, numRows=3)
		lines[1] = "999.99"
		self.assertTrue(flow.rereadContent())
		self.assertIn("999.99", " ".join(flow.describeRows()))

	def test_theReaderKeepsTheirPlace(self):
		lines = ["first", "second", "third", "fourth"]
		flow = controllerOver(lines, caretIndex=1, numRows=2)
		before = flow.window.anchor
		lines[1] = "changed"
		flow.rereadContent()
		self.assertEqual(flow.window.anchor.blockId, before.blockId)

	def test_nothingIsReadWhileTheReaderIsTyping(self):
		"""Everything moves on every keystroke and the block they are in is the one being
		edited; re-reading under them would fight the editor rather than follow it."""
		lines = ["first", "second"]
		flow = controllerOver(lines, caretIndex=0, numRows=2, interactive=True)
		self.assertTrue(flow.source.writing)
		lines[1] = "changed"
		flow.rereadContent()
		self.assertNotIn("changed", " ".join(flow.describeRows()))

	def test_aControlIsLeftAsItWas(self):
		"""A control's block carries what it is as well as what it says — that it is a
		control, and that a gap follows it — and a plain re-read of the position gives back
		neither."""
		lines = ["before", "a field", "after"]
		flow = controllerOver(lines, caretIndex=0, numRows=4, atObject=control(1))
		held = [flow.blocks.get(block.blockId) for block in flow.window.blocks]
		self.assertTrue(
			any(block is not None and block.isControl for block in held),
			"the fixture must put a control on the band",
		)
		flow.rereadContent()
		after = [flow.blocks.get(block.blockId) for block in flow.window.blocks]
		self.assertTrue(any(block is not None and block.isControl for block in after))

	def test_aSourceThatCannotBeAskedIsLeftAlone(self):
		flow = controllerOver(["a", "b"], caretIndex=0, numRows=2)

		class NoReReads:
			writing = False

		flow.source = NoReReads()
		self.assertFalse(flow.rereadContent())


class TestPanningABandOneRowTall(unittest.TestCase):
	"""The reader's report: on a one row band, panning "sticks — it tries to scroll forward,
	but falls back", and the display flickers.

	One row is the case the band was never in until it began following the focus onto a
	single line display, and it is where panning and following the cursor first disagree: a
	block two rows tall has a second row the reader can pan to and the cursor never leaves the
	first."""

	def band(self):
		""":return: a live one row flow over a document whose first line wraps to two rows."""
		return controllerOver(
			["a line long enough to need two rows of this band", "second", "third"],
			caretIndex=0,
			numRows=1,
			numCols=24,
			live=True,
		)

	def test_theBlockIsTallerThanTheBand(self):
		control = self.band()
		self.assertTrue(control.window.blocks[0].moreRows or len(control.window.blocks[0].rows) > 1)

	def test_panningReachesTheSecondRow(self):
		control = self.band()
		before = control.describeRows()
		self.assertTrue(control.panForward())
		self.assertNotEqual(control.describeRows(), before)

	def test_followingTheCursorAfterwardsDoesNotUndoIt(self):
		"""What panning caused must not be what undoes it. Panning a live flow moves the
		reading position, NVDA reports it back, and the band is asked to show the cursor —
		whose row is the one that was panned away from."""
		control = self.band()
		control.panForward()
		panned = control.describeRows()
		control.followCursor()
		self.assertEqual(control.describeRows(), panned)

	def test_movingTheCaretAfterwardsBringsItBack(self):
		"""The rule this suspends is right for a caret move: a caret at the bottom of a long
		paragraph must not be shown by the paragraph's top. It is only wrong when the caret
		did not move at all, so a caret that does move must find the band waiting."""
		control = self.band()
		control.panForward()
		self.assertTrue(control._panIsTheReadersChoice())
		control.source.obj.caretOffset = 30
		control.followCursor()
		self.assertFalse(control._panIsTheReadersChoice())
		control.source.obj.caretOffset = 0
		control.followCursor()
		self.assertIn("row 1 of 2", control.describeRows()[0])

	def test_leavingTheBlockLetsTheOrdinaryRuleBack(self):
		"""Once the block is off the band there is nothing of the reader's to protect, and
		refusing to follow the cursor would be a display stuck showing something else."""
		control = controllerOver(
			["first", "second", "third", "fourth"],
			caretIndex=0,
			numRows=1,
			numCols=24,
			live=True,
		)
		# A flow whose reading position does not follow the window, as a table's does not:
		# there the active block can leave the band while the caret stays where it was.
		control.movesCursor = False
		active = control.activeBlockId
		control.panForward()
		control.panForward()
		self.assertFalse(control.window.isVisible(active))
		self.assertTrue(control.followCursor())

	def test_theHistorySaysWhatHappened(self):
		"""The instrument the diagnosis needed: pans were not recorded, so a history of six
		arrivals could not say whether the pans had happened at all."""
		control = self.band()
		control.panForward()
		self.assertTrue(any("panning" in note for note in control.placements))


class TestPanningADocumentBeingWrittenIn(unittest.TestCase):
	"""The reader's report, from a markdown file open in VSCode: the band "was panning by one
	line instead of whole display and at the point of this log it would not pan forward at
	all. pan back seems better."

	VSCode's editor is a plain editable text with no browse mode behind it, so a flow over it
	counts as written in for as long as the reader is in it — and every display update brings
	a settle pass, which for a written-in document re-reads the whole band from the caret and
	then decides where the band goes. Both of the ways it can decide lose a pan: back to a top
	row from before it, or half a band behind the caret. See `_caretIsWhereThePanLeftIt`."""

	def band(self, numRows=2):
		""":return: a live flow over a document being typed into, two rows of a dozen."""
		return controllerOver(
			[f"line {number}" for number in range(1, 13)],
			caretIndex=0,
			numRows=numRows,
			numCols=12,
			live=True,
			interactive=True,
		)

	def test_theDocumentIsOneBeingWrittenIn(self):
		"""The whole case rests on this: a settle in a written-in document is not a redraw, it
		is a full re-read of the band from the caret."""
		control = self.band()
		self.assertTrue(control._writing())

	def test_panningForwardMovesTheBand(self):
		control = self.band()
		before = control.describeRows()
		self.assertTrue(control.panForward())
		self.assertNotEqual(control.describeRows(), before)

	def test_theSettleAfterwardsLeavesItThere(self):
		"""What the reader felt as "it would not pan forward at all": the pan moved the band,
		and the pass a quarter of a second later moved it back."""
		control = self.band()
		control.panForward()
		panned = control.describeRows()
		control.followCursor()
		self.assertEqual(control.describeRows(), panned)

	def test_panningAgainGoesOnGoingForward(self):
		"""One pan surviving is not enough. Every pan has a settle after it, so the claim has
		to be renewed by each pan rather than spent by the first."""
		control = self.band()
		seen = []
		for _ in range(4):
			control.panForward()
			control.followCursor()
			seen.append(tuple(control.describeRows()))
		self.assertEqual(len(seen), len(set(seen)))

	def test_theBandIsNotReadAgainFromTheCaret(self):
		"""One row, not eight. A reader panning through a long file is reading rather than
		writing, and a band's worth of reads on every display update is what the cost lines
		were full of — a hundred and forty-one operations at twenty-seven milliseconds each,
		on the report that found this. The row they are standing on is read through its own
		region and costs the source no fetch at all."""
		control = self.band()
		control.panForward()
		control.followCursor()
		self.assertEqual(control.source.budget.lastBlocks, 0)

	def test_typingStillCostsTheReadItIsFor(self):
		"""The saving must come from the case where there is nothing to read, never from the
		case the re-read exists for."""
		control = self.band()
		control.panForward()
		control.source.obj.caretOffset = 3
		control.followCursor()
		self.assertGreater(control.source.budget.lastBlocks, 0)

	def test_theHistorySaysWhyTheBandStayed(self):
		control = self.band()
		control.panForward()
		control.followCursor()
		self.assertIn("the caret is where the pan left it", control.placements[-1])

	def test_typingHasTheBandReadAgain(self):
		"""A caret that moved is a reader writing again, and then the re-read is the whole
		point: every position in the document has moved and none of what the band holds can
		be trusted. Panning a live document takes the caret with it, so what says the reader
		typed is the caret moving *within* the line the pan left it on."""
		control = self.band()
		control.panForward()
		before = control.source.budget.operations
		control.source.obj.caretOffset = 3
		control.followCursor()
		self.assertGreater(control.source.budget.operations, before)
		self.assertIn("a keystroke while writing", control.placements[-1])

	def test_movingToAnotherLineBringsTheBandBack(self):
		control = self.band()
		control.panForward()
		control.source.obj.caretIndex = 0
		control.source.obj.caretOffset = 0
		control.followCursor()
		self.assertIn("line 1", " ".join(control.describeRows()))

	def test_typingAndPuttingTheCaretBackIsNotAPanAgain(self):
		"""Forgotten rather than merely not matching, which is the rule `_panIsTheReadersChoice`
		already follows: a claim that re-engaged on the way back would leave the band stuck
		where it was minutes ago."""
		control = self.band()
		control.panForward()
		where = control._caretPosition()
		control.source.obj.caretOffset = 3
		control.followCursor()
		control.source.obj.caretOffset = 0
		self.assertEqual(control._caretPosition(), where)
		self.assertFalse(control._caretIsWhereThePanLeftIt())

	def test_panningTakesTheCaretWithIt(self):
		"""What makes the claim answerable at all, and what the earlier reading of this got
		backwards. A live document flow moves the reader's place as it pans, so the caret
		after a pan is at the top of what they are now feeling — and "has anything been
		typed" is then a question about the caret moving away from *there*."""
		control = self.band()
		before = control._caretPosition()
		control.panForward()
		self.assertNotEqual(control._caretPosition(), before)
		self.assertIn(control._caretPosition(), control._pannedCaret)

	def test_theCaretThePanAskedForCountsToo(self):
		"""Moving the caret is a request to the application and reading it back is a question
		to it, and the two need not agree in the same breath. The position the pan *asked*
		for is taken from the block rather than from the document, which is the half of the
		answer a read cannot give until the application has caught up."""
		control = self.band()
		control.panForward()
		self.assertIn(control._cursorMark(control.window.topBlockId()), control._pannedCaret)

	def test_anApplicationThatHasNotCaughtUpStillHoldsThePan(self):
		"""The reader's second report: panning forward was "inconsistent, from moving the full
		display, to moving just a couple lines". Where the new top row continued the block
		the caret was already in, the cursor was asked to go where it already was and the
		read-back agreed. Where it began a new block the read-back was stale, the claim was
		made against a caret nobody was at, and the next caret event dragged the band half a
		display back."""
		control = self.band()
		stale = control._caretPosition()
		# The application answering with the caret the pan moved away from.
		control.source.caretPosition = lambda: stale
		control.panForward()
		del control.source.caretPosition
		self.assertNotEqual(control._caretPosition(), stale)
		self.assertTrue(control._caretIsWhereThePanLeftIt())

	def test_aFlowThatMovesNoCursorStillClaimsWhereTheCaretIs(self):
		"""The other half of the pair, and the one that carries a flow whose panning moves
		nothing: a table row is a place rather than a selection, and an application that
		declines to move its caret leaves it where it was. Then where it was is the only
		right answer there is."""
		control = self.band()
		control.movesCursor = False
		control.panForward()
		self.assertEqual(control._pannedCaret, (control._caretPosition(),))

	def test_aViewerClaimsTheSame(self):
		"""A viewer moves nothing at all, so the position a block would put the cursor at is
		not a position anything is going to."""
		control = controllerOver(
			[f"line {number}" for number in range(1, 13)],
			caretIndex=0,
			numRows=2,
			numCols=12,
			live=False,
			interactive=True,
		)
		control.panForward()
		self.assertEqual(control._pannedCaret, (control._caretPosition(),))

	def test_theMarkIsCollapsedLikeTheCaretItIsComparedWith(self):
		"""A block's position is normally its collapsed start, and then this costs nothing. It
		is not always: a focused edit inside a page keeps the reader's live selection there —
		see `tracksLiveCursorAcrossUnits` — and a range's bookmark is not a caret's, so the
		two would never match and the pan would never hold in the one place a document is
		most certainly being written in."""
		control = self.band()
		control.panForward()
		top = control.window.topBlockId()
		region = control.regionFor(top)
		region._position.expanded = True
		collapsed = region._position.copy()
		collapsed.collapse()
		self.assertEqual(control._cursorMark(top), collapsed.bookmark)
		# Only half of this can be shown here. `FakeTextInfo.bookmark` already reports an
		# expanded range as though it were collapsed, so the stub cannot produce the case
		# NVDA does: an offsets bookmark is the start *and the end*, and a range's is not its
		# start's. Reverting the collapse therefore fails nothing, which is worth knowing
		# before trusting that it does nothing.

	def test_anEditThatMovesNoCaretIsStillSeen(self):
		"""Forward Delete takes out the character *after* the caret and leaves it exactly where
		it was, so "the reader has not typed" was never a safe reading of "the caret has not
		moved". The row they are standing on is read again rather than trusted."""
		control = self.band()
		control.panForward()
		control.source.obj.lines[control.source.obj.caretIndex] = "line 3 cut"
		control.followCursor()
		self.assertIn("line 3 cut", " ".join(control.describeRows()))

	def test_andTheBandStaysWhereItWasPannedWhileThatHappens(self):
		"""Reading the row is not a reason to move: the block is on the band by construction,
		being the one the pan put the cursor on."""
		control = self.band()
		control.panForward()
		top = control.window.topBlockId()
		control.source.obj.lines[control.source.obj.caretIndex] = "line 3 cut"
		control.followCursor()
		self.assertEqual(control.window.topBlockId(), top)

	def test_theCaretItWasCatchingUpFromStopsCountingOnceItArrives(self):
		"""Both answers are trusted only while the application is between them. Once the caret
		is seen where the pan asked for it, going back to where it came from is the reader
		moving — control+home, or a routing key onto the line they panned away from."""
		control = self.band()
		wasAt = control._caretPosition()
		control.source.caretPosition = lambda: wasAt
		control.panForward()
		del control.source.caretPosition
		self.assertTrue(control._caretIsWhereThePanLeftIt())
		control.source.obj.caretIndex = 0
		control.source.obj.caretOffset = 0
		self.assertEqual(control._caretPosition(), wasAt)
		self.assertFalse(control._caretIsWhereThePanLeftIt())

	def test_typingLandsOnNeitherOfThem(self):
		"""Which is what makes taking both safe: typing moves the caret away from where it was
		*and* away from where the pan put it."""
		control = self.band()
		control.panForward()
		control.source.obj.caretOffset = 2
		self.assertFalse(control._caretIsWhereThePanLeftIt())

	def test_typingAfterAPanDoesNotGoBackToTheWindowBeforeIt(self):
		"""The re-read puts the band back under the row it had, from a claim made the last time
		the reader typed. Once they pan, that claim names a window they left deliberately —
		and kept, it drags them back to it the moment they touch a key, with the line they
		are typing pushed onto the bottom rows and no room left under it."""
		control = controllerOver(
			[f"paragraph {number} runs on for three rows here" for number in range(1, 12)],
			caretIndex=0,
			numRows=6,
			numCols=12,
			live=True,
			interactive=True,
		)
		# Two keystrokes, because the claim on a top row is only made when the top row is not
		# the caret's own block, and the first of these is what moves the caret off it.
		control.source.obj.caretIndex = 1
		control.followCursor()
		control.source.obj.caretOffset = 1
		control.followCursor()
		self.assertIsNotNone(control._writingTop)
		control.panForward()
		control.source.obj.caretOffset = 2
		control.followCursor()
		self.assertNotIn("paragraph 1 ", control.describeRows()[0])

	def test_aGroundedArrivalIsNotAPan(self):
		"""A jump by structure is the reader asking to be set down somewhere else. No claim on
		the window they panned to outlives that."""
		control = self.band()
		control.panForward()
		control.followCursor(ground=True)
		self.assertFalse(control._caretIsWhereThePanLeftIt())

	def test_theCaretMarkComesFromTheDocument(self):
		"""Not from anything the flow rendered, which is what lets the question be asked
		*before* the re-read that would replace the rendering."""
		control = self.band()
		before = control._caretPosition()
		self.assertIsNotNone(before)
		control.source.obj.caretOffset = 4
		self.assertNotEqual(control._caretPosition(), before)

	def test_aSourceThatCannotBeAskedKeepsTheOldBehaviour(self):
		"""A flow whose source has no caret to compare makes no claim, rather than making one
		it cannot check."""
		control = self.band()
		control.source.caretPosition = lambda: None
		control.panForward()
		self.assertFalse(control._caretIsWhereThePanLeftIt())


class TestAnEditUnderACaretThatHasNotMoved(unittest.TestCase):
	"""Forward Delete after a pan, where nothing moves but everything shifts.

	A caret that has not moved since the reader panned keeps the band where they put it, and
	only the row they are standing on is read again — the cheap path a caret settling after a
	pan deserves. But a block's position is an *offset*, and forward Delete changes the
	length of that row without moving the caret off it, so every position after it moves and
	the rest of the band is left reading at offsets that have gone. Panning again then walked
	from a stale position: a line shown twice, or one skipped.

	Told against `OffsetDocument`, whose positions are real offsets. The line-indexed stand-in
	the rest of this file uses cannot express the bug at all, because its positions follow
	their line wherever it goes.
	"""

	COLS = 24
	ROWS = 4

	def flow(self, count=20):
		doc = OffsetDocument([f"line {n} aaaaaaaa" for n in range(count)])
		source = DocumentFlowSource(
			doc,
			regionFactoryFor(CursorManagerRegion(doc), live=True),
			generation=1,
			interactive=True,
		)
		control = FlowController(
			source,
			renderer(self.COLS),
			numRows=self.ROWS,
			live=True,
		)
		control.enterAtCursor()
		return doc, control

	def rows(self, control):
		cells = control.cells()
		return [
			"".join(chr(cell) if cell else " " for cell in cells[i * self.COLS : (i + 1) * self.COLS]).strip()
			for i in range(self.ROWS)
		]

	def written(self, control):
		return [row for row in self.rows(control) if row]

	def pannedTo(self, doc, control, needle):
		"""Pan until the band starts at a chosen line, as a reader reading onward does."""
		for _ in range(10):
			if self.rows(control)[0].startswith(needle):
				return
			if not control.panForward():
				break
		raise AssertionError(f"never reached {needle}: {self.rows(control)}")

	def test_thePanIsWhereItStarts(self):
		doc, control = self.flow()
		self.pannedTo(doc, control, "line 4")
		self.assertEqual(self.written(control)[0], "line 4 aaaaaaaa")

	def test_forwardDeleteLeavesEveryLaterLineReadable(self):
		doc, control = self.flow()
		self.pannedTo(doc, control, "line 4")
		doc.caret = doc.offsetOf("line 4")
		for _ in range(5):
			doc.edit(doc.caret, 1)
		control.followCursor()
		control.panForward()
		truth = doc.lines
		for row in self.written(control):
			self.assertTrue(any(row in line for line in truth), f"{row!r} is in no line of the document")

	def test_andSkipsNothingAndRepeatsNothing(self):
		doc, control = self.flow()
		self.pannedTo(doc, control, "line 4")
		doc.caret = doc.offsetOf("line 4")
		doc.edit(doc.caret, 1)
		control.followCursor()
		control.panForward()
		self.assertEqual(self.written(control), [f"line {n} aaaaaaaa" for n in (8, 9, 10, 11)])

	def test_deletingTheLineBreakMergesAndStillWalksOn(self):
		"""Forward Delete at the end of a line takes the break out and moves no caret."""
		doc, control = self.flow()
		self.pannedTo(doc, control, "line 4")
		doc.caret = doc.offsetOf("line 4")
		doc.edit(doc.lineEndFrom(doc.caret), 1)
		control.followCursor()
		self.assertTrue(self.written(control)[0].startswith("line 4 aaaaaaaaline 5"))
		control.panForward()
		self.assertEqual(self.written(control), [f"line {n} aaaaaaaa" for n in (8, 9, 10, 11)])

	def test_anExternalRewriteOfTheRowIsFollowed(self):
		"""An editor rewriting the line under a caret that never moved."""
		doc, control = self.flow()
		self.pannedTo(doc, control, "line 4")
		doc.caret = doc.offsetOf("line 4")
		doc.edit(doc.caret, doc.lineEndFrom(doc.caret) - doc.caret, "line 4 " + "X" * 60)
		control.followCursor()
		self.assertTrue(self.written(control)[0].startswith("line 4 XXXX"))

	def test_theBandStaysWhereTheReaderPannedIt(self):
		"""The whole point of the cheap path, which the rebuild must not cost them."""
		doc, control = self.flow()
		self.pannedTo(doc, control, "line 4")
		doc.caret = doc.offsetOf("line 4")
		doc.edit(doc.caret, 1)
		control.followCursor()
		# The top row is still the line the reader panned to, one character shorter.
		self.assertEqual(self.written(control)[0], "ine 4 aaaaaaaa")

	def test_aCaretSettlingWithNoEditStillCostsOneBlock(self):
		"""The case the cheap path was written for is untouched."""
		doc, control = self.flow()
		self.pannedTo(doc, control, "line 4")
		before = self.written(control)
		control.followCursor()
		self.assertEqual(self.written(control), before)
