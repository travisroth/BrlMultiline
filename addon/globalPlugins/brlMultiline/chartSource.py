# BrlMultiline: where a chart's numbers come from.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Turning what the reader has selected into a series a chart can be drawn from.

Knows nothing of Excel: it asks the focused object for its grid through
`flowObjectTable.SHEET`, and Excel's half is `ExcelSheet.selectedValues` in
`appModules/excel.py`. What is here is not about any application: which column holds the
numbers, which the labels, and what to do when neither does.
"""

import math
from typing import NamedTuple, Optional

from logHandler import log

try:
	from exceptions import CallCancelled
except ImportError:  # Outside NVDA, and on an NVDA old enough not to have it.

	class CallCancelled(Exception):  # type: ignore[no-redef]
		"""Stand-in for NVDA's own, so this package imports where NVDA is not."""


from .chartDraw import Series, numberText
from .chartPrice import Period
from .flowObjectTable import describeThing, sheetOf

import addonHandler

addonHandler.initTranslation()

__all__ = [
	"CHART",
	"ChartDefinition",
	"ChartPoint",
	"ChartSeries",
	"Column",
	"NoNumbers",
	"POINT",
	"Table",
	"gridFromFocus",
	"periodsFrom",
	"seriesFromFocus",
	"seriesFromGrid",
	"seriesFromTable",
	"tableFromFocus",
	"tableFromGrid",
	"chartFromFocus",
	"chartOf",
	"pointOf",
]

MAX_BARS = 96
"""How many rows to take from a selection before cutting it short.

A limit on reading, so a whole selected column is not read; the chart's `MIN_SLOT` says how
many fit.
"""


CHART = "brlMultilineChart"
"""What an object that is part of a chart offers, to say which chart. See `chartOf`.

The chart's twin of `flowObjectTable.SHEET`. Excel's is in `appModules/excel.py`.
"""

POINT = "brlMultilinePoint"
"""What a point of a chart offers, to say which point it is. See `pointOf`."""


class ChartSeries(NamedTuple):
	"""One series of a chart: what it is called and its value at every category."""

	name: str
	values: list
	"""One per category, in order. None where the point has no value: an empty cell, #N/A."""

	key: object = None
	"""Which series this is, as the application numbers them, so a point of it can be matched to
	its line when two series share a name. The name is for speech."""


class ChartDefinition(NamedTuple):
	"""A chart as its application defines it, read once and holding nothing of the application.

	What an application module's chart offers from `definition()`. Drawn by
	`chartMenu.offerForChart`, which is where the kinds below become drawings.
	"""

	key: tuple
	"""Which chart this is, comparable across reads, so a point the reader arrives at can be told
	to be on the chart that is drawn. Two wrappers of one chart from an object model do not compare
	equal, which is why this is not the chart itself."""

	title: str
	"""The chart's title, or its name where it has none."""

	kind: str
	"""What to draw it as: `chartMenu.LINE`, `BARS`, `OHLC` or `HLC`. Empty where it cannot be
	drawn, and then `refusal` says why."""

	typeName: str
	"""The chart's type in the words the screen reader uses for it: "Line", "Scatter"."""

	categories: list
	"""One label per point, as the application shows it: a date reads as the date."""

	series: list
	"""`ChartSeries`, only those to draw: a stock chart's volume is already left out."""

	note: str = ""
	"""Something to say about what was drawn, such as the volume being left out."""

	refusal: str = ""
	"""Why it cannot be drawn, in the reader's words, where `kind` is empty."""


class ChartPoint(NamedTuple):
	"""Where in a chart the screen reader's own chart navigation is."""

	key: tuple
	"""The chart, as `ChartDefinition.key`."""

	index: int
	"""The point, counting from 0: the category it is at."""

	seriesName: str
	"""The series the reader is moving through, for speech."""

	series: object = None
	"""The same series as `ChartSeries.key`, which is what the level line follows it by."""


