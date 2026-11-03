# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the viewer feature: what it gathers, and what pointing at a cell does.

See docs/design/braille-viewer-plan.md, The viewer.
"""

import sys
import types
import unittest

from ._stubs import (
	FakeBrailleHandler,
	callAfterQueue,
	callLaterQueue,
	clipboard,
	fakeVirtualDisplay,
	installStubs,
	pre_writeCells,
	resetPluginState,
	spokenMessages,
)

installStubs()

import braille  # noqa: E402
import inputCore  # noqa: E402

from brlMultiline import graphics  # noqa: E402
from brlMultiline.graphics import GraphicsSurface, PinRect  # noqa: E402
from brlMultiline.layout import SegmentRect  # noqa: E402
from brlMultiline import viewer  # noqa: E402
from brlMultiline.viewer import BrailleViewerTool  # noqa: E402
from brlMultiline.viewerModel import CellPosition  # noqa: E402

SIMULATED = "brlMultilineSimulated1"
FOCUS = "freedomScientific"


class FakeHost:
	"""The plugin, as far as `ViewerHost` goes."""

	def __init__(self, container=None):
		self.container = container


class FakeSimulated:
	"""A simulated member, as far as the viewer presses its keys and touches it."""

	name = SIMULATED

	def __init__(self):
		self.calls = []

	def touch(self, x, y):
		self.calls.append(("touch", x, y))

	def pressKey(self, keyId, cellIndex=None):
		self.calls.append(("press", keyId, cellIndex))


def segment(text):
	return types.SimpleNamespace(windowRawText=text)


class ViewerTestCase(unittest.TestCase):
	def setUp(self):
		resetPluginState()
		self.handler = FakeBrailleHandler()
		braille.handler = self.handler
		self.simulated = FakeSimulated()
		display = fakeVirtualDisplay((SIMULATED, 0, 8, 32), (FOCUS, 8, 1, 80), drivers={SIMULATED: self.simulated})
		display.numRows, display.numCols = 9, 80
		display.simulatedMode = False
		display.slotForDriverName = lambda name: next(
			(slot for slot in display.slots if slot.driverName == name),
			None,
		)
		self.display = display
		self.handler.display = display
		container = types.SimpleNamespace(
			rects=[SegmentRect(0, 0, 8, 32), SegmentRect(8, 0, 1, 80)],
			segments=[segment("on the simulated display"), segment("on the Focus")],
		)
		self.tool = BrailleViewerTool(FakeHost(container))
		self.tool.install()

	def tearDown(self):
		self.tool.terminate()
		braille.handler = None


class TestPicture(ViewerTestCase):
	def test_theCellsWrittenAreShown(self):
		pre_writeCells.notify(cells=list(range(9 * 80)), rawText="", currentCellCount=9 * 80)
		picture = self.tool.picture()
		self.assertEqual((picture.numRows, picture.numCols), (9, 80))
		self.assertEqual(picture.cell(8, 1), 8 * 80 + 1)

	def test_eachMemberIsShownWithItsOwnWidth(self):
		members = self.tool.picture().members
		self.assertEqual([(member.name, member.rowStart, member.numCols) for member in members], [(SIMULATED, 0, 32), (FOCUS, 8, 80)])
		self.assertEqual(members[0].label, "Simulated display 1")

	def test_eachSegmentHasItsOwnText(self):
		rows = self.tool.picture().rawTextRows()
		self.assertTrue(rows[0].startswith("on the simulated display"))
		self.assertTrue(rows[8].startswith("on the Focus"))

	def test_aSegmentsOwnRowTextsAreUsed(self):
		rowed = types.SimpleNamespace(windowRawText="one two", windowRowTexts=lambda: ["one", "two"])
		self.tool.plugin.container.segments[1] = rowed
		self.assertEqual(self.tool.picture().segments[1].rowTexts, ("one", "two"))

	def test_anOrdinaryDisplayIsOneMember(self):
		self.handler.display = types.SimpleNamespace(name=FOCUS, numRows=1, numCols=80, numCells=80)
		members = self.tool.picture().members
		self.assertEqual([(member.name, member.numRows, member.numCols) for member in members], [(FOCUS, 1, 80)])

	def test_nothingIsShownWhileLocked(self):
		self.tool.onSessionLock(True)
		self.assertEqual(self.tool.picture().numRows, 0)
		self.tool.onSessionLock(False)
		self.assertEqual(self.tool.picture().numRows, 9)

	def test_aDrawingPutUpIsShownAndOneTakenOffIsNot(self):
		surface = GraphicsSurface(self.simulated, 96, 40, 8, 32, 0)
		self.simulated.setGraphicsOverlay = lambda *args: None
		self.simulated.clearGraphicsOverlay = lambda key=None: None
		surface.show("chart", PinRect(3, 4, 2, 2), "buffer")
		self.assertEqual(len(self.tool.picture().overlays[SIMULATED]), 1)
		surface.hide()
		self.assertEqual(self.tool.picture().overlays, {})


class TestCopying(ViewerTestCase):
	def setUp(self):
		super().setUp()
		clipboard.clear()

	def test_theBrailleAndTextAreCopiedWithTheWindowClosed(self):
		pre_writeCells.notify(cells=[0b1] + [0] * (9 * 80 - 1), rawText="", currentCellCount=9 * 80)
		self.assertFalse(self.tool.isOpen)
		self.assertTrue(self.tool.copyToClipboard())
		lines = clipboard[-1].split("\r\n")
		self.assertEqual(lines[:3], ["Simulated display 1", "⠁", "on the simulated display"])

	def test_nothingIsCopiedWithNoDisplay(self):
		self.handler.display = None
		self.assertFalse(self.tool.copyToClipboard())
		self.assertEqual(clipboard, [])

	def test_nothingIsCopiedWithNoBraille(self):
		# NVDA's "No braille" is a driver with no cells, not the absence of a display.
		self.handler.display = types.SimpleNamespace(name="noBraille", numRows=1, numCols=0, numCells=0)
		self.assertFalse(self.tool.copyToClipboard())
		self.assertEqual(clipboard, [])

	def test_aBlankDisplayIsStillCopied(self):
		pre_writeCells.notify(cells=[0] * (9 * 80))
		self.assertTrue(self.tool.copyToClipboard())
		self.assertIn("Simulated display 1", clipboard[-1])

	def test_nothingIsCopiedWhileLocked(self):
		self.tool.onSessionLock(True)
		self.assertFalse(self.tool.copyToClipboard())
		self.assertEqual(clipboard, [])


class TestRouting(ViewerTestCase):
	def test_aSimulatedDisplayPressesItsOwnRoutingKey(self):
		picture = self.tool.picture()
		self.tool.route(picture, CellPosition(2, 5))
		self.assertEqual(self.simulated.calls, [("press", "routing", 2 * 32 + 5)])

	def test_aPinIsTouchedBeforeTheKeyIsPressed(self):
		self.tool.route(self.tool.picture(), CellPosition(2, 5, (16, 11)))
		self.assertEqual(self.simulated.calls, [("touch", 16, 11), ("press", "routing", 2 * 32 + 5)])

	def test_aDeadCellRoutesNowhere(self):
		self.tool.route(self.tool.picture(), CellPosition(2, 40))
		self.assertEqual(self.simulated.calls, [])

	def test_realHardwareIsRoutedAsNVDAsViewerRoutes(self):
		executed = []

		class RouteTo:
			def __init__(self, index):
				self.index = index

		module = types.ModuleType("brailleViewer.brailleViewerInputGesture")
		module.BrailleViewerGesture_RouteTo = RouteTo
		self.addCleanup(sys.modules.pop, "brailleViewer.brailleViewerInputGesture", None)
		sys.modules["brailleViewer.brailleViewerInputGesture"] = module
		manager = inputCore.manager
		self.addCleanup(setattr, inputCore, "manager", manager)
		inputCore.manager = types.SimpleNamespace(executeGesture=lambda gesture: executed.append(gesture.index))
		self.tool.route(self.tool.picture(), CellPosition(8, 5))
		self.assertEqual(executed, [8 * 80 + 5])


class TestTelling(ViewerTestCase):
	def test_simulatedModeIsSaidOncePerDisplay(self):
		self.display.simulatedMode = True
		self.tool.afterRebuild()
		self.tool.afterRebuild()
		callLaterQueue.fire()
		self.assertEqual(len([message for message in spokenMessages if "simulating" in message]), 1)

	def test_realDisplaysAreNotSaidToBeSimulated(self):
		self.tool.afterRebuild()
		callLaterQueue.fire()
		self.assertEqual(spokenMessages, [])

	def test_nvdasViewerOnSeveralRowsIsExplained(self):
		self.tool._onNvdaViewer(created=True)
		self.assertEqual(len(spokenMessages), 1)
		self.assertIn("one row", spokenMessages[0])

	def test_nvdasViewerOnOneRowSaysNothing(self):
		self.handler.display = types.SimpleNamespace(name=FOCUS, numRows=1, numCols=80, numCells=80)
		self.tool._onNvdaViewer(created=True)
		self.tool._onNvdaViewer(created=False)
		self.assertEqual(spokenMessages, [])


class TestLifetime(ViewerTestCase):
	def test_terminateStopsListening(self):
		self.tool.terminate()
		self.assertEqual(pre_writeCells.handlers, [])
		self.assertNotIn(self.tool._onOverlay, graphics.overlayChanged.handlers)


class TestAfterTerminate(ViewerTestCase):
	"""Nothing queued before the plugin let go acts after it."""

	def test_theStartupOpeningIsDropped(self):
		self.tool.terminate()
		tool = BrailleViewerTool(self.tool.plugin)
		shouldOpen = viewer.bmConfig.shouldOpenViewerAtStartup
		self.addCleanup(setattr, viewer.bmConfig, "shouldOpenViewerAtStartup", shouldOpen)
		viewer.bmConfig.shouldOpenViewerAtStartup = lambda: True
		tool.install()
		tool.terminate()
		callAfterQueue.flush()
		self.assertFalse(tool.isOpen)

	def test_theSimulatedModeMessageIsDropped(self):
		self.display.simulatedMode = True
		self.tool.afterRebuild()
		self.tool.terminate()
		callLaterQueue.fire()
		self.assertEqual(spokenMessages, [])

	def test_theMessageIsDroppedForADisplayNoLongerThere(self):
		self.display.simulatedMode = True
		self.tool.afterRebuild()
		self.handler.display = types.SimpleNamespace(name=FOCUS, numRows=1, numCols=80, numCells=80)
		callLaterQueue.fire()
		self.assertEqual(spokenMessages, [])


class TestGuards(ViewerTestCase):
	def test_nothingRoutesWhileLocked(self):
		picture = self.tool.picture()
		self.tool.onSessionLock(True)
		self.tool.route(picture, CellPosition(2, 5))
		self.assertEqual(self.simulated.calls, [])

	def test_everyWriteRepaintsEvenWithTheSameCells(self):
		refreshes = []
		self.tool._window = types.SimpleNamespace(refresh=lambda: refreshes.append(True), closeQuietly=lambda: None)
		pre_writeCells.notify(cells=[1], rawText="a")
		pre_writeCells.notify(cells=[1], rawText="A")
		self.assertEqual(len(refreshes), 2)

	def test_displayNamesAreReadOncePerDisplayChange(self):
		reads = []
		original = viewer.displayDescriptions
		self.addCleanup(setattr, viewer, "displayDescriptions", original)
		viewer.displayDescriptions = lambda: reads.append(True) or original()
		self.tool.picture()
		self.tool.picture()
		self.assertEqual(len(reads), 1)
		self.tool.afterRebuild()
		self.tool.picture()
		self.assertEqual(len(reads), 2)


class TestSegmentRowTexts(unittest.TestCase):
	def test_eachRowsTextIsWhereItsCellsAre(self):
		from brlMultiline.panels import SegmentSpec
		from brlMultiline.segments import BrailleBufferSegment

		row = types.SimpleNamespace
		spec = SegmentSpec(rect=SegmentRect(row=0, col=0, numRows=2, numCols=8), key="text")
		segment = BrailleBufferSegment(FakeBrailleHandler(), None, spec)
		segment.rawText = "BrlMultiline Viewer"
		segment._windowRowBufferOffsets = [row(start=0, end=13), row(start=13, end=19)]
		self.assertEqual(segment.windowRowTexts(), ["BrlMultiline ", "Viewer"])


if __name__ == "__main__":
	unittest.main()
