# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for where a flow's blocks come from.

A source is asked for the block at the cursor and for its neighbours, and answers with a
block, the end of the stream, a deferred fetch or an error. Telling those apart is the
point: a budget stop that looked like the end of a document would tell the reader, on the
display and wrongly, that there was no more to read.
"""

import unittest

from ._stubs import (
	BRAILLE_CONFIG,
	CursorManagerRegion,
	FakeNavigatorObject,
	FakeTextInfo,
	FakeTreeInterceptor,
	Region,
	TextInfoRegion,
	installStubs,
	resetConfig,
)

installStubs()

from brlMultiline.flow import ResultKind  # noqa: E402
from brlMultiline.flowSources import (  # noqa: E402
	DEFAULT_MAX_BLOCKS,
	DEFAULT_MAX_SECONDS,
	DocumentFlowSource,
	FetchBudget,
	FlowCursorManagerRegion,
	FlowTextInfoRegion,
	budgetForBand,
	regionFactoryFor,
)


def control(index: int, role: str = "EDITABLETEXT") -> FakeNavigatorObject:
	""":return: a form control the document can place on a given line."""
	return FakeNavigatorObject(name="a field", role=role, documentIndex=index)


def unplaceable() -> FakeNavigatorObject:
	""":return: a control this document knows nothing about."""
	return FakeNavigatorObject(name="somewhere else", role="EDITABLETEXT")


class FakeClock:
	"""A clock a test drives by hand, so that a budget can be exhausted without waiting."""

	def __init__(self, step=0.0):
		self.now = 0.0
		self.step = step

	def __call__(self):
		self.now += self.step
		return self.now


def sourceOver(
	lines,
	caretIndex=0,
	live=False,
	interactive=False,
	budget=None,
	expandsBackAt=None,
	expandsOnAt=None,
) -> DocumentFlowSource:
	"""Build a source over a browse mode document of the given lines."""
	interceptor = FakeTreeInterceptor(
		lines,
		caretIndex=caretIndex,
		expandsBackAt=expandsBackAt,
		expandsOnAt=expandsOnAt,
	)
	factory = regionFactoryFor(CursorManagerRegion(interceptor), live=live)
	return DocumentFlowSource(
		interceptor,
		factory,
		generation=1,
		interactive=interactive,
		budget=budget,
	)


def textsFrom(source: DocumentFlowSource, count: int, forward: bool = True) -> list[str]:
	"""Walk a source from the cursor, returning what each block reads."""
	result = source.blockAtCursor()
	texts = [result.block.region.rawText]
	for _ in range(count):
		step = (
			source.blockAfter(result.block.blockId) if forward else source.blockBefore(result.block.blockId)
		)
		if step.block is None:
			break
		result = step
		texts.append(result.block.region.rawText)
	return texts


class TestReadingBlocks(unittest.TestCase):
	def test_theBlockAtTheCursorIsTheCursorsLine(self):
		source = sourceOver(["first", "second", "third"], caretIndex=1)
		self.assertEqual(source.blockAtCursor().block.region.rawText, "second")

	def test_walkingForwardReadsTheDocumentInOrder(self):
		source = sourceOver(["a", "b", "c", "d"], caretIndex=0)
		self.assertEqual(textsFrom(source, 3), ["a", "b", "c", "d"])

	def test_walkingBackReadsItInReverse(self):
		source = sourceOver(["a", "b", "c", "d"], caretIndex=3)
		self.assertEqual(textsFrom(source, 3, forward=False), ["d", "c", "b", "a"])

	def test_eachBlockIsBoundToItsOwnPosition(self):
		# The failure this prevents: every CursorManagerRegion over one document reads the
		# same live selection, so a run of stock regions all render the cursor's block.
		source = sourceOver(["first", "second"], caretIndex=0)
		first = source.blockAtCursor().block
		second = source.blockAfter(first.blockId).block
		first.region.update()
		second.region.update()
		self.assertEqual(first.region.rawText, "first")
		self.assertEqual(second.region.rawText, "second")

	def test_blocksHaveDistinctIdentities(self):
		source = sourceOver(["a", "b"], caretIndex=0)
		first = source.blockAtCursor().block
		second = source.blockAfter(first.blockId).block
		self.assertNotEqual(first.blockId, second.blockId)
		self.assertEqual(first.blockId.generation, 1)

	def test_theSameBlockFetchedTwiceHasTheSameIdentity(self):
		source = sourceOver(["a", "b"], caretIndex=0)
		first = source.blockAtCursor().block
		second = source.blockAfter(first.blockId).block
		back = source.blockBefore(second.blockId).block
		self.assertEqual(back.blockId, first.blockId)


class TestReadingABlockAgain(unittest.TestCase):
	"""A page whose values change under the reader has to be re-read without the band moving,
	and the band does not move because every block keeps the identity it already had.

	The reader's own finding on a watchlist: read as a table the prices moved and read as
	ordinary browse mode they sat still. NVDA refreshes the caret's line because the caret's
	line is all it is showing; a band showing eight is showing seven that nobody refreshes.
	"""

	def test_aBlockComesBackWithTheSameIdentity(self):
		source = sourceOver(["first", "second", "third"], caretIndex=1)
		block = source.blockAtCursor().block
		again = source.blockAt(block.blockId).block
		self.assertEqual(again.blockId, block.blockId)

	def test_itComesBackWithWhatTheLineSaysNow(self):
		lines = ["309.48", "second"]
		source = sourceOver(lines, caretIndex=0)
		block = source.blockAtCursor().block
		lines[0] = "309.46"
		self.assertEqual(source.blockAt(block.blockId).block.region.rawText, "309.46")

	def test_aBlockTheBandNeverReadIsNotKnown(self):
		"""Only the positions this source handed out can be read again; there is nothing to
		expand at a bookmark it has never seen."""
		source = sourceOver(["a", "b"], caretIndex=0)
		block = source.blockAtCursor().block
		source.forget()
		self.assertEqual(source.blockAt(block.blockId).kind, ResultKind.ERROR)

	def test_aBlockThatNoLongerBeginsWhereItDidIsRefused(self):
		"""The bookmark is an offset, so text growing earlier in the document moves every
		block after it. Expanding at the old position would hand back a neighbour under this
		block's name — a line drawn twice on the band, which is worse than one out of date."""
		lines = ["first", "second", "third"]
		source = sourceOver(lines, caretIndex=1)
		block = source.blockAtCursor().block
		self.assertEqual(block.region.rawText, "second")
		lines[0] = "a much longer first line"
		self.assertEqual(source.blockAt(block.blockId).kind, ResultKind.ERROR)

	def test_aChangeWithinTheBlockDoesNotMoveIt(self):
		"""Which is the ordinary case this exists for: a price going from 309.48 to 309.46
		leaves every position alone."""
		lines = ["first", "309.48", "third"]
		source = sourceOver(lines, caretIndex=1)
		block = source.blockAtCursor().block
		lines[1] = "309.46"
		self.assertEqual(source.blockAt(block.blockId).block.region.rawText, "309.46")

	def test_theBlockAfterOneIsStillTheOneAfterIt(self):
		"""Re-reading must not disturb the walk: the cached positions are what the window
		pans by, and a re-read that moved them would move the band."""
		lines = ["first", "second", "third"]
		source = sourceOver(lines, caretIndex=0)
		first = source.blockAtCursor().block
		source.blockAt(first.blockId)
		self.assertEqual(source.blockAfter(first.blockId).block.region.rawText, "second")


