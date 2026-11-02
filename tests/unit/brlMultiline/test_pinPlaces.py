# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for places, where pins make their own room, and for setting pins aside for now.

Written for a reader who has not set up segments: a Monarch left whole, a page pinned above the
focus, then a chat pinned beside it, sharing the rows without anything having been planned.
See docs/design/pin-places-plan.md.
"""

import unittest
from unittest import mock

from ._stubs import (
	CONFIG,
	FakeHandler,
	FakeNavigatorObject,
	FakeTreeInterceptor,
	callAfterQueue,
	callLaterQueue,
	fakeRun,
	fakeVirtualDisplay,
	installStubs,
	setBandConfig,
	spokenMessages,
)

installStubs()

from .test_plugin import MONARCH_COLS, MONARCH_ROWS, PluginTestCase  # noqa: E402

from brlMultiline import flowBuild, pinPlaces  # noqa: E402
from brlMultiline.layout import SegmentRect  # noqa: E402
from brlMultiline.panels import SinglePanel  # noqa: E402
from brlMultiline.pinPlaces import (  # noqa: E402
	FOCUS_PLACE,
	PlaceError,
	Places,
	divide,
	flowRoom,
	minimumFor,
)
from brlMultiline.views import viewFromConfig  # noqa: E402

MONARCH = SegmentRect(0, 0, MONARCH_ROWS, MONARCH_COLS)
FOCUS_80 = SegmentRect(8, 0, 1, 80)


class DyingObject(FakeNavigatorObject):
	"""An object that raises once its window has closed, as a dead accessible object does."""

	dead = False

	@property
	def role(self):
		if self.dead:
			raise RuntimeError("the object has died")
		return self._role

	@role.setter
	def role(self, value):
		self._role = value


class TestDividing(unittest.TestCase):
	def test_fillsShareEqually(self):
		self.assertEqual(divide(9, [None, None, None], 1), [3, 3, 3])

	def test_aRowLeftOverGoesToTheEarlierFill(self):
		self.assertEqual(divide(8, [None, None, None], 1), [3, 3, 2])

	def test_sizesComeFirstAndTheFillsShareTheRest(self):
		self.assertEqual(divide(8, [None, 3], 1), [5, 3])

	def test_withNoFillTheRestIsLeft(self):
		self.assertEqual(divide(8, [2, 3], 1), [2, 3])

	def test_aFillThatWouldGetLessThanTheLeastIsRefused(self):
		with self.assertRaises(PlaceError):
			divide(8, [7, None, None], 1)

	def test_sizesThatDoNotFitAreRefused(self):
		with self.assertRaises(PlaceError):
			divide(8, [5, 4], 1)

	def test_aRowDisplayKeepsAQuarterOfItsRow(self):
		"""A share, so it suits an 80 cell display and a 40 alike."""
		self.assertEqual(minimumFor(FOCUS_80), 20)
		self.assertEqual(minimumFor(SegmentRect(0, 0, 1, 40)), 10)
		self.assertEqual(minimumFor(MONARCH), 1)


class TestPlaces(unittest.TestCase):
	def setUp(self):
		self.places = Places()

	def members(self, placeId=FOCUS_PLACE):
		return [None if member is None else member.key for member in self.places.membersFor(placeId)]

	def test_aPinAfterTheFocusGoesBelowIt(self):
		pin = self.places.add(FOCUS_PLACE, MONARCH, None)
		self.assertEqual(self.members(), [None, pin.key])

	def test_aPinBeforeTheFocusGoesAboveIt(self):
		pin = self.places.add(FOCUS_PLACE, MONARCH, None, before=True)
		self.assertEqual(self.members(), [pin.key, None])

	def test_aPinGoesAfterThePinsAlreadyOnItsSide(self):
		first = self.places.add(FOCUS_PLACE, MONARCH, None)
		second = self.places.add(FOCUS_PLACE, MONARCH, None)
		self.assertEqual(self.members(), [None, first.key, second.key])

	def test_orBeforeThemWhenAsked(self):
		first = self.places.add(FOCUS_PLACE, MONARCH, None)
		second = self.places.add(FOCUS_PLACE, MONARCH, None, atStart=True)
		self.assertEqual(self.members(), [None, second.key, first.key])

	def test_aPinThereIsNoRoomForIsRefusedSayingHowMuchIsFree(self):
		self.places.add(FOCUS_PLACE, MONARCH, 5)
		with self.assertRaises(PlaceError) as caught:
			self.places.add(FOCUS_PLACE, MONARCH, 4)
		self.assertEqual(caught.exception.room, 2)
		self.assertEqual(len(self.places.keys()), 1)

	def test_aResizeThereIsNoRoomForChangesNothing(self):
		pin = self.places.add(FOCUS_PLACE, MONARCH, 3)
		with self.assertRaises(PlaceError):
			self.places.resize(pin.key, MONARCH, 8)
		self.assertEqual(self.places.pinFor(pin.key).size, 3)

	def test_movingAPinTakesItPastTheFocus(self):
		pin = self.places.add(FOCUS_PLACE, MONARCH, None)
		self.assertTrue(self.places.move(pin.key, -1))
		self.assertEqual(self.members(), [pin.key, None])
		self.assertFalse(self.places.move(pin.key, -1))

	def test_aPlaceWithNoPinsLeftIsForgotten(self):
		pin = self.places.add("freedomScientific", FOCUS_80, None)
		self.places.remove(pin.key)
		self.assertEqual(self.places.places, {})

	def test_pinsTheReaderNoLongerHasAreForgotten(self):
		kept = self.places.add(FOCUS_PLACE, MONARCH, None)
		self.places.add(FOCUS_PLACE, MONARCH, None)
		self.assertTrue(self.places.keepOnly([kept.key]))
		self.assertEqual(self.places.keys(), {kept.key})


class TestLayingPlacesOut(unittest.TestCase):
	def view(self):
		CONFIG["segmentCount"] = 1
		return viewFromConfig(MONARCH_ROWS, MONARCH_COLS)

	def test_theFocusPlaceDividesTheFocusSegment(self):
		places = Places()
		pin = places.add(FOCUS_PLACE, MONARCH, None, before=True)
		view = self.view()
		placed = pinPlaces.withPlaces(view, places, {FOCUS_PLACE: MONARCH}, MONARCH_ROWS, MONARCH_COLS)
		rects = {spec.key: spec.rect for spec in placed.flatten()}
		self.assertEqual(rects[pin.key], SegmentRect(0, 0, 4, MONARCH_COLS))
		self.assertEqual(rects[view.focusSegmentKey], SegmentRect(4, 0, 4, MONARCH_COLS))
		self.assertEqual(placed.focusSegmentKey, view.focusSegmentKey)

	def test_aPinsSegmentIsOrdinarySpace(self):
		"""So everything that works with a pin in the reader's own segment works with this one."""
		places = Places()
		pin = places.add(FOCUS_PLACE, MONARCH, None)
		placed = pinPlaces.withPlaces(self.view(), places, {FOCUS_PLACE: MONARCH}, MONARCH_ROWS, MONARCH_COLS)
		spec = next(spec for spec in placed.flatten() if spec.key == pin.key)
		self.assertFalse(spec.isReserved)

	def test_aPlaceThatIsNotThereIsLeftOut(self):
		places = Places()
		places.add("freedomScientific", FOCUS_80, None)
		view = self.view()
		self.assertIs(pinPlaces.withPlaces(view, places, {}, MONARCH_ROWS, MONARCH_COLS), view)


