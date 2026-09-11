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

**A coverage budget, not a threshold.** "Which pixels are edge enough to raise a pin" has no
answer in the pixels. It has one in the hand: a panel much more than a fifth raised stops being
a picture and becomes a texture, and a panel with a dozen pins up says nothing at all. So no cut
is chosen as a magnitude. The values are sorted and the cut is taken wherever it leaves the
wanted fraction of pins raised.

That is the same move as drawing a chart at the size of the space it is going into. A fixed
threshold raises everything on a high contrast logo and nothing on a soft photograph, and a
reader cannot tell those two failures apart by touch — both are a panel that says nothing.
Aiming at the coverage means a picture of any contrast arrives at a readable density, and what
differs between pictures is *which* pins those are, which is the part carrying the information.

**No numpy, and it is importable while you check.** It is in NVDA's build environment as an
optional dependency of comtypes, so it works in the Python console of any NVDA run from
source — and `source/setup.py` lists it under `excludes`, so py2exe leaves it out of what is
shipped. Importing it here would pass every test the author could run and fail on every
installed NVDA. Pillow is on neither path. The work is a reduction taken as slice sums and a
Sobel over a few thousand cells, which is milliseconds and wants nothing.
"""

from typing import NamedTuple

__all__ = [
	"EDGES",
	"BRIGHTNESS",
	"ImageRefused",
	"Picture",
	"Rendering",
	"brightnessPins",
	"edgePins",
	"fitBox",
	"greysFromPixels",
	"render",
	"windowOf",
]


EDGES = "edges"
"""Outlines: where the picture changes, which is what line art and diagrams are made of."""

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

A ceiling and deliberately no floor, which is the opposite of what the outline budget does.

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

SPARSE = 0.004
"""Below this fraction raised, there is nothing under the hand to find."""


class ImageRefused(Exception):
	"""There is a picture, but not one that can be put on pins.

	Raised rather than returning None, because every case carries a reason the reader can act
	on — nothing in the picture, nothing left after reducing it — and a caller that only knew
	it had failed could not tell them which.
	"""


class Rendering(NamedTuple):
	"""One picture worked out for one rectangle of pins."""

	pins: bytearray
	"""One byte per pin, row major, 1 for raised."""

	width: int
	"""Pins across."""

	height: int
	"""Pins down."""

	raised: int
	"""How many are up. What decides whether this is worth showing at all."""

	@property
	def coverage(self) -> float:
		""":return: the fraction of the rectangle that is raised."""
		total = self.width * self.height
		return self.raised / total if total else 0.0

	def draw(self, buffer) -> None:
		"""Stamp this onto a drawing buffer at its origin.

		:param buffer: anything with `setDot`, which is `PinBuffer`.
		"""
		for y in range(self.height):
			row = y * self.width
			for x in range(self.width):
				if self.pins[row + x]:
					buffer.setDot(x, y)


def greysFromPixels(rows, width: int, height: int) -> bytearray:
	"""Flatten captured screen pixels into one brightness each.

	**Not NVDA's `screenBitmap.rgbPixelBrightness`.** That one weights blue at 0.3 and red at
	0.11, which is the standard luma formula with its red and blue terms exchanged; on anything
	where the subject is told from its background by being red or blue — a red line on a chart,
	a blue link underline — it gets the contrast backwards. The weights here are Rec. 601 as
	written, and this is the one place they are applied.

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
			greys[at] = int(0.299 * pixel.rgbRed + 0.587 * pixel.rgbGreen + 0.114 * pixel.rgbBlue)
			at += 1
	return greys


