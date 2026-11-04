# BrlMultiline: the pinned objects dialog.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Where a reader pins the navigator object to a segment or a place, or changes or releases a pin,
with one key.

The per segment commands need a key for every segment, which a display such as the Monarch does
not have to spare. See docs/design/port-plan.md, The pinned objects dialog, and
docs/design/pin-places-plan.md for places.

`PinManager` knows nothing about wx and is tested without a display; the dialog is widgets, the
split `flowTableManager` makes.
"""

import copy
import dataclasses
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol

import api
import ui
from logHandler import log

from . import pinPlaces
from .i18n import ngettext
from .layout import SegmentRect
from .objectMonitor import ObjectMonitor

if TYPE_CHECKING:
	from NVDAObjects import NVDAObject

	from .container import DisplayContainer

try:
	import wx
	import gui
	from gui import guiHelper
except ImportError:  # pragma: no cover - only in a test run with no NVDA around it.
	wx = None
	gui = None
	guiHelper = None

import addonHandler

addonHandler.initTranslation()


class PinHost(Protocol):
	"""What `PinManager` uses of the plugin. See docs/design/architecture.md, Module map."""

	places: pinPlaces.Places

	@property
	def container(self) -> "DisplayContainer | None": ...

	@property
	def pins(self) -> Mapping[str, ObjectMonitor]: ...

	@property
	def pinsAside(self) -> Mapping[str, ObjectMonitor]: ...

	@property
	def arePinsAside(self) -> bool: ...

	def pinTargets(self) -> list[int]: ...

	def pinRefusal(self, container: "DisplayContainer", number: int) -> str | None: ...

	def describeSegment(self, number: int) -> str: ...

	def describeDisplay(self, driverName: str) -> str: ...

	def placeAreas(self) -> dict[str, SegmentRect]: ...

	def offeredPlaces(self) -> dict[str, SegmentRect]: ...

	def noRoomMessage(self, area: SegmentRect, room: int | None) -> str: ...

	def startMonitoring(self, segmentNumber: int, obj: "NVDAObject | None" = None) -> None: ...

	def pinToPlace(
		self,
		placeId: str,
		obj: "NVDAObject",
		size: int | None = None,
		before: bool = False,
		atStart: bool = False,
	) -> bool: ...

	def resizePin(self, key: str, size: int | None) -> str | None: ...

	def shiftPin(self, key: str, by: int) -> bool: ...

	def releasePin(self, key: str) -> None: ...

	def setPinsAside(self, aside: bool) -> None: ...


@dataclasses.dataclass(frozen=True)
class Target:
	"""Somewhere the object can be pinned: one of the reader's own segments, or a place."""

	name: str
	"""What the reader is offered."""

	key: str | None = None
	"""The segment's key, for one of the reader's own segments."""

	placeId: str | None = None
	"""The place's id, for a place."""

	before: bool = False
	"""In the focus place, before the focus rather than after it."""

	area: SegmentRect | None = None
	"""Where the place is, for its sizes."""

	holds: int = 0
	"""How many pins a place already has on this side, which a new one would share with."""

	@property
	def isPlace(self) -> bool:
		return self.placeId is not None


def sizeWords(size: int | None, area: SegmentRect | None) -> str:
	""":return: a pin's size as the reader is shown it: fill, or a count of rows or cells."""
	if size is None:
		# Translators: the size of a pin that shares what the others leave.
		return _("Fill")
	if area is not None and area.numRows == 1:
		# Translators: the size of a pin on a display of one row. The placeholder is a number of cells.
		return ngettext("{count} cell", "{count} cells", size).format(count=size)
	# Translators: the size of a pin. The placeholder is a number of rows.
	return ngettext("{count} row", "{count} rows", size).format(count=size)


def parseSize(text: str) -> int | None:
	"""Read a size the reader chose or typed.

	:return: the size, or None for fill, which is also what nothing typed means.
	:raises ValueError: if it is neither fill, a whole number of at least one, nor a size as
		`sizeWords` words it.
	"""
	text = text.strip()
	if not text or text.casefold() == sizeWords(None, None).casefold():
		return None
	numbers = re.findall(r"\d+", text)
	if len(numbers) != 1 or int(numbers[0]) < 1:
		raise ValueError(text)
	size = int(numbers[0])
	worded = {sizeWords(size, area).casefold() for area in (_ROWS, _CELLS)}
	if text != numbers[0] and text.casefold() not in worded:
		raise ValueError(text)
	return size


