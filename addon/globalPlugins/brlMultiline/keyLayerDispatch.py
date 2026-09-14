# BrlMultiline: choosing a key's command from a layer, before NVDA looks for one.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Layered keys, the NVDA side: which layer is on, and making a key do what that layer says.

Read `docs/design/layered-keys-plan.md` first. `keyLayers` is the model and decides; this module
reads the stored layers, keeps which one is on for each device, and carries a decision out.

**The mechanism**, proven on hardware in phase 0. `inputCore.InputManager.executeGesture` asks
`decide_executeGesture` before it reads `gesture.script`. That property's getter has no setter,
so an instance attribute assigned in the decider shadows it, and everything after — input help,
sleep mode, speech cancel, the repeat count — treats the layer's command exactly as a key bound
in Input Gestures. A key nothing in the layer binds is left alone.

**Turning a layer on or off happens in scripts**, on the main thread, with one exception. The
decider runs on the thread the driver dispatched from and input help or sleep mode can abandon a
gesture after it, which is how `panning.py` went wrong when it recorded things at decision time.
The exception is **the key a one shot layer was waiting for**, which ends the layer when it is
decided: that key may have no script of ours to end it from — an unbound key passing through to
Windows has no script at all — and wrapping every script NVDA runs would cost the repeat count.
It is not ended while any capture function is set, so input help describing a key does not spend
the shot.

**Loading happens on the main thread too.** The decider only reads the cached layer sets, which
are read from the configuration at start, on a profile switch and after a save, so it never
touches `config.conf` from a driver's thread.

**Which device.** A braille gesture's `source` is its driver's name, and through the virtual
display each member's keys keep their own. A keyboard gesture has no source and is the device
`keyboard`.

**Acts for.** A keyboard binding may name a display it acts for. That is recorded on the gesture,
because the same object is what the script is later called with, and L{displayFor} answers from
it: a command that asks which display it is for gets the Monarch from a keypad key.

Everything logged here starts `BrlMultiline key layers:` so a run can be read back out of the log.
"""

import importlib
import sys
from typing import Optional

import inputCore
import ui
from logHandler import log

from . import bmConfig, keyLayers
from .keyLayers import KEYBOARD, Layer, LayerSet, Target

ACTS_FOR_ATTRIBUTE = "_brlMultilineActsFor"
"""Where the display a keyboard binding acts for is recorded on the gesture."""

LAYER_COMMAND_PREFIX = "keyLayer"
"""Every layered keys command on the plugin is named `script_keyLayer...`. A key whose ordinary
command is one of them always reaches it, so the layer key can turn a layer off again."""

LOG_PREFIX = "BrlMultiline key layers: "

PLUGIN_MODULE = __package__
"""This add-on's plugin module, which the default layers name their commands on. The plugin class
is defined in the package itself, so its module is the package."""

_sets: dict = {}
"""Device to its L{LayerSet}, as last read on the main thread."""

_active: dict = {}
"""Device to the id of the layer that is on for it."""

_automatic: set = set()
"""The devices whose layer came on by itself. Such a layer stays on whatever its style, since its
context is what ends it, and turning it off by hand is remembered. See L{autoEnable}."""

_declined: dict = {}
"""Device to the contexts whose automatic layer the reader turned off while the context was there.
A declined context stays declined until it goes: turning a layer back on under a reader the next
time a drawing is redrawn would be overruling them."""

_scripts: dict = {}
"""The scripts made for emulated and blocked keys, kept so a repeated key is the same script."""

_installed = False


# Lifetime


def install() -> None:
	"""Read every device's layers and start deciding. Every key passes straight through while no
	layer is on."""
	global _installed
	reload()
	if not _installed:
		inputCore.decide_executeGesture.register(_decide)
		_installed = True


def remove() -> None:
	"""Stop deciding, turn every layer off and forget what was read."""
	global _installed
	if _installed:
		inputCore.decide_executeGesture.unregister(_decide)
		_installed = False
	_active.clear()
	_automatic.clear()
	_declined.clear()
	_sets.clear()


def reload() -> list:
	"""Read every device's layers again, as after a profile switch.

	:return: (device, layer) for each layer that was on and is not in the layers read, which has
		been turned off. A layer still there by id stays on.
	"""
	devices = set(bmConfig.keyLayerDevices()) | set(_active) | set(_sets) | {KEYBOARD}
	previous = dict(_sets)
	_sets.clear()
	for device in devices:
		_sets[device] = _read(device)
	gone = []
	for device, layerId in list(_active.items()):
		if _sets[device].get(layerId) is None:
			del _active[device]
			_automatic.discard(device)
			was = previous.get(device)
			gone.append((device, (was.get(layerId) if was is not None else None) or Layer(id=layerId)))
			log.info(f"{LOG_PREFIX}{device} layer {layerId!r} is not in this profile, so it is off")
	return gone


def _read(device: str) -> LayerSet:
	factory = keyLayers.defaultLayers(device, PLUGIN_MODULE)
	layers, problem = LayerSet.fromText(device, bmConfig.keyLayerText(device), factory)
	if problem:
		log.warning(f"{LOG_PREFIX}{device}: {problem}")
	return layers


def layerSet(device: str) -> LayerSet:
	""":return: a device's layers, reading them if they have not been. Call on the main thread."""
	found = _sets.get(device)
	if found is None:
		found = _sets[device] = _read(device)
	return found


