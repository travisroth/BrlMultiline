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
from brlMultiline.chartLine import MAX_LINES, THICK_FROM, Line, lineChart, patternName, viewsFor  # noqa: E402

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


class TestAValueThatIsNotANumber(unittest.TestCase):
	"""A gap, which is what it is: a point the source could not give.

	Drawn as a number an error cell would read as a price, and a reader following a line has no
	way to tell one from the other. A gap breaks the line and reads as a break.
	"""

	def test_itBecomesAGapRatherThanAPoint(self):
		drawing = lineChart(newBuffer, 24, 12, [Line("close", [1.0, float("nan"), 3.0])])
		self.assertIsNotNone(drawing)

	def test_aSeriesOfNothingButThoseIsNotASeries(self):
		with self.assertRaises(ChartRefused):
			lineChart(newBuffer, 24, 12, [Line("close", [float("nan"), float("inf")])])


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
		# Two rows down, past the second pin of the first line, which is thick with three series.
		dashed = max(row.count("O") for row in text[PLOT_TOP + 2 : PLOT_BOTTOM])
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


def labelled(count):
	""":return: labels d0, d1 and so on, so a name says exactly which points a window holds."""
	return [f"d{index}" for index in range(count)]


class TestTheFirstLineOnABusyChart(unittest.TestCase):
	"""With three or four textures crossing, the line the reader came for is the one they lose.

	Weight is read before texture, so the first line is drawn two pins thick from `THICK_FROM`
	series on. Two series are left one pin each, since a solid line beside a dashed one is still
	easy to follow.
	"""

	def middleRow(self, text):
		""":return: the first row a flat line at 50 of 0 to 100 is drawn in."""
		return next(index for index in range(PLOT_TOP + 1, PLOT_BOTTOM) if text[index].count("O") == WIDTH)

	def test_itIsTwoPinsThickWithThreeSeries(self):
		self.assertEqual(THICK_FROM, 3)
		drawing = chart([Line("a", [50] * 8), Line("b", [100] * 8), Line("c", [0] * 8)])
		text = rows(drawing)
		row = self.middleRow(text)
		self.assertEqual(text[row + 1].count("O"), WIDTH)

	def test_itIsOnePinWithTwo(self):
		drawing = chart([Line("a", [100] * 8), Line("b", [0] * 8)])
		text = rows(drawing)
		self.assertEqual(text[PLOT_TOP].count("O"), WIDTH)
		self.assertNotIn("O", text[PLOT_TOP + 1])

	def test_aSteepStretchIsThickenedBesideRatherThanBelow(self):
		"""Below each dot of a near vertical line is the next dot of the same line, so a second
		pin there would add nothing and the steep stretches would stay one pin wide."""
		drawing = chart(
			[Line("a", [0, 100]), Line("b", [0, 0]), Line("c", [0, 0])],
			width=10,
			translate=None,
		)
		text = rows(drawing)
		for row in text[4:-4]:
			self.assertIn("OO", row)

	def test_theSecondPinStaysInsideThePlot(self):
		"""Along the floor of the plot the second pin goes above, not into the dates written
		under it."""
		drawing = chart([Line("a", [0] * 8), Line("b", [100] * 8), Line("c", [50] * 8)])
		self.assertEqual(rows(drawing)[PLOT_BOTTOM - 1].count("O"), WIDTH)

	def test_theKeySaysItIsThick(self):
		drawing = chart([Line("Close", [1, 2]), Line("MA", [1, 2]), Line("Low", [1, 2])])
		self.assertIn("Close thick solid", drawing.name)
		self.assertIn("MA dashed", drawing.name)


