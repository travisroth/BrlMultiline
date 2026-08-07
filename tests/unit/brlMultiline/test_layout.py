# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for segment geometry.

The two displays these are written against are a Humanware Monarch (8 rows of 32 cells)
and a Freedom Scientific Focus 80 (1 row of 80 cells).
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
	calculateSegmentRects,
	divideEvenly,
	findSegmentAtWindowPos,
	segmentPosToWindowPos,
)

MONARCH_ROWS = 8
MONARCH_COLS = 32
FOCUS_ROWS = 1
FOCUS_COLS = 80


class TestDivideEvenly(unittest.TestCase):
	def test_exactDivision(self):
		self.assertEqual(divideEvenly(80, 2), [40, 40])
		self.assertEqual(divideEvenly(8, 4), [2, 2, 2, 2])

	def test_remainderGoesToEarlierParts(self):
		self.assertEqual(divideEvenly(8, 3), [3, 3, 2])
		self.assertEqual(divideEvenly(80, 3), [27, 27, 26])

	def test_singlePart(self):
		self.assertEqual(divideEvenly(80, 1), [80])

	def test_rejectsNonPositiveParts(self):
		with self.assertRaises(ValueError):
			divideEvenly(80, 0)

	def test_rejectsMorePartsThanCells(self):
		with self.assertRaises(ValueError):
			divideEvenly(8, 9)


class TestCalculateSegmentRectsMultiRow(unittest.TestCase):
	"""On a Monarch, segments are groups of whole rows spanning the full width."""

	def test_singleSegmentCoversDisplay(self):
		rects = calculateSegmentRects(MONARCH_ROWS, MONARCH_COLS, 1)
		self.assertEqual(rects, [SegmentRect(row=0, col=0, numRows=8, numCols=32)])

	def test_evenSplitByRows(self):
		rects = calculateSegmentRects(MONARCH_ROWS, MONARCH_COLS, 4)
		self.assertEqual(
			rects,
			[
				SegmentRect(row=0, col=0, numRows=2, numCols=32),
				SegmentRect(row=2, col=0, numRows=2, numCols=32),
				SegmentRect(row=4, col=0, numRows=2, numCols=32),
				SegmentRect(row=6, col=0, numRows=2, numCols=32),
			],
		)

	def test_oneRowPerSegment(self):
		rects = calculateSegmentRects(MONARCH_ROWS, MONARCH_COLS, 8)
		self.assertEqual(len(rects), 8)
		self.assertEqual([rect.row for rect in rects], list(range(8)))
		self.assertTrue(all(rect.numRows == 1 and rect.numCols == 32 for rect in rects))

	def test_explicitUnequalSizes(self):
		rects = calculateSegmentRects(MONARCH_ROWS, MONARCH_COLS, [1, 5, 2])
		self.assertEqual(
			rects,
			[
				SegmentRect(row=0, col=0, numRows=1, numCols=32),
				SegmentRect(row=1, col=0, numRows=5, numCols=32),
				SegmentRect(row=6, col=0, numRows=2, numCols=32),
			],
		)

	def test_segmentsTileTheDisplayWithoutGaps(self):
		for layout in (1, 2, 3, 4, 8, [1, 5, 2], [7, 1]):
			with self.subTest(layout=layout):
				rects = calculateSegmentRects(MONARCH_ROWS, MONARCH_COLS, layout)
				self.assertEqual(sum(rect.numRows for rect in rects), MONARCH_ROWS)
				self.assertEqual(sum(rect.displaySize for rect in rects), MONARCH_ROWS * MONARCH_COLS)
				expectedRow = 0
				for rect in rects:
					self.assertEqual(rect.row, expectedRow)
					expectedRow += rect.numRows

	def test_rejectsSizesThatDoNotSumToDisplay(self):
		with self.assertRaises(ValueError):
			calculateSegmentRects(MONARCH_ROWS, MONARCH_COLS, [1, 2])

	def test_rejectsMoreSegmentsThanRows(self):
		with self.assertRaises(ValueError):
			calculateSegmentRects(MONARCH_ROWS, MONARCH_COLS, 9)


