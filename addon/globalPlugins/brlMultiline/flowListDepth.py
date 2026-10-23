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

Like `flowIndent`, this imports nothing from NVDA, so the counting can be tested without one.
Roles are compared by name, as `flowForms` compares them.
"""

from typing import Iterable, Optional

LIST_ROLES = frozenset({"LIST"})
"""The roles that count as one level of nesting, by `controlTypes.Role` member name."""

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


def _isLevel(field) -> bool:
	""":return: whether a control field is a list that nests what is inside it."""
	if not hasattr(field, "get"):
		return False
	role = field.get("role")
	if role is None or _name(role) not in LIST_ROLES:
		return False
	states = field.get("states") or ()
	return not any(_name(state) in CONTROL_STATES for state in states)


def depthBeforeText(commands: Iterable) -> Optional[int]:
	"""How many lists are open where the first of a range's own text begins.

	The first text and not the line's end, because a line belongs where it starts: a line can
	close a sublist after its own words, and the reader is reading the item, not what follows.

	:param commands: what `getTextWithFields` returned — strings and field commands, in order.
	:return: the number of lists enclosing the first non-empty text, 0 for text in no list,
		or None when the range holds no text at all. None is not 0: a collapsed range before
		the cursor says nothing, and the next part of the line must be asked instead.
	"""
	enclosing: list = []
	for command in commands:
		if isinstance(command, str):
			if command:
				return sum(1 for field in enclosing if _isLevel(field))
			continue
		kind = getattr(command, "command", None)
		if kind == "controlStart":
			enclosing.append(getattr(command, "field", None))
		elif kind == "controlEnd" and enclosing:
			enclosing.pop()
	return None


def blockDepth(lists: Optional[int]) -> Optional[int]:
	""":return: the depth a block carries for a count of enclosing lists.

	A line in no list has no depth, which is prose, and draws flat. A line in one list is at
	depth 1, the same number a top level tree item carries — see `SourceBlock.depth`.

	:param lists: what `depthBeforeText` answered.
	"""
	if not lists:
		return None
	return lists
