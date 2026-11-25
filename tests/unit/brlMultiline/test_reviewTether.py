# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the flow band standing down while braille is tethered to review.

Phase 1 of docs/design/review-flow-plan.md: the flow reads only by the caret, so under a review
tether the band leaves the display to NVDA's own review presentation, and comes back on the
tether back to focus.
"""

import unittest
from unittest import mock

from ._stubs import FakeHandler, FakeNavigatorObject, FakeTreeInterceptor, installStubs

installStubs()

from brlMultiline import flowCursor, patches  # noqa: E402

from .test_flowSegment import COLS, ROWS, FakePlugin, containerWithBand, documentLines  # noqa: E402


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


class TestStandingDownForReview(unittest.TestCase):
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
		self.addCleanup(setattr, api, "getFocusObject", api.getFocusObject)
		self.api = api
		interceptor = FakeTreeInterceptor(documentLines())
		self.page = FakeNavigatorObject("a page", treeInterceptor=interceptor)
		self.api.getFocusObject = lambda: self.page

	def _start(self) -> bool:
		self.band._follow()
		return self.band.refresh(force=True)

	def _focusRegions(self):
		from ._stubs import fakeGetFocusRegions
		from brlMultiline.objectMonitor import resolveTarget

		return fakeGetFocusRegions(resolveTarget(self.page))

	def _setTether(self, tether, auto=True):
		"""Change the tether through the add-on's patch, over a stand-in for NVDA's own."""

		def nvdas(handler, tether, auto=False):
			if tether != handler.tether:
				handler.tether = tether
				handler.mainBuffer.clear()

		with (
			mock.patch.dict(patches._originals, {"setTether": nvdas}),
			mock.patch("brlMultiline.getPlugin", return_value=self.plugin, create=True),
		):
			self.plugin.flowBand = self.band
			patches._setTetherStandingDownForReview(self.handler, tether, auto)

	def test_aBandStartedUnderAReviewTetherFlowsNothing(self):
		self.handler.tether = "review"
		self.assertFalse(self._start())
		self.assertIsNone(self.band.controller)
		self.assertFalse(self.segment.isFlowing)

	def test_focusRegionsUnderAReviewTetherAreHandedBackToNVDA(self):
		# NVDA's review move to another object builds review regions and offers them to the
		# band; refusing them is what lets NVDA place them in the band as an ordinary segment.
		self.assertTrue(self._start())
		self.handler.tether = "review"
		self.assertFalse(self.segment.acceptFocusRegions(self._focusRegions()))
		self.assertIsNone(self.band.controller)
		self.assertFalse(self.segment.isFlowing)

	def test_theTetherGoingToReviewEmptiesTheBand(self):
		# NVDA's clear puts the flow's active block straight back, so without standing down here
		# the review move that follows would find the flow's block and build no review regions.
		self.assertTrue(self._start())
		self._setTether("review")
		self.assertIsNone(self.band.controller)
		self.assertFalse(self.segment.isFlowing)
		self.assertEqual(self.segment.regions, [])

	def test_theBandKeepsItsClaimWhileStandingDown(self):
		self.assertTrue(self._start())
		self._setTether("review")
		self.assertTrue(self.band.isClaimed)

	def test_theTetherBackToFocusFlowsAgain(self):
		self.assertTrue(self._start())
		self._setTether("review")
		self._setTether("focus")
		# NVDA follows the tether back to focus with the focus or caret regions it builds then.
		self.assertTrue(self.segment.acceptFocusRegions(self._focusRegions()))
		self.assertTrue(self.segment.isFlowing)

	def test_aTetherThatStaysOnFocusLeavesTheFlow(self):
		self.assertTrue(self._start())
		control = self.band.controller
		self._setTether("focus")
		self.assertIs(self.band.controller, control)
		self.assertTrue(self.segment.isFlowing)

	def test_aReviewTetherSetAgainLeavesTheBandAlone(self):
		# Only a change stands the band down: a review command sets an automatic tether to review
		# on every press, and the band must not be detached from what NVDA has placed in it.
		self.handler.tether = "review"
		self.band.standDownForReview = mock.Mock()
		self._setTether("review")
		self.band.standDownForReview.assert_not_called()

	def test_aFailureStandingDownStillChangesTheTether(self):
		self.assertTrue(self._start())
		self.band.standDownForReview = mock.Mock(side_effect=RuntimeError("broken"))
		self._setTether("review")
		self.assertEqual(self.handler.tether, "review")


if __name__ == "__main__":
	unittest.main()
