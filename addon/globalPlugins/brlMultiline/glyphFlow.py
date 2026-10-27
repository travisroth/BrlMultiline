# BrlMultiline: putting the glyph vocabulary onto the line NVDA wrote.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Where a symbol actually replaces something the reader would otherwise have to read.

`glyphs` holds the shapes; this decides which object gets one: whichever NVDA already wrote a
word for. It is a substitution over NVDA's own wording, so:

1. Nothing is announced that NVDA would not announce.
2. The cells come from the region's own buffer, so a contracted table cannot misalign a shape.
3. Failing means showing the word as NVDA wrote it.

Applied wherever a region is read, before the line is cut: `segments.BrailleBufferSegment.update`
and `flowRender._layoutBuffer`. Whole words are matched anywhere in the line, since NVDA puts the
role on either side of the name.

The marks on a region say which cells are symbols. A segment or `flowControl.cellGlyphs` turns
them into places, and `container` hands them to the driver, afresh each frame, since the driver
retires a glyph when its cells change.
"""

from typing import NamedTuple

from logHandler import log

from . import bmConfig, glyphs
from .devices import VIRTUAL_DISPLAY_NAME

MARKS = "brlMultilineGlyphs"
"""Attribute a compressed region carries: `{cell index: the fitted glyph starting there}`.

On the region, which every later stage already holds, rather than in a side table to keep in
step.
"""

STAMP = "_brlMultilineGlyphCells"
"""Attribute holding the cells `MARKS` was worked out over.

A receipt: a region is laid out several times, and a second pass over compressed text would
find nothing and replace good marks with none. Unchanged cells mean the marks still hold.
"""

DRIVER = "_brlMultilineGlyphDriver"
"""Attribute holding the driver that built a region's shapes.

A shape belongs to the driver that built it, so a different driver is a different receipt. Held
beside `STAMP`.
"""

BEFORE = "_brlMultilineBeforeGlyphs"
"""Attribute holding the region exactly as NVDA left it, so it can be given back.

Compression rewrites the region in place, and it is not always read again before being drawn,
so this is how the words return.

Restored only where the region is still the one that was compressed. See `_restore`.
"""

FIELDS = "_brlMultilineFieldSpans"
"""Attribute a document region carries: which stretches of its text NVDA wrote itself.

The feature's safety: NVDA's finished line does not say which words are the writer's, so
"btn" in prose looks like a button. A patch on `TextInfoRegion._addFieldText`, the one door
for control field text, records NVDA's own. See `patches._addFieldTextRecordingProvenance`.

An empty list means no fields; the attribute missing means nothing is replaced at all.
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

What `getPropertiesBraille` is handed from the application. Any stretch matching one is
refused: in "btn btn" for a button named "btn", neither is replaced.
"""

MOVED = ("brailleCursorPos", "brailleSelectionStart", "brailleSelectionEnd")
"""The region's other positions into its cells, which have to move when the cells do.

`compress` keeps the position maps true; these single positions are moved by `_moveMarks`.
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

Keyed on `controlTypes.Role.<name>.name`, so the word is read from NVDA at run time in the
reader's language.
"""

POSITIVE_GLYPHS = {
	"CHECKED": "checked",
	"HALFCHECKED": "halfChecked",
	"ON": "checked",
	"PRESSED": "pressed",
	"HASPOPUP": "submenu",
}
"""Which entry stands over each state NVDA writes when the state is present.

`ON` and `CHECKED` share a shape, as NVDA writes them the same three cells.
"""

NEGATIVE_GLYPHS = {
	"CHECKED": "unchecked",
	"ON": "unchecked",
	"PRESSED": "notPressed",
}
"""Which entry stands over each state NVDA writes when the state is absent."""

COUNTED = ("list",)
"""Entries whose word NVDA may run straight into a count: "lst6" for a list of six.

`getPropertiesBraille` appends the count with no separator. Only the label is drawn over.
"""

HEADING_LEVELS = (1, 2, 3)
"""Which heading levels have a shape. See `glyphs.HEADING_1` for why it stops at three."""

VISITED_LINK = "vlnk"
"""NVDA's own message for a link already followed, as its catalogue lists it.

Not in `braille.labels`, so the message id is put through NVDA's catalogue. See `_nvdaSays`.
"""

MULTISELECT_LIST = "mslst"
"""NVDA's own message for a list that allows more than one selection. See `VISITED_LINK`.

Drawn as a list: the shape says what the thing is, and the count NVDA runs on after it stays,
as it does for a plain one. See `COUNTED`.
"""

HEADING = "h%s"
"""NVDA's own template for a heading, as its catalogue lists it. See `VISITED_LINK`."""

try:
	from braille.constants import TEXT_SEPARATOR as SEPARATOR
except Exception:  # pragma: no cover - an NVDA that has moved it
	SEPARATOR = " "
"""What NVDA parts one thing on a braille line from the next by.

Taken from NVDA, with a space as fallback. Whole words are what make matching safe.
"""

_TOKENS: dict | None = None
"""The token table, built once. Cleared by `forget`."""

_ORDERED: "list[str] | None" = None
"""Its labels, longest first, so that one beginning with another is not lost to it."""


