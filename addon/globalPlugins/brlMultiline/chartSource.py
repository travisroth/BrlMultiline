# BrlMultiline: where a chart's numbers come from.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Turning what the reader has selected into a series a chart can be drawn from.

**This module does not know that Excel exists**, and neither does anything else outside
`appModules/excel.py`. It asks the focused object for its grid through `flowObjectTable.SHEET`
— the one seam an application module joins the add-on at — and asks that grid what is
selected. The Excel half of charting is `ExcelSheet.selectedValues`, beside the reading code
that already talks to that object model, and is loaded only while Excel is running.

That is the same arrangement the flow uses, and it is worth restating why it is worth the
indirection. The alternative was for this module to reach into `cell.excelCellObject` itself,
which works and puts knowledge of one application's object model in the part of the add-on
that is always loaded. Then a second application means a second special case here, a bug in
Excel handling can stop the graphics commands importing at all, and the rule that
`appModules/excel.py` owns everything Excel-shaped stops being true — which is the rule that
keeps the reading path testable without a spreadsheet.

What is left here is the part that is not about any application: which column holds the
numbers, which holds the labels, and what to do when neither does.
"""

from typing import NamedTuple, Optional

from logHandler import log

from .chartDraw import Series, numberText
from .chartPrice import Period
from .flowObjectTable import describeThing, sheetOf

__all__ = [
	"Column",
	"NoNumbers",
	"Table",
	"gridFromFocus",
	"periodsFrom",
	"seriesFromFocus",
	"seriesFromGrid",
	"seriesFromTable",
	"tableFromFocus",
	"tableFromGrid",
]

MAX_BARS = 96
"""How many rows to take from a selection before cutting it short.

