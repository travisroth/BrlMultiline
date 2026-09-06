# BrlMultiline: the Humanware Monarch's pin geometry and wire format.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Everything about the Monarch that a second tactile display would not share.

This is the device specific half of the split described in
`docs/design/monarch-driver-plan.md`: the pin grid's size, how a dot becomes a bit in the
output report, how a touch value becomes a pin, and the pitches text can be drawn at. A
DotPad X driver would supply its own version of this file and reuse `pinBuffer.py` and most
of the driver unchanged.

Like `pinBuffer.py` it imports nothing from NVDA, so it can be unit tested without one.
Everything here was established against hardware and two readings of it were wrong first;
see the tactile graphics plan for what the wrong ones looked like.
"""

PIN_WIDTH = 96
PIN_HEIGHT = 40
"""The pin grid. 3,840 pins, uniformly spaced, no clusters."""

PIN_COUNT = PIN_WIDTH * PIN_HEIGHT

BLOCK_WIDTH = 2
BLOCK_HEIGHT = 4
"""One output byte covers a 2 by 4 block of pins."""

BLOCK_COLS = PIN_WIDTH // BLOCK_WIDTH
BLOCK_ROWS = PIN_HEIGHT // BLOCK_HEIGHT
"""48 blocks across, 10 down, 480 bytes: exactly the payload of output report 0x21."""

PIN_REPORT_ID = 0x21
"""Output report 0x21, usage 0x301, ReportCount 3840. One bit per pin.

Declared as a single usage repeated 3,840 times rather than as a usage range, which is why
NVDA cannot see it: it reads output value caps only, and button caps only for input.
"""

PIN_REPORT_BYTES = PIN_COUNT // 8

TOUCH_PIN_USAGE = 0x401
"""Input report 0x40. The touched pin, one based, 0 on release."""

ROUTING_USAGE_MIN = 0x402
ROUTING_USAGE_MAX = 0x501
"""Input report 0x41. The device's own routing cell, on its native 8 by 32 grid."""

NATIVE_ROUTING_COLS = 32
NATIVE_ROUTING_ROWS = 8

_BRAILLE_DOT_COORDS = [
	(0, 0),  # dot 1
	(0, 1),  # dot 2
	(0, 2),  # dot 3
	(1, 0),  # dot 4
	(1, 1),  # dot 5
	(1, 2),  # dot 6
	(0, 3),  # dot 7
	(1, 3),  # dot 8
]
"""Where each dot sits inside a cell, matching NVDA's `tactile.braille._brailleDotCoords`.

Copied rather than imported because this module must be importable without NVDA. The driver
asserts the two agree at load time, so a change upstream is caught rather than inherited.
"""

BIT_FOR_BLOCK_POSITION = {coords: bit for bit, coords in enumerate(_BRAILLE_DOT_COORDS)}
"""Which bit of a block byte raises the pin at a position within that block.

**Braille dot numbering**, not the column major nibble layout
`dotPad.driver.DpTactileGraphicsBuffer` uses. The Monarch raises a byte's pins by dot number
whichever report the byte arrives in, so the DotPad's graphics packing is wrong here even
though the pin report is unambiguously a graphics buffer. This was tried and the hardware
refused it: a vertical line came out as a column of *p* and a horizontal one as a row of *e*.
"""


class Pitch:
	"""One way of laying braille lines out on the pin grid.

	A cell is always four dot rows: dots 1, 2, 3 down the left and 4, 5, 6 down the right,
	with dots 7 and 8 on the fourth. **Every pitch draws all eight**, so a caret shows and an
	eight dot table works whichever is chosen. Nothing here ever discards a dot.

	What the pitch decides is the blank row *after* a cell. One blank row gives 8 lines on a
	40 row grid; none gives 10. That is the whole difference.
	"""

	def __init__(self, name: str, gapRows: int, cellCols: int = 2, gapCols: int = 1):
		"""
		:param name: the setting value, and how it is logged.
		:param gapRows: blank rows after a line, before the next one starts.
		:param cellCols: dot columns in a cell, always 2.
		:param gapCols: blank columns between cells.
		"""
		self.name = name
		self.dotRows = BLOCK_HEIGHT
		self.gapRows = gapRows
		self.cellCols = cellCols
		self.gapCols = gapCols

	@property
	def lineStride(self) -> int:
		""":return: pin rows from the top of one line to the top of the next."""
		return self.dotRows + self.gapRows

	@property
	def cellStride(self) -> int:
		""":return: pin columns from the left of one cell to the left of the next."""
		return self.cellCols + self.gapCols

	@property
	def numRows(self) -> int:
		""":return: how many braille lines fit on the grid at this pitch."""
		return PIN_HEIGHT // self.lineStride

	@property
	def numCols(self) -> int:
		""":return: how many cells fit across the grid at this pitch."""
		return PIN_WIDTH // self.cellStride


