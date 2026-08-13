# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the virtual display driver's orchestration.

Not for whether a Focus answers a query packet in time — no stub can tell you that, and the
hardware runs are what settle it. What is tested here is everything around that: which
members get opened, what happens to the ones that do not, who is holding a display during a
switch, and that nothing is left open when construction fails.

The switch tests are the point of the exercise. `_stubs.FakeBrailleHandler._switchDisplay`
reproduces NVDA's ordering, in which the incoming driver is constructed before the outgoing
one is closed, so a member the user is switching away from is genuinely still held when the
virtual display tries to open it. That is the situation `handover` exists for, and it is
reproduced here rather than described.
"""

import unittest

import braille

from ._virtualStubs import (
	GlobalGestureMap,
	StubBrailleDisplayGesture,
	bgThread,
	decide_executeGesture,
	driverRegistry,
	globalMapScripts,
	installVirtualStubs,
	loadDriver,
	log,
	makeMemberDriver,
	resetStubs,
)
from ._stubs import FakeBrailleHandler

installVirtualStubs()

from brlMultilineVirtual import ackPatch, handover, vdConfig  # noqa: E402
from brlMultilineVirtual.virtualLayout import DeviceSpec  # noqa: E402

driverModule = loadDriver()
VirtualDisplay = driverModule.BrailleDisplayDriver

MONARCH = "hidBrailleStandard"
FOCUS = "freedomScientific"


class VirtualDriverTestCase(unittest.TestCase):
	def setUp(self):
		resetStubs()
		# Retrying three times at 0.3 seconds would put a second on every failure test.
		self._realDelay = driverModule.OPEN_RETRY_DELAY
		driverModule.OPEN_RETRY_DELAY = 0
		self.monarch = makeMemberDriver(MONARCH, numRows=8, numCols=32)
		self.focus = makeMemberDriver(FOCUS, numRows=1, numCols=80)
		self.handler = FakeBrailleHandler()
		braille.handler = self.handler

	def tearDown(self):
		driverModule.OPEN_RETRY_DELAY = self._realDelay
		handover.removeSwitchPatch()
		ackPatch.remove()
		driverModule._retiredDrivers.clear()
		braille.handler = None

	def configure(self, *driverNames):
		vdConfig.setDevices([DeviceSpec(name) for name in driverNames])

	def build(self, *driverNames):
		"""Construct a virtual display and install it, as `_setDisplay` does.

		The assignment matters: NVDA makes it only after the constructor has returned, and
		several things this driver does are deliberately inert until it has happened.
		"""
		self.configure(*driverNames)
		display = VirtualDisplay()
		self.handler.display = display
		return display


class TestConstruction(VirtualDriverTestCase):
	def test_membersStackTopFirst(self):
		display = self.build(MONARCH, FOCUS)
		self.assertEqual((display.numRows, display.numCols), (9, 80))
		self.assertEqual([slot.driverName for slot in display.slots], [MONARCH, FOCUS])
		self.assertEqual(display.slots[1].band.rowStart, 8)

	def test_noDevicesConfiguredIsRefused(self):
		with self.assertRaises(RuntimeError) as caught:
			VirtualDisplay()
		self.assertIn("no displays configured", str(caught.exception))

	def test_aMemberThatWillNotOpenIsDropped(self):
		self.focus.failedOpens = -1
		display = self.build(MONARCH, FOCUS)
		self.assertEqual([slot.driverName for slot in display.slots], [MONARCH])
		self.assertEqual((display.numRows, display.numCols), (8, 32))

	def test_noMemberOpeningIsRefused(self):
		self.monarch.failedOpens = -1
		self.focus.failedOpens = -1
		self.configure(MONARCH, FOCUS)
		with self.assertRaises(RuntimeError):
			VirtualDisplay()

	def test_anUnknownDriverIsDropped(self):
		self.configure(MONARCH, "noSuchDriver")
		display = VirtualDisplay()
		self.assertEqual([slot.driverName for slot in display.slots], [MONARCH])

	def test_openingIsRetried(self):
		"""From Phase 0: a display just released may refuse once and then open normally."""
		self.focus.failedOpens = 2
		display = self.build(MONARCH, FOCUS)
		self.assertEqual([slot.driverName for slot in display.slots], [MONARCH, FOCUS])
		self.assertEqual(self.focus.openAttempts, 3)

	def test_retriesAreBounded(self):
		self.focus.failedOpens = -1
		self.build(MONARCH, FOCUS)
		self.assertEqual(self.focus.openAttempts, driverModule.OPEN_ATTEMPTS)


class TestConstructionCleanup(VirtualDriverTestCase):
	"""Nothing opened may be left open when construction does not complete."""

	def test_aZeroCellMemberIsClosedRatherThanKept(self):
		# What `noBraille` looks like, and what a display that connected but failed to
		# identify itself looks like.
		empty = makeMemberDriver("emptyDisplay", numRows=1, numCols=0)
		display = self.build(MONARCH, "emptyDisplay")
		self.assertEqual([slot.driverName for slot in display.slots], [MONARCH])
		self.assertEqual(len(empty.instances), 1)
		self.assertEqual(empty.instances[0].terminated, 1)

	def test_openedMembersAreClosedWhenTheLayoutFails(self):
		self.configure(MONARCH, FOCUS)

		def explode(shapes):
			raise ValueError("geometry is unwell")

		original = driverModule.stackDevices
		driverModule.stackDevices = explode
		try:
			with self.assertRaises(ValueError):
				VirtualDisplay()
		finally:
			driverModule.stackDevices = original
		self.assertEqual(self.monarch.instances[0].terminated, 1)
		self.assertEqual(self.focus.instances[0].terminated, 1)

	def test_aConstructorThatRaisesStillHasItsDeviceReleased(self):
		"""Both target drivers open the device before their constructor can fail."""
		self.focus.failedOpens = -1
		self.build(MONARCH, FOCUS)
		self.assertEqual(len(self.focus.attempted), driverModule.OPEN_ATTEMPTS)
		for instance in self.focus.attempted:
			self.assertEqual(instance.terminated, 1)

	def test_aFailingInitSettingsStillHasItsDeviceReleased(self):
		"""`initSettings` runs after the device is open in every case."""
		self.focus.failInitSettings = True
		self.build(MONARCH, FOCUS)
		self.assertEqual(len(self.focus.attempted), driverModule.OPEN_ATTEMPTS)
		for instance in self.focus.attempted:
			self.assertEqual(instance.terminated, 1)

	def test_aMemberThatOpensIsNotReleased(self):
		display = self.build(MONARCH, FOCUS)
		self.assertEqual(self.focus.instances[0].terminated, 0)
		display.terminate()

	def test_aRetriedMemberReleasesOnlyItsFailedAttempts(self):
		self.focus.failedOpens = 1
		display = self.build(MONARCH, FOCUS)
		self.assertEqual([instance.terminated for instance in self.focus.attempted], [1, 0])
		display.terminate()

	def test_releasingAFailedAttemptIsNotReportedAsAnError(self):
		"""A display that is simply not plugged in takes this path on every attempt.

		Three failed attempts must not mean three logged errors. The one error worth having
		is that the member could not be opened at all.
		"""
		self.focus.failedOpens = -1
		self.focus.failOnTerminate = True
		self.build(MONARCH, FOCUS)
		errors = [message for level, message in log.messages if level == "error"]
		self.assertEqual(len(errors), 1)
		self.assertIn("did not open", errors[0])

	def test_patchesAreRemovedWhenConstructionFails(self):
		self.monarch.failedOpens = -1
		self.configure(MONARCH)
		with self.assertRaises(RuntimeError):
			VirtualDisplay()
		self.assertIsNone(ackPatch._originalHandleAck)
		self.assertIsNone(handover._originalSwitchDisplay)


class TestConfigurationRefusals(VirtualDriverTestCase):
	def test_theVirtualDriverCannotBeItsOwnMember(self):
		with self.assertRaises(ValueError):
			vdConfig.setDevices([DeviceSpec("brlMultilineVirtual")])

	def test_aStoredSelfReferenceIsRefusedAtConstruction(self):
		"""Written straight into configuration, bypassing `setDevices`."""
		import config

		config.conf["BrlMultilineVirtualDisplay"]["devices"] = ["brlMultilineVirtual"]
		with self.assertRaises(RuntimeError) as caught:
			VirtualDisplay()
		self.assertIn("misconfigured", str(caught.exception))

	def test_duplicateDriversAreRefusedAtConstruction(self):
		import config

		config.conf["BrlMultilineVirtualDisplay"]["devices"] = [FOCUS, f"{FOCUS}|COM3"]
		with self.assertRaises(RuntimeError):
			VirtualDisplay()


class TestDisplayFanOut(VirtualDriverTestCase):
	def setUp(self):
		super().setUp()
		self.display = self.build(MONARCH, FOCUS)
		self.monarchDevice = self.monarch.instances[0]
		self.focusDevice = self.focus.instances[0]
		self.monarchDevice.written.clear()
		self.focusDevice.written.clear()

	def composite(self, monarchFill, focusFill):
		"""A 9 by 80 array with one value across the Monarch's band and another on the Focus."""
		cells = [monarchFill] * (8 * 80) + [focusFill] * 80
		return cells

	def write(self, cells):
		"""Display, then let the queued writes run.

		Both members are thread safe, so their slots queue onto the background thread rather
		than writing inline. The stub thread holds those calls until they are flushed.
		"""
		self.display.display(cells)
		bgThread.flush()

	def test_eachMemberGetsItsOwnBand(self):
		self.write(self.composite(1, 2))
		self.assertEqual(self.monarchDevice.written, [[1] * 256])
		self.assertEqual(self.focusDevice.written, [[2] * 80])

	def test_anUnchangedMemberIsNotWrittenTo(self):
		"""The reason a second display is usable at all: NVDA rewrites on every cursor blink."""
		self.write(self.composite(1, 2))
		self.write(self.composite(1, 3))
		self.assertEqual(len(self.monarchDevice.written), 1)
		self.assertEqual(len(self.focusDevice.written), 2)

	def test_aShortArrayIsPadded(self):
		self.write([1] * 10)
		self.assertEqual(len(self.monarchDevice.written[0]), 256)
		self.assertEqual(self.focusDevice.written, [[0] * 80])

	def test_aLongArrayIsTruncated(self):
		self.write([1] * 5000)
		self.assertEqual(self.monarchDevice.written, [[1] * 256])
		self.assertEqual(self.focusDevice.written, [[1] * 80])

	def test_aFailedMemberIsSkippedButTheOtherIsNot(self):
		self.display.slots[0].fail()
		self.write(self.composite(1, 2))
		self.assertEqual(self.monarchDevice.written, [])
		self.assertEqual(self.focusDevice.written, [[2] * 80])

	def test_aNonThreadSafeMemberIsWrittenInline(self):
		"""Which, now the composite reports itself unsafe, means on the main thread."""
		resetStubs()
		makeMemberDriver(MONARCH, numRows=8, numCols=32, isThreadSafe=False)
		display = self.build(MONARCH)
		device = driverRegistry[MONARCH].instances[0]
		device.written.clear()
		display.display([7] * display.numCells)
		self.assertEqual(bgThread.queued, [])
		self.assertEqual(device.written, [[7] * 256])