class TestAZoomedWindowIsSpacedEvenly(unittest.TestCase):
	"""Forty points across ninety-six pins are 2.4 pins apart, which rounds to gaps of 2, 3, 2,
	2, 3 -- a wobble in the line that is not in the data. A zoomed window is widened or narrowed
	until every point is the same whole number of pins from the next."""

	def setUp(self):
		self.count = 250
		self.labels = labelled(self.count)
		values = [(index % 2) * 10 for index in range(self.count)]
		self.drawing = chart([Line("zigzag", values)], labels=self.labels)

	def window(self, first, take):
		""":return: the chart redrawn for a window of points, asked for the way the mode asks."""
		return self.drawing.redraw(first / self.count, take / self.count, WIDTH, HEIGHT)

	def test_aWindowAsWideAsThePanelIsOnePinPerPoint(self):
		window = self.window(100, 96)
		self.assertIn("96 points", window.name)
		self.assertIn("1 pin per point", window.name)

	def test_aWindowNearlyAsWideAsThePanelIsOnePinPerPointToo(self):
		"""The mode can only express a window to a dot of its origin, so the window it asks for
		is close rather than exact. Close to one pin per point is taken as meaning it."""
		self.assertIn("1 pin per point", self.window(100, 104).name)

	def test_theGapsBetweenPointsAreAllTheSame(self):
		window = self.window(100, 40)
		self.assertIn("2 pins per point", window.name)
		peaks = [column for column, dot in enumerate(rows(window)[PLOT_TOP]) if dot == "O"]
		gaps = {right - left for left, right in zip(peaks, peaks[1:])}
		self.assertEqual(gaps, {4})

	def test_theWindowStaysAboutItsMiddle(self):
		"""The mode keeps the reader's place in the middle of the view, so the snapping widens a
		window on both sides rather than off one end."""
		self.assertIn("d96 to d143", self.window(100, 40).name)

	def test_aWindowAtTheEndStaysInsideTheData(self):
		self.assertIn("d202 to d249", self.window(238, 40).name)

	def test_aWindowAgainstTheEndIsWidenedBackwardsNotOffTheEnd(self):
		"""Widened about its middle, a window ending on the last day came back a day short of it, and
		the last close could not be panned to. Found on hardware at one pin per point."""
		self.assertTrue(self.window(156, 94).name.endswith("d154 to d249"))

	def test_aWindowAgainstTheStartIsWidenedForwards(self):
		self.assertIn("d0 to d95", self.window(0, 94).name)

	def test_aCompressedWindowIsLeftAsItIs(self):
		"""No spacing makes two hundred points across ninety-six pins even."""
		window = self.window(0, 200)
		self.assertNotIn("per point", window.name)
		self.assertIn("2.1 points per pin", window.name)

	def test_theWholeChartIsNeverSnapped(self):
		"""The whole chart has to show every point, and there is nothing past its ends to widen
		into, so it keeps its points spread to both edges."""
		drawing = chart([Line("a", list(range(60)))], labels=labelled(60))
		self.assertNotIn("per point", drawing.name)
		self.assertTrue(any(row[WIDTH - 1] == "O" for row in rows(drawing)))

	def test_theWholeChartSaysWhenPointsShareAPin(self):
		self.assertIn("2.6 points per pin", self.drawing.name)


class TestPointingWithAFinger(unittest.TestCase):
	"""A press answers for the line as drawn, within the pad of a finger.

	It used to answer for the one data point nearest the press, so a finger squarely on a steep
	stretch of line between two points -- where neither point's height is anywhere near the
	finger -- was told the height instead. Asked for from hardware as missing data.
	"""

	def test_aSteepStretchBetweenPointsIsFound(self):
		values = [0, 100] * 5
		drawing = chart([Line("Close", values)])
		text = rows(drawing)
		middle = (PLOT_TOP + PLOT_BOTTOM) // 2
		# About halfway up the first rise, well away from either point's column.
		column = text[middle].index("O")
		self.assertIn("Close", drawing.describeAt(column, middle))

	def test_twoColumnsBesideALineIsStillOnIt(self):
		drawing = chart([Line("a", [None, 5, None]), Line("b", [1, 1, 1])])
		self.assertIn("a 5", drawing.describeAt(50, PLOT_TOP))

	def test_threeColumnsBesideIsNot(self):
		drawing = chart([Line("a", [None, 5, None]), Line("b", [1, 1, 1])])
		self.assertNotIn("a 5", drawing.describeAt(51, PLOT_TOP))

	def test_threeRowsAboveOrBelowIsStillOnIt(self):
		drawing = chart([Line("a", [None, 5, None]), Line("b", [1, 1, 1])])
		self.assertIn("a 5", drawing.describeAt(48, PLOT_TOP + 3))
		self.assertNotIn("a 5", drawing.describeAt(48, PLOT_TOP + 4))

	def test_everyLineUnderTheFingerAnswersNearestFirst(self):
		"""So a crossing, or two lines in a tight squeeze, says why it feels crowded."""
		drawing = chart(
			[Line("Close", [100] * 3), Line("MA", [96] * 3), Line("Low", [0] * 3)],
			labels=["Mon", "Tue", "Wed"],
		)
		answer = drawing.describeAt(10, PLOT_TOP)
		self.assertIn("Close 100", answer)
		self.assertIn("MA 96", answer)
		self.assertLess(answer.index("Close"), answer.index("MA"))
		self.assertIn("Mon", answer)
		self.assertNotIn("Low", answer)

	def test_linesUnderTheFingerAreReadAtTheSameDay(self):
		"""One press on two lines is a question about one day. A line found a column over is
		read at the day under the finger, not at the day its nearest dot belongs to, or one
		press on a band and its average would answer for two different days."""
		drawing = chart(
			[Line("Close", [60] * 96), Line("MA", [60] * 11 + [100] * 85), Line("Low", [0] * 96)],
			labels=labelled(96),
		)
		# Beside the jump in the average from d10 to d11, and three rows above the close.
		answer = drawing.describeAt(12, 11)
		self.assertIn("MA 100", answer)
		self.assertIn("Close 60", answer)
		self.assertIn("d12", answer)
		self.assertNotIn("d11", answer)


