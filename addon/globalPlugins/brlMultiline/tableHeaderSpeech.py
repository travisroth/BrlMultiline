# BrlMultiline: not speaking the headers of one table.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Silencing the spoken headers of the table the reader is in, and only that table.

NVDA's "report table headers" is one setting for every table, which suits neither a table
with help text in every header nor a profile per site.

1. A saved layout can say which headers are spoken (see
   `flowTableLayouts.TableLayout.speakHeaders`); over that, a command silences the table at the
   cursor until the page reloads, and another saves what it left.
2. NVDA's configuration is not touched, since the change would be saved into a profile and
   would also take the headers off braille (see `bmConfig.wantsColumnHeaders`).
3. Done in `speech.speech.getPropertiesSpeech`, where browse mode and focused cells meet, by
   leaving out `rowHeaderText` and `columnHeaderText` for a silenced `_tableID`.
4. Installed when the add-on loads, like `tableArrows`; no flow is needed.
5. It can only take headers away; where NVDA reports none, the command says so.

A browse mode table ID is unique only within its document, so it is kept with a weak reference
to the document; a focused cell's ID names its window, and is kept until NVDA restarts. A
saved layout is looked up once per table per page, from the table at the cursor, so say all
reading ahead of the cursor speaks the headers.
"""

import dataclasses
import functools
import weakref
from typing import Any

import api
from logHandler import log

from . import bmConfig, flowTableLayouts, flowTableSource
from .flowTableLayouts import FOLLOW, SPEAK_COLUMNS, SPEAK_OFF, SPEAK_ROWS

import addonHandler

addonHandler.initTranslation()

HEADER_PROPERTIES = ("rowHeaderText", "columnHeaderText")
"""What `getPropertiesSpeech` is given a table cell's headers as."""

DROPS = {
	SPEAK_OFF: HEADER_PROPERTIES,
	SPEAK_ROWS: ("columnHeaderText",),
	SPEAK_COLUMNS: ("rowHeaderText",),
}
"""Which of the headers each way of speaking them leaves out. `FOLLOW` leaves out nothing."""

MAX_OVERRIDES = 64
"""How many tables the command may have changed at once before the one changed longest ago goes
back to its saved setting.

Entries for a page go when the page does. An entry for a focused table stays until NVDA
restarts, and this is what keeps a long session from collecting them without end."""

MAX_REMEMBERED = 256
"""How many focused tables' saved settings are kept before they are all looked up again."""


@dataclasses.dataclass(frozen=True)
class Here:
	"""One table, named the way speech names it."""

	tableID: Any
	"""The table, as `_key` made it hashable."""

	document: weakref.ref | None = None
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


@dataclasses.dataclass(frozen=True)
class Override:
	"""What the command said about one table, over whatever its saved layout says."""

	here: Here
	speakHeaders: str
	"""`SPEAK_OFF`, or `FOLLOW` for a table whose saved layout silences it and the reader wants to hear."""


_overrides: list[Override] = []
"""Every table the command has changed, oldest first."""

_savedInDocument: "weakref.WeakKeyDictionary[Any, dict]" = weakref.WeakKeyDictionary()
"""What the saved layouts say of each browse mode table, by document, then table ID.

Each answer is kept with the stored text it was read from, and is an answer only while the store
still says that."""

_savedForObject: dict = {}
"""The same for focused tables, by table ID."""

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


def _browseDocument(focus: Any) -> Any:
	""":return: the browse mode document the focus is in while it is reading in browse mode, or None."""
	document = _focusDocument(focus)
	if document is None or getattr(document, "passThrough", False):
		return None
	return document


