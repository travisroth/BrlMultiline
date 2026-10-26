# BrlMultiline: deciding where a table's columns go.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Where a table's columns sit on the band, and how wide each of them is.

So a column starts at the same cell offset on every row and can be read down. Arithmetic
only, importing nothing from NVDA, like `flowIndent`, so it can be tested on its own.

1. Widths are in cells, not characters; the caller measures by translating.
2. The plan is made once on entering the table and kept, so columns do not move; see
   `shouldReplan`.
3. A table row may take more than one band row; see `maxRows`, a setting.
4. Columns are dealt into bandfuls at readable widths, which decides the widths. Nothing
   is dropped.
5. The band shows a run of columns that scrolls one column at a time, as rows do; the page
   turn moves by however many are showing. See `ColumnPlan.at`.
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

Wrap is the default, since truncation silently drops data. Truncation is a setting for a
reader who knows a table well enough to recognise values from their start.
"""

DEFAULT_OVERFLOW = WRAP

COLUMN_GAP = 1
"""Cells between one column and the next.

One blank cell; none would run a cut cell into the next column.
"""

CELL_INDENT = 2
"""How far a wrapped cell's continuation is indented within its own column.

Less than `flowIndent.CONTINUATION_EXTRA`, since a column has fewer cells to spare, but enough
to show the row below continues the same value. Narrowed further on narrow columns; see
`indentFor`.
"""

READABLE_CELLS = 6
"""The narrowest a column is shrunk to in order to make room for another.

Below six a value becomes fragments. A floor on shrinking, not a size: a short column keeps
its own width, and what will not fit goes to the next page. `MIN_COLUMN_CELLS` is the hard
floor.
"""

MIN_COLUMN_CELLS = 3
"""The narrowest a column may be drawn.

Below this a column is unreadable, so it is left out of the plan instead.
"""

MAX_COLUMN_CELLS = 40
"""The widest a column may be drawn, however long its content is.

Generous, wider than a Monarch's row, so it only stops a runaway column such as a description.
"""

MAX_TABLE_ROWS = 4
"""How many band rows one table row may use.

Half a Monarch, so at least two table rows show; taller is better read in reading order.
"""

DEFAULT_MAX_ROWS = 1
"""How many band rows one table row uses unless the reader says otherwise.

One, since a table is for comparing rows, and more records under the hand is better.
"""

DEFAULT_TARGET_HEIGHT = 2
"""How tall one table row may be, in band rows, before a page gives up a column.

Not `maxRows`, which is how many band rows the columns are packed into. This is how tall a
row may become once its cells wrap, and it decides how many columns go on a page, so that
four records show on an eight row band.
"""

TARGET_SHARE = 4
"""What fraction of the band one table row may fill, which is where the target comes from.

A quarter: `DEFAULT_TARGET_HEIGHT` on an eight row band, more on a taller one. See
`targetHeightFor`.
"""

KEY_SHARE = 3
"""The most of the band a pinned key column may take, as a fraction of it.

A third, since the pin is an orientation aid; see `ColumnPlan.keyColumn`.
"""


def targetHeightFor(bandRows: int) -> int:
	"""How tall a table row may be on a band of a given height.

	:param bandRows: how many rows the band has.
	:return: the target, in band rows, never less than `DEFAULT_TARGET_HEIGHT`.
	"""
	return max(DEFAULT_TARGET_HEIGHT, bandRows // TARGET_SHARE)


def rowsNeeded(content: int, width: int) -> int:
	"""How many band rows a cell of a given length takes in a column of a given width.

	What a width costs in rows. Continuation rows are narrower by the column's indent, so
	this is not a plain division. Assumes wrapping; a cut column is always one row.

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


KEEP_START = "start"
KEEP_END = "end"

KEEP_ENDS: tuple[str, ...] = (KEEP_START, KEEP_END)
"""Which half of a cut cell is kept.

The start or the end, since some columns put what identifies the row at the end. A cell is
cut by drawing it in full and keeping its first or last row. See
`flowRender.FlowRenderer._cellRows`.
"""


