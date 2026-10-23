# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for how often a frame compares two bookmarks.

In Word a bookmark is a copy of the position, and comparing two is a call into Word. Working the
display out per cell compared every cell's block with the blocks ahead of it, several thousand
calls a keystroke on a Monarch, and that was the arrowing in Word that froze NVDA. These count the
comparisons rather than time them, because the count is what was wrong.
"""

import contextlib
import unittest

from ._stubs import FakeBookmark, installStubs

installStubs()

from brlMultiline.flow import ByIdentity, findByIdentityFirst  # noqa: E402

from .test_flowControl import controllerOver  # noqa: E402


@contextlib.contextmanager
def countingComparisons():
	""":return: a list that grows by one for every bookmark comparison made inside the block."""
	calls = []
	original = FakeBookmark.__eq__

	def counted(self, other):
		calls.append(1)
		return original(self, other)

	FakeBookmark.__eq__ = counted
	try:
		yield calls
	finally:
		FakeBookmark.__eq__ = original


class Key:
	"""A key whose equality is counted, as a bookmark's call into another process would be."""

	asked = 0

	def __init__(self, value):
		self.value = value

	def __eq__(self, other):
		Key.asked += 1
		return isinstance(other, Key) and other.value == self.value

	__hash__ = None


class TestFindingByIdentityFirst(unittest.TestCase):
	def setUp(self):
		Key.asked = 0

	def test_theSameObjectIsFoundWithoutAskingAnyone(self):
		keys = [Key(number) for number in range(10)]
		self.assertEqual(findByIdentityFirst(keys, keys[7]), 7)
		self.assertEqual(Key.asked, 0)

	def test_anEqualKeyIsStillFound(self):
		# A block read again: equal to the one held, and not the same object.
		keys = [Key(number) for number in range(10)]
		self.assertEqual(findByIdentityFirst(keys, Key(7)), 7)

	def test_aMissingKeyIsNone(self):
		self.assertIsNone(findByIdentityFirst([Key(1)], Key(2)))

	def test_theTableAsksNobodyForAKeyItHolds(self):
		table = ByIdentity()
		keys = [Key(number) for number in range(10)]
		for key in keys:
			table.set(key, key.value)
		Key.asked = 0
		self.assertEqual(table.get(keys[8]), 8)
		table.set(keys[8], "again")
		self.assertEqual(table.pop(keys[8]), "again")
		self.assertEqual(Key.asked, 0)
		self.assertIsNone(table.get(keys[8]))

	def test_theTableStillAnswersByEquality(self):
		table = ByIdentity()
		table.set(Key(3), "three")
		self.assertEqual(table.get(Key(3)), "three")
		table.set(Key(3), "replaced")
		self.assertEqual(table.pop(Key(3)), "replaced")


class TestAFrameComparesFewBookmarks(unittest.TestCase):
	ROWS = 8

	def flow(self):
		lines = [f"line {number} of the document" for number in range(30)]
		return controllerOver(lines, caretIndex=5, numRows=self.ROWS, numCols=32, live=True)

	def test_findingTheCursorAsksAlmostNothing(self):
		control = self.flow()
		with countingComparisons() as calls:
			self.assertIsNotNone(control.cursorCell())
		self.assertLess(len(calls), 20, f"{len(calls)} comparisons for one cursor")

	def test_findingTheGlyphsAsksAlmostNothing(self):
		control = self.flow()
		with countingComparisons() as calls:
			control.cellGlyphs()
		self.assertLess(len(calls), 20, f"{len(calls)} comparisons for one frame's glyphs")

	def test_aFrameAfterAReReadAsksAlmostNothing(self):
		# From the review of 786be5c: a re-read stored equal ids that were not the same objects
		# as the ones the renderings and the anchor carry, and every lookup then asked.
		control = self.flow()
		control.rereadContent()
		with countingComparisons() as calls:
			control.cellGlyphs()
			control.cursorCell()
			control.cells()
		self.assertLess(len(calls), 20, f"{len(calls)} comparisons for a frame after a re-read")

	def test_aFrameAfterCursorMovesAsksAlmostNothing(self):
		control = self.flow()
		for index in (6, 7, 8, 7):
			control.source.obj.caretIndex = index
			control.followCursor()
		with countingComparisons() as calls:
			control.cellGlyphs()
			control.cursorCell()
			control.cells()
		self.assertLess(len(calls), 20, f"{len(calls)} comparisons for a frame after moving")

	def test_aReReadKeepsTheIdsTheBandAlreadyHas(self):
		control = self.flow()
		before = [block.blockId for block in control.window.blocks]
		control.rereadContent()
		held = [control.blocks.get(blockId).blockId for blockId in before]
		self.assertTrue(all(new is old for new, old in zip(held, before)))

	def test_theCellsAskAlmostNothing(self):
		control = self.flow()
		with countingComparisons() as calls:
			control.cells()
		self.assertLess(len(calls), 20, f"{len(calls)} comparisons for one frame's cells")


if __name__ == "__main__":
	unittest.main()
