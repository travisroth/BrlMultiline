# BrlMultiline: the saved table layouts, listed and edited.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Where a reader sees every table layout they have saved, and fixes the ones that stopped applying.

A saved layout is found again by the address of its table and the table's first headings, and
either can change under the reader without their doing anything. A site renumbers a view in its
address; a watchlist stops coming up laid out. Before this there was nothing to do about that
except arrange the table again from nothing, and nothing to say which saved layout had been its
own, or which of the rest belonged to pages that were gone. Found on hardware, 16 September 2026.

So this lists them — the ones for the table the reader is in first — and says of each what it is
called, where it applies, how strictly, and when it was last used. And it lets the reader do the
things a changed site calls for:

1. **Use a layout for this table**, which points it at the table in front of them: its address and
   its headings. The watchlist whose address changed is one press.
2. **Also use it for this table**, which copies it, for the same table living at two addresses.
3. **Change how strictly its address matches**: this page whatever the query string says, this
   exact address, or anywhere on the site.
4. **Let it apply whatever the headings are**, for a site that renames a column among its first.
5. **Rename it, edit its address, or delete it**, which is what an orphaned layout wants.

**The manager is a plain object and the dialog is a shell over it**, the split `flowTableDesigner`
and `keyLayerDialog` make. `LayoutManager` holds a working copy of the store and decides
everything, tested without a display; the dialog is widgets. Nothing is written until OK.
"""

import dataclasses
from typing import Optional

from logHandler import log

from . import flowTableLayouts
from .flowTableLayouts import (
	FOLLOW,
	MATCH_EXACT,
	MATCH_PATH,
	MATCH_SITE,
	SPEAK_COLUMNS,
	SPEAK_HEADERS,
	SPEAK_OFF,
	SPEAK_ROWS,
	SavedLayout,
)

try:
	import wx
	import gui
	from gui import guiHelper
except ImportError:  # pragma: no cover - only in a test run with no NVDA around it.
	wx = None
	gui = None
	guiHelper = None


@dataclasses.dataclass(frozen=True)
class Here:
	"""The table the reader was in when the manager was opened."""

	where: str
	"""Its address, or "" for a table that cannot be named."""

	headings: tuple = ()
	"""Its first headings."""

	reason: str = ""
	"""Why no layout applied to it, when none did."""


def hereFor(handle) -> Optional[Here]:
	""":return: what the manager needs to know about the table the reader is in, or None for no table.

	Read once, when the manager is opened, since the reader is not in the table while the dialog is up.

	:param handle: the table, as `flowTableSource.tableAt` returned it, or None.
	"""
	if handle is None:
		return None
	found = flowTableLayouts.find(handle)
	if not found.where:
		return Here("", reason=found.reason)
	headings = found.headings or flowTableLayouts.headingsOf(handle)
	return Here(found.where, tuple(headings), found.reason if found.saved is None else "")


class LayoutManager:
	"""A working copy of the saved layouts, and every change the manager can make to it."""

	def __init__(self, layouts, here: Optional[Here] = None) -> None:
		"""
		:param layouts: the store, as `flowTableLayouts.stored` reads it.
		:param here: the table the reader was in, or None.
		"""
		self.here = here if here is not None and here.where else None
		self.changed = False
		"""Whether anything has been changed, so OK with nothing changed writes nothing."""
		self._layouts = list(layouts)
		self._applied: Optional[tuple] = None
		"""(the layouts it was worked out for, the layout that applies here), since every line of the
		list asks."""
		# Worked out before sorting and handed to the ranking, not asked for inside it: while a list is
		# being sorted it reads as empty, so the ranking found nothing applying and a review saw a layout
		# for other headings put first, where the dialog selects it.
		applied = self.appliedId
		self._layouts.sort(key=lambda saved: self._rank(saved, applied))

	# Reading

	def _rank(self, saved: SavedLayout, applied: Optional[str]) -> tuple:
		""":return: where a layout goes in the list: the one that applies here, then others at this
		address, then others on this site, then the rest, most recently used first.

		:param saved: the layout.
		:param applied: the id of the layout that applies here, worked out before the sort.
		"""
		if self.here is not None:
			if saved.id == applied:
				nearness = 0
			elif flowTableLayouts.placeMatches(saved, self.here.where):
				nearness = 1
			elif self._sameSite(saved):
				nearness = 2
			else:
				nearness = 3
		else:
			nearness = 3
		return (nearness, -saved.used, -saved.saved, saved.name.lower())

	def _sameSite(self, saved: SavedLayout) -> bool:
		if self.here is None or saved.isLegacy:
			return False
		return flowTableLayouts.sameSite(saved.where, self.here.where)

	def __len__(self) -> int:
		return len(self._layouts)

	@property
	def layouts(self) -> list:
		""":return: the working copy, as it would be stored."""
		return list(self._layouts)

	def at(self, index: int) -> Optional[SavedLayout]:
		return self._layouts[index] if 0 <= index < len(self._layouts) else None

	def indexOf(self, layoutId: str) -> int:
		""":return: where a layout is in the list now, or -1."""
		return next((index for index, saved in enumerate(self._layouts) if saved.id == layoutId), -1)

	@property
	def appliedId(self) -> Optional[str]:
		""":return: the layout that would apply to the table the reader was in, as the list now stands.

		Judged on what was read of the table when the manager opened. See `KnownHeadings`.
		"""
		if self.here is None:
			return None
		now = tuple(self._layouts)
		if self._applied is None or self._applied[0] != now:
			headings = flowTableLayouts.KnownHeadings(self.here.headings)
			found = flowTableLayouts.find(None, now, headings, where=self.here.where)
			self._applied = (now, found.saved.id if found.saved is not None else None)
		return self._applied[1]

	@staticmethod
	def nameOf(saved: SavedLayout) -> str:
		if saved.name:
			return saved.name
		if saved.isLegacy:
			# Translators: the name of a saved table layout from before names were kept, until the
			# table it belongs to is next seen. The placeholder is the date it was saved.
			return _("Unnamed layout saved {date}").format(date=flowTableLayouts.dateWords(saved.saved))
		return flowTableLayouts.defaultNameFor(saved.where, saved.headings)

	@staticmethod
	def matchWords(saved: SavedLayout) -> str:
		""":return: how strictly a layout's address matches, as the list and the details say it."""
		if saved.isLegacy:
			# Translators: said of a saved table layout whose address is not known yet.
			return _("address not recorded yet")
		if saved.match == MATCH_PATH:
			# Translators: said of a saved table layout that applies to its page whatever follows the
			# question mark in the address.
			return _("this page, any query")
		if saved.match == MATCH_SITE:
			# Translators: said of a saved table layout that applies anywhere on its site.
			return _("anywhere on this site")
		# Translators: said of a saved table layout that applies only at the exact address it was saved at.
		return _("this exact address")

	@staticmethod
	def columnsWords(saved: SavedLayout) -> str:
		layout = saved.tableLayout
		if not layout.laysOut:
			# Translators: said of a saved table layout that was saved only for how its table's headers
			# are spoken, and does not lay the table out in columns.
			return _("not laid out in columns")
		if not layout.columns:
			# Translators: said of a saved table layout that shows every column of its table.
			return _("all columns")
		# Translators: said of a saved table layout that shows some of its table's columns. The
		# placeholder is how many.
		return _("{count} columns").format(count=len(layout.columns))

	@staticmethod
	def headerSpeechWords(speakHeaders: str) -> str:
		""":return: how a layout's table has its headers spoken, as the list, the details and the choice say it."""
		if speakHeaders == SPEAK_OFF:
			# Translators: said of a saved table layout whose table has none of its headers spoken.
			return _("no headers spoken")
		if speakHeaders == SPEAK_ROWS:
			# Translators: said of a saved table layout whose table has only its row headers spoken.
			return _("row headers only spoken")
		if speakHeaders == SPEAK_COLUMNS:
			# Translators: said of a saved table layout whose table has only its column headers spoken.
			return _("column headers only spoken")
		# Translators: said of a saved table layout whose table has its headers spoken as NVDA's
		# document formatting settings say.
		return _("headers spoken as NVDA says")

	def speakHeadersChoices(self) -> list:
		""":return: (speakHeaders, label) for each way a layout's table can have its headers spoken."""
		return [(speakHeaders, self.headerSpeechWords(speakHeaders)) for speakHeaders in SPEAK_HEADERS]

	def label(self, index: int) -> str:
		""":return: one line for the list, saying everything a reader choosing among them needs.

		**Where it applies first after its name**, because the question the reader opened this with is
		usually which of these is the one for the table they are in.
		"""
		saved = self.at(index)
		if saved is None:
			return ""
		parts = [self.nameOf(saved)]
		if saved.id == self.appliedId:
			# Translators: said of the saved table layout that applies to the table the reader is in.
			parts.append(_("applies here"))
		elif self.here is not None and flowTableLayouts.placeMatches(saved, self.here.where):
			# Translators: said of a saved table layout for the reader's address that does not apply to
			# this table, because its headings are different.
			parts.append(_("at this address, other headings"))
		parts.append(self.matchWords(saved))
		parts.append(self.columnsWords(saved))
		speakHeaders = saved.tableLayout.speakHeaders
		if speakHeaders != FOLLOW:
			parts.append(self.headerSpeechWords(speakHeaders))
		# Translators: when a saved table layout was last used. The placeholder is a date.
		parts.append(_("last used {date}").format(date=flowTableLayouts.dateWords(saved.used)))
		return ", ".join(parts)

	def details(self, index: int) -> str:
		""":return: everything about one layout, a line each, for the read-only details field."""
		saved = self.at(index)
		if saved is None:
			return ""
		layout = saved.tableLayout
		# Translators: a line of the details of a saved table layout. The placeholder is its name.
		lines = [_("Name: {name}").format(name=self.nameOf(saved))]
		if saved.isLegacy:
			lines.append(
				# Translators: a line of the details of a saved table layout from before addresses were
				# kept.
				_(
					"Address: not recorded. It is filled in the next time its table is seen, or use it for this table."
				),
			)
		else:
			# Translators: a line of the details of a saved table layout. The placeholder is its address.
			lines.append(_("Address: {where}").format(where=saved.where))
		# Translators: a line of the details of a saved table layout: how strictly its address matches.
		lines.append(_("Matches: {how}").format(how=self.matchWords(saved)))
		headings = ", ".join(heading for heading in saved.headings if heading)
		if headings:
			if saved.requireHeadings:
				# Translators: a line of the details of a saved table layout: the headings a table must have.
				lines.append(_("Only for a table with the headings: {headings}").format(headings=headings))
			else:
				# Translators: a line of the details of a saved table layout that applies whatever the
				# headings are. The placeholder is the headings it was saved with.
				lines.append(_("For any headings; saved with: {headings}").format(headings=headings))
		named = [layout.columnHeadings.get(column) or str(column) for column in layout.columns]
		if named:
			# Translators: a line of the details of a saved table layout: the columns it shows, in order.
			lines.append(_("Columns shown: {columns}").format(columns=", ".join(named)))
		else:
			lines.append(_("Columns shown: {columns}").format(columns=self.columnsWords(saved)))
		lines.append(
			# Translators: a line of the details of a saved table layout: which of its table's headers
			# speech says.
			_("Spoken headers: {how}").format(how=self.headerSpeechWords(layout.speakHeaders)),
		)
		# Translators: a line of the details of a saved table layout. The placeholder is a date.
		lines.append(_("Saved: {date}").format(date=flowTableLayouts.dateWords(saved.saved)))
		# Translators: a line of the details of a saved table layout. The placeholder is a date.
		lines.append(_("Last used: {date}").format(date=flowTableLayouts.dateWords(saved.used)))
		return "\n".join(lines)

	def hereWords(self) -> str:
		""":return: what the manager says about the table the reader was in."""
		if self.here is None:
			# Translators: said in the saved table layouts manager when it was opened outside a table.
			return _("Not in a table. Open this from a table to use a layout for it.")
		# Translators: the start of what the saved table layouts manager says about the reader's table.
		lines = [_("This table: {where}").format(where=self.here.where)]
		headings = ", ".join(heading for heading in self.here.headings if heading)
		if headings:
			# Translators: the headings of the table the reader is in.
			lines.append(_("Headings: {headings}").format(headings=headings))
		applied = self.appliedId
		if applied is not None:
			# Translators: said when a saved layout applies to the reader's table. The placeholder is its name.
			lines.append(_("Uses: {name}").format(name=self.nameOf(self._layouts[self.indexOf(applied)])))
		else:
			# Translators: said when no saved layout applies to the reader's table.
			lines.append(_("No saved layout applies to this table."))
		return "\n".join(lines)

	def matchChoices(self, index: int) -> list:
		""":return: (match, label) for each way this layout's address can match. One, for anything but a web page."""
		saved = self.at(index)
		if saved is None or saved.isLegacy:
			return []
		if not flowTableLayouts.isAddress(saved.where):
			return [(MATCH_EXACT, self.matchWords(dataclasses.replace(saved, match=MATCH_EXACT)))]
		return [
			(match, self.matchWords(dataclasses.replace(saved, match=match)))
			for match in flowTableLayouts.MATCHES
		]

	# Changing

	def _replace(self, index: int, changed: SavedLayout) -> None:
		self._layouts[index] = changed
		self.changed = True

	def rename(self, index: int, name: str) -> None:
		"""Call a layout something. Empty gives it back the name made from its address."""
		saved = self.at(index)
		name = " ".join((name or "").split())
		if saved is None or name == saved.name:
			return
		self._replace(index, dataclasses.replace(saved, name=name))

	def setAddress(self, index: int, where: str) -> str:
		"""Point a layout at another address.

		:return: why not, or "" when it was done.
		"""
		saved = self.at(index)
		where = (where or "").strip()
		if saved is None:
			return ""
		if not where:
			# Translators: reported when the address of a saved table layout is left empty.
			return _("A saved layout needs an address")
		if where == saved.where:
			return ""
		match = saved.match
		if saved.isLegacy:
			match = flowTableLayouts.defaultMatchFor(where)
		elif match != MATCH_EXACT and not flowTableLayouts.isAddress(where):
			match = MATCH_EXACT
		self._replace(
			index,
			dataclasses.replace(saved, where=where, match=match, legacyWhere="", legacyWhat=""),
		)
		return ""

	def setMatch(self, index: int, match: str) -> None:
		saved = self.at(index)
		if saved is None or match == saved.match or match not in dict(self.matchChoices(index)):
			return
		self._replace(index, dataclasses.replace(saved, match=match))

	def setSpeakHeaders(self, index: int, speakHeaders: str) -> None:
		"""Say which of a layout's table's headers speech says. See `tableHeaderSpeech`."""
		saved = self.at(index)
		if saved is None or speakHeaders not in SPEAK_HEADERS:
			return
		layout = saved.tableLayout
		if speakHeaders == layout.speakHeaders:
			return
		changed = dataclasses.replace(layout, speakHeaders=speakHeaders)
		self._replace(index, dataclasses.replace(saved, layout=changed.asRecord()))

	def setRequireHeadings(self, index: int, require: bool) -> None:
		saved = self.at(index)
		if saved is None or bool(require) == saved.requireHeadings:
			return
		self._replace(index, dataclasses.replace(saved, requireHeadings=bool(require)))

	def useHere(self, index: int) -> str:
		"""Point a layout at the table the reader was in: its address and its headings.

		How strictly the address matches is kept, unless it is a layout from before addresses were
		kept, which gets the default for this address, or a looser match no longer means anything here.

		**Only if it then applies.** A layout more particular about this address — an exact one, where
		this matches the whole site — goes on winning, and a review found this reporting success for a
		change the table would never show. So the change is tried against the list as it would be and
		refused, naming the layout in the way, where it would not apply.

		Given the date as now, since pointing a layout at a table is using it, and a date of months ago
		would put it first in line to be dropped when the store is full.

		:return: why not, or "" when it was done.
		"""
		saved = self.at(index)
		if saved is None:
			return ""
		if self.here is None:
			# Translators: reported when a layout cannot be used for this table because the manager was
			# not opened from a table.
			return _("Open this from a table to use a layout for it")
		match = saved.match
		if saved.isLegacy:
			match = flowTableLayouts.defaultMatchFor(self.here.where)
		elif match != MATCH_EXACT and not flowTableLayouts.isAddress(self.here.where):
			match = MATCH_EXACT
		now = flowTableLayouts._now()
		pointed = dataclasses.replace(
			saved,
			name=saved.name or flowTableLayouts.defaultNameFor(self.here.where, self.here.headings),
			where=self.here.where,
			match=match,
			headings=self.here.headings,
			legacyWhere="",
			legacyWhat="",
			saved=now,
			used=now,
		)
		why = self._wouldNotApply(index, pointed)
		if why:
			return why
		self._replace(index, pointed)
		return ""

	def alsoUseHere(self, index: int) -> tuple:
		"""Copy a layout, pointed at the table the reader was in, leaving the original where it was.

		Refused for a layout that already applies here, which would only make a second copy of what the
		table already shows, and where the copy would not apply, for the reason `useHere` gives.

		:return: (where the copy is in the list, or -1; why not, or "").
		"""
		saved = self.at(index)
		if saved is None:
			return -1, ""
		if self.here is None:
			return -1, _("Open this from a table to use a layout for it")
		if saved.id == self.appliedId:
			# Translators: reported when a saved table layout is copied for the table it already applies to.
			return -1, _("This layout already applies to this table")
		now = flowTableLayouts._now()
		copy = dataclasses.replace(
			saved,
			id=flowTableLayouts.newId(),
			name=flowTableLayouts.defaultNameFor(self.here.where, self.here.headings),
			where=self.here.where,
			match=flowTableLayouts.defaultMatchFor(self.here.where),
			headings=self.here.headings,
			requireHeadings=True,
			legacyWhere="",
			legacyWhat="",
			saved=now,
			used=now,
		)
		why = self._wouldNotApply(index + 1, copy, insert=True)
		if why:
			return -1, why
		self._layouts.insert(index + 1, copy)
		self.changed = True
		return index + 1, ""

	def _wouldNotApply(self, index: int, changed: SavedLayout, insert: bool = False) -> str:
		""":return: why a changed or added layout would not apply to the reader's table, or "" if it would.

		:param index: where it would be in the list.
		:param changed: the layout.
		:param insert: whether it is added there rather than replacing what is there.
		"""
		trial = list(self._layouts)
		if insert:
			trial.insert(index, changed)
		else:
			trial[index] = changed
		headings = flowTableLayouts.KnownHeadings(self.here.headings)
		found = flowTableLayouts.find(None, trial, headings, where=self.here.where)
		if found.saved is not None and found.saved.id == changed.id:
			return ""
		if found.saved is not None:
			return _(
				# Translators: reported when a saved table layout cannot be used for this table because
				# another layout is more particular about this address. The placeholder is its name.
				"{name} would still apply here, because it is more particular about this address. "
				"Change how it matches, or delete it, first."
			).format(name=self.nameOf(found.saved))
		# Translators: reported when a saved table layout would still not apply to this table after being
		# pointed at it, which a layout from a different kind of table can do.
		return _("That layout would still not apply to this table")

	def delete(self, index: int) -> None:
		if self.at(index) is None:
			return
		del self._layouts[index]
		self.changed = True

	def commit(self) -> bool:
		""":return: whether anything was written. Nothing is, when nothing was changed.

		:raises flowTableLayouts.LayoutsNotSaved: if the configuration would not take them, in which case
			the manager still holds its changes and says so.
		"""
		if not self.changed:
			return False
		flowTableLayouts.replaceAll(self._layouts)
		self.changed = False
		return True


