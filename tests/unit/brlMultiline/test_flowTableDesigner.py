# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for arranging a table's columns.

The dialog is a shell; everything that decides anything is `Arrangement`, which knows nothing
about wx. That split is what makes this file possible at all — a test run has no display —
and it is the same split `flowTable` makes against `flowRender`.
"""

import unittest

from ._stubs import installStubs, resetConfig

installStubs()

from brlMultiline import flowTable, flowTableDesigner, flowTableLayouts  # noqa: E402


def plan(columns=(1, 2, 3, 4), labels=None, excluded=()):
	""":return: a column plan of the shape the designer is opened over."""
	labels = labels or {1: "Symbol", 2: "Last", 3: "Change", 4: "%Chg"}
	return flowTable.ColumnPlan(
		columns=tuple(
			flowTable.Column(index=index, width=6, label=labels.get(index, ""))
			for index in columns
		),
		numCols=32,
		excluded=tuple(excluded),
	)


class TestWhatTheReaderOpens(unittest.TestCase):
	"""What the dialog shows is what they are feeling: the columns they can see, in the order
	they see them, and then the ones their layout leaves out."""

	def test_theColumnsAreTheOnesOnTheDisplay(self):
		arrangement = flowTableDesigner.Arrangement.of(plan())
		self.assertEqual([column.index for column in arrangement.columns], [1, 2, 3, 4])

	def test_inTheOrderTheyAreDrawn(self):
		arrangement = flowTableDesigner.Arrangement.of(plan(columns=(3, 1, 2)))
		self.assertEqual([column.index for column in arrangement.columns], [3, 1, 2])

	def test_andTheHiddenOnesAfterThem(self):
		"""So that a column the reader dropped can be put back, which is the whole reason to
		show it at all."""
		arrangement = flowTableDesigner.Arrangement.of(plan(columns=(1, 2), excluded=(3, 4)))
		self.assertEqual([column.index for column in arrangement.columns], [1, 2, 3, 4])
		self.assertFalse(arrangement.at(2).shown)

	def test_aColumnIsCalledWhatItIsCalled(self):
		arrangement = flowTableDesigner.Arrangement.of(plan())
		self.assertEqual(arrangement.at(0).name, "Symbol")

	def test_andByItsNumberWhereItHasNoHeading(self):
		"""A message list declares none, and "6" on its own is not a name."""
		arrangement = flowTableDesigner.Arrangement.of(plan(columns=(6,), labels={}))
		self.assertIn("6", arrangement.at(0).name)

	def test_whatWasDecidedBeforeIsThere(self):
		layout = flowTableLayouts.TableLayout(
			perColumn={2: flowTable.ColumnChoice(label="Price", keep=flowTable.KEEP_END)},
		)
		arrangement = flowTableDesigner.Arrangement.of(plan(), layout)
		self.assertEqual(arrangement.at(1).choice.label, "Price")


class TestTheLineForOneColumn(unittest.TestCase):
	"""Every column is one line that says everything decided about it. A list that said only
	the name would mean opening each column in turn to find the one that was changed."""

	def line(self, **changes):
		arrangement = flowTableDesigner.Arrangement.of(plan())
		arrangement.decide(0, **changes)
		return arrangement.at(0).describe()

	def test_theNameIsEnoughForAColumnNobodyHasTouched(self):
		self.assertEqual(flowTableDesigner.Arrangement.of(plan()).at(0).describe(), "Symbol")

	def test_aHiddenColumnSaysSo(self):
		arrangement = flowTableDesigner.Arrangement.of(plan())
		arrangement.toggle(0)
		self.assertIn("hidden", arrangement.at(0).describe())

	def test_soDoesOneCutAtItsEnd(self):
		said = self.line(overflow=flowTable.TRUNCATE, keep=flowTable.KEEP_END)
		self.assertIn("keeping the end", said)

	def test_andTheReadersOwnNameForIt(self):
		self.assertIn("called Ticker", self.line(label="Ticker"))

	def test_andTheRoomItWasGiven(self):
		self.assertIn("at least 12 cells", self.line(minWidth=12))

	def test_andWhereAPageBegins(self):
		self.assertIn("starts a page", self.line(startsAPage=True))


class TestArrangingIt(unittest.TestCase):
	def setUp(self):
		resetConfig()
		self.addCleanup(resetConfig)
		self.arrangement = flowTableDesigner.Arrangement.of(plan())

	def test_aColumnCanBeMovedEarlier(self):
		self.assertEqual(self.arrangement.move(2, -1), 1)
		self.assertEqual([column.index for column in self.arrangement.columns], [1, 3, 2, 4])

	def test_andNotPastTheEnds(self):
		self.assertEqual(self.arrangement.move(0, -1), 0)
		self.assertEqual(self.arrangement.move(3, 1), 3)
		self.assertEqual([column.index for column in self.arrangement.columns], [1, 2, 3, 4])

	def test_aColumnCanBeHiddenAndShown(self):
		self.assertFalse(self.arrangement.toggle(1))
		self.assertTrue(self.arrangement.toggle(1))

	def test_butNotTheLastOneShowing(self):
		"""A table of no columns is not a layout, it is a blank display."""
		for position in (1, 2, 3):
			self.arrangement.toggle(position)
		self.assertTrue(self.arrangement.toggle(0))

	def test_cuttingIsOneQuestionRatherThanTwo(self):
		"""A reader looking at a column asks "what happens when it does not fit", and which
		end only means anything under the answer to that."""
		self.arrangement.setCutting(0, flowTableDesigner.CUT_END)
		self.assertEqual(self.arrangement.at(0).choice.overflow, flowTable.TRUNCATE)
		self.assertEqual(self.arrangement.at(0).choice.keep, flowTable.KEEP_END)
		self.arrangement.setCutting(0, flowTableDesigner.WRAPPED)
		self.assertEqual(self.arrangement.at(0).choice.overflow, flowTable.WRAP)
		self.assertEqual(self.arrangement.at(0).choice.keep, "")

	def test_andComesBackOutAsTheReaderPutItIn(self):
		self.arrangement.setCutting(1, flowTableDesigner.CUT_START)
		self.assertEqual(self.arrangement.at(1).cutting, flowTableDesigner.CUT_START)


class TestWhatLeavesTheDialog(unittest.TestCase):
	"""A `TableLayout`, holding only what was decided."""

	def setUp(self):
		resetConfig()
		self.addCleanup(resetConfig)
		self.arrangement = flowTableDesigner.Arrangement.of(plan())

	def test_lookingAndPressingOkChangesNothing(self):
		"""Which is what pressing OK on a dialog nobody touched should mean."""
		self.assertTrue(self.arrangement.asLayout().isEmpty)

	def test_hidingAColumnNamesTheOnesThatStay(self):
		self.arrangement.toggle(1)
		self.assertEqual(self.arrangement.asLayout().columns, (1, 3, 4))

	def test_reorderingThemIsWrittenDownToo(self):
		self.arrangement.move(0, 1)
		self.assertEqual(self.arrangement.asLayout().columns, (2, 1, 3, 4))

	def test_aColumnsOwnDecisionsAreKeptByItsNumber(self):
		self.arrangement.decide(2, label="Delta", maxWidth=8)
		layout = self.arrangement.asLayout()
		self.assertEqual(layout.perColumn[3].label, "Delta")
		self.assertEqual(layout.perColumn[3].maxWidth, 8)

	def test_aColumnNobodyTouchedIsNotWrittenDown(self):
		self.arrangement.decide(0, label="Ticker")
		self.assertEqual(set(self.arrangement.asLayout().perColumn), {1})

	def test_theKeyColumnLeavesWithIt(self):
		self.arrangement.keyColumn = 3
		self.assertEqual(self.arrangement.asLayout().keyColumn, 3)

	def test_andTheWholeThingSurvivesTheStore(self):
		"""The record is what the layout is for: saved, read back, and the same."""
		self.arrangement.decide(1, label="Price", keep=flowTable.KEEP_END, minWidth=9)
		self.arrangement.toggle(3)
		read = flowTableLayouts.fromRecord(self.arrangement.asLayout().asRecord())
		self.assertEqual(read.columns, (1, 2, 3))
		self.assertEqual(read.perColumn[2].label, "Price")
		self.assertEqual(read.perColumn[2].keep, flowTable.KEEP_END)
		self.assertEqual(read.perColumn[2].minWidth, 9)


if __name__ == "__main__":
	unittest.main()
