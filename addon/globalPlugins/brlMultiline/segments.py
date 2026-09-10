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
from logHandler import log
from braille.display import DisplayDimensions
from braille.regions.base import Region

from . import glyphFlow
from .layout import SegmentRect, calculateFilledRowOffsets
from .panels import SegmentSpec
from .routing import RoutingPolicy

if TYPE_CHECKING:
	from braille.brailleHandler import BrailleHandler

	from .container import DisplayContainer


class RectHandlerProxy:
	"""Stands in for the braille handler, reporting one rectangle as the whole display.

	This is the trick the module docstring describes, on its own: every geometry dependent
	method in `BrailleBuffer` reads its dimensions from `self.handler`, so a buffer given one
	of these wraps its text, places its continuation marks and moves its window inside the
	rectangle without knowing there is anything else on the display.

	Every attribute other than the display dimensions is forwarded to the real handler.
	"""

	def __init__(self, handler: "BrailleHandler", rect: SegmentRect) -> None:
		"""
		:param handler: the real braille handler.
		:param rect: the rectangle to report as the display.
		"""
		self._handler = handler
		self.setRect(rect)

	def setRect(self, rect: SegmentRect) -> None:
		"""Report a different rectangle from now on.

		:param rect: the new rectangle.
		"""
		self._dimensions = DisplayDimensions(numRows=rect.numRows, numCols=rect.numCols)

	@property
	def displayDimensions(self) -> DisplayDimensions:
		return self._dimensions

	@property
	def displaySize(self) -> int:
		return self._dimensions.displaySize

	def __getattr__(self, name: str) -> Any:
		# Only called when normal attribute lookup fails, so the properties above win.
		try:
			handler = object.__getattribute__(self, "_handler")
		except AttributeError:
			raise AttributeError(name) from None
		return getattr(handler, name)


class _SegmentHandlerProxy(RectHandlerProxy):
	"""A rectangle proxy for a segment of the container."""

	def __init__(
		self,
		handler: "BrailleHandler",
		container: "DisplayContainer | None",
		rect: SegmentRect,
	) -> None:
		"""
		:param handler: the real braille handler.
		:param container: the container that owns the segment using this proxy, or None for
			a segment that is not on the display at all. `flowRender` builds one of those to
			lay a block out in, so that the cutting of text into rows stays NVDA's.
		:param rect: the rectangle the segment occupies.
		"""
		super().__init__(handler, rect)
		self._container = container
		self.segment: "BrailleBufferSegment | None" = None
		"""The segment using this proxy. Assigned by the segment on construction."""

	@property
	def buffer(self) -> Any:
		"""The buffer the handler is currently displaying, as the segment should see it.

		`BrailleBuffer.updateDisplay` guards its work with `self is self.handler.buffer`.
		A segment is never the handler's buffer, because the container is. Reporting the
		segment while the container is active lets that guard pass, so a segment can
		refresh the display in the normal way.

		A message buffer needs no such thing: it really is `handler.buffer` while it is
		showing, so the forwarded attribute is already the right answer.
		"""
		realBuffer = self._handler.buffer
		if realBuffer is self._container:
			return self.segment
		return realBuffer


