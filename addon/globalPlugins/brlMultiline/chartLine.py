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
2. **Lines are told apart by texture, not by weight.** There is no colour, and every line but
   one is one pin thick. So each series gets a pattern along its own path — solid, dashed,
   dotted, dash dot — which is the convention tactile graphics standards already use, and
   which a finger reads as a difference in surface rather than in position. The one exception
   is the first series on a busy chart, which is drawn two pins thick; see `THICK_FROM`.
3. **Four series is the limit.** Not an arbitrary number: it is how many patterns can be told
   apart by touch on a path that is often diagonal. A fifth would have to repeat a pattern,
   and two lines with the same texture crossing each other is a chart that lies.

**A gap in a series is drawn as a gap.** A twenty day moving average has nineteen empty cells
at the top of its column, and a chart that treated those as zero would draw the average
plunging to the floor and climbing back — which looks exactly like a crash. So a run of
missing values breaks the line, and the reader feels the line start where the data starts.

**Some of the lines can be put aside.** Four textures crossing each other are four textures
to hold apart at once, and the way a tactile reader untangles that on paper is to have the
lines on separate sheets. `Drawing.nextView` is that: each line alone, and the first line with
each of the others, on the same scale and in the same textures as the whole chart, so what is
learned from one line on its own is still true when the others come back.

Knows nothing about NVDA, Excel, or any display.
"""

from typing import Callable, NamedTuple, Optional

from .chartDraw import (
	NEAR_COLUMNS,
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
	"THICK_FROM",
	"Line",
	"lineChart",
	"patternName",
	"viewsFor",
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

THICK_FROM = 3
"""Series in a chart from which its first series is drawn two pins thick.

Solid against dashed is a real difference on a flat line and a faint one on a steep line, and
with three or four textures crossing each other the line the reader came for is the one they
lose. Weight is the one thing a finger reads before texture, so the first line gets it. Two
series are left one pin each: a solid line beside one dashed line is still easy to follow,
and a second pin costs a row of the plot's precision.

Decided by how many series the chart has, not by how many a view is showing, so the first
line is the same thickness whenever it is felt.
"""

SNAP_FROM = 0.75
"""Pins per point, zoomed in, above which points are spaced a whole number of pins apart.

Spreading forty points exactly across ninety-six pins puts them 2.4 pins apart, which rounds
to gaps of 2, 3, 2, 2, 3 — and a finger reads that unevenness as a wobble in the line that the
data does not have. So a zoomed window is widened or narrowed a little, until every point is
the same whole number of pins from the next: one, two, three. The rhythm is then something a
reader can count along.

Below this the window has more points than pins and is compressed, and no spacing will make
that even; it is left as it is. The whole chart is never snapped, because the whole chart has
to show every point, and there is nothing beyond its ends to widen into.
"""


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


def viewsFor(count: int) -> "list[tuple]":
	"""Which series each view of a chart shows, in the order a reader steps through them.

	The whole chart, then each line on its own, then — where there are three or more — the
	first line with each of the others. Alone first because the question that sends a reader
	to a view is usually "what is this one doing", and the pairs after because the next
	question is "what is it doing against the price".

	:param count: how many series the chart has.
	:return: one tuple of series positions per view, the whole chart first. A single series
		has only the one view.
	"""
	everything = tuple(range(count))
	if count < 2:
		return [everything]
	views = [everything] + [(index,) for index in range(count)]
	if count >= 3:
		views += [(0, index) for index in range(1, count)]
	return views


def lineChart(
	newBuffer: Callable,
	width: int,
	height: int,
	lines: "list[Line]",
	labels: Optional[list] = None,
	translate: Optional[Callable] = None,
	shown: Optional[tuple] = None,
) -> Drawing:
	"""Draw several series over a shared period, to fill a rectangle exactly.

	:param newBuffer: makes a blank buffer of a given width and height.
	:param width: the rectangle's width in pins.
	:param height: its height in pins.
	:param lines: the series, in the order they are to be drawn.
	:param labels: what each point is called — dates, usually. Positions are used where this
		is None, which is honest for a bare column of numbers.
	:param translate: turns a string into braille cells. None draws the chart bare.
	:param shown: which of the series to draw, by position among those that have numbers.
		None draws them all. The others still set the scale, so a line keeps its height
		whichever of its neighbours are showing.
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
	shown = tuple(sorted({index for index in shown if 0 <= index < len(lines)})) if shown else None
	shown = shown or tuple(range(len(lines)))
	return _compose(
		newBuffer,
		width,
		height,
		lines,
		labels,
		translate,
		shown,
		pitch=None,
		redraw=_reframer(newBuffer, lines, labels, translate, shown),
		nextView=_viewChanger(newBuffer, width, height, lines, labels, translate, shown),
	)


