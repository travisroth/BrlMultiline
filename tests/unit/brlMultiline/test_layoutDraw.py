# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for drawing a page's areas as boxes on the pins, and for gathering a page live.

The drawing tests run the saved pages from `tests/fixtures/layout` through the region rules
and onto a 96 by 40 pin buffer, the Monarch's panel, and check what a hand would check: the
boxes are in the right order, none is too small to find or to label, a press names the area
under it, and an area whose insides will not fit is drawn without them rather than with
slivers. The collector tests drive `layoutCollect.Collector` over a fake browse mode document,
to check that it works in batches, finds what the rules need, and stops when cancelled.
"""

import json
import os
import sys
import unittest

from . import _stubs
from ._stubs import installStubs

installStubs()

sys.path.insert(
	0,
	os.path.join(
		os.path.dirname(__file__),
		"..",
		"..",
		"..",
		"addon",
		"brailleDisplayDrivers",
		"brlMultilineMonarch",
	),
)

from pinBuffer import PinBuffer  # noqa: E402

from brlMultiline.layoutCollect import Collector  # noqa: E402
from brlMultiline.layoutDraw import (  # noqa: E402
	MIN_BOX,
	CountStyle,
	DrawnBox,
	_Count,
	_labelSpot,
	_placeDetails,
	_Ruler,
	compose,
	composeFitting,
)
from brlMultiline.layoutRegions import Box, Region, describe, findRegions, fromProbe  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "layout")
WIDTH, HEIGHT = 96, 40
FULL_CELL = 0xFF


def translate(word: str) -> list[int]:
	"""One solid cell per letter: what matters here is how many cells, not which."""
	return [FULL_CELL] * len(word)


def draw(name: str, view: str = "screen", depth: int = 2):
	with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
		data = json.load(f)
	root, viewport = fromProbe(data)
	page = findRegions(root, viewport)
	return compose(PinBuffer, page, WIDTH, HEIGHT, view, viewport, translate, depth)


class TestRuler(unittest.TestCase):
	def test_proportionalWhenThereIsRoom(self):
		ruler = _Ruler([(0, 500, 1), (500, 1000, 1)], 0, 1000, 40)
		self.assertEqual(ruler.span(0, 500), (0, 20))
		self.assertEqual(ruler.span(500, 1000), (20, 40))

	def test_aThinBandIsGrownAndOrderIsKept(self):
		"""A banner a tenth of a screen tall still gets the pins a label needs."""
		ruler = _Ruler([(0, 90, 8), (90, 900, 8), (900, 1000, 8)], 0, 1000, 40)
		top, middle, bottom = ruler.span(0, 90), ruler.span(90, 900), ruler.span(900, 1000)
		self.assertGreaterEqual(top[1] - top[0], 8)
		self.assertGreaterEqual(bottom[1] - bottom[0], 8)
		self.assertEqual(top[1], middle[0])
		self.assertEqual(middle[1], bottom[0])


class TestRealPages(unittest.TestCase):
	def assertNoBoxTooSmall(self, overview):
		for box in overview.walk():
			self.assertGreaterEqual(box.rect.width, MIN_BOX[0], box.region.title)
			self.assertGreaterEqual(box.rect.height, MIN_BOX[1], box.region.title)

	def test_hackerNewsTopToBottom(self):
		overview = draw("hackernews.json", "page")
		kinds = [b.region.kind for b in overview.boxes]
		self.assertEqual(kinds, ["header", "main", "footer"])
		tops = [b.rect.top for b in overview.boxes]
		self.assertEqual(tops, sorted(tops))
		self.assertNoBoxTooSmall(overview)

	def test_seekingAlphaSidebarAtTheLeft(self):
		overview = draw("seekingalpha.json")
		aside = [b for b in overview.boxes if b.region.kind == "complementary"][0]
		banner = [b for b in overview.boxes if b.region.kind == "banner"][0]
		self.assertEqual(aside.rect.left, 0)
		self.assertGreaterEqual(aside.rect.height, HEIGHT - 1, "the full height of the panel")
		self.assertGreaterEqual(banner.rect.left, aside.rect.right - 1)
		self.assertTrue([c for c in banner.children if c.region.kind == "search"])
		self.assertNoBoxTooSmall(overview)

	def test_pressNamesTheAreaUnderIt(self):
		overview = draw("seekingalpha.json")
		aside = [b for b in overview.boxes if b.region.kind == "complementary"][0]
		said = overview.describeAt(aside.rect.left + 1, aside.rect.top + 1)
		self.assertTrue(said.startswith("complementary, down the left side"), said)
		self.assertIsNone(overview.describeAt(WIDTH + 5, 0))

	def test_crampedInsidesAreLeftForADrillIn(self):
		"""Fabula's whole page: main's five bands cannot each get a readable box."""
		overview = draw("fabula-chrome-scrolled.json", "page")
		main = [b for b in overview.boxes if b.region.kind == "main"][0]
		self.assertEqual(main.children, [])
		self.assertEqual([b.region.kind for b in overview.boxes], ["region", "banner", "main", "contentinfo"])
		self.assertNoBoxTooSmall(overview)

	def test_everyPageDrawsBoxesBigEnoughToFind(self):
		for name in sorted(os.listdir(FIXTURES)):
			for view in ("screen", "page"):
				with self.subTest(page=name, view=view):
					overview = draw(name, view)
					self.assertTrue(overview.boxes)
					self.assertNoBoxTooSmall(overview)

	def test_labelIsDrawnInsideItsBox(self):
		overview = draw("hackernews.json", "page")
		header = overview.boxes[0]
		self.assertTrue(header.label)
		# "head" is four cells; the first cell's top left dot sits two pins in from the corner.
		self.assertTrue(overview.buffer.getDot(header.rect.left + 2, header.rect.top + 2))


class TestScreenView(unittest.TestCase):
	def test_stickyHeaderIsDrawnAtTheTopOfTheScreen(self):
		"""Fabula scrolled a screen down: on the page the banner is near the top, off screen; on
		the screen it is pinned at the top, over the main content."""
		overview = draw("fabula-chrome-scrolled.json", "screen")
		kinds = [b.region.kind for b in overview.boxes]
		self.assertEqual(kinds[0], "banner", kinds)
		banner, main = overview.boxes[0], [b for b in overview.boxes if b.region.kind == "main"][0]
		self.assertEqual(banner.rect.top, 0)
		self.assertGreaterEqual(main.rect.top, banner.rect.bottom - 1, "main is drawn below it, not under it")
		self.assertTrue([c for c in banner.children if c.region.kind == "navigation"])


def loadPage(name: str):
	with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
		root, viewport = fromProbe(json.load(f))
	return findRegions(root, viewport), viewport


class TestTallPageAndItsWindows(unittest.TestCase):
	"""The whole page drawn a screen to a panel, and the views of it zooming and moving compose."""

	def test_aTallPageKeepsTheAreasInsideMain(self):
		"""Squeezed onto one panel Fabula's main lost its five bands; a screen to a panel keeps them."""
		page, viewport = loadPage("fabula-chrome-scrolled.json")
		screens = page.box.height / viewport.height
		overview = compose(PinBuffer, page, WIDTH, round(HEIGHT * screens), "page", viewport, translate)
		main = [b for b in overview.boxes if b.region.kind == "main"][0]
		self.assertGreaterEqual(len(main.children), 4)

	def test_aWindowShowsWhatWasUnderIt(self):
		page, viewport = loadPage("fabula-chrome-scrolled.json")
		tall = compose(PinBuffer, page, WIDTH, HEIGHT * 9, "page", viewport, translate)
		banner = [b for b in tall.boxes if b.region.kind == "banner"][0]
		top = banner.rect.top / tall.buffer.height
		down = banner.rect.height / tall.buffer.height
		window = tall.windowAt(0.0, 1.0, top, down)
		self.assertLessEqual(abs(window.top - banner.region.box.top), 20)
		self.assertLessEqual(abs(window.bottom - banner.region.box.bottom), 20)

	def test_magnifiedLabelsAreBrailleAtItsOwnSize(self):
		"""Zoomed in, a label is composed again, three pins to a cell, not blown up."""
		page, viewport = loadPage("seekingalpha.json")
		part = compose(
			PinBuffer, page, WIDTH, HEIGHT, "screen", viewport, translate, window=Box(0, 0, 960, 455)
		)
		labelled = [b for b in part.walk() if b.label]
		self.assertTrue(labelled)
		box = labelled[0]
		row = box.rect.top + 2
		dots = [x for x in range(box.rect.left + 1, box.rect.right - 1) if part.buffer.getDot(x, row)]
		self.assertTrue(dots)
		# Solid test cells raise both columns of each cell, then a gap: pairs three pins apart.
		self.assertEqual(dots[1] - dots[0], 1)

	def test_aKeyLetterIsTheSameInEveryView(self):
		page = Region("page", "", Box(0, 0, 1000, 2000), "page")
		narrow = Region("region", "Announcements", Box(0, 0, 60, 2000), "landmark")
		wide = Region("main", "", Box(60, 0, 940, 2000), "landmark")
		page.children = [narrow, wide]
		keys: dict = {}
		whole = compose(PinBuffer, page, WIDTH, HEIGHT, "page", page.box, translate, 1, keys=keys)
		part = compose(
			PinBuffer,
			page,
			WIDTH,
			HEIGHT,
			"page",
			page.box,
			translate,
			1,
			window=Box(0, 1000, 1000, 1000),
			keys=keys,
		)
		self.assertEqual([b.key for b in whole.keyed], ["a"])
		self.assertEqual([b.key for b in part.keyed], ["a"])
		self.assertIs(part.keyed[0].region, narrow)

	def test_aPressInAWindowSaysWhereOnTheWholePage(self):
		page, viewport = loadPage("seekingalpha.json")
		part = compose(
			PinBuffer, page, WIDTH, HEIGHT, "screen", viewport, translate, window=Box(0, 0, 960, 455)
		)
		aside = [b for b in part.boxes if b.region.kind == "complementary"][0]
		said = part.describeAt(aside.rect.left + 1, aside.rect.top + 1)
		self.assertIn("down the left side", said)


