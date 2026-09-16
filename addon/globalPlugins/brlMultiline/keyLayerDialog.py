# BrlMultiline: the layered keys dialog.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Where a reader edits the layers of keys each display and the keyboard has.

Built to look and work like NVDA's Input Gestures dialog, because that is the dialog every reader
already knows for this job: a filter, a tree of categories, commands and keys, and Add, Change and
Remove. Above it, which device and which layer is being edited. See the layered keys plan's
section on the dialog for every difference from Input Gestures and the reason for each.

**The editor is a plain object and the dialog is a shell over it**, the split `flowTableDesigner`
makes. `LayerEditor` knows nothing about wx: it holds the working copy of every device's layers,
builds the tree, refuses and asks about the keys that need refusing or asking about, and commits.
Everything that decides anything is tested without a display. What is left to the dialog is
widgets, capturing a key, and asking the reader a question.
"""

import re
from typing import Callable, Iterable, NamedTuple, Optional

from logHandler import log

from . import keyLayers
from .keyLayers import DEFAULT_ID, KEYBOARD, STYLES, Layer, LayerSet, LayerSetError, Target

try:
	import wx
	from wx.lib.mixins.treemixin import VirtualTree

	import config
	import gui
	import inputCore
	from gui import guiHelper
	from gui.settingsDialogs import SettingsDialog
except ImportError:  # pragma: no cover - only in a test run with no NVDA around it.
	wx = None


class Device(NamedTuple):
	"""A device the dialog can edit the layers of."""

	name: str
	"""A braille display's driver name, or `keyboard`."""

	label: str
	connected: bool
	"""Whether it is here to press keys on. One that is not can be reviewed and cleared, not given keys."""


class Command(NamedTuple):
	"""Something a key in a layer can do, as the tree shows it."""

	category: str
	name: str
	target: Target
	"""With no display it acts for: that is the binding's business, not the command's."""


class KeyNode(NamedTuple):
	identifier: Optional[str]
	"""None for the prompt shown while a key is being waited for."""

	name: str
	fromLayer: Optional[str] = None
	"""The name of the layer further down the chain the key comes from, or None for this layer's own."""


class CommandNode(NamedTuple):
	command: Command
	keys: list


class CategoryNode(NamedTuple):
	name: str
	commands: list


def identity(target: Target) -> tuple:
	""":return: what makes two targets the same command, whichever display a binding acts for."""
	return (target.kind, target.moduleName, target.className, target.scriptName)


# Translators: the category of the layered keys dialog holding keys bound to commands that are not
# available where the dialog was opened from, such as a browse mode command opened from the desktop.
UNAVAILABLE = _("Unavailable from here")

# Translators: the prompt shown in the layered keys dialog while it waits for a key to be pressed.
PROMPT = _("Enter input gesture:")

PROMPT_FOCUS = "<prompt>"
"""Where the dialog is to put the reader after Add: on the prompt under the command, which is a key
row like any other and so needs its command expanded like any other. A value no key identifier can
be, since identifiers always carry a source and a colon."""

CONTEXT_LABELS = {
	# Translators: a layer of keys that is for a chart on the display.
	"chart": _("Chart"),
	# Translators: a layer of keys that is for a picture on the display.
	"picture": _("Picture"),
	# Translators: a layer of keys that is for any drawing on the display.
	"graphics": _("Graphics"),
	# Translators: a layer of keys that is for a table.
	"table": _("Table"),
	# Translators: a layer of keys that is for lines shown unwrapped, one to a row, and panned across.
	"unwrapped": _("Unwrapped lines"),
}

STYLE_LABELS = {
	# Translators: a layer of keys that turns off after the next key.
	keyLayers.ONE_SHOT: _("One shot: off after the next key"),
	# Translators: a layer of keys that stays on until it is turned off.
	keyLayers.STAYS_ON: _("Stays on until turned off"),
}


class Check(NamedTuple):
	"""What adding a key to a command needs first."""

	refusal: Optional[str] = None
	"""Why it cannot be added at all."""

	conflict: Optional[str] = None
	"""The command the key already runs in this layer, to ask before moving it."""

	conflictLayer: Optional[str] = None
	"""Where the key's command comes from when that is a layer further down, whose key is not moved but
	overridden in this one."""