def chartOf(obj):
	""":return: the chart an object is part of, or None.

	What comes back answers `key()` and `definition()`, and can be told to `leadTo(index)`: see
	`appModules/excel.py`. Asked by attribute so this never imports an application module.

	:param obj: the object the reader is on.
	:raises CallCancelled: if the application stopped answering, which is not "no chart here".
	"""
	offered = getattr(obj, CHART, None) if obj is not None else None
	if offered is None:
		return None
	try:
		return offered()
	except CallCancelled:
		raise
	except Exception:
		log.debugWarning(f"Could not reach the chart behind {describeThing(obj)}", exc_info=True)
		return None


def pointOf(obj) -> Optional[ChartPoint]:
	""":return: which point of which chart an object is, or None if it is not a point.

	Asked on every change of focus, by `GlobalPlugin.event_gainFocus`, so it costs one `getattr`
	that finds nothing anywhere but a chart.

	:param obj: the new focus.
	"""
	offered = getattr(obj, POINT, None) if obj is not None else None
	if offered is None:
		return None
	try:
		return offered()
	except Exception:
		# Following is a convenience. Nothing a point fails to say may cost the focus change.
		log.debugWarning(f"Could not tell which chart point {describeThing(obj)} is", exc_info=True)
		return None


def chartFromFocus():
	""":return: the chart the reader is on, or None if they are not on one.

	:raises CallCancelled: if the application stopped answering.
	"""
	try:
		import api

		obj = api.getFocusObject()
	except Exception:
		log.error("BrlMultiline: could not read the focus to chart from", exc_info=True)
		return None
	return chartOf(obj)


class NoNumbers(Exception):
	"""There is no series to be had here, and the message says why.

	Every case is one the reader can act on, so it carries the explanation.
	"""


def seriesFromFocus() -> "list[Series]":
	"""Read a chartable series from wherever the reader is.

	:return: the labelled numbers, in the order they were selected.
	:raises NoNumbers: if there is nothing chartable here.
	"""
	return seriesFromGrid(gridFromFocus())


class Grid(list):
	"""The cells of a selection, and what the application could say about them.

	A list, plus whether the application says the first row names the columns. See
	`flowObjectTable.Sheet.selectedHeadings`.
	"""

	headings: Optional[bool] = None
	"""True if the first row names the columns, False if it does not, None if nobody knows."""


def gridFromFocus() -> "Grid":
	"""Read what the reader has selected, without deciding what it means yet.

	Read once; each chart type then says whether it can be drawn from it.

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
		grid = sheet.selectedValues(maxRows=MAX_POINTS)
	except CallCancelled:
		# NVDA gave up on the application, which is not the same as the application
		# having nothing to say. Told apart because they are different things for the
		# reader to do about: press again, against go and select something.
		log.error("BrlMultiline: the application did not answer in time to chart")
		raise NoNumbers(
			# Translators: reported when a chart was asked for and the application took so
			# long to answer that NVDA gave up waiting for it.
			_("The application did not answer in time; try again"),
		) from None
	except Exception:
		log.error("BrlMultiline: a grid would not say what is selected", exc_info=True)
		grid = None
	log.info(f"BrlMultiline: the selection is {len(grid) if grid else 0} rows")
	if not grid:
		# Translators: reported when a chart is asked for and nothing chartable is selected.
		raise NoNumbers(_("Nothing is selected to chart"))
	return _withHeadings(grid, sheet)


def _withHeadings(grid, sheet) -> Grid:
	""":return: the selection, carrying what the grid could say about its first row.

	A fact the numbers cannot give ("Metric, 2025, 2026"). Optional on the seam; without it,
	`_headerNames` guesses.

	:param grid: the rows read.
	:param sheet: the grid they came from.
	"""
	told = None
	ask = getattr(sheet, "selectedHeadings", None)
	if ask is not None:
		try:
			told = ask()
		except CallCancelled:
			told = None
		except Exception:
			log.debugWarning("BrlMultiline: a grid would not say whether it has headings", exc_info=True)
	rows = Grid(grid)
	rows.headings = told if isinstance(told, bool) else None
	log.info(f"BrlMultiline: the grid says its first row is headings: {rows.headings}")
	return rows


def _focusedSheet():
	"""Find what the reader is on and the grid it offers.

	Both, so a refusal can name what the reader was on. `api` is imported here so the module
	can be tested without NVDA.

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

	Booleans are refused, since Excel stores TRUE as -1. So are NaN and infinity, which would
	otherwise fail at drawing time with nothing to say which cell they came from.

	:param value: one cell's stored value.
	"""
	if value is None or isinstance(value, bool):
		return None
	try:
		number = float(value)
	except (TypeError, ValueError, OverflowError):
		return None
	return number if math.isfinite(number) else None


def _column(grid: "list[list]", index: int) -> list:
	""":return: one column of a grid, short rows tolerated.

	:param grid: rows of `(text, value)`.
	:param index: which column.
	"""
	return [row[index] if index < len(row) else ("", None) for row in grid]


def _numericColumn(grid: "list[list]") -> Optional[int]:
	"""Find the column to chart.

	The rightmost column numeric throughout, where a total or result usually sits.

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

	The column left of the numbers where there is one, else the charted cells' own text.

	:param grid: rows of `(text, value)`.
	:param numeric: which column holds the numbers.
	:return: one label per row.
	"""
	source = numeric - 1 if numeric > 0 else numeric
	return [str(cell[0]) for cell in _column(grid, source)]
# --- Several columns at once ----------------------------------------------------------------
#
# Line and candlestick charts need several columns together, so the selection is read once
# into a table and each chart type takes what it needs.


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

Larger than `MAX_BARS`, since more points than pins still draw as a compressed line.
"""

