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

import types
import unittest

from ._stubs import (
	CONFIG,
	FakeCallLater,
	FakeHandler,
	FakeNavigatorObject,
	FakeTreeInterceptor,
	Region,
	callAfterQueue,
	displayChanged,
	displaySizeChanged,
	fakeGetFocusRegions,
	fakeVirtualDisplay,
	installStubs,
	log,
	loadPlugin,
	post_configProfileSwitch,
	resetPluginState,
	setBandConfig,
	setSpeechOutputMode,
	spokenMessages,
)

installStubs()
plugin = loadPlugin()
GlobalPlugin = plugin.GlobalPlugin

import braille  # noqa: E402
import config  # noqa: E402
from braille.brailleHandler import BrailleHandler  # noqa: E402

from brlMultiline import patches  # noqa: E402
from brlMultiline.container import PLACEMENT_KEY_ATTRIBUTE, DisplayContainer  # noqa: E402
from brlMultiline.layout import SegmentRect  # noqa: E402
from brlMultiline.messages import MessageBuffer  # noqa: E402
from brlMultiline.panels import BlankPanel, GridPanel, SinglePanel  # noqa: E402
from brlMultiline.devices import deviceMap  # noqa: E402
from brlMultiline.views import SegmentView, validateAgainstHardware  # noqa: E402

MONARCH_ROWS = 8
MONARCH_COLS = 32


class PluginTestCase(unittest.TestCase):
	segmentCount = 4

	def makeHandler(self):
		""":return: the handler the plugin is built against. Overridden to change the display."""
		return FakeHandler(MONARCH_ROWS, MONARCH_COLS)

	def setUp(self):
		resetPluginState()
		CONFIG["segmentCount"] = self.segmentCount
		self.handler = self.makeHandler()
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

	Entering the mode is two things at once: NVDA's braille mode setting changes, and NVDA
	assigns a fresh list to `container.regions`, which empties every segment. That emptying
	is deliberate: in this mode NVDA stops handling focus, caret and review moves, so
	anything left elsewhere would sit under the reader's fingers with nothing to refresh or
	remove it. For the same reason the add-on draws nothing of its own while the mode is on.

	The gap is what happens on the way back. A pin's registration survives, but its content
	does not return until something redraws it, and nothing does so automatically; the tests
	below that leave the mode record that rather than desired behaviour. A panel owner has
	no recovery path at all, which is the strongest argument for giving panels a render
	callback.
	"""

	segmentCount = 8

	def enterSpeechMode(self):
		"""Do both halves of what NVDA does when the display starts showing speech."""
		setSpeechOutputMode(True)
		self.container.regions = [Region("spoken text")]

	def leaveSpeechMode(self):
		"""Only the mode changes: NVDA puts nothing of the add-on's back on the way out."""
		setSpeechOutputMode(False)

	def contentOf(self, key):
		return self.container.segmentForKey(key).regions

	def test_speechOutputEmptiesEverySegmentButTheFocus(self):
		self.pin(1)
		self.assertNotEqual(self.contentOf("display.1"), [])
		self.enterSpeechMode()
		self.assertEqual(self.contentOf("display.1"), [])
		self.assertEqual(len(self.container.focusSegment.regions), 1)

	def test_thePinRegistrationSurvivesSpeechOutput(self):
		self.pin(1)
		self.enterSpeechMode()
		self.assertEqual(self.plugin.monitoredKeys, {"display.1"})

	def test_nothingIsDrawnWhileTheDisplayShowsSpeech(self):
		self.pin(1, name="watch me")
		self.enterSpeechMode()
		self.plugin.refreshMonitors()
		self.assertEqual(self.contentOf("display.1"), [])

	def test_aRebuildDuringSpeechOutputDoesNotResurrectAPin(self):
		"""The regression: a rebuild redrew every pin whatever the display was showing.

		NVDA draws no focus content in this mode, so the pin would arrive beside the speech
		on a display that nothing else is going to touch.
		"""
		self.pin(1, name="watch me")
		self.enterSpeechMode()
		self.plugin.rebuildBuffer()
		self.assertEqual(self.contentOf("display.1"), [])
		self.assertEqual(self.plugin.monitoredKeys, {"display.1"})

	def test_aProfileSwitchDuringSpeechOutputDoesNotResurrectAPin(self):
		"""The same rebuild, reached the way a user is most likely to reach it."""
		self.pin(1, name="watch me")
		self.enterSpeechMode()
		post_configProfileSwitch.notify()
		callAfterQueue.flush()
		self.assertEqual(self.contentOf("display.1"), [])

	def test_pinningDuringSpeechOutputRegistersWithoutDrawing(self):
		self.enterSpeechMode()
		self.pin(1, name="watch me")
		self.assertEqual(self.plugin.monitoredKeys, {"display.1"})
		self.assertEqual(self.contentOf("display.1"), [])

	def test_pinnedContentDoesNotComeBackWhenSpeechOutputEnds(self):
		"""Documents the gap: leaving the mode redraws nothing on its own."""
		self.pin(1, name="watch me")
		self.enterSpeechMode()
		self.leaveSpeechMode()
		self.assertEqual(self.contentOf("display.1"), [])

	def test_refreshMonitorsIsTheRecoveryPath(self):
		"""It exists and works; what is missing is anything that calls it at the right time."""
		self.pin(1, name="watch me")
		self.enterSpeechMode()
		self.leaveSpeechMode()
		self.plugin.refreshMonitors()
		self.assertNotEqual(self.contentOf("display.1"), [])

	def test_aRebuildAfterSpeechOutputEndsRestoresThePin(self):
		"""Which is why the registration is kept while the mode refuses to draw it."""
		self.pin(1, name="watch me")
		self.enterSpeechMode()
		self.leaveSpeechMode()
		self.plugin.rebuildBuffer()
		self.assertNotEqual(self.contentOf("display.1"), [])

	def test_aClaimedPanelHasNoRecoveryPathAtAll(self):
		"""The panel keeps its cells, but nothing can put its content back."""
		self.plugin.activatePanel(SinglePanel("table", SegmentRect(0, 0, 1, MONARCH_COLS)))
		self.container.segmentForKey("table").append(Region("owned content"))
		self.enterSpeechMode()
		self.leaveSpeechMode()
		self.assertTrue(self.container.hasKey("table"))
		self.assertEqual(self.contentOf("table"), [])


