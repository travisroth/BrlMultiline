# BrlMultiline: flash messages in a segment.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""NVDA's flash messages, confined to one segment instead of the whole display.

A flash message — the braille half of "5:57 PM", "selected", a settings change — does not
go through the main buffer at all. `BrailleHandler.message` swaps the handler's buffer for
`handler.messageBuffer`, a plain `BrailleBuffer`, and swaps it back when the message times
out or a routing key dismisses it. So while a message is showing, the display container this
add-on installs is bypassed completely, and NVDA's own flat buffer drives every cell.

On a single display nobody notices: the message covers the display, which is what a message
did before. Three things go wrong as soon as the display is divided, and one of them loses
text.

**Messages land at the top left.** A flat buffer starts at cell 0, and on a composite of two
physical displays cell 0 is the top left of the *upper* display. So messages appear on the
secondary display rather than beside the focus. The focus segment does not catch them,
because the focus segment is a property of the main buffer and a message is never in it.

**Long messages are silently truncated.** This is the real defect.
`BrailleBuffer._get_windowBrailleCells` pads every row to `handler.displayDimensions.numCols`.
On a Monarch above a Focus 80 that is 80, while the Monarch's rows only have 32 cells that
reach hardware — so up to 48 characters of every row are written into cells nothing displays,
and the rest of the message reads as though it were never there. It is the dead column trap
of `views.deviceView`, reappearing through the one path that view cannot cover.

**Everything else on the display goes blank.** A flat buffer holds the message and nothing
else, so every cell outside it is written as blank — including, on a composite, a pinned object
on the display the message is not even on. It also means every display is written to for every
message, since every display's cells have changed, which is felt as the displays slowing down.
Found on hardware, and the fix is L{MessageBuffer._cellsAroundTheMessage}.

L{MessageBuffer} answers all three by doing to the message buffer what `segments` does to
the main one: it is given a `RectHandlerProxy`, so NVDA's own wrapping lays the message out
at the segment's width rather than the display's, and its cells are then composited into that
segment's rectangle. Nothing can reach a dead column because nothing is laid out wider than
the hardware it is going to, and *which* segment is a setting.

The object is deliberately reused rather than rebuilt when the layout changes. The handler
compares `self.buffer is self.messageBuffer` in six places to decide whether a message is
showing, whether to reset its timer and whether to dismiss it; replacing the object those
comparisons point at while a message was up would leave a message that could never be
dismissed. Retargeting keeps the identity and moves the rectangle.
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
			# And written out, which nothing else will do. A message moves because the layout
			# was rebuilt, and the rebuild redraws through `handleGainFocus`, which deliberately
			# leaves the display alone while a message is showing. Without this the message
			# stays under the reader's fingers where it was while every calculation about it —
			# routing above all — has moved to where it now is. `updateDisplay` checks that this
			# buffer is the one being shown, so it costs nothing when the message has gone.
			self.updateDisplay()

	@property
	def _displayNumCols(self) -> int:
		""":return: the width of the whole display, which the proxy hides from everything else."""
		return self._realHandler.displayDimensions.numCols

	def _cellsAroundTheMessage(self, dimensions) -> list[int]:
		"""What the rest of the display is showing, for the message to be placed onto.

		Blanking it would be NVDA's behaviour, and on one display it is the right one: a
		message covers the display it is on. On several it is wrong twice over. Everything
		outside the message's segment goes blank for as long as the message lasts — including
		a pinned object on the other display, which is precisely what the reader put there to
		keep — and every display is written to, because every display's cells changed. Reading
		the time should not clear the display beside it.

		Taking the main buffer's cells instead leaves the other segments as they were, and the
		display they are on then receives nothing at all: `DeviceSlot.write` compares against
		what it last sent and skips a member whose cells have not changed.

		:param dimensions: the whole display's dimensions.
		:return: a display sized array. Blank if the main buffer cannot be read, which is what
			this class did for every message before.
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

		The handler pads whatever it is given out to the display size, so returning the
		segment's cells alone would put the message at the top left and leave NVDA to pad
		around it — which is the behaviour this class exists to stop.
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

		NVDA dismisses the message immediately after this returns, and `TextRegion.routeTo`
		does nothing, so today this is a translation with no observable effect. It is written
		correctly anyway: the day a message holds something routable, an untranslated position
		would act on a cell the user did not press, which is the failure this add-on has
		already met once in cell index rebasing.

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