class TestEndOfStream(unittest.TestCase):
	def test_theEndOfTheDocumentIsReportedAsSuch(self):
		source = sourceOver(["only"], caretIndex=0)
		block = source.blockAtCursor().block
		self.assertEqual(source.blockAfter(block.blockId).kind, ResultKind.END_OF_STREAM)

	def test_theStartOfTheDocumentIsReportedAsSuch(self):
		source = sourceOver(["only"], caretIndex=0)
		block = source.blockAtCursor().block
		self.assertEqual(source.blockBefore(block.blockId).kind, ResultKind.END_OF_STREAM)

	def test_aPartialMoveIsNoMove(self):
		# TextInfo.move stops at the edge of the document and says how far it got. A block
		# built from a short move would repeat the last line for every block below it.
		source = sourceOver(["a", "b"], caretIndex=1)
		block = source.blockAtCursor().block
		self.assertEqual(source.blockAfter(block.blockId).kind, ResultKind.END_OF_STREAM)


class TestWhyAWalkStopped(unittest.TestCase):
	"""'There is no more this way' is the one answer a reader disputes: they can see the
	document goes on and NVDA pans through it. A walk has several ways of concluding it has
	finished, they are different faults with different fixes, and the report used to say only
	"(end of content)" — which is the claim rather than the reason for it."""

	def test_theEndOfTheDocumentSaysSo(self):
		source = sourceOver(["only"], caretIndex=0)
		result = source.blockAfter(source.blockAtCursor().block.blockId)
		self.assertEqual(result.kind, ResultKind.END_OF_STREAM)
		self.assertIn("move on", result.message)

	def test_theStartOfItSaysSoTheOtherWay(self):
		source = sourceOver(["only"], caretIndex=0)
		result = source.blockBefore(source.blockAtCursor().block.blockId)
		self.assertIn("move back", result.message)

	def test_aUnitThatReachesBackSaysThat(self):
		"""What a rich editor does at a position just past a break, and what an Outlook
		message appeared to do three pans down."""
		source = sourceOver(["first", "second", "third"], caretIndex=0, expandsBackAt=1)
		result = source.blockAfter(source.blockAtCursor().block.blockId)
		self.assertEqual(result.kind, ResultKind.END_OF_STREAM)
		self.assertIn("went nowhere", result.message)


