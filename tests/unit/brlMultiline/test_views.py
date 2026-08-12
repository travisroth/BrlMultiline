# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for views: whole display arrangements, and composing them from panels.

The scenario driving most of these is a pinned object in the top row while a table claims
the rows below it. The point of composition is that the claim takes only what it asks for,
so the pin's segment keeps its key and the pin survives the rebuild.
"""

import unittest

from ._stubs import CONFIG, installStubs, resetConfig

installStubs()

from brlMultiline.layout import SegmentRect  # noqa: E402
from brlMultiline.panels import GridPanel, SinglePanel  # noqa: E402
from brlMultiline.views import (  # noqa: E402
	SegmentView,
	displaySegmentKey,
	singleSegmentView,
	viewFromConfig,
)

MONARCH_ROWS = 8
MONARCH_COLS = 32


def keysOf(view):
	return [spec.key for spec in view.flatten()]


def tableClaim(name="table"):
	"""A three by three grid over the bottom six rows of a Monarch."""
	return GridPanel(
		name,
		SegmentRect(row=2, col=0, numRows=6, numCols=MONARCH_COLS),
		rowBands=[2, 2, 2],
		colWidths=[10, 10, 10],
	)


class ViewTestCase(unittest.TestCase):
	def setUp(self):
		resetConfig()

	def tearDown(self):
		resetConfig()


class TestConfiguredView(ViewTestCase):
	def test_onePanelPerSegment(self):
		"""The configured view is the finest grained view there is, so claims can be precise."""
		CONFIG["segmentCount"] = 4
		view = viewFromConfig(MONARCH_ROWS, MONARCH_COLS)
		view.validate(MONARCH_ROWS, MONARCH_COLS)
		self.assertEqual(len(view.panels), 4)
		self.assertEqual(len(view.flatten()), 4)

	def test_keysFollowSegmentNumbers(self):
		CONFIG["segmentCount"] = 8
		view = viewFromConfig(MONARCH_ROWS, MONARCH_COLS)
		self.assertEqual(keysOf(view), [f"display.{index}" for index in range(8)])

	def test_focusDefaultsToTheLastSegment(self):
		CONFIG["segmentCount"] = 4
		self.assertEqual(viewFromConfig(MONARCH_ROWS, MONARCH_COLS).focusSegmentKey, "display.3")

	def test_focusCanBeChosen(self):
		CONFIG["segmentCount"] = 4
		CONFIG["focusSegment"] = 1
		self.assertEqual(viewFromConfig(MONARCH_ROWS, MONARCH_COLS).focusSegmentKey, "display.1")

	def test_impossibleFocusFallsBackToTheLast(self):
		CONFIG["segmentCount"] = 2
		CONFIG["focusSegment"] = 6
		self.assertEqual(viewFromConfig(MONARCH_ROWS, MONARCH_COLS).focusSegmentKey, "display.1")

	def test_layoutThatDoesNotFitFallsBackToOneSegment(self):
		CONFIG["segmentSizes"] = [4, 4, 4]
		view = viewFromConfig(MONARCH_ROWS, MONARCH_COLS)
		view.validate(MONARCH_ROWS, MONARCH_COLS)
		self.assertEqual(len(view.flatten()), 1)
		self.assertEqual(view.name, "configured")

	def test_configuredSegmentsAreFree(self):
		"""Free segments are the ones the document lines feature may fill."""
		CONFIG["segmentCount"] = 4
		self.assertTrue(
			all(not spec.isReserved for spec in viewFromConfig(MONARCH_ROWS, MONARCH_COLS).flatten())
		)

	def test_singleRowDisplayDividesIntoColumns(self):
		CONFIG["segmentCount"] = 2
		view = viewFromConfig(1, 80)
		view.validate(1, 80)
		self.assertEqual(
			[spec.rect for spec in view.flatten()], [SegmentRect(0, 0, 1, 40), SegmentRect(0, 40, 1, 40)]
		)


class TestSegmentsSwitch(ViewTestCase):
	"""The master switch over the configured layout, not over the display.

	It overrides what the user configured without disturbing it, so that a layout can be put
	aside and taken up again. A view activated by code is a different kind of thing and is
	not affected; that is tested where activation lives, in the plugin tests.
	"""

	def test_turningItOffGivesOneSegment(self):
		CONFIG["segmentCount"] = 4
		CONFIG["segmentsEnabled"] = False
		view = viewFromConfig(MONARCH_ROWS, MONARCH_COLS)
		view.validate(MONARCH_ROWS, MONARCH_COLS)
		self.assertEqual(len(view.flatten()), 1)

	def test_theUndividedDisplayReportsItself(self):
		"""So that the layout report says why there is one segment."""
		CONFIG["segmentsEnabled"] = False
		self.assertEqual(viewFromConfig(MONARCH_ROWS, MONARCH_COLS).name, "single")

	def test_theLayoutIsKept(self):
		CONFIG["segmentSizes"] = [1, 5, 2]
		CONFIG["segmentsEnabled"] = False
		viewFromConfig(MONARCH_ROWS, MONARCH_COLS)
		self.assertEqual(CONFIG["segmentSizes"], [1, 5, 2])

	def test_turningItBackOnRestoresTheLayout(self):
		CONFIG["segmentSizes"] = [1, 5, 2]
		CONFIG["segmentsEnabled"] = False
		viewFromConfig(MONARCH_ROWS, MONARCH_COLS)
		CONFIG["segmentsEnabled"] = True
		view = viewFromConfig(MONARCH_ROWS, MONARCH_COLS)
		self.assertEqual([spec.rect.numRows for spec in view.flatten()], [1, 5, 2])

	def test_theSegmentKeepsTheKeyOfSegmentZero(self):
		"""So that switching the layout off and on does not strand a pin in segment 0."""
		CONFIG["segmentsEnabled"] = False
		self.assertEqual(keysOf(viewFromConfig(MONARCH_ROWS, MONARCH_COLS)), [displaySegmentKey(0)])


class TestSingleSegmentView(ViewTestCase):
	def test_oneSegmentCoveringTheDisplay(self):
		view = singleSegmentView(MONARCH_ROWS, MONARCH_COLS)
		view.validate(MONARCH_ROWS, MONARCH_COLS)
		self.assertEqual(len(view.flatten()), 1)
		self.assertEqual(view.flatten()[0].rect, SegmentRect(0, 0, MONARCH_ROWS, MONARCH_COLS))

	def test_keyMatchesAConfiguredViewOfOneSegment(self):
		"""So that a pin survives switching between the two."""
		self.assertEqual(singleSegmentView(MONARCH_ROWS, MONARCH_COLS).focusSegmentKey, displaySegmentKey(0))


class TestFlatten(ViewTestCase):
	def test_sortsIntoDisplayOrder(self):
		"""Segment numbers are display order, whatever order the panels were composed in."""
		view = SegmentView(
			"jumbled",
			[
				SinglePanel("bottom", SegmentRect(4, 0, 4, 32)),
				SinglePanel("top", SegmentRect(0, 0, 4, 32)),
			],
			focusSegmentKey="bottom",
		)
		self.assertEqual(keysOf(view), ["top", "bottom"])

	def test_sortsColumnsWithinARow(self):
		view = SegmentView(
			"columns",
			[
				SinglePanel("right", SegmentRect(0, 16, 8, 16)),
				SinglePanel("left", SegmentRect(0, 0, 8, 16)),
			],
			focusSegmentKey="left",
		)
		self.assertEqual(keysOf(view), ["left", "right"])


class TestValidation(ViewTestCase):
	def test_panelsMustTileTheDisplay(self):
		view = SegmentView("partial", [SinglePanel("a", SegmentRect(0, 0, 4, 32))])
		with self.assertRaises(ValueError) as caught:
			view.validate(MONARCH_ROWS, MONARCH_COLS)
		self.assertIn("no owner", str(caught.exception))

	def test_panelsMustNotOverlap(self):
		view = SegmentView(
			"overlapping",
			[SinglePanel("a", SegmentRect(0, 0, 5, 32)), SinglePanel("b", SegmentRect(4, 0, 4, 32))],
			focusSegmentKey="a",
		)
		with self.assertRaises(ValueError):
			view.validate(MONARCH_ROWS, MONARCH_COLS)

	def test_keysMustBeUnique(self):
		view = SegmentView(
			"duplicated",
			[SinglePanel("a", SegmentRect(0, 0, 4, 32)), SinglePanel("a", SegmentRect(4, 0, 4, 32))],
			focusSegmentKey="a",
		)
		with self.assertRaises(ValueError) as caught:
			view.validate(MONARCH_ROWS, MONARCH_COLS)
		self.assertIn("reuses segment keys", str(caught.exception))

	def test_focusSegmentMustExist(self):
		view = SegmentView("a", [SinglePanel("a", SegmentRect(0, 0, 8, 32))], focusSegmentKey="nope")
		with self.assertRaises(LookupError):
			view.validate(MONARCH_ROWS, MONARCH_COLS)

	def test_aViewOfNothingButBlankPanelsIsRejected(self):
		from brlMultiline.panels import BlankPanel

		view = SegmentView("empty", [BlankPanel(SegmentRect(0, 0, 8, 32))])
		with self.assertRaises(ValueError):
			view.validate(MONARCH_ROWS, MONARCH_COLS)


class TestComposition(ViewTestCase):
	"""The scenario: row 0 holds a pinned object, and a table claims the rows below."""

	def setUp(self):
		super().setUp()
		CONFIG["segmentCount"] = 8
		self.configured = viewFromConfig(MONARCH_ROWS, MONARCH_COLS)
		self.composed = self.configured.withPanel(tableClaim(), MONARCH_ROWS, MONARCH_COLS)
		self.composed.validate(MONARCH_ROWS, MONARCH_COLS)

	def test_untouchedSegmentsKeepTheirKeys(self):
		"""The whole point: the pin in display.0 is still addressable afterwards."""
		self.assertIn("display.0", keysOf(self.composed))
		self.assertIn("display.1", keysOf(self.composed))

	def test_claimedSegmentsAreEvicted(self):
		for index in range(2, 8):
			self.assertNotIn(f"display.{index}", keysOf(self.composed))

	def test_theClaimIsPresent(self):
		self.assertEqual(sum(1 for key in keysOf(self.composed) if key.startswith("table.")), 9)

	def test_focusMovesIntoTheClaim(self):
		"""The claim took the row the focus was in, and offered its first cell instead."""
		self.assertEqual(self.configured.focusSegmentKey, "display.7")
		self.assertEqual(self.composed.focusSegmentKey, "table.r0c0")

	def test_claimedSegmentsAreReserved(self):
		reserved = {spec.key for spec in self.composed.flatten() if spec.isReserved}
		self.assertEqual(reserved, {f"table.r{band}c{column}" for band in range(3) for column in range(3)})

	def test_untouchedSegmentsStayFree(self):
		for spec in self.composed.flatten():
			if spec.key.startswith("display."):
				self.assertFalse(spec.isReserved)

	def test_policyIsPerSegmentNotPerView(self):
		"""The grid fills rows while the segments beside it wrap at words, in one view."""
		byKey = {spec.key: spec for spec in self.composed.flatten()}
		self.assertTrue(byKey["table.r0c0"].fillRows)
		self.assertFalse(byKey["display.0"].fillRows)

	def test_stillTilesTheDisplay(self):
		covered = sum(panel.rect.displaySize for panel in self.composed.panels)
		self.assertEqual(covered, MONARCH_ROWS * MONARCH_COLS)

	def test_theOriginalViewIsUnchanged(self):
		self.assertEqual(len(self.configured.flatten()), 8)
		self.assertEqual(self.configured.focusSegmentKey, "display.7")

	def test_claimsStack(self):
		stacked = self.configured.withPanel(
			SinglePanel("status", SegmentRect(0, 0, 1, MONARCH_COLS)),
			MONARCH_ROWS,
			MONARCH_COLS,
		).withPanel(tableClaim(), MONARCH_ROWS, MONARCH_COLS)
		stacked.validate(MONARCH_ROWS, MONARCH_COLS)
		self.assertIn("status", keysOf(stacked))
		self.assertIn("table.r0c0", keysOf(stacked))
		self.assertIn("display.1", keysOf(stacked))

	def test_aClaimTakingTheFocusWithoutAReplacementIsRefused(self):
		with self.assertRaises(LookupError) as caught:
			self.configured.withPanel(
				SinglePanel("status", SegmentRect(7, 0, 1, MONARCH_COLS)),
				MONARCH_ROWS,
				MONARCH_COLS,
			)
		self.assertIn("nowhere for untargeted regions", str(caught.exception))

	def test_aClaimAwayFromTheFocusNeedsNoReplacement(self):
		view = self.configured.withPanel(
			SinglePanel("status", SegmentRect(0, 0, 1, MONARCH_COLS)),
			MONARCH_ROWS,
			MONARCH_COLS,
		)
		view.validate(MONARCH_ROWS, MONARCH_COLS)
		self.assertEqual(view.focusSegmentKey, "display.7")

	def test_aClaimOutsideTheDisplayIsRefused(self):
		with self.assertRaises(ValueError):
			self.configured.withPanel(
				SinglePanel("oops", SegmentRect(7, 0, 4, MONARCH_COLS)),
				MONARCH_ROWS,
				MONARCH_COLS,
			)

	def test_partialOverlapEvictsTheWholePanel(self):
		"""A panel is a rectangle and has to stay one, so there is no partial eviction."""
		CONFIG["segmentCount"] = 2
		coarse = viewFromConfig(MONARCH_ROWS, MONARCH_COLS)
		view = coarse.withPanel(
			SinglePanel("strip", SegmentRect(3, 0, 2, MONARCH_COLS), focusSegmentKey="strip"),
			MONARCH_ROWS,
			MONARCH_COLS,
		)
		view.validate(MONARCH_ROWS, MONARCH_COLS)
		self.assertNotIn("display.0", keysOf(view))
		self.assertNotIn("display.1", keysOf(view))

	def test_freedCellsAreOwnedByBlankPanels(self):
		"""Eviction can free more than the claim wants; the rest must still have an owner."""
		CONFIG["segmentCount"] = 2
		coarse = viewFromConfig(MONARCH_ROWS, MONARCH_COLS)
		view = coarse.withPanel(
			SinglePanel("strip", SegmentRect(3, 0, 2, MONARCH_COLS), focusSegmentKey="strip"),
			MONARCH_ROWS,
			MONARCH_COLS,
		)
		view.validate(MONARCH_ROWS, MONARCH_COLS)
		self.assertTrue(any(panel.name.startswith("blank.") for panel in view.panels))

	def test_aClaimReplacesOneOfTheSameName(self):
		twice = self.composed.withPanel(tableClaim(), MONARCH_ROWS, MONARCH_COLS)
		twice.validate(MONARCH_ROWS, MONARCH_COLS)
		self.assertEqual(sum(1 for panel in twice.panels if panel.name == "table"), 1)


class TestRemoval(ViewTestCase):
	def setUp(self):
		super().setUp()
		CONFIG["segmentCount"] = 8
		self.configured = viewFromConfig(MONARCH_ROWS, MONARCH_COLS)
		self.composed = self.configured.withPanel(tableClaim(), MONARCH_ROWS, MONARCH_COLS)

	def test_removingAPanelThatIsNotThereChangesNothing(self):
		self.assertIs(self.composed.withoutPanel("nope", MONARCH_ROWS, MONARCH_COLS), self.composed)

	def test_removingTheFocusPanelNeedsAReplacement(self):
		with self.assertRaises(LookupError):
			self.composed.withoutPanel("table", MONARCH_ROWS, MONARCH_COLS)

	def test_removingWithAReplacement(self):
		view = self.composed.withoutPanel("table", MONARCH_ROWS, MONARCH_COLS, focusSegmentKey="display.1")
		view.validate(MONARCH_ROWS, MONARCH_COLS)
		self.assertFalse(any(key.startswith("table.") for key in keysOf(view)))
		self.assertEqual(view.focusSegmentKey, "display.1")

	def test_anUnknownReplacementIsRefused(self):
		with self.assertRaises(LookupError):
			self.composed.withoutPanel("table", MONARCH_ROWS, MONARCH_COLS, focusSegmentKey="nope")

	def test_theHoleIsStillOwned(self):
		view = self.composed.withoutPanel("table", MONARCH_ROWS, MONARCH_COLS, focusSegmentKey="display.1")
		self.assertEqual(
			sum(panel.rect.displaySize for panel in view.panels),
			MONARCH_ROWS * MONARCH_COLS,
		)

	def test_removingANonFocusPanelNeedsNothing(self):
		view = self.configured.withPanel(
			SinglePanel("status", SegmentRect(0, 0, 1, MONARCH_COLS)),
			MONARCH_ROWS,
			MONARCH_COLS,
		).withoutPanel("status", MONARCH_ROWS, MONARCH_COLS)
		view.validate(MONARCH_ROWS, MONARCH_COLS)
		self.assertNotIn("status", keysOf(view))


class TestPanelLookup(ViewTestCase):
	def test_panelNamed(self):
		CONFIG["segmentCount"] = 8
		view = viewFromConfig(MONARCH_ROWS, MONARCH_COLS).withPanel(
			tableClaim(),
			MONARCH_ROWS,
			MONARCH_COLS,
		)
		self.assertIsNotNone(view.panelNamed("table"))
		self.assertIsNone(view.panelNamed("nope"))


if __name__ == "__main__":
	unittest.main()
