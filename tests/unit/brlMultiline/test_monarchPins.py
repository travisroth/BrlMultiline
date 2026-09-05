# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the Monarch's pin geometry, packing and touch decode.

Every expectation here was established against hardware, and two readings of the packing were
wrong before this one. The values in `TestAgainstHardware` are the actual samples, so a change
that breaks them is a change that would have been refused by the device.
"""

import os
import sys
import unittest

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

import monarch  # noqa: E402
from pinBuffer import PinBuffer  # noqa: E402


class TestGeometry(unittest.TestCase):
	def test_gridIsThreeThousandEightHundredAndFortyPins(self):
		self.assertEqual(monarch.PIN_WIDTH * monarch.PIN_HEIGHT, 3840)
		self.assertEqual(monarch.PIN_COUNT, 3840)

	def test_blocksFillTheGridExactly(self):
		self.assertEqual(monarch.BLOCK_COLS, 48)
		self.assertEqual(monarch.BLOCK_ROWS, 10)
		self.assertEqual(monarch.BLOCK_COLS * monarch.BLOCK_ROWS, monarch.PIN_REPORT_BYTES)
		self.assertEqual(monarch.PIN_REPORT_BYTES, 480)

	def test_eightDotPitchGivesTerminalModesShape(self):
		"""8 lines of 32 is what terminal mode reports, and 8 x 5 is 40."""
		pitch = monarch.PITCH_EIGHT_DOT
		self.assertEqual(pitch.lineStride, 5)
		self.assertEqual(pitch.cellStride, 3)
		self.assertEqual(pitch.numRows, 8)
		self.assertEqual(pitch.numCols, 32)
		self.assertFalse(pitch.dropsDots7And8)

	def test_sixDotPitchGivesTenLines(self):
		"""3 dot rows and a gap is 4, and 10 x 4 is 40, with the same separation."""
		pitch = monarch.PITCH_SIX_DOT
		self.assertEqual(pitch.lineStride, 4)
		self.assertEqual(pitch.numRows, 10)
		self.assertEqual(pitch.numCols, 32)
		self.assertTrue(pitch.dropsDots7And8)

	def test_bothPitchesUseEveryRow(self):
		for pitch in (monarch.PITCH_EIGHT_DOT, monarch.PITCH_SIX_DOT):
			self.assertEqual(pitch.numRows * pitch.lineStride, monarch.PIN_HEIGHT)


class TestPinBitIndex(unittest.TestCase):
	"""The output packing: 2 by 4 blocks in reading order, braille dot numbering within."""

	def test_originIsBitZero(self):
		self.assertEqual(monarch.pinBitIndex(0, 0), 0)

	def test_bitOneIsBelowBitZero(self):
		self.assertEqual(monarch.pinBitIndex(0, 1), 1)

	def test_dotFourIsTopOfTheRightColumn(self):
		"""Braille dot numbering, not the DotPad's column major nibbles.

		Column major would put bit 4 here. It put out a row of *e* on the hardware.
		"""
		self.assertEqual(monarch.pinBitIndex(1, 0), 3)

	def test_bitFourIsDotFive(self):
		self.assertEqual(monarch.pinBitIndex(1, 1), 4)

	def test_dotSevenIsBottomOfTheLeftColumn(self):
		self.assertEqual(monarch.pinBitIndex(0, 3), 6)

	def test_lastPinIsLastBit(self):
		self.assertEqual(
			monarch.pinBitIndex(monarch.PIN_WIDTH - 1, monarch.PIN_HEIGHT - 1),
			monarch.PIN_COUNT - 1,
		)

	def test_everyPinGetsItsOwnBit(self):
		seen = {
			monarch.pinBitIndex(x, y) for y in range(monarch.PIN_HEIGHT) for x in range(monarch.PIN_WIDTH)
		}
		self.assertEqual(len(seen), monarch.PIN_COUNT)

	def test_secondBlockStartsAtTheThirdColumn(self):
		"""Blocks run in reading order, so x 2 is the next block along."""
		self.assertEqual(monarch.pinBitIndex(2, 0), 8)

	def test_secondBlockRowStartsAfterFortyEightBlocks(self):
		self.assertEqual(monarch.pinBitIndex(0, monarch.BLOCK_HEIGHT), monarch.BLOCK_COLS * 8)


class TestPackPins(unittest.TestCase):
	def test_blankBufferPacksToZeroes(self):
		payload = monarch.packPins(PinBuffer(monarch.PIN_WIDTH, monarch.PIN_HEIGHT))
		self.assertEqual(len(payload), monarch.PIN_REPORT_BYTES)
		self.assertEqual(set(payload), {0})

	def test_fullBufferPacksToOnes(self):
		buffer = PinBuffer(monarch.PIN_WIDTH, monarch.PIN_HEIGHT)
		buffer.rect(0, 0, monarch.PIN_WIDTH, monarch.PIN_HEIGHT, filled=True)
		self.assertEqual(set(monarch.packPins(buffer)), {0xFF})

	def test_horizontalLineIsDotsOneAndFourInEveryBlock(self):
		"""The figure the cell path cannot draw, and it came out solid on hardware.

		Dots 1 and 4 are bits 0 and 3, so every block byte is 0x09.
		"""
		buffer = PinBuffer(monarch.PIN_WIDTH, monarch.PIN_HEIGHT)
		buffer.line(0, 0, monarch.PIN_WIDTH - 1, 0)
		payload = monarch.packPins(buffer)
		self.assertEqual(set(payload[: monarch.BLOCK_COLS]), {0x09})
		self.assertEqual(set(payload[monarch.BLOCK_COLS :]), {0})

	def test_verticalLineIsTheLeftColumnOfEveryBlock(self):
		"""Dots 1, 2, 3 and 7, which is bits 0, 1, 2 and 6: 0x47."""
		buffer = PinBuffer(monarch.PIN_WIDTH, monarch.PIN_HEIGHT)
		buffer.line(0, 0, 0, monarch.PIN_HEIGHT - 1)
		payload = monarch.packPins(buffer)
		firstColumnBytes = [payload[row * monarch.BLOCK_COLS] for row in range(monarch.BLOCK_ROWS)]
		self.assertEqual(set(firstColumnBytes), {0x47})

	def test_smallBufferIsDrawnAtTheTopLeft(self):
		buffer = PinBuffer(2, 4)
		buffer.rect(0, 0, 2, 4, filled=True)
		payload = monarch.packPins(buffer)
		self.assertEqual(payload[0], 0xFF)
		self.assertEqual(set(payload[1:]), {0})


class TestTouchDecode(unittest.TestCase):
	"""Input is a plain raster index, not the block packing the output uses."""

	def test_zeroMeansReleased(self):
		self.assertIsNone(monarch.touchedPin(0))

	def test_oneIsTheOrigin(self):
		self.assertEqual(monarch.touchedPin(1), (0, 0))

	def test_lastValueIsTheLastPin(self):
		self.assertEqual(
			monarch.touchedPin(monarch.PIN_COUNT),
			(monarch.PIN_WIDTH - 1, monarch.PIN_HEIGHT - 1),
		)

	def test_beyondTheGridIsRejected(self):
		self.assertIsNone(monarch.touchedPin(monarch.PIN_COUNT + 1))
		self.assertIsNone(monarch.touchedPin(-5))

	def test_rowMajorNotBlockPacked(self):
		"""Value 97 is the start of the second raster row, not the second block."""
		self.assertEqual(monarch.touchedPin(monarch.PIN_WIDTH + 1), (0, 1))


class TestAgainstHardware(unittest.TestCase):
	"""The six touches recorded on the device, each with the cell it reported alongside.

	Every one of these derived the routing cell the Monarch itself sent in report 0x41. That
	agreement is what established the decode, so these are the regression that matters.
	"""

	SAMPLES = (  # noqa: RUF012
		(290, 0),
		(1120, 85),
		(1600, 117),
		(2356, 145),
		(998, 76),
		(2510, 164),
	)

	def test_everySampleDerivesTheDevicesOwnCell(self):
		for value, cell in self.SAMPLES:
			with self.subTest(value=value):
				position = monarch.touchedPin(value)
				self.assertIsNotNone(position)
				self.assertEqual(monarch.nativeRoutingCell(*position), cell)

	def test_knownPositions(self):
		self.assertEqual(monarch.touchedPin(998), (37, 10))
		self.assertEqual(monarch.touchedPin(2510), (13, 26))

	def test_consecutiveBrailleRowsAreOneLineBandApart(self):
		"""1120 and 1600 were one braille row apart, and 480 is 96 by 5."""
		self.assertEqual(1600 - 1120, monarch.PIN_WIDTH * monarch.PITCH_EIGHT_DOT.lineStride)


class TestCellAtPin(unittest.TestCase):
	def test_originIsTheFirstCell(self):
		self.assertEqual(monarch.cellAtPin(0, 0, monarch.PITCH_EIGHT_DOT), (0, 0))

	def test_gapRowBelongsToTheLineAbove(self):
		"""A fingertip covers several pins, so the gap must not be dead."""
		self.assertEqual(monarch.cellAtPin(0, 4, monarch.PITCH_EIGHT_DOT), (0, 0))
		self.assertEqual(monarch.cellAtPin(0, 5, monarch.PITCH_EIGHT_DOT), (1, 0))

	def test_gapColumnBelongsToTheCellBefore(self):
		self.assertEqual(monarch.cellAtPin(2, 0, monarch.PITCH_EIGHT_DOT), (0, 0))
		self.assertEqual(monarch.cellAtPin(3, 0, monarch.PITCH_EIGHT_DOT), (0, 1))

	def test_sixDotPitchGivesTenAddressableRows(self):
		self.assertEqual(monarch.cellAtPin(0, 36, monarch.PITCH_SIX_DOT), (9, 0))

	def test_offGridIsRejected(self):
		self.assertIsNone(monarch.cellAtPin(-1, 0, monarch.PITCH_EIGHT_DOT))
		self.assertIsNone(monarch.cellAtPin(0, monarch.PIN_HEIGHT, monarch.PITCH_EIGHT_DOT))

	def test_lastPinClampsToTheLastCell(self):
		row, col = monarch.cellAtPin(
			monarch.PIN_WIDTH - 1,
			monarch.PIN_HEIGHT - 1,
			monarch.PITCH_EIGHT_DOT,
		)
		self.assertEqual((row, col), (7, 31))

	def test_cellOriginRoundTrips(self):
		for pitch in (monarch.PITCH_EIGHT_DOT, monarch.PITCH_SIX_DOT):
			for row in range(pitch.numRows):
				for col in range(pitch.numCols):
					x, y = monarch.cellOrigin(row, col, pitch)
					self.assertEqual(monarch.cellAtPin(x, y, pitch), (row, col))


class TestPinBuffer(unittest.TestCase):
	def test_clipsRatherThanRaising(self):
		buffer = PinBuffer(4, 4)
		buffer.setDot(-1, 0)
		buffer.setDot(4, 0)
		buffer.setDot(0, 99)
		self.assertEqual(buffer.rows(), ["...."] * 4)

	def test_lineAndRect(self):
		buffer = PinBuffer(4, 4)
		buffer.rect(0, 0, 4, 4)
		self.assertEqual(buffer.rows(), ["OOOO", "O..O", "O..O", "OOOO"])

	def test_filledRect(self):
		buffer = PinBuffer(3, 2)
		buffer.rect(0, 0, 3, 2, filled=True)
		self.assertEqual(buffer.rows(), ["OOO", "OOO"])

	def test_blitIsAdditive(self):
		"""Text and a graphic share a panel, so neither may punch a hole in the other."""
		base = PinBuffer(4, 2)
		base.setDot(0, 0)
		overlay = PinBuffer(2, 1)
		overlay.setDot(1, 0)
		base.blit(overlay, 2, 1)
		self.assertEqual(base.rows(), ["O...", "...O"])

	def test_clearRect(self):
		buffer = PinBuffer(4, 2)
		buffer.rect(0, 0, 4, 2, filled=True)
		buffer.clearRect(1, 0, 2, 1)
		self.assertEqual(buffer.rows(), ["O..O", "OOOO"])

	def test_polyline(self):
		buffer = PinBuffer(3, 3)
		buffer.polyline([(0, 0), (2, 0), (2, 2)])
		self.assertEqual(buffer.rows(), ["OOO", "..O", "..O"])


if __name__ == "__main__":
	unittest.main()
