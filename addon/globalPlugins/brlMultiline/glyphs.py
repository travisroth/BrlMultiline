# BrlMultiline: the shapes a pin display can draw in place of braille.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Saying what NVDA says, in shapes rather than in letters.

NVDA already writes short strings to stand for roles and states: "btn" for a button, "cbo" for
a combo box, three cells for a checkbox. They are abbreviations because a braille line has
nothing else to spend — and a display that can raise individual pins does. The cell slot a
letter sits in is three pin columns by four, and nothing says those twelve pins have to spell.

So a glyph is a **drawing of the same width as the text it replaces**. "btn" is three cells,
so its glyph is nine pins by four; the cell after it starts exactly where it did, and routing,
wrapping and scrolling never learn that anything happened. Replacing three cells with one
would have been the obvious idea and is the wrong one: it moves everything after it.

Three things live here, and the order they are in is the order they are used.

1. **The notation.** How a human writes a shape down.
2. **The vocabulary.** What each shape *means*, and what text it stands in for. The driver is
   handed patterns and never learns a symbol's name, deliberately — so an Excel formula marker
   and a Word checkmark never touch the driver file — which means the naming has to happen
   somewhere, and this is where.
3. **The fitting.** Turning a vocabulary entry into something a particular driver can draw
   against a particular reader's braille table.

**The fallback is not a spare tyre.** It is what the add-on writes into the ordinary cell
buffer, so one composed frame serves a Monarch and a Focus 80 alike; it is the key the driver
matches on, which is what scopes a glyph's life to the content it belongs to; and it is what a
caret overrides, because a reader needs the caret more than the symbol. Making it NVDA's own
wording is one less thing to keep in step: the text is right whether or not the drawing
happens.

Knows nothing about which objects get which symbol. That question — where the flow introduces
a glyph — is open, and this file is deliberately usable from any answer to it.
"""

from typing import Callable, NamedTuple, Optional

from logHandler import log

__all__ = [
	"BUTTON",
	"CHECKED",
	"FOCUS",
	"Glyph",
	"PENDING",
	"UNCHECKED",
	"VOCABULARY",
	"cellValue",
	"fittedGlyph",
	"patternRows",
	"supported",
]

CELL_DOTS = {
	1: (0, 0),
	2: (0, 1),
	3: (0, 2),
	7: (0, 3),
	4: (1, 0),
	5: (1, 1),
	6: (1, 2),
	8: (1, 3),
	9: (2, 0),
	10: (2, 1),
	11: (2, 2),
	12: (2, 3),
}
"""Where each dot of a glyph's cell slot is, as (column, row).

**Dots 1 to 8 are exactly braille's own**, and 9 to 12 continue the same rule into the column
braille leaves blank: down the left, down the middle, down the right, with the fourth row last
in each. There is no standard for a three column tactile cell to follow, so this is ours — and
starting from the numbering every braille reader already has is the only sensible place to
start.

Twelve dots is the whole slot at either pitch: `slotSize` is the cell stride by the cell's dot
rows, which is 3 by 4 whether the display is set to 8 rows or 10.
"""

CELL_HEIGHT = 4
"""Dot rows in a cell, and so in a glyph."""

CELL_WIDTH = 3
"""Pin columns in a cell slot, which a glyph fills and braille leaves one of blank."""

GROUP = "|"
"""What separates one cell's dots from the next in a multi cell shape.

