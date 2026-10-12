# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for finding the main areas of a page and where they are.

Two kinds of test. The small ones build a page by hand to pin down one rule each: the scroll
estimate, hidden elements, sticky headers, boxes drawn around content, search forms, and the
geometric split. The fixture ones run real pages saved by `spikes/layoutOverviewProbe.py` and
check that each comes out the shape a sighted reader would describe. The fixtures are the
point: every rule here exists because one of those pages broke the version before it, and the
probe findings in `docs/design/layout-overview-plan.md` say which.
"""

import json
import os
import unittest

from ._stubs import installStubs

installStubs()

from brlMultiline.layoutRegions import (  # noqa: E402
	Box,
	Node,
	Region,
	describe,
	estimateScroll,
	findRegions,
	fromProbe,
	splitByGeometry,
	whereWords,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "layout")
VIEW = Box(0, 0, 1920, 911)


def load(name: str) -> Region:
	with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
		root, viewport = fromProbe(json.load(f))
	return findRegions(root, viewport)


def ofKind(region: Region, kind: str) -> list[Region]:
	return [r for r in region.walk() if r.kind == kind]


def leaf(role: str, left: int, top: int, width: int, height: int, name: str = "") -> Node:
	return Node(role, Box(left, top, width, height), name=name)


class TestBox(unittest.TestCase):
	def test_unionAndIntersection(self):
		a, b = Box(0, 0, 10, 10), Box(5, 5, 10, 10)
		self.assertEqual(a.union(b), Box(0, 0, 15, 15))
		self.assertEqual(a.intersection(b), Box(5, 5, 5, 5))
		self.assertIsNone(a.intersection(Box(20, 20, 5, 5)))

	def test_whereWords(self):
		page = Box(0, 0, 1000, 1000)
		self.assertEqual(whereWords(Box(0, 0, 1000, 100), page), "across the top")
		self.assertEqual(whereWords(Box(0, 0, 200, 1000), page), "down the left side")
		self.assertEqual(whereWords(Box(800, 0, 150, 100), page), "top right")
		self.assertEqual(whereWords(Box(400, 400, 200, 200), page), "in the centre")


class TestScroll(unittest.TestCase):
	def test_topmostWideElementGivesTheScroll(self):
		root = Node(
			"document", children=[leaf("section", 0, -912, 1905, 35), leaf("section", 0, 0, 1905, 93)]
		)
		self.assertEqual(estimateScroll(root, VIEW), 912)

	def test_narrowSkipLinkParkedAboveIsIgnored(self):
		"""WSJ parks a skip to search button 939 pixels above a page that is not scrolled."""
		root = Node(
			"document", children=[leaf("button", 100, -939, 320, 51), leaf("section", 0, 0, 1905, 32)]
		)
		self.assertEqual(estimateScroll(root, VIEW), 0)


class TestHidden(unittest.TestCase):
	def test_hiddenThingsAreNotAreas(self):
		main = Node(
			"landmark",
			Box(0, 0, 1900, 900),
			landmark="main",
			children=[
				leaf("heading", 10, 10, 1, 1, "visually hidden"),
				leaf("link", -15440, 10, 80, 20),
				leaf("listitem", 0, 10, 15994, 38),
				leaf("link", 10, 50, 100, 20),
			],
		)
		page = findRegions(Node("document", children=[main]), VIEW)
		self.assertEqual([n.rect for n in main.children], [Box(10, 50, 100, 20)])
		self.assertEqual(ofKind(page, "main")[0].box, Box(0, 0, 1900, 900))


class TestSticky(unittest.TestCase):
	def test_stickyHeaderGoesBackIntoItsGap(self):
		"""Fabula Coffee scrolled one screen: the banner reports where it is on screen."""
		bar = Node("landmark", Box(0, -912, 1905, 35), landmark="region", name="Announcement bar")
		nav = Node("landmark", Box(500, 30, 900, 32), landmark="navigation", links=6)
		banner = Node("landmark", Box(0, 0, 1905, 93), landmark="banner", children=[nav])
		main = Node("landmark", Box(0, -784, 1905, 7656), landmark="main")
		page = findRegions(Node("document", children=[bar, banner, main]), VIEW)
		found = ofKind(page, "banner")[0]
		self.assertEqual(found.box, Box(0, 35, 1905, 93))
		self.assertEqual(ofKind(found, "navigation")[0].box.top, 65, "its contents move with it")

	def test_notScrolledMeansNothingMoves(self):
		banner = Node("landmark", Box(0, 35, 1905, 93), landmark="banner")
		main = Node("landmark", Box(0, 128, 1905, 7000), landmark="main")
		page = findRegions(Node("document", children=[banner, main]), VIEW)
		self.assertEqual(ofKind(page, "banner")[0].box, Box(0, 35, 1905, 93))


class TestContentBoxes(unittest.TestCase):
	def test_wideFormHoldingANarrowColumnIsDrawnNarrow(self):
		"""The favorites page: a form as wide as the page, everything in it 320 pixels wide."""
		links = [leaf("link", 13, 60 + i * 30, 300, 18, f"link {i}") for i in range(30)]
		form = Node("form", Box(0, 0, 1889, 1404), landmark="form", children=links)
		page = findRegions(Node("document", children=[form]), VIEW)
		self.assertEqual(len(page.children), 1)
		self.assertLessEqual(page.children[0].box.width, 320)

	def test_landmarkKeepsItsOwnRectangle(self):
		"""Seeking Alpha's sidebar runs the full page height with its links at the top."""
		nav = Node("landmark", Box(0, 68, 240, 843), landmark="navigation", links=18)
		aside = Node("landmark", Box(0, 0, 240, 16280), landmark="complementary", children=[nav])
		page = findRegions(Node("document", children=[aside]), VIEW)
		self.assertEqual(ofKind(page, "complementary")[0].box.height, 16280)