class TestFlowRoom(unittest.TestCase):
	def test_withNoPinInTheWayTheBandIsAsAsked(self):
		self.assertEqual(flowRoom(MONARCH, [], None, MONARCH_ROWS, MONARCH_COLS), MONARCH)

	def test_theBandKeepsToTheRowsBesideTheFocus(self):
		pins = [SegmentRect(0, 0, 3, MONARCH_COLS)]
		focus = SegmentRect(3, 0, 5, MONARCH_COLS)
		self.assertEqual(flowRoom(MONARCH, pins, focus, MONARCH_ROWS, MONARCH_COLS), focus)


class PlacesTestCase(PluginTestCase):
	"""A Monarch left whole: one segment, which follows the focus."""

	segmentCount = 1

	def pinTo(self, placeId=FOCUS_PLACE, name="the page", **kwargs):
		return self.plugin.pinToPlace(placeId, FakeNavigatorObject(name), **kwargs)

	def keyOf(self, name):
		return next(key for key, monitor in self.plugin.pins.items() if monitor.name == name)

	def rectOf(self, name):
		return self.container.segmentForKey(self.keyOf(name)).rect

	@property
	def focusRect(self):
		return self.container.focusSegment.rect


class TestPinningInAPlace(PlacesTestCase):
	def test_aPinNeedsNoSegmentSetUpFirst(self):
		self.assertTrue(self.pinTo(before=True))
		self.assertEqual(self.rectOf("the page"), SegmentRect(0, 0, 4, MONARCH_COLS))
		self.assertEqual(self.focusRect, SegmentRect(4, 0, 4, MONARCH_COLS))
		self.assertIn("Monitoring the page in segment 0", spokenMessages)

	def test_aSecondPinSharesTheRows(self):
		self.pinTo(before=True)
		self.pinTo(name="the chat", before=True)
		self.assertEqual(self.rectOf("the page").numRows, 3)
		self.assertEqual(self.rectOf("the chat").numRows, 3)
		self.assertEqual(self.focusRect.numRows, 2)

	def test_aPinCanAskForItsRows(self):
		self.pinTo(before=True)
		self.pinTo(name="the chat", size=2)
		self.assertEqual(self.rectOf("the chat").numRows, 2)
		self.assertEqual(self.rectOf("the page").numRows, 3)

	def test_aPinThereIsNoRoomForIsRefusedSayingHowMuchIsFree(self):
		self.pinTo(size=5)
		self.assertFalse(self.pinTo(name="the chat", size=4))
		self.assertIn("Only 2 rows are free there", spokenMessages)
		self.assertEqual(len(self.plugin.pins), 1)

	def test_releasingAPinGivesItsRowsBack(self):
		self.pinTo(before=True)
		self.pinTo(name="the chat", before=True)
		self.plugin.releasePin(self.keyOf("the chat"))
		self.assertEqual(self.rectOf("the page").numRows, 4)
		self.assertEqual(self.focusRect.numRows, 4)

	def test_releasingTheLastPinGivesTheFocusTheWholeDisplay(self):
		self.pinTo()
		self.plugin.releasePin(self.keyOf("the page"))
		self.assertEqual(self.focusRect, MONARCH)
		self.assertEqual(self.plugin.places.places, {})

	def test_aPinCanBeResizedWithoutReleasingIt(self):
		self.pinTo(before=True)
		monitor = self.plugin.pins[self.keyOf("the page")]
		self.assertIsNone(self.plugin.resizePin(self.keyOf("the page"), 6))
		self.assertEqual(self.rectOf("the page").numRows, 6)
		self.assertIs(self.plugin.pins[self.keyOf("the page")], monitor)

	def test_aResizeThereIsNoRoomForIsRefused(self):
		self.pinTo()
		self.assertEqual(self.plugin.resizePin(self.keyOf("the page"), 8), "Only 7 rows are free there")

	def test_aPinCanBeMovedPastTheFocus(self):
		self.pinTo()
		self.assertTrue(self.plugin.shiftPin(self.keyOf("the page"), -1))
		self.assertEqual(self.rectOf("the page").row, 0)

	def test_thePerSegmentReleaseCommandReleasesAPinInAPlace(self):
		self.pinTo(before=True)
		self.plugin.stopMonitoring(0)
		self.assertEqual(self.plugin.pins, {})
		self.assertEqual(self.focusRect, MONARCH)

	def test_aPinInAPlaceSurvivesARebuild(self):
		self.pinTo()
		key = self.keyOf("the page")
		self.plugin.rebuildBuffer()
		self.assertIn(key, self.plugin.pins)

	def test_thePlacesRowsAreTakenFromAnAppLayout(self):
		"""Pins come before app layouts, in places as in the reader's own segments."""
		self.pinTo(size=3)
		room = self.plugin.appLayoutRoom()
		self.assertEqual(room.area, SegmentRect(0, 0, 5, MONARCH_COLS))


