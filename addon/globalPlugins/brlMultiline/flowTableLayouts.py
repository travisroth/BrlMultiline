# BrlMultiline: remembering how a reader wants one table laid out.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A layout the reader saved, kept against the table it was made for.

The command that lays a table out in columns says of itself that the end state is "a layout
remembered against the table so that a watchlist comes up laid out". This is that: a record
per table, looked up when the reader arrives, applied without a keystroke.

**What is saved is the reader's choices, not the arithmetic.** A column plan holds widths in
cells, and cells belong to the display it was made on — a plan saved on a Monarch's
thirty-two would be nonsense on a Focus 80's eighty, and this reader carries both. So the
record holds what the reader *decided*: which columns to show and in what order, how tall a
row may be, whether a long cell is cut, whether the key column is repeated, whether a header
row is pinned. The widths are worked out again by `flowTable.planFor` for whatever band the
table lands on, which is the same code that works them out today.

**Every field can say "not my business."** A record made by turning columns on and saving
says nothing about row height, and a reader who later changes the row height setting expects
that change to reach this table too. So an unset field means "whatever the settings say", and
only what the reader chose *for this table* is written down. That is also what keeps a record
from silently freezing a default the add-on later improves.

**A saved layout is something the reader can find again, name and move.** The first store kept
each table only as a digest of its address and its headings, so that the configuration file
carried no URLs. It worked until a site changed its address: the watchlist stopped coming up
laid out, nothing could say which saved layout had been its own, and nothing could point that
layout at the new address. Found on hardware, 16 September 2026. So each layout is now a
`SavedLayout` that keeps, readably, the reader's name for it, the address it was saved at, how
strictly that address has to match, and the table's headings — which is what the layout
manager, `flowTableManager`, lists and edits. The reader chose to keep the full address,
query string and all, knowing the file then carries it.

**How strictly an address matches is the reader's to set, per layout.** A web page is matched
by its site and path by default, ignoring the query string, because the query string is what a
site changes: a view renumbered, a parameter added. The headings still have to match, which is
what keeps two tables on one path apart. Exactly, or anywhere on the site, are the other two
answers.

**Column settings follow their column by heading.** A record names columns by number, and a
site that inserts one moves every column after it — so each decision would land silently on
its neighbour. The heading of each column the record names is saved beside it, and a column
whose heading has moved is followed to where it went.

**One store, in the base configuration, whatever profile is active.** Written through NVDA's
ordinary configuration it went into whichever profile was active, and a layout saved while an
application's profile was on was hidden everywhere else. See `bmConfig.tableLayouts`.

Stored as one JSON string, because the keys would be URLs and window classes and a
configuration key is not allowed to be either.
"""

import dataclasses
import datetime
import hashlib
import json

import urllib.parse
import uuid
from typing import Any, Optional

from logHandler import log

from . import bmConfig, flowTableIdentity

FOLLOW = ""
"""What a three-state field says when it defers to the reader's ordinary settings."""

YES = "yes"
NO = "no"

STORE_VERSION = 2
"""The stored form. Version 1 had no version: a mapping of digests. See `_fromStoredText`."""

MATCH_EXACT = "exact"
"""The address has to be the one saved, character for character."""

MATCH_PATH = "path"
"""The same site and path, whatever the query string and fragment say. The default for a web page."""

MATCH_SITE = "site"
"""Any page on the same site. The headings still have to match."""

MATCHES = (MATCH_PATH, MATCH_EXACT, MATCH_SITE)

KEY_LENGTH = 16
"""How much of a digest named one table in the version 1 store. Kept to read that store."""

FOLLOW_COLUMNS = 64
"""How many of a table's columns are read to find a column that has moved, or a heading that has.

Only read when a saved column's heading is not where it was, which is a site having changed its
table rather than anything that happens on an ordinary redraw.
"""

USED_EVERY = 24 * 60 * 60
"""How often, in seconds, applying a layout writes down that it was used.

Once a day is enough to tell a layout still in use from one whose page has gone, which is what
the date is for, and it keeps the configuration from being written on every redraw.
"""


def keyFor(said: str) -> str:
	""":return: the digest the version 1 store named one half of an identity by.

	Only for reading that store, and for matching a layout carried over from it until it is next
	seen and can be written down readably. See `SavedLayout.legacyWhere`.

	:param said: a part of an identity, from `flowTableIdentity`.
	"""
	if not said:
		return ""
	return hashlib.sha256(said.encode("utf-8", "replace")).hexdigest()[:KEY_LENGTH]


MAX_SAVED = 200
"""How many tables may be remembered before the least recently used is dropped.

A bound on the configuration file rather than on the reader: two hundred tables is more than
anyone lays out by hand, and a store that grows without limit is a file that eventually
cannot be written. Ranked by when each was last used, which is written down at most once a day;
see `USED_EVERY`.
"""


