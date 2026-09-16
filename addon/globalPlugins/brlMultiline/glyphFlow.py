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

DRIVER = "_brlMultilineGlyphDriver"
"""Attribute holding the driver that built a region's shapes.

**A shape belongs to the driver that built it.** `glyphs.fittedOver` asks the driver for each one, and
what comes back is that driver's own and means nothing to another. The receipt was the cells alone, so
a band moved from one display that draws to another handed the second the first one's shapes, laid out
again over unchanged cells. Found by review. Held beside `STAMP`, and a different driver is a different
receipt.
"""

BEFORE = "_brlMultilineBeforeGlyphs"
"""Attribute holding the region exactly as NVDA left it, so it can be given back.

Compression rewrites the cells and both position maps in place, and a region is not always
read again before it is drawn again — a flow keeps the rendering it has when the text has not
changed, and a display rebuild or a profile switch can hand the same region back. Without this
the words could not return: turning the setting off left "b Search" on the line with no shape
registered for it, which is a cell that means nothing.

Restored only where the region is still the one that was compressed. See `_restore`.
"""

FIELDS = "_brlMultilineFieldSpans"
"""Attribute a document region carries: which stretches of its text NVDA wrote itself.

**The whole safety of the feature rests on this.** NVDA flattens everything it has to say
about a line into one string joined by single spaces — the document's own words, the roles, the
states, an object's name, its description — and nothing in the result says which is which. So a
line of somebody's prose containing the word "btn" is, to anything reading the finished text,
indistinguishable from a button. Replacing it would take the middle out of a word the writer
typed and draw a symbol over it.

`TextInfoRegion._addFieldText` is the one door every piece of control field text goes through;
document content is appended by a different branch of the same loop. So a patch on that method
records exactly what NVDA contributed, and everything else is left alone. See
`patches._addFieldTextRecordingProvenance`.

An empty list means the patch is in and this region had no fields — nothing to replace. The
attribute missing altogether means no provenance is available, and then nothing is replaced at
all.
"""

SAID = "_brlMultilineSaid"
"""Attribute a region carries when it was read through a stand-in name rather than the object's.

A table row read as one line is the case: the name in the composed text is the row's own cells,
not `obj.name`, so the exclusions worked out from the object would not find it. See
`flowObjects`.
"""

OBJECT_TEXT = (
	"name",
	"value",
	"description",
	"placeholder",
	"keyboardShortcut",
	"errorMessage",
	"cellCoordsText",
	"roleTextBraille",
)
"""What an object contributes to its own line that is not NVDA's own wording.

Everything `getPropertiesBraille` is handed that came from the application rather than from
NVDA's tables. A stretch of the line matching any of them is refused, whichever of them it
matched: a button named "btn" reads as "btn btn" and there is nothing in the finished string to
say which of the two is the role, so neither is replaced. A lost symbol is a symbol; a mangled
name is a fault the reader cannot see.
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

HEADING_LEVELS = (1, 2, 3)
"""Which heading levels have a shape. See `glyphs.HEADING_1` for why it stops at three."""

VISITED_LINK = "vlnk"
"""NVDA's own message for a link already followed, as its catalogue lists it.

Not in `braille.labels`: `getPropertiesBraille` composes it where it finds `State.VISITED` on
a link, so there is no dictionary to read it out of. What is written here is the message id,
and it goes through NVDA's own catalogue before it is used — see `_nvdaSays` — so this is not
the English text being matched, it is the key that finds the reader's own.
"""

HEADING = "h%s"
"""NVDA's own template for a heading, as its catalogue lists it. See `VISITED_LINK`."""

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

_ORDERED: "Optional[list[str]]" = None
"""Its labels, longest first, so that one beginning with another is not lost to it."""


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


def glyphTargets(display=None) -> "list[Target]":
	"""Find every display that can draw glyphs, behind a composite or not.

	**All of them, not the first.** Two displays driven as one are two pieces of hardware, and
	a Monarch stacked with a Focus is the arrangement this add-on exists for. Taking the first
	that could draw and treating it as *the* display meant a region bound for the Focus was
	compressed because the Monarch was plugged in: the container then rightly refused to send
	the shape to a row outside the Monarch, and the reader was left with "b Search", a word
	with its middle removed and nothing drawn over it.

	:param display: the display to look at, or None for the one NVDA is driving.
	:return: one target per member that draws, in row order. Empty where nothing attached can,
		which is the ordinary case on ordinary hardware and not an error.
	"""
	if display is None:
		import braille

		handler = braille.handler
		display = handler.display if handler is not None else None
	if display is None:
		return []
	if canDraw(display):
		found = _targetFor(display, rowStart=0)
		return [found] if found is not None else []
	if getattr(display, "name", None) != VIRTUAL_DISPLAY_NAME:
		return []
	try:
		slots = list(display.slots)
	except Exception:
		log.error("BrlMultiline: could not read the composite's members to find one that draws", exc_info=True)
		return []
	targets = []
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
		found = _targetFor(driver, rowStart=rowStart)
		if found is not None:
			targets.append(found)
	return sorted(targets, key=lambda target: target.rowStart)


