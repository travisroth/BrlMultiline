# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for showing a flow in a segment of the display.

The question this milestone exists to answer is whether NVDA's own window handling can be
kept out of a flow. NVDA saves and restores the window around every pending update,
scrolls to the cursor after every caret move, and focuses a region on every new object,
and each of those would drag the band off the rows the reader is holding.
"""

import unittest

from ._stubs import (
	CursorManagerRegion,
	FakeHandler,
	FakeNavigatorObject,
	FakeTreeInterceptor,
	installStubs,
)

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
		segment.scrollTo(segment.regions[-1], 0)
		self.assertEqual(bandRows(segment), before)

	def test_focusingARegionChangesNothing(self):
		_, segment, control = attachedBand()
		control.panForward()
		before = bandRows(segment)
		segment.focus(segment.regions[-1])
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

	def test_theLastRegionIsTheActiveBlocksOwnRegion(self):
		# NVDA reaches for regions[-1] in nine places, and on a single line display that is
		# the block the cursor is in. A band puts the same thing there.
		container, segment, control = attachedBand()
		self.assertIs(container.regions[-1], control.activeRegion())

	def test_theLastRegionIsARealTextRegion(self):
		# Braille input only writes to the last region when it is a TextInfoRegion, so a
		# stand-in of our own would swallow untranslated dots and break the erase that
		# checks them.
		from braille.regions.textInfo import TextInfoRegion

		container, _, _ = attachedBand()
		self.assertIsInstance(container.regions[-1], TextInfoRegion)

	def test_theLastRegionFollowsTheActiveBlock(self):
		container, segment, control = attachedBand()
		first = container.regions[-1]
		control.stepBlock(forward=True)
		segment.refresh()
		self.assertIsNot(container.regions[-1], first)
		self.assertIs(container.regions[-1], control.activeRegion())

	def test_nvdasLineCommandMovesTheBrowseModeCursor(self):
		# What script_braille_nextLine does: regions[-1].nextLine(). In a live flow that is
		# a live region, so the cursor moves and the band follows it.
		container, segment, control = attachedBand()
		self.assertEqual(control.source.obj.caretIndex, 0)
		container.regions[-1].nextLine()
		self.assertEqual(control.source.obj.caretIndex, 1)

	def test_steppingABlockMovesTheBrowseModeCursorToo(self):
		# Selecting a block and moving the cursor to it are two things, and only the second
		# makes speech follow and the arrow keys carry on from there.
		_, segment, control = attachedBand()
		control.stepBlock(forward=True)
		self.assertEqual(control.source.obj.caretIndex, 1)

	def test_theLastRegionCarriesTheObjectSoNVDARecognisesIt(self):
		container, segment, control = attachedBand()
		self.assertIs(container.regions[-1].obj, control.source.obj)

	def test_aCaretMoveFollowsTheCursor(self):
		container, segment, control = attachedBand()
		# The reader arrows down past the bottom of the band. NVDA re-reads the queued
		# region and then updates the buffer, which is where a flow hears about it.
		control.source.obj.caretIndex = 6
		container.regions[-1].update()
		segment.update()
		self.assertEqual(bandRows(segment)[-1], "line 6  ")

	def test_aCaretMoveOntoSomethingAlreadyShownMovesNothing(self):
		container, segment, control = attachedBand()
		before = bandRows(segment)
		control.source.obj.caretIndex = 2
		container.regions[-1].update()
		segment.update()
		self.assertEqual(bandRows(segment), before)

	def test_routingReachesTheBlockUnderTheKey(self):
		_, segment, control = attachedBand(["abc", "def"])
		segment.routeTo(9)
		second = control.window.blocks[1].blockId
		self.assertEqual(control.regionFor(second).routedTo, 1)


class TestThroughThePatch(unittest.TestCase):
	"""A focus change as NVDA delivers it, through the patched `_doNewObject`.

	The path that mattered and was not covered: a flow claiming the whole display is one
	segment, and the patch used to hand every one segment container back to NVDA, which
	cleared the band and appended its own regions to it.
	"""

	def setUp(self):
		from brlMultiline import patches

		patches.install()
		self.addCleanup(patches.remove)

	def _newObject(self, container, handler, regions):
		from brlMultiline import patches

		handler.mainBuffer = handler.buffer = container
		patches._doNewObjectMultiSegment(handler, regions)

	def test_aFocusChangeIsOfferedToTheBandRatherThanWrittenIntoIt(self):
		container, segment, control = attachedBand()
		handler = container.handler
		nvdaRegion = CursorManagerRegion(control.source.obj)
		self._newObject(container, handler, [nvdaRegion])
		self.assertNotIn(nvdaRegion, segment.regions)
		self.assertIs(container.regions[-1], control.activeRegion())

	def test_anOrdinaryOneSegmentDisplayStillGoesToNVDA(self):
		view = SegmentView(
			name="plain",
			panels=[SinglePanel("plain", SegmentRect(row=0, col=0, numRows=ROWS, numCols=COLS))],
			focusSegmentKey="plain",
		)
		handler = FakeHandler(ROWS, COLS)
		container = DisplayContainer(handler, view)
		from brlMultiline import patches

		delegated = []
		patches._originals["_doNewObject"] = lambda handlerArg, regionsArg: delegated.append(regionsArg)
		region = CursorManagerRegion(FakeTreeInterceptor(documentLines()))
		self._newObject(container, handler, [region])
		self.assertEqual(delegated, [[region]])

	def test_aFlowOnAOneSegmentDisplayIsNotDelegated(self):
		from brlMultiline import patches

		container, segment, control = attachedBand()
		delegated = []
		patches._originals["_doNewObject"] = lambda handlerArg, regionsArg: delegated.append(regionsArg)
		self._newObject(container, container.handler, [CursorManagerRegion(control.source.obj)])
		self.assertEqual(delegated, [])


class TestFocusChanges(unittest.TestCase):
	def test_theBandTakesAFocusChangeInsteadOfNVDAsRegions(self):
		_, segment, control = attachedBand()
		self.assertTrue(segment.acceptFocusRegions([CursorManagerRegion(control.source.obj)]))

	def test_anUnattachedBandRefusesIt(self):
		container = containerWithBand()
		segment = container.segmentForKey("flow")
		self.assertFalse(segment.acceptFocusRegions([]))

	def test_clearingKeepsTheActiveRegion(self):
		_, segment, control = attachedBand()
		segment.clear()
		self.assertEqual(segment.regions, [control.activeRegion()])


class FakePlugin:
	"""Enough plugin for a band to claim part of the display and be rebuilt."""

	def __init__(self, container):
		self.container = container
		self.claimed = []

	def activatePanel(self, panel):
		self.claimed.append(panel.name)

	def deactivatePanel(self, name):
		self.claimed = [claim for claim in self.claimed if claim != name]


class TestFollowingTheFocus(unittest.TestCase):
	"""A flow shows the document the focus is in, and follows it to the next one."""

	def setUp(self):
		import api
		from brlMultiline.flowBand import FlowBand

		self.handler = FakeHandler(ROWS, COLS)
		self.container = containerWithBand(self.handler)
		self.handler.mainBuffer = self.handler.buffer = self.container
		self.segment = self.container.segmentForKey("flow")
		self.plugin = FakePlugin(self.container)
		self.band = FlowBand(self.plugin)
		self.previousFocus = api.getFocusObject
		self.addCleanup(setattr, api, "getFocusObject", self.previousFocus)
		self.api = api

	def _focusOn(self, obj):
		self.api.getFocusObject = lambda: obj
		return obj

	def _document(self, lines=None, caretIndex=0):
		interceptor = FakeTreeInterceptor(lines or documentLines(), caretIndex=caretIndex)
		return FakeNavigatorObject("a page", treeInterceptor=interceptor), interceptor

	def _start(self, obj):
		self._focusOn(obj)
		self.band._follow()
		return self.band.refresh(force=True)

	def _focusRegionsFor(self, obj):
		"""What NVDA hands the band on a focus change: its regions for the new focus."""
		from ._stubs import fakeGetFocusRegions
		from brlMultiline.objectMonitor import resolveTarget

		return fakeGetFocusRegions(resolveTarget(obj))

	def test_theBandReadsWhereTheFocusIsNotWhereTheNavigatorIs(self):
		# The band is the focus segment, so it shows the document being worked in, while
		# the navigator object is wherever it was last sent.
		focused, interceptor = self._document(["focused document"])
		self.assertTrue(self._start(focused))
		self.assertIs(self.band.controller.source.obj, interceptor)

	def test_aFocusChangeToAnotherDocumentReadsTheNewOne(self):
		first, firstInterceptor = self._document(["first document"])
		self._start(first)
		second, secondInterceptor = self._document(["second document"])
		self._focusOn(second)
		self.assertTrue(self.segment.acceptFocusRegions(self._focusRegionsFor(second)))
		self.assertIs(self.band.controller.source.obj, secondInterceptor)
		# Asserted on what the band is reading rather than on its cells: a band built
		# through `buildController` wraps at word boundaries, which the harness's buffer
		# does not implement, so its cells are empty here and are not in NVDA.
		self.assertEqual(self.band.controller.window.blocks[0].rawText, "second document")

	def test_aFocusChangeWithinTheSameDocumentKeepsTheReading(self):
		# The controller holds the blocks already read and the positions they came from,
		# so moving within a document must not throw them away.
		page, _ = self._document(documentLines())
		self._start(page)
		control = self.band.controller
		self.assertTrue(self.segment.acceptFocusRegions(self._focusRegionsFor(page)))
		self.assertIs(self.band.controller, control)

	def test_anotherDocumentGetsItsOwnGeneration(self):
		# A bookmark is a position within one document and says nothing about which, so a
		# stale anchor must never match a block in the document now being read.
		first, _ = self._document(["first"])
		self._start(first)
		wasGeneration = self.band.controller.source.generation
		second, _ = self._document(["second"])
		self._focusOn(second)
		self.segment.acceptFocusRegions(self._focusRegionsFor(second))
		self.assertNotEqual(self.band.controller.source.generation, wasGeneration)

	def test_focusOnSomethingWithNoTextIsGivenBackToNVDA(self):
		# A button in a dialog has no lines to flow. The band becomes an ordinary segment
		# again rather than leaving the reader with a blank display.
		page, _ = self._document(documentLines())
		self._start(page)
		button = self._focusOn(FakeNavigatorObject("a button"))
		self.assertFalse(self.segment.acceptFocusRegions(self._focusRegionsFor(button)))
		self.assertFalse(self.segment.isFlowing)
		self.assertIsNone(self.band.controller)

	def test_comingBackToADocumentReadsItAgain(self):
		page, interceptor = self._document(documentLines())
		self._start(page)
		button = self._focusOn(FakeNavigatorObject("a button"))
		self.segment.acceptFocusRegions(self._focusRegionsFor(button))
		self._focusOn(page)
		self.assertTrue(self.segment.acceptFocusRegions(self._focusRegionsFor(page)))
		self.assertIs(self.band.controller.source.obj, interceptor)


if __name__ == "__main__":
	unittest.main()
