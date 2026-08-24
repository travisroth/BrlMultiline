# BrlMultiline: the row stream a flow presents.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Packing blocks into rows, and the window that moves over them.

A flow shows a run of content across several rows of the display: a document read straight
through rather than one line at a time. The unit is a **block** — a reading unit of a
document, a control with its label, an item in a list — and the rule that makes a flow
read the way a page does is that a block always starts on a new **row**, and never starts
a new window.

NVDA's own buffer cannot express that rule. `BrailleBuffer.update` concatenates the cells
of every region into one flat list and cuts rows out of the result afterwards, so a ten
cell heading followed by a paragraph puts the paragraph at cell eleven of the same row.
Padding each region to a multiple of the display width does not help either, because word
wrapping means rendered rows are not all the same length. So a flow renders each block
separately and assembles the rows itself, which is what this module does.

This module deliberately imports nothing from NVDA. Everything here works on
`RenderedBlock` values — cells and their mapping back to positions, already translated —
so the packing, the window arithmetic and the recovery rules can be unit tested without a
running screen reader or an attached display. Producing a `RenderedBlock` from an object
is `flowRender`'s job, and finding the blocks is `flowSources`'.

The window is held as an anchor and the direction it was entered from, not as a page
number. Panning forward puts the row after the current bottom row at the **top** of the
window; panning back puts the row before the current top row at the **bottom**. That
symmetry is what makes panning reversible: re-entering a long paragraph from below shows
its tail rather than an aligned window somewhere in its middle. It is the rule
`DisplayContainer._panPinnedContent` already applies to a single region, generalised to a
run of blocks.

Panning forward and back are exact inverses only while the source generation and the
render key are unchanged. Live content that rewrites itself is handled by the recovery
rules in `replaceBlocks` instead.
"""

import dataclasses
import enum
from typing import Iterable, Optional, Sequence

BLANK_CELL = 0
"""The cell value of a blank cell, matching NVDA's own use of 0 for an empty cell."""

PENDING_CELL = 0xC0
"""Dots 7 and 8, the mark on a row that stands for content not fetched.

`RowKind` has always distinguished the end of a document from a budget stop, but both were
drawn as blank cells, so under the fingers they were the same thing — which is the very
confusion the result states exist to prevent. NVDA uses the same shape for a row cut mid
word, which reads as "there is more of this" and is what is meant here too.
"""

PENDING_MARK_CELLS = 3
"""How many cells a pending or failed row spends on saying so."""

SEPARATOR_CELL = 0xC0
"""Dots 7 and 8, drawn the width of the band for a line between two groups.

The same shape as `PENDING_CELL`, and told apart by how much of the row it covers: a pending
row marks its first few cells and leaves the rest blank, and a separator is a line all the
way across. Both mean "this row is not content", which is why they share a shape, and a
reader asked for the line rather than the blank row it started as — a separator is
decoration, and decoration is worth feeling.
"""

NO_POSITION = -1
"""Marks a cell that came from no position in a block: padding, a gap, a status row.

Routing into such a cell must do nothing. NVDA's ordinary behaviour of mapping trailing
blank columns back to the last content cell would otherwise activate the end of the
preceding block when the reader presses a key in the space after a short field.
"""


@dataclasses.dataclass(frozen=True)
class BlockId:
	"""A block's identity, stable across re-rendering but not across editing.

	Compared by equality and never hashed, because a bookmark is whatever the source's
	`TextInfo` produces and need not be hashable.
	"""

	generation: int
	"""The source generation this block was fetched in.

	Bumped when the document is replaced — a different tree interceptor, a different root
	object, a document change event — so that a bookmark from the old document can never
	match one from the new.
	"""

	bookmark: object
	"""Whatever the source uses to find this block's start again."""

	unit: str
	"""The reading unit it was fetched with, since the same position read by line and by
	paragraph is not the same block."""


@dataclasses.dataclass(frozen=True)
class RenderKey:
	"""Everything that changes cells without changing content.

	Held beside a rendered block so that a changed braille table, a resized band or a
	different wrapping rule invalidates the rendering while leaving block identity intact.
	The anchor therefore survives a translation change, and the reader keeps their place.
	"""

	numCols: int
	"""Width of the band the block was rendered for."""

	table: str = ""
	"""Name of the output braille table."""

	fillRows: bool = False
	"""Whether rows were filled to the edge rather than wrapped at word boundaries."""

	markCuts: bool = False
	"""Whether a cut row spends a cell on a continuation mark."""

	settings: tuple = ()
	"""Anything else the renderer depends on: formatting configuration, cursor expansion,
	the configuration profile generation. A tuple so that adding one does not change this
	class's shape."""

	indent: int = 0
	"""How many cells of indent this block was drawn with.

	Part of the key rather than of the identity, because indent changes the cells and
	changes nothing about which block this is. It has to be here: an indented block is laid
	out in the cells the indent leaves, so the same block at two depths is two different
	renderings and serving one for the other would cut its rows in the wrong places.
	"""