@dataclasses.dataclass(frozen=True)
class ColumnChoice:
	"""What a reader decided about one column, or nothing where they have said nothing.

	Empty fields defer to the measurement and settings, as in `flowTableLayouts.TableLayout`,
	so a measurement is never stored as a decision.
	"""

	label: str = ""
	"""What the reader calls this column, instead of what the table calls it.

	The strongest answer to a heading nobody can read: a column whose header is six lines of
	help text is called "Status" and the problem is gone, because a header is orientation drawn
	once at the top rather than data."""

	overflow: str = ""
	"""`TRUNCATE`, `WRAP`, or "" to follow the table's own setting."""

	keep: str = ""
	"""`KEEP_START`, `KEEP_END`, or "" for the start. Only meaningful when the column is cut."""

	headerKeep: str = ""
	"""Which end of this column's *heading* is kept when it is too wide to draw.

	Separate from the data's, because they go wrong separately: a column of short values under
	an unreadable heading is the reader's own case, and a custom label is not always wanted
	where the table's heading merely ends better than it begins."""

	minWidth: int = 0
	"""The fewest cells this column may be drawn in, or 0 to let the fitting decide."""

	maxWidth: int = 0
	"""The most, or 0 for no ceiling of the reader's own."""

	startsAPage: bool = False
	"""Whether a page of columns begins here.

	How a reader says "these together, those after them" without describing every page: the
	packing still decides where the other breaks fall, and this one is theirs."""

	plainCase: bool = False
	"""Whether this column is drawn without capital signs, values and heading alike.

	Saves the capital indicator cells in six dot tables. See `flowTableSource.withoutCapitals`.
	No change in eight dot computer braille, where a capital costs nothing.
	"""

	@property
	def isEmpty(self) -> bool:
		""":return: whether this decides nothing at all."""
		return not (
			self.label
			or self.overflow
			or self.keep
			or self.headerKeep
			or self.minWidth
			or self.maxWidth
			or self.startsAPage
			or self.plainCase
		)


NOTHING_CHOSEN = ColumnChoice()
"""What a column the reader has said nothing about answers to every question."""


@dataclasses.dataclass(frozen=True)
class Column:
	"""One column, as it will be drawn."""

	index: int
	"""The table's own column number, one based, as NVDA reports it.

	Not the plan position, since fetching and routing need the table's own number.
	"""

	width: int
	"""How many cells it is drawn in."""

	label: str = ""
	"""The header, where the table has one.

	Used for the pinned header row, for matching saved layouts, and for the width of a column
	whose content is all short.
	"""

	overflow: str = DEFAULT_OVERFLOW
	"""What becomes of a cell too long for `width`. See `OVERFLOW_STYLES`."""

	keep: str = KEEP_START
	"""Which end of a cut cell is kept. See `KEEP_ENDS`."""

	headerKeep: str = KEEP_START
	"""Which end of this column's heading is kept when the header row is drawn."""

	pinned: bool = False
	"""Whether this is the repeated copy of the key column rather than the column itself.

	Drawn again at the left of a later page. Carries the table's own number, so routing goes
	to the real cell.
	"""

	startsAPage: bool = False
	"""Whether the reader said a page of columns begins here. See `ColumnChoice.startsAPage`.

	On the column, since the band shows a scrolling run rather than fixed pages.
	"""

	@property
	def indent(self) -> int:
		""":return: how far a wrapped cell's continuation is indented in this column."""
		return indentFor(self.width)


