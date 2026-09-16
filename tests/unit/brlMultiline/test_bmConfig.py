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

from ._stubs import (
	CONFIG,
	FORMAT_CONFIG,
	FakeHandler,
	ReportTableHeaders,
	installStubs,
	realBmConfig,
	resetConfig,
)

installStubs()

import braille  # noqa: E402
import config  # noqa: E402

from brlMultiline import bmConfig, flowIndent  # noqa: E402

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

	def test_newContentScrollsIntoViewUntilItIsTurnedOff(self):
		"""On by default: a pin is a thing somebody asked to watch, and a monitor showing what
		stopped arriving twenty messages ago is worse than one that moves."""
		del CONFIG["flowScrollToNewContent"]
		self.assertTrue(bmConfig.shouldScrollToNewContent(FOCUS))
		CONFIG["flowScrollToNewContent"] = False
		self.assertFalse(bmConfig.shouldScrollToNewContent(FOCUS))

	def test_theIndentStyleDefaultsToTwoSpaces(self):
		del CONFIG["flowIndentStyle"]
		self.assertEqual(bmConfig.flowIndentStyle(FOCUS), flowIndent.TWO_SPACES)

	def test_theIndentStyleIsRead(self):
		CONFIG["flowIndentStyle"] = flowIndent.DOTS_78
		self.assertEqual(bmConfig.flowIndentStyle(FOCUS), flowIndent.DOTS_78)

	def test_anIndentStyleThisVersionHasNotGotReadsAsTheDefault(self):
		"""Written by a later version, or by hand. A setting nobody recognises must not stop
		a display being drawn."""
		CONFIG["flowIndentStyle"] = "engraved"
		self.assertEqual(bmConfig.flowIndentStyle(FOCUS), flowIndent.DEFAULT_STYLE)


if __name__ == "__main__":
	unittest.main()


class TestWhatNvdaWasToldAboutTables(unittest.TestCase):
	"""NVDA's Document Formatting settings are not only about browse mode: an application
	module reads them for its own objects too, which is how turning table reporting off in
	Outlook stops "From" and "Received" being announced before every message in the list.
	They are the reader's answer to a question this add-on asks again in a different shape,
	so where the question is the same the answer is taken rather than asked for twice."""

	def setUp(self):
		resetConfig()
		self.addCleanup(resetConfig)

	def test_oneSettingHasTwoAxesAndThisAddOnHasOneFeatureForEach(self):
		"""`reportTableHeaders` says rows, columns, both or neither. The pinned header row is
		the column axis and the repeated key column is the row axis, which is what a row
		header is once a table is laid out spatially."""
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.ROWS.value
		self.assertTrue(bmConfig.wantsRowHeaders())
		self.assertFalse(bmConfig.wantsColumnHeaders())
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.COLUMNS.value
		self.assertFalse(bmConfig.wantsRowHeaders())
		self.assertTrue(bmConfig.wantsColumnHeaders())

	def test_bothAndNeither(self):
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.ROWS_AND_COLUMNS.value
		self.assertTrue(bmConfig.wantsRowHeaders())
		self.assertTrue(bmConfig.wantsColumnHeaders())
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.OFF.value
		self.assertFalse(bmConfig.wantsRowHeaders())
		self.assertFalse(bmConfig.wantsColumnHeaders())

	def test_theOtherThreeAreRead(self):
		FORMAT_CONFIG["reportTables"] = False
		FORMAT_CONFIG["reportTableCellCoords"] = False
		FORMAT_CONFIG["includeLayoutTables"] = True
		self.assertFalse(bmConfig.wantsTables())
		self.assertFalse(bmConfig.wantsCellCoordinates())
		self.assertTrue(bmConfig.wantsLayoutTables())

	def test_aSettingThatCannotBeReadTakesNvdasOwnDefault(self):
		"""An NVDA that has not got the setting, or a configuration part way through being
		replaced. Answering "no headers" there would silently take away a feature."""
		del FORMAT_CONFIG["reportTableHeaders"]
		self.assertTrue(bmConfig.wantsColumnHeaders())
		self.assertTrue(bmConfig.wantsRowHeaders())

	def test_itIsReadOnEveryCallRatherThanRemembered(self):
		"""NVDA has commands that cycle these while the reader is in the table — see
		`globalCommands.script_toggleReportTableHeaders` — so a value read once at start-up
		would be the wrong one by the time it mattered."""
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.OFF.value
		self.assertFalse(bmConfig.wantsColumnHeaders())
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.COLUMNS.value
		self.assertTrue(bmConfig.wantsColumnHeaders())