class TestAPinReadAsAFlowInAPlace(PlacesTestCase):
	"""A page pinned above the focus, panned part way down, then sharing its rows."""

	def pinPage(self):
		page = FakeTreeInterceptor([f"line {index}" for index in range(20)], caretIndex=0)
		self.plugin.pinToPlace(
			FOCUS_PLACE,
			FakeNavigatorObject("the page", treeInterceptor=page),
			before=True,
		)
		return self.plugin.pins[self.keyOf("the page")]

	def topLine(self, monitor):
		return monitor.controller.blocks.get(monitor.controller.window.anchor.blockId).region.rawText

	def test_sharingThePlaceKeepsWhereThePageWasPannedTo(self):
		monitor = self.pinPage()
		monitor.controller.panForward()
		monitor.controller.panForward()
		before = self.topLine(monitor)
		self.assertNotEqual(before, "line 0")
		self.pinTo(name="the chat", before=True)
		self.assertEqual(self.rectOf("the page").numRows, 3)
		self.assertEqual(self.topLine(monitor), before)

	def test_resizingItKeepsItToo(self):
		monitor = self.pinPage()
		monitor.controller.panForward()
		before = self.topLine(monitor)
		self.plugin.resizePin(self.keyOf("the page"), 6)
		self.assertEqual(monitor._builtFor, (6, MONARCH_COLS))
		self.assertEqual(self.topLine(monitor), before)


