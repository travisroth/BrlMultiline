# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for layered keys on the NVDA side: which layer is on, and carrying a decision out.

The rules themselves are tested in `test_keyLayers`. What is held to here is what only this side
can get wrong: that a key outside the layer and another display's keys are never touched, that
the decider changes state only for a one shot layer's key and never while something is capturing,
that the lock screen rule `findScript` applies is applied here too, that the stored layers are
read on the main thread and survive a profile switch by id, and that a command is found on the
object NVDA would have run it on.
"""

import sys
import types
import unittest
from dataclasses import replace

from ._stubs import BrailleDisplayGesture, KeyboardInputGesture, installStubs, log, spokenMessages

installStubs()

import api  # noqa: E402
import braille  # noqa: E402
import globalPluginHandler  # noqa: E402
import inputCore  # noqa: E402

from brlMultiline import bmConfig  # noqa: E402
from brlMultiline import keyLayerDispatch as dispatch  # noqa: E402
from brlMultiline.keyLayers import (  # noqa: E402
	DEFAULT_ID,
	KEYBOARD,
	MONARCH,
	ONE_SHOT,
	STAYS_ON,
	LayerSet,
	Target,
	newLayer,
	normalize,
)

MODULE = "_keyLayerTargets"


class Plugin:
	def script_panUp(self, gesture):
		"""Moves the drawing up"""


class Commands:
	def script_sayLine(self, gesture):
		"""Reports the current line"""

	def script_dateTime(self, gesture):
		"""Reports the time"""


class Document:
	passThrough = False
	isReady = True

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


def script(className, scriptName, actsFor=None):
	return Target.script(MODULE, className, scriptName, actsFor)


def monarch(key):
	return normalize(f"br({MONARCH}):{key}")


class BrailleKey(BrailleDisplayGesture):
	def __init__(self, source, key, ordinary=None, cellIndexes=None):
		self.source = source
		self.identifiers = [f"br({source}):{key}"]
		self.script = ordinary
		self.cellIndexes = cellIndexes


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


def toggleScript(gesture):
	"""The layer key."""


toggleScript.__name__ = "script_keyLayerToggle"


def monarchSet():
	default = newLayer(MONARCH, DEFAULT_ID, bindings={monarch("leftDpadDown"): script("Commands", "sayLine")})
	graphics = newLayer(
		MONARCH,
		"graphics",
		"Graphics",
		"graphics",
		bindings={
			monarch("leftDpadUp"): script("Plugin", "panUp"),
			monarch("space+dot1"): script("Document", "nextHeading"),
			monarch("space+dot2"): Target.key("kb:downArrow"),
			monarch("space+dot3"): Target.blocked(),
		},
	)
	reading = newLayer(
		MONARCH,
		"reading",
		"Reading",
		bindings={monarch("leftDpadUp"): script("Commands", "sayLine")},
	)
	return LayerSet(MONARCH, (default, graphics, reading))


def keyboardSet():
	graphics = newLayer(
		KEYBOARD,
		"graphics",
		"Graphics",
		"graphics",
		bindings={normalize("kb:numpad8"): script("Plugin", "panUp", MONARCH)},
	)
	return LayerSet(KEYBOARD, (graphics,))


class DispatchTestCase(unittest.TestCase):
	def setUp(self):
		self.stored = {MONARCH: monarchSet().toText(), KEYBOARD: keyboardSet().toText()}
		self.saved = {
			"keyLayerText": bmConfig.keyLayerText,
			"setKeyLayerText": bmConfig.setKeyLayerText,
			"keyLayerDevices": bmConfig.keyLayerDevices,
			"runningPlugins": globalPluginHandler.__dict__.get("runningPlugins"),
			"getFocusObject": api.__dict__.get("getFocusObject"),
			"getFocusAncestors": api.__dict__.get("getFocusAncestors"),
			"handler": braille.handler,
			"commands": sys.modules["globalCommands"].commands,
		}
		bmConfig.keyLayerText = lambda device: self.stored.get(device, "")
		bmConfig.setKeyLayerText = self.stored.__setitem__
		bmConfig.keyLayerDevices = lambda: sorted(self.stored)
		self.plugin = Plugin()
		self.commands = Commands()
		self.focus = types.SimpleNamespace(appModule=None, treeInterceptor=None)
		self.ancestors = []
		globalPluginHandler.runningPlugins = [self.plugin]
		api.getFocusObject = lambda: self.focus
		api.getFocusAncestors = lambda: self.ancestors
		sys.modules["globalCommands"].commands = self.commands
		braille.handler = None
		inputCore.manager._captureFunc = None
		inputCore.manager.isInputHelpActive = False
		dispatch.remove()
		dispatch._scripts.clear()
		dispatch.install()
		spokenMessages.clear()
		log.messages.clear()

	def tearDown(self):
		dispatch.remove()
		bmConfig.keyLayerText = self.saved["keyLayerText"]
		bmConfig.setKeyLayerText = self.saved["setKeyLayerText"]
		bmConfig.keyLayerDevices = self.saved["keyLayerDevices"]
		globalPluginHandler.runningPlugins = self.saved["runningPlugins"] or []
		if self.saved["getFocusObject"] is not None:
			api.getFocusObject = self.saved["getFocusObject"]
		if self.saved["getFocusAncestors"] is None:
			api.__dict__.pop("getFocusAncestors", None)
		else:
			api.getFocusAncestors = self.saved["getFocusAncestors"]
		braille.handler = self.saved["handler"]
		sys.modules["globalCommands"].commands = self.saved["commands"]
		inputCore.manager._captureFunc = None
		for name in ("winAPI", "winAPI.sessionTracking", "utils", "utils.security"):
			sys.modules.pop(name, None)

	def decide(self, gesture):
		self.assertTrue(dispatch._decide(gesture=gesture), "a layer must never drop a gesture")
		return gesture.script


class TestChoosing(DispatchTestCase):
	def test_nothingIsTouchedWhileNoLayerIsOn(self):
		self.assertIs(ordinaryScript, self.decide(BrailleKey(MONARCH, "leftDpadUp", ordinaryScript)))

	def test_aBoundKeyRunsTheCommandOnTheLiveObject(self):
		dispatch.activate(MONARCH, "graphics")
		found = self.decide(BrailleKey(MONARCH, "leftDpadUp", ordinaryScript))
		self.assertEqual(self.plugin.script_panUp, found)

	def test_theScriptIsTheCommandsOwnBoundMethodSoRepeatsCount(self):
		dispatch.activate(MONARCH, "graphics")
		found = self.decide(BrailleKey(MONARCH, "leftDpadDown"))
		self.assertIs(Commands.script_sayLine, found.__func__)
		self.assertIs(self.commands, found.__self__)

	def test_anUnboundKeyIsLeftToNvdaAndTheLayerStaysOn(self):
		dispatch.activate(MONARCH, "graphics")
		self.assertIs(ordinaryScript, self.decide(BrailleKey(MONARCH, "rightDpadUp", ordinaryScript)))
		self.assertIsNotNone(dispatch.activeLayer(MONARCH))

	def test_anotherDisplaysKeysAreNeverTouched(self):
		dispatch.activate(MONARCH, "graphics")
		gesture = BrailleKey("freedomScientific", "leftDpadUp", ordinaryScript)
		self.assertIs(ordinaryScript, self.decide(gesture))

	def test_theKeyboardIsItsOwnDevice(self):
		dispatch.activate(MONARCH, "graphics")
		self.assertIs(ordinaryScript, self.decide(Key("numpad8", ordinaryScript)))
		dispatch.activate(KEYBOARD, "graphics")
		self.assertEqual(self.plugin.script_panUp, self.decide(Key("numpad8", ordinaryScript)))

	def test_aKeyboardBindingActsForItsDisplay(self):
		dispatch.activate(KEYBOARD, "graphics")
		gesture = Key("numpad8")
		self.decide(gesture)
		self.assertEqual(MONARCH, dispatch.deviceFor(gesture))
		self.assertEqual(MONARCH, dispatch.displayFor(gesture))
		self.assertEqual(KEYBOARD, dispatch.deviceOf(gesture))

	def test_displayForIsNoneForAPlainKeyboardKey(self):
		"""What panning used to get from a keyboard gesture's missing source."""
		self.assertIsNone(dispatch.displayFor(Key("a")))
		self.assertEqual(MONARCH, dispatch.displayFor(BrailleKey(MONARCH, "a")))
		self.assertIsNone(dispatch.displayFor(None))

	def test_anEmulatedKeyIsTheSameScriptEachTime(self):
		"""So a repeated press counts as a repeat, as NVDA's own emulated keys do."""
		dispatch.activate(MONARCH, "graphics")
		first = self.decide(BrailleKey(MONARCH, "space+dot2"))
		second = self.decide(BrailleKey(MONARCH, "space+dot2"))
		self.assertIs(first, second)
		self.assertEqual("script_kb:downArrow", first.__name__)

	def test_aBlockedKeyDoesNothing(self):
		dispatch.activate(MONARCH, "graphics")
		blocked = self.decide(BrailleKey(MONARCH, "space+dot3", ordinaryScript))
		self.assertIsNot(ordinaryScript, blocked)
		blocked(None)
		self.assertEqual([], spokenMessages)