def _compose(
	newBuffer: Callable,
	width: int,
	height: int,
	lines: "list[Line]",
	labels: Optional[list],
	translate: Optional[Callable],
	shown: tuple,
	pitch: Optional[int],
	redraw: Optional[Callable] = None,
	nextView: Optional[Callable] = None,
) -> Drawing:
	"""Draw the chart for one run of points, whole or windowed.

	:param newBuffer: makes a blank buffer.
	:param width: the rectangle's width in pins.
	:param height: its height in pins.
	:param lines: every series, for the run being drawn. Series with nothing in this run are
		kept rather than dropped, so a series keeps its texture however far in the reader is.
	:param labels: what each point is called, or None.
	:param translate: turns a string into braille cells.
	:param shown: which series to draw.
	:param pitch: pins between points, or None to spread the points across the whole width.
	:param redraw: how to compose a window of this, for the whole chart only.
	:param nextView: how to change which series are shown, for the whole chart only.
	:return: the drawing.
	:raises ChartRefused: if there is nothing to chart or no room to chart it.
	"""
	count = max(len(line.values) for line in lines)
	if count < MIN_POINTS:
		# Translators: reported when a line chart was asked for over a single value.
		raise ChartRefused(_("A line needs at least two points"))
	if not any(value is not None for line in lines for value in line.values):
		raise ChartRefused(_("There are no numbers here to chart"))
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
	columns = _columns(count, width, pitch)
	thick = len(lines) >= THICK_FROM
	for index in shown:
		_drawLine(
			buffer, lines[index], columns, scale, PATTERNS[index % len(PATTERNS)][1], thick and not index
		)
	if textRows:
		writeFrame(
			buffer,
			translate,
			scale,
			firstLabel=_labelAt(labels, 0),
			lastLabel=_labelAt(labels, count - 1),
		)
	note = ""
	if not any(value is not None for index in shown for value in lines[index].values):
		# A window into the start of a moving average, with only the average showing. The
		# frame is still drawn so the dates say where the reader is, and this says why the
		# plot is empty, since an empty plot on its own reads as a fault.
		# Translators: reported when the part of a line chart being shown holds no values for
		# the lines chosen to be shown.
		note = _("no values here for the lines shown")
	return Drawing(
		buffer,
		name=_chartName(lines, shown, count, labels, pitch, width),
		describeAt=_describer(lines, shown, columns, scale, labels),
		redraw=redraw,
		points=count,
		note=note,
		nextView=nextView,
		pinsPerPoint=1,
	)


