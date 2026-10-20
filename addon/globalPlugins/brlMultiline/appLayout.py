# BrlMultiline: an application arranging the free part of the display.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The contract by which an app module arranges the part of the display the reader left free.

An app module offers a layout by having a method named L{LAYOUT_METHOD}. Nothing is imported
and nothing is registered, exactly as with the flow attributes, so the app module works
unchanged with BrlMultiline absent, disabled or older. The method is given the focus and a
L{DisplayArea}, and answers `None` or a plain dictionary:

```python
{
	"name": "chat",
	"segments": [
		{"size": area.rows - 1, "show": messageList},
		{"show": "focus"},
	],
}
```

This module is the half of that contract which can be got right without NVDA: reading the
answer and refusing a bad one, working out the area the app module may have, and placing its
segments in that area. It imports nothing from NVDA, so all of it is unit tested.

Four rules, each with its reason in the design (`docs/design/app-layouts-plan.md`):

1. **The reader's pins come first.** The area is what no pin and no other claim holds, so a
   layout never moves, covers or releases anything the reader put on the display. See
   L{freeArea}.
2. **Rows on a display with rows, cells on a display without.** The same rule the reader's own
   arrangement follows, because rows are what make panning work on a Monarch.
3. **The app module is not told about hardware.** A Monarch and a Focus 80 driven as one are
   eleven rows to it. A segment that would straddle the two is adjusted here, quietly, since
   the app module cannot see the boundary it crossed. See L{placeSegments}.
4. **A bad answer is refused whole.** L{LayoutError} carries what was wrong, in words an
   app module's author can act on, and the reader keeps their own arrangement.
"""

import dataclasses
from collections.abc import Callable, Sequence
from typing import Any, NamedTuple

from .layout import SegmentRect
from .panels import PRIORITY_APP_LAYOUT, BraillePanel, SegmentSpec

LAYOUT_METHOD = "brlMultilineLayout"
"""The name of the app module method that offers a layout. Read with `getattr`, never called
on anything that is not an app module."""

FOCUS = "focus"
"""The `show` value of the segment that follows the focus."""

PANEL_NAME = "appLayout"
"""The name of the claim an app layout is shown in, and the prefix of its segment keys.

One name for every layout, whatever the app module called it: there is only ever one app
layout up, and a fixed name is what lets the plugin find its own segments, and leave them
out, when it works out the room for the next one.
"""

MAX_ADJUSTMENTS = 16
"""How many times a placement may be moved off display boundaries before it is refused.

Each adjustment settles one boundary and a composite has one per display after the first, so
a placement that has not settled in this many has met a case the rule cannot settle rather
than one it is slowly settling.
"""


class LayoutError(ValueError):
	"""A layout that cannot be shown. The message says why, for the app module's author."""


class DisplayArea(NamedTuple):
	"""What an app module is told about the room it has, and all it is told.

	In the spirit of `braille.handler.displayDimensions`, but counting only the free area.
	"""

	rows: int
	"""Rows free to arrange. One means the segments divide cells rather than rows."""

	columns: int
	"""Cells across the free area. On displays of different widths driven as one, the
	narrowest of those the area reaches, so nothing planned for it is wider than a row."""


class Band(NamedTuple):
	"""One physical display's rows, as placement needs them."""

	rowStart: int
	numRows: int
	numCols: int

	@property
	def rowEnd(self) -> int:
		return self.rowStart + self.numRows


@dataclasses.dataclass(frozen=True)
class LayoutSegment:
	"""One segment of an app layout, as the app module described it."""

	show: Any
	"""L{FOCUS}, an object to show as it is, or a callable asked for the object each time."""

	size: int | None = None
	"""Rows, or cells on a display of one row. None takes what the others leave."""

	@property
	def isFocus(self) -> bool:
		return isinstance(self.show, str) and self.show == FOCUS

	@property
	def isResolved(self) -> bool:
		""":return: whether the object shown is asked for again rather than kept."""
		return not self.isFocus and callable(self.show)


