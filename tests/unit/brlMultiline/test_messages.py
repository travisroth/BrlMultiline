# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for NVDA's flash messages confined to a segment.

The case that started this was found on hardware: on a Monarch above a Focus 80, a flash
message appeared on the Monarch rather than beside the focus. That is the visible half. The
half that loses text is that NVDA's own message buffer wraps to the *display's* width, which
on that composite is 80, while the Monarch's rows only have 32 cells that reach hardware. So
the tests here are as much about what does not appear past a segment's edge as about where
the message lands.
"""

import unittest

from ._stubs import CONFIG, FakeHandler, Region, installStubs, resetConfig

installStubs()

from brlMultiline.layout import SegmentRect  # noqa: E402
from brlMultiline.messages import MessageBuffer  # noqa: E402

MONARCH_BAND = SegmentRect(row=0, col=0, numRows=8, numCols=32)
FOCUS_BAND = SegmentRect(row=8, col=0, numRows=1, numCols=80)
COMPOSITE_ROWS = 9
COMPOSITE_COLS = 80


class MessageBufferTestCase(unittest.TestCase):
	def setUp(self):
		resetConfig()
		self.handler = FakeHandler(COMPOSITE_ROWS, COMPOSITE_COLS)
		self.buffer = MessageBuffer(self.handler, FOCUS_BAND)

	def tearDown(self):
		resetConfig()

	def show(self, text, buffer=None):
		"""Put a message in the buffer, as `BrailleHandler.message` does."""
		buffer = buffer or self.buffer
		buffer.clear()
		region = Region(text)
		region.update()
		buffer.regions.append(region)
		buffer.update()
		return buffer.windowBrailleCells

	def occupied(self, cells):
		""":return: the positions of every cell with dots raised."""
		return [index for index, cell in enumerate(cells) if cell]


class TestPlacement(MessageBufferTestCase):
	def test_theMessageIsTheSizeOfTheWholeDisplay(self):
		"""The handler pads what it is given, so anything short would land at the top left."""
		self.assertEqual(len(self.show("hi")), COMPOSITE_ROWS * COMPOSITE_COLS)

	def test_aMessageLandsInItsSegment(self):
		"""Row 8 is the Focus, so the message starts at cell 640 rather than cell 0."""
		self.assertEqual(self.occupied(self.show("hi")), [640, 641])

	def test_nothingLandsOutsideTheSegment(self):
		cells = self.show("a message of some length")
		for position in self.occupied(cells):
			self.assertGreaterEqual(position, 8 * COMPOSITE_COLS)

	def test_aMessageInTheTopSegmentStartsAtTheTopLeft(self):
		"""Which is NVDA's own behaviour, and has to remain reachable."""
		buffer = MessageBuffer(self.handler, MONARCH_BAND)
		self.assertEqual(self.occupied(self.show("hi", buffer)), [0, 1])


class TestDeadColumns(MessageBufferTestCase):
	"""The defect this class exists for: text written where no hardware is.

	The Monarch's band is 32 cells wide on a composite 80 wide, so cells 32 to 79 of each of
	its rows reach nothing. NVDA's own message buffer wraps at 80 and writes straight through
	them.
	"""

	def setUp(self):
		super().setUp()
		self.buffer = MessageBuffer(self.handler, MONARCH_BAND)

	def test_aLongMessageDoesNotReachThem(self):
		cells = self.show("A" * 32 + "MISSING")
		for position in self.occupied(cells):
			self.assertLess(position % COMPOSITE_COLS, 32, f"cell {position} is past the Monarch")

	def test_theOverhangGoesToTheNextRowRatherThanNowhere(self):
		"""It is not enough that nothing is written past the edge; it has to appear somewhere."""
		cells = self.show("A" * 32 + "B" * 8)
		self.assertEqual(len(self.occupied(cells)), 40)
		# 32 on the first row of the segment, 8 on the second.
		self.assertEqual(len([p for p in self.occupied(cells) if p < COMPOSITE_COLS]), 32)
		self.assertEqual(
			len([p for p in self.occupied(cells) if COMPOSITE_COLS <= p < 2 * COMPOSITE_COLS]),
			8,
		)

	def test_aMessageWiderThanTheDisplayIsUnaffected(self):
		"""The Focus is the full width, so its segment has no dead columns to avoid."""
		buffer = MessageBuffer(self.handler, FOCUS_BAND)
		cells = self.show("A" * 80, buffer)
		self.assertEqual(len(self.occupied(cells)), 80)


