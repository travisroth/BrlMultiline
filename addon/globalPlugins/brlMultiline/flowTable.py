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

DEFAULT_TARGET_HEIGHT = 2
"""How tall one table row may be, in band rows, before a page gives up a column.

Not the same number as `maxRows`, and the two are easy to confuse. `maxRows` is how many
band rows the *columns* are packed into — a shape decision, and the reader's setting.
This is how tall the drawn row is allowed to become once its cells have wrapped, and it is
what decides how many columns go on a page.

Two, so that four records are under the hand at once on an eight row band. The first cut of
this counted only the horizontal fit, and on a real table it produced four columns at seven
cells that added up to exactly thirty-two — a flawless fit on the axis nobody reads on, with
every cell wrapping to four rows underneath it. Two records on the display, and the reason
to lay a table out in columns is gone.
"""

TARGET_SHARE = 4
"""What fraction of the band one table row may fill, which is where the target comes from.

A quarter. See `targetHeightFor`: on an eight row band that is the two of
`DEFAULT_TARGET_HEIGHT`, and on a taller display it is more, because what matters is how
many records are under the hand rather than how many rows one of them takes.
"""

KEY_SHARE = 3
"""The most of the band a pinned key column may take, as a fraction of it.

A third. The pin is an orientation aid rather than the data — see `ColumnPlan.keyColumn` —
and a pin that took half the band would be paying for orientation with the columns being
oriented.
"""


