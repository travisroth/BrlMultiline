# BrlMultiline: the commands that turn layers of keys on and off.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The layered keys commands, and keeping the layers in step with what is on the display.

The plugin's scripts call into this; which keys a layer holds and deciding each key is
`keyLayerDispatch`'s. See docs/design/layered-keys-plan.md.
"""

import contextlib

import addonHandler
import braille
import gui
import ui
import wx
from logHandler import log

from . import keyLayerContexts, keyLayerDispatch, keyLayers
from .devices import deviceMap
from .settingsPanel import displayDescriptions

addonHandler.initTranslation()


class KeyLayerCommands:
	"""The layered keys commands, for the plugin's scripts."""

	def __init__(self, plugin):
		"""
		:param plugin: the global plugin, for what is on its display.
		"""
		self.plugin = plugin
		self._changesToSay: list | None = None
		"""While a command is putting a drawing up or taking it off, the layers that came or went with
		it, to say in its message. See `collectingChanges`."""

	@contextlib.contextmanager
	def collectingChanges(self):
		"""Collect the layers that come or go while a command runs, rather than saying them.

		:return: a context whose value is the list they are added to, to read once it ends.
		"""
		self._changesToSay = changes = []
		try:
			yield changes
		finally:
			self._changesToSay = None

	def reconcile(self) -> None:
		"""Turn off the layers whose drawing has gone, turn on the ones that come on by themselves for
		what is drawn now, and say so. See `keyLayerDispatch.reconcile`.

		Also after a profile switch and a display reconnecting, which is why it is not only for graphics
		changes. Tables are not looked for here; see `keyLayerContexts`.
		"""
		try:
			present = keyLayerContexts.presentContexts(self.plugin, tables=False)
			ended, started = keyLayerDispatch.reconcile(
				present, self.devices(), keyLayerContexts.DETECTED
			)
		except Exception:
			log.error("BrlMultiline: could not change the layers for a drawing", exc_info=True)
			return
		words = []
		for _device, layer in ended:
			# Translators: reported when a layer of keys is turned off. The placeholder is its name.
			said = _("{name} layer off").format(name=keyLayerDispatch.layerName(layer))
			if said not in words:
				words.append(said)
		for _device, layer in started:
			# Translators: reported when a layer of keys is turned on. The placeholder is its name.
			said = _("{name} layer on").format(name=keyLayerDispatch.layerName(layer))
			if said not in words:
				words.append(said)
		if not words:
			return
		if self._changesToSay is not None:
			self._changesToSay.extend(words)
		else:
			# After whatever the command that changed the drawing says, rather than before it: a chart
			# is described and then its layer is named, not the other way round.
			wx.CallAfter(ui.message, ", ".join(words))

	def reload(self) -> None:
		"""Read the layered keys the new profile holds, and say which layer that turned off.

		A layer that is on stays on if the new profile still has it; otherwise it goes off and
		the reader is told.
		"""
		try:
			gone = keyLayerDispatch.reload()
		except Exception:
			log.error("BrlMultiline: could not read the layered keys after a profile switch", exc_info=True)
			return
		for _device, layer in gone:
			# Translators: reported when a profile switch removes the layer of keys that was on. The
			# placeholder is its name.
			ui.message(_("{name} layer off").format(name=keyLayerDispatch.layerName(layer)))

	def devices(self) -> list[str]:
		""":return: the devices a layer can come on for: every connected braille display, and the keyboard."""
		names = [info.driverName for info in deviceMap()]
		if not names:
			display = braille.handler.display if braille.handler else None
			if display is not None and display.name != "noBraille":
				names = [display.name]
		return [*names, keyLayers.KEYBOARD]

	def toggle(self, device: str | None) -> None:
		"""Turn a device's layer off, or turn on the one for what is on the display, and say which.

		:param device: a braille display's driver name, or `keyboard`.
		"""
		if device is None:
			# Translators: reported when the layer key is pressed on something that cannot have layers.
			ui.message(_("No layers for this key"))
			return
		isOn, layer = keyLayerDispatch.toggle(device, keyLayerContexts.presentContexts(self.plugin, device=device))
		name = keyLayerDispatch.layerName(layer)
		if isOn:
			# Translators: reported when a layer of keys is turned on. The placeholder is its name.
			ui.message(_("{name} layer on").format(name=name))
		else:
			# Translators: reported when a layer of keys is turned off. The placeholder is its name.
			ui.message(_("{name} layer off").format(name=name))

	def choose(self, device: str | None) -> None:
		"""Ask which of a device's layers to turn on, then turn it on.

		:param device: a braille display's driver name, or `keyboard`.
		"""
		if device is None:
			ui.message(_("No layers for this key"))
			return
		layers = list(keyLayerDispatch.layerSet(device).layers)
		active = keyLayerDispatch.activeLayer(device)
		current = next(
			(index for index, layer in enumerate(layers) if active is not None and layer.id == active.id),
			0,
		)
		deviceName = self._deviceName(device)

		def ask():
			gui.mainFrame.prePopup()
			try:
				dialog = wx.SingleChoiceDialog(
					gui.mainFrame,
					# Translators: the message of a dialog asking which layer of keys to turn on. The
					# placeholder is the display, or keyboard.
					_("Which layer should be on for {device}?").format(device=deviceName),
					# Translators: the title of a dialog asking which layer of keys to turn on.
					_("Choose a layer"),
					[keyLayerDispatch.layerName(layer) for layer in layers],
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
			if 0 <= chosen < len(layers):
				layer = keyLayerDispatch.activate(device, layers[chosen].id)
				ui.message(_("{name} layer on").format(name=keyLayerDispatch.layerName(layer)))

		wx.CallAfter(ask)

	def report(self) -> None:
		"""Say which layers are on, and on which devices."""
		on = keyLayerDispatch.activeLayers()
		if not on:
			# Translators: reported when no layer of keys is on.
			ui.message(_("No layers on"))
			return
		ui.message(
			", ".join(
				# Translators: one layer of keys that is on, in a report of them. Placeholders are
				# the layer's name and the display it is on, or keyboard.
				_("{name} layer on {device}").format(
					name=keyLayerDispatch.layerName(layer),
					device=self._deviceName(device),
				)
				for device, layer in on
			),
		)

	def turnAllOff(self) -> None:
		"""Turn every layer off, and say so."""
		if keyLayerDispatch.allOff():
			# Translators: reported when every layer of keys is turned off.
			ui.message(_("All layers off"))
		else:
			ui.message(_("No layers on"))

	def _deviceName(self, device: str) -> str:
		""":return: what to call a device with layers when speaking of it."""
		if device == keyLayers.KEYBOARD:
			# Translators: the keyboard, as a device that has layers of keys.
			return _("keyboard")
		return displayDescriptions().get(device, device)
