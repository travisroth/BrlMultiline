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
):
	"""Build a controller over a browse mode document of the given lines."""
	interceptor = FakeTreeInterceptor(
		lines,
		caretIndex=caretIndex,
		bookmarks=bookmarks,
		expandsBackAt=expandsBackAt,
		paragraphBreaks=paragraphBreaks,
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