PITCH_8_ROW = Pitch("8row", gapRows=1)
"""8 lines of 32. Four dot rows and a blank one: 8 x 5 = 40, exactly terminal mode.

The two lines terminal mode appears to withhold are not withheld, they are this blank row.
"""

PITCH_10_ROW = Pitch("10row", gapRows=0)
"""10 lines of 32. Four dot rows and no blank one: 10 x 4 = 40.

What the Monarch's own software offers, and the reason it can. Dots 7 and 8 are still drawn:
the fourth row of a cell is where they live, so what this spends is the separation between
lines rather than the lower two dots. Six dot content leaves that row empty and the lines
look separated anyway; a caret or an eight dot table fills it and the lines meet there.

Which is the user's trade to make, not the driver's. Nothing masks a dot.
"""

PITCHES = {pitch.name: pitch for pitch in (PITCH_8_ROW, PITCH_10_ROW)}


def pinBitIndex(x: int, y: int) -> int:
	"""Convert a pin coordinate to its bit index in the output report.

	One byte is a 2 by 4 block packed as an eight dot braille cell, and the blocks run in
	reading order, 48 across by 10 down.

	:param x: pin column, 0 to 95.
	:param y: pin row, 0 to 39.
	:return: bit index, 0 to 3839.
	"""
	blockIndex = (y // BLOCK_HEIGHT) * BLOCK_COLS + (x // BLOCK_WIDTH)
	return blockIndex * 8 + BIT_FOR_BLOCK_POSITION[(x % BLOCK_WIDTH, y % BLOCK_HEIGHT)]


def packPins(buffer) -> bytes:
	"""Convert a dot canvas to the 480 byte payload of output report 0x21.

	:param buffer: a `pinBuffer.PinBuffer` the size of the grid. A smaller one is drawn at the
		top left and the rest of the panel is left flat; a larger one is clipped.
	:return: the payload, without the report ID.
	"""
	payload = bytearray(PIN_REPORT_BYTES)
	for y in range(min(PIN_HEIGHT, buffer.height)):
		for x in range(min(PIN_WIDTH, buffer.width)):
			if buffer.getDot(x, y):
				index = pinBitIndex(x, y)
				payload[index // 8] |= 1 << (index % 8)
	return bytes(payload)


def touchedPin(value: int) -> tuple[int, int] | None:
	"""Convert an input usage 0x401 value to the pin the reader touched.

	The value is a **row major** index over the grid, one based, with 0 meaning released.
	Note that this is not the block packing the output uses: pins are written as blocks of
	braille dots and read back as a plain raster. Two orders on one device, and decoding the
	input with the output's mapping gives plausible nonsense.

	:param value: the raw value.
	:return: pin column and row, or None when nothing is touched or the value is out of range.
	"""
	if value <= 0 or value > PIN_COUNT:
		return None
	index = value - 1
	y, x = divmod(index, PIN_WIDTH)
	return x, y


def nativeRoutingCell(x: int, y: int) -> int:
	"""Convert a touched pin to the routing cell the device reports alongside it.

	The device's routing grid is its native 8 by 32 layout, whatever pitch we are rendering
	at. Six touches were checked against this and every one agreed, which is what established
	`touchedPin`.

	:param x: pin column.
	:param y: pin row.
	:return: routing cell index, 0 to 255.
	"""
	stride = PITCH_8_ROW
	return (y // stride.lineStride) * NATIVE_ROUTING_COLS + (x // stride.cellStride)


def cellAtPin(x: int, y: int, pitch: Pitch) -> tuple[int, int] | None:
	"""Convert a touched pin to a braille line and column at the pitch being rendered.

	This is what routing on a graphic needs, and why the pin report matters: the device's own
	routing cell assumes its native pitch and stops meaning anything at any other.

	A touch landing in a gap row or gap column still belongs to the cell it follows, because a
	fingertip covers several pins and refusing the ones between lines would make the bottom of
	every line dead.

	:param x: pin column.
	:param y: pin row.
	:param pitch: the layout being rendered.
	:return: braille row and column, or None if the pin is off the grid.
	"""
	if not (0 <= x < PIN_WIDTH and 0 <= y < PIN_HEIGHT):
		return None
	row = min(y // pitch.lineStride, pitch.numRows - 1)
	col = min(x // pitch.cellStride, pitch.numCols - 1)
	return row, col


def cellOrigin(row: int, col: int, pitch: Pitch) -> tuple[int, int]:
	"""Where a braille cell's top left dot sits on the pin grid.

	:param row: braille line, from 0.
	:param col: braille column, from 0.
	:param pitch: the layout being rendered.
	:return: pin column and row.
	"""
	return col * pitch.cellStride, row * pitch.lineStride