class TestOneShot(DispatchTestCase):
	def setUp(self):
		super().setUp()
		dispatch.activate(MONARCH, "reading")

	def test_itIsOneShot(self):
		self.assertEqual(ONE_SHOT, dispatch.activeLayer(MONARCH).style)

	def test_theNextKeyRunsAndEndsTheLayer(self):
		self.assertEqual(self.commands.script_sayLine, self.decide(BrailleKey(MONARCH, "leftDpadUp")))
		self.assertIsNone(dispatch.activeLayer(MONARCH))
		self.assertIs(ordinaryScript, self.decide(BrailleKey(MONARCH, "leftDpadUp", ordinaryScript)))

	def test_anUnboundKeyPassesAndEndsTheLayer(self):
		self.assertIs(ordinaryScript, self.decide(BrailleKey(MONARCH, "dot1", ordinaryScript)))
		self.assertIsNone(dispatch.activeLayer(MONARCH))

	def test_routingDoesNotSpendTheShot(self):
		self.decide(BrailleKey(MONARCH, "routerSet1_routerKey", ordinaryScript, cellIndexes=[4]))
		self.assertIsNotNone(dispatch.activeLayer(MONARCH))

	def test_inputHelpDoesNotSpendTheShot(self):
		inputCore.manager._captureFunc = lambda gesture: False
		inputCore.manager.isInputHelpActive = True
		self.assertEqual(self.commands.script_sayLine, self.decide(BrailleKey(MONARCH, "leftDpadUp")))
		self.assertIsNotNone(dispatch.activeLayer(MONARCH))

	def test_theLayerKeyIsLeftToTurnItOff(self):
		gesture = BrailleKey(MONARCH, "space+dot1+dot2+dot3+dot7", toggleScript)
		self.assertIs(toggleScript, self.decide(gesture))
		self.assertIsNotNone(dispatch.activeLayer(MONARCH))