def glyphTarget(display=None) -> Optional[Target]:
	""":return: any display that can draw glyphs, for asking whether one is attached at all.

	The settings panel is what wants this: whether to offer the choice is a question about the
	arrangement rather than about a place on it. Anything deciding what to *do* with a
	particular region wants `targetForRows`, which asks which piece of hardware that region is
	going to.

	:param display: the display to look at, or None for the one NVDA is driving.
	"""
	targets = glyphTargets(display)
	return targets[0] if targets else None


def targetForRows(row: int, numRows: int, display=None) -> Optional[Target]:
	"""Find the display a run of rows is on, if one piece of hardware holds all of them.

	**All of them, or none.** A segment spanning the join between two displays is drawn partly
	on each, and a shape can only be registered against one of them; compressing such a region
	would take cells out of the half that cannot draw. Rare — segments are usually cut to a
	display — and cheap to refuse.

	:param row: the first row on NVDA's display.
	:param numRows: how many rows.
	:param display: the display to look at, or None for the one NVDA is driving.
	:return: the target holding every one of those rows, or None.
	"""
	if numRows < 1:
		return None
	for target in glyphTargets(display):
		if target.rowStart <= row and row + numRows <= target.rowStart + target.numRows:
			return target
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
	global _ORDERED, _TOKENS
	_TOKENS = None
	_ORDERED = None


def tokens() -> dict:
	""":return: every word NVDA writes that a shape stands over, to the entry that stands there.

	Built from `braille.labels` where it can be read, so the words are NVDA's own in NVDA's own
	language. Where it cannot — a test, or an NVDA that has moved them — the vocabulary's own
	English wordings are used, which is what it was written against.
	"""
	global _TOKENS
	if _TOKENS is None:
		# A word two shapes wanted is left in the table as None while it is built, so that a
		# later offer cannot take it; it is dropped here, once, rather than checked for on
		# every line of every frame.
		_TOKENS = {label: glyph for label, glyph in _buildTokens().items() if glyph is not None}
	return _TOKENS


def _buildTokens() -> dict:
	""":return: the token table. See `tokens`."""
	found = _fromNVDA()
	if not found:
		log.debugWarning("BrlMultiline: using this add-on's own wordings for glyphs, not NVDA's")
		found = _fromVocabulary()
	found.update(_fromComposition())
	return found


def _nvdaSays(message: str) -> str:
	"""Put one of NVDA's own messages through NVDA's own catalogue.

	**Deliberately not written as a call to this add-on's underscore.** The message ids it is
	given are NVDA's, and translating them against this add-on's catalogue would find nothing
	and hand back the English — the failure that looks exactly like the feature being off. It
	would also offer them to this add-on's translators, who have no business being asked to
	translate somebody else's strings.

	NVDA installs its gettext into builtins, which is why it is reached that way rather than
	imported. A build that has installed none leaves the message as it is, and the English is
	then the honest answer rather than a crash.

	:param message: the message id, exactly as NVDA's source writes it.
	:return: what NVDA would put on the line.
	"""
	import builtins

	translate = getattr(builtins, "_", None)
	if not callable(translate):
		return message
	try:
		return str(translate(message))
	except Exception:
		log.debugWarning(f"BrlMultiline: {message!r} could not be translated", exc_info=True)
		return message


def _fromComposition() -> dict:
	""":return: the words NVDA builds rather than looks up, to the entry standing over each.

	A visited link and a heading are not in `braille.labels`. `getPropertiesBraille` composes
	them where it meets the role — a link carrying `State.VISITED`, a heading with a level — so
	there is no dictionary to read, and the message is asked of NVDA's catalogue instead.
	"""
	found: dict = {}
	_offer(found, _nvdaSays(VISITED_LINK), "visitedLink")
	template = _nvdaSays(HEADING)
	for level in HEADING_LEVELS:
		try:
			written = template % level
		except Exception:
			log.debugWarning(f"BrlMultiline: {template!r} is not a heading template", exc_info=True)
			break
		_offer(found, written, f"heading{level}")
	return found


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
	named = set(ROLE_GLYPHS.values()) | set(POSITIVE_GLYPHS.values()) | set(NEGATIVE_GLYPHS.values())
	for name in named:
		glyph = glyphs.VOCABULARY.get(name)
		if glyph is None:
			continue
		_offer(found, glyph.says or _brailleText(glyph), name)
	return found