class TestBlankLines(unittest.TestCase):
	"""Collapsed when reading, kept when writing. The test is interaction, not role."""

	def test_readingCollapsesARunOfBlankLines(self):
		source = sourceOver(["a", "", "", "", "b"], caretIndex=0, interactive=False)
		self.assertEqual(textsFrom(source, 2), ["a", "", "b"])

	def test_theCollapsedBlockSaysHowManyItStandsFor(self):
		source = sourceOver(["a", "", "", "", "b"], caretIndex=0, interactive=False)
		first = source.blockAtCursor().block
		blank = source.blockAfter(first.blockId).block
		self.assertTrue(blank.isBlank)
		self.assertEqual(blank.collapsed, 3)

	def test_writingKeepsEveryBlankLine(self):
		# Inside an edit field the blank lines are the document: they are paragraph breaks
		# the reader put there and navigates by.
		source = sourceOver(["a", "", "", "", "b"], caretIndex=0, interactive=True)
		self.assertEqual(textsFrom(source, 4), ["a", "", "", "", "b"])

	def test_aDocumentEndingInBlanksStillEnds(self):
		source = sourceOver(["a", "", ""], caretIndex=0, interactive=False)
		first = source.blockAtCursor().block
		blank = source.blockAfter(first.blockId)
		self.assertEqual(blank.kind, ResultKind.BLOCK)
		self.assertEqual(source.blockAfter(blank.block.blockId).kind, ResultKind.END_OF_STREAM)

	def test_aBlankLineOutsideATableStillOwnsItsRow(self):
		"""Where an author put it there. The band is showing the shape of what is written."""
		source = sourceOver(["a", "", "b"], caretIndex=0)
		self.assertEqual(textsFrom(source, 2), ["a", "", "b"])

	def test_walkingBackOverBlanksKeepsTheRunsFirstMember(self):
		source = sourceOver(["a", "", "", "b"], caretIndex=3, interactive=False)
		block = source.blockAtCursor().block
		blank = source.blockBefore(block.blockId).block
		before = source.blockAfter(blank.blockId)
		# The row the run owns is its first member, so reading forward from it reaches the
		# rest of the run rather than jumping past it.
		self.assertEqual(before.kind, ResultKind.BLOCK)


