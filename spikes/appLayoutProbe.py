# BrlMultiline: spike for a screen layout overview of Windows applications.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

r"""Console spike: what can UI Automation tell us about where things are in an application?

The layout overview (`docs/design/layout-overview-plan.md`) draws a web page's main areas as
labelled boxes in their real places. Step 6 of its plan does the same for Windows applications.
An application has no landmarks as a rule; its shape comes from panes, toolbars, ribbons, menu
bars, status bars, trees, lists and tab controls. Before writing rules for it, these questions
need answers that reading documentation cannot give:

1. Which control types frame an application's main areas, and do they carry names or
   automation ids worth showing? How deep are the panes nested before anything has a name?
2. Are the bounding rectangles sensible: inside the window, not overlapping, and what do
   offscreen and collapsed elements report?
3. Do newer applications (WinUI, WebView2, Electron) report UIA landmarks, as web pages do?
4. What does reading the tree cost: one call per element, or one call for the whole window
   with a subtree cache, and how does that grow with the number of elements?
5. Where is the focus, so an overview can say which area it is in?

Bring the application to the front, put the focus where you would normally work in it, open the
NVDA Python console (NVDA+control+z), and run:

	import sys; sys.path.insert(0, r"C:\code\BrlMultiline\spikes")
	import appLayoutProbe as probe
	probe.dump()       # the window's UI Automation tree as an outline, with rectangles
	probe.cost()       # how long the tree takes to read, one call per element against one call
	probe.save()       # the outline and a JSON file in spikes\layoutProbeOut

Every function reads the console's snapshot of the foreground window and the focus from when
NVDA+control+z was pressed, so it is the application, not the console. Pass an NVDA object to
look at another window: `probe.dump(fg)` is the same as `probe.dump()`.

`dump` and `save` stop at `maxDepth` levels and `maxNodes` elements, by default 12 and 800, so
a huge window cannot hold NVDA up for long. Raise them to see more: `probe.save(maxNodes=3000)`.
`probe.save(cost=True)` also times reading the whole window in one call and saves that too.

What the walk leaves out, so that its budget goes on the window's structure:

- Items (tree items, list items, data items, tab items, menu items) are counted, by type, on
  the element that holds them, and not walked into. A navigation tree's six hundred folders are
  one line saying so, rather than the whole budget (found in the first captures: Explorer's
  tree used 612 of its 800 elements, and the address bar was never reached).
- Elements that hold nothing, such as text, buttons and scroll bars, are recorded without
  asking for their children, which saves a cross process call each.
- An element in a window of its own that another of the application's windows covers is
  recorded, marked covered, and not walked into. Explorer's inactive tabs report the same
  rectangle as the active one, are not offscreen, and are visible windows as far as Windows
  says; what tells them apart is which child window the application's own window has on top
  at their points (`ChildWindowFromPointEx`, which ignores other applications, the console
  included). Asking whether the window is visible does not do: the input site of a XAML
  island, such as Explorer's address bar, is a hidden window by design, under a visible one.

Each rectangle is clipped to the one around it, so a tree that reports its whole scrolled
content as its height is recorded as the part that can be seen, with the full size kept as
`fullRect`. Names are trimmed of the spaces and line breaks some old applications put in them.

`save` writes to the layoutProbeOut folder next to this file,
C:\code\BrlMultiline\spikes\layoutProbeOut, in files named after the application and the time,
such as explorer-20261013-143005.txt and .json, and prints the full paths when it is done.
Window titles can name a document or a message, so look at the files before sharing them.

After editing this file, reload it, since `import` is a no op once a module is loaded:

	import importlib; probe = importlib.reload(probe)

Rectangles are reported relative to the window's own top left corner, in screen pixels.

Nothing here changes the application, moves the focus or presses anything. It only reads.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from typing import Any, Optional

import ctypes
from ctypes import wintypes

import api
import UIAHandler

#: The properties read for every element, by the name they have in the JSON.
PROPERTIES = {
	"type": UIAHandler.UIA_ControlTypePropertyId,
	"localizedType": UIAHandler.UIA_LocalizedControlTypePropertyId,
	"name": UIAHandler.UIA_NamePropertyId,
	"automationId": UIAHandler.UIA_AutomationIdPropertyId,
	"className": UIAHandler.UIA_ClassNamePropertyId,
	"framework": UIAHandler.UIA_FrameworkIdPropertyId,
	"rect": UIAHandler.UIA_BoundingRectanglePropertyId,
	"offscreen": UIAHandler.UIA_IsOffscreenPropertyId,
	"enabled": UIAHandler.UIA_IsEnabledPropertyId,
	"focusable": UIAHandler.UIA_IsKeyboardFocusablePropertyId,
	"hasFocus": UIAHandler.UIA_HasKeyboardFocusPropertyId,
	"landmark": UIAHandler.UIA_LandmarkTypePropertyId,
	"localizedLandmark": UIAHandler.UIA_LocalizedLandmarkTypePropertyId,
	"runtimeId": UIAHandler.UIA_RuntimeIdPropertyId,
	"hwnd": UIAHandler.UIA_NativeWindowHandlePropertyId,
}

#: Control types that are the items of a list, tree, grid, tab strip or menu: counted on the
#: element holding them, not walked into.
ITEM_TYPES = frozenset(("TreeItem", "ListItem", "DataItem", "TabItem", "MenuItem", "HeaderItem"))

#: Control types that hold nothing an overview needs, so their children are never asked for.
LEAF_TYPES = frozenset(
	(
		"Text",
		"Button",
		"Image",
		"Edit",
		"Hyperlink",
		"CheckBox",
		"RadioButton",
		"ScrollBar",
		"Thumb",
		"Slider",
		"ProgressBar",
		"Separator",
		"Spinner",
		"SplitButton",
		"ToolTip",
		"Header",
	),
)

#: Control types an overview of an application is likely to make areas of, for the summary.
FRAMING_TYPES = frozenset(
	(
		"Pane",
		"Window",
		"ToolBar",
		"MenuBar",
		"StatusBar",
		"TitleBar",
		"Tree",
		"List",
		"DataGrid",
		"Table",
		"Tab",
		"Group",
		"Document",
		"Custom",
		"AppBar",
		"SemanticZoom",
		"Header",
	),
)


def _names(suffix: str) -> dict:
	""":return: UIA's constants ending in `suffix`, by value, with the suffix and prefix taken off:
	50000 to "Button" for control types."""
	found = {}
	for attribute in dir(UIAHandler):
		if attribute.startswith("UIA_") and attribute.endswith(suffix):
			value = getattr(UIAHandler, attribute)
			if isinstance(value, int):
				found[value] = attribute[len("UIA_") : -len(suffix)]
	return found


CONTROL_TYPES = _names("ControlTypeId")
LANDMARK_TYPES = _names("LandmarkTypeId")


def _snapshot(name: str):
	"""A variable from the console's snapshot of when it was opened, or None."""
	try:
		import pythonConsole

		snapshot = pythonConsole.consoleUI.console._namespaceSnapshotVars
		if snapshot:
			return snapshot.get(name)
	except Exception:
		pass
	return None


