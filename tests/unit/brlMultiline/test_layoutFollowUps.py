# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Follow-up regressions from the review of the fixes at commit 167d19e.

Written by the reviewer as a standalone script, and kept here as an ordinary test module so that
it runs with the rest. Each test reproduces one finding: a tab strip losing its first tab's
identifier, a form inside a plain wrapper lost when the wrapper was absorbed, a top level area
wholly behind a sticky header still drawn, and a key letter changing when drilling in.
"""

import re
import unittest

from . import test_layoutDraw as harness
from brlMultiline import layoutRegions as rules  # noqa: E402

Box, Node, Region = rules.Box, rules.Node, rules.Region
VIEW = Box(0, 0, 1000, 800)


class LayoutOverviewFixFollowups(unittest.TestCase):
	def setUp(self):
		harness._stubs.callAfterQueue.discard()

	def test_a_tab_strip_keeps_its_first_tabs_anchor_when_offsets_move(self):
		tabs = [
			Node("tab", Box(100 + i * 130, 200, 120, 40), start=10 + i, ident=(1, 40 + i)) for i in range(3)
		]
		main = Node("landmark", VIEW, landmark="main", start=0, ident=(1, 1), children=tabs)
		page = rules.findRegions(Node("document", children=[main]), VIEW, scroll=0)
		strip = next(r for r in page.walk() if r.kind == "tabs")
		fixture = harness.TestMovingToAnArea()
		fixture.setUp()
		self.addCleanup(fixture.doCleanups)
		document = fixture.document
		document.documentConstantIdentifier = "https://example.com/"
		document.VBufHandle = 1
		# A dynamic insertion shifts offsets without replacing the live buffer.
		document.elements[(1, 40)] = (300, 310)
		fixture.plugin.layouts.moveToArea(document, strip, ("https://example.com/", 1))
		self.assertEqual(
			document.selections,
			[((300, 300), "quicknav")],
			"The strip discarded the tab's stable identity and navigated to old offset 10.",
		)

	def test_absorbing_a_plain_wrapper_does_not_delete_the_form_inside_it(self):
		fields = [Node("editabletext", Box(140, 230 + i * 45, 400, 35)) for i in range(3)]
		form = Node("form", Box(120, 200, 560, 200), name="Contact", children=fields)
		wrapper = Node(
			"section",
			Box(100, 180, 600, 250),
			children=[
				Node("graphic", Box(100, 180, 40, 20)),
				form,
				Node("graphic", Box(660, 410, 40, 20)),
			],
		)
		headers = [
			Node("button", Box(100, y, 600, 40), expandable=True, start=10 + i)
			for i, y in enumerate((100, 560))
		]
		# A page without landmarks uses the accessible-tree fallback; a telling
		# id still names the main area and the browser exposes the plain form role.
		main = Node("section", VIEW, id="main", children=[headers[0], wrapper, headers[1]])
		page = rules.findRegions(Node("document", children=[main]), VIEW, scroll=0)
		self.assertTrue(any(r.kind == "accordion" for r in page.walk()))
		self.assertTrue(
			any(r.kind == "form" and r.label == "Contact" for r in page.walk()),
			"Removing a plain wrapper swallowed its meaningful form descendant too.",
		)

	def test_a_top_level_form_fully_behind_a_sticky_header_is_hidden(self):
		banner = Region("banner", "", Box(0, 35, 1000, 100), "landmark", screenBox=Box(0, 0, 1000, 100))
		form = Region(
			"form", "Covered form", Box(100, 520, 600, 50), "landmark", screenBox=Box(100, 20, 600, 50)
		)
		main = Region("main", "", Box(0, 700, 1000, 1200), "landmark", screenBox=Box(0, 200, 1000, 1200))
		page = Region("page", "", Box(0, 0, 1000, 1900), "page", children=[banner, form, main], scroll=500)
		on_page = harness.compose(harness.PinBuffer, page, 96, 120, "page", VIEW, harness.translate, 3)
		self.assertIn(form, [b.region for b in on_page.walk()])
		on_screen = harness.composeFitting(
			harness.PinBuffer, page, 96, 40, "screen", VIEW, harness.translate, 3
		)
		self.assertNotIn(
			form,
			[b.region for b in on_screen.walk()],
			"Only partial top overlaps are clipped; a fully covered top-level area remains drawn.",
		)

	def test_a_region_keeps_its_key_letter_when_drilling_into_its_parent(self):
		"""Followed by the area itself, not its title: Seeking Alpha has two areas called Latest
		News, the column and the block heading it, and drilled into, the column has room for its
		name and the block inside it is the one with a letter."""
		fixture = harness.TestDrillingInAndBackingOut()
		fixture.setUp()
		self.addCleanup(fixture.doCleanups)
		pattern = r"\b([a-z]), area, Latest News(?:;|$)"
		self.assertIsNotNone(
			re.search(pattern, fixture.mode.source.note),
			"The saved page must have a keyed Latest News area.",
		)
		whole = fixture.mode.source.note
		given = dict(fixture.state.keys)
		fixture.pressOn("main")
		fixture.plugin.script_layoutDrillIn(None)
		for region, letter in given.items():
			self.assertEqual(
				fixture.state.keys[region],
				letter,
				"The same area was reassigned a different letter when its parent was drilled into.",
			)
		letters = list(fixture.state.keys.values())
		self.assertEqual(len(letters), len(set(letters)), "no letter stands for two areas")
		fixture.plugin.script_layoutBackOut(None)
		self.assertEqual(fixture.mode.source.note, whole, "backed out, the same key as before")
