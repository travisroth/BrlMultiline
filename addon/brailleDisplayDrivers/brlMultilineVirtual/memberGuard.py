# BrlMultiline: noticing a member display that has stopped answering, and letting go of it.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A watch on every call into a member driver that can wait on its hardware.

The virtual display does not write to hardware; its members' drivers do, and most of them
are NVDA's. NVDA's `hwIo.IoBase.write` waits for an overlapped write with no limit, so a
display that stops taking writes never lets the calling thread go. On 2026-09-14 that was a
Monarch, and the add-on's own driver now bounds its writes. NVDA's drivers cannot be changed
from here, and a Focus that hangs the same way would do the same damage:

- A write from NVDA's I/O thread never returns, and that thread is shared. Every other
  member's writes queue behind it, and so do the read completions that deliver keys, so the
  whole of braille stops.
- A write from the main thread — a member blanking itself as it closes, or a constructor
  querying its display as the reconnect poll reopens it — freezes NVDA outright.

So each such call is made inside `MemberGuard.watching`. A watchdog thread looks at what is in
progress, and a call that has been running longer than its limit is rescued: the member is
given up on first, so nothing more is queued to it, and then its pending I/O is cancelled.
`hwIo.IoBase.write` ignores the result of the wait it was stuck in, so a cancelled write simply
returns and the thread is free again. The composite lays itself out around the lost display,
and the reconnect poll brings it back when it answers.

What cancelling cannot free is a driver stuck on something other than I/O on its device — a
lock, a loop. That is logged as an error, because the thread it holds is not coming back.

Imports of NVDA are kept to the log.
"""

from __future__ import annotations

import contextlib
import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable, Iterator

from logHandler import log

MEMBER_WRITE_TIMEOUT = 6.0
"""Seconds a member may spend writing, or closing, before it is taken to be stuck.

A braille write takes milliseconds. This is set above the Monarch driver's own limit — three
seconds for the write and one for its cancellation — so that a driver that bounds its own
writes always gets to handle its own failure first.
"""

MEMBER_OPEN_TIMEOUT = 15.0
"""Seconds a member's constructor may take. Longer, because opening legitimately waits on
hardware: a driver may try several ports and give each one time to answer."""

CHECK_INTERVAL = 0.5
"""How often the watchdog looks, while anything is being watched."""

THREAD_TERMINATE = 0x0001
_INVALID_HANDLES = {0, -1, ctypes.c_void_p(-1).value}


class Watch:
	"""One call in progress."""

	__slots__ = ("label", "driver", "timeout", "threadId", "started", "onOverdue", "fired")

	def __init__(self, label: str, driver, timeout: float, threadId: int, started: float, onOverdue):
		self.label = label
		self.driver = driver
		self.timeout = timeout
		self.threadId = threadId
		self.started = started
		self.onOverdue = onOverdue
		self.fired = False
		"""Whether this call was given up on. A caller whose call returns with this set got
		a result from a device that was cancelled underneath it, and must not trust it."""


class MemberGuard:
	"""Watches calls into member drivers, and rescues the ones that do not come back."""

	def __init__(
		self,
		clock: Callable[[], float] = time.monotonic,
		cancel: Callable[[object, int], bool] | None = None,
		runThread: bool = True,
		interval: float = CHECK_INTERVAL,
	):
		"""
		:param clock: the time source, replaceable so a test can make a call overdue.
		:param cancel: frees a stuck call, given the driver and the thread; `cancelMemberIo`
			by default.
		:param runThread: whether to start the watchdog thread. A test that calls L{check}
			itself says no.
		:param interval: how often the watchdog looks.
		"""
		self._clock = clock
		self._cancel = cancel if cancel is not None else cancelMemberIo
		self._runThread = runThread
		self._interval = interval
		self._condition = threading.Condition()
		self._watches: set[Watch] = set()
		self._thread: threading.Thread | None = None

	@contextlib.contextmanager
	def watching(self, label: str, driver, timeout: float, onOverdue=None) -> Iterator[Watch]:
		"""Watch a call into a member driver for as long as this block runs.

		:param label: what the call is, for the log: "freedomScientific writing".
		:param driver: the member driver, whose device is what gets cancelled.
		:param timeout: seconds the call may take.
		:param onOverdue: called, from the watchdog thread, when the call is given up on and
			before its I/O is cancelled. For giving the member up, so nothing more is queued
			to it as the thread comes free.
		:return: the watch, whose `fired` says afterwards whether the call was rescued.
		"""
		watch = Watch(label, driver, timeout, threading.get_native_id(), self._clock(), onOverdue)
		with self._condition:
			wasIdle = not self._watches
			self._watches.add(watch)
			if self._runThread:
				self._ensureThread()
				if wasIdle:
					self._condition.notify()
		try:
			yield watch
		finally:
			with self._condition:
				self._watches.discard(watch)

	@property
	def watches(self) -> tuple[Watch, ...]:
		"""The calls in progress, for tests and diagnostics."""
		with self._condition:
			return tuple(self._watches)

	def check(self) -> list[Watch]:
		"""Rescue every watched call that has run past its limit. Each is rescued once.

		:return: the calls rescued by this check.
		"""
		now = self._clock()
		with self._condition:
			overdue = [w for w in self._watches if not w.fired and now - w.started >= w.timeout]
			for watch in overdue:
				watch.fired = True
		for watch in overdue:
			self._rescue(watch, now - watch.started)
		return overdue

	def _rescue(self, watch: Watch, elapsed: float) -> None:
		log.warning(
			f"BrlMultiline: {watch.label} has not returned after {elapsed:.1f} seconds; "
			"giving the display up and cancelling its I/O",
		)
		if watch.onOverdue is not None:
			try:
				watch.onOverdue()
			except Exception:
				log.error(f"BrlMultiline: error giving up {watch.label}", exc_info=True)
		try:
			freed = self._cancel(watch.driver, watch.threadId)
		except Exception:
			log.error(f"BrlMultiline: error cancelling {watch.label}", exc_info=True)
			return
		if not freed:
			log.error(
				f"BrlMultiline: {watch.label} had no I/O to cancel, so the thread it holds is not "
				"coming back. Braille may not work again until NVDA is restarted.",
			)

	def _ensureThread(self) -> None:
		"""Start the watchdog if it is not running. Call with `_condition` held."""
		if self._thread is not None and self._thread.is_alive():
			return
		self._thread = threading.Thread(target=self._run, name="BrlMultiline member guard", daemon=True)
		self._thread.start()

	def _run(self) -> None:
		"""Sleep while nothing is watched; otherwise look every `interval`.

		Almost every watch lasts milliseconds, so the thread mostly waits on the condition and
		costs nothing. It is daemonic because it holds nothing and must not keep NVDA from
		exiting.
		"""
		while True:
			with self._condition:
				while not self._watches:
					self._condition.wait()
			time.sleep(self._interval)
			try:
				self.check()
			except Exception:
				log.error("BrlMultiline: error in the member guard", exc_info=True)


_kernel32 = None


def _api():
	""":return: kernel32 with this module's prototypes, bound on a private `WinDLL`."""
	global _kernel32
	if _kernel32 is None:
		kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
		kernel32.CancelIoEx.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
		kernel32.CancelIoEx.restype = wintypes.BOOL
		kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
		kernel32.OpenThread.restype = wintypes.HANDLE
		kernel32.CancelSynchronousIo.argtypes = (wintypes.HANDLE,)
		kernel32.CancelSynchronousIo.restype = wintypes.BOOL
		kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
		kernel32.CloseHandle.restype = wintypes.BOOL
		_kernel32 = kernel32
	return _kernel32


