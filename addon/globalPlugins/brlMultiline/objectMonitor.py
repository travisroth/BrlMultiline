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

from . import bmConfig
from .container import DisplayContainer
from .pinnedRegions import pinnedCounterpart

if TYPE_CHECKING:
	from NVDAObjects import NVDAObject
	from braille.regions.base import Region

	from .segments import BrailleBufferSegment


_generations = itertools.count(1)
"""Numbers each reading a pin makes, so that a bookmark from one cannot match another's."""

MIN_FLOW_ROWS = 2
"""The fewest rows a segment can show a pinned document or table as a flow in.

Two. A document in one row is a row, and what a document flow is for — the shape of a page
under the hand, a table's columns lining up — needs a second one to exist at all. Below this
such a pin reads as it always has, through NVDA's own regions.
"""

MIN_RUN_ROWS = 1
"""The fewest rows a segment can show a pinned *run of objects* as a flow in.

One, and the difference from L{MIN_FLOW_ROWS} is the whole point. A run has no shape to
spread out: it is one thing per row, and one row of it is one message, one list item, one
menu entry, with panning moving to the next. That is a chat monitor on a Focus 80, and it is
what the two row rule was quietly refusing.

Reported from hardware. A chat pinned to a Monarch and carried to a single row Focus 80 by
the focus command stopped being a flow, fell back to regions, and showed the list *object*
rather than the messages in it — one line that could not be panned at all. The fallback is
worse there than the flow it was protecting the reader from.

A document is a different case and keeps the old rule: one row of a document is the line NVDA
already shows, and the regions show it better.
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

		self.wantsColumns = False
		"""Whether this pin's table is read in columns.

		Set when the pin is made, from whether the reader was already reading that table in
		columns — see `FlowBand.wantsColumnsFor`. It is a flag of this pin's own rather than a
		reading of the band's, because the band drops its request the moment the reader leaves
		the table and a pin is precisely a layout that outlives their being there.
		"""

		self._builtFor: Optional[tuple[int, int]] = None
		"""The segment size the controller was built for, since a rebuild may change it."""

		self._builtInColumns = False
		"""Whether the controller in hand was built in columns, so a change of mind rebuilds."""

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

		Two things have to hold. The segment must be tall enough — see L{_minimumRows}, which
		is two rows for a document and one for a run of objects — and `buildController` must
		find something to read, the same question, asked the same way, that decides whether
		the band lights up.

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
		if size[0] < self._minimumRows(size[0]) or not hasattr(segment, "attach"):
			self._dropFlow(segment)
			return False
		if self.controller is not None and self._tableHasGrown():
			# The plan is of a table that no longer exists: its widths were measured from the
			# columns that were there and it has no page for one that has arrived. Read the
			# whole thing again, which for a pin means building it again.
			self._dropFlow(segment)
		if (
			self.controller is not None
			and self._builtFor == size
			and self._builtInColumns == self.wantsColumns
		):
			return True
		self._dropFlow(segment)
		control = self._build(size)
		if control is None:
			return False
		# Asked once, here, while the pin is being made: a band that filled exactly never
		# probed past its last block, so nothing had established that this run ends where the
		# display does — and what arrives next would then have arrived below a display that
		# would not follow it. See `FlowController.lookPastTheEnd`.
		control.lookPastTheEnd()
		self.controller = control
		self._builtFor = size
		self._builtInColumns = self.wantsColumns
		log.debug(f"Reading the pin on {self.name!r} as a flow in {size[0]} by {size[1]}")
		return True

	def _minimumRows(self, rows: int) -> int:
		""":return: the fewest rows this pin can be read as a flow in.

		Asked what is being read only when it matters. A segment with two rows or more can
		flow anything, so the question is not put; a segment with one row asks whether this
		pin is a run of objects, which is the same question `buildController` answers a moment
		later and is answered here by the same function.

		:param rows: how many rows the segment has.
		"""
		if rows >= MIN_FLOW_ROWS:
			return MIN_FLOW_ROWS
		return MIN_RUN_ROWS if self._readsAsARun() else MIN_FLOW_ROWS

	def _readsAsARun(self) -> bool:
		""":return: whether this pin is a run of objects rather than a document or a table.

		A table asked for in columns is not, whatever its rows are made of: columns need rows
		to line up in, so a table on one row goes back to the regions like a document.
		"""
		if self.wantsColumns:
			return False
		try:
			from .flowBuild import objectAdapterFor

			return objectAdapterFor(self.pinned) is not None
		except Exception:
			log.debugWarning(f"Could not ask whether {self.name!r} is a run of objects", exc_info=True)
			return False

	def _build(self, size: tuple):
		"""Build the flow for this pin, in columns if that is what was asked for.

		The column layout is tried first and falls back rather than failing: what the reader
		asked for was this table in columns, and a table that will not lay out is still a
		table worth reading in order. Falling back also covers the pin outliving the table —
		a page that reloaded under it — where insisting on columns would leave the segment
		blank rather than showing whatever is there now.

		:param size: the segment's rows and columns.
		:return: the controller, or None if there is nothing here to read.
		"""
		from .flowBuild import buildController, buildTableController

		shape = dict(
			numRows=size[0],
			numCols=size[1],
			handler=braille.handler,
			live=False,
			generation=next(_generations),
		)
		if self.wantsColumns:
			try:
				control = buildTableController(obj=self.pinned, **shape)
			except Exception:
				log.debugWarning(f"Could not read {self.name!r} in columns", exc_info=True)
				control = None
			if control is not None:
				return control
		try:
			return buildController(obj=self.pinned, **shape)
		except Exception:
			log.debugWarning(f"Could not read {self.name!r} as a flow", exc_info=True)
			return None

	def columnPlan(self):
		""":return: the table layout this pin is showing, or None if it is not showing one.

		The same question the band answers, asked the same way, so that a command looking for
		a table on the display does not have to know which of the two it found.
		"""
		plan = getattr(getattr(self.controller, "renderer", None), "columnPlan", None)
		return None if plan is None or plan.isEmpty else plan

	def turnColumnPage(self, by: int) -> bool:
		"""Move this pin across a table too wide to show at once.

		:param by: how many pages to move, negative for back towards the first column.
		:return: whether the page changed.
		"""
		plan = self.columnPlan()
		if plan is None:
			return False
		if not self.controller.useColumnPage(plan.turnedBy(by)):
			return False
		self.refresh()
		return True

	def _tableHasGrown(self) -> bool:
		""":return: whether the table this pin is showing has gained a column.

		Asked of the source's own handle rather than of the reader's position, because the
		reader has gone somewhere else — which is what the pin is for. The band's shape check
		cannot be used here for exactly that reason.
		"""
		grown = getattr(getattr(self.controller, "source", None), "hasGrown", None)
		if grown is None:
			return False
		try:
			return bool(grown())
		except Exception:
			log.debugWarning(f"Could not check the shape of the table on {self.name!r}", exc_info=True)
			return False

	def _rereadPinnedRow(self) -> None:
		"""Read this pin's header row again, since it is outside the window.

		`rereadContent` walks the window's blocks and the pinned row is deliberately not one
		of them, so a header renamed under the pin went on saying what it used to. The band
		learnt this a few commits ago and the pin did not.
		"""
		control = self.controller
		if control is None or getattr(control, "pinnedBlock", None) is None:
			return
		header = getattr(control.source, "headerBlock", None)
		if header is None:
			return
		try:
			control.setPinned(header())
		except Exception:
			log.debugWarning(f"Could not read the header row on {self.name!r} again", exc_info=True)

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
			self._rereadPinnedRow()
			if control.hasMoreToFetch:
				# The pin ran out of budget last time. Its refresh tick is the continuation
				# pass the band gets from a timer of its own — the same bargain, on a clock
				# that is already ticking.
				control.fill()
			if control.isShowingTheTail:
				# A pin is a thing the reader asked to watch, and some of what is worth
				# watching is still being written: a chat history, a log, a build. The run was
				# read to its end when the pin was made and nothing asked again, so a pinned
				# conversation stopped at the message that was newest that minute. Asked only
				# while the reader can feel the last row that has been read, so panning back
				# into the history costs nothing, and only one fetch, which the source refuses
				# again the moment there is really nothing more.
				#
				# Whether what comes back scrolls onto a full display is the reader's, per
				# display and per profile: a chat is worth following and a page that rewrites
				# itself for reasons of its own is not.
				control.reconsiderEnd(scrollIntoView=bmConfig.shouldScrollToNewContent())
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
		self._builtInColumns = False
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
