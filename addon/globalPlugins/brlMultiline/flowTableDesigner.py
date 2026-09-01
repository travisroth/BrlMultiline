# BrlMultiline: arranging a table's columns.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Where a reader says how one table is to be read, and the dialog that asks them.

The commands on the band answer the questions worth answering with one keystroke while
reading — hide this column, cut it from the other end. This is the other half: a table the
reader comes back to every day, arranged once and remembered, with the things a keystroke
cannot say. What to *call* a column whose heading is six lines of somebody's help text. How
much room the description may have. Which column is repeated at the left of every page. Where
a page begins.

**The arrangement is a plain object and the dialog is a shell over it.** `Arrangement` knows
nothing about wx: it holds the columns in order, what the reader has decided about each, and
turns that into a `flowTableLayouts.TableLayout`. Everything that decides anything is
therefore testable without a display, which is the same split `flowTable` makes against
`flowRender` — and the dialog is left holding only the parts that are genuinely about
widgets.
"""

from typing import Optional

from logHandler import log

from . import flowTable, flowTableLayouts

try:
	import wx
	import gui
	from gui import guiHelper
except ImportError:  # pragma: no cover - only in a test run with no NVDA around it.
	wx = None
	gui = None
	guiHelper = None

WRAPPED = "wrapped"
CUT_START = "cutStart"
CUT_END = "cutEnd"

CUTTING: tuple[tuple[str, str], ...] = (
	# Translators: how a column too wide for its place is drawn: the value continues on the
	# next row of the same table row.
	(WRAPPED, _("Wrapped over more rows")),
	# Translators: how a column too wide for its place is drawn: the start of the value is
	# shown and the rest is not.
	(CUT_START, _("Cut, keeping the start")),
	# Translators: how a column too wide for its place is drawn: the end of the value is
	# shown and what comes before it is not.
	(CUT_END, _("Cut, keeping the end")),
)
"""What a column does with a cell too long for it, in the words the dialog offers.

Three, rather than two settings that interact: "cut or wrapped" and "which end" are one
question to a reader looking at a column, and the second only means anything under the first.
See `flowTable.OVERFLOW_STYLES` and `flowTable.KEEP_ENDS`, which are what these become.
"""

HEADER_ENDS: tuple[tuple[str, str], ...] = (
	# Translators: which end of a column's heading is shown when it does not fit.
	(flowTable.KEEP_START, _("Keep the start")),
	# Translators: which end of a column's heading is shown when it does not fit.
	(flowTable.KEEP_END, _("Keep the end")),
)
"""Which end of a *heading* survives. Asked separately because they go wrong separately."""

NO_LIMIT = 0
"""What a width control holds when the reader has set no bound of their own."""

MAX_SETTABLE_WIDTH = 80
"""The widest a reader may pin a column to, which is a display's own width and then some."""


class Column:
	"""One column of a table, as the reader is arranging it."""

	def __init__(self, index: int, name: str, shown: bool, choice) -> None:
		"""
		:param index: the table's own column number.
		:param name: what to call it in the list — the reader's own name where they have given
			one, else the table's heading, else its number.
		:param shown: whether it is drawn.
		:param choice: what they have decided about it, as `flowTable.ColumnChoice`.
		"""
		self.index = index
		self.name = name
		self.shown = shown
		self.choice = choice

	@property
	def cutting(self) -> str:
		""":return: how this column handles a cell too long for it, in the dialog's words."""
		if self.choice.overflow == flowTable.TRUNCATE:
			return CUT_END if self.choice.keep == flowTable.KEEP_END else CUT_START
		if self.choice.overflow == flowTable.WRAP:
			return WRAPPED
		return ""

	def describe(self) -> str:
		""":return: the whole of what has been decided about this column, as one line.

		The line a reader arrows onto in the list. Everything they have said about the column
		is in it, because a list that says only the name means opening every column in turn to
		find the one they changed.
		"""
		said = [self.name]
		if not self.shown:
			# Translators: said of a column of a table that the reader's layout leaves out.
			said.append(_("hidden"))
		for value, label in CUTTING:
			if value == self.cutting:
				said.append(label.lower())
		if self.choice.label:
			# Translators: said of a table column the reader has given a name of their own.
			said.append(_("called {name}").format(name=self.choice.label))
		if self.choice.minWidth:
			# Translators: said of a table column with a width floor. The placeholder is cells.
			said.append(_("at least {cells} cells").format(cells=self.choice.minWidth))
		if self.choice.maxWidth:
			# Translators: said of a table column with a width ceiling. The placeholder is cells.
			said.append(_("at most {cells} cells").format(cells=self.choice.maxWidth))
		if self.choice.startsAPage:
			# Translators: said of a table column where the reader asked a page to begin.
			said.append(_("starts a page"))
		return ", ".join(said)


