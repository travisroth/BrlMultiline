# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for bar charts drawn as dots, and for reading a series out of a selection.

The drawing half is arithmetic and is tested as arithmetic: what matters is that a reader
comparing two bars with a finger is comparing the numbers and not an artefact of the rounding.
Three things here are that, and each was a decision rather than an accident — a small value
must still be a bar, a bar of zero must not punch a hole in the baseline, and the bars must be
told apart by a gap.

The reading half is about the split between what Excel stores and what it displays. A
percentage stored as 0.25 and shown as "25%" has to be charted as 0.25 or the bars are wrong
against each other, and has to be *called* what the reader sees. Getting that backwards would
produce a chart that is entirely plausible and entirely wrong, which is the failure worth
having tests for.
"""

import ast
import os
import sys
import unittest

from ._stubs import installStubs

installStubs()

sys.path.insert(
	0,
	os.path.join(
		os.path.dirname(__file__),
		"..",
		"..",
		"..",
		"addon",
		"brailleDisplayDrivers",
		"brlMultilineMonarch",
	),
)

from pinBuffer import PinBuffer  # noqa: E402

from brlMultiline import chartSource  # noqa: E402
from brlMultiline.chart import ChartRefused, Series, barChart  # noqa: E402


def newBuffer(width, height):
	""":return: a buffer, standing in for the display's own factory."""
	return PinBuffer(width, height)


def rows(drawing):
	""":return: the drawing as text, raised dots as O."""
	return drawing.buffer.rows()


def columnHeights(drawing):
	""":return: how many raised dots each column of the drawing has."""
	text = rows(drawing)
	return [sum(1 for row in text if row[x] == "O") for x in range(drawing.buffer.width)]


class TestTheBars(unittest.TestCase):
	def test_aChartFillsTheRectangleItWasGiven(self):
		"""Composed for its space rather than compressed into it, which is why the fitted view
		of a chart is its natural size."""
		drawing = barChart(newBuffer, 24, 10, [Series("a", 1), Series("b", 2)])
		self.assertEqual((drawing.width, drawing.height), (24, 10))

	def test_tallerNumbersMakeTallerBars(self):
		drawing = barChart(newBuffer, 30, 11, [Series("a", 1), Series("b", 2), Series("c", 3)])
		heights = columnHeights(drawing)
		# The first column of each bar: 30 pins over 3 bars is a slot of 10.
		self.assertLess(heights[0], heights[10])
		self.assertLess(heights[10], heights[20])

	def test_theTallestBarReachesTheTop(self):
		drawing = barChart(newBuffer, 20, 10, [Series("a", 5), Series("b", 10)])
		self.assertEqual(rows(drawing)[0][10], "O")

	def test_barsAreProportionalToTheirNumbers(self):
		"""What a finger is actually comparing."""
		drawing = barChart(newBuffer, 20, 11, [Series("a", 5), Series("b", 10)])
		heights = columnHeights(drawing)
		# The baseline row is in both, so the bars are their heights plus that shared row.
		self.assertEqual(heights[10] - 1, 10)
		self.assertEqual(heights[0] - 1, 5)

	def test_aSmallValueIsStillABar(self):
		"""Rounded rather than truncated: a bar that is not there cannot be compared with one
		that is, and a reader would read the gap as a missing value rather than a small one."""
		drawing = barChart(newBuffer, 20, 10, [Series("tiny", 1), Series("huge", 1000)])
		self.assertGreater(columnHeights(drawing)[0], 1)

	def test_aBarOfZeroDoesNotPunchAHoleInTheBaseline(self):
		"""A reader sweeping the floor should feel it continuous, with bars standing on it."""
		drawing = barChart(newBuffer, 20, 10, [Series("none", 0), Series("some", 4)])
		baseline = rows(drawing)[-1]
		self.assertTrue(all(char == "O" for char in baseline))

	def test_barsAreSeparatedSoTwoAreNotOneBlock(self):
		drawing = barChart(newBuffer, 20, 10, [Series("a", 8), Series("b", 8)])
		text = rows(drawing)
		# The last column of the first slot is the gap, and it is clear above the baseline.
		self.assertEqual(text[0][9], ".")
		self.assertEqual(text[0][0], "O")

	def test_negativesGrowDownFromAZeroLineInsideTheDrawing(self):
		drawing = barChart(newBuffer, 20, 11, [Series("up", 10), Series("down", -10)])
		text = rows(drawing)
		# The zero line is no longer the floor, and the second bar hangs below it.
		baselineRows = [index for index, row in enumerate(text) if all(char == "O" for char in row)]
		self.assertTrue(baselineRows)
		zero = baselineRows[0]
		self.assertGreater(zero, 0)
		self.assertLess(zero, len(text) - 1)
		self.assertEqual(text[-1][10], "O")
		self.assertEqual(text[0][0], "O")

	def test_allNegativeNumbersHangFromTheTop(self):
		drawing = barChart(newBuffer, 20, 10, [Series("a", -5), Series("b", -10)])
		self.assertTrue(all(char == "O" for char in rows(drawing)[0]))


