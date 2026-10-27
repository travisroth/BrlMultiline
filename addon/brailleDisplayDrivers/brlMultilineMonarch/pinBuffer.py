# BrlMultiline: a device independent dot canvas.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A rectangular grid of dots, and the drawing you can do on one.

Knows nothing about any display; how dots reach hardware is `monarch.py`'s business. Imports
nothing from NVDA. Mirrors `tactile.TactileGraphicsBuffer` (`width`, `height`, `setDot`) so
`tactile.braille.drawBrailleCells` can draw into it, without subclassing it.
"""

BRAILLE_DOT_COORDS = [
	(0, 0),  # dot 1
	(0, 1),  # dot 2
	(0, 2),  # dot 3
	(1, 0),  # dot 4
	(1, 1),  # dot 5
	(1, 2),  # dot 6
	(0, 3),  # dot 7
	(1, 3),  # dot 8
]
"""Where each dot sits inside a braille cell, matching NVDA's `tactile.braille._brailleDotCoords`.

Braille geometry, so here rather than in `monarch.py`, which re-exports it. The driver checks
it against NVDA's at load.
"""

MARKERS = {
	"dot": ["O"],
	"cross": [
		"O.O",
		".O.",
		"O.O",
	],
	"plus": [
		".O.",
		"OOO",
		".O.",
	],
	"square": [
		"OOO",
		"O.O",
		"OOO",
	],
	"diamond": [
		".O.",
		"O.O",
		".O.",
	],
}
"""The shapes `PinBuffer.marker` can stamp, as `fromRows` patterns.

