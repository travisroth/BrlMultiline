# BrlMultiline: the main areas of a page, drawn as boxes on the pins.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A page's main areas as labelled boxes in their real places, for a hand to take in.

`layoutRegions` says what the areas are and where they are on the page. This module draws them
on a pin surface: each area an outline, a short braille label inside it when it fits, and a key
letter when it does not, the way tactile graphics transcribers label a map. Pressing a routing
key or touching inside a box says what it is in full. `docs/design/layout-overview-plan.md`
has the reasoning.

Pure Python, with the buffer and the braille translation passed in, so the layout can be
tested against the saved pages without a display or NVDA.

**The page is not drawn to scale, and cannot be.** A banner is a tenth of a screen tall, which
on a 40 pin panel is four pins: too thin for a label, and too thin for a finger to find its
inside. So each axis is drawn as a warped ruler. The page's edges, and every box's edges, are
marked on it; the stretches between marks share the pins in proportion to their length, and
then any box too small for what it has to hold is grown at the expense of the stretches that
have room to spare. Order and adjacency are kept exactly — what is left of something stays
left of it, and boxes that touch on the page touch on the pins — and sizes are only as true as
the panel allows. That is the trade a tactile map makes too.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .i18n import _, pgettext
from .layoutRegions import Box, Region, _topLevel, countWords, whereWords


def shortLabel(kind: str) -> str:
	""":return: the short label drawn inside a kind of box, in the reader's language, or nothing for
	a kind with none. Short because every braille cell costs three pins across, and the full name is
	a press away. Translated when asked, as `layoutRegions.kindName` is."""
	labels = {
		# Translators: a label drawn in braille inside a box in the layout overview, for a page's
		# banner. Keep these labels as short as you can, six letters or fewer: every braille cell
		# takes room on the display, and pressing in the box says the full name.
		"banner": pgettext("braille box label", "banner"),
		# Translators: a short braille label in the layout overview, for navigation.
		"navigation": pgettext("braille box label", "nav"),
		# Translators: a short braille label in the layout overview, for main content.
		"main": pgettext("braille box label", "main"),
		# Translators: a short braille label in the layout overview, for a complementary area or a
		# sidebar.
		"complementary": pgettext("braille box label", "side"),
		# Translators: a short braille label in the layout overview, for a page's footer or content
		# info.
		"contentinfo": pgettext("braille box label", "foot"),
		# Translators: a short braille label in the layout overview, for search.
		"search": pgettext("braille box label", "find"),
		# Translators: a short braille label in the layout overview, for a form.
		"form": pgettext("braille box label", "form"),
		# Translators: a short braille label in the layout overview, for a named region.
		"region": pgettext("braille box label", "region"),
		# Translators: a short braille label in the layout overview, for an embedded frame.
		"frame": pgettext("braille box label", "frame"),
		# Translators: a short braille label in the layout overview, for a page's header.
		"header": pgettext("braille box label", "head"),
		# Translators: a short braille label in the layout overview, for a page's footer.
		"footer": pgettext("braille box label", "foot"),
		# Translators: a short braille label in the layout overview, for a sidebar.
		"sidebar": pgettext("braille box label", "side"),
		# Translators: a short braille label in the layout overview, for a page's content.
		"content": pgettext("braille box label", "text"),
		# Translators: a short braille label in the layout overview, for a data table.
		"table": pgettext("braille box label", "table"),
		# Translators: a short braille label in the layout overview, for a strip of tabs.
		"tabs": pgettext("braille box label", "tabs"),
		# Translators: a short braille label in the layout overview, for an accordion.
		"accordion": pgettext("braille box label", "accord"),
		# Translators: a short braille label in the layout overview, for a menu bar.
		"menubar": pgettext("braille box label", "menu"),
		# Translators: a short braille label in the layout overview, for a toolbar.
		"toolbar": pgettext("braille box label", "tools"),
		# Translators: a short braille label in the layout overview, for a ribbon of commands.
		"ribbon": pgettext("braille box label", "ribbon"),
		# Translators: a short braille label in the layout overview, for a status bar.
		"statusbar": pgettext("braille box label", "status"),
		# Translators: a short braille label in the layout overview, for a tree view.
		"tree": pgettext("braille box label", "tree"),
		# Translators: a short braille label in the layout overview, for a list.
		"list": pgettext("braille box label", "list"),
		# Translators: a short braille label in the layout overview, for a grid of cells.
		"grid": pgettext("braille box label", "grid"),
		# Translators: a short braille label in the layout overview, for a document or editor.
		"document": pgettext("braille box label", "doc"),
	}
	return labels.get(kind, "")


