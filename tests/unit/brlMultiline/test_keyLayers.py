# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the layered keys model: layers, the fall through chain, storage, and the decision.

The decision tests follow the list `docs/design/layered-keys-plan.md` gives for phase 1: both
styles against a key bound in the layer, bound further down the chain, bound nowhere, a routing
key, escape found by its ordinary script, space with z, a layer that binds escape itself, and the
layer key.
"""

import json
import os
import sys
import unittest

sys.path.insert(
	0,
	os.path.join(os.path.dirname(__file__), "..", "..", "..", "addon", "globalPlugins", "brlMultiline"),
)

import keyLayers  # noqa: E402
from keyLayers import (  # noqa: E402
	DEFAULT_ID,
	KEYBOARD,
	LEAVE,
	MONARCH,
	ONE_SHOT,
	PASS,
	RUN,
	STAYS_ON,
	Layer,
	LayerSet,
	LayerSetError,
	Target,
	decide,
	newLayer,
	normalize,
)

PLUGIN = "globalPlugins.brlMultiline"


def monarch(key):
	return normalize(f"br({MONARCH}):{key}")


PAN_UP = Target.script(PLUGIN, "GlobalPlugin", "graphicsPanUp")
SAY_LINE = Target.script("globalCommands", "GlobalCommands", "reportCurrentLine")
ZOOM_IN = Target.script(PLUGIN, "GlobalPlugin", "graphicsZoomIn")


def monarchLayers(graphicsStyle=STAYS_ON, chartFallsThrough="graphics"):
	default = newLayer(MONARCH, DEFAULT_ID, bindings={monarch("leftDpadDown"): SAY_LINE})
	graphics = newLayer(
		MONARCH,
		"graphics",
		"Graphics",
		"graphics",
		style=graphicsStyle,
		bindings={monarch("leftDpadUp"): PAN_UP},
	)
	chart = newLayer(
		MONARCH,
		"chart",
		"Chart",
		"chart",
		fallsThrough=chartFallsThrough,
		bindings={monarch("zoomIn"): ZOOM_IN},
	)
	return LayerSet(MONARCH, (graphics, chart, default))


def nothing():
	"""The ordinary script of a key NVDA has no command for, such as the escape key."""
	return None


class TestLayers(unittest.TestCase):
	def test_aSetAlwaysHasADefaultLayerFirst(self):
		layers = LayerSet(MONARCH, (newLayer(MONARCH, "graphics", "Graphics", "graphics"),))
		self.assertEqual(DEFAULT_ID, layers.default.id)
		self.assertEqual(DEFAULT_ID, layers.layers[0].id)
		self.assertEqual(DEFAULT_ID, monarchLayers().layers[0].id)

	def test_theStyleFollowsTheContext(self):
		"""Decided with the reader: a generic layer is one shot, a layer for somewhere stays on."""
		self.assertEqual(ONE_SHOT, newLayer(MONARCH, "x").style)
		self.assertEqual(STAYS_ON, newLayer(MONARCH, "x", context="graphics").style)

	def test_aBrailleLayerLeavesOnSpaceWithZ(self):
		self.assertEqual(frozenset({monarch("space+dot1+dot3+dot5+dot6")}), newLayer(MONARCH, "x").exitKeys)
		self.assertEqual(frozenset(), newLayer(KEYBOARD, "x").exitKeys)

	def test_theChainEndsAtTheDefaultLayer(self):
		self.assertEqual(
			["chart", "graphics", DEFAULT_ID], [layer.id for layer in monarchLayers().chain("chart")]
		)
		self.assertEqual([DEFAULT_ID], [layer.id for layer in monarchLayers().chain(DEFAULT_ID)])

	def test_aBrokenChainStillEndsAtTheDefaultLayer(self):
		"""A set read back broken still has to work; it is refused only when saved."""
		missing = monarchLayers(chartFallsThrough="nowhere")
		self.assertEqual(["chart", DEFAULT_ID], [layer.id for layer in missing.chain("chart")])
		circle = monarchLayers().withLayer(replace_(monarchLayers().get("graphics"), fallsThrough="chart"))
		self.assertEqual(["chart", "graphics", DEFAULT_ID], [layer.id for layer in circle.chain("chart")])

	def test_lookupFindsTheNearestLayerThatBindsTheKey(self):
		layers = monarchLayers()
		self.assertEqual((ZOOM_IN, layers.get("chart")), layers.lookup("chart", [monarch("zoomIn")]))
		self.assertEqual(PAN_UP, layers.lookup("chart", [monarch("leftDpadUp")])[0])
		self.assertEqual(SAY_LINE, layers.lookup("chart", [monarch("leftDpadDown")])[0])
		self.assertIsNone(layers.lookup("chart", [monarch("rightDpadUp")]))

	def test_aLayerOverridesTheLayerItFallsThroughTo(self):
		layers = monarchLayers().withLayer(
			replace_(monarchLayers().get("chart"), bindings={monarch("leftDpadUp"): SAY_LINE}),
		)
		self.assertEqual(SAY_LINE, layers.lookup("chart", [monarch("leftDpadUp")])[0])

	def test_theMostSpecificContextPresentWins(self):
		layers = monarchLayers()
		self.assertEqual("chart", layers.forContexts(["chart", "graphics"]).id)
		self.assertEqual("graphics", layers.forContexts(["picture", "graphics"]).id)
		self.assertEqual(DEFAULT_ID, layers.forContexts([]).id)
		self.assertEqual(DEFAULT_ID, LayerSet(KEYBOARD).forContexts(["graphics"]).id)

	def test_withLayerReplacesByIdAndKeepsTheRest(self):
		layers = monarchLayers().withLayer(newLayer(MONARCH, "graphics", "Pictures"))
		self.assertEqual("Pictures", layers.get("graphics").name)
		self.assertIsNotNone(layers.get("chart"))
		self.assertEqual(3, len(layers.layers))


def replace_(layer, **changes):
	from dataclasses import replace

	return replace(layer, **changes)


class TestStorage(unittest.TestCase):
	def test_aSetSurvivesTheRoundTrip(self):
		layers = monarchLayers()
		keyboard = LayerSet(
			KEYBOARD,
			(
				newLayer(
					KEYBOARD,
					"graphics",
					"Graphics",
					"graphics",
					autoEnable=True,
					bindings={
						normalize("kb:numpad8"): Target.script(
							PLUGIN, "GlobalPlugin", "graphicsPanUp", MONARCH
						),
						normalize("kb:numpad0"): Target.key("kb:downArrow"),
						normalize("kb:numpadDelete"): Target.blocked(),
					},
				),
			),
		)
		for original in (layers, keyboard):
			read, problem = LayerSet.fromText(original.device, original.toText())
			self.assertIsNone(problem)
			self.assertEqual(original, read)

	def test_theStoredFormCarriesItsVersion(self):
		self.assertEqual(keyLayers.VERSION, json.loads(monarchLayers().toText())["version"])

	def test_nothingStoredIsJustTheDefaultLayer(self):
		layers, problem = LayerSet.fromText(MONARCH, "")
		self.assertIsNone(problem)
		self.assertEqual([DEFAULT_ID], [layer.id for layer in layers.layers])

	def test_unreadableTextIsTheDefaultLayerAndAProblem(self):
		layers, problem = LayerSet.fromText(MONARCH, "{not json")
		self.assertIn("could not read", problem)
		self.assertEqual([DEFAULT_ID], [layer.id for layer in layers.layers])

	def test_whatCannotBeReadIsLeftOutAndTheRestKept(self):
		text = json.dumps(
			{
				"version": keyLayers.VERSION,
				"layers": [
					{
						"id": "graphics",
						"bindings": {"br(x):a": {"nonsense": 1}, "BR(X):B": {"key": "kb:tab"}},
					},
					{"no id": True},
				],
			},
		)
		layers, problem = LayerSet.fromText("x", text)
		self.assertIsNotNone(problem)
		self.assertEqual({"br(x):b": Target.key("kb:tab")}, layers.get("graphics").bindings)

	def test_aBindingToACommandThatIsGoneIsKept(self):
		"""An add-on disabled for a week should not cost the reader their bindings."""
		gone = Target.script("globalPlugins.somethingDisabled", "GlobalPlugin", "whatever")
		layers = LayerSet(MONARCH, (newLayer(MONARCH, "x", bindings={monarch("a"): gone}),))
		read, _problem = LayerSet.fromText(MONARCH, layers.toText())
		self.assertEqual(gone, read.get("x").bindings[monarch("a")])

	def test_aCircleIsRefusedWhenSaving(self):
		circle = monarchLayers().withLayer(replace_(monarchLayers().get("graphics"), fallsThrough="chart"))
		with self.assertRaises(LayerSetError) as caught:
			circle.toText()
		self.assertIn("circle", str(caught.exception))

	def test_fallingThroughToNothingIsRefusedWhenSaving(self):
		with self.assertRaises(LayerSetError):
			monarchLayers(chartFallsThrough="nowhere").toText()

	def test_aDefaultLayerReadBackHasNoContext(self):
		text = json.dumps(
			{"version": 1, "layers": [{"id": DEFAULT_ID, "context": "graphics", "fallsThrough": "x"}]}
		)
		layers, _problem = LayerSet.fromText(MONARCH, text)
		self.assertIsNone(layers.default.context)
		self.assertIsNone(layers.default.fallsThrough)


class TestDecide(unittest.TestCase):
	"""The phase 1 list, for both styles."""

	def decide(self, layers, activeId, key, ordinary=None, hasCells=False):
		return decide(
			layers,
			activeId,
			[monarch(key)],
			lambda: ordinary,
			hasCells=hasCells,
			isLayerCommand=lambda name: bool(name) and name.startswith("keyLayer"),
		)

	def styles(self):
		for style in (STAYS_ON, ONE_SHOT):
			with self.subTest(style=style):
				yield style, monarchLayers(graphicsStyle=style)

	def test_aKeyBoundInTheLayerRuns(self):
		for style, layers in self.styles():
			decision = self.decide(layers, "graphics", "leftDpadUp")
			self.assertEqual((RUN, PAN_UP, "graphics"), (decision.action, decision.target, decision.layer.id))
			self.assertEqual(style == ONE_SHOT, decision.endsOneShot)

	def test_aKeyBoundFurtherDownTheChainRuns(self):
		for style, layers in self.styles():
			decision = self.decide(layers, "graphics", "leftDpadDown")
			self.assertEqual(
				(RUN, SAY_LINE, DEFAULT_ID), (decision.action, decision.target, decision.layer.id)
			)
			self.assertEqual(style == ONE_SHOT, decision.endsOneShot)

	def test_aKeyBoundNowhereIsTransparent(self):
		"""Braille typing: the key does what it always does, and a layer that stays on stays on."""
		for style, layers in self.styles():
			decision = self.decide(layers, "graphics", "dot1+dot2", ordinary="braille_dots")
			self.assertEqual(PASS, decision.action)
			self.assertEqual(style == ONE_SHOT, decision.endsOneShot)

	def test_theOrdinaryLookupIsNotRunForABoundKey(self):
		"""It is NVDA's whole script lookup, and a bound key does not need it."""

		def mustNotRun():
			raise AssertionError("looked up a bound key")

		decide(monarchLayers(), "graphics", [monarch("leftDpadUp")], mustNotRun)

	def test_routingNeverEndsALayer(self):
		for _style, layers in self.styles():
			byCells = self.decide(layers, "graphics", "routerSet1_routerKey", hasCells=True)
			byScript = self.decide(layers, "graphics", "anything", ordinary="braille_routeTo")
			panning = self.decide(layers, "graphics", "panLeft", ordinary="braille_scrollBack")
			for decision in (byCells, byScript, panning):
				self.assertEqual(PASS, decision.action)
				self.assertFalse(decision.endsOneShot)

	def test_escapeFoundByItsOrdinaryScriptLeaves(self):
		for _style, layers in self.styles():
			decision = self.decide(layers, "graphics", "space+dot1+dot5", ordinary="kb:escape")
			self.assertEqual(LEAVE, decision.action)

	def test_theKeyboardsEscapeKeyLeaves(self):
		"""Found on hardware: the escape key has no NVDA command, so no ordinary script to recognise."""
		for style in (STAYS_ON, ONE_SHOT):
			with self.subTest(style=style):
				layers = LayerSet(
					KEYBOARD, (newLayer(KEYBOARD, "graphics", "Graphics", "graphics", style=style),)
				)
				decision = decide(
					layers, "graphics", [normalize("kb(laptop):escape"), normalize("kb:escape")], nothing
				)
				self.assertEqual(LEAVE, decision.action)
				shifted = decide(layers, "graphics", [normalize("kb:shift+escape")], nothing)
				self.assertEqual(PASS, shifted.action)

	def test_aKeyboardLayerThatBindsEscapeKeepsIt(self):
		layers = LayerSet(
			KEYBOARD,
			(newLayer(KEYBOARD, "x", bindings={normalize("kb:escape"): Target.key("kb:home")}),),
		)
		self.assertEqual(RUN, decide(layers, "x", [normalize("kb:escape")], nothing).action)

	def test_spaceWithZLeaves(self):
		for _style, layers in self.styles():
			self.assertEqual(LEAVE, self.decide(layers, "graphics", "space+dot1+dot3+dot5+dot6").action)

	def test_aLayerThatBindsEscapeKeepsIt(self):
		escapeTarget = Target.key("kb:home")
		for style, layers in self.styles():
			graphics = replace_(
				layers.get("graphics"),
				bindings={
					monarch("space+dot1+dot3+dot5+dot6"): escapeTarget,
					monarch("space+dot1+dot5"): escapeTarget,
				},
			)
			layers = layers.withLayer(graphics)
			self.assertEqual(RUN, self.decide(layers, "graphics", "space+dot1+dot3+dot5+dot6").action)
			self.assertEqual(
				RUN, self.decide(layers, "graphics", "space+dot1+dot5", ordinary="kb:escape").action
			)

	def test_theLayerKeyAlwaysReachesItsCommand(self):
		for _style, layers in self.styles():
			decision = self.decide(layers, "graphics", "space+dot1+dot2+dot3+dot7", ordinary="keyLayerToggle")
			self.assertEqual(PASS, decision.action)
			self.assertFalse(decision.endsOneShot, "the toggle turns the layer off, not the decider")

	def test_exitKeysAreTheActiveLayersOwn(self):
		layers = monarchLayers()
		chart = replace_(layers.get("chart"), exitKeys=frozenset())
		layers = layers.withLayer(chart)
		self.assertEqual(PASS, self.decide(layers, "chart", "space+dot1+dot3+dot5+dot6").action)

	def test_aLayerThatIsGoneDecidesNothing(self):
		self.assertEqual(PASS, self.decide(monarchLayers(), "gone", "leftDpadUp").action)