class TestTerminate(VirtualDriverTestCase):
	def test_membersAreClosed(self):
		display = self.build(MONARCH, FOCUS)
		display.terminate()
		self.assertEqual(self.monarch.instances[0].terminated, 1)
		self.assertEqual(self.focus.instances[0].terminated, 1)
		self.assertEqual(display.slots, ())

	def test_membersAreBlankedFirst(self):
		display = self.build(MONARCH, FOCUS)
		device = self.monarch.instances[0]
		device.written.clear()
		display.terminate()
		self.assertEqual(device.written, [[0] * 256])

	def test_suppressedClearReachesTheMembers(self):
		display = self.build(MONARCH, FOCUS)
		device = self.monarch.instances[0]
		display.display([1] * display.numCells)
		device.written.clear()
		display._suppressDisplayClear = True
		display.terminate()
		self.assertEqual(device.written, [])

	def test_patchesAreRemoved(self):
		display = self.build(MONARCH)
		display.terminate()
		self.assertIsNone(ackPatch._originalHandleAck)
		self.assertIsNone(handover._originalSwitchDisplay)

	def test_aMemberRaisingDoesNotStopTheOthersClosing(self):
		self.monarch.failOnTerminate = True
		display = self.build(MONARCH, FOCUS)
		display.terminate()
		self.assertEqual(self.focus.instances[0].terminated, 1)


