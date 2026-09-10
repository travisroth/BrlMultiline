# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for line charts of several series at once.

The case this module exists for is a closing price with a moving average through it and a pair
of bands around it, and every decision worth testing comes from that: the series share one
scale because a band on its own scale is not a band, they are told apart by texture because
there is no colour and no line weight to spend, and a missing value is a gap because a moving
average has nineteen of them before it starts.

That last one is the fault this file is most for. A column with empty cells at the top read as
zeroes draws an average that dives to the floor and climbs back out, which looks exactly like
a crash and is entirely convincing — the kind of wrong a chart can be while looking right.
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

from brlMultiline.chartDraw import TEXT_ROWS, ChartRefused  # noqa: E402
from brlMultiline.chartLine import MAX_LINES, Line, lineChart, patternName  # noqa: E402

WIDTH = 96
HEIGHT = 35
PLOT_TOP = TEXT_ROWS
PLOT_BOTTOM = HEIGHT - 1 - TEXT_ROWS


def newBuffer(width, height):
	""":return: a buffer, standing in for the display's own factory."""
	return PinBuffer(width, height)


def spell(text):
	""":return: one cell per character, dot 1 raised."""
	return [0b00000001 for _ in text]


def rows(drawing):
	""":return: the drawing as text, raised dots as O."""
	return drawing.buffer.rows()


def chart(lines, labels=None, width=WIDTH, height=HEIGHT, translate=spell):
	""":return: a line chart of the given series at panel size."""
	return lineChart(newBuffer, width, height, lines, labels, translate)


class TestTheLines(unittest.TestCase):
	def test_aChartFillsTheRectangleItWasGiven(self):
		drawing = chart([Line("a", [1, 2, 3])])
		self.assertEqual((drawing.width, drawing.height), (WIDTH, HEIGHT))

	def test_theLineStartsAtTheLeftEdgeAndEndsAtTheRight(self):
		"""So that the dates written under the two ends are under the ends of the line, and the
		reader sweeping to the edge of the panel has reached the edge of the data."""
		drawing = chart([Line("a", [1, 2, 3])])
		text = rows(drawing)
		self.assertTrue(any(row[0] == "O" for row in text))
		self.assertTrue(any(row[WIDTH - 1] == "O" for row in text))

	def test_aRisingSeriesClimbs(self):
		drawing = chart([Line("a", [1, 2, 3, 4])])
		plot = rows(drawing)[PLOT_TOP : PLOT_BOTTOM + 1]
		firstDot = next(index for index, row in enumerate(plot) if row[0] == "O")
		lastDot = next(index for index, row in enumerate(plot) if row[WIDTH - 1] == "O")
		self.assertGreater(firstDot, lastDot)

	def test_theHighestValueReachesTheTopOfThePlot(self):
		drawing = chart([Line("a", [1, 5])])
		self.assertIn("O", rows(drawing)[PLOT_TOP])

	def test_everySeriesIsDrawnAgainstTheSameScale(self):
		"""The whole reason they are drawn together. A band that is not on the same scale as
		the price it bands is not a band, and four series each fitted to their own range would
		draw four lines that all fill the panel and mean nothing against each other."""
		drawing = chart([Line("high", [100] * 8), Line("low", [0] * 8)])
		text = rows(drawing)
		self.assertEqual(text[PLOT_TOP].count("O"), WIDTH)
		self.assertIn("O", text[PLOT_BOTTOM])

	def test_theSeriesAreToldApartByTexture(self):
		"""There is no colour and no line width at one pin, so each series gets a pattern along
		its own path. Solid first, because the first series is the one the reader came for."""
		drawing = chart([Line("a", [100] * 8), Line("b", [50] * 8), Line("c", [0] * 8)])
		text = rows(drawing)
		solid = text[PLOT_TOP].count("O")
		dashed = max(row.count("O") for row in text[PLOT_TOP + 1 : PLOT_BOTTOM])
		dotted = text[PLOT_BOTTOM].count("O")
		self.assertEqual(solid, WIDTH)
		self.assertLess(dashed, solid)
		self.assertLess(dotted, dashed)

	def test_aDashedLineIsStillDashedWherePointsAreClose(self):
		"""The pattern runs along the path and its phase carries across the joins between
		points. Restarted at each point, a chart with points three pins apart would draw the
		same first dots of the pattern over and over and every series would look solid."""
		drawing = chart([Line("a", [100] * 40), Line("b", [0] * 40)])
		self.assertLess(rows(drawing)[PLOT_BOTTOM].count("O"), WIDTH)

	def test_everySeriesIsNamedWithTheTextureItWasDrawnIn(self):
		"""The one thing the reader cannot get from the drawing, and needs before the drawing
		means anything. Spoken on entry, because a key drawn on the chart would cost a quarter
		of the panel to say what one sentence says once."""
		drawing = chart([Line("Close", [1, 2]), Line("MA20", [1, 2])])
		self.assertIn("Close solid", drawing.name)
		self.assertIn("MA20 dashed", drawing.name)
		self.assertIn("2 points", drawing.name)

	def test_thePatternsAreNamedInOrder(self):
		self.assertEqual(
			[patternName(index) for index in range(MAX_LINES)],
			["solid", "dashed", "dotted", "dash dot"],
		)


