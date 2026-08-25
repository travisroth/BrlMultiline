# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the settings panels, without wx.

The panels are built out of wx controls, which these tests do not have. What they do have is
everything between the controls and the configuration: which displays can be edited, which
section each edit is written to, and what is refused. That is where the bugs would be.

The interesting case throughout is a display made of two displays. The panel then edits
several configuration sections at once while showing one at a time, and getting that wrong
would quietly write one display's layout over another's.
"""

import unittest

from ._stubs import installStubs, resetConfig

installStubs()

from brailleDisplayDrivers.brlMultilineVirtual import vdConfig  # noqa: E402
from brailleDisplayDrivers.brlMultilineVirtual.virtualLayout import DeviceSpec  # noqa: E402

from brlMultiline import bmConfig, flowIndent  # noqa: E402
from brlMultiline.devices import DeviceInfo  # noqa: E402
from brlMultiline.settingsPanel import (  # noqa: E402
	BrailleMultilineSettingsPanel,
	FlowSettingsPanel,
	VirtualDisplaySettingsPanel,
	indentStyleChoices,
	parseSegmentSizes,
)

MONARCH = DeviceInfo(driverName="hidBrailleStandard", rowStart=0, numRows=8, numCols=32)
FOCUS = DeviceInfo(driverName="freedomScientific", rowStart=8, numRows=1, numCols=80)
MONARCH_KEY = "hidBrailleStandard_8x32"
FOCUS_KEY = "freedomScientific_1x80"
COMPOSITE_KEY = "brlMultilineVirtual_9x80"


class FakeControl:
	"""A wx control reduced to the handful of calls the panels make on one."""

	def __init__(self, value=None):
		self.Value = value
		self.label = ""
		self.focused = False

	def IsChecked(self):
		return bool(self.Value)

	def SetValue(self, value):
		self.Value = value

	def GetSelection(self):
		return self.Value

	def SetSelection(self, index):
		self.Value = index

	def SetLabel(self, label):
		self.label = label

	def SetFocus(self):
		self.focused = True

	def Set(self, items):
		self.items = list(items)

	def Enable(self, enabled):
		self.enabled = enabled


def defaults():
	""":return: one display's settings as the configuration would answer them."""
	return {
		"segmentsEnabled": True,
		"segmentCount": 1,
		"segmentSizes": [],
		"focusSegment": -1,
		"messageSegment": -1,
		"reverseScrollBtns": False,
		"showDocumentLines": False,
		"flowEnabled": False,
		"flowBrowseMode": True,
		"flowObjects": False,
		"flowEditableText": False,
		"flowRows": 0,
		"flowDisplay": "",
		"flowGroundOnQuickNav": True,
	}


class SettingsPanelTestCase(unittest.TestCase):
	"""Gives each display a section of its own, which the shared stub configuration does not.

	`getDisplayConfig` is redirected rather than the stub being changed, because everything
	else is tested against one set of settings on purpose, and the point here is precisely
	that the panel must not confuse one display's section with another's.
	"""

	def setUp(self):
		resetConfig()
		self.sections = {}
		self.originalGetDisplayConfig = bmConfig.getDisplayConfig
		bmConfig.getDisplayConfig = self.section

	def tearDown(self):
		bmConfig.getDisplayConfig = self.originalGetDisplayConfig
		resetConfig()

	def section(self, displayKey=None):
		return self.sections.setdefault(displayKey, defaults())


class TestSegmentSizes(unittest.TestCase):
	def test_blankMeansDivideEvenly(self):
		self.assertEqual(parseSegmentSizes("   "), [])

	def test_commasOrSpaces(self):
		self.assertEqual(parseSegmentSizes("3, 3,2"), [3, 3, 2])
		self.assertEqual(parseSegmentSizes("3 3 2"), [3, 3, 2])

	def test_wordsAreRefused(self):
		with self.assertRaises(ValueError):
			parseSegmentSizes("three")

	def test_zeroIsRefused(self):
		with self.assertRaises(ValueError):
			parseSegmentSizes("3, 0")


