# BrlMultiline: a picture as a figure on the panel.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Joining captured pixels to the graphics mode.

`imageSource.py` gets the pixels, `imagePins.py` turns some of them into pins, and this is the
short piece between: it composes a `Drawing` for the panel and gives it a `redraw` so that
zooming reduces from the pixels again rather than magnifying what is already there.

**A picture redraws itself, and the plan said it would not.** The reasoning there was that a
photograph is only its dots, so magnifying is all that can be done to it. That is true of a
picture *once it is on the pins* and false of one that is still pixels: a capture six hundred
wide reduced onto ninety-six pins threw away five sixths of what it knew, and a reader looking
at a quarter of it should get that quarter reduced from its own pixels instead. The limit is
the capture rather than the panel, and `zoomPoints` is where that limit is stated.

It is the first figure that windows vertically. A chart refits its value axis to whatever
periods are showing, so it has no up and down; the top half of a picture is the top half of a
picture. See `graphicsMode.Drawing.windowsVertically`.
"""

from typing import Optional

from logHandler import log

import math

from .graphicsMode import MAX_ZOOM_STEP, MIN_WINDOW_POINTS, ZOOM_FACTOR, Drawing
from .imagePins import (
	BRIGHTNESS,
	EDGES,
	STROKES,
	ImageRefused,
	Picture,
	Placement,
	place,
	renderAt,
	windowOf,
)

__all__ = [
	"LINES",
	"OUTLINES",
	"REVERSED",
	"SILHOUETTE",
	"STYLES",
	"colourName",
	"figureFor",
	"nextStyle",
	"styleName",
]


OUTLINES = (EDGES, False)
"""Where the picture changes. Every stroke as a pair of rails."""

LINES = (STROKES, False)
"""Every stroke of ink once, down its middle."""

SILHOUETTE = (BRIGHTNESS, False)
"""The subject raised, the background down."""

REVERSED = (BRIGHTNESS, True)
"""The background raised instead."""

STYLES = (OUTLINES, LINES, SILHOUETTE, REVERSED)
"""The ways of turning a picture into pins, in the order one key cycles them.

Outlines first because it is what works on the widest range of what readers meet — diagrams,
maps, logos, line art, and photographs as well as anything does. Single lines next, because on
the drawn part of that range it says the same thing more plainly: one line per stroke, where
outlines give two. Brightness third because it is what rescues a picture the lines made nothing
of, and reversed last because the guess about which side of a silhouette is the subject is a
guess: usually right, cheap to overturn, and impossible for anyone to check except by feeling
both.

**No reversed single lines.** The same guess is made for strokes, and it is a much safer one
there: a stroke is thin by being a stroke, so the ink is almost always the smaller class. A
fifth style on the cycling key would cost every reader a press on every picture for a case that
rarely comes up, and white-on-dark line art is still readable reversed as a silhouette.

