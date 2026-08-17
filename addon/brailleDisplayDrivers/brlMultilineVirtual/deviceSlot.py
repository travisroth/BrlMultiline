# BrlMultiline: one member of the virtual display.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A physical display, its place in the composite, and the queue that writes to it.

This is a transcription of the scheduling half of `BrailleHandler._writeCells` and
`_bgThreadExecutor`, per device rather than per handler. The handler has exactly one queued
write and one lock, belonging to whichever display it owns; several members need one each.

Two departures from the handler are deliberate:

**Change detection.** The handler writes the whole display on every cursor blink, several
times a second. Fanning that out unfiltered would put constant traffic on a member showing
something static, which on a Bluetooth display is not free. A slot therefore drops a write
that would not change what the device is showing. This is not an optimisation to add later;
it is what makes a second display usable.

**No acknowledgement pacing.** Phase 0 established that the collision this was originally
meant to avoid cannot occur, and that what remains is a member never being told to wait.
See the acknowledgement section of `docs/design/virtual-display-plan.md`. Pacing is staged
until an ack driven member is shown to drop frames under sustained writing.

Imports of NVDA are kept to the log and the background I/O thread, so that the scheduling
can be tested with very little standing in for NVDA.
"""

from __future__ import annotations

import threading
from typing import Sequence

import hwIo
from logHandler import log

from .virtualLayout import DeviceBand, DeviceSpec


class DeviceSlot:
	"""One physical display within the virtual one."""

	def __init__(self, spec: DeviceSpec, driver, band: DeviceBand, onFailure=None):
		"""
		:param spec: the configuration this member was opened from.
		:param driver: the live `BrailleDisplayDriver` instance.
		:param band: the rows of the composite this member occupies.
		:param onFailure: called with this slot, once, when the member is given up on. The
			composite uses it to lay itself out again around what is left. Called from
			whichever thread noticed, which is why the composite marshals from there.
		"""
		self.spec = spec
		self.driver = driver
		self.band = band
		self.failed = False
		"""Set when the member has been given up on.

		A failed member is skipped rather than taking the whole virtual display down with
		it. NVDA's own `handleDisplayUnavailable` would fall back to no braille and lose
		both displays, which is the wrong answer when one of two is still working.
		"""
		self._onFailure = onFailure
		self._lastCells: list[int] | None = None
		self._queuedWrite: list[int] | None = None
		self._writeLock = threading.Lock()
		self._watched: tuple | None = None
		"""The device being watched, its own read error hook, and ours. See L{stopWatching}."""
		self.watchForDisconnect()

	@property
	def driverName(self) -> str:
		return self.spec.driverName

	def watchForDisconnect(self) -> None:
		"""Ask this member's device to say when it has gone, rather than waiting to find out.

		A write is the obvious way to discover a display has been unplugged, and it is the slow
		way: a member showing something that is not changing is not written to at all, so it can
		be gone for a long time before anything notices. The read side knows at once. When a
		Bluetooth display drops, the overlapped read completion fails immediately::

			hwIo.ioThread._internalCompletionRoutine
			OSError: [WinError 1167] The device is not connected.

		`hwIo.IoBase._ioDone` offers that error to `self._onReadError` before raising, so
		chaining onto it turns the failure into a notification. There is no other route: the
		exception it raises is caught and logged by the I/O thread and goes no further.

		Three cares, all of them about it being someone else's object:

		- It chains rather than replaces. `freedomScientific` supplies one of these, which
			handles a suspend induced broken pipe by restarting itself, and losing that would
			cost the user their display over a laptop lid.
		- The member is given up on only when the driver's own hook did not deal with the error.
			A driver that says it has handled it is restarting, not dying.
		- The return value is the driver's own, so the traceback in the log is unchanged. It is
			how this was found, and someone else will need it.

		Best effort throughout. `_dev` is a private attribute of somebody else's driver and not
		every driver has one, so a member without a reachable device simply keeps the slower
		detection. A driver that reopens its own device drops this hook with the old one, which
		is the same case.
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

		Only if ours is still the one there, for the reason every other restore in this add-on
		checks: putting something back over a replacement is how a monkey patch damages
		something other than itself.
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
		if not cells:
			return
		self._displayNow(cells)

	def _displayNow(self, cells: list[int]) -> None:
		"""Hand cells to the driver, marking the member failed if it raises."""
		try:
			self.driver.display(cells)
		except Exception:
			log.error(f"BrlMultiline: {self.driverName} failed while displaying, dropping it", exc_info=True)
			self.fail()

	def fail(self) -> None:
		"""Stop writing to this member, and tell the composite it has one fewer display.

		Once only, however many ways the same disconnection is noticed: a display coming apart
		will often fail its read and its next write within a few milliseconds of each other,
		and the composite must not lay itself out twice for one loss.

		The callback runs on whichever thread noticed — the I/O thread for a read failure, and
		either thread for a write — so what it must not do here is touch NVDA. The composite
		marshals to the main thread; see `BrailleDisplayDriver._memberFailed`.
		"""
		if self.failed:
			return
		self.failed = True
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

		:param suppressDisplayClear: leave whatever is on the device rather than blanking
			it. NVDA sets this on the way to the secure desktop, where clearing the display
			is both pointless and slow.
		"""
		with self._writeLock:
			self._queuedWrite = None
		self.invalidate()
		self.stopWatching()
		if suppressDisplayClear:
			self.driver._suppressDisplayClear = True
		try:
			self.driver.terminate()
		except Exception:
			log.error(f"BrlMultiline: error terminating {self.driverName}", exc_info=True)
