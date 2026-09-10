# BrlMultiline: line charts, several series at once.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Several numbers over the same period, drawn as lines a finger can tell apart.

A bar chart compares a handful of things to each other. A line chart follows one thing over
time, and the reason it earns its own module is that the interesting case is more than one
line at once: a closing price with a moving average through it and a pair of Bollinger bands
around it is four series that only mean anything **against each other**. Charting them
separately would answer none of the questions they exist to answer.

Three decisions carry that:

1. **One scale for every series.** They are drawn against a single low and high taken across
   all of them, because a band that is not on the same scale as the price it bands is not a
   band. This is the whole reason the series are drawn together rather than as four charts.
2. **Lines are told apart by texture, not by weight.** There is no colour and no line width
   to spend: every line is one pin thick. So each series gets a pattern along its own path —
   solid, dashed, dotted, dash dot — which is the convention tactile graphics standards
   already use, and which a finger reads as a difference in surface rather than in position.
3. **Four series is the limit.** Not an arbitrary number: it is how many patterns can be told
   apart by touch on a path that is often diagonal. A fifth would have to repeat a pattern,
   and two lines with the same texture crossing each other is a chart that lies.

**A gap in a series is drawn as a gap.** A twenty day moving average has nineteen empty cells
at the top of its column, and a chart that treated those as zero would draw the average
plunging to the floor and climbing back — which looks exactly like a crash. So a run of
missing values breaks the line, and the reader feels the line start where the data starts.

Knows nothing about NVDA, Excel, or any display.
"""

from typing import Callable, NamedTuple, Optional

from .chartDraw import (
	NEAR_ROWS,
	ChartRefused,
	Scale,
	isFinite,
	numberText,
	roundedText,
	textRowsFor,
	windowOf,
	writeFrame,
)
from .graphicsMode import Drawing

__all__ = [
	"MAX_LINES",
	"MIN_POINTS",
	"Line",
	"lineChart",
	"patternName",
]

MIN_WIDTH = 8
"""Pins across, below which a line chart is refused.

A line needs somewhere to go sideways. Under about eight pins every point lands on the same
few columns and what is drawn is a smear rather than a shape.
"""

MIN_POINTS = 2
"""Points below which there is no line to draw, only a dot."""

PATTERNS = [
	("solid", (1,)),
	("dashed", (1, 1, 1, 0, 0)),
	("dotted", (1, 0, 0)),
	("dashDot", (1, 1, 1, 0, 1, 0)),
]
"""How each series is drawn, in the order the series arrive.

The pattern runs along the path rather than across the drawing, so a steep line dashes at the
same rate as a flat one. Drawn across the columns instead, a near vertical line would put its
whole dash in one column and read as solid.