@dataclasses.dataclass(frozen=True)
class TableLayout:
	"""What a reader decided about one table.

	Every field is optional in the sense that matters: `columns` empty means "all of them",
	and the three-state fields mean "what the settings say" when they hold `FOLLOW`.
	"""

	columns: tuple = ()
	"""The table's own column numbers, in the order to draw them. Empty means every column
	the table shows, which is what the layout does today."""

	rowHeight: int = 0
	"""How many band rows one table row may use, or 0 to follow the setting."""

	truncate: str = FOLLOW
	"""Whether a cell too long for its column is cut. `YES`, `NO`, or `FOLLOW`."""

	pinKey: str = FOLLOW
	"""Whether the first column is repeated on every page. `YES`, `NO`, or `FOLLOW`."""

	headers: str = FOLLOW
	"""Whether a header row is held above the band. `YES`, `NO`, or `FOLLOW`."""

	keyColumn: int = 0
	"""Which column is repeated at the left of every later page, or 0 for the first drawn.

	The first is the row's own label in most tables — the symbol, the criterion, the date — and
	an icon in some, which is why the reader can say."""

	perColumn: dict = dataclasses.field(default_factory=dict)
	"""What they decided about individual columns, by the table's own column number.

	By number rather than by position, so that a table which gains or loses a column does not
	shift everybody's settings onto their neighbours. See `flowTable.ColumnChoice` for what one
	holds; a column named here that the table has not got is dropped on reading, exactly as a
	named column already is."""

	columnHeadings: dict = dataclasses.field(default_factory=dict, compare=False)
	"""The heading each column this layout names had when it was saved, by column number.

	What lets a decision follow its column when a site inserts or moves one; see `_followColumns`.
	Not a decision, so it neither makes a layout worth saving nor makes two layouts that decide the
	same thing different. Filled in by `remember`, which is the one place that reads the table."""

	def asRecord(self) -> dict:
		""":return: this layout as the plain data that goes into the store.

		Only what was decided. A record of five defaults is five things to keep working
		forever, and it is indistinguishable, later, from a reader who meant them.
		"""
		record: dict = {}
		if self.columns:
			record["columns"] = list(self.columns)
		if self.keyColumn:
			record["keyColumn"] = int(self.keyColumn)
		chosen = {
			str(column): _choiceAsRecord(choice)
			for column, choice in (self.perColumn or {}).items()
			if not choice.isEmpty
		}
		if chosen:
			record["perColumn"] = chosen
		if self.rowHeight:
			record["rowHeight"] = int(self.rowHeight)
		for name in ("truncate", "pinKey", "headers"):
			said = getattr(self, name)
			if said in (YES, NO):
				record[name] = said
		if record and self.columnHeadings:
			record["headings"] = {
				str(column): heading for column, heading in sorted(self.columnHeadings.items()) if heading
			}
		return record

	@property
	def isEmpty(self) -> bool:
		""":return: whether this decides nothing, which is a layout not worth saving."""
		return not dataclasses.replace(self, columnHeadings={}).asRecord()

	@property
	def namedColumns(self) -> set:
		""":return: every column this layout decides something about, by the table's own number."""
		named = {column for column in self.columns if column > 0}
		named.update(column for column in (self.perColumn or {}) if column > 0)
		if self.keyColumn:
			named.add(self.keyColumn)
		return named

	def rowHeightOr(self, setting: int) -> int:
		""":return: the row height to use, this layout's or the reader's setting."""
		return self.rowHeight or setting

	def truncateOr(self, setting: bool) -> bool:
		return _decide(self.truncate, setting)

	def pinKeyOr(self, setting: bool) -> bool:
		return _decide(self.pinKey, setting)

	def headersOr(self, setting: bool) -> bool:
		return _decide(self.headers, setting)


def _decide(said: str, setting: bool) -> bool:
	""":return: a three-state field's answer, deferring to the setting where it has none."""
	if said == YES:
		return True
	if said == NO:
		return False
	return setting


def fromRecord(record: Any) -> TableLayout:
	""":return: a layout read back out of the store, defaulting anything it cannot read.

	Forgiving on purpose. This reads a file a reader may have edited and a record an older
	or newer version of the add-on wrote, and the honest failure for either is the layout
	losing a field rather than the table losing its layout.
	"""
	if not isinstance(record, dict):
		return TableLayout()
	columns = []
	# A list or a tuple or nothing. A scalar here is not a short list of columns, it is a
	# record somebody edited by hand or a version that wrote something else, and iterating it
	# raises where the reader is waiting for a table.
	said = record.get("columns")
	for number in said if isinstance(said, (list, tuple)) else ():
		try:
			number = int(number)
		except (TypeError, ValueError):
			continue
		if number > 0:
			columns.append(number)
	try:
		rowHeight = int(record.get("rowHeight") or 0)
	except (TypeError, ValueError):
		rowHeight = 0
	said = {}
	for name in ("truncate", "pinKey", "headers"):
		value = record.get(name)
		said[name] = value if value in (YES, NO) else FOLLOW
	try:
		keyColumn = int(record.get("keyColumn") or 0)
	except (TypeError, ValueError):
		keyColumn = 0
	headings = {}
	rawHeadings = record.get("headings")
	for column, heading in rawHeadings.items() if isinstance(rawHeadings, dict) else ():
		try:
			number = int(column)
		except (TypeError, ValueError):
			continue
		if number > 0 and isinstance(heading, str) and heading.strip():
			headings[number] = heading
	return TableLayout(
		columns=tuple(columns),
		rowHeight=max(0, rowHeight),
		keyColumn=max(0, keyColumn),
		perColumn=_perColumnFrom(record.get("perColumn")),
		columnHeadings=headings,
		**said,
	)


