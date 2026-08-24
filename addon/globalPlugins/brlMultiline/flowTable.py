# BrlMultiline: deciding where a table's columns go.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Where a table's columns sit on the band, and how wide each of them is.

A table read one cell at a time is a table nobody can compare. NVDA reads a row left to
right, reproducing each cell's header before its value, and on one line that is the only
thing it could do — but the whole reason a watchlist is a table is that the reader wants to
run a finger *down* a column. That needs the same column to start at the same cell offset on
every row, and it needs enough rows on the display at once for "down" to mean anything.

This module is the arithmetic and nothing else, and it is here on its own for the reason
`flowIndent` is: it imports nothing from NVDA, so every rule below can be tested without a
screen reader or a display. What it never sees is a table — only a list of measurements and
a width to fit them into.

**Widths are measured in cells, not characters.** A caller that measures text length is
measuring the wrong thing: a contracted table translates "Change" to four cells and "%" to
one, and columns aligned by character count are columns that are not aligned. Measuring
means translating, which is the caller's business and costs what it costs; this module takes
the answer.

**The plan is made once and kept.** Indent rebases as the band moves, because a margin that
shifts by two cells is a small thing beside drawing the wrong depth. Columns are the
opposite: a column that moves is a column the reader has to find again, and they would move
on every scroll if the widths came from whatever rows happen to be on the display. So a plan
is made from what was measured when the reader entered the table and kept until something
makes it wrong — see `shouldReplan`, which answers no to almost everything.

**A row may take more than one row of the band.** Five columns that want thirty-four cells
do not fit on thirty-two, and there are only two honest answers: give every column less, or
let the row run onto a second band row. Which one is `maxRows`, a setting, because a reader
comparing four numbers wants them side by side at any cost and a reader reading addresses
wants them legible. Columns are packed greedily across the rows allowed, and what will not
fit in them is dropped rather than squeezed past legibility — a column at two cells says
nothing that its absence does not.
"""

import dataclasses
from typing import Iterable, Optional, Sequence

TRUNCATE = "truncate"
WRAP = "wrap"

OVERFLOW_STYLES: tuple[str, ...] = (TRUNCATE, WRAP)
"""What becomes of a cell too long for its column.

Truncate cuts it at the column's width. Wrap continues it on the next band row this table
row is using, in the same column, which only exists where `maxRows` allows a second row.

Truncate is the default because a truncated column is still a column: every row is cut at
the same place, so the shape down the display survives and the reader can widen the column
or read the cell on its own. A wrapped column is the one that destroys the shape, since one
long cell pushes that row's other columns down and nothing lines up any more.
"""

DEFAULT_OVERFLOW = TRUNCATE

COLUMN_GAP = 1
"""Cells between one column and the next.

One, and blank. Two is a cell a 32 cell display has not got to spare four times over, and
none at all runs the columns together — a truncated cell ends mid word and the next column
starts immediately, which reads as one word.
"""

MIN_COLUMN_CELLS = 3
"""The narrowest a column may be drawn.

Below this a column is not short, it is gone: three cells of a contracted word is a letter
and a bit, and a reader cannot tell "AAPL" from "AAL" in it. A column that cannot have three
cells is left out of the plan instead, which at least says plainly that it is not there.
"""

MAX_COLUMN_CELLS = 40
"""The widest a column may be drawn, however long its content is.

A description column in a table of files is hundreds of characters and would take the whole
band for itself. The cap is generous — wider than a Monarch's row — so that it only bites on
the genuinely runaway column, and a reader who wants more sets it.
"""

MAX_TABLE_ROWS = 4
"""How many band rows one table row may use.

Four of a Monarch's eight, so that at the very worst two table rows are on the display at
once. A "table" laid out taller than that is not being read as a table any more, and reading
order — which NVDA already does well — is the better answer for it.
"""

DEFAULT_MAX_ROWS = 1
"""How many band rows one table row uses unless the reader says otherwise.

