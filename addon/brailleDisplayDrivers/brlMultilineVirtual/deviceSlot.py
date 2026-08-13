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

	def __init__(self, spec: DeviceSpec, driver, band: DeviceBand):
		"""
		:param spec: the configuration this member was opened from.
		:param driver: the live `BrailleDisplayDriver` instance.
		:param band: the rows of the composite this member occupies.
		"""
		self.spec = spec
		self.driver = driver
		self.band = band
		self.failed = False
		"""Set when the device has raised while displaying.

		A failed member is skipped rather than taking the whole virtual display down with
		it. NVDA's own `handleDisplayUnavailable` would fall back to no braille and lose
		both displays, which is the wrong answer when one of two is still working.
		"""
		self._lastCells: list[int] | None = None
		self._queuedWrite: list[int] | None = None
		self._writeLock = threading.Lock()

	@property
	def driverName(self) -> str:
		return self.spec.driverName

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
		"""Stop writing to this member.

		The band it occupies goes dark and stays that way. Rebuilding the geometry around a
		member that has gone belongs with the rest of the reconnection work, and doing it
		here, part way through a write, would be the wrong place for it.
		"""
		self.failed = True
		self.invalidate()

	def terminate(self, suppressDisplayClear: bool = False) -> None:
		"""Close this member.

		:param suppressDisplayClear: leave whatever is on the device rather than blanking
			it. NVDA sets this on the way to the secure desktop, where clearing the display
			is both pointless and slow.
		"""
		with self._writeLock:
			self._queuedWrite = None
		self.invalidate()
		if suppressDisplayClear:
			self.driver._suppressDisplayClear = True
		try:
			self.driver.terminate()
		except Exception:
			log.error(f"BrlMultiline: error terminating {self.driverName}", exc_info=True)
