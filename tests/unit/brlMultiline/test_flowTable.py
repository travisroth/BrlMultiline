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

import dataclasses
import os
import sys
import unittest

sys.path.insert(
	0,
	os.path.join(os.path.dirname(__file__), "..", "..", "..", "addon", "globalPlugins", "brlMultiline"),
)

from flowTable import (  # noqa: E402
	KEEP_END,
	KEEP_START,
	ColumnChoice,
	COLUMN_GAP,
	DEFAULT_TARGET_HEIGHT,
	KEY_SHARE,
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
	predictedHeight,
	rowsNeeded,
	shouldReplan,
	targetHeightFor,
)

MONARCH_COLS = 32

class TestWhatAReaderChoseAboutAColumn(unittest.TestCase):
	"""M7's vocabulary, tested where it is arithmetic rather than dialog.

	The reported case: a table whose first column carries six lines of help text before the
	thing that names the row. Cut from the start it says somebody else's advice on every row;
	cut from the end it says the row's name; renamed it says whatever the reader calls it.
	"""

	def _plan(self, choices=None, **kwargs):
		return planFor(watchlist(), MONARCH_COLS, choices=choices, **kwargs)

	def _column(self, plan, index):
		return next(column for column in plan.columns if column.index == index)

	def test_aColumnKeepsItsOwnNameByDefault(self):
		self.assertEqual(self._column(self._plan(), 1).label, "Symbol")

	def test_andTakesTheReadersInstead(self):
		"""The strongest answer to a heading nobody can read."""
		plan = self._plan({1: ColumnChoice(label="Ticker")})
		self.assertEqual(self._column(plan, 1).label, "Ticker")

	def test_aColumnCanBeCutWhileTheRestWrap(self):
		plan = self._plan({2: ColumnChoice(overflow=TRUNCATE)}, overflow=WRAP)
		self.assertEqual(self._column(plan, 2).overflow, TRUNCATE)
		self.assertEqual(self._column(plan, 1).overflow, WRAP)

	def test_andCutFromTheEndWhereThatIsWhereTheAnswerIs(self):
		plan = self._plan({1: ColumnChoice(keep=KEEP_END)})
		self.assertEqual(self._column(plan, 1).keep, KEEP_END)
		self.assertEqual(self._column(plan, 2).keep, KEEP_START)

	def test_theHeadingIsCutAtItsOwnEnd(self):
		"""A column of short values under a six line heading is the reader's case, and the
		two go wrong separately."""
		plan = self._plan({1: ColumnChoice(headerKeep=KEEP_END)})
		header = next(column for column in plan.cutting().columns if column.index == 1)
		self.assertEqual(header.keep, KEEP_END)
		self.assertEqual(header.overflow, TRUNCATE)

	def test_aColumnCanBeGivenARoom(self):
		"""What a reader means by "give the description room"."""
		plan = self._plan({4: ColumnChoice(minWidth=12)})
		self.assertGreaterEqual(self._column(plan, 4).width, 12)

	def test_andCappedWhereItIsWiderThanItIsWorth(self):
		plan = self._plan({2: ColumnChoice(maxWidth=4)})
		self.assertLessEqual(self._column(plan, 2).width, 4)

	def test_aPageCanBeMadeToBeginAtAColumn(self):
		"""How a reader says "these together, those after them" without describing every page."""
		plan = self._plan({3: ColumnChoice(startsAPage=True)})
		self.assertEqual(plan.pageOf(1), 0)
		self.assertEqual(plan.pageOf(2), 0)
		self.assertEqual(plan.pageOf(3), 1)

	def test_andTheOtherBreaksStillFallWhereTheyFit(self):
		"""Only the break they named is theirs; the packing decides the rest."""
		plan = self._plan({2: ColumnChoice(startsAPage=True)})
		self.assertEqual(plan.pageOf(1), 0)
		self.assertGreaterEqual(plan.numPages, 2)

	def test_aBreakAtTheFirstColumnIsNotAnEmptyPage(self):
		plan = self._plan({1: ColumnChoice(startsAPage=True)})
		self.assertEqual(plan.pageOf(1), 0)

	def test_theKeyColumnIsTheFirstDrawnUnlessTheySayOtherwise(self):
		plan = planFor(watchlist(), 20, pinKey=True)
		self.assertEqual(plan.keyColumn, 1)

	def test_andIsTheirsToChoose(self):
		"""The first column is the row's own label in most tables and an icon in some."""
		plan = planFor(watchlist(), 20, pinKey=True, keyColumn=2)
		self.assertEqual(plan.keyColumn, 2)

	def test_aChoiceOfSomethingNotDrawnIsNoChoice(self):
		plan = planFor(watchlist(), 20, pinKey=True, keyColumn=99)
		self.assertEqual(plan.keyColumn, 1)

	def test_sayingNothingChangesNothing(self):
		self.assertEqual(planFor(watchlist(), MONARCH_COLS, choices={}), planFor(watchlist(), MONARCH_COLS))


