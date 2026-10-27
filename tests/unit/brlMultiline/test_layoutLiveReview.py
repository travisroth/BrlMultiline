# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Regressions from the review of the live collection and drawing, at commit 7808507.

Written by the reviewer as a standalone script, and kept here as an ordinary test module so that
it runs with the rest. Each test reproduces one finding: tabs counted as fields, a plain form
role, counts lost from blocks beside landmarks, tabs separated by plain wrappers, a form hidden
under a sticky header drawn as visible, and an old drawing moving into a replaced buffer.
"""

import unittest

from . import test_layoutDraw as harness
from brlMultiline import layoutRegions as rules  # noqa: E402

Box, Node, Region = rules.Box, rules.Node, rules.Region
VIEW = Box(0, 0, 1000, 800)


class LayoutOverviewLiveRegressions(unittest.TestCase):
	def setUp(self):
		harness._stubs.callAfterQueue.discard()

	def test_nvda_tabs_in_formfield_results_are_not_counted_as_input_fields(self):
		# NVDA gecko_ia2._searchableAttribsForNodeType("formField") includes PAGETAB.
		main = harness.FakeItem(
			(1, 1),
			0,
			1000,
			harness.FakeObject("LANDMARK", (0, 100, 1000, 800), attributes={"xml-roles": "main"}),
		)
		tabs = [
			harness.FakeItem(
				(1, 40 + i),
				10 + i,
				11 + i,
				harness.FakeObject("TAB", (100 + 130 * i, 250, 120, 40)),
			)
			for i in range(3)
		]
		document = harness.FakeDocument({"landmark": [main], "tab": tabs, "formField": tabs})
		got = []
		harness.run(harness.Collector(document, lambda payload, why: got.append(payload)))
		record = next(r for r in got[0]["candidates"]["records"] if r["type"] == "landmark")
		self.assertEqual(record["tabs"], 3)
		self.assertEqual(record["fields"], 0, "The same three tabs were also called three input fields.")

	def test_a_plain_form_role_with_three_fields_is_recognized_without_xmlroles(self):
		fields = [Node("editabletext", Box(100, 150 + 80 * i, 300, 40)) for i in range(3)]
		form = Node("form", Box(80, 100, 500, 400), tag="form", children=fields)
		nav = Node("landmark", Box(0, 0, 1000, 60), landmark="navigation", links=5)
		page = rules.findRegions(Node("document", children=[nav, form]), VIEW, scroll=0)
		found = next(r for r in page.walk() if r.node is form)
		self.assertEqual(found.fields, 3)
		self.assertEqual(
			found.kind, "form", "The authoritative form role was ignored without xml-roles=form.",
		)

	def test_uncovered_blocks_keep_their_sampled_field_and_button_counts(self):
		nav = Node("landmark", Box(0, 0, 1000, 60), landmark="navigation", links=5)
		leaves = [
			Node("editabletext" if i < 6 else "button", Box(100, 100 + 75 * i, 600, 75), start=100 + i)
			for i in range(8)
		]
		page = rules.findRegions(Node("document", children=[nav, *leaves]), VIEW, scroll=0)
		uncovered = [r for r in page.children if r.kind != "navigation"]
		self.assertEqual(len(uncovered), 1)
		self.assertEqual(
			(uncovered[0].fields, uncovered[0].buttons),
			(6, 2),
			"The no-gutter fallback created a block but discarded every control count.",
		)

	def test_tabs_in_plain_wrappers_are_still_grouped(self):
		items = [
			Node(
				"listitem",
				Box(100 + 130 * i, 200, 120, 40),
				children=[
					Node("tab", Box(100 + 130 * i, 200, 120, 40), start=10 + i),
				],
			)
			for i in range(4)
		]
		# The accessible-tree fallback preserves these wrappers on a page
		# without real landmarks; an id can still identify its main area.
		main = Node("section", Box(0, 0, 1000, 800), id="main", children=items)
		page = rules.findRegions(Node("document", children=[main]), VIEW, scroll=0)
		strips = [r for r in page.walk() if r.kind == "tabs"]
		self.assertEqual(len(strips), 1, "Each plain wrapper prevents its tab joining the other tabs.")
		self.assertEqual(strips[0].tabs, 4)

	def test_a_form_completely_covered_by_a_sticky_header_is_not_drawn_on_screen(self):
		banner = Region(
			"banner",
			"",
			Box(0, 35, 1000, 100),
			"landmark",
			screenBox=Box(0, 0, 1000, 100),
		)
		form = Region(
			"form",
			"Covered form",
			Box(0, 520, 1000, 50),
			"landmark",
			screenBox=Box(0, 20, 1000, 50),
		)
		main = Region(
			"main",
			"",
			Box(0, 135, 1000, 1500),
			"landmark",
			children=[form],
			screenBox=Box(0, -365, 1000, 1500),
		)
		page = Region("page", "", Box(0, 0, 1000, 1635), "page", children=[banner, main], scroll=500)
		on_page = harness.compose(
			harness.PinBuffer,
			page,
			96,
			100,
			"page",
			VIEW,
			harness.translate,
			3,
		)
		self.assertIn(form, [b.region for b in on_page.walk()])
		on_screen = harness.composeFitting(
			harness.PinBuffer,
			page,
			96,
			40,
			"screen",
			VIEW,
			harness.translate,
			3,
		)
		self.assertNotIn(
			form,
			[b.region for b in on_screen.walk()],
			"The parent was clipped below the sticky header, but its occluded child was enlarged to fill it.",
		)

	def test_a_new_buffer_in_the_same_document_cannot_use_old_offset_anchors(self):
		fixture = harness.TestMovingToAnArea()
		fixture.setUp()
		self.addCleanup(fixture.doCleanups)
		fixture.plugin.graphicsMode.newBuffer = harness.PinBuffer
		fixture.document.documentConstantIdentifier = "https://example.com/old"
		fixture.document.VBufHandle = 1
		page = Region(
			"page",
			"",
			VIEW,
			"page",
			children=[
				Region("main", "Original area", VIEW, "landmark", start=120),
			],
		)
		drawing = fixture.plugin.layouts.drawingFor(
			page,
			"screen",
			VIEW,
			(96, 32),
			"layout",
			fixture.document,
		)
		# A refresh or SPA transition can keep the treeInterceptor object while
		# replacing its buffer/content. The old integer offset remains valid,
		# but now belongs to unrelated content, so range checks cannot help.
		fixture.document.documentConstantIdentifier = "https://example.com/new"
		fixture.document.VBufHandle = 2
		drawing.activateAt(5, 20)
		self.assertEqual(
			fixture.document.selections,
			[],
			"The stale drawing navigated to offset 120 in a replacement buffer on the same document object.",
		)


if __name__ == "__main__":
	unittest.main()