**Four states on one key rather than a dialog.** None of these can be chosen in advance. The
same picture as outlines and as brightness are two entirely different panels, nobody can predict
which will read, and the whole cost of finding out is one keypress — so the answer is a key that
cycles rather than a question the reader has no way to answer.
"""


def styleName(mode: str, invert: bool = False) -> str:
	""":return: what to call one of the styles out loud.

	Said every time it changes, because it is the thing the reader has just altered and the one
	thing they cannot tell from the panel: the picture under their hand looks different, and
	without being told why they would have to work out which of four it had become.

	**"Single lines", not "lines".** Heard straight after "outlines", "lines" is the same word
	with its first syllable lost, and a reader cycling quickly would hear no change at all.

	:param mode: from `imagePins`.
	:param invert: whether the silhouette is reversed.
	"""
	if mode == STROKES:
		# Translators: a way of drawing a picture on a braille display: each stroke of ink as
		# one line down its middle, where outlines would give two.
		return _("single lines")
	if mode == BRIGHTNESS and invert:
		# Translators: a way of drawing a picture: its background raised instead of its subject.
		return _("brightness reversed")
	if mode == BRIGHTNESS:
		# Translators: a way of drawing a picture on a braille display: light against dark.
		return _("brightness")
	# Translators: a way of drawing a picture on a braille display: its outlines.
	return _("outlines")


def nextStyle(mode: str, invert: bool = False) -> tuple:
	""":return: the style after this one, wrapping.

	:param mode: the one in force.
	:param invert: whether it is reversed.
	"""
	try:
		return STYLES[(STYLES.index((mode, invert)) + 1) % len(STYLES)]
	except ValueError:
		return STYLES[0]


def zoomPoints(picture: Picture, width: int, height: int, box: "tuple | None" = None) -> int:
	"""How much there is to zoom into, in the vocabulary the graphics mode already has.

	The mode refuses a zoom that would leave fewer than `MIN_WINDOW_POINTS` of whatever the
	figure is made of -- for a chart, periods. A picture is made of captured pixels, and the
	natural place to stop is where a pin stands on a single pixel, since past that there is no
	more detail in the file to find.

	**One step is allowed past it anyway**, which is what the `ZOOM_FACTOR` here is. Stopping
	exactly at a pixel a pin is right about detail and wrong about hands: a pin is a small
	thing to read a shape with, and a reader asking to magnify a toolbar that has run out of
	pixels is asking for the shape to get bigger, not for new detail to appear. Half a pixel a
	pin gives them that and costs nothing true -- the picture is no longer gaining information,
	but it was not losing any either. Two steps past would be feeling the reduction rather than
	the picture, so it is one.

	It is arithmetic against the mode's own constants rather than a number chosen to feel
	right, which is why it is written as one: a change to `MIN_WINDOW_POINTS` or to
	`ZOOM_FACTOR` should move this and not silently mean something else.

	:param picture: the capture.
	:param width: pins across the panel.
	:param height: pins down.
	:param box: the part of the capture the whole figure covers, which is what a zoom narrows.
		The whole capture if None.
	:return: the count, at least `MIN_WINDOW_POINTS` so a picture is never refused its first
		zoom merely for having been captured small.
	"""
	if width <= 0 or height <= 0:
		return MIN_WINDOW_POINTS
	_left, _top, across, down = box if box is not None else (0, 0, picture.width, picture.height)
	perPin = max(across / width, down / height)
	return max(MIN_WINDOW_POINTS, int(MIN_WINDOW_POINTS * perPin * ZOOM_FACTOR))


def colourName(rgb: tuple) -> str:
	""":return: what to call a colour out loud.

	NVDA's own names, from `colors.RGB`, which are what it already says for the colour of text:
	translated, and the same words a reader has heard for years. A hex value if they cannot be
	had, which is ugly and still true.

	:param rgb: red, green and blue.
	"""
	try:
		import colors

		return colors.RGB(*rgb).name
	except Exception:
		log.debugWarning("BrlMultiline: NVDA would not name a colour", exc_info=True)
		return "#{:02x}{:02x}{:02x}".format(*rgb)


def _original(picture: Picture) -> Picture:
	""":return: the captured picture a picture of one colour was made from, or the picture."""
	while picture.derivedFrom is not None:
		picture = picture.derivedFrom
	return picture


def _describer(picture: Picture, spot: Placement):
	"""Build what a routing press on the picture answers with.

	A picture cannot say what is at a point the way a bar chart can -- that is the machinery
	that comes later -- but it can say *where* the point is, which is more than nothing and is
	the thing a reader loses first when both hands are on a panel with no edges to count from.

	**Where in the picture, not where on the panel.** The pin is put back through the
	placement it was drawn from, so the answer is a percentage of the whole capture however
	far in the reader has zoomed. Read off the panel instead -- which is what an earlier
	version did, since it took a box and never used it -- the left edge says nought across
	while the hand is three quarters of the way along the picture, and the number is wrong
	exactly when the reader has most need of it: they zoomed in because they had lost the
	place.

	**A press in the margin says so.** A square picture on a panel over twice as wide has
	margin on a third of it, and there is nothing there. Reporting the nearest edge as though
	it were the picture would be a fact about the letterbox, so the margin is named instead.

	**And it names the colour there**, when the capture kept its colour: the ink under the
	finger if there is any, the paper if not. See `imagePins.Picture.colourAt` for why that is
	not simply the average. The pixels looked at are the pin pressed and one pin round it,
	because a routing press is a fingertip's guess and a line one pin away is what it was
	aimed at. The colour is also kept on the captured picture, as `touched`, so that "draw only
	this colour" knows which one was meant.

	:param picture: the capture, for its size.
	:param spot: the part of it being shown and where that part sits on the panel.
	:return: a function taking panel coordinates and returning a phrase.
	"""
	left, top, across, down = spot.source

	def describeAt(x: int, y: int) -> Optional[str]:
		if spot.width <= 0 or spot.height <= 0 or not picture.width or not picture.height:
			return None
		insideX = x - spot.left
		insideY = y - spot.top
		if not (0 <= insideX < spot.width and 0 <= insideY < spot.height):
			# Translators: reported when a routing press lands beside a drawn picture rather
			# than on it, which happens where the picture does not fill the panel.
			return _("outside the picture")
		# The middle of the pin rather than its corner: a pin covers a span of the picture,
		# and its corner is the boundary between it and the one before it.
		atX = left + (insideX + 0.5) * across / spot.width
		atY = top + (insideY + 0.5) * down / spot.height
		# Translators: where a finger is on a drawn picture. Placeholders are percentages
		# across from the left and down from the top.
		where = _("{across} across, {down} down").format(
			across=round(atX / picture.width * 100),
			down=round(atY / picture.height * 100),
		)
		pinWidth = across / spot.width
		pinHeight = down / spot.height
		colour = picture.colourAt(
			(
				int(left + (insideX - 1) * pinWidth),
				int(top + (insideY - 1) * pinHeight),
				max(1, round(3 * pinWidth)),
				max(1, round(3 * pinHeight)),
			),
		)
		if colour is None:
			return where
		_original(picture).touched = colour
		# Translators: a colour and where a finger is on a drawn picture, as in "dark red, 40
		# across, 60 down". Placeholders are the colour's name and the position.
		return _("{colour}, {where}").format(colour=colourName(colour), where=where)

	return describeAt


def figureFor(
	newBuffer,
	picture: Picture,
	width: int,
	height: int,
	mode: str = EDGES,
	invert: bool = False,
) -> Drawing:
	"""Compose a captured picture for a rectangle of pins.

	:param newBuffer: makes a blank buffer of a given size.
	:param picture: the capture.
	:param width: pins across.
	:param height: pins down.
	:param mode: `EDGES`, `STROKES` or `BRIGHTNESS`.
	:param invert: swap which side of a silhouette is raised.
	:return: the figure.
	:raises ImageRefused: if there is nothing here to draw.
	"""
	# Its content rather than the whole capture, so blank margins do not take pins from what
	# is in it. See `imagePins.Picture.content`.
	box = picture.content
	return _compose(
		newBuffer,
		picture,
		box,
		width,
		height,
		mode,
		invert,
		whole=True,
		canMagnify=canMagnify(zoomPoints(picture, width, height, box), 1.0),
	)


def canMagnify(points: int, fraction: float) -> bool:
	""":return: whether the graphics mode will allow one more zoom in from here.

	**The advice has to match the key.** A picture too dense for the pins says so and says to
	magnify, and that was said unconditionally -- so a reader zoomed in as far as the capture
	allowed heard "magnify to read it" and, pressing the key, "no more detail to show". Both
	true, and together a contradiction: the second is the one that decides, so the first must
	know it. Found on a logo whose lettering was too small for the pins at every zoom the
	capture could support.

	The same two limits the mode applies, restated here rather than asked of it, since a window
	is composed before the mode knows whether it will keep it: the ladder's top step, and the
	points left at the next step (`graphicsMode.GraphicsMode._tooFewPoints`).

	:param points: the whole figure's zoom points, from `zoomPoints`.
	:param fraction: how much of the whole the view shows across its narrower axis, 1 for all.
	"""
	if fraction <= 0:
		return False
	step = round(math.log(1 / fraction, ZOOM_FACTOR))
	return step < MAX_ZOOM_STEP and points / (ZOOM_FACTOR ** (step + 1)) >= MIN_WINDOW_POINTS


def _compose(
	newBuffer,
	picture,
	box,
	width,
	height,
	mode,
	invert,
	whole: bool,
	canMagnify: bool = True,
) -> Drawing:
	"""Draw one rectangle of a picture at one size.

	:param whole: whether this is the whole picture, which is what the name says and what
		decides whether a `redraw` is attached — a window's own drawing is never asked to
		window again, and attaching one would let a stale box be applied twice.
	:param canMagnify: whether a zoom in from this view is possible, which decides what a
		picture too detailed for the pins advises. See `canMagnify`.
	:return: the figure.
	:raises ImageRefused: if there is nothing in this part of the picture.
	"""
	spot = place(picture, box, width, height)
	rendering = renderAt(picture, spot, mode, invert, whole)
	buffer = newBuffer(width, height)
	if buffer is None:
		# Translators: reported when the display would not give a surface to draw on.
		raise ImageRefused(_("The display would not provide a drawing surface"))
	rendering.draw(buffer)
	name = _nameOf(picture, mode, invert, whole)
	note = ""
	if rendering.barren:
		# Translators: said of a magnified part of a picture that has nothing in it, so that a
		# reader feeling an empty panel knows the picture is blank here rather than that the
		# display has stopped working. They can keep panning to reach the part that is not.
		note = _("only background here")
	elif rendering.crowded and canMagnify:
		# Translators: said when a picture holds more detail than the pins can carry, so what
		# is drawn is an even texture rather than its lines. Magnifying shows less of the
		# picture at a time and lets its lines come apart from each other.
		note = _("too detailed to draw whole; magnify to read it")
	elif rendering.crowded and picture.fromScreen:
		# Translators: said when a picture copied off the screen holds more detail than the
		# pins can carry and it is already magnified as far as the captured pixels allow, so
		# magnifying cannot help. Copying the image and drawing it from the clipboard gets it
		# at its own size, which is often larger than it was shown on screen.
		note = _("too detailed for the pins at this size; try drawing it from the clipboard")
	elif rendering.crowded:
		# Translators: said when a picture holds more detail than the pins can carry and it is
		# already magnified as far as its pixels allow, so magnifying cannot help.
		note = _("too detailed for the pins, even at the closest zoom")
	if not whole:
		return Drawing(buffer, name=name, describeAt=_describer(picture, spot), note=note)
	return Drawing(
		buffer,
		name=name,
		describeAt=_describer(picture, spot),
		redraw=_reframer(newBuffer, picture, box, mode, invert),
		points=zoomPoints(picture, width, height, box),
		windowsVertically=True,
		note=note,
	)


def _nameOf(picture: Picture, mode: str, invert: bool, whole: bool) -> str:
	""":return: what to call this on the display and in speech.

	The mode is in the name because it is the thing the reader changes and the thing they
	cannot otherwise tell: the same picture as outlines and as brightness are two quite
	different panels, and a reader who has just pressed the key needs to hear which one
	arrived.
	"""
	said = picture.name or _("picture")
	if picture.onlyColour is not None:
		# Translators: what a picture is called when only one of its colours is drawn. The
		# placeholders are the picture's name and the colour, as in "map, only blue".
		said = _("{name}, only {colour}").format(name=said, colour=colourName(picture.onlyColour))
	if whole:
		# Translators: what a drawn picture is called. Placeholders are what the picture is
		# of and how it was drawn, one of "outlines", "single lines", "brightness" or
		# "brightness reversed".
		return _("{name}, {mode}").format(name=said, mode=styleName(mode, invert))
	# Translators: what part of a drawn picture is called when the reader has zoomed into it.
	return _("{name}, {mode}, part").format(name=said, mode=styleName(mode, invert))


def _reframer(newBuffer, picture: Picture, box, mode: str, invert: bool):
	"""Build the closure that draws this picture again for part of itself.

	The window arrives as fractions of the whole, which is what keeps the graphics mode from
	learning that a picture is pixels — the same arrangement a chart has, where the mode does
	not learn that the data is periods.

	:param newBuffer: makes a blank buffer.
	:param picture: the capture.
	:param box: the part of it the whole figure covers, which the window narrows.
	:param mode: how it is being drawn.
	:param invert: whether the silhouette is inverted.
	:return: the function the mode calls.
	"""

	def redraw(
		offset: float,
		span: float,
		pinWidth: int,
		pinHeight: int,
		top: float = 0.0,
		down: float = 1.0,
	) -> Optional[Drawing]:
		narrowed = windowOf(picture, box, offset, top, span, down)
		try:
			return _compose(
				newBuffer,
				picture,
				narrowed,
				pinWidth,
				pinHeight,
				mode,
				invert,
				whole=False,
				canMagnify=canMagnify(zoomPoints(picture, pinWidth, pinHeight, box), min(span, down)),
			)
		except ImageRefused as refusal:
			# The mode keeps the view the reader already had when a window will not compose,
			# which is the right answer: a blank panel would say the picture had gone.
			log.debug(f"BrlMultiline: a window of a picture would not draw: {refusal}")
			return None

	return redraw
