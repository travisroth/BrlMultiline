# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the pinned objects dialog's manager, and the command that opens it.

Written for a Monarch with too few keys for a pin and a release command per segment: one key
opens the dialog, a segment is chosen or typed, and Enter pins what the navigator was on when
the key was pressed.
"""

from ._stubs import (
	FakeNavigatorObject,
	FakeTreeInterceptor,
	callAfterQueue,
	installStubs,
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

	def test_theTargetsAreTheSegmentsAPinCanGoOn(self):
		self.pin(0)
		self.assertEqual(
			self.manager().targets(),
			[("display.1", "Segment 1"), ("display.2", "Segment 2")],
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
		self.assertIn("display.1", [key for key, _name in manager.targets()])
		self.assertIn("Stopped monitoring in segment 1", spokenMessages)


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