class TestHandover(VirtualDriverTestCase):
	"""NVDA constructs the incoming driver before terminating the outgoing one."""

	def test_switchingFromAMemberAdoptsItRatherThanReopeningIt(self):
		"""Reopening a display NVDA has just closed is what breaks on real hardware."""
		self.handler.setDisplay(self.focus)
		focusDevice = self.focus.instances[0]
		self.configure(MONARCH, FOCUS)

		self.handler.setDisplay(VirtualDisplay)

		display = self.handler.display
		self.assertEqual([slot.driverName for slot in display.slots], [MONARCH, FOCUS])
		# The very instance NVDA had, still open, now a member.
		self.assertIs(display.slots[1].driver, focusDevice)
		self.assertEqual(len(self.focus.instances), 1)
		self.assertEqual(focusDevice.terminated, 0)

	def test_theAdoptedDisplaySurvivesNVDAsTerminate(self):
		"""NVDA terminates the outgoing display once, after the switch completes."""
		self.handler.setDisplay(self.focus)
		focusDevice = self.focus.instances[0]
		self.configure(FOCUS)
		self.handler.setDisplay(VirtualDisplay)
		self.assertEqual(focusDevice.terminated, 0)

	def test_anAdoptedDisplayIsStillClosableAfterwards(self):
		"""The one-shot must uncover the real method again, or the device never closes."""
		self.handler.setDisplay(self.focus)
		focusDevice = self.focus.instances[0]
		self.configure(FOCUS)
		self.handler.setDisplay(VirtualDisplay)
		self.handler.display.terminate()
		self.assertEqual(focusDevice.terminated, 1)

	def test_adoptionIsHandedBackWhenConstructionFails(self):
		"""Otherwise the one-shot swallows NVDA's terminate and the device is orphaned."""
		self.handler.setDisplay(self.focus)
		focusDevice = self.focus.instances[0]
		import config

		config.conf["BrlMultilineVirtualDisplay"]["devices"] = [FOCUS, FOCUS]
		with self.assertRaises(RuntimeError):
			self.handler.setDisplay(VirtualDisplay)
		focusDevice.terminate()
		self.assertEqual(focusDevice.terminated, 1)

	def test_aClosedMemberIsKeptAliveRatherThanFinalised(self):
		"""NVDA's hwIo closes an already closed handle when the driver is finalised."""
		display = self.build(MONARCH)
		device = self.monarch.instances[0]
		display.terminate()
		self.assertIn(device, driverModule._retiredDrivers)

	def test_aDisplayThatIsNotAMemberIsLeftToNVDA(self):
		other = makeMemberDriver("someOtherDisplay")
		self.handler.setDisplay(other)
		self.configure(MONARCH)
		self.handler.setDisplay(VirtualDisplay)
		# Closed once, by NVDA, after the virtual display was constructed.
		self.assertEqual(other.instances[0].terminated, 1)
		self.assertEqual(len(other.instances), 1)

	def test_switchingBackToAMemberReleasesTheVirtualDisplayFirst(self):
		self.configure(MONARCH, FOCUS)
		self.handler.setDisplay(VirtualDisplay)
		heldFocus = self.focus.instances[0]

		self.handler.setDisplay(self.focus)

		# The virtual display let go before the Focus driver was constructed, so the new
		# Focus is the second instance and the one the virtual display held is closed.
		self.assertEqual(heldFocus.terminated, 1)
		self.assertEqual(len(self.focus.instances), 2)
		self.assertIs(self.handler.display, self.focus.instances[1])

	def test_theSwitchPatchIsRemovedOnTheWayOut(self):
		self.configure(MONARCH)
		self.handler.setDisplay(VirtualDisplay)
		self.handler.setDisplay(self.focus)
		self.assertIsNone(handover._originalSwitchDisplay)

	def test_anUnrelatedSwitchIsUnaffected(self):
		other = makeMemberDriver("someOtherDisplay")
		self.handler.setDisplay(self.monarch)
		self.handler.setDisplay(other)
		self.assertIs(self.handler.display, other.instances[0])
		self.assertEqual(self.monarch.instances[0].terminated, 1)


