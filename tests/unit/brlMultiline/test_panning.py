# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for panning keys belonging to the display they are on.

Two rules, both found on hardware. A display's panning keys drive that display's own segment
rather than whatever holds the focus. And which of the two keys means forward is the pressed
display's setting, because that is a fact about how the keys sit on a piece of hardware.

The narrow part is knowing which display was pressed at all, since the method that scrolls
takes no gesture. The display is read from inside NVDA's own panning command, which does have
one — and the tests that matter most here are the ones about gestures that never reach a
command, or reach one out of order, because an earlier version of this recorded the display
when the gesture passed the input deciders and could be wrong in four different ways.
"""

import types
import unittest

from ._stubs import (
	CONFIG,
	BrailleDisplayGesture,
	fakeVirtualDisplay,
	installStubs,
	log,
	resetConfig,
	setBandConfig,
)

installStubs()

import braille  # noqa: E402
import globalCommands  # noqa: E402
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


class StubGesture(BrailleDisplayGesture):
	"""A braille gesture: it names its display and resolves what it is bound to.

	A real subclass of the shared base rather than a duck type, and its script is looked up on
	each read rather than held. Both matter: NVDA resolves a gesture's script by `getattr` on
	the object holding it, which is the only reason a replaced command is reachable at all. A
	gesture that captured the function at construction would run NVDA's own command and none
	of this would be exercised.
	"""

	def __init__(self, source, scriptName="script_braille_scrollForward"):
		self.source = source
		self.scriptName = scriptName

	@property
	def script(self):
		if self.scriptName is None:
			return None
		return getattr(globalCommands.commands, self.scriptName)


class RecordingHandler:
	"""The handler NVDA's panning commands end up in, recording who asked.

	The display is read here, from inside the command, which is the whole point: this stands
	where `patches._nativeScroll` stands.
	"""

	def __init__(self):
		self.scrolls = []

	def scrollForward(self):
		self.scrolls.append(("forward", panning.sourceForNativeScroll()))

	def scrollBack(self):
		self.scrolls.append(("back", panning.sourceForNativeScroll()))


def fakeContainer(focusSegmentNumber, rects=None):
	"""The two things `nativeSegmentForSource` asks of a container."""
	return types.SimpleNamespace(
		rects=list(COMPOSITE_RECTS if rects is None else rects),
		focusSegmentNumber=focusSegmentNumber,
	)


class PanningTestCase(unittest.TestCase):
	def setUp(self):
		resetConfig()
		log.messages.clear()
		panning.remove()
		self.handler = RecordingHandler()
		self.handler.display = fakeVirtualDisplay(
			(MONARCH, 0, 8, 32),
			(FOCUS, 8, 1, 80),
		)
		braille.handler = self.handler
		inputCore.manager.captureFunc = None
		inputCore.manager.queue = []
		panning.install()

	def tearDown(self):
		panning.remove()
		braille.handler = None
		inputCore.manager.captureFunc = None
		inputCore.manager.queue = []
		resetConfig()

	def press(self, source, scriptName="script_braille_scrollForward"):
		"""Dispatch a gesture, as a driver's read loop does, without running its script yet."""
		inputCore.manager.executeGesture(StubGesture(source, scriptName))

	def pan(self, source, scriptName="script_braille_scrollForward"):
		"""Press a key and let the queued command run, which is one whole key press."""
		self.press(source, scriptName)
		inputCore.manager.runQueued()

	def sources(self):
		""":return: the display each scroll saw, in the order the scrolls happened."""
		return [source for _direction, source in self.handler.scrolls]


class TestWhichDisplayTheScrollSees(PanningTestCase):
	def test_aPanningKeyIsSeenByTheScrollItCauses(self):
		self.pan(MONARCH)
		self.assertEqual(self.sources(), [MONARCH])

	def test_eitherDirectionIsSeen(self):
		self.pan(FOCUS, "script_braille_scrollBack")
		self.assertEqual(self.handler.scrolls, [("back", FOCUS)])

	def test_theDisplayIsNotStillThereAfterwards(self):
		"""It lasts exactly as long as the command, so nothing later can pick it up."""
		self.pan(MONARCH)
		self.assertIsNone(panning.sourceForNativeScroll())

	def test_aScrollWithNoCommandBehindItSeesNoDisplay(self):
		"""Automatic scroll, which must keep NVDA's own behaviour."""
		self.handler.scrollForward()
		self.assertEqual(self.sources(), [None])


