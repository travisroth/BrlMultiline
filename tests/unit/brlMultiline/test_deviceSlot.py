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

from ._virtualStubs import FakeDriver, bgThread, installVirtualStubs, log, resetStubs

installVirtualStubs()

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


if __name__ == "__main__":
	unittest.main()
