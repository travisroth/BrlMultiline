# BrlMultiline: line charts, several series at once.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Several numbers over the same period, drawn as lines a finger can tell apart.

Series such as a price with its moving average and bands only mean something against each
other, so:

1. One scale for every series.
2. Lines are told apart by texture along the path (see `PATTERNS`), and on a busy chart the
   first is two pins thick (see `THICK_FROM`).
3. Four series at most, as many textures as touch can tell apart.

A gap in a series is drawn as a gap, never as zero. `Drawing.nextView` shows each line alone
and the first with each other, on the same scale and textures. See
docs/design/excel-chart-plan.md.

Knows nothing about NVDA, Excel, or any display.
"""

from typing import Callable, NamedTuple, Optional

from .chartDraw import (
	NEAR_COLUMNS,
	NEAR_ROWS,
	ChartRefused,
	PointMark,
	Scale,
	isFinite,
	numberText,
	roundedText,
	textRowsFor,
	windowOf,
	writeFrame,
)
from .graphicsMode import Drawing

import addonHandler

addonHandler.initTranslation()

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

Along the path, so a steep line dashes at the same rate as a flat one. Solid first, for the
series the reader came for, then in descending order of how much of each is raised.
"""

MAX_LINES = len(PATTERNS)
"""Series above which the chart is refused rather than drawn with repeated patterns."""

THICK_FROM = 3
"""Series in a chart from which its first series is drawn two pins thick.

With three or more textures crossing, weight is what keeps the first line findable. Decided by
the chart's series, not the view's, so the first line's thickness never changes.
"""

SNAP_FROM = 0.75
"""Pins per point, zoomed in, above which points are spaced a whole number of pins apart.

Uneven gaps read as a wobble the data does not have, so a zoomed window is widened or narrowed
until the spacing is even. Below this, or for the whole chart, it is left as it is.
"""


class Line(NamedTuple):
	"""One named series of numbers over the shared period."""

	name: str
	"""What to call it: the column heading, where the selection had one."""

	values: list
	"""One number per point, in order. None where that point has no value."""

	key: object = None
	"""Which series this is where it came from an application's chart, so a point of it can be
	matched to this line when two lines share a name. See `Drawing.levelKeys`."""


def patternName(index: int) -> str:
	"""Say which texture a series was drawn with.

	Spoken rather than drawn, in the drawing's name, so entering the chart reads the legend.

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

	The whole chart, then each line alone, then, where there are three or more, the first line
	with each of the others.

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
	lines = [line._replace(values=[value if isFinite(value) else None for value in line.values]) for line in lines]
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
	firstPoint: int = 0,
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
	:param firstPoint: which point of the whole chart the first of these is, for a window.
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
		firstPoint=firstPoint,
		pointAt=lambda x: _nearestPoint(columns, x) if 0 <= x < width else None,
		markFor=_marker(lines, shown, columns, scale),
		sayPoint=lambda index: _reading(lines, shown, labels, index),
		levelNames=tuple(lines[index].name for index in shown),
		levelKeys=tuple(lines[index].key for index in shown),
	)


def _reframer(
	newBuffer: Callable,
	lines: "list[Line]",
	labels: Optional[list],
	translate: Optional[Callable],
	shown: tuple,
) -> Callable:
	"""Build the closure that draws this chart again for part of its period.

	Zoom composes the chart again for fewer points, with their own dates, rather than
	magnifying.

	The window asked for is adjusted to space its points evenly; see `SNAP_FROM`.

	:param newBuffer: makes a blank buffer.
	:param lines: the whole series.
	:param labels: what each point is called.
	:param translate: turns a string into braille cells.
	:param shown: which series are drawn, carried into every window.
	:return: a function taking where the window starts and how wide it is as fractions,
		and the size to compose for, since the rectangle can change under a figure.
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
				[line._replace(values=line.values[first:last]) for line in lines],
				labels[first:last] if labels else None,
				translate,
				shown,
				pitch,
				firstPoint=first,
			)
		except ChartRefused:
			# None leaves the reader the view they had, which is a better answer than a blank
			# panel for a window that happened to hold nothing chartable.
			return None

	return redraw


def _snapped(count: int, first: int, last: int, width: int) -> tuple:
	"""Adjust a window so its points are a whole number of pins apart.

	About the window's middle, except that a window against an end of the data stays against it,
	so panning can still reach the last point.

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

	Spread, the first and last points are against the edges. At a pitch, each point is that many
	pins from the last.

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

	The phase carries across joins, or close points would repeat the pattern's start and look
	solid.

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

	The second pin goes across the direction of travel: below a shallow stretch, beside a steep
	one, and the other way at the edge of the plot.

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

	Yielding rather than setting, since a pattern needs the dots one at a time.

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

	The line as drawn, not its points, so a finger between points finds it. The pattern's gaps
	are included.

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

	Near a line, every shown line under the finger, nearest first, with its value and point.
	Off every line, the height read back as a value. Near is `NEAR_COLUMNS` and `NEAR_ROWS`
	against the line as drawn.

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


def _valueAt(line: Line, index: int):
	""":return: a line's value at a point, or None where it has none."""
	return line.values[index] if 0 <= index < len(line.values) else None


def _reading(lines: "list[Line]", shown: tuple, labels: Optional[list], index: int) -> str:
	"""Say every line being shown at one point, for a step onto it.

	Every shown line, since a step has no nearest; one with no value says so.

	:param lines: every series.
	:param shown: which of them are drawn.
	:param labels: what each point is called, or None.
	:param index: which point.
	:return: the readings, then what the point is called.
	"""
	readings = []
	for series in shown:
		value = _valueAt(lines[series], index)
		if value is None:
			# Translators: one line's reading on a line chart where it has no value at that point.
			# The placeholder is the series' name.
			readings.append(_("{name} no value").format(name=lines[series].name))
		else:
			readings.append(_("{name} {value}").format(name=lines[series].name, value=numberText(value)))
	return _("{readings}, {label}").format(readings=", ".join(readings), label=_labelAt(labels, index))


def _marker(lines: "list[Line]", shown: tuple, columns: "list[int]", scale: Scale) -> Callable:
	"""Build the lookup from a point to where its guides go.

	The point line through the point's own column, and the level line at the value of the line
	the reader chose for it: the first line shown unless they changed it, which is the thick one
	where there are three or more.

	:param lines: every series.
	:param shown: which of them are drawn; the level follows one of these.
	:param columns: the column each point is drawn in.
	:param scale: the shared value to row mapping.
	:return: a function taking a point and a position in `shown`, and returning a `PointMark`.
	"""

	def markFor(index: int, level: int = 0) -> Optional[PointMark]:
		if not 0 <= index < len(columns) or not shown:
			return None
		line = lines[shown[level % len(shown)]]
		value = _valueAt(line, index)
		return PointMark(
			column=columns[index],
			row=None if value is None else scale.row(value),
			plotTop=scale.top,
			plotBottom=scale.bottom,
			value=value,
			name=line.name,
		)

	return markFor


def _nearestPoint(columns: "list[int]", x: int) -> int:
	""":return: which point a column belongs to.

	Where points share a column, the first wins; the drawing's name gives the count.

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

	Each shown series with its texture, how many are showing if not all, and how the points sit
	on the pins ("1 pin per point", "2.6 points per pin").

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