def spell(text):
	"""A stand-in for braille translation: one cell per character, dot 1 always raised.

	Enough to check where text lands and how much of it there is, which is what the layout
	is about. What the cells actually say is liblouis's business and is tested by using it.
	"""
	return [0b00000001 for _ in text]


class TestAChartWithNegativesInIt(unittest.TestCase):
	"""One scale, and zero is one of the numbers it has to hold.

	The chart used to place the zero line by the whole low-to-high range and then draw each bar
	as a fraction of the largest magnitude. Those are two different scales whenever the positive
	and negative extremes differ, and the reader has no way at all to tell: the drawing is
	plausible, the labels are right, and the proportions are wrong.
	"""

	def barHeights(self, values, width=24, height=41):
		""":return: (rows above the baseline, rows below) for each bar.

		A Monarch is forty rows of pins, so the numbers below are the ones a hand would meet.
		"""
		series = [Series(chr(ord("a") + index), value) for index, value in enumerate(values)]
		drawing = barChart(newBuffer, width, height, series)
		text = rows(drawing)
		slot = width // len(values)
		baseline = next(y for y, row in enumerate(text) if all(cell == "O" for cell in row))
		found = []
		for index in range(len(values)):
			column = index * slot
			above = sum(1 for y in range(baseline) if text[y][column] == "O")
			below = sum(1 for y in range(baseline + 1, height) if text[y][column] == "O")
			found.append((above, below))
		return baseline, found

	def test_aPositiveBarFillsTheRoomAboveTheLine(self):
		"""The failure this is here for: +25 beside −75 put zero a quarter of the way down,
		correctly, and then drew the +25 bar as a third of that quarter."""
		baseline, heights = self.barHeights([25.0, -75.0])
		above, _below = heights[0]
		self.assertEqual(above, baseline)

	def test_aNegativeBarFillsTheRoomBelowIt(self):
		baseline, heights = self.barHeights([25.0, -75.0])
		_above, below = heights[1]
		self.assertEqual(below, 40 - baseline)

	def test_theOtherWayAboutIsTheSame(self):
		baseline, heights = self.barHeights([75.0, -25.0])
		self.assertEqual(heights[0][0], baseline)
		self.assertEqual(heights[1][1], 40 - baseline)

	def test_theLineSitsWhereZeroIs(self):
		"""A quarter of the way down for +25 against −75, because that is where zero falls."""
		baseline, _heights = self.barHeights([25.0, -75.0])
		self.assertEqual(baseline, 10)

	def test_barsKeepTheirRatioToEachOther(self):
		"""Which is what a finger is actually comparing."""
		_baseline, heights = self.barHeights([25.0, -75.0])
		self.assertEqual(heights[1][1], 3 * heights[0][0])

	def test_aSmallValueIsStillABarClearOfTheFloor(self):
		"""Rounding it to nothing shows as bare floor, which reads as a missing value rather
		than a small one."""
		_baseline, heights = self.barHeights([1000.0, -1.0])
		self.assertGreaterEqual(heights[1][1], 1)

	def test_everyValueZeroLeavesTheFloorAtTheBottom(self):
		"""A row of empty slots halfway up reads as a chart whose bars have been cut off; the
		same row along the floor reads as nothing to show."""
		baseline, _heights = self.barHeights([0.0, 0.0])
		self.assertEqual(baseline, 40)