class SegmentPanelTestCase(SettingsPanelTestCase):
	"""Builds the panel without wx, by hand, since only what is below the controls is tested."""

	devices: list[DeviceInfo] = []
	numRows = 8
	numCols = 32
	displayKey = "hidBrailleStandard_8x32"

	def setUp(self):
		super().setUp()
		self.panel = object.__new__(BrailleMultilineSettingsPanel)
		self.panel.displayKey = self.displayKey
		self.panel.numRows = self.numRows
		self.panel.numCols = self.numCols
		self.panel.devices = self.devices
		self.panel.targets = self.panel._divisionTargets()
		self.panel.pending = {
			target.displayKey: self.panel._readTarget(target.displayKey) for target in self.panel.targets
		}
		self.panel.targetIndex = 0
		self.panel.targetCtrl = FakeControl(0) if self.devices else None
		self.panel.segmentsEnabledCtrl = FakeControl(True)
		self.panel.segmentCountCtrl = FakeControl(1)
		self.panel.segmentSizesCtrl = FakeControl("")
		self.panel.sizesHintCtrl = FakeControl()
		self.panel.focusSegmentCtrl = FakeControl(-1)
		self.panel.messageSegmentCtrl = FakeControl(-1)
		self.panel.reverseScrollCtrl = FakeControl(False)
		self.panel.documentLinesCtrl = FakeControl(False)
		self.panel._showTarget(0)

	def show(self, index):
		"""Switch to another display, as choosing it in the combo box does."""
		self.panel.targetCtrl.SetSelection(index)
		self.panel._onTargetChanged(None)


class TestOrdinaryDisplay(SegmentPanelTestCase):
	def test_thereIsOneDisplayToEdit(self):
		self.assertEqual([target.displayKey for target in self.panel.targets], [self.displayKey])

	def test_thereIsNoChooser(self):
		self.assertIsNone(self.panel.targetCtrl)

	def test_savingWritesTheOneSection(self):
		self.panel.segmentCountCtrl.SetValue(3)
		self.panel.focusSegmentCtrl.SetValue(1)
		self.panel.reverseScrollCtrl.SetValue(True)
		self.panel.onSave()
		self.assertEqual(self.sections[self.displayKey]["segmentCount"], 3)
		self.assertEqual(self.sections[self.displayKey]["focusSegment"], 1)
		# One display, so per display and per arrangement are the same section: unchanged
		# from before the panning direction moved into the per display group.
		self.assertTrue(self.sections[self.displayKey]["reverseScrollBtns"])

	def test_aLayoutThatDoesNotFitIsRefused(self):
		self.panel.segmentSizesCtrl.SetValue("4, 5")
		self.assertFalse(self.panel.isValid())
		self.assertTrue(self.panel.segmentSizesCtrl.focused)

	def test_aLayoutThatDoesNotFitIsAllowedWhenDivisionIsOff(self):
		"""Turning division off is what a user reaches for when the display got smaller."""
		self.panel.segmentsEnabledCtrl.SetValue(False)
		self.panel.segmentSizesCtrl.SetValue("4, 5")
		self.assertTrue(self.panel.isValid())

	def test_sizesThatAreNotNumbersAreRefusedEvenThen(self):
		"""Nothing may put text into the configuration that cannot be read back."""
		self.panel.segmentsEnabledCtrl.SetValue(False)
		self.panel.segmentSizesCtrl.SetValue("half")
		self.assertFalse(self.panel.isValid())