class TestAGapInTheNumbers(unittest.TestCase):
	"""A twenty day moving average has nineteen empty cells above it.

	Read as zeroes those draw the average diving to the floor and climbing back, which looks
	exactly like a crash. So a run of missing values breaks the line, and the reader feels the
	average start where the data starts.
	"""

	def test_theLineStartsWhereTheNumbersDo(self):
		drawing = chart([Line("close", [10] * 8), Line("average", [None] * 4 + [5] * 4)])
		text = rows(drawing)
		# The lower line is only drawn across the right hand half.
		self.assertNotIn("O", "".join(row[:20] for row in text[PLOT_BOTTOM - 1 : PLOT_BOTTOM + 1]))
		self.assertIn("O", text[PLOT_BOTTOM])

	def test_aGapInTheMiddleBreaksTheLineRatherThanCrossingIt(self):
		"""Joined across, the chart would draw a straight run through days it has no numbers
		for and the reader would read it as data."""
		drawing = chart([Line("a", [10, 10, None, None, 10, 10])])
		text = rows(drawing)
		middle = [row[38:58] for row in text[PLOT_TOP:PLOT_BOTTOM]]
		self.assertNotIn("O", "".join(middle))

	def test_aSingleValueBetweenGapsIsStillDrawn(self):
		"""One measurement is still a measurement, and a chart that drew nothing for it would
		have a hole in it that says nothing."""
		drawing = chart([Line("a", [None, 5, None]), Line("b", [1, 1, 1])])
		self.assertIn("O", "".join(row[44:52] for row in rows(drawing)))

	def test_aSeriesWithNoNumbersAtAllIsLeftOut(self):
		"""An empty column selected alongside full ones is a column the reader did not mean, and
		it must not use up one of the four textures."""
		drawing = chart([Line("a", [1, 2]), Line("empty", [None, None])])
		self.assertNotIn("empty", drawing.name)


class TestWhatALineChartRefuses(unittest.TestCase):
	def test_nothingToChart(self):
		with self.assertRaises(ChartRefused):
			chart([])

	def test_aSinglePointIsNotALine(self):
		with self.assertRaises(ChartRefused) as caught:
			chart([Line("a", [1])])
		self.assertIn("two points", str(caught.exception))

	def test_moreSeriesThanCanBeToldApart(self):
		"""Not an arbitrary limit: a fifth series would have to repeat a texture, and two lines
		with the same texture crossing each other is a chart that lies."""
		lines = [Line(str(index), [1, 2]) for index in range(MAX_LINES + 1)]
		with self.assertRaises(ChartRefused) as caught:
			chart(lines)
		self.assertIn(str(MAX_LINES), str(caught.exception))

	def test_noRoomAtAll(self):
		with self.assertRaises(ChartRefused):
			chart([Line("a", [1, 2])], width=4, height=35)

	def test_aDisplayThatWillNotProvideABuffer(self):
		with self.assertRaises(ChartRefused):
			lineChart(lambda width, height: None, WIDTH, HEIGHT, [Line("a", [1, 2])])


