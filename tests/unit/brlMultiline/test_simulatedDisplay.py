# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the simulated member of the combined display, and simulated mode.

See docs/design/braille-viewer-plan.md, The simulated display.
"""

import unittest

from ._monarchStubs import installMonarchStubs
from ._virtualStubs import loadDriver, makeMemberDriver, resetStubs
from ._stubs import FakeBrailleHandler, decide_executeGesture, displaySizeChanged

installMonarchStubs()

import braille  # noqa: E402

from brlMultilineVirtual import ackPatch, handover, simulated, vdConfig  # noqa: E402
from brlMultilineVirtual.virtualLayout import SIMULATED_DRIVER_NAMES, DeviceSpec  # noqa: E402

driverModule = loadDriver()
VirtualDisplay = driverModule.BrailleDisplayDriver

# NVDA's gesture base computes this, and the combined display freezes it before rebasing; the stub
# base has none.
simulated.SimulatedGesture._cellIndexesStr = None

FIRST, SECOND = SIMULATED_DRIVER_NAMES
FOCUS = "freedomScientific"


class SimulatedTestCase(unittest.TestCase):
	def setUp(self):
		resetStubs()
		self._realDelay = driverModule.OPEN_RETRY_DELAY
		driverModule.OPEN_RETRY_DELAY = 0
		makeMemberDriver(FOCUS, numRows=1, numCols=80)
		self.handler = FakeBrailleHandler()
		braille.handler = self.handler
		self.sizes = []
		displaySizeChanged.register(self.noteSize)

	def tearDown(self):
		displaySizeChanged.unregister(self.noteSize)
		driverModule.OPEN_RETRY_DELAY = self._realDelay
		handover.removeSwitchPatch()
		ackPatch.remove()
		driverModule._retiredDrivers.clear()
		braille.handler = None

	def noteSize(self, **kwargs):
		self.sizes.append((kwargs.get("numRows"), kwargs.get("numCols")))

	def build(self, *driverNames):
		vdConfig.setDevices([DeviceSpec(name) for name in driverNames])
		display = VirtualDisplay()
		self.handler.display = display
		display.initSettings()
		return display

	def member(self, display, name=FIRST) -> simulated.SimulatedDisplay:
		slot = display.slotForDriverName(name)
		self.assertIsNotNone(slot)
		return slot.driver


class TestSimulatedMode(SimulatedTestCase):
	def test_aSimulatedMemberStandsBesideARealOne(self):
		display = self.build(SECOND, FOCUS)
		self.assertFalse(display.simulatedMode)
		self.assertEqual([slot.driverName for slot in display.slots], [SECOND, FOCUS])
		# The second simulated display imitates a Focus 80 until told otherwise.
		self.assertEqual((display.numRows, display.numCols), (2, 80))

	def test_twoSimulatedMembersStack(self):
		display = self.build(FIRST, SECOND)
		self.assertEqual((display.numRows, display.numCols), (9, 80))
		self.assertEqual(display.slots[1].band.rowStart, 8)

	def test_theMemberKeepsWhatItIsShown(self):
		display = self.build(FIRST)
		display.display(list(range(8 * 32)))
		self.assertEqual(self.member(display).cells, list(range(8 * 32)))


class TestShape(SimulatedTestCase):
	def test_changingTheModelLaysTheDisplayOutAgain(self):
		display = self.build(FIRST)
		self.member(display).model = simulated.FOCUS_80.id
		self.assertEqual((display.numRows, display.numCols), (1, 80))
		self.assertEqual(display.slots[0].band.numCols, 80)
		self.assertIn((1, 80), self.sizes)

	def test_aCustomSizeAppliesOnlyToTheCustomModel(self):
		display = self.build(FIRST)
		member = self.member(display)
		member.customRows = 3
		self.assertEqual((display.numRows, display.numCols), (8, 32))
		member.customCols = 20
		member.model = simulated.CUSTOM
		self.assertEqual((display.numRows, display.numCols), (3, 20))

	def test_anUnknownModelIsRefused(self):
		display = self.build(FIRST)
		self.member(display).model = "noSuchModel"
		self.assertEqual(self.member(display).model, simulated.MONARCH_8.id)

	def test_theSettingsReachTheCombinedDisplay(self):
		display = self.build(FIRST)
		ids = [setting.id for setting in display.supportedSettings]
		self.assertIn(f"{FIRST}_model", ids)
		self.assertIn(f"{FIRST}_customRows", ids)
		setattr(display, f"{FIRST}_model", simulated.MONARCH_10.id)
		self.assertEqual((display.numRows, display.numCols), (10, 32))


class TestDrawing(SimulatedTestCase):
	def test_onlyAMonarchCanDraw(self):
		display = self.build(FIRST)
		member = self.member(display)
		self.assertEqual(member.graphicsSize, (96, 40))
		member.model = simulated.FOCUS_80.id
		for name in ("graphicsSize", "newGraphicsBuffer", "setGraphicsOverlay", "clearGraphicsOverlay"):
			with self.subTest(attribute=name):
				self.assertFalse(hasattr(member, name))

	def test_overlaysAreKeptAndCleared(self):
		member = self.member(self.build(FIRST))
		buffer = member.newGraphicsBuffer(10, 4)
		self.assertEqual((buffer.width, buffer.height), (10, 4))
		member.setGraphicsOverlay("chart", 3, 5, buffer)
		member.setGraphicsOverlay("label", 0, 0, buffer)
		self.assertEqual(set(member.overlays), {"chart", "label"})
		member.clearGraphicsOverlay("chart")
		self.assertEqual(set(member.overlays), {"label"})
		member.clearGraphicsOverlay()
		self.assertEqual(member.overlays, {})

	def test_touchKeepsTheRoutingPinAfterRelease(self):
		member = self.member(self.build(FIRST))
		member.touch(12, 7)
		member.release()
		self.assertIsNone(member.lastTouch)
		self.assertEqual(member.lastRoutingPin, (12, 7))


class TestKeys(SimulatedTestCase):
	def press(self, member, keyId, cellIndex=None):
		""":return: what reached NVDA, after the combined display's own decider had its turn."""
		seen = []

		def stop(gesture=None, **kwargs):
			seen.append((gesture.source, gesture.id, getattr(gesture, "cellIndexes", None)))
			return False

		decide_executeGesture.register(stop)
		try:
			member.pressKey(keyId, cellIndex)
		finally:
			decide_executeGesture.unregister(stop)
		return seen

	def test_aRoutingPressIsRebasedOntoTheCombinedDisplay(self):
		display = self.build(FIRST, SECOND)
		seen = self.press(self.member(display, SECOND), "routing", 5)
		self.assertEqual(seen, [(SECOND, "routing", [8 * 80 + 5])])

	def test_aKeyIsRaisedAsTheMembersOwn(self):
		display = self.build(FIRST)
		self.assertEqual(self.press(self.member(display), "zoomIn"), [(FIRST, "zoomIn", None)])


if __name__ == "__main__":
	unittest.main()
