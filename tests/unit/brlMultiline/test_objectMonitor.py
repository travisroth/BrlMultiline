# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for pinning an object to a segment.

Three claims are made here, and each of them failed on hardware before this was written:

1. A browse mode document is pinned through its tree interceptor, so that the pin covers
   the document rather than the one element the browse mode cursor was in.
2. A pinned segment can be panned from line to line, and doing so moves nothing outside
   the segment — not the caret, not the browse mode cursor, not the focus.
3. A pin re-reads its object, so it shows what the object says now rather than what it
   said when it was pinned, and re-reading does not undo the user's panning.

The stub regions move their object's cursor when panned, exactly as NVDA's do, so that
claim 2 is tested against something that would fail if the pinned regions were not doing
their own bookkeeping.
"""

import unittest
from types import SimpleNamespace

from ._stubs import (
	CONFIG,
	FakeHandler,
	FakeNavigatorObject,
	FakeTreeInterceptor,
	Region,
	installStubs,
	loadPlugin,
	resetPluginState,
	setSpeechOutputMode,
)

installStubs()
plugin = loadPlugin()
GlobalPlugin = plugin.GlobalPlugin

import braille  # noqa: E402

from brlMultiline import patches  # noqa: E402
from brlMultiline.objectMonitor import ObjectMonitor, resolveTarget  # noqa: E402
from brlMultiline.pinnedRegions import (  # noqa: E402
	PinnedCursorManagerRegion,
	PinnedTextInfoRegion,
	pinnedCounterpart,
)

TABLE = [
	["Symbol", "Last", "Change"],
	["AAPL", "182.50", "+1.25"],
	["F", "9.10", "-0.05"],
	["BRK.B", "402.15", "+3.40"],
]

MONARCH_ROWS = 8
MONARCH_COLS = 32
PINNED_SEGMENT = 1
PINNED_KEY = "display.1"


def documentLines():
	""":return: a fresh list of short lines, so that a test mutating one does not leak."""
	return [f"line {index}" for index in range(6)]


class TestResolveTarget(unittest.TestCase):
	"""The substitution NVDA's own `handleGainFocus` makes, and this has to make too."""

	def test_anObjectWithoutAnInterceptorIsUnchanged(self):
		obj = FakeNavigatorObject("a button")
		self.assertIs(resolveTarget(obj), obj)

	def test_aReadyInterceptorIsSubstituted(self):
		interceptor = FakeTreeInterceptor(documentLines())
		obj = FakeNavigatorObject("a page", treeInterceptor=interceptor)
		self.assertIs(resolveTarget(obj), interceptor)

	def test_anInterceptorInPassThroughIsNotSubstituted(self):
		"""Pass through means the user is in a form field, where the object itself reads."""
		interceptor = FakeTreeInterceptor(documentLines(), passThrough=True)
		obj = FakeNavigatorObject("a page", treeInterceptor=interceptor)
		self.assertIs(resolveTarget(obj), obj)

	def test_anInterceptorThatIsNotReadyIsNotSubstituted(self):
		interceptor = FakeTreeInterceptor(documentLines(), isReady=False)
		obj = FakeNavigatorObject("a page", treeInterceptor=interceptor)
		self.assertIs(resolveTarget(obj), obj)


class TestPinnedCounterpart(unittest.TestCase):
	def test_aBrowseModeRegionBecomesACursorManagerPin(self):
		interceptor = FakeTreeInterceptor(documentLines())
		monitor = ObjectMonitor(FakeNavigatorObject("a page", treeInterceptor=interceptor), PINNED_KEY)
		regions = monitor.buildRegions()
		self.assertIsInstance(regions[-1], PinnedCursorManagerRegion)

	def test_anEditableRegionBecomesATextInfoPin(self):
		monitor = ObjectMonitor(FakeNavigatorObject("a document", lines=documentLines()), PINNED_KEY)
		regions = monitor.buildRegions()
		self.assertIsInstance(regions[-1], PinnedTextInfoRegion)

	def test_aRegionWithNoCursorIsLeftAlone(self):
		self.assertIsNone(pinnedCounterpart(Region("a button")))

	def test_everyRegionIsMarkedForItsSegment(self):
		monitor = ObjectMonitor(FakeNavigatorObject("a document", lines=documentLines()), PINNED_KEY)
		for region in monitor.buildRegions():
			self.assertEqual(region.targetSegment, PINNED_KEY)

	def test_thePinAdoptsThePositionItWasMadeAt(self):
		"""Pinning captures where the user was standing, not the top of the document."""
		interceptor = FakeTreeInterceptor(documentLines(), caretIndex=3)
		monitor = ObjectMonitor(FakeNavigatorObject("a page", treeInterceptor=interceptor), PINNED_KEY)
		self.assertEqual(monitor.buildRegions()[-1].rawText, "line 3")

	def test_theNameComesFromTheObjectNotTheInterceptor(self):
		interceptor = FakeTreeInterceptor(documentLines())
		monitor = ObjectMonitor(FakeNavigatorObject("A page title", treeInterceptor=interceptor), PINNED_KEY)
		self.assertEqual(monitor.name, "A page title")


