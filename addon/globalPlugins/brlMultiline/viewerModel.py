# BrlMultiline: what the viewer shows.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The picture the viewer draws, worked out from cells, displays, segments and drawings.

Imports nothing from NVDA, so every rule here is unit tested; `viewer` gathers the inputs and
`viewerWindow` paints the result. See docs/design/braille-viewer-plan.md, The viewer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

from .layout import SegmentRect

BRAILLE_PATTERNS_START = 0x2800

BRAILLE_DOT_COORDS = (
	(0, 0),
	(0, 1),
	(0, 2),
	(1, 0),
	(1, 1),
	(1, 2),
	(0, 3),
	(1, 3),
)
"""Where each of dots 1 to 8 sits in a cell, as the Monarch driver's `pinBuffer` has it."""


class Member(NamedTuple):
	"""One physical or simulated display, and its rows of the display NVDA has."""

	name: str
	"""Its driver name."""

	label: str
	"""What the viewer calls it."""

	rowStart: int
	numRows: int
	numCols: int
	"""Its own width, which may be narrower than the display NVDA has."""

	@property
	def rowEnd(self) -> int:
		return self.rowStart + self.numRows


class PinBand(NamedTuple):
	"""A member that can draw: its pin grid, and how a cell sits on it."""

	member: str
	"""The member's driver name."""

	pinWidth: int
	pinHeight: int
	pinsPerRow: int
	"""Pin rows from one braille line to the next."""

	pinsPerCol: int
	"""Pin columns from one cell to the next."""


class Segment(NamedTuple):
	"""A segment's rectangle, and the text it shows."""

	rect: SegmentRect
	rawText: str
	rowTexts: tuple[str, ...] = ()
	"""The text behind each of its rows, where the segment can say. Empty to cut `rawText` by width."""


class Overlay(NamedTuple):
	"""A drawing on one member, at its own pins."""

	x: int
	y: int
	buffer: object
	"""Anything with `width`, `height` and `getDot(x, y)`, as the drivers' pin buffers have."""


@dataclass(frozen=True)
class Picture:
	"""Everything one paint of the viewer needs."""

	numRows: int
	numCols: int
	cells: tuple[int, ...]
	"""Row major over the whole display, as NVDA wrote them."""

	members: tuple[Member, ...] = ()
	"""Top first. One member covering everything for a display that is not a composite."""

	segments: tuple[Segment, ...] = ()
	pinBands: tuple[PinBand, ...] = ()
	overlays: dict = field(default_factory=dict)
	"""Member name to its `Overlay`s."""

	def cell(self, row: int, col: int) -> int:
		""":return: the dots of one cell, 0 for any cell NVDA did not write."""
		index = row * self.numCols + col
		return self.cells[index] if 0 <= col < self.numCols and 0 <= index < len(self.cells) else 0

	def memberForRow(self, row: int) -> Member | None:
		for member in self.members:
			if member.rowStart <= row < member.rowEnd:
				return member
		return None

	def isDead(self, row: int, col: int) -> bool:
		"""Whether a cell reaches no hardware, because its display is narrower than the composite."""
		member = self.memberForRow(row)
		return member is not None and col >= member.numCols

	def pinBandFor(self, member: Member) -> PinBand | None:
		return next((band for band in self.pinBands if band.member == member.name), None)

	def showsPins(self, member: Member) -> bool:
		"""Whether a member is drawn as pins, which it is only while a drawing is up on it."""
		return self.pinBandFor(member) is not None and bool(self.overlays.get(member.name))

	def rawTextRows(self) -> list[str]:
		"""The text under each row, each segment's own text under its own cells.

		Each row's own text where the segment gives it, cut at the segment's width, since contracted
		braille fits more words in a row than its cells have room for as text. Otherwise the text
		fills the rows in turn, its width at a time, and runs ahead of the cells it belongs to.

		:return: one string per row, `numCols` long.
		"""
		rows = [[" "] * self.numCols for _ in range(self.numRows)]
		for segment in self.segments:
			rect = segment.rect
			text = segment.rawText
			for offset in range(rect.numRows):
				row = rect.row + offset
				if not 0 <= row < self.numRows:
					continue
				if segment.rowTexts:
					rowText = segment.rowTexts[offset] if offset < len(segment.rowTexts) else ""
					chunk = rowText[: rect.numCols]
				else:
					chunk = text[offset * rect.numCols : (offset + 1) * rect.numCols]
				for index, character in enumerate(chunk):
					col = rect.col + index
					if 0 <= col < self.numCols:
						rows[row][col] = character if character.isprintable() else " "
		return ["".join(row) for row in rows]

	def pins(self, member: Member) -> list[list[bool]]:
		"""Compose a drawing member's pins: its cells as dots, and its drawings over them.

		:param member: a member with a `PinBand`.
		:return: one list of raised or lowered pins per pin row.
		"""
		band = self.pinBandFor(member)
		if band is None:
			return []
		grid = [[False] * band.pinWidth for _ in range(band.pinHeight)]

		def raisePin(x: int, y: int) -> None:
			if 0 <= x < band.pinWidth and 0 <= y < band.pinHeight:
				grid[y][x] = True

		for localRow in range(member.numRows):
			for col in range(member.numCols):
				cell = self.cell(member.rowStart + localRow, col)
				for bit, (dotX, dotY) in enumerate(BRAILLE_DOT_COORDS):
					if cell & (1 << bit):
						raisePin(col * band.pinsPerCol + dotX, localRow * band.pinsPerRow + dotY)
		for overlay in self.overlays.get(member.name, ()):
			buffer = overlay.buffer
			for y in range(getattr(buffer, "height", 0)):
				for x in range(getattr(buffer, "width", 0)):
					if buffer.getDot(x, y):
						raisePin(overlay.x + x, overlay.y + y)
		return grid


