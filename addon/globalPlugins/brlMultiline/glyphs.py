# BrlMultiline: the shapes a pin display can draw in place of braille.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Saying what NVDA says, in shapes rather than in letters.

A glyph is a drawing in a cell slot, three pin columns by four, standing over the text NVDA
writes for a role or state ("btn", "cbo"). It may be narrower than that text, giving cells back
to the line; `compress` keeps routing and the cursor true when it does.

In order of use:

1. The notation: how a shape is written down.
2. The vocabulary: what each shape means and what text it stands in for. The driver never
   learns a symbol's name.
3. The fitting: making an entry drawable by a driver against the reader's braille table.

The fallback is what goes in the ordinary cell buffer, what the driver matches on, and what a
caret overrides. Which objects get which symbol is `glyphFlow`'s business. See "Cell glyphs" in
docs/design/monarch-driver-plan.md.
"""

from typing import Callable, NamedTuple, Optional

from logHandler import log

import addonHandler

addonHandler.initTranslation()

__all__ = [
	"Fitted",
	"Glyph",
	"VOCABULARY",
	"catalogue",
	"cellValue",
	"compress",
	"fittedGlyph",
	"fittedOver",
	"listing",
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

Dots 1 to 8 are braille's own; 9 to 12 continue the rule into the third column, fourth row
last. Twelve dots is the whole slot at either pitch.
"""

CELL_HEIGHT = 4
"""Dot rows in a cell, and so in a glyph."""

CELL_WIDTH = 3
"""Pin columns in a cell slot, which a glyph fills and braille leaves one of blank."""

GROUP = "|"
"""What separates one cell's dots from the next in a multi cell shape.

Each cell is numbered 1 to 12 on its own: "1,2,3|4,5,6" is two cells.
"""


class Glyph(NamedTuple):
	"""One symbol: a shape, and the text it is drawn instead of."""

	dots: str
	"""The shape, as dot numbers. Cells separated by `GROUP`, dots within a cell by commas.

	Empty groups are allowed and are how a shape leaves a cell of its run blank.
	"""

	says: str = ""
	"""The text it stands in for, translated through the reader's own braille table.

	This or `fallbackDots`, never both. Use this wherever NVDA has wording, so the fallback is
	right by construction.
	"""

	fallbackDots: str = ""
	"""The cells it stands in for, as dot numbers, where no wording exists.

	Dots 1 to 8 only, since this is an ordinary braille cell; 9 to 12 are refused.
	"""

	stands: str = ""
	"""What NVDA calls the thing this is drawn for: "Role.BUTTON", "State.CHECKED".

	Documentary, so the vocabulary can be checked against NVDA.
	"""

	describes: str = ""
	"""What the shape is meant to depict, in a phrase.

	Said when a reader points at the catalogue, and printed in `listing`.
	"""

	carries: str = ""
	"""The braille cell to leave in the buffer where the text was, when this compresses.

	Dot numbers, one cell. Defaults to the text's first cell, so "btn" becomes "b" if the
	drawing fails. See `_carrier`.
	"""

	@property
	def width(self) -> int:
		""":return: how many cells the shape is drawn in.

		Narrower than its text, the glyph compresses; see `fittedGlyph`.
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
# Each entry pairs a shape with its fallback. The fallbacks must match what NVDA writes, or the
# glyph does not draw; the shapes are drafts, to be judged by touch, one line each to revise.

FOCUS = Glyph(
	dots="1,2,3,4,5,6,9,10,11",
	fallbackDots="3,6,7,8",
	stands="the add-on's own list focus mark",
	describes="a solid square with the bottom row clear",
)
"""Where the focus is in a list, as Monarch's own firmware draws it.

	OOO
	OOO
	OOO
	...

Drawn by `flowControl._markLineFocus` on the mark's first cell, the second left blank. The
fallback is `flowIndent.FOCUS_CELL`; where nothing draws, the mark stays dots 3678 twice.
"""

PENDING = Glyph(
	dots="9,10,11,12",
	fallbackDots="7,8",
	stands="the add-on's own not-yet-fetched mark",
	describes="a bar against the right edge, where the missing content is",
)
"""There is content on this row the add-on has not fetched yet.

	..O
	..O
	..O
	..O

