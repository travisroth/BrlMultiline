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
   part of the page. The scroll position is not reported, so unless the caller knows it, it is
   estimated from the topmost wide container that is not an isolated outlier, and the page
   region says it was estimated.
2. **Hidden things are dropped.** A visually hidden element still reports a rectangle: one
   pixel square, thousands of pixels wide, or wholly above the top of the page. None of those
   is something a sighted reader sees.
3. **Sticky headers are put back.** A header that stays on screen as the page scrolls reports
   its position on screen, not its place in the page, and lands on top of whatever is there.
   In reading order it still sits between its neighbors, and the gap it left between them is
   its own height, so it goes back into that gap, but only when it is pinned near the top of
   the screen and looks like a header: sections merely shown out of reading order stay where
   they are. Its box on screen is kept too, for a view of the screen.
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
   split again. That finds a news site's columns, which no markup describes. Regions already
   found are never cut through and always end up wholly inside the part holding them. Beside
   a page's own landmarks, only the content outside every landmark is split this way, so a
   landmark is never wrapped in a guess and content no landmark marks is never lost.
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

try:
	from .i18n import _, ngettext, pgettext
except ImportError:
	# Loaded on its own from its file, as the probe spike does: the words in English as they are.
	def _(message: str) -> str:
		return message

	def ngettext(singular: str, plural: str, count: int) -> str:
		return singular if count == 1 else plural

	def pgettext(context: str, message: str) -> str:
		return message


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
		"tab",
		"slider",
	),
)

#: Roles that are form fields, for counting them. Buttons are counted on their own.
FIELD_ROLES = frozenset(("editabletext", "combobox", "checkbox", "radiobutton", "slider", "list"))

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


def kindName(kind: str) -> str:
	""":return: what a kind of region is called when it is spoken or listed, in the reader's
	language. The kind itself, for one with no name here.

	Translated when asked rather than once at import, so that a test can put in a translation of
	its own and see every word go through it.
	"""
	names = {
		# Translators: the name of an area of a web page or window in the layout overview: the
		# banner across the top of a page, as the ARIA landmark is called.
		"banner": _("banner"),
		# Translators: the name of an area in the layout overview: a page's navigation landmark.
		"navigation": _("navigation"),
		# Translators: the name of an area in the layout overview: a page's main landmark.
		"main": _("main content"),
		# Translators: the name of an area in the layout overview: a page's complementary
		# landmark, usually a sidebar.
		"complementary": _("complementary"),
		# Translators: the name of an area in the layout overview: a page's contentinfo landmark,
		# usually its footer.
		"contentinfo": _("content info"),
		# Translators: the name of an area in the layout overview: a search field or search
		# landmark.
		"search": _("search"),
		# Translators: the name of an area in the layout overview: a form.
		"form": _("form"),
		# Translators: the name of an area in the layout overview: a named region landmark.
		"region": _("region"),
		# Translators: the name of an area in the layout overview: an embedded frame.
		"frame": _("frame"),
		# Translators: the name of an area in the layout overview: a page's header, found by its
		# code or its place rather than a landmark.
		"header": _("header"),
		# Translators: the name of an area in the layout overview: a page's footer.
		"footer": _("footer"),
		# Translators: the name of an area in the layout overview: a sidebar.
		"sidebar": _("sidebar"),
		# Translators: the name of an area in the layout overview: a page's content, found by its
		# code.
		"content": _("content"),
		# Translators: the name of an area in the layout overview: a data table.
		"table": _("table"),
		# Translators: the name of an area in the layout overview: a strip of tabs.
		"tabs": _("tabs"),
		# Translators: the name of an area in the layout overview: an accordion, headers that
		# expand stacked one under another.
		"accordion": _("accordion"),
		# Translators: the name of an area in the layout overview with no kind of its own, found
		# from where its content is or called by its own name.
		"area": _("area"),
		# Translators: the name of an area of an application window in the layout overview.
		"menubar": _("menu bar"),
		# Translators: the name of an area of an application window in the layout overview.
		"toolbar": _("toolbar"),
		# Translators: the name of an area of an application window in the layout overview: the
		# ribbon of commands in Office and other applications.
		"ribbon": _("ribbon"),
		# Translators: the name of an area of an application window in the layout overview.
		"statusbar": _("status bar"),
		# Translators: the name of an area of an application window in the layout overview: a
		# tree view, such as a folder tree.
		"tree": _("tree"),
		# Translators: the name of an area of an application window in the layout overview.
		"list": _("list"),
		# Translators: the name of an area of an application window in the layout overview: a grid
		# of cells, such as a spreadsheet or a message list.
		"grid": _("grid"),
		# Translators: the name of an area of an application window in the layout overview: a
		# document or an editor.
		"document": _("document"),
	}
	return names.get(kind, kind)


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
	#: Counts of what the element holds, besides links: form fields, buttons, tabs, and the
	#: expandable headers of accordions. Like `links`, given by a caller that has them (see
	#: `counted`) and otherwise worked out from the children.
	fields: int = 0
	buttons: int = 0
	tabs: int = 0
	sections: int = 0
	#: Whether the counts above, and `links`, came from the caller, who counted them exactly; a
	#: node that says so keeps them rather than having them added up from what was sampled.
	counted: bool = False
	#: A button that expands and collapses something, as an accordion's headers do.
	expandable: bool = False
	#: Where the element starts and ends in browse mode's text, when the caller knows: what a
	#: command that moves to an area will move to.
	start: int | None = None
	end: int | None = None
	#: Browse mode's identifier for the element, when the caller knows it: what finds the element
	#: again after the page's text has changed around it, where an offset would not.
	ident: tuple | None = None
	children: list["Node"] = field(default_factory=list)
	# Worked out by `findRegions`: the rectangle on the page, the box around the content, and how
	# far a sticky header was moved to put it back in its place on the page.
	pageRect: Box | None = field(default=None, repr=False)
	box: Box | None = field(default=None, repr=False)
	shift: int = field(default=0, repr=False)

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
	#: Where the region starts in browse mode's text, when known.
	start: int | None = None
	#: The identifier of the element the region starts at, when known. See `Node.ident`.
	anchor: tuple | None = None
	#: What the region holds besides links. See `Node.fields`.
	fields: int = 0
	buttons: int = 0
	tabs: int = 0
	sections: int = 0
	#: The box as it was on screen, relative to the visible part of the page. Differs from
	#: `box` moved by the scroll only for a sticky header put back in its place, which on
	#: screen is still where it was reported. None where nothing was on screen to report.
	screenBox: Box | None = None
	#: How the page was read: the scroll used, and whether it was estimated, for the page
	#: region only.
	scroll: int = 0
	scrollEstimated: bool = False
	#: Items held, in an application's lists, trees, grids and menus. See `appRegions`.
	items: int = 0
	#: Whether the focus is in the region, where that is known (applications, so far).
	hasFocus: bool = False
	#: The UI Automation element the region was found from, in an application. See `appRegions`.
	record: dict | None = field(default=None, repr=False, compare=False)

	@property
	def title(self) -> str:
		"""How the region is spoken: its kind, then its label when that adds something."""
		kind = kindName(self.kind)
		if self.label and self.label.lower() != kind.lower():
			# Translators: how an area with a name of its own is spoken in the layout overview: its
			# kind, then its name, such as "navigation, Primary" or "area, Clipboard".
			return _("{kind}, {name}").format(kind=kind, name=self.label)
		return kind

	def walk(self) -> Iterator["Region"]:
		yield self
		for child in self.children:
			yield from child.walk()

	@property
	def counts(self) -> list[tuple[str, int]]:
		"""What the region holds, most telling first: what kind of area it is, then what can
		be done in it, then where it leads. Only the counts above zero."""
		ordered = (
			("tabs", self.tabs),
			("sections", self.sections),
			("items", self.items),
			("fields", self.fields),
			("buttons", self.buttons),
			("links", self.links),
		)
		return [(name, count) for name, count in ordered if count > 0]


