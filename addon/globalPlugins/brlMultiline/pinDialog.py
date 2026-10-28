# BrlMultiline: the pinned objects dialog.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Where a reader pins the navigator object to a segment, or releases a pin, with one key.

The per segment commands need a key for every segment, which a display such as the Monarch does
not have to spare. See docs/design/port-plan.md, The pinned objects dialog.

`PinManager` knows nothing about wx and is tested without a display; the dialog is widgets, the
split `flowTableManager` makes.
"""

import re

import api
import ui
from logHandler import log

from .objectMonitor import ObjectMonitor

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


class PinManager:
	"""The segments the object can be pinned to, the pins there are, and the reader's choice.

	Segments are held by key, so a rebuild while the dialog is up cannot turn a choice into
	another segment.
	"""

	def __init__(self, plugin, obj) -> None:
		"""
		:param plugin: the global plugin, which holds the pins.
		:param obj: the object to pin, taken before the dialog took the navigator, or None.
		"""
		self.plugin = plugin
		self.obj = obj
		self.chosenKey: str | None = None
		"""The segment chosen to pin to, pinned by `pinChosen` once the dialog has closed."""

	def objectName(self) -> str:
		""":return: what to call the object to pin."""
		if self.obj is None:
			# Translators: shown in the pinned objects dialog when there is no navigator object to pin.
			return _("None")
		return ObjectMonitor.describe(self.obj)

	def targets(self) -> list[tuple[str, str]]:
		""":return: the key and name of each segment the object can be pinned to, in display order."""
		container = self.plugin.container
		if container is None or self.obj is None:
			return []
		return [
			(container.segments[number].key, self.plugin.describeSegment(number))
			for number in self.plugin.pinTargets()
		]

	def pins(self) -> list[tuple[str, str]]:
		""":return: the key of each pin's segment and a line saying what is pinned where, in display order."""
		container = self.plugin.container
		if container is None:
			return []
		found = []
		for key, monitor in self.plugin.pins.items():
			try:
				number = container.numberForKey(key)
			except LookupError:
				continue
			# Translators: one line in the list of pinned objects. Placeholders are the object's
			# name and the segment it is pinned to, such as "Segment 2".
			line = _("{name}: {segment}").format(
				name=monitor.name, segment=self.plugin.describeSegment(number)
			)
			found.append((number, key, line))
		return [(key, line) for _number, key, line in sorted(found)]

	def choose(self, text: str) -> str | None:
		"""Take the segment the reader chose from the list or typed.

		:param text: a segment's name from `targets`, or a segment number.
		:return: why that is no segment to pin to, to be shown, or None once it is chosen.
		"""
		self.chosenKey = None
		if self.obj is None:
			# Translators: reported when there is no object to pin.
			return _("No object to monitor")
		text = text.strip()
		for key, name in self.targets():
			if text.casefold() == name.casefold():
				self.chosenKey = key
				return None
		found = re.search(r"\d+", text)
		if found is None:
			# Translators: shown when the reader pressed Pin without choosing a segment or typing its number.
			return _("Choose a segment, or type its number.")
		number = int(found.group())
		container = self.plugin.container
		if container is None:
			# Translators: reported when a command needs segments but none are configured.
			return _("BrlMultiline is not active")
		if number >= container.numSegments:
			# Translators: reported when a command names a segment that does not exist.
			# The placeholder is replaced with the segment number.
			return _("There is no segment {number}").format(number=number)
		key = container.segments[number].key
		if key in self.plugin.pins:
			# Translators: shown when the reader chose a segment that already holds a pinned object.
			# The placeholder is the segment number.
			return _("Segment {number} already holds a pinned object. Release it first.").format(
				number=number
			)
		refusal = self.plugin.pinRefusal(container, number)
		if refusal is not None:
			return refusal
		self.chosenKey = key
		return None

	def pinChosen(self) -> None:
		"""Pin the object to the chosen segment, wherever that segment is now."""
		container = self.plugin.container
		if self.chosenKey is None or container is None:
			return
		try:
			number = container.numberForKey(self.chosenKey)
		except LookupError:
			# Translators: reported when the segment chosen in the pinned objects dialog is no longer
			# on the display, because the layout changed while the dialog was open.
			ui.message(_("That segment is no longer on the display"))
			return
		self.plugin.startMonitoring(number, self.obj)

	def release(self, key: str) -> None:
		"""Release the pin on a segment, saying so as the release command does.

		:param key: the segment's key, from `pins`.
		"""
		container = self.plugin.container
		if container is None:
			return
		try:
			number = container.numberForKey(key)
		except LookupError:
			return
		self.plugin.stopMonitoring(number)


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
	if not manager.pins() and not plugin.pinTargets():
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
		"""The object and a segment to pin it to, then the pins there are and a button to release one.

		Pin is the default button, so the reader can type a number and press Enter. It closes the
		dialog, and the pin is made once the dialog has gone. Release keeps it open.
		"""

		def __init__(self, parent, manager: PinManager) -> None:
			# Translators: the title of the dialog for pinning objects to segments.
			super().__init__(parent, title=_("Pinned objects"))
			self.manager = manager
			self._pinKeys: list[str] = []
			main = wx.BoxSizer(wx.VERTICAL)
			helper = guiHelper.BoxSizerHelper(self, sizer=main)
			self.objectCtrl = helper.addLabeledControl(
				# Translators: the label of the name of the object the pinned objects dialog will pin.
				_("Object to pin:"),
				wx.TextCtrl,
				value=manager.objectName(),
				style=wx.TE_READONLY,
				size=(400, -1),
			)
			self.segmentCtrl = helper.addLabeledControl(
				# Translators: the label of the segment chooser in the pinned objects dialog.
				_("&Segment to pin it to:"),
				wx.ComboBox,
				choices=[],
				style=wx.CB_DROPDOWN,
			)
			# Translators: the button that pins the object to the chosen segment and closes the dialog.
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
			# Translators: the button that stops showing the selected pinned object.
			self.releaseButton = helper.addItem(wx.Button(self, label=_("&Release")))
			self.releaseButton.Bind(wx.EVT_BUTTON, self._onRelease)
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
			"""Show the segments and pins again, since releasing a pin frees its segment."""
			names = [name for _key, name in self.manager.targets()]
			typed = self.segmentCtrl.GetValue()
			self.segmentCtrl.Set(names)
			if typed in names:
				self.segmentCtrl.SetValue(typed)
			elif names:
				self.segmentCtrl.SetSelection(0)
			canPin = self.manager.obj is not None
			self.segmentCtrl.Enable(canPin)
			self.pinButton.Enable(canPin)
			pins = self.manager.pins()
			self._pinKeys = [key for key, _line in pins]
			self.pinsList.Set([line for _key, line in pins])
			if pins:
				self.pinsList.SetSelection(0)
			self.releaseButton.Enable(bool(pins))

		def _onPin(self, event) -> None:
			why = self.manager.choose(self.segmentCtrl.GetValue())
			if why:
				gui.messageBox(why, _("Pinned objects"), wx.OK | wx.ICON_ERROR, self)
				self.segmentCtrl.SetFocus()
				return
			self.EndModal(wx.ID_OK)

		def _onRelease(self, event) -> None:
			index = self.pinsList.GetSelection()
			if not 0 <= index < len(self._pinKeys):
				return
			self.manager.release(self._pinKeys[index])
			self._refresh()
			if self._pinKeys:
				self.pinsList.SetSelection(min(index, len(self._pinKeys) - 1))
				self.pinsList.SetFocus()
			elif self.segmentCtrl.IsEnabled():
				self.segmentCtrl.SetFocus()
			else:
				self.FindWindowById(wx.ID_CLOSE).SetFocus()

else:

	class PinDialog:
		"""What the dialog is where there is no wx to build one with."""

		def __init__(self, parent, manager: PinManager) -> None:
			raise RuntimeError("The pinned objects dialog needs wx")


__all__ = ["PinDialog", "PinManager", "managePins"]