@dataclasses.dataclass(frozen=True)
class RenderedBlock:
	"""One block, translated and cut into rows, with the map back to where each cell came from.

	`rows` and `positions` are the same shape. A cell in `rows[r][c]` came from
	`positions[r][c]` in the block's own content, or from `NO_POSITION` if it is padding.
	"""

	blockId: BlockId
	rows: tuple[tuple[int, ...], ...]
	"""The cells, one tuple per row. A row may be shorter than the band; it is padded when
	the window is assembled."""

	positions: tuple[tuple[int, ...], ...]
	"""For each cell, the position within the block it came from, or `NO_POSITION`."""

	renderKey: RenderKey
	rowOffset: int = 0
	"""Which row of the block `rows[0]` is.

	A block longer than one rendering is laid out in chunks, and a row is named by its
	place in the whole block rather than in the chunk it happens to be in. That is what
	lets the anchor survive a chunk changing underneath it, which is how a reader pans
	through a very long paragraph and back again without losing their place.
	"""

	moreRows: bool = False
	"""Whether the block continues past this rendering."""

	rawText: str = ""
	"""The block's text, for logging and for tests. Not used to lay anything out."""

	gapBefore: bool = False
	"""Ask for a blank row before this block.

	Spacing is declared by the block, never produced by packing: a short edit field is
	separated from what follows it because the source said so. Sources do not set this on
	the first block of a document, where it would show as a blank row above the top.
	"""

	gapAfter: bool = False
	"""Ask for a blank row after this block."""

	isBlank: bool = False
	"""Whether this block is empty content — a blank line in a document.

	Collapsing a run of these is the source's decision, since only it knows whether the
	reader is reading the content or writing it. A collapsed run keeps the identity of its
	first member, which is the one that owns the visible row.
	"""

	isDecoration: bool = False
	"""Whether this block is drawn rather than read. See `SourceBlock.isDecoration`."""

	depth: Optional[int] = None
	"""How deep this block sits in a structure, carried through from `SourceBlock.depth`.

	Here so that the band can find the depths of what it is showing without going back to
	the source, which is what the indent baseline is computed from. See `flowIndent`.
	"""

	@property
	def numRows(self) -> int:
		""":return: how many rows this rendering holds, not counting its gaps."""
		return len(self.rows)

	@property
	def endRow(self) -> int:
		""":return: one past the last row of the block this rendering reaches."""
		return self.rowOffset + len(self.rows)

	def holdsRow(self, rowIndex: int) -> bool:
		""":return: whether a row of the block is in this rendering."""
		return self.rowOffset <= rowIndex < self.endRow


@dataclasses.dataclass(frozen=True)
class SourceBlock:
	"""One block as a source found it, before it has been rendered.

	The region is whatever the source built to read this block with — a position bound
	text region, in practice. This module never looks inside it, which is what keeps the
	packing arithmetic free of NVDA.
	"""

	blockId: BlockId
	region: object
	gapBefore: bool = False
	gapAfter: bool = False
	isBlank: bool = False
	isControl: bool = False
	"""Whether this block holds a form control the reader stops at and answers.

	Two things follow from it, both in `flowForms`: the block declares a blank row after
	itself, so a one row answer is separated from the next prompt; and arriving at it places
	the window so that what precedes it — its label — is on the display above it, rather
	than putting the control on the top row and filling downward.
	"""

	isDecoration: bool = False
	"""Whether this block is something drawn between items rather than one of them.

	A menu separator, in practice. It occupies its row as a blank one, because the grouping
	it stands for is worth the space, and a routing key on it does nothing: there is nothing
	there to go to. See `flowObjects.isDecoration`.
	"""

	isInteractive: bool = False
	"""Whether this block is being read through the focused editable object.

	A form inside browse mode belongs to the surrounding document for placement and context,
	but its live caret belongs to the edit control. The flag lets the controller replace the
	document-bound region with the control-bound one, and put the document region back when
	the reader leaves the field, without changing the block's document identity.
	"""

	depth: Optional[int] = None
	"""How deep this block sits in the structure it belongs to, or None where it has none.

	One-based where it is set, because that is what `positionInfo["level"]` means and
	converting it here would make two conventions where there is now one. Prose has no
	depth and never sets this; a list item has depth 1 whether or not anything nests inside
	it. What it is drawn as is `flowIndent`'s decision and the renderer's doing — this is
	the fact, not the presentation.
	"""

	collapsed: int = 1
	"""How many blank blocks this one stands for.

	A run of blank lines collapses to a single row while the reader is reading, and to one
	row each while they are writing. The collapsed block keeps the identity of the first
	member of the run, which is the one that owns the visible row.
	"""