FORMATTING = "$£€¥%,\u00a0\u202f "
"""What a sheet may put around a number without making it something other than a number.

Currency symbols, a percent sign, and the several spaces used as group separators. Not the
decimal point, and not a minus sign, because those are part of the number itself. See
`_readsAsANumber`.
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
	told = getattr(grid, "headings", None)
	names = None if told is False else _headerNames(rows, told)
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


def _headerNames(rows: "list[list]", told: Optional[bool] = None) -> Optional[list]:
	"""Decide whether the first row names the columns.

	The application's answer where it gives one. Otherwise a heading row has no numbers and
	names every column that has numbers under it, so a data row with an empty cell is not
	taken for headings.

	:param rows: the selection.
	:param told: what the application said, or None if it could not say.
	:return: the headings, or None if the first row is data.
	"""
	if len(rows) < 2 or not rows[0]:
		return None
	names = [str(cell[0]).strip() for cell in rows[0]]
	if told:
		return names if any(names) else None
	if any(_asNumber(cell[1]) is not None for cell in rows[0]):
		return None
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

	The leftmost, when it holds text or numbers not shown as numbers, as a date stored as a
	serial is. A formatted number ("$1,000") still reads as one; see `_readsAsANumber`.

	:param rows: the selection, headings removed.
	:param width: how many columns it has.
	:return: the label column's index, or None if every column is data.
	"""
	if width < 2:
		return None
	cells = _column(rows, 0)
	if _dataColumn(cells) is None:
		return 0
	if any(_isShownAsSomethingElse(cell) for cell in cells):
		return 0
	return None


def _isShownAsSomethingElse(cell) -> bool:
	""":return: whether a cell holding a number shows something that is not a number.

	:param cell: one `(text, value)` pair.
	"""
	value = _asNumber(cell[1])
	text = str(cell[0]).strip()
	return value is not None and bool(text) and not _readsAsANumber(text)


def _readsAsANumber(text: str) -> bool:
	"""Whether displayed text is a number wearing a format.

	Currency symbols, group separators, a percent sign, and the accountant's parentheses for a
	negative are all formatting; what is left has to parse. A date does not: it carries slashes,
	dashes or a month's name, and none of those survive into a number.

	Generous, since dropping a series silently is worse than a date drawn as an obvious line.

	:param text: what the cell shows.
	"""
	stripped = text.strip().strip("()")
	for character in FORMATTING:
		stripped = stripped.replace(character, "")
	if not stripped:
		return False
	try:
		float(stripped)
	except ValueError:
		return False
	return True


def _dataColumn(cells: list) -> Optional[list]:
	"""Read one column as numbers, if that is what it is.

	An empty cell is a gap, not a zero. Text among the numbers disqualifies the column.

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

	For a selection with a heading row, which `seriesFromGrid` cannot read. The rightmost
	column, as there.

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

	By heading where there is one, else by position in the usual order, anything after the
	fourth left out. `periodsFrom` checks the numbers.

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
