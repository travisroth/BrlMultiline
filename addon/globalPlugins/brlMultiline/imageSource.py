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
location with nothing of it on a monitor, an object that says it is not in view, and something
too small on screen to be a picture at all.

**Occlusion is the limit this cannot reach.** A window sitting over the thing being captured is
copied instead of it, because a screen grab is a grab of the screen and there is nothing else
to ask. Nothing here can detect it; it is written down rather than worked around.
"""

import os
from typing import Optional

import api
from logHandler import log

from .imagePins import (
	ImageRefused,
	Picture,
	greysFromPixels,
	hasPillow,
	pictureFromBgrx,
	pictureFromImage,
)

__all__ = [
	"MIN_SIDE",
	"captureClipboard",
	"captureNavigator",
	"keepPixels",
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
"""How many pixels are worth keeping without Pillow, whatever the thing's size on screen.

Six hundred square. The capture is held so that zooming can reduce from pixels rather than
magnify pins, and every doubling of the pixels kept is one more doubling of zoom before a pin
stands on a single pixel. The limit is time, not usefulness: this is about what Python reduces
without a pause a reader would notice, which is why `PILLOW_PIXELS` is larger.
"""

PILLOW_PIXELS = 1440000
"""How many pixels are worth keeping when Pillow does the work: twelve hundred square.

Four times `MAX_PIXELS`, which is one more doubling of zoom before a pin stands on a single
pixel. With Pillow a reduction to the panel takes well under a millisecond. The dearest style
is single lines, whose ink reduction took about 35 milliseconds for a whole picture this size
on the development machine, and under 50 for the whole drawing; a zoomed window is smaller and
quicker. Worth most for an image from the clipboard or a file, which is often far larger than
anything on the screen.
"""

GRAB_PIXELS = 2000000
"""How many pixels to copy off the screen when Pillow will shrink them to `PILLOW_PIXELS`.

What is kept is `PILLOW_PIXELS`. This is how much is copied so that the shrink to it can be
done properly: Windows can shrink while it copies, but in the mode NVDA's `ScreenBitmap` leaves
it in, the pixels it drops are combined by a bitwise AND of their colours -- see
`imagePins.pictureFromBgrx`. Two megapixels is a full HD screen, so nearly everything is copied
at its own size and averaged down, and something larger than that is shrunk by Windows only as
far as two megapixels. About eight megabytes of capture for a moment.
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


def _visibleScreen() -> "tuple | None":
	""":return: the whole desktop across every monitor, or None if Windows will not say.

	The virtual screen rather than the primary monitor, because a reader with two of them has
	windows on the second one and those are as real as anything else.

	None rather than a guess when the metrics cannot be read: not clipping is the safer
	failure. A capture that should have been refused gives a picture that is wrong; a clip
	against a wrong rectangle refuses pictures that would have worked.
	"""
	try:
		from winAPI.winUser.constants import SystemMetrics
		from winBindings import user32

		bounds = (
			user32.GetSystemMetrics(SystemMetrics.X_VIRTUAL_SCREEN),
			user32.GetSystemMetrics(SystemMetrics.Y_VIRTUAL_SCREEN),
			user32.GetSystemMetrics(SystemMetrics.CX_VIRTUAL_SCREEN),
			user32.GetSystemMetrics(SystemMetrics.CY_VIRTUAL_SCREEN),
		)
	except Exception:
		log.debugWarning("BrlMultiline: could not measure the screen to clip a capture to", exc_info=True)
		return None
	return bounds if bounds[2] > 0 and bounds[3] > 0 else None


