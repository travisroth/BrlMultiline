# BrlMultiline: which chart the reader meant.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""What can be drawn from this selection, so the reader can say which of them they want.

A selection does not say what chart it is, so the command asks, offering only the chart types
that can be drawn from it, each carrying its numbers. A price chart is offered for four
numeric columns and checked when drawn, so a wrong column order is explained.

Pure. It reads a grid and hands back drawing instructions; the dialog is `__init__.py`'s and
the drawing is each chart module's.
"""

from typing import Callable, NamedTuple, Optional

from . import chart, chartDraw, chartLine, chartPrice, chartSource

import addonHandler

addonHandler.initTranslation()

__all__ = [
	"BARS",
	"CANDLE",
	"HLC",
	"LINE",
	"OHLC",
	"Offer",
	"offerForChart",
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

HLC = "hlc"
"""Three series as high, low, close bars: the price bars with no open tick."""


class Offer(NamedTuple):
	"""One chart that could be drawn from what is selected."""

	key: str
	"""Which chart type, for remembering what the reader chose last time."""

	label: str
	"""What the reader is offered, saying what would actually be drawn."""

	draw: Callable
	"""Draws it, given a buffer factory, a width, a height and a translator.

	A closure over the numbers, so the selection is read once.
	"""


def offersFor(grid: "list[list]") -> "list[Offer]":
	"""Work out which charts this selection could be drawn as.

	Least specialised first.

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

	From the grid first, then from the table when a heading row makes the grid refuse.

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

	Both, so the reader chooses which reads better.

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


def offerForChart(definition: chartSource.ChartDefinition) -> Offer:
	"""The one drawing of a chart its application already defines.

	One and not a list, because the chart says what it is. The dialog `offersFor` feeds exists
	because a selection does not; a chart does, so there is nothing to ask. See
	`docs/design/excel-chart-plan.md`.

	:param definition: the chart, from its application module.
	:return: the drawing on offer.
	:raises chartSource.NoNumbers: with the reason, where it cannot be drawn.
	"""
	if not definition.kind:
		raise chartSource.NoNumbers(
			definition.refusal
			# Translators: reported when a chart of a kind the display cannot draw is asked for.
			# The placeholder is the chart's type, as the screen reader names it: "Scatter".
			or _("{type} charts cannot be drawn yet").format(type=definition.typeName or _("These")),
		)
	series = definition.series
	labels = list(definition.categories)
	if not series or not labels:
		raise chartSource.NoNumbers(_("There are no numbers here to chart"))
	if definition.kind == LINE:
		draw = _chartLines(definition)
	elif definition.kind == BARS:
		draw = _chartBars(definition)
	elif definition.kind in (OHLC, HLC):
		draw = _chartPrices(definition)
	else:
		raise chartSource.NoNumbers(_("{type} charts cannot be drawn yet").format(type=definition.typeName))

	def drawTitled(newBuffer, width, height, translate):
		return fromChart(draw(newBuffer, width, height, translate), definition)

	return Offer(key=definition.kind, label=definition.title, draw=drawTitled)


def _chartLines(definition: chartSource.ChartDefinition) -> Callable:
	""":return: draws a line chart of a chart's series.

	:param definition: the chart.
	:raises chartSource.NoNumbers: for more series than can be told apart, or too few points.
	"""
	if len(definition.series) > chartLine.MAX_LINES:
		raise chartSource.NoNumbers(
			# Translators: reported when a chart has more lines than the display can tell apart.
			# Placeholders are how many it has and how many can be drawn.
			_("This chart has {count} lines; {most} can be told apart").format(
				count=len(definition.series),
				most=chartLine.MAX_LINES,
			),
		)
	if len(definition.categories) < chartLine.MIN_POINTS:
		raise chartSource.NoNumbers(_("There are no numbers here to chart"))
	lines = [
		chartLine.Line(name=one.name, values=list(one.values), key=getattr(one, "key", None))
		for one in definition.series
	]
	labels = list(definition.categories)
	return lambda newBuffer, width, height, translate: chartLine.lineChart(
		newBuffer,
		width,
		height,
		lines,
		labels,
		translate,
	)


def _chartBars(definition: chartSource.ChartDefinition) -> Callable:
	""":return: draws a bar chart of a chart's one series.

	:param definition: the chart.
	:raises chartSource.NoNumbers: for a point with no value, since a bar of nothing would be read
		as a bar of zero.
	"""
	values = definition.series[0].values
	if any(value is None for value in values):
		# Translators: reported when a bar chart has a point with no value, such as an empty cell.
		raise chartSource.NoNumbers(_("A bar here has no value"))
	bars = [chartDraw.Series(label, value) for label, value in zip(definition.categories, values)]
	return lambda newBuffer, width, height, translate: chart.barChart(
		newBuffer,
		width,
		height,
		bars,
		translate,
	)


def _chartPrices(definition: chartSource.ChartDefinition) -> Callable:
	""":return: draws price bars from a stock chart's series, in the order the chart keeps them.

	Open, high, low and close for an open, high, low, close chart; high, low and close for a
	high, low, close chart, whose bars are drawn without the open tick. The order is the one the
	application itself requires of a stock chart, so it is not guessed.

	:param definition: the chart.
	"""
	opens = definition.kind == OHLC
	wanted = 4 if opens else 3
	if len(definition.series) < wanted:
		raise chartSource.NoNumbers(
			# Translators: reported when a stock chart has fewer series than its kind needs.
			_("This stock chart is missing a price series"),
		)
	columns = [one.values for one in definition.series[:wanted]]
	if not opens:
		columns = [[None] * len(definition.categories), *columns]
	periods = [
		chartPrice.Period(label, open_, high, low, close)
		for label, open_, high, low, close in zip(definition.categories, *columns)
	]
	return lambda newBuffer, width, height, translate: chartPrice.ohlcChart(
		newBuffer,
		width,
		height,
		periods,
		translate,
	)


def fromChart(drawing, definition: chartSource.ChartDefinition):
	"""Mark a drawing as made from a chart, and give it the chart's title.

	Every window too: `redraw` and `nextView` are wrapped, so chart navigation can match a point.

	:param drawing: what the chart module drew.
	:param definition: the chart it was drawn from.
	:return: the same drawing.
	"""
	if drawing is None:
		return None
	drawing.chartKey = definition.key
	if definition.title:
		drawing.name = ", ".join(part for part in (definition.title, drawing.name, definition.note) if part)
	redraw = drawing.redraw
	if redraw is not None:
		drawing.redraw = lambda *args, **kwargs: fromChart(redraw(*args, **kwargs), definition)
	nextView = getattr(drawing, "nextView", None)
	if nextView is not None:
		drawing.nextView = lambda *args, **kwargs: fromChart(nextView(*args, **kwargs), definition)
	return drawing
