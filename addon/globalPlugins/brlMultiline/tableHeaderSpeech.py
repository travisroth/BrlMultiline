# BrlMultiline: not speaking the headers of one table.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Silencing the spoken headers of the table the reader is in, and only that table.

Some tables on the web declare good headers, and some put a paragraph of help text in every
one of them. NVDA's "report table headers" is one setting for every table there is: it can be
set per profile, but a profile per website — or per table on one website — is not a way to
live. Braille already has its own answer, a layout that cuts a header short. Speech had none,
and speech and braille are read together.

**A command, for the table at the cursor, until the page is loaded again.** It does not touch
NVDA's configuration. The obvious way to do this is to switch `reportTableHeaders` off while
the reader is in the table and on again when they leave, and it has two faults. The value is
written into whichever profile is active, and saved with it if NVDA saves on the way — on exit,
by default. And the add-on's own braille reads that setting too: the pinned header row and the
repeated key column follow it (see `bmConfig.wantsColumnHeaders`), so switching it off for
speech would take the headers off the display as well, which is the opposite of what a reader
who wants braille and speech to differ asked for.

**So it is done in speech, at the one place both routes to a header meet.** A browse mode
cell's headers reach speech through `getControlFieldSpeech`, and a focused cell's — a list
view, a grid in focus mode, a worksheet — through `getObjectPropertiesSpeech`. Both end in
`speech.speech.getPropertiesSpeech`, with the header text as `rowHeaderText` and
`columnHeaderText` and the table named by `_tableID`. That call is wrapped, and for a silenced
table the two headers are left out. Everything else about the cell — its coordinates, its
content, its states — is spoken as NVDA would speak it.

**Nothing here needs a flow, or a multi line display.** It is how a table is heard, not how one
is drawn, so it is installed when the add-on loads, like `tableArrows`, and works the same on a
single line display with no band running.

**It can only take headers away.** Where NVDA has been told to report none, it never reads them,
so there is nothing here to put back; the command says so rather than claiming to turn on what
it cannot.

**Which table, and for how long.** A browse mode table is named by NVDA's own table ID, which is
unique only within its document, so it is kept with a weak reference to that document: loading
the page again makes a new document, and the old one's silenced tables go with it. A focused
cell's table ID names its window as well — `(windowHandle, uniqueID)` for IAccessible, a runtime
ID for UI Automation — so it is kept on its own, until NVDA restarts.
"""

import dataclasses
import functools
import weakref
from typing import Any, Optional

import api
from logHandler import log

from . import bmConfig

HEADER_PROPERTIES = ("rowHeaderText", "columnHeaderText")
"""What `getPropertiesSpeech` is given a table cell's headers as."""

MAX_SILENCED = 64
"""How many tables may be silenced at once before the one silenced longest ago speaks again.

Entries for a page go when the page does. An entry for a focused table stays until NVDA
restarts, and this is what keeps a long session from collecting them without end."""


@dataclasses.dataclass(frozen=True)
class Silenced:
	"""One table whose headers are not spoken."""

	tableID: Any
	"""The table, as `_key` made it hashable."""

	document: Optional[weakref.ref] = None
	"""The browse mode document the table ID belongs to, or None for a focused table's ID,
	which says its window and needs no document to be unique."""

	@property
	def isGone(self) -> bool:
		""":return: whether the document this belonged to has been closed or loaded again."""
		return self.document is not None and self.document() is None

	def names(self, tableID: Any, document: Any) -> bool:
		""":return: whether this is the table speech is about to describe.

		:param tableID: the table, as `_key` made it hashable.
		:param document: the browse mode document the focus is in, or None.
		"""
		if tableID != self.tableID:
			return False
		if self.document is None:
			return True
		return document is not None and self.document() is document


_silenced: list[Silenced] = []
"""Every table silenced now, oldest first."""

_original = None
"""NVDA's own `getPropertiesSpeech`, while this module's is installed in its place."""

_wrapper = None
"""What was installed, so that `remove` can tell whether it is still there."""


def _key(tableID: Any) -> Any:
	""":return: a table ID in a form that can be compared and kept, or None for no ID.

	UI Automation's runtime ID arrives as a list or an array; IAccessible's is a tuple and a
	browse mode document's a number. What cannot be made hashable is compared by its text.
	"""
	if tableID is None:
		return None
	if isinstance(tableID, (list, tuple)):
		return tuple(_key(part) for part in tableID)
	try:
		hash(tableID)
	except TypeError:
		return repr(tableID)
	return tableID


def _focusDocument(focus: Any) -> Any:
	""":return: the browse mode document the focus is in, or None."""
	return getattr(focus, "treeInterceptor", None)


