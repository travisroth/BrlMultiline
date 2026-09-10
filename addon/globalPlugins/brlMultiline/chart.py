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
	Series,
	cellsFor,
	numberText,
	textRowsFor,
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
	baseline = _baselineRow(series, barTop, barBottom)
	_draw(buffer, bars, baseline, barTop, barBottom)
	if textRows:
		_write(buffer, bars, slot, translate, valueTop=0, labelTop=height - textRows)
	return Drawing(
		buffer,
		name=_chartName(series),
		describeAt=_describer(bars, baseline),
	)


def _baselineRow(series: "list[Series]", top: int, bottom: int) -> int:
	"""Work out which row zero sits on.

	The bottom of the bar area where nothing is negative, which is the ordinary case and
	the one worth being exact about: a bar then grows up from the floor and its height is
	the whole of what the reader has to compare. With negatives present the zero line moves
	up into the area so bars can grow downwards from it, at the cost of the floor no longer
	meaning zero — which is why it is only done when the numbers demand it.

	:param series: what is being charted.
	:param top: the first row the bars may occupy.
	:param bottom: the last row they may occupy.
	:return: the row zero is drawn on.
	"""
	values = [entry.value for entry in series]
	low = min(min(values), 0.0)
	high = max(max(values), 0.0)
	if low >= 0:
		return bottom
	if high <= 0:
		return top
	span = high - low
	# Proportional, and never on the very edge of the area: a zero line against either end
	# would leave one direction with no room to grow in and would read as a chart with no
	# negatives in it.
	row = top + int(round((bottom - top) * (high / span)))
	return max(top + 1, min(bottom - 1, row))


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


def _draw(buffer, bars: "list[Bar]", baseline: int, top: int, bottom: int) -> None:
	"""Put the bars and the zero line on the buffer.

	The zero line is drawn first and the bars over it, so a bar of zero still shows as the
	line passing through its slot rather than as a gap. A reader sweeping the baseline
	should feel a continuous floor with bars standing on it, not a dotted one.

	:param buffer: what to draw on.
	:param bars: where the bars go.
	:param baseline: the row zero sits on.
	:param top: the first row the bars may occupy.
	:param bottom: the last row they may occupy.
	"""
	buffer.line(0, baseline, buffer.width - 1, baseline)
	values = [bar.series.value for bar in bars]
	reach = max(abs(value) for value in values) or 1.0
	upward = baseline - top
	downward = bottom - baseline
	for bar in bars:
		value = bar.series.value
		room = upward if value >= 0 else downward
		pins = int(round(abs(value) / reach * room)) if room else 0
		if value and not pins:
			# A value too small to round to a pin still gets one, **clear of the baseline**.
			# Rounding it to nothing shows as bare floor, which a reader reads as a missing
			# value rather than a small one; and a bar one pin tall that happens to be the
			# baseline row is the same thing said differently, because the baseline is drawn
			# there already. So the least a non-zero value can be is one pin above the floor.
			pins = 1
		# The bar spans from the floor to its tip inclusive, so a zero value is the floor
		# alone and every other value is the floor plus its own height.
		barTop = baseline - pins if value >= 0 else baseline
		buffer.rect(bar.left, barTop, bar.width, pins + 1, filled=True)


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
