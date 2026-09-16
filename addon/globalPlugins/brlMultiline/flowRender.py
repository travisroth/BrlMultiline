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

import dataclasses
from typing import TYPE_CHECKING, Optional

import config
from braille.constants import CONTINUATION_SHAPE
from logHandler import log

from . import glyphFlow
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

A working set, not a limit: a block longer than this is rendered in chunks, and the
controller asks for the next one when the reader pans towards it. What it bounds is the
cells held for a pathological block — a whole document rendered as one paragraph, a
minified script in a code view — not how much of it can be read.
"""


def _rowsAreFixedWidth(buffer: BrailleBufferSegment) -> bool:
	""":return: whether every row of a buffer is exactly its width, so rows can be counted.

	True for the fill mode with no continuation marks, which is what
	`layout.calculateFilledRowOffsets` produces when it is given no cut test: each row is the
	full width and the next one starts where it ended. A mark costs a row its last cell, and
	word wrapping moves the cut to a boundary, so in either of those where a row ends depends
	on where the one before it ended and the rows have to be walked.

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
		:param indentPlan: how a block's depth is drawn. The default draws nothing, which is
			what prose wants and what everything wanted before depth existed.
		:param columnPlan: where a table row's columns go. The default draws nothing, which
			is reading order and is what every block that is not a table row gets.
		:raises ValueError: if the width is not positive.
		"""
		if numCols < 1:
			raise ValueError(f"A band needs at least one cell, got {numCols}")
		self.glyphTarget = None
		"""The display this band's blocks are being drawn on, or None if none of them draws.

		Set by the band, because where the band is is not something a renderer can see and is
		not fixed: the band can be moved between displays, and a member of a composite can be
		unplugged. None means no shape is drawn and no word is taken out — which is what a
		region bound for a display that cannot draw one must get. See `glyphFlow.targetForRows`.
		"""
		self.glyphsWanted = False
		"""Whether the reader's setting asked for shapes when the band was last laid out for them.

		Kept beside `glyphTarget` because both decide what a block looks like, and a review found the
		setting turned off on a band that stayed on the same display leaving every shape in place. See
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

		Held rather than passed, so that none of the controller's six render calls has to
		know about depth. Replaced wholesale when the band rebases, which is also what
		invalidates the renderings drawn under the old one — see `flow.RenderKey.indent`.
		"""

		self.columnPlan = columnPlan
		"""Where a table row's columns go. See `flowTable`.

		Held for the same reason `indentPlan` is, and part of the rendering key for the same
		reason: a row drawn under one set of column widths must never be served for a row
		drawn under another, or the reader's finger finds the previous layout's columns.
		"""

	@property
	def renderKey(self) -> RenderKey:
		"""What this renderer's output depends on, beyond the content itself.

		A rendering carries this so that a changed braille table or a resized band
		invalidates the cells while leaving block identity alone, and the reader keeps
		their place across the change.
		"""
		return self._keyWith(0)

	def keyFor(self, block: SourceBlock) -> RenderKey:
		""":return: the key a rendering of one block would carry if it were made now.

		So that a caller holding a rendering can ask whether it is still the one this
		renderer would produce, rather than producing it again to find out. The indent is
		the block's own, which is what `render` puts in the key; a table row drawn at its
		plan's offsets carries no indent, exactly as `_chunkOf` records.

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

		Cut rather than wrapped, and one row whatever it holds. A pinned row that changed
		height would move every row under it, which is the one thing a pinned row exists not
		to do: the reader finds a column by its offset, and they can only do that while the
		offsets stay where they were.

		The plan is swapped for its cutting form for the duration rather than the rows being
		trimmed afterwards, because trimming would keep the *wrapped* first line — a header
		laid out for two rows shows only `width - indent` cells on the first, so "%Change" in
		an eight cell column would come out as "%Chang" with two cells of nothing beside it.

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

		The one thing in this file that does not go through a braille buffer holding the
		whole block. It cannot: the row's cells come from different places in the document
		and each has to be translated on its own, or the widths are character counts and the
		columns do not line up. So each cell is laid out in a buffer of its own at its own
		column's width — the same machinery, several times — and the results are placed.

		**A packing row is a lane, and a lane is as tall as the tallest cell in it.** The
		plan may put columns on more than one row of the band; a cell in the first of those
		that wraps needs the rows underneath it, and those rows are not the second lane's to
		take. Adding the wrapped line's index to the lane number gave both of them the same
		row and the later column won — a wrapped value with the next column written through
		the middle of it. Lanes are stacked by their own heights instead.

		Every drawn row is the full width of the band, padding included, which is the one
		place a rendering here is padded. `assembleCells` pads a short row at its right hand
		end; a table row needs padding *between* its columns as well, and that padding is
		what puts the next column where the reader's finger expects it.

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

		The whole of it, however tall: what the band shows is decided afterwards, by
		`_chunkOf`. Deciding it here is what let content be dropped without anything saying
		so.

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
						# A cell that came from nowhere stays from nowhere. Packing
						# `NO_POSITION` was packing a negative number, which came back out of
						# `positionParts` as a real column one lower with an enormous offset —
						# so the continuation indent of one column claimed to be content of
						# the column before it, and a routing key over it would have gone
						# there.
						mark = marks[offset]
						if mark != NO_POSITION:
							drawn = True
						where[target][place.offset + offset] = (
							NO_POSITION if mark == NO_POSITION else cellPosition(place.column.index, mark)
						)
				# **An empty cell is still a place, and it is as wide as its column.** It
				# draws nothing, so without this it left no position anywhere on the row —
				# and a position is what the cursor is found by and what a routing key is
				# turned back into. So the reader arriving in an empty cell got no cursor at
				# all and could not tell which column they were in, and no routing key would
				# take them into one. Reported from an Excel sheet, on the first blank row
				# under the data, where *every* cell is empty and the whole row was
				# unreachable and unmarked.
				#
				# Marking only the column's first band cell fixed the cursor and did not fix
				# routing, which is the second report: on a blank row nothing is drawn, so
				# there is no way to feel which single cell of thirty-two is the live one,
				# and every press either side of it reached nothing. The reader's own way of
				# aiming is the pinned header — press under the heading you want — and that
				# only works if the whole of the column answers.
				#
				# So every band cell the column occupies says which column it is. All at
				# offset zero, not at their own offsets: a cell is routed to as a place
				# rather than at the character under the finger (see
				# `flowTableSource.TableCellRegion.routeTo`), and one position for the whole
				# of it also keeps the cursor at the column's start, since
				# `FlowController.cursorCell` takes the first band cell that matches.
				if not drawn and start < total:
					for offset in range(place.column.width):
						at = place.offset + offset
						if at >= plan.numCols:
							break
						where[start][at] = cellPosition(place.column.index, 0)
		return rows, where

	def _chunkOf(self, block: SourceBlock, rows: list, where: list, fromRow: int) -> RenderedBlock:
		"""Take the part of a table row the band will show, and say whether there is more.

		A table row taller than `flowTable.MAX_TABLE_ROWS` is not refused and is not silently
		cut: it is a block with more rows, which is the same thing a paragraph longer than the
		band is, and the window pans through it by the machinery that already exists. Saying
		`moreRows` is false while dropping the rest was the alternative and it is the one
		thing that must not happen — the reader is promised every value and has no way to
		tell they are not getting one.

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

		Cut, not wrapped at word boundaries: a column six cells wide has no room to keep
		words whole, and a reader comparing values down a column is reading positions rather
		than prose. That is the renderer's fill mode, which a table always asks for.

		A cell that fits is one row and this is one layout. A cell that does not is laid out
		again at the column's width less its indent, because its continuation rows are
		indented — see `flowTable.indentFor`. The first row is cut at the narrower width too,
		which costs it the indent in blank cells at the right; the alternative is re-cutting
		a remainder, which is a second way of cutting text to keep in step with the first.

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
		glyphs: bool = True,
	) -> Optional[BrailleBufferSegment]:
		"""Build the buffer one block is laid out in.

		:param block: the block to lay out.
		:param width: how many cells wide to cut its rows, defaulting to the whole band. An
			indented block is laid out in what its indent leaves.
		:param glyphs: whether a role or state in this block may be drawn as a shape. False
			for one cell of a table row: a cell's positions are packed with the column they
			came from — see `flowTable.cellPosition` — so a mark naming one of its cells would
			never be found again, and compressing it would leave the cell the shape was meant
			to stand on with nothing drawn over it.
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

		Rows before `fromRow` are walked and discarded rather than skipped: where a row ends
		depends on where the one before it ended, so the only way to know is to cut them.
		The translation is done once either way, when the region is read.

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

		**A row of the band is too expensive for the space NVDA parks a caret on.** Every
		reading unit NVDA hands over ends with a space it added on purpose — see
		`TextInfoRegion.update`, "in case the cursor is at the end of the reading unit" — and
		that space is a cell like any other. A block whose content fills the band exactly
		therefore needs one more cell than the row has, and the wrap put that one blank cell on
		a row of its own. On an eight row band that is an eighth of the display spent on
		nothing, and under the fingers it reads as a line skipped between two links, as though
		the first were continuing.

		**Unless the cursor is in it**, which is the case the space was added for. In a document
		the caret sits on a character and never on that space, so the row goes; in an edit box
		filled to exactly the width the caret does sit there, and a caret with no cell is worse
		than a wasted row. The block is read again on every keystroke while it is being written
		in, so the question is asked afresh exactly when the answer can change.

		Only the last row, and only when the block ends here: a blank row in the middle of a
		block is a blank line the document really has, and a chunk that stops short does not
		know what follows it. A block that is nothing but a blank row keeps it, because that is
		a blank line and a blank line is a row.

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

		The same rows `_layout` walks to, without the walk. Filling a row means carrying
		straight on at the width the buffer was given — see `layout.calculateFilledRowOffsets`
		— so row *n* is the cells from `n * width`, and the tail of a long block can be
		reached without cutting everything in front of it first.

		The walk is not wasteful by accident: where a row ends genuinely depends on where the
		one before it ended, which is why word wrapping still has to be walked. It is only
		this mode that has nothing to remember, and this mode is what a narrow band and every
		table cell use. A reader typing at the end of a very long paragraph was paying for a
		re-cut of the whole of it on each keystroke to be shown ten rows.

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
