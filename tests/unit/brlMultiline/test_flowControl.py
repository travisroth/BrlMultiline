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
):
	"""Build a controller over a browse mode document of the given lines."""
	interceptor = FakeTreeInterceptor(lines, caretIndex=caretIndex)
	source = DocumentFlowSource(
		interceptor,
		regionFactoryFor(CursorManagerRegion(interceptor), live=live),
		generation=1,
		budget=budget,
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

	def test_aViewerHasNoCursorRow(self):
		control = controllerOver(["Notes", "abc"], caretIndex=1, live=False)
		self.assertIsNone(control.cursorRow())


if __name__ == "__main__":
	unittest.main()