class TestDoNewObjectPatch(PluginTestCase):
	"""The installed replacement for `BrailleHandler._doNewObject`, driven end to end.

	This is the main path: every focus change NVDA reports arrives here, and it is where
	the targeting rules have to hold. Reached through the handler class rather than through
	the function, so that a patch which was never installed fails the test too.
	"""

	segmentCount = 8

	def doNewObject(self, *regions):
		BrailleHandler._doNewObject(self.handler, list(regions))

	def test_thePatchIsInstalled(self):
		self.assertIs(BrailleHandler._doNewObject, patches._doNewObjectMultiSegment)

	def test_untargetedRegionsGoToTheFocusSegment(self):
		region = Region("focus content")
		self.doNewObject(region)
		self.assertEqual(self.container.focusSegment.regions, [region])

	def test_aTargetedRegionGoesToItsOwnSegment(self):
		region = Region("pinned content")
		region.targetSegment = "display.1"
		self.doNewObject(region)
		self.assertEqual(self.container.segmentForKey("display.1").regions, [region])
		self.assertEqual(self.container.focusSegment.regions, [])

	def test_aRegionForADepartedSegmentIsDropped(self):
		"""The regression behind the targeting correction: it must not fall back to focus.

		Showing a departed panel's content in the focus segment would write it over the
		user's ordinary braille.
		"""
		region = Region("orphaned content")
		region.targetSegment = "table.r0c0"
		self.doNewObject(region)
		self.assertEqual(self.container.focusSegment.regions, [])
		self.assertFalse(any(segment.regions for segment in self.container.segments))

	def test_aSegmentReceivingNothingKeepsWhatItHad(self):
		"""What makes a pin survive a focus change without anything intervening."""
		kept = Region("pinned thing")
		self.container.segmentForKey("display.1").append(kept)
		self.doNewObject(Region("new focus content"))
		self.assertEqual(self.container.segmentForKey("display.1").regions, [kept])

	def test_aSegmentReceivingRegionsIsClearedFirst(self):
		self.container.focusSegment.append(Region("stale"))
		fresh = Region("fresh")
		self.doNewObject(fresh)
		self.assertEqual(self.container.focusSegment.regions, [fresh])

	def test_wherePlacementWentIsRecordedWithoutBecomingAClaim(self):
		region = Region("focus content")
		self.doNewObject(region)
		self.assertEqual(getattr(region, PLACEMENT_KEY_ATTRIBUTE), self.container.focusSegmentKey)
		self.assertIsNone(getattr(region, "targetSegment", None))

	def test_eachSegmentGetsItsOwnHardLeftAnchor(self):
		"""The rule is about a region's place in its own group, not on the display."""
		first = Region("focus content")
		second = Region("pinned content")
		second.targetSegment = "display.1"
		self.doNewObject(first, second)
		self.assertTrue(first.focusToHardLeft)
		self.assertTrue(second.focusToHardLeft)

	def test_theLastRegionOfEachGroupIsScrolledTo(self):
		"""Every segment that received content is scrolled, not only the focus segment."""
		first = Region("focus content")
		second = Region("pinned content")
		second.targetSegment = "display.1"
		self.doNewObject(first, second)
		self.assertCountEqual(self.handler.scrolledTo, [first, second])

	def test_documentLinesAreNotFilledInWhileTheDisplayShowsSpeech(self):
		"""The same rule the pins follow: nothing of the add-on's own on a display showing speech."""
		from brlMultiline import documentLines

		calls = []
		original = documentLines.populate
		documentLines.populate = lambda *args, **kwargs: calls.append(args)
		self.addCleanup(setattr, documentLines, "populate", original)
		CONFIG["showDocumentLines"] = True
		self.doNewObject(Region("focus content"))
		self.assertEqual(len(calls), 1)
		setSpeechOutputMode(True)
		self.doNewObject(Region("focus content"))
		self.assertEqual(len(calls), 1)


class TestFlowSwitch(PluginTestCase):
	"""Claiming and giving back the flow band, from the setting and from the command.

	The setting is read on every rebuild, which is what makes it answer a configuration
	profile: NVDA switches profile when the foreground application changes, that rebuilds
	the display, and the rebuild is where the band is claimed or given back. Before this the
	flow was a command and nothing else, so a reader who wanted it had to press it again
	after every profile switch, display change and restart.
	"""

	segmentCount = 4

	def toggle(self):
		self.plugin.script_toggleFlow(None)

	def test_theBandIsNotClaimedUntilItIsAskedFor(self):
		"""Installing the add-on must not change how anything reads."""
		self.assertIsNone(self.plugin.flowBand)
		self.assertEqual(self.container.numSegments, 4)

	def test_aRebuildClaimsTheBandWhenTheSettingSaysSo(self):
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		self.assertIsNotNone(self.plugin.flowBand)
		self.assertTrue(self.plugin.flowBand.isClaimed)

	def test_aProfileThatTurnsItOffGivesTheBandBack(self):
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		CONFIG["flowEnabled"] = False
		post_configProfileSwitch.notify()
		callAfterQueue.flush()
		self.assertIsNone(self.plugin.flowBand)
		self.assertEqual(self.container.numSegments, 4)

	def test_theBandIsClaimedEvenWithNothingToFlowHere(self):
		"""It presents the focus as an undivided display would, and lights up at a document.

		Giving it back would mean asking for it again after every dialog.
		"""
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		self.assertTrue(self.plugin.flowBand.isClaimed)
		self.assertFalse(self.plugin.flowBand.isShowing)

	def test_theCommandRemembersItsAnswer(self):
		self.toggle()
		self.assertTrue(self.plugin.flowBand.isClaimed)
		from brlMultiline import bmConfig

		self.assertTrue(bmConfig.isFlowEnabled())

	def test_theCommandTurnsItOffAgain(self):
		self.toggle()
		self.toggle()
		self.assertIsNone(self.plugin.flowBand)
		self.assertEqual(self.container.numSegments, 4)

	def test_theCommandSaysWhichWayItWent(self):
		self.toggle()
		self.assertIn("Flow on", spokenMessages)
		self.toggle()
		self.assertIn("Flow off", spokenMessages)

	def test_turningItOnWithNothingSetToFlowSaysSo(self):
		"""A band that could never show anything is not claimed, and the reader is told why."""
		CONFIG["flowBrowseMode"] = False
		self.toggle()
		self.assertIsNone(self.plugin.flowBand)
		self.assertTrue(any("nothing is set to flow" in message for message in spokenMessages))

	def test_claimingTheBandDoesNotClaimItAgain(self):
		"""Claiming rebuilds the display, and the rebuild is where claiming happens."""
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		first = self.plugin.flowBand
		self.plugin.rebuildBuffer()
		self.assertIs(self.plugin.flowBand, first)

	def test_anInactiveBandStillFollowsFocusAfterARebuild(self):
		"""A new segment needs the owner's callback even when nothing was flowing yet."""
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		band = self.plugin.flowBand
		self.assertIsNone(band.controller)
		self.plugin.rebuildBuffer()
		segment = band.segment()
		self.assertIsNotNone(segment.onFocusRegions)
		page = FakeTreeInterceptor(["a page"])
		self.assertTrue(segment.acceptFocusRegions(fakeGetFocusRegions(page)))
		self.assertTrue(band.isShowing)

	def test_changingRowsReclaimsAnEnabledBand(self):
		"""Re-composing a panel preserves its old rectangle; settings must replace it."""
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		first = self.plugin.flowBand
		self.assertEqual(first.segment().rect.numRows, MONARCH_ROWS)
		CONFIG["flowRows"] = 3
		self.plugin.rebuildBuffer()
		self.assertIsNot(self.plugin.flowBand, first)
		self.assertEqual(self.plugin.flowBand.segment().rect.numRows, 3)

	def test_anEnabledProfileMayChangeTheBandsRows(self):
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		CONFIG["flowRows"] = 2
		post_configProfileSwitch.notify()
		callAfterQueue.flush()
		self.assertEqual(self.plugin.flowBand.segment().rect.numRows, 2)

	def test_terminatingWithABandIsClean(self):
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		self.plugin.terminate()
		self.assertIsNone(self.plugin.flowBand)