class MonitorTestCase(unittest.TestCase):
	"""A Monarch divided into eight single row segments, with a document pinned to one."""

	segmentCount = 8

	def setUp(self):
		resetPluginState()
		CONFIG["segmentCount"] = self.segmentCount
		self.handler = FakeHandler(MONARCH_ROWS, MONARCH_COLS)
		self.originalBuffer = object()
		self.handler.mainBuffer = self.handler.buffer = self.originalBuffer
		braille.handler = self.handler
		patches._lastMonitorRefresh = 0.0
		self.plugin = GlobalPlugin()
		self.lines = documentLines()
		self.addCleanup(self.cleanUp)

	def cleanUp(self):
		try:
			if not self.plugin._terminated:
				self.plugin.terminate()
		finally:
			patches.remove()
			patches._lastMonitorRefresh = 0.0
			resetPluginState()

	@property
	def container(self):
		return self.plugin.container

	@property
	def segment(self):
		return self.container.segmentForKey(PINNED_KEY)

	def pinDocument(self, caretIndex=2, **kwargs):
		"""Pin a browse mode document, as the per segment command does.

		:return: the tree interceptor behind it, so a test can check its cursor did not move.
		"""
		import api

		interceptor = FakeTreeInterceptor(self.lines, caretIndex=caretIndex, **kwargs)
		api.getNavigatorObject = lambda: FakeNavigatorObject("a page", treeInterceptor=interceptor)
		self.plugin.startMonitoring(PINNED_SEGMENT)
		return interceptor

	@property
	def pinnedRegion(self):
		return self.segment.regions[-1]

	def pan(self, forward=True):
		self.container.scrollForward(PINNED_SEGMENT) if forward else self.container.scrollBack(PINNED_SEGMENT)


class TestPanningAPin(MonitorTestCase):
	"""The reported defect: a pinned page showed one line and would not pan."""

	def test_theContentIsShownWherePinned(self):
		self.pinDocument(caretIndex=2)
		self.assertEqual(self.pinnedRegion.rawText, "line 2")

	def test_panningForwardMovesToTheNextLine(self):
		self.pinDocument(caretIndex=2)
		self.pan(forward=True)
		self.assertEqual(self.pinnedRegion.rawText, "line 3")

	def test_panningBackMovesToThePreviousLine(self):
		self.pinDocument(caretIndex=2)
		self.pan(forward=False)
		self.assertEqual(self.pinnedRegion.rawText, "line 1")

	def test_panningDoesNotMoveTheBrowseModeCursor(self):
		"""The reason a segment out of focus was forbidden to change lines at all."""
		interceptor = self.pinDocument(caretIndex=2)
		self.pan(forward=True)
		self.pan(forward=True)
		self.assertEqual(interceptor.caretIndex, 2)

	def test_panningWalksTheWholeDocument(self):
		self.pinDocument(caretIndex=0)
		for _ in range(5):
			self.pan(forward=True)
		self.assertEqual(self.pinnedRegion.rawText, "line 5")

	def test_panningStopsAtTheEndOfTheDocument(self):
		self.pinDocument(caretIndex=5)
		self.pan(forward=True)
		self.assertEqual(self.pinnedRegion.rawText, "line 5")

	def test_panningStopsAtTheStartOfTheDocument(self):
		self.pinDocument(caretIndex=0)
		self.pan(forward=False)
		self.assertEqual(self.pinnedRegion.rawText, "line 0")

	def test_standingStillDoesNotWriteToTheDisplay(self):
		self.pinDocument(caretIndex=5)
		before = self.handler.updateCount
		self.pan(forward=True)
		self.assertEqual(self.handler.updateCount, before)

	def test_aLongLineIsPannedWithinBeforeChangingLine(self):
		"""Line changes are the last resort, after the window has run out of its own line."""
		self.lines[2] = "x" * 100
		self.pinDocument(caretIndex=2)
		self.pan(forward=True)
		self.assertEqual(self.pinnedRegion.rawText, "x" * 100)
		self.assertGreater(self.segment.windowStartPos, 0)

	def test_anOrdinaryRegionStillMayNotChangeLine(self):
		"""Unpinned content in a segment out of focus keeps NVDA's caret out of it."""
		from ._stubs import TextInfoRegion

		document = FakeNavigatorObject("a document", lines=self.lines)
		document.caretIndex = 2
		region = TextInfoRegion(document)
		region.update()
		segment = self.container.segmentForKey("display.3")
		segment.append(region)
		self.container.update()
		self.container.scrollForward(3)
		self.assertFalse(hasattr(region, "panned"))
		self.assertEqual(document.caretIndex, 2)


