# BrlMultiline: charts drawn as dots.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Turning numbers into a tactile bar chart.

Phase 4 of the tactile graphics plan, and the first real content the graphics mode has been
given. Charts rather than pictures for a reason the plan states plainly: at this resolution the
panel is a diagram surface and not a screen, so it should be fed from structure rather than
from pixels — and a chart drawn from live application data is something no other tool does.

**It is drawn at the size of the space it is going in.** A photograph arrives at whatever size
it was and has to be compressed to fit; a chart is composed for its rectangle, so the fitted
view is the natural one and zooming is for looking closer at a bar rather than for discovering
what the drawing was. That is why `GraphicsMode.fitScale` never magnifies.

**A bar answers for itself.** The drawing carries a lookup from a source dot back to the bar
under it, so a routing press inside the chart says which bar and what it is worth. That is the
whole point of a chart on a display that can be pointed at, and it is the reason `Drawing`
takes a `describeAt` rather than only a name: without it a reader can feel that one bar is
taller than another and never learn what either of them is.

The bar chart itself. The vocabulary it shares with the other chart types — the value to row
mapping, the writing helpers, how many rows the writing costs — is in `chartDraw`, and the
types that read numbers out of an application are in `chartSource`.

This module knows nothing about NVDA or about Excel. It takes labels and numbers and a way to
make a buffer, and gives back a drawing.
"""

from typing import Callable, NamedTuple, Optional

from .chartDraw import (
	CELL_COLUMNS,
	GAP,
	ChartRefused,
	Scale,
	Series,
	cellsFor,
	isFinite,
	numberText,
	textRowsFor,
	windowOf,
)
from .graphicsMode import Drawing

__all__ = [
	"Bar",
	"ChartRefused",
	"Series",
	"barChart",
]

MIN_SLOT = 2
"""Pins per bar, including the gap after it, below which a chart is refused.