class TestAutomaticAndInherited(unittest.TestCase):
	def layers(self):
		return (
			monarchLayers()
			.withLayer(replace_(monarchLayers().get("graphics"), autoEnable=True))
			.withLayer(
				replace_(monarchLayers().get("chart"), autoEnable=True),
			)
		)

	def test_theMostSpecificAutomaticLayerPresentComesOn(self):
		self.assertEqual("chart", self.layers().autoLayerFor(["chart", "graphics"]).id)
		self.assertEqual("graphics", self.layers().autoLayerFor(["picture", "graphics"]).id)
		self.assertIsNone(self.layers().autoLayerFor([]))

	def test_aLayerThatDoesNotComeOnByItselfNeverDoes(self):
		self.assertIsNone(monarchLayers().autoLayerFor(["chart", "graphics"]))

	def test_aDeclinedContextIsSkipped(self):
		self.assertEqual("graphics", self.layers().autoLayerFor(["chart", "graphics"], declined=["chart"]).id)
		self.assertIsNone(self.layers().autoLayerFor(["graphics"], declined=["graphics"]))

	def test_keysFromBelowAreTheNearestAndNotTheLayersOwn(self):
		layers = monarchLayers()
		inherited = layers.inherited("chart")
		self.assertEqual(
			{monarch("leftDpadUp"): (PAN_UP, "graphics"), monarch("leftDpadDown"): (SAY_LINE, DEFAULT_ID)},
			{key: (target, layer.id) for key, (target, layer) in inherited.items()},
		)
		self.assertNotIn(monarch("zoomIn"), inherited)
		self.assertEqual({}, layers.inherited(DEFAULT_ID))


