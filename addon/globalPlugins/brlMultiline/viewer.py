# BrlMultiline: the viewer, for a sighted person beside the reader.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Showing what is on every row, segment and display, and the pins of a drawing.

NVDA's Braille Viewer forces the display to one row while it is open, so the add-on has its own.
This gathers what the window paints and carries out what is done in it; `viewerModel` decides
what the picture is and `viewerWindow` paints it. See docs/design/braille-viewer-plan.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import addonHandler
import api
import braille
import gui
import inputCore
import ui
import wx
from logHandler import log

from . import bmConfig, devices, graphics
from .settingsPanel import displayDescriptions
from .viewerModel import (
	CellPosition,
	Member,
	Overlay,
	OverlayStore,
	PinBand,
	Picture,
	Segment,
	memberCellIndex,
	routingIndex,
)

if TYPE_CHECKING:
	from .container import DisplayContainer

addonHandler.initTranslation()

SIMULATED_MODE_DELAY = 5000
"""Milliseconds before saying the display is simulated, so startup's own speech does not cut it off."""


class ViewerHost(Protocol):
	"""What `BrailleViewerTool` uses of the plugin. See docs/design/architecture.md, Module map."""

	@property
	def container(self) -> DisplayContainer | None: ...


class BrailleViewerTool:
	"""The viewer window and everything that feeds it, for the plugin to hold."""

	def __init__(self, plugin: ViewerHost):
		"""
		:param plugin: the global plugin, for the segments on its display.
		"""
		self.plugin = plugin
		self._window = None
		self._cells: tuple[int, ...] = ()
		self._overlays = OverlayStore()
		self._menuItem = None
		self._listening: list[tuple[object, object]] = []
		"""Each extension point registered on, with its handler, to unregister on terminate."""
		self._announcedFor: int | None = None
		"""The display last told to be simulated, by identity, so a rebuild does not say it again."""
		self._announceTimer = None
		self._labels: dict[str, str] | None = None
		"""Each display's name, read once per display change rather than on every paint."""
		self._locked = False
		self._terminated = False
		"""Set once the plugin has let go of this, so nothing queued before then acts after it."""

	# Lifetime

	def install(self) -> None:
		"""Listen for cells and drawings, put the viewer in the Tools menu, and open it if asked."""
		from braille.extensions import pre_writeCells

		self._listen(pre_writeCells, self._onCells)
		self._listen(graphics.overlayChanged, self._onOverlay)
		nvdaViewerToggled = _nvdaViewerToggled()
		if nvdaViewerToggled is not None:
			self._listen(nvdaViewerToggled, self._onNvdaViewer)
		self._menuItem = self._addMenuItem()
		if bmConfig.shouldOpenViewerAtStartup() and not _isSecure():
			wx.CallAfter(self.open)

	def terminate(self) -> None:
		"""Close the window and stop listening. Safe to call more than once."""
		self._terminated = True
		if self._announceTimer is not None:
			self._announceTimer.Stop()
			self._announceTimer = None
		self.close()
		for action, handler in self._listening:
			try:
				action.unregister(handler)
			except Exception:
				log.debugWarning("BrlMultiline: could not stop listening for the viewer", exc_info=True)
		self._listening = []
		self._removeMenuItem()

	def _listen(self, action, handler) -> None:
		action.register(handler)
		self._listening.append((action, handler))

	# The window

	@property
	def isOpen(self) -> bool:
		return self._window is not None

	def toggle(self) -> bool:
		""":return: whether the viewer is open now."""
		if self.isOpen:
			self.close()
			return False
		return self.open()

	def open(self) -> bool:
		""":return: whether the viewer is open, which it cannot be in a secure session or after
		`terminate`."""
		if self._window is not None:
			return True
		if self._terminated or _isSecure():
			return False
		from .viewerWindow import ViewerFrame

		self._window = ViewerFrame(self)
		self._window.refresh()
		return True

	def close(self) -> None:
		window, self._window = self._window, None
		if window is not None:
			window.closeQuietly()

	def windowClosed(self) -> None:
		"""Called by the window when the user closes it."""
		self._window = None

	def refresh(self) -> None:
		"""Paint the viewer again, if it is open."""
		if self._window is not None:
			self._window.refresh()

	def afterRebuild(self) -> None:
		"""Called after the plugin builds the display again: say once that it is simulated, and repaint."""
		self._labels = None
		display = _currentDisplay()
		if devices.isSimulatedMode(display) and id(display) != self._announcedFor:
			self._announcedFor = id(display)
			log.info("BrlMultiline: the combined display has no displays set up, and is simulating one")
			if self._announceTimer is not None:
				self._announceTimer.Stop()
			self._announceTimer = wx.CallLater(SIMULATED_MODE_DELAY, self._announceSimulated, id(display))
		self.refresh()

	def _announceSimulated(self, displayId: int) -> None:
		"""Say the display is simulated, if it is still the display that was.

		:param displayId: the identity of the display found to be simulated.
		"""
		self._announceTimer = None
		display = _currentDisplay()
		if self._terminated or id(display) != displayId or not devices.isSimulatedMode(display):
			return
		ui.message(
			# Translators: said when the combined braille display has no displays set up and is
			# simulating one instead, for testing without hardware.
			_(
				"BrlMultiline is simulating a braille display. Open the BrlMultiline viewer in the Tools menu to see it.",
			),
		)

	def onSessionLock(self, isNowLocked: bool) -> None:
		"""Show nothing while Windows is locked, as NVDA's viewer shows nothing.

		:param isNowLocked: whether the session has just locked.
		"""
		self._locked = isNowLocked
		self.refresh()

	@property
	def isLocked(self) -> bool:
		return self._locked

	# What the window paints and does

	def picture(self) -> Picture:
		""":return: what is on the display now."""
		handler = braille.handler
		display = handler.display if handler is not None else None
		if display is None or self._locked:
			return Picture(0, 0, ())
		dimensions = handler.displayDimensions
		numRows, numCols = dimensions.numRows, dimensions.numCols
		if numRows <= 0 or numCols <= 0:
			# NVDA's "No braille" is a display with no cells, not the absence of one.
			return Picture(0, 0, ())
		if self._labels is None:
			self._labels = displayDescriptions()
		labels = self._labels
		members = tuple(
			Member(
				device.driverName,
				labels.get(device.driverName, device.driverName),
				device.rowStart,
				device.numRows,
				device.numCols,
			)
			for device in devices.deviceMap(display)
		)
		if not members and numRows:
			name = getattr(display, "name", "")
			members = (Member(name, labels.get(name, name), 0, numRows, numCols),)
		pinBands = tuple(
			PinBand(
				getattr(surface.driver, "name", ""),
				surface.pinWidth,
				surface.pinHeight,
				surface.pinsPerRow,
				surface.pinsPerCol,
			)
			for surface in graphics.findSurfaces(display)
		)
		self._overlays.keepOnly({member.name for member in members})
		return Picture(
			numRows=numRows,
			numCols=numCols,
			cells=self._cells,
			members=members,
			segments=self._segments(),
			pinBands=pinBands,
			overlays=self._overlays.snapshot(),
		)

	def _segments(self) -> tuple[Segment, ...]:
		container = self.plugin.container
		if container is None:
			return ()
		segments = []
		for rect, segment in zip(container.rects, container.segments, strict=False):
			try:
				rawText = segment.windowRawText
				rowTexts = tuple(segment.windowRowTexts()) if hasattr(segment, "windowRowTexts") else ()
			except Exception:
				log.debugWarning(
					"BrlMultiline: could not read a segment's text for the viewer",
					exc_info=True,
				)
				rawText, rowTexts = "", ()
			segments.append(Segment(rect, rawText, rowTexts))
		return tuple(segments)

	def copyToClipboard(self) -> bool:
		"""Copy what the viewer shows, braille over text, whether or not the window is open.

		:return: whether there was anything to copy and the clipboard took it.
		"""
		text = self.picture().toText()
		if not text:
			return False
		try:
			return bool(api.copyToClip(text))
		except Exception:
			log.debugWarning("BrlMultiline: could not copy the viewer's braille", exc_info=True)
			return False

	def route(self, picture: Picture, position: CellPosition) -> None:
		"""Press the routing key on a cell the viewer was pointed at.

		A simulated display presses its own key, so its layers apply, and is touched at the pin
		first where it is drawn as pins. Any other display's cell is routed as NVDA's viewer routes.

		Nothing while Windows is locked or after `terminate`, whatever the window was showing.

		:param picture: what the window was showing.
		:param position: the cell, and the pin where there is one.
		"""
		if self._locked or self._terminated:
			return
		index = routingIndex(picture, position)
		if index is None:
			return
		member = picture.memberForRow(position.row)
		simulatedDisplay = _simulatedDriver(member)
		try:
			if simulatedDisplay is not None and member is not None:
				if position.pin is not None:
					simulatedDisplay.touch(*position.pin)
				simulatedDisplay.pressKey("routing", memberCellIndex(member, position))
				return
			from brailleViewer.brailleViewerInputGesture import BrailleViewerGesture_RouteTo

			inputCore.manager.executeGesture(BrailleViewerGesture_RouteTo(index))
		except inputCore.NoInputGestureAction:
			pass
		except Exception:
			log.error("BrlMultiline: could not route from the viewer", exc_info=True)

	# Listeners

	def _onCells(self, cells=None, **kwargs) -> None:
		# Refreshed on every write, not only when the cells change: the text under them can change
		# while the cells stay the same, and the window collapses requests into one repaint.
		self._cells = tuple(cells or ())
		self.refresh()

	def _onOverlay(self, surface=None, key=None, rect=None, buffer=None, **kwargs) -> None:
		name = getattr(getattr(surface, "driver", None), "name", None)
		if name is None:
			return
		overlay = Overlay(rect.x, rect.y, buffer) if rect is not None and buffer is not None else None
		self._overlays.apply(name, key, overlay)
		self.refresh()

	def _onNvdaViewer(self, created: bool = False, **kwargs) -> None:
		"""Say that NVDA's viewer collapses a display of several rows to one, once per opening."""
		handler = braille.handler
		display = handler.display if handler is not None else None
		if not created or display is None or getattr(display, "numRows", 1) <= 1:
			return
		ui.message(
			# Translators: said when NVDA's own Braille Viewer is opened on a braille display of
			# several rows, which it shows as one.
			_(
				"NVDA's Braille Viewer shows one row, so the braille display shows one row while it is open. "
				"The BrlMultiline viewer in the Tools menu shows every row.",
			),
		)

	# The Tools menu

	def _addMenuItem(self):
		""":return: the viewer's item in NVDA's Tools menu, or None in a secure session or without a menu."""
		if _isSecure():
			return None
		try:
			tray = gui.mainFrame.sysTrayIcon
			item = tray.toolsMenu.Append(
				wx.ID_ANY,
				# Translators: the item in NVDA's Tools menu that opens the BrlMultiline viewer.
				_("BrlMultiline &viewer"),
			)
			tray.Bind(wx.EVT_MENU, self._onMenu, item)
			return item
		except Exception:
			log.debugWarning("BrlMultiline: could not add the viewer menu item", exc_info=True)
			return None

	def _removeMenuItem(self) -> None:
		item, self._menuItem = self._menuItem, None
		if item is None:
			return
		try:
			tray = gui.mainFrame.sysTrayIcon
			# Unbound as well as removed, for the reason the layered keys item is.
			tray.Unbind(wx.EVT_MENU, source=item, handler=self._onMenu)
			tray.toolsMenu.DestroyItem(item)
		except Exception:
			log.debugWarning("BrlMultiline: could not remove the viewer menu item", exc_info=True)

	def _onMenu(self, event) -> None:
		self.toggle()


def _isSecure() -> bool:
	""":return: whether this is a secure session or the lock screen, where NVDA's viewer is not offered either."""
	try:
		import globalVars

		if globalVars.appArgs.secure:
			return True
	except Exception:
		pass
	try:
		from utils.security import isLockScreenModeActive

		return bool(isLockScreenModeActive())
	except ImportError:
		return False


def _nvdaViewerToggled():
	""":return: NVDA's notice that its own viewer opened or closed, or None where there is none."""
	try:
		import brailleViewer

		return brailleViewer.postBrailleViewerToolToggledAction
	except Exception:
		return None


def _simulatedDriver(member: Member | None):
	""":return: the simulated display behind a member, or None for real hardware."""
	if member is None or member.name not in devices.SIMULATED_DRIVER_NAMES:
		return None
	slotFor = getattr(_currentDisplay(), "slotForDriverName", None)
	slot = slotFor(member.name) if slotFor is not None else None
	return getattr(slot, "driver", None)


def _currentDisplay():
	""":return: the display NVDA is driving, or None."""
	handler = braille.handler
	return handler.display if handler is not None else None