_ROWS = SegmentRect(0, 0, 2, 1)
"""Any place of rows, for wording a size in rows."""

_CELLS = SegmentRect(0, 0, 1, 1)
"""Any place of one row, for wording a size in cells."""


def sizeChoices(area: SegmentRect | None, most: int | None = None) -> list[str]:
	"""The sizes offered for a pin in a place: fill, then each count of rows, or of cells in steps.

	:param area: the place, or None for no place, which is offered fill alone.
	:param most: the largest size there is room for, the place's whole extent if None.
	"""
	choices = [sizeWords(None, area)]
	if area is None:
		return choices
	least = pinPlaces.minimumFor(area)
	most = pinPlaces.extentOf(area) if most is None else most
	step = 1 if area.numRows > 1 else 5
	counts = list(range(least, most + 1, step))
	if counts and counts[-1] != most:
		counts.append(most)
	choices.extend(sizeWords(count, area) for count in counts)
	return choices


class PinManager:
	"""Where the object can be pinned, the pins there are, and what the reader does with them.

	Segments are held by key and places by id, so a rebuild while the dialog is up cannot turn a
	choice into somewhere else.
	"""

	def __init__(self, plugin: PinHost, obj) -> None:
		"""
		:param plugin: the global plugin, which holds the pins.
		:param obj: the object to pin, taken before the dialog took the navigator, or None.
		"""
		self.plugin = plugin
		self.obj = obj
		self.chosen: Target | None = None
		"""Where to pin, pinned by `pinChosen` once the dialog has closed."""

		self.chosenSize: int | None = None
		"""The size asked for in a place, None to fill."""

		self.chosenAtStart = False
		"""Whether to go before the pins already on that side of a place rather than after them."""

		self.offered: list[Target] = []
		"""The targets last listed, which a name the reader chose is taken from. See `choose`."""

	@property
	def chosenKey(self) -> str | None:
		""":return: the key of the reader's own segment chosen, or None."""
		return self.chosen.key if self.chosen is not None else None

	def objectName(self) -> str:
		""":return: what to call the object to pin."""
		if self.obj is None:
			# Translators: shown in the pinned objects dialog when there is no navigator object to pin.
			return _("None")
		return ObjectMonitor.describe(self.obj)

	def targets(self) -> list[Target]:
		"""Where the object can be pinned: the reader's free segments in display order, then places.

		While pins are set aside the segments are not offered, since their numbers are those of
		the arrangement that comes back. A segment that is a whole display is offered only as that
		display's place, which is the same rows with sizes that can change later.
		"""
		container = self.plugin.container
		if container is None or self.obj is None:
			return []
		places = []
		for placeId, area in self.plugin.offeredPlaces().items():
			if placeId == pinPlaces.FOCUS_PLACE:
				places.extend(self._focusPlaceTargets(area))
			else:
				places.append(self._displayTarget(placeId, area))
		if self.plugin.arePinsAside:
			self.offered = places
			return places
		self.offered = [
			Target(self.plugin.describeSegment(number), key=container.segments[number].key)
			for number in self.plugin.pinTargets()
			if self._wholeDisplayPlace(container.segments[number].rect, places) is None
		] + places
		return self.offered

	@staticmethod
	def _wholeDisplayPlace(rect: SegmentRect, places: list[Target]) -> Target | None:
		""":return: the place of another display that is exactly a segment's rows, or None."""
		return next(
			(target for target in places if target.placeId != pinPlaces.FOCUS_PLACE and target.area == rect),
			None,
		)

	def _focusPlaceTargets(self, area: SegmentRect) -> list[Target]:
		""":return: before and after the focus, with how many pins each side holds."""
		members = self.plugin.places.membersFor(pinPlaces.FOCUS_PLACE)
		focusAt = members.index(None)
		holdsBefore, holdsAfter = focusAt, len(members) - focusAt - 1
		if area.numRows > 1:
			# Translators: a place in the pinned objects dialog: rows taken from above the focus.
			before = _("Above the focus")
			# Translators: a place in the pinned objects dialog: rows taken from below the focus.
			after = _("Below the focus")
		else:
			# Translators: a place in the pinned objects dialog: cells taken from before the focus,
			# on a display of one row.
			before = _("Before the focus")
			# Translators: a place in the pinned objects dialog: cells taken from after the focus,
			# on a display of one row.
			after = _("After the focus")
		return [
			Target(
				_sharing(before, holdsBefore),
				placeId=pinPlaces.FOCUS_PLACE,
				before=True,
				area=area,
				holds=holdsBefore,
			),
			Target(_sharing(after, holdsAfter), placeId=pinPlaces.FOCUS_PLACE, area=area, holds=holdsAfter),
		]

	def _displayTarget(self, placeId: str, area: SegmentRect) -> Target:
		""":return: another display as a place."""
		display = self.plugin.describeDisplay(placeId)
		holds = len(self.plugin.places.membersFor(placeId))
		if holds:
			# Translators: a place in the pinned objects dialog: one of the combined displays, which
			# pins already share. The placeholder is its name, such as "the second display".
			name = _sharing(_("On {display}").format(display=display), holds)
		else:
			# Translators: a place in the pinned objects dialog: the whole of one of the combined
			# displays. The placeholder is its name, such as "the second display".
			name = _("All of {display}").format(display=display)
		return Target(name, placeId=placeId, area=area, holds=holds)

	def pins(self) -> list[tuple[str, str]]:
		""":return: the key of each pin and a line saying what is pinned where, those shown in display
		order and then those set aside."""
		container = self.plugin.container
		found = []
		if container is not None:
			for key, monitor in self.plugin.pins.items():
				try:
					number = container.numberForKey(key)
				except LookupError:
					continue
				# Translators: one line in the list of pinned objects. Placeholders are the object's
				# name and the segment it is pinned to, such as "Segment 2".
				line = _("{name}: {segment}").format(
					name=monitor.name,
					segment=self.plugin.describeSegment(number),
				)
				found.append((number, key, self._withSize(key, line)))
		shown = [(key, line) for _number, key, line in sorted(found)]
		aside = [
			# Translators: one line in the list of pinned objects, for a pin set aside for now. The
			# placeholder is the object's name.
			(key, self._withSize(key, _("{name}: set aside").format(name=monitor.name)))
			for key, monitor in self.plugin.pinsAside.items()
		]
		return shown + aside

	def _withSize(self, key: str, line: str) -> str:
		""":return: a line of the list, with the pin's size added for a pin in a place."""
		pin = self.plugin.places.pinFor(key)
		if pin is None:
			return line
		return f"{line}, {sizeWords(pin.size, self.areaOf(key))}"

	def isInPlace(self, key: str) -> bool:
		return self.plugin.places.pinFor(key) is not None

	def areaOf(self, key: str) -> SegmentRect | None:
		""":return: where the place a pin is in is now, or None."""
		place = self.plugin.places.placeOf(key)
		return self.plugin.placeAreas().get(place.placeId) if place is not None else None

	def sizeChoicesFor(self, target: Target | None) -> list[str]:
		""":return: the sizes a new pin could have where the reader chose, fill alone for a segment."""
		if target is None or not target.isPlace:
			return sizeChoices(None)
		return sizeChoices(target.area, self.plugin.places.roomIn(target.placeId, target.area))

	def resizeChoicesFor(self, key: str) -> list[str]:
		""":return: the sizes a pin in a place could be given, the others keeping theirs."""
		place = self.plugin.places.placeOf(key)
		area = self.areaOf(key)
		if place is None or area is None:
			return sizeChoices(None)
		return sizeChoices(area, self.plugin.places.roomIn(place.placeId, area, key))

	def sizeOf(self, key: str) -> str:
		""":return: a pin's size as the reader is shown it."""
		pin = self.plugin.places.pinFor(key)
		return sizeWords(pin.size if pin is not None else None, self.areaOf(key))

	def choose(self, text: str, sizeText: str = "", atStart: bool = False) -> str | None:
		"""Take where the reader chose to pin, from the list or typed.

		A name is taken from the targets as they were listed, and checked again by its key or
		place, so segments renumbered while the dialog is up do not turn it into another.

		:param text: a name from `targets`, or a segment number.
		:param sizeText: for a place, the size chosen or typed.
		:param atStart: for a place, before the pins already on that side.
		:return: why it cannot go there, to be shown, or None once it is chosen.
		"""
		self.chosen = None
		if self.obj is None:
			# Translators: reported when there is no object to pin.
			return _("No object to monitor")
		text = text.strip()
		offered = self.offered or self.targets()
		listed = next((target for target in offered if text.casefold() == target.name.casefold()), None)
		if listed is not None:
			if listed.isPlace:
				return self._chooseListedPlace(listed, sizeText, atStart)
			return self._chooseSegment(listed.key, sizeText, atStart)
		if not text.isdigit():
			# Translators: shown when the reader pressed Pin without choosing a segment or typing its number.
			return _("Choose a segment, or type its number.")
		number = int(text)
		container = self.plugin.container
		if container is None:
			# Translators: reported when a command needs segments but none are configured.
			return _("BrlMultiline is not active")
		if number >= container.numSegments:
			# Translators: reported when a command names a segment that does not exist.
			# The placeholder is replaced with the segment number.
			return _("There is no segment {number}").format(number=number)
		return self._chooseSegment(container.segments[number].key, sizeText, atStart)

	def _chooseListedPlace(self, listed: Target, sizeText: str, atStart: bool) -> str | None:
		""":return: why the object cannot go in a place listed earlier, or None once it is chosen."""
		area = self.plugin.offeredPlaces().get(listed.placeId)
		if area is None:
			# Translators: reported when a place to pin to is no longer on the display.
			return _("That place is no longer on the display")
		return self._chooseIn(dataclasses.replace(listed, area=area), sizeText, atStart)

	def _chooseSegment(self, key: str, sizeText: str, atStart: bool) -> str | None:
		""":return: why the object cannot go in one of the reader's segments, or None once it is chosen."""
		container = self.plugin.container
		if container is None:
			# Translators: reported when a command needs segments but none are configured.
			return _("BrlMultiline is not active")
		try:
			number = container.numberForKey(key)
		except LookupError:
			# Translators: reported when the segment chosen in the pinned objects dialog is no longer
			# on the display, because the layout changed while the dialog was open.
			return _("That segment is no longer on the display")
		if key in self.plugin.pins:
			# Translators: shown when the reader chose a segment that already holds a pinned object.
			# The placeholder is the segment number.
			return _("Segment {number} already holds a pinned object. Release it first.").format(
				number=number,
			)
		refusal = self.plugin.pinRefusal(container, number)
		if refusal is not None:
			return refusal
		place = self._wholeDisplayPlace(container.segments[number].rect, self.targets())
		if place is not None:
			return self._chooseIn(place, sizeText, atStart)
		self.chosen = Target(self.plugin.describeSegment(number), key=key)
		return None

	def _chooseIn(self, target: Target, sizeText: str, atStart: bool) -> str | None:
		""":return: why the object cannot go in a place at that size, or None once it is chosen.

		Tried on a copy of the places, so the dialog stays up for a size that does not fit.
		"""
		try:
			size = parseSize(sizeText)
		except ValueError:
			# Translators: shown when the size typed for a pin is neither fill nor a number.
			return _("Choose a size, or type a number of rows or cells.")
		try:
			copy.deepcopy(self.plugin.places).add(
				target.placeId,
				target.area,
				size,
				before=target.before,
				atStart=atStart,
			)
		except pinPlaces.PlaceError as error:
			return self.plugin.noRoomMessage(target.area, error.room)
		self.chosen = target
		self.chosenSize = size
		self.chosenAtStart = atStart
		return None

	def pinChosen(self) -> None:
		"""Pin the object where the reader chose, wherever that is now."""
		target = self.chosen
		container = self.plugin.container
		if target is None or container is None:
			return
		if target.isPlace:
			self.plugin.pinToPlace(
				target.placeId,
				self.obj,
				size=self.chosenSize,
				before=target.before,
				atStart=self.chosenAtStart,
			)
			return
		try:
			number = container.numberForKey(target.key)
		except LookupError:
			# Translators: reported when the segment chosen in the pinned objects dialog is no longer
			# on the display, because the layout changed while the dialog was open.
			ui.message(_("That segment is no longer on the display"))
			return
		self.plugin.startMonitoring(number, self.obj)

	def resize(self, key: str, sizeText: str) -> str | None:
		""":return: why a pin in a place cannot have that size, to be shown, or None once it has."""
		try:
			size = parseSize(sizeText)
		except ValueError:
			# Translators: shown when the size typed for a pin is neither fill nor a number.
			return _("Choose a size, or type a number of rows or cells.")
		return self.plugin.resizePin(key, size)

	def move(self, key: str, by: int) -> bool:
		""":return: whether a pin in a place moved earlier or later in it."""
		return self.plugin.shiftPin(key, by)

	def release(self, key: str) -> None:
		"""Release a pin, shown or set aside, saying so as the release command does.

		:param key: the segment's key, from `pins`.
		"""
		self.plugin.releasePin(key)

	def setAside(self, aside: bool) -> None:
		self.plugin.setPinsAside(aside)


