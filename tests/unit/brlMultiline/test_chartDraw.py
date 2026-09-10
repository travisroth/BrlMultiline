# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the drawing vocabulary the chart types share.

Three chart types now put a number on a row and a string in a corner, and the reason that
arithmetic is in one module is that two of them disagreeing about where the top of the plot is
would be felt by the reader as two charts that cannot be compared — a fault that shows up as a
line chart and a candlestick chart of the same prices sitting at different heights, and which
nothing in either of those modules' own tests would catch.

The scale is tested in both directions on purpose. Reading a row back as a value is what
answers "what price is my finger at", which is a question a chart on paper cannot be asked,
and it is the half that has no drawing to check it against.
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

from brlMultiline.chartDraw import (  # noqa: E402
	TEXT_ROWS,
	Scale,
	cellsFor,
	numberText,
	roundedText,
	textRowsFor,
	writeAt,
	writeFrame,
	writeRightAt,
)


def spell(text):
	""":return: one cell per character, dot 1 raised.

	Enough to check where writing lands and how much of it there is, which is what these
	helpers decide. What the cells say is liblouis's business and is tested by using it.
	"""
	return [0b00000001 for _ in text]


class TestTheScale(unittest.TestCase):
	def test_theHighestValueIsDrawnAtTheTop(self):
		scale = Scale.forValues([5, 10], top=4, bottom=30)
		self.assertEqual(scale.row(10), 4)

	def test_theLowestValueIsDrawnAtTheBottom(self):
		scale = Scale.forValues([5, 10], top=4, bottom=30)
		self.assertEqual(scale.row(5), 30)

	def test_theMiddleOfTheDataIsTheMiddleOfThePlot(self):
		scale = Scale.forValues([0, 100], top=0, bottom=10)
		self.assertEqual(scale.row(50), 5)

	def test_missingValuesDoNotSetTheRange(self):
		"""A moving average has no value for its first rows, and a None in the numbers must not
		be read as a zero that drags the whole plot down to it."""
		scale = Scale.forValues([None, 5, 10, None], top=0, bottom=10)
		self.assertEqual((scale.low, scale.high), (5.0, 10.0))

	def test_aFlatSeriesIsDrawnThroughTheMiddle(self):
		"""Along the floor it reads as a row of zeroes; through the middle it reads as what it
		is, which is a quantity that did not change."""
		scale = Scale.forValues([7, 7, 7], top=0, bottom=10)
		self.assertEqual(scale.row(7), 5)

	def test_aValueOffTheEndIsHeldAtTheEdge(self):
		"""Drawing it outside the plot would put it in the writing, and the buffer would clip it
		silently somewhere the reader could not account for."""
		scale = Scale.forValues([5, 10], top=4, bottom=30)
		self.assertEqual(scale.row(1000), 4)
		self.assertEqual(scale.row(-1000), 30)

	def test_aRowReadsBackAsTheValueItStandsFor(self):
		scale = Scale.forValues([0, 100], top=0, bottom=10)
		self.assertAlmostEqual(scale.valueAt(5), 50.0)
		self.assertAlmostEqual(scale.valueAt(0), 100.0)
		self.assertAlmostEqual(scale.valueAt(10), 0.0)

	def test_aPlotOfOneRowStillAnswers(self):
		scale = Scale.forValues([5, 10], top=3, bottom=3)
		self.assertEqual(scale.row(10), 3)
		self.assertEqual(scale.valueAt(3), 10.0)

	def test_oneRowIsWorthAStepOfTheRange(self):
		scale = Scale.forValues([0, 100], top=0, bottom=10)
		self.assertAlmostEqual(scale.step, 10.0)


class TestSayingANumber(unittest.TestCase):
	def test_aWholeNumberHasNoDecimalPart(self):
		self.assertEqual(numberText(17.0), "17")

	def test_aFractionKeepsIt(self):
		self.assertEqual(numberText(2.5), "2.5")

	def test_aValueIsRoundedToWhatARowCanDistinguish(self):
		"""Read off a position rather than out of the data. The drawing does not have the
		precision to mean 50.428571, so answering it would be a confidence the chart has not
		earned."""
		self.assertEqual(roundedText(50.428571, step=1.0), "50")
		self.assertEqual(roundedText(50.428571, step=0.01), "50.43")

	def test_aFlatScaleSaysTheNumberItself(self):
		self.assertEqual(roundedText(7.25, step=0.0), "7.25")