def _choiceAsRecord(choice) -> dict:
	""":return: one column's decisions as the plain data that goes into the store."""
	record: dict = {}
	for name in ("label", "overflow", "keep", "headerKeep"):
		said = getattr(choice, name, "")
		if said:
			record[name] = said
	for name in ("minWidth", "maxWidth"):
		said = getattr(choice, name, 0)
		if said:
			record[name] = int(said)
	for name in ("startsAPage", "plainCase"):
		if getattr(choice, name, False):
			record[name] = True
	return record


def _perColumnFrom(said: Any) -> dict:
	""":return: the per-column decisions read back, dropping anything unreadable.

	As forgiving as the rest of the store, and for the same reason: this file is edited by hand
	and written by other versions, and the honest failure is one column losing a setting rather
	than a reader losing their table.

	:param said: whatever the record held under "perColumn".
	"""
	if not isinstance(said, dict):
		return {}
	found = {}
	for column, record in said.items():
		try:
			number = int(column)
		except (TypeError, ValueError):
			continue
		if number < 1 or not isinstance(record, dict):
			continue
		choice = _choiceFrom(record)
		if not choice.isEmpty:
			found[number] = choice
	return found


def _choiceFrom(record: dict):
	""":return: one column's decisions, defaulting anything that cannot be read."""
	from . import flowTable

	said = {}
	for name, allowed in (
		("overflow", flowTable.OVERFLOW_STYLES),
		("keep", flowTable.KEEP_ENDS),
		("headerKeep", flowTable.KEEP_ENDS),
	):
		value = record.get(name)
		said[name] = value if value in allowed else ""
	label = record.get("label")
	widths = {}
	for name in ("minWidth", "maxWidth"):
		try:
			widths[name] = max(0, int(record.get(name) or 0))
		except (TypeError, ValueError):
			widths[name] = 0
	return flowTable.ColumnChoice(
		label=label if isinstance(label, str) else "",
		startsAPage=bool(record.get("startsAPage")),
		plainCase=bool(record.get("plainCase")),
		**said,
		**widths,
	)


@dataclasses.dataclass(frozen=True)
class SavedLayout:
	"""One saved layout, and what it is saved against.

	Frozen, and changed with `dataclasses.replace`, because the manager edits a working copy of the
	store and a record changed in place would be changed in the store it was read from too.
	"""

	id: str
	"""What names this record in the store, whatever its name and address become."""

	name: str = ""
	"""What the reader calls it. Made up from the address and headings when it is first saved, and
	empty for a layout carried over from the version 1 store until it is next seen."""

	where: str = ""
	"""The address it applies at, as `flowTableIdentity.whereOf` says it: a page's URL, a workbook's
	path, or an application and window class. Empty only for a layout from the version 1 store that
	has not been seen since, which is matched by `legacyWhere` instead."""

	match: str = MATCH_EXACT
	"""How strictly `where` has to match. One of `MATCHES`; anything but exact is for an address."""

	headings: tuple = ()
	"""The table's first headings when it was saved, which is what tells two tables in one place apart."""

	requireHeadings: bool = True
	"""Whether `headings` have to match. The manager can let a layout apply to whatever table is at
	its address, which is the answer for a site that renames a column in its first few."""

	layout: dict = dataclasses.field(default_factory=dict)
	"""The layout itself, as `TableLayout.asRecord` writes it."""

	saved: int = 0
	"""When it was last saved, in seconds."""

	used: int = 0
	"""When it was last applied, to within `USED_EVERY`, or when it was saved if it has not been since."""

	legacyWhere: str = ""
	"""The version 1 store's digest of the address, for a layout carried over from it."""

	legacyWhat: str = ""
	"""The version 1 store's digest of the headings, likewise. Empty meant "any table at the address"."""

	@property
	def isLegacy(self) -> bool:
		""":return: whether this is a layout from the version 1 store that has not been seen since."""
		return not self.where and bool(self.legacyWhere)

	@property
	def tableLayout(self) -> TableLayout:
		return fromRecord(self.layout)

	def asStored(self) -> dict:
		stored: dict = {"id": self.id, "layout": dict(self.layout), "saved": self.saved, "used": self.used}
		if self.name:
			stored["name"] = self.name
		if self.where:
			stored["where"] = self.where
			stored["match"] = self.match
		if self.headings:
			stored["headings"] = list(self.headings)
		if not self.requireHeadings:
			stored["requireHeadings"] = False
		if self.isLegacy:
			stored["legacyWhere"] = self.legacyWhere
			stored["legacyWhat"] = self.legacyWhat
		return stored

	@classmethod
	def fromStored(cls, stored: Any) -> Optional["SavedLayout"]:
		""":return: a record read back, or None for something that is not one.

		As forgiving as `fromRecord`, for the same reason: the file is edited by hand and written by
		other versions, and one bad entry should cost that entry.
		"""
		if not isinstance(stored, dict) or not isinstance(stored.get("id"), str) or not stored["id"]:
			return None
		layout = stored.get("layout")
		if not isinstance(layout, dict):
			return None
		where = stored.get("where") if isinstance(stored.get("where"), str) else ""
		legacyWhere = stored.get("legacyWhere") if isinstance(stored.get("legacyWhere"), str) else ""
		if not where and not legacyWhere:
			return None
		headings = stored.get("headings")
		match = stored.get("match")
		return cls(
			id=stored["id"],
			name=stored.get("name") if isinstance(stored.get("name"), str) else "",
			where=where,
			match=match
			if match in MATCHES and (match == MATCH_EXACT or isAddress(where))
			else defaultMatchFor(where),
			headings=tuple(str(heading) for heading in headings) if isinstance(headings, list) else (),
			requireHeadings=stored.get("requireHeadings") is not False,
			layout=layout,
			saved=_seconds(stored.get("saved")),
			used=_seconds(stored.get("used")),
			legacyWhere="" if where else legacyWhere,
			legacyWhat=""
			if where
			else (stored.get("legacyWhat") if isinstance(stored.get("legacyWhat"), str) else ""),
		)