class TestRefreshingAPin(MonitorTestCase):
	"""A pin is re-read on a timer, because nothing else will tell it that it changed."""

	def test_aChangeInTheObjectReachesTheDisplay(self):
		self.pinDocument(caretIndex=2)
		self.lines[2] = "changed"
		self.plugin.refreshMonitors()
		self.assertEqual(self.pinnedRegion.rawText, "changed")

	def test_anUnchangedRefreshWritesNothing(self):
		self.pinDocument(caretIndex=2)
		before = self.handler.updateCount
		self.plugin.refreshMonitors()
		self.plugin.refreshMonitors()
		self.assertEqual(self.handler.updateCount, before)

	def test_aRefreshKeepsThePannedLine(self):
		"""The regression rebuilding the regions on every refresh would cause."""
		self.pinDocument(caretIndex=2)
		self.pan(forward=True)
		self.lines[0] = "something else entirely"
		self.plugin.refreshMonitors()
		self.assertEqual(self.pinnedRegion.rawText, "line 3")

	def test_aRefreshKeepsTheWindowPosition(self):
		self.lines[2] = "x" * 100
		self.pinDocument(caretIndex=2)
		self.pan(forward=True)
		panned = self.segment.windowStartPos
		self.assertGreater(panned, 0)
		self.lines[2] = "y" * 100
		self.plugin.refreshMonitors()
		self.assertEqual(self.segment.windowStartPos, panned)

	def test_aFreshPinIsShownFromItsStart(self):
		self.lines[2] = "x" * 100
		self.pinDocument(caretIndex=2)
		self.assertEqual(self.segment.windowStartPos, 0)

	def test_aRefreshDrawsNothingWhileTheDisplayShowsSpeech(self):
		self.pinDocument(caretIndex=2)
		setSpeechOutputMode(True)
		self.container.regions = [Region("spoken text")]
		self.lines[2] = "changed"
		self.plugin.refreshMonitors()
		self.assertEqual(self.segment.regions, [])


class TestRefreshTimer(MonitorTestCase):
	"""`patches` drives the refresh from the core cycle, which is far too fast to obey."""

	def setUp(self):
		super().setUp()
		self.refreshes = 0
		self.plugin.refreshMonitors = self.countRefresh

	def countRefresh(self, reveal=None):
		self.refreshes += 1

	def pinAndCount(self):
		"""Pin, then start counting: pinning draws the pin, which is not a tick."""
		self.pinDocument()
		self.refreshes = 0

	def test_theFirstTickRefreshes(self):
		self.pinAndCount()
		patches._refreshPinnedObjects()
		self.assertEqual(self.refreshes, 1)

	def test_aSecondTickStraightAfterIsSkipped(self):
		self.pinAndCount()
		patches._refreshPinnedObjects()
		patches._refreshPinnedObjects()
		self.assertEqual(self.refreshes, 1)

	def test_nothingIsReadWhenNothingIsPinned(self):
		patches._refreshPinnedObjects()
		self.assertEqual(self.refreshes, 0)


