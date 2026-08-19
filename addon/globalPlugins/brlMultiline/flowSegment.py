# BrlMultiline: the segment a flow is shown in.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A segment whose cells come from a flow controller rather than from NVDA's buffer.

Everywhere else in this add-on, a segment is a stock `BrailleBuffer` that has been lied to
about the size of the display, so that NVDA's own code lays text out inside it. A flow
cannot work that way: a buffer concatenates its regions and cuts rows out of the result,
which is the arrangement that puts a paragraph on the same row as the heading before it.
So a flow band assembles its own rows and this segment simply presents them.

That leaves one thing to be careful about, and it is the point of this milestone. NVDA
moves the buffer's window on its own account — `saveWindow` and `restoreWindow` around
every pending update, `scrollTo` whenever the caret moves, `focus` on a new object — and
each of those would drag a flow off the rows the reader is holding. The window belongs to
the anchor, so those calls are refused here. Following the cursor is the controller's
`syncToCursor`, which moves by the smallest amount that brings the cursor into view, and
does nothing at all when it is already there.

The other half is `FlowCommandRegion`. NVDA reaches for `mainBuffer.regions[-1]` in nine
places — see [flow-last-region-audit.md](../../../docs/design/flow-last-region-audit.md) —
and a flow band has no regions in that list to find. Rather than let those calls reach
nothing, the band holds exactly one stand-in whose job is to route them to the controller,
which sends them to the active block. It is not a `TextInfoRegion`, deliberately: that is
what stops `_handlePendingUpdate` treating it as something to scroll to.
"""

from typing import TYPE_CHECKING, Any, Optional

from braille.regions.base import Region
from logHandler import log

from .segments import BrailleBufferSegment

if TYPE_CHECKING:
	from braille.brailleHandler import BrailleHandler

	from .container import DisplayContainer
	from .flowControl import FlowController
	from .panels import SegmentSpec


class FlowCommandRegion(Region):
	"""Where NVDA's reach for the last region lands in a flow band.

	Carries the object being read, so that `handleCaretMove` recognises it and marks it for
	update, which is how a flow hears that the cursor moved. Everything it is asked to do is
	passed to the controller and thence to the active block, rather than to whatever happens
	to be last.
	"""

	def __init__(self, segment: "FlowBufferSegment") -> None:
		"""
		:param segment: the band this stands in for.
		"""
		super().__init__()
		self.segment = segment
		self.obj: Any = None
		"""The object the flow is reading, as NVDA's own regions carry."""

		self.pendingCaretUpdate = False
		"""Set by `handleCaretMove`. Read and cleared by NVDA; acted on in `update`."""

	@property
	def controller(self) -> "Optional[FlowController]":
		return self.segment.controller

	def update(self) -> None:
		"""Answer a caret move, which is what NVDA queues this region for.

		The block the cursor is in is re-read, and the window then moves by the smallest
		amount that brings it into view — which is nothing at all when the cursor has landed
		on something already under the reader's fingers.
		"""
		control = self.controller
		if control is None:
			return
		self.pendingCaretUpdate = False
		try:
			control.followCursor()
		except Exception:
			log.debugWarning("A flow could not follow the cursor", exc_info=True)
		self.segment.refresh()

	def nextLine(self) -> None:
		"""NVDA's next line command: one block on, taking the cursor."""
		self._step(forward=True)

	def previousLine(self, start: bool = False) -> None:
		"""NVDA's previous line command: one block back, taking the cursor."""
		self._step(forward=False)

	def _step(self, forward: bool) -> None:
		control = self.controller
		if control is None:
			return
		try:
			control.stepBlock(forward)
		except Exception:
			log.debugWarning("A flow could not step a block", exc_info=True)
			return
		self.segment.refresh()

	def routeTo(self, braillePos: int) -> None:
		# Routing is answered by the segment, which knows where in the band the press
		# landed. Reaching this is NVDA routing through the region rather than the buffer.
		self.segment.routeTo(braillePos)

	def __repr__(self) -> str:
		return f"<FlowCommandRegion for {self.segment.key!r}>"