class TestWhereAResizedPinIsReadTo(PlacesTestCase):
	"""Rows cut to the width, as the renderer does without NVDA's own word wrap, so a paragraph
	wraps over several rows."""

	def setUp(self):
		super().setUp()
		renderer = flowBuild.FlowRenderer

		def cutToTheWidth(*args, **kwargs):
			kwargs["fillRows"] = True
			return renderer(*args, **kwargs)

		patcher = mock.patch.object(flowBuild, "FlowRenderer", cutToTheWidth)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_aPageKeepsTheRowOfAWrappedParagraphItWasPannedTo(self):
		page = FakeTreeInterceptor(["word " * 150, "last line"], caretIndex=0)
		self.plugin.pinToPlace(FOCUS_PLACE, FakeNavigatorObject("the page", treeInterceptor=page), before=True)
		monitor = self.plugin.pins[self.keyOf("the page")]
		self.assertTrue(monitor.controller.panForward())
		before = monitor.controller.topPosition()
		self.assertGreater(before.offset, 0)
		self.assertIsNone(self.plugin.resizePin(self.keyOf("the page"), 3))
		self.assertEqual(monitor.controller.topPosition().compareEndPoints(before, "startToStart"), 0)

	def test_aListKeepsTheItemAtItsTop(self):
		items = fakeRun([f"item {index}" for index in range(20)])
		self.plugin.pinToPlace(FOCUS_PLACE, items[0], before=True)
		key, monitor = next(iter(self.plugin.pins.items()))
		self.assertTrue(monitor.controller.panForward())

		def topObject():
			control = monitor.controller
			return control.blocks.get(control.window.anchor.blockId).region.obj

		before = topObject()
		self.assertIsNot(before, items[0])
		self.assertIsNone(self.plugin.resizePin(key, 3))
		self.assertIs(topObject(), before)


