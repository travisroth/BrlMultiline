# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for grid layouts and rectangle validation.

Grids are not reachable from the settings dialog, which offers whole row groups only.
They exist for code that drives the display directly, such as a table reader wanting
several cells visible at once on a Monarch.
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
	calculateGridRects,
	findSegmentAtWindowPos,
	segmentPosToWindowPos,
	validateRects,
)

MONARCH_ROWS = 8
MONARCH_COLS = 32


class TestCalculateGridRects(unittest.TestCase):
	def test_fourColumnsAcrossThreeBands(self):
		"""The motivating case: twelve table cells visible on a Monarch at once."""
		rects = calculateGridRects(MONARCH_ROWS, MONARCH_COLS, [2, 2, 2], [8, 8, 8, 8])
		self.assertEqual(len(rects), 12)
		self.assertEqual(rects[0], SegmentRect(row=0, col=0, numRows=2, numCols=8))
		self.assertEqual(rects[3], SegmentRect(row=0, col=24, numRows=2, numCols=8))
		self.assertEqual(rects[4], SegmentRect(row=2, col=0, numRows=2, numCols=8))
		self.assertEqual(rects[11], SegmentRect(row=4, col=24, numRows=2, numCols=8))

	def test_readingOrder(self):
		"""Rectangles come back left to right, then top to bottom."""
		rects = calculateGridRects(MONARCH_ROWS, MONARCH_COLS, [3, 3], [16, 16])
		self.assertEqual([(rect.row, rect.col) for rect in rects], [(0, 0), (0, 16), (3, 0), (3, 16)])

	def test_unevenBandsAndColumns(self):
		rects = calculateGridRects(MONARCH_ROWS, MONARCH_COLS, [1, 3], [10, 22])
		self.assertEqual(rects[0], SegmentRect(row=0, col=0, numRows=1, numCols=10))
		self.assertEqual(rects[1], SegmentRect(row=0, col=10, numRows=1, numCols=22))
		self.assertEqual(rects[2], SegmentRect(row=1, col=0, numRows=3, numCols=10))

	def test_gridNeedNotFillTheDisplay(self):
		"""Leftover rows and cells are allowed, and show as blank."""
		rects = calculateGridRects(MONARCH_ROWS, MONARCH_COLS, [2], [8, 8])
		self.assertEqual(len(rects), 2)
		validateRects(rects, MONARCH_ROWS, MONARCH_COLS)

	def test_rejectsTooManyRows(self):
		with self.assertRaises(ValueError):
			calculateGridRects(MONARCH_ROWS, MONARCH_COLS, [3, 3, 3], [8])

	def test_rejectsTooManyCells(self):
		with self.assertRaises(ValueError):
			calculateGridRects(MONARCH_ROWS, MONARCH_COLS, [2], [16, 16, 16])