class FlowBufferSegment(BrailleBufferSegment):
	"""A segment showing a flow, drawn by its controller.

	Built by the container for any specification whose owner draws the focus content
	itself. Until a controller is attached it behaves as an ordinary segment, which is what
	makes an unattached band blank rather than broken.
	"""

	def __init__(
		self,
		handler: "BrailleHandler",
		container: "DisplayContainer | None",
		spec: "SegmentSpec",
	) -> None:
		super().__init__(handler, container, spec)
		self.controller: "Optional[FlowController]" = None
		self.commandRegion = FlowCommandRegion(self)
		self.regions.append(self.commandRegion)

	# Lifetime.

	def attach(self, controller: "FlowController", obj: Any = None) -> None:
		"""Show a flow in this band.

		:param controller: the flow to show.
		:param obj: the object being read, for NVDA's caret handling to recognise.
		"""
		self.controller = controller
		self.commandRegion.obj = obj if obj is not None else getattr(controller.source, "obj", None)
		self.refresh()

	def detach(self) -> None:
		"""Stop showing a flow, leaving the band blank."""
		self.controller = None
		self.commandRegion.obj = None
		self.brailleCells = []
		self.rawText = ""
		self.cursorPos = None

	def clear(self) -> None:
		# The command region is this segment's own furniture rather than content, so it
		# survives a clear; without it NVDA's reach for the last region finds nothing.
		super().clear()
		self.regions.append(self.commandRegion)

	@property
	def isFlowing(self) -> bool:
		""":return: whether a flow is attached and has something to show."""
		return self.controller is not None

	# What is shown.

	def update(self) -> None:
		if self.controller is None:
			return super().update()
		self.brailleCells = self.cells()
		self.rawText = ""
		self.cursorPos = self.controller.cursorCell()

	def cells(self) -> list:
		""":return: the band's cells, exactly as many as the rectangle holds."""
		if self.controller is None:
			return []
		try:
			cells = self.controller.cells()
		except Exception:
			log.debugWarning("A flow could not produce its cells", exc_info=True)
			return [0] * self.rect.displaySize
		size = self.rect.displaySize
		if len(cells) < size:
			return cells + [0] * (size - len(cells))
		return cells[:size]

	def _get_windowBrailleCells(self) -> list:
		if self.controller is None:
			return super()._get_windowBrailleCells()
		return self.cells()

	def _get_cursorWindowPos(self) -> Optional[int]:
		if self.controller is None:
			return super()._get_cursorWindowPos()
		return self.controller.cursorCell()

	def refresh(self) -> None:
		"""Redraw the band after the flow moved."""
		self.update()
		try:
			self.updateDisplay()
		except Exception:
			log.debugWarning("A flow could not refresh the display", exc_info=True)

	# Moving, and the moves that are refused.

	def scrollForward(self) -> None:
		if self.controller is None:
			return super().scrollForward()
		if self.controller.panForward():
			self.refresh()

	def scrollBack(self) -> None:
		if self.controller is None:
			return super().scrollBack()
		if self.controller.panBack():
			self.refresh()

	def routeTo(self, windowPos: int) -> None:
		if self.controller is None:
			return super().routeTo(windowPos)
		if self.controller.routeTo(windowPos):
			self.refresh()

	def focus(self, region) -> None:
		if self.controller is None:
			return super().focus(region)
		# NVDA puts a region at the start of the window on a new object. The window here
		# belongs to the anchor, and where it should sit after a focus change is
		# `enterAtCursor`, which the segment is told about separately.

	def scrollTo(self, region, pos) -> None:
		if self.controller is None:
			return super().scrollTo(region, pos)
		# `scrollToCursorOrSelection` calls this after every caret move. Following the
		# cursor is `syncToCursor`, which moves by the smallest amount that brings it into
		# view and does nothing when it is already there; NVDA's version would put it at
		# the edge of the window every time.

	def saveWindow(self) -> None:
		if self.controller is None:
			return super().saveWindow()
		# `_handlePendingUpdate` saves and restores the window around every update. The
		# anchor already holds the position, through re-rendering and a changed row count
		# alike, so there is nothing here to save and nothing to put back.

	def restoreWindow(self) -> None:
		if self.controller is None:
			return super().restoreWindow()

	# Content from NVDA.

	def acceptFocusRegions(self, regions) -> bool:
		"""Take a focus change instead of having NVDA's regions written into the band.

		This is what `SegmentSpec.exclusive` means in practice: two producers would
		otherwise write to one segment, and `_doNewObjectMultiSegment` clears and rewrites
		the focus segment on every focus change, so the flow would be wiped and replaced by
		one object's regions.

		:param regions: the regions NVDA built for the new focus.
		:return: whether they were dealt with, so the caller does not place them.
		"""
		if self.controller is None:
			return False
		try:
			self.controller.enterAtCursor()
		except Exception:
			log.debugWarning("A flow could not enter at the new focus", exc_info=True)
		self.refresh()
		return True

	def __repr__(self) -> str:
		state = "flowing" if self.isFlowing else "empty"
		return f"<FlowBufferSegment {self.key!r} rect={self.rect} {state}>"
