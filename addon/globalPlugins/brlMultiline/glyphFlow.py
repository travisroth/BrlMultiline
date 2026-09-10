# BrlMultiline: putting the glyph vocabulary onto the line NVDA wrote.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Where a symbol actually replaces something the reader would otherwise have to read.

`glyphs` holds the shapes and the arithmetic and deliberately knows nothing about which object
gets which one. This is the module that answers that question, and the answer is: **whichever
one NVDA already wrote a word for.**

NVDA writes "btn" before a button's name, "cbo" before a combo box, and three cells of braille
patterns for a checked box. It does that because a braille line has nothing else to spend. So
the wiring is not a new decision about what to announce — it is a substitution over what NVDA
decided already, which is why it can be switched on without changing a single thing about what
the display says. Every symbol stands over a token NVDA put there, the fallback is that token's
own cells, and turning the setting off gives the words back.

Three things follow from doing it that way, and all three are the reason for doing it that way.

1. **Nothing has to be announced twice.** NVDA's own rules decide when a role or a state is
   worth the cells; this never adds one and never removes one.
2. **The cells are taken from the region's own buffer**, not translated a second time. Whatever
   the reader's table made of "btn" is what the glyph stands over and what the driver matches
   on, so a contracted table cannot leave a shape drawn over the wrong number of cells.
3. **Failing means showing the word.** Every path out of here that cannot draw a shape leaves
   the line exactly as NVDA wrote it. A reader who loses a glyph loses nothing but the shape.

**Everything the display shows, not only a flow.** A menu bar in File Explorer is full of
buttons and never goes near browse mode; the three cells NVDA spends saying "btn" cost the same
there and the shape saves the same. So the substitution is made where every region is read —
`segments.BrailleBufferSegment.update` for an ordinary segment, `flowRender._layoutBuffer` for a
block of a flow — rather than anywhere that knows what kind of content it is looking at.

**Position within the line does not matter.** NVDA is not consistent about which side of the
name the word goes: browse mode writes "btn Search" and an ordinary window writes it the other
way about. Matching whole words wherever they fall in the line is what makes that somebody
else's problem rather than this module's.

**Before the line is cut, in both places.** That is the difference between a symbol and a
saving: compressing after the row was cut would shorten a row and change nothing about how much
fits on it.

**Where it is drawn.** The marks left on the region say which of its cells are symbols. An
ordinary segment turns those into places on itself, a flow band asks its controller
(`flowControl.cellGlyphs`), and `container` turns either into places on the display and hands
them to the driver. It is done afresh on every frame, because the driver retires a glyph the
moment a frame arrives without its cells — which is what stops a symbol outliving the object it
belonged to.
"""

from typing import NamedTuple, Optional

from logHandler import log

from . import bmConfig, glyphs
from .devices import VIRTUAL_DISPLAY_NAME

MARKS = "brlMultilineGlyphs"
"""Attribute a compressed region carries: `{cell index: the fitted glyph starting there}`.

On the region rather than in a table of our own, because the region is the thing every stage
after this already has in its hand — the renderer, the controller and routing all reach for it
by block — and a side table would need keeping in step with regions being built and dropped.
"""

STAMP = "_brlMultilineGlyphCells"
"""Attribute holding the cells `MARKS` was worked out over.

A region is laid out several times per reading — once per width the indent leaves — and each
of those would otherwise re-find and re-fit every symbol. More importantly the second pass
would find nothing to save, the text having already been compressed, and would replace a good
set of marks with an empty one. So the cells are the receipt: unchanged means the marks still
describe them, and a fresh `update` rebuilds them and invalidates it by construction.
"""

MOVED = ("brailleCursorPos", "brailleSelectionStart", "brailleSelectionEnd")
"""The region's other positions into its cells, which have to move when the cells do.