class TestDetailLines(unittest.TestCase):
	"""What a box holds, beside or under its label, only where there is room already."""

	def page(self, links: int, height: int = 2000):
		page = Region("page", "", Box(0, 0, 1000, height), "page")
		page.children = [Region("main", "", Box(0, 0, 1000, height), "landmark", links)]
		return page

	def test_aWideBoxGetsItsLinksOnTheLabelsLine(self):
		"""Read as one phrase, "main 8 links", with a blank cell between."""
		overview = compose(
			PinBuffer, self.page(8), WIDTH, HEIGHT, "page", Box(0, 0, 1000, 2000), translate, 1
		)
		box = overview.boxes[0]
		self.assertEqual(box.detail, "8 links")
		self.assertEqual(box.detailAt.top, box.labelAt.top)
		self.assertEqual(box.detailAt.left, box.labelAt.right + 1 + 3, "one blank cell after the label")
		self.assertTrue(overview.buffer.getDot(box.detailAt.left, box.detailAt.top))

	def test_aNarrowBoxGetsItsLinksUnderItsLabel(self):
		"""Too narrow for both on one line, tall enough for a second line."""
		page = Region("page", "", Box(0, 0, 1000, 2000), "page")
		page.children = [
			Region("navigation", "", Box(0, 0, 220, 2000), "landmark", 8),
			Region("main", "", Box(320, 0, 680, 2000), "landmark"),
		]
		overview = compose(PinBuffer, page, WIDTH, HEIGHT, "page", page.box, translate, 1)
		nav = overview.boxes[0]
		self.assertEqual(nav.detail, "8 links")
		self.assertEqual(nav.detailAt.left, nav.labelAt.left)
		self.assertEqual(nav.detailAt.top, nav.labelAt.bottom, "line after line, as the Monarch's text")

	def test_oneLinkIsOneLink(self):
		overview = compose(
			PinBuffer, self.page(1), WIDTH, HEIGHT, "page", Box(0, 0, 1000, 2000), translate, 1
		)
		self.assertEqual(overview.boxes[0].detail, "1 link")

	def test_noLinksNoDetail(self):
		overview = compose(
			PinBuffer, self.page(0), WIDTH, HEIGHT, "page", Box(0, 0, 1000, 2000), translate, 1
		)
		self.assertEqual(overview.boxes[0].detail, "")
		self.assertIsNone(overview.boxes[0].detailAt)

	def test_aBoxIsNeverGrownForItsDetail(self):
		"""A box just big enough for its label keeps its size and goes without the detail.

		Thin, so there is no line under the label, and narrow, so there is no room beside it.
		"""
		page = Region("page", "", Box(0, 0, 1000, 1000), "page")
		thin = Region("navigation", "", Box(0, 0, 180, 50), "landmark", 12)
		rest = Region("main", "", Box(0, 50, 1000, 950), "landmark")
		page.children = [thin, rest]
		withLinks = compose(PinBuffer, page, WIDTH, HEIGHT, "page", page.box, translate, 1)
		thin.links = 0
		without = compose(PinBuffer, page, WIDTH, HEIGHT, "page", page.box, translate, 1)
		self.assertEqual([b.rect for b in withLinks.boxes], [b.rect for b in without.boxes])
		banner = withLinks.boxes[0]
		self.assertTrue(banner.label)
		self.assertEqual(banner.detail, "", "no room beside or under the label")

	def test_everyDetailIsInsideItsBoxAndClearOfTheBoxesInside(self):
		for name in sorted(os.listdir(FIXTURES)):
			for view in ("screen", "page"):
				with self.subTest(page=name, view=view):
					for box in draw(name, view).walk():
						if box.detailAt is None:
							continue
						self.assertIsNotNone(box.detailAt.intersection(box.rect))
						self.assertEqual(box.detailAt.intersection(box.rect), box.detailAt)
						for child in box.children:
							self.assertIsNone(child.rect.intersection(box.detailAt), box.region.title)


