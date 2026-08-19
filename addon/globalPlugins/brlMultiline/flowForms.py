# BrlMultiline: reading a form as a flow.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Telling a form control apart from prose, and what a flow does differently for one.

A form is where spatial context pays best, and it is also where a single line display is
at its worst: the reader arrives at an edit field and is shown the field, with whatever
said what the field was for now somewhere behind them. Everything else about a form
follows from the packing rules already in `flow`, but two things do not, and both need to
know that a block holds a control.

**A control's prompt is context, and context goes above it.** Arriving at a control, the
window is placed so that what precedes it is on the display with the control below, rather
than putting the control on the top row and filling downward with the rest of the form.
The rows this costs are bounded, because a label that filled the display would be no better
than the field that filled it.

**A short answer does not deserve the whole display.** A control declares a blank row after
it, so that a one row edit holding "Ada" is separated from the next prompt. This is rule 5
of the packing rules — spacing declared by the block — and it is why a source has to know
which blocks are controls at all rather than only being asked about the one at the cursor.

How a control is recognised is deliberately cheap. In browse mode the whole form is in one
virtual buffer, and its field commands are already in NVDA's process: reading them costs no
call to the application, which walking to the `NVDAObject` for each block would. That is the
difference between a probe run on every block of a document and one that could not be.

Focus mode is not this. Once browse mode drops into focus mode the control's `TextInfo`
usually cannot reach the next object at all, so there is nothing above or below to place;
that wants object traversal and an adapter, which is milestone 6.
"""

from typing import Callable, Optional

import textInfos
from logHandler import log

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
	},
)
"""The roles whose blocks are read as controls, by `controlTypes.Role` member name.

Named rather than imported so that a role NVDA has not got — the set is written against
several NVDA versions, and roles are added — costs nothing and matches nothing, and so that
the policy can be read and tested without a running screen reader.

What is not here matters as much. A link is not a control: a page of links would then
declare a blank row after each of them and spend half the display on spacing. A list item
is not one either, for the same reason. What is here is the things a reader stops at and
answers, which is what has a prompt.
"""

MAX_CONTEXT_SHARE = 2
"""The fraction of the band a control's label may take: one row in this many.

Half. A label longer than that is shown by its last rows, since the rows nearest the
control are the ones that say what it is for, and the control itself must stay on the
display — a window filled with the prompt would be the same failure as a window filled with
the field.
"""


def roleName(role) -> str:
	""":return: the name of a role, however it was given.

	`controlTypes.Role` is an enumeration, so its members have a name. Anything else is
	rendered as text, which keeps this working against a field carrying a bare string.
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


def controlProbe(unit: str) -> Callable[[object], bool]:
	"""Build the test a source uses to decide whether a block holds a control.

	:param unit: the reading unit blocks are cut by, so that the probe looks at exactly the
		text the block holds.
	:return: a callable taking a position and answering whether the block there is a control.
	"""

	def probe(info) -> bool:
		return isControlAt(info, unit)

	return probe


def isControlAt(info, unit: str = textInfos.UNIT_LINE) -> bool:
	"""Whether the block at a position holds a form control.

	Answered from the field commands of the text itself, which in a browse mode document
	are already in NVDA's process, rather than by fetching an object per block.

	Never raises. A document whose fields cannot be read is prose as far as this is
	concerned, which costs the reader a row of context and nothing else.

	:param info: a position within the block.
	:param unit: the reading unit the block is cut by.
	:return: whether it holds a control.
	"""
	try:
		probe = info.copy()
		probe.expand(unit)
		fields = probe.getTextWithFields()
	except Exception:
		log.debugWarning("Could not read the fields of a block", exc_info=True)
		return False
	for item in fields:
		if not isinstance(item, textInfos.FieldCommand) or item.command != "controlStart":
			continue
		field = item.field
		if not hasattr(field, "get"):
			continue
		try:
			if not isControlRole(field.get("role")):
				continue
			if not wholeControlHere(field):
				continue
		except Exception:
			log.debugWarning("Could not read a control field", exc_info=True)
			continue
		return True
	return False


def wholeControlHere(field) -> bool:
	"""Whether a control field is a control this block holds, rather than one around it.

	Two blocks would otherwise be misread, and both are common.

	A block *inside* a control — a line of a rich text editor, which is a block of the page
	within an editable region — carries that region's field without being it. NVDA marks the
	difference with `_startOfNode`, and uses it for exactly this purpose when deciding what
	to put in braille.

	A block that only *starts* a control — the first line of that same editor — would take
	the blank row that separates a finished answer from the next prompt and put it in the
	middle of the field. `_endOfNode` is the other half of the same test.

	A buffer that says neither is taken at its word rather than doubted: reporting no
	controls at all would turn the whole of this off silently, which is worse than the
	occasional row of spacing in the wrong place.

	:param field: the control field.
	:return: whether the control begins and ends within this block.
	"""
	return field.get("_startOfNode", True) is not False and field.get("_endOfNode", True) is not False


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


def probeFor(obj, unit: str) -> Optional[Callable[[object], bool]]:
	"""Choose the control probe for what is being read, if it is a kind that has controls.

	Browse mode documents only. Everything else is either prose, where the probe would cost
	a field read per block and answer no every time, or a control the reader is already
	inside, where there is nothing around it to place.

	:param obj: the object or tree interceptor being read.
	:param unit: the reading unit blocks are cut by.
	:return: the probe, or None to read every block as prose.
	"""
	try:
		from treeInterceptorHandler import TreeInterceptor

		if not isinstance(obj, TreeInterceptor):
			return None
	except ImportError:
		# Outside a running NVDA. A tree interceptor is the thing that can stand aside for a
		# control the reader has entered, which is what `passThrough` says.
		if not hasattr(obj, "passThrough"):
			return None
	return controlProbe(unit)