`compress` keeps the two position maps true. These three are single positions NVDA worked out
from those maps before we shortened them, and a cursor left where the uncompressed line put it
is a cursor one word to the right of the letter it belongs to.
"""

ROLE_GLYPHS = {
	"BUTTON": "button",
	"TOGGLEBUTTON": "toggle",
	"RADIOBUTTON": "radio",
	"EDITABLETEXT": "edit",
	"PASSWORDEDIT": "password",
	"COMBOBOX": "combo",
	"LINK": "link",
	"LIST": "list",
	"MENUITEM": "menuItem",
	"TABLE": "table",
	"GRAPHIC": "graphic",
	"PROGRESSBAR": "progress",
	"SEPARATOR": "separator",
}
"""Which vocabulary entry stands over each of NVDA's role abbreviations, by member name.

Keyed on `controlTypes.Role.<name>.name` rather than on the abbreviation, so that the word this
replaces is read out of NVDA at run time. A reader running NVDA in French gets "btn" translated
to whatever their braille labels say and the shape still lands on it; keying on the English
string would have quietly given them nothing, which is the failure nobody reports because it
looks exactly like the feature being off.
"""

POSITIVE_GLYPHS = {
	"CHECKED": "checked",
	"HALFCHECKED": "halfChecked",
	"ON": "checked",
	"PRESSED": "pressed",
	"HASPOPUP": "submenu",
}
"""Which entry stands over each state NVDA writes when the state is present.

`ON` and `CHECKED` share a shape because NVDA writes them the same three cells — a switch that
is on and a box that is checked are one picture in braille today, and inventing a difference
here would be this add-on saying something NVDA does not.
"""

NEGATIVE_GLYPHS = {
	"CHECKED": "unchecked",
	"ON": "unchecked",
	"PRESSED": "notPressed",
}
"""Which entry stands over each state NVDA writes when the state is absent."""

try:
	from braille.constants import TEXT_SEPARATOR as SEPARATOR
except Exception:  # pragma: no cover - an NVDA that has moved it
	SEPARATOR = " "
"""What NVDA parts one thing on a braille line from the next by.