def _seconds(said: Any) -> int:
	try:
		return max(0, int(said or 0))
	except (TypeError, ValueError):
		return 0


def newId() -> str:
	""":return: a name for a new record in the store."""
	return uuid.uuid4().hex[:12]


# Addresses


def _address(where: str) -> Optional[tuple]:
	""":return: (site, path) for a web address, or None for anything else — a workbook, a control.

	The site is the host in lower case, with its port if it has one. The path loses a trailing
	slash, which a site adds and drops without meaning anything by it.
	"""
	if not where:
		return None
	try:
		parts = urllib.parse.urlsplit(where)
		if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
			return None
		site = parts.hostname.lower()
		if parts.port:
			site = f"{site}:{parts.port}"
	except ValueError:
		return None
	return site, parts.path.rstrip("/") or "/"


def isAddress(where: str) -> bool:
	""":return: whether this is a web address, which is what a match other than exact is for."""
	return _address(where) is not None


def _site(site: str) -> str:
	""":return: a site without its www, which a site answers to with and without."""
	return site[4:] if site.startswith("www.") else site


def sameSite(where: str, other: str) -> bool:
	""":return: whether two addresses are web pages on the same site, www or not."""
	mine, theirs = _address(where), _address(other)
	return bool(mine and theirs and _site(mine[0]) == _site(theirs[0]))


def defaultMatchFor(where: str) -> str:
	""":return: how strictly a new layout saved at this address matches. See `MATCH_PATH`."""
	return MATCH_PATH if isAddress(where) else MATCH_EXACT


def placeMatches(saved: SavedLayout, where: str) -> bool:
	""":return: whether a saved layout applies at this address, before its headings are asked about.

	:param saved: the layout.
	:param where: where the table is, as `flowTableIdentity.whereOf` says.
	"""
	if not where:
		return False
	if saved.isLegacy:
		return keyFor(where) == saved.legacyWhere
	if saved.match == MATCH_EXACT:
		return saved.where == where
	mine, theirs = _address(saved.where), _address(where)
	if mine is None or theirs is None:
		return saved.where == where
	if saved.match == MATCH_SITE:
		return _site(mine[0]) == _site(theirs[0])
	return mine == theirs


def _specificity(saved: SavedLayout) -> int:
	""":return: how particular a layout is about its address, so the most particular one wins."""
	if saved.isLegacy or saved.match == MATCH_EXACT:
		return 3
	return 2 if saved.match == MATCH_PATH else 1


def defaultNameFor(where: str, headings) -> str:
	""":return: what a new layout is called until the reader renames it.

	The address as a reader would say it, and the first few headings, since two tables on one page
	share the first half.
	"""
	address = _address(where)
	if address is not None:
		place = _site(address[0]) + ("" if address[1] == "/" else address[1])
	else:
		place = where
	said = [heading for heading in headings if heading][:3]
	return ", ".join([place, *said]) if said else place


def _normal(heading: Any) -> str:
	return " ".join(str(heading or "").split())


# Reading the table


