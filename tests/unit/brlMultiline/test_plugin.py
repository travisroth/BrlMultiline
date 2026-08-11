# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the global plugin: the lifetime of the container, and the claims on it.

This is the layer that was left uncovered when panels were built, and the layer where the
interesting failures live. Nothing here happens inside a single container: every case is
about what survives one being replaced, or about work queued against a container that no
longer exists.

`wx.CallAfter` is a queue the tests flush by hand, so a deferred rebuild can be inspected
before, after, or instead of running.
"""

import unittest

from ._stubs import (
	CONFIG,
	FakeHandler,
	FakeNavigatorObject,
	Region,
	callAfterQueue,
	displayChanged,
	displaySizeChanged,
	installStubs,
	loadPlugin,
	post_configProfileSwitch,
	resetPluginState,
	spokenMessages,
)

installStubs()
plugin = loadPlugin()
GlobalPlugin = plugin.GlobalPlugin

import braille  # noqa: E402
import config  # noqa: E402

from brlMultiline import patches  # noqa: E402
from brlMultiline.container import DisplayContainer  # noqa: E402
from brlMultiline.layout import SegmentRect  # noqa: E402
from brlMultiline.panels import GridPanel, SinglePanel  # noqa: E402

MONARCH_ROWS = 8
MONARCH_COLS = 32


class PluginTestCase(unittest.TestCase):
	segmentCount = 4

	def setUp(self):
		resetPluginState()
		CONFIG["segmentCount"] = self.segmentCount
		self.handler = FakeHandler(MONARCH_ROWS, MONARCH_COLS)
		self.originalBuffer = object()
		self.handler.mainBuffer = self.handler.buffer = self.originalBuffer
		braille.handler = self.handler
		self.plugin = GlobalPlugin()
		self.addCleanup(self.cleanUp)

	def cleanUp(self):
		try:
			if not self.plugin._terminated:
				self.plugin.terminate()
		finally:
			patches.remove()
			resetPluginState()

	@property
	def container(self):
		return self.plugin.container

	def pin(self, segmentNumber, name="pinned thing"):
		"""Pin an object to a segment, as the per segment command does."""
		import api

		api.getNavigatorObject = lambda: FakeNavigatorObject(name)
		self.plugin.startMonitoring(segmentNumber)


class TestInstallation(PluginTestCase):
	def test_aContainerIsInstalled(self):
		self.assertIsInstance(self.handler.mainBuffer, DisplayContainer)
		self.assertIs(self.handler.buffer, self.handler.mainBuffer)

	def test_theConfiguredLayoutIsUsed(self):
		self.assertEqual(self.container.numSegments, 4)

	def test_terminateHandsTheBufferBack(self):
		self.plugin.terminate()
		self.assertIs(self.handler.mainBuffer, self.originalBuffer)
		self.assertIs(self.handler.buffer, self.originalBuffer)

	def test_terminateIsRecorded(self):
		self.plugin.terminate()
		self.assertTrue(self.plugin._terminated)


class TestMonitorCarryOver(PluginTestCase):
	segmentCount = 8

	def test_aPinSurvivesAnUnrelatedRebuild(self):
		self.pin(2)
		self.assertEqual(self.plugin.monitoredKeys, {"display.2"})
		self.plugin.rebuildBuffer()
		self.assertEqual(self.plugin.monitoredKeys, {"display.2"})

	def test_aPinSurvivesAClaimElsewhere(self):
		"""The whole point of composition: the claim did not touch the pinned segment."""
		self.pin(0)
		self.plugin.activatePanel(
			GridPanel(
				"table", SegmentRect(2, 0, 6, MONARCH_COLS), rowBands=[2, 2, 2], colWidths=[10, 10, 10]
			),
		)
		self.assertEqual(self.plugin.monitoredKeys, {"display.0"})
		self.assertTrue(self.container.hasKey("display.0"))

	def test_aPinIsReleasedWhenItsSegmentGoes(self):
		self.pin(5)
		self.plugin.activatePanel(
			SinglePanel("strip", SegmentRect(0, 0, 8, MONARCH_COLS), focusSegmentKey="strip"),
		)
		self.assertEqual(self.plugin.monitoredKeys, set())

	def test_aPinIsReleasedWhenAClaimTakesItsKey(self):
		"""A claim reusing the key would redraw over the pin, and the two would fight."""
		self.pin(1)
		self.plugin.activatePanel(SinglePanel("display.1", SegmentRect(1, 0, 1, MONARCH_COLS)))
		self.assertEqual(self.plugin.monitoredKeys, set())

	def test_aPinIsReleasedWhenItsSegmentBecomesTheFocus(self):
		"""The regression: settings moved the focus onto a segment that was already pinned.

		Keeping the pin would let `refreshMonitors` clear the freshly drawn focus content
		and write the pinned object over it.
		"""
		self.pin(2)
		self.assertEqual(self.plugin.monitoredKeys, {"display.2"})
		CONFIG["focusSegment"] = 2
		self.plugin.rebuildBuffer()
		self.assertEqual(self.container.focusSegmentKey, "display.2")
		self.assertEqual(self.plugin.monitoredKeys, set())

	def test_pinningTheFocusSegmentIsRefused(self):
		self.pin(self.container.focusSegmentNumber)
		self.assertEqual(self.plugin.monitoredKeys, set())
		self.assertTrue(any("follows the focus" in message for message in spokenMessages))

	def test_pinningAClaimedSegmentIsRefused(self):
		self.plugin.activatePanel(SinglePanel("table", SegmentRect(0, 0, 1, MONARCH_COLS)))
		self.pin(self.container.numberForKey("table"))
		self.assertEqual(self.plugin.monitoredKeys, set())
		self.assertTrue(any("in use by" in message for message in spokenMessages))

	def test_monitoredSegmentsResolvesToCurrentIndices(self):
		self.pin(6)
		self.assertEqual(self.plugin.monitoredSegments, {6})
		# A claim above it renumbers the display without disturbing the pin.
		self.plugin.activatePanel(
			GridPanel("t", SegmentRect(0, 0, 4, MONARCH_COLS), rowBands=[2, 2], colWidths=[10, 10, 10]),
		)
		self.assertEqual(self.plugin.monitoredKeys, {"display.6"})
		self.assertEqual(self.plugin.monitoredSegments, {self.container.numberForKey("display.6")})

	def test_stopMonitoringClearsTheSegment(self):
		self.pin(3)
		self.container.segmentForKey("display.3").append(Region("stuff"))
		self.plugin.stopMonitoring(3)
		self.assertEqual(self.plugin.monitoredKeys, set())
		self.assertEqual(self.container.segmentForKey("display.3").regions, [])


class TestDeferredRebuild(PluginTestCase):
	def test_aDisplayEventQueuesOneRebuild(self):
		displayChanged.notify()
		displaySizeChanged.notify()
		self.assertEqual(len(callAfterQueue.pending), 1)

	def test_theQueuedRebuildRuns(self):
		CONFIG["segmentCount"] = 6
		displayChanged.notify()
		callAfterQueue.flush()
		self.assertEqual(self.container.numSegments, 6)

	def test_aRebuildQueuedBeforeTerminationDoesNotRun(self):
		"""The race: the callback would install a container over the restored buffer."""
		displayChanged.notify()
		self.assertEqual(len(callAfterQueue.pending), 1)
		self.plugin.terminate()
		self.assertIs(self.handler.mainBuffer, self.originalBuffer)
		callAfterQueue.flush()
		self.assertIs(self.handler.mainBuffer, self.originalBuffer)

	def test_nothingIsQueuedAfterTermination(self):
		self.plugin.terminate()
		displayChanged.notify()
		self.assertEqual(callAfterQueue.pending, [])

	def test_terminationUnregistersTheHandlers(self):
		self.plugin.terminate()
		self.assertEqual(displayChanged.handlers, [])
		self.assertEqual(displaySizeChanged.handlers, [])
		self.assertEqual(config.post_configProfileSwitch.handlers, [])


class TestProfileSwitch(PluginTestCase):
	def test_aProfileSwitchQueuesARebuild(self):
		"""Settings live in config.conf, which is profile aware, so nothing else announces it."""
		self.assertEqual(len(post_configProfileSwitch.handlers), 1)
		post_configProfileSwitch.notify()
		self.assertEqual(len(callAfterQueue.pending), 1)

	def test_theNewProfilesLayoutIsApplied(self):
		self.assertEqual(self.container.numSegments, 4)
		CONFIG["segmentCount"] = 2
		post_configProfileSwitch.notify()
		callAfterQueue.flush()
		self.assertEqual(self.container.numSegments, 2)

	def test_aProfileSwitchAfterTerminationIsIgnored(self):
		self.plugin.terminate()
		post_configProfileSwitch.notify()
		self.assertEqual(callAfterQueue.pending, [])


class TestPanelClaims(PluginTestCase):
	segmentCount = 8

	def claim(self, name="table"):
		return GridPanel(
			name,
			SegmentRect(2, 0, 6, MONARCH_COLS),
			rowBands=[2, 2, 2],
			colWidths=[10, 10, 10],
		)

	def test_activatingAPanelClaimsItsCells(self):
		self.plugin.activatePanel(self.claim())
		self.assertEqual(len(self.container.reservedKeys), 9)
		self.assertEqual(self.container.focusSegmentKey, "table.r0c0")

	def test_untouchedSegmentsSurvive(self):
		self.plugin.activatePanel(self.claim())
		self.assertTrue(self.container.hasKey("display.0"))
		self.assertTrue(self.container.hasKey("display.1"))

	def test_aClaimSurvivesASettingsRebuild(self):
		"""Claims are re-composed each time rather than baked into a stored view."""
		self.plugin.activatePanel(self.claim())
		self.plugin.rebuildBuffer()
		self.assertTrue(self.container.hasKey("table.r0c0"))

	def test_deactivatingGivesTheCellsBack(self):
		self.plugin.activatePanel(self.claim())
		self.plugin.deactivatePanel("table")
		self.assertFalse(any(key.startswith("table.") for key in self.container._byKey))
		self.assertEqual(self.container.numSegments, 8)

	def test_aClaimOfTheSameNameReplacesIt(self):
		self.plugin.activatePanel(self.claim())
		self.plugin.activatePanel(self.claim())
		self.assertEqual(sum(1 for panel in self.container.panels if panel.name == "table"), 1)

	def test_aClaimThatCannotBeHonouredLeavesTheDisplayAlone(self):
		before = self.container
		with self.assertRaises(ValueError):
			self.plugin.activatePanel(SinglePanel("oops", SegmentRect(6, 0, 4, MONARCH_COLS)))
		self.assertIs(self.container, before)

	def test_aClaimTakingTheFocusWithoutAReplacementIsRefused(self):
		before = self.container
		with self.assertRaises(LookupError):
			self.plugin.activatePanel(SinglePanel("status", SegmentRect(7, 0, 1, MONARCH_COLS)))
		self.assertIs(self.container, before)

	def test_aClaimThatNoLongerFitsIsDropped(self):
		"""A display swap can leave a claim that cannot be placed on the new geometry."""
		self.plugin.activatePanel(self.claim())
		self.handler.displayDimensions.numRows = 4
		self.plugin.rebuildBuffer()
		self.assertFalse(any(key.startswith("table.") for key in self.container._byKey))
		self.assertEqual(self.plugin._activePanels, [])

	def test_restoreConfiguredViewDropsEveryClaim(self):
		self.plugin.activatePanel(self.claim())
		self.plugin.restoreConfiguredView()
		self.assertEqual(self.plugin._activePanels, [])
		self.assertEqual(self.container.numSegments, 8)


class TestSpeechOutputMode(PluginTestCase):
	"""What happens to claimed content when NVDA shows speech in braille.

	These record present behaviour rather than desired behaviour. Assigning to
	`container.regions` empties every segment, deliberately: in speech output mode NVDA
	stops handling focus, caret and review moves, so anything left elsewhere would sit
	under the reader's fingers with nothing to refresh or remove it.

	The gap is what happens on the way back. A pin's registration survives, but its content
	does not return until something redraws it, and nothing does so automatically. A panel
	owner has no equivalent recovery at all, which is the strongest argument for giving
	panels a render callback.
	"""

	segmentCount = 8

	def enterSpeechMode(self):
		self.container.regions = [Region("spoken text")]

	def test_speechOutputEmptiesEverySegmentButTheFocus(self):
		self.pin(1)
		self.assertNotEqual(self.container.segmentForKey("display.1").regions, [])
		self.enterSpeechMode()
		self.assertEqual(self.container.segmentForKey("display.1").regions, [])
		self.assertEqual(len(self.container.focusSegment.regions), 1)

	def test_thePinRegistrationSurvivesSpeechOutput(self):
		self.pin(1)
		self.enterSpeechMode()
		self.assertEqual(self.plugin.monitoredKeys, {"display.1"})

	def test_pinnedContentDoesNotComeBackOnItsOwn(self):
		"""Documents the gap: nothing redraws a pin when speech output ends."""
		self.pin(1, name="watch me")
		self.enterSpeechMode()
		self.assertEqual(self.container.segmentForKey("display.1").regions, [])

	def test_refreshMonitorsIsTheRecoveryPath(self):
		"""It exists and works; what is missing is anything that calls it at the right time."""
		self.pin(1, name="watch me")
		self.enterSpeechMode()
		self.plugin.refreshMonitors()
		self.assertNotEqual(self.container.segmentForKey("display.1").regions, [])

	def test_aClaimedPanelHasNoRecoveryPathAtAll(self):
		"""The panel keeps its cells, but nothing can put its content back."""
		self.plugin.activatePanel(SinglePanel("table", SegmentRect(0, 0, 1, MONARCH_COLS)))
		self.container.segmentForKey("table").append(Region("owned content"))
		self.enterSpeechMode()
		self.assertTrue(self.container.hasKey("table"))
		self.assertEqual(self.container.segmentForKey("table").regions, [])


class TestNoDisplay(PluginTestCase):
	def test_aRebuildWithNoDisplayHandsTheBufferBack(self):
		self.handler.displayDimensions.numRows = 0
		self.handler.displayDimensions.numCols = 0
		self.plugin.rebuildBuffer()
		self.assertIs(self.handler.mainBuffer, self.originalBuffer)
		self.assertIsNone(self.plugin.container)


if __name__ == "__main__":
	unittest.main()
