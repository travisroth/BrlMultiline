# BrlMultiline: reading a form as a flow.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Arriving at a form control, and what a flow does differently for one.

A form is where spatial context pays best, and it is also where a single line display is
at its worst: the reader arrives at an edit field and is shown the field, with whatever
said what the field was for now somewhere behind them. Everything else about a form
follows from the packing rules already in `flow`, but two things do not.

**A control's prompt is context, and context goes above it.** Arriving at a control, the
window is placed so that what precedes it is on the display with the control below, rather
than putting the control on the top row and filling downward. The rows this costs are
bounded, because a prompt that filled the display would be no better than the field that
filled it.

**A short answer does not deserve the whole display.** The control the reader is on asks
for a blank row after it, so that a one row edit holding "Ada" is separated from the next
prompt. This is rule 5 of the packing rules — spacing declared by the block.

How a control is recognised was got wrong once, and the way it was wrong is worth keeping
written down. The first attempt read the field commands of every block's own text, looking
for a control role. It was wrong twice over.

It was wrong about cost. Those fields are in NVDA's process, so one read looks cheap, but
it ran on every block of every document — a tax on all reading, to answer a question that
only matters at the one block the reader has arrived at. On an eight row band that pushed
the fetch budget over on arrival, and the display filled with the marker that means "there
is more I have not read".

It was wrong about correctness, which is the more interesting half. Tabbing to an edit
field or a combo box drops browse mode into focus mode, and the reading position is then
*inside* the control, so the line the reader is on carries the control's field as an
enclosing one rather than as its own. Every attempt to tell "this block is a control" from
"this block is inside a control" by the field's `_startOfNode` therefore answered no for
exactly the two controls a reader most wants a prompt for. A checkbox, which does not enter
focus mode, worked — which is what the hardware run found.

So the question is asked of the object instead. NVDA hands the band the focus object on
every focus change, and its role is authoritative, free, and right in both modes. The
control's own place in the document is then found from that object, which is also what puts
the reading position back at the start of the control rather than inside it — so the block
carries the control's name, role and value as browse mode presents them.
"""

CONTROL_ROLES = frozenset(
	{
		"EDITABLETEXT",
		"COMBOBOX",
		"CHECKBOX",
		"RADIOBUTTON",
		"BUTTON",
		"TOGGLEBUTTON",
		"SLIDER",
		"SPINBUTTON",
		"MENUBUTTON",
		"DROPDOWNBUTTON",
		"SPLITBUTTON",
		"DROPLIST",
		"LISTBOX",
		"RADIOMENUITEM",
		"CHECKMENUITEM",
	},
)
"""The roles a reader stops at and answers, by `controlTypes.Role` member name.

Named rather than imported so that a role NVDA has not got — the set is written against
several NVDA versions, and roles are added — costs nothing and matches nothing, and so that
the policy can be read and tested without a running screen reader.

What is not here matters as much. A link is not a control: a page of links would then spend
a row of spacing on each of them. A list item is not one either. What is here is the things
that have a prompt.
"""

MAX_CONTEXT_SHARE = 2
"""The fraction of the band a control's prompt may take: one row in this many.

Half. A prompt longer than that is shown by its last rows, since the rows nearest the
control are the ones that say what it is for, and the control itself must stay on the
display — a window filled with the prompt would be the same failure as a window filled with
the field.
"""


def roleName(role) -> str:
	""":return: the name of a role, however it was given.

	`controlTypes.Role` is an enumeration, so its members have a name. Anything else is
	rendered as text, which keeps this working against an object carrying a bare string.
	"""
	name = getattr(role, "name", None)
	if isinstance(name, str):
		return name.upper()
	return str(role).upper()


def isControlRole(role) -> bool:
	""":return: whether a role is one a reader stops at and answers."""
	if role is None:
		return False
	return roleName(role) in CONTROL_ROLES


def isControlObject(obj) -> bool:
	"""Whether the reader has arrived at a form control.

	Asked of the object NVDA built its focus regions for, which is the only account of this
	that is right in both browse mode and focus mode. See the module docstring for the two
	ways reading it out of the document's own fields was wrong.

	:param obj: what the reader has arrived at.
	:return: whether to present it as a control.
	"""
	if obj is None:
		return False
	try:
		return isControlRole(getattr(obj, "role", None))
	except Exception:
		# An object that will not say what it is reads as prose, which costs the reader a row
		# of context and nothing else.
		return False


def contextRowsFor(previousRows: int, gapRows: int, bandRows: int) -> int:
	"""How many rows above a control to put on the display, so that its prompt is there.

	:param previousRows: how many rows the block before the control has.
	:param gapRows: how many blank rows sit between the two, from declared spacing.
	:param bandRows: how many rows the band has.
	:return: how far above the control the window should start, 0 to leave it at the top.
	"""
	if previousRows <= 0 or bandRows <= 1:
		return 0
	cap = max(1, bandRows // MAX_CONTEXT_SHARE)
	return min(previousRows + max(0, gapRows), cap)
