# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for layered keys, phase 0: choosing a key's command before NVDA looks for one.

What is held to here is what the hardware run cannot show cheaply: that a key outside the
layer is never touched, that another display's keys are never touched, that the decider never
changes state, and that the lock screen rule `findScript` applies is applied here too. Whether
NVDA then runs what was chosen is the hardware's question, and `docs/design/layered-keys-plan.md`
lists it.
"""

import sys
import types
import unittest

from ._stubs import BrailleDisplayGesture, KeyboardInputGesture, installStubs, loadPlugin, log, spokenMessages

installStubs()

import api  # noqa: E402
import braille  # noqa: E402
import globalPluginHandler  # noqa: E402
import inputCore  # noqa: E402

from brlMultiline import keyLayerDispatch as layers  # noqa: E402
from brlMultiline.keyLayerDispatch import Binding, normalize  # noqa: E402

MONARCH = layers.MONARCH
KEYBOARD = layers.KEYBOARD
MODULE = "_keyLayerTargets"


class Plugin:
	def script_panUp(self, gesture):
		"""Moves the drawing up"""
		self.ran = gesture


class Commands:
	def script_sayLine(self, gesture):
		"""Reports the current line"""

	def script_dateTime(self, gesture):
		"""Reports the time"""


class Document:
	passThrough = False

	def script_nextHeading(self, gesture):
		"""Moves to the next heading"""


class Ancestor:
	def script_propagates(self, gesture):
		"""Propagates"""

	script_propagates.canPropagate = True

	def script_staysPut(self, gesture):
		"""Does not propagate"""


class Member:
	def script_wizWheel(self, gesture):
		"""Cycles the wiz wheel"""


targets = types.ModuleType(MODULE)
for cls in (Plugin, Commands, Document, Ancestor, Member):
	setattr(targets, cls.__name__, cls)
sys.modules[MODULE] = targets


def bound(className, scriptName, actsFor=None):
	return Binding(MODULE, className, scriptName, actsFor)


class BrailleKey(BrailleDisplayGesture):
	def __init__(self, source, key, ordinary=None):
		self.source = source
		self.identifiers = [f"br({source}):{key}"]
		self.script = ordinary


class Key(KeyboardInputGesture):
	def __init__(self, key, ordinary=None, isModifier=False):
		self.identifiers = [f"kb:{key}"]
		self.script = ordinary
		self.isModifier = isModifier


def ordinaryScript(gesture):
	"""What the key does without a layer."""


def escapeScript(gesture):
	"""Emulates escape."""


escapeScript.__name__ = "script_kb:escape"


class KeyLayerTestCase(unittest.TestCase):
	def setUp(self):
		self.saved = (
			layers.TEST_LAYERS,
			layers.EXIT_KEYS,
			globalPluginHandler.__dict__.get("runningPlugins"),
			api.__dict__.get("getFocusObject"),
			api.__dict__.get("getFocusAncestors"),
			braille.handler,
			sys.modules["globalCommands"].commands,
		)
		self.plugin = Plugin()
		self.commands = Commands()
		self.focus = types.SimpleNamespace(appModule=None, treeInterceptor=None)
		self.ancestors = []
		globalPluginHandler.runningPlugins = [self.plugin]
		api.getFocusObject = lambda: self.focus
		api.getFocusAncestors = lambda: self.ancestors
		sys.modules["globalCommands"].commands = self.commands
		braille.handler = None
		layers.TEST_LAYERS = {
			MONARCH: {
				normalize(f"br({MONARCH}):leftDpadUp"): bound("Plugin", "panUp"),
				normalize(f"br({MONARCH}):zoomIn"): bound("Commands", "sayLine"),
				normalize(f"br({MONARCH}):space+dot1"): bound("Document", "nextHeading"),
			},
			KEYBOARD: {
				normalize("kb:numpad8"): bound("Plugin", "panUp", MONARCH),
			},
		}
		layers.EXIT_KEYS = {
			MONARCH: frozenset({normalize(f"br({MONARCH}):space+dot1+dot3+dot5+dot6")}),
			KEYBOARD: frozenset({normalize("kb:escape")}),
		}
		layers._active.clear()
		inputCore.manager._captureFunc = None
		inputCore.manager.isInputHelpActive = False
		spokenMessages.clear()
		log.messages.clear()

	def tearDown(self):
		(
			layers.TEST_LAYERS,
			layers.EXIT_KEYS,
			runningPlugins,
			getFocusObject,
			getFocusAncestors,
			braille.handler,
			sys.modules["globalCommands"].commands,
		) = self.saved
		globalPluginHandler.runningPlugins = runningPlugins or []
		if getFocusObject is not None:
			api.getFocusObject = getFocusObject
		if getFocusAncestors is None:
			api.__dict__.pop("getFocusAncestors", None)
		else:
			api.getFocusAncestors = getFocusAncestors
		layers._active.clear()
		inputCore.manager._captureFunc = None
		for name in ("winAPI", "winAPI.sessionTracking", "utils", "utils.security"):
			sys.modules.pop(name, None)

	def decide(self, gesture):
		self.assertTrue(layers._decide(gesture=gesture), "a layer must never drop a gesture")
		return gesture.script


class TestChoosing(KeyLayerTestCase):
	def test_nothingIsTouchedWhileNoLayerIsOn(self):
		gesture = BrailleKey(MONARCH, "leftDpadUp", ordinaryScript)
		self.assertIs(ordinaryScript, self.decide(gesture))

	def test_aBoundKeyRunsTheLayersCommandOnTheLiveObject(self):
		layers.toggle(MONARCH)
		script = self.decide(BrailleKey(MONARCH, "leftDpadUp", ordinaryScript))
		self.assertEqual(self.plugin.script_panUp, script)

	def test_theScriptIsTheTargetsOwnBoundMethodSoRepeatsCount(self):
		"""`executeScript` counts a repeat by comparing `script.__func__`. A wrapper would never repeat."""
		layers.toggle(MONARCH)
		script = self.decide(BrailleKey(MONARCH, "zoomIn"))
		self.assertIs(Commands.script_sayLine, script.__func__)
		self.assertIs(self.commands, script.__self__)

	def test_anUnboundKeyIsLeftToNvda(self):
		"""Transparent: the right d-pad is still the arrow keys, and dot chords still type."""
		layers.toggle(MONARCH)
		self.assertIs(ordinaryScript, self.decide(BrailleKey(MONARCH, "rightDpadUp", ordinaryScript)))
		self.assertTrue(layers.isActive(MONARCH))

	def test_anotherDisplaysKeysAreNeverTouched(self):
		layers.toggle(MONARCH)
		gesture = BrailleKey("freedomScientific", "leftDpadUp", ordinaryScript)
		self.assertIs(ordinaryScript, self.decide(gesture))

	def test_theKeyboardIsItsOwnDevice(self):
		layers.toggle(MONARCH)
		self.assertIs(ordinaryScript, self.decide(Key("numpad8", ordinaryScript)))
		layers.toggle(KEYBOARD)
		self.assertEqual(self.plugin.script_panUp, self.decide(Key("numpad8", ordinaryScript)))

	def test_aKeyboardBindingActsForItsDisplay(self):
		layers.toggle(KEYBOARD)
		gesture = Key("numpad8")
		self.decide(gesture)
		self.assertEqual(MONARCH, layers.deviceFor(gesture))
		self.assertEqual(KEYBOARD, layers.deviceOf(gesture))

	def test_aBrailleKeyIsForItsOwnDisplay(self):
		self.assertEqual(MONARCH, layers.deviceFor(BrailleKey(MONARCH, "leftDpadUp")))

	def test_theDeciderNeverChangesWhichLayersAreOn(self):
		"""State changes in scripts, on the main thread, where a gesture is not abandoned."""
		layers.toggle(MONARCH)
		gesture = BrailleKey(MONARCH, "space+dot1+dot3+dot5+dot6")
		self.decide(gesture)
		self.assertTrue(layers.isActive(MONARCH))
		gesture.script(gesture)
		self.assertFalse(layers.isActive(MONARCH))
		self.assertIn("Test layer off", spokenMessages)


class TestStandingAside(KeyLayerTestCase):
	def setUp(self):
		super().setUp()
		layers.toggle(MONARCH)

	def test_aModifierIsLeftAlone(self):
		layers.toggle(KEYBOARD)
		self.assertIs(ordinaryScript, self.decide(Key("numpad8", ordinaryScript, isModifier=True)))

	def test_aDialogWaitingForAKeyGetsTheKey(self):
		inputCore.manager._captureFunc = lambda gesture: False
		self.assertIs(ordinaryScript, self.decide(BrailleKey(MONARCH, "leftDpadUp", ordinaryScript)))

	def test_inputHelpIsToldTheLayersCommand(self):
		inputCore.manager._captureFunc = lambda gesture: False
		inputCore.manager.isInputHelpActive = True
		self.assertEqual(self.plugin.script_panUp, self.decide(BrailleKey(MONARCH, "leftDpadUp")))

	def test_aFailureLetsTheKeyThrough(self):
		gesture = BrailleKey(MONARCH, "leftDpadUp", ordinaryScript)
		gesture.identifiers = None
		self.assertIs(ordinaryScript, self.decide(gesture))
		self.assertTrue(any(level == "error" for level, _message in log.messages))


class TestLeaving(KeyLayerTestCase):
	def setUp(self):
		super().setUp()
		layers.toggle(MONARCH)
		layers.toggle(KEYBOARD)

	def test_spaceWithZLeaves(self):
		gesture = BrailleKey(MONARCH, "dot6+dot5+space+dot3+dot1", ordinaryScript)
		script = self.decide(gesture)
		self.assertIsNot(ordinaryScript, script)
		script(gesture)
		self.assertFalse(layers.isActive(MONARCH))
		self.assertTrue(layers.isActive(KEYBOARD))

	def test_aKeyWhoseOrdinaryCommandIsEscapeLeaves(self):
		"""The display's own escape chord, whatever its gesture map makes it."""
		gesture = BrailleKey(MONARCH, "space+dot1+dot5", escapeScript)
		script = self.decide(gesture)
		self.assertIsNot(escapeScript, script)
		script(gesture)
		self.assertFalse(layers.isActive(MONARCH))

	def test_escapeOnTheKeyboardLeavesOnlyTheKeyboardsLayer(self):
		gesture = Key("escape")
		self.decide(gesture).__call__(gesture)
		self.assertFalse(layers.isActive(KEYBOARD))
		self.assertTrue(layers.isActive(MONARCH))


