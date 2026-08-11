# BrlMultiline: cursor routing policies.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""What a cursor routing key press does.

A policy belongs to a segment rather than to the display, so a table cell can treat a
routing press as a selection while the segment beside it routes into text in the ordinary
way. Panels hand their own policy to the segments they create, so in practice a policy is
chosen once per panel and applies across it.

This module is separate from `views` so that `panels` can carry a policy without importing
the view layer that is built on top of it.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from .container import DisplayContainer


class RoutingPolicy:
	"""Decides what a cursor routing key press does.

	The default routes within the segment the key falls in, which is what a braille
	display normally does.
	"""

	def route(self, container: "DisplayContainer", segmentNumber: int, segmentPos: int) -> None:
		"""Act on a routing key press.

		:param container: the whole display.
		:param segmentNumber: the segment the key falls in.
		:param segmentPos: position of the key within that segment, as a flat row major
			index.
		"""
		container.segments[segmentNumber].routeTo(segmentPos)

	def __repr__(self) -> str:
		return f"<{type(self).__name__}>"


class EdgeRowScrollRoutingPolicy(RoutingPolicy):
	"""Turns the top and bottom row of a segment into scroll keys.

	Intended for panels with many segments, where there are not enough physical keys to
	give every segment its own pair of panning commands. A press in a segment's top row
	scrolls that segment back, one in its bottom row scrolls it forward, and anything
	between routes as usual.

	This is a starting point rather than a settled design: how many rows to sacrifice, and
	whether the edge rows should be per segment or per panel, is worth deciding against a
	real view with real content.
	"""

	def route(self, container: "DisplayContainer", segmentNumber: int, segmentPos: int) -> None:
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


DEFAULT_ROUTING_POLICY = RoutingPolicy()
"""The policy a segment gets when nothing chooses one for it.

Policies hold no state, so one instance is shared rather than built per segment.
"""