#: Pins a braille cell takes across, with the gap after it, and down.
CELL_STRIDE = 3
CELL_HEIGHT = 4
#: Pins between a box's outline and its label, on each side the label touches.
LABEL_MARGIN = 1
#: The smallest box drawn. Enough for an outline around a key letter with a pin of space.
MIN_BOX = (2 + 2 * LABEL_MARGIN + 2, 2 + 2 * LABEL_MARGIN + CELL_HEIGHT)
#: Letters for boxes too small for their label, in the order they are given out.
KEY_LETTERS = "abcdefghijklmnopqrstuvwxyz"
#: Letters kept for a kind of area found on most pages, so a reader can find it by its letter
#: without pressing to ask: s is always search, and never anything else.
RESERVED_KEYS = {"search": "s"}


@dataclass(eq=False)
class DrawnBox:
	"""A region as drawn: where on the pins, and how it was labelled."""

	region: Region
	rect: Box
	depth: int
	label: str = ""
	key: str = ""
	children: list["DrawnBox"] = field(default_factory=list)
	#: Where the label or key letter is drawn.
	labelAt: Box | None = None
	#: The counts drawn with the label, most telling first: each one's words, the area it takes,
	#: and what is drawn for it, as `_marks` gives.
	details: list[tuple[str, Box, list]] = field(default_factory=list)

	@property
	def detail(self) -> str:
		""":return: the first count drawn, or nothing."""
		return self.details[0][0] if self.details else ""

	@property
	def detailAt(self) -> Box | None:
		""":return: where the first count drawn is, or None."""
		return self.details[0][1] if self.details else None


@dataclass
class Overview:
	"""What `compose` made: the buffer, the boxes on it, and how to describe them."""

	buffer: object
	boxes: list[DrawnBox]
	window: Box
	#: Where each region is in the view drawn, by `id`: on the page, or on the screen.
	places: dict[int, Box] = field(default_factory=dict)
	#: The whole view this is part of, which a press describes positions within: "down the left
	#: side" of the page, not of whatever part of it is magnified.
	whole: Box | None = None
	#: The rulers the boxes were placed on, for finding which part of the page a part of the
	#: drawing shows. See `windowAt`.
	xs: "_Ruler | None" = None
	ys: "_Ruler | None" = None
	#: Areas in view that were left out for want of room, so the reader can be told.
	omitted: list[Region] = field(default_factory=list)

	def walk(self):
		stack = list(reversed(self.boxes))
		while stack:
			box = stack.pop()
			yield box
			stack.extend(reversed(box.children))

	def boxAt(self, x: int, y: int) -> DrawnBox | None:
		"""The innermost box drawn around a pin, or None outside every box."""
		found = None
		for box in self.walk():
			r = box.rect
			if r.left <= x < r.right and r.top <= y < r.bottom:
				if found is None or box.depth >= found.depth:
					found = box
		return found

	def describeAt(self, x: int, y: int) -> str | None:
		"""What a press at a pin says: the area's full name, where it is, and its links."""
		box = self.boxAt(x, y)
		if box is None:
			return None
		region = box.region
		within = self.whole or self.window
		parts = [region.title, whereWords(self.places.get(id(region), region.box), within)]
		parts += [countWords(name, count) for name, count in region.counts]
		return ", ".join(parts)

	@property
	def unreadable(self) -> int:
		"""How many areas in view could not be drawn big enough to find: left out, or drawn as
		slivers because even the tallest drawing had no more room. Said after the drawing."""
		return len(self.omitted) + sum(1 for b in self.walk() if _isCramped(b))

	@property
	def keyed(self) -> list[DrawnBox]:
		return [b for b in self.walk() if b.key]

	def keyText(self) -> str:
		"""The key for boxes labelled with a letter, as one line to say."""
		# Translators: one entry in the key to the letters on a drawing of a page's or window's
		# layout: a letter, and the area it stands for, such as "a, navigation".
		entry = _("{letter}, {area}")
		return "; ".join(entry.format(letter=b.key, area=b.region.title) for b in self.keyed)

	def windowAt(self, offset: float, span: float, top: float, down: float) -> Box:
		"""The part of the page shown by a part of this drawing, given as fractions of it.

		Graphics mode asks a figure to draw a window of itself again in those terms (see
		`graphicsMode.Drawing.redraw`). The drawing is warped, so a third of its height is not a
		third of the page; the rulers it was laid out on are read backwards to find the page
		that third shows, so a zoomed view holds exactly what was under the reader's hand.
		"""
		width = getattr(self.buffer, "width", 0) or 1
		height = getattr(self.buffer, "height", 0) or 1
		if self.xs is None or self.ys is None:
			return self.window
		left = self.xs.valueAt(offset * width)
		right = self.xs.valueAt(min(1.0, offset + span) * width)
		upper = self.ys.valueAt(top * height)
		lower = self.ys.valueAt(min(1.0, top + down) * height)
		return Box(round(left), round(upper), max(1, round(right - left)), max(1, round(lower - upper)))


def windowFor(page: Region, view: str, viewport: Box) -> Box:
	"""What is drawn: the whole page in page coordinates, or the screen in screen coordinates."""
	if view == "page":
		return page.box
	return Box(0, 0, viewport.width, viewport.height)


