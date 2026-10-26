# BrlMultiline: reading an Excel worksheet as a flow of rows.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Excel, and the only file in this add-on that knows Excel exists.

1. An application module rather than part of the global plugin, so it is loaded only while
   Excel runs and its questions are not asked of every object.
2. The flow needs one thing NVDA cannot answer: the cell at a coordinate. An overlay class on
   a cell offers `brlMultilineSheet`, which answers `flowObjectTable.Sheet`; neither
   `flowObjectTable.Sheet` nor `SheetTable` mentions Excel.
3. It replaces NVDA's own Excel module, so it extends it through `nvdaBuiltin`, and NVDA's
   behaviour continues through `super`.
4. Only the default COM object model can be read: `ExcelCell.excelCellObject` answers
   `cells(row, column)`. With UI Automation for Excel turned on
   (`config.conf["UIA"]["useInMSExcelWhenAvailable"]`) there is no coordinate lookup, and
   the cell is left to NVDA's ordinary reading.
"""

import ctypes
import math
import re
from typing import NamedTuple, Optional

import addonHandler
import config
import eventHandler
import NVDAHelper
from braille.constants import TEXT_SEPARATOR
from braille.regions.NVDAObject import NVDAObjectRegion, ReviewNVDAObjectRegion
from comtypes import BSTR, COMError
from config.configFlags import ReportTableHeaders
from logHandler import log
from NVDAHelper.localLib import EXCEL_CELLINFO
from NVDAObjects.window.excel import (
	NVCELLINFOFLAG_ADDRESS,
	NVCELLINFOFLAG_COORDS,
	NVCELLINFOFLAG_TEXT,
	ExcelCell,
	ExcelSelection,
	convertAddressToLocal,
	xlA1,
)
from nvdaBuiltin.appModules.excel import AppModule as ExcelAppModule

try:
	addonHandler.initTranslation()
except Exception:
	# Translation is a nicety; failing to set it up must not cost the reader their spreadsheet.
	log.debugWarning("BrlMultiline: could not initialise Excel translations", exc_info=True)

try:
	from exceptions import CallCancelled
except ImportError:  # Outside NVDA, and on an NVDA old enough not to have it.

	class CallCancelled(Exception):  # type: ignore[no-redef]
		"""Stand-in for NVDA's own. See `globalPlugins.brlMultiline.flow`."""


try:
	from tableUtils import HeaderCellTracker
except ImportError:  # pragma: no cover - an NVDA without it answers no headings at all.
	HeaderCellTracker = None
"""Where NVDA records the header rows and columns a reader has marked. See
`ExcelSheet._trackerNow`, which fills one rather than reading the worksheet's."""


try:
	from NVDAObjects.window import _msOfficeChart as officeChart
except ImportError:  # pragma: no cover - an NVDA without it has no chart objects to overlay.
	officeChart = None
"""NVDA's model of an Office chart: the chart, its series and its points as objects the reader
moves through, and the names of the chart types. Private to NVDA, so it is reached from here
only, and everything taken from it has a fallback. See `docs/design/excel-chart-plan.md`."""


def _xl(name: str, fallback: int) -> int:
	""":return: one of Excel's chart constants, by NVDA's name for it.

	Excel's numbers, which do not change, so a copy stands in if NVDA's module ever moves.

	:param name: NVDA's name for it, which is Excel's.
	:param fallback: the number, for when NVDA's module does not have it.
	"""
	return getattr(officeChart, name, fallback)


XL_LINE_CHARTS = frozenset(
	_xl(name, number)
	for name, number in (
		("xlLine", 4),
		("xlLineMarkers", 65),
		("xlLineStacked", 63),
		("xlLineStacked100", 64),
		("xlLineMarkersStacked", 66),
		("xlLineMarkersStacked100", 67),
	)
)
"""Excel's line chart types, drawn as the line chart. The stacked ones are drawn unstacked, so
each line is at its own values rather than on top of the one below."""

XL_BAR_CHARTS = frozenset((_xl("xlColumnClustered", 51), _xl("xlBarClustered", 57)))
"""Excel's column and bar chart types that are drawn, as the bar chart, with one series."""

XL_STOCK_CHARTS = {
	_xl("xlStockOHLC", 89): ("ohlc", 0),
	_xl("xlStockVOHLC", 91): ("ohlc", 1),
	_xl("xlStockHLC", 88): ("hlc", 0),
	_xl("xlStockVHLC", 90): ("hlc", 1),
}
"""Excel's stock chart types: what each is drawn as, and how many series lead its prices.

The volume charts have the volume first, in the order Excel requires of them, and it is left
out: the price bars have nowhere to put it."""

XL_LIST_SEPARATOR = _xl("xlListSeparator", 5)
XL_SECONDARY = _xl("xlSecondary", 2)

MAX_CHART_POINTS = 400
"""Points a chart may have to be drawn: the ceiling a selection is read to. See
`chartSource.MAX_POINTS`."""

XL_STACKED_LINE_CHARTS = frozenset(
	_xl(name, number)
	for name, number in (
		("xlLineStacked", 63),
		("xlLineStacked100", 64),
		("xlLineMarkersStacked", 66),
		("xlLineMarkersStacked100", 67),
	)
)
"""The line chart types Excel stacks, each line on top of the one below. Drawn unstacked, so the
drawing says so."""

XL_HORIZONTAL_BAR_CHARTS = frozenset((_xl("xlBarClustered", 57),))
"""The bar chart types Excel draws across the page. Drawn upright, so the drawing says so."""

XL_ERRORS = range(-2146826288, -2146826245)
"""The numbers Excel's error values arrive as when they come through as integers: #N/A, #DIV/0!
and the rest. A point holding one has no value."""


class SeriesReading(NamedTuple):
	"""One series of a chart. The shape `chartSource.ChartSeries` describes."""

	name: str
	values: list
	key: object = None
	"""Its number in Excel's `SeriesCollection`, from 1: what NVDA's point calls `arg1`. The name
	is for speech; two series may share one."""


class ChartReading(NamedTuple):
	"""A chart read from Excel. The shape `chartSource.ChartDefinition` describes, field for
	field; defined again here because this module does not import the global plugin."""

	key: tuple
	title: str
	kind: str
	typeName: str
	categories: list
	series: list
	note: str = ""
	refusal: str = ""


class PointReading(NamedTuple):
	"""Which point of which chart a point object is. The shape of `chartSource.ChartPoint`."""

	key: tuple
	index: int
	seriesName: str
	series: object = None
	"""The series' number in Excel's `SeriesCollection`, as `SeriesReading.key`."""


def _chartValue(value) -> Optional[float]:
	""":return: one value of a series, or None where the point has none.

	The rules of `chartSource._asNumber`: no booleans, no infinities, nothing that is not a
	number. And an Excel error arriving as an integer is no value, not a value of minus two
	billion.

	:param value: one entry of `Series.Values`.
	"""
	if value is None or isinstance(value, bool):
		return None
	if isinstance(value, int) and value in XL_ERRORS:
		return None
	try:
		number = float(value)
	except (TypeError, ValueError, OverflowError):
		return None
	return number if math.isfinite(number) else None


def seriesArguments(formula: str, separator: str) -> list:
	""":return: the arguments of a series formula, `=SERIES(name, categories, values, order)`.

	Split only on separators outside quotes, which may be doubled inside, and outside brackets
	and braces, as in `'Sales, Q1'!$A$2:$A$9` or `"Close, adjusted"`.

	:param formula: the series' `Formula`.
	:param separator: the reader's list separator, a comma or a semicolon, as Excel says it is.
	:return: the arguments as written, or nothing if this is not a formula of that shape.
	"""
	text = (formula or "").strip()
	start, end = text.find("("), text.rfind(")")
	if start < 0 or end <= start or not separator:
		return []
	body = text[start + 1 : end]
	arguments = []
	current = []
	depth = 0
	quote = ""
	index = 0
	while index < len(body):
		character = body[index]
		if quote:
			current.append(character)
			if character == quote:
				if body[index + 1 : index + 2] == quote:
					current.append(quote)
					index += 1
				else:
					quote = ""
		elif character in "'\"":
			quote = character
			current.append(character)
		elif character in "({":
			depth += 1
			current.append(character)
		elif character in ")}":
			depth -= 1
			current.append(character)
		elif depth == 0 and body.startswith(separator, index):
			arguments.append("".join(current))
			current = []
			index += len(separator)
			continue
		else:
			current.append(character)
		index += 1
	arguments.append("".join(current))
	return arguments