def _monitors() -> "list | None":
	""":return: every monitor rectangle, or None if Windows will not enumerate them.

	The virtual screen is a bounding box, and a bounding box of two monitors that are not
	aligned contains ground that belongs to neither: put one screen above and right of the
	other and the two corners between them are inside the box and on nothing. A window cannot
	be there, but a stale or wrong object rectangle can, and clipping to the box alone said it
	was visible.
	"""
	try:
		import ctypes
		from ctypes import wintypes

		try:
			from winBindings import user32
		except ImportError:
			user32 = ctypes.windll.user32
		found = []

		def collect(_monitor, _context, rect, _data):
			bounds = rect.contents
			found.append(
				(
					bounds.left,
					bounds.top,
					bounds.right - bounds.left,
					bounds.bottom - bounds.top,
				),
			)
			return 1

		callback = ctypes.WINFUNCTYPE(
			ctypes.c_int,
			ctypes.c_void_p,
			ctypes.c_void_p,
			ctypes.POINTER(wintypes.RECT),
			ctypes.c_void_p,
		)(collect)
		user32.EnumDisplayMonitors(None, None, callback, 0)
	except Exception:
		log.debugWarning("BrlMultiline: could not enumerate the monitors", exc_info=True)
		return None
	return [rect for rect in found if rect[2] > 0 and rect[3] > 0] or None


def _overlap(first, second) -> "tuple | None":
	""":return: the rectangle common to two, or None if they do not meet."""
	left = max(first[0], second[0])
	top = max(first[1], second[1])
	right = min(first[0] + first[2], second[0] + second[2])
	bottom = min(first[1] + first[3], second[1] + second[3])
	if right <= left or bottom <= top:
		return None
	return left, top, right - left, bottom - top


def _onScreen(left: int, top: int, width: int, height: int) -> "tuple | None":
	"""Cut a rectangle down to the part of it that is actually on a monitor.

	**An object's location is where it would be, not where it can be seen.** A graphic scrolled
	off the bottom of a page keeps a perfectly ordinary rectangle with a y coordinate past the
	end of the screen, and copying that rectangle succeeds: it comes back as whatever the
	graphics card has there, which is black, or the desktop, or another window. The reader is
	then handed a picture that is not of the thing they pointed at, drawn as confidently as a
	real one — and the size check alone will not catch it, since a large off-screen object is
	still large.

	A partly visible object is clipped to its visible part rather than refused. That is a
	picture of part of the thing, which is what the reader can see and is worth having; it is
	also why the size check below runs on what came back from here.

	**The virtual screen is a bounding box and not a shape.** Two monitors that are not
	aligned leave ground inside the box that belongs to neither, and a rectangle sitting there
	passed a clip against the box while being on nothing at all. So the real monitors are
	enumerated and the rectangle is measured against them; it keeps its full clipped extent
	only when every pixel of it lands on some monitor.

	**Occlusion is not solved by this and cannot be.** A window sitting over the object is
	copied instead of it, because a screen grab is a grab of the screen. Documented rather than
	worked around.

	:return: the visible rectangle, or None if none of it is on a monitor.
	"""
	screen = _visibleScreen()
	if screen is None:
		return left, top, width, height
	clipped = _overlap((left, top, width, height), screen)
	if clipped is None:
		return None
	monitors = _monitors()
	if not monitors:
		return clipped
	# Monitors never overlap each other on the virtual desktop, so the parts of the rectangle
	# that fall on one add up. Adding to the whole of it means every pixel is on some monitor,
	# gaps included in the answer only if there are none.
	parts = [found for found in (_overlap(clipped, monitor) for monitor in monitors) if found]
	if not parts:
		return None
	covered = sum(part[2] * part[3] for part in parts)
	if covered >= clipped[2] * clipped[3]:
		return clipped
	# Some of it is over a gap between monitors, or off the end of one. The largest piece that
	# is genuinely on a single screen is the honest answer: stitching the pieces back into one
	# rectangle would put the gap back in, which is the thing being avoided.
	return max(parts, key=lambda part: part[2] * part[3])


