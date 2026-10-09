# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for keeping the reader's braille substitutions and putting them in force.

What these hold to is that nothing the reader typed is lost: a save replaces the list whole and
keeps the one before, a list that cannot be read is set aside rather than overwritten, and the rules
file is written only when what it should hold has changed. liblouis is a stand-in that counts how
often its compiled tables are freed. See `userBrailleTable`.
"""

import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

from ._stubs import installStubs

installStubs()

from brlMultiline import brailleSubstitutions as bs  # noqa: E402
from brlMultiline import userBrailleTable  # noqa: E402
from brlMultiline.brailleSubstitutions import DOTS, TEXT, Substitution  # noqa: E402

ENTRIES = [Substitution("Travis Roth", TEXT, "TR"), Substitution("\u2022", DOTS, "456-256", wholeWord=False)]


class FakeLouis:
	def __init__(self):
		self.freed = 0
		self.liblouis = types.SimpleNamespace(lou_free=self._free)

	def _free(self):
		self.freed += 1


class Profile:
	def __init__(self, name):
		self.name = name


class StoreTestCase(unittest.TestCase):
	def setUp(self):
		self.folder = tempfile.mkdtemp()
		self.config = os.path.join(self.folder, "config")
		self.tables = os.path.join(self.folder, "brailleTables")
		os.makedirs(self.config)
		os.makedirs(self.tables)
		self.louis = FakeLouis()
		self.appArgs = types.SimpleNamespace(secure=False)
		self.modules = mock.patch.dict(
			sys.modules,
			{
				"NVDAState": types.SimpleNamespace(WritePaths=types.SimpleNamespace(configDir=self.config)),
				"globalVars": types.SimpleNamespace(appArgs=self.appArgs),
				"louis": self.louis,
				# The tables NVDA knows: a personal table is among them only after a restart.
				"brailleTables": types.SimpleNamespace(listTables=lambda: list(self.registered)),
			},
		)
		self.registered = []
		self.synced = []
		self.sync = mock.patch.object(
			userBrailleTable.personalTables,
			"sync",
			side_effect=lambda folder, tables: self.synced.append(list(tables)) or True,
		)
		self.sync.start()
		self.modules.start()
		self.tablesDir = mock.patch.object(userBrailleTable, "tablesDir", return_value=self.tables)
		self.tablesDir.start()
		self.profiles = [Profile(None)]
		self.conf = mock.patch.object(
			userBrailleTable.config, "conf", types.SimpleNamespace(profiles=self.profiles)
		)
		self.conf.start()
		userBrailleTable._store = None
		userBrailleTable._loadProblem = None

	def tearDown(self):
		self.sync.stop()
		self.conf.stop()
		self.tablesDir.stop()
		self.modules.stop()
		userBrailleTable._store = None
		userBrailleTable._onChanged = None
		shutil.rmtree(self.folder, ignore_errors=True)

	def storeText(self) -> str:
		with open(userBrailleTable.storePath(), encoding="utf-8") as file:
			return file.read()

	def writeStore(self, text: str, suffix: str = "") -> None:
		os.makedirs(userBrailleTable.storeDir(), exist_ok=True)
		with open(userBrailleTable.storePath() + suffix, "w", encoding="utf-8") as file:
			file.write(text)

	def rulesText(self) -> str:
		with open(userBrailleTable.rulesPath(), encoding="utf-8") as file:
			return file.read()


class TestSaving(StoreTestCase):
	def test_aSavedListIsReadBack(self):
		userBrailleTable.save(ENTRIES)
		userBrailleTable._store = None
		self.assertEqual(userBrailleTable.entries(), ENTRIES)

	def test_theFolderIsMadeIfItIsNotThere(self):
		userBrailleTable.save(ENTRIES)
		self.assertTrue(os.path.isfile(userBrailleTable.storePath()))

	def test_theListReplacedIsKeptAsABackup(self):
		userBrailleTable.save(ENTRIES[:1])
		userBrailleTable.save(ENTRIES)
		with open(userBrailleTable.storePath() + ".bak", encoding="utf-8") as file:
			self.assertEqual(bs.fromText(file.read()).entries, ENTRIES[:1])

	def test_noTemporaryFileIsLeftBehind(self):
		userBrailleTable.save(ENTRIES)
		self.assertEqual(sorted(os.listdir(userBrailleTable.storeDir())), ["brailleSubstitutions.json"])

	def test_nothingIsSavedInASecureSession(self):
		self.appArgs.secure = True
		with self.assertRaises(userBrailleTable.NotSaved):
			userBrailleTable.save(ENTRIES)
		self.assertFalse(os.path.exists(userBrailleTable.storePath()))

	def test_aListThatCannotBeWrittenLeavesTheOldOneAndSaysSo(self):
		userBrailleTable.save(ENTRIES[:1])
		with mock.patch.object(userBrailleTable.os, "replace", side_effect=OSError("disk full")):
			with self.assertRaises(userBrailleTable.NotSaved):
				userBrailleTable.save(ENTRIES)
		self.assertEqual(bs.fromText(self.storeText()).entries, ENTRIES[:1])
		self.assertEqual(userBrailleTable.entries(), ENTRIES[:1])

	def test_theEditedCopyIsNotTheStoredList(self):
		userBrailleTable.save(ENTRIES)
		copy = userBrailleTable.entries()
		copy.append(Substitution("x"))
		self.assertEqual(userBrailleTable.entries(), ENTRIES)


class TestReading(StoreTestCase):
	def test_noListIsAnEmptyList(self):
		self.assertEqual(userBrailleTable.entries(), [])
		self.assertIsNone(userBrailleTable.loadProblem())

	def test_aDamagedListIsSetAsideAndTheBackupUsed(self):
		self.writeStore(bs.toText(ENTRIES[:1]), suffix=".bak")
		self.writeStore("{ not json")
		self.assertEqual(userBrailleTable.entries(), ENTRIES[:1])
		names = os.listdir(userBrailleTable.storeDir())
		self.assertTrue(any(name.startswith("brailleSubstitutions.json.damaged-") for name in names))
		self.assertIn("copy saved before", userBrailleTable.loadProblem())

	def test_aDamagedListWithNoBackupIsKeptAndTheReaderTold(self):
		self.writeStore("{ not json")
		self.assertEqual(userBrailleTable.entries(), [])
		names = os.listdir(userBrailleTable.storeDir())
		self.assertTrue(any(name.startswith("brailleSubstitutions.json.damaged-") for name in names))
		self.assertIsNotNone(userBrailleTable.loadProblem())

	def test_savingClearsWhatWentWrongReading(self):
		self.writeStore("{ not json")
		userBrailleTable.entries()
		userBrailleTable.save(ENTRIES)
		self.assertIsNone(userBrailleTable.loadProblem())


class TestApplying(StoreTestCase):
	def test_theRulesAreWrittenAndLiblouisFreed(self):
		userBrailleTable.save(ENTRIES)
		before = bs.generation()
		self.assertTrue(userBrailleTable.apply())
		self.assertIn("# 1: Travis Roth", self.rulesText())
		self.assertEqual(self.louis.freed, 1)
		self.assertNotEqual(bs.generation(), before)

	def test_nothingIsDoneWhenTheRulesAreTheSame(self):
		userBrailleTable.save(ENTRIES)
		userBrailleTable.apply()
		self.assertFalse(userBrailleTable.apply())
		self.assertEqual(self.louis.freed, 1)

	def test_anEmptyListStillWritesTheFileTheTablesInclude(self):
		self.assertTrue(userBrailleTable.apply())
		self.assertTrue(os.path.isfile(userBrailleTable.rulesPath()))

	def test_aProfilesEntriesComeAndGoWithIt(self):
		userBrailleTable.save([Substitution("work", TEXT, "W", profiles=("Office",))])
		userBrailleTable.apply()
		self.assertNotIn("# 1:", self.rulesText())
		self.profiles.append(Profile("Office"))
		self.assertTrue(userBrailleTable.apply())
		self.assertIn("# 1:", self.rulesText())

	def test_aProfileSwitchThatChangesTheRulesRedraws(self):
		calls = []
		userBrailleTable.save([Substitution("work", TEXT, "W", profiles=("Office",))])
		userBrailleTable.install(lambda: calls.append("drawn"))
		calls.clear()
		self.profiles.append(Profile("Office"))
		userBrailleTable._handleProfileSwitch()
		self.assertEqual(calls, ["drawn"])
		userBrailleTable._handleProfileSwitch()
		self.assertEqual(calls, ["drawn"])
		userBrailleTable.remove()

	def test_committingSavesAndRedraws(self):
		calls = []
		userBrailleTable.install(lambda: calls.append("drawn"))
		calls.clear()
		userBrailleTable.commit(ENTRIES)
		self.assertEqual(calls, ["drawn"])
		self.assertEqual(bs.fromText(self.storeText()).entries, ENTRIES)
		userBrailleTable.remove()


class TestPersonalTables(StoreTestCase):
	SPANISH = bs.PersonalTable("es-g1.ctb", "Spanish grade 1")

	def test_theTablesAreSavedWithTheList(self):
		userBrailleTable.save(ENTRIES, [self.SPANISH])
		userBrailleTable._store = None
		self.assertEqual(userBrailleTable.tables(), [self.SPANISH])

	def test_savingTheListAloneKeepsTheTables(self):
		userBrailleTable.save(ENTRIES, [self.SPANISH])
		userBrailleTable.save(ENTRIES[:1])
		self.assertEqual(userBrailleTable.tables(), [self.SPANISH])

	def test_aNewPersonalTableNeedsARestart(self):
		self.assertTrue(userBrailleTable.commit(ENTRIES, [self.SPANISH]))
		self.assertEqual(self.synced[-1], [self.SPANISH])

	def test_onceNVDAKnowsItNoRestartIsNeeded(self):
		self.registered.append(types.SimpleNamespace(fileName="es-g1-brlMultiline-personal.utb"))
		self.assertFalse(userBrailleTable.commit(ENTRIES, [self.SPANISH]))

	def test_removingOneNVDAStillKnowsNeedsARestart(self):
		self.registered.append(types.SimpleNamespace(fileName="es-g1-brlMultiline-personal.utb"))
		self.assertTrue(userBrailleTable.commit(ENTRIES, []))

	def test_aPersonalTableUsesTheSubstitutions(self):
		userBrailleTable.save(ENTRIES, [self.SPANISH])
		self.assertTrue(userBrailleTable.usesSubstitutions("es-g1-brlMultiline-personal.utb"))
		self.assertFalse(userBrailleTable.usesSubstitutions("es-g1.ctb"))

	def test_aManifestThatCannotBeWrittenStillSavesTheList(self):
		self.sync.stop()
		failing = mock.patch.object(
			userBrailleTable.personalTables,
			"sync",
			side_effect=userBrailleTable.personalTables.NotListed("would change name"),
		)
		failing.start()
		try:
			with self.assertRaises(userBrailleTable.NotListed):
				userBrailleTable.commit(ENTRIES, [self.SPANISH])
		finally:
			failing.stop()
			self.sync.start()
		self.assertEqual(bs.fromText(self.storeText()).tables, [self.SPANISH])

	def test_nothingIsMadeInASecureSession(self):
		self.appArgs.secure = True
		self.assertFalse(userBrailleTable.syncPersonalTables())
		self.assertEqual(self.synced, [])


class TestTables(StoreTestCase):
	def test_ourTablesLeaveOutCandidatesLeftBehind(self):
		for name in (
			"en-us-g2-brlMultiline.utb",
			"en-us-g2-brlMultiline.cti",
			"brlMultiline-user.check.en-us-g2-brlMultiline.utb",
			"en-us-g2.ctb",
		):
			open(os.path.join(self.tables, name), "w").close()
		self.assertEqual(userBrailleTable.ourTables(), ["en-us-g2-brlMultiline.utb"])


class TestRefusals(unittest.TestCase):
	def test_aRefusalIsPutDownToTheEntryItsLineBelongsTo(self):
		text = bs.rulesText([Substitution("a", TEXT, "b"), Substitution("", bs.RULE, "noback oops")])
		lineNumber = text.split("\n").index("noback oops") + 1
		messages = [
			f"C:\\addon\\brailleTables\\brlMultiline-user.check.cti:{lineNumber}: error: opcode 'oops' not defined.",
			"1 errors found.",
		]
		self.assertEqual(
			userBrailleTable._refusalsFrom(messages, text),
			[userBrailleTable.Refusal(1, "error: opcode 'oops' not defined.")],
		)

	def test_messagesAboutOtherFilesAreNotRefusals(self):
		self.assertEqual(
			userBrailleTable._refusalsFrom(["en-us-g2.ctb:3: error: x", "1 errors found."], ""), []
		)


if __name__ == "__main__":
	unittest.main()
