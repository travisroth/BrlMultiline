# BrlMultiline: pinning an object to a segment.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Keeps an object visible in a segment while the focus moves elsewhere.

Regions are generated for the object in the ordinary way and marked for the segment they
belong to. The replacement for `BrailleHandler._doNewObject` clears only the segments
that are receiving new regions, so a pinned object survives focus changes without any
further intervention.

1. A browse mode document is read through its tree interceptor; see `resolveTarget`.
2. The regions are built once and kept, so a refresh keeps the reader's panning. See
   `pinnedRegions`.
3. Nothing reports a change in a background window, so `patches` re-reads pins on a timer,
   and `refresh` leaves the display alone when nothing changed.

A monitor holds its segment's key, not its number, so it survives a rebuild that keeps the
segment. See `panels.SegmentSpec.key`.
"""

import itertools
from typing import TYPE_CHECKING

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

Two, since one row of a document is the line NVDA's own regions already show.
"""

MIN_RUN_ROWS = 1
"""The fewest rows a segment can show a pinned *run of objects* as a flow in.

One, unlike `MIN_FLOW_ROWS`: one row of a run is one message or item, and panning moves to the
next, where the regions would show only the list object.
"""


def resolveTarget(obj: "NVDAObject"):
	"""Substitute an object's tree interceptor, as NVDA's own focus handling does.

	As `BrailleHandler.handleGainFocus` does, or the pin could not be panned past the element
	under the browse mode cursor.

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


def isBelowLockScreen(obj) -> bool:
	""":return: whether Windows is locked and the object is below the lock screen, so must not be shown.

	NVDA's own test, which also accepts a tree interceptor. Without NVDA's security module, as in
	the unit tests, nothing is below a lock screen.

	:param obj: the object or tree interceptor a pin reads.
	"""
	try:
		from utils.security import objectBelowLockScreenAndWindowsIsLocked
	except ImportError:
		return False
	return objectBelowLockScreenAndWindowsIsLocked(obj, shouldLog=False)


class ObjectMonitor:
	"""An object pinned to one segment of the braille display."""

	def __init__(self, obj: "NVDAObject", segmentKey: str) -> None:
		"""
		:param obj: the object to keep visible.
		:param segmentKey: the key of the segment to show it in.
		"""
		self.name = self.describe(obj)
		"""What to call this pin. Taken from the object the user chose, before the tree
		interceptor is substituted for it, because a page's title is more use than the word
		document."""
		self.obj = resolveTarget(obj)
		self.segmentKey = segmentKey
		self.regions: list["Region"] | None = None
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

		Set when the pin is made (see `FlowBand.wantsColumnsFor`), and the pin's own, since the
		band forgets its request when the reader leaves the table.
		"""

		self._builtFor: tuple[int, int] | None = None
		"""The segment size the controller was built for, since a rebuild may change it."""

		self._builtInColumns = False
		"""Whether the controller in hand was built in columns, so a change of mind rebuilds."""

		self._lastCells: list[int] | None = None
		"""What was last written, so an unchanged refresh can leave the display alone."""

		self.counts = [0, 0]
		"""How many times this pin was read, and how many of those read something different.

		For the dry run: reads climbing with changes at zero means the page is not changing,
		not that this add-on stopped asking.
		"""
		log.debug(f"Monitoring {self.name!r} ({self.obj!r}) in segment {segmentKey!r}")

	def moveTo(self, segmentKey: str) -> None:
		"""Show this pin in a different segment from now on.

		The key and the regions' `targetSegment` both move, or `_doNewObject` would drop them.
		What was last written is forgotten too. Used when a pin's display has gone.

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
	def describe(obj: "NVDAObject") -> str:
		""":return: a short name for an object, for announcing what is being monitored."""
		if obj.name:
			return obj.name
		try:
			return controlTypes.Role(obj.role).displayString
		except (ValueError, AttributeError):
			return str(obj.role)

	def buildRegions(self) -> list["Region"]:
		"""Generate the regions for the monitored object, marked for its segment.

		NVDA chooses the regions; a text region is replaced by one reading from a position of its
		own, keeping the choice of region in NVDA's hands.
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

		Called several times a second; the display is written only when its cells changed. While
		Windows is locked an object below the lock screen is neither read nor shown. See `_hide`.

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
		if isBelowLockScreen(self.obj):
			self._hide(container, segment)
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

		When the segment is tall enough (see `_minimumRows`) and `buildController` finds
		something to read. Not gated on the flow settings, which are about the focus, not pins.
		Rebuilt when the segment's size changes.

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
		# Once, as the pin is made, so a band that filled exactly knows whether the run ends
		# there. See `FlowController.lookPastTheEnd`.
		control.lookPastTheEnd()
		self.controller = control
		self._builtFor = size
		self._builtInColumns = self.wantsColumns
		log.debug(f"Reading the pin on {self.name!r} as a flow in {size[0]} by {size[1]}")
		return True

	def _minimumRows(self, rows: int) -> int:
		""":return: the fewest rows this pin can be read as a flow in.

		Only a one row segment asks whether this is a run of objects, as `buildController` will.

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

		Columns first, falling back to reading order, also when the page reloaded under the pin.

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

		Asked of the source's handle, since the reader is elsewhere.
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

		`rereadContent` walks only the window's blocks, as the band also handles.
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

		As `refresh`, written only when the cells changed; `rereadContent` keeps the window.

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
				# What a pin watches may still be growing, such as a chat. Asked only while the
				# last row read is on the display, one fetch at a time. Whether it scrolls on is
				# `bmConfig.shouldFollowLiveContent`.
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
			# Drawn here and published once below, not through `refresh`, which would send a
			# second identical frame.
			segment.update()
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

	def _hide(self, container: DisplayContainer, segment) -> None:
		"""Take this pin off the display, keeping what it reads for when it may be shown again.

		Detached rather than cleared, since clearing a flow's segment puts its regions back. The
		next refresh allowed to show the pin draws it again, because the segment no longer holds
		it. The display is written only when something was taken off.

		:param container: the display.
		:param segment: the segment this pin is in.
		"""
		if getattr(segment, "controller", None) is not None:
			segment.detach()
		elif segment.regions:
			segment.clear()
		else:
			return
		self._lastCells = None
		container.updateDisplay()

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

		The window position is kept across the swap, so a pin can both update and be panned.
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