class TestFlowTargetSwitch(PluginTestCase):
	"""An enabled flow moves when its chosen physical display changes."""

	def makeHandler(self):
		handler = FakeHandler(9, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 32),
			("freedomScientific", 8, 1, 80),
		)
		return handler

	def test_changingTheDisplayReclaimsTheBandThere(self):
		CONFIG["flowEnabled"] = True
		CONFIG["flowDisplay"] = "hidBrailleStandard"
		self.plugin.rebuildBuffer()
		first = self.plugin.flowBand
		self.assertEqual(first.segment().rect, SegmentRect(0, 0, 8, 32))
		CONFIG["flowDisplay"] = "freedomScientific"
		self.plugin.rebuildBuffer()
		self.assertIsNot(self.plugin.flowBand, first)
		self.assertEqual(self.plugin.flowBand.segment().rect, SegmentRect(8, 0, 1, 80))


class TestSegmentsSwitch(PluginTestCase):
	"""Turning the configured layout off and on, from the command and from the settings.

	The switch is over the user's own layout. A view or a panel activated by code says what
	the display is being used for rather than what the user prefers, so it is not overridden;
	the alternative would be a table reader silently losing its grid to a setting.
	"""

	segmentCount = 4

	def toggle(self):
		self.plugin.script_toggleSegments(None)

	def test_theCommandUndividesTheDisplay(self):
		self.assertEqual(self.container.numSegments, 4)
		self.toggle()
		self.assertEqual(self.container.numSegments, 1)

	def test_theCommandDividesItAgain(self):
		self.toggle()
		self.toggle()
		self.assertEqual(self.container.numSegments, 4)

	def test_theCommandSaysWhichWayItWent(self):
		self.toggle()
		self.assertIn("Segments off", spokenMessages)
		self.toggle()
		self.assertIn("Segments on", spokenMessages)

	def test_theLayoutIsNotDisturbed(self):
		CONFIG["segmentSizes"] = [1, 5, 2]
		self.plugin.rebuildBuffer()
		self.toggle()
		self.toggle()
		self.assertEqual(self.container.numSegments, 3)

	def test_pinsAreReleasedWhenTheDisplayIsUndivided(self):
		"""There is one segment and it follows the focus, so no pin can be kept."""
		self.pin(1)
		self.assertEqual(self.plugin.monitoredKeys, {"display.1"})
		self.toggle()
		self.assertEqual(self.plugin.monitoredKeys, set())

	def test_pinningIsRefusedWhileUndivided(self):
		self.toggle()
		spokenMessages.clear()
		self.pin(0)
		self.assertEqual(self.plugin.monitoredKeys, set())
		self.assertTrue(any("not divided into segments" in message for message in spokenMessages))

	def test_anActivatedViewIsNotOverridden(self):
		"""A claim by code is not a preference, so the user's switch does not answer it."""
		self.toggle()
		self.plugin.activateView(
			SegmentView(
				"table",
				[
					SinglePanel("top", SegmentRect(0, 0, 4, MONARCH_COLS)),
					SinglePanel("bottom", SegmentRect(4, 0, 4, MONARCH_COLS)),
				],
			),
		)
		self.assertEqual(self.container.numSegments, 2)

	def test_anActivatedPanelIsComposedOverTheUndividedDisplay(self):
		self.toggle()
		self.plugin.activatePanel(
			SinglePanel("table", SegmentRect(0, 0, 2, MONARCH_COLS), focusSegmentKey="table"),
		)
		self.assertTrue(self.container.hasKey("table"))

	def test_aClaimOnTheUndividedDisplayMustOfferAFocusSegment(self):
		"""The one segment holds the focus, so every claim touching it evicts the focus.

		Divided, a claim over the top rows leaves the focus segment further down alone and
		need offer nothing. Undivided there is nowhere else for it to be, so the rule that
		applies to any claim evicting the focus applies to every claim.
		"""
		self.toggle()
		with self.assertRaises(LookupError):
			self.plugin.activatePanel(SinglePanel("table", SegmentRect(0, 0, 2, MONARCH_COLS)))

	def test_aClaimThatCannotSurviveUndividingIsDropped(self):
		"""Reported rather than raised: the rebuild was asked for by a setting, not a claim."""
		self.plugin.activatePanel(SinglePanel("table", SegmentRect(0, 0, 2, MONARCH_COLS)))
		self.assertTrue(self.container.hasKey("table"))
		self.toggle()
		self.assertFalse(self.container.hasKey("table"))
		self.assertTrue(any("cannot be shown" in message for level, message in log.messages))


class TestNativePanning(PluginTestCase):
	"""NVDA's own panning keys, on a Monarch above a Focus 80.

	The Monarch is divided in two and the Focus is whole, so segments 0 and 1 are on the
	Monarch and segment 2 is on the Focus.
	"""

	def makeHandler(self):
		handler = FakeHandler(9, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 32),
			("freedomScientific", 8, 1, 80),
		)
		return handler

	def setUp(self):
		super().setUp()
		setBandConfig("hidBrailleStandard_8x32", segmentCount=2)
		setBandConfig("freedomScientific_1x80", segmentCount=1)
		self.rebuild()

	def rebuild(self):
		"""Rebuild and give every segment more content than it can show at once.

		The largest segment here is four rows of 32, so the text has to be longer than 128
		cells or a segment simply has nowhere to pan to and the test proves nothing.
		"""
		self.plugin.rebuildBuffer()
		self.handler.buffer = self.handler.mainBuffer
		for segment in self.container.segments:
			segment.append(Region("word " * 80))
		self.container.update()

	def pan(self, source, forward=True):
		"""Press a display's own panning key.

		The display is put in place around the call, which is what the wrapper around NVDA's
		panning command does for as long as the command runs. `test_panning` covers the wrapper
		and every way a gesture can fail to reach one; this covers what the handler does once a
		display is known.
		"""
		from brlMultiline import panning

		panning._activeSource = source
		try:
			if forward:
				BrailleHandler.scrollForward(self.handler)
			else:
				BrailleHandler.scrollBack(self.handler)
		finally:
			panning._activeSource = None

	def movedSegments(self):
		"""Which segments panned.

		Two ways to tell, because the container pans them two ways: the focus segment scrolls
		as an ordinary buffer does, and any other segment moves its own window instead, so
		that panning something the user is only reading cannot drag the caret.
		"""
		return [
			index
			for index, segment in enumerate(self.container.segments)
			if segment.scrolled is not None or segment.windowStartPos != 0
		]

	def test_aDisplaysKeysDriveItsOwnSegment(self):
		"""The hardware bug: the Monarch's keys were scrolling the Focus."""
		self.pan("hidBrailleStandard")
		self.assertEqual(self.movedSegments(), [0])

	def test_theOtherDisplaysKeysDriveTheFocusSegment(self):
		self.pan("freedomScientific")
		self.assertEqual(self.movedSegments(), [2])

	def test_aDisplayHoldingTheFocusDrivesTheFocusSegment(self):
		CONFIG["focusSegment"] = 1
		self.rebuild()
		self.pan("hidBrailleStandard")
		self.assertEqual(self.movedSegments(), [1])

	def test_scrollingWithNoKeyPressBehindItIsUnchanged(self):
		"""Automatic scroll, which must go on driving the focus segment."""
		self.pan(None)
		self.assertEqual(self.movedSegments(), [2])

	def test_theDirectionIsThePressedDisplays(self):
		"""The focus on the Monarch, so the direction is observable as a scroll either way."""
		CONFIG["focusSegment"] = 0
		setBandConfig("hidBrailleStandard_8x32", segmentCount=2, reverseScrollBtns=True)
		self.rebuild()
		self.pan("hidBrailleStandard", forward=True)
		self.assertEqual(self.container.segments[0].scrolled, "back")

	def test_theOtherDisplayKeepsItsOwnDirection(self):
		setBandConfig("hidBrailleStandard_8x32", segmentCount=2, reverseScrollBtns=True)
		self.rebuild()
		self.pan("freedomScientific", forward=True)
		self.assertEqual(self.container.segments[2].scrolled, "forward")

	def test_onePressDrivesOneScroll(self):
		"""The display lasts only as long as its command, so the next scroll starts clean."""
		self.pan("hidBrailleStandard")
		self.assertEqual(self.movedSegments(), [0])
		self.pan(None)
		self.assertEqual(self.movedSegments(), [0, 2])