def countWords(name: str, count: int) -> str:
	"""A count as it is said and shown: "8 links", "1 field", in the reader's language."""
	if name == "tabs":
		# Translators: how many tabs an area of the layout overview holds.
		return ngettext("{count} tab", "{count} tabs", count).format(count=count)
	if name == "sections":
		# Translators: how many sections an accordion in the layout overview holds.
		return ngettext("{count} section", "{count} sections", count).format(count=count)
	if name == "items":
		# Translators: how many items a list, tree, grid or menu in the layout overview holds.
		return ngettext("{count} item", "{count} items", count).format(count=count)
	if name == "fields":
		# Translators: how many form fields an area of the layout overview holds.
		return ngettext("{count} field", "{count} fields", count).format(count=count)
	if name == "buttons":
		# Translators: how many buttons an area of the layout overview holds.
		return ngettext("{count} button", "{count} buttons", count).format(count=count)
	if name == "links":
		# Translators: how many links an area of the layout overview holds.
		return ngettext("{count} link", "{count} links", count).format(count=count)
	return f"{count} {name}"


def findRegions(root: Node, viewport: Box, scroll: int | None = None) -> Region:
	"""The page as a tree of regions, the page itself at the top.

	`viewport` is the visible part of the page on screen, which only its size is used of.
	`scroll` is how far down the page is scrolled, when the caller knows; otherwise it is
	estimated, and the page region says so.
	"""
	estimated = scroll is None
	if scroll is None:
		scroll = estimateScroll(root, viewport)
	_toPage(root, scroll)
	# Pages scroll down, not across: anything far past the view's width is parked off screen.
	_ = _dropHidden(root, viewport, viewport.width)
	if scroll > 0:
		_restoreSticky(root, scroll, viewport)
	_countContent(root)
	_contentBoxes(root)
	pageBox = Box(0, 0, viewport.width, max(root.box.bottom if root.box else 0, viewport.height))
	regions = [r for c in root.children for r in _regions(c, viewport)]
	page = Region("page", root.name, pageBox, "page", root.links, regions, root)
	page.children = [_collapse(c) for c in page.children]
	_widgets(page)
	_split(page, viewport)
	page.children = [_collapse(c) for c in page.children]
	_nameByPosition(_topLevel(page), viewport)
	_tally(page)
	for region in page.walk():
		named = region.source in ("structure", "position", "id") or region.kind == "form"
		if not region.label and region.kind != "page" and named:
			region.label = _firstHeading(region.node)
		region.label = " ".join(region.label.split())
	_placeOnScreen(page, scroll, 0)
	for child in page.children:
		_anchor(child)
	page.scroll, page.scrollEstimated = scroll, estimated
	return page


