# BrlMultiline: drawing how deep a block sits.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""How deep a block sits, turned into cells.

A tree has depth and the depth is information. NVDA does not draw it: in a nested list it
marks each point where a list begins and leaves the reader to hold the level in their head,
and in a tree view it says the level in words if asked. Neither survives being read across
eight rows at once, which is the whole point of a flow — the reader is feeling the shape of
several items together, and the shape is exactly what a flat left margin destroys.

So a block carries a depth and the renderer draws it. This module is the arithmetic in
between, and it is here on its own for the reason `flow.py` is: it imports nothing from
NVDA, so every rule below can be tested without a screen reader or a display.

**Indent is relative, and the band is the unit it is relative to.** Absolute indent from the
root spends cells a 32 cell display has not got — six levels into a folder tree at two cells
a level is a quarter of the row gone before a word of the item. So the shallowest depth on
the band is drawn at the left margin and everything else is indented from it.

**Relative, but only when it has to be.** If the deepest item on the band can be drawn at
its true depth without spending more than a share of the row, that is what happens, and the
reader gets the real picture for free. The rebasing is what the display falls back to, not
what it does by default. `MAX_INDENT_SHARE` is where the line is drawn, and it is a share
rather than a count because the answer is different on a 32 cell row and an 80 cell one.

**The whole band rebases at once, and items already shown do move.** An item's indent
depends on what else is on the band, so bringing a shallower item into view at the bottom
shifts everything above it. That is chosen rather than tolerated: the alternative is
remembering what indent each item was last drawn at, which is a second history to keep in
step with the window and buys nothing the reader asked for. One baseline, one arithmetic.

**A wrapped row is indented further than a child would be.** This is the rule that is easy
to leave out and impossible to read without. A long item that does not fit on one row
continues on the next, and a continuation drawn at the child indent is a child as far as the
fingers are concerned. So a continuation goes past where a child would start — see
`CONTINUATION_EXTRA` — and the cells it goes past with are blank even in a style that marks
its levels, so that "further in than a child" and "one level deeper" never draw the same
thing.

What is *not* here: which blocks have a depth at all, and where the depth came from. That is
the source's business — `positionInfo["level"]` for an object, a document's own control
fields for a nested list — and this module only ever sees the number.
"""

import dataclasses
from typing import Iterable, Optional, Sequence

BLANK_CELL = 0
"""A blank cell, matching `flow.BLANK_CELL` and NVDA's own use of 0."""

LEVEL_CELL = 0xC0
"""Dots 7 and 8, for the style that marks each level rather than spacing it.

The same shape NVDA uses for a row cut mid word and this add-on uses for a pending row, and
for the same reason in all three: it is the mark that means "structure", not content. It is
never ambiguous with those here because it appears only in the indent, before any content.
"""

FOCUS_CELL = 0xE4
"""Dots 3, 6, 7 and 8: the mark on the left of the row the focus is on.

The bottom four dots of the cell, drawn full width across two cells. A solid bar under the
fingers, which is the loudest thing on a row that is otherwise text, and it contains the
level mark — dots 7 and 8 — rather than resembling it, so a marked row in the dots 78 style
cannot be read as one level deeper.

The cursor already says where the focus is, and it is not enough. By default it is dots 7
and 8 blinking *under* the text, which has to be found by reading the row it is under; a
reader running a finger down eight rows of a folder tree to see where they are has to read
every row to find it. A mark at the left margin is in the same column on every row, so the
answer is one pass of the hand rather than eight readings.
"""

FOCUS_WIDTH = 2
"""How many cells the focus mark is drawn with.

Two, so that it cannot be mistaken for the one cell of a level mark, and so that it is
still there to feel when a hand is moving quickly. It is also exactly one level of the
default style, which is what lets it be drawn without costing the row a cell of text.
"""

TWO_SPACES = "twoSpaces"
ONE_SPACE = "oneSpace"
DOTS_78 = "dots78"

INDENT_STYLES: tuple[str, ...] = (TWO_SPACES, ONE_SPACE, DOTS_78)
"""How one level of depth may be drawn, in the order the settings dialog offers them.

Two spaces is the default because it is what sighted readers see and what every text
outline uses. One space is for a narrow band, where two is a luxury. Dots 7 and 8 is for a
reader who would rather feel the level than count blank cells, and it is the only style
whose indent can be told from a genuinely blank run of cells.
"""