class TestSeveralDetails(unittest.TestCase):
	"""As many counts as a box already has room for, most telling first."""

	def big(self, **counts):
		page = Region("page", "", Box(0, 0, 1000, 2000), "page")
		main = Region("main", "", Box(0, 0, 1000, 2000), "landmark", counts.pop("links", 0))
		for name, value in counts.items():
			setattr(main, name, value)
		page.children = [main]
		return compose(PinBuffer, page, WIDTH, HEIGHT, "page", page.box, translate, 1).boxes[0]

	def test_aBigBoxShowsSeveralInOrder(self):
		box = self.big(links=8, fields=3, tabs=2)
		self.assertEqual([text for text, _, _ in box.details], ["2 tabs", "3 fields", "8 links"])

	def test_theyFillTheLabelsLineThenTheNext(self):
		box = self.big(links=8, fields=3, tabs=2, buttons=4, sections=5)
		spots = [spot for _, spot, _ in box.details]
		self.assertEqual(spots[0].top, box.labelAt.top)
		self.assertTrue(any(spot.top > box.labelAt.top for spot in spots), "some on a line below")
		for a, b in zip(spots, spots[1:]):
			if a.top == b.top:
				self.assertEqual(b.left, a.right + 1 + 3, "a blank cell between counts on a line")

	def test_aLessTellingCountIsNeverShownWithoutAMoreTellingOne(self):
		page = Region("page", "", Box(0, 0, 1000, 1000), "page")
		thin = Region("navigation", "", Box(0, 0, 400, 50), "landmark", 8)
		thin.fields = 12345678
		page.children = [thin, Region("main", "", Box(0, 50, 1000, 950), "landmark")]
		box = compose(PinBuffer, page, WIDTH, HEIGHT, "page", page.box, translate, 1).boxes[0]
		self.assertEqual(box.details, [], "the fields will not fit, so the links are not shown either")

	def test_aTabStripShowsJustTheNumber(self):
		"""Its label already says tabs."""
		page = Region("page", "", Box(0, 0, 1000, 1000), "page")
		strip = Region("tabs", "", Box(0, 0, 1000, 400), "widget")
		strip.tabs = 5
		page.children = [strip, Region("main", "", Box(0, 400, 1000, 600), "landmark")]
		box = compose(PinBuffer, page, WIDTH, HEIGHT, "page", page.box, translate, 1).boxes[0]
		self.assertEqual(box.detail, "5")

	def test_aPressSaysEveryCount(self):
		page = Region("page", "", Box(0, 0, 1000, 1000), "page")
		main = Region("main", "", Box(0, 0, 1000, 1000), "landmark", 8)
		main.fields = 1
		page.children = [main]
		overview = compose(PinBuffer, page, WIDTH, HEIGHT, "page", page.box, translate, 1)
		self.assertEqual(overview.describeAt(5, 30), "main content, filling it, 1 field, 8 links")


class TestLookingDownTheBox(unittest.TestCase):
	"""A count blocked where it would go first is looked for further down the box.

	Found on Fabula's coffee: boxes shown by a key letter got no count, with free lines below.
	"""

	def place(self, rect, children, text="2 fields"):
		region = Region("area", "", Box(0, 0, 10, 10), "geometry")
		kids = [DrawnBox(region, Box(*c), 2) for c in children]
		box = DrawnBox(region, Box(*rect), 1, key="a", children=kids, labelAt=Box(2, 2, 2, 4))
		_placeDetails(box, [_Count(text, translate(text))])
		return box

	def test_aLineFurtherDown(self):
		"""The label's line and the two under it are blocked by a box inside; the third is free."""
		box = self.place((0, 0, 40, 30), [(12, 1, 12, 12)])
		self.assertEqual(box.detail, "2 fields")
		self.assertEqual(box.detailAt.left, box.labelAt.left)
		self.assertGreater(box.detailAt.top, 12)

	def test_furtherAlongTheLine(self):
		"""Under the label, small boxes inside at the left and the right, with room between."""
		box = self.place((0, 0, 60, 14), [(10, 1, 6, 4), (2, 9, 4, 3), (50, 8, 6, 4)])
		self.assertEqual(box.detail, "2 fields")
		self.assertEqual(box.detailAt.top, box.labelAt.bottom)
		self.assertGreater(box.detailAt.left, 6)

	def test_stillNeverOutsideTheBox(self):
		box = self.place((0, 0, 40, 12), [(12, 1, 12, 10)])
		self.assertEqual(box.details, [])