class TestSearch(unittest.TestCase):
	def test_formWhoseTextSaysSearch(self):
		"""Hacker News: a field with no name, beside the words "Search:"."""
		form = Node(
			"form",
			Box(149, 1207, 1607, 21),
			landmark="form",
			children=[
				leaf("statictext", 853, 1209, 58, 16, "Search: "),
				leaf("editabletext", 910, 1207, 142, 21),
			],
		)
		page = findRegions(Node("document", children=[form]), VIEW)
		search = ofKind(page, "search")
		self.assertEqual(len(search), 1)
		self.assertEqual(search[0].box, Box(853, 1207, 199, 21), "drawn around the field, not the form")

	def test_pageWrappedInAFormIsNotASearchForm(self):
		field = Node("editabletext", Box(15, 15, 212, 21), name="Search", isSearch=True)
		links = [leaf("link", 13, 60 + i * 30, 300, 18) for i in range(30)]
		form = Node("form", Box(0, 0, 1889, 1404), landmark="form", children=[field, *links])
		page = findRegions(Node("document", children=[form]), VIEW)
		search = ofKind(page, "search")
		self.assertEqual(len(search), 1)
		self.assertLess(search[0].box.area, 10000)


class TestRepeats(unittest.TestCase):
	def test_rowsOfAListAreNotAreas(self):
		rows = [
			Node(
				"tablerow", Box(149, 42 + i * 35, 648, 19), children=[leaf("link", 160, 42 + i * 35, 400, 16)]
			)
			for i in range(30)
		]
		table = Node("table", Box(149, 42, 648, 1076), children=rows)
		page = findRegions(Node("document", children=[table]), VIEW)
		self.assertEqual(page.children[0].children, [])


class TestGeometry(unittest.TestCase):
	def test_columnsSeparatedByGutters(self):
		items = []
		for column in range(4):
			for row in range(5):
				items.append((Box(320 * column + 20, 100 * row, 280, 60), leaf("link", 0, 0, 1, 1)))
		blocks = splitByGeometry(items, Box(0, 0, 1280, 500))
		self.assertEqual(len(blocks), 4)
		self.assertEqual([b.box.left for b in blocks], [20, 340, 660, 980])

	def test_aPictureAcrossTwoColumnsDoesNotJoinThem(self):
		items = [(Box(20, 100 * r, 280, 60), leaf("link", 0, 0, 1, 1)) for r in range(1, 6)]
		items += [(Box(340, 100 * r, 280, 60), leaf("link", 0, 0, 1, 1)) for r in range(1, 6)]
		items += [(Box(660, 100 * r, 280, 60), leaf("link", 0, 0, 1, 1)) for r in range(1, 6)]
		items += [(Box(20, 0, 600, 60), leaf("graphic", 0, 0, 1, 1))]
		blocks = splitByGeometry(items, Box(0, 0, 960, 600))
		lefts = sorted(b.box.left for b in blocks)
		self.assertEqual(lefts, [20, 340, 660])
		self.assertTrue(all(b.box.width <= 300 for b in blocks), [b.box for b in blocks])

	def test_blocksAreNamedByTheirFirstHeading(self):
		items = [
			(Box(0, 0, 200, 30), leaf("heading", 0, 0, 200, 30, "Latest News")),
			*((Box(0, 40 + 30 * i, 200, 20), leaf("link", 0, 0, 1, 1)) for i in range(4)),
			(Box(400, 0, 200, 30), leaf("heading", 400, 0, 200, 30, "Opinion")),
			*((Box(400, 40 + 30 * i, 200, 20), leaf("link", 0, 0, 1, 1)) for i in range(4)),
		]
		blocks = splitByGeometry(items, Box(0, 0, 600, 200))
		self.assertEqual([b.label for b in blocks], ["Latest News", "Opinion"])