class TestPlacesBesideTheFlow(PlacesTestCase):
	def setUp(self):
		super().setUp()
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()

	def test_theFlowKeepsToTheRowsThePinsLeave(self):
		self.pinTo(before=True)
		self.assertIn(self.keyOf("the page"), self.plugin.pins)
		self.assertEqual(self.plugin.flowBand.segment().rect, SegmentRect(4, 0, 4, MONARCH_COLS))

	def test_theFlowHasTheWholeDisplayBackWhenThePinGoes(self):
		self.pinTo(before=True)
		self.plugin.releasePin(self.keyOf("the page"))
		self.assertEqual(self.plugin.flowBand.segment().rect, MONARCH)

	def test_theFlowStopsWhenThePinsLeaveItTooFewRows(self):
		self.pinTo(before=True, size=7)
		self.assertIn(self.keyOf("the page"), self.plugin.pins)
		self.assertIsNone(self.plugin.flowBand)
		self.assertIn("too few rows left", spokenMessages[-1])

	def test_aResizeThatStopsTheFlowSaysSo(self):
		self.pinTo(before=True)
		spokenMessages.clear()
		self.assertIsNone(self.plugin.resizePin(self.keyOf("the page"), 7))
		self.assertIsNone(self.plugin.flowBand)
		self.assertEqual(spokenMessages, ["The flow has too few rows left, so it is off until there is room"])

	def test_andComesBackWhenThePinGoes(self):
		self.pinTo(before=True, size=7)
		self.plugin.releasePin(self.keyOf("the page"))
		self.assertEqual(self.plugin.flowBand.segment().rect, MONARCH)


class TestPlacesBesideAnAppLayout(PlacesTestCase):
	def test_theLayoutIsPlacedAgainInTheRowsThePinsLeave(self):
		import api

		from .test_plugin import ScriptedAppModule, chatLayout

		CONFIG["appLayouts"] = True
		focus = FakeNavigatorObject("Compose", role="editableText")
		focus.appModule = ScriptedAppModule(chatLayout(FakeNavigatorObject("Messages", role="list")))
		api.getFocusObject = lambda: focus
		self.plugin.event_gainFocus(focus, lambda: None)
		callLaterQueue.fire()
		self.assertTrue(self.plugin.appLayouts.isUp)
		self.pinTo(before=True, size=3)
		callLaterQueue.fire()
		self.assertIn(self.keyOf("the page"), self.plugin.pins)
		self.assertTrue(self.plugin.appLayouts.isUp)
		self.assertEqual(self.plugin.appLayouts.shown.panel.rect, SegmentRect(3, 0, 5, MONARCH_COLS))


class TestAnotherDisplayAsAPlace(PlacesTestCase):
	"""A Monarch above a Focus 80, with the focus on the Monarch."""

	def makeHandler(self):
		handler = FakeHandler(9, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 32),
			("freedomScientific", 8, 1, 80),
		)
		return handler

	def setUp(self):
		super().setUp()
		setBandConfig("hidBrailleStandard_8x32", segmentCount=1)
		setBandConfig("freedomScientific_1x80", segmentCount=1)
		CONFIG["focusSegment"] = 0
		self.plugin.rebuildBuffer()

	def test_theOtherDisplayIsOffered(self):
		self.assertEqual(set(self.plugin.offeredPlaces()), {FOCUS_PLACE, "freedomScientific"})

	def test_aFillPinTakesTheWholeDisplay(self):
		self.pinTo("freedomScientific")
		self.assertEqual(self.rectOf("the page"), FOCUS_80)

	def test_aSecondPinSharesItsCells(self):
		self.pinTo("freedomScientific")
		self.pinTo("freedomScientific", name="the chat")
		self.assertEqual(self.rectOf("the page"), SegmentRect(8, 0, 1, 40))
		self.assertEqual(self.rectOf("the chat"), SegmentRect(8, 40, 1, 40))

	def test_eachPinKeepsAQuarterOfTheRow(self):
		for number in range(4):
			self.assertTrue(self.pinTo("freedomScientific", name=f"pin {number}"))
		self.assertFalse(self.pinTo("freedomScientific", name="one too many"))
		self.assertIn("There is no room for that there", spokenMessages)

	def test_aSizedPinLeavesTheRestBlank(self):
		self.pinTo("freedomScientific", size=30)
		self.assertEqual(self.rectOf("the page"), SegmentRect(8, 0, 1, 30))

	def test_aDisplayHoldingAPinInTheReadersOwnSegmentIsNotOffered(self):
		"""A place there would take that segment, and the pin with it."""
		self.pin(1)
		self.assertNotIn("freedomScientific", self.plugin.offeredPlaces())


