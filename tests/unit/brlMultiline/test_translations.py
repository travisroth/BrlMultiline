# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""The add-on's words reach its translations.

NVDA's `addonHandler.initTranslation()` points `_`, `ngettext` and `pgettext` at an add-on's own
translations for the module that calls it, and for no other. Eighteen modules used `_` without
calling it, so their words went to NVDA's translations, which do not have them, and would never
have been shown translated however well translated. And the layout overview's rules and drawing,
kept free of NVDA for testing, had no translation at all.
"""

import ast
import contextlib
import json
import os
import re
import unittest
from unittest import mock

from ._stubs import installStubs

installStubs()

from brlMultiline import i18n, layoutDraw, layoutRegions  # noqa: E402
from brlMultiline.appRegions import findAppRegions  # noqa: E402
from brlMultiline.layoutRegions import Box, Region, countWords, kindName, searchWords, whereWords  # noqa: E402

ADDON = os.path.join(os.path.dirname(__file__), "..", "..", "..", "addon")
FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures")
GETTEXT = ("_", "ngettext", "pgettext", "npgettext")


def _cross(message: str) -> str:
	"""A translation into a language whose every letter is x, leaving `{placeholders}` as they are,
	so anything still in English afterwards did not go through it."""
	return re.sub(r"\{[^}]*\}|[^\W\d_]", lambda m: m.group(0) if m.group(0).startswith("{") else "x", message)


@contextlib.contextmanager
def crossed():
	"""Every layout module's gettext functions, translating into x."""
	fake = {
		"_": _cross,
		"ngettext": lambda singular, plural, count: _cross(singular if count == 1 else plural),
		"pgettext": lambda context, message: _cross(message),
	}
	with contextlib.ExitStack() as stack:
		for module in (layoutRegions, layoutDraw):
			for name, function in fake.items():
				if hasattr(module, name):
					stack.enter_context(mock.patch.object(module, name, function))
		yield


def english(text: str) -> list[str]:
	""":return: the words of a text that are in English, the overview's own words being in small
	letters and a page's or a window's names starting with capitals or being x."""
	return re.findall(r"\b(?!x+\b)[a-z]{2,}\b", text)


class TestEveryModuleUsesTheAddonsTranslations(unittest.TestCase):
	def test_aModuleWithWordsToTranslateSetsUpTheAddonsTranslations(self):
		missing = []
		for folder, _dirs, files in os.walk(ADDON):
			for name in files:
				if not name.endswith(".py"):
					continue
				path = os.path.join(folder, name)
				with open(path, encoding="utf-8") as f:
					source = f.read()
				calls = {
					node.func.id
					for node in ast.walk(ast.parse(source))
					if isinstance(node, ast.Call)
					and isinstance(node.func, ast.Name)
					and node.func.id in GETTEXT
				}
				if not calls:
					continue
				ownSetUp = "addonHandler.initTranslation()" in source
				shared = re.search(r"from \.i18n import", source)
				if not (ownSetUp or shared):
					missing.append(os.path.relpath(path, ADDON))
		self.assertEqual(missing, [], "these use gettext without the add-on's translations")

	def test_outsideNVDAtheWordsAreEnglish(self):
		self.assertEqual(i18n._("navigation"), "navigation")
		self.assertEqual(i18n.ngettext("{count} link", "{count} links", 2), "{count} links")
		self.assertEqual(i18n.pgettext("braille box label", "nav"), "nav")