def indentFor(width: int) -> int:
	"""How far to indent a wrapped cell's continuation in a column of a given width.

	`CELL_INDENT`, at most a third of the column and at least one cell.

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

	Frozen, since it is part of a rendering's key, as `IndentPlan` is.
	"""

	columns: tuple[Column, ...] = ()
	"""The columns to draw, in drawing order. A hidden column is simply not here."""

	numCols: int = 0
	"""The band width the plan was made for."""

	maxRows: int = DEFAULT_MAX_ROWS
	"""How many band rows one table row may use."""

	gap: int = COLUMN_GAP
	"""Cells between one column and the next."""

	at: int = 0
	"""Which column the band starts at, as an index into `columns`.

	An offset into the run, not a page number, so the band scrolls a column at a time and a
	page turn moves by however many are showing. The pages still decide the widths (see
	`assignment`), and a break the reader named is kept (see `Column.startsAPage`).
	"""

	assignment: tuple[tuple[int, ...], ...] = ()
	"""Which columns are on which page, by the table's own column numbers.

	Empty means pages are packed greedily; see `_packedPages`. A field rather than a
	calculation, so a reader's grouping has somewhere to live.
	"""

	keyColumn: Optional[int] = None
	"""The column repeated at the left of every page after the first, if any.

	Drawn again at the left of each later page, cut to `keyWidth`, so the reader knows whose
	row they are on. On its own page it is drawn whole like any other column.
	"""

	keyWidth: int = 0
	"""How wide the repeated copy of `keyColumn` is drawn."""

	omitted: tuple[int, ...] = ()
	"""The table's numbers for columns that were measured and are not drawn.

	Reported, since a missing column cannot be felt. See `Measurement.hidden`.
	"""

	excluded: tuple[int, ...] = ()
	"""The table's numbers for columns the reader's own saved layout leaves out.

	Unlike `omitted`, which the measurement found empty and is worth one more look, an
	excluded column is never rebuilt for. Both are known to `knows`, so the caret in one is
	not taken as a changed table.
	"""

	narrowed: tuple[int, ...] = ()
	"""The table's numbers for columns drawn narrower than their content asked for.

	Reported, since cut words look like short entries, so the reader can decide what to do.
	"""

	@property
	def isEmpty(self) -> bool:
		""":return: whether this plan draws nothing, which is what reading order gets."""
		return not self.columns

	@property
	def cuts(self) -> bool:
		""":return: whether a cell too long for its column is cut rather than wrapped.

		Asked of the columns, which carry it, rather than a plan-wide flag.
		"""
		return any(column.overflow == TRUNCATE for column in self.columns)

	def _placeFrom(self, start: int) -> tuple[Placement, ...]:
		"""Lay out as many columns as the band holds, beginning at one of them.

		Packed greedily across the rows allowed, so a record's first row holds as much as it
		can. Columns keep their order and widths; only where they sit depends on the start.

		:param start: which column to begin at, as an index into `columns`.
		:return: where each drawn column goes.
		"""
		if not self.columns or self.numCols < 1:
			return ()
		start = max(0, min(start, len(self.columns) - 1))
		placed: list[Placement] = []
		row = 0
		offset = 0
		pin = self._pinFor(start)
		if pin is not None:
			placed.append(Placement(column=pin, row=0, offset=0))
			offset = self.keyWidth + self.gap
		for position, column in enumerate(self.columns[start:]):
			if position and column.startsAPage:
				# A break the reader asked for ends the run before it, however much room is
				# left. Theirs is the only break named; the rest fall where they fit.
				break
			if offset and offset + column.width > self.numCols:
				row, offset = row + 1, 0
			if row >= self.maxRows or offset + column.width > self.numCols:
				break
			placed.append(Placement(column=column, row=row, offset=offset))
			offset += column.width + self.gap
		return tuple(placed)

	def _pinFor(self, start: int) -> Optional["Column"]:
		""":return: the key column's repeated copy for a band starting here, or None.

		Only where the key column itself is off to the left.

		:param start: which column the band begins at, as an index into `columns`.
		"""
		if self.keyColumn is None or not self.keyWidth:
			return None
		position = next(
			(index for index, column in enumerate(self.columns) if column.index == self.keyColumn),
			None,
		)
		if position is None or position >= start:
			return None
		return dataclasses.replace(
			self.columns[position],
			width=self.keyWidth,
			overflow=TRUNCATE,
			pinned=True,
		)

	@property
	def lastStart(self) -> int:
		""":return: the furthest along the run the band can start and still show something new.

		The first start from which the last column is drawn, as the window does for rows.
		"""
		if not self.columns:
			return 0
		last = self.columns[-1].index
		for start in range(len(self.columns)):
			if any(place.column.index == last for place in self._placeFrom(start)):
				return start
		return len(self.columns) - 1

	@property
	def shown(self) -> int:
		""":return: how many of the table's own columns the band is showing.

		Not counting the repeated key column. A page turn moves by this.
		"""
		return sum(1 for place in self.placements() if not place.column.pinned)

	def scrolledTo(self, start: int) -> "ColumnPlan":
		""":return: this plan with the band starting at another column, clamped to the run.

		Every move comes through this clamp, so turning repeatedly stops at the end.
		"""
		return dataclasses.replace(self, at=max(0, min(start, self.lastStart)))

	def scrolledBy(self, columns: int) -> "ColumnPlan":
		""":return: this plan moved along the run by a number of columns."""
		return self.scrolledTo(self.at + columns)

	def turnedBy(self, pages: int) -> "ColumnPlan":
		""":return: this plan moved by whole bandfuls of columns.

		A bandful is however many are showing now, from where the band is.

		:param pages: how many bandfuls, negative for back towards the first column.
		"""
		return self.scrolledBy(pages * max(1, self.shown))

	def startShowing(self, column: int) -> int:
		""":return: where the band would have to start to draw a column, moving as little as
		possible.

		The column counterpart of `FlowWindow.ensureVisible`: no move if shown, else the least
		scroll that reaches it.

		:param column: the table's own column number.
		"""
		if self.showing(column):
			return self.at
		position = next(
			(index for index, item in enumerate(self.columns) if item.index == column),
			None,
		)
		if position is None:
			return self.at
		if position < self.at:
			return position
		for start in range(self.at + 1, position + 1):
			if any(place.column.index == column for place in self._placeFrom(start)):
				return start
		return position

	def showing(self, column: int) -> bool:
		""":return: whether a table column is on the band as it is drawn now.

		Unlike `holds`, counts the repeated key column. For `FlowBand._showColumn`.

		:param column: the table's own column number.
		"""
		return any(place.column.index == column for place in self.placements())

	def holds(self, column: int) -> bool:
		""":return: whether this plan draws a table column anywhere along the run."""
		return any(item.index == column for item in self.columns)

	@property
	def numColumns(self) -> int:
		""":return: how many columns this plan draws in all, over the whole run."""
		return len(self.columns)

	# The run, counted in bandfuls. Scrolling is the primitive and these are a view of it:
	# where the band would sit if it had only ever moved a whole bandful at a time, which is
	# what the page turn command does and what "how far along is this table" means to a
	# reader. Nothing here decides a layout; `_placeFrom` does that.

	def pageStarts(self) -> tuple[int, ...]:
		""":return: where each bandful of columns begins, counted from the first column."""
		if not self.columns:
			return ()
		starts = [0]
		while True:
			placed = self._placeFrom(starts[-1])
			taken = sum(1 for place in placed if not place.column.pinned)
			following = starts[-1] + max(1, taken)
			if following > len(self.columns) - 1:
				return tuple(starts)
			starts.append(following)

	def pages(self) -> tuple[tuple[Placement, ...], ...]:
		""":return: the placements of each bandful, in order. See `pageStarts`."""
		return tuple(self._placeFrom(start) for start in self.pageStarts())

	@property
	def numPages(self) -> int:
		""":return: how many bandfuls of columns this table takes."""
		return max(1, len(self.pageStarts()))

	@property
	def page(self) -> int:
		""":return: which bandful the band is in, zero based.

		The last bandful starting at or before the band, since after a scroll it sits between
		them. Not reported to the reader; see `whereItIs`.
		"""
		starts = self.pageStarts()
		return max((number for number, start in enumerate(starts) if start <= self.at), default=0)

	def onPage(self, page: int) -> "ColumnPlan":
		""":return: this plan with the band at the start of a bandful, clamped to the ones there are."""
		starts = self.pageStarts()
		if not starts:
			return self
		return self.scrolledTo(starts[max(0, min(page, len(starts) - 1))])

	def pageOf(self, column: int) -> Optional[int]:
		""":return: which bandful a table column falls in, or None if the plan does not draw it."""
		for number, page in enumerate(self.pages()):
			if any(place.column.index == column and not place.column.pinned for place in page):
				return number
		return None

	@property
	def showsEverything(self) -> bool:
		""":return: whether the whole table is on the band, so there is nowhere to scroll."""
		return self.shown >= self.numColumns

	@property
	def whereItIs(self) -> tuple[int, int, int]:
		""":return: which columns of the run are showing, as (first, last, how many in all).

		Counted along the run, one based, not by the table's numbers, which have holes.
		"""
		return (self.at + 1, self.at + max(1, self.shown), self.numColumns)

	def cutting(self) -> "ColumnPlan":
		""":return: this layout with every column cut rather than wrapped.

		For the pinned header row, which is orientation and always one row, so the rows below
		never move. Cut at the end the reader chose for the heading, separately from the data.
		See `ColumnChoice.headerKeep`.
		"""
		return dataclasses.replace(
			self,
			columns=tuple(
				dataclasses.replace(column, overflow=TRUNCATE, keep=column.headerKeep)
				for column in self.columns
			),
		)

	def knows(self, column: int) -> bool:
		""":return: whether this plan was made from a table that had this column.

		Drawn somewhere in the run (see `holds`), or measured and left out, so a caret in an
		omitted column is not taken as a changed table.

		:param column: the table's own column number.
		"""
		return self.holds(column) or column in self.omitted or column in self.excluded

	def placements(self) -> tuple[Placement, ...]:
		""":return: where each column the band is showing goes. See `_placeFrom`."""
		return self._placeFrom(self.at)

	@property
	def numRows(self) -> int:
		""":return: how many band rows one row of this table takes."""
		placed = self.placements()
		return 1 + max((place.row for place in placed), default=0) if placed else 1

	def columnAt(self, row: int, offset: int) -> Optional[Column]:
		"""Which column a cell of a drawn row belongs to.

		For routing. Gaps between columns answer None, like padding.

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
		first, last, total = self.whereItIs
		pages = "" if self.showsEverything else f", columns {first} to {last} of {total}"
		return f"<ColumnPlan {widths} in {self.numCols} over {self.numRows} rows{pages}>"


READING_ORDER = ColumnPlan()
"""The plan that draws nothing, and what a table gets until the reader asks for otherwise.