class TestHowACountIsDrawn(unittest.TestCase):
	"""A count is its number and NVDA's abbreviation for the role, or the role's glyph.

	Found on Fabula's coffee: "3 links" in full did not fit a box that "3 lnk" did, and main
	content's only room was a strip four cells wide down its side.
	"""

	def drawn(self, width, height=2000, style=None, **counts):
		page = Region("page", "", Box(0, 0, 1000, 2000), "page")
		area = Region("navigation", "", Box(0, 0, width, height), "landmark", counts.pop("links", 0))
		for name, value in counts.items():
			setattr(area, name, value)
		page.children = [area, Region("main", "", Box(width, 0, 1000 - width, 2000), "landmark")]
		return compose(PinBuffer, page, WIDTH, HEIGHT, "page", page.box, translate, 1, style=style).boxes[0]

	def inks(self, box):
		return [ink for _, _, marks in box.details for _, _, ink in marks]

	def test_theRolesAbbreviationAfterTheNumber(self):
		box = self.drawn(400, links=8, buttons=2, fields=3)
		self.assertEqual([t for t, _, _ in box.details], ["3 fields", "2 buttons", "8 links"])
		self.assertEqual(
			self.inks(box),
			[
				translate("3"),
				translate("edt"),
				translate("2"),
				translate("btn"),
				translate("8"),
				translate("lnk"),
			],
		)

	def test_theReadersOwnWords(self):
		style = CountStyle(words={"links": "lien"})
		box = self.drawn(400, style=style, links=8)
		self.assertEqual(self.inks(box), [translate("8"), translate("lien")])

	def test_aGlyphInPlaceOfTheWord(self):
		shape = ["O.O", ".O.", "O.O", "..."]
		box = self.drawn(400, style=CountStyle(shapes={"links": shape}), links=8)
		(_, area, marks) = box.details[0]
		self.assertEqual(marks[1][2], shape)
		self.assertEqual(marks[1][0], area.left + 3, "straight after the number")
		self.assertEqual(area.width, 3 + 3, "one cell and the glyph")

	def test_aNarrowStripTakesTheNumberThenTheWordUnderIt(self):
		"""Four cells across: "#bb" fits, and "edt" fits, but not both on one line."""
		box = self.drawn(140, links=0, fields=22)
		(_, area, marks) = box.details[0]
		(nx, ny, number), (wx, wy, word) = marks
		self.assertEqual(number, translate("22"))
		self.assertEqual(word, translate("edt"))
		self.assertEqual(nx, wx, "the word under the number")
		self.assertEqual(wy, ny + 4, "on the next line")
		self.assertEqual(area.height, 8)

	def test_aTabStripsNumberAlone(self):
		page = Region("page", "", Box(0, 0, 1000, 1000), "page")
		strip = Region("tabs", "", Box(0, 0, 1000, 400), "widget")
		strip.tabs = 5
		page.children = [strip, Region("main", "", Box(0, 400, 1000, 600), "landmark")]
		box = compose(PinBuffer, page, WIDTH, HEIGHT, "page", page.box, translate, 1).boxes[0]
		self.assertEqual(self.inks(box), [translate("5")])

	def test_stackedOverABottomLabelTheNumberStillComesFirst(self):
		region = Region("area", "", Box(0, 0, 10, 10), "geometry")
		region.fields = 22
		box = DrawnBox(region, Box(0, 0, 16, 30), 1, key="a", labelAt=Box(3, 24, 2, 4))
		_placeDetails(box, [_Count("22 fields", translate("22"), translate("edt"))])
		(number, _), (word, _) = [(y, ink) for _, y, ink in box.details[0][2]]
		self.assertLess(number, word, "the number above the word")
		self.assertLess(word, 24, "both above the label")


class TestWhereALabelGoes(unittest.TestCase):
	"""Top left, or else just past the box inside that holds the corner."""

	def box(self, *children):
		region = Region("area", "", Box(0, 0, 10, 10), "geometry")
		kids = [DrawnBox(region, Box(*c), 2) for c in children]
		return DrawnBox(region, Box(16, 24, 57, 15), 1, children=kids)

	def test_theTopLeftCornerWhenItIsClear(self):
		self.assertEqual(_labelSpot(self.box(), 17), Box(18, 26, 17, 4))

	def test_justRightOfAColumnHoldingTheCorner(self):
		"""Found on Fabula's coffee: pushed to the far right edge, with room left of it unused."""
		spot = _labelSpot(self.box((17, 25, 21, 13)), 17)
		self.assertEqual(spot.top, 26)
		self.assertEqual(spot.left, 17 + 21 + 1, "a pin clear of the column, and no further")

	def test_alongTheBottomWhenTheTopIsFull(self):
		spot = _labelSpot(self.box((17, 25, 55, 6)), 17)
		self.assertEqual((spot.left, spot.bottom), (18, 24 + 15 - 2))


class TestSearchIsAlwaysS(unittest.TestCase):
	"""Search is on most pages, so its key letter is always s, found without a press."""

	def test_fabulasSearchIsS(self):
		page, viewport = loadPage("fabula-firefox-top.json")
		keys = {}
		overview = composeFitting(PinBuffer, page, WIDTH, 40, "screen", viewport, translate, 3, keys=keys)
		search = [b for b in overview.walk() if b.region.kind == "search"]
		self.assertTrue(search)
		self.assertEqual([b.key for b in search], ["s"])
		self.assertIn("s, search", overview.keyText())

	def test_nothingElseIsS(self):
		for name in sorted(os.listdir(FIXTURES)):
			with self.subTest(page=name):
				for box in draw(name).walk():
					if box.key == "s":
						self.assertEqual(box.region.kind, "search", box.region.title)

	def test_aSecondSearchTakesAnOrdinaryLetter(self):
		page = Region("page", "", Box(0, 0, 1000, 1000), "page")
		banner = Region("banner", "", Box(0, 0, 1000, 100), "landmark")
		banner.children = [
			Region("search", "", Box(400, 10, 20, 20), "landmark"),
			Region("search", "", Box(900, 10, 20, 20), "landmark"),
		]
		page.children = [banner, Region("main", "", Box(0, 100, 1000, 900), "landmark")]
		overview = compose(PinBuffer, page, WIDTH, HEIGHT, "page", page.box, translate, 2)
		keys = sorted(b.key for b in overview.walk() if b.region.kind == "search")
		self.assertEqual(keys, ["a", "s"])