A bar at the right edge, the direction the missing content is in. The fallback is
`flow.PENDING_CELL`.
"""

# --- Controls, over the abbreviations NVDA writes for them ------------------------------------
#
# The system across the shapes:
#
#   hollow means off or empty, filled means on or checked
#   a square is a checkbox, a circle is a radio button, a slab is a button
#   a rule low in the cell is a place to type, a chevron points where a thing opens

BUTTON = Glyph(
	dots="2,3,5,6,10,11",
	says="btn",
	stands="Role.BUTTON",
	describes="a solid slab across the middle, the face of a key",
)
"""A button: a slab across the middle of the cell.

	...
	OOO
	OOO
	...
"""

TOGGLE = Glyph(
	dots="2,3,10,11",
	says="tgbtn",
	stands="Role.TOGGLEBUTTON",
	describes="two posts with the slab taken out, a button with two states",
)
"""A toggle button: two posts with the slab taken out, because it has two states.

	...
	O.O
	O.O
	...
"""

CHECKED = Glyph(
	dots="1,2,3,4,5,6,7,8,9,10,11,12",
	fallbackDots="1,2,3,4,7,8|1,2,3,4,5,6,7,8|1,4,5,6,7,8",
	stands="State.CHECKED, and State.ON",
	describes="a solid square, a box with something in it",
)
"""A ticked checkbox: a solid square.

	OOO
	OOO
	OOO
	OOO

The fallback is dots, since NVDA writes this state as three braille patterns rather than as
text.
"""

UNCHECKED = Glyph(
	dots="1,2,3,4,7,8,9,10,11,12",
	fallbackDots="1,2,3,4,7,8|7,8|1,4,5,6,7,8",
	stands="State.CHECKED absent, and State.ON absent",
	describes="a hollow square, an empty box",
)
"""An empty checkbox: a hollow square.

	OOO
	O.O
	O.O
	OOO
"""

HALF_CHECKED = Glyph(
	dots="1,2,3,4,6,7,8,9,10,11,12",
	fallbackDots="1,2,3,4,7,8|4,5,6,7,8|1,4,5,6,7,8",
	stands="State.HALFCHECKED",
	describes="a square filled from the middle down, neither one thing nor the other",
)
"""A checkbox that is neither: the square filled from the middle down.

	OOO
	O.O
	OOO
	OOO
"""

PRESSED = Glyph(
	dots="2,3,4,5,6,8,10,11",
	fallbackDots="2,3,4,8|1,2,3,4,5,6,7,8|1,5,6,7",
	stands="State.PRESSED",
	describes="a filled circle, the rounded box NVDA draws for this",
)
"""A pressed toggle: a filled circle, matching the rounded box NVDA draws for this state.

	.O.
	OOO
	OOO
	.O.
"""

NOT_PRESSED = Glyph(
	dots="2,3,4,8,10,11",
	fallbackDots="2,3,4,8|7,8|1,5,6,7",
	stands="State.PRESSED absent",
	describes="a hollow circle",
)
"""A toggle that is not pressed: the same circle, hollow.

	.O.
	O.O
	O.O
	.O.
"""

RADIO = Glyph(
	dots="2,4,6,10",
	says="rbtn",
	stands="Role.RADIOBUTTON",
	describes="a diamond, round where a checkbox is square",
)
"""A radio button: a diamond, round-ish where a checkbox is square.

	.O.
	O.O
	.O.
	...

Three rows, so it is not the circle `NOT_PRESSED` uses. NVDA reports its state with the
checkbox patterns.
"""

EDIT = Glyph(
	dots="7,8,12",
	says="edt",
	stands="Role.EDITABLETEXT",
	describes="a rule along the bottom, the line writing sits on",
)
"""An edit field: a rule along the bottom, which is where writing sits.

	...
	...
	...
	OOO
"""

PASSWORD = Glyph(
	dots="2,7,8,10,12",
	says="pwdedt",
	stands="Role.PASSWORDEDIT",
	describes="the writing rule with two dots over it, for what it hides",
)
"""A password field: the same rule with two dots floating over it, for what it hides.

	...
	O.O
	...
	OOO