class _Headings:
	"""A table's headings, read only as far as a question needs and each column once.

	`flowTableSource.declaredHeaders` is a read per column, made where the reader is waiting, so
	nothing here reads a heading nobody asked about.
	"""

	def __init__(self, handle):
		self.handle = handle
		self._signature: Optional[str] = None
		self._columns: dict = {}

	@property
	def signature(self) -> str:
		""":return: the first headings as `flowTableIdentity.signatureOf` joins them."""
		if self._signature is None:
			self._signature = flowTableIdentity.signatureOf(self.handle)
		return self._signature

	@property
	def first(self) -> tuple:
		""":return: the first headings, one per column, "" for a column that declares none."""
		said = self.signature
		return tuple(said.split(flowTableIdentity.SEPARATOR)) if said else ()

	def of(self, columns) -> dict:
		""":return: the heading of each column asked about that declares one, by column number."""
		from . import flowTableSource

		wanted = [column for column in columns if column not in self._columns]
		if wanted:
			try:
				said = flowTableSource.declaredHeaders(self.handle, wanted)
			except flowTableIdentity.CallCancelled:
				raise
			except Exception:
				log.debugWarning("Could not read a table's headings", exc_info=True)
				said = {}
			for column in wanted:
				self._columns[column] = _normal(said.get(column))
		return {column: self._columns[column] for column in columns if self._columns.get(column)}

	def everything(self) -> dict:
		""":return: every column's heading, to `FOLLOW_COLUMNS`."""
		count = min(int(getattr(self.handle, "numCols", 0) or 0), FOLLOW_COLUMNS)
		return self.of(range(1, count + 1))


class KnownHeadings:
	"""Headings already read, answering what `_Headings` answers without a table to read.

	For the manager, which asks which layout would apply after an edit and has only what it read of
	the table when it opened. So a heading beyond the first few is not known, and an inserted column
	is judged on those alone.
	"""

	def __init__(self, first):
		self.first = tuple(first)
		self.signature = flowTableIdentity.SEPARATOR.join(self.first) if any(self.first) else ""

	def of(self, columns) -> dict:
		return {
			column: self.first[column - 1]
			for column in columns
			if 0 < column <= len(self.first) and self.first[column - 1]
		}

	def everything(self) -> dict:
		return self.of(range(1, len(self.first) + 1))


def headingsOf(handle) -> tuple:
	""":return: a table's first headings, one per column, "" for a column that declares none."""
	return _Headings(handle).first if handle is not None else ()


def headingsMatch(saved: SavedLayout, headings, candidates: int = 1) -> bool:
	""":return: whether a table's headings are the ones a layout was saved against.

	The first headings in order, which is what they were saved as. Failing that, every saved heading
	still somewhere in the table: a site that inserts a column among the first few has changed the
	sequence and not the table, and the layout's own columns follow by heading. See `_followColumns`.

	:param saved: a layout whose address already matched.
	:param headings: the table's.
	:param candidates: how many version 1 layouts share the address, for the rule they had.
	"""
	if saved.isLegacy:
		if keyFor(headings.signature) == saved.legacyWhat:
			return True
		# One layout saved for this place and nothing to tell tables apart by. A list view that
		# declares no headings is the case: it has only its place to be known by.
		return saved.legacyWhat == "" and candidates == 1
	wanted = [_normal(heading) for heading in saved.headings]
	if not saved.requireHeadings or not any(wanted):
		return True
	here = [_normal(heading) for heading in headings.first]
	if here == wanted:
		return True
	present = set(headings.everything().values())
	return all(heading in present for heading in wanted if heading)


# The store


_parsed: tuple = (None, [])
"""The last store text read and what it read as, since `stored` is asked on every redraw."""


def stored() -> list:
	""":return: every saved layout."""
	global _parsed
	try:
		text = bmConfig.tableLayouts()
	except Exception:
		log.debugWarning("Could not read the saved table layouts", exc_info=True)
		return []
	if text != _parsed[0]:
		_parsed = (text, _fromStoredText(text))
	return list(_parsed[1])


def _fromStoredText(text: str) -> list:
	""":return: the layouts a stored text holds, in either version, leaving out what cannot be read.

	**Forgiving all the way down.** A stored `1`, or a place whose value is a string, once reached
	the lookup and raised there — in the middle of the reader walking into a table, which is a
	braille refresh that stops rather than an error anybody sees. One bad entry costs that entry.
	"""
	if not text:
		return []
	try:
		read = json.loads(text)
	except ValueError:
		log.debugWarning("Could not read the saved table layouts", exc_info=True)
		return []
	if not isinstance(read, dict):
		log.debugWarning(f"The saved table layouts are not a store but a {type(read).__name__}")
		return []
	if "version" not in read:
		return _fromVersionOne(read)
	layouts = []
	for stored in read.get("layouts") if isinstance(read.get("layouts"), list) else ():
		saved = SavedLayout.fromStored(stored)
		if saved is None:
			log.debugWarning("A saved table layout could not be read and was left out")
			continue
		layouts.append(saved)
	return layouts


