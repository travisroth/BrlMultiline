# BrlMultiline: open, high, low, close charts.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Four numbers per period, drawn so a finger gets all four in one touch.

A price chart is the case that makes a tactile display worth the trouble. Four numbers a day
over a month is a hundred and twenty numbers, and read as a table that is a hundred and twenty
things to hold in mind; drawn, it is a shape with a texture, and the questions a trader
actually asks — is this trending, is the range widening, did it close near its high — are
answered by the hand rather than by the memory.

**Why the OHLC bar shape works here at all.** It is made of exactly the three strokes this
resolution can carry: a vertical stem for the day's range, a tick left for where it opened, a
tick right for where it closed. Three pins wide is enough for all three, so a ninety-six pin
panel holds twenty-four periods — about a trading month. Nothing about it depends on colour,
on line weight, or on hue, which is why it survives the translation to pins better than most
chart types drawn for the eye do. The ticks point the way they do for a reason a finger can
use: sweeping left to right along the row of stems, a tick met **before** its stem is an open
and one met **after** it is a close.

**Candlesticks are the same data with a different bet.** Instead of two ticks they draw a
solid body between open and close, with the wick above and below. The body is a mass rather
than a stroke, which is easier to find and gives the size of the day's move directly; the cost
is that rising and falling have to be told apart by the body being hollow rather than filled,
and at three pins wide a hollow body is one column of gap. Whether that reads under a finger
is a hardware question, so both are offered and the reader decides.

Knows nothing about NVDA, Excel, or any display.
"""

from typing import Callable, NamedTuple, Optional

from .chartDraw import (
	GAP,
	ChartRefused,
	PointMark,
	Scale,
	isFinite,
	numberText,
	textRowsFor,
	windowOf,
	writeFrame,
)
from .graphicsMode import Drawing

__all__ = [
	"MIN_SLOT",
	"Period",
	"candleChart",
	"ohlcChart",
]

MIN_SLOT = 4
"""Pins per period, including the gap after it, below which a price chart is refused.

Three pins of bar and one of gap. Three is not a preference: it is the narrowest shape that
can carry a stem with something on each side of it, and a bar with its ticks on top of its
stem is a bar with no open and no close in it. Twenty-four periods on a ninety-six pin panel,
which is about a trading month.
"""

MIN_PLOT = 4
"""Pin rows the plot must keep for the shape to mean anything."""


class Period(NamedTuple):
	"""One period's four prices, and what to call it."""

	label: str
	"""The date, usually: what the reader sees in the sheet."""

	open: Optional[float]
	"""What it opened at. None for a high, low, close chart, which has no open to draw: its bars
	are drawn without the left hand tick, and a press or a step says the other three."""

	high: float
	"""The highest it reached."""

	low: float
	"""The lowest."""

	close: float
	"""What it closed at."""


def ohlcChart(
	newBuffer: Callable,
	width: int,
	height: int,
	periods: "list[Period]",
	translate: Optional[Callable] = None,
) -> Drawing:
	"""Draw open, high, low and close bars to fill a rectangle exactly.

	:param newBuffer: makes a blank buffer of a given width and height.
	:param width: the rectangle's width in pins.
	:param height: its height in pins.
	:param periods: the periods, in the order they go left to right.
	:param translate: turns a string into braille cells. None draws the chart bare.
	:return: the drawing, which answers with all four prices when pointed at.
	:raises ChartRefused: if there is nothing to chart or no room to chart it.
	"""
	return _priceChart(newBuffer, width, height, periods, translate, _drawBar, _ohlcName)


def candleChart(
	newBuffer: Callable,
	width: int,
	height: int,
	periods: "list[Period]",
	translate: Optional[Callable] = None,
) -> Drawing:
	"""Draw the same four numbers as candlesticks.

	:param newBuffer: makes a blank buffer of a given width and height.
	:param width: the rectangle's width in pins.
	:param height: its height in pins.
	:param periods: the periods, in the order they go left to right.
	:param translate: turns a string into braille cells. None draws the chart bare.
	:return: the drawing, which answers with all four prices when pointed at.
	:raises ChartRefused: if there is nothing to chart or no room to chart it.
	"""
	if any(period.open is None for period in periods):
		# A candle's body runs from the open to the close, so there is nothing to draw it from.
		# Translators: reported when candlesticks are asked for over prices with no open.
		raise ChartRefused(_("Candlesticks need an open price for every period"))
	return _priceChart(newBuffer, width, height, periods, translate, _drawCandle, _candleName)


