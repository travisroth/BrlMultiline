# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Regressions from the review of commit 31ee6a9.

Written by the reviewer as a standalone script, and kept here as an ordinary test module so that
it runs with the rest. Each test reproduces one finding in reading an application window and
moving to its areas: hidden subtrees spending the collection budget, a failed read passed off as
an empty subtree, a move carried out after the window in front changed, and a recycled window
handle accepted as the area's.
"""

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from . import test_appCollect as harness

appCollect = harness.appCollect


class CollectorRegressions(unittest.TestCase):
	def setUp(self):
		harness._stubs.callAfterQueue.discard()
		harness.FakeElement.asked = []

	def gather(self, root, allow_failure=False):
		got = []
		window = SimpleNamespace(windowHandle=5, location=(0, 0, 1000, 700))
		with mock.patch.dict(sys.modules, {"UIAHandler": harness.fakeUIA(root)}):
			harness.run(
				appCollect.AppCollector(window, None, lambda payload, why: got.append((payload, why)))
			)
		self.assertEqual(len(got), 1)
		if allow_failure:
			return got[0]
		self.assertIsNotNone(got[0][0], got)
		return got[0][0]

	def test_hidden_subtrees_do_not_spend_the_budget_before_visible_areas(self):
		for hidden_kind in ("offscreen", "outside"):
			with self.subTest(hidden=hidden_kind):
				hidden = harness.FakeElement(
					"Pane",
					(0, 0, 500, 600),
					"Hidden",
					[
						harness.FakeElement("Button", (10, 10 + i * 15, 100, 15), f"Hidden {i}")
						for i in range(appCollect.MAX_NODES + 20)
					],
				)
				if hidden_kind == "offscreen":
					hidden.props[7] = True
				else:
					hidden.props[6] = (2000.0, 2000.0, 500.0, 600.0)
				visible = harness.FakeElement(
					"Pane",
					(500, 0, 500, 600),
					"Visible main",
					[harness.FakeElement("Button", (520, 20, 100, 30), "Work")],
				)
				root = harness.FakeElement("Window", (0, 0, 1000, 700), "App", [hidden, visible])
				payload = self.gather(root)
				page = harness.findAppRegions(payload["window"], payload["windowRect"])
				self.assertTrue(
					any(r.label == "Visible main" for r in page.walk()),
					"Invisible descendants exhausted the node budget and hid the visible work area.",
				)

	def test_a_failed_subtree_read_marks_the_result_incomplete(self):
		pane = harness.FakeElement(
			"Pane", (0, 0, 1000, 600), "Options", [harness.FakeElement("Button", (20, 20, 100, 30), "Work")]
		)
		pane.BuildUpdatedCache = mock.Mock(side_effect=RuntimeError("provider disconnected"))
		root = harness.FakeElement("Window", (0, 0, 1000, 700), "App", [pane])
		payload, reason = self.gather(root, allow_failure=True)
		if payload is None:
			self.assertTrue(reason, "An overall read failure must have a reason.")
		else:
			self.assertTrue(
				payload["incomplete"], "A failed child read was presented as a complete empty subtree."
			)


class NavigationRegressions(unittest.TestCase):
	def setUp(self):
		self.fixture = harness.TestMovingToAnAreaOfAWindow()
		self.fixture.setUp()
		self.addCleanup(self.fixture.doCleanups)

	def test_a_window_switch_during_lookup_cancels_the_focus_move(self):
		fixture = self.fixture
		target = harness.FakeFound()

		def lookup(hwnd, runtime_id):
			# A slow cross-process lookup gives the user time to Alt+Tab.
			fixture.front = SimpleNamespace(windowHandle=6)
			return harness.FakeFound()

		with (
			mock.patch.object(appCollect, "findElement", side_effect=lookup),
			mock.patch.object(appCollect, "focusableWithin", return_value=target),
		):
			fixture.plugin._moveToAppArea(fixture.region, (5,))
		self.assertEqual(
			target.focused, 0, "The old application's area was focused after the foreground changed."
		)
		self.assertTrue(fixture.mode.active, "A refused stale move should not tear down the drawing.")

	def test_a_reused_fallback_handle_outside_the_original_window_is_refused(self):
		fixture = self.fixture
		fixture.region.record = {"hwnd": 77}
		foreign = harness.FakeFound()
		foreign.CurrentNativeWindowHandle = 77
		foreign.GetRuntimeId = lambda: [42, 6, 77]
		target = harness.FakeFound()
		# Handle 77 is valid again, but now belongs to top-level window 6,
		# not to the original window 5 whose overview is still being read.
		user32 = SimpleNamespace(
			IsWindow=lambda hwnd: hwnd in (5, 6, 77),
			IsChild=lambda parent, child: parent == 6 and child == 77,
			GetAncestor=lambda hwnd, flags: 6 if hwnd == 77 else hwnd,
			GetForegroundWindow=lambda: 5,
		)
		uia = harness.fakeUIA(None)
		uia.handler.clientObject.ElementFromHandle = lambda hwnd: foreign if hwnd == 77 else None
		with (
			mock.patch.dict(
				sys.modules,
				{
					"UIAHandler": uia,
					"winUser": SimpleNamespace(
						isDescendantWindow=lambda parent, child: parent == child
						or user32.IsChild(parent, child)
					),
				},
			),
			mock.patch.object(appCollect.ctypes, "windll", SimpleNamespace(user32=user32), create=True),
			mock.patch.object(appCollect, "findElement", return_value=None),
			mock.patch.object(appCollect, "focusableWithin", return_value=target),
		):
			fixture.plugin._moveToAppArea(fixture.region, (5,))
		self.assertEqual(
			target.focused, 0, "IsWindow accepted a recycled handle in another application's window."
		)
