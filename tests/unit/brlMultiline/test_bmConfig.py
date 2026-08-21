# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the configuration layer, and for meeting a display for the first time.

Every display is a display the add-on has never met until something writes settings for it,
and NVDA does not create a `__many__` subsection just because one was read: it raises
`KeyError`. That is the whole of what these tests are about, because it is a failure the
add-on hits at startup rather than in some corner, and it takes the plugin down with it.

The rest of the suite reads its settings through stand-ins for these functions, so the real
ones are reached here through L{realBmConfig}.
"""

import unittest

from ._stubs import CONFIG, FakeHandler, installStubs, realBmConfig, resetConfig

installStubs()

import braille  # noqa: E402
import config  # noqa: E402

from brlMultiline import bmConfig  # noqa: E402

FOCUS = "freedomScientific_1x80"
MONARCH = "humanware_8x32"


def displaysSection():
	return config.conf[bmConfig.CONFIG_SECTION]["displays"]


class ConfigTestCase(unittest.TestCase):
	def setUp(self):
		resetConfig()

	def tearDown(self):
		resetConfig()
		braille.handler = None


class TestDisplaySections(ConfigTestCase):
	def test_aDisplayNeverSeenBeforeIsCreated(self):
		"""The failure that took the plugin down at startup: reading is not enough."""
		self.assertFalse(displaysSection().isSet(FOCUS))
		section = bmConfig.getDisplayConfig(FOCUS)
		self.assertIsNotNone(section)
		self.assertTrue(displaysSection().isSet(FOCUS))

	def test_settingsFallBackToTheirDefaults(self):
		"""A section NVDA creates carries no spec, so its settings have no defaults yet."""
		section = bmConfig.getDisplayConfig(FOCUS)
		del CONFIG["segmentCount"]
		del CONFIG["segmentSizes"]
		self.assertEqual(section["segmentCount"], 1)
		self.assertEqual(section["segmentSizes"], [])

	def test_storedSettingsWin(self):
		CONFIG["segmentCount"] = 3
		self.assertEqual(bmConfig.getDisplayConfig(FOCUS)["segmentCount"], 3)

	def test_eachDisplayIsCreatedAsItIsMet(self):
		"""Swapping displays must not raise, whichever display is met first."""
		bmConfig.getDisplayConfig(FOCUS)
		bmConfig.getDisplayConfig(MONARCH)
		self.assertTrue(displaysSection().isSet(FOCUS))
		self.assertTrue(displaysSection().isSet(MONARCH))

	def test_theCurrentDisplayIsUsedWhenNoKeyIsGiven(self):
		braille.handler = FakeHandler(numRows=1, numCols=80)
		bmConfig.getDisplayConfig()
		self.assertTrue(displaysSection().isSet("stub_1x80"))


class TestDisplayKey(ConfigTestCase):
	def test_keyIsDriverNameAndGeometry(self):
		braille.handler = FakeHandler(numRows=8, numCols=32)
		self.assertEqual(realBmConfig["getDisplayKey"](), "stub_8x32")

	def test_geometryDistinguishesTheSameDriver(self):
		braille.handler = FakeHandler(numRows=1, numCols=80)
		self.assertEqual(realBmConfig["getDisplayKey"](), "stub_1x80")

	def test_noDisplayHasAKeyOfItsOwn(self):
		braille.handler = FakeHandler(numRows=1, numCols=40)
		braille.handler.display = None
		self.assertEqual(realBmConfig["getDisplayKey"](), "noBraille_1x40")


class TestSettingsForANewDisplay(ConfigTestCase):
	"""Each accessor funnels through `getDisplayConfig`, so each one met the same failure."""

	def test_layoutIsASegmentCount(self):
		CONFIG["segmentCount"] = 4
		self.assertEqual(realBmConfig["getLayout"](FOCUS), 4)

	def test_explicitSizesWinOverTheCount(self):
		CONFIG["segmentSizes"] = [20, 60]
		self.assertEqual(realBmConfig["getLayout"](FOCUS), [20, 60])

	def test_focusSegment(self):
		CONFIG["focusSegment"] = 2
		self.assertEqual(realBmConfig["getFocusSegment"](FOCUS), 2)

	def test_reverseScrollButtons(self):
		CONFIG["reverseScrollBtns"] = True
		self.assertTrue(realBmConfig["shouldReverseScrollButtons"](FOCUS))

	def test_showDocumentLines(self):
		CONFIG["showDocumentLines"] = True
		self.assertTrue(realBmConfig["shouldShowDocumentLines"](FOCUS))

	def test_segmentsAreEnabledUntilTurnedOff(self):
		"""Installing the add-on should not need a switch thrown before anything works."""
		del CONFIG["segmentsEnabled"]
		self.assertTrue(realBmConfig["areSegmentsEnabled"](FOCUS))

	def test_segmentsCanBeTurnedOff(self):
		CONFIG["segmentsEnabled"] = False
		self.assertFalse(realBmConfig["areSegmentsEnabled"](FOCUS))

	def test_setLayoutStoresAgainstADisplayNeverSeenBefore(self):
		realBmConfig["setLayout"](2, [20, 60], FOCUS)
		section = bmConfig.getDisplayConfig(FOCUS)
		self.assertEqual(section["segmentCount"], 2)
		self.assertEqual(section["segmentSizes"], [20, 60])

	def test_theSwitchStoresAgainstADisplayNeverSeenBefore(self):
		realBmConfig["setSegmentsEnabled"](False, FOCUS)
		self.assertFalse(bmConfig.getDisplayConfig(FOCUS)["segmentsEnabled"])

	def test_turningTheSwitchOffLeavesTheLayoutAlone(self):
		realBmConfig["setLayout"](2, [20, 60], FOCUS)
		realBmConfig["setSegmentsEnabled"](False, FOCUS)
		self.assertEqual(bmConfig.getDisplayConfig(FOCUS)["segmentSizes"], [20, 60])


class TestCarryingOverReversedPanning(ConfigTestCase):
	"""Reversed panning moved from the composite display to the displays behind it.

	The old value is no longer read anywhere, so an existing user's panning would silently swap
	back to the default — and panning the wrong way is the kind of thing a reader blames on
	themselves for a while before blaming the software.

	Tested against the real configuration layers rather than the stand-ins the rest of the suite
	reads through, because where the value lands is the whole question.
	"""

	COMPOSITE = "brlMultilineVirtual_9x80"
	MEMBERS = [MONARCH, FOCUS]

	def migrate(self):
		bmConfig.migrateReverseScrollButtons(self.COMPOSITE, self.MEMBERS)

	def reversed(self, displayKey):
		return bool(bmConfig.getDisplayConfig(displayKey)["reverseScrollBtns"])

	def test_theCompositesSettingIsCopiedToEveryDisplayBehindIt(self):
		"""Every one of them, which is what one setting for the whole composite meant."""
		bmConfig.getDisplayConfig(self.COMPOSITE)["reverseScrollBtns"] = True
		self.migrate()
		self.assertTrue(self.reversed(MONARCH))
		self.assertTrue(self.reversed(FOCUS))

	def test_aCompositeThatWasNotReversedChangesNothing(self):
		self.migrate()
		self.assertFalse(self.reversed(MONARCH))
		self.assertFalse(self.reversed(FOCUS))

	def test_itHappensOnceSoALaterChoiceIsNotUndone(self):
		"""The rebuild happens on every settings save, and this must not fight the user.

		There is no telling a member's stored `False` from a member that has never been asked,
		so running again would keep putting the composite's old answer back over the setting the
		user had just made.
		"""
		bmConfig.getDisplayConfig(self.COMPOSITE)["reverseScrollBtns"] = True
		self.migrate()
		bmConfig.getDisplayConfig(MONARCH)["reverseScrollBtns"] = False
		self.migrate()
		self.assertFalse(self.reversed(MONARCH))

	def test_itIsRecordedAgainstTheCompositeRatherThanInferred(self):
		self.migrate()
		self.assertTrue(bmConfig.getDisplayConfig(self.COMPOSITE)["reverseScrollBtnsMigrated"])

	def test_aNewCompositeIsNotAffectedByAnOldOnesMigration(self):
		"""The mark belongs to the arrangement it was made for, keyed by geometry as usual."""
		self.migrate()
		self.assertFalse(bmConfig.getDisplayConfig("brlMultilineVirtual_2x40")["reverseScrollBtnsMigrated"])

	def test_itIsCopiedByTheRealReader(self):
		"""Not only stored where it looks right: read back the way panning reads it."""
		bmConfig.getDisplayConfig(self.COMPOSITE)["reverseScrollBtns"] = True
		self.migrate()
		self.assertTrue(realBmConfig["shouldReverseScrollButtons"](FOCUS))

	def test_aDisplayWithASettingOfItsOwnKeepsIt(self):
		"""Including an explicit False, which is a choice and not an absence.

		`isSet` reports whether a value is stored rather than whether it differs from the
		default, so a display the user has answered for directly is never overwritten.
		"""
		bmConfig.getDisplayConfig(self.COMPOSITE)["reverseScrollBtns"] = True
		bmConfig.getDisplayConfig(MONARCH)["reverseScrollBtns"] = False
		self.migrate()
		self.assertFalse(self.reversed(MONARCH))
		self.assertTrue(self.reversed(FOCUS))

	def test_aBrokenConfigurationIsNotFatal(self):
		"""Panning the default way round is not worth losing a display over."""
		bmConfig.getDisplayConfig(self.COMPOSITE)["reverseScrollBtns"] = True
		bmConfig.migrateReverseScrollButtons(self.COMPOSITE, None)
		self.assertFalse(self.reversed(FOCUS))

	def test_aFailedAttemptIsTriedAgain(self):
		"""The mark is set last, so a run that fell over is not remembered as finished."""
		bmConfig.getDisplayConfig(self.COMPOSITE)["reverseScrollBtns"] = True
		bmConfig.migrateReverseScrollButtons(self.COMPOSITE, None)
		self.assertFalse(bmConfig.getDisplayConfig(self.COMPOSITE)["reverseScrollBtnsMigrated"])
		self.migrate()
		self.assertTrue(self.reversed(FOCUS))


class TestFlowSettings(ConfigTestCase):
	"""Turning spatial reading on, per display and per kind of content.

	Every one of these goes through `getDisplayConfig`, and so through `config.conf`, which
	is what makes them differ per configuration profile. That is the point of storing them
	this way rather than on the band: a profile triggered by one browser can read as a flow
	while the normal configuration goes on as before.
	"""

	def test_offUntilItIsAskedFor(self):
		"""Installing the add-on must not change how anything reads."""
		del CONFIG["flowEnabled"]
		self.assertFalse(bmConfig.isFlowEnabled(FOCUS))
		self.assertFalse(bmConfig.shouldClaimFlowBand(FOCUS))

	def test_turningItOnIsStoredAgainstTheDisplay(self):
		bmConfig.setFlowEnabled(True, FOCUS)
		self.assertTrue(bmConfig.getDisplayConfig(FOCUS)["flowEnabled"])
		self.assertTrue(bmConfig.isFlowEnabled(FOCUS))

	def test_browseModeFlowsOnceTheDisplayDoes(self):
		"""What the toggle command has always meant, now with somewhere to remember it."""
		bmConfig.setFlowEnabled(True, FOCUS)
		self.assertTrue(bmConfig.isFlowEnabledFor("browseMode", FOCUS))
		self.assertTrue(bmConfig.shouldClaimFlowBand(FOCUS))

	def test_aKindOfContentDoesNotFlowWhileTheDisplayIsOff(self):
		"""One switch turns the whole thing off without visiting each kind of content."""
		CONFIG["flowBrowseMode"] = True
		self.assertFalse(bmConfig.isFlowEnabledFor("browseMode", FOCUS))

	def test_editableTextIsOptIn(self):
		bmConfig.setFlowEnabled(True, FOCUS)
		self.assertFalse(bmConfig.isFlowEnabledFor("editableText", FOCUS))
		CONFIG["flowEditableText"] = True
		self.assertTrue(bmConfig.isFlowEnabledFor("editableText", FOCUS))

	def test_noBandIsClaimedWhenNothingIsSetToFlow(self):
		"""A band with nothing to show would take rows the layout wanted and never fill them."""
		bmConfig.setFlowEnabled(True, FOCUS)
		CONFIG["flowBrowseMode"] = False
		self.assertTrue(bmConfig.isFlowEnabled(FOCUS))
		self.assertFalse(bmConfig.shouldClaimFlowBand(FOCUS))

	def test_aKindOfContentNobodyHasHeardOfNeverFlows(self):
		bmConfig.setFlowEnabled(True, FOCUS)
		self.assertFalse(bmConfig.isFlowEnabledFor("interpretiveDance", FOCUS))

	def test_eachDisplayAnswersForItself(self):
		"""A Monarch can flow while a single line display does not: it has rows to spend."""
		bmConfig.getDisplayConfig(MONARCH)["flowEnabled"] = True
		bmConfig.getDisplayConfig(FOCUS)["flowEnabled"] = False
		self.assertTrue(bmConfig.isFlowEnabledFor("browseMode", MONARCH))
		self.assertFalse(bmConfig.isFlowEnabledFor("browseMode", FOCUS))

	def test_everyReadGoesBackToTheConfiguration(self):
		"""Nothing caches the answer, or a profile switch would not change it."""
		bmConfig.setFlowEnabled(True, FOCUS)
		self.assertTrue(bmConfig.isFlowEnabled(FOCUS))
		bmConfig.setFlowEnabled(False, FOCUS)
		self.assertFalse(bmConfig.isFlowEnabled(FOCUS))

	def test_theBandTakesTheWholeDisplayUntilItIsToldOtherwise(self):
		del CONFIG["flowRows"]
		del CONFIG["flowDisplay"]
		self.assertEqual(bmConfig.getFlowRows(FOCUS), 0)
		self.assertEqual(bmConfig.getFlowDisplay(FOCUS), "")

	def test_theBandCanBeGivenSomeOfTheRows(self):
		CONFIG["flowRows"] = 5
		CONFIG["flowDisplay"] = "hidBrailleStandard"
		self.assertEqual(bmConfig.getFlowRows(MONARCH), 5)
		self.assertEqual(bmConfig.getFlowDisplay(MONARCH), "hidBrailleStandard")

	def test_groundingOnAJumpIsOnUntilItIsTurnedOff(self):
		del CONFIG["flowGroundOnQuickNav"]
		self.assertTrue(bmConfig.shouldGroundOnQuickNav(FOCUS))
		CONFIG["flowGroundOnQuickNav"] = False
		self.assertFalse(bmConfig.shouldGroundOnQuickNav(FOCUS))

	def test_writingIsReadByParagraphUntilItIsTurnedOff(self):
		"""The default is the one editor's evidence; turning it off is how to test another."""
		del CONFIG["flowWriteByParagraph"]
		self.assertTrue(bmConfig.shouldWriteByParagraph(FOCUS))
		CONFIG["flowWriteByParagraph"] = False
		self.assertFalse(bmConfig.shouldWriteByParagraph(FOCUS))


if __name__ == "__main__":
	unittest.main()
