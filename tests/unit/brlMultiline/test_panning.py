# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for panning keys belonging to the display they are on.

Two rules, both found on hardware. A display's panning keys drive that display's own segment
rather than whatever holds the focus. And which of the two keys means forward is the pressed
display's setting, because that is a fact about how the keys sit on a piece of hardware.

The narrow part is knowing which display was pressed at all, since NVDA's hook takes no
gesture. Only gestures bound to NVDA's own panning commands are recorded, and the record is
consumed by the scroll that follows, so a routing key press cannot leave a display behind for
a later scroll to act on.
"""

import types
import unittest

from ._stubs import (
	CONFIG,
	BrailleDisplayGesture,
	fakeVirtualDisplay,
	installStubs,
	resetConfig,
	setBandConfig,
)

installStubs()

import braille  # noqa: E402
import inputCore  # noqa: E402

from brlMultiline import panning  # noqa: E402
from brlMultiline.layout import SegmentRect  # noqa: E402

MONARCH = "hidBrailleStandard"
FOCUS = "freedomScientific"
MONARCH_KEY = "hidBrailleStandard_8x32"
FOCUS_KEY = "freedomScientific_1x80"

#: The Monarch divided in two, the Focus whole: segments 0 and 1 on the Monarch, 2 on the Focus.
COMPOSITE_RECTS = [
	SegmentRect(row=0, col=0, numRows=4, numCols=32),
	SegmentRect(row=4, col=0, numRows=4, numCols=32),
	SegmentRect(row=8, col=0, numRows=1, numCols=80),
]


def script_braille_scrollForward(gesture):
	"""Stands in for NVDA's own command, and is recognised by its name alone."""


def script_braille_scrollBack(gesture):
	pass


def script_somethingElse(gesture):
	pass


class StubGesture(BrailleDisplayGesture):
	"""A braille gesture: it names its display and what it is bound to.

	A real subclass of the shared base, because that is what `_noteGestureSource` tests with
	`isinstance` — a duck typed one would be ignored and every test below would pass vacuously.
	"""

	def __init__(self, source, script=script_braille_scrollForward):
		self.source = source
		self.script = script


def fakeContainer(focusSegmentNumber, rects=None):
	"""The two things `nativeSegmentForSource` asks of a container."""
	return types.SimpleNamespace(
		rects=list(COMPOSITE_RECTS if rects is None else rects),
		focusSegmentNumber=focusSegmentNumber,
	)


class PanningTestCase(unittest.TestCase):
	def setUp(self):
		resetConfig()
		panning.remove()
		self.handler = types.SimpleNamespace(
			display=fakeVirtualDisplay(
				(MONARCH, 0, 8, 32),
				(FOCUS, 8, 1, 80),
			),
		)
		braille.handler = self.handler
		panning.install()

	def tearDown(self):
		panning.remove()
		braille.handler = None
		resetConfig()

	def press(self, source, script=script_braille_scrollForward):
		"""Dispatch a braille gesture, as a driver's read loop does."""
		inputCore.decide_executeGesture.decide(gesture=StubGesture(source, script))


class TestWhatIsRecorded(PanningTestCase):
	"""Only panning keys, and only until the scroll they caused."""

	def test_aPanningKeyIsRecorded(self):
		self.press(MONARCH)
		self.assertEqual(panning.takePendingSource(), MONARCH)

	def test_eitherDirectionIsRecorded(self):
		self.press(FOCUS, script_braille_scrollBack)
		self.assertEqual(panning.takePendingSource(), FOCUS)

	def test_anyOtherKeyIsNot(self):
		"""The point of the filter: a routing press must not steer a later scroll."""
		self.press(MONARCH, script_somethingElse)
		self.assertIsNone(panning.takePendingSource())

	def test_anUnboundKeyIsNot(self):
		self.press(MONARCH, None)
		self.assertIsNone(panning.takePendingSource())

	def test_oneKeyPressDrivesOneScroll(self):
		self.press(MONARCH)
		self.assertEqual(panning.takePendingSource(), MONARCH)
		self.assertIsNone(panning.takePendingSource())

	def test_aRoutingPressDoesNotClearAPendingPan(self):
		"""It records nothing, so it must also disturb nothing."""
		self.press(MONARCH)
		self.press(FOCUS, script_somethingElse)
		self.assertEqual(panning.takePendingSource(), MONARCH)

	def test_somethingThatIsNotABrailleGestureIsIgnored(self):
		inputCore.decide_executeGesture.decide(gesture=object())
		self.assertIsNone(panning.takePendingSource())

	def test_theDeciderNeverCancelsAGesture(self):
		self.assertTrue(inputCore.decide_executeGesture.decide(gesture=StubGesture(MONARCH)))

	def test_theDeciderSurvivesAGestureItCannotRead(self):
		self.assertTrue(panning._noteGestureSource(gesture=types.SimpleNamespace()))


