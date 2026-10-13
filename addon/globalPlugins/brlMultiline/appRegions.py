# BrlMultiline: the main areas of a Windows application's window and where they are.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The main areas of an application's window, found from UI Automation, with no AI.

The layout overview draws a web page's areas as labelled boxes (`layoutRegions`,
`layoutDraw`). This module finds an application window's areas, from what UI Automation
reports, as the same tree of `Region`, so the drawing, the presses and drilling in work on it
unchanged. `docs/design/layout-overview-plan.md` has the captures the rules rest on, made by
`spikes/appLayoutProbe.py`.

It is pure Python with no NVDA imports. The input is one element as the probe records it, a
dict with its control type, name, automation id, class, rectangle relative to the window, and
its children; the output is a page region the size of the window.

An application is easier than a web page: its controls already say what they are. The rules,
in the order they are applied:

1. **What cannot be seen goes.** An element with no rectangle, offscreen, covered by another of
   the application's windows (an inactive Explorer tab), outside what holds it, or too small to
   be an area is left out, with everything in it.
2. **A control that frames an area is one.** Menu bars, toolbars and app bars, the ribbon, tab
   strips, trees, lists, grids, documents and status bars are areas of their own kind; so is
   anything with a UI Automation landmark, as newer applications give their navigation, search
   and main content.
3. **Otherwise a name makes an area.** A pane or group with a name a reader would want, such as
   "Folders" or "Recommended settings", is an area called that. A name that is the window's
   class, or a programmer's word such as "NUIDocumentWindow" or "ASEditorWnd", is not, and the
   element is gone through as a wrapper, its areas taken up by the one around it.
4. **Wrappers collapse.** Office stacks four or five panes on the one rectangle before the
   ribbon or the status bar inside them. An area whose only area inside has nearly its
   rectangle is replaced by whichever of the two says more about what it is.
5. **What each holds is counted:** tabs, items (in lists, trees, grids and menus), fields,
   buttons and links, in the whole of the area.
6. **The focus is placed.** Every area on the chain of elements holding the focus is marked, so
   the overview can say where the reader is.