class TestSegmentWindow(MessageBufferTestCase):
	"""What the rectangle proxy buys, asserted directly.

	Placing cells correctly is only half of it, and the cheaper half. The half that stops
	text being lost is that NVDA lays the message out believing the display *is* the segment,
	so it never wraps a row wider than the hardware under it and never windows more text than
	the segment can hold.
	"""

	def test_theBufferBelievesTheDisplayIsItsSegment(self):
		buffer = MessageBuffer(self.handler, MONARCH_BAND)
		self.assertEqual(buffer.handler.displayDimensions.numCols, 32)
		self.assertEqual(buffer.handler.displaySize, MONARCH_BAND.displaySize)

	def test_theRealDisplayIsStillReachableForPlacement(self):
		self.assertEqual(self.handler.displayDimensions.numCols, COMPOSITE_COLS)

	def test_aMessageLongerThanTheSegmentIsWindowedToIt(self):
		"""80 cells of the Focus band, not 720 cells of the composite."""
		cells = self.show("A" * 100)
		self.assertEqual(len(self.occupied(cells)), FOCUS_BAND.displaySize)

	def test_everythingElseIsForwardedToTheRealHandler(self):
		"""The proxy replaces the geometry and nothing else."""
		self.assertIs(self.buffer.handler.mainBuffer, self.handler.mainBuffer)


class TestRetargeting(MessageBufferTestCase):
	"""The object is reused rather than replaced, because the handler compares identity."""

	def test_movingTheSegmentMovesTheMessage(self):
		self.show("hi")
		self.buffer.setRect(MONARCH_BAND)
		self.assertEqual(self.occupied(self.buffer.windowBrailleCells), [0, 1])

	def test_theSameRectangleIsNotReLaidOut(self):
		self.show("hi")
		before = self.buffer.windowBrailleCells
		self.buffer.setRect(FOCUS_BAND)
		self.assertEqual(self.buffer.windowBrailleCells, before)

	def test_movingWithNoMessageShowingIsFine(self):
		self.buffer.setRect(MONARCH_BAND)
		self.assertEqual(self.occupied(self.buffer.windowBrailleCells), [])


class TestRouting(MessageBufferTestCase):
	"""NVDA dismisses the message straight afterwards, but the translation has to be right."""

	def test_aPressInTheSegmentRoutesWithinTheMessage(self):
		self.show("a message")
		self.buffer.routeTo(640 + 3)
		self.assertEqual(self.buffer.routedTo, 3)

	def test_aPressElsewhereOnTheDisplayIsIgnored(self):
		self.show("a message")
		self.buffer.routeTo(5)
		self.assertIsNone(self.buffer.routedTo)


class TestCursor(MessageBufferTestCase):
	def test_aMessageWithNoCursorReportsNone(self):
		self.show("hi")
		self.assertIsNone(self.buffer.cursorWindowPos)

	def test_aCursorIsReportedOnTheWholeDisplay(self):
		self.show("hi")
		self.buffer.cursorPos = 1
		self.assertEqual(self.buffer.cursorWindowPos, 641)


class TestStubFidelity(unittest.TestCase):
	"""These tests are worth nothing if the stub buffer shadows the overrides.

	NVDA's `BrailleBuffer` is an `AutoPropertyObject`, so `_get_windowBrailleCells` on a
	subclass replaces the parent's. A stub declaring `windowBrailleCells` with `@property`
	instead would win over the subclass and every test above would pass while exercising the
	stub. Asserting the wiring is cheaper than discovering that later.
	"""

	def test_theOverriddenGetterIsTheOneThatRuns(self):
		self.assertIs(
			MessageBuffer.windowBrailleCells.fget,
			MessageBuffer._get_windowBrailleCells,
		)

	def test_theInheritedGetterIsStillReachable(self):
		from braille.buffers import BrailleBuffer

		self.assertTrue(hasattr(BrailleBuffer, "_get_windowBrailleCells"))
