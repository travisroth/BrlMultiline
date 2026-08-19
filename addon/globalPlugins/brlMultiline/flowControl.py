# BrlMultiline: driving a flow.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""What holds a window, a source and a renderer together.

The three pieces below it each know one thing and nothing about each other: `flow` knows
how rows are packed and where the window sits, `flowSources` knows how to find blocks and
read them, `flowRender` knows how to cut one block into rows. The controller is the only
place that knows all three, and it is deliberately the only place with a fetch loop in it.

Fetching is on demand and nowhere else. `FlowWindow` raises `ContentNeeded` when a move
would run off the cached rows, the controller asks the source for one more block, renders
it, and tries again — stopping when the window is full, when the source says the stream
ends, or when the budget says to show what we have. Nothing reads ahead speculatively,
which is what keeps a page with expensive accessibility calls from being walked further
than the display can show.

The controller also owns the two things a flow must be exact about:

1. **One active block.** The block holding the cursor is the only one that exposes a
	braille cursor, and the only one NVDA's commands may act on. See
	[flow-last-region-audit.md](../../../docs/design/flow-last-region-audit.md).
2. **Who moves.** In a live flow, panning moves the browse mode cursor to the top block of
	the new window, so speech and braille agree and routing can still activate a link. A
	viewer moves nothing outside its own band.
"""

from typing import TYPE_CHECKING, Optional

from logHandler import log

from .flow import (
	ByIdentity,
	ContentNeeded,
	Edge,
	EdgeState,
	FlowWindow,
	ResultKind,
	RowKind,
	assembleCells,
	cellSource,
)
from .panels import PanelOwner

if TYPE_CHECKING:
	from .flow import BlockId
	from .flowRender import FlowRenderer
	from .flowSources import DocumentFlowSource

MAX_FETCHES = 24
"""How many blocks one operation may add before giving up.