class TestDisplayRelativeCommands(PluginTestCase):
	"""Commands that name a segment by which display it is on."""

	def makeHandler(self):
		handler = FakeHandler(9, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 32),
			("freedomScientific", 8, 1, 80),
		)
		return handler

	def setUp(self):
		super().setUp()
		setBandConfig("hidBrailleStandard_8x32", segmentCount=2)
		setBandConfig("freedomScientific_1x80", segmentCount=1)
		self.plugin.rebuildBuffer()

	def test_theSecondDisplaysFirstSegmentIsResolved(self):
		self.assertEqual(self.plugin.displaySegmentNumber(1, 0), 2)

	def test_theFirstDisplaysSegmentsAreResolved(self):
		self.assertEqual(self.plugin.displaySegmentNumber(0, 0), 0)
		self.assertEqual(self.plugin.displaySegmentNumber(0, 1), 1)

	def test_theBindingSurvivesARearrangement(self):
		"""What the whole family is for: the index moved and the command did not."""
		self.assertEqual(self.plugin.displaySegmentNumber(1, 0), 2)
		setBandConfig("hidBrailleStandard_8x32", segmentCount=4)
		self.plugin.rebuildBuffer()
		self.assertEqual(self.plugin.displaySegmentNumber(1, 0), 4)

	def test_aSegmentThatIsNotThereIsReported(self):
		self.assertIsNone(self.plugin.displaySegmentNumber(1, 3))
		self.assertTrue(any("No segment" in message for message in spokenMessages), spokenMessages)

	def test_theScrollScriptsExist(self):
		self.assertTrue(hasattr(self.plugin, "script_scrollDisplay1Segment0Forward"))
		self.assertTrue(hasattr(self.plugin, "script_scrollDisplay2Segment3Back"))

	def test_theMonitorScriptsExist(self):
		self.assertTrue(hasattr(self.plugin, "script_monitorObjectInDisplay1Segment0"))
		self.assertTrue(hasattr(self.plugin, "script_stopMonitoringDisplay0Segment1"))

	def test_aScrollScriptScrollsTheRightSegment(self):
		for segment in self.container.segments:
			segment.append(Region("some words to pan through"))
		self.container.update()
		self.plugin.script_scrollDisplay1Segment0Forward(None)
		self.assertEqual(self.container.segments[2].scrolled, "forward")

	def test_aMonitorScriptPinsTheRightSegment(self):
		import api

		api.getNavigatorObject = lambda: FakeNavigatorObject("a pinned thing")
		self.plugin.script_monitorObjectInDisplay0Segment0(None)
		self.assertIn("device.hidBrailleStandard.0", self.plugin.monitoredKeys)

	def test_aMonitorScriptOnAMissingSegmentPinsNothing(self):
		import api

		api.getNavigatorObject = lambda: FakeNavigatorObject("a pinned thing")
		self.plugin.script_monitorObjectInDisplay1Segment2(None)
		self.assertEqual(self.plugin.monitoredKeys, set())


class TestPatchOwnership(PluginTestCase):
	"""`BrailleHandler` is a class every add-on can reach, and several of them do.

	Installing over someone else's method and restoring over it are the two ways a monkey patch
	damages something other than itself. The second is the worse one, because it happens while
	this add-on is being disabled and looks like the other add-on breaking.
	"""

	def restore(self, name, original):
		setattr(BrailleHandler, name, original)
		patches._originals.pop(name, None)
		patches._installedMethods.pop(name, None)

	def somebodyElseTakesOver(self, name):
		"""Another add-on replaces one of the patched methods after this one did."""

		def theirs(handler, *args, **kwargs):
			pass

		self.addCleanup(self.restore, name, patches._originals[name])
		setattr(BrailleHandler, name, theirs)
		return theirs

	def test_everyPatchIsInstalled(self):
		for name, replacement in patches._replacements().items():
			self.assertIs(getattr(BrailleHandler, name), replacement)

	def test_removingPutsNVDAsOwnMethodsBack(self):
		originals = dict(patches._originals)
		patches.remove()
		for name, original in originals.items():
			self.assertIs(getattr(BrailleHandler, name), original)

	def test_removingLeavesAnotherAddOnsMethodAlone(self):
		theirs = self.somebodyElseTakesOver("scrollForward")
		patches.remove()
		self.assertIs(BrailleHandler.scrollForward, theirs)

	def test_theOtherPatchesAreStillTakenBack(self):
		"""One method being someone else's is no reason to leave the rest patched."""
		original = patches._originals["scrollBack"]
		self.somebodyElseTakesOver("scrollForward")
		patches.remove()
		self.assertIs(BrailleHandler.scrollBack, original)

	def test_aLaterInstallDoesNotStackOnTopOfTheirs(self):
		"""And this is the one that would hang NVDA rather than merely misbehave.

		If they wrapped this add-on's method rather than replacing it, installing again would
		put this one over a wrapper that calls it, and the two would call each other until the
		stack ran out.
		"""
		theirs = self.somebodyElseTakesOver("scrollForward")
		patches.remove()
		patches.install()
		self.assertIs(BrailleHandler.scrollForward, theirs)

	def test_theRestAreInstalledAgainAfterwards(self):
		self.somebodyElseTakesOver("scrollForward")
		patches.remove()
		patches.install()
		self.assertIs(BrailleHandler.scrollBack, patches._scrollBackMaybeReversed)


