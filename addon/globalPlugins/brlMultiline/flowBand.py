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

import itertools
from typing import TYPE_CHECKING, Any, Optional

import api
from logHandler import log

from . import bmConfig, flowForms, flowObjects, flowQuickNav, flowTableSource
from .flowControl import FlowController
from .flowBuild import (
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
from .flowSources import DocumentFlowSource, documentFor
from .panels import FlowPanel, PanelOwner

SETTLE_MILLIS = 150
"""How long after a keystroke into an edit the band comes back for a second look.

Long enough for the editor to finish answering — the transients the probe caught were gone
by the next keystroke, half a second later, and are an artefact of the instant itself — and
short enough that a reader pausing to read the display never meets them. Restarted on every
keystroke, so a steady typist pays for one settle pass per pause rather than one per key.
"""

if TYPE_CHECKING:
	from .container import DisplayContainer

BAND_NAME = "flow"
"""The panel's name, and so the key of its single segment."""

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

		self.tableWanted: Any = None
		"""The table the reader has asked to see in columns, by its own identifier.

		None means every table reads as the page around it does, which is reading order and is
		the default. See `layOutTable`, and decision 19 of the structured presentation plan:
		the end state is a layout remembered against a table, and this is the command that
		exists before that and remains the escape hatch after it.
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
		try:
			panel = FlowPanel(BAND_NAME, self.bandRect())
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
		the reader named, or else the tallest, since rows are what a flow has to spend —
		because a claim across both would reach the dead columns beside the narrower one.

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
		device = preferredDevice(bmConfig.getFlowDisplay(), devices)
		whole = device.liveRect if device is not None else wholeDisplayRect(numRows, numCols)
		rows = bmConfig.getFlowRows()
		if rows and rows < whole.numRows:
			return SegmentRect(row=whole.row, col=whole.col, numRows=rows, numCols=whole.numCols)
		return whole

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

		Restarted on every keystroke, so during steady typing it fires once, after the
		burst. The pass itself redraws only when it changed something, and scheduling
		happens only from a display update, so a settle that changes nothing ends the
		exchange rather than perpetuating it.
		"""
		import wx

		if self._settleTimer is not None:
			try:
				self._settleTimer.Stop()
			except Exception:
				pass
		try:
			self._settleTimer = wx.CallLater(SETTLE_MILLIS, self._settle)
		except Exception:
			log.debugWarning("Could not schedule a flow settle pass", exc_info=True)
			self._settleTimer = None

	def _settle(self) -> None:
		"""Read the edit again now that it has had a moment, and redraw if that changed anything."""
		self._settleTimer = None
		control = self.controller
		if control is None:
			return
		try:
			before = control.cells()
			control.followCursor()
			if control.cells() == before:
				return
			segment = self.segment()
			if segment is not None:
				segment.refresh()
		except Exception:
			log.debugWarning("A flow settle pass failed", exc_info=True)

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
		if self._readingATable():
			self._recheckTable()
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
			focusRegions=regions,
		)

	def showObject(self, obj: Any, force: bool = False, focusRegions=None) -> bool:
		"""Show a flow over an object, keeping the current one if it is the same document.

		:param obj: what the reader is now on.
		:param force: rebuild even when the document has not changed.
		:param focusRegions: the regions NVDA built for this focus, so an embedded edit can
			keep its real text region and caret owner.
		:return: whether a flow is showing afterwards.
		"""
		segment = self.segment()
		if segment is None:
			return False
		shown = self._showTable(obj, segment, force=force)
		if shown is not None:
			return shown
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
			self.controller.followCursor()
			segment.refresh()
			return True
		target = self._resolve(obj)
		if target is not None and self._isCurrentDocument(target):
			# The same document, so the reader has moved within it rather than left it. A
			# focus change is a jump, so the window is placed afresh at the cursor, but the
			# blocks already read and the positions they were read from are still good.
			changedEdit = self._setInteractiveObject(obj, target, focusRegions)
			if not force and self.obj is obj and not changedEdit:
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
			segment.refresh()
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
		segment.attach(control, obj=control.source.obj)
		return True

	# Tables.

	def layOutTable(self) -> bool:
		"""Read the table the reader is in as columns, if they are in one.

		:return: whether there was a table to lay out.
		"""
		handle = flowTableSource.tableAt(self._target())
		if handle is None:
			return False
		self.tableWanted = handle.tableID
		self.refresh(force=True)
		return self.controller is not None and self._readingATable()

	def clearTable(self) -> bool:
		"""Go back to reading the table the way the page around it is read.

		:return: whether anything was being laid out.
		"""
		if self.tableWanted is None:
			return False
		self.tableWanted = None
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
		if found is None or not flowTableSource._sameTable(found.tableID, self.tableWanted):
			# Out of the table. The request goes with it, so that walking into a different
			# table later does not lay that one out uninvited.
			self.tableWanted = None
			self._rebuildTable()
			return
		if found.row == source.row:
			return
		source.moveTo(found)
		self._rechecking = True
		try:
			self.controller.followCursor()
			segment = self.segment()
			if segment is not None:
				segment.refresh()
		finally:
			self._rechecking = False

	def _rebuildTable(self) -> None:
		"""Read whatever is here now, the ordinary way. Guarded, since this runs in a redraw."""
		self._rechecking = True
		try:
			self.refresh(force=True)
		except Exception:
			log.debugWarning("Could not give the table's band back", exc_info=True)
		finally:
			self._rechecking = False

	def _readingATable(self) -> bool:
		""":return: whether the band is showing a table laid out in columns."""
		source = getattr(self.controller, "source", None)
		return isinstance(source, flowTableSource.TableFlowSource)

	def _showTable(self, obj: Any, segment, force: bool = False) -> Optional[bool]:
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
		if self.tableWanted is None:
			return None
		handle = flowTableSource.tableAt(obj)
		if handle is None or not flowTableSource._sameTable(handle.tableID, self.tableWanted):
			self.tableWanted = None
			return None
		if not force and self._readingATable() and self.controller.source.isStillHere(obj):
			# The same table. The caret has moved between its cells, which is a move within
			# what is already being read rather than an arrival somewhere new.
			self.obj = obj
			self.controller.source.setCurrent(obj)
			self.controller.followCursor()
			segment.refresh()
			return True
		numRows, numCols = self._bandSize(segment)
		control = buildTableController(
			obj=obj,
			numRows=numRows,
			numCols=numCols,
			handler=self._handler(),
			live=True,
			generation=next(_generations),
		)
		if control is None:
			# Recognised a moment ago and not now, or no column layout fits this band. Reading
			# order is a good answer to both, so the request is dropped rather than held on to
			# in the hope that the next redraw goes better.
			self.tableWanted = None
			return None
		self.controller = control
		self.obj = obj
		segment.attach(control, obj=control.source.obj)
		return True

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
		""":return: whether an object is something inside the document being read."""
		if obj is None or obj is target:
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
