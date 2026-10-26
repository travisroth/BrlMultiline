# BrlMultiline: the arrow keys inside a browse mode table.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Reading a browse mode table with the arrow keys, the way a spreadsheet is read.

Inside a table, up and down move a row and keep the column, left and right move a cell and
keep the row, and home and end go to the first and last cell of the row. Outside a table not
one key changes.

1. Off until the reader turns it on, since these are NVDA's keys. See
   `bmConfig.shouldArrowsMoveByRow`.
2. Not a mode: being in a table is the state.
3. Nothing is trapped: with no cell to move to, the key goes to NVDA untouched, so the arrows
   walk out of the table.
4. Six of `cursorManager.CursorManager`'s movement scripts are wrapped; shift and control
   variants are other scripts and untouched.
5. The movement is NVDA's own `documentBase.DocumentWithTableNavigation._tableFindNewCell`,
   with `raiseOnEdge`, reported as NVDA's table movement reports.

Left alone: a selection, say all resuming, a backed up key queue, focus mode, and tables turned
off in Document Formatting. See `wantsCellMovement`.
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

Guarded; without it the arrow keys are left as they are.
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

By script name, so a reader's own binding to the same script counts. Up and down keep the
column; left and right keep the row; home and end are the row's first and last cells.
"""

_originals: dict[str, object] = {}
"""NVDA's own scripts, kept to be put back. See L{remove}."""

_wrappers: dict[str, object] = {}
"""What was put in their place, so teardown never takes back somebody else's patch."""

_installed = False


def wantsCellMovement(document, gesture) -> bool:
	""":return: whether this keypress is one to answer with a move by cell at all.

	Settled before reading the document; whether there is a cell is `moveToCell`'s question.

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
	if not bmConfig.shouldArrowsMoveByRow():
		# Off unless the reader turned it on: these are NVDA's own keys, and the add-on
		# changes nothing of NVDA's by default. See `bmConfig.shouldArrowsMoveByRow`.
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
	:return: whether the key was answered here. False gives NVDA's own movement.
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
	:return: the replacement, carrying the say all mark and description over through
		`functools.wraps`.
	"""

	@functools.wraps(original)
	def moveByCellOrAsNvdaWould(document, gesture):
		if moveToCell(document, gesture, movement, axis):
			return None
		return original(document, gesture)

	return moveByCellOrAsNvdaWould


def install() -> None:
	"""Start answering the arrow keys inside browse mode tables.

	When the add-on loads, not with a flow, since this is how a table is read.
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

	Each script goes back only if it is still this module's. See `patches.remove`.
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