class Target(NamedTuple):
	"""The display a glyph would be drawn on, and where its rows start.

	As `graphics.findSurface` gives, since a composite member knows nothing of rows above it.
	"""

	driver: object
	"""The driver publishing the glyph capability."""

	rowStart: int
	"""Which row of NVDA's display this driver's first row is."""

	numRows: int
	"""How many rows it has."""

	numCols: int
	"""How many cells to a row it has."""

	def indexFor(self, row: int, col: int) -> int | None:
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

	Duck typed. Not through `graphics.findSurface`, since a display may draw glyphs without
	graphics.

	:param driver: any object, including None.
	"""
	if driver is None:
		return False
	if not all(hasattr(driver, name) for name in ("newGlyph", "setCellGlyphs", "glyphSize", "cellSize")):
		return False
	return glyphs.supported(driver)


def glyphTargets(display=None) -> "list[Target]":
	"""Find every display that can draw glyphs, behind a composite or not.

	All of them, so a region is compressed only for the display it is going to.

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
		log.error(
			"BrlMultiline: could not read the composite's members to find one that draws",
			exc_info=True,
		)
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


def glyphTarget(display=None) -> Target | None:
	""":return: any display that can draw glyphs, for asking whether one is attached at all.

	For the settings panel. Deciding about a region wants `targetForRows`.

	:param display: the display to look at, or None for the one NVDA is driving.
	"""
	targets = glyphTargets(display)
	return targets[0] if targets else None


def targetForRows(row: int, numRows: int, display=None) -> Target | None:
	"""Find the display a run of rows is on, if one piece of hardware holds all of them.

	All or none, since a shape registers against one display.

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


def _targetFor(driver, rowStart: int) -> Target | None:
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

	From `braille.labels` where readable, else the vocabulary's English wordings.
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

	Not this add-on's `_`, whose catalogue lacks NVDA's ids and whose translators should not see
	them. Reached through builtins, where NVDA installs it; without it the English is returned.

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

	A visited link, a multi select list and a heading level are composed by
	`getPropertiesBraille`, not in `braille.labels`.
	"""
	found: dict = {}
	_offer(found, _nvdaSays(VISITED_LINK), "visitedLink")
	_offer(found, _nvdaSays(MULTISELECT_LIST), "list")
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

	Whether it saves cells is `fittedOver`'s question, at the moment of use. A word two
	different shapes want is given to neither.

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

	Converted from the vocabulary's dot notation, so the two cannot disagree.

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

	Called from the patch on `_addFieldText`. See `FIELDS`.

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

	Called from the patch on `TextInfoRegion.update`. An empty list, not none; see `FIELDS`.

	:param region: the region about to be read.
	"""
	try:
		setattr(region, FIELDS, [])
	except Exception:
		log.debugWarning("BrlMultiline: a region would not carry its field spans", exc_info=True)


def replaceableSpans(region) -> "list[tuple] | None":
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

	By class, since a document region carries an object too.

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

	Every occurrence of each of `OBJECT_TEXT` is cut out; what is left is NVDA's wording.

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


def marksIn(rawText: str, spans: "list[tuple] | None" = None) -> "list[tuple]":
	"""Find everything in a line that a shape stands over.

	Whole words, from every word boundary, within `replaceableSpans`. A label may be several
	words ("sorted asc"), so the longest that fits wins.

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
			# Not past the separator: a count after the label is still this word. See `COUNTED`.
			at = matched[1]
		nextWord = rawText.find(SEPARATOR, at, end)
		if nextWord < 0:
			break
		at = nextWord + len(SEPARATOR)
	return found


def _labelAt(rawText: str, at: int, labels: "list[str]", table: dict, limit: int):
	""":return: (start, end, glyph) for the label starting here, or None.

	Longest first. What follows must be a separator or the end of the stretch, so "h1" does not
	match in "h10", except a count after an entry in `COUNTED`.

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
		after = end
		if table[label] in _counted():
			while after < limit and rawText[after].isdigit():
				after += 1
		if after < len(rawText) and rawText[after] != SEPARATOR:
			continue
		return at, end, table[label]
	return None


def _counted() -> frozenset:
	""":return: the entries a count may follow. See `COUNTED`."""
	return frozenset(glyphs.VOCABULARY[name] for name in COUNTED)


def _byLength() -> "list[str]":
	""":return: every label, longest first. Built with the table and dropped with it."""
	global _ORDERED
	if _ORDERED is None:
		_ORDERED = sorted(tokens(), key=len, reverse=True)
	return _ORDERED


def compressRegion(region, target: Target | None = None) -> dict:
	"""Replace the words a shape stands over with the shape, in one region's cells.

	After reading and before layout, so wrapping can use the cells given back. Right to left,
	so places found stay put; marks after each cut move with the cells.

	:param region: the region to compress, which may be anything or nothing.
	:param target: the display this region is going to, decided by whoever knows. None means
		unknown: nothing is compressed and words taken out are put back.
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

	Marks cleared and cells put back, since the region may not be read again in between. See
	`BEFORE`.

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

	Only where its cells still match `STAMP`, or the restore would undo a re-read.

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


def keepCompressed(region, target: Target | None = None) -> None:
	"""Put the shapes back on a region that has been read again since it was laid out.

	NVDA's `_handlePendingUpdate` re-reads the region, rebuilding uncompressed maps under a kept
	rendering cut from compressed cells, so routing would be out of step. Compressing again is
	deterministic, and costs one attribute read where there was never a shape.

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
