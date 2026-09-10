# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for drawing a block's depth as cells.

One cell per character, which is what the harness's regions produce, so a row of cells
reads back as the string it came from and a test can say what it expects to feel. Fill
mode throughout: the row cutting is then the add-on's own arithmetic rather than NVDA's
word wrapping, which is a hardware question.

The rule these exist to pin down is the one that is easy to leave out and impossible to
read without: a wrapped row is indented past where a child would start, so that a long item
does not read as its own parent. See `flowIndent.CONTINUATION_EXTRA`.
"""

import unittest

from ._stubs import Region, installStubs

installStubs()

from brlMultiline.flow import NO_POSITION, BlockId, SourceBlock  # noqa: E402
from brlMultiline.flowIndent import DOTS_78, LEVEL_CELL, TWO_SPACES, planFor  # noqa: E402
from brlMultiline.flowRender import FlowRenderer  # noqa: E402


class FakeHandler:
	"""Everything the layout buffer reads off the real braille handler."""

	def __init__(self):
		self.buffer = None


def block(text: str, depth=None, name="b") -> SourceBlock:
	""":return: a block of plain text at a given depth."""
	return SourceBlock(
		blockId=BlockId(generation=1, bookmark=name, unit="line"),
		region=Region(text),
		depth=depth,
	)


def renderer(numCols=16, plan=None) -> FlowRenderer:
	return FlowRenderer(
		FakeHandler(),
		numCols=numCols,
		fillRows=True,
		indentPlan=plan if plan is not None else planFor([], numCols),
	)


def textOf(row) -> str:
	""":return: a rendered row read back as characters, with blanks as spaces."""
	return "".join(" " if cell == 0 else chr(cell) for cell in row)


class BlankEndedRegion(Region):
	"""A region whose spaces come out as blank cells, the way a braille table's do.

	The harness translates a character to its own code, which makes a space cell 0x20 and a
	row of spaces something a test cannot tell from text. Liblouis gives a space no dots at
	all, and whether the last row of a block is blank is exactly the question below.
	"""

	def __init__(self, text=""):
		super().__init__(text)
		self._translate()

	def update(self):
		super().update()
		self._translate()

	def _translate(self):
		self.brailleCells = [0 if character == " " else ord(character) & 0xFF for character in self.rawText]


def blankEnded(text: str, name="b") -> SourceBlock:
	""":return: a block whose spaces are blank cells."""
	return SourceBlock(
		blockId=BlockId(generation=1, bookmark=name, unit="line"),
		region=BlankEndedRegion(text),
	)


class TestARowThatWouldHoldNothing(unittest.TestCase):
	"""The space NVDA parks a caret on must not cost a row of the band.

	Every reading unit NVDA hands over ends with a space it added on purpose — see
	`TextInfoRegion.update`, "in case the cursor is at the end of the reading unit". It is a
	cell like any other, so a line whose content fills the band exactly needs one cell more
	than the row has, and the wrap gave that one blank cell a row to itself. On an eight row
	band that is an eighth of the display spent on nothing, and under the fingers it reads as
	a line skipped between two links, as though the first were continuing.
	"""

	def test_aLineThatFitsExactlyIsOneRow(self):
		rendered = renderer(numCols=8).render(blankEnded("abcdefgh "))
		self.assertEqual(len(rendered.rows), 1)
		self.assertEqual(textOf(rendered.rows[0]), "abcdefgh")

	def test_aLineThatOverflowsProperlyStillWraps(self):
		rendered = renderer(numCols=8).render(blankEnded("abcdefghi "))
		self.assertEqual([textOf(row) for row in rendered.rows], ["abcdefgh", "i "])

	def test_aBlockThatIsNothingButABlankRowKeepsIt(self):
		"""A blank line the document really has is a row, and reads as one."""
		rendered = renderer(numCols=8).render(blankEnded(" "))
		self.assertEqual(len(rendered.rows), 1)

	def test_aCaretParkedOnThatSpaceKeepsItsRow(self):
		"""The case the space was added for. A caret with no cell is worse than a wasted row,
		and a block being written in is read again on every keystroke, so the question is asked
		afresh exactly when the answer can change."""
		source = blankEnded("abcdefgh ")
		source.region.cursorPos = 8
		source.region.brailleCursorPos = 8
		rendered = renderer(numCols=8).render(source)
		self.assertEqual(len(rendered.rows), 2)

	def test_aCaretElsewhereInTheBlockDoesNotKeepIt(self):
		source = blankEnded("abcdefgh ")
		source.region.cursorPos = 0
		source.region.brailleCursorPos = 0
		rendered = renderer(numCols=8).render(source)
		self.assertEqual(len(rendered.rows), 1)

	def test_theRoutingMapLosesOnlyTheDroppedCell(self):
		rendered = renderer(numCols=8).render(blankEnded("abcdefgh "))
		self.assertEqual(rendered.positions[0], tuple(range(8)))


class TestNoDepth(unittest.TestCase):
	"""Prose must render exactly as it did before depth existed."""

	def test_aBlockWithNoDepthIsNotIndented(self):
		rendered = renderer().render(block("hello"))
		self.assertEqual(textOf(rendered.rows[0]), "hello")

	def test_theKeySaysNoIndent(self):
		self.assertEqual(renderer().render(block("hello")).renderKey.indent, 0)

	def test_aDepthWithNoPlanIsStillNotIndented(self):
		"""The depth is a fact about the content; drawing it is the band's decision."""
		rendered = renderer().render(block("hello", depth=4))
		self.assertEqual(textOf(rendered.rows[0]), "hello")