def _isOffScreen(obj) -> bool:
	""":return: whether the object itself says it is not being shown.

	Asked as well as the rectangle being clipped, because the two catch different things. A
	control scrolled out of a pane can keep a location that is still over the window it is in,
	so the clip finds nothing wrong and the capture comes back as whatever is drawn there
	instead — the rows that did scroll into view.
	"""
	try:
		import controlTypes

		return controlTypes.State.OFFSCREEN in obj.states
	except Exception:
		# An object that will not say is treated as showing. Refusing on a question nobody
		# answered would refuse the ordinary case.
		return False


def keepPixels() -> int:
	""":return: how many pixels a picture may keep, which depends on whether Pillow is here."""
	return PILLOW_PIXELS if hasPillow() else MAX_PIXELS


def _captureSize(width: int, height: int, most: "int | None" = None) -> "tuple[int, int]":
	"""Choose how many pixels to keep for something of this size on screen.

	Never enlarged. Upscaling a small image before reducing it adds no detail and costs the
	reduction real time, and the pixels it invents are the ones a reader would then be feeling.

	:param width: its width on screen.
	:param height: its height on screen.
	:param most: the most pixels to allow, `keepPixels` if None.
	:return: the capture size.
	"""
	if most is None:
		most = keepPixels()
	if width * height <= most:
		return width, height
	# Shrunk on both axes by the same factor, because the shape has to survive: a picture
	# squeezed on one axis is a picture of something else, and nothing downstream could know.
	scale = (most / (width * height)) ** 0.5
	return max(1, int(width * scale)), max(1, int(height * scale))


def _rawBytes(pixels: object, width: int, height: int) -> "bytes | None":
	""":return: a capture's pixels as bytes, four to a pixel, or None if it is not that shape.

	`ScreenBitmap.captureImage` returns a ctypes array of `RGBQUAD`, which is exactly the bytes
	Pillow wants, in one piece of memory. Anything else -- a test's rows of pixel objects, or a
	future NVDA that returns something different -- is None, and goes the Python way.
	"""
	try:
		import ctypes

		size = ctypes.sizeof(pixels)
		if size != width * height * 4:
			return None
		return ctypes.string_at(ctypes.addressof(pixels), size)
	except TypeError:
		return None


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

	:return: the pixels, at or below `keepPixels`.
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
	if _isOffScreen(obj):
		# Translators: reported when a picture was asked for over something scrolled out of
		# view, which would otherwise be copied as whatever is drawn in its place.
		raise ImageRefused(_("This is not in view; scroll to it and try again"))
	visible = _onScreen(int(left), int(top), int(width), int(height))
	if visible is None:
		# Translators: reported when a picture was asked for over something not on the screen.
		raise ImageRefused(_("This is not showing on the screen"))
	left, top, width, height = visible
	# Measured after the clip, so that a large object with a few pixels showing is refused as
	# what it is rather than passed as what it would be if it were all there.
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