MONARCH_ROWS = 8


def statement():
	""":return: the table that showed the widths were being chosen on the wrong axis.

	A bank statement, from the reader's own hardware log. Four columns of 10, 8, 24 and 6
	cells: they were laid out at 7, 7, 7 and 8, which is exactly thirty-two with the gaps —
	a flawless horizontal fit in which every cell wrapped to four rows underneath, and two
	records reached an eight row display.
	"""
	return [
		Measurement(index=1, width=10, typicalWidth=10, label="Date", labelWidth=4),
		Measurement(index=2, width=8, typicalWidth=7, label="Activity", labelWidth=8),
		Measurement(index=3, width=24, typicalWidth=22, label="Description", labelWidth=11),
		Measurement(index=4, width=6, typicalWidth=5, label="Amount", labelWidth=6),
	]


def vpat():
	""":return: a conformance report: two short columns and a column of prose.

	The reader works with these constantly and named the shape: "a super long remarks
	column". No arrangement puts it beside anything at a readable width.
	"""
	return [
		Measurement(index=1, width=22, typicalWidth=18, label="Criteria", labelWidth=8),
		Measurement(index=2, width=12, typicalWidth=8, label="Level", labelWidth=5),
		Measurement(index=3, width=200, typicalWidth=60, label="Remarks", labelWidth=7),
	]


def bigWatchlist(columns=29):
	""":return: the reader's own watchlist: far more columns than any band can hold."""
	return [
		Measurement(index=n, width=7, typicalWidth=6, label=f"C{n}", labelWidth=3)
		for n in range(1, columns + 1)
	]


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


SHARING = dict(targetHeight=MAX_TABLE_ROWS * 8, pinKey=False)
"""Plan for columns sharing a page, whatever it costs in height.

How many columns share a page is a separate rule with tests of its own — see
L{TestHeightDecidesHowManyColumnsShareAPage}. These tests are about the cells being shared
once that is settled, and a target that pages them apart would leave them measuring one
column at a time and passing for the wrong reason.
"""


