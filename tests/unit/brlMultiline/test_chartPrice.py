# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for open, high, low, close charts and for candlesticks.

Both draw the same four numbers per period, and the shapes are asserted dot by dot on a small
chart because that is the only way to know they are the shape at all. An OHLC bar is a stem
with a tick on each side of it; if the ticks land on the stem, or on the wrong side of it, the
chart still looks like a chart and has lost the open and the close — which is two of the four
numbers, silently.

The candlestick tests are mostly about the hollow. Rising and falling are told apart by the
body being hollow rather than filled, because colour and greyscale are not available, and the
hollow is one column of gap at three pins wide. The wick is what threatens it: drawn up the
whole range with the body over it, it fills that column and every candle reads as a falling
one, which is a chart that is wrong about the direction of every day in it.
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
from brlMultiline.chartPrice import MIN_SLOT, Period, candleChart, ohlcChart  # noqa: E402

RISING = Period(label="Mon", open=10, high=20, low=0, close=15)
"""A day that opened at 10, ranged from 0 to 20, and closed up at 15."""

FALLING = Period(label="Tue", open=10, high=10, low=5, close=5)
"""A day that opened at its high and closed at its low."""


def newBuffer(width, height):
	""":return: a buffer, standing in for the display's own factory."""
	return PinBuffer(width, height)


def spell(text):
	""":return: one cell per character, dot 1 raised."""
	return [0b00000001 for _ in text]


def rows(drawing):
	""":return: the drawing as text, raised dots as O."""
	return drawing.buffer.rows()


class TestTheBarShape(unittest.TestCase):
	"""Twelve pins by twelve, two periods, so every dot can be accounted for.

	Two periods across twelve pins is a slot of six: five pins of bar and one of gap. The stem
	is in the middle of the five, at column two of the first bar, which leaves two columns for
	the open tick on its left and two for the close tick on its right.

	The values are chosen so that the four rows are all different and none of them is an edge
	the drawing would have landed on anyway: the range is 0 to 20 over rows 0 to 11, so 10 is
	row 5, 15 is row 3 and 5 is row 8.
	"""

	def setUp(self):
		self.text = rows(ohlcChart(newBuffer, 12, 12, [RISING, FALLING]))

	def test_theStemSpansTheHighToTheLow(self):
		"""The day's range, and the first thing a finger following the row of stems reads."""
		self.assertTrue(all(row[2] == "O" for row in self.text))

	def test_theOpenIsATickOnTheLeftOfTheStem(self):
		"""Sweeping left to right, a tick met *before* its stem is an open. That is the whole
		convention, and it is why the side matters more than the shape."""
		self.assertEqual(self.text[5][:3], "OOO")

	def test_theCloseIsATickOnTheRightOfTheStem(self):
		self.assertEqual(self.text[3][2:5], "OOO")

	def test_eachPeriodGetsItsOwnSlot(self):
		self.assertEqual(self.text[5][6:9], "OOO")
		self.assertEqual(self.text[8][8:11], "OOO")

	def test_thereIsAGapBetweenOnePeriodAndTheNext(self):
		"""Without it two full bars are one block, and the reader counts one day where there
		were two."""
		self.assertTrue(all(row[5] == "." for row in self.text))

	def test_everyPeriodIsDrawnAgainstTheSameScale(self):
		"""A day scaled to its own range would fill the panel whatever it did, and the shape of
		a month would be nothing at all."""
		# The second day's high is 10, which is halfway up a range of 0 to 20.
		self.assertEqual(self.text[0][8], ".")
		self.assertEqual(self.text[5][8], "O")


class TestTheCandleShape(unittest.TestCase):
	def setUp(self):
		self.text = rows(candleChart(newBuffer, 12, 12, [RISING, FALLING]))

	def test_aRisingPeriodIsHollow(self):
		"""The only one of the sighted chart's two signals — hollow against filled, green
		against red — that survives having neither colour nor greyscale."""
		self.assertEqual(self.text[4][:5], "O...O")

	def test_aFallingPeriodIsSolid(self):
		self.assertEqual(self.text[6][6:11], "OOOOO")

	def test_theBodyRunsBetweenTheOpenAndTheClose(self):
		self.assertEqual(self.text[3][:5], "OOOOO")
		self.assertEqual(self.text[5][:5], "OOOOO")

	def test_theWickReachesTheHighAndTheLow(self):
		self.assertEqual(self.text[0][2], "O")
		self.assertEqual(self.text[11][2], "O")

	def test_theWickDoesNotRunThroughTheBody(self):
		"""Drawn up the whole range with the body over it, the wick fills the one column of gap
		that makes a rising body hollow, and every candle reads as a falling one."""
		self.assertEqual(self.text[4][2], ".")

	def test_aPeriodThatDidNotRangeBeyondItsBodyHasNoWick(self):
		"""The second day opened at its high and closed at its low, so there is nothing above
		or below the body and nothing should be drawn there."""
		self.assertTrue(all(row[8] == "." for row in self.text[:5]))
		self.assertTrue(all(row[8] == "." for row in self.text[9:]))


