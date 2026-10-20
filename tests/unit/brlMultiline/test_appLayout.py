# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the app layout contract: reading an app module's answer, the free area, placement.

The displays these are written against are a Humanware Monarch of ten rows of 32 cells, a
Freedom Scientific Focus 80 of one row, and the two driven as one, the Monarch on top. That
pairing is the one with a boundary to straddle, and the reason placement is not arithmetic.
"""

import unittest

from ._stubs import installStubs

installStubs()

from brlMultiline.appLayout import (  # noqa: E402
	FOCUS,
	LAYOUT_METHOD,
	PANEL_NAME,
	AppLayoutPanel,
	Band,
	DisplayArea,
	LayoutError,
	askAppModule,
	claimFor,
	describeLayout,
	displayArea,
	freeArea,
	isAppLayoutKey,
	onHardware,
	parseLayout,
	placeSegments,
	resolveShown,
	segmentKey,
)
from brlMultiline.layout import SegmentRect  # noqa: E402

MONARCH_ROWS = 10
MONARCH_COLS = 32
FOCUS_COLS = 80
COMBINED = [Band(0, MONARCH_ROWS, MONARCH_COLS), Band(MONARCH_ROWS, 1, FOCUS_COLS)]
"""A Monarch above a Focus 80, as the virtual display driver stacks them."""

FOCUS_ON_TOP = [Band(0, 1, FOCUS_COLS), Band(1, MONARCH_ROWS, MONARCH_COLS)]


def layout(*segments, name="chat"):
	""":return: an app module's answer with these segments."""
	return {"name": name, "segments": list(segments)}


def rows(rects):
	""":return: (first row, rows) of each rectangle, which is what placement decides."""
	return [(rect.row, rect.numRows) for rect in rects]


class TestReadingTheAnswer(unittest.TestCase):
	def test_noneIsNoLayout(self):
		self.assertIsNone(parseLayout(None))

	def test_aLayoutIsRead(self):
		chat = object()
		read = parseLayout(layout({"size": 6, "show": chat}, {"show": FOCUS}))
		self.assertEqual(read.name, "chat")
		self.assertEqual([segment.size for segment in read.segments], [6, None])
		self.assertIs(read.segments[0].show, chat)
		self.assertEqual(read.focusIndex, 1)

	def test_aCallableIsAskedLater(self):
		read = parseLayout(layout({"size": 6, "show": lambda: None}, {"show": FOCUS}))
		self.assertTrue(read.segments[0].isResolved)
		self.assertFalse(read.segments[1].isResolved)

	def test_aTupleOfSegmentsIsAccepted(self):
		read = parseLayout({"name": "chat", "segments": ({"show": FOCUS},)})
		self.assertEqual(len(read.segments), 1)

	def test_refusals(self):
		"""Each says what was wrong, since the app module's author is who reads it."""
		cases = {
			"not a dict": ["chat"],
			"no name": {"segments": [{"show": FOCUS}]},
			"blank name": {"name": " ", "segments": [{"show": FOCUS}]},
			"no segments": {"name": "chat", "segments": []},
			"segments not a list": {"name": "chat", "segments": "focus"},
			"segment not a dict": layout(FOCUS),
			"nothing to show": layout({"size": 2}, {"show": FOCUS}),
			"shows None": layout({"size": 2, "show": None}, {"show": FOCUS}),
			"a typo for focus": layout({"show": "Focus"}),
			"no focus": layout({"show": object()}),
			"two focuses": layout({"size": 2, "show": FOCUS}, {"show": FOCUS}),
			"two without a size": layout({"show": object()}, {"show": FOCUS}),
			"size zero": layout({"size": 0, "show": object()}, {"show": FOCUS}),
			"size negative": layout({"size": -2, "show": object()}, {"show": FOCUS}),
			"size text": layout({"size": "6", "show": object()}, {"show": FOCUS}),
			"size a fraction": layout({"size": 2.5, "show": object()}, {"show": FOCUS}),
			"size True": layout({"size": True, "show": object()}, {"show": FOCUS}),
		}
		for case, raw in cases.items():
			with self.subTest(case=case):
				with self.assertRaises(LayoutError):
					parseLayout(raw)

	def test_aRefusalNamesTheLayoutAndSegment(self):
		with self.assertRaises(LayoutError) as caught:
			parseLayout(layout({"size": 0, "show": object()}, {"show": FOCUS}))
		self.assertIn("segment 0", str(caught.exception))
		self.assertIn("'chat'", str(caught.exception))