class TestCompositeDisplay(SegmentPanelTestCase):
	devices = [MONARCH, FOCUS]
	numRows = 9
	numCols = 80
	displayKey = COMPOSITE_KEY

	def test_eachDisplayBehindItCanBeEdited(self):
		self.assertEqual(
			[target.displayKey for target in self.panel.targets],
			[MONARCH_KEY, FOCUS_KEY],
		)

	def test_theCompositeItselfIsNotOneOfThem(self):
		"""It has no segment count of its own; it is always divided at the displays."""
		self.assertNotIn(COMPOSITE_KEY, [target.displayKey for target in self.panel.targets])

	def test_eachIsMeasuredInItsOwnUnits(self):
		self.assertIn("rows", self.panel.sizesHintCtrl.label)
		self.show(1)
		self.assertIn("cells", self.panel.sizesHintCtrl.label)

	def test_editsToOneDisplaySurviveSwitchingToTheOther(self):
		self.panel.segmentCountCtrl.SetValue(4)
		self.show(1)
		self.show(0)
		self.assertEqual(self.panel.segmentCountCtrl.Value, 4)

	def test_savingWritesEachDisplaysOwnSection(self):
		self.panel.segmentCountCtrl.SetValue(4)
		self.show(1)
		self.panel.segmentCountCtrl.SetValue(2)
		self.panel.onSave()
		self.assertEqual(self.sections[MONARCH_KEY]["segmentCount"], 4)
		self.assertEqual(self.sections[FOCUS_KEY]["segmentCount"], 2)

	def test_theFocusSegmentBelongsToTheDisplayAsAWhole(self):
		"""There is one focus, whichever of the displays it happens to be sitting on."""
		self.panel.focusSegmentCtrl.SetValue(2)
		self.panel.messageSegmentCtrl.SetValue(1)
		self.panel.onSave()
		self.assertEqual(self.sections[COMPOSITE_KEY]["focusSegment"], 2)
		self.assertEqual(self.sections[COMPOSITE_KEY]["messageSegment"], 1)
		# Not written to the displays behind it, which have no focus of their own.
		self.assertEqual(self.sections[MONARCH_KEY]["focusSegment"], -1)

	def test_panningDirectionIsPerDisplay(self):
		"""The panning keys are on one piece of hardware and sit differently on each."""
		self.panel.reverseScrollCtrl.SetValue(True)
		self.show(1)
		self.panel.reverseScrollCtrl.SetValue(False)
		self.panel.onSave()
		self.assertTrue(self.sections[MONARCH_KEY]["reverseScrollBtns"])
		self.assertFalse(self.sections[FOCUS_KEY]["reverseScrollBtns"])

	def test_panningDirectionFollowsTheChooser(self):
		"""The bug this fixes: the checkbox sat under the chooser and ignored it."""
		self.panel.reverseScrollCtrl.SetValue(True)
		self.show(1)
		self.assertFalse(self.panel.reverseScrollCtrl.IsChecked())
		self.show(0)
		self.assertTrue(self.panel.reverseScrollCtrl.IsChecked())

	def test_panningDirectionIsNotWrittenToTheComposite(self):
		self.panel.reverseScrollCtrl.SetValue(True)
		self.panel.onSave()
		self.assertFalse(self.sections[COMPOSITE_KEY]["reverseScrollBtns"])

	def test_aLayoutRefusedOnADisplayNotShownSelectsIt(self):
		"""The error is about the Focus, so the Focus is what the user is put back on."""
		self.show(1)
		self.panel.segmentSizesCtrl.SetValue("40, 50")
		self.show(0)
		self.assertFalse(self.panel.isValid())
		self.assertEqual(self.panel.targetIndex, 1)

	def test_theFocusSegmentIsCheckedEvenWhenNoDisplayIsDivided(self):
		"""A composite still has one segment per display: the boundaries are not optional."""
		self.panel.segmentsEnabledCtrl.SetValue(False)
		self.show(1)
		self.panel.segmentsEnabledCtrl.SetValue(False)
		self.panel.focusSegmentCtrl.SetValue(7)
		self.assertFalse(self.panel.isValid())
		self.panel.focusSegmentCtrl.SetValue(1)
		self.assertTrue(self.panel.isValid())

	def test_theFocusSegmentIsCountedAcrossEveryDisplay(self):
		self.panel.segmentCountCtrl.SetValue(4)
		self.show(1)
		self.panel.segmentCountCtrl.SetValue(2)
		self.panel.focusSegmentCtrl.SetValue(5)
		self.assertTrue(self.panel.isValid())
		self.panel.focusSegmentCtrl.SetValue(6)
		self.assertFalse(self.panel.isValid())