class TestWidthsThatDoNotFit(unittest.TestCase):
	"""Taken from the widest first, which is what keeps a short column readable."""

	def test_theWideColumnGivesUpTheCells(self):
		"""Forty cells of description can spare ten before five cells of ticker spare one."""
		columns = [
			Measurement(index=1, width=5, label=""),
			Measurement(index=2, width=40, label=""),
		]
		plan = planFor(columns, 20, **SHARING)
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
		plan = planFor(columns, MONARCH_COLS, **SHARING)
		self.assertEqual(widthsOf(plan)[:2], [4, 4])

	def test_nothingIsDrawnBelowTheMinimum(self):
		columns = [Measurement(index=n, width=9, label="") for n in range(1, 6)]
		plan = planFor(columns, 20, **SHARING)
		for width in widthsOf(plan):
			self.assertGreaterEqual(width, MIN_COLUMN_CELLS)

	def test_aColumnIsNotWidenedPastWhatItAskedFor(self):
		"""A column wider than its own longest cell is blank cells with a name."""
		columns = [
			Measurement(index=1, width=4, label=""),
			Measurement(index=2, width=40, label=""),
			Measurement(index=3, width=40, label=""),
		]
		plan = planFor(columns, MONARCH_COLS, **SHARING)
		self.assertEqual(widthsOf(plan)[0], 4)

	def test_nothingIsShrunkPastReading(self):
		"""A price of "310.34" in three cells is a digit at a time, and a reader running down
		a column of those is assembling prices rather than comparing them."""
		plan = planFor(tooMany(), MONARCH_COLS, **SHARING)
		for width in widthsOf(plan):
			self.assertGreaterEqual(width, READABLE_CELLS)

	def test_aColumnAlreadyShorterKeepsItsOwnWidth(self):
		"""The floor is a floor on shrinking, not a size. A column of one-character flags is
		not widened to six for the sake of a rule about wide columns."""
		columns = [Measurement(index=1, width=2, label="")] + [
			Measurement(index=n, width=20, label="") for n in range(2, 6)
		]
		plan = planFor(columns, MONARCH_COLS, **SHARING)
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
		drawn = [place.column.index for page in plan.pages() for place in page if not place.column.pinned]
		self.assertEqual(drawn, [item.index for item in tooMany()])

	def test_theColumnsKeepTheTablesOrder(self):
		"""A column is findable because it is always on the same page at the same offset, and
		that holds only while the pages are dealt in the table's own order."""
		plan = planFor(tooMany(), MONARCH_COLS)
		flat = [index for page in plan.assignment for index in page]
		self.assertEqual(flat, sorted(flat))

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


class TestHowTallARowWillBe(unittest.TestCase):
	"""The arithmetic the width decision was missing. A column is chosen by how many cells it
	gets; what the reader feels is how many rows that costs."""

	def test_aCellThatFitsIsOneRow(self):
		self.assertEqual(rowsNeeded(6, 6), 1)
		self.assertEqual(rowsNeeded(1, 6), 1)

	def test_aCellThatDoesNotFitTakesMore(self):
		self.assertEqual(rowsNeeded(7, 6), 2)

	def test_theContinuationsAreNarrowerByTheIndent(self):
		"""Not a plain division: every row after the first pays the column's indent."""
		width = 10
		self.assertEqual(indentFor(width), 2)
		# Ten cells, then eight on each row after it.
		self.assertEqual(rowsNeeded(10, width), 1)
		self.assertEqual(rowsNeeded(18, width), 2)
		self.assertEqual(rowsNeeded(19, width), 3)

	def test_theStatementsDescriptionAtSevenCells(self):
		"""The number from the hardware log, which is what a perfect horizontal fit cost."""
		self.assertEqual(rowsNeeded(22, 7), 4)

	def test_theSameDescriptionWithFiveMoreCells(self):
		"""And why giving one column up is worth more than it costs: five more cells to the
		column that needed them takes the row from four band rows to two."""
		self.assertEqual(rowsNeeded(22, 12), 2)

	def test_aColumnWithNoCellsIsStillOneRow(self):
		self.assertEqual(rowsNeeded(10, 0), 1)


class TestTheTargetComesFromTheBand(unittest.TestCase):
	"""How tall a row may be is only meaningful beside how many rows there is room for."""

	def test_aMonarchGetsTwo(self):
		self.assertEqual(targetHeightFor(MONARCH_ROWS), DEFAULT_TARGET_HEIGHT)

	def test_aTallerBandAllowsTallerRows(self):
		self.assertGreater(targetHeightFor(40), targetHeightFor(MONARCH_ROWS))

	def test_aShortBandDoesNotCollapseToOne(self):
		"""One would mean a page per column on any table with prose in it."""
		self.assertGreaterEqual(targetHeightFor(2), DEFAULT_TARGET_HEIGHT)


