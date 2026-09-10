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
	"COMBO",
	"CHECKED",
	"FOCUS",
	"Glyph",
	"PENDING",
	"UNCHECKED",
	"WIDE_BUTTON",
	"VOCABULARY",
	"Fitted",
	"cellValue",
	"compress",
	"fittedGlyph",
	"movedPosition",
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

	carries: str = ""
	"""The braille cell to leave in the buffer where the text was, when this compresses.

	Dot numbers, one cell. It is what a display without glyphs would show and what the
	driver matches on, so something related to the text is better than something arbitrary:
	the default is the first cell of the text itself, which turns "btn" into "b" if the
	drawing ever fails to happen. Set it where a better single cell exists.
	"""

	@property
	def width(self) -> int:
		""":return: how many cells the shape is drawn in.

		This against the width of the text is what decides whether the glyph compresses. One
		group of dots over three cells of text gives two cells back to the line; three groups
		over three cells is drawn in place and gives back nothing but legibility.
		"""
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

BUTTON = Glyph(dots="2,3,5,6,10,11", says="btn")
"""A button: a solid bar across the middle of one cell, where "btn" was.

	...
	OOO
	OOO
	...

Two cells given back, and on a 32 cell Monarch that is a sixteenth of the line; on a 20 cell
DotPad it is a tenth. A bar rather than a box, because the checkbox below is a box and the two
are met in the same places — a symbol's first job is to not be another symbol.
"""

COMBO = Glyph(dots="1,4,9,5", says="cbo")
"""A combo box: a wedge pointing down, where "cbo" was.

	OOO
	.O.
	...
	...

Down because that is where its list comes from, which is the one thing about a combo box a
reader wants reminding of.
"""

UNCHECKED = Glyph(dots="1,2,3,7,4,8,9,10,11,12", says="( )")
"""An empty checkbox: a hollow box in one cell, where "( )" was.

	OOO
	O.O
	O.O
	OOO
"""

CHECKED = Glyph(dots="1,2,3,4,5,6,7,8,9,10,11,12", says="(x)")
"""A ticked checkbox: the same box, filled.

	OOO
	OOO
	OOO
	OOO

**Filled against hollow, not a drawn tick.** A tick inside a three by four cell is two or three
dots a finger cannot separate from the box around them, where solid against hollow is the
difference between a surface and an edge — the one distinction touch makes instantly and never
doubts. It is also the pair that survives being met in a hurry, which is how a checkbox is
usually met.

Worth watching on hardware: this and `FOCUS` differ by one row, the fourth. They are met in
quite different places, so it may never come up — and if it does, the fix is a row.
"""

WIDE_BUTTON = Glyph(dots="2,3,4,8,9,12|1,4,7,8,9,12|1,4,7,8,10,11", says="btn")
"""The same button drawn in place, nine pins by four, giving no cells back.

	.OOOOOOO.
	O.......O
	O.......O
	.OOOOOOO.

Here because the choice between the two is real and belongs to the reader rather than to this
file. Compressing buys room and costs the certainty of three familiar letters; drawing in place
buys legibility and costs nothing. Which is better is a question about a display's width and a
reader's habits, and both answers are one line long.
"""