def _priceChart(
	newBuffer: Callable,
	width: int,
	height: int,
	periods: "list[Period]",
	translate: Optional[Callable],
	drawOne: Callable,
	name: Callable,
	firstPoint: int = 0,
	widen: bool = True,
) -> Drawing:
	"""Lay out and draw a price chart, whichever shape each period is drawn in.

	The two shapes differ only in what is drawn inside one period's slot. Everything else —
	the slot arithmetic, the shared scale over every high and low, the frame, what a press
	answers — is the same for both, and has to be: a reader comparing an OHLC chart with a
	candlestick chart of the same data is comparing shapes, not scales.

	:param newBuffer: makes a blank buffer of a given width and height.
	:param width: the rectangle's width in pins.
	:param height: its height in pins.
	:param periods: what to draw.
	:param translate: turns a string into braille cells.
	:param drawOne: draws one period, given the buffer, the period and its slot.
	:param name: says what the chart is called, given the periods it holds.
	:param firstPoint: which period of the whole chart the first of these is, for a window.
	:param widen: whether more periods than fit make a chart too wide to show whole rather than
		a refusal. True for the whole chart and False for a window of it, which has to fit.
	:return: the drawing.
	:raises ChartRefused: if there is nothing to chart or no room to chart it.
	"""
	if not periods:
		# Translators: reported when a chart was asked for with no numbers to chart.
		raise ChartRefused(_("There are no numbers here to chart"))
	if not all(
		isFinite(number)
		for period in periods
		for number in (period.high, period.low, period.close)
		+ (() if period.open is None else (period.open,))
	):
		# Refused rather than skipped: a period is four numbers that have to agree, and one of
		# them missing is not a shorter bar, it is a bar nobody can read.
		# Translators: reported when a chart was asked for over a cell holding an error, an
		# overflow or a division by zero.
		raise ChartRefused(_("There is a value here that is not a number"))
	if width < MIN_SLOT or height < MIN_PLOT:
		# Translators: reported when the space for a drawing is too small for a chart.
		raise ChartRefused(_("There is not enough room here for a chart"))
	slot = width // len(periods)
	if slot < MIN_SLOT and widen:
		return _tooWide(newBuffer, width, height, periods, translate, drawOne, name)
	if slot < MIN_SLOT:
		raise ChartRefused(
			# Translators: reported when a price chart has more periods than the display can
			# show. Placeholders are how many were asked for and how many would fit.
			_("{asked} periods will not fit; this display holds {fits}").format(
				asked=len(periods),
				fits=width // MIN_SLOT,
			),
		)
	buffer = newBuffer(width, height)
	if buffer is None:
		# Translators: reported when the display would not provide a surface to draw on.
		raise ChartRefused(_("The display would not provide a drawing surface"))
	textRows = textRowsFor(height, translate, MIN_PLOT)
	scale = Scale.forValues(
		[value for period in periods for value in (period.high, period.low)],
		top=textRows,
		bottom=height - 1 - textRows,
	)
	edges = _edges(len(periods), width)
	for index, period in enumerate(periods):
		left = edges[index]
		drawOne(buffer, period, scale, left, max(1, edges[index + 1] - left - GAP))
	if textRows:
		writeFrame(buffer, translate, scale, periods[0].label, periods[-1].label)
	return Drawing(
		buffer,
		name=name(periods),
		describeAt=_describer(periods, width),
		redraw=_reframer(newBuffer, width, height, periods, translate, drawOne, name),
		points=len(periods),
		firstPoint=firstPoint,
		pointAt=_pointFinder(len(periods), width),
		markFor=_marker(periods, edges, scale),
		sayPoint=lambda index: _say(periods[index]),
	)


def _tooWide(
	newBuffer: Callable,
	width: int,
	height: int,
	periods: "list[Period]",
	translate: Optional[Callable],
	drawOne: Callable,
	name: Callable,
) -> Drawing:
	"""The whole of a price chart with more periods than the panel holds.

	**Shown a window at a time rather than refused.** A bar needs four pins, so ninety-six hold
	twenty-four days, and a year of prices was refused outright with a message the dialog closing
	talked over. Panning and stepping already move a window over a chart; this lets them. The
	drawing says how many periods fit, and the mode opens it at that view on the latest of them.

	Nothing of this drawing is shown: the mode never zooms out past the widest view. It is the
	frame the windows are cut from, so its width, its points and where it says each period sits
	are what the mode's origin and a marked period are measured in. The windows are drawn by
	`_reframer`, each to its own scale, as for any other window.

	:param newBuffer: makes a blank buffer.
	:param width: the rectangle's width in pins.
	:param height: its height in pins.
	:param periods: all of them.
	:param translate: turns a string into braille cells.
	:param drawOne: draws one period.
	:param name: says what the chart is called.
	:return: the drawing.
	:raises ChartRefused: if the display would not provide a buffer.
	"""
	buffer = newBuffer(width, height)
	if buffer is None:
		raise ChartRefused(_("The display would not provide a drawing surface"))
	textRows = textRowsFor(height, translate, MIN_PLOT)
	scale = Scale.forValues(
		[value for period in periods for value in (period.high, period.low)],
		top=textRows,
		bottom=height - 1 - textRows,
	)
	return Drawing(
		buffer,
		name=name(periods),
		describeAt=_describer(periods, width),
		redraw=_reframer(newBuffer, width, height, periods, translate, drawOne, name),
		points=len(periods),
		pointAt=_pointFinder(len(periods), width),
		markFor=_marker(periods, _edges(len(periods), width), scale),
		sayPoint=lambda index: _say(periods[index]),
		widestView=width // MIN_SLOT,
	)