class TestAPinTallEnoughToFlow(MonitorTestCase):
	"""A pin is a document, a run of objects or a table just as much as the focus is, and the
	display the focus is not on is now where pins live — so there is room to mean it.

	Two segments of four rows, rather than the eight of one row the tests above use, because
	one row is the case that cannot show a flow at all."""

	segmentCount = 2

	def setUp(self):
		# Two segments, so the last of them is the one the focus follows by default and a pin
		# there would be refused. The focus goes to the first and the pin to the second.
		super().setUp()
		CONFIG["focusSegment"] = 0
		self.plugin.rebuildBuffer()

	def test_itIsReadAsAFlow(self):
		self.pinDocument()
		self.assertIsNotNone(self.plugin._monitors[PINNED_KEY].controller)

	def test_theSegmentDrawsFromIt(self):
		self.pinDocument()
		self.assertIs(self.segment.controller, self.plugin._monitors[PINNED_KEY].controller)

	def held(self, monitor):
		""":return: what the pin's flow is holding, line by line.

		Read off the blocks rather than off `describeRows`, because a document flow lays out
		with `fillRows` off and the stub buffer cannot wrap at word boundaries: it returns no
		row offsets, so every drawn row comes back empty. That is a gap in the fixture and not
		in the renderer — hardware wraps these rows correctly — but it means the *drawn* text
		of a document flow cannot be asserted here.
		"""
		return [
			monitor.controller.blocks.get(block.blockId).region.rawText
			for block in monitor.controller.window.blocks
		]

	def test_itShowsMoreThanOneLineOfTheDocument(self):
		"""Which is the whole of what a flow buys here: the region path shows the line the pin
		was made on and nothing else."""
		self.pinDocument(caretIndex=0)
		held = self.held(self.plugin._monitors[PINNED_KEY])
		self.assertIn("line 0", held)
		self.assertIn("line 1", held)

	def test_itKeepsUpWithTheDocument(self):
		self.pinDocument(caretIndex=0)
		monitor = self.plugin._monitors[PINNED_KEY]
		self.lines[1] = "changed"
		monitor.refresh()
		self.assertIn("changed", self.held(monitor))

	def test_anUnchangedPinIsNotWritten(self):
		"""The same bargain the region path makes: a pin that is not changing costs a read
		and no display traffic."""
		self.pinDocument()
		monitor = self.plugin._monitors[PINNED_KEY]
		written = []
		original = self.segment.refresh
		self.segment.refresh = lambda *a, **k: (written.append(1), original(*a, **k))[1]
		self.addCleanup(setattr, self.segment, "refresh", original)
		monitor.refresh()
		self.assertEqual(written, [])

	def test_theReadingPositionSurvivesARead(self):
		"""Panning is the reader's, and a re-read must not undo it."""
		self.pinDocument(caretIndex=0)
		monitor = self.plugin._monitors[PINNED_KEY]
		monitor.controller.panForward()
		anchor = monitor.controller.window.anchor
		self.lines[1] = "changed"
		monitor.refresh()
		self.assertEqual(monitor.controller.window.anchor.blockId, anchor.blockId)

	def test_itIsCountedLikeAnyOtherPin(self):
		self.pinDocument()
		monitor = self.plugin._monitors[PINNED_KEY]
		monitor.refresh()
		self.assertGreater(monitor.counts[0], 0)

	def test_unpinningLetsTheSegmentGo(self):
		"""A segment left drawing from a flow nothing owns any more would keep showing it."""
		self.pinDocument()
		self.plugin.stopMonitoring(PINNED_SEGMENT)
		self.assertIsNone(self.segment.controller)

	def test_theReadersCursorIsNotMoved(self):
		"""A pin shows something the reader is not working in, so nothing it does may move
		them: the flow is built without permission to write a position back."""
		interceptor = self.pinDocument(caretIndex=2)
		before = interceptor.caretIndex
		monitor = self.plugin._monitors[PINNED_KEY]
		monitor.controller.panForward()
		monitor.refresh()
		self.assertEqual(interceptor.caretIndex, before)


