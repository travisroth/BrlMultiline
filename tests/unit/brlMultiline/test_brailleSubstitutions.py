# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the reader's braille substitutions as data and as liblouis rules.

What liblouis does with the rules was found by compiling them against NVDA's tables; these tests
hold the rules to the shapes that were found to work. See `brailleSubstitutions`.
"""

import json
import unittest

from ._stubs import installStubs

installStubs()

from brlMultiline import brailleSubstitutions as bs  # noqa: E402
from brlMultiline.brailleSubstitutions import DOTS, RULE, TEXT, Substitution  # noqa: E402


class TestCaseForms(unittest.TestCase):
	def test_matchCaseIsTheTextAsTyped(self):
		self.assertEqual(bs.caseForms("Travis Roth", matchCase=True), ["Travis Roth"])

	def test_anyCaseCoversTheFormsAReaderMeets(self):
		self.assertEqual(
			bs.caseForms("Travis Roth", matchCase=False),
			["Travis Roth", "travis roth", "TRAVIS ROTH", "Travis roth"],
		)

	def test_eachFormIsGivenOnce(self):
		self.assertEqual(bs.caseForms("nvda", matchCase=False), ["nvda", "NVDA", "Nvda"])

	def test_anApostropheDoesNotCapitaliseWhatFollowsIt(self):
		# `str.title` would give "Don'T Panic".
		self.assertIn("Don't Panic", bs.caseForms("don't panic", matchCase=False))
		self.assertNotIn("Don'T Panic", bs.caseForms("don't panic", matchCase=False))

	def test_aNameIsFoundAsTypedAsWellAsInTheUsualForms(self):
		self.assertEqual(bs.caseForms("O'Neil", matchCase=False), ["O'Neil", "o'neil", "O'NEIL", "O'neil"])

	def test_aSymbolHasOneForm(self):
		self.assertEqual(bs.caseForms("•", matchCase=False), ["•"])


class TestEscape(unittest.TestCase):
	def test_spacesAndBackslashesAreEscaped(self):
		self.assertEqual(bs.escape("a b\\c"), "a\\sb\\\\c")

	def test_aQuotationMarkIsEscapedWithABackslash(self):
		# Not by its code, which liblouis decodes before it finds the end of the string.
		self.assertEqual(bs.escape('say "hi"'), 'say\\s\\"hi\\"')

	def test_anythingOutsideAsciiIsWrittenByItsCode(self):
		self.assertEqual(bs.escape("•\U0001f4ac"), "\\x2022\\y1f4ac")

	def test_theOutputIsAscii(self):
		bs.escape("café \U0001f525").encode("ascii")


class TestDots(unittest.TestCase):
	def test_eachCellIsPutInOrder(self):
		self.assertEqual(bs.normalizedDots(" 5421-0- 3"), "1245-0-3")

	def test_badDotsAreRefused(self):
		for text in ("", "9", "12-", "1-a", "11", "123456789", "1 2"):
			with self.subTest(text=text):
				with self.assertRaises(ValueError):
					bs.normalizedDots(text)


class TestProblems(unittest.TestCase):
	def test_aGoodEntryHasNone(self):
		self.assertIsNone(bs.problemOf(Substitution("Travis Roth", TEXT, "TR")))

	def test_emptyTextIsAllowedAndRemovesTheMatch(self):
		self.assertIsNone(bs.problemOf(Substitution("hidden", TEXT, "")))

	def test_textToFindIsNeeded(self):
		self.assertEqual(bs.problemOf(Substitution("", TEXT, "x")), bs.NO_MATCH)

	def test_dotsAreNeededAndChecked(self):
		self.assertEqual(bs.problemOf(Substitution("x", DOTS, "")), bs.NO_DOTS)
		self.assertEqual(bs.problemOf(Substitution("x", DOTS, "19")), bs.BAD_DOTS)

	def test_aLineBreakIsRefused(self):
		self.assertEqual(bs.problemOf(Substitution("a\nb", TEXT, "x")), bs.LINE_BREAK)

	def test_aRuleEntryNeedsARuleAndNoInclude(self):
		self.assertEqual(bs.problemOf(Substitution("", RULE, "# only a comment\n")), bs.NO_RULE)
		self.assertEqual(bs.problemOf(Substitution("", RULE, "include other.cti")), bs.RULE_INCLUDES)
		self.assertIsNone(bs.problemOf(Substitution("", RULE, "noback always xyzzy 123456")))


class TestRules(unittest.TestCase):
	def test_textAnywhereIsOneRuleAForm(self):
		rules = bs.rulesFor(Substitution("NVDA", TEXT, "screen reader", wholeWord=False, matchCase=True))
		self.assertEqual(rules, ['noback correct "NVDA" "screen\\sreader"'])

	def test_aWholeWordTakesFourRules(self):
		rules = bs.rulesFor(Substitution("Travis Roth", TEXT, "TR", matchCase=True))
		self.assertEqual(
			rules,
			[
				'noback correct `["Travis\\sRoth"]~ "TR"',
				'noback correct `["Travis\\sRoth"]!$ld_ "TR"',
				'noback correct !$ld["Travis\\sRoth"]~ "TR"',
				'noback correct !$ld["Travis\\sRoth"]!$ld_ "TR"',
			],
		)

	def test_anyCaseRepeatsTheRulesForEachForm(self):
		rules = bs.rulesFor(Substitution("Travis Roth", TEXT, "TR"))
		self.assertEqual(len(rules), 4 * len(bs.caseForms("Travis Roth", False)))

	def test_dotsGoThroughAPlaceholderDefinedAsALetterWithNoLetterSign(self):
		rules = bs.rulesFor(Substitution("•", DOTS, "456-256", wholeWord=False), placeholder=0xF0003)
		self.assertEqual(
			rules,
			[
				'noback correct "\\x2022" "\\yf0003"',
				"noback letter \\yf0003 456-256",
				"noletsign \\yf0003",
			],
		)

	def test_aDotsEntryNeedsAPlaceholder(self):
		with self.assertRaises(ValueError):
			bs.rulesFor(Substitution("x", DOTS, "1"))

	def test_ruleEntriesArePutInAsTheyAreInAscii(self):
		rules = bs.rulesFor(Substitution("mine", RULE, "# a comment\n  noback always é 1\n\n"))
		self.assertEqual(rules, ["noback always \\x00e9 1"])


class TestRulesText(unittest.TestCase):
	ENTRIES = [
		Substitution("Travis Roth", TEXT, "TR"),
		Substitution("•", DOTS, "456-256", wholeWord=False),
		Substitution("work", TEXT, "W", profiles=("Office",)),
		Substitution("off", TEXT, "OFF", enabled=False),
		Substitution("o'neil", DOTS, "1345-145"),
		Substitution("", TEXT, "broken"),
	]

	def test_eachEntryIsMarkedByItsNumber(self):
		text = bs.rulesText(self.ENTRIES, [])
		self.assertIn("# 1: Travis Roth", text)
		self.assertIn("# 2: \\x2022", text)

	def test_aProfilesEntryIsUsedOnlyInIt(self):
		self.assertNotIn("# 3:", bs.rulesText(self.ENTRIES, []))
		self.assertIn("# 3:", bs.rulesText(self.ENTRIES, ["Office"]))

	def test_validationTakesEveryProfile(self):
		self.assertIn("# 3:", bs.rulesText(self.ENTRIES))

	def test_aDisabledEntryAndABrokenOneAreLeftOut(self):
		text = bs.rulesText(self.ENTRIES)
		self.assertNotIn("# 4:", text)
		self.assertNotIn("# 6:", text)

	def test_aDotsEntryKeepsItsCharacterWhicheverProfileIsInForce(self):
		withOffice = bs.rulesText(
			[Substitution("x", DOTS, "1", profiles=("Office",))] + self.ENTRIES[4:5], ["Office"]
		)
		without = bs.rulesText([Substitution("x", DOTS, "1", profiles=("Office",))] + self.ENTRIES[4:5], [])
		self.assertIn("\\yf0001", withOffice)
		self.assertIn("\\yf0001", without)
		self.assertNotIn("\\yf0000", without)

	def test_theFileIsAscii(self):
		bs.rulesText(self.ENTRIES).encode("ascii")

	def test_anEmptyListIsAFileWithOnlyComments(self):
		lines = [line for line in bs.rulesText([], []).splitlines() if line and not line.startswith("#")]
		self.assertEqual(lines, [])


class TestClash(unittest.TestCase):
	def test_twoEntriesForTheSameTextClash(self):
		entries = [Substitution("Travis Roth", TEXT, "TR"), Substitution("travis roth", DOTS, "1")]
		self.assertEqual(bs.clash(entries), (0, 1))

	def test_differentCapitalsWithMatchCaseDoNotClash(self):
		entries = [
			Substitution("NVDA", TEXT, "a", matchCase=True),
			Substitution("nvda", TEXT, "b", matchCase=True),
		]
		self.assertIsNone(bs.clash(entries))

	def test_twoProfilesDoNotClashButOneForAllClashesWithEither(self):
		office = Substitution("x", TEXT, "1", profiles=("Office",))
		home = Substitution("x", TEXT, "2", profiles=("Home",))
		everywhere = Substitution("x", TEXT, "3")
		self.assertIsNone(bs.clash([office, home]))
		self.assertEqual(bs.clash([office, everywhere]), (0, 1))

	def test_entriesWhoseProfilesOverlapClash(self):
		work = Substitution("x", TEXT, "1", profiles=("Outlook", "Teams"))
		chat = Substitution("x", TEXT, "2", profiles=("Teams", "Discord"))
		code = Substitution("x", TEXT, "3", profiles=("Code",))
		self.assertEqual(bs.clash([work, chat]), (0, 1))
		self.assertIsNone(bs.clash([work, code]))

	def test_aDisabledEntryDoesNotClash(self):
		self.assertIsNone(
			bs.clash([Substitution("x", TEXT, "1"), Substitution("x", TEXT, "2", enabled=False)])
		)


class TestProfiles(unittest.TestCase):
	BOTH = Substitution("Travis Roth", TEXT, "TR", profiles=("Outlook", "Teams"))

	def test_oneEntryServesEachOfItsProfiles(self):
		for active in (["Outlook"], ["Teams"], ["Outlook", "Teams"], ["Teams", "Code"]):
			with self.subTest(active=active):
				self.assertIn("# 1:", bs.rulesText([self.BOTH], active))
		self.assertNotIn("# 1:", bs.rulesText([self.BOTH], []))
		self.assertNotIn("# 1:", bs.rulesText([self.BOTH], ["Code"]))

	def test_bothProfilesActiveWriteItsRulesOnce(self):
		text = bs.rulesText([self.BOTH], ["Outlook", "Teams"])
		self.assertEqual(text.count("# 1:"), 1)

	def test_theProfilesAreStoredAsAList(self):
		self.assertEqual(self.BOTH.asStored()["profiles"], ["Outlook", "Teams"])
		self.assertEqual(bs.fromText(bs.toText([self.BOTH])).entries, [self.BOTH])

	def test_anEntryWithOneProfileFromBeforeIsReadAsAListOfOne(self):
		stored = {"match": "x", "profile": "Teams"}
		self.assertEqual(bs.Substitution.fromStored(stored).profiles, ("Teams",))
		self.assertEqual(bs.Substitution.fromStored({"match": "x", "profile": ""}).profiles, ())

	def test_aProfileNamedTwiceIsKeptOnce(self):
		stored = {"match": "x", "profiles": ["Teams", "Teams", "", 3, "Outlook"]}
		self.assertEqual(bs.Substitution.fromStored(stored).profiles, ("Teams", "Outlook"))


class TestStore(unittest.TestCase):
	def test_aListRoundTrips(self):
		entries = TestRulesText.ENTRIES
		tables = [
			bs.PersonalTable("es-g1.ctb", "Spanish grade 1"),
			bs.PersonalTable("en-gb-g2.ctb", "English (U.K.) grade 2", True),
		]
		stored = bs.fromText(bs.toText(entries, tables))
		self.assertEqual(stored.entries, entries)
		self.assertEqual(stored.tables, tables)
		self.assertEqual(stored.leftOut, 0)

	def test_aFirstVersionListHasNoPersonalTables(self):
		stored = bs.fromText(json.dumps({"version": 1, "entries": [{"match": "a"}]}))
		self.assertEqual(stored.entries, [Substitution("a")])
		self.assertEqual(stored.tables, [])

	def test_aPersonalTableMustBeAPlainTableFileName(self):
		# It is written into an `include` line and a manifest key.
		for base in ("../evil.ctb", "a b.ctb", "x.txt", "", None, "es-g1.ctb\ninclude other.ctb"):
			with self.subTest(base=base):
				self.assertIsNone(bs.PersonalTable.fromStored({"base": base, "displayName": "x"}))
		self.assertEqual(bs.PersonalTable.fromStored({"base": "es-g1.ctb"}).displayName, "es-g1.ctb")

	def test_aPersonalTableStoredTwiceIsKeptOnce(self):
		text = json.dumps(
			{"version": 2, "entries": [], "tables": [{"base": "es-g1.ctb"}, {"base": "es-g1.ctb"}]}
		)
		stored = bs.fromText(text)
		self.assertEqual(len(stored.tables), 1)
		self.assertEqual(stored.leftOut, 1)

	def test_textIsStoredReadably(self):
		self.assertIn("•", bs.toText([Substitution("•", DOTS, "456-256")]))

	def test_aRecordThatIsNotAnEntryIsLeftOutAndCounted(self):
		text = json.dumps(
			{
				"version": 1,
				"entries": [{"match": "a"}, {"kind": "text"}, "nonsense", {"match": "b", "kind": "?"}],
			}
		)
		stored = bs.fromText(text)
		self.assertEqual(stored.entries, [Substitution("a")])
		self.assertEqual(stored.leftOut, 3)

	def test_somethingThatIsNotAListIsRefused(self):
		for text in ("", "{", "[]", '{"version": 1}', '{"entries": []}'):
			with self.subTest(text=text):
				with self.assertRaises(bs.StoreError):
					bs.fromText(text)

	def test_aListFromALaterVersionIsRefusedRatherThanCutDown(self):
		with self.assertRaises(bs.StoreError):
			bs.fromText(json.dumps({"version": bs.STORE_VERSION + 1, "entries": []}))


class TestGeneration(unittest.TestCase):
	def test_itChangesWhenTheRulesDo(self):
		before = bs.generation()
		bs.rulesChanged()
		self.assertNotEqual(bs.generation(), before)


if __name__ == "__main__":
	unittest.main()