def focusWindow(
	page: Region,
	view: str,
	viewport: Box,
	focus: Region | None,
	places: dict[int, Box] | None = None,
) -> Box:
	"""What a view draws: the page or the screen, or the part of it an area drilled into covers.

	An area drilled into fills the drawing, and positions are said within it: "top left" of the
	navigation, not of the page. On the screen it is the part of the area that is on screen.
	"""
	whole = windowFor(page, view, viewport)
	if focus is None:
		return whole
	place = (places or placesFor(page, view)).get(id(focus), focus.box)
	return place.intersection(whole) or place


def placesFor(page: Region, view: str) -> dict[int, Box]:
	"""Where each region is in the view: its box on the page, or its box on the screen.

	On the screen a sticky header is where the browser reported it, at the top, rather than at
	its place on the page; the rules keep both (`Region.screenBox`). Whatever it covers on the
	screen is drawn from below it, so the two do not overlap.
	"""
	regions = [r for r in page.walk() if r is not page]
	if view == "page":
		return {id(r): r.box for r in regions}
	places = {id(r): r.screenBox or r.box.moved(dy=-page.scroll) for r in regions}
	pinned = [
		places[id(r)]
		for r in _topLevel(page).children
		if r.screenBox is not None and r.screenBox != r.box.moved(dy=-page.scroll)
	]
	for region in _topLevel(page).children:
		box = places[id(region)]
		for header in pinned:
			if box == header or not box.overlapX(header) or not box.overlapY(header):
				continue
			covered = header.left <= box.left and box.right <= header.right
			if covered and header.top <= max(box.top, 0) and box.bottom <= header.bottom:
				# Wholly behind the header, so none of it can be seen, nor anything in it.
				box = Box(box.left, box.top, box.width, 0)
				break
			# The header covers the top of what is visible of this box, which may start above
			# the screen, as a main area scrolled past does.
			if box.top < header.bottom < box.bottom and header.top <= max(box.top, 0):
				box = Box(box.left, header.bottom, box.width, box.bottom - header.bottom)
		places[id(region)] = box
		_clipInside(region, box, places)
	return places


def _clipInside(region: Region, bound: Box, places: dict[int, Box]) -> None:
	"""What can be seen of each area inside a region is no more than what can be seen of it.

	On the screen an area wholly under a sticky header is hidden, however big it is. One with
	nothing visible gets no height, so nothing chooses it to draw, and nothing inside it either.
	"""
	for child in region.children:
		box = places[id(child)]
		visible = box.intersection(bound)
		places[id(child)] = visible or Box(box.left, box.top, box.width, 0)
		_clipInside(child, visible or Box(box.left, box.top, 0, 0), places)


def compose(
	newBuffer: Callable[[int, int], object],
	page: Region,
	width: int,
	height: int,
	view: str,
	viewport: Box,
	translate: Callable[[str], list[int]],
	depth: int = 2,
	window: Box | None = None,
	keys: dict[int, str] | None = None,
	focus: Region | None = None,
	style: CountStyle | None = None,
) -> Overview | None:
	"""Draw the page's areas onto a new buffer of width by height pins.

	:param newBuffer: makes a buffer to draw in, as `GraphicsMode.newBuffer` does.
	:param page: the page from `layoutRegions.findRegions`.
	:param view: "screen" for the part of the page on screen, or "page" for all of it.
	:param viewport: the visible part of the page, whose size the screen view is.
	:param translate: braille cells for a short label, as `graphicsMode.labelCells` does.
	:param depth: how many levels of areas to draw: 1 for the main areas alone, 2 for the
		areas inside them as well.
	:param window: part of the view to draw, in the view's coordinates, for a magnified or
		moved view of it; the whole view if None. Labels are composed afresh at the panel's
		own resolution, never magnified.
	:param keys: key letters already given out, by region `id`, kept and added to, so that an
		area has the same letter in every view of the page.
	:param focus: an area drilled into, drawn as the whole view with what is inside it; the
		page if None. See `focusWindow`.
	:param style: how counts are drawn; NVDA's English abbreviations if None.
	:return: the overview, or None if no buffer could be made.
	"""
	buffer = newBuffer(width, height)
	if buffer is None:
		return None
	places = placesFor(page, view)
	whole = focusWindow(page, view, viewport, focus, places)
	window = window or whole
	level = focus or _topLevel(page)
	chosen = _choose(level.children, window, depth, places)
	wanted = [r for r, _depth in chosen]
	labels = {id(r): _labelFor(r, translate) for r, _depth in chosen}
	# A box whose areas cannot each be drawn big enough to find is drawn without them; they are
	# a drill in away. Each round lays out again with those areas left out, which gives the
	# rest more room, until everything drawn is big enough.
	# When even the main areas will not all fit, the smallest unnamed one found from the gaps
	# between content goes first: a strip of links between a site's navigation and its banner
	# matters less than either, and the text overview still lists it.
	boxes: list[DrawnBox] = []
	xs = ys = None
	for _round in range(len(chosen) + 1):
		boxes, xs, ys = _layout(chosen, labels, window, width, height, places)
		cramped = {id(b.region) for b in _walkBoxes(boxes) if any(_isCramped(c) for c in b.children)}
		if cramped:
			chosen = [(r, d) for r, d in chosen if id(_parentOf(r, chosen)) not in cramped]
			continue
		spare = [b for b in boxes if b.region.source == "geometry" and not b.region.label]
		if not any(_isCramped(b) for b in boxes) or not spare:
			break
		dropped = min(spare, key=lambda b: b.region.box.area).region
		chosen = [(r, d) for r, d in chosen if r is not dropped and _parentOf(r, chosen) is not dropped]
	drawn = {id(b.region) for b in _walkBoxes(boxes)}
	overview = Overview(
		buffer, boxes, window, places, whole, xs, ys, [r for r in wanted if id(r) not in drawn]
	)
	_label(overview, labels, keys if keys is not None else {})
	style = style or CountStyle()
	for box in overview.walk():
		_placeDetails(box, _countsFor(box.region, translate, style))
	_draw(overview, labels)
	return overview


