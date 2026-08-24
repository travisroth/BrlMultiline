# BrlMultiline: settings panel.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""NVDA settings categories for BrlMultiline.

Three of them, because they are answers to different questions.

L{BrailleMultilineSettingsPanel} arranges the display that is connected now, and stores
against it. When that display is several physical displays combined, division is asked of
each of them separately, since a segment may not straddle two pieces of hardware — so the
panel gains a chooser, and everything under it applies to the chosen display.

L{FlowSettingsPanel} turns spatial reading on, and says which kinds of content it applies
to. That is a question about how content is presented rather than about how the display is
divided, and the reader's answer to it changes from one application to the next, so it has
a category of its own where a configuration profile is the obvious thing to reach for.

L{VirtualDisplaySettingsPanel} chooses which physical displays are combined, and in what
order. That is a standing arrangement rather than a property of what is connected, so it is
stored on its own and is editable whether or not the combined display is in use.

All three write through `config.conf`, so every setting here belongs to the configuration
profile being edited. NVDA says which that is in the dialog's own title.
"""

from typing import NamedTuple

import addonHandler
import gui
import wx
from gui import guiHelper
from logHandler import log

from . import bmConfig, devices, flowIndent
from .layout import calculateSegmentRects

addonHandler.initTranslation()


def indentStyleChoices() -> tuple[tuple[str, str], ...]:
	""":return: the indent styles and their labels, in the order the dialog offers them.

	A function rather than a constant because the labels are translated, and a constant
	built at import time is translated once into whatever language was current then. Ordered
	by `flowIndent.INDENT_STYLES` so that the one definition of what a style is decides what
	the dialog shows, and a style added there without a label here is offered by its name
	rather than silently dropped.
	"""
	labels = {
		# Translators: a choice of how the depth of a tree item is shown in braille.
		flowIndent.TWO_SPACES: _("Two spaces for each level"),
		# Translators: a choice of how the depth of a tree item is shown in braille.
		flowIndent.ONE_SPACE: _("One space for each level"),
		# Translators: a choice of how the depth of a tree item is shown in braille. Dots 7
		# and 8 are the two lower dots of an eight dot braille cell.
		flowIndent.DOTS_78: _("Dots 7 and 8 for each level"),
	}
	return tuple((style, labels.get(style, style)) for style in flowIndent.INDENT_STYLES)


def parseSegmentSizes(text: str) -> list[int]:
	"""Parse a user typed list of segment sizes.

	:param text: sizes separated by commas or spaces. Empty means divide evenly.
	:return: the sizes, or an empty list if none were given.
	:raises ValueError: if the text is not a list of positive whole numbers.
	"""
	cleaned = text.replace(",", " ").split()
	if not cleaned:
		return []
	sizes = []
	for item in cleaned:
		try:
			size = int(item)
		except ValueError:
			raise ValueError(
				# Translators: reported when the segment sizes typed in settings are not numbers.
				# The placeholder is replaced with what the user typed.
				_("{value} is not a whole number.").format(value=item),
			) from None
		if size < 1:
			raise ValueError(
				# Translators: reported when a segment size typed in settings is zero or negative.
				_("Every segment must be at least 1."),
			)
		sizes.append(size)
	return sizes


def reportSettingsError(panel, message: str, control) -> None:
	"""Tell the reader why a setting was refused, and put them on the control it was about.

	The focus move is the point: a message box saying what is wrong, followed by the reader
	having to find the field again, is a message box that has only half worked.

	:param panel: the settings category the error is about, which owns the dialog.
	:param message: what is wrong, as the reader should hear it.
	:param control: the control to put the focus on.
	"""
	gui.messageBox(
		message,
		# Translators: title of an error dialog shown when settings cannot be applied.
		_("BrlMultiline settings"),
		wx.OK | wx.ICON_ERROR,
		panel,
	)
	control.SetFocus()


def displayDescriptions() -> dict[str, str]:
	"""Read the name NVDA shows for each braille display driver.

	Every driver is listed, including those whose hardware is not connected, because a member
	of a combined display is chosen once and used whenever it is switched on. A driver list
	that hid a display that happened to be off would be a list the user could not finish.

	:return: driver name to description. Empty if NVDA would not say, in which case callers
		fall back to the driver names, which are at least unique.
	"""
	try:
		from braille.display import getDisplayList

		return dict(getDisplayList(excludeNegativeChecks=False))
	except Exception:
		log.debugWarning("BrlMultiline: could not read the braille display list", exc_info=True)
		return {}


class _DivisionTarget(NamedTuple):
	"""One display the segment settings can be edited for."""

	displayKey: str
	"""Its configuration key."""

	label: str
	"""What to call it in the chooser."""

	description: str
	"""What to call it in an error message."""

	numRows: int
	numCols: int

	@property
	def dividesByRows(self) -> bool:
		""":return: whether its segment sizes are measured in rows rather than in cells."""
		return self.numRows > 1


class BrailleMultilineSettingsPanel(gui.settingsDialogs.SettingsPanel):
	# Translators: title of the BrlMultiline settings category in NVDA's settings.
	title = _("BrlMultiline")

	panelDescription = _(
		# Translators: description of the BrlMultiline settings category, announced with it.
		"Divide the braille display into segments, each showing something of its own.",
	)
	"""What NVDA announces about this category beyond its name.

	`SettingsPanelAccessible.GetDescription` reads this, and a panel that leaves it empty
	announces nothing but its title — which is what a hardware run heard as an empty panel in
	the tab order. NVDA's own categories set it; see `gui.settingsDialogs`.
	"""

	def makeSettings(self, settingsSizer):
		sHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self.displayKey = bmConfig.getDisplayKey()
		section = bmConfig.getDisplayConfig(self.displayKey)
		import braille

		dimensions = braille.handler.displayDimensions
		self.numRows = dimensions.numRows
		self.numCols = dimensions.numCols
		self.devices = devices.deviceMap()
		self.targets = self._divisionTargets()
		self.pending = {target.displayKey: self._readTarget(target.displayKey) for target in self.targets}
		"""The unsaved segment settings of every display this panel can edit.

		The controls show one display at a time, so edits to the others have to live
		somewhere until the dialog is closed. Held here rather than written straight to the
		configuration, so that cancelling the dialog still cancels everything.
		"""
		self.targetIndex = 0

		if self.devices:
			sHelper.addItem(
				wx.StaticText(
					self,
					label=_(
						# Translators: shown in settings when the display is several displays
						# combined. Placeholders are the number of displays, rows and columns.
						"This display is {count} displays combined, {rows} rows of {columns} cells. "
						"Each display is divided into segments of its own.",
					).format(count=len(self.devices), rows=self.numRows, columns=self.numCols),
				),
			)
			# Translators: label of a combo box in settings, choosing which of the combined
			# displays the segment settings below apply to.
			targetLabel = _("Segment settings &for:")
			self.targetCtrl = sHelper.addLabeledControl(
				targetLabel,
				wx.Choice,
				choices=[target.label for target in self.targets],
			)
			self.targetCtrl.SetSelection(0)
			self.targetCtrl.Bind(wx.EVT_CHOICE, self._onTargetChanged)
		else:
			self.targetCtrl = None
			sHelper.addItem(
				wx.StaticText(
					self,
					# Translators: shown in settings to say which display the settings apply to.
					# Placeholders are the display name, number of rows and number of columns.
					label=_("These settings apply to: {display} ({rows} rows of {columns} cells)").format(
						display=self.displayKey,
						rows=self.numRows,
						columns=self.numCols,
					),
				),
			)

		# Translators: label of a checkbox in settings.
		enabledLabel = _("&Divide this display into segments")
		self.segmentsEnabledCtrl = sHelper.addItem(wx.CheckBox(self, label=enabledLabel))
		# Translators: label of a spin control in settings.
		segmentCountLabel = _("Number of &segments:")
		self.segmentCountCtrl = sHelper.addLabeledControl(
			segmentCountLabel,
			wx.SpinCtrl,
			min=1,
			max=bmConfig.MAX_UI_SEGMENTS,
			initial=1,
		)
		# Translators: label of an edit field in settings. Whether the sizes are rows or cells
		# depends on the display, and is said by the line below the field.
		sizesLabel = _("Segment si&zes, separated by commas (blank to divide evenly):")
		self.segmentSizesCtrl = sHelper.addLabeledControl(sizesLabel, wx.TextCtrl)
		self.sizesHintCtrl = sHelper.addItem(wx.StaticText(self, label=""))
		# Above the line, with the chooser, because reversal belongs to the keys of one
		# physical display rather than to the arrangement as a whole. Placed below the
		# chooser but saved against the connected display, it read as though it followed the
		# chooser and did not.
		# Translators: label of a checkbox in settings.
		reverseLabel = _("&Reverse the panning keys on this display")
		self.reverseScrollCtrl = sHelper.addItem(wx.CheckBox(self, label=reverseLabel))
		self._showTarget(0)

		if self.devices:
			# Translators: label of a spin control in settings, on a display made of several
			# displays. Segments are numbered through all of them. -1 means the last segment.
			focusLabel = _("Segment that &follows the focus, counted across every display (-1 for the last):")
		else:
			# Translators: label of a spin control in settings. -1 means the last segment.
			focusLabel = _("Segment that &follows the focus (-1 for the last):")
		self.focusSegmentCtrl = sHelper.addLabeledControl(
			focusLabel,
			wx.SpinCtrl,
			min=-1,
			max=self._highestSegmentNumber(),
			initial=int(section["focusSegment"]),
		)
		if self.devices:
			messageLabel = _(
				# Translators: label of a spin control in settings, on a display made of several
				# displays. -1 means wherever the focus is, which is where messages appear on an
				# undivided display.
				"Segment that flash &messages appear in, counted across every display "
				"(-1 to follow the focus):",
			)
		else:
			# Translators: label of a spin control in settings. -1 means wherever the focus is.
			messageLabel = _("Segment that flash &messages appear in (-1 to follow the focus):")
		self.messageSegmentCtrl = sHelper.addLabeledControl(
			messageLabel,
			wx.SpinCtrl,
			min=-1,
			max=self._highestSegmentNumber(),
			initial=int(section["messageSegment"]),
		)
		# Translators: label of a checkbox in settings.
		documentLinesLabel = _("Show the &document lines around the caret in the other segments")
		self.documentLinesCtrl = sHelper.addItem(wx.CheckBox(self, label=documentLinesLabel))
		self.documentLinesCtrl.SetValue(bool(section["showDocumentLines"]))

	def _highestSegmentNumber(self) -> int:
		""":return: the largest segment number the spin controls should offer.

		Segments are numbered across the whole display, so on a composite the count to
		bound by is the sum of what each display behind it may be divided into rather than
		what one of them may. A control bounded by the latter cannot express the last
		segment of a tall composite, which is a number the reader can perfectly well
		reach — the display relative commands and the command that moves the focus from
		one display to another both produce it.

		This is a bound on what can be typed, not on what is right: L{isValid} checks the
		number against the segments the configured layout actually has.
		"""
		return bmConfig.MAX_UI_SEGMENTS * max(1, len(self.devices)) - 1

	# Choosing which display the segment settings apply to

	def _divisionTargets(self) -> list[_DivisionTarget]:
		""":return: the displays whose division this panel can edit.

		The connected display itself, or, when it is several displays combined, each of those.
		The combined display is not among them: it has no segment count of its own, because a
		segment may not straddle two pieces of hardware, so it is always divided at the
		boundaries between them.
		"""
		if not self.devices:
			return [
				_DivisionTarget(
					displayKey=self.displayKey,
					label=self.displayKey,
					description=self.displayKey,
					numRows=self.numRows,
					numCols=self.numCols,
				),
			]
		names = displayDescriptions()
		targets = []
		for device in self.devices:
			description = names.get(device.driverName, device.driverName)
			targets.append(
				_DivisionTarget(
					displayKey=device.displayKey,
					# Translators: an entry in the settings chooser of which combined display
					# to edit. Placeholders are its name, its first and last row on the
					# combined display, counted from 1, and how many cells wide it is.
					label=_("{display}, rows {first} to {last}, {columns} cells wide").format(
						display=description,
						first=device.rowStart + 1,
						last=device.rowEnd,
						columns=device.numCols,
					),
					description=description,
					numRows=device.numRows,
					numCols=device.numCols,
				),
			)
		return targets

	def _readTarget(self, displayKey: str) -> dict:
		""":return: one display's stored segment settings, as this panel holds them.

		Sizes are kept as the user's own text rather than as numbers, so that text which does
		not parse is reported against the display it was typed for rather than silently lost
		on the way to another one.
		"""
		section = bmConfig.getDisplayConfig(displayKey)
		return {
			"segmentsEnabled": bool(section["segmentsEnabled"]),
			"segmentCount": int(section["segmentCount"]),
			"segmentSizes": ", ".join(str(size) for size in section["segmentSizes"]),
			"reverseScrollBtns": bool(section["reverseScrollBtns"]),
		}

	def _stashTarget(self) -> None:
		"""Take what the controls are showing and hold it against the display it is for."""
		values = self.pending[self.targets[self.targetIndex].displayKey]
		values["segmentsEnabled"] = self.segmentsEnabledCtrl.IsChecked()
		values["segmentCount"] = self.segmentCountCtrl.Value
		values["segmentSizes"] = self.segmentSizesCtrl.Value
		values["reverseScrollBtns"] = self.reverseScrollCtrl.IsChecked()

	def _showTarget(self, index: int) -> None:
		"""Put one display's held settings into the controls.

		:param index: which of L{targets} to show.
		"""
		self.targetIndex = index
		target = self.targets[index]
		values = self.pending[target.displayKey]
		self.segmentsEnabledCtrl.SetValue(values["segmentsEnabled"])
		self.segmentCountCtrl.SetValue(values["segmentCount"])
		self.segmentSizesCtrl.Value = values["segmentSizes"]
		self.reverseScrollCtrl.SetValue(values["reverseScrollBtns"])
		if target.dividesByRows:
			# Translators: shown in settings under the segment sizes field, on a display with
			# more than one row. The placeholder is the number of rows it has.
			hint = _("Sizes are in rows. This display has {rows} rows.").format(rows=target.numRows)
		else:
			# Translators: shown in settings under the segment sizes field, on a display with
			# a single row. The placeholder is the number of cells it has.
			hint = _("Sizes are in cells. This display has {columns} cells.").format(
				columns=target.numCols,
			)
		self.sizesHintCtrl.SetLabel(hint)

	def _onTargetChanged(self, event) -> None:
		"""Switch the controls to another of the combined displays, keeping the edits made."""
		self._stashTarget()
		self._showTarget(self.targetCtrl.GetSelection())

	def _selectTarget(self, index: int) -> None:
		"""Show a display because something about it needs the user's attention."""
		if self.targetCtrl is not None:
			self.targetCtrl.SetSelection(index)
		self._showTarget(index)

	# Saving

	def isValid(self) -> bool:
		self._stashTarget()
		anyEnabled = False
		totalSegments = 0
		for index, target in enumerate(self.targets):
			values = self.pending[target.displayKey]
			try:
				sizes = parseSegmentSizes(values["segmentSizes"])
			except ValueError as error:
				# Checked whether or not the layout is in use, since it is stored either way
				# and nothing should be able to put text into the configuration that cannot be
				# read back.
				self._selectTarget(index)
				self._reportError(self._forTarget(target, str(error)), self.segmentSizesCtrl)
				return False
			if not values["segmentsEnabled"]:
				# The layout is not going to be used, so whether it fits this display is not a
				# reason to refuse the save. It matters when a display has been replaced by a
				# smaller one: turning division off is exactly what the user would reach for,
				# and holding them to a layout that no longer fits would stop them doing it.
				totalSegments += 1
				continue
			anyEnabled = True
			layout = sizes if sizes else values["segmentCount"]
			try:
				rects = calculateSegmentRects(target.numRows, target.numCols, layout)
			except ValueError as error:
				self._selectTarget(index)
				self._reportError(
					self._forTarget(target, str(error)),
					self.segmentSizesCtrl if sizes else self.segmentCountCtrl,
				)
				return False
			totalSegments += len(rects)
		if not anyEnabled and len(self.targets) == 1:
			# An ordinary display with division turned off has one segment, and the focus
			# setting is not going to be read. A composite is different: it is divided at the
			# hardware boundaries whatever the switches say, so it still has one segment per
			# display and the focus number still has to name one of them.
			return True
		for control in (self.focusSegmentCtrl, self.messageSegmentCtrl):
			if control.Value >= totalSegments:
				self._reportError(
					_(
						# Translators: reported when a chosen segment number does not exist.
						# Placeholders are the chosen number and the number of segments configured.
						"There is no segment {chosen}; this layout has {count} segments, numbered from 0.",
					).format(chosen=control.Value, count=totalSegments),
					control,
				)
				return False
		return True

	def _forTarget(self, target: _DivisionTarget, message: str) -> str:
		""":return: an error message saying which display it is about, when there are several."""
		if len(self.targets) == 1:
			return message
		# Translators: an error in settings, said of one of several combined displays.
		# Placeholders are the display's name and the problem.
		return _("{display}: {problem}").format(display=target.description, problem=message)

	def _reportError(self, message: str, control: wx.Window) -> None:
		reportSettingsError(self, message, control)

	def onSave(self):
		self._stashTarget()
		for target in self.targets:
			values = self.pending[target.displayKey]
			try:
				sizes = parseSegmentSizes(values["segmentSizes"])
			except ValueError:
				# isValid has already vetted this; keep whatever was stored before.
				log.debugWarning("Segment sizes could not be parsed while saving", exc_info=True)
				continue
			section = bmConfig.getDisplayConfig(target.displayKey)
			section["segmentsEnabled"] = values["segmentsEnabled"]
			section["segmentCount"] = values["segmentCount"]
			section["segmentSizes"] = sizes
			# Per display, because the panning keys are on one piece of hardware and sit
			# differently on each. See `panning`.
			section["reverseScrollBtns"] = values["reverseScrollBtns"]
		# The rest belong to the display as a whole rather than to one of the displays behind
		# it: there is one focus, one place for messages, and one reading order.
		section = bmConfig.getDisplayConfig(self.displayKey)
		section["focusSegment"] = self.focusSegmentCtrl.Value
		section["messageSegment"] = self.messageSegmentCtrl.Value
		section["showDocumentLines"] = self.documentLinesCtrl.IsChecked()

	def postSave(self):
		# Rebuild the display with the new layout straight away.
		from . import getPlugin

		plugin = getPlugin()
		if plugin is not None:
			plugin.rebuildBuffer()