class AckPatchTestCase(VirtualDriverTestCase):
	def setUp(self):
		super().setUp()
		self.acking = makeMemberDriver("ackingDisplay", receivesAckPackets=True)


class TestAckPatch(AckPatchTestCase):

	def test_theDisplayNVDAOwnsKeepsItsOwnBehaviour(self):
		ackPatch.install()
		self.handler.setDisplay(self.acking)
		device = self.handler.display
		device._handleAck()
		self.assertEqual(device.handlerAcksReceived, 1)

	def test_aMemberDoesNotReachTheHandler(self):
		self.configure("ackingDisplay")
		display = VirtualDisplay()
		self.handler.display = display
		member = self.acking.instances[0]
		member._awaitingAck = True
		member._handleAck()
		self.assertFalse(member._awaitingAck)
		self.assertFalse(hasattr(member, "handlerAcksReceived"))

	def test_aDriverWithoutAcknowledgementsStillRefuses(self):
		self.configure(MONARCH)
		VirtualDisplay()
		with self.assertRaises(NotImplementedError):
			self.monarch.instances[0]._handleAck()

	def test_removeRestoresNVDAsOwnMethod(self):
		from brlMultilineVirtual.ackPatch import BrailleDisplayDriver

		original = BrailleDisplayDriver._handleAck
		ackPatch.install()
		self.assertIsNot(BrailleDisplayDriver._handleAck, original)
		ackPatch.remove()
		self.assertIs(BrailleDisplayDriver._handleAck, original)