class TestFollowingOrOverridingNvda(unittest.TestCase):
	"""Three states, and the third is the default. The other two exist because braille has
	room speech does not: what a reader turns off for speech is usually the *repetition*, and
	a header drawn once at the top of the display costs none of that."""

	def setUp(self):
		import braille

		resetConfig()
		self.addCleanup(resetConfig)
		self.addCleanup(setattr, braille, "handler", braille.handler)
		# These settings are stored per display, so there has to be one to read them for.
		braille.handler = FakeHandler(numRows=8, numCols=32)

	def test_followingIsTheDefault(self):
		self.assertEqual(CONFIG["flowTableHeadersMode"], bmConfig.FOLLOW_NVDA)
		self.assertEqual(CONFIG["flowTablePinKeyMode"], bmConfig.FOLLOW_NVDA)

	def test_followingTakesNvdasAnswer(self):
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.OFF.value
		self.assertFalse(bmConfig.shouldPinTableHeaders())
		self.assertFalse(bmConfig.shouldPinKeyColumn())
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.ROWS_AND_COLUMNS.value
		self.assertTrue(bmConfig.shouldPinTableHeaders())
		self.assertTrue(bmConfig.shouldPinKeyColumn())

	def test_alwaysAndNeverDoNotAsk(self):
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.OFF.value
		CONFIG["flowTableHeadersMode"] = bmConfig.ALWAYS
		CONFIG["flowTablePinKeyMode"] = bmConfig.ALWAYS
		self.assertTrue(bmConfig.shouldPinTableHeaders())
		self.assertTrue(bmConfig.shouldPinKeyColumn())
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.ROWS_AND_COLUMNS.value
		CONFIG["flowTableHeadersMode"] = bmConfig.NEVER
		CONFIG["flowTablePinKeyMode"] = bmConfig.NEVER
		self.assertFalse(bmConfig.shouldPinTableHeaders())
		self.assertFalse(bmConfig.shouldPinKeyColumn())

	def test_theTwoFollowDifferentHalvesOfTheSameSetting(self):
		"""The whole point of the mapping: a reader who wants to know which row they are on
		but not what each column is has said so, and gets the key column without the header
		row."""
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.ROWS.value
		self.assertTrue(bmConfig.shouldPinKeyColumn())
		self.assertFalse(bmConfig.shouldPinTableHeaders())