def tableHere() -> Here | None:
	""":return: the table at the reader's cursor, named as speech names it, or None.

	In browse mode, the cell at the browse mode cursor. Anywhere else, including focus mode in a
	web page, the table of the focused object.
	"""
	focus = api.getFocusObject()
	document = _browseDocument(focus)
	if document is not None:
		finder = getattr(document, "_getTableCellCoords", None)
		if finder is None:
			return None
		try:
			cell = finder(document.selection)
		except LookupError:
			return None
		except Exception:
			log.debugWarning(
				"BrlMultiline: could not find the table at the browse mode cursor",
				exc_info=True,
			)
			return None
		tableID = _key(getattr(cell, "tableID", None))
		if tableID is None:
			return None
		return Here(tableID, weakref.ref(document))
	try:
		tableID = _key(focus.tableID)
	except NotImplementedError:
		return None
	except Exception:
		log.debugWarning("BrlMultiline: could not find the table of the focus", exc_info=True)
		return None
	if tableID is None:
		return None
	return Here(tableID)


def _storeText() -> str:
	try:
		return bmConfig.tableLayouts()
	except Exception:
		log.debugWarning("BrlMultiline: could not read the saved table layouts", exc_info=True)
		return ""


def savedFor(tableID: Any) -> str:
	""":return: what the saved layouts say of this table's headers: one of `SPEAK_HEADERS`.

	Only where the table at the cursor is this table, for the reason the module gives.

	:param tableID: the table, as `_key` made it hashable.
	"""
	if tableID is None or not flowTableLayouts.decidesHeaderSpeech():
		return FOLLOW
	text = _storeText()
	focus = api.getFocusObject()
	document = _browseDocument(focus)
	if document is not None:
		remembered = _savedInDocument.setdefault(document, {})
	else:
		remembered = _savedForObject
	said = remembered.get(tableID)
	if said is not None and said[0] == text:
		return said[1]
	handle = flowTableSource.tableAt(focus)
	if handle is None:
		return FOLLOW
	if document is not None and _key(handle.tableID) != tableID:
		# Speech is describing a table the cursor is not in. Not kept, so the table is looked up
		# again when the cursor is in it.
		return FOLLOW
	layout = flowTableLayouts.layoutFor(handle, follow=False)
	speakHeaders = layout.speakHeaders if layout is not None else FOLLOW
	if document is None and len(_savedForObject) >= MAX_REMEMBERED:
		_savedForObject.clear()
	remembered[tableID] = (text, speakHeaders)
	return speakHeaders


def _prune() -> None:
	"""Forget what the command said of tables in documents that have gone."""
	_overrides[:] = [override for override in _overrides if not override.here.isGone]


def _overrideOf(here: Here) -> Override | None:
	""":return: what the command said of this table, or None."""
	document = here.document() if here.document is not None else None
	for override in _overrides:
		if override.here.names(here.tableID, document):
			return override
	return None


def _setOverride(here: Here, speakHeaders: str | None) -> None:
	"""Say how this table's headers are spoken until the page is loaded again, or None to go back to its saved setting."""
	_prune()
	override = _overrideOf(here)
	if override is not None:
		_overrides.remove(override)
	if speakHeaders is not None:
		_overrides.append(Override(here, speakHeaders))
		del _overrides[:-MAX_OVERRIDES]


def speakHeadersFor(tableID: Any) -> str:
	""":return: how the headers of the table speech is describing are spoken: one of `SPEAK_HEADERS`.

	Called from speech, on every table cell it describes, so it answers without asking anything
	where the command has changed nothing and no saved layout says anything about headers.

	:param tableID: the table as speech names it, `_tableID`.
	"""
	tableID = _key(tableID)
	if tableID is None:
		return FOLLOW
	if _overrides:
		document = _focusDocument(api.getFocusObject())
		for override in _overrides:
			if not override.here.isGone and override.here.names(tableID, document):
				return override.speakHeaders
	return savedFor(tableID)


def isSilenced(tableID: Any) -> bool:
	""":return: whether none of this table's headers are to be spoken."""
	return speakHeadersFor(tableID) == SPEAK_OFF


def _effective(here: Here) -> tuple:
	""":return: how this table's headers are spoken now, and what its saved layout says."""
	saved = savedFor(here.tableID)
	override = _overrideOf(here)
	return (override.speakHeaders if override is not None else saved, saved)


