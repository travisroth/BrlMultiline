# BrlMultiline: turning a block into rows of cells.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Laying one block out as rows, without reimplementing any of NVDA's text handling.

`flow` needs each block cut into rows of the band's width, with a map from each cell back
to the position in the block it came from. NVDA has all of that already — translation,
contraction, word boundary wrapping, syllable hyphenation, continuation marks — but only
inside a `BrailleBuffer`, and a buffer holding several regions concatenates them and cuts
rows out of the result, which is exactly the arrangement a flow cannot use.

So a block is laid out in a buffer of its own: one region, a rectangle the width of the
band, and nothing else in it to run together with. This is `RectHandlerProxy` doing the
same job it does for a segment — telling a stock `BrailleBuffer` that the display is the
size of a rectangle — with the rectangle here being a place to lay text out rather than a
place on the display. `BrailleBufferSegment` is reused rather than subclassed afresh, so
the block is cut by the same rules the band itself would use, including the fill mode a
narrow band wants.

Nothing is copied out of NVDA. The one thing assembled here that NVDA also assembles is
the padded row, in `_rowFrom`, and that is because NVDA's version
(`BrailleBuffer._get_windowBrailleCells`) produces cells alone, while a flow needs the
positions beside them or routing has nothing to aim at.
"""

from typing import TYPE_CHECKING, Optional

import config
from braille.constants import CONTINUATION_SHAPE
from logHandler import log

from .flow import NO_POSITION, RenderedBlock, RenderKey, SourceBlock
from .flowIndent import FLAT, IndentPlan
from .layout import SegmentRect
from .panels import SegmentSpec
from .segments import BrailleBufferSegment

if TYPE_CHECKING:
	from braille.brailleHandler import BrailleHandler

LAYOUT_ROWS = 8
"""How many rows are laid out at a time.

A block longer than this is laid out in several passes, so the number is a working set
rather than a limit. Eight is a Monarch's height, which makes the common case one pass.
"""

CHUNK_ROWS = 64
"""How many rows of one block are laid out at a time.

