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

	def test_eightRowPitchGivesTerminalModesShape(self):
		"""8 lines of 32 is what terminal mode reports, and 8 x 5 is 40."""
		pitch = monarch.PITCH_8_ROW
		self.assertEqual(pitch.name, "8row")
		self.assertEqual(pitch.lineStride, 5)
		self.assertEqual(pitch.cellStride, 3)
		self.assertEqual(pitch.numRows, 8)
		self.assertEqual(pitch.numCols, 32)

	def test_tenRowPitchDropsTheBlankRowNotTheDots(self):
		"""Four dot rows and no blank one: 10 x 4 is 40."""
		pitch = monarch.PITCH_10_ROW
		self.assertEqual(pitch.name, "10row")
		self.assertEqual(pitch.lineStride, 4)
		self.assertEqual(pitch.gapRows, 0)
		self.assertEqual(pitch.numRows, 10)
		self.assertEqual(pitch.numCols, 32)

	def test_everyPitchDrawsAllEightDots(self):
		"""A pitch never costs a dot, so a caret and an eight dot table work at both.

		Four dot rows in a cell is the whole point: dots 7 and 8 live on the fourth, which at
		10 rows is the row that would otherwise separate the lines.
		"""
		for pitch in (monarch.PITCH_8_ROW, monarch.PITCH_10_ROW):
			self.assertEqual(pitch.dotRows, 4)

	def test_bothPitchesUseEveryRow(self):
		for pitch in (monarch.PITCH_8_ROW, monarch.PITCH_10_ROW):
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
		self.assertEqual(1600 - 1120, monarch.PIN_WIDTH * monarch.PITCH_8_ROW.lineStride)


class TestCellAtPin(unittest.TestCase):
	def test_originIsTheFirstCell(self):
		self.assertEqual(monarch.cellAtPin(0, 0, monarch.PITCH_8_ROW), (0, 0))

	def test_gapRowBelongsToTheLineAbove(self):
		"""A fingertip covers several pins, so the gap must not be dead."""
		self.assertEqual(monarch.cellAtPin(0, 4, monarch.PITCH_8_ROW), (0, 0))
		self.assertEqual(monarch.cellAtPin(0, 5, monarch.PITCH_8_ROW), (1, 0))

	def test_gapColumnBelongsToTheCellBefore(self):
		self.assertEqual(monarch.cellAtPin(2, 0, monarch.PITCH_8_ROW), (0, 0))
		self.assertEqual(monarch.cellAtPin(3, 0, monarch.PITCH_8_ROW), (0, 1))

	def test_tenRowPitchGivesTenAddressableRows(self):
		self.assertEqual(monarch.cellAtPin(0, 36, monarch.PITCH_10_ROW), (9, 0))

	def test_tenRowPitchHasNoGapRowToAbsorb(self):
		"""With no blank row, every pin row belongs to a line and rows follow each other."""
		self.assertEqual(monarch.cellAtPin(0, 3, monarch.PITCH_10_ROW), (0, 0))
		self.assertEqual(monarch.cellAtPin(0, 4, monarch.PITCH_10_ROW), (1, 0))

	def test_offGridIsRejected(self):
		self.assertIsNone(monarch.cellAtPin(-1, 0, monarch.PITCH_8_ROW))
		self.assertIsNone(monarch.cellAtPin(0, monarch.PIN_HEIGHT, monarch.PITCH_8_ROW))

	def test_lastPinClampsToTheLastCell(self):
		row, col = monarch.cellAtPin(
			monarch.PIN_WIDTH - 1,
			monarch.PIN_HEIGHT - 1,
			monarch.PITCH_8_ROW,
		)
		self.assertEqual((row, col), (7, 31))

	def test_routingIndexIsFlatAcrossTheCurrentPitch(self):
		self.assertEqual(monarch.routingIndexForPin(0, 0, monarch.PITCH_8_ROW), 0)
		self.assertEqual(monarch.routingIndexForPin(3, 0, monarch.PITCH_8_ROW), 1)
		self.assertEqual(monarch.routingIndexForPin(0, 5, monarch.PITCH_8_ROW), 32)

	def test_routingIndexCoversEveryCellAtTenRows(self):
		"""320 cells at 10 rows, against the 256 the device can name. That is the bug.

		The device reports routing on its native 8 by 32 grid whatever we render, so at 10
		rows it cannot address 64 of the cells on the panel and misplaces the rest.
		"""
		pitch = monarch.PITCH_10_ROW
		seen = {
			monarch.routingIndexForPin(*monarch.cellOrigin(row, col, pitch), pitch)
			for row in range(pitch.numRows)
			for col in range(pitch.numCols)
		}
		self.assertEqual(seen, set(range(pitch.numRows * pitch.numCols)))
		self.assertEqual(len(seen), 320)
		self.assertGreater(len(seen), monarch.NATIVE_ROUTING_COLS * monarch.NATIVE_ROUTING_ROWS)

	def test_theSamePinRoutesDifferentlyAtEachPitch(self):
		"""Pin row 20 is line 4 at a 5 row pitch and line 5 at a 4 row pitch."""
		self.assertEqual(monarch.routingIndexForPin(0, 20, monarch.PITCH_8_ROW), 4 * 32)
		self.assertEqual(monarch.routingIndexForPin(0, 20, monarch.PITCH_10_ROW), 5 * 32)

	def test_routingIndexRejectsAPinOffTheGrid(self):
		self.assertIsNone(monarch.routingIndexForPin(-1, 0, monarch.PITCH_8_ROW))
		self.assertIsNone(monarch.routingIndexForPin(0, monarch.PIN_HEIGHT, monarch.PITCH_10_ROW))

	def test_cellOriginRoundTrips(self):
		for pitch in (monarch.PITCH_8_ROW, monarch.PITCH_10_ROW):
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