Few, differing in outline, and odd sized so each has a centre dot.
"""


class PinBuffer:
	"""A grid of dots that can be drawn on.

	Coordinates are x across from 0 at the left, y down from 0 at the top. Everything clips:
	drawing outside the buffer is silently ignored rather than raising, because a caller
	scaling a chart to a panel should not have to bounds check every point.
	"""

	def __init__(self, width: int, height: int):
		"""
		:param width: dots across.
		:param height: dots down.
		"""
		self.width = width
		self.height = height
		self._dots = bytearray(width * height)

	@classmethod
	def fromRows(cls, rows: list[str]) -> "PinBuffer":
		"""Build a buffer from text, the inverse of `rows`.

		For glyph literals, so the shape is visible in the source::

			PinBuffer.fromRows(["OOO", "OOO", "OOO", "..."])

		Any character other than a space or a full stop raises a dot, so ``#`` or ``*`` read
		as well as ``O``. Short rows are padded, so a trailing blank row can be written as
		``""``.

		:param rows: one string per row. The width is the longest of them.
		:return: the buffer.
		"""
		height = len(rows)
		width = max((len(row) for row in rows), default=0)
		buffer = cls(width, height)
		for y, row in enumerate(rows):
			for x, character in enumerate(row):
				if character not in " .":
					buffer.setDot(x, y)
		return buffer

	def clear(self) -> None:
		"""Lower every dot."""
		self._dots = bytearray(self.width * self.height)

	def contains(self, x: int, y: int) -> bool:
		"""
		:param x: dot column.
		:param y: dot row.
		:return: whether the coordinate is on the buffer.
		"""
		return 0 <= x < self.width and 0 <= y < self.height

	def setDot(self, x: int, y: int) -> None:
		"""Raise one dot, ignoring anything off the buffer.

		Named to match `tactile.TactileGraphicsBuffer`, so NVDA's `drawBrailleCells` can be
		pointed straight at this.

		:param x: dot column.
		:param y: dot row.
		"""
		if self.contains(x, y):
			self._dots[y * self.width + x] = 1

	def clearDot(self, x: int, y: int) -> None:
		"""Lower one dot, ignoring anything off the buffer.

		:param x: dot column.
		:param y: dot row.
		"""
		if self.contains(x, y):
			self._dots[y * self.width + x] = 0

	def getDot(self, x: int, y: int) -> bool:
		"""
		:param x: dot column.
		:param y: dot row.
		:return: whether that dot is raised. False for anything off the buffer.
		"""
		return bool(self._dots[y * self.width + x]) if self.contains(x, y) else False

	def line(self, x0: int, y0: int, x1: int, y1: int) -> None:
		"""Draw a straight line, by Bresenham.

		:param x0: start column.
		:param y0: start row.
		:param x1: end column.
		:param y1: end row.
		"""
		dx = abs(x1 - x0)
		dy = -abs(y1 - y0)
		stepX = 1 if x0 < x1 else -1
		stepY = 1 if y0 < y1 else -1
		error = dx + dy
		while True:
			self.setDot(x0, y0)
			if x0 == x1 and y0 == y1:
				return
			doubled = 2 * error
			if doubled >= dy:
				error += dy
				x0 += stepX
			if doubled <= dx:
				error += dx
				y0 += stepY

	def rect(self, x: int, y: int, width: int, height: int, filled: bool = False) -> None:
		"""Draw a rectangle.

		:param x: left column.
		:param y: top row.
		:param width: dots across.
		:param height: dots down.
		:param filled: solid rather than an outline.
		"""
		if width <= 0 or height <= 0:
			return
		if filled:
			for row in range(y, y + height):
				for col in range(x, x + width):
					self.setDot(col, row)
			return
		self.line(x, y, x + width - 1, y)
		self.line(x, y + height - 1, x + width - 1, y + height - 1)
		self.line(x, y, x, y + height - 1)
		self.line(x + width - 1, y, x + width - 1, y + height - 1)

	def polyline(self, points: list[tuple[int, int]]) -> None:
		"""Draw connected line segments through a series of points.

		:param points: the points, in order. Fewer than two draws nothing.
		"""
		for start, end in zip(points, points[1:]):
			self.line(start[0], start[1], end[0], end[1])

	def blit(self, other: "PinBuffer", x: int = 0, y: int = 0) -> None:
		"""Draw another buffer onto this one, raising dots and never lowering them.

		Additive, so neither layer punches a hole in the other; clear the rectangle first to
		replace.

		:param other: the buffer to draw.
		:param x: where its left edge goes.
		:param y: where its top edge goes.
		"""
		for row in range(other.height):
			for col in range(other.width):
				if other.getDot(col, row):
					self.setDot(x + col, y + row)

	def marker(self, x: int, y: int, kind: str = "dot") -> None:
		"""Stamp a small named shape centred on a point.

		:param x: the point's column.
		:param y: the point's row.
		:param kind: a key of `MARKERS`. An unknown name draws a single dot, because losing
			a data point is worse than losing its shape.
		"""
		rows = MARKERS.get(kind)
		if rows is None:
			rows = MARKERS["dot"]
		shape = PinBuffer.fromRows(rows)
		self.blit(shape, x - shape.width // 2, y - shape.height // 2)

	def text(self, x: int, y: int, cells: list[int], cellStride: int = 3) -> None:
		"""Draw braille cells into this buffer as dots.

		At any position, unlike the driver's lines. Additive; call `clearRect` first for a
		cleared strip.

		:param x: left edge of the first cell.
		:param y: top edge of the line.
		:param cells: one eight dot cell value per cell, as NVDA writes them.
		:param cellStride: dot columns from one cell's left edge to the next. Three matches a
			display that leaves a gap column; two packs cells with no separation, which is
			legible for a short label and not for prose.
		"""
		for index, cell in enumerate(cells):
			originX = x + index * cellStride
			for bit in range(8):
				if cell & (1 << bit):
					dotX, dotY = BRAILLE_DOT_COORDS[bit]
					self.setDot(originX + dotX, y + dotY)

	def clearRect(self, x: int, y: int, width: int, height: int) -> None:
		"""Lower every dot in a rectangle.

		:param x: left column.
		:param y: top row.
		:param width: dots across.
		:param height: dots down.
		"""
		for row in range(y, y + height):
			for col in range(x, x + width):
				self.clearDot(col, row)

	def rows(self) -> list[str]:
		"""Render as text, one string per row, for tests and for the log.

		A raised dot is ``O`` and a lowered one ``.``.

		:return: one string per row of the buffer.
		"""
		return [
			"".join("O" if self.getDot(x, y) else "." for x in range(self.width)) for y in range(self.height)
		]