class TestIndentIsDrawn(unittest.TestCase):
	"""A block at depth is pushed in by its depth."""

	def setUp(self):
		self.plan = planFor([1, 2, 3], 16, style=TWO_SPACES)

	def test_theBaselineSitsAtTheMargin(self):
		rendered = renderer(plan=self.plan).render(block("root", depth=1))
		self.assertEqual(textOf(rendered.rows[0]), "root")

	def test_aChildIsPushedInOneLevel(self):
		rendered = renderer(plan=self.plan).render(block("kid", depth=2))
		self.assertEqual(textOf(rendered.rows[0]), "  kid")

	def test_aGrandchildIsPushedInTwo(self):
		rendered = renderer(plan=self.plan).render(block("deep", depth=3))
		self.assertEqual(textOf(rendered.rows[0]), "    deep")

	def test_theStyleThatMarksLevelsDrawsMarks(self):
		plan = planFor([1, 3], 16, style=DOTS_78)
		rendered = renderer(plan=plan).render(block("kid", depth=3))
		self.assertEqual(tuple(rendered.rows[0][:2]), (LEVEL_CELL, LEVEL_CELL))
		self.assertEqual(textOf(rendered.rows[0][2:]), "kid")

	def test_theDepthIsCarriedOnTheRendering(self):
		"""The band computes its next baseline from what it is showing, without re-reading."""
		rendered = renderer(plan=self.plan).render(block("kid", depth=2))
		self.assertEqual(rendered.depth, 2)


class TestRoutingIntoTheMargin(unittest.TestCase):
	"""An indent cell came from nowhere, so a routing key in it must aim at nothing."""

	def test_indentCellsMapToNoPosition(self):
		plan = planFor([1, 3], 16, style=TWO_SPACES)
		rendered = renderer(plan=plan).render(block("kid", depth=3))
		self.assertEqual(tuple(rendered.positions[0][:4]), (NO_POSITION,) * 4)

	def test_theContentStillMapsToItsOwnPositions(self):
		plan = planFor([1, 3], 16, style=TWO_SPACES)
		rendered = renderer(plan=plan).render(block("kid", depth=3))
		self.assertEqual(tuple(rendered.positions[0][4:7]), (0, 1, 2))


class TestWrapping(unittest.TestCase):
	"""The rule a long item cannot be read without."""

	def setUp(self):
		self.plan = planFor([1, 4], 12, style=TWO_SPACES)

	def _wrapped(self, depth):
		return renderer(numCols=12, plan=self.plan).render(block("abcdefghijklmnop", depth=depth))

	def test_aWrappedRowIsIndentedFurtherThanItsFirst(self):
		rendered = self._wrapped(1)
		self.assertEqual(textOf(rendered.rows[0])[:1], "a")
		self.assertTrue(textOf(rendered.rows[1]).startswith("   "))

	def test_aWrappedRowGoesPastWhereAChildWouldStart(self):
		"""A continuation drawn at the child indent is a child, as far as the fingers know."""
		child = renderer(numCols=12, plan=self.plan).render(block("kid", depth=2))
		wrapped = self._wrapped(1)
		childIndent = len(textOf(child.rows[0])) - len(textOf(child.rows[0]).lstrip())
		wrapIndent = len(textOf(wrapped.rows[1])) - len(textOf(wrapped.rows[1]).lstrip())
		self.assertGreater(wrapIndent, childIndent)

	def test_everyRowStillFitsTheBand(self):
		"""The block is laid out in what the *wider* indent leaves, so nothing overflows."""
		for row in self._wrapped(2).rows:
			self.assertLessEqual(len(row), 12)

	def test_noTextIsLostToTheSecondPass(self):
		rendered = self._wrapped(2)
		self.assertEqual("".join(textOf(row) for row in rendered.rows).replace(" ", ""), "abcdefghijklmnop")

	def test_anItemThatFitsPaysNothingForAContinuation(self):
		"""The first pass is at the wider width, so a short item keeps every cell it can."""
		wide = renderer(numCols=12, plan=self.plan).render(block("abcdefghijkl", depth=1))
		self.assertEqual(len(wide.rows), 1)
		self.assertEqual(textOf(wide.rows[0]), "abcdefghijkl")

	def test_continuationCellsMapToNoPosition(self):
		rendered = self._wrapped(1)
		self.assertEqual(tuple(rendered.positions[1][:3]), (NO_POSITION,) * 3)


