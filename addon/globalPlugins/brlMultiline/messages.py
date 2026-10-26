# BrlMultiline: flash messages in a segment.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""NVDA's flash messages, confined to one segment instead of the whole display.

`BrailleHandler.message` swaps in `handler.messageBuffer`, bypassing the display container. On
a divided display NVDA's flat buffer would put the message at the top left, lose text into dead
columns, and blank every other segment.

`MessageBuffer` is given a `RectHandlerProxy`, as `segments` does, so the message is laid out at
its segment's width and placed in its rectangle, over the main buffer's cells (see
`_cellsAroundTheMessage`). The object is retargeted rather than rebuilt, since the handler
compares `self.buffer is self.messageBuffer` to time out and dismiss a message.
"""

from typing import TYPE_CHECKING, Any

from braille.buffers import BrailleBuffer
from logHandler import log

from .layout import SegmentRect, findSegmentAtWindowPos, segmentPosToWindowPos
from .segments import RectHandlerProxy

if TYPE_CHECKING:
	from braille.brailleHandler import BrailleHandler


class MessageBuffer(BrailleBuffer):
	"""The buffer NVDA flashes messages in, occupying one segment of the display.

	An ordinary `BrailleBuffer` that believes the display is the size of one segment, whose
	cells are then placed at that segment's position. Everything NVDA does to a message —
	wrapping it, panning it, timing it out, dismissing it on a routing key — is untouched.
	"""

	drawsMainBufferAround = True
	"""Whether the rest of the display is drawn from the main buffer while a message is up.

	Read by `segments.isShowing`, so pins and app layouts go on updating around a message.
	"""

	def __init__(self, handler: "BrailleHandler", rect: SegmentRect) -> None:
		"""
		:param handler: the real braille handler.
		:param rect: the rectangle messages should appear in.
		"""
		self._realHandler = handler
		"""The real handler, for the size of the whole display. `self.handler` is the proxy,
		which reports the segment, and everything inherited must go on believing it."""
		super().__init__(RectHandlerProxy(handler, rect))
		self.rect = rect

	def setRect(self, rect: SegmentRect) -> None:
		"""Show messages somewhere else from now on.

		:param rect: the new rectangle.
		"""
		if rect == self.rect:
			return
		self.rect = rect
		self.handler.setRect(rect)
		if self.regions:
			# A message is showing and has just been told it is a different shape. Laying it
			# out again is cheap and the alternative is a message half wrapped to a width it
			# no longer has.
			self.update()
			# Written out here, since the rebuild's redraw leaves the display alone while a
			# message shows. `updateDisplay` does nothing once the message has gone.
			self.updateDisplay()

	@property
	def _displayNumCols(self) -> int:
		""":return: the width of the whole display, which the proxy hides from everything else."""
		return self._realHandler.displayDimensions.numCols

	def _cellsAroundTheMessage(self, dimensions) -> list[int]:
		"""What the rest of the display is showing, for the message to be placed onto.

		The main buffer's cells, not blanks, so other segments stay and their displays, unchanged,
		are not written to (see `DeviceSlot.write`).

		:param dimensions: the whole display's dimensions.
		:return: a display sized array. Blank if the main buffer cannot be read, which is what
			NVDA does.
		"""
		size = dimensions.displaySize
		try:
			cells = list(self._realHandler.mainBuffer.windowBrailleCells)
		except Exception:
			log.debugWarning("BrlMultiline: could not read what is behind a message", exc_info=True)
			return [0] * size
		if len(cells) != size:
			cells = cells[:size]
			cells.extend([0] * (size - len(cells)))
		return cells

	windowBrailleCells: Any
	"""The message in its segment, with the rest of the display left as it was."""

	def _get_windowBrailleCells(self) -> list[int]:
		"""Composite this segment's cells into a display sized array.

		The segment's cells alone would be padded by the handler at the top left.
		"""
		dimensions = self._realHandler.displayDimensions
		cells = self._cellsAroundTheMessage(dimensions)
		rect = self.rect
		# Cleared before it is written, rather than relying on the message to cover its own
		# segment: a message shorter than the segment would otherwise leave the rows below it
		# showing whatever the main buffer had there.
		for row in range(rect.numRows):
			start = (rect.row + row) * dimensions.numCols + rect.col
			cells[start : start + rect.numCols] = [0] * rect.numCols
		for position, cell in enumerate(super()._get_windowBrailleCells()):
			if position >= rect.displaySize:
				# The inherited getter pads each row to the proxy's width, so this cannot
				# happen; never let a miscount write outside the segment if it does.
				log.debugWarning(f"BrlMultiline: message produced more cells than {rect}")
				break
			row, col = divmod(position, rect.numCols)
			cells[(rect.row + row) * dimensions.numCols + rect.col + col] = cell
		return cells

	cursorWindowPos: Any
	"""Where the cursor is on the whole display, if a message has one at all."""

	def _get_cursorWindowPos(self) -> int | None:
		position = super()._get_cursorWindowPos()
		if position is None:
			return None
		try:
			return segmentPosToWindowPos(self.rect, position, self._displayNumCols)
		except ValueError:
			log.debugWarning(f"BrlMultiline: message cursor at {position} is outside {self.rect}")
			return None

	def routeTo(self, windowPos: int) -> None:
		"""Route within the message, from a position on the whole display.

		No effect today, since NVDA dismisses the message and `TextRegion.routeTo` does nothing,
		but correct for a message that holds something routable.

		:param windowPos: the position pressed, on the whole display.
		"""
		try:
			_index, position = findSegmentAtWindowPos([self.rect], windowPos, self._displayNumCols)
		except LookupError:
			# A routing key on a part of the display the message is not on. NVDA will dismiss
			# the message either way, which is the whole of what the press achieves.
			return
		super().routeTo(position)

	def __repr__(self) -> str:
		return f"<MessageBuffer at {self.rect}>"
