# BrlMultiline: turning a picture into pins.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Greyscale pixels in, raised pins out.

Knows nothing about NVDA, the screen or the display, so the arithmetic can be tested on
lists of numbers, as with `chart.py`.

1. Reduce first, then detect. The greys are area-averaged to exactly the size being drawn,
   and the detector runs on that. Detecting first and shrinking the edge map turns texture
   into solid blocks and drops thin lines. A window is a crop reduced the same way, so zoom
   needs nothing special.
2. A ceiling, not a quota. The cut comes from the data (Otsu over gradient magnitudes for
   `EDGES`, class membership for `BRIGHTNESS`); coverage limits only ever remove pins, and
   an empty part of a picture is refused rather than filled.
3. A window is judged against the whole picture, since every detector finds something even
   in blank paper. See `MEANINGFUL`.
4. Pillow for the per-pixel work (grey conversion, reduction, thickening), Python for the
   panel-sized decisions after it. Pillow ships with NVDA but is not part of its add-on API,
   so every use is behind `_Image is None` and the pure Python path is kept, tested and
   equivalent. numpy is not used: it is missing from installed NVDA.
"""

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
	from PIL.Image import Image as PillowImage

try:
	from PIL import Image as _Image, ImageChops as _ImageChops, ImageFilter as _ImageFilter
except Exception:
	# ImportError on an NVDA without it, and anything else on one whose copy will not load: a
	# missing or mismatched native module raises OSError or ImportError depending on how it is
	# missing. Either way the answer is the same, which is the Python path.
	_Image = None
	_ImageChops = None
	_ImageFilter = None

import addonHandler

addonHandler.initTranslation()

__all__ = [
	"EDGES",
	"STROKES",
	"BRIGHTNESS",
	"ImageRefused",
	"Picture",
	"Placement",
	"Rendering",
	"Strength",
	"brightnessPins",
	"edgePins",
	"fitWithin",
	"greysFromPixels",
	"hasPillow",
	"pictureFromBgrx",
	"pictureFromImage",
	"place",
	"render",
	"renderAt",
	"strokePins",
	"subjectIsDarkIn",
	"windowOf",
]


EDGES = "edges"
"""Outlines: where the picture changes, which is what line art and diagrams are made of."""

STROKES = "strokes"
"""Single lines: each stroke of ink drawn once, down its middle.

`EDGES` draws each stroke as two rails, one per side (see `_thin`). This finds the ink, as
`BRIGHTNESS` does, and pares it to a centre line one pin wide.
"""

BRIGHTNESS = "brightness"
"""Silhouette: the dark against the light, or the light against the dark."""


EDGE_COVERAGE = 0.16
"""What fraction of the panel an outline should raise.

A ceiling. Roughly what a hand reads as a drawing with white space in it; the number most
worth tuning by touch.
"""

BRIGHTNESS_MOST = 0.55
"""The most of the panel a silhouette may raise.

A ceiling and no floor, like `EDGE_COVERAGE`: raising more pins than the subject has would
raise background. Past the ceiling the most extreme pins are kept, leaving a mass with holes;
rare, since such a picture is usually wanted reversed.
"""

PLAIN = 10
"""How much darkest-to-lightest spread a picture needs before it is worth drawing.

Out of 255. Below this every threshold would split noise. Refused rather than drawn, since an
empty or solid panel feels like a broken display.
"""

MEANINGFUL = 0.15
"""How much of the whole picture's strength a window must carry to be worth drawing.

Judged against the whole picture, since a detector finds a best answer even in blank paper.
A sixth: faint background texture measures about five per cent of real ink, and refusing a
window costs less than drawing noise the reader takes for the picture.
"""

SUBJECT_TONES = 20
"""A tone difference this size is something a picture is saying, whatever else is in it.

An absolute floor beside `MEANINGFUL`, so one very dark patch elsewhere cannot make a real
feature look like background. Twice `PLAIN`, in tones, so both styles can use it.
"""

REFERENCE_SHARE = 0.02
"""How much of the picture the strength reference is taken from, strongest first.

The strongest fiftieth rather than the single strongest cell, so one speck cannot set the
yardstick.
"""

REFERENCE_CELLS = 48
"""How finely the whole picture is measured when working out what its strength is.

Fixed rather than the panel size, so the reference means the same at every zoom. Close to a
panel's forty rows, so gradients are comparable, and cheap.
"""

SOLID = 4
"""How thick, in pins, a piece of ink has to be before `STROKES` draws round it, not through it.

Average width, as area over skeleton length, taken from the plain reduction rather than
`Picture.reduceInk`, whose thickening would inflate it. At four a band's width can be felt, so
both sides are worth drawing.
"""

TRIM_KEEPS = 0.85
"""Past this share of the picture, content is left untrimmed.

Such a trim gains nothing a hand notices, and would make captures of the same picture differ.
"""

TRIM_MARGIN = 0.04
"""How much room to leave round trimmed content, as a share of its longer side.

Outlines never raise the panel's outermost ring, so content trimmed flush would lose its
outer edge. About a pin and a half at fit.
"""

COLOUR_REACH = 85
"""How far a colour may be from the one asked for and still count as it, per channel.

Out of 255, on the channel that differs most. Covers the anti-aliasing and compression cloud
around a colour but not a neighbouring series. Scaled so this distance is full white; Otsu
then decides where the colour ends.
"""

INK_FROM_PAPER = 40
"""How far a pixel must be from the paper, on its most different channel, to count as ink.

For `Picture.colourAt`. Below it the finger is on paper. Noise and fringes stay under it.
"""

SAME_HUE = 0.95
"""How closely two colours must point the same way from the paper to be one stroke's.

A cosine between the colours as steps away from the paper. A fringe points the stroke's way.
Red and blue on white are about 0.63 and black and red about 0.86, so both pairs are parted.
"""

SAME_COLOUR = 32
"""How near, on the channel that differs most, a pixel must be to count as the colour found.

Small, because it only has to take in the core of one stroke; the fringe and every other
stroke are meant to fall outside it. See `Picture.colourAt`.
"""

SPARSE = 0.004
"""Below this fraction raised, there is nothing under the hand to find."""


class ImageRefused(Exception):
	"""There is a picture, but not one that can be put on pins.

	Raised rather than returning None, so the reason reaches the reader.
	"""


class Strength(NamedTuple):
	"""How much a picture has to say, measured once on the whole of it.

	The yardstick a window is held against. See `MEANINGFUL`.
	"""

	gradient: int
	"""The strongest edge anywhere in the picture."""

	separation: int
	"""How far apart its two tone classes are, at Otsu's split between them."""


class Placement(NamedTuple):
	"""Which pixels are being drawn, and where on the panel they land.

	Two rectangles: the source always lies inside the picture, and the destination says where
	its reduction sits on the panel. The margins are never reduced, detected or counted, so a
	detector is never asked about pins the add-on invented.
	"""

	source: tuple
	"""`(left, top, width, height)` in picture pixels. Always inside the picture."""

	left: int
	"""Panel column the drawn content starts at."""

	top: int
	"""Panel row it starts at."""

	width: int
	"""How many pins across the content is."""

	height: int
	"""How many pins down."""

	@property
	def area(self) -> int:
		""":return: how many pins the picture itself gets.

		What a coverage limit is a fraction of, rather than the whole panel.
		"""
		return self.width * self.height