class TestReachingIntoALongParagraph(unittest.TestCase):
	"""**The rendering cap bounds what is kept, and used to bound nothing about the work.**

	A rendering holds at most `CHUNK_ROWS` rows, and the tail of a long block was reached by
	cutting every row in front of it and throwing them away. That is the right answer for
	word wrapping, where a row's end genuinely depends on the row before it — and the wrong
	one for the mode where every row is the buffer's width and carries straight on, which is
	what a narrow band and every table cell use.

	A reader typing at the end of a very long paragraph was paying for a re-cut of the whole
	of it, on every keystroke, to be shown ten rows.
	"""

	COLS = 32

	def _long(self, length):
		return block("x" * length, name=f"long{length}")

	def _cuts(self, draw):
		""":return: a counter of how many times the buffer was asked to cut a window of rows."""
		from brlMultiline.segments import BrailleBufferSegment

		counted = [0]
		original = BrailleBufferSegment._calculateWindowRowBufferOffsets

		def cut(buffer, pos):
			counted[0] += 1
			return original(buffer, pos)

		BrailleBufferSegment._calculateWindowRowBufferOffsets = cut
		self.addCleanup(
			setattr, BrailleBufferSegment, "_calculateWindowRowBufferOffsets", original
		)
		return counted

	def test_theTailIsReachedWithoutCuttingWhatIsInFrontOfIt(self):
		draw = renderer(numCols=self.COLS)
		for length in (2048, 32768, 131072):
			with self.subTest(length=length):
				long = self._long(length)
				rows = length // self.COLS
				counted = self._cuts(draw)
				rendered = draw.render(long, fromRow=rows - 10)
				self.assertEqual(rendered.numRows, 10)
				self.assertEqual(counted[0], 0, "the whole paragraph was cut to reach its tail")

	def test_andTheRowsAreTheOnesTheWalkWouldHaveProduced(self):
		"""The arithmetic replaces the walk; it does not replace what the walk was for."""
		draw = renderer(numCols=8)
		text = "".join(chr(ord("a") + index % 26) for index in range(200))
		long = block(text, name="mixed")
		rendered = draw.render(long, fromRow=3)
		self.assertEqual(textOf(rendered.rows[0]), text[24:32])
		self.assertEqual(rendered.positions[0], tuple(range(24, 32)))
		self.assertEqual(rendered.rowOffset, 3)
		# Twenty-five rows of eight cells, less the three skipped, and the last one short.
		self.assertEqual(rendered.numRows, 22)
		self.assertEqual(textOf(rendered.rows[-1]), text[192:200])
		self.assertFalse(rendered.moreRows)

	def test_andABlockTallerThanOneChunkSaysThereIsMore(self):
		draw = renderer(numCols=8)
		rendered = draw.render(self._long(8 * 100))
		self.assertEqual(rendered.numRows, draw.maxRows)
		self.assertTrue(rendered.moreRows)
		last = draw.render(self._long(8 * 100), fromRow=100 - draw.maxRows)
		self.assertFalse(last.moreRows)

	def test_andAShortBlockStillEndsWhereItEnds(self):
		draw = renderer(numCols=8)
		rendered = draw.render(block("abcdefghij"), fromRow=1)
		self.assertEqual([textOf(row) for row in rendered.rows], ["ij"])
		self.assertFalse(rendered.moreRows)

	def test_andABlankBlockIsStillOneRow(self):
		rendered = renderer(numCols=8).render(block(""))
		self.assertEqual(rendered.numRows, 1)

	def test_aCaretsRowIsFoundWithoutCuttingEitherOfThem(self):
		"""`renderAround` locates the caret's row before rendering it, and that lookup was a
		second walk from the start of the block."""
		draw = renderer(numCols=self.COLS)
		long = self._long(131072)
		counted = self._cuts(draw)
		rendered = draw.renderAround(long, 131072 - 5, contextRows=9)
		self.assertEqual(counted[0], 0)
		self.assertTrue(rendered.rowOffset > 0)
		self.assertIn(131072 - 5, rendered.positions[-1])


class TestTheRenderKey(unittest.TestCase):
	"""Two depths are two renderings, and one must never be served for the other."""

	def test_theKeyRecordsTheIndent(self):
		plan = planFor([1, 3], 16, style=TWO_SPACES)
		self.assertEqual(renderer(plan=plan).render(block("a", depth=3)).renderKey.indent, 4)

	def test_twoDepthsGiveTwoKeys(self):
		plan = planFor([1, 3], 16, style=TWO_SPACES)
		draw = renderer(plan=plan)
		self.assertNotEqual(
			draw.render(block("a", depth=1)).renderKey,
			draw.render(block("a", depth=3)).renderKey,
		)


if __name__ == "__main__":
	unittest.main()
