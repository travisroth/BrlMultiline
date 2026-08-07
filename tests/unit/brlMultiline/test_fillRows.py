# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for filling every row of a segment rather than wrapping at word boundaries.

A segment 8 cells wide has no cells to spare for keeping words whole, so the fill mode
runs text straight on from one row to the next. This is NVDA's own `BrailleTextWrapFlag`
NONE behaviour, reimplemented here because NVDA reads that setting from the global
configuration rather than through the braille handler, which means a segment cannot be
given a different rule by the handler proxy.
"""

import os
import sys
import unittest

sys.path.insert(
	0,
	os.path.join(os.path.dirname(__file__), "..", "..", "..", "addon", "globalPlugins", "brlMultiline"),
)

from layout import calculateFilledRowOffsets  # noqa: E402


class TestFilledRowOffsets(unittest.TestCase):
	def test_fillsEveryRowCompletely(self):
		"""A 3 by 8 segment over a long buffer uses all 24 cells."""
		rows = calculateFilledRowOffsets(bufferLength=100, startPos=0, numRows=3, numCols=8)
		self.assertEqual(rows, [(0, 8, False), (8, 16, False), (16, 24, False)])

	def test_noCellsAreSkippedBetweenRows(self):
		rows = calculateFilledRowOffsets(bufferLength=100, startPos=0, numRows=4, numCols=7)
		for previous, following in zip(rows, rows[1:]):
			self.assertEqual(previous[1], following[0])

	def test_startsWhereTheWindowStarts(self):
		rows = calculateFilledRowOffsets(bufferLength=100, startPos=16, numRows=2, numCols=8)
		self.assertEqual(rows, [(16, 24, False), (24, 32, False)])

	def test_stopsAtTheEndOfTheBuffer(self):
		"""Content shorter than the segment produces fewer rows, not padded ones."""
		rows = calculateFilledRowOffsets(bufferLength=12, startPos=0, numRows=3, numCols=8)
		self.assertEqual(rows, [(0, 8, False), (8, 12, False)])

	def test_contentShorterThanOneRow(self):
		rows = calculateFilledRowOffsets(bufferLength=5, startPos=0, numRows=3, numCols=8)
		self.assertEqual(rows, [(0, 5, False)])

	def test_emptyBuffer(self):
		rows = calculateFilledRowOffsets(bufferLength=0, startPos=0, numRows=3, numCols=8)
		self.assertEqual(rows, [(0, 0, False)])

	def test_startBeyondTheBufferIsClamped(self):
		rows = calculateFilledRowOffsets(bufferLength=10, startPos=50, numRows=2, numCols=8)
		self.assertEqual(rows, [(10, 10, False)])

	def test_neverReturnsNoRows(self):
		for numRows in (0, 1, 5):
			with self.subTest(numRows=numRows):
				self.assertTrue(calculateFilledRowOffsets(100, 0, numRows, 8))


class TestFilledRowOffsetsWithCutMarks(unittest.TestCase):
	"""With cut marking on, a row cut mid word gives up its last cell to a marker."""

	@staticmethod
	def cutAt(*positions):
		"""Build a mid word test that reports a cut at the given buffer positions."""
		return lambda end: end in positions

	def test_marksAndShortensARowCutMidWord(self):
		rows = calculateFilledRowOffsets(100, 0, 2, 8, self.cutAt(8))
		self.assertEqual(rows[0], (0, 7, True))

	def test_theNextRowStartsWhereTheShortenedRowEnded(self):
		rows = calculateFilledRowOffsets(100, 0, 2, 8, self.cutAt(8))
		self.assertEqual(rows[1][0], 7)

	def test_rowsNotCutMidWordAreUnchanged(self):
		rows = calculateFilledRowOffsets(100, 0, 3, 8, self.cutAt(8))
		self.assertEqual(rows[1], (7, 15, False))

	def test_noMarkerOnTheLastRowOfTheBuffer(self):
		"""There is no cut when the text simply ends."""
		rows = calculateFilledRowOffsets(12, 0, 3, 8, self.cutAt(8, 12))
		self.assertEqual(rows[-1], (7, 12, False))

	def test_withoutTheTestNothingIsMarked(self):
		rows = calculateFilledRowOffsets(100, 0, 3, 8, None)
		self.assertTrue(all(not row[2] for row in rows))
