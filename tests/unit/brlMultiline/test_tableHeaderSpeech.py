# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for silencing the spoken headers of one table.

What is held to: the headers of the silenced table are left out of speech and nothing else
about any cell is; a table with the same ID in another document is not silenced; NVDA's own
configuration is never written; a saved layout can say it once for every visit; and without a
flow — nothing here builds one — it all works.
"""

import gc
import json
import sys
import types
import unittest
from unittest import mock

from ._stubs import (
	CONFIG,
	FORMAT_CONFIG,
	FakeNavigatorObject,
	FakeTableDocument,
	ReportTableHeaders,
	installStubs,
	resetConfig,
)

installStubs()

import api  # noqa: E402
import speech  # noqa: E402

from brlMultiline import flowTableLayouts, flowTableManager, flowTableSource, tableHeaderSpeech  # noqa: E402
from brlMultiline.flowTableLayouts import FOLLOW, SPEAK_COLUMNS, SPEAK_OFF, SPEAK_ROWS  # noqa: E402

GRID = [["Symbol", "Price"], ["ABC", "12.5"]]
HEADERS = {1: "Symbol", 2: "Price"}
URL = "https://example.com/watchlist"


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


class HeaderSpeechTestCase(unittest.TestCase):
	def setUp(self):
		resetConfig()
		self.addCleanup(resetConfig)
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

	def headersOf(self, tableID):
		said = self.cell(tableID)
		return [name for name in tableHeaderSpeech.HEADER_PROPERTIES if name in said]

	def inBrowseMode(self, tableID=7, url=URL):
		""":return: a new page, as loading it again makes one, with the cursor in its table."""
		document = FakeTableDocument(GRID, tableID=tableID, row=2, col=2, columnHeaders=HEADERS)
		document.documentConstantIdentifier = url
		self.focus = FakeNavigatorObject("a page", treeInterceptor=document)
		return document

	def handle(self):
		return flowTableSource.tableAt(self.focus)


class TestTheCommand(HeaderSpeechTestCase):
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
		self.assertEqual(tableHeaderSpeech._overrides, [])

	def test_anotherTableInTheSameDocumentStillSpeaks(self):
		self.inBrowseMode(tableID=7)
		tableHeaderSpeech.toggle()
		self.assertIn("columnHeaderText", self.cell(8))

	def test_theSameTableIDInAnotherDocumentStillSpeaks(self):
		first = self.inBrowseMode(tableID=7)
		tableHeaderSpeech.toggle()
		self.inBrowseMode(tableID=7)
		self.assertIn("columnHeaderText", self.cell(7))
		self.focus = FakeNavigatorObject("a page", treeInterceptor=first)
		self.assertNotIn("columnHeaderText", self.cell(7))

	def test_reloadingThePageForgetsIt(self):
		self.inBrowseMode(tableID=7)
		tableHeaderSpeech.toggle()
		self.focus = None
		gc.collect()
		self.inBrowseMode(tableID=7)
		self.assertIn("columnHeaderText", self.cell(7))
		tableHeaderSpeech._prune()
		self.assertEqual(tableHeaderSpeech._overrides, [])

	def test_notInATable(self):
		document = self.inBrowseMode()
		document.inTable = False
		self.assertEqual(tableHeaderSpeech.toggle(), "Not in a table")
		self.assertEqual(tableHeaderSpeech._overrides, [])

	def test_nvdaReportingNoHeadersIsSaidAndNothingSilenced(self):
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.OFF.value
		self.inBrowseMode()
		self.assertEqual(tableHeaderSpeech.toggle(), "NVDA is set to report no table headers")
		self.assertEqual(tableHeaderSpeech._overrides, [])

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
		with mock.patch.object(tableHeaderSpeech, "speakHeadersFor", side_effect=RuntimeError("boom")):
			self.assertIn("columnHeaderText", self.cell((1, 2)))

	def test_theOldestIsForgottenPastTheLimit(self):
		for number in range(tableHeaderSpeech.MAX_OVERRIDES + 1):
			self.focus = FocusedCell((number, 0))
			tableHeaderSpeech.toggle()
		self.assertEqual(len(tableHeaderSpeech._overrides), tableHeaderSpeech.MAX_OVERRIDES)
		self.assertIn("columnHeaderText", self.cell((0, 0)))
		self.assertNotIn("columnHeaderText", self.cell((1, 0)))


class TestTheSavedSetting(HeaderSpeechTestCase):
	def test_savingTheToggleKeepsItAcrossPageLoads(self):
		self.inBrowseMode()
		tableHeaderSpeech.toggle()
		self.assertEqual(tableHeaderSpeech.save(), "This table's headers will not be spoken")
		self.assertEqual(tableHeaderSpeech._overrides, [])
		self.focus = None
		gc.collect()
		self.inBrowseMode()
		self.assertEqual(self.headersOf(7), [])

	def test_aLayoutSavedForSpeechDoesNotAskForColumns(self):
		self.inBrowseMode()
		self.assertTrue(flowTableLayouts.rememberHeaderSpeech(self.handle(), SPEAK_OFF))
		layout = flowTableLayouts.layoutFor(self.handle())
		self.assertEqual(layout.speakHeaders, SPEAK_OFF)
		self.assertTrue(layout.speechOnly)
		self.assertFalse(layout.laysOut)

	def test_rowsOnlyLeavesOutTheColumnHeaders(self):
		self.inBrowseMode()
		flowTableLayouts.rememberHeaderSpeech(self.handle(), SPEAK_ROWS)
		self.assertEqual(self.headersOf(7), ["rowHeaderText"])

	def test_columnsOnlyLeavesOutTheRowHeaders(self):
		self.inBrowseMode()
		flowTableLayouts.rememberHeaderSpeech(self.handle(), SPEAK_COLUMNS)
		self.assertEqual(self.headersOf(7), ["columnHeaderText"])

	def test_theToggleSpeaksASavedSilencedTableUntilThePageIsLoadedAgain(self):
		self.inBrowseMode()
		flowTableLayouts.rememberHeaderSpeech(self.handle(), SPEAK_OFF)
		self.assertEqual(tableHeaderSpeech.toggle(), "Table headers spoken")
		self.assertEqual(len(self.headersOf(7)), 2)
		self.assertEqual(tableHeaderSpeech.toggle(), "Table headers not spoken")
		# Back to what is saved, rather than a second override saying the same.
		self.assertEqual(tableHeaderSpeech._overrides, [])
		self.assertEqual(self.headersOf(7), [])

	def test_savingSpokenOverASpeechOnlyLayoutDeletesIt(self):
		self.inBrowseMode()
		flowTableLayouts.rememberHeaderSpeech(self.handle(), SPEAK_OFF)
		tableHeaderSpeech.toggle()
		self.assertEqual(tableHeaderSpeech.save(), "This table's headers will be spoken as NVDA's settings say")
		self.assertEqual(flowTableLayouts.stored(), [])
		self.assertEqual(len(self.headersOf(7)), 2)

	def test_savingIntoAColumnLayoutKeepsItsColumns(self):
		self.inBrowseMode()
		flowTableLayouts.remember(self.handle(), flowTableLayouts.TableLayout(columns=(2, 1)))
		flowTableLayouts.rememberHeaderSpeech(self.handle(), SPEAK_OFF)
		layout = flowTableLayouts.layoutFor(self.handle())
		self.assertEqual((layout.columns, layout.speakHeaders, layout.laysOut), ((2, 1), SPEAK_OFF, True))
		flowTableLayouts.rememberHeaderSpeech(self.handle(), FOLLOW)
		layout = flowTableLayouts.layoutFor(self.handle())
		self.assertEqual((layout.columns, layout.speakHeaders), ((2, 1), FOLLOW))

	def test_savingColumnsFromTheBandKeepsTheSpeechAndAsksForColumns(self):
		"""The band's layout says nothing about speech, so saving it must not undo what was saved."""
		self.inBrowseMode()
		flowTableLayouts.rememberHeaderSpeech(self.handle(), SPEAK_OFF)
		flowTableLayouts.remember(self.handle(), flowTableLayouts.TableLayout(columns=(1,)))
		layout = flowTableLayouts.layoutFor(self.handle())
		self.assertEqual((layout.columns, layout.speakHeaders, layout.laysOut), ((1,), SPEAK_OFF, True))
		self.assertEqual(len(flowTableLayouts.stored()), 1)

	def test_aChangedStoreIsReadAgain(self):
		self.inBrowseMode()
		flowTableLayouts.rememberHeaderSpeech(self.handle(), SPEAK_OFF)
		self.assertEqual(self.headersOf(7), [])
		flowTableLayouts.rememberHeaderSpeech(self.handle(), SPEAK_ROWS)
		self.assertEqual(self.headersOf(7), ["rowHeaderText"])

	def test_aTableTheCursorIsNotInIsNotLookedUp(self):
		"""Say all reads ahead of the cursor: the saved layout is only taken for the cursor's table."""
		self.inBrowseMode(tableID=7)
		flowTableLayouts.rememberHeaderSpeech(self.handle(), SPEAK_OFF)
		self.assertEqual(len(self.headersOf(8)), 2)

	def test_nothingIsLookedUpWhereNoLayoutDecidesSpeech(self):
		self.inBrowseMode()
		flowTableLayouts.remember(self.handle(), flowTableLayouts.TableLayout(columns=(1,)))
		with mock.patch.object(flowTableSource, "tableAt", side_effect=AssertionError("looked up")):
			self.assertEqual(len(self.headersOf(7)), 2)

	def test_aTableThatCannotBeNamedCannotBeSaved(self):
		self.inBrowseMode(url="")
		tableHeaderSpeech.toggle()
		with mock.patch.object(flowTableLayouts.flowTableIdentity, "whereOf", return_value=""):
			self.assertEqual(
				tableHeaderSpeech.save(),
				"This table cannot be recognised again, so its layout cannot be saved",
			)
		# The toggle is still in force.
		self.assertEqual(self.headersOf(7), [])

	def test_saveOutsideATable(self):
		document = self.inBrowseMode()
		document.inTable = False
		self.assertEqual(tableHeaderSpeech.save(), "Not in a table")

	def test_theRecordRoundTripsAndForgivesNonsense(self):
		layout = flowTableLayouts.TableLayout(speakHeaders=SPEAK_ROWS, speechOnly=True)
		self.assertEqual(flowTableLayouts.fromRecord(layout.asRecord()), layout)
		self.assertEqual(flowTableLayouts.TableLayout().asRecord(), {})
		read = flowTableLayouts.fromRecord({"speakHeaders": "loudly", "speechOnly": "yes"})
		self.assertEqual((read.speakHeaders, read.speechOnly), (FOLLOW, False))

	def test_storedAsItSays(self):
		self.inBrowseMode()
		flowTableLayouts.rememberHeaderSpeech(self.handle(), SPEAK_OFF)
		stored = json.loads(CONFIG["tableLayouts"])["layouts"][0]["layout"]
		self.assertEqual(stored, {"speakHeaders": "off", "speechOnly": True})


