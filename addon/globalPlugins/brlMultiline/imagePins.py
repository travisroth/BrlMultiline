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
	"Placement",
	"Rendering",
	"Strength",
	"brightnessPins",
	"edgePins",
	"greysFromPixels",
	"place",
	"render",
	"renderAt",
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

REFERENCE_CELLS = 48
"""How finely the whole picture is measured when working out what its strength is.

Fixed, and deliberately not the panel size: the reference has to mean the same thing at every
zoom, so it cannot be taken at whatever reduction the window happens to want. Forty-eight is
close enough to a panel's forty rows that gradients come out the same order of magnitude, and
small enough that measuring costs a few thousand cells once per capture.
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
		self._strength = None

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
		""":return: the picture's own strength, at `REFERENCE_CELLS`."""
		if not self.greys or self.width < 2 or self.height < 2:
			return Strength(0, 0)
		if self.width >= self.height:
			across = min(REFERENCE_CELLS, self.width)
			down = max(1, round(across * self.height / self.width))
		else:
			down = min(REFERENCE_CELLS, self.height)
			across = max(1, round(down * self.width / self.height))
		greys = self.reduce((0, 0, self.width, self.height), across, down)
		cells = _gradients(greys, across, down)
		return Strength(max((cell[0] for cell in cells), default=0), _separation(greys))

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

		The inner sum is taken over a slice so that the per-pixel work happens below Python.
		A capture of a few hundred thousand pixels reduces in milliseconds that way and in a
		noticeable pause without it.

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
				out[rowOut + ox] = min(255, total // area) if area else background
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
		if magnitude >= before and magnitude >= after:
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


def edgePins(greys, width: int, height: int, coverage: float = EDGE_COVERAGE) -> Rendering:
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
	:return: the pins.
	"""
	pins = bytearray(width * height)
	cells = _gradients(greys, width, height)
	if not cells:
		return Rendering(pins, width, height, 0)
	kept = _thin(cells, width, height)
	if not kept:
		return Rendering(pins, width, height, 0)
	chosen = _followEdges(kept, _otsuOver([magnitude for magnitude, _place in kept]), width, height)
	if not chosen:
		return Rendering(pins, width, height, 0)
	ceiling = max(1, int(round(width * height * coverage)))
	if len(chosen) <= ceiling:
		for place in chosen:
			pins[place] = 1
		return Rendering(pins, width, height, len(chosen))
	values = [magnitude for magnitude, place in kept if place in chosen]
	places = [place for _magnitude, place in kept if place in chosen]
	return Rendering(pins, width, height, _raiseTopmost(values, places, ceiling, pins))


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
	dark = sum(1 for grey in greys if grey < threshold)
	subjectIsDark = dark <= total - dark
	if invert:
		subjectIsDark = not subjectIsDark
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
	left, top, across, down = box
	left = max(0, min(int(left), max(0, picture.width - 1)))
	top = max(0, min(int(top), max(0, picture.height - 1)))
	across = min(int(across), picture.width - left)
	down = min(int(down), picture.height - top)
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
	:param mode: `EDGES` or `BRIGHTNESS`.
	:param invert: swap which side of a silhouette is raised.
	:param whole: whether this is the entire picture rather than a window of it.
	:return: the pins, positioned on the panel.
	:raises ImageRefused: if there is nothing here to draw and nothing to pan towards.
	"""
	if spot.width <= 0 or spot.height <= 0:
		# Translators: reported when a picture was asked for in no space at all.
		raise ImageRefused(_("There is no room here for a picture"))
	greys = picture.reduce(spot.source, spot.width, spot.height)
	if not greys or (max(greys) - min(greys)) < PLAIN:
		# Refused rather than drawn. Every threshold below would be arbitrary on a flat field,
		# and both ways it could go -- an empty panel and a solid one -- are indistinguishable
		# by touch from a display that has stopped working.
		# Translators: reported when a picture has nothing in it to feel.
		raise ImageRefused(_("There is nothing in this picture to draw"))
	strength = picture.strength
	if mode == BRIGHTNESS:
		found = _separation(greys) >= strength.separation * MEANINGFUL
	else:
		cells = _gradients(greys, spot.width, spot.height)
		found = max((cell[0] for cell in cells), default=0) >= strength.gradient * MEANINGFUL
	if not found:
		if whole:
			# Translators: reported when what was pointed at holds nothing that can be drawn.
			raise ImageRefused(_("There is only background in this picture"))
		return Rendering(
			bytearray(spot.width * spot.height),
			spot.width,
			spot.height,
			0,
			spot.left,
			spot.top,
			True,
		)
	if mode == BRIGHTNESS:
		rendering = brightnessPins(greys, spot.width, spot.height, invert=invert)
	else:
		rendering = edgePins(greys, spot.width, spot.height)
	if rendering.coverage < SPARSE:
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
	:param mode: `EDGES` or `BRIGHTNESS`.
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
