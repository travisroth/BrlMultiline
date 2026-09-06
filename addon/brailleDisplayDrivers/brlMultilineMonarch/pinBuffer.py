# BrlMultiline: a device independent dot canvas.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A rectangular grid of dots, and the drawing you can do on one.

Deliberately knows nothing about any display. Width, height, and which dots are raised; how
those dots reach hardware is a device's business and lives beside this, in `monarch.py` for
the one device we have. A second display would supply its own equivalent of that file and
reuse this unchanged, which is the whole point of the split.

It imports nothing from NVDA, so it can be unit tested without one. That is not incidental:
the packing this feeds is the part that was got wrong twice against real hardware, and the
tests are how it stays right.

The interface mirrors NVDA's `tactile.TactileGraphicsBuffer` — `width`, `height`, `setDot` —
so that `tactile.braille.drawBrailleCells` can draw into it directly. It does not subclass
it, because that module is not importable outside NVDA and this one has to be.
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

		Written for glyph literals, where being able to see the shape in the source is worth
		more than compactness::

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

		There is nothing to antialias with one bit pins, so a shallow diagonal is a series of
		horizontal runs stepping across. That is not a defect and is what a slope looks like
		at this resolution.

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

		Additive because that is what compositing text and a graphic wants: whichever is
		drawn second must not punch a hole in the other. A caller wanting a graphic to
		replace what is under it clears that rectangle first.

		:param other: the buffer to draw.
		:param x: where its left edge goes.
		:param y: where its top edge goes.
		"""
		for row in range(other.height):
			for col in range(other.width):
				if other.getDot(col, row):
					self.setDot(x + col, y + row)

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

		A raised dot is ``O`` and a lowered one ``.``, because a drawing that is wrong is
		much easier to see than to reason about, and neither of the two packing errors this
		module exists to prevent was obvious from the numbers.

		:return: one string per row of the buffer.
		"""
		return [
			"".join("O" if self.getDot(x, y) else "." for x in range(self.width)) for y in range(self.height)
		]