class TestTheFreeArea(unittest.TestCase):
	def test_nothingPinnedIsTheWholeDisplay(self):
		self.assertEqual(
			freeArea(MONARCH_ROWS, MONARCH_COLS, []), SegmentRect(0, 0, MONARCH_ROWS, MONARCH_COLS)
		)

	def test_aPinTakesItsRows(self):
		"""A clock pinned to the bottom row leaves the nine above it."""
		clock = SegmentRect(9, 0, 1, MONARCH_COLS)
		self.assertEqual(freeArea(MONARCH_ROWS, MONARCH_COLS, [clock]), SegmentRect(0, 0, 9, MONARCH_COLS))

	def test_aPinTakesTheWholeRowEvenIfNarrower(self):
		"""Rows are the unit on a display with rows, so a pin in part of a row takes it all."""
		narrow = SegmentRect(0, 0, 1, 10)
		self.assertEqual(freeArea(MONARCH_ROWS, MONARCH_COLS, [narrow]).row, 1)

	def test_theLongerGapWins(self):
		pin = SegmentRect(3, 0, 1, MONARCH_COLS)
		self.assertEqual(freeArea(MONARCH_ROWS, MONARCH_COLS, [pin]), SegmentRect(4, 0, 6, MONARCH_COLS))

	def test_theEarlierGapWinsATie(self):
		"""Where a reader's eye starts."""
		pins = [SegmentRect(0, 0, 1, MONARCH_COLS), SegmentRect(5, 0, 1, MONARCH_COLS)]
		self.assertEqual(freeArea(MONARCH_ROWS, MONARCH_COLS, pins), SegmentRect(1, 0, 4, MONARCH_COLS))

	def test_everythingPinnedIsNoArea(self):
		pins = [SegmentRect(0, 0, 5, MONARCH_COLS), SegmentRect(5, 0, 5, MONARCH_COLS)]
		self.assertIsNone(freeArea(MONARCH_ROWS, MONARCH_COLS, pins))

	def test_aSingleRowDisplayIsDividedInCells(self):
		"""On a Focus 80, a pin in the right half leaves the left half."""
		pin = SegmentRect(0, 40, 1, 40)
		self.assertEqual(freeArea(1, FOCUS_COLS, [pin]), SegmentRect(0, 0, 1, 40))

	def test_aCombinedDisplayWithTheFocusPinned(self):
		"""A pin on the Focus row of a combined display leaves the Monarch."""
		pin = SegmentRect(MONARCH_ROWS, 0, 1, FOCUS_COLS)
		area = freeArea(MONARCH_ROWS + 1, FOCUS_COLS, [pin])
		self.assertEqual(area, SegmentRect(0, 0, MONARCH_ROWS, FOCUS_COLS))

	def test_noDisplayIsNoArea(self):
		self.assertIsNone(freeArea(0, 0, []))


class TestTheRunTheFocusIsIn(unittest.TestCase):
	"""A layout arranges the focus among other things, so it is where the focus is."""

	def test_theFocusRunWinsOverALongerOne(self):
		pin = SegmentRect(3, 0, 1, MONARCH_COLS)
		focus = SegmentRect(1, 0, 1, MONARCH_COLS)
		self.assertEqual(
			freeArea(MONARCH_ROWS, MONARCH_COLS, [pin], focus), SegmentRect(0, 0, 3, MONARCH_COLS)
		)

	def test_theFocusInTheLongerRun(self):
		pin = SegmentRect(3, 0, 1, MONARCH_COLS)
		focus = SegmentRect(9, 0, 1, MONARCH_COLS)
		self.assertEqual(
			freeArea(MONARCH_ROWS, MONARCH_COLS, [pin], focus), SegmentRect(4, 0, 6, MONARCH_COLS)
		)

	def test_aFocusSpanningRowsIsFoundByItsFirst(self):
		focus = SegmentRect(4, 0, 6, MONARCH_COLS)
		self.assertEqual(
			freeArea(MONARCH_ROWS, MONARCH_COLS, [], focus), SegmentRect(0, 0, MONARCH_ROWS, MONARCH_COLS)
		)

	def test_onASingleRowItIsTheFocusCells(self):
		pin = SegmentRect(0, 40, 1, 10)
		focus = SegmentRect(0, 60, 1, 20)
		self.assertEqual(freeArea(1, FOCUS_COLS, [pin], focus), SegmentRect(0, 50, 1, 30))

	def test_aFocusOnATakenRowFallsBackToTheLongest(self):
		"""Only a stale rectangle can do this, and the longest run is the safe answer."""
		pin = SegmentRect(3, 0, 1, MONARCH_COLS)
		self.assertEqual(
			freeArea(MONARCH_ROWS, MONARCH_COLS, [pin], pin),
			SegmentRect(4, 0, 6, MONARCH_COLS),
		)