#: The tallest the screen view grows, in panels, before it settles for leaving areas out.
MAX_SCREEN_PANELS = 3
#: How much taller each try is than the one before, in panels.
SCREEN_PANEL_STEP = 0.5


def composeFitting(
	newBuffer: Callable[[int, int], object],
	page: Region,
	width: int,
	height: int,
	view: str,
	viewport: Box,
	translate: Callable[[str], list[int]],
	depth: int = 3,
	keys: dict[int, str] | None = None,
	focus: Region | None = None,
	style: CountStyle | None = None,
) -> Overview | None:
	"""Draw a view as tall as it has to be for everything in it to be drawn, up to a limit.

	A screen squeezed onto one panel by leaving areas out is a picture of less than the screen.
	So a view that does not fit is drawn half a panel taller at a time, up to
	`MAX_SCREEN_PANELS`, and the reader moves down it. What still does not fit is in
	`Overview.omitted`.

	:param height: one panel's height; the drawing is a whole number of half panels tall.
	:return: the overview, whose buffer is that tall, or None if no buffer could be made.
	"""
	keys = keys if keys is not None else {}
	overview = None
	steps = int((MAX_SCREEN_PANELS - 1) / SCREEN_PANEL_STEP) + 1
	for step in range(steps):
		tall = round(height * (1 + step * SCREEN_PANEL_STEP))
		# A fresh set of letters for each try, so a try thrown away does not use up letters.
		trial = dict(keys)
		overview = compose(
			newBuffer,
			page,
			width,
			tall,
			view,
			viewport,
			translate,
			depth,
			keys=trial,
			focus=focus,
			style=style,
		)
		# Grown again while anything was left out, or drawn too small to find: a main area that
		# will not fit is drawn regardless, as a sliver, rather than left out.
		if overview is None or not (overview.omitted or any(_isCramped(b) for b in overview.walk())):
			break
	if overview is not None:
		keys.clear()
		keys.update(trial)
	return overview


def _isCramped(box: DrawnBox) -> bool:
	return box.rect.width < MIN_BOX[0] or box.rect.height < MIN_BOX[1]


def _walkBoxes(boxes: list[DrawnBox]):
	for box in boxes:
		yield box
		yield from _walkBoxes(box.children)


def _layout(
	chosen: list[tuple[Region, int]],
	labels: dict[int, list[int]],
	window: Box,
	width: int,
	height: int,
	places: dict[int, Box],
) -> tuple[list[DrawnBox], "_Ruler", "_Ruler"]:
	"""Place the chosen regions on warped rulers, children inside their parents."""
	clips = {id(r): _clip(places[id(r)], window) for r, _depth in chosen}
	xs = _Ruler(
		[(clips[id(r)].left, clips[id(r)].right, _minWidth(labels[id(r)], d)) for r, d in chosen],
		window.left,
		window.right,
		width,
	)
	ys = _Ruler(
		[(clips[id(r)].top, clips[id(r)].bottom, _minHeight(d)) for r, d in chosen],
		window.top,
		window.bottom,
		height,
	)
	boxes: list[DrawnBox] = []
	byRegion: dict[int, DrawnBox] = {}
	skipped: set[int] = set()
	for region, d in chosen:
		clipped = clips[id(region)]
		left, right = xs.span(clipped.left, clipped.right)
		top, bottom = ys.span(clipped.top, clipped.bottom)
		rect = Box(left, top, right - left, bottom - top)
		drawn = DrawnBox(region, rect, d)
		parentRegion = _parentOf(region, chosen)
		if parentRegion is not None and id(parentRegion) in skipped:
			skipped.add(id(region))
			continue
		parent = byRegion.get(id(parentRegion))
		if parent is None:
			boxes.append(drawn)
		else:
			# Inside its parent's outline, never on it, so the two can be told apart. One that
			# falls outside it altogether is not drawn, rather than stretched to fill the parent
			# and drawn as though it were all of it.
			inner = Box(
				parent.rect.left + 1, parent.rect.top + 1, parent.rect.width - 2, parent.rect.height - 2
			)
			fitted = rect.intersection(inner)
			if fitted is None:
				skipped.add(id(region))
				continue
			drawn.rect = fitted
			parent.children.append(drawn)
		byRegion[id(region)] = drawn
	return boxes, xs, ys


