# BrlMultiline: reaching a display that can draw.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Finding the drawable surface behind NVDA's display, and converting cells to pins.

The one place that finds a display that can raise individual pins, so everything above works
in rectangles. The driver is not imported, for the reason `devices.py` gives; a display that
can draw publishes `graphicsSize`, `newGraphicsBuffer`, `setGraphicsOverlay`,
`clearGraphicsOverlay` and `lastTouch`, and that duck typing is the capability check.

Behind `brlMultilineVirtual`, whose `__getattr__` proxies settings only, the drawable member
is found among the slots and its rows offset by its band. Cells convert to pins by the pin
grid divided by the cell grid, which gives any display's pitch.
"""

from typing import NamedTuple

import braille
from logHandler import log

from .devices import VIRTUAL_DISPLAY_NAME
from .layout import SegmentRect

__all__ = [
	"GraphicsSurface",
	"PinRect",
	"findSurface",
]


class PinRect(NamedTuple):
	"""A rectangle of pins, in one display's own pin coordinates."""

	x: int
	"""Leftmost pin column."""

	y: int
	"""Topmost pin row."""

	width: int
	"""Pin columns across."""

	height: int
	"""Pin rows down."""

	@property
	def isEmpty(self) -> bool:
		""":return: whether this rectangle holds no pins at all."""
		return self.width <= 0 or self.height <= 0


class GraphicsSurface(NamedTuple):
	"""A display that can draw, and where it sits in the display NVDA thinks it has.

	Held for one operation, not cached, since a member can be given up on between commands.
	`findSurface` is cheap; call it again.
	"""

	driver: object
	"""The live driver instance that can draw. Duck typed, never imported."""

	pinWidth: int
	"""Pin columns this display has."""

	pinHeight: int
	"""Pin rows this display has."""

	numRows: int
	"""Braille lines this display shows, at its current pitch."""

	numCols: int
	"""Braille cells across this display."""

	rowStart: int
	"""Which row of NVDA's display this one's first row is.

	Zero when NVDA drives it directly; in a composite, where its band begins, subtracted from a
	claim's rows to reach the member's own pins.
	"""

	@property
	def pinsPerRow(self) -> int:
		""":return: pin rows from the top of one braille line to the top of the next.

		The pitch, derived rather than asked for. 5 where a display leaves a blank row after
		each line and 4 where it does not, on a 40 row grid showing 8 or 10 lines.
		"""
		return self.pinHeight // self.numRows if self.numRows else 0

	@property
	def pinsPerCol(self) -> int:
		""":return: pin columns from the left of one cell to the left of the next.

		3 on a display that leaves a gap column between cells, which is every braille display:
		the gap is there so a reader can tell one cell from the next.
		"""
		return self.pinWidth // self.numCols if self.numCols else 0

	def newBuffer(self, width: int, height: int):
		"""Make a buffer to draw into.

		:param width: dots across.
		:param height: dots down.
		:return: a blank buffer, or None if the driver would not make one.
		"""
		try:
			return self.driver.newGraphicsBuffer(width, height)
		except Exception:
			log.error("BrlMultiline: could not get a graphics buffer from the display", exc_info=True)
			return None

	def show(self, key: str, rect: PinRect, buffer) -> bool:
		"""Put a drawing on the display.

		:param key: names this drawing, so the same caller replaces its own.
		:param rect: where it goes, in this display's pins. Only the origin is used; the
			buffer's own size decides how far it reaches.
		:param buffer: what to draw.
		:return: whether the display took it.
		"""
		try:
			self.driver.setGraphicsOverlay(key, rect.x, rect.y, buffer)
			return True
		except Exception:
			log.error("BrlMultiline: could not put a drawing on the display", exc_info=True)
			return False

	def hide(self, key: str | None = None) -> None:
		"""Take a drawing off the display.

		Never raises, since it is called from teardown paths where the display may be gone.

		:param key: which drawing, or None for every one this display holds.
		"""
		try:
			self.driver.clearGraphicsOverlay(key)
		except Exception:
			log.debugWarning("BrlMultiline: could not clear a drawing", exc_info=True)

	def touchedPin(self) -> tuple | None:
		""":return: the pin last touched on this display, or None if none was.

		In this display's own pin coordinates, which is what `pointInRect` expects.
		"""
		try:
			return self.driver.lastTouch
		except Exception:
			log.debugWarning("BrlMultiline: could not read the touched pin", exc_info=True)
			return None

	def routingPin(self) -> tuple | None:
		"""Where the finger was at the last routing press, in this display's pins.

		Kept at the press, since the panel reports zero on release before a queued script runs.
		`touchedPin` is the live one, for polling while the finger is down.

		:return: the pin, or None where the display does not report one. A drawable display that
			cannot say where a press landed is allowed; the caller falls back to the cell.
		"""
		try:
			return self.driver.lastRoutingPin
		except Exception:
			log.debugWarning("BrlMultiline: could not read the pin at the routing press", exc_info=True)
			return None

	def pinRectForCells(self, rect: SegmentRect) -> PinRect:
		"""Convert a claim on cells into the pins it covers.

		The whole cell slot, gap column and blank row included, or every drawing would be striped.

		:param rect: the claim, in NVDA's display coordinates.
		:return: the pins it covers, in this display's own coordinates. Empty where the claim
			does not reach this display at all.
		"""
		perRow = self.pinsPerRow
		perCol = self.pinsPerCol
		if not perRow or not perCol:
			return PinRect(0, 0, 0, 0)
		localRow = rect.row - self.rowStart
		# Clipped to this display, because a claim is made against NVDA's display and a
		# composite's is taller than any one member. A drawing must not run off the end of
		# the member's pins on the strength of rows that belong to the display above it.
		top = max(0, localRow)
		bottom = min(self.numRows, localRow + rect.numRows)
		left = max(0, rect.col)
		right = min(self.numCols, rect.col + rect.numCols)
		if bottom <= top or right <= left:
			return PinRect(0, 0, 0, 0)
		return PinRect(
			x=left * perCol,
			y=top * perRow,
			width=(right - left) * perCol,
			height=(bottom - top) * perRow,
		)

	def cellForPin(self, x: int, y: int) -> tuple | None:
		"""Convert a pin on this display back to a cell of NVDA's display.

		The inverse of `pinRectForCells` at a point.

		:param x: pin column.
		:param y: pin row.
		:return: row and column in NVDA's display coordinates, or None if the pin is off this
			display.
		"""
		perRow = self.pinsPerRow
		perCol = self.pinsPerCol
		if not perRow or not perCol:
			return None
		if not (0 <= x < self.pinWidth and 0 <= y < self.pinHeight):
			return None
		return self.rowStart + y // perRow, x // perCol


