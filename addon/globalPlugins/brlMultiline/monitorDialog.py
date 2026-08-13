# BrlMultiline: dialog for object monitoring.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

import addonHandler
import ui
import wx

addonHandler.initTranslation()


class MonitorObjectDialog(wx.Dialog):
	"""Manage objects pinned to segments for monitoring."""

	def __init__(self, parent, plugin):
		super().__init__(
			parent,
			title=_("Manage monitored objects"),
			style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
		)
		self.plugin = plugin
		self._buildControls()
		self.refreshControls()
		self.segmentCombo.SetFocus()
		self.CentreOnParent()

	def _buildControls(self) -> None:
		mainSizer = wx.BoxSizer(wx.VERTICAL)

		segmentLabel = wx.StaticText(
			self,
			# Translators: label of the segment chooser in the monitor management dialog.
			label=_("Segment to monitor:"),
		)
		mainSizer.Add(segmentLabel, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)

		self.segmentCombo = wx.ComboBox(self, style=wx.CB_DROPDOWN)
		mainSizer.Add(self.segmentCombo, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=10)

		self.startButton = wx.Button(
			self,
			wx.ID_OK,
			# Translators: button label to pin the navigator object to the chosen segment.
			label=_("&Start monitoring"),
		)
		self.startButton.SetDefault()
		self.startButton.Bind(wx.EVT_BUTTON, self.onStartMonitoring)
		mainSizer.Add(self.startButton, flag=wx.ALIGN_RIGHT | wx.ALL, border=10)

		monitoredLabel = wx.StaticText(
			self,
			# Translators: label above the list of currently monitored objects.
			label=_("Currently monitored objects:"),
		)
		mainSizer.Add(monitoredLabel, flag=wx.LEFT | wx.RIGHT, border=10)

		self.monitorsList = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
		self.monitorsList.InsertColumn(
			0,
			# Translators: column title for the monitored object name.
			_("Object"),
		)
		self.monitorsList.InsertColumn(
			1,
			# Translators: column title for the segment number.
			_("Segment"),
		)
		self.monitorsList.Bind(wx.EVT_LIST_ITEM_SELECTED, self.onSelectionChanged)
		self.monitorsList.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.onSelectionChanged)
		mainSizer.Add(self.monitorsList, proportion=1, flag=wx.EXPAND | wx.ALL, border=10)

		buttonSizer = wx.BoxSizer(wx.HORIZONTAL)
		self.stopButton = wx.Button(
			self,
			# Translators: button label to stop monitoring the selected object.
			label=_("S&top monitoring"),
		)
		self.stopButton.Bind(wx.EVT_BUTTON, self.onStopMonitoring)
		buttonSizer.Add(self.stopButton)

		closeButton = wx.Button(self, wx.ID_CLOSE)
		closeButton.Bind(wx.EVT_BUTTON, self.onClose)
		buttonSizer.Add(closeButton, flag=wx.LEFT, border=10)

		mainSizer.Add(buttonSizer, flag=wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)
		self.SetSizerAndFit(mainSizer)

	def refreshControls(self) -> None:
		choices = [str(number) for number in self.plugin.availableMonitoringSegments()]
		current = self.segmentCombo.GetValue().strip()
		self.segmentCombo.Clear()
		for choice in choices:
			self.segmentCombo.Append(choice)
		if current and current in choices:
			self.segmentCombo.SetValue(current)
		elif choices:
			self.segmentCombo.SetValue(choices[0])
		else:
			self.segmentCombo.SetValue("")

		self.monitorsList.DeleteAllItems()
		for row, (name, number) in enumerate(self.plugin.monitoredObjects()):
			index = self.monitorsList.InsertItem(row, name)
			self.monitorsList.SetItem(index, 1, str(number))
			self.monitorsList.SetItemData(index, number)
		if self.monitorsList.GetItemCount():
			self.monitorsList.SetColumnWidth(0, wx.LIST_AUTOSIZE)
		else:
			self.monitorsList.SetColumnWidth(0, wx.LIST_AUTOSIZE_USEHEADER)
		self.monitorsList.SetColumnWidth(1, wx.LIST_AUTOSIZE_USEHEADER)
		self._updateStopButton()

	def _updateStopButton(self) -> None:
		self.stopButton.Enable(self.monitorsList.GetFirstSelected() != -1)

	def _selectedSegmentNumber(self) -> int | None:
		text = self.segmentCombo.GetValue().strip()
		if not text:
			# Translators: reported when the monitor dialog is asked to start without a segment.
			ui.message(_("Choose a segment to monitor in"))
			return None
		try:
			return int(text)
		except ValueError:
			# Translators: reported when the monitor dialog receives text that is not a whole segment number.
			ui.message(_("Segment numbers must be whole numbers"))
			return None

	def onStartMonitoring(self, event) -> None:
		number = self._selectedSegmentNumber()
		if number is None:
			return
		before = self.plugin.monitoredKeys
		self.plugin.startMonitoring(number)
		if self.plugin.monitoredKeys != before:
			self.EndModal(wx.ID_OK)
			return
		self.refreshControls()

	def onStopMonitoring(self, event) -> None:
		selected = self.monitorsList.GetFirstSelected()
		if selected == -1:
			# Translators: reported when asked to stop monitoring without selecting a monitor.
			ui.message(_("Choose a monitored object to stop"))
			return
		self.plugin.stopMonitoring(self.monitorsList.GetItemData(selected))
		self.refreshControls()

	def onSelectionChanged(self, event) -> None:
		self._updateStopButton()
		event.Skip()

	def onClose(self, event) -> None:
		self.EndModal(wx.ID_CLOSE)
