# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the pinned objects dialog's manager, and the command that opens it.

Written for a Monarch with too few keys for a pin and a release command per segment: one key
opens the dialog, a segment is chosen or typed, and Enter pins what the navigator was on when
the key was pressed.
"""

import unittest

from ._stubs import (
	CONFIG,
	FakeHandler,
	FakeNavigatorObject,
	FakeTreeInterceptor,
	callAfterQueue,
	fakeVirtualDisplay,
	installStubs,
	setBandConfig,
	spokenMessages,
)

installStubs()

from .test_plugin import MONARCH_COLS, PluginTestCase  # noqa: E402

from brlMultiline import pinDialog  # noqa: E402
from brlMultiline.layout import SegmentRect  # noqa: E402
from brlMultiline.panels import SinglePanel  # noqa: E402
from brlMultiline.pinDialog import PinManager  # noqa: E402


class PinDialogTestCase(PluginTestCase):
	segmentCount = 4

	def manager(self, name="the chat"):
		return PinManager(self.plugin, FakeNavigatorObject(name))


class TestWhatIsOffered(PinDialogTestCase):
	def test_theObjectIsNamed(self):
		self.assertEqual(self.manager("the chat").objectName(), "the chat")

	def test_inBrowseModeThePageIsNamedAndNotTheLinkUnderTheCursor(self):
		"""Since the pin shows the page."""
		page = FakeTreeInterceptor(["Headlines"])
		FakeNavigatorObject("the news page", role="DOCUMENT", treeInterceptor=page)
		link = FakeNavigatorObject("Read more", role="LINK", treeInterceptor=page)
		self.assertEqual(PinManager(self.plugin, link).objectName(), "the news page")

	def test_inFocusModeTheControlIsNamed(self):
		page = FakeTreeInterceptor(["Headlines"], passThrough=True)
		FakeNavigatorObject("the news page", role="DOCUMENT", treeInterceptor=page)
		field = FakeNavigatorObject("Search", role="EDITABLETEXT", treeInterceptor=page)
		self.assertEqual(PinManager(self.plugin, field).objectName(), "Search")

	def test_thePinIsNamedAsTheDialogNamedIt(self):
		page = FakeTreeInterceptor(["Headlines"])
		FakeNavigatorObject("the news page", role="DOCUMENT", treeInterceptor=page)
		manager = PinManager(self.plugin, FakeNavigatorObject("Read more", role="LINK", treeInterceptor=page))
		manager.choose("1")
		manager.pinChosen()
		self.assertEqual(self.plugin.pins["display.1"].name, "the news page")

	def test_theTargetsAreTheSegmentsAPinCanGoOnThenThePlaces(self):
		self.pin(0)
		targets = self.manager().targets()
		self.assertEqual(
			[(target.key, target.name) for target in targets],
			[
				("display.1", "Segment 1"),
				("display.2", "Segment 2"),
				(None, "Above the focus"),
				(None, "Below the focus"),
			],
		)

	def test_withNoObjectThereIsNothingToPinTo(self):
		self.assertEqual(PinManager(self.plugin, None).targets(), [])

	def test_thePinsAreListedInDisplayOrder(self):
		self.pin(2, name="second")
		self.pin(0, name="first")
		self.assertEqual(
			self.manager().pins(),
			[("display.0", "first: Segment 0"), ("display.2", "second: Segment 2")],
		)


class TestChoosing(PinDialogTestCase):
	def test_aNameFromTheListIsChosen(self):
		manager = self.manager()
		self.assertIsNone(manager.choose("Segment 2"))
		self.assertEqual(manager.chosenKey, "display.2")

	def test_aTypedNumberIsChosen(self):
		manager = self.manager()
		self.assertIsNone(manager.choose(" 1 "))
		self.assertEqual(manager.chosenKey, "display.1")

	def test_nothingTypedIsRefused(self):
		manager = self.manager()
		self.assertIn("type its number", manager.choose(""))
		self.assertIsNone(manager.chosenKey)

	def test_aSegmentThatIsNotThereIsRefused(self):
		self.assertIn("no segment 9", self.manager().choose("9"))

	def test_theFocusSegmentIsRefusedWithTheCommandsReason(self):
		focus = str(self.container.focusSegmentNumber)
		self.assertIn("follows the focus", self.manager().choose(focus))

	def test_aClaimedSegmentIsRefused(self):
		self.plugin.activatePanel(SinglePanel("table", SegmentRect(2, 0, 2, MONARCH_COLS)))
		number = self.container.numberForKey("table")
		self.assertIn("in use by table", self.manager().choose(str(number)))

	def test_aSegmentAlreadyHoldingAPinIsRefused(self):
		"""Rather than replaced, so a reader never drops a pin without meaning to."""
		self.pin(1)
		self.assertIn("Release it first", self.manager().choose("1"))


class TestPinningAndReleasing(PinDialogTestCase):
	def test_theChosenObjectIsPinnedAndNotTheNavigator(self):
		"""By the time Pin is pressed, the navigator is on the dialog's own combo box."""
		import api

		manager = self.manager("the chat")
		api.getNavigatorObject = lambda: FakeNavigatorObject("the dialog's combo box")
		manager.choose("1")
		manager.pinChosen()
		self.assertEqual(self.plugin.pins["display.1"].name, "the chat")
		self.assertIn("Monitoring the chat in segment 1", spokenMessages)

	def test_nothingChosenPinsNothing(self):
		self.manager().pinChosen()
		self.assertEqual(self.plugin.monitoredKeys, set())

	def test_aChosenSegmentThatWentIsSaid(self):
		manager = self.manager()
		manager.choose("1")
		self.plugin.activatePanel(SinglePanel("strip", SegmentRect(0, 0, 8, MONARCH_COLS), focusSegmentKey="strip"))
		manager.pinChosen()
		self.assertEqual(self.plugin.monitoredKeys, set())
		self.assertIn("That segment is no longer on the display", spokenMessages)

	def test_releasingAPinFreesItsSegment(self):
		self.pin(1)
		manager = self.manager()
		manager.release("display.1")
		self.assertEqual(self.plugin.monitoredKeys, set())
		self.assertIn("display.1", [target.key for target in manager.targets()])
		self.assertIn("Stopped monitoring in segment 1", spokenMessages)