class TestBudget(unittest.TestCase):
	def test_aLongRunOfBlanksDefersRatherThanWalkingForever(self):
		budget = FetchBudget(maxBlocks=3, maxSeconds=10.0, clock=FakeClock())
		source = sourceOver(["a"] + [""] * 50 + ["b"], caretIndex=0, budget=budget)
		first = source.blockAtCursor().block
		self.assertEqual(source.blockAfter(first.blockId).kind, ResultKind.DEFERRED)

	def test_aDeferredFetchCarriesOnWhereItStopped(self):
		budget = FetchBudget(maxBlocks=3, maxSeconds=10.0, clock=FakeClock())
		source = sourceOver(["a"] + [""] * 6 + ["b"], caretIndex=0, budget=budget)
		first = source.blockAtCursor().block
		self.assertEqual(source.blockAfter(first.blockId).kind, ResultKind.DEFERRED)
		# The same request again resumes rather than starting the walk over, so a second pan
		# makes progress instead of deferring at the same place forever.
		second = source.blockAfter(first.blockId)
		for _ in range(6):
			if second.kind is ResultKind.BLOCK:
				break
			second = source.blockAfter(first.blockId)
		# What the resumed walk finishes is the run of blanks, counted in full despite
		# having been walked across several fetches.
		self.assertEqual(second.kind, ResultKind.BLOCK)
		self.assertTrue(second.block.isBlank)
		self.assertEqual(second.block.collapsed, 6)
		# And stepping out of the run reaches the content after it, not back into it.
		self.assertEqual(source.blockAfter(second.block.blockId).block.region.rawText, "b")

	def test_timeAloneCanExhaustTheBudget(self):
		budget = FetchBudget(maxBlocks=100, maxSeconds=0.01, clock=FakeClock(step=0.005))
		source = sourceOver(["a"] + [""] * 50 + ["b"], caretIndex=0, budget=budget)
		first = source.blockAtCursor().block
		self.assertEqual(source.blockAfter(first.blockId).kind, ResultKind.DEFERRED)

	def test_theSlowestBlockIsRecorded(self):
		budget = FetchBudget(maxBlocks=4, maxSeconds=10.0, clock=FakeClock(step=0.002))
		source = sourceOver(["a"] + [""] * 20, caretIndex=0, budget=budget)
		first = source.blockAtCursor().block
		source.blockAfter(first.blockId)
		self.assertGreater(budget.slowest, 0)


