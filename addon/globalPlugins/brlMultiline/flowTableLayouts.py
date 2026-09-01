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

Stored as one JSON string per configuration profile, keyed by `flowTableIdentity`. JSON
rather than a nest of `configobj` sections, because the keys are URLs and window classes and
a configuration key is not allowed to be either.
"""

import dataclasses
import hashlib
import json
from typing import Any, Optional

from logHandler import log

from . import bmConfig, flowTableIdentity

FOLLOW = ""
"""What a three-state field says when it defers to the reader's ordinary settings."""

YES = "yes"
NO = "no"

KEY_LENGTH = 16
"""How much of a digest names one table in the store.

Sixty-four bits. The store holds at most `MAX_SAVED` entries, so a collision is not a thing
that happens; what it buys is that the file does not carry the reader's URLs around.
"""


def keyFor(said: str) -> str:
	""":return: how one half of an identity is written down.

	**A digest rather than the thing itself.** An identity is a URL — with its query string,
	which is where a session token lives — or a local file path, or the headings of a table the
	reader has open; the store is a file in their configuration folder that goes wherever a
	profile goes. A digest matches exactly as the text did and says nothing about what was
	matched. Nothing reads these keys back for meaning: the lookup compares them and that is
	all, and the log still names tables in full, where it is the reader's own screen.

	Empty stays empty, since "" is a real answer — a list view that declares no headings has
	only its place to be known by, and `layoutFor` looks for that exact key.

	:param said: a part of an identity, from `flowTableIdentity`.
	"""
	if not said:
		return ""
	return hashlib.sha256(said.encode("utf-8", "replace")).hexdigest()[:KEY_LENGTH]


