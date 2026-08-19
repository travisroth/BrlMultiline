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

MAX_BLOCK_ROWS = 64
"""How many rows one block may occupy before the rest of it is dropped.

A guard against a pathological block — a whole document rendered as one paragraph, a
minified script in a code view — costing a great deal of layout for rows no reader will
ever pan to. At 32 cells that is two thousand cells, far more than a display can show.
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
		maxRows: int = MAX_BLOCK_ROWS,
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
		:param maxRows: how many rows one block may occupy at most.
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

	@property
	def renderKey(self) -> RenderKey:
		"""What this renderer's output depends on, beyond the content itself.

		A rendering carries this so that a changed braille table or a resized band
		invalidates the cells while leaving block identity alone, and the reader keeps
		their place across the change.
		"""
		return RenderKey(
			numCols=self.numCols,
			table=self._setting("translationTable"),
			fillRows=self.fillRows,
			markCuts=self.markCuts,
			settings=(self._setting("textWrap"), self._setting("expandAtCursor")),
		)

	def _setting(self, name: str):
		""":return: one braille setting, or None where the configuration cannot be read."""
		try:
			return config.conf["braille"][name]
		except Exception:
			return None

	def render(self, block: SourceBlock) -> RenderedBlock:
		"""Lay a block out.

		:param block: the block to render, carrying the region to read it through.
		:return: the rendering. A block with no text renders as one blank row, so that a
			blank line in a document occupies the row it deserves.
		"""
		rows: list[tuple[int, ...]] = []
		positions: list[tuple[int, ...]] = []
		buffer = self._layoutBuffer(block)
		if buffer is not None:
			rows, positions = self._layout(buffer)
		if not rows:
			rows = [()]
			positions = [()]
		return RenderedBlock(
			blockId=block.blockId,
			rows=tuple(rows),
			positions=tuple(positions),
			renderKey=self.renderKey,
			rawText=getattr(block.region, "rawText", ""),
			gapBefore=block.gapBefore,
			gapAfter=block.gapAfter,
			isBlank=block.isBlank,
		)

	def _layoutBuffer(self, block: SourceBlock) -> Optional[BrailleBufferSegment]:
		"""Build the buffer one block is laid out in.

		:return: the buffer, its window at the start of the block, or None if the block
			could not be laid out at all.
		"""
		spec = SegmentSpec(
			rect=SegmentRect(row=0, col=0, numRows=self.layoutRows, numCols=self.numCols),
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

	def _layout(self, buffer: BrailleBufferSegment) -> tuple[list, list]:
		"""Walk a laid out block, a pass at a time, collecting its rows.

		:param buffer: the buffer holding the block.
		:return: the rows and their position maps.
		"""
		rows: list[tuple[int, ...]] = []
		positions: list[tuple[int, ...]] = []
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
				if rowPositions.start >= len(cells) and rows:
					# Past the end of the block. A first row is kept even when empty, since
					# a blank line is a row.
					continue
				row, where = self._rowFrom(cells, rowPositions)
				rows.append(row)
				positions.append(where)
				if len(rows) >= self.maxRows:
					log.debugWarning(f"A block reached {self.maxRows} rows; the rest is not shown")
					return rows, positions
			if buffer.windowEndPos >= len(cells):
				break
			if not buffer._nextWindow():
				break
		return rows, positions

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
