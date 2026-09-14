# BrlMultiline: layers of key bindings, and what a key does while one is on.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The model behind layered keys: layers, what their keys run, and the decision for one key.

Read `docs/design/layered-keys-plan.md` first. This is its model, and it imports nothing from
NVDA, so every rule the plan settles is unit tested here without one. `keyLayerDispatch` is the
NVDA side: it asks L{decide} what a key does and then makes it happen.

**A device** — a braille display, named by its driver, or the keyboard — has a L{LayerSet}: a
default layer that is always there and any number of the reader's own. One layer per device is
on at a time.

**A key the layer on does not bind is transparent.** It is looked up in the layer that one falls
through to, and so on down to the device's default layer, and if nothing binds it NVDA does what
it always does with it. Decided with the reader: braille typing and every ordinary command go on
working in any layer, with no list of exceptions to maintain.

**Two styles.** A one shot layer turns off after the next key, whatever that key did; it is the
default for a layer with no context, and it is how twenty commands go behind one key. A layer
that stays on turns off only when asked; it is the default for a layer with a context, because
graphics is somewhere a reader stays.

**Escape leaves**, and goes no further: never sent on, since a reader backing out of a layer does
not mean to close the dialog under it. A key leaves if it is one of the layer's exit keys — space
with z on a braille display, where `hidBrailleStandard` has escape somewhere else — or if its
ordinary command is the emulated escape key. A layer that binds escape itself keeps it.

**Routing and panning never end a layer**, not even a one shot one. In graphics a routing press
asks what is under the finger, and panning is how the reader keeps reading.

**The add-on ships default layers** for the Monarch and the keyboard, in L{defaultLayers}. They
are what a device has until the reader saves layers of their own, and what the layered keys
dialog's reset to factory defaults puts back. Decided with the reader, 14 September 2026: the
Monarch should work out of the box, and the layout can be tuned from here.
"""

import json
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, NamedTuple, Optional

VERSION = 1
"""The stored form's version. Written with every layer set and checked on reading."""

KEYBOARD = "keyboard"
"""The device name for keyboard gestures, which carry no driver name of their own."""

MONARCH = "brlMultilineMonarch"

DEFAULT_ID = "default"
"""The default layer's id. Every device has exactly one, and it cannot be removed."""

ONE_SHOT = "oneShot"
STAYS_ON = "staysOn"
STYLES = (ONE_SHOT, STAYS_ON)

CONTEXTS = ("chart", "picture", "graphics", "table")
"""Every context a layer can be for, most specific first. See `keyLayerContexts`."""

SPACE_WITH_Z = "dot1+dot3+dot5+dot6+space"
"""Escape on most braille displays, normalized. `hidBrailleStandard` maps escape to space with e,
so without this a Monarch reader would have no escape the ordinary script rule could find."""

ESCAPE_SCRIPT = "kb:escape"
"""The script name NVDA gives the emulated escape key. See `scriptHandler._makeKbEmulateScript`."""

ESCAPE_KEY = "kb:escape"
"""The keyboard's own escape key, normalized, which leaves a keyboard layer.

A rule of its own because the ordinary script rule cannot find it: the escape key has no NVDA
command, it goes straight to Windows, so its ordinary script is nothing at all. Phase 1 on hardware
found a keyboard layer that escape did not leave. With modifiers it is another key, so shift with
escape is left to NVDA.
"""

NEVER_END_A_LAYER = frozenset(
	{
		"braille_routeTo",
		"braille_selectRange",
		"braille_scrollBack",
		"braille_scrollForward",
	},
)
"""Ordinary commands whose keys pass through a layer without ending it, in either style.

By script name, because the script is what every display's gesture map binds, whatever the key
is called on that display. A key raising cell indexes is a routing key too; see L{decide}.
"""


def normalize(identifier: str) -> str:
	"""Normalize a gesture identifier as `inputCore.normalizeGestureIdentifier` does.

	Copied rather than called so this module needs no NVDA. Lower case, and the parts after the
	source sorted, so `space+dot1` and `dot1+space` are one key.
	"""
	identifier = identifier.lower()
	prefix, _, main = identifier.partition(":")
	return f"{prefix}:{'+'.join(sorted(main.split('+')))}"


def isBrailleDevice(device: str) -> bool:
	return device != KEYBOARD


def spaceWithZ(device: str) -> str:
	""":return: space with z on a braille display, as that display's identifier."""
	return normalize(f"br({device}):{SPACE_WITH_Z}")


# Targets


SCRIPT = "script"
KEY = "key"
BLOCKED = "blocked"


