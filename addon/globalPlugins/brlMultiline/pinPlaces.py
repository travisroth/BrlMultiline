# BrlMultiline: places, where pins make their own room.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Places on the display that pins share, divided again whenever a pin comes, goes or changes.

A place is the segment that follows the focus, which keeps a share for the focus, or the whole
of a combined display the focus is not on. Its pins are laid into the reader's own arrangement
as ordinary segments, so everything that works with a pin in a configured segment works with
one in a place. See docs/design/pin-places-plan.md.

Imports nothing from NVDA except the log.
"""

import dataclasses
from collections.abc import Iterable, Mapping, Sequence

from logHandler import log

from .appLayout import freeArea
from .layout import SegmentRect, rectContains, rectsIntersect
from .panels import BlankPanel, BraillePanel, SinglePanel
from .views import SegmentView, deviceFallbackView, singleSegmentView

PANEL_PREFIX = "place"
"""The prefix of every segment key a place makes."""

FOCUS_PLACE = "focus"
"""The id of the place in the segment that follows the focus. Other places are named by the
driver name of their display."""

MIN_PLACE_SHARE = 0.25
"""The least of a one row place that each pin, and the focus, keeps, as a share of the row.

A share rather than a count, since a fixed count would suit an 80 cell display and not a 40."""


class PlaceError(ValueError):
	"""A place that has no room for what was asked of it."""

	def __init__(self, message: str, room: int | None = None) -> None:
		"""
		:param message: what did not fit, for the log.
		:param room: the most the pin asked about could have, for the reader, where known.
		"""
		super().__init__(message)
		self.room = room


@dataclasses.dataclass
class PlacePin:
	"""One pin in a place."""

	key: str
	"""The key of the segment the pin is in. Kept while the place is divided again, so the pin
	stays filed under it."""

	size: int | None = None
	"""Rows, or cells in a one row place. None fills, sharing what the sized pins leave."""


@dataclasses.dataclass
class Place:
	"""A place and its members in display order: pins, and in the focus place the focus."""

	placeId: str
	members: list[PlacePin | None]
	"""None is the focus, which only the focus place has, and which is always a fill."""

	@property
	def isFocusPlace(self) -> bool:
		return self.placeId == FOCUS_PLACE

	@property
	def pins(self) -> list[PlacePin]:
		return [member for member in self.members if member is not None]

	def sizes(self) -> list[int | None]:
		""":return: each member's size, None for a fill, the focus included."""
		return [None if member is None else member.size for member in self.members]


def placeKey(placeId: str, number: int) -> str:
	""":return: the key of a pin's segment in a place."""
	return f"{PANEL_PREFIX}.{placeId}.{number}"


def isPlaceKey(key: str) -> bool:
	""":return: whether a segment key is a pin's in a place."""
	return key.startswith(f"{PANEL_PREFIX}.")


def extentOf(area: SegmentRect) -> int:
	""":return: what a place divides: its rows, or its cells when it is one row."""
	return area.numRows if area.numRows > 1 else area.numCols


def minimumFor(area: SegmentRect) -> int:
	""":return: the least each member of a place keeps. See L{MIN_PLACE_SHARE}."""
	if area.numRows > 1:
		return 1
	return max(1, int(area.numCols * MIN_PLACE_SHARE))


def divide(extent: int, sizes: Sequence[int | None], minimum: int) -> list[int]:
	"""Share a place among its members.

	Sized members get what they asked for, and the fills share what is left equally, a unit left
	over going to the earlier fill. With no fill, what is left is left blank.

	:param extent: rows of the place, or cells of a one row place.
	:param sizes: each member's size, None for a fill.
	:param minimum: the least each member keeps.
	:return: one size per member.
	:raises PlaceError: if a member would get less than the minimum, or the sizes do not fit.
	"""
	fixed = sum(size for size in sizes if size is not None)
	fills = sum(size is None for size in sizes)
	if any(size is not None and size < minimum for size in sizes):
		raise PlaceError(f"a size is below the least a member keeps, {minimum}")
	rest = extent - fixed
	if not fills:
		if rest < 0:
			raise PlaceError(f"the sizes add up to {fixed} and the place has {extent}")
		return [size for size in sizes if size is not None]
	share, extra = divmod(rest, fills)
	if share < minimum:
		raise PlaceError(f"{fills} fills share {rest} and each keeps at least {minimum}")
	result = []
	seen = 0
	for size in sizes:
		if size is None:
			result.append(share + (1 if seen < extra else 0))
			seen += 1
		else:
			result.append(size)
	return result