class TestWhichSegment(PanningTestCase):
	"""A display's keys drive that display."""

	def test_aDisplayHoldingTheFocusDrivesTheFocusSegment(self):
		container = fakeContainer(focusSegmentNumber=2)
		self.assertEqual(panning.nativeSegmentForSource(FOCUS, container), 2)

	def test_theOtherDisplayDrivesItsOwnFirstSegment(self):
		"""The bug from the hardware run: the Monarch's keys were scrolling the Focus."""
		container = fakeContainer(focusSegmentNumber=2)
		self.assertEqual(panning.nativeSegmentForSource(MONARCH, container), 0)

	def test_itWorksTheOtherWayRound(self):
		container = fakeContainer(focusSegmentNumber=0)
		self.assertEqual(panning.nativeSegmentForSource(MONARCH, container), 0)
		self.assertEqual(panning.nativeSegmentForSource(FOCUS, container), 2)

	def test_aDisplayHoldingTheFocusInItsSecondSegmentDrivesThat(self):
		container = fakeContainer(focusSegmentNumber=1)
		self.assertEqual(panning.nativeSegmentForSource(MONARCH, container), 1)

	def test_noKeyPressMeansNoRedirect(self):
		"""Automatic scroll has no display behind it, so NVDA's own behaviour stands."""
		self.assertIsNone(panning.nativeSegmentForSource(None, fakeContainer(2)))

	def test_aDisplayThatIsNotAMemberMeansNoRedirect(self):
		self.assertIsNone(panning.nativeSegmentForSource("someOtherDriver", fakeContainer(2)))

	def test_anOrdinaryDisplayMeansNoRedirect(self):
		"""One display, nothing to resolve, and nothing about panning changes."""
		self.handler.display = types.SimpleNamespace(name=FOCUS)
		self.assertIsNone(panning.nativeSegmentForSource(FOCUS, fakeContainer(2)))

	def test_noContainerMeansNoRedirect(self):
		self.assertIsNone(panning.nativeSegmentForSource(MONARCH, None))

	def test_aDisplayWithNoSegmentsMeansNoRedirect(self):
		container = fakeContainer(0, rects=[SegmentRect(row=8, col=0, numRows=1, numCols=80)])
		self.assertIsNone(panning.nativeSegmentForSource(MONARCH, container))


class TestWhichDirection(PanningTestCase):
	def setUp(self):
		super().setUp()
		setBandConfig(MONARCH_KEY, reverseScrollBtns=True)
		setBandConfig(FOCUS_KEY, reverseScrollBtns=False)

	def test_theDisplayWhoseKeyWasPressedDecides(self):
		self.assertTrue(panning.shouldReverseForSource(MONARCH))
		self.assertFalse(panning.shouldReverseForSource(FOCUS))

	def test_aCommandHoldingItsGestureReadsItFromThere(self):
		"""The per segment commands never touch the record; they have the gesture."""
		self.assertTrue(panning.shouldReverseForGesture(StubGesture(MONARCH)))
		self.assertFalse(panning.shouldReverseForGesture(StubGesture(FOCUS)))

	def test_noGestureFallsBackToTheConnectedDisplay(self):
		CONFIG["reverseScrollBtns"] = True
		self.assertTrue(panning.shouldReverseForGesture(None))

	def test_anUnknownDisplayFallsBackToo(self):
		CONFIG["reverseScrollBtns"] = True
		self.assertIsNone(panning.displayKeyForSource("someOtherDriver"))
		self.assertTrue(panning.shouldReverseForSource("someOtherDriver"))

	def test_theDisplayKeyIsResolvedFromTheDeviceMap(self):
		self.assertEqual(panning.displayKeyForSource(FOCUS), FOCUS_KEY)


class TestLifetime(PanningTestCase):
	def test_installingTwiceRegistersOnce(self):
		before = len(inputCore.decide_executeGesture.handlers)
		panning.install()
		self.assertEqual(len(inputCore.decide_executeGesture.handlers), before)

	def test_removingUnregisters(self):
		panning.remove()
		self.assertEqual(inputCore.decide_executeGesture.handlers, [])

	def test_removingForgetsAPendingPan(self):
		self.press(MONARCH)
		panning.remove()
		self.assertIsNone(panning.takePendingSource())