def _fromVersionOne(read: dict) -> list:
	""":return: the layouts of the first store: address digest, then headings digest, then a record.

	Named by their digests until they are next seen, which is when the address and headings they
	belong to become known and are written down. See `find`.
	"""
	layouts = []
	for where, inner in read.items():
		if not isinstance(where, str) or not isinstance(inner, dict):
			log.debugWarning(f"A saved table layout is not kept where one would be: {where!r}")
			continue
		for what, record in inner.items():
			if not isinstance(what, str) or not isinstance(record, dict):
				log.debugWarning(f"A saved table layout under {where!r} is not a record")
				continue
			layout = {name: value for name, value in record.items() if name != "used"}
			used = _seconds(record.get("used"))
			layouts.append(
				SavedLayout(
					id=f"{where}{what}"[:24] or newId(),
					layout=layout,
					saved=used,
					used=used,
					legacyWhere=where,
					legacyWhat=what,
				),
			)
	return layouts


def replaceAll(layouts) -> None:
	"""Store exactly these layouts, as the manager does when the reader presses OK.

	:param layouts: every layout to keep.
	:raises LayoutsNotSaved: if the configuration would not take them.
	"""
	_write(list(layouts))


class LayoutsNotSaved(Exception):
	"""The configuration would not take the saved layouts, so nothing asked for was saved.

	Raised rather than logged and swallowed, which is what the store did: a review found the reader
	told "saved" and "deleted" of changes that were never written, and the manager forgetting it had
	anything left to write.
	"""


def _write(layouts: list) -> None:
	"""Put the store back, dropping the least recently used if it has grown too large.

	:raises LayoutsNotSaved: if the configuration would not take it. Nothing is changed then.
	"""
	global _parsed
	kept = _trimmed(layouts)
	text = json.dumps(
		{"version": STORE_VERSION, "layouts": [saved.asStored() for saved in kept]},
		separators=(",", ":"),
	)
	try:
		bmConfig.setTableLayouts(text)
	except Exception as error:
		log.error("BrlMultiline: could not save the table layouts", exc_info=True)
		raise LayoutsNotSaved(str(error)) from error
	_parsed = (text, kept)


def _trimmed(layouts: list) -> list:
	""":return: the store, with the least recently used dropped if it is over `MAX_SAVED`."""
	if len(layouts) <= MAX_SAVED:
		return list(layouts)
	keep = sorted(layouts, key=lambda saved: (saved.used, saved.saved), reverse=True)[:MAX_SAVED]
	kept = {id(saved) for saved in keep}
	return [saved for saved in layouts if id(saved) in kept]


def _withChanged(layouts: list, changed: SavedLayout) -> list:
	return [changed if saved.id == changed.id else saved for saved in layouts]


# Finding the layout for a table


@dataclasses.dataclass(frozen=True)
class Found:
	"""What `find` made of the table in front of the reader."""

	where: str
	"""Where the table is. Empty for a table that cannot be named."""

	headings: tuple = ()
	"""Its first headings, where anything was saved at its address to make reading them worth it."""

	saved: Optional[SavedLayout] = None
	"""The layout that applies, or None."""

	reason: str = ""
	"""Why none applies, for the log and the manager. Empty when one does."""


def find(
	handle,
	layouts: Optional[list] = None,
	headings: Optional["_Headings"] = None,
	where: Optional[str] = None,
) -> Found:
	""":return: which saved layout applies to this table, and why not if none does.

	**Cheap where nothing is saved at the address**, which is every table until the reader saves one:
	the address is one attribute read and its headings are not read at all. Among the layouts that
	match, the one most particular about its address wins, then one that asks for headings over one
	that does not, then the one used most recently.

	:param handle: the table, as `flowTableSource.tableAt` returned it.
	:param layouts: the store, to look in something other than what is saved.
	:param headings: the table's headings, where a caller has already been reading them.
	:param where: where the table is, for a caller that already knows. See `KnownHeadings`.
	"""
	layouts = stored() if layouts is None else layouts
	if where is None:
		where = flowTableIdentity.whereOf(handle) if handle is not None else ""
	if not where:
		return Found("", reason="this table cannot be named")
	here = [saved for saved in layouts if placeMatches(saved, where)]
	if not here:
		return Found(where, reason=f"no layout is saved for this place: {where!r}")
	headings = headings if headings is not None else _Headings(handle)
	legacy = {}
	for saved in here:
		if saved.isLegacy:
			legacy[saved.legacyWhere] = legacy.get(saved.legacyWhere, 0) + 1
	matching = [saved for saved in here if headingsMatch(saved, headings, legacy.get(saved.legacyWhere, 0))]
	if not matching:
		return Found(
			where,
			headings.first,
			reason=(
				f"a layout is saved for {where!r}, but not for a table with these headings: "
				f"{list(headings.first)!r}"
			),
		)
	return Found(where, headings.first, bestOf(matching))


def bestOf(matching: list) -> SavedLayout:
	""":return: the one of several matching layouts that applies. See `find`."""
	return max(
		matching,
		key=lambda saved: (
			_specificity(saved),
			saved.requireHeadings and any(saved.headings),
			saved.used,
			saved.saved,
		),
	)


