# BrlMultiline: pinning an object to a segment.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Keeps an object visible in a segment while the focus moves elsewhere.

Regions are generated for the object in the ordinary way and marked for the segment they
belong to. The replacement for `BrailleHandler._doNewObject` clears only the segments
that are receiving new regions, so a pinned object survives focus changes without any
further intervention.

Three things separate a pin from the ordinary braille NVDA produces for the focus, and each
costs something here:

1. The object is read where it stands. A browse mode document is presented through its
   tree interceptor, exactly as `BrailleHandler.handleGainFocus` does it, because the
   `NVDAObject` under a browse mode cursor carries only the element the cursor is in and a
   pin on one paragraph cannot be read past its end.
2. The regions are built once and kept. Rebuilding them on every refresh would throw away
   the reading position the user panned to, so a refresh re-renders the regions it already
   has. See `pinnedRegions`.
3. Nothing tells the add-on that a pinned object has changed. It is in a window the user is
   not working in, so there is no focus or caret event to redraw from, and NVDA does not
   even deliver events for a background window unless something has asked for them. So
   `patches` re-reads pinned objects on a timer instead, and L{refresh} keeps that cheap by
   leaving the display alone when nothing has changed.

A monitor holds its segment's key rather than its number. Numbers are display order and are
reassigned whenever the view changes; a key is not, so a pin survives a rebuild as long as
the segment it names does. That is what lets a panel be laid over part of the display
without disturbing an object pinned outside the claim.
"""

from typing import TYPE_CHECKING, Optional

import braille
import controlTypes
from braille.regions.focus import getFocusRegions
from logHandler import log

from .container import DisplayContainer
from .pinnedRegions import pinnedCounterpart

if TYPE_CHECKING:
	from NVDAObjects import NVDAObject
	from braille.regions.base import Region

	from .segments import BrailleBufferSegment


def resolveTarget(obj: "NVDAObject"):
	"""Substitute an object's tree interceptor, as NVDA's own focus handling does.

	`BrailleHandler.handleGainFocus` presents a browse mode document through its interceptor
	rather than through the `NVDAObject`, and a pin has to do the same. Without it the
	regions are built over the single element the browse mode cursor happens to be in, which
	reads correctly and then cannot be panned, because that element's text ends where the
	element does.

	:param obj: the object the user asked to pin.
	:return: the interceptor if there is a ready one that is not in pass through, otherwise
		the object unchanged.
	"""
	interceptor = getattr(obj, "treeInterceptor", None)
	if interceptor is None:
		return obj
	try:
		if not interceptor.passThrough and interceptor.isReady:
			return interceptor
	except Exception:
		# An interceptor part way through being torn down. The object itself still reads.
		log.debugWarning("Could not consult a tree interceptor while pinning", exc_info=True)
	return obj


class ObjectMonitor:
	"""An object pinned to one segment of the braille display."""

	def __init__(self, obj: "NVDAObject", segmentKey: str) -> None:
		"""
		:param obj: the object to keep visible.
		:param segmentKey: the key of the segment to show it in.
		"""
		self.name = self._describe(obj)
		"""What to call this pin. Taken from the object the user chose, before the tree
		interceptor is substituted for it, because a page's title is more use than the word
		document."""
		self.obj = resolveTarget(obj)
		self.segmentKey = segmentKey
		self.regions: Optional[list["Region"]] = None
		"""The regions being shown, built on first refresh and kept thereafter."""
		self._lastCells: Optional[list[int]] = None
		"""What was last written, so an unchanged refresh can leave the display alone."""
		log.debug(f"Monitoring {self.name!r} ({self.obj!r}) in segment {segmentKey!r}")

	@staticmethod
	def _describe(obj: "NVDAObject") -> str:
		""":return: a short name for an object, for announcing what is being monitored."""
		if obj.name:
			return obj.name
		try:
			return controlTypes.Role(obj.role).displayString
		except (ValueError, AttributeError):
			return str(obj.role)

	def buildRegions(self) -> list["Region"]:
		"""Generate the regions for the monitored object, marked for its segment.

		NVDA decides what regions the object wants; this only replaces the cursor policy of
		any text region among them, so that the pin reads from a position of its own rather
		than from the object's cursor. The region NVDA built for that purpose is discarded,
		which wastes one render per pin and keeps the choice of region in NVDA's hands.
		"""
		regions: list["Region"] = []
		for region in getFocusRegions(self.obj, review=False):
			pinned = pinnedCounterpart(region)
			if pinned is not None:
				pinned.update()
				region = pinned
			region.targetSegment = self.segmentKey
			regions.append(region)
		return regions

	def refresh(self, reveal: bool = False) -> None:
		"""Redraw the monitored object in its segment.

		Called when the object may have changed, which on the refresh timer means several
		times a second. The display is written only when what it would show has changed, so
		the usual case costs a re-read of the object and nothing more.

		:param reveal: show the pin from its start, as when it is first made. Otherwise the
			segment's window position is kept, so a refresh does not undo the user's panning.
		"""
		container = braille.handler.mainBuffer if braille.handler else None
		if not isinstance(container, DisplayContainer):
			return
		try:
			segment = container.segmentForKey(self.segmentKey)
		except LookupError:
			log.debugWarning(f"Segment {self.segmentKey!r} no longer exists")
			return
		try:
			if self.regions is None:
				self.regions = self.buildRegions()
			else:
				for region in self.regions:
					region.update()
		except Exception:
			# The object may have died, which is not worth an error in the log. Whatever is
			# on the display stays there rather than being replaced with nothing.
			log.debugWarning(f"Could not generate regions for {self.name!r}", exc_info=True)
			return
		cells = [cell for region in self.regions for cell in region.brailleCells]
		if not reveal and cells == self._lastCells and segment.regions == self.regions:
			# Nothing has changed and the segment still holds what this monitor put there.
			# Rewriting it would cost a display update and lose the user's window position.
			return
		self._lastCells = cells
		self._install(container, segment, reveal)

	def _install(
		self,
		container: DisplayContainer,
		segment: "BrailleBufferSegment",
		reveal: bool,
	) -> None:
		"""Put the regions into the segment and show them.

		The window position is saved across the swap and restored afterwards. Without that,
		every refresh would send the segment back to the start of the pinned content, which
		would make a pin that updates and a pin that can be panned mutually exclusive.
		"""
		saved = False
		if not reveal and segment.regions:
			try:
				segment.saveWindow()
				saved = True
			except LookupError:
				saved = False
		segment.clear()
		for region in self.regions or ():
			segment.append(region)
		container.update()
		if saved:
			try:
				segment.restoreWindow()
				segment.hasSavedWindow = True
			except LookupError:
				log.debug(f"Could not restore the window in segment {self.segmentKey!r}", exc_info=True)
				saved = False
		if not saved and segment.regions:
			container.focus(segment.regions[-1])
		container.updateDisplay()

	def __repr__(self) -> str:
		return f"<ObjectMonitor {self.name!r} in segment {self.segmentKey!r}>"