class TestAPinWithNoRoomToFlow(MonitorTestCase):
	"""One row is a row. Everything a flow is for needs a second one to exist at all."""

	segmentCount = 8

	def test_itReadsThroughRegionsAsItAlwaysHas(self):
		self.pinDocument()
		self.assertIsNone(self.plugin._monitors[PINNED_KEY].controller)

	def test_andTheSegmentHoldsRegions(self):
		self.pinDocument()
		self.assertTrue(self.segment.regions)
		self.assertIsNone(self.segment.controller)


class TestAPinnedTableInColumns(MonitorTestCase):
	"""A pinned table read in reading order is a pinned table read the way NVDA already reads
	it. What the reader pinned it for is the shape, and the shape is the columns."""

	segmentCount = 2

	def setUp(self):
		super().setUp()
		CONFIG["focusSegment"] = 0
		self.plugin.rebuildBuffer()

	def pinTable(self, wantsColumns=True):
		""":return: the monitor on a table, laid out in columns or not."""
		import api

		from ._stubs import FakeTableDocument

		document = FakeTableDocument(
			[list(line) for line in TABLE],
			row=2,
			col=1,
			lines=[" ".join(line) for line in TABLE],
		)
		api.getNavigatorObject = lambda: FakeNavigatorObject("a page", treeInterceptor=document)
		self.plugin.startMonitoring(PINNED_SEGMENT)
		monitor = self.plugin._monitors[PINNED_KEY]
		monitor.wantsColumns = wantsColumns
		monitor.refresh()
		return monitor, document

	def test_aPinnedTableCanBeReadInColumns(self):
		monitor, _document = self.pinTable()
		plan = getattr(monitor.controller.renderer, "columnPlan", None)
		self.assertIsNotNone(plan)
		self.assertFalse(plan.isEmpty)

	def test_withoutAskingItReadsInOrder(self):
		"""Which is what the reader met: the table flowed down the row rather than across."""
		monitor, _document = self.pinTable(wantsColumns=False)
		plan = getattr(monitor.controller.renderer, "columnPlan", None)
		self.assertTrue(plan is None or plan.isEmpty)

	def test_changingYourMindRebuildsIt(self):
		monitor, _document = self.pinTable(wantsColumns=False)
		monitor.wantsColumns = True
		monitor.refresh()
		self.assertFalse(monitor.controller.renderer.columnPlan.isEmpty)

	def test_aRowIsOneBlock(self):
		"""The difference the reader can feel: a row of the table is a row of the band, not
		one cell per line down the display."""
		monitor, _document = self.pinTable()
		block = monitor.controller.window.blocks[0]
		held = monitor.controller.blocks.get(block.blockId)
		self.assertGreater(len(held.region.cells), 1)

	def test_somethingThatIsNotATableFallsBackToReadingOrder(self):
		"""What the reader asked for was this table in columns, and a table that will not lay
		out is still worth reading in order."""
		self.pinDocument()
		monitor = self.plugin._monitors[PINNED_KEY]
		monitor.wantsColumns = True
		monitor.refresh()
		self.assertIsNotNone(monitor.controller)

	def test_pinningATableAlreadyInColumnsPinsItInColumns(self):
		"""The reader turned it on and then pinned, and the layout was lost."""
		import api

		from ._stubs import FakeTableDocument

		document = FakeTableDocument([list(line) for line in TABLE], row=2, col=1)
		obj = FakeNavigatorObject("a page", treeInterceptor=document)
		api.getNavigatorObject = lambda: obj
		self.plugin.flowBand = SimpleNamespace(wantsColumnsFor=lambda pinned: pinned is obj)
		self.plugin.startMonitoring(PINNED_SEGMENT)
		self.assertTrue(self.plugin._monitors[PINNED_KEY].wantsColumns)

	def test_pinningOneThatIsNotPinsItInOrder(self):
		import api

		from ._stubs import FakeTableDocument

		document = FakeTableDocument([list(line) for line in TABLE], row=2, col=1)
		api.getNavigatorObject = lambda: FakeNavigatorObject("a page", treeInterceptor=document)
		self.plugin.flowBand = SimpleNamespace(wantsColumnsFor=lambda pinned: False)
		self.plugin.startMonitoring(PINNED_SEGMENT)
		self.assertFalse(self.plugin._monitors[PINNED_KEY].wantsColumns)
