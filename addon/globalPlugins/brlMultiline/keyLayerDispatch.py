# BrlMultiline: choosing a key's command from a layer, before NVDA looks for one.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Layered keys, phase 0: one hard coded layer per device, to prove the mechanism on hardware.

Read `docs/design/layered-keys-plan.md` first. This is its phase 0 and nothing more: no model,
no storage, no dialog. It exists to answer the questions that plan lists before anything is
built on the answers.

**The mechanism.** `inputCore.InputManager.executeGesture` asks `decide_executeGesture` before
it reads `gesture.script`. That property's getter has no setter, so an instance attribute
assigned in the decider shadows it, and everything after — input help, sleep mode, speech
cancel, the repeat count — treats the layer's command exactly as a key bound in Input Gestures.
A key the layer does not bind is left alone and NVDA looks it up as it always does.

**State changes in scripts, never in the decider.** The decider runs on the thread the driver
dispatched from and the script later, on the main thread, and several paths between the two
abandon a gesture: input help captures it, sleep mode refuses it. `panning.py` explains how
recording anything at decision time went wrong there. So turning a layer on or off happens
in the script that says so, and the decider only reads which layers are on. The cost is that
a key pressed faster than the toggle's script runs is decided against the old state, which
phase 0 is also watching for.

**Which device.** A braille gesture's `source` is its driver's name, and through the virtual
display each member's keys keep their own, so the Monarch and the Focus 80 are two devices
with no knowledge of composites needed here. A keyboard gesture has no source and is the
device `keyboard`.

**Acts for.** A keyboard binding may name a display it acts for, recorded on the gesture
object because that same object is what the script is later called with. L{deviceFor} answers
from it, so a command asking which display it is for gets the Monarch from a keypad key.

Everything logged here starts `BrlMultiline key layers:` so a hardware run can be read back
out of NVDA's log.
"""

import importlib
import sys
from typing import NamedTuple, Optional

import inputCore
import ui
from logHandler import log

KEYBOARD = "keyboard"
"""The device name for keyboard gestures, which carry no source of their own."""

MONARCH = "brlMultilineMonarch"

ACTS_FOR_ATTRIBUTE = "_brlMultilineActsFor"
"""Where the display a keyboard binding acts for is recorded on the gesture."""

LOG_PREFIX = "BrlMultiline key layers: "


class Binding(NamedTuple):
	"""What a key in a layer runs: a script named as NVDA's gesture maps name one."""

	moduleName: str
	className: str
	scriptName: str
	"""Without the `script_` prefix."""

	actsFor: Optional[str] = None
	"""The display a keyboard binding acts for, or None."""


def normalize(identifier: str) -> str:
	"""Normalize a gesture identifier as `inputCore.normalizeGestureIdentifier` does.

	Copied rather than called so the tables below can be built at import, before NVDA's input
	core is guaranteed to be importable in a test. Lower case, and the parts after the source
	sorted, so `space+dot1` and `dot1+space` are one key.
	"""
	identifier = identifier.lower()
	prefix, _, main = identifier.partition(":")
	return f"{prefix}:{'+'.join(sorted(main.split('+')))}"


_PLUGIN = (__package__, "GlobalPlugin")
"""This add-on's plugin, as a module and class name. Its package is its module."""

_COMMANDS = ("globalCommands", "GlobalCommands")


def _plugin(scriptName: str, actsFor: Optional[str] = None) -> Binding:
	return Binding(*_PLUGIN, scriptName, actsFor)


def _command(scriptName: str, actsFor: Optional[str] = None) -> Binding:
	return Binding(*_COMMANDS, scriptName, actsFor)


def _monarch(key: str) -> str:
	return normalize(f"br({MONARCH}):{key}")


def _keys(names, binding: Binding) -> dict[str, Binding]:
	return {normalize(f"kb:{name}"): binding for name in names}


