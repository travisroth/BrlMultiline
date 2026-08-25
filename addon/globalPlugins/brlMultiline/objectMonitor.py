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

import itertools
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


_generations = itertools.count(1)
"""Numbers each reading a pin makes, so that a bookmark from one cannot match another's."""

MIN_FLOW_ROWS = 2
"""The fewest rows a segment can show a pinned object as a flow in.

Two. A flow in one row is a row, and everything a flow is for — the shape of a document under
the hand, a table's columns lining up, a run of items to scan down — needs a second one to
exist at all. Below this the pin reads as it always has, through NVDA's own regions.
"""


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
		self.pinned = obj
		"""The object the reader chose, before the tree interceptor was substituted for it.

		Kept because a flow does its own substituting: `flowBuild.buildController` asks
		`documentFor` the same question `resolveTarget` asks, and handing it the answer
		instead of the question loses the object the answer was about."""

		self.controller = None
		"""The flow reading this pin, when its segment has room for one. See `_asAFlow`."""

		self._builtFor: Optional[tuple[int, int]] = None
		"""The segment size the controller was built for, since a rebuild may change it."""

		self._lastCells: Optional[list[int]] = None
		"""What was last written, so an unchanged refresh can leave the display alone."""

		self.counts = [0, 0]
		"""How many times this pin was read, and how many of those read something different.

		For the dry run, and it answers a question nothing else can. A pin that stops moving
		has stopped somewhere, and there are three candidates: this add-on stopped asking, the
		page stopped changing, or something between the two stopped delivering. The first is
		the only one that is ours, and these two numbers separate it from the others — reads
		climbing with changes at zero means the asking is fine and the answer is always the
		same, which is the page.
		"""
		log.debug(f"Monitoring {self.name!r} ({self.obj!r}) in segment {segmentKey!r}")

	def moveTo(self, segmentKey: str) -> None:
		"""Show this pin in a different segment from now on.

		Two things have to move, and the key on this object is only the first. The regions built
		for the pin carry `targetSegment`, which is what `_doNewObject` reads to decide where a
		region belongs, so a region left stamped with the old key would be dropped as belonging
		to a segment that no longer exists.

		What was last written is forgotten as well, which is housekeeping rather than a third
		necessity: `refresh` also asks whether the destination already holds these regions, and
		a segment this pin has never been in does not, so it would install either way. Clearing
		it keeps that from being the only thing standing between a move and a blank segment.

		Used when the display a pin was on has gone and there is somewhere else for it.

		:param segmentKey: the key of the segment to show it in.
		"""
		if segmentKey == self.segmentKey:
			return
		log.debug(f"Moving the pin on {self.name!r} from {self.segmentKey!r} to {segmentKey!r}")
		self._dropFlow(self._segment())
		self.segmentKey = segmentKey
		for region in self.regions or ():
			region.targetSegment = segmentKey
		self._lastCells = None

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
		if self._asAFlow(segment):
			self._refreshFlow(container, segment, reveal)
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
		self.counts[0] += 1
		if cells != self._lastCells:
			self.counts[1] += 1
		if not reveal and cells == self._lastCells and segment.regions == self.regions:
			# Nothing has changed and the segment still holds what this monitor put there.
			# Rewriting it would cost a display update and lose the user's window position.
			return
		self._lastCells = cells
		self._install(container, segment, reveal)

	# Reading a pin as a flow.

	def _asAFlow(self, segment) -> bool:
		"""Whether this pin is being read as a flow, building one if it can be.

		A pin is a document, a run of objects or a table just as much as the focus is, and
		everything the band does for those it can do here — the reader said so, and the
		display the focus is not on is now where pins live, so there is room to mean it.

		Two things have to hold. The segment must have `MIN_FLOW_ROWS` to show one in, and
		`buildController` must find something to read — the same question, asked the same way,
		that decides whether the band lights up.

		**Not gated on the flow settings**, deliberately, and the reader's own account is the
		argument: they turned the flow off in order to pin something at all. Those settings say
		what the band does with the focus. A pin is not the focus and is not automatic — it is
		one thing the reader asked to see in one place — and refusing to read it well because
		the band is reading something else badly would be the same frustration in a new place.

		Rebuilt when the segment's size changes, because a controller is laid out for a band
		of a particular shape and a display swap or a claim can change it.

		:param segment: the segment this pin is in.
		:return: whether there is a flow to draw.
		"""
		size = (segment.rect.numRows, segment.rect.numCols)
		if size[0] < MIN_FLOW_ROWS or not hasattr(segment, "attach"):
			self._dropFlow(segment)
			return False
		if self.controller is not None and self._builtFor == size:
			return True
		self._dropFlow(segment)
		from .flowBuild import buildController

		try:
			control = buildController(
				obj=self.pinned,
				numRows=size[0],
				numCols=size[1],
				handler=braille.handler,
				live=False,
				generation=next(_generations),
			)
		except Exception:
			log.debugWarning(f"Could not read {self.name!r} as a flow", exc_info=True)
			return False
		if control is None:
			return False
		self.controller = control
		self._builtFor = size
		log.debug(f"Reading the pin on {self.name!r} as a flow in {size[0]} by {size[1]}")
		return True

	def _refreshFlow(self, container: DisplayContainer, segment, reveal: bool) -> None:
		"""Read the flow again and draw it, if what it would show has changed.

		The same bargain the region path makes and for the same reason: the display is
		written only when the cells came out different, so a pin that is not changing costs a
		read and no display traffic. Panning is the reader's and is not undone here — the
		window keeps its place across a re-read, which is the whole of what `rereadContent`
		is for.

		:param container: the display.
		:param segment: the segment this pin is in.
		:param reveal: show it from the start, as when the pin is first made.
		"""
		control = self.controller
		try:
			before = control.cells()
			control.rereadContent()
			cells = control.cells()
		except Exception:
			log.debugWarning(f"Could not read the flow on {self.name!r}", exc_info=True)
			return
		self.counts[0] += 1
		if cells != self._lastCells:
			self.counts[1] += 1
		attached = getattr(segment, "controller", None) is control
		if attached and not reveal and cells == before and cells == self._lastCells:
			return
		self._lastCells = cells
		if not attached:
			segment.attach(control)
		else:
			segment.refresh()
		container.updateDisplay()

	def _dropFlow(self, segment=None) -> None:
		"""Stop reading this pin as a flow, leaving the segment for the region path."""
		if self.controller is None:
			return
		self.controller = None
		self._builtFor = None
		self._lastCells = None
		detach = getattr(segment, "detach", None)
		if detach is not None and getattr(segment, "controller", None) is not None:
			detach()

	def stop(self) -> None:
		"""Give up whatever this pin is holding, before it is forgotten."""
		self._dropFlow(self._segment())

	def _segment(self):
		""":return: the segment this pin is in, or None if it has gone."""
		container = braille.handler.mainBuffer if braille.handler else None
		if not isinstance(container, DisplayContainer):
			return None
		try:
			return container.segmentForKey(self.segmentKey)
		except LookupError:
			return None

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
