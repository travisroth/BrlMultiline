# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the row stream and the window that moves over it.

Written against a Monarch sized band: 8 rows of 32 cells, and smaller bands where a
smaller one makes the arithmetic easier to read.

The blocks here are built by hand rather than rendered, which is the point of `flow`
importing nothing from NVDA: a paragraph that occupies five rows is five tuples of cells,
and no braille table is involved in deciding that.
"""

import os
import sys
import unittest

sys.path.insert(
	0,
	os.path.join(os.path.dirname(__file__), "..", "..", "..", "addon", "globalPlugins", "brlMultiline"),
)

from flow import (  # noqa: E402
	BlockId,
	ContentNeeded,
	Edge,
	EdgeState,
	Entry,
	FlowWindow,
	NO_POSITION,
	RenderedBlock,
	RenderKey,
	RowKind,
	assembleCells,
	cellSource,
)

MONARCH_ROWS = 8
MONARCH_COLS = 32

KEY = RenderKey(numCols=MONARCH_COLS)


def blockId(name: str, generation: int = 1, unit: str = "line") -> BlockId:
	""":return: an identity for a block named in a test."""
	return BlockId(generation=generation, bookmark=name, unit=unit)


def block(
	name: str,
	numRows: int = 1,
	numCols: int = 4,
	generation: int = 1,
	**kwargs,
) -> RenderedBlock:
	"""Build a block whose cells say which block and row they came from.

	Cell values are meaningless as braille and deliberately so: what matters is that a
	cell can be traced back to its block, so that a test can say what is on each row.

	:param name: names the block, and seeds its cell values.
	:param numRows: how many rows it occupies.
	:param numCols: how wide its rows are, which may be less than the band.
	:return: the block.
	"""
	seed = ord(name[0])
	rows = tuple(tuple(seed + row for _ in range(numCols)) for row in range(numRows))
	positions = tuple(tuple(row * numCols + col for col in range(numCols)) for row in range(numRows))
	return RenderedBlock(
		blockId=blockId(name, generation=generation),
		rows=rows,
		positions=positions,
		renderKey=KEY,
		rawText=name,
		**kwargs,
	)


def windowWith(numRows: int, blocks, *, before=EdgeState.END, after=EdgeState.END) -> FlowWindow:
	"""Build a window holding the given blocks, anchored at the top of the first.

	:param numRows: the band's height.
	:param blocks: the blocks to cache, in reading order.
	:param before: what is known about the stream above them.
	:param after: what is known about the stream below them.
	:return: the window.
	"""
	window = FlowWindow(numRows)
	for item in blocks:
		window.appendBlock(item)
	window.setEdge(Edge.BEFORE, before)
	window.setEdge(Edge.AFTER, after)
	window.enterAt(blocks[0].blockId)
	return window


def visibleNames(window: FlowWindow) -> list[str | None]:
	""":return: the block name on each row of the window, or the row's kind if it has none."""
	names: list[str | None] = []
	for row in window.visibleRows():
		if row.kind is RowKind.CONTENT and row.blockId is not None:
			names.append(f"{row.blockId.bookmark}{row.rowIndex}")
		else:
			names.append(row.kind.value)
	return names


class TestPacking(unittest.TestCase):
	"""A block starts on a new row, and never starts a new window."""

	def test_headingIsFollowedOnTheNextRowNotTheNextDisplay(self):
		# The case the whole design exists for: a one row heading must not leave the rest
		# of the display blank, and the paragraph after it must not share its row.
		window = windowWith(MONARCH_ROWS, [block("heading"), block("paragraph", numRows=5)])
		self.assertEqual(
			visibleNames(window),
			["heading0", "paragraph0", "paragraph1", "paragraph2", "paragraph3", "paragraph4", "blank", "blank"],
		)

	def test_blocksPackContinuouslyAcrossTheWindowBoundary(self):
		window = windowWith(4, [block("a", numRows=3), block("b", numRows=3)])
		self.assertEqual(visibleNames(window), ["a0", "a1", "a2", "b0"])

	def test_declaredGapsShowAsBlankRows(self):
		window = windowWith(4, [block("field", gapAfter=True), block("next")])
		self.assertEqual(visibleNames(window), ["field0", "gap", "next0", "blank"])

	def test_packingItselfInsertsNoBlankRow(self):
		# Spacing is declared by the block. Packing puts the next block on the next row and
		# nothing more, which is why a short field has to ask for its gap.
		window = windowWith(4, [block("field"), block("next")])
		self.assertEqual(visibleNames(window), ["field0", "next0", "blank", "blank"])