def save(layers: LayerSet) -> None:
	"""Store a device's layers in the profile in force, and use them from now on.

	:raises keyLayers.LayerSetError: if the set cannot be saved, such as one that falls through in a
		circle. Nothing is stored then.
	"""
	text = layers.toText()
	bmConfig.setKeyLayerText(layers.device, text)
	_sets[layers.device] = layers
	activeId = _active.get(layers.device)
	if activeId is not None and layers.get(activeId) is None:
		del _active[layers.device]
		_automatic.discard(layers.device)


# Which layer is on


def activeLayer(device: Optional[str]) -> Optional[Layer]:
	activeId = _active.get(device)
	return layerSet(device).get(activeId) if activeId is not None else None


def activeLayers() -> list:
	""":return: (device, layer) for every layer that is on, by device name."""
	return [(device, layer) for device in sorted(_active) if (layer := activeLayer(device)) is not None]


def activate(device: str, layerId: str) -> Optional[Layer]:
	""":return: the layer now on, or None if the device has no such layer. Turned on by the reader.

	Choosing another layer in place of one that came on by itself turns that one off by the reader's
	hand, so its context is declined as if they had pressed the layer key. Choosing the same layer
	only makes it theirs.
	"""
	layer = layerSet(device).get(layerId)
	if layer is None:
		return None
	if device in _automatic and _active.get(device) != layer.id:
		deactivate(device)
	_active[device] = layer.id
	_automatic.discard(device)
	log.info(f"{LOG_PREFIX}{device} layer {layer.id!r} on, {layer.style}")
	return layer


def deactivate(device: str, byReader: bool = True) -> Optional[Layer]:
	""":return: the layer that was on, now off, or None if none was.

	:param byReader: whether the reader turned it off. An automatic layer the reader turns off keeps
		its context declined until the context goes. See L{_declined}.
	"""
	layer = activeLayer(device)
	if _active.pop(device, None) is not None:
		log.info(f"{LOG_PREFIX}{device} layer off")
	if device in _automatic:
		_automatic.discard(device)
		if byReader and layer is not None and layer.context:
			_declined.setdefault(device, set()).add(layer.context)
			log.info(f"{LOG_PREFIX}{device} {layer.context} context declined until it goes")
	return layer


def isAutomatic(device: str) -> bool:
	return device in _automatic


def autoEnable(present, devices, detected) -> list:
	"""Turn on the layer that comes on by itself for what is on the display, on each device with none on.

	A device that already has a layer on keeps it: the reader chose that, and a drawing going up is not
	a reason to take it away. A context the reader declined is skipped until it goes.

	:param present: the contexts present now, most specific first.
	:param devices: the devices to consider: the connected displays and the keyboard.
	:param detected: the contexts followed as they change. A context declined and no longer present
		is forgotten here, so it comes back next time.
	:return: (device, layer) for each layer turned on.
	"""
	present = list(present)
	for device, contexts in list(_declined.items()):
		contexts.difference_update(context for context in detected if context not in present)
		if not contexts:
			del _declined[device]
	started = []
	for device in devices:
		if device in _active:
			continue
		layer = layerSet(device).autoLayerFor(present, _declined.get(device, ()))
		if layer is None:
			continue
		_active[device] = layer.id
		_automatic.add(device)
		started.append((device, layer))
		log.info(f"{LOG_PREFIX}{device} layer {layer.id!r} on by itself, for its {layer.context} context")
	return started


def toggle(device: str, contexts=()) -> tuple:
	"""The layer key: turn a device's layer off, or turn on the one for what is on the display.

	:param contexts: the contexts present, most specific first. See `keyLayerContexts`.
	:return: whether a layer is now on, and that layer, or the one just turned off.
	"""
	if device in _active:
		return False, deactivate(device)
	return True, activate(device, layerSet(device).forContexts(contexts).id)


