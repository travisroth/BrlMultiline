# BrlMultiline: drawing how deep a block sits.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""How deep a block sits, turned into cells.

Imports nothing from NVDA. Where a depth comes from is the source's business.

1. True depth is drawn while the deepest item fits within `MAX_INDENT_SHARE` of the row;
   otherwise the band rebases, its shallowest depth at the left margin.
2. The whole band rebases at once, so items already shown can move. One baseline, no history.
3. A wrapped row is indented past where a child would start, in blank cells, so it never
   reads as a child. See `CONTINUATION_EXTRA`.

See docs/design/structured-presentation-plan.md.
"""

import dataclasses
from typing import Iterable, Optional, Sequence

BLANK_CELL = 0
"""A blank cell, matching `flow.BLANK_CELL` and NVDA's own use of 0."""

LEVEL_CELL = 0xC0
"""Dots 7 and 8, for the style that marks each level rather than spacing it.

The structure mark, as for a pending row; unambiguous, since it appears only in the indent.
"""

FOCUS_CELL = 0xE4
"""Dots 3, 6, 7 and 8: the mark on the left of the row the focus is on.

A solid bar across two cells, containing the level mark rather than resembling it. See
`bmConfig.shouldMarkLineFocus`.
"""

FOCUS_WIDTH = 2
"""How many cells the focus mark is drawn with.

Two, unlike a one cell level mark, and one level of the default style.
"""

TWO_SPACES = "twoSpaces"
ONE_SPACE = "oneSpace"
DOTS_78 = "dots78"

INDENT_STYLES: tuple[str, ...] = (TWO_SPACES, ONE_SPACE, DOTS_78)
"""How one level of depth may be drawn, in the order the settings dialog offers them.

Two spaces by default, one for a narrow band, and dots 7 and 8 to feel the level.
"""

CELLS_PER_LEVEL: dict[str, int] = {TWO_SPACES: 2, ONE_SPACE: 1, DOTS_78: 1}
"""How many cells one level of depth costs, per style."""

DEFAULT_STYLE = TWO_SPACES
"""The style used when none was configured, or when one was that this version has not got."""

MAX_INDENT_SHARE = 4
"""The fraction of the row indent may take before the band rebases: one cell in this many.

A quarter, so indent does not crowd out content, as `flowForms.MAX_CONTEXT_SHARE` keeps a
prompt from doing. A share, so a wider band shows deeper true depth.
"""

CONTINUATION_EXTRA = 1
"""How much further than a child a wrapped row is indented, in half levels.

One half level, rounded up, at least a cell. At one cell a level it lands where a grandchild
would; the marked styles stay unambiguous, since its extra cells are blank.
"""


def focusMark(indentCells: int) -> tuple[int, ...]:
	"""The cells to draw over the left of a row to say the focus is on it.

	Drawn into the indent, only where it is wide enough, so nothing moves; a row at the margin
	gets none.

	:param indentCells: how many cells of indent the row is drawn with.
	:return: the cells to write at the left of the row, empty where there is no room.
	"""
	if indentCells < FOCUS_WIDTH:
		return ()
	return (FOCUS_CELL,) * FOCUS_WIDTH


def cellsPerLevel(style: Optional[str]) -> int:
	""":return: how many cells one level costs in a style, defaulting for an unknown one.

	An unknown style, from a later version or by hand, reads as the default.
	"""
	return CELLS_PER_LEVEL.get(style or DEFAULT_STYLE, CELLS_PER_LEVEL[DEFAULT_STYLE])


def _levelCells(style: Optional[str]) -> tuple[int, ...]:
	""":return: the cells one level of indent is drawn with."""
	if style == DOTS_78:
		return (LEVEL_CELL,)
	return (BLANK_CELL,) * cellsPerLevel(style)


