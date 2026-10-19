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
	Band,
	DisplayArea,
	LayoutError,
	askAppModule,
	describeLayout,
	displayArea,
	freeArea,
	parseLayout,
	placeSegments,
	resolveShown,
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
