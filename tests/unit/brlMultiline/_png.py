# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Just enough PNG to read a test fixture.

**Why this exists rather than Pillow.** Pillow is not in NVDA and is not in this project
either, and the one thing worth having a real image file for is a regression test against an
image that actually produced a wrong panel. Sixty lines of `zlib` and a filter loop buys that
without adding a dependency to a test suite that has none.

Deliberately narrow: eight bits a channel, no interlacing, the five standard filters. It is
not a decoder, it is a fixture reader, and anything it cannot read should fail loudly rather
than be worked around here.
"""

import struct
import zlib

__all__ = ["greysOf"]


def _unfilter(raw, width: int, height: int, channels: int) -> list:
	""":return: the scanlines with their filters undone.

	:param raw: the decompressed image data, each line prefixed by its filter type.
	:param width: pixels across.
	:param height: pixels down.
	:param channels: bytes per pixel.
	"""
	stride = width * channels
	rows = []
	previous = bytearray(stride)
	at = 0
	for _y in range(height):
		kind = raw[at]
		at += 1
		line = bytearray(raw[at : at + stride])
		at += stride
		if kind == 1:
			for i in range(channels, stride):
				line[i] = (line[i] + line[i - channels]) & 255
		elif kind == 2:
			for i in range(stride):
				line[i] = (line[i] + previous[i]) & 255
		elif kind == 3:
			for i in range(stride):
				left = line[i - channels] if i >= channels else 0
				line[i] = (line[i] + ((left + previous[i]) >> 1)) & 255
		elif kind == 4:
			for i in range(stride):
				left = line[i - channels] if i >= channels else 0
				up = previous[i]
				corner = previous[i - channels] if i >= channels else 0
				estimate = left + up - corner
				byLeft = abs(estimate - left)
				byUp = abs(estimate - up)
				byCorner = abs(estimate - corner)
				if byLeft <= byUp and byLeft <= byCorner:
					nearest = left
				elif byUp <= byCorner:
					nearest = up
				else:
					nearest = corner
				line[i] = (line[i] + nearest) & 255
		elif kind:
			raise ValueError(f"unsupported PNG filter {kind}")
		rows.append(line)
		previous = line
	return rows


def greysOf(path: str, over: int = 255) -> tuple:
	"""Read a PNG as one brightness per pixel.

	Rec. 601 as written, matching `imagePins.greysFromPixels`, so that a fixture read here and
	a capture taken off the screen arrive at the detectors in the same units.

	:param path: the file.
	:param over: what to composite any transparency onto.
	:return: the brightnesses row major, the width and the height.
	"""
	with open(path, "rb") as opened:
		raw = opened.read()
	if raw[:8] != b"\x89PNG\r\n\x1a\x0a":
		raise ValueError("not a PNG")
	at = 8
	data = b""
	palette = None
	transparency = None
	width = height = depth = colour = interlace = 0
	while at < len(raw):
		(length,) = struct.unpack(">I", raw[at : at + 4])
		kind = raw[at + 4 : at + 8]
		chunk = raw[at + 8 : at + 8 + length]
		if kind == b"IHDR":
			width, height, depth, colour, _compression, _filter, interlace = struct.unpack(
				">IIBBBBB",
				chunk,
			)
		elif kind == b"PLTE":
			palette = chunk
		elif kind == b"tRNS":
			transparency = chunk
		elif kind == b"IDAT":
			data += chunk
		elif kind == b"IEND":
			break
		at += 12 + length
	if depth != 8 or interlace:
		raise ValueError(f"unsupported PNG: depth {depth}, interlace {interlace}")
	channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
	rows = _unfilter(zlib.decompress(data), width, height, channels)
	greys = bytearray(width * height)
	for y in range(height):
		line = rows[y]
		for x in range(width):
			alpha = 255
			if colour == 3:
				index = line[x]
				red, green, blue = palette[index * 3 : index * 3 + 3]
				if transparency is not None and index < len(transparency):
					alpha = transparency[index]
			elif colour == 0:
				red = green = blue = line[x]
			elif colour == 4:
				red = green = blue = line[x * 2]
				alpha = line[x * 2 + 1]
			elif colour == 2:
				red, green, blue = line[x * 3 : x * 3 + 3]
			else:
				red, green, blue, alpha = line[x * 4 : x * 4 + 4]
			value = (299 * red + 587 * green + 114 * blue) // 1000
			greys[y * width + x] = value if alpha == 255 else (value * alpha + over * (255 - alpha)) // 255
	return greys, width, height