class TestWritingOnTheChart(unittest.TestCase):
	def test_theRangeIsWrittenAcrossTheTop(self):
		drawing = chart([Line("a", [5, 40])])
		self.assertIn("O", rows(drawing)[0])

	def test_theFirstAndLastLabelsAreWrittenAcrossTheBottom(self):
		drawing = chart([Line("a", [5, 40])], labels=["Jan", "Dec"])
		labelRow = rows(drawing)[-TEXT_ROWS]
		self.assertIn("O", labelRow[:12])
		self.assertIn("O", labelRow[80:])

	def test_aShortChartIsDrawnBare(self):
		"""Below a point the writing would take so much of the chart that its shape stops
		being readable, so the plot takes the whole rectangle and the reader gets the
		numbers by pointing at it instead.
		"""
		drawing = chart([Line("a", [5, 40])], height=10)
		text = rows(drawing)
		self.assertIn("O", text[0])
		self.assertIn("O", text[9])

	def test_theLinesKeepTheMiddle(self):
		"""The writing must not overlap the plot, or a line and the number above it become one
		shape under the finger."""
		drawing = chart([Line("a", [5, 40])], labels=["Jan", "Dec"])
		text = rows(drawing)
		self.assertNotIn("O", "".join(text[1:PLOT_TOP]))


class TestPointingAtALineChart(unittest.TestCase):
	def setUp(self):
		# Close falls from the top of the plot to its middle; the average is flat along the
		# bottom. Every press below is far from one line and on the other, which is what
		# makes the answers unambiguous rather than a matter of a row either way.
		self.drawing = chart(
			[Line("Close", [30, 25, 20]), Line("MA", [10, 10, 10])],
			labels=["Mon", "Tue", "Wed"],
		)

	def test_aLineNamesItselfItsValueAndThePoint(self):
		answer = self.drawing.describeAt(0, PLOT_TOP)
		self.assertIn("Close", answer)
		self.assertIn("30", answer)
		self.assertIn("Mon", answer)

	def test_theNearerLineAnswers(self):
		"""Two lines apart, and the press belongs to the one under the finger."""
		answer = self.drawing.describeAt(0, PLOT_BOTTOM)
		self.assertIn("MA", answer)
		self.assertIn("10", answer)

	def test_aPressAwayFromEveryLineReadsTheHeightBackAsAValue(self):
		"""The question a chart on paper cannot be asked: what is my finger worth. It is how a
		reader finds out whether a line is near a level without following the line to get
		there."""
		answer = self.drawing.describeAt(0, (PLOT_TOP + PLOT_BOTTOM) // 2)
		self.assertIn("Mon", answer)
		self.assertNotIn("Close", answer)

	def test_aPressBetweenPointsBelongsToTheNearestOne(self):
		answer = self.drawing.describeAt(WIDTH // 2 + 2, PLOT_BOTTOM)
		self.assertIn("Tue", answer)

	def test_pointsAreCalledByTheirPositionWhereNothingNamesThem(self):
		"""A bare column of numbers with nothing beside it. Counting from one, because a reader
		asked to count from zero is being asked to do arithmetic to read a chart."""
		drawing = chart([Line("a", [1, 2, 3])])
		self.assertIn("1", drawing.describeAt(0, PLOT_BOTTOM))

	def test_aSeriesWithNoValueThereDoesNotAnswerForIt(self):
		drawing = chart([Line("a", [None, 5, 5]), Line("b", [1, 1, 1])], labels=["x", "y", "z"])
		answer = drawing.describeAt(0, PLOT_TOP)
		self.assertNotIn("a,", answer)