class TestPinningInAPlace(PinDialogTestCase):
	"""A Monarch left whole, which is what places are for."""

	segmentCount = 1

	def test_onlyThePlacesAreOfferedOnAnUndividedDisplay(self):
		self.assertEqual(
			[target.name for target in self.manager().targets()],
			["Above the focus", "Below the focus"],
		)

	def test_aPlaceHoldingPinsSaysItWillShare(self):
		self.manager().plugin.pinToPlace("focus", FakeNavigatorObject("the page"))
		self.assertIn("Below the focus (holds 1 pin, will share)", [target.name for target in self.manager().targets()])

	def test_thePinIsMadeInThePlaceChosenAtTheSizeChosen(self):
		manager = self.manager("the chat")
		self.assertIsNone(manager.choose("Above the focus", "3 rows"))
		manager.pinChosen()
		key = next(iter(self.plugin.pins))
		self.assertEqual(self.container.segmentForKey(key).rect.numRows, 3)
		self.assertEqual(self.container.segmentForKey(key).rect.row, 0)

	def test_aSizeThereIsNoRoomForKeepsTheDialogUp(self):
		manager = self.manager()
		self.assertEqual(manager.choose("Below the focus", "8"), "Only 7 rows are free there")
		self.assertIsNone(manager.chosen)
		self.assertEqual(self.plugin.places.places, {})

	def test_aSizeThatIsNotOneIsRefused(self):
		self.assertIn("Choose a size", self.manager().choose("Below the focus", "lots"))

	def test_thePinsListSaysHowEachIsSized(self):
		self.plugin.pinToPlace("focus", FakeNavigatorObject("the page"), size=2)
		self.assertEqual(self.manager().pins(), [(next(iter(self.plugin.pins)), "the page: Segment 1, 2 rows")])

	def test_aPinCanBeResizedFromTheList(self):
		self.plugin.pinToPlace("focus", FakeNavigatorObject("the page"))
		key = next(iter(self.plugin.pins))
		manager = self.manager()
		self.assertIsNone(manager.resize(key, "5 rows"))
		self.assertEqual(manager.sizeOf(key), "5 rows")

	def test_pinsSetAsideAreStillListed(self):
		self.plugin.pinToPlace("focus", FakeNavigatorObject("the page"))
		self.plugin.setPinsAside(True)
		manager = self.manager()
		self.assertEqual([line for _key, line in manager.pins()], ["the page: set aside, Fill"])
		self.assertEqual(
			[target.name for target in manager.targets()],
			["Above the focus", "Below the focus (holds 1 pin, will share)"],
		)


