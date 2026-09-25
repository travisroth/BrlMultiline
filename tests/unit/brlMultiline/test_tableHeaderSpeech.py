# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for silencing the spoken headers of one table.

What is held to: the headers of the silenced table are left out of speech and nothing else
about any cell is; a table with the same ID in another document is not silenced; NVDA's own
configuration is never written; and without a flow — nothing here builds one — it all works.
"""

import gc
import sys
import types
import unittest
from unittest import mock

from ._stubs import FORMAT_CONFIG, FakeTableDocument, ReportTableHeaders, installStubs, resetConfig

installStubs()

import api  # noqa: E402
import speech  # noqa: E402

from brlMultiline import tableHeaderSpeech  # noqa: E402

GRID = [["Symbol", "Price"], ["ABC", "12.5"]]


def _nvdaGetPropertiesSpeech(*args, **propertyValues):
	""":return: what NVDA's `getPropertiesSpeech` was given, standing in for what it would say."""
	return dict(propertyValues)


class FocusedCell:
	"""A focused table cell outside browse mode: a list view, a grid in focus mode, a worksheet."""

	def __init__(self, tableID, treeInterceptor=None):
		self._tableID = tableID
		self.treeInterceptor = treeInterceptor

	@property
	def tableID(self):
		if isinstance(self._tableID, Exception):
			raise self._tableID
		return self._tableID


class InDocument:
	"""The focus inside a browse mode document."""

	def __init__(self, document):
		self.treeInterceptor = document


