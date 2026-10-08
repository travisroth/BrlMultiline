# BrlMultiline: the reader's own braille substitutions, as data and as liblouis rules.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A personal list of things braille should show differently, and the liblouis rules for it.

"Travis Roth" shown as "TR", a symbol shown as dots the reader chose, or any liblouis rule they
write themselves. The list is turned into `brlMultiline-user.cti`, which every BrlMultiline braille
table includes ahead of everything else, so NVDA translates with it like any other part of the
table: nothing is patched. `userBrailleTable` writes and loads it; `substitutionsDialog` edits it.

**Nothing here imports NVDA**, so everything that decides anything is tested without it.

**Every entry becomes `correct` rules**, liblouis's pass that rewrites the text before it is
translated. That is the only rule that matched names reliably when tested: `word` and `always`
rules written with capitals never matched at all, and written in lower case did not match a name
in title case and doubled the capital sign when they did. A `correct` rule matches exactly what it
is given, so an entry that should match whatever the capitals gets one rule for each form it is
likely to meet. See `caseForms`.

**Text is translated after it is put in**, so "TR" gets the table's capital signs and a
replacement is contracted in grade 2 as if it had been typed.

**Dots go through a character of their own.** The match is rewritten to a character from Unicode's
private use plane 15, which no document uses, and that character is defined as the dots. Defined as
a `letter`, so the punctuation after it is a word's punctuation: as a `sign`, US grade 2 wrote the
period after it as a decimal point. And `noletsign`, or grade 1 put a letter sign in front of it when
it stood alone.

**Whole words take four rules.** A `correct` test can look at the character before the match and
the one after, but not past either end of the text, so the start and the end of the text each need a
rule of their own. Each test of the character after steps back with `_`, so it does not swallow the
character it looked at.

**What each costs.** liblouis tries every `correct` rule at every position of every line, unlike
its translation rules, which it looks up by character. A whole word entry that matches any capitals
is up to twenty rules. Measured: forty of those, 640 rules in all, took a line from 0.15 ms to 1 ms.
A list of tens of entries costs nothing anyone will notice; a list of hundreds would.
"""

import dataclasses
import json
import re
from typing import Iterable, NamedTuple, Optional

TEXT = "text"
"""Shown as other text, which the table then translates."""

DOTS = "dots"
"""Shown as dots the reader gives."""

RULE = "rule"
"""One or more liblouis rules, written by the reader and put in as they are."""

KINDS = (TEXT, DOTS, RULE)

STORE_VERSION = 2

RULES_FILE = "brlMultiline-user.cti"
"""The file every BrlMultiline table includes first. See `tools/makeBrailleTables.py`."""

PLACEHOLDER_BASE = 0xF0000
"""The first character a dots entry is rewritten to: Supplementary Private Use Area A.