def layoutFor(handle, follow: bool = True) -> Optional[TableLayout]:
	""":return: the layout saved for this table, with its columns followed to where they are now, or None.

	What it writes it writes seldom: a layout from the version 1 store is written down readably the
	first time it is seen, and the date a layout was used at most once a day. See `USED_EVERY`. A
	write that fails is logged and costs nothing else, since the layout is still the one to read with.

	:param handle: the table, as `flowTableSource.tableAt` returned it.
	:param follow: whether to follow the columns it names to where they are now. **Only for a caller
		about to read the table with it**, since following reads the headings of those columns, and the
		band asks on every redraw only whether a layout applies. So columns are followed once per table
		the band builds, and nothing followed is kept to go stale: a review found a remembered following
		carried to the next page load of a table whose first headings were the same and whose later
		columns had moved again.
	"""
	layouts = stored()
	if not layouts or handle is None:
		return None
	headings = _Headings(handle)
	found = find(handle, layouts, headings)
	if found.saved is None:
		if found.where:
			_explainMiss(found.reason, (found.where, found.headings))
		return None
	saved = found.saved
	now = _now()
	changed = saved
	if saved.isLegacy:
		changed = dataclasses.replace(
			saved,
			name=defaultNameFor(found.where, found.headings),
			where=found.where,
			match=defaultMatchFor(found.where),
			headings=found.headings,
			legacyWhere="",
			legacyWhat="",
		)
		log.info(f"BrlMultiline: a saved table layout from before names were kept is now {changed.name!r}")
	if now - saved.used >= USED_EVERY:
		changed = dataclasses.replace(changed, used=now)
	if changed is not saved:
		try:
			_write(_withChanged(layouts, changed))
		except LayoutsNotSaved:
			pass
	if not follow:
		return changed.tableLayout
	return _followColumns(changed, found.where, headings)


def _followColumns(saved: SavedLayout, where: str, headings: _Headings) -> TableLayout:
	""":return: a saved layout with every column it names moved to where that column's heading is now.

	A column whose heading is where it was stays. One whose heading is now under another number is
	followed there — **only where that is the one place it can have gone**: its heading is not shared
	with another column the layout names, exactly one column of the table has it now, and no other
	column has already been given that place. A review found two saved columns both called "Value"
	followed onto the one "Value" the table had, and what was decided about one silently overwriting
	the other. Anything that cannot be followed keeps its number, which is right for a column the site
	renamed and a guess for one it removed, unless another column was followed onto that number, in
	which case it is left out rather than drawn twice. Either way it is logged.

	:param saved: the layout that applies.
	:param where: where the table is.
	:param headings: the table's headings.
	"""
	layout = saved.tableLayout
	named = {
		column: _normal(heading)
		for column, heading in layout.columnHeadings.items()
		if column in layout.namedColumns and _normal(heading)
	}
	if not named:
		return layout
	now = headings.of(sorted(named))
	moved = {column: heading for column, heading in named.items() if now.get(column) != heading}
	if not moved:
		return layout
	everywhere = headings.everything()
	sharedBySaved = {heading for heading in named.values() if list(named.values()).count(heading) > 1}
	taken = {column for column in named if column not in moved}
	mapping = {}
	lost = []
	for column, heading in sorted(moved.items()):
		places = [number for number, said in everywhere.items() if said == heading]
		if heading not in sharedBySaved and len(places) == 1 and places[0] not in taken:
			mapping[column] = places[0]
			taken.add(places[0])
		else:
			lost.append(column)
	destinations = set(mapping.values())

	def to(column: int) -> Optional[int]:
		if column in mapping:
			return mapping[column]
		if column in moved and column in destinations:
			# Could not be followed, and its number now belongs to a column that was.
			return None
		return column

	columns = []
	for number in (to(column) for column in layout.columns):
		if number and number not in columns:
			columns.append(number)
	result = dataclasses.replace(
		layout,
		columns=tuple(columns),
		perColumn={to(column): choice for column, choice in layout.perColumn.items() if to(column)},
		keyColumn=(to(layout.keyColumn) or 0) if layout.keyColumn else 0,
		columnHeadings={
			to(column): heading for column, heading in layout.columnHeadings.items() if to(column)
		},
	)
	said = [
		f"{named[column]!r} from column {column} to {number}" for column, number in sorted(mapping.items())
	]
	said += [
		f"{named[column]!r} cannot be found in one place, so "
		+ (f"kept at column {column}" if to(column) else "left out")
		for column in sorted(lost)
	]
	_explainMiss(
		f"columns of {saved.name or where!r} have moved: {'; '.join(said)}",
		("moved", saved.id, where, tuple(sorted(mapping.items())), tuple(lost)),
	)
	return result


_explained: set = set()
"""What `_explainMiss` has already said this session, so a table walked through twice says it once."""


def _explainMiss(said: str, about) -> None:
	"""Say in the log why a table was not laid out as saved, once for each place and table.

	**A saved layout that stops applying was silent.** A site that adds a query string to its
	address or renames a column has changed what a layout is matched on without the reader doing
	anything. Reported from hardware as a watchlist that stopped coming up laid out, with nothing in
	the log to say which half had moved.

	:param said: why.
	:param about: what it was about, so the same miss is said once.
	"""
	if about in _explained:
		return
	if len(_explained) > MAX_SAVED:
		_explained.clear()
	_explained.add(about)
	if said.startswith("columns of"):
		log.info(f"BrlMultiline: saved table layout applied, but {said}")
	else:
		log.info(f"BrlMultiline: saved table layout not applied, {said}")


