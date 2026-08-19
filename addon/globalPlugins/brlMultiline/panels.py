# BrlMultiline: panels and segment specifications.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Claims on the display, and the descriptions of the segments they contain.

A `BraillePanel` is a claim: one owner, one rectangle, and a rule for subdividing it. It
is the unit of composition. A table reader hands the plugin a panel saying "these rows are
mine, laid out as a grid" without knowing or caring what the rest of the display holds.

A `SegmentSpec` is the leaf a panel produces: one rectangle, a stable key, and the policy
the segment behaves by. The container builds one live `BrailleBufferSegment` per spec, in
display order.

Panels exist only while a view is being built. They are dissolved by `SegmentView.flatten`
into the flat list of specs the container works from, so no container operation ever has
to walk a tree. Panels remain reachable through the view for composing the next view and
for reporting the layout, but they carry no live state.

This module deliberately imports nothing from NVDA, so panel arithmetic can be unit tested
without a running screen reader or an attached display.
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


@dataclasses.dataclass(frozen=True)
class SegmentSpec:
	"""Everything needed to build one segment, and nothing that changes once it is built."""

	rect: SegmentRect
	"""The rectangle of the display this segment occupies."""

	key: str
	"""A stable identity for this segment, unique within a view.

	Indices are display order and change whenever the layout does. A key does not, so
	anything that has to survive a rebuild holds a key: which segment follows the focus,
	which segment a pinned object lives in, which segment a claim is waiting for.
	"""

	owner: str | None = None
	"""The panel that reserved this segment, or None if it is free.

	A free segment is one the add-on may fill with the document lines around the caret. A
	reserved one belongs to whoever claimed it and is left alone.
	"""

	hostsSystemFocus: bool = False
	"""Whether this segment is where NVDA's own focus content belongs.

	Reserving a segment and hosting the focus were one flag until a flow needed both at
	once. They are different questions: `owner` says who may draw here, and this says
	whether NVDA's untargeted regions land here. A flow band is reserved *and* hosts the
	focus, which is exactly the combination the old single flag could not express.
	"""

	exclusive: bool = False
	"""Whether the owner is the only producer of this segment's content.

	Set on a segment whose owner draws NVDA's focus content itself, as a flow does: the
	ordinary focus regions are handed to the owner instead of being written into the
	segment beside what the owner drew. Without it the two producers fight, because
	`_doNewObjectMultiSegment` clears and rewrites the focus segment on every focus change.
	"""

	fillRows: bool = False
	"""Fill each row completely, continuing mid word, rather than wrapping at word
	boundaries. Narrow segments have no cells to spare for keeping words whole."""

	markCuts: bool = False
	"""When filling rows, spend one cell on a continuation mark where a row was cut mid
	word. Ignored unless `fillRows` is set."""

	routingPolicy: RoutingPolicy = DEFAULT_ROUTING_POLICY
	"""What a cursor routing key press within this segment does."""

	documentContextIndex: int | None = None
	"""This segment's place in the reading order of the document lines feature.

	The distance between two of these is how many lines apart the segments read, so a
	segment two after the focus segment shows the line two after the caret.

	It cannot be derived from the display order of segments, because a panel may put
	several segments on one physical row: a three column grid consumes three indices per
	row band, which would inflate the offsets of everything below it. Nor can it be derived
	from the row, because a single row display divided into columns puts every segment on
	row 0 and still wants them to read consecutively. So it is stated rather than inferred.

	None means this segment takes no part in the document lines feature, which is the
	default and is what every claimed panel gets unless it opts in.
	"""

	@property
	def isReserved(self) -> bool:
		""":return: whether this segment belongs to a panel that claimed it."""
		return self.owner is not None

	@property
	def ownerDrawsFocus(self) -> bool:
		""":return: whether the focus content here is the owner's to draw, not NVDA's."""
		return self.hostsSystemFocus and self.exclusive

	@property
	def hasDocumentContext(self) -> bool:
		""":return: whether this segment takes part in the document lines feature."""
		return self.documentContextIndex is not None


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
		documentContextIndex: int | None = None,
		hostsSystemFocus: bool = False,
		exclusive: bool = False,
	) -> None:
		"""
		:param name: who this panel belongs to. Used as the prefix of its segment keys, so
			it must be unique within a view.
		:param rect: the rectangle claimed, in display coordinates.
		:param reserve: whether the segments produced are reserved for this panel's owner.
			Reserved segments are left alone by the document lines feature. Pass False for
			a panel whose segments are ordinary display space, as the configured view does.
		:param fillRows: the wrapping rule handed to this panel's segments.
		:param markCuts: whether cut rows show a continuation mark, when filling rows.
		:param routingPolicy: what a routing key press does within this panel's segments.
			Defaults to routing within the segment pressed.
		:param focusSegmentKey: the segment that should follow the system focus if this
			panel is composed into a view that loses its own focus segment. None means this
			panel cannot host the focus, so such a composition is refused.
		:param documentContextIndex: this panel's place in the reading order of the
			document lines feature. None, the default, means its segments take no part in
			it, which is what a claimed panel usually wants. See
			L{SegmentSpec.documentContextIndex}.
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
		self.documentContextIndex = documentContextIndex
		self.hostsSystemFocus = hostsSystemFocus
		self.exclusive = exclusive

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
		documentContextIndex: int | None = None,
	) -> SegmentSpec:
		"""Build one segment specification carrying this panel's defaults.

		:param suffix: distinguishes this segment from the panel's others. Combined with
			the panel name to form the key. Empty for a panel holding a single segment,
			whose key is then the panel name itself.
		:param rect: the rectangle the segment occupies, in display coordinates.
		:param documentContextIndex: this segment's own place in the document reading
			order, for a panel whose segments each need a different one. None falls back to
			the panel's.
		:return: the specification.
		"""
		return SegmentSpec(
			rect=rect,
			key=f"{self.name}.{suffix}" if suffix else self.name,
			owner=self.name if self.reserve else None,
			fillRows=self.fillRows,
			markCuts=self.markCuts,
			routingPolicy=self.routingPolicy,
			documentContextIndex=(
				documentContextIndex if documentContextIndex is not None else self.documentContextIndex
			),
			hostsSystemFocus=self.hostsSystemFocus,
			exclusive=self.exclusive,
		)

	def validate(self) -> None:
		"""Check this panel's segments fit inside its own claim and do not overlap.

		Gaps between a panel's segments are allowed, and show as blank cells. They belong
		to this panel, which is what lets a panel put space between its segments without
		leaving cells unowned.

		:raises ValueError: if a segment falls outside the claim or overlaps another.
		"""
		specs = self.segments()
		# Containment is checked first, because a segment that has escaped its panel would
		# otherwise be reported as running past the edge of a display it was never measured
		# against, which says nothing about what actually went wrong.
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

	Its segment takes the panel's own name as its key, since there is no sibling to tell it
	apart from. A view built of one such panel per segment is therefore the finest grained
	view there is, and that is what the configured view uses: a claim laid over it evicts
	only the segments it actually covers, leaving the others with their keys intact.
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

	Produces no segments at all, so no buffer is built and the cells composite as blank.
	Used to give ownership to space that no other panel wants, which is what lets the
	coverage rule hold without every view builder doing the arithmetic.
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


class FlowPanel(BraillePanel):
	"""A band of rows presented as one continuous flow of content.

	Geometry and ownership only. What is shown in the band, how blocks are packed into its
	rows and where the window sits are the flow controller's business; this exists so that
	a flow can claim its rows through the same mechanism as everything else.

	Two arrangements, and the difference is decision 1 of the spatial reading plan:

	1. **The focus flow** hosts the system focus and draws it itself, so it is reserved,
		`hostsSystemFocus` and `exclusive` all at once. NVDA's untargeted focus regions are
		handed to the controller rather than written into the segment beside what it drew.
	2. **A viewer band** shows content the reader is not working in. It is reserved and
		nothing else, shows no cursor, and moves nothing outside itself.

	The band is one segment covering the whole claim, because the flow does its own row
	assembly: `flow.assembleCells` pads each row to the band's width, which is what stops
	the block after a short one sharing its row. Subdividing the band into a segment per
	row would hand that job back to NVDA's buffer, which cannot do it.
	"""

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
			band, which `views.validateAgainstHardware` enforces: a full width band across
			a Monarch's rows in a Monarch and Focus composite would reach the Monarch's
			dead columns and is refused.
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

	Geometry survives a rebuild and content does not: a claim is re-composed and comes back
	empty, with nothing telling its owner to draw again. That is recoverable for a pinned
	object, which is re-read from its object, and is a permanently blank band for a flow.
	These are the calls that close the gap.

	Implemented by the flow controller, and available to any other owner. The base does
	nothing, so an owner may override only what it cares about.
	"""

	def onRebuilt(self, keys: frozenset[str]) -> None:
		"""The display was rebuilt and this owner's segments are empty again.

		:param keys: the keys of this owner's segments in the new arrangement.
		"""

	def onEvicted(self, keys: frozenset[str]) -> None:
		"""This owner's claim no longer fits, and these segments have gone.

		An owner told this must stop producing regions targeted at them. Until now eviction
		was a log line, and an owner went on addressing segments that had departed.

		:param keys: the keys that are gone.
		"""

	def onTerminate(self) -> None:
		"""The add-on is shutting down, or this owner's claim has been given back."""
