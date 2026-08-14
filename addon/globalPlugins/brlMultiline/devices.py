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
