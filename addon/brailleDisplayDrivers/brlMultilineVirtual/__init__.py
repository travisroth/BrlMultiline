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

Input is handled in `gestures`. Members' keys already reach NVDA on their own, so the work
there is rebasing routing cell indexes onto the composite and answering the three things NVDA
looks up on `braille.handler.display` while resolving a gesture.

**Not done here, by design.** The composite is a rectangle as wide as its widest member, so a
narrower member has dead columns that reach no hardware, and NVDA flowing one buffer across
the whole rectangle loses text into them. Masking them is the add-on's job rather than the
driver's, because by the time cells arrive here they have already been laid out: the global
plugin builds a view with one panel per member and a blank panel over each member's dead
columns. With the plugin disabled, a mixed width arrangement reads gappily and loses whatever
lands past the narrower display's edge. The driver says so in the log when it starts.
"""

from __future__ import annotations

import collections
import time
import typing

import baseObject
import braille
import braille.display.driver
import extensionPoints
from braille.display import _getDisplayDriver
from logHandler import log

from . import ackPatch, gestures, handover, vdConfig
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


class BrailleDisplayDriver(braille.display.driver.BrailleDisplayDriver, baseObject.ScriptableObject):
	"""Presents several physical displays to NVDA as one.

	A `ScriptableObject` because NVDA offers `braille.handler.display` when it is looking for
	a display's own scripts, and that is now this driver rather than the member that has
	them. See `getScript`.
	"""

	name = "brlMultilineVirtual"
	# Translators: the name of the virtual braille display, shown in NVDA's display list.
	description = _("BrlMultiline: several displays as one")

	isThreadSafe = False
	"""False so that members decide their own threading, rather than having ours forced on them.

	Reporting True would have `BrailleHandler._writeCells` queue our `display` onto the
	background I/O thread, and a member that is not thread safe would then be called from
	it — the one thing `isThreadSafe = False` exists to promise will not happen. Reporting
	False puts the fan out on the main thread, where the only work is slicing an array and
	comparing it, and leaves each member free to be driven the way it asked for: a thread
	safe one still gets its actual I/O queued onto the background thread by its slot, and an
	unsafe one is called on the main thread as it requires.

	Both target displays happen to be thread safe, so this does not change their behaviour.
	It matters because a member list is a thing the user composes, and it should not be
	possible to compose one that is unsafe.
	"""

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

		# NVDA constructs this driver before terminating the one it is replacing, so a member
		# the user is switching away from is still held open at this point. It is taken over
		# as it stands rather than closed and reopened; see `handover` for why reopening a
		# display NVDA has just closed is a trap rather than a delay.
		adopted = handover.adoptConflictingDisplay(spec.driverName for spec in specs)
		if adopted:
			log.info(f"BrlMultiline: took the running {adopted[0]} over from NVDA for the switch")

		ackPatch.install()
		handover.installSwitchPatch()
		# A live view of the members' maps, so that a driver rewriting its own bindings —
		# which the Focus does when its wiz wheel action is cycled — is followed rather than
		# snapshotted.
		self.gestureMap = gestures.MemberGestureMap(self)
		try:
			self._openMembers(specs, adopted)
		except Exception:
			# Never leave half the members open behind a failed construction, and give back
			# anything adopted so that NVDA can close it in the ordinary way.
			self._closeMembers()
			if adopted:
				handover.releaseAdoption(adopted[1])
			handover.removeSwitchPatch()
			ackPatch.remove()
			raise
		gestures.install(self)

	def _openMembers(self, specs: list[DeviceSpec], adopted=None) -> None:
		"""Open what can be opened, lay it out, and report what the result is.

		A member that will not open is dropped rather than fatal, so that two configured
		displays with one of them switched off still gives the user the other one.

		Everything opened is closed again if the layout cannot be built, because until the
		slots exist there is nothing else holding these drivers, and a dropped driver keeps
		its port.

		:param specs: the members to open, in stacking order.
		:param adopted: an optional (name, driver) pair taken over from NVDA, used in place
			of opening that driver.
		"""
		opened: list[tuple[DeviceSpec, braille.display.driver.BrailleDisplayDriver]] = []
		try:
			for spec in specs:
				if adopted and spec.driverName == adopted[0]:
					driver = adopted[1]
				else:
					driver = _openDriver(spec)
				if driver is None:
					log.warning(f"BrlMultiline: could not open {spec.driverName}, continuing without it")
					continue
				if not _shapeOf(driver)[1]:
					# A display reporting no cells is not one, whatever it says about itself.
					# `noBraille` is the honest example; a display that connected but failed
					# to identify itself is the awkward one.
					log.warning(f"BrlMultiline: {spec.driverName} reports no cells, dropping it")
					_terminateQuietly(driver)
					continue
				opened.append((spec, driver))
			if not opened:
				raise RuntimeError("BrlMultiline virtual display could not open any of its displays")

			geometry = stackDevices([_shapeOf(driver) for _spec, driver in opened])
			self._slots = [
				DeviceSlot(spec, driver, band)
				for (spec, driver), band in zip(opened, geometry.bands, strict=True)
			]
		except Exception:
			# The slots are what `_closeMembers` closes, so anything opened before they were
			# built has to be closed here or it is simply lost, port and all.
			if not self._slots:
				for _spec, driver in opened:
					_terminateQuietly(driver)
			raise
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
			gestures.remove()
			self._closeMembers(suppressDisplayClear)
			handover.removeSwitchPatch()
			ackPatch.remove()

	def _closeMembers(self, suppressDisplayClear: bool = False) -> None:
		"""Terminate every member. Safe to call when none are open."""
		for slot in self._slots:
			slot.terminate(suppressDisplayClear)
			_retireDriver(slot.driver)
		self._slots = []

	@property
	def slots(self) -> tuple[DeviceSlot, ...]:
		"""The live members, in stacking order.

		The device map the add-on's view builder will read, so that a segment can be made to
		line up with a physical display and the dead columns can be masked.
		"""
		return tuple(self._slots)

	def slotForDriverName(self, driverName: str) -> DeviceSlot | None:
		"""Find the member a gesture came from.

		A gesture's `source` is its driver's name, and a driver name identifies a member
		uniquely because the configuration refuses to list one twice.

		:param driverName: the name to look for.
		:return: the member, or None if no member has that name.
		"""
		for slot in self._slots:
			if slot.driverName == driverName:
				return slot
		return None

	def getScript(self, gesture):
		"""Hand a gesture to the member that raised it, for that member's own scripts.

		NVDA offers `braille.handler.display` when looking for display specific scripts, and
		that is this driver now. Without this, a Focus running as a member would lose every
		command of its own.

		:param gesture: the gesture being resolved.
		:return: the bound script, or None.
		"""
		script = gestures.scriptForMember(self, gesture)
		if script is not None:
			return script
		return super().getScript(gesture)

	def _getModifierGestures(self, model=None):
		"""Chain every member's modifier gestures.

		Shadows the base class's classmethod deliberately: `BrailleDisplayGesture._get_script`
		calls this on the instance, and only the instance knows what the members are.

		:param model: the optional display model, passed through unchanged.
		"""
		return gestures.modifierGesturesForMembers(self, model)


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


_retiredDrivers: list[braille.display.driver.BrailleDisplayDriver] = []
"""Every member this driver has closed, kept alive for the rest of the session.