class Picture:
	"""Captured pixels, at whatever size they were captured.

	Held rather than reduced once, because it is what makes zoom worth doing. A picture reduced
	straight to the panel would make the panel everything that was ever known about it, and
	zooming could then only make each dot bigger. Keeping the pixels means a reader looking at a
	quarter of the picture gets that quarter reduced from its own pixels — genuinely more
	detail, up to the resolution it was captured at, and no further.
	"""

	def __init__(self, greys: bytearray, width: int, height: int, name: str = ""):
		"""
		:param greys: one brightness per pixel, row major.
		:param width: pixels across.
		:param height: pixels down.
		:param name: what to call this picture when a reader asks what is on the display.
		"""
		self.greys = greys
		self.width = width
		self.height = height
		self.name = name

	@property
	def spread(self) -> int:
		""":return: darkest to lightest, out of 255. See `PLAIN`."""
		return (max(self.greys) - min(self.greys)) if self.greys else 0

	def reduce(self, box, outWidth: int, outHeight: int) -> bytearray:
		"""Average a rectangle of the pixels down to a grid of the given size.

		Area average rather than point sampling, and the difference is not subtle at these
		ratios. Sampling one pixel in sixteen drops a one pixel line entirely whenever it falls
		between the samples, so a diagram's strokes come and go along their own length —
		visibly wrong to a hand, and wrong differently each time the window moves.

		The inner sum is taken over a slice so that the per-pixel work happens below Python.
		A capture of a few hundred thousand pixels reduces in milliseconds that way and in a
		noticeable pause without it.

		:param box: the source rectangle, `(left, top, width, height)` in pixels. Clipped.
		:param outWidth: columns wanted.
		:param outHeight: rows wanted.
		:return: one average per output cell, row major.
		"""
		left, top, width, height = box
		left = max(0, min(left, self.width - 1)) if self.width else 0
		top = max(0, min(top, self.height - 1)) if self.height else 0
		width = max(1, min(width, self.width - left))
		height = max(1, min(height, self.height - top))
		out = bytearray(max(0, outWidth) * max(0, outHeight))
		if outWidth <= 0 or outHeight <= 0:
			return out
		greys = self.greys
		for oy in range(outHeight):
			y0 = top + oy * height // outHeight
			y1 = max(y0 + 1, top + (oy + 1) * height // outHeight)
			y1 = min(y1, self.height)
			rowOut = oy * outWidth
			for ox in range(outWidth):
				x0 = left + ox * width // outWidth
				x1 = max(x0 + 1, left + (ox + 1) * width // outWidth)
				x1 = min(x1, self.width)
				total = 0
				count = 0
				for y in range(y0, y1):
					base = y * self.width
					total += sum(greys[base + x0 : base + x1])
					count += x1 - x0
				out[rowOut + ox] = total // count if count else 0
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


def edgePins(greys, width: int, height: int, coverage: float = EDGE_COVERAGE) -> Rendering:
	"""Raise a pin wherever the picture changes fastest.

	Sobel, which is a derivative with a little smoothing across it, and the smoothing is why it
	is Sobel rather than a plain difference: a single noisy pixel in a photograph is a large
	difference and is not an edge, and at this reduction one stray raised pin in open space is
	as loud to a finger as a whole stroke.

	The magnitude is `|gx| + |gy|` rather than the square root of the sum of squares. The two
	differ by at most a factor of the square root of two, they differ most on exact diagonals,
	and the cut that follows is a rank rather than a value — so the only thing the cheaper
	measure changes is a slight preference for diagonals over axis-aligned strokes, which on a
	panel where a diagonal has to fight the pin lattice is the right way round anyway.

	The border pins are left down. A one pin frame that is really the edge of the capture reads
	as part of the picture, and a picture with a box drawn round it is a picture a reader has to
	be told to ignore.

	:param greys: one brightness per cell, row major, already reduced to `width` by `height`.
	:param width: cells across.
	:param height: cells down.
	:param coverage: the fraction of the panel to raise.
	:return: the pins.
	"""
	pins = bytearray(width * height)
	if width < 3 or height < 3:
		return Rendering(pins, width, height, 0)
	magnitudes = []
	places = []
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
				magnitudes.append(magnitude)
				places.append(here + x)
	if not magnitudes:
		return Rendering(pins, width, height, 0)
	wanted = max(1, int(round(width * height * coverage)))
	raised = _raiseTopmost(magnitudes, places, wanted, pins)
	return Rendering(pins, width, height, raised)


def _otsu(greys) -> int:
	"""Split brightnesses into two classes at the point that separates them best.

	Otsu's method: the threshold maximising the variance *between* the two classes, which is
	the same threshold that minimises the variance within them and is found in one pass over a
	256 bin histogram. Fifteen lines, no parameters, and far better than half of full scale at
	the thing that actually matters here — which is that a picture's own two tones are rarely
	either side of 128.

	What it is used for is the polarity rather than the cut: which side of the split is the
	subject. The cut itself comes from the coverage budget, because how much of the panel may
	be raised is a fact about a hand and not about a histogram.

	:param greys: brightnesses.
	:return: the threshold. Pixels below it are the dark class.
	"""
	histogram = [0] * 256
	for grey in greys:
		histogram[grey] += 1
	total = len(greys)
	if not total:
		return 128
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
	subject — a thing is usually smaller than what it is in front of. An image that is genuinely
	mostly subject comes out inverted, which is what `invert` is for.

	**The subject keeps its own size**, up to the ceiling. How much of the frame a thing fills
	is information — a face filling the picture and a bird in a wide sky should not arrive at
	the same size — and there is no floor for the same reason: raising background to meet a
	quota would put pins where the subject is not.

	:param greys: one brightness per cell, row major, already reduced.
	:param width: cells across.
	:param height: cells down.
	:param invert: raise the other side instead.
	:param most: the ceiling on how much of the panel may be raised.
	:return: the pins.
	"""
	pins = bytearray(width * height)
	total = width * height
	if not total or not greys:
		return Rendering(pins, width, height, 0)
	threshold = _otsu(greys)
	dark = sum(1 for grey in greys if grey < threshold)
	subjectIsDark = dark <= total - dark
	if invert:
		subjectIsDark = not subjectIsDark
	share = (dark if subjectIsDark else total - dark) / total
	wanted = max(1, int(round(total * min(most, share))))
	# Ranked on whichever side the subject turned out to be, rather than cut at Otsu's own
	# threshold. Otsu says which side; how much of the panel may be raised is a fact about a
	# hand and not about a histogram, and a very high contrast picture cut at the threshold
	# would raise its whole half.
	values = [255 - grey for grey in greys] if subjectIsDark else list(greys)
	raised = _raiseTopmost(values, range(total), wanted, pins)
	return Rendering(pins, width, height, raised)


def render(
	picture: Picture,
	box,
	width: int,
	height: int,
	mode: str = EDGES,
	invert: bool = False,
) -> Rendering:
	"""Work one rectangle of a picture out for one rectangle of pins.

	The single entry point, and the one place the refusals live, so that every caller — the
	first view, a zoom, a change of mode — fails the same way for the same reasons.

	:param picture: the captured pixels.
	:param box: which part of them, `(left, top, width, height)`.
	:param width: pins across.
	:param height: pins down.
	:param mode: `EDGES` or `BRIGHTNESS`.
	:param invert: swap which side of a silhouette is raised.
	:return: the pins.
	:raises ImageRefused: if there is nothing in this part of the picture to draw.
	"""
	if width <= 0 or height <= 0:
		# Translators: reported when a picture was asked for in no space at all.
		raise ImageRefused(_("There is no room here for a picture"))
	greys = picture.reduce(box, width, height)
	if not greys or (max(greys) - min(greys)) < PLAIN:
		# Refused rather than drawn. Every threshold below would be arbitrary on a flat field,
		# and both ways it could go — an empty panel and a solid one — are indistinguishable by
		# touch from a display that has stopped working.
		# Translators: reported when a picture has nothing in it to feel.
		raise ImageRefused(_("There is nothing in this picture to draw"))
	if mode == BRIGHTNESS:
		rendering = brightnessPins(greys, width, height, invert=invert)
	else:
		rendering = edgePins(greys, width, height)
	if rendering.coverage < SPARSE:
		# Translators: reported when a picture came out as too few raised dots to feel.
		raise ImageRefused(_("There is too little in this picture to feel"))
	return rendering


def fitBox(picture: Picture, width: int, height: int) -> tuple:
	"""Choose the part of a picture to show so that it is not stretched.

	A picture squeezed to the panel's shape is a picture of something else: a face becomes a
	wide face, a circle on a map becomes an ellipse, and a reader has no way to know it
	happened. The panel is over two to one and most pictures are not, so this is the common
	case rather than an edge one.

	Answered by enlarging the *source* box rather than by shrinking the drawing: the box is
	grown on the short axis until it has the panel's proportions, which keeps the whole picture
	in view with blank pixels beside it, and blank pixels reduce to a background the detectors
	then find nothing in.

	:param picture: the captured pixels.
	:param width: pins across.
	:param height: pins down.
	:return: the source rectangle, which may reach outside the picture. `Picture.reduce`
		clips it, so the extra is background rather than a stretched edge.
	"""
	if width <= 0 or height <= 0 or not picture.width or not picture.height:
		return (0, 0, picture.width, picture.height)
	if picture.width * height >= picture.height * width:
		# Wider than the panel: the height has to grow.
		wanted = picture.width * height // width
		return (0, (picture.height - wanted) // 2, picture.width, max(1, wanted))
	wanted = picture.height * width // height
	return ((picture.width - wanted) // 2, 0, max(1, wanted), picture.height)


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
