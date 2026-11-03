# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for what the viewer shows. See docs/design/braille-viewer-plan.md, The viewer."""

import types
import unittest

from ._stubs import installStubs

installStubs()

from brlMultiline.layout import SegmentRect  # noqa: E402
from brlMultiline.viewerModel import (  # noqa: E402
	MARGIN,
	CellPosition,
	HoverTracker,
	Member,
	Overlay,
	OverlayStore,
	PinBand,
	Picture,
	Segment,
	ViewerLayout,
	brailleCharacter,
	memberCellIndex,
	pinsAsBraille,
	routingIndex,
)

MONARCH = Member("brlMultilineMonarch", "Monarch", 0, 8, 32)
FOCUS = Member("freedomScientific", "Focus", 8, 1, 80)
MONARCH_PINS = PinBand("brlMultilineMonarch", 96, 40, 5, 3)


def composite(cells=None, **changes) -> Picture:
	""":return: a Monarch above a Focus 80, 9 rows of 80."""
	values = {
		"numRows": 9,
		"numCols": 80,
		"cells": tuple(cells if cells is not None else [0] * 9 * 80),
		"members": (MONARCH, FOCUS),
		"pinBands": (MONARCH_PINS,),
	}
	values.update(changes)
	return Picture(**values)


def buffer(rows):
	"""A pin buffer as the drivers make them, from rows of `O` and `.`."""
	return types.SimpleNamespace(
		width=max(len(row) for row in rows),
		height=len(rows),
		getDot=lambda x, y: y < len(rows) and x < len(rows[y]) and rows[y][x] == "O",
	)


class TestCells(unittest.TestCase):
	def test_aCellIsReadRowMajor(self):
		cells = [0] * 9 * 80
		cells[8 * 80 + 3] = 0b101
		self.assertEqual(composite(cells).cell(8, 3), 0b101)

	def test_aCellNotWrittenIsBlank(self):
		self.assertEqual(Picture(2, 4, (1, 2)).cell(1, 3), 0)

	def test_brailleCharacters(self):
		self.assertEqual(brailleCharacter(0), "⠀")
		self.assertEqual(brailleCharacter(0xFF), "⣿")


class TestMembers(unittest.TestCase):
	def test_aNarrowerMembersExtraColumnsAreDead(self):
		picture = composite()
		self.assertFalse(picture.isDead(7, 31))
		self.assertTrue(picture.isDead(7, 32))
		self.assertFalse(picture.isDead(8, 79))

	def test_eachRowBelongsToItsMember(self):
		picture = composite()
		self.assertIs(picture.memberForRow(0), MONARCH)
		self.assertIs(picture.memberForRow(8), FOCUS)
		self.assertIsNone(picture.memberForRow(9))

	def test_routingIndexIsOnTheWholeDisplay(self):
		picture = composite()
		self.assertEqual(routingIndex(picture, CellPosition(8, 5)), 8 * 80 + 5)
		self.assertIsNone(routingIndex(picture, CellPosition(0, 40)))
		self.assertIsNone(routingIndex(picture, CellPosition(9, 0)))

	def test_aMembersOwnIndexIsOnItsOwnRows(self):
		self.assertEqual(memberCellIndex(FOCUS, CellPosition(8, 5)), 5)
		self.assertEqual(memberCellIndex(MONARCH, CellPosition(2, 5)), 2 * 32 + 5)


class TestRawText(unittest.TestCase):
	def test_eachSegmentsTextIsUnderItsOwnCells(self):
		picture = Picture(
			2,
			10,
			(),
			segments=(
				Segment(SegmentRect(0, 0, 2, 4), "abcdefgh"),
				Segment(SegmentRect(0, 5, 1, 5), "vwxyz!"),
			),
		)
		self.assertEqual(picture.rawTextRows(), ["abcd vwxyz", "efgh      "])

	def test_eachRowsOwnTextIsUnderItAndCutAtTheSegment(self):
		picture = Picture(
			2,
			10,
			(),
			segments=(Segment(SegmentRect(0, 0, 2, 6), "ignored", ("BrlMultiline", "ode)")),),
		)
		self.assertEqual(picture.rawTextRows(), ["BrlMul    ", "ode)      "])

	def test_controlCharactersAreBlank(self):
		picture = Picture(1, 3, (), segments=(Segment(SegmentRect(0, 0, 1, 3), "a\tb"),))
		self.assertEqual(picture.rawTextRows(), ["a b"])


