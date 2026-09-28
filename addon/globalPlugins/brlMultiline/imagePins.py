# BrlMultiline: turning a picture into pins.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Greyscale pixels in, raised pins out.

Knows nothing about NVDA, about the screen it came from, or about the display it is going to.
Numbers and a buffer to draw on, which is the same seam `chart.py` sits on and for the same
reason: the arithmetic here is the part most likely to be wrong in a way a reader cannot see,
so it has to be testable on a list of numbers.

**Reduce first, then detect.** The order is the whole of the design and it is not the obvious
one. Finding edges at capture resolution and then shrinking the edge map is what suggests
itself, and it destroys exactly what it found: at four pixels to a pin, reducing by "was there
an edge in these four" turns every textured region into a solid block of raised pins, and
reducing by averaging turns a one pixel line into a quarter-grey that then fails any threshold.
So the greys are reduced by area average to exactly the size being drawn and the detector runs
on that. Area averaging is a low-pass filter, which is what belongs in front of a derivative
anyway, and the edges come out one pin thick because the image they were found in was one pixel
per pin.

It also makes zoom uniform. A window is a crop of the pixels, reduced to the panel, detected —
so nothing here has to know whether it is drawing the whole picture or a tenth of it.

**A ceiling, not a quota, and the difference cost a reader a panel.** "Which pixels are edge
enough to raise a pin" has no answer in the pixels, and the first answer here was to stop asking:
sort the gradients, take the strongest sixth of the panel, and let the density be right by
construction. That reasoning holds for a photograph, where the edge count is genuinely unknown
and a fixed threshold gives either a solid panel or an empty one. It fails for everything else,
because a quota has to be met and a picture that has not got that much in it will have the
shortfall made up out of whatever came next.

What came next, on the drawing this was found with: the outline widened from one pin to five
until the hexagon corners rounded off, the seam between the picture and its own letterbox became
two full height vertical lines, and a zoom into the blank middle came back as a panel of stipple
with the subject nowhere on it. Three complaints, one cause, and every one of them drawn as
confidently as something real.

So the cut is now taken from the data — Otsu over the gradient magnitudes for `EDGES`, class
membership for `BRIGHTNESS` — the coverage may only ever remove pins, and a part of a picture
that holds nothing is refused rather than filled. What is lost is the guarantee of a constant
density; what is gained is that the density now means something, since it is how much was found
rather than how much was demanded.

**A window is judged against the picture it came from.** Every detector here returns a best
answer for whatever it is handed, and blank paper has a best answer too. See `MEANINGFUL`.

**Pillow for the pixels, Python for the pins.** The work falls into two sizes. Everything that
touches every captured pixel -- colour to grey, shrinking a region to the panel, thickening ink
so a shrink cannot lose it -- is done by Pillow when it is there, because a third of a megapixel
is a lot of Python. Everything after the reduction -- Sobel, thinning, hysteresis, Otsu,
skeletons, the ceilings -- runs on a grid the size of the panel, a few thousand cells, and stays
here in Python: it is milliseconds either way, it is where every decision this module makes
lives, and it has to read as arithmetic.

Pillow is in installed NVDA builds (2026.2 ships Pillow 12.2.0, and the 2026.3 lock file has
12.3.0) but NVDA's own code does not import it. It is in NVDA's build environment for the system
tests, as a dependency of Robot Framework's screen capture library, and py2exe packs what the
bundled code imports -- `wx.lib.agw.shapedbutton`, which NVDA ships, imports it optionally. It is
therefore **not part of NVDA's add-on API**: no deprecation cycle covers it, and a change to
NVDA's build could remove it or change its version. So every use here is behind
`_Image is None`, and the pure Python path it replaces is kept whole, tested, and is what an
NVDA without Pillow gets. The two agree on everything the tests measure.

numpy is a different case and stays out entirely. It is importable in an NVDA run from source,
as an optional dependency of comtypes, and `source/setup.py` lists it under `excludes`, so it is
missing from every installed copy -- the worst shape a dependency can take, since it works
everywhere the author checks and nowhere a reader runs it.
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

What `EDGES` cannot give. A drawn line has two edges, one on each side of the ink, so outlines
feel every stroke as a pair of rails -- see `_thin` for why they must. This finds the ink, as
`BRIGHTNESS` does, and then pares it down to a centre line one pin wide, so a hexagon drawn
with a thick pen is six sides under the finger rather than twelve.
"""

BRIGHTNESS = "brightness"
"""Silhouette: the dark against the light, or the light against the dark."""


EDGE_COVERAGE = 0.16
"""What fraction of the panel an outline should raise.

A sixteenth of a panel is too sparse to make a shape and a third is a texture rather than a
picture. Sixteen per cent is roughly what a hand reads as a drawing with white space in it,
and it is the number most worth changing on the evidence of a finger.
"""

BRIGHTNESS_MOST = 0.55
"""The most of the panel a silhouette may raise.

A ceiling and deliberately no floor, which is what `EDGE_COVERAGE` is too. It was not always:
outlines used to meet a quota, and the difference cost a reader a hexagon whose sides had been
widened until they met. Both are now limits on how much may be raised and neither is a target.

