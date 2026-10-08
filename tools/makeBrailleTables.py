# BrlMultiline: generate the braille tables that fix characters liblouis leaves undefined.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Write `addon/brailleTables`: stock NVDA tables plus rules for what they leave undefined.

**The problem.** liblouis writes a character its table has no rule for as an escape, so the
bidi isolate Discord puts around every name comes out as `'\\x2068'`, eight cells for a
character that has no meaning to a reader, and an emoji as `'\\y1f4ca'`. NVDA's speech knows
names for most of these, from `symbols.dic` and from the Unicode Consortium's `cldr.dic`, but
braille never consults either.

**What this writes.** For each table in `TABLES`, one `.cti` of rules and one `.utb` that
includes it ahead of the stock table. NVDA lists the `.utb` beside its own tables through the
`brailleTables` section of the manifest (see `buildVars.py`), and resolves the stock table's
`include` against its own tables directory.

**Only what the stock table leaves undefined gets a rule.** Each candidate is translated with the
stock table first; anything the table already writes some way of its own is left alone. That
is also why the two tables get different rule files.

**What a rule writes is worked out here, by the stock table.** A name is translated with the
table it is for and the rule writes those cells, so "locked" comes out contracted in grade 2
and spelled in computer braille, and a white bullet comes out as whichever bullet the table
uses. The obvious alternative, a `correct` rule that substitutes the text before translation
and lets the table translate it, gives the same cells but is far too slow: liblouis tries every
`correct` rule at every position of every line, and with nine thousand of them a line took
60 ms against 0.1 ms. Character and `always` rules are looked up by character and cost
nothing measurable.

**Every character of a named sequence is defined.** liblouis matches a rule for a sequence, a
family or a flag, only when it knows the characters in it, so each one the stock table leaves
undefined is given its own name. The joiners between them need nothing; they are dropped.

**A name is held between two full cells**, dots 1-2-3-4-5-6-7-8 on each side with no space, the
way quotation marks hold a quotation: "📊intraday" reads "⣿bar chart⣿intraday". That says the
words stand for a symbol, and it separates the name from the text around it, so no spacing is
needed. A character written as another character (a bullet, a hyphen, a box line) is not
marked; it reads as itself.

**Run with NVDA's interpreter**, which carries liblouis, against an NVDA source checkout for the
symbol files and the stock tables::

	C:/code/nvda/.venv/Scripts/python.exe -I tools/makeBrailleTables.py

Point it at a checkout elsewhere with the `BRLMULTILINE_NVDA` environment variable. The output is
committed, so building the add-on does not need this.
"""

import ctypes
import os
import sys
import time
import unicodedata
from pathlib import Path

NVDA = Path(os.environ.get("BRLMULTILINE_NVDA", "C:/code/nvda"))
SOURCE = NVDA / "source"
STOCK_TABLES = NVDA / "include" / "liblouis" / "tables"
LOCALE = SOURCE / "locale" / "en"
OUT = Path(__file__).resolve().parent.parent / "addon" / "brailleTables"

# Load NVDA's own liblouis by path before the `louis` wrapper asks for it by name, so a different
# liblouis.dll earlier on the search path cannot be picked up instead.
ctypes.WinDLL(str(SOURCE / "liblouis.dll"))
sys.path.insert(0, str(SOURCE))
import louis  # noqa: E402

# Where a written `.utb` finds the stock table it includes. NVDA's own resolver does this inside
# NVDA; here liblouis looks under `<data path>/liblouis/tables`.
louis.liblouis.lou_setDataPath(str(STOCK_TABLES.parent.parent).encode("mbcs"))

#: Stock table file name -> the name of the table written for it.
TABLES = {
	"en-us-g2.ctb": "en-us-g2-brlMultiline",
	"en-us-comp8-ext.utb": "en-us-comp8-ext-brlMultiline",
	"en-ueb-g1.ctb": "en-ueb-g1-brlMultiline",
	"en-ueb-g2.ctb": "en-ueb-g2-brlMultiline",
	"en-us-g1.ctb": "en-us-g1-brlMultiline",
	"en-us-comp6.ctb": "en-us-comp6-brlMultiline",
}

#: Characters with no meaning to a reader: zero width spaces and joiners, direction marks,
#: embeddings and isolates, invisible operators, variation selectors, the byte order mark, and the
#: object replacement character, which Chromium puts in an application's text where an embedded
#: object (an icon, an image, a button) sits; the object is reported on its own, and speech says
#: nothing for the character.
DROP = [
	*range(0x200B, 0x2010),
	*range(0x202A, 0x202F),
	*range(0x2060, 0x2065),
	*range(0x2066, 0x206A),
	*range(0xFE00, 0xFE10),
	0xFEFF,
	0xFFFC,
]

#: Characters better written as another character than by name.
SUBSTITUTE = {
	# List bullets of other shapes, as the plain bullet.
	**dict.fromkeys(
		(0x2023, 0x2043, 0x25AA, 0x25AB, 0x25B6, 0x25B8, 0x25B9, 0x25BA, 0x25CF, 0x25E6), "\u2022"
	),
	# Hyphens other than the ASCII one, as it, and the horizontal bar as an em dash: these sit
	# inside words, where a name would split the word in two.
	**dict.fromkeys((0x2010, 0x2011, 0x2012), "-"),
	0x2015: "—",
	# Box drawing, as the ASCII a terminal falls back to.
	**dict.fromkeys(range(0x250C, 0x254C), "+"),
	**dict.fromkeys(range(0x2552, 0x2571), "+"),
	**dict.fromkeys(
		(
			0x2500,
			0x2501,
			0x2504,
			0x2505,
			0x2508,
			0x2509,
			0x254C,
			0x254D,
			0x2550,
			0x2574,
			0x2576,
			0x2578,
			0x257A,
		),
		"-",
	),
	**dict.fromkeys(
		(
			0x2502,
			0x2503,
			0x2506,
			0x2507,
			0x250A,
			0x250B,
			0x254E,
			0x254F,
			0x2551,
			0x2575,
			0x2577,
			0x2579,
			0x257B,
		),
		"|",
	),
	0x2571: "/",
	0x2572: "\\",
	0x2573: "x",
}

#: The cell written on each side of a name.
NAME_MARK = "12345678"

VS16 = "\ufe0f"

USER_RULES = "brlMultiline-user.cti"
"""The reader's own rules, which every table includes first. Written by the add-on, not here."""


