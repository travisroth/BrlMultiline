# BrlMultiline: moving the focus from one combined display to another.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Which of several combined displays follows the focus, and moving it, pins and all.

The plugin's script calls into this. The focus segment is stored in the configuration; a pin in
the way is carried to another segment, or the reader is asked which pins to keep.
"""

from __future__ import annotations

from typing import NamedTuple, Protocol, TYPE_CHECKING

import addonHandler
import braille
import gui
import ui
import wx
from logHandler import log

from . import bmConfig
from .devices import deviceMap
from .settingsPanel import displayDescriptions
from .objectMonitor import ObjectMonitor
from .views import deviceSegmentKeys, driverNameForSegmentKey

addonHandler.initTranslation()

if TYPE_CHECKING:
	from collections.abc import Collection, Mapping

	from .container import DisplayContainer
	from .devices import DeviceInfo
	from .views import SegmentView

BREAK = chr(10)
"""A line break in a dialog's text, spelled so no escape has to survive a shell."""


class FocusDisplayTarget(NamedTuple):
	"""One of the displays behind a composite, considered as somewhere to put the focus."""

	driverName: str
	"""The NVDA driver behind it, which is what names it in the configuration."""

	label: str
	"""What to call it when there is a choice to announce or to show in a list."""

	segments: list[int]
	"""The segments lying on it, numbered as the configuration numbers them, in display order.

	Never empty: a display holding no segment of its own is not somewhere the focus can go,
	and is left out of the list rather than offered and refused.
	"""

	holdsFocus: bool
	"""Whether the segment following the focus is one of these."""


class CarriedPin(NamedTuple):
	"""A pin the focus move took with it, and what it was before it went.

	What it was matters, because a pin moved from many rows to one stops being a flow, and
	the reader is told.
	"""

	monitor: ObjectMonitor
	home: str
	"""The segment it was filed under. What it ends up on is settled by the rebuild."""

	wasFlowing: bool


class FocusHost(Protocol):
	"""What `FocusDisplayMoves` uses of the plugin. See docs/design/architecture.md, Module map."""

	@property
	def container(self) -> DisplayContainer | None: ...

	@property
	def pins(self) -> Mapping[str, ObjectMonitor]: ...

	def baseView(self, numRows: int, numCols: int, devices: list[DeviceInfo]) -> SegmentView: ...

	def landPin(self, monitor: ObjectMonitor, home: str) -> None: ...

	def movePin(self, key: str, home: str) -> ObjectMonitor: ...

	def rebuildBuffer(self) -> None: ...

	def refreshMonitors(self, reveal: str | None = None) -> None: ...

	def rehomeMonitor(self, container: DisplayContainer, taken: Collection[str]) -> str | None: ...