class Rendering(NamedTuple):
	"""One picture worked out for one rectangle of pins."""

	pins: bytearray
	"""One byte per pin, row major, 1 for raised."""

	width: int
	"""Pins across the content. Not the panel: see `Placement`."""

	height: int
	"""Pins down the content."""

	raised: int
	"""How many are up. What decides whether this is worth showing at all."""

	left: int = 0
	"""Panel column to stamp the content at."""

	top: int = 0
	"""Panel row to stamp it at."""

	barren: bool = False
	"""Whether this is deliberately empty, because the picture is empty here.

	Only ever true of a window of a picture that drew. An empty capture is refused, since a
	blank panel feels like a broken display; but once the picture has drawn, a blank window is
	a fact about the picture. It is drawn and announced, never silently.
	"""

	crowded: bool = False
	"""Whether the picture holds more edge than the panel can carry, so this is a texture.

	Such as a floor plan or small print, drawn as an even scattering a hand cannot tell from a
	contour. Announced, as `barren` is, so the reader knows to magnify.
	"""

	@property
	def coverage(self) -> float:
		""":return: the fraction of the picture's own pins that are raised.

		Of the content rather than of the panel, because it is a statement about how dense
		the picture is under a hand and the margin is not part of the picture.
		"""
		total = self.width * self.height
		return self.raised / total if total else 0.0

	def draw(self, buffer) -> None:
		"""Stamp this onto a drawing buffer where it belongs.

		:param buffer: anything with `setDot`, which is `PinBuffer`.
		"""
		for y in range(self.height):
			row = y * self.width
			for x in range(self.width):
				if self.pins[row + x]:
					buffer.setDot(self.left + x, self.top + y)


def greysFromPixels(rows, width: int, height: int) -> bytearray:
	"""Flatten captured screen pixels into one brightness each.

	Rec. 601 weights, not NVDA's `screenBitmap.rgbPixelBrightness`, which swaps the red and
	blue terms. Written as Pillow's own rounded fixed point formula, so both paths give the same
	levels. The path without Pillow; `pictureFromBgrx` is the one with it.

	:param rows: what `screenBitmap.captureImage` gives back, indexable as `rows[y][x]` with
		`rgbRed`, `rgbGreen` and `rgbBlue` on each pixel.
	:param width: pixels across.
	:param height: pixels down.
	:return: one byte per pixel, row major.
	"""
	greys = bytearray(width * height)
	at = 0
	for y in range(height):
		row = rows[y]
		for x in range(width):
			pixel = row[x]
			greys[at] = (pixel.rgbRed * 19595 + pixel.rgbGreen * 38470 + pixel.rgbBlue * 7471 + 0x8000) >> 16
			at += 1
	return greys


def hasPillow() -> bool:
	""":return: whether the pixel work can be handed to Pillow.

	Asked by the capture, which grabs more pixels when Pillow is there to shrink them properly.
	"""
	return _Image is not None


def pictureFromBgrx(
	raw: bytes,
	width: int,
	height: int,
	keep: "tuple[int, int]",
	name: str = "",
) -> "Picture | None":
	"""Make a picture from a raw screen capture, shrunk to the size worth keeping.

	`raw` is the bytes of what `screenBitmap.captureImage` returns: four bytes a pixel, blue,
	green, red and one unused, rows top to bottom. Pillow reads that layout directly as "BGRX",
	so no pixel is visited in Python.

	The shrink to `keep` is a box average here, not the screen copy's own stretch, which ANDs
	dropped pixels' colours and mangles colour lines.

	:param raw: the captured bytes, `width * height * 4` of them.
	:param width: pixels across in the capture.
	:param height: pixels down.
	:param keep: `(width, height)` to hold on to, no larger than the capture.
	:param name: what to call the picture.
	:return: the picture, or None if Pillow is not there or would not read the bytes, in which
		case the caller goes the Python way.
	"""
	pillow = _Image
	if pillow is None or len(raw) != width * height * 4:
		return None
	try:
		colour = pillow.frombuffer("RGB", (width, height), raw, "raw", "BGRX", 0, 1)
		if (width, height) != (keep[0], keep[1]):
			colour = colour.resize((keep[0], keep[1]), pillow.Resampling.BOX)
		image = colour.convert("L")
	except Exception:
		return None
	return Picture(bytearray(image.tobytes()), image.width, image.height, name, image=image, colour=colour)


def fitWithin(width: int, height: int, most: int) -> "tuple[int, int]":
	"""Choose how many pixels to keep of something this size, at most `most`.

	Never enlarged, which would add time and invented pixels but no detail.

	:param width: pixels across.
	:param height: pixels down.
	:param most: the most pixels to allow.
	:return: the size to keep.
	"""
	if width * height <= most:
		return width, height
	# Shrunk on both axes by the same factor, because the shape has to survive: a picture
	# squeezed on one axis is a picture of something else, and nothing downstream could know.
	scale = (most / (width * height)) ** 0.5
	return max(1, int(width * scale)), max(1, int(height * scale))


def pictureFromImage(image: "PillowImage", most: int, name: str = "") -> "Picture | None":
	"""Make a picture from an image that did not come off the screen: a file, the clipboard.

	1. Transparency is laid on white, since the colour stored behind it is often black.
	2. A photograph is turned by its EXIF orientation first.
	3. The size kept is chosen after turning, from a pixel budget, so it fits the turned
	   picture.

	:param image: the image, as Pillow opened it.
	:param most: the most pixels to keep; see `fitWithin`.
	:param name: what to call the picture.
	:return: the picture, or None if Pillow is not there.
	"""
	pillow = _Image
	if pillow is None:
		return None
	from PIL import ImageOps

	image = ImageOps.exif_transpose(image) or image
	if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
		image = image.convert("RGBA")
		ground = pillow.new("RGBA", image.size, (255, 255, 255, 255))
		image = pillow.alpha_composite(ground, image)
	colour = image.convert("RGB")
	keep = fitWithin(colour.width, colour.height, most)
	if colour.size != keep:
		colour = colour.resize(keep, pillow.Resampling.BOX)
	grey = colour.convert("L")
	return Picture(bytearray(grey.tobytes()), grey.width, grey.height, name, image=grey, colour=colour)


def _rgbOf(image: "PillowImage") -> "list[tuple[int, int, int]]":
	""":return: an RGB image's pixels as tuples, row major.

	From `tobytes`, not `getdata`, which Pillow 12 deprecates; NVDA may update Pillow at any
	release.
	"""
	raw = image.tobytes()
	return list(zip(raw[0::3], raw[1::3], raw[2::3]))


def _topOf(values, share: float = REFERENCE_SHARE) -> int:
	""":return: the value at the top `share` of a list, or 0 if it is empty.

	A percentile, so a reference cannot be moved by a single cell.

	:param values: the measurements.
	:param share: how much of the top to look past.
	"""
	if not values:
		return 0
	ordered = sorted(values, reverse=True)
	return ordered[min(len(ordered) - 1, int(len(ordered) * share))]


