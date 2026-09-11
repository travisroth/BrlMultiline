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

from .graphicsMode import MIN_WINDOW_POINTS, Drawing
from .imagePins import (
	BRIGHTNESS,
	EDGES,
	ImageRefused,
	Picture,
	Placement,
	place,
	renderAt,
	windowOf,
)

__all__ = [
	"STYLES",
	"figureFor",
	"nextStyle",
	"styleName",
]


STYLES = (
	(EDGES, False),
	(BRIGHTNESS, False),
	(BRIGHTNESS, True),
)
"""The ways of turning a picture into pins, in the order one key cycles them.

Outlines first because it is what works on what readers actually meet — diagrams, maps, logos,
line art. Brightness second because it is what rescues a picture the outlines made nothing of,
and reversed third because the guess about which side of a silhouette is the subject is a guess:
usually right, cheap to overturn, and impossible for anyone to check except by feeling both.

**Three states on one key rather than a dialog.** None of these can be chosen in advance. The
same picture as outlines and as brightness are two entirely different panels, nobody can predict
which will read, and the whole cost of finding out is one keypress — so the answer is a key that
cycles rather than a question the reader has no way to answer.
"""


def styleName(mode: str, invert: bool = False) -> str:
	""":return: what to call one of the styles out loud.

	Said every time it changes, because it is the thing the reader has just altered and the one
	thing they cannot tell from the panel: the picture under their hand looks different, and
	without being told why they would have to work out which of three it had become.

	:param mode: from `imagePins`.
	:param invert: whether the silhouette is reversed.
	"""
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


def zoomPoints(picture: Picture, width: int, height: int) -> int:
	"""How much there is to zoom into, in the vocabulary the graphics mode already has.

	The mode refuses a zoom that would leave fewer than `MIN_WINDOW_POINTS` of whatever the
	figure is made of — for a chart, periods. A picture is made of captured pixels, and the
	point past which zooming stops being worth anything is the one where a pin is standing on a
	single pixel: after that the reader is feeling the magnification rather than the picture,
	which is the thing keeping the pixels was meant to avoid.

	So the count is scaled to put the refusal exactly there. It is arithmetic against the
	mode's constant rather than a number chosen to feel right, which is why it is written as
	one: a change to `MIN_WINDOW_POINTS` should move this and not silently mean something else.

	:param picture: the capture.
	:param width: pins across the panel.
	:param height: pins down.
	:return: the count, at least `MIN_WINDOW_POINTS` so a picture is never refused its first
		zoom merely for having been captured small.
	"""
	if width <= 0 or height <= 0:
		return MIN_WINDOW_POINTS
	perPin = max(picture.width / width, picture.height / height)
	return max(MIN_WINDOW_POINTS, int(MIN_WINDOW_POINTS * perPin))


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
		return _("{across} across, {down} down").format(
			across=round(atX / picture.width * 100),
			down=round(atY / picture.height * 100),
		)

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
	:param mode: `EDGES` or `BRIGHTNESS`.
	:param invert: swap which side of a silhouette is raised.
	:return: the figure.
	:raises ImageRefused: if there is nothing here to draw.
	"""
	box = (0, 0, picture.width, picture.height)
	return _compose(newBuffer, picture, box, width, height, mode, invert, whole=True)


def _compose(newBuffer, picture, box, width, height, mode, invert, whole: bool) -> Drawing:
	"""Draw one rectangle of a picture at one size.

	:param whole: whether this is the whole picture, which is what the name says and what
		decides whether a `redraw` is attached — a window's own drawing is never asked to
		window again, and attaching one would let a stale box be applied twice.
	:return: the figure.
	:raises ImageRefused: if there is nothing in this part of the picture.
	"""
	spot = place(picture, box, width, height)
	rendering = renderAt(picture, spot, mode, invert)
	buffer = newBuffer(width, height)
	if buffer is None:
		# Translators: reported when the display would not give a surface to draw on.
		raise ImageRefused(_("The display would not provide a drawing surface"))
	rendering.draw(buffer)
	name = _nameOf(picture, mode, invert, whole)
	if not whole:
		return Drawing(buffer, name=name, describeAt=_describer(picture, spot))
	return Drawing(
		buffer,
		name=name,
		describeAt=_describer(picture, spot),
		redraw=_reframer(newBuffer, picture, box, mode, invert),
		points=zoomPoints(picture, width, height),
		windowsVertically=True,
	)


def _nameOf(picture: Picture, mode: str, invert: bool, whole: bool) -> str:
	""":return: what to call this on the display and in speech.

	The mode is in the name because it is the thing the reader changes and the thing they
	cannot otherwise tell: the same picture as outlines and as brightness are two quite
	different panels, and a reader who has just pressed the key needs to hear which one
	arrived.
	"""
	said = picture.name or _("picture")
	if whole:
		# Translators: what a drawn picture is called. Placeholders are what the picture is
		# of and how it was drawn, one of "outlines", "brightness" or "brightness reversed".
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
			)
		except ImageRefused as refusal:
			# The mode keeps the view the reader already had when a window will not compose,
			# which is the right answer: a blank panel would say the picture had gone.
			log.debug(f"BrlMultiline: a window of a picture would not draw: {refusal}")
			return None

	return redraw
