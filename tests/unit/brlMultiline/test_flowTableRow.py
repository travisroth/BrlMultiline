# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for drawing one row of a table at its columns' offsets.

One cell per character, which is what the harness's regions produce, so a drawn row reads
back as the string it came from and a test can say what it expects to feel. That is also
what makes the point of this milestone visible in a test at all: the same column starts at
the same character on every row, and a test can assert that by slicing.

Fill mode throughout, so the row cutting is the add-on's own arithmetic rather than NVDA's
word wrapping, which is a hardware question.
"""

import unittest

from ._stubs import Region, installStubs

installStubs()

from brlMultiline.flow import NO_POSITION, BlockId, SourceBlock  # noqa: E402
from brlMultiline.flowIndent import planFor as indentPlanFor  # noqa: E402
from brlMultiline.flowRender import FlowRenderer  # noqa: E402
from brlMultiline.flowTable import (  # noqa: E402
	READING_ORDER,
	Column,
	ColumnPlan,
	Measurement,
	RowCell,
	TableRow,
	planFor,
	positionParts,
	rowCellsOf,
)

BAND = 32


class FakeHandler:
	def __init__(self):
		self.buffer = None


WATCHLIST = ["Symbol", "Last", "Change", "%Chg"]


def row(*values) -> TableRow:
	""":return: one row of the watchlist, one cell per value."""
	return TableRow([RowCell(index=n, region=Region(text)) for n, text in enumerate(values, 1)])


def block(rowRegion, name="r") -> SourceBlock:
	return SourceBlock(blockId=BlockId(generation=1, bookmark=name, unit="row"), region=rowRegion)


def watchlistPlan(numCols=BAND, maxRows=1) -> ColumnPlan:
	measured = [
		Measurement(index=1, width=6, label="Symbol"),
		Measurement(index=2, width=7, label="Last"),
		Measurement(index=3, width=7, label="Change"),
		Measurement(index=4, width=6, label="%Chg"),
	]
	return planFor(measured, numCols, maxRows=maxRows)


def renderer(plan=None, numCols=BAND) -> FlowRenderer:
	return FlowRenderer(
		FakeHandler(),
		numCols=numCols,
		fillRows=True,
		indentPlan=indentPlanFor([], numCols),
		columnPlan=watchlistPlan(numCols) if plan is None else plan,
	)


def textOf(cells) -> str:
	return "".join(" " if cell == 0 else chr(cell) for cell in cells)


class TestARowIsRecognisedAsOne(unittest.TestCase):
	"""A row knows it is a row; everything else asks and gets None."""

	def test_aTableRowSaysWhatItsCellsAre(self):
		self.assertEqual(len(rowCellsOf(row("AAPL", "1.00"))), 2)

	def test_anOrdinaryRegionIsNotATableRow(self):
		self.assertIsNone(rowCellsOf(Region("just a line")))

	def test_anEmptyRowIsNotDrawnAsOne(self):
		self.assertIsNone(rowCellsOf(TableRow([])))

	def test_aRowStillReadsAsOneLine(self):
		"""The fallback that keeps a table readable by everything not taught about columns —
		the dry run, a log line, and reading order itself."""
		self.assertEqual(row("AAPL", "182.50").rawText, "AAPL  182.50")


class TestDrawingARowInColumns(unittest.TestCase):
	def test_eachColumnStartsWhereThePlanSaysItDoes(self):
		drawn = renderer().render(block(row("AAPL", "182.50", "+1.25", "+0.7%")))
		line = textOf(drawn.rows[0])
		self.assertEqual(line[0:4], "AAPL")
		self.assertEqual(line[7:13], "182.50")
		self.assertEqual(line[15:20], "+1.25")

	def test_theSameColumnIsAtTheSameOffsetOnEveryRow(self):
		"""The whole milestone in one assertion: this is what makes running a finger down a
		column mean anything, and it is what reading order cannot give."""
		render = renderer()
		first = textOf(render.render(block(row("AAPL", "182.50", "+1.25", "+0.7%"))).rows[0])
		second = textOf(render.render(block(row("F", "9.10", "-0.05", "-0.5%"), name="s")).rows[0])
		self.assertEqual(first[15], "+")
		self.assertEqual(second[15], "-")

	def test_aShortCellLeavesItsColumnPadded(self):
		"""Padding between columns is what puts the next one where the finger expects it, and
		it is the one place a rendering here is padded rather than `assembleCells` doing it."""
		drawn = renderer().render(block(row("F", "9.10", "-0.05", "-0.5%")))
		self.assertEqual(textOf(drawn.rows[0])[1:7], "      ")

	def test_aLongCellIsCutAtItsColumn(self):
		"""Truncated, not spilled: every row is cut at the same place, so the shape down the
		display survives and the reader can widen the column."""
		drawn = renderer().render(block(row("BERKSHIRE", "1.00", "+0.01", "+0.1%")))
		self.assertEqual(textOf(drawn.rows[0])[0:7], "BERKSH ")

	def test_theRowIsTheFullWidthOfTheBand(self):
		drawn = renderer().render(block(row("F", "9.10", "-0.05", "-0.5%")))
		self.assertEqual(len(drawn.rows[0]), BAND)

	def test_aMissingCellLeavesItsColumnBlankAndMovesNothing(self):
		"""A merged cell, or a row still being built. Every other column stays where the
		reader left it, which is the only answer that keeps the table a table."""
		partial = TableRow(
			[RowCell(index=1, region=Region("AAPL")), RowCell(index=3, region=Region("+1.25"))]
		)
		line = textOf(renderer().render(block(partial)).rows[0])
		self.assertEqual(line[0:4], "AAPL")
		self.assertEqual(line[7:13], "      ")
		self.assertEqual(line[15:20], "+1.25")

	def test_columnsOnASecondRowAreDrawnThere(self):
		plan = watchlistPlan(numCols=16, maxRows=2)
		drawn = FlowRenderer(
			FakeHandler(),
			numCols=16,
			fillRows=True,
			columnPlan=plan,
		).render(block(row("AAPL", "182.50", "+1.25", "+0.7%")))
		self.assertEqual(len(drawn.rows), plan.numRows)
		self.assertGreater(plan.numRows, 1)


class TestARowWithNoPlan(unittest.TestCase):
	"""Reading order must survive, and it is what a table gets by default."""

	def test_readingOrderDrawsTheRowAsALine(self):
		drawn = renderer(plan=READING_ORDER).render(block(row("AAPL", "182.50")))
		self.assertEqual(textOf(drawn.rows[0]).strip(), "AAPL  182.50")

	def test_anOrdinaryBlockIsUnaffectedByAColumnPlan(self):
		"""A plan is in force for the table; the prose around it is drawn as it always was."""
		plain = SourceBlock(
			blockId=BlockId(generation=1, bookmark="p", unit="line"),
			region=Region("a paragraph"),
		)
		self.assertEqual(textOf(renderer().render(plain).rows[0]).strip(), "a paragraph")


class TestRoutingIntoAColumn(unittest.TestCase):
	"""A finger lands on the band and the answer has to be a cell of the table."""

	def test_aCellsPositionSaysWhichColumnAndWhereInIt(self):
		drawn = renderer().render(block(row("AAPL", "182.50", "+1.25", "+0.7%")))
		ordinal, offset = positionParts(drawn.positions[0][8])
		self.assertEqual(ordinal, 1)
		self.assertEqual(offset, 1)

	def test_aGapBelongsToNoPosition(self):
		"""Routing into padding must do nothing, which is what `NO_POSITION` says."""
		drawn = renderer().render(block(row("F", "9.10", "-0.05", "-0.5%")))
		self.assertEqual(drawn.positions[0][6], NO_POSITION)

	def test_theRowSendsARoutingPressToTheRightCell(self):
		cells = [RowCell(index=n, region=Region(text)) for n, text in enumerate(("AAPL", "182.50"), 1)]
		table = TableRow(cells)
		drawn = renderer().render(block(table))
		table.routeTo(drawn.positions[0][8])
		self.assertEqual(cells[1].region.routedTo, 1)
		self.assertFalse(hasattr(cells[0].region, "routedTo"))

	def test_aPositionForAColumnTheRowHasNotGotDoesNothing(self):
		table = row("AAPL")
		table.routeTo(1 << 20)


class TestThePlanIsPartOfTheKey(unittest.TestCase):
	"""A row drawn under one set of widths must never be served for one drawn under another,
	or the reader's finger finds the previous layout's columns."""

	def test_twoPlansGiveTwoKeys(self):
		wide = renderer().render(block(row("AAPL", "182.50"))).renderKey
		narrow = renderer(plan=watchlistPlan(numCols=24)).render(block(row("AAPL", "182.50"))).renderKey
		self.assertNotEqual(wide, narrow)

	def test_thesamePlanGivesTheSameKey(self):
		first = renderer().render(block(row("AAPL", "182.50"))).renderKey
		second = renderer().render(block(row("AAPL", "182.50"))).renderKey
		self.assertEqual(first, second)

	def test_aPlanBuiltByHandDrawsWhereItSays(self):
		plan = ColumnPlan(columns=(Column(index=1, width=4), Column(index=2, width=6)), numCols=BAND)
		drawn = renderer(plan=plan).render(block(row("AAPL", "182.50")))
		line = textOf(drawn.rows[0])
		self.assertEqual(line[0:4], "AAPL")
		self.assertEqual(line[5:11], "182.50")
