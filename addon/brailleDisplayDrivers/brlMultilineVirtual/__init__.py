# BrlMultiline: a braille display driver that is several braille displays.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""One display as far as NVDA is concerned, several displays in fact.

NVDA drives one braille display at a time, and almost all of that assumption lives in
`BrailleHandler`, which talks to hardware through a narrow interface: geometry, one flat
row major cell array, a few write scheduling flags, and terminate. This driver implements
that interface by owning a list of other drivers, so NVDA's ordinary driver loading,
buffers and cell writing all run untouched above it.

Members stack vertically. See `virtualLayout` for the geometry and for what dead columns
are; see `deviceSlot` for how writes are scheduled and why unchanged frames are dropped;
see `ackPatch` for the one place NVDA has to be adjusted, and for the careful statement of
what that patch is not for.

**Not yet done here, by design.** Input from members reaches NVDA already — Phase 0
confirmed that gestures from a display NVDA does not own arrive with their own driver's
identifiers, so existing gesture maps keep working — but three things are still wrong until
the next phase:

- Routing keys carry cell indexes relative to their own device, so a press on the second
  display routes to the wrong character. `virtualLayout.deviceCellIndexToVirtual` is the
  arithmetic, written and tested, and simply not wired up yet.
- `scriptHandler` reads `braille.handler.display.gestureMap`, which here is one map and
  should be the members' maps merged.
- A member that is a `ScriptableObject`, such as `freedomScientific`, loses its own scripts,
  because NVDA offers `braille.handler.display` for those and that is now this driver.

So this phase is about output. Both displays showing the halves of one buffer, with NVDA
unaware, is the whole of what it claims.
"""

from __future__ import annotations

import collections
import time
import typing

import braille
import braille.display.driver
import extensionPoints
from braille.display import _getDisplayDriver
from logHandler import log

from . import ackPatch, vdConfig
from .deviceSlot import DeviceSlot
from .virtualLayout import DeviceSpec, VirtualGeometry, deadColumnCount, sliceBandCells, stackDevices

try:
	import addonHandler

	addonHandler.initTranslation()
except Exception:
	# Translation is a nicety; failing to set it up must not cost the user their braille.
	log.debugWarning("BrlMultiline: could not initialise translations", exc_info=True)

OPEN_ATTEMPTS = 3
"""How many times to try opening a member before giving up on it.