class TestTheClaim(unittest.TestCase):
	def panel(self):
		area = SegmentRect(0, 0, MONARCH_ROWS, MONARCH_COLS)
		rects = [SegmentRect(0, 0, 9, MONARCH_COLS), SegmentRect(9, 0, 1, MONARCH_COLS)]
		return AppLayoutPanel(area, rects, focusIndex=1)

	def test_itsSegmentsAreKeyedByPlace(self):
		self.assertEqual([spec.key for spec in self.panel().segments()], ["appLayout.0", "appLayout.1"])
		self.assertEqual(segmentKey(0), f"{PANEL_NAME}.0")

	def test_theObjectsAreReservedAndTheFocusIsNot(self):
		"""So the reader's pin commands refuse the layout's segments, and NVDA's focus content
		lands in the focus segment as it does in the reader's own."""
		objects, focus = self.panel().segments()
		self.assertTrue(objects.isReserved)
		self.assertFalse(focus.isReserved)

	def test_itOffersTheFocusSegment(self):
		self.assertEqual(self.panel().focusSegmentKey, "appLayout.1")
		self.panel().validate()

	def test_itOutranksTheFlowAndNotADrawing(self):
		from brlMultiline.panels import PRIORITY_FLOW, PRIORITY_GRAPHICS

		self.assertGreater(AppLayoutPanel.priority, PRIORITY_FLOW)
		self.assertLess(AppLayoutPanel.priority, PRIORITY_GRAPHICS)

	def test_itsKeysAreRecognised(self):
		self.assertTrue(isAppLayoutKey("appLayout.3"))
		self.assertFalse(isAppLayoutKey("display.0"))
		self.assertFalse(isAppLayoutKey("appLayoutish"))


class TestWhatTheAppModuleIsTold(unittest.TestCase):
	def test_anOrdinaryDisplay(self):
		self.assertEqual(
			displayArea(SegmentRect(0, 0, 9, MONARCH_COLS)),
			DisplayArea(rows=9, columns=MONARCH_COLS),
		)

	def test_aSingleRow(self):
		self.assertEqual(displayArea(SegmentRect(0, 0, 1, 40)), DisplayArea(rows=1, columns=40))

	def test_aCombinedDisplayIsOneTallDisplayAsWideAsItsNarrowest(self):
		"""Eleven rows, and 32 cells, since that is the most every row of it has."""
		area = SegmentRect(0, 0, MONARCH_ROWS + 1, FOCUS_COLS)
		self.assertEqual(displayArea(area, COMBINED), DisplayArea(rows=11, columns=MONARCH_COLS))

	def test_onlyTheDisplaysTheAreaReachesCount(self):
		"""With the Monarch pinned full, the area is the Focus row, and it is 80 wide."""
		area = SegmentRect(MONARCH_ROWS, 0, 1, FOCUS_COLS)
		self.assertEqual(displayArea(area, COMBINED), DisplayArea(rows=1, columns=FOCUS_COLS))