class TestCalculateSegmentRectsSingleRow(unittest.TestCase):
	"""On a Focus 80, segments are column slices of the single row."""

	def test_singleSegmentCoversDisplay(self):
		rects = calculateSegmentRects(FOCUS_ROWS, FOCUS_COLS, 1)
		self.assertEqual(rects, [SegmentRect(row=0, col=0, numRows=1, numCols=80)])

	def test_twoHalves(self):
		rects = calculateSegmentRects(FOCUS_ROWS, FOCUS_COLS, 2)
		self.assertEqual(
			rects,
			[
				SegmentRect(row=0, col=0, numRows=1, numCols=40),
				SegmentRect(row=0, col=40, numRows=1, numCols=40),
			],
		)

	def test_explicitUnequalSizes(self):
		rects = calculateSegmentRects(FOCUS_ROWS, FOCUS_COLS, [20, 60])
		self.assertEqual(
			rects,
			[
				SegmentRect(row=0, col=0, numRows=1, numCols=20),
				SegmentRect(row=0, col=20, numRows=1, numCols=60),
			],
		)

	def test_segmentsTileTheRowWithoutGaps(self):
		for layout in (1, 2, 3, 4, [20, 60], [10, 10, 60]):
			with self.subTest(layout=layout):
				rects = calculateSegmentRects(FOCUS_ROWS, FOCUS_COLS, layout)
				self.assertEqual(sum(rect.numCols for rect in rects), FOCUS_COLS)
				expectedCol = 0
				for rect in rects:
					self.assertEqual(rect.col, expectedCol)
					self.assertEqual(rect.numRows, 1)
					expectedCol += rect.numCols


class TestRoutingOnSingleRow(unittest.TestCase):
	"""Routing key mapping on a Focus 80 split into two halves."""

	def setUp(self):
		self.rects = calculateSegmentRects(FOCUS_ROWS, FOCUS_COLS, 2)

	def test_firstCellOfFirstSegment(self):
		self.assertEqual(findSegmentAtWindowPos(self.rects, 0, FOCUS_COLS), (0, 0))

	def test_lastCellOfFirstSegment(self):
		self.assertEqual(findSegmentAtWindowPos(self.rects, 39, FOCUS_COLS), (0, 39))

	def test_firstCellOfSecondSegment(self):
		self.assertEqual(findSegmentAtWindowPos(self.rects, 40, FOCUS_COLS), (1, 0))

	def test_lastCellOfDisplayIsRoutable(self):
		"""The 2023 prototype swallowed cell 79 as a debug hook. It must route normally."""
		self.assertEqual(findSegmentAtWindowPos(self.rects, 79, FOCUS_COLS), (1, 39))

	def test_positionBeyondDisplayRaises(self):
		with self.assertRaises(LookupError):
			findSegmentAtWindowPos(self.rects, 80, FOCUS_COLS)


class TestRoutingOnMultiRow(unittest.TestCase):
	"""Routing key mapping on a Monarch split into four two row segments."""

	def setUp(self):
		self.rects = calculateSegmentRects(MONARCH_ROWS, MONARCH_COLS, 4)

	def test_firstCell(self):
		self.assertEqual(findSegmentAtWindowPos(self.rects, 0, MONARCH_COLS), (0, 0))

	def test_secondRowStaysInFirstSegment(self):
		# Row 1, column 0 is the 33rd cell, and belongs to the segment covering rows 0 and 1.
		self.assertEqual(findSegmentAtWindowPos(self.rects, 32, MONARCH_COLS), (0, 32))

	def test_thirdRowMovesToSecondSegment(self):
		# Row 2, column 0 is the 65th cell, the start of the second segment.
		self.assertEqual(findSegmentAtWindowPos(self.rects, 64, MONARCH_COLS), (1, 0))

	def test_lastCellOfDisplay(self):
		lastPos = MONARCH_ROWS * MONARCH_COLS - 1
		self.assertEqual(findSegmentAtWindowPos(self.rects, lastPos, MONARCH_COLS), (3, 63))

	def test_positionBeyondDisplayRaises(self):
		with self.assertRaises(LookupError):
			findSegmentAtWindowPos(self.rects, MONARCH_ROWS * MONARCH_COLS, MONARCH_COLS)


class TestRoundTrip(unittest.TestCase):
	"""Segment position and window position must be inverses of each other."""

	def test_monarchRoundTrip(self):
		for layout in (1, 2, 4, 8, [1, 5, 2]):
			rects = calculateSegmentRects(MONARCH_ROWS, MONARCH_COLS, layout)
			for windowPos in range(MONARCH_ROWS * MONARCH_COLS):
				with self.subTest(layout=layout, windowPos=windowPos):
					index, segmentPos = findSegmentAtWindowPos(rects, windowPos, MONARCH_COLS)
					self.assertEqual(
						segmentPosToWindowPos(rects[index], segmentPos, MONARCH_COLS),
						windowPos,
					)

	def test_focusRoundTrip(self):
		for layout in (1, 2, 3, [20, 60]):
			rects = calculateSegmentRects(FOCUS_ROWS, FOCUS_COLS, layout)
			for windowPos in range(FOCUS_COLS):
				with self.subTest(layout=layout, windowPos=windowPos):
					index, segmentPos = findSegmentAtWindowPos(rects, windowPos, FOCUS_COLS)
					self.assertEqual(
						segmentPosToWindowPos(rects[index], segmentPos, FOCUS_COLS),
						windowPos,
					)

	def test_rejectsPositionOutsideSegment(self):
		rect = SegmentRect(row=0, col=0, numRows=1, numCols=40)
		with self.assertRaises(ValueError):
			segmentPosToWindowPos(rect, 40, FOCUS_COLS)
