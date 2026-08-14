# BrlMultiline
# An NVDA add-on that divides a braille display into independent segments.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Global plugin entry point.

Owns the lifetime of the display container: builds it from the configuration for the
connected display, rebuilds it when the display or the settings change, and puts NVDA
back exactly as it found it on termination.

Also owns the two kinds of claim that outlive a single view. Panels activated by code are
kept and re-composed onto the configured view on every rebuild, so a table's grid is not
lost when the user saves a setting. Pinned objects are held by segment key, so a rebuild
that keeps their segment keeps the pin.
"""

import addonHandler
import api
import braille
import config
import globalPluginHandler
import gui
import ui
import wx
from braille.extensions import displayChanged, displaySizeChanged
from logHandler import log
from scriptHandler import script

from . import bmConfig, patches
from .container import DisplayContainer
from .devices import deviceMap
from .objectMonitor import ObjectMonitor
from .panels import BraillePanel
from .settingsPanel import BrailleMultilineSettingsPanel, VirtualDisplaySettingsPanel
from .views import SegmentView, deviceView, singleSegmentView, viewFromConfig

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
		self._monitors: dict[str, ObjectMonitor] = {}
		"""Pinned objects, keyed by the segment key they live in.

		Keyed rather than numbered so that a rebuild which keeps a segment keeps its pin.
		"""
		self._rebuildPending = False
		self._terminated = False
		"""Set by L{terminate}, so that work already queued does not run afterwards."""
		self._activeView: SegmentView | None = None
		"""A view installed by code rather than by the settings, if any.

		While this is set it takes over the display wholesale; clearing it returns to the
		view the user configured.
		"""
		self._activePanels: list[BraillePanel] = []
		"""Claims laid over whatever view is in force, in the order they were made.

		Kept across rebuilds and re-composed each time, so that a claim survives a settings
		change or a display swap. A claim that no longer fits is dropped and reported.
		"""
		bmConfig.initialize()
		patches.install()
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(BrailleMultilineSettingsPanel)
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(VirtualDisplaySettingsPanel)
		displaySizeChanged.register(self._handleDisplayChanged)
		displayChanged.register(self._handleDisplayChanged)
		# Settings are read through `config.conf`, which is profile aware, so switching
		# profile can change the layout, the focus segment and the panning direction
		# without any braille event firing.
		config.post_configProfileSwitch.register(self._handleProfileSwitch)
		self.rebuildBuffer()

	def terminate(self):
		# Set first, so that anything already queued sees it even if the teardown below
		# raises part way through.
		self._terminated = True
		try:
			displaySizeChanged.unregister(self._handleDisplayChanged)
			displayChanged.unregister(self._handleDisplayChanged)
			config.post_configProfileSwitch.unregister(self._handleProfileSwitch)
			self.stopAllMonitoring()
			self._restoreOriginalBuffer()
			patches.remove()
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(BrailleMultilineSettingsPanel)
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(VirtualDisplaySettingsPanel)
		except Exception:
			log.error("Error while terminating BrlMultiline", exc_info=True)
		finally:
			global _plugin
			_plugin = None
			super().terminate()

	# Buffer lifetime

	@property
	def container(self) -> DisplayContainer | None:
		""":return: the display container, or None if one is not installed."""
		buffer = braille.handler.mainBuffer
		return buffer if isinstance(buffer, DisplayContainer) else None

	@property
	def currentView(self) -> SegmentView | None:
		""":return: the view on the display now, or None if the add-on is not active."""
		container = self.container
		return container.view if container is not None else None

	def _displayDimensions(self) -> tuple[int, int]:
		""":return: the rows and columns of the connected display.

		:raises ValueError: if there is no display to arrange.
		"""
		handler = braille.handler
		if not handler or handler.displaySize == 0:
			raise ValueError("There is no braille display to show a view on")
		dimensions = handler.displayDimensions
		return dimensions.numRows, dimensions.numCols

	def activateView(self, view: SegmentView) -> None:
		"""Put a view on the display, taking over from the user's configured one.

		Replaces the whole arrangement. Prefer L{activatePanel} where only part of the
		display is wanted: a view replaces everything, including segments other code is
		relying on, while a panel takes only what it claims.

		Only one activated view is in force at a time: activating a second replaces the
		first. Call L{restoreConfiguredView} to hand the display back.

		:param view: the view to show.
		:raises ValueError: if the view's panels do not tile the display.
		:raises LookupError: if the view's focus segment does not exist.
		"""
		numRows, numCols = self._displayDimensions()
		# Validate before storing, so a view that does not fit leaves the display as it was.
		view.validate(numRows, numCols)
		self._activeView = view
		self.rebuildBuffer()

	def restoreConfiguredView(self) -> None:
		"""Return the display to the view the user configured, dropping every claim."""
		if self._activeView is None and not self._activePanels:
			return
		self._activeView = None
		self._activePanels = []
		self.rebuildBuffer()

	def activatePanel(self, panel: BraillePanel) -> None:
		"""Lay a panel over the display, leaving everything it does not claim alone.

		EXPERIMENTAL. The geometry half of this is settled, but the contract is not: an
		owner is told nothing when its claim is rebuilt or dropped, and has no way to
		redraw its cells except by noticing for itself. Expect this to gain a lease object
		carrying those callbacks, which will change the return type. Do not depend on it
		from another add-on yet.

		This is how code takes part of the display: a table reader claims the rows it wants
		as a grid, and a pinned object elsewhere keeps both its segment and its content,
		because the panel holding it was never involved in the claim.

		A panel with the same name as one already active replaces it, so refreshing a
		claim is a matter of activating it again. The panel is held by reference and read
		again on every rebuild, so it must not be mutated after being passed in.

		:param panel: the claim to lay over the current view.
		:raises ValueError: if the claim falls outside the display, or its segments do not
			fit within it.
		:raises LookupError: if the claim would take the focus segment without offering one
			in its place.
		"""
		numRows, numCols = self._displayDimensions()
		candidate = [*self._panelsExcept(panel.name), panel]
		# Compose against the view in force to find out whether this claim can be honoured,
		# before storing anything. A claim that cannot leaves the display as it was.
		self._composeView(numRows, numCols, candidate).validate(numRows, numCols)
		self._activePanels = candidate
		self.rebuildBuffer()

	def deactivatePanel(self, name: str) -> None:
		"""Take back a panel activated by code, leaving the rest of the display alone.

		:param name: the panel to remove. Removing one that is not active does nothing.
		"""
		remaining = self._panelsExcept(name)
		if len(remaining) == len(self._activePanels):
			return
		self._activePanels = remaining
		self.rebuildBuffer()

	def _panelsExcept(self, name: str) -> list[BraillePanel]:
		""":return: the active panels other than the one with a given name."""
		return [panel for panel in self._activePanels if panel.name != name]

	def _composeView(self, numRows: int, numCols: int, panels: list[BraillePanel]) -> SegmentView:
		"""Lay a list of claims over the base view, in order.

		:param numRows: number of rows on the display.
		:param numCols: number of columns on the display.
		:param panels: the claims to lay over it.
		:return: the composed view.
		:raises ValueError: if a claim does not fit.
		:raises LookupError: if a claim takes the focus segment without replacing it.
		"""
		view = self._baseView(numRows, numCols)
		for panel in panels:
			view = view.withPanel(panel, numRows, numCols)
		return view

	def _baseView(self, numRows: int, numCols: int) -> SegmentView:
		""":return: the view claims are laid over: an activated one, else the configured one."""
		if self._activeView is not None:
			try:
				self._activeView.validate(numRows, numCols)
				return self._activeView
			except (ValueError, LookupError):
				# The display changed under the view and its panels no longer fit.
				log.warning(
					f"BrlMultiline: view {self._activeView.name!r} does not fit "
					f"a {numRows} by {numCols} display; returning to the configured view",
				)
				self._activeView = None
		devices = deviceMap()
		if devices:
			try:
				# Validates itself against this size, so a driver and a handler that disagree
				# about the geometry are caught here rather than inside the container.
				return deviceView(numRows, numCols, devices)
			except (ValueError, LookupError):
				log.error(
					f"BrlMultiline: the displays behind this one do not describe "
					f"a {numRows} by {numCols} display; arranging it as one display instead. "
					"Anything that lands on a display narrower than the widest will be lost.",
					exc_info=True,
				)
		return viewFromConfig(numRows, numCols)

	def _buildView(self, numRows: int, numCols: int) -> SegmentView:
		"""Build the view to show, dropping any claim that no longer fits.

		Claims are re-composed on every rebuild rather than being baked into a stored view,
		so that a settings change or a display swap is answered by rebuilding the same
		claims against the new base rather than by discarding them.
		"""
		view = self._baseView(numRows, numCols)
		kept: list[BraillePanel] = []
		for panel in self._activePanels:
			try:
				view = view.withPanel(panel, numRows, numCols)
			except (ValueError, LookupError):
				log.warning(
					f"BrlMultiline: panel {panel.name!r} does not fit "
					f"a {numRows} by {numCols} display; dropping it",
					exc_info=True,
				)
				continue
			kept.append(panel)
		self._activePanels = kept
		return view

	def rebuildBuffer(self) -> None:
		"""Build the container for the connected display and install it.

		Called at startup, after the settings are saved, after the display changes, and
		when a view or panel is activated or removed.
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
			container = DisplayContainer(handler, view)
		except (ValueError, LookupError):
			log.error(
				f"BrlMultiline: could not build view {view.name!r}; using a single segment",
				exc_info=True,
			)
			self._activeView = None
			self._activePanels = []
			container = DisplayContainer(
				handler,
				singleSegmentView(dimensions.numRows, dimensions.numCols),
			)
		self._carryOverMonitors(container)
		wasShowingMainBuffer = handler.buffer is handler.mainBuffer
		handler.mainBuffer = container
		if wasShowingMainBuffer:
			handler.buffer = container
		self._refreshDisplay()
		# After the display has been redrawn from the focus, so that a pinned object is
		# written over the fresh layout rather than under it.
		self.refreshMonitors()

	def _carryOverMonitors(self, container: DisplayContainer) -> None:
		"""Keep the pins whose segment survived into a new container, and drop the rest.

		A pin names its segment by key, so it survives any rebuild that keeps that segment:
		a settings change elsewhere on the display, a claim laid over other rows, a display
		reconnecting at the same size. It is dropped in three cases:

		1. Its segment is gone.
		2. A claim has taken over a segment of the same key, since the new owner would
			redraw over the pin and the two would fight for the cells.
		3. Its segment now follows the system focus. `startMonitoring` refuses the focus
			segment for that reason, and a settings change can move the focus onto a
			segment that was pinned when the pin was made. Keeping it would let
			`refreshMonitors` clear the freshly drawn focus content and replace it with the
			pinned object.

		:param container: the container about to be installed.
		"""
		survivors = {
			key: monitor
			for key, monitor in self._monitors.items()
			if container.hasKey(key)
			and not container.segmentForKey(key).isReserved
			and key != container.focusSegmentKey
		}
		for key in self._monitors.keys() - survivors.keys():
			log.debug(
				f"BrlMultiline: segment {key!r} is gone, claimed, or now follows the focus, "
				f"so its pinned object is released",
			)
		self._monitors = survivors

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
		self._scheduleRebuild()

	def _handleProfileSwitch(self, **kwargs) -> None:
		"""Rebuild when NVDA switches configuration profile.

		The layout, the focus segment and the panning direction all live in `config.conf`,
		which is profile aware, so a profile switch can change them with no braille event
		to announce it. Deferred for the same reason a display change is: the switch is
		still being applied when this runs.
		"""
		self._scheduleRebuild()

	def _scheduleRebuild(self) -> None:
		"""Queue one rebuild, however many events asked for it."""
		if self._rebuildPending or self._terminated:
			# Both display events fire for a single swap. One rebuild answers both.
			return
		self._rebuildPending = True
		wx.CallAfter(self._deferredRebuild)

	def _deferredRebuild(self) -> None:
		if self._terminated:
			# The plugin was unloaded between queueing this and it running. Rebuilding now
			# would install a container over the buffer `terminate` just handed back to
			# NVDA, leaving the add-on driving the display after it was disabled.
			log.debug("BrlMultiline: skipping a rebuild queued before termination")
			return
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
	def monitoredKeys(self) -> set[str]:
		""":return: the keys of the segments currently holding a pinned object."""
		return set(self._monitors)

	@property
	def monitoredSegments(self) -> set[int]:
		""":return: the indices of the segments currently holding a pinned object.

		Resolved afresh from the keys, so this reports where the pins are on the display
		now rather than where they were when they were made.
		"""
		container = self.container
		if container is None:
			return set()
		numbers = set()
		for key in self._monitors:
			try:
				numbers.add(container.numberForKey(key))
			except LookupError:
				continue
		return numbers

	def startMonitoring(self, segmentNumber: int) -> None:
		"""Pin the navigator object to a segment.

		:param segmentNumber: the segment to pin it to, as the per segment commands count
			them. The pin itself is held by key, so it survives a later rebuild.
		"""
		if not bmConfig.areSegmentsEnabled():
			# There is one segment and it follows the focus, so the check below would refuse
			# this anyway. Said plainly here, because the reason is the switch rather than
			# anything about the segment the user named.
			# Translators: reported when asked to pin an object while the display is undivided.
			ui.message(_("The display is not divided into segments, so there is nowhere to pin an object"))
			return
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
		segment = container.segments[resolved]
		if segment.isReserved:
			# Something else already owns these cells and will keep redrawing them, so a
			# pin here would be overwritten without the user being told why.
			ui.message(
				# Translators: reported when asked to pin an object to a segment another
				# feature has claimed. Placeholders are the segment number and the claim's name.
				_("Segment {number} is in use by {owner}").format(number=resolved, owner=segment.owner),
			)
			return
		obj = api.getNavigatorObject()
		if obj is None:
			# Translators: reported when there is no object to pin.
			ui.message(_("No object to monitor"))
			return
		monitor = ObjectMonitor(obj, segment.key)
		self._monitors[segment.key] = monitor
		# Through `refreshMonitors` rather than straight to the monitor, so that pinning
		# while the display is showing speech registers the pin without drawing it.
		self.refreshMonitors(reveal=segment.key)
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
		key = container.segments[resolved].key
		if key not in self._monitors:
			# Translators: reported when asked to stop monitoring a segment that is not monitored.
			# The placeholder is replaced with the segment number.
			ui.message(_("Segment {number} is not monitoring anything").format(number=resolved))
			return
		del self._monitors[key]
		container.clear(key)
		container.update()
		container.updateDisplay()
		# Translators: reported when an object is unpinned from a segment.
		# The placeholder is replaced with the segment number.
		ui.message(_("Stopped monitoring in segment {number}").format(number=resolved))

	def stopAllMonitoring(self) -> None:
		"""Release every monitored segment, without announcing anything."""
		container = self.container
		for key in list(self._monitors):
			del self._monitors[key]
			if container is not None and container.hasKey(key):
				container.clear(key)

	def refreshMonitors(self, reveal: str | None = None) -> None:
		"""Redraw every pinned object. Called when a monitored object may have changed.

		The single place pins are drawn, so that the one condition under which they must not
		be drawn is checked once. Registrations are kept either way: a pin is not lost by
		being unable to draw, it simply waits for the next refresh.

		:param reveal: the key of a segment to show from the start of its content, rather
			than at the window position it was already panned to. Given when a pin is first
			made, so that the user sees the beginning of what they just pinned; withheld
			everywhere else, so that a refresh does not undo their panning.
		"""
		if bmConfig.isSpeechOutputMode():
			# NVDA is showing speech on the display and has stopped handling focus, caret
			# and review moves. A pin drawn now would sit beside the speech with nothing to
			# refresh or remove it, and a rebuild is exactly when that would happen, since
			# `_refreshDisplay` above draws nothing in this mode.
			log.debug("BrlMultiline: not drawing pinned objects while braille shows speech")
			return
		for key, monitor in list(self._monitors.items()):
			monitor.refresh(reveal=key == reveal)

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
		description=_("Turns the BrlMultiline segment layout on or off"),
		category=SCRIPT_CATEGORY,
	)
	def script_toggleSegments(self, gesture):
		enabled = not bmConfig.areSegmentsEnabled()
		bmConfig.setSegmentsEnabled(enabled)
		self.rebuildBuffer()
		if enabled:
			# Translators: reported when the display is divided into segments again.
			ui.message(_("Segments on"))
		else:
			# Translators: reported when the display stops being divided into segments.
			ui.message(_("Segments off"))

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
		# the number of panels, the number of segments, and the segment following the focus.
		ui.message(
			_("{view} view, {panels} panels, {count} segments, focus in segment {focus}").format(
				view=container.view.name,
				panels=len(container.panels),
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
