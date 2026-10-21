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

	readUnit: str | None = None
	"""How much of what is found is spoken, as NVDA's own quick navigation has it: a line of a
	table, a list or a landmark rather than the whole of it. None speaks the item."""


TYPES = (
	# Translators: a kind of thing in a document, in "Pinned document: next heading".
	QuickNavType("heading", "Heading", "no next heading", "no previous heading", _("heading")),
	# Translators: a kind of thing in a document, in "Pinned document: next link".
	QuickNavType("link", "Link", "no next link", "no previous link", _("link")),
	# Translators: a kind of thing in a document, in "Pinned document: next table".
	QuickNavType("table", "Table", "no next table", "no previous table", _("table"), textInfos.UNIT_LINE),
	# Translators: a kind of thing in a document, in "Pinned document: next list".
	QuickNavType("list", "List", "no next list", "no previous list", _("list"), textInfos.UNIT_LINE),
	# Translators: a kind of thing in a document, in "Pinned document: next list item".
	QuickNavType("listItem", "ListItem", "no next list item", "no previous list item", _("list item")),
	# Translators: a kind of thing in a document, in "Pinned document: next graphic".
	QuickNavType("graphic", "Graphic", "no next graphic", "no previous graphic", _("graphic")),
	# Translators: a kind of thing in a document, in "Pinned document: next form field".
	QuickNavType("formField", "FormField", "no next form field", "no previous form field", _("form field")),
	# Translators: a kind of thing in a document, in "Pinned document: next landmark".
	QuickNavType(
		"landmark", "Landmark", "no next landmark", "no previous landmark", _("landmark"), textInfos.UNIT_LINE
	),
	# Translators: a kind of thing in a document, in "Pinned document: next block quote".
	QuickNavType(
		"blockQuote", "BlockQuote", "no next block quote", "no previous block quote", _("block quote")
	),
)
"""The types a pin offers, with NVDA's own words for finding none. The English is NVDA's, so
NVDA's translation of it is found; see L{nvdaMessage}."""

NOT_SUPPORTED = "Not supported in this document"
"""NVDA's message for a quick navigation type a document cannot search, in English."""

SAFE_TO_BUILD = frozenset({"UIABrowseModeDocument", "WordDocumentTreeInterceptor"})
"""The browse mode documents a pin may build over an object only to search it, by class name.

Their construction keeps what it learns to itself: UI Automation's, which Word through UI Automation
is, and Word's through its object model. A virtual buffer is not among them. See L{searcherFor}."""


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

	**NVDA's own first.** A browse mode document NVDA already made for the object, and has ready, is
	the one searched, so nothing new is made while one exists.

	**Otherwise built and never registered, and only of a kind that is safe to build.** Made only to
	search, it is not put in NVDA's running table, so NVDA routes no events or keys through it and
	makes its own when the reader moves into the document, exactly as it would have. Not every kind
	can be built that way: a virtual buffer, a web page's, records itself in NVDA's lookup of
	documents as it is constructed, and a second one would take the live one's place there (found in
	review). So only the kinds in L{SAFE_TO_BUILD}, whose construction touches nothing of NVDA's,
	are built; any other says it cannot be searched.

	Its positions wrap the object's own, so a position read from the object goes in and one the
	object can read comes out; see L{_asSearched} and L{_asRead}.
	"""
	if hasattr(document, "_iterNodesByType"):
		return document
	try:
		existing = getattr(document, "treeInterceptor", None)
		if existing is not None and existing.isReady and hasattr(existing, "_iterNodesByType"):
			return existing
	except Exception:
		log.debugWarning("BrlMultiline: could not consult a pin's browse mode document", exc_info=True)
	cls = _browseModeClass(document)
	if cls is None or not any(base.__name__ in SAFE_TO_BUILD for base in cls.__mro__):
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


FOUND_AT_ATTRIBUTE = "_brlMultilineQuickNavFoundAt"
"""Set on a pin's monitor: where quick navigation last put it, and where its window was then."""


def positionOf(monitor, document):
	"""Where a search through a pin starts.

	**Where the last search put it, while the reader has not moved it since.** A found item is put
	at the top by the block it is in, so the top of the window is the start of that block; two links
	in one line would otherwise find the first again from there, for ever (found in review). Panning
	moves the window, and then the search starts where the reader is.

	Otherwise the top of its window, or a pin read as regions its own position.
	"""
	control = getattr(monitor, "controller", None)
	if control is not None:
		found = getattr(monitor, FOUND_AT_ATTRIBUTE, None)
		windowMark = getattr(control, "windowMark", None)
		if found is not None and windowMark is not None and found[0] == windowMark():
			return found[1].copy()
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
		# A copy, kept before speaking, since reporting may move the range it is given.
		target = _asRead(searcher, document, info).copy()
		target.collapse()
	except Exception:
		log.debugWarning("BrlMultiline: could not read where a quick navigation item is", exc_info=True)
		return nvdaMessage(NOT_SUPPORTED)
	try:
		if quickNavType.readUnit is None:
			item.report()
		else:
			item.report(readUnit=quickNavType.readUnit)
	except Exception:
		log.debugWarning("BrlMultiline: could not report a quick navigation item", exc_info=True)
	if not placeAt(monitor, target):
		# Translators: said when a quick navigation key found something in a pinned document and the
		# pin could not be moved to show it.
		return _("Cannot show that in this pin")
	return None


def placeAt(monitor, info) -> bool:
	"""Put a pin's reading position at a place in its document, at the top of its segment.

	:param info: a collapsed position in the pin's document.
	:return: whether the pin moved.
	"""
	control = getattr(monitor, "controller", None)
	try:
		if control is not None:
			if not control.enterAtPosition(info.copy()):
				return False
			windowMark = getattr(control, "windowMark", None)
			if windowMark is not None:
				setattr(monitor, FOUND_AT_ATTRIBUTE, (windowMark(), info.copy()))
			monitor.refresh()
			return True
		region = _pinnedRegion(monitor)
		if region is None:
			return False
		region._setCursor(info.copy())
		monitor.refresh(reveal=True)
		return True
	except Exception:
		log.debugWarning("BrlMultiline: could not move a pin to what quick navigation found", exc_info=True)
		return False
