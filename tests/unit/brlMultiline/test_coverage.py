# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the geometry that composition rests on.

Panels are held to a stricter rule than segments: they must cover the display exactly once
over, so that every cell has an owner and a new claim can only take cells from a panel that
can be named and evicted. `remainderRects` is what lets a view satisfy that rule without
every caller doing the arithmetic.
"""

import os
import sys
import unittest

sys.path.insert(
	0,
	os.path.join(os.path.dirname(__file__), "..", "..", "..", "addon", "globalPlugins", "brlMultiline"),
)

from layout import (  # noqa: E402
	SegmentRect,
	calculateGridRectsIn,
	calculateSegmentRectsIn,
	rectContains,
	rectsIntersect,
	remainderRects,
	translateRect,
	validateCoverage,
	wholeDisplayRect,
)

MONARCH_ROWS = 8
MONARCH_COLS = 32
FOCUS_ROWS = 1
FOCUS_COLS = 80


class TestRectHelpers(unittest.TestCase):
	def test_endRowAndEndCol(self):
		rect = SegmentRect(row=2, col=8, numRows=3, numCols=10)
		self.assertEqual(rect.endRow, 5)
		self.assertEqual(rect.endCol, 18)

	def test_wholeDisplayRect(self):
		rect = wholeDisplayRect(MONARCH_ROWS, MONARCH_COLS)
		self.assertEqual(rect, SegmentRect(0, 0, MONARCH_ROWS, MONARCH_COLS))
		self.assertEqual(rect.displaySize, 256)

	def test_translateRect(self):
		self.assertEqual(
			translateRect(SegmentRect(0, 0, 2, 10), row=4, col=20),
			SegmentRect(4, 20, 2, 10),
		)

	def test_rectContains(self):
		outer = wholeDisplayRect(MONARCH_ROWS, MONARCH_COLS)
		self.assertTrue(rectContains(outer, SegmentRect(2, 0, 3, 32)))
		self.assertTrue(rectContains(outer, outer))
		self.assertFalse(rectContains(SegmentRect(2, 0, 3, 32), outer))

	def test_rectContainsRejectsAnOverhang(self):
		outer = SegmentRect(0, 0, 4, 32)
		self.assertFalse(rectContains(outer, SegmentRect(3, 0, 2, 32)))
		self.assertFalse(rectContains(outer, SegmentRect(0, 30, 4, 4)))

	def test_rectsIntersect(self):
		self.assertTrue(rectsIntersect(SegmentRect(0, 0, 2, 32), SegmentRect(1, 0, 2, 32)))
		self.assertFalse(rectsIntersect(SegmentRect(0, 0, 2, 32), SegmentRect(2, 0, 2, 32)))

	def test_touchingEdgesDoNotIntersect(self):
		"""Two segments sharing an edge are neighbours, not an overlap."""
		self.assertFalse(rectsIntersect(SegmentRect(0, 0, 8, 16), SegmentRect(0, 16, 8, 16)))

	def test_intersectionAcrossColumns(self):
		self.assertTrue(rectsIntersect(SegmentRect(0, 0, 8, 20), SegmentRect(4, 10, 2, 20)))


class TestValidateCoverage(unittest.TestCase):
	def test_acceptsAnExactTiling(self):
		validateCoverage(
			[SegmentRect(0, 0, 4, 32), SegmentRect(4, 0, 4, 32)],
			MONARCH_ROWS,
			MONARCH_COLS,
		)

	def test_acceptsOneRectCoveringEverything(self):
		validateCoverage([wholeDisplayRect(MONARCH_ROWS, MONARCH_COLS)], MONARCH_ROWS, MONARCH_COLS)

	def test_rejectsAGap(self):
		with self.assertRaises(ValueError) as caught:
			validateCoverage([SegmentRect(0, 0, 4, 32)], MONARCH_ROWS, MONARCH_COLS)
		self.assertIn("no owner", str(caught.exception))

	def test_rejectsAnOverlap(self):
		with self.assertRaises(ValueError):
			validateCoverage(
				[SegmentRect(0, 0, 5, 32), SegmentRect(4, 0, 4, 32)],
				MONARCH_ROWS,
				MONARCH_COLS,
			)

	def test_rejectsRunningPastTheEdge(self):
		with self.assertRaises(ValueError):
			validateCoverage([SegmentRect(0, 0, 9, 32)], MONARCH_ROWS, MONARCH_COLS)

	def test_rejectsNothing(self):
		with self.assertRaises(ValueError):
			validateCoverage([], MONARCH_ROWS, MONARCH_COLS)

	def test_columnTilingOnASingleRowDisplay(self):
		validateCoverage(
			[SegmentRect(0, 0, 1, 40), SegmentRect(0, 40, 1, 40)],
			FOCUS_ROWS,
			FOCUS_COLS,
		)


class TestRemainderRects(unittest.TestCase):
	def test_nothingLeftOverWhenAlreadyCovered(self):
		self.assertEqual(
			remainderRects([wholeDisplayRect(MONARCH_ROWS, MONARCH_COLS)], MONARCH_ROWS, MONARCH_COLS),
			[],
		)

	def test_everythingWhenNothingIsClaimed(self):
		self.assertEqual(
			remainderRects([], MONARCH_ROWS, MONARCH_COLS),
			[wholeDisplayRect(MONARCH_ROWS, MONARCH_COLS)],
		)

	def test_aRowClaimLeavesOneBlock(self):
		"""A freed block comes back as one rectangle rather than one per row."""
		self.assertEqual(
			remainderRects([SegmentRect(0, 0, 1, 32)], MONARCH_ROWS, MONARCH_COLS),
			[SegmentRect(1, 0, 7, 32)],
		)

	def test_aColumnClaimLeavesOneBlock(self):
		self.assertEqual(
			remainderRects([SegmentRect(0, 0, 8, 10)], MONARCH_ROWS, MONARCH_COLS),
			[SegmentRect(0, 10, 8, 22)],
		)

	def test_aClaimInTheMiddleLeavesTwoBlocks(self):
		self.assertEqual(
			remainderRects([SegmentRect(3, 0, 2, 32)], MONARCH_ROWS, MONARCH_COLS),
			[SegmentRect(0, 0, 3, 32), SegmentRect(5, 0, 3, 32)],
		)

	def test_theResultAlwaysCompletesTheTiling(self):
		claims = [SegmentRect(0, 0, 1, 32), SegmentRect(4, 8, 2, 16)]
		filled = claims + remainderRects(claims, MONARCH_ROWS, MONARCH_COLS)
		validateCoverage(filled, MONARCH_ROWS, MONARCH_COLS)

	def test_overlappingClaimsAreToleratedNotRejected(self):
		"""The caller is mid composition, so this reports what is left rather than complaining."""
		claims = [SegmentRect(0, 0, 4, 32), SegmentRect(2, 0, 4, 32)]
		leftover = remainderRects(claims, MONARCH_ROWS, MONARCH_COLS)
		self.assertEqual(leftover, [SegmentRect(6, 0, 2, 32)])

	def test_resultsAreInReadingOrder(self):
		leftover = remainderRects([SegmentRect(2, 0, 2, 32)], MONARCH_ROWS, MONARCH_COLS)
		self.assertEqual([rect.row for rect in leftover], sorted(rect.row for rect in leftover))


class TestCalculateWithinARect(unittest.TestCase):
	def test_segmentsWithinAPanelAreInDisplayCoordinates(self):
		rects = calculateSegmentRectsIn(SegmentRect(2, 0, 6, 32), 3)
		self.assertEqual(
			rects,
			[SegmentRect(2, 0, 2, 32), SegmentRect(4, 0, 2, 32), SegmentRect(6, 0, 2, 32)],
		)

	def test_aSingleRowPanelDividesIntoColumns(self):
		rects = calculateSegmentRectsIn(SegmentRect(3, 0, 1, 32), 2)
		self.assertEqual(rects, [SegmentRect(3, 0, 1, 16), SegmentRect(3, 16, 1, 16)])

	def test_offsetPanelsCarryTheirOrigin(self):
		rects = calculateSegmentRectsIn(SegmentRect(4, 8, 2, 16), 2)
		self.assertEqual(rects, [SegmentRect(4, 8, 1, 16), SegmentRect(5, 8, 1, 16)])

	def test_gridWithinAPanel(self):
		rects = calculateGridRectsIn(SegmentRect(2, 0, 6, 32), rowBands=[2, 2, 2], colWidths=[10, 10, 10])
		self.assertEqual(len(rects), 9)
		self.assertEqual(rects[0], SegmentRect(2, 0, 2, 10))
		self.assertEqual(rects[5], SegmentRect(4, 20, 2, 10))
		self.assertEqual(rects[8], SegmentRect(6, 20, 2, 10))

	def test_segmentsStayInsideTheirPanel(self):
		panel = SegmentRect(2, 0, 6, 32)
		for rect in calculateGridRectsIn(panel, rowBands=[2, 2, 2], colWidths=[10, 10, 10]):
			self.assertTrue(rectContains(panel, rect))

	def test_aLayoutThatDoesNotFitIsRejected(self):
		with self.assertRaises(ValueError):
			calculateSegmentRectsIn(SegmentRect(0, 0, 2, 32), 5)

	def test_aGridThatDoesNotFitIsRejected(self):
		with self.assertRaises(ValueError):
			calculateGridRectsIn(SegmentRect(0, 0, 2, 32), rowBands=[2, 2], colWidths=[10])


if __name__ == "__main__":
	unittest.main()