class Target(NamedTuple):
	"""What a key in a layer does."""

	kind: str
	"""L{SCRIPT}, L{KEY} for an emulated keyboard key, or L{BLOCKED} for nothing at all."""

	moduleName: str = ""
	className: str = ""
	scriptName: str = ""
	"""For a script, its name without `script_`, as NVDA's gesture maps name one. For a key, the
	key as Input Gestures' emulated keys store it, such as `kb:downArrow`."""

	actsFor: Optional[str] = None
	"""The display this binding acts for, when a keyboard key runs a command that asks which
	display it is for. See `keyLayerDispatch.deviceFor`."""

	@classmethod
	def script(cls, moduleName: str, className: str, scriptName: str, actsFor: Optional[str] = None):
		return cls(SCRIPT, moduleName, className, scriptName, actsFor)

	@classmethod
	def key(cls, key: str, actsFor: Optional[str] = None):
		return cls(KEY, scriptName=key if key.startswith("kb:") else f"kb:{key}", actsFor=actsFor)

	@classmethod
	def blocked(cls):
		return cls(BLOCKED)

	def describe(self) -> str:
		""":return: the target as a log line names it."""
		if self.kind == SCRIPT:
			return f"{self.moduleName}.{self.className}.{self.scriptName}"
		if self.kind == KEY:
			return self.scriptName
		return "blocked"

	def toStored(self) -> dict:
		stored: dict = {}
		if self.kind == SCRIPT:
			stored = {"module": self.moduleName, "class": self.className, "script": self.scriptName}
		elif self.kind == KEY:
			stored = {"key": self.scriptName}
		else:
			stored = {"blocked": True}
		if self.actsFor:
			stored["actsFor"] = self.actsFor
		return stored

	@classmethod
	def fromStored(cls, stored) -> Optional["Target"]:
		""":return: the target, or None for a form this version cannot read."""
		if not isinstance(stored, dict):
			return None
		actsFor = stored.get("actsFor")
		actsFor = actsFor if isinstance(actsFor, str) and actsFor else None
		if stored.get("blocked"):
			return cls.blocked()
		key = stored.get("key")
		if isinstance(key, str) and key:
			return cls.key(key, actsFor)
		parts = (stored.get("module"), stored.get("class"), stored.get("script"))
		if all(isinstance(part, str) and part for part in parts):
			return cls.script(*parts, actsFor)
		return None


# Layers


def defaultStyle(context: Optional[str]) -> str:
	""":return: one shot for a layer with no context, stays on for one with. See the module docstring."""
	return STAYS_ON if context else ONE_SHOT


@dataclass(frozen=True)
class Layer:
	"""One layer of one device's keys."""

	id: str
	name: str = ""
	"""What the reader called it. Empty for the default layer, which is named when it is spoken."""

	context: Optional[str] = None
	autoEnable: bool = False
	"""Whether it comes on by itself with its context. Honoured from phase 3."""

	style: str = ONE_SHOT
	exitKeys: frozenset = frozenset()
	"""Keys, normalized, that turn this layer off whatever it binds."""

	fallsThrough: Optional[str] = None
	"""The id of the layer an unbound key is looked up in next, before the default layer."""

	bindings: dict = field(default_factory=dict)
	"""Normalized identifier to L{Target}."""

	@property
	def isDefault(self) -> bool:
		return self.id == DEFAULT_ID

	def toStored(self) -> dict:
		stored = {
			"id": self.id,
			"name": self.name,
			"style": self.style,
			"exitKeys": sorted(self.exitKeys),
			"bindings": {
				identifier: target.toStored() for identifier, target in sorted(self.bindings.items())
			},
		}
		if self.context:
			stored["context"] = self.context
		if self.autoEnable:
			stored["autoEnable"] = True
		if self.fallsThrough:
			stored["fallsThrough"] = self.fallsThrough
		return stored

	@classmethod
	def fromStored(cls, stored) -> Optional["Layer"]:
		if not isinstance(stored, dict) or not isinstance(stored.get("id"), str) or not stored["id"]:
			return None
		context = stored.get("context") if stored.get("context") in CONTEXTS else None
		style = stored.get("style") if stored.get("style") in STYLES else defaultStyle(context)
		bindings = {}
		rawBindings = stored.get("bindings")
		if isinstance(rawBindings, dict):
			for identifier, rawTarget in rawBindings.items():
				target = Target.fromStored(rawTarget)
				if isinstance(identifier, str) and target is not None:
					bindings[normalize(identifier)] = target
		rawExits = stored.get("exitKeys")
		exits = (
			frozenset(normalize(key) for key in rawExits if isinstance(key, str))
			if isinstance(rawExits, list)
			else frozenset()
		)
		fallsThrough = stored.get("fallsThrough")
		return cls(
			id=stored["id"],
			name=stored.get("name") if isinstance(stored.get("name"), str) else "",
			context=context,
			autoEnable=bool(stored.get("autoEnable")),
			style=style,
			exitKeys=exits,
			fallsThrough=fallsThrough if isinstance(fallsThrough, str) and fallsThrough else None,
			bindings=bindings,
		)


