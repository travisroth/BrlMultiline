# BrlMultiline: the viewer window.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Painting the viewer's picture, and pointing at a cell to route to it.

A painted panel rather than a text control, since a text control cannot outline segments, grey
out cells or draw pins. Hover routes, as in NVDA's viewer, since a click would move the focus to
this window and change what is on the display. Where things are drawn, and what a resting pointer
may route, are `viewerModel.ViewerLayout` and `viewerModel.HoverTracker`; this times and paints.
See docs/design/braille-viewer-plan.md, The viewer.
"""

from __future__ import annotations

from typing import Protocol

import addonHandler
import config
import gui
import wx
from logHandler import log

from . import bmConfig
from .viewerModel import (
	MARGIN,
	ROW_GAP,
	CellPosition,
	HoverTracker,
	Member,
	Picture,
	ViewerLayout,
	brailleCharacter,
)

addonHandler.initTranslation()

BRAILLE_FONT = "FreeMono-FixedBraille"
"""The font NVDA's viewer installs and uses, whose braille and text characters are one width."""

FONT_SIZE = 20
FLASH_SECONDS = 0.4
SW_SHOWNOACTIVATE = 4
PENDING_COLOUR = wx.Colour(255, 205, 60)
ROUTED_COLOUR = wx.Colour(81, 215, 81)
"""NVDA's viewer's hover colours, so the two viewers look alike."""


class ViewerSource(Protocol):
	"""What the window uses of `viewer.BrailleViewerTool`."""

	@property
	def isLocked(self) -> bool: ...

	def picture(self) -> Picture: ...

	def route(self, picture: Picture, position: CellPosition) -> None: ...

	def windowClosed(self) -> None: ...