"""

from __future__ import annotations

import re

from .layoutRegions import MIN_AREA, MIN_SIDE, Box, Region, mentionsSearch

#: The kind of area each control type frames.
CONTROL_KINDS = {
	"MenuBar": "menubar",
	"ToolBar": "toolbar",
	"AppBar": "toolbar",
	"StatusBar": "statusbar",
	"Tab": "tabs",
	"Tree": "tree",
	"List": "list",
	"DataGrid": "grid",
	"Table": "grid",
	"Document": "document",
}

#: The kind of area each UI Automation landmark is.
LANDMARK_KINDS = {
	"Navigation": "navigation",
	"Search": "search",
	"Main": "main",
	"Form": "form",
	"Custom": "region",
}

#: Control types that can be an area called by its name, when nothing else makes it one.
NAMEABLE = frozenset(("Pane", "Group", "Custom", "Window"))

#: Items, counted on the area holding them.
ITEM_TYPES = frozenset(("TreeItem", "ListItem", "DataItem", "MenuItem", "HeaderItem"))
FIELD_TYPES = frozenset(("Edit", "ComboBox", "CheckBox", "RadioButton", "Slider", "Spinner"))
BUTTON_TYPES = frozenset(("Button", "SplitButton"))

#: Names that say what an element is to its programmer, not what it is to a reader. Kept short,
#: since anything with a space in it is usually written for people.
GENERIC_NAMES = frozenset(("control host", "property field", "shell folder view"))

#: One run of letters and digits with capitals inside it, such as "NUIDocumentWindow",
#: "DropShadowTop" or "ASEditorWnd", or a word with a number on the end, such as "Document12": a
#: programmer's identifier, not a name.
_IDENTIFIER = re.compile(r"^(?:[A-Za-z][a-z0-9]*[A-Z][A-Za-z0-9]*|[A-Za-z]+\d+)$")

#: Kinds that say nothing about an area unless they hold something or have a name: an empty
#: unnamed toolbar or list is a frame left behind by the application.
_EMPTY_UNLESS_NAMED = frozenset(("toolbar", "list", "tree", "menubar"))

#: A strip along the bottom of the window no taller than this, holding only text, is its status
#: bar, though nothing calls it one (Notepad's).
STATUS_STRIP_HEIGHT = 40

#: How the kinds rank when a wrapper and the one area in it are to be collapsed into one: the
#: higher says more about what the area is.
_RANK = {
	"area": 1,
	"toolbar": 2,
	"region": 3,
	"form": 3,
	"ribbon": 4,
	"menubar": 4,
	"statusbar": 4,
	"tabs": 4,
	"tree": 4,
	"list": 4,
	"grid": 4,
	"document": 4,
	"navigation": 5,
	"search": 5,
	"main": 5,
}

#: An area whose only area inside covers this share of it is a wrapper around that one.
COLLAPSE_SHARE = 0.85


def findAppRegions(window: dict, windowRect: list | None = None, focusPath: list | None = None) -> Region:
	"""The window as a tree of regions, the window itself at the top.

	:param window: the window's element, as `spikes/appLayoutProbe.py` records it.
	:param windowRect: the window on screen, (left, top, width, height); only its size is used.
		The window element's own rectangle if None.
	:param focusPath: the runtime ids of the focus and every element above it.
	"""
	own = window.get("rect") or [0, 0, 0, 0]
	width = (windowRect or own)[2]
	height = (windowRect or own)[3]
	bounds = Box(0, 0, width, height)
	holding = {tuple(r) for r in focusPath or [] if r}
	title = _clean(window.get("name"))
	regions = _regionsIn(window, bounds, holding, title, bounds)
	page = Region("page", title, bounds, "page", children=regions)
	page.children = [_collapse(r) for r in page.children]
	_count(page, window)
	page.hasFocus = any(r.hasFocus for r in page.children)
	return page


def _clean(name) -> str:
	return " ".join(str(name).split()) if name else ""


def _isMeaningful(name: str, record: dict, title: str) -> bool:
	""":return: whether a name is one a reader would want an area called."""
	if not name or name == title:
		return False
	if name.lower() in GENERIC_NAMES or name == record.get("className"):
		return False
	return not _IDENTIFIER.match(name)


def _isHidden(record: dict) -> bool:
	""":return: whether an element cannot be seen: offscreen, covered by another of the
	application's windows, or outside what holds it. Shared by finding areas and counting what
	they hold, so an area does not count what it was found without (found in review)."""
	return bool(record.get("offscreen") or record.get("covered") or record.get("outside"))


def _visibleBox(record: dict, bounds: Box) -> Box | None:
	if _isHidden(record):
		return None
	rect = record.get("rect")
	if not rect:
		return None
	box = Box(*rect).intersection(bounds)
	if box is None or box.width < MIN_SIDE or box.height < MIN_SIDE or box.area < MIN_AREA:
		return None
	return box


def _types(record: dict) -> set:
	""":return: the control types of everything inside an element."""
	found = set()
	for child in record.get("children", []):
		found.add(child.get("type"))
		found |= _types(child)
	return found


def _isScrollBar(record: dict) -> bool:
	""":return: whether an element is a scroll bar, or only holds one, as Office's panes called
	"Vertical" and "Horizontal" do."""
	if record.get("type") in ("ScrollBar", "Thumb"):
		return True
	# A tree or list whose items were counted rather than walked holds only its scroll bar too.
	if record.get("type") in CONTROL_KINDS or _heldItems(record):
		return False
	inside = _types(record)
	return "ScrollBar" in inside and inside <= {"ScrollBar", "Thumb", "Button", "Pane"}


def _heldItems(record: dict) -> dict:
	""":return: the items an element holds directly, by type, counted or walked."""
	held = dict(record.get("items") or {})
	for child in record.get("children", []):
		kind = child.get("type")
		if kind in ITEM_TYPES or kind == "TabItem":
			held[kind] = held.get(kind, 0) + 1
	return held


#: An edit control is an editing surface, an area of its own, when it is at least this tall and
#: takes at least this share of the window: several lines of text, not a field to type a word in.
EDITOR_MIN_HEIGHT = 60
EDITOR_MIN_SHARE = 0.1


def _isEditingSurface(box: Box, window: Box) -> bool:
	return box.height >= EDITOR_MIN_HEIGHT and box.area >= window.area * EDITOR_MIN_SHARE


def _isStatusStrip(record: dict, box: Box, window: Box) -> bool:
	""":return: whether an unnamed element is a status bar by where it is and what it holds."""
	if box.height > STATUS_STRIP_HEIGHT or box.width < window.width * 0.9:
		return False
	if window.bottom - box.bottom > 4:
		return False
	inside = _types(record)
	return bool(inside) and inside <= {"Text", "Pane", "Group"}


def _classify(record: dict, title: str, box: Box, window: Box, parent: str) -> tuple[str, str]:
	""":return: what kind of area an element is and what it is called, or ("", "") for none.

	:param parent: the kind of the area it is in, or "" for the window.
	"""
	name = _clean(record.get("name"))
	meaningful = _isMeaningful(name, record, title)
	label = name if meaningful else ""
	kind = record.get("type")
	landmark = record.get("landmark")
	if landmark in LANDMARK_KINDS:
		found = LANDMARK_KINDS[landmark]
		if found == "region" and not label:
			return "", ""
		return found, label
	if _isScrollBar(record):
		return "", ""
	if kind in ("Edit", "ComboBox") and mentionsSearch(name, record.get("automationId") or ""):
		return "search", ""
	if kind == "Edit" and _isEditingSurface(box, window):
		# The editor of an application that shows it as a plain edit control rather than a
		# document, as a classic Notepad does (found in review). A form field is not one.
		return "document", label
	if name.lower() == "ribbon":
		return "ribbon", ""
	held = _heldItems(record)
	if held.get("TabItem") and kind != "Tab":
		# A tab strip's own list of tabs is the strip, not a list beside it.
		return ("", "") if parent == "tabs" else ("tabs", label)
	found = CONTROL_KINDS.get(kind)
	if found:
		return found, label
	if kind in NAMEABLE and (held.get("ListItem") or held.get("TreeItem")) and not meaningful:
		# An unnamed container of items is a list, as Settings' navigation pane is.
		return ("tree" if held.get("TreeItem") else "list"), ""
	if kind in NAMEABLE and meaningful:
		return "area", label
	if kind in NAMEABLE and _isStatusStrip(record, box, window):
		return "statusbar", ""
	return "", ""


def _regionsIn(
	record: dict, bounds: Box, holding: set, title: str, window: Box, parent: str = ""
) -> list[Region]:
	"""The areas among an element's children, each with the areas inside it; a wrapper's areas
	are taken up as its parent's own. Items are counted, never gone into."""
	found = []
	for child in record.get("children", []):
		if child.get("type") in ITEM_TYPES or child.get("type") == "TabItem":
			continue
		box = _visibleBox(child, bounds)
		if box is None:
			continue
		kind, label = _classify(child, title, box, window, parent)
		inner = _regionsIn(child, box, holding, title, window, kind or parent)
		if not kind:
			found.extend(inner)
			continue
		region = Region(kind, label, box, "uia", children=inner, screenBox=box)
		region.record = child
		if kind in _EMPTY_UNLESS_NAMED and not label and not inner:
			_count(region, child)
			if not region.counts:
				continue
		region.anchor = tuple(child["runtimeId"]) if child.get("runtimeId") else None
		region.hasFocus = bool(region.anchor and region.anchor in holding) or any(r.hasFocus for r in inner)
		found.append(region)
	return sorted(found, key=lambda r: (r.box.top, r.box.left))