class TestStandingAside(DispatchTestCase):
	def setUp(self):
		super().setUp()
		dispatch.activate(MONARCH, "graphics")

	def test_aModifierIsLeftAlone(self):
		dispatch.activate(KEYBOARD, "graphics")
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


class TestLeaving(DispatchTestCase):
	def setUp(self):
		super().setUp()
		dispatch.activate(MONARCH, "graphics")
		dispatch.activate(KEYBOARD, "graphics")

	def test_spaceWithZLeavesInTheScriptNotTheDecider(self):
		gesture = BrailleKey(MONARCH, "dot6+dot5+space+dot3+dot1", ordinaryScript)
		leave = self.decide(gesture)
		self.assertIsNotNone(dispatch.activeLayer(MONARCH))
		leave(gesture)
		self.assertIsNone(dispatch.activeLayer(MONARCH))
		self.assertIsNotNone(dispatch.activeLayer(KEYBOARD))
		self.assertIn("Graphics layer off", spokenMessages)

	def test_theKeyboardsEscapeLeavesOnlyTheKeyboardsLayer(self):
		gesture = Key("escape")
		leave = self.decide(gesture)
		self.assertIsNotNone(leave, "escape has no command of its own, so the layer has to give it one")
		leave(gesture)
		self.assertIsNone(dispatch.activeLayer(KEYBOARD))
		self.assertIsNotNone(dispatch.activeLayer(MONARCH))
		self.assertIn("Graphics layer off", spokenMessages)

	def test_aKeyWhoseOrdinaryCommandIsEscapeLeaves(self):
		gesture = BrailleKey(MONARCH, "space+dot1+dot5", escapeScript)
		leave = self.decide(gesture)
		self.assertIsNot(escapeScript, leave)
		leave(gesture)
		self.assertIsNone(dispatch.activeLayer(MONARCH))


