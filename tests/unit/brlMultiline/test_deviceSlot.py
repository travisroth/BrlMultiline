# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for how writes reach one member of the virtual display.

Two properties carry the weight here. Unchanged frames must not be sent, because NVDA
rewrites the whole display on every cursor blink and a second display showing something
static would otherwise be written to several times a second for no reason. And a write
arriving while another is queued must replace it rather than queue a second, which is the
behaviour `BrailleHandler` has and the reason it collapses a burst into its last frame.
"""

import unittest

from ._virtualStubs import FakeDevice, FakeDriver, bgThread, installVirtualStubs, log, resetStubs

installVirtualStubs()

from brlMultilineVirtual import memberGuard  # noqa: E402
from brlMultilineVirtual.deviceSlot import DeviceSlot  # noqa: E402
from brlMultilineVirtual.virtualLayout import DeviceBand, DeviceSpec  # noqa: E402

BAND = DeviceBand(rowStart=0, numRows=1, numCols=4)
SPEC = DeviceSpec("fakeDisplay", "auto")


class DeviceSlotTestCase(unittest.TestCase):
	threadSafe = True

	def setUp(self):
		resetStubs()
		self.driver = FakeDriver(isThreadSafe=self.threadSafe)
		self.slot = DeviceSlot(SPEC, self.driver, BAND)

	def write(self, cells):
		"""Write, then let any queued call run, so a test reads the same result either way."""
		sent = self.slot.write(cells)
		bgThread.flush()
		return sent


class TestChangeDetection(DeviceSlotTestCase):
	def test_firstWriteIsSent(self):
		self.assertTrue(self.write([1, 2, 3, 4]))
		self.assertEqual(self.driver.written, [[1, 2, 3, 4]])

	def test_unchangedWriteIsDropped(self):
		self.write([1, 2, 3, 4])
		self.assertFalse(self.write([1, 2, 3, 4]))
		self.assertEqual(self.driver.written, [[1, 2, 3, 4]])

	def test_changedWriteIsSent(self):
		self.write([1, 2, 3, 4])
		self.assertTrue(self.write([1, 2, 3, 5]))
		self.assertEqual(len(self.driver.written), 2)

	def test_writingBackAnEarlierFrameIsSent(self):
		"""Only the last frame is remembered, not every frame ever shown."""
		self.write([1, 1, 1, 1])
		self.write([2, 2, 2, 2])
		self.assertTrue(self.write([1, 1, 1, 1]))
		self.assertEqual(len(self.driver.written), 3)

	def test_invalidateForcesTheNextWrite(self):
		self.write([1, 2, 3, 4])
		self.slot.invalidate()
		self.assertTrue(self.write([1, 2, 3, 4]))
		self.assertEqual(len(self.driver.written), 2)

	def test_theCallersListIsNotHeld(self):
		"""A caller reusing its buffer must not silently change what the slot remembers."""
		cells = [1, 2, 3, 4]
		self.write(cells)
		cells[0] = 9
		self.assertTrue(self.write([9, 2, 3, 4]))


class TestThreadSafeScheduling(DeviceSlotTestCase):
	threadSafe = True

	def test_writeIsQueuedRatherThanImmediate(self):
		self.slot.write([1, 2, 3, 4])
		self.assertEqual(self.driver.written, [])
		self.assertEqual(len(bgThread.queued), 1)
		bgThread.flush()
		self.assertEqual(self.driver.written, [[1, 2, 3, 4]])

	def test_burstCollapsesToTheLastFrame(self):
		self.slot.write([1, 1, 1, 1])
		self.slot.write([2, 2, 2, 2])
		self.slot.write([3, 3, 3, 3])
		# One queued call, however many writes arrived before it ran.
		self.assertEqual(len(bgThread.queued), 1)
		bgThread.flush()
		self.assertEqual(self.driver.written, [[3, 3, 3, 3]])

	def test_aLaterWriteQueuesAgain(self):
		self.write([1, 1, 1, 1])
		self.slot.write([2, 2, 2, 2])
		self.assertEqual(len(bgThread.queued), 1)
		bgThread.flush()
		self.assertEqual(self.driver.written, [[1, 1, 1, 1], [2, 2, 2, 2]])

	def test_runningWithNothingQueuedIsHarmless(self):
		self.slot._bgExecutor(0)
		self.assertEqual(self.driver.written, [])


class TestDirectScheduling(DeviceSlotTestCase):
	threadSafe = False

	def test_writeIsImmediate(self):
		self.slot.write([1, 2, 3, 4])
		self.assertEqual(self.driver.written, [[1, 2, 3, 4]])
		self.assertEqual(bgThread.queued, [])


class TestFailure(unittest.TestCase):
	def setUp(self):
		resetStubs()
		self.driver = FakeDriver(isThreadSafe=False, failOnDisplay=True)
		self.slot = DeviceSlot(SPEC, self.driver, BAND)

	def test_aRaisingDriverIsDropped(self):
		self.slot.write([1, 2, 3, 4])
		self.assertTrue(self.slot.failed)
		self.assertTrue(any(level == "error" for level, _message in log.messages))

	def test_nothingIsWrittenAfterFailure(self):
		self.slot.write([1, 2, 3, 4])
		self.driver.failOnDisplay = False
		self.assertFalse(self.slot.write([5, 6, 7, 8]))
		self.assertEqual(self.driver.written, [])


class TestNoticingADisconnection(unittest.TestCase):
	"""The read side knows first, and a member showing something static is never written to.

	Taken from a real log: a Bluetooth display dropped, the overlapped read completion failed
	with WinError 1167 twenty milliseconds before anything tried to write, and NVDA's I/O thread
	logged the traceback and went no further. `IoBase._ioDone` offers that error to
	`_onReadError` before raising, which is the one route out of it.
	"""

	def setUp(self):
		resetStubs()
		self.lost = []
		self.device = FakeDevice()
		self.driver = FakeDriver(isThreadSafe=False, device=self.device)
		self.slot = DeviceSlot(SPEC, self.driver, BAND, onFailure=self.lost.append)

	def readFails(self, error=1167):
		"""Let the read fail as NVDA's I/O thread would, swallowing what it swallows."""
		try:
			self.device.readFails(error)
		except OSError:
			pass

	def test_aLostDeviceIsNoticedWithoutWritingToIt(self):
		self.readFails()
		self.assertTrue(self.slot.failed)
		self.assertEqual(self.driver.written, [])

	def test_theCompositeIsTold(self):
		self.readFails()
		self.assertEqual(self.lost, [self.slot])

	def test_itIsToldOnceForOneLoss(self):
		"""A display coming apart fails its read and its next write moments apart."""
		self.readFails()
		self.driver.failOnDisplay = True
		self.slot.write([1, 2, 3, 4])
		self.assertEqual(self.lost, [self.slot])

	def test_nothingIsWrittenToItAfterwards(self):
		self.readFails()
		self.assertFalse(self.slot.write([1, 2, 3, 4]))
		self.assertEqual(self.driver.written, [])