def _isDrawable(driver) -> bool:
	""":return: whether a driver publishes the graphics capability.

	:param driver: any object, including None.
	"""
	if driver is None:
		return False
	return all(
		hasattr(driver, name)
		for name in ("graphicsSize", "newGraphicsBuffer", "setGraphicsOverlay", "clearGraphicsOverlay")
	)


def _surfaceFor(driver, rowStart: int) -> GraphicsSurface | None:
	"""Read a drawable driver's geometry.

	:param driver: a driver already known to publish the capability.
	:param rowStart: where its first row sits on NVDA's display.
	:return: the surface, or None if its geometry could not be read or makes no sense.
	"""
	try:
		pinWidth, pinHeight = driver.graphicsSize
		numRows = int(driver.numRows)
		numCols = int(driver.numCols)
	except Exception:
		log.error("BrlMultiline: a display claimed it could draw but would not say its size", exc_info=True)
		return None
	if min(pinWidth, pinHeight, numRows, numCols) <= 0:
		log.debugWarning(
			f"BrlMultiline: ignoring a drawable display reporting {pinWidth} by {pinHeight} pins "
			f"and {numRows} by {numCols} cells",
		)
		return None
	return GraphicsSurface(
		driver=driver,
		pinWidth=pinWidth,
		pinHeight=pinHeight,
		numRows=numRows,
		numCols=numCols,
		rowStart=rowStart,
	)


def findSurface(display=None) -> GraphicsSurface | None:
	"""Find the display that can draw, whether or not it is behind a composite.

	:param display: the display to look at, or None for the one NVDA is driving.
	:return: the surface, or None where nothing attached can draw, the ordinary case.
	"""
	if display is None:
		handler = braille.handler
		display = handler.display if handler is not None else None
	if display is None:
		return None
	if _isDrawable(display):
		return _surfaceFor(display, rowStart=0)
	if getattr(display, "name", None) != VIRTUAL_DISPLAY_NAME:
		return None
	try:
		slots = list(display.slots)
	except Exception:
		log.error(
			"BrlMultiline: could not read the composite's members to find one that draws",
			exc_info=True,
		)
		return None
	for slot in slots:
		# A member the composite has given up on has had its rows taken out of the geometry,
		# so a drawing placed against its band would land on rows that now belong to someone
		# else. `devices.deviceMap` skips failed members for the same reason.
		if getattr(slot, "failed", False):
			continue
		driver = getattr(slot, "driver", None)
		if not _isDrawable(driver):
			continue
		try:
			rowStart = slot.band.rowStart
		except Exception:
			log.error("BrlMultiline: a drawable member would not say where its band starts", exc_info=True)
			continue
		return _surfaceFor(driver, rowStart=rowStart)
	return None
