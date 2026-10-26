# BrlMultiline: reading the virtual display's device map.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Which physical displays are behind the display NVDA thinks it has.

With `brlMultilineVirtual` in use, each physical display owns a band of rows, and a narrower
one has dead columns. This is the one place that asks; everything above works in bands.

The driver is not imported, which would pull in `hwIo` and the driver base and could break
the view layer's import; its published live object is read instead.
"""

from typing import NamedTuple

import braille
from logHandler import log

from .layout import SegmentRect, rectContains

VIRTUAL_DISPLAY_NAME = "brlMultilineVirtual"
"""The name of the add-on's own braille display driver.

Duplicated rather than imported; see the module docstring. NVDA requires it to equal the
module file name.
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

		The key `bmConfig.getDisplayKey` composes for the display alone, so a member keeps its
		layout.
		"""
		return f"{self.driverName}_{self.numRows}x{self.numCols}"

	@property
	def band(self) -> tuple[int, int, int]:
		""":return: this display's band, in the form `layout.deviceBandRects` takes."""
		return (self.rowStart, self.numRows, self.numCols)

	@property
	def liveRect(self) -> SegmentRect:
		""":return: the cells of the composite this display actually has, in composite coordinates.

		A band's live part, as `layout.deviceBandRects` has it, without the composite's width.
		"""
		return SegmentRect(row=self.rowStart, col=0, numRows=self.numRows, numCols=self.numCols)


MAX_UI_DISPLAYS = 3
"""How many physical displays the display relative commands are generated for.

A limit on the commands only, not on members.
"""

MAX_UI_DISPLAY_SEGMENTS = 4
"""How many segments per display the display relative commands are generated for.

A display split more finely than this is still reachable by the commands that name a segment
outright; these exist to be stable, not to be exhaustive.
"""


def preferredDevice(preferred: str = "", devices: "list[DeviceInfo] | None" = None) -> "DeviceInfo | None":
	"""Choose the physical display a claim covering a whole display should be made on.

	A claim must lie on one display, so the settings dialog and the claim ask here. The named
	display when present, else the tallest, since rows are what spatial reading spends; ties
	by width.

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
		# Configured for hardware that is not here; the tallest still gives a flow.
		log.debug(f"BrlMultiline: {preferred} is not among the displays in use")
	return max(devices, key=lambda device: (device.numRows, device.numCols))


def segmentsForDevice(rects, device: DeviceInfo) -> list[int]:
	"""Find the segments lying on one physical display.

	Full containment, so a straddling segment belongs to neither; the second line behind
	`views.validateAgainstHardware`, and what decides a display's panning keys.

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

	The stable way to name a segment, since an index moves when the layout changes.

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

	The one import of the driver, run once at start when NVDA has imported every driver
	anyway. A failure means going without the notification, not without the plugin.

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

	Only the running composite can answer. Driven is narrower than connected: a member is
	given up on when a write or read to it fails, so a display holding still content may be off
	for a while before that is noticed.

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

	Includes members off or given up on, unlike `memberStates`; the configuration says what is
	wanted now, not what the running display was told.

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
			# A member given up on has no rows any more; `memberStates` still reports it.
			if not slot.failed
		]
	except Exception:
		# A display naming itself ours but not shaped like ours. Reporting nothing leaves the
		# add-on arranging the display from the user's own settings, which is wrong about the
		# dead columns but is at least a display.
		log.error("BrlMultiline: could not read the virtual display's device map", exc_info=True)
		return []