Taken from NVDA rather than assumed, with a space behind it so that this module can still say
what a whole word is if the constant ever moves. Whole words are the whole of the safety here:
"btn" as a token is a role NVDA wrote, and "btn" inside a word is somebody's text.
"""

_TOKENS: Optional[dict] = None
"""The token table, built once. Cleared by `forget`."""


class Target(NamedTuple):
	"""The display a glyph would be drawn on, and where its rows start.

	The same shape of answer `graphics.findSurface` gives and for the same reason: a member of
	a composite knows nothing about the rows above it, so a place on NVDA's display has to be
	brought back to a place on the display that will draw it.
	"""

	driver: object
	"""The driver publishing the glyph capability."""

	rowStart: int
	"""Which row of NVDA's display this driver's first row is."""

	numRows: int
	"""How many rows it has."""

	numCols: int
	"""How many cells to a row it has."""

	def indexFor(self, row: int, col: int) -> Optional[int]:
		""":return: the driver's own flat cell index, or None if the place is not on it.

		:param row: the row on NVDA's display.
		:param col: the column on NVDA's display.
		"""
		local = row - self.rowStart
		if not 0 <= local < self.numRows or not 0 <= col < self.numCols:
			return None
		return local * self.numCols + col


def canDraw(driver) -> bool:
	""":return: whether a driver offers glyphs and has anything to gain from them.

	Duck typed and never imported, like every other capability here. Deliberately not asked
	through `graphics.findSurface`: that one duck types the four *graphics* methods, and a
	display that can draw a glyph in a cell without being able to hold a picture over the whole
	panel is a display this would otherwise have skipped.

	:param driver: any object, including None.
	"""
	if driver is None:
		return False
	if not all(hasattr(driver, name) for name in ("newGlyph", "setCellGlyphs", "glyphSize", "cellSize")):
		return False
	return glyphs.supported(driver)


def glyphTarget(display=None) -> Optional[Target]:
	"""Find the display that can draw glyphs, whether or not it is behind a composite.

	:param display: the display to look at, or None for the one NVDA is driving.
	:return: the target, or None where nothing attached can draw one. Not an error: it is the
		ordinary case on ordinary hardware, and the words stay on the line.
	"""
	if display is None:
		import braille

		handler = braille.handler
		display = handler.display if handler is not None else None
	if display is None:
		return None
	if canDraw(display):
		return _targetFor(display, rowStart=0)
	if getattr(display, "name", None) != VIRTUAL_DISPLAY_NAME:
		return None
	try:
		slots = list(display.slots)
	except Exception:
		log.error("BrlMultiline: could not read the composite's members to find one that draws", exc_info=True)
		return None
	for slot in slots:
		# A member the composite has given up on has had its rows taken out of the geometry, so
		# a cell index against its band would name a cell that now belongs to someone else.
		if getattr(slot, "failed", False):
			continue
		driver = getattr(slot, "driver", None)
		if not canDraw(driver):
			continue
		try:
			rowStart = slot.band.rowStart
		except Exception:
			log.error("BrlMultiline: a member that draws would not say where its band starts", exc_info=True)
			continue
		return _targetFor(driver, rowStart=rowStart)
	return None


def _targetFor(driver, rowStart: int) -> Optional[Target]:
	""":return: a driver's geometry, or None if it would not say or it makes no sense.

	:param driver: a driver already known to publish the capability.
	:param rowStart: where its first row sits on NVDA's display.
	"""
	try:
		numRows = int(driver.numRows)
		numCols = int(driver.numCols)
	except Exception:
		log.error("BrlMultiline: a display that draws glyphs would not say its size", exc_info=True)
		return None
	if min(numRows, numCols) <= 0:
		return None
	return Target(driver=driver, rowStart=rowStart, numRows=numRows, numCols=numCols)


def enabled() -> bool:
	""":return: whether the reader has asked for symbols on this display."""
	try:
		return bmConfig.shouldDrawGlyphs()
	except Exception:
		log.debugWarning("Could not read whether glyphs are wanted", exc_info=True)
		return False


def forget() -> None:
	"""Drop the token table, so that a changed braille table or language is picked up.

	Called when the display or the configuration changes. Cheap to rebuild and wrong to keep:
	the labels are translated strings, and a profile switch can change the language.
	"""
	global _TOKENS
	_TOKENS = None


def tokens() -> dict:
	""":return: every word NVDA writes that a shape stands over, to the entry that stands there.

	Built from `braille.labels` where it can be read, so the words are NVDA's own in NVDA's own
	language. Where it cannot — a test, or an NVDA that has moved them — the vocabulary's own
	English wordings are used, which is what it was written against.
	"""
	global _TOKENS
	if _TOKENS is None:
		_TOKENS = _buildTokens()
	return _TOKENS


def _buildTokens() -> dict:
	""":return: the token table. See `tokens`."""
	found = _fromNVDA()
	if found:
		return found
	log.debugWarning("BrlMultiline: using this add-on's own wordings for glyphs, not NVDA's")
	return _fromVocabulary()


def _fromNVDA() -> dict:
	""":return: the token table read out of NVDA's braille labels, or empty if they cannot be."""
	try:
		from braille.labels import negativeStateLabels, positiveStateLabels, roleLabels
	except Exception:
		log.debugWarning("BrlMultiline: NVDA's braille labels could not be read", exc_info=True)
		return {}
	found: dict = {}
	for labels, wanted in (
		(roleLabels, ROLE_GLYPHS),
		(positiveStateLabels, POSITIVE_GLYPHS),
		(negativeStateLabels, NEGATIVE_GLYPHS),
	):
		try:
			written = list(labels.items())
		except Exception:
			log.debugWarning("BrlMultiline: a set of braille labels could not be walked", exc_info=True)
			continue
		for member, label in written:
			name = wanted.get(getattr(member, "name", ""))
			if name is None or not label:
				continue
			_offer(found, label, name)
	return found