class TestHeightDecidesHowManyColumnsShareAPage(unittest.TestCase):
	"""The rule that replaces "as many columns as fit across the band". Four columns at seven
	cells add up to exactly thirty-two and every one of them wraps to four rows underneath;
	the fit is perfect on the axis nobody reads on."""

	def test_theStatementGivesUpAColumn(self):
		plan = planFor(statement(), MONARCH_COLS, targetHeight=DEFAULT_TARGET_HEIGHT)
		self.assertEqual(len(plan.placements()), 3)
		self.assertEqual(plan.numPages, 2)

	def test_andTheRowIsWithinTheTarget(self):
		plan = planFor(statement(), MONARCH_COLS, targetHeight=DEFAULT_TARGET_HEIGHT)
		self.assertLessEqual(predictedHeight(plan, statement()), DEFAULT_TARGET_HEIGHT)

	def test_theColumnThatNeededTheCellsGotThem(self):
		"""Description at seven cells is four rows. The point of dropping a column is the
		cells it frees, and they have to reach the column that was wrapping."""
		plan = planFor(statement(), MONARCH_COLS, targetHeight=DEFAULT_TARGET_HEIGHT)
		description = next(place for place in plan.placements() if place.column.index == 3)
		self.assertGreater(description.column.width, 7)

	def test_aTableThatFitsIsUntouched(self):
		"""The rule must not cost anything on a table that was already right."""
		plan = planFor(watchlist(), MONARCH_COLS)
		self.assertEqual(plan.numPages, 1)
		self.assertEqual(len(plan.placements()), 4)

	def test_aGenerousTargetKeepsMoreColumnsTogether(self):
		"""The reader who wants the columns side by side at any cost, which is what the
		row-height setting is for one axis over."""
		tight = planFor(statement(), MONARCH_COLS, targetHeight=2)
		loose = planFor(statement(), MONARCH_COLS, targetHeight=4)
		self.assertGreater(len(loose.placements()), len(tight.placements()))


class TestHowTallARowActuallyComesOut(unittest.TestCase):
	"""A plan may pack its columns across more than one row of the band, and each of those
	lanes is as tall as the tallest cell in it. Taking the tallest column overall under-counts
	by exactly the lanes it ignores: two lanes of two rows each is four rows on the band,
	reported as two, and the search then accepted a page it should have refused."""

	def _twoLanes(self):
		"""Four columns wanting more cells than one row of the band has."""
		return [Measurement(index=n, width=20, typicalWidth=20, label="") for n in range(1, 5)]

	def test_theHeightIsTheLanesAddedUp(self):
		plan = planFor(self._twoLanes(), MONARCH_COLS, maxRows=2, targetHeight=99, pinKey=False)
		lanes = {place.row for place in plan.placements()}
		self.assertEqual(len(lanes), 2)
		self.assertEqual(
			predictedHeight(plan, self._twoLanes()),
			sum(
				max(rowsNeeded(20, place.column.width) for place in plan.placements() if place.row == lane)
				for lane in lanes
			),
		)

	def test_aPlanIsNotAcceptedTallerThanTheTarget(self):
		"""The reviewer's case: configured for two rows, predicted two, rendered four."""
		wanted = self._twoLanes()
		plan = planFor(wanted, MONARCH_COLS, maxRows=2, targetHeight=2, pinKey=False)
		self.assertLessEqual(predictedHeight(plan, wanted), 2)

	def test_oneLaneIsStillItsOwnHeight(self):
		wanted = [Measurement(index=1, width=40, typicalWidth=40, label="")]
		plan = planFor(wanted, MONARCH_COLS, targetHeight=99, pinKey=False)
		self.assertEqual(predictedHeight(plan, wanted), rowsNeeded(40, MONARCH_COLS))

	def test_cutColumnsAreOneRowPerLane(self):
		wanted = self._twoLanes()
		plan = planFor(wanted, MONARCH_COLS, maxRows=2, targetHeight=99, overflow=TRUNCATE, pinKey=False)
		lanes = len({place.row for place in plan.placements()})
		self.assertEqual(predictedHeight(plan, wanted), lanes)