def newLayer(device: str, id: str, name: str = "", context: Optional[str] = None, **changes) -> Layer:
	"""Make a layer as a reader creating one would get it: its style from its context, and space
	with z to leave it on a braille display."""
	exitKeys = frozenset({spaceWithZ(device)}) if isBrailleDevice(device) else frozenset()
	layer = Layer(id=id, name=name, context=context, style=defaultStyle(context), exitKeys=exitKeys)
	return replace(layer, **changes) if changes else layer


class LayerSetError(ValueError):
	"""A layer set that cannot be saved as it stands."""


@dataclass(frozen=True)
class LayerSet:
	"""All of one device's layers. The default layer is always present and always first."""

	device: str
	layers: tuple = ()

	def __post_init__(self):
		if not any(layer.isDefault for layer in self.layers):
			object.__setattr__(self, "layers", (newLayer(self.device, DEFAULT_ID), *self.layers))
		else:
			ordered = sorted(self.layers, key=lambda layer: not layer.isDefault)
			object.__setattr__(self, "layers", tuple(ordered))

	@property
	def default(self) -> Layer:
		return self.layers[0]

	def get(self, id: Optional[str]) -> Optional[Layer]:
		for layer in self.layers:
			if layer.id == id:
				return layer
		return None

	def chain(self, id: str) -> list:
		""":return: the layer, the layers it falls through to, and the default layer, in lookup order.

		A cycle or a missing layer ends the named part of the chain rather than failing: a set is
		checked for both when it is saved, and one read back broken still has to work.
		"""
		chain = []
		seen = set()
		layer = self.get(id)
		while layer is not None and layer.id not in seen and not layer.isDefault:
			chain.append(layer)
			seen.add(layer.id)
			layer = self.get(layer.fallsThrough)
		chain.append(self.default)
		return chain

	def lookup(self, id: str, identifiers: Iterable[str]) -> Optional[tuple]:
		""":return: the target a key runs in this layer's chain, and the layer that binds it, or None."""
		identifiers = list(identifiers)
		for layer in self.chain(id):
			for identifier in identifiers:
				target = layer.bindings.get(identifier)
				if target is not None:
					return target, layer
		return None

	def forContexts(self, present: Iterable[str]) -> Layer:
		""":return: the layer for the most specific context present, or the default layer.

		:param present: the contexts present now, most specific first, as `keyLayerContexts` gives them.
		"""
		for context in present:
			for layer in self.layers:
				if layer.context == context:
					return layer
		return self.default

	def autoLayerFor(self, present: Iterable[str], declined: Iterable[str] = ()) -> Optional[Layer]:
		""":return: the layer that comes on by itself for the most specific context present, or None.

		:param present: the contexts present now, most specific first.
		:param declined: contexts whose automatic layer the reader turned off while they were present,
			which stay off until the context goes and comes back.
		"""
		declined = set(declined)
		for context in present:
			if context in declined:
				continue
			for layer in self.layers:
				if layer.autoEnable and layer.context == context:
					return layer
		return None

	def inherited(self, id: str) -> dict:
		""":return: identifier to (target, layer) for every key a layer gets from further down its chain.

		What a key the layer does not bind would run: the nearest layer below that binds it. A key the
		layer binds itself is not here, since it is not inherited. Empty for the default layer.
		"""
		layer = self.get(id)
		own = set(layer.bindings) if layer is not None else set()
		found: dict = {}
		for below in self.chain(id)[1:]:
			for identifier, target in below.bindings.items():
				if identifier not in own and identifier not in found:
					found[identifier] = (target, below)
		return found

	def withLayer(self, layer: Layer) -> "LayerSet":
		""":return: this set with a layer added, or replacing the one with its id."""
		layers = [existing for existing in self.layers if existing.id != layer.id]
		layers.append(layer)
		return LayerSet(self.device, tuple(layers))

	def problems(self) -> list:
		""":return: what is wrong with this set, as sentences for the log. Empty when it can be saved."""
		found = []
		ids = [layer.id for layer in self.layers]
		for duplicate in sorted({id for id in ids if ids.count(id) > 1}):
			found.append(f"two layers have the id {duplicate!r}")
		for layer in self.layers:
			if layer.isDefault and (layer.context or layer.fallsThrough):
				found.append("the default layer can have no context and falls through to nothing")
			if layer.fallsThrough and self.get(layer.fallsThrough) is None:
				found.append(f"{layer.id!r} falls through to {layer.fallsThrough!r}, which does not exist")
			seen = []
			current = layer
			while current is not None and current.fallsThrough:
				if current.id in seen:
					found.append(
						f"{layer.id!r} falls through in a circle: {' to '.join(seen + [current.id])}"
					)
					break
				seen.append(current.id)
				current = self.get(current.fallsThrough)
		return found

	def toText(self) -> str:
		""":return: the stored form.

		:raises LayerSetError: if the set has a problem. Cycles are refused here, when saving.
		"""
		problems = self.problems()
		if problems:
			raise LayerSetError("; ".join(problems))
		return json.dumps(
			{"version": VERSION, "layers": [layer.toStored() for layer in self.layers]}, sort_keys=True
		)

	@classmethod
	def fromText(cls, device: str, text: str, factory: Iterable = ()) -> tuple:
		"""Read a stored layer set, as much of it as can be read.

		:param factory: the layers the device ships with, used when nothing is stored.
		:return: the set, and a problem to log or None. An empty text gives the factory layers, and so
			does one that cannot be read at all, since working keys are better than none. Layers and
			bindings this version cannot read are left out of an otherwise readable text, and a
			binding to a command that no longer exists is kept, since that is decided at runtime.
		"""
		if not text:
			return cls(device, tuple(factory)), None
		try:
			stored = json.loads(text)
		except ValueError as error:
			return cls(
				device, tuple(factory)
			), f"could not read the stored layers, so using the defaults: {error}"
		if not isinstance(stored, dict) or not isinstance(stored.get("layers"), list):
			return cls(device, tuple(factory)), "the stored layers are not in a form this version knows"
		problem = None
		if stored.get("version") != VERSION:
			problem = (
				f"stored layers are version {stored.get('version')!r}; reading them as version {VERSION}"
			)
		layers = []
		for rawLayer in stored["layers"]:
			layer = Layer.fromStored(rawLayer)
			if layer is None:
				problem = problem or "a stored layer could not be read and was left out"
				continue
			if layer.isDefault:
				layer = replace(layer, context=None, fallsThrough=None, autoEnable=False)
			layers.append(layer)
		return cls(device, tuple(layers)), problem


