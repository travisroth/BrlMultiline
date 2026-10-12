# BrlMultiline: the main areas of a web page and where they are.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The main areas of a page, found from what the browser reports, with no AI.

Browse mode reads a page in one line of reading order, which loses what a sighted reader gets
at a glance: the shape of the page. This module turns what the browser reports about a page
into a small tree of named areas — banner, navigation, main content, a sidebar, a search
field — each with its rectangle on the page, so that the shape can be drawn or described.
`docs/design/layout-overview-plan.md` has the reasoning, and the probe runs it rests on.

It is pure Python with no NVDA imports, so that every rule can be tested against real pages
saved by `spikes/layoutOverviewProbe.py`. The input is a tree of `Node`, which says what the
browser reported about one element; the output is a tree of `Region`, which says what an area
of the page is and where. `fromProbe` builds the input from a saved probe file.

The rules, in the order they are applied:

1. **Positions become page positions.** The browser reports rectangles relative to the visible
   part of the page. The scroll position is not reported, so it is estimated from the topmost
   wide element: when that is above the visible part, the page is scrolled by that much.
2. **Hidden things are dropped.** A visually hidden element still reports a rectangle: one
   pixel square, thousands of pixels wide, or wholly above the top of the page. None of those
   is something a sighted reader sees.
3. **Sticky headers are put back.** A header that stays on screen as the page scrolls reports
   its position on screen, not its place in the page, and lands on top of whatever is there.
   In reading order it still sits between its neighbours, and the gap it left between them is
   its own height, so it goes back into that gap.
4. **Boxes are drawn around content.** A container's own rectangle often lies: a form as wide
   as the page holding a narrow column of links, or a footer that starts behind floated
   columns. So an area's box is the union of what is inside it, inside its own rectangle.
5. **Areas are named from the most trusted signal there is:** a landmark, then search, then an
   element's id (header, nav, main, content, footer, sidebar). Class names are not used: on
   modern sites they are generated or framework names, and they misfire.
6. **Wrappers collapse.** A container that holds one area filling it adds nothing, so the two
   become one, keeping the better name.
7. **An area with no structure inside is split by geometry.** Recursive XY-cut: the widest gap
   of empty space running right across or right down the area splits it, and the parts are
   split again. That finds a news site's columns, which no markup describes.
8. **Unnamed areas at the top are named by position:** a full width band at the top is a
   header, one at the bottom a footer, a tall narrow column at a side a sidebar, and the
   largest of the rest the main content. Inside the page those words would be guesses, so they
   are used only at the top.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, NamedTuple


class Box(NamedTuple):
	"""A rectangle in pixels: left and top edges, then width and height."""

	left: int
	top: int
	width: int
	height: int

	@property
	def right(self) -> int:
		return self.left + self.width

	@property
	def bottom(self) -> int:
		return self.top + self.height

	@property
	def area(self) -> int:
		return max(self.width, 0) * max(self.height, 0)

	def moved(self, dx: int = 0, dy: int = 0) -> "Box":
		return Box(self.left + dx, self.top + dy, self.width, self.height)

	def union(self, other: "Box") -> "Box":
		left, top = min(self.left, other.left), min(self.top, other.top)
		return Box(left, top, max(self.right, other.right) - left, max(self.bottom, other.bottom) - top)

	def intersection(self, other: "Box") -> "Box | None":
		left, top = max(self.left, other.left), max(self.top, other.top)
		right, bottom = min(self.right, other.right), min(self.bottom, other.bottom)
		if right <= left or bottom <= top:
			return None
		return Box(left, top, right - left, bottom - top)

	def overlapX(self, other: "Box") -> int:
		return max(0, min(self.right, other.right) - max(self.left, other.left))

	def overlapY(self, other: "Box") -> int:
		return max(0, min(self.bottom, other.bottom) - max(self.top, other.top))


def unionOf(boxes: Iterable[Box]) -> Box | None:
	result = None
	for box in boxes:
		result = box if result is None else result.union(box)
	return result


#: Roles, as NVDA names them in lower case, that can hold an area of the page. Text, links,
#: headings, pictures and fields are what areas are made of, not areas themselves.
CONTAINER_ROLES = frozenset(
	(
		"document",
		"landmark",
		"region",
		"section",
		"form",
		"table",
		"tablerow",
		"tablecell",
		"article",
		"grouping",
		"textframe",
		"internalframe",
		"frame",
		"list",
		"listitem",
		"application",
		"dialog",
	),
)

