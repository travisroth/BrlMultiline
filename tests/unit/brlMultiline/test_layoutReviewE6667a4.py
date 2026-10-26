# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Regressions from the review of commit e6667a4.

Written by the reviewer as a standalone script, and kept here as an ordinary test module so that
it runs with the rest. Besides a check that key letters stay with their areas, each test
reproduces one finding: counts stacked over a bottom label overlapping, controls marked outside
their container still counted, a large plain edit control left out of an application's layout,
and the tab count's word not following NVDA's localized role name.
"""

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from . import test_appRegions as appHarness
from . import test_layoutDraw as harness
from brlMultiline import layoutDraw as drawing  # noqa: E402

Box, Region = harness.Box, harness.Region


class LayoutOverviewRegressions(unittest.TestCase):
	def setUp(self):
		harness._stubs.callAfterQueue.discard()

	def test_key_letters_remain_stable_by_region_identity(self):
		fixture = harness.TestDrillingInAndBackingOut()
		fixture.setUp()
		self.addCleanup(fixture.doCleanups)
		given = dict(fixture.state.keys)
		original = fixture.mode.source.note
		fixture.pressOn("main")
		fixture.plugin.script_layoutDrillIn(None)
		self.assertTrue(given)
		self.assertEqual({k: fixture.state.keys[k] for k in given}, given)
		fixture.plugin.script_layoutBackOut(None)
		self.assertEqual(fixture.mode.source.note, original)

	def test_a_following_count_does_not_overlap_a_stacked_number_above_a_bottom_label(self):
		region = Region("area", "", Box(0, 0, 1000, 800), "geometry")
		box = drawing.DrawnBox(region, Box(0, 0, 21, 40), 1, key="a", labelAt=Box(2, 34, 2, 4))
		# Four digits plus a numeric indicator, followed by one digit plus
		# its indicator. Real shapes from the shipped glyph vocabulary.
		from brlMultiline import glyphs

		counts = [
			drawing._Count(
				"1000 fields", [0x3C, 0x01, 0x1A, 0x1A, 0x1A], shape=glyphs.patternRows(glyphs.EDIT.dots)
			),
			drawing._Count("1 button", [0x3C, 0x01], shape=glyphs.patternRows(glyphs.BUTTON.dots)),
		]
		drawing._placeDetails(box, counts)
		self.assertEqual(len(box.details), 2, "This fixture must place both counts.")
		number_marks = []
		for text, _, marks in box.details:
			for left, top, ink in marks:
				width = len(ink[0]) if isinstance(ink[0], str) else len(ink) * 3 - 1
				number_marks.append((text, Box(left, top, width, 4)))
		collisions = [
			(a_name, b_name, a.intersection(b))
			for i, (a_name, a) in enumerate(number_marks)
			for b_name, b in number_marks[i + 1 :]
			if a_name != b_name and a.intersection(b)
		]
		self.assertEqual(
			collisions, [], "The cursor followed the lower word while the next line is the upper number."
		)

	def test_application_counts_exclude_controls_marked_outside(self):
		button = appHarness.element
		pane = button(
			"Pane",
			(0, 0, 400, 300),
			"Options",
			children=[
				button("Button", (20, 20, 100, 40), "Visible"),
				# The probe removes rect and sets outside when an element falls
				# entirely outside the rectangle of its containing area.
				{
					"type": "Button",
					"name": "Outside",
					"outside": True,
					"fullRect": [500, 20, 100, 40],
					"children": [],
				},
			],
		)
		page = appHarness.findAppRegions(appHarness.window(pane))
		self.assertEqual(
			page.children[0].buttons, 1, "An invisible outside control inflated the area's count."
		)

	def test_a_large_plain_edit_control_is_not_lost_from_the_application_layout(self):
		edit = appHarness.element(
			"Edit", (0, 40, 1000, 620), className="Edit", focusable=True, runtimeId=[42, 99]
		)
		page = appHarness.findAppRegions(
			appHarness.window(
				appHarness.element("MenuBar", (0, 0, 1000, 40), items={"MenuItem": 3}),
				edit,
				appHarness.element("StatusBar", (0, 660, 1000, 40)),
			),
			focusPath=[[42, 99]],
		)
		self.assertTrue(
			any(r.record is edit for r in page.walk()),
			"The application has only menu/status bars in its layout; its main editor is missing.",
		)

	def test_tab_word_falls_back_to_nvdas_localized_role_name(self):
		from . import test_plugin

		module = sys.modules["brlMultiline.layoutOverview"]
		roles = SimpleNamespace(
			**{
				name: SimpleNamespace(name=name, displayString="onglet" if name == "TAB" else name.lower())
				for name in ("LINK", "BUTTON", "EDITABLETEXT", "TAB", "SECTION")
			}
		)
		# NVDA has no TAB entry in braille.labels.roleLabels; its own
		# braille property renderer uses Role.TAB.displayString instead.
		with (
			mock.patch.object(sys.modules["controlTypes"], "Role", roles),
			mock.patch.object(module, "findSurface", return_value=None),
		):
			style = module.countStyle()
		self.assertEqual(
			style.words["tabs"], "onglet", "The fallback stayed English instead of matching NVDA."
		)