def roomFor(extent: int, sizes: Sequence[int | None], minimum: int, index: int) -> int:
	""":return: the most one member could have, the others keeping their sizes or the minimum.

	:param index: the member asked about. Its own size is not counted.
	"""
	others = [size for place, size in enumerate(sizes) if place != index]
	taken = sum(minimum if size is None else size for size in others)
	return max(0, extent - taken)


def stack(area: SegmentRect, sizes: Sequence[int]) -> list[SegmentRect]:
	""":return: the members' rectangles in display order: rows, or cells in a one row place."""
	rects = []
	if area.numRows > 1:
		row = area.row
		for size in sizes:
			rects.append(SegmentRect(row, area.col, size, area.numCols))
			row += size
	else:
		col = area.col
		for size in sizes:
			rects.append(SegmentRect(area.row, col, 1, size))
			col += size
	return rects


def placeRects(place: Place, area: SegmentRect) -> list[SegmentRect]:
	""":return: one rectangle per member of a place.

	:raises PlaceError: if the place has no room for its members.
	"""
	return stack(area, divide(extentOf(area), place.sizes(), minimumFor(area)))


def placeAreas(view: SegmentView, devices: Iterable) -> dict[str, SegmentRect]:
	"""Where each place is, in the reader's own arrangement.

	:param view: the arrangement places are laid into.
	:param devices: the physical displays behind a combined one, empty for an ordinary display.
	:return: the area of each place by id: the focus segment, and every display it is not on.
	"""
	focusRect = next((spec.rect for spec in view.flatten() if spec.key == view.focusSegmentKey), None)
	if focusRect is None:
		return {}
	areas = {FOCUS_PLACE: focusRect}
	for device in devices:
		live = device.liveRect
		if not rectsIntersect(live, focusRect):
			areas[device.driverName] = live
	return areas


def withPlaces(
	view: SegmentView,
	places: Iterable[Place],
	areas: Mapping[str, SegmentRect],
	numRows: int,
	numCols: int,
) -> SegmentView:
	"""Lay places into an arrangement.

	The focus place replaces the focus segment's panel with the focus and its pins, and a
	display's place replaces every panel on that display. A place that does not fit is left out
	and logged, and its pins, having no segment, are released by the rebuild.

	:param view: the reader's arrangement.
	:param places: the places that hold pins.
	:param areas: where each place is. See L{placeAreas}.
	:param numRows: rows of the display.
	:param numCols: cells across it.
	:return: the arrangement with the places in it, or the arrangement as it was if there are none.
	"""
	panels: list[BraillePanel] = list(view.panels)
	laid = False
	for place in places:
		if not place.pins:
			continue
		area = areas.get(place.placeId)
		try:
			if area is None:
				raise PlaceError("there is no such place on this display")
			rects = placeRects(place, area)
			panels = _laidIn(panels, place, area, rects, view.focusSegmentKey)
		except PlaceError as error:
			log.warning(f"BrlMultiline: place {place.placeId!r} cannot be laid out: {error}")
			continue
		laid = True
	if not laid:
		return view
	placed = SegmentView(f"{view.name}+places", panels, view.focusSegmentKey)
	try:
		placed.validate(numRows, numCols)
	except (ValueError, LookupError):
		log.warning("BrlMultiline: the places do not fit this arrangement; leaving them out", exc_info=True)
		return view
	return placed