"""

COMBO = Glyph(
	dots="1,4,5,9",
	says="cbo",
	stands="Role.COMBOBOX",
	describes="a wedge pointing down, where its list comes from",
)
"""A combo box: a wedge pointing down, where its list comes from.

	OOO
	.O.
	...
	...
"""

SUBMENU = Glyph(
	dots="1,3,5,10",
	says="submnu",
	stands="State.HASPOPUP",
	describes="a chevron pointing right, where the submenu opens",
)
"""There is a submenu here: a chevron pointing right, where it opens.

	O..
	.Oo
	O..
	...
"""

LINK = Glyph(
	dots="7,6,10,12",
	says="lnk",
	stands="Role.LINK",
	describes="a stroke rising to the right, going somewhere",
)
"""A link: a stroke rising to the right, going somewhere.

	...
	..o
	.o.
	o..
"""

LIST = Glyph(
	dots="1,3,4,6,9,11",
	says="lst",
	stands="Role.LIST",
	describes="two rules stacked, items one above another",
)
"""A list: two rules, one above the other.

	OOO
	...
	OOO
	...
"""

MENU_ITEM = Glyph(
	dots="1,2,3,5,10",
	says="mnuitem",
	stands="Role.MENUITEM",
	describes="a rule with an upright at its left, one entry of a menu",
)
"""One item of a menu: a rule with an upright at its left.

	O..
	OOO
	O..
	...
"""

TABLE = Glyph(
	dots="1,2,3,4,6,7,9,10,11,12",
	says="tbl",
	stands="Role.TABLE",
	describes="two cells stacked with a rule between, a grid",
)
"""A table: two cells stacked, which is what one is.

	OOO
	O.O
	OOO
	O.O
"""

GRAPHIC = Glyph(
	dots="3,5,6,7,8,11,12",
	says="gra",
	stands="Role.GRAPHIC",
	describes="a shape standing on the ground, a picture of something",
)
"""A graphic: a shape standing on the ground, which is what a picture of anything is.

	...
	.O.
	OOO
	OOO
"""

PROGRESS = Glyph(
	dots="1,2,3,4,5,6,7,8,9,12",
	says="prgbar",
	stands="Role.PROGRESSBAR",
	describes="a box filling from the left, which is what one does",
)
"""A progress bar: a box filled from the left, which is what one does.

	OOO
	OO.
	OO.
	OOO
"""

VISITED_LINK = Glyph(
	dots="1,5,11,12",
	says="vlnk",
	stands="Role.LINK with State.VISITED",
	describes="a stroke falling to the right, somewhere you have been",
)
"""A link already followed.

The mirror of `LINK`: the falling stroke is coming back.
"""

HEADING_1 = Glyph(
	dots="1,4,7,9",
	says="h1",
	stands="Role.HEADING at level 1",
	describes="a rule at the top with one dot under it",
)
HEADING_2 = Glyph(
	dots="1,4,7,8,9",
	says="h2",
	stands="Role.HEADING at level 2",
	describes="a rule at the top with two dots under it",
)
HEADING_3 = Glyph(
	dots="1,4,7,8,9,12",
	says="h3",
	stands="Role.HEADING at level 3",
	describes="a rule at the top with three dots under it",
)
"""The heading family: one roof, and the level counted along the bottom.

Three levels, one per place along the bottom row. A deeper heading keeps NVDA's "h4".
"""

SEPARATOR = Glyph(
	dots="3,6,11",
	fallbackDots="3,6|3,6|3,6|3,6|3,6",
	stands="Role.SEPARATOR",
	describes="one rule where NVDA draws five cells of dashes",
)
"""A separator: one rule, where NVDA writes five cells of dashes.

	...
	...
	OOO
	...
"""

WIDE_BUTTON = Glyph(
	dots="2,3,4,8,9,12|1,4,7,8,9,12|1,4,7,8,10,11",
	says="btn",
	stands="Role.BUTTON, drawn in place",
	describes="a wide slab with its corners cut, three cells of drawing",
)
"""The same button drawn in place, nine pins by four, giving no cells back.

	.OOOOOOO.
	O.......O
	O.......O
	.OOOOOOO.