TEST_LAYERS: dict[str, dict[str, Binding]] = {
	MONARCH: {
		_monarch("leftDpadUp"): _plugin("graphicsPanUp"),
		_monarch("leftDpadDown"): _plugin("graphicsPanDown"),
		_monarch("leftDpadLeft"): _plugin("graphicsPanLeft"),
		_monarch("leftDpadRight"): _plugin("graphicsPanRight"),
		_monarch("zoomIn"): _plugin("graphicsZoomIn"),
		_monarch("zoomOut"): _plugin("graphicsZoomOut"),
		# No say line here. The Monarch's d-pads have no centre press: pressing the middle gives
		# two directions at once, whichever two the finger lands between. Found on hardware.
	},
	KEYBOARD: {
		# Both spellings of each keypad key, with num lock off and on.
		**_keys(("numpad8", "numLockNumpad8"), _plugin("graphicsPanUp", MONARCH)),
		**_keys(("numpad2", "numLockNumpad2"), _plugin("graphicsPanDown", MONARCH)),
		**_keys(("numpad4", "numLockNumpad4"), _plugin("graphicsPanLeft", MONARCH)),
		**_keys(("numpad6", "numLockNumpad6"), _plugin("graphicsPanRight", MONARCH)),
		# Num lock is a modifier on these two when it is on. See `KeyboardInputGesture`.
		**_keys(("numpadPlus", "numLock+numpadPlus"), _plugin("graphicsZoomIn", MONARCH)),
		**_keys(("numpadMinus", "numLock+numpadMinus"), _plugin("graphicsZoomOut", MONARCH)),
		# Say line, on another object entirely: what `bindGesture` could never reach, and the
		# repeat count test, since pressed twice it spells.
		**_keys(("numpad5", "numLockNumpad5"), _command("reportCurrentLine")),
	},
}
"""The phase 0 layers. The Monarch's left d-pad pans and its zoom keys zoom; the keypad does the
same acting for the Monarch. The right d-pad is unbound, so it stays the arrow keys."""

EXIT_KEYS: dict[str, frozenset[str]] = {
	# Space with z: escape on most braille displays, though not in `hidBrailleStandard`'s map.
	MONARCH: frozenset({_monarch("space+dot1+dot3+dot5+dot6")}),
	KEYBOARD: frozenset({normalize("kb:escape")}),
}
"""Keys that leave a layer whatever it binds. Any key whose ordinary command is escape does too."""

_active: set[str] = set()
"""The devices whose test layer is on. Changed only on the main thread, by scripts."""

_installed = False


def install() -> None:
	"""Start deciding. Every key passes straight through while no layer is on."""
	global _installed
	if _installed:
		return
	inputCore.decide_executeGesture.register(_decide)
	_installed = True


def remove() -> None:
	"""Stop deciding and turn every layer off."""
	global _installed
	if _installed:
		inputCore.decide_executeGesture.unregister(_decide)
		_installed = False
	_active.clear()


def isActive(device: str) -> bool:
	return device in _active


def activeDevices() -> list[str]:
	return sorted(_active)


def hasLayer(device: Optional[str]) -> bool:
	return device in TEST_LAYERS


def toggle(device: str) -> bool:
	"""Turn a device's test layer on or off. Call from a script.

	:return: whether it is now on.
	"""
	if device in _active:
		_active.discard(device)
		log.info(f"{LOG_PREFIX}{device} layer off")
		return False
	_active.add(device)
	log.info(f"{LOG_PREFIX}{device} layer on")
	return True


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
	""":return: the display a gesture acts for when a binding named one, else the device that raised it."""
	return getattr(gesture, ACTS_FOR_ATTRIBUTE, None) or deviceOf(gesture)


def _identifiers(gesture) -> list[str]:
	identifiers = getattr(gesture, "normalizedIdentifiers", None)
	if identifiers is None:
		identifiers = [normalize(identifier) for identifier in getattr(gesture, "identifiers", ())]
	return list(identifiers)


def _standAside(gesture) -> bool:
	""":return: whether this gesture is none of a layer's business, whatever is on."""
	if getattr(gesture, "isModifier", False):
		return True
	manager = inputCore.manager
	# Input Gestures, or a later layer editor, waiting for a key. Answering would bind the wrong
	# thing. Input help is a capture function too, and is let through so it reports the layer.
	capture = getattr(manager, "_captureFunc", None)
	return capture is not None and not getattr(manager, "isInputHelpActive", False)


