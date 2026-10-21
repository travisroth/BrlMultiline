# BrlMultiline: regressions from the review of 64197a4.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The five faults the review of 64197a4 found, each by the reviewer's reproduction.

Taken from the reviewer's `review_regressions_64197a4.py`, which failed on 64197a4 and passes
with the fixes. No NVDA, hardware or real mouse input; the clock is simulated. See "Review of
64197a4" in `docs/design/app-layouts-plan.md`.
"""

import ctypes
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from . import test_monitorRouting as routingHarness
from . import test_plugin as harness

from brlMultiline import appLayoutHost, monitorRouting  # noqa: E402

import api  # noqa: E402


class LayoutCase(harness.PluginTestCase):
	def setUp(self):
		super().setUp()
		harness.CONFIG["appLayouts"] = True
		self.messages = harness.FakeNavigatorObject("Messages", role="list")
		self.app = harness.ScriptedAppModule(harness.chatLayout(self.messages))
		self.focus = harness.focusIn(self.app)
		focusPatch = mock.patch.object(api, "getFocusObject", return_value=self.focus)
		focusPatch.start()
		self.addCleanup(focusPatch.stop)
		self.host = self.plugin.appLayouts

	def show(self):
		self.plugin.event_gainFocus(self.focus, lambda: None)
		harness.callLaterQueue.fire()
		self.assertTrue(self.host.isUp)

	def threePanes(self):
		self.app.answer = {
			"name": "three panes",
			"segments": [
				{"size": 1, "show": harness.FakeNavigatorObject(f"Pane {index}")} for index in range(3)
			]
			+ [{"show": "focus"}],
		}
		self.show()


class TestSlicedPassesFinish(LayoutCase):
	"""Finding 2: a pass that ran out started another rotation rather than finishing."""

	def test_oneChangeStopsSchedulingAfterAWholePass(self):
		self.threePanes()
		clock = [1000.0]
		self.host._resolvedAt = clock[0]

		def read(reveal=False):
			clock[0] += 0.04

		for monitor in self.host.liveMonitors().values():
			monitor.refresh = read
		with mock.patch.object(appLayoutHost.time, "monotonic", side_effect=lambda: clock[0]):
			self.plugin.brlMultilineContentChanged(self.app)
			for _cycle in range(6):
				harness.callLaterQueue.fire()
		self.assertIsNone(self.host._changeTimer)

	def test_aSlicedPassReadsEachPaneOnceAndEnds(self):
		self.threePanes()
		clock = [1000.0]
		reads = []
		self.host._resolvedAt = clock[0]

		def read(index):
			reads.append(index)
			clock[0] += 0.04

		for index, monitor in enumerate(self.host.liveMonitors().values()):
			monitor.refresh = lambda reveal=False, index=index: read(index)
		with mock.patch.object(appLayoutHost.time, "monotonic", side_effect=lambda: clock[0]):
			unfinished = [self.plugin.refreshAppLayoutInTurn(0.03) for _ in range(4)]
		self.assertEqual(unfinished[:3], [True, True, False])
		self.assertEqual(reads[:3], [0, 1, 2])

	def test_aSlowLayoutDoesNotStarveTheReadersPins(self):
		self.pin(0, "Clock")
		self.show()
		clock = [1000.0]
		pinReads = []
		self.host._resolvedAt = clock[0]

		def slowLayout(reveal=False):
			clock[0] += 0.04

		self.host.liveMonitors()["appLayout.0"].refresh = slowLayout
		self.plugin._monitors["display.0"].refresh = lambda reveal=False: pinReads.append(True)
		with mock.patch.object(appLayoutHost.time, "monotonic", side_effect=lambda: clock[0]):
			for _cycle in range(5):
				self.plugin.refreshMonitorsInTurn(0.03)
		self.assertTrue(pinReads)


class TestTheKeyShownIsKept(LayoutCase):
	"""Finding 3: a key changed on the same Python object was read off it on both sides."""

	def test_aKeyChangedInPlaceReplacesTheMonitor(self):
		self.messages.brlMultilineShowKey = "message A"
		self.app.answer = harness.chatLayout(lambda: self.messages)
		self.show()
		old = self.host.liveMonitors()["appLayout.0"]
		self.messages.brlMultilineShowKey = "message B"
		self.host.resolve()
		self.assertIsNot(self.host.liveMonitors()["appLayout.0"], old)


class TestTheFlowTurnsOverInALayout(LayoutCase):
	"""Finding 5: the flow setting was not answered while a layout asking for it was up."""

	def mailLayout(self):
		self.app.answer = {
			"name": "mail",
			"segments": [{"size": 3, "show": "focus", "flow": True}, {"show": self.messages}],
		}
		self.show()

	def test_turningTheFlowOnStartsItInTheLayout(self):
		self.mailLayout()
		self.assertIsNone(self.plugin.flowBand)
		self.plugin.script_toggleFlow(None)
		self.assertTrue(harness.bmConfig.isFlowEnabled())
		self.assertIsNotNone(self.plugin.flowBand)
		self.assertIs(self.plugin.flowBand.segment(), self.plugin.container.segmentForKey("appLayout.0"))

	def test_turningTheFlowOffStopsIt(self):
		self.mailLayout()
		self.plugin.script_toggleFlow(None)
		self.plugin.script_toggleFlow(None)
		self.assertFalse(harness.bmConfig.isFlowEnabled())
		self.assertIsNone(self.plugin.flowBand)
		self.assertTrue(self.host.isUp)

	def test_aLayoutNotAskingStillKeepsTheFlowWaiting(self):
		self.show()
		self.plugin.script_toggleFlow(None)
		self.assertIsNone(self.plugin.flowBand)


class TestRoutingIsSafe(routingHarness.RoutingTestCase):
	"""Findings 1 and 4: what a click is checked against, and text clicked where it is not."""

	def test_textWithoutAPointGetsItsCaretRatherThanAClick(self):
		obj = routingHarness.FakeObject()
		info = routingHarness.FakeInfo(point=None, objectAtStart=obj)
		monitorRouting.goTo(monitorRouting.Target(obj=obj, info=info))
		self.settle()
		self.assertEqual(self.desktop.clicks, [])
		self.assertTrue(info.caretPlaced)

	def test_anotherControlInTheSameWindowIsNotAHit(self):
		target = routingHarness.FakeObject(windowHandle=7)
		other = routingHarness.FakeObject(windowHandle=7)
		accessibleDesktop = SimpleNamespace(objectFromPoint=lambda x, y: other)
		with (
			mock.patch.object(
				ctypes,
				"windll",
				SimpleNamespace(user32=SimpleNamespace(WindowFromPoint=lambda point: 7)),
				create=True,
			),
			mock.patch.dict(
				sys.modules,
				{"winUser": SimpleNamespace(isDescendantWindow=lambda parent, child: parent == child)},
			),
			mock.patch.object(api, "getDesktopObject", return_value=accessibleDesktop, create=True),
		):
			self.assertFalse(monitorRouting._Desktop().isTheTargetsAt(target, (130, 210)))

	def test_theControlItselfIsAHit(self):
		target = routingHarness.FakeObject(windowHandle=7)
		accessibleDesktop = SimpleNamespace(objectFromPoint=lambda x, y: target)
		with (
			mock.patch.object(
				ctypes,
				"windll",
				SimpleNamespace(user32=SimpleNamespace(WindowFromPoint=lambda point: 7)),
				create=True,
			),
			mock.patch.dict(
				sys.modules,
				{"winUser": SimpleNamespace(isDescendantWindow=lambda parent, child: parent == child)},
			),
			mock.patch.object(api, "getDesktopObject", return_value=accessibleDesktop, create=True),
		):
			self.assertTrue(monitorRouting._Desktop().isTheTargetsAt(target, (130, 210)))


if __name__ == "__main__":
	unittest.main()