Reading order, as NVDA reads a table; decision 16 of docs/design/structured-presentation-plan.md.
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

	Widths are planned for the typical cell, not the widest, so one long cell wraps rather
	than widening every row. Zero means only the widest was measured, which `typical` then
	uses.
	"""

	label: str = ""
	"""Its header, where it has one."""

	declared: bool = False
	"""Whether the table *said* that is its header, rather than it being read off row one.

	A declared header is pinned above the band; one borrowed from row one is already on it.
	See `flowTableSource.measure` and `TableFlowSource`.
	"""

	labelWidth: int = 0
	"""How wide the header is in cells, where it was measured separately.

	Zero where `width` already covers it, as from `flowTableSource.measure`. For a caller that
	knows the header separately. In cells, not `len(label)`.
	"""

	hidden: bool = False
	"""Whether the table itself is not showing this column.

	Such as a zero width list view column or a `display: none` cell. Excluded from the plan.
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
	excluded: Sequence[int] = (),
	choices: Optional[dict] = None,
	keyColumn: Optional[int] = None,
) -> ColumnPlan:
	"""Work out how wide each of a table's columns is drawn, and which of them share a page.

	1. A page holds as many columns as it can without a row growing taller than
	   `targetHeight`, found by searching down from all remaining columns; see
	   `_howManyFit`. A column that cannot meet it alone gets a page to itself.
	2. Spare cells are given away: wide columns shrink while above `READABLE_CELLS`, and
	   unclaimed cells go one at a time to the tallest predicted column.
	3. Nothing shrinks below `READABLE_CELLS`; what will not fit goes to the next page.

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
	:param excluded: the table's numbers for columns the reader's saved layout leaves out.
		Not measured here and never drawn, but *known* — see `ColumnPlan.excluded`.
	:param choices: what the reader decided about individual columns, by column number. See
		`ColumnChoice`.
	:param keyColumn: the column to repeat at the left of every later page, or None for the
		first one drawn.
	:return: the plan, or `READING_ORDER` when there is nothing to lay out.
	"""
	measured = list(measured)
	choices = dict(choices or {})
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
	shape["choices"] = choices
	widths, assignment = _dealIntoPages(wanted, reserve=0, **shape)
	keyColumn, keyWidth = _keyFor(
		wanted,
		numCols,
		gap,
		minWidth,
		pinKey and len(assignment) > 1,
		chosen=keyColumn,
	)
	if keyColumn is not None:
		# Deal again with the pin's cells taken out of every page after the first. Only ever
		# twice: the pin costs room, so a table that needed more than one page without it
		# still needs more than one page with it, and the answer cannot flip back.
		widths, assignment = _dealIntoPages(wanted, reserve=keyWidth + gap, **shape)
	return ColumnPlan(
		columns=tuple(
			_asChosen(item, widths[item.index], overflow, choices.get(item.index, NOTHING_CHOSEN))
			for item in wanted
		),
		numCols=numCols,
		maxRows=maxRows,
		gap=gap,
		assignment=assignment,
		keyColumn=keyColumn,
		keyWidth=keyWidth,
		excluded=tuple(sorted(excluded)),
		omitted=tuple(sorted(drawn.symmetric_difference(item.index for item in measured))),
		narrowed=tuple(item.index for item in wanted if widths[item.index] < item.wants),
	)