class TestFinding(DispatchTestCase):
	def setUp(self):
		super().setUp()
		dispatch.activate(MONARCH, "graphics")

	def test_aCommandWithNoObjectHereSaysSo(self):
		gesture = BrailleKey(MONARCH, "space+dot1", ordinaryScript)
		unavailable = self.decide(gesture)
		self.assertIsNot(ordinaryScript, unavailable)
		unavailable(gesture)
		self.assertIn("Moves to the next heading is not available here", spokenMessages)

	def test_aTreeInterceptorCommandIsFound(self):
		self.focus.treeInterceptor = Document()
		found = self.decide(BrailleKey(MONARCH, "space+dot1"))
		self.assertEqual(self.focus.treeInterceptor.script_nextHeading, found)

	def test_notInFocusMode(self):
		document = Document()
		document.passThrough = True
		self.focus.treeInterceptor = document
		self.assertIsNone(dispatch.resolve(script("Document", "nextHeading"), BrailleKey(MONARCH, "x"))[0])

	def test_anAncestorOnlyOffersCommandsThatPropagate(self):
		self.ancestors = [Ancestor()]
		gesture = BrailleKey(MONARCH, "x")
		self.assertIsNotNone(dispatch.resolve(script("Ancestor", "propagates"), gesture)[0])
		self.assertIsNone(dispatch.resolve(script("Ancestor", "staysPut"), gesture)[0])

	def test_aMembersOwnCommandIsFoundOnTheMember(self):
		member = Member()
		slots = {"freedomScientific": types.SimpleNamespace(driver=member)}
		braille.handler = types.SimpleNamespace(display=types.SimpleNamespace(slotForDriverName=slots.get))
		found = dispatch.resolve(script("Member", "wizWheel", "freedomScientific"), Key("numpad8"))[1]
		self.assertIs(member, found)


class TestLockScreen(DispatchTestCase):
	def setUp(self):
		super().setUp()
		dispatch.activate(MONARCH, "graphics")
		self.locked = True
		sys.modules["winAPI"] = types.ModuleType("winAPI")
		sys.modules["winAPI.sessionTracking"] = types.SimpleNamespace(
			isLockScreenModeActive=lambda: self.locked,
		)
		sys.modules["utils"] = types.ModuleType("utils")
		sys.modules["utils.security"] = types.SimpleNamespace(
			getSafeScripts=lambda: {self.commands.script_dateTime},
		)

	def test_anUnsafeCommandIsRefusedAndTheKeyLeftToNvda(self):
		self.assertIs(ordinaryScript, self.decide(BrailleKey(MONARCH, "leftDpadUp", ordinaryScript)))

	def test_anEmulatedKeyIsRefusedToo(self):
		self.assertIs(ordinaryScript, self.decide(BrailleKey(MONARCH, "space+dot2", ordinaryScript)))

	def test_aSafeCommandRuns(self):
		layers = dispatch.layerSet(MONARCH)
		graphics = layers.get("graphics")
		bindings = dict(graphics.bindings)
		bindings[monarch("leftDpadUp")] = script("Commands", "dateTime")
		dispatch.save(layers.withLayer(replace(graphics, bindings=bindings)))
		self.assertEqual(self.commands.script_dateTime, self.decide(BrailleKey(MONARCH, "leftDpadUp")))

	def test_aKeyThatDoesNothingStillDoesNothing(self):
		"""Refused, it would be left to NVDA, which could run its ordinary command if that were safe."""
		self.assertIsNot(ordinaryScript, self.decide(BrailleKey(MONARCH, "space+dot3", ordinaryScript)))

	def test_unlockedEverythingRuns(self):
		self.locked = False
		self.assertEqual(self.plugin.script_panUp, self.decide(BrailleKey(MONARCH, "leftDpadUp")))


