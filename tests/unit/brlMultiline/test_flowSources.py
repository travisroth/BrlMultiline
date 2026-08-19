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
	CursorManagerRegion,
	FakeTextInfo,
	FakeTreeInterceptor,
	Region,
	TextInfoRegion,
	installStubs,
)

installStubs()

from brlMultiline.flow import ResultKind  # noqa: E402
from brlMultiline.flowSources import (  # noqa: E402
	DocumentFlowSource,
	FetchBudget,
	FlowCursorManagerRegion,
	FlowTextInfoRegion,
	regionFactoryFor,
)


class FakeClock:
	"""A clock a test drives by hand, so that a budget can be exhausted without waiting."""

	def __init__(self, step=0.0):
		self.now = 0.0
		self.step = step

	def __call__(self):
		self.now += self.step
		return self.now


def sourceOver(lines, caretIndex=0, live=False, interactive=False, budget=None) -> DocumentFlowSource:
	"""Build a source over a browse mode document of the given lines."""
	interceptor = FakeTreeInterceptor(lines, caretIndex=caretIndex)
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
		step = source.blockAfter(result.block.blockId) if forward else source.blockBefore(result.block.blockId)
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


if __name__ == "__main__":
	unittest.main()
