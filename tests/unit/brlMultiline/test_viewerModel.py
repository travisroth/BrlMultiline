# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for what the viewer shows. See docs/design/braille-viewer-plan.md, The viewer."""

import types
import unittest

from ._stubs import installStubs

installStubs()

from brlMultiline.layout import SegmentRect  # noqa: E402
from brlMultiline.viewerModel import (  # noqa: E402
	CellPosition,
	Member,
	Overlay,
	OverlayStore,
	PinBand,
	Picture,
	Segment,
	brailleCharacter,
	memberCellIndex,
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


if __name__ == "__main__":
	unittest.main()