class TestABlockThatRunsIntoItsNeighbour(unittest.TestCase):
	"""Two rows are two blocks, and two blocks must be two different pieces of the document.

	Where the ranges behind them overlap, whatever they share is drawn twice and the reader
	reads a line they have already read. A rich editor being typed into produces exactly that:
	asked to expand the unit at a landing it reaches *forward* over the boundary, so the block
	above ends inside the line being typed.

	The reach backwards was already refused — see `_startsWhereItLanded` — and this is its
	twin. `_advanced` cannot catch it, because it compares where two blocks begin and a block
	can begin earlier and still reach over its neighbour; `_hasSwallowedWhatFollows` cannot,
	because it reads the text of a line for a break and says nothing about a paragraph or
	about the walk backwards.
	"""

	LINES = ["one", "two", "three", "four"]

	def test_aStepBackOntoAUnitThatReachesIntoTheCaretsLineIsRefused(self):
		source = sourceOver(self.LINES, caretIndex=3, interactive=True, expandsOnAt={2: 3})
		here = source.blockAtCursor().block
		found = source.blockBefore(here.blockId)
		self.assertEqual(found.kind, ResultKind.END_OF_STREAM)

	def test_andTheOrdinaryStepBackIsUntouched(self):
		"""The guard costs nothing where nothing is wrong, which is nearly always."""
		source = sourceOver(self.LINES, caretIndex=3, interactive=True)
		here = source.blockAtCursor().block
		self.assertEqual(source.blockBefore(here.blockId).block.region.rawText, "three")

	def test_aStepOnFromAUnitThatAlreadyHoldsWhatFollowsIsRefused(self):
		"""The same rule forwards, where a line break in the text is not the evidence: a
		paragraph may hold breaks of its own, and the extent is what says the block below
		would repeat what this one already draws."""
		source = sourceOver(self.LINES, caretIndex=0, interactive=True, expandsOnAt={0: 1})
		here = source.blockAtCursor().block
		self.assertEqual(source.blockAfter(here.blockId).kind, ResultKind.END_OF_STREAM)

	def test_butADocumentBeingReadRatherThanWrittenKeepsItsContent(self):
		"""Asked only while writing, where the transient lives. A document merely being read
		holds still, and content is too expensive to refuse over a quirk that is not
		happening — the reason `_startsWhereItLanded` gives, and the same one here."""
		source = sourceOver(self.LINES, caretIndex=3, expandsOnAt={2: 3})
		here = source.blockAtCursor().block
		self.assertEqual(source.blockBefore(here.blockId).kind, ResultKind.BLOCK)


class TestBookmarksAreNotHashable(unittest.TestCase):
	"""The constraint that broke the first hardware run.

	NVDA's bookmark for a virtual buffer is `textInfos.offsets.Offsets`, a plain dataclass,
	so it defines `__eq__` and Python sets its `__hash__` to None. Anything keying a
	dictionary by a bookmark, or by a `BlockId` holding one, works against a tuple in a test
	and raises `TypeError` against every real document.
	"""

	def test_theHarnessProducesAnUnhashableBookmark(self):
		source = sourceOver(["a"], caretIndex=0)
		block = source.blockAtCursor().block
		with self.assertRaises(TypeError):
			{block.blockId.bookmark: 1}
		with self.assertRaises(TypeError):
			{block.blockId: 1}

	def test_aDocumentWithUnhashableBookmarksStillReads(self):
		source = sourceOver(["a", "b", "c"], caretIndex=0)
		self.assertEqual(textsFrom(source, 2), ["a", "b", "c"])


class TestFailures(unittest.TestCase):
	def test_anUnknownBlockIsAnErrorNotACrash(self):
		source = sourceOver(["a"], caretIndex=0)
		block = source.blockAtCursor().block
		source.forget()
		self.assertEqual(source.blockAfter(block.blockId).kind, ResultKind.ERROR)

	def test_aDocumentThatCannotBeReadIsAnError(self):
		class Broken:
			def makeTextInfo(self, position):
				raise RuntimeError("gone")

		source = DocumentFlowSource(Broken(), lambda obj, info: None, generation=1)
		self.assertEqual(source.blockAtCursor().kind, ResultKind.ERROR)


