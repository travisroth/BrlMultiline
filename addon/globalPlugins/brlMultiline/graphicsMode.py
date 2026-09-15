# BrlMultiline: the graphics mode.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A drawing on part of the display, with the zoom and pan that make it readable.

Read the tactile graphics plan's phase 2 before changing anything here.

**Why a mode and not merely a panel.** A drawing has state a rectangle does not: a scale, an
origin within the source, and the question of what a touch inside it means. A panel that
tiles a rectangle has nowhere to keep any of that. So the claim on the display is an ordinary
panel, composed through the same machinery as every other claim, and this object is what owns
it and holds the state — a `PanelOwner`, not a second way of arranging the display.

**The drawing does not travel through the container.** Everything else the add-on shows
becomes braille cells in a buffer NVDA writes. A drawing is pins, handed to the display as an
overlay, and composited by the driver over whatever cells arrive. `GraphicsPanel` claims the
rectangle so that nothing writes cells underneath, and the two halves meet only on the
hardware, in one write.

**Pointing is a routing press.** The claim holds one blank segment, and it is there for one
reason: a routing press reaches the add-on only through the segment it lands in. On this
hardware a reader points by putting a finger on the panel and pressing a routing key there,
which is one hand doing one thing. There is no second gesture available — running a command
means lifting the hand, and the panel reports the touch as released the moment they do. See
`GraphicsRoutingPolicy`.

**Refresh discipline.** The panel is a page turning device: every write is a full mechanical
refresh, it clatters, and the reader has to lift their hand for it to settle. So this redraws
on deliberate events only — entering, zooming, panning, a new drawing — and never on caret
movement or a focus change.
"""

import math
from typing import Optional

import braille
import ui
from logHandler import log

from .graphics import GraphicsSurface, PinRect, findSurface
from .layout import SegmentRect
from .panels import GraphicsPanel, PanelOwner
from .routing import RoutingPolicy

__all__ = [
	"Drawing",
	"GraphicsMode",
	"labelCells",
	"GraphicsRoutingPolicy",
	"testFigure",
]

PANEL_NAME = "graphics"
"""The claim's name, and the handle `deactivatePanel` is given."""

OVERLAY_KEY = "brlMultiline.graphics"
"""Names this drawing on the driver, so re-showing replaces it rather than stacking."""

DEFAULT_TEXT_LINES = 1
"""Braille lines left to NVDA above the drawing, by default.

Taken from the **top** of the drawable display for two reasons. On a display NVDA is driving
directly the first segment is usually the focus segment, and a claim that took it without
offering one in its place would be refused outright. And a label above a figure is where a
reader looks for it.
"""

FIT = 0
MAX_ZOOM_STEP = 5
ZOOM_FACTOR = 2
"""The zoom ladder, counted in steps out from the whole drawing.

**Step 0 shows all of it.** That is what a tactile viewer is for: you put a hand on the panel
and feel the shape of the thing, compressed as far as it needs to be to fit. It is also why
panning does not exist at step 0 — there is nothing off the edge to pan to — and the Monarch's
own tactile viewer works the same way.

Each step doubles, and magnification is what makes panning mean anything: past the point where
the drawing no longer fits, moving the window is the only way to reach the rest of it.

Five steps is 32 times the fitted size, which for a drawing that started at a quarter scale is
8 pins per source dot. `MAX_SCALE` stops it there whatever the ladder says, because beyond
that a single source dot is wider than a braille cell and the reader is feeling the
magnification rather than the figure.
"""

MIN_WINDOW_POINTS = 3
"""Data points below which a redrawn figure will not be zoomed in any further.

A chart of twenty periods magnified thirty-two times is a chart of half a period. Three is the
least that still has a shape to feel — a rise and a fall — and refusing at that point keeps the
zoom the reader is told matching the zoom they are feeling, which clamping silently would not.
"""

MAX_SCALE = 8.0
"""Pins per source dot, at the most magnified. See the zoom ladder."""


def labelCells(text: str) -> list[int]:
	"""Translate a short label into braille cells.

	Uses the reader's own output table, so a caption inside a figure reads the way the rest of
	their braille does.

	:param text: the label.
	:return: one cell value per cell, or an empty list if braille is not translating.
	"""
	try:
		import louisHelper

		cells, _brailleToRaw, _rawToBraille, _cursor = louisHelper.translate(
			[braille.handler.table.fileName, "braille-patterns.cti"],
			text,
		)
		return list(cells)
	except Exception:
		log.debugWarning("BrlMultiline: could not translate a figure's caption", exc_info=True)
		return []


class Drawing:
	"""A figure at its natural size, and what to call it.

	The source, never the thing on the display. What reaches the display is this sampled
	through the zoom and origin, which is why the source keeps its own size however far in the
	reader has magnified it.

	**A drawing that knows how it was made can be drawn again instead of magnified**, and that
	is what `redraw` is for. Magnifying is the only thing that can be done to a photograph: the
	dots are all there is, so zooming in makes each of them bigger and the parts that no longer
	fit go off the edges. A chart is not like that. It was composed for this panel out of
	numbers that are still to hand, so zooming into a date range can *recompose* it — the same
	panel, fewer periods, drawn at full resolution with their own dates written under them.

	The difference is not a nicety. Magnified, a chart's braille labels become smears of
	enlarged dots and say nothing, and the dates written at the two ends go on naming the whole
	range while the reader is looking at a tenth of it. Redrawn, the writing is writing and the
	dates are the dates of what is actually under the hand. Reported from hardware, which is
	where the two are told apart.

	A picture supplies no `redraw` and keeps the sampling. Both work; they are answers to
	different questions.
	"""

	def __init__(
		self,
		buffer,
		name: str = "",
		describeAt=None,
		redraw=None,
		points: int = 0,
		windowsVertically: bool = False,
		note: str = "",
		nextView=None,
		pinsPerPoint: int = 0,
	):
		"""
		:param buffer: the dots, a buffer from `GraphicsSurface.newBuffer`.
		:param name: what to call this when the reader asks what is on the display.
		:param describeAt: what is at a point of this drawing, taking source coordinates and
			returning a phrase or None. Optional, and it is the difference between a drawing a
			reader can feel and one they can interrogate: without it a press can only report a
			dot, so a reader learns that one bar is taller than another and never learns what
			either of them is. A chart supplies one; a scanned picture has nothing to say.
		:param redraw: composes this drawing again for a part of itself, taking where that part
			starts and how much of it there is, both as fractions of the whole, and giving back
			the width and height to compose for, and giving back a `Drawing` of that size.
			Optional, and the second thing only a drawing built from structure can offer. See
			the note above on what it changes.
		:param points: how many data points the whole drawing covers, for deciding how far in
			the reader can usefully zoom. Zero where the drawing is not made of points.
		:param windowsVertically: whether this figure has a vertical extent to window over as
			well as a horizontal one. False for a chart and True for a picture, and the
			difference is real rather than a convenience: a chart refits its value axis to
			whatever periods it is showing, so there is never anything above or below the
			panel to pan to, while half of a photograph is still half of a photograph. Only a
			figure that says yes is offered the vertical part of the window, because a chart
			handed one might honour it, and a chart that scrolled its values would be showing
			a range whose own axis no longer named it.
		:param note: something about this drawing a reader has to be told rather than left to
			work out from the panel. Empty for almost everything. The case it exists for is a
			drawing that is legitimately blank -- a window on the empty middle of a shape --
			where the panel has nothing to say and silence would read as a fault.
		:param nextView: draws the whole of this figure shown another way, taking 1 for the
			next way and -1 for the previous, and giving back a `Drawing`. Optional. A line
			chart supplies one to show some of its lines and put the others aside; the figure
			decides what its views are, and the mode only keeps the reader's place across them.
		:param pinsPerPoint: how many pins across one point needs to be felt on its own, for a
			figure whose zoom is counted in points. One for a line chart. Zero where there is
			no such thing, which is everything a zoom to one pin per point means nothing for.
		"""
		self.buffer = buffer
		self.name = name
		self.note = note
		self.describeAt = describeAt
		self.redraw = redraw
		self.points = points
		self.windowsVertically = windowsVertically
		self.nextView = nextView
		self.pinsPerPoint = pinsPerPoint

	@property
	def width(self) -> int:
		""":return: dots across."""
		return self.buffer.width

	@property
	def height(self) -> int:
		""":return: dots down."""
		return self.buffer.height