def captureClipboard() -> Picture:
	"""Take a picture from the clipboard: an image copied, or an image file copied.

	**Nothing here looks at the screen**, which is the point of it. The screen capture cannot
	work under the screen curtain, copies whatever window is on top of the thing, and gets a
	picture only at the size it is shown. A picture copied from a browser's context menu is
	the image itself, at its own size, whatever is showing; and a file copied in File Explorer
	is the same, for anything saved on disk.

	Pillow reads both: `ImageGrab.grabclipboard` returns an image for copied image data and a
	list of paths for copied files. Only the first file that opens as a picture is drawn.

	**Needs Pillow, and says so.** There is no Python path for this one. Decoding PNG and JPEG
	by hand is not a fallback worth carrying, and a reader on an NVDA without Pillow still has
	the screen capture.

	:return: the pixels, greyscale and colour, at or below `keepPixels`.
	:raises ImageRefused: with a reason the reader can act on.
	"""
	if not hasPillow():
		raise ImageRefused(
			# Translators: reported when a picture was asked for from the clipboard on an NVDA
			# that does not include the Pillow image library, which reading one needs.
			_("Drawing from the clipboard needs the Pillow library, which this NVDA does not have"),
		)
	try:
		from PIL import Image, ImageGrab

		found = ImageGrab.grabclipboard()
	except Exception:
		log.error("BrlMultiline: the clipboard would not be read", exc_info=True)
		# Translators: reported when the clipboard could not be read to draw a picture from it.
		raise ImageRefused(_("The clipboard could not be read"))
	# Translators: what a picture drawn from the clipboard is called when nothing names it.
	name = _("clipboard picture")
	image = None
	if isinstance(found, list):
		for path in found:
			try:
				opened = Image.open(path)
				opened.load()
			except Exception:
				log.debug(f"BrlMultiline: a copied file is not a picture: {path!r}")
				continue
			image = opened
			name = os.path.basename(str(path))
			break
		if image is None:
			# Translators: reported when files were copied, and none of them is a picture.
			raise ImageRefused(_("None of the copied files is a picture"))
	elif found is not None:
		image = found
	if image is None:
		# Translators: reported when a picture was asked for from the clipboard and there is none
		# on it. Says how to put one there.
		raise ImageRefused(_("There is no picture on the clipboard; copy an image or an image file"))
	width, height = image.size
	if width < MIN_SIDE or height < MIN_SIDE:
		raise ImageRefused(
			# Translators: reported when a picture on the clipboard is too small to draw. The
			# placeholders are its width and height in pixels.
			_("This picture is only {width} by {height}, too small to draw").format(
				width=width, height=height
			),
		)
	try:
		picture = pictureFromImage(image, _captureSize(width, height), name)
	except Exception:
		log.error("BrlMultiline: a picture from the clipboard would not convert", exc_info=True)
		picture = None
	if picture is None:
		# Translators: reported when a picture on the clipboard could not be read.
		raise ImageRefused(_("The picture on the clipboard could not be read"))
	return picture


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
	**Two ways, the same picture.** With Pillow the rectangle is copied near its own size and
	Pillow turns it grey and shrinks it to `keepPixels`, without a Python loop over a pixel, and
	the colour is kept beside the grey for the things that need it.
	Without it Windows shrinks during the copy and each kept pixel is read in Python, which is
	what every version of this did before and is kept whole for an NVDA whose build leaves
	Pillow out. See `imagePins` for why that is possible.

	:return: the pixels.
	:raises ImageRefused: if the screen would not give them up.
	"""
	keep = _captureSize(int(width), int(height))
	grab = _captureSize(int(width), int(height), GRAB_PIXELS) if hasPillow() else keep
	try:
		import screenBitmap

		grabber = screenBitmap.ScreenBitmap(*grab)
		pixels = grabber.captureImage(int(left), int(top), int(width), int(height))
	except Exception:
		log.error("BrlMultiline: the screen would not be captured", exc_info=True)
		# Translators: reported when copying part of the screen failed.
		raise ImageRefused(_("This could not be copied off the screen"))
	raw = _rawBytes(pixels, *grab)
	if raw is not None:
		picture = pictureFromBgrx(raw, grab[0], grab[1], keep, name)
		if picture is not None:
			return picture
	if grab != keep:
		# Pillow was there when the size was chosen and would not read the capture. Rare
		# enough to be worth a log line, and the capture is still good: read in Python and
		# shrunk by `Picture.reduce`, slower and the same picture.
		log.debugWarning("BrlMultiline: Pillow would not read a capture, reading it in Python")
		large = Picture(greysFromPixels(pixels, *grab), grab[0], grab[1], name)
		return Picture(large.reduce((0, 0, grab[0], grab[1]), *keep), keep[0], keep[1], name)
	return Picture(greysFromPixels(pixels, *keep), keep[0], keep[1], name)


def sizeWords(picture: Optional[Picture]) -> str:
	""":return: how big the captured picture is, for a reader asking what they have.

	:param picture: what was captured, or None.
	"""
	if picture is None:
		return ""
	# Translators: the size of a captured picture. Placeholders are pixels across and down.
	return _("{width} by {height}").format(width=picture.width, height=picture.height)