class ResultKind(enum.Enum):
	"""What came back from asking a source for a block."""

	BLOCK = "block"
	"""Here it is."""

	END_OF_STREAM = "endOfStream"
	"""There is genuinely no more content this way."""

	DEFERRED = "deferred"
	"""The budget stopped us. More exists, and the resume token says where to carry on."""

	ERROR = "error"
	"""The source failed. Logged, shown as a status row, and not retried in a loop."""


@dataclasses.dataclass(frozen=True)
class FetchResult:
	"""A source's answer, which is not always a block.

	Running out of budget and running out of document must not look the same on the
	display, or the reader is told spatially that the page ended when it merely took too
	long to read.
	"""

	kind: ResultKind
	block: SourceBlock | None = None
	resume: object = None
	"""Where to carry on from, for a deferred result."""

	message: str = ""
	"""Why, for an error."""

	@classmethod
	def found(cls, block: SourceBlock) -> "FetchResult":
		""":return: a result carrying a block."""
		return cls(kind=ResultKind.BLOCK, block=block)

	@classmethod
	def endOfStream(cls) -> "FetchResult":
		""":return: a result saying there is no more this way."""
		return cls(kind=ResultKind.END_OF_STREAM)

	@classmethod
	def deferred(cls, resume: object = None) -> "FetchResult":
		""":return: a result saying the budget ran out."""
		return cls(kind=ResultKind.DEFERRED, resume=resume)

	@classmethod
	def failed(cls, message: str) -> "FetchResult":
		""":return: a result saying the source could not answer."""
		return cls(kind=ResultKind.ERROR, message=message)

	@property
	def edgeState(self) -> "EdgeState":
		""":return: what this answer says about the edge it was fetched at."""
		return _EDGE_STATE_FOR_RESULT[self.kind]


class RowKind(enum.Enum):
	"""What a row of the stream is."""

	CONTENT = "content"
	"""A row of a block."""

	GAP = "gap"
	"""A blank row a block asked for, before or after itself."""

	BLANK = "blank"
	"""Past the end of the stream. There is genuinely no more content this way."""

	PENDING = "pending"
	"""There is more content, and it has not been fetched.

	Distinct from BLANK on purpose. A budget stop that rendered as blank rows would tell
	the reader, spatially and wrongly, that the document had ended.
	"""


class EdgeState(enum.Enum):
	"""What is known about the stream beyond one end of the cached blocks."""

	OPEN = "open"
	"""There is, or may be, more; it has not been asked for yet."""

	END = "end"
	"""The source said there is no more this way."""

	DEFERRED = "deferred"
	"""The source ran out of budget. More exists and can be resumed."""

	ERROR = "error"
	"""The source failed. Not retried in a loop."""


_EDGE_STATE_FOR_RESULT = {
	ResultKind.BLOCK: EdgeState.OPEN,
	ResultKind.END_OF_STREAM: EdgeState.END,
	ResultKind.DEFERRED: EdgeState.DEFERRED,
	ResultKind.ERROR: EdgeState.ERROR,
}
"""What each kind of answer says about the end of the stream it was fetched at."""


class Edge(enum.Enum):
	"""Which end of the stream something concerns."""

	BEFORE = "before"
	AFTER = "after"


class Entry(enum.Enum):
	"""Which edge of the window the anchor names."""

	TOP = "top"
	BOTTOM = "bottom"


@dataclasses.dataclass(frozen=True)
class StreamRow:
	"""One row of the stream, or of the window drawn from it."""

	kind: RowKind
	blockId: BlockId | None = None
	rowIndex: int = 0
	"""Which row of its block this is, for a CONTENT row."""


@dataclasses.dataclass(frozen=True)
class Anchor:
	"""Where the window is, and which of its edges is held fixed."""

	blockId: BlockId
	rowIndex: int
	entry: Entry


class ByIdentity:
	"""A small store keyed by things that compare but cannot be hashed.

	A bookmark is whatever a `TextInfo` produces, and NVDA's own bookmark for a virtual
	buffer is `textInfos.offsets.Offsets` — a plain dataclass, so it defines `__eq__` and
	Python sets its `__hash__` to None. Keying a dictionary by one raises `TypeError`, and
	a `BlockId` holding one cannot be a dictionary key either.

	So lookup is a linear scan by equality. That is affordable precisely because a flow
	holds so little: the window's blocks and a margin either side, a couple of dozen at
	most, dropped by `FlowWindow.trim` as the reader moves.
	"""

	def __init__(self) -> None:
		self._items: list[list] = []

	def get(self, key, default=None):
		""":return: the value stored under a key, or `default`."""
		for item in self._items:
			if item[0] == key:
				return item[1]
		return default

	def set(self, key, value) -> None:
		"""Store a value under a key, replacing any value already there."""
		for item in self._items:
			if item[0] == key:
				item[1] = value
				return
		self._items.append([key, value])

	def pop(self, key, default=None):
		""":return: the value stored under a key, removing it."""
		for index, item in enumerate(self._items):
			if item[0] == key:
				del self._items[index]
				return item[1]
		return default

	def clear(self) -> None:
		"""Forget everything."""
		self._items.clear()

	def values(self) -> list:
		""":return: the values, in the order they were first stored."""
		return [item[1] for item in self._items]

	def __contains__(self, key) -> bool:
		return any(item[0] == key for item in self._items)

	def __len__(self) -> int:
		return len(self._items)