class BrailleBufferSegment(BrailleBuffer):
	"""A braille buffer occupying a rectangle of the display.

	Behaves as an ordinary `BrailleBuffer` that happens to think the display is the size
	of its own rectangle.
	"""

	def __init__(
		self,
		handler: "BrailleHandler",
		container: "DisplayContainer | None",
		spec: SegmentSpec,
	) -> None:
		"""
		:param handler: the real braille handler.
		:param container: the container that owns this segment, or None for a segment built
			only to lay text out. See `RectHandlerProxy`.
		:param spec: everything about this segment that does not change once it is built:
			its rectangle, its key, its owner, and the policies it behaves by.
		"""
		proxy = _SegmentHandlerProxy(handler, container, spec.rect)
		super().__init__(proxy)
		proxy.segment = self
		self.spec = spec
		self.rect = spec.rect
		self.isOnDisplay = container is not None
		"""Whether this segment is a place on the display rather than a place to lay text out.

		The renderer builds buffers of this class with no container, to cut a block into rows
		in — see `flowRender._layoutBuffer`. Those must not decide anything for themselves: the
		caller has already decided whether a shape may be drawn in this block, and a cell of a
		table row is one where it may not.
		"""
		self.fillRows = spec.fillRows
		self.markCuts = spec.markCuts
		self.isFocusBuffer = False
		"""Whether this segment is the one tracking the system focus."""
		self.hasSavedWindow = False
		"""Whether L{saveWindow} has succeeded since this segment was last cleared.

		An empty segment cannot save a window position, which is a normal state rather
		than an error, so the container tracks it instead of relying on exceptions.
		"""

	@property
	def key(self) -> str:
		""":return: this segment's stable identity, unchanged across a rebuild."""
		return self.spec.key

	@property
	def owner(self) -> str | None:
		""":return: the panel that reserved this segment, or None if it is free."""
		return self.spec.owner

	@property
	def isReserved(self) -> bool:
		""":return: whether this segment belongs to a panel that claimed it.

		A free segment is one the add-on may fill with the document lines around the caret.
		"""
		return self.spec.isReserved

	@property
	def routingPolicy(self) -> RoutingPolicy:
		""":return: what a cursor routing key press within this segment does."""
		return self.spec.routingPolicy

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

	def update(self) -> None:
		"""Read the regions, drawing what can be drawn as a shape rather than written.

		**Before the buffer concatenates them**, because the cells a shape gives back have to be
		cells the row cutting can use. This is the one place every ordinary segment passes
		through, whatever is in it — an object, a document, a menu bar — which is why it is the
		place the substitution is made rather than somewhere that knows what kind of content it
		is looking at. It costs one attribute read per region when the reader has not asked for
		shapes; see `glyphFlow.compressRegion`.

		A segment built only to lay text out is left alone. Its caller has already decided.
		"""
		target = self.glyphTarget()
		if self.isOnDisplay:
			for region in self.visibleRegions:
				glyphFlow.compressRegion(region, target)
		super().update()

	def glyphTarget(self):
		""":return: the display this segment is drawn on, if one piece of hardware can draw a
		shape in a cell on every row of it.

		Asked of the rows rather than of the arrangement, because two displays driven as one are
		two pieces of hardware. A segment on the Focus half of a Focus and Monarch must keep its
		words even though the Monarch is plugged in, and a segment straddling the join between
		them can only have its shapes registered against one of the two, so it keeps its words
		as well.
		"""
		if not self.isOnDisplay:
			return None
		try:
			return glyphFlow.targetForRows(self.rect.row, self.rect.numRows)
		except Exception:
			log.debugWarning("Could not ask which display a segment is on", exc_info=True)
			return None

	def cellGlyphs(self) -> dict:
		""":return: `{position in this segment's window: the fitted glyph}`.

		The regions carry their symbols against their own cells, which is where they were put;
		this turns those into places on the segment, and the container turns those into places
		on the display. A symbol that has been scrolled out of the window is simply not in the
		answer, which is what `bufferPosToWindowPos` refusing means.
		"""
		found: dict = {}
		start = 0
		for region in self.visibleRegions:
			cells = getattr(region, "brailleCells", None) or ()
			for at, fitted in glyphFlow.marksOf(region).items():
				try:
					found[self.bufferPosToWindowPos(start + at)] = fitted
				except LookupError:
					continue
			start += len(cells)
		return found

	def append(self, region: Region) -> None:
		"""Add a region to this segment.

		`BrailleBuffer` has no such method; NVDA appends to the `regions` list directly.
		"""
		self.regions.append(region)

	def __repr__(self) -> str:
		return (
			f"<BrailleBufferSegment {self.key!r} rect={self.rect} owner={self.owner!r} "
			f"isFocusBuffer={self.isFocusBuffer} regions={len(self.regions)}>"
		)