def _reframer(
	newBuffer: Callable,
	lines: "list[Line]",
	labels: Optional[list],
	translate: Optional[Callable],
	shown: tuple,
) -> Callable:
	"""Build the closure that draws this chart again for part of its period.

	**Zoom on a chart is not magnification.** Magnifying is the only thing that can be done to
	a photograph, and done to a chart it makes the braille labels into smears of enlarged dots
	while the dates written at the two ends go on naming the whole range — so the reader is
	looking at a tenth of the data with the wrong dates under it, which is what hardware
	reported. The numbers are still to hand, so the chart is composed again instead: the same
	panel, fewer days, drawn at full resolution with their own dates written under them.

	The window asked for is adjusted to space its points evenly; see `SNAP_FROM`.

	:param newBuffer: makes a blank buffer.
	:param lines: the whole series.
	:param labels: what each point is called.
	:param translate: turns a string into braille cells.
	:param shown: which series are drawn, carried into every window.
	:return: a function taking where the window starts and how wide it is as fractions,
		and the size to compose for. The size comes with the window because the
		rectangle can change under a figure that is already up: toggling the braille
		line beside the drawing is a command.
	"""
	count = max(len(line.values) for line in lines)

	def redraw(offset: float, span: float, pinWidth: int, pinHeight: int) -> Optional[Drawing]:
		first, last = windowOf(count, offset, span, MIN_POINTS)
		first, last, pitch = _snapped(count, first, last, pinWidth)
		try:
			return _compose(
				newBuffer,
				pinWidth,
				pinHeight,
				[Line(line.name, line.values[first:last]) for line in lines],
				labels[first:last] if labels else None,
				translate,
				shown,
				pitch,
			)
		except ChartRefused:
			# None leaves the reader the view they had, which is a better answer than a blank
			# panel for a window that happened to hold nothing chartable.
			return None

	return redraw


def _snapped(count: int, first: int, last: int, width: int) -> tuple:
	"""Adjust a window so its points are a whole number of pins apart.

	About the window's middle, which is where the mode keeps the reader's place, except at an end of
	the data. A window against an end stays against it: widened or narrowed about its middle, one
	that ended on the last day came back a day short of it, and no amount of panning reached the
	last close. Found on hardware, a year of prices at one pin per point.

	:param count: how many points the whole chart has.
	:param first: the window's first point.
	:param last: one past its last point.
	:param width: the pins it will be drawn across.
	:return: (first, last, pitch), where pitch is None if the window was left as it was.
	"""
	take = last - first
	if take >= count or take < MIN_POINTS or width < MIN_POINTS:
		return first, last, None
	across = (width - 1) / (take - 1)
	if across < SNAP_FROM:
		return first, last, None
	pitch = max(1, int(across + 0.5))
	fits = (width - 1) // pitch + 1
	if fits > count:
		return first, last, None
	if last >= count:
		return count - fits, count, pitch
	if first <= 0:
		return 0, fits, pitch
	middle = first + take / 2
	first = max(0, min(count - fits, int(round(middle - fits / 2))))
	return first, first + fits, pitch


def _viewChanger(
	newBuffer: Callable,
	width: int,
	height: int,
	lines: "list[Line]",
	labels: Optional[list],
	translate: Optional[Callable],
	shown: tuple,
) -> Optional[Callable]:
	"""Build the closure that draws the whole chart with the next or previous set of lines.

	:return: a function taking a direction, 1 for the next view and -1 for the previous,
		and returning the whole chart drawn that way. None for a chart with one series,
		which has nothing to put aside.
	"""
	views = viewsFor(len(lines))
	if len(views) < 2:
		return None
	here = views.index(shown) if shown in views else 0

	def nextView(direction: int) -> Drawing:
		return lineChart(
			newBuffer,
			width,
			height,
			lines,
			labels,
			translate,
			shown=views[(here + direction) % len(views)],
		)

	return nextView


def _columns(count: int, width: int, pitch: Optional[int] = None) -> "list[int]":
	"""Place the points across the drawing.

	Spread, the first point is against the left edge and the last against the right, so the
	chart uses its whole width and the two ends are where the writing under them says they
	are. At a pitch, every point is that many pins from the last, and whatever is left over is
	less than one pitch at the right.

	:param count: how many points there are.
	:param width: the drawing's width in pins.
	:param pitch: pins between points, or None to spread them.
	:return: the column each point is drawn in.
	"""
	if count < 2:
		return [0]
	if pitch:
		return [index * pitch for index in range(count)]
	return [int(round(index * (width - 1) / (count - 1))) for index in range(count)]