Phase 0 found that a display NVDA had released moments earlier refused to open twice, while
Windows still listed it and the driver still had a port to try, and then opened normally a
short time later. Presence in the device enumeration does not imply openability, and the
handover from NVDA owning a display to this driver owning it is exactly when that bites.
"""

OPEN_RETRY_DELAY = 0.3
"""Seconds between attempts. Short because this runs on the main thread during startup;
three attempts cost at most six tenths of a second per member that never appears."""


class BrailleDisplayDriver(braille.display.driver.BrailleDisplayDriver):
	"""Presents several physical displays to NVDA as one."""

	name = "brlMultilineVirtual"
	# Translators: the name of the virtual braille display, shown in NVDA's display list.
	description = _("BrlMultiline: several displays as one")

	isThreadSafe = True
	receivesAckPackets = False
	"""False deliberately, and it is what makes members safe.

	The handler arms its single acknowledgement timer only for the display it owns. Reporting
	False here means it never arms it at all, so a member acknowledging a packet cannot
	disturb anything. See `ackPatch`.
	"""

	@classmethod
	def check(cls) -> bool:
		"""Always available.

		Deliberately not "available only when members are configured". The members are
		chosen in the add-on's own settings, and a driver that hid itself until configured
		could never be selected in order to be configured.
		"""
		return True

	@classmethod
	def getPossiblePorts(cls) -> typing.OrderedDict[str, str]:
		"""No ports. NVDA's port control has no meaning here; members carry their own."""
		return collections.OrderedDict()

	def __init__(self, port: str | None = None):
		"""Open every configured member and lay them out.

		:param port: accepted and ignored, so that NVDA's `_setDisplay` can pass one.
		:raise RuntimeError: if nothing is configured, the configuration is unusable, or no
			member could be opened. Raising is the contract every display driver has for
			"there is no display here", and leaves NVDA to fall back to no braille and tell
			the user.
		"""
		super().__init__()
		self._slots: list[DeviceSlot] = []
		self.numRows = 1
		self.numCols = 0

		try:
			specs = vdConfig.getDevices()
		except ValueError as error:
			raise RuntimeError(f"BrlMultiline virtual display is misconfigured: {error}") from error
		if not specs:
			raise RuntimeError(
				"BrlMultiline virtual display has no displays configured. "
				"Choose them in NVDA menu, Preferences, Settings, Braille Multiline.",
			)

		ackPatch.install()
		try:
			self._openMembers(specs)
		except Exception:
			# Never leave half the members open behind a failed construction.
			self._closeMembers()
			ackPatch.remove()
			raise

	def _openMembers(self, specs: list[DeviceSpec]) -> None:
		"""Open what can be opened, lay it out, and report what the result is.

		A member that will not open is dropped rather than fatal, so that two configured
		displays with one of them switched off still gives the user the other one.
		"""
		opened: list[tuple[DeviceSpec, braille.display.driver.BrailleDisplayDriver]] = []
		for spec in specs:
			driver = _openDriver(spec)
			if driver is None:
				log.warning(f"BrlMultiline: could not open {spec.driverName}, continuing without it")
				continue
			opened.append((spec, driver))
		if not opened:
			raise RuntimeError("BrlMultiline virtual display could not open any of its displays")

		geometry = stackDevices([_shapeOf(driver) for _spec, driver in opened])
		self._slots = [
			DeviceSlot(spec, driver, band)
			for (spec, driver), band in zip(opened, geometry.bands, strict=True)
		]
		self.numRows = geometry.numRows
		self.numCols = geometry.numCols
		self._describe(geometry)

	def _describe(self, geometry: VirtualGeometry) -> None:
		"""Log what was built, including the one thing that will otherwise mystify."""
		for slot in self._slots:
			log.info(
				f"BrlMultiline: {slot.driverName} occupies rows "
				f"{slot.band.rowStart} to {slot.band.rowEnd - 1}, {slot.band.numCols} cells wide",
			)
		log.info(
			f"BrlMultiline virtual display: {geometry.numRows} rows of {geometry.numCols}, "
			f"{len(self._slots)} display(s)",
		)
		dead = deadColumnCount(geometry)
		if dead:
			log.warning(
				f"BrlMultiline: {dead} cells of the virtual display reach no hardware, because the "
				"displays are not the same width. Content flowed into them will not be shown. "
				"Divide the display into segments that match the displays.",
			)

	def display(self, cells: list[int]) -> None:
		"""Fan one composite cell array out to the members.

		NVDA pads to the handler's display size before calling this, but the array is
		normalised again rather than trusted: a short write here would raise out of a
		background thread, and the cost of checking is a length comparison.

		:param cells: the composite array, row major over the whole rectangle.
		"""
		total = self.numCells
		if not total:
			return
		if len(cells) != total:
			cells = list(cells[:total])
			cells.extend([0] * (total - len(cells)))
		for slot in self._slots:
			if slot.failed:
				continue
			slot.write(sliceBandCells(cells, self.numCols, slot.band))

	def terminate(self) -> None:
		"""Close every member, then put NVDA's acknowledgement handling back.

		`_suppressDisplayClear` is read before delegating, because the base class consumes
		it, and is passed on to the members so that a switch to the secure desktop does not
		spend time blanking displays that are about to be closed anyway.
		"""
		suppressDisplayClear = bool(getattr(self, "_suppressDisplayClear", False))
		try:
			# Blanks the display through `display` above, unless suppressed, then saves
			# settings. Members are still open at this point, which is what makes it work.
			super().terminate()
		finally:
			self._closeMembers(suppressDisplayClear)
			ackPatch.remove()

	def _closeMembers(self, suppressDisplayClear: bool = False) -> None:
		"""Terminate every member. Safe to call when none are open."""
		for slot in self._slots:
			slot.terminate(suppressDisplayClear)
		self._slots = []

	@property
	def slots(self) -> tuple[DeviceSlot, ...]:
		"""The live members, in stacking order.

		The device map the add-on's view builder will read, so that a segment can be made to
		line up with a physical display and the dead columns can be masked.
		"""
		return tuple(self._slots)


def _shapeOf(driver: braille.display.driver.BrailleDisplayDriver) -> tuple[int, int]:
	"""Read a driver's geometry the way `BrailleHandler` reads it.

	A single row driver may report its size through `numCells` and leave `numCols` alone, so
	the handler prefers `numCells` in that case. Mirrored here rather than reinvented.

	:param driver: the driver.
	:return: its (numRows, numCols).
	"""
	numRows = driver.numRows
	numCols = driver.numCols if numRows > 1 else driver.numCells
	return numRows, numCols


def _openDriver(spec: DeviceSpec) -> braille.display.driver.BrailleDisplayDriver | None:
	"""Construct one member, retrying a device that is present but not yet openable.

	Construction follows `BrailleHandler._setDisplay` exactly — `__new__`, `__init__` through
	`callWithSupportedKwargs` so that a driver taking no port still works, then
	`initSettings`. Phase 0 established that nothing about that path is privileged.

	:param spec: the member to open.
	:return: the live driver, or None if it could not be opened.
	"""
	try:
		driverClass = _getDisplayDriver(spec.driverName)
	except ImportError:
		log.error(f"BrlMultiline: no braille display driver named {spec.driverName!r}")
		return None

	for attempt in range(1, OPEN_ATTEMPTS + 1):
		try:
			driver = driverClass.__new__(driverClass)
			extensionPoints.callWithSupportedKwargs(driver.__init__, port=spec.port)
			driver.initSettings()
		except Exception:
			if attempt == OPEN_ATTEMPTS:
				log.error(
					f"BrlMultiline: {spec.driverName} did not open after {OPEN_ATTEMPTS} attempts",
					exc_info=True,
				)
				return None
			log.debugWarning(
				f"BrlMultiline: {spec.driverName} did not open on attempt {attempt}, retrying",
				exc_info=True,
			)
			time.sleep(OPEN_RETRY_DELAY)
		else:
			log.debug(f"BrlMultiline: opened {spec.driverName} on attempt {attempt}")
			return driver
	return None