class LayerEditor:
	"""The working copy of every device's layers, and everything the dialog does to them."""

	def __init__(
		self,
		devices: list,
		readLayers: Callable[[str], LayerSet],
		factoryLayers: Callable[[str], LayerSet],
		commands: Iterable[Command],
		describeKey: Callable[[str], str] = lambda identifier: identifier,
		nameLayer: Callable[[Layer], str] = lambda layer: layer.name or layer.id,
		emulatedCategory: str = "Emulated system keyboard keys",
		blockedCategory: str = "BrlMultiline",
		reservedKeys: Iterable[str] = (),
		device: Optional[str] = None,
	):
		"""
		:param devices: the devices to offer, in the order to offer them.
		:param readLayers: a device's layers as saved, from `keyLayerDispatch.layerSet`.
		:param factoryLayers: a device's shipped layers, for resetting to factory defaults.
		:param commands: every command the tree offers.
		:param describeKey: a key's name as Input Gestures gives it.
		:param nameLayer: a layer's name as it is spoken, `keyLayerDispatch.layerName`.
		:param emulatedCategory: NVDA's category for emulated keyboard keys.
		:param blockedCategory: where the command that does nothing is listed.
		:param reservedKeys: keys bound to the layered keys commands, which a layer may not take.
		:param device: the device to start on, or None for the first.
		"""
		if not devices:
			raise ValueError("there is no device to edit")
		self.devices = list(devices)
		self._readLayers = readLayers
		self._factoryLayers = factoryLayers
		self.commands = list(commands)
		self.describeKey = describeKey
		self.nameLayer = nameLayer
		self.emulatedCategory = emulatedCategory
		self.blockedCategory = blockedCategory
		self.reservedKeys = frozenset(keyLayers.normalize(key) for key in reservedKeys)
		self._sets: dict = {}
		self._dirty: set = set()
		self._emulated: dict = {}
		"""Device to emulated keys added this session with no key bound to them yet."""
		self.device = self.devices[0].name
		self.layerId = DEFAULT_ID
		self.filterText = ""
		self.onlyBound = False
		self.pending: Optional[tuple] = None
		"""The command a key is being waited for, by identity, so the tree can show the prompt."""
		if device is not None:
			self.selectDevice(device)

	# Selection

	@property
	def current(self) -> Device:
		return next(device for device in self.devices if device.name == self.device)

	@property
	def layers(self) -> LayerSet:
		found = self._sets.get(self.device)
		if found is None:
			found = self._sets[self.device] = self._readLayers(self.device)
		return found

	@property
	def layer(self) -> Layer:
		return self.layers.get(self.layerId) or self.layers.default

	def selectDevice(self, name: str) -> None:
		if not any(device.name == name for device in self.devices):
			raise LookupError(name)
		self.device = name
		self.layerId = DEFAULT_ID
		self.pending = None

	def selectLayer(self, layerId: str) -> None:
		if self.layers.get(layerId) is None:
			raise LookupError(layerId)
		self.layerId = layerId
		self.pending = None

	def deviceLabel(self, name: Optional[str]) -> str:
		for device in self.devices:
			if device.name == name:
				return device.label
		return name or ""

	@property
	def isDirty(self) -> bool:
		return bool(self._dirty)

	# The tree

	def categories(self) -> list:
		""":return: the tree for the layer being edited, filtered as the reader has asked.

		Keys the layer gets from further down its chain are listed under their commands too, each saying
		which layer it comes from, because a stack the reader cannot see is one they have to hold in their
		head. Every key the layer answers to is in the tree, and nothing it does not answer to is.
		"""
		bound: dict = {}
		for identifier, target in sorted(self.layer.bindings.items()):
			bound.setdefault(identity(target), []).append((identifier, target, None))
		for identifier, (target, below) in sorted(self.layers.inherited(self.layer.id).items()):
			bound.setdefault(identity(target), []).append((identifier, target, below))
		known = {}
		for command in self._allCommands():
			known.setdefault(identity(command.target), command)
		for key, pairs in bound.items():
			if key in known:
				continue
			target = replace_(pairs[0][1], actsFor=None)
			if target.kind == keyLayers.KEY:
				# An emulated key is always available: it is a key, not a command that needs an object.
				known[key] = Command(self.emulatedCategory, self.describeKey(target.scriptName), target)
			else:
				known[key] = Command(UNAVAILABLE, self._unavailableName(target), target)
		pattern = self._pattern()
		byCategory: dict = {}
		for key, command in known.items():
			keys = [
				self._keyNode(identifier, target, below) for identifier, target, below in bound.get(key, [])
			]
			if self.pending == key:
				keys.append(KeyNode(None, PROMPT))
			if self.onlyBound and not keys:
				continue
			if pattern is not None and not pattern.search(command.name):
				continue
			byCategory.setdefault(command.category, []).append(CommandNode(command, keys))
		return [
			CategoryNode(name, sorted(nodes, key=lambda node: node.command.name.casefold()))
			for name, nodes in sorted(byCategory.items(), key=lambda item: item[0].casefold())
		]

	def _allCommands(self) -> list:
		blocked = Command(
			self.blockedCategory,
			# Translators: a command in the layered keys dialog that makes a key do nothing in a layer.
			_("Does nothing in this layer"),
			Target.blocked(),
		)
		return [*self.commands, blocked, *self._emulated.get(self.device, [])]

	def _pattern(self):
		if not self.filterText.strip():
			return None
		words = [re.escape(word) for word in self.filterText.split()]
		return re.compile("".join(f"(?=.*?{word})" for word in words), re.IGNORECASE)

	def _keyNode(self, identifier: str, target: Target, below: Optional[Layer]) -> KeyNode:
		name = self._keyName(identifier, target)
		if below is None:
			return KeyNode(identifier, name)
		fromLayer = self.nameLayer(below)
		# Translators: a key in the layered keys dialog that the layer being edited gets from a layer further
		# down. Placeholders are the key's name and that layer's name.
		return KeyNode(
			identifier, _("{key} (from {layer} layer)").format(key=name, layer=fromLayer), fromLayer
		)

	def _keyName(self, identifier: str, target: Target) -> str:
		name = self.describeKey(identifier)
		if target.actsFor:
			# Translators: a key in the layered keys dialog that acts for a display. Placeholders are the
			# key's name and the display.
			return _("{key}, acting for {display}").format(key=name, display=self.deviceLabel(target.actsFor))
		return name

	def _unavailableName(self, target: Target) -> str:
		return f"{target.scriptName} ({target.className})"

	# Keys

	def check(self, command: Command, identifier: str) -> Check:
		""":return: whether a key can go to a command in the layer being edited, and what to ask first."""
		identifier = keyLayers.normalize(identifier)
		if identifier in self.reservedKeys:
			# Translators: why a key cannot be added to a layer: it turns layers on and off.
			return Check(refusal=_("That key turns layers on and off, so no layer can use it"))
		if identifier in self.layer.exitKeys:
			# Translators: why a key cannot be added to a layer: it is one of the keys that leave it.
			return Check(
				refusal=_("That key leaves this layer. Remove it from the exit keys in Properties first")
			)
		existing = self.layer.bindings.get(identifier)
		if existing is not None and identity(existing) != identity(command.target):
			return Check(conflict=self._nameOf(existing))
		inherited = self.layers.inherited(self.layer.id).get(identifier)
		if inherited is not None and identity(inherited[0]) != identity(command.target):
			return Check(conflict=self._nameOf(inherited[0]), conflictLayer=self.nameLayer(inherited[1]))
		return Check()

	def _nameOf(self, target: Target) -> str:
		for command in self._allCommands():
			if identity(command.target) == identity(target):
				return command.name
		return self._unavailableName(target)

	def add(
		self,
		command: Command,
		identifier: str,
		actsFor: Optional[str] = None,
		replacing: Optional[str] = None,
	) -> None:
		"""Bind a key to a command in the layer being edited, replacing what it did in this layer.

		:param replacing: the key this one takes the place of, for Change. Removed in the same edit, so
			a Change that is cancelled or refused before it gets here leaves the old key where it was.
		:raises ValueError: if L{check} refuses the key. Ask first; this does not.
		"""
		identifier = keyLayers.normalize(identifier)
		refusal = self.check(command, identifier).refusal
		if refusal:
			raise ValueError(refusal)
		bindings = dict(self.layer.bindings)
		if replacing is not None:
			bindings.pop(keyLayers.normalize(replacing), None)
		bindings[identifier] = replace_(command.target, actsFor=actsFor or None)
		self._keep(replace_(self.layer, bindings=bindings))
		self.pending = None
		if command.target.kind == keyLayers.KEY:
			self._emulated[self.device] = [
				each
				for each in self._emulated.get(self.device, [])
				if identity(each.target) != identity(command.target)
			]

	def remove(self, identifier: str) -> None:
		bindings = dict(self.layer.bindings)
		if bindings.pop(keyLayers.normalize(identifier), None) is not None:
			self._keep(replace_(self.layer, bindings=bindings))

	def block(self, identifier: str) -> None:
		"""Make a key do nothing in the layer being edited: how a key from further down is taken away here.

		A key from another layer cannot be removed from this one, because it is not in it; removing it
		from where it is would change that layer too. So it is overridden instead.
		"""
		bindings = dict(self.layer.bindings)
		bindings[keyLayers.normalize(identifier)] = Target.blocked()
		self._keep(replace_(self.layer, bindings=bindings))

	def addEmulatedKey(self, key: str) -> Command:
		""":return: the command for pressing a keyboard key, listed until a key is bound to it."""
		target = Target.key(keyLayers.normalize(key))
		for command in self._allCommands():
			if identity(command.target) == identity(target):
				return command
		command = Command(self.emulatedCategory, self.describeKey(target.scriptName), target)
		self._emulated.setdefault(self.device, []).append(command)
		return command

	def needsDisplay(self, command: Command) -> bool:
		""":return: whether a keyboard key for this command should be asked which display it acts for.

		Only for the keyboard, only when there is a display to name, and only for commands that can
		ask which display they are for: this add-on's own, a braille display driver's, and NVDA's
		braille commands. Asking of every command would be a question with no answer most of the time.
		"""
		if self.device != KEYBOARD or command.target.kind != keyLayers.SCRIPT:
			return False
		if not self.displays():
			return False
		target = command.target
		return (
			target.moduleName.endswith("brlMultiline")
			or target.moduleName.startswith("brailleDisplayDrivers")
			or target.scriptName.startswith("braille_")
		)

	def displays(self) -> list:
		""":return: the connected braille displays a keyboard binding could act for."""
		return [device for device in self.devices if device.name != KEYBOARD and device.connected]

	# Layers

	def newLayer(self, name: str) -> Layer:
		"""Add a layer as a reader creating one gets it, and edit it."""
		name = name.strip()
		base = re.sub(r"[^a-z0-9]+", "", name.casefold()) or "layer"
		layerId = base
		number = 2
		while layerId == DEFAULT_ID or self.layers.get(layerId) is not None:
			layerId = f"{base}{number}"
			number += 1
		layer = keyLayers.newLayer(self.device, layerId, name)
		self._keepSet(self.layers.withLayer(layer))
		self.layerId = layerId
		return layer

	def renameLayer(self, name: str) -> None:
		if self.layer.isDefault:
			raise ValueError("the default layer cannot be renamed")
		self._keep(replace_(self.layer, name=name.strip()))

	def deleteLayer(self) -> None:
		if self.layer.isDefault:
			raise ValueError("the default layer cannot be deleted")
		gone = self.layer.id
		layers = [
			replace_(layer, fallsThrough=None) if layer.fallsThrough == gone else layer
			for layer in self.layers.layers
			if layer.id != gone
		]
		self._keepSet(LayerSet(self.device, tuple(layers)))
		self.layerId = DEFAULT_ID

	def setProperties(
		self,
		name: str,
		context: Optional[str],
		autoEnable: bool,
		style: str,
		fallsThrough: Optional[str],
		exitKeys: Iterable[str],
	) -> Optional[str]:
		""":return: why the properties cannot be kept, or None when they have been."""
		if style not in STYLES:
			raise ValueError(style)
		layer = self.layer
		changes = {"style": style, "exitKeys": frozenset(keyLayers.normalize(key) for key in exitKeys)}
		if not layer.isDefault:
			changes.update(
				name=name.strip(),
				context=context or None,
				autoEnable=bool(autoEnable and context),
				fallsThrough=fallsThrough or None,
			)
		updated = self.layers.withLayer(replace_(layer, **changes))
		problems = updated.problems()
		if problems:
			# Translators: why a layer's properties cannot be kept. The placeholder says what is wrong.
			return _("These properties cannot be kept: {problem}").format(problem=problems[0])
		self._keepSet(updated)
		return None

	def fallThroughChoices(self) -> list:
		""":return: the layers the layer being edited could fall through to."""
		return [layer for layer in self.layers.layers if not layer.isDefault and layer.id != self.layer.id]

	def clearLayer(self) -> None:
		self._keep(replace_(self.layer, bindings={}))

	def resetToFactoryDefaults(self) -> None:
		"""Put the device's shipped layers back, in place of every layer it has."""
		self._keepSet(self._factoryLayers(self.device))
		self._emulated.pop(self.device, None)
		self.layerId = DEFAULT_ID
		self.pending = None

	def _keep(self, layer: Layer) -> None:
		self._keepSet(self.layers.withLayer(layer))

	def _keepSet(self, layers: LayerSet) -> None:
		self._sets[self.device] = layers
		self._dirty.add(self.device)

	# Saving

	def commit(self, save: Callable[[LayerSet], None]) -> list:
		"""Save every device whose layers were changed.

		:param save: `keyLayerDispatch.save`.
		:return: (device, reason) for each device that could not be saved. The rest are saved.
		"""
		failed = []
		for device in sorted(self._dirty):
			try:
				save(self._sets[device])
			except LayerSetError as error:
				failed.append((device, str(error)))
			except Exception as error:  # noqa: BLE001 - every device gets its own chance to save.
				log.error(f"BrlMultiline: could not save the layered keys for {device}", exc_info=True)
				failed.append((device, str(error)))
			else:
				self._dirty.discard(device)
		return failed