def testFigure(surface: GraphicsSurface, width: int, height: int) -> Optional[Drawing]:
	"""Build the figure phase 2 is verified with.

	Deliberately not a chart. Phase 2 is the enter, the claim, the transform and the leave;
	the first real content is phase 4. What this has to do is make every one of those visible
	to a finger: a border says where the claim's edges are, the diagonals say the drawing is
	not stretched or offset, the markers say a point lands where it was asked to, and the
	caption says text and graphics arrived in the same write.

	Drawn at twice the rectangle's size so that zooming has somewhere to go and panning has
	something to reach.

	:param surface: the display to make a buffer on.
	:param width: the rectangle's pin width. The figure is twice this.
	:param height: the rectangle's pin height. The figure is twice this.
	:return: the figure, or None if no buffer could be made.
	"""
	sourceWidth = max(1, width * 2)
	sourceHeight = max(1, height * 2)
	buffer = surface.newBuffer(sourceWidth, sourceHeight)
	if buffer is None:
		return None
	buffer.rect(0, 0, sourceWidth, sourceHeight)
	buffer.line(0, 0, sourceWidth - 1, sourceHeight - 1)
	buffer.line(sourceWidth - 1, 0, 0, sourceHeight - 1)
	buffer.marker(sourceWidth // 2, sourceHeight // 2, "diamond")
	buffer.marker(sourceWidth // 4, sourceHeight // 4, "cross")
	buffer.marker(3 * sourceWidth // 4, 3 * sourceHeight // 4, "square")
	cells = labelCells("test")
	if cells:
		# Cleared first, then drawn: a caption over the diagonals would be unreadable, and
		# `text` is additive like everything else here.
		buffer.clearRect(2, 2, len(cells) * 3 + 1, 5)
		buffer.text(3, 2, cells)
	return Drawing(buffer, name="test figure")


def _sayAfterThePress(text: str) -> None:
	"""Say something in speech and braille, once the routing press has finished.

	**Not `ui.message` directly, and the reason is a rule of NVDA's that this walks into.**
	`BrailleHandler.routeTo` is:

		self.buffer.routeTo(windowPos)
		if self.buffer is self.messageBuffer:
			self._dismissMessage()

	The first line is what runs the routing policy, so a message raised from inside a policy
	arrives *before* the second — and the second exists to make a routing key dismiss a
	message that is up. NVDA says so in its own docstring: "The message will be dismissed
	immediately if the user presses a cursor routing key." The press and the message were
	meant to be two events; here they are one, so the message dismissed itself the instant it
	appeared. It was spoken, and never reached braille, which is exactly how it behaved on
	hardware and is a difference no log would have shown.

	Queued onto the event queue, so it lands after `routeTo` has returned and the press is
	over. Then it is an ordinary message and stands for its ordinary timeout.

	:param text: what to say.
	"""
	try:
		import queueHandler

		queueHandler.queueFunction(queueHandler.eventQueue, ui.message, text)
	except Exception:
		# Better spoken now than not at all: without the queue the braille half is lost to
		# the dismissal above, and the speech half is the part carrying the answer.
		log.debugWarning("BrlMultiline: could not defer a message past the press", exc_info=True)
		ui.message(text)


class GraphicsRoutingPolicy(RoutingPolicy):
	"""Turns a routing press inside the drawing into a report of what is there.

	The Monarch answers a finger with the touched pin, and the driver keeps the pin that was
	under the finger at the moment a routing key went down. A press is therefore a *pointing*
	gesture with a position attached, at pin resolution rather than cell resolution, and it
	is the only pointing gesture this hardware can offer. A separate command cannot do it:
	the reader's hand is on the panel, running a command means lifting it, and the panel
	reports the touch as released the moment they do. NVDA compounds that by running a
	gesture's script from a queue rather than during dispatch, so even the press's own live
	touch has gone by the time a script sees it. The pin the driver kept is what is left.

	So the press does not route a cursor here. There is no text under the drawing to route
	into: the claim's segment is blank by design and exists so that this policy has
	somewhere to live.
	"""

	def __init__(self, mode: "GraphicsMode"):
		"""
		:param mode: the mode to ask. Held rather than looked up, so that a claim outliving
			the mode that made it reports nothing rather than reporting about a later figure.
		"""
		self.mode = mode

	def route(self, container, segmentNumber: int, segmentPos: int) -> None:
		if not self.mode.active:
			return
		self.mode.reportPress(segmentPos)

	def __repr__(self) -> str:
		return f"<{type(self).__name__}>"


class GraphicsMode(PanelOwner):
	"""A drawing shown on part of the display, and the state that makes it readable.

	Held by the plugin for as long as the reader wants a figure up. One at a time: a second
	`enter` replaces the first, because there is one panel and one overlay key.
	"""

	def __init__(self, plugin):
		"""
		:param plugin: the global plugin, for its claims.
		"""
		self.plugin = plugin

		self.lastError: Optional[str] = None
		"""Why the figure could not be shown, for a command to report."""

		self._drawing: Optional[Drawing] = None
		"""What is on the panel. None means the mode is not up, and is the whole of that test.

		The same object as `_source` for a drawing that is magnified rather than redrawn,
		and the current window's own drawing for one that redraws itself.
		"""

		self._source: Optional[Drawing] = None
		"""The figure as it was given, which is what the zoom and origin are a window onto.

		Kept apart from `_drawing` so that every window is computed from the whole rather
		than from the last window: zooming in and out again has to arrive back where it
		started, and a window taken of a window would drift.
		"""

		self._window: Optional[tuple] = None
		"""Which part of the source `_drawing` was redrawn for, so it is not redrawn again
		for the same part on every refresh."""

		self._rect: Optional[SegmentRect] = None
		"""The part of the display the drawing occupies, in NVDA's display coordinates.

		Not the whole claim. The claim covers the drawable display's band and includes the
		braille line the panel keeps beside the figure; this is what is left for pins.
		"""

		self._zoomStep = FIT
		self._originX = 0
		self._originY = 0
		"""Which source dot is at the drawing's top left, and how far out from fit it is.

		The origin means nothing at `FIT`, where the whole drawing is on the panel and is pinned
		to its corner; it starts mattering the moment there is more drawing than display.
		"""

		self._textLines = DEFAULT_TEXT_LINES
		"""Braille lines kept beside the figure. Zero gives the whole band to the drawing.

		Held so `setTextLines` can change it without the reader losing their zoom and origin,
		which is the whole point of being able to change it mid-read.
		"""

	# --- State -----------------------------------------------------------------------------

	@property
	def active(self) -> bool:
		""":return: whether a figure is up."""
		return self._drawing is not None

	@property
	def drawing(self) -> Optional[Drawing]:
		""":return: the source being shown, or None."""
		return self._drawing

	@property
	def zoom(self) -> float:
		""":return: how many steps out from the whole drawing, 0 showing all of it.

		A whole number on the ladder, and usually between two of them after a zoom to one pin
		per point.
		"""
		return self._zoomStep

	def fitScale(self, pins: PinRect) -> float:
		"""Pins per source dot that puts the whole drawing on the panel.

		The smaller of the two ratios, so nothing is cut off in either direction. Below 1 for a
		drawing larger than the display, which is the ordinary case for a real image and the
		reason the sampler has to compress rather than only magnify.

		**Never above 1.** Fitting a drawing smaller than the display would mean magnifying it
		to fill the panel, and that is not what showing the whole of something means: a figure
		authored at the panel's own size — a chart we generated, say — should come out at the
		size it was drawn. So this is "the whole drawing, at natural size or smaller", and
		magnification is always something the reader asked for.

		:param pins: the rectangle the drawing occupies.
		:return: the scale, above zero and at most 1.
		"""
		source = self._source.buffer
		if not source.width or not source.height:
			return 1.0
		return min(1.0, pins.width / source.width, pins.height / source.height) or 1.0

	def scale(self, pins: PinRect) -> float:
		"""Pins per source dot at the current zoom.

		:param pins: the rectangle the drawing occupies.
		:return: the scale. One means a source dot is a pin; below one the drawing is
			compressed to fit; above one it is magnified.
		"""
		scale = self.fitScale(pins) * (ZOOM_FACTOR**self._zoomStep)
		if self._countsPoints:
			# `MAX_SCALE` is about a dot growing wider than a finger, and a figure that counts
			# its zoom in points never grows a dot: it is composed again at the panel's own
			# size for fewer points. Its limit is running out of points, which `_tooFewPoints`
			# already says. Capped, a chart of a thousand days could never be zoomed closer
			# than an eighth of them, which is still several days to a pin.
			return scale
		return min(MAX_SCALE, scale)

	@property
	def _countsPoints(self) -> bool:
		""":return: whether the figure's zoom is measured in its points rather than its dots."""
		source = self._source
		return self._windows and source is not None and bool(source.pinsPerPoint) and bool(source.points)

	@property
	def origin(self) -> tuple:
		""":return: the source dot at the drawing's top left corner."""
		return self._originX, self._originY

	# --- Entering and leaving ---------------------------------------------------------------

	def drawingSize(self, textLines: int = DEFAULT_TEXT_LINES) -> Optional[tuple]:
		"""How big a drawing would be if one were shown now.

		For a caller composing a drawing for this display rather than bringing one that
		already exists. A chart is drawn to fill its rectangle exactly, so it has to know the
		rectangle before it can be built, and asking here keeps the claim's arithmetic — the
		band, the pitch, the text line, the composite's offset — in one place instead of
		being repeated by everything that wants to draw.

		:param textLines: braille lines that would be kept beside it.
		:return: width and height in pins, or None where nothing can be drawn.
		"""
		surface = findSurface()
		if surface is None:
			return None
		claim = self._claimFor(surface, textLines)
		if claim is None:
			return None
		panel = GraphicsPanel(PANEL_NAME, claim, textRows=textLines)
		pins = surface.pinRectForCells(panel.drawingRect)
		return None if pins.isEmpty else (pins.width, pins.height)

	def newBuffer(self, width: int, height: int):
		"""Make a buffer to compose a drawing in.

		The display's own factory, so a caller building a drawing never imports a driver.

		:param width: dots across.
		:param height: dots down.
		:return: a blank buffer, or None if there is no display to ask.
		"""
		surface = findSurface()
		return None if surface is None else surface.newBuffer(width, height)

	def enter(self, drawing: Optional[Drawing] = None, textLines: int = DEFAULT_TEXT_LINES) -> bool:
		"""Claim a rectangle and show a figure in it.

		:param drawing: the figure, or None for the test figure sized to the claim.
		:param textLines: braille lines to leave to NVDA above the drawing. Zero is allowed
			and gives the whole drawable display to the figure, which a claim will refuse if
			those rows hold the focus segment.
		:return: whether the figure is up.
		"""
		self.lastError = None
		surface = findSurface()
		if surface is None:
			self.lastError = _("This display cannot show graphics")
			return False
		claim = self._claimFor(surface, textLines)
		if claim is None:
			self.lastError = _("There is no room on this display for a drawing")
			return False
		panel = GraphicsPanel(
			PANEL_NAME,
			claim,
			textRows=textLines,
			drawingRoutingPolicy=GraphicsRoutingPolicy(self),
		)
		rect = panel.drawingRect
		pins = surface.pinRectForCells(rect)
		if pins.isEmpty:
			self.lastError = _("There is no room on this display for a drawing")
			return False
		if drawing is None:
			drawing = testFigure(surface, pins.width, pins.height)
			if drawing is None:
				self.lastError = _("The display would not provide a drawing surface")
				return False
		# Claimed before anything is drawn. A claim that cannot be honoured leaves the display
		# as it was, and a drawing pushed first would then be sitting over live braille with
		# nothing owning the rows underneath it.
		try:
			self.plugin.activatePanel(panel)
		except (ValueError, LookupError):
			log.debugWarning(f"BrlMultiline: the graphics claim {claim} was refused", exc_info=True)
			self.lastError = _("The display could not give up those rows for a drawing")
			return False
		self._source = drawing
		self._drawing = drawing
		self._window = None
		self._rect = rect
		self._textLines = textLines
		self._zoomStep = FIT
		self._originX = 0
		self._originY = 0
		self.render(surface)
		self._notifyChanged()
		return True

	def _notifyChanged(self) -> None:
		"""Tell the plugin what is drawn has changed, so layers of keys for a drawing can follow it.

		A hook on the plugin rather than an extension point, because the plugin is the one listener
		there is and it already owns this mode. Failing to tell it must not cost the drawing.
		"""
		notify = getattr(self.plugin, "onGraphicsChanged", None)
		if notify is None:
			return
		try:
			notify()
		except Exception:
			log.error("BrlMultiline: could not tell the plugin the drawing changed", exc_info=True)

	def leave(self) -> None:
		"""Take the figure off the display and give the rows back."""
		if not self.active:
			return
		# The drawing goes first, so that the rows are never briefly showing text with a stale
		# figure still composited over them.
		surface = findSurface()
		if surface is not None:
			surface.hide(OVERLAY_KEY)
		self._drawing = None
		self._source = None
		self._window = None
		self._rect = None
		try:
			self.plugin.deactivatePanel(PANEL_NAME)
		except Exception:
			log.error("BrlMultiline: could not give back the graphics claim", exc_info=True)
		self._notifyChanged()

	@property
	def source(self) -> Optional[Drawing]:
		""":return: the figure that was entered, rather than the window of it being shown.

		What a caller holding a figure asks to find out whether it is still the one on the
		display. `drawing` is the window; this is the thing the window is of, and it is the
		one that does not change as the reader zooms.
		"""
		return self._source

	@property
	def textLines(self) -> int:
		":return: braille lines kept beside the figure."
		return self._textLines

	def replaceSource(self, drawing: Drawing) -> bool:
		"""Show a different figure of the same thing, without losing the view.

		What a change of style needs. Going through `enter` claims the panel again and resets
		the zoom and both origins, so a reader who had magnified a corner of a diagram and
		switched from outlines to brightness was put back at the whole picture -- which
		defeats the purpose of having two styles, since the way to tell which reads better is
		to feel the same part of the picture in both.

		The window is kept and recomposed from the new figure. `_window` is cleared so that
		the recomposition actually happens, and `_drawing` is deliberately left alone: a
		figure that will not compose the current window has to be a refusal, and `_reframe`
		treats a null drawing as "nothing composed yet" and falls back to the whole figure.

		:param drawing: the new figure, which must be of the same thing at the same size.
		:return: whether it is up. False leaves the previous figure exactly as it was.
		"""
		if not self.active or drawing is None:
			return False
		surface = findSurface()
		if surface is None:
			return False
		was = self._source
		restore = self._state()
		self._source = drawing
		self._window = None
		if self.render(surface):
			self._notifyChanged()
			return True
		self._source = was
		self._restore(restore)
		return False

	@property
	def hasViews(self) -> bool:
		""":return: whether the figure up can be shown another way. See `Drawing.nextView`."""
		return self.active and getattr(self._source, "nextView", None) is not None

	def changeView(self, direction: int) -> bool:
		"""Show the figure the next or previous way it offers, keeping the zoom and the place.

		Through `replaceSource`, for the reason it exists: the way to tell what one line of a
		chart is doing is to feel the same days with and without the others, and a view change
		that put the reader back at the whole chart would take them off those days.

		:param direction: 1 for the next view, -1 for the previous.
		:return: whether the new view is up. False leaves the old one exactly as it was.
		"""
		nextView = getattr(self._source, "nextView", None) if self.active else None
		if nextView is None:
			return False
		try:
			drawing = nextView(direction)
		except Exception:
			# A figure refusing a view says so by raising, with its reason; this knows nothing
			# about charts, so it cannot tell a refusal from a fault and does not try.
			log.debugWarning("BrlMultiline: a figure would not draw another view", exc_info=True)
			return False
		return self.replaceSource(drawing)

	def setTextLines(self, lines: int) -> bool:
		"""Change how much of the band stays braille, keeping the figure and where it is read.

		The reason this exists rather than being decided once at `enter`: a reader wants the
		whole panel for a figure *at times*, and asking them to leave, change a setting and
		come back would lose their zoom and their place in the drawing, which is most of what
		they had built up.

		At zero the drawing takes the band and the focus line goes with it. That is a real
		cost — NVDA's focus braille has nowhere to go and is dropped rather than drawn under
		the figure — so it is a thing the reader asks for and can undo with the same command,
		and the caller is expected to say so.

		**Transactional, for the same reason zoom is.** It used to claim the new rectangle,
		record the new size, call `render` and return True without looking at what `render`
		said. A figure that would not compose for the new shape then left the old overlay on
		the display -- the old 96 by 35 drawing, still at pin row 5 -- while the mode reported
		no braille line and a full panel rectangle, and the command announced "full panel, no
		braille line" over a drawing that had not moved. That is the same disagreement between
		the panel and the words that the zoom rollback was written to end, reached through a
		different door.

		So the old claim and the old state are both put back when the new composition fails.

		:param lines: braille lines to keep, zero for the whole band.
		:return: whether the display took it. False leaves the figure exactly as it was.
		"""
		if not self.active:
			return False
		surface = findSurface()
		if surface is None:
			return False
		wanted = max(0, lines)
		claim = self._claimFor(surface, wanted)
		if claim is None:
			return False
		panel = GraphicsPanel(
			PANEL_NAME,
			claim,
			textRows=wanted,
			drawingRoutingPolicy=GraphicsRoutingPolicy(self),
		)
		wasLines = self._textLines
		wasRect = self._rect
		restore = self._state()
		try:
			self.plugin.activatePanel(panel)
		except (ValueError, LookupError):
			log.debugWarning(
				f"BrlMultiline: the graphics claim {claim} with {wanted} text lines was refused",
				exc_info=True,
			)
			return False
		self._textLines = wanted
		self._rect = panel.drawingRect
		# The window is kept where it was, and only pulled back inside if the figure grew
		# past what the source has left. A reader gaining a row should not lose their place.
		if self.render(surface):
			return True
		self._textLines = wasLines
		self._rect = wasRect
		self._restore(restore)
		self._reclaim(surface, wasLines)
		return False

	def _reclaim(self, surface: GraphicsSurface, textLines: int) -> bool:
		"""Put the previous claim back after a change of shape that would not draw.

		Rebuilt from `_claimFor` rather than kept as an object, because the claim is a
		function of the surface and the number of text lines and nothing else -- holding the
		old one would be holding a second copy of something already derivable, and the copy is
		the thing that goes stale.

		:param surface: the drawable display.
		:param textLines: how many braille lines the old claim left.
		:return: whether the display took it back.
		"""
		claim = self._claimFor(surface, textLines)
		if claim is None:
			log.error("BrlMultiline: the previous graphics claim can no longer be made")
			return False
		try:
			self.plugin.activatePanel(
				GraphicsPanel(
					PANEL_NAME,
					claim,
					textRows=textLines,
					drawingRoutingPolicy=GraphicsRoutingPolicy(self),
				),
			)
		except (ValueError, LookupError):
			log.error("BrlMultiline: the previous graphics claim was refused", exc_info=True)
			return False
		self.render(surface)
		return True

	def _claimFor(self, surface: GraphicsSurface, textLines: int) -> Optional[SegmentRect]:
		"""Work out which cells to claim.

		The drawable display's whole band, and on a composite that is the member's rows
		rather than the display's, which keeps a figure on the Monarch from claiming rows
		belonging to a display beside it.

		**The whole band, and not a careful subset.** Panels are evicted whole, so a claim on
		part of a band takes the band's panel with it — focus segment included, however
		carefully the claim avoided the focus segment's own rows. An earlier version left
		those rows out and was refused on hardware all the same. `GraphicsPanel` therefore
		takes the band and supplies the replacement braille line itself; see its docstring.

		:param surface: the display that can draw.
		:param textLines: braille lines the panel will keep at the top of the claim.
		:return: the claim in NVDA's display coordinates, or None if there would be no room
			left to draw in.
		"""
		if surface.numRows <= 0 or surface.numCols <= 0:
			return None
		if textLines >= surface.numRows:
			return None
		return SegmentRect(
			row=surface.rowStart,
			col=0,
			numRows=surface.numRows,
			numCols=surface.numCols,
		)

	# --- Drawing ----------------------------------------------------------------------------

	@property
	def _windows(self) -> bool:
		""":return: whether the figure redraws itself for a window rather than being magnified.

		Asked of the figure rather than of a type or a flag, exactly as `describeAt` is: a
		drawing either can do this or cannot, and what kind of thing it is is beside the point.
		A chart can; a scanned picture cannot; anything later that can will say so the same way.
		"""
		return self._source is not None and self._source.redraw is not None

	@property
	def _windowsVertically(self) -> bool:
		""":return: whether the figure windows up and down as well as left and right.

		Asked of the figure for the same reason `_windows` is. A picture has a top and a
		bottom that stay where they are; a chart's value axis is refitted to whatever is
		showing, so it has neither.
		"""
		return self._windows and bool(getattr(self._source, "windowsVertically", False))

	def _reframe(self, pins: PinRect) -> bool:
		"""Ask the figure to draw itself again for what is visible, if it can.

		The origin and the zoom are already a window over the source in dots, and the source of
		a redrawable figure is exactly panel sized — so that window, as a fraction of the whole,
		is the part of the data the reader is looking at. Handing it over as a fraction keeps
		this from knowing that the data is periods, or prices, or anything at all.

		**The size goes with it**, because the rectangle can change under a figure that is
		already up: toggling the braille line beside the drawing is a command, and a figure
		composed for the old rectangle would be blitted into the new one with a row of it
		cut off — the row its dates are written on.

		Skipped when the window has not moved, so an ordinary refresh — a rebuild, a settings
		change, the flow giving rows back — costs nothing.

		**A figure that will not compose the window says no, and nothing here pretends
		otherwise.** It used to fall back to the whole figure and record the refused window as
		though it had been drawn, which left the panel showing one thing and the zoom, the
		origin and the spoken position describing another — a reader told "50 across, 47 down"
		with the whole picture under their hands. Wrong in the worst way available: confidently.
		So it answers False and the caller that moved is the one that puts it back.

		:param pins: the rectangle the drawing occupies.
		:return: whether the drawing now matches the window. False only where the figure
			refused to compose one, which leaves the previous drawing in place untouched.
		"""
		if not self._windows:
			return True
		source = self._source.buffer
		if not source.width or not source.height:
			return True
		visibleX, visibleY = self._visible(pins)
		visibleX = min(visibleX, source.width)
		visibleY = min(visibleY, source.height)
		vertical = self._windowsVertically
		down = (self._originY / source.height, visibleY / source.height) if vertical else (0.0, 1.0)
		window = (
			self._originX / source.width,
			visibleX / source.width,
			pins.width,
			pins.height,
			down[0],
			down[1],
		)
		if window == self._window and self._drawing is not None:
			return True
		whole = window[:2] == (0.0, 1.0) and down == (0.0, 1.0)
		if whole and (source.width, source.height) == (pins.width, pins.height):
			# The whole figure at the size it was drawn, which is the figure itself. Drawing
			# it again would only produce a copy, and entering the mode is the commonest
			# time this is asked.
			self._drawing = self._source
			self._window = window
			return True
		try:
			# The vertical half is passed by keyword and only to a figure that asked for it.
			# A chart's closure does not take it, and that is the point: the horizontal window
			# is something every redrawable figure has, and the vertical one is not.
			extra = {"top": down[0], "down": down[1]} if vertical else {}
			drawn = self._source.redraw(window[0], window[1], pins.width, pins.height, **extra)
		except Exception:
			log.error("BrlMultiline: a figure could not draw itself again", exc_info=True)
			drawn = None
		if drawn is None:
			if self._drawing is None:
				# Nothing has been composed yet, so there is no previous view to keep and the
				# figure itself is a better answer than a blank panel.
				self._drawing = self._source
				self._window = window
				return True
			return False
		self._drawing = drawn
		self._window = window
		return True

	def render(self, surface: Optional[GraphicsSurface] = None) -> bool:
		"""Sample the source through the zoom and origin and put it on the display.

		One overlay write, which is one mechanical refresh. Every caller here is a deliberate
		event for exactly that reason.

		:param surface: the display, or None to find it again.
		:return: whether the display took the drawing.
		"""
		if not self.active or self._rect is None:
			return False
		if surface is None:
			surface = findSurface()
		if surface is None:
			return False
		pins = surface.pinRectForCells(self._rect)
		if pins.isEmpty:
			return False
		buffer = surface.newBuffer(pins.width, pins.height)
		if buffer is None:
			return False
		self._clampOrigin(pins)
		if not self._reframe(pins):
			# The figure would not compose this window. The overlay already on the display is
			# the last one that was composed and is still correct for the state the caller is
			# about to put back, so it is left alone rather than rewritten with something that
			# does not match what is being reported.
			return False
		if self._windows:
			# Already composed for this panel at this window, so there is nothing to sample:
			# a dot of it is a pin of the display.
			buffer.blit(self._drawing.buffer)
		else:
			self._sample(buffer, pins)
		return surface.show(OVERLAY_KEY, pins, buffer)

	def _sample(self, buffer, pins: PinRect) -> None:
		"""Draw the visible part of the source into a buffer the size of the claim.

		Each pin stands for a rectangle of the source — one dot or a fraction of one when
		magnified, several when the drawing is compressed to fit — and the pin is **raised if
		any dot in that rectangle is raised**.

		That rule rather than sampling the middle dot, because compression is where a tactile
		drawing is most easily ruined: a line one dot wide, reduced to a quarter scale, is
		missed by three sample points out of four and comes out as a dashed line or as
		nothing. Taking any dot in the rectangle keeps every line the drawing had, at the cost
		of thickening a dense area into a solid one — which is the right way round: a reader
		can feel that a region is busy, and cannot feel a line that is not there.

		At a scale of 1 or more the rectangle is a single dot and this degenerates to nearest
		neighbour, so magnification costs nothing extra.

		:param buffer: the destination, the size of `pins`.
		:param pins: the rectangle being filled, for its size.
		"""
		source = self._source.buffer
		scale = self.scale(pins)
		# How much source one pin covers. At least one dot, so a magnified pin still asks
		# about the dot it is standing on rather than about an empty span.
		span = math.ceil(1 / scale) if scale < 1 else 1
		for y in range(pins.height):
			top = self._originY + int(y / scale)
			if top >= source.height:
				break
			for x in range(pins.width):
				left = self._originX + int(x / scale)
				if left >= source.width:
					break
				if self._anyDot(source, left, top, span):
					buffer.setDot(x, y)

	@staticmethod
	def _anyDot(source, left: int, top: int, span: int) -> bool:
		"""Whether any dot of a square of the source is raised.

		:param source: the drawing.
		:param left: leftmost source column.
		:param top: topmost source row.
		:param span: how many dots across and down the square is.
		:return: whether one of them is raised.
		"""
		if span == 1:
			return source.getDot(left, top)
		for dy in range(span):
			for dx in range(span):
				if source.getDot(left + dx, top + dy):
					return True
		return False

	def _visible(self, pins: PinRect) -> tuple:
		""":return: how many source dots across and down are on the display.

		Rounded up, because a source dot showing only partly is still one the reader can feel
		and one that panning has to be able to reach.

		:param pins: the rectangle the drawing occupies.
		"""
		scale = self.scale(pins)
		return math.ceil(pins.width / scale), math.ceil(pins.height / scale)

	def positionWords(self, surface: Optional[GraphicsSurface] = None) -> str:
		"""Where the window sits in the drawing, for a reader rather than for a debugger.

		The origin is held as a source dot, which is the right thing to compute with and the
		wrong thing to say. "At 48, 18" means the source dot now at the top left corner, and a
		reader has no way to know what that is a fraction of: the number depends on how large
		the drawing happens to be in dots, which is a fact about the file and not about what
		they are feeling. Panning by a quarter of the view produced a different pair of
		numbers each time with nothing to measure them against.

		So it is said as a percentage of **how far the window can move**, which is the
		question a reader panning around a magnified drawing actually has: 0 is hard against
		one edge, 100 hard against the other, and the ends are named rather than numbered
		because hitting an edge is worth hearing as an edge. An axis with nothing to pan along
		— a drawing wider than the display but not taller, say — is left out rather than
		reported as a meaningless zero. A figure that redraws itself for its window has no
		up and down to report at all: it fits its value axis to whatever it is showing, so
		there is never anything above or below the panel and "top edge" would be a fact
		about nothing. A picture redraws itself too and does have an up and a down, so it
		is asked rather than assumed — see `Drawing.windowsVertically`.

		:param surface: the display, or None to find it again.
		:return: a short phrase, empty when nothing can move in either direction.
		"""
		if not self.active or self._rect is None:
			return ""
		if surface is None:
			surface = findSurface()
		if surface is None:
			return ""
		pins = surface.pinRectForCells(self._rect)
		if pins.isEmpty:
			return ""
		visibleX, visibleY = self._visible(pins)
		source = self._source.buffer
		parts = []
		across = self._axisWords(
			self._originX,
			source.width - visibleX,
			# Translators: the drawing is panned hard against its left edge.
			_("left edge"),
			# Translators: the drawing is panned hard against its right edge.
			_("right edge"),
			# Translators: how far across a drawing the visible part sits, as a percentage of
			# how far it can be moved. The placeholder is that percentage.
			_("{percent} across"),
		)
		down = "" if (self._windows and not self._windowsVertically) else self._axisWords(
			self._originY,
			source.height - visibleY,
			# Translators: the drawing is panned hard against its top edge.
			_("top edge"),
			# Translators: the drawing is panned hard against its bottom edge.
			_("bottom edge"),
			# Translators: how far down a drawing the visible part sits, as a percentage of
			# how far it can be moved. The placeholder is that percentage.
			_("{percent} down"),
		)
		parts = [words for words in (across, down) if words]
		return ", ".join(parts)

	@staticmethod
	def _axisWords(origin: int, span: int, atStart: str, atEnd: str, between: str) -> str:
		"""Say where along one axis the window sits.

		:param origin: the first source dot visible along this axis.
		:param span: how far the window can move along it, zero if it cannot.
		:param atStart: what to say hard against the low edge.
		:param atEnd: what to say hard against the high edge.
		:param between: what to say elsewhere, taking a `percent` placeholder.
		:return: the words, empty when this axis cannot move at all.
		"""
		if span <= 0:
			return ""
		if origin <= 0:
			return atStart
		if origin >= span:
			return atEnd
		return between.format(percent=round(origin / span * 100))

	def showsWholeDrawing(self, pins: PinRect) -> bool:
		"""Whether everything the drawing has is on the panel.

		True at `FIT` by construction, and the reason panning says so rather than reporting an
		edge: there is no edge to have reached, the whole thing is under the reader's hands.

		:param pins: the rectangle the drawing occupies.
		:return: whether nothing is off the panel.
		"""
		visibleX, visibleY = self._visible(pins)
		source = self._source.buffer
		if self._windows and not self._windowsVertically:
			# A redrawn chart fits its value axis to whatever it is showing, so there is
			# never anything above or below the panel to pan to. Only the period range is
			# a window, and only it can have an edge. A picture is not like that: half of it
			# is still half of it, and the other half is up or down.
			return visibleX >= source.width
		return visibleX >= source.width and visibleY >= source.height

	def _clampOrigin(self, pins: PinRect) -> None:
		"""Keep the origin somewhere the source actually is.

		:param pins: the rectangle the drawing occupies.
		"""
		source = self._source.buffer
		visibleX, visibleY = self._visible(pins)
		self._originX = max(0, min(self._originX, max(0, source.width - visibleX)))
		if self._windows and not self._windowsVertically:
			self._originY = 0
		else:
			self._originY = max(0, min(self._originY, max(0, source.height - visibleY)))

	# --- Zoom and pan -----------------------------------------------------------------------

	def zoomBy(self, step: int) -> bool:
		"""Magnify the figure, keeping what is under the middle of the claim in the middle.

		Zooming about the centre rather than the corner, because the reader's hand is on the
		figure and the thing they were feeling should still be under it afterwards.

		:param step: how many levels in, negative for out.
		:return: whether the zoom changed.
		"""
		if not self.active or self._rect is None:
			return False
		surface = findSurface()
		if surface is None:
			return False
		pins = surface.pinRectForCells(self._rect)
		if pins.isEmpty:
			return False
		wanted = self._stepFrom(step)
		if wanted == self._zoomStep:
			return False
		if wanted > self._zoomStep and self._tooFewPoints(wanted):
			# A chart of twenty periods magnified thirty-two times is a chart of half a
			# period, which is not a chart. Refused rather than clamped, so that the zoom
			# the reader is told matches the one they are feeling.
			return False
		if wanted > self._zoomStep and self._atScaleCap(pins):
			# The ladder has steps left but the scale cap has been reached, which happens to a
			# drawing that started near the panel's own size. Saying no here keeps the reported
			# zoom honest: a step that changes nothing on the panel should not change the number.
			return False
		return self._zoomTo(wanted, pins, surface)

	def _stepFrom(self, step: int) -> float:
		"""The ladder step a zoom key goes to from here.

		From a step on the ladder that is simply the next one. From a zoom to one pin per
		point, which is usually between two of them, it is the rung on the side the key points
		to, so magnifying from 2.6 times goes to 4 and shrinking goes to 2, and the ladder is
		back on its own numbers after one press rather than drifting at 5.2 and 1.3.

		Past the top of the ladder, which only a zoom to one pin per point reaches, magnifying
		stays where it is rather than going back down to the top rung.

		:param step: how many levels in, negative for out.
		:return: the step, which may be the current one where there is nowhere to go.
		"""
		here = self._zoomStep
		if step > 0:
			wanted = math.floor(here) + step
		elif step < 0:
			wanted = math.ceil(here) + step
		else:
			return here
		return max(FIT, min(max(MAX_ZOOM_STEP, here), wanted))

	def _atScaleCap(self, pins: PinRect) -> bool:
		""":return: whether the drawing is already as large as the pins may make it.

		:param pins: the rectangle the drawing occupies.
		"""
		return not self._countsPoints and self.scale(pins) >= MAX_SCALE

	def _zoomTo(self, wanted: float, pins: PinRect, surface: GraphicsSurface) -> bool:
		"""Go to a zoom step about the middle of the view, or stay put if it will not draw.

		:param wanted: the step.
		:param pins: the rectangle the drawing occupies.
		:param surface: the display.
		:return: whether the zoom changed.
		"""
		visibleX, visibleY = self._visible(pins)
		centreX = self._originX + visibleX // 2
		centreY = self._originY + visibleY // 2
		restore = self._state()
		self._zoomStep = wanted
		visibleX, visibleY = self._visible(pins)
		self._originX = centreX - visibleX // 2
		self._originY = centreY - visibleY // 2
		if not self.render(surface):
			self._restore(restore)
			return False
		return True

	def zoomRefusal(self, step: int) -> str:
		""":return: why a zoom did not happen, for a reader who pressed the key and felt
		nothing change. Empty if nothing is in the way.

		**A refusal a reader cannot account for is the same as a broken key.** Every guard
		below was written with a reason and none of them said it out loud: the announcement
		after a zoom reports where the reader now is, so a refused zoom reported where they
		already were, in the same words, however many times they pressed. That reads exactly
		like a command that is not wired up -- and it was reported as one, on a toolbar whose
		capture simply had no more detail in it.

		Recomputed from the same guards rather than recorded by `zoomBy`, so that there is no
		second piece of state to fall out of step with the first.

		:param step: the step that was asked for, negative for out.
		"""
		if not self.active or self._rect is None:
			return ""
		surface = findSurface()
		if surface is None:
			return ""
		pins = surface.pinRectForCells(self._rect)
		if pins.isEmpty:
			return ""
		wanted = self._stepFrom(step)
		if wanted == self._zoomStep:
			if step > 0:
				# Translators: reported when the drawing cannot be magnified any further
				# because the magnification ladder has no steps left.
				return _("closest view")
			return ""
		if step > 0 and self._tooFewPoints(wanted):
			# Translators: reported when magnifying further would show no more than it
			# already does, because the picture or chart has run out of detail rather than
			# because the display has run out of room.
			return _("no more detail to show")
		if step > 0 and self._atScaleCap(pins):
			# Translators: reported when the drawing is already as large as the pins can make
			# it, so a further step would change the number and not the panel.
			return _("as large as the pins can show")
		# Translators: reported when the figure would not draw the part that was asked for,
		# so the view the reader already had was kept.
		return _("this part will not draw")

	def pointZoomRefusal(self) -> str:
		""":return: why a zoom to one pin per point cannot happen, empty if it can.

		Asked before the zoom rather than after it, unlike `zoomRefusal`, because both of these
		are facts about the figure and the panel and neither depends on trying.
		"""
		if not self.active or self._rect is None:
			return ""
		source = self._source
		if source is None or not self._countsPoints:
			# Translators: reported when a zoom to one pin per point is asked for on a drawing
			# that is not made of points, such as a picture.
			return _("this drawing has no points to zoom to")
		surface = findSurface()
		if surface is None:
			return ""
		pins = surface.pinRectForCells(self._rect)
		if pins.isEmpty:
			return ""
		if source.points * source.pinsPerPoint <= pins.width:
			# Translators: reported when a zoom to one pin per point is asked for on a chart
			# whose points already each have a pin or more in the whole-chart view.
			return _("every point already has its own pin")
		return ""

	def zoomToPoints(self) -> bool:
		"""Zoom a figure made of points to where each point has its own pin, and no more.

		The view a ladder of doublings almost never lands on. Two hundred and fifty days across
		ninety-six pins is 2.6 days to a pin whole and 1.3 at the first doubling, which is still
		two days sharing a pin somewhere, and 0.65 at the next, which is every day a pin and a
		half wide and a third of the panel's width given to spacing. The number that matters is
		the one where nothing is shared and nothing is spent, and it is a fact about the data
		and the panel rather than a power of two.

		About the middle of the view, as every zoom is. The figure is then left to space the
		points exactly, since the window a dot of origin can express is only close.

		:return: whether the view is now at one pin per point. True where it already was.
		"""
		source = self._source
		if source is None or self._rect is None or self.pointZoomRefusal():
			return False
		surface = findSurface()
		if surface is None:
			return False
		pins = surface.pinRectForCells(self._rect)
		if pins.isEmpty:
			return False
		across = max(MIN_WINDOW_POINTS, pins.width // source.pinsPerPoint)
		# The window is `pins.width / scale` dots of a source `source.buffer.width` wide, and it
		# should hold `across` of `source.points` points.
		scale = pins.width * source.points / (across * source.buffer.width)
		wanted = math.log2(scale / self.fitScale(pins)) / math.log2(ZOOM_FACTOR)
		if abs(wanted - self._zoomStep) < 1e-9:
			return True
		return self._zoomTo(wanted, pins, surface)

	def _tooFewPoints(self, step: float) -> bool:
		"""Whether zooming this far would leave too little of the data to be a chart.

		:param step: the zoom step being asked for.
		:return: whether it should be refused.
		"""
		points = self._source.points if self._source is not None else 0
		if not self._windows or not points:
			return False
		return points / (ZOOM_FACTOR**step) < MIN_WINDOW_POINTS

	def panBy(self, dx: int, dy: int) -> bool:
		"""Move the window over the source, in source dots.

		:param dx: dots to move right, negative for left.
		:param dy: dots to move down, negative for up.
		:return: whether anything moved.
		"""
		if not self.active or self._rect is None:
			return False
		surface = findSurface()
		if surface is None:
			return False
		pins = surface.pinRectForCells(self._rect)
		if pins.isEmpty:
			return False
		if self.showsWholeDrawing(pins):
			return False
		before = (self._originX, self._originY)
		restore = self._state()
		self._originX += dx
		self._originY += dy
		self._clampOrigin(pins)
		if (self._originX, self._originY) == before:
			return False
		if not self.render(surface):
			self._restore(restore)
			return False
		return True

	def _state(self) -> tuple:
		""":return: everything a refused window has to be able to put back.

		Zoom, origin, which window was last composed and what was composed for it. Taken before
		a move rather than reconstructed afterwards, because the composed drawing cannot be
		reconstructed at all: the figure has already said it will not make that window, and
		asking it again for the one before would be a second composition of something the
		display is already showing.
		"""
		return (self._zoomStep, self._originX, self._originY, self._window, self._drawing)

	def _restore(self, state: tuple) -> None:
		"""Put back what `_state` took.

		Nothing is written to the display here, and that is the point: the overlay on it was
		never replaced, so it already agrees with what is being put back.

		:param state: from `_state`.
		"""
		self._zoomStep, self._originX, self._originY, self._window, self._drawing = state

	def panStep(self) -> tuple:
		""":return: a sensible pan distance across and down, in source dots.

		A quarter of what is visible, so a pan moves usefully and still leaves three quarters
		of the previous view to reorient by. One dot at minimum, so panning never does nothing
		on a very small claim.
		"""
		if not self.active or self._rect is None:
			return 0, 0
		surface = findSurface()
		if surface is None:
			return 0, 0
		pins = surface.pinRectForCells(self._rect)
		if pins.isEmpty:
			return 0, 0
		visibleX, visibleY = self._visible(pins)
		return max(1, visibleX // 4), max(1, visibleY // 4)

	# --- Touch ------------------------------------------------------------------------------

	def pointForPin(
		self,
		pinX: int,
		pinY: int,
		surface: Optional[GraphicsSurface] = None,
	) -> Optional[tuple]:
		"""Where in the source a pin of the display falls.

		The inverse of the claim's cell to pin conversion and of the zoom and origin, which is
		the whole reason both are kept rather than baked into the buffer.

		:param pinX: pin column on the display.
		:param pinY: pin row on the display.
		:param surface: the display, or None to find it again.
		:return: the source dot, or None if that pin is not inside the figure.
		"""
		if not self.active or self._rect is None:
			return None
		if surface is None:
			surface = findSurface()
		if surface is None:
			return None
		pins = surface.pinRectForCells(self._rect)
		if pins.isEmpty:
			return None
		insideX = pinX - pins.x
		insideY = pinY - pins.y
		if not (0 <= insideX < pins.width and 0 <= insideY < pins.height):
			return None
		if self._windows:
			# What is on the panel *is* the drawing, drawn for this window at this size, so
			# a pin is a dot of it. Putting the zoom and the origin through here as well
			# would apply the window twice and answer about a period the reader is not
			# touching — which is worse than no answer, because it is a plausible one.
			sourceX, sourceY = insideX, insideY
		else:
			scale = self.scale(pins)
			sourceX = self._originX + int(insideX / scale)
			sourceY = self._originY + int(insideY / scale)
		source = self._drawing.buffer
		if not (0 <= sourceX < source.width and 0 <= sourceY < source.height):
			return None
		return sourceX, sourceY

	def pointForCell(
		self,
		segmentPos: int,
		surface: Optional[GraphicsSurface] = None,
	) -> Optional[tuple]:
		"""Where in the source a routing cell of the claim falls.

		The fallback for a drawable display that does not report a touched pin. A cell is
		3 by 5 pins at the 8 row pitch, so this is a much coarser answer than `pointForPin`,
		and the middle of the cell is the least wrong point in it. Used only when the display
		will not say better.

		:param segmentPos: the pressed cell, as a flat row major index into the claim.
		:param surface: the display, or None to find it again.
		:return: the source dot, or None if it cannot be worked out.
		"""
		if not self.active or self._rect is None or self._rect.numCols <= 0:
			return None
		if surface is None:
			surface = findSurface()
		if surface is None:
			return None
		pins = surface.pinRectForCells(self._rect)
		if pins.isEmpty:
			return None
		row, col = divmod(segmentPos, self._rect.numCols)
		centreX = pins.x + col * surface.pinsPerCol + surface.pinsPerCol // 2
		centreY = pins.y + row * surface.pinsPerRow + surface.pinsPerRow // 2
		return self.pointForPin(centreX, centreY, surface)

	def reportPress(self, segmentPos: int) -> None:
		"""Say what is under the finger, for a routing press inside the drawing.

		Called from `GraphicsRoutingPolicy`, which is to say from the reader pointing at the
		figure and pressing where they are pointing. That is the whole gesture: no second
		command, no second hand, and nothing to press while holding a position.

		:param segmentPos: the pressed cell, as a flat row major index into the claim.
		"""
		surface = findSurface()
		point = None
		if surface is not None:
			pin = surface.routingPin()
			if pin:
				point = self.pointForPin(pin[0], pin[1], surface)
			if point is None:
				# Either the display does not report a pin, or the one it reported was outside
				# the claim, which happens when a press was decided from the device's own cell
				# index instead. The cell is coarser and is still an answer.
				point = self.pointForCell(segmentPos, surface)
		_sayAfterThePress(self.describePoint(point, surface))

	def searchRadius(self, surface: Optional[GraphicsSurface] = None) -> int:
		"""How far from the reported pin to look for a dot, in source dots.

		A fingertip is far wider than a pin. Asking what is at the single pin the panel names
		makes a thin line almost unfindable: a border one dot wide, touched squarely, reports
		"blank" whenever the contact centre lands a pin to either side of it — which is most
		of the time, and was what the first hardware run of this found for every feature on the
		test figure.

		So the answer is the nearest raised dot within half a braille line, rounded up: 3 pins at
		the 8 row pitch and 2 at the 10 row pitch. Half a line because that is about how far the
		panel's idea of the contact centre can sit from the ridge a finger is actually on — a
		finger pad rests below the fingertip tracing a line, and the camera reports one point for
		the whole patch. Three deliberate touches on hardware missed by 1, 2 and 3 pins, which is
		what this number is set from; it is a starting point from a small sample rather than a
		calibration, and worth revisiting with more of them.

		Divided by the zoom, because magnification is exactly the act of making a source dot
		bigger than a finger: past about 3 times a single source dot is wider than a cell slot,
		the reader can place a finger on it, and searching would start answering about a dot they
		are not touching.

		:param surface: the display, or None to find it again.
		:return: the radius in source dots, possibly zero.
		"""
		if surface is None:
			surface = findSurface()
		if surface is None:
			return 0
		if self._rect is None:
			return 0
		pins = surface.pinRectForCells(self._rect)
		if pins.isEmpty:
			return 0
		# In source dots, so it grows as the drawing is compressed — one pin then stands for
		# several source dots and a finger covers more of the drawing, not less. A redrawn
		# figure is never compressed or magnified, whatever the zoom says: it is composed at
		# the panel's own size for a narrower window, so a dot is always a pin.
		scale = 1.0 if self._windows else self.scale(pins)
		return max(0, int(((surface.pinsPerRow + 1) // 2) / scale))

	def nearestDot(self, point: tuple, radius: int) -> Optional[tuple]:
		"""Find the raised dot closest to a point of the source.

		Searched outward a ring at a time, so the first hit is the closest and a tie goes to
		whichever the scan reaches first. Precision beyond that would be false: the point this
		is measuring from is itself the panel's guess at the middle of a contact patch.

		:param point: the source dot the press mapped to.
		:param radius: how far to look, in source dots. Zero asks about that dot alone.
		:return: the raised dot, or None if there is none within the radius.
		"""
		source = self._drawing.buffer
		x, y = point
		for ring in range(radius + 1):
			for dy in range(-ring, ring + 1):
				for dx in range(-ring, ring + 1):
					if max(abs(dx), abs(dy)) != ring:
						# Already covered by an inner ring.
						continue
					if source.getDot(x + dx, y + dy):
						return x + dx, y + dy
		return None

	def _meaningAt(self, point: tuple) -> Optional[str]:
		"""Ask the drawing what is at a point of it.

		Preferred over reporting a dot, because a drawing that knows what it is made of has
		a better answer than "raised at 26, 9": a chart can say which bar and what it is
		worth. Only a drawing built from structure has one — a picture does not — so this
		falls through to the dot when there is nothing to ask.

		A drawing's own answer is trusted about a *blank* point too, which is why this comes
		before the search: the space above a short bar still belongs to that bar, and naming
		it is more use than finding the nearest raised dot somewhere else.

		:param point: the source dot the press mapped to.
		:return: what is there, or None if the drawing does not say.
		"""
		describeAt = getattr(self._drawing, "describeAt", None)
		if describeAt is None:
			return None
		try:
			return describeAt(point[0], point[1])
		except Exception:
			log.error("BrlMultiline: a drawing could not say what is at a point", exc_info=True)
			return None

	def describePoint(self, point: Optional[tuple], surface: Optional[GraphicsSurface] = None) -> str:
		"""Say what the finger is on.

		Reports the nearest raised dot rather than the exact one pressed, for the reason
		`searchRadius` gives. The coordinates given are that dot's, which is what a reader
		following a line wants: "where is the line here", not "where exactly did I press".

		:param point: the source dot, or None if the press was not on the figure.
		:param surface: the display, or None to find it again.
		:return: what to speak.
		"""
		if point is None or not self.active:
			# Translators: reported for a press that did not land on the drawing.
			return _("Not on the drawing")
		meaning = self._meaningAt(point)
		if meaning:
			return meaning
		found = self.nearestDot(point, self.searchRadius(surface))
		if found is not None:
			# Translators: reported for a press on or beside a raised dot of a drawing. {x} and
			# {y} are the dot's position within the drawing, from its top left corner.
			return _("Raised at {x}, {y}").format(x=found[0], y=found[1])
		# Translators: reported for a press on a blank part of a drawing.
		return _("Blank at {x}, {y}").format(x=point[0], y=point[1])

	def describe(self) -> str:
		""":return: what is on the display, for speech."""
		if not self.active:
			return _("No drawing")
		name = self._drawing.name or _("drawing")
		# Translators: reports the figure on the display. {name} is what it is called, {zoom}
		# the magnification, {x} and {y} the top left corner of what is visible.
		if self._zoomStep == FIT:
			# Translators: reports a drawing shown whole, with no part of it off the display.
			# The placeholder is what the drawing is called.
			description = _("{name}, whole drawing").format(name=name)
		else:
			# Translators: reports a magnified drawing. Placeholders are what it is called, how
			# many times larger than the whole-drawing view it is, and where in it the visible
			# part sits.
			# One decimal place, for a zoom to one pin per point that fell between two rungs of
			# the ladder. "2.6041666 times" is a precision nobody can feel.
			description = _("{name}, {times} times, {position}").format(
				name=name,
				times=f"{round(ZOOM_FACTOR**self._zoomStep, 1):g}",
				position=self.positionWords(),
			)
		if self.note:
			description += ", " + self.note
		if not self._textLines:
			# Translators: added when a drawing has taken the whole display and there is no
			# braille line left beside it.
			description += ", " + _("full panel, no braille line")
		return description

	@property
	def note(self) -> str:
		""":return: anything the reader has to be told about the drawing itself.

		Almost always empty. It carries the one case where the panel cannot speak for itself:
		a window that is blank because the picture is blank there. Exposed separately from
		`describe` because panning reports only a position, and a reader panning across the
		empty middle of a shape needs to hear why they are feeling nothing every time, not
		only on the zoom that took them there.
		"""
		return getattr(self._drawing, "note", "") if self.active and self._drawing else ""

	# --- Lifecycle --------------------------------------------------------------------------

	def onRebuilt(self, keys: frozenset = frozenset()) -> None:
		"""The display was rebuilt and the claim survived, so draw again.

		The overlay itself lives on the driver and survives a rebuild — it is not made of
		cells — but the claim may have landed somewhere else, and the drawing has to follow it.
		A claim that no longer reaches the drawable display at all is treated as gone.

		:param keys: unused. A graphics panel has no segments; the claim is its own answer.
		"""
		if not self.active:
			return
		surface = findSurface()
		if surface is None or self._rect is None or surface.pinRectForCells(self._rect).isEmpty:
			self.onEvicted()
			return
		if self.render(surface):
			return
		# The figure will not draw for where the claim has landed. Falling back to the whole
		# figure is worth one try, since a window is the thing most likely to have become
		# impossible and fit is the one view every figure can always compose.
		self._zoomStep = FIT
		self._originX = 0
		self._originY = 0
		self._window = None
		if self.render(surface):
			return
		# It cannot be drawn at all here, and an overlay composed for somewhere else is worse
		# than no overlay: it is a picture of the wrong part of the wrong size, in a rectangle
		# the mode is now describing differently.
		log.debugWarning("BrlMultiline: a drawing could not be recomposed after a rebuild")
		self.onEvicted()

	def onEvicted(self, keys: frozenset = frozenset()) -> None:
		"""The claim is gone. Stop showing a figure over rows that belong to someone else.

		Reached when a display change leaves the claim unable to fit — a pitch change from 10
		rows to 8 is the everyday case, since it takes two lines off the display. The figure is
		dropped rather than resized: a drawing squeezed into a rectangle the reader did not
		choose is worse than one they are told has gone.

		:param keys: unused, as for `onRebuilt`.
		"""
		wasActive = self.active
		self._hide()
		if wasActive:
			self._notifyChanged()

	def _hide(self) -> None:
		surface = findSurface()
		if surface is not None:
			surface.hide(OVERLAY_KEY)
		self._drawing = None
		self._rect = None

	def onTerminate(self) -> None:
		"""The add-on is shutting down. Leave nothing on the hardware, and announce nothing."""
		self._hide()