def _placeOnScreen(region: Region, scroll: int, inherited: int) -> None:
	"""Give each region its box on screen: its box on the page, less the scroll, less any move
	that put a sticky header back in its place.

	An area found by geometry has no node of its own, so it takes the move of the region it is
	in (found in review: the columns inside a pinned banner were placed far above the screen).
	"""
	shift = region.node.shift if region.node is not None and region.kind != "page" else inherited
	region.screenBox = region.box.moved(dy=-scroll - shift)
	for child in region.children:
		_placeOnScreen(child, scroll, shift)


def _anchor(region: Region) -> int | None:
	"""Where a region starts in browse mode's text: its own start or its earliest child's.

	Worked out bottom up, after everything is built, so a block found by geometry starts at the
	earliest of what it holds, including regions whose starts were set from their nodes (found
	in review: a block holding a table at offset 10 started at the links after it, at 100).
	"""
	for child in region.children:
		_anchor(child)
	node = region.node
	if node is not None and region.kind != "page":
		if region.start is None:
			region.start = node.start
		if region.anchor is None and region.start is not None and region.start == node.start:
			region.anchor = node.ident
	starts = [(c.start, c.anchor) for c in region.children if c.start is not None]
	if region.start is not None:
		starts.append((region.start, region.anchor))
	if starts:
		region.start, region.anchor = min(starts, key=lambda pair: pair[0])
	return region.start


#: Tabs in a row, or expandable headers in a column, need at least this many to be a tab strip
#: or an accordion rather than a lone tab or a disclosure button.
WIDGET_MIN = 2


def _widgets(region: Region) -> None:
	"""Find tab strips and accordions among what a region holds, and make each an area.

	Neither has a landmark or an id of its own as a rule, and both say what kind of area they
	are in a way nothing else does. A tab strip is two or more tabs in a run along one line, or
	down one column; the area is the strip itself, the tabs a reader chooses between. An
	accordion is two or more expandable buttons stacked one under another with their left
	edges together; the area runs from the first header to the last, so it holds whichever
	sections are open. A row of buttons that expand, such as the menus along a site's top, is
	not an accordion: those run across, not down. Tabs and headers inside an area found
	already belong to it, and are looked for there.
	"""
	for child in region.children:
		_widgets(child)
	if region.node is None or region.kind in ("search", "tabs", "accordion"):
		return
	# A plain wrapper is layout, not an area with meaning of its own, so it does not keep the tab
	# or the header in it from the others (found in review: four tabs, each in a list item, made
	# no strip). Anything named does.
	taken = _takenWithin(region)
	free = []
	seen = set()
	for n in region.node.walk():
		if n.box is None or not (n.role == "tab" or (n.role == "button" and n.expandable)):
			continue
		if any(t.intersection(n.box) for t in taken):
			continue
		# A tab saved as a record and sampled as a leaf too is one tab, not two in the same place.
		key = (n.role, tuple(n.box))
		if key not in seen:
			seen.add(key)
			free.append(n)
	order = lambda n: (n.start if n.start is not None else 0, n.box.top, n.box.left)  # noqa: E731
	tabs = sorted((n for n in free if n.role == "tab"), key=order)
	headers = sorted((n for n in free if n.role == "button"), key=lambda n: (n.box.top, n.box.left))
	found = [("tabs", run) for run in _runs(tabs, _nextTab)]
	found += [("accordion", run) for run in _runs(headers, _nextHeader)]
	added = []
	for kind, run in found:
		box = unionOf(n.box for n in run)
		if box is None or not _isSizeable(box):
			continue
		# Where the first member is, and what finds it again once the text around it has moved
		# (found in review: a strip kept its first tab's offset and lost its identifier).
		starts = [(n.start, n.ident) for n in run if n.start is not None]
		widget = Region(kind, "", box, "widget")
		if starts:
			widget.start, widget.anchor = min(starts, key=lambda pair: pair[0])
		if kind == "tabs":
			widget.tabs = len(run)
		else:
			widget.sections = len(run)
		added.append(widget)
	if added:
		# The plain wrappers a strip or an accordion was found through are part of it now; the
		# areas with meaning inside them go into it, not away with the wrapper (found in review:
		# a named form in a wrapper between an accordion's headers was lost).
		kept = []
		for c in region.children:
			holder = _isPlainWrapper(c) and next(
				(w for w in added if w.box.intersection(c.box) == c.box), None
			)
			if holder:
				holder.children.extend(_meaningfulWithin(c))
			else:
				kept.append(c)
		for widget in added:
			widget.children.sort(key=lambda r: (r.box.top, r.box.left))
		region.children = sorted(kept + added, key=lambda r: (r.box.top, r.box.left))


