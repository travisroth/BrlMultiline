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

The band is the whole display for now. A band of some of the rows is the same code with a
different rectangle, and wants a setting rather than an assumption; it is not attempted
here because the rectangle must lie inside one physical display's live band, which is a
question about the reader's hardware rather than about this module.
"""

from typing import TYPE_CHECKING, Any, Optional

import api
from logHandler import log

from .flowControl import FlowController
from .flowDryRun import bandSize, buildController
from .flowSegment import FlowBufferSegment
from .layout import SegmentRect
from .panels import FlowPanel, PanelOwner

if TYPE_CHECKING:
	from .container import DisplayContainer

BAND_NAME = "flow"
"""The panel's name, and so the key of its single segment."""


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
		self.controller: Optional[FlowController] = None
		self.obj: Any = None
		"""What the current controller is reading, so a focus change can be recognised."""

	# The claim.

	def start(self) -> bool:
		"""Claim the band and show a flow in it.

		:return: whether a flow is now showing.
		"""
		try:
			numRows, numCols = self._displaySize()
			panel = FlowPanel(BAND_NAME, SegmentRect(row=0, col=0, numRows=numRows, numCols=numCols))
			self.plugin.activatePanel(panel)
		except Exception:
			log.error("Could not claim a band for the flow", exc_info=True)
			return False
		return self.refresh(force=True)

	def stop(self) -> None:
		"""Give the band back and forget the flow."""
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

		:param force: build a controller even if the object has not changed, which is what
			a fresh claim or a rebuilt display needs.
		:return: whether a flow is showing afterwards.
		"""
		segment = self.segment()
		if segment is None:
			return False
		obj = self._target()
		if not force and obj is self.obj and self.controller is not None:
			return True
		numRows, numCols = self._bandSize(segment)
		control = buildController(
			obj=obj,
			numRows=numRows,
			numCols=numCols,
			handler=self._handler(),
			# The band is the focus segment, so this is the reader's own place in the
			# document: panning moves the browse mode cursor, and routing can activate.
			live=True,
		)
		if control is None:
			self.controller = None
			self.obj = None
			segment.detach()
			segment.refresh()
			return False
		self.controller = control
		self.obj = obj
		segment.attach(control, obj=control.source.obj)
		return True

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
		self.refresh(force=True)

	def onEvicted(self, keys: frozenset[str] = frozenset()) -> None:
		"""The claim no longer fits, so there is nothing to draw into."""
		log.debug("The flow band was evicted")
		self.controller = None
		self.obj = None

	def onTerminate(self) -> None:
		"""Everything is being taken back."""
		self.controller = None
		self.obj = None

	# Where things are.

	def _target(self):
		""":return: the object to read, which is where the reader is now."""
		return api.getNavigatorObject()

	def _container(self) -> "Optional[DisplayContainer]":
		return getattr(self.plugin, "container", None)

	def _handler(self):
		import braille

		return braille.handler

	def _displaySize(self) -> tuple[int, int]:
		""":return: the whole display's size, for the claim."""
		return bandSize(self._handler())

	def _bandSize(self, segment: FlowBufferSegment) -> tuple[int, int]:
		""":return: the band's own size, which is what the flow is laid out for."""
		return segment.rect.numRows, segment.rect.numCols

	def __repr__(self) -> str:
		state = "showing" if self.isShowing else "empty"
		return f"<FlowBand {state} reading {self.obj!r}>"