def endLayersOutOfContext(present, detected) -> list:
	"""Turn off each layer whose context has gone: a graphics layer once the drawing is off.

	A layer with a context is for that context, so it has nothing left to do when the context
	goes, whether it was turned on by the layer key, chosen from the list or came on by itself.
	L{autoEnable} is the other half.

	:param present: the contexts present now.
	:param detected: the contexts that can be told present at all. A layer for another context is
		left alone, since its context cannot be seen to have gone.
	:return: (device, layer) for each layer turned off.
	"""
	ended = []
	for device in sorted(_active):
		layer = activeLayer(device)
		if layer is None or not layer.context or layer.context not in detected or layer.context in present:
			continue
		deactivate(device, byReader=False)
		ended.append((device, layer))
		log.info(f"{LOG_PREFIX}{device} layer {layer.id!r} off, its {layer.context} context has gone")
	return ended


def reconcile(present, devices, detected) -> tuple:
	"""Bring the layers into line with what is on the display: the one place that decides it.

	Called whenever something that decides it may have changed without the drawing changing: the
	drawing itself, a profile switch that read other layers, and a display reconnecting. Each of those
	alone left a layer on that should have gone, or off that should have come on.

	1. A layer that came on by itself and is no longer marked to, since a profile or an edit changed
	   it, goes. Nothing the reader chose is touched.
	2. A layer whose context has gone goes. See L{endLayersOutOfContext}.
	3. Each device with no layer on takes the one that comes on by itself for what is there. See
	   L{autoEnable}.

	:return: (ended, started), each (device, layer) pairs.
	"""
	ended = []
	for device in sorted(_automatic):
		layer = activeLayer(device)
		if layer is not None and layer.autoEnable:
			continue
		deactivate(device, byReader=False)
		if layer is not None:
			ended.append((device, layer))
			log.info(f"{LOG_PREFIX}{device} layer {layer.id!r} off, it no longer comes on by itself")
	ended.extend(endLayersOutOfContext(present, detected))
	started = autoEnable(present, devices, detected)
	return ended, started


def allOff() -> list:
	""":return: (device, layer) for each layer that was on."""
	was = activeLayers()
	for device, _layer in was:
		deactivate(device)
	_active.clear()
	if was:
		log.info(f"{LOG_PREFIX}all layers off")
	return was


def layerName(layer: Optional[Layer]) -> str:
	""":return: what to call a layer when speaking of it."""
	if layer is None:
		return ""
	if layer.isDefault:
		# Translators: the name of the layer of keys every device has, spoken as in "Default layer on".
		return _("Default")
	return layer.name or layer.id


# Which device


def deviceOf(gesture) -> Optional[str]:
	""":return: the device that raised a gesture: a braille driver's name, `keyboard`, or None."""
	from braille.display.gesture import BrailleDisplayGesture

	if isinstance(gesture, BrailleDisplayGesture):
		return getattr(gesture, "source", None) or None
	try:
		from keyboardHandler import KeyboardInputGesture
	except ImportError:  # pragma: no cover - NVDA always has one.
		return None
	return KEYBOARD if isinstance(gesture, KeyboardInputGesture) else None


def deviceFor(gesture) -> Optional[str]:
	""":return: the display a binding said a gesture acts for, else the device that raised it."""
	return getattr(gesture, ACTS_FOR_ATTRIBUTE, None) or deviceOf(gesture)


def displayFor(gesture) -> Optional[str]:
	""":return: the braille display a gesture is for, or None for a keyboard key acting for none.

	What a command that used to read `gesture.source` reads instead: the same answer for a
	display's own key, and the named display for a keyboard key a binding said acts for one.
	"""
	device = deviceFor(gesture) if gesture is not None else None
	return None if device in (None, KEYBOARD) else device


# Deciding


def _identifiers(gesture) -> list:
	identifiers = getattr(gesture, "normalizedIdentifiers", None)
	if identifiers is None:
		identifiers = [keyLayers.normalize(identifier) for identifier in getattr(gesture, "identifiers", ())]
	return list(identifiers)


def _capturing() -> bool:
	return getattr(inputCore.manager, "_captureFunc", None) is not None


def _standAside(gesture) -> bool:
	""":return: whether this gesture is none of a layer's business, whatever is on."""
	if getattr(gesture, "isModifier", False):
		return True
	# Input Gestures, or a layer editor, waiting for a key. Answering would bind the wrong thing.
	# Input help is a capture function too, and is let through so it reports the layer.
	return _capturing() and not getattr(inputCore.manager, "isInputHelpActive", False)


