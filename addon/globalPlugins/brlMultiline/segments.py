# BrlMultiline: braille buffer segments.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A braille buffer that occupies part of a display.

The approach here is to avoid reimplementing any of NVDA's buffer logic. Rather than
teaching a buffer that it is smaller than the display, each segment is given a stand-in
handler that reports the segment's own rectangle as the display dimensions. Every
geometry dependent method in `BrailleBuffer` reads its dimensions from `self.handler` and
from nowhere else, so stock NVDA code then behaves correctly inside a segment, including
per row word wrapping and continuation marks.
"""

from typing import TYPE_CHECKING, Any

from braille.buffers import BrailleBuffer, _WindowRowPositions
from braille.display import DisplayDimensions
from braille.regions.base import Region

from .layout import SegmentRect, calculateFilledRowOffsets

if TYPE_CHECKING:
	from braille.brailleHandler import BrailleHandler

	from .container import BrailleBufferContainer


class _SegmentHandlerProxy:
	"""Stands in for the braille handler, reporting a segment's rectangle as the display.

	Every attribute other than the display dimensions is forwarded to the real handler.
	"""

	def __init__(
		self,
		handler: "BrailleHandler",
		container: "BrailleBufferContainer",
		rect: SegmentRect,
	) -> None:
		"""
		:param handler: the real braille handler.
		:param container: the container that owns the segment using this proxy.
		:param rect: the rectangle the segment occupies.
		"""
		self._handler = handler
		self._container = container
		self._dimensions = DisplayDimensions(numRows=rect.numRows, numCols=rect.numCols)
		self.segment: "BrailleBufferSegment | None" = None
		"""The segment using this proxy. Assigned by the segment on construction."""

	@property
	def displayDimensions(self) -> DisplayDimensions:
		return self._dimensions

	@property
	def displaySize(self) -> int:
		return self._dimensions.displaySize

	@property
	def buffer(self) -> Any:
		"""The buffer the handler is currently displaying, as the segment should see it.

		`BrailleBuffer.updateDisplay` guards its work with `self is self.handler.buffer`.
		A segment is never the handler's buffer, because the container is. Reporting the
		segment while the container is active lets that guard pass, so a segment can
		refresh the display in the normal way.
		"""
		realBuffer = self._handler.buffer
		if realBuffer is self._container:
			return self.segment
		return realBuffer

	def __getattr__(self, name: str) -> Any:
		# Only called when normal attribute lookup fails, so the properties above win.
		try:
			handler = object.__getattribute__(self, "_handler")
		except AttributeError:
			raise AttributeError(name) from None
		return getattr(handler, name)


class BrailleBufferSegment(BrailleBuffer):
	"""A braille buffer occupying a rectangle of the display.

	Behaves as an ordinary `BrailleBuffer` that happens to think the display is the size
	of its own rectangle.
	"""

	def __init__(
		self,
		handler: "BrailleHandler",
		container: "BrailleBufferContainer",
		rect: SegmentRect,
		fillRows: bool = False,
		markCuts: bool = False,
	) -> None:
		"""
		:param handler: the real braille handler.
		:param container: the container that owns this segment.
		:param rect: the rectangle this segment occupies.
		:param fillRows: fill each row completely rather than wrapping at word
			boundaries. See L{_calculateWindowRowBufferOffsets}.
		:param markCuts: when filling rows, mark a row that was cut mid word.
		"""
		proxy = _SegmentHandlerProxy(handler, container, rect)
		super().__init__(proxy)
		proxy.segment = self
		self.rect = rect
		self.fillRows = fillRows
		self.markCuts = markCuts
		self.isFocusBuffer = False
		"""Whether this segment is the one tracking the system focus."""
		self.hasSavedWindow = False
		"""Whether L{saveWindow} has succeeded since this segment was last cleared.

		An empty segment cannot save a window position, which is a normal state rather
		than an error, so the container tracks it instead of relying on exceptions.
		"""

	def clear(self) -> None:
		super().clear()
		self.hasSavedWindow = False

	def _calculateWindowRowBufferOffsets(self, pos: int) -> None:
		"""Work out where each row of this segment's window starts and ends.

		NVDA's own version reads the text wrap setting from the global configuration, not
		through the handler, so a segment cannot be given a different wrapping rule by
		the handler proxy. When `fillRows` is set, this replaces the calculation with the
		one narrow segments want: fill every row completely and carry straight on, even
		mid word. Otherwise NVDA's version is used unchanged, so the user's own wrap
		setting applies as it always has.
		"""
		if not self.fillRows:
			return super()._calculateWindowRowBufferOffsets(pos)
		bufferEnd = len(self.brailleCells)
		cutTest = (lambda end: self._isMidWordCut(end, bufferEnd)) if self.markCuts else None
		self._windowRowBufferOffsets = [
			_WindowRowPositions(start, end, showContinuationMark)
			for start, end, showContinuationMark in calculateFilledRowOffsets(
				bufferEnd,
				pos,
				self.rect.numRows,
				self.rect.numCols,
				cutTest,
			)
		]

	def _set_windowEndPos(self, endPos: int) -> None:
		"""Place the window so that it ends at a position, when scrolling back.

		NVDA's version consults the text wrap setting and, when wrapping at word
		boundaries, snaps the start of the window to a word. With `fillRows` there are no
		word boundaries to snap to, so the window is simply placed a whole segment back.

		This also drops NVDA's restriction that scrolling back should not pass the start
		of a region marked `focusToHardLeft`. That restriction is about keeping focus
		context off the display, which is not a concern for a segment showing one
		deliberately chosen thing.
		"""
		if not self.fillRows:
			return super()._set_windowEndPos(endPos)
		self.windowStartPos = max(0, endPos - self.handler.displaySize)

	def append(self, region: Region) -> None:
		"""Add a region to this segment.

		`BrailleBuffer` has no such method; NVDA appends to the `regions` list directly.
		"""
		self.regions.append(region)

	def __repr__(self) -> str:
		return (
			f"<BrailleBufferSegment rect={self.rect} "
			f"isFocusBuffer={self.isFocusBuffer} regions={len(self.regions)}>"
		)
