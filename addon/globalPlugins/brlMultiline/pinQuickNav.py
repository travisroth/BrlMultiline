# BrlMultiline: browse mode's quick navigation keys in a pinned document.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Moving a pinned document by its structure, without moving anything the reader is working in.

Browse mode's quick navigation keys end in `BrowseModeTreeInterceptor._quickNavScript`, which
searches with `_iterNodesByType(itemType, direction, position)`, speaks what it found and moves the
caret there. The search takes any position. A pin searches from its own reading position, speaks
what it found as NVDA would, and moves only itself: the found item is put at the top of its segment
and the document runs on from it. The focus and the caret stay where the reader left them.

A pin read from a browse mode document is searched through it. A pin read from a document object
that has none yet, as the Outlook reading pane has until it gets the focus, is searched through a
browse mode document made over it for the purpose and never registered; see L{searcherFor}. A run
of objects has no positions, and says "Not supported in this document", as NVDA does for a type a
document cannot answer.

Which pin a key is for is the plugin's question; see `GlobalPlugin.pinForQuickNav`. See
`docs/design/pinned-quick-nav-plan.md`.
"""

import builtins
from typing import Any, NamedTuple

import addonHandler
import textInfos
from logHandler import log

# NVDA's own `_` is kept first, for NVDA's messages; see L{nvdaMessage}.
addonHandler.initTranslation()


class QuickNavType(NamedTuple):
	"""One of browse mode's quick navigation types, as a pin offers it."""

	itemType: str
	"""The type `_iterNodesByType` is asked for."""

	name: str
	"""Its part of the command names, `pinNextHeading` and `pinPreviousHeading`."""

	nextError: str
	"""NVDA's own message when there is no next one, in English, translated by NVDA."""

	prevError: str

	label: str
	"""What the commands call it, in this add-on's translation."""


TYPES = (
	# Translators: a kind of thing in a document, in "Pinned document: next heading".
	QuickNavType("heading", "Heading", "no next heading", "no previous heading", _("heading")),
	# Translators: a kind of thing in a document, in "Pinned document: next link".
	QuickNavType("link", "Link", "no next link", "no previous link", _("link")),
	# Translators: a kind of thing in a document, in "Pinned document: next table".
	QuickNavType("table", "Table", "no next table", "no previous table", _("table")),
	# Translators: a kind of thing in a document, in "Pinned document: next list".
	QuickNavType("list", "List", "no next list", "no previous list", _("list")),
	# Translators: a kind of thing in a document, in "Pinned document: next list item".
	QuickNavType("listItem", "ListItem", "no next list item", "no previous list item", _("list item")),
	# Translators: a kind of thing in a document, in "Pinned document: next graphic".
	QuickNavType("graphic", "Graphic", "no next graphic", "no previous graphic", _("graphic")),
	# Translators: a kind of thing in a document, in "Pinned document: next form field".
	QuickNavType("formField", "FormField", "no next form field", "no previous form field", _("form field")),
	# Translators: a kind of thing in a document, in "Pinned document: next landmark".
	QuickNavType("landmark", "Landmark", "no next landmark", "no previous landmark", _("landmark")),
	# Translators: a kind of thing in a document, in "Pinned document: next block quote".
	QuickNavType(
		"blockQuote", "BlockQuote", "no next block quote", "no previous block quote", _("block quote")
	),
)
"""The types a pin offers, with NVDA's own words for finding none. The English is NVDA's, so
NVDA's translation of it is found; see L{nvdaMessage}."""

NOT_SUPPORTED = "Not supported in this document"
"""NVDA's message for a quick navigation type a document cannot search, in English."""


def nvdaMessage(english: str) -> str:
	""":return: NVDA's own translation of one of its messages.

	This add-on's modules have `_` pointed at the add-on's translations, which do not have NVDA's
	words. NVDA's own `_` is still the one in builtins.
	"""
	translate = getattr(builtins, "_", None)
	if translate is None:
		return english
	try:
		return translate(english)
	except Exception:
		return english


def _readFrom(monitor) -> Any:
	""":return: what a pin is read from: its flow's document, or the object its regions read."""
	control = getattr(monitor, "controller", None)
	if control is not None:
		return getattr(getattr(control, "source", None), "obj", None)
	return getattr(monitor, "obj", None)