def clear() -> None:
	"""Put every table the command changed back to its saved setting, and forget what was looked up."""
	_overrides.clear()
	_savedInDocument.clear()
	_savedForObject.clear()


def toggle() -> str:
	"""Silence the headers of the table at the cursor, or speak them again, until the page is loaded again.

	A table whose saved layout speaks only some of its headers counts as silenced, so the first press
	there speaks all of them.

	:return: what to tell the reader.
	"""
	here = tableHere()
	if here is None:
		# Translators: reported when the command to silence a table's headers is used outside a table.
		return _("Not in a table")
	_prune()
	current, saved = _effective(here)
	if current != FOLLOW:
		_setOverride(here, None if saved == FOLLOW else FOLLOW)
		# Translators: reported when the headers of the table at the cursor are spoken again.
		return _("Table headers spoken")
	if not (bmConfig.wantsRowHeaders() or bmConfig.wantsColumnHeaders()):
		# Translators: reported when the command to silence a table's headers is used and NVDA's
		# document formatting settings already report no table headers.
		return _("NVDA is set to report no table headers")
	_setOverride(here, None if saved == SPEAK_OFF else SPEAK_OFF)
	# Translators: reported when the headers of the table at the cursor will no longer be spoken.
	return _("Table headers not spoken")


def save() -> str:
	"""Save how the headers of the table at the cursor are spoken now, into its saved layout.

	What the toggle left is what is saved, so silencing a table and saving it is two presses.

	:return: what to tell the reader.
	"""
	here = tableHere()
	try:
		handle = flowTableSource.tableAt(api.getFocusObject()) if here is not None else None
	except Exception:
		log.debugWarning("BrlMultiline: could not look for the table the reader is in", exc_info=True)
		handle = None
	if here is None or handle is None:
		# Translators: reported when a command needs the cursor to be in a table.
		return _("Not in a table")
	current, _saved = _effective(here)
	try:
		saved = flowTableLayouts.rememberHeaderSpeech(handle, current)
	except flowTableLayouts.LayoutsNotSaved:
		# Translators: reported when saving table layouts to the configuration failed.
		return _("The table layout could not be saved, see the log")
	if not saved:
		# Translators: reported when a table's layout cannot be saved because nothing
		# about the table or its window is stable enough to recognise it again.
		return _("This table cannot be recognised again, so its layout cannot be saved")
	_setOverride(here, None)
	return savedWords(current)


def savedWords(speakHeaders: str) -> str:
	""":return: what the reader is told when a table's header speech is saved."""
	if speakHeaders == SPEAK_OFF:
		# Translators: reported when a table is saved to have none of its headers spoken.
		return _("This table's headers will not be spoken")
	if speakHeaders == SPEAK_ROWS:
		# Translators: reported when a table is saved to have only its row headers spoken.
		return _("Only this table's row headers will be spoken")
	if speakHeaders == SPEAK_COLUMNS:
		# Translators: reported when a table is saved to have only its column headers spoken.
		return _("Only this table's column headers will be spoken")
	# Translators: reported when a table is saved to have its headers spoken as NVDA's settings say.
	return _("This table's headers will be spoken as NVDA's settings say")


def _withoutSilencedHeaders(original):
	""":return: `getPropertiesSpeech`, leaving out the headers of a silenced table."""

	@functools.wraps(original)
	def getPropertiesSpeech(*args, **propertyValues):
		if any(name in propertyValues for name in HEADER_PROPERTIES):
			try:
				drop = DROPS.get(speakHeadersFor(propertyValues.get("_tableID")), ())
			except Exception:
				# Speech has no boundary above this to hand a failure to, and a header spoken
				# that should not have been costs far less than a cell not spoken at all.
				# Including a cancelled call, which is the application not answering.
				log.debugWarning(
					"BrlMultiline: could not tell whether a table's headers are silenced",
					exc_info=True,
				)
				drop = ()
			if drop:
				propertyValues = {name: value for name, value in propertyValues.items() if name not in drop}
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