FLOW_MODE_LABELS = {
	# Translators: label of a checkbox in the flow settings, naming the kind of content the
	# flow is used for. Browse mode is what NVDA calls its reading of a web page.
	"browseMode": _("&Browse mode: web pages, and documents NVDA reads like one"),
	# Translators: label of a checkbox in the flow settings, naming the kind of content the
	# flow is used for.
	"objects": _("&Lists and menus: the items around the one you are on"),
	# Translators: label of a checkbox in the flow settings, naming the kind of content the
	# flow is used for.
	"editableText": _("&Editable text: editors and fields outside browse mode"),
}
"""What to call each kind of content in the dialog, keyed as `bmConfig.FLOW_MODES` names them.

A kind with no label here is shown by its name, so that adding one to `FLOW_MODES` cannot
leave a checkbox the reader cannot see.
"""


class _BandTarget(NamedTuple):
	"""One place the flow band can be put."""

	driverName: str
	"""The display's driver, or empty for "whichever is tallest"."""

	label: str
	"""What to call it in the chooser."""

	numRows: int
	"""How many rows it has, which is the most the band can use."""


class FlowSettingsPanel(gui.settingsDialogs.SettingsPanel):
	"""Turning spatial reading on, and saying what it applies to."""

	# Translators: title of the settings category for BrlMultiline's flowing reading.
	title = _("BrlMultiline flow")

	panelDescription = _(
		# Translators: description of the flow settings category, announced with it and shown
		# at the top of it.
		"Read a band of the display as one flowing document, so that a heading is followed by "
		"what comes after it and a paragraph runs on across the rows, rather than one line "
		"being shown at a time. "
		"These settings belong to the configuration profile being edited, so a profile for one "
		"application can read as a flow while the normal configuration goes on as before.",
	)
	"""What NVDA announces about this category, and the paragraph at the top of it.

	One string serving both, as `VirtualDisplaySettingsPanel` does: the arrangement NVDA
	uses for a category that needs explaining.
	"""

	def makeSettings(self, settingsSizer):
		sHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self.displayKey = bmConfig.getDisplayKey()
		section = bmConfig.getDisplayConfig(self.displayKey)
		self.devices = devices.deviceMap()
		self.targets = self._bandTargets()
		sHelper.addItem(wx.StaticText(self, label=self.panelDescription))
		# Translators: label of a checkbox in settings, turning the flowing reading on.
		enabledLabel = _("&Read this display as a flowing document")
		self.enabledCtrl = sHelper.addItem(wx.CheckBox(self, label=enabledLabel))
		self.enabledCtrl.SetValue(bool(section["flowEnabled"]))
		sHelper.addItem(
			wx.StaticText(
				self,
				# Translators: shown in settings above the list of kinds of content that flow.
				label=_("Use it for:"),
			),
		)
		self.modeCtrls = {}
		for mode in bmConfig.FLOW_MODES:
			control = sHelper.addItem(wx.CheckBox(self, label=FLOW_MODE_LABELS.get(mode, mode)))
			control.SetValue(bool(section[bmConfig.flowModeKey(mode)]))
			self.modeCtrls[mode] = control
		if len(self.targets) > 1:
			# Translators: label of a combo box in settings, choosing which of several combined
			# displays the flow appears on.
			displayLabel = _("&Display the flow appears on:")
			self.bandDisplayCtrl = sHelper.addLabeledControl(
				displayLabel,
				wx.Choice,
				choices=[target.label for target in self.targets],
			)
			self.bandDisplayCtrl.SetSelection(self._targetIndex(str(section["flowDisplay"] or "")))
			self.bandDisplayCtrl.Bind(wx.EVT_CHOICE, self._onBandDisplayChanged)
		else:
			self.bandDisplayCtrl = None
		# Translators: label of a spin control in settings, saying how tall the flow band is.
		rowsLabel = _("Ro&ws it uses, counted from the top (0 for all of them):")
		self.rowsCtrl = sHelper.addLabeledControl(
			rowsLabel,
			wx.SpinCtrl,
			min=0,
			max=bmConfig.MAX_FLOW_ROWS,
			initial=int(section["flowRows"]),
		)
		self.rowsHintCtrl = sHelper.addItem(wx.StaticText(self, label=""))
		# Translators: label of a checkbox in settings. Quick navigation keys are the single
		# letters that move to the next heading, table or landmark in browse mode.
		groundLabel = _("Start reading afresh from what a &quick navigation key found")
		self.groundCtrl = sHelper.addItem(wx.CheckBox(self, label=groundLabel))
		self.groundCtrl.SetValue(bool(section["flowGroundOnQuickNav"]))
		# Translators: label of a checkbox in settings. It governs how the text of an edit box
		# the user is typing in is divided up for the flow.
		paragraphLabel = _("In a multi-line edit &you are typing in, read a paragraph at a time")
		self.writeByParagraphCtrl = sHelper.addItem(wx.CheckBox(self, label=paragraphLabel))
		self.writeByParagraphCtrl.SetValue(bool(section["flowWriteByParagraph"]))
		sHelper.addItem(
			wx.StaticText(
				self,
				label=_(
					# Translators: shown in settings under the checkbox above, explaining what it
					# does. Read by paragraph is a setting of NVDA's own braille settings.
					"On, a paragraph is what you typed rather than what the box's wrapping made "
					"of it. Off, an edit is read the same way a page is, by NVDA's own read by "
					"paragraph setting.",
				),
			),
		)
		# Translators: label of a combo box in settings, choosing how the depth of an item in a
		# tree or a nested list is shown on the display.
		indentLabel = _("Show how deep an item &sits with:")
		self.indentStyleCtrl = sHelper.addLabeledControl(
			indentLabel,
			wx.Choice,
			choices=[label for _name, label in indentStyleChoices()],
		)
		self.indentStyleCtrl.SetSelection(self._indentStyleIndex(str(section["flowIndentStyle"] or "")))
		sHelper.addItem(
			wx.StaticText(
				self,
				label=_(
					# Translators: shown in settings under the combo box above, explaining what it
					# does. Dots 7 and 8 are the two lower dots of an eight dot braille cell.
					"Only where a control says how deep its items are, such as a tree view. The "
					"display shows depth relative to the shallowest item on it, so a deep tree "
					"still leaves cells for the item itself.",
				),
			),
		)
		# Translators: label of a checkbox in settings. Dots 3, 6, 7 and 8 are the bottom four
		# dots of an eight dot braille cell, drawn in the two cells at the left of a row.
		lineFocusLabel = _("&Mark the item you are on with dots 3678 at the left of its row")
		self.lineFocusCtrl = sHelper.addItem(wx.CheckBox(self, label=lineFocusLabel))
		self.lineFocusCtrl.SetValue(bool(section["flowLineFocus"]))
		sHelper.addItem(
			wx.StaticText(
				self,
				label=_(
					# Translators: shown in settings under the checkbox above, explaining what it
					# does and where it does nothing.
					"The mark is in the same place on every row, so which item you are on can be "
					"found by running a hand down the left of the display rather than reading "
					"each row. It is drawn in the item's own indent, so it costs no space, and "
					"there is none to draw it in on an item at the left margin.",
				),
			),
		)
		self._updateRowsHint()

	# Where the band goes.

	def _bandTargets(self) -> list[_BandTarget]:
		""":return: the places the band can be put, the automatic choice first.

		A band must lie inside one physical display's live cells, so on a display made of
		several there is a choice to make and the reader may want it made for them. On an
		ordinary display there is nothing to choose and the list holds the one answer, which
		is what leaves the chooser out of the dialog.
		"""
		import braille

		dimensions = braille.handler.displayDimensions
		if not self.devices:
			return [_BandTarget(driverName="", label=self.displayKey, numRows=dimensions.numRows)]
		names = displayDescriptions()
		tallest = devices.preferredDevice("", self.devices)
		targets = [
			_BandTarget(
				driverName="",
				# Translators: the automatic entry in the settings chooser of which combined
				# display the flow appears on. The placeholder is the display chosen for it.
				label=_("Whichever has the most rows (now {display})").format(
					display=names.get(tallest.driverName, tallest.driverName),
				),
				numRows=tallest.numRows,
			),
		]
		for device in self.devices:
			targets.append(
				_BandTarget(
					driverName=device.driverName,
					# Translators: an entry in the settings chooser of which combined display the
					# flow appears on. Placeholders are its name and how many rows it has.
					label=_("{display}, {rows} rows").format(
						display=names.get(device.driverName, device.driverName),
						rows=device.numRows,
					),
					numRows=device.numRows,
				),
			)
		return targets

	def _indentStyleIndex(self, style: str) -> int:
		""":return: which entry of the chooser a stored style means, 0 for the default.

		A style this version has not got reads as the default, which is what
		`bmConfig.flowIndentStyle` will actually use for it. The dialog must agree with the
		band, or saving without touching the control would quietly change the setting.
		"""
		for index, (name, _label) in enumerate(indentStyleChoices()):
			if name == style:
				return index
		return 0

	def _targetIndex(self, driverName: str) -> int:
		""":return: which entry of the chooser a stored driver name means, 0 for automatic."""
		for index, target in enumerate(self.targets):
			if target.driverName == driverName:
				return index
		# Configured for a display that is not among these. Automatic is the honest answer:
		# it is what the band will actually do until that display comes back.
		return 0

	def _chosenTarget(self) -> _BandTarget:
		""":return: the display the band would be put on now."""
		if self.bandDisplayCtrl is None:
			return self.targets[0]
		index = self.bandDisplayCtrl.GetSelection()
		return self.targets[index] if 0 <= index < len(self.targets) else self.targets[0]

	def _onBandDisplayChanged(self, event) -> None:
		self._updateRowsHint()

	def _updateRowsHint(self) -> None:
		"""Say how many rows the chosen display has, since that is what bounds the band."""
		target = self._chosenTarget()
		if target.numRows > 1:
			# Translators: shown in settings under the number of rows the flow uses. The
			# placeholder is how many rows the display it appears on has.
			hint = _("That display has {rows} rows.").format(rows=target.numRows)
		else:
			hint = _(
				# Translators: shown in settings when the display the flow would appear on has
				# only one row, where a flow can show no more than an ordinary segment.
				"That display has one row, so a flow shows no more of the document than a single line does.",
			)
		self.rowsHintCtrl.SetLabel(hint)

	# Saving.

	def isValid(self) -> bool:
		if not self.enabledCtrl.IsChecked():
			# Not going to be used, so a band that does not fit is not a reason to refuse the
			# save. It matters when a display has been replaced by a smaller one: turning the
			# flow off is what the reader would reach for, and holding them to a band that no
			# longer fits would stop them doing it.
			return True
		target = self._chosenTarget()
		if self.rowsCtrl.Value > target.numRows:
			reportSettingsError(
				self,
				_(
					# Translators: reported when the flow is asked for more rows than the display
					# it appears on has. Placeholders are the number asked for and the number of
					# rows that display has.
					"The flow cannot use {chosen} rows; that display has {rows}. Use 0 for all of them.",
				).format(chosen=self.rowsCtrl.Value, rows=target.numRows),
				self.rowsCtrl,
			)
			return False
		return True

	def onSave(self):
		section = bmConfig.getDisplayConfig(self.displayKey)
		section["flowEnabled"] = self.enabledCtrl.IsChecked()
		for mode, control in self.modeCtrls.items():
			section[bmConfig.flowModeKey(mode)] = control.IsChecked()
		section["flowDisplay"] = self._chosenTarget().driverName
		section["flowRows"] = self.rowsCtrl.Value
		section["flowGroundOnQuickNav"] = self.groundCtrl.IsChecked()
		section["flowWriteByParagraph"] = self.writeByParagraphCtrl.IsChecked()
		section["flowIndentStyle"] = indentStyleChoices()[self.indentStyleCtrl.GetSelection()][0]
		section["flowLineFocus"] = self.lineFocusCtrl.IsChecked()

	def postSave(self):
		# Claim or give back the band straight away, rather than at the next display event.
		from . import getPlugin

		plugin = getPlugin()
		if plugin is not None:
			plugin.rebuildBuffer()