class TestBlankAndPendingRows(unittest.TestCase):
	"""Running out of document and running out of budget must not look the same."""

	def test_endOfStreamShowsBlankRows(self):
		window = windowWith(4, [block("only")], after=EdgeState.END)
		self.assertEqual(visibleNames(window)[1:], ["blank", "blank", "blank"])

	def test_deferredShowsPendingRows(self):
		window = windowWith(4, [block("only")], after=EdgeState.DEFERRED)
		self.assertEqual(visibleNames(window)[1:], ["pending", "pending", "pending"])

	def test_unfetchedContentIsPendingNotBlank(self):
		window = windowWith(4, [block("only")], after=EdgeState.OPEN)
		self.assertEqual(visibleNames(window)[1:], ["pending", "pending", "pending"])


class TestPanning(unittest.TestCase):
	def test_forwardTakesTheRowAfterTheBottomToTheTop(self):
		window = windowWith(4, [block("a", numRows=4), block("b", numRows=4)])
		self.assertTrue(window.panForward())
		self.assertEqual(visibleNames(window), ["b0", "b1", "b2", "b3"])

	def test_forwardStopsAtTheEndOfTheStream(self):
		window = windowWith(4, [block("a", numRows=4)])
		self.assertFalse(window.panForward())
		self.assertEqual(visibleNames(window), ["a0", "a1", "a2", "a3"])

	def test_forwardAsksForContentItDoesNotHave(self):
		window = windowWith(4, [block("a", numRows=4)], after=EdgeState.OPEN)
		with self.assertRaises(ContentNeeded) as caught:
			window.panForward()
		self.assertEqual(caught.exception.edge, Edge.AFTER)

	def test_backStopsAtTheStartOfTheStream(self):
		window = windowWith(4, [block("a", numRows=4)])
		self.assertFalse(window.panBack())

	def test_backAsksForContentItDoesNotHave(self):
		window = windowWith(4, [block("a", numRows=4)], before=EdgeState.OPEN)
		with self.assertRaises(ContentNeeded) as caught:
			window.panBack()
		self.assertEqual(caught.exception.edge, Edge.BEFORE)


class TestReverseSymmetry(unittest.TestCase):
	"""Panning back must undo panning forward. This is the defect the design exists to fix."""

	def test_backAfterForwardReturnsToTheSameRows(self):
		window = windowWith(4, [block("a", numRows=6), block("b", numRows=6)])
		before = visibleNames(window)
		window.panForward()
		window.panBack()
		self.assertEqual(visibleNames(window), before)

	def test_reEnteringAParagraphFromBelowShowsItsTail(self):
		# Pan through a paragraph, cross into the next element, then pan back. The reader
		# expects the end of the paragraph at the bottom of the display, not an aligned
		# window somewhere in its middle.
		paragraph = block("paragraph", numRows=7)
		window = windowWith(4, [paragraph, block("next", numRows=4)])
		window.panForward()
		self.assertEqual(visibleNames(window), ["paragraph4", "paragraph5", "paragraph6", "next0"])
		window.panForward()
		self.assertEqual(visibleNames(window), ["next1", "next2", "next3", "blank"])
		window.panBack()
		# The paragraph's tail is under the reader's fingers again, continuing directly
		# above where they were. Rendering the paragraph afresh from its own start would
		# have shown an aligned window somewhere in its middle instead.
		self.assertEqual(visibleNames(window), ["paragraph4", "paragraph5", "paragraph6", "next0"])

	def test_severalRoundTripsAreStable(self):
		window = windowWith(3, [block(name, numRows=5) for name in "abcd"])
		before = visibleNames(window)
		for _ in range(4):
			window.panForward()
		for _ in range(4):
			window.panBack()
		self.assertEqual(visibleNames(window), before)

	def test_backAtTheStartFillsDownwardRatherThanShowingBlanksAbove(self):
		# A bottom anchor near the start would put the window's top above row zero. Blank
		# rows below content are how the end of a document reads; blank rows above content
		# never are.
		window = windowWith(4, [block("a", numRows=2), block("b", numRows=6)])
		window.panForward()
		window.panBack()
		self.assertEqual(visibleNames(window), ["a0", "a1", "b0", "b1"])


