# BrlMultiline: getting a picture off the screen.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The NVDA half of image import: which object, and its pixels.

The only thing in the image path that knows about NVDA, and the counterpart of
`chartSource.py` — one module that reads the world, so that `imagePins.py` can be arithmetic
over a list of numbers and nothing else.

**The picture is the one NVDA just found.** A reader arrowing a web page hears "graphic", and
that is the moment this has to work in. So the source is the navigator object, which is what
every other capture in NVDA uses and which, in browse mode, *is* the graphic: arrowing sets the
review position, setting it clears the navigator object, and the next read of the navigator
object comes back from the review position as the object under the caret. Nothing has to be
aimed and nothing new has to be learned.

**Not restricted to things NVDA calls a graphic.** A diagram in a canvas, a map, a floor plan,
a chart somebody published as a picture — half of them report a role that says nothing useful,
and a reader who has pointed at something and asked for it to be drawn has said what they want
more clearly than a role ever will. What is refused is only what cannot work: no location, a
location clipped to nothing, and something too small on screen to be a picture at all.
"""

from typing import Optional

import api
from logHandler import log

from .imagePins import ImageRefused, Picture, greysFromPixels

__all__ = [
	"MIN_SIDE",
	"captureNavigator",
	"nameFor",
]


MIN_SIDE = 16
"""How many screen pixels across and down something must be before it is worth drawing.

A sixteen pixel square reduced onto a ninety-six by forty panel is six pins to a side, which is
a smudge rather than a picture. Below this the honest answer is that the reader has pointed at
something that is not a picture — a bullet, a spacer, a one pixel tracking image — and saying
so is better than drawing them a blur they then have to interpret.
"""

MAX_PIXELS = 360000
"""How many pixels are worth keeping, whatever the thing's size on screen.

Six hundred square. The capture is held so that zooming can reduce from pixels rather than
magnify pins, and past about eight pixels to a pin there is nothing left for zoom to reach —
the panel runs out first. Capturing a four megapixel image would cost the reader a pause for
detail no finger will ever arrive at.
"""


def _screenCurtainIsUp() -> bool:
	""":return: whether NVDA is blanking the screen.

	Worth its own check because the failure is otherwise invisible in the wrong way: a GDI
	capture under the screen curtain succeeds and comes back uniformly black, which reaches the
	reader as "there is nothing in this picture to draw" — true of the capture, and quite wrong
	as an account of what happened.
	"""
	try:
		from screenCurtain import screenCurtain

		return screenCurtain is not None and screenCurtain.enabled
	except Exception:
		# An NVDA without it, or one that has not started it. Either way it is not up.
		return False


def _captureSize(width: int, height: int) -> tuple:
	"""Choose how many pixels to keep for something of this size on screen.

	Never enlarged. Upscaling a small image before reducing it adds no detail and costs the
	reduction real time, and the pixels it invents are the ones a reader would then be feeling.

	:param width: its width on screen.
	:param height: its height on screen.
	:return: the capture size.
	"""
	if width * height <= MAX_PIXELS:
		return width, height
	# Shrunk on both axes by the same factor, because the shape has to survive: a picture
	# squeezed on one axis is a picture of something else, and nothing downstream could know.
	scale = (MAX_PIXELS / (width * height)) ** 0.5
	return max(1, int(width * scale)), max(1, int(height * scale))


def nameFor(obj) -> str:
	"""What to call what is on the display.

	The object's own words first — alt text on a web graphic, a file name in a picture
	viewer — because that is what the reader was just told and what they will recognise. The
	role is a fallback rather than a prefix: "graphic, graphic" says nothing twice.

	:param obj: the object being drawn.
	:return: a short phrase, never empty.
	"""
	for attribute in ("name", "description"):
		said = getattr(obj, attribute, None)
		if said and str(said).strip():
			return str(said).strip()
	try:
		import controlTypes

		role = obj.role
		said = controlTypes.Role(role).displayString
		if said:
			return said
	except Exception:
		log.debug("BrlMultiline: an object being drawn would not say what it is", exc_info=True)
	# Translators: what an imported picture is called when nothing would say what it is of.
	return _("picture")


def captureNavigator() -> Picture:
	"""Take a picture of whatever the reader is pointing at.

	:return: the pixels, greyscale, at or below `MAX_PIXELS`.
	:raises ImageRefused: with a reason the reader can act on.
	"""
	if _screenCurtainIsUp():
		# Translators: reported when a picture was asked for while the screen curtain is on.
		raise ImageRefused(_("Turn the screen curtain off to draw a picture"))
	try:
		obj = api.getNavigatorObject()
	except Exception:
		log.error("BrlMultiline: could not find the object to draw", exc_info=True)
		obj = None
	if obj is None:
		# Translators: reported when a picture was asked for and there is nothing to draw.
		raise ImageRefused(_("There is nothing here to draw"))
	location = getattr(obj, "location", None)
	try:
		left, top, width, height = location
	except TypeError:
		log.debugWarning(f"BrlMultiline: object to draw returned location {location!r}")
		# Translators: reported when a picture was asked for over something not on the screen.
		raise ImageRefused(_("This is not showing on the screen"))
	if width < MIN_SIDE or height < MIN_SIDE:
		raise ImageRefused(
			# Translators: reported when a picture is too small on screen to draw. The
			# placeholders are its width and height in screen pixels.
			_("This is only {width} by {height} on screen, too small to draw").format(
				width=int(width),
				height=int(height),
			),
		)
	return capture(left, top, width, height, name=nameFor(obj))


def capture(left: int, top: int, width: int, height: int, name: str = "") -> Picture:
	"""Copy a rectangle of the screen and keep it as brightnesses.

	Split out from `captureNavigator` so that the finding and the copying can be tested apart,
	and so that a later caller with a rectangle of its own — an image file, a region the reader
	drew — has somewhere to arrive.

	:param left: screen x of the top left corner.
	:param top: screen y.
	:param width: how wide on screen.
	:param height: how tall.
	:param name: what to call it.
	:return: the pixels.
	:raises ImageRefused: if the screen would not give them up.
	"""
	keepWidth, keepHeight = _captureSize(int(width), int(height))
	try:
		import screenBitmap

		grabber = screenBitmap.ScreenBitmap(keepWidth, keepHeight)
		pixels = grabber.captureImage(int(left), int(top), int(width), int(height))
	except Exception:
		log.error("BrlMultiline: the screen would not be captured", exc_info=True)
		# Translators: reported when copying part of the screen failed.
		raise ImageRefused(_("This could not be copied off the screen"))
	return Picture(greysFromPixels(pixels, keepWidth, keepHeight), keepWidth, keepHeight, name)


def sizeWords(picture: Optional[Picture]) -> str:
	""":return: how big the captured picture is, for a reader asking what they have.

	:param picture: what was captured, or None.
	"""
	if picture is None:
		return ""
	# Translators: the size of a captured picture. Placeholders are pixels across and down.
	return _("{width} by {height}").format(width=picture.width, height=picture.height)