class TestDisplayList(SettingsPanelTestCase):
	"""The panel that chooses which displays are combined."""

	def setUp(self):
		super().setUp()
		self.panel = object.__new__(VirtualDisplaySettingsPanel)
		self.panel.descriptions = {
			"noBraille": "No braille",
			"freedomScientific": "Freedom Scientific Focus",
			"hidBrailleStandard": "Standard HID braille display",
			"brlMultilineVirtual": "BrlMultiline: several displays as one",
		}
		self.panel.storedSpecs = {}
		self.panel.chosen = []
		self.panel._original = []
		self.panel.chosenCtrl = FakeControl(-1)
		self.panel.availableCtrl = FakeControl(0)
		self.panel.moveUpButton = FakeControl()
		self.panel.moveDownButton = FakeControl()
		self.panel.removeButton = FakeControl()
		self.panel.addButton = FakeControl()
		self.panel._refresh()

	def test_noBrailleCannotBeCombined(self):
		"""It is the absence of a display rather than one."""
		self.assertNotIn("noBraille", self.panel._addable())

	def listed(self, states=None, chosen=("hidBrailleStandard", "freedomScientific")):
		"""Put a list into the panel with a given set of live states, and read it back."""
		self.panel._states = states
		self.panel.chosen = list(chosen)
		self.panel._refresh(select=0)
		return self.panel.chosenCtrl.items

	def test_withNoCompositeRunningTheNamesAreGivenPlainly(self):
		"""Nothing else knows which of these displays were opened, so nothing is claimed."""
		self.assertEqual(
			self.listed(states=None),
			["Standard HID braille display", "Freedom Scientific Focus"],
		)

	def test_aDisplayBeingDrivenSaysSo(self):
		listed = self.listed(states={"hidBrailleStandard": True, "freedomScientific": True})
		self.assertEqual(listed[0], "Standard HID braille display: in use")

	def test_aDisplayTheCompositeNeverOpenedSaysSo(self):
		"""Switched off, or out of range, when braille started."""
		listed = self.listed(states={"freedomScientific": True})
		self.assertEqual(listed[0], "Standard HID braille display: not connected")

	def test_aDisplayThatHasStoppedRespondingSaysSo(self):
		listed = self.listed(states={"hidBrailleStandard": False, "freedomScientific": True})
		self.assertEqual(listed[0], "Standard HID braille display: not responding")

	def test_theDisplaysThatMayBeAddedAreNamedPlainly(self):
		"""They are not in the composite, so there is no state to report for them."""
		self.listed(states={"hidBrailleStandard": True, "freedomScientific": True})
		for name in self.panel.availableCtrl.items:
			self.assertNotIn(":", name)

	def test_theCombinedDisplayCannotContainItself(self):
		self.assertNotIn("brlMultilineVirtual", self.panel._addable())

	def test_addingTakesADisplayOutOfTheChoices(self):
		self.panel.availableCtrl.SetSelection(self.panel._available.index("hidBrailleStandard"))
		self.panel._onAdd(None)
		self.assertEqual(self.panel.chosen, ["hidBrailleStandard"])
		self.assertNotIn("hidBrailleStandard", self.panel._addable())

	def test_displaysAreNamedAsNVDANamesThem(self):
		self.assertEqual(self.panel._describe("freedomScientific"), "Freedom Scientific Focus")

	def test_anUnknownDriverIsNamedByItsDriverName(self):
		self.assertEqual(self.panel._describe("somethingElse"), "somethingElse")

	def test_movingChangesTheOrderAndKeepsTheSelection(self):
		self.panel.chosen = ["hidBrailleStandard", "freedomScientific"]
		self.panel._refresh(select=1)
		self.panel._onMoveUp(None)
		self.assertEqual(self.panel.chosen, ["freedomScientific", "hidBrailleStandard"])
		self.assertEqual(self.panel.chosenCtrl.GetSelection(), 0)

	def test_theTopDisplayCannotMoveUp(self):
		self.panel.chosen = ["hidBrailleStandard", "freedomScientific"]
		self.panel._refresh(select=0)
		self.assertFalse(self.panel.moveUpButton.enabled)
		self.assertTrue(self.panel.moveDownButton.enabled)

	def test_removingLeavesTheRest(self):
		self.panel.chosen = ["hidBrailleStandard", "freedomScientific"]
		self.panel._refresh(select=0)
		self.panel._onRemove(None)
		self.assertEqual(self.panel.chosen, ["freedomScientific"])

	def test_nothingCanBeRemovedFromAnEmptyList(self):
		self.assertFalse(self.panel.removeButton.enabled)
		self.panel._onRemove(None)
		self.assertEqual(self.panel.chosen, [])


