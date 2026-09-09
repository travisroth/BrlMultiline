# BrlMultiline: reaching a display that can draw.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Finding the drawable surface behind NVDA's display, and converting cells to pins.

Some braille displays can raise individual pins rather than only whole braille cells. The
add-on ships one such driver, `brailleDisplayDrivers.brlMultilineMonarch`, and there will be
others. This module is the one place that knows how to find such a display and how to
address it, so that everything above works in rectangles and nothing above imports a driver.

The driver is deliberately not imported, for the reason `devices.py` gives: it is in the same
add-on, so importing it would work, but it pulls in `hwIo`, `inputCore` and the driver base
class, and it would make everything above fail to import if anything in the driver ever
failed to. A display that can draw publishes `graphicsSize`, `newGraphicsBuffer`,
`setGraphicsOverlay`, `clearGraphicsOverlay` and `lastTouch`; one that cannot, does not, and
that duck typing is the whole of the capability check.

Two things make this more than an attribute lookup.

**The composite.** When `brlMultilineVirtual` is the display, `braille.handler.display` is the
composite and not the display that can draw. Its `__getattr__` proxies driver *settings*
names only, so `setGraphicsOverlay` is not reachable through it. The drawable member has to
be found among its slots and addressed directly, and its rows offset by where its band sits.

**Cells against pins.** Panels claim rectangles in display rows and columns. A drawing is
placed in pins. The conversion depends on the display's own pitch — a braille line is 5 pin
rows where a display leaves a blank row between lines and 4 where it does not — which is not
published as such and does not need to be: the pin grid divided by the cell grid gives it,
and gives it for any display rather than for the one we happen to have.
"""

from typing import NamedTuple, Optional

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

	Held for the length of one operation rather than cached. The drawable member of a
	composite can be given up on between one command and the next, and a stale surface would
	go on writing to a driver nothing is reading. `findSurface` is cheap; call it again.
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

	Zero when NVDA is driving it directly. Inside a composite it is where the member's band
	begins, and it is *subtracted* from a claim's rows to reach the member's own coordinates,
	because an overlay is placed in the member's pins and the member's pins start at its own
	row 0 however far down the composite it sits.
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

	def hide(self, key: Optional[str] = None) -> None:
		"""Take a drawing off the display.

		Never raises. This is called from teardown paths — an eviction, a rebuild, the add-on
		terminating — where the display may already have gone, and a drawing left behind
		because clearing it raised would be the worse outcome.

		:param key: which drawing, or None for every one this display holds.
		"""
		try:
			self.driver.clearGraphicsOverlay(key)
		except Exception:
			log.debugWarning("BrlMultiline: could not clear a drawing", exc_info=True)

	def touchedPin(self) -> Optional[tuple]:
		""":return: the pin last touched on this display, or None if none was.

		In this display's own pin coordinates, which is what `pointInRect` expects.
		"""
		try:
			return self.driver.lastTouch
		except Exception:
			log.debugWarning("BrlMultiline: could not read the touched pin", exc_info=True)
			return None

	def routingPin(self) -> Optional[tuple]:
		"""Where the finger was at the last routing press, in this display's pins.

		The pointing gesture. On a display like the Monarch you point at a place and press a
		routing button there, which is one hand doing one thing, and it is the only way the
		position can be read at all: the panel reports the touched pin as zero on release, and
		NVDA runs a gesture's script from a queue rather than during dispatch, so by the time
		anything here is asked, the live touch has gone. `touchedPin` is the live one and is only
		of use to something polling while the finger is still down.

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

		The whole cell slot is converted, gap column and blank row included. Those pins are
		unused by braille and are the claim's to draw in: reserving two lines and getting the
		dots of two lines but not the row between them would leave a stripe through every
		drawing.

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

	def cellForPin(self, x: int, y: int) -> Optional[tuple]:
		"""Convert a pin on this display back to a cell of NVDA's display.

		The inverse of `pinRectForCells` at a point, and the reason `rowStart` is kept: a
		touch is reported in the member's pins and everything above works in NVDA's rows.

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


def _surfaceFor(driver, rowStart: int) -> Optional[GraphicsSurface]:
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


def findSurface(display=None) -> Optional[GraphicsSurface]:
	"""Find the display that can draw, whether or not it is behind a composite.

	:param display: the display to look at, or None for the one NVDA is driving.
	:return: the surface, or None where nothing attached can draw. Not an error: it is the
		ordinary case on ordinary hardware, and callers fall back to text.
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
			"BrlMultiline: could not read the composite's members to find one that draws", exc_info=True
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