class TestFinding(KeyLayerTestCase):
	def setUp(self):
		super().setUp()
		layers.toggle(MONARCH)

	def test_aCommandWithNoObjectHereSaysSo(self):
		gesture = BrailleKey(MONARCH, "space+dot1", ordinaryScript)
		script = self.decide(gesture)
		self.assertIsNot(ordinaryScript, script)
		script(gesture)
		self.assertIn("Moves to the next heading is not available here", spokenMessages)

	def test_aTreeInterceptorCommandIsFound(self):
		self.focus.treeInterceptor = Document()
		self.focus.treeInterceptor.isReady = True
		script = self.decide(BrailleKey(MONARCH, "space+dot1"))
		self.assertEqual(self.focus.treeInterceptor.script_nextHeading, script)

	def test_notInFocusMode(self):
		"""NVDA's own rule: a tree interceptor's commands do not run while it passes keys through."""
		document = Document()
		document.isReady = True
		document.passThrough = True
		self.focus.treeInterceptor = document
		script, found = layers.resolve(bound("Document", "nextHeading"), BrailleKey(MONARCH, "space+dot1"))
		self.assertIsNone(script)

	def test_anAncestorOnlyOffersCommandsThatPropagate(self):
		self.ancestors = [Ancestor()]
		gesture = BrailleKey(MONARCH, "x")
		self.assertIsNotNone(layers.resolve(bound("Ancestor", "propagates"), gesture)[0])
		self.assertIsNone(layers.resolve(bound("Ancestor", "staysPut"), gesture)[0])

	def test_aMembersOwnCommandIsFoundOnTheMember(self):
		"""The virtual display is not an instance of the member's class, so it has to be offered."""
		member = Member()
		slots = {"freedomScientific": types.SimpleNamespace(driver=member)}
		braille.handler = types.SimpleNamespace(display=types.SimpleNamespace(slotForDriverName=slots.get))
		binding = bound("Member", "wizWheel", "freedomScientific")
		script, found = layers.resolve(binding, Key("numpad8"))
		self.assertIs(member, found)

	def test_anUnknownClassFindsNothing(self):
		self.assertEqual((None, None), layers.resolve(Binding(MODULE, "Nope", "x"), BrailleKey(MONARCH, "x")))


