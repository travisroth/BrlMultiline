# BrlMultiline: a routing key in a monitored segment goes where it points, as a click would.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Routing in a segment that shows an object, a pin or an app layout's, goes to what is pressed.

A press acts as a mouse click on what is under the key would, in whichever window or
application that is:

1. **What is under the key.** The region and position, read the way NVDA reads a press, or
   from the flow when the segment shows one. Text gives a position in the text; anything else
   gives its object.
2. **Its window comes forward**, restored if it was minimised.
3. **A click, where the click would land on it.** The point is the start of the text pressed, or
   the middle of the object; text that cannot say where it starts is not clicked at all. Windows
   is asked which window is at the point, and accessibility which object, and the click goes
   ahead only if both are the target's (see `_Desktop.isTheTargetsAt`). A click puts an
   application's own caret where pressed, which Outlook's reading pane will not otherwise do.
4. **Otherwise the object is focused** and its caret, or the browse mode cursor, is put at the
   position pressed, once the focus has settled so the application cannot move it back.

The window takes a moment to come forward, so the click waits L{FOREGROUND_SETTLE_MS} for it.
Nothing is said when it works, since the focus moving is the signal; something is said when a
press reaches nothing that can be gone to.
"""

import dataclasses
from typing import Any

import addonHandler
import ui
import wx
from logHandler import log

addonHandler.initTranslation()

FOREGROUND_SETTLE_MS = 150
"""How long a window brought forward is given before Windows is asked what is at the point."""

HIT_TEST_DEPTH = 8
"""How many parents up from what is at the point the target is looked for. A click on an item
lands on whatever is innermost there, which is rarely deeper than this below the item."""

SW_RESTORE = 9
"""`ShowWindow`'s command to restore a minimised window, which `winUser` does not define."""

CARET_SETTLE_MS = 250
"""How long after focusing the object the caret is placed, so the application's own placing of
it on gaining the focus comes first and is then corrected."""


@dataclasses.dataclass
class Target:
	"""What a press reached."""

	obj: Any
	"""The object to focus: the one pressed, or the document the text pressed is in."""

	info: Any = None
	"""A collapsed position in the text pressed, or None where an object was pressed."""

	treeInterceptor: Any = None
	"""The browse mode document the text is in, whose cursor is placed rather than the caret."""


def regionAt(segment, position: int) -> tuple[Any, int] | None:
	"""The region and the position in it under a routing key, read as NVDA reads a press.

	:param segment: the segment pressed.
	:param position: the key's position within the segment.
	:return: the region and position, or None where the press reaches nothing.
	"""
	controller = getattr(segment, "controller", None)
	if controller is not None:
		return controller.regionAt(position)
	try:
		pos = segment.windowPosToBufferPos(position)
		if pos >= segment.windowEndPos:
			return None
		return segment.bufferPosToRegionPos(pos)
	except LookupError:
		return None


def targetFor(region, at: int) -> Target | None:
	""":return: what pressing a position in a region reaches, or None if nothing."""
	shown = getattr(region, "obj", None)
	if shown is None:
		return None
	info = None
	getInfo = getattr(region, "getTextInfoForBraillePos", None)
	if getInfo is not None:
		try:
			info = getInfo(at)
		except Exception:
			log.debugWarning("BrlMultiline: no text position under a routing key", exc_info=True)
	root = getattr(shown, "rootNVDAObject", None)
	if root is not None:
		# A browse mode document. Its cursor is where the reader reads it from.
		return Target(obj=root, info=info, treeInterceptor=shown)
	return Target(obj=shown, info=info)


def pointFor(target: Target) -> tuple[int, int] | None:
	"""The screen point a click on the target would be made at, or None where there is none.

	Text only where it says it starts; otherwise it is gone to by its caret. An object in its
	middle.
	"""
	if target.info is not None:
		try:
			point = target.info.pointAtStart
			return point.x, point.y
		except Exception:
			return None
	try:
		return _middleOf(target.obj)
	except Exception:
		return None


def expectedAt(target: Target):
	""":return: the object a click at the target's point should land on, for the hit test.

	The object the text pressed starts in, which may be deep inside the document, or the object
	pressed.
	"""
	if target.info is not None:
		try:
			inner = target.info.NVDAObjectAtStart
		except Exception:
			inner = None
		if inner is not None:
			return inner
	return target.obj