class TestTheScreenIsNotSqueezed(unittest.TestCase):
	"""The screen drawn as tall as it has to be, so every area on it can be checked by hand.

	Found on the Wells Fargo sign on page: squeezed onto one panel, the header's navigation, the
	sign on form and two areas beside it were left out, with nothing said.
	"""

	def test_wellsFargoShowsEverythingOnScreen(self):
		page, viewport = loadPage("wellsfargo.json")
		overview = composeFitting(PinBuffer, page, WIDTH, 32, "screen", viewport, translate, 3)
		titles = [b.region.title for b in overview.walk()]
		self.assertIn("navigation, Header Navigation", titles)
		self.assertIn("form", [b.region.kind for b in overview.walk()])
		self.assertEqual(overview.omitted, [])
		self.assertGreater(overview.buffer.height, 32, "taller than a panel, to fit it all")

	def test_aBoxInsideAThinBannerIsStillBigEnough(self):
		"""Inside its parent's outline, it asks the ruler for the pins that outline takes."""
		page, viewport = loadPage("wellsfargo.json")
		overview = composeFitting(PinBuffer, page, WIDTH, 32, "screen", viewport, translate, 3)
		inner = [b for b in overview.walk() if b.region.title == "navigation, Header Navigation"]
		self.assertTrue(inner)
		self.assertGreaterEqual(inner[0].rect.height, MIN_BOX[1])

	def test_aFormBelowTheDepthDrawnIsStillDrawn(self):
		page, viewport = loadPage("wellsfargo.json")
		overview = composeFitting(PinBuffer, page, WIDTH, 32, "screen", viewport, translate, 3)
		form = [b for b in overview.walk() if b.region.kind == "form"][0]
		self.assertGreater(form.depth, 3, "deeper than the three levels drawn")
		holder = [b for b in overview.walk() if form in b.children][0]
		self.assertEqual(holder.depth, 3, "inside the deepest box drawn")

	def test_itStopsGrowingAtThreePanelsAndSaysWhatIsLeftOut(self):
		page = Region("page", "", Box(0, 0, 1000, 1000), "page")
		page.children = [
			Region("region", f"Band {i}", Box(0, i * 25, 1000, 25), "landmark") for i in range(40)
		]
		overview = composeFitting(PinBuffer, page, WIDTH, 32, "page", page.box, translate, 1)
		self.assertEqual(overview.buffer.height, 96, "grown to three panels and no further")
		self.assertTrue(
			any(b.rect.height < MIN_BOX[1] for b in overview.walk()), "still not room for all forty"
		)

	def test_aScreenThatFitsStaysOnePanel(self):
		page, viewport = loadPage("seekingalpha.json")
		overview = composeFitting(PinBuffer, page, WIDTH, 32, "screen", viewport, translate, 3)
		self.assertEqual(overview.buffer.height, 32)


class TestDrawingOneArea(unittest.TestCase):
	"""An area drilled into, drawn as the whole drawing with what is inside it."""

	def test_theAreaFillsTheDrawingAndItsInsidesAreItsBoxes(self):
		page, viewport = loadPage("seekingalpha.json")
		main = [r for r in page.children if r.kind == "main"][0]
		overview = compose(PinBuffer, page, WIDTH, HEIGHT, "page", viewport, translate, 2, focus=main)
		self.assertEqual(overview.window, main.box)
		self.assertTrue(overview.boxes)
		self.assertTrue(all(any(b.region is c for c in main.children) for b in overview.boxes))

	def test_positionsAreSaidWithinTheArea(self):
		page = Region("page", "", Box(0, 0, 1000, 1000), "page")
		side = Region("navigation", "", Box(0, 0, 200, 1000), "landmark")
		top = Region("region", "Menu", Box(0, 0, 200, 100), "landmark")
		side.children = [top]
		page.children = [side, Region("main", "", Box(200, 0, 800, 1000), "landmark")]
		overview = compose(PinBuffer, page, WIDTH, HEIGHT, "page", page.box, translate, 1, focus=side)
		self.assertEqual(overview.describeAt(5, 2), "region, Menu, across the top")

	def test_onTheScreenOnlyThePartOnScreenIsDrawn(self):
		page, viewport = loadPage("seekingalpha.json")
		main = [r for r in page.children if r.kind == "main"][0]
		overview = compose(PinBuffer, page, WIDTH, HEIGHT, "screen", viewport, translate, 2, focus=main)
		self.assertLessEqual(overview.window.bottom, viewport.height)


class FakeDrawingMode:
	"""Graphics mode as far as an overview being drawn and drilled into uses it."""

	lastError = ""

	def __init__(self):
		self.active = False
		self.source = None
		self.newBuffer = PinBuffer

	def drawingSize(self):
		return (WIDTH, 32)

	def enter(self, drawing):
		self.active, self.source = True, drawing
		return True

	def describe(self):
		return self.source.name

	def leave(self):
		self.active, self.source = False, None

	def holdsFocus(self):
		return True


class TestTheCountStyleForThisReader(unittest.TestCase):
	"""NVDA's own role labels, and glyphs only when wanted and the display can show them."""

	def setUp(self):
		from unittest import mock

		from . import test_plugin

		self.mock = mock
		self.module = sys.modules["brlMultiline.layoutOverview"]

	def styleWith(self, wanted, supported):
		module, mock = self.module, self.mock
		surface = mock.Mock(driver=object())
		with (
			mock.patch.object(module, "findSurface", return_value=surface),
			mock.patch.object(module.glyphFlow, "enabled", return_value=wanted),
			mock.patch.object(module.glyphs, "supported", return_value=supported),
		):
			return module.countStyle()

	def test_nvdasWordsWithoutGlyphs(self):
		style = self.styleWith(False, True)
		self.assertEqual(style.words["links"], "lnk")
		self.assertEqual(style.words["buttons"], "btn")
		self.assertEqual(style.words["fields"], "edt")
		self.assertEqual(style.shapes, {})

	def test_glyphsWhenWantedAndShown(self):
		style = self.styleWith(True, True)
		self.assertEqual(set(style.shapes), {"links", "buttons", "fields"})
		self.assertEqual(style.shapes["links"], self.module.glyphs.patternRows(self.module.glyphs.LINK.dots))

	def test_noGlyphsOnADisplayThatCannotShowThem(self):
		self.assertEqual(self.styleWith(True, False).shapes, {})


class TestDrillingInAndBackingOut(unittest.TestCase):
	def setUp(self):
		from . import test_plugin

		test_plugin.resetPluginState()
		import braille

		braille.handler = test_plugin.FakeHandler(test_plugin.MONARCH_ROWS, test_plugin.MONARCH_COLS)
		self.plugin = test_plugin.GlobalPlugin()
		self.addCleanup(self.plugin.terminate)
		self.mode = FakeDrawingMode()
		self.plugin.graphicsMode = self.mode
		_stubs.flashedMessages.clear()
		page, viewport = loadPage("seekingalpha.json")
		self.page = page
		LayoutView = sys.modules["brlMultiline.layoutOverview"].LayoutView
		self.state = LayoutView(page, "screen", viewport, None)
		self.plugin.layouts.current = self.state
		self.assertIsNotNone(self.plugin.layouts.enter(self.state))

	def pressOn(self, kind):
		"""Press inside the drawn box of an area of this kind, as a routing press would."""
		drawing = self.mode.source
		for y in range(drawing.buffer.height):
			for x in range(drawing.buffer.width):
				drawing.describeAt(x, y)
				if self.state.pressed is not None and self.state.pressed.kind == kind:
					return self.state.pressed
		self.fail(f"no box for {kind}")

	def test_drillingGoesIntoTheAreaLastPressed(self):
		main = self.pressOn("main")
		self.plugin.script_layoutDrillIn(None)
		self.assertIs(self.state.focus, main)
		self.assertEqual(self.mode.source.name, "layout of main content")
		self.assertIn("layout of main content", _stubs.flashedMessages[-1])

	def test_backingOutReturnsToTheWholeAndRemembersWhereItCameFrom(self):
		main = self.pressOn("main")
		self.plugin.script_layoutDrillIn(None)
		self.plugin.script_layoutBackOut(None)
		self.assertIsNone(self.state.focus)
		self.assertEqual(self.mode.source.name, "layout of the page on screen")
		self.assertIs(self.state.pressed, main, "so dot 8 goes straight back in")

	def test_drillingWithNothingPressedSaysSo(self):
		self.plugin.script_layoutDrillIn(None)
		self.assertIn("Press a routing key", _stubs.flashedMessages[-1])
		self.assertIsNone(self.state.focus)

	def test_anAreaWithNothingInsideSaysSo(self):
		self.state.pressed = Region("search", "", Box(0, 0, 10, 10), "search")
		self.plugin.script_layoutDrillIn(None)
		self.assertIn("Nothing inside search", _stubs.flashedMessages[-1])

	def test_backingOutAtTheTopSaysSo(self):
		self.plugin.script_layoutBackOut(None)
		self.assertIn("Already showing all of it", _stubs.flashedMessages[-1])

	def test_withAnotherDrawingUpTheCommandsSaySo(self):
		self.mode.enter(type("Chart", (), {"name": "chart"})())
		self.plugin.script_layoutDrillIn(None)
		self.assertIn("No page layout is shown", _stubs.flashedMessages[-1])