class TestLosingADisplay(PluginTestCase):
	"""What the reader is left with when one of two displays goes.

	The rule, in the author's words: the focus has to end up on a display that is still there.
	If what is left has room, recover what the lost display was showing into it. If what is
	left is a single segment, do not — that segment follows the focus, and taking it for a
	pinned object would leave the reader with neither.
	"""

	def makeHandler(self):
		handler = FakeHandler(9, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 32),
			("freedomScientific", 8, 1, 80),
		)
		return handler

	def setUp(self):
		super().setUp()
		setBandConfig("hidBrailleStandard_8x32", segmentCount=2)
		setBandConfig("freedomScientific_1x80", segmentCount=1)
		self.plugin.rebuildBuffer()

	def loseTheMonarch(self, focusSegments=1):
		"""Let the Monarch go, as the driver's own `_relayout` leaves things.

		Three parts, and all three are the driver's doing: the slot is marked failed, the
		display that is left moves up to row 0, and the composite is one row of 80.

		:param focusSegments: how many segments the Focus is divided into afterwards.
		"""
		self.handler.display.slots[0].failed = True
		self.handler.display.slots[1].band.rowStart = 0
		self.handler.displayDimensions.numRows = 1
		self.handler.displayDimensions.numCols = 80
		setBandConfig("freedomScientific_1x80", segmentCount=focusSegments)
		self.plugin.rebuildBuffer()

	def test_whatIsLeftIsArrangedByItself(self):
		self.loseTheMonarch()
		self.assertEqual(self.container.numSegments, 1)
		self.assertEqual(self.container.rects[0], SegmentRect(row=0, col=0, numRows=1, numCols=80))

	def test_theFocusIsOnTheDisplayThatIsStillThere(self):
		self.loseTheMonarch()
		self.assertEqual(self.container.focusSegmentKey, "device.freedomScientific.0")

	def test_aPinOnTheLostDisplayMovesToASegmentThatIsFree(self):
		"""The Focus in two: one follows the focus, and the other can take the orphan."""
		self.pin(0)
		self.assertEqual(self.plugin.monitoredKeys, {"device.hidBrailleStandard.0"})
		self.loseTheMonarch(focusSegments=2)
		self.assertEqual(self.plugin.monitoredKeys, {"device.freedomScientific.0"})

	def test_aPinIsReleasedWhenTheOneSegmentLeftFollowsTheFocus(self):
		"""Taking it would leave the reader with something they did not ask to be reading."""
		self.pin(0)
		self.loseTheMonarch(focusSegments=1)
		self.assertEqual(self.plugin.monitoredKeys, set())

	def test_theMovedPinActuallyDrawsInItsNewHome(self):
		"""Filing it under a new key moves nothing.

		The monitor looks its segment up by the key it holds, and the regions it has already
		built carry `targetSegment`, which is what decides where a region is placed. A pin
		moved by the dictionary alone looks moved and draws nowhere.
		"""
		self.pin(0)
		monitor = self.plugin._monitors["device.hidBrailleStandard.0"]
		self.plugin.refreshMonitors()
		self.loseTheMonarch(focusSegments=2)
		self.assertEqual(monitor.segmentKey, "device.freedomScientific.0")
		self.assertTrue(monitor.regions)
		for region in monitor.regions:
			self.assertEqual(region.targetSegment, "device.freedomScientific.0")
		self.plugin.refreshMonitors()
		self.assertEqual(
			self.container.segmentForKey("device.freedomScientific.0").regions,
			monitor.regions,
		)

	def test_theOldSegmentIsNotLeftHoldingIt(self):
		"""There is no old segment; what matters is that nothing else claims to hold the pin."""
		self.pin(0)
		self.plugin.refreshMonitors()
		self.loseTheMonarch(focusSegments=2)
		self.plugin.refreshMonitors()
		monitor = self.plugin._monitors["device.freedomScientific.0"]
		for index, segment in enumerate(self.container.segments):
			if self.container.specs[index].key == "device.freedomScientific.0":
				continue
			self.assertNotEqual(segment.regions, monitor.regions)

	def test_theMoveIsReported(self):
		self.pin(0)
		self.loseTheMonarch(focusSegments=2)
		self.assertTrue(
			any("has gone" in message for level, message in log.messages if level == "info"),
			log.messages,
		)

	def test_twoPinsCompeteForOneFreeSegment(self):
		"""Only one can have it, and the other is released rather than sharing."""
		setBandConfig("hidBrailleStandard_8x32", segmentCount=3)
		self.plugin.rebuildBuffer()
		self.pin(0)
		self.pin(1)
		self.loseTheMonarch(focusSegments=2)
		self.assertEqual(len(self.plugin.monitoredKeys), 1)

	def test_aPinOnTheSurvivingDisplayIsNotDisturbed(self):
		"""It has not lost anything, so nothing about it should move.

		The Focus in two throughout: its second segment follows the focus, and its first is
		pinned before and after.
		"""
		setBandConfig("freedomScientific_1x80", segmentCount=2)
		self.plugin.rebuildBuffer()
		self.pin(2)
		self.assertEqual(self.plugin.monitoredKeys, {"device.freedomScientific.0"})
		self.loseTheMonarch(focusSegments=2)
		self.assertEqual(self.plugin.monitoredKeys, {"device.freedomScientific.0"})

	def test_nothingIsLaidOverTheDisplayThatHasGone(self):
		self.loseTheMonarch()
		for rect in self.container.rects:
			self.assertLess(rect.row, 1, f"{rect} is on rows the display no longer has")


class TestMembersChangedSubscription(PluginTestCase):
	"""That the plugin hears the composite's own notification, through the real one.

	Worth a class of its own because the driver tests prove the event is raised and the plugin
	tests proved a rebuild happens when *something* fires — and neither shows the two are the
	same event. Replacing the lookup with None left every plugin test passing.
	"""

	def makeHandler(self):
		handler = FakeHandler(9, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 32),
			("freedomScientific", 8, 1, 80),
		)
		return handler

	def membersChanged(self):
		"""The canonical action, imported the way the plugin imports it."""
		from brailleDisplayDrivers.brlMultilineVirtual.events import membersChanged

		return membersChanged

	def test_thePluginIsSubscribedToTheCanonicalAction(self):
		self.assertIn(self.plugin._handleDisplayChanged, self.membersChanged().handlers)

	def test_theMembersChangingRebuildsFromTheNewDeviceMap(self):
		"""The same size case: nothing about the display's dimensions has changed."""
		setBandConfig("hidBrailleStandard_8x32", segmentCount=2)
		setBandConfig("freedomScientific_1x80", segmentCount=1)
		self.plugin.rebuildBuffer()
		self.assertEqual(self.container.numSegments, 3)
		# One display goes, and the driver lays the rest out again before it says so.
		self.handler.display.slots[0].failed = True
		self.handler.display.slots[1].band.rowStart = 0
		self.handler.displayDimensions.numRows = 1
		self.membersChanged().notify(display=self.handler.display)
		callAfterQueue.flush()
		self.assertEqual(self.container.numSegments, 1)
		self.assertTrue(self.container.hasKey("device.freedomScientific.0"))

	def test_nothingHappensAfterThePluginHasGone(self):
		self.plugin.terminate()
		self.assertNotIn(self.plugin._handleDisplayChanged, self.membersChanged().handlers)
		self.membersChanged().notify(display=self.handler.display)
		self.assertEqual(callAfterQueue.pending, [])
		self.assertIs(self.handler.mainBuffer, self.originalBuffer)


class NvdaMessageBuffer:
	"""A stand-in for the message buffer NVDA builds for itself, with nothing of ours in it."""

	def __init__(self):
		self.cleared = False

	def clear(self):
		self.cleared = True