def _middleOf(obj) -> tuple[int, int] | None:
	location = obj.location
	if not location or location.width <= 0 or location.height <= 0:
		return None
	return location.left + location.width // 2, location.top + location.height // 2


class _Desktop:
	"""The calls into Windows, kept together so a test can stand in for them."""

	def topWindow(self, obj) -> int:
		import winUser

		return winUser.getAncestor(obj.windowHandle, winUser.GA_ROOT)

	def bringForward(self, window: int) -> None:
		import ctypes

		import winUser

		if winUser.getForegroundWindow() == window:
			return
		user32 = ctypes.windll.user32
		if user32.IsIconic(window):
			user32.ShowWindow(window, SW_RESTORE)
		winUser.setForegroundWindow(window)

	def isTheTargetsAt(self, obj, point: tuple[int, int]) -> bool:
		"""Whether a click at the point would land on the object, or on something inside it.

		The window first, for a covered or minimised one; then accessibility, since in a browser
		every control shares one window.
		"""
		import ctypes
		from ctypes import wintypes

		import api
		import winUser

		hit = ctypes.windll.user32.WindowFromPoint(wintypes.POINT(*point))
		window = obj.windowHandle
		if not hit or not window or not winUser.isDescendantWindow(window, hit):
			return False
		try:
			found = api.getDesktopObject().objectFromPoint(*point)
		except Exception:
			return False
		return isWithin(found, obj)


def isWithin(found, obj) -> bool:
	"""Whether an object is the one expected, or inside it within L{HIT_TEST_DEPTH} parents.

	Inside counts, as a click on a message lands on its text; around does not, since a container
	holds neighbours too. Objects that cannot be compared count as different.
	"""
	for _level in range(HIT_TEST_DEPTH):
		if found is None:
			return False
		try:
			if found is obj or found == obj:
				return True
			found = found.parent
		except Exception:
			return False
	return False

	def click(self, point: tuple[int, int]) -> None:
		import mouseHandler
		import winUser

		winUser.setCursorPos(*point)
		mouseHandler.doPrimaryClick()


desktop = _Desktop()


def goTo(target: Target) -> None:
	"""Go to what a press reached: a click where that is safe, the focus and caret where not."""
	try:
		desktop.bringForward(desktop.topWindow(target.obj))
	except Exception:
		log.debugWarning("BrlMultiline: could not bring a window forward for routing", exc_info=True)
	wx.CallLater(FOREGROUND_SETTLE_MS, _clickOrFocus, target)


def _clickOrFocus(target: Target) -> None:
	point = pointFor(target)
	try:
		if point is not None and desktop.isTheTargetsAt(expectedAt(target), point):
			desktop.click(point)
			return
	except Exception:
		log.debugWarning("BrlMultiline: could not click where a routing key pointed", exc_info=True)
	_focusInstead(target)


def _focusInstead(target: Target) -> None:
	"""Focus the object, and put its caret or browse mode cursor where the reader pressed."""
	try:
		target.obj.setFocus()
	except Exception:
		log.debugWarning("BrlMultiline: could not focus what a routing key pointed at", exc_info=True)
		# Translators: Said when a routing key is pressed on something in a monitored segment
		# that can be neither clicked nor focused.
		ui.message(_("Cannot go there"))
		return
	if target.info is not None:
		wx.CallLater(CARET_SETTLE_MS, _placeCaret, target)


def _placeCaret(target: Target) -> None:
	try:
		if target.treeInterceptor is not None:
			target.treeInterceptor.selection = target.info
		else:
			target.info.updateCaret()
	except Exception:
		log.debugWarning("BrlMultiline: could not put the caret where a routing key pointed", exc_info=True)


def route(segment, position: int) -> bool:
	"""Act on a routing key press in a segment showing an object.

	:param segment: the segment pressed.
	:param position: the key's position within it.
	:return: True, always: a press in a monitored segment is this module's, and padding or a
		blank row reaches nothing and does nothing, as a click on empty space would.
	"""
	found = regionAt(segment, position)
	if found is None:
		return True
	target = targetFor(*found)
	if target is None:
		return True
	goTo(target)
	return True
