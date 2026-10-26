# BrlMultiline: how deep in a document's lists a line sits.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""How many lists a document line sits inside, read from what NVDA already fetched.

An object knows its own level and says so in `positionInfo["level"]`. A line of a browse mode
document does not: a nested list there is text in document order, and its depth is carried
only by the control fields around the text. Reading those fields for every block is the tax
M0 of the structured presentation plan warned against — `getTextWithFields` once more per
line, to answer a question most lines do not have.

**But NVDA has already asked.** `TextInfoRegion._addTextWithFields` calls `getTextWithFields`
for every line it reads, and what comes back opens with a `controlStart` for every field that
encloses the start of the range — the document, the list, the list item, the list inside it —
before the first piece of text. So the depth is a count over a list that is already in hand,
and the only cost is the walk. `flowSources.FlowRegion` hands that list here on its way past.

**Lists are counted two ways, and the larger answer is taken.** A browser's virtual buffer
wraps each level in a LIST and each item in a LISTITEM, so either count gives the depth. Word's
UIA document gives the items and never the list around them: a top level item arrives as
`LISTITEM, LINK` and an item one level in as `LISTITEM, LISTITEM`. Counting lists alone left
every Word line at 0, which is why Word drew flat until this said otherwise.

Like `flowIndent`, this imports nothing from NVDA, so the counting can be tested without one.
Roles are compared by name, as `flowForms` compares them.
"""

from typing import Iterable, NamedTuple, Optional

LIST_ROLES = frozenset({"LIST"})
"""The roles of a list that nests what is inside it, by `controlTypes.Role` member name."""

ITEM_ROLES = frozenset({"LISTITEM"})
"""The roles of an item of such a list, counted for a document that gives no list around them."""

CONTROL_STATES = frozenset({"FOCUSABLE"})
"""States that make a list a control rather than a document's structure.

A list box in a page — `<select size="5">` — calls itself a LIST in NVDA, and is read as one
line however many options it holds. Counting it would put the line holding it one level
deeper than the list item it sits in, so a list the reader can focus is not a level.
"""


def _name(value) -> str:
	""":return: the name of a role or state, however it was given. See `flowForms.roleName`."""
	name = getattr(value, "name", None)
	if isinstance(name, str):
		return name.upper()
	return str(value).upper()


def _roleOf(field) -> str:
	""":return: a control field's role by name, or an empty string where it has none."""
	if not hasattr(field, "get"):
		return ""
	role = field.get("role")
	return "" if role is None else _name(role)


def _isControl(field) -> bool:
	""":return: whether a control field is one the reader can focus. See `CONTROL_STATES`."""
	if not hasattr(field, "get"):
		return False
	states = field.get("states") or ()
	return any(_name(state) in CONTROL_STATES for state in states)


def _levels(enclosing: list) -> int:
	""":return: how many levels of list the fields enclosing some text stand for.

	Lists and items are counted apart and the larger count is the depth, so a document that gives
	both says the same as one that gives only its items. Nothing inside a list the reader can
	focus counts: that list is a control, and its options are not the document's structure.
	"""
	lists = items = 0
	for field in enclosing:
		role = _roleOf(field)
		if role in LIST_ROLES:
			if _isControl(field):
				break
			lists += 1
		elif role in ITEM_ROLES:
			items += 1
	return max(lists, items)


def continuesItem(enclosing: list) -> bool:
	"""Whether text inside a list item is not where that item begins.

	**One item can be several lines.** NVDA's browse mode cuts a long paragraph into lines of at
	most its "maximum number of characters on one line", a hundred by default, and each line is a
	block of its own. The first is where the item begins; the rest carry on from it. Drawn alike,
	the second line of a top level bullet sat at the margin under its own first line's wrapped rows
	— the reader's "the fifth line is not indented, then every other line" on a page of long
	bullets.

	The field says. NVDA marks each enclosing field with `_startOfNode`, true only where the range
	begins at the start of that field, in a virtual buffer and in Word alike. A field that does not
	say is taken as the start, which is how every line was drawn before.

	:param enclosing: the fields open at the text, outermost first.
	:return: whether the innermost list item there began on an earlier line.
	"""
	innermost = None
	for field in enclosing:
		role = _roleOf(field)
		if role in LIST_ROLES and _isControl(field):
			# A list box's options are not the document's items, as in `_levels`.
			break
		if role in ITEM_ROLES:
			innermost = field
	return innermost is not None and innermost.get("_startOfNode", True) is False


class ListPlace(NamedTuple):
	"""Where a line's text sits in the document's lists."""

	levels: int
	"""How many levels of list enclose it. See `_levels`."""

	continues: bool
	"""Whether it carries on an item that began on an earlier line. See `continuesItem`."""


def placeBeforeText(commands: Iterable) -> Optional[ListPlace]:
	"""Where the first of a range's own text sits in the lists around it.

	:param commands: what `getTextWithFields` returned — strings and field commands, in order.
	:return: the place, or None when the range holds no text. See `depthBeforeText`.
	"""
	enclosing: list = []
	for command in commands:
		if isinstance(command, str):
			if command:
				return ListPlace(_levels(enclosing), continuesItem(enclosing))
			continue
		kind = getattr(command, "command", None)
		if kind == "controlStart":
			enclosing.append(getattr(command, "field", None))
		elif kind == "controlEnd" and enclosing:
			enclosing.pop()
	return None


def depthBeforeText(commands: Iterable) -> Optional[int]:
	"""How many lists are open where the first of a range's own text begins.

	The first text and not the line's end, because a line belongs where it starts: a line can
	close a sublist after its own words, and the reader is reading the item, not what follows.

	:param commands: what `getTextWithFields` returned — strings and field commands, in order.
	:return: the levels of list enclosing the first non-empty text, 0 for text in no list,
		or None when the range holds no text at all. None is not 0: a collapsed range before
		the cursor says nothing, and the next part of the line must be asked instead.
	"""
	place = placeBeforeText(commands)
	return None if place is None else place.levels


def blockDepth(lists: Optional[int]) -> Optional[int]:
	""":return: the depth a block carries for a count of enclosing lists.

	A line in no list has no depth, which is prose, and draws flat. A line in one list is at
	depth 1, the same number a top level tree item carries — see `SourceBlock.depth`.

	:param lists: what `depthBeforeText` answered.
	"""
	if not lists:
		return None
	return lists