class TestPlacingOnOneDisplay(unittest.TestCase):
	area = SegmentRect(0, 0, MONARCH_ROWS, MONARCH_COLS)

	def place(self, *segments, area=None, bands=()):
		return placeSegments(parseLayout(layout(*segments)), area or self.area, bands)

	def test_theRestGoesToTheSegmentWithoutASize(self):
		rects = self.place({"size": 7, "show": object()}, {"show": FOCUS})
		self.assertEqual(rows(rects), [(0, 7), (7, 3)])
		self.assertTrue(all(rect.numCols == MONARCH_COLS for rect in rects))

	def test_theSegmentWithoutASizeMayComeFirst(self):
		self.assertEqual(rows(self.place({"show": object()}, {"size": 2, "show": FOCUS})), [(0, 8), (8, 2)])

	def test_sizesThatAddUpNeedNoRest(self):
		self.assertEqual(
			rows(self.place({"size": 6, "show": object()}, {"size": 4, "show": FOCUS})), [(0, 6), (6, 4)]
		)

	def test_sizesThatDoNotAddUpAreRefused(self):
		"""As the reader's own segment sizes must add up to the display."""
		with self.assertRaises(LayoutError):
			self.place({"size": 6, "show": object()}, {"size": 3, "show": FOCUS})

	def test_leavingNothingForTheRestIsRefused(self):
		with self.assertRaises(LayoutError):
			self.place({"size": MONARCH_ROWS, "show": object()}, {"show": FOCUS})

	def test_theAreaNeedNotStartAtTheTop(self):
		"""Below a pin in the top row."""
		area = SegmentRect(1, 0, 9, MONARCH_COLS)
		self.assertEqual(
			rows(self.place({"size": 6, "show": object()}, {"show": FOCUS}, area=area)), [(1, 6), (7, 3)]
		)

	def test_aSingleRowIsDividedInCells(self):
		"""A Focus 80: the chat on the left, the compose box on the right."""
		area = SegmentRect(0, 0, 1, FOCUS_COLS)
		rects = self.place({"size": 50, "show": object()}, {"show": FOCUS}, area=area)
		self.assertEqual(rects, [SegmentRect(0, 0, 1, 50), SegmentRect(0, 50, 1, 30)])

	def test_aSingleRowAfterAPinStartsWhereTheAreaDoes(self):
		area = SegmentRect(0, 20, 1, 60)
		rects = self.place({"size": 40, "show": object()}, {"show": FOCUS}, area=area)
		self.assertEqual(rects, [SegmentRect(0, 20, 1, 40), SegmentRect(0, 60, 1, 20)])


class TestPlacingAcrossDisplays(unittest.TestCase):
	"""A segment never crosses from one display to another, and the app module is not told."""

	area = SegmentRect(0, 0, MONARCH_ROWS + 1, FOCUS_COLS)

	def place(self, *segments, bands=COMBINED, area=None):
		return placeSegments(parseLayout(layout(*segments)), area or self.area, bands)

	def test_aLayoutOnTheBoundaryIsLeftAlone(self):
		"""The Monarch for the chat, the Focus for the compose box: what an app module that
		asks for one row of focus gets."""
		rects = self.place({"show": object()}, {"size": 1, "show": FOCUS})
		self.assertEqual(rows(rects), [(0, 10), (10, 1)])

	def test_eachSegmentIsAsWideAsItsDisplay(self):
		rects = self.place({"show": object()}, {"size": 1, "show": FOCUS})
		self.assertEqual([rect.numCols for rect in rects], [MONARCH_COLS, FOCUS_COLS])

	def test_aLastSegmentAcrossTheBoundaryStartsAtIt(self):
		"""Six rows and the rest would put the rest over four Monarch rows and the Focus. It
		is the last, so it moves down onto the Focus and the first takes the whole Monarch."""
		rects = self.place({"size": 6, "show": object()}, {"show": FOCUS})
		self.assertEqual(rows(rects), [(0, 10), (10, 1)])

	def test_theRowsGoToTheSegmentWithoutASizeWhenItIsEarlier(self):
		"""Four, four and the rest is four, four and three; the third crosses, and the rows it
		gives up go to the second, since the one without a size is the one being cut."""
		rects = self.place({"size": 4, "show": object()}, {"size": 4, "show": object()}, {"show": FOCUS})
		self.assertEqual(rows(rects), [(0, 4), (4, 6), (10, 1)])

	def test_theRowsGoToTheEarlierSegmentWithoutASize(self):
		"""The rest, four and three is four, four and three; the third crosses, and the two rows
		it gives up go to the first, which is the one without a size."""
		rects = self.place({"show": object()}, {"size": 4, "show": object()}, {"size": 3, "show": FOCUS})
		self.assertEqual(rows(rects), [(0, 6), (6, 4), (10, 1)])

	def test_aSegmentThatIsNotLastEndsAtTheBoundary(self):
		"""With the Focus on top, three rows and the rest becomes the Focus row and the whole
		Monarch."""
		rects = self.place({"size": 3, "show": FOCUS}, {"show": object()}, bands=FOCUS_ON_TOP)
		self.assertEqual(rows(rects), [(0, 1), (1, 10)])
		self.assertEqual([rect.numCols for rect in rects], [FOCUS_COLS, MONARCH_COLS])

	def test_aSegmentThatIsNotLastGivesItsRowsToTheNext(self):
		"""No segment without a size: the next one grows."""
		rects = self.place(
			{"size": 3, "show": FOCUS},
			{"size": 4, "show": object()},
			{"size": 4, "show": object()},
			bands=FOCUS_ON_TOP,
		)
		self.assertEqual(rows(rects), [(0, 1), (1, 6), (7, 4)])

	def test_oneSegmentOverTwoDisplaysIsRefused(self):
		with self.assertRaises(LayoutError):
			self.place({"show": FOCUS})

	def test_anAreaOnOneOfTheDisplaysHasNoBoundary(self):
		"""The Focus row pinned: the layout is on the Monarch alone."""
		area = SegmentRect(0, 0, MONARCH_ROWS, FOCUS_COLS)
		rects = self.place({"size": 6, "show": object()}, {"show": FOCUS}, area=area)
		self.assertEqual(rows(rects), [(0, 6), (6, 4)])
		self.assertTrue(all(rect.numCols == MONARCH_COLS for rect in rects))

	def test_everyPlacementCoversTheAreaExactly(self):
		"""Whatever the sizes, the rows given out are the rows there are, and no segment
		crosses the boundary."""
		for first in range(1, MONARCH_ROWS + 1):
			with self.subTest(first=first):
				rects = self.place({"size": first, "show": object()}, {"show": FOCUS})
				self.assertEqual(sum(rect.numRows for rect in rects), MONARCH_ROWS + 1)
				for rect in rects:
					self.assertFalse(rect.row < MONARCH_ROWS < rect.endRow, rect)