class TestRealPages(unittest.TestCase):
	"""Pages saved by the probe, each checked for the shape a sighted reader would describe."""

	def test_fabulaScrolledInChrome(self):
		page = load("fabula-chrome-scrolled.json")
		banner = ofKind(page, "banner")[0]
		main = ofKind(page, "main")[0]
		self.assertEqual(banner.box.top, 35, "the sticky header is back under the announcement bar")
		self.assertLessEqual(banner.box.bottom, main.box.top)
		self.assertTrue(ofKind(banner, "navigation"))
		self.assertTrue(ofKind(banner, "search"), "search is a button that opens a panel")

	def test_fabulaAtTheTopInFirefox(self):
		page = load("fabula-firefox-top.json")
		self.assertEqual([r.kind for r in page.children], ["region", "banner", "main", "contentinfo"])
		self.assertEqual(ofKind(page, "banner")[0].box.top, 35)

	def test_seekingAlphaSidebar(self):
		page = load("seekingalpha.json")
		aside = ofKind(page, "complementary")[0]
		self.assertEqual(aside.box.left, 0)
		self.assertLessEqual(aside.box.width, 250)
		self.assertGreaterEqual(aside.box.height, page.box.height * 0.9)
		self.assertTrue(ofKind(aside, "navigation"))
		self.assertGreaterEqual(ofKind(page, "banner")[0].box.left, 230)
		self.assertIn("complementary: down the left side", describe(page))

	def test_seekingAlphaMainOpensInTwoColumns(self):
		main = ofKind(load("seekingalpha.json"), "main")[0]
		left = [c for c in main.children if c.box.right < 900]
		right = [c for c in main.children if c.box.left > 880]
		self.assertTrue(left and right)
		self.assertTrue(left[0].box.overlapY(right[0].box))

	def test_wsjDropsStoryLabelsAndFindsColumns(self):
		page = load("wsj.json")
		navigations = ofKind(page, "navigation")
		self.assertTrue(all(n.box.height >= 12 for n in navigations), "story labels are 11 pixels tall")
		self.assertIn("Primary", [n.label for n in navigations])
		main = ofKind(page, "main")[0]
		lefts = {r.box.left for r in main.walk() if r.source == "geometry"}
		for column in (322, 642, 962, 1282):
			self.assertTrue(
				any(abs(left - column) <= 20 for left in lefts), f"no column near {column}: {sorted(lefts)}"
			)

	def test_favoritesIsOneNarrowColumn(self):
		page = load("favorites.json")
		self.assertEqual(len(page.children), 1)
		column = page.children[0]
		self.assertEqual((column.kind, column.label), ("main", "Favorites"))
		self.assertLessEqual(column.box.width, 400)
		self.assertEqual(len(ofKind(page, "search")), 1)
		self.assertEqual(describe(page)[0], "main content, Favorites: down the left side")

	def test_hackerNews(self):
		page = load("hackernews.json")
		self.assertEqual(
			[line for line in describe(page) if not line.startswith(" ")],
			["header: across the top", "main content, table: down the left side", "footer: bottom centre"],
		)
		footer = ofKind(page, "footer")[0]
		self.assertTrue(ofKind(footer, "search"))
		self.assertLess(len(ofKind(page, "main")[0].children), 6, "the story rows are not areas")

	def test_w3cAccessiblePageByIds(self):
		page = load("w3c-bad-after.json")
		inner = [r for r in ofKind(page, "main") if r.source == "id"][0]
		nav = ofKind(inner, "navigation")[0]
		content = ofKind(inner, "content")[0]
		self.assertLessEqual(nav.box.right, content.box.left, "navigation beside the content")
		footer = [r for r in ofKind(page, "footer") if r.box.width < 800][0]
		self.assertGreaterEqual(
			footer.box.top, inner.box.bottom - 2, "the footer below, not behind, the columns"
		)

	def test_w3cInaccessiblePageByLayoutTables(self):
		page = load("w3c-bad-before.json")
		rows = [r for r in page.walk() if sorted(round(c.box.width / 10) for c in r.children) == [15, 16, 44]]
		self.assertEqual(len(rows), 1, "the three columns of the inner layout table")
		columns = sorted(rows[0].children, key=lambda c: c.box.left)
		self.assertEqual(columns[1].kind, "main", "the middle cell has the id main")


if __name__ == "__main__":
	unittest.main()
