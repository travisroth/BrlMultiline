# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for layered keys where they meet the rest of the add-on.

The contexts read off graphics mode, the configuration they are stored in, the commands the
reader presses, and the commands that used to read a gesture's source and now ask which display
a key is for.
"""

import types
import unittest

from ._stubs import (
	BrailleDisplayGesture,
	FakeHandler,
	KeyboardInputGesture,
	flashedMessages,
	installStubs,
	loadPlugin,
	resetPluginState,
)

installStubs()

import braille  # noqa: E402

from brlMultiline import bmConfig, keyLayerContexts, keyLayerDispatch, keyLayers, panning  # noqa: E402

plugin = loadPlugin()
GlobalPlugin = plugin.GlobalPlugin
MONARCH = keyLayers.MONARCH
KEYBOARD = keyLayers.KEYBOARD


class FakeMode:
	def __init__(self, active=True, source=None, plugin=None):
		self.active = active
		self.source = source
		self.plugin = plugin

	def leave(self):
		"""As `GraphicsMode.leave` does: take the figure down, then tell the plugin."""
		self.active = False
		self.source = None
		if self.plugin is not None:
			self.plugin.onGraphicsChanged()


class TestContexts(unittest.TestCase):
	def test_nothingWhenGraphicsModeIsOff(self):
		host = types.SimpleNamespace(graphicsMode=FakeMode(active=False), _pictureDrawing=None)
		self.assertEqual([], keyLayerContexts.presentContexts(host))

	def test_aChartIsAChartAndGraphics(self):
		chart = types.SimpleNamespace(redraw=lambda *args: None)
		host = types.SimpleNamespace(graphicsMode=FakeMode(source=chart), _pictureDrawing=None)
		self.assertEqual(["chart", "graphics"], keyLayerContexts.presentContexts(host))

	def test_aPictureIsAPictureAndGraphics(self):
		picture = types.SimpleNamespace(redraw=None)
		host = types.SimpleNamespace(graphicsMode=FakeMode(source=picture), _pictureDrawing=picture)
		self.assertEqual(["picture", "graphics"], keyLayerContexts.presentContexts(host))

	def test_anythingElseDrawnIsGraphics(self):
		"""The glyph catalogue and the test figure."""
		figure = types.SimpleNamespace(redraw=None)
		host = types.SimpleNamespace(graphicsMode=FakeMode(source=figure), _pictureDrawing=None)
		self.assertEqual(["graphics"], keyLayerContexts.presentContexts(host))

	def test_theContextsAreInTheModelsOrder(self):
		chart = types.SimpleNamespace(redraw=lambda *args: None)
		host = types.SimpleNamespace(graphicsMode=FakeMode(source=chart), _pictureDrawing=None)
		present = keyLayerContexts.presentContexts(host)
		self.assertEqual(present, [context for context in keyLayers.CONTEXTS if context in present])

	def test_aModeThatCannotBeReadIsNoContext(self):
		class Broken:
			@property
			def active(self):
				raise RuntimeError("gone")

		self.assertEqual([], keyLayerContexts.presentContexts(types.SimpleNamespace(graphicsMode=Broken())))
		self.assertEqual([], keyLayerContexts.presentContexts(None))


class TestStorage(unittest.TestCase):
	def setUp(self):
		resetPluginState()

	def test_aDeviceWithNothingStoredReadsEmpty(self):
		self.assertEqual("", bmConfig.keyLayerText(MONARCH))

	def test_whatIsStoredIsReadBackPerDevice(self):
		bmConfig.setKeyLayerText(MONARCH, "monarch layers")
		bmConfig.setKeyLayerText(KEYBOARD, "keyboard layers")
		self.assertEqual("monarch layers", bmConfig.keyLayerText(MONARCH))
		self.assertEqual("keyboard layers", bmConfig.keyLayerText(KEYBOARD))
		self.assertEqual(
			[MONARCH, KEYBOARD], sorted(bmConfig.keyLayerDevices(), key=[MONARCH, KEYBOARD].index)
		)

	def test_keyedByDriverNameSoTheRowPitchCannotLoseThem(self):
		"""The Monarch is `brlMultilineMonarch_8x32` at one pitch and `_10x32` at the other."""
		bmConfig.setKeyLayerText(MONARCH, "kept")
		self.assertNotIn("x", "".join(bmConfig.keyLayerDevices()).replace("brlMultilineMonarch", ""))


class GestureFrom(BrailleDisplayGesture):
	def __init__(self, source):
		self.source = source
		self.identifiers = [f"br({source}):x"]


class KeyFrom(KeyboardInputGesture):
	def __init__(self):
		self.identifiers = ["kb:x"]


class PluginTestCase(unittest.TestCase):
	def setUp(self):
		resetPluginState()
		# Layer state is module state, and a plugin whose teardown fails in the stubs never reaches
		# the layers' own, so it is cleared here rather than trusted to have been.
		keyLayerDispatch.remove()
		braille.handler = FakeHandler(8, 32)
		self.plugin = GlobalPlugin()
		self.addCleanup(self.tidy)
		self.plugin.graphicsMode = FakeMode(active=False)
		flashedMessages.clear()

	def tidy(self):
		try:
			if not self.plugin._terminated:
				self.plugin.terminate()
		except Exception:
			pass
		keyLayerDispatch.remove()


class TestCommands(PluginTestCase):
	def test_theLayerKeyTurnsOnTheDefaultLayerAndSaysSo(self):
		self.plugin.script_keyLayerToggle(GestureFrom(MONARCH))
		self.assertEqual("Default layer on", flashedMessages[-1])
		self.plugin.script_keyLayerToggle(GestureFrom(MONARCH))
		self.assertEqual("Default layer off", flashedMessages[-1])

	def test_theLayerKeyOnAChartTakesTheShippedGraphicsLayer(self):
		chart = types.SimpleNamespace(redraw=lambda *args: None)
		self.plugin.graphicsMode = FakeMode(source=chart)
		self.plugin.script_keyLayerToggle(GestureFrom(MONARCH))
		self.assertEqual("Graphics layer on", flashedMessages[-1])

	def test_theKeyboardHasItsOwnLayer(self):
		self.plugin.script_keyLayerToggle(KeyFrom())
		self.assertIsNotNone(keyLayerDispatch.activeLayer(KEYBOARD))
		self.assertIsNone(keyLayerDispatch.activeLayer(MONARCH))

	def test_reportNamesEachLayerAndItsDevice(self):
		self.plugin.script_keyLayerReport(None)
		self.assertEqual("No layers on", flashedMessages[-1])
		self.plugin.script_keyLayerToggle(KeyFrom())
		self.plugin.script_keyLayerReport(None)
		self.assertEqual("Default layer on keyboard", flashedMessages[-1])

	def test_allOff(self):
		self.plugin.script_keyLayerToggle(KeyFrom())
		self.plugin.script_keyLayersOff(None)
		self.assertEqual("All layers off", flashedMessages[-1])
		self.assertEqual([], keyLayerDispatch.activeLayers())

	def test_thereIsNoCommandToAddLayers(self):
		"""The layouts ship as defaults; reset in the dialog puts them back."""
		self.assertFalse(hasattr(GlobalPlugin, "script_keyLayerUseSuggested"))

	def test_theDisplayCommandsReachTheDisplayByPosition(self):
		original = getattr(braille.handler, "display", None)
		braille.handler.display = types.SimpleNamespace(name=MONARCH)
		self.addCleanup(setattr, braille.handler, "display", original)
		self.plugin.script_keyLayerToggleDisplay0(KeyFrom())
		self.assertIsNotNone(keyLayerDispatch.activeLayer(MONARCH))
		self.plugin.script_keyLayerToggleDisplay1(KeyFrom())
		self.assertIn("There is no", flashedMessages[-1])

	def test_everyLayerCommandIsNamedSoALayerLetsItsKeysThrough(self):
		names = [name for name in dir(GlobalPlugin) if name.startswith("script_") and "Layer" in name]
		self.assertTrue(names)
		for name in names:
			self.assertTrue(name.startswith(f"script_{keyLayerDispatch.LAYER_COMMAND_PREFIX}"), name)

	def test_aProfileThatLosesTheLayerSaysSo(self):
		keyLayerDispatch.activate(MONARCH, "graphics")
		bmConfig.setKeyLayerText(MONARCH, keyLayers.LayerSet(MONARCH).toText())
		self.plugin._reloadKeyLayers()
		self.assertEqual("Graphics layer off", flashedMessages[-1])


class TestDrawingGoing(PluginTestCase):
	def setUp(self):
		super().setUp()
		chart = types.SimpleNamespace(redraw=lambda *args: None)
		self.plugin.graphicsMode = FakeMode(source=chart, plugin=self.plugin)

	def test_takingTheDrawingOffTakesItsLayerAndSaysBothOnce(self):
		self.plugin.script_keyLayerToggle(GestureFrom(MONARCH))
		self.plugin.script_keyLayerToggle(KeyFrom())
		self.plugin.script_toggleGraphics(None)
		self.assertEqual("Drawing off, Graphics layer off", flashedMessages[-1])
		self.assertEqual([], keyLayerDispatch.activeLayers())

	def test_takingTheDrawingOffWithNoLayerOnSaysOnlyThat(self):
		self.plugin.script_toggleGraphics(None)
		self.assertEqual("Drawing off", flashedMessages[-1])

	def test_aDrawingThatGoesByItselfSaysItsLayerWentOnItsOwn(self):
		self.plugin.script_keyLayerToggle(GestureFrom(MONARCH))
		self.plugin.graphicsMode.leave()
		self.assertEqual("Graphics layer off", flashedMessages[-1])

	def test_theDefaultLayerStaysWhenTheDrawingGoes(self):
		self.plugin.graphicsMode = FakeMode(active=False, plugin=self.plugin)
		self.plugin.script_keyLayerToggle(GestureFrom(MONARCH))
		self.plugin.onGraphicsChanged()
		self.assertEqual("Default", keyLayerDispatch.layerName(keyLayerDispatch.activeLayer(MONARCH)))


class TestWhichDisplay(unittest.TestCase):
	def test_panningAsksWhichDisplayAKeyIsFor(self):
		self.assertEqual(MONARCH, panning._displayFor(GestureFrom(MONARCH)))
		self.assertIsNone(panning._displayFor(KeyFrom()))
		acting = KeyFrom()
		setattr(acting, keyLayerDispatch.ACTS_FOR_ATTRIBUTE, MONARCH)
		self.assertEqual(MONARCH, panning._displayFor(acting))


if __name__ == "__main__":
	unittest.main()
