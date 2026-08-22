# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for turning a block's depth into cells.

Written against a Monarch sized row of 32 cells, and against narrower ones where the
arithmetic is the thing being tested. Nothing here needs a braille table: a depth is a
number and an indent is a count of cells, which is why `flowIndent` imports nothing from
NVDA.
"""

import os
import sys
import unittest

sys.path.insert(
	0,
	os.path.join(os.path.dirname(__file__), "..", "..", "..", "addon", "globalPlugins", "brlMultiline"),
)

from flowIndent import (  # noqa: E402
	BLANK_CELL,
	DOTS_78,
	FLAT,
	LEVEL_CELL,
	ONE_SPACE,
	TWO_SPACES,
	IndentPlan,
	cellsPerLevel,
	planFor,
	shouldRebase,
)

MONARCH_COLS = 32


class TestCellsPerLevel(unittest.TestCase):
	"""What one level costs, per style."""

	def test_knownStyles(self):
		self.assertEqual(cellsPerLevel(TWO_SPACES), 2)
		self.assertEqual(cellsPerLevel(ONE_SPACE), 1)
		self.assertEqual(cellsPerLevel(DOTS_78), 1)

	def test_unknownStyleReadsAsTheDefault(self):
		"""A setting from a later version must not stop a display being drawn."""
		self.assertEqual(cellsPerLevel("engraved"), cellsPerLevel(TWO_SPACES))
		self.assertEqual(cellsPerLevel(None), cellsPerLevel(TWO_SPACES))


class TestPlanning(unittest.TestCase):
	"""Choosing a baseline for a band."""

	def test_contentWithNoDepthIsFlat(self):
		"""Prose must cost no arithmetic at all."""
		plan = planFor([None, None, None], MONARCH_COLS)
		self.assertIs(plan, FLAT)
		self.assertTrue(plan.isFlat)
		self.assertEqual(plan.cellsFor(None), 0)

	def test_noDepthsAtAllIsFlat(self):
		self.assertIs(planFor([], MONARCH_COLS), FLAT)

	def test_shallowTreeIsDrawnAtItsTrueDepth(self):
		"""Three levels cost four cells of 32, so there is nothing to rebase away from."""
		plan = planFor([1, 2, 3], MONARCH_COLS, style=TWO_SPACES)
		self.assertEqual(plan.baseline, 1)
		self.assertIsNone(plan.noteLevel)
		self.assertEqual(plan.cellsFor(1), 0)
		self.assertEqual(plan.cellsFor(2), 2)
		self.assertEqual(plan.cellsFor(3), 4)

	def test_deepTreeRebasesToTheShallowestOnTheBand(self):
		"""Nine levels at two cells would be sixteen of 32, so the band rebases."""
		plan = planFor([7, 8, 9], MONARCH_COLS, style=TWO_SPACES)
		self.assertEqual(plan.baseline, 7)
		self.assertEqual(plan.cellsFor(7), 0)
		self.assertEqual(plan.cellsFor(9), 4)

	def test_rebasingSaysWhatTheMarginStandsFor(self):
		plan = planFor([7, 8, 9], MONARCH_COLS, style=TWO_SPACES)
		self.assertEqual(plan.noteLevel, 7)

	def test_nothingIsSaidWhenTheMarginMeansWhatItLooksLike(self):
		"""A margin that really is level 1 must not be announced as one."""
		self.assertIsNone(planFor([1, 2], MONARCH_COLS).noteLevel)

	def test_aRebaseToLevelOneSaysNothingEither(self):
		"""The band is deep, but its shallowest item is the root: the margin is honest."""
		plan = planFor([1, 12], MONARCH_COLS, style=TWO_SPACES)
		self.assertEqual(plan.baseline, 1)
		self.assertIsNone(plan.noteLevel)

	def test_theShallowestItemMayNotBeTheTopRow(self):
		"""Walking a tree in visible order comes back out of a subtree, so a later block can
		be shallower than the first. It cannot be drawn at a negative indent."""
		plan = planFor([9, 10, 8], MONARCH_COLS, style=TWO_SPACES)
		self.assertEqual(plan.baseline, 8)
		self.assertEqual(plan.cellsFor(8), 0)
		self.assertEqual(plan.cellsFor(9), 2)


class TestTheShare(unittest.TestCase):
	"""How much of a row indent may take."""

	def test_indentIsCappedAtAQuarterOfTheRow(self):
		"""32 cells allows 8 of indent, which is four levels at two cells."""
		plan = planFor([20, 30], MONARCH_COLS, style=TWO_SPACES)
		self.assertEqual(plan.maxLevels, 4)
		self.assertEqual(plan.cellsFor(30), 8)

	def test_depthPastTheCapSharesTheDeepestIndent(self):
		"""Better than pushing an item off the right by its own position in a tree."""
		plan = planFor([20, 30], MONARCH_COLS, style=TWO_SPACES)
		self.assertEqual(plan.cellsFor(24), 8)
		self.assertEqual(plan.cellsFor(30), 8)

	def test_aWiderBandDrawsMoreOfTheTruth(self):
		"""The share is of the row, so 80 cells buys the deeper true picture."""
		wide = planFor([1, 8], 80, style=TWO_SPACES)
		self.assertEqual(wide.baseline, 1)
		self.assertEqual(wide.cellsFor(8), 14)
		narrow = planFor([1, 8], MONARCH_COLS, style=TWO_SPACES)
		self.assertEqual(narrow.cellsFor(8), 8)

	def test_aBandTooNarrowToIndentIsFlat(self):
		"""One cell of indent on a row this short costs more than the depth is worth."""
		self.assertIs(planFor([1, 2, 3], 4, style=TWO_SPACES), FLAT)

	def test_oneSpaceFitsWhereTwoWouldNot(self):
		plan = planFor([1, 6], MONARCH_COLS, style=ONE_SPACE)
		self.assertEqual(plan.baseline, 1)
		self.assertEqual(plan.cellsFor(6), 5)


class TestPrefixes(unittest.TestCase):
	"""The cells an indent is actually drawn with."""

	def test_spacesAreBlank(self):
		plan = planFor([1, 3], MONARCH_COLS, style=TWO_SPACES)
		self.assertEqual(plan.prefixFor(3), (BLANK_CELL,) * 4)

	def test_dots78MarksEachLevel(self):
		plan = planFor([1, 3], MONARCH_COLS, style=DOTS_78)
		self.assertEqual(plan.prefixFor(3), (LEVEL_CELL, LEVEL_CELL))

	def test_theBaselineItselfHasNoPrefix(self):
		plan = planFor([1, 3], MONARCH_COLS, style=DOTS_78)
		self.assertEqual(plan.prefixFor(1), ())

	def test_contentWithNoDepthHasNoPrefix(self):
		plan = planFor([1, 3], MONARCH_COLS, style=DOTS_78)
		self.assertEqual(plan.prefixFor(None), ())


class TestContinuations(unittest.TestCase):
	"""A wrapped row must not read as a child of the item it belongs to."""

	def test_aContinuationGoesPastWhereAChildWouldStart(self):
		"""Stated as the relationship rather than a number, since that is the rule: whatever
		a child of this item would be indented by, its own wrapped rows go further."""
		plan = planFor([1, 5], MONARCH_COLS, style=TWO_SPACES)
		for depth in (1, 2, 3):
			with self.subTest(depth=depth):
				self.assertGreater(plan.continuationCellsFor(depth), plan.cellsFor(depth + 1))

	def test_theTopLevelCaseInFull(self):
		"""A level 1 item indents nothing, its child two cells, its continuation three."""
		plan = planFor([1, 5], MONARCH_COLS, style=TWO_SPACES)
		self.assertEqual(plan.cellsFor(1), 0)
		self.assertEqual(plan.cellsFor(2), 2)
		self.assertEqual(plan.continuationCellsFor(1), 3)

	def test_aContinuationStopsShortOfAGrandchild(self):
		"""So the three readings stay in order under the fingers."""
		plan = planFor([1, 5], MONARCH_COLS, style=TWO_SPACES)
		self.assertLess(plan.continuationCellsFor(2), plan.cellsFor(4))

	def test_continuationCellsAreBlankEvenWhenLevelsAreMarked(self):
		"""The marks say how deep this is, and a continuation is not deeper."""
		plan = planFor([1, 4], MONARCH_COLS, style=DOTS_78)
		self.assertEqual(plan.continuationPrefixFor(2), (LEVEL_CELL, BLANK_CELL, BLANK_CELL))

	def test_aMarkedGrandchildIsNotAContinuation(self):
		"""At one cell a level the counts collide; the shapes must not."""
		plan = planFor([1, 4], MONARCH_COLS, style=DOTS_78)
		self.assertEqual(len(plan.continuationPrefixFor(2)), len(plan.prefixFor(4)))
		self.assertNotEqual(plan.continuationPrefixFor(2), plan.prefixFor(4))

	def test_contentWithNoDepthHasNoContinuationIndent(self):
		plan = planFor([1, 3], MONARCH_COLS)
		self.assertEqual(plan.continuationCellsFor(None), 0)
		self.assertEqual(plan.continuationPrefixFor(None), ())

	def test_aFlatPlanIndentsNothing(self):
		self.assertEqual(FLAT.continuationCellsFor(4), 0)
		self.assertEqual(FLAT.cellsFor(4), 0)


class TestRebasing(unittest.TestCase):
	"""A plan is kept until it stops working, so the margin does not twitch."""

	def _plan(self, depths, numCols=MONARCH_COLS, style=TWO_SPACES):
		return planFor(depths, numCols, style=style)

	def test_aPlanThatStillWorksIsKept(self):
		"""The ideal baseline would be 8 now, and moving to it would shift every row for
		nothing the reader asked for."""
		plan = self._plan([7, 8, 9])
		self.assertFalse(shouldRebase(plan, [8, 9, 10], MONARCH_COLS, style=TWO_SPACES))

	def test_aShallowerItemForcesARebase(self):
		"""Coming back out of a subtree; there is nothing left of the margin."""
		plan = self._plan([7, 8, 9])
		self.assertTrue(shouldRebase(plan, [6, 7, 8], MONARCH_COLS, style=TWO_SPACES))

	def test_runningOutOfLevelsForcesARebase(self):
		"""Two depths drawing one indent is the thing indent exists to prevent."""
		plan = self._plan([7, 8, 9])
		self.assertTrue(shouldRebase(plan, [7, 13, 14], MONARCH_COLS, style=TWO_SPACES))

	def test_leavingTheTreeGoesFlat(self):
		plan = self._plan([7, 8, 9])
		self.assertTrue(shouldRebase(plan, [None, None], MONARCH_COLS, style=TWO_SPACES))

	def test_flatContentUnderAFlatPlanStaysPut(self):
		"""Prose must not pay for this on every operation."""
		self.assertFalse(shouldRebase(FLAT, [None, None], MONARCH_COLS, style=TWO_SPACES))

	def test_depthArrivingUnderAFlatPlanRebases(self):
		self.assertTrue(shouldRebase(FLAT, [1, 2], MONARCH_COLS, style=TWO_SPACES))

	def test_aChangedStyleRebases(self):
		plan = self._plan([1, 2, 3])
		self.assertTrue(shouldRebase(plan, [1, 2, 3], MONARCH_COLS, style=DOTS_78))

	def test_aResizedBandRebases(self):
		plan = self._plan([1, 2, 3])
		self.assertTrue(shouldRebase(plan, [1, 2, 3], 8, style=TWO_SPACES))

	def test_theSameBandDoesNotRebase(self):
		plan = self._plan([1, 2, 3])
		self.assertFalse(shouldRebase(plan, [1, 2, 3], MONARCH_COLS, style=TWO_SPACES))


class TestThePlanIsAKey(unittest.TestCase):
	"""Two blocks drawn under different plans are two different renderings."""

	def test_plansCompareByValue(self):
		first = planFor([1, 3], MONARCH_COLS, style=TWO_SPACES)
		second = planFor([1, 3], MONARCH_COLS, style=TWO_SPACES)
		self.assertEqual(first, second)

	def test_aRebasedPlanIsNotTheSameAsATrueOne(self):
		self.assertNotEqual(
			planFor([1, 3], MONARCH_COLS, style=TWO_SPACES),
			planFor([7, 9], MONARCH_COLS, style=TWO_SPACES),
		)

	def test_aPlanIsHashable(self):
		"""Unlike a bookmark. It goes in a render key, which is compared and may be cached."""
		self.assertIsInstance(hash(IndentPlan()), int)


if __name__ == "__main__":
	unittest.main()