def _takenWithin(region: Region) -> list[Box]:
	"""The boxes of what a region holds that its tabs and headers must not be taken from.

	Every area with a name or a kind of its own; and, looking through plain wrappers, every such
	area inside them, the strips and accordions already found there included, so the same tabs
	are not made into a strip a second time a level up.
	"""
	return [r.box for r in _meaningfulWithin(region)]


def _meaningfulWithin(region: Region) -> list[Region]:
	""":return: the areas with a name or a kind of their own a region holds, looking through
	plain wrappers to the ones inside them."""
	found = []
	stack = list(region.children)
	while stack:
		child = stack.pop()
		if _isPlainWrapper(child):
			stack.extend(child.children)
		else:
			found.append(child)
	return found


def _isPlainWrapper(region: Region) -> bool:
	""":return: whether a region is only a container, with no name or kind of its own."""
	return region.kind == "area" and region.source == "structure" and not region.label


def _runs(nodes: list[Node], follows) -> list[list[Node]]:
	"""Split nodes, in order, into runs where each follows the one before; keep runs long enough."""
	runs: list[list[Node]] = []
	for node in nodes:
		if runs and follows(runs[-1][-1], node):
			runs[-1].append(node)
		else:
			runs.append([node])
	return [run for run in runs if len(run) >= WIDGET_MIN]


def _nextTab(before: Node, after: Node) -> bool:
	"""Whether a tab carries on the strip the one before it is in: along its row or down its
	column, with no more than a couple of tabs' room between."""
	a, b = before.box, after.box
	if a.overlapY(b) >= min(a.height, b.height) / 2:
		return 0 <= b.left - a.right <= max(40, 2 * a.height)
	if a.overlapX(b) >= min(a.width, b.width) / 2:
		return 0 <= b.top - a.bottom <= max(40, 2 * a.height)
	return False


def _nextHeader(before: Node, after: Node) -> bool:
	"""Whether an expandable header is the next of an accordion: below the one before, left
	edges together, much the same width."""
	a, b = before.box, after.box
	if b.top < a.top + a.height / 2:
		return False
	return abs(a.left - b.left) <= 24 and abs(a.width - b.width) <= 0.3 * max(a.width, b.width)


def _tally(region: Region) -> None:
	"""Give every region its counts: from its node where it has one, else from what it holds.

	A tab strip or an accordion that is an area of its own carries its own count, so the area
	around it does not say the same tabs again.
	"""
	for child in region.children:
		_tally(child)
	if region.kind in ("tabs", "accordion"):
		return
	node = region.node
	if node is not None and region.kind != "page":
		region.fields, region.buttons = node.fields, node.buttons
		region.tabs, region.sections = node.tabs, node.sections
	else:
		# A block found by geometry has no node; what lies loose in it was counted when it was
		# made, and what its regions hold is added here.
		region.fields += sum(c.fields for c in region.children)
		region.buttons += sum(c.buttons for c in region.children)
		region.tabs += sum(c.tabs for c in region.children)
		region.sections += sum(c.sections for c in region.children)
	for widget in region.walk():
		if widget is not region and widget.kind == "tabs":
			region.tabs = max(0, region.tabs - widget.tabs)
		elif widget is not region and widget.kind == "accordion":
			region.sections = max(0, region.sections - widget.sections)
			region.buttons = max(0, region.buttons - widget.sections)


def _firstHeading(node: Node | None) -> str:
	"""The first heading inside a node, in reading order, as a name for an unnamed area."""
	if node is None:
		return ""
	return next((n.name for n in node.walk() if n.role == "heading" and n.name.strip()), "")


def estimateScroll(root: Node, viewport: Box) -> int:
	"""How far down the page is scrolled, from the topmost wide container.

	The browser does not say, so this is an estimate, and a caller that knows should pass the
	scroll to `findRegions` instead. The top of the page is at the top of its topmost element,
	with three kinds of element left out because pages park hidden things far above the top:

	1. Anything that is not a container. A skip link or a button is a control, and a hidden one
	   can be any width (found in review: a wide skip link at minus 9999).
	2. Anything narrower than 40 percent of the view, or absurdly wide.
	3. An outlier: a top with nothing else starting within a screen of where it ends. A page's
	   content runs on from its top; a hidden container parked thousands of pixels up does not.
	"""
	spans = sorted(
		(n.rect.top, n.rect.bottom)
		for n in root.walk()
		if n is not root
		and n.rect
		and n.role in CONTAINER_ROLES
		and viewport.width * 0.4 <= n.rect.width <= viewport.width * 1.5
		and n.rect.height >= 8
	)
	for index, (top, bottom) in enumerate(spans):
		following = spans[index + 1 :]
		if not following or any(t <= bottom + viewport.height for t, _ in following):
			return max(0, -top)
	return 0


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


#: A sticky header is pinned within this many pixels of the top of the screen.
STICKY_PINNED = 120
#: Tags and landmarks a sticky header has. Two ordinary sections in the opposite order on screen
#: to reading order also look like a header out of place, and moving one hides exactly the
#: mismatch a reader testing a page wants to find; so a header has to look like one.
STICKY_TAGS = frozenset(("header", "nav", "sticky-header"))
STICKY_LANDMARKS = frozenset(("banner", "navigation"))