def _laidIn(
	panels: list[BraillePanel],
	place: Place,
	area: SegmentRect,
	rects: Sequence[SegmentRect],
	focusKey: str | None,
) -> list[BraillePanel]:
	""":return: the panels with one place laid in.

	:raises PlaceError: if the focus place's area is not the focus segment's own panel.
	"""
	if place.isFocusPlace:
		replaced = [panel for panel in panels if panel.rect == area and panel.name == focusKey]
		if not replaced:
			raise PlaceError("the focus segment is not a panel of its own")
	else:
		replaced = [panel for panel in panels if rectContains(area, panel.rect)]
	kept = [panel for panel in panels if panel not in replaced]
	for member, rect in zip(place.members, rects):
		name = focusKey if member is None else member.key
		kept.append(SinglePanel(name, rect, reserve=False))
	used = sum(extentOf(rect) for rect in rects)
	if used < extentOf(area):
		rest = SegmentRect(area.row + used, area.col, area.numRows - used, area.numCols)
		if area.numRows == 1:
			rest = SegmentRect(area.row, area.col + used, 1, area.numCols - used)
		kept.append(BlankPanel(rest, name=f"{PANEL_PREFIX}.{place.placeId}.rest"))
	return kept


def undividedView(view: SegmentView, numRows: int, numCols: int, devices: Sequence) -> SegmentView:
	"""The arrangement while pins are set aside: each display one segment, so the focus, the flow
	or an app layout has every row the pins had.

	:param view: the reader's arrangement, for which display the focus is on.
	:param devices: the physical displays behind a combined one, empty for an ordinary display.
	"""
	if not devices:
		return singleSegmentView(numRows, numCols)
	fallback = deviceFallbackView(numRows, numCols, devices)
	focusRect = next((spec.rect for spec in view.flatten() if spec.key == view.focusSegmentKey), None)
	focusKey = next(
		(
			spec.key
			for spec in fallback.flatten()
			if focusRect is not None and rectsIntersect(spec.rect, focusRect)
		),
		fallback.focusSegmentKey,
	)
	return SegmentView("pinsAside", fallback.panels, focusKey)


def flowRoom(
	rect: SegmentRect,
	pinRects: Sequence[SegmentRect],
	focusRect: SegmentRect | None,
	numRows: int,
	numCols: int,
) -> SegmentRect:
	"""Where the flow band goes when pins in places take some of the rows it wants.

	The run of its rows no pin touches that holds the focus, or else the longest. Rows only,
	since a flow needs two of them.

	:param rect: the band the flow settings ask for.
	:param pinRects: the rectangles of the pins in places.
	:param focusRect: the focus segment, among the pins.
	:return: the band to claim, which is `rect` when no pin is in its way. Its rows may be fewer
		than a flow needs, which the band then refuses.
	"""
	if rect.numRows < 2 or not any(rectsIntersect(rect, pin) for pin in pinRects):
		return rect
	outside = [
		SegmentRect(0, 0, rect.row, numCols),
		SegmentRect(rect.endRow, 0, numRows - rect.endRow, numCols),
	]
	occupied = [*pinRects, *(each for each in outside if each.numRows > 0)]
	within = focusRect if focusRect is not None and rect.row <= focusRect.row < rect.endRow else None
	run = freeArea(numRows, numCols, occupied, within)
	if run is None:
		return SegmentRect(rect.row, rect.col, 1, rect.numCols)
	return SegmentRect(run.row, rect.col, run.numRows, rect.numCols)