def _window(obj=None):
	"""The top level window to read: obj's, by default the console's foreground snapshot."""
	obj = obj or _snapshot("fg") or api.getForegroundObject()
	handle = getattr(obj, "windowHandle", None)
	if not handle:
		print("No window to read. Bring the application to the front and reopen the console.")
		return None, None
	return obj, handle


def _cacheRequest(scope: int):
	handler = UIAHandler.handler
	request = handler.clientObject.CreateCacheRequest()
	for propertyId in PROPERTIES.values():
		try:
			request.addProperty(propertyId)
		except Exception:
			# An older Windows without landmarks, say: read what there is.
			pass
	request.TreeScope = scope
	request.TreeFilter = handler.clientObject.ControlViewCondition
	return request


def _value(element, name: str):
	try:
		value = element.GetCachedPropertyValue(PROPERTIES[name])
	except Exception:
		return None
	if name == "rect":
		try:
			left, top, width, height = (float(v) for v in value)
			return [round(left), round(top), round(width), round(height)]
		except Exception:
			return None
	if name == "runtimeId":
		try:
			return [int(v) for v in value]
		except Exception:
			return None
	if name == "type":
		return CONTROL_TYPES.get(value, value)
	if name == "landmark":
		return LANDMARK_TYPES.get(value) if value else None
	if name == "name" and isinstance(value, str):
		return " ".join(value.split())
	if isinstance(value, (str, bool, int, float)) or value is None:
		return value
	return str(value)


