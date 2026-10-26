# BrlMultiline: how deep in a document's lists a line sits.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""How many lists a document line sits inside, read from what NVDA already fetched.

A browse mode line's depth is carried only by the control fields around its text. These are
already in hand: `TextInfoRegion._addTextWithFields` fetches them for every line it reads, and
`flowSources.FlowRegion` passes them here, so the only cost is a walk.

Lists and items are counted apart and the larger is taken, since a browser gives both and
Word's UIA document gives only the items. See docs/design/structured-presentation-plan.md.

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

A list box (`<select size="5">`) calls itself a LIST but is one line, so a list the reader can
focus is not a level.
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

	Browse mode cuts a long item into lines of at most a hundred characters by default, each a
	block. NVDA's `_startOfNode` on the enclosing field says whether the range begins at the
	field's start, in a virtual buffer and in Word; a field that does not say is taken as the start.

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

	The first text, not the line's end, since a line can close a sublist after its own words.

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
