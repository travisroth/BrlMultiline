# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the flow band under a review tether.

Phases 1 and 2 of docs/design/review-flow-plan.md: while braille is tethered to review the band
reads at the review cursor rather than the caret, and stands down, leaving NVDA to present review,
wherever a flow cannot read it.
"""

import sys
import types
import unittest
from unittest import mock

from ._stubs import FakeHandler, FakeNavigatorObject, FakeTreeInterceptor, installStubs

installStubs()

from brlMultiline import flowCursor, patches  # noqa: E402
from brlMultiline.flowControl import FlowController  # noqa: E402
from brlMultiline.flowSources import DocumentFlowSource  # noqa: E402

from .test_flowSegment import COLS, ROWS, FakePlugin, containerWithBand, documentLines  # noqa: E402


def reviewMode(mode: str):
	""":return: a patch putting NVDA's review module in a mode, for `flowCursor.canFlowReview`."""
	return mock.patch.dict(sys.modules, {"review": types.SimpleNamespace(getCurrentMode=lambda: mode)})


class TestIsReviewing(unittest.TestCase):
	def test_reviewTetherIsReviewing(self):
		handler = FakeHandler(ROWS, COLS)
		handler.tether = "review"
		self.assertTrue(flowCursor.isReviewing(handler))

	def test_focusTetherIsNot(self):
		self.assertFalse(flowCursor.isReviewing(FakeHandler(ROWS, COLS)))

	def test_noHandlerIsNot(self):
		self.assertFalse(flowCursor.isReviewing(None))

	def test_aHandlerThatCannotSayIsNot(self):
		# Asked on every redraw, so a failure must read as the flow carrying on, not as a
		# display left to NVDA for no reason.
		handler = mock.Mock()
		handler.getTether.side_effect = RuntimeError("no tether")
		self.assertFalse(flowCursor.isReviewing(handler))


class BandUnderReview(unittest.TestCase):
	"""A band flowing a 40 line page with its caret on the first line."""

	def setUp(self):
		import api
		import braille
		from brlMultiline.flowBand import FlowBand

		from ._stubs import CONFIG, resetConfig

		resetConfig()
		self.addCleanup(resetConfig)
		CONFIG["flowEnabled"] = True
		self.handler = FakeHandler(ROWS, COLS)
		self.addCleanup(setattr, braille, "handler", braille.handler)
		braille.handler = self.handler
		self.container = containerWithBand(self.handler)
		self.handler.mainBuffer = self.handler.buffer = self.container
		self.segment = self.container.segmentForKey("flow")
		self.plugin = FakePlugin(self.container)
		self.band = FlowBand(self.plugin)
		self.plugin.flowBand = self.band
		self.api = api
		self.addCleanup(setattr, api, "getFocusObject", api.getFocusObject)
		self.addCleanup(setattr, api, "getReviewPosition", getattr(api, "getReviewPosition", None))
		self.interceptor = FakeTreeInterceptor(documentLines())
		self.page = FakeNavigatorObject("a page", treeInterceptor=self.interceptor)
		self.api.getFocusObject = lambda: self.page
		self._reviewAt(0)

	def _start(self) -> bool:
		self.band._follow()
		return self.band.refresh(force=True)

	def _reviewAt(self, index: int):
		position = self.interceptor._position(index)
		position.obj = self.interceptor
		self.api.getReviewPosition = lambda: position
		return position

	def _focusRegions(self):
		from ._stubs import fakeGetFocusRegions
		from brlMultiline.objectMonitor import resolveTarget

		return fakeGetFocusRegions(resolveTarget(self.page))

	def _setTether(self, tether, auto=True):
		"""Change the tether through the add-on's patch, over the stand-in for NVDA's own."""
		with (
			mock.patch.dict(patches._originals, {"setTether": type(self.handler).setTether}),
			mock.patch("brlMultiline.getPlugin", return_value=self.plugin, create=True),
		):
			patches._setTetherTellingTheBand(self.handler, tether, auto)

	def _reviewMove(self, index: int) -> None:
		"""Move the review cursor as a review key does, through NVDA's `handleReviewMove`.

		Transcribed from NVDA's: tether automatically, then update the last region if it is over
		the review position's object, as it is when the band is flowing that object.
		"""
		self._reviewAt(index)
		self._setTether("review")
		regions = self.container.regions
		region = regions[-1] if regions else None
		if region is not None and getattr(region, "obj", None) == self.interceptor:
			region.pendingCaretUpdate = True
			self.handler._regionsPendingUpdate.add(region)
			self.handler._handlePendingUpdate()
		else:
			self.segment.acceptFocusRegions(self._focusRegions())

	def _activeText(self) -> str:
		return self.band.controller.activeRegion().rawText