def remember(handle, layout: TableLayout) -> bool:
	"""Save a layout against the table in front of the reader.

	**Into the layout that already applies here, where one does**, keeping its name, its address and
	how strictly that matches. Saving again after arranging a column is changing that layout, not
	making a second one beside it that the first would then compete with.

	The headings of the columns it names are read and written down with it, which is what lets those
	columns be followed if the site moves them. See `_followColumns`.

	:param handle: the table.
	:param layout: what they decided.
	:return: whether anything was saved, which is no for a table that cannot be named.
	:raises LayoutsNotSaved: if the configuration would not take it.
	"""
	layouts = stored()
	headings = _Headings(handle)
	found = find(handle, layouts, headings)
	if not found.where:
		return False
	named = sorted(layout.namedColumns)
	record = dataclasses.replace(layout, columnHeadings=headings.of(named) if named else {}).asRecord()
	now = _now()
	first = headings.first
	if found.saved is not None:
		saved = found.saved
		changed = dataclasses.replace(saved, layout=record, saved=now, used=now)
		if saved.isLegacy:
			changed = dataclasses.replace(
				changed,
				name=defaultNameFor(found.where, first),
				where=found.where,
				match=defaultMatchFor(found.where),
				headings=first,
				legacyWhere="",
				legacyWhat="",
			)
		_write(_withChanged(layouts, changed))
		return True
	layouts.append(
		SavedLayout(
			id=newId(),
			name=defaultNameFor(found.where, first),
			where=found.where,
			match=defaultMatchFor(found.where),
			headings=first,
			layout=record,
			saved=now,
			used=now,
		),
	)
	_write(layouts)
	return True


def forget(handle) -> bool:
	"""Drop the layout that applies to this table.

	:param handle: the table.
	:return: whether there was one to drop.
	:raises LayoutsNotSaved: if the configuration would not take the store without it.
	"""
	layouts = stored()
	found = find(handle, layouts)
	if found.saved is None:
		return False
	_write([saved for saved in layouts if saved.id != found.saved.id])
	return True


def dateWords(seconds: int) -> str:
	""":return: a date as a reader says it, or "never" for none. For the manager's list."""
	if not seconds:
		# Translators: said in place of a date for a saved table layout that has no record of it.
		return _("never")
	day = datetime.date.fromtimestamp(seconds)
	return f"{day.strftime('%B')} {day.day}, {day.year}"


def layoutFrom(plan, handle=None, arranged: Optional[TableLayout] = None) -> TableLayout:
	""":return: what the remember command saves, which today is the *request* and nothing else.

	**A measurement is not a decision.** The first cut of this wrote down the columns as they
	were drawn, on the reasoning that a reader who has paged through a layout and pressed
	remember is looking at something they want back. What they are looking at, though, is what
	the measurement made of the table a moment ago: a column blank in the bandful that was
	sampled is not drawn, and writing that into the record froze it out of every later reading —
	so a column that fills in later, or was merely empty where the reader happened to be, could
	never come back. The reader who found this had configured nothing at all; they had pressed
	one key, and their watchlist's first column was excluded for good.

	So the record says only "lay this table out", and the columns are measured afresh each time,
	which is what happens for a table with no record at all. Every other field still says "not
	my business" and follows the settings.

	**When there is a way to choose columns there will be something to write here** — M7's
	designer, or M6b's favourite by name — and the field is kept for it: a record that names
	columns is still honoured, which is also what a record saved by an older build does.

	:param plan: the column plan in force. Read for nothing, and kept because a caller with
		a plan and no arrangement is the ordinary case and should not have to say so twice.
	:param handle: the table, for a future field that needs it. Unused today.
	:param arranged: what the reader has arranged for this table with the band commands or the
		dialog, where they have arranged anything. That *is* a decision, and it is what this
		hands back when there is one.
	"""
	return arranged if arranged is not None else TableLayout()


def _now() -> int:
	""":return: a coarse clock for ordering the store, in whole seconds."""
	import time

	return int(time.time())


__all__ = [
	"FOLLOW",
	"MATCHES",
	"MATCH_EXACT",
	"MATCH_PATH",
	"MATCH_SITE",
	"NO",
	"Found",
	"SavedLayout",
	"TableLayout",
	"YES",
	"dateWords",
	"defaultMatchFor",
	"defaultNameFor",
	"KnownHeadings",
	"LayoutsNotSaved",
	"bestOf",
	"find",
	"forget",
	"headingsMatch",
	"headingsOf",
	"fromRecord",
	"isAddress",
	"layoutFor",
	"layoutFrom",
	"placeMatches",
	"remember",
	"replaceAll",
	"sameSite",
	"stored",
]
