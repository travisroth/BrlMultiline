# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for holding the reader's place in a terminal against output while they review it.

Phase 4 of docs/design/review-flow-plan.md: a terminal moves its caret for every line of output,
and NVDA answers each by taking an automatic tether back to focus and moving the review cursor to
the caret. While the reader is reviewing, the hold stops both, until they press something that is
not reading.
"""

import sys
import types
import unittest
from unittest import mock

from ._stubs import installStubs

installStubs()

from brlMultiline import patches, reviewHold  # noqa: E402

from .test_reviewTether import BandUnderReview, reviewMode  # noqa: E402


def gestureFor(scriptName):
	"""A gesture whose command is the script of that name, or None for a key with no command."""
	if scriptName is None:
		return types.SimpleNamespace(script=None)

	def script(gesture):
		pass

	script.__name__ = scriptName
	return types.SimpleNamespace(script=script)


class HoldCleared(unittest.TestCase):
	def setUp(self):
		reviewHold.end()
		self.addCleanup(reviewHold.end)


class TestWhatKeepsTheHold(HoldCleared):
	def test_reviewCommandsKeepIt(self):
		for name in ("script_review_previousLine", "script_review_currentLine", "script_review_nextWord"):
			self.assertTrue(reviewHold.keepsTheHold(gestureFor(name)), name)

	def test_brailleCommandsKeepIt(self):
		for name in ("script_braille_scrollForward", "script_braille_scrollBack", "script_braille_routeTo"):
			self.assertTrue(reviewHold.keepsTheHold(gestureFor(name)), name)

	def test_objectNavigationKeepsIt(self):
		self.assertTrue(reviewHold.keepsTheHold(gestureFor("script_navigatorObject_parent")))

	def test_theAddOnsOwnScrollingKeepsIt(self):
		for name in ("script_scrollSegment0Forward", "script_scrollThisDisplaySegment1Back", "script_flowPanLinesRight"):
			self.assertTrue(reviewHold.keepsTheHold(gestureFor(name)), name)

	def test_typingEndsIt(self):
		# A key with no command passes through to the terminal: the reader is writing again.
		self.assertFalse(reviewHold.keepsTheHold(gestureFor(None)))

	def test_otherCommandsEndIt(self):
		self.assertFalse(reviewHold.keepsTheHold(gestureFor("script_dateTime")))

	def test_brailleToFocusEndsIt(self):
		self.assertFalse(reviewHold.keepsTheHold(gestureFor("script_braille_toFocus")))

	def test_typingOnABrailleKeyboardEndsIt(self):
		for name in ("script_braille_dots", "script_braille_enter", "script_braille_eraseLastCell"):
			self.assertFalse(reviewHold.keepsTheHold(gestureFor(name)), name)

	def test_aGestureWhoseCommandCannotBeFoundEndsIt(self):
		class Broken:
			@property
			def script(self):
				raise RuntimeError("no script")

		self.assertFalse(reviewHold.keepsTheHold(Broken()))


class TestStartingAndEndingTheHold(HoldCleared):
	def setUp(self):
		super().setUp()
		self.terminal = object()
		patches_ = [
			mock.patch.object(reviewHold.flowCursor, "reviewObject", return_value=self.terminal),
			mock.patch.object(reviewHold, "isTerminal", side_effect=lambda obj: obj is self.terminal),
			mock.patch.object(reviewHold, "_isFlowing", return_value=True),
		]
		for patch in patches_:
			patch.start()
			self.addCleanup(patch.stop)

	def test_aReviewMoveInATerminalTheBandShowsHolds(self):
		reviewHold.noteReaderReviewMove()
		self.assertTrue(reviewHold.isHolding(self.terminal))

	def test_aReviewMoveElsewhereDoesNotHold(self):
		reviewHold.noteReaderReviewMove()
		with mock.patch.object(reviewHold.flowCursor, "reviewObject", return_value=object()):
			reviewHold.noteReaderReviewMove()
		self.assertIsNone(reviewHold.held())

	def test_aTerminalTheBandIsNotShowingIsNotHeld(self):
		with mock.patch.object(reviewHold, "_isFlowing", return_value=False):
			reviewHold.noteReaderReviewMove()
		self.assertIsNone(reviewHold.held())

	def test_theHoldIsOnlyOverItsTerminal(self):
		reviewHold.noteReaderReviewMove()
		self.assertFalse(reviewHold.isHolding(object()))
		self.assertFalse(reviewHold.isHolding(None))

	def test_typingEndsTheHold(self):
		reviewHold.noteReaderReviewMove()
		self.assertTrue(reviewHold.decideExecuteGesture(gestureFor(None)))
		self.assertIsNone(reviewHold.held())

	def test_aReviewKeyKeepsTheHold(self):
		reviewHold.noteReaderReviewMove()
		self.assertTrue(reviewHold.decideExecuteGesture(gestureFor("script_review_previousLine")))
		self.assertTrue(reviewHold.isHolding(self.terminal))

	def test_theDeciderNeverStopsAGesture(self):
		reviewHold.noteReaderReviewMove()
		with mock.patch.object(reviewHold, "keepsTheHold", side_effect=RuntimeError("broken")):
			self.assertTrue(reviewHold.decideExecuteGesture(gestureFor(None)))

	def test_onlyTheReadersReviewMovesAreNoted(self):
		# NVDA's own review moves, the review cursor following the caret among them, come without
		# an automatic tether allowed.
		with (
			mock.patch.dict(patches._originals, {"handleReviewMove": lambda handler, auto: None}),
			mock.patch.object(reviewHold, "noteReaderReviewMove") as noted,
		):
			patches._handleReviewMoveNotingTheReader(object(), shouldAutoTether=False)
			noted.assert_not_called()
			patches._handleReviewMoveNotingTheReader(object(), shouldAutoTether=True)
			noted.assert_called_once()


class TestHearingGestures(HoldCleared):
	def test_installRegistersWithNVDAsDecider(self):
		decider = mock.Mock()
		with mock.patch.dict(sys.modules, {"inputCore": types.SimpleNamespace(decide_executeGesture=decider)}):
			reviewHold.install()
			decider.register.assert_called_once_with(reviewHold.decideExecuteGesture)
			reviewHold.remove()
			decider.unregister.assert_called_once_with(reviewHold.decideExecuteGesture)


class TestHoldingAgainstOutput(BandUnderReview):
	"""A band reviewing a terminal while its output moves the caret down the page."""

	def setUp(self):
		super().setUp()
		reviewHold.end()
		self.addCleanup(reviewHold.end)
		patch = reviewMode("document")
		patch.start()
		self.addCleanup(patch.stop)
		for patch in (
			mock.patch.object(reviewHold, "isTerminal", return_value=True),
			mock.patch("brlMultiline.getPlugin", return_value=self.plugin, create=True),
		):
			patch.start()
			self.addCleanup(patch.stop)
		self.assertTrue(self._start())
		self._reviewMove(20)
		reviewHold.noteReaderReviewMove()
		self.assertTrue(reviewHold.isHolding(self.interceptor))

	def _nvdasCaretMove(self, handler, obj, shouldAutoTether=True):
		"""NVDA's `handleCaretMove`, transcribed: an automatic tether goes back to focus first."""
		if shouldAutoTether:
			self._setTether("focus")
		if handler.tether != "focus":
			return
		handler.handleCaretMove(obj)
		handler._handlePendingUpdate()

	def _nvdasReviewFollow(self, pos):
		"""NVDA's `review.handleCaretMove`, as far as it matters here: the review cursor goes to the caret."""
		self._reviewAt(pos.index)

	def _output(self, line: int) -> None:
		"""A line of output: the caret moves, and NVDA answers it twice."""
		self.interceptor.caretIndex = line
		caret = self.interceptor._position(line)
		caret.obj = self.interceptor
		with mock.patch.dict(
			patches._originals,
			{"handleCaretMove": self._nvdasCaretMove, "reviewHandleCaretMove": self._nvdasReviewFollow},
		):
			patches._handleCaretMoveHoldingReview(self.handler, self.interceptor)
			patches._reviewCaretMoveHoldingReview(caret)

	def test_outputKeepsTheTetherOnReview(self):
		self._output(38)
		self.assertEqual(self.handler.tether, "review")

	def test_outputLeavesTheReviewCursorWhereTheReaderWas(self):
		self._output(38)
		self.assertEqual(self.api.getReviewPosition().index, 20)

	def test_outputLeavesTheBandOnTheReadersLine(self):
		for line in (37, 38, 39):
			self._output(line)
		self.assertEqual(self._activeText(), "line 20")

	def test_theNextReviewKeyCarriesOnFromTheReadersPlace(self):
		self._output(38)
		self._reviewMove(19)
		self.assertEqual(self._activeText(), "line 19")

	def test_typingGivesTheCaretBack(self):
		reviewHold.decideExecuteGesture(gestureFor(None))
		self._output(38)
		self.assertEqual(self.handler.tether, "focus")
		self.assertEqual(self.api.getReviewPosition().index, 38)
		self.assertEqual(self._activeText(), "line 38")

	def test_aManualTetherToFocusEndsTheHold(self):
		self._setTether("focus", auto=False)
		self.assertIsNone(reviewHold.held())


if __name__ == "__main__":
	unittest.main()