One. The comparison a table is for is between rows, and rows the reader can hold under one
hand at once. Eight one-row records beat two four-row ones for every question a watchlist is
asked, and a reader who needs the fourth column legible can raise this or hide a column.
"""


@dataclasses.dataclass(frozen=True)
class Column:
	"""One column, as it will be drawn."""

	index: int
	"""The table's own column number, one based, as NVDA reports it.

	Kept rather than the position in the plan, because the plan's order is the drawing order
	and a hidden column leaves a hole in it. Everything that has to go back to the table —
	fetching the cell, routing a finger press into it — needs the table's number.
	"""

	width: int
	"""How many cells it is drawn in."""

	label: str = ""
	"""The header, where the table has one.

	Carried through the plan although nothing in this milestone draws it: it is what the
	pinned header row will hold, and it is what a saved layout matches a table by. Measuring
	it is also the only honest way to decide a default width for a column whose content is
	all short — a column of one character flags under a header called "Watched".
	"""

	overflow: str = DEFAULT_OVERFLOW
	"""What becomes of a cell too long for `width`. See `OVERFLOW_STYLES`."""


@dataclasses.dataclass(frozen=True)
class Placement:
	"""Where one column is drawn within a table row."""

	column: Column
	row: int
	"""Which of the table row's band rows it is on, zero based."""

	offset: int
	"""Which cell of that band row it starts at."""

	@property
	def end(self) -> int:
		""":return: one past the last cell it occupies."""
		return self.offset + self.column.width


@dataclasses.dataclass(frozen=True)
class ColumnPlan:
	"""One table's layout, fixed for as long as the reader is in that table.

	Frozen because it is part of a rendering's key, exactly as `IndentPlan` is: two rows drawn
	under different plans are two different renderings and one must never be served for the
	other.
	"""

	columns: tuple[Column, ...] = ()
	"""The columns to draw, in drawing order. A hidden column is simply not here."""

	numCols: int = 0
	"""The band width the plan was made for."""

	maxRows: int = DEFAULT_MAX_ROWS
	"""How many band rows one table row may use."""

	gap: int = COLUMN_GAP
	"""Cells between one column and the next."""

	dropped: tuple[int, ...] = ()
	"""The table's numbers for columns that did not fit, in the order they were asked for.

	Recorded rather than discarded, so that something can say so. A column silently absent
	from a display is indistinguishable from a column the table does not have, and a reader
	deciding whether to widen the band or hide something else needs to know which.
	"""

	narrowed: tuple[int, ...] = ()
	"""The table's numbers for columns drawn narrower than their content asked for.

	The other half of `dropped`, and the half a reader meets far more often. Three columns of
	real text on a thirty-two cell band leave ten cells each, and ten cells of "Software
	Engineer" is "Software E" — the layout is working exactly as designed and the reader is
	looking at cut words with nothing to say they are cut. What they do about it is a
	decision — raise the row height, hide a column, live with it — and they cannot make it
	from a display that looks like a table with short entries in it.
	"""

	@property
	def isEmpty(self) -> bool:
		""":return: whether this plan draws nothing, which is what reading order gets."""
		return not self.columns

	def placements(self) -> tuple[Placement, ...]:
		"""Where every column goes, packed greedily across the rows allowed.

		Greedy rather than balanced: the reader's eye and hand both go left to right and then
		down, so the first row of a record should hold as much of it as it can. Balancing the
		rows would put the second column on the second row of a two row record for the sake of
		an even shape nobody is reading.

		:return: one placement per column, in drawing order.
		"""
		placed: list[Placement] = []
		row = 0
		offset = 0
		for column in self.columns:
			if offset and offset + self.gap + column.width > self.numCols:
				row += 1
				offset = 0
			elif offset:
				offset += self.gap
			placed.append(Placement(column=column, row=row, offset=offset))
			offset += column.width
		return tuple(placed)

	@property
	def numRows(self) -> int:
		""":return: how many band rows one row of this table takes."""
		placed = self.placements()
		return 1 + max((place.row for place in placed), default=0) if placed else 1

	def columnAt(self, row: int, offset: int) -> Optional[Column]:
		"""Which column a cell of a drawn row belongs to.

		What routing needs: a finger lands on a cell of the band and the answer has to be a
		cell of the table, or the press must do nothing. The gaps between columns belong to
		no column and answer None, which is the same answer padding gives.

		:param row: which band row of the table row, zero based.
		:param offset: which cell of it.
		:return: the column, or None for a gap or past the end.
		"""
		for place in self.placements():
			if place.row == row and place.offset <= offset < place.end:
				return place.column
		return None

	def __repr__(self) -> str:
		if self.isEmpty:
			return "<ColumnPlan reading order>"
		widths = ", ".join(f"{column.index}:{column.width}" for column in self.columns)
		return f"<ColumnPlan {widths} in {self.numCols} over {self.numRows} rows>"