def _looksLikeHeader(node: Node) -> bool:
	if node.landmark in STICKY_LANDMARKS or (node.tag or "").lower() in STICKY_TAGS:
		return True
	return _idKind(node) in ("header", "navigation")


def _restoreSticky(node: Node, scroll: int, viewport: Box) -> None:
	"""Move headers that stay on screen as the page scrolls back to their place in the page.

	Such a header reports where it is on screen, so on a scrolled page it lands on top of what
	follows it in reading order. Its place is the gap between the sibling before it and the one
	after, which on the page it left is its own height. All of that has to hold, and two more
	things: it is pinned near the top of the screen, and it looks like a header (a banner or
	navigation landmark, a header or nav element, or an id saying so). Its rectangle on screen is
	kept in `shift`, so a view of the screen can still draw it where it is.
	"""
	for child in node.children:
		_restoreSticky(child, scroll, viewport)
	placed = [(c, c.pageRect) for c in node.children if c.pageRect]
	for i in range(len(placed) - 1):
		child, rect = placed[i]
		nextRect = placed[i + 1][1]
		if rect.top <= nextRect.top or not rect.overlapX(nextRect):
			continue
		onScreen = rect.top - scroll
		if not (-2 <= onScreen <= STICKY_PINNED) or not _looksLikeHeader(child):
			continue
		gapTop = placed[i - 1][1].bottom if i > 0 else 0
		gap = nextRect.top - gapTop
		if gap >= rect.height * 0.8 and rect.height <= gap * 1.25:
			dy = gapTop - rect.top
			for n in child.walk():
				n.shift += dy
				if n.pageRect:
					n.pageRect = n.pageRect.moved(dy=dy)


def _countContent(node: Node) -> None:
	"""Fill in the counts the caller did not have, from the node itself and its children.

	A caller that counted everything says so (`Node.counted`). One that gave only links and
	text, as pages saved before the other counts did, keeps those, and has the rest added up
	here (found on a saved page: a sign on form with four fields counted none, and was not an
	area).
	"""
	for child in node.children:
		_countContent(child)
	if node.counted:
		return
	linksGiven = bool(node.links or node.textLength)
	if node.role == "link" and not linksGiven:
		node.links = 1
	elif node.role in FIELD_ROLES:
		node.fields = 1
	elif node.role == "button":
		node.buttons = 1
		node.sections = 1 if node.expandable else 0
	elif node.role == "tab":
		node.tabs = 1
	if node.role in LEAF_ROLES and not node.children and not linksGiven:
		node.textLength = len(node.name)
	for child in node.children:
		if not linksGiven:
			node.links += child.links
			node.textLength += child.textLength
		node.fields += child.fields
		node.buttons += child.buttons
		node.tabs += child.tabs
		node.sections += child.sections


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


def words(text: str) -> list[str]:
	"""The words in a name or an identifier, lower case: "siteSearch-box" is site, search, box.

	In any alphabet: a page or a window named in Spanish or Russian has its words in its own
	letters, "Búsqueda" or "Поиск", not only A to Z.
	"""
	spaced = []
	previous = ""
	for character in text or "":
		if previous.islower() and character.isupper():
			# A capital after a small letter starts a word in an identifier: siteSearch.
			spaced.append(" ")
		spaced.append(character)
		previous = character
	return [w for w in re.split(r"[\W_]+", "".join(spaced).lower()) if w]


def searchWords() -> tuple[str, ...]:
	""":return: the beginnings of the words that mean search: English's, always, since the code of a
	page or an application is written in it, and the reader's own language's."""
	# Translators: the layout overview recognises a search field by its name, and pages and
	# applications in your language name theirs in it. Put the words that mean search, or the
	# beginnings of them, separated by spaces: a word in a name counts when it starts with one of
	# these, so "recherch" finds both "recherche" and "rechercher". The English "search" is always
	# recognised as well, so you need not include it.
	own = pgettext("search words", "search")
	return tuple(dict.fromkeys(["search", *words(own)]))


def mentionsSearch(*texts: str) -> bool:
	"""Whether any of the texts has search as a word, or a word starting with it.

	Search box, searchbox, search-form and siteSearch count; research does not (found in
	review: a "Research grants" form became a search field). Shared with the collector and the
	probe, so all three agree on what search is. In the reader's language as well as English;
	see `searchWords`.
	"""
	starts = searchWords()
	return any(w.startswith(starts) for text in texts for w in words(text))


def _mentionsSearch(node: Node) -> bool:
	return mentionsSearch(node.name, node.id)


#: A form without a name is an area of its own when it holds at least this many fields: a
#: sign in, a checkout, a contact form. Those are often what a page is for.
FORM_MIN_FIELDS = 3

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


def _classifyForm(node: Node, source: str) -> tuple[str, str, str]:
	"""A form is search, or a form area, or nothing to name. See `FORM_MIN_FIELDS`."""
	if _isSearchForm(node):
		return "search", "", "landmark" if source == "landmark" else "search"
	if not node.name and node.fields < FORM_MIN_FIELDS:
		# A form around a single field, or around a whole page with one search box in it, is not
		# a form a reader would name.
		return "", "", ""
	pageRect = node.pageRect
	if pageRect and (pageRect.width < 40 or pageRect.height < 12):
		return "", "", ""
	return "form", node.name, source