A working set, not a limit: a block longer than this is rendered in chunks, and the
controller asks for the next one when the reader pans towards it. What it bounds is the
cells held for a pathological block — a whole document rendered as one paragraph, a
minified script in a code view — not how much of it can be read.
"""


class FlowRenderer:
	"""Lays blocks out as rows for one band.

	One renderer per band, since the width and the wrapping rules belong to the band. It
	holds no state about which blocks exist; it is asked for a rendering and produces one.
	"""

	def __init__(
		self,
		handler: "BrailleHandler",
		numCols: int,
		fillRows: bool = True,
		markCuts: bool = False,
		layoutRows: int = LAYOUT_ROWS,
		maxRows: int = CHUNK_ROWS,
		indentPlan: IndentPlan = FLAT,
	) -> None:
		"""
		:param handler: the real braille handler, which the layout buffer reads its
			settings through.
		:param numCols: the width of the band, in cells.
		:param fillRows: fill each row to the edge rather than wrapping at word boundaries.
			A band of full display width usually wants False, so that the user's own wrap
			setting applies; a narrow one has no cells to spare for keeping words whole.
		:param markCuts: spend a cell on a continuation mark where a row was cut mid word.
		:param layoutRows: how many rows to lay out per pass.
		:param maxRows: how many rows of one block to hold at a time.
		:param indentPlan: how a block's depth is drawn. The default draws nothing, which is
			what prose wants and what everything wanted before depth existed.
		:raises ValueError: if the width is not positive.
		"""
		if numCols < 1:
			raise ValueError(f"A band needs at least one cell, got {numCols}")
		self.handler = handler
		self.numCols = numCols
		self.fillRows = fillRows
		self.markCuts = markCuts
		self.layoutRows = layoutRows
		self.maxRows = maxRows
		self.indentPlan = indentPlan
		"""How deep a block sits, turned into cells. See `flowIndent`.

		Held rather than passed, so that none of the controller's six render calls has to
		know about depth. Replaced wholesale when the band rebases, which is also what
		invalidates the renderings drawn under the old one — see `flow.RenderKey.indent`.
		"""

	@property
	def renderKey(self) -> RenderKey:
		"""What this renderer's output depends on, beyond the content itself.

		A rendering carries this so that a changed braille table or a resized band
		invalidates the cells while leaving block identity alone, and the reader keeps
		their place across the change.
		"""
		return self._keyWith(0)

	def _keyWith(self, indent: int) -> RenderKey:
		""":return: the render key for a block drawn with a given indent."""
		return RenderKey(
			numCols=self.numCols,
			table=self._setting("translationTable"),
			fillRows=self.fillRows,
			markCuts=self.markCuts,
			settings=(self._setting("textWrap"), self._setting("expandAtCursor")),
			indent=indent,
		)

	def _setting(self, name: str):
		""":return: one braille setting, or None where the configuration cannot be read."""
		try:
			return config.conf["braille"][name]
		except Exception:
			return None

	def render(self, block: SourceBlock, fromRow: int = 0) -> RenderedBlock:
		"""Lay a block out, or one chunk of a long one.

		:param block: the block to render, carrying the region to read it through.
		:param fromRow: which row of the block to start at. A long block is read in chunks
			so that a very long paragraph can be panned through rather than cut off.
		:return: the rendering. A block with no text renders as one blank row, so that a
			blank line in a document occupies the row it deserves.
		"""
		fromRow = max(0, fromRow)
		first = self.indentPlan.prefixFor(block.depth)
		rest = self.indentPlan.continuationPrefixFor(block.depth)
		rows, positions, more = self._indented(block, fromRow, first, rest)
		if not rows:
			rows = [()]
			positions = [()]
			fromRow = 0
		return RenderedBlock(
			blockId=block.blockId,
			rows=tuple(rows),
			positions=tuple(positions),
			renderKey=self._keyWith(len(first)),
			depth=block.depth,
			rowOffset=fromRow,
			moreRows=more,
			rawText=getattr(block.region, "rawText", ""),
			gapBefore=block.gapBefore,
			gapAfter=block.gapAfter,
			isBlank=block.isBlank,
			isDecoration=block.isDecoration,
		)

	def renderAround(self, block: SourceBlock, position: int, contextRows: int = 0) -> RenderedBlock:
		"""Lay out the chunk containing one braille position.

		The 64-row chunk is a working-set bound, not a text-length limit. A caret can be well
		past that first chunk in a large edit, so locating its row is a separate streaming
		pass and does not guess that an unseen cursor belongs on the chunk's last row.

		:param block: the block whose active region owns ``position``.
		:param position: the region-relative braille cell holding the caret.
		:param contextRows: how many rows before the caret to include where possible.
		:return: the rendering beginning shortly before the caret, or the first chunk if the
			position could not be mapped.
		"""
		row = self._rowContaining(block, position)
		if row is None:
			return self.render(block)
		return self.render(block, fromRow=max(0, row - max(0, contextRows)))

	def _rowContaining(self, block: SourceBlock, position: int) -> Optional[int]:
		""":return: the whole-block row containing a braille position, or None.

		Measured at the width a wrapped block is laid out in, since a position past the
		first row belongs to a block that wraps by definition. Measuring at the wider width
		would name a row the rendering does not have.
		"""
		rest = self.indentPlan.continuationPrefixFor(block.depth)
		buffer = self._layoutBuffer(block, width=self.numCols - len(rest) if rest else None)
		if buffer is None:
			return None
		seen = 0
		cells = buffer.brailleCells
		while True:
			try:
				buffer._calculateWindowRowBufferOffsets(buffer.windowStartPos)
			except Exception:
				log.debugWarning("Could not locate a cursor in a laid out block", exc_info=True)
				return None
			offsets = list(buffer._windowRowBufferOffsets)
			if not offsets:
				return None
			for rowPositions in offsets:
				if rowPositions.start >= len(cells) and seen:
					continue
				end = min(rowPositions.end, len(cells))
				if rowPositions.start <= position < end:
					return seen
				seen += 1
			if buffer.windowEndPos >= len(cells) or not buffer._nextWindow():
				return None

	def _indented(
		self,
		block: SourceBlock,
		fromRow: int,
		first: tuple[int, ...],
		rest: tuple[int, ...],
	) -> tuple[list, list, bool]:
		"""Lay a block out in the cells its indent leaves, and put the indent in front.

		Two widths, because a wrapped row is indented further than the first one is — see
		`flowIndent.CONTINUATION_EXTRA` — and a buffer cuts every row of a block at one
		width. An item that fits on a row is laid out in the wider of the two and never
		pays for a continuation it does not have; only an item that actually wraps is laid
		out again in the narrower one. The second pass costs a second translation of one
		block, and it is spent only where the reader can see what it bought.

		The indent cells map back to `NO_POSITION`, which is how the continuation mark and
		the padding after a short row are already handled, and is what stops a routing key
		in the margin aiming at the first character of the item.

		:param block: the block to lay out.
		:param fromRow: the first row of the block to keep.
		:param first: the indent cells for the block's own first row.
		:param rest: the indent cells for its wrapped rows.
		:return: the rows, their position maps, and whether the block continues past them.
		"""
		if not first and not rest:
			# No depth, or a band with no cells to spend on it. The path everything took
			# before indent existed, and it must stay free.
			buffer = self._layoutBuffer(block)
			if buffer is None:
				return [], [], False
			return self._layout(buffer, fromRow)
		if fromRow == 0:
			buffer = self._layoutBuffer(block, width=self.numCols - len(first))
			if buffer is None:
				return [], [], False
			rows, positions, more = self._layout(buffer, 0)
			if len(rows) <= 1 and not more:
				return self._withIndent(rows, positions, 0, first, rest) + (more,)
		buffer = self._layoutBuffer(block, width=self.numCols - len(rest))
		if buffer is None:
			return [], [], False
		rows, positions, more = self._layout(buffer, fromRow)
		return self._withIndent(rows, positions, fromRow, first, rest) + (more,)

	def _withIndent(
		self,
		rows: list,
		positions: list,
		fromRow: int,
		first: tuple[int, ...],
		rest: tuple[int, ...],
	) -> tuple[list, list]:
		"""Put the indent cells in front of each row.

		The block's own first row gets `first` and every other row gets `rest`, counted by
		the row's place in the whole block rather than in this chunk — a chunk starting part
		way through a long block holds no first row at all.
		"""
		out: list[tuple[int, ...]] = []
		where: list[tuple[int, ...]] = []
		for index, (row, place) in enumerate(zip(rows, positions)):
			prefix = first if fromRow + index == 0 else rest
			out.append(prefix + tuple(row))
			where.append((NO_POSITION,) * len(prefix) + tuple(place))
		return out, where

	def _layoutBuffer(
		self,
		block: SourceBlock,
		width: Optional[int] = None,
	) -> Optional[BrailleBufferSegment]:
		"""Build the buffer one block is laid out in.

		:param block: the block to lay out.
		:param width: how many cells wide to cut its rows, defaulting to the whole band. An
			indented block is laid out in what its indent leaves.
		:return: the buffer, its window at the start of the block, or None if the block
			could not be laid out at all.
		"""
		spec = SegmentSpec(
			rect=SegmentRect(
				row=0,
				col=0,
				numRows=self.layoutRows,
				numCols=max(1, self.numCols if width is None else width),
			),
			key="flowRender",
			fillRows=self.fillRows,
			markCuts=self.markCuts,
		)
		try:
			# No container: this buffer is a place to lay text out, not a place on the
			# display, so nothing composites it and nothing routes into it.
			buffer = BrailleBufferSegment(self.handler, None, spec)
			buffer.append(block.region)
			buffer.update()
		except Exception:
			log.debugWarning(f"Could not lay out {block.blockId}", exc_info=True)
			return None
		return buffer

	def _layout(self, buffer: BrailleBufferSegment, fromRow: int = 0) -> tuple[list, list, bool]:
		"""Walk a laid out block, a pass at a time, collecting the rows wanted.

		Rows before `fromRow` are walked and discarded rather than skipped: where a row ends
		depends on where the one before it ended, so the only way to know is to cut them.
		The translation is done once either way, when the region is read.

		:param buffer: the buffer holding the block.
		:param fromRow: the first row of the block to keep.
		:return: the rows kept, their position maps, and whether the block continues past
			them.
		"""
		rows: list[tuple[int, ...]] = []
		positions: list[tuple[int, ...]] = []
		seen = 0
		cells = buffer.brailleCells
		while True:
			try:
				# NVDA's own `update` ends by doing this; the call is here as well because a
				# pass after the first has moved the window and the offsets go with it.
				buffer._calculateWindowRowBufferOffsets(buffer.windowStartPos)
			except Exception:
				log.debugWarning("Could not cut a block into rows", exc_info=True)
				break
			offsets = list(buffer._windowRowBufferOffsets)
			if not offsets:
				break
			for rowPositions in offsets:
				if rowPositions.start >= len(cells) and (rows or seen):
					# Past the end of the block. A first row is kept even when empty, since
					# a blank line is a row.
					continue
				if seen < fromRow:
					seen += 1
					continue
				seen += 1
				row, where = self._rowFrom(cells, rowPositions)
				rows.append(row)
				positions.append(where)
				if len(rows) >= self.maxRows:
					# Not the end of the block, only the end of this chunk. The controller
					# asks for the next one when the reader pans towards it.
					more = buffer.windowEndPos < len(cells) or rowPositions.end < len(cells)
					return rows, positions, more
			if buffer.windowEndPos >= len(cells):
				break
			if not buffer._nextWindow():
				break
		return rows, positions, False

	def _rowFrom(self, cells: list, rowPositions) -> tuple[tuple[int, ...], tuple[int, ...]]:
		"""Build one row and the map back to where each of its cells came from.

		The shape of `BrailleBuffer._get_windowBrailleCells`, which cannot be used directly
		because it returns cells alone: a flow needs to know which position in the block
		each cell holds, or a routing key press has nothing to aim at. A continuation mark
		and the padding after a short row come from no position at all, and routing into
		them must do nothing.

		A row is left at its natural length rather than padded out. Padding is
		`flow.assembleCells`' job, done once when the window is drawn, and doing it here as
		well would bury cells belonging to no position inside a block's own rows.

		:param cells: the block's cells.
		:param rowPositions: where this row starts and ends in them.
		:return: the row's cells, and the position each came from.
		"""
		start, end = rowPositions.start, min(rowPositions.end, len(cells))
		row = list(cells[start:end])
		where = list(range(start, end))
		if rowPositions.showContinuationMark and len(row) < self.numCols:
			row.append(CONTINUATION_SHAPE)
			where.append(NO_POSITION)
		return tuple(row), tuple(where)

	def __repr__(self) -> str:
		mode = "filled" if self.fillRows else "wrapped"
		return f"<FlowRenderer {self.numCols} cells, {mode}>"
