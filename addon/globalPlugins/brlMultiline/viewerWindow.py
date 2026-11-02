# BrlMultiline: the viewer window.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Painting the viewer's picture, and pointing at a cell to route to it.

A painted panel rather than a text control, since a text control cannot outline segments, grey
out cells or draw pins. Hover routes, as in NVDA's viewer, since a click would move the focus to
this window and change what is on the display. See docs/design/braille-viewer-plan.md, The viewer.
"""

from __future__ import annotations

from typing import Protocol

import addonHandler
import config
import gui
import wx
from logHandler import log

from . import bmConfig
from .viewerModel import CellPosition, Member, Picture, brailleCharacter, routingIndex

addonHandler.initTranslation()

BRAILLE_FONT = "FreeMono-FixedBraille"
"""The font NVDA's viewer installs and uses, whose braille and text characters are one width."""

FONT_SIZE = 20
ROW_GAP = 2
MEMBER_GAP = 12
MARGIN = 8
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


class _Layout:
	"""Where each row and member of a picture is drawn, for painting and for hit testing."""

	def __init__(self, picture: Picture, cellWidth: int, cellHeight: int, labelHeight: int):
		self.picture = picture
		self.cellWidth = cellWidth
		self.cellHeight = cellHeight
		self.rowHeight = cellHeight * 2 + ROW_GAP
		"""A line of braille and the line of text under it."""
		self.labelHeight = labelHeight
		self.rowTops: list[int] = []
		self.memberTops: list[tuple[Member, int]] = []
		y = MARGIN
		members = picture.members or (Member("", "", 0, picture.numRows, picture.numCols),)
		for member in members:
			self.memberTops.append((member, y))
			y += labelHeight
			for _row in range(member.numRows):
				self.rowTops.append(y)
				y += self.rowHeight
			y += MEMBER_GAP
		self.height = y
		self.width = MARGIN * 2 + cellWidth * picture.numCols

	def left(self, col: int) -> int:
		return MARGIN + col * self.cellWidth

	def position(self, x: int, y: int) -> CellPosition | None:
		""":return: the cell, and the pin where its member is drawn as pins, at a point; None for none."""
		col = (x - MARGIN) // self.cellWidth if x >= MARGIN else -1
		if not 0 <= col < self.picture.numCols:
			return None
		for row, top in enumerate(self.rowTops):
			if top <= y < top + self.rowHeight:
				return CellPosition(row, col, self._pin(row, x, y))
		return None

	def _pin(self, row: int, x: int, y: int) -> tuple[int, int] | None:
		picture = self.picture
		member = picture.memberForRow(row)
		if member is None or not picture.showsPins(member):
			return None
		band = picture.pinBandFor(member)
		if band is None:
			return None
		bandTop = self.rowTops[member.rowStart]
		pinX = int((x - MARGIN) * band.pinsPerCol / self.cellWidth)
		pinY = int((y - bandTop) * band.pinsPerRow / self.rowHeight)
		return pinX, pinY


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
		self._layout: _Layout | None = None
		self._pending: CellPosition | None = None
		self._routed: CellPosition | None = None
		self._hoverTimer: wx.CallLater | None = None
		self._flashTimer: wx.CallLater | None = None
		self._refreshQueued = False
		self._closing = False

		self.Bind(wx.EVT_CLOSE, self._onClose)
		sizer = wx.BoxSizer(wx.VERTICAL)
		self._panel = wx.Panel(self, style=wx.FULL_REPAINT_ON_RESIZE)
		self._panel.SetDoubleBuffered(True)
		self._panel.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW))
		self._panel.Bind(wx.EVT_PAINT, self._onPaint)
		self._panel.Bind(wx.EVT_MOTION, self._onMotion)
		self._panel.Bind(wx.EVT_LEAVE_WINDOW, lambda event: self._cancelHover())
		sizer.Add(self._panel, proportion=1, flag=wx.EXPAND)

		options = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: the checkbox in the BrlMultiline viewer that opens it whenever NVDA starts.
		self._atStartup = wx.CheckBox(self, label=_("&Show the viewer when NVDA starts"))
		self._atStartup.SetValue(bmConfig.shouldOpenViewerAtStartup())
		self._atStartup.Bind(
			wx.EVT_CHECKBOX,
			lambda event: bmConfig.setOpenViewerAtStartup(event.IsChecked()),
		)
		options.Add(self._atStartup, flag=wx.ALL, border=4)
		# Translators: the checkbox in the BrlMultiline viewer that presses a cell's routing key when the
		# mouse rests on it.
		self._hover = wx.CheckBox(self, label=_("&Hover for cell routing"))
		self._hover.SetValue(bmConfig.shouldViewerHoverRoute())
		self._hover.Bind(wx.EVT_CHECKBOX, self._onHoverChanged)
		options.Add(self._hover, flag=wx.ALL, border=4)
		sizer.Add(options)
		self.SetSizer(sizer)

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
			self._picture = self._source.picture()
		except Exception:
			log.error("BrlMultiline: could not read what the viewer should show", exc_info=True)
			return
		layout = self._makeLayout(self._picture)
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

	def _makeLayout(self, picture: Picture) -> _Layout:
		dc = wx.ClientDC(self._panel)
		dc.SetFont(self._brailleFont)
		cellWidth, cellHeight = dc.GetTextExtent(brailleCharacter(0xFF))
		dc.SetFont(self._labelFont)
		_labelWidth, labelHeight = dc.GetTextExtent("Ag")
		return _Layout(picture, max(cellWidth, 1), max(cellHeight, 1), labelHeight + ROW_GAP)

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
			dc.DrawText(member.label, MARGIN, top)
			if picture.showsPins(member):
				self._paintPins(dc, layout, member, text)
				continue
			for row in range(member.rowStart, member.rowEnd):
				self._paintRow(dc, layout, row, rawTextRows[row], text, dead)
		self._paintHighlight(dc, layout)
		dc.SetPen(wx.Pen(outline, 1))
		dc.SetBrush(wx.TRANSPARENT_BRUSH)
		for segment in picture.segments:
			rect = segment.rect
			if not 0 <= rect.row < len(layout.rowTops) or rect.numRows <= 0:
				continue
			top = layout.rowTops[rect.row]
			bottom = layout.rowTops[min(rect.endRow, len(layout.rowTops)) - 1] + layout.rowHeight
			dc.DrawRectangle(layout.left(rect.col), top, rect.numCols * layout.cellWidth, bottom - top)

	def _paintRow(self, dc, layout: _Layout, row: int, rawText: str, text, dead) -> None:
		picture = self._picture
		top = layout.rowTops[row]
		dc.SetPen(wx.TRANSPARENT_PEN)
		dc.SetBrush(wx.Brush(dead))
		for col in range(picture.numCols):
			if picture.isDead(row, col):
				dc.DrawRectangle(layout.left(col), top, layout.cellWidth, layout.rowHeight)
		dc.SetFont(self._brailleFont)
		dc.SetTextForeground(text)
		braille = "".join(brailleCharacter(picture.cell(row, col)) for col in range(picture.numCols))
		dc.DrawText(braille, layout.left(0), top)
		dc.DrawText(rawText, layout.left(0), top + layout.cellHeight)

	def _paintPins(self, dc, layout: _Layout, member: Member, text) -> None:
		picture = self._picture
		band = picture.pinBandFor(member)
		if band is None:
			return
		top = layout.rowTops[member.rowStart]
		stepX = layout.cellWidth / band.pinsPerCol
		stepY = layout.rowHeight / band.pinsPerRow
		radius = max(1, int(min(stepX, stepY) * 0.35))
		lowered = wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)
		for y, row in enumerate(picture.pins(member)):
			for x, raised in enumerate(row):
				colour = text if raised else lowered
				dc.SetPen(wx.Pen(colour, 1))
				dc.SetBrush(wx.Brush(colour) if raised else wx.TRANSPARENT_BRUSH)
				centreX = int(MARGIN + (x + 0.5) * stepX)
				centreY = int(top + (y + 0.5) * stepY)
				dc.DrawCircle(centreX, centreY, radius if raised else max(1, radius // 2))

	def _paintHighlight(self, dc, layout: _Layout) -> None:
		for position, colour in ((self._pending, PENDING_COLOUR), (self._routed, ROUTED_COLOUR)):
			if position is None or position.row >= len(layout.rowTops):
				continue
			dc.SetPen(wx.Pen(colour, 3))
			dc.SetBrush(wx.TRANSPARENT_BRUSH)
			dc.DrawRectangle(
				layout.left(position.col),
				layout.rowTops[position.row],
				layout.cellWidth,
				layout.rowHeight,
			)

	# Hover routing

	def _onHoverChanged(self, event) -> None:
		bmConfig.setViewerHoverRoute(event.IsChecked())
		if not event.IsChecked():
			self._cancelHover()

	def _onMotion(self, event) -> None:
		if not self._hover.IsChecked() or self._layout is None:
			return
		position = self._layout.position(event.GetX(), event.GetY())
		if position is not None and routingIndex(self._picture, position) is None:
			# A cell that reaches no hardware routes nowhere, so it is not offered.
			position = None
		if (
			position is not None
			and self._pending is not None
			and position.row == self._pending.row
			and position.col == self._pending.col
		):
			# The same cell. A different pin of it still points the touch at the latest pin.
			self._pending = position
			return
		self._cancelHover()
		if position is None:
			return
		self._pending = position
		self._hoverTimer = wx.CallLater(int(_hoverSeconds() * 1000), self._activate)
		self._panel.Refresh()

	def _cancelHover(self) -> None:
		if self._hoverTimer is not None:
			self._hoverTimer.Stop()
			self._hoverTimer = None
		if self._pending is not None:
			self._pending = None
			self._panel.Refresh()

	def _activate(self) -> None:
		self._hoverTimer = None
		position, self._pending = self._pending, None
		if position is None or self._closing:
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