class TestValidateRects(unittest.TestCase):
	def test_acceptsAGrid(self):
		rects = calculateGridRects(MONARCH_ROWS, MONARCH_COLS, [2, 2, 2], [8, 8, 8, 8])
		validateRects(rects, MONARCH_ROWS, MONARCH_COLS)

	def test_acceptsGaps(self):
		"""Gutters between segments are useful, so gaps must be allowed."""
		rects = [
			SegmentRect(row=0, col=0, numRows=2, numCols=8),
			SegmentRect(row=0, col=12, numRows=2, numCols=8),
		]
		validateRects(rects, MONARCH_ROWS, MONARCH_COLS)

	def test_rejectsEmpty(self):
		with self.assertRaises(ValueError):
			validateRects([], MONARCH_ROWS, MONARCH_COLS)

	def test_rejectsOverlapOnColumns(self):
		rects = [
			SegmentRect(row=0, col=0, numRows=2, numCols=10),
			SegmentRect(row=0, col=8, numRows=2, numCols=10),
		]
		with self.assertRaises(ValueError):
			validateRects(rects, MONARCH_ROWS, MONARCH_COLS)

	def test_rejectsOverlapOnRows(self):
		rects = [
			SegmentRect(row=0, col=0, numRows=3, numCols=8),
			SegmentRect(row=2, col=0, numRows=3, numCols=8),
		]
		with self.assertRaises(ValueError):
			validateRects(rects, MONARCH_ROWS, MONARCH_COLS)

	def test_rejectsRunningPastTheBottom(self):
		rects = [SegmentRect(row=6, col=0, numRows=4, numCols=8)]
		with self.assertRaises(ValueError):
			validateRects(rects, MONARCH_ROWS, MONARCH_COLS)

	def test_rejectsRunningPastTheRight(self):
		rects = [SegmentRect(row=0, col=28, numRows=2, numCols=8)]
		with self.assertRaises(ValueError):
			validateRects(rects, MONARCH_ROWS, MONARCH_COLS)

	def test_rejectsNegativeOrigin(self):
		with self.assertRaises(ValueError):
			validateRects([SegmentRect(row=-1, col=0, numRows=2, numCols=8)], MONARCH_ROWS, MONARCH_COLS)

	def test_rejectsEmptyRectangle(self):
		with self.assertRaises(ValueError):
			validateRects([SegmentRect(row=0, col=0, numRows=0, numCols=8)], MONARCH_ROWS, MONARCH_COLS)


class TestRoutingOnAGrid(unittest.TestCase):
	"""Routing keys must find the right cell of a grid, not just the right row band."""

	def setUp(self):
		self.rects = calculateGridRects(MONARCH_ROWS, MONARCH_COLS, [2, 2, 2], [8, 8, 8, 8])

	def test_firstCellOfFirstSegment(self):
		self.assertEqual(findSegmentAtWindowPos(self.rects, 0, MONARCH_COLS), (0, 0))

	def test_ninthCellIsTheSecondSegment(self):
		# Row 0, column 8 is the start of the second column of the grid.
		self.assertEqual(findSegmentAtWindowPos(self.rects, 8, MONARCH_COLS), (1, 0))

	def test_secondRowOfTheFirstSegment(self):
		# Row 1, column 0 is the 33rd cell of the display, and the 9th cell of segment 0.
		self.assertEqual(findSegmentAtWindowPos(self.rects, 32, MONARCH_COLS), (0, 8))

	def test_secondBandStartsAtRowTwo(self):
		# Row 2, column 0 is the 65th cell of the display.
		self.assertEqual(findSegmentAtWindowPos(self.rects, 64, MONARCH_COLS), (4, 0))

	def test_lastCellOfTheGrid(self):
		# Row 5, column 31 is the last cell of the twelfth segment.
		self.assertEqual(findSegmentAtWindowPos(self.rects, 5 * 32 + 31, MONARCH_COLS), (11, 15))

	def test_positionInAnUncoveredRowRaises(self):
		# The grid covers rows 0 to 5; rows 6 and 7 belong to no segment.
		with self.assertRaises(LookupError):
			findSegmentAtWindowPos(self.rects, 6 * 32, MONARCH_COLS)

	def test_roundTripOverEveryCoveredCell(self):
		for windowPos in range(6 * MONARCH_COLS):
			with self.subTest(windowPos=windowPos):
				index, segmentPos = findSegmentAtWindowPos(self.rects, windowPos, MONARCH_COLS)
				self.assertEqual(
					segmentPosToWindowPos(self.rects[index], segmentPos, MONARCH_COLS),
					windowPos,
				)

	def test_everyCellMapsToExactlyOneSegment(self):
		seen = {}
		for windowPos in range(6 * MONARCH_COLS):
			index, segmentPos = findSegmentAtWindowPos(self.rects, windowPos, MONARCH_COLS)
			key = (index, segmentPos)
			self.assertNotIn(key, seen, f"{key} claimed by both {seen.get(key)} and {windowPos}")
			seen[key] = windowPos
		self.assertEqual(len(seen), 6 * MONARCH_COLS)
