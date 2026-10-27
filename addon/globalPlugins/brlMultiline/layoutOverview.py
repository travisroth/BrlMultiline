# BrlMultiline: a picture of a web page's or a window's main areas, on the pins.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The layout overview: a page's or a window's main areas drawn as labelled boxes, and moving in.

The plugin's overview scripts call into this. Outside a web page in browse mode it draws the
window the focus is in instead, from UI Automation. `layoutRegions` and `appRegions` find the
areas, `layoutCollect` and `appCollect` gather what they are found from, and `layoutDraw` draws
them; this gathers, draws, and keeps the overview up so it can be drilled into and backed out of.
See docs/design/layout-overview-plan.md.
"""

from __future__ import annotations

from typing import Optional, Protocol, TYPE_CHECKING

import addonHandler
import api
import ui
from logHandler import log

from . import (
	appCollect,
	appRegions,
	glyphFlow,
	glyphs,
	graphicsMode,
	layoutCollect,
	layoutDraw,
	layoutRegions,
)
from .graphics import findSurface

addonHandler.initTranslation()

if TYPE_CHECKING:
	from .graphicsMode import GraphicsMode


class LayoutView:
	"""An overview of a page's layout that is up: what it was drawn from, and where the reader is.

	Kept so that drilling into an area and backing out draw from the same reading of the page,
	rather than reading it again, and so that the area last pressed is what a drill goes into.
	"""

	def __init__(self, page, view: str, viewport, document, identity=None, app: bool = False):
		self.page = page
		self.view = view
		self.viewport = viewport
		self.document = document
		#: Whether this is an application window's layout rather than a web page's: there is no
		#: page to scroll, and moving to an area moves the focus. See `moveToAppArea`.
		self.app = app
		#: Which page and which reading of it the layout is of; see `pageIdentity`.
		self.identity = tuple(identity) if identity else pageIdentity(document)
		#: The area drilled into, or None for the whole page or screen.
		self.focus = None
		#: The areas drilled through to get here, outermost first; None stands for the whole.
		self.trail: list = []
		#: The area the last routing press landed in, which a drill goes into.
		self.pressed = None
		#: The drawing on the display, so a drill can tell it is still this overview that is up.
		self.drawing = None
		#: Key letters given out, by region, kept for every drawing of this reading of the page,
		#: so an area keeps its letter when drilled into, backed out of, zoomed or panned.
		self.keys: dict = {}


#: The role NVDA names each count of a page's layout by, in its braille labels, and the glyph
#: drawn for it where there is one.
LAYOUT_COUNT_ROLES = {
	"links": ("LINK", glyphs.LINK),
	"buttons": ("BUTTON", glyphs.BUTTON),
	"fields": ("EDITABLETEXT", glyphs.EDIT),
	"tabs": ("TAB", None),
	"sections": ("SECTION", None),
}


def countStyle() -> "layoutDraw.CountStyle":
	""":return: how a page layout's counts are drawn for this reader and this display.

	NVDA's own braille abbreviation for each role, in the reader's language, or the role's
	glyph where glyphs are on and the display can show them.
	"""
	words = dict(layoutDraw.COUNT_WORDS)
	written = {}
	try:
		from braille.labels import roleLabels

		written = {getattr(member, "name", ""): label for member, label in roleLabels.items()}
	except Exception:
		log.debugWarning("BrlMultiline: NVDA's braille labels could not be read", exc_info=True)
	for count, (role, _glyph) in LAYOUT_COUNT_ROLES.items():
		label = written.get(role)
		if not label:
			# As NVDA's braille does for a role with no abbreviation: the role's own name.
			try:
				import controlTypes

				label = getattr(getattr(controlTypes.Role, role, None), "displayString", None)
			except Exception:
				label = None
		if label:
			words[count] = label
	shapes = {}
	surface = findSurface()
	if surface is not None and glyphFlow.enabled() and glyphs.supported(surface.driver):
		for count, (_role, glyph) in LAYOUT_COUNT_ROLES.items():
			if glyph is not None:
				shapes[count] = glyphs.patternRows(glyph.dots)
	return layoutDraw.CountStyle(words, shapes)


def pageIdentity(document) -> tuple:
	""":return: which page a browse mode document shows, and which reading of it: its address and
	its buffer. Browse mode can read a page again, or a page can become another, under the same
	document object, so the object alone does not say the text is the text a layout was made of.
	"""
	return (getattr(document, "documentConstantIdentifier", None), getattr(document, "VBufHandle", None))


LAYOUT_DEPTH = 3
"""Levels of areas a drawing of a page's layout shows: the main areas, the areas inside them,
and those inside those. Forms, search, tab strips and the like are drawn deeper still; see
`layoutDraw.MAJOR_KINDS`."""

LAYOUT_SMALLEST_VIEW = 200
"""Pixels of a web page, top to bottom, that the most magnified view of its layout shows."""


class LayoutHost(Protocol):
	"""What `LayoutOverview` uses of the plugin. See docs/design/architecture.md, Module map."""

	graphicsMode: GraphicsMode

	@property
	def isTerminated(self) -> bool: ...

	def cancelPendingDrawings(self) -> None: ...


class LayoutOverview:
	"""The layout overview of the page or window the focus is in, for the plugin's scripts."""

	def __init__(self, plugin: LayoutHost):
		"""
		:param plugin: the global plugin, for its graphics mode and to cancel other drawings.
		"""
		self.plugin = plugin
		self._collector = None
		"""The layout being gathered, if one is; see `draw`."""
		self.current: Optional[LayoutView] = None
		"""The overview last drawn, for drilling into it; see `LayoutView`."""

	def cancel(self) -> None:
		"""Stop a layout still being gathered, so it is never drawn."""
		if self._collector is not None:
			self._collector.cancel()
			self._collector = None

	def draw(self, view: str) -> None:
		"""Gather the focused page's layout a batch at a time, then draw or show it.

		Gathering takes a second or more on a busy page, so it is done in batches with NVDA
		free to speak between them, and only the last request counts: another overview, a
		chart, a picture or the drawing turned off in the meantime cancels it.
		"""
		self.plugin.cancelPendingDrawings()
		document = getattr(api.getFocusObject(), "treeInterceptor", None)
		if (
			document is None
			or not hasattr(document, "_iterNodesByType")
			or not hasattr(document, "VBufHandle")
		):
			self._drawWindow(view)
			return
		if not getattr(document, "isReady", True):
			# Translators: reported when the layout overview is asked for while a page loads.
			ui.message(_("The page is still loading"))
			return
		if view != "text" and self.plugin.graphicsMode.drawingSize() is None:
			view = "text"
		collector = None

		def gathered(payload, why):
			if self.plugin.isTerminated or self._collector is not collector:
				return
			self._collector = None
			if payload is None:
				log.debug(f"BrlMultiline: no page layout: {why}")
				# Translators: reported when the layout of a web page could not be worked out.
				ui.message(_("Could not read the layout of this page"))
				return
			self.show(payload, view, document)

		collector = layoutCollect.Collector(document, gathered)
		self._collector = collector
		# Translators: reported while the add-on reads where things are on a web page.
		ui.message(_("Reading the page layout"))
		collector.start()

	def _drawWindow(self, view: str) -> None:
		"""Gather the layout of the window the focus is in, a batch at a time, then draw or show it.

		The same overview as a web page's, from UI Automation rather than browse mode: see
		`appCollect` and `appRegions`. A window has no page to scroll, so its whole is its screen.
		"""
		window = api.getForegroundObject()
		if window is None or not getattr(window, "windowHandle", None):
			# Translators: reported when the layout overview is asked for with no window to read.
			ui.message(_("No window to read the layout of"))
			return
		if view != "text" and self.plugin.graphicsMode.drawingSize() is None:
			view = "text"
		collector = None

		def gathered(payload, why):
			if self.plugin.isTerminated or self._collector is not collector:
				return
			self._collector = None
			if payload is None:
				log.debug(f"BrlMultiline: no window layout: {why}")
				# Translators: reported when the layout of an application window could not be read.
				ui.message(_("Could not read the layout of this window"))
				return
			self.show(payload, view)

		collector = appCollect.AppCollector(window, api.getFocusObject(), gathered)
		self._collector = collector
		# Translators: reported while the add-on reads where things are in an application window.
		ui.message(_("Reading the window layout"))
		collector.start()

	def show(self, payload: dict, view: str, document=None) -> None:
		"""Work out the areas from what was gathered, a page's or a window's, and draw or show them."""
		app = payload.get("kind") == "uia"
		try:
			if app:
				page = appRegions.findAppRegions(
					payload["window"],
					payload.get("windowRect"),
					(payload.get("focus") or {}).get("path"),
				)
				viewport = page.box
				# A window does not scroll: its whole is what is on screen.
				view = "screen" if view == "page" else view
			else:
				root, viewport = layoutRegions.fromProbe(payload)
				page = layoutRegions.findRegions(root, viewport)
		except Exception:
			log.error("BrlMultiline: could not work out the layout", exc_info=True)
			if app:
				ui.message(_("Could not read the layout of this window"))
			else:
				ui.message(_("Could not read the layout of this page"))
			return
		# Any limit reached other than sampling, which is normal on a busy page, means part of the
		# page was not read, and an overview of part of a page must not pass for the whole.
		partial = any(why != "sampled" for why in payload.get("incomplete", ()))
		if app:
			# Translators: said after the layout of an application window when only part of it
			# could be read in the time allowed, such as a slow older application.
			partialNote = _("Only part of the window could be read")
		else:
			# Translators: said after the layout of a web page when only part of it could be read
			# in the time allowed, such as a very long or slow page.
			partialNote = _("Only part of the page could be read")
		if view == "text":
			lines = layoutRegions.describe(page, 3)
			if not lines:
				if app:
					# Translators: reported when no areas were found in an application window.
					ui.message(_("No areas found in this window"))
				else:
					# Translators: reported when no areas were found on a web page.
					ui.message(_("No areas found on this page"))
				return
			if partial:
				lines.append(partialNote)
			if app:
				# Translators: the title of the window showing an application window's layout as
				# text.
				title = _("Window layout")
			else:
				# Translators: the title of the window showing a web page's layout as text.
				title = _("Page layout")
			ui.browseableMessage("\n".join(lines), title=title)
			return
		mode = self.plugin.graphicsMode
		size = mode.drawingSize()
		if size is None:
			ui.message(_("This display cannot show graphics"))
			return
		state = LayoutView(page, view, viewport, document, payload.get("identity"), app=app)
		message = self.enter(state)
		if message is None:
			return
		self.current = state
		if partial:
			message = f"{message}. {partialNote}"
		ui.message(message)

	def enter(self, state: "LayoutView") -> Optional[str]:
		"""Draw the overview for where the reader is in it and put it up.

		:return: what to say, or None when it could not be drawn, the reason having been said.
		"""
		mode = self.plugin.graphicsMode
		size = mode.drawingSize()
		if size is None:
			ui.message(_("This display cannot show graphics"))
			return None
		if state.focus is not None:
			# Translators: the name of the drawing of one area of a web page's layout, drilled
			# into. The placeholder is the area's name, such as "navigation, Main".
			name = _("layout of {area}").format(area=state.focus.title)
		elif state.app:
			# Translators: the name of the drawing of an application window's layout.
			name = _("layout of the window")
		elif state.view == "page":
			# Translators: the name of the drawing of a whole web page's layout.
			name = _("layout of the whole page")
		else:
			# Translators: the name of the drawing of the layout of the part of a web page on
			# screen.
			name = _("layout of the page on screen")
		drawing = self.drawingFor(
			state.page,
			state.view,
			state.viewport,
			size,
			name,
			state.document,
			focus=state.focus,
			state=state,
		)
		if drawing is None or not mode.enter(drawing):
			ui.message(mode.lastError or _("The drawing could not be shown"))
			return None
		state.drawing = drawing
		# The key to the letters on the panel is the drawing's note, which this includes.
		return mode.describe()

	def _shown(self) -> Optional["LayoutView"]:
		""":return: the overview of a page's layout, if that is what is on the display."""
		state = self.current
		mode = self.plugin.graphicsMode
		if state is None or not mode.active or mode.source is not state.drawing:
			# Translators: said when drilling into a page's layout is asked for with no layout
			# on the display.
			ui.message(_("No page layout is shown"))
			return None
		return state

	def drillIn(self) -> None:
		"""Draw the area last pressed as the whole drawing, with the areas inside it.

		Positions are then said within that area.
		"""
		state = self._shown()
		if state is None:
			return
		region = state.pressed
		if region is None:
			# Translators: said when drilling into a page's layout is asked for before any area
			# has been pressed, since the drill goes into the area last pressed.
			ui.message(_("Press a routing key in an area first"))
			return
		if not region.children:
			# Translators: said when the area asked to be drilled into has no areas inside it.
			# The placeholder is the area's name.
			ui.message(_("Nothing inside {area}").format(area=region.title))
			return
		before = (state.focus, list(state.trail), state.pressed)
		state.trail.append(state.focus)
		state.focus, state.pressed = region, None
		message = self.enter(state)
		if message is None:
			state.focus, state.trail, state.pressed = before
			return
		ui.message(message)

	def backOut(self) -> None:
		"""Draw the area around the one drilled into, or the whole page or screen."""
		state = self._shown()
		if state is None:
			return
		if state.focus is None:
			# Translators: said when backing out of a page's layout is asked for and the whole
			# page, or the whole screen, is already shown.
			ui.message(_("Already showing all of it"))
			return
		before = (state.focus, list(state.trail), state.pressed)
		# Came out of this area, so a drill straight back in returns to it.
		state.pressed = state.focus
		state.focus = state.trail.pop() if state.trail else None
		message = self.enter(state)
		if message is None:
			state.focus, state.trail, state.pressed = before
			return
		ui.message(message)

	def drawingFor(
		self,
		page,
		view: str,
		viewport,
		size: tuple,
		name: str,
		document=None,
		focus=None,
		state: Optional["LayoutView"] = None,
	):
		"""The drawing of a page's layout, which draws itself again when zoomed or moved.

		1. The page is drawn tall, a screen of it to a panel's height, opening at its top. See
		   `Drawing.opensAcross`.
		2. Zooming and moving compose the view again through `Drawing.redraw`, so labels stay
		   at braille size and areas keep their key letters.
		3. A second press in a box moves the browse mode cursor there. See `moveToArea`.

		:return: the drawing, or None.
		"""
		mode = self.plugin.graphicsMode
		width, height = size
		keys: dict = state.keys if state is not None else {}
		style = countStyle()
		whole = layoutDraw.focusWindow(page, view, viewport, focus)
		if view == "page":
			screens = whole.height / max(viewport.height, 1)
			source = layoutDraw.compose(
				mode.newBuffer,
				page,
				width,
				max(height, round(height * screens)),
				view,
				viewport,
				graphicsMode.labelCells,
				LAYOUT_DEPTH,
				keys=keys,
				focus=focus,
				style=style,
			)
		else:
			# The screen as tall as it has to be for everything on it to be drawn, rather than
			# squeezed onto one panel with areas left out. See `layoutDraw.composeFitting`.
			source = layoutDraw.composeFitting(
				mode.newBuffer,
				page,
				width,
				height,
				view,
				viewport,
				graphicsMode.labelCells,
				LAYOUT_DEPTH,
				keys=keys,
				focus=focus,
				style=style,
			)
		if source is None:
			return None

		def describer(overview):
			# A press says what is there, and remembers the area, which a drill goes into.
			def describeAt(x, y):
				box = overview.boxAt(x, y)
				if state is not None and box is not None:
					state.pressed = box.region
				return overview.describeAt(x, y)

			return describeAt

		identity = state.identity if state is not None else pageIdentity(document)

		def activator(overview):
			app = state is not None and state.app

			def activateAt(x, y):
				box = overview.boxAt(x, y)
				if box is None:
					return
				if app:
					self.moveToAppArea(box.region, identity)
				else:
					self.moveToArea(document, box.region, identity)

			return activateAt

		def areaOf(overview):
			# The region itself is the area, whatever view it is drawn in, so a press in the
			# sidebar and the next one a cell away, or after a move, are on the same area.
			def areaAt(x, y):
				box = overview.boxAt(x, y)
				return None if box is None else id(box.region)

			return areaAt

		def redraw(offset, span, pinWidth, pinHeight, top=0.0, down=1.0):
			window = source.windowAt(offset, span, top, down)
			part = layoutDraw.compose(
				mode.newBuffer,
				page,
				pinWidth,
				pinHeight,
				view,
				viewport,
				graphicsMode.labelCells,
				LAYOUT_DEPTH,
				window=window,
				keys=keys,
				focus=focus,
				style=style,
			)
			if part is None:
				return None
			key = part.keyText()
			return graphicsMode.Drawing(
				part.buffer,
				name=name,
				describeAt=describer(part),
				activateAt=activator(part),
				areaAt=areaOf(part),
				# Translators: the key to the letters on the part of a page's layout that is on
				# the display. The placeholder is a list of letters with what each stands for.
				note=_("Key: {key}").format(key=key) if key else "",
			)

		sourceKey = source.keyText()
		notes = [_("Key: {key}").format(key=sourceKey)] if sourceKey else []
		if source.unreadable:
			notes.append(
				# Translators: said after a drawing of a web page's layout when some areas on it
				# could not be drawn big enough to find, even with the drawing grown taller. The
				# placeholder is how many.
				_("{count} areas too small to show; magnify to see them").format(count=source.unreadable),
			)
		drawing = graphicsMode.Drawing(
			source.buffer,
			name=name,
			describeAt=describer(source),
			activateAt=activator(source),
			areaAt=areaOf(source),
			note=". ".join(notes),
			redraw=redraw,
			# How far in the zoom can go, as the mode counts it: a window no smaller than about
			# 200 pixels of the page, past which there is nothing more to see in a layout.
			points=max(1, round(whole.height * graphicsMode.MIN_WINDOW_POINTS / LAYOUT_SMALLEST_VIEW)),
			windowsVertically=True,
			# A view taller than the panel opens at its top, a panel wide, and is read by moving
			# down it with the panning keys.
			opensAcross=source.buffer.height > height,
		)
		return drawing

	def moveToArea(self, document, region, identity: Optional[tuple] = None) -> None:
		"""Put the browse mode cursor where an area of the page starts, and leave the drawing.

		Like quick navigation: the area's name is said, then the line there. The drawing comes
		down only when its display also shows the focus.

		The start is found again by the area's first element (`Region.anchor`), since browse
		mode can re-read a page under the same document; an offset is used only while the buffer
		is unchanged. Refused, with a reason, when the area has no place in the text, the page
		has changed, or the element is gone.

		:param document: the browse mode document the layout was read from.
		:param region: the area pressed.
		:param identity: the page and buffer the layout was read from; see `pageIdentity`.
		"""
		import controlTypes
		import speech
		import textInfos
		from textInfos.offsets import Offsets

		if region.start is None:
			# Translators: said when a reader asks to move to an area of a page's layout that
			# has no place in the page's text to move to. The placeholder is the area's name.
			ui.message(_("No place in the page to move to for {area}").format(area=region.title))
			return
		# Translators: said when a reader asks to move to an area of a page's layout, but the
		# page has changed since its layout was read, so the layout no longer fits it.
		changed = _("The page has changed; read its layout again")
		current = getattr(api.getFocusObject(), "treeInterceptor", None)
		if document is None or current is not document or not getattr(document, "isAlive", True):
			ui.message(changed)
			return
		page, buffer = identity or (None, None)
		pageNow, bufferNow = pageIdentity(document)
		if page is not None and pageNow != page:
			ui.message(changed)
			return
		start = region.start
		anchor = getattr(region, "anchor", None)
		try:
			if anchor is not None:
				# Its first element, wherever it now is; gone, the lookup says so.
				probe = document.makeTextInfo(textInfos.POSITION_FIRST)
				start = probe._getOffsetsFromFieldIdentifier(*anchor)[0]
			elif buffer is not None and bufferNow != buffer:
				# No element to find again, and a different reading of the page: the offset
				# belongs to that other text.
				ui.message(changed)
				return
			info = document.makeTextInfo(Offsets(start, start))
		except Exception:
			log.debugWarning("BrlMultiline: an area's start is not in the page now", exc_info=True)
			ui.message(changed)
			return
		if self.plugin.graphicsMode.holdsFocus():
			self.plugin.cancelPendingDrawings()
			self.plugin.graphicsMode.leave()
		if getattr(document, "passThrough", False):
			import browseMode

			document.passThrough = False
			browseMode.reportPassThrough(document)
		document._set_selection(info, reason=controlTypes.OutputReason.QUICKNAV)
		speech.speakMessage(region.title)
		line = info.copy()
		line.expand(textInfos.UNIT_LINE)
		speech.speakTextInfo(line, reason=controlTypes.OutputReason.QUICKNAV)

	def moveToAppArea(self, region, identity: Optional[tuple] = None) -> None:
		"""Move to an area of an application window: the focus to it, or to the first thing in it
		that can take the focus, or the navigator object to it when nothing in it can.

		The application counterpart of `moveToArea`, moving as F6 between panes does. Found
		again by the element's runtime id, and refused, with a reason, when the window is no
		longer in front or the element is gone.

		:param region: the area pressed, whose `anchor` is its element's runtime id.
		:param identity: the window the layout was read from, as `[window handle]`.
		"""
		import controlTypes
		import speech

		# Translators: said when a reader asks to move to an area of a window's layout, but the
		# window has changed since its layout was read, so the layout no longer fits it.
		changed = _("The window has changed; read its layout again")
		# Translators: said when a reader asks to move to an area of a window's layout while
		# another window is in front.
		notInFront = _("That window is no longer in front")
		hwnd = identity[0] if identity else None

		def inFront() -> bool:
			# Asked of Windows, not of NVDA's foreground object, which is only brought up to date by
			# events this thread cannot take while it is waiting on the lookups below.
			return bool(hwnd) and appCollect.foregroundWindow() == hwnd

		if not inFront():
			ui.message(notInFront)
			return
		anchor = getattr(region, "anchor", None)
		element = appCollect.findElement(hwnd, anchor) if anchor else None
		record = getattr(region, "record", None) or {}
		if element is None and record.get("hwnd"):
			# A window of its own, found by its handle when its runtime id finds nothing.
			element = appCollect.elementOfWindow(record["hwnd"], hwnd)
		if element is None:
			ui.message(changed)
			return
		target = appCollect.focusableWithin(element)
		if not inFront():
			# The lookups can take long enough for the reader to switch windows; then the move
			# is stale.
			ui.message(notInFront)
			return
		if self.plugin.graphicsMode.holdsFocus():
			self.plugin.cancelPendingDrawings()
			self.plugin.graphicsMode.leave()
		speech.speakMessage(region.title)
		if target is not None:
			try:
				# NVDA reports the focus when it arrives, as for any move of the focus.
				target.SetFocus()
				return
			except Exception:
				log.debugWarning("BrlMultiline: could not focus an area", exc_info=True)
		try:
			import UIAHandler
			from NVDAObjects.UIA import UIA

			obj = UIA(UIAElement=element.BuildUpdatedCache(UIAHandler.handler.baseCacheRequest))
		except Exception:
			log.debugWarning("BrlMultiline: could not make an object of an area", exc_info=True)
			ui.message(changed)
			return
		api.setNavigatorObject(obj)
		speech.speakObject(obj, reason=controlTypes.OutputReason.QUERY)