class VirtualDisplaySettingsPanel(gui.settingsDialogs.SettingsPanel):
	"""Choosing which physical displays are driven as one, and in what order."""

	# Translators: title of the settings category for combining several braille displays.
	title = _("BrlMultiline displays")

	panelDescription = _(
		# Translators: description of the settings category for combining several braille
		# displays, announced with it and shown at the top of it.
		"Several braille displays can be driven as one, stacked one above another. "
		"Choose them here, then choose "
		'"BrlMultiline: several displays as one"'
		" in NVDA's Braille settings. "
		"Leaving this list empty turns the combined display off.",
	)
	"""What NVDA announces about this category, and the paragraph at the top of it.

	One string serving both, which is NVDA's own arrangement for a category that needs
	explaining: `SettingsPanelAccessible.GetDescription` reads this attribute, and the visible
	text is added from it in `makeSettings`.
	"""

	_states: dict[str, bool] | None = None
	"""Which chosen displays the composite is driving, or None if it is not the display in use.

	Read once, when the category is opened: this reports what the running display has, and
	nothing in this panel changes that. None until then, and None whenever there is no composite
	to ask, in which case the list says nothing about any display's state rather than guessing.
	"""

	def makeSettings(self, settingsSizer):
		sHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self.descriptions = displayDescriptions()
		self._states = devices.memberStates()
		self.storedSpecs = self._readDevices()
		"""What each chosen display was configured as, keyed on driver name.

		Kept so that a port set outside this panel survives being reordered here. The panel
		offers no port control, because members are detected afresh, but a value it cannot
		show is not a value it may quietly discard.
		"""
		self.chosen = list(self.storedSpecs)
		self._original = list(self.chosen)
		sHelper.addItem(wx.StaticText(self, label=self.panelDescription))
		# Translators: label of a list box in settings, holding the chosen braille displays.
		chosenLabel = _("Displays, &top to bottom:")
		self.chosenCtrl = sHelper.addLabeledControl(chosenLabel, wx.ListBox, choices=[])
		orderButtons = guiHelper.ButtonHelper(wx.HORIZONTAL)
		# Translators: label of a button in settings, moving a display up the list.
		self.moveUpButton = orderButtons.addButton(self, label=_("Move &up"))
		# Translators: label of a button in settings, moving a display down the list.
		self.moveDownButton = orderButtons.addButton(self, label=_("Move dow&n"))
		# Translators: label of a button in settings, taking a display out of the list.
		self.removeButton = orderButtons.addButton(self, label=_("&Remove"))
		sHelper.addItem(orderButtons)
		self.moveUpButton.Bind(wx.EVT_BUTTON, self._onMoveUp)
		self.moveDownButton.Bind(wx.EVT_BUTTON, self._onMoveDown)
		self.removeButton.Bind(wx.EVT_BUTTON, self._onRemove)
		self.chosenCtrl.Bind(wx.EVT_LISTBOX, self._onSelectionChanged)
		# Translators: label of a combo box in settings, of displays that may be added.
		addLabel = _("Display to &add:")
		self.availableCtrl = sHelper.addLabeledControl(addLabel, wx.Choice, choices=[])
		addButtons = guiHelper.ButtonHelper(wx.HORIZONTAL)
		# Translators: label of a button in settings, adding a display to the list.
		self.addButton = addButtons.addButton(self, label=_("&Add"))
		sHelper.addItem(addButtons)
		self.addButton.Bind(wx.EVT_BUTTON, self._onAdd)
		sHelper.addItem(
			wx.StaticText(
				self,
				label=_(
					# Translators: a note at the bottom of the settings category for combining
					# braille displays.
					"Each display is detected afresh when it is opened, so no port is chosen here. "
					"One display per driver: two displays of the same make cannot be told apart. "
					"A display is shown as in use while the combined display is the one in use; "
					"one that stops responding is noticed when something is next written to it.",
				),
			),
		)
		self._refresh()

	def _readDevices(self) -> dict:
		""":return: the configured members, keyed on driver name, in stacking order.

		A dictionary rather than a list because Python keeps insertion order, so this is the
		stacking order and the lookup at once.
		"""
		try:
			from brailleDisplayDrivers.brlMultilineVirtual import vdConfig

			return {spec.driverName: spec for spec in vdConfig.getDevices()}
		except Exception:
			# A list that cannot be read is shown as empty rather than refusing to open the
			# panel, since the panel is where it would be put right.
			log.error("BrlMultiline: could not read the combined display's device list", exc_info=True)
			return {}

	def _describe(self, driverName: str) -> str:
		""":return: what to call a driver in the list."""
		return self.descriptions.get(driverName, driverName)

	def _describeChosen(self, driverName: str) -> str:
		""":return: what to call a chosen display, with what is known about it now.

		Only the running composite knows which of these it opened, so when something else is
		the display in use the names are given plainly rather than with a state that would be
		a guess.
		"""
		name = self._describe(driverName)
		if self._states is None:
			return name
		if driverName not in self._states:
			# Configured, and the composite is running without it: it was not there, or would
			# not open, when braille started.
			# Translators: a display in the combined display's list that is not being used.
			return _("{display}: not connected").format(display=name)
		if not self._states[driverName]:
			# Translators: a display in the combined display's list that has stopped working.
			return _("{display}: not responding").format(display=name)
		# Translators: a display in the combined display's list that is being used now.
		return _("{display}: in use").format(display=name)

	def _addable(self) -> list[str]:
		""":return: the drivers that may still be added, in NVDA's own order.

		Neither "no braille", which is the absence of a display rather than one, nor the
		combined display itself, which would construct another of itself reading the same
		list.
		"""
		return [
			name
			for name in self.descriptions
			if name not in self.chosen and name != devices.VIRTUAL_DISPLAY_NAME and name != "noBraille"
		]

	def _refresh(self, select: int | None = None) -> None:
		"""Put the current list into the controls.

		:param select: which chosen display to leave selected, or None to keep the selection.
		"""
		if select is None:
			select = self.chosenCtrl.GetSelection()
		self.chosenCtrl.Set([self._describeChosen(name) for name in self.chosen])
		if self.chosen:
			self.chosenCtrl.SetSelection(max(0, min(select, len(self.chosen) - 1)))
		self._available = self._addable()
		self.availableCtrl.Set([self._describe(name) for name in self._available])
		if self._available:
			self.availableCtrl.SetSelection(0)
		self._updateButtons()

	def _updateButtons(self) -> None:
		"""Enable only the buttons that would do something, so tabbing past them says so."""
		index = self.chosenCtrl.GetSelection()
		hasSelection = 0 <= index < len(self.chosen)
		self.moveUpButton.Enable(hasSelection and index > 0)
		self.moveDownButton.Enable(hasSelection and index < len(self.chosen) - 1)
		self.removeButton.Enable(hasSelection)
		self.addButton.Enable(bool(self._available))

	def _onSelectionChanged(self, event) -> None:
		self._updateButtons()

	def _move(self, offset: int) -> None:
		"""Move the selected display by one place, and keep it selected."""
		index = self.chosenCtrl.GetSelection()
		target = index + offset
		if not (0 <= index < len(self.chosen) and 0 <= target < len(self.chosen)):
			return
		self.chosen[index], self.chosen[target] = self.chosen[target], self.chosen[index]
		self._refresh(select=target)
		self.chosenCtrl.SetFocus()

	def _onMoveUp(self, event) -> None:
		self._move(-1)

	def _onMoveDown(self, event) -> None:
		self._move(1)

	def _onRemove(self, event) -> None:
		index = self.chosenCtrl.GetSelection()
		if not 0 <= index < len(self.chosen):
			return
		del self.chosen[index]
		self._refresh(select=index)
		self.chosenCtrl.SetFocus()

	def _onAdd(self, event) -> None:
		index = self.availableCtrl.GetSelection()
		if not 0 <= index < len(self._available):
			return
		self.chosen.append(self._available[index])
		self._refresh(select=len(self.chosen) - 1)
		self.availableCtrl.SetFocus()

	def specsToStore(self) -> list:
		""":return: the chosen displays as specifications, in stacking order.

		A display that was already configured keeps the specification it had, so that a port
		set from the console is not silently changed to automatic detection by someone opening
		this category and pressing OK. A newly added one is stored as a name alone, which means
		detect afresh.
		"""
		from brailleDisplayDrivers.brlMultilineVirtual.virtualLayout import DeviceSpec

		return [self.storedSpecs.get(name) or DeviceSpec(name) for name in self.chosen]

	def onSave(self):
		try:
			from brailleDisplayDrivers.brlMultilineVirtual import vdConfig

			vdConfig.setDevices(self.specsToStore())
		except Exception:
			# Refusing here would be refusing after the dialog has accepted the values, which
			# it has no way to show. The list is validated as it is built, so this can only be
			# something structural.
			log.error("BrlMultiline: could not store the combined display's device list", exc_info=True)

	def postSave(self):
		if self.chosen == self._original or not devices.isVirtualDisplay():
			return
		# The members are opened when the display is constructed, so a changed list does
		# nothing until it is constructed again. Deferred so that the settings dialog has
		# closed first: reopening two displays can take a moment, and it happens on this
		# thread.
		wx.CallAfter(self._reopen)

	def _reopen(self) -> None:
		"""Build the combined display again, so that a changed list takes effect now."""
		import braille

		try:
			if not braille.handler.setDisplayByName(devices.VIRTUAL_DISPLAY_NAME):
				log.error("BrlMultiline: the combined display did not reopen with its new list")
		except Exception:
			log.error("BrlMultiline: error reopening the combined display", exc_info=True)