class TestTheLayout(unittest.TestCase):
	def test_aChartFillsTheRectangleItWasGiven(self):
		drawing = ohlcChart(newBuffer, 96, 35, [RISING, FALLING])
		self.assertEqual((drawing.width, drawing.height), (96, 35))

	def test_theLastPeriodReachesTheRightEdge(self):
		"""Rather than each period taking a slot of width over count and the remainder being
		left blank, which would put the last date — written along the bottom, right aligned to
		the panel — two braille cells past the bar it names."""
		periods = [RISING] * 20
		text = rows(ohlcChart(newBuffer, 96, 35, periods))
		self.assertIn("O", "".join(row[90:] for row in text))

	def test_theWholeRowOfPeriodsIsDrawn(self):
		periods = [RISING, FALLING] * 12
		text = rows(ohlcChart(newBuffer, 96, 35, periods))
		# Twenty-four periods is a slot of four across ninety-six pins, and the chart is only
		# right if every one of those slots has something in it: a period drawn nowhere is a
		# day the reader will never find and will not know is missing.
		for start in range(0, 96, MIN_SLOT):
			self.assertIn("O", "".join(row[start : start + MIN_SLOT] for row in text))


class TestWhatAPriceChartRefuses(unittest.TestCase):
	def test_nothingToChart(self):
		with self.assertRaises(ChartRefused):
			ohlcChart(newBuffer, 96, 35, [])

	def test_morePeriodsThanTheDisplayHolds(self):
		"""Silently charting the first twenty-four of sixty days would be a different chart
		drawn confidently, which is worse than saying it does not fit."""
		with self.assertRaises(ChartRefused) as caught:
			ohlcChart(newBuffer, 96, 35, [RISING] * 40)
		self.assertIn("24", str(caught.exception))

	def test_theLimitIsTheNarrowestBarThatCanCarryTwoTicks(self):
		"""Three pins of bar and one of gap. A bar with its ticks on top of its stem is a bar
		with no open and no close in it."""
		self.assertEqual(MIN_SLOT, 4)

	def test_noRoomAtAll(self):
		with self.assertRaises(ChartRefused):
			candleChart(newBuffer, 2, 35, [RISING])

	def test_aDisplayThatWillNotProvideABuffer(self):
		with self.assertRaises(ChartRefused):
			ohlcChart(lambda width, height: None, 96, 35, [RISING])


class TestWritingOnAPriceChart(unittest.TestCase):
	def chart(self, height=35):
		return rows(ohlcChart(newBuffer, 96, height, [RISING, FALLING], spell))

	def test_theRangeIsWrittenAcrossTheTop(self):
		self.assertIn("O", self.chart()[0])

	def test_theFirstAndLastDatesAreWrittenAcrossTheBottom(self):
		labelRow = self.chart()[-TEXT_ROWS]
		self.assertIn("O", labelRow[:12])
		self.assertIn("O", labelRow[80:])

	def test_aShortChartIsDrawnBare(self):
		text = self.chart(height=10)
		self.assertEqual(text[0].count("O"), text[9].count("O"))

	def test_theBarsKeepTheMiddle(self):
		"""The writing must not overlap the bars, or a tall stem and the number above it become
		one shape under the finger."""
		self.assertNotIn("O", "".join(self.chart()[1:TEXT_ROWS]))


class TestPointingAtAPriceChart(unittest.TestCase):
	def setUp(self):
		self.drawing = ohlcChart(newBuffer, 12, 12, [RISING, FALLING])

	def test_aPeriodAnswersWithAllFourPrices(self):
		"""Not the one nearest the finger. A press on a price chart is the reader asking about
		that day, and answering "high 20" alone would make them press three more times for the
		rest of a day they already had their finger on."""
		answer = self.drawing.describeAt(2, 5)
		self.assertIn("Mon", answer)
		self.assertIn("10", answer)
		self.assertIn("20", answer)
		self.assertIn("0", answer)
		self.assertIn("15", answer)

	def test_theGapAfterAPeriodStillBelongsToIt(self):
		"""A fingertip is wider than the bar it is on, so a press that lands in the gap is a
		press on the bar the reader was following."""
		self.assertIn("Mon", self.drawing.describeAt(5, 0))

	def test_theNextPeriodAnswersForItsOwnSlot(self):
		self.assertIn("Tue", self.drawing.describeAt(8, 5))

	def test_aPressPastTheEndIsHeldAtTheLastPeriod(self):
		self.assertIn("Tue", self.drawing.describeAt(200, 5))

	def test_theChartIsNamedByWhatItIsAndHowManyPeriodsItHas(self):
		self.assertIn("open, high, low, close", self.drawing.name)
		self.assertIn("2 periods", self.drawing.name)
		self.assertIn("candlestick", candleChart(newBuffer, 12, 12, [RISING]).name)