def _offer(found: dict, label: str, name: str) -> None:
	"""Put one word in the table.

	**Whether it saves anything is not decided here.** How many cells a word costs is a
	question about the reader's braille table and not about how many characters it has: "btn"
	is three cells uncontracted and may be fewer contracted. `fittedOver` asks that question of
	the cells actually on the line, at the moment of use, which is the only place it can be
	answered truthfully.

	**A word two shapes want is given to neither.** Two properties can translate to the same
	abbreviation — nothing stops a language rendering two roles alike — and a table built by
	overwriting would then draw whichever was offered last, silently, and differently in
	different languages. Where they are the same shape there is nothing to argue about: NVDA
	writes a switch that is on and a box that is checked as the same three cells, and this add-on
	should not invent a difference NVDA does not make.

	:param found: the table being built.
	:param label: what NVDA writes.
	:param name: the vocabulary entry's name.
	"""
	glyph = glyphs.VOCABULARY.get(name)
	if glyph is None:
		log.debugWarning(f"BrlMultiline: no glyph called {name}")
		return
	if not label:
		return
	held = found.get(label)
	if held is not None and held is not glyph:
		log.debugWarning(
			f"BrlMultiline: {label!r} is written for more than one thing, so it keeps its words",
		)
		found[label] = None
		return
	if label in found and found[label] is None:
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


def recordFieldText(region, start: int, end: int) -> None:
	"""Note that NVDA wrote this stretch of a document region's text itself.

	Called from the patch on `_addFieldText`, which is where every control field's text is
	added. Nothing else appends to that list, so what is in it is exactly what NVDA said.

	:param region: the region being built.
	:param start: where its text stood before the field was added.
	:param end: where it stands after.
	"""
	if end <= start:
		return
	spans = getattr(region, FIELDS, None)
	if spans is None:
		spans = []
		try:
			setattr(region, FIELDS, spans)
		except Exception:
			log.debugWarning("BrlMultiline: a region would not carry its field spans", exc_info=True)
			return
	spans.append((start, end))


def restartFields(region) -> None:
	"""Forget what was recorded, because the region is about to be read again.

	Called from the patch on `TextInfoRegion.update`, which begins by emptying the text. An
	empty list rather than no list at all: the difference between "the patch is in and this
	region has no fields" and "there is no provenance here" is the difference between replacing
	nothing and not knowing whether it would be safe to.

	:param region: the region about to be read.
	"""
	try:
		setattr(region, FIELDS, [])
	except Exception:
		log.debugWarning("BrlMultiline: a region would not carry its field spans", exc_info=True)


def replaceableSpans(region) -> "Optional[list[tuple]]":
	"""Work out which parts of a region's line NVDA wrote and which belong to somebody else.

	:param region: the region.
	:return: the stretches a shape may be drawn over, or None where that cannot be established
		— in which case nothing is drawn and the line keeps every word of it.
	"""
	rawText = getattr(region, "rawText", "") or ""
	if not rawText:
		return None
	spans = getattr(region, FIELDS, None)
	if spans is not None:
		# A document. Only what came through `_addFieldText`, clipped: the text is stripped of
		# its line ending after the fields are added, and a field at the very end of the line
		# can be left naming characters that are no longer there.
		return [(start, min(end, len(rawText))) for start, end in spans if start < len(rawText)]
	if _isObjectRegion(region):
		return _outsideItsOwnWords(region, rawText)
	return None


def _isObjectRegion(region) -> bool:
	""":return: whether a region's whole line is `getPropertiesBraille` output.

	Asked by class rather than by duck typing, because the question is exactly "did NVDA compose
	the whole of this", and a document region carries an object too.

	:param region: the region.
	"""
	try:
		from braille.regions.NVDAObject import NVDAObjectRegion
	except Exception:
		log.debugWarning("BrlMultiline: NVDA's object region could not be found", exc_info=True)
		return False
	return isinstance(region, NVDAObjectRegion)


