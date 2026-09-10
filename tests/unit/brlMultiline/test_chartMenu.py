# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for reading a selection as columns, and for what can be charted from it.

Two halves of one question. `chartSource` turns what a grid says is selected into labelled
columns of numbers; `chartMenu` says which charts those columns could be drawn as, so the
reader can be asked rather than guessed at.

The trap this file exists for is the date column. **Excel stores a date as a number**, so a
column of dates is numeric to anything that only looks at what is stored — and a stock chart
whose first series is the dates would be a straight line climbing off the top of the panel,
drawn with complete confidence. What tells them apart is that a date does not *display* as the
number it holds, and the grid already carries both halves.

The second trap is the empty cell. A twenty day moving average has nineteen of them before it
starts, and reading them as zeroes draws an average that dives to the floor and climbs back
out — which looks exactly like a crash.
"""

import os
import sys
import unittest

from ._stubs import installStubs

installStubs()

sys.path.insert(
	0,
	os.path.join(
		os.path.dirname(__file__),
		"..",
		"..",
		"..",
		"addon",
		"brailleDisplayDrivers",
		"brlMultilineMonarch",
	),
)

from pinBuffer import PinBuffer  # noqa: E402

from brlMultiline import chartMenu, chartSource  # noqa: E402
from brlMultiline.chartDraw import numberText  # noqa: E402


def cell(value):
	""":return: one `(text, value)` pair, the way a grid answers.

	A string is a text cell, a number is a cell displaying the number it holds, and None is an
	empty cell.
	"""
	if value is None:
		return ("", None)
	if isinstance(value, str):
		return (value, value)
	return (numberText(value), value)


def shown(text, value):
	""":return: a cell displaying one thing and holding another — a date, or a percentage."""
	return (text, value)


def sheet(*rows):
	""":return: a grid of `(text, value)` rows."""
	return [[cell(value) if not isinstance(value, tuple) else value for value in row] for row in rows]


def newBuffer(width, height):
	""":return: a buffer, standing in for the display's own factory."""
	return PinBuffer(width, height)


def spell(text):
	""":return: one cell per character, dot 1 raised."""
	return [0b00000001 for _ in text]


PRICES = sheet(
	["Date", "Open", "High", "Low", "Close"],
	[shown("3 Jun", 45806), 10, 12, 9, 11],
	[shown("4 Jun", 45807), 11, 14, 11, 13],
	[shown("5 Jun", 45808), 13, 13, 10, 10],
)
"""Four days of prices with a heading row, laid out the way a reader would have them."""


class TestReadingTheColumns(unittest.TestCase):
	def test_aRowWithNoNumbersInItIsAHeadingRow(self):
		"""Anything subtler — matching known words, looking at formatting — would be a rule that
		works on the sheets it was written against. A row of headings has no numbers in it and a
		row of data has at least one, which is true of every table a reader would think to
		select."""
		table = chartSource.tableFromGrid(PRICES)
		self.assertEqual([column.name for column in table.columns], ["Open", "High", "Low", "Close"])
		self.assertEqual(table.rowCount, 3)

	def test_aFirstRowWithANumberInItIsData(self):
		table = chartSource.tableFromGrid(sheet(["Mon", 5], ["Tue", 10]))
		self.assertEqual(table.labels, ["Mon", "Tue"])
		self.assertEqual(table.columns[0].values, [5.0, 10.0])

	def test_aDateColumnIsLabelsAndNotASeries(self):
		"""The fault worth having a test for. Excel stores 3 June as 45806, so a column of dates
		is numeric to anything that only looks at what is stored, and charting it would draw a
		fifth line climbing off the top of the panel."""
		table = chartSource.tableFromGrid(PRICES)
		self.assertEqual(table.labels, ["3 Jun", "4 Jun", "5 Jun"])
		self.assertEqual(len(table.columns), 4)

	def test_aTextColumnIsLabels(self):
		table = chartSource.tableFromGrid(sheet(["Mon", 5], ["Tue", 10]))
		self.assertEqual(table.labels, ["Mon", "Tue"])

	def test_columnsOfPlainNumbersAreAllData(self):
		"""Nothing here says the first of them is an axis, and taking it away would silently
		drop a series the reader selected."""
		table = chartSource.tableFromGrid(sheet([1, 5], [2, 10]))
		self.assertIsNone(table.labels)
		self.assertEqual(len(table.columns), 2)

	def test_anEmptyCellIsAGapAndNotAZero(self):
		"""A twenty day moving average has nineteen of them before it starts."""
		table = chartSource.tableFromGrid(sheet(["a", None], ["b", 5], ["c", 10]))
		self.assertEqual(table.columns[0].values, [None, 5.0, 10.0])

	def test_aColumnWithTextAmongTheNumbersIsNotAColumnOfNumbers(self):
		table = chartSource.tableFromGrid(sheet(["a", 1, 5], ["b", "x", 10], ["c", 3, 15]))
		self.assertEqual(len(table.columns), 1)

	def test_aColumnWithOneNumberInItIsNotASeries(self):
		"""A stray figure beside a table is not a column the reader meant to chart."""
		table = chartSource.tableFromGrid(sheet(["a", None, 5], ["b", 2, 10], ["c", None, 15]))
		self.assertEqual(len(table.columns), 1)

	def test_aColumnWithNoHeadingIsCalledByItsPosition(self):
		table = chartSource.tableFromGrid(sheet(["a", 1], ["b", 2]))
		self.assertEqual(table.columns[0].name, "column 2")

	def test_nothingNumericRefuses(self):
		with self.assertRaises(chartSource.NoNumbers):
			chartSource.tableFromGrid(sheet(["a", "x"], ["b", "y"]))

	def test_aVeryLargeSelectionIsCutRatherThanRead(self):
		"""A stray Ctrl+Space selects a million cells, and turning them all into tuples would
		stop NVDA long before any chart refused them."""
		grid = sheet(*[[index, float(index)] for index in range(chartSource.MAX_POINTS + 50)])
		self.assertEqual(chartSource.tableFromGrid(grid).rowCount, chartSource.MAX_POINTS)


