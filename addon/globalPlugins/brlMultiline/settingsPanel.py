# BrlMultiline: settings panel.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""NVDA settings category for BrlMultiline.

Every setting on this panel applies to the display that is connected now, and is stored
against it. Connecting a different display presents that display's own settings.
"""

import addonHandler
import gui
import wx
from gui import guiHelper
from logHandler import log

from . import bmConfig
from .layout import calculateSegmentRects

addonHandler.initTranslation()


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


class BrailleMultilineSettingsPanel(gui.settingsDialogs.SettingsPanel):
	# Translators: title of the BrlMultiline settings category in NVDA's settings.
	title = _("BrlMultiline")

	def makeSettings(self, settingsSizer):
		sHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self.displayKey = bmConfig.getDisplayKey()
		section = bmConfig.getDisplayConfig(self.displayKey)
		import braille

		dimensions = braille.handler.displayDimensions
		self.numRows = dimensions.numRows
		self.numCols = dimensions.numCols
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
		self.segmentsEnabledCtrl.SetValue(bool(section["segmentsEnabled"]))
		# Translators: label of a spin control in settings.
		segmentCountLabel = _("Number of &segments:")
		self.segmentCountCtrl = sHelper.addLabeledControl(
			segmentCountLabel,
			wx.SpinCtrl,
			min=1,
			max=bmConfig.MAX_UI_SEGMENTS,
			initial=int(section["segmentCount"]),
		)
		if self.numRows > 1:
			# Translators: label of an edit field in settings, for a multi row display.
			sizesLabel = _("Segment sizes in &rows, separated by commas (blank to divide evenly):")
		else:
			# Translators: label of an edit field in settings, for a single row display.
			sizesLabel = _("Segment sizes in &cells, separated by commas (blank to divide evenly):")
		self.segmentSizesCtrl = sHelper.addLabeledControl(sizesLabel, wx.TextCtrl)
		self.segmentSizesCtrl.Value = ", ".join(str(size) for size in section["segmentSizes"])
		# Translators: label of a spin control in settings. -1 means the last segment.
		focusLabel = _("Segment that &follows the focus (-1 for the last):")
		self.focusSegmentCtrl = sHelper.addLabeledControl(
			focusLabel,
			wx.SpinCtrl,
			min=-1,
			max=bmConfig.MAX_UI_SEGMENTS - 1,
			initial=int(section["focusSegment"]),
		)
		# Translators: label of a checkbox in settings.
		reverseLabel = _("&Reverse the panning keys on this display")
		self.reverseScrollCtrl = sHelper.addItem(wx.CheckBox(self, label=reverseLabel))
		self.reverseScrollCtrl.SetValue(bool(section["reverseScrollBtns"]))
		# Translators: label of a checkbox in settings.
		documentLinesLabel = _("Show the &document lines around the caret in the other segments")
		self.documentLinesCtrl = sHelper.addItem(wx.CheckBox(self, label=documentLinesLabel))
		self.documentLinesCtrl.SetValue(bool(section["showDocumentLines"]))

	def isValid(self) -> bool:
		try:
			sizes = parseSegmentSizes(self.segmentSizesCtrl.Value)
		except ValueError as error:
			# Checked whether or not the layout is in use, since it is stored either way and
			# nothing should be able to put text into the configuration that cannot be read
			# back.
			self._reportError(str(error), self.segmentSizesCtrl)
			return False
		if not self.segmentsEnabledCtrl.IsChecked():
			# The layout is not going to be used, so whether it fits this display is not a
			# reason to refuse the save. It matters when a display has been replaced by a
			# smaller one: turning division off is exactly what the user would reach for, and
			# holding them to a layout that no longer fits would stop them doing it.
			return True
		layout = sizes if sizes else self.segmentCountCtrl.Value
		try:
			rects = calculateSegmentRects(self.numRows, self.numCols, layout)
		except ValueError as error:
			self._reportError(str(error), self.segmentSizesCtrl if sizes else self.segmentCountCtrl)
			return False
		focusSegment = self.focusSegmentCtrl.Value
		if focusSegment >= len(rects):
			self._reportError(
				# Translators: reported when the chosen focus segment does not exist.
				# Placeholders are the chosen number and the number of segments configured.
				_("There is no segment {chosen}; this layout has {count} segments, numbered from 0.").format(
					chosen=focusSegment,
					count=len(rects),
				),
				self.focusSegmentCtrl,
			)
			return False
		return True

	def _reportError(self, message: str, control: wx.Window) -> None:
		gui.messageBox(
			message,
			# Translators: title of an error dialog shown when settings cannot be applied.
			_("BrlMultiline settings"),
			wx.OK | wx.ICON_ERROR,
			self,
		)
		control.SetFocus()

	def onSave(self):
		section = bmConfig.getDisplayConfig(self.displayKey)
		try:
			sizes = parseSegmentSizes(self.segmentSizesCtrl.Value)
		except ValueError:
			# isValid has already vetted this; keep whatever was stored before.
			log.debugWarning("Segment sizes could not be parsed while saving", exc_info=True)
			return
		section["segmentsEnabled"] = self.segmentsEnabledCtrl.IsChecked()
		section["segmentCount"] = self.segmentCountCtrl.Value
		section["segmentSizes"] = sizes
		section["focusSegment"] = self.focusSegmentCtrl.Value
		section["reverseScrollBtns"] = self.reverseScrollCtrl.IsChecked()
		section["showDocumentLines"] = self.documentLinesCtrl.IsChecked()

	def postSave(self):
		# Rebuild the display with the new layout straight away.
		from . import getPlugin

		plugin = getPlugin()
		if plugin is not None:
			plugin.rebuildBuffer()