def _clip(box: Box, window: Box) -> Box:
	return box.intersection(window) or Box(box.left, max(box.top, window.top), box.width, 0)


def _choose(
	regions: list[Region],
	window: Box,
	depth: int,
	places: dict[int, Box],
	level: int = 1,
) -> list[tuple[Region, int]]:
	"""The regions to draw, parents before children, with the level each is drawn at.

	Down to `depth` levels, and below that only the areas that matter whatever their depth
	(`MAJOR_KINDS`), drawn inside the deepest box that is drawn, since splitting by gaps nests
	blocks deeply.
	"""
	chosen = []
	for region in sorted(regions, key=lambda r: (places[id(r)].top, places[id(r)].left)):
		if not _inView(region, window, places):
			continue
		chosen.append((region, level))
		if level < depth:
			chosen += _choose(region.children, window, depth, places, level + 1)
		else:
			chosen += [(major, level + 1) for major in _majorWithin(region, window, places)]
	return chosen


#: The kinds of area drawn however deep they are found.
MAJOR_KINDS = frozenset(
	("form", "search", "tabs", "accordion", "navigation", "complementary", "region", "main")
)


def _inView(region: Region, window: Box, places: dict[int, Box]) -> bool:
	place = places[id(region)]
	clipped = place.intersection(window)
	# An area just showing at the edge of the screen is left out; an area thin only because the
	# page is long, like a banner on a whole page view, is not.
	return clipped is not None and not (clipped.height < 20 and clipped.height < place.height / 2)


def _majorWithin(region: Region, window: Box, places: dict[int, Box]) -> list[Region]:
	"""The major areas below a region, not inside one another, in reading order of position."""
	found = []
	stack = list(reversed(region.children))
	while stack:
		inner = stack.pop()
		if inner.kind in MAJOR_KINDS and _inView(inner, window, places):
			found.append(inner)
			continue
		stack.extend(reversed(inner.children))
	return sorted(found, key=lambda r: (places[id(r)].top, places[id(r)].left))


def _parentOf(region: Region, chosen: list[tuple[Region, int]]) -> Region | None:
	"""The deepest region drawn that holds this one, which for a major area found below the
	depth drawn is not the region directly holding it."""
	best, bestLevel = None, -1
	for candidate, level in chosen:
		if candidate is region or level <= bestLevel:
			continue
		if region in candidate.children or any(inner is region for inner in candidate.walk()):
			best, bestLevel = candidate, level
	return best


def _labelFor(region: Region, translate: Callable[[str], list[int]]) -> list[int]:
	word = shortLabel(region.kind)
	if not word and region.label:
		word = region.label.split()[0][:8]
	return translate(word) if word else []


def _labelWidth(cells: int) -> int:
	return max(0, cells * CELL_STRIDE - 1)


def _minWidth(cells: list[int], level: int) -> int:
	"""The pins across a box asks the ruler for, so that it is still big enough once drawn.

	A box inside another is drawn inside its outline, which takes a pin off each side where the
	two touch, and a pin more for every level further in; so it asks for those too. A main area asks
	for room for its whole label where that is short; a long one gets a key letter instead, so it
	is not allowed to claim the whole panel.
	"""
	nesting = 2 * (level - 1)
	wanted = 2 + 2 * LABEL_MARGIN + _labelWidth(min(len(cells), 4))
	return max(MIN_BOX[0], wanted if level == 1 else MIN_BOX[0]) + nesting


def _minHeight(level: int) -> int:
	"""The pins down a box asks the ruler for. See `_minWidth` for the nesting."""
	return MIN_BOX[1] + 2 * (level - 1)


def _label(overview: Overview, labels: dict[int, list[int]], keys: dict[int, str]) -> None:
	"""Decide each box's label: its short word where it fits clear of the boxes inside it,
	else a key letter, else nothing (a press still names it).

	A letter given to an area is kept in `keys` and used for it again in any other view of the
	page, so the key a reader heard stays true as they zoom and move. Search gets its own letter,
	s, whatever else is on the page; see `RESERVED_KEYS`.
	"""
	for box in overview.walk():
		cells = labels[id(box.region)]
		spot = _labelSpot(box, _labelWidth(len(cells)))
		if cells and spot is not None:
			box.label = "word"
			box.labelAt = spot
			continue
		spot = _labelSpot(box, _labelWidth(1))
		if spot is None:
			continue
		box.labelAt = spot
		letter = keys.get(id(box.region))
		if letter is None:
			used = set(keys.values())
			reserved = RESERVED_KEYS.get(box.region.kind)
			if reserved and reserved not in used:
				letter = reserved
			else:
				# A second search box takes an ordinary letter; nothing takes a reserved one.
				kept = set(RESERVED_KEYS.values())
				letter = next((c for c in KEY_LETTERS if c not in used and c not in kept), "")
			if letter:
				keys[id(box.region)] = letter
		box.key = letter