class TestExplicitPorts(SettingsPanelTestCase):
	"""A port can only be set from the console, so this panel must not quietly drop one.

	Read through the real `vdConfig`, so that what the panel loads is what the driver stores.
	"""

	def setUp(self):
		super().setUp()
		vdConfig.setDevices(
			[DeviceSpec("hidBrailleStandard"), DeviceSpec("freedomScientific", "COM4")],
		)
		self.panel = object.__new__(VirtualDisplaySettingsPanel)
		self.panel.descriptions = {
			"freedomScientific": "Freedom Scientific Focus",
			"hidBrailleStandard": "Standard HID braille display",
		}
		self.panel.storedSpecs = self.panel._readDevices()
		self.panel.chosen = list(self.panel.storedSpecs)
		self.panel._original = list(self.panel.chosen)
		self.panel.chosenCtrl = FakeControl(-1)
		self.panel.availableCtrl = FakeControl(0)
		self.panel.moveUpButton = FakeControl()
		self.panel.moveDownButton = FakeControl()
		self.panel.removeButton = FakeControl()
		self.panel.addButton = FakeControl()
		self.panel._refresh()

	def test_openingAndSavingKeepsAPort(self):
		"""Pressing OK with nothing changed must change nothing."""
		self.assertEqual(
			[(spec.driverName, spec.port) for spec in self.panel.specsToStore()],
			[("hidBrailleStandard", "auto"), ("freedomScientific", "COM4")],
		)

	def test_reorderingKeepsAPort(self):
		self.panel._refresh(select=1)
		self.panel._onMoveUp(None)
		self.assertEqual(
			[(spec.driverName, spec.port) for spec in self.panel.specsToStore()],
			[("freedomScientific", "COM4"), ("hidBrailleStandard", "auto")],
		)

	def test_aNewlyAddedDisplayIsDetectedAfresh(self):
		self.panel.storedSpecs = {}
		self.panel.chosen = ["hidBrailleStandard"]
		self.assertEqual([spec.port for spec in self.panel.specsToStore()], ["auto"])

	def test_savingAndReadingBackAgreeWithTheDriver(self):
		"""The round trip, so that the panel and the driver cannot drift apart."""
		self.panel.onSave()
		self.assertEqual(
			[(spec.driverName, spec.port) for spec in vdConfig.getDevices()],
			[("hidBrailleStandard", "auto"), ("freedomScientific", "COM4")],
		)

	def test_aRemovedDisplayIsGone(self):
		self.panel._refresh(select=0)
		self.panel._onRemove(None)
		self.panel.onSave()
		self.assertEqual([spec.driverName for spec in vdConfig.getDevices()], ["freedomScientific"])