def _classify(node: Node) -> tuple[str, str, str]:
	"""What a node is, if anything can be said: (kind, label, source), or empty strings."""
	landmark = node.landmark
	if landmark in LANDMARKS:
		if landmark == "region" and (not node.name or node.tag in SVG_TAGS):
			return "", "", ""
		if landmark == "form":
			return _classifyForm(node, "landmark")
		pageRect = node.pageRect
		if pageRect and (pageRect.width < 40 or pageRect.height < 12):
			return "", "", ""
		return landmark, node.name, "landmark"
	if node.isSearch:
		return "search", "", "search"
	if node.role == "form":
		# The form role alone, without the landmark attribute some browsers add, is the same
		# form (found in review: three fields in a plain form were left as an area).
		return _classifyForm(node, "structure")
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
		return [
			Region(
				"search", label or "", node.box, source or "search", node.links, node=node, start=node.start
			)
		]
	if node.role not in CONTAINER_ROLES:
		return children
	if not kind and children and all(c.kind == "search" for c in children):
		# A cell or a wrapper around a search field and its button is the search, as long as
		# it is not much more than them.
		inner = unionOf(c.box for c in children)
		if inner and node.box.area <= inner.area * 3:
			return [Region("search", "", node.box, "search", node.links, node=node, start=node.start)]
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
	return [
		Region(
			kind or "area",
			label,
			node.box,
			source or "structure",
			node.links,
			children,
			node,
			start=node.start,
		)
	]


#: This many unnamed areas of much the same size side by side or stacked are the items of a
#: list, such as the rows of stories on Hacker News, not areas of the page.
REPEAT_MIN = 6


#: The roles a repeated record has. A section the same size as its neighbors is not a record.
REPEAT_ROLES = frozenset(("tablerow", "listitem"))


def _isRecord(region: Region) -> bool:
	"""An unnamed row or list item with no heading of its own: one of a list's records."""
	node = region.node
	if region.kind != "area" or region.children or node is None or node.role not in REPEAT_ROLES:
		return False
	return not any(n.role == "heading" for n in node.walk())


def _dropRepeats(children: list[Region]) -> list[Region]:
	"""Leave out the records of a list, such as Hacker News's rows of stories.

	Found in review: dropping any six areas of one size took six titled sections with them. A
	record is a row or a list item with no heading, so a section, or a row with its own heading,
	is always kept.
	"""
	plain = [c for c in children if _isRecord(c)]
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
		if inner.kind in ("tabs", "accordion") and not _isPlainWrapper(region):
			# A named area that is all tab strip is still both: the strip inside "main" is not
			# merged away into it. A plain wrapper around a strip is.
			break
		keep = _better(region, inner)
		starts = [(r.start, r.anchor) for r in (region, inner) if r.start is not None]
		start, anchor = min(starts, key=lambda pair: pair[0]) if starts else (None, None)
		merged = Region(
			keep.kind,
			keep.label,
			region.box,
			keep.source,
			region.links,
			inner.children,
			region.node,
			start=start,
			anchor=anchor,
		)
		# What the kept region counted goes with it: a tab strip found inside a plain list keeps
		# its tabs when the list around it is merged away.
		merged.fields, merged.buttons = keep.fields, keep.buttons
		merged.tabs, merged.sections = keep.tabs, keep.sections
		region = merged
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
	if any(c.kind in PAGE_LANDMARKS for c in region.children):
		# The page's own landmarks say how most of it is divided, and wrapping them in
		# geometric guesses would only hide that. But content outside every landmark is still
		# content (found in review: a navigation landmark hid a whole unmarked article), so it
		# is found from its own gaps and set beside the landmarks.
		region.children = sorted(
			region.children + _uncovered(leaves, taken, region.box),
			key=lambda r: (r.box.top, r.box.left),
		)
		return
	items: list[Item] = [(c.box, c) for c in region.children] + leaves
	blocks = splitByGeometry(items, region.box)
	if blocks:
		region.children = blocks


def _uncovered(leaves: list[Item], taken: list[Box], bounds: Box) -> list[Region]:
	"""Areas for content outside the regions already found, none overlapping them.

	The content is split by its own gaps. A block that would reach across a region found
	already is not dropped but divided around it: into what is above it, below it, and to
	each side, and each of those again around any other region it still reaches across. The
	content's own rectangles are all outside the regions, so dividing always ends in blocks
	clear of them (found in review: content on all four sides of a narrow navigation landmark,
	with no straight gutter between, was discarded as one block crossing it).
	"""
	blocks = splitByGeometry(leaves, bounds) or [_block(leaves, bounds)]
	result: list[Region] = []
	for block in blocks:
		if block is None:
			continue
		if not any(t.intersection(block.box) for t in taken):
			result.append(block)
			continue
		inside = [(b, n) for b, n in leaves if block.box.intersection(b)]
		result += _around(inside, taken, bounds)
	return result


