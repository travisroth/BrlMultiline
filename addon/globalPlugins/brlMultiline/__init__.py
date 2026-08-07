# BrlMultiline
# An NVDA add-on that divides a braille display into independent segments.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Global plugin entry point.

Owns the lifetime of the multi segment buffer: builds it from the configuration for the
connected display, rebuilds it when the display or the settings change, and puts NVDA
back exactly as it found it on termination.
"""

import addonHandler
import api
import braille
import globalPluginHandler
import gui
import ui
import wx
from braille.extensions import displayChanged, displaySizeChanged
from logHandler import log
from scriptHandler import script

from . import bmConfig, patches
from .container import BrailleBufferContainer
from .objectMonitor import ObjectMonitor
from .settingsPanel import BrailleMultilineSettingsPanel
from .views import SegmentView, singleSegmentView, viewFromConfig

addonHandler.initTranslation()

# Translators: the name of the input gestures category for this add-on's commands.
SCRIPT_CATEGORY = _("BrlMultiline")

_plugin = None


def getPlugin() -> "GlobalPlugin | None":
	""":return: the running plugin instance, or None if it is not loaded."""
	return _plugin


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	scriptCategory = SCRIPT_CATEGORY

	def __init__(self):
		super().__init__()
		global _plugin
		_plugin = self
		self._originalMainBuffer = braille.handler.mainBuffer
		self._monitors: dict[int, ObjectMonitor] = {}
		self._rebuildPending = False
		self._activeView: SegmentView | None = None
		"""A view installed by code rather than by the settings, if any.

		While this is set it takes over the display; clearing it returns to the view the
		user configured.
		"""
		bmConfig.initialize()
		patches.install()
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(BrailleMultilineSettingsPanel)
		displaySizeChanged.register(self._handleDisplayChanged)
		displayChanged.register(self._handleDisplayChanged)
		self.rebuildBuffer()

	def terminate(self):
		try:
			displaySizeChanged.unregister(self._handleDisplayChanged)
			displayChanged.unregister(self._handleDisplayChanged)
			self.stopAllMonitoring()
			self._restoreOriginalBuffer()
			patches.remove()
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(BrailleMultilineSettingsPanel)
		except Exception:
			log.error("Error while terminating BrlMultiline", exc_info=True)
		finally:
			global _plugin
			_plugin = None
			super().terminate()

	# Buffer lifetime

	@property
	def container(self) -> BrailleBufferContainer | None:
		""":return: the active multi segment buffer, or None if one is not installed."""
		buffer = braille.handler.mainBuffer
		return buffer if isinstance(buffer, BrailleBufferContainer) else None

	@property
	def currentView(self) -> SegmentView | None:
		""":return: the view on the display now, or None if the add-on is not active."""
		container = self.container
		return container.view if container is not None else None

	def activateView(self, view: SegmentView) -> None:
		"""Put a view on the display, taking over from the user's configured one.

		For code that drives the display directly, such as a table reader that wants a
		grid of segments. Only one such view is active at a time: activating a second
		replaces the first. Call L{restoreConfiguredView} to hand the display back.

		:param view: the view to show.
		:raises ValueError: if the view's segments do not fit the display, or overlap.
		:raises LookupError: if the view's focus segment does not exist.
		"""
		handler = braille.handler
		if not handler or handler.displaySize == 0:
			raise ValueError("There is no braille display to show a view on")
		# Build before storing, so a view that does not fit leaves the display as it was.
		view.validate(handler.displayDimensions.numRows, handler.displayDimensions.numCols)
		self._activeView = view
		self.rebuildBuffer()

	def restoreConfiguredView(self) -> None:
		"""Return the display to the view the user configured."""
		if self._activeView is None:
			return
		self._activeView = None
		self.rebuildBuffer()

	def _buildView(self, numRows: int, numCols: int) -> SegmentView:
		"""Choose the view to show: an activated one if there is one, else the configured one."""
		if self._activeView is not None:
			try:
				self._activeView.validate(numRows, numCols)
				return self._activeView
			except (ValueError, LookupError):
				# The display changed under the view and its segments no longer fit.
				log.warning(
					f"BrlMultiline: view {self._activeView.name!r} does not fit "
					f"a {numRows} by {numCols} display; returning to the configured view",
				)
				self._activeView = None
		return viewFromConfig(numRows, numCols)

	def rebuildBuffer(self) -> None:
		"""Build the buffer for the connected display and install it.

		Called at startup, after the settings are saved, after the display changes, and
		when a view is activated or restored.
		"""
		handler = braille.handler
		if not handler or handler.displaySize == 0:
			# No display, or braille is disabled. Nothing to divide.
			log.debug("BrlMultiline: no display, leaving NVDA's buffer in place")
			self._restoreOriginalBuffer()
			return
		dimensions = handler.displayDimensions
		view = self._buildView(dimensions.numRows, dimensions.numCols)
		try:
			container = BrailleBufferContainer(handler, view)
		except (ValueError, LookupError):
			log.error(
				f"BrlMultiline: could not build view {view.name!r}; using a single segment",
				exc_info=True,
			)
			self._activeView = None
			container = BrailleBufferContainer(
				handler,
				singleSegmentView(dimensions.numRows, dimensions.numCols),
			)
		self._monitors.clear()
		wasShowingMainBuffer = handler.buffer is handler.mainBuffer
		handler.mainBuffer = container
		if wasShowingMainBuffer:
			handler.buffer = container
		self._refreshDisplay()

	def _restoreOriginalBuffer(self) -> None:
		"""Put NVDA's own buffer back."""
		handler = braille.handler
		if not handler or handler.mainBuffer is self._originalMainBuffer:
			return
		wasShowingMainBuffer = handler.buffer is handler.mainBuffer
		handler.mainBuffer = self._originalMainBuffer
		if wasShowingMainBuffer:
			handler.buffer = self._originalMainBuffer
		self._refreshDisplay()

	def _refreshDisplay(self) -> None:
		"""Redraw the display from the current focus."""
		try:
			braille.handler.handleGainFocus(api.getFocusObject())
		except Exception:
			log.debugWarning("Could not refresh the braille display", exc_info=True)

	def _handleDisplayChanged(self, **kwargs) -> None:
		"""Rebuild when the display or its dimensions change.

		Both `displaySizeChanged` and `displayChanged` are listened for. The first alone is
		not enough: NVDA raises it only when the dimensions actually differ, so swapping
		one display for another of the same geometry would go unnoticed, and settings are
		stored per driver name as well as per size. The second alone is not enough either,
		as it is not raised when a display keeps its driver but changes size.

		The rebuild is deferred: this can run from within the handler's displayDimensions
		property, which has not finished updating its own cache yet, and reading those
		dimensions again from here would re-enter it.
		"""
		if self._rebuildPending:
			# Both events fire for a single swap. One rebuild answers both.
			return
		self._rebuildPending = True
		wx.CallAfter(self._deferredRebuild)

	def _deferredRebuild(self) -> None:
		try:
			self.rebuildBuffer()
		except Exception:
			log.error("BrlMultiline: could not rebuild after a display change", exc_info=True)
		finally:
			# Cleared last, so that reading the dimensions during the rebuild cannot
			# schedule a second one for the change now being handled.
			self._rebuildPending = False

	# Object monitoring

	@property
	def monitoredSegments(self) -> set[int]:
		""":return: the segments currently holding a pinned object."""
		return set(self._monitors)

	def startMonitoring(self, segmentNumber: int) -> None:
		"""Pin the navigator object to a segment.

		:param segmentNumber: the segment to pin it to.
		"""
		container = self.container
		if container is None:
			# Translators: reported when a command needs segments but none are configured.
			ui.message(_("BrlMultiline is not active"))
			return
		try:
			resolved = container.resolveSegmentNumber(segmentNumber)
		except LookupError:
			# Translators: reported when a command names a segment that does not exist.
			# The placeholder is replaced with the segment number.
			ui.message(_("There is no segment {number}").format(number=segmentNumber))
			return
		if resolved == container.focusSegmentNumber:
			# Translators: reported when asked to pin an object to the segment that follows focus.
			ui.message(_("That segment follows the focus, so it cannot hold a pinned object"))
			return
		obj = api.getNavigatorObject()
		if obj is None:
			# Translators: reported when there is no object to pin.
			ui.message(_("No object to monitor"))
			return
		monitor = ObjectMonitor(obj, resolved)
		self._monitors[resolved] = monitor
		monitor.refresh()
		# Translators: reported when an object is pinned to a segment.
		# Placeholders are the object's name and the segment number.
		ui.message(_("Monitoring {name} in segment {number}").format(name=monitor.name, number=resolved))

	def stopMonitoring(self, segmentNumber: int) -> None:
		"""Stop pinning an object to a segment and clear it.

		:param segmentNumber: the segment to release.
		"""
		container = self.container
		if container is None:
			return
		try:
			resolved = container.resolveSegmentNumber(segmentNumber)
		except LookupError:
			return
		if resolved not in self._monitors:
			# Translators: reported when asked to stop monitoring a segment that is not monitored.
			# The placeholder is replaced with the segment number.
			ui.message(_("Segment {number} is not monitoring anything").format(number=resolved))
			return
		del self._monitors[resolved]
		container.clear(resolved)
		container.update()
		container.updateDisplay()
		# Translators: reported when an object is unpinned from a segment.
		# The placeholder is replaced with the segment number.
		ui.message(_("Stopped monitoring in segment {number}").format(number=resolved))

	def stopAllMonitoring(self) -> None:
		"""Release every monitored segment, without announcing anything."""
		container = self.container
		for segmentNumber in list(self._monitors):
			del self._monitors[segmentNumber]
			if container is not None:
				container.clear(segmentNumber)

	def refreshMonitors(self) -> None:
		"""Redraw every pinned object. Called when a monitored object may have changed."""
		for monitor in list(self._monitors.values()):
			monitor.refresh()

	# Scrolling

	def scrollSegment(self, segmentNumber: int, forward: bool) -> None:
		"""Scroll one segment, whether or not it holds focus.

		:param segmentNumber: the segment to scroll.
		:param forward: True to scroll forward, False to scroll back.
		"""
		container = self.container
		if container is None:
			# Translators: reported when a command needs segments but none are configured.
			ui.message(_("BrlMultiline is not active"))
			return
		if bmConfig.shouldReverseScrollButtons():
			forward = not forward
		if forward:
			container.scrollForward(segmentNumber)
		else:
			container.scrollBack(segmentNumber)

	# Scripts

	@script(
		# Translators: input help message for a command.
		description=_("Opens the BrlMultiline settings"),
		category=SCRIPT_CATEGORY,
	)
	@gui.blockAction.when(gui.blockAction.Context.MODAL_DIALOG_OPEN)
	def script_showSettings(self, gesture):
		wx.CallAfter(
			gui.mainFrame.popupSettingsDialog,
			gui.settingsDialogs.NVDASettingsDialog,
			BrailleMultilineSettingsPanel,
		)

	@script(
		# Translators: input help message for a command.
		description=_("Reports the BrlMultiline segment layout"),
		category=SCRIPT_CATEGORY,
	)
	def script_reportLayout(self, gesture):
		container = self.container
		if container is None:
			# Translators: reported when a command needs segments but none are configured.
			ui.message(_("BrlMultiline is not active"))
			return
		# Translators: reports the current layout. Placeholders are the name of the view,
		# the number of segments, and the segment that follows the focus.
		ui.message(
			_("{view} view, {count} segments, focus in segment {focus}").format(
				view=container.view.name,
				count=container.numSegments,
				focus=container.focusSegmentNumber,
			),
		)