def readSymbols(path: Path) -> dict[str, str]:
	""":return: identifier -> spoken name, from the `symbols:` section of an NVDA symbol file."""
	names = {}
	section = None
	for line in path.read_text(encoding="utf-8-sig").splitlines():
		if line.endswith(":") and "\t" not in line:
			section = line
			continue
		if section != "symbols:" or not line or line.startswith("#"):
			continue
		identifier, _, rest = line.partition("\t")
		name = rest.split("\t", 1)[0].strip()
		if identifier and name:
			names[identifier] = name
	return names


def isUndefined(table: list[str], text: str) -> bool:
	""":return: whether the table writes any character of the text as a liblouis escape.

	Asked as cells, with and without liblouis's `noUndefined` mode, which leaves the escape out:
	the two differ only when there is one. Asking for text instead and looking for the escape
	fails on the UEB tables, which have no text for some eight dot cells.
	"""
	mode = louis.dotsIO
	return louis.translateString(table, text, mode=mode) != louis.translateString(
		table, text, mode=mode | louis.noUndefined
	)


def dotsOf(table: list[str], text: str) -> str:
	""":return: the cells the table writes for the text, as a liblouis dot pattern operand."""
	cells = []
	for c in louis.translateString(table, text, mode=louis.dotsIO):
		bits = ord(c) & 0xFF
		cells.append("".join(str(dot + 1) for dot in range(8) if bits & (1 << dot)) or "0")
	return "-".join(cells)


def escape(text: str) -> str:
	""":return: the characters as a liblouis operand, in ASCII."""
	out = []
	for c in text:
		cp = ord(c)
		if c == " ":
			out.append("\\s")
		elif c == "\t":
			out.append("\\t")
		elif c == "\\":
			out.append("\\\\")
		elif 0x20 < cp < 0x7F:
			out.append(c)
		elif cp <= 0xFFFF:
			out.append(f"\\x{cp:04x}")
		else:
			out.append(f"\\y{cp:05x}")
	return "".join(out)


def rulesFor(stock: str) -> tuple[list[str], list[str], dict[str, int]]:
	""":return: the rules for one stock table, its sequence rules apart, and counts of each kind."""
	table = [str(STOCK_TABLES / stock)]
	names = readSymbols(LOCALE / "cldr.dic")
	# symbols.dic is NVDA's own and wins where both name a character.
	names.update(readSymbols(LOCALE / "symbols.dic"))

	drops = [chr(cp) for cp in DROP if isUndefined(table, chr(cp))]
	substitutes = {chr(cp): r for cp, r in SUBSTITUTE.items() if isUndefined(table, chr(cp))}
	handled = set(drops) | set(substitutes)

	# Single characters and sequences, each with the name to write for it. A sequence is also
	# taken without its variation selectors, since text arrives both ways.
	singles: dict[str, str] = {}
	sequences: dict[str, str] = {}
	for identifier, name in names.items():
		if all(ord(c) < 0x80 for c in identifier):
			continue
		for form in {identifier, identifier.replace(VS16, "")}:
			if not form or form in handled or not isUndefined(table, form):
				continue
			if len(form) == 1:
				singles[form] = name
			else:
				sequences.setdefault(form, name)
	# A character that only ever appears inside a sequence still has to be defined for the
	# sequence to match. One with no name of its own gets its Unicode name.
	for form in sequences:
		for c in form:
			if c in singles or c in handled or not isUndefined(table, c):
				continue
			try:
				singles[c] = unicodedata.name(c).lower()
			except ValueError:
				continue

	def marked(name: str) -> str:
		return f"{NAME_MARK}-{dotsOf(table, name)}-{NAME_MARK}"

	rules = [f"noback sign {escape(c)} {marked(singles[c])}" for c in sorted(singles)]
	sequenceRules = [f"noback always {escape(form)} {marked(sequences[form])}" for form in sorted(sequences)]
	for c, replacement in sorted(substitutes.items()):
		# A table can lack the replacement too: 6 dot computer braille has no bullet. Then the
		# character reads as the replacement does there, by its name.
		if not isUndefined(table, replacement):
			rules.append(f"noback sign {escape(c)} {dotsOf(table, replacement)}")
		elif replacement in singles:
			rules.append(f"noback sign {escape(c)} {marked(singles[replacement])}")
	for c in drops:
		rules.append(f"noback replace {escape(c)}")
	counts = {
		"named": len(singles),
		"sequences": len(sequences),
		"substituted": len(substitutes),
		"dropped": len(drops),
	}
	return rules, sequenceRules, counts


