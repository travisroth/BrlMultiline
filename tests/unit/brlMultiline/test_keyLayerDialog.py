# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the layered keys dialog's editor, and for gathering what the dialog offers.

The dialog is a shell; everything that decides anything is `LayerEditor`, which knows nothing about
wx. The widgets themselves are checked on hardware, with NVDA reading them.
"""

import types
import unittest

from ._stubs import installStubs

installStubs()

from brlMultiline import keyLayerDialog as dialog  # noqa: E402
from brlMultiline.keyLayerDialog import Command, Device, LayerEditor, identity  # noqa: E402
from brlMultiline.keyLayers import (  # noqa: E402
	DEFAULT_ID,
	KEYBOARD,
	MONARCH,
	ONE_SHOT,
	STAYS_ON,
	LayerSet,
	LayerSetError,
	Target,
	newLayer,
	normalize,
)

PLUGIN = "globalPlugins.brlMultiline"
PAN_UP = Command(
	"BrlMultiline",
	"Graphics: Move the drawing view up",
	Target.script(PLUGIN, "GlobalPlugin", "graphicsPanUp"),
)
SAY_LINE = Command(
	"System caret",
	"Reports the current line",
	Target.script("globalCommands", "GlobalCommands", "reportCurrentLine"),
)
SCROLL = Command(
	"Braille",
	"Scrolls the braille display forward",
	Target.script("globalCommands", "GlobalCommands", "braille_scrollForward"),
)
COMMANDS = [PAN_UP, SAY_LINE, SCROLL]


def monarch(key):
	return normalize(f"br({MONARCH}):{key}")


def saved():
	graphics = newLayer(
		MONARCH,
		"graphics",
		"Graphics",
		"graphics",
		bindings={monarch("leftDpadUp"): PAN_UP.target},
	)
	return {
		MONARCH: LayerSet(
			MONARCH,
			(newLayer(MONARCH, DEFAULT_ID, bindings={monarch("leftDpadDown"): SAY_LINE.target}), graphics),
		),
		KEYBOARD: LayerSet(KEYBOARD),
	}


DEVICES = [
	Device(MONARCH, "Monarch", True),
	Device("freedomScientific", "Focus 80", True),
	Device(KEYBOARD, "Keyboard", True),
	Device("handyTech", "Handy Tech (not connected)", False),
]


class EditorTestCase(unittest.TestCase):
	def setUp(self):
		self.saved = saved()
		self.reads = []

		def read(device):
			self.reads.append(device)
			return self.saved.get(device, LayerSet(device))

		self.editor = LayerEditor(
			DEVICES,
			read,
			lambda device: LayerSet(
				device, (newLayer(device, DEFAULT_ID, bindings={monarch("zoomIn"): SAY_LINE.target}),)
			),
			COMMANDS,
			describeKey=lambda identifier: f"<{identifier}>",
			reservedKeys=[f"br({MONARCH}):space+dot1+dot2+dot3+dot7"],
		)

	def node(self, commandName):
		for category in self.editor.categories():
			for node in category.commands:
				if node.command.name == commandName:
					return node
		return None


class TestSelecting(EditorTestCase):
	def test_itStartsOnTheFirstDevicesDefaultLayer(self):
		self.assertEqual((MONARCH, DEFAULT_ID), (self.editor.device, self.editor.layer.id))

	def test_layersAreReadOnlyWhenADeviceIsLookedAt(self):
		self.assertEqual([], self.reads)
		self.editor.layers
		self.editor.layers
		self.assertEqual([MONARCH], self.reads)

	def test_editsToOneDeviceSurviveLookingAtAnother(self):
		self.editor.add(SAY_LINE, monarch("rightDpadUp"))
		self.editor.selectDevice(KEYBOARD)
		self.editor.selectDevice(MONARCH)
		self.assertIn(monarch("rightDpadUp"), self.editor.layer.bindings)

	def test_anUnknownDeviceOrLayerIsRefused(self):
		with self.assertRaises(LookupError):
			self.editor.selectDevice("nope")
		with self.assertRaises(LookupError):
			self.editor.selectLayer("nope")


class TestTree(EditorTestCase):
	def test_eachCommandListsItsKeysInThisLayer(self):
		self.assertEqual(
			[f"<{monarch('leftDpadDown')}>"], [key.name for key in self.node(SAY_LINE.name).keys]
		)
		self.assertEqual([], self.node(PAN_UP.name).keys)
		self.editor.selectLayer("graphics")
		self.assertEqual([monarch("leftDpadUp")], [key.identifier for key in self.node(PAN_UP.name).keys])

	def test_categoriesAndCommandsAreSorted(self):
		names = [category.name for category in self.editor.categories()]
		self.assertEqual(sorted(names, key=str.casefold), names)

	def test_theCommandThatDoesNothingIsOffered(self):
		self.assertIsNotNone(self.node("Does nothing in this layer"))

	def test_aKeyToACommandNotAvailableHereIsStillShown(self):
		"""So it can be removed, rather than hiding in the layer."""
		gone = Target.script("browseMode", "BrowseModeTreeInterceptor", "nextHeading")
		self.editor.add(Command("x", "x", gone), monarch("rightDpadDown"))
		unavailable = [
			category for category in self.editor.categories() if category.name == dialog.UNAVAILABLE
		]
		self.assertEqual(1, len(unavailable))
		self.assertEqual("nextHeading (BrowseModeTreeInterceptor)", unavailable[0].commands[0].command.name)

	def test_theFilterMatchesEveryWordInAnyOrder(self):
		self.editor.filterText = "view drawing"
		commands = [node.command.name for category in self.editor.categories() for node in category.commands]
		self.assertEqual([PAN_UP.name], commands)

	def test_onlyCommandsWithKeysInThisLayer(self):
		self.editor.onlyBound = True
		commands = [node.command.name for category in self.editor.categories() for node in category.commands]
		self.assertEqual([SAY_LINE.name], commands)

	def test_thePromptShowsUnderTheCommandWaitingForAKey(self):
		self.editor.pending = identity(PAN_UP.target)
		self.assertEqual([None], [key.identifier for key in self.node(PAN_UP.name).keys])
		self.assertEqual(dialog.PROMPT, self.node(PAN_UP.name).keys[0].name)

	def test_aKeyActingForADisplaySaysWhich(self):
		self.editor.selectDevice(KEYBOARD)
		self.editor.add(PAN_UP, "kb:numpad8", actsFor=MONARCH)
		self.assertEqual("<kb:numpad8>, acting for Monarch", self.node(PAN_UP.name).keys[0].name)


class TestKeys(EditorTestCase):
	def test_aKeyIsBoundNormalizedAndTheDeviceMarkedChanged(self):
		self.editor.add(PAN_UP, f"br({MONARCH}):Space+DOT1")
		self.assertEqual(PAN_UP.target, self.editor.layer.bindings[monarch("dot1+space")])
		self.assertTrue(self.editor.isDirty)

	def test_theLayerKeyIsRefused(self):
		check = self.editor.check(PAN_UP, monarch("dot7+dot3+dot2+dot1+space"))
		self.assertIn("turns layers on and off", check.refusal)
		with self.assertRaises(ValueError):
			self.editor.add(PAN_UP, monarch("space+dot1+dot2+dot3+dot7"))

	def test_anExitKeyIsRefused(self):
		check = self.editor.check(PAN_UP, monarch("space+dot1+dot3+dot5+dot6"))
		self.assertIn("leaves this layer", check.refusal)

	def test_aKeyAlreadyRunningSomethingElseAsksFirst(self):
		check = self.editor.check(PAN_UP, monarch("leftDpadDown"))
		self.assertEqual(dialog.Check(conflict=SAY_LINE.name), check)
		self.editor.add(PAN_UP, monarch("leftDpadDown"))
		self.assertEqual(PAN_UP.target, self.editor.layer.bindings[monarch("leftDpadDown")])

	def test_theSameCommandAgainIsNoConflict(self):
		self.assertEqual(dialog.Check(), self.editor.check(SAY_LINE, monarch("leftDpadDown")))

	def test_changingAKeyRemovesTheOldOneInTheSameEdit(self):
		self.editor.add(SAY_LINE, monarch("rightDpadDown"), replacing=monarch("leftDpadDown"))
		self.assertEqual({monarch("rightDpadDown"): SAY_LINE.target}, self.editor.layer.bindings)

	def test_aChangeToTheSameKeyKeepsIt(self):
		self.editor.add(SAY_LINE, monarch("leftDpadDown"), replacing=monarch("leftDpadDown"))
		self.assertEqual({monarch("leftDpadDown"): SAY_LINE.target}, self.editor.layer.bindings)

	def test_aRefusedChangeLeavesTheOldKey(self):
		with self.assertRaises(ValueError):
			self.editor.add(
				SAY_LINE, monarch("space+dot1+dot2+dot3+dot7"), replacing=monarch("leftDpadDown")
			)
		self.assertIn(monarch("leftDpadDown"), self.editor.layer.bindings)

	def test_removingAKey(self):
		self.editor.remove(monarch("leftDpadDown"))
		self.assertEqual({}, self.editor.layer.bindings)

	def test_anEmulatedKeyIsListedUntilAKeyIsBoundToIt(self):
		command = self.editor.addEmulatedKey("kb:downArrow")
		self.assertEqual(Target.key("kb:downarrow"), command.target)
		self.assertIsNotNone(self.node(command.name))
		self.editor.add(command, monarch("rightDpadLeft"))
		self.assertEqual(command.target, self.editor.layer.bindings[monarch("rightDpadLeft")])
		self.assertEqual(1, len(self.node(command.name).keys))

	def test_theSameEmulatedKeyTwiceIsOneCommand(self):
		self.assertIs(self.editor.addEmulatedKey("kb:tab"), self.editor.addEmulatedKey("kb:tab"))

	def test_aKeyboardKeyForADisplayCommandAsksWhichDisplay(self):
		self.editor.selectDevice(KEYBOARD)
		self.assertTrue(self.editor.needsDisplay(PAN_UP))
		self.assertTrue(self.editor.needsDisplay(SCROLL))
		self.assertFalse(self.editor.needsDisplay(SAY_LINE))
		self.assertEqual([MONARCH, "freedomScientific"], [device.name for device in self.editor.displays()])

	def test_aDisplaysOwnKeyNeverAsks(self):
		self.assertFalse(self.editor.needsDisplay(PAN_UP))

	def test_noDisplayToNameNeverAsks(self):
		editor = LayerEditor([Device(KEYBOARD, "Keyboard", True)], LayerSet, LayerSet, COMMANDS)
		self.assertFalse(editor.needsDisplay(PAN_UP))


class TestKeysFromBelow(EditorTestCase):
	"""A layer answers to keys further down its chain, and the dialog has to show them."""

	def setUp(self):
		super().setUp()
		self.editor.nameLayer = lambda layer: "Default" if layer.isDefault else layer.name
		self.editor.newLayer("Chart")
		self.editor.setProperties("Chart", "chart", False, STAYS_ON, "graphics", ())
		self.editor.add(SAY_LINE, monarch("zoomIn"))

	def test_keysFromBelowAreListedWithWhereTheyComeFrom(self):
		pan = self.node(PAN_UP.name).keys
		self.assertEqual(
			[(monarch("leftDpadUp"), "Graphics")], [(key.identifier, key.fromLayer) for key in pan]
		)
		self.assertEqual(f"<{monarch('leftDpadUp')}> (from Graphics layer)", pan[0].name)
		say = {key.identifier: key.fromLayer for key in self.node(SAY_LINE.name).keys}
		self.assertEqual({monarch("zoomIn"): None, monarch("leftDpadDown"): "Default"}, say)

	def test_theDefaultLayerHasNothingFromBelow(self):
		self.editor.selectLayer(DEFAULT_ID)
		self.assertFalse(
			any(
				key.fromLayer
				for category in self.editor.categories()
				for node in category.commands
				for key in node.keys
			)
		)

	def test_aKeyFromBelowTakenForAnotherCommandSaysWhereItCameFrom(self):
		check = self.editor.check(SAY_LINE, monarch("leftDpadUp"))
		self.assertEqual(dialog.Check(conflict=PAN_UP.name, conflictLayer="Graphics"), check)

	def test_givingItAnotherCommandOverridesItHereOnly(self):
		self.editor.add(SAY_LINE, monarch("leftDpadUp"))
		self.assertEqual(SAY_LINE.target, self.editor.layer.bindings[monarch("leftDpadUp")])
		self.assertEqual(PAN_UP.target, self.editor.layers.get("graphics").bindings[monarch("leftDpadUp")])
		self.assertEqual([], self.node(PAN_UP.name).keys)

	def test_blockingAKeyFromBelowLeavesTheOtherLayerAlone(self):
		self.editor.block(monarch("leftDpadUp"))
		self.assertEqual(Target.blocked(), self.editor.layer.bindings[monarch("leftDpadUp")])
		self.assertIn(monarch("leftDpadUp"), self.editor.layers.get("graphics").bindings)
		self.assertEqual([None], [key.fromLayer for key in self.node("Does nothing in this layer").keys])


class TestLayers(EditorTestCase):
	def test_aNewLayerIsOneShotLeavesOnSpaceWithZAndIsSelected(self):
		layer = self.editor.newLayer("Reading keys")
		self.assertEqual(("readingkeys", "Reading keys", ONE_SHOT), (layer.id, layer.name, layer.style))
		self.assertIn(monarch("space+dot1+dot3+dot5+dot6"), layer.exitKeys)
		self.assertEqual("readingkeys", self.editor.layer.id)

	def test_newLayerIdsAreUnique(self):
		self.assertEqual("graphics2", self.editor.newLayer("Graphics").id)
		self.assertEqual("default2", self.editor.newLayer("Default").id)
		self.assertEqual("layer", self.editor.newLayer("!!!").id)

	def test_theDefaultLayerCannotBeRenamedOrDeleted(self):
		with self.assertRaises(ValueError):
			self.editor.renameLayer("x")
		with self.assertRaises(ValueError):
			self.editor.deleteLayer()

	def test_renaming(self):
		self.editor.selectLayer("graphics")
		self.editor.renameLayer("  Pictures ")
		self.assertEqual("Pictures", self.editor.layer.name)

	def test_deletingALayerClearsWhatFellThroughToIt(self):
		chart = self.editor.newLayer("Chart")
		self.editor.setProperties("Chart", "chart", False, STAYS_ON, "graphics", ())
		self.editor.selectLayer("graphics")
		self.editor.deleteLayer()
		self.assertEqual(DEFAULT_ID, self.editor.layer.id)
		self.assertIsNone(self.editor.layers.get(chart.id).fallsThrough)
		self.assertIsNone(self.editor.layers.get("graphics"))

	def test_propertiesAreKept(self):
		self.editor.selectLayer("graphics")
		problem = self.editor.setProperties("Pictures", "picture", True, ONE_SHOT, None, ["kb:escape"])
		self.assertIsNone(problem)
		layer = self.editor.layer
		self.assertEqual(
			("Pictures", "picture", True, ONE_SHOT, frozenset({"kb:escape"})),
			(layer.name, layer.context, layer.autoEnable, layer.style, layer.exitKeys),
		)

	def test_comingOnByItselfNeedsSomethingToComeOnFor(self):
		self.editor.selectLayer("graphics")
		self.editor.setProperties("Graphics", None, True, STAYS_ON, None, ())
		self.assertFalse(self.editor.layer.autoEnable)

	def test_aCircleIsRefusedAndNothingKept(self):
		self.editor.newLayer("Chart")
		self.editor.setProperties("Chart", "chart", False, STAYS_ON, "graphics", ())
		self.editor.selectLayer("graphics")
		problem = self.editor.setProperties("Graphics", "graphics", False, STAYS_ON, "chart", ())
		self.assertIn("circle", problem)
		self.assertIsNone(self.editor.layer.fallsThrough)

	def test_theDefaultLayerKeepsNoNameAndNoContext(self):
		self.editor.setProperties("Named", "graphics", True, STAYS_ON, "graphics", ())
		layer = self.editor.layer
		self.assertEqual(
			("", None, None, STAYS_ON), (layer.name, layer.context, layer.fallsThrough, layer.style)
		)

	def test_aLayerCannotFallThroughToItselfOrTheDefault(self):
		self.editor.newLayer("Chart")
		self.assertEqual(["graphics"], [layer.id for layer in self.editor.fallThroughChoices()])

	def test_clearingALayer(self):
		self.editor.selectLayer("graphics")
		self.editor.clearLayer()
		self.assertEqual({}, self.editor.layer.bindings)
		self.assertIn(monarch("leftDpadDown"), self.editor.layers.default.bindings)

	def test_resetPutsTheShippedLayersBackInPlaceOfEveryLayer(self):
		self.editor.selectLayer("graphics")
		self.editor.resetToFactoryDefaults()
		self.assertEqual([DEFAULT_ID], [layer.id for layer in self.editor.layers.layers])
		self.assertEqual(DEFAULT_ID, self.editor.layer.id)
		self.assertIn(monarch("zoomIn"), self.editor.layer.bindings)


class TestCommitting(EditorTestCase):
	def test_onlyChangedDevicesAreSaved(self):
		saved = []
		self.editor.add(SAY_LINE, monarch("rightDpadUp"))
		self.editor.selectDevice(KEYBOARD)
		self.assertEqual([], self.editor.commit(saved.append))
		self.assertEqual([MONARCH], [layers.device for layers in saved])
		self.assertFalse(self.editor.isDirty)

	def test_aDeviceThatCannotBeSavedIsReportedAndStaysChanged(self):
		def refuse(layers):
			raise LayerSetError("falls through in a circle")

		self.editor.add(SAY_LINE, monarch("rightDpadUp"))
		self.assertEqual([(MONARCH, "falls through in a circle")], self.editor.commit(refuse))
		self.assertTrue(self.editor.isDirty)


class TestGathering(unittest.TestCase):
	def test_devicesAreConnectedThenTheKeyboardThenSavedOnes(self):
		labels = {MONARCH: "Monarch", "handyTech": "Handy Tech"}
		choices = dialog.deviceChoices(
			[MONARCH, "noBraille"], [KEYBOARD, "handyTech", MONARCH], lambda name: labels.get(name, name)
		)
		self.assertEqual(
			[(MONARCH, True), (KEYBOARD, True), ("handyTech", False)],
			[(choice.name, choice.connected) for choice in choices],
		)
		self.assertIn("not connected", choices[-1].label)

	def test_commandsAreInputGesturesOwnLessEmulations(self):
		info = types.SimpleNamespace(
			moduleName="globalCommands", className="GlobalCommands", scriptName="sayAll"
		)
		emulation = types.SimpleNamespace(
			moduleName="globalCommands", className="GlobalCommands", scriptName="kb:tab"
		)
		mappings = {"System caret": {"Say all": info, "tab": emulation}, "Emulated": {"tab": emulation}}
		commands = dialog.commandsFromMappings(mappings, "Emulated")
		self.assertEqual(
			[("System caret", "Say all", "sayAll")],
			[(c.category, c.name, c.target.scriptName) for c in commands],
		)

	def test_theLayerKeysKeysAreReserved(self):
		toggle = types.SimpleNamespace(scriptName="keyLayerToggle", gestures=["kb:nvda+control+shift+l"])
		other = types.SimpleNamespace(scriptName="sayAll", gestures=["kb:nvda+downarrow"])
		self.assertEqual(
			["kb:nvda+control+shift+l"],
			dialog.reservedFromMappings({"a": {"t": toggle, "o": other}}, "keyLayer"),
		)

	def test_theKeysOfTheLayerKeyForEachDisplayAreReservedToo(self):
		byPosition = types.SimpleNamespace(scriptName="keyLayerToggleDisplay0", gestures=["kb:nvda+alt+1"])
		lookalike = types.SimpleNamespace(scriptName="keyLayerToggleDisplayed", gestures=["kb:nvda+alt+2"])
		self.assertEqual(
			["kb:nvda+alt+1"],
			dialog.reservedFromMappings({"a": {"t": byPosition, "l": lookalike}}, "keyLayer"),
		)

	def test_aMembersOwnCommandsAreListedWithItsName(self):
		class FocusDriver:
			scriptCategory = "Braille"

			def script_toggleWizWheel(self, gesture):
				"""Cycles what the wiz wheel does"""

			def script_undocumented(self, gesture):
				pass

		commands = dialog.memberCommands([(FocusDriver(), "Focus 80")], [], "Miscellaneous")
		self.assertEqual(
			["Cycles what the wiz wheel does (Focus 80)"], [command.name for command in commands]
		)
		self.assertEqual("Braille", commands[0].category)
		again = dialog.memberCommands([(FocusDriver(), "Focus 80")], commands, "Miscellaneous")
		self.assertEqual([], again)

	def test_theTitleNamesTheProfile(self):
		self.assertEqual("Layered keys (Excel)", dialog.dialogTitle("Excel"))
		self.assertEqual("Layered keys (normal configuration)", dialog.dialogTitle(None))


if __name__ == "__main__":
	unittest.main()