Written per cell rather than numbering a nine by four shape from 1 to 36, because a reader
writing one of these is thinking one cell at a time and 1 to 12 is the vocabulary they already
have. "1,2,3|4,5,6" is two cells, each said in its own terms.
"""


class Glyph(NamedTuple):
	"""One symbol: a shape, and the text it is drawn instead of."""

	dots: str
	"""The shape, as dot numbers. Cells separated by `GROUP`, dots within a cell by commas.

	Empty groups are allowed and are how a shape leaves a cell of its run blank.
	"""

	says: str = ""
	"""The text it stands in for, translated through the reader's own braille table.

	This or `fallbackDots`, never both. Use this for anything NVDA already has wording for —
	"btn", "cbo", a state abbreviation — because then the fallback is right by construction and
	the drawing is the only thing this file is deciding.
	"""

	fallbackDots: str = ""
	"""The cells it stands in for, as dot numbers, where no wording exists.

	Braille's own dots 1 to 8 only: this is an ordinary braille cell, not a glyph slot, so 9 to
	12 have nowhere to be and are refused. Written in the same notation as `dots` so that the
	relationship between the two is legible — a focus indicator's fallback is its shape with
	the third column taken away, and said like this you can see that.
	"""

	@property
	def cells(self) -> int:
		""":return: how many cells the shape covers."""
		return len(self.dots.split(GROUP))


def patternRows(dots: str) -> "list[str]":
	"""Turn dot numbers into the rows the driver's buffer is built from.

	:param dots: the shape, in the notation `Glyph.dots` describes.
	:return: one string per dot row, `PinBuffer.fromRows` style.
	:raises ValueError: for a dot number that is not in a cell.
	"""
	groups = dots.split(GROUP)
	raised = [[False] * (CELL_WIDTH * len(groups)) for _ in range(CELL_HEIGHT)]
	for cell, group in enumerate(groups):
		for dot in _numbers(group):
			if dot not in CELL_DOTS:
				raise ValueError(f"{dot} is not a dot of a glyph cell; they run 1 to 12")
			column, row = CELL_DOTS[dot]
			raised[row][cell * CELL_WIDTH + column] = True
	return ["".join("O" if on else "." for on in row) for row in raised]


def cellValue(dots: str) -> int:
	""":return: one braille cell as NVDA writes it, from dot numbers.

	:param dots: dots 1 to 8, comma separated.
	:raises ValueError: for a dot number a braille cell does not have.
	"""
	value = 0
	for dot in _numbers(dots):
		if not 1 <= dot <= 8:
			raise ValueError(f"{dot} is not a dot of a braille cell; a fallback has 1 to 8")
		value |= 1 << (dot - 1)
	return value


def _numbers(group: str) -> "list[int]":
	""":return: the dot numbers in one group, blanks tolerated.

	:param group: a comma separated run of numbers, possibly empty.
	"""
	return [int(part) for part in group.replace(" ", "").split(",") if part]


# --- The vocabulary -----------------------------------------------------------------------
#
# Named because the driver never learns a name. It is handed a pattern and a run of cells, so
# the decision that a solid square means "the focus is here" has to be written down somewhere,
# and a file the add-on and its app modules can all read is the place.
#
# Every entry is a pair for a reason. The shape is what a pin display draws; the fallback is
# what every other display shows, what the driver matches on to know the symbol still belongs
# to the content under it, and what a caret overrides. An entry with a good shape and a poor
# fallback is an entry that reads badly on most hardware.
#
# **The shapes below are first drafts and the fallbacks are not.** A fallback is right or wrong
# and can be checked here: it either is what NVDA writes or it is not, and where it is not the
# glyph simply does not draw and the reader gets the text. A shape is only right if a finger
# says so, and no amount of looking at it settles that. They are set out one line each so that
# revising one after a hardware run is editing that line, which is the whole reason the
# vocabulary is a table of numbers rather than code.

FOCUS = Glyph(dots="1,2,3,4,5,6,9,10,11", fallbackDots="1,2,3,4,5,6")
"""Where the focus is in a list, as Monarch's own firmware draws it.

A solid three by three with the fourth row clear. It is easy to find precisely because it is
square, and square needs three columns — which is why the add-on's present approximation is
dots 3678 twice and does not read the same. The fallback is the shape with its third column
taken away: a solid two by three, which stays recognisable on a Focus.
"""

PENDING = Glyph(dots="3,6,7,8,11,12", fallbackDots="7,8")
"""There is content on this row the add-on has not fetched yet.

The fallback is `flow.PENDING_CELL` — dots 7 and 8, the mark the flow already writes — so this
costs nothing to adopt and changes nothing for a display that cannot draw it.
"""

BUTTON = Glyph(dots="2,3,4,8,9,12|1,4,7,8,9,12|1,4,7,8,10,11", says="btn")
"""A button: a wide slab with its corners cut, nine pins by four.

	.OOOOOOO.
	O.......O
	O.......O
	.OOOOOOO.

The width is the width of what NVDA already writes, which is the whole trick: three cells of
"btn" become nine pins of drawing and nothing after them moves. Rounded rather than square so
that it is not the same shape as a checkbox met at speed — the two are told apart by their
corners and by their length, which is about as much as a fingertip can be asked to notice.
"""

UNCHECKED = Glyph(dots="1,2,3,4,7,8,9,12|1,4,5,6,7,8|", says="( )")
"""An empty checkbox: a hard cornered square, outlined.

	OOOOO....
	O...O....
	O...O....
	OOOOO....