class Arrangement:
	"""Every column of one table, in the order they are drawn, and what was decided about them.

	Built from the plan on the display and the layout in force, so what the reader opens is
	what they are feeling: the columns they can see, in the order they see them, followed by
	the ones their layout leaves out. Answers a `TableLayout`, which is the only thing that
	leaves here.
	"""

	def __init__(self, columns, keyColumn: int = 0, remembered: bool = False) -> None:
		"""
		:param columns: the columns, in order, as `Column`.
		:param keyColumn: which is repeated at the left of every later page, or 0 for the first
			one drawn.
		:param remembered: whether this table already has a saved layout, which is what the
			dialog offers to keep it in.
		"""
		self.columns = list(columns)
		self.keyColumn = keyColumn
		self.remembered = remembered

	@classmethod
	def of(cls, plan, layout=None, remembered: bool = False) -> "Arrangement":
		"""Build the arrangement of a table on the display.

		:param plan: the column plan being drawn.
		:param layout: the layout in force, or None where the table reads as it comes.
		:param remembered: whether the table has a saved layout.
		:return: the arrangement.
		"""
		layout = layout if layout is not None else flowTableLayouts.TableLayout()
		choices = dict(layout.perColumn or {})
		drawn = [column.index for column in getattr(plan, "columns", ()) or ()]
		names = {column.index: column.label for column in getattr(plan, "columns", ()) or ()}
		hidden = [index for index in getattr(plan, "excluded", ()) or () if index not in drawn]
		ordered = [*drawn, *sorted(hidden)]
		return cls(
			[
				Column(
					index=index,
					name=_nameOf(index, names.get(index, "")),
					shown=index in drawn,
					choice=choices.get(index, flowTable.ColumnChoice()),
				)
				for index in ordered
			],
			keyColumn=layout.keyColumn,
			remembered=remembered,
		)

	def at(self, position: int) -> Optional[Column]:
		""":return: the column at one place in the list, or None if there is none there."""
		if 0 <= position < len(self.columns):
			return self.columns[position]
		return None

	def move(self, position: int, by: int) -> int:
		"""Move one column up or down the drawing order.

		:param position: where it is now.
		:param by: -1 for earlier, 1 for later.
		:return: where it ended up, which is where it started if it could not move.
		"""
		target = position + by
		if not (0 <= position < len(self.columns) and 0 <= target < len(self.columns)):
			return position
		self.columns[position], self.columns[target] = self.columns[target], self.columns[position]
		return target

	def toggle(self, position: int) -> bool:
		"""Show or hide one column.

		:param position: which column.
		:return: whether it is shown afterwards.
		"""
		column = self.at(position)
		if column is None:
			return False
		if column.shown and len([item for item in self.columns if item.shown]) < 2:
			# A table of no columns is not a layout, it is a blank display.
			return True
		column.shown = not column.shown
		return column.shown

	def decide(self, position: int, **changes) -> None:
		"""Record what the reader said about one column.

		:param position: which column.
		:param changes: the fields of `flowTable.ColumnChoice` to set.
		"""
		import dataclasses

		column = self.at(position)
		if column is None:
			return
		column.choice = dataclasses.replace(column.choice, **changes)

	def setCutting(self, position: int, cutting: str) -> None:
		"""Say how one column handles a cell too long for it.

		:param position: which column.
		:param cutting: one of `CUTTING`.
		"""
		if cutting == WRAPPED:
			self.decide(position, overflow=flowTable.WRAP, keep="")
		elif cutting == CUT_START:
			self.decide(position, overflow=flowTable.TRUNCATE, keep=flowTable.KEEP_START)
		elif cutting == CUT_END:
			self.decide(position, overflow=flowTable.TRUNCATE, keep=flowTable.KEEP_END)
		else:
			self.decide(position, overflow="", keep="")

	def asLayout(self) -> "flowTableLayouts.TableLayout":
		""":return: what the reader arranged, as the record that carries it.

		Only what they decided. A column they said nothing about is not written down, and the
		order is only written down when it differs from the table's own — so a reader who
		opened the dialog, looked, and pressed OK has changed nothing about how the table is
		read, which is what pressing OK on a dialog they did not touch should mean.
		"""
		shown = [column.index for column in self.columns if column.shown]
		everything = sorted(column.index for column in self.columns)
		columns = () if shown == everything else tuple(shown)
		chosen = {
			column.index: column.choice
			for column in self.columns
			if not column.choice.isEmpty
		}
		return flowTableLayouts.TableLayout(
			columns=columns,
			keyColumn=self.keyColumn,
			perColumn=chosen,
		)