def _sharing(name: str, holds: int) -> str:
	""":return: a place's name, saying how many pins a new one would share it with."""
	if not holds:
		return name
	# Translators: added to the name of a place in the pinned objects dialog that already holds pins.
	# Placeholders are the place's name and how many pins it holds.
	return ngettext(
		"{place} (holds {count} pin, will share)",
		"{place} (holds {count} pins, will share)",
		holds,
	).format(place=name, count=holds)


def managePins(plugin) -> bool:
	"""Put the dialog up for the navigator object, or say why there is nothing it could do.

	The navigator object is taken here, before the dialog moves it. **Opened from the event loop
	and not from the script**, for the reason `flowTableDesigner.arrangeTheTable` gives.

	:param plugin: the global plugin.
	:return: whether the dialog is opening.
	"""
	if plugin.container is None:
		# Translators: reported when a command needs segments but none are configured.
		ui.message(_("BrlMultiline is not active"))
		return False
	manager = PinManager(plugin, api.getNavigatorObject())
	if not manager.pins() and not manager.targets():
		# Translators: reported when the pinned objects dialog is asked for and no segment can take a pin,
		# because every segment follows the focus or is in use, and nothing is pinned to release.
		ui.message(_("There is nowhere to pin an object, and nothing is pinned"))
		return False
	if not CAN_DRAW:
		log.debugWarning("There is no wx to open the pinned objects dialog with")
		return False
	wx.CallAfter(_show, manager)
	return True