def _outsideItsOwnWords(region, rawText: str) -> "list[tuple]":
	"""Everything in an object's line except what the application put there.

	An object region's whole line is composed by NVDA, so the question is not which parts are
	NVDA's but which parts are the object's own — its name, its value, its description. Those
	are known, so every occurrence of each is cut out and what is left is NVDA's wording.

	Every occurrence, not the likely one. "btn btn" is a button named "btn" and nothing in the
	finished string says which is the role.

	:param region: the object's region.
	:param rawText: its line.
	:return: the stretches a shape may be drawn over.
	"""
	obj = getattr(region, "obj", None)
	forbidden = []
	said = getattr(region, SAID, None)
	for text in [said] + [getattr(obj, name, None) for name in OBJECT_TEXT]:
		if not isinstance(text, str) or not text.strip():
			continue
		at = rawText.find(text)
		while at >= 0:
			forbidden.append((at, at + len(text)))
			at = rawText.find(text, at + 1)
	return _outside(forbidden, len(rawText))


def _outside(forbidden: "list[tuple]", length: int) -> "list[tuple]":
	"""Turn a set of stretches to avoid into the stretches that are left.

	:param forbidden: (start, end) pairs, in any order and possibly overlapping.
	:param length: how long the line is.
	:return: what is not covered by any of them, left to right.
	"""
	spans = []
	at = 0
	for start, end in sorted(forbidden):
		if start > at:
			spans.append((at, start))
		at = max(at, end)
	if at < length:
		spans.append((at, length))
	return spans


def marksIn(rawText: str, spans: "Optional[list[tuple]]" = None) -> "list[tuple]":
	"""Find everything in a line that a shape stands over.

	**Whole words only, and nothing to do with which words they are.** NVDA joins the things it
	says about an object with a single space, so what it wrote is always a token of its own;
	"btn" inside somebody's text is their text. Matching anywhere in the line would have put a
	button in the middle of a word, which reads as a shape that means nothing and cannot be
	traced back.

	**Nor with where in the line they fall.** NVDA is not consistent about which side of the
	name the word goes: browse mode writes "btn Search" and an ordinary window writes it the
	other way about. Scanning from every word boundary makes that somebody else's problem.

	**A word here may be several.** The labels are NVDA's translated ones, and nothing says a
	language has to render a role in one word — NVDA's own "sorted asc" already does not. So
	the longest label that fits at a boundary wins, rather than one token being looked up.

	**And only where NVDA wrote the line.** See `replaceableSpans`: a word is a role because of
	where it came from, never because of what it says.

	:param rawText: the line as NVDA wrote it.
	:param spans: the stretches that may be replaced. None means the whole line, which is for
		asking what a piece of text says rather than for changing it.
	:return: (start, end, glyph) per label found, left to right.
	"""
	table = tokens()
	if not table or not rawText:
		return []
	labels = _byLength()
	found = []
	for spanStart, spanEnd in [(0, len(rawText))] if spans is None else spans:
		found.extend(_marksBetween(rawText, spanStart, min(spanEnd, len(rawText)), labels, table))
	found.sort()
	return found


def _marksBetween(rawText: str, start: int, end: int, labels: "list[str]", table: dict) -> "list[tuple]":
	""":return: every label inside one stretch of a line.

	:param rawText: the line.
	:param start: where the stretch begins.
	:param end: where it ends.
	:param labels: every label, longest first.
	:param table: label to entry.
	"""
	found = []
	at = start
	while at < end:
		matched = _labelAt(rawText, at, labels, table, end)
		if matched is not None:
			found.append(matched)
			at = matched[1] + len(SEPARATOR)
			continue
		nextWord = rawText.find(SEPARATOR, at, end)
		if nextWord < 0:
			break
		at = nextWord + len(SEPARATOR)
	return found


def _labelAt(rawText: str, at: int, labels: "list[str]", table: dict, limit: int):
	""":return: (start, end, glyph) for the label starting here, or None.

	Longest first, so a label that begins with another one is not lost to it. What follows has
	to be a separator or the end of what may be replaced, which is what makes "h1" fail to match
	inside "h10" and what stops a label running off the end of NVDA's own words into somebody's.

	:param rawText: the line.
	:param at: a word boundary in it.
	:param labels: every label, longest first.
	:param table: label to entry.
	:param limit: the end of the stretch being searched.
	"""
	for label in labels:
		end = at + len(label)
		if end > limit or not rawText.startswith(label, at):
			continue
		if end < len(rawText) and rawText[end] != SEPARATOR:
			continue
		return at, end, table[label]
	return None


def _byLength() -> "list[str]":
	""":return: every label, longest first. Built with the table and dropped with it."""
	global _ORDERED
	if _ORDERED is None:
		_ORDERED = sorted(tokens(), key=len, reverse=True)
	return _ORDERED