class TestStandingDownWhereReviewCannotFlow(BandUnderReview):
	"""Screen review, where the review cursor is the screen's text, is NVDA's to present."""

	def setUp(self):
		super().setUp()
		patch = reviewMode("screen")
		patch.start()
		self.addCleanup(patch.stop)

	def test_aBandStartedInScreenReviewFlowsNothing(self):
		self.handler.tether = "review"
		self.assertFalse(self._start())
		self.assertIsNone(self.band.controller)
		self.assertFalse(self.segment.isFlowing)

	def test_reviewRegionsInScreenReviewAreHandedBackToNVDA(self):
		self.assertTrue(self._start())
		self.handler.tether = "review"
		self.assertFalse(self.segment.acceptFocusRegions(self._focusRegions()))
		self.assertFalse(self.segment.isFlowing)

	def test_theTetherGoingToScreenReviewEmptiesTheBand(self):
		# NVDA's clear puts the flow's active block straight back, so without standing down here
		# the review move that follows would find the flow's block and build no review regions.
		self.assertTrue(self._start())
		self._setTether("review")
		self.assertIsNone(self.band.controller)
		self.assertEqual(self.segment.regions, [])
		self.assertTrue(self.band.isClaimed)

	def test_theTetherBackToFocusFlowsAgain(self):
		self.assertTrue(self._start())
		self._setTether("review")
		self._setTether("focus")
		self.assertTrue(self.segment.acceptFocusRegions(self._focusRegions()))
		self.assertTrue(self.segment.isFlowing)

	def test_withNoReviewPositionTheBandStandsDown(self):
		self.api.getReviewPosition = lambda: None
		self.handler.tether = "review"
		with reviewMode("document"):
			self.assertFalse(self._start())


class TestFollowingTheReviewCursor(BandUnderReview):
	def setUp(self):
		super().setUp()
		patch = reviewMode("document")
		patch.start()
		self.addCleanup(patch.stop)

	def test_aReviewMoveBringsTheBandToTheReviewCursor(self):
		self.assertTrue(self._start())
		self.assertEqual(self._activeText(), "line 0")
		self._reviewMove(20)
		self.assertTrue(self.segment.isFlowing)
		self.assertEqual(self._activeText(), "line 20")
		# The caret has not moved: the band followed the review cursor, not the caret.
		self.assertEqual(self.interceptor.caretIndex, 0)

	def test_successiveReviewMovesAreFollowed(self):
		self.assertTrue(self._start())
		for index in (20, 21, 19):
			self._reviewMove(index)
			self.assertEqual(self._activeText(), f"line {index}")

	def test_theBandKeepsItsReadingWhenTheTetherGoesToReview(self):
		# The same document, so nothing need be read again: the review move that follows finds the
		# flow's own block and the band follows it.
		self.assertTrue(self._start())
		control = self.band.controller
		self._setTether("review")
		self.assertIs(self.band.controller, control)
		self.assertTrue(self.segment.isFlowing)

	def test_aBandStartedUnderAReviewTetherReadsAtTheReviewCursor(self):
		self._reviewAt(25)
		self.handler.tether = "review"
		self.assertTrue(self._start())
		self.assertEqual(self._activeText(), "line 25")

	def test_panningUnderAReviewTetherLeavesTheCaret(self):
		# Moving the caret would take an automatic tether straight back to focus.
		self.assertTrue(self._start())
		self._reviewMove(20)
		self.segment.scrollForward()
		self.assertEqual(self.interceptor.caretIndex, 0)

	def test_theTetherBackToFocusFollowsTheCaretAgain(self):
		self.assertTrue(self._start())
		self._reviewMove(20)
		self._setTether("focus")
		self.handler.handleCaretMove(self.interceptor)
		self.handler._handlePendingUpdate()
		self.assertEqual(self._activeText(), "line 0")