def _makeScrollScript(segmentNumber: int, forward: bool):
	"""Build one per segment scrolling script."""

	def scrollScript(self, gesture):
		self.scrollSegment(segmentNumber, forward)

	if forward:
		# Translators: input help message for a command. The placeholder is a segment number.
		scrollScript.__doc__ = _("Scrolls segment {number} forward").format(number=segmentNumber)
	else:
		# Translators: input help message for a command. The placeholder is a segment number.
		scrollScript.__doc__ = _("Scrolls segment {number} back").format(number=segmentNumber)
	scrollScript.category = SCRIPT_CATEGORY
	scrollScript.bypassInputHelp = False
	return scrollScript


def _makeMonitorScript(segmentNumber: int, start: bool):
	"""Build one per segment object monitoring script."""

	def monitorScript(self, gesture):
		if start:
			self.startMonitoring(segmentNumber)
		else:
			self.stopMonitoring(segmentNumber)

	if start:
		# Translators: input help message for a command. The placeholder is a segment number.
		monitorScript.__doc__ = _("Shows the navigator object in segment {number}").format(
			number=segmentNumber,
		)
	else:
		# Translators: input help message for a command. The placeholder is a segment number.
		monitorScript.__doc__ = _("Stops showing an object in segment {number}").format(
			number=segmentNumber,
		)
	monitorScript.category = SCRIPT_CATEGORY
	return monitorScript


def _generateSegmentScripts() -> None:
	"""Add the per segment commands to the plugin class.

	One set is generated for every segment the add-on can offer, whether or not the
	current layout has that many. None are bound by default: assign the ones wanted
	through NVDA's Input Gestures dialog, ideally to keys on the display itself.
	"""
	for segmentNumber in range(bmConfig.MAX_UI_SEGMENTS):
		setattr(
			GlobalPlugin,
			f"script_scrollSegment{segmentNumber}Forward",
			_makeScrollScript(segmentNumber, True),
		)
		setattr(
			GlobalPlugin,
			f"script_scrollSegment{segmentNumber}Back",
			_makeScrollScript(segmentNumber, False),
		)
		setattr(
			GlobalPlugin,
			f"script_monitorObjectInSegment{segmentNumber}",
			_makeMonitorScript(segmentNumber, True),
		)
		setattr(
			GlobalPlugin,
			f"script_stopMonitoringSegment{segmentNumber}",
			_makeMonitorScript(segmentNumber, False),
		)


_generateSegmentScripts()