The alternative to compressing, for a reader who would rather keep the cells.
"""

VOCABULARY = {
	"focus": FOCUS,
	"pending": PENDING,
	"button": BUTTON,
	"toggle": TOGGLE,
	"checked": CHECKED,
	"unchecked": UNCHECKED,
	"halfChecked": HALF_CHECKED,
	"pressed": PRESSED,
	"notPressed": NOT_PRESSED,
	"radio": RADIO,
	"edit": EDIT,
	"password": PASSWORD,
	"combo": COMBO,
	"submenu": SUBMENU,
	"link": LINK,
	"visitedLink": VISITED_LINK,
	"heading1": HEADING_1,
	"heading2": HEADING_2,
	"heading3": HEADING_3,
	"list": LIST,
	"menuItem": MENU_ITEM,
	"table": TABLE,
	"graphic": GRAPHIC,
	"progress": PROGRESS,
	"separator": SEPARATOR,
	"wideButton": WIDE_BUTTON,
}
"""Every symbol by name, for a caller that has a name rather than a reference.

An app module asking for "formula" wants to look one up rather than import it, and a name is
what survives being written in a settings file or sent across the seam.
"""


# --- Fitting one to a display and a reader ---------------------------------------------------


ALPHABET = {
	"a": "1",
	"b": "1,2",
	"c": "1,4",
	"d": "1,4,5",
	"e": "1,5",
	"f": "1,2,4",
	"g": "1,2,4,5",
	"h": "1,2,5",
	"i": "2,4",
	"j": "2,4,5",
	"k": "1,3",
	"l": "1,2,3",
	"m": "1,3,4",
	"n": "1,3,4,5",
	"o": "1,3,5",
	"p": "1,2,3,4",
	"q": "1,2,3,4,5",
	"r": "1,2,3,5",
	"s": "2,3,4",
	"t": "2,3,4,5",
	"u": "1,3,6",
	"v": "1,2,3,6",
	"w": "2,4,5,6",
	"x": "1,3,4,6",
	"y": "1,3,4,5,6",
	"z": "1,3,5,6",
}
"""The uncontracted braille alphabet, for drawing an abbreviation in `listing`.

Only for the listing, which runs without NVDA. The display uses the reader's own table; see
`fittedGlyph`.
"""

DIGITS = {
	"1": "1",
	"2": "1,2",
	"3": "1,4",
	"4": "1,4,5",
	"5": "1,5",
	"6": "1,2,4",
	"7": "1,2,4,5",
	"8": "1,2,5",
	"9": "2,4",
	"0": "2,4,5",
}
"""The digits, which braille writes as the first ten letters after a numeric indicator.

For the listing only, as `ALPHABET`; headings need it.
"""

NUMBER = "3,4,5,6"
"""The numeric indicator, which is why "h1" is three cells of braille and not two."""

LISTING_GAP = 11
"""Characters the glyph column is padded to in `listing`, so the two halves line up.