def _categoryText(value) -> str:
	""":return: a category Excel gave as a value rather than as the text of its cell.

	:param value: one entry of `Series.XValues`.
	"""
	if value is None:
		return ""
	if isinstance(value, float) and value.is_integer():
		return str(int(value))
	return str(value)


def _chartTypeName(chartType) -> str:
	""":return: a chart type in NVDA's own words, as NVDA's chart navigation says it."""
	names = getattr(officeChart, "chartTypeDict", None) or {}
	return str(names.get(chartType, ""))


class ExcelChart:
	"""One of Excel's charts, read as a whole for drawing.

	Read in bulk through `Series.Values`, where NVDA reads a point at a time for speech.
	"""

	def __init__(self, chart):
		"""
		:param chart: Excel's `Chart`, as NVDA's chart objects hold it in `officeChartObject`.
		"""
		self.chart = chart

	def key(self) -> tuple:
		""":return: which chart this is, as the workbook and the chart's name.

		Not the chart object, because two COM wrappers of one chart do not compare equal. An
		embedded chart's name includes its sheet's: "Prices Chart 1".
		"""
		chart = self.chart
		return (str(chart.Application.ActiveWorkbook.FullName), str(chart.Name))

	def title(self) -> str:
		""":return: the chart's title, or its name where it has none, as NVDA names it."""
		chart = self.chart
		if chart.HasTitle:
			return str(chart.ChartTitle.Text)
		return str(chart.Name)

	def definition(self) -> ChartReading:
		""":return: the chart, read, or a reading that says why it cannot be drawn.

		:raises CallCancelled: if NVDA stopped waiting on Excel.
		"""
		chart = self.chart
		chartType = chart.ChartType
		key = self.key()
		title = self.title()
		typeName = _chartTypeName(chartType)

		def refused(why: str) -> ChartReading:
			return ChartReading(key, title, "", typeName, [], [], refusal=why)

		count = chart.SeriesCollection().Count
		if not count:
			# Translators: reported when a chart with no series is asked for.
			return refused(_("This chart has no series"))
		everySeries = [chart.SeriesCollection(index) for index in range(1, count + 1)]
		notes = []
		if chartType in XL_LINE_CHARTS:
			kind, lead = "line", 0
			if chartType in XL_STACKED_LINE_CHARTS:
				# The drawing is not the shape on the screen, so the reader is told rather than left
				# to compare a stacked chart with a sighted colleague's and find it different.
				# Translators: added to what a stacked line chart is called on the display, which
				# draws each line at its own values rather than on top of the one below.
				notes.append(_("stacked lines drawn at their own values"))
		elif chartType in XL_BAR_CHARTS:
			if count > 1:
				return refused(
					# Translators: reported when a column or bar chart of several series is
					# asked for. The placeholder is the chart's type, as NVDA names it.
					_("{type} charts of more than one series cannot be drawn yet").format(type=typeName),
				)
			kind, lead = "bars", 0
			if chartType in XL_HORIZONTAL_BAR_CHARTS:
				# Translators: added to what a bar chart is called on the display, when Excel draws
				# its bars across the page and the display draws them standing up.
				notes.append(_("bars drawn upright"))
		elif chartType in XL_STOCK_CHARTS:
			kind, lead = XL_STOCK_CHARTS[chartType]
			if lead:
				# Translators: added to what a stock chart drawn without its volume is called.
				notes.append(_("volume not drawn"))
		else:
			return refused("")
		if kind in ("line", "bars"):
			for one in everySeries:
				if one.ChartType != chartType:
					# Translators: reported when a chart mixes kinds, such as columns and a line.
					return refused(_("Charts that mix kinds cannot be drawn yet"))
				if one.AxisGroup == XL_SECONDARY:
					# Translators: reported when a chart has a series on a second value axis.
					return refused(_("Charts with a second value axis cannot be drawn yet"))
		drawn = everySeries[lead:]
		series = [
			SeriesReading(str(one.Name), [_chartValue(value) for value in (one.Values or ())], key=number)
			for number, one in enumerate(drawn, start=lead + 1)
		]
		lengths = {len(one.values) for one in series}
		if len(lengths) != 1:
			# Translators: reported when a chart's series do not all have the same number of
			# points.
			return refused(_("This chart's series are not all the same length"))
		points = lengths.pop()
		if points > MAX_CHART_POINTS:
			return refused(
				# Translators: reported when a chart has more points than can be drawn. The
				# placeholders are how many it has and the most that can be drawn.
				_("This chart has {count} points; at most {most} can be drawn").format(
					count=points,
					most=MAX_CHART_POINTS,
				),
			)
		categories = self._categories(drawn[0], points)
		return ChartReading(key, title, kind, typeName, categories, series, note=", ".join(notes))

	def _categories(self, series, count: int) -> list:
		""":return: what each point is called, as the sheet shows it.

		As NVDA reads them, from the text of the cells the series formula names, so dates show
		as dates. Falls back to `XValues` where NVDA does, then to counting.

		:param series: the series to read the categories of.
		:param count: how many points it has.
		"""
		chart = self.chart
		try:
			separator = str(chart.Application.International(XL_LIST_SEPARATOR))
			arguments = seriesArguments(str(series.Formula), separator)
			if len(arguments) >= 3 and arguments[1].strip():
				cells = chart.Application.Range(arguments[1].strip())
				return [str(cells.Item(index).Text) for index in range(1, count + 1)]
		except CallCancelled:
			raise
		except Exception:
			log.debugWarning("Could not read a chart's categories from its cells", exc_info=True)
		try:
			values = list(series.XValues or ())
			if len(values) == count:
				return [_categoryText(value) for value in values]
		except CallCancelled:
			raise
		except Exception:
			log.debugWarning("Could not read a chart's categories", exc_info=True)
		return [str(index) for index in range(1, count + 1)]


CHART_DIGITS = 10
"""Significant digits a chart value is spoken to in NVDA's own chart navigation. See
`ChartNumber`.

Enough for any price or measurement a sheet holds, and few enough that the noise of binary
arithmetic, which sits around the sixteenth digit, never reaches speech."""


def _chartNumberText(value: float) -> str:
	""":return: a chart value as NVDA should say it: no binary noise, no ".0" on a whole number.

	:param value: the value.
	"""
	if not math.isfinite(value):
		return float.__repr__(value)
	rounded = float(f"{value:.{CHART_DIGITS}g}")
	if rounded.is_integer() and abs(rounded) < 1e15:
		return str(int(rounded))
	return float.__repr__(rounded)


class ChartNumber(float):
	"""A value of a chart series that subtracts and prints the way a reader expects.

	NVDA's `OfficeChartElementPoint._getChartElementText` speaks raw float arithmetic, such as
	3.0000000000000004, and has no smaller function to replace. This float behaves normally
	except that subtraction rounds to `CHART_DIGITS` significant digits and plain formatting
	drops the noise. An explicit format specification is passed straight through.
	"""

	def __sub__(self, other):
		difference = float.__sub__(self, other)
		if difference is NotImplemented:
			return difference
		return ChartNumber(float(f"{difference:.{CHART_DIGITS}g}"))

	def __rsub__(self, other):
		difference = float.__rsub__(self, other)
		if difference is NotImplemented:
			return difference
		return ChartNumber(float(f"{difference:.{CHART_DIGITS}g}"))

	def __str__(self) -> str:
		return _chartNumberText(self)

	def __format__(self, spec: str) -> str:
		if not spec:
			return _chartNumberText(self)
		return float.__format__(self, spec)