Five pins of the nine, left aligned, because a box the reader has to find is better small and
sharp than large and vague, and the space after it separates it from whatever follows. The
third cell of the run is drawn blank, which is what the empty group at the end of the notation
says.
"""

CHECKED = Glyph(dots="1,2,3,4,5,6,7,8,9,10,11,12|1,2,3,4,5,6,7,8|", says="(x)")
"""A ticked checkbox: the same square, filled.

	OOOOO....
	OOOOO....
	OOOOO....
	OOOOO....

**Filled against outlined, not a drawn tick.** A tick inside a five by four box is three or
four dots that a finger cannot separate from the box around them, where solid against hollow
is the difference between a surface and an edge — which is the one distinction touch makes
instantly and never doubts.
"""

VOCABULARY = {
	"focus": FOCUS,
	"pending": PENDING,
	"button": BUTTON,
	"unchecked": UNCHECKED,
	"checked": CHECKED,
}
"""Every symbol by name, for a caller that has a name rather than a reference.

An app module asking for "formula" wants to look one up rather than import it, and a name is
what survives being written in a settings file or sent across the seam.
"""


# --- Fitting one to a display and a reader ---------------------------------------------------


def supported(driver) -> bool:
	""":return: whether this display has anything to gain from glyphs.

	A display whose cells are already gapless reports the same size for both, and a glyph on it
	would be a braille cell drawn the long way round. Asked rather than assumed, so the answer
	for hardware nobody here has is the honest one.

	:param driver: the live driver, duck typed and never imported.
	"""
	try:
		return tuple(driver.glyphSize) > tuple(driver.cellSize)
	except Exception:
		return False


def fittedGlyph(driver, glyph: Glyph, translate: Optional[Callable] = None):
	"""Build a glyph this driver can draw, for this reader's braille table.

	**The fallback is translated now, not when the vocabulary was written.** "btn" is three
	cells in one table and might be two in another, and the driver matches on the exact bytes
	the add-on wrote — so the cells have to come from the table in force.

	Which is also why a mismatch refuses. A shape drawn for three cells, over a fallback that
	came out two cells long, would be clipped: half a symbol, in a place the reader has learned
	to expect a whole one. The plain text is a better answer than a broken drawing, and it is
	what a display without glyphs was going to show anyway.

	:param driver: the live driver, for its `newGlyph` factory.
	:param glyph: the vocabulary entry.
	:param translate: turns a string into braille cells, or None for the reader's own table.
	:return: the driver's glyph, or None if it should not be drawn.
	"""
	fallback = _fallbackCells(glyph, translate)
	if not fallback:
		return None
	if len(fallback) != glyph.cells:
		log.debugWarning(
			f"BrlMultiline: a glyph of {glyph.cells} cells stands over {len(fallback)} "
			"cells of braille in this table, so the text is being left as it is",
		)
		return None
	try:
		return driver.newGlyph(patternRows(glyph.dots), fallback)
	except Exception:
		log.error("BrlMultiline: a glyph could not be built", exc_info=True)
		return None


def _fallbackCells(glyph: Glyph, translate: Optional[Callable]) -> "list[int]":
	""":return: the braille cells a glyph stands in for, or an empty list.

	:param glyph: the vocabulary entry.
	:param translate: turns a string into braille cells, or None for the reader's own table.
	"""
	if glyph.says and glyph.fallbackDots:
		log.debugWarning("BrlMultiline: a glyph gave both a wording and a fallback shape")
		return []
	if glyph.fallbackDots:
		try:
			return [cellValue(group) for group in glyph.fallbackDots.split(GROUP)]
		except ValueError:
			log.error(f"BrlMultiline: {glyph.fallbackDots} is not a braille fallback", exc_info=True)
			return []
	if not glyph.says:
		return []
	if translate is None:
		translate = _readersTable
	try:
		return list(translate(glyph.says) or [])
	except Exception:
		log.debugWarning("BrlMultiline: a glyph's wording could not be translated", exc_info=True)
		return []


def _readersTable(text: str) -> list:
	""":return: text in the reader's own output table.

	Imported where it is used rather than at the top, because this module is about shapes and
	should not drag the graphics stack in behind it for the one line it borrows.

	:param text: what to translate.
	"""
	from .graphicsMode import labelCells

	return labelCells(text)