class Picture:
	"""Captured pixels, at whatever size they were captured.

	Held rather than reduced once, so zooming reduces each window from its own pixels and
	gives real detail, up to the capture's resolution.
	"""

	def __init__(
		self,
		greys: bytearray,
		width: int,
		height: int,
		name: str = "",
		image: "PillowImage | None" = None,
		colour: "PillowImage | None" = None,
	):
		"""
		:param greys: one brightness per pixel, row major.
		:param width: pixels across.
		:param height: pixels down.
		:param name: what to call this picture when a reader asks what is on the display.
		:param image: the same pixels as a Pillow "L" image, if the capture already has one.
			Made from `greys` when first needed otherwise.
		:param colour: the same pixels in colour, as a Pillow "RGB" image. Only a capture made
			with Pillow has one, and everything that needs colour says so when it is missing.
		"""
		self.greys = greys
		self.width = width
		self.height = height
		self.name = name
		self._strength = None
		self._image: "PillowImage | None" = image
		self.colour: "PillowImage | None" = colour
		self._content: "tuple[int, int, int, int] | None" = None
		self._paper: "tuple[int, int, int] | None" = None
		self._againstPaper: "Picture | None" = None
		self.derivedFrom: "Picture | None" = None
		"""The picture this one was made from, for a picture of one colour of another."""
		self.onlyColour: "tuple[int, int, int] | None" = None
		"""The colour this picture keeps, if it is a picture of one colour of another."""
		self.touched: "tuple[int, int, int] | None" = None
		"""The colour under the last routing press on this picture, kept on the original."""
		self.fromScreen = False
		"""Whether this was copied off the screen, and so is only as large as it was shown."""

	def _pillow(self) -> "PillowImage | None":
		""":return: these pixels as a Pillow image, or None to go the Python way.

		Built once and kept, because a reader zooming asks for a reduction per view. The greys
		stay the record: nothing writes to either after capture, so the two cannot drift.
		"""
		pillow = _Image
		if pillow is None:
			return None
		if self._image is None:
			try:
				self._image = pillow.frombytes("L", (self.width, self.height), bytes(self.greys))
			except Exception:
				return None
		return self._image

	@property
	def content(self) -> "tuple[int, int, int, int]":
		""":return: the part of the picture with something in it, as a source box.

		Blank margins waste pins, so the starting box is cut to what differs from the border
		tone by `SUBJECT_TONES`, which is above faint texture and noise, with `TRIM_MARGIN`
		round it. All pixels are kept; only the starting box moves. A one-colour picture made
		from this one keeps the same box. Measured once.
		"""
		if self._content is None:
			self._content = self._findContent()
		return self._content

	def _findContent(self) -> "tuple[int, int, int, int]":
		""":return: the trimmed box. See `content`."""
		whole = (0, 0, self.width, self.height)
		if not self.greys or self.width < 3 or self.height < 3:
			return whole
		paper = self.border
		low = paper - SUBJECT_TONES
		high = paper + SUBJECT_TONES
		image = self._pillow()
		if image is not None:
			found = image.point([0 if low <= value <= high else 255 for value in range(256)]).getbbox()
			if not found:
				return whole
			left, top, right, bottom = found
		else:
			width = self.width
			greys = self.greys
			rows = [
				y
				for y in range(self.height)
				if min(greys[y * width : (y + 1) * width]) < low
				or max(greys[y * width : (y + 1) * width]) > high
			]
			if not rows:
				return whole
			columns = [x for x in range(width) if min(greys[x::width]) < low or max(greys[x::width]) > high]
			left, right = columns[0], columns[-1] + 1
			top, bottom = rows[0], rows[-1] + 1
		room = max(2, round(TRIM_MARGIN * max(right - left, bottom - top)))
		left = max(0, left - room)
		top = max(0, top - room)
		right = min(self.width, right + room)
		bottom = min(self.height, bottom + room)
		if (right - left) * (bottom - top) > TRIM_KEEPS * self.width * self.height:
			return whole
		return left, top, right - left, bottom - top

	@property
	def paper(self) -> "tuple[int, int, int] | None":
		""":return: the colour round the edge of the picture, or None if there is no colour.

		The colour counterpart of `border`, and taken the same way, from the outermost pixels,
		since what is round the edge of a picture is almost always what it is drawn on.
		"""
		if self.colour is None:
			return None
		if self._paper is None:
			colour = self.colour
			width, height = colour.size
			total = [0, 0, 0]
			count = 0
			for strip in (
				(0, 0, width, 1),
				(0, height - 1, width, height),
				(0, 0, 1, height),
				(width - 1, 0, width, height),
			):
				for red, green, blue in _rgbOf(colour.crop(strip)):
					total[0] += red
					total[1] += green
					total[2] += blue
					count += 1
			self._paper = (total[0] // count, total[1] // count, total[2] // count)
		return self._paper

	def colourAt(self, box: "tuple[int, int, int, int]") -> "tuple[int, int, int] | None":
		"""What colour is under a finger: the ink nearest it if there is any, else the paper.

		One stroke's colour, never a blend, since the pressed area can hold two series:

		1. The ink nearest the middle of the press picks the stroke; ties go to the stronger.
		2. Its strongest neighbour pointing the same way from the paper (see `SAME_HUE`) is the
		   stroke's core, since the nearest ink is often an anti-aliased fringe.
		3. The answer averages the pixels within `SAME_COLOUR` of that core.

		With no ink beyond `INK_FROM_PAPER`, the paper's colour is the answer.

		:param box: `(left, top, width, height)` in picture pixels, clipped to the picture.
		:return: red, green and blue, or None if the picture has no colour or the box is empty.
		"""
		paper = self.paper
		if paper is None or self.colour is None:
			return None
		left, top, width, height = box
		right = min(self.width, left + width)
		bottom = min(self.height, top + height)
		left = max(0, left)
		top = max(0, top)
		if right <= left or bottom <= top:
			return None
		pixels = _rgbOf(self.colour.crop((left, top, right, bottom)))
		across = right - left
		middleX = (across - 1) / 2
		middleY = (bottom - top - 1) / 2

		def apart(one: "tuple[int, int, int]", other: "tuple[int, int, int]") -> int:
			return max(abs(one[0] - other[0]), abs(one[1] - other[1]), abs(one[2] - other[2]))

		def fromPaper(pixel: "tuple[int, int, int]") -> "tuple[int, int, int]":
			return (pixel[0] - paper[0], pixel[1] - paper[1], pixel[2] - paper[2])

		def sameHue(one: "tuple[int, int, int]", other: "tuple[int, int, int]") -> bool:
			first = fromPaper(one)
			second = fromPaper(other)
			dot = sum(a * b for a, b in zip(first, second))
			lengths = (sum(a * a for a in first) * sum(b * b for b in second)) ** 0.5
			return lengths > 0 and dot >= SAME_HUE * lengths

		ink = [at for at, pixel in enumerate(pixels) if apart(pixel, paper) >= INK_FROM_PAPER]
		if not ink:
			return paper

		def gap(at: int) -> float:
			return (at % across - middleX) ** 2 + (at // across - middleY) ** 2

		nearest = min(ink, key=lambda at: (gap(at), -apart(pixels[at], paper)))
		seed = pixels[nearest]
		seedX = nearest % across
		seedY = nearest // across
		core = max(
			(
				at
				for at in ink
				if abs(at % across - seedX) <= 1
				and abs(at // across - seedY) <= 1
				and sameHue(pixels[at], seed)
			),
			key=lambda at: apart(pixels[at], paper),
		)
		colour = pixels[core]
		alike = [pixels[at] for at in ink if apart(pixels[at], colour) <= SAME_COLOUR]
		return (
			sum(pixel[0] for pixel in alike) // len(alike),
			sum(pixel[1] for pixel in alike) // len(alike),
			sum(pixel[2] for pixel in alike) // len(alike),
		)

	def ofOneColour(self, rgb: "tuple[int, int, int]", name: str = "") -> "Picture | None":
		"""A picture of just the parts of this one that are a given colour.

		A new picture of the same size whose greys are each pixel's distance from the colour,
		black at the colour and white at `COLOUR_REACH`, so every style draws it unchanged.
		Distance is on the channel that differs most, which is enough for colours chosen to be
		told apart.

		:param rgb: the colour to keep.
		:param name: what to call the result.
		:return: the picture, or None if this one has no colour to go on.
		"""
		pillow = _Image
		chops = _ImageChops
		if self.colour is None or pillow is None or chops is None:
			return None
		apart = chops.difference(self.colour, pillow.new("RGB", self.colour.size, tuple(rgb)))
		red, green, blue = apart.split()
		furthest = chops.lighter(chops.lighter(red, green), blue)
		grey = furthest.point([min(255, value * 255 // COLOUR_REACH) for value in range(256)])
		picture = Picture(
			bytearray(grey.tobytes()),
			self.width,
			self.height,
			name,
			image=grey,
			colour=self.colour,
		)
		picture.derivedFrom = self
		picture.fromScreen = self.fromScreen
		picture.onlyColour = (rgb[0], rgb[1], rgb[2])
		picture._content = self.content
		return picture

	def againstPaper(self) -> "Picture":
		""":return: this picture as how far each pixel is from the paper, for single lines.

		Single lines finds ink by tone, which misses a colour of the same brightness as the
		paper. So each grey is the pixel's distance from the paper colour, on the channel that
		differs most, taken from white. For black and grey on white the greys are unchanged.
		A picture without colour, or already of one colour, is returned as it is. Made once,
		with this picture's content box.
		"""
		if self.colour is None or self.onlyColour is not None or self.paper is None:
			return self
		if self._againstPaper is None:
			chops = _ImageChops
			pillow = _Image
			if chops is None or pillow is None:
				return self
			apart = chops.difference(self.colour, pillow.new("RGB", self.colour.size, self.paper))
			red, green, blue = apart.split()
			grey = chops.invert(chops.lighter(chops.lighter(red, green), blue))
			picture = Picture(bytearray(grey.tobytes()), self.width, self.height, self.name, image=grey)
			picture._content = self.content
			self._againstPaper = picture
		return self._againstPaper

	def channels(
		self,
		box: "tuple[int, int, int, int]",
		outWidth: int,
		outHeight: int,
	) -> "list[bytearray] | None":
		""":return: red, green and blue each reduced as `reduce` does, or None without colour.

		What lets outlines see an edge that exists only in colour. See `_edgeCells`.

		None too for a one-colour picture, whose edges are in its greys; it keeps the colour
		only to name what is under a finger.
		"""
		pillow = _Image
		if (
			self.colour is None
			or self.onlyColour is not None
			or pillow is None
			or outWidth <= 0
			or outHeight <= 0
			or not self._inside(box)
		):
			return None
		left, top, width, height = box
		reduced = self.colour.resize(
			(outWidth, outHeight),
			pillow.Resampling.BOX,
			box=(left, top, left + width, top + height),
		)
		return [bytearray(channel.tobytes()) for channel in reduced.split()]

	@property
	def spread(self) -> int:
		""":return: darkest to lightest, out of 255. See `PLAIN`."""
		return (max(self.greys) - min(self.greys)) if self.greys else 0

	@property
	def border(self) -> int:
		""":return: the average brightness around the edge of the picture.

		Fills a box that reaches beyond the picture, so the margin makes no false edge against
		it, as a constant would. Sampled from the outermost rows and columns.
		"""
		if not self.greys or not self.width or not self.height:
			return 0
		last = (self.height - 1) * self.width
		edge = list(self.greys[0 : self.width]) + list(self.greys[last : last + self.width])
		for y in range(self.height):
			base = y * self.width
			edge.append(self.greys[base])
			edge.append(self.greys[base + self.width - 1])
		return sum(edge) // len(edge)

	@property
	def strength(self) -> Strength:
		""":return: how much this picture has to say, measured once and kept.

		The yardstick every window is held against. See `MEANINGFUL`. Measured lazily and kept.
		"""
		if self._strength is None:
			self._strength = self._measure()
		return self._strength

	def _measure(self) -> Strength:
		""":return: the picture own strength, at `REFERENCE_CELLS`.

		From the strongest fiftieth; see `REFERENCE_SHARE`. The short axis is at least three
		cells, since a Sobel needs three and a wide picture would otherwise measure nothing and
		pass every window.
		"""
		if not self.greys or self.width < 2 or self.height < 2:
			return Strength(0, 0)
		if self.width >= self.height:
			across = min(REFERENCE_CELLS, self.width)
			down = max(1, round(across * self.height / self.width))
		else:
			down = min(REFERENCE_CELLS, self.height)
			across = max(1, round(down * self.width / self.height))
		across = max(across, min(3, self.width))
		down = max(down, min(3, self.height))
		greys = self.reduce((0, 0, self.width, self.height), across, down)
		cells = _edgeCells(self, (0, 0, self.width, self.height), greys, across, down)
		return Strength(_topOf([cell[0] for cell in cells]), _separation(greys))

	def reduce(self, box, outWidth: int, outHeight: int, background: "int | None" = None) -> bytearray:
		"""Average a rectangle of the pixels down to a grid of the given size.

		Area average rather than point sampling, which drops thin lines between samples.

		The box is honoured as given, even outside the picture, where the rest of each cell
		counts at the background value; `place` no longer asks for that.

		Pillow handles a box inside the picture. Its box filter splits a pixel straddling two
		cells by area, where the Python gives it whole to one, so single cells on line art can
		differ, but both draw the same shapes. The Python sums over slices for speed.

		:param box: the source rectangle, `(left, top, width, height)` in pixels. May lie
			partly or wholly outside the picture.
		:param outWidth: columns wanted.
		:param outHeight: rows wanted.
		:param background: what to count the margin as, or None for the picture's own border.
		:return: one average per output cell, row major.
		"""
		left, top, width, height = box
		width = max(1, width)
		height = max(1, height)
		out = bytearray(max(0, outWidth) * max(0, outHeight))
		if outWidth <= 0 or outHeight <= 0:
			return out
		pillow = _Image
		image = self._pillow() if self._inside(box) else None
		if image is not None and pillow is not None:
			return bytearray(
				image.resize(
					(outWidth, outHeight),
					pillow.Resampling.BOX,
					box=(left, top, left + width, top + height),
				).tobytes(),
			)
		if background is None:
			background = self.border
		greys = self.greys
		for oy in range(outHeight):
			y0 = top + oy * height // outHeight
			y1 = max(y0 + 1, top + (oy + 1) * height // outHeight)
			insideY0 = max(0, y0)
			insideY1 = min(y1, self.height)
			rowOut = oy * outWidth
			for ox in range(outWidth):
				x0 = left + ox * width // outWidth
				x1 = max(x0 + 1, left + (ox + 1) * width // outWidth)
				area = (x1 - x0) * (y1 - y0)
				insideX0 = max(0, x0)
				insideX1 = min(x1, self.width)
				total = 0
				found = 0
				if insideX1 > insideX0 and insideY1 > insideY0:
					for y in range(insideY0, insideY1):
						base = y * self.width
						total += sum(greys[base + insideX0 : base + insideX1])
					found = (insideX1 - insideX0) * (insideY1 - insideY0)
				total += background * (area - found)
				# Rounded, as Pillow's box filter rounds. Truncating put this a level darker.
				out[rowOut + ox] = min(255, (total + area // 2) // area) if area else background
		return out

	def _inside(self, box: "tuple[int, int, int, int]") -> bool:
		""":return: whether a source rectangle lies wholly on the picture."""
		left, top, width, height = box
		return (
			left >= 0
			and top >= 0
			and width >= 1
			and height >= 1
			and left + width <= self.width
			and top + height <= self.height
		)

	def reduceInk(
		self,
		box: "tuple[int, int, int, int]",
		outWidth: int,
		outHeight: int,
		dark: bool,
	) -> bytearray:
		"""Reduce so that a line of ink cannot be averaged away.

		The reduction for `STROKES`: an area average turns a thin pen line pale enough to drop.
		With Pillow, the ink is thickened before each halving (a 3 by 3 minimum filter for dark
		ink, maximum for light, then a 2 by 2 average), then box-shrunk to size; staged, since a
		wide filter costs the square of its width. `strokePins`' skeleton takes the thickness
		back off. Without Pillow, the most inked pixel of each cell, which is stricter.

		:param box: the source rectangle, inside the picture.
		:param outWidth: columns wanted.
		:param outHeight: rows wanted.
		:param dark: whether the ink is darker than its ground.
		:return: one value per output cell, row major.
		"""
		out = bytearray(max(0, outWidth) * max(0, outHeight))
		if outWidth <= 0 or outHeight <= 0:
			return out
		left, top, width, height = box
		width = max(1, width)
		height = max(1, height)
		pillow = _Image
		filters = _ImageFilter
		image = self._pillow() if self._inside(box) else None
		if image is not None and pillow is not None and filters is not None:
			image = image.crop((left, top, left + width, top + height))
			widen = filters.MinFilter(3) if dark else filters.MaxFilter(3)
			while True:
				acrossBy = 2 if image.width >= 2 * outWidth else 1
				downBy = 2 if image.height >= 2 * outHeight else 1
				if acrossBy == downBy == 1:
					break
				image = image.filter(widen).reduce((acrossBy, downBy))
			if image.size != (outWidth, outHeight):
				image = image.resize((outWidth, outHeight), pillow.Resampling.BOX)
			return bytearray(image.tobytes())
		pick = min if dark else max
		greys = self.greys
		for oy in range(outHeight):
			y0 = max(0, top + oy * height // outHeight)
			y1 = min(self.height, max(y0 + 1, top + (oy + 1) * height // outHeight))
			rowOut = oy * outWidth
			for ox in range(outWidth):
				x0 = max(0, left + ox * width // outWidth)
				x1 = min(self.width, max(x0 + 1, left + (ox + 1) * width // outWidth))
				if x1 <= x0 or y1 <= y0:
					# Only for a box off the picture, which nothing in the add-on asks for. No
					# ink there, whichever way round the ink is.
					out[rowOut + ox] = 255 if dark else 0
					continue
				out[rowOut + ox] = pick(
					pick(greys[y * self.width + x0 : y * self.width + x1]) for y in range(y0, y1)
				)
		return out


def _raiseTopmost(values, places, wanted: int, into: bytearray) -> int:
	"""Raise the pins with the largest values, and no more of them than asked for.

	Ties are common, as in stripes, so everything strictly above the cut is raised and the tie
	group at the cut is strided through in raster order to fill the rest, giving an even
	scattering rather than a block at the top.

	:param values: one per candidate.
	:param places: where each candidate is, in raster order.
	:param wanted: how many pins to raise.
	:param into: the pin array to raise them in.
	:return: how many were raised.
	"""
	if not values or wanted <= 0:
		return 0
	ordered = sorted(values, reverse=True)
	wanted = min(wanted, len(ordered))
	cut = ordered[wanted - 1]
	tied = []
	raised = 0
	for value, place in zip(values, places):
		if value > cut:
			into[place] = 1
			raised += 1
		elif value == cut:
			tied.append(place)
	room = wanted - raised
	if room <= 0 or not tied:
		return raised
	if room >= len(tied):
		for place in tied:
			into[place] = 1
		return raised + len(tied)
	step = len(tied) / room
	for n in range(room):
		into[tied[int(n * step)]] = 1
	return raised + room


def _gradients(greys, width: int, height: int) -> list:
	"""Sobel over a reduced grid.

	Sobel rather than a plain difference, for its smoothing against single noisy pixels. The
	magnitude is the sum of absolute gradients, which only slightly favours diagonals. Border
	cells are skipped.

	:param greys: one brightness per cell, row major.
	:param width: cells across.
	:param height: cells down.
	:return: magnitude, gx, gy and place for every cell with any gradient, in raster order.
	"""
	cells = []
	if width < 3 or height < 3:
		return cells
	for y in range(1, height - 1):
		above = (y - 1) * width
		here = y * width
		below = (y + 1) * width
		for x in range(1, width - 1):
			topLeft = greys[above + x - 1]
			topMid = greys[above + x]
			topRight = greys[above + x + 1]
			midLeft = greys[here + x - 1]
			midRight = greys[here + x + 1]
			lowLeft = greys[below + x - 1]
			lowMid = greys[below + x]
			lowRight = greys[below + x + 1]
			gx = (topRight + 2 * midRight + lowRight) - (topLeft + 2 * midLeft + lowLeft)
			gy = (lowLeft + 2 * lowMid + lowRight) - (topLeft + 2 * topMid + topRight)
			magnitude = abs(gx) + abs(gy)
			if magnitude:
				cells.append((magnitude, gx, gy, here + x))
	return cells


def _thin(cells, width: int, height: int) -> list:
	"""Keep only the crest of each ridge of gradient.

	Non-maximum suppression along the gradient, quantised to the lattice's four directions. It
	thins a broad response but does not merge a stroke's two edges, since thinning harder
	breaks contours; `STROKES` and `BRIGHTNESS` give one line. The quantisation uses 24 over 10
	for the tangent of 67.5 degrees, so this stays in integers.

	:param cells: from `_gradients`.
	:param width: cells across.
	:param height: cells down.
	:return: magnitude and place for the survivors, in raster order.
	"""
	magnitudes = [0] * (width * height)
	for magnitude, _gx, _gy, place in cells:
		magnitudes[place] = magnitude
	kept = []
	for magnitude, gx, gy, place in cells:
		across = abs(gx)
		down = abs(gy)
		if across * 10 > down * 24:
			stepX, stepY = 1, 0
		elif down * 10 > across * 24:
			stepX, stepY = 0, 1
		elif (gx > 0) == (gy > 0):
			stepX, stepY = 1, 1
		else:
			stepX, stepY = 1, -1
		x = place % width
		y = place // width
		before = 0
		after = 0
		if 0 <= x - stepX < width and 0 <= y - stepY < height:
			before = magnitudes[place - stepY * width - stepX]
		if 0 <= x + stepX < width and 0 <= y + stepY < height:
			after = magnitudes[place + stepY * width + stepX]
		# Strictly greater on one side, not less on the other, so a plateau keeps only its first
		# cell along the gradient rather than drawing one edge twice.
		if magnitude > before and magnitude >= after:
			kept.append((magnitude, place))
	return kept


def _followEdges(kept, cut: int, width: int, height: int) -> set:
	"""Grow the strong edges along themselves through the weak ones.

	Hysteresis: anything at half the cut or better that touches something already in joins it,
	so a contour that fades in places is not broken. Nothing weak is raised on its own.

	:param kept: magnitude and place, from `_thin`.
	:param cut: the threshold for a strong edge.
	:param width: cells across.
	:param height: cells down.
	:return: the places to raise.
	"""
	strong = set()
	weak = {}
	for magnitude, place in kept:
		if magnitude >= cut:
			strong.add(place)
		elif magnitude * 2 >= cut:
			weak[place] = magnitude
	frontier = list(strong)
	while frontier and weak:
		place = frontier.pop()
		x = place % width
		y = place // width
		for stepY in (-1, 0, 1):
			for stepX in (-1, 0, 1):
				nearX = x + stepX
				nearY = y + stepY
				if 0 <= nearX < width and 0 <= nearY < height:
					near = nearY * width + nearX
					if near in weak:
						del weak[near]
						strong.add(near)
						frontier.append(near)
	return strong


def _components(places, width: int, height: int) -> list:
	""":return: the connected groups among a set of raised places.

	Diagonal neighbours count, so a staircase contour stays in one piece.

	:param places: which cells are raised.
	:param width: cells across.
	:param height: cells down.
	"""
	left = set(places)
	groups = []
	while left:
		frontier = [left.pop()]
		group = set(frontier)
		while frontier:
			place = frontier.pop()
			x = place % width
			y = place // width
			for stepY in (-1, 0, 1):
				for stepX in (-1, 0, 1):
					nearX = x + stepX
					nearY = y + stepY
					if 0 <= nearX < width and 0 <= nearY < height:
						near = nearY * width + nearX
						if near in left:
							left.remove(near)
							group.add(near)
							frontier.append(near)
		groups.append(group)
	return groups


def _fitEdges(kept, cut: int, width: int, height: int, ceiling: int):
	"""Bring an edge map under the ceiling without taking a contour to pieces.

	Fitting pin by pin would break contours into pieces, undoing the hysteresis. So the
	threshold is raised and the contours regrown until they fit; failing that, whole contours
	are kept strongest first. Half a contour is never drawn.

	:param kept: magnitude and place, from `_thin`, in raster order.
	:param cut: the threshold the data chose, which is the lowest that will be tried.
	:param width: cells across.
	:param height: cells down.
	:param ceiling: the most that may be raised.
	:return: the places to raise, or None if no contour will fit at all.
	"""
	steps = sorted({magnitude for magnitude, _place in kept if magnitude >= cut})
	# A bounded sweep rather than a search: growing is not strictly monotonic in the threshold,
	# since raising it moves the weak band up as well as the strong one, so a bisection could
	# settle on the wrong side of a step it never looked at.
	tried = set()
	for n in range(24):
		at = steps[min(len(steps) - 1, n * len(steps) // 24)] if steps else cut
		if at in tried:
			continue
		tried.add(at)
		grown = _followEdges(kept, at, width, height)
		if len(grown) <= ceiling:
			return grown
	strongest = {place: magnitude for magnitude, place in kept}
	grown = _followEdges(kept, cut, width, height)
	groups = sorted(
		_components(grown, width, height),
		key=lambda group: max(strongest.get(place, 0) for place in group),
		reverse=True,
	)
	out = set()
	for group in groups:
		if len(out) + len(group) <= ceiling:
			out |= group
	return out or None


def _edgeCells(picture: "Picture", box: "tuple[int, int, int, int]", greys, width: int, height: int) -> list:
	"""Sobel over a reduction, in colour when there is colour.

	Two colours of the same brightness are one grey, so with colour the gradient is taken per
	channel and each cell keeps the strongest. Grey edges are found at least as strongly. The
	strength reference is measured the same way.

	:param picture: whose colour to use, if it has any.
	:param box: the source rectangle `greys` was reduced from.
	:param greys: the grey reduction, used when there is no colour.
	:param width: cells across.
	:param height: cells down.
	:return: as `_gradients`.
	"""
	channels = picture.channels(box, width, height)
	if channels is None:
		return _gradients(greys, width, height)
	strongest: dict = {}
	for channel in channels:
		for cell in _gradients(channel, width, height):
			held = strongest.get(cell[3])
			if held is None or cell[0] > held[0]:
				strongest[cell[3]] = cell
	return [strongest[place] for place in sorted(strongest)]


def edgePins(greys, width: int, height: int, coverage: float = EDGE_COVERAGE, cells=None) -> Rendering:
	"""Raise a pin wherever the picture changes fastest.

	The coverage is a ceiling, not a target; see the module docstring. The cut is Otsu over the
	gradient magnitudes, the contour is thinned to its crest and grown back along itself, and
	the ceiling only removes pins. Too little to feel is refused rather than topped up.

	:param greys: one brightness per cell, row major, already reduced to width by height.
	:param width: cells across.
	:param height: cells down.
	:param coverage: the most of the picture that may be raised.
	:param cells: the gradients already taken, from `_edgeCells`, or None to take them here.
	:return: the pins.
	"""
	pins = bytearray(width * height)
	if cells is None:
		cells = _gradients(greys, width, height)
	if not cells:
		return Rendering(pins, width, height, 0)
	kept = _thin(cells, width, height)
	if not kept:
		return Rendering(pins, width, height, 0)
	cut = _otsuOver([magnitude for magnitude, _place in kept])
	chosen = _followEdges(kept, cut, width, height)
	if not chosen:
		return Rendering(pins, width, height, 0)
	ceiling = max(1, int(round(width * height * coverage)))
	if len(chosen) > ceiling:
		chosen = _fitEdges(kept, cut, width, height, ceiling)
	if chosen:
		for place in chosen:
			pins[place] = 1
		return Rendering(pins, width, height, len(chosen))
	# Edge everywhere and no single contour small enough to keep whole -- a texture rather
	# than a drawing. Nothing can be both truthful and within the budget here, so the budget
	# wins and the result is an even scattering, which is an honest account of a texture. What
	# would not be honest is letting it pass for a drawing, so it is marked and said.
	values = [magnitude for magnitude, _place in kept]
	places = [place for _magnitude, place in kept]
	raised = _raiseTopmost(values, places, ceiling, pins)
	return Rendering(pins, width, height, raised, crowded=True)


def _otsu(greys) -> int:
	"""Split brightnesses into two classes at the point that separates them best.

	Otsu's method: the threshold maximising variance between the classes, in one pass over a
	256 bin histogram, since a picture's two tones are rarely either side of 128. Used for both
	the polarity and the cut; `brightnessPins` adds the ceiling.

	:param greys: brightnesses.
	:return: the threshold. Pixels below it are the dark class.
	"""
	histogram = [0] * 256
	for grey in greys:
		histogram[grey] += 1
	total = len(greys)
	if not total:
		return 128
	return _splitAt(histogram, total)


def _splitAt(histogram, total: int) -> int:
	"""Otsu method over an already built histogram.

	Shared, so brightnesses and gradient magnitudes are split by the same arithmetic.

	:param histogram: 256 counts.
	:param total: how many values went into it.
	:return: the bin above the split.
	"""
	overall = sum(value * count for value, count in enumerate(histogram))
	belowCount = 0
	belowSum = 0
	best = 0
	bestAt = 0
	for value in range(256):
		belowCount += histogram[value]
		if not belowCount:
			continue
		aboveCount = total - belowCount
		if not aboveCount:
			break
		belowSum += value * histogram[value]
		belowMean = belowSum / belowCount
		aboveMean = (overall - belowSum) / aboveCount
		between = belowCount * aboveCount * (belowMean - aboveMean) ** 2
		if between > best:
			best = between
			bestAt = value + 1
	return bestAt


def _otsuOver(values) -> int:
	"""The same split, over something that is not brightnesses.

	For gradient magnitudes, binned into 256 and scaled back. The lost precision does not
	matter, since edges and non-edges are far apart.

	:param values: whatever is being split.
	:return: the threshold, in the values own units.
	"""
	if not values:
		return 0
	top = max(values)
	if top <= 0:
		return 0
	histogram = [0] * 256
	for value in values:
		histogram[value * 255 // top] += 1
	return _splitAt(histogram, len(values)) * top // 255


def _separation(greys) -> int:
	""":return: how far apart a picture two tone classes are, at Otsu split between them.

	The brightness counterpart of edge strength: ink on paper separates by near the full
	range, faint background by about ten.

	:param greys: brightnesses.
	"""
	if not greys:
		return 0
	threshold = _otsu(greys)
	belowCount = 0
	belowSum = 0
	for grey in greys:
		if grey < threshold:
			belowCount += 1
			belowSum += grey
	aboveCount = len(greys) - belowCount
	if not belowCount or not aboveCount:
		return 0
	return int(abs((sum(greys) - belowSum) / aboveCount - belowSum / belowCount))


def brightnessPins(
	greys,
	width: int,
	height: int,
	invert: bool = False,
	most: float = BRIGHTNESS_MOST,
) -> Rendering:
	"""Raise the subject and leave the background down.

	Otsu splits the picture and the smaller class is the subject, since a thing is usually
	smaller than its background; `invert` swaps it. What is raised is what fell on the
	subject side, and the ceiling only removes. The subject keeps its own size, with no floor.

	:param greys: one brightness per cell, row major, already reduced.
	:param width: cells across.
	:param height: cells down.
	:param invert: raise the other side instead.
	:param most: the most of the picture that may be raised.
	:return: the pins.
	"""
	pins = bytearray(width * height)
	total = width * height
	if not total or not greys:
		return Rendering(pins, width, height, 0)
	threshold = _otsu(greys)
	subjectIsDark = subjectIsDarkIn(greys, invert)
	places = [place for place, grey in enumerate(greys) if (grey < threshold) == subjectIsDark]
	if not places:
		return Rendering(pins, width, height, 0)
	ceiling = max(1, int(round(total * most)))
	if len(places) <= ceiling:
		for place in places:
			pins[place] = 1
		return Rendering(pins, width, height, len(places))
	values = [(255 - greys[place]) if subjectIsDark else greys[place] for place in places]
	return Rendering(pins, width, height, _raiseTopmost(values, places, ceiling, pins))


def subjectIsDarkIn(greys: "bytes | bytearray", invert: bool = False) -> bool:
	""":return: whether the subject of a picture is its dark side.

	The smaller of Otsu's two classes, as in `brightnessPins`. Truer still of strokes, so
	`STROKES` has no reversed style.

	:param greys: brightnesses, reduced.
	:param invert: take the other side.
	"""
	if not greys:
		return not invert
	threshold = _otsu(greys)
	dark = sum(1 for grey in greys if grey < threshold)
	return (dark <= len(greys) - dark) != invert


def _ring(grid: bytearray, place: int, width: int, height: int) -> "tuple[int, ...]":
	""":return: the eight neighbours of a cell, clockwise from the one above it.

	The order Zhang and Suen number them in, P2 to P9, which is what makes `_skeleton` checkable
	against the paper. Off the grid counts as empty.
	"""
	x = place % width
	y = place // width
	up = y > 0
	down = y < height - 1
	left = x > 0
	right = x < width - 1
	return (
		grid[place - width] if up else 0,
		grid[place - width + 1] if up and right else 0,
		grid[place + 1] if right else 0,
		grid[place + width + 1] if down and right else 0,
		grid[place + width] if down else 0,
		grid[place + width - 1] if down and left else 0,
		grid[place - 1] if left else 0,
		grid[place - width - 1] if up and left else 0,
	)


def _skeleton(mask: "bytes | bytearray", width: int, height: int) -> bytearray:
	"""Pare a mask down to its centre lines, one cell wide.

	Zhang and Suen's thinning (1984), in two alternating passes so the result is centred. A
	cell may go only with two to six neighbours forming exactly one run, which keeps ends and
	connections. A 2 by 2 block can vanish entirely, so a piece left with nothing gets its most
	central cell back. Pure Python, on the reduced grid.

	:param mask: one byte per cell, non-zero for ink.
	:param width: cells across.
	:param height: cells down.
	:return: the centre lines, one byte per cell.
	"""
	grid = bytearray(1 if value else 0 for value in mask)
	inked = [place for place, value in enumerate(grid) if value]
	changing = True
	while changing:
		changing = False
		for firstPass in (True, False):
			doomed = []
			for place in inked:
				ring = _ring(grid, place, width, height)
				count = sum(ring)
				if count < 2 or count > 6:
					continue
				runs = sum(1 for n in range(8) if not ring[n] and ring[(n + 1) % 8])
				if runs != 1:
					continue
				p2, _p3, p4, _p5, p6, _p7, p8, _p9 = ring
				if firstPass:
					if (p2 and p4 and p6) or (p4 and p6 and p8):
						continue
				elif (p2 and p4 and p8) or (p2 and p6 and p8):
					continue
				doomed.append(place)
			for place in doomed:
				grid[place] = 0
			if doomed:
				changing = True
				inked = [place for place in inked if grid[place]]
	for group in _components([place for place, value in enumerate(mask) if value], width, height):
		if any(grid[place] for place in group):
			continue
		middleX = sum(place % width for place in group) / len(group)
		middleY = sum(place // width for place in group) / len(group)
		grid[
			min(group, key=lambda place: (place % width - middleX) ** 2 + (place // width - middleY) ** 2)
		] = 1
	return grid


def _onBoundary(mask: bytearray, place: int, width: int, height: int) -> bool:
	""":return: whether an inked cell touches the ground above, below or to either side.

	Four neighbours rather than eight, which is what makes the boundary one cell thick and
	still joined: a diagonal step is a corner of the shape, not a way out of it.
	"""
	x = place % width
	y = place // width
	return (
		y == 0
		or y == height - 1
		or x == 0
		or x == width - 1
		or not mask[place - width]
		or not mask[place + width]
		or not mask[place - 1]
		or not mask[place + 1]
	)


def strokePins(
	inked: "bytes | bytearray",
	width: int,
	height: int,
	dark: bool,
	coverage: float = EDGE_COVERAGE,
	plain: "bytes | bytearray | None" = None,
) -> Rendering:
	"""Raise each stroke of ink once, down its middle.

	For line art, where `EDGES` gives two rails per stroke. Otsu splits ink from ground and each
	piece of ink is pared to a skeleton, except a piece thicker than `SOLID`, which is drawn
	round as its boundary, since a filled shape's skeleton is a dot. The outlines ceiling
	applies; over it, whole strokes are kept largest first, as in `_fitEdges`, and `crowded`
	says so.

	:param inked: one value per cell, from `Picture.reduceInk`, so that thin lines survived.
	:param width: cells across.
	:param height: cells down.
	:param dark: whether the ink is the dark side, from `subjectIsDarkIn`.
	:param coverage: the most of the picture that may be raised.
	:param plain: the same cells from `Picture.reduce`, which is what how thick a piece of ink
		is gets measured on. See `SOLID`. None to measure on `inked`.
	:return: the pins.
	"""
	pins = bytearray(width * height)
	if not width or not height or not inked:
		return Rendering(pins, width, height, 0)
	threshold = _otsu(inked)
	mask = bytearray(1 if (grey < threshold) == dark else 0 for grey in inked)
	if not any(mask):
		return Rendering(pins, width, height, 0)
	if plain is None:
		body = mask
	else:
		cut = _otsu(plain)
		body = bytearray(1 if (grey < cut) == dark else 0 for grey in plain)
	lines = _skeleton(mask, width, height)
	places = []
	for group in _components([place for place, value in enumerate(mask) if value], width, height):
		spine = [place for place in group if lines[place]]
		if sum(body[place] for place in group) > SOLID * max(1, len(spine)):
			places.extend(place for place in group if _onBoundary(mask, place, width, height))
		else:
			places.extend(spine)
	places.sort()
	ceiling = max(1, int(round(width * height * coverage)))
	if len(places) <= ceiling:
		for place in places:
			pins[place] = 1
		return Rendering(pins, width, height, len(places))
	kept = 0
	for group in sorted(_components(places, width, height), key=len, reverse=True):
		if kept + len(group) <= ceiling:
			for place in group:
				pins[place] = 1
			kept += len(group)
	if kept * 2 >= ceiling:
		return Rendering(pins, width, height, kept, crowded=True)
	# Most of the ink is one tangle larger than the whole budget -- a scribble, a dense web of
	# crossing lines, a texture Otsu took for ink -- so keeping whole strokes keeps only the
	# specks round it. Found on forty crossing lines: one network over the ceiling and a
	# three pin fragment beside it, drawn as three pins. As with outlines, an even scattering
	# of all of it instead, marked, which tells the reader to magnify.
	pins = bytearray(width * height)
	return Rendering(
		pins, width, height, _raiseTopmost([1] * len(places), places, ceiling, pins), crowded=True
	)


def place(picture: Picture, box, width: int, height: int) -> Placement:
	"""Work out which pixels to draw and where on the panel they go.

	Keeps the picture's proportions, since the panel is over two to one, by shrinking the
	destination and leaving margin beside it. See `Placement`.

	:param picture: the captured pixels.
	:param box: the part of them wanted, which is clipped to the picture.
	:param width: pins across the panel.
	:param height: pins down.
	:return: the source rectangle and where its reduction sits.
	"""
	# Intersected with the picture rather than clamped into it. Moving the near edge and then
	# measuring from there slides a rectangle instead of cutting it: a box starting five pixels
	# left of a ten pixel picture came back as the whole picture rather than as the five
	# columns of it that the box actually covered, and a box entirely past the end came back as
	# a one pixel strip at the last pixel rather than as nothing.
	left, top, across, down = box
	right = int(left) + int(across)
	bottom = int(top) + int(down)
	left = max(0, int(left))
	top = max(0, int(top))
	right = min(picture.width, right)
	bottom = min(picture.height, bottom)
	across = right - left
	down = bottom - top
	if width <= 0 or height <= 0 or across <= 0 or down <= 0:
		return Placement((left, top, max(0, across), max(0, down)), 0, 0, 0, 0)
	if across * height >= down * width:
		# Wider than the panel: it gets the full width and the margin is above and below.
		outWidth = width
		outHeight = max(1, min(height, round(width * down / across)))
	else:
		outHeight = height
		outWidth = max(1, min(width, round(height * across / down)))
	return Placement(
		(left, top, across, down),
		(width - outWidth) // 2,
		(height - outHeight) // 2,
		outWidth,
		outHeight,
	)


def renderAt(
	picture: Picture,
	spot: Placement,
	mode: str = EDGES,
	invert: bool = False,
	whole: bool = True,
) -> Rendering:
	"""Work a placement out into pins.

	The one place refusals live, so every caller fails the same way. Each style's measure
	(edge strength or tone separation) is compared with the same measure over the whole
	capture; see `MEANINGFUL`. An empty whole picture is refused. An empty window is drawn
	and marked, so a zoom through the empty middle of a shape still works; see
	`Rendering.barren`.

	:param picture: the captured pixels.
	:param spot: which of them, and where they land.
	:param mode: `EDGES`, `STROKES` or `BRIGHTNESS`.
	:param invert: swap which side of a silhouette is raised.
	:param whole: whether this is the entire picture rather than a window of it.
	:return: the pins, positioned on the panel.
	:raises ImageRefused: if there is nothing here to draw and nothing to pan towards.
	"""
	if mode == STROKES:
		# Ink is whatever is not paper, which for a picture in colour is not the same as what
		# is darker. See `Picture.againstPaper`.
		picture = picture.againstPaper()
	if spot.width <= 0 or spot.height <= 0:
		# Translators: reported when a picture was asked for in no space at all.
		raise ImageRefused(_("There is no room here for a picture"))
	if min(spot.width, spot.height) < 3:
		# Something long and thin fits as a strip too shallow to draw in. Said here, more
		# usefully than the sparse refusal below.
		# Translators: reported when something is too long and thin to draw on the pins. The
		# placeholder is how many pin rows deep it would be.
		raise ImageRefused(
			_("This is too thin to draw; it would be {rows} rows deep").format(
				rows=min(spot.width, spot.height),
			),
		)

	def nothingHere(why: str) -> Rendering:
		"""Refuse the whole picture, or hand back a blank window that says so.

		Every way of finding nothing arrives here, so the answer depends only on whether this
		is the whole picture, not on which check noticed.
		"""
		if whole:
			raise ImageRefused(why)
		return Rendering(
			bytearray(spot.width * spot.height),
			spot.width,
			spot.height,
			0,
			left=spot.left,
			top=spot.top,
			barren=True,
		)

	greys = picture.reduce(spot.source, spot.width, spot.height)
	flat = not greys or (max(greys) - min(greys)) < PLAIN
	if flat and greys and mode == EDGES:
		# Flat in grey is not flat to outlines when there is colour: red on a green of the same
		# brightness is one grey and two colours. See `_edgeCells`.
		channels = picture.channels(spot.source, spot.width, spot.height)
		if channels:
			flat = all((max(channel) - min(channel)) < PLAIN for channel in channels)
	if flat:
		# A flat field, on which every threshold below would be arbitrary. See `PLAIN`.
		# Translators: reported when a picture has nothing in it to feel.
		return nothingHere(_("There is nothing in this picture to draw"))
	# Relative to the picture, or above a floor that stands on its own. The relative test is
	# what tells a subject from a backdrop; the floor is what stops one small very dark thing
	# elsewhere in the capture from making everything softer than itself look like backdrop.
	# See `SUBJECT_TONES`.
	strength = picture.strength
	if mode in (BRIGHTNESS, STROKES):
		# Strokes are ink, found by tone as a silhouette is, so they ask the silhouette's
		# question: are there two tones here at all.
		apart = _separation(greys)
		found = apart >= strength.separation * MEANINGFUL or apart >= SUBJECT_TONES
	else:
		cells = _edgeCells(picture, spot.source, greys, spot.width, spot.height)
		strongest = max((cell[0] for cell in cells), default=0)
		# Four times, because a clean step of n tones answers as 4n through a Sobel.
		found = strongest >= strength.gradient * MEANINGFUL or strongest >= 4 * SUBJECT_TONES
	if not found:
		# Translators: reported when what was pointed at holds nothing that can be drawn.
		return nothingHere(_("There is only background in this picture"))
	if mode == BRIGHTNESS:
		rendering = brightnessPins(greys, spot.width, spot.height, invert=invert)
	elif mode == STROKES:
		# Which side is ink is decided on the plain reduction, where the two tones are in their
		# true proportions. On the inked one they are not: thickening the ink is the point of it.
		dark = subjectIsDarkIn(greys, invert)
		inked = picture.reduceInk(spot.source, spot.width, spot.height, dark)
		rendering = strokePins(inked, spot.width, spot.height, dark, plain=greys)
	else:
		rendering = edgePins(greys, spot.width, spot.height, cells=cells)
	if whole and rendering.coverage < SPARSE:
		# Only of a whole picture: a sparse window still found something real.
		# Translators: reported when a picture came out as too few raised dots to feel.
		raise ImageRefused(_("There is too little in this picture to feel"))
	return rendering._replace(left=spot.left, top=spot.top)


def render(
	picture: Picture,
	box,
	width: int,
	height: int,
	mode: str = EDGES,
	invert: bool = False,
	whole: bool = True,
) -> Rendering:
	"""Work one rectangle of a picture out for one rectangle of pins.

	`place` and then `renderAt`, for callers that do not need the two rectangles.

	:param picture: the captured pixels.
	:param box: which part of them, as left, top, width and height.
	:param width: pins across.
	:param height: pins down.
	:param mode: `EDGES`, `STROKES` or `BRIGHTNESS`.
	:param invert: swap which side of a silhouette is raised.
	:return: the pins, positioned on the panel.
	:raises ImageRefused: if there is nothing in this part of the picture to draw.
	"""
	return renderAt(picture, place(picture, box, width, height), mode, invert, whole)


def windowOf(picture: Picture, box, left: float, top: float, across: float, down: float) -> tuple:
	"""Narrow a source box to a fraction of itself.

	From the graphics mode's fractions. Here rather than in the mode, so the mode does not
	learn that a picture is pixels.

	:param picture: the captured pixels.
	:param box: the box the whole drawing covers.
	:param left: how far across the whole the window starts, 0 to 1.
	:param top: how far down, 0 to 1.
	:param across: what fraction of the width it covers.
	:param down: what fraction of the height.
	:return: the narrowed source rectangle.
	"""
	wholeLeft, wholeTop, wholeWidth, wholeHeight = box
	return (
		wholeLeft + int(wholeWidth * max(0.0, left)),
		wholeTop + int(wholeHeight * max(0.0, top)),
		max(1, int(wholeWidth * min(1.0, max(0.0, across)))),
		max(1, int(wholeHeight * min(1.0, max(0.0, down)))),
	)
