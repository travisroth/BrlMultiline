# BrlMultiline: the commands that lay a table out in columns and arrange it.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The table commands: columns on and off, rows on one line, arranging columns, saved layouts.

The plugin's scripts call into this. The table is drawn by the flow band or by a pin; this asks
them for it and says what came of it. See docs/design/structured-presentation-plan.md, Tables, in two milestones.
"""

from __future__ import annotations

from typing import Protocol, TYPE_CHECKING

import addonHandler
import api
import braille
import ui
from logHandler import log

from . import keyLayerDispatch, tableRowLine
from .flowTableSource import wantsColumns

addonHandler.initTranslation()

if TYPE_CHECKING:
	from .container import DisplayContainer
	from .flowBand import FlowBand
	from .flowTable import ColumnPlan
	from .flowTableSource import TableRequest


class PagedTable(Protocol):
	"""A table laid out in columns that pages across them: the flow band's, or a pin's."""

	def columnPlan(self) -> ColumnPlan | None: ...

	def turnColumnPage(self, by: int) -> bool: ...


def columnName(column) -> str:
	""":return: what to call one column of a table when saying which ones are showing.

	Its heading, or failing that "column" and its number.

	:param column: the column, as the layout planned it.
	"""
	if column.label:
		return column.label
	# Translators: how a column with no heading of its own is named when a table's columns
	# are reported. The placeholder is which column of the table it is.
	return _("column {index}").format(index=column.index)


class TableHost(Protocol):
	"""What `TableCommands` uses of the plugin. See docs/design/architecture.md, Module map."""

	flowBand: FlowBand | None
	tableRequest: TableRequest

	@property
	def container(self) -> DisplayContainer | None: ...

	def layOutPinnedTables(self, wanted: bool, obj) -> int: ...

	def reportAboutTheDisplay(self, text: str) -> None: ...

	def segmentKeysOn(self, driverName: str | None) -> list[str]: ...

	def tablesInColumns(self) -> dict[str, PagedTable]: ...


class TableCommands:
	"""The table commands, for the plugin's scripts."""

	def __init__(self, plugin: TableHost):
		"""
		:param plugin: the global plugin, for its band, its pins and its table request.
		"""
		self.plugin = plugin

	def toggleColumns(self) -> None:
		"""Read the table the reader is in as columns, or stop.

		A command rather than automatic, because most web tables are for layout. Saved layouts
		apply by themselves; see `rememberLayout`.
		"""
		band = self.plugin.flowBand
		target = api.getNavigatorObject()
		# Off only when the request is about where the reader is standing, or when they are
		# not in a table at all — pressing it outside one is how a request left behind is
		# cleared. Pressing it in a *different* table means that table, not this one off: with
		# no band nothing drops the old request, so the press that should have laid out the
		# second table was turning the first one off instead.
		if self.plugin.tableRequest.wanted is not None and not self._isInAnotherTable(target):
			# Remembered before the request is dropped, because dropping it is what lets a
			# saved layout put it back. See `TableRequest.refused`.
			self.plugin.tableRequest.refused = self.plugin.tableRequest.wanted
			if band is not None:
				band.clearTable()
			else:
				self.plugin.tableRequest.wanted = None
			self.plugin.layOutPinnedTables(False, target)
			# Translators: reported when a table stops being laid out in columns.
			self.plugin.reportAboutTheDisplay(_("Table columns off"))
			return
		# Neither an empty band nor no band is a reason to refuse: `FlowBand.refresh` lets a
		# requested table through the flow settings, and on a one row display the request
		# carries to a pin.
		if band is None or not band.isClaimed:
			if not self._requestTableAt(target):
				# Translators: reported when a command needs the cursor to be in a table.
				ui.message(_("Not in a table"))
				return
			self.plugin.layOutPinnedTables(True, target)
			ui.message(
				# Translators: reported when a table is asked for in columns while there is no
				# room on the display to show it, so that pinning it elsewhere shows it that
				# way.
				_("Table columns on, for a pinned copy; there is no band to show it here"),
			)
			return
		if not band.layOutTable():
			from .flowBand import NO_LAYOUT, NOT_READ

			if band.tableProblem == NOT_READ:
				ui.message(
					# Translators: reported when the cursor is in a table but nothing could be
					# read out of it, so there were no columns to arrange. Said apart from the
					# message below because this one is often a moment that has passed and is
					# worth trying again, where that one is about this display.
					_("Nothing could be read from this table; the NVDA log says why"),
				)
				return
			if band.tableProblem == NO_LAYOUT:
				ui.message(
					# Translators: reported when the cursor is in a table but it could not be
					# shown in columns. The NVDA log says which step it was.
					_("This table could not be laid out in columns; the NVDA log says why"),
				)
				return
			# Translators: reported when a command needs the cursor to be in a table.
			ui.message(_("Not in a table"))
			return
		self.plugin.layOutPinnedTables(True, target)
		control = band.controller
		plan = getattr(getattr(control, "renderer", None), "columnPlan", None)
		if plan is None:
			# Translators: reported when a table is laid out in columns.
			self.plugin.reportAboutTheDisplay(_("Table columns on"))
			return
		# What it cost is said, not only that it worked. A column narrower than its content
		# cuts every one of its cells at the same place, which is what makes the shape down
		# the display readable and also what leaves the reader feeling clipped words with
		# nothing to say they are clipped. Three columns of real text on a thirty-two cell
		# band is the ordinary case for it, not the extreme one.
		said = [
			# Translators: reported when a table is laid out in columns. The placeholder is
			# how many columns are shown.
			_("Table columns on, {shown} columns").format(shown=len(plan.columns)),
		]
		if plan.narrowed and plan.cuts:
			said.append(
				# Translators: reported after the above when some columns are too narrow for
				# what they hold and long values are being cut off. The placeholder is how
				# many columns are affected.
				_("{cut} cut to fit").format(cut=len(plan.narrowed)),
			)
		elif plan.narrowed:
			said.append(
				# Translators: reported after the above when some columns are too narrow for
				# what they hold, so long values continue on the next row. The placeholder is
				# how many columns are affected.
				_("{wrapped} wrapping").format(wrapped=len(plan.narrowed)),
			)
		if not plan.showsEverything:
			first, last, total = plan.whereItIs
			said.append(
				# Translators: reported after the above when a table has more columns than the
				# display can show at once. Placeholders are, in order, the first and last of
				# the columns being shown and how many columns there are in all.
				_("columns {first} to {last} of {total}").format(
					first=first,
					last=last,
					total=total,
				),
			)
		self.plugin.reportAboutTheDisplay(", ".join(said))

	def _isInAnotherTable(self, obj) -> bool:
		""":return: whether the reader is in a table other than the one already asked for.

		False in the table asked for and in no table at all, so pressing the command outside a
		table clears a request left behind.

		:param obj: the object the reader is on.
		"""
		from .flowTableSource import tableAt

		try:
			handle = tableAt(obj)
		except Exception:
			log.debugWarning("Could not look for the table the reader is in", exc_info=True)
			return False
		return handle is not None and not wantsColumns(self.plugin.tableRequest.wanted, obj)

	def _requestTableAt(self, obj) -> bool:
		"""Record that the table an object is in should be read in columns.

		`FlowBand.layOutTable` without showing it, for when there is no band.

		:param obj: the object the reader is on.
		:return: whether they were in a table.
		"""
		from .flowTableSource import logExplanation, tableAt

		try:
			handle = tableAt(obj)
		except Exception:
			log.debugWarning("Could not look for a table to lay out", exc_info=True)
			return False
		if handle is None:
			# The same account the band writes when it refuses, because this is the same
			# refusal reaching the reader through the display that has no band.
			logExplanation(obj, "asked for a table in columns and found none")
			return False
		self.plugin.tableRequest.wanted = handle.key
		return True

	def toggleRowLine(self) -> None:
		"""Read the table the caret is in a row at a time on NVDA's one line, or stop.

		For a display with no band. See `tableRowLine`. On and off follow the same rule as
		`toggleColumns`.
		"""
		from . import flowObjectTable, flowTableLayouts
		from .flowTableSource import sameTable

		handle = self._tableHere()
		asked = tableRowLine.wanted()
		if asked is not None and (handle is None or sameTable(handle.key, asked.key)):
			tableRowLine.clear()
			tableRowLine.redraw(braille.handler)
			# Translators: reported when a table stops being read a row at a time on one line.
			self.plugin.reportAboutTheDisplay(_("Table rows off"))
			return
		if handle is None:
			# Translators: reported when a command needs the cursor to be in a table.
			ui.message(_("Not in a table"))
			return
		if isinstance(handle.document, flowObjectTable.ObjectTable):
			# A list or a worksheet is shown by NVDA a cell or an item at a time through regions
			# of its own, not through the browse mode line this replaces.
			ui.message(
				# Translators: reported when rows on one line are asked for in a table that is not
				# on a web page or in a document read in browse mode.
				_("Rows on one line are for tables in browse mode"),
			)
			return
		try:
			layout = flowTableLayouts.layoutFor(handle)
		except Exception:
			log.debugWarning("Could not look for a saved layout for this table", exc_info=True)
			layout = None
		# A saved layout's choice of columns and their order, where the reader made one, so
		# that the line says what the columns would. Everything else about a layout is widths.
		tableRowLine.request(handle, layout.columns if layout is not None else ())
		tableRowLine.redraw(braille.handler)
		# Translators: reported when a table starts being read a row at a time on one line.
		self.plugin.reportAboutTheDisplay(_("Table rows on one line"))

	def _currentColumn(self):
		""":return: the band and the table column the cursor is in, or (None, 0).

		The column the reader is in, not the one at the left of the display.
		"""
		band = self.plugin.flowBand
		if band is None or band.columnPlan() is None:
			return None, 0
		source = getattr(band.controller, "source", None)
		try:
			return band, int(getattr(source, "column", 0) or 0)
		except (TypeError, ValueError):
			return band, 0

	def _reportColumn(self, band, column: int, said: str) -> None:
		"""Report a change to one column, naming it the way the reader knows it.

		:param band: the band showing the table.
		:param column: the table's own column number.
		:param said: what happened to it.
		"""
		plan = band.columnPlan()
		found = next((item for item in plan.columns if item.index == column), None) if plan else None
		# The heading where the column has one, and "column 6" where it has not — the same
		# answer the paging report gives, since a bare number is not a name.
		name = (
			columnName(found)
			if found is not None
			# Translators: how a column is named when it is not one the layout is drawing. The
			# placeholder is which column of the table it is.
			else _("column {index}").format(index=column)
		)
		self.plugin.reportAboutTheDisplay(f"{name}: {said}")

	def toggleCurrentColumn(self) -> None:
		"""Take the column the cursor is in out of the layout, or put it back.

		For exploring a table without opening the designer.
		"""
		band, column = self._currentColumn()
		if band is None or not column:
			# Translators: reported when a command needs a table laid out in columns and there
			# is none on the display.
			ui.message(_("No table columns are showing"))
			return
		plan = band.columnPlan()
		showing = [item.index for item in plan.columns]
		if column in showing and len(showing) < 2:
			# Translators: reported when hiding a column would leave a table with none.
			ui.message(_("This is the only column showing"))
			return
		wanted = [index for index in showing if index != column] if column in showing else None
		if wanted is None:
			# Back again, in the table's own order, which is where the reader will expect it.
			wanted = sorted({*showing, column})
		if not band.showTheseColumns(wanted):
			return
		self._reportColumn(
			band,
			column,
			# Translators: reported when a column is taken out of a table's layout.
			_("hidden") if column not in wanted else _("showing"),
		)

	def cycleCurrentColumnTrim(self) -> None:
		"""Cycle one column between wrapping, cut at the start, and cut at the end.

		For a column whose useful text is at the end of each cell.
		"""
		from . import flowTable

		band, column = self._currentColumn()
		if band is None or not column:
			# Translators: reported when a command needs a table laid out in columns and there
			# is none on the display.
			ui.message(_("No table columns are showing"))
			return
		# From what the reader is feeling rather than from what they have said: a column they
		# have said nothing about is being drawn some way, and the next press should move on
		# from *that* rather than from the start of the list.
		plan = band.columnPlan()
		drawn = next((item for item in plan.columns if item.index == column), None) if plan else None
		order = (
			(flowTable.WRAP, "", _("wrapped")),
			(flowTable.TRUNCATE, flowTable.KEEP_START, _("cut, keeping the start")),
			(flowTable.TRUNCATE, flowTable.KEEP_END, _("cut, keeping the end")),
		)
		here = next(
			(
				position
				for position, (overflow, keep, _said) in enumerate(order)
				if drawn is not None and overflow == drawn.overflow and (not keep or keep == drawn.keep)
			),
			-1,
		)
		overflow, keep, said = order[(here + 1) % len(order)]
		if band.arrangeColumn(column, overflow=overflow, keep=keep):
			self._reportColumn(band, column, said)

	def resetLayout(self) -> None:
		"""Drop what has been arranged from the band, without touching what was saved.

		A saved layout comes back; `forgetLayout` removes it.
		"""
		band = self.plugin.flowBand
		if band is None or band.columnPlan() is None:
			# Translators: reported when a command needs a table laid out in columns and there
			# is none on the display.
			ui.message(_("No table columns are showing"))
			return
		band.arrangeTable(None)
		self.plugin.reportAboutTheDisplay(
			# Translators: reported when the columns a reader arranged are given up, so the
			# table is laid out the way it would be if they had arranged nothing.
			_("Reading this table as it comes"),
		)

	def openDesigner(self) -> None:
		"""Open the designer for the table on the display.

		For arrangements a keystroke cannot make, such as column names and widths. See
		`flowTableDesigner`. Only asks for the dialog: a modal dialog shown from a script,
		inside `queueHandler.pumpAll`, would hang NVDA. See `flowTableDesigner.arrangeTheTable`.
		"""
		from . import flowTableDesigner

		band = self.plugin.flowBand
		if band is None or band.columnPlan() is None:
			# Translators: reported when a command needs a table laid out in columns and there
			# is none on the display.
			ui.message(_("No table columns are showing"))
			return
		try:
			flowTableDesigner.arrangeTheTable(band)
		except Exception:
			log.debugWarning("Could not arrange the table", exc_info=True)

	def rememberLayout(self) -> None:
		"""Save the layout on the display against the table it is showing.

		Saves what the reader arranged, never the measured columns, so a measurement is not
		stored as a decision.
		"""
		from . import flowTableLayouts

		band = self.plugin.flowBand
		plan = band.columnPlan() if band is not None else None
		if plan is None:
			# Translators: reported when a command needs a table laid out in columns and there
			# is none on the display.
			ui.message(_("No table columns are showing"))
			return
		handle = self._tableHere()
		if handle is None:
			# Translators: reported when a command needs the cursor to be in a table.
			ui.message(_("Not in a table"))
			return
		arranged = band.tableLayoutInForce
		try:
			saved = flowTableLayouts.remember(handle, flowTableLayouts.layoutFrom(plan, handle, arranged))
		except flowTableLayouts.LayoutsNotSaved:
			ui.message(
				# Translators: reported when saving table layouts to the configuration failed.
				_("The table layout could not be saved, see the log"),
			)
			return
		if not saved:
			ui.message(
				# Translators: reported when a table's layout cannot be saved because nothing
				# about the table or its window is stable enough to recognise it again.
				_("This table cannot be recognised again, so its layout cannot be saved"),
			)
			return
		self.plugin.reportAboutTheDisplay(
			# Translators: reported when a table's layout is saved, so that the table comes up
			# in columns whenever the reader is in it.
			_("This table will display in columns"),
		)

	def forgetLayout(self) -> None:
		"""Drop the saved layout for this table, so it reads as the page around it again."""
		from . import flowTableLayouts

		handle = self._tableHere()
		if handle is None:
			# Translators: reported when a command needs the cursor to be in a table.
			ui.message(_("Not in a table"))
			return
		try:
			forgotten = flowTableLayouts.forget(handle)
		except flowTableLayouts.LayoutsNotSaved:
			# Translators: reported when deleting a saved table layout from the configuration failed.
			ui.message(_("The table layout could not be deleted, see the log"))
			return
		if not forgotten:
			# Translators: reported when a table has no saved layout to forget.
			ui.message(_("This table has no saved layout"))
			return
		band = self.plugin.flowBand
		if band is not None and band.tableWanted is not None:
			band.clearTable()
		self.plugin.tableRequest.wanted = None
		self.plugin.tableRequest.refused = None
		# Translators: reported when a table's saved layout is dropped.
		ui.message(_("Table layout deleted"))

	def manageLayouts(self) -> None:
		"""List every saved table layout, and fix the ones that stopped applying.

		Opened over the table the reader is in, if they are in one, so that a layout whose site changed
		its address can be pointed at this table with one button. Works outside a table too, for
		renaming and deleting. See `flowTableManager`.
		"""
		from . import flowTableManager

		try:
			flowTableManager.manageLayouts(self._tableHere(), afterwards=self._takeUpSavedLayouts)
		except Exception:
			log.debugWarning("Could not open the saved table layouts", exc_info=True)

	def _takeUpSavedLayouts(self) -> None:
		"""Show the table the reader is in the way the saved layouts now say, after the manager changed them.

		See `FlowBand.takeUpSavedLayouts`.
		"""
		band = self.plugin.flowBand
		if band is not None:
			band.takeUpSavedLayouts()

	def _tableHere(self):
		""":return: the table the reader is in, or None, without disturbing anything.

		From the navigator object, as the column command uses.
		"""
		from .flowTableSource import logExplanation, tableAt

		obj = api.getNavigatorObject()
		try:
			handle = tableAt(obj)
		except Exception:
			log.debugWarning("Could not look for the table the reader is in", exc_info=True)
			return None
		if handle is None:
			logExplanation(obj, "asked about the table the reader is in and found none")
		return handle

	def tableToPage(self, gesture) -> PagedTable | None:
		""":return: which table a paging command should move, or None if there is none.

		The table on the display whose key was pressed, as the panning keys do; failing that,
		the first table in display order.

		:param gesture: the gesture that ran the command, if there was one.
		"""
		showing = self.plugin.tablesInColumns()
		if not showing:
			return None
		container = self.plugin.container
		order = [segment.key for segment in container.segments] if container is not None else []
		pressed = self.plugin.segmentKeysOn(keyLayerDispatch.displayFor(gesture))
		for key in [*pressed, *order]:
			if key in showing:
				return showing[key]
		return next(iter(showing.values()))

	def turnColumnPage(self, by: int, gesture=None) -> None:
		"""Move a table by pages of columns, and say where it landed.

		Said, because the display cannot show which page of columns it is on.

		:param by: how many pages to move, negative for back.
		:param gesture: the gesture that ran the command, which says which display's table is
			meant. See `tableToPage`.
		"""
		band = self.tableToPage(gesture)
		plan = band.columnPlan() if band is not None else None
		if plan is None:
			# Translators: reported when a command needs a table laid out in columns and there
			# is none on the display.
			ui.message(_("No table columns are showing"))
			return
		if plan.showsEverything:
			# Translators: reported when a table's columns all fit on the display already, so
			# there is nowhere to move to.
			ui.message(_("The whole table is showing"))
			return
		if not band.turnColumnPage(by):
			first, last, _total = plan.whereItIs
			self.plugin.reportAboutTheDisplay(
				# Translators: reported when a command would move past the first or last
				# columns of a table. Placeholders are the first and last columns showing.
				_("Columns {first} to {last}, no further").format(first=first, last=last),
			)
			return
		now = band.columnPlan()
		labels = ", ".join(columnName(place.column) for place in now.placements())
		first, last, total = now.whereItIs
		self.plugin.reportAboutTheDisplay(
			# Translators: reported after moving across a table's columns. Placeholders are,
			# in order, the first and last of the columns now showing, how many columns there
			# are in all, and the names of the ones on the display.
			_("Columns {first} to {last} of {total}: {columns}").format(
				first=first,
				last=last,
				total=total,
				columns=labels,
			),
		)