The widest a chart could ever be on a 96 pin panel is 48 bars, so anything past this is a
reader who has selected a whole column rather than a range within it. A limit on the *reading*
rather than on the chart: it keeps a stray Ctrl+Space from turning a million cells into a
million tuples, and the chart's own `MIN_SLOT` gives the useful message about how many fit.
"""


class NoNumbers(Exception):
	"""There is no series to be had here, and the message says why.

	Every case is something the reader can act on — nothing that offers a grid, nothing
	selected, a selection with no numbers in it — so it carries the explanation rather than
	being a bare failure.
	"""


def seriesFromFocus() -> "list[Series]":
	"""Read a chartable series from wherever the reader is.

	:return: the labelled numbers, in the order they were selected.
	:raises NoNumbers: if there is nothing chartable here.
	"""
	return seriesFromGrid(gridFromFocus())


def gridFromFocus() -> "list[list]":
	"""Read what the reader has selected, without deciding what it means yet.

	Separate from `seriesFromGrid` because there is more than one chart to be made from the
	same cells now, and which one is the reader's choice rather than this module's. So the
	selection is read once, and each chart type says whether it can be drawn from it.

	:return: rows of `(text, value)`.
	:raises NoNumbers: if there is nothing chartable here.
	"""
	obj, sheet = _focusedSheet()
	# At INFO, not debug. Every refusal below looks the same from the outside — the command
	# said no — and which one it was is the whole of what a reader or a log needs to know. A
	# debug warning is no use for that: it is off by default, so the one press that failed is
	# never the press that was recorded.
	log.info(f"BrlMultiline: charting from {describeThing(obj)}, grid={sheet is not None}")
	if sheet is None:
		raise NoNumbers(
			# Translators: reported when a chart is asked for somewhere that offers no grid to
			# chart from. The placeholder is what the reader is on. A spreadsheet is the only
			# place that offers one today.
			_("Charts need a spreadsheet cell; this is {thing}").format(thing=describeThing(obj)),
		)
	try:
		grid = sheet.selectedValues()
	except Exception:
		log.error("BrlMultiline: a grid would not say what is selected", exc_info=True)
		grid = None
	log.info(f"BrlMultiline: the selection is {len(grid) if grid else 0} rows")
	if not grid:
		# Translators: reported when a chart is asked for and nothing chartable is selected.
		raise NoNumbers(_("Nothing is selected to chart"))
	return grid


def _focusedSheet():
	"""Find what the reader is on and the grid it offers.

	Both, because a refusal that can name what the reader *was* on is one they can act on:
	"charts need a spreadsheet cell; this is a list item" says where to go, and "charts need a
	spreadsheet" said over a spreadsheet says only that something is broken.

	`api` is imported here rather than at the top so that this module can be unit tested
	without a running NVDA, which is what keeps the column-picking rules below testable.

	:return: the focused object and its grid, either of which may be None.
	"""
	try:
		import api

		obj = api.getFocusObject()
	except Exception:
		log.error("BrlMultiline: could not read the focus to chart from", exc_info=True)
		return None, None
	try:
		return obj, sheetOf(obj)
	except Exception:
		log.error("BrlMultiline: could not reach a grid to chart from", exc_info=True)
		return obj, None


def seriesFromGrid(grid: "list[list]") -> "list[Series]":
	"""Pick the labels and the numbers out of a selection.

	:param grid: rows of `(text, value)`, as `Sheet.selectedValues` gives them.
	:return: the labelled numbers.
	:raises NoNumbers: if no column of the selection is numeric.
	"""
	if len(grid) > MAX_BARS:
		grid = grid[:MAX_BARS]
	numeric = _numericColumn(grid)
	if numeric is None:
		# Translators: reported when a chart is asked for over cells that hold no numbers.
		raise NoNumbers(_("There are no numbers in the selection to chart"))
	labels = _labelColumn(grid, numeric)
	return [
		Series(label=label, value=_asNumber(cell[1]))
		for label, cell in zip(labels, _column(grid, numeric))
	]


def _asNumber(value) -> Optional[float]:
	""":return: a cell's stored value as a number, or None if it is not one.

	Booleans are refused deliberately. Excel stores TRUE as -1 and Python calls a bool an int,
	so a column of flags would chart as bars of equal height and look like data.

	:param value: one cell's stored value.
	"""
	if value is None or isinstance(value, bool):
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def _column(grid: "list[list]", index: int) -> list:
	""":return: one column of a grid, short rows tolerated.

	:param grid: rows of `(text, value)`.
	:param index: which column.
	"""
	return [row[index] if index < len(row) else ("", None) for row in grid]


def _numericColumn(grid: "list[list]") -> Optional[int]:
	"""Find the column to chart.

	The **rightmost** column that is numeric throughout, because that is where a total or a
	computed result sits in a sheet laid out the ordinary way, and because a reader selecting
	two columns of a table means "these labels, those numbers".

	:param grid: rows of `(text, value)`.
	:return: the column index, or None if no column is numeric.
	"""
	if not grid:
		return None
	for index in reversed(range(max(len(row) for row in grid))):
		cells = _column(grid, index)
		if cells and all(_asNumber(cell[1]) is not None for cell in cells):
			return index
	return None


def _labelColumn(grid: "list[list]", numeric: int) -> "list[str]":
	"""Work out what to call each bar.

	The column to the left of the numbers where the selection has one, because that is how a
	table is laid out and it is what the reader meant by selecting both. Otherwise the charted
	cells' own displayed text, which for a bare column of numbers is the numbers themselves —
	and a bar called by its value is at least true, where a bar called by its position is one
	more thing to hold in mind.

	:param grid: rows of `(text, value)`.
	:param numeric: which column holds the numbers.
	:return: one label per row.
	"""
	source = numeric - 1 if numeric > 0 else numeric
	return [str(cell[0]) for cell in _column(grid, source)]
# --- Several columns at once ----------------------------------------------------------------
#
# A bar chart is one column of numbers with something to call each of them. Everything after
# it — a line chart of a closing price with its moving average, a candlestick chart — is
# several columns that have to stay together, so the selection is read once into a table and
# each chart type takes what it needs from that.
#
# The rules here are the ones that are not about any application: which column is the labels,
# which are data, and what a blank cell means. Excel is still nowhere in this file.


class Column(NamedTuple):
	"""One column of a selection, read as numbers."""

	name: str
	"""What the reader calls it: the heading, where the selection had one."""

	values: list
	"""One number per row, in order. None where that row's cell was empty."""


class Table(NamedTuple):
	"""A selection read as labelled columns of numbers."""

	labels: Optional[list]
	"""What each row is called, or None where no column of the selection said."""

	columns: "list[Column]"
	"""The numeric columns, left to right, the label column not among them."""

	@property
	def rowCount(self) -> int:
		""":return: how many rows the widest column has."""
		return max((len(column.values) for column in self.columns), default=0)


MAX_POINTS = 400
"""Rows to take from a selection before cutting it short.

Larger than `MAX_BARS` because a line chart is not limited by the panel the way a bar chart
is: more points than pins draw as a compressed line, which is still the right shape, whereas
more bars than pins is nothing at all. Four hundred trading days is about eighteen months,
which is more history than ninety-six pins can say anything useful about and is a sane place
to stop turning cells into tuples.
"""

MIN_COLUMN_NUMBERS = 2
"""Numbers a column must have before it counts as data rather than as a stray figure."""