class FlowPanelTestCase(SettingsPanelTestCase):
	"""Builds the flow panel without wx, as the segment panel tests build theirs."""

	devices: list[DeviceInfo] = []
	numRows = 8
	numCols = 32
	displayKey = MONARCH_KEY

	def setUp(self):
		super().setUp()
		import braille

		from ._stubs import FakeHandler

		self.addCleanup(setattr, braille, "handler", braille.handler)
		braille.handler = FakeHandler(self.numRows, self.numCols)
		self.panel = object.__new__(FlowSettingsPanel)
		self.panel.displayKey = self.displayKey
		self.panel.devices = self.devices
		self.panel.targets = self.panel._bandTargets()
		self.panel.enabledCtrl = FakeControl(False)
		self.panel.modeCtrls = {mode: FakeControl(True) for mode in bmConfig.FLOW_MODES}
		self.panel.bandDisplayCtrl = FakeControl(0) if len(self.panel.targets) > 1 else None
		self.panel.rowsCtrl = FakeControl(0)
		self.panel.rowsHintCtrl = FakeControl()
		self.panel.groundCtrl = FakeControl(True)
		self.panel.writeByParagraphCtrl = FakeControl(True)
		self.panel.indentStyleCtrl = FakeControl(0)
		self.panel.lineFocusCtrl = FakeControl(True)
		self.panel.tableRowsCtrl = FakeControl(1)
		self.panel.truncateCtrl = FakeControl(False)
		self.panel.pinKeyCtrl = FakeControl(True)

	def section(self, displayKey=None):
		return self.sections.setdefault(displayKey, defaults())


class TestFlowOnOneDisplay(FlowPanelTestCase):
	def test_thereIsNothingToChoose(self):
		"""One display, so the band goes on it and the chooser is left out of the dialog."""
		self.assertEqual(len(self.panel.targets), 1)
		self.assertIsNone(self.panel.bandDisplayCtrl)

	def test_turningItOnIsSaved(self):
		self.panel.enabledCtrl.SetValue(True)
		self.panel.rowsCtrl.SetValue(4)
		self.panel.onSave()
		self.assertTrue(self.sections[MONARCH_KEY]["flowEnabled"])
		self.assertEqual(self.sections[MONARCH_KEY]["flowRows"], 4)

	def test_eachKindOfContentIsSavedSeparately(self):
		self.panel.enabledCtrl.SetValue(True)
		self.panel.modeCtrls["browseMode"].SetValue(False)
		self.panel.modeCtrls["objects"].SetValue(False)
		self.panel.modeCtrls["editableText"].SetValue(True)
		self.panel.onSave()
		self.assertTrue(self.sections[MONARCH_KEY]["flowEnabled"])
		self.assertFalse(self.sections[MONARCH_KEY]["flowBrowseMode"])
		self.assertFalse(self.sections[MONARCH_KEY]["flowObjects"])
		self.assertTrue(self.sections[MONARCH_KEY]["flowEditableText"])

	def test_groundingIsSaved(self):
		self.panel.groundCtrl.SetValue(False)
		self.panel.onSave()
		self.assertFalse(self.sections[MONARCH_KEY]["flowGroundOnQuickNav"])

	def test_theIndentStyleIsSaved(self):
		choices = indentStyleChoices()
		self.panel.indentStyleCtrl.SetSelection(len(choices) - 1)
		self.panel.onSave()
		self.assertEqual(self.sections[MONARCH_KEY]["flowIndentStyle"], choices[-1][0])

	def test_everyStyleTheModuleHasIsOffered(self):
		"""The dialog is built from `flowIndent.INDENT_STYLES`, so a style added there cannot
		become one the reader has no way to choose."""
		self.assertEqual(
			[style for style, _label in indentStyleChoices()],
			list(flowIndent.INDENT_STYLES),
		)

	def test_everyStyleHasALabelOfItsOwn(self):
		labels = [label for _style, label in indentStyleChoices()]
		self.assertEqual(len(set(labels)), len(labels))

	def test_aStyleThisVersionHasNotGotReadsAsTheDefault(self):
		"""The dialog must agree with `bmConfig.flowIndentStyle`, or opening the settings and
		saving them without touching this control would quietly change it."""
		self.assertEqual(self.panel._indentStyleIndex("engraved"), 0)
		self.assertEqual(indentStyleChoices()[0][0], flowIndent.DEFAULT_STYLE)

	def test_theLineFocusMarkIsSaved(self):
		self.panel.lineFocusCtrl.SetValue(False)
		self.panel.onSave()
		self.assertFalse(self.sections[MONARCH_KEY]["flowLineFocus"])

	def test_theTableRowHeightIsSaved(self):
		self.panel.tableRowsCtrl.SetValue(2)
		self.panel.onSave()
		self.assertEqual(self.sections[MONARCH_KEY]["flowTableRowHeight"], 2)

	def test_cuttingLongTableCellsIsSaved(self):
		self.panel.truncateCtrl.SetValue(True)
		self.panel.onSave()
		self.assertTrue(self.sections[MONARCH_KEY]["flowTableTruncate"])

	def test_readingWritingByParagraphIsSaved(self):
		self.panel.writeByParagraphCtrl.SetValue(False)
		self.panel.onSave()
		self.assertFalse(self.sections[MONARCH_KEY]["flowWriteByParagraph"])

	def test_moreRowsThanTheDisplayHasIsRefused(self):
		self.panel.enabledCtrl.SetValue(True)
		self.panel.rowsCtrl.SetValue(9)
		self.assertFalse(self.panel.isValid())
		self.assertTrue(self.panel.rowsCtrl.focused)

	def test_allOfTheRowsIsWhatZeroMeans(self):
		self.panel.enabledCtrl.SetValue(True)
		self.panel.rowsCtrl.SetValue(0)
		self.assertTrue(self.panel.isValid())

	def test_aBandThatNoLongerFitsIsAllowedWhenTheFlowIsOff(self):
		"""Turning the flow off is what a reader reaches for when the display got smaller."""
		self.panel.rowsCtrl.SetValue(20)
		self.assertTrue(self.panel.isValid())

	def test_theHintSaysHowManyRowsThereAre(self):
		self.panel._updateRowsHint()
		self.assertIn("8", self.panel.rowsHintCtrl.label)