class TestFollowingTheCursor(unittest.TestCase):
	def test_aBlockAlreadyOnTheDisplayDoesNotMoveIt(self):
		# Landing on a heading that is already visible must not jerk the display.
		window = windowWith(MONARCH_ROWS, [block("a", numRows=3), block("heading"), block("b", numRows=3)])
		before = visibleNames(window)
		self.assertFalse(window.ensureVisible(blockId("heading")))
		self.assertEqual(visibleNames(window), before)

	def test_movingForwardPutsTheBlockAtTheBottom(self):
		blocks = [block("a", numRows=4), block("b", numRows=4), block("c", numRows=4)]
		window = windowWith(4, blocks)
		self.assertTrue(window.ensureVisible(blockId("c"), rowIndex=0, forward=True))
		self.assertEqual(visibleNames(window)[-1], "c0")

	def test_movingBackPutsTheBlockAtTheTop(self):
		blocks = [block("a", numRows=4), block("b", numRows=4), block("c", numRows=4)]
		window = windowWith(4, blocks)
		window.panForward()
		window.panForward()
		self.assertTrue(window.ensureVisible(blockId("a"), rowIndex=3, forward=False))
		self.assertEqual(visibleNames(window)[0], "a3")

	def test_isVisibleAnswersForOneRowOfABlock(self):
		window = windowWith(2, [block("long", numRows=6)])
		self.assertTrue(window.isVisible(blockId("long"), rowIndex=1))
		self.assertFalse(window.isVisible(blockId("long"), rowIndex=5))
		self.assertTrue(window.isVisible(blockId("long")))


class TestCursorAfterPanning(unittest.TestCase):
	def test_theCursorGoesToTheTopBlockOfTheNewWindow(self):
		window = windowWith(4, [block("a", numRows=4), block("b"), block("c"), block("d"), block("e")])
		window.panForward()
		self.assertEqual(window.topBlockId(), blockId("b"))

	def test_topBlockSkipsAGapRow(self):
		window = windowWith(4, [block("a", gapAfter=True), block("b", numRows=4)])
		window.panForward()
		self.assertEqual(window.topBlockId(), blockId("b"))

	def test_bottomBlockIsReported(self):
		window = windowWith(4, [block("a", numRows=2), block("b", numRows=2)])
		self.assertEqual(window.bottomBlockId(), blockId("b"))


class TestLineCommands(unittest.TestCase):
	"""Next and previous line move one block, and act on the cursor's block."""

	def test_stepsToTheNeighbouringBlock(self):
		window = windowWith(MONARCH_ROWS, [block("a"), block("b"), block("c")])
		self.assertEqual(window.stepBlock(forward=True), blockId("b"))

	def test_stepsFromTheAnchoredBlockNotTheLastRendered(self):
		# NVDA's own command acts on regions[-1], which in a flow is the block at the
		# bottom of the window rather than the one the cursor is in.
		window = windowWith(MONARCH_ROWS, [block("a"), block("b"), block("c")])
		self.assertNotEqual(window.bottomBlockId(), window.anchor.blockId)
		self.assertEqual(window.stepBlock(forward=True), blockId("b"))

	def test_returnsNothingBeyondTheCachedRun(self):
		window = windowWith(MONARCH_ROWS, [block("a")])
		self.assertIsNone(window.stepBlock(forward=True))
		self.assertIsNone(window.stepBlock(forward=False))


class TestRecovery(unittest.TestCase):
	def test_aReRenderThatShrinksABlockKeepsTheAnchorInside(self):
		window = windowWith(4, [block("a", numRows=6)])
		window.panForward()
		self.assertEqual(window.anchor.rowIndex, 4)
		window.replaceBlock(block("a", numRows=2))
		self.assertEqual(window.anchor.rowIndex, 1)

	def test_aTopAnchorWhoseBlockWentTakesItsSuccessor(self):
		window = windowWith(4, [block("a"), block("b"), block("c")])
		window.enterAt(blockId("b"))
		self.assertTrue(window.replaceBlocks([block("a"), block("c")]))
		self.assertEqual(window.anchor.blockId, blockId("c"))

	def test_aBottomAnchorWhoseBlockWentTakesItsPredecessor(self):
		window = windowWith(4, [block("a"), block("b"), block("c", numRows=4)])
		window.panForward()
		window.panBack()
		self.assertEqual(window.anchor.entry, Entry.BOTTOM)
		window.enterAt(blockId("b"))
		window.anchor = window.anchor.__class__(blockId=blockId("b"), rowIndex=0, entry=Entry.BOTTOM)
		self.assertTrue(window.replaceBlocks([block("a"), block("c", numRows=4)]))
		self.assertEqual(window.anchor.blockId, blockId("a"))

	def test_nothingSurvivingIsReportedRatherThanGuessed(self):
		window = windowWith(4, [block("a"), block("b")])
		self.assertFalse(window.replaceBlocks([block("x"), block("y")]))

	def test_aChangedRenderKeyKeepsTheAnchor(self):
		# A different braille table changes every cell and no identity, so the reader keeps
		# their place.
		window = windowWith(4, [block("a", numRows=2), block("b", numRows=6)])
		window.panForward()
		anchor = window.anchor
		wider = [
			RenderedBlock(
				blockId=item.blockId,
				rows=item.rows,
				positions=item.positions,
				renderKey=RenderKey(numCols=40, table="other"),
				rawText=item.rawText,
			)
			for item in window.blocks
		]
		self.assertTrue(window.replaceBlocks(wider))
		self.assertEqual(window.anchor, anchor)