PRICE_NAMES = {
	"open": ("open", "o", "opening"),
	"high": ("high", "h", "max"),
	"low": ("low", "l", "min"),
	"close": ("close", "c", "last", "closing", "adj close"),
}
"""Headings that name one of the four prices, so columns in any order can be recognised.

Matched before falling back to position, because a reader who has headings has already said
which column is which and should not have to reorder their sheet to be charted.
"""


def tableFromFocus() -> Table:
	"""Read the selection as labelled columns of numbers.

	:return: the table.
	:raises NoNumbers: if there is nothing chartable here.
	"""
	return tableFromGrid(gridFromFocus())


def tableFromGrid(grid: "list[list]") -> Table:
	"""Turn a selection into labels and numeric columns.

	:param grid: rows of `(text, value)`, as `Sheet.selectedValues` gives them.
	:return: the table.
	:raises NoNumbers: if no column of the selection is numeric.
	"""
	rows = grid[:MAX_POINTS]
	names = _headerNames(rows)
	if names:
		rows = rows[1:]
	width = max((len(row) for row in rows), default=0)
	labelIndex = _labelIndex(rows, width)
	columns = []
	for index in range(width):
		if index == labelIndex:
			continue
		values = _dataColumn(_column(rows, index))
		if values is not None:
			columns.append(Column(name=_nameFor(names, index), values=values))
	if not columns:
		# Translators: reported when a chart is asked for over cells that hold no numbers.
		raise NoNumbers(_("There are no numbers in the selection to chart"))
	labels = None
	if labelIndex is not None:
		labels = [str(cell[0]) for cell in _column(rows, labelIndex)]
	return Table(labels=labels, columns=columns)


def _headerNames(rows: "list[list]") -> Optional[list]:
	"""Decide whether the first row names the columns.

	**A heading row has no numbers in it and names every column that does.** The first half
	is the plain rule and is right about almost everything: a row of headings has no numbers
	in it and a row of data has at least one. The second half is for the case the first gets
	wrong — a first row whose only numeric column happens to be empty, which is data with a
	hole in it and looks exactly like a heading row. A real heading names the columns it
	heads, so a column with numbers under it and nothing at the top of it says the row was
	data. Getting this wrong drops a point off the chart and says nothing.

	Anything subtler than these two — matching known words, looking at formatting — would
	be a rule that works on the sheets it was written against.

	:param rows: the selection.
	:return: the headings, or None if the first row is data.
	"""
	if len(rows) < 2 or not rows[0]:
		return None
	if any(_asNumber(cell[1]) is not None for cell in rows[0]):
		return None
	names = [str(cell[0]).strip() for cell in rows[0]]
	if not any(names):
		return None
	body = rows[1:]
	for index in range(max((len(row) for row in body), default=0)):
		if _dataColumn(_column(body, index)) is None:
			continue
		if index >= len(names) or not names[index]:
			# A column with numbers in it and nothing at the top of it. So the first row
			# was data with a blank in it, not a heading row, and taking it away would
			# silently drop a point off the chart.
			return None
	return names


def _nameFor(names: Optional[list], index: int) -> str:
	""":return: what to call one column.

	:param names: the headings, or None.
	:param index: which column of the selection.
	"""
	if names and index < len(names) and names[index]:
		return names[index]
	# Translators: what a chart calls a column of a selection that had no heading. The
	# placeholder is its position in the selection, counting from one.
	return _("column {number}").format(number=index + 1)


def _labelIndex(rows: "list[list]", width: int) -> Optional[int]:
	"""Decide which column names the rows.

	**The leftmost, when it is not itself data.** Two ways it can fail to be data: it holds
	text, which is the ordinary table of a name and its numbers; or it holds numbers that are
	*displayed as something else*, which is what a date is. Excel stores a date as a serial
	number, so a column of dates is numeric to anything that only looks at what is stored —
	and a stock chart whose first series is the dates would be a straight line climbing off
	the top of the panel, drawn confidently.

	Comparing what the cell shows against what its number would print as is what tells them
	apart, and it costs nothing: the grid already carries both.

	:param rows: the selection, headings removed.
	:param width: how many columns it has.
	:return: the label column's index, or None if every column is data.
	"""
	if width < 2:
		return None
	cells = _column(rows, 0)
	if _dataColumn(cells) is None:
		return 0
	if any(_isDisplayedDifferently(cell) for cell in cells):
		return 0
	return None


def _isDisplayedDifferently(cell) -> bool:
	""":return: whether a cell shows something other than the number it holds.

	:param cell: one `(text, value)` pair.
	"""
	value = _asNumber(cell[1])
	text = str(cell[0]).strip()
	return value is not None and bool(text) and text != numberText(value)