@dataclasses.dataclass(frozen=True)
class AppLayout:
	"""An app layout that has been read and found to make sense, though not yet placed."""

	name: str
	segments: tuple[LayoutSegment, ...]

	@property
	def focusIndex(self) -> int:
		return next(index for index, segment in enumerate(self.segments) if segment.isFocus)


def parseLayout(raw: Any) -> AppLayout | None:
	"""Read what an app module answered.

	Checks only what can be checked without a display: the shape of the answer, that one
	segment follows the focus, and that at most one has no size. Whether the sizes fit is the
	business of L{placeSegments}, which knows the area.

	:param raw: the answer, which is None or a mapping with `name` and `segments`.
	:return: the layout, or None when the app module offered none here.
	:raises LayoutError: if the answer is not a layout.
	"""
	if raw is None:
		return None
	if not isinstance(raw, dict):
		raise LayoutError(f"a layout is a dict or None, not {type(raw).__name__}")
	name = raw.get("name")
	if not isinstance(name, str) or not name.strip():
		raise LayoutError("a layout needs a name, a non-empty string")
	rawSegments = raw.get("segments")
	if not isinstance(rawSegments, (list, tuple)) or not rawSegments:
		raise LayoutError(f"layout {name!r} needs segments, a non-empty list")
	segments = tuple(_parseSegment(name, index, each) for index, each in enumerate(rawSegments))
	focusCount = sum(segment.isFocus for segment in segments)
	if focusCount != 1:
		raise LayoutError(
			f"layout {name!r} needs exactly one segment showing {FOCUS!r}, and has {focusCount}",
		)
	unsized = sum(segment.size is None for segment in segments)
	if unsized > 1:
		raise LayoutError(
			f"layout {name!r} leaves {unsized} segments without a size; at most one may take what is left",
		)
	return AppLayout(name=name, segments=segments)


def _parseSegment(layoutName: str, index: int, raw: Any) -> LayoutSegment:
	""":return: one segment of a layout, read and checked.

	:raises LayoutError: if it is not a segment.
	"""
	where = f"segment {index} of layout {layoutName!r}"
	if not isinstance(raw, dict):
		raise LayoutError(f"{where} is a dict, not {type(raw).__name__}")
	if "show" not in raw or raw["show"] is None:
		raise LayoutError(f"{where} needs something to show: {FOCUS!r}, an object, or a callable")
	show = raw["show"]
	if isinstance(show, str) and show != FOCUS:
		# The one string that means anything is FOCUS. Any other is a typo for it, and showing
		# a string as though it were an object would fail far from here.
		raise LayoutError(f"{where} shows {show!r}; the only text it may show is {FOCUS!r}")
	size = raw.get("size")
	if size is not None:
		# bool is an int, and True as a size is a mistake that would otherwise be one row.
		if isinstance(size, bool) or not isinstance(size, int):
			raise LayoutError(f"{where} has size {size!r}; a size is a whole number")
		if size < 1:
			raise LayoutError(f"{where} has size {size}; a size is at least 1")
	return LayoutSegment(show=show, size=size)


def freeArea(
	numRows: int,
	numCols: int,
	occupied: Sequence[SegmentRect],
	within: SegmentRect | None = None,
) -> SegmentRect | None:
	"""The part of the display an app layout may have.

	On a display of more than one row, a row is taken if anything occupied touches it, and the
	area is an unbroken run of rows nothing touches. On a display of one row, the same in cells.

	**The run the focus is in.** A layout arranges the focus among other things, so it has to
	be where the focus is: the focus segment is never pinned, so it always lies in a free run,
	and that run is the area. Only when no focus is given is it the longest run, the earlier on
	a tie.

	Taking one run rather than every free row is deliberate: one layout split around a pin
	would be one layout read in two places, and a gap left over stays as the reader arranged
	it.

	:param numRows: rows on the display.
	:param numCols: cells across it.
	:param occupied: the rectangles of what is already there: pins, and claims that are not the
		focus itself.
	:param within: the focus segment's rectangle, whose run is wanted.
	:return: the area, or None if nothing is free.
	"""
	if numRows < 1 or numCols < 1:
		return None
	if numRows > 1:
		taken = [any(rect.row <= row < rect.endRow for rect in occupied) for row in range(numRows)]
		run = _runAt(taken, within.row) if within is not None else _longestRun(taken)
		if run is None:
			return None
		start, length = run
		return SegmentRect(row=start, col=0, numRows=length, numCols=numCols)
	taken = [any(rect.col <= col < rect.endCol for rect in occupied) for col in range(numCols)]
	run = _runAt(taken, within.col) if within is not None else _longestRun(taken)
	if run is None:
		return None
	start, length = run
	return SegmentRect(row=0, col=start, numRows=1, numCols=length)