class TestMessageBuffer(PluginTestCase):
	"""Flash messages go into a segment rather than across the whole display."""

	segmentCount = 4

	def messageBuffer(self):
		return self.handler.messageBuffer

	def test_nvdasOwnMessageBufferIsReplaced(self):
		self.assertIsInstance(self.messageBuffer(), MessageBuffer)

	def test_messagesFollowTheFocusSegmentByDefault(self):
		"""Which is where a message lands on an undivided display."""
		self.assertEqual(self.messageBuffer().rect, self.container.focusSegment.rect)

	def test_aChosenSegmentIsUsedInstead(self):
		CONFIG["messageSegment"] = 0
		self.plugin.rebuildBuffer()
		self.assertEqual(self.messageBuffer().rect, self.container.segments[0].rect)

	def test_aSegmentThatDoesNotExistFallsBackToTheFocus(self):
		CONFIG["messageSegment"] = 7
		self.plugin.rebuildBuffer()
		self.assertEqual(self.messageBuffer().rect, self.container.focusSegment.rect)
		self.assertTrue(
			any("for messages" in message for level, message in log.messages),
			log.messages,
		)

	def test_theSameObjectIsKeptAcrossRebuilds(self):
		"""The handler decides a message is showing by comparing identity with this object."""
		before = self.messageBuffer()
		CONFIG["messageSegment"] = 1
		self.plugin.rebuildBuffer()
		self.assertIs(self.messageBuffer(), before)
		self.assertEqual(self.messageBuffer().rect, self.container.segments[1].rect)

	def test_aShowingMessageSurvivesARebuild(self):
		"""Replacing the object under a live message would leave one nothing could dismiss."""
		self.handler.buffer = self.messageBuffer()
		self.plugin.rebuildBuffer()
		self.assertIs(self.handler.buffer, self.handler.messageBuffer)

	def test_terminateGivesNVDAItsOwnBufferBack(self):
		original = self.plugin._originalMessageBuffer
		self.plugin.terminate()
		self.assertIs(self.handler.messageBuffer, original)

	def showAMessage(self):
		"""Put a message up, as `BrailleHandler.message` does, timeout and all."""
		buffer = self.messageBuffer()
		buffer.clear()
		region = Region("a message")
		region.update()
		buffer.regions.append(region)
		buffer.update()
		self.handler.buffer = buffer
		self.handler._messageCallLater = FakeCallLater()
		return buffer

	def test_terminateWhileAMessageIsShowingHandsEveryBufferBack(self):
		"""Both buffers, and the display showing the one NVDA expects to be showing.

		The message cannot come with us: its regions are in a buffer that stops being the
		handler's. So it is dismissed the way NVDA dismisses one, which is the part that was
		missing — leaving the display on an emptied buffer that was no longer anybody's, with
		nothing scheduled to move it on if messages are shown indefinitely.
		"""
		originalMessageBuffer = self.plugin._originalMessageBuffer
		self.showAMessage()
		self.plugin.terminate()
		self.assertIs(self.handler.messageBuffer, originalMessageBuffer)
		self.assertIs(self.handler.mainBuffer, self.originalBuffer)
		self.assertIs(self.handler.buffer, self.handler.mainBuffer)

	def test_terminateWhileAMessageIsShowingStopsItsTimeout(self):
		"""Left running it fires into `_dismissMessage`, which would clear the main buffer."""
		self.showAMessage()
		timer = self.handler._messageCallLater
		self.plugin.terminate()
		self.assertTrue(timer.stopped)
		self.assertIsNone(self.handler._messageCallLater)

	def test_theDisplayIsRedrawnAfterAMessageIsHandedBack(self):
		"""So that braille shows something again without waiting for the next event."""
		self.showAMessage()
		self.handler.gainedFocus.clear()
		self.plugin.terminate()
		self.assertTrue(self.handler.gainedFocus)

	def test_aMessageInAReplacedBufferIsDismissedRatherThanLeftShowing(self):
		"""Something else has put its own message buffer there — another add-on, most likely.

		Not NVDA: `messageBuffer` is assigned once, in `BrailleHandler.__init__`, so a rebuilt
		one arrives with a whole new handler. The message in the replacement belongs to it, so
		taking over cannot carry it along; what it must not do is leave the display on a buffer
		nothing will write to again.
		"""
		replacement = NvdaMessageBuffer()
		self.handler.messageBuffer = self.handler.buffer = replacement
		timer = self.handler._messageCallLater = FakeCallLater()
		self.plugin.rebuildBuffer()
		self.assertTrue(replacement.cleared)
		self.assertIs(self.handler.messageBuffer, self.plugin._messageBuffer)
		self.assertIs(self.handler.buffer, self.handler.mainBuffer)
		self.assertTrue(timer.stopped)

	def test_aReplacedBufferIsTheOneGivenBack(self):
		"""Whatever was there when we took over is what termination owes, not what was there
		at startup. Restoring the older one would throw away another add-on's buffer."""
		replacement = NvdaMessageBuffer()
		self.handler.messageBuffer = replacement
		self.plugin.rebuildBuffer()
		self.plugin.terminate()
		self.assertIs(self.handler.messageBuffer, replacement)

	def test_losingTheDisplayGivesNVDAItsOwnBufferBack(self):
		original = self.plugin._originalMessageBuffer
		self.handler.displayDimensions.numRows = 0
		self.handler.displayDimensions.numCols = 0
		self.plugin.rebuildBuffer()
		self.assertIs(self.handler.messageBuffer, original)


class TestNoDisplay(PluginTestCase):
	def test_aRebuildWithNoDisplayHandsTheBufferBack(self):
		self.handler.displayDimensions.numRows = 0
		self.handler.displayDimensions.numCols = 0
		self.plugin.rebuildBuffer()
		self.assertIs(self.handler.mainBuffer, self.originalBuffer)
		self.assertIsNone(self.plugin.container)