class TestLockScreen(KeyLayerTestCase):
	def setUp(self):
		super().setUp()
		layers.toggle(MONARCH)
		self.locked = True
		sys.modules["winAPI"] = types.ModuleType("winAPI")
		sys.modules["winAPI.sessionTracking"] = types.SimpleNamespace(
			isLockScreenModeActive=lambda: self.locked
		)
		sys.modules["utils"] = types.ModuleType("utils")
		sys.modules["utils.security"] = types.SimpleNamespace(
			getSafeScripts=lambda: {self.commands.script_dateTime},
		)

	def test_anUnsafeCommandIsRefusedAndTheKeyLeftToNvda(self):
		"""Assigning a script skips `findScript`, which is where NVDA applies this rule."""
		gesture = BrailleKey(MONARCH, "leftDpadUp", ordinaryScript)
		self.assertIs(ordinaryScript, self.decide(gesture))

	def test_aSafeCommandRuns(self):
		layers.TEST_LAYERS[MONARCH][normalize(f"br({MONARCH}):leftDpadUp")] = bound("Commands", "dateTime")
		self.assertEqual(self.commands.script_dateTime, self.decide(BrailleKey(MONARCH, "leftDpadUp")))

	def test_unlockedEverythingRuns(self):
		self.locked = False
		self.assertEqual(self.plugin.script_panUp, self.decide(BrailleKey(MONARCH, "leftDpadUp")))


