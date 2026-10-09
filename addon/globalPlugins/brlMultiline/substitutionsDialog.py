# BrlMultiline: the braille substitutions dialog.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Where a reader edits their braille substitutions. See `brailleSubstitutions`.

Built like NVDA's speech dictionary dialog, which is the dialog a reader already knows for this:
a list of entries, Add, Edit and Remove, and a dialog for one entry. What is different is said where
it is done.

**OK checks everything before anything is saved.** Each entry has to make sense, no two may match
the same text in the same profile, and liblouis has to compile the result with every BrlMultiline
table. If any of that fails the reader is told which entry and why, the entry is selected, and the
dialog stays open with nothing saved. Cancel leaves the list as it was.

**Personal tables are made here too**, below the list: one of NVDA's own tables with the
substitutions in front, for a reader whose language BrlMultiline has no table for. NVDA sees a new
one only once it restarts, so OK offers to restart it, as NVDA's add-on store does. See
`personalTables`.
"""

from typing import Optional

import wx

import braille
import brailleTables
import config
import core
import gui
from gui import guiHelper
from gui.settingsDialogs import SettingsDialog
from logHandler import log

from . import brailleSubstitutions, personalTables, userBrailleTable
from .brailleSubstitutions import DOTS, KINDS, RULE, TEXT, PersonalTable, Substitution


def kindLabels() -> dict:
	return {
		# Translators: how a braille substitution is shown: as other text.
		TEXT: _("Text"),
		# Translators: how a braille substitution is shown: as braille dots.
		DOTS: _("Dots"),
		# Translators: a braille substitution written as liblouis rules.
		RULE: _("liblouis rules"),
	}


def problemMessage(problem: brailleSubstitutions.Problem) -> str:
	return {
		# Translators: refused in the braille substitution dialog.
		brailleSubstitutions.NO_MATCH: _("Type the text to find."),
		# Translators: refused in the braille substitution dialog.
		brailleSubstitutions.NO_DOTS: _("Type the dots to show, such as 1245-1235."),
		# Translators: refused in the braille substitution dialog.
		brailleSubstitutions.BAD_DOTS: _(
			"Dots are cells of the numbers 1 to 8, joined by hyphens, such as 1245-1235. Use 0 for a blank cell."
		),
		# Translators: refused in the braille substitution dialog.
		brailleSubstitutions.NO_RULE: _("Type at least one liblouis rule."),
		# Translators: refused in the braille substitution dialog.
		brailleSubstitutions.RULE_INCLUDES: _("The rules cannot include other tables."),
		# Translators: refused in the braille substitution dialog.
		brailleSubstitutions.LINE_BREAK: _("The text to find and its replacement must each be one line."),
	}.get(problem, str(problem))


def profilesLabel(profiles) -> str:
	# Translators: a braille substitution used whatever configuration profile is in force.
	return ", ".join(profiles) or _("All profiles")


class SubstitutionsDialog(SettingsDialog):
	# Translators: the title of the braille substitutions dialog.
	title = _("BrlMultiline braille substitutions")
	helpId = "SpeechDictionaries"

	def __init__(self, parent):
		super().__init__(parent, resizeable=True)
		self.SetSize(640, 520)
		self.CentreOnScreen()

	def makeSettings(self, settingsSizer):
		# Read here rather than before the base constructor, which calls this: nothing is set on a wx
		# window before wx has made it. A copy, so Cancel leaves the list as it was.
		self.entries: list = userBrailleTable.entries()
		self.tables: list = userBrailleTable.tables()
		sHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		for notice in self._notices():
			sHelper.addItem(wx.StaticText(self, label=notice))
		self.list = sHelper.addLabeledControl(
			# Translators: the label of the list of braille substitutions.
			_("&Substitutions"),
			wx.ListCtrl,
			style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
		)
		# Translators: a column of the braille substitutions list: the text found.
		self.list.AppendColumn(_("Find"), width=170)
		# Translators: a column of the braille substitutions list: how it is shown.
		self.list.AppendColumn(_("Shown as"), width=90)
		# Translators: a column of the braille substitutions list: the text, dots or rules it is shown as.
		self.list.AppendColumn(_("Replacement"), width=170)
		# Translators: a column of the braille substitutions list: which profiles it is used in.
		self.list.AppendColumn(_("Profile"), width=110)
		# Translators: a column of the braille substitutions list: whether it is used.
		self.list.AppendColumn(_("Used"), width=50)
		self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._onEdit)
		self.list.Bind(wx.EVT_CHAR_HOOK, self._onCharHook)

		buttons = guiHelper.ButtonHelper(orientation=wx.HORIZONTAL)
		# Translators: adds a braille substitution.
		buttons.addButton(self, label=_("&Add...")).Bind(wx.EVT_BUTTON, self._onAdd)
		# Translators: edits the selected braille substitution.
		self.editButton = buttons.addButton(self, label=_("&Edit..."))
		self.editButton.Bind(wx.EVT_BUTTON, self._onEdit)
		# Translators: removes the selected braille substitution.
		self.removeButton = buttons.addButton(self, label=_("&Remove"))
		self.removeButton.Bind(wx.EVT_BUTTON, self._onRemove)
		sHelper.addItem(buttons)

		self.tableList = sHelper.addLabeledControl(
			# Translators: the label of the list of the reader's personal braille tables: NVDA tables with
			# their braille substitutions in front of them.
			_("Personal &tables, for languages BrlMultiline has no table for"),
			wx.ListBox,
			choices=[],
		)
		tableButtons = guiHelper.ButtonHelper(orientation=wx.HORIZONTAL)
		# Translators: makes a personal braille table from one of NVDA's braille tables.
		tableButtons.addButton(self, label=_("&Create personal table...")).Bind(
			wx.EVT_BUTTON, self._onCreateTable
		)
		# Translators: removes the selected personal braille table.
		self.removeTableButton = tableButtons.addButton(self, label=_("Remo&ve table"))
		self.removeTableButton.Bind(wx.EVT_BUTTON, self._onRemoveTable)
		sHelper.addItem(tableButtons)
		self._fill(0)
		self._fillTables(0)

	def _notices(self) -> list:
		notices = []
		problem = userBrailleTable.loadProblem()
		if problem:
			notices.append(problem)
		table = braille.handler.table.fileName if braille.handler else ""
		if not userBrailleTable.usesSubstitutions(table):
			notices.append(
				# Translators: said in the braille substitutions dialog when the braille output table in
				# force does not use the substitutions.
				_(
					"Substitutions are used only with BrlMultiline's braille tables and your personal tables. "
					"Choose one as the output table in NVDA's Braille settings, or create a personal table "
					"from the table you read."
				)
			)
		return notices

	def postInit(self):
		self.list.SetFocus()

	# The list

	def _row(self, entry: Substitution) -> tuple:
		replacement = entry.replacement
		if entry.kind == RULE:
			replacement = " / ".join(brailleSubstitutions.ruleLines(replacement))
		elif entry.kind == TEXT and not replacement:
			# Translators: a braille substitution that shows nothing in place of what it finds.
			replacement = _("(nothing)")
		# Translators: whether a braille substitution is used.
		used = _("yes") if entry.enabled else _("no")
		return (entry.match, kindLabels()[entry.kind], replacement, profilesLabel(entry.profiles), used)

	def _fill(self, select: Optional[int]) -> None:
		self.list.DeleteAllItems()
		for entry in self.entries:
			self.list.Append(self._row(entry))
		if self.entries and select is not None:
			select = max(0, min(select, len(self.entries) - 1))
			self.list.Select(select)
			self.list.Focus(select)
		hasAny = bool(self.entries)
		self.editButton.Enable(hasAny)
		self.removeButton.Enable(hasAny)

	def _selected(self) -> int:
		return self.list.GetFirstSelected()

	def _onCharHook(self, event) -> None:
		if event.GetKeyCode() == wx.WXK_DELETE:
			self._onRemove(None)
		else:
			event.Skip()

	def _onAdd(self, event) -> None:
		entry = self._ask(Substitution(match=""), add=True)
		if entry is not None:
			self.entries.append(entry)
			self._fill(len(self.entries) - 1)
		self.list.SetFocus()

	def _onEdit(self, event) -> None:
		index = self._selected()
		if index < 0:
			return
		entry = self._ask(self.entries[index], add=False)
		if entry is not None:
			self.entries[index] = entry
			self._fill(index)
		self.list.SetFocus()

	def _onRemove(self, event) -> None:
		index = self._selected()
		if index < 0:
			return
		del self.entries[index]
		self._fill(index)
		self.list.SetFocus()

	# Personal tables

	def _fillTables(self, select: Optional[int]) -> None:
		self.tableList.SetItems([f"{table.displayName}{personalTables.NAME_SUFFIX}" for table in self.tables])
		if self.tables and select is not None:
			self.tableList.SetSelection(max(0, min(select, len(self.tables) - 1)))
		self.removeTableButton.Enable(bool(self.tables))

	def _offered(self) -> list:
		""":return: NVDA's own output tables that have no personal table yet, by name.

		Only NVDA's own: a personal table includes its table by name, which NVDA's table resolver finds
		among its own tables and not another add-on's.
		"""
		taken = {table.base for table in self.tables}
		return [
			table
			for table in brailleTables.listTables()
			if table.output
			and table.source == brailleTables.TableSource.BUILTIN
			and table.fileName not in taken
		]

	def _onCreateTable(self, event) -> None:
		offered = self._offered()
		with wx.MultiChoiceDialog(
			self,
			# Translators: asks which of NVDA's braille tables to make personal tables from.
			_("Choose the braille tables to use your substitutions with:"),
			# Translators: the title of the dialog choosing braille tables to make personal tables from.
			_("Create personal tables"),
			[table.displayName for table in offered],
		) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				self.tableList.SetFocus()
				return
			chosen = [offered[index] for index in dialog.GetSelections()]
		for table in chosen:
			self.tables.append(PersonalTable(table.fileName, table.displayName, table.contracted))
		self._fillTables(len(self.tables) - 1)
		self.tableList.SetFocus()

	def _onRemoveTable(self, event) -> None:
		index = self.tableList.GetSelection()
		if index < 0 or index >= len(self.tables):
			return
		del self.tables[index]
		self._fillTables(index)
		self.tableList.SetFocus()

	def _ask(self, entry: Substitution, add: bool) -> Optional[Substitution]:
		with EntryDialog(self, entry, add) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				return None
			return dialog.entry

	# Saving

	def onOk(self, evt):
		restart = self._commit()
		if restart is not None:
			super().onOk(evt)
			if restart:
				wx.CallAfter(_offerRestart)

	def _refuse(self, index: Optional[int], message: str) -> None:
		if index is not None and 0 <= index < len(self.entries):
			self.list.Select(index)
			self.list.Focus(index)
			# Translators: says which braille substitution could not be saved, and why. The placeholders
			# are the text it finds and the reason.
			message = _("{find}: {why}").format(find=self.entries[index].match, why=message)
		gui.messageBox(message, self.GetTitle(), wx.OK | wx.ICON_ERROR, self)
		self.list.SetFocus()
		return None

	def _commit(self) -> Optional[bool]:
		""":return: None if nothing was saved, otherwise whether NVDA has to restart for the personal tables."""
		for index, entry in enumerate(self.entries):
			problem = brailleSubstitutions.problemOf(entry)
			if problem is not None:
				return self._refuse(index, problemMessage(problem))
		clash = brailleSubstitutions.clash(self.entries)
		if clash is not None:
			first, second = clash
			return self._refuse(
				second,
				# Translators: refused when two braille substitutions find the same text in the same profile.
				# The placeholder is the text the other one finds.
				_("This finds the same text as {other}. Change or remove one of them.").format(
					other=self.entries[first].match,
				),
			)
		with wx.BusyCursor():
			refusals = userBrailleTable.validate(self.entries, self.tables)
		if refusals:
			refusal = refusals[0]
			return self._refuse(
				refusal.entry,
				# Translators: refused when liblouis cannot compile a braille substitution. The placeholder is
				# what liblouis said.
				_("liblouis could not use this: {message}").format(message=refusal.message),
			)
		try:
			return userBrailleTable.commit(self.entries, self.tables)
		except userBrailleTable.NotSaved as error:
			log.error(f"BrlMultiline: braille substitutions not saved: {error}")
			return self._refuse(
				None,
				# Translators: said when the braille substitutions could not be written.
				_("The braille substitutions could not be saved. Nothing was changed. See NVDA's log."),
			)
		except userBrailleTable.NotApplied:
			# Saved, but braille is still using the rules from before. The dialog stays open, so OK tries
			# again; the list is saved again with it, which changes nothing.
			return self._refuse(
				None,
				# Translators: said when the braille substitutions were saved but could not be put in use.
				_(
					"The braille substitutions were saved, but braille could not start using them, and is "
					"still using the ones from before. Press OK to try again, or see NVDA's log."
				),
			)
		except userBrailleTable.NotListed:
			gui.messageBox(
				# Translators: said when the substitutions were saved but the personal tables could not be
				# listed for NVDA.
				_(
					"The substitutions were saved and are in use, but the personal tables could not be made. "
					"See NVDA's log."
				),
				self.GetTitle(),
				wx.OK | wx.ICON_ERROR,
				self,
			)
			return False


class EntryDialog(wx.Dialog):
	"""One substitution: what it finds, what it shows, and where."""

	def __init__(self, parent, entry: Substitution, add: bool):
		super().__init__(
			parent,
			# Translators: the title of the dialog adding a braille substitution.
			title=_("Add substitution") if add else _("Edit substitution"),
		)
		self.entry: Optional[Substitution] = None
		self.profiles = sorted(config.conf.listProfiles())
		for name in entry.profiles:
			if name not in self.profiles:
				# Kept though the profile is gone, so editing does not quietly drop it from the entry.
				self.profiles.append(name)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sHelper = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)
		self.kindCtrl = sHelper.addLabeledControl(
			# Translators: the label of the choice of how a braille substitution is shown.
			_("Show it &as:"),
			wx.Choice,
			choices=[kindLabels()[kind] for kind in KINDS],
		)
		self.kindCtrl.SetSelection(KINDS.index(entry.kind))
		self.kindCtrl.Bind(wx.EVT_CHOICE, self._onKind)
		self.matchLabel = wx.StaticText(self)
		self.matchCtrl = wx.TextCtrl(self, value=entry.match)
		sHelper.addItem(self.matchLabel)
		sHelper.addItem(self.matchCtrl, flag=wx.EXPAND)
		# Two fields, one shown at a time: a line for text and dots, so Enter presses OK as it does in
		# NVDA's dictionary entry dialog, and several lines for liblouis rules.
		self.replacementLabel = wx.StaticText(self)
		self.replacementCtrl = wx.TextCtrl(self, value=entry.replacement if entry.kind != RULE else "")
		sHelper.addItem(self.replacementLabel)
		sHelper.addItem(self.replacementCtrl, flag=wx.EXPAND)
		# Translators: the label of the liblouis rules a braille substitution is made of.
		self.rulesLabel = wx.StaticText(self, label=_("liblouis &rules, one a line:"))
		self.rulesCtrl = wx.TextCtrl(
			self,
			value=entry.replacement if entry.kind == RULE else "",
			style=wx.TE_MULTILINE,
			size=(-1, 90),
		)
		sHelper.addItem(self.rulesLabel)
		sHelper.addItem(self.rulesCtrl, flag=wx.EXPAND)
		# Translators: a braille substitution that finds only whole words.
		self.wholeWordCtrl = sHelper.addItem(wx.CheckBox(self, label=_("&Whole word only")))
		self.wholeWordCtrl.SetValue(entry.wholeWord)
		# Translators: a braille substitution that finds only the capitals typed.
		self.matchCaseCtrl = sHelper.addItem(wx.CheckBox(self, label=_("Match &case")))
		self.matchCaseCtrl.SetValue(entry.matchCase)
		# Translators: a braille substitution used whatever configuration profile is in force.
		self.allProfilesCtrl = sHelper.addItem(wx.CheckBox(self, label=_("Use in all &profiles")))
		self.allProfilesCtrl.SetValue(not entry.profiles)
		self.allProfilesCtrl.Bind(wx.EVT_CHECKBOX, self._onAllProfiles)
		# A list view with the system's own checkboxes, as the table layout editor's list of columns:
		# a `wx.CheckListBox` draws its own, and a screen reader hears no states in it.
		self.profilesCtrl = sHelper.addLabeledControl(
			# Translators: the label of the list of configuration profiles a braille substitution is used in,
			# each ticked when it is.
			_("&Only in these profiles:"),
			wx.ListCtrl,
			style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_NO_HEADER,
			size=(-1, 90),
		)
		# Translators: the heading of the only column of the list of profiles.
		self.profilesCtrl.InsertColumn(0, _("Profile"))
		self.profilesCtrl.EnableCheckBoxes(True)
		for position, name in enumerate(self.profiles):
			self.profilesCtrl.InsertItem(position, name)
			self.profilesCtrl.CheckItem(position, name in entry.profiles)
		self.profilesCtrl.SetColumnWidth(0, wx.LIST_AUTOSIZE)
		if self.profiles:
			self.profilesCtrl.Select(0)
			self.profilesCtrl.Focus(0)
		self._onAllProfiles(None)
		# Translators: whether a braille substitution is used.
		self.enabledCtrl = sHelper.addItem(wx.CheckBox(self, label=_("&Use this substitution")))
		self.enabledCtrl.SetValue(entry.enabled)
		sHelper.addDialogDismissButtons(wx.OK | wx.CANCEL)
		self.Bind(wx.EVT_BUTTON, self._onOk, id=wx.ID_OK)
		sizer.Add(sHelper.sizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL | wx.EXPAND)
		self.SetSizer(sizer)
		self._onKind(None)
		sizer.Fit(self)
		self.SetMinSize((460, -1))
		self.CentreOnParent()
		self.kindCtrl.SetFocus()

	def _kind(self) -> str:
		return KINDS[self.kindCtrl.GetSelection()]

	def _onAllProfiles(self, event) -> None:
		"""The list of profiles is for choosing some of them, so not while it is used in all."""
		self.profilesCtrl.Enable(not self.allProfilesCtrl.GetValue() and bool(self.profiles))

	def _chosenProfiles(self) -> tuple:
		if self.allProfilesCtrl.GetValue():
			return ()
		return tuple(
			name for position, name in enumerate(self.profiles) if self.profilesCtrl.IsItemChecked(position)
		)

	def _onKind(self, event) -> None:
		"""Name the fields for what is being made. A rule entry finds nothing itself, so no options."""
		kind = self._kind()
		if kind == RULE:
			# Translators: the label of a braille substitution's description, for one written as liblouis rules.
			match = _("&Description:")
		else:
			# Translators: the label of the text a braille substitution finds.
			match = _("&Find:")
		replacement = (
			# Translators: the label of the text a braille substitution shows. Empty shows nothing.
			_("&Replace with text:")
			if kind == TEXT
			# Translators: the label of the dots a braille substitution shows.
			else _("&Dots, such as 1245-1235:")
		)
		self.matchLabel.SetLabel(match)
		self.replacementLabel.SetLabel(replacement)
		# The labels are read as the fields' names: set them on the fields as well, since a label
		# changed after the field was made is not always what a screen reader is told.
		self.matchCtrl.SetName(match.replace("&", ""))
		self.replacementCtrl.SetName(replacement.replace("&", ""))
		self.rulesCtrl.SetName(self.rulesLabel.GetLabel().replace("&", ""))
		for control in (self.replacementLabel, self.replacementCtrl):
			control.Show(kind != RULE)
		for control in (self.rulesLabel, self.rulesCtrl):
			control.Show(kind == RULE)
		for control in (self.wholeWordCtrl, self.matchCaseCtrl):
			control.Enable(kind != RULE)
		self.Layout()
		self.Fit()

	def _onOk(self, event) -> None:
		kind = self._kind()
		replacement = (self.rulesCtrl if kind == RULE else self.replacementCtrl).GetValue()
		if kind == DOTS:
			try:
				replacement = brailleSubstitutions.normalizedDots(replacement)
			except ValueError:
				pass
		entry = Substitution(
			match=self.matchCtrl.GetValue(),
			kind=kind,
			replacement=replacement,
			wholeWord=self.wholeWordCtrl.GetValue(),
			matchCase=self.matchCaseCtrl.GetValue(),
			profiles=self._chosenProfiles(),
			enabled=self.enabledCtrl.GetValue(),
		)
		if not self.allProfilesCtrl.GetValue() and not entry.profiles:
			gui.messageBox(
				# Translators: refused in the braille substitution dialog when it is to be used in only some
				# profiles and none is ticked.
				_("Tick the profiles to use it in, or use it in all profiles."),
				self.GetTitle(),
				wx.OK | wx.ICON_ERROR,
				self,
			)
			(self.profilesCtrl if self.profiles else self.allProfilesCtrl).SetFocus()
			return
		problem = brailleSubstitutions.problemOf(entry)
		if problem is not None:
			gui.messageBox(problemMessage(problem), self.GetTitle(), wx.OK | wx.ICON_ERROR, self)
			if problem == brailleSubstitutions.NO_MATCH:
				self.matchCtrl.SetFocus()
			else:
				(self.rulesCtrl if kind == RULE else self.replacementCtrl).SetFocus()
			return
		self.entry = entry
		self.EndModal(wx.ID_OK)


def _offerRestart() -> None:
	"""Ask to restart NVDA, as its add-on store does after a change it sees only on starting."""
	result = gui.messageBox(
		# Translators: asks to restart NVDA after the reader's personal braille tables changed.
		_(
			"Your personal braille tables changed. NVDA shows them in its Braille settings only after it "
			"restarts. Would you like to restart now?"
		),
		# Translators: the title of the question asking to restart NVDA.
		_("Restart NVDA"),
		wx.YES | wx.NO | wx.ICON_WARNING,
	)
	if result == wx.YES:
		core.restart()


def openDialog() -> None:
	"""Open the braille substitutions dialog the way NVDA opens its speech dictionaries."""
	gui.mainFrame.popupSettingsDialog(SubstitutionsDialog)
