# BrlMultiline: naming a table, so that a layout can be remembered against it.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Which table is this, said in a way that survives the page being loaded again.

A name in two parts, so a saved layout is found again:

1. `where`, the document or control: a URL, or an application and window class. One read.
2. `what`, the columns by their headings, which tell two tables in one place apart and survive
   regenerated markup. A read per column, so asked only when `where` has something saved. See
   `flowTableLayouts.layoutFor`.

An identity may be a guess: a wrong match is felt at once and undone with a keystroke.
"""

import dataclasses
from typing import Any, Optional

from logHandler import log

from .flow import CallCancelled

SIGNATURE_COLUMNS = 8
"""How many of a table's columns are read to make its signature.

Enough to tell tables apart, and a bound on reading as the reader arrives.
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

	Empty is a real answer; no signature is invented from the data.
	"""

	@property
	def isKnown(self) -> bool:
		""":return: whether this names anything at all."""
		return bool(self.where)

	def __str__(self) -> str:
		return f"{self.where}{SEPARATOR}{self.what}" if self.what else self.where


def whereOf(handle) -> str:
	""":return: the document or control a table is in, or "" if it will not say.

	A browse mode document's `documentConstantIdentifier`, as NVDA uses across loads; for a
	control, the application and window class, as for File Explorer's file list.

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

	For a table that is neither, such as a sheet, whose window class is the same in every
	workbook. See `flowObjectTable.Sheet.whereIsIt`.
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

	The expensive half, bounded by `SIGNATURE_COLUMNS`. `declaredHeaders` asks a few rows, since
	one half built row would rename the table.

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
	:param withSignature: whether to read the headings. False, the cheap half alone, is what a
		lookup asks first.
	"""
	where = whereOf(handle)
	if not where:
		return TableIdentity("")
	return TableIdentity(where, signatureOf(handle) if withSignature else "")


__all__ = ["SIGNATURE_COLUMNS", "TableIdentity", "identityOf", "signatureOf", "whereOf"]