**No floor**, because the only way to raise more pins than the subject has is to raise pins the
subject is not on, and background raised to meet a quota is a lie about where the thing is. A
subject that comes out as thirty pins is a small subject; a bird in a wide sky should read as a
bird in a wide sky, and if there is genuinely too little to feel that is what the refusal is
for.

**A ceiling**, because at some point a silhouette stops having an outline to follow and becomes
a panel that is simply up. Past this the most extreme pins on the subject's side are kept and
the rest of them let go, which leaves a mass with holes in it — not lovely, honest, and rare,
since a picture more than half subject is usually one the reader wants reversed anyway.
"""

PLAIN = 10
"""How much darkest-to-lightest spread a picture needs before it is worth drawing.

Out of 255. Below this there is nothing in the picture to find, and every threshold in here
would then be arbitrary: a flat field has no edges, and Otsu on a single-valued histogram
splits noise. Refused up front rather than drawn as an empty or a solid panel, because a reader
running a hand over either cannot tell it from a display that has stopped working.
"""

MEANINGFUL = 0.15
"""How much of the whole picture's strength a window must carry to be worth drawing.

**A window is judged against the picture it came from, not against itself.** Every detector
here finds a best answer in whatever it is handed, and a patch of empty paper has a best answer
too — so zooming into the blank middle of a drawing used to come back as a panel of stipple,
confidently drawn, with the subject nowhere on it. Asked instead whether this window holds
anything like what the picture holds, the same window answers no.

A sixth, because the gap it has to straddle is wide and the cost of the two mistakes is not
equal. Measured on a line drawing with a faint hatched background: ink reads 1282 and the
hatch 68, five per cent of it. Refusing a window that had something in it costs a reader one
keypress and a sentence saying why; drawing one that had nothing costs them a panel they will
read as the picture.
"""

SUBJECT_TONES = 20
"""A tone difference this size is something a picture is saying, whatever else is in it.

The escape hatch from judging a window only against the rest of the picture. One tiny patch of
pure black in a corner should not make the rest of a photograph look like background, and with
a purely relative test it does: a feature twenty-five levels darker than its surroundings was
refused in both styles because something unrelated elsewhere was two hundred levels darker.

Twice `PLAIN`, so it sits just above what this module already calls flat, and stated in tones
so that both styles can express their own floor in terms of it.
"""

REFERENCE_SHARE = 0.02
"""How much of the picture the strength reference is taken from, strongest first.

Not the single strongest gradient, which is one cell and therefore one accident: a four pixel
speck of pure black anywhere in a capture set the yardstick for everything else in it. The
strongest fiftieth is still firmly inside the picture's real structure and no longer moves when
one cell does.
"""

REFERENCE_CELLS = 48
"""How finely the whole picture is measured when working out what its strength is.

Fixed, and deliberately not the panel size: the reference has to mean the same thing at every
zoom, so it cannot be taken at whatever reduction the window happens to want. Forty-eight is
close enough to a panel's forty rows that gradients come out the same order of magnitude, and
small enough that measuring costs a few thousand cells once per capture.
"""

SOLID = 4
"""How thick, in pins, a piece of ink has to be before `STROKES` draws round it, not through it.

Measured as its area over the length of its skeleton, which is its average width, and the area
is taken from the plain reduction rather than from `Picture.reduceInk`. Thickening is what that
reduction is for, and measured after it a pen stroke three pins wide came back as five and was
drawn round as a band, rails and all -- the one thing this style exists not to do. On the plain
reduction a fine line has hardly any area, a pen stroke has its own, and a filled square is off
the scale altogether: its skeleton is a dot, so its width comes out as its whole area.

At four a stroke is a band a fingertip can feel the width of, and a band has two sides worth
feeling.
"""

TRIM_KEEPS = 0.85
"""Past this share of the picture, content is left untrimmed.

Trimming a picture that is nearly all content moves its drawing by a pin or two and gains
nothing a hand could notice, while making the same picture sit differently from one capture to
the next. So a trim that would keep more than this of the area is not made.
"""

TRIM_MARGIN = 0.04
"""How much room to leave round trimmed content, as a share of its longer side.

Not none, because outlines never raise the outermost ring of the panel -- a Sobel has nothing
beyond the border to compare with -- and content trimmed flush would lose its own outer edge.
Four per cent is about a pin and a half at fit, which clears the ring with room to spare.
"""

COLOUR_REACH = 85
"""How far a colour may be from the one asked for and still count as it, per channel.

Out of 255, on whichever of red, green and blue differs most. A plotted line is drawn
anti-aliased and a photograph is compressed, so the colour a reader touched is really a small
cloud of colours; eighty-five takes in the cloud and not the neighbouring series. Distances are
scaled so this one lands at full white, and the usual Otsu split then decides where the colour
ends -- which is what lets the edge pixels of a line fall on the side they mostly belong to.
"""

INK_FROM_PAPER = 40
"""How far a pixel must be from the paper, on its most different channel, to count as ink.