class TestGesturesThatNeverReachTheirCommand(PanningTestCase):
	"""Every way a gesture can be abandoned after the input deciders have seen it.

	Each of these left a display recorded that no scroll would ever collect, for the next
	scroll with no key press behind it — automatic scroll, most likely — to act on instead.
	Reading the display from inside the command means an abandoned gesture leaves nothing.
	"""

	def test_inputHelpDescribingAPanningKeyLeavesNothingBehind(self):
		"""Input help's captor runs after the deciders and returns before the script."""
		inputCore.manager.captureFunc = lambda gesture: False
		self.press(MONARCH)
		self.assertEqual(inputCore.manager.runQueued(), 0)
		self.assertIsNone(panning.sourceForNativeScroll())
		self.handler.scrollForward()
		self.assertEqual(self.sources(), [None])

	def test_aGestureCancelledByADeciderLeavesNothingBehind(self):
		"""NVDA Remote registers on this extension point and cancels what it forwards."""

		def cancel(gesture=None, **kwargs):
			return False

		inputCore.decide_executeGesture.register(cancel)
		try:
			self.pan(MONARCH)
		finally:
			inputCore.decide_executeGesture.unregister(cancel)
		self.assertEqual(self.sources(), [])
		self.handler.scrollForward()
		self.assertEqual(self.sources(), [None])

	def test_anUnboundKeyLeavesNothingBehind(self):
		self.pan(MONARCH, None)
		self.assertEqual(self.sources(), [])
		self.assertIsNone(panning.sourceForNativeScroll())

	def test_aRoutingKeyLeavesNothingBehind(self):
		"""The filter that was there before, now free: only these two commands set anything."""
		self.pan(MONARCH, "script_braille_routeTo")
		self.assertEqual(self.sources(), [])
		self.assertIsNone(panning.sourceForNativeScroll())


class TestTwoDisplaysAtOnce(PanningTestCase):
	"""The race the earlier design could lose.

	A gesture is decided on the thread its driver dispatched from and its script is only
	*queued*, so a pan on each of two displays can both be decided before either script runs.
	Whatever holds the display has to be attached to the command that runs, not to the gesture
	that passed.
	"""

	def test_eachCommandSeesTheDisplayThatCausedIt(self):
		self.press(MONARCH)
		self.press(FOCUS)
		inputCore.manager.runQueued()
		self.assertEqual(self.sources(), [MONARCH, FOCUS])

	def test_theOrderOfTheQueueIsTheOrderOfThePresses(self):
		self.press(FOCUS)
		self.press(MONARCH)
		inputCore.manager.runQueued()
		self.assertEqual(self.sources(), [FOCUS, MONARCH])

	def test_repeatedPressesOnOneDisplayAllSeeIt(self):
		for _each in range(4):
			self.press(MONARCH)
		inputCore.manager.runQueued()
		self.assertEqual(self.sources(), [MONARCH] * 4)

	def test_aDisplayAndThenAnAutomaticScroll(self):
		"""The failure this replaces: the leftover display steering a scroll of its own."""
		self.pan(MONARCH)
		self.handler.scrollForward()
		self.assertEqual(self.sources(), [MONARCH, None])


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
		"""The per segment commands never touch the global; they have the gesture."""
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
	def test_thePanningCommandsAreReplaced(self):
		for name in panning.NATIVE_SCROLL_SCRIPTS:
			self.assertIsNot(getattr(globalCommands.GlobalCommands, name), panning._originals[name])

	def test_installingTwiceWrapsOnce(self):
		"""A second wrapping would be harmless but would never be unwound."""
		wrapped = {
			name: getattr(globalCommands.GlobalCommands, name) for name in panning.NATIVE_SCROLL_SCRIPTS
		}
		panning.install()
		for name, command in wrapped.items():
			self.assertIs(getattr(globalCommands.GlobalCommands, name), command)

	def test_removingPutsNVDAsOwnCommandsBack(self):
		originals = dict(panning._originals)
		panning.remove()
		for name, original in originals.items():
			self.assertIs(getattr(globalCommands.GlobalCommands, name), original)

	def test_removingTwiceIsHarmless(self):
		panning.remove()
		panning.remove()
		self.assertFalse(panning._originals)

	def test_aRemovedWrapperFollowsNoDisplay(self):
		panning.remove()
		self.pan(MONARCH)
		self.assertEqual(self.sources(), [None])

	def test_theCommandsStillLookLikeNVDAsOwn(self):
		"""Input help and the Input Gestures dialog read a script's name and documentation."""
		for name in panning.NATIVE_SCROLL_SCRIPTS:
			wrapper = getattr(globalCommands.GlobalCommands, name)
			original = panning._originals[name]
			self.assertEqual(wrapper.__name__, name)
			self.assertEqual(wrapper.__doc__, original.__doc__)

	def test_installingWithoutTheCommandsIsReportedAndHarmless(self):
		"""Braille must go on working, panning the focus segment as NVDA always did."""
		panning.remove()
		missing = type("Empty", (), {})
		original = globalCommands.GlobalCommands
		globalCommands.GlobalCommands = missing
		try:
			panning.install()
			self.assertFalse(panning._installed)
		finally:
			globalCommands.GlobalCommands = original
		self.assertTrue(any(level == "error" for level, _message in log.messages), log.messages)
		self.handler.scrollForward()
		self.assertEqual(self.sources(), [None])
