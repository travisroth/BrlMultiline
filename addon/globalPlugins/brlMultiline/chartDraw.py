# BrlMultiline: the drawing vocabulary every chart type shares.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""What a bar chart, a line chart and a price chart all need, in one place.

Split out when the second and third chart types arrived. Each of them draws something quite
different, and each of them has to do the same four things first: work out how many rows the
writing costs, turn a number into a row, turn a string into cells that fit, and put the range
of the data where a finger can find it. Three copies of that arithmetic would have been three
chances for two charts to disagree about where the top of the plot is, which the reader would
feel as two charts that cannot be compared.

**The frame is deliberately the same on every chart that has an axis.** The top line is the
value range and the bottom line is the period range, always, so a reader who has learned to
sweep the top of a line chart for its high and low knows where those are on a candlestick
chart too. Consistency between chart types is worth more here than on a screen: there is no
glance, so every convention a reader does not have to re-learn is time they get back.

Knows nothing about NVDA, Excel, or any display. Numbers and a buffer to draw on.
"""

import math
from typing import Callable, NamedTuple, Optional

from logHandler import log

__all__ = [
	"CELL_COLUMNS",
	"ChartRefused",
	"GAP",
	"MIN_PLOT_ROWS",
	"NEAR_ROWS",
	"Scale",
	"Series",
	"TEXT_ROWS",
	"cellsFor",
	"numberText",
	"roundedText",
	"textRowsFor",
	"writeAt",
	"writeFrame",
	"writeRightAt",
]

GAP = 1
"""Pins of clear space after each bar, so two full bars are not one solid block."""

TEXT_ROWS = 4
"""Pin rows one line of braille needs. A cell is four dot rows, dots 7 and 8 included."""

CELL_COLUMNS = 3
"""Pin columns one braille cell needs, its gap column included."""

MIN_PLOT_ROWS = 6
"""Pin rows the drawing itself must keep for writing on it to be worth the space.

Below this the labels would have taken so much of the chart that its shape stops being
readable, which is the one thing a chart is for. A chart that small is drawn bare and the
reader gets the numbers by pointing at it instead.
"""

NEAR_ROWS = 3
"""How far from a line a press may land and still count as that line's press.