class ContentNeeded(Exception):
	"""Raised when an operation needs rows the cache does not have.

	The controller answers this by asking the source for more and trying again, which is
	how fetching stays on demand: nothing is read ahead that the window did not turn out
	to need.
	"""

	def __init__(self, edge: Edge, rows: int) -> None:
		"""
		:param edge: which end more content is needed at.
		:param rows: how many rows short the window is at that end.
		"""
		super().__init__(f"{rows} more rows needed {edge.value} the cached blocks")
		self.edge = edge
		self.rows = rows


class FlowWindow:
	"""A run of blocks, and the window of rows shown from them.

	The blocks held are a contiguous run of the source's content in reading order, with a
	margin either side of what the window shows. The window itself is an anchor and an
	entry direction; every operation either moves the anchor or reports that it cannot
	without more content.
	"""

	def __init__(self, numRows: int) -> None:
		"""
		:param numRows: how many rows the band shows at once.
		:raises ValueError: if `numRows` is not positive.
		"""
		if numRows < 1:
			raise ValueError(f"A flow needs at least one row, got {numRows}")
		self.numRows = numRows
		self.blocks: list[RenderedBlock] = []
		"""The cached blocks, in reading order."""

		self.anchor: Anchor | None = None
		self.edges: dict[Edge, EdgeState] = {Edge.BEFORE: EdgeState.OPEN, Edge.AFTER: EdgeState.OPEN}

	# Cache maintenance.

	def setEdge(self, edge: Edge, state: EdgeState) -> None:
		"""Record what the source said about one end of the stream."""
		self.edges[edge] = state

	def blockIndex(self, blockId: BlockId) -> int:
		"""
		:param blockId: the block to find.
		:return: its index among the cached blocks.
		:raises LookupError: if it is not cached.
		"""
		for index, block in enumerate(self.blocks):
			if block.blockId == blockId:
				return index
		raise LookupError(f"No cached block {blockId}")

	def hasBlock(self, blockId: BlockId) -> bool:
		""":return: whether this block is cached."""
		try:
			self.blockIndex(blockId)
		except LookupError:
			return False
		return True

	def appendBlock(self, block: RenderedBlock) -> None:
		"""Add a block after the ones cached, and reopen that edge."""
		self.blocks.append(block)
		self.edges[Edge.AFTER] = EdgeState.OPEN

	def prependBlock(self, block: RenderedBlock) -> None:
		"""Add a block before the ones cached, and reopen that edge."""
		self.blocks.insert(0, block)
		self.edges[Edge.BEFORE] = EdgeState.OPEN

	def replaceBlock(self, block: RenderedBlock) -> None:
		"""Swap a cached block for a freshly rendered one of the same identity.

		Used when the block the cursor is in has changed under the reader, which is the
		common case: only that block is re-read on the core cycle.

		:param block: the new rendering.
		:raises LookupError: if no block of that identity is cached.
		"""
		index = self.blockIndex(block.blockId)
		self.blocks[index] = block
		self._clampAnchor()

	def replaceBlocks(self, blocks: Iterable[RenderedBlock]) -> bool:
		"""Replace every cached block, recovering the anchor against what survived.

		Recovery, in order:

		1. The anchored block is still there: keep it, clamping the row index.
		2. It is gone: take its surviving successor for a top anchor and its predecessor
			for a bottom anchor, so the window keeps growing the way the reader was going.
		3. Nothing survives: report failure, so the caller re-enters at the live cursor
			rather than silently choosing some other block.

		:param blocks: the new blocks, in reading order.
		:return: whether the anchor survived. False means the caller must re-enter.
		"""
		previous = [block.blockId for block in self.blocks]
		self.blocks = list(blocks)
		if self.anchor is None:
			return False
		if self.hasBlock(self.anchor.blockId):
			self._clampAnchor()
			return True
		return self._recoverAnchor(previous)

	def trim(self, marginRows: int) -> None:
		"""Drop cached blocks further than `marginRows` from the window.

		Keeps the cache to a window's worth either side, which is the whole of the memory
		budget: on a Monarch that is at most sixteen rows outside the window.

		:param marginRows: how many rows outside the window to keep.
		"""
		if self.anchor is None:
			return
		rows = self.streamRows()
		try:
			start, end = self._bounds(rows)
		except LookupError:
			return
		keepStart = max(0, start - marginRows)
		keepEnd = min(len(rows), end + marginRows)
		indices = {self._blockIndexOfRow(rows, position) for position in range(keepStart, keepEnd)}
		indices.discard(None)
		if not indices:
			return
		first, last = min(indices), max(indices)
		# Anything dropped means the stream continues past what is now held, whatever the
		# source last said about its ends.
		if first > 0:
			self.edges[Edge.BEFORE] = EdgeState.OPEN
		if last < len(self.blocks) - 1:
			self.edges[Edge.AFTER] = EdgeState.OPEN
		# Sliced rather than filtered, so the cache stays one contiguous run.
		self.blocks = self.blocks[first : last + 1]

	# The stream.

	def streamRows(self) -> list[StreamRow]:
		"""Pack the cached blocks into rows.

		A block starts on a new row and never starts a new window, which is the rule the
		whole design rests on. Gaps are the blocks' own declared spacing.

		:return: one entry per row of the cached run, in reading order.
		"""
		rows: list[StreamRow] = []
		for block in self.blocks:
			if block.gapBefore:
				rows.append(StreamRow(kind=RowKind.GAP, blockId=block.blockId, rowIndex=-1))
			for index in range(block.numRows):
				rows.append(
					StreamRow(kind=RowKind.CONTENT, blockId=block.blockId, rowIndex=block.rowOffset + index),
				)
			if block.gapAfter:
				rows.append(StreamRow(kind=RowKind.GAP, blockId=block.blockId, rowIndex=block.numRows))
		return rows

	def _blockIndexOfRow(self, rows: Sequence[StreamRow], position: int) -> int | None:
		""":return: the index among cached blocks of the block owning a stream row."""
		row = rows[position]
		if row.blockId is None:
			return None
		try:
			return self.blockIndex(row.blockId)
		except LookupError:
			return None

	def _anchorPosition(self, rows: Sequence[StreamRow]) -> int:
		"""
		:return: the index in `rows` of the row the anchor names.
		:raises LookupError: if the anchor is unset or its row is not in the stream.
		"""
		if self.anchor is None:
			raise LookupError("The flow has no anchor")
		for position, row in enumerate(rows):
			if (
				row.kind is RowKind.CONTENT
				and row.blockId == self.anchor.blockId
				and row.rowIndex == self.anchor.rowIndex
			):
				return position
		raise LookupError(f"Anchor row {self.anchor.rowIndex} of {self.anchor.blockId} is not in the stream")

	def _bounds(self, rows: Sequence[StreamRow]) -> tuple[int, int]:
		"""Work out which stream rows the window covers.

		The result may run past either end of `rows`: past the start when content above has
		not been fetched, past the end when the window is longer than what remains. Only
		the start is clamped, and only when the source has said there is genuinely nothing
		above — blank rows below content are how the end of a document reads, but blank
		rows above content are never right.

		:return: start and end positions in `rows`, end exclusive.
		"""
		position = self._anchorPosition(rows)
		if self.anchor is not None and self.anchor.entry is Entry.BOTTOM:
			end = position + 1
			start = end - self.numRows
		else:
			start = position
			end = start + self.numRows
		if start < 0 and self.edges[Edge.BEFORE] is EdgeState.END:
			shift = -start
			start += shift
			end += shift
		return start, end

	def shortfall(self, edge: Edge) -> int:
		"""How many rows the window is missing at one end.

		:param edge: which end to measure.
		:return: the number of rows that would have to be fetched to fill the window there.
			Zero if the window is full at that end, or if the source has said the stream
			ends there.
		"""
		rows = self.streamRows()
		try:
			start, end = self._bounds(rows)
		except LookupError:
			return 0
		if edge is Edge.BEFORE:
			missing = -start
		else:
			missing = end - len(rows)
		if missing <= 0 or self.edges[edge] is EdgeState.END:
			return 0
		return missing

	def rowsAbove(self) -> int:
		"""How many stream rows sit above the top row of the window.

		What a caller wanting context above the window asks: the shortfall says only whether
		the window is full, and a window anchored at its top row is full while having
		nothing above it at all. Placing a control's label above it means fetching rows the
		window does not need, so it has to be possible to ask how many are there.

		:return: the number of cached rows above the window, zero if it starts at the top of
			what has been read.
		"""
		rows = self.streamRows()
		try:
			start, _end = self._bounds(rows)
		except LookupError:
			return 0
		return max(0, start)

	def visibleRows(self) -> list[StreamRow]:
		"""The rows the display should show.

		Always `numRows` long. Rows past the end of the stream are BLANK where the source
		said the stream ends and PENDING where it did not, so that running out of budget
		never reads as running out of document.

		:return: the window's rows, top first.
		:raises LookupError: if the flow has no usable anchor.
		"""
		rows = self.streamRows()
		start, end = self._bounds(rows)
		visible: list[StreamRow] = []
		for position in range(start, end):
			if 0 <= position < len(rows):
				visible.append(rows[position])
				continue
			edge = Edge.BEFORE if position < 0 else Edge.AFTER
			kind = RowKind.BLANK if self.edges[edge] is EdgeState.END else RowKind.PENDING
			visible.append(StreamRow(kind=kind))
		return visible

	# Where the window is.

	def isVisible(self, blockId: BlockId, rowIndex: int | None = None) -> bool:
		"""Whether a block, or one row of it, is on the display.

		This is the question that has to be asked before anything else when the cursor
		moves: if the answer is yes, the window must not move at all. Landing on a heading
		that is already on the display should not jerk the display to put it at the top.

		:param blockId: the block to look for.
		:param rowIndex: a particular row of it, or None for any part of it.
		:return: whether it is within the window.
		"""
		try:
			visible = self.visibleRows()
		except LookupError:
			return False
		for row in visible:
			if row.kind is not RowKind.CONTENT or row.blockId != blockId:
				continue
			if rowIndex is None or row.rowIndex == rowIndex:
				return True
		return False

	def topBlockId(self) -> BlockId | None:
		"""The block owning the topmost content row.

		This is where the cursor goes after a pan: reading onward with the arrow keys then
		continues from the top of what is under the reader's hands, in both directions.

		:return: the block, or None if the window holds no content.
		"""
		try:
			visible = self.visibleRows()
		except LookupError:
			return None
		for row in visible:
			if row.kind is RowKind.CONTENT:
				return row.blockId
		return None

	def bottomBlockId(self) -> BlockId | None:
		""":return: the block owning the lowest content row, or None."""
		try:
			visible = self.visibleRows()
		except LookupError:
			return None
		for row in reversed(visible):
			if row.kind is RowKind.CONTENT:
				return row.blockId
		return None

	# Moving.

	def enterAt(self, blockId: BlockId, rowIndex: int = 0, contextRows: int = 0) -> None:
		"""Place the window afresh, with the given row at the top.

		Used on arrival: a focus change, a jump, or a recovery that found nothing to hold
		on to.

		:param blockId: the block to enter at.
		:param rowIndex: which of its rows to put at the top.
		:param contextRows: how many rows of what precedes it to show above instead. A
			plain document asks for none, so the block sits at the top and the display
			fills downward; a control asks for enough to show its label.
		:raises LookupError: if the block is not cached.
		"""
		self.blockIndex(blockId)
		self.anchor = Anchor(blockId=blockId, rowIndex=rowIndex, entry=Entry.TOP)
		self._clampAnchor()
		if contextRows <= 0:
			return
		rows = self.streamRows()
		position = self._anchorPosition(rows) - contextRows
		if position < 0:
			return
		row = rows[position]
		if row.kind is RowKind.CONTENT and row.blockId is not None:
			self.anchor = Anchor(blockId=row.blockId, rowIndex=row.rowIndex, entry=Entry.TOP)

	def panForward(self) -> bool:
		"""Move the window on by a whole display.

		The row after the current bottom row becomes the new top row.

		:return: whether the window moved. False means the stream has ended below.
		:raises ContentNeeded: if the rows to move onto have not been fetched.
		"""
		rows = self.streamRows()
		_, end = self._bounds(rows)
		position = self._nextContent(rows, end, forward=True)
		if position is None:
			if self.edges[Edge.AFTER] is EdgeState.END:
				return False
			raise ContentNeeded(Edge.AFTER, max(1, end - len(rows) + 1))
		return self._anchorTo(rows[position], Entry.TOP)

	def panBack(self) -> bool:
		"""Move the window back by a whole display.

		The row before the current top row becomes the new bottom row, which is what makes
		panning back the exact inverse of panning forward: a paragraph re-entered from
		below shows its tail at the bottom of the display.

		:return: whether the window moved. False means the stream has ended above.
		:raises ContentNeeded: if the rows to move onto have not been fetched.
		"""
		rows = self.streamRows()
		start, _ = self._bounds(rows)
		position = self._nextContent(rows, start - 1, forward=False)
		if position is None:
			if self.edges[Edge.BEFORE] is EdgeState.END:
				return False
			raise ContentNeeded(Edge.BEFORE, max(1, 1 - start))
		return self._anchorTo(rows[position], Entry.BOTTOM)

	def ensureVisible(self, blockId: BlockId, rowIndex: int = 0, forward: bool = True) -> bool:
		"""Bring a row onto the display by the smallest movement that does so.

		If it is already there, nothing moves at all. Otherwise it is brought on at the edge
		it is nearest: a row above the window becomes the top row, one below it becomes the
		bottom row. Either way the display moves by the distance to it and no further.

		:param blockId: the block to show.
		:param rowIndex: which of its rows must be visible.
		:param forward: the direction the reader is moving. A tiebreak only, for a row the
			stream cannot place.
		:return: whether the window moved.
		:raises LookupError: if the block is not cached.
		"""
		self.blockIndex(blockId)
		if self.isVisible(blockId, rowIndex):
			return False
		entry = self._entryFor(blockId, rowIndex, forward)
		self.anchor = Anchor(blockId=blockId, rowIndex=rowIndex, entry=entry)
		self._clampAnchor()
		return True

	def _entryFor(self, blockId: BlockId, rowIndex: int, forward: bool) -> Entry:
		"""Which edge of the display to bring a row on at.

		Answered from where the row is, not from which way the reader is thought to be
		going. The two agree whenever the guess is right, and the guess is the thing that
		has gone wrong twice on hardware: a caller with no way of knowing passes `forward`
		and gets a row that was *above* the window planted on the bottom row with a whole
		display of what precedes it filled in behind — a display's worth of movement for a
		reader who pressed the up arrow once.

		`refreshActive` was the last such caller. It re-renders the block the cursor is in
		and then asks for it to be shown, always forward, because the case it was written
		for is an edit growing downward as it is typed into. On a focus move in a tree it
		runs first and places the window, and the considered answer that follows it finds
		the row already visible and lets the guess stand.

		There is nothing to guess. A row the window has not reached yet is either before it
		or after it, the stream says which, and the nearer edge is the smallest movement
		that shows it — which is what this is for.

		:param blockId: the block being shown.
		:param rowIndex: which of its rows must be visible.
		:param forward: the direction the reader is moving, used only when the row cannot
			be found in the stream at all.
		:return: the edge to anchor at.
		"""
		guess = Entry.BOTTOM if forward else Entry.TOP
		rows = self.streamRows()
		try:
			position = self._positionOfRow(rows, blockId, rowIndex)
			start, end = self._bounds(rows)
		except LookupError:
			return guess
		if position < start:
			return Entry.TOP
		if position >= end:
			return Entry.BOTTOM
		# Neither before the window nor after it, and `isVisible` has already said it is not
		# on it: a gap row, or a row the two accounts of the window disagree about. Nothing
		# has been established, so the caller's guess stands.
		return guess

	def _positionOfRow(self, rows: Sequence[StreamRow], blockId: BlockId, rowIndex: int) -> int:
		"""Where one row of one block sits in the stream.

		:param rows: the stream, from `streamRows`.
		:param blockId: the block wanted.
		:param rowIndex: which of its rows, counted within the whole block. A block held as
			chunks may not be holding that row, so its first row answers instead: which side
			of the window the block is on is the same either way.
		:return: the position in `rows`.
		:raises LookupError: if the block has no content row in the stream.
		"""
		first = None
		for position, row in enumerate(rows):
			if row.kind is not RowKind.CONTENT or row.blockId != blockId:
				continue
			if row.rowIndex == rowIndex:
				return position
			if first is None:
				first = position
		if first is None:
			raise LookupError(f"Row {rowIndex} of {blockId} is not in the stream")
		return first

	def stepBlock(self, forward: bool, fromBlockId: BlockId | None = None) -> BlockId | None:
		"""The block one step from the one the cursor is in, for the line commands.

		NVDA's next and previous line commands move by a reading unit, which is a block
		here, and take the cursor with them. They act on `regions[-1]`, the last region in
		the buffer, which on a single line display is the cursor's block and in a flow is
		the block at the bottom of the window — so a flow must route them by identity
		instead, which is what this supports.

		:param forward: True for the next block, False for the previous.
		:param fromBlockId: the block to step from. Defaults to the anchored one, which is
			the top of the window and is only the right answer when the cursor happens to
			be there — so a caller that knows where the cursor is should say.
		:return: the neighbouring block's identity, or None if it is not cached.
		"""
		if fromBlockId is None:
			fromBlockId = self.anchor.blockId if self.anchor is not None else None
		if fromBlockId is None:
			return None
		try:
			index = self.blockIndex(fromBlockId)
		except LookupError:
			return None
		index += 1 if forward else -1
		if not 0 <= index < len(self.blocks):
			return None
		return self.blocks[index].blockId

	def _nextContent(self, rows: Sequence[StreamRow], position: int, forward: bool) -> int | None:
		"""Find the nearest row that can hold an anchor.

		A gap is not one: it belongs to a block rather than being one of its rows, so
		anchoring to it puts the window back on that block's own first row and the pan
		reports that it moved while showing the same thing. A one row window over a block
		with a trailing gap would pan forever.

		:param rows: the stream.
		:param position: where to start looking.
		:param forward: which way to look.
		:return: the position of the nearest content row, or None if there is none that way.
		"""
		step = 1 if forward else -1
		while 0 <= position < len(rows):
			if rows[position].kind is RowKind.CONTENT:
				return position
			position += step
		return None

	def _anchorTo(self, row: StreamRow, entry: Entry) -> bool:
		"""Anchor the window to a stream row, skipping rows that cannot hold an anchor.

		A gap is not a place the window can be anchored, because it belongs to a block
		rather than being one of its rows. Anchoring moves to the block's own nearest row
		in the direction of travel.

		:return: whether an anchor was set.
		"""
		if row.kind is RowKind.CONTENT and row.blockId is not None:
			self.anchor = Anchor(blockId=row.blockId, rowIndex=row.rowIndex, entry=entry)
			return True
		if row.blockId is None:
			return False
		block = self.blocks[self.blockIndex(row.blockId)]
		rowIndex = block.rowOffset if entry is Entry.TOP else block.endRow - 1
		self.anchor = Anchor(blockId=row.blockId, rowIndex=rowIndex, entry=entry)
		return True

	def _clampAnchor(self) -> None:
		"""Pull the anchor's row index back inside its block, after a re-render shrank it."""
		if self.anchor is None:
			return
		try:
			block = self.blocks[self.blockIndex(self.anchor.blockId)]
		except LookupError:
			return
		last = max(block.rowOffset, block.endRow - 1)
		if self.anchor.rowIndex > last:
			self.anchor = dataclasses.replace(self.anchor, rowIndex=last)
		elif self.anchor.rowIndex < block.rowOffset:
			self.anchor = dataclasses.replace(self.anchor, rowIndex=block.rowOffset)

	def _recoverAnchor(self, previous: Sequence[BlockId]) -> bool:
		"""Find something to hold on to after the anchored block went away.

		:param previous: the block identities in the order they were in before.
		:return: whether a new anchor was found.
		"""
		if self.anchor is None:
			return False
		try:
			was = list(previous).index(self.anchor.blockId)
		except ValueError:
			return False
		forward = self.anchor.entry is Entry.TOP
		candidates = previous[was + 1 :] if forward else list(reversed(previous[:was]))
		for blockId in candidates:
			if not self.hasBlock(blockId):
				continue
			block = self.blocks[self.blockIndex(blockId)]
			rowIndex = block.rowOffset if forward else max(block.rowOffset, block.endRow - 1)
			self.anchor = Anchor(blockId=blockId, rowIndex=rowIndex, entry=self.anchor.entry)
			return True
		return False

	def __repr__(self) -> str:
		return f"<FlowWindow {self.numRows} rows, {len(self.blocks)} blocks, anchor {self.anchor}>"