class TestCompositeDisplay(PluginTestCase):
	"""A display that is a Monarch above a Focus 80, driven through the add-on's own driver."""

	def makeHandler(self):
		handler = FakeHandler(9, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 32),
			("freedomScientific", 8, 1, 80),
		)
		return handler

	def setUp(self):
		super().setUp()
		# After the plugin is up, since building it resets the stub configuration.
		setBandConfig("hidBrailleStandard_8x32", segmentCount=2)
		setBandConfig("freedomScientific_1x80", segmentCount=1)
		self.plugin.rebuildBuffer()

	def test_theDisplayIsArrangedByItsPhysicalDisplays(self):
		self.assertEqual(self.plugin.currentView.name, "devices")
		self.assertEqual(self.container.numSegments, 3)

	def test_theDeadColumnsAreClaimedByNothing(self):
		"""Nothing may be written to cells that reach no hardware."""
		for rect in self.container.rects:
			if rect.row < 8:
				self.assertLessEqual(rect.endCol, 32)

	def test_theCompositeSegmentCountIsNotUsed(self):
		"""It has none of its own: the displays behind it each answer for their own rows."""
		CONFIG["segmentCount"] = 8
		self.plugin.rebuildBuffer()
		self.assertEqual(self.container.numSegments, 3)

	def test_aClaimStillComposesOverIt(self):
		"""The claim takes the Monarch's rows; the Focus keeps its segment and its key."""
		self.plugin.activatePanel(
			SinglePanel("reader", SegmentRect(row=0, col=0, numRows=8, numCols=32)),
		)
		self.assertTrue(self.container.hasKey("reader"))
		self.assertTrue(self.container.hasKey("device.freedomScientific.0"))

	def test_aPinOnTheSecondDisplaySurvivesTheOtherBeingRearranged(self):
		"""What the whole phase is for: a pinned object held on the display beside the focus."""
		# The focus onto the Monarch, so that the Focus 80 is free to be pinned: a pin is
		# refused on the segment following the focus, which would otherwise redraw over it.
		CONFIG["focusSegment"] = 0
		self.plugin.rebuildBuffer()
		self.pin(2)
		setBandConfig("hidBrailleStandard_8x32", segmentCount=4)
		self.plugin.rebuildBuffer()
		self.assertIn("device.freedomScientific.0", self.plugin._monitors)

	def claimAcrossTheMonarch(self, numCols):
		"""A claim over the Monarch's rows, of a given width."""
		return SinglePanel("reader", SegmentRect(row=0, col=0, numRows=8, numCols=numCols))

	def test_aClaimWiderThanItsDisplayIsRefused(self):
		"""The mask is an ordinary panel, so a wide claim evicts it and takes the cells.

		Cells 32 to 79 of those rows reach no hardware. A segment over them would be filled
		by NVDA and thrown away by the driver's slice, with nothing raised and nothing logged.
		The caller is told at once, as it is for a claim that does not fit.
		"""
		with self.assertRaises(ValueError):
			self.plugin.activatePanel(self.claimAcrossTheMonarch(80))
		self.assertFalse(self.container.hasKey("reader"))

	def test_aRefusedClaimLeavesTheDisplayAsItWas(self):
		with self.assertRaises(ValueError):
			self.plugin.activatePanel(self.claimAcrossTheMonarch(80))
		for rect in self.container.rects:
			if rect.row < 8:
				self.assertLessEqual(rect.endCol, 32)

	def test_aClaimIsDroppedWhenItsDisplayIsReplacedByANarrowerOne(self):
		"""Accepted when it was made, and no longer possible. Dropped rather than refused."""
		self.plugin.activatePanel(self.claimAcrossTheMonarch(32))
		self.assertTrue(self.container.hasKey("reader"))
		self.handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 16),
			("freedomScientific", 8, 1, 80),
		)
		setBandConfig("hidBrailleStandard_8x16", segmentCount=1)
		self.plugin.rebuildBuffer()
		self.assertFalse(self.container.hasKey("reader"))
		self.assertTrue(
			any("cannot be shown" in message for level, message in log.messages),
			log.messages,
		)

	def test_aClaimWithinItsDisplayIsKept(self):
		self.plugin.activatePanel(self.claimAcrossTheMonarch(32))
		self.assertTrue(self.container.hasKey("reader"))

	def test_anActivatedViewReachingDeadCellsIsRefused(self):
		"""A view arrives whole, so it never passes through the composite view builder."""
		view = SegmentView(
			"wide",
			[SinglePanel("all", SegmentRect(row=0, col=0, numRows=9, numCols=80))],
		)
		with self.assertRaises(ValueError):
			self.plugin.activateView(view)
		self.assertEqual(self.plugin.currentView.name, "devices")

	def test_anActivatedViewWithinTheDisplaysIsAllowed(self):
		view = SegmentView(
			"narrow",
			[
				SinglePanel("top", SegmentRect(row=0, col=0, numRows=8, numCols=32)),
				BlankPanel(SegmentRect(row=0, col=32, numRows=8, numCols=48)),
				SinglePanel("bottom", SegmentRect(row=8, col=0, numRows=1, numCols=80)),
			],
		)
		self.plugin.activateView(view)
		self.assertEqual(self.plugin.currentView.name, "narrow")

	def test_aClaimSpanningTwoDisplaysIsRefused(self):
		"""The Monarch's last row and the Focus's row. Every cell is live, and it is still wrong.

		Nothing is lost here, which is what makes it worth its own test: the cells all reach
		hardware, so the only symptom is that the segment belongs to no display, and everything
		that addresses a segment through its display stops being able to find it.
		"""
		self.focusOntoTheMonarch()
		with self.assertRaises(ValueError):
			self.plugin.activatePanel(
				SinglePanel("reader", SegmentRect(row=7, col=0, numRows=2, numCols=32)),
			)
		self.assertFalse(self.container.hasKey("reader"))
		self.assertEqual(self.container.numSegments, 3)

	def test_anActivatedViewSpanningTwoDisplaysIsRefused(self):
		view = SegmentView(
			"across",
			[
				BlankPanel(SegmentRect(row=0, col=0, numRows=7, numCols=32)),
				SinglePanel("across", SegmentRect(row=7, col=0, numRows=2, numCols=32)),
				BlankPanel(SegmentRect(row=0, col=32, numRows=8, numCols=48)),
				BlankPanel(SegmentRect(row=8, col=32, numRows=1, numCols=48)),
			],
		)
		with self.assertRaises(ValueError):
			self.plugin.activateView(view)
		self.assertEqual(self.plugin.currentView.name, "devices")

	def focusOntoTheMonarch(self):
		"""Put the system focus on the Monarch's first segment.

		So that a claim over the boundary between the two displays is refused for spanning it
		rather than for evicting the focus segment, which by default is the Focus's.
		"""
		CONFIG["focusSegment"] = 0
		self.plugin.rebuildBuffer()

	def test_aSegmentWithinOneDisplayIsStillAllowedAtEitherEdge(self):
		"""Both sides of the boundary, so that the check is about spanning it and not about it."""
		self.focusOntoTheMonarch()
		self.plugin.activatePanel(
			SinglePanel("last", SegmentRect(row=7, col=0, numRows=1, numCols=32)),
		)
		self.assertTrue(self.container.hasKey("last"))
		self.plugin.activatePanel(
			SinglePanel("beside", SegmentRect(row=8, col=0, numRows=1, numCols=80)),
		)
		self.assertTrue(self.container.hasKey("beside"))

	def failTheFirstContainer(self):
		"""Make the first container built raise, as one built from an impossible view does.

		The fallback is otherwise unreachable from a test, and it is the one arrangement that
		gets installed without ever having been through `validateAgainstHardware`.
		"""
		real = DisplayContainer.__init__
		built = []

		def failOnce(container, handler, view):
			built.append(view)
			if len(built) == 1:
				raise ValueError("this view cannot be built")
			real(container, handler, view)

		# The constructor rather than the class, so that the class stays the class: the plugin
		# tests `isinstance(buffer, DisplayContainer)` to find its own container.
		DisplayContainer.__init__ = failOnce
		self.addCleanup(setattr, DisplayContainer, "__init__", real)
		return built

	def test_theFallbackForACompositeIsOneSegmentPerDisplay(self):
		self.failTheFirstContainer()
		self.plugin.rebuildBuffer()
		self.assertEqual(self.plugin.currentView.name, "devices.fallback")
		self.assertEqual(self.container.numSegments, 2)

	def test_theFallbackObeysTheHardware(self):
		"""What the old one did not: a single segment over a composite covers dead columns.

		Cells 32 to 79 of the Monarch's rows reach nothing, so an emergency arrangement across
		the whole display starts losing text — silently, and at the worst possible moment.
		"""
		self.failTheFirstContainer()
		self.plugin.rebuildBuffer()
		validateAgainstHardware(self.plugin.currentView, deviceMap(), 80)
		for rect in self.container.rects:
			if rect.row < 8:
				self.assertLessEqual(rect.endCol, 32, f"{rect} runs past the Monarch")

	def test_theFallbackReadsNoSettings(self):
		"""A setting is the likeliest reason the other view could not be built."""
		setBandConfig("hidBrailleStandard_8x32", segmentCount=4)
		self.failTheFirstContainer()
		self.plugin.rebuildBuffer()
		self.assertEqual(self.container.numSegments, 2)

	def test_aPinOnADisplaySurvivesTheFallback(self):
		"""Its keys are the ones a display's first segment has either way.

		The Monarch's first segment, since the fallback leaves the focus on the last segment
		and a pin there is released whatever else happens.
		"""
		self.pin(0)
		self.failTheFirstContainer()
		self.plugin.rebuildBuffer()
		self.assertIn("device.hidBrailleStandard.0", self.plugin.monitoredKeys)

	def test_aMessageDoesNotChangeTheOtherDisplaysCells(self):
		"""Which is what stops the other display being written to at all.

		`DeviceSlot.write` compares each member's cells against what it last sent and skips the
		member if they match, so a message in the Focus's segment costs the Monarch nothing —
		neither its content nor the time it takes to redraw it.
		"""
		for segment in self.container.segments:
			segment.append(Region("some content"))
		self.container.update()
		before = list(self.container.windowBrailleCells)
		buffer = self.handler.messageBuffer
		buffer.clear()
		region = Region("a message")
		region.update()
		buffer.regions.append(region)
		buffer.update()
		self.handler.buffer = buffer
		monarch = slice(0, 8 * 80)
		self.assertEqual(list(buffer.windowBrailleCells)[monarch], before[monarch])

	def test_arrangingACompositeCarriesOverAnOldReversalSetting(self):
		"""Where the carry over is hooked in: the one place both keys are known at once.

		Asserted against the configuration section rather than through
		`bmConfig.shouldReverseScrollButtons`, which the rest of the suite reads through a
		stand-in. Which section the value is in is the whole question here.
		"""
		from brlMultiline import bmConfig

		composite = bmConfig.getDisplayConfig("brlMultilineVirtual_9x80")
		composite["reverseScrollBtns"] = True
		# As it stands the first time this version runs: setUp has already built the display.
		composite["reverseScrollBtnsMigrated"] = False
		self.plugin.rebuildBuffer()
		self.assertTrue(bmConfig.getDisplayConfig("hidBrailleStandard_8x32")["reverseScrollBtns"])
		self.assertTrue(bmConfig.getDisplayConfig("freedomScientific_1x80")["reverseScrollBtns"])

	def _configureBothDisplays(self) -> None:
		"""Store the member list this class's composite was opened for, and clear the log."""
		from brailleDisplayDrivers.brlMultilineVirtual import vdConfig
		from brailleDisplayDrivers.brlMultilineVirtual.virtualLayout import DeviceSpec

		vdConfig.setDevices(
			[DeviceSpec("hidBrailleStandard"), DeviceSpec("freedomScientific")],
		)
		log.messages.clear()

	def test_aProfileListingDifferentDisplaysIsReported(self):
		"""The list is not applied until the composite is reopened, so say so rather than not.

		A profile can hold a member list of its own, and NVDA does not reinitialise a display
		whose driver name has not changed. The configuration and the hardware then disagree
		with nothing to announce it.
		"""
		from brailleDisplayDrivers.brlMultilineVirtual import vdConfig
		from brailleDisplayDrivers.brlMultilineVirtual.virtualLayout import DeviceSpec

		vdConfig.setDevices([DeviceSpec("freedomScientific")])
		post_configProfileSwitch.notify()
		self.assertTrue(
			any(level == "warning" and "was opened for" in message for level, message in log.messages),
			log.messages,
		)

	def test_aDisplayThatIsMerelyAwayIsNotReported(self):
		"""The complaint is about the list, and a display being switched off is not about the list.

		The composite drops a member that has gone and goes on with the rest, so the members it
		is driving are not the members it was opened for. Comparing against those reported every
		disconnection as a configuration problem — on every profile switch, for as long as the
		display stayed away, which on a machine with two profiles is a log full of it.
		"""
		self._configureBothDisplays()
		self.handler.display.slots[0].failed = True
		post_configProfileSwitch.notify()
		self.assertFalse(
			[message for level, message in log.messages if level == "warning"],
			log.messages,
		)

	def test_aDisplayNeverOpenedAtAllIsNotReported(self):
		"""The same, for a display that was already switched off when braille started.

		It has no slot rather than a failed one, so nothing below the driver can tell it from a
		display that was never configured. The composite's own record of what it was opened for
		is the only thing that can.
		"""
		self._configureBothDisplays()
		self.handler.display = fakeVirtualDisplay(
			("freedomScientific", 0, 1, 80),
			configured=("hidBrailleStandard", "freedomScientific"),
		)
		post_configProfileSwitch.notify()
		self.assertFalse(
			[message for level, message in log.messages if level == "warning"],
			log.messages,
		)

	def test_theSameDivergenceIsReportedOnce(self):
		"""A profile switch is a cheap moment to look, which makes it a cheap moment to repeat."""
		from brailleDisplayDrivers.brlMultilineVirtual import vdConfig
		from brailleDisplayDrivers.brlMultilineVirtual.virtualLayout import DeviceSpec

		vdConfig.setDevices([DeviceSpec("freedomScientific")])
		post_configProfileSwitch.notify()
		log.messages.clear()
		post_configProfileSwitch.notify()
		post_configProfileSwitch.notify()
		self.assertFalse(
			[message for level, message in log.messages if level == "warning"],
			log.messages,
		)

	def test_aDivergenceIsReportedAgainOnceItHasChanged(self):
		"""Said once is not said and forgotten: a different mismatch is a different thing to say."""
		from brailleDisplayDrivers.brlMultilineVirtual import vdConfig
		from brailleDisplayDrivers.brlMultilineVirtual.virtualLayout import DeviceSpec

		vdConfig.setDevices([DeviceSpec("freedomScientific")])
		post_configProfileSwitch.notify()
		log.messages.clear()
		vdConfig.setDevices([DeviceSpec("hidBrailleStandard")])
		post_configProfileSwitch.notify()
		self.assertTrue(
			any(level == "warning" and "was opened for" in message for level, message in log.messages),
			log.messages,
		)

	def test_aProfileListingTheSameDisplaysIsQuiet(self):
		from brailleDisplayDrivers.brlMultilineVirtual import vdConfig
		from brailleDisplayDrivers.brlMultilineVirtual.virtualLayout import DeviceSpec

		vdConfig.setDevices(
			[DeviceSpec("hidBrailleStandard"), DeviceSpec("freedomScientific")],
		)
		log.messages.clear()
		post_configProfileSwitch.notify()
		self.assertFalse([message for level, message in log.messages if level == "warning"])

	def test_anOrdinaryDisplayIsUnaffected(self):
		"""The device view is reached only through the driver, never by an ordinary display."""
		self.handler.display = types.SimpleNamespace(name="freedomScientific")
		self.handler.displayDimensions.numRows = MONARCH_ROWS
		self.handler.displayDimensions.numCols = MONARCH_COLS
		self.plugin.rebuildBuffer()
		self.assertEqual(self.plugin.currentView.name, "configured")


if __name__ == "__main__":
	unittest.main()
