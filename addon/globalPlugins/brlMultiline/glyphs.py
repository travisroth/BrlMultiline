# BrlMultiline: the shapes a pin display can draw in place of braille.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Saying what NVDA says, in shapes rather than in letters.

NVDA already writes short strings to stand for roles and states: "btn" for a button, "cbo" for
a combo box, three cells for a checkbox. They are abbreviations because a braille line has
nothing else to spend — and a display that can raise individual pins does. The cell slot a
letter sits in is three pin columns by four, and nothing says those twelve pins have to spell.

So a glyph is a **drawing standing over the text it replaces**, and it may be narrower than
that text. A shape the same width changes no layout at all: the cell after it starts exactly
where it did. A shape one cell wide over "btn" gives two cells back to the line, which is the
whole point — a Monarch has thirty-two cells and a DotPad twenty, and NVDA spends three of them
saying "button" before the button's name. Moving everything after it is handled rather than
avoided: `compress` shifts the position maps with the cells, so routing and the cursor stay
true, and every cell of the run keeps pointing at the character the run started on — exactly
what NVDA already does, where a routing press anywhere on "btn Search" reaches the button.

Three things live here, and the order they are in is the order they are used.

1. **The notation.** How a human writes a shape down.
2. **The vocabulary.** What each shape *means*, and what text it stands in for. The driver is
   handed patterns and never learns a symbol's name, deliberately — so an Excel formula marker
   and a Word checkmark never touch the driver file — which means the naming has to happen
   somewhere, and this is where.
3. **The fitting.** Turning a vocabulary entry into something a particular driver can draw
   against a particular reader's braille table.

**The fallback is not extra.** It is what the add-on writes into the ordinary cell
buffer, so one composed frame serves a Monarch and a Focus 80 alike; it is the key the driver
matches on, which is what scopes a glyph's life to the content it belongs to; and it is what a
caret overrides, because a reader needs the caret more than the symbol. Making it NVDA's own
wording is one less thing to keep in step: the text is right whether or not the drawing
happens.

Knows nothing about which objects get which symbol. `glyphFlow` is the module that answers
that, by matching what NVDA actually wrote on the line; this file is deliberately usable from
any other answer to it as well.
"""

from typing import Callable, NamedTuple, Optional

from logHandler import log

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

**Dots 1 to 8 are exactly braille's own**, and 9 to 12 continue the same rule into the column
braille leaves blank: down the left, down the middle, down the right, with the fourth row last
in each. There is no standard for a three column tactile cell to follow, so this is ours — and
starting from the numbering every braille reader already has.

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

	stands: str = ""
	"""What NVDA calls the thing this is drawn for: "Role.BUTTON", "State.CHECKED".

	Documentary. Nothing reads it to decide anything, and it is here because a vocabulary
	nobody can check against its source is a vocabulary that drifts from it — the checkbox
	entries were written against a guess at NVDA's wording before anyone looked.
	"""

	describes: str = ""
	"""What the shape is meant to depict, in a phrase.

	Said aloud when a reader points at the catalogue, and printed beside the shape in
	`listing`. A shape whose description is hard to write is usually a shape that will be
	hard to recognise, so writing it is part of choosing it.
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

FOCUS = Glyph(
	dots="1,2,3,4,5,6,9,10,11",
	fallbackDots="1,2,3,4,5,6",
	stands="the add-on's own list focus mark",
	describes="a solid square with the bottom row clear",
)
"""Where the focus is in a list, as Monarch's own firmware draws it.

	OOO
	OOO
	OOO
	...

A solid three by three with the fourth row clear. It is easy to find precisely because it is
square, and square needs three columns — which is why the add-on's present approximation is
dots 3678 twice and does not read the same. The fallback is the shape with its third column
taken away: a solid two by three, which stays recognisable on a Focus.
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

A bar against the right hand edge of the cell, which is the direction the missing content is
in. It was a low block, and that put it in a family with the edit field's baseline and the
separator's rule — three low horizontals a finger has to count rows to tell apart.

The fallback is `flow.PENDING_CELL` — dots 7 and 8, the mark the flow already writes — so this
costs nothing to adopt and changes nothing for a display that cannot draw it.
"""