READING_ORDER = ColumnPlan()
"""The plan that draws nothing, and what a table gets until the reader asks for otherwise.

Not a placeholder: reading order is what NVDA does today, it is right for a table of prose,
and decision 16 says it survives. A flow with this plan reads a table exactly as it reads
anything else.
"""


@dataclasses.dataclass(frozen=True)
class Measurement:
	"""What was found in one column of the table, in cells."""

	index: int
	"""The table's own column number, one based."""

	width: int
	"""The widest content found in it, in cells. See the module docstring on measuring."""

	label: str = ""
	"""Its header, where it has one."""

	hidden: bool = False
	"""Whether the table itself is not showing this column.

	A zero width column in a list view, a `display: none` cell on a page. Excluded from the
	plan rather than given a width, because a column the sighted reader cannot see is not a
	column the braille reader is missing.
	"""


def planFor(
	measured: Iterable[Measurement],
	numCols: int,
	maxRows: int = DEFAULT_MAX_ROWS,
	gap: int = COLUMN_GAP,
	minWidth: int = MIN_COLUMN_CELLS,
	maxWidth: int = MAX_COLUMN_CELLS,
) -> ColumnPlan:
	"""Work out where a table's columns go.

	Each column asks for what its widest content needs, and then everything is fitted into
	what the band has. Fitting is done by taking from the widest columns first, one cell at a
	time, down to `minWidth`: the column with forty cells of description can spare ten before
	the column with six cells of ticker can spare one, and a proportional cut would take from
	both.

	If the columns still do not fit in `numCols` times `maxRows`, the ones that will not fit
	are dropped from the right. Dropped rather than squeezed, because a column below
	`minWidth` is not a narrow column, it is a column that says nothing — and `dropped`
	records them so that something can tell the reader.

	:param measured: what was found in each column. Hidden ones are ignored.
	:param numCols: the width of the band.
	:param maxRows: how many band rows one table row may use.
	:param gap: cells between columns.
	:param minWidth: the narrowest a column may be drawn.
	:param maxWidth: the widest, however long the content.
	:return: the plan, or `READING_ORDER` when there is nothing to lay out.
	"""
	wanted = [item for item in measured if not item.hidden]
	maxRows = max(1, min(maxRows, MAX_TABLE_ROWS))
	if not wanted or numCols < minWidth:
		return READING_ORDER
	widths = {item.index: max(minWidth, min(maxWidth, max(item.width, len(item.label)))) for item in wanted}
	budget = _budgetFor(numCols, maxRows, len(wanted), gap)
	_shrinkToFit(widths, budget, minWidth)
	kept, dropped = _dropWhatWillNotFit(wanted, widths, numCols, maxRows, gap, minWidth)
	if not kept:
		return READING_ORDER
	wants = {item.index: max(item.width, len(item.label)) for item in wanted}
	return ColumnPlan(
		columns=tuple(Column(index=item.index, width=widths[item.index], label=item.label) for item in kept),
		numCols=numCols,
		maxRows=maxRows,
		gap=gap,
		dropped=tuple(item.index for item in dropped),
		narrowed=tuple(item.index for item in kept if widths[item.index] < wants[item.index]),
	)


def _budgetFor(numCols: int, maxRows: int, count: int, gap: int) -> int:
	""":return: how many cells of content the band has room for, gaps already taken out.

	An upper bound rather than an exact answer: the gaps are counted as though every column
	sat on one row, which over-counts by one gap for each row after the first. Over-counting
	makes the first fit attempt slightly pessimistic and never optimistic, and being
	pessimistic here costs a cell where being optimistic costs a column that does not fit.
	"""
	return max(0, numCols * maxRows - gap * max(0, count - 1))