Not the private use area in the Basic Multilingual Plane, U+E000 to U+F8FF, because Word writes the
bullets of a Symbol font list there.
"""

PLACEHOLDER_LIMIT = 0xFFFFD
"""The last character of that area. 65,534 dots entries."""

_DOT_CELL = re.compile(r"^(0|[1-8]{1,8})$")


@dataclasses.dataclass(frozen=True)
class Substitution:
	"""One thing the reader wants shown differently."""

	match: str
	"""The text to find. For a rule entry, a description of the rule, shown in the list."""

	kind: str = TEXT
	"""One of `KINDS`."""

	replacement: str = ""
	"""The text to show instead, the dots (such as `1245-1235`), or the liblouis rules, one a line.

	Empty text removes what was matched."""

	wholeWord: bool = True
	"""Only where the match is not part of a longer word: no letter or digit right before or after."""

	matchCase: bool = False
	"""Only with the capitals as typed. Otherwise as typed, in lower case, in capitals, and capitalised."""

	profile: str = ""
	"""The configuration profile it is used in, or empty for every profile."""

	enabled: bool = True
	"""Whether it is used at all. A reader can keep an entry they want back later."""

	def asStored(self) -> dict:
		return dataclasses.asdict(self)

	@classmethod
	def fromStored(cls, stored) -> Optional["Substitution"]:
		""":return: the entry a stored record describes, or None if it is not one."""
		if not isinstance(stored, dict):
			return None
		match, kind = stored.get("match"), stored.get("kind", TEXT)
		if not isinstance(match, str) or kind not in KINDS:
			return None
		replacement = stored.get("replacement", "")
		profile = stored.get("profile", "")
		return cls(
			match=match,
			kind=kind,
			replacement=replacement if isinstance(replacement, str) else "",
			wholeWord=bool(stored.get("wholeWord", True)),
			matchCase=bool(stored.get("matchCase", False)),
			profile=profile if isinstance(profile, str) else "",
			enabled=bool(stored.get("enabled", True)),
		)


# Checking an entry


def normalizedDots(text: str) -> str:
	""":return: dots as liblouis takes them, each cell's dots in order: `" 21-0 "` is `12-0`.

	:raises ValueError: if the text is not cells of dots 1 to 8, or 0 for a blank cell, joined by
		hyphens.
	"""
	cells = [cell.strip() for cell in text.strip().split("-")]
	if not cells or not all(_DOT_CELL.match(cell) for cell in cells):
		raise ValueError(text)
	normalized = []
	for cell in cells:
		if cell == "0":
			normalized.append(cell)
			continue
		if len(set(cell)) != len(cell):
			raise ValueError(text)
		normalized.append("".join(sorted(cell)))
	return "-".join(normalized)


class Problem(str):
	"""Why an entry cannot be used, as a code the dialog turns into words. See `problemOf`."""


NO_MATCH = Problem("noMatch")
NO_DOTS = Problem("noDots")
BAD_DOTS = Problem("badDots")
NO_RULE = Problem("noRule")
RULE_INCLUDES = Problem("ruleIncludes")
LINE_BREAK = Problem("lineBreak")


def problemOf(entry: Substitution) -> Optional[Problem]:
	""":return: why the entry cannot be turned into rules, or None if it can.

	Whether liblouis accepts the rules is another question, which only liblouis can answer. See
	`userBrailleTable.validate`.
	"""
	if entry.kind == RULE:
		lines = ruleLines(entry.replacement)
		if not lines:
			return NO_RULE
		if any(re.match(r"^\s*include\b", line) for line in lines):
			# A file found relative to wherever the rules happen to be compiled from is a file
			# that is there at one time and not another.
			return RULE_INCLUDES
		return None
	if not entry.match:
		return NO_MATCH
	if "\n" in entry.match or "\r" in entry.match or "\n" in entry.replacement or "\r" in entry.replacement:
		return LINE_BREAK
	if entry.kind == DOTS:
		if not entry.replacement.strip():
			return NO_DOTS
		try:
			normalizedDots(entry.replacement)
		except ValueError:
			return BAD_DOTS
	return None


def clash(entries: list) -> Optional[tuple[int, int]]:
	""":return: the first two entries that would match the same text in the same profile, or None.

	Two `correct` rules for the same text leave liblouis to use one of them, and the reader would
	never learn which. Rule entries are the reader's own business and are not compared.
	"""
	seen: list[tuple[int, str, set]] = []
	for index, entry in enumerate(entries):
		if not entry.enabled or entry.kind == RULE or problemOf(entry) is not None:
			continue
		forms = set(caseForms(entry.match, entry.matchCase))
		for other, profile, otherForms in seen:
			sameProfile = not profile or not entry.profile or profile == entry.profile
			if sameProfile and forms & otherForms:
				return other, index
		seen.append((index, entry.profile, forms))
	return None


def ruleLines(text: str) -> list[str]:
	""":return: the lines of a rule entry that say anything, comments and blank lines left out."""
	lines = []
	for line in text.splitlines():
		stripped = line.strip()
		if stripped and not stripped.startswith("#"):
			lines.append(stripped)
	return lines


# Turning entries into rules


def escape(text: str) -> str:
	""":return: the text as a liblouis operand, in ASCII.

	A space or tab ends an operand, so they are escaped; a backslash starts an escape; a quotation
	mark ends a quoted string, and is written `\\"`, which liblouis takes as one inside a test or an
	action. Not by its code: liblouis turns `\\x0022` into a quotation mark before it looks for the
	string's end, and the rule stopped compiling. Anything outside printable ASCII is written by its
	code, so the file is the same whatever encoding liblouis reads it in.
	"""
	out = []
	for c in text:
		cp = ord(c)
		if c == " ":
			out.append("\\s")
		elif c == "\t":
			out.append("\\t")
		elif c == "\\":
			out.append("\\\\")
		elif c == '"':
			out.append('\\"')
		elif 0x20 < cp < 0x7F:
			out.append(c)
		elif cp <= 0xFFFF:
			out.append(f"\\x{cp:04x}")
		else:
			out.append(f"\\y{cp:05x}")
	return "".join(out)


def asciiLine(text: str) -> str:
	""":return: a rule line the reader wrote, with anything outside ASCII written by its code."""
	return "".join(
		c if ord(c) < 0x80 else (f"\\x{ord(c):04x}" if ord(c) <= 0xFFFF else f"\\y{ord(c):05x}") for c in text
	)


def caseForms(match: str, matchCase: bool) -> list[str]:
	""":return: each form of the match to give a rule, the one typed first.

	As typed, in lower case, in capitals, with every word capitalised, and with only the first letter
	capitalised, as a sentence starts. Each once.
	"""
	if matchCase:
		return [match]
	forms = [match, match.lower(), match.upper(), _titled(match), match[:1].upper() + match[1:].lower()]
	unique = []
	for form in forms:
		if form not in unique:
			unique.append(form)
	return unique


def _titled(text: str) -> str:
	""":return: every word capitalised, without `str.title`'s capital after an apostrophe."""
	return re.sub(
		r"[^\W\d_]+(?:'[^\W\d_]+)?", lambda word: word.group(0)[:1].upper() + word.group(0)[1:].lower(), text
	)