def _asChartNumber(value):
	""":return: a series value as a `ChartNumber`, or as it was if it is not an ordinary number.

	An empty point, an Excel error and a boolean are left exactly as Excel gave them, so NVDA
	says about them whatever it says today.

	:param value: one entry of `Series.Values`.
	"""
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		return value
	if isinstance(value, int) and value in XL_ERRORS:
		return value
	return ChartNumber(value)


class _ForwardingTo:
	"""Answers everything as the object it wraps does, except what a subclass defines."""

	def __init__(self, wrapped):
		object.__setattr__(self, "_wrapped", wrapped)

	def __getattr__(self, name):
		return getattr(self._wrapped, name)

	def __setattr__(self, name, value):
		setattr(self._wrapped, name, value)


class SeriesWithChartNumbers(_ForwardingTo):
	"""Excel's `Series`, whose `Values` are `ChartNumber`s. Everything else is Excel's own."""

	@property
	def Values(self):  # noqa: N802 - Excel's own spelling.
		values = self._wrapped.Values
		if values is None:
			return None
		return tuple(_asChartNumber(value) for value in values)


class ChartWithChartNumbers(_ForwardingTo):
	"""Excel's `Chart`, as NVDA's point objects are given it. See `ChartNumber`.

	A series asked for by number comes back as `SeriesWithChartNumbers`; asked for without one,
	`SeriesCollection()` is the collection, for its `Count`, as Excel gives it. Everything else --
	the formula, the axes, the chart type, `Points(n).Select()` -- goes straight to Excel.
	"""

	def SeriesCollection(self, *args):  # noqa: N802 - Excel's own spelling.
		found = self._wrapped.SeriesCollection(*args)
		return SeriesWithChartNumbers(found) if args else found


class SpreadsheetChart:
	"""The overlay on NVDA's chart objects: the chart, its series, its points, its other parts.

	Offers the chart to the global plugin, says which point a point is, and moves NVDA's place
	in the chart when the reader steps on the pins. What NVDA does with these objects — the
	arrow keys, the speech, the colours, Escape back to the cells — is left exactly as it is.
	"""

	def initOverlayClass(self):
		"""Give a point the chart through `ChartWithChartNumbers`, so what NVDA says of it is
		said without the noise of binary arithmetic. See `ChartNumber`.

		Here, because NVDA calls this after storing the chart and before using it. Points only.
		"""
		pointClass = getattr(officeChart, "OfficeChartElementPoint", None)
		if pointClass is None or not isinstance(self, pointClass):
			return
		chart = getattr(self, "officeChartObject", None)
		if chart is not None and not isinstance(chart, ChartWithChartNumbers):
			self.officeChartObject = ChartWithChartNumbers(chart)

	def brlMultilineChart(self) -> ExcelChart:
		""":return: the chart this is part of. See `chartSource.chartOf`."""
		return ExcelChart(self.officeChartObject)

	def brlMultilinePoint(self) -> Optional[PointReading]:
		""":return: which point of which chart this is, or None if it is not a point.

		NVDA's point carries its series and its point, counting from 1, as `arg1` and `arg2`;
		`arg2` is -1 on the object that stands for the series itself.
		"""
		pointClass = getattr(officeChart, "OfficeChartElementPoint", None)
		if pointClass is None or not isinstance(self, pointClass):
			return None
		seriesIndex, pointIndex = self.arg1, self.arg2
		if not seriesIndex or not pointIndex or pointIndex < 1:
			return None
		chart = self.officeChartObject
		return PointReading(
			ExcelChart(chart).key(),
			pointIndex - 1,
			str(chart.SeriesCollection(seriesIndex).Name),
			series=seriesIndex,
		)

	def reportFocus(self):
		"""NVDA's own report of a point arriving, unless the arrival is one the pins made.

		See `brlMultilineLeadTo`: a step on the pins has already said the point, so the focus
		event it sends is not spoken a second time. Only that once; every other arrival, an
		arrow key's included, is reported exactly as NVDA reports it.
		"""
		if getattr(self, "_brlMultilineQuiet", False):
			self._brlMultilineQuiet = False
			return
		super().reportFocus()

	def brlMultilineLeadTo(self, index: int) -> bool:
		"""Move NVDA's place in this series to a point, with everything but the speech.

		So the next arrow key goes on from where the reader stepped on the pins. NVDA's series
		keeps the point it is on as `activeElement` and moves from it; this sets it, and selects
		the point in Excel, then queues a focus event for the point exactly as NVDA's own
		`navigateToElement` does.

		The focus event is what moves NVDA's focus, navigator object and braille, so it is
		sent; only its speech is skipped, once, by `reportFocus`. Reaches into NVDA's
		`elementList` and `activeElement`, which are not promised, so that is kept here alone.

		:param index: the point, counting from 0.
		:return: whether NVDA's place moved. False on anything that is not a series or a point
			of one, such as the chart itself before a series is chosen.
		"""
		seriesClass = getattr(officeChart, "OfficeChartElementSeries", None)
		if seriesClass is None:
			return False
		series = self if isinstance(self, seriesClass) else getattr(self, "parent", None)
		if not isinstance(series, seriesClass):
			return False
		elements = getattr(series, "elementList", None)
		count = getattr(series, "pointsCount", 0)
		if not elements or not 0 <= index < min(count, len(elements)):
			return False
		element = elements[index]
		# Selected first, so a refused selection does not move NVDA's place.
		element.select()
		series.activeElement = element
		element._brlMultilineQuiet = True
		eventHandler.queueEvent("gainFocus", element)
		return True


def isPartOfAChart(obj) -> bool:
	""":return: whether this object is one of NVDA's Office chart objects, in Excel.

	`isinstance` and an attribute only, for the reason `readsByCoordinate` gives: this runs for
	every object NVDA builds.

	:param obj: the object NVDA has just built.
	"""
	kinds = tuple(
		found
		for found in (
			getattr(officeChart, "OfficeChartElementBase", None),
			getattr(officeChart, "OfficeChartElementList", None),
		)
		if found is not None
	)
	return bool(kinds) and isinstance(obj, kinds) and hasattr(obj, "officeChartObject")


_UNASKED = object()
"""Stands for a question not yet put, where None is one of the answers."""

MAX_SELECTED_ROWS = 500
"""Rows of a selection that may be read, however many the reader selected.

Ctrl+Space selects 1,048,576 rows. Fixed here beside the calls it protects, so no caller can
raise or forget it. `ExcelSheet._clipToData` cuts to the used range first; this backs it up.
More than any chart can use, and quick to read.
"""

MAX_SELECTED_COLUMNS = 64
"""Columns of a selection that may be read. Ctrl+Shift+Space selects 16,384 of them."""

MAX_SELECTED_CELLS = 4000
"""Cells of a selection that may be read, which is the ceiling the other two do not give.

Row and column caps still multiply, and Excel's used range can be very wide, since it grows
with formatting and never shrinks. Rows come down to fit, never columns: a missing column is
a missing series, while fewer rows is the same chart.
"""

MAX_ROWS_ONE_AT_A_TIME = 100
"""Rows to read when the whole block could not be had in one call.

About a second at the measured ten milliseconds a row. The fallback when the block fetch is
not available.
"""

MAX_COLUMNS = 250
"""How wide a sheet may be before it is left to NVDA's ordinary reading.

Excel's used range grows with formatting and never shrinks, and `flowTableSource.measure`
reads every column, so a wider sheet is refused and keeps NVDA's ordinary reading. Generous
for anything arranged by hand, and bounds the measurement at a couple of thousand reads.
"""