The widest shape is three cells, which is nine, and two spaces after it. Fixed rather than
fitted to each entry, because the point of the column is that a reader can run down it.
"""


def listing(entries: Optional[dict] = None) -> str:
	"""The vocabulary written out, each glyph beside the braille it replaces.

	Both on the same lines, for comparison. Wordings are drawn uncontracted; a contracted
	table can be shorter, and then the glyph is not drawn. See `fittedGlyph`.

	:param entries: what to write out, by name. None for the whole vocabulary.
	:return: the listing, as plain text.
	"""
	entries = VOCABULARY if entries is None else entries
	lines = []
	saved = 0
	for name, glyph in entries.items():
		was = _replacedRows(glyph)
		over = len(was[0]) // 2
		gain = max(0, over - glyph.width)
		saved += gain
		lines.append(f"{name} -- {glyph.stands or 'nothing named'}")
		lines.append(f"    {glyph.describes or 'undescribed'}")
		lines.append(f"    dots {glyph.dots}")
		cells = "cell" if glyph.width == 1 else "cells"
		lines.append(f"    {glyph.width} {cells} in place of {over}, saving {gain}")
		lines.append(f"    {_written(glyph)}")
		for drawn, before in zip(patternRows(glyph.dots), was):
			lines.append(f"    {drawn:<{LISTING_GAP}}< {before}")
		lines.append("")
	lines.append(f"{len(entries)} symbols, {saved} cells given back.")
	return "\n".join(lines)


def _written(glyph: Glyph) -> str:
	""":return: how to describe what NVDA puts on the line for this.

	:param glyph: the vocabulary entry.
	"""
	if glyph.says:
		return f'NVDA writes "{glyph.says}"'
	return "NVDA writes braille patterns, not text"


def _replacedRows(glyph: Glyph) -> "list[str]":
	"""Draw what a glyph is standing in place of, whichever way it was written.

	Two columns per cell, as braille has.

	:param glyph: the vocabulary entry.
	:return: one string per dot row.
	"""
	if glyph.fallbackDots:
		groups = glyph.fallbackDots.split(GROUP)
	else:
		groups = _spelled(glyph.says)
	raised = [[False] * (2 * len(groups)) for _ in range(CELL_HEIGHT)]
	for cell, group in enumerate(groups):
		for dot in _numbers(group):
			column, row = CELL_DOTS[dot]
			raised[row][cell * 2 + column] = True
	return ["".join("O" if on else "." for on in row) for row in raised]


def _spelled(says: str) -> "list[str]":
	""":return: one cell's dots per cell a wording takes, for the listing to draw.

	A numeric indicator goes before a run of digits. Anything without an entry is a blank cell,
	so the count stays true.

	:param says: the wording, as NVDA writes it.
	"""
	groups = []
	inNumber = False
	for character in says:
		if character in DIGITS:
			if not inNumber:
				groups.append(NUMBER)
				inNumber = True
			groups.append(DIGITS[character])
			continue
		inNumber = False
		groups.append(ALPHABET.get(character.lower(), ""))
	return groups


SLOT = 2
"""Cell slots each entry of the catalogue is given: one for the shape, one for air after it."""

LINE = CELL_HEIGHT + 2
"""Pin rows from one row of the catalogue to the next: a cell, and two rows of air."""


def catalogue(newBuffer: Callable, width: int, height: int, entries: Optional[dict] = None):
	"""Lay every symbol out on the panel so a finger can compare them.

	Each shape is followed by the braille it replaces, then a blank slot. Stops when the panel
	is full; `listing` has all of it.

	:param newBuffer: makes a blank buffer of a given width and height.
	:param width: the panel's width in pins.
	:param height: its height in pins.
	:param entries: what to lay out, by name. None for the whole vocabulary.
	:return: (buffer, describeAt), where `describeAt` names the symbol under a point.
	"""
	entries = VOCABULARY if entries is None else entries
	buffer = newBuffer(width, height)
	if buffer is None:
		return None, None
	placed = []
	x, y = 0, 0
	for name, glyph in entries.items():
		was = _replacedRows(glyph)
		span = (glyph.width + len(was[0]) // 2 + 1) * CELL_WIDTH
		if x + span > width:
			x, y = 0, y + LINE
		if y + CELL_HEIGHT > height:
			# Out of panel. Better a catalogue that stops than one that overwrites its own
			# first row, which would read as a symbol nobody wrote. The listing is the one
			# that holds everything; this is the one that can be felt.
			break
		_stamp(buffer, patternRows(glyph.dots), x, y)
		_stamp(buffer, _spaced(was), x + glyph.width * CELL_WIDTH, y)
		placed.append((name, glyph, x, y, span - CELL_WIDTH))
		x += span
	return buffer, _namer(placed)


def _stamp(buffer, rows: "list[str]", x: int, y: int) -> None:
	"""Draw one shape at a place on the panel.

	:param buffer: what to draw on.
	:param rows: the shape.
	:param x: its left edge.
	:param y: its top row.
	"""
	for down, row in enumerate(rows):
		for across, character in enumerate(row):
			if character not in " .":
				buffer.setDot(x + across, y + down)


def _spaced(rows: "list[str]") -> "list[str]":
	"""Space braille out to the pitch a glyph is drawn at.

	Each two column braille cell goes in its own three column slot, as the display puts it.

	:param rows: the braille, two columns per cell.
	:return: the same, three columns per cell.
	"""
	spaced = []
	for row in rows:
		out = []
		for index in range(0, len(row), 2):
			out.append(row[index : index + 2] + ".")
		spaced.append("".join(out))
	return spaced


def _namer(placed: "list[tuple]") -> Callable:
	"""Build the lookup from a point of the catalogue back to the symbol there.

	:param placed: (name, glyph, x, y, width) per symbol drawn.
	:return: a function taking a point and returning what is there.
	"""

	def describeAt(x: int, y: int) -> Optional[str]:
		for name, glyph, left, top, width in placed:
			if left <= x < left + width + CELL_WIDTH and top <= y < top + LINE:
				# Translators: reported for a touch on the glyph catalogue. Placeholders are
				# the symbol's name and what its shape is meant to depict.
				return _("{name}, {shape}").format(name=name, shape=glyph.describes or "")
		return None

	return describeAt


def supported(driver) -> bool:
	""":return: whether this display has anything to gain from glyphs.

	True when the slot is at least the braille cell's size on every side and bigger on one.
	Compared per side, since `(3, 4) > (2, 8)` is true of tuples and false of pins.

	:param driver: the live driver, duck typed and never imported.
	"""
	try:
		slot = tuple(int(side) for side in driver.glyphSize)
		cell = tuple(int(side) for side in driver.cellSize)
	except Exception:
		return False
	if len(slot) != len(cell) or not slot:
		return False
	return all(theirs >= ours for theirs, ours in zip(slot, cell)) and slot != cell


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

	The text is translated now, through the table in force. A shape narrower than it
	compresses (see `compress`); the same width is drawn in place; wider is refused, as it
	would paint over what follows.

	:param driver: the live driver, for its `newGlyph` factory.
	:param glyph: the vocabulary entry.
	:param translate: turns a string into braille cells, or None for the reader's own table.
	:return: what to write and what to draw, or None if it should not be drawn.
	"""
	return fittedOver(driver, glyph, _fallbackCells(glyph, translate))