def compressRegion(region, target: Optional[Target] = None) -> dict:
	"""Replace the words a shape stands over with the shape, in one region's cells.

	Called after the region has been read and before it is laid out, which is what makes the
	cells given back cells the wrapping can use.

	Everything is done right to left, so that a place already found stays where it was found
	while the line in front of it is shortened; the marks already recorded are the ones after
	the cut, and those are moved along with the cells.

	:param region: the region to compress, which may be anything or nothing.
	:param target: the display this region is going to. **None means it is not known and
		nothing is compressed**, and any words already taken out are put back. Whoever knows
		where the region is being drawn — a segment knows its rows — is who decides, because a
		region bound for a display that cannot draw must keep its words.
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
	if target is None or not enabled():
		return _forget(region)
	held = getattr(region, MARKS, None)
	if held is not None and getattr(region, STAMP, None) == tuple(cells):
		if getattr(region, DRIVER, None) is target.driver:
			# Laid out again at another width, with nothing read since. See `STAMP`.
			return held
		# Built by another display's driver. The words go back, and the shapes are fitted again for
		# this one from the region as NVDA left it. See `DRIVER`.
		_forget(region)
		cells = getattr(region, "brailleCells", None)
		rawText = getattr(region, "rawText", "") or ""
		rawToBraille = getattr(region, "rawToBraillePos", None)
		brailleToRaw = getattr(region, "brailleToRawPos", None)
		if not cells or not rawText or rawToBraille is None or brailleToRaw is None:
			return {}
	spans = replaceableSpans(region)
	if spans is None:
		# Nothing says which words here are NVDA's. See `replaceableSpans`.
		return _forget(region)
	marks = marksIn(rawText, spans)
	if not marks:
		return _forget(region)
	before = _snapshot(region)
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
	setattr(region, DRIVER, target.driver)
	setattr(region, BEFORE, before)
	return drawn


def _snapshot(region) -> dict:
	""":return: everything compression is about to change, so it can be put back.

	:param region: the region about to be compressed.
	"""
	kept = {
		"rawText": getattr(region, "rawText", ""),
		"brailleCells": list(getattr(region, "brailleCells", None) or []),
		"rawToBraillePos": list(getattr(region, "rawToBraillePos", None) or []),
		"brailleToRawPos": list(getattr(region, "brailleToRawPos", None) or []),
	}
	for name in MOVED:
		kept[name] = getattr(region, name, None)
	return kept


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
	"""Give a region its words back, and leave no marks on it.

	Cleared rather than left alone, because a region that was compressed on one pass and is not
	on the next — the setting turned off, the display unplugged, the profile switched — would
	otherwise carry marks naming cells that are no longer symbols. **And the cells put back**,
	because a region is not always read again in between: turning the setting off used to leave
	"b Search" on the line with nothing registered to draw over it, which is a cell that means
	nothing at all.

	:param region: the region.
	:return: an empty table, for the caller to return in turn.
	"""
	if getattr(region, MARKS, None) is None:
		return {}
	try:
		_restore(region)
		setattr(region, MARKS, None)
		setattr(region, STAMP, None)
		setattr(region, DRIVER, None)
		setattr(region, BEFORE, None)
	except Exception:
		log.debugWarning("BrlMultiline: could not clear a region's glyphs", exc_info=True)
	return {}


def _restore(region) -> None:
	"""Put a compressed region back the way NVDA left it.

	**Only where it is still the region that was compressed.** A re-read rebuilds the cells and
	the maps from the text, so a snapshot taken before that is a description of something that
	no longer exists, and writing it back would undo the re-reading. The stamp is what answers
	that: it holds the cells compression produced, so cells that still match it are cells
	nothing else has touched.

	:param region: the region.
	"""
	before = getattr(region, BEFORE, None)
	if not before:
		return
	cells = getattr(region, "brailleCells", None) or []
	if tuple(cells) != getattr(region, STAMP, None) or before["rawText"] != getattr(region, "rawText", ""):
		# Read again since. The region already holds its own uncompressed cells.
		return
	region.brailleCells = list(before["brailleCells"])
	region.rawToBraillePos = list(before["rawToBraillePos"])
	region.brailleToRawPos = list(before["brailleToRawPos"])
	for name in MOVED:
		setattr(region, name, before[name])


def keepCompressed(region, target: Optional[Target] = None) -> None:
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
	:param target: the display it is going to, as `compressRegion` means it.
	"""
	if region is None or getattr(region, MARKS, None) is None:
		return
	if getattr(region, STAMP, None) == tuple(getattr(region, "brailleCells", None) or ()):
		return
	compressRegion(region, target)


def marksOf(region) -> dict:
	""":return: the glyphs a region is carrying, or an empty table.

	:param region: the region, which may be anything or nothing.
	"""
	if region is None:
		return {}
	return getattr(region, MARKS, None) or {}