def _around(members: list[Item], taken: list[Box], bounds: Box, depth: int = 0) -> list[Region]:
	"""Divide content around the first region its box reaches across, until no box does."""
	block = _block(members, bounds)
	if block is None:
		return []
	crossed = next((t for t in taken if t.intersection(block.box)), None)
	if crossed is None or depth > 2 * len(taken):
		return [block] if crossed is None else []
	sides: dict[str, list[Item]] = {"above": [], "below": [], "left": [], "right": []}
	for b, n in members:
		middleX, middleY = b.left + b.width / 2, b.top + b.height / 2
		if middleY < crossed.top:
			sides["above"].append((b, n))
		elif middleY >= crossed.bottom:
			sides["below"].append((b, n))
		elif middleX < crossed.left:
			sides["left"].append((b, n))
		else:
			sides["right"].append((b, n))
	return [piece for side in sides.values() if side for piece in _around(side, taken, bounds, depth + 1)]


def _block(members: list[Item], bounds: Box) -> Region | None:
	"""One area around some content, named by its first heading, or None if too small."""
	box = unionOf(b for b, _ in members)
	box = box and box.intersection(bounds)
	if not box or not _isSizeable(box) or len(members) < SPLIT_MIN_LEAVES // 2:
		return None
	block = Region("area", "", box, "geometry")
	_fillBlock(block, members, countLoose=True)
	return block


def _fillBlock(block: Region, members: list[Item], countLoose: bool) -> None:
	"""Name a block found by geometry, count what lies loose in it, and find where it starts.

	Shared by both ways blocks are made, so neither forgets a count (found in review: the blocks
	made beside landmarks counted their links and not their fields or buttons). Links are counted
	whole, regions' included; fields and buttons only where they lie loose and the block is not
	split again, since a split block's are its parts', which `_tally` adds up.
	"""
	nodes = [(b, n) for b, n in members if isinstance(n, Node)]
	regions = [m for _, m in members if isinstance(m, Region)]
	headings = sorted((b.top, b.left, n.name) for b, n in nodes if n.role == "heading" and n.name)
	block.label = headings[0][2] if headings else ""
	block.links = sum(1 for _, n in nodes if n.role == "link") + sum(r.links for r in regions)
	if countLoose:
		block.fields = sum(1 for _, n in nodes if n.role in FIELD_ROLES)
		block.buttons = sum(1 for _, n in nodes if n.role == "button")
	starts = [(n.start, n.ident) for _, n in nodes if n.start is not None]
	starts += [(r.start, r.anchor) for r in regions if r.start is not None]
	if starts:
		block.start, block.anchor = min(starts, key=lambda pair: pair[0])


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
			inner = splitByGeometry(members, box, depth + 1, maxDepth)
			children = inner or sorted(regions, key=lambda r: (r.box.top, r.box.left))
			block = Region("area", "", box, "geometry", children=children)
			_fillBlock(block, members, countLoose=not inner)
			blocks.append(block)
		return blocks
	return []


def _cut(items: list[Item], bounds: Box, axis: str) -> list[tuple[Box, list[Item]]]:
	"""Split items at the widest gutters along one axis, up to `MAX_PARTS` parts.

	A wide piece of content, more than half the area's width, is left out when looking for
	columns, since a picture across two columns would otherwise hide the gutter between them.
	It is still placed, in whichever part holds its center. A region found already is different:
	it is never cut through, whatever its width, so that it stays whole inside the part that
	holds it (found in review: a wide table ended up outside the column it was put in). Parts
	are clipped to the area: a carousel's slides parked off to the right are part of the
	carousel, not a column of their own.
	"""
	if axis == "x":
		spans = [
			(b.left, b.right) for b, m in items if b.width <= bounds.width * 0.5 or isinstance(m, Region)
		]
		minGap = max(16, bounds.width * 0.015)
	else:
		spans = [(b.top, b.bottom) for b, _ in items]
		minGap = max(32, min(bounds.height * 0.02, 64))
	barriers = [
		(b.left, b.right) if axis == "x" else (b.top, b.bottom) for b, m in items if isinstance(m, Region)
	]
	gutters = [g for g in _gutters(spans, minGap) if not any(low < g[1] < high for low, high in barriers)]
	cuts = [position for _, position in sorted(gutters, reverse=True)[: MAX_PARTS - 1]]
	if not cuts:
		return []
	cuts.sort()
	groups: list[list[Item]] = [[] for _ in range(len(cuts) + 1)]
	for box, item in items:
		center = box.left + box.width / 2 if axis == "x" else box.top + box.height / 2
		groups[sum(1 for c in cuts if center > c)].append((box, item))
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
		# Regions are always wholly inside the part that holds them.
		box = unionOf([b for b in (box,) if b] + [b for b, m in members if isinstance(m, Region)])
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
				# Said as "main content, table": the kind it has now, and what it is.
				biggest.label = kindName("table")
			biggest.kind, biggest.source = "main", "position"


def whereWords(box: Box, within: Box) -> str:
	"""Where a box is inside another, the way someone would say it, in the reader's language."""
	horizontal = _third(box.left + box.width / 2 - within.left, within.width, ("left", "center", "right"))
	vertical = _third(box.top + box.height / 2 - within.top, within.height, ("top", "middle", "bottom"))
	fullWidth = box.width >= within.width * 0.8
	fullHeight = box.height >= within.height * 0.8
	if fullWidth and fullHeight:
		return _placeWords("filling")
	if fullWidth:
		return _placeWords(f"across {vertical}")
	if fullHeight:
		return _placeWords(f"down {horizontal}")
	return _placeWords(f"{vertical} {horizontal}")