class TestPins(unittest.TestCase):
	def test_aMemberShowsPinsOnlyWithADrawingUp(self):
		picture = composite()
		self.assertFalse(picture.showsPins(MONARCH))
		drawn = composite(overlays={MONARCH.name: (Overlay(0, 0, buffer(["O"])),)})
		self.assertTrue(drawn.showsPins(MONARCH))
		self.assertFalse(drawn.showsPins(FOCUS))

	def test_cellsAreDrawnAtThePitch(self):
		cells = [0] * 9 * 80
		# Dot 1 in row 1, column 2: pin column 2 * 3, pin row 1 * 5.
		cells[1 * 80 + 2] = 0b1
		# Dot 8 in row 0, column 0: pin column 1, pin row 3.
		cells[0] = 0b10000000
		pins = composite(cells).pins(MONARCH)
		self.assertEqual((len(pins), len(pins[0])), (40, 96))
		raised = {(x, y) for y, row in enumerate(pins) for x, up in enumerate(row) if up}
		self.assertEqual(raised, {(6, 5), (1, 3)})

	def test_drawingsAreAddedOverTheCells(self):
		picture = composite(overlays={MONARCH.name: (Overlay(10, 20, buffer(["O.O"])),)})
		raised = {(x, y) for y, row in enumerate(picture.pins(MONARCH)) for x, up in enumerate(row) if up}
		self.assertEqual(raised, {(10, 20), (12, 20)})

	def test_aDrawingOffThePanelIsClipped(self):
		picture = composite(overlays={MONARCH.name: (Overlay(95, 39, buffer(["OO", "OO"])),)})
		raised = {(x, y) for y, row in enumerate(picture.pins(MONARCH)) for x, up in enumerate(row) if up}
		self.assertEqual(raised, {(95, 39)})

	def test_aMemberThatCannotDrawHasNoPins(self):
		self.assertEqual(composite().pins(FOCUS), [])


class TestText(unittest.TestCase):
	def test_eachRowIsBrailleOverItsText(self):
		cells = [0] * 9 * 80
		cells[0:2] = [0b1, 0b11]
		cells[8 * 80] = 0b1001
		segments = (
			Segment(SegmentRect(0, 0, 8, 32), "ab"),
			Segment(SegmentRect(8, 0, 1, 80), "c"),
		)
		lines = composite(cells, segments=segments).toText().split("\r\n")
		self.assertEqual(lines[:3], ["Monarch", "⠁⠃", "ab"])
		self.assertEqual(len(lines), 1 + 8 * 2 + 1 + 2)
		self.assertEqual(lines[-3:], ["Focus", "⠉", "c"])

	def test_aDeadCellIsLeftOut(self):
		cells = [0b1] * 9 * 80
		lines = composite(cells).toText().split("\r\n")
		self.assertEqual(lines[1], "⠁" * 32)
		self.assertEqual(lines[-2], "⠁" * 80)

	def test_aDrawingIsWrittenAsPins(self):
		picture = composite(overlays={MONARCH.name: (Overlay(0, 0, buffer(["OO", ".O", "O.", "OO"])),)})
		lines = picture.toText().split("\r\n")
		self.assertEqual(lines[0], "Monarch")
		self.assertEqual(lines[1], brailleCharacter(0b11011101))
		self.assertEqual(len(lines), 1 + 10 + 1 + 2)
		self.assertEqual(lines[11], "Focus")

	def test_nothingToShowIsEmpty(self):
		self.assertEqual(Picture(0, 0, ()).toText(), "")

	def test_pinsArePackedTwoAcrossAndFourDown(self):
		grid = [[False] * 3 for _ in range(5)]
		grid[4][2] = True
		self.assertEqual(pinsAsBraille(grid), ["", "⠀⠁"])


class TestOverlayStore(unittest.TestCase):
	def test_drawingsAreKeptByDisplayAndKey(self):
		store = OverlayStore()
		first, second = Overlay(0, 0, "a"), Overlay(1, 1, "b")
		store.apply("monarch", "chart", first)
		store.apply("monarch", "label", second)
		store.apply("monarch", "chart", None)
		self.assertEqual(store.snapshot(), {"monarch": (second,)})

	def test_noKeyTakesEveryDrawingOff(self):
		store = OverlayStore()
		store.apply("monarch", "chart", Overlay(0, 0, "a"))
		store.apply("monarch", None, None)
		self.assertEqual(store.snapshot(), {})

	def test_aDisplayThatHasGoneIsForgotten(self):
		store = OverlayStore()
		store.apply("monarch", "chart", Overlay(0, 0, "a"))
		store.apply("dotPad", "chart", Overlay(0, 0, "b"))
		store.keepOnly({"dotPad"})
		self.assertEqual(set(store.snapshot()), {"dotPad"})


