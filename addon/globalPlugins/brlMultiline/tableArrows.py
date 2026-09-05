# BrlMultiline: the arrow keys inside a browse mode table.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Reading a browse mode table with the arrow keys, the way a spreadsheet is read.

Inside a table, up and down move a row and keep the column, left and right move a cell and
keep the row, and home and end go to the first and last cell of the row. Outside a table not
one key changes.

**Why the arrows.** A table is two dimensional and browse mode's arrow keys are not. Reading
down a column with the down arrow means reading every cell of every row in between, so the
one thing a table is for — comparing what is under a heading — is the one thing the reading
keys cannot do. NVDA answers that with a second set of commands, control+alt+arrows, which
works and costs a four finger chord per cell; other screen readers answer it with a mode the
reader turns on and off and must remember the state of.

**So it is not a mode.** There is nothing to turn on, nothing to turn off and no state to
remember, because being in a table is already the state: the reader arrows into a table and
the arrows read the table, arrows out of it and they read the page. What makes that safe is
the second half:

**Nothing is ever trapped.** A key is taken only when there is a cell to move to. When there
is not, the key goes to NVDA untouched and does exactly what it does everywhere else — so
the down arrow on the last row leaves the table, the up arrow on the first row leaves it
upwards, and the right arrow in the last cell of a row walks on into whatever follows it.
There is no edge announcement and no special case at the boundary, because there is no
boundary: the fallback *is* NVDA's own key. That is the whole difference between this and a
navigation mode, and it is why nothing has to be turned off to get out.

**How.** Six of `cursorManager.CursorManager`'s movement scripts are wrapped — the ones
browse mode binds the plain arrows, home and end to. The wrapper asks for a destination cell
and, if there is one, moves and reports and NVDA's own script never runs. Shift and control
with the arrows are different scripts and are untouched, so selecting and moving by word
inside a cell still work as they always did.

**The movement is NVDA's own.** `documentBase.DocumentWithTableNavigation._tableFindNewCell`
is what control+alt+arrow moves with, and this calls it with `raiseOnEdge`, which is the same
arithmetic — merged cells, missing cells, the reader's layout table setting — with the edge
raising instead of announcing itself. What is reported on arrival is what NVDA's own table
movement reports. So a reader who uses both gets one behaviour, and this file contains no
opinion about what a table is.

Left alone deliberately: a key with a selection under it, a key resuming say all, a backed up
key queue, focus mode, and a reader who has turned tables off in NVDA's Document Formatting.
Each of those is a case where moving by cell is the wrong answer rather than one where it
would fail. See L{wantsCellMovement}.
"""

import functools

import config
import controlTypes
import speech
from logHandler import log
from scriptHandler import isScriptWaiting, willSayAllResume

from . import bmConfig

try:
	from documentBase import DocumentWithTableNavigation, _Axis, _Movement
except ImportError:  # pragma: no cover - an NVDA whose table navigation has moved.
	DocumentWithTableNavigation = None
	_Axis = None
	_Movement = None
"""NVDA's table navigation, which every one of these keys is answered out of.

Guarded because it is the one thing here with no fallback: without it there is no table
movement to offer and the arrow keys are left exactly as they are, which is what a reader had
before this existed.
"""


MOVES = (
	{
		"script_moveByLine_back": (_Movement.PREVIOUS, _Axis.ROW),
		"script_moveByLine_forward": (_Movement.NEXT, _Axis.ROW),
		"script_moveByCharacter_back": (_Movement.PREVIOUS, _Axis.COLUMN),
		"script_moveByCharacter_forward": (_Movement.NEXT, _Axis.COLUMN),
		"script_startOfLine": (_Movement.FIRST, _Axis.COLUMN),
		"script_endOfLine": (_Movement.LAST, _Axis.COLUMN),
	}
	if _Movement is not None
	else {}
)
"""Which of NVDA's movement scripts this stands in front of, and what each means in a table.

By script name rather than by keystroke because the script is what NVDA binds: `kb:downArrow`
is `moveByLine_forward` on a cursor manager, and a reader who has bound another key to the
same script means the same thing by it. Which also draws the line around what is touched —
selecting, moving by word and moving by paragraph are other scripts and keep their behaviour.

Up and down are the row axis: the same column, the next row along, which is what reading down
a column of figures is. Left and right are the column axis. Home and end are the first and
last cell of the row rather than of the column, because a row is what home and end have
always meant.
"""

_originals: dict[str, object] = {}
"""NVDA's own scripts, kept to be put back. See L{remove}."""

_wrappers: dict[str, object] = {}
"""What was put in their place, so teardown never takes back somebody else's patch."""

_installed = False


def wantsCellMovement(document, gesture) -> bool:
	""":return: whether this keypress is one to answer with a move by cell at all.

	Every no here is a case where moving by cell is the wrong answer, and is settled before
	anything is read out of the document. Whether there *is* a cell that way is a different
	question, and L{moveToCell} asks it afterwards.

	:param document: what NVDA ran the movement script on.
	:param gesture: the keypress behind it.
	"""
	if DocumentWithTableNavigation is None:
		return False
	if not isinstance(document, DocumentWithTableNavigation):
		# Something that reads text but knows nothing of tables: a review cursor over an
		# object, or an editor NVDA gives a cursor manager to.
		return False
	if getattr(document, "passThrough", False):
		# Focus mode, where the arrows belong to the control the reader is working in. NVDA
		# does not route these scripts then, so this is a guard rather than a case.
		return False
	if willSayAllResume(gesture):
		# The arrow means "carry on reading from here". Moving by cell would answer a
		# question the reader did not ask, and would stop the say all they did.
		return False
	if isScriptWaiting():
		# NVDA's own policy for both kinds of movement: with keypresses backed up, moving and
		# reporting costs more than the movement is worth. NVDA's own script says the same
		# thing, so handing the key over changes nothing except where it is decided.
		return False
	# The reader's own answer to "treat tables as tables". Taking over the arrow keys is
	# something this add-on does on its own account, which is exactly what that setting
	# governs. See `bmConfig.wantsTables`.
	return bmConfig.wantsTables()