A fingertip is several pins wide, so an exact hit on a one pin line is not something a reader
can be asked for. Three rows is a little under one braille line, which is about the pad of a
finger at this pitch.
"""


class ChartRefused(Exception):
	"""There is a chart to be drawn but not in this space, or not from these numbers.

	Raised rather than returning None, because every case carries a reason the reader can act
	on — too many bars for the display, too many series to tell apart, nothing numeric in the
	selection — and a caller that only knew it had failed could not tell them which.
	"""


class Series(NamedTuple):
	"""One labelled number."""

	label: str
	"""What to call it when the reader points at it."""

	value: float
	"""What it is worth."""


class Scale(NamedTuple):
	"""The mapping between the numbers and the rows they are drawn on.

	One object rather than arithmetic at each call site, because it is needed in both
	directions. Drawing goes value to row; a press on a blank part of a plot goes row back to
	value, which is how a reader asks "what price is my finger at" — a question a chart on
	paper cannot answer and this one can.

	Rows count down from the top, so the low value is at `bottom` and the high at `top`.
	"""

	low: float
	"""The smallest value the plot has to hold."""

	high: float
	"""The largest."""

	top: int
	"""The first row the plot may use."""

	bottom: int
	"""The last row it may use."""

	@classmethod
	def forValues(cls, values, top: int, bottom: int) -> "Scale":
		"""Fit a scale to what is being drawn.

		**No padding above or below.** On a screen a chart leaves a margin so the extremes do
		not touch the frame; here every row is a movement of the fingertip and there are about
		twenty-seven of them, so a row spent on air is a row not spent on the shape of the data.

		:param values: every number that has to fit, Nones ignored.
		:param top: the first row the plot may use.
		:param bottom: the last row it may use.
		:return: the scale.
		"""
		numbers = [float(value) for value in values if value is not None]
		return cls(
			low=min(numbers) if numbers else 0.0,
			high=max(numbers) if numbers else 0.0,
			top=top,
			bottom=bottom,
		)

	@property
	def span(self) -> float:
		""":return: how much value the plot covers, which may be nothing."""
		return self.high - self.low

	@property
	def rows(self) -> int:
		""":return: how many rows the plot covers."""
		return self.bottom - self.top

	def row(self, value: float) -> int:
		"""Where a number is drawn.

		A flat series — every value the same — is drawn through the middle rather than along
		the floor, because a flat line along the bottom edge reads as a series of zeroes and a
		flat line through the middle reads as what it is.

		:param value: the number.
		:return: the row, clamped to the plot.
		"""
		if not self.span:
			return (self.top + self.bottom) // 2
		fraction = (float(value) - self.low) / self.span
		row = self.bottom - int(round(fraction * self.rows))
		return max(self.top, min(self.bottom, row))

	def valueAt(self, row: int) -> float:
		"""What a row is worth, the inverse of `row`.

		:param row: the row.
		:return: the value at that height.
		"""
		if not self.rows:
			return self.high
		fraction = (self.bottom - row) / self.rows
		return self.low + fraction * self.span

	@property
	def step(self) -> float:
		""":return: how much value one row is worth, for saying `valueAt` to a sane precision."""
		return self.span / self.rows if self.rows else 0.0


def textRowsFor(height: int, translate: Optional[Callable], minPlot: int = MIN_PLOT_ROWS) -> int:
	"""Decide whether the chart can afford to be written on.

	:param height: the drawing's height in pins.
	:param translate: the braille translator, or None for a chart drawn bare.
	:param minPlot: pin rows the drawing itself must keep.
	:return: pin rows to give each of the two lines of writing, or zero for none.
	"""
	return TEXT_ROWS if translate and height >= minPlot + 2 * TEXT_ROWS else 0


def cellsFor(translate: Optional[Callable], text: str) -> list:
	"""Turn a string into braille cells, tolerating a translator that cannot.

	A chart with no writing on it is still a chart; one that failed to appear because the
	braille tables were between states is not.

	:param translate: the translator, or None.
	:param text: what to write.
	:return: the cells, or an empty list.
	"""
	if translate is None:
		return []
	try:
		return list(translate(text) or [])
	except Exception:
		log.debugWarning("BrlMultiline: could not write a chart label", exc_info=True)
		return []


def writeAt(
	buffer,
	x: int,
	y: int,
	translate: Optional[Callable],
	text: str,
	room: int,
	whole: bool = False,
) -> int:
	"""Write a string with its left edge at a column.

	:param buffer: what to draw on.
	:param x: the left edge of the first cell.
	:param y: the top row of the line.
	:param translate: turns a string into braille cells.
	:param text: what to write.
	:param room: how many cells there is space for.
	:param whole: refuse rather than cut. For a number, where a cut one is a different number
		said with confidence.
	:return: how many cells were written.
	"""
	cells = cellsFor(translate, text)
	if not cells or room < 1 or (whole and len(cells) > room):
		return 0
	cells = cells[:room]
	buffer.text(x, y, cells, cellStride=CELL_COLUMNS)
	return len(cells)


def writeRightAt(
	buffer,
	right: int,
	y: int,
	translate: Optional[Callable],
	text: str,
	room: int,
	whole: bool = False,
) -> int:
	"""Write a string with its right edge at a column.

	Right aligned only where the thing written belongs to the right hand end of the chart —
	the last date, the low of the range — so that a reader sweeping to the end of a line
	arrives at it.

	:param buffer: what to draw on.
	:param right: the last column the writing may occupy.
	:param y: the top row of the line.
	:param translate: turns a string into braille cells.
	:param text: what to write.
	:param room: how many cells there is space for.
	:param whole: refuse rather than cut.
	:return: how many cells were written.
	"""
	cells = cellsFor(translate, text)
	if not cells or room < 1 or (whole and len(cells) > room):
		return 0
	cells = cells[:room]
	buffer.text(right + 1 - len(cells) * CELL_COLUMNS, y, cells, cellStride=CELL_COLUMNS)
	return len(cells)


def writeFrame(
	buffer,
	translate: Optional[Callable],
	scale: Scale,
	firstLabel: str = "",
	lastLabel: str = "",
) -> None:
	"""Write the value range across the top and the period range across the bottom.

	The whole of the axis a chart of this size can afford. A drawn axis with ticks and numbers
	up the side would cost a third of the width and tell the reader less than these two lines
	do, because at ninety-six pins a number beside a tick has nowhere to be.

	Four corners, and each says something a reader would otherwise have to hunt for: what the
	top of the plot is worth, what the bottom is worth, where the data starts and where it
	ends. Everything between them is what pointing is for.

	:param buffer: what to draw on.
	:param translate: turns a string into braille cells.
	:param scale: the plot's value range.
	:param firstLabel: what the leftmost point is called.
	:param lastLabel: what the rightmost point is called.
	"""
	room = (buffer.width // 2) // CELL_COLUMNS
	if room < 1:
		return
	bottom = buffer.height - TEXT_ROWS
	writeAt(buffer, 0, 0, translate, numberText(scale.high), room, whole=True)
	writeRightAt(buffer, buffer.width - 1, 0, translate, numberText(scale.low), room, whole=True)
	if firstLabel:
		writeAt(buffer, 0, bottom, translate, firstLabel, room)
	if lastLabel and lastLabel != firstLabel:
		writeRightAt(buffer, buffer.width - 1, bottom, translate, lastLabel, room)


def numberText(value: float) -> str:
	""":return: a number said the way a reader would write it.

	Whole numbers without a decimal part, because a count of seventeen read as "17.0" is
	seventeen said badly.

	:param value: the number.
	"""
	if float(value).is_integer():
		return str(int(value))
	return f"{value:g}"


def roundedText(value: float, step: float) -> str:
	"""Say a number to the precision the chart can actually distinguish.

	For reading a value back off a position rather than out of the data. One row of a plot is
	worth `step`, so a press between two rows cannot mean anything finer than that, and
	answering "50.428571428" would be a precision the drawing does not have.

	:param value: the number.
	:param step: how much one row is worth.
	:return: the number, rounded to a row's worth.
	"""
	if step <= 0:
		return numberText(value)
	digits = max(0, -int(math.floor(math.log10(step))))
	return numberText(round(value, digits))
