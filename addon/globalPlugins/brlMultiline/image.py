# BrlMultiline: a picture as a figure on the panel.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Joining captured pixels to the graphics mode.

`imageSource.py` gets the pixels, `imagePins.py` turns some of them into pins, and this is the
short piece between: it composes a `Drawing` for the panel and gives it a `redraw` so that
zooming reduces from the pixels again rather than magnifying what is already there.

A zoomed window is reduced from its own pixels, so the limit is the capture, stated in
`zoomPoints`. Pictures window vertically too, unlike charts; see
`graphicsMode.Drawing.windowsVertically`.
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

import addonHandler

addonHandler.initTranslation()

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

Outlines first, working on the widest range; single lines, one line per stroke; brightness,
for a picture the lines made nothing of; and reversed brightness, since which side is the
subject is a guess. No reversed single lines, since the ink is almost always the thin side.
One cycling key, since which will read cannot be known in advance.
"""


def styleName(mode: str, invert: bool = False) -> str:
	""":return: what to call one of the styles out loud.

	Said on every change. "Single lines", since "lines" after "outlines" sounds the same.

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

	One `ZOOM_FACTOR` step past a pixel a pin is allowed, so a shape can still be made bigger.
	Written against the mode's constants, so changing them moves this.

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

	NVDA's own translated names, from `colors.RGB`, else a hex value.

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

	Where the point is, as a percentage of the whole capture however far in the reader has
	zoomed, or that it is in the margin. With the colour kept, the colour there: the ink under
	the finger or the paper (see `imagePins.Picture.colourAt`), from the pin and one pin round
	it, kept as `touched` for "draw only this colour".

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

	So the advice to magnify matches the key. The mode's two limits, restated since a window
	is composed before the mode decides: the ladder's top step, and the points left at the
	next (`graphicsMode.GraphicsMode._tooFewPoints`).

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

	:param whole: whether this is the whole picture, which alone gets a `redraw`.
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

	With the style in it. See `styleName`.
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

	As fractions of the whole, as for a chart. See `chartDraw.windowOf`.

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