class TestRegionFlavours(unittest.TestCase):
	def test_theClassIsTakenFromNVDAsOwnChoice(self):
		interceptor = FakeTreeInterceptor(["a"])
		factory = regionFactoryFor(CursorManagerRegion(interceptor), live=False)
		self.assertIsInstance(factory(interceptor, FakeTextInfo(["a"], 0)), FlowCursorManagerRegion)
		plain = regionFactoryFor(TextInfoRegion(interceptor), live=False)
		self.assertIsInstance(plain(interceptor, FakeTextInfo(["a"], 0)), FlowTextInfoRegion)

	def test_aRegionWithNoTextCannotBeFlowed(self):
		with self.assertRaises(TypeError):
			regionFactoryFor(Region("a button"), live=False)

	def test_aViewerKeepsItsMovesToItself(self):
		source = sourceOver(["a", "b", "c"], caretIndex=0, live=False)
		block = source.blockAtCursor().block
		block.region.nextLine()
		self.assertEqual(source.obj.caretIndex, 0)

	def test_aLiveBlockMovesTheBrowseModeCursor(self):
		# Which is what keeps speech and braille together, and leaves routing able to
		# activate a link.
		source = sourceOver(["a", "b", "c"], caretIndex=0, live=True)
		block = source.blockAtCursor().block
		block.region.nextLine()
		self.assertEqual(source.obj.caretIndex, 1)

	def test_onlyTheActiveBlockOfALiveFlowShowsACursor(self):
		source = sourceOver(["a", "b"], caretIndex=0, live=True)
		block = source.blockAtCursor().block
		block.region.cursorPos = 0
		block.region.brailleCursorPos = 0
		block.region.update()
		self.assertIsNone(block.region.brailleCursorPos)
		block.region.isActive = True
		block.region.cursorPos = 0
		block.region.brailleCursorPos = 0
		block.region.update()
		self.assertEqual(block.region.brailleCursorPos, 0)

	def test_aViewerShowsNoCursorEvenWhenActive(self):
		# One cursor on the display, and it belongs to the focus. Being the active block
		# says which block commands act on, not that a cursor is drawn.
		source = sourceOver(["a", "b"], caretIndex=0, live=False)
		block = source.blockAtCursor().block
		block.region.isActive = True
		block.region.cursorPos = 0
		block.region.brailleCursorPos = 0
		block.region.update()
		self.assertIsNone(block.region.brailleCursorPos)

	def test_hidePreviousRegionsIsNeverLeftSet(self):
		# NVDA's buffer shows the last region alone when it is set, which for a flow would
		# blank everything above the bottom block.
		source = sourceOver(["a", "b"], caretIndex=0)
		block = source.blockAtCursor().block
		block.region.hidePreviousRegions = True
		block.region.update()
		self.assertFalse(block.region.hidePreviousRegions)


class TestTheWordAtTheCursorIsExpandedOnceOnly(unittest.TestCase):
	"""**NVDA's "expand to computer braille for the word at the cursor", honoured too well.**

	It is applied in `braille.regions.base.Region.update`, which asks liblouis for computer
	braille at the cursor whenever the setting is on and the region has one. Every block of a
	flow reads a fixed position and answers that position when NVDA asks where the selection
	is, so the cursor fell inside all of them — and a band of eight rows came out with eight
	first words written out uncontracted, on a page the reader was only reading.

	The cursor was being cleared, but after `update`, which is one liblouis call too late.
	Reported from a Favorites page where every line began in computer braille.
	"""

	def setUp(self):
		resetConfig()
		self.addCleanup(resetConfig)
		BRAILLE_CONFIG["expandAtCursor"] = True
		self.addCleanup(BRAILLE_CONFIG.__setitem__, "expandAtCursor", True)

	def _blocks(self, live=True):
		"""The first three blocks of a document, as the band holds them."""
		source = sourceOver(["first line", "second line", "third line"], caretIndex=0, live=live)
		first = source.blockAtCursor().block
		second = source.blockAfter(first.blockId).block
		third = source.blockAfter(second.blockId).block
		return [block.region for block in (first, second, third)]

	def test_aBlockTheReaderIsNotInIsTranslatedWithNoCursorAtAll(self):
		for region in self._blocks():
			region.update()
			self.assertFalse(
				region.expandedAtCursor,
				f"{region.rawText!r} was expanded to computer braille",
			)

	def test_butTheBlockTheyAreInStillIs(self):
		"""The setting does what it says on the one row it is about."""
		regions = self._blocks()
		regions[1].isActive = True
		regions[1].update()
		self.assertTrue(regions[1].expandedAtCursor)

	def test_andNothingIsExpandedWhenTheSettingIsOff(self):
		BRAILLE_CONFIG["expandAtCursor"] = False
		regions = self._blocks()
		regions[0].isActive = True
		regions[0].update()
		self.assertFalse(regions[0].expandedAtCursor)

	def test_andAViewerBandExpandsNothingEvenOnItsActiveBlock(self):
		"""There is one cursor on the display and it belongs to the focus."""
		regions = self._blocks(live=False)
		regions[0].isActive = True
		regions[0].update()
		self.assertFalse(regions[0].expandedAtCursor)

	def test_theCursorItselfIsRefusedRatherThanClearedAfterwards(self):
		"""Which is the whole of the fix: cleared afterwards, the cells are already made."""
		region = self._blocks()[0]
		region.cursorPos = 0
		self.assertIsNone(region.cursorPos)
		region.isActive = True
		self.assertEqual(region.cursorPos, 0)


