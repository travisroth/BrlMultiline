# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for deciding where a table's columns go.

Written against a Monarch sized row of 32 cells, and against narrower ones where the
arithmetic is the thing being tested. Nothing here needs a braille table: a column width is
a count of cells, which is why `flowTable` imports nothing from NVDA.

The table used throughout is the one the whole milestone is for — a stock watchlist, which
is the case the reader named. Its shape is what makes the arithmetic matter: one narrow
column that cannot give up a cell, and one wide one that can give up ten.
"""

import os
import sys
import unittest

sys.path.insert(
	0,
	os.path.join(os.path.dirname(__file__), "..", "..", "..", "addon", "globalPlugins", "brlMultiline"),
)

from flowTable import (  # noqa: E402
	COLUMN_GAP,
	MAX_TABLE_ROWS,
	MIN_COLUMN_CELLS,
	READING_ORDER,
	TRUNCATE,
	Column,
	ColumnPlan,
	Measurement,
	describe,
	planFor,
	shouldReplan,
)

MONARCH_COLS = 32


def watchlist(**changes):
	""":return: the measurements of a stock watchlist, in cells."""
	columns = [
		Measurement(index=1, width=5, label="Symbol"),
		Measurement(index=2, width=8, label="Last"),
		Measurement(index=3, width=7, label="Change"),
		Measurement(index=4, width=6, label="%Chg"),
	]
	for index, width in changes.items():
		position = int(index.lstrip("c")) - 1
		columns[position] = Measurement(
			index=columns[position].index,
			width=width,
			label=columns[position].label,
		)
	return columns


def tooMany():
	""":return: more columns than a 32 cell band can hold at any width.

	Twelve. The threshold is worth knowing: shrinking runs all the way to `MIN_COLUMN_CELLS`
	before anything is dropped, so a 32 cell band fits eight columns before it drops one.
	Tables that overflow are tables with a great many columns, not tables with wide ones.
	"""
	return [Measurement(index=n, width=10, label="") for n in range(1, 13)]


def widthsOf(plan):
	return [column.width for column in plan.columns]


def indexesOf(plan):
	return [column.index for column in plan.columns]


class TestNothingToLayOut(unittest.TestCase):
	"""Reading order is what a table gets until something asks for otherwise."""

	def test_noColumnsIsReadingOrder(self):
		self.assertIs(planFor([], MONARCH_COLS), READING_ORDER)

	def test_everyColumnHiddenIsReadingOrder(self):
		hidden = [Measurement(index=n, width=6, hidden=True) for n in (1, 2)]
		self.assertIs(planFor(hidden, MONARCH_COLS), READING_ORDER)

	def test_aBandTooNarrowForOneColumnIsReadingOrder(self):
		"""Two cells is not a narrow column, it is no column."""
		self.assertIs(planFor(watchlist(), MIN_COLUMN_CELLS - 1), READING_ORDER)

	def test_readingOrderDrawsNothing(self):
		self.assertTrue(READING_ORDER.isEmpty)
		self.assertEqual(READING_ORDER.placements(), ())


class TestWidthsThatFit(unittest.TestCase):
	def test_everyColumnGetsWhatItAskedFor(self):
		"""5 + 8 + 7 + 6 is 26, and three gaps make 29 of 32."""
		plan = planFor(watchlist(), MONARCH_COLS)
		self.assertEqual(widthsOf(plan), [6, 8, 7, 6])

	def test_aColumnIsAtLeastAsWideAsItsHeader(self):
		"""A column of one character flags under a header called "Watched" is a column whose
		header cannot be read, and the header is what the reader is looking for."""
		plan = planFor([Measurement(index=1, width=1, label="Watched")], MONARCH_COLS)
		self.assertEqual(widthsOf(plan), [7])

	def test_aColumnIsNeverNarrowerThanTheMinimum(self):
		plan = planFor([Measurement(index=1, width=1, label="")], MONARCH_COLS)
		self.assertEqual(widthsOf(plan), [MIN_COLUMN_CELLS])

	def test_aRunawayColumnIsCapped(self):
		"""A description column is hundreds of characters and would take the band for
		itself."""
		plan = planFor([Measurement(index=1, width=900, label="")], 200)
		self.assertLess(widthsOf(plan)[0], 900)

	def test_aHiddenColumnIsNotDrawn(self):
		columns = watchlist()
		columns[1] = Measurement(index=2, width=8, label="Last", hidden=True)
		self.assertEqual(indexesOf(planFor(columns, MONARCH_COLS)), [1, 3, 4])


class TestWidthsThatDoNotFit(unittest.TestCase):
	"""Taken from the widest first, which is what keeps a short column readable."""

	def test_theWideColumnGivesUpTheCells(self):
		"""Forty cells of description can spare ten before five cells of ticker spare one."""
		columns = [
			Measurement(index=1, width=5, label=""),
			Measurement(index=2, width=40, label=""),
		]
		plan = planFor(columns, 20)
		self.assertEqual(widthsOf(plan)[0], 5)
		self.assertEqual(sum(widthsOf(plan)) + COLUMN_GAP, 20)

	def test_aProportionalCutIsNotWhatHappens(self):
		"""The narrow column keeps every cell it asked for while the wide one has room to
		give. A proportional cut would take from both."""
		columns = [
			Measurement(index=1, width=4, label=""),
			Measurement(index=2, width=4, label=""),
			Measurement(index=3, width=30, label=""),
		]
		plan = planFor(columns, MONARCH_COLS)
		self.assertEqual(widthsOf(plan)[:2], [4, 4])

	def test_nothingIsDrawnBelowTheMinimum(self):
		columns = [Measurement(index=n, width=9, label="") for n in range(1, 6)]
		plan = planFor(columns, 20)
		for width in widthsOf(plan):
			self.assertGreaterEqual(width, MIN_COLUMN_CELLS)

	def test_shrinkingIsTriedBeforeAnythingIsDropped(self):
		"""Three columns at ten cells beat two at fourteen: the reader asked for the table,
		and a column that is there but short can still be read."""
		columns = [Measurement(index=n, width=14, label="") for n in range(1, 4)]
		plan = planFor(columns, MONARCH_COLS)
		self.assertEqual(indexesOf(plan), [1, 2, 3])
		self.assertFalse(plan.dropped)

	def test_whatWillNotFitIsDroppedFromTheRight(self):
		"""Tables' least important columns are on the right, and a reader who disagrees can
		hide one by hand."""
		plan = planFor(tooMany(), MONARCH_COLS)
		self.assertEqual(indexesOf(plan) + list(plan.dropped), list(range(1, 13)))
		self.assertTrue(plan.dropped)
		self.assertEqual(indexesOf(plan)[0], 1)

	def test_aDroppedColumnIsRecordedRatherThanForgotten(self):
		"""A column absent from a display is indistinguishable from one the table has not
		got, and a reader deciding what to hide needs to know which."""
		self.assertIn("No room for column", describe(planFor(tooMany(), MONARCH_COLS)))

	def test_theRoomADroppedColumnFreesGoesBackToTheRest(self):
		"""Otherwise the band ends short by the cells the dropped column was going to use,
		with every column that is left drawn at the bare minimum for no reason."""
		plan = planFor(tooMany(), MONARCH_COLS)
		self.assertGreater(max(widthsOf(plan)), MIN_COLUMN_CELLS)
		used = sum(widthsOf(plan)) + COLUMN_GAP * (len(plan.columns) - 1)
		self.assertGreater(used, MONARCH_COLS - MIN_COLUMN_CELLS)

	def test_aColumnIsNotWidenedPastWhatItAskedFor(self):
		"""A column wider than its own longest cell is blank cells with a name."""
		columns = [
			Measurement(index=1, width=4, label=""),
			Measurement(index=2, width=40, label=""),
			Measurement(index=3, width=40, label=""),
		]
		plan = planFor(columns, MONARCH_COLS)
		self.assertEqual(widthsOf(plan)[0], 4)


class TestPackingAcrossRows(unittest.TestCase):
	"""A row may take more than one row of the band, where the reader allows it."""

	def test_oneRowByDefault(self):
		self.assertEqual(planFor(watchlist(), MONARCH_COLS).numRows, 1)

	def test_asecondRowLetsEveryColumnKeepItsWidth(self):
		columns = [Measurement(index=n, width=14, label="") for n in range(1, 5)]
		plan = planFor(columns, MONARCH_COLS, maxRows=2)
		self.assertEqual(widthsOf(plan), [14, 14, 14, 14])
		self.assertEqual(plan.numRows, 2)

	def test_columnsArePackedGreedilyNotBalanced(self):
		"""The hand goes left to right and then down, so the first row holds as much as it
		can. A balanced pack moves a column down for the sake of a shape nobody reads."""
		columns = [Measurement(index=n, width=9, label="") for n in range(1, 4)]
		plan = planFor(columns, 20, maxRows=2)
		rows = [place.row for place in plan.placements()]
		self.assertEqual(rows, [0, 0, 1])

	def test_aRowNeverTakesMoreThanTheCap(self):
		columns = [Measurement(index=n, width=30, label="") for n in range(1, 12)]
		plan = planFor(columns, MONARCH_COLS, maxRows=99)
		self.assertLessEqual(plan.numRows, MAX_TABLE_ROWS)

	def test_offsetsAreWhereTheColumnsStart(self):
		plan = planFor(watchlist(), MONARCH_COLS)
		offsets = [place.offset for place in plan.placements()]
		self.assertEqual(offsets, [0, 7, 16, 24])

	def test_aColumnAlwaysStartsAtTheSameOffset(self):
		"""The whole point: the same column at the same cell on every row is what makes
		running a finger down one of them mean anything."""
		plan = planFor(watchlist(), MONARCH_COLS)
		places = {place.column.index: place.offset for place in plan.placements()}
		self.assertEqual(places[3], 16)
		self.assertEqual(planFor(watchlist(), MONARCH_COLS).placements()[2].offset, 16)


class TestFindingAColumnFromACell(unittest.TestCase):
	"""What routing needs: a finger lands on the band and the answer is a cell of the table."""

	def test_aCellInsideAColumnFindsIt(self):
		plan = planFor(watchlist(), MONARCH_COLS)
		self.assertEqual(plan.columnAt(0, 0).index, 1)
		self.assertEqual(plan.columnAt(0, 17).index, 3)

	def test_aGapBelongsToNoColumn(self):
		"""Routing into it must do nothing, which is what padding already does."""
		plan = planFor(watchlist(), MONARCH_COLS)
		self.assertIsNone(plan.columnAt(0, 6))

	def test_pastTheLastColumnIsNoColumn(self):
		plan = planFor(watchlist(), MONARCH_COLS)
		self.assertIsNone(plan.columnAt(0, 31))

	def test_theWrongBandRowIsNoColumn(self):
		plan = planFor(watchlist(), MONARCH_COLS)
		self.assertIsNone(plan.columnAt(1, 0))

	def test_aColumnOnTheSecondRowIsFoundThere(self):
		columns = [Measurement(index=n, width=9, label="") for n in range(1, 4)]
		plan = planFor(columns, 20, maxRows=2)
		self.assertEqual(plan.columnAt(1, 0).index, 3)


class TestKeepingThePlan(unittest.TestCase):
	"""Columns are decided once. A column that moves is a column to be found again."""

	def test_thesameTableIsNotReplanned(self):
		plan = planFor(watchlist(), MONARCH_COLS)
		self.assertFalse(shouldReplan(plan, watchlist(), MONARCH_COLS))

	def test_contentGrowingWiderIsNotAReplan(self):
		"""That is what truncation is for. Re-jigging the table because one cell grew is
		exactly the dance this exists to prevent."""
		plan = planFor(watchlist(), MONARCH_COLS)
		self.assertFalse(shouldReplan(plan, watchlist(c2=40), MONARCH_COLS))

	def test_contentGrowingNarrowerIsNotAReplanEither(self):
		plan = planFor(watchlist(), MONARCH_COLS)
		self.assertFalse(shouldReplan(plan, watchlist(c2=1), MONARCH_COLS))

	def test_aDifferentBandWidthIsAReplan(self):
		plan = planFor(watchlist(), MONARCH_COLS)
		self.assertTrue(shouldReplan(plan, watchlist(), 40))

	def test_aDifferentRowHeightIsAReplan(self):
		plan = planFor(watchlist(), MONARCH_COLS)
		self.assertTrue(shouldReplan(plan, watchlist(), MONARCH_COLS, maxRows=2))

	def test_aColumnAppearingIsAReplan(self):
		plan = planFor(watchlist(), MONARCH_COLS)
		more = watchlist() + [Measurement(index=5, width=4, label="Vol")]
		self.assertTrue(shouldReplan(plan, more, MONARCH_COLS))

	def test_aColumnBeingHiddenIsAReplan(self):
		plan = planFor(watchlist(), MONARCH_COLS)
		columns = watchlist()
		columns[1] = Measurement(index=2, width=8, label="Last", hidden=True)
		self.assertTrue(shouldReplan(plan, columns, MONARCH_COLS))

	def test_aDroppedColumnStillCountsAsPartOfTheTable(self):
		"""It is not drawn, but it is still a column of the table the plan was made for, and
		a plan that forgot it would replan every time it was measured again."""
		plan = planFor(tooMany(), MONARCH_COLS)
		self.assertTrue(plan.dropped)
		self.assertFalse(shouldReplan(plan, tooMany(), MONARCH_COLS))

	def test_readingOrderIsNeverReplanned(self):
		"""A table nobody asked to lay out stays not laid out."""
		self.assertFalse(shouldReplan(READING_ORDER, watchlist(), MONARCH_COLS))


class TestSayingWhatThePlanIs(unittest.TestCase):
	def test_readingOrderSaysSo(self):
		self.assertIn("reading order", describe(READING_ORDER))

	def test_aPlanNamesEachColumnsPlace(self):
		said = describe(planFor(watchlist(), MONARCH_COLS))
		self.assertIn("column 1 at row 0 cell 0", said)
		self.assertIn("column 3 at row 0 cell 16", said)


class TestThePlanIsAKey(unittest.TestCase):
	"""Two rows drawn under different plans are two different renderings."""

	def test_thesameInputsGiveAnEqualPlan(self):
		self.assertEqual(planFor(watchlist(), MONARCH_COLS), planFor(watchlist(), MONARCH_COLS))

	def test_aDifferentWidthGivesADifferentPlan(self):
		self.assertNotEqual(planFor(watchlist(), MONARCH_COLS), planFor(watchlist(), 24))

	def test_aPlanIsHashable(self):
		self.assertIsInstance(hash(planFor(watchlist(), MONARCH_COLS)), int)

	def test_aColumnCarriesItsOverflowStyle(self):
		plan = planFor(watchlist(), MONARCH_COLS)
		self.assertEqual(plan.columns[0].overflow, TRUNCATE)

	def test_aPlanBuiltByHandIsUsableAsIs(self):
		"""What an application module and a saved layout will hand over."""
		plan = ColumnPlan(
			columns=(Column(index=1, width=6), Column(index=2, width=8)),
			numCols=MONARCH_COLS,
		)
		self.assertEqual([place.offset for place in plan.placements()], [0, 7])