def manageLayouts(handle, afterwards=None) -> bool:
	"""Put the manager up, over the table the reader is in if they are in one.

	**Opened from the event loop and not from the script**, for the reason `flowTableDesigner.arrangeTheTable`
	gives: a modal dialog shown inside a script never gives NVDA's queue back.

	:param handle: the table, or None.
	:param afterwards: called with no arguments after the reader presses OK and something was saved,
		so the display can take up what changed.
	:return: whether the manager is opening.
	"""
	if not CAN_DRAW:
		log.debugWarning("There is no wx to open the saved table layouts with")
		return False
	manager = LayoutManager(flowTableLayouts.stored(), hereFor(handle))
	wx.CallAfter(_show, manager, afterwards)
	return True


def _show(manager: LayoutManager, afterwards) -> None:
	try:
		gui.mainFrame.prePopup()
		try:
			dialog = LayoutManagerDialog(gui.mainFrame, manager)
			try:
				if dialog.ShowModal() != wx.ID_OK:
					return
			finally:
				dialog.Destroy()
		finally:
			gui.mainFrame.postPopup()
		try:
			committed = manager.commit()
		except flowTableLayouts.LayoutsNotSaved:
			gui.messageBox(
				# Translators: reported when the saved table layouts manager could not write its changes.
				_("The changes to saved table layouts could not be saved. See the NVDA log."),
				_("Saved table layouts"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		if committed and afterwards is not None:
			afterwards()
	except Exception:
		log.debugWarning("Could not manage the saved table layouts", exc_info=True)


CAN_DRAW = wx is not None and gui is not None and hasattr(wx, "Dialog")


if CAN_DRAW:  # pragma: no cover - a dialog needs a display.

	class LayoutManagerDialog(wx.Dialog):
		"""The saved layouts in a list, the details of the one selected, and what can be done to it.

		**The list says everything a reader choosing among them needs**, a line each, so arrowing it is
		enough to find the layout for this table or the ones for pages that are gone. The details field
		below says the rest of the one selected, and the controls under that change it.
		"""

		def __init__(self, parent, manager: LayoutManager) -> None:
			# Translators: the title of the dialog listing saved table layouts.
			super().__init__(parent, title=_("Saved table layouts"))
			self.manager = manager
			main = wx.BoxSizer(wx.VERTICAL)
			helper = guiHelper.BoxSizerHelper(self, sizer=main)
			self.hereCtrl = helper.addLabeledControl(
				# Translators: the label of what the saved table layouts manager says about the reader's table.
				_("This table:"),
				wx.TextCtrl,
				value=manager.hereWords(),
				style=wx.TE_MULTILINE | wx.TE_READONLY,
				size=(560, 70),
			)
			self.layoutList = helper.addLabeledControl(
				# Translators: the label of the list of saved table layouts.
				_("&Saved layouts:"),
				wx.ListBox,
				choices=[manager.label(index) for index in range(len(manager))],
				size=(560, 180),
			)
			self.layoutList.Bind(wx.EVT_LISTBOX, lambda event: self._showLayout())
			self.detailsCtrl = helper.addLabeledControl(
				# Translators: the label of the details of the selected saved table layout.
				_("&Details:"),
				wx.TextCtrl,
				style=wx.TE_MULTILINE | wx.TE_READONLY,
				size=(560, 130),
			)
			# Translators: the label of the field for a saved table layout's name.
			self.nameCtrl = helper.addLabeledControl(_("&Name:"), wx.TextCtrl)
			self.nameCtrl.Bind(wx.EVT_KILL_FOCUS, self._onName)
			# Translators: the label of the field for the address a saved table layout applies at.
			self.whereCtrl = helper.addLabeledControl(_("&Address:"), wx.TextCtrl, size=(560, -1))
			self.whereCtrl.Bind(wx.EVT_KILL_FOCUS, self._onWhere)
			# Translators: the label of the choice of how strictly a saved layout's address matches.
			self.matchCtrl = helper.addLabeledControl(_("&Match:"), wx.Choice, choices=[])
			self.matchCtrl.Bind(wx.EVT_CHOICE, self._onMatch)
			self.headingsCtrl = helper.addItem(
				# Translators: a checkbox for a saved table layout applying only to a table with its headings.
				wx.CheckBox(self, label=_("Only for a table with these &headings")),
			)
			self.headingsCtrl.Bind(wx.EVT_CHECKBOX, self._onHeadings)
			self.speakHeadersCtrl = helper.addLabeledControl(
				# Translators: the label of the choice of which of a saved layout's table's headers are spoken.
				_("Headers s&poken:"),
				wx.Choice,
				choices=[label for _speakHeaders, label in manager.speakHeadersChoices()],
			)
			self.speakHeadersCtrl.Bind(wx.EVT_CHOICE, self._onSpeakHeaders)
			buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
			# Translators: a button that points the selected saved layout at the reader's table.
			self.useButton = buttons.addButton(self, label=_("&Use for this table"))
			# Translators: a button that copies the selected saved layout and points the copy at the
			# reader's table.
			self.alsoButton = buttons.addButton(self, label=_("Also use for this &table"))
			# Translators: a button that deletes the selected saved table layout.
			self.deleteButton = buttons.addButton(self, label=_("De&lete"))
			self.useButton.Bind(wx.EVT_BUTTON, self._onUse)
			self.alsoButton.Bind(wx.EVT_BUTTON, self._onAlso)
			self.deleteButton.Bind(wx.EVT_BUTTON, self._onDelete)
			helper.addItem(buttons)
			helper.addDialogDismissButtons(wx.OK | wx.CANCEL)
			self.Bind(wx.EVT_BUTTON, self._onOk, id=wx.ID_OK)
			self.SetSizerAndFit(main)
			if len(manager):
				self.layoutList.SetSelection(0)
			self._showLayout()
			self.layoutList.SetFocus()

		def _index(self) -> int:
			return self.layoutList.GetSelection()

		def _refresh(self, select: Optional[int] = None) -> None:
			"""Say the list again, since one change can move which layout applies here."""
			at = self._index() if select is None else select
			self.layoutList.Set([self.manager.label(index) for index in range(len(self.manager))])
			if len(self.manager):
				self.layoutList.SetSelection(max(0, min(at, len(self.manager) - 1)))
			self.hereCtrl.SetValue(self.manager.hereWords())
			self._showLayout()

		def _showLayout(self) -> None:
			index = self._index()
			saved = self.manager.at(index)
			here = self.manager.here is not None
			for control in (
				self.nameCtrl,
				self.whereCtrl,
				self.matchCtrl,
				self.headingsCtrl,
				self.speakHeadersCtrl,
				self.deleteButton,
			):
				control.Enable(saved is not None)
			self.useButton.Enable(saved is not None and here)
			self.alsoButton.Enable(saved is not None and here)
			if saved is None:
				# Translators: said in place of details when there are no saved table layouts.
				self.detailsCtrl.SetValue(_("No table layouts are saved."))
				return
			self.detailsCtrl.SetValue(self.manager.details(index))
			self.nameCtrl.SetValue(self.manager.nameOf(saved))
			self.whereCtrl.SetValue(saved.where)
			choices = self.manager.matchChoices(index)
			self.matchCtrl.Set([label for _match, label in choices])
			self.matchCtrl.Enable(bool(choices))
			keys = [match for match, _label in choices]
			if saved.match in keys:
				self.matchCtrl.SetSelection(keys.index(saved.match))
			self.headingsCtrl.SetValue(saved.requireHeadings)
			self.headingsCtrl.Enable(any(saved.headings))
			ways = [speakHeaders for speakHeaders, _label in self.manager.speakHeadersChoices()]
			self.speakHeadersCtrl.SetSelection(ways.index(saved.tableLayout.speakHeaders))

		def _keepSelected(self, layoutId: str) -> None:
			self._refresh(self.manager.indexOf(layoutId))

		def _onName(self, event) -> None:
			event.Skip()
			saved = self.manager.at(self._index())
			if saved is None:
				return
			typed = self.nameCtrl.GetValue()
			if typed != self.manager.nameOf(saved):
				self.manager.rename(self._index(), typed)
				self._keepSelected(saved.id)

		def _onWhere(self, event) -> None:
			event.Skip()
			saved = self.manager.at(self._index())
			if saved is None or self.whereCtrl.GetValue().strip() == saved.where:
				return
			why = self.manager.setAddress(self._index(), self.whereCtrl.GetValue())
			if why:
				gui.messageBox(why, _("Saved table layouts"), wx.OK | wx.ICON_ERROR, self)
				self.whereCtrl.SetValue(saved.where)
				return
			self._keepSelected(saved.id)

		def _onMatch(self, event) -> None:
			saved = self.manager.at(self._index())
			choices = self.manager.matchChoices(self._index())
			chosen = self.matchCtrl.GetSelection()
			if saved is None or not 0 <= chosen < len(choices):
				return
			self.manager.setMatch(self._index(), choices[chosen][0])
			self._keepSelected(saved.id)

		def _onHeadings(self, event) -> None:
			saved = self.manager.at(self._index())
			if saved is None:
				return
			self.manager.setRequireHeadings(self._index(), self.headingsCtrl.GetValue())
			self._keepSelected(saved.id)

		def _onSpeakHeaders(self, event) -> None:
			saved = self.manager.at(self._index())
			choices = self.manager.speakHeadersChoices()
			chosen = self.speakHeadersCtrl.GetSelection()
			if saved is None or not 0 <= chosen < len(choices):
				return
			self.manager.setSpeakHeaders(self._index(), choices[chosen][0])
			self._keepSelected(saved.id)

		def _onUse(self, event) -> None:
			saved = self.manager.at(self._index())
			if saved is None:
				return
			why = self.manager.useHere(self._index())
			if why:
				gui.messageBox(why, _("Saved table layouts"), wx.OK | wx.ICON_ERROR, self)
				return
			self._keepSelected(saved.id)
			self.layoutList.SetFocus()

		def _onAlso(self, event) -> None:
			at, why = self.manager.alsoUseHere(self._index())
			if why:
				gui.messageBox(why, _("Saved table layouts"), wx.OK | wx.ICON_ERROR, self)
				return
			if at >= 0:
				self._refresh(at)
				self.layoutList.SetFocus()

		def _onDelete(self, event) -> None:
			saved = self.manager.at(self._index())
			if saved is None:
				return
			if (
				gui.messageBox(
					# Translators: asked before deleting a saved table layout. The placeholder is its name.
					_("Delete the saved layout {name}?").format(name=self.manager.nameOf(saved)),
					_("Saved table layouts"),
					wx.YES_NO | wx.ICON_QUESTION,
					self,
				)
				!= wx.YES
			):
				return
			at = self._index()
			self.manager.delete(at)
			self._refresh(at)
			self.layoutList.SetFocus()

		def _onOk(self, event) -> None:
			"""Take what is typed in the name and address fields, which only commit when focus leaves them."""
			saved = self.manager.at(self._index())
			if saved is not None:
				if self.nameCtrl.GetValue() != self.manager.nameOf(saved):
					self.manager.rename(self._index(), self.nameCtrl.GetValue())
				if self.whereCtrl.GetValue().strip() != saved.where:
					why = self.manager.setAddress(self._index(), self.whereCtrl.GetValue())
					if why:
						gui.messageBox(why, _("Saved table layouts"), wx.OK | wx.ICON_ERROR, self)
						self.whereCtrl.SetFocus()
						return
			event.Skip()

else:

	class LayoutManagerDialog:  # type: ignore[no-redef]
		"""What the manager is where there is no wx to build one with."""

		def __init__(self, parent, manager: LayoutManager) -> None:
			raise RuntimeError("The saved table layouts manager needs wx")


__all__ = ["Here", "LayoutManager", "LayoutManagerDialog", "hereFor", "manageLayouts"]
