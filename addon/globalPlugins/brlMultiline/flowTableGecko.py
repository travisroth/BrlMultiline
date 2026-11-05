# BrlMultiline: reading a Gecko or Chromium table's cells without finding the table each time.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A table in a Gecko or Chromium document, found once and held for one build.

`Gecko_ia2._getTableCellAt` finds the table again for every cell. `HeldTable.cellAt` is that
method with the finding moved out and must stay in step with it. Any document that replaces the
method is left to it. See docs/design/structured-presentation-plan.md, Table timing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from comtypes import COMError
from logHandler import log

from . import stageTimes
from .flow import CallCancelled


class HeldTableFailed(Exception):
	"""The held table could not answer, so the cell should be read the ordinary way.

	Not "there is no cell here", which is `LookupError`.
	"""


class HeldTable:
	"""One table, with the call that finds its cells."""

	def __init__(
		self,
		document,
		tableID,
		findCell: Callable[[int, int], Any],
		interface,
		cellObject: Callable[..., Any],
	) -> None:
		"""
		:param document: the document the table is in.
		:param tableID: NVDA's identifier for the table.
		:param findCell: the table's own call for a cell, taking a zero based row and column.
		:param interface: what a cell is asked for once found.
		:param cellObject: makes an NVDA object of a cell.
		"""
		self.document = document
		self.tableID = tableID
		self._findCell = findCell
		self._interface = interface
		self._cellObject = cellObject

	def holds(self, handle) -> bool:
		""":return: whether this is the table a handle names.

		:param handle: the table asked about.
		"""
		return handle.document is self.document and handle.tableID == self.tableID

	def cellAt(self, row: int, column: int):
		""":return: where one cell is, as `_getTableCellAt` returns it.

		:param row: the row, one based.
		:param column: the column, one based.
		:raises LookupError: where the cell is hidden or the document does not have it.
		:raises HeldTableFailed: where the table could not answer.
		"""
		try:
			with stageTimes.stage("cell at"):
				found = self._findCell(row - 1, column - 1).QueryInterface(self._interface)
			with stageTimes.stage("cell object"):
				cell = self._cellObject(IAccessibleObject=found, IAccessibleChildID=0)
			with stageTimes.stage("hidden check"):
				hidden = cell.IA2Attributes.get("hidden")
			if hidden:
				raise LookupError("Found hidden cell")
			with stageTimes.stage("cell info"):
				return self.document.makeTextInfo(cell)
		except (COMError, RuntimeError) as error:
			raise HeldTableFailed from error


def _findCellOf(table) -> Callable[[int, int], Any] | None:
	""":return: the table's call for a cell, from either table interface, or None.

	:param table: the table's NVDA object, or None.
	"""
	found = getattr(getattr(table, "IAccessibleTable2Object", None), "cellAt", None)
	if found is not None:
		return found
	return getattr(getattr(table, "IAccessibleTableObject", None), "accessibleAt", None)


def holdTable(handle) -> HeldTable | None:
	"""Find a table once, where its document reads cells the way `HeldTable` does.

	:param handle: the table.
	:return: the held table, or None to read it the ordinary way.
	:raises CallCancelled: where NVDA stopped waiting, which is not a reason to try another way.
	"""
	try:
		from comInterfaces.IAccessible2Lib import IAccessible2
		from NVDAObjects.IAccessible import IAccessible
		from virtualBuffers.gecko_ia2 import Gecko_ia2
	except ImportError:
		return None
	document = handle.document
	if getattr(type(document), "_getTableCellAt", None) is not Gecko_ia2._getTableCellAt:
		return None
	try:
		findCell = _findCellOf(document.getNVDAObjectFromIdentifier(*handle.tableID))
	except CallCancelled:
		raise
	except Exception:
		log.debugWarning("Could not hold a table to read it", exc_info=True)
		return None
	if findCell is None:
		return None
	return HeldTable(document, handle.tableID, findCell, IAccessible2, IAccessible)