def _fromVocabulary() -> dict:
	""":return: the token table built from the vocabulary's own wordings."""
	found: dict = {}
	for name in set(ROLE_GLYPHS.values()) | set(POSITIVE_GLYPHS.values()) | set(NEGATIVE_GLYPHS.values()):
		glyph = glyphs.VOCABULARY.get(name)
		if glyph is None:
			continue
		_offer(found, glyph.says or _brailleText(glyph), name)
	return found


def _offer(found: dict, label: str, name: str) -> None:
	"""Put one word in the table, if there is a shape for it that would save anything.

	A shape as wide as the word is refused here rather than at fitting time, so that a
	vocabulary entry meant to be drawn in place — the wide button, which exists to be compared
	with the narrow one — cannot claim the same word as the entry that compresses it.

	:param found: the table being built.
	:param label: what NVDA writes.
	:param name: the vocabulary entry's name.
	"""
	glyph = glyphs.VOCABULARY.get(name)
	if glyph is None:
		log.debugWarning(f"BrlMultiline: no glyph called {name}")
		return
	if glyph.width >= len(label):
		return
	found[label] = glyph


def _brailleText(glyph) -> str:
	""":return: a dotted fallback as the braille characters NVDA would have written.

	NVDA writes a checkbox as three literal braille pattern characters rather than as a word.
	The vocabulary records the same three cells in this add-on's own dot notation, because that
	is the notation the shapes are written in, so this converts one to the other rather than
	writing the characters down twice and letting them disagree.

	:param glyph: the vocabulary entry.
	"""
	if not glyph.fallbackDots:
		return ""
	try:
		return "".join(
			chr(0x2800 + glyphs.cellValue(group)) for group in glyph.fallbackDots.split(glyphs.GROUP)
		)
	except ValueError:
		log.error(f"BrlMultiline: {glyph.fallbackDots} is not a braille fallback", exc_info=True)
		return ""


def marksIn(rawText: str) -> "list[tuple]":
	"""Find every word in a line that a shape stands over.

	**Whole words only.** NVDA joins the things it says about an object with a single space, so
	a role is always a token of its own; "btn" inside somebody's text is their text. Matching
	anywhere in the line would have put a button on the middle of a word, which is the kind of
	fault a reader meets as a shape that means nothing and has no way to trace.

	:param rawText: the line as NVDA wrote it.
	:return: (start, end, glyph) per word found, left to right.
	"""
	table = tokens()
	if not table or not rawText:
		return []
	found = []
	start = 0
	for word in rawText.split(SEPARATOR):
		end = start + len(word)
		glyph = table.get(word)
		if glyph is not None:
			found.append((start, end, glyph))
		start = end + len(SEPARATOR)
	return found


