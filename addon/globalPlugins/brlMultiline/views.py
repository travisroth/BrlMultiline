# BrlMultiline: views.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A view is a complete arrangement of the braille display.

Everything the add-on puts on the display is a view, including the plain single segment
arrangement that matches NVDA's own behaviour. A view is a recipe rather than live state:
it is named, it is built from configuration or from code, and it is handed to the plugin to
activate. Exactly one is active at a time, because a view arranges every cell of a display
that can only be in one arrangement at once.

A view is made of panels. Each panel claims a rectangle and decides how to subdivide it, so
that code wanting part of the display can say so without knowing what holds the rest. The
panels of a view must tile the display exactly: every cell belongs to one named panel, so a
new claim can only take cells from a panel that can be identified and evicted.

`flatten` dissolves the panels into the flat list of segment specifications the container
builds from. Panels do not survive into the running display; they are kept on the view so
that the next view can be composed from this one.

The settings dialog produces views of one panel divided into whole row groups (or, on a
single row display, column slices). Code driving the display directly can compose any view
it likes, and `withPanel` is how it does so without disturbing what it did not claim.
"""

from collections.abc import Sequence

from logHandler import log

from . import bmConfig
from .layout import (
	SegmentRect,
	calculateSegmentRects,
	rectContains,
	rectsIntersect,
	remainderRects,
	validateCoverage,
	validateRects,
	wholeDisplayRect,
)
from .panels import BlankPanel, BraillePanel, GridPanel, RowsPanel, SegmentSpec, SinglePanel
from .routing import DEFAULT_ROUTING_POLICY, EdgeRowScrollRoutingPolicy, RoutingPolicy

__all__ = [
	# This module's own.
	"DISPLAY_PANEL_NAME",
	"SegmentView",
	"displayPanels",
	"displaySegmentKey",
	"singleSegmentView",
	"viewFromConfig",
	# Re-exported, so that code composing a view needs only this one import.
	"BlankPanel",
	"BraillePanel",
	"DEFAULT_ROUTING_POLICY",
	"EdgeRowScrollRoutingPolicy",
	"GridPanel",
	"RoutingPolicy",
	"RowsPanel",
	"SegmentSpec",
	"SinglePanel",
]

DISPLAY_PANEL_NAME = "display"
"""Name of the panel a configured view is built from.