def write(stock: str, name: str) -> None:
	rules, sequenceRules, counts = rulesFor(stock)
	# Sequences in a file of their own: emoji sequences are the bulk of it, and one file with
	# both is past the size the repository's pre-commit hook allows.
	for fileName, what, lines in (
		(f"{name}.cti", "dropped, substituted, or named", rules),
		(f"{name}-sequences.cti", "emoji sequences, named", sequenceRules),
	):
		header = (
			"# Generated by tools/makeBrailleTables.py from NVDA's symbols.dic and cldr.dic.\n"
			f"# Characters {stock} leaves undefined: {what}.\n"
			"# Do not edit; change the generator and run it again.\n\n"
		)
		(OUT / fileName).write_text(header + "\n".join(lines) + "\n", encoding="ascii", newline="\n")
	# The reader's own substitutions first, so their rules are the ones in force wherever they
	# match. That file is written by the add-on; see `userBrailleTable`.
	(OUT / f"{name}.utb").write_text(
		f"# {stock} with BrlMultiline's rules for the characters it leaves undefined.\n"
		"# Generated by tools/makeBrailleTables.py.\n\n"
		f"include {USER_RULES}\ninclude {name}.cti\ninclude {name}-sequences.cti\ninclude {stock}\n",
		encoding="ascii",
		newline="\n",
	)
	print(f"{name}: {counts}")


SAMPLES = [
	"\u2068Peter Tarr\u2069 (\u2068#\U0001f4caintraday-insights\u2069, \u2068\u256d\u2500\u2500 \U0001f512Insider Access",
	"\u2764\ufe0f thanks\U0001f525\U0001f525 end\U0001f525",
	"a\U0001f512b, x \U0001f512 y, \U0001f512.",
	"hi\U0001f468\u200d\U0001f469\u200d\U0001f467ok \U0001f44d\U0001f3fdyes \U0001f1fa\U0001f1f8!",
	"\u25e6 item \u2192 next \u2713 done \u2022 plain",
]


def check(stock: str, name: str) -> None:
	"""Compile the written table, time it against the stock one, and show sample lines."""
	ours = [str(OUT / f"{name}.utb")]
	theirs = [str(STOCK_TABLES / stock)]
	start = time.perf_counter()
	louis.checkTable(ours)
	print(f"  compiled in {(time.perf_counter() - start) * 1000:.0f} ms")
	line = " ".join(SAMPLES)
	for label, table in (("stock", theirs), ("fixed", ours)):
		start = time.perf_counter()
		for _ in range(200):
			louis.translateString(table, line, mode=louis.dotsIO)
		print(f"  {label}: {(time.perf_counter() - start) / 200 * 1000:.3f} ms a line")
	for sample in SAMPLES:
		before = len(louis.translateString(theirs, sample, mode=louis.dotsIO))
		after = "".join(
			chr(0x2800 + (ord(c) & 0xFF)) for c in louis.translateString(ours, sample, mode=louis.dotsIO)
		)
		print(f"  {before} -> {len(after)} cells: {after}")


def main() -> None:
	sys.stdout.reconfigure(encoding="utf-8")
	OUT.mkdir(parents=True, exist_ok=True)
	# Every table includes it, so none compiles without it. The add-on writes the reader's;
	# an empty one stands in for checking here when there is none yet.
	if not (OUT / USER_RULES).exists():
		(OUT / USER_RULES).write_text("# No substitutions.\n", encoding="ascii", newline="\n")
	for stock, name in TABLES.items():
		write(stock, name)
	for stock, name in TABLES.items():
		print(name)
		check(stock, name)


if __name__ == "__main__":
	main()