class TestLayersOnAndOff(DispatchTestCase):
	def test_theLayerKeyTakesTheLayerForWhatIsOnTheDisplay(self):
		self.assertEqual((True, "graphics"), self.toggled(MONARCH, ["chart", "graphics"]))
		self.assertEqual((False, "graphics"), self.toggled(MONARCH, ["chart", "graphics"]))

	def toggled(self, device, contexts):
		isOn, layer = dispatch.toggle(device, contexts)
		return isOn, layer.id

	def test_withNothingOnTheDisplayItTakesTheDefaultLayer(self):
		self.assertEqual((True, DEFAULT_ID), self.toggled(MONARCH, []))
		self.assertEqual("Default", dispatch.layerName(dispatch.activeLayer(MONARCH)))

	def test_aDeviceWithNothingStoredStillHasADefaultLayer(self):
		self.assertEqual((True, DEFAULT_ID), self.toggled("freedomScientific", []))

	def test_allOffSaysWhatWasOn(self):
		dispatch.activate(MONARCH, "graphics")
		dispatch.activate(KEYBOARD, "graphics")
		self.assertEqual({MONARCH, KEYBOARD}, {device for device, _layer in dispatch.allOff()})
		self.assertEqual([], dispatch.activeLayers())

	def test_anUnknownLayerIsNotTurnedOn(self):
		self.assertIsNone(dispatch.activate(MONARCH, "nope"))

	def test_aStaysOnLayerStaysOn(self):
		self.assertEqual(STAYS_ON, dispatch.activate(MONARCH, "graphics").style)


class TestProfiles(DispatchTestCase):
	def test_aLayerStillThereByIdStaysOn(self):
		dispatch.activate(MONARCH, "graphics")
		pictures = newLayer(MONARCH, "graphics", "Pictures", "graphics")
		self.stored[MONARCH] = LayerSet(MONARCH, (pictures,)).toText()
		self.assertEqual([], dispatch.reload())
		self.assertEqual("Pictures", dispatch.activeLayer(MONARCH).name)

	def test_aLayerThatIsGoneIsTurnedOffAndNamed(self):
		dispatch.activate(MONARCH, "graphics")
		self.stored[MONARCH] = LayerSet(MONARCH).toText()
		gone = dispatch.reload()
		self.assertEqual([(MONARCH, "Graphics")], [(device, layer.name) for device, layer in gone])
		self.assertIsNone(dispatch.activeLayer(MONARCH))

	def test_theDeciderReadsWhatWasLoadedNotTheConfiguration(self):
		"""It runs on a driver's thread, where `config.conf` is not to be touched."""
		dispatch.activate(MONARCH, "graphics")

		def mustNotRead(device):
			raise AssertionError("the decider read the configuration")

		bmConfig.keyLayerText = mustNotRead
		self.assertEqual(self.plugin.script_panUp, self.decide(BrailleKey(MONARCH, "leftDpadUp")))

	def test_aDeviceWithNothingStoredHasItsDefaultLayers(self):
		self.stored.clear()
		dispatch.reload()
		self.assertEqual(
			"reportCurrentLine", dispatch.layerSet(MONARCH).default.bindings[monarch("leftDpadUp")].scriptName
		)
		self.assertIsNotNone(dispatch.layerSet(KEYBOARD).get("graphics"))
		self.assertEqual(
			dispatch.PLUGIN_MODULE,
			dispatch.layerSet(MONARCH).get("graphics").bindings[monarch("zoomIn")].moduleName,
		)

	def test_savingASetWithoutTheLayerThatIsOnTurnsItOff(self):
		dispatch.activate(MONARCH, "reading")
		dispatch.save(LayerSet(MONARCH))
		self.assertIsNone(dispatch.activeLayer(MONARCH))


