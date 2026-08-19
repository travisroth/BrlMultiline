# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for panels: claims on the display, and the segments they produce.

A panel is the unit of reservation. It owns a rectangle, subdivides it, and hands its
segments the policies they behave by. Everything here is build time arithmetic, so none of
it needs a display attached.
"""

import unittest

from ._stubs import installStubs

installStubs()

from brlMultiline.layout import SegmentRect  # noqa: E402
from brlMultiline.panels import (  # noqa: E402
	BlankPanel,
	BraillePanel,
	FlowPanel,
	GridPanel,
	RowsPanel,
	SegmentSpec,
	SinglePanel,
)
from brlMultiline.routing import DEFAULT_ROUTING_POLICY, RoutingPolicy  # noqa: E402

MONARCH = SegmentRect(row=0, col=0, numRows=8, numCols=32)
FOCUS80 = SegmentRect(row=0, col=0, numRows=1, numCols=80)


class TestSegmentSpec(unittest.TestCase):
	def test_defaultsAreNVDABehaviour(self):
		spec = SegmentSpec(rect=MONARCH, key="a")
		self.assertIsNone(spec.owner)
		self.assertFalse(spec.isReserved)
		self.assertFalse(spec.fillRows)
		self.assertFalse(spec.markCuts)
		self.assertIs(spec.routingPolicy, DEFAULT_ROUTING_POLICY)

	def test_anOwnerMeansReserved(self):
		self.assertTrue(SegmentSpec(rect=MONARCH, key="a", owner="table").isReserved)


class TestSinglePanel(unittest.TestCase):
	def test_oneSegmentFillingTheClaim(self):
		panel = SinglePanel("status", SegmentRect(0, 0, 1, 32))
		specs = panel.segments()
		self.assertEqual(len(specs), 1)
		self.assertEqual(specs[0].rect, SegmentRect(0, 0, 1, 32))

	def test_keyIsThePanelName(self):
		"""A lone segment has no sibling to be told apart from, so it takes the panel's name.

		This is what lets a configured view of one panel per segment produce the keys
		`display.0`, `display.1` and so on.
		"""
		self.assertEqual(SinglePanel("display.3", MONARCH).segments()[0].key, "display.3")

	def test_reservedByDefault(self):
		self.assertEqual(SinglePanel("table", MONARCH).segments()[0].owner, "table")

	def test_canBeLeftFree(self):
		self.assertIsNone(SinglePanel("display.0", MONARCH, reserve=False).segments()[0].owner)


class TestRowsPanel(unittest.TestCase):
	def test_dividesIntoRowGroups(self):
		panel = RowsPanel("stack", SegmentRect(2, 0, 6, 32), 3)
		rects = [spec.rect for spec in panel.segments()]
		self.assertEqual(
			rects,
			[SegmentRect(2, 0, 2, 32), SegmentRect(4, 0, 2, 32), SegmentRect(6, 0, 2, 32)],
		)

	def test_coordinatesAreRelativeToTheDisplay(self):
		"""A panel divides its own claim but reports in display coordinates."""
		panel = RowsPanel("stack", SegmentRect(5, 8, 3, 16), 3)
		self.assertEqual([spec.rect.row for spec in panel.segments()], [5, 6, 7])
		self.assertTrue(all(spec.rect.col == 8 for spec in panel.segments()))

	def test_explicitSizes(self):
		panel = RowsPanel("stack", MONARCH, [1, 3, 4])
		self.assertEqual([spec.rect.numRows for spec in panel.segments()], [1, 3, 4])

	def test_singleRowClaimDividesIntoColumns(self):
		panel = RowsPanel("halves", FOCUS80, 2)
		rects = [spec.rect for spec in panel.segments()]
		self.assertEqual(rects, [SegmentRect(0, 0, 1, 40), SegmentRect(0, 40, 1, 40)])

	def test_keysAreNumbered(self):
		self.assertEqual([spec.key for spec in RowsPanel("s", MONARCH, 3).segments()], ["s.0", "s.1", "s.2"])

	def test_rejectsALayoutThatDoesNotFit(self):
		with self.assertRaises(ValueError):
			RowsPanel("s", SegmentRect(0, 0, 2, 32), 5)

	def test_policiesCascadeToSegments(self):
		policy = RoutingPolicy()
		panel = RowsPanel("s", MONARCH, 2, fillRows=True, markCuts=True, routingPolicy=policy)
		for spec in panel.segments():
			self.assertTrue(spec.fillRows)
			self.assertTrue(spec.markCuts)
			self.assertIs(spec.routingPolicy, policy)


class TestGridPanel(unittest.TestCase):
	def setUp(self):
		self.panel = GridPanel("table", SegmentRect(2, 0, 6, 32), rowBands=[2, 2, 2], colWidths=[10, 10, 10])

	def test_nineCells(self):
		self.assertEqual(len(self.panel.segments()), 9)

	def test_cellsAreNamedByPosition(self):
		keys = [spec.key for spec in self.panel.segments()]
		self.assertEqual(keys[0], "table.r0c0")
		self.assertEqual(keys[5], "table.r1c2")
		self.assertEqual(keys[8], "table.r2c2")

	def test_keyForRoundTrips(self):
		self.assertEqual(self.panel.keyFor(1, 2), "table.r1c2")

	def test_keyForRejectsAMissingCell(self):
		with self.assertRaises(LookupError):
			self.panel.keyFor(3, 0)

	def test_cellGeometry(self):
		lookup = {spec.key: spec.rect for spec in self.panel.segments()}
		self.assertEqual(lookup["table.r0c0"], SegmentRect(2, 0, 2, 10))
		self.assertEqual(lookup["table.r1c2"], SegmentRect(4, 20, 2, 10))

	def test_narrowCellsFillRowsByDefault(self):
		"""A cell 10 wide cannot spare cells for whole words, so it fills instead."""
		self.assertTrue(all(spec.fillRows for spec in self.panel.segments()))

	def test_offersItsFirstCellAsAFocusSegment(self):
		self.assertEqual(self.panel.focusSegmentKey, "table.r0c0")

	def test_unusedWidthStaysWithinTheClaim(self):
		"""Three 10 cell columns in a 32 cell claim leave 2 cells, which the panel keeps."""
		covered = sum(spec.rect.displaySize for spec in self.panel.segments())
		self.assertEqual(covered, 6 * 30)
		self.assertEqual(self.panel.rect.displaySize, 6 * 32)
		self.panel.validate()

	def test_rejectsAGridThatDoesNotFit(self):
		with self.assertRaises(ValueError):
			GridPanel("table", SegmentRect(0, 0, 2, 32), rowBands=[2, 2], colWidths=[10])


class TestBlankPanel(unittest.TestCase):
	def test_producesNoSegments(self):
		self.assertEqual(BlankPanel(SegmentRect(0, 0, 1, 32)).segments(), [])

	def test_namedByPosition(self):
		self.assertEqual(BlankPanel(SegmentRect(3, 8, 1, 4)).name, "blank.r3c8")

	def test_stillClaimsItsCells(self):
		panel = BlankPanel(SegmentRect(3, 0, 2, 32))
		self.assertEqual(panel.rect.displaySize, 64)


class TestPanelValidation(unittest.TestCase):
	def test_rejectsSegmentsOutsideTheClaim(self):
		class Escaping(BraillePanel):
			def segments(self):
				return [self.buildSpec("0", SegmentRect(0, 0, 8, 32))]

		with self.assertRaises(ValueError) as caught:
			Escaping("bad", SegmentRect(4, 0, 2, 32)).validate()
		self.assertIn("outside panel", str(caught.exception))

	def test_rejectsOverlappingSegments(self):
		class Overlapping(BraillePanel):
			def segments(self):
				return [
					self.buildSpec("0", SegmentRect(0, 0, 2, 32)),
					self.buildSpec("1", SegmentRect(1, 0, 2, 32)),
				]

		with self.assertRaises(ValueError):
			Overlapping("bad", SegmentRect(0, 0, 4, 32)).validate()

	def test_allowsGapsBetweenSegments(self):
		"""A panel may leave space between its segments; those cells are still its own."""

		class Spaced(BraillePanel):
			def segments(self):
				return [
					self.buildSpec("0", SegmentRect(0, 0, 1, 32)),
					self.buildSpec("1", SegmentRect(2, 0, 1, 32)),
				]

		Spaced("spaced", SegmentRect(0, 0, 3, 32)).validate()

	def test_rejectsAFocusKeyItDoesNotHave(self):
		with self.assertRaises(LookupError):
			SinglePanel("a", MONARCH, focusSegmentKey="a.9").validate()


if __name__ == "__main__":
	unittest.main()


class TestOwnershipSplit(unittest.TestCase):
	"""Reserved, hosting the focus and exclusive are three questions, not one flag."""

	def test_anOrdinarySegmentAnswersNoToAllThree(self):
		spec = SegmentSpec(rect=MONARCH, key="a")
		self.assertFalse(spec.hostsSystemFocus)
		self.assertFalse(spec.exclusive)
		self.assertFalse(spec.ownerDrawsFocus)

	def test_reservedDoesNotImplyExclusive(self):
		# A pinned object reserves its segment without taking over the focus content.
		spec = SegmentSpec(rect=MONARCH, key="a", owner="monitor")
		self.assertTrue(spec.isReserved)
		self.assertFalse(spec.ownerDrawsFocus)

	def test_theFlowCombinationIsExpressible(self):
		# Reserved and hosting the focus at once is what the single owner flag could not say.
		spec = SegmentSpec(rect=MONARCH, key="a", owner="flow", hostsSystemFocus=True, exclusive=True)
		self.assertTrue(spec.isReserved)
		self.assertTrue(spec.ownerDrawsFocus)

	def test_hostingWithoutExclusiveStillSharesWithNVDA(self):
		spec = SegmentSpec(rect=MONARCH, key="a", owner="flow", hostsSystemFocus=True)
		self.assertFalse(spec.ownerDrawsFocus)

	def test_aPanelHandsTheFlagsToItsSegments(self):
		panel = BraillePanelStub("owner", MONARCH, hostsSystemFocus=True, exclusive=True)
		spec = panel.segments()[0]
		self.assertTrue(spec.hostsSystemFocus)
		self.assertTrue(spec.exclusive)


class BraillePanelStub(BraillePanel):
	"""A panel of one segment, for testing what the base class hands down."""

	def segments(self):
		return [self.buildSpec("", self.rect)]


class TestFlowPanel(unittest.TestCase):
	def test_theBandIsOneSegmentCoveringTheClaim(self):
		panel = FlowPanel("flow", SegmentRect(row=0, col=0, numRows=6, numCols=32))
		specs = panel.segments()
		self.assertEqual(len(specs), 1)
		self.assertEqual(specs[0].key, "flow")
		self.assertEqual(specs[0].rect, panel.rect)

	def test_theFocusFlowDrawsItsOwnFocusContent(self):
		panel = FlowPanel("flow", MONARCH)
		spec = panel.segments()[0]
		self.assertTrue(spec.isReserved)
		self.assertTrue(spec.ownerDrawsFocus)
		self.assertEqual(panel.focusSegmentKey, "flow")

	def test_aViewerBandHostsNothingAndOffersNoFocusSegment(self):
		panel = FlowPanel("viewer", MONARCH, hostsSystemFocus=False)
		spec = panel.segments()[0]
		self.assertTrue(spec.isReserved)
		self.assertFalse(spec.hostsSystemFocus)
		self.assertFalse(spec.ownerDrawsFocus)
		self.assertIsNone(panel.focusSegmentKey)

	def test_theBandValidates(self):
		panel = FlowPanel("flow", SegmentRect(row=2, col=0, numRows=4, numCols=32))
		panel.validate()