class TestAOneRowPinOnADisplayOfRows(PlacesTestCase):
	"""A Focus 80 above a Monarch, with the focus on the Focus 80 and the Monarch a place."""

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

	def test_aPinOfOneRowLeavesTheRestBlank(self):
		self.assertTrue(self.pinTo("hidBrailleStandard", size=1))
		self.assertEqual(self.rectOf("the page"), SegmentRect(1, 0, 1, 32))

	def test_resizingToOneRowKeepsThePin(self):
		self.pinTo("hidBrailleStandard", size=2)
		self.assertIsNone(self.plugin.resizePin(self.keyOf("the page"), 1))
		self.assertEqual(self.rectOf("the page"), SegmentRect(1, 0, 1, 32))

	def test_aResizeThatCannotBeLaidOutKeepsThePinAsItWas(self):
		self.pinTo("hidBrailleStandard", size=2)
		key = self.keyOf("the page")
		with mock.patch.object(self.plugin, "doPlacesFit", return_value=False):
			self.assertEqual(self.plugin.resizePin(key, 4), "There is no room for that there")
		self.assertEqual(self.plugin.places.pinFor(key).size, 2)
		self.assertEqual(self.rectOf("the page"), SegmentRect(1, 0, 2, 32))

	def test_aPinThatCannotBeLaidOutIsNotMade(self):
		with mock.patch.object(self.plugin, "doPlacesFit", return_value=False):
			self.assertFalse(self.pinTo("hidBrailleStandard"))
		self.assertEqual(self.plugin.places.places, {})


class TestMovingTheFocusWithPlaces(TestAOneRowPinOnADisplayOfRows):
	"""Places are laid over the reader's arrangement, so the focus can still move between displays."""

	def test_thePlacesLeaveBothDisplaysToMoveTo(self):
		self.pinTo("hidBrailleStandard")
		self.assertEqual(len(self.plugin.focusMoves.targets()), 2)

	def test_soDoPinsSetAside(self):
		self.pinTo("hidBrailleStandard")
		self.plugin.setPinsAside(True)
		self.assertEqual(len(self.plugin.focusMoves.targets()), 2)

	def test_thePinsOnTheOtherDisplayTradePlacesWithTheFocus(self):
		self.pinTo("hidBrailleStandard", size=3)
		monitor = self.plugin.pins[self.keyOf("the page")]
		self.plugin.script_changeFocusTrackingDisplay(None)
		self.assertIs(self.plugin.pins[self.keyOf("the page")], monitor)
		self.assertEqual(self.rectOf("the page"), SegmentRect(0, 0, 1, 80))
		self.assertEqual(self.focusRect.row, 1)
		self.assertIsNone(self.plugin.places.pinFor(self.keyOf("the page")).size)

	def test_aPinWithNoRoomWhereItGoesIsReleased(self):
		for number in range(5):
			self.assertTrue(self.pinTo("hidBrailleStandard", name=f"pin {number}"))
		self.plugin.script_changeFocusTrackingDisplay(None)
		self.assertEqual(len(self.plugin.pins), 4)
		self.assertIn("There is no room for pin 4, so its pin is released", spokenMessages)


class TestTheSegmentCommandsWithPinsAside(TestAOneRowPinOnADisplayOfRows):
	def test_pinningToASegmentShowsThemAgain(self):
		self.plugin.startMonitoring(1, FakeNavigatorObject("the clock"))
		self.plugin.setPinsAside(True)
		self.plugin.startMonitoring(1, FakeNavigatorObject("the chat"))
		self.assertFalse(self.plugin.arePinsAside)
		self.assertEqual(self.plugin.pinsAside, {})

	def test_releasingASegmentReleasesAPinSetAsideThere(self):
		self.plugin.startMonitoring(1, FakeNavigatorObject("the clock"))
		self.plugin.setPinsAside(True)
		self.plugin.stopMonitoring(1)
		self.assertEqual(self.plugin.pinsAside, {})
		self.assertEqual(self.plugin.pins, {})