Held constant across the single segment view and the configured view so that segment keys
survive a settings change wherever the segment itself does.
"""


class SegmentView:
	"""A complete arrangement of the braille display."""

	def __init__(
		self,
		name: str,
		panels: Sequence[BraillePanel],
		focusSegmentKey: str | None = None,
	) -> None:
		"""
		:param name: a short name, for logging and for reporting the current view.
		:param panels: the claims making up this view. They must tile the display, which
			L{validate} checks against a specific display size.
		:param focusSegmentKey: the key of the segment following the system focus. None
			means the last segment in display order. A view must have one: NVDA reads
			`mainBuffer.regions[-1]` in several places, so there always has to be a segment
			for untargeted content to land in.
		"""
		self.name = name
		self.panels = list(panels)
		specs = self.flatten()
		if focusSegmentKey is None and specs:
			focusSegmentKey = specs[-1].key
		self.focusSegmentKey = focusSegmentKey

	def flatten(self) -> list[SegmentSpec]:
		"""Dissolve the panels into the segments the container builds from.

		:return: one specification per segment, sorted into display order, top to bottom
			and then left to right. The order is the segment numbering the user's per
			segment commands address, so it has to follow the display rather than the
			order the panels happened to be composed in.
		"""
		specs = [spec for panel in self.panels for spec in panel.segments()]
		specs.sort(key=lambda spec: (spec.rect.row, spec.rect.col))
		return specs

	def validate(self, numRows: int, numCols: int) -> None:
		"""Check this view can be shown on a display of the given size.

		:param numRows: number of rows on the display.
		:param numCols: number of columns on the display.
		:raises ValueError: if the panels do not tile the display, if a panel's segments do
			not fit within it, or if two segments share a key.
		:raises LookupError: if the focus segment does not exist.
		"""
		if not self.panels:
			raise ValueError(f"View {self.name!r} has no panels")
		validateCoverage([panel.rect for panel in self.panels], numRows, numCols)
		for panel in self.panels:
			panel.validate()
		specs = self.flatten()
		if not specs:
			raise ValueError(f"View {self.name!r} has no segments; every panel is blank")
		keys = [spec.key for spec in specs]
		duplicates = {key for key in keys if keys.count(key) > 1}
		if duplicates:
			raise ValueError(f"View {self.name!r} reuses segment keys {sorted(duplicates)}")
		# Panels have been checked against their own claims, and the claims tile the
		# display, so this can only fail if a panel reported segments outside the rectangle
		# it validated. Checking anyway keeps the compositor's assumption explicit.
		validateRects([spec.rect for spec in specs], numRows, numCols)
		if self.focusSegmentKey not in keys:
			raise LookupError(
				f"View {self.name!r} wants segment {self.focusSegmentKey!r} to follow the focus, "
				f"but has no segment with that key",
			)

	def withPanel(
		self,
		panel: BraillePanel,
		numRows: int,
		numCols: int,
		name: str | None = None,
	) -> "SegmentView":
		"""Compose a new view by laying a panel over this one.

		Panels this claim touches are evicted whole. A panel is a rectangle and has to stay
		one, so there is no partial eviction: a claim overlapping half a panel takes all of
		it. Cells freed that the new claim does not want are given to blank panels, so the
		result still tiles the display.

		Panels the claim does not touch are carried over untouched, keeping their segment
		keys. That is what lets a pinned object survive: the panel holding it is not
		involved in the claim, so its key still resolves after the rebuild.

		:param panel: the claim to lay over this view.
		:param numRows: number of rows on the display.
		:param numCols: number of columns on the display.
		:param name: a name for the resulting view. Defaults to combining the two.
		:return: the composed view. This view is left unchanged.
		:raises ValueError: if the claim falls outside the display.
		:raises LookupError: if the claim would evict the focus segment and the new panel
			does not offer one in its place.
		"""
		panel.validate()
		if not rectContains(wholeDisplayRect(numRows, numCols), panel.rect):
			raise ValueError(
				f"Panel {panel.name!r} at {panel.rect} does not fit a {numRows} by {numCols} display",
			)
		kept = [each for each in self.panels if not rectsIntersect(each.rect, panel.rect)]
		panels: list[BraillePanel] = [*kept, panel]
		panels.extend(
			BlankPanel(rect) for rect in remainderRects([each.rect for each in panels], numRows, numCols)
		)
		keys = {spec.key for each in panels for spec in each.segments()}
		if self.focusSegmentKey in keys:
			focusKey = self.focusSegmentKey
		elif panel.focusSegmentKey is not None:
			# The claim took the rows the focus segment lived in, and offers a replacement.
			focusKey = panel.focusSegmentKey
		else:
			raise LookupError(
				f"Panel {panel.name!r} would evict the focus segment {self.focusSegmentKey!r} "
				f"but does not offer one in its place, leaving nowhere for untargeted regions to go",
			)
		return SegmentView(name or f"{self.name}+{panel.name}", panels, focusKey)

	def panelNamed(self, name: str) -> BraillePanel | None:
		""":return: the panel with a given name, or None if this view has no such panel."""
		for panel in self.panels:
			if panel.name == name:
				return panel
		return None

	def withoutPanel(
		self,
		name: str,
		numRows: int,
		numCols: int,
		focusSegmentKey: str | None = None,
	) -> "SegmentView":
		"""Compose a new view with one panel removed, its cells left blank.

		Removing a panel leaves a hole rather than reflowing what is left, since the panels
		around it claimed the cells they wanted and none of them asked for more. The usual
		way back to a full display is to rebuild from the configured view instead, which is
		what the plugin does when a claim is withdrawn.

		:param name: the panel to remove.
		:param numRows: number of rows on the display.
		:param numCols: number of columns on the display.
		:param focusSegmentKey: the segment to follow the focus if the removed panel held
			it. None means the removal must not touch the focus.
		:return: the composed view, or this view unchanged if it has no such panel.
		:raises LookupError: if the removed panel held the focus segment and no replacement
			was named, or the named replacement does not exist.
		"""
		panel = self.panelNamed(name)
		if panel is None:
			return self
		panels: list[BraillePanel] = [each for each in self.panels if each is not panel]
		panels.extend(
			BlankPanel(rect) for rect in remainderRects([each.rect for each in panels], numRows, numCols)
		)
		keys = {spec.key for each in panels for spec in each.segments()}
		if self.focusSegmentKey in keys:
			focusKey = self.focusSegmentKey
		elif focusSegmentKey is not None:
			focusKey = focusSegmentKey
		else:
			raise LookupError(
				f"Removing panel {name!r} would take the focus segment {self.focusSegmentKey!r} with it, "
				f"leaving nowhere for untargeted regions to go; name a replacement to remove it anyway",
			)
		if focusKey not in keys:
			raise LookupError(f"There is no segment {focusKey!r} to take the focus after removing {name!r}")
		return SegmentView(f"{self.name}-{name}", panels, focusKey)

	def __repr__(self) -> str:
		return (
			f"<SegmentView {self.name!r} {len(self.panels)} panels, "
			f"{len(self.flatten())} segments, focus {self.focusSegmentKey!r}>"
		)


def displaySegmentKey(index: int) -> str:
	""":return: the key of one segment of a configured view.

	The settings dialog counts segments; a view names them. Because a configured view puts
	each segment in a panel of its own, the two agree: segment 3 of the display is always
	keyed `display.3`, whatever else is on the display around it.
	"""
	return f"{DISPLAY_PANEL_NAME}.{index}"


def displayPanels(rects: list[SegmentRect]) -> list[SinglePanel]:
	"""Wrap each segment rectangle of a configured view in a panel of its own.

	One panel per segment is the finest grained view there is, and it is what makes the
	configured view composable. A claim laid over it evicts only the panels whose cells it
	actually wants, so segments elsewhere on the display keep their keys, and anything
	holding one of those keys, such as a pinned object, survives the rebuild.

	Each is given its ordinal as its document context index, so the document lines feature
	reads them consecutively however many claimed segments end up between them.

	:param rects: the segment rectangles, in display order.
	:return: one panel per rectangle, none of them reserved, so the add-on may fill the
		free ones with the document lines around the caret.
	"""
	return [
		SinglePanel(displaySegmentKey(index), rect, reserve=False, documentContextIndex=index)
		for index, rect in enumerate(rects)
	]


# The settings dialog offers no control over wrapping, continuation marks, or routing, so
# a configured view carries the defaults across all of its segments. Those values live on
# each segment rather than on the view, so code composing a panel can vary them freely
# without any of it reaching the settings dialog.
def viewFromConfig(numRows: int, numCols: int, displayKey: str | None = None) -> SegmentView:
	"""Build the view the user has configured for a display.

	Falls back to a single segment covering the whole display if the stored layout does
	not fit, which can happen when a display is replaced by a smaller one under the same
	name.

	:param numRows: number of rows on the display.
	:param numCols: number of columns on the display.
	:param displayKey: the display to read settings for, or None for the current one.
	:return: the configured view.
	"""
	layout = bmConfig.getLayout(displayKey)
	try:
		rects = calculateSegmentRects(numRows, numCols, layout)
	except ValueError:
		log.error(
			f"BrlMultiline: configured layout {layout} does not fit "
			f"a {numRows} by {numCols} display; using a single segment",
			exc_info=True,
		)
		# Not calling singleSegmentView, because the view should still report itself as
		# configured: the layout failed, but this is still the user's view of the display.
		rects = calculateSegmentRects(numRows, numCols, 1)
	focusSegment = bmConfig.getFocusSegment(displayKey)
	if focusSegment != -1 and not 0 <= focusSegment < len(rects):
		log.warning(
			f"BrlMultiline: no segment {focusSegment} in this layout; using the last segment",
		)
		focusSegment = -1
	focusIndex = len(rects) - 1 if focusSegment == -1 else focusSegment
	return SegmentView(
		name="configured",
		panels=displayPanels(rects),
		focusSegmentKey=displaySegmentKey(focusIndex),
	)


def singleSegmentView(numRows: int, numCols: int) -> SegmentView:
	"""Build a view of one segment covering the whole display.

	This is what NVDA does on its own, expressed as a view.
	"""
	return SegmentView(
		name="single",
		panels=displayPanels([wholeDisplayRect(numRows, numCols)]),
		focusSegmentKey=displaySegmentKey(0),
	)