def replace_(value, **changes):
	""":return: a dataclass or named tuple with fields changed."""
	if hasattr(value, "_replace"):
		return value._replace(**changes)
	from dataclasses import replace

	return replace(value, **changes)


# Gathering what the dialog offers


def deviceChoices(connected: Iterable[str], stored: Iterable[str], labelOf: Callable[[str], str]) -> list:
	""":return: the devices to offer: connected displays, then the keyboard, then displays with saved
	layers that are not connected, marked as such."""
	connected = [name for name in connected if name and name not in (KEYBOARD, "noBraille")]
	choices = [Device(name, labelOf(name), True) for name in connected]
	# Translators: the keyboard, as a device in the layered keys dialog.
	choices.append(Device(KEYBOARD, _("Keyboard"), True))
	for name in sorted(stored):
		if name in connected or name in (KEYBOARD, "noBraille"):
			continue
		# Translators: a display in the layered keys dialog that has saved layers and is not connected.
		# The placeholder is its name.
		choices.append(Device(name, _("{display} (not connected)").format(display=labelOf(name)), False))
	return choices


def commandsFromMappings(mappings: dict, emulatedCategory: str) -> list:
	""":return: the commands Input Gestures would list, from `inputCore.manager.getAllGestureMappings`.

	The emulated keys category is left out: what it lists are the reader's own emulations in
	Input Gestures, and a layer's emulated keys are its own.
	"""
	commands = []
	for category, scripts in mappings.items():
		if category == emulatedCategory:
			continue
		for name, info in scripts.items():
			scriptName = getattr(info, "scriptName", "") or ""
			if scriptName.startswith("kb:"):
				continue
			commands.append(
				Command(category, name, Target.script(info.moduleName, info.className, scriptName))
			)
	return commands


