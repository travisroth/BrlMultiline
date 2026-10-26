# BrlMultiline: panels and segment specifications.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Claims on the display, and the descriptions of the segments they contain.

A `BraillePanel` is a claim: one owner, one rectangle, and a rule for subdividing it. A
`SegmentSpec` is the leaf a panel produces: one rectangle, a stable key and a policy; the
container builds one live `BrailleBufferSegment` per spec.

`SegmentView.flatten` dissolves panels into the flat list the container works from. Panels
stay reachable through the view but carry no live state. Imports nothing from NVDA.
"""

import dataclasses

from .layout import (
	SegmentRect,
	calculateGridRectsIn,
	calculateSegmentRectsIn,
	rectContains,
	validateRects,
)
from .routing import DEFAULT_ROUTING_POLICY, RoutingPolicy

BLANK_PANEL_NAME = "blank"
"""Name given to the filler panels that claim cells nothing else wants."""

PRIORITY_ORDINARY = 0
PRIORITY_FLOW = 10
PRIORITY_APP_LAYOUT = 15
PRIORITY_GRAPHICS = 20
"""The ladder that decides which claim wins when two want the same cells.

Claims are composed lowest first, so the highest wins; ties keep the order they were made in.
Graphics is deliberate and temporary, so it outranks an app layout, which outranks the flow, a
standing preference that resumes by itself.
"""


@dataclasses.dataclass(frozen=True)
class SegmentSpec:
	"""Everything needed to build one segment, and nothing that changes once it is built."""

	rect: SegmentRect
	"""The rectangle of the display this segment occupies."""

	key: str
	"""A stable identity for this segment, unique within a view.

	Indices change with the layout and a key does not, so anything surviving a rebuild holds a
	key.
	"""

	owner: str | None = None
	"""The panel that reserved this segment, or None if it is free.

	A free segment is ordinary display space, which a pinned object may be put in. A reserved
	one belongs to whoever claimed it and is left alone.
	"""

	hostsSystemFocus: bool = False
	"""Whether this segment is where NVDA's own focus content belongs.

	Separate from `owner`, which says who may draw here; a flow band is both.
	"""

	exclusive: bool = False
	"""Whether the owner is the only producer of this segment's content.

	For an owner that draws the focus itself, as a flow does: the focus regions are handed to
	it, or `_doNewObjectMultiSegment` would rewrite the segment on every focus change.
	"""

	fillRows: bool = False
	"""Fill each row completely, continuing mid word, rather than wrapping at word
	boundaries. Narrow segments have no cells to spare for keeping words whole."""

	markCuts: bool = False
	"""When filling rows, spend one cell on a continuation mark where a row was cut mid
	word. Ignored unless `fillRows` is set."""

	routingPolicy: RoutingPolicy = DEFAULT_ROUTING_POLICY
	"""What a cursor routing key press within this segment does."""

	@property
	def isReserved(self) -> bool:
		""":return: whether this segment belongs to a panel that claimed it."""
		return self.owner is not None

	@property
	def ownerDrawsFocus(self) -> bool:
		""":return: whether the focus content here is the owner's to draw, not NVDA's."""
		return self.hostsSystemFocus and self.exclusive


class BraillePanel:
	"""A claim on a rectangle of the display, and the rule for subdividing it.

	Subclasses decide the subdivision by implementing L{segments}. Everything else, the
	policy defaults handed down to those segments and the validation of the result, is
	provided here.
	"""

	def __init__(
		self,
		name: str,
		rect: SegmentRect,
		*,
		reserve: bool = True,
		fillRows: bool = False,
		markCuts: bool = False,
		routingPolicy: RoutingPolicy | None = None,
		focusSegmentKey: str | None = None,
		hostsSystemFocus: bool = False,
		exclusive: bool = False,
	) -> None:
		"""
		:param name: who this panel belongs to. Used as the prefix of its segment keys, so
			it must be unique within a view.
		:param rect: the rectangle claimed, in display coordinates.
		:param reserve: whether the segments produced are reserved for this panel's owner.
			Pass False for a panel whose segments are ordinary display space, as the
			configured view does.
		:param fillRows: the wrapping rule handed to this panel's segments.
		:param markCuts: whether cut rows show a continuation mark, when filling rows.
		:param routingPolicy: what a routing key press does within this panel's segments.
			Defaults to routing within the segment pressed.
		:param focusSegmentKey: the segment that should follow the system focus if this
			panel is composed into a view that loses its own focus segment. None means this
			panel cannot host the focus, so such a composition is refused.
		:param hostsSystemFocus: whether this panel's segments are where NVDA's own focus
			content belongs. A panel offering a `focusSegmentKey` is saying which segment
			*could* host the focus; this says its segments actually do.
		:param exclusive: whether this panel's owner is the only producer of its segments'
			content, drawing the focus itself rather than sharing the segment with NVDA's
			untargeted regions.
		"""
		self.name = name
		self.rect = rect
		self.reserve = reserve
		self.fillRows = fillRows
		self.markCuts = markCuts
		self.routingPolicy = routingPolicy if routingPolicy is not None else DEFAULT_ROUTING_POLICY
		self.focusSegmentKey = focusSegmentKey
		self.hostsSystemFocus = hostsSystemFocus
		self.exclusive = exclusive

	priority: int = PRIORITY_ORDINARY
	"""Which claim wins where two want the same cells. See the priority constants above."""

	def segments(self) -> list[SegmentSpec]:
		"""Subdivide this panel's claim.

		:return: one specification per segment, in display coordinates. May be empty, for
			a panel that claims cells in order to keep them blank.
		"""
		raise NotImplementedError

	def buildSpec(
		self,
		suffix: str,
		rect: SegmentRect,
	) -> SegmentSpec:
		"""Build one segment specification carrying this panel's defaults.

		:param suffix: distinguishes this segment from the panel's others. Combined with
			the panel name to form the key. Empty for a panel holding a single segment,
			whose key is then the panel name itself.
		:param rect: the rectangle the segment occupies, in display coordinates.
		:return: the specification.
		"""
		return SegmentSpec(
			rect=rect,
			key=f"{self.name}.{suffix}" if suffix else self.name,
			owner=self.name if self.reserve else None,
			fillRows=self.fillRows,
			markCuts=self.markCuts,
			routingPolicy=self.routingPolicy,
			hostsSystemFocus=self.hostsSystemFocus,
			exclusive=self.exclusive,
		)

	def validate(self) -> None:
		"""Check this panel's segments fit inside its own claim and do not overlap.

		Gaps between segments are allowed, blank and owned by this panel.

		:raises ValueError: if a segment falls outside the claim or overlaps another.
		"""
		specs = self.segments()
		# Containment first, so an escaped segment is reported as that.
		for spec in specs:
			if not rectContains(self.rect, spec.rect):
				raise ValueError(
					f"Segment {spec.key!r} at {spec.rect} falls outside panel {self.name!r} at {self.rect}",
				)
		if specs:
			# Every segment is now known to sit inside the claim, so treating the claim's
			# far edge as the bounds only leaves overlaps to find.
			validateRects([spec.rect for spec in specs], self.rect.endRow, self.rect.endCol)
		if self.focusSegmentKey is not None and self.focusSegmentKey not in {spec.key for spec in specs}:
			raise LookupError(
				f"Panel {self.name!r} offers {self.focusSegmentKey!r} as its focus segment, "
				f"but has no segment with that key",
			)

	def __repr__(self) -> str:
		return f"<{type(self).__name__} {self.name!r} at {self.rect}>"


class SinglePanel(BraillePanel):
	"""A panel holding one segment that fills its whole claim.

	Its segment's key is the panel's name. The configured view uses one per segment, so a claim
	evicts only the segments it covers.
	"""

	def segments(self) -> list[SegmentSpec]:
		return [self.buildSpec("", self.rect)]


class RowsPanel(BraillePanel):
	"""A panel divided into row groups spanning its full width.

	On a claim one row tall the division is by columns instead, matching what
	`calculateSegmentRects` does for a single row display.
	"""

	def __init__(self, name: str, rect: SegmentRect, layout: int | list[int], **kwargs) -> None:
		"""
		:param name: who this panel belongs to.
		:param rect: the rectangle claimed.
		:param layout: a count of segments to divide evenly into, or explicit sizes,
			measured in rows unless the claim is a single row.
		:raises ValueError: if the layout does not fit the claim.
		"""
		super().__init__(name, rect, **kwargs)
		self.layout = layout
		self._rects = calculateSegmentRectsIn(rect, layout)

	def segments(self) -> list[SegmentSpec]:
		return [self.buildSpec(str(index), rect) for index, rect in enumerate(self._rects)]


class GridPanel(BraillePanel):
	"""A panel divided into a grid of cells.

	For code driving the display directly: a table reader might claim six rows and divide
	them into four columns of 8 cells across three bands of 2 rows. Segment keys are named
	by position, so `table.r1c2` is the third cell of the second band whichever view it
	ends up in.
	"""

	def __init__(
		self,
		name: str,
		rect: SegmentRect,
		rowBands: list[int],
		colWidths: list[int],
		**kwargs,
	) -> None:
		"""
		:param name: who this panel belongs to.
		:param rect: the rectangle claimed.
		:param rowBands: heights of each band, in rows, top to bottom.
		:param colWidths: widths of each column, in cells, left to right.
		:raises ValueError: if the grid does not fit the claim.
		"""
		self.rowBands = list(rowBands)
		self.colWidths = list(colWidths)
		self._rects = calculateGridRectsIn(rect, self.rowBands, self.colWidths)
		kwargs.setdefault("fillRows", True)
		kwargs.setdefault("focusSegmentKey", f"{name}.r0c0")
		super().__init__(name, rect, **kwargs)

	def segments(self) -> list[SegmentSpec]:
		specs = []
		for index, rect in enumerate(self._rects):
			band, column = divmod(index, len(self.colWidths))
			specs.append(self.buildSpec(f"r{band}c{column}", rect))
		return specs

	def keyFor(self, band: int, column: int) -> str:
		"""Find the key of one grid cell, for targeting regions at it.

		:param band: index of the row band, from the top.
		:param column: index of the column, from the left.
		:return: the segment key.
		:raises LookupError: if there is no such cell.
		"""
		if not (0 <= band < len(self.rowBands) and 0 <= column < len(self.colWidths)):
			raise LookupError(
				f"Panel {self.name!r} has no cell at band {band}, column {column}; "
				f"it is {len(self.rowBands)} by {len(self.colWidths)}",
			)
		return f"{self.name}.r{band}c{column}"


class BlankPanel(BraillePanel):
	"""A panel that claims cells in order to keep them blank.

	Produces no segments, so the cells composite as blank and the coverage rule holds.
	"""

	def __init__(self, rect: SegmentRect, name: str | None = None) -> None:
		"""
		:param rect: the rectangle claimed.
		:param name: a name for the claim. Defaults to naming it after its position, which
			keeps the blank panels of one view distinguishable from each other.
		"""
		super().__init__(name or f"{BLANK_PANEL_NAME}.r{rect.row}c{rect.col}", rect, reserve=True)

	def segments(self) -> list[SegmentSpec]:
		return []


class GraphicsPanel(BraillePanel):
	"""A claim on a band of the display: a drawing, and a line of braille beside it.

	Two segments. The **drawing** segment is reserved and never written into, so it
	composites as blank and the overlay the driver holds shows through. The **text**
	segment is an ordinary line of braille that hosts the system focus.

	The text line is this panel's own because `SegmentView.withPanel` evicts every panel a claim
	touches, focus segment included; offering it as `focusSegmentKey` is how a claim may take a
	focus segment. The drawing is sent to the driver as an overlay, not through the container.
	The blank drawing segment exists to carry a routing policy, since a press reaches the
	add-on only through a segment (see `DisplayContainer.routeTo`).
	"""

	priority = PRIORITY_GRAPHICS

	TEXT_SUFFIX = "text"
	DRAWING_SUFFIX = "drawing"
	"""Segment key suffixes, so an owner can name either without composing strings."""

	def __init__(
		self,
		name: str,
		rect: SegmentRect,
		*,
		textRows: int = 1,
		drawingRoutingPolicy: RoutingPolicy | None = None,
	) -> None:
		"""
		:param name: who this panel belongs to, and the prefix of its segment keys.
		:param rect: the whole band claimed, drawing and text line together.
		:param textRows: rows at the top of the claim kept as ordinary braille. Zero gives the
			whole claim to the drawing, which then hosts the focus as `exclusive` and draws none
			of it. The reader has to ask for that.
		:param drawingRoutingPolicy: what a routing press inside the drawing does. None
			leaves it routing as any other segment would, which is what a press on blank
			cells does: nothing.
		"""
		self.textRows = max(0, min(textRows, rect.numRows))
		focusKey = f"{name}.{self.TEXT_SUFFIX}" if self.textRows else f"{name}.{self.DRAWING_SUFFIX}"
		super().__init__(
			name,
			rect,
			reserve=True,
			focusSegmentKey=focusKey,
		)
		self.drawingRoutingPolicy = drawingRoutingPolicy or DEFAULT_ROUTING_POLICY

	@property
	def drawingRect(self) -> SegmentRect:
		":return: the part of the claim the drawing occupies."
		return SegmentRect(
			row=self.rect.row + self.textRows,
			col=self.rect.col,
			numRows=self.rect.numRows - self.textRows,
			numCols=self.rect.numCols,
		)

	def segments(self) -> list[SegmentSpec]:
		"""Built directly rather than through `buildSpec`, because the two differ in every
		flag that matters: one is ordinary display space hosting the focus, the other is
		reserved space nothing may write into.
		"""
		specs = []
		if self.textRows:
			specs.append(
				SegmentSpec(
					rect=SegmentRect(
						row=self.rect.row,
						col=self.rect.col,
						numRows=self.textRows,
						numCols=self.rect.numCols,
					),
					key=f"{self.name}.{self.TEXT_SUFFIX}",
					# Ordinary space, deliberately: no owner, so a pinned object may use it,
					# and hosting the focus so NVDA's untargeted regions land here rather
					# than under the drawing where they would be drawn over and lost.
					owner=None,
					hostsSystemFocus=True,
					routingPolicy=DEFAULT_ROUTING_POLICY,
				),
			)
		drawing = self.drawingRect
		if drawing.numRows > 0:
			specs.append(
				SegmentSpec(
					rect=drawing,
					key=f"{self.name}.{self.DRAWING_SUFFIX}",
					owner=self.name,
					# With no text line the drawing is the only segment, so it has to host the
					# focus — a view must have somewhere for untargeted content to land. Exclusive
					# with it, so that content is handed to the owner and dropped rather than
					# written into cells the figure is about to be composited over.
					hostsSystemFocus=not self.textRows,
					exclusive=not self.textRows,
					routingPolicy=self.drawingRoutingPolicy,
				),
			)
		return specs


class FlowPanel(BraillePanel):
	"""A band of rows presented as one continuous flow of content.

	Geometry and ownership only; the content is the flow controller's. Two arrangements, as in
	decision 1 of docs/design/spatial-reading-plan.md:

	1. **The focus flow** hosts the system focus and draws it itself, so it is reserved,
		`hostsSystemFocus` and `exclusive` all at once. NVDA's untargeted focus regions are
		handed to the controller rather than written into the segment beside what it drew.
	2. **A viewer band** shows content the reader is not working in. It is reserved and
		nothing else, shows no cursor, and moves nothing outside itself.

	One segment, since the flow assembles its own rows; see `flow.assembleCells`.
	"""

	priority = PRIORITY_FLOW

	def __init__(
		self,
		name: str,
		rect: SegmentRect,
		*,
		hostsSystemFocus: bool = True,
		routingPolicy: RoutingPolicy | None = None,
	) -> None:
		"""
		:param name: who this band belongs to, and the key of its single segment.
		:param rect: the rectangle claimed. It must lie within one physical display's live
			band, as `views.validateAgainstHardware` enforces.
		:param hostsSystemFocus: True for the focus flow, False for a viewer band.
		:param routingPolicy: what a routing key press in the band does. The flow's own
			policy maps a pressed cell back through the window to a position in a block,
			and does nothing for padding.
		"""
		super().__init__(
			name,
			rect,
			reserve=True,
			routingPolicy=routingPolicy,
			focusSegmentKey=name if hostsSystemFocus else None,
			hostsSystemFocus=hostsSystemFocus,
			exclusive=hostsSystemFocus,
		)

	def segments(self) -> list[SegmentSpec]:
		return [self.buildSpec("", self.rect)]


class PanelOwner:
	"""What an owner of a claim must answer, so that its content survives the display changing.

	A claim comes back empty after a rebuild; these calls tell its owner to draw again. The
	base does nothing, so an owner overrides only what it needs.
	"""

	def onRebuilt(self, keys: frozenset[str]) -> None:
		"""The display was rebuilt and this owner's segments are empty again.

		:param keys: the keys of this owner's segments in the new arrangement.
		"""

	def onEvicted(self, keys: frozenset[str]) -> None:
		"""This owner's claim no longer fits, and these segments have gone.

		An owner told this must stop producing regions targeted at them.

		:param keys: the keys that are gone.
		"""

	def onTerminate(self) -> None:
		"""The add-on is shutting down, or this owner's claim has been given back."""