def _nameOf(index: int, label: str) -> str:
	""":return: what to call one column in the list.

	:param index: the table's own column number.
	:param label: the heading it is drawn with, where it has one.
	"""
	if label:
		return label
	# Translators: how a column with no heading of its own is named. The placeholder is which
	# column of the table it is.
	return _("column {index}").format(index=index)


def arrangeTheTable(band) -> bool:
	"""Put the designer up for the table on the display, and apply what comes back.

	:param band: the flow band showing the table.
	:return: whether anything was changed.
	"""
	if not CAN_DRAW:
		log.debugWarning("There is no wx to open the table designer with")
		return False
	plan = band.columnPlan()
	if plan is None:
		return False
	handle = band._tableOnTheBand()
	remembered = flowTableLayouts.layoutFor(handle) is not None if handle is not None else False
	arrangement = Arrangement.of(plan, band.tableLayoutInForce, remembered=remembered)
	dialog = TableDesignerDialog(gui.mainFrame, arrangement)
	try:
		if dialog.ShowModal() != wx.ID_OK:
			return False
		layout = arrangement.asLayout()
		band.arrangeTable(layout if not layout.isEmpty else None)
		if dialog.keepIt and handle is not None:
			flowTableLayouts.remember(handle, layout)
		return True
	finally:
		dialog.Destroy()


CAN_DRAW = wx is not None and gui is not None and hasattr(wx, "Dialog")
"""Whether there is enough wx here to build a dialog with.

Asked of `wx.Dialog` itself rather than of the import, because a test run has a stand-in for
`wx` that answers the handful of names the add-on uses and has no windows in it at all."""