class TestFlowOnACompositeDisplay(FlowPanelTestCase):
	devices = [MONARCH, FOCUS]
	numRows = 9
	numCols = 80
	displayKey = COMPOSITE_KEY

	def test_thereIsAnAutomaticChoiceAndOnePerDisplay(self):
		self.assertEqual(
			[target.driverName for target in self.panel.targets],
			["", MONARCH.driverName, FOCUS.driverName],
		)

	def test_theAutomaticChoiceIsTheTallest(self):
		"""Rows are what a flow spends, so eight rows of thirty-two beat one of eighty."""
		self.assertEqual(self.panel.targets[0].numRows, 8)
		self.assertIn(MONARCH.driverName, self.panel.targets[0].label)

	def test_namingADisplayIsSaved(self):
		self.panel.enabledCtrl.SetValue(True)
		self.panel.bandDisplayCtrl.SetSelection(2)
		self.panel.onSave()
		self.assertEqual(self.sections[COMPOSITE_KEY]["flowDisplay"], FOCUS.driverName)

	def test_theStoredDisplayIsTheOneShown(self):
		self.assertEqual(self.panel._targetIndex(FOCUS.driverName), 2)

	def test_aStoredDisplayThatIsNotHereShowsAsAutomatic(self):
		"""Which is what the band will do until that display comes back."""
		self.assertEqual(self.panel._targetIndex("brailliant"), 0)

	def test_theRowsAreBoundedByTheChosenDisplayNotTheWholeThing(self):
		# The composite is nine rows tall, but a band may not straddle two displays: chosen
		# on the Focus 80 it has one row to spend, not nine.
		self.panel.enabledCtrl.SetValue(True)
		self.panel.bandDisplayCtrl.SetSelection(2)
		self.panel.rowsCtrl.SetValue(4)
		self.assertFalse(self.panel.isValid())

	def test_theSameRowsFitOnTheTallerDisplay(self):
		self.panel.enabledCtrl.SetValue(True)
		self.panel.bandDisplayCtrl.SetSelection(1)
		self.panel.rowsCtrl.SetValue(4)
		self.assertTrue(self.panel.isValid())

	def test_theHintFollowsTheChosenDisplay(self):
		self.panel.bandDisplayCtrl.SetSelection(2)
		self.panel._onBandDisplayChanged(None)
		self.assertIn("one row", self.panel.rowsHintCtrl.label)