class TestTheFocusStaysOnItsDisplay(unittest.TestCase):
	"""The display the reader put the focus on keeps it, whichever way the displays are stacked."""

	area = SegmentRect(0, 0, MONARCH_ROWS + 1, FOCUS_COLS)
	focusOnTop = SegmentRect(0, 0, 1, FOCUS_COLS)
	focusBelow = SegmentRect(MONARCH_ROWS, 0, 1, FOCUS_COLS)
	onTheMonarch = SegmentRect(2, 0, 2, MONARCH_COLS)

	def chat(self, focusRows=2):
		""":return: what the Teams add-on offers: the history, then the compose box."""
		return parseLayout(layout({"show": object()}, {"size": focusRows, "show": FOCUS}))

	def test_aFocus80OnTopKeepsTheFocus(self):
		"""The case that found this: the Focus 80 on top holding the focus. The history goes on
		the Monarch below, though the layout lists it first."""
		rects = placeSegments(self.chat(), self.area, FOCUS_ON_TOP, self.focusOnTop)
		self.assertEqual(
			rects, [SegmentRect(1, 0, MONARCH_ROWS, MONARCH_COLS), SegmentRect(0, 0, 1, FOCUS_COLS)]
		)

	def test_theSameWithTheDisplaysTheOtherWayUp(self):
		rects = placeSegments(self.chat(), self.area, COMBINED, self.focusBelow)
		self.assertEqual(
			rects,
			[SegmentRect(0, 0, MONARCH_ROWS, MONARCH_COLS), SegmentRect(MONARCH_ROWS, 0, 1, FOCUS_COLS)],
		)

	def test_theFocusTakesItsWholeRowWhateverItAskedFor(self):
		"""It asked for two rows of a display it could not see had one."""
		rects = placeSegments(self.chat(focusRows=2), self.area, FOCUS_ON_TOP, self.focusOnTop)
		self.assertEqual(rects[1].numRows, 1)
		self.assertEqual(rects[1].numCols, FOCUS_COLS)

	def test_aMonarchHoldingTheFocusTakesTheWholeLayout(self):
		"""Placed as on a single display, with the Focus 80 left as the reader arranged it."""
		rects = placeSegments(self.chat(), self.area, COMBINED, self.onTheMonarch)
		self.assertEqual(rows(rects), [(0, 8), (8, 2)])
		self.assertTrue(all(rect.numCols == MONARCH_COLS for rect in rects))
		self.assertEqual(claimFor(self.area, rects), SegmentRect(0, 0, MONARCH_ROWS, FOCUS_COLS))

	def test_theAppModuleIsToldTheFocusDisplayAloneWhenItHasRows(self):
		self.assertEqual(
			displayArea(self.area, COMBINED, self.onTheMonarch),
			DisplayArea(rows=MONARCH_ROWS, columns=MONARCH_COLS),
		)

	def test_theAppModuleIsToldBothWhenTheFocusDisplayIsOneRow(self):
		self.assertEqual(
			displayArea(self.area, FOCUS_ON_TOP, self.focusOnTop),
			DisplayArea(rows=MONARCH_ROWS + 1, columns=MONARCH_COLS),
		)

	def test_theClaimCoversBothDisplays(self):
		rects = placeSegments(self.chat(), self.area, FOCUS_ON_TOP, self.focusOnTop)
		self.assertEqual(claimFor(self.area, rects), self.area)

	def test_theOthersShareTheOtherDisplayInTheirOrder(self):
		read = parseLayout(
			layout({"size": 3, "show": object()}, {"show": object()}, {"size": 1, "show": FOCUS}),
		)
		rects = placeSegments(read, self.area, FOCUS_ON_TOP, self.focusOnTop)
		self.assertEqual(rows(rects), [(1, 3), (4, 7), (0, 1)])

	def test_withTheFocusUnsizedTheLastOtherTakesWhatIsLeft(self):
		read = parseLayout(layout({"size": 4, "show": object()}, {"show": FOCUS}))
		rects = placeSegments(read, self.area, FOCUS_ON_TOP, self.focusOnTop)
		self.assertEqual(rows(rects), [(1, MONARCH_ROWS), (0, 1)])

	def test_othersTooBigForTheOtherDisplayAreRefused(self):
		read = parseLayout(
			layout(
				{"size": 6, "show": object()},
				{"size": 6, "show": object()},
				{"show": object()},
				{"size": 1, "show": FOCUS},
			),
		)
		with self.assertRaises(LayoutError):
			placeSegments(read, self.area, FOCUS_ON_TOP, self.focusOnTop)

	def test_withTheFocusUnsizedTheLastOtherShrinksToFit(self):
		"""Sized for eleven rows, placed in ten: the last of the others gives up the difference."""
		read = parseLayout(
			layout({"size": 6, "show": object()}, {"size": 6, "show": object()}, {"show": FOCUS}),
		)
		rects = placeSegments(read, self.area, FOCUS_ON_TOP, self.focusOnTop)
		self.assertEqual(rows(rects), [(1, 6), (7, 4), (0, 1)])

	def test_aLayoutOfOnlyTheFocus(self):
		read = parseLayout(layout({"show": FOCUS}))
		self.assertEqual(placeSegments(read, self.area, FOCUS_ON_TOP, self.focusOnTop), [self.focusOnTop])

	def test_theTallestOtherDisplayIsChosen(self):
		bands = [Band(0, 1, FOCUS_COLS), Band(1, 4, 40), Band(5, MONARCH_ROWS, MONARCH_COLS)]
		area = SegmentRect(0, 0, 1 + 4 + MONARCH_ROWS, FOCUS_COLS)
		rects = placeSegments(self.chat(), area, bands, SegmentRect(0, 0, 1, FOCUS_COLS))
		self.assertEqual(rects[0], SegmentRect(5, 0, MONARCH_ROWS, MONARCH_COLS))

	def test_oneDisplayIsUnchanged(self):
		area = SegmentRect(0, 0, MONARCH_ROWS, MONARCH_COLS)
		self.assertEqual(
			placeSegments(self.chat(), area, (), SegmentRect(9, 0, 1, MONARCH_COLS)),
			placeSegments(self.chat(), area),
		)

	def test_aFocusOnNoDisplayOfTheAreaFallsBackToTheBoundaryRule(self):
		elsewhere = SegmentRect(40, 0, 1, FOCUS_COLS)
		self.assertEqual(
			placeSegments(self.chat(), self.area, COMBINED, elsewhere),
			placeSegments(self.chat(), self.area, COMBINED),
		)