What `Picture.colourAt` asks before naming a colour under a finger. Less than this and the
finger is on paper, and the paper's colour is the true answer. JPEG noise and anti-aliased
fringes stay under it; any line a hand would want named is well over it.
"""

SAME_HUE = 0.95
"""How closely two colours must point the same way from the paper to be one stroke's.

A cosine, between the two colours each taken as a step away from the paper. An anti-aliased
fringe is a mix of a stroke and the paper, so it points exactly the stroke's way and differs
only in how far; JPEG noise moves it a little. Red and blue on white are at about 0.63, and
black and red at about 0.86, so this keeps the one and parts the others.
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

	Raised rather than returning None, because every case carries a reason the reader can act
	on — nothing in the picture, nothing left after reducing it — and a caller that only knew
	it had failed could not tell them which.
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

	**Two rectangles rather than one.** An earlier version said all of this with a single
	source rectangle that was allowed to reach outside the picture, so that the part hanging
	over the edge became the margin. It fitted correctly and it put the margin somewhere it
	did not belong: into the detector. A letterbox filled with the picture's own border tone
	still meets the picture's own edge pixels along a seam, and on a drawing whose background
	texture ran out to its boundary that seam measured about one per cent of a real edge —
	which a coverage quota, having run out of genuine edges, promoted to two full height
	vertical lines down a panel that had no such lines in it.

	Nothing is wrong with the arithmetic of growing a box. What is wrong is asking a detector
	about pins the add-on invented. So the source rectangle now always lies inside the
	picture, the destination says where its reduction sits on the panel, and the margins are
	never reduced, never detected, and never counted towards how full the panel may be.
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

		What a coverage limit is a fraction of. Counting the whole panel instead would let a
		square picture on a panel over twice as wide raise every pin it owns and still be
		under a sixth of the panel.
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

	Only ever true of a *window* of a picture that drew. The distinction is the whole reason
	this exists: a blank panel a reader cannot account for is indistinguishable by touch from
	a display that has stopped working, so an empty capture is still refused outright. But
	once the whole picture has drawn, the display has demonstrated that it works, and a blank
	window is then a fact about the picture rather than a possible fault -- the middle of a
	hexagon really is empty, and a reader zooming towards an edge has to be able to pass
	through it to get there.

	So it is drawn, and it is announced. What must not happen is that it is drawn silently.
	"""

	crowded: bool = False
	"""Whether the picture holds more edge than the panel can carry, so this is a texture.

	A floor plan, a grid, a page of small print: the structure is real and there is simply more
	of it than the pins can say. Neither raising the threshold nor dropping whole contours gets
	it under the ceiling, so what is drawn is an even scattering -- which is an honest account
	of a dense texture and a dishonest one of a contour, and a hand cannot tell those apart.

	So it is announced, for the same reason `barren` is. The reader is told the picture is too
	detailed at this size, which is also what to do about it: magnify, and the same pins then
	cover less of the picture until its lines come apart from each other.
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

	**Not NVDA's `screenBitmap.rgbPixelBrightness`.** That one weights blue at 0.3 and red at
	0.11, which is the standard luma formula with its red and blue terms exchanged; on anything
	where the subject is told from its background by being red or blue — a red line on a chart,
	a blue link underline — it gets the contrast backwards. The weights here are Rec. 601 as
	written.

	**Written as Pillow's own integer formula, so the two paths agree to the level.** Pillow's
	`convert("L")` applies the same weights in 16 bit fixed point, rounded; the floating point
	version this used to be truncated instead, which put a Python capture one level darker than
	a Pillow one on about half of all pixels. Nothing a hand could feel, but a threshold sitting
	on that level would have raised different pins depending on which NVDA the reader had.

	The path without Pillow. `pictureFromBgrx` is the one with it.

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

	**The shrink to `keep` is done here, by area, and not by the screen copy.** Windows can
	shrink while it copies, and NVDA's `ScreenBitmap` asks it to, but a memory device context
	stretches in the "black on white" mode unless told otherwise: the pixels a shrink drops are
	combined with a bitwise AND of their colour values. That keeps a black line on white, and
	does something with no name to a red line on blue. A box average is the right answer for
	every colour, so the capture is taken near full size and averaged down here.

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

	Never enlarged. Upscaling a small image before reducing it adds no detail and costs the
	reduction real time, and the pixels it invents are the ones a reader would then be feeling.

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

	**Transparency is laid on white.** A picture with see-through parts -- most icons, logos
	and diagrams on the web -- stores some colour behind the transparency, very often black,
	and dropping the transparency hands that colour over as though it were drawn. A black logo
	on a transparent ground would arrive as black on black and draw as nothing. Laid on white,
	it is what the page showed, since white is what the page usually was.

	**A photograph is turned the way it was taken.** Cameras store a photo sideways and record
	which way up it goes; the picture is turned by that record first, so what reaches the pins
	is what anybody looking at it sees.

	**The size kept is chosen after turning, here, and not by the caller.** It used to be passed
	in, worked out from the stored size -- so a photo stored 60 wide by 30 and turned to 30 by
	60 was then squeezed back into 60 by 30, a picture of something else. Taking a pixel budget
	instead of a size means the size can only ever be measured on the picture as it will be
	drawn.

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

	Read from `tobytes` rather than `getdata`, which Pillow 12 deprecates and says it will
	remove in Pillow 14. NVDA can move to a new Pillow in any release without telling an
	add-on, since Pillow is not part of its add-on API, so nothing here may lean on a call
	already marked to go.
	"""
	raw = image.tobytes()
	return list(zip(raw[0::3], raw[1::3], raw[2::3]))


def _topOf(values, share: float = REFERENCE_SHARE) -> int:
	""":return: the value at the top `share` of a list, or 0 if it is empty.

	A percentile written out, so that a reference cannot be moved by a single cell. With the
	default share and a forty-eight square reference grid this is about the fortieth strongest
	gradient in the picture.

	:param values: the measurements.
	:param share: how much of the top to look past.
	"""
	if not values:
		return 0
	ordered = sorted(values, reverse=True)
	return ordered[min(len(ordered) - 1, int(len(ordered) * share))]


class Picture:
	"""Captured pixels, at whatever size they were captured.

	Held rather than reduced once, because it is what makes zoom worth doing. A picture reduced
	straight to the panel would make the panel everything that was ever known about it, and
	zooming could then only make each dot bigger. Keeping the pixels means a reader looking at a
	quarter of the picture gets that quarter reduced from its own pixels — genuinely more
	detail, up to the resolution it was captured at, and no further.
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

		**Blank margins cost the reader pins.** A diagram exported with a wide white border, a
		logo in the middle of a banner, a chart with padding: the picture is fitted to the
		panel whole, margin and all, so the part that matters arrives smaller than it needed to
		be. The box the drawing starts from is cut down to what differs from the picture's own
		border tone, with `TRIM_MARGIN` left round it.

		"Differs" means by `SUBJECT_TONES`, the tone difference this module already treats as
		something the picture is saying. That is above faint background texture -- measured on
		the hexagon fixture, its hatch stays within ten levels of the border, and the ink starts
		past fifteen -- and above JPEG noise.

		The pixels are all kept; only where the drawing starts moves. A routing press still
		says where it is in the whole capture, and a picture of one colour of this one keeps
		this box, so that it lands where the picture it came from did.

		Measured once and kept. Pillow finds the box in one call; without it, each row and each
		column is tested as a slice.
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

		**One stroke's colour, never a blend of two.** A press covers the pin and one pin round
		it, and on a chart that area can hold two series. An earlier version averaged the
		strongest ink anywhere in it, and a red line two pixels from a blue one came back as a
		purple neither of them is -- which "draw only this colour" then drew as nothing. The
		rule is now:

		1. **The ink nearest the middle of the press says which stroke is meant.** Nearest by
		   position; between two equally near, the stronger.
		2. **Its strongest neighbour of the same hue is the stroke's own colour.** The nearest
		   ink is often the anti-aliased fringe of a line, a mix of the line and the paper. A
		   mix lies on the way from the paper to the ink, so it points the same way from the
		   paper as the ink does, only less far; so the strongest pixel within one of it that
		   points the same way is the core. Another colour points another way and is never
		   taken, however close.
		3. **The answer is the average of the pixels close to that core colour**, within
		   `SAME_COLOUR`, so neither the fringe nor a neighbour of another colour is in it.

		Less than `INK_FROM_PAPER` from the paper anywhere in the area and there is no ink here,
		and the paper's own colour is the answer. Two strokes of the same hue that differ only
		in how dark they are -- a grey grid line beside a black one -- are still told apart,
		because step 3 averages only colours close to the one found in step 2.

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

		**Made as a new picture of the same size, so every style works on it unchanged.** Its
		greys are how far each pixel is from the colour -- the colour itself black, everything
		`COLOUR_REACH` away or further white -- so the colour becomes dark ink on white paper,
		and outlines, single lines and brightness all draw it with nothing new to learn. A
		chart's one series comes out as that one line; a map's rivers as the rivers.

		Distance is on whichever of red, green and blue differs most. Crude beside a proper
		perceptual measure, and enough for what readers point this at, which is colours chosen
		to be told apart.

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

		**A stroke is ink that is not paper, whatever its brightness.** Single lines finds its
		ink by tone, and tone is brightness: a red stroke on a green ground of the same
		brightness is one flat grey, and the style said there was nothing to draw while
		outlines, which look at colour, found the shape. Found by review. So for a picture that
		kept its colour, single lines works on a picture whose greys are each pixel's distance
		from the paper colour, on the channel that differs most, taken from white: paper is
		white, and anything drawn on it is dark in proportion to how different it is.

		**Nothing changes for a drawing in black and grey on white.** A grey of g is 255 - g
		from white on every channel, so its distance taken from white is g again: the greys are
		the same numbers they were. A picture without colour, and a picture that is already one
		colour of another, is returned as it is.

		Made once and kept, with this picture's content box, so it lands where this one does.
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

		None for a picture of one colour of another, too. It carries the original's colour so
		that a routing press can still name what is under the finger, but its edges are the
		edges of the one colour, which are in its greys and nowhere else.
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

		What the margin outside the picture is filled with when the box asked for reaches
		beyond it. **The value matters, and a constant would be wrong.** Padding with white
		puts a hard step all the way round a dark photograph, and the edge detector, asked for
		the strongest sixth of the panel, would spend a good deal of it drawing a rectangle
		that is not in the picture — a frame the reader would feel as confidently as anything
		real. Matching the picture's own border means the margin has no gradient against it,
		so nothing is found there and the shape of the picture is what is left.

		Sampled from the outermost row and column on each side rather than from the whole
		picture, because it is the boundary the margin has to be continuous with.
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

		The yardstick every window is held against, which is the whole point of measuring it
		on the picture instead of on the window: a detector asked what the strongest thing in
		a patch of blank paper is will answer, and be believed. See `MEANINGFUL`.

		Measured lazily because a picture that is never drawn should cost nothing, and kept
		because a reader zooming is asking the same question of the same pixels repeatedly.
		"""
		if self._strength is None:
			self._strength = self._measure()
		return self._strength

	def _measure(self) -> Strength:
		""":return: the picture own strength, at `REFERENCE_CELLS`.

		**Taken from the strongest fiftieth rather than from the strongest cell**, because one
		cell is one accident. See `REFERENCE_SHARE`.

		**The short axis is never allowed to collapse.** Keeping the proportions and putting
		`REFERENCE_CELLS` on the long side gives a toolbar 900 by 36 a reference grid two rows
		tall, and a Sobel needs three -- so the strongest gradient in the picture came back as
		nought, every window compared itself against nought and passed, and the check that was
		supposed to catch a window of pure background was silently off for every wide picture
		on the screen. It did not announce itself as a failure because its failure mode is to
		permit, which is exactly the kind of broken that gets shipped.

		So the short axis is floored at three cells, or at whatever the picture has if it has
		fewer. That distorts the reference grid for a very wide picture, which costs nothing:
		this is one number to hold windows against, not something anybody feels.
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

		Area average rather than point sampling, and the difference is not subtle at these
		ratios. Sampling one pixel in sixteen drops a one pixel line entirely whenever it falls
		between the samples, so a diagram's strokes come and go along their own length —
		visibly wrong to a hand, and wrong differently each time the window moves.

		**The box is honoured as given, including where it reaches outside the picture.**
		Nothing in the add-on asks for that any more -- `place` keeps the source inside the
		picture and letterboxes by shrinking the destination instead -- but the behaviour is
		kept, and the history is worth having. Fitting used to be done by growing the box
		outwards, and an earlier version of this clipped the growth away again before reducing,
		so the whole picture was resized to the whole panel: a square feature came out 33 pins
		by 14 and a circle was an ellipse. No test of the fitting alone could see it, because
		the fitting was giving the right answer to a question this then declined to be asked.
		That is why the tests for shape measure the pins that were raised.

		Each output cell averages whatever part of it falls on the picture and counts the rest
		at the background value, which is why the count is over the cell whole area rather than
		over the pixels found in it.

		**Pillow when it is there, for a box inside the picture**, which is every box the add-on
		asks for. Its box filter is the same area average with one difference worth knowing:
		it weights a pixel that straddles two cells by how much of it falls in each, where the
		Python below gives it whole to one. Where every cell is a whole number of pixels the two
		agree to within one level, which is Pillow's fixed point rounding. Where cells split
		pixels they differ at the boundaries, and on line art by more than that suggests: a one
		pixel line on a boundary is shared between two cells by Pillow and given to one by
		Python, so a single cell can differ by a quarter of full scale. Measured on the hexagon
		fixture fitted to forty pins, the average cell is four levels apart and both draw the
		same hexagon. A box reaching outside the picture goes the Python way, which is the one
		that knows about `background`.

		The Python inner sum is taken over a slice so that the per-pixel work happens below
		Python. A capture of a few hundred thousand pixels reduces in milliseconds that way and
		in a noticeable pause without it.

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

		`reduce` is right for edges and silhouettes and wrong for a thin line: at eight pixels
		to a pin, a one pixel pen stroke is an eighth of each cell it crosses and averages to
		a pale grey that no threshold will call ink. A diagram drawn with a fine pen comes out
		with gaps along its own lines, and a gap in a line is the one thing a hand cannot work
		around. This is the reduction for `STROKES`, where the only question asked of a cell
		is whether a stroke went through it.

		**Thickened, then shrunk.** The ink is widened before each halving, so that nothing
		one pixel wide is ever halved: a 3 by 3 minimum filter for dark ink, maximum for light,
		then a 2 by 2 average, repeated until the next halving would overshoot, then a box
		shrink to size. Measured at fifteen pixels to a pin, a one pixel line comes out two pins
		wide at about a third of full brightness, where a plain average leaves it paler than
		200 and gone. The thickness is what the skeleton in `strokePins` takes back off, so it
		costs nothing except that two strokes closer together than a pin merge into one --
		which they would have done on the pins anyway.

		Staged rather than one wide filter, because a minimum filter costs the square of its
		width for every pixel, and fifteen pixels to a pin would be two hundred and twenty-five
		comparisons each. Each stage works on a quarter of the pixels the last one did.

		**Without Pillow, the most inked pixel of each cell**, taken over slices as `reduce`
		takes its sums. Not the same arithmetic and asking the same question, and it is the
		stricter of the two: one stray dark pixel inks a whole cell. On line art that is what
		is wanted, and on a photograph no stroke style was going to help.

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

	**The ties are the whole difficulty, and they are not a corner case.** A picture of stripes
	has thousands of pixels with exactly the same gradient; a black shape on white has every
	background pixel at exactly the same brightness. Cutting at "the value the wanted number
	are at or above" then lets the entire tie group through, and a budget of a sixth of the
	panel quietly becomes six sixths of it — which is the one outcome the budget exists to make
	impossible, arrived at by the mechanism meant to prevent it. Found by a test of a striped
	image, which came back with 86 per cent of the panel raised.

	So everything strictly above the cut is raised, and the tie group at the cut is **strided
	through in raster order** to fill whatever room is left. Striding rather than taking the
	first of them, because the first of them are the top rows: a uniform texture would come out
	as a solid block at the top of the panel and nothing below it, which is a picture of
	something that is not there. Strided, it comes out as an even scattering, which is what a
	uniform texture honestly is.

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

	A derivative with a little smoothing across it, and the smoothing is why it is Sobel
	rather than a plain difference: a single noisy pixel in a photograph is a large difference
	and is not an edge, and at this reduction one stray raised pin in open space is as loud to
	a finger as a whole stroke.

	The magnitude is the sum of the absolute gradients rather than the square root of the sum
	of their squares. The two differ by at most a factor of the square root of two, they
	differ most on exact diagonals, and what follows is largely a rank -- so the only thing
	the cheaper measure changes is a slight preference for diagonals over axis-aligned
	strokes, which on a panel where a diagonal has to fight the pin lattice is the right way
	round anyway.

	The border cells are skipped, so nothing is ever found on the outermost ring.

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

	Non-maximum suppression: a cell survives if nothing directly across the edge from it --
	along its own gradient, quantised to one of the four directions the lattice has -- is
	stronger. A stroke two or three cells wide produces gradient across all of them, and this
	reduces that to the one in the middle.

	**It thins a broad response; it does not merge two edges into one.** A black stroke on a
	light page has two genuine transitions, light to dark on the way in and dark to light on
	the way out, and they are different edges with opposite gradient. A reader feeling an
	outlined shape in this style is feeling the outline of the outline, and the way to get one
	line is to raise the ink instead, which is what `BRIGHTNESS` does. Written down because the
	alternative -- thinning harder until the pair collapses -- breaks the contour somewhere
	else along its length, and a broken contour is worse than a doubled one: a hand can follow
	a thick line, and cannot follow a line that stops.

	The quantisation compares against the tangent of 67.5 degrees as the rational 24 over 10,
	so the whole of this stays in integers.

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
		# Strictly greater on one side and merely not less on the other. Equal on both sides
		# kept them both, so a single clean light-to-dark step came back as two raised columns
		# rather than one -- which is not the doubled contour of a thick stroke, it is one edge
		# drawn twice. The asymmetry breaks a plateau deterministically in favour of its first
		# cell along the gradient, which is a choice rather than an answer, but a plateau has
		# no true crest to find and drawing one of them beats drawing all of them.
		if magnitude > before and magnitude >= after:
			kept.append((magnitude, place))
	return kept