One pin of bar and one of gap is the least that can be told apart by a finger. Fewer bars drawn
larger would be a different chart from the one asked for, and silently charting the first
twenty of sixty values would be worse than saying it does not fit.
"""

class Bar(NamedTuple):
	"""Where one series ended up on the drawing, so a touch can be traced back to it."""

	series: Series
	left: int
	"""Leftmost source column of the bar."""

	width: int
	"""How many columns wide it is."""

	@property
	def right(self) -> int:
		""":return: one past the last column the bar occupies."""
		return self.left + self.width


def barChart(
	newBuffer: Callable,
	width: int,
	height: int,
	series: "list[Series]",
	translate: Optional[Callable] = None,
) -> Drawing:
	"""Draw a bar chart to fill a rectangle exactly.

	**Labels under the bars and values over them**, where there is room for both. Bars alone
	say which is larger and nothing else: a reader can feel the shape of the data and has to
	point at every bar to learn what any of it is. Written on, the chart is readable in one
	pass — sweep the bottom for the categories, the top for the numbers, the middle for the
	shape — and pointing becomes the way to ask about one bar rather than the only way to
	read the chart at all.

	:param newBuffer: makes a blank buffer of a given width and height.
	:param width: the rectangle's width in pins.
	:param height: its height in pins.
	:param series: what to chart, in the order the bars go left to right.
	:param translate: turns a string into braille cells. None draws the bars bare, which is
		what a display with no braille table behind it gets.
	:return: the drawing, which answers for each bar when pointed at.
	:raises ChartRefused: if there is nothing to chart or no room to chart it.
	"""
	if not series:
		# Translators: reported when a chart was asked for with no numbers to chart.
		raise ChartRefused(_("There are no numbers here to chart"))
	if not all(isFinite(entry.value) for entry in series):
		# Refused rather than skipped: a bar carries its own label, so leaving one out would
		# move every label after it and quietly rename the bars.
		# Translators: reported when a chart was asked for over a cell holding an error, an
		# overflow or a division by zero.
		raise ChartRefused(_("There is a value here that is not a number"))
	if width < MIN_SLOT or height < 2:
		# Translators: reported when the space for a drawing is too small for a chart.
		raise ChartRefused(_("There is not enough room here for a chart"))
	slot = width // len(series)
	if slot < MIN_SLOT:
		raise ChartRefused(
			# Translators: reported when a chart has more bars than the display can show.
			# Placeholders are how many were asked for and how many would fit.
			_("{asked} bars will not fit; this display holds {fits}").format(
				asked=len(series),
				fits=width // MIN_SLOT,
			),
		)
	buffer = newBuffer(width, height)
	if buffer is None:
		# Translators: reported when the display would not provide a surface to draw on.
		raise ChartRefused(_("The display would not provide a drawing surface"))
	textRows = textRowsFor(height, translate)
	barTop = textRows
	barBottom = height - 1 - textRows
	bars = _layout(series, slot, height)
	scale = _scaleFor(series, barTop, barBottom)
	baseline = _baselineRow(scale)
	_draw(buffer, bars, scale, baseline)
	if textRows:
		_write(buffer, bars, slot, translate, valueTop=0, labelTop=height - textRows)
	return Drawing(
		buffer,
		name=_chartName(series),
		describeAt=_describer(bars, baseline),
		redraw=_reframer(newBuffer, width, height, series, translate),
		points=len(series),
	)


def _reframer(
	newBuffer: Callable,
	width: int,
	height: int,
	series: "list[Series]",
	translate: Optional[Callable],
) -> Callable:
	"""Build the closure that draws this chart again for some of its bars.

	Zooming a bar chart magnifies it into fatter bars with their labels smeared; drawn again it
	is fewer bars, each wider, each with room for a label that is still braille. Forty bars are
	two pins each and unlabelled; ten of them are nine pins each and named.

	:param newBuffer: makes a blank buffer.
	:param width: the rectangle's width in pins.
	:param height: its height in pins.
	:param series: all the numbers.
	:param translate: turns a string into braille cells.
	:return: a function taking where the window starts and how wide it is as fractions,
		and the size to compose for. The size comes with the window because the
		rectangle can change under a figure that is already up: toggling the braille
		line beside the drawing is a command.
	"""

	def redraw(offset: float, span: float, pinWidth: int, pinHeight: int) -> Optional[Drawing]:
		first, last = windowOf(len(series), offset, span, 1)
		try:
			return barChart(newBuffer, pinWidth, pinHeight, series[first:last], translate)
		except ChartRefused:
			return None

	return redraw


def _scaleFor(series: "list[Series]", top: int, bottom: int) -> Scale:
	"""Fit one scale to the bars and to zero.

	**One scale, and zero is one of the numbers it has to hold.** Anything else and the chart
	lies. It used to place the zero line by the whole low-to-high range and then draw each bar
	as a fraction of the largest magnitude, which are two different scales whenever the
	positive and negative extremes differ: a chart of +25 and −75 put zero a quarter of the way
	down, correctly, and then drew the +25 bar as a third of that quarter rather than filling
	it. The reader felt a bar an eighth of the height of its neighbour where the numbers say a
	third, and nothing on the panel could have told them.

	:param series: what is being charted.
	:param top: the first row the bars may occupy.
	:param bottom: the last row they may occupy.
	:return: the scale, which holds every value and zero.
	"""
	return Scale.forValues([entry.value for entry in series] + [0.0], top, bottom)


def _baselineRow(scale: Scale) -> int:
	"""Work out which row zero sits on.

	The bottom of the bar area where nothing is negative, which is the ordinary case and
	the one worth being exact about: a bar then grows up from the floor and its height is
	the whole of what the reader has to compare. With negatives present the zero line moves
	up into the area so bars can grow downwards from it, at the cost of the floor no longer
	meaning zero — which is why it is only done when the numbers demand it.

	:param scale: the chart's scale, which already holds zero.
	:return: the row zero is drawn on.
	"""
	if not scale.span or scale.low >= 0:
		# Nothing below zero, or nothing at all. `Scale` draws a flat series through the middle,
		# which is right for a line and wrong here: a row of empty slots halfway up reads as a
		# chart whose bars have been cut off, where the same row along the floor reads as
		# nothing to show.
		return scale.bottom
	if scale.high <= 0:
		return scale.top
	# Proportional, and never on the very edge: a zero line against either end leaves one
	# direction with no room to grow in, and a bar that cannot be drawn reads as a value that
	# is not there. A chart of 1000 and −1 is the case — zero rounds onto the floor and the
	# small bar disappears through it.
	return max(scale.top + 1, min(scale.bottom - 1, scale.row(0.0)))


def _layout(series: "list[Series]", slot: int, height: int) -> "list[Bar]":
	"""Place the bars across the drawing.

	:param series: what is being charted.
	:param slot: pins from one bar's left edge to the next.
	:param height: the drawing's height in pins.
	:return: one bar per series entry, left to right.
	"""
	barWidth = max(1, slot - GAP)
	return [
		Bar(series=entry, left=index * slot, width=barWidth) for index, entry in enumerate(series)
	]


def _draw(buffer, bars: "list[Bar]", scale: Scale, baseline: int) -> None:
	"""Put the bars and the zero line on the buffer.

	The zero line is drawn first and the bars over it, so a bar of zero still shows as the
	line passing through its slot rather than as a gap. A reader sweeping the baseline
	should feel a continuous floor with bars standing on it, not a dotted one.

	Every bar's tip comes from the same scale the baseline did, so the rows between them are
	the rows the value is worth. See `_scaleFor`.

	:param buffer: what to draw on.
	:param bars: where the bars go.
	:param scale: the chart's scale.
	:param baseline: the row zero sits on.
	"""
	buffer.line(0, baseline, buffer.width - 1, baseline)
	for bar in bars:
		value = bar.series.value
		tip = _tipRow(scale, baseline, value)
		first = min(tip, baseline)
		# The bar spans from the floor to its tip inclusive, so a zero value is the floor
		# alone and every other value is the floor plus its own height.
		buffer.rect(bar.left, first, bar.width, abs(tip - baseline) + 1, filled=True)


def _tipRow(scale: Scale, baseline: int, value: float) -> int:
	"""Where a bar's far end goes.

	:param scale: the chart's scale.
	:param baseline: the row zero sits on.
	:param value: what the bar is worth.
	:return: the row of its tip, which is the baseline for a value of zero.
	"""
	if not value:
		return baseline
	# Each side measured against its own extreme and its own room. That is one scale and not
	# two, because the baseline was placed proportionally: the rows per unit come out the same
	# above the line as below it. Saying it per side is what lets the line be nudged clear of
	# an edge — see `_baselineRow` — without the other side losing its proportions.
	if value > 0:
		room, reach = baseline - scale.top, scale.high
	else:
		room, reach = scale.bottom - baseline, -scale.low
	pins = int(round(abs(value) / reach * room)) if reach and room else 0
	if pins:
		return baseline - pins if value > 0 else baseline + pins
	# A value too small to round to a pin still gets one, **clear of the baseline**.
	# Rounding it to nothing shows as bare floor, which a reader reads as a missing value
	# rather than a small one; and a bar one pin tall that happens to be the baseline row is
	# the same thing said differently, because the baseline is drawn there already. So the
	# least a non-zero value can be is one pin clear of the floor.
	moved = baseline - 1 if value > 0 else baseline + 1
	return max(scale.top, min(scale.bottom, moved))


def _write(buffer, bars: "list[Bar]", slot: int, translate: Callable, valueTop: int, labelTop: int) -> None:
	"""Write each bar's value above it and its name below it.

	**Left aligned with the bar rather than centred under it.** Centring is what a printed
	chart does, and it is the wrong choice for a finger: a reader following a bar downwards
	hits its label where the bar's own left edge is, and a centred label would start
	somewhere that depends on how long it happens to be.

	**A label that does not fit is cut; a value that does not fit is left out.** They are
	not the same kind of thing. "Wednes" is recognisably Wednesday and is worth having,
	whereas "330" for 33000 is a different number said with confidence — so a value is
	drawn whole or not at all, and the reader can still get it by pointing at the bar.

	:param buffer: what to draw on.
	:param bars: where the bars went.
	:param slot: pins from one bar's left edge to the next.
	:param translate: turns a string into braille cells.
	:param valueTop: the row the values are written on.
	:param labelTop: the row the labels are written on.
	"""
	room = slot // CELL_COLUMNS
	if room < 1:
		return
	for bar in bars:
		label = cellsFor(translate, bar.series.label)
		if label:
			buffer.text(bar.left, labelTop, label[:room], cellStride=CELL_COLUMNS)
		value = cellsFor(translate, numberText(bar.series.value))
		if value and len(value) <= room:
			buffer.text(bar.left, valueTop, value, cellStride=CELL_COLUMNS)


def _describer(bars: "list[Bar]", baseline: int) -> Callable:
	"""Build the lookup from a source dot back to the bar under it.

	:param bars: where the bars went.
	:param baseline: the row zero sits on, so the floor can name itself.
	:return: a function taking source coordinates and returning what is there, or None.
	"""

	def describeAt(x: int, y: int) -> Optional[str]:
		for bar in bars:
			if bar.left <= x < bar.right:
				# Translators: reported for a touch on a bar of a chart. Placeholders are what
				# the bar is called and what it is worth.
				return _("{label}, {value}").format(
					label=bar.series.label,
					value=numberText(bar.series.value),
				)
		if y == baseline:
			# Translators: reported for a touch on the zero line of a chart, between bars.
			return _("baseline")
		return None

	return describeAt


def _chartName(series: "list[Series]") -> str:
	""":return: what to call the chart when the reader asks what is on the display.

	:param series: what is being charted.
	"""
	# Translators: the name of a chart on the display. The placeholder is how many bars it has.
	return _("chart of {count} bars").format(count=len(series))