A backstop under the source's own budget, for the case where a source keeps answering with
blocks that add no rows. Never reached in ordinary reading: filling a Monarch from nothing
costs eight.
"""


class FlowController(PanelOwner):
	"""Drives one band of flowing content."""

	def __init__(
		self,
		source: "DocumentFlowSource",
		renderer: "FlowRenderer",
		numRows: int,
		live: bool = False,
	) -> None:
		"""
		:param source: where the blocks come from.
		:param renderer: how a block becomes rows.
		:param numRows: the height of the band.
		:param live: whether this flow is the focus segment, and so moves the real cursor.
		"""
		self.source = source
		self.renderer = renderer
		self.window = FlowWindow(numRows)
		self.live = live
		self.activeBlockId: Optional["BlockId"] = None
		"""The block the cursor is in, and the only one that may show a cursor."""

		self.lastResult = None
		"""The source's last answer, so a caller can say why nothing appeared."""

		self.blocks = ByIdentity()
		"""The source block behind each rendering, by identity, for its region.

		Not a dictionary: a `BlockId` holds a bookmark, and a bookmark compares but does not
		hash. See `flow.ByIdentity`."""

	# Arriving.

	def enterAtCursor(self, contextRows: int = 0) -> bool:
		"""Place the window afresh at the cursor.

		Used on arrival and whenever the window has to be abandoned: a focus change, a
		jump, or a recovery that found nothing to hold on to.

		:param contextRows: how many rows of what precedes the cursor to show above it. A
			plain document asks for none, so the cursor's block sits at the top and the
			display fills downward.
		:return: whether anything is now on the display.
		"""
		result = self.source.blockAtCursor()
		self.lastResult = result
		if result.kind is not ResultKind.BLOCK or result.block is None:
			log.debugWarning(f"A flow could not enter at the cursor: {result.kind} {result.message}")
			return False
		self.window.blocks.clear()
		self.blocks.clear()
		self.window.setEdge(Edge.BEFORE, EdgeState.OPEN)
		self.window.setEdge(Edge.AFTER, EdgeState.OPEN)
		self._keep(result.block)
		self.window.appendBlock(self.renderer.render(result.block))
		self.window.enterAt(result.block.blockId)
		self._setActive(result.block.blockId)
		if contextRows > 0:
			self._fill(Edge.BEFORE, contextRows)
			self.window.enterAt(result.block.blockId, contextRows=contextRows)
		self.fill()
		return True

	# Filling.

	def fill(self) -> None:
		"""Fetch whatever the window is short of, at both ends."""
		for edge in (Edge.AFTER, Edge.BEFORE):
			shortfall = self.window.shortfall(edge)
			if shortfall:
				self._fill(edge, shortfall)

	def _fill(self, edge: Edge, rows: int) -> bool:
		"""Fetch until the window is no longer short at one end.

		:param edge: which end to fetch at.
		:param rows: how many rows are wanted.
		:return: whether the shortfall was made up.
		"""
		for _ in range(MAX_FETCHES):
			if self.window.shortfall(edge) <= 0:
				return True
			if not self._fetchOne(edge):
				return False
		log.debugWarning(f"A flow stopped fetching {edge.value} after {MAX_FETCHES} blocks")
		return False

	def _fetchOne(self, edge: Edge) -> bool:
		"""Ask the source for one more block at one end.

		:param edge: which end to extend.
		:return: whether a block was added. False closes that end, with the reason recorded
			on the window so that the display can tell the end of a document from a budget
			stop.
		"""
		blocks = self.window.blocks
		if not blocks:
			return False
		anchorId = blocks[0].blockId if edge is Edge.BEFORE else blocks[-1].blockId
		result = (
			self.source.blockBefore(anchorId) if edge is Edge.BEFORE else self.source.blockAfter(anchorId)
		)
		if result.kind is not ResultKind.BLOCK or result.block is None:
			# END, DEFERRED and ERROR each mean something different on the display: the end
			# of the stream reads as blank rows, the other two as rows that say there is
			# more we have not got.
			self.window.setEdge(edge, result.edgeState)
			return False
		self._keep(result.block)
		rendered = self.renderer.render(result.block)
		if edge is Edge.BEFORE:
			self.window.prependBlock(rendered)
		else:
			self.window.appendBlock(rendered)
		return True

	def _keep(self, block) -> None:
		"""Remember a source block, so its region can be reached from its identity."""
		self.blocks.set(block.blockId, block)

	def regionFor(self, blockId: "BlockId"):
		""":return: the region reading one block, or None if it is not held."""
		block = self.blocks.get(blockId)
		return getattr(block, "region", None)

	# Moving.

	def panForward(self) -> bool:
		"""Move the window on by a whole display, fetching if it has to."""
		return self._pan(forward=True)

	def panBack(self) -> bool:
		"""Move the window back by a whole display, fetching if it has to."""
		return self._pan(forward=False)

	def _pan(self, forward: bool) -> bool:
		"""Pan, answering `ContentNeeded` by fetching and trying again.

		:param forward: the direction to pan.
		:return: whether the window moved.
		"""
		for _ in range(MAX_FETCHES):
			try:
				moved = self.window.panForward() if forward else self.window.panBack()
			except ContentNeeded as needed:
				if self._fetchOne(needed.edge):
					continue
				# Nothing more that way: the window stays where it is rather than sliding
				# onto rows that are not there.
				return False
			if moved:
				self.fill()
				self._cursorToTop()
			return moved
		return False

	def stepBlock(self, forward: bool) -> bool:
		"""Move one block, as NVDA's next and previous line commands do.

		They move by a reading unit, which is a block here, and take the cursor with them.
		They also act on `regions[-1]`, which in a flow is the block at the bottom of the
		window rather than the one the cursor is in, so this steps from the active block
		instead.

		:param forward: True for the next block, False for the previous.
		:return: whether the cursor moved.
		"""
		if self.activeBlockId is None:
			return False
		nextId = self.window.stepBlock(forward, fromBlockId=self.activeBlockId)
		if nextId is None:
			edge = Edge.AFTER if forward else Edge.BEFORE
			if not self._fetchOne(edge):
				return False
			nextId = self.window.stepBlock(forward, fromBlockId=self.activeBlockId)
			if nextId is None:
				return False
		self._setActive(nextId)
		self.syncToCursor(forward=forward)
		return True

	def syncToCursor(self, forward: bool = True) -> bool:
		"""Bring the active block onto the display, moving as little as possible.

		If it is already there, nothing moves at all: landing on a heading that is already
		under the reader's fingers must not jerk the display.

		:param forward: the direction the reader is travelling.
		:return: whether the window moved.
		"""
		if self.activeBlockId is None:
			return False
		try:
			moved = self.window.ensureVisible(self.activeBlockId, forward=forward)
		except LookupError:
			return self.enterAtCursor()
		if moved:
			self.fill()
		return moved

	def _cursorToTop(self) -> None:
		"""Put the cursor on the top block of the window, after a pan.

		Reading onward with the arrow keys then continues from the top of what is under the
		reader's hands, in both directions. A viewer moves nothing, so it only records
		which block is active.
		"""
		topId = self.window.topBlockId()
		if topId is None:
			return
		self._setActive(topId)
		if not self.live:
			return
		region = self.regionFor(topId)
		takeCursor = getattr(region, "takeCursor", None)
		if takeCursor is not None:
			takeCursor()

	def _setActive(self, blockId: "BlockId") -> None:
		"""Make one block the active one, and no other.

		Every block holds a collapsed position, so every block looks to NVDA like it holds
		a cursor. Only the active one is allowed to say so.
		"""
		self.activeBlockId = blockId
		for block in self.blocks.values():
			region = getattr(block, "region", None)
			if region is None:
				continue
			region.isActive = block.blockId == blockId

	def refreshActive(self) -> bool:
		"""Re-render the block the cursor is in, after it changed under the reader.

		Only that block, because that is the one NVDA marks as pending on the core cycle.

		:return: whether the rendering changed.
		"""
		if self.activeBlockId is None:
			return False
		block = self.blocks.get(self.activeBlockId)
		if block is None:
			return False
		try:
			block.region.update()
		except Exception:
			log.debugWarning(f"Could not refresh {self.activeBlockId}", exc_info=True)
			return False
		rendered = self.renderer.render(block)
		try:
			before = self.window.blocks[self.window.blockIndex(self.activeBlockId)]
		except LookupError:
			return False
		if rendered.rows == before.rows:
			return False
		self.window.replaceBlock(rendered)
		self.fill()
		return True

	# Showing.

	def cells(self) -> list[int]:
		"""The band's cells, row major, padded to the full width.

		The padding is the point: it is what stops the block after a short one sharing its
		row, which NVDA's own buffer cannot avoid.
		"""
		try:
			return assembleCells(self.window, self.renderer.numCols)
		except LookupError:
			return [0] * (self.window.numRows * self.renderer.numCols)

	def cursorCell(self) -> Optional[int]:
		"""Where the cursor is within the band, as a flat row major position.

		:return: the position, or None if the active block is not on the display or has no
			cursor in it.
		"""
		if self.activeBlockId is None:
			return None
		region = self.regionFor(self.activeBlockId)
		at = getattr(region, "brailleCursorPos", None)
		if at is None:
			return None
		numCols = self.renderer.numCols
		for position in range(self.window.numRows * numCols):
			source = cellSource(self.window, numCols, position)
			if source is not None and source[0] == self.activeBlockId and source[1] == at:
				return position
		return None

	def routeTo(self, position: int) -> bool:
		"""Act on a routing key press within the band.

		:param position: a flat row major position within the band.
		:return: whether the press reached content. Padding, gaps and blank rows reach
			nothing, and must do nothing: NVDA's habit of mapping trailing blank columns
			back to the last content cell would otherwise activate the end of a short field
			when the reader pressed a key in the space after it.
		"""
		source = cellSource(self.window, self.renderer.numCols, position)
		if source is None:
			return False
		blockId, at = source
		region = self.regionFor(blockId)
		if region is None:
			return False
		self._setActive(blockId)
		try:
			region.routeTo(at)
		except Exception:
			log.debugWarning(f"Could not route into {blockId}", exc_info=True)
			return False
		return True

	def describeRows(self) -> list[str]:
		"""What each row of the band holds, in words rather than cells.

		For the dry run command and for the log: it shows the packing and the window
		without a display attached, which is where the design is easiest to get wrong.

		:return: one line per row of the band.
		"""
		numCols = self.renderer.numCols
		lines: list[str] = []
		try:
			visible = self.window.visibleRows()
		except LookupError:
			return ["(no anchor)"]
		for index, row in enumerate(visible):
			if row.kind is RowKind.BLANK:
				lines.append(f"{index}: (end of content)")
				continue
			if row.kind is RowKind.PENDING:
				lines.append(f"{index}: (more, not fetched)")
				continue
			if row.kind is RowKind.GAP or row.blockId is None:
				lines.append(f"{index}: (gap)")
				continue
			region = self.regionFor(row.blockId)
			text = getattr(region, "rawText", "")
			rendered = self.window.blocks[self.window.blockIndex(row.blockId)]
			cells = rendered.rows[row.rowIndex] if row.rowIndex < len(rendered.rows) else ()
			active = " *" if row.blockId == self.activeBlockId else "  "
			lines.append(
				f"{index}:{active}[{len(cells)}/{numCols} cells] "
				f"block {row.rowIndex + 1} of {rendered.numRows}: {text[:60]!r}",
			)
		return lines

	def __repr__(self) -> str:
		flavour = "live" if self.live else "viewer"
		return f"<FlowController {flavour} {self.window!r}>"