def _correctRules(form: str, action: str, wholeWord: bool) -> list[str]:
	match = escape(form)
	if not wholeWord:
		return [f'noback correct "{match}" {action}']
	return [
		f'noback correct `["{match}"]~ {action}',
		f'noback correct `["{match}"]!$ld_ {action}',
		f'noback correct !$ld["{match}"]~ {action}',
		f'noback correct !$ld["{match}"]!$ld_ {action}',
	]


def rulesFor(entry: Substitution, placeholder: Optional[int] = None) -> list[str]:
	""":return: the liblouis rules for one entry.

	:param placeholder: the character a dots entry is rewritten to. Required for one.
	"""
	if entry.kind == RULE:
		return [asciiLine(line) for line in ruleLines(entry.replacement)]
	if entry.kind == DOTS:
		if placeholder is None:
			raise ValueError("A dots entry needs a placeholder character")
		character = escape(chr(placeholder))
		action = f'"{character}"'
		rules = []
		for form in caseForms(entry.match, entry.matchCase):
			rules += _correctRules(form, action, entry.wholeWord)
		return rules + [
			f"noback letter {character} {normalizedDots(entry.replacement)}",
			f"noletsign {character}",
		]
	action = f'"{escape(entry.replacement)}"'
	rules = []
	for form in caseForms(entry.match, entry.matchCase):
		rules += _correctRules(form, action, entry.wholeWord)
	return rules


def applies(entry: Substitution, activeProfiles: Iterable[str]) -> bool:
	""":return: whether the entry is used with these profiles active. See `Substitution.profile`."""
	return entry.enabled and (not entry.profile or entry.profile in set(activeProfiles))


def rulesText(entries: Iterable[Substitution], activeProfiles: Optional[Iterable[str]] = None) -> str:
	""":return: the whole rules file, for the entries that apply.

	Entries that cannot be turned into rules are left out; the dialog does not save one, so only a
	stored list edited by hand has any.

	:param activeProfiles: the profiles in force, by name, or None to take every enabled entry
		whatever its profile, which is what validation compiles.
	"""
	entries = list(entries)
	active = None if activeProfiles is None else set(activeProfiles)
	lines = [
		"# Generated by BrlMultiline from the reader's braille substitutions.",
		"# Do not edit; it is written again whenever they change. Edit them in NVDA's Preferences menu.",
		"",
	]
	placeholder = PLACEHOLDER_BASE
	for number, entry in enumerate(entries, start=1):
		# Numbered across the whole list, used or not, so an entry keeps its character whichever
		# profile is in force.
		ownCharacter = placeholder
		if entry.kind == DOTS:
			placeholder = min(placeholder + 1, PLACEHOLDER_LIMIT)
		if problemOf(entry) is not None:
			continue
		if not entry.enabled or (active is not None and entry.profile and entry.profile not in active):
			continue
		lines.append(f"# {number}: {asciiLine(entry.match)}".rstrip())
		lines += rulesFor(entry, ownCharacter)
	return "\n".join(lines) + "\n"