def assembleCells(window: FlowWindow, numCols: int) -> list[int]:
	"""Lay the window's rows out as one flat list of cells, as the hardware wants them.

	Each row is padded to the full width, which is what keeps the next block on the next
	row: the padding is the reason a ten cell heading does not leave the paragraph after it
	sharing its row.

	:param window: the window to draw.
	:param numCols: the width of the band.
	:return: `window.numRows * numCols` cells, row major.
	"""
	cells: list[int] = []
	for row in window.visibleRows():
		cells.extend(_rowCells(window, row, numCols))
	return cells


def cellSource(window: FlowWindow, numCols: int, position: int) -> tuple[BlockId, int] | None:
	"""Work out what a cell of the window came from, for cursor routing.

	:param window: the window that was drawn.
	:param numCols: the width of the band.
	:param position: a flat row major position within the window.
	:return: the block and the position within it, or None if the cell is padding, a gap,
		a blank or a status marker. Routing into any of those must do nothing.
	"""
	rows = window.visibleRows()
	rowIndex, col = divmod(position, numCols)
	if not 0 <= rowIndex < len(rows):
		return None
	row = rows[rowIndex]
	if row.kind is not RowKind.CONTENT or row.blockId is None:
		return None
	block = window.blocks[window.blockIndex(row.blockId)]
	if not block.holdsRow(row.rowIndex):
		return None
	positions = block.positions[row.rowIndex - block.rowOffset]
	if col >= len(positions):
		return None
	where = positions[col]
	if where == NO_POSITION:
		return None
	return row.blockId, where


