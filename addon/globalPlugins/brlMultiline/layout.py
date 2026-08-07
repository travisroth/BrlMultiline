# BrlMultiline: segment geometry.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Geometry for dividing a braille display into segments.

This module deliberately imports nothing from NVDA, so that the arithmetic can be unit
tested without a running screen reader or an attached display.

A segment occupies a rectangle of the display. Two shapes are produced in practice:

1. On a display with more than one row, segments are groups of whole rows spanning the
	full width. A Monarch with 8 rows of 32 cells divided into 3 segments yields row
	groups of 3, 3 and 2 rows.
2. On a single row display, segments are column slices of that row. A Focus 80 divided
	into 2 segments yields two 40 cell halves.
"""

import dataclasses


@dataclasses.dataclass(frozen=True)
class SegmentRect:
	"""The rectangle of the braille display occupied by one segment.

	Positions are zero based, in display coordinates.
	"""

	row: int
	"""Index of the topmost display row this segment occupies."""

	col: int
	"""Index of the leftmost display column this segment occupies."""

	numRows: int
	"""Number of display rows this segment occupies."""

	numCols: int
	"""Number of display columns this segment occupies."""

	@property
	def displaySize(self) -> int:
		""":return: the total number of cells in this segment."""
		return self.numRows * self.numCols


def divideEvenly(total: int, parts: int) -> list[int]:
	"""Divide `total` into `parts` sizes that differ by at most one.

	Any remainder is given to the earlier parts, so that the larger segments appear at
	the top of the display (or at the left, on a single row display).

	:param total: the amount to divide.
	:param parts: the number of pieces to divide it into.
	:return: a list of `parts` sizes summing to `total`.
	:raises ValueError: if `parts` is not positive, or is larger than `total`.
	"""
	if parts < 1:
		raise ValueError(f"Number of segments must be at least 1, got {parts}")
	if parts > total:
		raise ValueError(f"Cannot divide {total} into {parts} segments of at least 1 each")
	size, remainder = divmod(total, parts)
	return [size + 1 if index < remainder else size for index in range(parts)]


def calculateSegmentRects(
	numRows: int,
	numCols: int,
	layout: int | list[int],
) -> list[SegmentRect]:
	"""Work out the rectangle each segment occupies.

	:param numRows: number of rows on the display.
	:param numCols: number of columns on the display.
	:param layout: either a count, meaning divide the display evenly into that many
		segments, or an explicit list of segment sizes. Sizes are measured in rows when
		the display has more than one row, and in cells when it has a single row.
	:return: one rectangle per segment, ordered top to bottom (or left to right on a
		single row display).
	:raises ValueError: if the layout does not fit the display.
	"""
	if numRows < 1 or numCols < 1:
		raise ValueError(f"Display has no cells to divide: {numRows} rows by {numCols} columns")
	# On a multi row display segments are measured in rows; on a single row display, in cells.
	total = numRows if numRows > 1 else numCols
	if isinstance(layout, int):
		sizes = divideEvenly(total, layout)
	else:
		sizes = list(layout)
		if not sizes:
			raise ValueError("Layout must contain at least one segment")
		if any(size < 1 for size in sizes):
			raise ValueError(f"Every segment must be at least 1, got {sizes}")
		if sum(sizes) != total:
			raise ValueError(f"Segment sizes {sizes} sum to {sum(sizes)}, but the display has {total}")
	rects: list[SegmentRect] = []
	offset = 0
	for size in sizes:
		if numRows > 1:
			rects.append(SegmentRect(row=offset, col=0, numRows=size, numCols=numCols))
		else:
			rects.append(SegmentRect(row=0, col=offset, numRows=1, numCols=size))
		offset += size
	return rects


def calculateFilledRowOffsets(
	bufferLength: int,
	startPos: int,
	numRows: int,
	numCols: int,
	isMidWordCut=None,
) -> list[tuple[int, int, bool]]:
	"""Work out where each row of a segment's window starts and ends, filling every row.

	This is the arithmetic behind the `fillRows` option: every row is filled to its full
	width and the text carries straight on, even mid word. A narrow segment, such as a
	table cell 8 cells wide, has nothing to spare for keeping words whole.

	:param bufferLength: number of braille cells in the segment's buffer.
	:param startPos: where the window starts in that buffer.
	:param numRows: rows in the segment.
	:param numCols: columns in the segment.
	:param isMidWordCut: optional callable taking a buffer position and returning whether
		a cut there would fall in the middle of a word. When given, a row cut mid word
		gives up its last cell to a continuation mark.
	:return: one tuple of start, end and whether to show a continuation mark, per row.
	"""
	if bufferLength <= 0:
		return [(0, 0, False)]
	rows: list[tuple[int, int, bool]] = []
	start = max(0, min(startPos, bufferLength))
	for _row in range(max(1, numRows)):
		end = start + numCols
		if end >= bufferLength:
			rows.append((start, bufferLength, False))
			break
		showContinuationMark = False
		if isMidWordCut is not None and isMidWordCut(end):
			end -= 1
			showContinuationMark = True
		rows.append((start, end, showContinuationMark))
		start = end
	return rows or [(0, 0, False)]


def calculateGridRects(
	numRows: int,
	numCols: int,
	rowBands: list[int],
	colWidths: list[int],
) -> list[SegmentRect]:
	"""Divide the display into a grid of segments.

	Not reachable from the settings dialog, which offers whole row groups only. This is
	for code that drives the display directly: a table reader, for instance, might use
	four columns of 8 cells across three bands of 2 rows to show twelve cells at once.

	:param numRows: number of rows on the display.
	:param numCols: number of columns on the display.
	:param rowBands: heights of each band, in rows, top to bottom.
	:param colWidths: widths of each column, in cells, left to right.
	:return: one rectangle per grid cell, in reading order: the whole of the first band
		left to right, then the second band, and so on.
	:raises ValueError: if the grid does not fit the display.
	"""
	if sum(rowBands) > numRows:
		raise ValueError(f"Row bands {rowBands} need {sum(rowBands)} rows, but the display has {numRows}")
	if sum(colWidths) > numCols:
		raise ValueError(f"Columns {colWidths} need {sum(colWidths)} cells, but the display has {numCols}")
	if any(size < 1 for size in rowBands + colWidths):
		raise ValueError("Every band and column must be at least 1")
	rects: list[SegmentRect] = []
	row = 0
	for bandHeight in rowBands:
		col = 0
		for width in colWidths:
			rects.append(SegmentRect(row=row, col=col, numRows=bandHeight, numCols=width))
			col += width
		row += bandHeight
	return rects


def validateRects(rects: list[SegmentRect], numRows: int, numCols: int) -> None:
	"""Check that a set of segment rectangles can be shown on a display.

	This is the only limit on how many segments there may be: they have to fit. Gaps are
	allowed, and show as blank cells, which is useful for putting space between segments.
	Overlaps are not, because the compositor would silently let one segment overwrite
	another.

	:param rects: the rectangles to check.
	:param numRows: number of rows on the display.
	:param numCols: number of columns on the display.
	:raises ValueError: if the rectangles are empty, out of bounds, or overlap.
	"""
	if not rects:
		raise ValueError("A view must have at least one segment")
	occupied: set[tuple[int, int]] = set()
	for index, rect in enumerate(rects):
		if rect.numRows < 1 or rect.numCols < 1:
			raise ValueError(f"Segment {index} has no cells: {rect}")
		if rect.row < 0 or rect.col < 0:
			raise ValueError(f"Segment {index} starts outside the display: {rect}")
		if rect.row + rect.numRows > numRows or rect.col + rect.numCols > numCols:
			raise ValueError(
				f"Segment {index} runs past the edge of a {numRows} by {numCols} display: {rect}",
			)
		for row in range(rect.row, rect.row + rect.numRows):
			for col in range(rect.col, rect.col + rect.numCols):
				if (row, col) in occupied:
					raise ValueError(f"Segment {index} overlaps an earlier segment at row {row}, cell {col}")
				occupied.add((row, col))


def findSegmentAtWindowPos(
	rects: list[SegmentRect],
	windowPos: int,
	numCols: int,
) -> tuple[int, int]:
	"""Locate the segment containing a display window position.

	Used to route a cursor routing key press to the correct segment. NVDA reports the
	position as a flat index into a row major array of cells, so it encodes both a row
	and a column.

	:param rects: the segment rectangles, as returned by L{calculateSegmentRects}.
	:param windowPos: position within the whole display window.
	:param numCols: number of columns on the display.
	:return: a tuple of the segment index and the position within that segment, the
		latter again expressed as a flat row major index.
	:raises LookupError: if the position falls outside every segment.
	"""
	if numCols < 1:
		raise LookupError(f"Display has no columns, cannot locate position {windowPos}")
	row, col = divmod(windowPos, numCols)
	for index, rect in enumerate(rects):
		if rect.row <= row < rect.row + rect.numRows and rect.col <= col < rect.col + rect.numCols:
			segmentPos = (row - rect.row) * rect.numCols + (col - rect.col)
			return index, segmentPos
	raise LookupError(f"No segment contains display position {windowPos} (row {row}, column {col})")


def segmentPosToWindowPos(rect: SegmentRect, segmentPos: int, numCols: int) -> int:
	"""Convert a position within a segment to a position within the display window.

	The inverse of the mapping performed by L{findSegmentAtWindowPos}. Used to report a
	segment's cursor position to the braille handler, which knows only about a single
	cursor on a single display.

	:param rect: the rectangle occupied by the segment.
	:param segmentPos: position within the segment, as a flat row major index.
	:param numCols: number of columns on the display.
	:return: the corresponding position within the whole display window.
	:raises ValueError: if the position falls outside the segment.
	"""
	if not 0 <= segmentPos < rect.displaySize:
		raise ValueError(f"Position {segmentPos} is outside a segment of {rect.displaySize} cells")
	segmentRow, segmentCol = divmod(segmentPos, rect.numCols)
	return (rect.row + segmentRow) * numCols + rect.col + segmentCol