def _browseModeClass(document) -> Any:
	""":return: the browse mode document class an object would have, or None.

	What NVDA+Space would make of it. Only document objects have one; asking anything else raises
	`NotImplementedError`, which is how NVDA says there is none.
	"""
	try:
		cls = document.treeInterceptorClass
	except Exception:
		return None
	return cls if hasattr(cls, "_iterNodesByType") else None


def documentOf(monitor) -> Any:
	""":return: the document a pin is read from, if it can be searched by structure, else None.

	A browse mode document can. So can a document object NVDA would give one: found on hardware,
	the Outlook reading pane shown beside the message list is read from its Word document object,
	since NVDA makes the browse mode document only when the pane gets the focus.
	"""
	document = _readFrom(monitor)
	if document is None:
		return None
	if hasattr(document, "_iterNodesByType") or _browseModeClass(document) is not None:
		return document
	return None


def searcherFor(document) -> Any:
	""":return: what searches a document by structure: the document, or a browse mode document over it.

	**Built and never registered.** Made only to search, it is not put in NVDA's running table, so
	NVDA does not route events or keys through it and makes its own when the reader moves into the
	document, exactly as it would have. Its positions wrap the object's own, so a position read from
	the object goes in and one the object can read comes out; see L{_asSearched} and L{_asRead}.
	"""
	if hasattr(document, "_iterNodesByType"):
		return document
	cls = _browseModeClass(document)
	if cls is None:
		return None
	try:
		return cls(document)
	except Exception:
		log.debugWarning("BrlMultiline: could not make a browse mode document to search a pin", exc_info=True)
		return None


def _asSearched(searcher, document, position):
	""":return: a position read from the document, as the searcher takes one."""
	if searcher is document or position is None:
		return position
	return searcher.makeTextInfo(position)


def _asRead(searcher, document, info):
	""":return: a position the searcher found, as the document reads one."""
	if searcher is document:
		return info
	return getattr(info, "innerTextInfo", info)


def _pinnedRegion(monitor):
	""":return: the region holding a pin's own reading position, when it is read as regions."""
	for region in getattr(monitor, "regions", None) or ():
		if hasattr(region, "_setCursor") and hasattr(region, "_getSelection"):
			return region
	return None


def positionOf(monitor, document):
	""":return: where a search through a pin starts: the top of its window, or its own position."""
	control = getattr(monitor, "controller", None)
	if control is not None:
		position = control.topPosition()
		if position is not None:
			return position
	region = _pinnedRegion(monitor)
	if region is not None:
		try:
			return region._getSelection()
		except Exception:
			log.debugWarning("BrlMultiline: could not read a pin's position", exc_info=True)
	try:
		return document.makeTextInfo(textInfos.POSITION_FIRST)
	except Exception:
		return None


def move(monitor, quickNavType: QuickNavType, forward: bool) -> str | None:
	"""Move a pin to the next or previous item of a type, speaking it as NVDA would.

	:return: None when it moved, otherwise what to tell the reader.
	"""
	document = documentOf(monitor)
	searcher = searcherFor(document) if document is not None else None
	if searcher is None:
		return nvdaMessage(NOT_SUPPORTED)
	direction = "next" if forward else "previous"
	try:
		position = _asSearched(searcher, document, positionOf(monitor, document))
		item = next(searcher._iterNodesByType(quickNavType.itemType, direction, position))
	except NotImplementedError:
		return nvdaMessage(NOT_SUPPORTED)
	except StopIteration:
		return nvdaMessage(quickNavType.nextError if forward else quickNavType.prevError)
	except Exception:
		log.debugWarning(f"BrlMultiline: could not search a pin for {quickNavType.itemType}", exc_info=True)
		return nvdaMessage(NOT_SUPPORTED)
	info = getattr(item, "textInfo", None)
	if info is None:
		return nvdaMessage(NOT_SUPPORTED)
	try:
		item.report()
	except Exception:
		log.debugWarning("BrlMultiline: could not report a quick navigation item", exc_info=True)
	placeAt(monitor, _asRead(searcher, document, info))
	return None


def placeAt(monitor, info) -> None:
	"""Put a pin's reading position at a place in its document, at the top of its segment."""
	info = info.copy()
	info.collapse()
	control = getattr(monitor, "controller", None)
	if control is not None:
		if control.enterAtPosition(info):
			monitor.refresh()
		return
	region = _pinnedRegion(monitor)
	if region is not None:
		region._setCursor(info)
		monitor.refresh(reveal=True)