class TestKeyLetters(unittest.TestCase):
	def test_aWordTooLongForItsBoxGetsALetter(self):
		"""A narrow box is widened for a short word, but never past four cells' worth."""
		page = Region("page", "", Box(0, 0, 1000, 1000), "page")
		narrow = Region("region", "Announcements", Box(0, 0, 60, 1000), "landmark")
		wide = Region("main", "", Box(60, 0, 940, 1000), "landmark")
		page.children = [narrow, wide]
		overview = compose(PinBuffer, page, WIDTH, HEIGHT, "page", page.box, translate, 1)
		boxes = {b.region.kind: b for b in overview.boxes}
		self.assertEqual(boxes["main"].label, "word")
		self.assertEqual(boxes["region"].key, "a")
		self.assertEqual(overview.keyText(), "a, region, Announcements")


class FakeInfo:
	def __init__(self, offsets, document=None):
		self.offsets = offsets
		self.unit = None
		self.document = document

	def copy(self):
		return FakeInfo(self.offsets, self.document)

	def _getOffsetsFromFieldIdentifier(self, docHandle, ID):
		"""Where an element is in the text now, as browse mode looks it up; LookupError if gone."""
		found = self.document.elements.get((docHandle, ID)) if self.document else None
		if found is None:
			raise LookupError
		return found

	def expand(self, unit):
		self.unit = unit


class FakeBrowseDocument:
	"""A browse mode document as far as moving to an area uses one."""

	isAlive = True
	passThrough = False

	def __init__(self):
		self.selections = []
		#: Where each element is in the text now, by browse mode's identifier.
		self.elements = {}

	def makeTextInfo(self, offsets):
		return FakeInfo(offsets, self)

	def _set_selection(self, info, reason=None):
		self.selections.append((info.offsets, reason))


class FakeLeavingMode:
	def __init__(self, focusHere: bool = True):
		self.left = 0
		self.focusHere = focusHere

	def leave(self):
		self.left += 1

	def holdsFocus(self):
		return self.focusHere


class TestMovingToAnArea(unittest.TestCase):
	"""Pressing a box twice puts the browse mode cursor where the area starts."""

	def setUp(self):
		from . import test_plugin

		test_plugin.resetPluginState()
		import braille

		braille.handler = test_plugin.FakeHandler(test_plugin.MONARCH_ROWS, test_plugin.MONARCH_COLS)
		self.plugin = test_plugin.GlobalPlugin()
		self.addCleanup(self.plugin.terminate)
		self.plugin.graphicsMode = FakeLeavingMode()
		self.document = FakeBrowseDocument()
		api = sys.modules["api"]
		focus = type("Focus", (), {"treeInterceptor": self.document})()
		original = api.getFocusObject
		api.getFocusObject = lambda: focus
		self.addCleanup(setattr, api, "getFocusObject", original)
		_stubs.spokenMessages.clear()
		_stubs.spokenPositions.clear()
		_stubs.flashedMessages.clear()

	def area(self, start):
		return Region("navigation", "Primary", Box(0, 0, 100, 100), "landmark", start=start)

	def test_theCursorGoesToTheStartAndTheDrawingComesDown(self):
		self.plugin.layouts.moveToArea(self.document, self.area(120))
		self.assertEqual(self.document.selections, [((120, 120), "quicknav")])
		self.assertEqual(self.plugin.graphicsMode.left, 1)
		self.assertEqual(_stubs.spokenMessages[-1], "navigation, Primary")
		info, _format, reason = _stubs.spokenPositions[-1]
		self.assertEqual((info.unit, reason), ("line", "quicknav"))

	def test_withTheFocusOnAnotherDisplayTheDrawingStaysUp(self):
		"""A Monarch drawing beside a Focus 80 showing the focus: the line arrives there."""
		self.plugin.graphicsMode = FakeLeavingMode(focusHere=False)
		self.plugin.layouts.moveToArea(self.document, self.area(120))
		self.assertEqual(self.document.selections, [((120, 120), "quicknav")])
		self.assertEqual(self.plugin.graphicsMode.left, 0)

	def test_anAreaWithNoPlaceInTheTextSaysSo(self):
		self.plugin.layouts.moveToArea(self.document, self.area(None))
		self.assertEqual(self.document.selections, [])
		self.assertEqual(self.plugin.graphicsMode.left, 0)
		self.assertIn("No place in the page", _stubs.flashedMessages[-1])

	def test_theDrawingsSecondPressReachesTheAreaUnderIt(self):
		"""Seeking Alpha on screen: the bottom left pin is the sidebar, below its navigation."""
		self.plugin.graphicsMode.newBuffer = PinBuffer
		page, viewport = loadPage("seekingalpha.json")
		drawing = self.plugin.layouts.drawingFor(
			page, "screen", viewport, (WIDTH, HEIGHT), "layout", self.document
		)
		self.assertIn("complementary", drawing.describeAt(0, HEIGHT - 1))
		drawing.activateAt(0, HEIGHT - 1)
		aside = [r for r in page.children if r.kind == "complementary"][0]
		self.assertIsNotNone(aside.start)
		self.assertEqual(self.document.selections, [((aside.start, aside.start), "quicknav")])

	def test_aScreenTallerThanAPanelOpensAtItsTop(self):
		"""WSJ's screen needs two panels; Seeking Alpha's fits on one and opens whole."""
		self.plugin.graphicsMode.newBuffer = PinBuffer
		for name, tall in (("wsj.json", True), ("seekingalpha.json", False)):
			with self.subTest(page=name):
				page, viewport = loadPage(name)
				drawing = self.plugin.layouts.drawingFor(
					page, "screen", viewport, (WIDTH, 32), "layout", self.document
				)
				self.assertEqual(drawing.opensAcross, tall)
				self.assertEqual(drawing.buffer.height > 32, tall)
				self.assertNotIn("too small", drawing.note)

	def test_onTheSamePageReadAgainTheAreasElementIsFoundWhereItNowIs(self):
		"""Browse mode read the page again: the offset moved, the element is still there."""
		area = Region("navigation", "Primary", Box(0, 0, 100, 100), "landmark", start=120, anchor=(1, 77))
		self.document.documentConstantIdentifier, self.document.VBufHandle = "https://a.example/", 2
		self.document.elements[(1, 77)] = (300, 340)
		self.plugin.layouts.moveToArea(self.document, area, ("https://a.example/", 1))
		self.assertEqual(self.document.selections, [((300, 300), "quicknav")])

	def test_anAreaWhoseElementIsGoneIsNotMovedTo(self):
		area = Region("navigation", "Primary", Box(0, 0, 100, 100), "landmark", start=120, anchor=(1, 77))
		self.document.documentConstantIdentifier, self.document.VBufHandle = "https://a.example/", 1
		self.plugin.layouts.moveToArea(self.document, area, ("https://a.example/", 1))
		self.assertEqual(self.document.selections, [])
		self.assertIn("page has changed", _stubs.flashedMessages[-1])

	def test_anotherPageUnderTheSameDocumentIsNotMovedIn(self):
		area = Region("navigation", "Primary", Box(0, 0, 100, 100), "landmark", start=120, anchor=(1, 77))
		self.document.documentConstantIdentifier = "https://b.example/"
		self.document.elements[(1, 77)] = (300, 340)
		self.plugin.layouts.moveToArea(self.document, area, ("https://a.example/", 1))
		self.assertEqual(self.document.selections, [])

	def test_aPageThatHasChangedIsNotMovedIn(self):
		self.plugin.layouts.moveToArea(FakeBrowseDocument(), self.area(120))
		self.assertEqual(self.document.selections, [])
		self.assertIn("page has changed", _stubs.flashedMessages[-1])