class TestReadingPrices(unittest.TestCase):
	def test_theHeadingsSayWhichColumnIsWhich(self):
		periods = chartSource.periodsFrom(chartSource.tableFromGrid(PRICES))
		self.assertEqual(len(periods), 3)
		self.assertEqual(periods[0].label, "3 Jun")
		self.assertEqual((periods[0].open, periods[0].high), (10.0, 12.0))

	def test_headingsAreMatchedInAnyOrder(self):
		"""A reader who has labelled their columns has already said which is which, and should
		not have to reorder their sheet to be charted."""
		grid = sheet(
			["Close", "Open", "Low", "High"],
			[11, 10, 9, 12],
			[13, 11, 11, 14],
		)
		periods = chartSource.periodsFrom(chartSource.tableFromGrid(grid))
		self.assertEqual((periods[0].open, periods[0].close), (10.0, 11.0))

	def test_withoutHeadingsTheOrderIsTheOneEveryoneWrites(self):
		grid = sheet(["Mon", 10, 12, 9, 11], ["Tue", 11, 14, 11, 13])
		periods = chartSource.periodsFrom(chartSource.tableFromGrid(grid))
		self.assertEqual(
			(periods[0].open, periods[0].high, periods[0].low, periods[0].close),
			(10.0, 12.0, 9.0, 11.0),
		)

	def test_aVolumeColumnAfterTheFourIsLeftOut(self):
		grid = sheet(["Mon", 10, 12, 9, 11, 50000], ["Tue", 11, 14, 11, 13, 60000])
		periods = chartSource.periodsFrom(chartSource.tableFromGrid(grid))
		self.assertEqual(periods[0].close, 11.0)

	def test_columnsInTheWrongOrderAreRefusedRatherThanDrawn(self):
		"""Drawn anyway they would be a chart of nothing, and a plausible one. The message names
		the period that gave it away, because that is what the reader has to go and look at."""
		grid = sheet(["Mon", 12, 10, 9, 11], ["Tue", 14, 11, 11, 13])
		with self.assertRaises(chartSource.NoNumbers) as caught:
			chartSource.periodsFrom(chartSource.tableFromGrid(grid))
		self.assertIn("Mon", str(caught.exception))

	def test_aRowMissingAPriceIsSkippedRatherThanRefused(self):
		"""A blank row in the middle of a year of prices is a holiday."""
		grid = sheet(["Mon", 10, 12, 9, 11], ["Tue", None, None, None, None], ["Wed", 11, 14, 11, 13])
		periods = chartSource.periodsFrom(chartSource.tableFromGrid(grid))
		self.assertEqual([period.label for period in periods], ["Mon", "Wed"])

	def test_tooFewColumnsToBePrices(self):
		with self.assertRaises(chartSource.NoNumbers):
			chartSource.periodsFrom(chartSource.tableFromGrid(sheet(["a", 1], ["b", 2])))


