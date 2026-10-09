# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the reader's personal braille tables: what is written, and what is listed for NVDA.

Writing the manifest itself needs `configobj` and NVDA's manifest reader, which this test run does
not have; it was checked against a real NVDA. These hold the files and the entries to their shape.
See `personalTables`.
"""

import os
import shutil
import tempfile
import unittest

from ._stubs import installStubs

installStubs()

from brlMultiline import personalTables  # noqa: E402
from brlMultiline.brailleSubstitutions import PersonalTable  # noqa: E402

SPANISH = PersonalTable("es-g1.ctb", "Spanish grade 1")
UK = PersonalTable("en-gb-g2.ctb", "English (U.K.) grade 2", contracted=True)


class TestNames(unittest.TestCase):
	def test_theFileIsNamedAfterTheTable(self):
		self.assertEqual(personalTables.wrapperName("es-g1.ctb"), "es-g1.ctb-brlMultiline-personal.utb")
		self.assertEqual(
			personalTables.wrapperName("en-us-comp8-ext.utb"), "en-us-comp8-ext.utb-brlMultiline-personal.utb"
		)

	def test_theTableIsTheSubstitutionsThenTheNVDATable(self):
		lines = [
			line
			for line in personalTables.wrapperText("es-g1.ctb").splitlines()
			if line and not line.startswith("#")
		]
		self.assertEqual(lines, ["include brlMultiline-user.cti", "include es-g1.ctb"])


class TestTablesOfOneName(unittest.TestCase):
	# NVDA has both: Bulgarian computer braille and Bulgarian grade 1.
	COMPUTER = PersonalTable("bg.ctb", "Bulgarian 8 dot computer braille")
	GRADE1 = PersonalTable("bg.utb", "Bulgarian grade 1")

	def test_theyAreTwoTables(self):
		self.assertNotEqual(personalTables.wrapperName("bg.ctb"), personalTables.wrapperName("bg.utb"))
		self.assertEqual(len(personalTables.manifestEntries([self.COMPUTER, self.GRADE1])), 2)

	def test_eachIncludesItsOwn(self):
		self.assertIn("include bg.ctb\n", personalTables.wrapperText("bg.ctb"))
		self.assertIn("include bg.utb\n", personalTables.wrapperText("bg.utb"))


class TestManifestEntries(unittest.TestCase):
	def test_eachTableIsListedForOutputOnly(self):
		self.assertEqual(
			personalTables.manifestEntries([SPANISH, UK]),
			{
				"es-g1.ctb-brlMultiline-personal.utb": {
					"displayName": "Spanish grade 1 (BrlMultiline personal)",
					"contracted": "False",
					"output": "True",
					"input": "False",
				},
				"en-gb-g2.ctb-brlMultiline-personal.utb": {
					"displayName": "English (U.K.) grade 2 (BrlMultiline personal)",
					"contracted": "True",
					"output": "True",
					"input": "False",
				},
			},
		)


class TestWrappers(unittest.TestCase):
	def setUp(self):
		self.folder = tempfile.mkdtemp()

	def tearDown(self):
		shutil.rmtree(self.folder, ignore_errors=True)

	def names(self) -> list:
		return sorted(os.listdir(self.folder))

	def test_eachTableIsWritten(self):
		self.assertTrue(personalTables._syncWrappers(self.folder, [SPANISH, UK]))
		self.assertEqual(
			self.names(),
			["en-gb-g2.ctb-brlMultiline-personal.utb", "es-g1.ctb-brlMultiline-personal.utb"],
		)

	def test_nothingIsWrittenWhenNothingChanged(self):
		personalTables._syncWrappers(self.folder, [SPANISH])
		self.assertFalse(personalTables._syncWrappers(self.folder, [SPANISH]))

	def test_bothTablesOfOneNameAreWritten(self):
		personalTables._syncWrappers(self.folder, [TestTablesOfOneName.COMPUTER, TestTablesOfOneName.GRADE1])
		self.assertEqual(
			self.names(),
			["bg.ctb-brlMultiline-personal.utb", "bg.utb-brlMultiline-personal.utb"],
		)

	def test_aTableNoLongerWantedButInUseIsKept(self):
		personalTables._syncWrappers(self.folder, [SPANISH, UK])
		inUse = {personalTables.wrapperName(SPANISH.base)}
		personalTables._syncWrappers(self.folder, [UK], inUse)
		self.assertIn("es-g1.ctb-brlMultiline-personal.utb", self.names())
		# After the restart NVDA no longer has it, and it goes.
		personalTables._syncWrappers(self.folder, [UK], set())
		self.assertNotIn("es-g1.ctb-brlMultiline-personal.utb", self.names())

	def test_aTableNamedTheEarlierWayIsReplaced(self):
		open(os.path.join(self.folder, "es-g1-brlMultiline-personal.utb"), "w").close()
		personalTables._syncWrappers(self.folder, [SPANISH])
		self.assertEqual(self.names(), ["es-g1.ctb-brlMultiline-personal.utb"])

	def test_aTableNoLongerWantedIsRemovedAndNothingElse(self):
		open(os.path.join(self.folder, "en-us-g2-brlMultiline.utb"), "w").close()
		personalTables._syncWrappers(self.folder, [SPANISH, UK])
		self.assertTrue(personalTables._syncWrappers(self.folder, [UK]))
		self.assertEqual(
			self.names(), ["en-gb-g2.ctb-brlMultiline-personal.utb", "en-us-g2-brlMultiline.utb"]
		)


if __name__ == "__main__":
	unittest.main()
