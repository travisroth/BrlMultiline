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

FOLLOW = ""
"""What a per-column control holds when the reader has decided nothing about that column.

Not the same as a decision that happens to match the table: a column following the table
changes when the table's own setting changes, and a column that was set does not. The
control says which of the two it is, and names what following currently means — a review
found the cutting control reading "Wrapped" on an untouched column of a table that was
cutting, which is the dialog describing something other than what is on the display.
"""

WRAPPED = "wrapped"
CUT_START = "cutStart"
CUT_END = "cutEnd"

CUTTING: tuple[tuple[str, str], ...] = (
	# Translators: a column of a table is drawn however the table itself is set to draw them.
	(FOLLOW, _("Follow the table setting")),
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

Three answers rather than two settings that interact: "cut or wrapped" and "which end" are
one question to a reader looking at a column, and the second only means anything under the
first. See `flowTable.OVERFLOW_STYLES` and `flowTable.KEEP_ENDS`, which are what these
become — and `FOLLOW`, which is the fourth and the one every column starts on.
"""

HEADER_ENDS: tuple[tuple[str, str], ...] = (
	# Translators: a column's heading is drawn however the table itself is set to draw them.
	(FOLLOW, _("Follow the table setting")),
	# Translators: which end of a column's heading is shown when it does not fit.
	(flowTable.KEEP_START, _("Keep the start")),
	# Translators: which end of a column's heading is shown when it does not fit.
	(flowTable.KEEP_END, _("Keep the end")),
)
"""Which end of a *heading* survives. Asked separately because they go wrong separately."""

# Translators: the option for repeating whichever column is drawn first on every page.
KEY_IS_THE_FIRST = _("The first column shown")
"""What the repeated-column control holds when the reader has named no column of their own.

Zero in the record, and the first drawn column on the display. A checkbox on each column
could not say that: the reader saw nothing checked and the display was repeating one.
"""

NO_LIMIT = 0
"""What a width control holds when the reader has set no bound of their own."""

MAX_SETTABLE_WIDTH = 80
"""The widest a reader may pin a column to, which is a display's own width and then some."""


class Column:
	"""One column of a table, as the reader is arranging it."""

	def __init__(self, index: int, name: str, shown: bool, choice, blank: bool = False) -> None:
		"""
		:param index: the table's own column number.
		:param name: what to call it in the list — the reader's own name where they have given
			one, else the table's heading, else its number.
		:param shown: whether it is drawn.
		:param choice: what they have decided about it, as `flowTable.ColumnChoice`.
		:param blank: whether the measurement found nothing in it, which is why it is not on
			the display although nothing hid it. See `flowTable.ColumnPlan.omitted`.
		"""
		self.index = index
		self.name = name
		self.shown = shown
		self.choice = choice
		self.blank = blank

	@property
	def cutting(self) -> str:
		""":return: how this column handles a cell too long for it, in the dialog's words."""
		if self.choice.overflow == flowTable.TRUNCATE:
			return CUT_END if self.choice.keep == flowTable.KEEP_END else CUT_START
		if self.choice.overflow == flowTable.WRAP:
			return WRAPPED
		return ""

	@property
	def headerEnd(self) -> str:
		""":return: which end of this column's heading survives, in the dialog's words."""
		return self.choice.headerKeep if self.choice.headerKeep in flowTable.KEEP_ENDS else FOLLOW

	def describe(self, repeated: bool = False) -> str:
		""":return: the whole of what has been decided about this column, as one line.

		The line a reader arrows onto in the list. Everything they have said about the column
		is in it, because a list that says only the name means opening every column in turn to
		find the one they changed. A review found two things missing from "everything": which
		end of the heading survives, and whether this is the column repeated on every page.

		**Except whether it is shown**, which is the checkbox on the line and is announced with
		it. Saying it here as well would have the reader told "hidden" twice on every line they
		arrow onto.

		:param repeated: whether this is the column drawn again on every page after the first.
		"""
		said = [self.name]
		if self.shown and self.blank:
			# Translators: said of a table column the measurement found nothing in.
			said.append(_("empty here"))
		for value, label in CUTTING:
			if value and value == self.cutting:
				said.append(label.lower())
		if self.headerEnd == flowTable.KEEP_END:
			# Translators: said of a table column whose heading is cut from the front.
			said.append(_("heading keeps its end"))
		if repeated:
			# Translators: said of the table column drawn again at the left of every page.
			said.append(_("repeated on every page"))
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
		if self.choice.plainCase:
			# Translators: said of a table column drawn without braille capital signs.
			said.append(_("no capital signs"))
		return ", ".join(said)


class Arrangement:
	"""Every column of one table, in the order they are drawn, and what was decided about them.

	Built from the plan on the display and the layout in force, so what the reader opens is
	what they are feeling: the columns they can see, in the order they see them, followed by
	the ones their layout leaves out. Answers a `TableLayout`, which is the only thing that
	leaves here.
	"""

	def __init__(
		self,
		columns,
		keyColumn: int = 0,
		remembered: bool = False,
		base=None,
		following: str = WRAPPED,
		repeats: bool = True,
	) -> None:
		"""
		:param columns: the columns, in order, as `Column`.
		:param keyColumn: which is repeated at the left of every later page, or 0 for the first
			one drawn.
		:param remembered: whether this table already has a saved layout, which is what the
			dialog offers to keep it in.
		:param base: the layout being read, which everything not edited here is kept from.
		:param following: what the table itself does with a cell too long for its column.
		:param repeats: whether this table repeats a column on its later pages at all, which
			is a setting rather than a column and can be off.
		"""
		self.columns = list(columns)
		self.keyColumn = keyColumn
		self.remembered = remembered
		self.base = base if base is not None else flowTableLayouts.TableLayout()
		self.following = following
		self.repeats = repeats

	@classmethod
	def of(
		cls,
		plan,
		layout=None,
		remembered: bool = False,
		following: str = WRAPPED,
		headings=None,
	) -> "Arrangement":
		"""Build the arrangement of a table on the display.

		:param plan: the column plan being drawn.
		:param layout: the layout being read — what the reader arranged if they arranged
			anything, and otherwise what was saved for this table. Not merely the transient
			one: a remembered table normally has nothing arranged, and opening the designer
			over an empty layout showed none of what was saved and then offered to save that
			emptiness over it.
		:param remembered: whether the table has a saved layout.
		:param following: what the table itself does with a cell too long for its column,
			which is what a column set to `FOLLOW` is doing.
		:param headings: what the table calls columns the plan does not draw, by column
			number. **A hidden column has no place in the plan, so the plan cannot say what it
			is called** — and it came back as "column 19", which is not a name and is no use to
			a reader deciding whether to show it again. See `arrangeTheTable`, which asks the
			table.
		:return: the arrangement.
		"""
		layout = layout if layout is not None else flowTableLayouts.TableLayout()
		choices = dict(layout.perColumn or {})
		drawn = [column.index for column in getattr(plan, "columns", ()) or ()]
		names = dict(headings or {})
		names.update({column.index: column.label for column in getattr(plan, "columns", ()) or ()})
		# Measured and not drawn, which is not the same as hidden and must not become it. A
		# column that was blank in the rows sampled is still a column of the table, and
		# leaving it out here meant that hiding any *other* column wrote a list of survivors
		# that did not have it in — freezing a column out of every later reading over a
		# decision the reader never made.
		blank = [index for index in getattr(plan, "omitted", ()) or () if index not in drawn]
		hidden = [index for index in getattr(plan, "excluded", ()) or () if index not in drawn]
		ordered = [*drawn, *sorted(blank), *sorted(index for index in hidden if index not in blank)]
		return cls(
			[
				Column(
					index=index,
					# Their own name for it first, which needs nothing looked up and is the
					# answer for a hidden column they had named. Then what the table calls it,
					# then its number.
					name=_nameOf(
						index,
						choices.get(index, flowTable.ColumnChoice()).label or names.get(index, ""),
					),
					shown=index not in hidden,
					choice=choices.get(index, flowTable.ColumnChoice()),
					blank=index in blank,
				)
				for index in ordered
			],
			keyColumn=layout.keyColumn,
			remembered=remembered,
			base=layout,
			following=following,
			repeats=getattr(plan, "keyColumn", None) is not None,
		)

	def at(self, position: int) -> Optional[Column]:
		""":return: the column at one place in the list, or None if there is none there."""
		if 0 <= position < len(self.columns):
			return self.columns[position]
		return None

	@property
	def repeatedColumn(self) -> int:
		""":return: the table's number for the column drawn again on every page, or 0.

		The reader's own where they named one, and otherwise the first column that is drawn —
		which is what zero means on the display and what nothing at all meant in the dialog.
		Zero for a table that repeats no column, because the setting can be off and then there
		is no such column to name.
		"""
		if self.keyColumn:
			return self.keyColumn
		if not self.repeats:
			return 0
		return next((column.index for column in self.columns if column.shown), 0)

	def describe(self, position: int) -> str:
		""":return: the line for one column of the list, or "" if there is none there.

		Here rather than on `Column`, because whether a column is the repeated one is a fact
		about the table and not about the column.
		"""
		column = self.at(position)
		if column is None:
			return ""
		return column.describe(repeated=column.index == self.repeatedColumn)

	def complaint(self) -> str:
		""":return: what is wrong with this arrangement, or "" if nothing is.

		Only contradictions, and there is one to have: a floor above the ceiling is two
		requests that cannot both be met, and the dialog closing on it would leave the reader
		with a column drawn to neither. Said rather than silently normalised, because which of
		the two numbers they meant is not ours to guess.
		"""
		for column in self.columns:
			low, high = column.choice.minWidth, column.choice.maxWidth
			if low and high and low > high:
				# Translators: reported when a table column is given a width floor above its
				# ceiling. The placeholders are the column's name and the two widths.
				return _(
					"{name} is set to at least {low} cells and at most {high}, "
					"and both cannot be met.",
				).format(name=column.name, low=low, high=high)
		return ""

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

	def setShown(self, position: int, shown: bool) -> bool:
		"""Say whether one column is drawn.

		:param position: which column.
		:param shown: whether the reader wants it drawn.
		:return: whether it is shown afterwards, which is not what was asked for when the
			last showing column was the one being taken away.
		"""
		column = self.at(position)
		if column is None:
			return False
		if not shown and len([item for item in self.columns if item.shown]) < 2:
			# A table of no columns is not a layout, it is a blank display.
			return True
		column.shown = shown
		return column.shown

	def toggle(self, position: int) -> bool:
		"""Show a hidden column, or hide a shown one.

		:param position: which column.
		:return: whether it is shown afterwards.
		"""
		column = self.at(position)
		if column is None:
			return False
		return self.setShown(position, not column.shown)

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

		Only what they decided, **laid over what the table was already being read with**. A
		column they said nothing about is not written down, and the order is only written
		down when it differs from the table's own — so a reader who opened the dialog, looked,
		and pressed OK has changed nothing about how the table is read, which is what pressing
		OK on a dialog they did not touch should mean.

		The three fields the dialog does not offer — the row height, the table-wide cutting,
		and whether headers are held above the band — come back out untouched rather than
		reset, because a dialog that does not ask about something must not decide it.
		"""
		import dataclasses

		shown = [column.index for column in self.columns if column.shown]
		everything = sorted(column.index for column in self.columns)
		columns = () if shown == everything else tuple(shown)
		chosen = {
			column.index: column.choice
			for column in self.columns
			if not column.choice.isEmpty
		}
		return dataclasses.replace(
			self.base,
			columns=columns,
			keyColumn=self.keyColumn,
			perColumn=chosen,
		)


def _indexOf(offered, value: str) -> int:
	""":return: where one value sits in a choice's options, or 0 for the following one.

	:param offered: pairs of value and label, `FOLLOW` first.
	:param value: what the column holds.
	"""
	return next(
		(position for position, (held, _label) in enumerate(offered) if held == value),
		0,
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

	**The dialog is opened from the queue and not from the script.** A script runs inside
	`queueHandler.pumpAll`, and a modal dialog shown there never gives the pump back: NVDA's
	core stops turning, the watchdog reports it frozen every fifteen seconds, and the reader
	has to end the process. This is what happened on hardware the first time the designer was
	opened. So the script only asks for the dialog and returns; `wx.CallAfter` runs the rest
	on the next turn of the event loop, outside the pump, which is what NVDA's own commands do
	for every settings dialog they open and what `gui.runScriptModalDialog` was for.

	What the reader is arranging is read *here*, while the table is certainly still on the
	display, and carried to the dialog. Only the showing is put off.

	:param band: the flow band showing the table.
	:return: whether the designer is opening.
	"""
	if not CAN_DRAW:
		log.debugWarning("There is no wx to open the table designer with")
		return False
	plan = band.columnPlan()
	if plan is None:
		return False
	handle = band._tableOnTheBand()
	saved = flowTableLayouts.layoutFor(handle) if handle is not None else None
	# **What the table is actually being read with**, which is what the reader arranged if
	# they arranged anything and what they saved for it otherwise. Handing over only the
	# transient arrangement opened a remembered table on an empty dialog — none of the saved
	# decisions in it — with "remember this" already ticked, so pressing OK wrote that
	# emptiness over the record they came back for.
	arrangement = Arrangement.of(
		plan,
		band.tableLayoutInForce or saved,
		remembered=saved is not None,
		following=_tableCutting(band.tableLayoutInForce or saved),
		headings=_whatTheTableCallsTheRest(handle, plan),
	)
	wx.CallAfter(_askThenApply, band, arrangement, handle)
	return True


def _whatTheTableCallsTheRest(handle, plan) -> dict:
	""":return: the headings of the columns the plan does not draw, by column number.

	A column the layout leaves out is not in the plan, so nothing on the display knows what it
	is called — and the dialog showed it as "column 19", which is a position rather than a
	name and tells a reader nothing about whether to show it again. So the table is asked,
	here and once: the dialog is opened by a keystroke and this is a few cells read at the
	reader's own row, against the alternative of measuring the whole table again.

	:param handle: the table, or None where nothing recognises one.
	:param plan: the column plan being drawn.
	"""
	from . import flowTableSource

	drawn = {column.index for column in getattr(plan, "columns", ()) or ()}
	rest = [
		index
		for index in (*(getattr(plan, "omitted", ()) or ()), *(getattr(plan, "excluded", ()) or ()))
		if index not in drawn
	]
	if handle is None or not rest:
		return {}
	try:
		return flowTableSource.declaredHeaders(handle, sorted(set(rest)))
	except Exception:
		log.debugWarning("Could not ask a table what its hidden columns are called", exc_info=True)
		return {}


def _askThenApply(band, arrangement: "Arrangement", handle) -> None:
	"""Show the designer, and do what the reader decided in it.

	On the event loop rather than in the script that asked for it. See `arrangeTheTable`.

	:param band: the flow band.
	:param arrangement: what to open the dialog over.
	:param handle: the table it was read from.
	"""
	try:
		# NVDA keeps the previous focus and restores the foreground around a popup, and only
		# knows to if it is told. See `gui.mainFrame.prePopup`.
		gui.mainFrame.prePopup()
		try:
			dialog = TableDesignerDialog(gui.mainFrame, arrangement)
			try:
				if dialog.ShowModal() != wx.ID_OK:
					return
				keepIt = dialog.keepIt
			finally:
				dialog.Destroy()
		finally:
			gui.mainFrame.postPopup()
		layout = arrangement.asLayout()
		# The band only if it is still reading the table this was arranged for. The dialog is
		# open for as long as the reader wants it, and applying a watchlist's columns to
		# whatever they walked into meanwhile would be the same wrongness the layout in force
		# is keyed against. What was saved is saved against `handle` either way, which is the
		# right table wherever the reader is now.
		if _stillThere(band, handle):
			band.arrangeTable(layout if not layout.isEmpty else None)
		_keepOrDrop(handle, layout, arrangement.remembered, keepIt)
	except Exception:
		log.debugWarning("Could not arrange the table", exc_info=True)


def _stillThere(band, handle) -> bool:
	""":return: whether the band is still reading the table the designer was opened over.

	:param band: the flow band.
	:param handle: the table the arrangement was read from.
	"""
	from . import flowTableSource

	if handle is None:
		return False
	here = band._tableOnTheBand()
	return here is not None and flowTableSource.sameTable(here.key, handle.key)


def _tableCutting(layout) -> str:
	""":return: what this table does with a cell too long for its column, in the dialog's words.

	What a column set to `FOLLOW` is following, so the control can name it rather than leave
	the reader to guess which of the four they are on.

	:param layout: the layout the table is being read with, or None.
	"""
	from . import bmConfig

	saying = layout if layout is not None else flowTableLayouts.TableLayout()
	return CUT_START if saying.truncateOr(bmConfig.shouldTruncateTableCells()) else WRAPPED


def _keepOrDrop(handle, layout, remembered: bool, keepIt: bool) -> None:
	"""Save the arrangement against this table, or drop what was saved for it.

	The checkbox is a state and not a button: ticking it saves, and *unticking* one that was
	ticked means the reader does not want this table remembered any more. Skipping the save
	on an untick left the old record in place, so the table came back laid out by a layout
	they had just said they were finished with.

	:param handle: the table, or None where nothing can name it.
	:param layout: what they arranged.
	:param remembered: whether the table had a saved layout when the dialog opened.
	:param keepIt: whether the checkbox is ticked now.
	"""
	import ui

	if keepIt:
		if handle is not None and flowTableLayouts.remember(handle, layout):
			return
		# Translators: reported when a table's arrangement cannot be saved because nothing
		# about the table is stable enough to find it by later.
		ui.message(_("This table cannot be recognised again, so the arrangement was not saved"))
		return
	if remembered and handle is not None and flowTableLayouts.forget(handle):
		# Translators: reported when the reader unticks the box that remembers a table.
		ui.message(_("Table layout deleted"))


CAN_DRAW = wx is not None and gui is not None and hasattr(wx, "Dialog")
"""Whether there is enough wx here to build a dialog with.

Asked of `wx.Dialog` itself rather than of the import, because a test run has a stand-in for
`wx` that answers the handful of names the add-on uses and has no windows in it at all."""


if CAN_DRAW:  # pragma: no cover - a dialog needs a display.

	class TableDesignerDialog(wx.Dialog):
		"""The columns of one table, in a checked list, with what has been decided about each.

		**The list is the dialog.** Every column is one line that says everything decided
		about it, so a reader arrows down it and hears the whole arrangement rather than
		opening each column in turn to find the one they changed. The controls beside it act
		on whichever line they are on.

		**The checkbox on the line is whether the column is drawn.** This was a plain list
		with a "Show or hide" button beside it, and the reader who used it said what was wrong
		with that: the state was in the line's text and the way to change it was somewhere
		else. A checked list says both in one place — the control announces "checked" with the
		line, and space toggles it where the reader already is.
		"""

		def __init__(self, parent, arrangement: Arrangement) -> None:
			# Translators: the title of the dialog for arranging a table's columns.
			super().__init__(parent, title=_("Arrange this table"))
			self.arrangement = arrangement
			self.keepIt = arrangement.remembered
			main = wx.BoxSizer(wx.VERTICAL)
			helper = guiHelper.BoxSizerHelper(self, sizer=main)
			# **A checked list, and the checkbox is whether the column is drawn.** It was a
			# plain list with a "Show or hide" button beside it, which is a worse way to say
			# the same thing: the state lived on the line and the way to change it lived
			# somewhere else, and a reader arrowing the list heard "hidden" in the text
			# instead of hearing the control's own answer. A checkbox is announced with the
			# line it is on and is toggled where it stands.
			#
			# A **list view** with checkboxes rather than a `wx.CheckListBox`, which is the
			# obvious control and the wrong one: its checkboxes are drawn by wx rather than by
			# the system, so nothing reaches a screen reader and the reader hears a list of
			# names with no states in it. A list view's checkbox is the system's own, which
			# NVDA reports as it reports every other one.
			self.columnList = helper.addLabeledControl(
				# Translators: the label of the list of a table's columns, each of which is
				# ticked when that column is drawn.
				_("&Columns to show:"),
				wx.ListCtrl,
				style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_NO_HEADER,
			)
			# Translators: the heading of the only column of the list of a table's columns.
			self.columnList.InsertColumn(0, _("Column"))
			self.columnList.EnableCheckBoxes(True)
			self._settingChecks = False
			"""Set while the checkboxes are being put where the arrangement has them, because
			checking an item raises the same event the reader's own tick does."""

			self.columnList.Bind(wx.EVT_LIST_ITEM_SELECTED, self._onColumn)
			self.columnList.Bind(wx.EVT_LIST_ITEM_CHECKED, self._onChecked)
			self.columnList.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self._onChecked)
			buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
			# Translators: a button that moves a column earlier in the reading order.
			self.upButton = buttons.addButton(self, label=_("Move &up"))
			# Translators: a button that moves a column later in the reading order.
			self.downButton = buttons.addButton(self, label=_("Move &down"))
			self.upButton.Bind(wx.EVT_BUTTON, lambda event: self._move(-1))
			self.downButton.Bind(wx.EVT_BUTTON, lambda event: self._move(1))
			helper.addItem(buttons)
			# Translators: the label of a field for the reader's own name for a column.
			self.labelCtrl = helper.addLabeledControl(_("Call this column:"), wx.TextCtrl)
			self.labelCtrl.Bind(wx.EVT_KILL_FOCUS, self._onLabel)
			self.cuttingCtrl = helper.addLabeledControl(
				# Translators: the label of a choice of what a column does with a long value.
				_("When a value does not fit:"),
				wx.Choice,
				choices=self._withFollowing(CUTTING, arrangement.following),
			)
			self.cuttingCtrl.Bind(wx.EVT_CHOICE, self._onCutting)
			self.headerCtrl = helper.addLabeledControl(
				# Translators: the label of a choice of which end of a heading is shown.
				_("When the heading does not fit:"),
				wx.Choice,
				choices=self._withFollowing(HEADER_ENDS, flowTable.KEEP_START),
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
			self.caseCtrl = helper.addItem(
				# Translators: a checkbox for drawing a column without braille capital signs.
				wx.CheckBox(self, label=_("&No capital signs in this column")),
			)
			self.caseCtrl.Bind(wx.EVT_CHECKBOX, self._onCase)
			# A choice of one column rather than a box on each, because that is the shape of
			# the question: exactly one column is repeated, and "none of them" is not one of
			# the answers. A box on each column also had nothing to show for the default —
			# the display repeated the first drawn column and no box was ticked.
			self.keyCtrl = helper.addLabeledControl(
				# Translators: the label of a choice of which column is drawn again on every page.
				_("Column &repeated on every page:"),
				wx.Choice,
				choices=[KEY_IS_THE_FIRST],
			)
			self.keyCtrl.Bind(wx.EVT_CHOICE, self._onKey)
			self.keepCtrl = helper.addItem(
				# Translators: a checkbox for saving the arrangement against this table.
				wx.CheckBox(self, label=_("Remember this arrangement for this &table")),
			)
			self.keepCtrl.SetValue(self.keepIt)
			self.keepCtrl.Bind(wx.EVT_CHECKBOX, self._onKeep)
			helper.addDialogDismissButtons(wx.OK | wx.CANCEL)
			self.Bind(wx.EVT_BUTTON, self._onOk, id=wx.ID_OK)
			self.SetSizerAndFit(main)
			self._fillList(0)
			self.columnList.SetFocus()

		@staticmethod
		def _withFollowing(offered, effective: str) -> list:
			""":return: the labels for one choice, the following one naming what it means.

			:param offered: pairs of value and label, `FOLLOW` first.
			:param effective: the value that following currently comes out as.
			"""
			named = dict(offered)
			return [
				# Translators: an option that follows the table's own setting. The placeholder
				# is what that setting currently is.
				_("Follow the table setting ({how})").format(how=named.get(effective, "").lower())
				if not value
				else label
				for value, label in offered
			]

		@property
		def _position(self) -> int:
			return max(0, self.columnList.GetFirstSelected())

		def _fillList(self, select: int) -> None:
			"""Draw the list again and put the selection back where it was."""
			self.columnList.DeleteAllItems()
			for position in range(len(self.arrangement.columns)):
				self.columnList.InsertItem(position, self.arrangement.describe(position))
			self._showChecks()
			if self.arrangement.columns:
				self._select(min(select, len(self.arrangement.columns) - 1))
			self.columnList.SetColumnWidth(0, wx.LIST_AUTOSIZE)
			self._fillKeys()
			self._showColumn()

		def _select(self, position: int) -> None:
			"""Put the selection, and the focus that follows it, on one line."""
			self.columnList.Select(position)
			self.columnList.Focus(position)

		def _showChecks(self) -> None:
			"""Tick the columns that are drawn, and untick the rest.

			Every line rather than the one in hand, and after every redrawing of the text:
			setting a line's text is not documented to leave its check alone, and the checks
			move with the columns when one is moved up or down.

			Guarded, because checking an item raises the same event the reader's own tick
			does — and that event asks the arrangement, which redraws the lines, which checks
			the items again.
			"""
			self._settingChecks = True
			try:
				for position, column in enumerate(self.arrangement.columns):
					self.columnList.CheckItem(position, column.shown)
			finally:
				self._settingChecks = False

		def _fillKeys(self) -> None:
			"""Draw the repeated-column choice again, in whatever order the columns are now."""
			self.keyCtrl.Set([KEY_IS_THE_FIRST, *(column.name for column in self.arrangement.columns)])
			self.keyCtrl.SetSelection(
				next(
					(
						position + 1
						for position, column in enumerate(self.arrangement.columns)
						if column.index == self.arrangement.keyColumn
					),
					0,
				),
			)

		def _redrawLines(self) -> None:
			"""Draw the lines again without moving the selection.

			All of them rather than the one in hand: which column is repeated is a fact about
			the table, so a change to it moves a phrase from one line to another.
			"""
			for position in range(len(self.arrangement.columns)):
				self.columnList.SetItem(position, 0, self.arrangement.describe(position))
			self._showChecks()

		def _showColumn(self) -> None:
			"""Put the controls where the column in hand has them."""
			column = self.arrangement.at(self._position)
			if column is None:
				return
			self.labelCtrl.SetValue(column.choice.label)
			self.cuttingCtrl.SetSelection(_indexOf(CUTTING, column.cutting))
			self.headerCtrl.SetSelection(_indexOf(HEADER_ENDS, column.headerEnd))
			self.minCtrl.SetValue(column.choice.minWidth)
			self.maxCtrl.SetValue(column.choice.maxWidth)
			self.pageCtrl.SetValue(column.choice.startsAPage)
			self.caseCtrl.SetValue(column.choice.plainCase)

		def _onColumn(self, event) -> None:
			self._showColumn()

		def _move(self, by: int) -> None:
			self._fillList(self.arrangement.move(self._position, by))

		def _onChecked(self, event) -> None:
			"""Show or hide the column whose box was just ticked or unticked.

			The one answer that can be refused: the last showing column is not something to
			take away, because a table of no columns is not a layout but a blank display. The
			tick goes back and the reader is told why, rather than the box staying clear over
			a column that is still drawn.
			"""
			if self._settingChecks:
				return
			position = event.GetIndex()
			wanted = self.columnList.IsItemChecked(position)
			shown = self.arrangement.setShown(position, wanted)
			if shown != wanted:
				# Translators: reported when hiding a column would leave a table with none.
				gui.messageBox(
					_("This is the only column showing"),
					# Translators: the title of the table designer's dialog.
					_("Arrange this table"),
					wx.OK | wx.ICON_ERROR,
					self,
				)
			self._redrawLines()

		def _onLabel(self, event) -> None:
			self.arrangement.decide(self._position, label=self.labelCtrl.GetValue().strip())
			self._redrawLines()
			self._fillKeys()
			event.Skip()

		def _onCutting(self, event) -> None:
			self.arrangement.setCutting(self._position, CUTTING[self.cuttingCtrl.GetSelection()][0])
			self._redrawLines()

		def _onHeader(self, event) -> None:
			self.arrangement.decide(
				self._position,
				headerKeep=HEADER_ENDS[self.headerCtrl.GetSelection()][0],
			)
			self._redrawLines()

		def _onWidths(self, event) -> None:
			self.arrangement.decide(
				self._position,
				minWidth=self.minCtrl.GetValue(),
				maxWidth=self.maxCtrl.GetValue(),
			)
			self._redrawLines()

		def _onPage(self, event) -> None:
			self.arrangement.decide(self._position, startsAPage=self.pageCtrl.GetValue())
			self._redrawLines()

		def _onCase(self, event) -> None:
			self.arrangement.decide(self._position, plainCase=self.caseCtrl.GetValue())
			self._redrawLines()

		def _onKey(self, event) -> None:
			chosen = self.keyCtrl.GetSelection()
			column = self.arrangement.at(chosen - 1) if chosen > 0 else None
			self.arrangement.keyColumn = column.index if column is not None else 0
			self._redrawLines()

		def _onKeep(self, event) -> None:
			self.keepIt = self.keepCtrl.GetValue()

		def _onOk(self, event) -> None:
			"""Close, unless the reader asked for two things that cannot both be met.

			Said rather than silently normalised: a floor above a ceiling is not a typo that
			can be read through, and closing on it would leave the column drawn to neither
			number.
			"""
			complaint = self.arrangement.complaint()
			if not complaint:
				event.Skip()
				return
			gui.messageBox(
				complaint,
				# Translators: the title of the message about a contradictory column width.
				_("Arrange this table"),
				wx.OK | wx.ICON_ERROR,
				self,
			)
			self.minCtrl.SetFocus()

else:

	class TableDesignerDialog:  # type: ignore[no-redef]
		"""What the designer is where there is no wx to build one with."""

		def __init__(self, parent, arrangement: Arrangement) -> None:
			raise RuntimeError("The table designer needs wx")


__all__ = ["Arrangement", "Column", "TableDesignerDialog", "arrangeTheTable"]