def _longestRun(taken: Sequence[bool]) -> tuple[int, int] | None:
	""":return: (start, length) of the longest run of untaken places, earliest on a tie."""
	best = None
	start = None
	for index, isTaken in enumerate([*taken, True]):
		if not isTaken:
			if start is None:
				start = index
			continue
		if start is not None:
			length = index - start
			if best is None or length > best[1]:
				best = (start, length)
			start = None
	return best


def _runAt(taken: Sequence[bool], place: int) -> tuple[int, int] | None:
	""":return: (start, length) of the run of untaken places holding one, or the longest run
	if that place is taken or out of range, which only a stale focus rectangle can make it."""
	if not 0 <= place < len(taken) or taken[place]:
		return _longestRun(taken)
	start = place
	while start > 0 and not taken[start - 1]:
		start -= 1
	end = place + 1
	while end < len(taken) and not taken[end]:
		end += 1
	return start, end - start


def bandsIn(area: SegmentRect, bands: Sequence[Band]) -> list[Band]:
	"""The physical displays the area reaches, clipped to it.

	:param area: the free area.
	:param bands: every physical display, top first. Empty for an ordinary display, which is
		then one band as wide as the area.
	"""
	if not bands:
		return [Band(area.row, area.numRows, area.col + area.numCols)]
	clipped = []
	for band in bands:
		start = max(band.rowStart, area.row)
		end = min(band.rowEnd, area.endRow)
		if start < end:
			clipped.append(Band(start, end - start, band.numCols))
	return clipped


def displayArea(area: SegmentRect, bands: Sequence[Band] = ()) -> DisplayArea:
	""":return: what the app module is told about an area. See L{DisplayArea.columns}."""
	if area.numRows == 1:
		return DisplayArea(rows=1, columns=area.numCols)
	reached = bandsIn(area, bands)
	columns = min((band.numCols for band in reached), default=area.numCols)
	return DisplayArea(rows=area.numRows, columns=min(columns, area.numCols))


def resolveSizes(layout: AppLayout, extent: int, unit: str = "rows") -> list[int]:
	"""Give the segment without a size what the others leave.

	:param layout: the layout.
	:param extent: rows of the area, or cells of a one row area.
	:param unit: what the extent counts, for the message.
	:return: one size per segment.
	:raises LayoutError: if the sizes do not fit. With a segment left to take the rest, the
		others must leave it at least one; without one, they must add up to the area exactly,
		as the reader's own segment sizes must.
	"""
	given = sum(segment.size for segment in layout.segments if segment.size is not None)
	if any(segment.size is None for segment in layout.segments):
		rest = extent - given
		if rest < 1:
			raise LayoutError(
				f"layout {layout.name!r} asks for {given} of {extent} {unit} and leaves nothing "
				"for the segment without a size",
			)
		return [segment.size if segment.size is not None else rest for segment in layout.segments]
	if given != extent:
		raise LayoutError(f"layout {layout.name!r} asks for {given} {unit} and the area has {extent}")
	return [segment.size for segment in layout.segments]


