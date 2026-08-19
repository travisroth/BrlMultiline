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


def preferredDevice(preferred: str = "", devices: "list[DeviceInfo] | None" = None) -> "DeviceInfo | None":
	"""Choose the physical display a claim covering a whole display should be made on.

	A claim may not straddle two pieces of hardware, and on a composite it may not reach the
	dead columns beside a narrow one, so anything wanting "the display" has to mean one of
	them. This is that choice, in one place, so that the settings dialog and the code making
	the claim cannot disagree about which display was meant.

	The named display when it is there, and otherwise the tallest, because rows are what
	spatial reading spends: a Monarch's eight rows of thirty-two are worth more to it than a
	Focus 80's single row of eighty. Ties are broken by width.

	:param preferred: the driver name asked for, empty to take the tallest.
	:param devices: the physical displays, read from the driver when not given.
	:return: the display to claim on, or None when there is no composite and the caller
		should use the whole display it already has.
	"""
	devices = deviceMap() if devices is None else devices
	if not devices:
		return None
	if preferred:
		for device in devices:
			if device.driverName == preferred:
				return device
		# Configured for hardware that is not here. The tallest is a better answer than
		# nothing: a reader whose second display is switched off still wants their flow.
		log.debug(f"BrlMultiline: {preferred} is not among the displays in use")
	return max(devices, key=lambda device: (device.numRows, device.numCols))


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


def membersChangedAction():
	"""Find the notification the composite raises when its displays change.

	The one place in this module that does import the driver, and it is worth saying why the
	rule bends here. The rule exists because everything else in this module runs while a view
	is being built, several times a session; this runs once, when the plugin starts, and NVDA
	has already imported every display driver by then in order to list them. What it must not
	do is fail: an add-on installed without its driver, or a driver that will not import, is a
	reason to go without this notification and not a reason to go without the plugin.

	:return: the `extensionPoints.Action`, or None if the driver cannot be reached.
	"""
	try:
		from brailleDisplayDrivers.brlMultilineVirtual.events import membersChanged

		return membersChanged
	except Exception:
		log.debugWarning(
			"BrlMultiline: the combined display cannot be reached, so its members changing "
			"will not be noticed",
			exc_info=True,
		)
		return None


def memberStates(display=None) -> dict[str, bool] | None:
	"""Say which of the composite's members it is actually driving.

	Only the composite can answer this, and only while it is the display in use: a driver name
	in the configuration is a choice, not a connection, and nothing outside the running display
	knows which of those choices was opened.

	What "being driven" means is worth being exact about, because it is narrower than
	"connected". A member appears here if the composite opened it at braille start and has not
	since given up on it, and it is given up on when a write to it raises — which is only ever
	found out by writing. A display holding still content, a pinned object say, may be off or
	out of range for some time before anything tries to write to it and notices. Watching for
	that, and taking a display back when it returns, is the resilience work that has not been
	done yet.

	:param display: the display to read, or None for the one NVDA is driving.
	:return: True for a member being driven and False for one given up on, by driver name; or
		None when the composite is not the display in use, in which case nothing here can say
		anything about any display at all.
	"""
	if display is None:
		handler = braille.handler
		display = handler.display if handler is not None else None
	if not isVirtualDisplay(display):
		return None
	try:
		return {slot.driverName: not slot.failed for slot in display.slots}
	except Exception:
		log.error("BrlMultiline: could not read the virtual display's members", exc_info=True)
		return None


def configuredMembers(display=None) -> list[str] | None:
	"""Read which displays the running composite was opened for.

	Not the same as which it is driving. A member switched off at startup, or one given up on
	since, is still one of the displays the composite was built for, and only the composite
	remembers that: the configuration says what is wanted now, and `deviceMap` says what is
	working now, and neither answers what the running display was told.

	:param display: the display to read, or None for the one NVDA is driving.
	:return: the driver names in stacking order, or None when the composite is not the display
		in use and so has nothing to say.
	"""
	if display is None:
		handler = braille.handler
		display = handler.display if handler is not None else None
	if not isVirtualDisplay(display):
		return None
	try:
		return list(display.configuredMembers)
	except Exception:
		log.error("BrlMultiline: could not read the virtual display's configured members", exc_info=True)
		return None


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
			# A member the composite has given up on is not part of the display any more: its
			# rows have been taken out of the geometry, and a band for it here would put
			# segments on hardware that is not there. L{memberStates} is where it is still
			# visible, because "configured and gone" is worth saying and this is not the place.
			if not slot.failed
		]
	except Exception:
		# A display naming itself ours but not shaped like ours. Reporting nothing leaves the
		# add-on arranging the display from the user's own settings, which is wrong about the
		# dead columns but is at least a display.
		log.error("BrlMultiline: could not read the virtual display's device map", exc_info=True)
		return []