class TestWritingOnTheChart(unittest.TestCase):
	"""Labels under the bars and values over them.

	Bars alone say which is larger and nothing else: a reader can feel the shape of the data
	and has to point at every bar to learn what any of it is. Written on, the chart reads in
	one pass — the bottom for the categories, the top for the numbers, the middle for the
	shape — and pointing becomes the way to ask about one bar rather than the only way to
	read the chart at all.
	"""

	def chart(self, series=None, width=96, height=35, translate=spell):
		series = series or [Series("June", 33000), Series("July", 50000)]
		return barChart(newBuffer, width, height, series, translate)

	def test_theValuesAreWrittenAcrossTheTop(self):
		drawing = self.chart()
		text = rows(drawing)
		self.assertIn("O", text[0])

	def test_theLabelsAreWrittenAcrossTheBottom(self):
		drawing = self.chart()
		text = rows(drawing)
		# A braille line is four pin rows, so the labels start four from the bottom.
		self.assertIn("O", text[-4])

	def test_aLabelStartsWhereItsBarDoes(self):
		"""Left aligned rather than centred, which is what a printed chart does and the
		wrong choice for a finger: a reader following a bar downwards hits its label where the
		bar's own left edge is.
		"""
		drawing = self.chart()
		labelRow = rows(drawing)[-4]
		slot = 96 // 2
		self.assertEqual(labelRow[0], "O")
		self.assertEqual(labelRow[slot], "O")

	def test_theBarsKeepTheMiddle(self):
		"""The text must not overlap the bars, or a tall bar and its own label become one
		shape under the finger.
		"""
		drawing = self.chart([Series("a", 10), Series("b", 10)])
		text = rows(drawing)
		# Both bars are at full height, and still leave the four rows at each end clear.
		self.assertNotIn("O" * 10, text[3])
		self.assertEqual(text[4][:10], "O" * 10)

	def test_barsStayProportionalWithTheTextRowsTakenOut(self):
		drawing = self.chart([Series("a", 5), Series("b", 10)])
		heights = columnHeights(drawing)
		# Minus the baseline they share, and minus the label row's own dot.
		self.assertAlmostEqual((heights[0] - 2) / (heights[48] - 2), 0.5, delta=0.1)

	def test_aLongLabelIsCutToTheBarsWidth(self):
		""""Wednes" is recognisably Wednesday and is worth having."""
		drawing = self.chart(
			[Series("a very long name indeed", 1), Series("b", 1)],
		)
		labelRow = rows(drawing)[-4]
		# 48 pins is a slot, which is 16 cells of three columns each.
		self.assertEqual(labelRow[:48].count("O"), 16)

	def test_aValueTooWideIsLeftOutRatherThanCut(self):
		""""330" for 33000 is a different number said with confidence. The reader can
		still get it by pointing at the bar.
		"""
		series = [Series(str(index), 12345678901234567890) for index in range(12)]
		drawing = barChart(newBuffer, 96, 35, series, spell)
		self.assertNotIn("O", rows(drawing)[0])

	def test_aShortChartIsDrawnBare(self):
		"""Below a point the labels would take so much of the chart that the bars stop
		being comparable, which is the one thing a bar chart is for.
		"""
		drawing = self.chart(height=10)
		text = rows(drawing)
		# Bare means the bars have the whole height: the tallest reaches the top row, and the
		# bottom row is the baseline rather than a line of labels.
		self.assertEqual(text[0][48:71], "O" * 23)
		self.assertTrue(all(char == "O" for char in text[-1]))

	def test_noTranslatorDrawsTheBarsBare(self):
		"""What a display with no braille table behind it gets."""
		drawing = self.chart(translate=None)
		text = rows(drawing)
		self.assertEqual(text[0][48:71], "O" * 23)
		self.assertTrue(all(char == "O" for char in text[-1]))

	def test_aTranslatorThatFailsCostsTheWritingAndNotTheChart(self):
		"""A chart with no writing on it is still a chart; one that failed to appear
		because the braille tables were between states is not.
		"""
		def broken(text):
			raise RuntimeError("no tables")

		drawing = self.chart(translate=broken)
		self.assertTrue(all(char == "O" for char in rows(drawing)[-5]))

	def test_pointingStillAnswersWithTheWritingThere(self):
		drawing = self.chart()
		self.assertIn("June", drawing.describeAt(0, 20))