class ViewerFrame(wx.Frame):
	"""The viewer window. Shown without taking the focus, and kept on top."""

	def __init__(self, source: ViewerSource):
		position = bmConfig.viewerPosition()
		if position is not None and wx.Display.GetFromPoint(wx.Point(*position)) == wx.NOT_FOUND:
			# On no monitor now, such as one that has been unplugged.
			position = None
		super().__init__(
			gui.mainFrame,
			# Translators: the title of the BrlMultiline viewer window.
			title=_("BrlMultiline Viewer"),
			pos=wx.Point(*position) if position is not None else wx.DefaultPosition,
			style=wx.CAPTION | wx.CLOSE_BOX | wx.STAY_ON_TOP,
		)
		self._source = source
		self._picture = Picture(0, 0, ())
		self._layout: ViewerLayout | None = None
		self._hoverTracker = HoverTracker()
		self._routed: CellPosition | None = None
		"""The cell just routed, flashed for a moment."""
		self._hoverTimer: wx.CallLater | None = None
		self._flashTimer: wx.CallLater | None = None
		self._refreshQueued = False
		self._closing = False

		self.Bind(wx.EVT_CLOSE, self._onClose)
		frameSizer = wx.BoxSizer(wx.VERTICAL)
		# The controls sit in a panel of their own, as in NVDA's viewer, since only a panel moves
		# between its controls with tab.
		container = wx.Panel(self)
		frameSizer.Add(container, proportion=1, flag=wx.EXPAND)
		sizer = wx.BoxSizer(wx.VERTICAL)
		self._panel = wx.Panel(container, style=wx.FULL_REPAINT_ON_RESIZE)
		# Painted, with nothing a screen reader could read, so tab passes it by.
		self._panel.SetCanFocus(False)
		self._panel.SetDoubleBuffered(True)
		self._panel.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW))
		self._panel.Bind(wx.EVT_PAINT, self._onPaint)
		self._panel.Bind(wx.EVT_MOTION, self._onMotion)
		self._panel.Bind(wx.EVT_LEAVE_WINDOW, lambda event: self._cancelHover())
		sizer.Add(self._panel, proportion=1, flag=wx.EXPAND)

		options = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: the checkbox in the BrlMultiline viewer that opens it whenever NVDA starts.
		self._atStartup = wx.CheckBox(container, label=_("&Show the viewer when NVDA starts"))
		self._atStartup.SetValue(bmConfig.shouldOpenViewerAtStartup())
		self._atStartup.Bind(
			wx.EVT_CHECKBOX,
			lambda event: bmConfig.setOpenViewerAtStartup(event.IsChecked()),
		)
		options.Add(self._atStartup, flag=wx.ALL, border=4)
		# Translators: the checkbox in the BrlMultiline viewer that presses a cell's routing key when the
		# mouse rests on it.
		self._hover = wx.CheckBox(container, label=_("&Hover for cell routing"))
		self._hover.SetValue(bmConfig.shouldViewerHoverRoute())
		self._hover.Bind(wx.EVT_CHECKBOX, self._onHoverChanged)
		options.Add(self._hover, flag=wx.ALL, border=4)
		sizer.Add(options)
		container.SetSizer(sizer)
		self.SetSizer(frameSizer)

		self._brailleFont = self._makeBrailleFont()
		self._labelFont = wx.Font(wx.FontInfo(10).Bold())
		self.ShowWithoutActivating()
		self._showNormal()

	@staticmethod
	def _makeBrailleFont() -> wx.Font:
		try:
			import fonts

			fonts.importFonts()
		except Exception:
			log.debugWarning("BrlMultiline: could not load NVDA's braille font for the viewer", exc_info=True)
		return wx.Font(wx.FontInfo(FONT_SIZE).FaceName(BRAILLE_FONT))

	# Refreshing

	def refresh(self) -> None:
		"""Ask for a repaint, collapsing every request made before it happens into one."""
		if self._refreshQueued or self._closing:
			return
		self._refreshQueued = True
		wx.CallAfter(self._repaint)

	def _repaint(self) -> None:
		self._refreshQueued = False
		if self._closing:
			return
		try:
			picture = self._source.picture()
		except Exception:
			log.error("BrlMultiline: could not read what the viewer should show", exc_info=True)
			return
		# As in NVDA's viewer, the lock screen cannot change what happens at startup.
		self._atStartup.Enable(not self._source.isLocked)
		if self._hoverTracker.pictureChanged(self._picture, picture):
			self._stopHoverTimer()
		self._picture = picture
		layout = self._makeLayout(picture)
		resized = self._layout is None or (layout.width, layout.height) != (
			self._layout.width,
			self._layout.height,
		)
		self._layout = layout
		if resized:
			self._panel.SetMinSize(wx.Size(layout.width, layout.height))
			self.Fit()
			self._showNormal()
		self._panel.Refresh()

	def _showNormal(self) -> None:
		"""Bring the window out of the minimized state, without taking the focus.

		Windows can show it minimized when it opens from NVDA's menu, and it has no minimize
		button, so nothing in it could restore it. `Iconize(False)` would activate it.
		"""
		if not self.IsIconized():
			return
		log.info("BrlMultiline: the viewer was minimized; showing it")
		try:
			import ctypes

			ctypes.windll.user32.ShowWindow(self.GetHandle(), SW_SHOWNOACTIVATE)
		except Exception:
			log.debugWarning("BrlMultiline: could not show the viewer", exc_info=True)

	def _makeLayout(self, picture: Picture) -> ViewerLayout:
		dc = wx.ClientDC(self._panel)
		dc.SetFont(self._brailleFont)
		cellWidth, cellHeight = dc.GetTextExtent(brailleCharacter(0xFF))
		dc.SetFont(self._labelFont)
		_labelWidth, labelHeight = dc.GetTextExtent("Ag")
		return ViewerLayout(picture, max(cellWidth, 1), max(cellHeight, 1), labelHeight + ROW_GAP)

	# Painting

	def _onPaint(self, event) -> None:
		dc = wx.PaintDC(self._panel)
		layout = self._layout
		if layout is None:
			return
		picture = self._picture
		if self._source.isLocked or not picture.numRows:
			dc.SetFont(self._labelFont)
			dc.DrawText(
				# Translators: shown in the BrlMultiline viewer while there is no braille to show, such as
				# while Windows is locked or no display is in use.
				_("No braille to show"),
				MARGIN,
				MARGIN,
			)
			return
		text = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT)
		dead = wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE)
		outline = wx.SystemSettings.GetColour(wx.SYS_COLOUR_HIGHLIGHT)
		rawTextRows = picture.rawTextRows()
		for member, top in layout.memberTops:
			dc.SetFont(self._labelFont)
			dc.SetTextForeground(text)
			dc.DrawText(member.label, MARGIN, int(top))
			if picture.showsPins(member):
				self._paintPins(dc, layout, member, text)
				continue
			for row in range(member.rowStart, member.rowEnd):
				self._paintRow(dc, layout, row, rawTextRows[row], text, dead)
		self._paintHighlight(dc, layout)
		dc.SetPen(wx.Pen(outline, 1))
		dc.SetBrush(wx.TRANSPARENT_BRUSH)
		rowCount = len(layout.rowTops)
		for segment in picture.segments:
			rect = segment.rect
			if not 0 <= rect.row < rowCount or rect.numRows <= 0 or rect.numCols <= 0:
				continue
			first = layout.cellBox(rect.row, rect.col)
			last = layout.cellBox(min(rect.endRow, rowCount) - 1, rect.col + rect.numCols - 1)
			_drawBox(dc, first.x, first.y, last.x + last.width - first.x, last.y + last.height - first.y)

	def _paintRow(self, dc, layout: ViewerLayout, row: int, rawText: str, text, dead) -> None:
		picture = self._picture
		dc.SetPen(wx.TRANSPARENT_PEN)
		dc.SetBrush(wx.Brush(dead))
		for col in range(picture.numCols):
			if picture.isDead(row, col):
				box = layout.cellBox(row, col)
				_drawBox(dc, box.x, box.y, box.width, box.height)
		dc.SetFont(self._brailleFont)
		dc.SetTextForeground(text)
		braille = "".join(brailleCharacter(picture.cell(row, col)) for col in range(picture.numCols))
		top = int(layout.rowTops[row])
		dc.DrawText(braille, MARGIN, top)
		dc.DrawText(rawText, MARGIN, top + layout.cellHeight)

	def _paintPins(self, dc, layout: ViewerLayout, member: Member, text) -> None:
		step = layout.pinStep(member)
		if step is None:
			return
		radius = max(1, int(step * 0.35))
		lowered = wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)
		for y, row in enumerate(self._picture.pins(member)):
			for x, raised in enumerate(row):
				colour = text if raised else lowered
				dc.SetPen(wx.Pen(colour, 1))
				dc.SetBrush(wx.Brush(colour) if raised else wx.TRANSPARENT_BRUSH)
				centreX, centreY = layout.pinCentre(member, x, y)
				dc.DrawCircle(int(centreX), int(centreY), radius if raised else max(1, radius // 2))

	def _paintHighlight(self, dc, layout: ViewerLayout) -> None:
		pending = self._hoverTracker.pending
		for position, colour in ((pending, PENDING_COLOUR), (self._routed, ROUTED_COLOUR)):
			if position is None or position.row >= len(layout.rowTops):
				continue
			dc.SetPen(wx.Pen(colour, 3))
			dc.SetBrush(wx.TRANSPARENT_BRUSH)
			box = layout.cellBox(position.row, position.col)
			_drawBox(dc, box.x, box.y, box.width, box.height)

	# Hover routing

	def _onHoverChanged(self, event) -> None:
		bmConfig.setViewerHoverRoute(event.IsChecked())
		if not event.IsChecked():
			self._cancelHover()

	def _onMotion(self, event) -> None:
		if not self._hover.IsChecked() or self._layout is None:
			return
		wasPending = self._hoverTracker.pending is not None
		position = self._layout.position(event.GetX(), event.GetY())
		if self._hoverTracker.move(self._picture, position):
			self._stopHoverTimer()
			self._hoverTimer = wx.CallLater(int(_hoverSeconds() * 1000), self._activate)
		elif self._hoverTracker.pending is None:
			self._stopHoverTimer()
		if wasPending or self._hoverTracker.pending is not None:
			self._panel.Refresh()

	def _cancelHover(self) -> None:
		self._stopHoverTimer()
		wasPending = self._hoverTracker.pending is not None
		self._hoverTracker.leave()
		if wasPending:
			self._panel.Refresh()

	def _stopHoverTimer(self) -> None:
		if self._hoverTimer is not None:
			self._hoverTimer.Stop()
			self._hoverTimer = None

	def _activate(self) -> None:
		self._hoverTimer = None
		if self._closing:
			return
		position = self._hoverTracker.activate(self._picture)
		if position is None:
			self._panel.Refresh()
			return
		self._routed = position
		self._panel.Refresh()
		self._source.route(self._picture, position)
		if self._flashTimer is not None:
			self._flashTimer.Stop()
		self._flashTimer = wx.CallLater(int(FLASH_SECONDS * 1000), self._endFlash)

	def _endFlash(self) -> None:
		self._flashTimer = None
		self._routed = None
		if not self._closing:
			self._panel.Refresh()

	# Closing

	def _stopTimers(self) -> None:
		for timer in (self._hoverTimer, self._flashTimer):
			if timer is not None:
				timer.Stop()
		self._hoverTimer = self._flashTimer = None

	def closeQuietly(self) -> None:
		"""Close without telling the source, which asked for it."""
		self._closing = True
		self._savePosition()
		self._stopTimers()
		self.Destroy()

	def _onClose(self, event) -> None:
		self._closing = True
		self._savePosition()
		self._stopTimers()
		self._source.windowClosed()
		self.Destroy()

	def _savePosition(self) -> None:
		if self.IsIconized():
			# A minimized window's position is far off screen, not where it was.
			return
		try:
			position = self.GetPosition()
			bmConfig.setViewerPosition(position.x, position.y)
		except Exception:
			log.debugWarning("BrlMultiline: could not save where the viewer was", exc_info=True)


def _hoverSeconds() -> float:
	""":return: how long the mouse rests on a cell before it routes, NVDA's viewer's own setting."""
	try:
		return float(config.conf["brailleViewer"]["secondsOfHoverToActivate"])
	except Exception:
		return 1.0


def _drawBox(dc, x: float, y: float, width: float, height: float) -> None:
	dc.DrawRectangle(int(x), int(y), max(1, int(width + 0.5)), max(1, int(height + 0.5)))