def compressRegion(region) -> dict:
	"""Replace the words a shape stands over with the shape, in one region's cells.

	Called after the region has been read and before it is laid out, which is what makes the
	cells given back cells the wrapping can use.

	Everything is done right to left, so that a place already found stays where it was found
	while the line in front of it is shortened; the marks already recorded are the ones after
	the cut, and those are moved along with the cells.

	:param region: the region to compress, which may be anything or nothing.
	:return: `{cell index: the fitted glyph}`, empty when nothing was drawn.
	"""
	if region is None:
		return {}
	cells = getattr(region, "brailleCells", None)
	rawText = getattr(region, "rawText", "") or ""
	rawToBraille = getattr(region, "rawToBraillePos", None)
	brailleToRaw = getattr(region, "brailleToRawPos", None)
	if not cells or not rawText or rawToBraille is None or brailleToRaw is None:
		return _forget(region)
	if not enabled():
		return _forget(region)
	held = getattr(region, MARKS, None)
	if held is not None and getattr(region, STAMP, None) == tuple(cells):
		# Laid out again at another width, with nothing read since. See `STAMP`.
		return held
	target = glyphTarget()
	if target is None:
		return _forget(region)
	marks = marksIn(rawText)
	if not marks:
		return _forget(region)
	drawn: dict = {}
	for start, end, glyph in reversed(marks):
		at = rawToBraille[start] if start < len(rawToBraille) else None
		after = rawToBraille[end] if end < len(rawToBraille) else len(cells)
		if at is None or not 0 <= at < after <= len(cells):
			continue
		fitted = glyphs.fittedOver(target.driver, glyph, list(cells[at:after]))
		if fitted is None or fitted.saved <= 0:
			# Nothing to gain in this reader's table. The word stays, which is what a display
			# without glyphs shows anyway.
			continue
		cells, rawToBraille, brailleToRaw = glyphs.compress(cells, rawToBraille, brailleToRaw, at, fitted)
		drawn = {index - fitted.saved: value for index, value in drawn.items()}
		drawn[at] = fitted
		_moveMarks(region, at, fitted)
	if not drawn:
		return _forget(region)
	region.brailleCells = cells
	region.rawToBraillePos = rawToBraille
	region.brailleToRawPos = brailleToRaw
	setattr(region, MARKS, drawn)
	setattr(region, STAMP, tuple(cells))
	return drawn


def _moveMarks(region, at: int, fitted) -> None:
	"""Move the region's single positions to where the shortened line put them.

	:param region: the region being compressed.
	:param at: the cell the run started on.
	:param fitted: what was put there.
	"""
	for name in MOVED:
		position = getattr(region, name, None)
		if position is None:
			continue
		try:
			setattr(region, name, glyphs.movedPosition(position, at, fitted.replaces, len(fitted.cells)))
		except Exception:
			log.debugWarning(f"BrlMultiline: could not move {name} after a glyph", exc_info=True)


def _forget(region) -> dict:
	"""Leave a region with no marks on it, and say so.

	Cleared rather than left alone, because a region that was compressed on one pass and is not
	on the next — the setting turned off, the display unplugged — would otherwise carry marks
	naming cells that are no longer symbols.

	:param region: the region.
	:return: an empty table, for the caller to return in turn.
	"""
	if getattr(region, MARKS, None) is not None:
		try:
			setattr(region, MARKS, None)
			setattr(region, STAMP, None)
		except Exception:
			log.debugWarning("BrlMultiline: could not clear a region's glyphs", exc_info=True)
	return {}


def keepCompressed(region) -> None:
	"""Put the shapes back on a region that has been read again since it was laid out.

	**The reason this exists is a routing key reaching the wrong word.** NVDA re-reads the
	region the flow is drawing on its own account — `_handlePendingUpdate` does it every time
	the caret moves — and a re-read rebuilds the cells and both position maps from the text.
	When the text has not changed the flow keeps the rendering it already has, which is the
	right thing and the cheap thing; but that rendering was cut from compressed cells while the
	maps are now the uncompressed ones. A press on the second half of the row then answered
	through a map two cells out of step with what the reader was touching.

	Compressing is deterministic, so doing it again over the same text gives the same cells and
	the same marks the rendering was cut from. Costs one attribute read for a region that has
	never carried a shape, which is nearly all of them.

	:param region: the region about to be used, which may be anything or nothing.
	"""
	if region is None or getattr(region, MARKS, None) is None:
		return
	if getattr(region, STAMP, None) == tuple(getattr(region, "brailleCells", None) or ()):
		return
	compressRegion(region)


def marksOf(region) -> dict:
	""":return: the glyphs a region is carrying, or an empty table.

	:param region: the region, which may be anything or nothing.
	"""
	if region is None:
		return {}
	return getattr(region, MARKS, None) or {}