@dataclasses.dataclass(frozen=True)
class IndentPlan:
	"""One band's indent arithmetic, fixed for as long as the band shows what it shows.

	Built by `planFor`. Frozen, as part of a rendering's key; see `flow.RenderKey.indent`.
	"""

	baseline: Optional[int] = None
	"""The depth drawn flush left, or None when nothing on the band has a depth at all.

	None is the ordinary case and the one that must cost nothing: prose has no depth, and a
	plan over prose has to answer "no indent" without any arithmetic.
	"""

	style: str = DEFAULT_STYLE
	"""How one level is drawn."""

	maxLevels: int = 0
	"""How many levels of indent the band has cells for.

	Depth past this is drawn at this indent, so no item is pushed off the display.
	"""

	noteLevel: Optional[int] = None
	"""The absolute level the left margin stands for, when that is worth saying.

	Set only where the band has rebased below the top of the structure.
	"""

	def levelsFor(self, depth: Optional[int]) -> int:
		""":return: how many levels of indent a block of this depth is drawn with."""
		if depth is None or self.baseline is None:
			return 0
		return max(0, min(depth - self.baseline, self.maxLevels))

	def cellsFor(self, depth: Optional[int]) -> int:
		""":return: how many cells of indent a block of this depth is drawn with."""
		return self.levelsFor(depth) * cellsPerLevel(self.style)

	def prefixFor(self, depth: Optional[int]) -> tuple[int, ...]:
		""":return: the indent cells for a block's first row."""
		return _levelCells(self.style) * self.levelsFor(depth)

	def continuationCellsFor(self, depth: Optional[int]) -> int:
		""":return: how many cells of indent this block's wrapped rows are drawn with."""
		if depth is None or self.baseline is None:
			return 0
		return self.cellsFor(depth) + self._extraCells()

	def continuationPrefixFor(self, depth: Optional[int]) -> tuple[int, ...]:
		""":return: the indent cells for a block's second and later rows.

		The item's own indent, then blank cells past where a child would begin, in every style.
		"""
		if depth is None or self.baseline is None:
			return ()
		return self.prefixFor(depth) + (BLANK_CELL,) * self._extraCells()

	def _extraCells(self) -> int:
		""":return: how far past a child's indent a continuation goes. See `CONTINUATION_EXTRA`."""
		perLevel = cellsPerLevel(self.style)
		half = max(1, (perLevel * CONTINUATION_EXTRA + 1) // 2)
		return perLevel + half

	@property
	def isFlat(self) -> bool:
		""":return: whether this plan draws nothing, which is the common case."""
		return self.baseline is None or self.maxLevels <= 0


FLAT = IndentPlan()
"""The plan for content with no depth. Shared, since it is frozen and holds nothing."""


def shouldRebase(
	plan: IndentPlan,
	depths: Iterable[Optional[int]],
	numCols: int,
	style: Optional[str] = None,
	maxShare: int = MAX_INDENT_SHARE,
) -> bool:
	"""Whether a band's plan has stopped working for what the band is showing.

	Not a comparison with the ideal plan, which would make the margin twitch while arrowing.
	A plan stops working when:

	1. It would draw a negative indent, for an item shallower than the baseline.
	2. Items at different depths past the cap would draw the same indent.
	3. It is for another style, band size, or presence of depth.

	:param plan: the plan in force.
	:param depths: the depth of each block the band is showing.
	:param numCols: the width of the band.
	:param style: how one level is to be drawn.
	:param maxShare: one cell in this many may go to indent.
	:return: whether to build a fresh plan and lay the band out again.
	"""
	known = [depth for depth in depths if depth is not None]
	fresh = planFor(known, numCols, style=style, maxShare=maxShare)
	if not known:
		# Left the tree. A flat plan over flat content, which is where everything starts.
		return not plan.isFlat
	if plan.isFlat:
		return not fresh.isFlat
	if plan.style != fresh.style or plan.maxLevels != fresh.maxLevels:
		return True
	if plan.baseline is None:
		return True
	if min(known) < plan.baseline:
		return True
	return max(known) - plan.baseline > plan.maxLevels


def planFor(
	depths: Iterable[Optional[int]],
	numCols: int,
	style: Optional[str] = None,
	maxShare: int = MAX_INDENT_SHARE,
) -> IndentPlan:
	"""Work out how a band's depths are drawn.

	:param depths: the depth of each block the band is showing, `None` for those with none.
	:param numCols: the width of the band, in cells.
	:param style: how one level is drawn. The default is used where this is None or unknown.
	:param maxShare: one cell in this many may go to indent. See `MAX_INDENT_SHARE`.
	:return: the plan, which is `FLAT` when nothing on the band has a depth.
	"""
	known: Sequence[int] = [depth for depth in depths if depth is not None]
	if not known or numCols < 1:
		return FLAT
	style = style if style in INDENT_STYLES else DEFAULT_STYLE
	perLevel = cellsPerLevel(style)
	allowance = max(0, numCols // max(1, maxShare))
	maxLevels = allowance // perLevel
	if maxLevels <= 0:
		# A band too narrow to indent at all. Drawn flat rather than badly: one cell of
		# indent on a row this short costs more than the depth is worth.
		return FLAT
	shallowest = min(known)
	deepest = max(known)
	if (deepest - 1) * perLevel <= allowance:
		# The true depth fits. Draw it, and say nothing about levels, because the left margin
		# means what it appears to mean.
		return IndentPlan(baseline=1, style=style, maxLevels=maxLevels, noteLevel=None)
	return IndentPlan(
		baseline=shallowest,
		style=style,
		maxLevels=maxLevels,
		noteLevel=shallowest if shallowest > 1 else None,
	)
