# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Regressions from the review of the translation commit, ca7973b.

Written by the reviewer as a standalone script, and kept here as an ordinary test module so that
it runs with the rest. Each test reproduces one finding: search words split at combining marks,
the shipped layers' names left in English, a layer's properties error with an English reason in
it, and the fallback outside NVDA taking the interactive interpreter's last result for gettext.
"""

import builtins
import runpy
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from . import test_keyLayerDialog as editorHarness
from . import test_translations as harness
from brlMultiline import i18n, keyLayerDispatch, keyLayers, layoutRegions  # noqa: E402

REPO = Path(__file__).resolve().parents[3]


class TranslationRegressions(unittest.TestCase):
	def test_search_accepts_canonically_equivalent_accented_text(self):
		# Same word, composed in the catalogue and decomposed in provider text.
		with mock.patch.object(layoutRegions, "pgettext", return_value="búsqueda"):
			self.assertTrue(layoutRegions.mentionsSearch("búsqueda"))
			self.assertTrue(
				layoutRegions.mentionsSearch("bu\u0301squeda"),
				"Combining accents split an otherwise identical search word.",
			)

	def test_combining_marks_do_not_turn_search_words_into_single_letter_prefixes(self):
		# U+094B is a vowel mark. The entire word must remain one token;
		# splitting it turns the configured prefix into isolated letters.
		with mock.patch.object(layoutRegions, "pgettext", return_value="खोज"):
			self.assertTrue(layoutRegions.mentionsSearch("खोज"))
			self.assertFalse(
				layoutRegions.mentionsSearch("खाना"),
				"An unrelated word matched only because the search prefix was split.",
			)

	def test_shipped_layer_names_are_translated_but_user_names_are_not(self):
		def translate(text):
			return "Translated Graphics" if text == "Graphics" else text

		with ExitStack() as stack:
			for module in (i18n, keyLayers, keyLayerDispatch):
				stack.enter_context(mock.patch.object(module, "_", translate, create=True))
				stack.enter_context(
					mock.patch.object(module, "pgettext", lambda context, text: translate(text), create=True),
				)
			shipped = keyLayers.LayerSet(
				keyLayers.MONARCH,
				tuple(keyLayers.defaultLayers(keyLayers.MONARCH, "globalPlugins.brlMultiline")),
			)
			shown = keyLayerDispatch.layerName(shipped.get("graphics"))
			custom = keyLayerDispatch.layerName(keyLayers.newLayer(keyLayers.MONARCH, "custom", "Graphics"))
		self.assertEqual(shown, "Translated Graphics", "The shipped layer's name bypassed gettext.")
		self.assertEqual(
			custom, "Graphics", "A user-written name must not be translated as application text.",
		)

	def test_layer_properties_error_does_not_embed_an_english_reason(self):
		fixture = editorHarness.EditorTestCase()
		fixture.setUp()
		self.addCleanup(fixture.doCleanups)
		editor = fixture.editor
		editor.newLayer("Chart")
		editor.setProperties("Chart", "chart", False, keyLayers.STAYS_ON, "graphics", ())
		editor.selectLayer("graphics")
		with ExitStack() as stack:
			for module in (i18n, keyLayers, editorHarness.dialog):
				stack.enter_context(mock.patch.object(module, "_", harness._cross, create=True))
			problem = editor.setProperties("Graphics", "graphics", False, keyLayers.STAYS_ON, "chart", ())
		self.assertIsNotNone(problem)
		self.assertNotIn(
			"falls through in a circle",
			problem,
			"The translated dialog wrapper contains an untranslated validator message.",
		)

	def test_standalone_i18n_ignores_the_interactive_interpreters_last_result(self):
		unavailable = SimpleNamespace(
			initTranslation=mock.Mock(side_effect=RuntimeError("not an installed add-on")),
		)
		with (
			mock.patch.dict(sys.modules, {"addonHandler": unavailable}),
			mock.patch.object(builtins, "_", 42, create=True),
		):
			namespace = runpy.run_path(str(REPO / "addon/globalPlugins/brlMultiline/i18n.py"))
		self.assertTrue(callable(namespace["_"]), "Python's interactive last-result value became gettext.")
		self.assertEqual(namespace["_"]("navigation"), "navigation")
