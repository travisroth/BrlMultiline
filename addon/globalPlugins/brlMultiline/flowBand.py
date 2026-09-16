# BrlMultiline: putting a flow on the display.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Claiming a band of the display for a flow, and keeping it fed.

The pieces below this each do one thing: `flowSources` finds blocks, `flowRender` lays
one out, `flow` packs them into rows and moves a window, `flowControl` drives those three,
and `flowSegment` shows the result. This is what puts them on a display: it claims the
band, builds a controller over whatever the reader is in, and hands the two to each other.

It is also the owner of the claim, so it is where the lifecycle the claim contract was
missing lives — being told that the display was rebuilt, that the claim was evicted, and
that everything is being taken back. Geometry survives a rebuild and content does not, so
without `onRebuilt` a flow comes back as an empty band and stays that way.

The band is as much of one physical display as there is. Not the whole composite: a
Monarch and a Focus 80 driven as one display are nine rows of eighty cells, of which the
Monarch's eight rows have only thirty-two — and a claim across the whole rectangle reaches
forty-eight columns of dead cells that no hardware has, which `validateAgainstHardware`
refuses outright. Choosing the band is therefore a question about the reader's hardware,
answered in `bandRect` below, and a band of some of one display's rows is the same code
with a smaller rectangle once there is a setting to name it.
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
)
from .devices import DeviceInfo, deviceMap, preferredDevice
from .flowSegment import FlowBufferSegment
from .layout import SegmentRect, wholeDisplayRect
from .views import focusDisplayDriver
from .flowSources import DocumentFlowSource, documentFor
from .panels import FlowPanel, PanelOwner

FILL_AGAIN_MILLIS = 120
"""How long after a fill that ran out of budget the band tries again.

The budget exists so that one keypress cannot block the reader for a second on a heavy page,
and 250 milliseconds of Word buys about five blocks where the band wants eight. Left there,
the band showed two rows saying "more, not fetched" and kept showing them: the reader was
told the content existed and given no way to reach it.

So the answer to running out is to come back, not to raise the ceiling. Each pass gets a fresh
allowance, the display is written only when a pass added something, and the chain stops the
moment one adds nothing — which is what keeps a document that genuinely will not answer from
being asked forever.

Short enough that the rows fill in while the hand is still moving, long enough to leave the
reader's own keystrokes ahead of it in the queue.
"""

MIN_BAND_ROWS = 2
"""The fewest rows a display must have before a flow is worth claiming it.

Two. Everything a flow is for needs a second row to exist at all — the shape of a document
under the hand, a table's columns lining up, a run of items to scan down — and on one row it
takes a display where NVDA was already doing the same job and adds two things that are wrong
there. The focus mark is drawn at the left of the focused item, which on a single line is
every line. And the indent is spent on depth the reader cannot see the shape of.

The reader had this by accident until the band began following the focus: the band took the
tallest display, so it never landed on a single line one. It does now, and a one row display
is a display NVDA should be left to.

The same number as `objectMonitor.MIN_FLOW_ROWS`, and for the same reason, kept apart because
a band and a pin are claimed by different code and either could reasonably change.
"""

LIVE_SETTLE_MILLIS = 250
"""How long the band waits after a document change before reading the table again.

Not a poll. Nothing happens until NVDA says the browse mode document changed — see
`patches._handleUpdateTellingTheBand`, and the chain behind it, which is driven by ordinary
accessibility events and not by ARIA. This is only how long the band lets the news settle:
a page redrawing a table sends an event per region it touched, and a watchlist repricing
thirty rows would otherwise be thirty reads of the same band.

A quarter of a second, which is under what a hand notices and far over what a burst takes.
"""

LIVE_POLL_MILLIS = 2000
"""How often a table is read again when nothing is going to tell the band it changed.

The fallback, and only that: it is used when the document-change patch is not installed. See
`bmConfig.liveReadSeconds` for the reader's own number, which overrides it either way.
"""

SETTLE_MILLIS = 150
"""How long after a keystroke into an edit the band comes back for a second look.

Long enough for the editor to finish answering — the transients the probe caught were gone
by the next keystroke, half a second later, and are an artefact of the instant itself — and
short enough that a reader pausing to read the display never meets them. Restarted on every
keystroke, so a steady typist pays for one settle pass per pause rather than one per key.
"""

SETTLE_LATEST_MILLIS = 600
"""The longest a settle pass may be put off by the typing it is waiting for.

**Both rules are needed, and each without the other is a bug this add-on has had.** Restart
the pass on every keystroke and a reader who types faster than the delay never reaches the
pause it waits for, so the repair never runs — and a steady typist in a rich editor is
exactly who needs it. Refuse to restart it at all and the pass fires in the middle of the
burst, which is the one moment the editor's answers are transiently wrong: it reads the
garbage it exists to clear and shows the reader that instead.

So a keystroke pushes the pass out, but never past this. A typist gets a settle within a
keystroke or two of pausing, and at worst one mid-burst pass every this often — the price of
not waiting for a pause that may not come."""


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

A different thing to be told and, until it had a name, the same sentence: a table recognised
and then dropped somewhere further down said "not in a table", which sends the reader to look
at whether the control is a table at all — the one part that had worked.
"""

NOT_READ = "unread"
"""They are in one, and nothing could be read out of it.