class TestShortfallAndTrimming(unittest.TestCase):
	def test_shortfallCountsTheRowsAFetchWouldHaveToProduce(self):
		window = windowWith(8, [block("a", numRows=3)], after=EdgeState.OPEN)
		self.assertEqual(window.shortfall(Edge.AFTER), 5)

	def test_noShortfallWhenTheStreamHasEnded(self):
		window = windowWith(8, [block("a", numRows=3)], after=EdgeState.END)
		self.assertEqual(window.shortfall(Edge.AFTER), 0)

	def test_trimmingKeepsTheMarginAndReopensTheEdge(self):
		blocks = [block(name, numRows=2) for name in "abcdefgh"]
		window = windowWith(2, blocks)
		window.panForward()
		window.panForward()
		window.trim(marginRows=2)
		self.assertLess(len(window.blocks), len(blocks))
		self.assertEqual(window.edges[Edge.BEFORE], EdgeState.OPEN)
		self.assertEqual(window.edges[Edge.AFTER], EdgeState.OPEN)

	def test_trimmingKeepsTheWindowItself(self):
		blocks = [block(name, numRows=2) for name in "abcdefgh"]
		window = windowWith(2, blocks)
		window.panForward()
		before = visibleNames(window)
		window.trim(marginRows=2)
		self.assertEqual(visibleNames(window), before)


class TestAssembly(unittest.TestCase):
	"""The rows are padded to the full width, which is what stops blocks sharing a row."""

	def test_everyRowIsPaddedToTheBandWidth(self):
		window = windowWith(2, [block("a", numRows=1, numCols=3), block("b", numRows=1, numCols=3)])
		cells = assembleCells(window, numCols=8)
		self.assertEqual(len(cells), 16)
		self.assertEqual(cells[3:8], [0, 0, 0, 0, 0])
		self.assertEqual(cells[8:11], list(window.blocks[1].rows[0]))

	def test_blankRowsAreBlankCells(self):
		window = windowWith(2, [block("a", numRows=1, numCols=3)])
		cells = assembleCells(window, numCols=8)
		self.assertEqual(cells[8:], [0] * 8)

	def test_aRowWiderThanTheBandIsCut(self):
		window = windowWith(1, [block("a", numRows=1, numCols=12)])
		self.assertEqual(len(assembleCells(window, numCols=8)), 8)


class TestRouting(unittest.TestCase):
	def test_aCellMapsBackToItsBlockAndPosition(self):
		window = windowWith(2, [block("a", numRows=1, numCols=3), block("b", numRows=1, numCols=3)])
		self.assertEqual(cellSource(window, 8, 1), (blockId("a"), 1))
		self.assertEqual(cellSource(window, 8, 9), (blockId("b"), 1))

	def test_routingIntoPaddingDoesNothing(self):
		# NVDA maps trailing blank columns back to the last content cell. Inheriting that
		# here would activate the end of a short field when the reader pressed a key in the
		# space after it.
		window = windowWith(2, [block("a", numRows=1, numCols=3)])
		self.assertIsNone(cellSource(window, 8, 5))

	def test_routingIntoAGapOrABlankDoesNothing(self):
		window = windowWith(3, [block("a", numRows=1, numCols=3, gapAfter=True)])
		self.assertIsNone(cellSource(window, 8, 8))
		self.assertIsNone(cellSource(window, 8, 16))

	def test_aCellExplicitlyMarkedAsPaddingDoesNothing(self):
		padded = RenderedBlock(
			blockId=blockId("a"),
			rows=((1, 2, 0),),
			positions=((0, 1, NO_POSITION),),
			renderKey=KEY,
		)
		window = windowWith(1, [padded])
		self.assertIsNone(cellSource(window, 8, 2))


if __name__ == "__main__":
	unittest.main()