def _labelAt(labels: Optional[list], index: int) -> str:
	""":return: what a point is called, or its position where there is nothing to call it.

	:param labels: the labels, or None.
	:param index: which point.
	"""
	if labels and 0 <= index < len(labels):
		return str(labels[index])
	return str(index + 1)


def _drawLine(buffer, line: Line, columns: "list[int]", scale: Scale, pattern: tuple, thick: bool) -> None:
	"""Draw one series, broken wherever it has no value.

	:param buffer: what to draw on.
	:param line: the series.
	:param columns: the column each point is drawn in.
	:param scale: the shared value to row mapping.
	:param pattern: which dots along the path to raise.
	:param thick: whether to draw it two pins thick.
	"""
	for run in _runs(line.values, columns, scale):
		if len(run) == 1:
			# A single value with gaps either side is still a measurement, and a chart that
			# drew nothing for it would be a chart with a hole in it that says nothing.
			_raise(buffer, run[0][0], run[0][1], False, thick, scale)
			continue
		_drawPath(buffer, run, pattern, thick, scale)


def _runs(values: list, columns: "list[int]", scale: Scale) -> "list[list]":
	"""Split a series into the stretches that have values.

	:param values: the series, Nones where there is no value.
	:param columns: the column each point is drawn in.
	:param scale: the shared value to row mapping.
	:return: runs of points, each a list of (x, y, which point).
	"""
	runs = []
	current: list = []
	for index, value in enumerate(values):
		if value is None or index >= len(columns):
			if current:
				runs.append(current)
				current = []
			continue
		current.append((columns[index], scale.row(value), index))
	if current:
		runs.append(current)
	return runs


def _drawPath(buffer, points: "list[tuple]", pattern: tuple, thick: bool, scale: Scale) -> None:
	"""Draw a run of points with a pattern running along it.

	The phase carries across the joins between segments, so a dash is not restarted at every
	data point. Restarted, a chart with points three pins apart would draw the same first
	three dots of the pattern over and over and every series would look solid.

	:param buffer: what to draw on.
	:param points: the run, in order, as (x, y, which point).
	:param pattern: which dots along the path to raise.
	:param thick: whether to draw it two pins thick.
	:param scale: the plot's rows, which a thick line's second pin must stay inside.
	"""
	phase = 0
	for index, (start, end) in enumerate(zip(points, points[1:])):
		steep = abs(end[1] - start[1]) > abs(end[0] - start[0])
		dots = _segmentDots(start[0], start[1], end[0], end[1])
		if index:
			# The join is one dot, not two: the previous segment already drew it, and drawing
			# it again would advance the phase by one at every data point.
			next(dots, None)
		for x, y in dots:
			if pattern[phase % len(pattern)]:
				_raise(buffer, x, y, steep, thick, scale)
			phase += 1


def _raise(buffer, x: int, y: int, steep: bool, thick: bool, scale: Scale) -> None:
	"""Raise one dot of a line, and its second pin if the line is thick.

	The second pin goes **across** the direction of travel: below a shallow stretch and beside
	a steep one. Always below would leave a near vertical stretch one pin wide, since the pin
	below each dot of it is the next dot of the line. It goes the other way at the edge of the
	plot rather than into the writing under it or off the panel.

	:param buffer: what to draw on.
	:param x: the dot's column.
	:param y: its row.
	:param steep: whether the stretch it is on climbs more than it runs.
	:param thick: whether to add the second pin.
	:param scale: the plot's rows.
	"""
	buffer.setDot(x, y)
	if not thick:
		return
	if steep:
		buffer.setDot(x + 1 if x + 1 < buffer.width else x - 1, y)
	else:
		buffer.setDot(x, y + 1 if y + 1 <= scale.bottom else y - 1)


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