def placeSegments(layout: AppLayout, area: SegmentRect, bands: Sequence[Band] = ()) -> list[SegmentRect]:
	"""Where each segment of a layout goes, in display coordinates.

	A one row area is divided left to right in cells. Any other is divided top to bottom in
	rows, and each segment is as wide as the display it is on, so none reaches cells that no
	hardware has.

	**A segment never crosses from one display to another**, and the app module is not told
	when one would have. A boundary inside a segment is settled by moving one edge onto it:

	1. The segment's lower edge, if anything comes after it. The rows it gives up go to the
	   segment without a size if that comes after it, and otherwise to the segment directly
	   after, so everything above the boundary stays as asked.
	2. Its upper edge, if it is the last. The rows it gives up go to the segment without a size
	   if that comes before it, and otherwise to the segment directly before.

	So with a Monarch of ten rows above a Focus 80, a layout of six rows and the rest would put
	its second segment over the Monarch's last four rows and the Focus. It is the last, so it
	starts at the Focus instead, and the first grows to the whole Monarch. With the Focus on
	top, a layout of three rows and the rest becomes one row on the Focus and ten on the
	Monarch.

	:param layout: the layout.
	:param area: the free area, from L{freeArea}.
	:param bands: the physical displays, top first, or empty for an ordinary display.
	:return: one rectangle per segment, in the layout's order.
	:raises LayoutError: if the sizes do not fit, or a boundary cannot be settled, as when a
		layout of one segment covers two displays.
	"""
	if area.numRows == 1:
		sizes = resolveSizes(layout, area.numCols, "cells")
		rects = []
		col = area.col
		for size in sizes:
			rects.append(SegmentRect(row=area.row, col=col, numRows=1, numCols=size))
			col += size
		return rects
	reached = bandsIn(area, bands)
	sizes = resolveSizes(layout, area.numRows)
	unsized = next((index for index, segment in enumerate(layout.segments) if segment.size is None), None)
	for _attempt in range(MAX_ADJUSTMENTS):
		crossing = _firstCrossing(sizes, area.row, reached)
		if crossing is None:
			return [
				SegmentRect(row=row, col=0, numRows=size, numCols=_widthAt(row, reached, area))
				for row, size in zip(_starts(sizes, area.row), sizes)
			]
		index, boundary, start, end = crossing
		_settle(layout.name, sizes, unsized, index, boundary, start, end)
	raise LayoutError(f"layout {layout.name!r} could not be fitted to the displays it reaches")


def _starts(sizes: Sequence[int], top: int) -> list[int]:
	""":return: the first row of each segment."""
	starts = []
	row = top
	for size in sizes:
		starts.append(row)
		row += size
	return starts


def _firstCrossing(sizes: Sequence[int], top: int, bands: Sequence[Band]) -> tuple[int, int, int, int] | None:
	""":return: (segment, boundary row, its start, its end) for the topmost segment a display
	boundary falls inside, or None if none does."""
	boundaries = [band.rowStart for band in bands[1:]]
	for index, (start, size) in enumerate(zip(_starts(sizes, top), sizes)):
		end = start + size
		for boundary in boundaries:
			if start < boundary < end:
				return index, boundary, start, end
	return None


def _settle(
	name: str,
	sizes: list[int],
	unsized: int | None,
	index: int,
	boundary: int,
	start: int,
	end: int,
) -> None:
	"""Move one edge of a segment onto the boundary inside it. See L{placeSegments}."""
	last = len(sizes) - 1
	if last == 0:
		raise LayoutError(f"layout {name!r} is one segment, and one segment cannot cover two displays")
	if index < last:
		given = end - boundary
		sizes[index] = boundary - start
		receiver = unsized if unsized is not None and unsized > index else index + 1
	else:
		given = boundary - start
		sizes[index] = end - boundary
		receiver = unsized if unsized is not None and unsized < index else index - 1
	sizes[receiver] += given


def _widthAt(row: int, bands: Sequence[Band], area: SegmentRect) -> int:
	""":return: how many cells the display holding a row has, within the area."""
	for band in bands:
		if band.rowStart <= row < band.rowEnd:
			return min(band.numCols, area.col + area.numCols)
	return area.numCols


def segmentKey(index: int) -> str:
	""":return: the key of one segment of the app layout, by its place in the layout."""
	return f"{PANEL_NAME}.{index}"


def isAppLayoutKey(key: str) -> bool:
	""":return: whether a segment key belongs to the app layout's claim."""
	return key.startswith(f"{PANEL_NAME}.")