def _show(manager: PinManager) -> None:
	try:
		gui.mainFrame.prePopup()
		try:
			dialog = PinDialog(gui.mainFrame, manager)
			try:
				chosen = dialog.ShowModal() == wx.ID_OK
			finally:
				dialog.Destroy()
		finally:
			gui.mainFrame.postPopup()
		if chosen:
			manager.pinChosen()
	except Exception:
		log.debugWarning("Could not manage the pinned objects", exc_info=True)


CAN_DRAW = wx is not None and gui is not None and hasattr(wx, "Dialog")


if CAN_DRAW:  # pragma: no cover - a dialog needs a display.

	class PinDialog(wx.Dialog):
		"""The object and where to pin it, then the pins there are and what can be done with them.

		Pin is the default button, so the reader can type a number and press Enter. It closes the
		dialog, and the pin is made once the dialog has gone. Everything else is done at once and
		keeps it open.
		"""

		def __init__(self, parent, manager: PinManager) -> None:
			# Translators: the title of the dialog for pinning objects to segments.
			super().__init__(parent, title=_("Pinned objects"))
			self.manager = manager
			self._targets: list[Target] = []
			self._pinKeys: list[str] = []
			main = wx.BoxSizer(wx.VERTICAL)
			helper = guiHelper.BoxSizerHelper(self, sizer=main)
			self.objectCtrl = helper.addLabeledControl(
				# Translators: the label of the name of the object the pinned objects dialog will pin.
				_("Object to pin:"),
				wx.TextCtrl,
				value=manager.objectName(),
				# Multi-line, since wx leaves a single-line read-only field out of the tab order.
				style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_NO_VSCROLL,
				size=(400, -1),
			)
			self.segmentCtrl = helper.addLabeledControl(
				# Translators: the label of the chooser of a segment or place in the pinned objects dialog.
				_("&Segment to pin it to:"),
				wx.ComboBox,
				choices=[],
				style=wx.CB_DROPDOWN,
			)
			self.segmentCtrl.Bind(wx.EVT_COMBOBOX, self._onTargetChanged)
			self.segmentCtrl.Bind(wx.EVT_TEXT, self._onTargetChanged)
			self.sizeCtrl = helper.addLabeledControl(
				# Translators: the label of the size of a new pin in a place, in the pinned objects dialog.
				_("Si&ze:"),
				wx.ComboBox,
				choices=[],
				style=wx.CB_DROPDOWN,
			)
			self.orderCtrl = helper.addLabeledControl(
				# Translators: the label of where a new pin goes among the pins already in a place.
				_("&Before or after the pins there:"),
				wx.Choice,
				choices=[
					# Translators: a new pin goes after the pins already in a place.
					_("After them"),
					# Translators: a new pin goes before the pins already in a place.
					_("Before them"),
				],
			)
			self.orderCtrl.SetSelection(0)
			# Translators: the button that pins the object where chosen and closes the dialog.
			self.pinButton = helper.addItem(wx.Button(self, label=_("&Pin")))
			self.pinButton.SetDefault()
			self.pinButton.Bind(wx.EVT_BUTTON, self._onPin)
			self.pinsList = helper.addLabeledControl(
				# Translators: the label of the list of pinned objects.
				_("Pinned &objects:"),
				wx.ListBox,
				choices=[],
				size=(400, 150),
			)
			self.pinsList.Bind(wx.EVT_LISTBOX, self._onPinSelected)
			self.newSizeCtrl = helper.addLabeledControl(
				# Translators: the label of a new size for the selected pin, in the pinned objects dialog.
				_("&New size:"),
				wx.ComboBox,
				choices=[],
				style=wx.CB_DROPDOWN,
			)
			# Translators: the button that gives the selected pin the new size.
			self.resizeButton = helper.addItem(wx.Button(self, label=_("Res&ize")))
			self.resizeButton.Bind(wx.EVT_BUTTON, self._onResize)
			# Translators: the button that moves the selected pin earlier in its place.
			self.upButton = helper.addItem(wx.Button(self, label=_("Move &up")))
			self.upButton.Bind(wx.EVT_BUTTON, lambda event: self._onMove(-1))
			# Translators: the button that moves the selected pin later in its place.
			self.downButton = helper.addItem(wx.Button(self, label=_("Move &down")))
			self.downButton.Bind(wx.EVT_BUTTON, lambda event: self._onMove(1))
			# Translators: the button that stops showing the selected pinned object.
			self.releaseButton = helper.addItem(wx.Button(self, label=_("&Release")))
			self.releaseButton.Bind(wx.EVT_BUTTON, self._onRelease)
			# Translators: the check box that sets every pin aside for now, or shows them again.
			self.asideCtrl = helper.addItem(wx.CheckBox(self, label=_("Set pins &aside for now")))
			self.asideCtrl.Bind(wx.EVT_CHECKBOX, self._onAside)
			helper.addDialogDismissButtons(wx.CLOSE)
			self.SetEscapeId(wx.ID_CLOSE)
			self.SetSizerAndFit(main)
			self._refresh()
			if manager.obj is not None and self.segmentCtrl.GetCount():
				self.segmentCtrl.SetFocus()
			else:
				self.pinsList.SetFocus()
			self.CentreOnParent()

		def _refresh(self) -> None:
			"""Show where to pin and the pins again, since anything done here changes both."""
			self._targets = self.manager.targets()
			names = [target.name for target in self._targets]
			typed = self.segmentCtrl.GetValue()
			self.segmentCtrl.Set(names)
			if typed in names:
				self.segmentCtrl.SetValue(typed)
			elif names:
				self.segmentCtrl.SetSelection(0)
			canPin = self.manager.obj is not None
			self.segmentCtrl.Enable(canPin)
			self.pinButton.Enable(canPin)
			self._onTargetChanged()
			selected = self.pinsList.GetSelection()
			pins = self.manager.pins()
			self._pinKeys = [key for key, _line in pins]
			self.pinsList.Set([line for _key, line in pins])
			if pins:
				self.pinsList.SetSelection(min(max(selected, 0), len(pins) - 1))
			self.releaseButton.Enable(bool(pins))
			self._onPinSelected()
			plugin = self.manager.plugin
			self.asideCtrl.SetValue(plugin.arePinsAside)
			self.asideCtrl.Enable(plugin.arePinsAside or bool(plugin.pins))

		def _target(self) -> Target | None:
			text = self.segmentCtrl.GetValue().strip().casefold()
			return next((target for target in self._targets if target.name.casefold() == text), None)

		def _onTargetChanged(self, event=None) -> None:
			"""Offer a size, and where among its pins, only for a place."""
			target = self._target()
			isPlace = target is not None and target.isPlace
			_offer(self.sizeCtrl, self.manager.sizeChoicesFor(target))
			self.sizeCtrl.Enable(isPlace)
			self.orderCtrl.Enable(isPlace and target.holds > 0)

		def _selectedKey(self) -> str | None:
			index = self.pinsList.GetSelection()
			return self._pinKeys[index] if 0 <= index < len(self._pinKeys) else None

		def _onPinSelected(self, event=None) -> None:
			"""Offer a new size and moving only for a pin in a place."""
			key = self._selectedKey()
			inPlace = key is not None and self.manager.isInPlace(key)
			if inPlace:
				self.newSizeCtrl.Set(self.manager.resizeChoicesFor(key))
				self.newSizeCtrl.SetValue(self.manager.sizeOf(key))
			for control in (self.newSizeCtrl, self.resizeButton, self.upButton, self.downButton):
				control.Enable(inPlace)

		def _onPin(self, event) -> None:
			why = self.manager.choose(
				self.segmentCtrl.GetValue(),
				self.sizeCtrl.GetValue(),
				atStart=self.orderCtrl.GetSelection() == 1,
			)
			if why:
				gui.messageBox(why, _("Pinned objects"), wx.OK | wx.ICON_ERROR, self)
				self.segmentCtrl.SetFocus()
				return
			self.EndModal(wx.ID_OK)

		def _onResize(self, event) -> None:
			key = self._selectedKey()
			if key is None:
				return
			why = self.manager.resize(key, self.newSizeCtrl.GetValue())
			if why:
				gui.messageBox(why, _("Pinned objects"), wx.OK | wx.ICON_ERROR, self)
				self.newSizeCtrl.SetFocus()
				return
			self._refresh()
			self._selectKey(key)
			self.pinsList.SetFocus()

		def _onMove(self, by: int) -> None:
			key = self._selectedKey()
			if key is None or not self.manager.move(key, by):
				return
			self._refresh()
			self._selectKey(key)
			self.pinsList.SetFocus()

		def _selectKey(self, key: str) -> None:
			if key in self._pinKeys:
				self.pinsList.SetSelection(self._pinKeys.index(key))
				self._onPinSelected()

		def _onRelease(self, event) -> None:
			index = self.pinsList.GetSelection()
			if not 0 <= index < len(self._pinKeys):
				return
			self.manager.release(self._pinKeys[index])
			self._refresh()
			if self._pinKeys:
				self.pinsList.SetSelection(min(index, len(self._pinKeys) - 1))
				self._onPinSelected()
				self.pinsList.SetFocus()
			elif self.segmentCtrl.IsEnabled():
				self.segmentCtrl.SetFocus()
			else:
				self.FindWindowById(wx.ID_CLOSE).SetFocus()

		def _onAside(self, event) -> None:
			self.manager.setAside(self.asideCtrl.IsChecked())
			self._refresh()

	def _offer(control, choices: list[str]) -> None:
		"""Give a size box new choices, keeping what it shows only if it is still one of them.

		So a size chosen for a place of rows is not left showing for a place of cells.
		"""
		if [control.GetString(index) for index in range(control.GetCount())] == choices:
			return
		shown = control.GetValue()
		control.Set(choices)
		control.SetValue(shown if shown in choices else choices[0])

else:

	class PinDialog:
		"""What the dialog is where there is no wx to build one with."""

		def __init__(self, parent, manager: PinManager) -> None:
			raise RuntimeError("The pinned objects dialog needs wx")


__all__ = ["PinDialog", "PinManager", "Target", "managePins"]