def _labelSpot(box: DrawnBox, labelWidth: int) -> Box | None:
	"""Where a label of this width goes inside a box, clear of its children, or None.

	The top left corner first, where a hand looks for it; then the leftmost clear place along the
	top, just past whatever box inside holds the corner; then the same along the bottom. A banner
	whose search field sits at its left still has room further along. Leftmost rather than the
	far corner, so the label stays near where a hand looks.
	"""
	if labelWidth <= 0:
		return None
	r = box.rect
	inset = 1 + LABEL_MARGIN
	if labelWidth > r.width - 2 * inset or CELL_HEIGHT > r.height - 2 * inset:
		return None
	lefts = range(r.left + inset, r.right - inset - labelWidth + 1)
	for top in (r.top + inset, r.bottom - inset - CELL_HEIGHT):
		for left in lefts:
			spot = Box(left, top, labelWidth, CELL_HEIGHT)
			clearance = Box(spot.left - 1, spot.top - 1, spot.width + 2, spot.height + 2)
			if not any(child.rect.intersection(clearance) for child in box.children):
				return spot
	return None


#: The short word each count is drawn with: NVDA's own braille abbreviation for the role, which
#: a braille reader meets on every line, rather than the word a press says. NVDA supplies them
#: in the reader's language (`CountStyle`); these English ones are for when it does not. Fields
#: are drawn as edit fields, which most are; a press says "fields".
COUNT_WORDS = {"links": "lnk", "buttons": "btn", "fields": "edt", "tabs": "tab", "sections": "sect"}


@dataclass(frozen=True)
class CountStyle:
	"""How counts are drawn for this reader and this display.

	:ivar words: the short word for each kind of count, as `COUNT_WORDS`.
	:ivar shapes: the glyph drawn in place of the word, as rows of pins ("O" raised), for each
		kind of count that has one, when the reader has glyphs on and the display can show
		them. Empty for words throughout.
	"""

	words: dict = field(default_factory=lambda: dict(COUNT_WORDS))
	shapes: dict = field(default_factory=dict)


@dataclass
class _Count:
	"""One count ready to place: the words a press says, and what is drawn for it."""

	text: str
	#: The number, in braille cells.
	number: list[int]
	#: The short word after it, in braille cells; empty for a number alone or a shape.
	word: list[int] = field(default_factory=list)
	#: A glyph drawn after the number instead of the word, as rows of pins.
	shape: list[str] | None = None

	@property
	def numberWidth(self) -> int:
		return _labelWidth(len(self.number))

	@property
	def wordWidth(self) -> int:
		if self.shape:
			return len(self.shape[0])
		return _labelWidth(len(self.word))

	@property
	def wordGap(self) -> int:
		"""Pins from the number's left to the word's: a blank cell between, as a space; a glyph
		follows straight on, its shape being no letter to run into the number."""
		if self.shape:
			return len(self.number) * CELL_STRIDE
		return len(self.number) * CELL_STRIDE + CELL_STRIDE

	@property
	def hasWord(self) -> bool:
		return bool(self.shape or self.word)

	@property
	def oneLineWidth(self) -> int:
		return self.wordGap + self.wordWidth if self.hasWord else self.numberWidth


def _ownCountWord(name: str) -> str:
	""":return: the short word drawn with a count NVDA has no braille label for: items, in lists,
	trees, grids and menus. NVDA abbreviates a tree item and a list but not their items, and its
	fallback, the role's whole name, is too long for a count drawn in a box."""
	if name == "items":
		# Translators: drawn in braille after the number of items in a list, tree, grid or menu in
		# the layout overview, as NVDA abbreviates roles in braille ("lnk", "btn"). Keep it as
		# short as you can: pressing in the box says the full words.
		return pgettext("braille count word", "itm")
	return name


def _countsFor(region: Region, translate: Callable[[str], list[int]], style: CountStyle) -> list[_Count]:
	"""What a box holds, most telling first, ready to draw. See `Region.counts`.

	A tab strip's own count is just the number: its label already says tabs.
	"""
	counts = []
	for name, count in region.counts:
		number = translate(str(count))
		if not number:
			continue
		if (region.kind, name) == ("tabs", "tabs"):
			counts.append(_Count(str(count), number))
			continue
		shape = style.shapes.get(name)
		word = [] if shape else translate(style.words.get(name) or _ownCountWord(name))
		counts.append(_Count(countWords(name, count), number, word, shape or None))
	return counts