class TestTablesUnderReview(BandUnderReview):
	"""A table in columns follows the caret only, so a change of tether rebuilds the band."""

	def setUp(self):
		super().setUp()
		self.assertTrue(self._start())
		self.band.standDown = mock.Mock(wraps=self.band.standDown)

	def test_aTableInColumnsStandsDownWhenTheTetherGoesToReview(self):
		with reviewMode("document"), mock.patch.object(type(self.band), "_readingATable", return_value=True):
			self._setTether("review")
		self.band.standDown.assert_called_once()

	def test_aWantedTableIsTakenUpAgainWhenTheTetherComesBack(self):
		with reviewMode("document"):
			self._setTether("review")
			self.band.standDown.assert_not_called()
			with mock.patch.object(type(self.band), "tableWanted", new_callable=mock.PropertyMock) as wanted:
				wanted.return_value = "a table"
				self._setTether("focus")
		self.band.standDown.assert_called_once()

	def test_aTetherSetAgainChangesNothing(self):
		# A review command sets an automatic tether to review on every press.
		with reviewMode("document"):
			self._setTether("review")
			self._setTether("review")
		self.band.standDown.assert_not_called()

	def test_aFailureInTheBandStillChangesTheTether(self):
		self.band.tetherChanged = mock.Mock(side_effect=RuntimeError("broken"))
		self._setTether("review")
		self.assertEqual(self.handler.tether, "review")


class TestOnlyTheBandFollowsReview(unittest.TestCase):
	def setUp(self):
		import api
		import braille

		self.addCleanup(setattr, braille, "handler", braille.handler)
		braille.handler = FakeHandler(ROWS, COLS)
		braille.handler.tether = "review"
		self.addCleanup(setattr, api, "getReviewPosition", getattr(api, "getReviewPosition", None))
		self.interceptor = FakeTreeInterceptor(documentLines(), caretIndex=3)
		position = self.interceptor._position(30)
		position.obj = self.interceptor
		api.getReviewPosition = lambda: position

	def test_aSourceThatFollowsReviewReadsAtTheReviewCursor(self):
		self.assertEqual(flowCursor.readerPositionIn(self.interceptor, followsReview=True).index, 30)

	def test_aPinReadsAtTheCaret(self):
		self.assertEqual(flowCursor.readerPositionIn(self.interceptor, followsReview=False).index, 3)

	def test_reviewInAnotherObjectReadsAtTheCaret(self):
		other = FakeTreeInterceptor(documentLines(), caretIndex=5)
		self.assertEqual(flowCursor.readerPositionIn(other, followsReview=True).index, 5)

	def test_aSourceFollowingReviewIsNotBeingWrittenIn(self):
		# The re-read for typing keeps the band where it is, and the band must move to the review
		# cursor, so following review is reading, whatever the document says.
		source = DocumentFlowSource(self.interceptor, mock.Mock(), followsReview=True)
		source.writing = True
		control = FlowController.__new__(FlowController)
		control.live = True
		control.source = source
		self.assertFalse(control._writing())
		source.followsReview = False
		self.assertTrue(control._writing())


if __name__ == "__main__":
	unittest.main()