class TestPatchCoexistence(AckPatchTestCase):
	"""Standing down must not discard a patch another add-on installed later."""

	def test_theAckPatchDoesNotDiscardALaterWrapper(self):
		from brlMultilineVirtual.ackPatch import BrailleDisplayDriver

		ackPatch.install()
		ours = BrailleDisplayDriver._handleAck
		calls = []

		def theirs(self):
			calls.append(self)
			return ours(self)

		BrailleDisplayDriver._handleAck = theirs
		try:
			ackPatch.remove()
			self.assertIs(BrailleDisplayDriver._handleAck, theirs)
			# Still in the chain, but no longer acting: an ack now reaches NVDA's own method.
			self.handler.setDisplay(self.acking)
			self.handler.display._handleAck()
			self.assertEqual(len(calls), 1)
			self.assertEqual(self.handler.display.handlerAcksReceived, 1)
		finally:
			BrailleDisplayDriver._handleAck = ours
			ackPatch.remove()

	def test_theSwitchPatchDoesNotDiscardALaterWrapper(self):
		from braille.brailleHandler import BrailleHandler

		handover.installSwitchPatch()
		ours = BrailleHandler._switchDisplay

		def theirs(self, oldDisplay, newDisplayClass, **kwargs):
			return ours(self, oldDisplay, newDisplayClass, **kwargs)

		BrailleHandler._switchDisplay = theirs
		try:
			handover.removeSwitchPatch()
			self.assertIs(BrailleHandler._switchDisplay, theirs)
		finally:
			BrailleHandler._switchDisplay = ours
			handover.removeSwitchPatch()

	def test_anInertSwitchPatchStillSwitchesCorrectly(self):
		"""Left in the chain but stood down, it must be a clean pass through."""
		self.configure(MONARCH, FOCUS)
		self.handler.setDisplay(VirtualDisplay)
		virtualDisplay = self.handler.display
		# Stand the patch down while a virtual display is live, which is the state a later
		# add-on discarding our restore would leave it in.
		handover._active = False

		self.handler.setDisplay(self.focus)

		self.assertIs(self.handler.display, self.focus.instances[-1])
		self.assertEqual(virtualDisplay.slots, ())

	def test_reinstallingReactivates(self):
		handover.installSwitchPatch()
		handover.removeSwitchPatch()
		handover.installSwitchPatch()
		self.assertTrue(handover._active)
		handover.removeSwitchPatch()


class TestVdConfig(VirtualDriverTestCase):
	def test_roundTrip(self):
		specs = [DeviceSpec(MONARCH), DeviceSpec(FOCUS, "COM3")]
		vdConfig.setDevices(specs)
		self.assertEqual(vdConfig.getDevices(), specs)

	def test_defaultIsEmpty(self):
		self.assertEqual(vdConfig.getDevices(), [])

	def test_aPortlessEntryIsStoredWithoutOne(self):
		import config

		vdConfig.setDevices([DeviceSpec(MONARCH)])
		self.assertEqual(config.conf["BrlMultilineVirtualDisplay"]["devices"], [MONARCH])

	def test_settingRefusesWhatLoadingWouldRefuse(self):
		with self.assertRaises(ValueError):
			vdConfig.setDevices([DeviceSpec(FOCUS), DeviceSpec(FOCUS, "COM3")])


class GestureTestCase(VirtualDriverTestCase):
	def setUp(self):
		super().setUp()
		self.display = self.build(MONARCH, FOCUS)
		self.monarchBand = self.display.slots[0].band
		self.focusBand = self.display.slots[1].band

	def press(self, source, cellIndexes=None, gestureId="routing"):
		"""Send a gesture the way a member driver does, through the extension point."""
		gesture = StubBrailleDisplayGesture(source, gestureId, cellIndexes)
		self.assertTrue(decide_executeGesture.decide(gesture=gesture))
		return gesture


