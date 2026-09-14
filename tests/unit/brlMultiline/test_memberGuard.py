# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""The watch on calls into member drivers: when it rescues one, in what order, and whether the
cancellation actually frees a thread stuck the way `hwIo.IoBase.write` gets stuck.

The last of those is tested against real Windows handles. A named pipe whose reader never
reads leaves an overlapped write pending, and a thread waiting on it with
`GetOverlappedResult(bWait=True)` is exactly NVDA's I/O thread inside a write to a display that
has stopped answering.
"""

import ctypes
import itertools
import os
import sys
import threading
import unittest
from ctypes import wintypes

from ._virtualStubs import installVirtualStubs, log, resetStubs

installVirtualStubs()

from brlMultilineVirtual import memberGuard  # noqa: E402
from brlMultilineVirtual.memberGuard import MemberGuard  # noqa: E402


class FakeClock:
	def __init__(self):
		self.now = 100.0

	def __call__(self) -> float:
		return self.now


class StuckCall:
	"""A watched call running on its own thread, which does not return until released.

	The watchdog is a separate thread in NVDA, and what goes wrong goes wrong between the two:
	the call returning while the rescue is still at work. So the call is on a thread of its own
	here, and the test thread plays the watchdog, with the clock under its control.
	"""

	def __init__(self, guard, driver, label="fake writing", timeout=6.0, onOverdue=None, order=None):
		self.release = threading.Event()
		self.entered = threading.Event()
		self.left = threading.Event()
		self.watch = None
		self.threadId = None
		self._order = order

		def run():
			with guard.watching(label, driver, timeout, onOverdue) as watch:
				self.watch = watch
				self.threadId = threading.get_native_id()
				self.entered.set()
				self.release.wait(5)
			if self._order is not None:
				self._order.append("the call left")
			self.left.set()

		self.thread = threading.Thread(target=run, daemon=True)
		self.thread.start()
		assert self.entered.wait(5), "the call never started"

	def finish(self) -> None:
		self.release.set()
		self.thread.join(5)


class GuardTestCase(unittest.TestCase):
	def setUp(self):
		resetStubs()
		self.clock = FakeClock()
		self.events: list = []
		self.cancelResult = True
		self.cancelFrees = True
		self.calls: list[StuckCall] = []
		self.driver = object()
		self.useGuard()

	def tearDown(self):
		for call in self.calls:
			call.finish()

	def useGuard(self, grace: float = 5.0) -> None:
		self.guard = MemberGuard(clock=self.clock, cancel=self.cancel, runThread=False, grace=grace)

	def start(self, **kwargs) -> StuckCall:
		call = StuckCall(self.guard, kwargs.pop("driver", self.driver), **kwargs)
		self.calls.append(call)
		return call

	def cancel(self, driver, threadId):
		self.events.append(("cancel", driver, threadId))
		if self.cancelFrees:
			for call in self.calls:
				if call.threadId == threadId:
					call.release.set()
		return self.cancelResult

	def giveUp(self):
		self.events.append(("gave up",))

	def messages(self, level: str) -> list[str]:
		return [message for each, message in log.messages if each == level]


class TestRescuing(GuardTestCase):
	def test_aCallWithinItsLimitIsLeftAlone(self):
		call = self.start(onOverdue=self.giveUp)
		self.clock.now += 5.9
		self.assertEqual([], self.guard.check())
		call.finish()
		self.assertFalse(call.watch.fired)
		self.assertEqual([], self.events)

	def test_aCallPastItsLimitIsGivenUpAndCancelled(self):
		call = self.start(onOverdue=self.giveUp)
		self.clock.now += 6.0
		self.guard.check()
		self.assertTrue(call.left.wait(5))
		self.assertTrue(call.watch.fired)
		self.assertEqual([("gave up",), ("cancel", self.driver, call.threadId)], self.events)

	def test_theMemberIsGivenUpBeforeItsThreadIsFreed(self):
		"""Freed first, the thread would go straight on to the next write queued for it."""
		self.start(timeout=1.0, onOverdue=self.giveUp)
		self.clock.now += 2
		self.guard.check()
		self.assertEqual(["gave up", "cancel"], [event[0] for event in self.events])

	def test_aRescuedCallIsHeldUntilTheRescueHasFinished(self):
		"""Cancellation only asks. Were the call let go at once, the thread — NVDA's shared I/O
		thread — could start another member's write while this rescue was still cancelling I/O
		against it."""
		order: list[str] = []

		def cancel(driver, threadId):
			self.calls[0].release.set()
			# Long enough for the call to have left, if nothing held it.
			self.assertFalse(self.calls[0].left.wait(0.2), "the call left before the rescue finished")
			order.append("the rescue finished cancelling")
			return True

		self.guard = MemberGuard(clock=self.clock, cancel=cancel, runThread=False, grace=5.0)
		call = self.start(timeout=1.0, order=order)
		self.clock.now += 2
		self.guard.check()
		self.assertTrue(call.left.wait(5))
		self.assertEqual(["the rescue finished cancelling", "the call left"], order)

	def test_aCallThatReturnsOnceCancelledIsReportedFreed(self):
		call = self.start(timeout=1.0)
		self.clock.now += 2
		self.guard.check()
		self.assertTrue(call.left.wait(5))
		self.assertEqual([], self.messages("error"))

	def test_aCallStillStuckAfterASuccessfulCancelIsReportedStuck(self):
		"""What the cancellation returned proves nothing; only the call returning does."""
		self.cancelFrees = False
		self.useGuard(grace=0.05)
		self.start(timeout=1.0)
		self.clock.now += 2
		self.guard.check()
		errors = self.messages("error")
		self.assertTrue(any("still has not returned" in message for message in errors), errors)
		self.assertTrue(any("restarted" in message for message in errors), errors)

	def test_aCallWithNothingToCancelSaysBrailleMayNotComeBack(self):
		"""A driver stuck on a lock cannot be freed, and the user needs to know to restart."""
		self.cancelFrees = False
		self.cancelResult = False
		self.useGuard(grace=0.05)
		self.start(timeout=1.0)
		self.clock.now += 2
		self.guard.check()
		errors = self.messages("error")
		self.assertTrue(any("no I/O to cancel" in message for message in errors), errors)

	def test_aStuckCallIsStillLetGoWhenItFinallyReturns(self):
		"""Its rescue has finished, so nothing is left to hold it for."""
		self.cancelFrees = False
		self.useGuard(grace=0.05)
		call = self.start(timeout=1.0)
		self.clock.now += 2
		self.guard.check()
		call.release.set()
		self.assertTrue(call.left.wait(5))

	def test_eachCallIsRescuedOnce(self):
		self.cancelFrees = False
		self.useGuard(grace=0.05)
		self.start(timeout=1.0, onOverdue=self.giveUp)
		self.clock.now += 2
		self.guard.check()
		self.clock.now += 2
		self.assertEqual([], self.guard.check())
		self.assertEqual(2, len(self.events))

	def test_aFinishedCallIsNoLongerWatched(self):
		with self.guard.watching("fake writing", self.driver, 1.0):
			pass
		self.assertEqual((), self.guard.watches)
		self.clock.now += 10
		self.assertEqual([], self.guard.check())

	def test_aCallThatRaisesIsNoLongerWatched(self):
		with self.assertRaises(OSError), self.guard.watching("fake writing", self.driver, 1.0):
			raise OSError("gone")
		self.assertEqual((), self.guard.watches)

	def test_theRescueIsLogged(self):
		self.start(label="freedomScientific writing", timeout=1.0)
		self.clock.now += 7
		self.guard.check()
		warnings = self.messages("warning")
		self.assertTrue(any("freedomScientific writing" in message for message in warnings), warnings)

	def test_givingUpThatRaisesStillFreesTheThread(self):
		def explode():
			raise RuntimeError("layout is unwell")

		call = self.start(timeout=1.0, onOverdue=explode)
		self.clock.now += 2
		self.guard.check()
		self.assertEqual("cancel", self.events[-1][0])
		self.assertTrue(call.left.wait(5))

	def test_onlyTheOverdueCallIsRescued(self):
		self.start(label="stuck writing", driver=object(), timeout=1.0)
		self.clock.now += 5
		healthy = self.start(label="healthy writing", timeout=1.0)
		rescued = self.guard.check()
		self.assertEqual(["stuck writing"], [watch.label for watch in rescued])
		self.assertFalse(healthy.watch.fired)


class TestTheWatchdogThread(unittest.TestCase):
	def setUp(self):
		resetStubs()

	def test_rescuesACallWithoutBeingAsked(self):
		freed = threading.Event()

		def cancel(driver, threadId):
			freed.set()
			return True

		guard = MemberGuard(cancel=cancel, interval=0.01)
		with guard.watching("fake writing", object(), 0.05) as watch:
			self.assertTrue(freed.wait(5), "the watchdog never rescued the call")
		self.assertTrue(watch.fired)

	def test_leavesAQuickCallAlone(self):
		cancelled = []
		guard = MemberGuard(cancel=lambda driver, threadId: cancelled.append(driver) or True, interval=0.01)
		for _ in range(20):
			with guard.watching("fake writing", object(), 5.0):
				pass
		self.assertEqual([], cancelled)


PIPE_ACCESS_OUTBOUND = 0x2
FILE_FLAG_OVERLAPPED = 0x40000000
GENERIC_READ = 0x80000000
OPEN_EXISTING = 3
ERROR_IO_PENDING = 997
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
PAYLOAD = 1 << 20

_names = itertools.count()


class OVERLAPPED(ctypes.Structure):
	"""As `hwIo` declares it, with no event: `GetOverlappedResult` then waits on the handle."""

	_fields_ = (
		("Internal", ctypes.c_void_p),
		("InternalHigh", ctypes.c_void_p),
		("Offset", wintypes.DWORD),
		("OffsetHigh", wintypes.DWORD),
		("hEvent", wintypes.HANDLE),
	)


class PipeDevice:
	"""The attributes of `hwIo.IoBase` the cancellation reads, over a real pipe handle."""

	def __init__(self, writeFile, readFile=None):
		self._writeFile = writeFile
		self._file = readFile if readFile is not None else writeFile
		self._writeOl = OVERLAPPED()


class PipeDriver:
	def __init__(self, device):
		self._dev = device


@unittest.skipUnless(sys.platform == "win32", "cancelling I/O is a Windows thing")
class TestCancellingRealIo(unittest.TestCase):
	def setUp(self):
		resetStubs()
		kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
		kernel32.CreateNamedPipeW.argtypes = (wintypes.LPCWSTR,) + (wintypes.DWORD,) * 6 + (ctypes.c_void_p,)
		kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
		kernel32.CreateFileW.argtypes = (
			wintypes.LPCWSTR,
			wintypes.DWORD,
			wintypes.DWORD,
			ctypes.c_void_p,
			wintypes.DWORD,
			wintypes.DWORD,
			wintypes.HANDLE,
		)
		kernel32.CreateFileW.restype = wintypes.HANDLE
		kernel32.WriteFile.argtypes = (
			wintypes.HANDLE,
			ctypes.c_void_p,
			wintypes.DWORD,
			ctypes.POINTER(wintypes.DWORD),
			ctypes.c_void_p,
		)
		kernel32.WriteFile.restype = wintypes.BOOL
		kernel32.GetOverlappedResult.argtypes = (
			wintypes.HANDLE,
			ctypes.c_void_p,
			ctypes.POINTER(wintypes.DWORD),
			wintypes.BOOL,
		)
		kernel32.GetOverlappedResult.restype = wintypes.BOOL
		kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
		kernel32.CloseHandle.restype = wintypes.BOOL
		self.kernel32 = kernel32
		name = f"\\\\.\\pipe\\brlMultilineMemberGuard-{os.getpid()}-{next(_names)}"
		self.writer = kernel32.CreateNamedPipeW(name, PIPE_ACCESS_OUTBOUND | FILE_FLAG_OVERLAPPED, 0, 1, 1, 1, 0, None)
		self.assertNotEqual(INVALID_HANDLE_VALUE, self.writer, ctypes.WinError(ctypes.get_last_error()))
		self.reader = kernel32.CreateFileW(name, GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)
		self.assertNotEqual(INVALID_HANDLE_VALUE, self.reader, ctypes.WinError(ctypes.get_last_error()))
		self.addCleanup(kernel32.CloseHandle, self.reader)
		self.addCleanup(kernel32.CloseHandle, self.writer)

	def stuckWrite(self, overlapped: OVERLAPPED, started: threading.Event, threadIds: list) -> None:
		"""`hwIo.IoBase.write`, transcribed: start the write, then wait for it without limit."""
		threadIds.append(threading.get_native_id())
		buffer = ctypes.create_string_buffer(PAYLOAD)
		written = wintypes.DWORD()
		if not self.kernel32.WriteFile(self.writer, buffer, PAYLOAD, None, ctypes.byref(overlapped)):
			if ctypes.get_last_error() != ERROR_IO_PENDING:
				started.set()
				return
		started.set()
		self.kernel32.GetOverlappedResult(self.writer, ctypes.byref(overlapped), ctypes.byref(written), True)

	def startStuckWrite(self, overlapped: OVERLAPPED) -> tuple[threading.Thread, int]:
		started = threading.Event()
		threadIds: list = []
		thread = threading.Thread(target=self.stuckWrite, args=(overlapped, started, threadIds), daemon=True)
		thread.start()
		self.assertTrue(started.wait(5))
		thread.join(0.2)
		self.assertTrue(thread.is_alive(), "the write was supposed to be stuck")
		return thread, threadIds[0]

	def test_freesAThreadStuckInAnHwIoWrite(self):
		device = PipeDevice(self.writer)
		thread, threadId = self.startStuckWrite(device._writeOl)
		self.assertTrue(memberGuard.cancelMemberIo(PipeDriver(device), threadId))
		thread.join(5)
		self.assertFalse(thread.is_alive(), "the cancelled write still has the thread")

	def test_freesAWriteMadeWithTheDriversOwnOverlapped(self):
		"""As the Monarch driver's bounded write makes them, which `_writeOl` does not match."""
		device = PipeDevice(self.writer)
		thread, threadId = self.startStuckWrite(OVERLAPPED())
		self.assertTrue(memberGuard.cancelMemberIo(PipeDriver(device), threadId))
		thread.join(5)
		self.assertFalse(thread.is_alive())

	def test_theWholeRescueFreesTheThread(self):
		device = PipeDevice(self.writer)
		gaveUp = threading.Event()
		guard = MemberGuard(interval=0.01)
		finished = threading.Event()

		def call():
			with guard.watching("pipe writing", PipeDriver(device), 0.1, onOverdue=gaveUp.set):
				self.stuckWrite(device._writeOl, threading.Event(), [])
			finished.set()

		threading.Thread(target=call, daemon=True).start()
		self.assertTrue(finished.wait(5), "the guard did not free the stuck write")
		self.assertTrue(gaveUp.is_set())

	def test_saysSoWhenThereIsNothingToCancel(self):
		self.assertFalse(memberGuard.cancelMemberIo(PipeDriver(PipeDevice(self.writer)), threading.get_native_id()))

	def test_aDriverWithNoDeviceIsNotAnError(self):
		self.assertFalse(memberGuard.cancelMemberIo(object(), threading.get_native_id()))


if __name__ == "__main__":
	unittest.main()
