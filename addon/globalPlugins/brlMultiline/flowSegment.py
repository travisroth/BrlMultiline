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

The other half is what the band puts in its `regions` list. NVDA reaches for
`mainBuffer.regions[-1]` in nine places — see
[flow-last-region-audit.md](../../../docs/design/flow-last-region-audit.md) — and on a
single line display that is the block the cursor is in. So that is what a band puts there:
the active block's own region, and nothing else. A stand-in of our own was tried first and
was wrong, because braille input only writes to the last region when it is a
`TextInfoRegion`, so untranslated dots went nowhere and the erase that checks them could
flush the input buffer. The real region is a `TextInfoRegion`, so those paths work
unaltered, and the line commands move the browse mode cursor because it is a live region.

What has to be added is the other direction: those commands move the region without telling
anyone, so a live region calls back when its position changes, and the band follows it. The
one call that must not reach the band is the window moving, and that is refused above.
"""

from typing import TYPE_CHECKING, Optional

from logHandler import log

from . import bmConfig, flowQuickNav
from .segments import BrailleBufferSegment

if TYPE_CHECKING:
	from braille.brailleHandler import BrailleHandler

	from .container import DisplayContainer
	from .flowControl import FlowController
	from .panels import SegmentSpec


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
		self.onFocusRegions = None
		"""Called with the regions NVDA built for a new focus, returning whether it took them.

		Set by whoever owns the band. The segment cannot answer a focus change itself
		because the focus may have gone to a different document, and choosing what to read
		is the owner's business, not the display's."""

		self.onUpdate = None
		"""Called before each redraw, so the owner can check it is still reading the right
		thing.

		Browse mode can change what should be read without any focus change at all: Escape
		leaves a form field with the focus still on it, and entering one is the same toggle
		in reverse. A band that only ever asked on a focus event went on showing the field
		the reader had left."""

		self.onSettle = None
		"""Called after an update that re-read an edit being typed into, so the owner can
		come back for a second look.

		A rich editor's answers at the instant of a keystroke can be transiently wrong, and
		every wrong state heals on the next re-read — which used to arrive only with the
		next keystroke. A reader who pauses right after pressing return is reading the
		display, and what is under their fingers at that moment must not wait on them."""

	# Lifetime.

	def attach(self, controller: "FlowController") -> None:
		"""Show a flow in this band.

		This used to take the object being read as well, documented as being for NVDA's caret
		handling to recognise, and did nothing whatever with it. The parameter is gone rather
		than wired up, because the region is where NVDA looks — `handleCaretMove` reads `obj`
		off the last region in the buffer — so the region is where it has to be set, and a
		parameter here that claimed to do it is worse than none at all. It read as the place
		that question was already answered.

		:param controller: the flow to show.
		"""
		self.controller = controller
		controller.onChanged = self.refresh
		self.refresh()

	def follow(self, onFocusRegions) -> None:
		"""Say who decides what this band shows when the focus changes.

		:param onFocusRegions: called with NVDA's focus regions, returning whether it took
			them. None leaves the band answering focus changes itself.
		"""
		self.onFocusRegions = onFocusRegions

	def detach(self) -> None:
		"""Stop showing a flow, leaving the band blank."""
		if self.controller is not None:
			self.controller.onChanged = None
		self.controller = None
		self.regions = []
		self.brailleCells = []
		self.rawText = ""
		self.cursorPos = None

	def clear(self) -> None:
		super().clear()
		# The band's regions are not content that can be cleared away: the active block is
		# what NVDA's own commands act on, so it is put back at once.
		self._syncRegions()

	@property
	def isFlowing(self) -> bool:
		""":return: whether a flow is attached and has something to show."""
		return self.controller is not None

	# What is shown.

	def update(self) -> None:
		if self.controller is None:
			return super().update()
		# The renderer compresses a block while laying it out and cannot ask where the band is;
		# this is where that is known. Read afresh each pass because the band can move between
		# displays — the settings offer that — and because a member of a composite can go away.
		# Through the controller, so that what was laid out before the band knew is laid out
		# again rather than left with its words. See `FlowController.drawGlyphsOn`.
		self.controller.drawGlyphsOn(self.glyphTarget())
		self._checkWhatIsRead()
		if self.controller is None:
			# The owner found it was reading the wrong thing and gave the band back.
			return super().update()
		self._syncRegions()
		self._followIfRead()
		# Again, because following may have made a different block active. NVDA queues the
		# region it finds *here* — `handleCaretMove` takes `mainBuffer.regions[-1]` — so a
		# list left holding the block the reader has just left means the next caret move
		# queues a region the band no longer shows, and the update after it finds nothing
		# dirty and does nothing. The display then answered one keypress in two, which under
		# the fingers is a cursor that moves a press late and a letter that appears only once
		# the next one has been typed.
		self._syncRegions()
		self.brailleCells = self.cells()
		self.rawText = ""
		self.cursorPos = self.controller.cursorCell()
		if self.controller.rereadWhileWriting:
			self.controller.rereadWhileWriting = False
			if self.onSettle is not None:
				try:
					self.onSettle()
				except Exception:
					log.debugWarning("A flow could not arrange its settle pass", exc_info=True)

	def _checkWhatIsRead(self) -> None:
		"""Let the owner confirm the flow is still over the right document."""
		if self.onUpdate is None:
			return
		try:
			self.onUpdate()
		except Exception:
			log.debugWarning("A flow could not check what it is reading", exc_info=True)

	def syncRegions(self) -> None:
		"""Point NVDA at the region this band is actually drawing, without redrawing anything.

		Public because reconciling the two is not the same question as writing cells to
		hardware, and every path that rebuilds the flow's blocks has to answer it. A pass
		that re-read the band and found the cells unchanged used to return without either:
		the reader saw the right thing, NVDA was left holding the region the re-read
		retired, and the *next* keystroke went to a region this band no longer draws. See
		`FlowBand._settle`.
		"""
		self._syncRegions()

	def _syncRegions(self) -> None:
		"""Put the active block's region where NVDA looks for the last one, and nothing else.

		Cheap, and done twice per update on purpose: before reading it, because that is what
		NVDA's commands act on, and after following, because that is what NVDA will queue
		for the next event.

		**A pending read is carried across the swap.** NVDA queues a region and comes back
		to it a core cycle later — `handleCaretMove` takes `mainBuffer.regions[-1]`, and
		`_handlePendingUpdate` re-reads it before updating the buffer. Anything that rebuilds
		the band in between leaves that reading on a region nobody draws, and the replacement
		is clean, so `_followIfRead` finds nothing to act on and the keystroke is lost. The
		re-read was real news whichever object it landed on, so it moves with the block.
		"""
		if self.controller is None:
			return
		region = self.controller.activeRegion()
		if self.regions[-1:] == [region]:
			return
		retired = self.regions[-1] if self.regions else None
		if (
			region is not None
			and retired is not None
			and getattr(retired, "dirty", False)
			and not getattr(region, "dirty", False)
		):
			region.dirty = True
			retired.dirty = False
		self.regions = [region] if region is not None else []

	def _followIfRead(self) -> None:
		"""Follow the cursor when NVDA has re-read the block it is in, or a jump is waiting.

		`_handlePendingUpdate` re-reads the queued region and then updates the buffer, which
		is this. Re-reading is how a flow hears that the caret moved within the document, or
		that braille input changed what the block says.

		A quick navigation key is answered whether or not the region was re-read. It used to
		be answered only inside that condition, which made it depend on NVDA having queued
		our region — and a key whose caret move never reached us left the display sitting
		where it was while speech announced what it had moved to. That was true of every
		quick navigation key, not only the grounding ones: `h` appeared to work because
		grounding was the one path that did not need the region to be dirty, and `e` and `b`
		did nothing at all.
		"""
		region = self.controller.activeRegion()
		# Taken whether or not it is acted on, so that a jump the reader made before the
		# setting was turned off cannot ground a later move.
		jump = flowQuickNav.take()
		ground = bool(jump) and bmConfig.shouldGroundOnQuickNav()
		if jump is None and (region is None or not getattr(region, "dirty", False)):
			return
		try:
			self.controller.followCursor(ground=ground)
		except Exception:
			log.debugWarning("A flow could not follow the cursor", exc_info=True)
		finally:
			if region is not None:
				region.dirty = False

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

	def cellGlyphs(self) -> dict:
		""":return: `{position in this segment: the fitted glyph}`, empty where there are none.

		The band fills its rectangle and is written row major at the rectangle's width, so a
		position on the band is a position in the segment and no arithmetic is needed here. The
		container is what turns it into a place on the display.
		"""
		if self.controller is None:
			# An unattached band is an ordinary segment and answers as one, which is what
			# makes a band blank rather than broken while it waits for a flow.
			return super().cellGlyphs()
		try:
			return self.controller.cellGlyphs()
		except Exception:
			log.debugWarning("A flow could not say where its glyphs are", exc_info=True)
			return {}

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

		The owner decides what to do with it, because the focus may have gone to a different
		document — or to something with no text at all, in which case the band is given back
		and these regions are placed in it as they would be in any other segment.

		:param regions: the regions NVDA built for the new focus.
		:return: whether they were dealt with, so the caller does not place them.
		"""
		if self.onFocusRegions is not None:
			try:
				return bool(self.onFocusRegions(regions))
			except Exception:
				log.debugWarning("A flow could not answer a focus change", exc_info=True)
				return False
		if self.controller is None or not self.spec.hostsSystemFocus:
			# A flow that is not where the focus belongs must not take it. Every segment is a
			# flow segment now, so every segment is offered the focus, and a pinned object's
			# flow answering yes would both swallow NVDA's regions and jump the pin to
			# wherever the reader happens to be — which is the one place a pin exists not to
			# follow.
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
