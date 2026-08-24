# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the command that lays a table out on the band, and takes the layout away.

What is tested here is the *state*, which is the part a reader cannot see and the part that
goes wrong: a layout that outlives the table it was made for is a layout of something that is
not there, and the reader has no way to tell that is what they are feeling. Easy Table
Navigator clears its key bindings on every focus change for exactly this reason, and this is
the same discipline in the same place.
"""

import unittest
from types import SimpleNamespace

from ._stubs import (
	CONFIG,
	FakeNavigatorObject,
	FakeTableDocument,
	NoTableDocument,
	installStubs,
	resetConfig,
	spokenMessages,
)

installStubs()

from brlMultiline.flowTableSource import TableFlowSource  # noqa: E402

from .test_flowSegment import FakeHandler, FakePlugin, containerWithBand  # noqa: E402

ROWS = 8
COLS = 32

WATCHLIST = [
	["Symbol", "Last", "Change", "%Chg"],
	["AAPL", "182.50", "+1.25", "+0.7%"],
	["F", "9.10", "-0.05", "-0.5%"],
	["BRK.B", "402.15", "+3.40", "+0.9%"],
]


class TableBandTestCase(unittest.TestCase):
	"""The harness. No tests of its own; the classes below are the tests."""

	def setUp(self):
		import api
		import braille

		from brlMultiline.flowBand import FlowBand

		resetConfig()
		self.addCleanup(resetConfig)
		CONFIG["flowEnabled"] = True
		self.handler = FakeHandler(ROWS, COLS)
		self.addCleanup(setattr, braille, "handler", braille.handler)
		braille.handler = self.handler
		self.container = containerWithBand(self.handler, numRows=ROWS, numCols=COLS)
		self.handler.mainBuffer = self.handler.buffer = self.container
		self.band = FlowBand(FakePlugin(self.container))
		self.api = api
		self.addCleanup(setattr, api, "getFocusObject", api.getFocusObject)

	def _inTable(self, rows=None, tableID=1, row=2, col=1):
		document = FakeTableDocument(
			[list(line) for line in (rows or WATCHLIST)],
			tableID=tableID,
			row=row,
			col=col,
		)
		obj = FakeNavigatorObject("a page", treeInterceptor=document)
		self.api.getFocusObject = lambda: obj
		self.band._follow()
		return obj, document

	def _elsewhere(self):
		obj = FakeNavigatorObject("a page", treeInterceptor=NoTableDocument([["a"]]))
		self.api.getFocusObject = lambda: obj
		return obj

	def _readingATable(self):
		source = getattr(self.band.controller, "source", None)
		return isinstance(source, TableFlowSource)


class TestTheCommandItself(TableBandTestCase):
	"""The script, run rather than reasoned about.

	Everything below this class tests the band. Nothing tested the twenty lines between the
	reader's key press and the band, and those twenty lines shipped twice with a fault in
	them: once with a decorator that belonged to the command below it, and once calling a
	property as though it were a method. Both are the kind of mistake that reading the code
	does not catch and running it does.
	"""

	def _press(self):
		"""Run the command the way NVDA does, on a plugin that has this band."""
		from brlMultiline import GlobalPlugin

		spokenMessages.clear()
		holder = SimpleNamespace(flowBand=self.band)
		GlobalPlugin.script_flowTableColumns(holder, None)
		return list(spokenMessages)

	def test_pressingItInATableLaysItOut(self):
		self._inTable()
		said = self._press()
		self.assertTrue(self._readingATable())
		self.assertTrue(any("columns" in message for message in said))

	def test_pressingItAgainTakesTheLayoutAway(self):
		self._inTable()
		self._press()
		said = self._press()
		self.assertFalse(self._readingATable())
		self.assertTrue(any("off" in message for message in said))

	def test_pressingItOutsideATableSaysSo(self):
		self._elsewhere()
		said = self._press()
		self.assertIn("Not in a table", said)

	def test_pressingItWithNoBandSaysSo(self):
		from brlMultiline import GlobalPlugin

		spokenMessages.clear()
		GlobalPlugin.script_flowTableColumns(SimpleNamespace(flowBand=None), None)
		self.assertTrue(spokenMessages)

	def test_theBandNeedsSomewhereToDrawRatherThanAFlowAlreadyOnIt(self):
		"""A band with no flow on it is the case the reader wants this for: a table in a
		document whose kind of content the flow settings have turned off is still a table
		they can ask for by name."""
		self._inTable()
		self.band.controller = None
		self.assertFalse(self.band.isShowing)
		self.assertTrue(self.band.isClaimed)
		self._press()
		self.assertTrue(self._readingATable())


class TestTurningItOn(TableBandTestCase):
	def test_aTableIsLaidOutWhenAskedFor(self):
		self._inTable()
		self.assertTrue(self.band.layOutTable())
		self.assertTrue(self._readingATable())

	def test_theColumnsAreThePlanTheTableAskedFor(self):
		self._inTable()
		self.band.layOutTable()
		plan = self.band.controller.renderer.columnPlan
		self.assertEqual([column.index for column in plan.columns], [1, 2, 3, 4])

	def test_theRowTheReaderIsOnIsTheFirstBlock(self):
		self._inTable(row=3)
		self.band.layOutTable()
		self.assertEqual(self.band.controller.window.blocks[0].blockId.bookmark, 3)

	def test_askingOutsideATableDoesNothing(self):
		self._elsewhere()
		self.assertFalse(self.band.layOutTable())
		self.assertFalse(self._readingATable())

	def test_askingOutsideATableLeavesNoRequestBehind(self):
		"""A request that survived a refusal would lay out the next table the reader walked
		into, which they never asked for."""
		self._elsewhere()
		self.band.layOutTable()
		self.assertIsNone(self.band.tableWanted)


class TestTurningItOff(TableBandTestCase):
	def test_theLayoutIsGivenBack(self):
		self._inTable()
		self.band.layOutTable()
		self.assertTrue(self.band.clearTable())
		self.assertFalse(self._readingATable())

	def test_clearingWhenNothingIsLaidOutSaysSo(self):
		self._inTable()
		self.assertFalse(self.band.clearTable())


class TestTheLayoutDoesNotOutliveTheTable(TableBandTestCase):
	"""The state a reader cannot see, and the one that goes wrong."""

	def test_movingWithinTheTableKeepsTheLayout(self):
		obj, document = self._inTable(row=2)
		self.band.layOutTable()
		document.row = 3
		self.band.showObject(obj)
		self.assertTrue(self._readingATable())
		self.assertEqual(self.band.controller.source.row, 3)

	def test_movingWithinTheTableDoesNotRebuildThePlan(self):
		"""A column that moves is a column the reader has to find again, and they would move
		on every keystroke if the plan were remade on each one."""
		obj, document = self._inTable(row=2)
		self.band.layOutTable()
		plan = self.band.controller.renderer.columnPlan
		document.row = 3
		self.band.showObject(obj)
		self.assertIs(self.band.controller.renderer.columnPlan, plan)

	def test_leavingTheTableDropsTheLayout(self):
		self._inTable()
		self.band.layOutTable()
		self.band.showObject(self._elsewhere())
		self.assertIsNone(self.band.tableWanted)
		self.assertFalse(self._readingATable())

	def test_anotherTableIsNotTheOneThatWasAskedFor(self):
		"""Two tables on one page. The reader asked for this one."""
		self._inTable(tableID=1)
		self.band.layOutTable()
		other = FakeNavigatorObject(
			"a page",
			treeInterceptor=FakeTableDocument(
				[list(line) for line in WATCHLIST],
				tableID=2,
			),
		)
		self.api.getFocusObject = lambda: other
		self.band.showObject(other)
		self.assertIsNone(self.band.tableWanted)
		self.assertFalse(self._readingATable())