class TestSettingPinsAside(PlacesTestCase):
	def test_theFocusHasTheWholeDisplayWhileTheyAreAside(self):
		self.pinTo(before=True)
		self.plugin.setPinsAside(True)
		self.assertEqual(self.focusRect, MONARCH)
		self.assertEqual(self.plugin.pins, {})
		self.assertEqual(len(self.plugin.pinsAside), 1)
		self.assertIn("Pins set aside", spokenMessages)

	def test_theyComeBackAsTheyWere(self):
		self.pinTo(before=True, size=3)
		monitor = self.plugin.pins[self.keyOf("the page")]
		self.plugin.setPinsAside(True)
		self.plugin.setPinsAside(False)
		self.assertIs(self.plugin.pins[self.keyOf("the page")], monitor)
		self.assertEqual(self.rectOf("the page"), SegmentRect(0, 0, 3, MONARCH_COLS))
		self.assertIn("Pins shown", spokenMessages)

	def test_aPinInTheReadersOwnSegmentComesBackToo(self):
		CONFIG["segmentCount"] = 2
		CONFIG["focusSegment"] = 1
		self.plugin.rebuildBuffer()
		self.pin(0, name="the clock")
		self.plugin.setPinsAside(True)
		self.assertEqual(self.container.numSegments, 1)
		self.plugin.setPinsAside(False)
		self.assertIn("display.0", self.plugin.pins)

	def test_aPinWhoseObjectWentIsReleasedWhenTheyComeBack(self):
		self.pinTo(before=True)
		gone = DyingObject("the closed window")
		self.plugin.pinToPlace(FOCUS_PLACE, gone)
		self.plugin.setPinsAside(True)
		gone.dead = True
		self.plugin.setPinsAside(False)
		self.assertEqual([monitor.name for monitor in self.plugin.pins.values()], ["the page"])
		self.assertIn("Pins shown. the closed window is gone, so its pin is released", spokenMessages)
		self.assertEqual(len(self.plugin.places.keys()), 1)

	def test_aPinAsideCanBeReleasedWithoutShowingThem(self):
		self.pinTo()
		key = self.keyOf("the page")
		self.plugin.setPinsAside(True)
		self.plugin.releasePin(key)
		self.assertEqual(self.plugin.pinsAside, {})
		self.assertTrue(self.plugin.arePinsAside)
		self.assertEqual(self.plugin.places.places, {})

	def test_pinningShowsThemAgainFirst(self):
		self.pinTo()
		self.plugin.setPinsAside(True)
		self.pinTo(name="the chat")
		self.assertFalse(self.plugin.arePinsAside)
		self.assertEqual(len(self.plugin.pins), 2)

	def test_aPageStaysPinnedWhenTheLinkItWasPinnedFromGoes(self):
		page = FakeTreeInterceptor([f"line {index}" for index in range(20)])
		FakeNavigatorObject("the page", role="DOCUMENT", treeInterceptor=page)
		link = DyingObject("a link", role="LINK", treeInterceptor=page)
		self.plugin.pinToPlace(FOCUS_PLACE, link, before=True)
		key = next(iter(self.plugin.pins))
		self.plugin.setPinsAside(True)
		link.dead = True
		self.plugin.setPinsAside(False)
		self.assertIn(key, self.plugin.pins)

	def test_withNothingPinnedThereIsNothingToSetAside(self):
		self.plugin.setPinsAside(True)
		self.assertFalse(self.plugin.arePinsAside)
		self.assertIn("Nothing is pinned", spokenMessages)

	def test_theCommandTurnsItOver(self):
		self.pinTo()
		self.plugin.script_togglePinsAside(None)
		self.assertTrue(self.plugin.arePinsAside)
		self.plugin.script_togglePinsAside(None)
		self.assertFalse(self.plugin.arePinsAside)


class TestAClaimOverAPlace(PlacesTestCase):
	def test_aPinAClaimTakesIsForgottenByItsPlace(self):
		self.pinTo(before=True)
		self.plugin.activatePanel(SinglePanel("reader", SegmentRect(0, 0, 4, MONARCH_COLS)))
		callAfterQueue.flush()
		self.assertEqual(self.plugin.pins, {})
		self.assertEqual(self.plugin.places.places, {})