class TestShowingSomeOfTheLines(unittest.TestCase):
	"""Four textures crossing are four to hold apart at once. On paper a reader would have the
	lines on separate sheets; a view is that, on the same scale and in the same textures, so
	what is learned from a line alone is still true when the others come back."""

	def setUp(self):
		self.drawing = chart(
			[Line("Close", [100] * 8), Line("MA", [50] * 8), Line("Low", [0] * 8)],
			labels=labelled(8),
		)

	def test_theViewsAreTheWholeThenEachAloneThenTheFirstWithEachOther(self):
		self.assertEqual(viewsFor(1), [(0,)])
		self.assertEqual(viewsFor(2), [(0, 1), (0,), (1,)])
		self.assertEqual(
			viewsFor(4),
			[(0, 1, 2, 3), (0,), (1,), (2,), (3,), (0, 1), (0, 2), (0, 3)],
		)

	def test_theNextViewIsTheFirstLineAlone(self):
		view = self.drawing.nextView(1)
		self.assertIn("Close thick solid", view.name)
		self.assertIn("1 of 3 lines", view.name)
		self.assertNotIn("MA", view.name)

	def test_stepsCarryOnFromTheViewTheyAreTakenFrom(self):
		view = self.drawing.nextView(1).nextView(1)
		self.assertIn("MA dashed", view.name)
		self.assertNotIn("Close", view.name)

	def test_backFromTheWholeChartIsTheLastPair(self):
		view = self.drawing.nextView(-1)
		self.assertIn("Close", view.name)
		self.assertIn("Low", view.name)
		self.assertIn("2 of 3 lines", view.name)

	def test_aLineAloneKeepsItsHeightAndItsTexture(self):
		"""The others still set the scale. Refitted, the line would move every time the view
		changed, and a line that moves is not the same line."""
		whole = rows(self.drawing)
		alone = rows(self.drawing.nextView(1).nextView(1))
		row = next(index for index in range(PLOT_TOP + 2, PLOT_BOTTOM) if "O" in whole[index])
		self.assertIn("O", alone[row])
		self.assertLess(alone[row].count("O"), WIDTH)
		self.assertNotIn("O", alone[PLOT_TOP])

	def test_theFirstLineIsStillThickWhenItIsAlone(self):
		alone = rows(self.drawing.nextView(1))
		self.assertEqual(alone[PLOT_TOP + 1].count("O"), WIDTH)

	def test_aLinePutAsideDoesNotAnswerAPress(self):
		alone = self.drawing.nextView(1).nextView(1)
		self.assertNotIn("Close", alone.describeAt(10, PLOT_TOP))

	def test_theViewIsKeptWhenTheChartIsZoomed(self):
		window = self.drawing.nextView(1).redraw(0.0, 0.5, WIDTH, HEIGHT)
		self.assertIn("1 of 3 lines", window.name)

	def test_aWindowWithNothingForTheLinesShownSaysSo(self):
		drawing = chart(
			[Line("Close", list(range(20))), Line("MA", [None] * 10 + [5] * 10)],
			labels=labelled(20),
		)
		averageAlone = drawing.nextView(-1)
		self.assertIn("1 of 2 lines", averageAlone.name)
		window = averageAlone.redraw(0.0, 0.25, WIDTH, HEIGHT)
		self.assertIsNotNone(window)
		self.assertEqual(window.note, "no values here for the lines shown")

	def test_aSingleLineHasNoOtherViews(self):
		self.assertIsNone(chart([Line("a", [1, 2])]).nextView)