A deliberate, bounded leak, and it is working around a defect in NVDA rather than in this
add-on. `hwIo.base.IoBase.close` closes the device handle without clearing `self._file`, and
`IoBase.__del__` calls `close` again — so finalising a driver that was already terminated
closes its handle a second time, by which point Windows may have given that handle value to
something else entirely. A Phase 1 hardware run watched exactly that kill a freshly opened
display nine milliseconds after it was opened.

Holding a reference means the finaliser never runs while NVDA is up, so the second close
never happens. What leaks is a handful of inert Python objects — a closed driver has had its
receive callback cleared and its reads cancelled — one per member per display switch.

Remove this once `hwIo` clears its handle fields on close. See the top of `handover`.
"""


def _retireDriver(driver: braille.display.driver.BrailleDisplayDriver) -> None:
	"""Keep a closed driver alive, so that its finaliser cannot close a recycled handle.

	:param driver: the driver that has just been terminated.
	"""
	if driver is not None and driver not in _retiredDrivers:
		_retiredDrivers.append(driver)


def _terminateQuietly(driver: braille.display.driver.BrailleDisplayDriver, partial: bool = False) -> None:
	"""Close a driver that is being dropped rather than used, reporting a failure but not raising.

	:param driver: the driver to close.
	:param partial: whether this driver failed part way through construction. Such a driver
		may be missing anything its constructor had not reached, so `terminate` raising is
		unremarkable and is reported quietly. A display that is simply not plugged in takes
		this path on every attempt, and should not fill the log with errors.
	"""
	name = getattr(driver, "name", "?")
	try:
		driver.terminate()
	except Exception:
		if partial:
			log.debugWarning(f"BrlMultiline: {name} raised while releasing a failed attempt", exc_info=True)
		else:
			log.error(f"BrlMultiline: error terminating {name}", exc_info=True)
	finally:
		# Whether or not it closed cleanly, it must not be finalised. See `_retiredDrivers`.
		_retireDriver(driver)


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
		driver = None
		try:
			driver = driverClass.__new__(driverClass)
			extensionPoints.callWithSupportedKwargs(driver.__init__, port=spec.port)
			driver.initSettings()
		except Exception:
			# A constructor that raises may still have opened the device: both target
			# drivers assign `self._dev` before they are finished, and `initSettings` runs
			# after the device is open in every case. Their own failure paths close it, but
			# an unexpected error between opening and returning does not, and the instance
			# is about to be dropped where nothing else can reach it. So it is closed here,
			# best effort, before the next attempt — which would otherwise be competing with
			# the handle this one is still holding.
			if driver is not None:
				_terminateQuietly(driver, partial=True)
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