def _pathOf(values: list, columns: "list[int]", scale: Scale) -> dict:
	"""Every dot a series' line passes through, and which point each belongs to.

	The line as drawn rather than the points it was drawn from, which is the difference
	between answering a finger and answering a column. Between two points three rows and two
	columns apart, the line crosses rows neither point is on; with more points than pins, a
	column holds several points and the line runs up and down through all of them. Checked
	against the points alone, a finger squarely on either of those found nothing.

	The pattern's gaps are included, since a finger on a dashed line is on the line whether or
	not it landed on a dash.

	:param values: the series.
	:param columns: the column each point is drawn in.
	:param scale: the shared value to row mapping.
	:return: column to a list of (row, which point), for looking up what is near a press.
	"""
	path: dict = {}
	for run in _runs(values, columns, scale):
		if len(run) == 1:
			path.setdefault(run[0][0], []).append((run[0][1], run[0][2]))
			continue
		for start, end in zip(run, run[1:]):
			dots = list(_segmentDots(start[0], start[1], end[0], end[1]))
			for step, (x, y) in enumerate(dots):
				# Each half of a segment belongs to the point at that end of it.
				point = start[2] if step * 2 < len(dots) else end[2]
				path.setdefault(x, []).append((y, point))
	return path


def _describer(
	lines: "list[Line]",
	shown: tuple,
	columns: "list[int]",
	scale: Scale,
	labels: Optional[list],
) -> Callable:
	"""Build the lookup from a source dot back to what is under it.

	Two answers, and the second is the one a printed chart cannot give. Near a line, it names
	the series, its value and the point — every line under the finger, nearest first, so a
	crossing or a tight squeeze says why it feels crowded. Off every line, it reads the
	**height** back as a value: "51.4 at June 3" is the reader asking what price their finger is
	at, which is how you find out whether a line is near a level without following the line to
	get there.

	Near means within the pad of a finger, `NEAR_COLUMNS` either side and `NEAR_ROWS` above and
	below, measured against the line as drawn. Only the lines being shown answer: a line that
	has been put aside is not under anyone's finger.

	:param lines: every series.
	:param shown: which of them are drawn.
	:param columns: the column each point is drawn in.
	:param scale: the shared value to row mapping.
	:param labels: what each point is called, or None.
	:return: a function taking source coordinates and returning what is there.
	"""
	paths: dict = {}
	compressed = len(set(columns)) < len(columns)
	"""Whether some points share a column, where the chart has more points than pins."""

	def pathOf(index: int) -> dict:
		# Built on the first press rather than with the chart, since most windows a reader
		# zooms through are never pressed at all.
		if index not in paths:
			paths[index] = _pathOf(lines[index].values, columns, scale)
		return paths[index]

	def describeAt(x: int, y: int) -> Optional[str]:
		hits = []
		for index in shown:
			path = pathOf(index)
			best = None
			for column in range(x - NEAR_COLUMNS, x + NEAR_COLUMNS + 1):
				for row, point in path.get(column, ()):
					if abs(row - y) > NEAR_ROWS:
						continue
					distance = (column - x) ** 2 + (row - y) ** 2
					if best is None or distance < best[0]:
						best = (distance, point)
			if best is not None:
				hits.append((best[0], index, best[1]))
		if not hits:
			# Translators: reported for a touch on a chart away from any line: the value that
			# height stands for, and what that point along the bottom is called.
			return _("{value} at {label}").format(
				value=roundedText(scale.valueAt(y), scale.step),
				label=_labelAt(labels, _nearestPoint(columns, x)),
			)
		hits.sort()
		if not compressed:
			# Every point has a column of its own, so there is one point under the finger, and
			# a line found a column or two over is read at that point rather than at the one
			# its nearest dot belongs to. Otherwise one press on two lines could answer for two
			# different days, which is precise about the pins and useless to a reader.
			under = _nearestPoint(columns, x)
			hits = [
				(
					distance,
					index,
					under
					if under < len(lines[index].values) and lines[index].values[under] is not None
					else point,
				)
				for distance, index, point in hits
			]
		if len({point for _distance, _index, point in hits}) == 1:
			readings = ", ".join(
				# Translators: one line's reading on a line chart. Placeholders are the series'
				# name and its value there.
				_("{name} {value}").format(
					name=lines[index].name, value=numberText(lines[index].values[point])
				)
				for _distance, index, point in hits
			)
			# Translators: reported for a touch on one or more lines of a chart at the same
			# point. Placeholders are each line's name and value, and what that point is called.
			return _("{readings}, {label}").format(readings=readings, label=_labelAt(labels, hits[0][2]))
		# Several points share the columns under the finger, which happens where the chart has
		# more points than pins, so each reading carries its own.
		return ", ".join(
			# Translators: one line's reading on a line chart, where lines under the finger are
			# at different points. Placeholders are the series' name, its value and the point.
			_("{name} {value} {label}").format(
				name=lines[index].name,
				value=numberText(lines[index].values[point]),
				label=_labelAt(labels, point),
			)
			for _distance, index, point in hits
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


def _styleName(index: int, lines: "list[Line]") -> str:
	""":return: how a series is drawn, weight included.

	:param index: the series' position.
	:param lines: every series, since whether the first is thick depends on how many there are.
	"""
	if not index and len(lines) >= THICK_FROM:
		# Translators: the name of a line style on a tactile chart drawn two pins wide. The
		# placeholder is the pattern, such as solid.
		return _("thick {pattern}").format(pattern=patternName(index))
	return patternName(index)


def _chartName(
	lines: "list[Line]",
	shown: tuple,
	count: int,
	labels: Optional[list],
	pitch: Optional[int],
	width: int,
) -> str:
	""":return: what to call the chart, which is also its key.

	Each series shown with the texture it was drawn in, because that mapping is the one thing
	the reader cannot get from the drawing and needs before the drawing means anything. Then
	how many of the lines are showing, if not all of them, and how many points there are and
	how they sit on the pins — "1 pin per point" is the view where every point can be felt, and
	"2.6 points per pin" says that some of them cannot.

	:param lines: every series.
	:param shown: which of them are drawn.
	:param count: how many points they cover.
	:param labels: what those points are called, or None.
	:param pitch: pins between points, or None where they are spread.
	:param width: pins across.
	"""
	legend = ", ".join(
		# Translators: one entry in a spoken key to a tactile line chart. Placeholders are the
		# series' name and the line style it was drawn in.
		_("{name} {pattern}").format(name=lines[index].name, pattern=_styleName(index, lines))
		for index in shown
	)
	# Translators: the start of the name of a line chart on the display. The placeholder is the
	# key to which series is which line style.
	parts = [_("line chart, {legend}").format(legend=legend)]
	if len(shown) < len(lines):
		# Translators: part of the name of a line chart showing only some of its lines.
		# Placeholders are how many are showing and how many the chart has.
		parts.append(_("{shown} of {total} lines").format(shown=len(shown), total=len(lines)))
	# Translators: part of the name of a line chart: how many points it covers.
	parts.append(_("{count} points").format(count=count))
	if pitch == 1:
		# Translators: part of the name of a line chart where each point has its own pin column.
		parts.append(_("1 pin per point"))
	elif pitch:
		# Translators: part of the name of a line chart: how many pins apart its points are.
		parts.append(_("{pins} pins per point").format(pins=pitch))
	elif count > width:
		# Translators: part of the name of a line chart with more points than pins, saying how
		# many points share each pin column on average.
		parts.append(_("{points} points per pin").format(points=numberText(round(count / width, 1))))
	if labels:
		# Translators: part of the name of a line chart: the first and last of its points.
		parts.append(
			_("{first} to {last}").format(first=_labelAt(labels, 0), last=_labelAt(labels, count - 1))
		)
	return ", ".join(parts)