VOCABULARY = {
	"focus": FOCUS,
	"pending": PENDING,
	"button": BUTTON,
	"combo": COMBO,
	"unchecked": UNCHECKED,
	"checked": CHECKED,
	"wideButton": WIDE_BUTTON,
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


class Fitted(NamedTuple):
	"""A vocabulary entry made ready for one display, one table and one place in the line."""

	drawn: object
	"""The driver's own glyph, built over `cells` and matched against them."""

	cells: "list[int]"
	"""What to write into the buffer where the text was: the text itself where the shape is
	drawn in place, or the single carrier cell where it compresses."""

	replaces: int
	"""How many cells of the original text this takes the place of."""

	@property
	def saved(self) -> int:
		""":return: cells given back to the line, which is zero for a shape drawn in place."""
		return self.replaces - len(self.cells)


def fittedGlyph(driver, glyph: Glyph, translate: Optional[Callable] = None) -> Optional[Fitted]:
	"""Make a vocabulary entry ready for this driver and this reader's braille table.

	**The text is translated now, not when the vocabulary was written.** "btn" is three cells in
	one table and might be two in another, and both what the glyph replaces and what the driver
	matches on have to come from the table in force.

	Then the shape's own width decides what happens. Narrower than the text and it compresses:
	one cell goes in the buffer, the rest of the room goes back to the line, and `compress` is
	what keeps the position maps true. The same width and it is drawn in place over the text,
	which changes no layout at all.

	Wider than the text is refused. A shape needs the cells it is drawn in, and taking one it
	was not given would paint over whatever came next — a symbol and half a letter, which is
	neither.

	:param driver: the live driver, for its `newGlyph` factory.
	:param glyph: the vocabulary entry.
	:param translate: turns a string into braille cells, or None for the reader's own table.
	:return: what to write and what to draw, or None if it should not be drawn.
	"""
	text = _fallbackCells(glyph, translate)
	if not text:
		return None
	if glyph.width > len(text):
		log.debugWarning(
			f"BrlMultiline: a glyph {glyph.width} cells wide stands over {len(text)} "
			"cells of braille in this table, so the text is being left as it is",
		)
		return None
	cells = text if glyph.width == len(text) else [_carrier(glyph, text)]
	try:
		return Fitted(
			drawn=driver.newGlyph(patternRows(glyph.dots), cells),
			cells=cells,
			replaces=len(text),
		)
	except Exception:
		log.error("BrlMultiline: a glyph could not be built", exc_info=True)
		return None


def _carrier(glyph: Glyph, text: "list[int]") -> int:
	""":return: the one cell a compressed glyph leaves behind.

	The entry's own choice where it made one, and otherwise the first cell of the text — so a
	drawing that fails to happen leaves "b" where "btn" was rather than something meaningless.

	:param glyph: the vocabulary entry.
	:param text: the cells it stands over.
	"""
	if not glyph.carries:
		return text[0]
	try:
		return cellValue(glyph.carries)
	except ValueError:
		log.error(f"BrlMultiline: {glyph.carries} is not a braille cell", exc_info=True)
		return text[0]


def compress(
	cells: "list[int]",
	rawToBraille: "list[int]",
	brailleToRaw: "list[int]",
	at: int,
	fitted: Fitted,
) -> tuple:
	"""Put a fitted glyph into a line, moving everything after it and keeping routing true.

	This is the only fiddly part of compressing, and it is fiddly because a braille line is
	three lists that have to agree. The cells are what the display shows. `brailleToRaw` says
	which character each cell came from, and is what a routing press is answered through.
	`rawToBraille` says where each character went, and is what puts the cursor in the right
	place. Shorten the cells without moving the other two and a routing key in the second half
	of the line reaches the wrong word — which is the kind of fault a reader meets long after
	the change that caused it and cannot possibly attribute.

	Every cell of the run keeps pointing at the character the run started on, which is what
	makes a press anywhere on the symbol reach the object: the same thing NVDA already does for
	the abbreviation this replaces, where a routing key on any cell of "btn Search" arrives at
	the button.

	:param cells: the line's cells.
	:param rawToBraille: braille position per character.
	:param brailleToRaw: character position per cell.
	:param at: the cell the text starts on.
	:param fitted: what to put there.
	:return: the three lists again, shortened.
	"""
	after = at + fitted.replaces
	newCells = list(cells[:at]) + list(fitted.cells) + list(cells[after:])
	start = brailleToRaw[at] if at < len(brailleToRaw) else 0
	newBrailleToRaw = (
		list(brailleToRaw[:at]) + [start] * len(fitted.cells) + list(brailleToRaw[after:])
	)
	newRawToBraille = [
		movedPosition(position, at, fitted.replaces, len(fitted.cells))
		for position in rawToBraille
	]
	return newCells, newRawToBraille, newBrailleToRaw


def movedPosition(position: int, at: int, replaces: int, into: int) -> int:
	"""Where a braille position ends up once a run has been replaced by fewer cells.

	Also for a cursor position, which a caller holds separately and which would otherwise be
	left pointing past the end of a line that just got shorter.

	:param position: the position before.
	:param at: the cell the replaced run starts on.
	:param replaces: how many cells it was.
	:param into: how many it became.
	:return: the position after.
	"""
	if position < at:
		return position
	if position >= at + replaces:
		return position - (replaces - into)
	# Inside the run. It is one symbol now, so every part of what it was points at its start.
	return at


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
