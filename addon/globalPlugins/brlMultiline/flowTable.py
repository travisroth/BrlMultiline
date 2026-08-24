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
wants them legible.

**And a table may have more columns than any band can hold.** Twenty-nine of them is an
ordinary watchlist and thirty-two cells is an ordinary display, and no arithmetic reconciles
those. So the columns are dealt into *pages* — as many as fit at readable widths, then the
next page — and the reader moves between them, exactly as the window moves between rows. The
first attempt at this squeezed what it could into one band and dropped the rest, which gave
three cells of each of eight columns and eleven columns that did not exist. Nothing is
dropped now.
"""

import dataclasses
from typing import Iterable, Optional, Sequence

TRUNCATE = "truncate"
WRAP = "wrap"

OVERFLOW_STYLES: tuple[str, ...] = (TRUNCATE, WRAP)
"""What becomes of a cell too long for its column.

Wrap continues it on the next band row of the same table row, in the same column and
indented, so the table row grows as tall as its longest cell needs. Truncate cuts it at the
column's width and the table row stays one row tall.

**Wrap is the default, because a display that silently drops data is not a display of the
data.** Truncation keeps a beautiful shape down the band and the reader cannot tell a cell
that ended from a cell that was cut, which is the wrong way round: the shape is what they
are reading the table *for*, and the values are what they are reading it *about*.

Truncation is kept and it earns its place — it is what a reader who knows the table wants.
An options watchlist has symbols long enough to push every other column into uselessness,
and a reader who can recognise a contract from its first six cells would rather have the
whole table on one row of the band than have every row grow to fit the one column they can
already read. That is a judgement about a particular table by somebody who knows it, which
is exactly what a setting is for and exactly what a default must not assume.
"""

DEFAULT_OVERFLOW = WRAP

COLUMN_GAP = 1
"""Cells between one column and the next.

One, and blank. Two is a cell a 32 cell display has not got to spare four times over, and
none at all runs the columns together — a truncated cell ends mid word and the next column
starts immediately, which reads as one word.
"""

CELL_INDENT = 2
"""How far a wrapped cell's continuation is indented within its own column.

Less than a list's or a tree's, which is what `flowIndent.CONTINUATION_EXTRA` spends, because
a column has far fewer cells to spend than a row does: two of a six cell column is a third of
it, where two of a thirty-two cell row is a sixteenth.

It is still worth spending. The column boundary already says *which* column a continuation
belongs to; what it cannot say is that the row below is the same value still going rather
than the next value down, and in a column of numbers that is the difference between reading
a price and reading two.

Narrowed further on a narrow column — see `indentFor` — because a third of a column is a
great deal and the alternative to indenting less is not indenting at all.
"""

READABLE_CELLS = 6
"""The narrowest a column is shrunk to in order to make room for another.

Six, because below it a column stops being a value and becomes a fragment of one. A price of
"310.34" in three cells is "310" on one row and ".34" on the next, or with the continuation
indent a digit at a time — and a reader running down a column of those is not comparing
prices, they are assembling them.

It is a floor on *shrinking*, not a size. A column of one-character flags keeps its own
width; what this stops is a wide column being cut down past reading to make room for a
neighbour. What will not fit goes on the next page instead, which costs a keypress and
costs nothing else.

`MIN_COLUMN_CELLS` is the harder floor below it, for a column whose content is genuinely
that short.
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

	@property
	def indent(self) -> int:
		""":return: how far a wrapped cell's continuation is indented in this column."""
		return indentFor(self.width)