def _placeDetails(box: DrawnBox, counts: list[_Count]) -> None:
	"""Put a box's counts with its label, as many as there is already room for, in order.

	On the label's own line first, after a blank cell, so the first reads as one phrase with
	it, "nav #f lnk"; then on the lines under the label, or over it for a label in a bottom
	corner. The lines follow one another with no blank row between, as the Monarch's lines of
	text do. Counts on one line are a blank cell apart. They go in order and stop at the first
	that will not fit anywhere left, so a count is never shown while a more telling one is not.

	A count that does not fit where the last one ended is looked for on every line down the box
	(up it, for a label at the bottom), at the label's left and then anywhere clear along the
	line. Where it fits on no line whole, its number may go on one line and its word under it,
	so a narrow strip down the side of a box still says what the box holds.

	Never by making room: the boxes are laid out for their labels alone, and what does not fit,
	clear of the boxes inside, is left for a press to say. A box with no label or key letter has
	nothing for counts to belong to, so gets none.
	"""
	label, r = box.labelAt, box.rect
	if label is None or not counts:
		return
	inset = 1 + LABEL_MARGIN
	downward = label.top <= r.top + inset

	def lineTop(index: int) -> int:
		if index == 0:
			return label.top
		return label.bottom + (index - 1) * CELL_HEIGHT if downward else label.top - index * CELL_HEIGHT

	def inBox(index: int) -> bool:
		top = lineTop(index)
		return top >= r.top + inset and top + CELL_HEIGHT <= r.bottom - inset

	def whole(count: _Count, left: int, index: int) -> list[Box] | None:
		spot = Box(left, lineTop(index), count.oneLineWidth, CELL_HEIGHT)
		return [spot] if _fitsIn(box, spot, inset) else None

	def stacked(count: _Count, left: int, index: int) -> list[Box] | None:
		# The number on this line and the word on the next, both from the same left; the next
		# line is above, for a label at the bottom, and the number still reads first.
		if not count.hasWord or index == 0 or not inBox(index + 1):
			return None
		upper, lower = sorted((lineTop(index), lineTop(index + 1)))
		spots = [
			Box(left, upper, count.numberWidth, CELL_HEIGHT),
			Box(left, lower, count.wordWidth, CELL_HEIGHT),
		]
		return spots if all(_fitsIn(box, s, inset) for s in spots) else None

	line, x = 0, label.right + 1 + CELL_STRIDE
	for count in counts:
		# Where the last one ended, to read on from it.
		spots = whole(count, x, line)
		while spots is None:
			# Not there: a line further on, at the label's left, or else anywhere clear on it;
			# whole if it will go, or else in two.
			line += 1
			if not inBox(line):
				return
			lefts = [label.left, *range(r.left + inset, r.right - inset + 1)]
			for shape in (whole, stacked):
				spots = next((s for s in (shape(count, left, line) for left in lefts) if s), None)
				if spots:
					break
		marks = _marks(count, spots)
		left, top = min(s.left for s in spots), min(s.top for s in spots)
		area = Box(left, top, max(s.right for s in spots) - left, max(s.bottom for s in spots) - top)
		box.details.append((count.text, area, marks))
		if len(spots) == 2:
			line += 1
		# Read on from the end of what is on the line now reached: after a count stacked over a
		# label at the bottom, that is the number, above, not the word.
		onLine = [spot for spot in spots if spot.top == lineTop(line)] or spots
		x = onLine[-1].right + 1 + CELL_STRIDE


def _fitsIn(box: DrawnBox, spot: Box, inset: int) -> bool:
	""":return: whether a spot is inside a box's margin and a cell clear of the boxes inside it."""
	r = box.rect
	if spot.top < r.top + inset or spot.bottom > r.bottom - inset:
		return False
	if spot.left < r.left + inset or spot.right > r.right - inset:
		return False
	clearance = Box(spot.left - 1, spot.top - 1, spot.width + 2, spot.height + 2)
	return not any(child.rect.intersection(clearance) for child in box.children)


def _marks(count: _Count, spots: list[Box]) -> list[tuple[int, int, list]]:
	"""What to draw for a count placed at these spots: braille cells, as a list of cell values,
	or a glyph, as a list of rows of pins; each with where its top left is."""
	first = spots[0]
	marks: list[tuple[int, int, list]] = [(first.left, first.top, count.number)]
	if not count.hasWord:
		return marks
	if len(spots) == 2:
		left, top = spots[1].left, spots[1].top
	else:
		left, top = first.left + count.wordGap, first.top
	marks.append((left, top, count.shape or count.word))
	return marks


def _draw(overview: Overview, labels: dict[int, list[int]]) -> None:
	buffer = overview.buffer
	for box in overview.walk():
		r = box.rect
		buffer.rect(r.left, r.top, r.width, r.height)
		if box.labelAt is not None and box.label:
			buffer.text(box.labelAt.left, box.labelAt.top, labels[id(box.region)], CELL_STRIDE)
		elif box.labelAt is not None and box.key:
			buffer.text(box.labelAt.left, box.labelAt.top, _keyCells(box.key), CELL_STRIDE)
		for _text, _area, marks in box.details:
			for left, top, ink in marks:
				if ink and isinstance(ink[0], str):
					_stamp(buffer, ink, left, top)
				else:
					buffer.text(left, top, ink, CELL_STRIDE)