class TableHeaderSpeechTests(unittest.TestCase):
	def setUp(self):
		resetConfig()
		self.speechModule = types.ModuleType("speech.speech")
		self.speechModule.getPropertiesSpeech = _nvdaGetPropertiesSpeech
		sys.modules["speech.speech"] = self.speechModule
		speech.speech = self.speechModule
		self.focus = None
		patcher = mock.patch.object(api, "getFocusObject", lambda: self.focus)
		patcher.start()
		self.addCleanup(patcher.stop)
		tableHeaderSpeech.install()
		self.addCleanup(self._uninstall)

	def _uninstall(self):
		tableHeaderSpeech.remove()
		sys.modules.pop("speech.speech", None)
		if hasattr(speech, "speech"):
			del speech.speech

	def speak(self, **propertyValues):
		return self.speechModule.getPropertiesSpeech(**propertyValues)

	def cell(self, tableID):
		return self.speak(
			_tableID=tableID,
			rowNumber=2,
			columnNumber=2,
			rowHeaderText="ABC",
			columnHeaderText="Price, the last traded price, updated every fifteen seconds",
		)

	def inBrowseMode(self, tableID=7):
		document = FakeTableDocument(GRID, tableID=tableID, row=2, col=2)
		self.focus = InDocument(document)
		return document

	def test_installReplacesAndRemoveRestores(self):
		self.assertIsNot(self.speechModule.getPropertiesSpeech, _nvdaGetPropertiesSpeech)
		tableHeaderSpeech.remove()
		self.assertIs(self.speechModule.getPropertiesSpeech, _nvdaGetPropertiesSpeech)

	def test_removeLeavesSomeoneElsesReplacement(self):
		def theirs(*args, **kwargs):
			return None

		self.speechModule.getPropertiesSpeech = theirs
		tableHeaderSpeech.remove()
		self.assertIs(self.speechModule.getPropertiesSpeech, theirs)

	def test_nothingSilencedSpeaksHeaders(self):
		said = self.cell(7)
		self.assertEqual(said["rowHeaderText"], "ABC")
		self.assertIn("columnHeaderText", said)

	def test_toggleSilencesTheTableAtTheBrowseModeCursor(self):
		self.inBrowseMode(tableID=7)
		self.assertEqual(tableHeaderSpeech.toggle(), "Table headers not spoken")
		said = self.cell(7)
		self.assertNotIn("rowHeaderText", said)
		self.assertNotIn("columnHeaderText", said)
		# Everything else about the cell is still spoken.
		self.assertEqual((said["rowNumber"], said["columnNumber"]), (2, 2))

	def test_toggleAgainSpeaksThemAgain(self):
		self.inBrowseMode(tableID=7)
		tableHeaderSpeech.toggle()
		self.assertEqual(tableHeaderSpeech.toggle(), "Table headers spoken")
		self.assertIn("columnHeaderText", self.cell(7))

	def test_anotherTableInTheSameDocumentStillSpeaks(self):
		self.inBrowseMode(tableID=7)
		tableHeaderSpeech.toggle()
		self.assertIn("columnHeaderText", self.cell(8))

	def test_theSameTableIDInAnotherDocumentStillSpeaks(self):
		first = self.inBrowseMode(tableID=7)
		tableHeaderSpeech.toggle()
		self.inBrowseMode(tableID=7)
		self.assertIn("columnHeaderText", self.cell(7))
		self.focus = InDocument(first)
		self.assertNotIn("columnHeaderText", self.cell(7))

	def test_reloadingThePageForgetsIt(self):
		self.inBrowseMode(tableID=7)
		tableHeaderSpeech.toggle()
		self.focus = None
		gc.collect()
		self.inBrowseMode(tableID=7)
		self.assertIn("columnHeaderText", self.cell(7))
		tableHeaderSpeech._prune()
		self.assertEqual(tableHeaderSpeech._silenced, [])

	def test_notInATable(self):
		document = self.inBrowseMode()
		document.inTable = False
		self.assertEqual(tableHeaderSpeech.toggle(), "Not in a table")
		self.assertEqual(tableHeaderSpeech._silenced, [])

	def test_nvdaReportingNoHeadersIsSaidAndNothingSilenced(self):
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.OFF.value
		self.inBrowseMode()
		self.assertEqual(tableHeaderSpeech.toggle(), "NVDA is set to report no table headers")
		self.assertEqual(tableHeaderSpeech._silenced, [])

	def test_configurationIsNeverWritten(self):
		self.inBrowseMode()
		before = dict(FORMAT_CONFIG)
		tableHeaderSpeech.toggle()
		self.cell(7)
		self.assertEqual(dict(FORMAT_CONFIG), before)

	def test_focusedTableOutsideBrowseMode(self):
		self.focus = FocusedCell((1234, -56))
		tableHeaderSpeech.toggle()
		self.assertNotIn("columnHeaderText", self.cell((1234, -56)))
		self.assertIn("columnHeaderText", self.cell((1234, -57)))

	def test_focusModeInABrowseModeDocumentUsesTheFocusedCell(self):
		document = FakeTableDocument(GRID, tableID=7, row=2, col=2)
		document.passThrough = True
		self.focus = FocusedCell((99, 7), treeInterceptor=document)
		tableHeaderSpeech.toggle()
		self.assertNotIn("columnHeaderText", self.cell((99, 7)))

	def test_runtimeIDAsAListMatchesItself(self):
		self.focus = FocusedCell([42, 1, 2])
		tableHeaderSpeech.toggle()
		self.assertNotIn("columnHeaderText", self.cell([42, 1, 2]))

	def test_focusWithNoTable(self):
		self.focus = FocusedCell(NotImplementedError())
		self.assertEqual(tableHeaderSpeech.toggle(), "Not in a table")

	def test_speechWithoutATableIDIsUntouched(self):
		self.focus = FocusedCell((1, 2))
		tableHeaderSpeech.toggle()
		said = self.speak(columnHeaderText="Price")
		self.assertEqual(said["columnHeaderText"], "Price")

	def test_aFailureInsideSpeechSpeaksTheHeaders(self):
		self.focus = FocusedCell((1, 2))
		tableHeaderSpeech.toggle()
		with mock.patch.object(tableHeaderSpeech, "isSilenced", side_effect=RuntimeError("boom")):
			self.assertIn("columnHeaderText", self.cell((1, 2)))

	def test_theOldestIsForgottenPastTheLimit(self):
		for number in range(tableHeaderSpeech.MAX_SILENCED + 1):
			self.focus = FocusedCell((number, 0))
			tableHeaderSpeech.toggle()
		self.assertEqual(len(tableHeaderSpeech._silenced), tableHeaderSpeech.MAX_SILENCED)
		self.assertIn("columnHeaderText", self.cell((0, 0)))
		self.assertNotIn("columnHeaderText", self.cell((1, 0)))


if __name__ == "__main__":
	unittest.main()