def moveToCell(document, gesture, movement, axis) -> bool:
	"""Move the browse mode cursor to the next cell along, where there is one.

	:param document: the browse mode document the key was pressed in.
	:param gesture: the keypress behind it.
	:param movement: which way, as `documentBase._Movement`.
	:param axis: along rows or along columns, as `documentBase._Axis`.
	:return: whether the key was answered here. **False is the important answer**: it means
		the reader gets NVDA's own movement for that key, and it is what keeps the table from
		trapping them.
	"""
	if not wantsCellMovement(document, gesture):
		return False
	moved = False
	try:
		selection = document.selection
		if not selection.isCollapsed:
			# Something is selected, and an arrow key over a selection is about the
			# selection.
			return False
		try:
			here = document._getTableCellCoords(selection)
		except LookupError:
			# Not in a table, which is most of a page. Asked directly rather than through
			# `_tableFindNewCell`, which says "not in a table cell" out loud — and an arrow
			# key on ordinary prose must not announce anything.
			return False
		try:
			cell, info, tableSelection = document._tableFindNewCell(movement, axis, raiseOnEdge=True)
		except LookupError:
			# The edge of the table this way. The key goes to NVDA and the reader walks out
			# of the table with it, which is the whole of not trapping.
			return False
		if (cell.row, cell.col) == (here.row, here.col):
			# Home in the first cell of a row, or end in the last: the destination is where
			# the reader already is. A key that would move nothing is not a key worth
			# keeping, so NVDA's own start or end of line gets it instead.
			return False
		# What NVDA's own table movement does on arrival, in the order it does it. Spoken
		# before the selection is set, because setting it can move the focus and mutate the
		# document under the position just found. See
		# `documentBase.DocumentWithTableNavigation._tableMovementScriptHelper`.
		formatConfig = config.conf["documentFormatting"].copy()
		formatConfig["reportTables"] = True
		speech.speakTextInfo(info, formatConfig=formatConfig, reason=controlTypes.OutputReason.CARET)
		info.collapse()
		document.selection = info
		moved = True
		# NVDA's record of where a run of table movements began, which is what carries a
		# column through merged cells. Set after the move, so a failure here costs the
		# reader nothing but that memory.
		document._lastTableSelection = tableSelection
	except Exception:
		log.debugWarning("Could not move to another table cell", exc_info=True)
	return moved


def _movingByCell(original, movement, axis):
	"""Wrap one of NVDA's movement scripts so that it moves by cell inside a table.

	:param original: the script as NVDA defines it.
	:param movement: which way this key moves, as `documentBase._Movement`.
	:param axis: which axis it moves along, as `documentBase._Axis`.
	:return: the replacement, indistinguishable from the original to everything that inspects
		a script. The say all resume mark on the line movement scripts and the description
		input help reads both live on the function object, and `functools.wraps` carries them
		over — including the description, which is left saying what the key does on a page,
		because that is what it does everywhere but in a table.
	"""

	@functools.wraps(original)
	def moveByCellOrAsNvdaWould(document, gesture):
		if moveToCell(document, gesture, movement, axis):
			return None
		return original(document, gesture)

	return moveByCellOrAsNvdaWould


def install() -> None:
	"""Start answering the arrow keys inside browse mode tables.

	Installed when the add-on loads rather than when a flow starts: this is how a table is
	read, not how one is displayed, and it is as much use to a reader who never lays a table
	out in columns as to one who always does.
	"""
	global _installed
	if _installed:
		return
	if not MOVES:
		log.debugWarning(
			"BrlMultiline: NVDA's table navigation could not be found, so the arrow keys are left alone",
		)
		return
	try:
		from cursorManager import CursorManager

		# Built before anything is replaced, so a failure leaves NVDA's own scripts untouched.
		wrapped = {
			name: _movingByCell(getattr(CursorManager, name), movement, axis)
			for name, (movement, axis) in MOVES.items()
		}
	except Exception:
		# Browse mode then keeps NVDA's own arrow keys, which is what a reader had before any
		# of this and is a working way to read a table.
		log.error(
			"BrlMultiline: could not take over the arrow keys in browse mode tables; "
			"they will move by line and character as NVDA moves them",
			exc_info=True,
		)
		return
	for name, wrapper in wrapped.items():
		_originals[name] = getattr(CursorManager, name)
		_wrappers[name] = wrapper
		setattr(CursorManager, name, wrapper)
	_installed = True


def remove() -> None:
	"""Stop, putting browse mode's own keys back. Safe to call when nothing is installed.

	Each script goes back only if it is still the one this module put there. Another add-on
	may have wrapped it since — this is a class attribute anyone can reach — and restoring
	NVDA's own over that would silently undo their work, which is the more damaging half of a
	shared monkey patch. See `panning.remove`, which says the same thing about NVDA's panning
	commands.
	"""
	global _installed
	if not _installed:
		return
	try:
		from cursorManager import CursorManager

		for name, original in _originals.items():
			if getattr(CursorManager, name, None) is not _wrappers.get(name):
				log.debugWarning(
					f"BrlMultiline: {name} has been replaced since it was wrapped; "
					"leaving it as it is rather than undoing whatever replaced it",
				)
				continue
			setattr(CursorManager, name, original)
	except Exception:
		log.error("BrlMultiline: could not give browse mode its own arrow keys back", exc_info=True)
	finally:
		_originals.clear()
		_wrappers.clear()
		_installed = False
