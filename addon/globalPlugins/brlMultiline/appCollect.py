# BrlMultiline: gathering what UI Automation reports about an application window's layout.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""What UI Automation reports about where things are in a window, gathered for `appRegions`.

The rules in `appRegions` are pure and are tested against windows saved by
`spikes/appLayoutProbe.py`. This module gathers the same thing live, in the same shape: the
window's control view, an element at a time, each with its control type, name, automation id,
class, rectangle relative to the window, landmark and runtime id, and the chain of elements
holding the focus. What the probe learned is kept:

- Items (tree, list, data, tab and menu items) are counted on what holds them, not walked: a
  navigation tree of six hundred folders would otherwise be the whole budget.
- Elements that hold nothing an overview needs, such as text and buttons, are not asked for
  their children, which saves a call into the application each.
- An element in a window of its own that another of the application's windows covers, as an
  inactive Explorer tab is, is marked covered and not walked. See `_isCovered`.
- Each rectangle is clipped to the one around it.

**Every element with children is a call into the application**, a millisecond in a modern one
and 65 in an old one through the MSAA bridge (askSam, in the probe's captures). So the work is
cut into batches with a return to NVDA's message loop between them, under a time limit, as the
web page collector's is; and what a limit cut short is said, so part of a window does not pass
for the whole.
"""

from __future__ import annotations

import ctypes
import time
from collections import Counter
from collections.abc import Callable, Iterator
from ctypes import wintypes
from typing import Any

import wx
from logHandler import log

#: The UI Automation properties read for every element, by the name they have in the record, as
#: the name of their constant in `UIAHandler` after "UIA_" and before "PropertyId".
PROPERTIES = {
	"type": "ControlType",
	"name": "Name",
	"automationId": "AutomationId",
	"className": "ClassName",
	"framework": "FrameworkId",
	"rect": "BoundingRectangle",
	"offscreen": "IsOffscreen",
	"hasFocus": "HasKeyboardFocus",
	"landmark": "LandmarkType",
	"runtimeId": "RuntimeId",
	"hwnd": "NativeWindowHandle",
}

#: Control types that are items: counted on what holds them, not walked into.
ITEM_TYPES = frozenset(("TreeItem", "ListItem", "DataItem", "TabItem", "MenuItem", "HeaderItem"))

#: Control types whose children are never asked for.
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

#: How deep, and how many elements, the walk goes. Every capture so far fitted in 140.
MAX_DEPTH = 12
MAX_NODES = 800
#: Calls into the application between returns to NVDA's message loop. Small, since one call can
#: take 65 milliseconds in an old application.
BATCH = 4
#: Past this, gathering stops where it is and the rules work with what there is.
TIME_LIMIT = 8.0


class AppCollector:
	"""Gathers one window's layout in batches, then hands it to `done`.

	`done` is called once, with the gathered data in the shape `appRegions.findAppRegions` reads
	from, or with None and a reason. `cancel` stops it, and `done` is then never called.

	:param window: the top level window, an NVDA object with a window handle.
	:param focus: the focus, an NVDA object, for placing it among the areas.
	"""

	def __init__(self, window, focus, done: Callable[[dict[str, Any] | None, str], None]):
		self.window = window
		self.focus = focus
		self.done = done
		self.cancelled = False
		self.started = 0.0
		self.calls = 0
		self.nodes = 0
		self.incomplete: list[str] = []
		self._steps: Iterator[None] | None = None

	def start(self) -> None:
		self.started = time.perf_counter()
		self._steps = self._gather()
		wx.CallAfter(self._step)

	def cancel(self) -> None:
		self.cancelled = True

	@property
	def overTime(self) -> bool:
		return time.perf_counter() - self.started > TIME_LIMIT

	def _step(self) -> None:
		if self.cancelled or self._steps is None:
			return
		try:
			next(self._steps)
		except StopIteration:
			return
		except Exception:
			log.error("BrlMultiline: gathering the window layout failed", exc_info=True)
			self.done(None, "error")
			return
		wx.CallAfter(self._step)

	def _gather(self) -> Iterator[None]:
		import UIAHandler

		handler = UIAHandler.handler
		handle = getattr(self.window, "windowHandle", None)
		location = getattr(self.window, "location", None)
		if handler is None or not handle or not location:
			self.done(None, "no window")
			return
		self.uia = UIAHandler
		self.handle = int(handle)
		self.origin = (location[0], location[1])
		self.request = _cacheRequest(UIAHandler, UIAHandler.TreeScope_Element | UIAHandler.TreeScope_Children)
		root = handler.clientObject.ElementFromHandleBuildCache(self.handle, self.request)
		self.calls = 1
		yield
		bounds = [0, 0, location[2], location[3]]
		window = yield from self._visit(root, 0, bounds, _cachedChildren(root))
		yield
		path = _focusPath(UIAHandler, self.focus)
		payload = {
			"kind": "uia",
			"window": window,
			"windowRect": list(location),
			"focus": {"path": path},
			"incomplete": self.incomplete,
			# Which window the layout is of, for telling later whether it is still that window.
			"identity": [self.handle],
		}
		log.debug(
			f"BrlMultiline: window layout gathered in {time.perf_counter() - self.started:.2f}s, "
			f"{self.nodes} elements, {self.calls} calls",
		)
		self.done(payload, "")

	def _visit(self, element, depth: int, clip: list, children: list | None = None) -> Iterator[None]:
		"""Record an element and, as far as the limits allow, what is inside it.

		A generator, so the walk can stop for NVDA between batches of calls; the record is what it
		returns, for `yield from`.
		"""
		self.nodes += 1
		record = _record(self.uia, element, self.origin)
		rect = record.get("rect")
		if rect:
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
			and _isCovered(self.handle, record["hwnd"], record["rect"], self.origin)
		):
			record["covered"] = True
			return record
		if record.get("type") in LEAF_TYPES:
			return record
		if depth >= MAX_DEPTH:
			_note(self.incomplete, "depth")
			return record
		if children is None:
			if self.overTime:
				_note(self.incomplete, "time")
				return record
			children = _children(element, self.request)
			self.calls += 1
			if self.calls % BATCH == 0:
				yield
		held = Counter()
		kids = []
		for child in children:
			kind = _value(self.uia, child, "type")
			if kind in ITEM_TYPES:
				held[kind] += 1
				continue
			if self.nodes >= MAX_NODES:
				_note(self.incomplete, "size")
				break
			kids.append((yield from self._visit(child, depth + 1, record.get("rect") or clip)))
		if held:
			record["items"] = dict(held)
		if kids:
			record["children"] = kids
		return record


def _note(incomplete: list, why: str) -> None:
	if why not in incomplete:
		incomplete.append(why)


def _names(uia, suffix: str) -> dict:
	""":return: UI Automation's constants ending in `suffix`, by value, without the prefix and
	suffix: 50000 to "Button" for control types."""
	found = {}
	for attribute in dir(uia):
		if attribute.startswith("UIA_") and attribute.endswith(suffix):
			value = getattr(uia, attribute)
			if isinstance(value, int):
				found[value] = attribute[len("UIA_") : -len(suffix)]
	return found


_TYPE_NAMES: dict = {}
_LANDMARK_NAMES: dict = {}


def _propertyId(uia, name: str):
	return getattr(uia, f"UIA_{PROPERTIES[name]}PropertyId", None)


def _cacheRequest(uia, scope: int):
	client = uia.handler.clientObject
	request = client.CreateCacheRequest()
	for name in PROPERTIES:
		propertyId = _propertyId(uia, name)
		if propertyId is None:
			continue
		try:
			request.addProperty(propertyId)
		except Exception:
			# An older Windows without landmarks, say: read what there is.
			pass
	request.TreeScope = scope
	request.TreeFilter = client.ControlViewCondition
	return request


def _value(uia, element, name: str):
	propertyId = _propertyId(uia, name)
	if propertyId is None:
		return None
	try:
		value = element.GetCachedPropertyValue(propertyId)
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
		if not _TYPE_NAMES:
			_TYPE_NAMES.update(_names(uia, "ControlTypeId"))
		return _TYPE_NAMES.get(value, value)
	if name == "landmark":
		if not _LANDMARK_NAMES:
			_LANDMARK_NAMES.update(_names(uia, "LandmarkTypeId"))
		return _LANDMARK_NAMES.get(value) if value else None
	if name == "name" and isinstance(value, str):
		return " ".join(value.split())
	if isinstance(value, (str, bool, int, float)) or value is None:
		return value
	return str(value)


def _record(uia, element, origin: tuple) -> dict:
	record = {name: _value(uia, element, name) for name in PROPERTIES}
	rect = record["rect"]
	if rect and (rect[2] or rect[3]):
		record["rect"] = [rect[0] - origin[0], rect[1] - origin[1], rect[2], rect[3]]
	else:
		record["rect"] = None
	return {k: v for k, v in record.items() if v not in (None, "", False, [])}


def _cachedChildren(element) -> list:
	try:
		children = element.GetCachedChildren()
	except Exception:
		return []
	if not children:
		return []
	return [children.GetElement(i) for i in range(children.Length)]


def _children(element, request) -> list:
	try:
		return _cachedChildren(element.BuildUpdatedCache(request))
	except Exception:
		return []


def _intersect(rect: list, clip: list) -> list | None:
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


def _isCovered(top: int, hwnd, rect: list, origin: tuple) -> bool:
	""":return: whether another of the application's windows is on top of this element's window
	at its middle and near each corner: one that is neither it, inside it, nor around it.

	Asking Windows whether the window is visible does not do (found in the probe): Explorer's
	inactive tabs are visible windows, and the input site of a XAML island, such as Explorer's
	address bar, is a hidden window by design, under a visible one. All five points, so a small
	window floating over part of a big pane does not hide the pane.
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


def _focusPath(uia, focus) -> list:
	""":return: the runtime ids of the focus and every element above it, focus first."""
	if focus is None:
		return []
	client = uia.handler.clientObject
	element = getattr(focus, "UIAElement", None)
	try:
		if element is None:
			element = client.ElementFromHandle(focus.windowHandle)
		walker = client.ControlViewWalker
	except Exception:
		return []
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


# Moving to an area


def findElement(hwnd: int, runtimeId) -> Any:
	""":return: the element with this runtime id in a window, wherever it now is, or None when it is
	gone. One search inside the application, so it costs no more than one call however deep the
	element is.

	By runtime id rather than by where it was, for the reason a page's area is found by its
	element's identifier: the window may have changed since its layout was read.
	"""
	import array

	import UIAHandler

	handler = UIAHandler.handler
	if handler is None or not hwnd or not runtimeId:
		return None
	client = handler.clientObject
	try:
		root = client.ElementFromHandle(int(hwnd))
		condition = client.CreatePropertyCondition(
			UIAHandler.UIA_RuntimeIdPropertyId, array.array("l", [int(v) for v in runtimeId])
		)
		found = root.FindFirst(UIAHandler.TreeScope_Subtree, condition)
	except Exception:
		log.debugWarning("BrlMultiline: could not look for an area's element", exc_info=True)
		return None
	# A search that finds nothing gives a null pointer, which is false but not None.
	return found if found else None


def focusableWithin(element) -> Any:
	""":return: the area's own element if it can take the focus, or else the first thing inside it
	that can, or None when nothing in it can: a toolbar's first button, a pane's first field, a list
	or a tree itself. Enabled and on screen, since the focus cannot go to anything else."""
	import UIAHandler

	client = UIAHandler.handler.clientObject
	try:
		condition = client.CreateAndConditionFromArray(
			[
				client.CreatePropertyCondition(UIAHandler.UIA_IsKeyboardFocusablePropertyId, True),
				client.CreatePropertyCondition(UIAHandler.UIA_IsEnabledPropertyId, True),
				client.CreatePropertyCondition(UIAHandler.UIA_IsOffscreenPropertyId, False),
			],
		)
		found = element.FindFirst(UIAHandler.TreeScope_Subtree, condition)
	except Exception:
		log.debugWarning("BrlMultiline: could not look for something to focus in an area", exc_info=True)
		return None
	return found if found else None


def elementOfWindow(hwnd) -> Any:
	""":return: the element of a window of its own, by its handle, or None when the window is gone.

	For an area whose element has no runtime id to find it by, or whose id has changed: a Win32
	control, an old application's toolbar or pane, is a window, and its handle stays the same.
	"""
	import UIAHandler

	handler = UIAHandler.handler
	if handler is None or not hwnd:
		return None
	try:
		if not ctypes.windll.user32.IsWindow(int(hwnd)):
			return None
		found = handler.clientObject.ElementFromHandle(int(hwnd))
	except Exception:
		log.debugWarning("BrlMultiline: could not find an area by its window", exc_info=True)
		return None
	return found if found else None