class TestSpreadingAcrossDisplays(unittest.TestCase):
	"""The reader's choice to put the focus alone on its display and the rest on another."""

	area = SegmentRect(0, 0, MONARCH_ROWS + 1, FOCUS_COLS)
	onTheMonarch = SegmentRect(2, 0, 2, MONARCH_COLS)

	def chat(self):
		return parseLayout(layout({"show": object()}, {"size": 2, "show": FOCUS}))

	def test_aMonarchHoldingTheFocusSendsTheHistoryToTheFocus80(self):
		"""What was asked for: the compose box on the Monarch, the history on the Focus 80."""
		rects = placeSegments(self.chat(), self.area, COMBINED, self.onTheMonarch, spread=True)
		self.assertEqual(
			rects,
			[SegmentRect(MONARCH_ROWS, 0, 1, FOCUS_COLS), SegmentRect(0, 0, MONARCH_ROWS, MONARCH_COLS)],
		)

	def test_keptIsTheDefault(self):
		rects = placeSegments(self.chat(), self.area, COMBINED, self.onTheMonarch)
		self.assertEqual(rows(rects), [(0, 8), (8, 2)])

	def test_aFocus80HoldingTheFocusIsTheSameEitherWay(self):
		focus = SegmentRect(MONARCH_ROWS, 0, 1, FOCUS_COLS)
		self.assertEqual(
			placeSegments(self.chat(), self.area, COMBINED, focus, spread=True),
			placeSegments(self.chat(), self.area, COMBINED, focus),
		)

	def test_oneDisplayIsTheSameEitherWay(self):
		area = SegmentRect(0, 0, MONARCH_ROWS, MONARCH_COLS)
		focus = SegmentRect(9, 0, 1, MONARCH_COLS)
		self.assertEqual(
			placeSegments(self.chat(), area, (), focus, spread=True),
			placeSegments(self.chat(), area, (), focus),
		)

	def test_theOthersShareAOneRowDisplayEvenly(self):
		"""Their sizes were rows, which say nothing about cells."""
		read = parseLayout(
			layout(
				{"size": 3, "show": object()},
				{"show": object()},
				{"show": object(), "size": 1},
				{"size": 1, "show": FOCUS},
			),
		)
		rects = placeSegments(read, self.area, COMBINED, self.onTheMonarch, spread=True)
		self.assertEqual(
			rects[:3],
			[
				SegmentRect(MONARCH_ROWS, 0, 1, 27),
				SegmentRect(MONARCH_ROWS, 27, 1, 27),
				SegmentRect(MONARCH_ROWS, 54, 1, 26),
			],
		)
		self.assertEqual(rects[3], SegmentRect(0, 0, MONARCH_ROWS, MONARCH_COLS))

	def test_theAppModuleIsToldBothDisplays(self):
		self.assertEqual(
			displayArea(self.area, COMBINED, self.onTheMonarch, spread=True),
			DisplayArea(rows=MONARCH_ROWS + 1, columns=MONARCH_COLS),
		)

	def test_theClaimCoversBothDisplays(self):
		rects = placeSegments(self.chat(), self.area, COMBINED, self.onTheMonarch, spread=True)
		self.assertEqual(claimFor(self.area, rects), self.area)