class FakeObject:
	def __init__(self, role="SECTION", location=(0, 0, 0, 0), name="", attributes=None, children=()):
		self.role = type("Role", (), {"name": role})()
		self.location = location
		self.name = name
		self.IA2Attributes = attributes or {}
		self.children = list(children)
		self.childCount = len(self.children)


class FakeItem:
	located = 0

	def __init__(self, ident, start, end, obj, label=""):
		self.vbufFieldIdentifier = ident
		self.textInfo = type("Info", (), {"_startOffset": start, "_endOffset": end})()
		self._obj = obj
		self.label = label

	@property
	def obj(self):
		FakeItem.located += 1
		return self._obj


class FakeDocument:
	documentConstantIdentifier = "https://example.com/"

	def __init__(self, items, tree=None):
		self.items = items
		self.rootNVDAObject = tree or FakeObject("DOCUMENT", (0, 100, 1920, 900))

	def _iterNodesByType(self, nodeType):
		return iter(self.items.get(nodeType, []))


def run(collector: Collector) -> int:
	collector.start()
	rounds = 0
	while _stubs.callAfterQueue.pending:
		_stubs.callAfterQueue.flush()
		rounds += 1
	return rounds


class TestCollector(unittest.TestCase):
	def setUp(self):
		_stubs.callAfterQueue.discard()
		FakeItem.located = 0

	def landmarkPage(self, links=60, buttons=()):
		banner = FakeItem(
			1,
			0,
			100,
			FakeObject("LANDMARK", (0, 100, 1920, 90), attributes={"xml-roles": "banner", "tag": "header"}),
		)
		main = FakeItem(
			2,
			100,
			1000,
			FakeObject("LANDMARK", (0, 190, 1920, 800), attributes={"xml-roles": "main", "tag": "main"}),
		)
		linkItems = [
			FakeItem(100 + i, 100 + i * 10, 101 + i * 10, FakeObject("LINK", (20, 200 + i * 20, 200, 18)))
			for i in range(links)
		]
		return FakeDocument({"landmark": [banner, main], "link": linkItems, "button": list(buttons)})

	def test_gathersInBatchesWhatTheRulesRead(self):
		got = []
		rounds = run(Collector(self.landmarkPage(), lambda payload, why: got.append((payload, why))))
		self.assertGreater(rounds, 2, "NVDA gets its message loop back between batches")
		payload, why = got[0]
		self.assertEqual(why, "")
		records = payload["candidates"]["records"]
		self.assertEqual([r["xmlRoles"] for r in records], ["banner", "main"])
		self.assertEqual(records[1]["links"], 60)
		self.assertEqual(records[0]["rect"], [0, 0, 1920, 90], "relative to the page's own corner")
		self.assertEqual(len(payload["leaves"]["leaves"]), 60)
		self.assertEqual(payload["tree"]["nodes"], [], "a page with landmarks needs no tree walk")
		page = findRegions(*fromProbe(payload))
		self.assertEqual([r.kind for r in page.children], ["banner", "main"])

	def test_onlySearchButtonsAreLocated(self):
		buttons = [
			FakeItem(500 + i, 50, 51, FakeObject("BUTTON", (0, 100, 30, 30), "Menu"), label="Menu")
			for i in range(20)
		]
		search = FakeItem(600, 60, 61, FakeObject("BUTTON", (1800, 120, 40, 30), "Search"), label="Search")
		got = []
		run(Collector(self.landmarkPage(links=0, buttons=[*buttons, search]), lambda p, w: got.append(p)))
		records = got[0]["candidates"]["records"]
		self.assertEqual([r["type"] for r in records].count("search button"), 1)
		self.assertEqual(FakeItem.located, 3, "two landmarks and the search button, not the menus")

	def test_leavesAreSampledFromEveryKindAndEveryPart(self):
		"""Hundreds of links must not crowd out the pictures, nor the bottom of the page."""
		links = [
			FakeItem(1000 + i, i, i + 1, FakeObject("LINK", (20, 100 + i * 10, 200, 8))) for i in range(900)
		]
		pictures = [
			FakeItem(3000 + i, i, i + 1, FakeObject("GRAPHIC", (600, 100 + i * 200, 300, 150)))
			for i in range(30)
		]
		got = []
		run(Collector(FakeDocument({"link": links, "graphic": pictures}), lambda p, w: got.append(p)))
		leaves = got[0]["leaves"]["leaves"]
		self.assertLessEqual(len(leaves), 300)
		self.assertEqual(sum(1 for leaf in leaves if leaf["type"] == "graphic"), 30)
		linkTops = [leaf["rect"][1] for leaf in leaves if leaf["type"] == "link"]
		self.assertGreater(max(linkTops), 8000, "links from the bottom of the page too")
		self.assertIn("sampled", got[0]["incomplete"])

	def test_countsAreExactAndTabsAndTogglesAreLocated(self):
		"""Fields, buttons, tabs and expandable headers counted from the buffer for each area."""

		def expandable(item, state):
			item.textInfo._getControlFieldAttribs = lambda *ident: {
				"states": {type("S", (), {"name": state})()}
			}
			return item

		main = FakeItem(
			2, 100, 1000, FakeObject("LANDMARK", (0, 190, 1920, 800), attributes={"xml-roles": "main"})
		)
		plainButton = FakeItem(20, 120, 121, FakeObject("BUTTON", (300, 300, 80, 30)))
		toggles = [
			expandable(
				FakeItem((1, 30 + i), 300 + i, 301 + i, FakeObject("BUTTON", (100, 400 + i * 80, 900, 50))),
				"COLLAPSED",
			)
			for i in range(2)
		]
		tabs = [
			FakeItem(40 + i, 400 + i, 401 + i, FakeObject("TAB", (100 + i * 130, 250, 120, 40)))
			for i in range(2)
		]
		fieldItems = [
			FakeItem(10, 110, 111, FakeObject("EDIT", (100, 300, 300, 30))),
			FakeItem(11, 120, 121, FakeObject("BUTTON", (300, 300, 80, 30))),
		]
		document = FakeDocument(
			{
				"landmark": [main],
				"button": [plainButton, *toggles],
				"formField": fieldItems,
				"tab": tabs,
			},
		)
		got = []
		run(Collector(document, lambda p, w: got.append(p)))
		records = got[0]["candidates"]["records"]
		landmark = [r for r in records if r["type"] == "landmark"][0]
		self.assertEqual(
			(landmark["fields"], landmark["buttons"], landmark["tabs"], landmark["sections"]),
			(1, 3, 2, 2),
			"the button among the form fields is a button, not a field",
		)
		self.assertEqual(
			sorted(r["type"] for r in records if r["type"] in ("tab", "toggle")),
			["tab", "tab", "toggle", "toggle"],
		)
		kinds = {leaf["start"]: leaf["type"] for leaf in got[0]["leaves"]["leaves"]}
		self.assertEqual(kinds.get(120), "button")

	def test_cancelledNeverReports(self):
		got = []
		collector = Collector(self.landmarkPage(), lambda p, w: got.append(p))
		collector.start()
		_stubs.callAfterQueue.flush()
		collector.cancel()
		while _stubs.callAfterQueue.pending:
			_stubs.callAfterQueue.flush()
		self.assertEqual(got, [])

	def test_pageWithoutLandmarksWalksTheTree(self):
		cells = [
			FakeObject("TABLECELL", (100 + i * 400, 150, 380, 600), attributes={"tag": "td"})
			for i in range(3)
		]
		row = FakeObject("TABLEROW", (100, 150, 1180, 600), attributes={"tag": "tr"}, children=cells)
		table = FakeObject("TABLE", (100, 150, 1180, 600), attributes={"tag": "table"}, children=[row])
		root = FakeObject("DOCUMENT", (0, 100, 1920, 900), children=[table])
		got = []
		run(Collector(FakeDocument({}, root), lambda p, w: got.append(p)))
		nodes = got[0]["tree"]["nodes"]
		self.assertEqual(
			[n["role"] for n in nodes],
			["DOCUMENT", "TABLE", "TABLEROW", "TABLECELL", "TABLECELL", "TABLECELL"],
		)
		self.assertEqual([n["depth"] for n in nodes], [0, 1, 2, 3, 3, 3])
		self.assertEqual(nodes[3]["rect"], [100, 50, 380, 600])

	def tablePage(self):
		cells = [
			FakeObject("TABLECELL", (100 + i * 400, 150, 380, 600), attributes={"tag": "td"})
			for i in range(3)
		]
		row = FakeObject("TABLEROW", (100, 150, 1180, 600), attributes={"tag": "tr"}, children=cells)
		table = FakeObject("TABLE", (100, 150, 1180, 600), attributes={"tag": "table"}, children=[row])
		return FakeObject("DOCUMENT", (0, 100, 1920, 900), children=[table]), cells

	def test_theTreeIsSavedWithWhereEachObjectIsInTheText(self):
		"""So an area found from it can be moved to, as one found from a landmark can."""
		root, cells = self.tablePage()
		got = []
		run(Collector(FakePlacedDocument(root), lambda p, w: got.append(p)))
		nodes = got[0]["tree"]["nodes"]
		cell = nodes[3]
		self.assertEqual(cell["ident"], [1, id(cells[0]) % 100000])
		self.assertEqual(cell["start"], FakePlacedDocument.startOf(cells[0]))

	def test_aPageWithoutLandmarksHasAreasToMoveTo(self):
		root, cells = self.tablePage()
		got = []
		run(Collector(FakePlacedDocument(root), lambda p, w: got.append(p)))
		page = findRegions(*fromProbe(got[0]))
		areas = [r for r in page.walk() if r is not page]
		self.assertTrue(areas, describe(page))
		for area in areas:
			with self.subTest(area=area.title):
				self.assertIsNotNone(area.start)
				self.assertIsNotNone(area.anchor)


class FakePlacedDocument(FakeDocument):
	"""A browse mode document that can say where an object of its tree is in its text."""

	def __init__(self, tree):
		super().__init__({}, tree)

	@staticmethod
	def startOf(obj) -> int:
		return 10 + obj.location[0] // 10 + obj.location[1] // 100

	def getIdentifierFromNVDAObject(self, obj):
		self.objects = getattr(self, "objects", {})
		ident = (1, id(obj) % 100000)
		self.objects[ident] = obj
		return ident

	def makeTextInfo(self, position):
		document = self

		class Info:
			def _getOffsetsFromFieldIdentifier(self, docHandle, ID):
				obj = document.objects[(docHandle, ID)]
				start = FakePlacedDocument.startOf(obj)
				return start, start + 5

		return Info()


if __name__ == "__main__":
	unittest.main()