XL_CELL_TYPE_VISIBLE = 12
"""Excel's `xlCellTypeVisible`, which is how a range is asked for the part of it on show.

Named here because NVDA's own Excel module has no name for it; it has `xlCellTypeFormulas`
beside it for its formula quick navigation and nothing for this.
"""

ADDRESS_SEPARATORS = ",;"
"""What Excel may put between the areas of an address: a comma, or a semicolon where that is
the reader's list separator."""


def columnNumberOf(letters: str) -> int:
	""":return: which column a run of letters names: A is 1, Z is 26, AA is 27.

	:param letters: the column's letters, in either case.
	"""
	number = 0
	for letter in letters.upper():
		number = number * 26 + (ord(letter) - ord("A") + 1)
	return number


def columnSpansIn(address: Optional[str]) -> Optional[list]:
	""":return: the runs of columns an address covers, as (first, last) pairs, or None.

	A sheet with column 5 hidden answers "$A$1:$D$129,$F$1:$F$129". None where an area names
	no column, as a whole-row reference does, since every column shows there.

	:param address: what Excel called the range, or None.
	"""
	if not address:
		return None
	spans = []
	for part in re.split(f"[{ADDRESS_SEPARATORS}]", address):
		letters = re.findall(r"([A-Za-z]+)", part)
		if not letters:
			return None
		numbers = [columnNumberOf(found) for found in letters]
		spans.append((min(numbers), max(numbers)))
	if not spans:
		return None
	return sorted(spans)


def rowSpansIn(address: Optional[str]) -> Optional[list]:
	""":return: the runs of rows an address covers, as (first, last) pairs, or None.

	As a filtered sheet answers, such as "$A$1:$F$3,$A$9:$F$20". Parsed from the string, one
	call, rather than walking `Areas` at two calls per area.

	:param address: what Excel called the range, or None.
	"""
	if not address:
		return None
	spans = []
	for part in re.split(f"[{ADDRESS_SEPARATORS}]", address):
		# The row numbers of "$A$9:$F$20", and of "$A$9", and of a whole-row "$9:$20". A
		# column is letters and a row is digits, so the digits are the answer whichever
		# shape the area has.
		numbers = [int(found) for found in re.findall(r"(\d+)", part)]
		if not numbers:
			continue
		spans.append((min(numbers), max(numbers)))
	if not spans:
		return None
	return sorted(spans)