class TestCellIndexTranslation(GestureTestCase):
	"""Checked against the cell indexes a real Monarch reported during the Phase 0 spike."""

	def test_aMonarchRoutingKeyIsRebasedOntoTheComposite(self):
		# 102 is row 3, column 6 of an 8 by 32 display, so row 3 column 6 of a 9 by 80 one.
		gesture = self.press(MONARCH, [102])
		self.assertEqual(gesture.cellIndexes, [3 * 80 + 6])

	def test_aFocusRoutingKeyIsOffsetByItsBand(self):
		gesture = self.press(FOCUS, [5])
		self.assertEqual(gesture.cellIndexes, [8 * 80 + 5])

	def test_theIdentifierKeepsThePhysicalCellNumber(self):
		"""A routing key bound by number must not start matching a different cell."""
		gesture = self.press(MONARCH, [102])
		self.assertEqual(gesture._cellIndexesStr, "103")
		self.assertIn(f"br({MONARCH}):routing103", gesture.identifiers)

	def test_multipleCellsAreRebasedIndividually(self):
		"""`selectRange` takes the lowest and highest, so both ends have to move together."""
		gesture = self.press(MONARCH, [2, 39])
		self.assertEqual(gesture.cellIndexes, [2, 1 * 80 + 7])

	def test_aGestureWithNoCellsIsUntouched(self):
		gesture = self.press(MONARCH, None, gestureId="panRight")
		self.assertIsNone(gesture.cellIndexes)

	def test_aGestureFromSomeOtherDisplayIsUntouched(self):
		gesture = self.press("someOtherDisplay", [5])
		self.assertEqual(gesture.cellIndexes, [5])

	def test_anImpossibleCellCancelsTheGesture(self):
		"""An out of range index on a narrow member is a valid index into the composite.

		Left alone it would route confidently to the other display's cell, so the press is
		refused rather than acted on somewhere the user did not touch.
		"""
		gesture = StubBrailleDisplayGesture(MONARCH, "routing", [999])
		self.assertFalse(decide_executeGesture.decide(gesture=gesture))

	def test_theCancelledGestureIsNotRebased(self):
		gesture = StubBrailleDisplayGesture(MONARCH, "routing", [999])
		decide_executeGesture.decide(gesture=gesture)
		self.assertEqual(gesture.cellIndexes, [999])

	def test_nothingIsTranslatedBeforeNVDAInstallsTheDisplay(self):
		"""Mid switch, the outgoing display is still NVDA's and still routes for itself.

		The window is real: `gestures.install` runs from the constructor, but
		`braille.handler.display` is assigned only after that constructor returns, the
		outgoing display is terminated and `initSettings` has run. An adopted Focus is
		dispatching input throughout.
		"""
		self.handler.display = self.focus.instances[0]
		gesture = StubBrailleDisplayGesture(FOCUS, "routing", [5])
		self.assertTrue(decide_executeGesture.decide(gesture=gesture))
		self.assertEqual(gesture.cellIndexes, [5])

	def test_anImpossibleCellIsNotCancelledBeforeInstallation(self):
		"""The outgoing display's own indexes are its business, whatever they are."""
		self.handler.display = self.focus.instances[0]
		gesture = StubBrailleDisplayGesture(FOCUS, "routing", [999])
		self.assertTrue(decide_executeGesture.decide(gesture=gesture))

	def test_translationStopsWhenTheDisplayIsTerminated(self):
		self.display.terminate()
		gesture = StubBrailleDisplayGesture(MONARCH, "routing", [102])
		decide_executeGesture.decide(gesture=gesture)
		self.assertEqual(gesture.cellIndexes, [102])

	def test_anOrdinaryGestureIsNeverCancelled(self):
		gesture = StubBrailleDisplayGesture(MONARCH, "panRight")
		self.assertTrue(decide_executeGesture.decide(gesture=gesture))