class TestChainingTheDriversOwnHook(unittest.TestCase):
	"""`freedomScientific` supplies one of these, and losing it would cost the user a display.

	Its hook restarts the display when a suspend breaks the pipe, and returns True to say so.
	A driver that says it has handled the error is restarting, not dying.
	"""

	def setUp(self):
		resetStubs()
		self.lost = []
		self.seen = []
		self.handle = False
		# One bound method object rather than a fresh one per attribute read, so that "the
		# driver got its own hook back" can be asked as a question about identity.
		self.theirsHook = self.theirs
		self.device = FakeDevice(onReadError=self.theirsHook)
		self.driver = FakeDriver(isThreadSafe=False, device=self.device)
		self.slot = DeviceSlot(SPEC, self.driver, BAND, onFailure=self.lost.append)

	def theirs(self, error: int) -> bool:
		self.seen.append(error)
		return self.handle

	def test_theDriversOwnHookStillRuns(self):
		try:
			self.device.readFails(995)
		except OSError:
			pass
		self.assertEqual(self.seen, [995])

	def test_anErrorTheDriverHandledDoesNotDropTheMember(self):
		self.handle = True
		self.device.readFails(995)
		self.assertFalse(self.slot.failed)
		self.assertEqual(self.lost, [])

	def test_anErrorTheDriverDidNotHandleDoes(self):
		try:
			self.device.readFails(1167)
		except OSError:
			pass
		self.assertTrue(self.slot.failed)

	def test_theAnswerGivenBackIsTheDriversOwn(self):
		"""So the I/O thread goes on logging what it logged; that log is how this was found."""
		self.handle = True
		self.assertTrue(self.device._onReadError(995))
		self.handle = False
		self.assertFalse(self.device._onReadError(1167))

	def test_aDriverHookThatRaisesDoesNotStopTheMemberBeingDropped(self):
		def raising(error):
			raise RuntimeError("nothing good")

		self.device._onReadError = None
		slot = DeviceSlot(SPEC, FakeDriver(device=FakeDevice(onReadError=raising)), BAND)
		device = slot.driver._dev
		try:
			device.readFails(1167)
		except OSError:
			pass
		self.assertTrue(slot.failed)

	def test_theDriverGetsItsOwnHookBackWhenTheMemberIsClosed(self):
		self.slot.terminate()
		self.assertIs(self.device._onReadError, self.theirsHook)

	def test_aHookSomethingElseReplacedIsLeftAlone(self):
		def somebodyElse(error):
			return False

		self.device._onReadError = somebodyElse
		self.slot.terminate()
		self.assertIs(self.device._onReadError, somebodyElse)