# The decision


PASS = "pass"
RUN = "run"
LEAVE = "leave"


class Decision(NamedTuple):
	"""What one key does with a layer on."""

	action: str
	"""L{PASS} to leave the key to NVDA, L{RUN} to run L{target}, L{LEAVE} to turn the layer off."""

	target: Optional[Target] = None
	layer: Optional[Layer] = None
	"""For L{RUN}, the layer in the chain that binds the key."""

	endsOneShot: bool = False
	"""Whether this key is the one a one shot layer was waiting for."""


def decide(
	layerSet: LayerSet,
	activeId: str,
	identifiers: Iterable[str],
	ordinaryScriptName: Callable[[], Optional[str]],
	hasCells: bool = False,
	isLayerCommand: Callable[[Optional[str]], bool] = lambda name: False,
) -> Decision:
	"""Decide what a key does while a layer is on.

	:param layerSet: the device's layers.
	:param activeId: the layer that is on.
	:param identifiers: the key's normalized identifiers, most specific first.
	:param ordinaryScriptName: what NVDA would run for the key without a layer, as a script name
		without `script_`, or None. Called only for a key the chain does not bind, because it runs
		NVDA's own lookup.
	:param hasCells: whether the key raised cell indexes, which only a routing key does.
	:param isLayerCommand: whether a script name is one of the layered keys commands, which always
		reach a key bound to them so that the layer key can turn the layer off again.
	"""
	active = layerSet.get(activeId)
	if active is None:
		return Decision(PASS)
	oneShot = active.style == ONE_SHOT
	identifiers = list(identifiers)
	found = layerSet.lookup(activeId, identifiers)
	if found is not None:
		target, layer = found
		return Decision(RUN, target, layer, endsOneShot=oneShot)
	if ESCAPE_KEY in identifiers or any(identifier in active.exitKeys for identifier in identifiers):
		return Decision(LEAVE, layer=active)
	ordinary = ordinaryScriptName()
	if ordinary == ESCAPE_SCRIPT:
		return Decision(LEAVE, layer=active)
	if isLayerCommand(ordinary) or hasCells or ordinary in NEVER_END_A_LAYER:
		return Decision(PASS)
	return Decision(PASS, endsOneShot=oneShot)