class TestContextGoing(DispatchTestCase):
	def test_aLayerWhoseDrawingHasGoneIsTurnedOff(self):
		dispatch.activate(MONARCH, "graphics")
		dispatch.activate(KEYBOARD, "graphics")
		ended = dispatch.endLayersOutOfContext([], ("chart", "picture", "graphics"))
		self.assertEqual({MONARCH, KEYBOARD}, {device for device, _layer in ended})
		self.assertEqual([], dispatch.activeLayers())

	def test_aLayerWhoseContextIsStillHereStaysOn(self):
		dispatch.activate(MONARCH, "graphics")
		self.assertEqual([], dispatch.endLayersOutOfContext(["chart", "graphics"], ("chart", "graphics")))
		self.assertIsNotNone(dispatch.activeLayer(MONARCH))

	def test_aLayerWithNoContextIsNeverTurnedOffForOne(self):
		dispatch.activate(MONARCH, "reading")
		self.assertEqual([], dispatch.endLayersOutOfContext([], ("graphics",)))
		self.assertIsNotNone(dispatch.activeLayer(MONARCH))

	def test_aContextThatCannotBeSeenIsNeverSeenToGo(self):
		"""Tables are not detected until phase 3, so a table layer must not end on every drawing."""
		tables = newLayer(MONARCH, "tables", "Tables", "table")
		dispatch.save(dispatch.layerSet(MONARCH).withLayer(tables))
		dispatch.activate(MONARCH, "tables")
		self.assertEqual([], dispatch.endLayersOutOfContext([], ("chart", "picture", "graphics")))


class TestComingOnByItself(DispatchTestCase):
	DETECTED = ("chart", "picture", "graphics")

	def setUp(self):
		super().setUp()
		layers = dispatch.layerSet(MONARCH)
		dispatch.save(layers.withLayer(replace(layers.get("graphics"), autoEnable=True, style=ONE_SHOT)))
		keyboard = dispatch.layerSet(KEYBOARD)
		dispatch.save(keyboard.withLayer(replace(keyboard.get("graphics"), autoEnable=True)))

	def auto(self, present, devices=(MONARCH, KEYBOARD, "freedomScientific")):
		return [(device, layer.id) for device, layer in dispatch.autoEnable(present, devices, self.DETECTED)]

	def test_aDrawingBringsEachDevicesLayerOn(self):
		self.assertEqual([(MONARCH, "graphics"), (KEYBOARD, "graphics")], self.auto(["graphics"]))
		self.assertTrue(dispatch.isAutomatic(MONARCH))

	def test_aDeviceWithALayerAlreadyOnKeepsIt(self):
		dispatch.activate(MONARCH, "reading")
		self.assertEqual([(KEYBOARD, "graphics")], self.auto(["graphics"]))
		self.assertEqual("reading", dispatch.activeLayer(MONARCH).id)

	def test_anAutomaticLayerStaysOnWhateverItsStyle(self):
		"""Its context ends it. The Monarch's is set one shot above, and a key must not end it."""
		self.auto(["graphics"])
		self.decide(BrailleKey(MONARCH, "leftDpadUp"))
		self.decide(BrailleKey(MONARCH, "dot1", ordinaryScript))
		self.assertIsNotNone(dispatch.activeLayer(MONARCH))

	def test_turnedOffByTheReaderItStaysOffUntilTheDrawingGoesAndComesBack(self):
		self.auto(["graphics"])
		dispatch.toggle(MONARCH)
		self.assertEqual([], [pair for pair in self.auto(["graphics"]) if pair[0] == MONARCH])
		dispatch.endLayersOutOfContext([], self.DETECTED)
		self.auto([])
		self.assertIn((MONARCH, "graphics"), self.auto(["graphics"]))

	def test_aLayerThatGoesWithItsDrawingIsNotDeclined(self):
		self.auto(["graphics"])
		dispatch.endLayersOutOfContext([], self.DETECTED)
		self.assertIn((MONARCH, "graphics"), self.auto(["graphics"]))

	def test_allOffDeclinesToo(self):
		self.auto(["graphics"])
		dispatch.allOff()
		self.assertEqual([], self.auto(["graphics"]))

	def test_leavingWithAnExitKeyDeclines(self):
		self.auto(["graphics"])
		gesture = BrailleKey(MONARCH, "space+dot1+dot3+dot5+dot6")
		self.decide(gesture)(gesture)
		self.assertNotIn(MONARCH, [device for device, _layer in self.auto(["graphics"])])

	def test_choosingALayerIsNoLongerAutomatic(self):
		self.auto(["graphics"])
		dispatch.activate(MONARCH, "reading")
		self.assertFalse(dispatch.isAutomatic(MONARCH))

	def test_choosingAnotherLayerDeclinesTheOneThatCameOnByItself(self):
		"""The whole sequence: chosen away, then turned off, with the drawing still up."""
		self.auto(["graphics"])
		dispatch.activate(MONARCH, "reading")
		dispatch.toggle(MONARCH)
		self.assertNotIn(MONARCH, [device for device, _layer in self.auto(["graphics"])])
		dispatch.endLayersOutOfContext([], self.DETECTED)
		self.auto([])
		self.assertIn((MONARCH, "graphics"), self.auto(["graphics"]))

	def test_choosingTheSameLayerOnlyMakesItTheReaders(self):
		self.auto(["graphics"])
		dispatch.activate(MONARCH, "graphics")
		self.assertFalse(dispatch.isAutomatic(MONARCH))
		dispatch.toggle(MONARCH)
		self.assertIn((MONARCH, "graphics"), self.auto(["graphics"]), "never declined, so it comes back")