def effectiveOverflow(choice: "ColumnChoice", overflow: str) -> str:
	""":return: what actually becomes of a cell too long for one column.

	The reader's choice, else the table's. One function, used by both the height prediction
	and `_asChosen`, so they agree.

	:param choice: what the reader decided about this column.
	:param overflow: what the table does with a cell too long for its column.
	"""
	return choice.overflow if choice.overflow in OVERFLOW_STYLES else overflow


def _asChosen(item: "Measurement", width: int, overflow: str, choice: "ColumnChoice") -> "Column":
	""":return: one column as the measurement found it and the reader asked for it.

	:param item: the measurement.
	:param width: the width the fitting gave it.
	:param overflow: what the table does with a cell too long for its column.
	:param choice: what the reader decided about this column.
	"""
	return Column(
		index=item.index,
		width=width,
		label=choice.label or item.label,
		overflow=effectiveOverflow(choice, overflow),
		keep=choice.keep if choice.keep in KEEP_ENDS else KEEP_START,
		headerKeep=choice.headerKeep if choice.headerKeep in KEEP_ENDS else KEEP_START,
		startsAPage=choice.startsAPage,
	)


def _keyFor(
	wanted: Sequence[Measurement],
	numCols: int,
	gap: int,
	minWidth: int,
	pinKey: bool,
	chosen: Optional[int] = None,
) -> tuple[Optional[int], int]:
	"""Which column is repeated at the left of every later page, and how wide.

	The first drawn column, usually the row's label, by the plan's order so a hidden column is
	not pinned. Typical width, capped at `KEY_SHARE`.

	:param wanted: the columns being drawn, in order.
	:param numCols: the width of the band.
	:param gap: cells between columns.
	:param minWidth: the narrowest a column may be drawn.
	:param pinKey: whether the reader wants this at all.
	:param chosen: the column the reader picked for it, or None for the first drawn. A choice
		the plan is not drawing is ignored.
	:return: the column's number and its pinned width, or None and zero.
	"""
	if not pinKey or len(wanted) < 2:
		return None, 0
	key = next((item for item in wanted if item.index == chosen), wanted[0])
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
	choices: Optional[dict] = None,
) -> tuple[dict, tuple[tuple[int, ...], ...]]:
	"""Walk the columns left to right, filling one page at a time.

	Greedily in table order, so a column's page depends only on the columns before it.

	:param wanted: the columns being drawn, in order.
	:param reserve: cells taken out of every page after the first, for a pinned key column.
	:param numCols: the width of the band.
	:param maxRows: how many band rows one table row may use.
	:param gap: cells between columns.
	:param minWidth: the narrowest a column may be drawn.
	:param maxWidth: the widest.
	:param targetHeight: how tall a table row may be before a page gives up a column.
	:param overflow: what becomes of a cell too long for its column.
	:param choices: what the reader decided per column, which can both bound a width and say
		where a page begins. See `ColumnChoice`.
	:return: every column's width, and which columns are on which page.
	"""
	choices = dict(choices or {})
	widths: dict = {}
	assignment: list[tuple[int, ...]] = []
	rest = list(wanted)
	while rest:
		room = numCols if not assignment else max(minWidth, numCols - reserve)
		# A page the reader asked to begin somewhere ends the one before it, however much
		# room is left. Theirs is the only break named; the rest still fall where they fit.
		upTo = _upToTheirBreak(rest, choices)
		take, chosen = _howManyFit(
			upTo, room, numCols, maxRows, gap, minWidth, maxWidth, targetHeight, overflow, choices
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
	choices: Optional[dict] = None,
) -> tuple[int, dict]:
	"""How many of the columns still to place go on this page.

	Searched downwards, stopping at the first count within the target height, which is the
	largest that works. One is the floor, returned without checking height.

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
		widths = _fitWidths(
			items,
			_budgetFor(room, numCols, maxRows, take, gap),
			minWidth,
			maxWidth,
			choices,
		)
		height = _stackedHeight(items, widths, room, numCols, maxRows, gap, overflow, choices)
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


def _upToTheirBreak(rest: Sequence[Measurement], choices: dict) -> Sequence[Measurement]:
	""":return: the columns up to the next page the reader asked to begin.

	The first column is always taken, so this only shortens what the packing may consider.

	:param rest: the columns still to place, in order.
	:param choices: what the reader decided per column.
	"""
	for position, item in enumerate(rest):
		if position and choices.get(item.index, NOTHING_CHOSEN).startsAPage:
			return rest[:position]
	return rest


def _budgetFor(room: int, numCols: int, maxRows: int, count: int, gap: int) -> int:
	""":return: how many cells of content one page of the band has room for.

	An upper bound: gaps are counted as if on one row, which only makes shrinking less eager.

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
	choices: Optional[dict] = None,
) -> Optional[int]:
	"""How tall a row of these columns actually comes out, or None if they do not fit.

	Lane by lane and summed, each lane as tall as its tallest cell, as `flowRender._laidOut`
	stacks them. The packing is walked, since a total that fits may still not pack.

	:param items: the columns, in drawing order.
	:param widths: column number to width.
	:param room: cells on the first band row, which a pinned key column has taken from.
	:param numCols: cells on the rest.
	:param maxRows: how many lanes the columns may be packed into.
	:param gap: cells between columns.
	:param overflow: what becomes of a cell too long for its column. A cut one is always one
		row, however much it holds.
	:param choices: what the reader decided per column, read through `effectiveOverflow`.
	:return: the height in band rows, or None if these columns do not fit.
	"""
	choices = dict(choices or {})
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
		style = effectiveOverflow(choices.get(item.index, NOTHING_CHOSEN), overflow)
		tall = 1 if style == TRUNCATE else rowsNeeded(item.typical, width)
		lanes[row] = max(lanes[row], tall)
		offset += width + gap
	return sum(lanes) if lanes else 1


