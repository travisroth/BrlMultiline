# BrlMultiline: putting a flow on the display.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Claiming a band of the display for a flow, and keeping it fed.

The pieces below this each do one thing: `flowSources` finds blocks, `flowRender` lays
one out, `flow` packs them into rows and moves a window, `flowControl` drives those three,
and `flowSegment` shows the result. This is what puts them on a display: it claims the
band, builds a controller over whatever the reader is in, and hands the two to each other.

It also owns the claim, so it is told when the display is rebuilt, the claim is evicted
or everything is taken back. A rebuild keeps geometry but not content, so `onRebuilt`
redraws.

The band lies within one physical display, never across a composite, which would reach
cells no hardware has. See `bandRect`.
"""

import dataclasses
import itertools
import time
from typing import TYPE_CHECKING, Any, Optional

import api
from logHandler import log

from . import (
	bmConfig,
	flowForms,
	flowObjects,
	flowQuickNav,
	flowTable,
	flowTableLayouts,
	flowTableSource,
	patches,
)
from .flow import CallCancelled
from .flowControl import FlowController
from .flowBuild import (
	Unreadable,
	buildTableController,
	bandSize,
	buildController,
	describeObject,
	interactiveRegionFactory,
	isTreeInterceptor,
	objectAdapterFor,
	readingUnitFor,
)
from .devices import DeviceInfo, deviceMap, preferredDevice
from .flowSegment import FlowBufferSegment
from .layout import SegmentRect, wholeDisplayRect
from .views import focusDisplayDriver
from .flowSources import DocumentFlowSource, documentFor
from .panels import FlowPanel, PanelOwner

FILL_AGAIN_MILLIS = 120
"""How long after a fill that ran out of budget the band tries again.

Running out of budget is answered by coming back rather than by a bigger budget. Each pass
gets a fresh budget, the display is written only when a pass added something, and the chain
stops when one adds nothing. Short enough to fill rows promptly, long enough to leave the
reader's keystrokes ahead of it. See `_fillMore`.
"""

MIN_BAND_ROWS = 2
"""The fewest rows a display must have before a flow is worth claiming it.

On one row a flow adds nothing NVDA's own line does not, and the focus mark and indent
would be wrong there. The same as `objectMonitor.MIN_FLOW_ROWS`, kept separate because either
could change.
"""

LIVE_SETTLE_MILLIS = 250
"""How long the band waits after a document change before reading the table again.

Not a poll: it starts when NVDA says the document changed (see
`patches._handleUpdateTellingTheBand`), and lets a burst of events settle into one read.
"""

LIVE_POLL_MILLIS = 2000
"""How often a table is read again when nothing is going to tell the band it changed.

The fallback, and only that: it is used when the document-change patch is not installed. See
`bmConfig.liveReadSeconds` for the reader's own number, which overrides it either way.
"""

SETTLE_MILLIS = 150
"""How long after a keystroke into an edit the band comes back for a second look.

Long enough for the editor to finish answering, short enough that a pausing reader never
meets its transient answers. Restarted on every keystroke, up to `SETTLE_LATEST_MILLIS`.
"""

SETTLE_LATEST_MILLIS = 600
"""The longest a settle pass may be put off by the typing it is waiting for.

Restarting on every keystroke alone would never run for a fast typist; never restarting
would run mid-burst, when the editor's answers are wrong. So a keystroke pushes the pass
out, but never past this."""


def _now() -> float:
	""":return: a monotonic clock in milliseconds, which is what the delays here are in."""
	return time.monotonic() * 1000

if TYPE_CHECKING:
	from .container import DisplayContainer

BAND_NAME = "flow"
"""The panel's name, and so the key of its single segment."""

NO_TABLE = "none"
"""The reader is not in a table this can lay out. See `FlowBand.layOutTable`."""

NO_LAYOUT = "layout"
"""They are in one, and it did not come out as columns.

Distinct from not being in a table at all.
"""

NOT_READ = "unread"
"""They are in one, and nothing could be read out of it.

Distinct from a table whose columns do not fit, which is about the arrangement. See
`flowBuild.Unreadable`.
"""

_generations = itertools.count(1)
"""Numbers each reading of a document, so that two documents cannot share a block identity.

A bookmark is a position within one document and says nothing about which. Following the
focus from one document to another with the same generation would let a stale anchor match
a block in the new one, and the reader would be put somewhere arbitrary."""