class TestAColumnOfProse(unittest.TestCase):
	"""A VPAT remarks column: no arrangement puts it beside anything at a readable width. The
	reader asked for one column at a time in that case, and the search reaching one is it."""

	def test_itGetsAPageToItself(self):
		plan = planFor(vpat(), MONARCH_COLS, targetHeight=DEFAULT_TARGET_HEIGHT, pinKey=False)
		last = plan.onPage(plan.numPages - 1)
		self.assertEqual([place.column.index for place in last.placements()], [3])

	def test_itIsNotDroppedForBeingImpossible(self):
		plan = planFor(vpat(), MONARCH_COLS, targetHeight=DEFAULT_TARGET_HEIGHT)
		drawn = {place.column.index for page in plan.pages() for place in page}
		self.assertEqual(drawn, {1, 2, 3})

	def test_theShortColumnsStillShareAPage(self):
		"""One prose column must not drag the readable columns into pages of their own."""
		plan = planFor(vpat(), MONARCH_COLS, targetHeight=DEFAULT_TARGET_HEIGHT, pinKey=False)
		self.assertEqual([place.column.index for place in plan.placements()], [1, 2])

	def test_itIsGivenTheWholeBand(self):
		plan = planFor(vpat(), MONARCH_COLS, targetHeight=DEFAULT_TARGET_HEIGHT, pinKey=False)
		last = plan.onPage(plan.numPages - 1)
		self.assertEqual(last.placements()[0].column.width, MONARCH_COLS)


class TestAPageIsFilled(unittest.TestCase):
	"""Every column starts at what its widest cell asked for, capped at the band, and the
	widest give cells back until the page holds them. Nothing is left unspent by that, which
	is why the step written to hand spare cells out afterwards turned out to be unreachable."""

	def _page(self):
		wanted = [
			Measurement(index=1, width=4, typicalWidth=4, label=""),
			Measurement(index=2, width=60, typicalWidth=40, label=""),
		]
		return planFor(wanted, MONARCH_COLS, targetHeight=4, pinKey=False)

	def test_thePageIsFilled(self):
		plan = self._page()
		used = sum(place.column.width for place in plan.placements())
		gaps = COLUMN_GAP * (len(plan.placements()) - 1)
		self.assertEqual(used + gaps, MONARCH_COLS)

	def test_theCellsWentToTheColumnThatWouldWrap(self):
		plan = self._page()
		widths = {place.column.index: place.column.width for place in plan.placements()}
		self.assertEqual(widths[1], 4)
		self.assertGreater(widths[2], READABLE_CELLS)

	def test_aColumnIsNotWidenedPastItsOwnContent(self):
		"""Cells after the end of a value are not readability, they are blank cells."""
		wanted = [Measurement(index=1, width=4, typicalWidth=4, label="")]
		plan = planFor(wanted, MONARCH_COLS, pinKey=False)
		self.assertEqual(plan.placements()[0].column.width, 4)


class TestTheWidthIsChosenForATypicalCell(unittest.TestCase):
	"""Nine rows in ten hold a few words and the tenth holds a paragraph. Planned from the
	widest, all ten are laid out for the paragraph."""

	def _oneLongCellIn(self, typical):
		return [
			Measurement(index=1, width=6, typicalWidth=6, label=""),
			Measurement(index=2, width=200, typicalWidth=typical, label=""),
			Measurement(index=3, width=6, typicalWidth=6, label=""),
		]

	def test_theOutlierDoesNotDecideTheLayout(self):
		plan = planFor(self._oneLongCellIn(8), MONARCH_COLS, pinKey=False)
		self.assertEqual(len(plan.placements()), 3)

	def test_aColumnThatIsUsuallyLongDoes(self):
		plan = planFor(self._oneLongCellIn(80), MONARCH_COLS, pinKey=False)
		self.assertLess(len(plan.placements()), 3)

	def test_anUnmeasuredTypicalFallsBackToTheWidest(self):
		"""A caller that measured only the widest gets what it always got."""
		wanted = [Measurement(index=1, width=12, label="")]
		self.assertEqual(wanted[0].typical, 12)