class TestReconciling(TestComingOnByItself):
	def reconcile(self, present):
		ended, started = dispatch.reconcile(present, (MONARCH, KEYBOARD), self.DETECTED)
		return [(device, layer.id) for device, layer in ended], [(device, layer.id) for device, layer in started]

	def test_aProfileWhoseLayerComesOnByItselfBringsItOnWithTheDrawingAlreadyUp(self):
		self.stored[MONARCH] = monarchSet().toText()
		dispatch.reload()
		self.reconcile(["graphics"])
		self.assertIsNone(dispatch.activeLayer(MONARCH))
		self.stored[MONARCH] = monarchSet().withLayer(replace(monarchSet().get("graphics"), autoEnable=True)).toText()
		dispatch.reload()
		self.assertEqual(([], [(MONARCH, "graphics")]), self.reconcile(["graphics"]))

	def test_aLayerThatNoLongerComesOnByItselfGoes(self):
		self.auto(["graphics"])
		layers = dispatch.layerSet(MONARCH)
		dispatch.save(layers.withLayer(replace(layers.get("graphics"), autoEnable=False)))
		ended, started = self.reconcile(["graphics"])
		self.assertIn((MONARCH, "graphics"), ended)
		self.assertIsNone(dispatch.activeLayer(MONARCH))
		self.assertNotIn(MONARCH, [device for device, _layer in started])

	def test_aLayerTheReaderChoseIsLeftAlone(self):
		dispatch.activate(KEYBOARD, "graphics")
		layers = dispatch.layerSet(MONARCH)
		dispatch.save(layers.withLayer(replace(layers.get("graphics"), autoEnable=False)))
		self.reconcile(["graphics"])
		self.assertEqual("graphics", dispatch.activeLayer(KEYBOARD).id)

	def test_nothingChangedChangesNothing(self):
		self.reconcile(["graphics"])
		self.assertEqual(([], []), self.reconcile(["graphics"]))


class TestInstall(unittest.TestCase):
	def test_installAndRemove(self):
		dispatch.install()
		try:
			self.assertIn(dispatch._decide, inputCore.decide_executeGesture.handlers)
		finally:
			dispatch.remove()
		self.assertNotIn(dispatch._decide, inputCore.decide_executeGesture.handlers)
		self.assertEqual({}, dispatch._active)


if __name__ == "__main__":
	unittest.main()