def reservedFromMappings(mappings: dict, prefix: str) -> list:
	""":return: the keys bound to a layer key command, which no layer may take.

	The layer key, and the generated ones that turn layers on and off for a display by its position,
	`keyLayerToggleDisplay0` and on. A layer that bound one of those keys would win over the command
	meant to turn it off.
	"""
	toggle = f"{prefix}Toggle"
	keys = []
	for scripts in mappings.values():
		for info in scripts.values():
			name = getattr(info, "scriptName", "") or ""
			if name == toggle or (name.startswith(f"{toggle}Display") and name[len(toggle) + 7 :].isdigit()):
				keys.extend(getattr(info, "gestures", ()) or ())
	return keys


def memberCommands(members: Iterable[tuple], known: Iterable[Command], miscCategory: str) -> list:
	"""The commands of each display behind a combined display, which Input Gestures cannot list.

	NVDA gathers a display driver's commands from `braille.handler.display`, and through a composite
	that is the virtual display, so the Focus 80's own commands would never appear.

	:param members: (driver, label) for each member.
	:param known: commands already offered, so none is listed twice.
	:return: the commands, each named with its display.
	"""
	seen = {identity(command.target) for command in known}
	commands = []
	for driver, label in members:
		cls = type(driver)
		for owner in cls.__mro__:
			for attribute, script in vars(owner).items():
				if not attribute.startswith("script_") or not getattr(script, "__doc__", None):
					continue
				target = Target.script(cls.__module__, cls.__name__, attribute[len("script_") :])
				if identity(target) in seen:
					continue
				seen.add(identity(target))
				category = (
					getattr(script, "category", None) or getattr(cls, "scriptCategory", None) or miscCategory
				)
				# Translators: a display driver's command in the layered keys dialog. Placeholders are the
				# command and the display it belongs to.
				name = _("{command} ({display})").format(
					command=script.__doc__.splitlines()[0], display=label
				)
				commands.append(Command(category, name, target))
	return commands


def dialogTitle(profileName: Optional[str]) -> str:
	# Translators: the title of the layered keys dialog. The placeholder is the configuration profile
	# being edited.
	return _("Layered keys ({profile})").format(
		# Translators: the name NVDA gives its configuration when no profile is in use.
		profile=profileName or _("normal configuration"),
	)