def _ordinaryScriptName(gesture) -> Optional[str]:
	""":return: what NVDA's own lookup finds for a key, as a script name without `script_`.

	Reading `gesture.script` runs the lookup NVDA was about to run anyway, and caches it there.
	"""
	name = getattr(getattr(gesture, "script", None), "__name__", None)
	if not isinstance(name, str):
		return None
	return name[len("script_") :] if name.startswith("script_") else name


def _isLayerCommand(name: Optional[str]) -> bool:
	return bool(name) and name.startswith(LAYER_COMMAND_PREFIX)


def _decide(gesture=None, **kwargs) -> bool:
	"""Give a key what its device's layer says. Registered on `decide_executeGesture`.

	Never refuses a gesture: returning False would drop it, and a layer chooses what a key does,
	not whether it does anything.
	"""
	try:
		if not _active or gesture is None or _standAside(gesture):
			return True
		device = deviceOf(gesture)
		activeId = _active.get(device)
		layers = _sets.get(device)
		if activeId is None or layers is None:
			return True
		identifiers = _identifiers(gesture)
		decision = keyLayers.decide(
			layers,
			activeId,
			identifiers,
			lambda: _ordinaryScriptName(gesture),
			hasCells=bool(getattr(gesture, "cellIndexes", None)),
			isLayerCommand=_isLayerCommand,
		)
		key = identifiers[0] if identifiers else "?"
		if decision.action == keyLayers.RUN:
			_run(gesture, device, key, decision)
		elif decision.action == keyLayers.LEAVE:
			gesture.script = _leaveScript(device)
			log.info(f"{LOG_PREFIX}{key} on {device} leaves the {decision.layer.id!r} layer")
		else:
			log.debug(f"{LOG_PREFIX}{key} on {device} passes through")
		if (
			decision.endsOneShot
			and device not in _automatic
			and not _capturing()
			and _active.get(device) == activeId
		):
			del _active[device]
			log.info(f"{LOG_PREFIX}{device} one shot layer {activeId!r} used, so off")
	except Exception:
		log.error(f"{LOG_PREFIX}could not decide a gesture", exc_info=True)
	return True


def _run(gesture, device: str, key: str, decision) -> None:
	target: Target = decision.target
	script, found = scriptFor(target, gesture)
	# A key that does nothing is safe anywhere. Refusing it would leave the key to NVDA, which could
	# run its ordinary command, and "does nothing in this layer" has to hold on the lock screen too.
	if target.kind != keyLayers.BLOCKED and not allowedOnLockScreen(script):
		# Left to NVDA, whose own lookup applies the same rule to whatever the key ordinarily does.
		log.info(f"{LOG_PREFIX}{key} on {device}: {target.describe()} refused on the lock screen")
		return
	if found is None and target.kind == keyLayers.SCRIPT:
		log.info(f"{LOG_PREFIX}{key} on {device}: {target.describe()} is not available here")
	else:
		log.info(
			f"{LOG_PREFIX}{key} on {device} runs {target.describe()} from the {decision.layer.id!r} layer"
			+ (f" on {type(found).__name__}" if found is not None else "")
			+ (f", acting for {target.actsFor}" if target.actsFor else ""),
		)
	if target.actsFor:
		setattr(gesture, ACTS_FOR_ATTRIBUTE, target.actsFor)
	gesture.script = script


def scriptFor(target: Target, gesture) -> tuple:
	""":return: the script a target runs and the object it is on, which is None for an emulated or
	blocked key. A script whose object is not here comes back as one saying so, with None."""
	if target.kind == keyLayers.KEY:
		return _emulateScript(target.scriptName), None
	if target.kind == keyLayers.BLOCKED:
		return _blockedScript(), None
	script, found = resolve(target, gesture)
	if script is None:
		return _unavailableScript(target), None
	return script, found


def _class(target: Target):
	module = sys.modules.get(target.moduleName)
	if module is None:
		try:
			module = importlib.import_module(target.moduleName)
		except ImportError:
			return None
	return getattr(module, target.className, None)


def resolve(target: Target, gesture) -> tuple:
	"""Find a live object of the target's class, and the script on it.

	Walks the objects `scriptHandler._yieldObjectsForFindScript` offers, in its order, taking the
	first instance of the class. Written out rather than calling that private generator, so that
	an upstream change breaks a test here rather than every layer at runtime.

	:return: the bound script and the object it is on, or (None, None).
	"""
	cls = _class(target)
	if cls is None:
		return None, None
	attribute = f"script_{target.scriptName}"
	for obj, rule in _candidates(gesture, target):
		if obj is None or not isinstance(obj, cls):
			continue
		script = getattr(obj, attribute, None)
		if script is None:
			continue
		if rule == "treeInterceptor" and getattr(obj, "passThrough", False):
			if not getattr(script, "ignoreTreeInterceptorPassThrough", False):
				continue
		if rule == "ancestor" and not getattr(script, "canPropagate", False):
			continue
		return script, obj
	return None, None