def _stamp(buffer, rows: list[str], left: int, top: int) -> None:
	"""Raise the pins of a glyph, given as rows with "O" for a raised pin."""
	for down, row in enumerate(rows):
		for across, pin in enumerate(row):
			if pin not in " .":
				buffer.setDot(left + across, top + down)


#: Braille letters a to z as eight dot cell values (dots 1 to 6 only), for key letters. Not
#: through the reader's table: a key letter is a symbol on a map, and in a contracted table a
#: lone letter can be a whole word sign, which is exactly what a key letter is not.
_LETTER_DOTS = {
	"a": (1,),
	"b": (1, 2),
	"c": (1, 4),
	"d": (1, 4, 5),
	"e": (1, 5),
	"f": (1, 2, 4),
	"g": (1, 2, 4, 5),
	"h": (1, 2, 5),
	"i": (2, 4),
	"j": (2, 4, 5),
	"k": (1, 3),
	"l": (1, 2, 3),
	"m": (1, 3, 4),
	"n": (1, 3, 4, 5),
	"o": (1, 3, 5),
	"p": (1, 2, 3, 4),
	"q": (1, 2, 3, 4, 5),
	"r": (1, 2, 3, 5),
	"s": (2, 3, 4),
	"t": (2, 3, 4, 5),
	"u": (1, 3, 6),
	"v": (1, 2, 3, 6),
	"w": (2, 4, 5, 6),
	"x": (1, 3, 4, 6),
	"y": (1, 3, 4, 5, 6),
	"z": (1, 3, 5, 6),
}


def _keyCells(letter: str) -> list[int]:
	return [sum(1 << (dot - 1) for dot in _LETTER_DOTS.get(letter, ()))]


class _Ruler:
	"""One axis of the drawing, warped so every box gets the pins it needs.

	Marks go at the window's edges and at every box's edges. The stretches between marks start
	with pins in proportion to their length on the page; then, a few rounds over, each box short
	of its minimum takes what it lacks from the stretches it does not cover, in proportion to
	their size. Nothing changes order, so boxes stay where they are relative to each other.
	"""

	ROUNDS = 24

	def __init__(self, spans: list[tuple[int, int, int]], low: int, high: int, pins: int):
		self.low, self.high, self.pins = low, high, pins
		marks = sorted({low, high, *(min(max(v, low), high) for a, b, _need in spans for v in (a, b))})
		self.marks = marks
		lengths = [b - a for a, b in zip(marks, marks[1:])]
		total = sum(lengths) or 1
		alloc = [pins * length / total for length in lengths]
		index = {mark: i for i, mark in enumerate(marks)}
		needs = [
			(index[min(max(a, low), high)], index[min(max(b, low), high)], minimum)
			for a, b, minimum in spans
			if b > a
		]
		for _round in range(self.ROUNDS):
			short = False
			for first, last, minimum in needs:
				have = sum(alloc[first:last])
				# Half a pin over, so that rounding to whole pins cannot leave a box one short.
				target = minimum + 0.5
				if have >= target or last <= first:
					continue
				short = True
				lack = target - have
				inside = alloc[first:last]
				share = sum(inside) or (last - first)
				for i in range(first, last):
					alloc[i] += lack * ((alloc[i] / share) if sum(inside) else 1 / (last - first))
			scale = pins / (sum(alloc) or 1)
			alloc = [a * scale for a in alloc]
			if not short:
				break
		self.alloc = alloc
		self.starts = [0.0]
		for a in alloc:
			self.starts.append(self.starts[-1] + a)

	def at(self, value: int) -> float:
		value = min(max(value, self.low), self.high)
		for i in range(len(self.marks) - 1):
			a, b = self.marks[i], self.marks[i + 1]
			if value <= b:
				fraction = (value - a) / (b - a) if b > a else 0
				return self.starts[i] + fraction * self.alloc[i]
		return float(self.pins)

	def valueAt(self, pin: float) -> float:
		"""The page coordinate at a pin: `at` read backwards."""
		pin = min(max(pin, 0.0), float(self.pins))
		for i in range(len(self.alloc)):
			if pin <= self.starts[i + 1] or i == len(self.alloc) - 1:
				a, b = self.marks[i], self.marks[i + 1]
				if self.alloc[i] <= 0:
					return float(a)
				return a + (pin - self.starts[i]) / self.alloc[i] * (b - a)
		return float(self.high)

	def span(self, a: int, b: int) -> tuple[int, int]:
		"""The pins from a to b, as a start and an end one past the last pin."""
		start, end = round(self.at(a)), round(self.at(b))
		end = min(max(end, start + 1), self.pins)
		return min(start, end - 1), end