def tableHere() -> Optional[Silenced]:
	""":return: the table at the reader's cursor, said as an entry could keep it, or None.

	In browse mode, the cell at the browse mode cursor. Anywhere else, including focus mode in a
	web page, the table of the focused object.
	"""
	focus = api.getFocusObject()
	document = _focusDocument(focus)
	if document is not None and not getattr(document, "passThrough", False):
		finder = getattr(document, "_getTableCellCoords", None)
		if finder is None:
			return None
		try:
			cell = finder(document.selection)
		except LookupError:
			return None
		except Exception:
			log.debugWarning(
				"BrlMultiline: could not find the table at the browse mode cursor", exc_info=True
			)
			return None
		tableID = _key(getattr(cell, "tableID", None))
		if tableID is None:
			return None
		return Silenced(tableID, weakref.ref(document))
	try:
		tableID = _key(focus.tableID)
	except NotImplementedError:
		return None
	except Exception:
		log.debugWarning("BrlMultiline: could not find the table of the focus", exc_info=True)
		return None
	if tableID is None:
		return None
	return Silenced(tableID)


def _prune() -> None:
	"""Forget the tables of documents that have gone."""
	_silenced[:] = [entry for entry in _silenced if not entry.isGone]


def _find(here: Silenced) -> Optional[Silenced]:
	""":return: the entry that silences this table, or None."""
	document = here.document() if here.document is not None else None
	for entry in _silenced:
		if entry.names(here.tableID, document):
			return entry
	return None


def isSilenced(tableID: Any) -> bool:
	""":return: whether the headers of this table are not to be spoken.

	Called from speech, so it asks for the focus only when something is silenced.

	:param tableID: the table as speech names it, `_tableID`.
	"""
	if not _silenced:
		return False
	tableID = _key(tableID)
	if tableID is None:
		return False
	document = _focusDocument(api.getFocusObject())
	return any(entry.names(tableID, document) for entry in _silenced if not entry.isGone)


def silence(here: Silenced) -> None:
	"""Stop speaking this table's headers."""
	_prune()
	if _find(here) is not None:
		return
	_silenced.append(here)
	del _silenced[:-MAX_SILENCED]


def unsilence(here: Silenced) -> None:
	"""Speak this table's headers again, as NVDA's settings say to."""
	entry = _find(here)
	if entry is not None:
		_silenced.remove(entry)


def clear() -> None:
	"""Speak every table's headers again."""
	_silenced.clear()


def toggle() -> str:
	"""Silence the headers of the table at the cursor, or speak them again.

	:return: what to tell the reader.
	"""
	here = tableHere()
	if here is None:
		# Translators: reported when the command to silence a table's headers is used outside a table.
		return _("Not in a table")
	_prune()
	if _find(here) is not None:
		unsilence(here)
		# Translators: reported when the headers of the table at the cursor are spoken again.
		return _("Table headers spoken")
	if not (bmConfig.wantsRowHeaders() or bmConfig.wantsColumnHeaders()):
		# Translators: reported when the command to silence a table's headers is used and NVDA's
		# document formatting settings already report no table headers.
		return _("NVDA is set to report no table headers")
	silence(here)
	# Translators: reported when the headers of the table at the cursor will no longer be spoken.
	return _("Table headers not spoken")


def _withoutSilencedHeaders(original):
	""":return: `getPropertiesSpeech`, leaving out the headers of a silenced table."""

	@functools.wraps(original)
	def getPropertiesSpeech(*args, **propertyValues):
		if _silenced and any(name in propertyValues for name in HEADER_PROPERTIES):
			try:
				silenced = isSilenced(propertyValues.get("_tableID"))
			except Exception:
				# Speech has no boundary above this to hand a failure to, and a header spoken
				# that should not have been costs far less than a cell not spoken at all.
				log.debugWarning(
					"BrlMultiline: could not tell whether a table's headers are silenced", exc_info=True
				)
				silenced = False
			if silenced:
				propertyValues = {
					name: value for name, value in propertyValues.items() if name not in HEADER_PROPERTIES
				}
		return original(*args, **propertyValues)

	return getPropertiesSpeech


def install() -> None:
	"""Start leaving out the headers of silenced tables. Nothing is silenced until the reader asks."""
	global _original, _wrapper
	if _wrapper is not None:
		return
	try:
		from speech import speech as speechModule

		original = speechModule.getPropertiesSpeech
		wrapper = _withoutSilencedHeaders(original)
	except Exception:
		log.error(
			"BrlMultiline: could not reach NVDA's table header speech; every table's headers will be spoken",
			exc_info=True,
		)
		return
	_original = original
	_wrapper = wrapper
	speechModule.getPropertiesSpeech = wrapper


def remove() -> None:
	"""Stop, putting NVDA's own `getPropertiesSpeech` back. Safe to call when nothing is installed.

	Put back only if it is still the one this module installed. See `tableArrows.remove`, which
	says why.
	"""
	global _original, _wrapper
	if _wrapper is None:
		return
	try:
		from speech import speech as speechModule

		if speechModule.getPropertiesSpeech is _wrapper:
			speechModule.getPropertiesSpeech = _original
		else:
			log.debugWarning(
				"BrlMultiline: getPropertiesSpeech has been replaced since it was wrapped; "
				"leaving it as it is rather than undoing whatever replaced it",
			)
	except Exception:
		log.error("BrlMultiline: could not give NVDA its own table header speech back", exc_info=True)
	finally:
		_original = None
		_wrapper = None
		clear()
