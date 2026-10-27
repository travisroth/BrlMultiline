# BrlMultiline: getting a picture off the screen.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The NVDA half of image import: which object, and its pixels.

The counterpart of `chartSource.py`, so `imagePins.py` can be arithmetic alone.

The source is the navigator object, which in browse mode is the graphic under the caret. Any
role is accepted; refused is only what cannot work: no location, nothing on a monitor, an
object that says it is not in view, or too small to be a picture. A window over the object is
copied instead of it, and cannot be detected. See docs/design/tactile-graphics-plan.md.
"""

import os

import api
from logHandler import log

from .imagePins import (
	ImageRefused,
	Picture,
	fitWithin,
	greysFromPixels,
	hasPillow,
	pictureFromBgrx,
	pictureFromImage,
)

import addonHandler

addonHandler.initTranslation()

__all__ = [
	"MIN_SIDE",
	"addressOf",
	"captureClipboard",
	"captureNavigator",
	"fetchPicture",
	"keepPixels",
	"nameFor",
	"readAddress",
]


MIN_SIDE = 16
"""How many screen pixels across and down something must be before it is worth drawing.

Below this it is a bullet, spacer or tracking image, and saying so beats drawing a smudge.
"""

MAX_PIXELS = 360000
"""How many pixels are worth keeping without Pillow, whatever the thing's size on screen.

Six hundred square. Kept so zoom reduces from pixels; bounded by what Python reduces without a
noticeable pause. See `PILLOW_PIXELS`.
"""

PILLOW_PIXELS = 1440000
"""How many pixels are worth keeping when Pillow does the work: twelve hundred square.

Four times `MAX_PIXELS`, one more doubling of zoom, since Pillow reduces quickly.
"""

GRAB_PIXELS = 2000000
"""How many pixels to copy off the screen when Pillow will shrink them to `PILLOW_PIXELS`.

Copied large so Pillow averages down, since Windows shrinking in `ScreenBitmap`'s mode ANDs
the colours together; see `imagePins.pictureFromBgrx`. Two megapixels is a full HD screen.
"""


def _screenCurtainIsUp() -> bool:
	""":return: whether NVDA is blanking the screen.

	A capture under the curtain comes back black, so the reason is said instead.
	"""
	try:
		from screenCurtain import screenCurtain

		return screenCurtain is not None and screenCurtain.enabled
	except Exception:
		# An NVDA without it, or one that has not started it. Either way it is not up.
		return False


def _visibleScreen() -> "tuple | None":
	""":return: the whole desktop across every monitor, or None if Windows will not say.

	The virtual screen, so a second monitor counts. None rather than a guess, since not
	clipping is the safer failure.
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

	The virtual screen's bounding box holds ground on no monitor when monitors are not aligned.
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

	An object's location is where it would be, not where it is seen, and copying off screen
	succeeds with whatever is there. A partly visible object is clipped rather than refused,
	so the size check runs on the result. Measured against the real monitors; see `_monitors`.

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

	As well as clipping, since a control scrolled out of a pane can keep a location over it.
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

	`imagePins.fitWithin`, with the budget this NVDA can afford.

	:param width: its width on screen.
	:param height: its height on screen.
	:param most: the most pixels to allow, `keepPixels` if None.
	:return: the capture size.
	"""
	return fitWithin(width, height, keepPixels() if most is None else most)


def _rawBytes(pixels: object, width: int, height: int) -> "bytes | None":
	""":return: a capture's pixels as bytes, four to a pixel, or None if it is not that shape.

	`ScreenBitmap.captureImage`'s `RGBQUAD` array is what Pillow wants; anything else goes the
	Python way.
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

	The object's own words first, such as alt text; the role is a fallback, not a prefix.

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


def captureNavigator(obj=None) -> Picture:
	"""Take a picture of whatever the reader is pointing at, off the screen.

	:param obj: the object to copy, or None for the navigator object. Given when the object
		was chosen earlier and the navigator may have moved on.
	:return: the pixels, at or below `keepPixels`.
	:raises ImageRefused: with a reason the reader can act on.
	"""
	if _screenCurtainIsUp():
		# Translators: reported when a picture was asked for while the screen curtain is on.
		raise ImageRefused(_("Turn the screen curtain off to draw a picture"))
	if obj is None:
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
	picture = capture(left, top, width, height, name=nameFor(obj))
	picture.fromScreen = True
	return picture


def captureClipboard() -> Picture:
	"""Take a picture from the clipboard: an image copied, or an image file copied.

	Not the screen, so the curtain and covering windows do not matter, at the image's own size.
	`ImageGrab.grabclipboard` gives image data or file paths; the first file that opens is
	drawn. Needs Pillow, and says so.

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
		from PIL import ImageGrab

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
				opened = openImage(path)
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
				width=width,
				height=height,
			),
		)
	try:
		# The budget, not a size: the size is chosen after a photo is turned the right way up.
		picture = pictureFromImage(image, keepPixels(), name)
	except Exception:
		log.error("BrlMultiline: a picture from the clipboard would not convert", exc_info=True)
		picture = None
	if picture is None:
		# Translators: reported when a picture on the clipboard could not be read.
		raise ImageRefused(_("The picture on the clipboard could not be read"))
	return picture


FETCH_SCHEMES = ("http", "https", "data")
"""The kinds of image address the add-on will load for itself.