if wx is not None:

	class _Tree(VirtualTree, wx.TreeCtrl):
		"""The categories, commands and keys, as NVDA's Input Gestures tree shows them."""

		def __init__(self, parent, dialog):
			self.dialog = dialog
			super().__init__(
				parent,
				size=wx.Size(600, 400),
				style=wx.TR_HAS_BUTTONS | wx.TR_HIDE_ROOT | wx.TR_LINES_AT_ROOT | wx.TR_SINGLE,
			)

		def OnGetChildrenCount(self, index):
			nodes = self.dialog.nodes
			if not index:
				return len(nodes)
			category = nodes[index[0]]
			if len(index) == 1:
				return len(category.commands)
			if len(index) == 2:
				return len(category.commands[index[1]].keys)
			return 0

		def OnGetItemText(self, index, column=0):
			category = self.dialog.nodes[index[0]]
			if len(index) == 1:
				return category.name
			command = category.commands[index[1]]
			if len(index) == 2:
				return command.command.name
			return command.keys[index[2]].name

		def selection(self) -> tuple:
			""":return: the category, command and key node selected, each None where not reached."""
			try:
				index = self.GetIndexOfItem(self.GetSelection())
			except Exception:
				return (None, None, None)
			nodes = self.dialog.nodes
			try:
				category = nodes[index[0]] if index else None
				command = category.commands[index[1]] if category is not None and len(index) > 1 else None
				key = command.keys[index[2]] if command is not None and len(index) > 2 else None
			except IndexError:
				return (None, None, None)
			return (category, command, key)

	class KeyLayersDialog(SettingsDialog):
		# Translators: the title of the layered keys dialog.
		title = _("Layered keys")
		helpId = "InputGestures"

		def __init__(self, parent, multiInstanceAllowed: bool = False):
			# OK and Cancel only, as Input Gestures has: an Apply button's accelerator would be the Add
			# button's.
			super().__init__(parent, resizeable=True, multiInstanceAllowed=multiInstanceAllowed)
			self.SetTitle(dialogTitle(config.conf.profiles[-1].name))

		def makeSettings(self, settingsSizer):
			# Made here rather than before the base constructor, which calls this: nothing is set on a wx
			# window before wx has made it.
			self.editor = _makeEditor()
			self.nodes: list = []
			self._captor = None
			sHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
			self.deviceCtrl = sHelper.addLabeledControl(
				# Translators: the label of the combo box choosing which device's layers are edited.
				_("D&evice:"),
				wx.Choice,
				choices=[device.label for device in self.editor.devices],
			)
			self.deviceCtrl.SetSelection(0)
			self.deviceCtrl.Bind(wx.EVT_CHOICE, self._onDevice)

			layerRow = guiHelper.BoxSizerHelper(self, orientation=wx.HORIZONTAL)
			self.layerCtrl = layerRow.addLabeledControl(
				# Translators: the label of the combo box choosing which layer is edited.
				_("&Layer:"),
				wx.Choice,
				choices=[],
			)
			self.layerCtrl.Bind(wx.EVT_CHOICE, self._onLayer)
			layerButtons = guiHelper.ButtonHelper(wx.HORIZONTAL)
			# Translators: the button that adds a layer in the layered keys dialog.
			self.newButton = layerButtons.addButton(self, label=_("&New layer..."))
			# Translators: the button that renames a layer in the layered keys dialog.
			self.renameButton = layerButtons.addButton(self, label=_("Rena&me..."))
			# Translators: the button that deletes a layer in the layered keys dialog.
			self.deleteButton = layerButtons.addButton(self, label=_("Dele&te layer"))
			# Translators: the button that opens a layer's properties in the layered keys dialog.
			self.propertiesButton = layerButtons.addButton(self, label=_("&Properties..."))
			layerRow.addItem(layerButtons)
			sHelper.addItem(layerRow)
			self.newButton.Bind(wx.EVT_BUTTON, self._onNewLayer)
			self.renameButton.Bind(wx.EVT_BUTTON, self._onRename)
			self.deleteButton.Bind(wx.EVT_BUTTON, self._onDelete)
			self.propertiesButton.Bind(wx.EVT_BUTTON, self._onProperties)

			self.filterCtrl = sHelper.addLabeledControl(
				# Translators: the label of the field that filters commands, as in Input Gestures.
				_("&Filter by:"),
				wx.TextCtrl,
			)
			self.filterCtrl.Bind(wx.EVT_TEXT, self._onFilter)
			self.onlyBoundCtrl = sHelper.addItem(
				# Translators: the check box that shows only commands with keys in the layer being edited.
				wx.CheckBox(self, label=_("&Only show commands with keys in this layer")),
			)
			self.onlyBoundCtrl.Bind(wx.EVT_CHECKBOX, self._onOnlyBound)

			self.tree = _Tree(self, self)
			self.tree.Bind(wx.EVT_TREE_SEL_CHANGED, self._onSelect)
			self.tree.Bind(wx.EVT_CHAR_HOOK, self._onTreeKey)
			self.tree.Bind(wx.EVT_CONTEXT_MENU, self._onContextMenu)
			self.tree.Bind(wx.EVT_TREE_ITEM_ACTIVATED, self._onContextMenu)
			sHelper.addItem(self.tree, proportion=1, flag=wx.EXPAND)

			buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
			self.addButton = buttons.addButton(self, label=_("&Add"))
			self.changeButton = buttons.addButton(self, label=_("C&hange"))
			self.removeButton = buttons.addButton(self, label=_("&Remove"))
			# Translators: the button that empties the layer being edited.
			self.clearButton = buttons.addButton(self, label=_("&Clear this layer"))
			# Translators: the button that puts a device's shipped layers back, as in Input Gestures.
			self.resetButton = buttons.addButton(self, label=_("Reset to factory &defaults"))
			sHelper.addItem(buttons)
			self.addButton.Bind(wx.EVT_BUTTON, self._onAdd)
			self.changeButton.Bind(wx.EVT_BUTTON, self._onChange)
			self.removeButton.Bind(wx.EVT_BUTTON, self._onRemove)
			self.clearButton.Bind(wx.EVT_BUTTON, self._onClear)
			self.resetButton.Bind(wx.EVT_BUTTON, self._onReset)
			self.Bind(wx.EVT_WINDOW_DESTROY, self._onDestroy)
			self._fillLayers()
			self._refresh()

		def postInit(self):
			self.tree.SetFocus()

		# Showing

		def _fillLayers(self) -> None:
			from . import keyLayerDispatch

			layers = self.editor.layers.layers
			self.layerCtrl.SetItems([keyLayerDispatch.layerName(layer) for layer in layers])
			self.layerCtrl.SetSelection(
				next(i for i, layer in enumerate(layers) if layer.id == self.editor.layer.id)
			)
			isDefault = self.editor.layer.isDefault
			self.renameButton.Enable(not isDefault)
			self.deleteButton.Enable(not isDefault)

		def _refresh(self, focus: Optional[tuple] = None) -> None:
			"""Rebuild the tree from the editor, keeping the reader where they were where possible.

			:param focus: (command identity, key identifier, L{PROMPT_FOCUS}, or None for the command)
				to select afterwards.
			"""
			self.nodes = self.editor.categories()
			with guiHelper.autoThaw(self.tree):
				self.tree.RefreshItems()
				if focus is not None:
					index = self._indexOf(focus)
					if index is not None:
						self.tree.Expand(self.tree.GetItemByIndex(index[:1]))
						if len(index) > 2:
							self.tree.Expand(self.tree.GetItemByIndex(index[:2]))
			if focus is not None:
				index = self._indexOf(focus)
				if index is not None:
					self.tree.SelectItem(self.tree.GetItemByIndex(index))
			self._refreshButtons()

		def _indexOf(self, focus: tuple) -> Optional[tuple]:
			commandKey, identifier = focus
			for categoryIndex, category in enumerate(self.nodes):
				for commandIndex, node in enumerate(category.commands):
					if identity(node.command.target) != commandKey:
						continue
					if identifier is None:
						return (categoryIndex, commandIndex)
					wanted = None if identifier == PROMPT_FOCUS else identifier
					for keyIndex, key in enumerate(node.keys):
						if key.identifier == wanted:
							return (categoryIndex, commandIndex, keyIndex)
					return (categoryIndex, commandIndex)
			return None

		def _refreshButtons(self) -> None:
			category, command, key = self.tree.selection()
			capturing = self._captor is not None
			canPress = self.editor.current.connected and not capturing
			emulated = (
				category is not None and command is None and category.name == self.editor.emulatedCategory
			)
			self.addButton.Enable(canPress and (command is not None and key is None or emulated))
			self.changeButton.Enable(
				canPress and key is not None and key.identifier is not None and key.fromLayer is None,
			)
			self.removeButton.Enable(not capturing and key is not None and key.identifier is not None)

		# Capturing a key

		def _capture(self, onKey: Callable, keyboardOnly: bool = False) -> None:
			"""Wait for the next key, from the device being edited, or from the keyboard for an emulated key."""
			from . import keyLayerDispatch

			wanted = KEYBOARD if keyboardOnly else self.editor.device

			def captor(gesture):
				if gesture.isModifier:
					return False
				source = keyLayerDispatch.deviceOf(gesture)
				if source == KEYBOARD and wanted != KEYBOARD and "kb:escape" in gesture.normalizedIdentifiers:
					# A reader waiting on a display they cannot reach, or who changed their mind, needs a way
					# out that is not the display. The keyboard's escape is it, as in any dialog.
					self._stopCapture()
					wx.CallAfter(self._cancelledCapture)
					return False
				if source != wanted:
					wx.CallAfter(self._wrongDevice, source, wanted)
					return False
				self._stopCapture()
				wx.CallAfter(onKey, gesture)
				return False

			self._captor = captor
			inputCore.manager._captureFunc = captor
			self._refreshButtons()

		def _stopCapture(self) -> None:
			if self._captor is not None and inputCore.manager._captureFunc is self._captor:
				inputCore.manager._captureFunc = None
			self._captor = None

		def _cancelledCapture(self) -> None:
			self.editor.pending = None
			self._refresh()

		def _wrongDevice(self, source: Optional[str], wanted: str) -> None:
			import ui

			ui.message(
				# Translators: said when a key from the wrong device is pressed while the layered keys dialog
				# waits for one. Placeholders are the device pressed and the device being edited.
				_(
					"That key is on {pressed}. This layer is for {editing}. Press escape to stop waiting"
				).format(
					pressed=self.editor.deviceLabel(source),
					editing=self.editor.deviceLabel(wanted),
				),
			)

		def _identifierFor(self, gesture) -> Optional[str]:
			"""The identifier to store: the most specific for a display, and the reader's choice for a keyboard."""
			identifiers = list(gesture.normalizedIdentifiers)
			if not identifiers:
				return None
			if self.editor.device != KEYBOARD or len(identifiers) == 1:
				return identifiers[0]
			return self._choose(identifiers, [self.editor.describeKey(each) for each in identifiers])

		def _choose(self, values: list, labels: list):
			""":return: the value chosen from a pop up menu, or the first when the menu is dismissed."""
			chosen = []
			menu = wx.Menu()
			for value, label in zip(values, labels):
				item = menu.Append(wx.ID_ANY, label)
				self.Bind(wx.EVT_MENU, lambda event, value=value: chosen.append(value), item)
			self.PopupMenu(menu)
			menu.Destroy()
			return chosen[0] if chosen else values[0]

		# Commands on keys

		def _onAdd(self, event) -> None:
			category, command, key = self.tree.selection()
			if command is None and category is not None and category.name == self.editor.emulatedCategory:
				self._capture(self._addEmulated, keyboardOnly=True)
				return
			if command is None:
				return
			self._startAdding(command.command)

		def _startAdding(self, command: Command, replacing: Optional[str] = None) -> None:
			""":param replacing: the key being changed, which stays until its replacement is accepted."""
			self.editor.pending = identity(command.target)
			# Through `_refresh`, which expands the command before selecting a key row under it. Selecting
			# the prompt directly failed on a collapsed command: a virtual tree has no rows for the children
			# of an item it has not expanded, and the lookup ran off the end. Found on hardware.
			self._refresh(focus=(identity(command.target), PROMPT_FOCUS))
			self._capture(lambda gesture: self._finishAdding(command, gesture, replacing))

		def _addEmulated(self, gesture) -> None:
			identifiers = list(gesture.normalizedIdentifiers)
			if not identifiers:
				return
			command = self.editor.addEmulatedKey(identifiers[-1])
			self._refresh(focus=(identity(command.target), None))

		def _finishAdding(self, command: Command, gesture, replacing: Optional[str] = None) -> None:
			if not self:
				# Closed between the key and this running.
				return
			identifier = self._identifierFor(gesture)
			self.editor.pending = None
			if identifier is None:
				self._refresh(focus=(identity(command.target), None))
				return
			check = self.editor.check(command, identifier)
			if check.refusal:
				gui.messageBox(check.refusal, self.title, wx.OK | wx.ICON_INFORMATION, self)
				self._refresh(focus=(identity(command.target), None))
				return
			if check.conflict and check.conflictLayer:
				answer = gui.messageBox(
					# Translators: asked when a key added in the layered keys dialog already runs another command
					# that the layer gets from a layer further down. Placeholders are the key, that command, the
					# layer it comes from, and the command the key is being added to.
					_(
						"{key} is {existing}, from the {layer} layer. Use it for {command} in this layer instead?"
					).format(
						key=self.editor.describeKey(identifier),
						existing=check.conflict,
						layer=check.conflictLayer,
						command=command.name,
					),
					self.title,
					wx.YES_NO | wx.ICON_QUESTION,
					self,
				)
				if answer != wx.YES:
					self._refresh(focus=(identity(command.target), None))
					return
			elif check.conflict:
				answer = gui.messageBox(
					# Translators: asked when a key added in the layered keys dialog already runs another command
					# in the layer. Placeholders are the key, the command it runs, and the command it is added to.
					_("{key} is {existing} in this layer. Use it for {command} instead?").format(
						key=self.editor.describeKey(identifier),
						existing=check.conflict,
						command=command.name,
					),
					self.title,
					wx.YES_NO | wx.ICON_QUESTION,
					self,
				)
				if answer != wx.YES:
					self._refresh(focus=(identity(command.target), None))
					return
			actsFor = None
			if self.editor.needsDisplay(command):
				displays = self.editor.displays()
				# Translators: the first choice when asked which display a keyboard key acts for.
				actsFor = self._choose(
					[None] + [each.name for each in displays],
					[_("No display")] + [each.label for each in displays],
				)
			self.editor.add(command, identifier, actsFor, replacing=replacing)
			self._refresh(focus=(identity(command.target), keyLayers.normalize(identifier)))

		def _onChange(self, event) -> None:
			category, command, key = self.tree.selection()
			if command is None or key is None or key.identifier is None:
				return
			# Not removed yet: escape, a refused key or a declined question must leave it where it was.
			self._startAdding(command.command, replacing=key.identifier)

		def _onRemove(self, event) -> None:
			category, command, key = self.tree.selection()
			if command is None or key is None or key.identifier is None:
				return
			if key.fromLayer is not None:
				answer = gui.messageBox(
					# Translators: asked when removing a key the layer being edited gets from a layer further
					# down. Placeholders are the key and the layer it comes from.
					_(
						"{key} comes from the {layer} layer, and removing it there would change that layer too. "
						"Make it do nothing in this layer instead?",
					).format(key=self.editor.describeKey(key.identifier), layer=key.fromLayer),
					self.title,
					wx.YES_NO | wx.ICON_QUESTION,
					self,
				)
				if answer == wx.YES:
					self.editor.block(key.identifier)
					self._refresh(focus=(identity(command.command.target), None))
				return
			self.editor.remove(key.identifier)
			self._refresh(focus=(identity(command.command.target), None))

		def _onTreeKey(self, event) -> None:
			if event.GetKeyCode() == wx.WXK_DELETE and self.removeButton.IsEnabled():
				self._onRemove(None)
				return
			event.Skip()

		def _onContextMenu(self, event) -> None:
			menu = wx.Menu()
			actions = (
				(self.addButton, self._onAdd),
				(self.changeButton, self._onChange),
				(self.removeButton, self._onRemove),
			)
			for button, action in actions:
				if button.IsEnabled():
					item = menu.Append(wx.ID_ANY, button.GetLabel())
					self.Bind(wx.EVT_MENU, lambda evt, action=action: action(None), item)
			if menu.GetMenuItemCount():
				self.PopupMenu(menu)
			menu.Destroy()

		def _onSelect(self, event) -> None:
			self._refreshButtons()

		def _onFilter(self, event) -> None:
			self.editor.filterText = self.filterCtrl.GetValue()
			self._refresh()

		def _onOnlyBound(self, event) -> None:
			self.editor.onlyBound = self.onlyBoundCtrl.IsChecked()
			self._refresh()

		# Devices and layers

		def _onDevice(self, event) -> None:
			self._stopCapture()
			self.editor.selectDevice(self.editor.devices[self.deviceCtrl.GetSelection()].name)
			self._fillLayers()
			self._refresh()

		def _onLayer(self, event) -> None:
			self._stopCapture()
			self.editor.selectLayer(self.editor.layers.layers[self.layerCtrl.GetSelection()].id)
			self._fillLayers()
			self._refresh()

		def _ask(self, message: str, value: str = "") -> Optional[str]:
			with wx.TextEntryDialog(self, message, self.title, value) as dialog:
				if dialog.ShowModal() != wx.ID_OK:
					return None
				return dialog.GetValue().strip() or None

		def _onNewLayer(self, event) -> None:
			# Translators: asked for the name of a new layer in the layered keys dialog.
			name = self._ask(_("Name of the new layer:"))
			if name is None:
				return
			self.editor.newLayer(name)
			self._fillLayers()
			self._refresh()

		def _onRename(self, event) -> None:
			# Translators: asked for a layer's new name in the layered keys dialog.
			name = self._ask(_("New name for the layer:"), self.editor.layer.name)
			if name is None:
				return
			self.editor.renameLayer(name)
			self._fillLayers()

		def _onDelete(self, event) -> None:
			from . import keyLayerDispatch

			answer = gui.messageBox(
				# Translators: asked before deleting a layer in the layered keys dialog. The placeholder is its name.
				_("Delete the {name} layer and every key in it?").format(
					name=keyLayerDispatch.layerName(self.editor.layer)
				),
				self.title,
				wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
				self,
			)
			if answer != wx.YES:
				return
			self.editor.deleteLayer()
			self._fillLayers()
			self._refresh()

		def _onProperties(self, event) -> None:
			self._stopCapture()
			try:
				with _PropertiesDialog(self) as dialog:
					dialog.ShowModal()
			finally:
				# However Properties closed, OK, Cancel or its close box, while Add exit key was waiting:
				# a capture left behind would swallow every key NVDA gets.
				self._stopCapture()
			self._fillLayers()
			self._refresh()

		def _onClear(self, event) -> None:
			from . import keyLayerDispatch

			answer = gui.messageBox(
				# Translators: asked before removing every key from a layer. The placeholder is its name.
				_("Remove every key from the {name} layer?").format(
					name=keyLayerDispatch.layerName(self.editor.layer)
				),
				self.title,
				wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
				self,
			)
			if answer == wx.YES:
				self.editor.clearLayer()
				self._refresh()

		def _onReset(self, event) -> None:
			answer = gui.messageBox(
				# Translators: asked before resetting a device's layers in the layered keys dialog. The
				# placeholder is the device.
				_(
					"Reset every layer of {device} to its factory defaults? "
					"Layers and keys you made for it will be lost when you press OK.",
				).format(device=self.editor.current.label),
				self.title,
				wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
				self,
			)
			if answer == wx.YES:
				self.editor.resetToFactoryDefaults()
				self._fillLayers()
				self._refresh()

		# Closing

		def _commit(self) -> bool:
			from . import keyLayerDispatch

			failed = self.editor.commit(keyLayerDispatch.save)
			if not failed:
				return True
			gui.messageBox(
				"\n".join(f"{self.editor.deviceLabel(device)}: {reason}" for device, reason in failed),
				# Translators: the title of an error saying some layers of keys could not be saved.
				_("Layers not saved"),
				wx.OK | wx.ICON_ERROR,
				self,
			)
			return False

		def onOk(self, evt):
			self._stopCapture()
			if self._commit():
				super().onOk(evt)

		def onCancel(self, evt):
			self._stopCapture()
			super().onCancel(evt)

		def _onDestroy(self, event) -> None:
			if event.GetEventObject() is self:
				self._stopCapture()
			event.Skip()

	class _PropertiesDialog(wx.Dialog):
		"""A layer's name, context, style, exit keys and the layer it falls through to."""

		def __init__(self, parent: "KeyLayersDialog"):
			from . import keyLayerDispatch

			self.owner = parent
			self.editor = parent.editor
			layer = self.editor.layer
			super().__init__(
				parent,
				# Translators: the title of a layer's properties dialog. The placeholder is the layer's name.
				title=_("{name} layer properties").format(name=keyLayerDispatch.layerName(layer)),
			)
			self.exitKeys = sorted(layer.exitKeys)
			self.fallThroughs = self.editor.fallThroughChoices()
			sizer = wx.BoxSizer(wx.VERTICAL)
			sHelper = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)
			# Translators: the label of a layer's name field.
			self.nameCtrl = sHelper.addLabeledControl(_("&Name:"), wx.TextCtrl, value=layer.name)
			contexts = [None, *keyLayers.CONTEXTS]
			# Translators: no context, in a layer's properties.
			contextLabels = [_("None")] + [CONTEXT_LABELS[context] for context in keyLayers.CONTEXTS]
			# Translators: the label of the choice of what a layer is for.
			self.contextCtrl = sHelper.addLabeledControl(_("&For:"), wx.Choice, choices=contextLabels)
			self.contextCtrl.SetSelection(contexts.index(layer.context))
			self.contexts = contexts
			self.autoCtrl = sHelper.addItem(
				# Translators: a check box in a layer's properties.
				wx.CheckBox(self, label=_("Comes on &by itself when what it is for appears")),
			)
			self.autoCtrl.SetValue(layer.autoEnable)
			# Translators: the label of the choice of how long a layer stays on.
			self.styleCtrl = sHelper.addLabeledControl(
				_("&Stays on:"),
				wx.Choice,
				choices=[STYLE_LABELS[style] for style in STYLES],
			)
			self.styleCtrl.SetSelection(STYLES.index(layer.style))
			# Translators: the label of the choice of the layer a layer falls through to.
			self.fallCtrl = sHelper.addLabeledControl(
				_("Keys it does not have come fro&m:"),
				wx.Choice,
				# Translators: a layer that falls through straight to the default layer.
				choices=[_("The default layer")]
				+ [keyLayerDispatch.layerName(each) for each in self.fallThroughs],
			)
			fallIds = [None] + [each.id for each in self.fallThroughs]
			self.fallIds = fallIds
			self.fallCtrl.SetSelection(
				fallIds.index(layer.fallsThrough) if layer.fallsThrough in fallIds else 0
			)
			# Translators: the label of the list of keys that turn a layer off.
			self.exitCtrl = sHelper.addLabeledControl(_("E&xit keys:"), wx.ListBox, choices=[])
			exitButtons = guiHelper.ButtonHelper(wx.HORIZONTAL)
			# Translators: adds a key that turns a layer off.
			self.addExitButton = exitButtons.addButton(self, label=_("A&dd exit key"))
			# Translators: removes a key that turns a layer off.
			self.removeExitButton = exitButtons.addButton(self, label=_("&Remove exit key"))
			sHelper.addItem(exitButtons)
			self.addExitButton.Bind(wx.EVT_BUTTON, self._onAddExit)
			self.removeExitButton.Bind(wx.EVT_BUTTON, self._onRemoveExit)
			self.addExitButton.Enable(self.editor.current.connected)
			for control in (self.nameCtrl, self.contextCtrl, self.autoCtrl, self.fallCtrl):
				control.Enable(not layer.isDefault)
			sHelper.addDialogDismissButtons(wx.OK | wx.CANCEL)
			self.Bind(wx.EVT_BUTTON, self._onOk, id=wx.ID_OK)
			sizer.Add(sHelper.sizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL | wx.EXPAND)
			self.SetSizerAndFit(sizer)
			self._fillExits()
			self.nameCtrl.SetFocus()

		def _fillExits(self) -> None:
			self.exitCtrl.SetItems([self.editor.describeKey(key) for key in self.exitKeys])
			if self.exitKeys:
				self.exitCtrl.SetSelection(0)
			self.removeExitButton.Enable(bool(self.exitKeys))

		def _onAddExit(self, event) -> None:
			self.owner._capture(self._addExit)

		def _addExit(self, gesture) -> None:
			if not self:
				# Properties closed between the key and this running.
				return
			identifiers = list(gesture.normalizedIdentifiers)
			if identifiers and identifiers[0] not in self.exitKeys:
				self.exitKeys.append(identifiers[0])
			self._fillExits()

		def _onRemoveExit(self, event) -> None:
			index = self.exitCtrl.GetSelection()
			if 0 <= index < len(self.exitKeys):
				del self.exitKeys[index]
			self._fillExits()

		def _onOk(self, event) -> None:
			self.owner._stopCapture()
			problem = self.editor.setProperties(
				name=self.nameCtrl.GetValue(),
				context=self.contexts[self.contextCtrl.GetSelection()],
				autoEnable=self.autoCtrl.IsChecked(),
				style=STYLES[self.styleCtrl.GetSelection()],
				fallsThrough=self.fallIds[self.fallCtrl.GetSelection()],
				exitKeys=self.exitKeys,
			)
			if problem:
				gui.messageBox(problem, self.GetTitle(), wx.OK | wx.ICON_ERROR, self)
				return
			self.EndModal(wx.ID_OK)

	def _makeEditor() -> LayerEditor:
		"""Gather what the dialog offers from NVDA, as Input Gestures gathers it."""
		import braille

		from . import bmConfig, devices, keyLayerDispatch
		from .settingsPanel import displayDescriptions

		descriptions = displayDescriptions()

		def labelOf(name: str) -> str:
			return descriptions.get(name, name)

		members = devices.deviceMap()
		display = braille.handler.display if braille.handler else None
		connected = [member.driverName for member in members] or (
			[display.name] if display is not None else []
		)
		choices = deviceChoices(connected, bmConfig.keyLayerDevices(), labelOf)
		mappings = inputCore.manager.getAllGestureMappings(
			obj=gui.mainFrame.prevFocus,
			ancestors=gui.mainFrame.prevFocusAncestors,
		)
		commands = commandsFromMappings(mappings, inputCore.SCRCAT_KBEMU)
		slots = getattr(display, "slots", None) or []
		memberDrivers = [
			(slot.driver, labelOf(slot.driverName)) for slot in slots if getattr(slot, "driver", None)
		]
		commands += memberCommands(memberDrivers, commands, inputCore.SCRCAT_MISC)
		from . import SCRIPT_CATEGORY

		def factory(device: str) -> LayerSet:
			return LayerSet(device, tuple(keyLayers.defaultLayers(device, keyLayerDispatch.PLUGIN_MODULE)))

		return LayerEditor(
			choices,
			keyLayerDispatch.layerSet,
			factory,
			commands,
			describeKey=_describeKey,
			nameLayer=keyLayerDispatch.layerName,
			emulatedCategory=inputCore.SCRCAT_KBEMU,
			blockedCategory=SCRIPT_CATEGORY,
			reservedKeys=reservedFromMappings(mappings, keyLayerDispatch.LAYER_COMMAND_PREFIX),
		)

	def _describeKey(identifier: str) -> str:
		"""A key's name as Input Gestures shows it: the key, then where it is."""
		try:
			source, main = inputCore.getDisplayTextForGestureIdentifier(identifier)
		except Exception:
			log.debugWarning(f"BrlMultiline: no display text for {identifier}", exc_info=True)
			return identifier
		# Translators: a key in the layered keys dialog, as Input Gestures names one. Placeholders are the
		# key and where it is, such as a display or the laptop keyboard.
		return _("{main} ({source})").format(main=main, source=source)

	def openDialog(parent=None, modal: bool = False) -> None:
		"""Open the layered keys dialog the way NVDA opens Input Gestures, or modally over another dialog."""
		if modal:
			dialog = KeyLayersDialog(parent, multiInstanceAllowed=True)
			dialog.ShowModal()
			return
		gui.mainFrame.popupSettingsDialog(KeyLayersDialog)