class TestOneFreeRowOnANarrowDisplay(unittest.TestCase):
	"""Review of ca7f5fc: a single free Monarch row was offered and divided as 80 cells."""

	bands = [Band(0, 8, 32), Band(8, 1, 80)]
	area = SegmentRect(7, 0, 1, 80)
	focus = SegmentRect(7, 0, 1, 32)

	def test_itIsOfferedAtItsOwnWidth(self):
		self.assertEqual(displayArea(self.area, self.bands, self.focus), DisplayArea(1, 32))

	def test_itIsPlacedOnItsHardware(self):
		read = parseLayout({"name": "row", "segments": [{"show": FOCUS}]})
		self.assertEqual(placeSegments(read, self.area, self.bands, self.focus), [self.focus])

	def test_itIsDividedAtItsOwnWidth(self):
		read = parseLayout(layout({"show": object()}, {"size": 12, "show": FOCUS}))
		rects = placeSegments(read, self.area, self.bands, self.focus)
		self.assertEqual(rects, [SegmentRect(7, 0, 1, 20), SegmentRect(7, 20, 1, 12)])
		self.assertEqual(claimFor(self.area, rects), SegmentRect(7, 0, 1, 32))

	def test_aFreeRowOnTheWideDisplayIsUnchanged(self):
		area = SegmentRect(8, 0, 1, 80)
		self.assertEqual(onHardware(area, self.bands), area)