class TestWhatIsOffered(unittest.TestCase):
	"""A selection does not say what chart it is, so the reader is asked — and only about the
	charts that will actually draw from what they selected."""

	def keys(self, grid):
		""":return: the chart types on offer for a selection."""
		return [offer.key for offer in chartMenu.offersFor(grid)]

	def test_aColumnOfNumbersOffersBarsAndALine(self):
		self.assertEqual(
			self.keys(sheet(["Mon", 5], ["Tue", 10], ["Wed", 15])),
			[chartMenu.BARS, chartMenu.LINE],
		)

	def test_fourPriceColumnsOfferBothPriceCharts(self):
		"""The same four numbers drawn two ways, and which reads better under a finger is the
		question the reader is being invited to answer. Offering one would decide it for them."""
		self.assertEqual(
			self.keys(PRICES),
			[chartMenu.BARS, chartMenu.LINE, chartMenu.OHLC, chartMenu.CANDLE],
		)

	def test_aSingleValueOffersBarsAndNoLine(self):
		"""One point is a bar; it is not a line."""
		self.assertEqual(self.keys(sheet(["Mon", 5])), [chartMenu.BARS])

	def test_nothingNumericOffersNothing(self):
		self.assertEqual(self.keys(sheet(["a", "x"], ["b", "y"])), [])

	def test_anOfferSaysWhatWouldActuallyBeDrawn(self):
		"""The list is what the reader chooses from, so each entry has to carry enough to choose
		on: how many bars, which series, how many periods."""
		offers = chartMenu.offersFor(PRICES)
		labels = {offer.key: offer.label for offer in offers}
		self.assertIn("3", labels[chartMenu.BARS])
		self.assertIn("Close", labels[chartMenu.LINE])
		self.assertIn("3 periods", labels[chartMenu.OHLC])

	def test_anOfferDrawsWhatItSaid(self):
		offers = {offer.key: offer for offer in chartMenu.offersFor(PRICES)}
		for key in (chartMenu.BARS, chartMenu.LINE, chartMenu.OHLC, chartMenu.CANDLE):
			drawing = offers[key].draw(newBuffer, 96, 35, spell)
			self.assertEqual((drawing.width, drawing.height), (96, 35))
			self.assertIn("O", "".join(drawing.buffer.rows()))

	def test_aPriceChartIsOfferedAndThenExplainsItself(self):
		"""Offered whenever there are four numeric columns, with the check that they really are
		open, high, low and close left until it is drawn. Offered and then explained beats not
		offered and unexplained: a reader who selected their columns in the wrong order gets
		told so, which is a thing they can fix."""
		grid = sheet(["Mon", 12, 10, 9, 11], ["Tue", 14, 11, 11, 13])
		offers = {offer.key: offer for offer in chartMenu.offersFor(grid)}
		self.assertIn(chartMenu.OHLC, offers)
		with self.assertRaises(chartSource.NoNumbers):
			offers[chartMenu.OHLC].draw(newBuffer, 96, 35, spell)

	def test_theLineChartCarriesTheLabelsIntoTheDrawing(self):
		drawing = {offer.key: offer for offer in chartMenu.offersFor(PRICES)}[chartMenu.LINE].draw(
			newBuffer,
			96,
			35,
			spell,
		)
		self.assertIn("3 Jun", drawing.describeAt(0, 20))


class FakeSheet:
	"""A grid that answers what is selected, and remembers what it was asked for."""

	def __init__(self, answer):
		"""
		:param answer: what to return, or an exception to raise.
		"""
		self.answer = answer
		self.asked = None

	def selectedValues(self, maxRows=0, maxColumns=0):
		self.asked = (maxRows, maxColumns)
		if isinstance(self.answer, Exception):
			raise self.answer
		return self.answer


class TestReadingWhatIsSelected(unittest.TestCase):
	"""What the command asks a grid for, and what it does with the ways that can fail.

	The failures are the point. A selection can be enormous — Ctrl+Space is a million cells —
	and a grid asked for one without a limit froze NVDA for ten seconds on hardware before the
	COM call was cancelled out from under it. So the caller says how much it can use, and a
	cancelled call is told apart from an empty selection, because they are different things for
	the reader to do about: press again, against go and select something.
	"""

	def gridFrom(self, answer):
		""":return: what `gridFromFocus` makes of a grid that answers this.

		:param answer: what the grid returns, or an exception it raises.
		"""
		sheet = FakeSheet(answer)
		original = chartSource._focusedSheet
		chartSource._focusedSheet = lambda: (None, sheet)
		try:
			return sheet, chartSource.gridFromFocus()
		finally:
			chartSource._focusedSheet = original

	def test_theCallerSaysHowManyRowsItCanUse(self):
		"""Reading more than the chart can draw is time spent on numbers that get thrown away,
		and on a whole column selection it is a great deal of time."""
		asked, _grid = self.gridFrom(sheet(["Mon", 5], ["Tue", 10]))
		self.assertEqual(asked.asked[0], chartSource.MAX_POINTS)

	def test_aCancelledCallIsNotAnEmptySelection(self):
		"""NVDA gave up on the application, which is not the application having nothing to say.
		Reported as itself so the reader presses again rather than going to look at a selection
		that was fine all along."""
		with self.assertRaises(chartSource.NoNumbers) as caught:
			self.gridFrom(chartSource.CallCancelled("COM call cancelled"))
		self.assertIn("in time", str(caught.exception))

	def test_aGridThatSimplyFailsIsNothingSelected(self):
		with self.assertRaises(chartSource.NoNumbers) as caught:
			self.gridFrom(RuntimeError("no"))
		self.assertIn("selected", str(caught.exception))

	def test_anEmptySelectionSaysSo(self):
		with self.assertRaises(chartSource.NoNumbers):
			self.gridFrom([])

	def test_somewhereWithNoGridSaysWhatTheReaderIsOn(self):
		"""A refusal that can name what they were on is one they can act on: "charts need a
		spreadsheet cell; this is a list item" says where to go."""
		original = chartSource._focusedSheet
		chartSource._focusedSheet = lambda: (None, None)
		try:
			with self.assertRaises(chartSource.NoNumbers) as caught:
				chartSource.gridFromFocus()
		finally:
			chartSource._focusedSheet = original
		self.assertIn("spreadsheet", str(caught.exception))
