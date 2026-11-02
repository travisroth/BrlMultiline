# BrlMultiline: what the viewer shows.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The picture the viewer draws, where it draws it, and what pointing at it does.

Worked out from cells, displays, segments and drawings. Imports nothing from NVDA, so every rule
here is unit tested; `viewer` gathers the inputs and `viewerWindow` paints the result and times
the hover. See docs/design/braille-viewer-plan.md, The viewer.
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

	@property
	def shape(self) -> tuple:
		""":return: the display's size and its members' places, which a pointer's target depends on."""
		return (self.numRows, self.numCols, tuple(self.members))

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


MARGIN = 8
ROW_GAP = 2
MEMBER_GAP = 12


class Box(NamedTuple):
	"""A rectangle of the viewer, in pixels."""

	x: float
	y: float
	width: float
	height: float


class ViewerLayout:
	"""Where each member, row, cell and pin of a picture is drawn, for painting and for pointing.

	A row is a line of braille and the line of text under it. A member drawn as pins has square pin
	spacing instead, a cell's width over its pin columns, so a drawing keeps its shape; its rows
	are as tall as their pins make them.
	"""

	def __init__(self, picture: Picture, cellWidth: int, cellHeight: int, labelHeight: int):
		"""
		:param picture: what is drawn.
		:param cellWidth: pixels across one braille character.
		:param cellHeight: pixels down one line of the braille font.
		:param labelHeight: pixels down a member's label, with its gap.
		"""
		self.picture = picture
		self.cellWidth = cellWidth
		self.cellHeight = cellHeight
		self.textRowHeight = cellHeight * 2 + ROW_GAP
		self.memberTops: list[tuple[Member, float]] = []
		self.rowTops: list[float] = []
		self.rowHeights: list[float] = []
		y: float = MARGIN
		members = picture.members or (Member("", "", 0, picture.numRows, picture.numCols),)
		for member in members:
			self.memberTops.append((member, y))
			y += labelHeight
			step = self.pinStep(member)
			band = picture.pinBandFor(member)
			height = band.pinsPerRow * step if step is not None and band is not None else self.textRowHeight
			for _row in range(member.numRows):
				self.rowTops.append(y)
				self.rowHeights.append(height)
				y += height
			y += MEMBER_GAP
		self.height = int(y + 0.5)
		self.width = MARGIN * 2 + cellWidth * picture.numCols

	def pinStep(self, member: Member) -> float | None:
		""":return: pixels from one pin to the next, both ways, for a member drawn as pins; else None."""
		band = self.picture.pinBandFor(member)
		if band is None or not band.pinsPerCol or not self.picture.showsPins(member):
			return None
		return self.cellWidth / band.pinsPerCol

	def cellBox(self, row: int, col: int) -> Box:
		""":return: where one cell is drawn."""
		return Box(MARGIN + col * self.cellWidth, self.rowTops[row], self.cellWidth, self.rowHeights[row])

	def pinCentre(self, member: Member, x: int, y: int) -> tuple[float, float]:
		""":return: where the centre of one of a member's pins is drawn. The member must be drawn as pins."""
		step = self.pinStep(member) or 0.0
		return MARGIN + (x + 0.5) * step, self.rowTops[member.rowStart] + (y + 0.5) * step

	def position(self, x: float, y: float) -> CellPosition | None:
		""":return: the cell at a point, with the pin where its member is drawn as pins; None for none."""
		if x < MARGIN:
			return None
		for row, top in enumerate(self.rowTops):
			if not top <= y < top + self.rowHeights[row]:
				continue
			member = self.picture.memberForRow(row)
			step = self.pinStep(member) if member is not None else None
			band = self.picture.pinBandFor(member) if member is not None else None
			if member is None or step is None or band is None:
				col = int((x - MARGIN) // self.cellWidth)
				return CellPosition(row, col) if 0 <= col < self.picture.numCols else None
			pinX = int((x - MARGIN) // step)
			pinY = int((y - self.rowTops[member.rowStart]) // step)
			if not (0 <= pinX < band.pinWidth and 0 <= pinY < band.pinHeight):
				return None
			cellRow = member.rowStart + min(pinY // band.pinsPerRow, member.numRows - 1)
			return CellPosition(cellRow, pinX // band.pinsPerCol, (pinX, pinY))
		return None


class HoverTracker:
	"""Which cell the pointer rests on, and which it has already routed, as NVDA's viewer keeps them.

	A routed cell does not route again until the pointer leaves it. A wait is dropped when the cell
	under it stops being the same cell of the same display.
	"""

	def __init__(self):
		self.pending: CellPosition | None = None
		"""The cell waited on, or None."""
		self._pendingMember: str | None = None
		self._routed: tuple[int, int] | None = None

	def move(self, picture: Picture, position: CellPosition | None) -> bool:
		"""Note where the pointer is.

		:param picture: what is shown.
		:param position: the cell under the pointer, or None.
		:return: whether a new wait has begun, for the caller to time.
		"""
		if position is None or routingIndex(picture, position) is None:
			self.leave()
			return False
		cell = (position.row, position.col)
		if cell == self._routed:
			return False
		if self.pending is not None and cell == (self.pending.row, self.pending.col):
			# The same cell. A different pin of it still points the touch at the latest pin.
			self.pending = position
			return False
		self._routed = None
		self.pending = position
		self._pendingMember = _memberName(picture, position.row)
		return True

	def activate(self, picture: Picture) -> CellPosition | None:
		""":return: the cell to route now the wait is over, or None where it no longer means the same cell."""
		position, self.pending = self.pending, None
		if position is None or routingIndex(picture, position) is None:
			return None
		if _memberName(picture, position.row) != self._pendingMember:
			return None
		self._routed = (position.row, position.col)
		return position

	def leave(self) -> None:
		"""Forget the pointer, as when it leaves the cells or hover routing is turned off."""
		self.pending = None
		self._pendingMember = None
		self._routed = None

	def pictureChanged(self, old: Picture, new: Picture) -> bool:
		"""Forget the pointer if the display under it changed shape or members.

		:return: whether it was forgotten, for the caller to stop its timer.
		"""
		if old.shape == new.shape:
			return False
		self.leave()
		return True


def _memberName(picture: Picture, row: int) -> str | None:
	member = picture.memberForRow(row)
	return member.name if member is not None else None