class TestAColumnThatIsNotThere(unittest.TestCase):
	"""A column of icons NVDA cannot read costs four cells of a thirty-two cell band on every
	row, and being the first column it was also what the key column pinned to every page."""

	def _withAnEmptyFirstColumn(self):
		return [Measurement(index=1, width=0, typicalWidth=0, label="", labelWidth=0)] + [
			Measurement(index=n, width=7, typicalWidth=6, label=f"C{n}", labelWidth=3) for n in range(2, 8)
		]

	def test_itIsNotDrawn(self):
		plan = planFor(self._withAnEmptyFirstColumn(), MONARCH_COLS)
		self.assertNotIn(1, [column.index for column in plan.columns])

	def test_itsCellsGoToTheColumnsThatHaveSomethingInThem(self):
		empty = planFor(self._withAnEmptyFirstColumn(), MONARCH_COLS)
		self.assertGreater(len(empty.placements()), 1)
		self.assertNotIn(1, [place.column.index for place in empty.placements()])

	def test_theKeyColumnIsTheFirstOneWithSomethingInIt(self):
		"""The thing repeated on every page to say which row this is was a blank."""
		plan = planFor(self._withAnEmptyFirstColumn(), MONARCH_COLS)
		self.assertEqual(plan.keyColumn, 2)

	def test_theReportSaysWhichColumnsWereLeftOut(self):
		"""The one kind of wrongness a reader cannot see: the band looks like a table with
		fewer columns in it, and nothing says whether that is the table or the layout."""
		plan = planFor(self._withAnEmptyFirstColumn(), MONARCH_COLS)
		self.assertEqual(plan.omitted, (1,))
		self.assertIn("Column 1 holds nothing", describe(plan))

	def test_thePlanStillKnowsAboutIt(self):
		"""Knowing a column is not the same as drawing it, and the difference is what tells a
		table that changed shape from a caret sitting somewhere ordinary."""
		plan = planFor(self._withAnEmptyFirstColumn(), MONARCH_COLS)
		self.assertIsNone(plan.pageOf(1))
		self.assertTrue(plan.knows(1))

	def test_aColumnItNeverMeasuredIsNotKnown(self):
		plan = planFor(self._withAnEmptyFirstColumn(), MONARCH_COLS)
		self.assertFalse(plan.knows(99))

	def test_aColumnItDrawsIsKnown(self):
		plan = planFor(self._withAnEmptyFirstColumn(), MONARCH_COLS)
		self.assertTrue(plan.knows(2))

	def test_aTableWithNothingInItIsNotLaidOut(self):
		nothing = [Measurement(index=n, width=0, label="") for n in range(1, 4)]
		self.assertIs(planFor(nothing, MONARCH_COLS), READING_ORDER)