# --- Controls, over the abbreviations NVDA writes for them ------------------------------------
#
# The saving is the point. "mnuitem" is seven cells of a twenty cell DotPad line before the menu
# item has a name; drawn, it is one. The shapes are meant to look like what they stand for, on
# the grounds that a reader who has met a hollow square and a hollow circle in the same places a
# sighted user meets a checkbox and a radio button has one less arbitrary thing to memorise.
#
# The system across them, which matters more than any single shape:
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

**The fallback is not text.** NVDA writes this state as three literal braille patterns rather
than as an abbreviation — a box drawn in dots 1 to 8, which is the same idea this is, done with
what a braille line has. So the fallback is given as dots, exactly, and does not depend on the
reader's table at all.

Which is also the argument for the glyph. NVDA's box has braille's blank column running through
it, so a filled one reads `O.OO.O` across the middle and a finger meets a broken surface. Fill
the gaps and it is a solid block; and drawn in one cell instead of three it costs two cells
less.
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

Solid against hollow is the difference between a surface and an edge, which is the one
distinction touch makes instantly and never doubts. A tick drawn inside a three by four cell
would be two dots a finger cannot separate from the box around them.
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

Three rows rather than four, so that it is not the circle `NOT_PRESSED` uses. NVDA reports a
radio button's state with the *checkbox* patterns, so a selected radio reads as a diamond
followed by a solid square — which is NVDA's inconsistency rather than this file's, and is
worth a finger before anything is done about it.
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

Six cells saved, which is nearly a third of a DotPad line.
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
	dots="4,6,9,10",
	says="lnk",
	stands="Role.LINK",
	describes="a stroke rising to the right, going somewhere",
)
"""A link: a stroke rising to the right, going somewhere.

	..O
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

Seven cells for one. The largest saving in the vocabulary, and menus are where a braille line
runs out of room fastest.
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

The mirror of `LINK`. Two opposite diagonals is about the largest difference two shapes this
sparse can have, which is what a pair distinguished by one fact rather than by kind wants: the
rising stroke is going somewhere and the falling one is coming back.
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

Three levels and not six, because there are three places along the bottom row and counting
dots is something a braille reader does without thinking. A deeper heading keeps NVDA's own
"h4" and is the better for it: level four is rare, and a shape nobody meets often enough to
learn is worse than three cells that spell it.

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

Four cells saved on a thing that carries no information beyond being there.
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

Here because the choice between the two is real and belongs to the reader rather than to this
file. Compressing buys room and costs the certainty of three familiar letters; drawing in place
buys legibility and costs nothing. Which is better is a question about a display's width and a
reader's habits, and both answers are one line long.
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
	"a": "1", "b": "1,2", "c": "1,4", "d": "1,4,5", "e": "1,5",
	"f": "1,2,4", "g": "1,2,4,5", "h": "1,2,5", "i": "2,4", "j": "2,4,5",
	"k": "1,3", "l": "1,2,3", "m": "1,3,4", "n": "1,3,4,5", "o": "1,3,5",
	"p": "1,2,3,4", "q": "1,2,3,4,5", "r": "1,2,3,5", "s": "2,3,4", "t": "2,3,4,5",
	"u": "1,3,6", "v": "1,2,3,6", "w": "2,4,5,6", "x": "1,3,4,6", "y": "1,3,4,5,6",
	"z": "1,3,5,6",
}
"""The uncontracted braille alphabet, for drawing an abbreviation in `listing`.

**Only for the listing, and only to be looked at.** What actually goes on the display is the
reader's own output table, asked for at the moment of use — see `fittedGlyph`, and the rule
about a contracted table making a wording shorter than the shape drawn over it. This is here so
that the vocabulary can be written out and studied without a running NVDA, and because a
listing that showed a glyph and left the reader to imagine what it replaced was not showing
them the thing they needed to compare.
"""

DIGITS = {
	"1": "1", "2": "1,2", "3": "1,4", "4": "1,4,5", "5": "1,5",
	"6": "1,2,4", "7": "1,2,4,5", "8": "1,2,5", "9": "2,4", "0": "2,4,5",
}
"""The digits, which braille writes as the first ten letters after a numeric indicator.

