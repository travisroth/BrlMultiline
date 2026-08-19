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

from . import flowQuickNav
from .flowControl import FlowController
from .flowDryRun import bandSize, buildController
from .devices import DeviceInfo, deviceMap
from .flowSegment import FlowBufferSegment
from .layout import SegmentRect, deviceBandRects, wholeDisplayRect
from .objectMonitor import resolveTarget
from .panels import FlowPanel, PanelOwner

if TYPE_CHECKING:
	from .container import DisplayContainer

BAND_NAME = "flow"
"""The panel's name, and so the key of its single segment."""

_generations = itertools.count(1)
"""Numbers each reading of a document, so that two documents cannot share a block identity.

A bookmark is a position within one document and says nothing about which. Following the
focus from one document to another with the same generation would let a stale anchor match
a block in the new one, and the reader would be put somewhere arbitrary."""


def _isTreeInterceptor(obj: Any) -> bool:
	""":return: whether an object is a browse mode document rather than something in one."""
	try:
		from treeInterceptorHandler import TreeInterceptor

		return isinstance(obj, TreeInterceptor)
	except ImportError:
		# Outside a running NVDA. A tree interceptor is the thing that can stand aside for
		# the control the reader has entered, which is what `passThrough` says.
		return hasattr(obj, "passThrough")


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

	# The claim.

	def start(self) -> bool:
		"""Claim the band and show a flow in it.

		:return: whether a flow is now showing. `lastError` says why not, and tells a band
			that could not be claimed apart from one that had nothing to read.
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
		# Browse mode is patched only while a flow is showing: a reader without one has no
		# use for it, and it is taken back in `stop`.
		flowQuickNav.install()
		if self.refresh(force=True):
			return True
		self.lastError = self.lastError or "nothing here reads as a flow"
		return False

	def bandRect(self, devices: "Optional[list[DeviceInfo]]" = None) -> SegmentRect:
		"""Choose the rectangle to claim.

		A flow band must lie inside one physical display's live cells. On an ordinary
		display that is the whole thing. On a composite it is one member's band, and the
		one chosen is the tallest, because rows are what a flow has to spend: a Monarch's
		eight rows of thirty-two are worth more to it than a Focus 80's single row of
		eighty, and a claim across both would reach the dead columns beside the Monarch.

		:param devices: the physical displays, read from the driver when not given.
		:return: the rectangle to claim.
		"""
		numRows, numCols = self._displaySize()
		devices = deviceMap() if devices is None else devices
		if not devices:
			return wholeDisplayRect(numRows, numCols)
		bands = deviceBandRects([device.band for device in devices], numCols)
		best = max(bands, key=lambda band: (band.live.numRows, band.live.displaySize))
		return best.live

	def _follow(self) -> None:
		"""Make the band bring its focus changes here, rather than answering them itself."""
		segment = self.segment()
		if segment is not None:
			segment.follow(self.handleFocusRegions)

	def stop(self) -> None:
		"""Give the band back and forget the flow."""
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

	# Keeping it fed.

	def refresh(self, force: bool = False) -> bool:
		"""Point the band at what the reader is in now.

		:param force: build a controller even if the reader has not moved, which is what a
			fresh claim or a rebuilt display needs.
		:return: whether a flow is showing afterwards.
		"""
		return self.showObject(self._target(), force=force)

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
		obj = self._objectOf(regions)
		return self.showObject(obj if obj is not None else self._target(), force=True)

	def showObject(self, obj: Any, force: bool = False) -> bool:
		"""Show a flow over an object, keeping the current one if it is the same document.

		:param obj: what the reader is now on.
		:param force: rebuild even when the document has not changed.
		:return: whether a flow is showing afterwards.
		"""
		segment = self.segment()
		if segment is None:
			return False
		if not self.isFlowable(obj):
			# Not something this flow is built for. The band goes back to being an ordinary
			# segment and NVDA presents the focus in it as it always has.
			self.controller = None
			self.obj = None
			segment.detach()
			return False
		target = self._resolve(obj)
		if target is not None and self._isCurrentDocument(target):
			# The same document, so the reader has moved within it rather than left it. A
			# focus change is a jump, so the window is placed afresh at the cursor, but the
			# blocks already read and the positions they were read from are still good.
			if not force and self.obj is obj:
				return True
			self.obj = obj
			showing = bool(self.controller and self.controller.enterAtCursor())
			segment.refresh()
			return showing
		control = buildController(
			obj=obj,
			numRows=segment.rect.numRows,
			numCols=segment.rect.numCols,
			handler=self._handler(),
			# The band is the focus segment, so this is the reader's own place in the
			# document: panning moves the browse mode cursor, and routing can activate.
			live=True,
			generation=next(_generations),
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
		segment.attach(control, obj=control.source.obj)
		return True

	def isFlowable(self, obj: Any) -> bool:
		"""Whether a flow is the right way to present this object yet.

		Browse mode is what the flow has been designed and tested against, and what it
		reads well: a document rendered as a run of blocks. So the test is whether the
		object belongs to a browse mode document at all — it has a tree interceptor — and
		not whether browse mode is presenting it at this moment. A form field the reader
		has entered inside a page still belongs to that page, and reading it as a flow is
		right; it is the same document, differently attended to.

		An object with no tree interceptor is a different matter. Notepad's edit control
		has none, and a flow over it followed the caret and grew a row at a time as the
		reader typed, which is not a presentation anyone asked for. Until a flow knows how
		to read objects — the adapters of milestone 6 — those are left to NVDA, which
		already presents them well.

		The object asked about may be either side of the same fact: an `NVDAObject` that has
		a tree interceptor, or the tree interceptor itself, since that is what NVDA puts on
		the regions it builds for a browse mode document.

		:param obj: what the reader is on.
		:return: whether to read it as a flow.
		"""
		if obj is None:
			return False
		try:
			if getattr(obj, "treeInterceptor", None) is not None:
				return True
			return _isTreeInterceptor(obj)
		except Exception:
			log.debugWarning("Could not tell whether this can be flowed", exc_info=True)
			return False

	def _isCurrentDocument(self, target: Any) -> bool:
		""":return: whether a resolved target is the one the current flow is reading."""
		return self.controller is not None and self.controller.source.obj is target

	def _resolve(self, obj: Any) -> Any:
		""":return: what would actually be read for an object, or None."""
		if obj is None:
			return None
		try:
			return resolveTarget(obj)
		except Exception:
			log.debugWarning("Could not resolve what to flow", exc_info=True)
			return None

	def _objectOf(self, regions) -> Any:
		"""Find what NVDA built a set of focus regions for.

		The last region is the one over the text — a browse mode document's tree
		interceptor, or the object itself — which is the same thing `resolveTarget` would
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
		if self.controller is None:
			return
		# A rebuild builds new segments, so the new one has to be told where its focus
		# changes go before anything else happens to it.
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