# Storing


@dataclasses.dataclass(frozen=True)
class PersonalTable:
	"""One of NVDA's own braille tables with the reader's substitutions in front of it.

	Made in the dialog for a table BrlMultiline has no table of its own for, such as Spanish or UK
	braille. See `personalTables`, which writes it and lists it in the add-on's manifest.
	"""

	base: str
	"""The file name of the NVDA table it is made from, such as `es-g1.ctb`."""

	displayName: str
	"""That table's name as NVDA showed it when the personal table was made."""

	contracted: bool = False

	def asStored(self) -> dict:
		return dataclasses.asdict(self)

	@classmethod
	def fromStored(cls, stored) -> Optional["PersonalTable"]:
		if not isinstance(stored, dict):
			return None
		base, displayName = stored.get("base"), stored.get("displayName")
		if not isinstance(base, str) or not re.fullmatch(r"[\w.+-]+\.(ctb|utb)", base):
			# Only a plain file name: it is written into an `include` and a manifest key.
			return None
		return cls(
			base=base,
			displayName=displayName if isinstance(displayName, str) and displayName else base,
			contracted=bool(stored.get("contracted", False)),
		)


class Store(NamedTuple):
	"""Everything the store holds."""

	entries: list
	tables: list
	"""`PersonalTable`s."""
	leftOut: int = 0
	"""How many stored records could not be read and were left out."""


def toText(entries: Iterable[Substitution], tables: Iterable[PersonalTable] = ()) -> str:
	""":return: the list and the personal tables as the JSON they are stored in."""
	return (
		json.dumps(
			{
				"version": STORE_VERSION,
				"entries": [entry.asStored() for entry in entries],
				"tables": [table.asStored() for table in tables],
			},
			ensure_ascii=False,
			indent="\t",
		)
		+ "\n"
	)


class StoreError(ValueError):
	"""A stored list that cannot be read at all, as opposed to one with entries left out."""


def fromText(text: str) -> Store:
	""":return: what a stored list holds.

	The first version had no personal tables, and is read as having none.

	:raises StoreError: if the text is not a stored list at all, or one from a later version than
		this one reads.
	"""
	try:
		read = json.loads(text)
	except ValueError as error:
		raise StoreError(str(error)) from error
	if not isinstance(read, dict) or not isinstance(read.get("entries"), list):
		raise StoreError("not a list of braille substitutions")
	version = read.get("version")
	if not isinstance(version, int) or version > STORE_VERSION:
		# Written by a later BrlMultiline. Reading what can be read and then saving would drop
		# whatever that version added.
		raise StoreError(f"version {version!r}")
	entries = []
	leftOut = 0
	for stored in read["entries"]:
		entry = Substitution.fromStored(stored)
		if entry is None:
			leftOut += 1
		else:
			entries.append(entry)
	tables = []
	storedTables = read.get("tables", [])
	for stored in storedTables if isinstance(storedTables, list) else ():
		table = PersonalTable.fromStored(stored)
		if table is None or any(other.base == table.base for other in tables):
			leftOut += 1
		else:
			tables.append(table)
	return Store(entries, tables, leftOut)


# Telling the renderer the rules changed

_generation = 0


def generation() -> int:
	""":return: a number that changes whenever the rules in force change.

	Part of every flow rendering's key, beside the table's name, because the table's name stays the
	same when the rules in it change. See `flow.RenderKey.settings`.
	"""
	return _generation


def rulesChanged() -> None:
	"""Note that the rules in force changed. Renderings made before are not served again."""
	global _generation
	_generation += 1