#: Roles that are the content of an area: their rectangles say where the content is.
LEAF_ROLES = frozenset(
	(
		"link",
		"heading",
		"graphic",
		"diagram",
		"statictext",
		"editabletext",
		"button",
		"combobox",
		"checkbox",
		"radiobutton",
		"paragraph",
	),
)

#: The ARIA landmarks the overview names. A form is only one when it has a name or holds a
#: search field, and a region only when it has a name; both are checked where they are used.
LANDMARKS = frozenset(
	("banner", "navigation", "main", "complementary", "contentinfo", "search", "form", "region"),
)

#: The landmarks that divide a page, as opposed to a named region or form inside one.
PAGE_LANDMARKS = frozenset(("banner", "navigation", "main", "complementary", "contentinfo"))

#: SVG element tags. Charts drawn in SVG name their series as regions, and a series is not an
#: area of the page.
SVG_TAGS = frozenset(("svg", "g", "path", "rect", "circle", "text", "tspan", "line", "polyline"))

#: Words in an element's id that name what it is. Ids written by hand on older sites are
#: reliable (`header`, `nav`, `main`, `content`, `footer`); see the probe findings.
ID_WORDS = {
	"header": "header",
	"masthead": "header",
	"banner": "header",
	"nav": "navigation",
	"navigation": "navigation",
	"navbar": "navigation",
	"menu": "navigation",
	"mnav": "navigation",
	"main": "main",
	"content": "content",
	"maincontent": "main",
	"footer": "footer",
	"sidebar": "sidebar",
	"aside": "sidebar",
	"search": "search",
}

#: What each kind of region is called when described.
KIND_NAMES = {
	"banner": "banner",
	"navigation": "navigation",
	"main": "main content",
	"complementary": "complementary",
	"contentinfo": "content info",
	"search": "search",
	"form": "form",
	"region": "region",
	"frame": "frame",
	"header": "header",
	"footer": "footer",
	"sidebar": "sidebar",
	"content": "content",
	"table": "table",
	"area": "area",
}

#: Smaller than this in either direction, an area is a line of text or a hidden element, not
#: something to draw a box for.
MIN_SIDE = 16
#: And smaller than this in area, it is a word or an icon.
MIN_AREA = 2000
#: An unnamed area with nothing found inside it must be at least this share of the view.
MIN_PLAIN_SHARE = 0.005
#: How far past the page's edges something can reach before it counts as moved off screen.
OFF_PAGE_SLACK = 0.25


@dataclass(eq=False)
class Node:
	"""One element as the browser reported it.

	`rect` is relative to the top left of the visible part of the page, as the browser gives
	it. `links` and `textLength` are counts the caller already has, such as from the browse mode
	buffer; when they are zero they are worked out from the children.
	"""

	role: str
	rect: Box | None = None
	landmark: str = ""
	name: str = ""
	tag: str = ""
	id: str = ""
	links: int = 0
	textLength: int = 0
	isSearch: bool = False
	#: A table holding data rather than laying out the page. Browse mode only lists these.
	isDataTable: bool = False
	children: list["Node"] = field(default_factory=list)
	# Worked out by `findRegions`: the rectangle on the page, and the box around the content.
	pageRect: Box | None = field(default=None, repr=False)
	box: Box | None = field(default=None, repr=False)

	def walk(self) -> Iterator["Node"]:
		yield self
		for child in self.children:
			yield from child.walk()


@dataclass(eq=False)
class Region:
	"""An area of the page: what it is, what it is called, and where it is on the page."""

	kind: str
	label: str
	box: Box
	source: str
	links: int = 0
	children: list["Region"] = field(default_factory=list)
	#: The node the region was found from, kept so its content can be split by geometry.
	node: Node | None = field(default=None, repr=False)

	@property
	def title(self) -> str:
		"""How the region is spoken: its kind, then its label when that adds something."""
		kindName = KIND_NAMES.get(self.kind, self.kind)
		if self.label and self.label.lower() != kindName:
			return f"{kindName}, {self.label}"
		return kindName

	def walk(self) -> Iterator["Region"]:
		yield self
		for child in self.children:
			yield from child.walk()