class TestTheKeyColumnIsRepeated(unittest.TestCase):
	"""Six columns into a watchlist the reader is feeling four numbers with nothing to say
	whose numbers they are, and the symbol that would say so is two page turns back."""

	def _wide(self, **kwargs):
		return planFor(bigWatchlist(), MONARCH_COLS, **kwargs)

	def test_itIsTheFirstColumnDrawn(self):
		self.assertEqual(self._wide().keyColumn, 1)

	def test_itIsAtTheLeftOfEveryLaterPage(self):
		plan = self._wide()
		for page in range(1, plan.numPages):
			first = plan.onPage(page).placements()[0]
			self.assertEqual(first.column.index, 1)
			self.assertEqual((first.row, first.offset), (0, 0))

	def test_itIsTheSameWidthOnEveryPageItIsRepeatedOn(self):
		"""A column that moves is a column the reader has to find again."""
		plan = self._wide()
		widths = {plan.onPage(page).placements()[0].column.width for page in range(1, plan.numPages)}
		self.assertEqual(widths, {plan.keyWidth})

	def test_itIsNotRepeatedOnItsOwnPage(self):
		plan = self._wide()
		first = [place.column.index for place in plan.onPage(0).placements()]
		self.assertEqual(first.count(1), 1)

	def test_theCopyIsCutRatherThanWrapped(self):
		"""On its own page it is a column and the reader is reading it. On a later page it is
		a label, and a label that grew the row would cost more than it says."""
		plan = self._wide()
		self.assertEqual(plan.onPage(1).placements()[0].column.overflow, TRUNCATE)
		self.assertTrue(plan.onPage(1).placements()[0].column.pinned)

	def test_theCopyCarriesTheTablesOwnColumnNumber(self):
		"""It is a copy in the plan and not in the table: a finger press over it has to route
		into the real cell."""
		pin = self._wide().onPage(1).placements()[0].column
		self.assertEqual(pin.index, 1)

	def test_theColumnItselfIsNotPinned(self):
		plan = self._wide()
		self.assertFalse(plan.onPage(0).placements()[0].column.pinned)

	def test_aTableThatFitsOnOnePageRepeatsNothing(self):
		"""The cost is paid only where the problem exists."""
		self.assertIsNone(planFor(watchlist(), MONARCH_COLS).keyColumn)

	def test_theReaderCanTurnItOff(self):
		self.assertIsNone(self._wide(pinKey=False).keyColumn)

	def test_itCostsPagesAndSaysSo(self):
		"""Honest arithmetic: the repeated column takes cells from every later page, so there
		are more pages with it than without."""
		self.assertGreater(self._wide().numPages, self._wide(pinKey=False).numPages)

	def test_itNeverTakesMoreThanItsShare(self):
		"""A pin wide enough to hold the longest criterion in full would be spending on the
		label what the columns being labelled need."""
		plan = planFor(
			[Measurement(index=n, width=40, typicalWidth=40, label="") for n in range(1, 8)],
			MONARCH_COLS,
		)
		self.assertLessEqual(plan.keyWidth, MONARCH_COLS // KEY_SHARE)

	def test_aBandWithNoRoomBesideItPinsNothing(self):
		"""A pin that left no room to read anything beside it would not be orientation."""
		self.assertIsNone(planFor(bigWatchlist(), 8).keyColumn)
		self.assertIsNotNone(planFor(bigWatchlist(), 12).keyColumn)

	def test_theCaretGoesBackToTheColumnsOwnPageForIt(self):
		"""`pageOf` answers where a column lives, not the pages it is repeated on: the caret
		being in a column means the reader is reading it, and the copy is cut."""
		self.assertEqual(self._wide().pageOf(1), 0)


class TestThePageAssignmentIsAField(unittest.TestCase):
	"""It is about to be a reader's choice — "show these columns together and those apart" is
	exactly this tuple with different contents."""

	def test_aPlanSaysWhichColumnsAreOnWhichPage(self):
		plan = planFor(bigWatchlist(), MONARCH_COLS, pinKey=False)
		self.assertEqual(len(plan.assignment), plan.numPages)
		self.assertEqual(
			[index for page in plan.assignment for index in page],
			[item.index for item in bigWatchlist()],
		)

	def test_anAssignmentIsHonoured(self):
		"""The grouping the reader will ask for, given by hand."""
		plan = planFor(watchlist(), MONARCH_COLS)
		grouped = dataclasses.replace(plan, assignment=((1, 2), (3, 4)))
		self.assertEqual(grouped.numPages, 2)
		self.assertEqual([place.column.index for place in grouped.placements()], [1, 2])

	def test_aPlanWithoutOneStillPacksItself(self):
		"""A plan built by hand, which is what most of these tests build."""
		columns = tuple(Column(index=n, width=6) for n in range(1, 9))
		plan = ColumnPlan(columns=columns, numCols=MONARCH_COLS)
		self.assertGreater(plan.numPages, 1)
		self.assertEqual([place.column.index for place in plan.placements()], [1, 2, 3, 4])