class TestDefaultLayers(unittest.TestCase):
	"""What the add-on ships: the Monarch works out of the box, and reset puts this back."""

	def test_theMonarchReadsOnTheLayerKeyAndHasAGraphicsLayer(self):
		layers = LayerSet(MONARCH, tuple(keyLayers.defaultLayers(MONARCH, PLUGIN)))
		self.assertEqual(ONE_SHOT, layers.default.style)
		self.assertEqual("reportCurrentLine", layers.default.bindings[monarch("leftDpadUp")].scriptName)
		graphics = layers.get("graphics")
		self.assertEqual((STAYS_ON, "graphics"), (graphics.style, graphics.context))
		self.assertEqual("graphicsPanUp", graphics.bindings[monarch("leftDpadUp")].scriptName)
		self.assertEqual("graphicsZoomIn", graphics.bindings[monarch("zoomIn")].scriptName)

	def test_theMonarchsGraphicsLayerComesOnByItselfAndTheKeyboardsDoesNot(self):
		"""Putting a drawing up is asking for it; the keypad is the review cursor on a desktop layout."""
		monarchGraphics = LayerSet(MONARCH, tuple(keyLayers.defaultLayers(MONARCH, PLUGIN))).get("graphics")
		keyboardGraphics = LayerSet(KEYBOARD, tuple(keyLayers.defaultLayers(KEYBOARD, PLUGIN))).get(
			"graphics"
		)
		self.assertTrue(monarchGraphics.autoEnable)
		self.assertFalse(keyboardGraphics.autoEnable)

	def test_theMonarchHasATableLayerForTheLayerKeyOnly(self):
		tables = LayerSet(MONARCH, tuple(keyLayers.defaultLayers(MONARCH, PLUGIN))).get("table")
		self.assertEqual(("table", STAYS_ON, False), (tables.context, tables.style, tables.autoEnable))
		self.assertEqual(
			("documentBase", "DocumentWithTableNavigation", "nextRow"),
			tuple(tables.bindings[monarch("leftDpadDown")][1:4]),
		)
		self.assertEqual("flowNextColumns", tables.bindings[monarch("zoomIn")].scriptName)

	def test_theRightPadAndTheCentreAreNeverBound(self):
		"""The right pad stays the arrow keys; the centre is not a key at all."""
		for layer in keyLayers.defaultLayers(MONARCH, PLUGIN):
			for identifier in layer.bindings:
				self.assertNotIn("rightdpad", identifier)
				self.assertNotIn("center", identifier)

	def test_theKeypadActsForTheMonarch(self):
		graphics = next(
			layer for layer in keyLayers.defaultLayers(KEYBOARD, PLUGIN) if layer.id == "graphics"
		)
		self.assertEqual(MONARCH, graphics.bindings[normalize("kb:numpad8")].actsFor)
		self.assertEqual(MONARCH, graphics.bindings[normalize("kb:numLockNumpad8")].actsFor)
		self.assertIn(normalize("kb:numLock+numpadPlus"), graphics.bindings)

	def test_anotherDisplayShipsWithNothing(self):
		self.assertEqual([], keyLayers.defaultLayers("freedomScientific", PLUGIN))

	def test_nothingStoredReadsAsTheDefaults(self):
		factory = keyLayers.defaultLayers(MONARCH, PLUGIN)
		layers, problem = LayerSet.fromText(MONARCH, "", factory)
		self.assertIsNone(problem)
		self.assertEqual(LayerSet(MONARCH, tuple(factory)), layers)

	def test_unreadableStorageFallsBackToTheDefaults(self):
		"""Working keys are better than none."""
		factory = keyLayers.defaultLayers(MONARCH, PLUGIN)
		layers, problem = LayerSet.fromText(MONARCH, "{not json", factory)
		self.assertIn("defaults", problem)
		self.assertIsNotNone(layers.get("graphics"))

	def test_whatTheReaderStoredWinsOverTheDefaults(self):
		mine = LayerSet(MONARCH, (newLayer(MONARCH, "tables", "Tables"),))
		layers, _problem = LayerSet.fromText(MONARCH, mine.toText(), keyLayers.defaultLayers(MONARCH, PLUGIN))
		self.assertIsNone(layers.get("graphics"))
		self.assertIsNotNone(layers.get("tables"))

	def test_theDefaultsCanBeSaved(self):
		for device in (MONARCH, KEYBOARD):
			layers = LayerSet(device, tuple(keyLayers.defaultLayers(device, PLUGIN)))
			self.assertEqual(layers, LayerSet.fromText(device, layers.toText())[0])


class TestNormalize(unittest.TestCase):
	def test_matchesNvda(self):
		self.assertEqual("br(x):dot1+space", normalize("br(X):Space+DOT1"))
		self.assertEqual("kb:numlock+numpadplus", normalize("kb:numpadPlus+numLock"))

	def test_aLayerIsItsOwnObject(self):
		self.assertIsInstance(newLayer(MONARCH, "x"), Layer)


if __name__ == "__main__":
	unittest.main()