def targetHeightFor(bandRows: int) -> int:
	"""How tall a table row may be on a band of a given height.

	:param bandRows: how many rows the band has.
	:return: the target, in band rows, never less than `DEFAULT_TARGET_HEIGHT`.
	"""
	return max(DEFAULT_TARGET_HEIGHT, bandRows // TARGET_SHARE)


def rowsNeeded(content: int, width: int) -> int:
	"""How many band rows a cell of a given length takes in a column of a given width.

	The arithmetic the width decision was missing. A column is chosen by how many cells it
	is given, and what the reader feels is how many rows that costs — those are not the same
	question, and a plan that answers only the first fits four columns into thirty-two cells
	perfectly and puts two records on an eight row band.

	The continuation rows are narrower than the first by the column's indent, so this is not
	a plain division. Wrapping is assumed: a truncated column is always one row and callers
	that know they are cutting do not ask.

	:param content: how many cells the value translates to.
	:param width: how many cells the column is drawn in.
	:return: the number of band rows, at least one.
	"""
	if width < 1:
		return 1
	if content <= width:
		return 1
	usable = max(1, width - indentFor(width))
	return 1 + -(-(content - width) // usable)


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

	pinned: bool = False
	"""Whether this is the repeated copy of the key column rather than the column itself.

	The same table column, drawn again at the left of a later page so that the reader knows
	whose row they are reading. It is a copy in the plan and not in the table: it carries the
	table's own column number, so routing a finger press over it goes to the real cell.
	"""

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

	assignment: tuple[tuple[int, ...], ...] = ()
	"""Which columns are on which page, by the table's own column numbers.

	Empty in a plan built by hand, and then the pages are worked out by packing greedily —
	see `_packedPages`, which is what this did before and is still what a plan with no
	assignment means.

	It is a field rather than a calculation because it is about to be a reader's choice. The
	custom layout is "show these columns together and those apart", and that is exactly this
	tuple with different contents; deriving it every time would mean the reader's grouping
	had nowhere to live. Making it a field now is small. Making it one later, under a
	shipped feature, is not.
	"""

	keyColumn: Optional[int] = None
	"""The column repeated at the left of every page after the first, if any.

	Six columns into a watchlist the reader is feeling four numbers with nothing to say whose
	numbers they are, and the symbol that would say so is two pages back. So the first column
	is drawn again at the left of each later page: the same table column, at the same offset
	every time, cut to `keyWidth` rather than wrapped.

	Cut, and only on the later pages, because it is doing a different job there. On its own
	page it is a column and the reader is reading it, so it is drawn whole like any other.
	On a later page it is a label — the answer to "whose row is this" — and the first few
	cells of an identifier answer that. Nothing is lost by cutting the copy: the column
	itself is one page turn away, drawn in full.
	"""

	keyWidth: int = 0
	"""How wide the repeated copy of `keyColumn` is drawn."""

	omitted: tuple[int, ...] = ()
	"""The table's numbers for columns that were measured and are not drawn.

	Reported rather than merely skipped, because a missing column is the one kind of wrongness
	a reader cannot see: the band looks like a table with fewer columns in it, and there is
	nothing to say whether that is the table or the layout. See `Measurement.hidden` for what
	puts a column here.
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
		if self.assignment:
			return tuple(self._placePage(number, page) for number, page in enumerate(self.assignment))
		return self._packedPages()

	def _placePage(self, number: int, indexes: tuple[int, ...]) -> tuple[Placement, ...]:
		"""Lay out one page of an assignment, pinning the key column where it belongs.

		:param number: which page this is, zero based.
		:param indexes: the table's numbers for the columns on it, in drawing order.
		:return: where each of them goes.
		"""
		byIndex = {column.index: column for column in self.columns}
		placed: list[Placement] = []
		row = 0
		offset = 0
		if number and self.keyColumn is not None and self.keyColumn not in indexes:
			key = byIndex.get(self.keyColumn)
			if key is not None and self.keyWidth:
				pin = dataclasses.replace(key, width=self.keyWidth, overflow=TRUNCATE, pinned=True)
				placed.append(Placement(column=pin, row=0, offset=0))
				offset = self.keyWidth + self.gap
		for index in indexes:
			column = byIndex.get(index)
			if column is None:
				continue
			if offset and offset + column.width > self.numCols:
				row, offset = row + 1, 0
			if row >= self.maxRows or offset + column.width > self.numCols:
				break
			placed.append(Placement(column=column, row=row, offset=offset))
			offset += column.width + self.gap
		return tuple(placed)

	def _packedPages(self) -> tuple[tuple[Placement, ...], ...]:
		""":return: the pages of a plan that carries no assignment, packed greedily."""
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

	def cutting(self) -> "ColumnPlan":
		""":return: this layout with every column cut rather than wrapped.

		What a pinned header row is drawn with. A header is orientation and not the data —
		the same argument the repeated key column is made of — and one that wrapped would
		take two of the band's rows away from the table to say "%Change" instead of "%Chang".
		One row, always, so that the rows below it never move.
		"""
		return dataclasses.replace(
			self,
			columns=tuple(dataclasses.replace(column, overflow=TRUNCATE) for column in self.columns),
		)

	def knows(self, column: int) -> bool:
		""":return: whether this plan was made from a table that had this column.

		Two ways of knowing it, and the second is the one that was missed. A column is known
		if it is drawn on some page — `pageOf` answers that — and it is *also* known if it was
		measured and deliberately left out, because a column holding nothing the reader can
		read is a decision this plan made rather than a column it has never heard of.

		The distinction is what tells a table that changed shape from a caret sitting
		somewhere ordinary. Quick navigation to a table lands the caret in the first cell, and
		on the reader's own watchlist the first cell is an unreadable icon: the band read that
		as "a column the plan has no page for", rebuilt the whole layout, and did it again on
		every redraw. Panning did nothing at all until the caret was moved off that column,
		because every pan was undone by the rebuild that followed it.

		:param column: the table's own column number.
		"""
		return self.pageOf(column) is not None or column in self.omitted

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

	typicalWidth: int = 0
	"""What a cell of it usually holds, in cells. Zero where the caller did not measure it.

	The widest is the wrong number to plan a width from and it took a real document to see
	why. A VPAT has a remarks column where nine rows in ten hold a few words and the tenth
	holds a paragraph; planned from the widest, every row of the table is laid out for the
	paragraph, and the reader pays for it on the nine rows that do not have one.

	So the width is chosen for the typical cell and the occasional long one wraps taller than
	the target. That is honest — since a tall row became a block with more rows, nothing is
	lost by it and it costs a keypress on the rows that earn it.

	Zero means the caller measured only the widest, and `typical` then answers with that,
	which is the old behaviour and is right for a caller that knows its table.
	"""

	label: str = ""
	"""Its header, where it has one."""

	declared: bool = False
	"""Whether the table *said* that is its header, rather than it being read off row one.

	The two are not interchangeable and one caller needs to know which it has: a header row
	is pinned above the band from what a table declares, and a label borrowed from row one is
	already on the band as row one. See `flowTableSource.measure`, which is where the
	difference is known, and `TableFlowSource`, which is given the declared ones so that it
	does not have to go and ask again.
	"""

	labelWidth: int = 0
	"""How wide the header is in cells, where it was measured separately.

	Zero where the caller's `width` already covers it, which is the ordinary case:
	`flowTableSource.measure` reads the header row along with the rest and the widest content
	it found includes the header. This exists for a caller that measured the body and knows
	the header separately — an application module, a saved layout — and it exists in *cells*
	because the obvious alternative is not.

	The obvious alternative was `len(label)`, which is what this used, and it quietly put
	character counts back into an arithmetic that is careful everywhere else to work in cells.
	A header contracted to four cells was given six because it has six characters.
	"""

	hidden: bool = False
	"""Whether the table itself is not showing this column.

	A zero width column in a list view, a `display: none` cell on a page. Excluded from the
	plan rather than given a width, because a column the sighted reader cannot see is not a
	column the braille reader is missing.
	"""

	@property
	def typical(self) -> int:
		""":return: what a cell of this column usually holds, falling back to the widest."""
		return self.typicalWidth or self.width

	@property
	def wants(self) -> int:
		""":return: the width that would draw every cell of it on one row."""
		return max(self.width, self.labelWidth)


def planFor(
	measured: Iterable[Measurement],
	numCols: int,
	maxRows: int = DEFAULT_MAX_ROWS,
	gap: int = COLUMN_GAP,
	minWidth: int = MIN_COLUMN_CELLS,
	maxWidth: int = MAX_COLUMN_CELLS,
	overflow: str = DEFAULT_OVERFLOW,
	targetHeight: int = DEFAULT_TARGET_HEIGHT,
	pinKey: bool = True,
) -> ColumnPlan:
	"""Work out how wide each of a table's columns is drawn, and which of them share a page.

	**A page holds as many columns as it can without the row growing taller than
	`targetHeight`.** That is the whole rule and it is worth stating plainly, because the
	rule it replaces was "as many columns as fit across the band", which is a different
	question with a much worse answer. Four columns at seven cells each add up to exactly
	thirty-two and every one of them wraps to four rows underneath; the fit is perfect on the
	axis nobody is reading on, and two records reach an eight row display.

	So the page size is searched downwards. Try every remaining column, work out the widths,
	predict the height; if it is over the target, try one column fewer, which gives the ones
	that remain more cells each and drops the height faster than linearly. One column is the
	floor, and a column that cannot meet the target even with the whole band to itself gets
	the whole band to itself. That is the right answer for a VPAT remarks column and it is
	not a special case — it falls out of the search reaching one.

	**Spare cells are given away rather than left.** A column gives up cells while it has
	more than `READABLE_CELLS` to spare, widest first; and when the page has cells nobody
	claimed, they go one at a time to whichever column is predicted tallest. On a page of a
	criterion, a level and a remark, that hands the remark everything the other two do not
	need, which is the single change that does most of the work here.

	**Nothing is shrunk below what can be read.** Below `READABLE_CELLS` a column stops being
	a value and becomes a fragment of one; on hardware a table of twenty-nine columns came
	out as one digit of each. What will not fit at a readable width goes on the next page.

	:param measured: what was found in each column. Hidden ones are ignored.
	:param numCols: the width of the band.
	:param maxRows: how many band rows one table row may use. See `DEFAULT_TARGET_HEIGHT`
		for how this differs from `targetHeight`, which is easy to confuse it with.
	:param gap: cells between columns.
	:param minWidth: the narrowest a column may be drawn at all.
	:param maxWidth: the widest, however long the content.
	:param overflow: what becomes of a cell too long for its column. See `OVERFLOW_STYLES`.
	:param targetHeight: how tall a table row may be before a page gives up a column.
	:param pinKey: whether to repeat the first column at the left of every later page.
	:return: the plan, or `READING_ORDER` when there is nothing to lay out.
	"""
	measured = list(measured)
	# A column asking for no cells is refused here as well as in the measuring, and the two
	# agree on purpose. `Measurement.hidden` is what a measurer says about a table it can see
	# — a merged cell, a column of unreadable icons — and this is the arithmetic refusing to
	# give `MIN_COLUMN_CELLS` to a column with nothing to put in them, whoever measured it.
	wanted = [item for item in measured if not item.hidden and item.wants > 0]
	maxRows = max(1, min(maxRows, MAX_TABLE_ROWS))
	if not wanted or numCols < minWidth:
		return READING_ORDER
	targetHeight = max(1, targetHeight)
	maxWidth = max(minWidth, min(maxWidth, numCols))
	drawn = {item.index for item in wanted}
	overflow = overflow if overflow in OVERFLOW_STYLES else DEFAULT_OVERFLOW
	shape = dict(
		numCols=numCols,
		maxRows=maxRows,
		gap=gap,
		minWidth=minWidth,
		maxWidth=maxWidth,
		targetHeight=targetHeight,
		overflow=overflow,
	)
	widths, assignment = _dealIntoPages(wanted, reserve=0, **shape)
	keyColumn, keyWidth = _keyFor(wanted, numCols, gap, minWidth, pinKey and len(assignment) > 1)
	if keyColumn is not None:
		# Deal again with the pin's cells taken out of every page after the first. Only ever
		# twice: the pin costs room, so a table that needed more than one page without it
		# still needs more than one page with it, and the answer cannot flip back.
		widths, assignment = _dealIntoPages(wanted, reserve=keyWidth + gap, **shape)
	return ColumnPlan(
		columns=tuple(
			Column(
				index=item.index,
				width=widths[item.index],
				label=item.label,
				overflow=overflow,
			)
			for item in wanted
		),
		numCols=numCols,
		maxRows=maxRows,
		gap=gap,
		assignment=assignment,
		keyColumn=keyColumn,
		keyWidth=keyWidth,
		omitted=tuple(sorted(drawn.symmetric_difference(item.index for item in measured))),
		narrowed=tuple(item.index for item in wanted if widths[item.index] < item.wants),
	)


def _keyFor(
	wanted: Sequence[Measurement], numCols: int, gap: int, minWidth: int, pinKey: bool
) -> tuple[Optional[int], int]:
	"""Which column is repeated at the left of every later page, and how wide.

	The first column that is drawn, which is the row's own label in every table that has one:
	the symbol in a watchlist, the criterion in a VPAT, the date in a statement. Asked of the
	plan's own order rather than of the table, so a hidden first column does not pin a column
	nobody can see.

	Its width is what a typical cell of it holds, capped at `KEY_SHARE` of the band. The cap
	is what keeps this an orientation aid: a pin wide enough to hold the longest criterion in
	full would be spending on the label what the columns being labelled need.

	:param wanted: the columns being drawn, in order.
	:param numCols: the width of the band.
	:param gap: cells between columns.
	:param minWidth: the narrowest a column may be drawn.
	:param pinKey: whether the reader wants this at all.
	:return: the column's number and its pinned width, or None and zero.
	"""
	if not pinKey or len(wanted) < 2:
		return None, 0
	key = wanted[0]
	width = max(minWidth, min(numCols // KEY_SHARE, max(key.typical, key.labelWidth)))
	if numCols - (width + gap) < max(minWidth, READABLE_CELLS):
		# The pin would leave no room to read anything beside it, which is not orientation.
		return None, 0
	return key.index, width


def _dealIntoPages(
	wanted: Sequence[Measurement],
	reserve: int,
	numCols: int,
	maxRows: int,
	gap: int,
	minWidth: int,
	maxWidth: int,
	targetHeight: int,
	overflow: str,
) -> tuple[dict, tuple[tuple[int, ...], ...]]:
	"""Walk the columns left to right, filling one page at a time.

	Left to right and greedily, because a column that moves is a column the reader has to
	find again: the order is the table's, and which page a column lands on must not depend on
	anything but the columns before it.

	:param wanted: the columns being drawn, in order.
	:param reserve: cells taken out of every page after the first, for a pinned key column.
	:param numCols: the width of the band.
	:param maxRows: how many band rows one table row may use.
	:param gap: cells between columns.
	:param minWidth: the narrowest a column may be drawn.
	:param maxWidth: the widest.
	:param targetHeight: how tall a table row may be before a page gives up a column.
	:param overflow: what becomes of a cell too long for its column.
	:return: every column's width, and which columns are on which page.
	"""
	widths: dict = {}
	assignment: list[tuple[int, ...]] = []
	rest = list(wanted)
	while rest:
		room = numCols if not assignment else max(minWidth, numCols - reserve)
		take, chosen = _howManyFit(
			rest, room, numCols, maxRows, gap, minWidth, maxWidth, targetHeight, overflow
		)
		widths.update(chosen)
		assignment.append(tuple(item.index for item in rest[:take]))
		rest = rest[take:]
	return widths, tuple(assignment)


def _howManyFit(
	rest: Sequence[Measurement],
	room: int,
	numCols: int,
	maxRows: int,
	gap: int,
	minWidth: int,
	maxWidth: int,
	targetHeight: int,
	overflow: str,
) -> tuple[int, dict]:
	"""How many of the columns still to place go on this page.

	The search: most first, one fewer each time, stopping at the first count whose predicted
	height is within the target. Downwards rather than upwards because the answer wanted is
	the largest that works, and a search that grows would have to keep going after it found
	one to know it was the largest.

	One is the floor and it is returned without asking about height. A column that cannot
	meet the target with the whole band to itself exists — a remarks column in a VPAT is
	exactly that — and the answer for it is a page of its own, which is what the reader asked
	for when they said to go one column at a time.

	:param rest: the columns still to place, in order.
	:param room: cells available on the first band row of this page.
	:param numCols: the width of the band, which the later rows have all of.
	:param maxRows: how many band rows one table row may use.
	:param gap: cells between columns.
	:param minWidth: the narrowest a column may be drawn.
	:param maxWidth: the widest.
	:param targetHeight: how tall a table row may be before a page gives up a column.
	:param overflow: what becomes of a cell too long for its column.
	:return: how many columns to take, and the widths for them.
	"""
	widths: dict = {}
	for take in range(len(rest), 0, -1):
		items = rest[:take]
		widths = _fitWidths(items, _budgetFor(room, numCols, maxRows, take, gap), minWidth, maxWidth)
		height = _stackedHeight(items, widths, room, numCols, maxRows, gap, overflow)
		if height is None:
			continue
		if take == 1:
			return 1, widths
		if height <= targetHeight:
			return take, widths
	# Nothing was placeable, not even one column: it is wider than the band. Draw it at the
	# band's width and let it wrap, which is what a column wider than the display has to do.
	item = rest[0]
	return 1, {item.index: max(minWidth, min(maxWidth, room))}


def _budgetFor(room: int, numCols: int, maxRows: int, count: int, gap: int) -> int:
	""":return: how many cells of content one page of the band has room for.

	An upper bound rather than an exact answer: the gaps are counted as though every column
	sat on one row, which over-counts by one gap for each row after the first. Over-counting
	makes the shrinking slightly less eager and never more, and being less eager here costs a
	page where being more eager costs legibility.

	:param room: cells on the first band row, which a pinned key column has taken from.
	:param numCols: cells on the rest, which it has not.
	:param maxRows: how many band rows one table row may use.
	:param count: how many columns are being fitted.
	:param gap: cells between columns.
	"""
	return max(0, room + numCols * max(0, maxRows - 1) - gap * max(0, count - 1))


def _stackedHeight(
	items: Sequence[Measurement],
	widths: dict,
	room: int,
	numCols: int,
	maxRows: int,
	gap: int,
	overflow: str,
) -> Optional[int]:
	"""How tall a row of these columns actually comes out, or None if they do not fit.

	**Lane by lane, and summed.** A plan may pack its columns across more than one row of the
	band, and each of those lanes is as tall as the tallest cell in it — `flowRender._laidOut`
	stacks them by their own heights, which is what stops a wrapped cell being written through
	by the lane below. Taking the tallest column overall was the first cut of this and it
	under-counts by exactly the lanes it ignores: two lanes of two rows each is four rows on
	the band, reported as two.

	The packing is walked rather than derived from a sum, for the same reason
	`ColumnPlan._placePage` walks it: a total that fits can still fail to pack, since three
	columns of eleven cells on a thirty-two cell row is thirty-three with the gaps.

	:param items: the columns, in drawing order.
	:param widths: column number to width.
	:param room: cells on the first band row, which a pinned key column has taken from.
	:param numCols: cells on the rest.
	:param maxRows: how many lanes the columns may be packed into.
	:param gap: cells between columns.
	:param overflow: what becomes of a cell too long for its column. A cut one is always one
		row, however much it holds.
	:return: the height in band rows, or None if these columns do not fit.
	"""
	lanes: list[int] = []
	row, offset, limit = 0, 0, room
	for item in items:
		width = widths[item.index]
		if offset and offset + width > limit:
			row, offset, limit = row + 1, 0, numCols
		if row >= maxRows or offset + width > limit:
			return None
		while len(lanes) <= row:
			lanes.append(1)
		tall = 1 if overflow == TRUNCATE else rowsNeeded(item.typical, width)
		lanes[row] = max(lanes[row], tall)
		offset += width + gap
	return sum(lanes) if lanes else 1


def _fitWidths(items: Sequence[Measurement], budget: int, minWidth: int, maxWidth: int) -> dict:
	"""Decide a width for each column of one page.

	Both directions. What was here shrank the widest columns towards fitting and stopped; it
	never gave anything back, so a page whose columns all happened to be short left cells
	unspent while a column beside them wrapped.

	:param items: the columns on this page.
	:param budget: how many cells of content the page holds.
	:param minWidth: the narrowest a column may be drawn.
	:param maxWidth: the widest.
	:return: column number to width.
	"""
	wants = {item.index: item.wants for item in items}
	widths = {item.index: max(minWidth, min(maxWidth, wants[item.index])) for item in items}
	_shrinkTowardsFitting(widths, budget, minWidth, wants)
	return widths


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


def predictedHeight(plan: ColumnPlan, measured: Iterable[Measurement]) -> int:
	"""How tall the page being drawn is expected to make a typical row.

	For the report, and for a test that wants the number the search was aiming at rather than
	a rendering to count the rows of. A truncated column is one row whatever it holds.

	Lane by lane and summed, because that is how the rows actually stack — see
	`_stackedHeight`, which is the same arithmetic on the way in.

	:param plan: the layout, on the page being asked about.
	:param measured: what was found in the table.
	:return: the height in band rows, at least one.
	"""
	typical = {item.index: item.typical for item in measured}
	lanes: dict[int, int] = {}
	for place in plan.placements():
		tall = (
			1
			if place.column.overflow == TRUNCATE
			else rowsNeeded(typical.get(place.column.index, 0), place.column.width)
		)
		lanes[place.row] = max(lanes.get(place.row, 1), tall)
	return sum(lanes.values()) if lanes else 1


def describe(plan: ColumnPlan) -> str:
	""":return: the plan in one line, for the dry run's report.

	Says which page and how many, as well as what is on this one, because those are the two
	questions a reader asks of a table that looks wrong and only one of them can be answered
	by feeling the display.
	"""
	if plan.isEmpty:
		return "reading order; no columns are laid out."
	drawn = ", ".join(
		f"column {place.column.index}{' pinned' if place.column.pinned else ''} at row "
		f"{place.row} cell {place.offset} in {place.column.width}"
		for place in plan.placements()
	)
	notes = []
	if plan.omitted:
		short = ", ".join(str(index) for index in plan.omitted)
		notes.append(f"Column {short} holds nothing the reader can read and is not drawn.")
	if plan.keyColumn is not None:
		notes.append(
			f"Column {plan.keyColumn} is repeated at the left of every page after the first, "
			f"cut to {plan.keyWidth} cells."
		)
	# The columns on this page, not every column of the table: a note about column
	# seventeen, on a page the reader is not looking at, is not a note about what is under
	# their hands.
	here = [
		place.column.index
		for place in plan.placements()
		if place.column.index in plan.narrowed and not place.column.pinned
	]
	if here:
		short = ", ".join(str(index) for index in here)
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
