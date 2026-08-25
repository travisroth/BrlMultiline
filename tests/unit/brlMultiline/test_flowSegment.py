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
	callLaterQueue,
	installStubs,
)

installStubs()

from brlMultiline.container import DisplayContainer  # noqa: E402
from brlMultiline.devices import DeviceInfo  # noqa: E402
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


def controllerOver(
	lines, caretIndex=0, numRows=ROWS, numCols=COLS, live=True, handler=None, interactive=False
):
	interceptor = FakeTreeInterceptor(lines, caretIndex=caretIndex)
	source = DocumentFlowSource(
		interceptor,
		regionFactoryFor(CursorManagerRegion(interceptor), live=live),
		generation=1,
		interactive=interactive,
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

	def test_anOrdinarySpecCanHoldOneToo(self):
		"""Every segment is one that can hold a flow, because a pinned object wants one and
		hosting the focus has nothing to do with whether a flow can be shown."""
		view = SegmentView(
			name="plain",
			panels=[SinglePanel("plain", SegmentRect(row=0, col=0, numRows=ROWS, numCols=COLS))],
			focusSegmentKey="plain",
		)
		container = DisplayContainer(FakeHandler(ROWS, COLS), view)
		self.assertIsInstance(container.segmentForKey("plain"), FlowBufferSegment)

	def test_andBehavesAsAnOrdinaryOneUntilSomethingIsAttached(self):
		"""Which is what makes it reasonable to build them all this way: every override falls
		back to its parent while there is nothing attached."""
		view = SegmentView(
			name="plain",
			panels=[SinglePanel("plain", SegmentRect(row=0, col=0, numRows=ROWS, numCols=COLS))],
			focusSegmentKey="plain",
		)
		container = DisplayContainer(FakeHandler(ROWS, COLS), view)
		segment = container.segmentForKey("plain")
		self.assertFalse(segment.isFlowing)
		container.update()
		self.assertEqual(len(container.windowBrailleCells), ROWS * COLS)

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


class TestChoosingTheBand(unittest.TestCase):
	"""A flow band must lie inside one physical display's live cells."""

	MONARCH = DeviceInfo(driverName="monarch", rowStart=0, numRows=8, numCols=32)
	FOCUS80 = DeviceInfo(driverName="focus", rowStart=8, numRows=1, numCols=80)

	def setUp(self):
		import braille

		from brlMultiline.flowBand import FlowBand

		from ._stubs import CONFIG, resetConfig

		resetConfig()
		self.addCleanup(resetConfig)
		self.handler = FakeHandler(9, 80)
		# The band reads which display and how many rows it was given from the settings of
		# the display it is on, so there has to be one.
		self.addCleanup(setattr, braille, "handler", braille.handler)
		braille.handler = self.handler
		self.config = CONFIG
		self.container = containerWithBand(self.handler, numRows=9, numCols=80)
		self.handler.mainBuffer = self.handler.buffer = self.container
		self.band = FlowBand(FakePlugin(self.container))

	def test_anOrdinaryDisplayGivesTheWholeThing(self):
		rect = self.band.bandRect(devices=[])
		self.assertEqual((rect.numRows, rect.numCols), (9, 80))

	def composite(self):
		""":return: a Monarch above a Focus 80, driven as one nine row display.

		Nine rows of eighty cells, of which the Monarch's eight have only thirty-two.
		Claiming the whole rectangle reaches forty-eight columns no hardware has.
		"""
		from brlMultiline.devices import DeviceInfo

		return [
			DeviceInfo(driverName="monarch", rowStart=0, numRows=8, numCols=32),
			DeviceInfo(driverName="focus", rowStart=8, numRows=1, numCols=80),
		]

	def focusOn(self, devices, driverName):
		"""Put the focus segment on one of the displays.

		The index is worked out from the same numbering the settings and the move-the-focus
		command use, rather than assumed: how many segments each display contributes depends
		on that display's own settings.
		"""
		from brlMultiline.views import deviceSegmentKeys, driverNameForSegmentKey

		keys = deviceSegmentKeys(devices, 80)
		self.config["focusSegment"] = next(
			index for index, key in enumerate(keys) if driverNameForSegmentKey(key) == driverName
		)

	def test_aCompositeGivesOneMembersBandNotTheWholeRectangle(self):
		devices = self.composite()
		self.focusOn(devices, "monarch")
		rect = self.band.bandRect(devices=devices)
		self.assertEqual((rect.row, rect.col, rect.numRows, rect.numCols), (0, 0, 8, 32))

	def test_itGoesWhereTheFocusIs(self):
		"""The reader's own finding: with the flow on one display and the focus moved to the
		other, neither display was any use. The flow's own display could hold nothing else,
		because the band owned it, and it was not showing what the reader was working in."""
		devices = self.composite()
		self.focusOn(devices, "focus")
		rect = self.band.bandRect(devices=devices)
		self.assertEqual((rect.row, rect.numRows, rect.numCols), (8, 1, 80))

	def test_movingTheFocusMovesIt(self):
		devices = self.composite()
		self.focusOn(devices, "monarch")
		before = self.band.bandRect(devices=devices)
		self.focusOn(devices, "focus")
		self.assertNotEqual(self.band.bandRect(devices=devices), before)

	def test_aNamedDisplayOutranksTheFocus(self):
		"""Which is what naming one is for: pin the flow here whatever the focus does."""
		devices = self.composite()
		self.focusOn(devices, "focus")
		self.config["flowDisplay"] = "monarch"
		rect = self.band.bandRect(devices=devices)
		self.assertEqual((rect.row, rect.numRows, rect.numCols), (0, 8, 32))

	def test_theChosenBandIsAcceptedByTheHardwareCheck(self):
		# The check that refused the whole display claim on real hardware.
		from brlMultiline.devices import DeviceInfo
		from brlMultiline.panels import FlowPanel
		from brlMultiline.views import SegmentView, validateAgainstHardware

		devices = [
			DeviceInfo(driverName="monarch", rowStart=0, numRows=8, numCols=32),
			DeviceInfo(driverName="focus", rowStart=8, numRows=1, numCols=80),
		]
		rect = self.band.bandRect(devices=devices)
		view = SegmentView(name="flow", panels=[FlowPanel("flow", rect)], focusSegmentKey="flow")
		validateAgainstHardware(view, devices, 80)

	def test_theWholeDisplayWouldNotBe(self):
		from brlMultiline.devices import DeviceInfo
		from brlMultiline.panels import FlowPanel
		from brlMultiline.views import SegmentView, validateAgainstHardware

		devices = [
			DeviceInfo(driverName="monarch", rowStart=0, numRows=8, numCols=32),
			DeviceInfo(driverName="focus", rowStart=8, numRows=1, numCols=80),
		]
		view = SegmentView(
			name="flow",
			panels=[FlowPanel("flow", SegmentRect(row=0, col=0, numRows=9, numCols=80))],
			focusSegmentKey="flow",
		)
		with self.assertRaises(ValueError):
			validateAgainstHardware(view, devices, 80)

	def test_theBandCanBeGivenSomeOfTheRows(self):
		"""The rows below it keep whatever the segment layout puts there."""
		self.focusOn([self.MONARCH, self.FOCUS80], "monarch")
		self.config["flowRows"] = 4
		rect = self.band.bandRect(devices=[self.MONARCH, self.FOCUS80])
		self.assertEqual((rect.row, rect.col, rect.numRows, rect.numCols), (0, 0, 4, 32))

	def test_askingForMoreRowsThanTheDisplayHasTakesThemAll(self):
		"""A setting outliving the hardware it was typed for is ordinary, not an error."""
		self.focusOn([self.MONARCH, self.FOCUS80], "monarch")
		self.config["flowRows"] = 20
		rect = self.band.bandRect(devices=[self.MONARCH, self.FOCUS80])
		self.assertEqual(rect.numRows, 8)

	def test_theReaderMayNameTheDisplayItAppearsOn(self):
		self.config["flowDisplay"] = "focus"
		rect = self.band.bandRect(devices=[self.MONARCH, self.FOCUS80])
		self.assertEqual((rect.row, rect.numRows, rect.numCols), (8, 1, 80))

	def test_aNamedDisplayThatIsNotThereFallsBackToTheTallest(self):
		"""A reader whose second display is switched off still wants their flow."""
		self.config["flowDisplay"] = "brailliant"
		rect = self.band.bandRect(devices=[self.MONARCH, self.FOCUS80])
		self.assertEqual((rect.row, rect.numRows, rect.numCols), (0, 8, 32))


class TestFollowingTheFocus(unittest.TestCase):
	"""A flow shows the document the focus is in, and follows it to the next one."""

	def setUp(self):
		import api
		import braille
		from brlMultiline.flowBand import FlowBand

		from ._stubs import CONFIG, resetConfig

		resetConfig()
		self.addCleanup(resetConfig)
		# Whether browse mode is read as a flow is a setting, and its default is off, so a
		# reader turns it on. These tests are about what happens once they have.
		CONFIG["flowEnabled"] = True
		self.handler = FakeHandler(ROWS, COLS)
		# The band reads its settings against the display it is on, so there has to be one.
		self.addCleanup(setattr, braille, "handler", braille.handler)
		braille.handler = self.handler
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

	def test_focusOnSomethingOutsideADocumentIsGivenBackToNVDA(self):
		# Notepad's edit control has no tree interceptor. A flow over it followed the caret
		# and grew a row at a time as the reader typed, which is not a presentation anyone
		# asked for; NVDA already presents it well.
		page, _ = self._document(documentLines())
		self._start(page)
		notepad = self._focusOn(FakeNavigatorObject("a document", lines=documentLines()))
		self.assertFalse(self.segment.acceptFocusRegions(self._focusRegionsFor(notepad)))
		self.assertFalse(self.segment.isFlowing)

	def test_aDirectRefreshOutsideADocumentPreservesNVDAsFocusContent(self):
		"""Startup and rebuild have already drawn the fallback before the owner refreshes."""
		button = self._focusOn(FakeNavigatorObject("a dialog button"))
		fallback = self._focusRegionsFor(button)
		self.segment.regions = fallback
		self.segment.brailleCells = [1, 2, 3]
		self.band._follow()
		self.assertFalse(self.band.refresh(force=True))
		self.assertEqual(self.segment.regions, fallback)
		self.assertEqual(self.segment.brailleCells, [1, 2, 3])

	def test_aFieldInsideAPageStillFlows(self):
		# The reader entering a form field has not left the document; it is the same page,
		# differently attended to.
		page, interceptor = self._document(documentLines())
		self._start(page)
		interceptor.passThrough = True
		field = FakeNavigatorObject("a search field", treeInterceptor=interceptor, lines=["typed"])
		self._focusOn(field)
		self.assertTrue(self.segment.acceptFocusRegions(self._focusRegionsFor(field)))
		self.assertTrue(self.segment.isFlowing)

	def test_aFieldInFocusModeUsesTheFieldsLiveRegionInsideThePage(self):
		# Placement and neighbouring blocks still belong to browse mode, but NVDA reports
		# caret and value events against the real edit. The active block has to use that same
		# object or those events never queue it for refresh.
		page, interceptor = self._document(["Name", "f: stale", "Town"])
		self._start(page)
		interceptor.passThrough = True
		field = FakeNavigatorObject(
			"a search field",
			role="EDITABLETEXT",
			treeInterceptor=interceptor,
			documentIndex=1,
			lines=["Ada"],
		)
		before = self.band.controller
		self._focusOn(field)
		self.segment.acceptFocusRegions(self._focusRegionsFor(field))
		flow = self.band.controller
		self.assertIs(flow.source.obj, interceptor)
		self.assertIs(flow.activeRegion().obj, field)
		self.assertEqual(flow.activeRegion().rawText, "Ada")
		# The same reading, kept: entering a field is a move within the page, so the blocks
		# already read and the positions they were read from are still good. Resolving to the
		# field instead makes every tab look like arriving in a new document, which throws the
		# cache away and takes a fresh generation each time.
		self.assertIs(flow, before)

	def test_aFocusModeCaretEventRefreshesTheLiveField(self):
		page, interceptor = self._document(["Name", "f: stale", "Town"])
		self._start(page)
		interceptor.passThrough = True
		field = FakeNavigatorObject(
			"a search field",
			role="EDITABLETEXT",
			treeInterceptor=interceptor,
			documentIndex=1,
			lines=["abcdef"],
		)
		self._focusOn(field)
		self.segment.acceptFocusRegions(self._focusRegionsFor(field))
		field.caretOffset = 4
		self.handler.handleCaretMove(field)
		self.assertEqual(self.handler._regionsPendingUpdate, {self.band.controller.activeRegion()})
		self.handler._handlePendingUpdate()
		self.assertEqual(self.band.controller.activeRegion().brailleCursorPos, 4)

	def test_aMultilineFieldTracksItsOwnLineRatherThanTheBrowseCursor(self):
		page, interceptor = self._document(["Notes", "f: stale", "After"])
		self._start(page)
		interceptor.passThrough = True
		field = FakeNavigatorObject(
			"notes",
			role="EDITABLETEXT",
			treeInterceptor=interceptor,
			documentIndex=1,
			lines=["first", "second line"],
		)
		self._focusOn(field)
		self.segment.acceptFocusRegions(self._focusRegionsFor(field))
		field.caretIndex = 1
		field.caretOffset = 3
		self.handler.handleCaretMove(field)
		self.handler._handlePendingUpdate()
		self.assertEqual(self.band.controller.activeRegion().rawText, "second line")
		self.assertEqual(self.band.controller.activeRegion().brailleCursorPos, 3)

	def test_leavingAFieldRestoresTheDocumentRegionAtThatPlace(self):
		page, interceptor = self._document(["Name", "field in page", "After"])
		self._start(page)
		interceptor.passThrough = True
		field = FakeNavigatorObject(
			"field",
			role="EDITABLETEXT",
			treeInterceptor=interceptor,
			documentIndex=1,
			lines=["live value"],
		)
		self._focusOn(field)
		self.segment.acceptFocusRegions(self._focusRegionsFor(field))
		oldRegion = self.band.controller.activeRegion()
		link = FakeNavigatorObject(
			"link",
			role="LINK",
			treeInterceptor=interceptor,
			documentIndex=1,
		)
		self._focusOn(link)
		self.segment.acceptFocusRegions(self._focusRegionsFor(link))
		self.assertIs(self.band.controller.activeRegion().obj, interceptor)
		self.assertEqual(self.band.controller.activeRegion().rawText, "field in page")
		self.assertIsNone(oldRegion.onMoved)

	def test_escapingBackToBrowseModeGivesTheDocumentItsBlockAgain(self):
		# Browse mode takes itself back without a focus change: Escape leaves the field with
		# the focus still on it. From that moment the reader's place belongs to the page
		# again, and a flow still answering every caret move with the field would sit on it
		# while they arrowed away down the document.
		page, interceptor = self._document(["Name", "field in page", "After"])
		self._start(page)
		interceptor.passThrough = True
		field = FakeNavigatorObject(
			"field",
			role="EDITABLETEXT",
			treeInterceptor=interceptor,
			documentIndex=1,
			lines=["live value"],
		)
		self._focusOn(field)
		self.segment.acceptFocusRegions(self._focusRegionsFor(field))
		self.assertIs(self.band.controller.activeRegion().obj, field)
		interceptor.passThrough = False
		interceptor.caretIndex = 2
		self.band.controller.followCursor()
		self.assertIs(self.band.controller.activeRegion().obj, interceptor)
		self.assertEqual(self.band.controller.activeRegion().rawText, "After")

	def test_andEnteringItAgainGivesItBackToTheField(self):
		"""The same toggle in reverse, which need not be a focus change either."""
		page, interceptor = self._document(["Name", "field in page", "After"])
		self._start(page)
		interceptor.passThrough = True
		field = FakeNavigatorObject(
			"field",
			role="EDITABLETEXT",
			treeInterceptor=interceptor,
			documentIndex=1,
			lines=["live value"],
		)
		self._focusOn(field)
		self.segment.acceptFocusRegions(self._focusRegionsFor(field))
		interceptor.passThrough = False
		self.band.controller.followCursor()
		interceptor.passThrough = True
		interceptor.caretIndex = 1
		self.band.controller.followCursor()
		self.assertIs(self.band.controller.activeRegion().obj, field)

	def _multiline(self, interceptor, lines):
		""":return: a multi line edit inside a page, as a textarea is."""
		field = FakeNavigatorObject(
			"notes",
			role="EDITABLETEXT",
			treeInterceptor=interceptor,
			documentIndex=1,
			lines=lines,
		)
		field.states = {"MULTILINE"}
		return field

	def test_aMultilineEditBeingWrittenInIsTheDocument(self):
		# A virtual buffer holds a textarea's text as its own lines *and* answers where the
		# field is with one of them, so placing the field among those lines showed the
		# reader's line twice — once as the field and once as the page.
		page, interceptor = self._document(["Notes", "a field", "After"])
		self._start(page)
		interceptor.passThrough = True
		field = self._multiline(interceptor, ["first", "second", "third"])
		self._focusOn(field)
		self.segment.acceptFocusRegions(self._focusRegionsFor(field))
		flow = self.band.controller
		self.assertIs(flow.source.obj, field)
		self.assertEqual(flow.regionFor(flow.window.topBlockId()).rawText, "first")

	def test_soItsOwnLinesAreTheBlocks(self):
		page, interceptor = self._document(["Notes", "a field", "After"])
		self._start(page)
		interceptor.passThrough = True
		field = self._multiline(interceptor, ["first", "second", "third"])
		self._focusOn(field)
		self.segment.acceptFocusRegions(self._focusRegionsFor(field))
		flow = self.band.controller
		self.assertEqual(
			[flow.regionFor(row.blockId).rawText for row in flow.window.visibleRows() if row.blockId],
			["first", "second", "third"],
		)

	def test_aMultilineEditIsReadByParagraph(self):
		# A paragraph is what the writer typed; a line is what the control's wrapping made of
		# it. And this control answers the first dependably and the second only sometimes.
		page, interceptor = self._document(["Notes", "a field", "After"])
		self._start(page)
		interceptor.passThrough = True
		field = self._multiline(interceptor, ["first", "second"])
		self._focusOn(field)
		self.segment.acceptFocusRegions(self._focusRegionsFor(field))
		self.assertEqual(self.band.controller.source.unit, "paragraph")

	def test_aPageIsStillReadTheReadersOwnWay(self):
		page, _interceptor = self._document(["Name", "a field", "After"])
		self._start(page)
		self.assertEqual(self.band.controller.source.unit, "line")

	def test_aSingleLineFieldStaysPartOfThePage(self):
		"""It is one line of the page, and the page has its label and what follows it."""
		page, interceptor = self._document(["Name", "a field", "After"])
		self._start(page)
		interceptor.passThrough = True
		field = FakeNavigatorObject(
			"name",
			role="EDITABLETEXT",
			treeInterceptor=interceptor,
			documentIndex=1,
			lines=["Ada"],
		)
		self._focusOn(field)
		self.segment.acceptFocusRegions(self._focusRegionsFor(field))
		self.assertIs(self.band.controller.source.obj, interceptor)

	def test_leavingAMultilineEditGivesThePageBackWithoutAFocusChange(self):
		# Escape leaves the field with the focus still on it, so nothing tells the band by
		# itself. It asked only on focus changes, and went on showing the field.
		page, interceptor = self._document(["Notes", "a field", "After"])
		self._start(page)
		interceptor.passThrough = True
		field = self._multiline(interceptor, ["first", "second", "third"])
		self._focusOn(field)
		self.segment.acceptFocusRegions(self._focusRegionsFor(field))
		self.assertIs(self.band.controller.source.obj, field)
		interceptor.passThrough = False
		self.segment.update()
		self.assertIs(self.band.controller.source.obj, interceptor)

	def test_everyCaretMoveReachesTheDisplay(self):
		"""Not every other one, which is what a stale last region gave.

		NVDA queues the region it finds in the buffer, so the buffer has to be holding the
		block the band is showing *now*. Left holding the one the reader had just left, the
		next caret move queued a region the band no longer showed and the update after it
		found nothing dirty. Under the fingers: a cursor that moves a keypress late.
		"""
		page, interceptor = self._document(["Notes", "a field", "After"])
		self._start(page)
		interceptor.passThrough = True
		field = self._multiline(interceptor, ["first", "second", "third"])
		self._focusOn(field)
		self.segment.acceptFocusRegions(self._focusRegionsFor(field))
		seen = []
		for index in (1, 2, 0):
			field.caretIndex = index
			self.handler.handleCaretMove(field)
			self.handler._handlePendingUpdate()
			seen.append(self.band.controller.activeRegion().rawText)
		self.assertEqual(seen, ["second", "third", "first"])

	def test_aStandaloneEditorFlowsWhenTheReaderEnablesEditableText(self):
		from ._stubs import CONFIG

		CONFIG["flowEditableText"] = True
		editor = self._focusOn(
			FakeNavigatorObject(
				"Notepad",
				role="EDITABLETEXT",
				lines=["first line", "second line", "third line"],
			)
		)
		self.assertTrue(self._start(editor))
		self.assertIs(self.band.controller.source.obj, editor)
		self.assertIs(self.band.controller.activeRegion().obj, editor)
		self.assertEqual(self.band.controller.activeRegion().rawText, "first line")

	def test_browseModeDoesNotFlowWhenTheReaderHasTurnedItOff(self):
		# The setting is asked before anything else: a reader who wants NVDA's own reading
		# of a web page gets it, and the band presents the focus as any segment would.
		from ._stubs import CONFIG

		CONFIG["flowBrowseMode"] = False
		page, _ = self._document(documentLines())
		self.assertFalse(self._start(page))
		self.assertFalse(self.segment.isFlowing)

	def test_arrivingAtAControlReadsItAtItsOwnPlace(self):
		# Tabbing into an edit field drops browse mode into focus mode, so the cursor is
		# inside the control and the line it is on is the value being edited — empty, for an
		# empty field, which is the blank row the hardware run felt. The control's own line
		# is what browse mode shows, and it is the one carrying the name and the role.
		page, interceptor = self._document(["Name", "f: Ada", "Town"])
		self._start(page)
		interceptor.caretIndex = 2
		field = FakeNavigatorObject(
			"a field",
			role="EDITABLETEXT",
			treeInterceptor=interceptor,
			documentIndex=1,
		)
		self._focusOn(field)
		self.assertTrue(self.segment.acceptFocusRegions(self._focusRegionsFor(field)))
		flow = self.band.controller
		self.assertEqual(flow.regionFor(flow.activeBlockId).rawText, "f: Ada")

	def test_soThePromptSitsAboveIt(self):
		page, interceptor = self._document(["Name", "f: Ada", "Town"])
		self._start(page)
		field = FakeNavigatorObject(
			"a field",
			role="EDITABLETEXT",
			treeInterceptor=interceptor,
			documentIndex=1,
		)
		self._focusOn(field)
		self.segment.acceptFocusRegions(self._focusRegionsFor(field))
		flow = self.band.controller
		self.assertEqual(flow.window.topBlockId(), flow.window.blocks[0].blockId)
		self.assertEqual(flow.regionFor(flow.window.topBlockId()).rawText, "Name")

	def test_arrivingAtALinkReadsAtItsOwnPlace(self):
		"""Tabbing lands on a thing, and the thing is the better account of where they are.

		A focus event can arrive before the browse mode cursor has caught up. Reading at the
		cursor then shows the line the reader was on before they pressed Tab, which is what
		"tabbing shows a blank space" turned out to be on the hardware.
		"""
		page, interceptor = self._document(["Name", "f: Ada", "Town"])
		self._start(page)
		interceptor.caretIndex = 2
		link = FakeNavigatorObject(
			"a link",
			role="LINK",
			treeInterceptor=interceptor,
			documentIndex=0,
		)
		self._focusOn(link)
		self.segment.acceptFocusRegions(self._focusRegionsFor(link))
		flow = self.band.controller
		self.assertEqual(flow.regionFor(flow.activeBlockId).rawText, "Name")

	def test_aLinkIsStillNotAFormControl(self):
		"""Where they arrived and what kind of thing it is are two questions.

		A link is read at its own place for the reason above, and none of the prompt and
		spacing rules a form control brings apply to it.
		"""
		page, interceptor = self._document(["Name", "f: Ada", "Town"])
		self._start(page)
		link = FakeNavigatorObject(
			"a link",
			role="LINK",
			treeInterceptor=interceptor,
			documentIndex=2,
		)
		self._focusOn(link)
		self.segment.acceptFocusRegions(self._focusRegionsFor(link))
		flow = self.band.controller
		self.assertFalse(flow.blocks.get(flow.activeBlockId).isControl)

	def test_aPendingJumpIsAnsweredWithoutTheRegionBeingReread(self):
		# It used to be answered only when NVDA had queued our region for re-reading, which
		# is a condition outside our control: a quick navigation key whose note was never
		# taken left the display where it was while speech announced the new heading.
		from brlMultiline import flowQuickNav

		page, interceptor = self._document(documentLines())
		self._start(page)
		flow = self.band.controller
		region = flow.activeRegion()
		if region is not None:
			region.dirty = False
		# Six lines down, which on a four row band is well past the bottom of the window.
		interceptor.caretIndex = 6
		flowQuickNav.note("heading")
		self.segment.update()
		self.assertEqual(flow.regionFor(flow.window.topBlockId()).rawText, "line 6")

	def test_aJumpThatDoesNotGroundStillBringsItsTargetOn(self):
		# `e` for the next edit field, `b` for the next button. They do not ground — the
		# reader is moving within what they are reading — but braille still has to go where
		# speech went. It did not: grounding was the one path that did not need our region to
		# have been re-read, so `h` appeared to work and `e` and `b` did nothing at all.
		from brlMultiline import flowQuickNav

		page, interceptor = self._document(documentLines())
		self._start(page)
		flow = self.band.controller
		region = flow.activeRegion()
		if region is not None:
			region.dirty = False
		interceptor.caretIndex = 6
		flowQuickNav.note("editText")
		self.segment.update()
		self.assertEqual(flow.regionFor(flow.activeBlockId).rawText, "line 6")

	def test_andDoesNotPutItOnTheTopRow(self):
		"""Which is the difference from a heading: the reader's window is nudged, not replaced."""
		from brlMultiline import flowQuickNav

		page, interceptor = self._document(documentLines())
		self._start(page)
		flow = self.band.controller
		region = flow.activeRegion()
		if region is not None:
			region.dirty = False
		interceptor.caretIndex = 6
		flowQuickNav.note("editText")
		self.segment.update()
		self.assertNotEqual(flow.regionFor(flow.window.topBlockId()).rawText, "line 6")

	def test_anArrivalSpendsAPendingJump(self):
		# An arrival places the window afresh at what the reader came to, which is what
		# grounding would have done. A note left behind would ground their next arrow key.
		from brlMultiline import flowQuickNav

		page, interceptor = self._document(documentLines())
		self._start(page)
		flowQuickNav.note("heading")
		self._focusOn(page)
		self.segment.acceptFocusRegions(self._focusRegionsFor(page))
		self.assertIsNone(flowQuickNav.take())

	def test_aListOutsideADocumentFlowsAsObjects(self):
		from ._stubs import CONFIG, fakeRun

		CONFIG["flowObjects"] = True
		self.band._follow()
		items = fakeRun(["Apple", "Banana", "Cherry"])
		self._focusOn(items[1])
		self.assertTrue(self.segment.acceptFocusRegions(self._focusRegionsFor(items[1])))
		flow = self.band.controller
		self.assertEqual(flow.regionFor(flow.activeBlockId).rawText, "Banana LISTITEM")

	def test_arrowingThroughItKeepsTheSameReading(self):
		# In a list the focus changes on every arrow key. Rebuilding each time would walk
		# the run again, which is a call into the application per item passed.
		from ._stubs import CONFIG, FakeNavigatorObject as Obj, fakeRun

		CONFIG["flowObjects"] = True
		self.band._follow()
		box = Obj("Fruit", role="LISTBOX")
		items = fakeRun(["Apple", "Banana", "Cherry"], parent=box)
		self._focusOn(items[0])
		self.segment.acceptFocusRegions(self._focusRegionsFor(items[0]))
		before = self.band.controller
		self._focusOn(items[1])
		self.segment.acceptFocusRegions(self._focusRegionsFor(items[1]))
		self.assertIs(self.band.controller, before)
		flow = self.band.controller
		self.assertEqual(flow.regionFor(flow.activeBlockId).rawText, "Banana LISTITEM")

	def test_aListIsLeftToNVDAUntilTheReaderAsksForIt(self):
		"""Objects are off until turned on: a new reading must not change how dialogs behave."""
		from ._stubs import fakeRun

		self.band._follow()
		items = fakeRun(["Apple", "Banana"])
		self._focusOn(items[0])
		self.assertFalse(self.segment.acceptFocusRegions(self._focusRegionsFor(items[0])))
		self.assertFalse(self.segment.isFlowing)

	def test_aListInsideAPageIsReadAsThePage(self):
		from ._stubs import CONFIG, FakeNavigatorObject as Obj

		CONFIG["flowObjects"] = True
		page, interceptor = self._document(["a heading", "Apple", "Banana"])
		self._start(page)
		item = Obj("Apple", role="LISTITEM", treeInterceptor=interceptor, documentIndex=1)
		self._focusOn(item)
		self.segment.acceptFocusRegions(self._focusRegionsFor(item))
		self.assertIs(self.band.controller.source.obj, interceptor)

	def test_focusOnSomethingWithNoTextIsGivenBackToNVDA(self):
		# A button in a dialog has no lines to flow. The band becomes an ordinary segment
		# again rather than leaving the reader with a blank display.
		page, _ = self._document(documentLines())
		self._start(page)
		button = self._focusOn(FakeNavigatorObject("a button"))
		self.assertFalse(self.segment.acceptFocusRegions(self._focusRegionsFor(button)))
		self.assertFalse(self.segment.isFlowing)
		self.assertIsNone(self.band.controller)

	def test_panningARunDoesNotBringTheFocusBack(self):
		"""Three displays' worth of items, and the reader's place stays on the first.

		The hardware report: the first pan moved, the second bounced back to the top. The
		second is the one that has to fetch, and the blocks it fetched came back marked as
		freshly read — which is how a flow hears that the reader moved, so it followed the
		focus back to where they had started.
		"""
		from ._stubs import CONFIG, fakeRun

		CONFIG["flowObjects"] = True
		self.band._follow()
		items = fakeRun([str(number) for number in range(12)], role="TAB")
		self._focusOn(items[0])
		self.segment.acceptFocusRegions(self._focusRegionsFor(items[0]))
		self.segment.scrollForward()
		self.segment.scrollForward()
		flow = self.band.controller
		self.assertEqual(flow.regionFor(flow.window.topBlockId()).obj.name, "8")
		self.assertEqual(flow.regionFor(flow.activeBlockId).obj.name, "0")

	def test_arrowingIntoAnOpenComboBoxKeepsItsChoices(self):
		# The focus moves to the choice itself the moment the reader arrows within an open
		# box. Letting that replace what the run was found from left the run looking for the
		# children of one choice, which is nothing, and the display lost the list.
		from ._stubs import CONFIG, FakeNavigatorObject as Obj, fakeRun

		CONFIG["flowObjects"] = True
		self.band._follow()
		box = Obj("Country", role="COMBOBOX")
		items = fakeRun(["a", "b", "c"], parent=box, selected=0)
		self._focusOn(box)
		self.segment.acceptFocusRegions(self._focusRegionsFor(box))
		before = self.band.controller
		self._focusOn(items[2])
		self.assertTrue(self.segment.acceptFocusRegions(self._focusRegionsFor(items[2])))
		self.assertIs(self.band.controller, before)
		flow = self.band.controller
		self.assertEqual(flow.regionFor(flow.activeBlockId).obj.name, "c")

	def test_tabbingToSomethingAlreadyOnTheDisplayLeavesItWhereItIs(self):
		# A window the reader has their hands on must not be thrown away for a target they
		# can already feel. Only a jump by structure rehomes the display.
		page, interceptor = self._document(["Name", "f: Ada", "Town", "more"])
		self._start(page)
		top = self.band.controller.window.topBlockId()
		link = FakeNavigatorObject(
			"a link",
			role="LINK",
			treeInterceptor=interceptor,
			documentIndex=2,
		)
		self._focusOn(link)
		self.segment.acceptFocusRegions(self._focusRegionsFor(link))
		self.assertEqual(self.band.controller.window.topBlockId(), top)

	def test_comingBackToADocumentReadsItAgain(self):
		page, interceptor = self._document(documentLines())
		self._start(page)
		button = self._focusOn(FakeNavigatorObject("a button"))
		self.segment.acceptFocusRegions(self._focusRegionsFor(button))
		self._focusOn(page)
		self.assertTrue(self.segment.acceptFocusRegions(self._focusRegionsFor(page)))
		self.assertIs(self.band.controller.source.obj, interceptor)


class TestTheSettlePass(unittest.TestCase):
	"""A keystroke into an edit earns a second look a moment later.

	A rich editor's answers at the instant of a keystroke can be transiently wrong, and
	every wrong state heals on the next re-read — which used to arrive only with the next
	keystroke. A reader who pauses right after pressing return is reading the display, and
	that is exactly the moment the garbage sat under their fingers.
	"""

	def writingBand(self):
		handler = FakeHandler(ROWS, COLS)
		container = containerWithBand(handler)
		handler.mainBuffer = handler.buffer = container
		segment = container.segmentForKey("flow")
		control = controllerOver(["one", "two", "three"], handler=handler, interactive=True)
		segment.attach(control)
		return segment, control

	def settleRequests(self, segment):
		requests = []
		segment.onSettle = lambda: requests.append(True)
		return requests

	def caretMoved(self, control, index):
		"""A caret move as NVDA delivers one: the queued region re-read, then the update."""
		control.source.obj.caretIndex = index
		region = control.activeRegion()
		if region is not None:
			region.dirty = True

	def test_aWritingReReadAsksForASettle(self):
		segment, control = self.writingBand()
		requests = self.settleRequests(segment)
		self.caretMoved(control, 1)
		segment.update()
		self.assertEqual(len(requests), 1)

	def test_theRequestIsConsumed(self):
		segment, control = self.writingBand()
		requests = self.settleRequests(segment)
		self.caretMoved(control, 1)
		segment.update()
		segment.update()
		self.assertEqual(len(requests), 1)

	def test_readingRatherThanWritingAsksForNone(self):
		segment, control = self.writingBand()
		control.source.writing = False
		requests = self.settleRequests(segment)
		self.caretMoved(control, 1)
		segment.update()
		self.assertEqual(requests, [])


class TestTheBandSettleTimer(unittest.TestCase):
	"""The band's half of the settle pass: one timer, restarted per keystroke."""

	def band(self, lines=None):
		from brlMultiline.flowBand import FlowBand

		control = controllerOver(lines or ["one", "two", "three"], interactive=True)
		band = object.__new__(FlowBand)
		band._settleTimer = None
		band.controller = control
		self.refreshes = []
		segment = type("FakeSegment", (), {"refresh": lambda inner: self.refreshes.append(True)})()
		band.segment = lambda: segment
		callLaterQueue.pending.clear()
		return band, control

	def test_aFreshKeystrokeRestartsTheTimer(self):
		band, _control = self.band()
		band._scheduleSettle()
		first = band._settleTimer
		band._scheduleSettle()
		self.assertTrue(first.stopped)
		self.assertEqual(len(callLaterQueue.pending), 1)

	def test_aSettleThatChangesNothingRedrawsNothing(self):
		band, _control = self.band()
		band._scheduleSettle()
		callLaterQueue.fire()
		self.assertEqual(self.refreshes, [])

	def test_aSettleThatHealsTheBandRedrawsIt(self):
		band, control = self.band()
		band._scheduleSettle()
		# The editor finishes answering between the keystroke and the settle.
		control.source.obj.lines[0] = "mended"
		callLaterQueue.fire()
		self.assertEqual(self.refreshes, [True])

	def test_stoppingTheBandCancelsTheTimer(self):
		band, _control = self.band()
		band._scheduleSettle()
		timer = band._settleTimer
		band._cancelSettle()
		self.assertTrue(timer.stopped)
		self.assertIsNone(band._settleTimer)


class TestATreeOpeningUnderTheReader(unittest.TestCase):
	"""Expanding a node is a change NVDA reports through no event the band can see.

	The focus does not move, so no fresh focus regions are built, and the object the band is
	reading is the one it was already reading. Every signal the band usually notices a change
	by says nothing happened, and the reader is left feeling the folder they just opened
	still shut.
	"""

	def setUp(self):
		import api
		import braille
		from brlMultiline.flowBand import FlowBand

		from ._stubs import CONFIG, resetConfig

		resetConfig()
		self.addCleanup(resetConfig)
		CONFIG["flowEnabled"] = True
		# Runs of objects are a setting of their own, and its default is off.
		CONFIG["flowObjects"] = True
		self.handler = FakeHandler(ROWS, COLS)
		self.addCleanup(setattr, braille, "handler", braille.handler)
		braille.handler = self.handler
		self.container = containerWithBand(self.handler)
		self.handler.mainBuffer = self.handler.buffer = self.container
		self.plugin = FakePlugin(self.container)
		self.band = FlowBand(self.plugin)
		self.previousFocus = api.getFocusObject
		self.addCleanup(setattr, api, "getFocusObject", self.previousFocus)
		self.api = api

	def _tree(self, at="Personal"):
		from ._stubs import fakeTree

		_control, index = fakeTree(
			[
				(
					"Inbox",
					True,
					[
						("Work", True, [("Urgent", False, [])]),
						("Personal", False, [("Hidden", False, [])]),
					],
				),
				("Archive", False, []),
			],
		)
		self.api.getFocusObject = lambda: index[at]
		self.band._follow()
		self.band.refresh(force=True)
		return index

	def _texts(self):
		return [block.rawText for block in self.band.controller.window.blocks]

	def test_theBandReadsATreeAsATree(self):
		self._tree()
		from brlMultiline import flowObjects

		self.assertIs(self.band.controller.source.adapter, flowObjects.VISIBLE_TREE)

	def test_aClosedFoldersContentsAreNotShown(self):
		self._tree()
		self.assertNotIn("Hidden", " ".join(self._texts()))

	def test_openingAFolderPutsItsContentsOnTheBand(self):
		"""The change no event announces."""
		index = self._tree()
		index["Personal"].states = {"EXPANDED"}
		self.band.recheck()
		self.assertIn("Hidden", " ".join(self._texts()))

	def test_closingAFolderTakesThemOffAgain(self):
		index = self._tree(at="Work")
		self.assertIn("Urgent", " ".join(self._texts()))
		index["Work"].states = {"COLLAPSED"}
		self.band.recheck()
		self.assertNotIn("Urgent", " ".join(self._texts()))

	def test_aRedrawWithNothingOpenedKeepsTheReading(self):
		"""`recheck` runs before every redraw, so rebuilding when nothing changed would
		throw away the blocks already read on each one."""
		self._tree()
		control = self.band.controller
		self.band.recheck()
		self.assertIs(self.band.controller, control)

	def test_oneOpeningRebuildsOnceRatherThanForever(self):
		index = self._tree()
		index["Personal"].states = {"EXPANDED"}
		self.band.recheck()
		control = self.band.controller
		self.band.recheck()
		self.assertIs(self.band.controller, control)


if __name__ == "__main__":
	unittest.main()