class TestWhatAChartRefuses(unittest.TestCase):
	"""Every refusal says something the reader can act on, rather than drawing something else."""

	def test_nothingToChart(self):
		with self.assertRaises(ChartRefused):
			barChart(newBuffer, 20, 10, [])

	def test_moreBarsThanTheDisplayHolds(self):
		"""Charting the first twenty of sixty would be a different chart, silently."""
		series = [Series(str(index), index) for index in range(60)]
		with self.assertRaises(ChartRefused) as caught:
			barChart(newBuffer, 20, 10, series)
		self.assertIn("60", str(caught.exception))
		self.assertIn("10", str(caught.exception))

	def test_noRoomAtAll(self):
		with self.assertRaises(ChartRefused):
			barChart(newBuffer, 1, 10, [Series("a", 1)])

	def test_aDisplayThatWillNotProvideABuffer(self):
		with self.assertRaises(ChartRefused):
			barChart(lambda width, height: None, 20, 10, [Series("a", 1)])


class TestAValueThatIsNotANumber(unittest.TestCase):
	"""A cell holding an error, an overflow or a division by zero.

	It used to reach the drawing and raise there — "cannot convert float NaN to integer" — with
	nothing left to say which cell it came from. A refusal names the problem where the reader
	can act on it.
	"""

	def test_aBarChartRefusesRatherThanCrashing(self):
		"""Refused rather than skipped: a bar carries its own label, so leaving one out would
		move every label after it and quietly rename the bars."""
		with self.assertRaises(ChartRefused):
			barChart(newBuffer, 24, 10, [Series("a", 1.0), Series("b", float("nan"))])

	def test_infinityIsRefusedTheSameWay(self):
		with self.assertRaises(ChartRefused):
			barChart(newBuffer, 24, 10, [Series("a", 1.0), Series("b", float("inf"))])

	def test_aSheetCellHoldingOneIsNotReadAsANumber(self):
		self.assertIsNone(chartSource._asNumber(float("nan")))
		self.assertIsNone(chartSource._asNumber(float("-inf")))


class TestPointingAtABar(unittest.TestCase):
	"""The payoff. Without this a reader learns that one bar is taller than another and never
	learns what either of them is."""

	def setUp(self):
		self.drawing = barChart(
			newBuffer,
			30,
			11,
			[Series("Monday", 5), Series("Tuesday", 10), Series("Wednesday", 2.5)],
		)

	def test_aBarNamesItselfAndItsValue(self):
		said = self.drawing.describeAt(0, 5)
		self.assertIn("Monday", said)
		self.assertIn("5", said)

	def test_eachBarAnswersForItsOwnColumns(self):
		self.assertIn("Monday", self.drawing.describeAt(0, 5))
		self.assertIn("Tuesday", self.drawing.describeAt(10, 5))
		self.assertIn("Wednesday", self.drawing.describeAt(20, 5))

	def test_theSpaceAboveAShortBarStillBelongsToIt(self):
		"""Which is why the drawing is asked before the nearest raised dot is looked for: the
		answer for empty space above a bar is that bar, not some dot on the next one."""
		self.assertIn("Wednesday", self.drawing.describeAt(20, 0))

	def test_awholeNumberIsNotSaidWithADecimalPart(self):
		"""A count of five read as "5.0" is five said badly."""
		self.assertIn("5", self.drawing.describeAt(0, 5))
		self.assertNotIn("5.0", self.drawing.describeAt(0, 5))

	def test_aFractionKeepsIt(self):
		self.assertIn("2.5", self.drawing.describeAt(20, 5))

	def test_theBaselineNamesItself(self):
		said = self.drawing.describeAt(29, 10)
		self.assertIsNotNone(said)

	def test_theChartIsNamedByHowManyBarsItHas(self):
		self.assertIn("3", self.drawing.name)