class TestTheTestLayer(unittest.TestCase):
	"""The real phase 0 tables, against the real plugin."""

	def test_everyPluginCommandExists(self):
		plugin = loadPlugin()
		saved = layers.TEST_LAYERS
		for device, layer in saved.items():
			for identifier, binding in layer.items():
				if binding.moduleName == layers._PLUGIN[0]:
					self.assertTrue(
						hasattr(plugin.GlobalPlugin, f"script_{binding.scriptName}"),
						f"{identifier} on {device} names {binding.scriptName}, which the plugin does not have",
					)

	def test_theRightPadIsLeftAlone(self):
		monarch = layers.TEST_LAYERS[MONARCH]
		self.assertIn(normalize(f"br({MONARCH}):leftDpadUp"), monarch)
		self.assertFalse(any("rightdpad" in identifier for identifier in monarch))

	def test_keypadBindingsActForTheMonarch(self):
		for identifier, binding in layers.TEST_LAYERS[KEYBOARD].items():
			if binding.moduleName == layers._PLUGIN[0]:
				self.assertEqual(MONARCH, binding.actsFor, identifier)

	def test_identifiersAreNormalized(self):
		for layer in layers.TEST_LAYERS.values():
			for identifier in layer:
				self.assertEqual(normalize(identifier), identifier)

	def test_normalizeSortsTheKeys(self):
		self.assertEqual("br(x):dot1+space", normalize("br(X):Space+DOT1"))

	def test_installAndRemove(self):
		layers.install()
		try:
			self.assertIn(layers._decide, inputCore.decide_executeGesture.handlers)
			layers.toggle(MONARCH)
		finally:
			layers.remove()
		self.assertNotIn(layers._decide, inputCore.decide_executeGesture.handlers)
		self.assertFalse(layers.isActive(MONARCH))


if __name__ == "__main__":
	unittest.main()