def indentFor(width: int) -> int:
	"""How far to indent a wrapped cell's continuation in a column of a given width.

	`CELL_INDENT`, and less on a column too narrow to spare it: never more than a third of
	the column, and never less than one cell, because a continuation drawn flush with the
	value above it reads as a second value.

	:param width: the column's width in cells.
	:return: the indent, in cells.
	"""
	return max(1, min(CELL_INDENT, width // 3))


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

	page: int = 0
	"""Which page of columns is being drawn, zero based.

	A table of twenty-nine columns cannot be shown on thirty-two cells and never could. The
	first cut of this squeezed what it could into the band and dropped the rest, which gave a
	reader three cells of every column and eleven columns that did not exist — a table you
	could not read and could not reach the rest of. Nothing is dropped now: the columns are
	dealt into pages, the band shows one, and the reader moves between them. Which is what
	the window does for rows, one axis over.
	"""

	narrowed: tuple[int, ...] = ()
	"""The table's numbers for columns drawn narrower than their content asked for.

	The half a reader meets most often. Three columns of
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

	@property
	def cuts(self) -> bool:
		""":return: whether a cell too long for its column is cut rather than wrapped.

		Asked of the columns rather than kept beside them, because it is the columns that
		carry it: an application module or a saved layout may one day cut one column and wrap
		another, and a plan-wide flag would have to be kept in step with that or quietly
		disagree with it.
		"""
		return any(column.overflow == TRUNCATE for column in self.columns)

	def pages(self) -> tuple[tuple[Placement, ...], ...]:
		"""Every column, dealt into pages of what the band can hold at once.

		Packed greedily across the rows allowed and then greedily across pages: the reader's
		eye and hand both go left to right and then down, so the first row of a record holds
		as much of it as it can and the first page holds as many columns as it can. Balancing
		would move a column to the next row or the next page for the sake of an even shape
		nobody is reading.

		Columns keep their order, so a column is always on the same page and always at the
		same offset on it, which is the whole of what makes a column findable.

		:return: the placements for each page, in order.
		"""
		if not self.columns or self.numCols < 1:
			return ()
		pages: list[tuple[Placement, ...]] = []
		current: list[Placement] = []
		row = 0
		offset = 0
		for column in self.columns:
			nextRow, nextOffset = row, offset
			if offset and offset + self.gap + column.width > self.numCols:
				nextRow, nextOffset = row + 1, 0
			elif offset:
				nextOffset = offset + self.gap
			if nextRow >= self.maxRows or nextOffset + column.width > self.numCols:
				# Out of band. Not out of table: this column starts the next page.
				if current:
					pages.append(tuple(current))
				current = []
				nextRow, nextOffset = 0, 0
			current.append(Placement(column=column, row=nextRow, offset=nextOffset))
			row, offset = nextRow, nextOffset + column.width
		if current:
			pages.append(tuple(current))
		return tuple(pages)

	@property
	def numPages(self) -> int:
		""":return: how many pages of columns this table has."""
		return max(1, len(self.pages()))

	def onPage(self, page: int) -> "ColumnPlan":
		""":return: this plan showing a different page, clamped to the ones there are."""
		return dataclasses.replace(self, page=max(0, min(page, self.numPages - 1)))

	def pageOf(self, column: int) -> Optional[int]:
		""":return: which page a table column is drawn on, or None if it is not drawn."""
		for number, page in enumerate(self.pages()):
			if any(place.column.index == column for place in page):
				return number
		return None

	def placements(self) -> tuple[Placement, ...]:
		""":return: where each column of the page being drawn goes."""
		pages = self.pages()
		if not pages:
			return ()
		return pages[max(0, min(self.page, len(pages) - 1))]

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
		widths = ", ".join(f"{place.column.index}:{place.column.width}" for place in self.placements())
		pages = f", page {self.page + 1} of {self.numPages}" if self.numPages > 1 else ""
		return f"<ColumnPlan {widths} in {self.numCols} over {self.numRows} rows{pages}>"


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
	overflow: str = DEFAULT_OVERFLOW,
) -> ColumnPlan:
	"""Work out how wide each of a table's columns is drawn.

	Where they *go* is `ColumnPlan.pages`, and the two are deliberately separate. This decides
	only how many cells each column deserves; nothing here asks whether they all fit, because
	the answer for a real table is often no and dealing with that by making every column
	narrower is how a display ends up with three cells of each and nothing readable in any.

	**Nothing is shrunk below what can be read.** A column gives up cells to the band while it
	has more than `READABLE_CELLS` to give, widest first — the column with forty cells of
	description can spare ten before the column with six cells of ticker can spare one, and a
	proportional cut takes from both. Below that it stops. A price cut to three cells is not a
	narrow price, it is a digit, and on hardware a table of twenty-nine columns came out as
	one digit of each and eleven columns that were not there at all.

	A column whose content is already shorter than that keeps its own width: the floor is a
	floor on *shrinking*, not a minimum size for a column of one-character flags.

	:param measured: what was found in each column. Hidden ones are ignored.
	:param numCols: the width of the band.
	:param maxRows: how many band rows one table row may use.
	:param gap: cells between columns.
	:param minWidth: the narrowest a column may be drawn at all.
	:param maxWidth: the widest, however long the content.
	:param overflow: what becomes of a cell too long for its column. See `OVERFLOW_STYLES`.
	:return: the plan, or `READING_ORDER` when there is nothing to lay out.
	"""
	wanted = [item for item in measured if not item.hidden]
	maxRows = max(1, min(maxRows, MAX_TABLE_ROWS))
	if not wanted or numCols < minWidth:
		return READING_ORDER
	wants = {item.index: max(item.width, len(item.label)) for item in wanted}
	widths = {item.index: max(minWidth, min(maxWidth, numCols, wants[item.index])) for item in wanted}
	_shrinkTowardsFitting(widths, _budgetFor(numCols, maxRows, len(wanted), gap), minWidth, wants)
	return ColumnPlan(
		columns=tuple(
			Column(
				index=item.index,
				width=widths[item.index],
				label=item.label,
				overflow=overflow if overflow in OVERFLOW_STYLES else DEFAULT_OVERFLOW,
			)
			for item in wanted
		),
		numCols=numCols,
		maxRows=maxRows,
		gap=gap,
		narrowed=tuple(item.index for item in wanted if widths[item.index] < wants[item.index]),
	)


def _budgetFor(numCols: int, maxRows: int, count: int, gap: int) -> int:
	""":return: how many cells of content one page of the band has room for.

	An upper bound rather than an exact answer: the gaps are counted as though every column
	sat on one row, which over-counts by one gap for each row after the first. Over-counting
	makes the shrinking slightly less eager and never more, and being less eager here costs a
	page where being more eager costs legibility.
	"""
	return max(0, numCols * maxRows - gap * max(0, count - 1))


def _shrinkTowardsFitting(widths: dict, budget: int, minWidth: int, wants: dict) -> None:
	"""Take cells from the widest columns while they have any to spare.

	*Towards* fitting, not until it fits. Whether every column fits on one page is not this
	function's business and often cannot be arranged: it stops when nothing has more than
	`READABLE_CELLS` left, and what is still over goes on the next page.

	From the widest first, one cell at a time, which is what keeps a short column whole and a
	long one merely shorter.

	:param widths: column number to width, narrowed in place.
	:param budget: how many cells of content one page has room for.
	:param minWidth: the narrowest a column may be drawn at all.
	:param wants: what each column asked for, so a column already short is left alone.
	"""
	floors = {index: min(wants[index], max(minWidth, READABLE_CELLS)) for index in widths}
	for _ in range(sum(widths.values())):
		if sum(widths.values()) <= budget:
			return
		spare = [index for index in widths if widths[index] > floors[index]]
		if not spare:
			# Everything is as narrow as it can be read at. What is left over is more columns
			# than a page holds, which is what pages are for.
			return
		widest = max(spare, key=lambda index: (widths[index], -index))
		widths[widest] -= 1


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
	return shown != tuple(column.index for column in plan.columns)


def describe(plan: ColumnPlan) -> str:
	""":return: the plan in one line, for the dry run's report.

	Says which page and how many, as well as what is on this one, because those are the two
	questions a reader asks of a table that looks wrong and only one of them can be answered
	by feeling the display.
	"""
	if plan.isEmpty:
		return "reading order; no columns are laid out."
	drawn = ", ".join(
		f"column {place.column.index} at row {place.row} cell {place.offset} in {place.column.width}"
		for place in plan.placements()
	)
	notes = []
	if plan.narrowed:
		short = ", ".join(str(index) for index in plan.narrowed)
		what = "cut" if plan.cuts else "wrapped over more rows"
		notes.append(f"Column {short} is drawn narrower than its content and is {what}.")
	if plan.numPages > 1:
		notes.append(f"Page {plan.page + 1} of {plan.numPages}.")
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
