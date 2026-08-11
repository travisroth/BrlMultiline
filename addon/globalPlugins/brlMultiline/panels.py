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
		"""
		self.name = name
		self.rect = rect
		self.reserve = reserve
		self.fillRows = fillRows
		self.markCuts = markCuts
		self.routingPolicy = routingPolicy if routingPolicy is not None else DEFAULT_ROUTING_POLICY
		self.focusSegmentKey = focusSegmentKey

	def segments(self) -> list[SegmentSpec]:
		"""Subdivide this panel's claim.

		:return: one specification per segment, in display coordinates. May be empty, for
			a panel that claims cells in order to keep them blank.
		"""
		raise NotImplementedError

	def buildSpec(self, suffix: str, rect: SegmentRect) -> SegmentSpec:
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
