# BrlMultiline: turning a block into rows of cells.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Laying one block out as rows, without reimplementing any of NVDA's text handling.

Each block is laid out in a `BrailleBuffer` of its own, one region the band's width, through
`RectHandlerProxy` and `BrailleBufferSegment`, so NVDA's translation and wrapping cut it.
`_rowFrom` builds rows with the positions beside the cells, which NVDA's version lacks.
"""

import dataclasses
from typing import TYPE_CHECKING, Optional

import config
from braille.constants import CONTINUATION_SHAPE
from logHandler import log

from . import brailleSubstitutions, glyphFlow
from .flow import BLANK_CELL, NO_POSITION, RenderedBlock, RenderKey, SourceBlock
from .flowIndent import FLAT, IndentPlan
from .flowTable import (
	MAX_TABLE_ROWS,
	READING_ORDER,
	KEEP_END,
	TRUNCATE,
	ColumnPlan,
	cellPosition,
	rowCellsOf,
)
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

A working set, not a limit: a longer block is rendered in chunks as the reader pans.
"""


def _rowsAreFixedWidth(buffer: BrailleBufferSegment) -> bool:
	""":return: whether every row of a buffer is exactly its width, so rows can be counted.

	True for the fill mode with no continuation marks; otherwise rows must be walked.

	:param buffer: the buffer a block is being laid out in.
	"""
	return bool(getattr(buffer, "fillRows", False)) and not getattr(buffer, "markCuts", False)


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
		columnPlan: ColumnPlan = READING_ORDER,
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
		:param indentPlan: how a block's depth is drawn. The default draws nothing.
		:param columnPlan: where a table row's columns go. The default is reading order.
		:raises ValueError: if the width is not positive.
		"""
		if numCols < 1:
			raise ValueError(f"A band needs at least one cell, got {numCols}")
		self.glyphTarget = None
		"""The display this band's blocks are being drawn on, or None if none of them draws.

		Set by the band, which can move. None means no shape is drawn. See
		`glyphFlow.targetForRows`.
		"""
		self.unwrappedPage: Optional[int] = None
		"""Which page across of unwrapped lines to draw, or None to wrap as ever.

		Unwrapped, every block is one row: only this page of the line is drawn, blank if the line
		is short, so each line keeps its row, as code wants. Table rows in columns have pages of
		their own. See `FlowController.setUnwrapped`.
		"""
		self.glyphsWanted = False
		"""Whether the reader's setting asked for shapes when the band was last laid out for them.

		Beside `glyphTarget`, since both decide what a block looks like. See
		`FlowController.drawGlyphsOn`.
		"""
		self.handler = handler
		self.numCols = numCols
		self.fillRows = fillRows
		self.markCuts = markCuts
		self.layoutRows = layoutRows
		self.maxRows = maxRows
		self.indentPlan = indentPlan
		"""How deep a block sits, turned into cells. See `flowIndent`.

		Held, so render calls need not know about depth. Replaced when the band rebases; see
		`flow.RenderKey.indent`.
		"""

		self.columnPlan = columnPlan
		"""Where a table row's columns go. See `flowTable`.

		As `indentPlan`, and part of the rendering key so old column widths are never served.
		"""

	@property
	def renderKey(self) -> RenderKey:
		"""What this renderer's output depends on, beyond the content itself.

		See `flow.RenderKey`.
		"""
		return self._keyWith(0)

	def keyFor(self, block: SourceBlock) -> RenderKey:
		""":return: the key a rendering of one block would carry if it were made now.

		So a held rendering can be checked without rendering again. A table row drawn at its
		plan's offsets carries no indent, as `_chunkOf` records.

		:param block: the block in question.
		"""
		if not self.columnPlan.isEmpty and rowCellsOf(block.region) is not None:
			return self._keyWith(0)
		return self._keyWith(len(self.indentPlan.prefixFor(block.depth)))

	def _keyWith(self, indent: int) -> RenderKey:
		""":return: the render key for a block drawn with a given indent."""
		return RenderKey(
			numCols=self.numCols,
			table=self._setting("translationTable"),
			fillRows=self.fillRows,
			markCuts=self.markCuts,
			settings=(
				self._setting("textWrap"),
				self._setting("expandAtCursor"),
				# The column plan, so that a row drawn under one set of widths is never served
				# for a row drawn under another. It goes in `settings` rather than beside
				# `indent` because that is what `settings` is for: one more thing the cells
				# depend on, added without changing the key's shape.
				self.columnPlan,
				# The page across, so that a line drawn at one page is never served for another.
				self.unwrappedPage,
				# The reader's braille substitutions, which change the cells while the table's name
				# stays the same. See `brailleSubstitutions.generation`.
				brailleSubstitutions.generation(),
			),
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
		columns = self._asColumns(block, fromRow)
		if columns is not None:
			return columns
		if self.unwrappedPage is not None:
			return self._unwrapped(block)
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

	def renderPinned(self, block: SourceBlock) -> RenderedBlock:
		"""Lay a block out as the one row that stays above the window.

		Cut, never wrapped, so the rows under it never move. The plan is swapped for its cutting
		form rather than trimming afterwards, which would keep the narrower wrapped first line.

		:param block: the block to draw.
		:return: its rendering, one row tall.
		"""
		plan = self.columnPlan
		self.columnPlan = plan.cutting()
		try:
			rendered = self.render(block)
		finally:
			self.columnPlan = plan
		return dataclasses.replace(
			rendered,
			rows=rendered.rows[:1],
			positions=rendered.positions[:1],
			moreRows=False,
		)

	def _asColumns(self, block: SourceBlock, fromRow: int = 0) -> Optional[RenderedBlock]:
		"""Lay a table row out at its plan's offsets, if it is one and there is a plan.

		Each cell is translated in a buffer of its own at its column's width, then placed. A
		packing row is a lane, as tall as its tallest cell, and lanes are stacked by height.
		Rows are padded to the full width here, since columns need padding between them.

		:param block: the block being rendered.
		:param fromRow: which row of the table row to start at, for a row taller than the
			band will show at once.
		:return: the rendering, or None if this is not a table row being drawn as one.
		"""
		cells = rowCellsOf(block.region)
		if cells is None or self.columnPlan.isEmpty:
			return None
		plan = self.columnPlan
		lanes = self._lanes(block.region, plan)
		rows, where = self._laidOut(lanes, plan)
		return self._chunkOf(block, rows, where, fromRow)

	def _lanes(self, row, plan) -> dict:
		""":return: the drawn lines of every placed cell, gathered by the lane they go in."""
		lanes: dict = {}
		for place in plan.placements():
			cell = row.cellFor(place.column.index)
			if cell is None:
				continue
			lines, positions = self._cellRows(cell, place.column)
			lanes.setdefault(place.row, []).append((place, lines, positions))
		return lanes

	def _laidOut(self, lanes: dict, plan) -> tuple[list, list]:
		"""Draw every lane, one under the last, and give back the whole table row.

		However tall; what the band shows is `_chunkOf`'s decision.

		:param lanes: the drawn cells, by lane.
		:param plan: the layout.
		:return: the rows and their position maps.
		"""
		heights = {lane: 1 for lane in range(max(1, plan.numRows))}
		for lane, items in lanes.items():
			heights[lane] = max([len(lines) for _place, lines, _marks in items] or [1])
		starts: dict = {}
		total = 0
		for lane in sorted(heights):
			starts[lane] = total
			total += heights[lane]
		total = max(1, total)
		rows = [[BLANK_CELL] * plan.numCols for _ in range(total)]
		where = [[NO_POSITION] * plan.numCols for _ in range(total)]
		for lane, items in lanes.items():
			for place, lines, positions in items:
				start = starts.get(lane, 0)
				drawn = False
				for index, (line, marks) in enumerate(zip(lines, positions)):
					target = start + index
					if target >= total:
						break
					for offset, value in enumerate(line[: place.column.width]):
						rows[target][place.offset + offset] = value
						# A cell from nowhere stays from nowhere; packing `NO_POSITION` would
						# unpack as a real position in the column before.
						mark = marks[offset]
						if mark != NO_POSITION:
							drawn = True
						where[target][place.offset + offset] = (
							NO_POSITION if mark == NO_POSITION else cellPosition(place.column.index, mark)
						)
				# An empty cell is still a place as wide as its column, so the cursor can show
				# and any routing key under its heading reaches it. Every band cell is at
				# offset zero: a cell is routed to as a place (see
				# `flowTableSource.TableCellRegion.routeTo`), and `FlowController.cursorCell`
				# then puts the cursor at the column's start.
				if not drawn and start < total:
					for offset in range(place.column.width):
						at = place.offset + offset
						if at >= plan.numCols:
							break
						where[start][at] = cellPosition(place.column.index, 0)
		return rows, where

	def _chunkOf(self, block: SourceBlock, rows: list, where: list, fromRow: int) -> RenderedBlock:
		"""Take the part of a table row the band will show, and say whether there is more.

		A row taller than `flowTable.MAX_TABLE_ROWS` is a block with more rows, panned through
		like a long paragraph, never silently cut.

		:param block: the block being rendered.
		:param rows: every row of the table row.
		:param where: their position maps.
		:param fromRow: the first row to show.
		:return: the rendering.
		"""
		total = len(rows)
		fromRow = max(0, min(fromRow, max(0, total - 1)))
		end = min(total, fromRow + max(1, MAX_TABLE_ROWS))
		return RenderedBlock(
			blockId=block.blockId,
			rows=tuple(tuple(line) for line in rows[fromRow:end]),
			positions=tuple(tuple(line) for line in where[fromRow:end]),
			renderKey=self._keyWith(0),
			depth=block.depth,
			rowOffset=fromRow,
			moreRows=end < total,
			rawText=getattr(block.region, "rawText", ""),
			gapBefore=block.gapBefore,
			gapAfter=block.gapAfter,
			isBlank=block.isBlank,
			isDecoration=block.isDecoration,
		)

	def _cellRows(self, cell, column) -> tuple[list, list]:
		"""Translate one cell of a table row and cut it to its column.

		Cut in fill mode, not at word boundaries. A cell that does not fit is laid out again at
		the column's width less its indent (see `flowTable.indentFor`), first row included, so
		there is one way of cutting.

		:param cell: the cell to read.
		:param column: the column it goes in.
		:return: its rows and their position maps.
		"""
		holder = SourceBlock(blockId=cell.index, region=cell.region)
		buffer = self._layoutBuffer(holder, width=column.width, glyphs=False)
		if buffer is None:
			return [], []
		rows, positions, _more = self._layout(buffer, fromRow=0)
		if column.overflow == TRUNCATE:
			# One row, and the rest of the value is not shown. The reader asked for that — and
			# which row is theirs too: the first is what cutting has always meant, and the last
			# is the end of the value, for a column whose cells begin with something nobody
			# wrote for reading. See `flowTable.KEEP_ENDS`.
			if column.keep == KEEP_END and len(rows) > 1:
				return rows[-1:], positions[-1:]
			return rows[:1], positions[:1]
		if len(rows) <= 1:
			return rows, positions
		indent = column.indent
		buffer = self._layoutBuffer(holder, width=max(1, column.width - indent), glyphs=False)
		if buffer is None:
			return rows, positions
		rows, positions, _more = self._layout(buffer, fromRow=0)
		return self._indentedCell(rows, positions, indent)

	def _indentedCell(self, rows: list, positions: list, indent: int) -> tuple[list, list]:
		"""Move a wrapped cell's continuation rows in, leaving its first row at the margin.

		:param rows: the cell's rows.
		:param positions: their position maps.
		:param indent: how many cells to move the continuations by.
		:return: the rows and maps, indented.
		"""
		pad = (BLANK_CELL,) * indent
		padded = (NO_POSITION,) * indent
		out: list = []
		where: list = []
		for index, (line, marks) in enumerate(zip(rows, positions)):
			if index:
				out.append(pad + tuple(line))
				where.append(padded + tuple(marks))
			else:
				out.append(tuple(line))
				where.append(tuple(marks))
		return out, where

	def _unwrapped(self, block: SourceBlock) -> RenderedBlock:
		"""Lay a block out as one line, and draw the page of it the band is across at.

		Filled, with no continuation mark, so page n of every line starts at the same cell. The
		indent goes in front before cutting, so nesting holds on every page.

		:param block: the block to draw.
		:return: one row, possibly blank, with `lineCells` saying how long the whole line is.
		"""
		prefix = self.indentPlan.prefixFor(block.depth)
		fillRows, markCuts = self.fillRows, self.markCuts
		self.fillRows, self.markCuts = True, False
		try:
			buffer = self._layoutBuffer(block)
		finally:
			self.fillRows, self.markCuts = fillRows, markCuts
		cells: tuple = prefix
		where: tuple = (NO_POSITION,) * len(prefix)
		if buffer is not None:
			content = tuple(buffer.brailleCells)
			if content and not content[-1] and getattr(buffer, "cursorPos", None) != len(content) - 1:
				# The space NVDA parks a caret on at the end of every reading unit, with no caret
				# on it. Counted, it made a line ending exactly at a page's edge reach a page
				# further across, with nothing on it. See `_withoutAnEmptyLastRow`.
				content = content[:-1]
			cells += content
			where += tuple(range(len(content)))
		start = max(0, self.unwrappedPage) * self.numCols
		row = cells[start : start + self.numCols]
		positions = where[start : start + self.numCols]
		return RenderedBlock(
			blockId=block.blockId,
			rows=(row,),
			positions=(positions,),
			renderKey=self._keyWith(len(prefix)),
			depth=block.depth,
			moreRows=False,
			rawText=getattr(block.region, "rawText", ""),
			gapBefore=block.gapBefore,
			gapAfter=block.gapAfter,
			isBlank=block.isBlank,
			isDecoration=block.isDecoration,
			lineCells=len(cells),
		)

	def renderAround(self, block: SourceBlock, position: int, contextRows: int = 0) -> RenderedBlock:
		"""Lay out the chunk containing one braille position.

		A caret can be past the first chunk, so its row is found by a separate pass.

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

		Measured at the narrower width a wrapped block is laid out in.
		"""
		rest = self.indentPlan.continuationPrefixFor(block.depth)
		buffer = self._layoutBuffer(block, width=self.numCols - len(rest) if rest else None)
		if buffer is None:
			return None
		if _rowsAreFixedWidth(buffer):
			cells = buffer.brailleCells
			if not cells or not 0 <= position < len(cells):
				return None
			return position // max(1, buffer.rect.numCols)
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

		Wrapped rows are indented further (see `flowIndent.CONTINUATION_EXTRA`), so only an item
		that wraps is laid out again at the narrower width. Indent cells map to `NO_POSITION`.

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

		The block's own first row gets `first`, every other row `rest`, counted in the whole
		block rather than this chunk.
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
		glyphs: bool = True,
	) -> Optional[BrailleBufferSegment]:
		"""Build the buffer one block is laid out in.

		:param block: the block to lay out.
		:param width: how many cells wide to cut its rows, defaulting to the whole band. An
			indented block is laid out in what its indent leaves.
		:param glyphs: whether a role or state may be drawn as a shape. False for a table cell,
			whose positions are packed (see `flowTable.cellPosition`).
		:return: the buffer, its window at the start of the block, or None if the block
			could not be laid out at all.
		"""
		if glyphs:
			# Before the buffer reads the region, so that the cells a symbol gives back are
			# cells the wrapping can use. Compressing afterwards would shorten a row and change
			# nothing about how much fits on it. Does nothing at all unless the reader asked
			# for it and the display can draw one; see `glyphFlow`.
			glyphFlow.compressRegion(block.region, self.glyphTarget)
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

		Rows before `fromRow` are walked and discarded, since each row's end depends on the last.

		:param buffer: the buffer holding the block.
		:param fromRow: the first row of the block to keep.
		:return: the rows kept, their position maps, and whether the block continues past
			them.
		"""
		if _rowsAreFixedWidth(buffer):
			return self._filledLayout(buffer, fromRow)
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
					return self._withoutAnEmptyLastRow(buffer, rows, positions, more)
			if buffer.windowEndPos >= len(cells):
				break
			if not buffer._nextWindow():
				break
		return self._withoutAnEmptyLastRow(buffer, rows, positions, False)

	def _withoutAnEmptyLastRow(
		self,
		buffer: BrailleBufferSegment,
		rows: list,
		positions: list,
		more: bool,
	) -> tuple[list, list, bool]:
		"""Drop a last row that holds nothing but blank cells.

		`TextInfoRegion.update` ends each unit with a space for the cursor, which can wrap onto a
		row of its own. Kept when the cursor is in it, as in an edit box filled to the width.
		Only the last row of a block that ends here, and never a block that is only a blank row.

		:param buffer: the buffer the block was laid out in, asked where its cursor is.
		:param rows: the rows cut from it.
		:param positions: where each of their cells came from.
		:param more: whether the block continues past them.
		:return: the same three, one row shorter where the row was worth nothing.
		"""
		if more or len(rows) < 2 or any(rows[-1]):
			return rows, positions, more
		cursor = getattr(buffer, "cursorPos", None)
		if cursor is not None and cursor in positions[-1]:
			return rows, positions, more
		return rows[:-1], positions[:-1], more

	def _filledLayout(self, buffer: BrailleBufferSegment, fromRow: int) -> tuple[list, list, bool]:
		"""Cut a block into rows by arithmetic, for the mode where every row is the same width.

		The rows `_layout` walks to, without the walk: row n is the cells from `n * width` (see
		`layout.calculateFilledRowOffsets`). Used by narrow bands and every table cell.

		:param buffer: the buffer holding the block.
		:param fromRow: the first row of the block to keep.
		:return: the rows kept, their position maps, and whether the block continues past
			them.
		"""
		cells = buffer.brailleCells
		width = max(1, buffer.rect.numCols)
		# At least one, because a blank line is a row and has to occupy the one it deserves.
		total = max(1, -(-len(cells) // width))
		rows: list[tuple[int, ...]] = []
		positions: list[tuple[int, ...]] = []
		for index in range(max(0, fromRow), min(total, max(0, fromRow) + self.maxRows)):
			start = index * width
			end = min(len(cells), start + width)
			rows.append(tuple(cells[start:end]))
			positions.append(tuple(range(start, end)))
		more = max(0, fromRow) + len(rows) < total
		return self._withoutAnEmptyLastRow(buffer, rows, positions, more)

	def _rowFrom(self, cells: list, rowPositions) -> tuple[tuple[int, ...], tuple[int, ...]]:
		"""Build one row and the map back to where each of its cells came from.

		As `BrailleBuffer._get_windowBrailleCells`, with positions; a continuation mark comes
		from no position. Left unpadded, since padding is `flow.assembleCells`' job.

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