class AppLayoutPanel(BraillePanel):
	"""The claim an app layout is shown in: the free area, cut into the placed segments.

	**Reserved, except where the focus is.** The segments showing objects belong to the layout,
	so the reader's pin commands refuse them as they refuse any claim, and nothing else draws
	there. The focus segment is ordinary display space, exactly as the reader's own focus
	segment is, because what lands in it is NVDA's own focus content.

	Ranked above the flow and below a drawing; see `panels.PRIORITY_APP_LAYOUT`.
	"""

	priority = PRIORITY_APP_LAYOUT

	def __init__(self, area: SegmentRect, rects: Sequence[SegmentRect], focusIndex: int) -> None:
		"""
		:param area: the free area, which is the claim.
		:param rects: where each segment goes, from L{placeSegments}.
		:param focusIndex: which of them follows the focus.
		"""
		super().__init__(PANEL_NAME, area, reserve=True, focusSegmentKey=segmentKey(focusIndex))
		self.rects = list(rects)
		self.focusIndex = focusIndex

	def segments(self) -> list[SegmentSpec]:
		specs = []
		for index, rect in enumerate(self.rects):
			spec = self.buildSpec(str(index), rect)
			if index == self.focusIndex:
				spec = dataclasses.replace(spec, owner=None)
			specs.append(spec)
		return specs


def askAppModule(appModule: Any, focus: Any, area: DisplayArea) -> tuple[AppLayout | None, str | None]:
	"""Ask an app module for its layout here, and read the answer.

	Never raises: an app module is someone else's code, and a mistake in it must cost the
	reader nothing but the layout.

	:param appModule: the focus's app module, which may have no layout method at all.
	:param focus: the focus, passed to the method.
	:param area: the room it has.
	:return: (layout, None), or (None, None) when there is no layout here, or (None, reason)
		when the app module's answer could not be used.
	"""
	method = getattr(appModule, LAYOUT_METHOD, None)
	if method is None:
		return None, None
	if not callable(method):
		return None, f"{LAYOUT_METHOD} is not a method"
	try:
		raw = method(focus, area)
	except Exception as error:
		return None, f"{LAYOUT_METHOD} raised {type(error).__name__}: {error}"
	try:
		return parseLayout(raw), None
	except LayoutError as error:
		return None, str(error)


def resolveShown(segment: LayoutSegment) -> tuple[Any, str | None]:
	"""What a segment shows now.

	:return: (object, None), or (None, reason) when a callable raised. The focus segment
		answers (None, None), since what it shows is not the layout's to say.
	"""
	if segment.isFocus:
		return None, None
	if not segment.isResolved:
		return segment.show, None
	try:
		return segment.show(), None
	except Exception as error:
		return None, f"raised {type(error).__name__}: {error}"


def describeObject(obj: Any) -> str:
	""":return: a short name for an object in a report, never raising."""
	if obj is None:
		return "nothing"
	try:
		name = getattr(obj, "name", None)
		role = getattr(obj, "role", None)
	except Exception:
		return type(obj).__name__
	roleName = getattr(role, "name", None) or (str(role) if role is not None else None)
	parts = [part for part in (roleName, repr(name) if name else None) if part]
	return " ".join(parts) or type(obj).__name__


def describeLayout(
	layout: AppLayout,
	rects: Sequence[SegmentRect],
	resolve: Callable[[LayoutSegment], tuple[Any, str | None]] = resolveShown,
) -> list[str]:
	""":return: a line per segment of a placed layout, for the debug report."""
	lines = [f"Layout {layout.name!r}, {len(layout.segments)} segments"]
	for index, (segment, rect) in enumerate(zip(layout.segments, rects)):
		where = f"rows {rect.row} to {rect.endRow - 1}" if rect.numRows > 1 else f"row {rect.row}"
		where += f", cells {rect.col} to {rect.endCol - 1}"
		asked = "asked for the rest" if segment.size is None else f"asked for {segment.size}"
		if segment.isFocus:
			what = "the focus"
		else:
			shown, problem = resolve(segment)
			if problem is not None:
				what = f"a callable that {problem}"
			elif segment.isResolved:
				what = f"asked each time, now {describeObject(shown)}"
			else:
				what = describeObject(shown)
		lines.append(f"  {index}: {where} ({asked}): {what}")
	return lines