def _followEdges(kept, cut: int, width: int, height: int) -> set:
	"""Grow the strong edges along themselves through the weak ones.

	Hysteresis. A contour does not have one strength along its length -- a stroke fades where
	it is thin, where it crosses something, where the reduction caught it between cells -- and
	a single threshold breaks it exactly there. A break is the one artefact a hand cannot work
	around: a reader following a line to a corner and finding it stop has been told something
	false about the shape, and has no way to find where it starts again.

	So anything at half the cut or better, touching something already in, joins it. Nothing
	weak is raised on its own account.

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

	Touching counted diagonally, because a contour on a pin lattice climbs in steps and a
	staircase that only counted as connected orthogonally would be in pieces.

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

	**A ceiling applied pin by pin undoes the hysteresis that just ran.** Ranking every chosen
	cell and keeping the strongest of them is the obvious way to fit a budget and it is the one
	thing this must not do: on a dense diagram it turned one connected edge map of 2,612 pins
	into 614 pins in 207 pieces. A hand following a line through that finds it stop, start
	again a little further on, and stop again -- which says something about the picture that is
	not true, and is worse than a line that was never drawn.

	So density is bought by asking for fewer edges, not by deleting parts of the edges there
	are: the threshold is raised and the contours regrown, until what survives fits. Failing
	that, whole contours are dropped strongest first. A contour is a thing; half of one is a
	lie.

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

	**Two colours of the same brightness are the same grey.** A red line on a green ground, a
	blue series beside a purple one, a coloured region on a map shaded to match its neighbour:
	turned grey, each is a flat field, and outlines have nothing to find. So when the picture
	kept its colour, the gradient is taken on red, green and blue separately and each cell
	keeps the strongest of the three, direction and all.

	Nothing is lost on a grey picture. Grey is a weighted average of the three channels, so
	where grey changes, at least one channel changes by as much: an edge outlines found before
	is found at the same strength or stronger. The picture's own strength reference is taken
	the same way, so a window is still held against a yardstick measured like itself.

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

	**The coverage is a ceiling and not a target, and that distinction is the whole of this.**
	It used to be a quota: sort every gradient and take the top sixth of the panel, whatever
	they were. The reasoning was sound for a photograph, where nobody can know the edge density
	in advance and a fixed threshold gives either a solid panel or an empty one. On a line
	drawing it is wrong, because a line drawing has a definite number of edges and it is not a
	sixth of the panel. Measured on a hexagon: a shape with about a hundred and ten pins of
	perimeter, a quota demanding six hundred and fourteen, and the difference spent widening
	the outline to five pins until the corners rounded off and the sides met at the vertices.
	A reader cannot count the sides of that, and counting the sides is all a hexagon has to
	say.

	So the cut comes from the gradients themselves -- Otsu over the magnitudes, the same split
	that separates a picture into two tones, applied instead to how fast it is changing -- the
	contour is thinned to its crest and grown back along itself, and the coverage only ever
	takes pins away. What is left being too little to feel is refused rather than topped up
	with background: filling a quota with whatever came next is how a faint hatched backdrop
	became a panel of stipple with the subject nowhere on it.

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

	Otsu's method: the threshold maximising the variance *between* the two classes, which is
	the same threshold that minimises the variance within them and is found in one pass over a
	256 bin histogram. Fifteen lines, no parameters, and far better than half of full scale at
	the thing that actually matters here — which is that a picture's own two tones are rarely
	either side of 128.

	Used for both the polarity and the cut: which side of the split is the subject, and where
	the split is. It used to be asked only for the polarity, with the cut coming from the
	coverage budget -- which meant a window of faint background had a subject side too, so the
	count came back positive and the darkest of the backdrop went up to fill the quota. What
	the histogram cannot say is how much of a panel a hand can read, and that is what the
	ceiling in `brightnessPins` is still for.

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

	Shared so that brightnesses and gradient magnitudes are split by the same arithmetic
	rather than by two copies of it that could drift apart.

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

	Used on gradient magnitudes, which run to four figures rather than to 255, so they are
	binned into 256 before splitting and the answer is scaled back. Binning loses a little
	precision in the cut and none of it matters: what is being separated is edges from
	not-edges, and on any picture where that distinction exists at all the two groups are
	orders of magnitude apart rather than bins apart.

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

	The brightness counterpart of "how strong is the strongest edge". A drawing of ink on
	paper separates by something near the full range; a patch of faintly textured background
	separates by ten or so, because Otsu will always find a best split and a best split of
	almost nothing is almost nothing. That difference is what tells a window with a subject in
	it from a window with only backdrop, and it is why this is measured rather than assumed.

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

	**Which side is the subject is decided, not assumed.** Dark ink on a light page is the
	common case and raising the dark is the common answer, but a white logo on a dark header is
	just as ordinary on a screen and raising the dark there gives the reader a solid panel with
	a hole in it. So Otsu splits the picture in two and the *smaller* class is taken as the
	subject -- a thing is usually smaller than what it is in front of. An image that is
	genuinely mostly subject comes out inverted, which is what `invert` is for.

	**Membership decides what is raised; the ceiling only ever takes some away.** What is
	raised is what fell on the subject side of the split, and that is the whole of it. An
	earlier version counted the subject and then raised that many of the darkest cells, which
	is the same answer whenever the split is clean and quietly a different one when it is not:
	a window of faint background has a subject side too, so the count came back positive and
	the darkest of the backdrop went up to meet it. The ceiling still applies, because a
	picture that really is two thirds ink would otherwise arrive as most of a solid panel, but
	it can now only remove.

	**The subject keeps its own size**, up to that ceiling. How much of the frame a thing fills
	is information -- a face filling the picture and a bird in a wide sky should not arrive at
	the same size -- and there is no floor for the same reason: raising background to meet a
	quota would put pins where the subject is not.

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

	The smaller of Otsu's two classes, on the reasoning in `brightnessPins`: a thing is usually
	smaller than what it is in front of. Truer still of a stroke, which is thin by being a
	stroke, and that is why `STROKES` asks this and offers no reversed style of its own.

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

	Zhang and Suen's thinning, 1984: peel away the cells on the boundary that can go without
	breaking anything, in two alternating passes -- one taking south and east boundaries, one
	north and west -- so that what is left sits in the middle of the stroke rather than against
	one side of it. A cell may go only if it has between two and six neighbours, which keeps
	line ends and interiors, and if its neighbours make exactly one run around it, which keeps
	every connection it was making.

	**One known fault, mended afterwards rather than worked around.** A stroke exactly two cells
	thick can vanish, because both passes see every cell of a 2 by 2 block as a removable
	corner and they all go at once. A dot of ink that small is still ink, so any piece of the
	mask that comes out with nothing left of it gets back its most central cell. That keeps a
	full stop a full stop.

	Pure Python on purpose. It runs on the reduced grid, a few thousand cells for a handful of
	passes, and it is a decision about what the pins say: exactly the kind of thing this module
	keeps where it can be read.

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

	The style for what readers meet most and `EDGES` serves worst: diagrams, maps, handwriting,
	a hexagon drawn with a pen. Outlines of those are rails, a pair of lines for every stroke,
	and a hand has to learn that two lines close together mean one. This draws one.

	Otsu splits the ink from the ground and the ink side is pared to a skeleton -- piece by
	piece, because not every piece of ink is a stroke. **A filled shape is drawn round, not
	through.** The skeleton of a solid square is a dot and of a disc a point, so a logo, a
	filled chart marker or a black blob on a map would arrive as nothing much. A piece whose
	ink is more than `SOLID` pins thick on average is a shape rather than a line, and is drawn
	as its own boundary instead: still one line, and the line a finger wants, round the
	outside. The same ceiling as outlines then applies, because a skeleton is a set of lines and the panel reads lines
	at the same density whichever way they were found. Over the ceiling, whole strokes are kept
	largest first and the rest let go, for the reason `_fitEdges` gives: half a line is a lie.
	Letting any go is said, as `crowded`, since the reader is then feeling less than there is.

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

	**A picture squeezed to the panel shape is a picture of something else**: a face becomes a
	wide face, a circle on a map becomes an ellipse, and a reader has no way to know it
	happened. The panel is over two to one and most pictures are not, so this is the common
	case rather than an edge one.

	Answered by shrinking the *destination* to the source proportions, which leaves margin
	beside the picture on the panel. It used to be answered the other way, by growing the
	source box outwards until it had the panel proportions and filling the overhang with the
	picture own border tone. That fitted correctly and it handed the detector a seam it had
	invented -- see `Placement` for what a coverage quota then did with it.

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

	The one place the refusals live, so that every caller -- the first view, a zoom, a change
	of style -- fails the same way for the same reasons.

	**A window is judged against the picture, not against itself.** Both detectors find a best
	answer in whatever they are handed, and a patch of blank paper has a best answer too, so
	zooming into the middle of a drawing used to come back as a confident panel of stipple with
	the subject nowhere on it. Each style is therefore asked the question it can answer -- how
	strong is the strongest edge here, how far apart are the two tones here -- and the answer
	is compared with the same measurement over the whole capture. See `MEANINGFUL`.

	**What happens when the answer is no depends on whether this is the whole picture.** A
	capture with nothing in it is refused: the reader pointed at something that is not a
	picture, and a blank panel would leave them unable to tell that from a display that had
	stopped working. A *window* with nothing in it is drawn empty and marked, because by then
	the whole picture has already drawn and the display has proved itself -- and because
	refusing it strands the reader. Zoom keeps what is under the hand under the hand, the
	middle of an outlined shape is empty, so every zoom from fit was refused and the rim,
	which is the part worth magnifying, could not be reached at all: no zoom, therefore no
	pan, therefore no way in. See `Rendering.barren`.

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
		# Something long and thin -- a rule, a divider, a one line toolbar -- fitted onto the
		# panel without being squashed leaves a strip a pin or two deep. Nothing can be drawn
		# in that, and saying so beats the refusal further down, which would report how few
		# pins came out and leave the reader wondering what was wrong with the picture.
		# Translators: reported when something is too long and thin to draw on the pins. The
		# placeholder is how many pin rows deep it would be.
		raise ImageRefused(
			_("This is too thin to draw; it would be {rows} rows deep").format(
				rows=min(spot.width, spot.height),
			),
		)

	def nothingHere(why: str) -> Rendering:
		"""Refuse the whole picture, or hand back a blank window that says so.

		Every way of finding nothing arrives here, and they must all answer the same way.
		Three of them did not: a window of flat tone, a window below the strength floor and a
		window that came out too sparse each had their own exit, two of them refusing -- and a
		refused window is a zoom that does not happen, which is how a reader ended up with a
		zoom key that did nothing on a picture with plenty in it. The rule is about who is
		asking, not about which check noticed.
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
		# A flat field. Every threshold below would be arbitrary on it, and for a whole
		# picture both ways it could go -- an empty panel and a solid one -- are
		# indistinguishable by touch from a display that has stopped working.
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
		# Only of a whole picture. A window this sparse has found a little of something real,
		# and a few true pins are worth more to a reader panning across a drawing than a
		# refusal that stops them moving -- the display has already proved itself by drawing
		# the whole picture, so there is nothing here to mistake for a fault.
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

	`place` and then `renderAt`. Kept as one call for the sake of everything that only wants a
	picture on a panel and has no use for the two rectangles separately.

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

	What the graphics mode's zoom hands over: where the window starts and how much of the
	drawing it covers, both as fractions of the whole. Kept here rather than in the mode
	because the mode must not learn that a picture is pixels, exactly as it does not learn that
	a chart is periods.

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