class TestDeciderOrdering(VirtualDriverTestCase):
	"""NVDA Remote registers on this same decider and returns False for braille gestures."""

	def cancelEverything(self, gesture=None, **kwargs):
		self.cancelled.append(gesture)
		return False

	def setUp(self):
		super().setUp()
		self.cancelled = []

	def test_translationRunsEvenWhenAnEarlierHandlerCancels(self):
		decide_executeGesture.register(self.cancelEverything)
		display = self.build(MONARCH, FOCUS)
		gesture = StubBrailleDisplayGesture(FOCUS, "routing", [5])

		self.assertFalse(decide_executeGesture.decide(gesture=gesture))

		# Cancelled by the other handler, but rebased first: 645, not 5.
		self.assertEqual(gesture.cellIndexes, [8 * 80 + 5])
		self.assertEqual(self.cancelled, [gesture])
		display.terminate()

	def test_theTranslatorIsPlacedFirst(self):
		decide_executeGesture.register(self.cancelEverything)
		display = self.build(MONARCH)
		# Compared by equality: a bound method is a fresh object on every attribute access.
		self.assertEqual(decide_executeGesture.handlers[-1], self.cancelEverything)
		self.assertEqual(len(decide_executeGesture.handlers), 2)
		display.terminate()


class TestGestureMap(GestureTestCase):
	def test_bothMembersAreConsulted(self):
		self.monarch.gestureMap = GlobalGestureMap()
		self.focus.gestureMap = GlobalGestureMap()
		self.monarch.gestureMap.addResolved(f"br({MONARCH}):panRight", str, "scrollForward")
		self.focus.gestureMap.addResolved(f"br({FOCUS}):topRouting1", int, "scrollBack")

		gestureMap = self.display.gestureMap
		self.assertEqual(
			list(gestureMap.getScriptsForGesture(f"br({MONARCH}):panRight")),
			[(str, "scrollForward")],
		)
		self.assertEqual(
			list(gestureMap.getScriptsForGesture(f"br({FOCUS}):topRouting1")),
			[(int, "scrollBack")],
		)

	def test_allGesturesSpansTheMembers(self):
		self.monarch.gestureMap = GlobalGestureMap()
		self.focus.gestureMap = GlobalGestureMap()
		self.monarch.gestureMap.addResolved(f"br({MONARCH}):panRight", str, "scrollForward")
		self.focus.gestureMap.addResolved(f"br({FOCUS}):topRouting1", int, "scrollBack")
		identifiers = {gesture for _cls, gesture, _name in self.display.gestureMap.getScriptsForAllGestures()}
		self.assertEqual(identifiers, {f"br({MONARCH}):panRight", f"br({FOCUS}):topRouting1"})

	def test_itFollowsAMemberRewritingItsOwnMap(self):
		"""The Focus does exactly this when its wiz wheel action is cycled."""
		self.focus.gestureMap = GlobalGestureMap()
		gestureMap = self.display.gestureMap
		self.assertEqual(list(gestureMap.getScriptsForGesture(f"br({FOCUS}):leftWizWheelUp")), [])
		self.focus.gestureMap.addResolved(f"br({FOCUS}):leftWizWheelUp", str, "moveByLine")
		self.assertEqual(
			list(gestureMap.getScriptsForGesture(f"br({FOCUS}):leftWizWheelUp")),
			[(str, "moveByLine")],
		)

	def test_aMemberWithoutAMapIsSkipped(self):
		self.monarch.gestureMap = None
		self.focus.gestureMap = GlobalGestureMap()
		self.focus.gestureMap.addResolved(f"br({FOCUS}):topRouting1", int, "scrollBack")
		self.assertEqual(
			list(self.display.gestureMap.getScriptsForGesture(f"br({FOCUS}):topRouting1")),
			[(int, "scrollBack")],
		)