MAX_SAVED = 200
"""How many tables may be remembered before the oldest is dropped.

A bound on the configuration file rather than on the reader: two hundred tables is more than
anyone lays out by hand, and a store that grows without limit is a file that eventually
cannot be written.

**Dropped by age of last *saving*, not of last reading**, which a review pointed out is not
what "least recently used" means. It is deliberate: a lookup happens every time the reader
walks into a table, and writing the configuration file from a braille refresh to record that
they read one is a cost paid constantly to improve an eviction that happens once in two
hundred layouts. The reader whose watchlist is evicted saves it again with one keystroke.
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
		return record

	@property
	def isEmpty(self) -> bool:
		""":return: whether this decides nothing, which is a layout not worth saving."""
		return not self.asRecord()

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
	return TableLayout(
		columns=tuple(columns),
		rowHeight=max(0, rowHeight),
		keyColumn=max(0, keyColumn),
		perColumn=_perColumnFrom(record.get("perColumn")),
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
	if getattr(choice, "startsAPage", False):
		record["startsAPage"] = True
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
		**said,
		**widths,
	)


def stored() -> dict:
	""":return: every saved layout, by where the table is and then by its signature.

	Two levels, because that is what makes the lookup cheap: the outer key is one attribute
	read of the table in front of the reader, and the inner one costs a read per column and is
	only asked for when the outer key is in here at all.
	"""
	try:
		read = json.loads(bmConfig.tableLayouts() or "{}")
	except Exception:
		log.debugWarning("Could not read the saved table layouts", exc_info=True)
		return {}
	return _asStore(read)


def _asStore(read: Any) -> dict:
	""":return: what was read, with anything that is not the shape of a store left out.

	**Forgiving all the way down, and it was not.** `fromRecord` is careful about a record it
	cannot read, and everything above it took the file on trust: a stored `1`, or a place whose
	value is a string, reached `layoutFor` and raised there — in the middle of the reader
	walking into a table, which is a braille refresh that stops rather than an error anybody
	sees. The module says outright that it reads a file a reader may have edited and a record
	another version wrote, so the whole shape has to be checked, not the innermost part of it.

	One bad entry costs that entry. Dropping the store because one place in it is malformed
	would lose every layout the reader has.

	:param read: whatever the configuration held.
	"""
	if not isinstance(read, dict):
		log.debugWarning(f"The saved table layouts are not a store but a {type(read).__name__}")
		return {}
	store: dict = {}
	for where, inner in read.items():
		if not isinstance(where, str) or not isinstance(inner, dict):
			log.debugWarning(f"A saved table layout is not kept where one would be: {where!r}")
			continue
		kept = {
			what: record
			for what, record in inner.items()
			if isinstance(what, str) and isinstance(record, dict)
		}
		if len(kept) != len(inner):
			log.debugWarning(f"A saved table layout under {where!r} is not a record")
		if kept:
			store[where] = kept
	return store


def _write(layouts: dict) -> None:
	"""Put the store back, dropping the least recently used if it has grown too large."""
	try:
		bmConfig.setTableLayouts(json.dumps(_trimmed(layouts), separators=(",", ":")))
	except Exception:
		log.debugWarning("Could not save the table layouts", exc_info=True)


def _trimmed(layouts: dict) -> dict:
	""":return: the store, with the oldest entries dropped if it is over `MAX_SAVED`."""
	total = sum(len(inner or {}) for inner in layouts.values())
	if total <= MAX_SAVED:
		return layouts
	ranked = [
		(inner.get(what, {}).get("used", 0), where, what)
		for where, inner in layouts.items()
		for what in list(inner or {})
	]
	ranked.sort()
	for _used, where, what in ranked[: total - MAX_SAVED]:
		layouts[where].pop(what, None)
		if not layouts[where]:
			layouts.pop(where, None)
	return layouts


def layoutFor(handle) -> Optional[TableLayout]:
	""":return: the layout saved for this table, or None if there is none.

	**Cheap when there is nothing saved**, which is every table until the reader saves one:
	the outer key is one attribute read, and a place that is not in the store ends the lookup
	there. Only a place that has something under it is worth reading the headings for.

	:param handle: the table, as `flowTableSource.tableAt` returned it.
	"""
	layouts = stored()
	if not layouts:
		return None
	where = flowTableIdentity.whereOf(handle)
	inner = layouts.get(keyFor(where)) if where else None
	if not inner:
		return None
	record = inner.get(keyFor(flowTableIdentity.signatureOf(handle)))
	if record is None and len(inner) == 1 and "" in inner:
		# One layout saved for this place and nothing to tell tables apart by. A list view
		# that declares no headings is the case: it has only its place to be known by, and
		# refusing the reader their own layout because of that would be refusing them the
		# feature in the control they saved it from.
		record = inner[""]
	return fromRecord(record) if record is not None else None


def remember(handle, layout: TableLayout) -> bool:
	"""Save a layout against the table in front of the reader.

	:param handle: the table.
	:param layout: what they decided.
	:return: whether anything was saved, which is no for a table that cannot be named.
	"""
	identity = flowTableIdentity.identityOf(handle)
	if not identity.isKnown:
		return False
	layouts = stored()
	where = keyFor(identity.where)
	inner = dict(layouts.get(where) or {})
	record = layout.asRecord()
	record["used"] = _now()
	inner[keyFor(identity.what)] = record
	layouts[where] = inner
	_write(layouts)
	return True


def forget(handle) -> bool:
	"""Drop the layout saved for this table.

	:param handle: the table.
	:return: whether there was one to drop.
	"""
	identity = flowTableIdentity.identityOf(handle)
	layouts = stored()
	where = keyFor(identity.where)
	inner = layouts.get(where) if identity.isKnown else None
	if not inner:
		return False
	if inner.pop(keyFor(identity.what), None) is None and inner.pop("", None) is None:
		return False
	if inner:
		layouts[where] = inner
	else:
		layouts.pop(where, None)
	_write(layouts)
	return True


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
	"NO",
	"TableLayout",
	"YES",
	"forget",
	"fromRecord",
	"layoutFor",
	"layoutFrom",
	"remember",
	"stored",
]