def _decide(gesture=None, **kwargs) -> bool:
	"""Give a key its layer's command. Registered on `decide_executeGesture`.

	Never refuses a gesture: returning False would drop it, and a layer's job is to choose what a
	key does, not whether it does anything.
	"""
	try:
		if not _active or gesture is None or _standAside(gesture):
			return True
		device = deviceOf(gesture)
		if device not in _active:
			return True
		identifiers = _identifiers(gesture)
		layer = TEST_LAYERS.get(device, {})
		for identifier in identifiers:
			binding = layer.get(identifier)
			if binding is not None:
				_useBinding(gesture, device, identifier, binding)
				return True
		if any(identifier in EXIT_KEYS.get(device, ()) for identifier in identifiers) or _isEscape(gesture):
			gesture.script = _leaveScript(device)
			log.info(f"{LOG_PREFIX}{identifiers[0] if identifiers else '?'} on {device} leaves the layer")
			return True
		log.debug(f"{LOG_PREFIX}{identifiers[0] if identifiers else '?'} on {device} is not in the layer")
	except Exception:
		log.error(f"{LOG_PREFIX}could not decide a gesture", exc_info=True)
	return True


def _useBinding(gesture, device: str, identifier: str, binding: Binding) -> None:
	script, found = resolve(binding, gesture)
	if script is None:
		script = _unavailableScript(binding)
		log.info(f"{LOG_PREFIX}{identifier} on {device}: {_describe(binding)} is not available here")
	elif not allowedOnLockScreen(script):
		# Left to NVDA, whose own lookup applies the same rule to whatever the key ordinarily does.
		log.info(f"{LOG_PREFIX}{identifier} on {device}: {_describe(binding)} refused on the lock screen")
		return
	else:
		log.info(
			f"{LOG_PREFIX}{identifier} on {device} runs {_describe(binding)} "
			f"on {type(found).__name__}" + (f", acting for {binding.actsFor}" if binding.actsFor else ""),
		)
	if binding.actsFor:
		setattr(gesture, ACTS_FOR_ATTRIBUTE, binding.actsFor)
	gesture.script = script


def _describe(binding: Binding) -> str:
	return f"{binding.moduleName}.{binding.className}.{binding.scriptName}"


def _isEscape(gesture) -> bool:
	""":return: whether the key's ordinary command is escape, as NVDA's own lookup finds it.

	Asked only of a key the layer does not bind. Reading `gesture.script` here runs the lookup
	NVDA was about to run anyway, and the answer is cached on the gesture for it.
	"""
	script = getattr(gesture, "script", None)
	return getattr(script, "__name__", "") == "script_kb:escape"


def _class(binding: Binding):
	module = sys.modules.get(binding.moduleName)
	if module is None:
		try:
			module = importlib.import_module(binding.moduleName)
		except ImportError:
			return None
	return getattr(module, binding.className, None)


def resolve(binding: Binding, gesture) -> tuple:
	"""Find a live object of the binding's class, and the script on it.

	Walks the objects `scriptHandler._yieldObjectsForFindScript` offers, in its order, taking the
	first instance of the class. Written out rather than calling that private generator, so that
	an upstream change breaks a test here rather than every layer at runtime.

	:return: the bound script and the object it is on, or (None, None).
	"""
	cls = _class(binding)
	if cls is None:
		return None, None
	attribute = f"script_{binding.scriptName}"
	for obj, rule in _candidates(gesture, binding):
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


def _candidates(gesture, binding: Binding):
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
		wanted = binding.actsFor or deviceOf(gesture)
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


def _leaveScript(device: str):
	def script_leaveKeyLayer(gesture):
		if device in _active:
			toggle(device)
		# Translators: reported when a layer of key commands is turned off.
		ui.message(_("Test layer off"))

	# Translators: input help for a key that leaves a layer of key commands.
	script_leaveKeyLayer.__doc__ = _("Layered keys: leaves the test layer")
	return script_leaveKeyLayer


def _unavailableScript(binding: Binding):
	cls = _class(binding)
	target = getattr(cls, f"script_{binding.scriptName}", None) if cls is not None else None
	name = (getattr(target, "__doc__", None) or binding.scriptName).splitlines()[0]

	def script_keyLayerUnavailable(gesture):
		# Translators: reported when a key in a layer names a command that cannot run where the
		# reader is, such as a browse mode command outside a web page. The placeholder is its name.
		ui.message(_("{command} is not available here").format(command=name))

	script_keyLayerUnavailable.__doc__ = name
	return script_keyLayerUnavailable
