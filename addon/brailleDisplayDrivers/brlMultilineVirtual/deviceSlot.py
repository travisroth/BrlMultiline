# BrlMultiline: one member of the virtual display.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A physical display, its place in the composite, and the queue that writes to it.

This is a transcription of the scheduling half of `BrailleHandler._writeCells` and
`_bgThreadExecutor`, per device rather than per handler. The handler has exactly one queued
write and one lock, belonging to whichever display it owns; several members need one each.

Two departures from the handler are deliberate:

**Change detection.** A write that would not change the device is dropped, since the handler
rewrites on every cursor blink and a static member would otherwise get constant traffic.

**No acknowledgement pacing.** See the acknowledgement section of
docs/design/virtual-display-plan.md.

Every call that can wait on hardware is watched by `memberGuard`. NVDA imports are kept to the
log and the I/O thread, for testing.
"""

from __future__ import annotations

import threading
from typing import Sequence

import hwIo
from logHandler import log

from . import memberGuard
from .virtualLayout import DeviceBand, DeviceSpec


class DeviceSlot:
	"""One physical display within the virtual one."""

	def __init__(self, spec: DeviceSpec, driver, band: DeviceBand, onFailure=None, guard=None):
		"""
		:param spec: the configuration this member was opened from.
		:param driver: the live `BrailleDisplayDriver` instance.
		:param band: the rows of the composite this member occupies.
		:param onFailure: called with this slot, once, when the member is given up on. The
			composite lays itself out again. Called from whichever thread noticed.
		:param guard: what watches calls into the driver; `memberGuard.guard` by default.
		"""
		self.spec = spec
		self.driver = driver
		self.band = band
		self.failed = False
		"""Set when the member has been given up on.

		A failed member is skipped, rather than NVDA's `handleDisplayUnavailable` losing all.
		"""
		self._onFailure = onFailure
		self._lastCells: list[int] | None = None
		self._queuedWrite: list[int] | None = None
		self._writeLock = threading.Lock()
		"""Guards the queued write, `failed`, and `_displaying`, and every move between them.

		One piece of state, so a member given up on never receives a queued frame, which on a
		stuck display would cost another `MEMBER_WRITE_TIMEOUT`.
		"""
		self._guard = guard if guard is not None else memberGuard.guard
		self._displaying = False
		"""Whether a call to the driver's `display` is in progress. See L{terminate}."""
		self._watched: tuple | None = None
		"""The device being watched, its own read error hook, and ours. See L{stopWatching}."""
		self.watchForDisconnect()

	@property
	def driverName(self) -> str:
		return self.spec.driverName

	def watchForDisconnect(self) -> None:
		"""Ask this member's device to say when it has gone, rather than waiting to find out.

		A static member is not written to, so the read side, which fails at once, is faster.
		`hwIo.IoBase._ioDone` offers the error to `self._onReadError` before raising. The hook
		chains onto any the driver has, such as `freedomScientific`'s restart; gives up only
		when that did not handle it; and returns the driver's own value. Best effort, through
		the private `_dev`.
		"""
		device = getattr(self.driver, "_dev", None)
		if device is None or not hasattr(device, "_onReadError"):
			log.debug(f"BrlMultiline: {self.driverName} has no device to watch for disconnection")
			return
		original = device._onReadError

		def onReadError(error: int) -> bool:
			handled = False
			try:
				if original is not None:
					handled = bool(original(error))
			except Exception:
				log.error(f"BrlMultiline: {self.driverName} raised handling a read error", exc_info=True)
			if not handled and not self.failed:
				log.warning(
					f"BrlMultiline: {self.driverName} stopped responding (error {error}), dropping it",
				)
				self.fail()
			return handled

		device._onReadError = onReadError
		self._watched = (device, original, onReadError)

	def stopWatching(self) -> None:
		"""Give the device its own read error hook back, for a member being closed.

		Only if ours is still the one there, as every restore here checks.
		"""
		if self._watched is None:
			return
		device, original, ours = self._watched
		self._watched = None
		try:
			if getattr(device, "_onReadError", None) is ours:
				device._onReadError = original
		except Exception:
			log.debugWarning(f"BrlMultiline: could not unwatch {self.driverName}", exc_info=True)

	def __repr__(self) -> str:
		return f"<DeviceSlot {self.driverName} rows {self.band.rowStart}:{self.band.rowEnd}>"

	def write(self, cells: Sequence[int]) -> bool:
		"""Send cells to this device, unless it is already showing them.

		:param cells: this device's own cell array.
		:return: whether anything was sent.
		"""
		if self.failed:
			return False
		cells = list(cells)
		if cells == self._lastCells:
			return False
		self._lastCells = cells
		self._dispatch(list(cells))
		return True

	def invalidate(self) -> None:
		"""Forget what this device is showing, so the next write is sent whatever it says.

		Needed whenever something other than this slot may have written to the device, such
		as a driver clearing itself while terminating.
		"""
		self._lastCells = None

	def _dispatch(self, cells: list[int]) -> None:
		"""Send now, or queue for the background thread, as the driver allows."""
		if not self.driver.isThreadSafe:
			self._displayNow(cells)
			return
		with self._writeLock:
			alreadyQueued = self._queuedWrite
			self._queuedWrite = cells
		# If a write was already queued we have just replaced its data, so there is nothing
		# further to arrange. Writes arriving while an earlier one is in progress collapse
		# into the last of them, as they do in the handler.
		if alreadyQueued is None:
			hwIo.bgThread.queueAsApc(self._bgExecutor)

	def _bgExecutor(self, param: int = 0) -> None:
		"""Write the queued cells. Runs as an APC on NVDA's background I/O thread."""
		with self._writeLock:
			cells = self._queuedWrite
			self._queuedWrite = None
			if self.failed:
				return
		if not cells:
			return
		self._displayNow(cells)

	def _displayNow(self, cells: list[int]) -> None:
		"""Hand cells to the driver, marking the member failed if it raises or never returns.

		A write that never returns is cancelled by the guard. `failed` is checked again under
		the lock, since the member may have failed after the frame left the queue.
		"""
		with self._writeLock:
			if self.failed:
				return
			self._displaying = True
		try:
			with self._guard.watching(
				f"{self.driverName} writing",
				self.driver,
				memberGuard.MEMBER_WRITE_TIMEOUT,
				onOverdue=self.fail,
			):
				self.driver.display(cells)
		except Exception:
			log.error(f"BrlMultiline: {self.driverName} failed while displaying, dropping it", exc_info=True)
			self.fail()
		finally:
			with self._writeLock:
				self._displaying = False

	def fail(self) -> None:
		"""Stop writing to this member, and tell the composite it has one fewer display.

		Once only, however the loss is noticed. The callback runs on the noticing thread; see
		`BrailleDisplayDriver._memberFailed`. A queued frame is dropped in the same step.
		"""
		with self._writeLock:
			if self.failed:
				return
			self.failed = True
			self._queuedWrite = None
		self.invalidate()
		self.stopWatching()
		if self._onFailure is None:
			return
		try:
			self._onFailure(self)
		except Exception:
			log.error(f"BrlMultiline: error reporting the loss of {self.driverName}", exc_info=True)

	def terminate(self, suppressDisplayClear: bool = False) -> None:
		"""Close this member.

		Never blanked when given up on, since the blank would fail too, nor while a write is in
		progress on the device's one write OVERLAPPED; closing the handle ends that write. The
		handle is always closed, and the close is watched.

		:param suppressDisplayClear: leave whatever is on the device rather than blanking
			it, as NVDA asks on the way to the secure desktop.
		"""
		with self._writeLock:
			self._queuedWrite = None
			busy = self._displaying
		self.invalidate()
		self.stopWatching()
		if suppressDisplayClear or self.failed or busy:
			self.driver._suppressDisplayClear = True
		try:
			with self._guard.watching(
				f"{self.driverName} closing",
				self.driver,
				memberGuard.MEMBER_WRITE_TIMEOUT,
			):
				self.driver.terminate()
		except Exception:
			log.error(f"BrlMultiline: error terminating {self.driverName}", exc_info=True)
