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