def findRegions(root: Node, viewport: Box) -> Region:
	"""The page as a tree of regions, the page itself at the top.

	`viewport` is the visible part of the page on screen, which only its size is used of.
	"""
	scroll = estimateScroll(root, viewport)
	_toPage(root, scroll)
	# Pages scroll down, not across: anything far past the view's width is parked off screen.
	_ = _dropHidden(root, viewport, viewport.width)
	if scroll > 0:
		_restoreSticky(root)
	_countContent(root)
	_contentBoxes(root)
	pageBox = Box(0, 0, viewport.width, max(root.box.bottom if root.box else 0, viewport.height))
	regions = [r for c in root.children for r in _regions(c, viewport)]
	page = Region("page", root.name, pageBox, "page", root.links, regions, root)
	page.children = [_collapse(c) for c in page.children]
	_split(page, viewport)
	page.children = [_collapse(c) for c in page.children]
	_nameByPosition(_topLevel(page), viewport)
	for region in page.walk():
		if not region.label and region.kind != "page" and region.source in ("structure", "position", "id"):
			region.label = _firstHeading(region.node)
		region.label = " ".join(region.label.split())
	return page


def _firstHeading(node: Node | None) -> str:
	"""The first heading inside a node, in reading order, as a name for an unnamed area."""
	if node is None:
		return ""
	return next((n.name for n in node.walk() if n.role == "heading" and n.name.strip()), "")


def estimateScroll(root: Node, viewport: Box) -> int:
	"""How far down the page is scrolled, from the topmost wide element.

	The browser does not say. But the top of the page is at the top of its topmost element,
	and elements narrower than 40 percent of the view are left out, since visually hidden skip
	links are parked far above the page and are narrow; so is anything absurdly wide.
	"""
	tops = [
		n.rect.top
		for n in root.walk()
		if n is not root
		and n.rect
		and viewport.width * 0.4 <= n.rect.width <= viewport.width * 1.5
		and n.rect.height >= 8
	]
	return max(0, -min(tops)) if tops else 0


def _toPage(node: Node, scroll: int) -> None:
	for n in node.walk():
		n.pageRect = n.rect.moved(dy=scroll) if n.rect else None


def _isHidden(rect: Box, viewport: Box, pageWidth: int) -> bool:
	"""Whether a rectangle is one of the ways pages hide things from sight but not from us."""
	if rect.width <= 2 or rect.height <= 2:
		return True
	if rect.width > pageWidth * (1 + OFF_PAGE_SLACK):
		return True
	slack = viewport.width * OFF_PAGE_SLACK
	if rect.right <= -slack or rect.left >= pageWidth + slack:
		return True
	return rect.bottom <= 0


def _dropHidden(node: Node, viewport: Box, pageWidth: int) -> bool:
	"""Remove hidden elements; returns whether anything visible is left under node.

	A hidden container can still hold visible children (an element with `display: contents`
	reports no size at all), so it loses its own rectangle but keeps them.
	"""
	node.children = [c for c in node.children if _dropHidden(c, viewport, pageWidth)]
	if node.pageRect and _isHidden(node.pageRect, viewport, pageWidth):
		node.pageRect = None
	return node.pageRect is not None or bool(node.children)


def _restoreSticky(node: Node) -> None:
	"""Move headers that stay on screen as the page scrolls back to their place in the page.

	Such a header reports where it is on screen, so on a scrolled page it lands on top of what
	follows it in reading order. Its place is the gap between the sibling before it and the one
	after, which on the page it left is its own height.
	"""
	for child in node.children:
		_restoreSticky(child)
	placed = [(c, c.pageRect) for c in node.children if c.pageRect]
	for i in range(len(placed) - 1):
		child, rect = placed[i]
		nextRect = placed[i + 1][1]
		if rect.top <= nextRect.top or not rect.overlapX(nextRect):
			continue
		gapTop = placed[i - 1][1].bottom if i > 0 else 0
		gap = nextRect.top - gapTop
		if gap >= rect.height * 0.8 and rect.height <= gap * 1.25:
			dy = gapTop - rect.top
			for n in child.walk():
				if n.pageRect:
					n.pageRect = n.pageRect.moved(dy=dy)