class TestControls(unittest.TestCase):
	"""A control is read at its own place in the document, not at the cursor.

	Which blocks are controls used to be read out of every block's own field commands. That
	was wrong about cost and wrong about focus mode, and `flowForms` records why. What is
	left here is the wiring: the source is told what the reader arrived at, and reads the
	document there.
	"""

	def test_aControlIsReadAtItsOwnPlace(self):
		# Tabbing into an edit field puts the cursor inside it, where the line is the value
		# being edited. The block wanted is the one browse mode shows for the control.
		source = sourceOver(["Your name", "Name: edit", "Your town"], caretIndex=2)
		field = control(1)
		block = source.blockAtCursor(field).block
		self.assertEqual(block.region.rawText, "Name: edit")

	def test_soPlacedItIsAControl(self):
		source = sourceOver(["Your name", "Name: edit"], caretIndex=0)
		block = source.blockAtCursor(control(1)).block
		self.assertTrue(block.isControl)

	def test_andIsSeparatedFromWhatFollowsIt(self):
		"""Rule 5: spacing is declared by the block, never produced by packing."""
		source = sourceOver(["Your name", "Name: edit"], caretIndex=0)
		block = source.blockAtCursor(control(1)).block
		self.assertTrue(block.gapAfter)

	def test_readingFromTheCursorAsksForNoSpacing(self):
		source = sourceOver(["Your name", "Name: edit"], caretIndex=1)
		block = source.blockAtCursor().block
		self.assertFalse(block.isControl)
		self.assertFalse(block.gapAfter)

	def test_anObjectThisDocumentCannotPlaceFallsBackToTheCursor(self):
		"""Which is what a control in some other document, or none at all, amounts to."""
		source = sourceOver(["Your name", "Name: edit"], caretIndex=0)
		block = source.blockAtCursor(unplaceable()).block
		self.assertEqual(block.region.rawText, "Your name")

	def test_blocksWalkedToAreNotProbedAtAll(self):
		# The question only matters at the block the reader arrived at. Asking it of every
		# block was a tax on all reading, and on an eight row band it spent the budget.
		source = sourceOver(["Your name", "Name: edit", "Your town"], caretIndex=0)
		first = source.blockAtCursor().block
		self.assertFalse(source.blockAfter(first.blockId).block.isControl)