def _shrinkToFit(widths: dict, budget: int, minWidth: int) -> None:
	"""Take cells from the widest columns until the total fits.

	From the widest first, one at a time, which is what keeps a short column short and a long
	one merely shorter. A proportional cut takes a cell from the ticker for every four it
	takes from the description, and the ticker is the column that cannot spare one.

	:param widths: column number to width, narrowed in place.
	:param budget: how many cells of content there is room for.
	:param minWidth: the narrowest a column may be drawn.
	"""
	for _ in range(sum(widths.values())):
		if sum(widths.values()) <= budget:
			return
		widest = max(widths, key=lambda index: (widths[index], -index))
		if widths[widest] <= minWidth:
			# Everything is as narrow as it may be drawn. What is left over is a column too
			# many, not a column too wide, and dropping is the next step rather than this one.
			return
		widths[widest] -= 1


def _dropWhatWillNotFit(
	wanted: Sequence[Measurement],
	widths: dict,
	numCols: int,
	maxRows: int,
	gap: int,
	minWidth: int,
) -> tuple[list, list]:
	"""Take columns off the right until the rest can be packed into the rows allowed.

	From the right because that is the order tables put their least important columns in, and
	because a reader who disagrees can hide a column by hand. Packed with the same greedy walk
	`ColumnPlan.placements` uses, so what this decides and what that draws cannot disagree.

	:return: the columns kept, and the columns dropped.
	"""
	kept = list(wanted)
	while kept:
		if _packs([widths[item.index] for item in kept], numCols, maxRows, gap):
			break
		kept.pop()
	dropped = list(wanted[len(kept) :])
	if kept and len(kept) < len(wanted):
		# Room was made by dropping, so give it back to what is left: a two column plan on a
		# 32 cell band should not leave fourteen cells blank because the widths were narrowed
		# to make three columns fit that then did not.
		_growIntoTheRoom(kept, widths, numCols, maxRows, gap, minWidth)
	return kept, dropped


def _packs(widths: Sequence[int], numCols: int, maxRows: int, gap: int) -> bool:
	""":return: whether these widths fit in the rows allowed, packed greedily."""
	if any(width > numCols for width in widths):
		return False
	rows = 1
	offset = 0
	for width in widths:
		if offset and offset + gap + width > numCols:
			rows += 1
			offset = 0
		elif offset:
			offset += gap
		offset += width
	return rows <= maxRows


def _growIntoTheRoom(
	kept: Sequence[Measurement],
	widths: dict,
	numCols: int,
	maxRows: int,
	gap: int,
	minWidth: int,
) -> None:
	"""Give the cells freed by a dropped column back to the columns that are left.

	To the narrowest first, and never past what its content asked for: a column widened past
	its own longest cell is blank cells with a name. The mirror of `_shrinkToFit`, and bounded
	the same way.
	"""
	asked = {item.index: max(minWidth, max(item.width, len(item.label))) for item in kept}
	for _ in range(numCols * maxRows):
		candidates = [item.index for item in kept if widths[item.index] < asked[item.index]]
		if not candidates:
			return
		narrowest = min(candidates, key=lambda index: (widths[index], index))
		widths[narrowest] += 1
		if not _packs([widths[item.index] for item in kept], numCols, maxRows, gap):
			widths[narrowest] -= 1
			return


