# BrlMultiline: reading a form as a flow.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Arriving at a form control, and what a flow does differently for one.

1. A control's prompt is context, so arriving at a control shows what precedes it above,
   bounded by `MAX_CONTEXT_SHARE`.
2. The control asks for a blank row after it, its declared spacing.

A control is recognised from the focus object's role, which is right in both browse and focus
mode and costs nothing, not from each block's fields, which cost every read and see a focused
edit as enclosing. Its place in the document is found from that object. See "Controls, labels
and edit fields" in docs/design/spatial-reading-plan.md.
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

Named, so a role an NVDA version lacks matches nothing. Only things with a prompt: not links,
which would each spend a row, nor list items.
"""

MAX_CONTEXT_SHARE = 2
"""The fraction of the band a control's prompt may take: one row in this many.

Half. A longer prompt shows its last rows, nearest the control, which stays on the display.
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

	Asked of the object NVDA built its focus regions for. See the module docstring.

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


def isEditableObject(obj) -> bool:
	"""Whether an object owns a text caret the reader may move.

	The role, the EDITABLE state, or NVDA's `EditableText` behaviour, which covers controls
	reporting neither, such as Windows 11 Notepad's editor.

	:param obj: the object to test.
	:return: whether its text should be eligible for an editable spatial flow.
	"""
	if obj is None:
		return False
	try:
		if roleName(getattr(obj, "role", None)) == "EDITABLETEXT":
			return True
		states = getattr(obj, "states", None) or ()
		if any(roleName(state) == "EDITABLE" for state in states):
			return True
		return _behavesAsEditableText(obj)
	except Exception:
		return False


def _behavesAsEditableText(obj) -> bool:
	"""Whether NVDA built this object with its editable text behaviour.

	`editableText.EditableText`, which NVDA mixes into everything it treats as editable.

	:param obj: the object to test.
	:return: whether NVDA chose the editable text behaviour for it.
	"""
	try:
		import editableText
	except ImportError:
		# The unit harness, unless it registered a stand-in. An answer of no leaves the
		# role and state answers standing, which is all the harness usually needs.
		return False
	try:
		return isinstance(obj, editableText.EditableText)
	except Exception:
		return False


def isMultilineEditable(obj) -> bool:
	"""Whether an object is an edit with lines of its own rather than a single field.

	A single line field is read as a line of its page; a multi line edit is read as its own
	lines, since the page cannot present it. The MULTILINE state, or an editable DOCUMENT
	without it, as Notepad and Word report.

	:param obj: the object to test.
	:return: whether it is an edit the reader can put more than one line into.
	"""
	if not isEditableObject(obj):
		return False
	try:
		states = getattr(obj, "states", None) or ()
		if any(roleName(state) == "MULTILINE" for state in states):
			return True
		return roleName(getattr(obj, "role", None)) == "DOCUMENT"
	except Exception:
		# Read the way every other judgement in this module is: an object that will not say
		# is not one to take a chance on, and the page is the safe answer for it.
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