class FakeAppModule:
	appName = "fake"

	def __init__(self, answer=None, raises=None):
		self.answer = answer
		self.raises = raises
		self.asked = []

	def brlMultilineLayout(self, focus, area):
		self.asked.append((focus, area))
		if self.raises is not None:
			raise self.raises
		return self.answer


class TestAskingTheAppModule(unittest.TestCase):
	area = DisplayArea(rows=10, columns=32)

	def test_theMethodNameIsTheContract(self):
		self.assertEqual(LAYOUT_METHOD, "brlMultilineLayout")

	def test_noMethodIsNoLayout(self):
		self.assertEqual(askAppModule(object(), None, self.area), (None, None))

	def test_noAppModuleIsNoLayout(self):
		self.assertEqual(askAppModule(None, None, self.area), (None, None))

	def test_itIsGivenTheFocusAndTheArea(self):
		focus = object()
		appModule = FakeAppModule(layout({"show": FOCUS}))
		read, problem = askAppModule(appModule, focus, self.area)
		self.assertIsNone(problem)
		self.assertEqual(read.name, "chat")
		self.assertEqual(appModule.asked, [(focus, self.area)])

	def test_aMethodThatRaisesCostsOnlyTheLayout(self):
		read, problem = askAppModule(FakeAppModule(raises=RuntimeError("COM failed")), None, self.area)
		self.assertIsNone(read)
		self.assertIn("RuntimeError", problem)
		self.assertIn("COM failed", problem)

	def test_aBadAnswerIsReported(self):
		read, problem = askAppModule(FakeAppModule({"name": "chat"}), None, self.area)
		self.assertIsNone(read)
		self.assertIn("segments", problem)

	def test_anAttributeThatIsNotAMethod(self):
		appModule = type("Odd", (), {LAYOUT_METHOD: 3})()
		read, problem = askAppModule(appModule, None, self.area)
		self.assertIsNone(read)
		self.assertIsNotNone(problem)


class Named:
	def __init__(self, name, roleName="LIST"):
		self.name = name
		self.role = type("Role", (), {"name": roleName})()


class TestDescribing(unittest.TestCase):
	def test_eachSegmentSaysWhereItIsAndWhatItShows(self):
		read = parseLayout(
			layout({"size": 9, "show": Named("Chat messages")}, {"show": FOCUS}),
		)
		lines = describeLayout(read, [SegmentRect(0, 0, 9, 32), SegmentRect(9, 0, 1, 32)])
		self.assertEqual(lines[0], "Layout 'chat', 2 segments")
		self.assertIn("rows 0 to 8", lines[1])
		self.assertIn("LIST 'Chat messages'", lines[1])
		self.assertIn("asked for 9", lines[1])
		self.assertIn("row 9", lines[2])
		self.assertIn("the focus", lines[2])
		self.assertIn("asked for the rest", lines[2])

	def test_aCallableIsAskedForTheReport(self):
		read = parseLayout(layout({"size": 9, "show": lambda: Named("Message", "DOCUMENT")}, {"show": FOCUS}))
		lines = describeLayout(read, [SegmentRect(0, 0, 9, 32), SegmentRect(9, 0, 1, 32)])
		self.assertIn("asked each time, now DOCUMENT 'Message'", lines[1])

	def test_aCallableThatRaisesIsSaidSo(self):
		def broken():
			raise LookupError("no pane")

		read = parseLayout(layout({"size": 9, "show": broken}, {"show": FOCUS}))
		lines = describeLayout(read, [SegmentRect(0, 0, 9, 32), SegmentRect(9, 0, 1, 32)])
		self.assertIn("LookupError", lines[1])

	def test_aCallableAnsweringNoneShowsNothing(self):
		read = parseLayout(layout({"size": 9, "show": lambda: None}, {"show": FOCUS}))
		self.assertEqual(resolveShown(read.segments[0]), (None, None))
		lines = describeLayout(read, [SegmentRect(0, 0, 9, 32), SegmentRect(9, 0, 1, 32)])
		self.assertIn("now nothing", lines[1])


if __name__ == "__main__":
	unittest.main()
