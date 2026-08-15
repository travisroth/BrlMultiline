# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for reversing the panning keys per physical display.

Reversal is a fact about the keys, not about what is on the display: on a Focus 80 the left
hand key is the comfortable one for moving forward, on a Monarch it is not. So when several
displays are driven as one, the setting that applies is the one belonging to the display
whose key was pressed — even when the segment that scrolls is on the other display.
"""

import types
import unittest

from ._stubs import (
	BAND_CONFIG,
	BrailleDisplayGesture,
	CONFIG,
	fakeVirtualDisplay,
	installStubs,
	resetConfig,
	setBandConfig,
)

installStubs()

import braille  # noqa: E402
import inputCore  # noqa: E402

from brlMultiline import panning  # noqa: E402

MONARCH_KEY = "hidBrailleStandard_8x32"
FOCUS_KEY = "freedomScientific_1x80"


class StubGesture(BrailleDisplayGesture):
	"""A braille gesture, as far as anything here is concerned: it names its display.

	A real subclass of the shared base, because that is what `_noteGestureSource` tests with
	`isinstance` — a duck typed one would be ignored and every test below would pass vacuously.
	"""

	def __init__(self, source):
		self.source = source


class PanningTestCase(unittest.TestCase):
	def setUp(self):
		resetConfig()
		panning.remove()
		self.handler = types.SimpleNamespace(
			display=fakeVirtualDisplay(
				("hidBrailleStandard", 0, 8, 32),
				("freedomScientific", 8, 1, 80),
			),
		)
		braille.handler = self.handler
		panning.install()

	def tearDown(self):
		panning.remove()
		braille.handler = None
		resetConfig()

	def press(self, source):
		"""Dispatch a braille gesture, as a driver's read loop does."""
		inputCore.decide_executeGesture.decide(gesture=StubGesture(source))


class TestPerDisplayReversal(PanningTestCase):
	def setUp(self):
		super().setUp()
		setBandConfig(MONARCH_KEY, reverseScrollBtns=True)
		setBandConfig(FOCUS_KEY, reverseScrollBtns=False)

	def test_theDisplayWhoseKeyWasPressedDecides(self):
		self.press("hidBrailleStandard")
		self.assertTrue(panning.shouldReverse())
		self.press("freedomScientific")
		self.assertFalse(panning.shouldReverse())

	def test_theSegmentBeingScrolledDoesNotDecide(self):
		"""Pressing the Monarch's key still uses the Monarch's setting.

		Which display holds the focus segment is not consulted, deliberately: the keys under
		the reader's fingers are the Monarch's whatever is being scrolled.
		"""
		CONFIG["focusSegment"] = -1
		self.press("hidBrailleStandard")
		self.assertTrue(panning.shouldReverse())

	def test_theDisplayKeyIsResolvedFromTheDeviceMap(self):
		self.press("freedomScientific")
		self.assertEqual(panning.displayKeyForLastGesture(), FOCUS_KEY)


class TestFallbacks(PanningTestCase):
	def test_noGestureYetFallsBackToTheConnectedDisplay(self):
		CONFIG["reverseScrollBtns"] = True
		self.assertIsNone(panning.displayKeyForLastGesture())
		self.assertTrue(panning.shouldReverse())

	def test_aDisplayThatIsNotAMemberFallsBackToo(self):
		CONFIG["reverseScrollBtns"] = True
		self.press("someOtherDriver")
		self.assertIsNone(panning.displayKeyForLastGesture())
		self.assertTrue(panning.shouldReverse())

	def test_anOrdinaryDisplayIsUnaffected(self):
		"""One display, so there is nothing to resolve and the old behaviour stands."""
		self.handler.display = types.SimpleNamespace(name="freedomScientific")
		CONFIG["reverseScrollBtns"] = True
		self.press("freedomScientific")
		self.assertIsNone(panning.displayKeyForLastGesture())
		self.assertTrue(panning.shouldReverse())

	def test_somethingThatIsNotABrailleGestureIsIgnored(self):
		self.press("hidBrailleStandard")
		inputCore.decide_executeGesture.decide(gesture=object())
		self.assertEqual(panning.displayKeyForLastGesture(), MONARCH_KEY)


class TestLifetime(PanningTestCase):
	def test_installingTwiceRegistersOnce(self):
		before = len(inputCore.decide_executeGesture.handlers)
		panning.install()
		self.assertEqual(len(inputCore.decide_executeGesture.handlers), before)

	def test_removingUnregisters(self):
		panning.remove()
		self.assertEqual(inputCore.decide_executeGesture.handlers, [])

	def test_removingForgetsTheDisplay(self):
		"""Nothing should carry over into the next session's first key press."""
		self.press("hidBrailleStandard")
		panning.remove()
		self.assertIsNone(panning.displayKeyForLastGesture())

	def test_theDeciderNeverCancelsAGesture(self):
		self.assertTrue(inputCore.decide_executeGesture.decide(gesture=StubGesture("anything")))

	def test_theDeciderSurvivesSomethingUnreadable(self):
		broken = types.SimpleNamespace()
		self.assertTrue(panning._noteGestureSource(gesture=broken))


class TestStubIsolation(unittest.TestCase):
	"""The band overrides have to be cleared, or one test's reversal is the next's."""

	def test_resettingClearsBandOverrides(self):
		setBandConfig(MONARCH_KEY, reverseScrollBtns=True)
		resetConfig()
		self.assertEqual(BAND_CONFIG, {})