class TestASettingThatUsedToBeACheckbox(unittest.TestCase):
	"""Both header settings were `boolean(default=True)` before they learned to defer to NVDA.
	A stored `False` is not a value the new specification allows, and `configobj` replaces a
	value that fails validation with the default before any code of this add-on is reached —
	so a reader who had turned the header row off would have been given it back, and told
	nothing about it, because the new default is to follow NVDA and NVDA reports table headers
	by default.

	The section is written to directly rather than through L{CONFIG}, because what is being
	tested is `isSet`: upstream it reports whether the key is stored in any profile, which is
	how an answer somebody gave is told from a default nobody ever saw.
	"""

	def setUp(self):
		import braille

		resetConfig()
		self.addCleanup(resetConfig)
		self.addCleanup(setattr, braille, "handler", braille.handler)
		# These settings are stored per display, so there has to be one to read them for.
		braille.handler = FakeHandler(numRows=8, numCols=32)
		self.section = bmConfig.getDisplayConfig()

	def test_aClearedCheckboxStaysCleared(self):
		self.section["flowTableHeaders"] = False
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.ROWS_AND_COLUMNS.value
		self.assertFalse(bmConfig.shouldPinTableHeaders())

	def test_aTickedCheckboxStaysTicked(self):
		self.section["flowTableHeaders"] = True
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.OFF.value
		self.assertTrue(bmConfig.shouldPinTableHeaders())

	def test_theSameForTheRepeatedColumn(self):
		self.section["flowTablePinKey"] = False
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.ROWS_AND_COLUMNS.value
		self.assertFalse(bmConfig.shouldPinKeyColumn())

	def test_answeringTheNewSettingOverridesTheOldOne(self):
		"""Once the reader has answered the question in the shape it is asked now, the checkbox
		they ticked under an older version has been superseded rather than contradicted."""
		self.section["flowTableHeaders"] = False
		self.section["flowTableHeadersMode"] = bmConfig.ALWAYS
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.OFF.value
		self.assertTrue(bmConfig.shouldPinTableHeaders())

	def test_aCheckboxNobodyTouchedFollowsNvda(self):
		"""Which is the new default, and the whole reason it is the new default: a reader who
		has told NVDA what they want from a table has said it once already."""
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.OFF.value
		self.assertFalse(bmConfig.shouldPinTableHeaders())
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.ROWS_AND_COLUMNS.value
		self.assertTrue(bmConfig.shouldPinTableHeaders())

	def test_nothingIsWrittenBack(self):
		"""An answer given in a profile triggered by one application is an answer about that
		application. Reading through the aggregated section asks the question in whichever
		profile is active, which is where the answer was given; rewriting it would have to
		choose a profile, and any choice would be wrong for somebody."""
		self.section["flowTableHeaders"] = False
		bmConfig.shouldPinTableHeaders()
		self.assertFalse(self.section.isSet("flowTableHeadersMode"))

	def stack(self, base, application):
		"""Two profiles in force: the base configuration and one an application triggered."""
		self.section.profiles = [base, application]

	def test_theApplicationProfileAnswersEvenInTheOlderForm(self):
		"""One question stored under two keys, which is what made this go wrong.

		`isSet` reports whether a key is stored in *any* active profile, so a new answer in
		the base configuration looked like an answer everywhere — including inside an
		application profile whose whole purpose is that its answer wins there.
		"""
		self.stack({"flowTableHeadersMode": bmConfig.ALWAYS}, {"flowTableHeaders": False})
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.ROWS_AND_COLUMNS.value
		self.assertFalse(bmConfig.shouldPinTableHeaders())

	def test_andTheOtherWayRound(self):
		"""The application profile answered in the new form, so the base checkbox is history."""
		self.stack({"flowTableHeaders": False}, {"flowTableHeadersMode": bmConfig.ALWAYS})
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.OFF.value
		self.assertTrue(bmConfig.shouldPinTableHeaders())

	def test_aProfileThatAnsweredNeitherDefersToTheOneBelow(self):
		"""Most profiles say nothing about most settings, which is why they can be stacked."""
		self.stack({"flowTableHeaders": False}, {"segmentCount": 2})
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.ROWS_AND_COLUMNS.value
		self.assertFalse(bmConfig.shouldPinTableHeaders())

	def test_withinOneProfileTheNewAnswerStillSupersedesTheOld(self):
		"""The reader answered the question in the shape it is asked now, in that profile."""
		self.stack({}, {"flowTableHeaders": False, "flowTableHeadersMode": bmConfig.ALWAYS})
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.OFF.value
		self.assertTrue(bmConfig.shouldPinTableHeaders())

	def test_nobodyAnsweringAnywhereStillFollowsNvda(self):
		self.stack({"segmentCount": 4}, {"segmentCount": 2})
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.OFF.value
		self.assertFalse(bmConfig.shouldPinTableHeaders())
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.ROWS_AND_COLUMNS.value
		self.assertTrue(bmConfig.shouldPinTableHeaders())

	def test_theRepeatedColumnReadsThemTheSameWay(self):
		self.stack({"flowTablePinKeyMode": bmConfig.ALWAYS}, {"flowTablePinKey": False})
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.ROWS_AND_COLUMNS.value
		self.assertFalse(bmConfig.shouldPinKeyColumn())


class TestSavedTableLayoutsLiveInTheBaseConfiguration(ConfigTestCase):
	"""One string, so a profile holding it hid every layout in the base while that profile was on.
	Decided with the reader, 16 September 2026: one store, whatever profile is active."""

	class Profile(dict):
		"""An activated profile, which upstream is a `ConfigObj` carrying its name."""

		def __init__(self, name, values):
			super().__init__(values)
			self.name = name

	def setUp(self):
		super().setUp()
		self.addCleanup(
			lambda: config.conf.profiles[0].get(bmConfig.CONFIG_SECTION, {}).pop("tableLayouts", None)
		)

	def test_writingWithAProfileActiveWritesTheBase(self):
		config.conf.profiles.append(self.Profile("outlook", {}))
		realBmConfig["setTableLayouts"]("the store")
		self.assertEqual("the store", config.conf.profiles[0][bmConfig.CONFIG_SECTION]["tableLayouts"])
		self.assertNotIn(bmConfig.CONFIG_SECTION, config.conf.profiles[-1])

	def test_readingWithAProfileActiveReadsTheBase(self):
		realBmConfig["setTableLayouts"]("the store")
		config.conf.profiles.append(
			self.Profile("outlook", {bmConfig.CONFIG_SECTION: {"tableLayouts": "hidden"}})
		)
		self.assertEqual("the store", realBmConfig["tableLayouts"]())