class TestScriptDelegation(GestureTestCase):
	def test_aMembersOwnScriptIsFound(self):
		import baseObject

		found = []

		class ScriptableMember(baseObject.ScriptableObject):
			def getScript(self, gesture):
				found.append(gesture)
				return "the member's script"

		self.display.slots[1].driver = ScriptableMember()
		self.display.slots[1].driver.name = FOCUS
		gesture = StubBrailleDisplayGesture(FOCUS, "leftBumperBarUp")
		self.assertEqual(self.display.getScript(gesture), "the member's script")
		self.assertEqual(found, [gesture])

	def test_aMemberThatIsNotScriptableIsSkipped(self):
		gesture = StubBrailleDisplayGesture(MONARCH, "panRight")
		self.assertIsNone(self.display.getScript(gesture))

	def test_aGestureFromSomeOtherDisplayIsNotDelegated(self):
		gesture = StubBrailleDisplayGesture("someOtherDisplay", "someKey")
		self.assertIsNone(self.display.getScript(gesture))

	def test_aUserBindingToAMemberDriversScriptIsHonoured(self):
		"""NVDA tests `isinstance` against the virtual display, where it cannot match."""
		import baseObject

		class ScriptableMember(baseObject.ScriptableObject):
			name = FOCUS

			def script_toggleLeftWizWheelAction(self, gesture):
				return None

		member = ScriptableMember()
		self.display.slots[1].driver = member
		globalMapScripts.append((ScriptableMember, "toggleLeftWizWheelAction"))

		gesture = StubBrailleDisplayGesture(FOCUS, "leftWizWheelPress")
		self.assertEqual(self.display.getScript(gesture), member.script_toggleLeftWizWheelAction)

	def test_aUserUnbindingIsHonoured(self):
		import baseObject

		class ScriptableMember(baseObject.ScriptableObject):
			name = FOCUS

			def getScript(self, gesture):
				return "the built in script"

		self.display.slots[1].driver = ScriptableMember()
		globalMapScripts.append((ScriptableMember, None))
		gesture = StubBrailleDisplayGesture(FOCUS, "leftWizWheelPress")
		self.assertIsNone(self.display.getScript(gesture))

	def test_aBindingForSomeOtherClassIsIgnored(self):
		import baseObject

		class ScriptableMember(baseObject.ScriptableObject):
			name = FOCUS

			def getScript(self, gesture):
				return "the built in script"

		self.display.slots[1].driver = ScriptableMember()
		globalMapScripts.append((int, "somethingElse"))
		gesture = StubBrailleDisplayGesture(FOCUS, "leftWizWheelPress")
		self.assertEqual(self.display.getScript(gesture), "the built in script")


class TestModifierGestures(GestureTestCase):
	"""What a member yields carries no namespace, so only the right member may be asked."""

	def setUp(self):
		super().setUp()
		# The same key name on both displays, which is the collision that matters. It costs
		# a user nothing to name a key "space" on each of two displays.
		self.monarch._getModifierGestures = classmethod(
			lambda cls, model=None: iter([({"space"}, {"alt"})]),
		)
		self.focus._getModifierGestures = classmethod(
			lambda cls, model=None: iter([({"space"}, {"shift"})]),
		)

	def test_onlyTheOriginatingMemberContributes(self):
		self.press(FOCUS, gestureId="space+dot1")
		self.assertEqual(list(self.display._getModifierGestures()), [({"space"}, {"shift"})])

	def test_theOtherMemberContributesForItsOwnGestures(self):
		self.press(MONARCH, gestureId="space+dot1")
		self.assertEqual(list(self.display._getModifierGestures()), [({"space"}, {"alt"})])

	def test_aGestureFromSomeOtherDisplayGetsNoModifiers(self):
		"""Better a gesture that does nothing than one that does something else."""
		self.press("someOtherDisplay", gestureId="space+dot1")
		self.assertEqual(list(self.display._getModifierGestures()), [])

	def test_noModifierContextIsRecordedBeforeInstallation(self):
		"""Mid switch the outgoing display answers for its own modifiers, through NVDA."""
		import brlMultilineVirtual.gestures as gestureModule

		if hasattr(gestureModule._currentGesture, "source"):
			del gestureModule._currentGesture.source
		self.handler.display = self.focus.instances[0]
		self.press(FOCUS, gestureId="space+dot1")
		self.assertEqual(list(self.display._getModifierGestures()), [])

	def test_noModifiersBeforeAnyGestureHasArrived(self):
		import brlMultilineVirtual.gestures as gestureModule

		if hasattr(gestureModule._currentGesture, "source"):
			del gestureModule._currentGesture.source
		self.assertEqual(list(self.display._getModifierGestures()), [])

	def test_aMemberThatRaisesYieldsNothingRatherThanFailing(self):
		def explode(cls, model=None):
			raise RuntimeError("modifier gestures are unwell")

		self.focus._getModifierGestures = classmethod(explode)
		self.press(FOCUS, gestureId="space+dot1")
		self.assertEqual(list(self.display._getModifierGestures()), [])


class TestGestureLifecycle(VirtualDriverTestCase):
	def test_theDeciderIsRegisteredWhileTheDisplayIsLive(self):
		display = self.build(MONARCH)
		self.assertEqual(len(decide_executeGesture.handlers), 1)
		display.terminate()
		self.assertEqual(decide_executeGesture.handlers, [])

	def test_theDeciderIsNotLeftBehindByAFailedConstruction(self):
		self.monarch.failedOpens = -1
		self.configure(MONARCH)
		with self.assertRaises(RuntimeError):
			VirtualDisplay()
		self.assertEqual(decide_executeGesture.handlers, [])


if __name__ == "__main__":
	unittest.main()