def _rowCells(window: FlowWindow, row: StreamRow, numCols: int) -> list[int]:
	""":return: one row's worth of cells, padded to the full width."""
	if _isDecorationRow(window, row):
		return [SEPARATOR_CELL] * numCols
	if row.kind is RowKind.CONTENT and row.blockId is not None:
		try:
			block = window.blocks[window.blockIndex(row.blockId)]
		except LookupError:
			return [BLANK_CELL] * numCols
		if not block.holdsRow(row.rowIndex):
			return [BLANK_CELL] * numCols
		cells = list(block.rows[row.rowIndex - block.rowOffset])[:numCols]
		return cells + [BLANK_CELL] * (numCols - len(cells))
	if row.kind is RowKind.PENDING:
		marks = min(PENDING_MARK_CELLS, numCols)
		return [PENDING_CELL] * marks + [BLANK_CELL] * (numCols - marks)
	return [BLANK_CELL] * numCols


def _isDecorationRow(window: FlowWindow, row: StreamRow) -> bool:
	""":return: whether a row belongs to a block that is drawn rather than read."""
	if row.kind is not RowKind.CONTENT or row.blockId is None:
		return False
	try:
		return bool(window.blocks[window.blockIndex(row.blockId)].isDecoration)
	except LookupError:
		return False