CELLS_PER_LEVEL: dict[str, int] = {TWO_SPACES: 2, ONE_SPACE: 1, DOTS_78: 1}
"""How many cells one level of depth costs, per style."""

DEFAULT_STYLE = TWO_SPACES
"""The style used when none was configured, or when one was that this version has not got."""

MAX_INDENT_SHARE = 4
"""The fraction of the row indent may take before the band rebases: one cell in this many.

A quarter. Past that the indent is eating the content it exists to organise, which is the
same failure `flowForms.MAX_CONTEXT_SHARE` guards against for a control's prompt — a display
filled with context is no better than a display filled without it.

A share rather than a number of levels, because the question is how many cells are left for
the item, and that depends on the band. Eight cells of indent is a quarter of a Monarch's
row and a tenth of an 80 cell one, and the reader of the wider display should get the deeper
true picture rather than the narrower display's compromise.
"""

CONTINUATION_EXTRA = 1
"""How much further than a child a wrapped row is indented, in half levels.

One half level, rounded up and never less than a cell. A continuation must sit *past* where
a child would start or the fingers read it as one: at two cells a level, a child begins at
two and a continuation at three, which leaves a grandchild at four and everything
distinguishable. At one cell a level there is no half cell to spend, so a continuation lands
where a grandchild would; the styles that mark their levels stay unambiguous anyway, because
the extra cells are blank and a grandchild's are not.
"""


def focusMark(indentCells: int) -> tuple[int, ...]:
	"""The cells to draw over the left of a row to say the focus is on it.

	Drawn *into* the row's indent, never in front of it: the mark is only offered where the
	indent is already at least as wide, so it costs no cell of text and moves nothing. A row
	at the left margin — a top level item, a flat list, a paragraph — gets no mark, because
	there is nowhere to put one that would not push the row's own content sideways and make
	the focused row the one that does not line up with the rest.

	That is a real limit rather than a compromise: at one cell a level, an item one level in
	has one cell of indent and cannot carry the mark. It is the reason the width is offered
	as an answer here instead of assumed by the caller.

	:param indentCells: how many cells of indent the row is drawn with.
	:return: the cells to write at the left of the row, empty where there is no room.
	"""
	if indentCells < FOCUS_WIDTH:
		return ()
	return (FOCUS_CELL,) * FOCUS_WIDTH


def cellsPerLevel(style: Optional[str]) -> int:
	""":return: how many cells one level costs in a style, defaulting for an unknown one.

	An unknown style is the setting written by a later version of the add-on, or by hand. It
	reads as the default rather than raising, because an indent nobody recognises is not
	worth refusing to draw a display over.
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

	Built by `planFor` from the depths actually on the display, and then asked about each
	block as it is rendered. Frozen because it is part of a rendering's key: two blocks drawn
	under different plans are two different renderings, and one must never be served for the
	other. See `flow.RenderKey.indent`.
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

	Depth past this is drawn at this indent rather than beyond it. The alternative is an item
	pushed off the right of the display by its own position in a tree, which tells the reader
	where it sits by refusing to tell them what it says.
	"""

	noteLevel: Optional[int] = None
	"""The absolute level the left margin stands for, when that is worth saying.

	Set only where the band has rebased to something other than the top of the structure, so
	that the reader is not told "level 1" about a margin that is level 1. What is done with
	it belongs to whatever draws the band's top row; this module only decides whether there
	is anything to say.
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

		The item's own indent, then blank cells past where a child would begin. Blank in
		every style, including the ones that mark their levels: the marks say "this is how
		deep I am", and a continuation is not deeper, it is the same item still going.
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

	Asked instead of simply comparing against the ideal plan, and the difference is the
	whole reason this exists. The ideal baseline changes every time a slightly different set
	of items is on the display, and rebuilding on each of those would make the left margin
	twitch under a reader's fingers while they arrow through a list — a display that moves
	when nothing moved. So a plan is kept until it genuinely cannot draw what is there, and
	the band pays the shift only when it has bought something.

	Three things stop a plan working:

	1. **It would have to draw a negative indent.** Walking a tree in visible order comes
	   back out of a subtree, so an item shallower than the baseline arrives at the bottom of
	   the band. There is nothing to the left of the margin.
	2. **The band has run out of levels.** The reader has gone deep enough that the deepest
	   item is past the cap and items at different depths are drawing the same indent, which
	   is the one thing indent exists to prevent.
	3. **The plan is for something else** — a different style, a resized band, or depth
	   arriving where there was none and the other way round.

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