class TestReadingASelection(unittest.TestCase):
	"""Picking labels and numbers out of what a grid says is selected.

	No Excel here, and that is the point of the layering: `chartSource` asks the focused object
	for its grid through `flowObjectTable.SHEET` and asks that grid what is selected, so these
	rules can be tested with a list of tuples. The Excel half is `ExcelSheet.selectedValues`,
	beside the reading code that already talks to that object model.

	A grid answers rows of `(text, value)` — what the cell displays, and what it stores. Keeping
	both is what lets a bar be as tall as 0.25 and be called "25%".
	"""

	def test_twoColumnsAreLabelsAndNumbers(self):
		"""What a reader means by selecting both."""
		result = chartSource.seriesFromGrid(
			[[("Monday", "Monday"), ("5", 5.0)], [("Tuesday", "Tuesday"), ("10", 10.0)]],
		)
		self.assertEqual(result, [Series("Monday", 5.0), Series("Tuesday", 10.0)])

	def test_oneColumnIsNumbersCalledByWhatTheySay(self):
		"""A bar called by its value is at least true; a bar called by its position is one more
		thing for the reader to hold in mind."""
		result = chartSource.seriesFromGrid([[("5", 5.0)], [("10", 10.0)]])
		self.assertEqual([entry.label for entry in result], ["5", "10"])
		self.assertEqual([entry.value for entry in result], [5.0, 10.0])

	def test_theRightmostNumericColumnIsCharted(self):
		"""Where a total sits in a sheet laid out the ordinary way."""
		result = chartSource.seriesFromGrid(
			[[("a", "a"), ("1", 1.0), ("10", 10.0)], [("b", "b"), ("2", 2.0), ("20", 20.0)]],
		)
		self.assertEqual([entry.value for entry in result], [10.0, 20.0])
		self.assertEqual([entry.label for entry in result], ["1", "2"])

	def test_storedValuesSizeTheBarsAndDisplayedTextNamesThem(self):
		"""The whole reason a grid answers both. A percentage shown as 25% is stored as 0.25:
		charting the text would put the bars in the wrong proportion, and labelling with the
		value would tell the reader something they never see in the sheet."""
		result = chartSource.seriesFromGrid(
			[[("share", "share"), ("25%", 0.25)], [("rest", "rest"), ("75%", 0.75)]],
		)
		self.assertEqual([entry.value for entry in result], [0.25, 0.75])
		self.assertEqual([entry.label for entry in result], ["share", "rest"])

	def test_textInTheNumberColumnRefusesRatherThanCountingAsZero(self):
		with self.assertRaises(chartSource.NoNumbers):
			chartSource.seriesFromGrid([[("a", "a"), ("x", "x")], [("b", "b"), ("y", "y")]])

	def test_flagsAreNotNumbers(self):
		"""Excel stores TRUE as -1 and Python calls a bool an int, so a column of flags would
		chart as bars of equal height and look like data."""
		with self.assertRaises(chartSource.NoNumbers):
			chartSource.seriesFromGrid([[("TRUE", True)], [("FALSE", False)]])

	def test_aSingleCellIsAOneBarChart(self):
		result = chartSource.seriesFromGrid([[("42", 42.0)]])
		self.assertEqual([entry.value for entry in result], [42.0])

	def test_shortRowsDoNotBreakTheColumnSearch(self):
		"""A ragged grid is a grid that could not be fully read, not a reason to raise."""
		with self.assertRaises(chartSource.NoNumbers):
			chartSource.seriesFromGrid([[("a", "a")], []])

	def test_averyLargeSelectionIsCutRatherThanCharted(self):
		"""A stray Ctrl+Space selects a million cells, and turning them all into tuples would
		stop NVDA long before the chart refused them."""
		grid = [[(str(index), float(index))] for index in range(500)]
		self.assertEqual(len(chartSource.seriesFromGrid(grid)), chartSource.MAX_BARS)


