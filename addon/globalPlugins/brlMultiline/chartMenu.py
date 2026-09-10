# BrlMultiline: which chart the reader meant.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""What can be drawn from this selection, so the reader can say which of them they want.

**A selection does not say what chart it is.** Four columns of numbers with dates down the
side are a line chart if they are four measurements, and a candlestick chart if they are one
day's trading; nothing in the cells distinguishes those, and guessing would be wrong often
enough to be worse than asking. So the command asks, and this module is what it asks about:
the chart types that can actually be drawn from what is selected, each already carrying the
numbers it would draw.

Two things follow from doing it this way rather than with a chart type setting.

1. **The reader is only offered what will work.** A selection of one column of sales figures
   offers bars and a line and nothing else, so the list is short and every entry in it is a
   chart they can have. A setting would offer candlesticks over a column of sales and fail
   afterwards.
2. **The refusal still arrives when it is useful.** A price chart is offered whenever there
   are four numeric columns, and the check that they really are open, high, low and close
   happens when it is drawn. Offered and then explained beats not offered and unexplained:
   a reader who selected their columns in the wrong order gets told so, which is a thing they
   can fix.

Pure. It reads a grid and hands back drawing instructions; the dialog is `__init__.py`'s and
the drawing is each chart module's.
"""

from typing import Callable, NamedTuple, Optional

from . import chart, chartLine, chartPrice, chartSource

__all__ = [
	"BARS",
	"CANDLE",
	"LINE",
	"OHLC",
	"Offer",
	"offersFor",
]

BARS = "bars"
"""One column of numbers as bars standing on a baseline."""

LINE = "line"
"""Several columns as lines over a shared period."""

OHLC = "ohlc"
"""Four columns as open, high, low, close bars."""

CANDLE = "candle"
"""The same four columns as candlesticks."""

class Offer(NamedTuple):
	"""One chart that could be drawn from what is selected."""

	key: str
	"""Which chart type, for remembering what the reader chose last time."""

	label: str
	"""What the reader is offered, saying what would actually be drawn."""

	draw: Callable
	"""Draws it, given a buffer factory, a width, a height and a translator.

	A closure over the numbers rather than a chart type to dispatch on later, so the reading
	of the selection happens once and the command that shows the dialog does not have to know
	what any chart type needs.
	"""


def offersFor(grid: "list[list]") -> "list[Offer]":
	"""Work out which charts this selection could be drawn as.

	In the order they are offered, which is least specialised first: bars are what most
	selections are for, and a reader who wanted a candlestick chart knows they did and will
	read the list.

	:param grid: rows of `(text, value)`, as `Sheet.selectedValues` gives them.
	:return: the charts on offer, which may be empty.
	"""
	table = _table(grid)
	offers = list(_barOffer(grid, table))
	if table is not None:
		offers.extend(_lineOffer(table))
		offers.extend(_priceOffers(table))
	return offers


def _table(grid: "list[list]") -> Optional[chartSource.Table]:
	""":return: the selection as columns, or None if it holds no numbers.

	:param grid: the selection.
	"""
	try:
		return chartSource.tableFromGrid(grid)
	except chartSource.NoNumbers:
		return None


def _barOffer(grid: "list[list]", table: Optional[chartSource.Table]) -> "list[Offer]":
	"""The bar chart, if one can be made, read whichever of the two ways works.

	Straight from the grid first, because that path's rules about which column is charted and
	which names the bars are the ones a reader has already learned. From the table when that
	refuses, which is what a heading row does to it: a column with "Close" at the top of it is
	not a column of numbers, and a selection with headings would otherwise offer every chart
	except the simplest one.

	:param grid: the selection.
	:param table: the same selection as columns, or None if it holds no numbers.
	:return: the offer, or nothing.
	"""
	try:
		series = chartSource.seriesFromGrid(grid)
	except chartSource.NoNumbers:
		series = chartSource.seriesFromTable(table) if table is not None else []
	if not series:
		return []
	return [
		Offer(
			key=BARS,
			# Translators: an entry in the list of charts a selection can be drawn as. The
			# placeholder is how many bars the chart would have.
			label=_("Bar chart, {count} bars").format(count=len(series)),
			draw=lambda newBuffer, width, height, translate: chart.barChart(
				newBuffer,
				width,
				height,
				series,
				translate,
			),
		),
	]


def _lineOffer(table: chartSource.Table) -> "list[Offer]":
	""":return: the line chart, if one can be made.

	:param table: the selection as columns.
	"""
	if table.rowCount < chartLine.MIN_POINTS:
		return []
	lines = [chartLine.Line(name=column.name, values=column.values) for column in table.columns]
	names = ", ".join(line.name for line in lines)
	return [
		Offer(
			key=LINE,
			# Translators: an entry in the list of charts a selection can be drawn as.
			# Placeholders are the names of the series and how many points they cover.
			label=_("Line chart, {names}, {count} points").format(
				names=names,
				count=table.rowCount,
			),
			draw=lambda newBuffer, width, height, translate: chartLine.lineChart(
				newBuffer,
				width,
				height,
				lines,
				table.labels,
				translate,
			),
		),
	]


def _priceOffers(table: chartSource.Table) -> "list[Offer]":
	"""The two price charts, offered together or not at all.

	They are the same four numbers drawn two ways, and which of them reads better under a
	finger is the question the reader is being invited to answer. Offering one of them would
	make that choice for them.

	:param table: the selection as columns.
	:return: the offers.
	"""
	if len(table.columns) < len(chartSource.PRICE_NAMES):
		return []
	count = table.rowCount

	def periods() -> "list[chartPrice.Period]":
		"""Read the four price columns, refusing here rather than at the offer.

		:return: the periods.
		"""
		return chartSource.periodsFrom(table)

	return [
		Offer(
			key=OHLC,
			# Translators: an entry in the list of charts a selection can be drawn as. The
			# placeholder is how many periods the chart would cover.
			label=_("Open, high, low, close bars, {count} periods").format(count=count),
			draw=lambda newBuffer, width, height, translate: chartPrice.ohlcChart(
				newBuffer,
				width,
				height,
				periods(),
				translate,
			),
		),
		Offer(
			key=CANDLE,
			# Translators: an entry in the list of charts a selection can be drawn as. The
			# placeholder is how many periods the chart would cover.
			label=_("Candlesticks, {count} periods").format(count=count),
			draw=lambda newBuffer, width, height, translate: chartPrice.candleChart(
				newBuffer,
				width,
				height,
				periods(),
				translate,
			),
		),
	]