Here for the same reason as `ALPHABET` and with the same warning: the listing draws with it so
that a vocabulary can be studied without a running NVDA. A heading is the entry that needs it —
NVDA writes "h1", and drawing that as one letter and a blank made the listing report a saving
of one cell where the real one is two.
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

	**Both halves on the same lines.** A shape on its own cannot be judged: the question is
	never "is this a good drawing of a button" but "is this better under a finger than the
	three cells it is taking the place of", and that is a comparison, which needs the two
	things next to each other. An earlier version of this printed them one after the other and
	printed nothing at all for a wording, which left the more important half to be imagined.

	The cell counts and the braille for anything given as a wording are what an uncontracted
	table produces. A contracted one can be shorter, and where it is shorter than the shape the
	glyph is simply not drawn; see `fittedGlyph`.

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

	Two columns per cell rather than three, because that is what braille has — and seeing the
	blank column missing from the picture is most of the argument for drawing the shape
	instead. NVDA's checkbox is a box with a gap running through its middle.

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

	A numeric indicator goes in front of a run of digits, which is what braille does and what
	makes "h1" three cells rather than two. Anything the alphabet has no entry for is drawn as a
	blank cell rather than dropped, so the count still says how many cells the wording costs.

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

	**Shapes cannot be chosen by looking at them.** A drawing that is obvious on a screen can be
	a smudge under a fingertip, two that look quite different can feel the same, and there is no
	way to find that out except by putting them side by side and running a hand along them. So
	the vocabulary comes with a way to feel all of it at once, and revising an entry afterwards
	is one line.

	**Each shape is followed by the braille it replaces**, so a hand running along the panel
	reads the symbol and then the word it stands for, and nothing has to be pointed at to find
	out what it was. That is also the comparison the catalogue is for: not whether a shape is a
	good drawing, but whether it is better under a finger than the cells it is taking the place
	of.

	A blank slot after each pair, because the question is whether one shape is another and
	shapes that touch each other answer it wrongly.

	It holds what it holds. Twenty-two symbols with their braille beside them do not fit on a
	Monarch, so this stops when the panel is full and `listing` is what has all of it.

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

	A braille cell is two dot columns where a glyph slot is three, so a run of cells drawn two
	columns apart would sit at a different pitch from the shape beside it and read as a
	different kind of thing. This puts each cell in its own slot, which is where the display
	would put it.

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

	Which is most of what makes the catalogue usable: a reader running a hand along a row of
	shapes wants to know which one they have just met, and asking them to count is asking them
	to hold the order in mind while judging the shapes.

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

	A display whose cells are already gapless reports the same size for both, and a glyph on it
	would be a braille cell drawn the long way round. Asked rather than assumed, so the answer
	for hardware nobody here has is the honest one.

	Compared per side rather than as tuples. `(3, 4) > (2, 8)` is true of tuples and false of
	pins: a display whose cell is narrower but taller than the slot it reports would have been
	told it had room it does not have, and a shape drawn into it would be clipped. Every side
	has to be at least as big, and one of them bigger, or there is nothing to gain.

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
	return fittedOver(driver, glyph, _fallbackCells(glyph, translate))


def fittedOver(driver, glyph: Glyph, text: "list[int]") -> Optional[Fitted]:
	"""Fit a glyph over the cells that are actually on the line.

	The same rule as `fittedGlyph` and the same result, for the caller that has already found
	the cells rather than needing them translated. `glyphFlow` is that caller: it locates the
	wording in what NVDA wrote and takes the cells from the region's own buffer, which is truer
	than translating the wording a second time — a contraction, a capital sign or a table that
	renders the abbreviation unexpectedly is then accounted for by construction.

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