class TestLayout(unittest.TestCase):
	"""Where things are drawn and what a point lands on, one transform for both."""

	def drawn(self):
		return composite(overlays={MONARCH.name: (Overlay(0, 0, buffer(["O"])),)})

	def test_pinsAreSpacedTheSameBothWays(self):
		# A character twice as tall as it is wide, which would stretch a drawing if rows set the scale.
		layout = ViewerLayout(self.drawn(), cellWidth=12, cellHeight=24, labelHeight=16)
		step = layout.pinStep(MONARCH)
		self.assertEqual(step, 4)
		first = layout.pinCentre(MONARCH, 0, 0)
		self.assertEqual(layout.pinCentre(MONARCH, 1, 0)[0] - first[0], step)
		self.assertEqual(layout.pinCentre(MONARCH, 0, 1)[1] - first[1], step)
		self.assertEqual(layout.rowHeights[0], 5 * step)

	def test_aDrawnPinIsWhereThePointerFindsIt(self):
		layout = ViewerLayout(self.drawn(), cellWidth=12, cellHeight=24, labelHeight=16)
		x, y = layout.pinCentre(MONARCH, 16, 11)
		self.assertEqual(layout.position(x, y), CellPosition(2, 5, (16, 11)))

	def test_aMemberWithoutADrawingIsTextRows(self):
		layout = ViewerLayout(composite(), cellWidth=12, cellHeight=24, labelHeight=16)
		self.assertIsNone(layout.pinStep(MONARCH))
		self.assertEqual(layout.rowHeights[0], 24 * 2 + 2)
		box = layout.cellBox(8, 5)
		self.assertEqual(layout.position(box.x + 1, box.y + 1), CellPosition(8, 5))

	def test_offTheCellsIsNoCell(self):
		layout = ViewerLayout(composite(), cellWidth=12, cellHeight=24, labelHeight=16)
		self.assertIsNone(layout.position(MARGIN - 1, layout.rowTops[0]))
		self.assertIsNone(layout.position(MARGIN + 12 * 80, layout.rowTops[0]))


class TestHover(unittest.TestCase):
	"""A resting pointer routes once per cell, and only to the cell it rested on."""

	def setUp(self):
		self.picture = composite()
		self.tracker = HoverTracker()

	def test_aRoutedCellDoesNotRouteAgainUntilLeft(self):
		self.assertTrue(self.tracker.move(self.picture, CellPosition(0, 2)))
		self.assertEqual(self.tracker.activate(self.picture), CellPosition(0, 2))
		self.assertFalse(self.tracker.move(self.picture, CellPosition(0, 2)))
		self.assertIsNone(self.tracker.pending)
		self.tracker.move(self.picture, CellPosition(0, 3))
		self.assertTrue(self.tracker.move(self.picture, CellPosition(0, 2)))

	def test_anotherCellRoutesAfterTheFirst(self):
		self.tracker.move(self.picture, CellPosition(0, 2))
		self.tracker.activate(self.picture)
		self.assertTrue(self.tracker.move(self.picture, CellPosition(0, 3)))
		self.assertEqual(self.tracker.activate(self.picture), CellPosition(0, 3))

	def test_aDeadCellIsNotWaitedOn(self):
		self.assertFalse(self.tracker.move(self.picture, CellPosition(0, 40)))
		self.assertIsNone(self.tracker.pending)

	def test_leavingDropsTheWait(self):
		self.tracker.move(self.picture, CellPosition(0, 2))
		self.tracker.leave()
		self.assertIsNone(self.tracker.activate(self.picture))

	def test_anotherDisplayInTheSamePlaceDropsTheWait(self):
		self.tracker.move(self.picture, CellPosition(0, 2))
		replaced = composite(members=(MONARCH._replace(name="anotherDisplay"), FOCUS))
		self.assertTrue(self.tracker.pictureChanged(self.picture, replaced))
		self.assertIsNone(self.tracker.activate(replaced))

	def test_aCellThatChangedDisplayIsNotRouted(self):
		# Missed by `pictureChanged`, as when the window repaints between the move and the timer.
		self.tracker.move(self.picture, CellPosition(0, 2))
		replaced = composite(members=(MONARCH._replace(name="anotherDisplay"), FOCUS))
		self.assertIsNone(self.tracker.activate(replaced))

	def test_aDrawingGoingUpOrOffDropsTheWait(self):
		drawn = composite(overlays={MONARCH.name: (Overlay(0, 0, buffer(["O"])),)})
		# The Monarch's rows change height, so the row under a still pointer is another display's.
		textLayout = ViewerLayout(self.picture, cellWidth=12, cellHeight=24, labelHeight=16)
		pinLayout = ViewerLayout(drawn, cellWidth=12, cellHeight=24, labelHeight=16)
		self.assertEqual(textLayout.position(38, 229).row, 4)
		self.assertEqual(pinLayout.position(38, 229).row, 8)
		for before, after in ((self.picture, drawn), (drawn, self.picture)):
			with self.subTest(before=bool(before.overlays)):
				self.tracker.move(before, CellPosition(4, 2))
				self.assertTrue(self.tracker.pictureChanged(before, after))
				self.assertIsNone(self.tracker.activate(after))

	def test_aDrawingThatChangesKeepsTheWait(self):
		drawn = composite(overlays={MONARCH.name: (Overlay(0, 0, buffer(["O"])),)})
		redrawn = composite(overlays={MONARCH.name: (Overlay(5, 5, buffer(["OO"])),)})
		self.tracker.move(drawn, CellPosition(4, 2))
		self.assertFalse(self.tracker.pictureChanged(drawn, redrawn))

	def test_newCellsOnTheSameDisplayKeepTheWait(self):
		self.tracker.move(self.picture, CellPosition(0, 2))
		self.assertFalse(self.tracker.pictureChanged(self.picture, composite([1] * 9 * 80)))
		self.assertEqual(self.tracker.activate(self.picture), CellPosition(0, 2))


if __name__ == "__main__":
	unittest.main()
