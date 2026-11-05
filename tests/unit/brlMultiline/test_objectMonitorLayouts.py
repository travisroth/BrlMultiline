# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for a pinned table being read with the layout the reader made for it.

A layout is work the reader did to make a table readable, so a pin that ignores it shows them
the table they had already fixed. The columns kept are what these tests look at, since that is
what a layout most visibly changes.
"""

from unittest import mock

from ._stubs import FakeNavigatorObject, FakeTableDocument, installStubs

installStubs()

from brlMultiline import flowTableLayouts, flowTableSource  # noqa: E402

from .test_objectMonitor import PINNED_KEY, PINNED_SEGMENT, TABLE, MonitorTestCase  # noqa: E402
from ._stubs import CONFIG  # noqa: E402


class PinnedLayoutTestCase(MonitorTestCase):
	segmentCount = 2

	def setUp(self):
		super().setUp()
		CONFIG["focusSegment"] = 0
		self.plugin.rebuildBuffer()
		self.document = FakeTableDocument([list(line) for line in TABLE], row=2, col=1)
		self.document.documentConstantIdentifier = "https://example.com/watchlist"
		self.obj = FakeNavigatorObject("a page", treeInterceptor=self.document)
		self.handle = flowTableSource.tableAt(self.obj)
		import api

		api.getNavigatorObject = lambda: self.obj

	def save(self, **fields):
		flowTableLayouts.remember(self.handle, flowTableLayouts.TableLayout(**fields))

	def pin(self):
		self.plugin.startMonitoring(PINNED_SEGMENT)
		return self.plugin._monitors[PINNED_KEY]

	def drawn(self, monitor):
		plan = monitor.controller.renderer.columnPlan
		return [column.index for column in plan.columns]


class TestASavedLayout(PinnedLayoutTestCase):
	def test_aPinInColumnsUsesIt(self):
		self.save(columns=(1, 3))
		self.plugin.tableRequest.wanted = self.handle.key
		self.assertEqual(self.drawn(self.pin()), [1, 3])

	def test_itIsUsedWithNoRequest(self):
		"""A display with no band never asked, so there is no request to find."""
		self.save(columns=(1, 3))
		self.plugin.tableRequest.wanted = None
		monitor = self.pin()
		self.assertTrue(monitor.wantsColumns)
		self.assertEqual(self.drawn(monitor), [1, 3])

	def test_itSurvivesTheRequestBeingDropped(self):
		self.save(columns=(1, 3))
		self.plugin.tableRequest.wanted = self.handle.key
		monitor = self.pin()
		self.plugin.tableRequest.forget()
		monitor._dropFlow(monitor._segment())
		monitor.refresh()
		self.assertEqual(self.drawn(monitor), [1, 3])

	def test_aLayoutTheReaderHasJustRefusedIsNotUsed(self):
		self.save(columns=(1, 3))
		self.plugin.tableRequest.wanted = None
		self.plugin.tableRequest.refused = self.handle.key
		self.assertFalse(self.pin().wantsColumns)

	def test_aLayoutForSpeechOnlyAsksForNoColumns(self):
		flowTableLayouts.rememberHeaderSpeech(self.handle, flowTableLayouts.SPEAK_OFF)
		self.plugin.tableRequest.wanted = None
		self.assertFalse(self.pin().wantsColumns)

	def test_aTableWithNoLayoutIsPinnedInOrder(self):
		self.plugin.tableRequest.wanted = None
		self.assertFalse(self.pin().wantsColumns)

	def test_aTableWithNoLayoutInColumnsShowsEveryColumn(self):
		self.plugin.tableRequest.wanted = self.handle.key
		self.assertEqual(len(self.drawn(self.pin())), len(TABLE[0]))

	def test_aFailureLookingForALayoutPinsInOrder(self):
		self.save(columns=(1, 3))
		self.plugin.tableRequest.wanted = None
		with mock.patch.object(flowTableLayouts, "layoutFor", side_effect=RuntimeError):
			monitor = ObjectMonitorCheck.madeWith(self.plugin, self.obj)
		self.assertFalse(monitor.wantsColumns)

	def test_turningColumnsOnForAPinUsesIt(self):
		self.plugin.tableRequest.wanted = None
		monitor = self.pin()
		self.save(columns=(1, 3))
		self.plugin.tableRequest.wanted = self.handle.key
		self.plugin.layOutPinnedTables(True, self.obj)
		self.assertEqual(self.drawn(monitor), [1, 3])


class ObjectMonitorCheck:
	"""Asks only the question a pin is asked as it is made, away from drawing it."""

	@staticmethod
	def madeWith(plugin, obj):
		from brlMultiline.objectMonitor import ObjectMonitor

		monitor = ObjectMonitor(obj, PINNED_KEY)
		plugin._layOutPin(monitor, obj)
		return monitor


class TestAnArrangedLayout(PinnedLayoutTestCase):
	def arrange(self, **fields):
		self.plugin.tableRequest.wanted = self.handle.key
		self.plugin.tableRequest.layout = flowTableLayouts.TableLayout(**fields)

	def test_itIsUsedEvenThoughItWasNeverSaved(self):
		self.arrange(columns=(2, 3))
		self.assertEqual(self.drawn(self.pin()), [2, 3])

	def test_itComesBeforeASavedOne(self):
		self.save(columns=(1, 3))
		self.arrange(columns=(2, 3))
		self.assertEqual(self.drawn(self.pin()), [2, 3])

	def test_itOutlivesTheRequest(self):
		self.arrange(columns=(2, 3))
		monitor = self.pin()
		self.plugin.tableRequest.forget()
		monitor._dropFlow(monitor._segment())
		monitor.refresh()
		self.assertEqual(self.drawn(monitor), [2, 3])

	def test_itIsNotAppliedToAnotherTable(self):
		self.arrange(columns=(2, 3))
		monitor = self.pin()
		self.document.tableID = 2
		monitor._dropFlow(monitor._segment())
		monitor.refresh()
		self.assertEqual(len(self.drawn(monitor)), len(TABLE[0]))
