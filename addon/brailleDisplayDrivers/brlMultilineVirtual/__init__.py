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
from .virtualLayout import (
	DeviceBand,
	DeviceSpec,
	VirtualGeometry,
	deadColumnCount,
	sliceBandCells,
	stackDevices,
)

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

POLL_INTERVAL = 5.0
"""Seconds between looks for a display that is configured but not being driven.

Long enough that a display left switched off costs nothing worth measuring, short enough that
switching one on is answered while the user still has their hand on it.
"""

POLL_BACKOFF_LIMIT = 60.0
"""How far apart attempts on one member may grow.

A display that is off, or paired but out of range, can be listed by Windows and still refuse to
open. Trying it every five seconds for a working day is the wasteful case this bounds; a minute
is still soon enough that switching it on is noticed while the user is looking at it.
"""


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
		self._specs: list[DeviceSpec] = []
		self._nextAttempt: dict[str, float] = {}
		"""When to next try to open each member that is not being driven. See L{_pollForMembers}."""
		self._pollTimer = None
		self.numRows = 1
		self.numCols = 0
		self._installed = False
		"""Whether NVDA has finished putting this display in place. See `initSettings`."""

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
		self._specs = list(specs)
		"""Every configured member, whether or not it opened. What the reconnect poll works
		from: a display that was switched off at startup has no slot to be found through."""
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
				DeviceSlot(spec, driver, band, onFailure=self._memberFailed)
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

	def _startPolling(self) -> None:
		"""Start looking for members that are not here yet. Safe to call more than once."""
		if self._pollTimer is not None:
			return
		try:
			import wx

			self._pollTimer = wx.CallLater(int(POLL_INTERVAL * 1000), self._pollForMembers)
		except Exception:
			# No timer means no reconnection, and everything else goes on working. The one
			# place this happens is a test harness with no wx.
			log.debugWarning("BrlMultiline: could not start looking for displays", exc_info=True)

	def _stopPolling(self) -> None:
		"""Stop looking. Safe to call when nothing is running."""
		timer, self._pollTimer = self._pollTimer, None
		if timer is None:
			return
		try:
			timer.Stop()
		except Exception:
			log.debugWarning("BrlMultiline: could not stop looking for displays", exc_info=True)

	def _pollForMembers(self) -> None:
		"""Try to bring back a configured display that is not being driven.

		Runs on the main thread, which is where opening a display has to happen and also where
		it is felt: a driver's constructor talks to hardware and can take a moment. Three things
		keep that from being a stutter every few seconds.

		The presence check comes first. `bdDetect` is asked whether Windows can see anything
		this driver knows how to talk to, and a driver that is nowhere in the enumeration is
		not opened at all. It is a filter and not a proof — a paired Bluetooth device is often
		still listed while it is switched off — so the other two matter as much.

		One attempt per member per tick, rather than the three the startup path takes: the poll
		is the retry, and it is the retry Phase 0 asked for.

		And each failure pushes that member's next attempt further out, up to a minute, so a
		display left switched off costs almost nothing while a display being switched on is
		found within a few seconds.
		"""
		self._pollTimer = None
		try:
			self._reopenMissingMembers()
		except Exception:
			log.error("BrlMultiline: error looking for displays that are not here", exc_info=True)
		if self._installed:
			self._startPolling()

	def _missingSpecs(self) -> list[DeviceSpec]:
		""":return: the configured members that are not being driven, in stacking order."""
		driven = {slot.driverName for slot in self._slots if not slot.failed}
		return [spec for spec in self._specs if spec.driverName not in driven]

	def _reopenMissingMembers(self) -> None:
		"""Open what can be opened of what is missing, and lay the display out again if any was."""
		missing = self._missingSpecs()
		if not missing:
			return
		now = time.monotonic()
		regained = False
		for spec in missing:
			if now < self._nextAttempt.get(spec.driverName, 0.0):
				continue
			if not _isPresent(spec.driverName):
				self._deferAttempt(spec.driverName, now)
				continue
			driver = _openDriver(spec, attempts=1, quiet=True)
			if driver is None or not _shapeOf(driver)[1]:
				if driver is not None:
					_terminateQuietly(driver)
				self._deferAttempt(spec.driverName, now)
				continue
			log.info(f"BrlMultiline: {spec.driverName} is back")
			self._nextAttempt.pop(spec.driverName, None)
			self._admit(spec, driver)
			regained = True
		if regained:
			self._relayout()

	def _deferAttempt(self, driverName: str, now: float) -> None:
		"""Wait longer before trying this member again, up to L{POLL_BACKOFF_LIMIT}."""
		waited = max(POLL_INTERVAL, self._nextAttempt.get(driverName, 0.0) - now)
		self._nextAttempt[driverName] = now + min(waited * 2, POLL_BACKOFF_LIMIT)

	def _admit(self, spec: DeviceSpec, driver) -> None:
		"""Put a member that has just opened back into the stack, in its configured place.

		Its band is a placeholder: the shape is the driver's own, and where those rows sit is
		L{_relayout}'s answer once every member has been counted.

		:param spec: the member's configuration.
		:param driver: the freshly opened driver.
		"""
		numRows, numCols = _shapeOf(driver)
		band = DeviceBand(rowStart=0, numRows=numRows, numCols=numCols)
		slot = DeviceSlot(spec, driver, band, onFailure=self._memberFailed)
		order = [each.driverName for each in self._specs]
		for index, existing in enumerate(self._slots):
			if existing.driverName == spec.driverName:
				# A slot that failed and is now back. The old driver is dead; let go of it.
				self._retireSlot(existing)
				self._slots[index] = slot
				return
			if order.index(existing.driverName) > order.index(spec.driverName):
				self._slots.insert(index, slot)
				return
		self._slots.append(slot)

	def _retireSlot(self, slot: DeviceSlot) -> None:
		"""Let go of a member that has been replaced, without letting its death be fatal."""
		try:
			slot.terminate()
		except Exception:
			log.debugWarning(f"BrlMultiline: {slot.driverName} raised while being let go", exc_info=True)
		_retireDriver(slot.driver)

	def _memberFailed(self, slot: DeviceSlot) -> None:
		"""A member has been given up on. Lay the composite out again around the rest.

		Called from whichever thread noticed, which for a read failure is NVDA's I/O thread, so
		nothing here may touch NVDA. The work is handed to the main thread, where changing the
		geometry and letting the handler notice are both safe.

		:param slot: the member that has gone.
		"""
		log.warning(f"BrlMultiline: {slot.driverName} has gone; laying the display out again")
		self._onMainThread(self._relayout)

	@staticmethod
	def _onMainThread(work) -> None:
		"""Run something on the main thread, wherever this is called from.

		:param work: what to run, taking no arguments.
		"""
		try:
			import wx

			wx.CallAfter(work)
		except Exception:
			# Without wx there is no main thread to hand this to, which is the test harness
			# and nothing else. Doing it here is then both safe and the only option.
			log.debugWarning("BrlMultiline: no main thread to defer to; working here", exc_info=True)
			work()

	def _relayout(self) -> None:
		"""Rebuild the geometry from the members still being driven.

		A lost member leaves a hole otherwise: its band stays in the composite, so NVDA goes on
		laying text into rows that reach nothing, and if the segment following the focus was one
		of them the reader is left with a working display showing nothing at all. Taking the
		rows away is what puts the focus back on hardware that exists.

		NVDA is told by the ordinary route rather than a special one. `_get_displayDimensions`
		recomputes from this driver's `numRows` and `numCols` on every read and raises
		`displaySizeChanged` when the answer differs, so reading it once here is the whole
		notification; the add-on's plugin is already listening and rebuilds its segments.

		Every member being gone is deliberately not treated as no display. The geometry is left
		as it was, dark, so that a member coming back finds a composite to come back to — where
		handing NVDA `handleDisplayUnavailable` would fall back to no braille and, with
		automatic detection on, could hand the returning display straight to NVDA instead.
		"""
		if not self._installed:
			# Terminated between the loss being noticed and this running, or still being
			# constructed. Either way this is not the display NVDA is showing.
			return
		live = [slot for slot in self._slots if not slot.failed]
		if not live:
			log.warning("BrlMultiline: no display left to show anything on; waiting for one to return")
			return
		try:
			geometry = stackDevices([(slot.band.numRows, slot.band.numCols) for slot in live])
		except ValueError:
			log.error("BrlMultiline: could not lay out the displays that are left", exc_info=True)
			return
		for slot, band in zip(live, geometry.bands, strict=True):
			if slot.band != band:
				slot.band = band
				# Its cells mean something different now, so what it last showed says nothing
				# about what it should show next.
				slot.invalidate()
		if (self.numRows, self.numCols) == (geometry.numRows, geometry.numCols):
			return
		self.numRows = geometry.numRows
		self.numCols = geometry.numCols
		self._describe(geometry)
		self._announceGeometry()

	@staticmethod
	def _announceGeometry() -> None:
		"""Let the handler notice that this display is a different size.

		One read of `displayDimensions` does it: the handler recomputes it from the driver
		every time and raises `displaySizeChanged` itself when the value has changed.
		"""
		try:
			handler = braille.handler
			if handler is not None and handler.display is not None:
				handler.displayDimensions  # noqa: B018 - read for its side effect.
		except Exception:
			log.error("BrlMultiline: could not tell NVDA the display had changed size", exc_info=True)

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

	def initSettings(self) -> None:
		"""Note that NVDA has finished the switch, as well as reading the settings.

		`_switchDisplay` calls this last, after the incoming driver is constructed and the
		outgoing one terminated, so it is the earliest honest answer to "is this display in
		place". Object identity is not, and the case that proves it is NVDA reselecting a
		display that is already in use: `sameDisplayReInit` terminates and reconstructs *this
		same instance*, so `braille.handler.display is self` stays true throughout, while the
		members, the bands and the geometry are all being replaced underneath it.

		Reopening the composite after its member list is edited takes exactly that path. A
		routing key pressed during it would otherwise be rebased onto the new bands and routed
		against the arrangement built for the old ones.
		"""
		super().initSettings()
		self._installed = True
		# Only now: until NVDA has finished the switch there is nothing for a member that
		# arrives to join, and `_relayout` would refuse to do anything with it anyway.
		self._startPolling()

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
		# First, so that nothing treats this display as NVDA's while it is being taken apart.
		# On the `sameDisplayReInit` path this instance is about to be reconstructed rather
		# than replaced, so identity says nothing; see `initSettings`.
		self._installed = False
		self._stopPolling()
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

	# Member settings
	#
	# NVDA's Braille settings dialog builds its controls from `supportedSettings` on the
	# display in use, which is this driver. Left alone, that means selecting the composite
	# hides every setting the displays behind it have — dot firmness, a Focus's wiz wheel
	# action — with no way to reach them short of selecting that display on its own.
	#
	# So the members' settings are offered as this driver's own, under names that say which
	# display each belongs to, and reads and writes are passed through to the member. What is
	# deliberately not passed through is storage: each copy is marked `useConfig = False`, so
	# the composite keeps none of them, and the value is saved by the member itself into the
	# section it reads when it is used alone. One display, one place its settings live,
	# whichever way it is being driven.

	def _get_supportedSettings(self):
		""":return: every setting of every member, named for the display it belongs to."""
		import copy

		settings = []
		proxies = {}
		for slot in self._slots:
			if slot.failed:
				continue
			try:
				own = list(slot.driver.supportedSettings)
			except Exception:
				log.debugWarning(f"BrlMultiline: could not read {slot.driverName}'s settings", exc_info=True)
				continue
			for setting in own:
				copied = copy.copy(setting)
				copied.id = f"{slot.driverName}_{setting.id}"
				copied.useConfig = False
				label = getattr(slot.driver, "description", slot.driverName)
				copied.displayName = f"{label}: {setting.displayName}"
				copied.displayNameWithAccelerator = f"{label}: {setting.displayNameWithAccelerator}"
				settings.append(copied)
				proxies[copied.id] = (slot, setting.id)
				# The dialog asks for a choice setting's options under this name, and
				# `capitalize` lowercases everything after the first letter, so the name is
				# not one that can be guessed from the id later.
				proxies[f"available{copied.id.capitalize()}s"] = (
					slot,
					f"available{setting.id.capitalize()}s",
				)
		self._settingProxies = proxies
		return settings

	def _proxyFor(self, name: str):
		""":return: the member and attribute a settings name stands for, or None.

		:param name: the attribute being read or written.
		"""
		if name.startswith("_"):
			# Nothing private is a setting, and looking one up here would re-enter
			# `supportedSettings` while it is part way through building the map.
			return None
		try:
			proxies = object.__getattribute__(self, "_settingProxies")
		except AttributeError:
			proxies = None
		if proxies is None or name not in proxies:
			if not object.__getattribute__(self, "_slots"):
				return None
			# Built on demand, since a caller may reach for a setting before anything has
			# asked this driver what settings it has.
			self.supportedSettings  # noqa: B018 - read to rebuild the map.
			proxies = object.__getattribute__(self, "_settingProxies")
		return proxies.get(name)

	def __getattr__(self, name: str):
		proxy = self._proxyFor(name) if not name.startswith("_") else None
		if proxy is None:
			raise AttributeError(name)
		slot, attribute = proxy
		return getattr(slot.driver, attribute)

	def __setattr__(self, name: str, value) -> None:
		proxy = self._proxyFor(name) if not name.startswith("_") else None
		if proxy is None:
			super().__setattr__(name, value)
			return
		slot, attribute = proxy
		setattr(slot.driver, attribute, value)

	def saveSettings(self) -> None:
		"""Save this display's settings, and let each member save its own.

		The members' values are theirs to keep: they are stored under the member's own name,
		which is where that display reads them when NVDA drives it directly.
		"""
		super().saveSettings()
		for slot in self._slots:
			if slot.failed:
				continue
			try:
				slot.driver.saveSettings()
			except Exception:
				log.error(f"BrlMultiline: could not save {slot.driverName}'s settings", exc_info=True)

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