class FocusDisplayMoves:
	"""Moving the focus between combined displays, for the plugin's script."""

	def __init__(self, plugin: FocusHost):
		"""
		:param plugin: the global plugin, for its display, its pins and to rebuild.
		"""
		self.plugin = plugin
		self.pinsInFlight: list[ObjectMonitor] = []
		"""Pins a focus move is carrying, until the display it caused has settled.

		A displaced pin is moved before the rebuild, and the segment it moves to may come back
		under a different key once the flow band has claimed or released rows, so the
		carry-over must not release it. Held as monitors rather than keys, because one move
		can rebuild the display more than once. Held only across that one move's rebuilds.
		"""

	def changeDisplay(self) -> None:
		"""Put the segment that follows the system focus on another of the displays.

		With two displays this toggles; with more, it offers a list. Stored in the profile in
		force.
		"""
		targets = self.targets()
		if len(targets) < 2:
			ui.message(
				# Translators: reported when a command that moves the focus from one braille
				# display to another is pressed with only one display to put it on.
				_("There is only one display for the focus to follow"),
			)
			return
		here = next((target for target in targets if target.holdsFocus), None)
		if here is None:
			# The focus is on cells no display of its own owns, which a claim over the whole
			# composite can do. Anywhere is as good as anywhere, so start at the top.
			position = 0
		else:
			total = sum(len(target.segments) for target in targets)
			position = here.segments.index(self.configuredFocusSegment(total))
		if len(targets) > 2:
			self.chooseDisplay(targets, position)
			return
		other = next(target for target in targets if target is not here)
		self.moveWithPins(other, position)

	def targets(self) -> list[FocusDisplayTarget]:
		""":return: the displays the focus could be made to follow, in stacking order.

		Numbered by `views.deviceSegmentKeys`, the configuration's numbering, not the
		container's, which changes while a claim such as the flow band is up. Empty for an
		ordinary display, and while an activated view has the whole display.
		"""
		handler = braille.handler
		if handler is None or handler.displaySize == 0:
			return []
		devices = deviceMap()
		if not devices:
			return []
		dimensions = handler.displayDimensions
		try:
			view = self.plugin.baseView(dimensions.numRows, dimensions.numCols, devices)
		except Exception:
			log.debugWarning("BrlMultiline: could not read the display's arrangement", exc_info=True)
			return []
		if view.name != "devices":
			# An activated view names its own focus segment, so the setting is not read.
			return []
		try:
			keys = deviceSegmentKeys(devices, dimensions.numCols)
		except ValueError:
			log.debugWarning("BrlMultiline: could not number the display's segments", exc_info=True)
			return []
		focusNumber = self.configuredFocusSegment(len(keys))
		names = displayDescriptions()
		targets = []
		for device in devices:
			numbers = [
				number for number, key in enumerate(keys) if driverNameForSegmentKey(key) == device.driverName
			]
			if not numbers:
				continue
			targets.append(
				FocusDisplayTarget(
					driverName=device.driverName,
					# Translators: names one of several combined braille displays, when choosing
					# which of them follows the focus. Placeholders are its name and its height.
					label=_("{display}, {rows} rows").format(
						display=names.get(device.driverName, device.driverName),
						rows=device.numRows,
					),
					segments=numbers,
					holdsFocus=focusNumber in numbers,
				),
			)
		return targets

	def configuredFocusSegment(self, numSegments: int) -> int:
		""":return: the segment the configuration says follows the focus, as an index.

		:param numSegments: how many segments the arrangement has, for resolving -1 and for
			refusing a number left over from a larger display.
		"""
		number = bmConfig.getFocusSegment()
		if number == -1 or not 0 <= number < numSegments:
			return numSegments - 1
		return number

	def focusSegmentFor(self, target: FocusDisplayTarget, position: int = 0) -> int:
		""":return: the segment the focus would take on a display, in the configuration's numbering.

		:param target: the display it would move to.
		:param position: how far down its display the focus segment is now, kept if the new
			display is divided finely enough and clamped to its last segment if it is not.
		"""
		return target.segments[min(max(position, 0), len(target.segments) - 1)]

	def pinsDisplacedBy(self, number: int) -> list[str]:
		"""Which pinned objects the focus would evict by landing where it is going.

		A pin and the focus cannot share a segment, so `GlobalPlugin._carryOverMonitors` would release the
		pin quietly. Asked first, because a reader moving the focus is not thinking about the
		pin.

		:param number: the segment the focus is going to, in the configuration's numbering.
		:return: the keys of the segments whose pins would go, in display order.
		"""
		container = self.plugin.container
		if container is None or not self.plugin.pins:
			return []
		try:
			key = self.segmentKeyFor(number)
		except LookupError:
			return []
		if key is None:
			return []
		return [spec.key for spec in container.specs if spec.key == key and spec.key in self.plugin.pins]

	def segmentKeyFor(self, number: int) -> str | None:
		""":return: the segment a configured number names, or None if it names none.

		The configuration's numbering, as in `targets`.
		"""
		handler = braille.handler
		devices = deviceMap()
		if handler is None or not devices:
			return None
		try:
			keys = deviceSegmentKeys(devices, handler.displayDimensions.numCols)
		except ValueError:
			log.debugWarning("BrlMultiline: could not number the display's segments", exc_info=True)
			return None
		if number == -1:
			number = len(keys) - 1
		return keys[number] if 0 <= number < len(keys) else None

	def homesForDisplacedPins(self, number: int) -> list[str]:
		"""Where a pin evicted by the focus could go instead, best first.

		The segment the focus is leaving comes first, so the two trade places and moving back
		trades them back; then the rest of the display in order. The segment being left
		counts even if the flow band claims it, since the band moves with the focus.

		This is where a pin could go, not where it will end up: `GlobalPlugin._carryOverMonitors` settles
		that against the display actually built.

		:param number: the segment the focus is going to.
		:return: the keys of segments that could hold a pin, best first.
		"""
		container = self.plugin.container
		if container is None:
			return []
		going = self.segmentKeyFor(number)
		taken = set(self.plugin.pins)
		homes: list[str] = []
		ordered = [container.focusSegmentKey] + [spec.key for spec in container.specs]
		for key in ordered:
			if key in homes or key == going or key in taken or not container.hasKey(key):
				continue
			if container.segmentForKey(key).isReserved and key != container.focusSegmentKey:
				continue
			homes.append(key)
		return homes

	def moveWithPins(self, target: FocusDisplayTarget, position: int = 0) -> None:
		"""Move the focus to a display, taking any pin it would evict with it.

		1. Nothing in the way: the focus moves.
		2. One pin and somewhere to put it: they trade places, without asking.
		3. Anything else: the reader chooses which pins to keep, or cancels.

		:param target: the display to move the focus to.
		:param position: how far down its display the focus segment is now.
		"""
		number = self.focusSegmentFor(target, position)
		displaced = self.pinsDisplacedBy(number)
		homes = self.homesForDisplacedPins(number)
		if not displaced:
			self.moveTo(target, position)
			return
		if len(displaced) == 1 and homes:
			self.moveTo(target, position, keep={displaced[0]: homes[0]})
			return
		self.askAboutDisplacedPins(target, position, displaced, homes)

	def moveTo(
		self,
		target: FocusDisplayTarget,
		position: int = 0,
		keep: "dict[str, str] | None" = None,
	) -> None:
		"""Make one of the combined displays the one that follows the system focus.

		:param target: the display to move it to.
		:param position: how far down its display the focus segment is now, kept if the new
			display is divided finely enough and clamped to its last segment if it is not.
		:param keep: pins to carry rather than let go, from the key they are on now to the key
			they should move to. Others are released by `GlobalPlugin._carryOverMonitors`.
		"""
		number = self.focusSegmentFor(target, position)
		carried = self._carryPins(keep or {})
		bmConfig.setFocusSegment(number)
		try:
			# One move can rebuild the display several times: the band is claimed or given
			# back as the focus arrives, and claiming rebuilds again. The pins are in flight
			# for all of it, and land only when it has finished.
			self.plugin.rebuildBuffer()
		finally:
			self._landPinsInFlight()
		ui.message(
			# Translators: reported when the focus is moved onto one of several combined
			# displays. Placeholders are the display's name and the segment number.
			_("Focus on {display}, segment {number}").format(display=target.label, number=number),
		)
		for message in self.describeCarriedPins(carried):
			ui.message(message)

	def _landPinsInFlight(self) -> None:
		"""Put the pins a focus move carried into the display it ended up building.

		Handles a pin that was held, not released, because it had nowhere to go part way
		through the rebuilds. If there is still nowhere, it is released here and the reader is
		told.
		"""
		inFlight, self.pinsInFlight = self.pinsInFlight, []
		container = self.plugin.container
		landed = False
		for monitor in inFlight:
			if any(monitor is filed for filed in self.plugin.pins.values()):
				continue
			home = self.plugin.rehomeMonitor(container, self.plugin.pins) if container is not None else None
			if home is None:
				log.info(
					f"BrlMultiline: nowhere on this display for the object pinned to "
					f"{monitor.segmentKey!r}, so moving the focus released it",
				)
				continue
			log.info(f"BrlMultiline: the pin the focus move carried lands on segment {home!r}")
			self.plugin.landPin(monitor, home)
			landed = True
		if landed:
			# Drawn where it landed, rather than waiting for the refresh tick to notice.
			self.plugin.refreshMonitors()

	def describeCarriedPins(self, carried: "list[CarriedPin]") -> list[str]:
		""":return: what to tell the reader about the pins a focus move took with it.

		After the rebuild, which decides where each pin landed and how many rows it has.
		Reports a pin that could not be kept, and a pin that is no longer read as a flow.

		:param carried: what the move took with it, before the rebuild.
		"""
		if not carried:
			return []
		alive = {id(monitor) for monitor in self.plugin.pins.values()}
		kept = [pin for pin in carried if id(pin.monitor) in alive]
		messages = []
		if len(kept) > 1:
			messages.append(
				# Translators: reported when moving the focus moves pinned objects out of its
				# way. The placeholder is how many objects moved.
				_("{count} pinned objects moved").format(count=len(kept)),
			)
		elif kept:
			# Translators: reported when moving the focus moves one pinned object out of its
			# way, so that it is not lost.
			messages.append(_("The pinned object moved with it"))
		flattened = sum(
			1 for pin in kept if pin.wasFlowing and getattr(pin.monitor, "controller", None) is None
		)
		if flattened:
			messages.append(
				# Translators: reported when a pinned object that was being read as a flow —
				# a list, a document, a table — lands somewhere with too few rows for one, so
				# it is now a single line that cannot be panned through.
				_("Too few rows there to read it as a flow"),
			)
		lost = len(carried) - len(kept)
		if lost:
			messages.append(
				# Translators: reported when moving the focus could not keep a pinned object
				# after all. The placeholder is how many were lost.
				_("{count} pinned objects could not be kept").format(count=lost)
				if lost > 1
				# Translators: reported when moving the focus could not keep the one pinned
				# object it was carrying.
				else _("The pinned object could not be kept"),
			)
		return messages

	def _carryPins(self, keep: "dict[str, str]") -> "list[CarriedPin]":
		"""Move pins to their new homes before the display is rebuilt.

		Before the rebuild, so `GlobalPlugin._carryOverMonitors` sees each pin where it now is. A home the
		move itself dissolves, such as rows the flow band gives back, is handled through
		`pinsInFlight`.

		:param keep: the key each pin is on now, to the key it should move to.
		:return: what moved, so the caller can say what became of it.
		"""
		carried: list[CarriedPin] = []
		for key, home in keep.items():
			monitor = self.plugin.pins.get(key)
			if monitor is None or home == key:
				continue
			wasFlowing = getattr(monitor, "controller", None) is not None
			try:
				self.plugin.movePin(key, home)
			except Exception:
				log.debugWarning(f"Could not move the pin on segment {key!r}", exc_info=True)
				continue
			carried.append(CarriedPin(monitor=monitor, home=home, wasFlowing=wasFlowing))
		self.pinsInFlight = [pin.monitor for pin in carried]
		return carried

	def askAboutDisplacedPins(
		self,
		target: FocusDisplayTarget,
		position: int,
		displaced: list[str],
		homes: list[str],
	) -> None:
		"""Ask which pinned objects to keep before moving the focus over them.

		The reader may keep as many as there are free segments, or cancel the move. The only
		place on this path that uses wx, so the rest can be tested without it.

		:param target: the display the focus would move to.
		:param position: how far down it the focus would sit.
		:param displaced: the keys of the pins in the way, in display order.
		:param homes: where they could go instead, best first.
		"""
		names = [self._describePin(key) for key in displaced]
		room = len(homes)

		def ask():
			gui.mainFrame.prePopup()
			try:
				if not room:
					answer = gui.messageBox(
						# Translators: a dialog shown when moving the focus would release
						# pinned objects and there is nowhere else to put them. The
						# placeholder is a list of what is pinned.
						_(
							"Moving the focus here would release these pinned objects, "
							"and there is nowhere else on the display to put them:{objects}"
							"{gap}Move the focus anyway?",
						).format(
							objects=BREAK + BREAK.join(names),
							gap=BREAK + BREAK,
						),
						# Translators: the title of that dialog.
						_("Moving the focus"),
						wx.YES_NO | wx.ICON_WARNING,
					)
					if answer == wx.YES:
						self.moveTo(target, position)
					return
				dialog = wx.MultiChoiceDialog(
					gui.mainFrame,
					# Translators: a dialog asking which pinned objects to keep when moving
					# the focus would release them. Placeholders are how many objects are in
					# the way and how many there is room to keep.
					_(
						"Moving the focus here would release {count} pinned objects. "
						"There is room to keep {room}. Choose which to keep; the rest are released.",
					).format(count=len(displaced), room=room),
					# Translators: the title of that dialog.
					_("Moving the focus"),
					names,
				)
				try:
					dialog.SetSelections(list(range(min(room, len(names)))))
					if dialog.ShowModal() != wx.ID_OK:
						return
					chosen = list(dialog.GetSelections())
				finally:
					dialog.Destroy()
			finally:
				gui.mainFrame.postPopup()
			keep = {}
			for index in chosen[:room]:
				if 0 <= index < len(displaced):
					keep[displaced[index]] = homes[len(keep)]
			self.moveTo(target, position, keep=keep)

		wx.CallAfter(ask)

	def _describePin(self, key: str) -> str:
		""":return: what to call a pinned object in a dialog."""
		monitor = self.plugin.pins.get(key)
		name = getattr(monitor, "name", None) or key
		container = self.plugin.container
		try:
			number = container.numberForKey(key) if container is not None else None
		except LookupError:
			number = None
		if number is None:
			return str(name)
		# Translators: names a pinned object and the segment it is in, in a dialog listing
		# them. Placeholders are the object's name and the segment number.
		return _("{name}, segment {number}").format(name=name, number=number)

	def chooseDisplay(self, targets: list[FocusDisplayTarget], position: int) -> None:
		"""Ask which display should follow the focus, then move it there.

		Only with more than two displays. The only place on this path that uses wx.

		:param targets: the displays to offer, in stacking order.
		:param position: passed on to `moveWithPins`.
		"""
		current = next((index for index, target in enumerate(targets) if target.holdsFocus), 0)

		def ask():
			# prePopup before the dialog is made and postPopup after it has gone, which is
			# how NVDA opens one: it raises its own frame first so the dialog can take the
			# focus, and puts things back afterwards.
			gui.mainFrame.prePopup()
			try:
				dialog = wx.SingleChoiceDialog(
					gui.mainFrame,
					# Translators: the message of a dialog asking which of several combined
					# braille displays should follow the system focus.
					_("Which display should follow the focus?"),
					# Translators: the title of a dialog asking which of several combined
					# braille displays should follow the system focus.
					_("Follow the focus"),
					[target.label for target in targets],
				)
				try:
					dialog.SetSelection(current)
					if dialog.ShowModal() != wx.ID_OK:
						return
					chosen = dialog.GetSelection()
				finally:
					dialog.Destroy()
			finally:
				gui.mainFrame.postPopup()
			if 0 <= chosen < len(targets):
				# Through the pin-preserving move, exactly as the two-display toggle goes:
				# choosing a display from a list is the same request as toggling to it, and a
				# pin on the one chosen was silently released while this called the plain move
				# directly.
				self.moveWithPins(targets[chosen], position)

		wx.CallAfter(ask)