def _countContent(node: Node) -> None:
	"""Fill in link and text counts the caller did not have, from the children."""
	for child in node.children:
		_countContent(child)
	if node.links or node.textLength:
		return
	if node.role == "link":
		node.links = 1
	if node.role in LEAF_ROLES and not node.children:
		node.textLength = len(node.name)
	node.links += sum(c.links for c in node.children)
	node.textLength += sum(c.textLength for c in node.children)


def _contentBoxes(node: Node) -> None:
	"""Each node's box: the union of its children's boxes inside its own rectangle.

	A landmark keeps its own rectangle. Its author drew that area on purpose, such as a sidebar
	running the full height of the page with its links at the top, and a sighted reader sees
	the column, not just the links.
	"""
	for child in node.children:
		_contentBoxes(child)
	inner = unionOf(c.box for c in node.children if c.box)
	if node.pageRect and _isNamedLandmark(node):
		node.box = node.pageRect
	elif inner and node.pageRect:
		node.box = inner.intersection(node.pageRect) or node.pageRect
	else:
		node.box = inner or node.pageRect


def _isNamedLandmark(node: Node) -> bool:
	"""A landmark that marks an area in its own right, as opposed to an unnamed form or region."""
	if node.landmark not in LANDMARKS or node.tag in SVG_TAGS:
		return False
	return node.landmark not in ("form", "region") or bool(node.name)


def _isSizeable(box: Box | None) -> bool:
	return box is not None and box.width >= MIN_SIDE and box.height >= MIN_SIDE and box.area >= MIN_AREA


def _idKind(node: Node) -> str:
	for word in re.split(r"[-_\s]+|(?<=[a-z])(?=[A-Z])", node.id or ""):
		kind = ID_WORDS.get(word.lower())
		if kind:
			return kind
	return ""


def _mentionsSearch(node: Node) -> bool:
	return "search" in f"{node.name} {node.id}".lower()


#: A form holding more links than this is a page wrapped in a form, not a search form.
SEARCH_FORM_MAX_LINKS = 3


def _isSearchForm(node: Node) -> bool:
	"""A form with a text field whose own words say search, as on Hacker News.

	Some pages wrap everything in one form, so a form with a search field in it is only a
	search form when it holds little else.
	"""
	if node.links > SEARCH_FORM_MAX_LINKS:
		return False
	fields = [n for n in node.walk() if n.role in ("editabletext", "combobox")]
	if not fields:
		return False
	return any(_mentionsSearch(n) for n in node.walk())


def _classify(node: Node) -> tuple[str, str, str]:
	"""What a node is, if anything can be said: (kind, label, source), or empty strings."""
	landmark = node.landmark
	if landmark in LANDMARKS:
		if landmark == "region" and (not node.name or node.tag in SVG_TAGS):
			return "", "", ""
		if landmark == "form":
			if _isSearchForm(node):
				return "search", "", "landmark"
			if not node.name:
				return "", "", ""
		pageRect = node.pageRect
		if pageRect and (pageRect.width < 40 or pageRect.height < 12):
			return "", "", ""
		return landmark, node.name, "landmark"
	if node.isSearch:
		return "search", "", "search"
	if node.role == "form" and _isSearchForm(node):
		return "search", "", "search"
	if node.role in ("internalframe", "frame"):
		return "frame", "", "frame"
	if node.role in CONTAINER_ROLES:
		kind = _idKind(node)
		if kind:
			return kind, "", "id"
	return "", "", ""


def _regions(node: Node, viewport: Box) -> list[Region]:
	"""The regions under node, with node's own region around them if it has one."""
	if node.tag in SVG_TAGS:
		# A drawing's parts, such as a chart's series, are content, not areas of the page.
		return []
	children = [r for c in node.children for r in _regions(c, viewport)]
	if node.box is None:
		return children
	kind, label, source = _classify(node)
	if kind == "search" or (node.isSearch and node.role not in CONTAINER_ROLES):
		# A search field or button is small but always worth a box; the drawing grows it.
		return [Region("search", label or "", node.box, source or "search", node.links, node=node)]
	if node.role not in CONTAINER_ROLES:
		return children
	if not kind and children and all(c.kind == "search" for c in children):
		# A cell or a wrapper around a search field and its button is the search, as long as
		# it is not much more than them.
		inner = unionOf(c.box for c in children)
		if inner and node.box.area <= inner.area * 3:
			return [Region("search", "", node.box, "search", node.links, node=node)]
	if not _isSizeable(node.box):
		return children
	if not kind and not children and not node.isDataTable and node.box.area < viewport.area * MIN_PLAIN_SHARE:
		# A list item holding one link, or a cell holding a picture: content, not an area.
		return children
	if kind == "frame" and node.box.area < viewport.area * 0.1:
		return children
	if not kind and node.isDataTable:
		kind = "table"
	children = _dropRepeats(children)
	return [Region(kind or "area", label, node.box, source or "structure", node.links, children, node)]