class FlowBand(PanelOwner):
	"""One flow, claimed onto the display and kept fed.

	Held by the plugin for as long as the reader wants a flow. Everything about which
	document is being read lives in the controller, which is replaced when the reader moves
	to a different one; the claim on the display outlives that.
	"""

	def __init__(self, plugin) -> None:
		"""
		:param plugin: the global plugin, for the display and its claims.
		"""
		self.plugin = plugin
		self.lastError: Optional[str] = None
		"""Why the band could not be shown, for a command to report."""

		self.controller: Optional[FlowController] = None
		self.obj: Any = None
		"""What the current controller is reading, so a focus change can be recognised."""

		self._rechecking = False
		"""Guards `recheck` against the redraw its own answer causes."""

		self._settleTimer = None
		"""The pending settle pass, so a fresh keystroke can restart it. See L{_scheduleSettle}."""

		self._settleBy = 0.0
		"""When the pending pass must run by, however much more is typed. See
		L{SETTLE_LATEST_MILLIS}."""

		self._liveTimer = None
		"""The pending live-table pass. See L{_scheduleLiveRead}."""

		self._liveDelay = 0
		"""How far off the pending pass is, so that sooner news can bring it forward."""

		self._fillTimer = None
		"""The pending pass to finish a fill the budget cut short. See L{_scheduleFill}."""

		self._unwrapTurnedOver: Optional[tuple[bool, bool]] = None
		"""The setting when the reader last turned unwrapped lines over, and what they chose, or
		None. See L{unwrapLines}."""


		self.liveCounts = [0, 0, 0]
		"""Changes heard, passes run, passes that redrew. For the dry run, and it earns its
		place: whether the event reaches us at all is the one thing about this that cannot be
		felt, and a reader whose prices sit still needs to know which half is not working."""

		self.tableProblem: Optional[str] = None
		"""Why the last request for columns came to nothing, for the command to report.

		`NO_TABLE` or `NO_LAYOUT`, and None when the last one worked. See `layOutTable`."""

		self.tableNotes: list = []
		"""What `buildTableController` said as it worked, kept for the report on a failure."""

		self._layoutOffered: Any = None
		"""The table a saved layout was last offered for, so it is offered once."""

		self._keepOnRebuild = True
		"""Whether the next rebuild leaves the reader where they were reading. Set with the
		reason for it; see `_rebuildBecause`."""

		self._readAgainAt: Any = None
		"""The page and row to put back when a rebuild is under way, or None. See
		`_whereTheyAreReading`."""

		self._askedAboutCell: Any = None
		"""The table and undrawn cell the band last asked about, so it is asked once. The table
		too, since two tables both have a row 1 column 1."""

		# The table the reader asked to see in columns is the plugin's. See `TableRequest`.

	# The claim.

	def start(self) -> bool:
		"""Claim the band, and show a flow in it if there is one to show.

		The claim is kept while the reader wants a flow, even with nothing to flow, when it
		shows the focus as an undivided display would.

		:return: whether the band is claimed. `lastError` says what went wrong, and tells a
			band that could not be claimed apart from one that has nothing to read yet.
		"""
		self.lastError = None
		rect = self.bandRect()
		if rect.numRows < MIN_BAND_ROWS:
			# Before claiming, so that nothing is taken and given back. A one row display is
			# one NVDA should be left to — see `MIN_BAND_ROWS`.
			self.lastError = f"a flow needs {MIN_BAND_ROWS} rows and this display has {rect.numRows}"
			return False
		try:
			panel = FlowPanel(BAND_NAME, rect)
			self.plugin.activatePanel(panel)
		except Exception as error:
			log.error("Could not claim a band for the flow", exc_info=True)
			self.lastError = str(error)
			return False
		self._follow()
		# Browse mode is patched only while a band is claimed: a reader without one has no
		# use for it, and it is taken back in `stop`.
		flowQuickNav.install()
		if not self.refresh(force=True):
			self.lastError = "nothing here reads as a flow"
		return True

	def bandRect(self, devices: "Optional[list[DeviceInfo]]" = None) -> SegmentRect:
		"""Choose the rectangle to claim.

		Inside one physical display's live cells: the whole of an ordinary display, or on a
		composite the member the reader named, or else the one the focus is on, so moving the
		focus moves the band.

		As many rows from the top as the reader asked for, all of them by default. A count
		larger than the display is trimmed rather than refused.

		:param devices: the physical displays, read from the driver when not given.
		:return: the rectangle to claim.
		"""
		numRows, numCols = self._displaySize()
		devices = deviceMap() if devices is None else devices
		device = preferredDevice(self._displayName(devices, numCols), devices)
		whole = device.liveRect if device is not None else wholeDisplayRect(numRows, numCols)
		rows = bmConfig.getFlowRows()
		if rows and rows < whole.numRows:
			return SegmentRect(row=whole.row, col=whole.col, numRows=rows, numCols=whole.numCols)
		return whole

	def _displayName(self, devices: "list[DeviceInfo]", numCols: int) -> str:
		""":return: the driver name of the display this band belongs on.

		The reader's own choice, else the display the focus is on, else None, which leaves
		`preferredDevice` to take the tallest.

		:param devices: the physical displays.
		:param numCols: the composite's width.
		"""
		named = bmConfig.getFlowDisplay()
		if named:
			return named
		try:
			return focusDisplayDriver(devices, numCols, bmConfig.getFocusSegment()) or ""
		except Exception:
			log.debugWarning("Could not tell which display the focus is on", exc_info=True)
			return ""

	def _follow(self) -> None:
		"""Make the band bring its focus changes here, rather than answering them itself."""
		segment = self.segment()
		if segment is not None:
			segment.follow(self.handleFocusRegions)
			segment.onUpdate = self.recheck
			segment.onSettle = self._scheduleSettle

	def _scheduleSettle(self) -> None:
		"""Come back for a second look shortly after a keystroke into an edit.

		A rich editor can answer transiently wrong at the instant of a keystroke, and the next
		re-read heals it. Timing follows `SETTLE_MILLIS` and `SETTLE_LATEST_MILLIS`. The pass
		redraws only when something changed, so a settle that changes nothing ends there.
		"""
		import wx

		now = _now()
		if self._settleTimer is None:
			self._settleBy = now + SETTLE_LATEST_MILLIS
		elif now + SETTLE_MILLIS <= self._settleBy:
			# Still inside the ceiling, so this keystroke buys the editor more time to
			# finish answering.
			self._cancelSettle()
		else:
			# A pass is coming and has been put off as long as it may be. Pushing it out
			# again is how the repair was starved.
			return
		try:
			self._settleTimer = wx.CallLater(int(max(1, min(SETTLE_MILLIS, self._settleBy - now))), self._settle)
		except Exception:
			log.debugWarning("Could not schedule a flow settle pass", exc_info=True)
			self._settleTimer = None

	def _settle(self) -> None:
		"""Read the edit again now that it has had a moment, and redraw if that changed anything.

		The re-read replaces the active block's region, so the segment's regions are
		reconciled on every path out, or NVDA would keep the retired region. Only writing to
		the hardware depends on the cells changing.
		"""
		self._settleTimer = None
		control = self.controller
		if control is None:
			return
		segment = self.segment()
		try:
			before = control.displayFrame()
			control.followCursor()
			if control.displayFrame() == before:
				# Disarm `rereadWhileWriting`, which this pass set; with no redraw the segment
				# would otherwise find it later and settle again for nothing.
				control.rereadWhileWriting = False
				return
			if segment is not None:
				segment.refresh()
		except Exception:
			log.debugWarning("A flow settle pass failed", exc_info=True)
		finally:
			if segment is not None:
				try:
					segment.syncRegions()
				except Exception:
					log.debugWarning("A flow could not point NVDA at its active block", exc_info=True)

	def documentChanged(self, document) -> None:
		"""Answer NVDA saying that a browse mode document changed under it.

		Arrives for any accessibility event the buffer backend acted on, so a quiet page costs
		nothing. For any document the band is reading, in columns or not, since NVDA refreshes
		only the caret's line. Bounded: only this band's document, and only the blocks on the
		band.

		:param document: the virtual buffer that changed.
		"""
		if not bmConfig.shouldFollowLiveContent():
			return
		source = getattr(self.controller, "source", None)
		if source is None or getattr(source, "obj", None) is not document:
			# Not this band's document. A browser holds a buffer per document and the reader
			# has other tabs open; an object flow's `obj` is an NVDAObject and matches nothing.
			return
		self.liveCounts[0] += 1
		self._scheduleLiveRead(LIVE_SETTLE_MILLIS)

	def _canReadAgain(self) -> bool:
		""":return: whether this band is showing something that can be read again in place.

		A source that answers `blockAt`, such as a table or a document. Not an object run,
		whose changes NVDA's events report, and not a document being written in, for the
		reason `FlowController._rereadBlocks` gives.
		"""
		source = getattr(self.controller, "source", None)
		if source is None or getattr(source, "blockAt", None) is None:
			return False
		return not getattr(source, "writing", False)

	def _pollMillis(self) -> int:
		""":return: how long to wait for a pass nothing has asked for, or zero for none.

		Zero whenever change notices will tell the band instead. The reader's own setting wins.
		"""
		wanted = bmConfig.liveReadSeconds()
		if wanted > 0:
			return wanted * 1000
		if not bmConfig.shouldFollowLiveContent():
			return 0
		document = getattr(getattr(self.controller, "source", None), "obj", None)
		return 0 if patches.liveUpdatesInstalled(document) else LIVE_POLL_MILLIS

	def _attach(self, segment, control) -> None:
		"""Put a controller on the band and start it being kept up to date.

		Every path that shows something goes through here, so every kind of flow is kept up to
		date the same way.

		:param segment: the band's segment.
		:param control: the controller to show.
		"""
		# Before attaching, so the first thing drawn is drawn the way the reader wants it.
		self._applyUnwrap(control)
		segment.attach(control)
		# The pending pass belongs to whatever was being read before this. Its delay was
		# chosen for that content and its first act would be to read this instead.
		self._cancelLiveRead()
		self._cancelFill()
		self._scheduleLiveRead()
		self._scheduleFill()

	def _scheduleLiveRead(self, millis: Optional[int] = None) -> None:
		"""Arrange to read a table laid out in columns again.

		:param millis: how long to wait, or None for the fallback poll — which is nothing at
			all when the band is being told about changes instead.
		"""
		import wx

		if not self._canReadAgain():
			return
		delay = self._pollMillis() if millis is None else millis
		if delay <= 0:
			return
		if self._liveTimer is not None:
			if self._liveDelay <= delay:
				# Something at least as soon is already coming. News arriving during a settle
				# must not keep pushing the pass further out, which is what restarting it
				# unconditionally would do on a page that never stops changing.
				return
			self._cancelLiveRead()
		try:
			self._liveTimer = wx.CallLater(delay, self._refreshLiveContent)
			self._liveDelay = delay
		except Exception:
			log.debugWarning("Could not schedule a live table pass", exc_info=True)
			self._liveTimer = None

	def _refreshLiveContent(self) -> None:
		"""Read the table's rows again, and redraw if the values moved.

		As `_settle`: the display is written only when the cells came out different.
		"""
		self._liveTimer = None
		self._liveDelay = 0
		control = self.controller
		if control is None or not self._canReadAgain():
			return
		fits = self._tableStillFits()
		if not fits:
			# The table changed shape, so the layout is being made again and there is nothing
			# to compare against. Asked before the re-read rather than after it, because a
			# re-read of a plan that is about to be thrown away is work for nothing.
			self._scheduleLiveRead()
			return
		self.liveCounts[1] += 1
		segment = self.segment()
		try:
			before = control.displayFrame()
			self._rereadPinnedRow()
			control.rereadContent()
			if control.displayFrame() != before:
				self.liveCounts[2] += 1
				if segment is not None:
					segment.refresh()
		except Exception:
			log.debugWarning("A live table pass failed", exc_info=True)
		finally:
			# A re-read replaces the regions the band's blocks are read through, so NVDA is
			# pointed at the replacements whether or not the cells moved. See `_settle`.
			if segment is not None:
				try:
					segment.syncRegions()
				except Exception:
					log.debugWarning("A flow could not point NVDA at its active block", exc_info=True)
		self._scheduleLiveRead()

	def _rereadPinnedRow(self) -> None:
		"""Read the pinned header row again, since it is outside the window.

		`rereadContent` walks only the window, and `FlowController.refreshPinned` only
		re-translates, so the header row needs its own read.
		"""
		control = self.controller
		if control is None or control.pinnedBlock is None:
			return
		header = getattr(control.source, "headerBlock", None)
		if header is None:
			return
		try:
			said = header()
		except Exception:
			log.debugWarning("Could not read the pinned header row again", exc_info=True)
			return
		if said is None:
			# Kept rather than cleared: `headerBlock` answers None for a failed read as well as
			# for no header, and spreadsheet reads can fail when the watchdog cancels them.
			log.debugWarning("The pinned header row could not be read again, so it stands")
			return
		control.setPinned(said)

	def _tableStillFits(self) -> bool:
		"""Notice a table that changed shape, on a pass that may write nothing.

		A column added beyond the page being drawn changes no cells, so the live pass asks the
		two cheap questions of `_tableChangedShape`. Leaving the table is `recheck`'s concern.

		:return: whether the layout in force is still a layout of this table.
		"""
		if not self._readingATable():
			return True
		source = self.controller.source
		try:
			found = flowTableSource.tableAt(self._target())
		except Exception:
			log.debugWarning("Could not look at the table during a live pass", exc_info=True)
			return True
		if found is None or not flowTableSource.sameTable(found.key, self.tableWanted):
			return True
		if not self._tableChangedShape(found, source):
			return True
		self._rebuildTable()
		return False

	def _scheduleFill(self) -> None:
		"""Arrange to finish a fill the budget cut short, if one was.

		Only one pass is ever pending, and only while there is something to finish.
		"""
		import wx

		control = self.controller
		if self._fillTimer is not None or control is None or not control.hasMoreToFetch:
			return
		try:
			self._fillTimer = wx.CallLater(FILL_AGAIN_MILLIS, self._fillMore)
		except Exception:
			log.debugWarning("Could not schedule a second fill", exc_info=True)
			self._fillTimer = None

	def _fillMore(self) -> None:
		"""Fetch what the band is still short of, with a fresh allowance.

		Chained rather than looped: each pass asks for the next only if this one added
		something, so a document that will not answer is asked twice and left alone.

		The chain follows what was added, not what is showing, since rows fetched above the
		window are off the display. The display is still written only when the cells changed.
		"""
		self._fillTimer = None
		control = self.controller
		if control is None or not control.hasMoreToFetch:
			return
		try:
			before = control.cells()
			if not control.fill():
				return
			if control.cells() != before:
				segment = self.segment()
				if segment is not None:
					segment.refresh()
		except Exception:
			log.debugWarning("Could not finish filling the band", exc_info=True)
			return
		self._scheduleFill()

	def _cancelFill(self) -> None:
		if self._fillTimer is None:
			return
		try:
			self._fillTimer.Stop()
		except Exception:
			pass
		self._fillTimer = None

	def _cancelLiveRead(self) -> None:
		self._liveDelay = 0
		if self._liveTimer is None:
			return
		try:
			self._liveTimer.Stop()
		except Exception:
			pass
		self._liveTimer = None

	def _cancelSettle(self) -> None:
		if self._settleTimer is None:
			return
		try:
			self._settleTimer.Stop()
		except Exception:
			pass
		self._settleTimer = None

	def stop(self) -> None:
		"""Give the band back and forget the flow."""
		self._cancelSettle()
		self._cancelLiveRead()
		self._cancelFill()
		flowQuickNav.remove()
		self.controller = None
		self.obj = None
		try:
			self.plugin.deactivatePanel(BAND_NAME)
		except Exception:
			log.debugWarning("Could not give back the flow band", exc_info=True)

	@property
	def isShowing(self) -> bool:
		""":return: whether a flow is on the display."""
		return self.controller is not None

	@property
	def isClaimed(self) -> bool:
		""":return: whether the band is on the display, flowing or not."""
		return self.segment() is not None

	# Keeping it fed.

	def refresh(self, force: bool = False) -> bool:
		"""Point the band at what the reader is in now.

		:param force: build a controller even if the reader has not moved, which is what a
			fresh claim or a rebuilt display needs.
		:return: whether a flow is showing afterwards.
		"""
		obj = self._target()
		# A table the reader has asked to lay out goes through whatever the flow settings say
		# about the document it is in. They asked for this table by name, which is more
		# specific than any setting about browse mode in general, and `showObject` drops the
		# request the moment they are somewhere else.
		if self.tableWanted is None and not self.isFlowable(obj):
			# `refresh` follows a rebuild. NVDA has already drawn the current focus into the
			# new segment, and clearing it here would leave a claimed but inactive band blank.
			# Forget a controller from the previous container while preserving that freshly
			# drawn fallback. A real focus change goes through `handleFocusRegions` instead;
			# returning False there lets NVDA place the regions it has just built.
			if self.controller is not None:
				self.controller.onChanged = None
			self.controller = None
			self.obj = None
			return False
		return self.showObject(obj, force=force)

	def recheck(self) -> None:
		"""Make sure the band is still reading what the reader is in.

		Called before each redraw. Catches the change NVDA sends no event for: entering or
		leaving focus mode in a form field, where the focus does not move.
		"""
		if self._rechecking or self.controller is None:
			return
		# Every redraw, because a profile switch changes the setting and says nothing to the band.
		# Costs a configuration read when nothing has changed.
		self._applyUnwrap(self.controller)
		# Before the questions below, and cheap: one attribute. A band left short by the
		# budget must be finished whatever else this redraw concludes.
		self._scheduleFill()
		if self._readingATable():
			self._recheckTable()
			return
		if self._walkedIntoASavedTable():
			return
		if self._runHasChangedShape():
			return
		obj = self._target()
		target = self._resolve(obj)
		current = self.controller.source.obj
		if target is None or target is current:
			return
		if not self._sameFamily(target, current):
			# Not the toggle this is here for. A focus somewhere else entirely is a focus
			# change, and NVDA reports those through regions that are a better account of
			# where it went than the focus object is — answering one here would race it.
			return
		self._rechecking = True
		try:
			self.showObject(obj, force=True)
		finally:
			self._rechecking = False

	def _walkedIntoASavedTable(self) -> bool:
		"""Lay a remembered table out when the reader arrows into it, with no event to say so.

		In browse mode the caret can walk into a table with no focus change, so `_showTable`
		is never asked. The counterpart of `_recheckTable`: one recognition per redraw, only
		when something is saved, and skipped for a table just refused (see `_refusedTable`).
		Offered once per table, so a layout that will not fit is not retried on every redraw.

		:return: whether the band was rebuilt, so the caller stops rather than asking a second
			question about a controller that has just been replaced.
		"""
		if not flowTableLayouts.stored():
			return False
		# Whether one applies, and nothing more: the layout is looked up again, its columns followed,
		# when the table is built. See `flowTableLayouts.layoutFor`.
		handle, _layout = self._savedLayoutHere(self._target(), follow=False)
		if handle is None:
			self._layoutOffered = None
			return False
		if flowTableSource.sameTable(handle.key, self._layoutOffered):
			return False
		self._layoutOffered = handle.key
		self.tableWanted = handle.key
		self._rechecking = True
		try:
			self.refresh(force=True)
		finally:
			self._rechecking = False
		return True

	def _runHasChangedShape(self) -> bool:
		"""Read a run of objects again if the reader has opened or closed something in it.

		Expanding a tree node sends no event and moves no focus, but changes the rows below
		it. Asked of the source, since only its adapter knows what open means.

		:return: whether the band was rebuilt, so the caller stops rather than going on to
			ask a second question about a controller that has just been replaced.
		"""
		source = getattr(self.controller, "source", None)
		if not isinstance(source, flowObjects.ObjectFlowSource):
			return False
		try:
			if not source.shapeChanged():
				return False
		except Exception:
			log.debugWarning("Could not tell whether a run has changed shape", exc_info=True)
			return False
		obj = source.obj
		self._rechecking = True
		try:
			# Dropped, because `showObject` would otherwise take its fast path for the same run.
			self.controller = None
			self.obj = None
			self.showObject(obj, force=True)
		finally:
			self._rechecking = False
		return True

	def _sameFamily(self, first: Any, second: Any) -> bool:
		""":return: whether two documents are a page and something inside that page.

		Which is what the toggle above moves between, and the only disagreement `recheck` is
		entitled to act on.
		"""
		try:
			return getattr(first, "treeInterceptor", None) is second or (
				getattr(second, "treeInterceptor", None) is first
			)
		except Exception:
			log.debugWarning("Could not compare two documents", exc_info=True)
			return False

	def handleFocusRegions(self, regions) -> bool:
		"""Answer a focus change, using the object NVDA built its regions for.

		The regions are the reliable account of where the focus went; asking again can race.

		:param regions: the regions NVDA built for the new focus.
		:return: whether the band dealt with it. False hands them back to NVDA, for a focus
			that cannot be read as a flow.
		"""
		regions = list(regions or ())
		obj = self._objectOf(regions)
		return self.showObject(
			obj if obj is not None else self._target(),
			force=True,
			# **A focus change is a move, not a new reading**, and for a table that is the
			# whole difference. See `showObject`.
			focusMoved=True,
			focusRegions=regions,
		)

	def showObject(
		self,
		obj: Any,
		force: bool = False,
		focusRegions=None,
		focusMoved: bool = False,
	) -> bool:
		"""Show a flow over an object, keeping the current one if it is the same document.

		:param obj: what the reader is now on.
		:param force: rebuild even when the document has not changed.
		:param focusRegions: the regions NVDA built for this focus, so an embedded edit can
			keep its real text region and caret owner.
		:param focusMoved: whether this is the focus arriving somewhere rather than a reading
			that has to be made again. In a spreadsheet or list every arrow key is a focus
			change, so for a table this is not `force`: `isStillHere` decides, and a move
			within the same table follows the cursor instead of rebuilding.
		:return: whether a flow is showing afterwards.
		"""
		segment = self.segment()
		if segment is None:
			return False
		shown = self._showTable(obj, segment, force=force and not focusMoved)
		if shown is not None:
			return shown
		if self._readingATable():
			# The table is finished with. Its controller is dropped first, because its source
			# reads the same tree interceptor and `_isCurrentDocument` would keep it, as in
			# `_runHasChangedShape`.
			self.controller = None
			self.obj = None
			force = True
		if not self.isFlowable(obj):
			# Not something this flow is built for. The band goes back to being an ordinary
			# segment and NVDA presents the focus in it as it always has.
			self.controller = None
			self.obj = None
			segment.detach()
			return False
		if (
			self.controller is not None
			and segment.controller is not self.controller
			and not self._keptControllerFits(segment)
		):
			# The display was rebuilt under a controller that cannot be carried onto it, so the
			# reading is made again, from the cursor. See `_keptControllerFits`.
			self.controller = None
			self.obj = None
			force = True
		if self._isCurrentRun(obj):
			# The same run of objects. In a list the focus changes on every arrow key, so this
			# is the ordinary move rather than a jump: the window tracks the reader by the
			# smallest amount that keeps them on it, and everything already walked is kept.
			# Walking again per keypress would be a call into the application for each item
			# they pass.
			self.obj = obj
			self.controller.source.setCurrent(obj)
			if not self.controller.runStillHoldsTheBand():
				# Something beside them was removed or added, such as a deleted message, so the
				# run is read again, as `_runHasChangedShape` does.
				self.controller = None
				self.obj = None
				return self.showObject(obj, force=True)
			# Before the window is placed, because it is the text of the block being placed:
			# an object is answered for reliably while the reader is on it, and this is that
			# moment. See `FlowController.rereadArrival`.
			self.controller.rereadArrival()
			self.controller.followCursor()
			self._redraw(segment)
			return True
		target = self._resolve(obj)
		if target is not None and self._isCurrentDocument(target):
			# The same document, so the reader has moved within it rather than left it. A
			# focus change is a jump, so the window is placed afresh at the cursor, but the
			# blocks already read and the positions they were read from are still good.
			changedEdit = self._setInteractiveObject(obj, target, focusRegions)
			if not force and self.obj is obj and not changedEdit and segment.controller is self.controller:
				return True
			self.obj = obj
			# Taken whether or not it is acted on, so that a jump the reader made before the
			# setting was turned off cannot ground a later move, and so that a note left by a
			# quick navigation key cannot ground the ordinary Tab press after it.
			jump = flowQuickNav.take()
			ground = bool(jump) and bmConfig.shouldGroundOnQuickNav()
			showing = bool(
				self.controller
				and self.controller.arriveAt(atObject=self._arrival(obj, target), ground=ground)
			)
			self._redraw(segment)
			return showing
		control = buildController(
			obj=obj,
			numRows=segment.rect.numRows,
			numCols=segment.rect.numCols,
			handler=self._handler(),
			# The band is the focus segment, so this is the reader's own place: the focused
			# block shows a cursor, and in a document panning takes that cursor with it. A
			# run of objects is live in the same sense and still moves nothing; see
			# `FlowController.movesCursor`.
			live=True,
			generation=next(_generations),
			atObject=self._arrival(obj, target),
			atRegion=self._textRegionFor(obj, focusRegions),
		)
		if control is None:
			# Nothing here reads as a flow. The band becomes an ordinary segment again and
			# NVDA presents the focus in it as it always has, rather than leaving the reader
			# with a blank display until they find their way back to a document.
			self.controller = None
			self.obj = None
			segment.detach()
			return False
		self.controller = control
		self.obj = obj
		flowQuickNav.forget()
		self._attach(segment, control)
		return True

	def _redraw(self, segment) -> None:
		"""Draw the kept controller, putting it on the band first if the band is a new one.

		A rebuild makes a new segment, which a kept controller is not yet on. The settings are
		asked about again too, since a profile switch may be what rebuilt it. See
		`FlowController.reconfigure` and `_keptControllerFits`.

		:param segment: the band's segment.
		"""
		if self.controller is not None and segment.controller is not self.controller:
			self.controller.caretByDisplay = bmConfig.shouldScrollCaretByDisplay()
			try:
				self.controller.reconfigure(bmConfig.flowIndentStyle(), bmConfig.shouldMarkLineFocus())
			except Exception:
				log.debugWarning("Could not draw a kept flow under the settings now in force", exc_info=True)
			self._attach(segment, self.controller)
			return
		segment.refresh()

	def _keptControllerFits(self, segment) -> bool:
		"""Whether a controller kept across a rebuild can be put on the band that replaced it.

		Not if the band's size or the reading unit changed; the new reading then starts at the
		cursor. Drawing-only changes keep it; see `FlowController.reconfigure`. A table has its
		own test, since its pinned header makes its window a row shorter on purpose.

		:param segment: the rebuilt band's segment.
		"""
		control = self.controller
		if control is None or self._readingATable():
			return True
		try:
			if (control.window.numRows, control.renderer.numCols) != self._bandSize(segment):
				return False
			source = control.source
			if type(source) is DocumentFlowSource and source.unit != readingUnitFor(source.obj):
				return False
		except Exception:
			log.debugWarning("Could not tell whether a kept flow fits the rebuilt band", exc_info=True)
			return False
		return True

	# Unwrapped lines.

	def unwrapLines(self) -> bool:
		"""Whether each line is drawn on one row and the band pans across it.

		The setting, unless the reader has toggled it since; the toggle lasts until the setting
		itself changes, so a profile switch that leaves the setting alone keeps it.

		:return: whether lines are wanted unwrapped.
		"""
		setting = bmConfig.shouldUnwrapLines()
		if self._unwrapTurnedOver is not None:
			wasSetting, wanted = self._unwrapTurnedOver
			if wasSetting == setting:
				return wanted
			self._unwrapTurnedOver = None
		return setting

	def toggleUnwrapLines(self) -> bool:
		"""Turn unwrapped lines over for the time being, and draw the band the new way.

		:return: whether lines are now unwrapped.
		"""
		setting = bmConfig.shouldUnwrapLines()
		wanted = not self.unwrapLines()
		self._unwrapTurnedOver = None if wanted == setting else (setting, wanted)
		if self.controller is not None and self._applyUnwrap(self.controller):
			segment = self.segment()
			if segment is not None:
				segment.refresh()
		return wanted

	def _applyUnwrap(self, control) -> bool:
		""":return: whether the controller changed how it draws lines, to match `unwrapLines`."""
		try:
			return control.setUnwrapped(self.unwrapLines())
		except Exception:
			log.debugWarning("Could not unwrap or wrap the lines on the band", exc_info=True)
			return False

	def panAcross(self, by: int) -> bool:
		"""Move the band across unwrapped lines by its own width, leaving the caret where it is.

		:param by: how many widths, negative for back towards the start of the lines.
		:return: whether the band moved. False when lines wrap, or at either end.
		"""
		if self.controller is None or not self.controller.isUnwrapped:
			return False
		moved = self.controller.panAcross(by)
		if moved:
			segment = self.segment()
			if segment is not None:
				segment.refresh()
		return moved

	def isShowingUnwrapped(self) -> bool:
		""":return: whether the band is claimed and draws its lines unwrapped, whatever is on it.

		Claimed rather than flowing, so that the keys for it do not come and go as the reader
		moves between the editor and a dialog. See `keyLayerContexts`.
		"""
		return self.isClaimed and self.unwrapLines()

	# Tables.

	def layOutTable(self) -> bool:
		"""Read the table the reader is in as columns, if they are in one.

		Which way it failed is left in `tableProblem`, so the reader hears the right reason.

		:return: whether there was a table to lay out.
		"""
		obj = self._target()
		self.tableNotes = []
		try:
			handle = flowTableSource.tableAt(obj)
		except CallCancelled:
			# First, so a cancelled recognition is not reported as no table. See
			# `flow.CallCancelled`.
			self.tableProblem = NOT_READ
			log.debugWarning("Asked for a table in columns and the reading was cancelled")
			return False
		if handle is None:
			self.tableProblem = NO_TABLE
			flowTableSource.logExplanation(obj, "asked for a table in columns and found none")
			return False
		self.tableWanted = handle.key
		self._askedAboutCell = None
		self.refresh(force=True)
		if self.controller is not None and self._readingATable():
			self.tableProblem = None
			return True
		# Which of the two the build hit, in its own words. See `flowBuild.Unreadable`.
		self.tableProblem = (
			NOT_READ
			if any(isinstance(note, Unreadable) for note in self.tableNotes)
			else NO_LAYOUT
		)
		self._reportNoLayout(obj, handle)
		return False

	def _reportNoLayout(self, obj: Any, handle) -> None:
		"""Write down why a table that was recognised did not come out as columns.

		The notes are `buildTableController`'s, naming the step that stopped it. Exceptions are
		contained, so the command's answer is still said.

		:param obj: the object they are on.
		:param handle: the table that was recognised.
		"""
		try:
			notes = self.tableNotes or ["The band never asked for a layout."]
			log.info(
				"\n".join(
					[
						f"BrlMultiline: {handle!r} was recognised and could not be laid out"
						" in columns.",
						*(f"  {note}" for note in notes),
						*flowTableSource.explain(obj),
					],
				),
			)
		except Exception:
			log.debugWarning("Could not say why a table was not laid out", exc_info=True)

	@property
	def tableWanted(self) -> Any:
		""":return: the table the reader asked for in columns. See `TableRequest.wanted`."""
		return self.plugin.tableRequest.wanted

	@tableWanted.setter
	def tableWanted(self, key: Any) -> None:
		self.plugin.tableRequest.wanted = key

	@property
	def tableLayoutInForce(self):
		""":return: the layout the table on the band is being read with, or None.

		A layout appears here when the reader arranges one from the band or the dialog, and it
		is what `script_rememberTableLayout` writes down. See `TableRequest.layout`.
		"""
		return self.plugin.tableRequest.layout

	@tableLayoutInForce.setter
	def tableLayoutInForce(self, layout) -> None:
		self.plugin.tableRequest.layout = layout

	def _forgetTheTable(self) -> None:
		"""Drop the reader's request for this table, and the arrangement that went with it."""
		self.plugin.tableRequest.forget()

	def arrangeColumn(self, column: int, **changes) -> bool:
		"""Change one column of the layout in force, and read the table again.

		Acts on the layout rather than the plan, so what the reader feels is what would be
		saved, and the change survives a rebuild.

		:param column: the table's own number for the column.
		:param changes: the fields of `flowTable.ColumnChoice` to set.
		:return: whether anything was changed.
		"""
		if not self._readingATable() or column < 1:
			return False
		layout = self.tableLayoutInForce or self._layoutBehindTheBand()
		choices = dict(layout.perColumn or {})
		choices[column] = dataclasses.replace(
			choices.get(column, flowTable.ColumnChoice()),
			**changes,
		)
		self.tableLayoutInForce = dataclasses.replace(layout, perColumn=choices)
		self._rebuildTable(keepTheWindow=True)
		return self._readingATable()

	def showTheseColumns(self, columns) -> bool:
		"""Read this table with these columns, in this order, and nothing else.

		:param columns: the table's own numbers, in drawing order.
		:return: whether a table is showing afterwards.
		"""
		layout = self.tableLayoutInForce or self._layoutBehindTheBand()
		return self.arrangeTable(dataclasses.replace(layout, columns=tuple(columns)))

	def arrangeTable(self, layout) -> bool:
		"""Read this table with a whole layout, as the dialog hands one over.

		:param layout: what to read it with, or None to go back to how it reads by itself.
		:return: whether a table is showing afterwards.
		"""
		if not self._readingATable():
			return False
		self.tableLayoutInForce = layout
		self._rebuildTable(keepTheWindow=True)
		return self._readingATable()

	def _layoutBehindTheBand(self):
		""":return: the layout this table would be read with if nobody arranged one.

		The saved record where there is one, so that arranging a column of a remembered table
		changes that column rather than throwing the rest of the record away.
		"""
		handle = self._tableOnTheBand()
		saved = flowTableLayouts.layoutFor(handle) if handle is not None else None
		return saved if saved is not None else flowTableLayouts.TableLayout()

	def _tableOnTheBand(self):
		""":return: the table the band is reading, as its source knows it, or None."""
		source = getattr(self.controller, "source", None)
		return getattr(source, "handle", None)

	def wantsColumnsFor(self, obj: Any) -> bool:
		""":return: whether the reader has asked for the table this object is in as columns.

		Asked by a pin as it is made. The band's request ends when the reader leaves the table,
		but a pin's layout outlives that, so the pin copies the answer.

		:param obj: the object being pinned.
		"""
		return flowTableSource.wantsColumns(self.tableWanted, obj)

	def clearTable(self) -> bool:
		"""Go back to reading the table the way the page around it is read.

		:return: whether anything was being laid out.
		"""
		if self.tableWanted is None:
			return False
		self._forgetTheTable()
		self._askedAboutCell = None
		self._cancelLiveRead()
		self.refresh(force=True)
		return True

	def _recheckTable(self) -> None:
		"""Follow the caret through the table, and give the layout back when it leaves.

		The only place that can notice: in browse mode the caret moves without a focus change,
		and a table's rows own no caret. Costs one `_getTableCellCoords` per redraw; nothing
		cheaper changes when the caret leaves a table.
		"""
		source = self.controller.source
		found = flowTableSource.tableAt(self._target())
		if found is None or not flowTableSource.sameTable(found.key, self.tableWanted):
			# Out of the table. The request goes with it, so that walking into a different
			# table later does not lay that one out uninvited.
			self._forgetTheTable()
			self._rebuildTable()
			return
		if self._tableChangedShape(found, source):
			# The table changed shape, so it is read again. A caret that moved is followed;
			# otherwise the reader keeps their page and rows. See `_whereTheyAreReading`.
			self._rebuildTable(keepTheWindow=(found.row, found.col) == (source.row, source.column))
			return
		if (found.row, found.col) == (source.row, source.column):
			return
		# The column as well as the row. Moving along a row is a move the reader makes
		# constantly — it is what the arrow keys do inside a table — and it is what decides
		# which cell shows the cursor. Watching only the row left the cursor on the cell they
		# entered the row at for as long as they stayed in it.
		source.moveTo(found)
		self._rechecking = True
		try:
			self._showColumn(found.col)
			self.controller.followCursor()
			segment = self.segment()
			if segment is not None:
				segment.refresh()
		finally:
			self._rechecking = False

	def _rebuildTable(self, keepTheWindow: bool = False) -> None:
		"""Read whatever is here now, the ordinary way. Guarded, since this runs in a redraw.

		:param keepTheWindow: whether to put the band back on the page and rows it was showing.
			False follows the caret, which is right when the caret moving is what set this off.
		"""
		self._rechecking = True
		self._readAgainAt = self._whereTheyAreReading() if keepTheWindow else None
		try:
			self.refresh(force=True)
		except Exception:
			log.debugWarning("Could not give the table's band back", exc_info=True)
		finally:
			self._readAgainAt = None
			self._rechecking = False

	def _whereTheyAreReading(self, andTheRows: bool = True):
		""":return: the page of columns and the top row the band is showing, or None.

		What a rebuild the reader did not ask for puts back, before anything is written, so the
		display is written once.

		:param andTheRows: whether the top row is theirs to keep. False keeps only the page of
			columns, for a band holding rows the table has stopped showing.
		"""
		plan = self.columnPlan()
		if plan is None:
			return None
		if not andTheRows:
			return (plan.at, None)
		top = self.controller.window.topBlockId() if self.controller is not None else None
		row = getattr(top, "bookmark", None) if top is not None else None
		return (plan.at, row if isinstance(row, int) else None)

	def _tableChangedShape(self, found, source) -> bool:
		"""Whether the table is no longer the shape the layout was made for.

		Three cheap questions per redraw: the column count, the row count where that is a fact,
		and whether the plan knows the caret's column. Known, not drawn: a measured column left
		out is still known. See `ColumnPlan.knows`. Renamed or reordered columns are not caught;
		turning the layout off and on measures again.

		:param found: the table as it is now.
		:param source: the source reading it.
		:return: whether to build the layout again.
		"""
		if found.numCols != source.handle.numCols:
			self._rebuildBecause(
				f"the table has {found.numCols} columns and the layout was made for "
				f"{source.handle.numCols}",
			)
			return True
		if getattr(found.document, "rowCountIsExact", False):
			# Only where the count is a fact, as for a sheet, and the content rows rather than
			# `numRows`, which moves with the reader. See `flowObjectTable.SheetTable.contentRows`.
			now = found.contentRows or found.numRows
			before = source.handle.contentRows or source.handle.numRows
			if now != before:
				self._rebuildBecause(
					f"the table has content in {now} rows and the layout was made for {before}",
				)
				return True
		plan = getattr(self.controller.renderer, "columnPlan", None)
		if plan is None or plan.isEmpty:
			return False
		if plan.holds(found.col):
			return False
		if found.col in plan.excluded:
			# A column the reader's saved layout leaves out: nothing to rebuild.
			return False
		if found.col not in plan.omitted:
			# A column the plan never measured. That is a table this layout is not of.
			self._rebuildBecause(f"column {found.col} is not one this layout was made from")
			return True
		if (self.tableWanted, found.row, found.col) == self._askedAboutCell:
			# Asked about this cell already, and the answer stands, so each cell is searched
			# once. A value appearing while the reader stands there is missed until they move.
			return False
		self._askedAboutCell = (self.tableWanted, found.row, found.col)
		if not self._omittedColumnHasContent(found):
			return False
		self._rebuildBecause(
			f"column {found.col} was measured as empty and row {found.row} of it is not",
			# The reader is standing in that cell. Leaving the band where it was would refuse
			# them the very thing that made the rebuild worth doing.
			keepTheWindow=False,
		)
		return True

	def _rebuildBecause(self, why: str, keepTheWindow: bool = True) -> None:
		"""Say why the layout is being made again, for the log.

		Every rebuild is a normal branch that would otherwise log nothing. Logs the reason, the
		table's shape, the caret and what the band showed, once per rebuild.

		:param why: which branch decided it, in the plainest words available.
		:param keepTheWindow: whether the reader is left where they were reading. False for a
			rebuild they caused by standing somewhere, which is theirs to be shown.
		"""
		self._keepOnRebuild = keepTheWindow
		try:
			plan = self.columnPlan()
			source = getattr(self.controller, "source", None)
			handle = getattr(source, "handle", None)
			log.debug(
				"BrlMultiline table: reading the layout again because "
				f"{why}. Table {handle!r}, caret at row {getattr(source, 'row', '?')} "
				f"column {getattr(source, 'column', '?')}, showing columns "
				f"{plan.whereItIs if plan is not None else '?'}, columns left out "
				f"{plan.omitted if plan is not None else '?'}, excluded by the reader "
				f"{plan.excluded if plan is not None else '?'}.",
			)
		except Exception:
			log.debugWarning("Could not say why a table was read again", exc_info=True)

	def _omittedColumnHasContent(self, found) -> bool:
		"""Whether a column the layout left out turns out to hold something after all.

		`measure` reads a sample, so a column empty in it is only tentatively empty. Asking about
		the one cell the reader arrived at costs one search, and a column nobody visits does not
		need drawing.

		:param found: the table, at the cell the caret has moved to.
		:return: whether the layout should be made again.
		"""
		try:
			return flowTableSource.cellHasContent(found, found.row, found.col)
		except Exception:
			log.debugWarning("Could not look into a column the layout left out", exc_info=True)
			return False

	def _showColumn(self, column: int) -> bool:
		"""Bring the page holding a column onto the band, unless it is already on it.

		The column counterpart of what the window does for rows: the display may travel away
		from the caret, and a caret that moves somewhere not shown brings it back. Asks whether
		the caret's cell is on the display, not whether this is its column's own page, since a
		pinned key column is drawn on every page.

		:param column: the table's own number for the column to show.
		:return: whether the page changed.
		"""
		plan = getattr(self.controller.renderer, "columnPlan", None)
		if plan is None or plan.isEmpty:
			return False
		if plan.showing(column):
			# Under their hand already. See `ColumnPlan.showing`, which counts the repeated
			# key column as the column it is a copy of.
			return False
		# By the least movement that reaches it, as the window does with rows.
		start = plan.startShowing(column)
		if start == plan.at:
			return False
		return self._useColumnPage(plan.scrolledTo(start))

	def turnColumnPage(self, by: int) -> bool:
		"""Move the band across the table by pages of columns, without moving the caret.

		As panning is for rows. The page stays while the caret's cell is drawn on it, and a
		move to a column not shown is followed. See `_showColumn`.

		:param by: how many pages to move, negative for back towards the first column.
		:return: whether the page changed.
		"""
		if not self._readingATable():
			return False
		plan = getattr(self.controller.renderer, "columnPlan", None)
		if plan is None or plan.isEmpty:
			return False
		moved = self._useColumnPage(plan.turnedBy(by))
		if moved:
			segment = self.segment()
			if segment is not None:
				segment.refresh()
		return moved

	def _useColumnPage(self, plan) -> bool:
		"""Show a page of columns, and read the rows again for it.

		Together and in this order, since the source reads only the page's columns. See
		`FlowController.useColumnPage`.

		:param plan: the layout, on the page wanted.
		:return: whether the page changed.
		"""
		return self.controller.useColumnPage(plan)

	def columnPlan(self):
		""":return: the table layout on the band, or None if it is not showing a table."""
		if not self._readingATable():
			return None
		return getattr(self.controller.renderer, "columnPlan", None)

	def _readingATable(self) -> bool:
		""":return: whether the band is showing a table laid out in columns.

		Asked of the source, because the controller can outlast the request until it is replaced.
		"""
		source = getattr(self.controller, "source", None)
		return isinstance(source, flowTableSource.TableFlowSource)

	def _showTable(self, obj: Any, segment, force: bool = False) -> Optional[bool]:
		"""Show the reader's table, and say when NVDA stopped waiting rather than pretending.

		The cancellation boundary covers recognition and the saved-layout lookup as well as the
		build, since all read the document. A cancelled read leaves a note, so the command says
		the table could not be read rather than that there is none.

		See `_showTheTable`, which is the work, and `flow.CallCancelled`.
		"""
		try:
			return self._showTheTable(obj, segment, force=force)
		except CallCancelled:
			if self.tableNotes is None:
				self.tableNotes = []
			self.tableNotes.append(
				Unreadable(
					"NVDA cancelled the reads while it was working out which table this is, "
					"because the core had stopped answering.",
				),
			)
			log.debugWarning("A table reading was cancelled while the table was recognised")
			return None

	def _showTheTable(self, obj: Any, segment, force: bool = False) -> Optional[bool]:
		"""Show the reader's table in columns, or say that this is not the moment to.

		None means "not a table for this path", where False would mean a blank band. The layout
		is dropped as soon as the reader leaves the table.

		:param obj: what the reader is now on.
		:param segment: the band's segment.
		:param force: rebuild even when the table has not changed.
		:return: whether a flow is showing, or None if this is not a table to lay out.
		"""
		# Found once and passed on, since each is a read of the document.
		handle, layout = (None, None)
		readAgainAt = self._readAgainAt
		"""Where to put the band once it is built: what a rebuild asked for, or, where the
		table has changed under the reader below, the page of columns they had."""
		if self.tableWanted is None:
			handle, layout = self._savedLayoutHere(obj)
			if handle is None:
				return None
			self.tableWanted = handle.key
		if handle is None:
			handle = flowTableSource.tableAt(obj)
		if handle is None or not flowTableSource.sameTable(handle.key, self.tableWanted):
			self._forgetTheTable()
			return None
		if not force and self._readingATable() and self.controller.source.stillReading(handle):
			# The same table: the caret moved between its cells. The handle in hand answers
			# both questions below without another lookup.
			self.obj = obj
			self.controller.source.moveTo(handle)
			if self.controller.tableStillHoldsTheBand():
				# Both axes, because in a spreadsheet or list this is the only place that hears
				# the move; `_recheckTable` does the same for browse mode.
				self._showColumn(handle.col)
				self.controller.followCursor()
				self._redraw(segment)
				return True
			# The table stopped showing a held row, as when filtered, so the reading is made
			# again below. Falls through rather than calling `_rebuildTable`, which would ask
			# for a redraw inside this one.
			readAgainAt = self._whereTheyAreReading(andTheRows=False)
		numRows, numCols = self._bandSize(segment)
		# Kept rather than discarded, because the step that stopped a build is the whole of
		# what the reader needs when they are told the columns did not happen. See
		# `_reportNoLayout`.
		self.tableNotes = []
		control = buildTableController(
			obj=obj,
			numRows=numRows,
			numCols=numCols,
			handler=self._handler(),
			live=True,
			generation=next(_generations),
			notes=self.tableNotes,
			handle=handle,
			# What the reader has arranged for this table, then what they saved for it, then
			# nothing — which is the measurement and the settings, as for any other table.
			layout=self._layoutToReadWith(handle, layout),
			# Where the reader had the band, when this is a rebuild rather than an arrival.
			# Applied inside the build, so what reaches the display is their own window and
			# not page one first. See `_whereTheyAreReading`.
			atPage=readAgainAt[0] if readAgainAt else 0,
			atRow=readAgainAt[1] if readAgainAt else None,
		)
		if control is None:
			# Recognised a moment ago and not now, or no column layout fits this band. Reading
			# order is a good answer to both, so the request is dropped rather than held on to
			# in the hope that the next redraw goes better.
			self._forgetTheTable()
			return None
		self.controller = control
		self.obj = obj
		self._attach(segment, control)
		return True

	def _layoutToReadWith(self, handle, offered):
		""":return: the layout to read this table with, in the order the reader means them.

		:param handle: the table.
		:param offered: the saved layout the caller has already looked up, or None.
		"""
		inForce = self.tableLayoutInForce
		if inForce is not None:
			return inForce
		return offered if offered is not None else flowTableLayouts.layoutFor(handle)

	def _savedLayoutHere(self, obj: Any, follow: bool = True):
		""":return: the table the reader has already laid out and what they saved for it.

		A table the store knows becomes the table asked for, as the command would make it;
		`clearTable` takes it away without touching what was saved. An empty store answers
		without reading the object. Both are returned, since the caller needs them next.

		:param obj: what the reader is now on.
		:param follow: whether to follow the saved columns to where they are now, which reads their
			headings. See `flowTableLayouts.layoutFor`.
		:return: the table and its layout, or (None, None) where there is nothing saved for
			what the reader is in.
		"""
		if not flowTableLayouts.stored() or self._refusedTable(obj):
			return (None, None)
		handle = flowTableSource.tableAt(obj)
		if handle is None:
			return (None, None)
		layout = flowTableLayouts.layoutFor(handle, follow=follow)
		# A layout saved for how the table's headers are spoken asks for nothing on the display.
		# See `flowTableLayouts.TableLayout.speechOnly`.
		return (handle, layout) if layout is not None and layout.laysOut else (None, None)

	def _refusedTable(self, obj: Any) -> bool:
		""":return: whether the reader has just taken this table's layout away.

		See `TableRequest.refused`.
		"""
		request = self.plugin.tableRequest
		refused = request.refused
		if refused is None:
			return False
		try:
			here = flowTableSource.wantsColumns(refused, obj)
		except Exception:
			log.debugWarning("Could not tell whether a table was refused", exc_info=True)
			return False
		if not here:
			# Somewhere else, so the refusal is spent. It was about the table they were in.
			request.refused = None
		return here

	def _setInteractiveObject(self, obj: Any, target: Any, regions) -> bool:
		"""Give an embedded edit's active block its real caret-owning region."""
		source = getattr(self.controller, "source", None)
		if not isinstance(source, DocumentFlowSource):
			return False
		factory = interactiveRegionFactory(
			target,
			obj,
			live=True,
			template=self._textRegionFor(obj, regions),
		)
		return source.setInteractiveObject(obj if factory is not None else None, factory)

	def _textRegionFor(self, obj: Any, regions):
		""":return: the text region NVDA built for an object, or None."""
		if obj is None:
			return None
		try:
			from braille.regions.textInfo import TextInfoRegion

			for region in reversed(list(regions or ())):
				if not isinstance(region, TextInfoRegion):
					continue
				regionObj = getattr(region, "obj", None)
				if regionObj is obj or bool(regionObj == obj):
					return region
		except Exception:
			log.debugWarning("Could not find the focused edit's text region", exc_info=True)
		return None

	def _arrival(self, obj: Any, target: Any) -> Any:
		"""What the reader arrived at, to be read at its own place in the document.

		The focus event can arrive before the browse mode cursor catches up, so the object's
		own place is more reliable. Whether it is a control is decided by `flowForms`, not
		here. Taken from the focus object, since the regions name only the document, and
		ignored if the focus has moved outside it.

		:param obj: what the focus regions were built for.
		:param target: the document being read.
		:return: the object to read at, or None to read from the cursor as ever.
		"""
		if self._isIn(obj, target):
			return obj
		focus = self._focusObject()
		if self._isIn(focus, target):
			return focus
		# Somewhere else by now. Reading at it would put the reader in another document.
		return None

	def _isIn(self, obj: Any, target: Any) -> bool:
		""":return: whether an object is something *inside* the document being read.

		Excludes the document's own `rootNVDAObject`, whose place is the start of the page and
		which is the focus object whenever the reader is not on a control. See `_arrival`.
		"""
		if obj is None or obj is target:
			return False
		if obj is getattr(target, "rootNVDAObject", None):
			return False
		try:
			return getattr(obj, "treeInterceptor", None) is target
		except Exception:
			log.debugWarning("Could not tell which document an object is in", exc_info=True)
			return False

	def _focusObject(self) -> Any:
		""":return: where the system focus is, or None if it cannot be read."""
		try:
			return api.getFocusObject()
		except Exception:
			log.debugWarning("Could not read the focus object", exc_info=True)
			return None

	def isFlowable(self, obj: Any) -> bool:
		"""Whether a flow is the right way to present this object.

		Two kinds, each with its own setting:

		1. A browse mode document: the object has a tree interceptor, or is one, even while a
		   form field inside it has focus.
		2. A run of objects, such as a list or menu, with no document behind it. See
		   `flowObjects`.

		Anything else, such as a plain edit control, is left to NVDA.

		:param obj: what the reader is on.
		:return: whether to read it as a flow.
		"""
		if obj is None:
			return False
		try:
			if getattr(obj, "treeInterceptor", None) is not None or isTreeInterceptor(obj):
				return self._enabledFor("browseMode", obj)
			if flowForms.isEditableObject(obj):
				return self._enabledFor("editableText", obj)
			if not self._enabledFor("objects", obj):
				return False
			if objectAdapterFor(obj) is not None:
				return True
			log.debug(f"BrlMultiline flow: no adapter reads {describeObject(obj)} as a run")
			return False
		except Exception:
			log.debugWarning("Could not tell whether this can be flowed", exc_info=True)
			return False

	def _enabledFor(self, mode: str, obj: Any) -> bool:
		"""Whether one kind of content flows, saying in the log when it does not.

		Logged so a setting that is off can be told from an object of another kind.

		:param mode: the kind of content, as `bmConfig.FLOW_MODES` names it.
		:param obj: what was being judged, for the log.
		:return: whether it flows.
		"""
		if bmConfig.isFlowEnabledFor(mode):
			return True
		log.debug(f"BrlMultiline flow: {mode} is off, so {describeObject(obj)} is left to NVDA")
		return False

	def _isCurrentRun(self, obj: Any) -> bool:
		""":return: whether a flow of objects is showing the run this object belongs to."""
		source = getattr(self.controller, "source", None)
		if not isinstance(source, flowObjects.ObjectFlowSource):
			return False
		return flowObjects.belongsTo(source, obj)

	def _isCurrentDocument(self, target: Any) -> bool:
		""":return: whether a resolved target is the one the current flow is reading."""
		return self.controller is not None and self.controller.source.obj is target

	def _resolve(self, obj: Any) -> Any:
		""":return: what would actually be read for an object, or None.

		The focus regions name only the page's tree interceptor, so a control read as a document
		of its own, such as a multi line edit being written in, is asked for from the focus. See
		`flowSources.documentFor`.
		"""
		if obj is None:
			return None
		try:
			target = documentFor(obj)
		except Exception:
			log.debugWarning("Could not resolve what to flow", exc_info=True)
			return None
		focus = self._focusObject()
		if focus is None or focus is obj or getattr(focus, "treeInterceptor", None) is not target:
			return target
		try:
			return focus if documentFor(focus) is focus else target
		except Exception:
			log.debugWarning("Could not resolve what the focus would flow", exc_info=True)
			return target

	def _objectOf(self, regions) -> Any:
		"""Find what NVDA built a set of focus regions for.

		The last region is the one over the text — a browse mode document's tree
		interceptor, or the object itself — which is the same thing `documentFor` would
		arrive at from the focus.

		:param regions: the regions NVDA built.
		:return: the object they are over, or None.
		"""
		for region in reversed(list(regions or ())):
			obj = getattr(region, "obj", None)
			if obj is not None:
				return obj
		return None

	def segment(self) -> Optional[FlowBufferSegment]:
		""":return: the band's segment, or None if the claim is not on the display.

		While an app layout whose focus segment asked for the flow is up, that segment is the
		band's. The band's own claim is outranked meanwhile and comes back when the layout goes,
		and the controller, which is where the reader's place is, carries over both ways.
		"""
		container = self._container()
		if container is None:
			return None
		appLayouts = getattr(self.plugin, "appLayouts", None)
		key = (appLayouts.flowSegmentKey() if appLayouts is not None else None) or BAND_NAME
		try:
			segment = container.segmentForKey(key)
		except LookupError:
			return None
		return segment if isinstance(segment, FlowBufferSegment) else None

	# The lifecycle the claim contract was missing.

	def onRebuilt(self, keys: frozenset[str] = frozenset()) -> None:
		"""The display was rebuilt, so the band came back empty and wants drawing again."""
		# A rebuild builds new segments, so the new one has to be told where its focus
		# changes go whether or not anything is flowing now. Otherwise a band rebuilt while
		# focus is outside browse mode never hears that the reader came back to a document.
		self._follow()
		self.refresh(force=True)

	def onSuspended(self) -> None:
		"""The band's rows have gone to a claim that outranks it, for as long as that lasts.

		Unlike eviction, the controller stays, and with it the reader's place. Pending passes
		are cancelled, since there is no segment to draw into; the reader's next action after
		the band returns starts them again.
		"""
		self._cancelSettle()
		self._cancelLiveRead()
		self._cancelFill()

	def onEvicted(self, keys: frozenset[str] = frozenset()) -> None:
		"""The claim no longer fits, so there is nothing to draw into."""
		log.debug("The flow band was evicted")
		self.controller = None
		self.obj = None

	def onTerminate(self) -> None:
		"""Everything is being taken back."""
		flowQuickNav.remove()
		self.controller = None
		self.obj = None

	# Where things are.

	def _target(self):
		""":return: the object to read, which is where the focus is.

		The band is the focus segment. The navigator object is the fallback when there is no
		focus.
		"""
		try:
			obj = api.getFocusObject()
		except Exception:
			obj = None
		return obj if obj is not None else api.getNavigatorObject()

	def _container(self) -> "Optional[DisplayContainer]":
		return getattr(self.plugin, "container", None)

	def _handler(self):
		import braille

		return braille.handler

	def _displaySize(self) -> tuple[int, int]:
		""":return: the size of the display being claimed against.

		The container's, not the handler's: the container is what the claim is composed
		over, and it is the thing that knows the shape it was built for.
		"""
		container = self._container()
		if container is not None:
			return container.numRows, container.numCols
		return bandSize(self._handler())

	def _bandSize(self, segment: FlowBufferSegment) -> tuple[int, int]:
		""":return: the band's own size, which is what the flow is laid out for."""
		return segment.rect.numRows, segment.rect.numCols

	def __repr__(self) -> str:
		state = "showing" if self.isShowing else "empty"
		return f"<FlowBand {state} reading {self.obj!r}>"
