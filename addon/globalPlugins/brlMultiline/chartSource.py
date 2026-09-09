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

from typing import Optional

from logHandler import log

from .chart import Series
from .flowObjectTable import describeThing, sheetOf

__all__ = [
	"NoNumbers",
	"seriesFromFocus",
	"seriesFromGrid",
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
	return seriesFromGrid(grid)


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