class TestADriverWithNoDeviceToWatch(unittest.TestCase):
	"""`_dev` is a private attribute of somebody else's driver, and not every driver has one."""

	def setUp(self):
		resetStubs()
		self.driver = FakeDriver(isThreadSafe=False, failOnDisplay=True)
		self.slot = DeviceSlot(SPEC, self.driver, BAND)

	def test_theMemberStillWorks(self):
		self.assertFalse(self.slot.failed)

	def test_theSlowerDetectionStillApplies(self):
		self.slot.write([1, 2, 3, 4])
		self.assertTrue(self.slot.failed)

	def test_closingItIsHarmless(self):
		self.slot.terminate()
		self.assertTrue(self.driver.terminated)


class TestTerminate(DeviceSlotTestCase):
	def test_driverIsTerminated(self):
		self.slot.terminate()
		self.assertTrue(self.driver.terminated)

	def test_clearIsSuppressedWhenAsked(self):
		self.slot.terminate(suppressDisplayClear=True)
		self.assertTrue(self.driver._suppressDisplayClear)

	def test_clearIsNotSuppressedByDefault(self):
		self.slot.terminate()
		self.assertFalse(self.driver._suppressDisplayClear)

	def test_aQueuedWriteIsAbandoned(self):
		self.slot.write([1, 2, 3, 4])
		self.slot.terminate()
		bgThread.flush()
		self.assertEqual(self.driver.written, [])

	def test_aRaisingTerminateIsReported(self):
		def raiseOnTerminate():
			raise OSError("the display has gone away")

		self.driver.terminate = raiseOnTerminate
		self.slot.terminate()
		self.assertTrue(any(level == "error" for level, _message in log.messages))



class HangingDriver(FakeDriver):
	"""A driver whose calls run past the guard's limit, as a display that stopped answering.

	The guard's watchdog is a thread; here the check is made from inside the call instead,
	with the clock moved on first, which is the same moment deterministically.
	"""

	def __init__(self, guard, clock, **kwargs):
		super().__init__(**kwargs)
		self.guard = guard
		self.clock = clock
		self.hangOnDisplay = True
		self.hangOnTerminate = False
		self.suppressedAtTerminate = None
		self.slot = None
		self.terminateDuringDisplay = False

	def _hang(self):
		self.clock[0] += memberGuard.MEMBER_WRITE_TIMEOUT + 1
		self.guard.check()

	def display(self, cells):
		if self.terminateDuringDisplay:
			self.slot.terminate()
		if self.hangOnDisplay:
			self._hang()
		super().display(cells)

	def terminate(self):
		self.suppressedAtTerminate = self._suppressDisplayClear
		if self.hangOnTerminate:
			self._hang()
		super().terminate()


class TestAMemberThatStopsAnswering(unittest.TestCase):
	"""A write that never returns holds NVDA's shared I/O thread, and every other display's
	writes and keys with it. The member is given up on and its write cancelled."""

	def setUp(self):
		resetStubs()
		self.clock = [0.0]
		self.cancelled = []
		self.guard = memberGuard.MemberGuard(
			clock=lambda: self.clock[0],
			cancel=lambda driver, threadId: self.cancelled.append(driver) or True,
			runThread=False,
		)
		self.failures = []
		self.driver = HangingDriver(self.guard, self.clock)
		self.slot = DeviceSlot(SPEC, self.driver, BAND, onFailure=self.failures.append, guard=self.guard)
		self.driver.slot = self.slot

	def test_aWriteThatNeverReturnsGivesTheMemberUp(self):
		self.slot.write([1, 2, 3, 4])
		bgThread.flush()
		self.assertTrue(self.slot.failed)
		self.assertEqual([self.slot], self.failures)

	def test_theStuckWriteIsCancelled(self):
		self.slot.write([1, 2, 3, 4])
		bgThread.flush()
		self.assertEqual([self.driver], self.cancelled)

	def test_nothingMoreIsSentToIt(self):
		self.slot.write([1, 2, 3, 4])
		bgThread.flush()
		self.assertFalse(self.slot.write([5, 6, 7, 8]))
		self.assertEqual([], bgThread.queued)

	def test_aHealthyWriteIsLeftAlone(self):
		self.driver.hangOnDisplay = False
		self.slot.write([1, 2, 3, 4])
		bgThread.flush()
		self.assertFalse(self.slot.failed)
		self.assertEqual([], self.cancelled)

	def test_aStuckCloseIsCancelled(self):
		"""Blanking on the way out is a write from the main thread."""
		self.driver.hangOnTerminate = True
		self.slot.terminate()
		self.assertEqual([self.driver], self.cancelled)
		self.assertTrue(self.driver.terminated)

	def test_aMemberStillWritingIsNotBlankedAsItCloses(self):
		"""The blank would be a second write on the same OVERLAPPED, behind a stuck one."""
		self.driver.hangOnDisplay = False
		self.driver.terminateDuringDisplay = True
		self.slot.write([1, 2, 3, 4])
		bgThread.flush()
		self.assertTrue(self.driver.suppressedAtTerminate)

	def test_anIdleMemberIsStillBlankedAsItCloses(self):
		self.slot.terminate()
		self.assertFalse(self.driver.suppressedAtTerminate)


if __name__ == "__main__":
	unittest.main()