if CAN_DRAW:  # pragma: no cover - a dialog needs a display.

	class TableDesignerDialog(wx.Dialog):
		"""The columns of one table, in a list, with what has been decided about each.

		**The list is the dialog.** Every column is one line that says everything decided
		about it, so a reader arrows down it and hears the whole arrangement rather than
		opening each column in turn to find the one they changed. The controls beside it act
		on whichever line they are on.
		"""

		def __init__(self, parent, arrangement: Arrangement) -> None:
			# Translators: the title of the dialog for arranging a table's columns.
			super().__init__(parent, title=_("Arrange this table"))
			self.arrangement = arrangement
			self.keepIt = arrangement.remembered
			main = wx.BoxSizer(wx.VERTICAL)
			helper = guiHelper.BoxSizerHelper(self, sizer=main)
			# Translators: the label of the list of a table's columns.
			self.columnList = helper.addLabeledControl(_("&Columns:"), wx.ListBox, choices=[])
			self.columnList.Bind(wx.EVT_LISTBOX, self._onColumn)
			buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
			# Translators: a button that moves a column earlier in the reading order.
			self.upButton = buttons.addButton(self, label=_("Move &up"))
			# Translators: a button that moves a column later in the reading order.
			self.downButton = buttons.addButton(self, label=_("Move &down"))
			# Translators: a button that shows or hides the selected column.
			self.showButton = buttons.addButton(self, label=_("&Show or hide"))
			self.upButton.Bind(wx.EVT_BUTTON, lambda event: self._move(-1))
			self.downButton.Bind(wx.EVT_BUTTON, lambda event: self._move(1))
			self.showButton.Bind(wx.EVT_BUTTON, self._onToggle)
			helper.addItem(buttons)
			# Translators: the label of a field for the reader's own name for a column.
			self.labelCtrl = helper.addLabeledControl(_("Call this column:"), wx.TextCtrl)
			self.labelCtrl.Bind(wx.EVT_KILL_FOCUS, self._onLabel)
			self.cuttingCtrl = helper.addLabeledControl(
				# Translators: the label of a choice of what a column does with a long value.
				_("When a value does not fit:"),
				wx.Choice,
				choices=[label for _value, label in CUTTING],
			)
			self.cuttingCtrl.Bind(wx.EVT_CHOICE, self._onCutting)
			self.headerCtrl = helper.addLabeledControl(
				# Translators: the label of a choice of which end of a heading is shown.
				_("When the heading does not fit:"),
				wx.Choice,
				choices=[label for _value, label in HEADER_ENDS],
			)
			self.headerCtrl.Bind(wx.EVT_CHOICE, self._onHeader)
			self.minCtrl = helper.addLabeledControl(
				# Translators: the label of a field for the fewest cells a column may have.
				_("At least this many cells (0 for no limit):"),
				wx.SpinCtrl,
				min=NO_LIMIT,
				max=MAX_SETTABLE_WIDTH,
			)
			self.minCtrl.Bind(wx.EVT_SPINCTRL, self._onWidths)
			self.maxCtrl = helper.addLabeledControl(
				# Translators: the label of a field for the most cells a column may have.
				_("At most this many cells (0 for no limit):"),
				wx.SpinCtrl,
				min=NO_LIMIT,
				max=MAX_SETTABLE_WIDTH,
			)
			self.maxCtrl.Bind(wx.EVT_SPINCTRL, self._onWidths)
			# Translators: a checkbox for beginning a page of columns at the selected column.
			self.pageCtrl = helper.addItem(wx.CheckBox(self, label=_("Start a &page here")))
			self.pageCtrl.Bind(wx.EVT_CHECKBOX, self._onPage)
			self.keyCtrl = helper.addItem(
				# Translators: a checkbox for repeating the selected column on every page.
				wx.CheckBox(self, label=_("&Repeat this column on every page")),
			)
			self.keyCtrl.Bind(wx.EVT_CHECKBOX, self._onKey)
			self.keepCtrl = helper.addItem(
				# Translators: a checkbox for saving the arrangement against this table.
				wx.CheckBox(self, label=_("Remember this arrangement for this &table")),
			)
			self.keepCtrl.SetValue(self.keepIt)
			self.keepCtrl.Bind(wx.EVT_CHECKBOX, self._onKeep)
			helper.addDialogDismissButtons(wx.OK | wx.CANCEL)
			self.SetSizerAndFit(main)
			self._fillList(0)
			self.columnList.SetFocus()

		@property
		def _position(self) -> int:
			return max(0, self.columnList.GetSelection())

		def _fillList(self, select: int) -> None:
			"""Draw the list again and put the selection back where it was."""
			self.columnList.Set([column.describe() for column in self.arrangement.columns])
			if self.arrangement.columns:
				self.columnList.SetSelection(min(select, len(self.arrangement.columns) - 1))
			self._showColumn()

		def _redrawLine(self) -> None:
			"""Draw the line for the column in hand again, without moving the selection."""
			position = self._position
			column = self.arrangement.at(position)
			if column is not None:
				self.columnList.SetString(position, column.describe())

		def _showColumn(self) -> None:
			"""Put the controls where the column in hand has them."""
			column = self.arrangement.at(self._position)
			if column is None:
				return
			self.labelCtrl.SetValue(column.choice.label)
			self.cuttingCtrl.SetSelection(
				next(
					(index for index, (value, _label) in enumerate(CUTTING) if value == column.cutting),
					0,
				),
			)
			self.headerCtrl.SetSelection(
				1 if column.choice.headerKeep == flowTable.KEEP_END else 0,
			)
			self.minCtrl.SetValue(column.choice.minWidth)
			self.maxCtrl.SetValue(column.choice.maxWidth)
			self.pageCtrl.SetValue(column.choice.startsAPage)
			self.keyCtrl.SetValue(self.arrangement.keyColumn == column.index)

		def _onColumn(self, event) -> None:
			self._showColumn()

		def _move(self, by: int) -> None:
			self._fillList(self.arrangement.move(self._position, by))

		def _onToggle(self, event) -> None:
			self.arrangement.toggle(self._position)
			self._redrawLine()

		def _onLabel(self, event) -> None:
			self.arrangement.decide(self._position, label=self.labelCtrl.GetValue().strip())
			self._redrawLine()
			event.Skip()

		def _onCutting(self, event) -> None:
			self.arrangement.setCutting(self._position, CUTTING[self.cuttingCtrl.GetSelection()][0])
			self._redrawLine()

		def _onHeader(self, event) -> None:
			self.arrangement.decide(
				self._position,
				headerKeep=HEADER_ENDS[self.headerCtrl.GetSelection()][0],
			)

		def _onWidths(self, event) -> None:
			self.arrangement.decide(
				self._position,
				minWidth=self.minCtrl.GetValue(),
				maxWidth=self.maxCtrl.GetValue(),
			)
			self._redrawLine()

		def _onPage(self, event) -> None:
			self.arrangement.decide(self._position, startsAPage=self.pageCtrl.GetValue())
			self._redrawLine()

		def _onKey(self, event) -> None:
			column = self.arrangement.at(self._position)
			if column is None:
				return
			self.arrangement.keyColumn = column.index if self.keyCtrl.GetValue() else 0

		def _onKeep(self, event) -> None:
			self.keepIt = self.keepCtrl.GetValue()

else:

	class TableDesignerDialog:  # type: ignore[no-redef]
		"""What the designer is where there is no wx to build one with."""

		def __init__(self, parent, arrangement: Arrangement) -> None:
			raise RuntimeError("The table designer needs wx")


__all__ = ["Arrangement", "Column", "TableDesignerDialog", "arrangeTheTable"]
