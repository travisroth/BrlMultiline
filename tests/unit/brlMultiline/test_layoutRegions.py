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
	countWords,
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
		self.assertEqual(whereWords(Box(400, 400, 200, 200), page), "in the center")


class TestScroll(unittest.TestCase):
	def test_topmostWideElementGivesTheScroll(self):
		root = Node(
			"document", children=[leaf("section", 0, -912, 1905, 35), leaf("section", 0, 0, 1905, 93)],
		)
		self.assertEqual(estimateScroll(root, VIEW), 912)

	def test_narrowSkipLinkParkedAboveIsIgnored(self):
		"""WSJ parks a skip to search button 939 pixels above a page that is not scrolled."""
		root = Node(
			"document", children=[leaf("button", 100, -939, 320, 51), leaf("section", 0, 0, 1905, 32)],
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
				"tablerow", Box(149, 42 + i * 35, 648, 19), children=[leaf("link", 160, 42 + i * 35, 400, 16)],
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


def regionOf(page: Region, node: Node) -> Region | None:
	return next((r for r in page.walk() if r.node is node), None)


def allInside(region: Region) -> bool:
	"""Whether every region lies inside the one holding it, all the way down."""
	for child in region.children:
		box, outer = child.box, region.box
		if (
			box.left < outer.left
			or box.top < outer.top
			or box.right > outer.right
			or box.bottom > outer.bottom
		):
			return False
		if not allInside(child):
			return False
	return True


class TestReviewFindings(unittest.TestCase):
	"""The six faults found in review of the first version, each as the page that showed it."""

	def test_oneLandmarkDoesNotHideUnmarkedContent(self):
		"""A navigation landmark, then a large area of content no landmark marks."""
		nav = Node(
			"landmark",
			Box(0, 0, 1900, 60),
			landmark="navigation",
			children=[leaf("link", 20 + i * 120, 20, 100, 20) for i in range(6)],
		)
		content = [leaf("heading", 100, 150, 600, 40, "Welcome")]
		content += [leaf("link", 100, 220 + i * 40, 700, 20) for i in range(14)]
		page = findRegions(Node("document", children=[nav, *content]), VIEW)
		unmarked = [r for r in page.children if r.kind != "navigation"]
		self.assertTrue(unmarked, describe(page))
		self.assertGreaterEqual(max(r.box.height for r in unmarked), 500)
		self.assertIn("navigation", [r.kind for r in page.children])

	def test_aKnownRegionStaysInsideItsBlock(self):
		"""A wide table above two columns: no cut may leave it outside the block holding it."""
		table = Region("table", "", Box(20, 0, 600, 60), "structure")
		items = [(table.box, table)]
		for column in (20, 340):
			items += [(Box(column, 120 + 40 * i, 280, 20), leaf("link", 0, 0, 1, 1)) for i in range(9)]
		bounds = Box(0, 0, 640, 500)
		blocks = splitByGeometry(items, bounds)
		self.assertTrue(blocks)
		holder = Region("area", "", bounds, "test", children=blocks)
		self.assertTrue(allInside(holder), [(b.box, [c.box for c in b.children]) for b in blocks])
		self.assertIn(table, list(holder.walk()))

	def test_reorderedSectionsAreNotTakenForAStickyHeader(self):
		"""Two ordinary sections shown in the opposite order to reading order, page scrolled."""
		second = Node("section", Box(0, -800, 1900, 100), children=[leaf("graphic", 0, -800, 1900, 100)])
		first = Node("section", Box(0, -900, 1900, 1000), children=[leaf("graphic", 0, -900, 1900, 1000)])
		page = findRegions(Node("document", children=[second, first]), VIEW, scroll=1000)
		self.assertEqual(regionOf(page, second).box.top, 200)

	def test_namedSectionsOfOneSizeAreNotRepeats(self):
		"""Six sections the same size, each with its own heading, are six areas."""
		sections = [
			Node(
				"section",
				Box(100, 100 + i * 150, 1200, 140),
				children=[
					leaf("heading", 110, 110 + i * 150, 400, 30, f"Part {i}"),
					leaf("paragraph", 110, 150 + i * 150, 1100, 80, "Text"),
				],
			)
			for i in range(6)
		]
		holder = Node("section", Box(0, 0, 1900, 1100), children=sections)
		page = findRegions(Node("document", children=[holder]), VIEW)
		kept = [r for r in page.walk() if r.node in sections]
		self.assertEqual(len(kept), 6, describe(page, 3))

	def test_aWideHiddenControlDoesNotSetTheScroll(self):
		root = Node(
			"document",
			children=[
				leaf("link", 0, -9999, 1000, 40, "Skip to content"),
				leaf("section", 0, 0, 1900, 100),
				leaf("section", 0, 100, 1900, 800),
			],
		)
		self.assertEqual(estimateScroll(root, VIEW), 0)

	def test_anIsolatedContainerFarAboveIsAnOutlier(self):
		root = Node(
			"document",
			children=[
				leaf("section", 0, -9999, 1900, 40),
				leaf("section", 0, 0, 1900, 100),
				leaf("section", 0, 100, 1900, 800),
			],
		)
		self.assertEqual(estimateScroll(root, VIEW), 0)

	def test_researchIsNotSearch(self):
		form = Node(
			"form",
			Box(100, 100, 600, 200),
			landmark="form",
			name="Research grants",
			children=[
				leaf("statictext", 110, 110, 200, 20, "Research grants"),
				leaf("editabletext", 110, 140, 300, 24, "Email"),
			],
		)
		page = findRegions(Node("document", children=[form]), VIEW)
		self.assertEqual(ofKind(page, "search"), [])


class TestReviewFollowUps(unittest.TestCase):
	"""Three faults found in review of the fixes for the first six."""

	def test_contentOnEverySideOfALandmarkIsKept(self):
		"""A narrow navigation in the middle, content packed above, below and to both sides, with
		no straight gutter separating it all."""
		nav = Node(
			"landmark",
			Box(800, 300, 300, 300),
			landmark="navigation",
			children=[leaf("link", 810, 310 + i * 40, 280, 20) for i in range(6)],
		)
		content = [leaf("link", 100 + i * 180, 120 + r * 60, 160, 40) for i in range(10) for r in range(3)]
		content += [leaf("link", 100 + i * 180, 610 + r * 60, 160, 40) for i in range(10) for r in range(3)]
		content += [leaf("link", 100 + i * 180, 300 + r * 60, 160, 40) for i in range(4) for r in range(5)]
		content += [leaf("link", 1140 + i * 180, 300 + r * 60, 160, 40) for i in range(4) for r in range(5)]
		page = findRegions(Node("document", children=[nav, *content]), VIEW)
		others = [r for r in page.walk() if r is not page and r.kind != "navigation"]
		self.assertTrue(any(r.box.right <= 800 and r.box.top >= 290 for r in others), describe(page, 3))
		self.assertTrue(any(r.box.left >= 1100 and r.box.top >= 290 for r in others), describe(page, 3))
		self.assertFalse(any(r.box.intersection(nav.box) for r in others if r.kind == "area"))

	def test_areasInsideAStickyHeaderKeepItsScreenPosition(self):
		"""A tall banner pinned at the top of a scrolled page, its links found as two columns."""
		links = [leaf("link", 40, 20 + i * 20, 500, 16) for i in range(8)]
		links += [leaf("link", 1000, 20 + i * 20, 500, 16) for i in range(8)]
		banner = Node("landmark", Box(0, 0, 1900, 200), landmark="banner", children=links)
		main = Node("landmark", Box(0, -800, 1900, 5000), landmark="main")
		page = findRegions(Node("document", children=[banner, main]), VIEW, scroll=1000)
		found = ofKind(page, "banner")[0]
		self.assertEqual(found.screenBox.top, 0)
		inner = [r for r in found.walk() if r is not found]
		self.assertTrue(inner, "the banner's links were split into columns")
		for region in inner:
			self.assertGreaterEqual(region.screenBox.top, 0, region)
			self.assertLess(region.screenBox.bottom, 200, region)

	def test_aGeometricParentStartsAtItsEarliestChild(self):
		"""A table starting at offset 10 beside links starting at 100, in one band."""
		table = Node(
			"table",
			Box(100, 100, 500, 300),
			isDataTable=True,
			start=10,
			end=90,
			children=[Node("link", Box(110, 110, 200, 20), start=12)],
		)
		beside = [Node("link", Box(1000, 100 + i * 30, 500, 20), start=100 + i) for i in range(8)]
		below = [Node("link", Box(100 + i * 200, 700, 180, 20), start=500 + i) for i in range(8)]
		section = Node("section", Box(0, 0, 1900, 900), children=[table, *beside, *below])
		page = findRegions(Node("document", children=[section]), VIEW)
		holders = [r for r in page.walk() if any(c.node is table for c in r.children)]
		self.assertTrue(holders, describe(page, 3))
		self.assertEqual(holders[0].start, 10)
		for region in page.walk():
			starts = [c.start for c in region.children if c.start is not None]
			if region.start is not None and starts:
				self.assertLessEqual(region.start, min(starts), region)


def tab(left, top, width=120, height=40, start=0, name="Tab"):
	return Node("tab", Box(left, top, width, height), name=name, start=start)


def header(left, top, width=900, height=50, start=0):
	return Node("button", Box(left, top, width, height), name="Section", start=start, expandable=True)


def inMain(*children):
	main = Node("landmark", Box(0, 0, 1900, 1500), landmark="main", children=list(children))
	return findRegions(Node("document", children=[main]), VIEW), main


class TestTabStripsAndAccordions(unittest.TestCase):
	"""Two kinds of area nothing else marks, found from what they are made of."""

	def test_tabsInARowAreATabStrip(self):
		page, _ = inMain(*(tab(100 + i * 130, 200, start=10 + i) for i in range(4)))
		strips = [r for r in page.walk() if r.kind == "tabs"]
		self.assertEqual(len(strips), 1)
		self.assertEqual((strips[0].tabs, strips[0].box), (4, Box(100, 200, 510, 40)))
		self.assertEqual(strips[0].start, 10)

	def test_tabsDownAColumnAreATabStrip(self):
		page, _ = inMain(*(tab(100, 200 + i * 50, width=200, start=i) for i in range(3)))
		self.assertEqual([r.tabs for r in page.walk() if r.kind == "tabs"], [3])

	def test_oneTabIsNotAStrip(self):
		page, _ = inMain(tab(100, 200))
		self.assertEqual([r for r in page.walk() if r.kind == "tabs"], [])

	def test_theAreaAroundAStripDoesNotCountItsTabsAgain(self):
		page, _ = inMain(*(tab(100 + i * 130, 200, start=i) for i in range(4)))
		main = [r for r in page.walk() if r.kind == "main"][0]
		self.assertEqual(main.tabs, 0)

	def test_expandableHeadersStackedAreAnAccordion(self):
		page, _ = inMain(*(header(100, 300 + i * 120, start=i) for i in range(4)))
		accordions = [r for r in page.walk() if r.kind == "accordion"]
		self.assertEqual(len(accordions), 1)
		self.assertEqual(accordions[0].sections, 4)
		self.assertEqual(accordions[0].box, Box(100, 300, 900, 410), "from the first header to the last")

	def test_aRowOfMenusIsNotAnAccordion(self):
		"""Buttons that expand along a site's top run across, not down."""
		page, _ = inMain(*(header(100 + i * 160, 20, width=140, height=30, start=i) for i in range(5)))
		self.assertEqual([r for r in page.walk() if r.kind == "accordion"], [])

	def test_headersOfDifferentWidthsAreNotOneAccordion(self):
		page, _ = inMain(header(100, 300), header(100, 400, width=300))
		self.assertEqual([r for r in page.walk() if r.kind == "accordion"], [])


class TestFormsAndCounts(unittest.TestCase):
	def form(self, fieldCount, name=""):
		fields = [leaf("editabletext", 120, 200 + i * 50, 400, 30) for i in range(fieldCount)]
		heading = leaf("heading", 120, 120, 300, 40, "Contact us")
		return Node(
			"form",
			Box(100, 100, 700, 600),
			landmark="form",
			name=name,
			children=[heading, *fields, leaf("button", 120, 600, 120, 40, "Send")],
		)

	def test_aFormWithSeveralFieldsIsAnArea(self):
		page = findRegions(Node("document", children=[self.form(3)]), VIEW)
		forms = [r for r in page.walk() if r.kind == "form"]
		self.assertEqual(len(forms), 1)
		self.assertEqual((forms[0].label, forms[0].fields, forms[0].buttons), ("Contact us", 3, 1))

	def test_aFormAroundOneFieldIsNot(self):
		page = findRegions(Node("document", children=[self.form(1)]), VIEW)
		self.assertEqual([r for r in page.walk() if r.kind == "form"], [])

	def test_countsGoMostTellingFirst(self):
		region = Region("main", "", Box(0, 0, 10, 10), "landmark", links=8)
		region.fields, region.buttons, region.tabs, region.sections = 3, 2, 5, 4
		self.assertEqual(
			[name for name, _ in region.counts],
			["tabs", "sections", "fields", "buttons", "links"],
		)

	def test_oneOfAnythingIsSingular(self):
		self.assertEqual(
			[countWords(n, 1) for n in ("tabs", "sections", "fields", "buttons", "links")],
			["1 tab", "1 section", "1 field", "1 button", "1 link"],
		)
		self.assertEqual(countWords("links", 8), "8 links")

	def test_exactCountsFromTheCollectorWin(self):
		"""Counted from browse mode's text, not added up from the few leaves sampled."""
		data = {
			"candidates": {
				"viewport": [0, 0, 1920, 911],
				"records": [
					{
						"type": "landmark",
						"xmlRoles": "main",
						"rect": [0, 0, 1900, 900],
						"start": 0,
						"end": 1000,
						"links": 40,
						"textLength": 900,
						"fields": 6,
						"buttons": 3,
						"tabs": 0,
						"sections": 0,
					},
				],
			},
			"leaves": {"leaves": [{"type": "formField", "start": 5, "rect": [10, 10, 100, 20]}]},
			"tree": {"nodes": []},
		}
		page = findRegions(*fromProbe(data))
		main = page.children[0]
		self.assertEqual((main.links, main.fields, main.buttons), (40, 6, 3))

	def test_tabsAndTogglesFromTheCollectorBecomeAStripAndAnAccordion(self):
		records = [
			{"type": "landmark", "xmlRoles": "main", "rect": [0, 0, 1900, 1400], "start": 0, "end": 1000},
		]
		records += [
			{
				"type": "tab",
				"label": f"Tab {i}",
				"rect": [100 + i * 130, 100, 120, 40],
				"start": 10 + i,
				"end": 11 + i,
			}
			for i in range(3)
		]
		records += [
			{
				"type": "toggle",
				"label": "More",
				"rect": [100, 400 + i * 120, 900, 50],
				"start": 50 + i,
				"end": 51 + i,
			}
			for i in range(3)
		]
		data = {"candidates": {"viewport": [0, 0, 1920, 911], "records": records}, "leaves": {"leaves": []}}
		page = findRegions(*fromProbe(data))
		self.assertEqual(
			sorted(r.kind for r in page.walk() if r.kind in ("tabs", "accordion")), ["accordion", "tabs"],
		)


class TestLiveReviewFindings(unittest.TestCase):
	"""Faults found in review of the live collection and the counts, in the rules' part."""

	def test_unmarkedContentBesideALandmarkCountsItsFieldsAndButtons(self):
		"""A navigation landmark, then six fields and two buttons no landmark marks."""
		nav = Node(
			"landmark",
			Box(0, 0, 1900, 60),
			landmark="navigation",
			children=[leaf("link", 20 + i * 120, 20, 100, 20) for i in range(6)],
		)
		fields = [leaf("editabletext", 100, 200 + i * 60, 600, 30) for i in range(6)]
		buttons = [leaf("button", 100 + i * 200, 600, 150, 40) for i in range(2)]
		page = findRegions(Node("document", children=[nav, *fields, *buttons]), VIEW)
		unmarked = [r for r in page.children if r.kind != "navigation"]
		self.assertTrue(unmarked, describe(page))
		self.assertEqual(sum(r.fields for r in unmarked), 6)
		self.assertEqual(sum(r.buttons for r in unmarked), 2)

	def test_aPlainFormRoleWithFieldsIsAForm(self):
		"""The form role alone, with no form landmark attribute, gets the same rule."""
		form = Node(
			"form",
			Box(100, 100, 700, 500),
			children=[leaf("editabletext", 120, 150 + i * 60, 400, 30) for i in range(3)],
		)
		page = findRegions(Node("document", children=[form]), VIEW)
		self.assertEqual([r.kind for r in page.walk() if r.node is form], ["form"])

	def test_tabsEachInAPlainWrapperAreStillAStrip(self):
		"""On a page without landmarks, each tab sits in a list item; the wrappers are layout."""
		items = [
			Node("listitem", Box(100 + i * 160, 200, 150, 50), children=[tab(105 + i * 160, 205, start=i)])
			for i in range(4)
		]
		holder = Node("list", Box(100, 200, 640, 50), children=items)
		section = Node("section", Box(0, 0, 1900, 900), children=[holder, leaf("graphic", 0, 300, 1900, 600)])
		page = findRegions(Node("document", children=[section]), VIEW)
		strips = [r for r in page.walk() if r.kind == "tabs"]
		self.assertEqual([r.tabs for r in strips], [4], describe(page, 4))
		self.assertFalse(
			[
				r
				for r in page.walk()
				if r.kind == "area" and r.box.intersection(strips[0].box) == r.box and r is not page
			],
			"the wrappers inside the strip are not left as areas of their own",
		)

	def test_aTabSampledTwiceIsOneTab(self):
		"""A tab as a record and as a sampled leaf is the same tab, not two side by side."""
		tabs = [tab(100 + i * 130, 200, start=i) for i in range(3)]
		twice = [tab(100 + i * 130, 200, start=i) for i in range(3)]
		page, _ = inMain(*tabs, *twice)
		self.assertEqual([r.tabs for r in page.walk() if r.kind == "tabs"], [3])


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
		top = min(main.children, key=lambda r: r.box.top)
		left = [c for c in top.children if c.box.right < 900]
		right = [c for c in top.children if c.box.left > 880]
		self.assertTrue(left and right, [c.box for c in top.children])
		self.assertTrue(left[0].box.overlapY(right[0].box))
		self.assertTrue(allInside(main), "a wide table below the columns is not cut through")

	def test_wsjDropsStoryLabelsAndFindsColumns(self):
		page = load("wsj.json")
		navigations = ofKind(page, "navigation")
		self.assertTrue(all(n.box.height >= 12 for n in navigations), "story labels are 11 pixels tall")
		self.assertIn("Primary", [n.label for n in navigations])
		main = ofKind(page, "main")[0]
		lefts = {r.box.left for r in main.walk() if r.source == "geometry"}
		for column in (322, 642, 962, 1282):
			self.assertTrue(
				any(abs(left - column) <= 20 for left in lefts), f"no column near {column}: {sorted(lefts)}",
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
			["header: across the top", "main content, table: down the left side", "footer: bottom center"],
		)
		footer = ofKind(page, "footer")[0]
		self.assertTrue(ofKind(footer, "search"))
		self.assertLess(len(ofKind(page, "main")[0].children), 6, "the story rows are not areas")

	def test_wellsFargosSignOnFormIsAnArea(self):
		"""Saved before fields were counted: the form's four fields are added up from what it holds."""
		forms = ofKind(load("wellsfargo.json"), "form")
		self.assertEqual(len(forms), 1)
		self.assertGreaterEqual(forms[0].fields, 3)

	def test_w3cAccessiblePageByIds(self):
		page = load("w3c-bad-after.json")
		inner = [r for r in ofKind(page, "main") if r.source == "id"][0]
		nav = ofKind(inner, "navigation")[0]
		content = ofKind(inner, "content")[0]
		self.assertLessEqual(nav.box.right, content.box.left, "navigation beside the content")
		footer = [r for r in ofKind(page, "footer") if r.box.width < 800][0]
		self.assertGreaterEqual(
			footer.box.top, inner.box.bottom - 2, "the footer below, not behind, the columns",
		)

	def test_w3cInaccessiblePageByLayoutTables(self):
		page = load("w3c-bad-before.json")
		rows = [r for r in page.walk() if sorted(round(c.box.width / 10) for c in r.children) == [15, 16, 44]]
		self.assertEqual(len(rows), 1, "the three columns of the inner layout table")
		columns = sorted(rows[0].children, key=lambda c: c.box.left)
		self.assertEqual(columns[1].kind, "main", "the middle cell has the id main")


if __name__ == "__main__":
	unittest.main()