class TestTheLayoutOverviewIsTranslated(unittest.TestCase):
	KINDS = (
		"banner navigation main complementary contentinfo search form region frame header footer sidebar"
		" content table tabs accordion area menubar toolbar ribbon statusbar tree list grid document"
	).split()

	def test_everyKindsName(self):
		with crossed():
			for kind in self.KINDS:
				with self.subTest(kind=kind):
					self.assertEqual(english(kindName(kind)), [])

	def test_everyShortLabel(self):
		with crossed():
			for kind in self.KINDS:
				with self.subTest(kind=kind):
					self.assertEqual(english(layoutDraw.shortLabel(kind)), [])

	def test_everyPlace(self):
		within = Box(0, 0, 900, 900)
		boxes = [Box(0, 0, 900, 900), Box(0, 0, 900, 100), Box(0, 400, 900, 100), Box(0, 800, 900, 100)]
		boxes += [Box(0, 0, 100, 900), Box(400, 0, 100, 900), Box(800, 0, 100, 900)]
		boxes += [Box(x, y, 100, 100) for x in (0, 400, 800) for y in (0, 400, 800)]
		with crossed():
			places = [whereWords(box, within) for box in boxes]
		self.assertEqual([english(p) for p in places], [[]] * len(places))
		self.assertEqual(len(places), 16, "every place, each a phrase of its own")

	def test_everyCountInBothNumbers(self):
		with crossed():
			for name in ("tabs", "sections", "items", "fields", "buttons", "links"):
				for count in (1, 3):
					with self.subTest(name=name, count=count):
						said = countWords(name, count)
						self.assertTrue(said.startswith(str(count)), said)
						self.assertEqual(english(said), [])

	def test_aNamedAreasTitle(self):
		with crossed():
			title = Region("navigation", "Primary", Box(0, 0, 10, 10), "landmark").title
		self.assertEqual(english(title), [])
		self.assertIn("Primary", title, "the page's own name is the page's")

	def test_whatAPressSaysTheKeyAndTheText(self):
		"""Through the drawing of a window, as a reader meets them."""
		with open(os.path.join(FIXTURES, "appLayout", "explorer.json"), encoding="utf-8") as f:
			data = json.load(f)
		from .test_layoutDraw import PinBuffer

		labels = []

		def translate(word):
			labels.append(word)
			return [0xFF] * len(word)

		# The count words come from NVDA's own braille labels, in the reader's language; see
		# `layoutOverview.countStyle`. Here, the same in x.
		style = layoutDraw.CountStyle(words={k: _cross(v) for k, v in layoutDraw.COUNT_WORDS.items()})
		with crossed():
			page = findAppRegions(data["window"], data["windowRect"], data["focus"]["path"])
			overview = layoutDraw.composeFitting(
				PinBuffer, page, 96, 40, "screen", page.box, translate, 3, style=style,
			)
			said = [overview.describeAt(b.rect.left + 1, b.rect.top + 1) for b in overview.walk()]
			key = overview.keyText()
			lines = layoutRegions.describe(page, 3)
		# The window's own names, such as Explorer's "Status bar", are the window's, not ours.
		names = sorted({r.label for r in page.walk() if r.label}, key=len, reverse=True)
		for text in [*said, key, *lines, *labels]:
			for name in names:
				text = text.replace(name, "")
			with self.subTest(text=text):
				self.assertEqual(english(text), [])

	def test_aTableMadeTheMainContentIsCalledTableInTheReadersLanguage(self):
		page = Region("page", "", Box(0, 0, 1000, 1000), "page")
		strip = Region("area", "", Box(0, 0, 1000, 80), "geometry")
		table = Region("table", "", Box(0, 100, 1000, 850), "structure")
		page.children = [strip, table]
		with crossed():
			layoutRegions._nameByPosition(page, Box(0, 0, 1000, 1000))
		self.assertEqual(table.kind, "main")
		self.assertEqual(english(table.label), [])


class TestSearchInTheReadersLanguage(unittest.TestCase):
	def test_englishSearchAlways(self):
		self.assertTrue(layoutRegions.mentionsSearch("siteSearch"))
		self.assertFalse(layoutRegions.mentionsSearch("Research grants"))

	def test_theTranslatorsWords(self):
		french = mock.patch.object(layoutRegions, "pgettext", lambda context, message: "recherch")
		with french:
			self.assertEqual(searchWords(), ("search", "recherch"))
			self.assertTrue(layoutRegions.mentionsSearch("Rechercher dans le site"))
			self.assertTrue(layoutRegions.mentionsSearch("Search"))

	def test_wordsInAnyAlphabet(self):
		self.assertEqual(layoutRegions.words("Búsqueda rápida"), ["búsqueda", "rápida"])
		self.assertEqual(layoutRegions.words("Поиск по сайту"), ["поиск", "по", "сайту"])
		self.assertEqual(layoutRegions.words("siteSearch-box"), ["site", "search", "box"])
		with mock.patch.object(layoutRegions, "pgettext", lambda context, message: "поиск"):
			self.assertTrue(layoutRegions.mentionsSearch("Поиск по сайту"))


if __name__ == "__main__":
	unittest.main()


class TestTheKeyLayersWords(unittest.TestCase):
	def test_aShippedLayerTheReaderRenamedKeepsTheirName(self):
		from brlMultiline import keyLayerDispatch, keyLayers

		with mock.patch.object(
			keyLayerDispatch, "pgettext", lambda context, message: _cross(message), create=True,
		):
			shipped = keyLayers.newLayer(keyLayers.MONARCH, "graphics", "Graphics")
			renamed = keyLayers.newLayer(keyLayers.MONARCH, "graphics", "Drawings")
			self.assertEqual(keyLayerDispatch.layerName(shipped), "xxxxxxxx")
			self.assertEqual(keyLayerDispatch.layerName(renamed), "Drawings")

	def test_theLogStillHasItsEnglishSentence(self):
		from brlMultiline import keyLayers

		chart = keyLayers.newLayer(keyLayers.MONARCH, "chart", "Chart", fallsThrough="graphics")
		graphics = keyLayers.newLayer(keyLayers.MONARCH, "graphics", "Graphics", fallsThrough="chart")
		layers = keyLayers.LayerSet(keyLayers.MONARCH, (graphics, chart))
		self.assertEqual(layers.issues()[0].kind, keyLayers.CIRCLE)
		self.assertIn("falls through in a circle", layers.problems()[0])