def _record(element, origin: tuple) -> dict:
	record = {name: _value(element, name) for name in PROPERTIES}
	rect = record["rect"]
	if rect and (rect[2] or rect[3]):
		record["rect"] = [rect[0] - origin[0], rect[1] - origin[1], rect[2], rect[3]]
	else:
		record["rect"] = None
	# Leave out what says nothing, to keep the files readable.
	return {k: v for k, v in record.items() if v not in (None, "", False, [])}


def _children(element, request) -> list:
	try:
		cached = element.BuildUpdatedCache(request)
		children = cached.GetCachedChildren()
	except Exception:
		return []
	if not children:
		return []
	return [children.GetElement(i) for i in range(children.Length)]


def _intersect(rect: list, clip: list) -> Optional[list]:
	left, top = max(rect[0], clip[0]), max(rect[1], clip[1])
	right = min(rect[0] + rect[2], clip[0] + clip[2])
	bottom = min(rect[1] + rect[3], clip[1] + clip[3])
	if right <= left or bottom <= top:
		return None
	return [left, top, right - left, bottom - top]


#: `ChildWindowFromPointEx` flags: pass over invisible and transparent child windows.
_SKIP = 0x0001 | 0x0004


def _topChildAt(top: int, x: int, y: int) -> int:
	""":return: the deepest child window of `top` on top at a point on the screen, by the
	application's own stacking of its windows, whatever other applications are over it."""
	user32 = ctypes.windll.user32
	user32.ChildWindowFromPointEx.restype = wintypes.HWND
	user32.ChildWindowFromPointEx.argtypes = (wintypes.HWND, wintypes.POINT, wintypes.UINT)
	window = top
	for _ in range(50):
		point = wintypes.POINT(x, y)
		user32.ScreenToClient(window, ctypes.byref(point))
		child = user32.ChildWindowFromPointEx(window, point, _SKIP)
		if not child or child == window:
			break
		window = child
	return window