class TestTheSeamIsTheOnlyWayIn(unittest.TestCase):
	"""Nothing outside `appModules/excel.py` may know that Excel exists.

	The rule the flow already keeps, restated for charts because charts were the first thing
	tempted to break it. Reaching into `cell.excelCellObject` from the plugin works, and puts
	one application's object model in the part of the add-on that is always loaded: a second
	application then means a second special case there, and a fault in Excel handling can stop
	the graphics commands importing at all.

	Asserted against the parsed source rather than against its text, because both modules
	discuss Excel at length in the course of explaining that they do not touch it — and a test
	that reads prose would either fail on the explanation or pass on the violation.
	"""

	def parsed(self, *parts):
		""":return: one of the add-on's files as a syntax tree."""
		path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "addon", *parts)
		with open(path, encoding="utf-8") as handle:
			return ast.parse(handle.read())

	def importedNames(self, tree):
		""":return: every module named by an import in a tree."""
		names = []
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				names.extend(alias.name for alias in node.names)
			elif isinstance(node, ast.ImportFrom) and node.module:
				names.append(node.module)
		return names

	def attributeNames(self, tree):
		""":return: every attribute name read anywhere in a tree."""
		return {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

	def test_chartSourceImportsNoApplicationModule(self):
		names = self.importedNames(self.parsed("globalPlugins", "brlMultiline", "chartSource.py"))
		self.assertTrue(names)
		self.assertFalse([name for name in names if name.startswith("appModules")])

	def test_chartSourceTouchesNoApplicationObjectModel(self):
		attributes = self.attributeNames(self.parsed("globalPlugins", "brlMultiline", "chartSource.py"))
		self.assertNotIn("excelCellObject", attributes)
		self.assertIn("selectedValues", attributes)

	def test_theChartDrawingImportsNothingOfNVDAsAtAll(self):
		"""It takes labels, numbers and a buffer factory. That is the whole of its world."""
		names = self.importedNames(self.parsed("globalPlugins", "brlMultiline", "chart.py"))
		self.assertFalse([name for name in names if name.startswith("appModules")])
		self.assertNotIn("excelCellObject", self.attributeNames(self.parsed("globalPlugins", "brlMultiline", "chart.py")))

	def test_noChartDrawingImportsAnythingOfNVDAsAtAll(self):
		"""The rule holds for every chart type, not only the first one. Each of them takes
		numbers and a buffer factory, and that is the whole of its world — which is what
		lets a chart be drawn dot by dot in a test with no display and no spreadsheet.
		"""
		for module in ("chartDraw.py", "chartLine.py", "chartPrice.py", "chartMenu.py"):
			tree = self.parsed("globalPlugins", "brlMultiline", module)
			names = self.importedNames(tree)
			self.assertFalse(
				[name for name in names if name.startswith("appModules")],
				module,
			)
			self.assertNotIn("excelCellObject", self.attributeNames(tree), module)

	def test_theExcelModuleIsWhereTheObjectModelIsRead(self):
		"""The other half of the rule: the knowledge has to live somewhere, and this is where."""
		tree = self.parsed("appModules", "excel.py")
		functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
		self.assertIn("selectedValues", functions)
		attributes = self.attributeNames(tree)
		self.assertIn("excelCellObject", attributes)
		self.assertIn("Value2", attributes)


if __name__ == "__main__":
	unittest.main()
