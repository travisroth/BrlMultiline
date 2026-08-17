# BrlMultiline: reading the virtual display's device map.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Which physical displays are behind the display NVDA thinks it has.

The add-on ships a braille display driver, `brailleDisplayDrivers.brlMultilineVirtual`, that
presents several physical displays to NVDA as one. When it is the display in use, the rows
of that composite are not uniform: each physical display owns a contiguous band of them, and
a display narrower than the widest one has columns that reach no hardware at all.

This module is the one place that knows how to ask. Everything above it works in bands, and
nothing above it imports the driver.

The driver is deliberately not imported. It is in the same add-on, so importing it would
work, but it pulls in `hwIo`, `inputCore` and the driver base class for the sake of one
string and three integers per display, and it would make the whole view layer fail to import
if anything in the driver ever failed to. Reading the live object the driver publishes costs
nothing and cannot fail that way.
"""

from typing import NamedTuple

import braille
from logHandler import log

from .layout import SegmentRect, rectContains

VIRTUAL_DISPLAY_NAME = "brlMultilineVirtual"
"""The name of the add-on's own braille display driver.

Duplicated from the driver rather than imported, for the reason in the module docstring. It
is a driver's module file name, which NVDA requires to equal its `name`, so it is as stable
as an identifier gets.
"""


class DeviceInfo(NamedTuple):
	"""One physical display's place in the composite."""

	driverName: str
	"""The NVDA driver behind it. Unique within a composite: the driver refuses to list one
	driver twice, because two displays sharing a driver cannot be told apart."""

	rowStart: int
	"""Index of the first row of the composite this display occupies."""

	numRows: int
	"""How many rows of the composite it occupies."""

	numCols: int
	"""How many cells wide it actually is, which may be narrower than the composite."""

	@property
	def rowEnd(self) -> int:
		""":return: one past the last row of the composite this display occupies."""
		return self.rowStart + self.numRows

	@property
	def displayKey(self) -> str:
		""":return: the configuration key this display has when it is used on its own.

		Deliberately the same key `bmConfig.getDisplayKey` composes for a display NVDA is
		driving directly, which is what makes a member keep the layout the user already chose
		for it. A Monarch divided into three segments on its own is divided into three
		segments as a band of a composite, with nothing to set up and nothing to explain.
		"""
		return f"{self.driverName}_{self.numRows}x{self.numCols}"

	@property
	def band(self) -> tuple[int, int, int]:
		""":return: this display's band, in the form `layout.deviceBandRects` takes."""
		return (self.rowStart, self.numRows, self.numCols)

	@property
	def liveRect(self) -> SegmentRect:
		""":return: the cells of the composite this display actually has, in composite coordinates.

		The same rectangle `layout.deviceBandRects` calls a band's live part, and computable
		without knowing the composite's width, because a display's own cells start at column 0
		and end where the hardware does. What the composite's width adds is the dead part.
		"""
		return SegmentRect(row=self.rowStart, col=0, numRows=self.numRows, numCols=self.numCols)


MAX_UI_DISPLAYS = 3
"""How many physical displays the display relative commands are generated for.

Three because the driver refuses two displays sharing a driver, so a fourth is unlikely to be
tellable apart from one of the first three. A limit on the commands only: a composite may have
as many members as the user can open.
"""

MAX_UI_DISPLAY_SEGMENTS = 4
"""How many segments per display the display relative commands are generated for.

A display split more finely than this is still reachable by the commands that name a segment
outright; these exist to be stable, not to be exhaustive.
"""


def segmentsForDevice(rects, device: DeviceInfo) -> list[int]:
	"""Find the segments lying on one physical display.

	Full containment rather than "where does it start", so that a segment straddling two
	displays belongs to neither rather than to whichever one holds its first row. Such a
	segment is refused by `views.validateAgainstHardware` before it can be shown, so this is
	the second line of the same defence — but it is the line that decides what a display's own
	panning keys do, and answering "this display" for a segment half of which is on the other
	one is worse than answering nothing.

	:param rects: the segment rectangles, in display order.
	:param device: the physical display.
	:return: their indices, in display order. Empty if the display holds no segment, which is
		the answer for a display made entirely of dead columns and for the single segment the
		plugin falls back to when a view cannot be shown, since that segment covers the whole
		composite and so sits on no one display.
	"""
	live = device.liveRect
	return [index for index, rect in enumerate(rects) if rectContains(live, rect)]


def resolveDisplaySegment(
	rects,
	devices: list[DeviceInfo],
	displayOrdinal: int,
	segmentOrdinal: int,
) -> int | None:
	"""Find a segment by which display it is on and where it sits on that display.

	This is the stable way to name a segment. A segment's index counts across the whole
	display and moves whenever the layout changes — the focus might be segments 0 and 1 today
	and 5 and 6 tomorrow — so a command bound to an index quietly starts addressing something
	else. Which display a segment is on, and how far down that display it is, survives every
	rearrangement that keeps the displays.

	An ordinary display answers as the first display, so a command bound for a composite goes
	on working when the second display is unplugged.

	:param rects: the segment rectangles, in display order.
	:param devices: the physical displays, in stacking order. Empty for an ordinary display.
	:param displayOrdinal: which display, counting from 0 at the top.
	:param segmentOrdinal: which of that display's segments, counting from 0.
	:return: the segment's index, or None if there is no such display or no such segment on it.
	"""
	if displayOrdinal < 0 or segmentOrdinal < 0:
		return None
	if not devices:
		if displayOrdinal != 0:
			return None
		return segmentOrdinal if segmentOrdinal < len(rects) else None
	if displayOrdinal >= len(devices):
		return None
	segments = segmentsForDevice(rects, devices[displayOrdinal])
	if segmentOrdinal >= len(segments):
		return None
	return segments[segmentOrdinal]


def isVirtualDisplay(display=None) -> bool:
	"""Say whether a display is the add-on's composite one.

	:param display: the display to test, or None for the one NVDA is driving.
	:return: whether it is the virtual display.
	"""
	if display is None:
		handler = braille.handler
		display = handler.display if handler is not None else None
	return display is not None and getattr(display, "name", None) == VIRTUAL_DISPLAY_NAME


def deviceMap(display=None) -> list[DeviceInfo]:
	"""Read the physical displays making up the composite.

	:param display: the display to read, or None for the one NVDA is driving.
	:return: one entry per physical display, in stacking order, top first. Empty when the
		display is an ordinary one, which is the usual case and is not an error.
	"""
	if display is None:
		handler = braille.handler
		display = handler.display if handler is not None else None
	if not isVirtualDisplay(display):
		return []
	try:
		return [
			DeviceInfo(
				driverName=slot.driverName,
				rowStart=slot.band.rowStart,
				numRows=slot.band.numRows,
				numCols=slot.band.numCols,
			)
			for slot in display.slots
		]
	except Exception:
		# A display naming itself ours but not shaped like ours. Reporting nothing leaves the
		# add-on arranging the display from the user's own settings, which is wrong about the
		# dead columns but is at least a display.
		log.error("BrlMultiline: could not read the virtual display's device map", exc_info=True)
		return []