class TestHowMuchRoomTheWritingCosts(unittest.TestCase):
	def test_aTallChartIsWrittenOn(self):
		self.assertEqual(textRowsFor(35, spell), TEXT_ROWS)

	def test_aShortChartIsDrawnBare(self):
		"""Below a point the writing takes so much of the chart that its shape stops being
		readable, which is the one thing a chart is for."""
		self.assertEqual(textRowsFor(10, spell), 0)

	def test_noTranslatorMeansNoWriting(self):
		self.assertEqual(textRowsFor(35, None), 0)


class TestWriting(unittest.TestCase):
	def buffer(self, width=30, height=8):
		return PinBuffer(width, height)

	def test_writingStartsWhereItWasPut(self):
		buffer = self.buffer()
		writeAt(buffer, 6, 0, spell, "ab", room=10)
		self.assertEqual(buffer.rows()[0][:12], "......O..O..")

	def test_writingIsCutToTheRoomItHas(self):
		"""A cut label is still recognisable: "Wednes" is Wednesday."""
		buffer = self.buffer()
		self.assertEqual(writeAt(buffer, 0, 0, spell, "abcdef", room=2), 2)

	def test_aNumberIsWrittenWholeOrNotAtAll(self):
		""""330" for 33000 is a different number said with confidence, so it is left out and the
		reader gets it by pointing at the chart instead."""
		buffer = self.buffer()
		self.assertEqual(writeAt(buffer, 0, 0, spell, "33000", room=3, whole=True), 0)
		self.assertNotIn("O", buffer.rows()[0])

	def test_rightAlignedWritingEndsWhereItWasPut(self):
		buffer = self.buffer(width=30)
		writeRightAt(buffer, 29, 0, spell, "ab", room=10)
		# Two cells of three columns each, so the first of them starts three columns back.
		self.assertEqual(buffer.rows()[0].index("O"), 24)

	def test_noRoomWritesNothing(self):
		buffer = self.buffer()
		self.assertEqual(writeAt(buffer, 0, 0, spell, "a", room=0), 0)

	def test_noTranslatorWritesNothing(self):
		buffer = self.buffer()
		self.assertEqual(writeAt(buffer, 0, 0, None, "a", room=5), 0)

	def test_aTranslatorThatFailsCostsTheWritingAndNotTheChart(self):
		"""A chart with no writing on it is still a chart; one that failed to appear because the
		braille tables were between states is not."""

		def broken(text):
			raise RuntimeError("no tables")

		self.assertEqual(cellsFor(broken, "a"), [])


class TestTheFrame(unittest.TestCase):
	"""The four corners, which are the whole of the axis a chart this size can afford.

	A drawn axis with ticks and numbers up the side would cost a third of the width and say
	less, because at ninety-six pins a number beside a tick has nowhere to be. These four say
	what the top is worth, what the bottom is worth, where the data starts and where it ends,
	and pointing covers everything in between.
	"""

	def frame(self, first="Jan", last="Dec", width=96, height=35):
		buffer = PinBuffer(width, height)
		scale = Scale.forValues([5, 40], top=TEXT_ROWS, bottom=height - 1 - TEXT_ROWS)
		writeFrame(buffer, spell, scale, first, last)
		return buffer.rows()

	def test_theRangeReadsLowToHighAcrossTheTop(self):
		"""Ascending left to right, like the dates along the bottom, because that is the
		only arrangement that does not have to be memorised. The high at the left was the
		first arrangement — the top of a plot is its high — and that reasoning does not
		survive the two of them being on the same line, where up and down mean nothing.
		"""
		rows = self.frame()
		# The low is 5, one cell: one dot at the left and nothing beside it.
		self.assertEqual(rows[0][:6], "O.....")
		# The high is 40, two cells, right aligned to the panel.
		self.assertEqual(rows[0][88:], "..O..O..")

	def test_theFirstLabelIsWrittenAtTheBottomLeft(self):
		rows = self.frame()
		self.assertEqual(rows[-TEXT_ROWS][:9], "O..O..O..")

	def test_theLastLabelIsWrittenAtTheBottomRight(self):
		rows = self.frame()
		self.assertIn("O", rows[-TEXT_ROWS][80:])

	def test_oneLabelIsNotWrittenTwice(self):
		"""A chart of a single period would otherwise say the same date at both ends and read as
		a range that goes nowhere."""
		rows = self.frame(first="Jan", last="Jan")
		self.assertNotIn("O", rows[-TEXT_ROWS][48:])

	def test_theFrameKeepsOffThePlot(self):
		rows = self.frame()
		self.assertTrue(all("O" not in row for row in rows[TEXT_ROWS:-TEXT_ROWS]))

	def test_aChartTooNarrowForOneCellIsLeftBare(self):
		rows = self.frame(width=4, height=35)
		self.assertTrue(all("O" not in row for row in rows))