def fittedOver(driver, glyph: Glyph, text: "list[int]") -> Optional[Fitted]:
	"""Fit a glyph over the cells that are actually on the line.

	As `fittedGlyph`, for a caller such as `glyphFlow` that already has the cells from the
	region's buffer, which accounts for contractions by construction.

	:param driver: the live driver, for its `newGlyph` factory.
	:param glyph: the vocabulary entry.
	:param text: the cells the shape would stand over.
	:return: what to write and what to draw, or None if it should not be drawn.
	"""
	if not text:
		return None
	if glyph.width > len(text):
		log.debugWarning(
			f"BrlMultiline: a glyph {glyph.width} cells wide stands over {len(text)} "
			"cells of braille in this table, so the text is being left as it is",
		)
		return None
	cells = list(text) if glyph.width == len(text) else [_carrier(glyph, text)]
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

	The entry's own choice, else the text's first cell.

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

	The cells and both position maps move together, or routing in the rest of the line reaches
	the wrong word. Every cell of the run points at the run's first character, as NVDA does for
	"btn".

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
	newBrailleToRaw = list(brailleToRaw[:at]) + [start] * len(fitted.cells) + list(brailleToRaw[after:])
	newRawToBraille = [
		movedPosition(position, at, fitted.replaces, len(fitted.cells)) for position in rawToBraille
	]
	return newCells, newRawToBraille, newBrailleToRaw


def movedPosition(position: int, at: int, replaces: int, into: int) -> int:
	"""Where a braille position ends up once a run has been replaced by fewer cells.

	Also for a cursor position the caller holds separately.

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

	Imported here, to keep the graphics stack out of this module's imports.

	:param text: what to translate.
	"""
	from .graphicsMode import labelCells

	return labelCells(text)
