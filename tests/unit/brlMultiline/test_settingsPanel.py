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

from brlMultiline import bmConfig  # noqa: E402
from brlMultiline.devices import DeviceInfo  # noqa: E402
from brlMultiline.settingsPanel import (  # noqa: E402
	BrailleMultilineSettingsPanel,
	VirtualDisplaySettingsPanel,
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
		"reverseScrollBtns": False,
		"showDocumentLines": False,
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
		self.panel.onSave()
		self.assertEqual(self.sections[self.displayKey]["segmentCount"], 3)
		self.assertEqual(self.sections[self.displayKey]["focusSegment"], 1)

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
		self.panel.reverseScrollCtrl.SetValue(True)
		self.panel.onSave()
		self.assertEqual(self.sections[COMPOSITE_KEY]["focusSegment"], 2)
		self.assertTrue(self.sections[COMPOSITE_KEY]["reverseScrollBtns"])
		# Not written to the displays behind it, which have no focus of their own.
		self.assertEqual(self.sections[MONARCH_KEY]["focusSegment"], -1)
		self.assertFalse(self.sections[FOCUS_KEY]["reverseScrollBtns"])

	def test_aLayoutRefusedOnADisplayNotShownSelectsIt(self):
		"""The error is about the Focus, so the Focus is what the user is put back on."""
		self.show(1)
		self.panel.segmentSizesCtrl.SetValue("40, 50")
		self.show(0)
		self.assertFalse(self.panel.isValid())
		self.assertEqual(self.panel.targetIndex, 1)

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