def _placeWords(place: str) -> str:
	""":return: a place as it is said, each phrase whole so that a language can word it as it needs
	to rather than as English words put together."""
	phrases = {
		# Translators: where an area is in the layout overview: it fills the page or window.
		"filling": _("filling it"),
		# Translators: where an area is in the layout overview: the full width, at the top.
		"across top": _("across the top"),
		# Translators: where an area is in the layout overview: the full width, in the middle.
		"across middle": _("across the middle"),
		# Translators: where an area is in the layout overview: the full width, at the bottom.
		"across bottom": _("across the bottom"),
		# Translators: where an area is in the layout overview: the full height, at the left.
		"down left": _("down the left side"),
		# Translators: where an area is in the layout overview: the full height, in the center.
		"down center": _("down the center"),
		# Translators: where an area is in the layout overview: the full height, at the right.
		"down right": _("down the right side"),
		# Translators: where an area is in the layout overview.
		"top left": _("top left"),
		# Translators: where an area is in the layout overview.
		"top center": _("top center"),
		# Translators: where an area is in the layout overview.
		"top right": _("top right"),
		# Translators: where an area is in the layout overview.
		"middle left": _("middle left"),
		# Translators: where an area is in the layout overview: in the middle of the page or window.
		"middle center": _("in the center"),
		# Translators: where an area is in the layout overview.
		"middle right": _("middle right"),
		# Translators: where an area is in the layout overview.
		"bottom left": _("bottom left"),
		# Translators: where an area is in the layout overview.
		"bottom center": _("bottom center"),
		# Translators: where an area is in the layout overview.
		"bottom right": _("bottom right"),
	}
	return phrases[place]


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
			# Translators: one line of the layout overview as text: an area and where it is, such
			# as "navigation: down the left side".
			line = _("{area}: {where}").format(area=child.title, where=whereWords(child.box, region.box))
			lines.append(f"{'  ' * level}{line}")
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


def _ident(record: dict[str, Any]) -> tuple | None:
	""":return: browse mode's identifier for a saved element, where the collector saved one."""
	ident = record.get("ident")
	return tuple(ident) if ident else None


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
#: A sampled form field the collector found to be a button is saved as "button"; any other is a
#: field. Saved pages from before that distinction call every one a form field.
_LEAF_ROLES_BY_TYPE = {
	"link": "link",
	"heading": "heading",
	"graphic": "graphic",
	"formField": "editabletext",
	"button": "button",
	"tab": "tab",
}


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
			node.start, node.end = r["start"], r["end"]
			node.ident = _ident(r)
			if "fields" in r:
				# Counted exactly by the collector from browse mode's text, which the samples of
				# content below cannot do on a busy page.
				node.fields, node.buttons = r.get("fields") or 0, r.get("buttons") or 0
				node.tabs, node.sections = r.get("tabs") or 0, r.get("sections") or 0
				node.counted = True
			items.append((r["start"], r["end"], node))
		elif kind.startswith("heading"):
			node = Node(
				"heading", _rect(r["rect"]), name=r.get("label") or "", start=r["start"], ident=_ident(r)
			)
			items.append((r["start"], r["start"], node))
		elif kind in ("tab", "toggle"):
			# A tab, or a button that expands and collapses something: what tab strips and
			# accordions are found from. See `_widgets`.
			node = Node(
				"tab" if kind == "tab" else "button",
				_rect(r["rect"]),
				name=r.get("name") or r.get("label") or "",
				start=r["start"],
				expandable=kind == "toggle",
				ident=_ident(r),
			)
			items.append((r["start"], r["start"], node))
		elif kind in ("search field", "search button"):
			role = "editabletext" if kind == "search field" else "button"
			node = Node(
				role,
				_rect(r["rect"]),
				name=r.get("name") or "",
				isSearch=True,
				start=r["start"],
				ident=_ident(r),
			)
			items.append((r["start"], r["start"], node))
	for leaf in leaves:
		role = _LEAF_ROLES_BY_TYPE.get(leaf["type"], "statictext")
		node = Node(role, _rect(leaf["rect"]), start=leaf["start"], ident=_ident(leaf))
		items.append((leaf["start"], leaf["start"], node))
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
		node = Node(
			role,
			_rect(n["rect"]),
			landmark=_landmarkWord(n.get("xmlRoles")),
			name=n.get("name") or "",
			tag=n.get("tag") or "",
			id=n.get("id") or "",
			isSearch=role in ("editabletext", "button", "combobox")
			and mentionsSearch(n.get("name") or "", n.get("id") or "", n.get("class") or ""),
			isDataTable=role == "table" and bool(n["rect"]) and tuple(n["rect"]) in dataTables,
			expandable=bool(n.get("expandable")),
			# Where it is in browse mode's text, for moving to an area found from the tree, where
			# the collector saved it. Pages the probe saved before have neither.
			start=n.get("start"),
			ident=_ident(n),
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
