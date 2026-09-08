# BrlMultiline: naming a table, so that a layout can be remembered against it.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Which table is this, said in a way that survives the page being loaded again.

The reader presses a key to see a table in columns, and today that is where it ends: leave
the table and the layout is gone, come back tomorrow and it is gone again. What they asked
for is a watchlist that comes up laid out. That needs a *name* for the table — something
computed from what is in front of the reader, stable enough that the same table answers to
it next time, and specific enough that the table beside it does not.

**A name has two parts, and they cost very different amounts.**

`where` is the document or the control: a page's URL, or an application and the window class
of the list inside it. One attribute read, always available, and never wrong in a way the
reader can be hurt by — two tables on one page share it.

`what` is the table's own columns, by the headings they declare. That is what tells two
tables in one place apart, and it is what survives a page being regenerated with new
identifiers, which is the case that matters for a watchlist. It costs a read per column, so
it is asked for **only when `where` already has something saved against it** — see
`flowTableLayouts.layoutFor`. A reader who has saved nothing pays one attribute read per
table they walk into, and nothing else.

**An identity is allowed to be a guess.** A wrong match lays a table out the way the reader
laid out a different one, which they feel immediately and undo with one keystroke; a missed
match lays it out the way it would have been laid out anyway. Neither is a failure worth
making the reader wait for, which is the whole argument for bounding the signature and for
not going looking for a better answer than the two above.
"""

import dataclasses
from typing import Any, Optional

from logHandler import log

from .flow import CallCancelled

SIGNATURE_COLUMNS = 8
"""How many of a table's columns are read to make its signature.

Eight headings tell any two tables apart that a reader has both of; the twenty-ninth column
of a watchlist adds a read and no certainty. It is also a bound on a call into the document,
made at the moment the reader arrives somewhere, which is the moment they are waiting.
"""

SEPARATOR = "␟"
"""What joins the parts of a signature: the unit separator, which no heading contains."""


@dataclasses.dataclass(frozen=True)
class TableIdentity:
	"""What names one table, in the two parts `flowTableIdentity` computes separately."""

	where: str
	"""The document or control it is in. A URL for a page, an application and window class
	for a list view, and "" for something that will not say — which is a table that cannot be
	remembered."""

	what: str = ""
	"""Its columns, by the headings they declare, or "" where it declares none.

	Empty is a real answer rather than a missing one: a list view that declares no headings
	has only its place to be known by, and two such lists in one window cannot be told apart.
	That is a limit worth having plainly rather than a signature invented out of the data.
	"""

	@property
	def isKnown(self) -> bool:
		""":return: whether this names anything at all."""
		return bool(self.where)

	def __str__(self) -> str:
		return f"{self.where}{SEPARATOR}{self.what}" if self.what else self.where


def whereOf(handle) -> str:
	""":return: the document or control a table is in, or "" if it will not say.

	**NVDA's own answer where it has one.** A browse mode document knows
	`documentConstantIdentifier`, which is the URL and is what NVDA itself remembers a caret
	position against across loads — the same kind of memory as a saved layout, so the same
	key. A list view has no such thing, and what stands in for it is the application and the
	window class: File Explorer's Details view is one control by that name whichever folder is
	open, which is what a reader means when they lay out "the file list".

	:param handle: the table, as `flowTableSource.tableAt` returned it.
	"""
	document = getattr(handle, "document", None)
	if document is None:
		return ""
	said = _saysWhereItIs(document)
	if said:
		return said
	said = _documentIdentifier(document)
	if said:
		return said
	return _controlIdentifier(getattr(document, "table", None) or getattr(document, "obj", None))


def _saysWhereItIs(document) -> str:
	""":return: what a table says names the place it is in, or "" where it says nothing.

	**For a table that is neither a page nor one control.** A review found two Excel workbooks
	sharing an identity: the application and the window class are "excel" and "EXCEL7" for
	every sheet of every workbook, so two sheets with the same headings were each other's
	saved layout. A workbook has a path and a sheet has a name, and nothing else here can know
	that — see `flowObjectTable.Sheet.whereIsIt`.
	"""
	said = getattr(document, "whereIsIt", None)
	if said is None:
		return ""
	try:
		found = said()
	except CallCancelled:
		# A table named by a read that never happened is a table matched against somebody
		# else's saved layout. See `flow.CallCancelled`.
		raise
	except Exception:
		log.debugWarning("Could not ask a table where it is", exc_info=True)
		return ""
	return str(found).strip() if found else ""


def _documentIdentifier(document) -> str:
	""":return: a browse mode document's own identifier, or "" where there is none.

	A page that will not say is not named after its title instead: a title changes while the
	page does not, and two tabs of one site share it.
	"""
	try:
		said = getattr(document, "documentConstantIdentifier", None)
	except Exception:
		log.debugWarning("Could not ask a document what it is", exc_info=True)
		return ""
	return str(said).strip() if said else ""


def _controlIdentifier(obj) -> str:
	""":return: the application and window a control belongs to, or "" if it will not say.

	Both, because either alone names too much: a window class of "DirectUIHWND" is half of
	Windows, and an application has many lists in it.

	:param obj: the object holding the rows.
	"""
	if obj is None:
		return ""
	found = []
	for what, thing in (("appName", getattr(obj, "appModule", None)), ("windowClassName", obj)):
		try:
			said = getattr(thing, what, None) if thing is not None else None
		except Exception:
			log.debugWarning(f"Could not read a control's {what}", exc_info=True)
			said = None
		found.append(str(said).strip() if said else "")
	return "/".join(found) if any(found) else ""


def signatureOf(handle, columns: Optional[Any] = None) -> str:
	""":return: a table's columns by the headings they declare, joined, or "" if none do.

	The expensive half of an identity, and the one that survives a page being regenerated:
	identifiers change with the markup and the headings are what the table is *about*.

	Bounded by `SIGNATURE_COLUMNS`, and asked only of a table whose place already has
	something saved against it. `declaredHeaders` asks a few rows rather than one, because a
	row that happens to be half built answers for the whole column — and a column that fell
	silent here would name the table something else, so the layout saved against it would not
	be found. That is a review finding: identity needs the same care measurement already took.

	:param handle: the table.
	:param columns: which columns to read, or None for the first few of the table's own.
	"""
	from . import flowTableSource

	wanted = list(columns) if columns is not None else list(range(1, handle.numCols + 1))
	wanted = wanted[:SIGNATURE_COLUMNS]
	if not wanted:
		return ""
	try:
		said = flowTableSource.declaredHeaders(handle, wanted)
	except CallCancelled:
		# A signature read from cancelled reads is a different table's name, and the layout
		# saved against the real one would not be found. See `flow.CallCancelled`.
		raise
	except Exception:
		log.debugWarning("Could not read a table's headings for its signature", exc_info=True)
		return ""
	headings = [" ".join((said.get(column) or "").split()) for column in wanted]
	return SEPARATOR.join(headings) if any(headings) else ""


def identityOf(handle, withSignature: bool = True) -> TableIdentity:
	""":return: what names this table.

	:param handle: the table, as `flowTableSource.tableAt` returned it.
	:param withSignature: whether to read the headings. False answers the cheap half alone,
		which is what a lookup asks for first: a place with nothing saved against it needs no
		signature, and that is every place until the reader saves something.
	"""
	where = whereOf(handle)
	if not where:
		return TableIdentity("")
	return TableIdentity(where, signatureOf(handle) if withSignature else "")


__all__ = ["SIGNATURE_COLUMNS", "TableIdentity", "identityOf", "signatureOf", "whereOf"]