Solid first because the first series is the one the reader came for — the closing price, the
measured quantity — and a solid line is the easiest to follow. The rest are in descending
order of how much of them is actually there, so the busiest chart still has its most
important line the most continuous.
"""

MAX_LINES = len(PATTERNS)
"""Series above which the chart is refused rather than drawn with repeated patterns."""


class Line(NamedTuple):
	"""One named series of numbers over the shared period."""

	name: str
	"""What to call it: the column heading, where the selection had one."""

	values: list
	"""One number per point, in order. None where that point has no value."""


def patternName(index: int) -> str:
	"""Say which texture a series was drawn with.

	Spoken rather than drawn, because a key drawn on the chart would cost a quarter of the
	panel to say what one sentence says once. It goes into the drawing's name, so entering the
	chart reads out the legend and pointing at a line confirms it.

	:param index: the series' position.
	:return: the name of its pattern.
	"""
	key = PATTERNS[index % len(PATTERNS)][0]
	names = {
		# Translators: the name of a line style on a tactile chart: an unbroken line.
		"solid": _("solid"),
		# Translators: the name of a line style on a tactile chart: short dashes.
		"dashed": _("dashed"),
		# Translators: the name of a line style on a tactile chart: single dots.
		"dotted": _("dotted"),
		# Translators: the name of a line style on a tactile chart: dashes and dots alternating.
		"dashDot": _("dash dot"),
	}
	return names[key]


def lineChart(
	newBuffer: Callable,
	width: int,
	height: int,
	lines: "list[Line]",
	labels: Optional[list] = None,
	translate: Optional[Callable] = None,
) -> Drawing:
	"""Draw several series over a shared period, to fill a rectangle exactly.

	:param newBuffer: makes a blank buffer of a given width and height.
	:param width: the rectangle's width in pins.
	:param height: its height in pins.
	:param lines: the series, in the order they are to be drawn.
	:param labels: what each point is called — dates, usually. Positions are used where this
		is None, which is honest for a bare column of numbers.
	:param translate: turns a string into braille cells. None draws the chart bare.
	:return: the drawing, which answers for each line and each point when pointed at.
	:raises ChartRefused: if there is nothing to chart or no room to chart it.
	"""
	# A value that is not a number becomes a gap, which is what it is: a point the source could
	# not give. A gap breaks the line and reads as one, where an error cell drawn as a number
	# would read as a price.
	lines = [Line(line.name, [value if isFinite(value) else None for value in line.values]) for line in lines]
	lines = [line for line in lines if any(value is not None for value in line.values)]
	if not lines:
		# Translators: reported when a chart was asked for with no numbers to chart.
		raise ChartRefused(_("There are no numbers here to chart"))
	if len(lines) > MAX_LINES:
		raise ChartRefused(
			# Translators: reported when a line chart has more series than can be told apart by
			# touch. Placeholders are how many were asked for and how many can be drawn.
			_("{asked} series cannot be told apart; a line chart holds {fits}").format(
				asked=len(lines),
				fits=MAX_LINES,
			),
		)
	count = max(len(line.values) for line in lines)
	if count < MIN_POINTS:
		# Translators: reported when a line chart was asked for over a single value.
		raise ChartRefused(_("A line needs at least two points"))
	if width < MIN_WIDTH or height < MIN_POINTS * 2:
		# Translators: reported when the space for a drawing is too small for a chart.
		raise ChartRefused(_("There is not enough room here for a chart"))
	buffer = newBuffer(width, height)
	if buffer is None:
		# Translators: reported when the display would not provide a surface to draw on.
		raise ChartRefused(_("The display would not provide a drawing surface"))
	textRows = textRowsFor(height, translate)
	scale = Scale.forValues(
		[value for line in lines for value in line.values],
		top=textRows,
		bottom=height - 1 - textRows,
	)
	columns = _columns(count, width)
	for index, line in enumerate(lines):
		_drawLine(buffer, line, columns, scale, PATTERNS[index % len(PATTERNS)][1])
	if textRows:
		writeFrame(
			buffer,
			translate,
			scale,
			firstLabel=_labelAt(labels, 0),
			lastLabel=_labelAt(labels, count - 1),
		)
	return Drawing(
		buffer,
		name=_chartName(lines, count, labels),
		describeAt=_describer(lines, columns, scale, labels),
		redraw=_reframer(newBuffer, width, height, lines, labels, translate),
		points=count,
	)


def _reframer(
	newBuffer: Callable,
	width: int,
	height: int,
	lines: "list[Line]",
	labels: Optional[list],
	translate: Optional[Callable],
) -> Callable:
	"""Build the closure that draws this chart again for part of its period.

	**Zoom on a chart is not magnification.** Magnifying is the only thing that can be done to
	a photograph, and done to a chart it makes the braille labels into smears of enlarged dots
	while the dates written at the two ends go on naming the whole range — so the reader is
	looking at a tenth of the data with the wrong dates under it, which is what hardware
	reported. The numbers are still to hand, so the chart is composed again instead: the same
	panel, fewer days, drawn at full resolution with their own dates written under them.

	:param newBuffer: makes a blank buffer.
	:param width: the rectangle's width in pins.
	:param height: its height in pins.
	:param lines: the whole series.
	:param labels: what each point is called.
	:param translate: turns a string into braille cells.
	:return: a function taking where the window starts and how wide it is as fractions,
		and the size to compose for. The size comes with the window because the
		rectangle can change under a figure that is already up: toggling the braille
		line beside the drawing is a command.
	"""
	count = max(len(line.values) for line in lines)

	def redraw(offset: float, span: float, pinWidth: int, pinHeight: int) -> Optional[Drawing]:
		first, last = windowOf(count, offset, span, MIN_POINTS)
		try:
			return lineChart(
				newBuffer,
				pinWidth,
				pinHeight,
				[Line(line.name, line.values[first:last]) for line in lines],
				labels[first:last] if labels else None,
				translate,
			)
		except ChartRefused:
			# None leaves the reader the view they had, which is a better answer than a blank
			# panel for a window that happened to hold nothing chartable.
			return None

	return redraw


def _columns(count: int, width: int) -> "list[int]":
	"""Place the points across the drawing.

	The first point is against the left edge and the last against the right, so the chart uses
	its whole width and the two ends are where the writing under them says they are.

	:param count: how many points there are.
	:param width: the drawing's width in pins.
	:return: the column each point is drawn in.
	"""
	if count < 2:
		return [0]
	return [int(round(index * (width - 1) / (count - 1))) for index in range(count)]


def _labelAt(labels: Optional[list], index: int) -> str:
	""":return: what a point is called, or its position where there is nothing to call it.

	:param labels: the labels, or None.
	:param index: which point.
	"""
	if labels and 0 <= index < len(labels):
		return str(labels[index])
	return str(index + 1)


def _drawLine(buffer, line: Line, columns: "list[int]", scale: Scale, pattern: tuple) -> None:
	"""Draw one series, broken wherever it has no value.

	:param buffer: what to draw on.
	:param line: the series.
	:param columns: the column each point is drawn in.
	:param scale: the shared value to row mapping.
	:param pattern: which dots along the path to raise.
	"""
	for run in _runs(line.values, columns, scale):
		if len(run) == 1:
			# A single value with gaps either side is still a measurement, and a chart that
			# drew nothing for it would be a chart with a hole in it that says nothing.
			buffer.setDot(run[0][0], run[0][1])
			continue
		_drawPath(buffer, run, pattern)


def _runs(values: list, columns: "list[int]", scale: Scale) -> "list[list]":
	"""Split a series into the stretches that have values.

	:param values: the series, Nones where there is no value.
	:param columns: the column each point is drawn in.
	:param scale: the shared value to row mapping.
	:return: runs of points, each a list of (x, y).
	"""
	runs = []
	current: list = []
	for index, value in enumerate(values):
		if value is None or index >= len(columns):
			if current:
				runs.append(current)
				current = []
			continue
		current.append((columns[index], scale.row(value)))
	if current:
		runs.append(current)
	return runs


def _drawPath(buffer, points: "list[tuple]", pattern: tuple) -> None:
	"""Draw a run of points with a pattern running along it.

	The phase carries across the joins between segments, so a dash is not restarted at every
	data point. Restarted, a chart with points three pins apart would draw the same first
	three dots of the pattern over and over and every series would look solid.

	:param buffer: what to draw on.
	:param points: the run, in order.
	:param pattern: which dots along the path to raise.
	"""
	phase = 0
	for index, (start, end) in enumerate(zip(points, points[1:])):
		dots = _segmentDots(start[0], start[1], end[0], end[1])
		if index:
			# The join is one dot, not two: the previous segment already drew it, and drawing
			# it again would advance the phase by one at every data point.
			next(dots, None)
		for x, y in dots:
			if pattern[phase % len(pattern)]:
				buffer.setDot(x, y)
			phase += 1


def _segmentDots(x0: int, y0: int, x1: int, y1: int):
	"""Walk the dots of a straight line, by Bresenham.

	The buffer can draw a line but cannot hand back the dots it drew, and a pattern needs
	them one at a time. Same algorithm, yielding rather than setting.

	:param x0: start column.
	:param y0: start row.
	:param x1: end column.
	:param y1: end row.
	:return: the dots, in order from the start.
	"""
	dx = abs(x1 - x0)
	dy = -abs(y1 - y0)
	stepX = 1 if x0 < x1 else -1
	stepY = 1 if y0 < y1 else -1
	error = dx + dy
	while True:
		yield x0, y0
		if x0 == x1 and y0 == y1:
			return
		doubled = 2 * error
		if doubled >= dy:
			error += dy
			x0 += stepX
		if doubled <= dx:
			error += dx
			y0 += stepY


def _describer(
	lines: "list[Line]",
	columns: "list[int]",
	scale: Scale,
	labels: Optional[list],
) -> Callable:
	"""Build the lookup from a source dot back to what is under it.

	Two answers, and the second is the one a printed chart cannot give. On a line, it names
	the series, its value and the point. Off every line, it reads the **height** back as a
	value: "51.4 at June 3" is the reader asking what price their finger is at, which is how
	you find out whether a line is near a level without following the line to get there.

	:param lines: the series drawn.
	:param columns: the column each point is drawn in.
	:param scale: the shared value to row mapping.
	:param labels: what each point is called, or None.
	:return: a function taking source coordinates and returning what is there.
	"""

	def describeAt(x: int, y: int) -> Optional[str]:
		index = _nearestPoint(columns, x)
		label = _labelAt(labels, index)
		nearest = None
		for line in lines:
			value = line.values[index] if index < len(line.values) else None
			if value is None:
				continue
			distance = abs(scale.row(value) - y)
			if nearest is None or distance < nearest[0]:
				nearest = (distance, line, value)
		if nearest and nearest[0] <= NEAR_ROWS:
			# Translators: reported for a touch on a line of a chart. Placeholders are the
			# series' name, its value there, and what that point is called.
			return _("{name}, {value}, {label}").format(
				name=nearest[1].name,
				value=numberText(nearest[2]),
				label=label,
			)
		# Translators: reported for a touch on a chart away from any line: the value that
		# height stands for, and what that point along the bottom is called.
		return _("{value} at {label}").format(
			value=roundedText(scale.valueAt(y), scale.step),
			label=label,
		)

	return describeAt


def _nearestPoint(columns: "list[int]", x: int) -> int:
	""":return: which point a column belongs to.

	With more points than pins several of them share a column, and the first of those wins.
	That is a limit of the display rather than a choice, and it is why the drawing's name says
	how many points there are.

	:param columns: the column each point is drawn in.
	:param x: the column pressed.
	"""
	best = 0
	for index, column in enumerate(columns):
		if abs(column - x) < abs(columns[best] - x):
			best = index
	return best


def _chartName(lines: "list[Line]", count: int, labels: Optional[list] = None) -> str:
	""":return: what to call the chart, which is also its key.

	Each series with the texture it was drawn in, because that mapping is the one thing the
	reader cannot get from the drawing and needs before the drawing means anything.

	:param lines: the series drawn.
	:param count: how many points they cover.
	:param labels: what those points are called, or None.
	"""
	legend = ", ".join(
		# Translators: one entry in a spoken key to a tactile line chart. Placeholders are the
		# series' name and the line style it was drawn in.
		_("{name} {pattern}").format(name=line.name, pattern=patternName(index))
		for index, line in enumerate(lines)
	)
	# Translators: the name of a line chart on the display. Placeholders are the key to which
	# series is which line style, how many points the chart covers, and the first and last
	# of those points.
	if labels:
		return _("line chart, {legend}, {count} points, {first} to {last}").format(
			legend=legend,
			count=count,
			first=_labelAt(labels, 0),
			last=_labelAt(labels, count - 1),
		)
	# Translators: the name of a line chart whose points have nothing to call them.
	return _("line chart, {legend}, {count} points").format(legend=legend, count=count)
