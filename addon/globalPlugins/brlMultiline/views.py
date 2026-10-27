# BrlMultiline: views.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A view is a complete arrangement of the braille display.

A view is a recipe, built from configuration or code and activated by the plugin; exactly one
is active. It is made of panels that tile the display exactly, so a new claim can only take
cells from a panel that can be named and evicted. `flatten` turns them into the segment
specifications the container builds from, and `withPanel` composes a claim over a view.
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
from .panels import (
	BlankPanel,
	BraillePanel,
	GraphicsPanel,
	GridPanel,
	RowsPanel,
	SegmentSpec,
	SinglePanel,
)
from .routing import DEFAULT_ROUTING_POLICY, EdgeRowScrollRoutingPolicy, RoutingPolicy

__all__ = [
	# This module's own.
	"DEVICE_PANEL_NAME",
	"DISPLAY_PANEL_NAME",
	"SegmentView",
	"deviceFallbackView",
	"deviceSegmentKey",
	"deviceView",
	"driverNameForSegmentKey",
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
	"GraphicsPanel",
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

Distinct from `DISPLAY_PANEL_NAME`, since a composite's keys are named after the physical
display, so a pin does not carry over between the two.
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
			means the last segment in display order. A view must have one, since NVDA reads
			`mainBuffer.regions[-1]`.
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
			and then left to right, the numbering the per segment commands use.
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
		# Implied by the checks above, and kept to make the compositor's assumption explicit.
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

		Panels the claim touches are evicted whole, and freed cells it does not want go to
		blank panels. Untouched panels keep their keys, so a pinned object elsewhere survives.

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

		Leaves a hole rather than reflowing. The plugin instead rebuilds from the configured view
		when a claim is withdrawn.

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

	Segment 3 is always `display.3`, since each segment has a panel of its own.
	"""
	return f"{DISPLAY_PANEL_NAME}.{index}"


def displayPanels(rects: list[SegmentRect]) -> list[SinglePanel]:
	"""Wrap each segment rectangle of a configured view in a panel of its own.

	So a claim evicts only the panels whose cells it wants. See `SegmentView.withPanel`.

	:param rects: the segment rectangles, in display order.
	:return: one panel per rectangle, none of them reserved.
	"""
	return [SinglePanel(displaySegmentKey(index), rect, reserve=False) for index, rect in enumerate(rects)]


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
		# The layout stays configured, so turning division back on restores it.
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
	"""Check that every segment of a view sits on exactly one physical display.

	An invariant of whatever is shown, since a composed view, or a claim over `deviceView`,
	can lose the dead column mask. Reaching a dead column silently loses text in
	`sliceBandCells`; straddling two displays leaves `segmentsForDevice` unable to say whose
	the segment is, which is why equal width composites are checked too.

	:param view: the view about to be shown.
	:param devices: the physical displays behind it, empty for an ordinary display.
	:param numCols: the composite's width.
	:raises ValueError: if any segment reaches cells no hardware has or spans two displays, or
		if the bands do not describe a display of this width.
	"""
	if not devices:
		return
	bands = deviceBandRects([device.band for device in devices], numCols)
	dead = [band.dead for band in bands if band.dead is not None]
	for spec in view.flatten():
		for rect in dead:
			if rectsIntersect(spec.rect, rect):
				raise ValueError(
					f"Segment {spec.key!r} at {spec.rect} reaches cells at {rect} that no display "
					f"has; anything shown there would be dropped on the way to the hardware",
				)
		if any(rectContains(band.live, spec.rect) for band in bands):
			continue
		touched = [
			device.driverName
			for device, band in zip(devices, bands, strict=True)
			if rectsIntersect(spec.rect, band.live)
		]
		raise ValueError(
			f"Segment {spec.key!r} at {spec.rect} is spread across {touched or 'no display'} rather "
			f"than sitting on one; panning, pinning and the per segment commands all address a "
			f"segment as belonging to a display",
		)


def deviceSegmentKey(driverName: str, index: int) -> str:
	""":return: the key of one segment of a composite display.

	Named after its physical display, so a pin on one survives a settings change on another.
	"""
	return f"{DEVICE_PANEL_NAME}.{driverName}.{index}"


def driverNameForSegmentKey(key: str) -> str | None:
	"""Read back which display a segment key names.

	The inverse of `deviceSegmentKey`, safe for any key, since a driver name has no dot.

	:param key: any segment key.
	:return: the driver name, or None if this key does not name a physical display.
	"""
	parts = key.split(".")
	if len(parts) == 3 and parts[0] == DEVICE_PANEL_NAME:
		return parts[1]
	return None


def deviceSegmentKeys(devices: Sequence[DeviceInfo], numCols: int) -> list[str]:
	""":return: the keys of a composite's segments, in the order the focus segment setting counts them.

	What `focusSegment` and `messageSegment` index: the configured arrangement, not the
	container's, which changes under a claim. `deviceView` names its segments from this list,
	so the two cannot disagree.

	:param devices: the physical displays, in stacking order, top first.
	:param numCols: the composite's width, the widest display's.
	:raises ValueError: if the bands do not tile a display of this width.
	"""
	keys: list[str] = []
	for device, band in zip(devices, deviceBandRects([each.band for each in devices], numCols), strict=True):
		keys.extend(
			deviceSegmentKey(device.driverName, index)
			for index in range(len(deviceBandSegmentRects(device, band.live)))
		)
	return keys


def focusDisplayDriver(
	devices: Sequence[DeviceInfo],
	numCols: int,
	focusSegment: int,
) -> str | None:
	""":return: which physical display holds the segment that follows the system focus.

	Numbered by `deviceSegmentKeys`, as the settings dialog and commands are.

	:param devices: the physical displays, in stacking order, top first.
	:param numCols: the composite's width.
	:param focusSegment: the stored setting, where -1 means the last segment.
	:return: the driver name, or None where there is nothing to choose between.
	"""
	try:
		keys = deviceSegmentKeys(devices, numCols)
	except ValueError:
		return None
	if not keys:
		return None
	number = focusSegment
	if number == -1 or not 0 <= number < len(keys):
		number = len(keys) - 1
	return driverNameForSegmentKey(keys[number])


def deviceBandSegmentRects(device: DeviceInfo, rect: SegmentRect) -> list[SegmentRect]:
	"""Divide one physical display's rows using that display's own settings.

	The settings it has when driven alone, so its division carries into a composite.

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


def deviceFallbackView(numRows: int, numCols: int, devices: Sequence[DeviceInfo]) -> SegmentView:
	"""Build the simplest arrangement a composite display can have.

	One segment per display, reading no settings, since a setting is the likeliest reason
	`deviceView` failed. Keeps the dead column mask, which is hardware. Keys are those
	`deviceView` gives a display's first segment, so a pin can survive the fallback.

	:param numRows: number of rows on the composite.
	:param numCols: number of columns on the composite, the widest display's width.
	:param devices: the physical displays, in stacking order, top first.
	:return: the view.
	:raises ValueError: if the bands do not tile a display of this size.
	"""
	if not devices:
		raise ValueError("A composite display needs at least one physical display")
	panels: list[BraillePanel] = []
	keys: list[str] = []
	for device, band in zip(devices, deviceBandRects([each.band for each in devices], numCols), strict=True):
		key = deviceSegmentKey(device.driverName, 0)
		panels.append(SinglePanel(key, band.live, reserve=False))
		keys.append(key)
		if band.dead is not None:
			panels.append(BlankPanel(band.dead, name=f"{DEVICE_PANEL_NAME}.{device.driverName}.dead"))
	view = SegmentView(name="devices.fallback", panels=panels, focusSegmentKey=keys[-1])
	view.validate(numRows, numCols)
	return view


def deviceView(
	numRows: int,
	numCols: int,
	devices: Sequence[DeviceInfo],
	displayKey: str | None = None,
) -> SegmentView:
	"""Build the view for a composite of several physical displays.

	Unlike `viewFromConfig`: always divided at the display boundaries, each display then by its
	own settings; and a narrower display's dead columns are claimed by a `BlankPanel` so no
	text is lost in them.

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
	# The one numbering, so that a command working out a segment number to store and this
	# view reading it back cannot disagree about which segment a number means.
	keys = deviceSegmentKeys(devices, numCols)
	position = 0
	for device, band in zip(devices, deviceBandRects([each.band for each in devices], numCols), strict=True):
		for rect in deviceBandSegmentRects(device, band.live):
			panels.append(
				# One panel per segment, as the configured view does and for the same reason:
				# a claim laid over the composite then evicts only the segments whose cells it
				# actually wants, leaving those on the other display with their keys.
				SinglePanel(keys[position], rect, reserve=False),
			)
			position += 1
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