def _candidates(gesture, target: Target):
	""":yield: (object, rule) in the order NVDA looks for a script, with the rule that filters it."""
	import api
	import braille
	import globalCommands
	import globalPluginHandler

	focus = api.getFocusObject()
	for plugin in globalPluginHandler.runningPlugins:
		yield plugin, None
	yield getattr(focus, "appModule", None), None
	display = braille.handler.display if braille.handler else None
	if display is not None:
		# A member's own scripts live on the member, which the virtual display is not an
		# instance of. See `brlMultilineVirtual.gestures.scriptForMember`.
		slotFor = getattr(display, "slotForDriverName", None)
		wanted = target.actsFor or deviceOf(gesture)
		slot = slotFor(wanted) if slotFor is not None and wanted else None
		if slot is not None:
			yield slot.driver, None
		yield display, None
	try:
		import vision

		if vision.handler:
			for provider in vision.handler.getActiveProviderInstances():
				yield provider, None
	except ImportError:
		pass
	treeInterceptor = getattr(focus, "treeInterceptor", None)
	if treeInterceptor is not None and getattr(treeInterceptor, "isReady", False):
		yield treeInterceptor, "treeInterceptor"
	yield focus, None
	for ancestor in reversed(list(api.getFocusAncestors())):
		yield ancestor, "ancestor"
	yield getattr(globalCommands, "configProfileActivationCommands", None), None
	yield globalCommands.commands, None


def allowedOnLockScreen(script) -> bool:
	""":return: whether a script may run, which on the Windows lock screen means it is on NVDA's safe list.

	`scriptHandler.findScript` applies this rule, and assigning a script skips `findScript`, so a
	layer that did not repeat it would be a way round the lock screen.
	"""
	try:
		from utils.security import getSafeScripts
		from winAPI.sessionTracking import isLockScreenModeActive
	except ImportError:
		return True
	if not isLockScreenModeActive():
		return True
	return script in getSafeScripts()


# Scripts made here


def _leaveScript(device: str):
	def script_keyLayerLeave(gesture):
		layer = deactivate(device)
		if layer is not None:
			# Translators: reported when a layer of keys is turned off. The placeholder is its name.
			ui.message(_("{name} layer off").format(name=layerName(layer)))

	# Translators: input help for a key that turns a layer of keys off.
	script_keyLayerLeave.__doc__ = _("Layered keys: turns the layer off")
	return script_keyLayerLeave


def _emulateScript(key: str):
	"""A script that presses a keyboard key, as `scriptHandler._makeKbEmulateScript` makes one."""
	cached = _scripts.get(key)
	if cached is not None:
		return cached

	def script(gesture):
		import keyboardHandler

		inputCore.manager.emulateGesture(keyboardHandler.KeyboardInputGesture.fromName(key[len("kb:") :]))

	script.__name__ = f"script_{key}"
	# Translators: input help for a key in a layer that presses a keyboard key. The placeholder is
	# the keyboard key.
	script.__doc__ = _("Emulates pressing {key} on the system keyboard").format(key=key[len("kb:") :])
	_scripts[key] = script
	return script


def _blockedScript():
	cached = _scripts.get(keyLayers.BLOCKED)
	if cached is not None:
		return cached

	def script_keyLayerBlocked(gesture):
		pass

	# Translators: input help for a key a layer has switched off.
	script_keyLayerBlocked.__doc__ = _("Does nothing in this layer")
	_scripts[keyLayers.BLOCKED] = script_keyLayerBlocked
	return script_keyLayerBlocked


def _unavailableScript(target: Target):
	cls = _class(target)
	command = getattr(cls, f"script_{target.scriptName}", None) if cls is not None else None
	name = (getattr(command, "__doc__", None) or target.scriptName).splitlines()[0]

	def script_keyLayerUnavailable(gesture):
		# Translators: reported when a key in a layer names a command that cannot run where the
		# reader is, such as a browse mode command outside a web page. The placeholder is its name.
		ui.message(_("{command} is not available here").format(command=name))

	script_keyLayerUnavailable.__doc__ = name
	return script_keyLayerUnavailable