def _dataColumn(cells: list) -> Optional[list]:
	"""Read one column as numbers, if that is what it is.

	**An empty cell is a gap, not a zero.** A twenty day moving average has nineteen empty
	cells above it, and a column that refused them would leave a stock chart with no moving
	average on it at all; a column that read them as zero would draw the average diving to the
	floor and climbing back, which looks exactly like a crash. Text in the middle of the
	numbers is a different matter and disqualifies the column, because that is a column of
	something else.

	:param cells: the column's `(text, value)` pairs.
	:return: one number or None per row, or None if this is not a column of numbers.
	"""
	values = []
	found = 0
	for cell in cells:
		number = _asNumber(cell[1])
		if number is None:
			if cell[1] is not None or str(cell[0]).strip():
				return None
			values.append(None)
			continue
		values.append(number)
		found += 1
	return values if found >= MIN_COLUMN_NUMBERS else None


def seriesFromTable(table: Table) -> "list[Series]":
	"""Read a table as one series of bars.

	The way in for a selection `seriesFromGrid` cannot read: one with a heading row. That path
	works straight from the grid and wants every cell of a column to be a number, which a
	heading makes false, and it is left alone because its rule about which column is charted and
	which names the bars is what a reader without headings has learned.

	The rightmost column, for the same reason as there: that is where a total or a computed
	result sits in a sheet laid out the ordinary way.

	:param table: the selection.
	:return: the labelled numbers, rows with no value left out.
	"""
	if not table.columns:
		return []
	column = table.columns[-1]
	series = []
	for index, value in enumerate(column.values):
		if value is None:
			# A bar with no value is not a short bar, and drawing it as one would be a
			# measurement the sheet never made.
			continue
		label = table.labels[index] if table.labels and index < len(table.labels) else numberText(value)
		series.append(Series(label=str(label), value=value))
	return series


def periodsFrom(table: Table) -> "list[Period]":
	"""Read a table as periods of open, high, low and close.

	:param table: the selection.
	:return: the periods, in the order they were selected.
	:raises NoNumbers: if these are not four price columns.
	"""
	picked = _priceColumns(table.columns)
	if picked is None:
		raise NoNumbers(
			# Translators: reported when a price chart is asked for over cells that are not
			# four columns of prices.
			_("A price chart needs open, high, low and close columns"),
		)
	periods = []
	for index in range(max(len(column.values) for column in picked)):
		values = [
			column.values[index] if index < len(column.values) else None for column in picked
		]
		if any(value is None for value in values):
			# A period missing one of its four prices is not a period. Skipped rather than
			# refused, because a blank row in the middle of a year of prices is a holiday.
			continue
		label = _periodLabel(table, index)
		openValue, high, low, close = values
		if high < max(openValue, close) or low > min(openValue, close):
			raise NoNumbers(
				# Translators: reported when the columns given for a price chart cannot be
				# open, high, low and close, because one period's high or low is inside them.
				# The placeholder is which period.
				_(
					"These are not open, high, low and close columns: "
					"the high or low at {label} is inside the open and close"
				).format(label=label),
			)
		periods.append(
			Period(label=label, open=openValue, high=high, low=low, close=close),
		)
	if not periods:
		# Translators: reported when a price chart is asked for and no row has all four prices.
		raise NoNumbers(_("There are no complete periods here to chart"))
	return periods


def _periodLabel(table: Table, index: int) -> str:
	""":return: what to call one period.

	:param table: the selection.
	:param index: which row.
	"""
	if table.labels and index < len(table.labels):
		return str(table.labels[index])
	return str(index + 1)


def _priceColumns(columns: "list[Column]") -> Optional[list]:
	"""Work out which columns are the open, the high, the low and the close.

	By heading where there is one, because a reader who has labelled their columns has already
	said which is which. By position otherwise, in the order the whole industry writes them —
	open, high, low, close, left to right — with anything after the fourth column, a volume
	usually, left out.

	Nothing here checks that the numbers make sense; `periodsFrom` does that, and does it after
	the columns are picked so that the message can say which period gave it away.

	:param columns: the table's numeric columns.
	:return: the four columns in open, high, low, close order, or None.
	"""
	named = {}
	for column in columns:
		key = _priceKey(column.name)
		if key and key not in named:
			named[key] = column
	if len(named) == len(PRICE_NAMES):
		return [named["open"], named["high"], named["low"], named["close"]]
	if len(columns) >= len(PRICE_NAMES):
		return list(columns[: len(PRICE_NAMES)])
	return None


def _priceKey(name: str) -> Optional[str]:
	""":return: which of the four prices a heading names, or None.

	:param name: the heading.
	"""
	text = " ".join(str(name).lower().split()).removesuffix(" price")
	for key, words in PRICE_NAMES.items():
		if text in words:
			return key
	return None