def _handleValue(handle) -> int | None:
	""":return: a usable handle as an integer, or None for a missing or invalid one."""
	value = getattr(handle, "value", handle)
	if not isinstance(value, int) or value in _INVALID_HANDLES:
		return None
	return value


def cancelMemberIo(driver, threadId: int) -> bool:
	"""Cancel what a stuck member call is waiting on.

	The device is `driver._dev`, as NVDA's drivers name it, with `hwIo.IoBase`'s handle and
	OVERLAPPED attributes. Private, and read defensively for that reason.

	- The pending write on `_writeOl` first, which is exactly what `IoBase.write` waits for.
	- Failing that, everything on the write handle. A driver that writes with its own
		OVERLAPPED, as the Monarch's does, is freed this way. It cancels the pending read too,
		which would matter for a member being kept; this one is being given up.
	- Failing that, everything on the read handle, where it differs.
	- And any synchronous I/O the thread is in, such as `HidD_SetOutputReport`.

	:param driver: the member driver.
	:param threadId: the native id of the thread making the call.
	:return: whether anything was cancelled.
	"""
	kernel32 = _api()
	cancelled = False
	device = getattr(driver, "_dev", None)
	writeFile = _handleValue(getattr(device, "_writeFile", None))
	readFile = _handleValue(getattr(device, "_file", None))
	writeOl = getattr(device, "_writeOl", None)
	if writeFile is not None:
		if isinstance(writeOl, ctypes.Structure):
			cancelled = bool(kernel32.CancelIoEx(writeFile, ctypes.addressof(writeOl)))
		if not cancelled:
			cancelled = bool(kernel32.CancelIoEx(writeFile, None))
	if not cancelled and readFile is not None and readFile != writeFile:
		cancelled = bool(kernel32.CancelIoEx(readFile, None))
	thread = kernel32.OpenThread(THREAD_TERMINATE, False, threadId)
	if thread:
		try:
			if kernel32.CancelSynchronousIo(thread):
				cancelled = True
		finally:
			kernel32.CloseHandle(thread)
	return cancelled


guard = MemberGuard()
"""The guard every member call is watched by. One is enough: the thread is shared, idle, and
knows nothing about which composite a member belongs to."""