class TestAWholeDisplaySegment(PinDialogTestCase):
	"""A Focus 80 above a Monarch left as one segment, with the focus on the Focus."""

	def makeHandler(self):
		handler = FakeHandler(9, 80)
		handler.display = fakeVirtualDisplay(
			("freedomScientific", 0, 1, 80),
			("hidBrailleStandard", 1, 8, 32),
		)
		return handler

	def setUp(self):
		super().setUp()
		setBandConfig("freedomScientific_1x80", segmentCount=1)
		setBandConfig("hidBrailleStandard_8x32", segmentCount=1)
		CONFIG["focusSegment"] = 0
		self.plugin.rebuildBuffer()

	def test_itIsOfferedOnlyAsItsDisplaysPlace(self):
		"""The same rows twice would be confusing, and only the place can share them later."""
		names = [target.name for target in self.manager().targets()]
		self.assertNotIn("Segment 1 (segment 0 of the second display)", names)
		self.assertIn("All of the second display", names)

	def test_typingItsNumberPinsInThePlace(self):
		manager = self.manager("the page")
		self.assertIsNone(manager.choose("1"))
		manager.pinChosen()
		self.assertEqual(len(self.plugin.places.keys()), 1)
		self.assertTrue(self.plugin.pinToPlace("hidBrailleStandard", FakeNavigatorObject("the chat")))
		self.assertEqual(len(self.plugin.pins), 2)


class TestSizesOffered(PinDialogTestCase):
	"""A Focus 80 alone, where the focus keeps a quarter of the row."""

	segmentCount = 1

	def makeHandler(self):
		return FakeHandler(1, 80)

	def target(self, name):
		manager = self.manager()
		return manager, next(target for target in manager.targets() if target.name == name)

	def test_onlySizesThereIsRoomForAreOffered(self):
		manager, target = self.target("After the focus")
		self.assertEqual(manager.sizeChoicesFor(target)[-1], "60 cells")

	def test_theRoomLeftIsOfferedOnceAPinTakesSome(self):
		self.plugin.pinToPlace("focus", FakeNavigatorObject("the page"), size=30)
		manager, target = self.target("After the focus (holds 1 pin, will share)")
		self.assertEqual(manager.sizeChoicesFor(target)[-1], "30 cells")

	def test_aPinCanBeResizedToNoMoreThanTheOthersLeave(self):
		self.plugin.pinToPlace("focus", FakeNavigatorObject("the page"), size=30)
		key = next(iter(self.plugin.pins))
		self.assertEqual(self.manager().resizeChoicesFor(key)[-1], "60 cells")

	def test_aSegmentIsOfferedFillAlone(self):
		self.assertEqual(self.manager().sizeChoicesFor(None), ["Fill"])


class TestSizes(unittest.TestCase):
	def test_fillAndNothingTypedBothFill(self):
		self.assertIsNone(pinDialog.parseSize("Fill"))
		self.assertIsNone(pinDialog.parseSize(""))

	def test_aCountIsReadFromWhatIsChosen(self):
		self.assertEqual(pinDialog.parseSize("3 rows"), 3)

	def test_aOneRowPlaceIsSizedInCells(self):
		choices = pinDialog.sizeChoices(SegmentRect(8, 0, 1, 80))
		self.assertEqual(choices[:3], ["Fill", "20 cells", "25 cells"])
		self.assertEqual(choices[-1], "80 cells")


class TestTheCommand(PinDialogTestCase):
	def test_theDialogIsQueuedAndNotShownInsideTheScript(self):
		import api

		api.getNavigatorObject = lambda: FakeNavigatorObject("the chat")
		shown = []
		original = pinDialog._show
		pinDialog._show = shown.append
		self.addCleanup(setattr, pinDialog, "_show", original)
		original_canDraw = pinDialog.CAN_DRAW
		pinDialog.CAN_DRAW = True
		self.addCleanup(setattr, pinDialog, "CAN_DRAW", original_canDraw)
		self.plugin.script_managePins(None)
		self.assertEqual(shown, [])
		callAfterQueue.flush()
		self.assertEqual(len(shown), 1)
		self.assertEqual(shown[0].objectName(), "the chat")

	def test_withNowhereToPinAndNothingPinnedItSaysSo(self):
		self.plugin.activatePanel(SinglePanel("strip", SegmentRect(0, 0, 8, MONARCH_COLS), focusSegmentKey="strip"))
		self.assertFalse(pinDialog.managePins(self.plugin))
		self.assertIn("There is nowhere to pin an object, and nothing is pinned", spokenMessages)
