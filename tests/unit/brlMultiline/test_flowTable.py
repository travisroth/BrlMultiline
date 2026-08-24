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
	READABLE_CELLS,
	READING_ORDER,
	TRUNCATE,
	WRAP,
	Column,
	ColumnPlan,
	CELL_INDENT,
	Measurement,
	describe,
	indentFor,
	planFor,
	shouldReplan,
)

MONARCH_COLS = 32


def watchlist(**changes):
	""":return: the measurements of a stock watchlist, in cells."""
	# The widths already cover the headers, which is what `flowTableSource.measure` produces:
	# it reads the header row along with the rest and takes the widest, in cells.
	columns = [
		Measurement(index=1, width=6, label="Symbol", labelWidth=6),
		Measurement(index=2, width=8, label="Last", labelWidth=4),
		Measurement(index=3, width=7, label="Change", labelWidth=6),
		Measurement(index=4, width=6, label="%Chg", labelWidth=4),
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
		measured = [Measurement(index=1, width=1, label="Watched", labelWidth=7)]
		self.assertEqual(widthsOf(planFor(measured, MONARCH_COLS)), [7])

	def test_theHeaderIsMeasuredInCellsRatherThanCharacters(self):
		"""The arithmetic here is careful to work in cells everywhere else, and `len(label)`
		quietly put character counts back into it: a header contracted to four cells was
		given six because it has six characters."""
		measured = [Measurement(index=1, width=3, label="Change", labelWidth=4)]
		self.assertEqual(widthsOf(planFor(measured, MONARCH_COLS)), [4])

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

	def test_aColumnIsNotWidenedPastWhatItAskedFor(self):
		"""A column wider than its own longest cell is blank cells with a name."""
		columns = [
			Measurement(index=1, width=4, label=""),
			Measurement(index=2, width=40, label=""),
			Measurement(index=3, width=40, label=""),
		]
		plan = planFor(columns, MONARCH_COLS)
		self.assertEqual(widthsOf(plan)[0], 4)

	def test_nothingIsShrunkPastReading(self):
		"""A price of "310.34" in three cells is a digit at a time, and a reader running down
		a column of those is assembling prices rather than comparing them."""
		plan = planFor(tooMany(), MONARCH_COLS)
		for width in widthsOf(plan):
			self.assertGreaterEqual(width, READABLE_CELLS)

	def test_aColumnAlreadyShorterKeepsItsOwnWidth(self):
		"""The floor is a floor on shrinking, not a size. A column of one-character flags is
		not widened to six for the sake of a rule about wide columns."""
		columns = [Measurement(index=1, width=2, label="")] + [
			Measurement(index=n, width=20, label="") for n in range(2, 6)
		]
		plan = planFor(columns, MONARCH_COLS)
		self.assertEqual(widthsOf(plan)[0], MIN_COLUMN_CELLS)

	def test_aColumnWiderThanTheBandIsCutToIt(self):
		"""It cannot be drawn otherwise, and a column that can never be placed would be a
		page with nothing on it."""
		plan = planFor([Measurement(index=1, width=400, label="")], MONARCH_COLS)
		self.assertEqual(widthsOf(plan), [MONARCH_COLS])


class TestPagingAcrossTheTable(unittest.TestCase):
	"""Twenty-nine columns is an ordinary watchlist and thirty-two cells is an ordinary
	display, and no arithmetic reconciles those.

	The first attempt squeezed what it could into one band and dropped the rest: three cells
	of each of eight columns, and eleven columns that did not exist. The columns are dealt
	into pages now, and the reader moves between them exactly as the window moves between
	rows."""

	def test_aTableThatFitsIsOnePage(self):
		self.assertEqual(planFor(watchlist(), MONARCH_COLS).numPages, 1)

	def test_aTableThatDoesNotFitIsSeveral(self):
		self.assertGreater(planFor(tooMany(), MONARCH_COLS).numPages, 1)

	def test_everyColumnIsOnSomePage(self):
		"""Nothing is dropped, which is the whole change."""
		plan = planFor(tooMany(), MONARCH_COLS)
		drawn = [place.column.index for page in plan.pages() for place in page]
		self.assertEqual(drawn, [item.index for item in tooMany()])

	def test_thePageBeingDrawnIsTheOneAskedFor(self):
		plan = planFor(tooMany(), MONARCH_COLS)
		first = [place.column.index for place in plan.placements()]
		second = [place.column.index for place in plan.onPage(1).placements()]
		self.assertNotEqual(first, second)
		self.assertEqual(second, [place.column.index for place in plan.pages()[1]])

	def test_aPageBeyondTheLastIsTheLast(self):
		plan = planFor(tooMany(), MONARCH_COLS)
		self.assertEqual(plan.onPage(99).page, plan.numPages - 1)

	def test_aPageBeforeTheFirstIsTheFirst(self):
		self.assertEqual(planFor(tooMany(), MONARCH_COLS).onPage(-4).page, 0)

	def test_aColumnSaysWhichPageItIsOn(self):
		"""What the band needs to follow the caret across the table."""
		plan = planFor(tooMany(), MONARCH_COLS)
		last = plan.columns[-1].index
		self.assertEqual(plan.pageOf(last), plan.numPages - 1)
		self.assertEqual(plan.pageOf(1), 0)

	def test_aColumnTheTableHasNotGotIsOnNoPage(self):
		self.assertIsNone(planFor(watchlist(), MONARCH_COLS).pageOf(99))

	def test_everyPageStartsAtTheLeftOfTheBand(self):
		plan = planFor(tooMany(), MONARCH_COLS)
		for page in plan.pages():
			self.assertEqual(page[0].offset, 0)

	def test_aColumnIsAlwaysAtTheSameOffsetOnItsOwnPage(self):
		"""Which is what makes it findable, and is the whole point of a fixed layout."""
		plan = planFor(tooMany(), MONARCH_COLS)
		once = {place.column.index: place.offset for place in plan.onPage(1).placements()}
		again = {place.column.index: place.offset for place in plan.onPage(1).placements()}
		self.assertEqual(once, again)

	def test_readingOrderHasNoPages(self):
		self.assertEqual(READING_ORDER.pages(), ())
		self.assertEqual(READING_ORDER.numPages, 1)


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

	def test_aTableTooWideForTheBandIsStillTheSameTable(self):
		"""Its columns are on several pages. None of them has gone anywhere, so measuring it
		again must not decide it is a different table."""
		plan = planFor(tooMany(), MONARCH_COLS)
		self.assertGreater(plan.numPages, 1)
		self.assertFalse(shouldReplan(plan, tooMany(), MONARCH_COLS))

	def test_readingOrderIsNeverReplanned(self):
		"""A table nobody asked to lay out stays not laid out."""
		self.assertFalse(shouldReplan(READING_ORDER, watchlist(), MONARCH_COLS))


class TestIndentingAWrappedCell(unittest.TestCase):
	"""The column boundary says which column a continuation is in. What it cannot say is that
	the row below is the same value still going rather than the next value down."""

	def test_aRoomyColumnGetsTheFullIndent(self):
		self.assertEqual(indentFor(12), CELL_INDENT)

	def test_aNarrowColumnGetsLess(self):
		"""Two of a four cell column is half of it. A third is the most it can spare."""
		self.assertEqual(indentFor(4), 1)

	def test_neverNothing(self):
		"""A continuation drawn flush with the value above it reads as a second value."""
		self.assertEqual(indentFor(MIN_COLUMN_CELLS), 1)
		self.assertEqual(indentFor(1), 1)

	def test_itIsLessThanAListSpends(self):
		"""A column has far fewer cells to spend than a row does."""
		from flowIndent import CONTINUATION_EXTRA

		self.assertLessEqual(CELL_INDENT, 2 + CONTINUATION_EXTRA)

	def test_aColumnKnowsItsOwn(self):
		self.assertEqual(Column(index=1, width=12).indent, CELL_INDENT)


class TestSayingWhatTheLayoutCost(unittest.TestCase):
	"""A column narrower than its content cuts every cell at the same place, which is what
	makes the shape readable and also what leaves the reader feeling clipped words with
	nothing to say they are clipped."""

	def test_aColumnCutToFitIsRecorded(self):
		columns = [
			Measurement(index=1, width=20, label=""),
			Measurement(index=2, width=20, label=""),
		]
		self.assertEqual(planFor(columns, MONARCH_COLS).narrowed, (1, 2))

	def test_aColumnThatGotWhatItAskedForIsNot(self):
		self.assertEqual(planFor(watchlist(), MONARCH_COLS).narrowed, ())

	def test_aCappedColumnCountsAsCut(self):
		"""Its content is cut, whatever the reason the cap exists."""
		self.assertEqual(planFor([Measurement(index=1, width=900)], 200).narrowed, (1,))

	def test_theProseSaysSo(self):
		columns = [Measurement(index=n, width=20, label="") for n in (1, 2)]
		self.assertIn("narrower than its content", describe(planFor(columns, MONARCH_COLS)))

	def test_theProseSaysWrappedWhenNothingIsCut(self):
		"""Narrow and wrapped is a different thing from narrow and cut, and the reader is
		deciding what to do about it from this."""
		columns = [Measurement(index=n, width=20, label="") for n in (1, 2)]
		self.assertIn("wrapped", describe(planFor(columns, MONARCH_COLS)))

	def test_theProseSaysCutWhenItIsCut(self):
		columns = [Measurement(index=n, width=20, label="") for n in (1, 2)]
		said = describe(planFor(columns, MONARCH_COLS, overflow=TRUNCATE))
		self.assertIn("is cut", said)

	def test_aPlanKnowsWhetherItCuts(self):
		self.assertFalse(planFor(watchlist(), MONARCH_COLS).cuts)
		self.assertTrue(planFor(watchlist(), MONARCH_COLS, overflow=TRUNCATE).cuts)

	def test_readingOrderCutsNothing(self):
		self.assertFalse(READING_ORDER.cuts)


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
		self.assertEqual(plan.columns[0].overflow, WRAP)

	def test_cuttingIsAskedForRatherThanAssumed(self):
		"""A display that silently drops data is not a display of the data. Cutting is what a
		reader who knows the table asks for; it is not what a default may assume."""
		plan = planFor(watchlist(), MONARCH_COLS, overflow=TRUNCATE)
		self.assertEqual([column.overflow for column in plan.columns], [TRUNCATE] * 4)

	def test_aPlanBuiltByHandIsUsableAsIs(self):
		"""What an application module and a saved layout will hand over."""
		plan = ColumnPlan(
			columns=(Column(index=1, width=6), Column(index=2, width=8)),
			numCols=MONARCH_COLS,
		)
		self.assertEqual([place.offset for place in plan.placements()], [0, 7])
