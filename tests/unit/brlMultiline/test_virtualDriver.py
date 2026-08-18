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

import types
import unittest

from ._virtualStubs import (
	DriverSetting,
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
from ._stubs import FakeBrailleHandler, callAfterQueue, callLaterQueue, displaySizeChanged

installVirtualStubs()

import braille  # noqa: E402

from brlMultilineVirtual import ackPatch, events, handover, vdConfig  # noqa: E402
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

		Both of the last two steps matter, and neither is decoration. NVDA assigns
		`handler.display` only after the constructor has returned, and it calls `initSettings`
		last of all; several things this driver does are deliberately inert until both have
		happened. A helper that skipped either would leave those guards untested.
		"""
		self.configure(*driverNames)
		display = VirtualDisplay()
		self.handler.display = display
		display.initSettings()
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


class TestLosingAMember(VirtualDriverTestCase):
	"""What happens to the composite when one of its displays goes.

	Before this, a lost member left its rows in the geometry and its band dark. NVDA went on
	laying text into rows that reached nothing, and if the segment following the focus was one
	of them the reader was left with a working display showing nothing at all.
	"""

	def setUp(self):
		super().setUp()
		self.display = self.build(MONARCH, FOCUS)
		self.sizes = []
		displaySizeChanged.register(self.noteSize)
		self.addCleanup(displaySizeChanged.unregister, self.noteSize)

	def noteSize(self, displaySize=None, numRows=None, numCols=None, **kwargs):
		self.sizes.append((numRows, numCols))

	def lose(self, index):
		"""Let a member go, and run the work it hands to the main thread."""
		self.display.slots[index].fail()
		callAfterQueue.flush()

	def test_theLostDisplaysRowsGoAway(self):
		self.lose(0)
		self.assertEqual((self.display.numRows, self.display.numCols), (1, 80))

	def test_theSurvivorMovesUpTheDisplay(self):
		"""The Focus was row 8 of 9; now it is the only row there is."""
		self.lose(0)
		self.assertEqual(self.display.slots[1].band.rowStart, 0)

	def test_theSurvivorIsWrittenToAgainWhateverItWasShowing(self):
		"""Its cells mean something different now, so what it last showed proves nothing."""
		self.display.display([1] * self.display.numCells)
		bgThread.flush()
		before = len(self.focus.instances[0].written)
		self.lose(0)
		self.display.display([1] * self.display.numCells)
		bgThread.flush()
		self.assertGreater(len(self.focus.instances[0].written), before)

	def test_writingFansOutToWhatIsLeft(self):
		self.lose(0)
		self.display.display(list(range(80)))
		bgThread.flush()
		self.assertEqual(self.focus.instances[0].written[-1], list(range(80)))

	def test_nvdaIsToldTheDisplayHasChangedSize(self):
		"""Through its own route: the handler recomputes the size and raises this itself."""
		self.lose(0)
		self.assertIn((1, 80), self.sizes)

	def test_theLostMemberIsNotWrittenToAgain(self):
		self.lose(0)
		before = len(self.monarch.instances[0].written)
		self.display.display(list(range(80)))
		bgThread.flush()
		self.assertEqual(len(self.monarch.instances[0].written), before)

	def test_losingTheOtherOneWorksTheSameWay(self):
		self.lose(1)
		self.assertEqual((self.display.numRows, self.display.numCols), (8, 32))
		self.assertEqual(self.display.slots[0].band.rowStart, 0)

	def test_losingEveryMemberKeepsTheCompositeWaiting(self):
		"""Rather than handing NVDA `handleDisplayUnavailable`, which falls back to no braille.

		With automatic detection on, that fallback could hand the returning display straight to
		NVDA instead of back to the composite the user asked for.
		"""
		self.lose(0)
		self.lose(1)
		self.assertEqual((self.display.numRows, self.display.numCols), (1, 80))
		self.assertTrue(
			any("waiting for one to return" in message for _level, message in log.messages),
			log.messages,
		)

	def test_writingWithNothingLeftIsHarmless(self):
		self.lose(0)
		self.lose(1)
		self.display.display([1] * self.display.numCells)
		bgThread.flush()

	def test_theWorkIsHandedToTheMainThread(self):
		"""A read failure runs on NVDA's I/O thread, where nothing may touch the handler."""
		self.display.slots[0].fail()
		self.assertEqual((self.display.numRows, self.display.numCols), (9, 80))
		callAfterQueue.flush()
		self.assertEqual((self.display.numRows, self.display.numCols), (1, 80))


class TestWaitingForAMemberToComeBack(VirtualDriverTestCase):
	"""A display switched off at startup, or lost since, is looked for until it is there."""

	def setUp(self):
		super().setUp()
		self.present = {MONARCH, FOCUS}
		self._realIsPresent = driverModule._isPresent
		driverModule._isPresent = lambda spec: spec.driverName in self.present
		self.addCleanup(setattr, driverModule, "_isPresent", self._realIsPresent)

	def poll(self, times=1):
		"""Let the timer fire, which is what the poll runs on."""
		for _each in range(times):
			callLaterQueue.fire()

	def test_aMemberThatWouldNotOpenAtStartupIsTriedAgain(self):
		self.focus.failedOpens = -1
		display = self.build(MONARCH, FOCUS)
		self.assertEqual([slot.driverName for slot in display.slots], [MONARCH])
		self.focus.failedOpens = 0
		self.poll()
		self.assertEqual([slot.driverName for slot in display.slots], [MONARCH, FOCUS])

	def test_itGoesBackInItsConfiguredPlace(self):
		"""Stacking order is the user's arrangement, not the order things happened to arrive."""
		self.monarch.failedOpens = -1
		display = self.build(MONARCH, FOCUS)
		self.monarch.failedOpens = 0
		self.poll()
		self.assertEqual([slot.driverName for slot in display.slots], [MONARCH, FOCUS])
		self.assertEqual(display.slots[0].band.rowStart, 0)
		self.assertEqual(display.slots[1].band.rowStart, 8)

	def test_theDisplayGrowsAgain(self):
		self.focus.failedOpens = -1
		display = self.build(MONARCH, FOCUS)
		self.assertEqual((display.numRows, display.numCols), (8, 32))
		self.focus.failedOpens = 0
		self.poll()
		self.assertEqual((display.numRows, display.numCols), (9, 80))

	def test_aMemberLostDuringTheSessionComesBack(self):
		display = self.build(MONARCH, FOCUS)
		display.slots[0].fail()
		callAfterQueue.flush()
		self.assertEqual((display.numRows, display.numCols), (1, 80))
		self.poll()
		self.assertEqual((display.numRows, display.numCols), (9, 80))
		self.assertFalse(display.slots[0].failed)

	def test_theDeadDriverIsLetGoOfWhenItsReplacementArrives(self):
		display = self.build(MONARCH, FOCUS)
		dead = display.slots[0].driver
		display.slots[0].fail()
		callAfterQueue.flush()
		self.poll()
		self.assertEqual(dead.terminated, 1)
		self.assertIsNot(display.slots[0].driver, dead)

	def test_aDisplayWindowsCannotSeeIsNotOpened(self):
		"""Opening talks to hardware on the main thread, so absence is worth believing."""
		self.focus.failedOpens = -1
		self.build(MONARCH, FOCUS)
		self.present.discard(FOCUS)
		attempts = self.focus.openAttempts
		self.poll()
		self.assertEqual(self.focus.openAttempts, attempts)

	def test_attemptsSpreadOutWhileADisplayStaysAway(self):
		"""A display left switched off must not cost an open attempt every few seconds."""
		self.focus.failedOpens = -1
		display = self.build(MONARCH, FOCUS)
		self.poll()
		first = self.focus.openAttempts
		self.poll(times=3)
		self.assertEqual(self.focus.openAttempts, first)
		self.assertGreater(display._nextAttempt[FOCUS], 0)

	def waits(self, display, times):
		"""The wait chosen each time, with the clock moved to each deadline as it arrives.

		Which is the whole of what makes this a test. Called with the same `now` every time, a
		backoff that reads its previous wait back off the deadline appears to double while in
		fact it never grows at all.
		"""
		chosen = []
		now = 0.0
		for _each in range(times):
			display._deferAttempt(FOCUS, now)
			chosen.append(display._nextAttempt[FOCUS] - now)
			now = display._nextAttempt[FOCUS]
		return chosen

	def test_theWaitGrowsWithEachFailure(self):
		display = self.build(MONARCH, FOCUS)
		chosen = self.waits(display, 3)
		self.assertEqual(chosen, sorted(chosen))
		self.assertGreater(chosen[-1], chosen[0])

	def test_theWaitIsBounded(self):
		display = self.build(MONARCH, FOCUS)
		self.assertEqual(max(self.waits(display, 20)), driverModule.POLL_BACKOFF_LIMIT)

	def test_comingBackForgetsTheWait(self):
		"""Or a display that goes away twice would start from where it left off."""
		self.focus.failedOpens = -1
		display = self.build(MONARCH, FOCUS)
		self.waits(display, 5)
		self.focus.failedOpens = 0
		display._nextAttempt[FOCUS] = 0
		self.poll()
		self.assertNotIn(FOCUS, display._retryDelays)
		self.assertNotIn(FOCUS, display._nextAttempt)

	def test_aMemberWithAPortOfItsOwnIsAlwaysAttempted(self):
		"""Nothing detected it, because the user said where it is."""
		driverModule._isPresent = self._realIsPresent
		vdConfig.setDevices([DeviceSpec(MONARCH), DeviceSpec(FOCUS, "COM4")])
		self.focus.failedOpens = -1
		display = VirtualDisplay()
		self.handler.display = display
		display.initSettings()
		attempts = self.focus.openAttempts
		self.poll()
		self.assertGreater(self.focus.openAttempts, attempts)

	def test_aFailedAttemptIsNotAnErrorInTheLog(self):
		"""It is the ordinary state of a display that is switched off."""
		self.focus.failedOpens = -1
		self.build(MONARCH, FOCUS)
		log.messages.clear()
		self.poll()
		self.assertEqual([message for level, message in log.messages if level == "error"], [])

	def test_nothingIsLookedForWhenEveryMemberIsThere(self):
		display = self.build(MONARCH, FOCUS)
		attempts = self.focus.openAttempts
		self.poll()
		self.assertEqual(self.focus.openAttempts, attempts)
		self.assertEqual(display._missingSpecs(), [])

	def test_thePollStopsWithTheDisplay(self):
		display = self.build(MONARCH, FOCUS)
		display.terminate()
		self.assertIsNone(display._pollTimer)
		self.assertEqual(callLaterQueue.pending, [])

	def test_thePollKeepsGoingWhileTheDisplayLives(self):
		self.focus.failedOpens = -1
		self.build(MONARCH, FOCUS)
		self.poll()
		self.assertEqual(len(callLaterQueue.pending), 1)


class TestASameSizeReconnect(VirtualDriverTestCase):
	"""The case a size alone cannot carry.

	One configured display, lost and reconnected: the composite is exactly as big afterwards as
	it was before, so nothing about its size ever changes. The arrangement above it is named
	after physical displays, so it still has to be built again — and the returning display is
	showing whatever it had before NVDA ever saw it until something writes to it.
	"""

	def setUp(self):
		super().setUp()
		self.announced = []
		events.membersChanged.register(self.noteMembers)
		self.addCleanup(events.membersChanged.unregister, self.noteMembers)
		self.sizes = []
		displaySizeChanged.register(self.noteSize)
		self.addCleanup(displaySizeChanged.unregister, self.noteSize)

	def noteMembers(self, display=None, **kwargs):
		self.announced.append([slot.driverName for slot in display.slots if not slot.failed])

	def noteSize(self, **kwargs):
		self.sizes.append(kwargs)

	def loseAndRegain(self, display):
		display.slots[0].fail()
		callAfterQueue.flush()
		callLaterQueue.fire()

	def test_theMembersChangingIsAnnouncedEvenThoughTheSizeDoesNot(self):
		display = self.build(MONARCH)
		self.loseAndRegain(display)
		self.assertEqual(self.sizes, [])
		self.assertEqual(self.announced[-1], [MONARCH])

	def test_theReturningDisplayIsWrittenToAtOnce(self):
		"""It is showing whatever it had before NVDA saw it until something writes."""
		display = self.build(MONARCH)
		self.handler.buffer = types.SimpleNamespace(windowBrailleCells=[7] * display.numCells)
		self.loseAndRegain(display)
		bgThread.flush()
		self.assertEqual(self.monarch.instances[-1].written[-1], [7] * display.numCells)

	def test_theLossIsAnnouncedToo(self):
		display = self.build(MONARCH, FOCUS)
		display.slots[0].fail()
		callAfterQueue.flush()
		self.assertEqual(self.announced[-1], [FOCUS])

	def test_theLastSurvivorReturningFirstIsAnnounced(self):
		"""Every member gone keeps the geometry, so the first one back may not change it."""
		display = self.build(MONARCH, FOCUS)
		display.slots[0].fail()
		callAfterQueue.flush()
		display.slots[1].fail()
		callAfterQueue.flush()
		self.announced.clear()
		# Only the Focus comes back; the Monarch is still away.
		self.monarch.failedOpens = -1
		callLaterQueue.fire()
		self.assertEqual(self.announced[-1], [FOCUS])

	def test_nothingIsAnnouncedWhenNothingChanged(self):
		display = self.build(MONARCH, FOCUS)
		self.announced.clear()
		display._relayout()
		self.assertEqual(self.announced, [])


class TestMemberSettings(VirtualDriverTestCase):
	"""Selecting the composite must not hide the settings of the displays behind it.

	NVDA's Braille settings dialog builds its controls from `supportedSettings` on the display
	in use. Without this, dot firmness and everything like it becomes unreachable for as long
	as the displays are being used together.
	"""

	def setUp(self):
		super().setUp()
		self.monarch.supportedSettings = (DriverSetting("dotFirmness", "Dot &firmness", defaultVal="1"),)
		self.focus.supportedSettings = (DriverSetting("wizWheelAction", "Wiz wheel &action"),)
		self.display = self.build(MONARCH, FOCUS)
		self.monarchDriver = self.monarch.instances[0]
		self.focusDriver = self.focus.instances[0]
		self.monarchDriver.dotFirmness = "1"
		self.monarchDriver.availableDotfirmnesss = {"1": "Soft", "2": "Firm"}
		self.focusDriver.wizWheelAction = "scroll"

	def ids(self):
		return [setting.id for setting in self.display.supportedSettings]

	def test_everyMembersSettingsAreOffered(self):
		self.assertEqual(
			self.ids(),
			["hidBrailleStandard_dotFirmness", "freedomScientific_wizWheelAction"],
		)

	def test_eachOneSaysWhichDisplayItBelongsTo(self):
		setting = self.display.supportedSettings[0]
		self.assertIn("hidBrailleStandard display", setting.displayName)
		self.assertIn("Dot &firmness", setting.displayNameWithAccelerator)

	def test_readingOneReadsTheMembers(self):
		self.assertEqual(self.display.hidBrailleStandard_dotFirmness, "1")

	def test_writingOneWritesToTheMember(self):
		self.display.hidBrailleStandard_dotFirmness = "2"
		self.assertEqual(self.monarchDriver.dotFirmness, "2")
		self.assertFalse(hasattr(type(self.display), "hidBrailleStandard_dotFirmness"))

	def test_theChoicesForASettingComeFromTheMember(self):
		"""Under the name the dialog asks for, which `capitalize` makes unguessable."""
		self.assertEqual(
			self.display.availableHidbraillestandard_dotfirmnesss,
			{"1": "Soft", "2": "Firm"},
		)

	def test_theCompositeStoresNoneOfThem(self):
		"""Each is the member's to keep, in the section it reads when used on its own."""
		for setting in self.display.supportedSettings:
			self.assertFalse(setting.useConfig)

	def test_theMembersOwnSettingIsNotAltered(self):
		"""It is copied, not renamed: that object belongs to the member."""
		self.display.supportedSettings  # noqa: B018 - read for its effect on the member.
		self.assertEqual(self.monarch.supportedSettings[0].id, "dotFirmness")
		self.assertTrue(self.monarch.supportedSettings[0].useConfig)

	def test_savingSavesEachMember(self):
		self.display.saveSettings()
		self.assertEqual(self.monarchDriver.settingsSaved, 1)
		self.assertEqual(self.focusDriver.settingsSaved, 1)

	def test_cancellingPutsTheMembersSettingsBack(self):
		"""NVDA writes a driver's settings as the dialog is used, so Cancel means this.

		Without it the change the user backed out of stays in force, and stays until the next
		configuration save writes it down for good.
		"""
		self.display.saveSettings()
		self.display.hidBrailleStandard_dotFirmness = "2"
		self.assertEqual(self.monarchDriver.dotFirmness, "2")
		self.display.loadSettings()
		self.assertEqual(self.monarchDriver.dotFirmness, "1")

	def test_everyMemberIsAskedToPutItsSettingsBack(self):
		self.display.loadSettings()
		self.assertEqual(self.monarchDriver.settingsLoaded, 1)
		self.assertEqual(self.focusDriver.settingsLoaded, 1)

	def test_oneMemberFailingDoesNotStopTheOthers(self):
		def explode(onlyChanged=False):
			raise RuntimeError("settings are unwell")

		self.monarchDriver.loadSettings = explode
		self.display.loadSettings()
		self.assertEqual(self.focusDriver.settingsLoaded, 1)

	def replaceTheMonarch(self):
		"""Put a fresh Monarch in, as a reconnection does, and hand back its driver.

		The narrow step rather than the whole poll, because the whole poll happens to look up
		other attributes on this driver and rebuilding the map is what those do. What is being
		asked here is whether a name already in the map follows the display or the object.
		"""
		self.display.hidBrailleStandard_dotFirmness  # noqa: B018 - read to build the map.
		retired = self.display.slots[0]
		retired.failed = True
		replacement = self.monarch(port=None)
		replacement.dotFirmness = "new"
		self.display._admit(retired.spec, replacement)
		self.assertIsNot(self.display.slots[0], retired)
		return replacement

	def test_aSettingReachesTheDisplayThatIsThereNow(self):
		"""A member replaced after a reconnection is a different driver object.

		The settings dialog, or the settings ring, may be holding the list from before it, so a
		name that was looked up once must not go on addressing the driver that has been let go.
		"""
		self.replaceTheMonarch()
		self.assertEqual(self.display.hidBrailleStandard_dotFirmness, "new")

	def test_writingReachesTheDisplayThatIsThereNow(self):
		replacement = self.replaceTheMonarch()
		self.display.hidBrailleStandard_dotFirmness = "2"
		self.assertEqual(replacement.dotFirmness, "2")
		self.assertEqual(self.monarchDriver.dotFirmness, "1")

	def test_aMemberThatHasGoneOffersNothing(self):
		self.display.slots[0].failed = True
		self.assertEqual(self.ids(), ["freedomScientific_wizWheelAction"])

	def test_aMemberWhoseSettingsCannotBeReadIsSkipped(self):
		def explode(self):
			raise RuntimeError("settings are unwell")

		type(self.monarchDriver).supportedSettings = property(explode)
		try:
			self.assertEqual(self.ids(), ["freedomScientific_wizWheelAction"])
		finally:
			del type(self.monarchDriver).supportedSettings

	def test_anUnknownAttributeIsStillAnError(self):
		with self.assertRaises(AttributeError):
			self.display.somethingNobodyHas

	def test_ordinaryAttributesAreUntouched(self):
		self.display.numRows = 3
		self.assertEqual(self.display.numRows, 3)


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

	def test_nothingIsTranslatedWhileTheSameInstanceIsReconstructed(self):
		"""Reselecting a display already in use rebuilds it in place, so identity says nothing.

		`_switchDisplay` takes its `sameDisplayReInit` path when the driver has not changed:
		it terminates and reruns `__init__` on the very object `braille.handler.display`
		already points at. Editing the member list and reopening the composite is exactly
		that. The members, the bands and the geometry are all replaced while identity holds
		steady, so a press in that window would be rebased onto the new bands and routed
		against the arrangement built for the old ones.
		"""
		self.display.terminate()
		self.configure(FOCUS, MONARCH)
		self.display.__init__()
		# Constructed, gesture handling reinstalled, and still the same object NVDA holds —
		# but `initSettings` has not run, so nothing may act as though the switch is over.
		self.assertIs(braille.handler.display, self.display)
		gesture = StubBrailleDisplayGesture(MONARCH, "routing", [0])
		self.assertTrue(decide_executeGesture.decide(gesture=gesture))
		self.assertEqual(gesture.cellIndexes, [0])

	def test_translationResumesOnceTheReinitialisationFinishes(self):
		self.display.terminate()
		self.configure(FOCUS, MONARCH)
		self.display.__init__()
		self.display.initSettings()
		# The Focus is now the top band, so the Monarch starts at row 1 of an 80 wide
		# composite: its own cell 0 is composite cell 80.
		gesture = StubBrailleDisplayGesture(MONARCH, "routing", [0])
		self.assertTrue(decide_executeGesture.decide(gesture=gesture))
		self.assertEqual(gesture.cellIndexes, [80])

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
