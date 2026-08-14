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
from .devices import DeviceInfo
from .layout import (
	SegmentRect,
	calculateSegmentRects,
	calculateSegmentRectsIn,
	deviceBandRects,
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
	"DEVICE_PANEL_NAME",
	"DISPLAY_PANEL_NAME",
	"SegmentView",
	"deviceSegmentKey",
	"deviceView",
	"displayPanels",
	"displaySegmentKey",
	"singleSegmentView",
	"unavailableRects",
	"validateAgainstHardware",
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

DEVICE_PANEL_NAME = "device"
"""Prefix of the panels a composite display is built from.

Distinct from L{DISPLAY_PANEL_NAME} because the two views are not interchangeable: keys of a
configured view are numbered across the whole display, while a composite's are named after
the physical display they sit on. A pin does not carry over between them, and it should not:
the cells have moved to different hardware.
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
	:return: the configured view, or a single segment if the user has turned division off.
	"""
	if not bmConfig.areSegmentsEnabled(displayKey):
		# The layout stays in the configuration untouched, so turning division back on
		# restores it. Reported as the single view rather than as a failed configured one,
		# because this is a deliberate choice rather than a layout that would not fit.
		return singleSegmentView(numRows, numCols)
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


def unavailableRects(devices: Sequence[DeviceInfo], numCols: int) -> list[SegmentRect]:
	"""Work out which cells of a composite display reach no hardware at all.

	:param devices: the physical displays, in stacking order.
	:param numCols: the composite's width.
	:return: the dead rectangles. Empty when every display is the same width, and empty for an
		ordinary display, which has none.
	:raises ValueError: if the bands do not describe a display of this width.
	"""
	if not devices:
		return []
	return [
		band.dead
		for band in deviceBandRects([device.band for device in devices], numCols)
		if band.dead is not None
	]


def validateAgainstHardware(view: SegmentView, devices: Sequence[DeviceInfo], numCols: int) -> None:
	"""Check that no segment of a view reaches cells that no hardware has.

	This is an invariant of whatever is finally shown, not a property of the base view, and
	that distinction is the point of this function. `deviceView` claims the dead columns with
	a `BlankPanel`, but a blank panel is an ordinary panel: `SegmentView.withPanel` evicts
	every panel a new claim intersects, so a claim spanning the full width of a narrow
	display's rows would take the mask with it and put a segment over cells that reach nothing.
	An activated view bypasses `deviceView` altogether.

	The failure is silent, which is why it is worth a check of its own. `sliceBandCells` takes
	only the first `numCols` of each of a display's rows, so text laid out past that edge is
	dropped on the way to the hardware with nothing raised, nothing logged and nothing to say
	where it went.

	:param view: the view about to be shown.
	:param devices: the physical displays behind it, empty for an ordinary display.
	:param numCols: the composite's width.
	:raises ValueError: if any segment reaches cells no hardware has.
	"""
	dead = unavailableRects(devices, numCols)
	if not dead:
		return
	for spec in view.flatten():
		for rect in dead:
			if rectsIntersect(spec.rect, rect):
				raise ValueError(
					f"Segment {spec.key!r} at {spec.rect} reaches cells at {rect} that no display "
					f"has; anything shown there would be dropped on the way to the hardware",
				)


def deviceSegmentKey(driverName: str, index: int) -> str:
	""":return: the key of one segment of a composite display.

	Named after the physical display it sits on rather than numbered across the composite, so
	that a pin on the secondary display survives a settings change on the primary. A driver
	name identifies a member uniquely, because the virtual driver refuses to list one driver
	twice.
	"""
	return f"{DEVICE_PANEL_NAME}.{driverName}.{index}"


def deviceBandSegmentRects(device: DeviceInfo, rect: SegmentRect) -> list[SegmentRect]:
	"""Divide one physical display's rows using that display's own settings.

	The key read is the one that display has when NVDA drives it on its own, so a Monarch
	divided into three segments stays divided into three segments as part of a composite. The
	settings the user already made are the settings that apply, with nothing to set up.

	:param device: the physical display.
	:param rect: the cells it actually has, in composite coordinates.
	:return: one rectangle per segment, in display order. A single rectangle covering the
		whole band when this display is not to be divided.
	"""
	displayKey = device.displayKey
	if not bmConfig.areSegmentsEnabled(displayKey):
		return [rect]
	layout = bmConfig.getLayout(displayKey)
	try:
		return calculateSegmentRectsIn(rect, layout)
	except ValueError:
		log.error(
			f"BrlMultiline: layout {layout} stored for {displayKey} does not fit its "
			f"{rect.numRows} by {rect.numCols} band; leaving that display as one segment",
			exc_info=True,
		)
		return [rect]


def deviceView(
	numRows: int,
	numCols: int,
	devices: Sequence[DeviceInfo],
	displayKey: str | None = None,
) -> SegmentView:
	"""Build the view for a composite of several physical displays.

	Two things make this different from L{viewFromConfig}, and both come from the composite
	not being one piece of hardware.

	The first is that no segment may straddle two displays. The composite is always divided at
	the band boundaries, whatever the user has configured for it, and each display is then
	divided within its own band by its own settings. So the composite has no segment count of
	its own; the question "how many segments" is asked of each display separately, and answered
	by the settings that display already had.

	The second is the dead columns. A display narrower than the widest one has cells on its
	rows that reach no hardware. They are claimed by a `BlankPanel` so that nothing is ever
	flowed into them, which is the whole reason this view exists: without it, text landing in
	those cells is silently lost.

	:param numRows: number of rows on the composite.
	:param numCols: number of columns on the composite, the widest display's width.
	:param devices: the physical displays, in stacking order, top first.
	:param displayKey: the composite's own configuration key, for the focus segment, or None
		for the current display.
	:return: the view.
	:raises ValueError: if the bands do not tile a display of this size.
	"""
	if not devices:
		raise ValueError("A composite display needs at least one physical display")
	panels: list[BraillePanel] = []
	keys: list[str] = []
	for device, band in zip(devices, deviceBandRects([each.band for each in devices], numCols), strict=True):
		for index, rect in enumerate(deviceBandSegmentRects(device, band.live)):
			key = deviceSegmentKey(device.driverName, index)
			panels.append(
				# One panel per segment, as the configured view does and for the same reason:
				# a claim laid over the composite then evicts only the segments whose cells it
				# actually wants, leaving those on the other display with their keys.
				SinglePanel(
					key,
					rect,
					reserve=False,
					# Numbered across the whole composite, so that the document lines feature
					# reads on from one display to the next rather than restarting.
					documentContextIndex=len(keys),
				),
			)
			keys.append(key)
		if band.dead is not None:
			panels.append(BlankPanel(band.dead, name=f"{DEVICE_PANEL_NAME}.{device.driverName}.dead"))
	focusSegment = bmConfig.getFocusSegment(displayKey)
	if focusSegment != -1 and not 0 <= focusSegment < len(keys):
		log.warning(
			f"BrlMultiline: no segment {focusSegment} across these {len(keys)} segments; using the last",
		)
		focusSegment = -1
	view = SegmentView(
		name="devices",
		panels=panels,
		focusSegmentKey=keys[focusSegment if focusSegment != -1 else len(keys) - 1],
	)
	# Checked here rather than left to the caller, because the bands come from the driver
	# while the size comes from the handler, and the two disagreeing is exactly the case a
	# caller cannot detect for itself. `deviceBandRects` has already caught a gap between two
	# displays; this catches the composite being a different shape from the displays in it.
	view.validate(numRows, numCols)
	return view