#: This many unnamed areas of much the same size side by side or stacked are the items of a
#: list, such as the rows of stories on Hacker News, not areas of the page.
REPEAT_MIN = 6


def _dropRepeats(children: list[Region]) -> list[Region]:
	plain = [c for c in children if c.kind == "area" and not c.children]
	if len(plain) < REPEAT_MIN:
		return children
	widths = sorted(c.box.width for c in plain)
	heights = sorted(c.box.height for c in plain)
	width, height = widths[len(widths) // 2], heights[len(heights) // 2]
	repeats = {
		id(c)
		for c in plain
		if abs(c.box.width - width) <= width * 0.1 and abs(c.box.height - height) <= height * 0.5
	}
	if len(repeats) < REPEAT_MIN:
		return children
	return [c for c in children if id(c) not in repeats]


def _better(outer: Region, inner: Region) -> Region:
	"""Of a wrapper and the one region filling it, the one whose name to keep."""
	rank = {"landmark": 4, "search": 3, "id": 2, "frame": 1}
	innerRank, outerRank = rank.get(inner.source, 0), rank.get(outer.source, 0)
	if innerRank == outerRank:
		# A table, or anything else with a name, says more than a plain area around it.
		return inner if outer.kind == "area" and inner.kind != "area" else outer
	return inner if innerRank > outerRank else outer


def _collapse(region: Region) -> Region:
	"""A region holding a single region that fills most of it becomes that one region."""
	region.children = [_collapse(c) for c in region.children]
	while len(region.children) == 1:
		inner = region.children[0]
		if inner.box.area < region.box.area * 0.9:
			break
		keep = _better(region, inner)
		region = Region(
			keep.kind,
			keep.label,
			region.box,
			keep.source,
			region.links,
			inner.children,
			region.node,
		)
	return region


#: An area at least this share of the view is split by geometry, when it has content enough
#: outside the regions already found in it to be worth splitting.
SPLIT_MIN_SHARE = 0.15
SPLIT_MIN_LEAVES = 8
#: The most parts one cut makes. An overview of a dozen boxes is the most a hand can take in,
#: so a long page is cut at its widest gaps first, and each part is cut again inside.
MAX_PARTS = 5

#: Something an area is split around: the rectangle of a piece of content, or of a region
#: already found inside the area, which is kept whole.
Item = tuple[Box, "Node | Region"]


def _split(region: Region, viewport: Box) -> None:
	"""Split every area with little structure of its own by the gaps between its content."""
	for child in region.children:
		_split(child, viewport)
	if (
		region.kind in ("search", "frame")
		or region.node is None
		or region.box.area < viewport.area * SPLIT_MIN_SHARE
	):
		return
	if any(c.kind in PAGE_LANDMARKS for c in region.children):
		# The page's own landmarks already say how it is divided; geometry would only wrap
		# them in guesses.
		return
	taken = [c.box for c in region.children]
	leaves: list[Item] = [
		(n.box, n)
		for n in region.node.walk()
		if n.role in LEAF_ROLES
		and n.box
		and not n.children
		and n.box.intersection(region.box)
		and not any(t.intersection(n.box) for t in taken)
	]
	if len(leaves) < SPLIT_MIN_LEAVES:
		return
	items: list[Item] = [(c.box, c) for c in region.children] + leaves
	blocks = splitByGeometry(items, region.box)
	if blocks:
		region.children = blocks


def splitByGeometry(items: list[Item], bounds: Box, depth: int = 0, maxDepth: int = 3) -> list[Region]:
	"""Recursive XY-cut: blocks of content separated by gutters of empty space.

	`items` are the rectangles in an area: pieces of content with the nodes they came from, and
	regions already found there, which are kept whole and end up inside whichever block they
	fall in. Returns a region for each block, holding the blocks found inside it in turn, or an
	empty list when there is no gutter worth cutting at. Columns are tried before bands, since
	side by side is the arrangement reading order loses. A block is labelled with the first
	heading in it, which is usually what a sighted reader would call it.
	"""
	if depth >= maxDepth or len(items) < 2:
		return []
	for axis in ("x", "y"):
		parts = _cut(items, bounds, axis)
		if len(parts) < 2:
			continue
		blocks = []
		for box, members in parts:
			regions = [m for _, m in members if isinstance(m, Region)]
			if len(members) == 1 and regions:
				blocks.append(regions[0])
				continue
			nodes = [(b, m) for b, m in members if isinstance(m, Node)]
			headings = sorted((b.top, b.left, n.name) for b, n in nodes if n.role == "heading" and n.name)
			label = headings[0][2] if headings else ""
			links = sum(1 for _, n in nodes if n.role == "link") + sum(r.links for r in regions)
			inner = splitByGeometry(members, box, depth + 1, maxDepth)
			children = inner or sorted(regions, key=lambda r: (r.box.top, r.box.left))
			blocks.append(Region("area", label, box, "geometry", links, children))
		return blocks
	return []


def _cut(items: list[Item], bounds: Box, axis: str) -> list[tuple[Box, list[Item]]]:
	"""Split items at the widest gutters along one axis, up to `MAX_PARTS` parts.

	A wide item, more than half the area's width, is left out when looking for columns, since a
	picture across two columns would otherwise hide the gutter between them. It is still placed,
	in whichever part holds its centre. Parts are clipped to the area: a carousel's slides
	parked off to the right are part of the carousel, not a column of their own.
	"""
	if axis == "x":
		spans = [(b.left, b.right) for b, _ in items if b.width <= bounds.width * 0.5]
		minGap = max(16, bounds.width * 0.015)
	else:
		spans = [(b.top, b.bottom) for b, _ in items]
		minGap = max(32, min(bounds.height * 0.02, 64))
	cuts = [position for _, position in sorted(_gutters(spans, minGap), reverse=True)[: MAX_PARTS - 1]]
	if not cuts:
		return []
	cuts.sort()
	groups: list[list[Item]] = [[] for _ in range(len(cuts) + 1)]
	for box, item in items:
		centre = box.left + box.width / 2 if axis == "x" else box.top + box.height / 2
		groups[sum(1 for c in cuts if centre > c)].append((box, item))
	edges = [
		bounds.left if axis == "x" else bounds.top,
		*cuts,
		bounds.right if axis == "x" else bounds.bottom,
	]
	parts = []
	for index, members in enumerate(groups):
		low, high = edges[index], edges[index + 1]
		# A picture spanning two columns belongs to one of them, but must not widen it into
		# the other, so a part's box comes from what lies wholly inside it where it can.
		inside = [
			b
			for b, _ in members
			if low <= (b.left if axis == "x" else b.top) and (b.right if axis == "x" else b.bottom) <= high
		]
		box = unionOf(inside) or unionOf(b for b, _ in members)
		box = box and (box.intersection(bounds) or box)
		if box and _isSizeable(box):
			parts.append((box, members))
	return parts


#: A gutter may be crossed by this share of the items around it, such as a picture spanning two
#: columns of a news page, and still count as a gutter.
GUTTER_CROSSINGS = 0.03


def _gutters(spans: list[tuple[int, int]], minGap: float) -> list[tuple[int, float]]:
	"""The runs at least minGap long that few spans cover, as (length, middle) pairs.

	Only runs between covered stretches count, never the margins before the first span or after
	the last.
	"""
	if not spans:
		return []
	allowed = int(len(spans) * GUTTER_CROSSINGS)
	events = sorted([(a, 1) for a, _ in spans] + [(b, -1) for _, b in spans])
	first, last = min(a for a, _ in spans), max(b for _, b in spans)
	gutters = []
	depth = 0
	openedAt = None
	for position, change in events:
		depth += change
		if depth <= allowed and openedAt is None:
			openedAt = position
		elif depth > allowed and openedAt is not None:
			if first < openedAt and position < last and position - openedAt >= minGap:
				gutters.append((position - openedAt, (openedAt + position) / 2))
			openedAt = None
	return gutters


def _topLevel(page: Region) -> Region:
	"""The first region down from the page that is more than a wrapper around the next.

	A single area filling most of the page, such as a layout table holding everything, is gone
	through; a single narrow column is not, since that it is narrow is the point.
	"""
	region = page
	while len(region.children) == 1:
		inner = region.children[0]
		if not inner.children or inner.box.area < region.box.area * 0.6:
			break
		region = inner
	return region


def _nameByPosition(level: Region, viewport: Box) -> None:
	"""Name the unnamed areas at the top of the page by where they are and what they hold."""
	areas = [c for c in level.children if c.kind == "area"]
	if not areas:
		return
	bounds = level.box
	if len(areas) == 1 and len(level.children) == 1:
		areas[0].kind, areas[0].source = "main", "position"
		return
	ordered = sorted(level.children, key=lambda r: (r.box.top, r.box.left))
	wide = bounds.width * 0.6
	first, last = ordered[0], ordered[-1]
	if first.kind == "area" and first.box.width >= wide and first.box.height <= viewport.height * 0.3:
		if not any(c.kind in ("banner", "header") for c in level.children):
			first.kind, first.source = "header", "position"
	if (
		last is not first
		and last.kind == "area"
		and last.box.width >= wide
		and last.box.height <= viewport.height * 0.6
		and not any(c.kind in ("contentinfo", "footer") for c in level.children)
	):
		last.kind, last.source = "footer", "position"
	for region in level.children:
		if region.kind != "area":
			continue
		box = region.box
		narrow = box.width <= bounds.width * 0.3
		tall = box.height >= bounds.height * 0.4
		atSide = (
			box.left - bounds.left <= bounds.width * 0.15 or bounds.right - box.right <= bounds.width * 0.15
		)
		if narrow and tall and atSide:
			region.kind, region.source = "sidebar", "position"
	# A data table can be the main content, as Hacker News's list of stories is.
	rest = [c for c in level.children if c.kind in ("area", "table")]
	if rest and not any(c.kind == "main" for c in level.children):
		biggest = max(rest, key=lambda r: r.box.area)
		if biggest.box.area >= bounds.area * 0.2:
			if biggest.kind == "table" and not biggest.label:
				biggest.label = "table"
			biggest.kind, biggest.source = "main", "position"


def whereWords(box: Box, within: Box) -> str:
	"""Where a box is inside another, the way someone would say it."""
	horizontal = _third(box.left + box.width / 2 - within.left, within.width, ("left", "centre", "right"))
	vertical = _third(box.top + box.height / 2 - within.top, within.height, ("top", "middle", "bottom"))
	fullWidth = box.width >= within.width * 0.8
	fullHeight = box.height >= within.height * 0.8
	if fullWidth and fullHeight:
		return "filling it"
	if fullWidth:
		return f"across the {vertical}"
	if fullHeight:
		return "down the centre" if horizontal == "centre" else f"down the {horizontal} side"
	if (vertical, horizontal) == ("middle", "centre"):
		return "in the centre"
	return f"{vertical} {horizontal}"


def _third(offset: float, span: int, words: tuple[str, str, str]) -> str:
	if span <= 0:
		return words[1]
	share = offset / span
	return words[0] if share < 1 / 3 else words[2] if share > 2 / 3 else words[1]


def describe(page: Region, depth: int = 2) -> list[str]:
	"""The page as lines of text, each area with where it is, nested areas indented.

	This is the overview for a display that cannot draw, and for speech. It starts at the first
	level with more than one area, since "the page holds one table holding everything" says
	nothing about the page.
	"""
	lines: list[str] = []

	def visit(region: Region, level: int) -> None:
		for child in sorted(region.children, key=lambda r: (r.box.top, r.box.left)):
			lines.append(f"{'  ' * level}{child.title}: {whereWords(child.box, region.box)}")
			if level + 1 < depth:
				visit(child, level + 1)

	visit(_topLevel(page), 0)
	return lines


def fromProbe(data: dict[str, Any]) -> tuple[Node, Box]:
	"""The input for `findRegions` from a file saved by `spikes/layoutOverviewProbe.py`.

	A page with landmarks is built from them, nested by their place in reading order, with the
	content's rectangles placed inside them the same way: that is what the overview will use
	live, since it is cheap. A page without is built from the accessible tree the probe walked,
	where layout tables and the divs with telling ids are.
	"""
	candidates = data["candidates"]
	_, _, vw, vh = candidates["viewport"]
	viewport = Box(0, 0, vw, vh)
	treeNodes = (data.get("tree") or {}).get("nodes") or []
	pageName = treeNodes[0]["name"] if treeNodes else ""
	records = candidates["records"]
	if any(_isMeaningfulLandmark(r) for r in records):
		root = _landmarkTree(records, (data.get("leaves") or {}).get("leaves") or [])
	else:
		dataTables = {tuple(r["rect"]) for r in records if r["type"] == "table" and r["rect"]}
		root = _accessibleTree(treeNodes, dataTables)
	root.name = root.name or pageName
	return root, viewport


def _rect(value: list[int] | None) -> Box | None:
	return Box(*value) if value else None


def _landmarkWord(xmlRoles: str | None) -> str:
	word = (xmlRoles or "").split(" ")[0]
	return word if word in LANDMARKS else ""


def _isMeaningfulLandmark(record: dict[str, Any]) -> bool:
	word = _landmarkWord(record.get("xmlRoles"))
	if record["type"] != "landmark" or not word:
		return False
	return word not in ("form", "region") or bool(record.get("name"))


_RECORD_ROLES = {"landmark": "landmark", "table": "table", "frame": "internalframe", "article": "article"}
_LEAF_ROLES_BY_TYPE = {"link": "link", "heading": "heading", "graphic": "graphic", "formField": "button"}


def _landmarkTree(records: list[dict[str, Any]], leaves: list[dict[str, Any]]) -> Node:
	root = Node("document")
	items = []
	for r in records:
		kind = r["type"]
		if kind in _RECORD_ROLES:
			node = Node(
				_RECORD_ROLES[kind],
				_rect(r["rect"]),
				landmark=_landmarkWord(r.get("xmlRoles")) if kind == "landmark" else "",
				name=r.get("name") or "",
				tag=r.get("tag") or "",
				id=r.get("id") or "",
				links=r.get("links") or 0,
				textLength=r.get("textLength") or 0,
				isDataTable=kind == "table",
			)
			items.append((r["start"], r["end"], node))
		elif kind.startswith("heading"):
			items.append(
				(r["start"], r["start"], Node("heading", _rect(r["rect"]), name=r.get("label") or ""))
			)
		elif kind in ("search field", "search button"):
			role = "editabletext" if kind == "search field" else "button"
			node = Node(role, _rect(r["rect"]), name=r.get("name") or "", isSearch=True)
			items.append((r["start"], r["start"], node))
	for leaf in leaves:
		role = _LEAF_ROLES_BY_TYPE.get(leaf["type"], "statictext")
		items.append((leaf["start"], leaf["start"], Node(role, _rect(leaf["rect"]))))
	# Containers first at the same offset, outermost first, so they are open before their content.
	items.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2].role in LEAF_ROLES))
	stack: list[tuple[int, Node]] = [(1 << 62, root)]
	for start, end, node in items:
		while start >= stack[-1][0]:
			stack.pop()
		stack[-1][1].children.append(node)
		if node.role not in LEAF_ROLES:
			stack.append((end, node))
	return root


def _accessibleTree(nodes: list[dict[str, Any]], dataTables: set[tuple[int, ...]] | None = None) -> Node:
	dataTables = dataTables or set()
	if not nodes:
		return Node("document")
	built: list[Node] = []
	stack: list[tuple[int, Node]] = []
	for n in nodes:
		role = n["role"].lower()
		text = f"{n.get('name') or ''} {n.get('id') or ''} {n.get('class') or ''}".lower()
		node = Node(
			role,
			_rect(n["rect"]),
			landmark=_landmarkWord(n.get("xmlRoles")),
			name=n.get("name") or "",
			tag=n.get("tag") or "",
			id=n.get("id") or "",
			isSearch=role in ("editabletext", "button", "combobox") and "search" in text,
			isDataTable=role == "table" and bool(n["rect"]) and tuple(n["rect"]) in dataTables,
		)
		while stack and stack[-1][0] >= n["depth"]:
			stack.pop()
		if stack:
			stack[-1][1].children.append(node)
		built.append(node)
		stack.append((n["depth"], node))
	root = built[0]
	root.rect = None
	return root