def _fitWidths(
	items: Sequence[Measurement],
	budget: int,
	minWidth: int,
	maxWidth: int,
	choices: Optional[dict] = None,
) -> dict:
	"""Decide a width for each column of one page.

	Shrinks wide columns and also gives unspent cells back.

	:param items: the columns on this page.
	:param budget: how many cells of content the page holds.
	:param minWidth: the narrowest a column may be drawn.
	:param maxWidth: the widest.
	:param choices: what the reader decided per column, which may bound either end.
	:return: column number to width.
	"""
	choices = dict(choices or {})
	wants = {item.index: _wantsWithin(item, choices, minWidth, maxWidth) for item in items}
	floors = {
		item.index: max(minWidth, choices.get(item.index, NOTHING_CHOSEN).minWidth)
		for item in items
	}
	widths = {item.index: max(floors[item.index], wants[item.index]) for item in items}
	_shrinkTowardsFitting(widths, budget, minWidth, wants, floors)
	return widths


def _wantsWithin(item: Measurement, choices: dict, minWidth: int, maxWidth: int) -> int:
	""":return: what a column asks for, inside whatever bounds the reader gave it."""
	choice = choices.get(item.index, NOTHING_CHOSEN)
	ceiling = min(maxWidth, choice.maxWidth) if choice.maxWidth else maxWidth
	floor = max(minWidth, choice.minWidth)
	return max(floor, min(max(ceiling, floor), item.wants))