The third of the same family, and the same argument a step further along. "Could not be laid
out in columns" is about arithmetic — these columns, this band, no arrangement fits — and it
sent a reader to the layout designer for an Excel sheet where every read had been cancelled
before a single width was measured. Nothing was wrong with the arrangement, because there had
been nothing to arrange. See `flowBuild.Unreadable`, which is how the build says so.
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
		"""The table and undrawn cell the band last asked about, so it is asked once.

		The table as well as the cell, because two tables have a row 1 column 1 apiece and a
		review found the second inheriting the first's answer."""

		"""The table the reader has asked to see in columns lives on the plugin.

		It was this band's, and a one row display moved it: there the band is not claimed at
		all — see `MIN_BAND_ROWS` — and the reader still wants to be able to say "this table,
		in columns" so that pinning it to a display with room carries the layout. A request
		that lived on the band could not be made when there was no band, which is exactly the
		arrangement the reader described wanting.

		None means every table reads as the page around it does, which is reading order and is
		the default. See `layOutTable`, and decision 19 of the structured presentation plan:
		the end state is a layout remembered against a table, and this is one step nearer it.
		"""

	# The claim.

	def start(self) -> bool:
		"""Claim the band, and show a flow in it if there is one to show.

		The claim is kept for as long as the reader wants a flow, whether or not there is
		anything to read as one at this moment. A band with nothing to flow presents the
		focus exactly as an undivided display would, and lights up by itself when the
		reader reaches a document — which is the difference between a feature and a command
		that has to be pressed again after every dialog.

		Which is not to say the claim is free: it takes rows the segment layout would
		otherwise fill. That is what the setting decides, and what a configuration profile
		makes conditional on the application in front.

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

		A flow band must lie inside one physical display's live cells. On an ordinary
		display that is the whole thing. On a composite it is one member's band — the one
		the reader named, or else **the one the focus is on** — because a claim across both
		would reach the dead columns beside the narrower one.

		Following the focus rather than taking the tallest, and the reader named the reason:
		with the flow on one display and the focus moved to the other, the flow's display
		could not be used for anything else. Nothing could be pinned to it, because the band
		owned it; and the band was not showing what the reader was working in, because the
		focus had left. Two displays and neither of them useful. Where the focus is, is where
		the reading is, so that is where the band goes, and moving the focus moves it — the
		command that moves the focus rebuilds the display, and the rebuild compares this
		rectangle against the claim in force.

		Naming a display is still the escape hatch, and it now means what it says: pin the
		flow here whatever the focus does.

		The band is as many rows of that display as the reader asked for, counted from its
		top, so that the rows below it keep whatever the segment layout puts there. Asking
		for none, which is the default, means all of them. A count larger than the display
		has is trimmed rather than refused: a setting outliving the hardware it was typed
		for is ordinary, and a reader who plugs in a smaller display wants their flow, not
		an error.

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

		The reader's own answer first. Failing that the display the focus is on, and failing
		that nothing, which leaves `preferredDevice` to take the tallest — the right fallback
		for a display whose segments cannot be numbered, since rows are what a flow spends.

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

		A rich editor's answers at the instant of a keystroke can be transiently wrong: the
		probe caught a plain textarea answering the paragraph at the caret, the moment
		return was pressed, as everything from the start of the document — so the band
		showed a merged block and a duplicate under it. Every such state heals on the next
		re-read, which used to arrive only with the next keystroke; a reader who pauses
		right after pressing return is reading the display, and that is exactly the moment
		the garbage sat under their fingers.

		**A keystroke puts the pass off, but only so far.** Restarting it on every keystroke
		is right for a reader who pauses — the pass then lands on an editor that has finished
		answering — and it is a trap for one who does not: typing faster than the delay pushed
		the repair out again on every key and it never ran at all. Refusing to restart it is
		the opposite trap, since the pass then fires mid-burst and reads the very transient it
		exists to clear. So the pass is pushed out while the burst is young, and left alone
		once it has been waiting `SETTLE_LATEST_MILLIS`.

		The pass itself redraws only when it changed something, and scheduling happens only
		from a display update, so a settle that changes nothing ends the exchange rather than
		perpetuating it.
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

		**Whether the display is written and whether NVDA is pointed at the right region are
		two questions.** They were one, and answering them together was the bug the reader
		reported as a letter arriving only when the next one was typed: a settle re-reads the
		band, which rebuilds its blocks and so replaces the region the active block is read
		through, and a settle that found the cells unchanged returned without telling the
		segment. NVDA went on holding the retired region, queued *that* for its next caret
		move, re-read it, and handed the band a replacement region with nothing dirty on it.
		The keystroke went nowhere, and the one after it healed the band by arriving after
		the lists had been put back in step. So the reconciliation happens on every path out
		of here, and only the writing to hardware is conditional.
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
				# The band has settled, and the claim this pass made on the way must not
				# outlive it. Reading again while writing arms `rereadWhileWriting` for the
				# segment to turn into a settle pass, and the segment only ever sees it when
				# the band is redrawn — which is exactly what is not about to happen here. Left
				# armed, it is found by whatever updates the display next, for its own
				# unrelated reason, and spends a band's worth of reads settling something that
				# settled long ago.
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

		The signal this used to guess at with a clock. It arrives for any accessibility event
		the buffer backend acted on — text inserted, removed or updated, a name, a value, a
		state, a reorder — so a page that says nothing costs nothing at all, and a repricing
		watchlist is heard within a settle delay rather than within two seconds.

		**Any document the band is reading, in columns or not.** The reader found the reason
		on the same watchlist: read as a table the prices moved and read as ordinary browse
		mode they sat still, and there is nothing about a table that makes it the dynamic one.
		NVDA refreshes the caret's line and nothing else because the caret's line is all it is
		showing; a band showing eight is showing seven that nobody is refreshing.

		Bounded twice over regardless. Only the document this band is reading is answered at
		all, and the pass that answers it reads only the blocks on the band — for a table, only
		at the columns of the page being drawn. A change three screens down the page costs the
		read of what the reader is touching, which is the read that was happening anyway.

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

		A source that answers `blockAt` — a table by its row number, a document by the
		position each block was read from. An object flow answers nothing, because an object
		run has no such thing as "the same block, read again": the objects themselves are the
		identity, and NVDA's own events are what say when one of them changed.

		Not a document being written in either, and for the same reason `_rereadBlocks`
		refuses one: every position moves on every keystroke, so the pass can never bring
		anything back. It was still being scheduled — a markdown file open in VSCode is a
		plain editable text with no change notices behind it, so it took the fallback poll
		and ran a hundred and eighteen times in one sitting, each of them re-rendering the
		whole band to arrive at the same cells.
		"""
		source = getattr(self.controller, "source", None)
		if source is None or getattr(source, "blockAt", None) is None:
			return False
		return not getattr(source, "writing", False)

	def _pollMillis(self) -> int:
		""":return: how long to wait for a pass nothing has asked for, or zero for none.

		Zero whenever something is going to tell the band instead, which is the point of the
		whole exercise: a table nobody is changing should cost nothing between the reader's
		own keystrokes. The reader's own number wins over both.
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

		Every path that shows something goes through here, which is the point: the table path
		started the chain and the ordinary document path did not, so a reader with a refresh
		interval set got one on a watchlist laid out in columns and none on the same page read
		by line. The promised fallback for a build without the change patch went the same way.

		:param segment: the band's segment.
		:param control: the controller to show.
		"""
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

		Modelled on `_settle`, and for the same reason: what decides whether the display is
		written is whether the cells came out different, not whether the read happened. A
		display rewritten with identical content under a reading hand is a display that
		flickers for nothing.
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

		`rereadContent` walks the window's blocks and the pinned row is deliberately not one
		of them, so a header renamed while the reader watched went on saying what it used to.
		`FlowController.refreshPinned` is not enough on its own: it re-translates the block it
		is holding, and what is wanted here is a fresh read of the row.
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
			# **Kept rather than cleared.** "I could not read it just now" is not "this table
			# has no headers": a header is a property of the table and it was there a moment
			# ago. `headerBlock` answers None for both, and it swallows its own failures to do
			# it — so one unlucky read took the header off the display and nothing put it back
			# until the layout was made again. On a spreadsheet every read can fail: a cell
			# fetch crosses a process boundary and the watchdog cancels the lot when the core
			# is busy.
			log.debugWarning("The pinned header row could not be read again, so it stands")
			return
		control.setPinned(said)

	def _tableStillFits(self) -> bool:
		"""Notice a table that changed shape, on a pass that may write nothing.

		The live pass writes the display only when the cells came out different, and a column
		appended beyond the page being drawn changes none of them — so a table that grew was
		invisible until something else caused a redraw. The shape questions are the two cheap
		ones `_tableChangedShape` already asks, and asking them here costs one read of the
		document's own fields per pass.

		Leaving the table is not this pass's business: `recheck` owns that, and it runs on
		every redraw.

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

		**What was added, not what is showing.** The chain used to go on only while the
		display changed, and the rows a cut-short band is missing are usually rows that do not
		show: content fetched above the window is what the reader scrolls up into, and it
		arrives off the top of the band by definition. So on the one document slow enough to
		need this — one step through Outlook's grouped inbox costs twice a whole allowance —
		the first pass fetched a row nobody could see yet, saw the display unchanged, and
		stopped, leaving the band short until something else happened to redraw it. The
		display is still only written when the cells came out different, which is the bargain
		that keeps a reading hand still.
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

		Called before each redraw. Almost always the answer is yes and this costs a focus
		object and an attribute, and the one time it is no is a change NVDA reports through
		no event at all: browse mode gives itself back when the reader presses Escape in a
		form field, and takes itself away again when they press Enter, and the focus does not
		move for either. What should be read changes on both, so a band that asked only on a
		focus change went on showing the field the reader had left.
		"""
		if self._rechecking or self.controller is None:
			return
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

		**The other half of a saved layout, and it was missing.** `_showTable` offers the
		layout whenever the band is built — arriving on the page, a focus change, a table being
		given up — and in browse mode the reader walks into a table without any of those
		happening: the caret moves, the focus object is still the document, and nothing asks.
		So a reader who saved a layout, arrowed out of the table and arrowed back got their
		ordinary reading, which is exactly the "watchlist that comes up laid out" the saving is
		for, refused at the moment they would notice.

		The twin of `_recheckTable`, which is what notices the caret leaving a table, and it
		costs the same: one recognition per redraw, and only for a reader who has saved
		something. An empty store answers here without looking at the object at all, and a
		table whose columns they have just turned off answers before the object is read too —
		see `_refusedTable`.

		**Offered once per table.** A table with a layout that will not fit this band would
		otherwise be rebuilt on every redraw, each attempt failing in the same way; the key of
		the last table offered is kept until the reader is somewhere that has none, which is
		the redraw after they leave.

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

		The second change NVDA reports through no event at all, and the twin of the one
		`recheck` already exists for. Pressing right arrow on a folder in a tree expands it:
		the rows below it become different rows, the focus does not move, no fresh focus
		regions are built, and the object the band is reading is the one it was already
		reading. Everything the band usually notices a change by says nothing happened.

		Asked of the source rather than worked out here, because only the source knows what
		kind of run it is holding and only the adapter knows what "open" means for it. A run
		whose members cannot be opened answers no and costs an attribute.

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
			# The controller is dropped rather than asked to rebuild, because `showObject`
			# recognises this as the same run the band is already showing and takes the fast
			# path for it: set the current object, follow the cursor, redraw. That path is
			# right for the ordinary case it was written for — in a list the focus changes on
			# every arrow key and walking the run again per keypress would be a call into the
			# application for each item passed — and it is exactly wrong here, where the run
			# is the thing that has changed. Without this the display kept the closed folder
			# and merely moved its cursor about inside it.
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

		The regions say where the focus has gone, which is the only reliable account of it:
		asking the system again can race the change, and the navigator object is somewhere
		else entirely once the reader has moved it.

		:param regions: the regions NVDA built for the new focus.
		:return: whether the band dealt with it. False hands them back to NVDA, which is
			what should happen when the focus has gone somewhere that cannot be read as a
			flow — a button in a dialog then reads as it always has.
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
			that has to be made again. **The two are the same thing for a document and
			opposite things for a table**, which is what this exists to say.

			In browse mode the focus stays on the page while the caret walks it, so a table
			was only ever redrawn through the live pass, which follows the cursor. In a
			*spreadsheet or a list* every arrow key is a focus change — a different cell, a
			different row — and each one arrived here as `force`, which means "make the
			reading again". So the layout was planned afresh on every keypress and the window
			was placed afresh with it: the row the reader had just moved to went to the **top**
			of the band with the rest of the table below it, instead of coming on at the
			bottom. Reported from an Excel worksheet as a display that jumped a page at a
			time, and, at the first row past the data, as a band that went blank but for its
			headers — a fresh window entered at the last row has nothing after it to fill with.

			The table path already has the question it needs, and it is a better one than
			`force`: `isStillHere` asks whether the reader is in the same table. Where they
			are, this is a move within it and the cursor is followed; where they are not, it
			rebuilds exactly as before.
		:return: whether a flow is showing afterwards.
		"""
		segment = self.segment()
		if segment is None:
			return False
		shown = self._showTable(obj, segment, force=force and not focusMoved)
		if shown is not None:
			return shown
		if self._readingATable():
			# The table is finished with — the reader left it, or asked for the columns to
			# stop — and the ordinary reading has to be built afresh. It will not be unless
			# the table flow is dropped first, because a table flow's source reads *the
			# document*: `_isCurrentDocument` compares the source's object with what the
			# focus resolves to, both of them the page's tree interceptor, and answers yes.
			# The band then took the "same document" path, called `arriveAt` on the table
			# controller, and kept the columns for the rest of the page — which on hardware
			# looked like a toggle that did nothing and a band stuck on the first table it
			# was given.
			#
			# The same shape as `_runHasChangedShape`, which drops the controller for the
			# same reason: a fast path that recognises the reading it is trying to replace.
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
		if self._isCurrentRun(obj):
			# The same run of objects. In a list the focus changes on every arrow key, so this
			# is the ordinary move rather than a jump: the window tracks the reader by the
			# smallest amount that keeps them on it, and everything already walked is kept.
			# Walking again per keypress would be a call into the application for each item
			# they pass.
			self.obj = obj
			self.controller.source.setCurrent(obj)
			if not self.controller.runStillHoldsTheBand():
				# Something beside them has been taken away or put there — a message deleted,
				# which is the case this was reported for: the focus moves to the next one and
				# the deleted row stays on the band, because every other block was read
				# correctly when it was read and nothing about the arrival says otherwise. The
				# run is what changed, so the run is read again, by the same path
				# `_runHasChangedShape` uses for a node being opened.
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

		**A rebuild makes a new segment, and a kept controller is not on it.** Every path that
		keeps the controller — the same document, the same run — used to refresh the segment
		and nothing more, which draws whatever the segment is holding. After a rebuild that is
		NVDA's regions for the focus, and the reader felt only the focused line. Reported from
		VS Code, which has a profile of its own: out to NVDA's menu and back switches profile
		twice, the switch rebuilds the display, and the reader is still in the same editor. A
		browser without a profile never rebuilt, which is why it looked like VS Code's fault.

		:param segment: the band's segment.
		"""
		if self.controller is not None and segment.controller is not self.controller:
			self._attach(segment, self.controller)
			return
		segment.refresh()

	# Tables.

	def layOutTable(self) -> bool:
		"""Read the table the reader is in as columns, if they are in one.

		Which of the two ways this can fail is left in `tableProblem`, because they are
		different things to be told and were one sentence. Standing in File Explorer's
		Details view and hearing "not in a table" says the recognition failed; hearing it
		when the table *was* recognised and the layout was the thing that would not come out
		sends the reader looking in the wrong place, and it is what they were told.

		:return: whether there was a table to lay out.
		"""
		obj = self._target()
		self.tableNotes = []
		try:
			handle = flowTableSource.tableAt(obj)
		except CallCancelled:
			# **Before anything is built, and it is the first thing the command does.** A
			# cancelled recognition used to come back as "you are not in a table", which is
			# the one thing the reader can see is untrue. See `flow.CallCancelled`.
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

		The notes are `buildTableController`'s own, which name the step that stopped it — no
		column layout fits this band, the first row could not be read — and until now nothing
		read them outside the dry run. The reader who is refused is the one person who needs
		them.

		Contained, because it runs between the reader's key press and what the command says
		back: a report that raised would take the answer with it, and the answer is the part
		they are waiting for.

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
		""":return: the table the reader asked for in columns, from the plugin that holds it."""
		return getattr(self.plugin, "tableWanted", None)

	@tableWanted.setter
	def tableWanted(self, key: Any) -> None:
		self.plugin.tableWanted = key

	@property
	def tableLayoutInForce(self):
		""":return: the layout the table on the band is being read with, or None.

		Kept on the plugin beside `tableWanted`, and for the same reason: a one row display
		moves the table off the band, and a request that lived on the band would go with it.

		None means "however this table reads by itself" — the measurement and the settings,
		or whatever was saved for it. A layout appears here when the reader arranges one from
		the band or the dialog, and it is what `script_rememberTableLayout` writes down.

		**Kept with the table it was arranged for**, and answered as None for any other. It
		is stored beside `tableWanted` but it is not the same thing: the request is dropped
		on several paths — leaving the table, a table that stopped being recognised, a build
		that could not fit the band — and an arrangement that outlived one of those would be
		waiting for the next table the reader laid out. Hidden columns and widths measured
		from a watchlist, applied to a message list, are worse than no columns at all, and
		the reader has no way to feel that is what has happened. So the key is stored with
		the layout and checked on the way out.
		"""
		held = getattr(self.plugin, "tableLayout", None)
		if held is None:
			return None
		key, layout = held
		return layout if flowTableSource.sameTable(key, self.tableWanted) else None

	@tableLayoutInForce.setter
	def tableLayoutInForce(self, layout) -> None:
		self.plugin.tableLayout = None if layout is None else (self.tableWanted, layout)

	def _forgetTheTable(self) -> None:
		"""Drop the reader's request for this table, and the arrangement that went with it.

		One place, because the two must go together and there are four paths that drop the
		request. See `tableLayoutInForce`.
		"""
		self.tableWanted = None
		self.tableLayoutInForce = None

	def arrangeColumn(self, column: int, **changes) -> bool:
		"""Change one column of the layout in force, and read the table again.

		What the commands on the band do: hide the column the cursor is in, cut it from the
		other end. They act on the layout rather than on the plan, so that what the reader
		feels and what `remember` would write down are the same thing — and so that a change
		survives the next rebuild, which a plan does not.

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

		Asked by a pin as it is made, so that pinning a table the reader is already reading in
		columns pins it in columns. The band's own request is dropped the moment the reader
		leaves the table — a layout must not outlive its table — and a pin is exactly a layout
		that outlives their being there, so the two cannot be one flag. This is how the second
		is seeded from the first.

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

		This is the only thing that can notice either. In browse mode the caret moves without
		the focus moving — the focus object stays the document — so no focus change is
		reported and `showObject` is not called. A table's blocks are rows read out of the
		document rather than regions that own a caret, so nothing else is watching either.

		The first cut of this returned immediately, on the grounds that the comparison
		`recheck` does next cannot answer a question about a table: it asks whether the object
		the reader is on resolves to the thing the source is reading, and for a table those
		are two different things by design. That was right about the comparison and wrong to
		stop there. On hardware the reader read a table, navigated up to a heading well above
		it, and the band went on showing the table — because the one place that could have
		noticed had been told not to look.

		So it looks, and it costs one `_getTableCellCoords` per redraw, which is a read of the
		document's own fields at the caret. That is the price of noticing, and nothing cheaper
		says anything: `passThrough`, the focus object and the navigator object are all
		unchanged when the caret walks out of a table.
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
			# A column appeared or went away. The plan is of a table that no longer exists —
			# it has no page for a column it never measured, so the band cannot follow the
			# caret there — and the widths were measured from what was in the old one. Read
			# the whole thing again.
			#
			# Where the reader is put afterwards depends on what set this off. A caret that
			# has just moved is the reader going somewhere, and the band follows them there
			# as it would for any move; a caret that has not moved means the table changed
			# under them while they were reading, and then the page and the rows they had are
			# theirs to keep. See `_whereTheyAreReading`.
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

		**What a rebuild has to put back, and put back before anything is written.** A rebuild
		is not something the reader asked for — the table changed shape under them while they
		were reading somewhere — and a new controller starts at the caret on page one. So they
		lost both axes at once: the page they had turned to, and the rows they had panned to.

		A review measured the cost of putting it back afterwards instead: two writes to the
		display for one rebuild, page one and then theirs, and the driver sends both.

		:param andTheRows: whether the top row is theirs to keep. False keeps the page of
			columns alone and follows the caret down the rows, which is what a band holding
			rows the table has stopped showing wants: the page is still the reader's own
			choice, and the row they were parked on may be one of the rows that went away.
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

		Three cheap questions, asked on each redraw beside the ones already being asked. How
		many columns it has now and — where the count is a fact rather than what has been
		built so far — how many rows, both of which `_getTableDimensions` has just answered
		anyway; and whether the caret's column is one the plan knows, which is proof of a
		change the counts cannot see, since a column removed and another added leaves the
		count alone.

		**Knowing a column is not the same as drawing it.** A column measured and left out —
		one holding nothing the reader can read — is one this plan knows perfectly well. The
		first cut asked where a column is *drawn*, so a caret sitting in
		an undrawn column read as a table that had changed under the band. Quick navigation
		lands the caret in the first cell of a table and on the reader's own watchlist that
		cell is an unreadable icon, so the layout was rebuilt on every redraw and panning did
		nothing: each pan was undone by the rebuild that followed it. See `ColumnPlan.knows`.

		What this cannot catch is a table whose columns are renamed or reordered without
		changing in number, with the caret staying where it is. Nothing cheap can, and the
		command is the way out: turning the layout off and on measures the table again.

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
			# **Only where the count is a fact, and only the part of it the reader cannot
			# move.** A list view's row count grows as the platform builds it and means
			# nothing; a sheet's means the sheet gained or lost rows — a formula filling down,
			# a query refreshing — and the stream then cannot be panned into the new ones at
			# all, because the source keeps the count it was made with.
			#
			# Not `numRows`, which reaches at least as far as the reader so that the row they
			# are standing in is part of the table: on a blank sheet that moves with every
			# arrow key, and a review caught the layout being measured and built again on each
			# of them. See `flowObjectTable.SheetTable.contentRows`.
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
			# A column the reader's own saved layout leaves out. They have said they do not
			# want it; there is nothing to look for in it and nothing to rebuild. Hardware
			# found this the hard way: a saved layout dropped the columns it did not name, so
			# the caret sitting in one read as a column from another table, and the band was
			# rebuilt on every redraw — which is a display that will not pan and a page turn
			# undone before the reader feels it.
			return False
		if found.col not in plan.omitted:
			# A column the plan never measured. That is a table this layout is not of.
			self._rebuildBecause(f"column {found.col} is not one this layout was made from")
			return True
		if (self.tableWanted, found.row, found.col) == self._askedAboutCell:
			# **Asked about this cell already, and the answer stands.** Whatever it was: a
			# cell that answered sent the layout to be made again, and asking a second time
			# would send it again on every live pass; a cell that said nothing is the reader
			# standing in an icon column, and asking again is a search of the document per
			# redraw for an answer that has not changed.
			#
			# The reader met both. Their watchlist has a blank first column and quick
			# navigation lands them in it, and the dry run put the cost at 44 ms a search
			# against 169 document change notices — panning stalled and a page turn took five
			# times as long as the same turn made from the column beside it.
			#
			# What it gives up is a value appearing in that cell while they stand on it.
			# Moving off and back asks again, and so does laying the table out afresh.
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

		**Every rebuild is a normal branch**, so nothing raised and nothing appeared in NVDA's
		log while a reader watched their display rebuild itself in a loop. A review asked for
		this after that hunt: the reason, the table's shape, where the caret is and what the
		band was showing, once per rebuild.

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

		`measure` reads a bounded sample, so a column empty throughout it is only
		*tentatively* empty — right for the column of unreadable icons the reader met, wrong
		for a column that happens to be blank in the eight rows that were looked at and holds
		a value further down. Left as it was, that value could never be reached: the column is
		not drawn, arriving at it changes nothing, and the cursor vanishes because the row on
		the band has no cell there.

		Proving a column empty needs reading all of it, which a wide table cannot afford.
		Asking about the one cell the reader has just arrived at costs a single search and
		answers the only case that matters, because a column nobody visits does not need to be
		drawn.

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

		The column axis of what the window does for rows, and the same bargain braille
		tethering makes with the focus: the display is free to travel away from the caret —
		panning does it, `turnColumnPage` does it — and a caret that then moves brings it back.

		**Brought back, rather than dragged back.** What is asked is whether the cell the caret
		moved to is on the display, not whether the page is that column's own. A pinned key
		column is drawn at the left of every page, so a reader who turned across to the numbers
		and then walked *down* the symbols is still feeling the column they are in, and the
		page they turned to stays. Only a caret that has gone somewhere they cannot feel is
		worth moving the display for.

		Hardware found both halves of that, one at a time. Following the caret's own page on
		every move put a reader on any page past the first back onto page one at the first
		press of down arrow, since table navigation keeps the column while moving the row.
		Refusing to follow at all — the fix for that — left the band where it had been paged
		while the caret walked off it, with nothing to bring it back and no way to tell where
		it had gone.

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
		# **By the least movement that reaches it**, which for the next column along is one
		# column: one comes on at the right and one goes off at the left, exactly as the
		# window does with rows. It used to turn to the column's own page, so a caret
		# stepping one to the right replaced every column under the reader's hands.
		start = plan.startShowing(column)
		if start == plan.at:
			return False
		return self._useColumnPage(plan.scrolledTo(start))

	def turnColumnPage(self, by: int) -> bool:
		"""Move the band across the table by pages of columns, without moving the caret.

		Looking around rather than going somewhere, which is why it leaves the caret where it
		is. The same relationship panning has with the cursor one axis over.

		**And the page stays turned while the reader can still feel where they are.** Two
		hardware reports, one either side of it. Bringing the page back to the caret's own
		column on every move meant that on any page past the first, one press of down arrow put
		the reader back on page one: table navigation keeps the column while moving the row, so
		the caret was still in column one and the band went there. Holding the page against
		every caret move instead — the fix for that — was worse the other way: the caret walked
		off the page, nothing brought the display back to it, and a reader arrowing across the
		columns had no idea where their cursor had gone.

		What settles it is the same rule braille tethering follows: the display may travel, and
		a caret that moves somewhere it is not showing brings it back. So the page holds for as
		long as the cell the caret is in is drawn on it — which, with the key column pinned at
		the left of every page, is the whole of reading a column down — and a move to a column
		this page does not show is followed. See `_showColumn`, which is where that is decided.

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

		The two halves have to happen together and in this order. The source reads only the
		page's columns — every cell is a search of the document, and reading a column on
		another page is a search for something nobody will feel — so the rows it read for the
		old page hold the wrong cells, and drawing them under the new plan would draw the old
		page's values at the new page's offsets.

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

		Asked of the source rather than of `tableWanted`, and the two part company exactly
		when it matters: the request is dropped the moment the reader leaves the table, and
		the controller built from it is still the one on the display until something replaces
		it.
		"""
		source = getattr(self.controller, "source", None)
		return isinstance(source, flowTableSource.TableFlowSource)

	def _showTable(self, obj: Any, segment, force: bool = False) -> Optional[bool]:
		"""Show the reader's table, and say when NVDA stopped waiting rather than pretending.

		**The boundary is here rather than around the build alone**, which is what a review
		asked for and where it counted: recognising the table, looking up what the reader
		saved for it and naming it for that lookup all happen *before* anything is built, and
		all of them are reads of the document. Cancelled, each answered "not a table" — and a
		reader told they are not in a table when they are looking at one goes looking in the
		wrong place.

		The band shows nothing either way. What differs is that a note is left, so the command
		says the table could not be read and they know to ask again.

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

		Answers None rather than False when there is no table to lay out, because None means
		"not mine" and False would mean "mine, and there is nothing to show" — which would
		leave the reader with a blank band on every page that is not a table.

		**The layout is dropped the moment the reader leaves the table.** Easy Table Navigator
		clears its key bindings on every focus change for the same reason. A watchlist's
		columns carried onto the next page are worse than no columns, because they are a
		layout of something that is not there, and the reader has no way to tell that is what
		they are feeling.

		:param obj: what the reader is now on.
		:param segment: the band's segment.
		:param force: rebuild even when the table has not changed.
		:return: whether a flow is showing, or None if this is not a table to lay out.
		"""
		# Found once and carried, because each of these is a read of the document at the
		# caret: a review counted the table resolved four times and the saved layout looked up
		# twice for one automatic layout, all of them asking what had just been asked.
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
			# The same table. The caret has moved between its cells, which is a move within
			# what is already being read rather than an arrival somewhere new.
			#
			# Both of these used to look the table up again, from the object, having been
			# handed the object the lookup above had just been made from. A review counted the
			# table resolved four times for one move between cells; on a worksheet each of
			# those reads the used range. The handle in hand is the answer to both questions.
			self.obj = obj
			self.controller.source.moveTo(handle)
			if self.controller.tableStillHoldsTheBand():
				# **Both axes, because this is the only thing that hears the move.** In browse
				# mode a caret move is not a focus change, so it arrives at `_recheckTable`,
				# which follows the columns as well as the rows. In a spreadsheet or a list it
				# arrives here instead — and `setCurrent` above has already moved the source,
				# so the check in `_recheckTable` finds nothing changed and never looks at the
				# column. The band followed the reader down the rows and left them behind
				# across the columns.
				self._showColumn(handle.col)
				self.controller.followCursor()
				self._redraw(segment)
				return True
			# The table has stopped showing a row the band is holding, which on a worksheet is
			# the reader filtering it. Everything below is the reading made again — the rows
			# walked afresh, so the ones the filter took away are stepped over, and the columns
			# measured afresh from rows that are actually on show.
			#
			# Falling through rather than calling `_rebuildTable`, which asks for a redraw from
			# inside the redraw that is already running. The build below is the same build, and
			# it has the table in hand.
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

		**The whole point of saving one.** A layout that has to be asked for by name every
		time is a layout the reader types out again on every page load; what they asked for is
		the watchlist coming up laid out. So a table the store knows becomes the table asked
		for, exactly as the command would have made it, and one keystroke still takes it away
		again — see `clearTable`, which drops the request without touching what was saved.

		Cheap where nothing is saved, which is every table until the reader saves one: an
		empty store answers here without looking at the object at all. See
		`flowTableLayouts.layoutFor`.

		Both, and neither is asked for twice: recognising the table and looking the layout up
		are reads of the document, and the caller needs the same two a moment later.

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
		return (handle, layout) if layout is not None else (None, None)

	def _refusedTable(self, obj: Any) -> bool:
		""":return: whether the reader has just taken this table's layout away.

		A saved layout applies itself, so turning it off has to mean something for longer than
		the redraw that follows: without this the command would drop the request and the next
		redraw would put it straight back, which is a toggle that does nothing. Held until the
		reader leaves the table, since coming back to it is them asking for it again.
		"""
		refused = getattr(self.plugin, "tableRefused", None)
		if refused is None:
			return False
		try:
			here = flowTableSource.wantsColumns(refused, obj)
		except Exception:
			log.debugWarning("Could not tell whether a table was refused", exc_info=True)
			return False
		if not here:
			# Somewhere else, so the refusal is spent. It was about the table they were in.
			self.plugin.tableRefused = None
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

		Tabbing lands on a thing rather than on a position, and of the two the position is
		the less reliable: the focus event can come before the browse mode cursor has caught
		up, and reading at the cursor then shows where the reader was. A document can be
		asked where an object is, so it is asked — for a link as much as for an edit field.

		Whether what they arrived at is a *control*, which is what decides prompt and
		spacing, is a separate question and `flowForms` answers it where the block is built.
		Deciding it here, from whether an object had been passed at all, made a link into a
		control the moment links began to be passed.

		Which object cannot be taken from the focus regions. They say which document, and for
		anything inside a page the document is what they are built over, so the thing itself
		is not among them. It is taken from the focus object instead, and a focus object that
		has since moved on somewhere else is ignored — which is what makes reading it here
		safe, since the regions remain the account of which document.

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

		Inside, not merely belonging to it, and the difference is the document's own object.
		A tree interceptor is built over one — `rootNVDAObject` — and that object's tree
		interceptor is the target, so a plain membership test calls the page a thing inside
		the page. What it is used for is `_arrival`: read at this object's own place rather
		than at the cursor. The page's own place is the start of the page, so the band was
		put at offset zero whenever the focus object was the document itself.

		In browse mode the focus object *is* the document for as long as the reader is not on
		a control, which is most of the time, so this was every rebuild that did not follow a
		real focus change. It showed up when a table gave its band back: the layout was
		released correctly and the reading that replaced it started at the top of the page
		rather than where the reader was standing, and the next arrow key put it right.
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

		Two readings, and each has a setting of its own, because they are different pieces of
		work and a reader may want one and not the other.

		**A browse mode document.** The test is whether the object belongs to one at all — it
		has a tree interceptor — and not whether browse mode is presenting it at this moment.
		A form field the reader has entered inside a page still belongs to that page, and the
		page is what has context to show; it is the same document, differently attended to.
		The object asked about may be either side of that fact: an `NVDAObject` with a tree
		interceptor, or the interceptor itself, since that is what NVDA puts on the regions it
		builds for a browse mode document.

		**A run of objects.** A list, a menu, the choices of a combo box: things the reader
		arrows through one of and wants the others of. Only where there is no document behind
		them, since a list inside a page is part of that page. See `flowObjects`.

		Anything else is left to NVDA, which presents it well. Notepad's edit control is
		neither, and a flow over it once followed the caret and grew a row at a time as the
		reader typed — which is what the narrowness here is for.

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

		Because "nothing happens" is the hardest report to act on, and the two answers behind
		it — this reader has not turned that kind on, and this object is not that kind — look
		identical from the outside.

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

		The focus regions say which document, and for browse mode that is the page's tree
		interceptor: the control the reader is inside is never among them. So a control that
		would be read as a document of its own has to be asked for separately, from the
		focus. A multi line edit being written in is that case — see `flowSources.documentFor`
		— and without this the page would always win and the edit would never be reached.
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
		""":return: the band's segment, or None if the claim is not on the display."""
		container = self._container()
		if container is None:
			return None
		try:
			segment = container.segmentForKey(BAND_NAME)
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

		Not the same as being evicted, and the difference is the whole of what the reader
		notices. Eviction means the claim could not be honoured and the flow is over, so the
		controller goes. This means a drawing has borrowed the rows and will give them back —
		so **the controller stays**, and with it every block already read and the position each
		was read from. Dropping it and building another on the way back put the reader at the
		top of the document instead of where they had got to, which is what this exists to
		stop.

		The pending passes are cancelled, because there is no segment for them to draw into
		and a settle or a live read arriving mid-suspension would be work done for nothing.
		They are started again by whatever the reader does after the band comes back.
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

		The focus rather than the navigator object, because this band *is* the focus
		segment: it shows the document the reader is working in, while the navigator object
		is wherever they last sent it. The navigator object is the fallback for the case
		where there is no focus to be had.
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
