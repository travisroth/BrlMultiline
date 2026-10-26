# BrlMultiline: a braille display driver that is several braille displays.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""One display as far as NVDA is concerned, several displays in fact.

This driver implements `BrailleHandler`'s narrow driver interface by owning a list of other
drivers, so NVDA's driver loading, buffers and cell writing run untouched above it. See
docs/design/virtual-display-plan.md.

Members stack vertically. See `virtualLayout` for the geometry and for what dead columns
are; see `deviceSlot` for how writes are scheduled and why unchanged frames are dropped;
see `ackPatch` for the one place NVDA has to be adjusted, and for the careful statement of
what that patch is not for.

Input is handled in `gestures`. Members' keys already reach NVDA on their own, so the work
there is rebasing routing cell indexes onto the composite and answering the three things NVDA
looks up on `braille.handler.display` while resolving a gesture.

Masking a narrower member's dead columns is the global plugin's job, not this driver's, since
cells arrive here already laid out. Without the plugin, text is lost into them; the log says so.
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

from . import ackPatch, events, gestures, handover, memberGuard, vdConfig
from .deviceSlot import DeviceSlot
from .virtualLayout import (
	DEFAULT_PORT,
	DeviceBand,
	DeviceSpec,
	VirtualGeometry,
	deadColumnCount,
	memberShape,
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

A display NVDA has just released can refuse to open for a moment. See "Phase 0 results" in
docs/design/virtual-display-plan.md.
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

Bounds the cost of a display left off, while still noticing it switched on within a minute.
"""


class BrailleDisplayDriver(braille.display.driver.BrailleDisplayDriver, baseObject.ScriptableObject):
	"""Presents several physical displays to NVDA as one.

	A `ScriptableObject`, since NVDA looks for display scripts here. See `getScript`.
	"""

	name = "brlMultilineVirtual"
	# Translators: the name of the virtual braille display, shown in NVDA's display list.
	description = _("BrlMultiline: several displays as one")

	isThreadSafe = False
	"""False so that members decide their own threading, rather than having ours forced on them.

	True would call an unsafe member from the I/O thread. False fans out on the main thread,
	and each slot queues a thread safe member's I/O onto the background thread itself.
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

		Even unconfigured, or it could never be selected in order to be configured.
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
			member could be opened, the driver contract for "no display here".
		"""
		super().__init__()
		self._slots: list[DeviceSlot] = []
		self._specs: list[DeviceSpec] = []
		self._nextAttempt: dict[str, float] = {}
		"""When to next try to open each member that is not being driven. See L{_pollForMembers}."""
		self._retryDelays: dict[str, float] = {}
		"""How long the last wait for each of those was, which is what the next one doubles."""
		self._pollTimer = None
		self._settingNames: set[str] = set()
		"""Every name that has stood for a member's setting, whether or not it does now.

		A write to one while its display is away is dropped, since storing it on this driver
		would shadow the member for good once it came back.
		"""
		self._settingProxies: dict = {}
		self._reportedDeadColumns = 0
		"""The dead column count last warned about, so a reconnect does not warn again."""
		self._membership: tuple[str, ...] = ()
		"""The members being driven, as of the last layout. What decides whether the arrangement
		above this driver has to be built again, since its segments are named after displays."""
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

		A member that will not open is dropped rather than fatal; one Windows cannot see is not
		attempted (see `_isPresent`). Everything opened is closed again if the layout cannot be
		built, or a dropped driver keeps its port.

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
				elif not _isPresent(spec):
					# Nothing this driver could talk to is in Windows' enumeration, so opening
					# it would be three constructor failures and six tenths of a second spent
					# on the main thread during startup, for an answer already known. Said at
					# info level because a display the user switched off is not a fault, and
					# the reconnect poll will take it the moment it appears.
					log.info(
						f"BrlMultiline: {spec.driverName} is not here; "
						"continuing without it and watching for it",
					)
					continue
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

			geometry = stackDevices([_shapeOf(driver, spec) for spec, driver in opened])
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
		self._membership = tuple(slot.driverName for slot in self._slots)
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

		On the main thread, where opening is felt, so: a presence check first (see
		`_isPresent`), one attempt per member per tick, and a growing wait after each failure.
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
			if not _isPresent(spec):
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
			self._retryDelays.pop(spec.driverName, None)
			self._admit(spec, driver)
			regained = True
		if regained:
			self._relayout()

	def _deferAttempt(self, driverName: str, now: float) -> None:
		"""Wait longer before trying this member again, up to L{POLL_BACKOFF_LIMIT}.

		The delay is remembered, since the deadline has always passed when this is called.

		:param driverName: the member that is not there.
		:param now: the current monotonic time.
		"""
		delay = min(self._retryDelays.get(driverName, POLL_INTERVAL) * 2, POLL_BACKOFF_LIMIT)
		self._retryDelays[driverName] = delay
		self._nextAttempt[driverName] = now + delay

	def _admit(self, spec: DeviceSpec, driver) -> None:
		"""Put a member that has just opened back into the stack, in its configured place.

		Its band is a placeholder: the shape is the driver's own, and where those rows sit is
		L{_relayout}'s answer once every member has been counted.

		:param spec: the member's configuration.
		:param driver: the freshly opened driver.
		"""
		numRows, numCols = _shapeOf(driver, spec)
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

		Called from any thread, so the work is handed to the main thread.

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

		Otherwise a lost member's rows stay, and the focus may be on hardware that is gone.
		Reading `_get_displayDimensions` once raises `displaySizeChanged`, which the plugin
		answers. Every member gone leaves the geometry as it was, dark, so a member coming back
		finds a composite rather than NVDA taking the display.
		"""
		if not self._installed:
			# Terminated between the loss being noticed and this running, or still being
			# constructed. Either way this is not the display NVDA is showing.
			return
		live = [slot for slot in self._slots if not slot.failed]
		membership = tuple(slot.driverName for slot in live)
		if not live:
			log.warning("BrlMultiline: no display left to show anything on; waiting for one to return")
			changed = membership != self._membership
			self._membership = membership
			if changed:
				# Said even though there is nothing to show it on, so that the notification
				# means what it says: the set of displays being driven has changed. The
				# geometry is kept, so there is nothing to write and nothing to write it to.
				self._announceMembership(write=False)
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
		changedMembers = membership != self._membership
		changedSize = (self.numRows, self.numCols) != (geometry.numRows, geometry.numCols)
		self._membership = membership
		if not (changedMembers or changedSize):
			return
		self.numRows = geometry.numRows
		self.numCols = geometry.numCols
		if changedSize:
			self._describe(geometry)
			self._announceGeometry()
		if changedMembers:
			# Separately, and this is the case a size alone cannot carry: a display lost and
			# reconnected leaves the composite exactly as big as it was, while the segments
			# above it are named after the displays and have to be built again.
			self._announceMembership()

	def _announceMembership(self, write: bool = True) -> None:
		"""Say that the displays behind this one have changed, and write to what is there now.

		The notification lets the plugin rebuild its segments; the write puts current braille on
		a member that has just opened. The write is queued behind the handlers, so it lands after
		the rebuild rather than slicing the old arrangement into the new bands.

		:param write: whether there is anything to write to. False when no member is left.
		"""
		try:
			events.membersChanged.notify(display=self)
		except Exception:
			log.error("BrlMultiline: error announcing the displays behind this one", exc_info=True)
		if write:
			self._onMainThread(self._writeCurrentCells)

	def _writeCurrentCells(self) -> None:
		"""Put whatever braille is current onto the displays that are here."""
		try:
			handler = braille.handler
			if handler is not None and handler.display is self:
				handler.update()
		except Exception:
			log.error("BrlMultiline: could not write to the displays that are here", exc_info=True)

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
		"""Log what was built, including the one thing that will otherwise mystify.

		Only the members in the layout, since a member given up on keeps its old slot and band.
		Those away get a line of their own.
		"""
		live = [slot for slot in self._slots if not slot.failed]
		for slot in live:
			log.info(
				f"BrlMultiline: {slot.driverName} occupies rows "
				f"{slot.band.rowStart} to {slot.band.rowEnd - 1}, {slot.band.numCols} cells wide",
			)
		log.info(
			f"BrlMultiline virtual display: {geometry.numRows} rows of {geometry.numCols}, "
			f"{len(live)} display(s)",
		)
		away = [name for name in self.configuredMembers if name not in {slot.driverName for slot in live}]
		if away:
			log.info(f"BrlMultiline: configured and not in the display: {away}")
		dead = deadColumnCount(geometry)
		if dead and dead != self._reportedDeadColumns:
			# Once per arrangement, not per layout. Not cleared when there are no dead columns,
			# or it would warn again each time the narrow display came back.
			log.warning(
				f"BrlMultiline: {dead} cells of the virtual display reach no hardware, because the "
				"displays are not the same width. The add-on's own plugin masks them, arranging "
				"one segment per display; without it, content flowed into them is lost.",
			)
			self._reportedDeadColumns = dead

	def initSettings(self) -> None:
		"""Note that NVDA has finished the switch, as well as reading the settings.

		`_switchDisplay` calls this last, so it is the earliest true answer to "is this display
		in place". Identity is not: `sameDisplayReInit` reconstructs this same instance, as
		reopening after a member list edit does.
		"""
		super().initSettings()
		self._installed = True
		# Only now: until NVDA has finished the switch there is nothing for a member that
		# arrives to join, and `_relayout` would refuse to do anything with it anyway.
		self._startPolling()

	def display(self, cells: list[int]) -> None:
		"""Fan one composite cell array out to the members.

		The array's length is checked again, since a short write would raise on a background
		thread.

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

		`_suppressDisplayClear` is read before the base class consumes it, and passed to members.
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
	def configuredMembers(self) -> tuple[str, ...]:
		"""The members this composite was opened for, in stacking order.

		Unlike `slots`, includes displays that are off or given up on. Ask this whether the
		configured list has changed.
		"""
		return tuple(spec.driverName for spec in self._specs)

	@property
	def slots(self) -> tuple[DeviceSlot, ...]:
		"""The live members, in stacking order.

		The device map the add-on's view builder will read, so that a segment can be made to
		line up with a physical display and the dead columns can be masked.
		"""
		return tuple(self._slots)

	def slotForDriverName(self, driverName: str) -> DeviceSlot | None:
		"""Find the member a gesture came from.

		A gesture's `source` is its driver's name, unique among members.

		:param driverName: the name to look for.
		:return: the member, or None if no member has that name.
		"""
		for slot in self._slots:
			if slot.driverName == driverName:
				return slot
		return None

	# Member settings
	#
	# The members' settings are offered as this driver's own, named per display, with reads
	# and writes passed through. Each copy is `useConfig = False`, so the member saves it in
	# its own section, as when used alone. See "Phase 5 results" in
	# docs/design/virtual-display-plan.md.

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
		self._settingNames.update(proxies)
		# Most drivers have no settings, so none is ordinary; logged to tell it from a fault.
		log.debug(f"BrlMultiline: members offer {len(settings)} setting(s) between them")
		for name in proxies:
			# Anything of this name stored on the composite is a value that was written while
			# its display was away. The member is the authority now that it is back.
			self.__dict__.pop(name, None)
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
		if proxies is None or name not in proxies or not self._proxyIsLive(proxies[name]):
			if not object.__getattribute__(self, "_slots"):
				return None
			# Built on demand, since a caller may reach for a setting before anything has asked
			# this driver what settings it has — and rebuilt when what it points at is stale.
			# A member replaced after a reconnection is a different driver object, and the
			# settings dialog or the settings ring may be holding the list from before it.
			self.supportedSettings  # noqa: B018 - read to rebuild the map.
			proxies = object.__getattribute__(self, "_settingProxies")
		proxy = proxies.get(name)
		return proxy if proxy is not None and self._proxyIsLive(proxy) else None

	def _proxyIsLive(self, proxy) -> bool:
		""":return: whether a proxy still names a member this display is driving.

		:param proxy: the slot and attribute a settings name stands for.
		"""
		slot = proxy[0]
		return not slot.failed and any(each is slot for each in object.__getattribute__(self, "_slots"))

	def __getattr__(self, name: str):
		proxy = self._proxyFor(name) if not name.startswith("_") else None
		if proxy is None:
			raise AttributeError(name)
		slot, attribute = proxy
		return getattr(slot.driver, attribute)

	def __setattr__(self, name: str, value) -> None:
		proxy = self._proxyFor(name) if not name.startswith("_") else None
		if proxy is not None:
			slot, attribute = proxy
			setattr(slot.driver, attribute, value)
			return
		if not name.startswith("_") and name in self._knownSettingNames():
			# A setting whose display is not here. Keeping the value would be worse than
			# dropping it: it would sit on this driver and shadow the member that comes back.
			log.debugWarning(f"BrlMultiline: {name} was set while its display was away; ignoring it")
			return
		super().__setattr__(name, value)

	def _knownSettingNames(self) -> set:
		""":return: every name that has stood for a member's setting, empty before any has."""
		try:
			return object.__getattribute__(self, "_settingNames")
		except AttributeError:
			return set()

	def loadSettings(self, onlyChanged: bool = False) -> None:
		"""Read this display's settings back, and have each member read its own back too.

		What Cancel does, since NVDA writes settings as the dialog is used. Almost all
		delegation; one member failing does not stop the others.

		:param onlyChanged: passed through, and passed the way NVDA passes it: a driver whose
			override does not take it still gets called.
		"""
		super().loadSettings(onlyChanged=onlyChanged)
		for slot in self._slots:
			if slot.failed:
				continue
			try:
				extensionPoints.callWithSupportedKwargs(
					slot.driver.loadSettings,
					onlyChanged=onlyChanged,
				)
			except Exception:
				log.error(f"BrlMultiline: could not read {slot.driverName}'s settings back", exc_info=True)

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

		NVDA looks for display scripts on this driver, so members' own commands go through here.

		:param gesture: the gesture being resolved.
		:return: the bound script, or None.
		"""
		script = gestures.scriptForMember(self, gesture)
		if script is not None:
			return script
		return super().getScript(gesture)

	def _getModifierGestures(self, model=None):
		"""Chain every member's modifier gestures.

		Shadows the base classmethod, since only the instance knows its members.

		:param model: the optional display model, passed through unchanged.
		"""
		return gestures.modifierGesturesForMembers(self, model)


def _isPresent(spec: DeviceSpec) -> bool:
	"""Ask Windows whether anything this member could be is there.

	A filter: True means worth trying, since a paired Bluetooth display is listed while off;
	False is trustworthy. A member with its own port, or one `bdDetect` has no data for, is
	always attempted.

	:param spec: the member to look for.
	:return: whether to attempt it.
	"""
	if spec.port and spec.port != DEFAULT_PORT:
		return True
	try:
		import bdDetect
	except Exception:
		return True
	found = False
	for lookup in (bdDetect.getConnectedUsbDevicesForDriver, bdDetect.getPossibleBluetoothDevicesForDriver):
		try:
			if next(iter(lookup(spec.driverName)), None) is not None:
				return True
			found = True
		except LookupError:
			# No detection data of this kind for this driver, which says nothing either way.
			continue
		except Exception:
			log.debugWarning(f"BrlMultiline: could not look for {spec.driverName}", exc_info=True)
			return True
	# Something answered, and nothing was there. Only then is absence worth believing.
	return not found


def _shapeOf(
	driver: braille.display.driver.BrailleDisplayDriver,
	spec: DeviceSpec | None = None,
) -> tuple[int, int]:
	"""Read a driver's geometry the way `BrailleHandler` reads it.

	Prefers `numCells` for a single row driver, as the handler does, then applies any rows the
	reader configured; see `virtualLayout.memberShape`. Rows that cannot apply are logged.

	:param driver: the driver.
	:param spec: the member's configuration, or None for the driver's own shape.
	:return: its (numRows, numCols).
	"""
	numRows = driver.numRows
	numCols = driver.numCols if numRows > 1 else driver.numCells
	rows = spec.rows if spec is not None else 0
	shape = memberShape(numRows, numCols, rows)
	if rows > 1 and shape == (numRows, numCols):
		log.warning(
			f"BrlMultiline: {spec.driverName} reports {numRows} rows of {numCols}, which cannot be "
			f"laid out as the {rows} rows configured for it; using what it reports",
		)
	return shape


_retiredDrivers: list[braille.display.driver.BrailleDisplayDriver] = []
"""Every member this driver has closed, kept alive for the rest of the session.

A deliberate, bounded leak around an NVDA defect: `hwIo.base.IoBase.__del__` closes a handle
`close` already closed, which Windows may have reused. Holding a reference keeps the finaliser
from running. See "The `hwIo` finaliser bug" in docs/design/virtual-display-plan.md.

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
	:param partial: whether this driver failed part way through construction, so `terminate`
		raising is expected and reported quietly.
	"""
	name = getattr(driver, "name", "?")
	if partial:
		# A half constructed driver can raise while the base class blanks it, and `terminate`
		# logs that itself. NVDA's flag closes without writing, and still closes the handle.
		driver._suppressDisplayClear = True
	try:
		# Watched, because blanking is a write and a display that did not open properly is the
		# likeliest of all not to take one.
		with memberGuard.guard.watching(f"{name} closing", driver, memberGuard.MEMBER_WRITE_TIMEOUT):
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

	Constructed as `BrailleHandler._setDisplay` does.

	:param spec: the member to open.
	:param attempts: how many times to try, or None for `OPEN_ATTEMPTS`. The reconnect poll
		passes one.
	:param quiet: report a failure at debug level, for the reconnect poll.
	:return: the live driver, or None if it could not be opened.

	Watched by `memberGuard`, so an unanswering display cannot freeze NVDA. A constructor the
	guard rescued is neither retried nor kept.
	"""
	attempts = OPEN_ATTEMPTS if attempts is None else attempts
	try:
		driverClass = _getDisplayDriver(spec.driverName)
	except ImportError:
		log.error(f"BrlMultiline: no braille display driver named {spec.driverName!r}")
		return None

	for attempt in range(1, attempts + 1):
		driver = None
		watch = None
		try:
			driver = driverClass.__new__(driverClass)
			with memberGuard.guard.watching(
				f"{spec.driverName} opening",
				driver,
				memberGuard.MEMBER_OPEN_TIMEOUT,
			) as watch:
				extensionPoints.callWithSupportedKwargs(driver.__init__, port=spec.port)
				driver.initSettings()
		except Exception:
			if watch is not None and watch.fired:
				_terminateQuietly(driver, partial=True)
				log.debugWarning(f"BrlMultiline: {spec.driverName} did not answer while opening", exc_info=True)
				return None
			# A constructor that raised may still hold the device, so close it, best effort,
			# before the next attempt.
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
			if watch.fired:
				_terminateQuietly(driver, partial=True)
				return None
			log.debug(f"BrlMultiline: opened {spec.driverName} on attempt {attempt}")
			return driver
	return None