class TestTheManager(HeaderSpeechTestCase):
	def test_theManagerChangesIt(self):
		self.inBrowseMode()
		flowTableLayouts.rememberHeaderSpeech(self.handle(), SPEAK_OFF)
		manager = flowTableManager.LayoutManager(flowTableLayouts.stored())
		self.assertIn("no headers spoken", manager.label(0))
		self.assertIn("not laid out in columns", manager.label(0))
		self.assertIn("Spoken headers: no headers spoken", manager.details(0))
		manager.setSpeakHeaders(0, SPEAK_COLUMNS)
		self.assertTrue(manager.commit())
		self.assertEqual(self.headersOf(7), ["columnHeaderText"])

	def test_theChoicesAreEveryWay(self):
		manager = flowTableManager.LayoutManager([])
		self.assertEqual(
			[way for way, _label in manager.speakHeadersChoices()],
			[FOLLOW, SPEAK_OFF, SPEAK_ROWS, SPEAK_COLUMNS],
		)

	def test_nonsenseIsIgnored(self):
		self.inBrowseMode()
		flowTableLayouts.rememberHeaderSpeech(self.handle(), SPEAK_OFF)
		manager = flowTableManager.LayoutManager(flowTableLayouts.stored())
		manager.setSpeakHeaders(0, "loudly")
		manager.setSpeakHeaders(5, SPEAK_ROWS)
		self.assertFalse(manager.changed)


if __name__ == "__main__":
	unittest.main()
