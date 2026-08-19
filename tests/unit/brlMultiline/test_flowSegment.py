# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for showing a flow in a segment of the display.

The question this milestone exists to answer is whether NVDA's own window handling can be
kept out of a flow. NVDA saves and restores the window around every pending update,
scrolls to the cursor after every caret move, and focuses a region on every new object,
and each of those would drag the band off the rows the reader is holding.
"""

import unittest

from ._stubs import CursorManagerRegion, FakeHandler, FakeTreeInterceptor, installStubs

installStubs()

from brlMultiline.container import DisplayContainer  # noqa: E402
from brlMultiline.flowControl import FlowController  # noqa: E402
from brlMultiline.flowRender import FlowRenderer  # noqa: E402
from brlMultiline.flowSegment import FlowBufferSegment  # noqa: E402
from brlMultiline.flowSources import DocumentFlowSource, regionFactoryFor  # noqa: E402
from brlMultiline.layout import SegmentRect  # noqa: E402
from brlMultiline.panels import FlowPanel, SinglePanel  # noqa: E402
from brlMultiline.views import SegmentView  # noqa: E402

ROWS = 4
COLS = 8


def documentLines(count=40):
	return [f"line {number}" for number in range(count)]


def bandView(numRows=ROWS, numCols=COLS) -> SegmentView:
	"""A view whose whole display is one flow band."""
	panel = FlowPanel("flow", SegmentRect(row=0, col=0, numRows=numRows, numCols=numCols))
	return SegmentView(name="flow", panels=[panel], focusSegmentKey="flow")


def containerWithBand(handler=None, numRows=ROWS, numCols=COLS) -> DisplayContainer:
	handler = handler if handler is not None else FakeHandler(numRows, numCols)
	return DisplayContainer(handler, bandView(numRows, numCols))


def controllerOver(lines, caretIndex=0, numRows=ROWS, numCols=COLS, live=True, handler=None):
	interceptor = FakeTreeInterceptor(lines, caretIndex=caretIndex)
	source = DocumentFlowSource(
		interceptor,
		regionFactoryFor(CursorManagerRegion(interceptor), live=live),
		generation=1,
	)
	renderer = FlowRenderer(handler or FakeHandler(ROWS, COLS), numCols=numCols, fillRows=True)
	control = FlowController(source, renderer, numRows=numRows, live=live)
	control.enterAtCursor()
	return control


def attachedBand(lines=None, caretIndex=0):
	"""A container whose band is showing a flow, and the segment and controller behind it."""
	handler = FakeHandler(ROWS, COLS)
	container = containerWithBand(handler)
	handler.mainBuffer = handler.buffer = container
	segment = container.segmentForKey("flow")
	control = controllerOver(lines or documentLines(), caretIndex=caretIndex, handler=handler)
	segment.attach(control)
	return container, segment, control


def bandRows(segment) -> list[str]:
	cells = segment.windowBrailleCells
	return [
		"".join(chr(cell) if cell else " " for cell in cells[index * COLS : (index + 1) * COLS])
		for index in range(ROWS)
	]


class TestTheBandIsAFlowSegment(unittest.TestCase):
	def test_theContainerBuildsAFlowSegmentForAnExclusiveSpec(self):
		container = containerWithBand()
		self.assertIsInstance(container.segmentForKey("flow"), FlowBufferSegment)

	def test_anOrdinarySpecStillBuildsAnOrdinarySegment(self):
		view = SegmentView(
			name="plain",
			panels=[SinglePanel("plain", SegmentRect(row=0, col=0, numRows=ROWS, numCols=COLS))],
			focusSegmentKey="plain",
		)
		container = DisplayContainer(FakeHandler(ROWS, COLS), view)
		self.assertNotIsInstance(container.segmentForKey("plain"), FlowBufferSegment)

	def test_anUnattachedBandIsBlankRatherThanBroken(self):
		container = containerWithBand()
		segment = container.segmentForKey("flow")
		self.assertFalse(segment.isFlowing)
		container.update()
		self.assertEqual(len(container.windowBrailleCells), ROWS * COLS)


class TestWhatTheBandShows(unittest.TestCase):
	def test_theBandShowsTheFlowsCells(self):
		_, segment, _ = attachedBand(["Heading", "body text here"])
		self.assertEqual(bandRows(segment)[:3], ["Heading ", "body tex", "t here  "])

	def test_theBandFillsTheWholeRectangleWhateverTheFlowSays(self):
		_, segment, _ = attachedBand(["short"])
		self.assertEqual(len(segment.windowBrailleCells), ROWS * COLS)

	def test_theContainerCompositesTheBand(self):
		container, segment, _ = attachedBand(["Heading", "body"])
		container.update()
		cells = container.windowBrailleCells
		self.assertEqual(cells[:7], [ord(character) for character in "Heading"])

	def test_detachingLeavesTheBandBlank(self):
		# A detached band is an ordinary empty segment again, and the container composites
		# the cells it does not cover as blank.
		container, segment, _ = attachedBand(["Heading"])
		segment.detach()
		container.update()
		self.assertEqual(container.windowBrailleCells, [0] * (ROWS * COLS))


class TestNVDACannotMoveTheWindow(unittest.TestCase):
	"""The point of the milestone: the anchor owns the window, not the buffer."""

	def test_savingAndRestoringTheWindowChangesNothing(self):
		_, segment, control = attachedBand()
		control.panForward()
		before = bandRows(segment)
		segment.saveWindow()
		segment.restoreWindow()
		self.assertEqual(bandRows(segment), before)

	def test_scrollingToARegionChangesNothing(self):
		# scrollToCursorOrSelection calls this after every caret move.
		_, segment, control = attachedBand()
		control.panForward()
		before = bandRows(segment)
		segment.scrollTo(segment.commandRegion, 0)
		self.assertEqual(bandRows(segment), before)

	def test_focusingARegionChangesNothing(self):
		_, segment, control = attachedBand()
		control.panForward()
		before = bandRows(segment)
		segment.focus(segment.commandRegion)
		self.assertEqual(bandRows(segment), before)

	def test_aWholePendingUpdateCycleLeavesTheWindowWhereItWas(self):
		# What `_handlePendingUpdate` does, in the order it does it.
		container, segment, control = attachedBand()
		control.panForward()
		before = bandRows(segment)
		container.saveWindow()
		container.update()
		container.restoreWindow()
		self.assertEqual(bandRows(segment), before)


class TestCommandsReachTheFlow(unittest.TestCase):
	def test_scrollingTheSegmentPansTheFlow(self):
		_, segment, control = attachedBand()
		first = bandRows(segment)
		segment.scrollForward()
		self.assertNotEqual(bandRows(segment), first)
		segment.scrollBack()
		self.assertEqual(bandRows(segment), first)

	def test_theLastRegionIsTheStandInRatherThanNothing(self):
		# NVDA reaches for regions[-1] in nine places. A band with no regions would give
		# every one of them nothing to act on.
		container, segment, _ = attachedBand()
		self.assertIs(container.regions[-1], segment.commandRegion)

	def test_theStandInStepsABlockRatherThanScrolling(self):
		_, segment, control = attachedBand()
		before = control.activeBlockId
		segment.commandRegion.nextLine()
		self.assertNotEqual(control.activeBlockId, before)

	def test_theStandInCarriesTheObjectSoNVDARecognisesIt(self):
		_, segment, control = attachedBand()
		self.assertIs(segment.commandRegion.obj, control.source.obj)

	def test_aCaretMoveFollowsTheCursor(self):
		container, segment, control = attachedBand()
		# The reader arrows down past the bottom of the band.
		control.source.obj.caretIndex = 6
		segment.commandRegion.update()
		self.assertEqual(bandRows(segment)[-1], "line 6  ")

	def test_aCaretMoveOntoSomethingAlreadyShownMovesNothing(self):
		container, segment, control = attachedBand()
		before = bandRows(segment)
		control.source.obj.caretIndex = 2
		segment.commandRegion.update()
		self.assertEqual(bandRows(segment), before)

	def test_routingReachesTheBlockUnderTheKey(self):
		_, segment, control = attachedBand(["abc", "def"])
		segment.routeTo(9)
		second = control.window.blocks[1].blockId
		self.assertEqual(control.regionFor(second).routedTo, 1)


class TestFocusChanges(unittest.TestCase):
	def test_theBandTakesAFocusChangeInsteadOfNVDAsRegions(self):
		_, segment, control = attachedBand()
		self.assertTrue(segment.acceptFocusRegions([CursorManagerRegion(control.source.obj)]))

	def test_anUnattachedBandRefusesIt(self):
		container = containerWithBand()
		segment = container.segmentForKey("flow")
		self.assertFalse(segment.acceptFocusRegions([]))

	def test_clearingKeepsTheStandIn(self):
		_, segment, _ = attachedBand()
		segment.clear()
		self.assertIn(segment.commandRegion, segment.regions)


if __name__ == "__main__":
	unittest.main()