def _shrinkTowardsFitting(
	widths: dict,
	budget: int,
	minWidth: int,
	wants: dict,
	floors: Optional[dict] = None,
) -> None:
	"""Take cells from the widest columns while they have any to spare.

	Towards fitting, stopping at `READABLE_CELLS`; what is still over goes to the next page.
	Widest first, one cell at a time.

	:param widths: column number to width, narrowed in place.
	:param budget: how many cells of content one page has room for.
	:param minWidth: the narrowest a column may be drawn at all.
	:param wants: what each column asked for, so a column already short is left alone.
	"""
	given = dict(floors or {})
	floors = {
		index: max(
			given.get(index, 0),
			min(wants[index], max(minWidth, READABLE_CELLS)),
		)
		for index in widths
	}
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

	No to almost everything, by design, so columns do not move. Only a different band width,
	row height or set of columns replans; wider content does not.

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

	For the report and tests. The same arithmetic as `_stackedHeight`.

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

	Includes which page and how many, which the display cannot show.
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
	if not plan.showsEverything:
		first, last, total = plan.whereItIs
		notes.append(f"Columns {first} to {last} of {total} are showing.")
	return " ".join([f"{drawn}.", *notes])


SEPARATOR_CELL = 0
"""A blank cell, matching `flow.BLANK_CELL` and NVDA's own use of 0 for an empty cell."""

POSITION_STRIDE = 1 << 16
"""How far apart two columns' positions are in a drawn row's position map.

A position is one number to routing and cursor code, so a table cell's position packs its
column and its place in the column in this base. Packed into the rendering rather than a
separate map, which could fall out of step with a cached rendering.
"""


def cellPosition(column: int, offset: int) -> int:
	"""Pack which column a cell came from, and where in it.

	The table's own column number, not the drawing order, which differs in a row with a
	merged cell. Both the plan and the row name columns by it.

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

	Asked of the region, so `flow.py` and `flowControl.py` need not know about tables.
	"""
	cells = getattr(region, "cells", None)
	if not cells:
		return None
	return cells if all(isinstance(cell, RowCell) for cell in cells) else None
