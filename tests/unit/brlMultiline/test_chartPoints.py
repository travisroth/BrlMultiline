# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for what a chart says about its points, and for the guides drawn at a marked one.

Stepping through a chart rests on each chart describing its own points: which point a column
belongs to, where a point was drawn, and what it is. Three charts each answer those questions
with their own geometry, and the tests hold every answer against the others: the point a
column belongs to is the point drawn there, the words a step says are the words a press says,
and a window of a chart knows which point of the whole it starts at. Any of those disagreeing
would mark one bar and read out another, which is a chart that lies plausibly.

The guides are tested dot by dot, because the one thing they must never do is change what the
chart says: a bar exactly as tall as the marked one keeps its top, a data line is never broken,
and nothing is drawn over the writing.
"""

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

from brlMultiline.chart import Series, barChart  # noqa: E402
from brlMultiline.chartDraw import GUIDE_SPACING, GUIDE_TICK, PointMark, drawGuides, windowOf  # noqa: E402
from brlMultiline.chartLine import Line, lineChart  # noqa: E402
from brlMultiline.chartPrice import Period, candleChart, ohlcChart  # noqa: E402

WIDTH = 96
HEIGHT = 35


def newBuffer(width, height):
	return PinBuffer(width, height)


def spell(text):
	""":return: one cell per character, so writing is drawn and its width is predictable."""
	return [1] * len(text)


def bars(values):
	return [Series(f"b{index}", value) for index, value in enumerate(values)]


def periods(count):
	return [
		Period(f"d{index}", open=10 + index % 3, high=14 + index % 5, low=8 - index % 2, close=11 + index % 4)
		for index in range(count)
	]


class TestABarChartsPoints(unittest.TestCase):
	def setUp(self):
		self.values = [3, 7, 0, 5, 9, 2]
		self.drawing = barChart(newBuffer, WIDTH, HEIGHT, bars(self.values), spell)

	def test_everyColumnOfABarBelongsToThatBar(self):
		for index in range(len(self.values)):
			left, right = self.drawing.markFor(index).own
			for x in range(left, right):
				self.assertEqual(self.drawing.pointAt(x), index)

	def test_theGapAfterABarIsThatBars(self):
		"""A press beside a bar is a press on it, not on nothing."""
		left, right = self.drawing.markFor(1).own
		self.assertEqual(self.drawing.pointAt(right), 1)

	def test_pastTheLastBarIsNoBar(self):
		slot = WIDTH // len(self.values)
		self.assertIsNone(self.drawing.pointAt(slot * len(self.values)))

	def test_aStepSaysWhatAPressSays(self):
		for index in range(len(self.values)):
			left, _right = self.drawing.markFor(index).own
			self.assertEqual(self.drawing.sayPoint(index), self.drawing.describeAt(left, HEIGHT // 2))

	def test_theLevelIsAtTheTipOfTheBar(self):
		mark = self.drawing.markFor(4)
		left, _right = mark.own
		self.assertTrue(self.drawing.buffer.getDot(left, mark.row))
		self.assertFalse(self.drawing.buffer.getDot(left, mark.row - 1))
		self.assertEqual(mark.value, 9)

	def test_thePointLineIsInTheGapToTheLeft(self):
		mark = self.drawing.markFor(3)
		self.assertEqual(mark.column, mark.own[0] - 1)
		self.assertFalse(
			any(self.drawing.buffer.getDot(mark.column, y) for y in range(mark.plotTop, mark.plotBottom))
		)

	def test_theFirstBarTakesTheGapToItsRight(self):
		mark = self.drawing.markFor(0)
		self.assertEqual(mark.column, mark.own[1])

	def test_aBarOfZeroDoesNotCutTheFloor(self):
		self.assertFalse(self.drawing.markFor(2).cuts)
		self.assertTrue(self.drawing.markFor(1).cuts)

	def test_theWholeChartStartsAtTheFirstPoint(self):
		self.assertEqual(self.drawing.firstPoint, 0)

	def test_aWindowKnowsWhereItStarts(self):
		values = list(range(1, 41))
		drawing = barChart(newBuffer, WIDTH, HEIGHT, bars(values), spell)
		window = drawing.redraw(0.5, 0.25, WIDTH, HEIGHT)
		first, _last = windowOf(len(values), 0.5, 0.25, 1)
		self.assertEqual(window.firstPoint, first)
		self.assertEqual(window.sayPoint(0), f"b{first}, {values[first]}")


class TestALineChartsPoints(unittest.TestCase):
	def setUp(self):
		self.lines = [
			Line("Close", [5, 6, 7, 8, 9, 8, 7, 6, 5, 4]),
			Line("Average", [None, None, 6, 7, 8, 8, 7, 6, 6, 5]),
		]
		self.labels = [f"d{index}" for index in range(10)]
		self.drawing = lineChart(newBuffer, WIDTH, HEIGHT, self.lines, self.labels, spell)

	def test_thePointDrawnInAColumnIsThePointThere(self):
		for index in range(10):
			self.assertEqual(self.drawing.pointAt(self.drawing.markFor(index).column), index)

	def test_offTheDrawingIsNoPoint(self):
		self.assertIsNone(self.drawing.pointAt(WIDTH))
		self.assertIsNone(self.drawing.pointAt(-1))

	def test_aStepReadsEveryLineShown(self):
		self.assertEqual(self.drawing.sayPoint(3), "Close 8, Average 7, d3")

	def test_aLineWithNothingThereSaysSo(self):
		self.assertEqual(self.drawing.sayPoint(0), "Close 5, Average no value, d0")

	def test_aStepReadsOnlyTheLinesShown(self):
		drawing = lineChart(newBuffer, WIDTH, HEIGHT, self.lines, self.labels, spell, shown=(1,))
		self.assertEqual(drawing.sayPoint(3), "Average 7, d3")
		self.assertEqual(drawing.levelNames, ("Average",))

	def test_theLevelFollowsTheFirstLineByDefault(self):
		mark = self.drawing.markFor(4)
		self.assertEqual((mark.name, mark.value), ("Close", 9))
		self.assertTrue(self.drawing.buffer.getDot(mark.column, mark.row))

	def test_theLevelCanFollowAnotherLine(self):
		mark = self.drawing.markFor(4, 1)
		self.assertEqual((mark.name, mark.value), ("Average", 8))

	def test_aGapHasNoLevel(self):
		mark = self.drawing.markFor(1, 1)
		self.assertIsNone(mark.row)
		self.assertIsNone(mark.value)

	def test_aLineIsNeverCut(self):
		self.assertFalse(self.drawing.markFor(4).cuts)

	def test_theLevelNamesAreTheLinesShown(self):
		self.assertEqual(self.drawing.levelNames, ("Close", "Average"))

	def test_aWindowKnowsWhereItStarts(self):
		count = 250
		labels = [f"d{index}" for index in range(count)]
		drawing = lineChart(
			newBuffer, WIDTH, HEIGHT, [Line("Close", [index % 9 for index in range(count)])], labels, spell
		)
		window = drawing.redraw(0.4, 0.3, WIDTH, HEIGHT)
		self.assertTrue(window.sayPoint(0).endswith(f", d{window.firstPoint}"))
		self.assertTrue(
			window.sayPoint(window.points - 1).endswith(f", d{window.firstPoint + window.points - 1}")
		)
		self.assertGreater(window.firstPoint, 0)


class TestAPriceChartsPoints(unittest.TestCase):
	def setUp(self):
		self.periods = periods(12)

	def charts(self):
		return (
			ohlcChart(newBuffer, WIDTH, HEIGHT, self.periods, spell),
			candleChart(newBuffer, WIDTH, HEIGHT, self.periods, spell),
		)

	def test_thePointDrawnInAColumnIsThePointThere(self):
		for drawing in self.charts():
			for index in range(len(self.periods)):
				self.assertEqual(drawing.pointAt(drawing.markFor(index).column), index)

	def test_aStepSaysWhatAPressSays(self):
		for drawing in self.charts():
			for index in range(len(self.periods)):
				column = drawing.markFor(index).column
				self.assertEqual(drawing.sayPoint(index), drawing.describeAt(column, HEIGHT // 2))

	def test_theLevelIsAtTheClose(self):
		drawing = ohlcChart(newBuffer, WIDTH, HEIGHT, self.periods, spell)
		mark = drawing.markFor(5)
		self.assertEqual(mark.value, self.periods[5].close)
		self.assertFalse(mark.cuts)

	def test_aWindowKnowsWhereItStarts(self):
		many = periods(24)
		drawing = ohlcChart(newBuffer, WIDTH, HEIGHT, many, spell)
		window = drawing.redraw(0.5, 0.25, WIDTH, HEIGHT)
		first, _last = windowOf(len(many), 0.5, 0.25, 1)
		self.assertEqual(window.firstPoint, first)
		self.assertTrue(window.sayPoint(0).startswith(f"d{first},"))


class TestTheGuides(unittest.TestCase):
	def test_aLevelOverEmptyPinsIsSparseWithSolidEnds(self):
		buffer = PinBuffer(20, 10)
		drawGuides(buffer, PointMark(column=-1, row=5, plotTop=0, plotBottom=9))
		raised = [x for x in range(20) if buffer.getDot(x, 5)]
		expected = sorted({x for x in range(20) if x % GUIDE_SPACING == 0} | {0, 1, 18, 19})
		self.assertEqual(raised, expected)

	def test_aTallerBarIsNotchedAndAnEqualOneIsNot(self):
		buffer = PinBuffer(20, 10)
		buffer.rect(2, 2, 2, 8, filled=True)
		"""Taller: rows 2 to 9."""
		buffer.rect(6, 5, 2, 5, filled=True)
		"""As tall as the marked bar: rows 5 to 9."""
		buffer.rect(10, 5, 2, 5, filled=True)
		"""The marked bar."""
		drawGuides(buffer, PointMark(column=9, row=5, plotTop=0, plotBottom=9, cuts=True, own=(10, 12)))
		self.assertFalse(buffer.getDot(2, 5))
		self.assertFalse(buffer.getDot(3, 5))
		self.assertTrue(buffer.getDot(6, 5))
		self.assertTrue(buffer.getDot(10, 5))
		self.assertTrue(buffer.getDot(11, 5))

	def test_withoutCuttingNothingIsLowered(self):
		buffer = PinBuffer(20, 10)
		buffer.rect(2, 2, 2, 8, filled=True)
		before = buffer.rows()
		drawGuides(buffer, PointMark(column=-1, row=5, plotTop=0, plotBottom=9))
		after = buffer.rows()
		for y in range(10):
			for x in range(20):
				if before[y][x] == "O":
					self.assertEqual(after[y][x], "O")

	def test_thePointLineIsSparseWithSolidEnds(self):
		buffer = PinBuffer(10, 20)
		drawGuides(buffer, PointMark(column=4, row=None, plotTop=3, plotBottom=16))
		raised = [y for y in range(20) if buffer.getDot(4, y)]
		expected = sorted(
			{y for y in range(3, 17) if (y - 3) % GUIDE_SPACING == 0}
			| set(range(3, 3 + GUIDE_TICK))
			| set(range(17 - GUIDE_TICK, 17))
		)
		self.assertEqual(raised, expected)

	def test_nothingIsDrawnOverTheWriting(self):
		buffer = PinBuffer(20, 20)
		drawGuides(buffer, PointMark(column=4, row=2, plotTop=4, plotBottom=15))
		for y in list(range(4)) + list(range(16, 20)):
			self.assertFalse(any(buffer.getDot(x, y) for x in range(20)), y)

	def test_noLevelDrawsOnlyThePointLine(self):
		buffer = PinBuffer(20, 10)
		drawGuides(buffer, PointMark(column=7, row=None, plotTop=0, plotBottom=9))
		self.assertEqual({x for x in range(20) for y in range(10) if buffer.getDot(x, y)}, {7})

	def test_onARealBarChartTheMarkedBarIsFeltWhole(self):
		values = [3, 9, 5, 9]
		drawing = barChart(newBuffer, WIDTH, HEIGHT, bars(values), spell)
		mark = drawing.markFor(2)
		buffer = PinBuffer(WIDTH, HEIGHT)
		buffer.blit(drawing.buffer)
		drawGuides(buffer, mark)
		left, right = mark.own
		for x in range(left, right):
			for y in range(mark.row, mark.plotBottom + 1):
				self.assertEqual(buffer.getDot(x, y), drawing.buffer.getDot(x, y))
		tall = drawing.markFor(1).own[0]
		self.assertTrue(drawing.buffer.getDot(tall, mark.row))
		self.assertFalse(buffer.getDot(tall, mark.row))


if __name__ == "__main__":
	unittest.main()