class Places:
	"""Every place holding pins, and the changes a reader makes to them.

	Changes are checked against the place's area before they are kept, so a place never holds
	more than it has room for. The pins themselves are the plugin's.
	"""

	def __init__(self) -> None:
		self.places: dict[str, Place] = {}
		self._nextNumber = 0

	def __iter__(self):
		return iter(list(self.places.values()))

	def placeOf(self, key: str) -> Place | None:
		""":return: the place a pin is in, or None if it is not in one."""
		for place in self.places.values():
			if any(pin.key == key for pin in place.pins):
				return place
		return None

	def pinFor(self, key: str) -> PlacePin | None:
		place = self.placeOf(key)
		return next((pin for pin in place.pins if pin.key == key), None) if place is not None else None

	def keys(self) -> set[str]:
		""":return: the keys of every pin in a place."""
		return {pin.key for place in self.places.values() for pin in place.pins}

	def membersFor(self, placeId: str) -> list[PlacePin | None]:
		""":return: a copy of a place's members, the focus alone for an empty focus place."""
		place = self.places.get(placeId)
		if place is not None:
			return list(place.members)
		return [None] if placeId == FOCUS_PLACE else []

	def roomIn(self, placeId: str, area: SegmentRect, key: str | None = None) -> int:
		""":return: the largest size a pin could have in a place, the others keeping their sizes or
		the least they keep.

		:param key: a pin already there, whose own size is not counted, or None for a new pin.
		"""
		sizes = [
			None if member is None else member.size
			for member in self.membersFor(placeId)
			if member is None or member.key != key
		]
		return roomFor(extentOf(area), [*sizes, None], minimumFor(area), len(sizes))

	def add(
		self,
		placeId: str,
		area: SegmentRect,
		size: int | None,
		before: bool = False,
		atStart: bool = False,
	) -> PlacePin:
		"""Make room for a pin in a place.

		:param placeId: which place.
		:param area: where it is now.
		:param size: rows, or cells in a one row place, or None to fill.
		:param before: in the focus place, before the focus rather than after it.
		:param atStart: before the place's pins on that side rather than after them.
		:return: the new pin, whose key the caller files the monitor under.
		:raises PlaceError: if there is no room. Nothing is changed.
		"""
		pin = PlacePin(placeKey(placeId, self._nextNumber), size)
		members = self.membersFor(placeId)
		focusAt = members.index(None) if None in members else None
		if focusAt is None:
			start, end = 0, len(members)
		elif before:
			start, end = 0, focusAt
		else:
			start, end = focusAt + 1, len(members)
		members.insert(start if atStart else end, pin)
		self._check(placeId, members, area, members.index(pin))
		self._nextNumber += 1
		self.places[placeId] = Place(placeId, members)
		return pin

	def resize(self, key: str, area: SegmentRect, size: int | None) -> None:
		"""Give a pin another size.

		:raises PlaceError: if there is no room. Nothing is changed.
		:raises LookupError: if the pin is in no place.
		"""
		place = self._placeFor(key)
		members = [
			PlacePin(member.key, size) if member is not None and member.key == key else member
			for member in place.members
		]
		index = next(at for at, member in enumerate(members) if member is not None and member.key == key)
		self._check(place.placeId, members, area, index)
		place.members = members

	def move(self, key: str, by: int) -> bool:
		"""Move a pin earlier or later in its place, past the focus too.

		Sizes are not checked again, since moving changes no member's share.

		:param by: -1 for earlier, 1 for later.
		:return: whether it moved, which it does not at either end.
		:raises LookupError: if the pin is in no place.
		"""
		place = self._placeFor(key)
		index = next(
			at for at, member in enumerate(place.members) if member is not None and member.key == key
		)
		other = index + by
		if not 0 <= other < len(place.members):
			return False
		place.members[index], place.members[other] = place.members[other], place.members[index]
		return True

	def remove(self, key: str) -> bool:
		""":return: whether the pin was in a place. A place with no pins left is forgotten."""
		place = self.placeOf(key)
		if place is None:
			return False
		place.members = [member for member in place.members if member is None or member.key != key]
		if not place.pins:
			del self.places[place.placeId]
		return True

	def keepOnly(self, keys: Iterable[str]) -> bool:
		"""Forget the pins not among some keys, as when the plugin let them go.

		:return: whether any was forgotten.
		"""
		alive = set(keys)
		gone = self.keys() - alive
		for key in gone:
			self.remove(key)
		return bool(gone)

	def _placeFor(self, key: str) -> Place:
		place = self.placeOf(key)
		if place is None:
			raise LookupError(f"No place holds a pin on {key!r}")
		return place

	def _check(self, placeId: str, members: list[PlacePin | None], area: SegmentRect, index: int) -> None:
		""":raises PlaceError: with the room one member has, if the place cannot hold its members."""
		sizes = [None if member is None else member.size for member in members]
		try:
			divide(extentOf(area), sizes, minimumFor(area))
		except PlaceError as error:
			room = roomFor(extentOf(area), sizes, minimumFor(area), index)
			raise PlaceError(str(error), room=room) from None