def _collapse(region: Region) -> Region:
	"""Collapse wrappers, from the bottom up. See rule 4."""
	region.children = [_collapse(c) for c in region.children]
	if len(region.children) != 1:
		return region
	inner = region.children[0]
	twin = (inner.kind, inner.label) == (region.kind, region.label)
	if inner.box.area < region.box.area * COLLAPSE_SHARE and not twin:
		return region
	keep, gone = (inner, region) if _RANK.get(inner.kind, 0) >= _RANK.get(region.kind, 0) else (region, inner)
	if keep is region:
		region.children = inner.children
	if not keep.label:
		keep.label = gone.label
	keep.hasFocus = keep.hasFocus or gone.hasFocus
	keep.box = region.box
	keep.screenBox = region.box
	# What it holds is counted in the whole of what it now stands for.
	keep.record = region.record or inner.record
	return keep


def _count(region: Region, record: dict) -> None:
	"""Count what each area holds, in the whole of it. See rule 5."""
	for child in region.children:
		if child.record is not None:
			_count(child, child.record)
	if region.kind == "page":
		return
	tally = {"tabs": 0, "items": 0, "fields": 0, "buttons": 0, "links": 0}
	_tally(record, tally)
	region.tabs = tally["tabs"]
	region.items = tally["items"]
	region.fields = tally["fields"]
	region.buttons = tally["buttons"]
	region.links = tally["links"]


def _tally(record: dict, tally: dict) -> None:
	for kind, count in (record.get("items") or {}).items():
		if kind == "TabItem":
			tally["tabs"] += count
		elif kind in ITEM_TYPES:
			tally["items"] += count
	for child in record.get("children", []):
		if _isHidden(child):
			continue
		kind = child.get("type")
		if kind == "TabItem":
			tally["tabs"] += 1
		elif kind in ITEM_TYPES:
			tally["items"] += 1
		elif kind in FIELD_TYPES:
			tally["fields"] += 1
		elif kind in BUTTON_TYPES:
			tally["buttons"] += 1
		elif kind == "Hyperlink":
			tally["links"] += 1
		_tally(child, tally)