def _isPresent(driverName: str) -> bool:
	"""Ask Windows whether anything this driver knows how to talk to is there.

	A filter rather than a proof, and it is worth being clear which way it errs. A paired
	Bluetooth display is often still enumerated while it is switched off, so True means "worth
	trying" and not "connected". False is the trustworthy answer, and it is the one that saves
	the work: opening a driver talks to hardware on the main thread.

	A driver `bdDetect` has no data for — one driven entirely by a port the user names — cannot
	be ruled out this way, so it is always tried.

	:param driverName: the driver to look for.
	:return: whether to attempt it.
	"""
	try:
		import bdDetect
	except Exception:
		return True
	found = False
	for lookup in (bdDetect.getConnectedUsbDevicesForDriver, bdDetect.getPossibleBluetoothDevicesForDriver):
		try:
			if next(iter(lookup(driverName)), None) is not None:
				return True
			found = True
		except LookupError:
			# No detection data of this kind for this driver, which says nothing either way.
			continue
		except Exception:
			log.debugWarning(f"BrlMultiline: could not look for {driverName}", exc_info=True)
			return True
	# Something answered, and nothing was there. Only then is absence worth believing.
	return not found


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


def _openDriver(
	spec: DeviceSpec,
	attempts: int | None = None,
	quiet: bool = False,
) -> braille.display.driver.BrailleDisplayDriver | None:
	"""Construct one member, retrying a device that is present but not yet openable.

	Construction follows `BrailleHandler._setDisplay` exactly — `__new__`, `__init__` through
	`callWithSupportedKwargs` so that a driver taking no port still works, then
	`initSettings`. Phase 0 established that nothing about that path is privileged.

	:param spec: the member to open.
	:param attempts: how many times to try, or None for L{OPEN_ATTEMPTS}. The reconnect poll
		passes one: it is itself the retry, and sleeping between attempts on the main thread is
		affordable once at startup and not every few seconds afterwards.
	:param quiet: report a failure at debug level rather than as an error. For the reconnect
		poll, where a display simply not being there is the ordinary case, and an error every
		few seconds for a display left switched off is noise in the one file a user is asked
		to send when something is wrong.
	:return: the live driver, or None if it could not be opened.
	"""
	attempts = OPEN_ATTEMPTS if attempts is None else attempts
	try:
		driverClass = _getDisplayDriver(spec.driverName)
	except ImportError:
		log.error(f"BrlMultiline: no braille display driver named {spec.driverName!r}")
		return None

	for attempt in range(1, attempts + 1):
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
			if attempt == attempts:
				report = log.debugWarning if quiet else log.error
				report(
					f"BrlMultiline: {spec.driverName} did not open after {attempts} attempts",
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