def _edges(count: int, width: int) -> "list[int]":
	"""Place the periods across the whole width.

	**The last period ends at the right edge**, rather than the periods each taking a slot of
	`width // count` and the remainder being left blank. Twenty periods on ninety-six pins is a
	slot of four and sixteen pins of nothing, which would put the last date — written along the
	bottom, right aligned to the panel — two braille cells past the bar it names. The spread
	costs nothing: some slots come out a pin wider than others, which no finger can tell, and it
	puts a price chart on the same footing as a line chart of the same period so the two can be
	compared.

	:param count: how many periods there are.
	:param width: the drawing's width in pins.
	:return: the left edge of each period, and one past the last.
	"""
	return [index * width // count for index in range(count + 1)]


def _reframer(
	newBuffer: Callable,
	width: int,
	height: int,
	periods: "list[Period]",
	translate: Optional[Callable],
	drawOne: Callable,
	name: Callable,
) -> Callable:
	"""Build the closure that draws this chart again for part of its periods.

	A month of days magnified twice is a fortnight of fat blobs; drawn again it is a fortnight
	of bars, each with a stem and two ticks the reader can actually feel, and with the right
	two dates written under the ends. The numbers never went anywhere, so there is no reason to
	settle for the blobs.

	:param newBuffer: makes a blank buffer.
	:param width: the rectangle's width in pins.
	:param height: its height in pins.
	:param periods: all of them.
	:param translate: turns a string into braille cells.
	:param drawOne: draws one period.
	:param name: says what the chart is called.
	:return: a function taking where the window starts and how wide it is as fractions,
		and the size to compose for. The size comes with the window because the
		rectangle can change under a figure that is already up: toggling the braille
		line beside the drawing is a command.
	"""

	def redraw(offset: float, span: float, pinWidth: int, pinHeight: int) -> Optional[Drawing]:
		first, last = windowOf(len(periods), offset, span, 1)
		try:
			return _priceChart(
				newBuffer,
				pinWidth,
				pinHeight,
				periods[first:last],
				translate,
				drawOne,
				name,
				first,
				widen=False,
			)
		except ChartRefused:
			return None

	return redraw


def _drawBar(buffer, period: Period, scale: Scale, left: int, barWidth: int) -> None:
	"""Draw one period as an open, high, low, close bar.

	:param buffer: what to draw on.
	:param period: its four prices.
	:param scale: the shared value to row mapping.
	:param left: the leftmost column of its slot.
	:param barWidth: how many columns it may use.
	"""
	stem = left + barWidth // 2
	buffer.line(stem, scale.row(period.high), stem, scale.row(period.low))
	if stem > left and period.open is not None:
		buffer.line(left, scale.row(period.open), stem - 1, scale.row(period.open))
	right = left + barWidth - 1
	if right > stem:
		buffer.line(stem + 1, scale.row(period.close), right, scale.row(period.close))


def _drawCandle(buffer, period: Period, scale: Scale, left: int, barWidth: int) -> None:
	"""Draw one period as a candlestick.

	The wick is drawn only where the body is not, rather than up the whole range with the body
	over it. Drawn through, it would fill the one column of gap that makes a rising body
	hollow, and every candle would read as a falling one.

	:param buffer: what to draw on.
	:param period: its four prices.
	:param scale: the shared value to row mapping.
	:param left: the leftmost column of its slot.
	:param barWidth: how many columns it may use.
	"""
	stem = left + barWidth // 2
	top = min(scale.row(period.open), scale.row(period.close))
	bottom = max(scale.row(period.open), scale.row(period.close))
	highRow = scale.row(period.high)
	lowRow = scale.row(period.low)
	if highRow < top:
		buffer.line(stem, highRow, stem, top - 1)
	if lowRow > bottom:
		buffer.line(stem, bottom + 1, stem, lowRow)
	# Hollow where the period rose, solid where it fell: the convention the sighted chart uses,
	# and the only one of its two signals — hollow against filled, and green against red — that
	# survives having neither colour nor greyscale.
	buffer.rect(left, top, barWidth, bottom - top + 1, filled=period.close < period.open)


def _describer(periods: "list[Period]", width: int) -> Callable:
	"""Build the lookup from a source dot back to the period under it.

	All four prices, not the one nearest the finger. A press on a price chart is the reader
	asking about that day, and answering "high 51.2" alone would make them press three more
	times for the rest of a day they already had their finger on.

	:param periods: what was drawn.
	:param width: the drawing's width in pins, which is what `_edges` divided up.
	:return: a function taking source coordinates and returning what is there.
	"""

	def describeAt(x: int, y: int) -> Optional[str]:
		# The exact inverse of `_edges`, so a press anywhere in a period's slot — bar, tick
		# or the gap after it — is that period's press.
		index = max(0, min(len(periods) - 1, x * len(periods) // width if width else 0))
		return _say(periods[index])

	return describeAt


def _say(period: Period) -> str:
	""":return: what a period is, for a press on it or a step onto it.

	:param period: the period.
	"""
	if period.open is None:
		# Translators: reported for a touch on a period of a high, low, close chart, or a step
		# onto it. Placeholders are what the period is called and its three prices.
		return _("{label}, high {high}, low {low}, close {close}").format(
			label=period.label,
			high=numberText(period.high),
			low=numberText(period.low),
			close=numberText(period.close),
		)
	# Translators: reported for a touch on a period of a price chart, or a step onto it.
	# Placeholders are what the period is called and its four prices.
	return _("{label}, open {open}, high {high}, low {low}, close {close}").format(
		label=period.label,
		open=numberText(period.open),
		high=numberText(period.high),
		low=numberText(period.low),
		close=numberText(period.close),
	)


def _pointFinder(count: int, width: int) -> Callable:
	"""Build the lookup from a column to the period whose slot it is in.

	The same inverse of `_edges` that a press uses, so a step and a press agree on which period a
	column belongs to.

	:param count: how many periods were drawn.
	:param width: the drawing's width in pins.
	:return: a function taking a column and returning a period's index, or None off the drawing.
	"""

	def pointAt(x: int) -> Optional[int]:
		if not width or not 0 <= x < width:
			return None
		return min(count - 1, x * count // width)

	return pointAt


def _marker(periods: "list[Period]", edges: "list[int]", scale: Scale) -> Callable:
	"""Build the lookup from a period to where its guides go.

	The point line goes through the stem, and the level line is at the close: the price a
	period is most often compared by, and the one its right hand tick already stands for.

	:param periods: what was drawn.
	:param edges: from `_edges`.
	:param scale: the chart's scale.
	:return: a function taking a period's index, and a level that a price chart ignores, and
		returning a `PointMark`.
	"""

	def markFor(index: int, level: int = 0) -> Optional[PointMark]:
		if not 0 <= index < len(periods):
			return None
		left = edges[index]
		barWidth = max(1, edges[index + 1] - left - GAP)
		period = periods[index]
		return PointMark(
			column=left + barWidth // 2,
			row=scale.row(period.close),
			plotTop=scale.top,
			plotBottom=scale.bottom,
			value=period.close,
			# Translators: what the level line follows on a price chart: each period's close.
			name=_("close"),
		)

	return markFor


def _ohlcName(periods: "list[Period]") -> str:
	""":return: what to call an open, high, low, close chart.

	:param periods: what it holds.
	"""
	if all(period.open is None for period in periods):
		# Translators: the name of a high, low, close chart on the display. Placeholders are
		# how many periods it covers and the first and last of them.
		return _("high, low, close chart, {count} periods, {first} to {last}").format(
			count=len(periods),
			first=periods[0].label,
			last=periods[-1].label,
		)
	# Translators: the name of an open, high, low, close chart on the display. Placeholders are
	# how many periods it covers and the first and last of them.
	return _("open, high, low, close chart, {count} periods, {first} to {last}").format(
		count=len(periods),
		first=periods[0].label,
		last=periods[-1].label,
	)


def _candleName(periods: "list[Period]") -> str:
	""":return: what to call a candlestick chart.

	**The dates are in the name because the zoom moves them.** A redrawn window writes its own
	two dates on the panel, and saying them as well is what tells the reader the zoom did
	something: the shape under the hand changed, and the spoken range is the confirmation of
	what it changed to.

	:param periods: what it holds.
	"""
	# Translators: the name of a candlestick chart on the display. Placeholders are how many
	# periods it covers and the first and last of them.
	return _("candlestick chart, {count} periods, {first} to {last}").format(
		count=len(periods),
		first=periods[0].label,
		last=periods[-1].label,
	)