# Default layers


def defaultLayers(device: str, pluginModule: str) -> list:
	"""The layers a device ships with: what it has until the reader saves layers of their own.

	The Monarch's default layer is one shot and reads: the layer key, then a direction on the left
	d-pad. Its graphics layer stays on, pans with the left d-pad and zooms with the zoom keys, and
	comes on by itself with a drawing, since putting a drawing up is already asking for it. Its table
	layer stays on and moves by table cell with the left d-pad, and turns column pages with the zoom
	keys; it comes on only from the layer key, since a table is somewhere the caret passes through.
	The right d-pad is in none of them, so it stays the arrow keys, and the d-pad centre is not a key.

	The keyboard's graphics layer does the same from the keypad, acting for the Monarch; a command
	that does not ask which display it is for ignores that, so it is harmless without one. It does not
	come on by itself: the keypad is the review cursor on a desktop layout, and a drawing going up is
	no reason to take it away.

	:param device: the device.
	:param pluginModule: this add-on's plugin module, which is `globalPlugins.brlMultiline` in NVDA.
	:return: the layers, including the default layer, or an empty list for a device with none.
	"""

	def plugin(name, actsFor=None):
		return Target.script(pluginModule, "GlobalPlugin", name, actsFor)

	def command(name):
		return Target.script("globalCommands", "GlobalCommands", name)

	if device == MONARCH:
		pad = {
			f"br({MONARCH}):leftDpad{direction}": direction for direction in ("Up", "Down", "Left", "Right")
		}
		reading = {
			"Up": "reportCurrentLine",
			"Down": "sayAll",
			"Left": "reportCurrentFocus",
			"Right": "title",
		}
		default = newLayer(
			device,
			DEFAULT_ID,
			bindings={normalize(key): command(reading[direction]) for key, direction in pad.items()},
		)
		graphics = newLayer(
			device,
			"graphics",
			"Graphics",
			"graphics",
			autoEnable=True,
			bindings={
				**{normalize(key): plugin(f"graphicsPan{direction}") for key, direction in pad.items()},
				normalize(f"br({MONARCH}):zoomIn"): plugin("graphicsZoomIn"),
				normalize(f"br({MONARCH}):zoomOut"): plugin("graphicsZoomOut"),
			},
		)

		def table(name):
			return Target.script("documentBase", "DocumentWithTableNavigation", name)

		cells = {"Up": "previousRow", "Down": "nextRow", "Left": "previousColumn", "Right": "nextColumn"}
		tables = newLayer(
			device,
			"table",
			"Table",
			"table",
			bindings={
				**{normalize(key): table(cells[direction]) for key, direction in pad.items()},
				normalize(f"br({MONARCH}):zoomIn"): plugin("flowNextColumns"),
				normalize(f"br({MONARCH}):zoomOut"): plugin("flowPreviousColumns"),
			},
		)
		return [default, graphics, tables]
	if device == KEYBOARD:

		def keypad(names, target):
			return {normalize(f"kb:{name}"): target for name in names}

		default = newLayer(
			device, DEFAULT_ID, bindings=keypad(("numpad5", "numLockNumpad5"), command("reportCurrentLine"))
		)
		graphics = newLayer(
			device,
			"graphics",
			"Graphics",
			"graphics",
			bindings={
				**keypad(("numpad8", "numLockNumpad8"), plugin("graphicsPanUp", MONARCH)),
				**keypad(("numpad2", "numLockNumpad2"), plugin("graphicsPanDown", MONARCH)),
				**keypad(("numpad4", "numLockNumpad4"), plugin("graphicsPanLeft", MONARCH)),
				**keypad(("numpad6", "numLockNumpad6"), plugin("graphicsPanRight", MONARCH)),
				# Num lock is a modifier on plus and minus when it is on. See `KeyboardInputGesture`.
				**keypad(("numpadPlus", "numLock+numpadPlus"), plugin("graphicsZoomIn", MONARCH)),
				**keypad(("numpadMinus", "numLock+numpadMinus"), plugin("graphicsZoomOut", MONARCH)),
				**keypad(("numpad5", "numLockNumpad5"), command("reportCurrentLine")),
			},
		)
		return [default, graphics]
	return []