class ExcelSheet:
	"""One worksheet, addressed by coordinate. Answers `flowObjectTable.Sheet`.

	Built afresh for each reading: it is cheap, and a held one could outlast a sheet change.
	"""

	def __init__(self, cell) -> None:
		"""
		:param cell: the `NVDAObjects.window.excel.ExcelCell` the reader is on.
		"""
		self.cell = cell
		self._headers = _UNASKED
		"""What the columns are called, worked out once. See `columnHeaders`."""

		self._used = None
		"""How far Excel considers this sheet used, asked once. See `shape`."""

		self._usedCorners = _UNASKED
		"""Where Excel considers this sheet used, all four corners, asked once.
		See `_usedBox`, and `shape` for why that is a different question."""

		self._shown = _UNASKED
		"""What Excel is showing of this sheet, asked once: the runs of rows and the runs of
		columns, either of which may be None for "cannot say". See `_whatIsShowing`."""

		self.obj = cell.parent
		"""The worksheet, which is what tells one sheet from another. NVDA builds it from the
		cell's own `Worksheet`, and compares two of them by the sheet's index — see
		`ExcelWorksheet._isEqual`, which is what stops a layout following the reader onto the
		next sheet."""

	def _application(self):
		""":return: Excel itself, whichever kind of object this sheet was built from.

		From `excelCellObject` or `excelRangeObject`, both Excel ranges; the only place the two
		differ.
		"""
		range_ = getattr(self.cell, "excelCellObject", None)
		if range_ is None:
			range_ = self.cell.excelRangeObject
		return range_.Application

	def _selectedRange(self):
		""":return: the range the reader has selected, or None.

		The selection object's own range in preference to Excel's current selection, which may
		have moved since NVDA built the object.
		"""
		range_ = getattr(self.cell, "excelRangeObject", None)
		if range_ is not None:
			return range_
		return self._application().Selection

	@property
	def _sheet(self):
		""":return: Excel's own worksheet object, which is what answers by coordinate."""
		return self.obj.excelWorksheetObject

	def where(self) -> tuple:
		""":return: the row and column the reader is on, as Excel numbers them.

		NVDA's `rowNumber` and `columnNumber`, the numbers `cells(row, column)` takes; else
		Excel's own, since NVDA's helper can be silent. Never assumed to be A1; see `sheetFor`,
		which declines when neither answers.
		"""
		return (self._coordinate("rowNumber", "row"), self._coordinate("columnNumber", "column"))

	def _coordinate(self, said: str, asked: str) -> int:
		""":return: one of the reader's coordinates, or 0 if nothing will say.

		:param said: what NVDA calls it on the cell.
		:param asked: what Excel calls it on the range.
		"""
		# `getattr` for the range rather than the attribute directly, because a selection
		# carries `excelRangeObject` where a cell carries `excelCellObject`. NVDA's own answer
		# is first for both and is the one that almost always replies, so an object with
		# neither name still gets its coordinate rather than raising on the way to the fallback.
		range_ = getattr(self.cell, "excelCellObject", None) or getattr(
			self.cell,
			"excelRangeObject",
			None,
		)
		for thing, name in ((self.cell, said), (range_, asked)):
			try:
				found = int(getattr(thing, name, 0) or 0)
			except CallCancelled:
				# Not "this cell will not say where it is", which declines the whole sheet.
				raise
			except Exception:
				log.debugWarning(f"Could not read a cell's {name}", exc_info=True)
				continue
			if found > 0:
				return found
		return 0

	def shape(self) -> tuple:
		""":return: how far the sheet is used, as (rows, columns), counted from A1.

		From A1, so coordinates are Excel's own with no offset to carry. From the used range,
		read once per adapter; the reader's position is asked afresh each time. Reaches at least
		as far as the reader's cell, which on a blank sheet can be past the used range.
		"""
		row, column = self.where()
		if self._used is None:
			try:
				used = self._sheet.usedRange
				self._used = (
					int(used.row) + int(used.rows.count) - 1,
					int(used.column) + int(used.columns.count) - 1,
				)
			except CallCancelled:
				raise
			except Exception:
				# Unknown rather than zero, which would present a truncated sheet. `sheetFor`
				# then declines.
				log.debugWarning("Could not ask a worksheet how far it is used", exc_info=True)
				raise LookupError("The worksheet would not say how far it is used") from None
		rows, columns = self._used
		return (max(1, rows, row), max(1, columns, column))

	def whereIsIt(self) -> Optional[str]:
		""":return: the workbook and the sheet, which is what tells one worksheet from another.

		See `flowObjectTable.Sheet.whereIsIt` and `flowTableLayouts.keyFor`. The workbook's name
		where it has no path, as when unsaved.
		"""
		try:
			sheet = self._sheet
			book = sheet.parent
			where = str(getattr(book, "fullName", "") or getattr(book, "name", "") or "").strip()
			return f"{where}/{str(sheet.name or '').strip()}" if where else None
		except CallCancelled:
			raise
		except Exception:
			log.debugWarning("Could not ask a worksheet which workbook it is in", exc_info=True)
			return None

	def usedShape(self) -> tuple:
		""":return: how far Excel considers this sheet *used*, as (rows, columns).

		Unlike `shape`, which reaches as far as the reader, this moves only when the sheet does.
		See `flowObjectTable.Sheet.usedShape`.
		"""
		self.shape()
		return self._used

	def tooWide(self) -> bool:
		""":return: whether this sheet is too wide to measure without stopping NVDA.

		See `MAX_COLUMNS`, which is about what Excel's used range says rather than about what
		anybody wrote.
		"""
		return self.shape()[1] > MAX_COLUMNS

	def textRow(self, row: int, first: int, last: int) -> Optional[list]:
		""":return: a row's text across a span of columns, in one call, or None.

		One cross-process call, through the batch fetch NVDA's quick navigation uses
		(`ExcelCellInfoQuicknavIterator.iterate`) pointed at a contiguous range. Gives the
		displayed text, unlike `Range.Value2`, which gives stored values. None wherever it
		cannot be sure, and the caller reads cell by cell. See `flowObjectTable.Sheet.textRow`.

		:param row: the row to read.
		:param first: the first column of the span.
		:param last: the last column, inclusive.
		"""
		count = last - first + 1
		if count < 1:
			return None
		try:
			sheet = self._sheet
			span = sheet.range(sheet.cells(row, first), sheet.cells(row, last))
			address = convertAddressToLocal(
				self._application(),
				span.address(True, True, xlA1, True),
			)
			said = _cellInfosFor(self.cell, address, count)
		except CallCancelled:
			raise
		except Exception:
			log.debugWarning(f"Could not read row {row} in one call", exc_info=True)
			return None
		if said is None:
			return None
		texts = [""] * count
		merged: dict = {}
		for index, (_row, column, text, address) in enumerate(said):
			# By the coordinate it came back with, and by its place in the answer only where
			# it came back without one. A cell that says which column it is in cannot be put
			# in the wrong one by a reordered answer.
			place = column - first if column else index
			if not 0 <= place < count:
				continue
			# Stripped, because the object path strips — see `flowTableSource._textOf` — and
			# a column measured from padding is a column wider than what is drawn in it.
			texts[place] = (text or "").strip()
			if address:
				merged.setdefault(address, []).append(place)
		for places in merged.values():
			# **A merged cell is one cell under several columns.** The helper reads `.text`
			# off each cell of the range, and only the top left member of a merge holds it,
			# so the rest came back empty. The address it reports is the *merge area's* — all
			# the members of one merge answer with the same address and nothing else does —
			# so they are the group that shares whichever of them has the text.
			if len(places) < 2:
				continue
			said = next((texts[place] for place in places if texts[place]), "")
			if said:
				for place in places:
					texts[place] = said
		return texts

	def rowAfter(self, row: int, by: int) -> Optional[int]:
		""":return: the next row Excel is showing, or None where it is showing no more.

		A filter hides rows without removing them, so Excel is asked once per reading which
		rows show. Where it cannot say, the next row is given, since None ends the walk.

		:param row: the row walked from, one based.
		:param by: which way, as 1 or -1.
		"""
		spans = self._shownRows()
		if spans is None:
			return row + by
		if by > 0:
			for first, last in spans:
				if row < first:
					return first
				if first <= row < last:
					return row + 1
			return None
		for first, last in reversed(spans):
			if row > last:
				return last
			if first < row <= last:
				return row - 1
		return None

	def rowShowing(self, row: int) -> Optional[bool]:
		""":return: whether Excel is showing one row, or None where it will not say.

		For rows fetched before a filter was applied. From the same answer `rowAfter` uses.
		Only within the used range; outside it the answer is "cannot say", read as showing, so
		empty rows past the data are not taken as hidden. A filter hiding the very first or last
		used rows is caught on the reader's next move instead.

		:param row: the row asked about, one based.
		"""
		spans = self._shownRows()
		if not spans:
			return None
		if row < spans[0][0] or row > spans[-1][1]:
			return None
		return any(first <= row <= last for first, last in spans)

	def selectedValues(self, maxRows: int = 0, maxColumns: int = 0) -> Optional[list]:
		"""What is selected in this sheet, as text and as numbers together.

		Answers `flowObjectTable.Sheet.selectedValues`, for charts.

		1. Values from `Value2`, the stored numbers, which bar heights need.
		2. Text from `_textBlock`, since `Range.Text` is Null for a multi-cell range, so labels
		   match what the reader reads.
		3. The selection is clipped to the used range (see `_clipToData`), then capped.

		:param maxRows: rows to read at most, or 0 for the ceiling here, which applies either way.
		:param maxColumns: columns to read at most, on the same terms.
		:return: rows of `(text, value)`, or None if Excel will not say.
		"""
		self._selectedBox = None
		try:
			box = self._clipToData(self._selectedRange(), maxRows, maxColumns)
			if box is None:
				return None
			firstRow, firstColumn, lastRow, lastColumn = box
			# Kept so that `selectedHeadings` can answer about the same cells. The clip is what
			# turns Ctrl+Space into the rows that have data in them, so the first row of what
			# came back is not the first row of what the reader selected.
			self._selectedBox = box
			sheet = self._sheet
			span = sheet.range(
				sheet.cells(firstRow, firstColumn),
				sheet.cells(lastRow, lastColumn),
			)
			values = _asGrid(span.Value2)
		except CallCancelled:
			# Not "there is no selection". See `sheetFor`.
			raise
		except Exception:
			log.debugWarning("Could not read the Excel selection", exc_info=True)
			return None
		if not values:
			return None
		width = max(len(row) for row in values)
		texts = self._textBlock(firstRow, firstColumn, len(values), width)
		if texts is None:
			# No block fetch, so a call per row, with fewer rows. See `MAX_ROWS_ONE_AT_A_TIME`.
			values = values[:MAX_ROWS_ONE_AT_A_TIME]
			texts = [
				self.textRow(firstRow + offset, firstColumn, firstColumn + width - 1)
				for offset in range(len(values))
			]
		return [
			_pairUp(texts[offset], valueRow, firstRow + offset, firstColumn)
			for offset, valueRow in enumerate(values)
		]

	def selectedHeadings(self) -> Optional[bool]:
		"""Whether the first row of the last selection read is a row of headings.

		Answers `flowObjectTable.Sheet.selectedHeadings`, and Excel can answer it properly for a
		structured table: `ListObject.HeaderRowRange` says which row that table's headings are
		on, and comparing it with the first row read settles the question outright. Nothing in
		the values could: "Metric, 2025, 2026" over "Sales, 10, 20" is cell for cell the same
		shape as a table of years with no headings, and guessing wrong charts 2025 and 2026 as
		data points.

		**Only for a structured table.** A plain range is not one, and there Excel knows no more
		than the reader can see, so the honest answer is None and the caller goes on guessing.

		:return: True, False, or None where it cannot be settled.
		"""
		box = getattr(self, "_selectedBox", None)
		if box is None:
			return None
		firstRow, firstColumn = box[0], box[1]
		try:
			table = self._sheet.cells(firstRow, firstColumn).ListObject
			if table is None:
				return None
			header = table.HeaderRowRange
		except CallCancelled:
			raise
		except Exception:
			# Not in a table, or an Excel that will not say. Either way this settles nothing.
			log.debug("Excel would not say whether the selection has headings", exc_info=True)
			return None
		if header is None:
			# A table with its header row turned off. That is an answer: the first row is data.
			return False
		try:
			return int(header.Row) == int(firstRow)
		except Exception:
			log.debugWarning("Could not read where an Excel table's headings are", exc_info=True)
			return None

	def _clipToData(self, selection, maxRows: int, maxColumns: int) -> Optional[tuple]:
		"""Cut a selection down to what can be read without stopping NVDA.

		1. Clip to the used range, one call, so a whole-column selection becomes the rows with
		   data.
		2. Then cap, whether or not the clip worked, so no selection can stop NVDA.

		:param selection: Excel's own range.
		:param maxRows: rows the caller can use, or 0 for the ceiling.
		:param maxColumns: columns the caller can use, or 0 for the ceiling.
		:return: (first row, first column, last row, last column), or None where the selection
			holds none of the sheet's data.
		"""
		firstRow = int(selection.Row)
		firstColumn = int(selection.Column)
		lastRow = firstRow + int(selection.Rows.Count) - 1
		lastColumn = firstColumn + int(selection.Columns.Count) - 1
		used = self._usedBox()
		if used is not None:
			firstRow = max(firstRow, used[0])
			firstColumn = max(firstColumn, used[1])
			lastRow = min(lastRow, used[2])
			lastColumn = min(lastColumn, used[3])
			if lastRow < firstRow or lastColumn < firstColumn:
				# The selection and the data do not overlap: a column to the right of
				# everything written in, or rows below it.
				return None
		rows = min(maxRows or MAX_SELECTED_ROWS, MAX_SELECTED_ROWS)
		columns = min(maxColumns or MAX_SELECTED_COLUMNS, MAX_SELECTED_COLUMNS)
		lastColumn = min(lastColumn, firstColumn + columns - 1)
		rows = min(rows, max(1, MAX_SELECTED_CELLS // (lastColumn - firstColumn + 1)))
		lastRow = min(lastRow, firstRow + rows - 1)
		log.info(
			f"BrlMultiline: charting rows {firstRow} to {lastRow}, "
			f"columns {firstColumn} to {lastColumn}",
		)
		return (firstRow, firstColumn, lastRow, lastColumn)

	def _usedBox(self) -> Optional[tuple]:
		""":return: the corners of the used range, or None where the sheet will not say.

		All four corners, for intersecting with a selection. Separate from `shape`, which
		refuses a table without an answer, while a chart can carry on with the cap alone.
		Asked once.
		"""
		if self._usedCorners is _UNASKED:
			self._usedCorners = None
			try:
				used = self._sheet.usedRange
				firstRow = int(used.row)
				firstColumn = int(used.column)
				self._usedCorners = (
					firstRow,
					firstColumn,
					firstRow + int(used.rows.count) - 1,
					firstColumn + int(used.columns.count) - 1,
				)
			except CallCancelled:
				raise
			except Exception:
				log.debugWarning("Could not ask a worksheet where its data is", exc_info=True)
		return self._usedCorners

	def _textBlock(self, firstRow: int, firstColumn: int, height: int, width: int) -> Optional[list]:
		"""Read a rectangle's displayed text in one call.

		`textRow` for a whole rectangle, for charts. Cells are placed by the coordinates the
		helper reports, not by position, so a block without coordinates is refused and the
		caller reads a row at a time.

		:param firstRow: the top row of the rectangle.
		:param firstColumn: its leftmost column.
		:param height: how many rows.
		:param width: how many columns.
		:return: one list of strings per row, or None where the block could not be had whole.
		"""
		count = height * width
		if count < 1:
			return None
		try:
			sheet = self._sheet
			span = sheet.range(
				sheet.cells(firstRow, firstColumn),
				sheet.cells(firstRow + height - 1, firstColumn + width - 1),
			)
			address = convertAddressToLocal(
				self._application(),
				span.address(True, True, xlA1, True),
			)
			said = _cellInfosFor(self.cell, address, count)
		except CallCancelled:
			raise
		except Exception:
			log.debugWarning("Could not read a selection in one call", exc_info=True)
			return None
		if said is None:
			return None
		texts = [[""] * width for _ in range(height)]
		for row, column, text, _address in said:
			if not row or not column:
				return None
			down = row - firstRow
			across = column - firstColumn
			if 0 <= down < height and 0 <= across < width:
				# Stripped, because the object path strips — see `flowTableSource._textOf`.
				texts[down][across] = (text or "").strip()
		return texts

	def columnsShowing(self) -> Optional[set]:
		""":return: the columns Excel is showing, or None where every column is showing.

		A hidden column still answers for its cells. From the same answer as the rows, at no
		extra cost.
		"""
		spans = self._whatIsShowing()[1]
		if spans is None:
			return None
		showing = set()
		for first, last in spans:
			showing.update(range(first, last + 1))
		return showing or None

	def _shownRows(self) -> Optional[list]:
		""":return: the runs of rows Excel is showing, or None where it would not say."""
		return self._whatIsShowing()[0]

	def _whatIsShowing(self) -> tuple:
		""":return: what Excel is showing, as (runs of rows, runs of columns).

		Asked once per reading; changing a filter builds the sheet afresh anyway.
		"""
		if self._shown is _UNASKED:
			address = self._askWhatIsShowing()
			self._shown = (rowSpansIn(address), columnSpansIn(address))
		return self._shown

	def _askWhatIsShowing(self) -> Optional[str]:
		""":return: how Excel describes the part of this sheet on show, or None if it will not.

		Two calls. An unfiltered sheet answers with one area.
		"""
		try:
			showing = self._sheet.usedRange.SpecialCells(XL_CELL_TYPE_VISIBLE)
			address = showing.address(True, True, xlA1, False)
		except CallCancelled:
			# Not "every row is showing". See `sheetFor`.
			raise
		except Exception:
			# Excel raises rather than answering when a range holds nothing at all, and an
			# older or otherwise unwilling Excel may simply refuse. Either way the walk goes
			# back to stepping by one, which is what it did before this existed.
			log.debugWarning("Excel would not say which rows it is showing", exc_info=True)
			return None
		return address

	def columnHeaders(self, first: int, last: int) -> Optional[dict]:
		""":return: what each column of a span is called, {} where none is, or None to ask cells.

		Asking cell by cell is very slow. NVDA's header cell tracker says which cell heads
		which columns, so a marked sheet is answered by one batch read of its header row
		(as `fetchAssociatedHeaderCellText` would), and an unmarked one by no read at all.
		An empty answer is given only from a tracker filled here; see `_trackerNow`. None for
		a heading several rows tall or one bounded to a run of rows, so the caller asks the
		cells.

		:param first: the first column of the span, one based.
		:param last: the last column, inclusive.
		"""
		if self._headers is _UNASKED:
			self._headers = self._askedHeaders()
		if self._headers is None:
			return None
		return {column: said for column, said in self._headers.items() if first <= column <= last}

	def _askedHeaders(self) -> Optional[dict]:
		"""Work out what every column of the sheet is called, once. See `columnHeaders`.

		The whole sheet rather than the span in hand, because the answer is cached and a later
		page asks about columns this one did not.
		"""
		try:
			marked = self._markedHeaders()
		except CallCancelled:
			raise
		except Exception:
			log.debugWarning("Could not ask a worksheet where its headings are", exc_info=True)
			return None
		if marked is None:
			return None
		if not marked:
			return {}
		width = self.shape()[1]
		found: dict = {}
		for row, low, high in marked:
			high = min(high, width)
			if high < low:
				continue
			said = self.textRow(row, low, high)
			if said is None:
				# The heading row could not be read in one go, and guessing which of these
				# columns it would have named is worse than asking their cells.
				return None
			for index, text in enumerate(said):
				# The first entry that answers, in the tracker's own order, which is what
				# `fetchAssociatedHeaderCellText` returns for a cell.
				if text and not found.get(low + index):
					found[low + index] = text
		return found

	def _markedHeaders(self) -> Optional[list]:
		""":return: (row, first column, last column) per marked column heading, or None.

		From NVDA's tracker, following `tableUtils.HeaderCellTracker.iterPossibleHeaderCellInfosFor`.
		None where the tracker could not be finished, or for shapes `columnHeaders` refuses.
		"""
		tracker = self._trackerNow()
		if tracker is None:
			return None
		infos = getattr(tracker, "infosDict", None) or {}
		marked = []
		for key in list(getattr(tracker, "listByRow", ()) or ()):
			info = infos.get(key)
			if info is None or not getattr(info, "isColumnHeader", False):
				continue
			if getattr(info, "minRowNumber", None) or getattr(info, "maxRowNumber", None):
				return None
			if int(getattr(info, "rowSpan", 1) or 1) != 1:
				return None
			low = max(int(info.columnNumber), int(getattr(info, "minColumnNumber", 0) or 0) or 1)
			high = int(getattr(info, "maxColumnNumber", 0) or 0) or self.shape()[1]
			if high >= low:
				marked.append((int(info.rowNumber), low, high))
		return marked

	def _trackerNow(self):
		""":return: a header cell tracker filled here and now, or None if it cannot be.

		NVDA's lazily built tracker keeps whatever its walk produced, so an interrupted walk
		looks like a sheet with no headings. Filled here, the walk either finishes or raises,
		giving None. The result is put on the worksheet, so cells resolve headers through the
		same tracker.
		"""
		populate = getattr(self.obj, "populateHeaderCellTrackerFromNames", None)
		if populate is None or HeaderCellTracker is None:
			return None
		tracker = HeaderCellTracker()
		populate(tracker)
		self.obj.headerCellTracker = tracker
		return tracker

	def cellAt(self, row: int, column: int):
		""":return: the cell at one coordinate, or None if it cannot be reached.

		Built from `ExcelCell` itself, as `ExcelWorksheet._get_firstChild` does, never from
		the composed class of the cell in hand, which would give bases with no consistent method
		resolution order. A merged coordinate reads as its top left content. For cells that are
		drawn and routed into; measuring uses `textRow`.
		"""
		try:
			found = ExcelCell(
				windowHandle=self.cell.windowHandle,
				excelWindowObject=self.cell.excelWindowObject,
				excelCellObject=self._sheet.cells(row, column),
			)
		except CallCancelled:
			# Not "there is no cell here": NVDA has stopped waiting on Excel, and a reader
			# that hears "empty" instead is told the sheet is blank. See
			# `globalPlugins.brlMultiline.flow.CallCancelled`.
			raise
		except Exception:
			log.debugWarning(f"Could not reach row {row} column {column}", exc_info=True)
			return None
		if found is None:
			# The metaclass answers None rather than raising when it will not build one.
			log.debugWarning(f"NVDA would not build a cell at row {row} column {column}")
			return None
		# Give the cell this worksheet, since `ExcelCell._get_parent` would build a new one per
		# cell, each walking the workbook for headers. Assigning the auto-property skips it.
		found.parent = self.obj
		return found


def _cellInfosFor(cell, address: str, count: int) -> Optional[list]:
	""":return: where each cell of a range is, what it says, and its merge area, or None.

	The only ctypes in this file: hand NVDA's in-process helper an address and a count, and
	read back what it filled in. Only the needed fields are requested, rather than
	`NVCELLINFOFLAG_ALL`. The address is the merge area's, which tells a merged cell from a
	blank one. See `ExcelSheet.textRow`.

	:param cell: any cell of the sheet, for the window and the helper's binding handle.
	:param address: the range, already in the application's own notation.
	:param count: how many cells the range holds.
	:return: one (row number, column number, text, address) per cell of the range, or None
		for anything short of the whole range.

	The row number is for placing cells in a block read; see `ExcelSheet._textBlock`. A short
	answer is incomplete, not empty, since the helper stops at the first cell it cannot get,
	so it gives None.
	"""
	binding = cell.appModule.helperLocalBindingHandle
	if not binding:
		return None
	infos = (EXCEL_CELLINFO * count)()
	fetched = ctypes.c_long()
	result = NVDAHelper.localLib.nvdaInProcUtils_excel_getCellInfos(
		binding,
		cell.windowHandle,
		BSTR(address),
		NVCELLINFOFLAG_TEXT | NVCELLINFOFLAG_COORDS | NVCELLINFOFLAG_ADDRESS,
		count,
		infos,
		ctypes.byref(fetched),
	)
	if result != 0 or fetched.value != count:
		if result == 0 and fetched.value:
			log.debugWarning(
				f"Excel answered for {fetched.value} of {count} cells of {address}, "
				"so the row is being read cell by cell instead",
			)
		return None
	return [
		(
			int(infos[index].rowNumber or 0),
			int(infos[index].columnNumber or 0),
			infos[index].text or "",
			infos[index].address or "",
		)
		for index in range(count)
	]


def _asGrid(value) -> list:
	"""Normalise what `Range.Value2` hands back into rows of cells.

	Several cells answer a tuple of row tuples, one cell answers a bare value.

	:param value: whatever the property returned.
	:return: rows of cell values.
	"""
	if isinstance(value, tuple):
		if value and isinstance(value[0], tuple):
			return [list(row) for row in value]
		return [list(value)]
	return [[value]]


def _pairUp(texts: Optional[list], values: list, row: int, firstColumn: int) -> list:
	"""Put each cell's displayed text beside its stored value.

	Where the text could not be had, the cell's address stands in.

	:param texts: what the row displays, or None.
	:param values: what it stores.
	:param row: which sheet row this is, for naming a cell that has no text.
	:param firstColumn: the sheet column the selection starts at.
	:return: `(text, value)` per cell.
	"""
	paired = []
	for offset, value in enumerate(values):
		text = texts[offset] if texts is not None and offset < len(texts) else None
		if not text:
			text = _cellName(row, firstColumn + offset)
		paired.append((str(text), value))
	return paired


def _cellName(row: int, column: int) -> str:
	"""Name a cell by its address, for a bar whose own cell displays nothing.

	:param row: the sheet row, as Excel numbers it.
	:param column: the sheet column, as Excel numbers it.
	:return: an A1 style address.
	"""
	letters = ""
	while column > 0:
		column, remainder = divmod(column - 1, 26)
		letters = chr(ord("A") + remainder) + letters
	return f"{letters}{row}"


def sheetFor(cell):
	""":return: the worksheet a cell is in, or None if it cannot be read by coordinate.

	Each no leaves NVDA's ordinary reading of the cell:

	- **Nothing will say where the reader is.** See `ExcelSheet.where`.
	- **The sheet is too wide to measure.** See `MAX_COLUMNS`.
	- **The sheet cannot be reached at all**, which is any other failure.

	:param cell: the cell the reader is on.
	"""
	try:
		sheet = ExcelSheet(cell)
		if 0 in sheet.where():
			log.debugWarning("An Excel cell would not say which row and column it is")
			return None
		if sheet.tooWide():
			log.debugWarning(
				f"An Excel sheet says it is {sheet.shape()[1]} columns wide, which is more "
				f"than the {MAX_COLUMNS} that can be measured without stopping NVDA. "
				"Reading it as NVDA ordinarily would.",
			)
			return None
	except CallCancelled:
		# **Not "this is not a worksheet".** NVDA has stopped waiting on Excel, and answering
		# no here tells the reader they are not in a table at all — an explanation of
		# something that never happened, and one they cannot act on. It goes up to whoever
		# asked. See `globalPlugins.brlMultiline.flow.CallCancelled`.
		raise
	except Exception:
		log.debugWarning("Could not reach the worksheet behind a cell", exc_info=True)
		return None
	return sheet


def wantedHeaders() -> tuple:
	""":return: which of a cell's headers to show, as the attribute names to read them from.

	The Document Formatting "report table headers" setting that speech obeys, in speech's order:
	row header, then column header. See `speech.getPropertiesSpeech`. Read on each call.
	"""
	try:
		setting = config.conf["documentFormatting"]["reportTableHeaders"]
	except Exception:
		log.debugWarning("Could not read NVDA's reportTableHeaders setting", exc_info=True)
		setting = ReportTableHeaders.ROWS_AND_COLUMNS.value
	wanted = []
	if setting in (ReportTableHeaders.ROWS_AND_COLUMNS.value, ReportTableHeaders.ROWS.value):
		wanted.append("rowHeaderText")
	if setting in (ReportTableHeaders.ROWS_AND_COLUMNS.value, ReportTableHeaders.COLUMNS.value):
		wanted.append("columnHeaderText")
	return tuple(wanted)


def headerTextFor(cell) -> str:
	""":return: what a cell's headers say, or "" where there are none to say.

	As speech asks: NVDA's `columnHeaderText` and `rowHeaderText`, which resolve through the
	headers the reader marked.

	:param cell: the `NVDAObjects.window.excel.ExcelCell` being drawn.
	"""
	said = []
	for name in wantedHeaders():
		try:
			answer = getattr(cell, name)
		except Exception:
			# A cancelled COM call among them, and it is caught here rather than handed
			# upward on purpose: braille is drawn from here, there is no boundary above
			# this to hand a failure to, and a header that could not be read this time
			# would cost the reader the cell as well as its header. The next update asks
			# again.
			log.debugWarning(f"Could not read a cell's {name}", exc_info=True)
			answer = None
		if answer:
			said.append(answer.strip())
	return TEXT_SEPARATOR.join(text for text in said if text)


class CellHeaders:
	"""A cell's one line of braille, with the header the reader marked after the coordinates.

	NVDA speaks a marked header but braille omits it, since `NVDAObjectRegion.update` never
	passes `columnHeaderText` to `getPropertiesBraille`. Added after the coordinates, as
	speech does, through `appendText`, which is joined before translation, and reset
	afterwards so an update does not add it twice. Shown on every cell, unlike speech,
	since braille is a standing description.
	"""

	def update(self):
		theirs = self.appendText
		said = headerTextFor(self.obj)
		if said:
			self.appendText = TEXT_SEPARATOR + said + theirs
		try:
			super().update()
		finally:
			self.appendText = theirs


class CellRegion(CellHeaders, NVDAObjectRegion):
	"""A worksheet cell as braille draws it while following the focus."""


class ReviewCellRegion(CellHeaders, ReviewNVDAObjectRegion):
	"""The same cell while braille follows the review cursor.

	Separate because NVDA's review region focuses the object first on a routing key.
	"""


class HeadersInBraille:
	"""The overlay that gives a worksheet cell its header in braille.

	Separate from `SpreadsheetCell`, the flow's seam, since a cell that cannot be laid out
	still deserves its header.
	"""

	def getBrailleRegions(self, review: bool = False):
		"""Yield the regions braille should draw this cell from, which is one region.

		NVDA's `getFocusRegions` makes exactly one `NVDAObjectRegion` for a cell, since it has
		no navigable text; this is that region with the header added. `getFocusRegions`
		updates it.

		:param review: whether braille is following the review cursor rather than the focus.
		"""
		yield (ReviewCellRegion if review else CellRegion)(self)


class SpreadsheetCell:
	"""The overlay on a worksheet cell: it offers a sheet, and it can be gone to.

	Two methods, and they are the whole interface between this application and the rest of the
	add-on. See the module docstring.
	"""

	def brlMultilineSheet(self):
		""":return: the worksheet this cell is in, as `flowObjectTable.Sheet`, or None."""
		return sheetFor(self)

	def setFocus(self):
		"""Go to this cell, which is what a routing key over it means.

		`NVDAObject.setFocus` does nothing by default. This is NVDA's own sequence from
		`ExcelBrowseModeTreeInterceptor.navigationHelper`: select, activate, then fire the
		focus event on this object.
		"""
		try:
			cell = self.excelCellObject
			cell.Select()
			cell.Activate()
		except Exception:
			log.debugWarning("Excel would not go to a cell", exc_info=True)
			return
		eventHandler.executeEvent("gainFocus", self)


class SpreadsheetSelection:
	"""The overlay on a selected range: it offers a sheet, and nothing else.

	With several cells selected NVDA builds an `ExcelSelection`, which the cell overlay
	does not cover, so the seam would vanish. It has `rowNumber`, `columnNumber`, a worksheet
	parent and `excelRangeObject`, all `ExcelSheet` needs. No `setFocus`, since a routing key
	lands on a cell, not a selection.
	"""

	def brlMultilineSheet(self):
		""":return: the worksheet this selection is in, as `flowObjectTable.Sheet`, or None."""
		return sheetFor(self)


class CellOutlivingItsWorkbook:
	"""A cell that answers None, rather than raising, once Excel has let go of what is behind it.

	Closing a workbook with control+W raises a `typedCharacter` event on the dead cell, and
	NVDA's `_get_excelCellInfo` then raises a COM error (0x800A01A8). The fault is NVDA's, but
	the logged class name carries this add-on's overlays. None is what NVDA's own
	`_get_excelCellInfo` answers when the helper is missing, so every caller handles it. Only
	this one property is covered.
	"""

	def _get_excelCellInfo(self):
		""":return: what NVDA's own answers, or None where Excel no longer has the cell."""
		try:
			return super()._get_excelCellInfo()
		except COMError:
			# Not `Exception`, and `CallCancelled` is not caught here either: a COM call NVDA
			# stopped waiting for is raised as `CallCancelled`, which is a plain exception
			# rather than a `COMError` and so goes past this to whoever asked. See `sheetFor`.
			log.debugWarning("Excel would not describe a cell, whose workbook has probably closed")
			return None


def isAWorksheetCell(obj) -> bool:
	""":return: whether this object is a cell of Excel's COM object model.

	The model whose cells carry marked headers. A UI Automation cell is left to NVDA; see the
	module docstring.

	:param obj: the object NVDA has just built.
	"""
	return isinstance(obj, ExcelCell)


def isASelectedRange(obj) -> bool:
	""":return: whether this object is a selection of several cells in the COM model.

	See `SpreadsheetSelection`.

	:param obj: the object NVDA has just built.
	"""
	if not isinstance(obj, ExcelSelection):
		return False
	# The same two questions `readsByCoordinate` asks, against the names a selection uses,
	# and asked for the same reason: nothing here may reach for the parent, which would
	# build a whole worksheet for every object NVDA constructs.
	return hasattr(obj, "excelRangeObject") and hasattr(obj, "excelWindowObject")


def readsByCoordinate(obj) -> bool:
	""":return: whether this object is a worksheet cell that can be read by coordinate.

	The COM model's cell only; see the module docstring. Must not ask for the cell's parent:
	this runs for every object NVDA builds, and `ExcelCell._get_parent` builds a worksheet
	that walks the workbook.

	:param obj: the object NVDA has just built.
	"""
	if not isinstance(obj, ExcelCell):
		return False
	return hasattr(obj, "excelCellObject") and hasattr(obj, "excelWindowObject")


OVERLAYS = (
	(HeadersInBraille, isAWorksheetCell),
	(SpreadsheetCell, readsByCoordinate),
	(SpreadsheetSelection, isASelectedRange),
	(CellOutlivingItsWorkbook, isAWorksheetCell),
	(SpreadsheetChart, isPartOfAChart),
)
"""The overlay classes this module adds, and what each of them is for.

Several, each with its own purpose: see `HeadersInBraille`, `CellOutlivingItsWorkbook` and
`SpreadsheetChart`, and docs/design/excel-chart-plan.md.
"""


class AppModule(ExcelAppModule):
	"""NVDA's Excel module, with the overlay classes in L{OVERLAYS} added.

	Everything NVDA's own module does goes on happening: this subclasses it and calls `super`
	rather than standing in for it, which is what `nvdaBuiltin` exists for.
	"""

	def chooseNVDAObjectOverlayClasses(self, obj, clsList):
		super().chooseNVDAObjectOverlayClasses(obj, clsList)
		try:
			for overlay, belongs in OVERLAYS:
				# Not twice, and a class made of an overlay counts as having it, since composing
				# again gives bases with no consistent method resolution order. See
				# `ExcelSheet.cellAt`.
				if any(issubclass(found, overlay) for found in clsList):
					continue
				if belongs(obj):
					clsList.insert(0, overlay)
		except Exception:
			log.debugWarning("Could not tell whether this is a worksheet cell", exc_info=True)