def shouldReplan(
	plan: ColumnPlan,
	measured: Iterable[Measurement],
	numCols: int,
	maxRows: int = DEFAULT_MAX_ROWS,
	gap: int = COLUMN_GAP,
) -> bool:
	"""Whether a plan in force has stopped being usable.

	It answers no to almost everything, and that is the design rather than an optimisation.
	Indent rebases as the band moves because a margin two cells out is a small wrong; a column
	that moves is a column the reader has to find again, and finding it is the work the layout
	existed to save. So the widths are decided once, from what was measured on the way in, and
	kept while they still draw the table they were made for.

	What makes a plan unusable is a change to what it is a plan *of*: a different band width,
	a different row height, or a different set of columns. Content growing wider than its
	column is not one of those — that is what truncation is for, and re-jigging the whole
	table because one cell grew is exactly the dance this prevents.

	:param plan: the plan in force.
	:param measured: what is in the table now.
	:param numCols: the width of the band.
	:param maxRows: how many band rows one table row may use.
	:param gap: cells between columns.
	:return: whether to make a new plan.
	"""
	if plan.isEmpty:
		return False
	if plan.numCols != numCols or plan.gap != gap:
		return True
	if plan.maxRows != max(1, min(maxRows, MAX_TABLE_ROWS)):
		return True
	shown = tuple(item.index for item in measured if not item.hidden)
	drawn = tuple(column.index for column in plan.columns) + plan.dropped
	return shown != drawn


def describe(plan: ColumnPlan) -> str:
	""":return: the plan in one line, for the dry run's report.

	Says what was dropped as well as what was drawn, because those are the two questions a
	reader asks of a table that looks wrong and only one of them can be answered by feeling
	the display.
	"""
	if plan.isEmpty:
		return "reading order; no columns are laid out."
	drawn = ", ".join(
		f"column {place.column.index} at row {place.row} cell {place.offset} in {place.column.width}"
		for place in plan.placements()
	)
	notes = []
	if plan.narrowed:
		cut = ", ".join(str(index) for index in plan.narrowed)
		notes.append(f"Column {cut} is drawn narrower than its content and is cut.")
	if plan.dropped:
		missing = ", ".join(str(index) for index in plan.dropped)
		notes.append(f"No room for column {missing}.")
	return " ".join([f"{drawn}.", *notes])


SEPARATOR_CELL = 0
"""A blank cell, matching `flow.BLANK_CELL` and NVDA's own use of 0 for an empty cell."""

POSITION_STRIDE = 1 << 16
"""How far apart two columns' positions are in a drawn row's position map.

A rendered row maps each of its cells back to a position, and everything that reads that
map — routing a finger press, finding the cursor's row — treats a position as one number.
A table row's cells come from several places, so a position has to carry both which column
it is in and where in that column it is, and this is the base it is packed in.

Sixty-five thousand cells for one table cell, which no cell of any table reaches, and the
whole row is still a small number. Packed rather than kept as a table of its own, because a
map that lived beside the rendering would have to be kept in step with it, and a rendering
that is served from the cache with the wrong map routes the reader into the wrong column.
"""


def cellPosition(column: int, offset: int) -> int:
	"""Pack which column a cell came from, and where in it.

	**The table's own column number, not the column's place in the drawing order.** They are
	the same number only while every row holds every column the plan draws, and a row with a
	merged cell in it does not: the plan draws columns 1, 2, 3 and 4, the row holds 1, 3 and
	4, and the third thing drawn is the row's *second* cell. Packing the drawing order and
	unpacking it as a cell index sent a routing press into the wrong column of exactly the
	rows that are hardest to read, and reported the wrong text for them.

	The table's number is the one thing both sides already agree on — it is what the plan
	names its columns by and what the row names its cells by — so it is what travels.

	:param column: the table's own column number, one based.
	:param offset: the braille position within that column's own content.
	:return: the packed position.
	"""
	return column * POSITION_STRIDE + offset


def positionParts(position: int) -> tuple[int, int]:
	""":return: the column number and the position within it. The inverse of `cellPosition`."""
	return divmod(position, POSITION_STRIDE)


@dataclasses.dataclass(frozen=True)
class RowCell:
	"""One cell of a table row, as the source read it."""

	index: int
	"""The table's own column number, one based, matching `Column.index`."""

	region: object
	"""Whatever reads that cell: a braille region, and nothing here looks inside it."""


def rowCellsOf(region) -> Optional[Sequence[RowCell]]:
	""":return: a region's table cells, or None if it is not a table row.

	Asked of the region rather than recorded on the block, so that nothing in `flow.py` or
	`flowControl.py` has to learn what a table is. A row knows it is a row; everything else
	asks and gets None.
	"""
	cells = getattr(region, "cells", None)
	if not cells:
		return None
	return cells if all(isinstance(cell, RowCell) for cell in cells) else None