def _isCovered(top: int, hwnd: int, rect: list, origin: tuple) -> bool:
	""":return: whether, at its middle and near each corner, another of the application's windows
	is on top of this element's window: one that is neither it, inside it, nor around it.

	All five points, so a small window floating over part of a big pane does not hide the pane.
	"""
	try:
		user32 = ctypes.windll.user32
		hwnd = int(hwnd)
		if hwnd == top:
			return False
		left, upper = rect[0] + origin[0], rect[1] + origin[1]
		width, height = rect[2], rect[3]
		points = [(left + width // 2, upper + height // 2)]
		points += [
			(left + dx, upper + dy)
			for dx in (width // 5, width * 4 // 5)
			for dy in (height // 5, height * 4 // 5)
		]
		for x, y in points:
			hit = _topChildAt(top, x, y)
			if hit == hwnd or user32.IsChild(hwnd, hit) or user32.IsChild(hit, hwnd):
				return False
		return True
	except Exception:
		return False


def _focusPath(focus) -> list:
	""":return: the runtime ids of the focus and every element above it, focus first, so an
	overview can tell which area holds it."""
	handler = UIAHandler.handler
	element = getattr(focus, "UIAElement", None)
	try:
		if element is None:
			element = handler.clientObject.ElementFromHandle(focus.windowHandle)
		walker = handler.clientObject.ControlViewWalker
		path = []
		for _ in range(60):
			# Past the top, UI Automation gives a null pointer, which is false but not None.
			if not element:
				break
			try:
				path.append([int(v) for v in element.GetRuntimeId()])
			except Exception:
				path.append(None)
			try:
				element = walker.GetParentElement(element)
			except Exception:
				break
		return path
	except Exception:
		return []


def walk(obj=None, maxDepth: int = 12, maxNodes: int = 800) -> Optional[dict]:
	"""Read the window's control view tree, one call per element that can hold others.

	See the module's description for what is counted rather than walked, and what is clipped.

	:return: the window's record, its children nested under it, with the cost and whether a
		limit was reached; or None when there is no window.
	"""
	obj, handle = _window(obj)
	if handle is None:
		return None
	handler = UIAHandler.handler
	if handler is None:
		print("UI Automation is not running in NVDA.")
		return None
	request = _cacheRequest(UIAHandler.TreeScope_Element | UIAHandler.TreeScope_Children)
	started = time.perf_counter()
	root = handler.clientObject.ElementFromHandleBuildCache(handle, request)
	location = getattr(obj, "location", None)
	origin = tuple(location[:2]) if location else (0, 0)
	window = [0, 0, location[2], location[3]] if location else None
	count = 0
	calls = 1
	items = 0
	hidden = 0
	truncated = False

	def visit(element, depth: int, clip: Optional[list], children: Optional[list] = None) -> dict:
		nonlocal count, calls, items, hidden, truncated
		count += 1
		record = _record(element, origin)
		rect = record.get("rect")
		if rect and clip:
			seen = _intersect(rect, clip)
			if seen != rect:
				record["fullRect"] = rect
				if seen:
					record["rect"] = seen
				else:
					record.pop("rect")
					record["outside"] = True
		if (
			record.get("hwnd")
			and record.get("rect")
			and _isCovered(handle, record["hwnd"], record["rect"], origin)
		):
			record["covered"] = True
			hidden += 1
			return record
		if record.get("type") in LEAF_TYPES:
			return record
		if depth >= maxDepth:
			truncated = True
			return record
		if children is None:
			calls += 1
			children = _children(element, request)
		held = Counter()
		kids = []
		for child in children:
			kind = _value(child, "type")
			if kind in ITEM_TYPES:
				held[kind] += 1
				continue
			if count >= maxNodes:
				truncated = True
				break
			kids.append(visit(child, depth + 1, record.get("rect") or clip))
		if held:
			record["items"] = dict(held)
			items += sum(held.values())
		if kids:
			record["children"] = kids
		return record

	first = []
	try:
		cached = root.GetCachedChildren()
		first = [cached.GetElement(i) for i in range(cached.Length)] if cached else []
	except Exception:
		first = []
	tree = visit(root, 0, window, first)
	elapsed = time.perf_counter() - started
	return {
		"window": tree,
		"windowRect": list(location) if location else None,
		"app": _app(obj),
		"focus": _focus(origin),
		"nodes": count,
		"items": items,
		"coveredWindows": hidden,
		"calls": calls,
		"seconds": round(elapsed, 3),
		"truncated": truncated,
		"maxDepth": maxDepth,
		"maxNodes": maxNodes,
	}


def _app(obj) -> dict:
	try:
		module = obj.appModule
		return {
			"name": module.appName,
			"product": module.productName,
			"version": module.productVersion,
		}
	except Exception:
		return {}


def _focus(origin: tuple) -> Optional[dict]:
	"""Where the focus was when the console opened, and what NVDA calls it."""
	focus = _snapshot("focus") or api.getFocusObject()
	if focus is None:
		return None
	location = getattr(focus, "location", None)
	rect = [location[0] - origin[0], location[1] - origin[1], location[2], location[3]] if location else None
	element = getattr(focus, "UIAElement", None)
	runtimeId = None
	if element is not None:
		try:
			runtimeId = [int(v) for v in element.GetRuntimeId()]
		except Exception:
			runtimeId = None
	return {
		"role": getattr(getattr(focus, "role", None), "name", None),
		"name": getattr(focus, "name", None),
		"rect": rect,
		# Which of NVDA's object classes it is, such as NVDAObjects.UIA.UIA or
		# NVDAObjects.IAccessible.IAccessible, which says what the application speaks.
		"nvdaClass": f"{type(focus).__module__}.{type(focus).__name__}",
		"runtimeId": runtimeId,
		"path": _focusPath(focus),
	}


def _where(rect, window) -> str:
	"""A rough, readable position in the window."""
	if not rect or not window:
		return "no location"
	left, top, width, height = rect
	ww, wh = window[2] or 1, window[3] or 1
	if width >= ww * 0.9 and height >= wh * 0.9:
		return "filling it"
	across = "left" if left + width / 2 < ww / 3 else "right" if left + width / 2 > ww * 2 / 3 else "center"
	down = "top" if top + height / 2 < wh / 3 else "bottom" if top + height / 2 > wh * 2 / 3 else "middle"
	if width >= ww * 0.9:
		return f"across the {down}"
	if height >= wh * 0.9:
		return f"down the {across} side" if across != "center" else "down the center"
	return f"{down} {across}"


def _line(record: dict, window, path: frozenset = frozenset()) -> str:
	parts = [str(record.get("type", "?"))]
	if record.get("name"):
		name = record["name"].replace("\n", " ")
		parts.append(repr(name[:60] + ("..." if len(name) > 60 else "")))
	if record.get("automationId"):
		parts.append(f"#{record['automationId']}")
	if record.get("className"):
		parts.append(f"[{record['className']}]")
	if record.get("landmark"):
		parts.append(f"landmark={record['landmark']}")
	if record.get("framework"):
		parts.append(f"({record['framework']})")
	rect = record.get("rect")
	parts.append(f"{rect} {_where(rect, window)}" if rect else "no rect")
	if record.get("fullRect"):
		parts.append(f"clipped from {record['fullRect']}")
	if record.get("outside"):
		parts.append("outside what holds it")
	if record.get("offscreen"):
		parts.append("offscreen")
	if record.get("covered"):
		parts.append("COVERED by another window")
	if record.get("hasFocus"):
		parts.append("FOCUS")
	if record.get("runtimeId") and tuple(record["runtimeId"]) in path:
		parts.append("holds the focus")
	if record.get("items"):
		parts.append("holds " + ", ".join(f"{n} {kind}" for kind, n in record["items"].items()))
	return " ".join(parts)


def _outline(record: dict, window, depth: int = 0, path: frozenset = frozenset()) -> list:
	lines = ["  " * depth + _line(record, window, path)]
	for child in record.get("children", []):
		lines.extend(_outline(child, window, depth + 1, path))
	return lines


def _pathOf(data: dict) -> frozenset:
	""":return: the runtime ids of the elements holding the focus, for marking them."""
	focus = data.get("focus") or {}
	return frozenset(tuple(r) for r in focus.get("path") or [] if r)


def _walkRecords(record: dict):
	yield record
	for child in record.get("children", []):
		yield from _walkRecords(child)


def _summary(data: dict) -> str:
	records = list(_walkRecords(data["window"]))
	types = Counter(r.get("type") for r in records)
	frameworks = Counter(r.get("framework") for r in records if r.get("framework"))
	framing = [r for r in records if r.get("type") in FRAMING_TYPES]
	named = [r for r in framing if r.get("name") or r.get("automationId")]
	landmarks = [r for r in records if r.get("landmark")]
	offscreen = sum(1 for r in records if r.get("offscreen"))
	app = data.get("app") or {}
	lines = [
		f"Application: {app.get('name')} ({app.get('product')} {app.get('version')})",
		f"Window: {data.get('windowRect')}",
		f"Elements read: {data['nodes']} in {data['seconds']} s, {data['calls']} calls, "
		f"{data.get('items', 0)} items counted, {data.get('coveredWindows', 0)} covered windows"
		+ (
			f"; stopped at a limit (depth {data['maxDepth']}, {data['maxNodes']} elements)"
			if data["truncated"]
			else ""
		),
		f"Frameworks: {dict(frameworks)}",
		f"Control types: {dict(types.most_common())}",
		f"Framing elements: {len(framing)}, of which named or with an automation id: {len(named)}",
		f"Landmarks: {len(landmarks)}"
		+ (f" ({', '.join(str(r['landmark']) for r in landmarks)})" if landmarks else ""),
		f"Offscreen: {offscreen}",
		f"Focus: { {k: v for k, v in (data.get('focus') or {}).items() if k != 'path'} }",
	]
	if data.get("subtree"):
		subtree = data["subtree"]
		lines.append(
			f"Whole window in one call: {subtree.get('elements')} elements, {subtree.get('seconds')} s"
			+ (f" ({subtree['error']})" if subtree.get("error") else "")
		)
	return "\n".join(lines)


def dump(obj=None, maxDepth: int = 12, maxNodes: int = 800) -> None:
	"""Print a summary of the window's tree and its outline."""
	data = walk(obj, maxDepth, maxNodes)
	if data is None:
		return
	print(_summary(data))
	print()
	print("\n".join(_outline(data["window"], data["windowRect"], path=_pathOf(data))))


def _subtreeCost(handle) -> dict:
	"""Time asking UI Automation for the window's whole subtree in one cross process call.

	The cheapest way to read a tree that is not huge, and the one with no limit, so it is only
	timed and counted, and nothing it reads is kept.
	"""
	request = _cacheRequest(UIAHandler.TreeScope_Subtree)
	started = time.perf_counter()
	try:
		root = UIAHandler.handler.clientObject.ElementFromHandleBuildCache(handle, request)
	except Exception as error:
		return {"error": str(error)}
	elapsed = time.perf_counter() - started
	stack = [root]
	count = 0
	while stack:
		element = stack.pop()
		count += 1
		try:
			children = element.GetCachedChildren()
		except Exception:
			children = None
		if children:
			stack.extend(children.GetElement(i) for i in range(children.Length))
	return {"elements": count, "seconds": round(elapsed, 3)}


def cost(obj=None) -> None:
	"""Time reading the window two ways: the walk, one call per element that can hold others,
	with no limits; and the whole subtree in one call."""
	obj, handle = _window(obj)
	if handle is None:
		return
	data = walk(obj, maxDepth=50, maxNodes=100000)
	if data is None:
		return
	print(
		f"The walk: {data['nodes']} elements and {data['items']} items counted, "
		f"{data['calls']} calls, {data['seconds']} s"
	)
	subtree = _subtreeCost(handle)
	if subtree.get("error"):
		print(f"One call for the subtree failed: {subtree['error']}")
	else:
		print(f"One call for the subtree: {subtree['elements']} elements, {subtree['seconds']} s")


def save(
	obj=None,
	folder: Optional[str] = None,
	maxDepth: int = 12,
	maxNodes: int = 800,
	cost: bool = False,
) -> None:
	"""Read the window and write a readable report and a JSON file, named after the application.

	The JSON is meant for working out the layout rules offline, in unit tests, without the
	application.

	:param cost: also time reading the whole window in one call, and save that.
	"""
	data = walk(obj, maxDepth, maxNodes)
	if data is None:
		return
	if cost:
		data["subtree"] = _subtreeCost(_window(obj)[1])
	folder = folder or os.path.join(os.path.dirname(os.path.abspath(__file__)), "layoutProbeOut")
	os.makedirs(folder, exist_ok=True)
	name = (data.get("app") or {}).get("name") or "app"
	stem = os.path.join(folder, f"{re.sub(r'[^A-Za-z0-9.-]', '_', name)}-{time.strftime('%Y%m%d-%H%M%S')}")
	report = (
		_summary(data) + "\n\n" + "\n".join(_outline(data["window"], data["windowRect"], path=_pathOf(data)))
	)
	payload: dict[str, Any] = {"kind": "uia", **data}
	with open(stem + ".txt", "w", encoding="utf-8") as f:
		f.write(report)
	with open(stem + ".json", "w", encoding="utf-8") as f:
		json.dump(payload, f, indent=1)
	print(f"Saved {stem}.txt and {stem}.json")