class TestWhatReadingCost(unittest.TestCase):
	"""The measurement the budget is meant to be checked against.

	A budget nobody compares with real reading is a number somebody guessed, and a guessed
	number here has twice turned out to be the fault rather than the safety limit. What a
	hardware run should be able to report is the slowest block, the slowest operation, and
	how often the allowance ran out.
	"""

	def budget(self, step=0.001, **kwargs):
		return FetchBudget(clock=FakeClock(step=step), **kwargs)

	def test_nothingIsCountedBeforeAnythingHappens(self):
		budget = self.budget()
		self.assertEqual(budget.operations, 0)
		self.assertEqual(budget.stops, 0)

	def test_anOperationIsCountedWhenItFinishes(self):
		budget = self.budget()
		budget.start()
		budget.finish()
		self.assertEqual(budget.operations, 1)

	def test_whatItCostIsKept(self):
		budget = self.budget(step=0.01)
		budget.start()
		budget.spend()
		budget.finish()
		self.assertGreater(budget.lastSeconds, 0)
		self.assertEqual(budget.lastBlocks, 1)

	def test_theWorstIsKeptAcrossOperations(self):
		budget = self.budget(step=0.01)
		budget.start()
		budget.spend()
		budget.spend()
		budget.finish()
		worst = budget.worstBlocks
		budget.start()
		budget.finish()
		self.assertEqual(budget.worstBlocks, worst)
		self.assertEqual(budget.lastBlocks, 0)

	def test_aRefusedFetchIsCounted(self):
		# The number that says whether the budget is sized right: it means the reader was
		# shown less than the band could hold.
		budget = self.budget(maxBlocks=1)
		budget.start()
		budget.spend()
		self.assertTrue(budget.refuseIfExhausted())
		budget.finish()
		self.assertEqual(budget.stops, 1)
		self.assertEqual(budget.stopsBy["blocks"], 1)

	def test_spendingTheLastBlockIsNotAStop(self):
		# An operation whose last permitted block filled the last row of the band wanted
		# nothing more and stopped nothing. Counting it would have the reader chasing an
		# allowance that already fits.
		budget = self.budget(maxBlocks=1)
		budget.start()
		budget.spend()
		budget.finish()
		self.assertEqual(budget.stops, 0)

	def test_runningOutOfTimeIsCountedToo(self):
		# Missed entirely before, and it is the reason a larger allowance would not help.
		budget = self.budget(maxBlocks=99, maxSeconds=0.005, step=0.01)
		budget.start()
		budget.spend()
		self.assertTrue(budget.refuseIfExhausted())
		budget.finish()
		self.assertEqual(budget.stops, 1)
		self.assertEqual(budget.stopsBy["time"], 1)

	def test_oneOperationCountsOnceHoweverOftenItIsRefused(self):
		budget = self.budget(maxBlocks=1)
		budget.start()
		budget.spend()
		budget.refuseIfExhausted()
		budget.refuseIfExhausted()
		budget.finish()
		self.assertEqual(budget.stops, 1)

	def test_finishingWithRoomToSpareIsNot(self):
		budget = self.budget(maxBlocks=4)
		budget.start()
		budget.spend()
		self.assertFalse(budget.refuseIfExhausted())
		budget.finish()
		self.assertEqual(budget.stops, 0)

	def test_theAccountReadsAsWords(self):
		budget = self.budget()
		budget.start()
		budget.spend()
		budget.finish()
		account = " ".join(budget.describe())
		self.assertIn("operations: 1", account)
		self.assertIn("slowest", account)


class TestTheBudgetFitsTheBand(unittest.TestCase):
	"""The budget stops a heavy page being walked, not the display being filled."""

	def test_aTallBandGetsMoreThanItNeedsToFill(self):
		self.assertGreater(budgetForBand(8).maxBlocks, 8)

	def test_aShortBandStillGetsTheFloor(self):
		self.assertGreaterEqual(budgetForBand(1).maxBlocks, DEFAULT_MAX_BLOCKS)

	def test_itGrowsWithTheBand(self):
		self.assertGreater(budgetForBand(16).maxBlocks, budgetForBand(8).maxBlocks)

	def test_itFundsAWritingReRead(self):
		"""A caret update while writing costs three passes over the band, not one.

		Enter at the caret, walk back to the row the reader was on, fill. Twice the band
		met that on hardware as rows of the marker meaning there is more we have not read.
		"""
		self.assertGreaterEqual(budgetForBand(8).maxBlocks, 8 * 3)
		self.assertGreater(budgetForBand(8).maxSeconds, DEFAULT_MAX_SECONDS)


if __name__ == "__main__":
	unittest.main()
