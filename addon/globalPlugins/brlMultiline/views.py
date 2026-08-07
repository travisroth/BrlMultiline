# BrlMultiline: views.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A view is a way of using the whole braille display.

Everything the add-on puts on the display is a view, including the plain single segment
arrangement that matches NVDA's own behaviour. A view carries:

1. the rectangle each segment occupies,
2. which segment follows the system focus,
3. how text wraps within a segment,
4. what a cursor routing key press does.

The settings dialog produces views that divide the display into whole row groups (or, on
a single row display, column slices). Code driving the display directly can build any
view it likes, including grids, and hand it to the plugin to activate.
"""

from typing import TYPE_CHECKING

from logHandler import log

from . import bmConfig
from .layout import SegmentRect, calculateSegmentRects, validateRects

if TYPE_CHECKING:
	from .container import BrailleBufferContainer


class RoutingPolicy:
	"""Decides what a cursor routing key press does.

	The default routes within the segment the key falls in, which is what a braille
	display normally does.
	"""

	def route(self, container: "BrailleBufferContainer", segmentNumber: int, segmentPos: int) -> None:
		"""Act on a routing key press.

		:param container: the multi segment buffer.
		:param segmentNumber: the segment the key falls in.
		:param segmentPos: position of the key within that segment, as a flat row major
			index.
		"""
		container.segments[segmentNumber].routeTo(segmentPos)

	def __repr__(self) -> str:
		return f"<{type(self).__name__}>"


class EdgeRowScrollRoutingPolicy(RoutingPolicy):
	"""Turns the top and bottom row of each segment into scroll keys.

	Intended for views with many segments, where there are not enough physical keys to
	give every segment its own pair of panning commands. A press in a segment's top row
	scrolls that segment back, one in its bottom row scrolls it forward, and anything
	between routes as usual.

	This is a starting point rather than a settled design: how many rows to sacrifice,
	and whether the edge rows should be per segment or per display, is worth deciding
	against a real view with real content.
	"""

	def route(self, container: "BrailleBufferContainer", segmentNumber: int, segmentPos: int) -> None:
		rect = container.rects[segmentNumber]
		if rect.numRows < 3:
			# There would be no rows left to route in, so leave this segment alone.
			super().route(container, segmentNumber, segmentPos)
			return
		row = segmentPos // rect.numCols
		if row == 0:
			container.scrollBack(segmentNumber)
		elif row == rect.numRows - 1:
			container.scrollForward(segmentNumber)
		else:
			super().route(container, segmentNumber, segmentPos)


class SegmentView:
	"""A complete arrangement of the braille display."""

	def __init__(
		self,
		name: str,
		rects: list[SegmentRect],
		focusSegmentNumber: int = -1,
		fillRows: bool = False,
		markCuts: bool = False,
		routingPolicy: RoutingPolicy | None = None,
	) -> None:
		"""
		:param name: a short name, for logging and for reporting the current view.
		:param rects: the rectangle each segment occupies.
		:param focusSegmentNumber: which segment follows the system focus. -1 means the
			last. A view must have one: NVDA reads `mainBuffer.regions[-1]` in several
			places, so there always has to be a segment for untargeted content to land
			in.
		:param fillRows: fill each row of a segment completely, continuing mid word onto
			the next row, rather than wrapping at word boundaries. Narrow segments have
			no cells to spare for whole words.
		:param markCuts: when filling rows, spend one cell on a continuation mark where a
			row was cut mid word. Ignored unless `fillRows` is set.
		:param routingPolicy: what a cursor routing key press does. Defaults to routing
			within the segment pressed.
		"""
		self.name = name
		self.rects = rects
		self.focusSegmentNumber = focusSegmentNumber
		self.fillRows = fillRows
		self.markCuts = markCuts
		self.routingPolicy = routingPolicy if routingPolicy is not None else RoutingPolicy()

	def validate(self, numRows: int, numCols: int) -> None:
		"""Check this view can be shown on a display of the given size.

		:raises ValueError: if the segments do not fit, or overlap.
		:raises LookupError: if the focus segment does not exist.
		"""
		validateRects(self.rects, numRows, numCols)
		if self.focusSegmentNumber != -1 and not 0 <= self.focusSegmentNumber < len(self.rects):
			raise LookupError(
				f"View {self.name!r} wants segment {self.focusSegmentNumber} to follow the focus, "
				f"but it has {len(self.rects)} segments",
			)

	def __repr__(self) -> str:
		return (
			f"<SegmentView {self.name!r} {len(self.rects)} segments, "
			f"focus {self.focusSegmentNumber}, fillRows={self.fillRows}>"
		)


def viewFromConfig(numRows: int, numCols: int, displayKey: str | None = None) -> SegmentView:
	"""Build the view the user has configured for a display.

	Falls back to a single segment covering the whole display if the stored layout does
	not fit, which can happen when a display is replaced by a smaller one under the same
	name.

	:param numRows: number of rows on the display.
	:param numCols: number of columns on the display.
	:param displayKey: the display to read settings for, or None for the current one.
	:return: the configured view.
	"""
	layout = bmConfig.getLayout(displayKey)
	try:
		rects = calculateSegmentRects(numRows, numCols, layout)
	except ValueError:
		log.error(
			f"BrlMultiline: configured layout {layout} does not fit "
			f"a {numRows} by {numCols} display; using a single segment",
			exc_info=True,
		)
		rects = calculateSegmentRects(numRows, numCols, 1)
	focusSegment = bmConfig.getFocusSegment(displayKey)
	if focusSegment != -1 and not 0 <= focusSegment < len(rects):
		log.warning(
			f"BrlMultiline: no segment {focusSegment} in this layout; using the last segment",
		)
		focusSegment = -1
	return SegmentView(name="configured", rects=rects, focusSegmentNumber=focusSegment)


def singleSegmentView(numRows: int, numCols: int) -> SegmentView:
	"""Build a view of one segment covering the whole display.

	This is what NVDA does on its own, expressed as a view.
	"""
	return SegmentView(
		name="single",
		rects=calculateSegmentRects(numRows, numCols, 1),
		focusSegmentNumber=-1,
	)