class TestCellSlot(unittest.TestCase):
	"""A glyph fills the gap column braille leaves blank, without moving the next cell."""

	def test_slotIsOneColumnWiderThanACell(self):
		for pitch in (monarch.PITCH_8_ROW, monarch.PITCH_10_ROW):
			width, height = pitch.slotSize
			self.assertEqual(width, pitch.cellCols + pitch.gapCols)
			self.assertEqual(width, pitch.cellCols + 1)
			self.assertEqual(height, 4)

	def test_slotsTileTheGridExactly(self):
		"""32 slots of 3 is 96, so even a glyph in the last cell fits with nothing spilling."""
		pitch = monarch.PITCH_8_ROW
		self.assertEqual(pitch.numCols * pitch.slotSize[0], monarch.PIN_WIDTH)

	def test_slotsDoNotOverlap(self):
		pitch = monarch.PITCH_8_ROW
		width = pitch.slotSize[0]
		for col in range(pitch.numCols - 1):
			thisX, _ = monarch.cellOrigin(0, col, pitch)
			nextX, _ = monarch.cellOrigin(0, col + 1, pitch)
			self.assertEqual(thisX + width, nextX)


class TestGlyphPatterns(unittest.TestCase):
	"""The shapes themselves, drawn as text so a wrong one is visible rather than inferred."""

	def test_fromRowsRoundTripsThroughRows(self):
		shape = ["OOO", "O.O", "OOO", "..."]
		self.assertEqual(PinBuffer.fromRows(shape).rows(), shape)

	def test_fromRowsAcceptsAnyInkCharacter(self):
		self.assertEqual(PinBuffer.fromRows(["#*O"]).rows(), ["OOO"])

	def test_fromRowsPadsShortRows(self):
		self.assertEqual(PinBuffer.fromRows(["OOO", ""]).rows(), ["OOO", "..."])

	def test_focusIndicatorIsASquareThatBrailleCannotDraw(self):
		"""Monarch's own list indicator: a solid 3 by 3 with the fourth row left blank.

		Three columns wide is the whole point — a braille cell has two, so the square is only
		square once the gap column is used. The fallback below is the nearest braille can get.
		"""
		glyph = PinBuffer.fromRows(["OOO", "OOO", "OOO", "..."])
		self.assertEqual(glyph.width, monarch.PITCH_8_ROW.slotSize[0])
		self.assertEqual(glyph.height, monarch.PITCH_8_ROW.slotSize[1])
		self.assertEqual(glyph.rows(), ["OOO", "OOO", "OOO", "..."])

	def test_dotsOneToSixIsTheFocusIndicatorsFallback(self):
		"""0x3F is a solid 2 by 3, which is the square minus the column braille cannot reach.

		So one buffer serves every display: a true square here, a recognisable block elsewhere.
		"""
		buffer = PinBuffer(monarch.BLOCK_WIDTH, monarch.BLOCK_HEIGHT)
		for bit in range(8):
			if 0x3F & (1 << bit):
				x, y = monarch._BRAILLE_DOT_COORDS[bit]
				buffer.setDot(x, y)
		self.assertEqual(buffer.rows(), ["OO", "OO", "OO", ".."])


if __name__ == "__main__":
	unittest.main()