def brailleCharacter(cell: int) -> str:
	""":return: the Unicode braille pattern for a cell's dots."""
	return chr(BRAILLE_PATTERNS_START + (cell & 0xFF))


class OverlayStore:
	"""The drawings each display has up, as `graphics.overlayChanged` reports them.

	By member name, since a drawing is on one physical display. Kept here rather than read from
	the drivers, because only some drivers say what they hold.
	"""

	def __init__(self):
		self._overlays: dict[str, dict[str, Overlay]] = {}

	def apply(self, member: str, key: str | None, overlay: Overlay | None) -> None:
		"""Record a drawing put up or taken off.

		:param member: the driver name of the display it is on.
		:param key: which drawing, or None with no overlay for all of that display's.
		:param overlay: the drawing, or None for one taken off.
		"""
		drawings = self._overlays.setdefault(member, {})
		if overlay is not None and key is not None:
			drawings[key] = overlay
		elif key is None:
			drawings.clear()
		else:
			drawings.pop(key, None)

	def keepOnly(self, members) -> None:
		"""Forget the drawings of displays no longer there.

		:param members: the driver names still there.
		"""
		for name in list(self._overlays):
			if name not in members:
				del self._overlays[name]

	def snapshot(self) -> dict:
		""":return: member name to its overlays, in the order they were put up."""
		return {name: tuple(drawings.values()) for name, drawings in self._overlays.items() if drawings}


class CellPosition(NamedTuple):
	"""Where a point in the viewer lands."""

	row: int
	col: int
	pin: tuple[int, int] | None = None
	"""The member's own pin, where the member is drawn as pins."""


def routingIndex(picture: Picture, position: CellPosition) -> int | None:
	""":return: the cell index NVDA routes against, or None for a dead cell or no cell at all."""
	if not (0 <= position.row < picture.numRows and 0 <= position.col < picture.numCols):
		return None
	if picture.isDead(position.row, position.col):
		return None
	return position.row * picture.numCols + position.col


def memberCellIndex(member: Member, position: CellPosition) -> int:
	""":return: the cell index on the member's own display, as its routing keys number it."""
	return (position.row - member.rowStart) * member.numCols + position.col