Not `file`: a page's `file` address can name a network share, and opening it offers the
reader's login to its owner. `blob` exists only in the browser. Both fall back to the screen.
"""

FETCH_SECONDS = 8
"""How long a download may take before the screen capture is used instead."""

FETCH_BYTES = 20 * 1024 * 1024
"""The most of an image file that will be read. Larger than this is not a picture for pins."""

DECODE_PIXELS = 25_000_000
"""The most pixels an image file may decode to, after a JPEG has been decoded at a reduced size.

A small file can declare an enormous picture, so the declared size is checked before decoding.
Past a large phone photograph, and about a hundred megabytes decoded. See `openImage`.
"""


def _domAttribute(obj, name: str) -> "str | None":
	""":return: an HTML attribute read through ISimpleDOMNode, or None.

	The second way to ask, for a browser that does not answer through IAccessible2 as Chrome
	does.
	"""
	try:
		from comtypes.automation import BSTR
		from comtypes.gen.ISimpleDOM import ISimpleDOMNode
		from ctypes import c_short

		node = obj.IAccessibleObject.QueryInterface(ISimpleDOMNode)
		value = node.attributesForNames(1, (BSTR * 1)(name), (c_short * 1)(0))
	except Exception:
		return None
	return str(value) if value else None


def _documentAddress(obj) -> "str | None":
	""":return: the address of the page an object is on, for resolving a relative one."""
	try:
		found = obj.treeInterceptor.documentConstantIdentifier
	except Exception:
		return None
	return found if isinstance(found, str) and "://" in found else None


def addressOf(obj) -> "str | None":
	"""The address of an image's own file, if the browser will say it.

	The file is the image at its own size, uncovered, and works under the screen curtain. Read
	from the `src` of `IA2Attributes`, then ISimpleDOMNode.

	:param obj: the object the reader is on.
	:return: an absolute `http`, `https` or `data` address, or None to use the screen.
	"""
	if obj is None:
		return None
	address = None
	try:
		attributes = obj.IA2Attributes
		if isinstance(attributes, dict):
			address = attributes.get("src")
	except Exception:
		address = None
	if not address:
		address = _domAttribute(obj, "src")
	if not address or not str(address).strip():
		return None
	address = str(address).strip()
	from urllib.parse import urljoin, urlsplit

	if not urlsplit(address).scheme:
		base = _documentAddress(obj)
		if base is None:
			return None
		address = urljoin(base, address)
	if urlsplit(address).scheme.lower() not in FETCH_SCHEMES:
		return None
	return address


def _opener():
	""":return: a URL opener that speaks HTTP and HTTPS and nothing else.

	Built from parts, so a redirect cannot reach anywhere `FETCH_SCHEMES` refuses.
	"""
	import urllib.request

	opener = urllib.request.OpenerDirector()
	for handler in (
		urllib.request.ProxyHandler(),
		urllib.request.HTTPHandler(),
		urllib.request.HTTPSHandler(),
		urllib.request.HTTPRedirectHandler(),
		urllib.request.HTTPDefaultErrorHandler(),
		urllib.request.HTTPErrorProcessor(),
	):
		opener.add_handler(handler)
	return opener


def _download(address: str) -> bytes:
	""":return: the bytes at a web address, at most `FETCH_BYTES`.

	Separate so the tests can stand in for the network.

	:raises ImageRefused: if there is more than that.
	"""
	import urllib.parse
	import urllib.request

	request = urllib.request.Request(address, headers={"User-Agent": "BrlMultiline NVDA add-on"})
	with _opener().open(request, timeout=FETCH_SECONDS) as response:
		if urllib.parse.urlsplit(response.geturl()).scheme.lower() not in ("http", "https"):
			# Translators: reported when a picture's own file could not be loaded.
			raise ImageRefused(_("The picture could not be loaded"))
		raw = response.read(FETCH_BYTES + 1)
	if len(raw) > FETCH_BYTES:
		# Translators: reported when a picture's file is too large to load.
		raise ImageRefused(_("The picture's file is too large to load"))
	return raw


def readAddress(address: str) -> bytes:
	"""The bytes of an image, from the page itself or from the web.

	A `data` address is decoded here; `http` and `https` are downloaded, without the browser's
	cookies, so a signed-in-only image falls back to the screen.

	Runs off NVDA's main thread; see `fetchPicture`.

	:param address: from `addressOf`.
	:return: the file's bytes.
	:raises Exception: anything a failed load raises. The caller falls back on any of them.
	"""
	import base64
	import urllib.parse

	scheme = urllib.parse.urlsplit(address).scheme.lower()
	if scheme == "data":
		header, _comma, payload = address.partition(",")
		if header.lower().endswith(";base64"):
			raw = base64.b64decode(payload, validate=False)
		else:
			raw = urllib.parse.unquote_to_bytes(payload)
		if len(raw) > FETCH_BYTES:
			# Translators: reported when a picture's file is too large to load.
			raise ImageRefused(_("The picture's file is too large to load"))
		return raw
	if scheme not in ("http", "https"):
		# Translators: reported when a picture's own file could not be loaded.
		raise ImageRefused(_("The picture could not be loaded"))
	return _download(address)


def pictureFromBytes(raw: bytes, name: str = "") -> Picture:
	"""Decode an image file and make a picture of it.

	:param raw: the file.
	:param name: what to call the picture.
	:raises ImageRefused: if it is not a picture Pillow can read, or is too small.
	"""
	import io

	try:
		image = openImage(io.BytesIO(raw))
	except ImageRefused:
		raise
	except Exception:
		# Translators: reported when a picture's file could not be read, for example because it
		# is a kind of image the add-on cannot draw.
		raise ImageRefused(_("The picture's file could not be read"))
	width, height = image.size
	if width < MIN_SIDE or height < MIN_SIDE:
		raise ImageRefused(
			# Translators: reported when a picture is too small to draw. The placeholders are
			# its width and height in pixels.
			_("This picture is only {width} by {height}, too small to draw").format(
				width=width,
				height=height,
			),
		)
	picture = pictureFromImage(image, keepPixels(), name)
	if picture is None:
		# Translators: reported when a picture's file could not be read.
		raise ImageRefused(_("The picture's file could not be read"))
	return picture


def openImage(source):
	"""Open an image file and decode it, refusing one that would decode too large.

	Checked after `open` reads the header and before `load`. A JPEG is decoded at a reduced
	scale near `keepPixels`. Pillow's decompression bomb warning is treated as an error.

	:param source: a path or a file object.
	:return: the decoded image.
	:raises ImageRefused: if it would decode to more than `DECODE_PIXELS`.
	:raises Exception: whatever Pillow raises for a file it cannot read.
	"""
	import math
	import warnings

	from PIL import Image

	with warnings.catch_warnings():
		warnings.simplefilter("error", Image.DecompressionBombWarning)
		image = Image.open(source)
		width, height = image.size
		wanted = keepPixels()
		if width * height > wanted:
			shrink = math.sqrt(wanted / (width * height))
			# A request rather than a command: a JPEG decodes at the largest of a half, a
			# quarter or an eighth that is still at least this size, and anything else ignores it.
			image.draft(image.mode, (math.ceil(width * shrink), math.ceil(height * shrink)))
			width, height = image.size
		if width * height > DECODE_PIXELS:
			raise ImageRefused(
				# Translators: reported when a picture's file is too large to draw. The placeholders
				# are its width and height in pixels.
				_("This picture is {width} by {height}, too large to draw").format(
					width=width,
					height=height,
				),
			)
		image.load()
	return image


def _startBackground(work) -> None:
	"""Run a piece of work on its own thread. Replaced by the tests, which run it at once."""
	import threading

	threading.Thread(target=work, name="BrlMultiline picture load", daemon=True).start()


def fetchPicture(address: str, name: str, done) -> None:
	"""Load an image's own file in the background, then hand the picture back.

	On a thread of its own that touches nothing of NVDA's, since NVDA is silent while its main
	thread waits; the result returns through `wx.CallAfter`.

	:param address: from `addressOf`.
	:param name: what to call the picture.
	:param done: called on the main thread as `done(picture, why)`, with the picture and None,
		or None and the reason it could not be had.
	"""

	def work():
		try:
			picture = pictureFromBytes(readAddress(address), name)
		except Exception as failure:
			picture = None
			why = failure
		else:
			why = None
		import wx

		wx.CallAfter(done, picture, why)

	_startBackground(work)


def capture(left: int, top: int, width: int, height: int, name: str = "") -> Picture:
	"""Copy a rectangle of the screen and keep it as brightnesses.

	With Pillow the rectangle is copied near its own size, then greyed and shrunk to
	`keepPixels`, colour kept beside. Without it Windows shrinks during the copy and Python
	reads each pixel. See `imagePins`.

	:param left: screen x of the top left corner.
	:param top: screen y.
	:param width: how wide on screen.
	:param height: how tall.
	:param name: what to call it.
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


def sizeWords(picture: Picture | None) -> str:
	""":return: how big the captured picture is, for a reader asking what they have.

	:param picture: what was captured, or None.
	"""
	if picture is None:
		return ""
	# Translators: the size of a captured picture. Placeholders are pixels across and down.
	return _("{width} by {height}").format(width=picture.width, height=picture.height)
