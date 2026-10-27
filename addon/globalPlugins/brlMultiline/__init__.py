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

import contextlib
import time

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

from . import (
	bmConfig,
	keyLayerContexts,
	keyLayerDispatch,
	keyLayers,
	panning,
	patches,
	tableArrows,
	tableRowLine,
)
from . import tableHeaderSpeech
from . import userBrailleTable
from . import container as containerModule, monitorRouting, pinQuickNav
from .container import DisplayContainer
from . import chartSource, glyphFlow, glyphs, graphicsMode
from . import appLayout
from .appLayoutHost import AppLayoutHost
from . import image as imageFigure
from .layoutOverview import LayoutOverview
from .focusDisplays import FocusDisplayMoves
from .pictureCommands import PictureCommands
from .tableCommands import TableCommands
from .flowTableSource import TableRequest, wantsColumns
from .graphicsMode import FIT as GRAPHICS_FIT, PANEL_NAME as GRAPHICS_PANEL_NAME, GraphicsMode
from . import devices as devicesModule
from .devices import (
	DeviceInfo,
	configuredMembers,
	deviceMap,
	resolveDisplaySegment,
	segmentsForDevice,
)
from .layout import SegmentRect
from .messages import MessageBuffer
from .objectMonitor import ObjectMonitor
from .panels import BraillePanel
from .settingsPanel import (
	BrailleMultilineSettingsPanel,
	FlowSettingsPanel,
	VirtualDisplaySettingsPanel,
	displayDescriptions,
)
from .views import (
	SegmentView,
	deviceFallbackView,
	deviceView,
	driverNameForSegmentKey,
	singleSegmentView,
	validateAgainstHardware,
	viewFromConfig,
)

addonHandler.initTranslation()

# Translators: the name of the input gestures category for this add-on's commands.
SCRIPT_CATEGORY = _("BrlMultiline")

_plugin = None

def getPlugin() -> "GlobalPlugin | None":
	""":return: the running plugin instance, or None if it is not loaded."""
	return _plugin


def _runLoggingFailure(step, *args) -> None:
	"""Run one step of the add-on's teardown, logging rather than raising if it fails."""
	try:
		step(*args)
	except Exception:
		log.error(f"BrlMultiline: a teardown step failed: {getattr(step, '__qualname__', step)}", exc_info=True)


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

		self._appLayoutsSuspended = False
		"""Whether the reader has set app layouts aside for now, until NVDA restarts.

		On top of the setting, and not stored. See `script_toggleAppLayouts`.
		"""

		self.appLayouts = AppLayoutHost(self)
		"""The focus's app layout, when one is up. See `appLayoutHost`."""

		self._pinsToRead: list[str] | None = None
		"""The pins the timed pass under way has still to read, or None between passes.

		See L{refreshMonitorsInTurn}."""

		self._lastSegmentOn: dict[str, str] = {}
		"""The segment the reader last panned or routed in on each display, by driver name. Which
		pinned document a quick navigation key there is for; see L{pinForQuickNav}."""

		self._pinsTurn = False
		"""Whether the reader's pins have the next slice rather than the app layout, so a slow
		layout cannot keep them from ever being read. See L{refreshMonitorsInTurn}."""

		self.focusMoves = FocusDisplayMoves(self)
		"""Which display follows the focus, and moving it there. See `focusDisplays`."""

		self._messageBuffer: MessageBuffer | None = None
		"""NVDA's flash messages, confined to one segment. Made on the first rebuild.

		Made once and retargeted, never replaced. See `_installMessageBuffer`.
		"""
		self._originalMessageBuffer = None
		"""NVDA's own, kept to be put back on termination."""
		self._rebuildPending = False
		self._reportedDivergence: tuple[tuple[str, ...], tuple[str, ...]] | None = None
		"""The last mismatch between the configured members and the ones the composite was
		opened for, so that it is said once rather than on every profile switch."""
		self._terminated = False
		"""Set by L{terminate}, so that work already queued does not run afterwards."""
		self._teardown = contextlib.ExitStack()
		"""The undo of everything `_install` put in place, registered beside it. See `_onTerminate`."""
		self._activeView: SegmentView | None = None
		"""A view installed by code rather than by the settings, if any.

		While this is set it takes over the display wholesale; clearing it returns to the
		view the user configured.
		"""
		self.flowBand = None
		"""The flow claiming part of the display, or None. See L{_applyFlow}."""

		self.tableRequest = TableRequest()
		"""The table the reader has asked to see in columns. See `TableRequest`."""

		self._applyingFlow = False
		"""Guards L{_applyFlow} against itself: claiming the band rebuilds the display."""

		self.graphicsMode = GraphicsMode(self)
		"""The drawing on the display, and the zoom and origin that make it readable.

		Built whether or not the display can draw. On one that cannot, `active` is False and
		a request to show something reports why.
		"""

		self.pictures = PictureCommands(self)
		"""The commands that put a chart or a picture up. See `pictureCommands`."""

		self.layouts = LayoutOverview(self)
		"""The layout overview of a page or a window. See `layoutOverview`."""

		self.tables = TableCommands(self)
		"""The commands that lay a table out in columns and arrange it. See `tableCommands`."""

		self._activePanels: list[BraillePanel] = []
		"""Claims laid over whatever view is in force, in the order they were made.

		Kept across rebuilds and re-composed each time, so that a claim survives a settings
		change or a display swap. A claim that no longer fits is dropped and reported.
		"""
		try:
			self._install()
		except BaseException:
			self._shutDown()
			raise

	def _install(self) -> None:
		"""Put the add-on in place, registering each undo with `_onTerminate` as it goes."""
		bmConfig.initialize()
		self._onTerminate(patches.remove)
		patches.install()
		# A routing key in a segment showing an object goes to that object. See `monitorRouting`.
		containerModule.segmentRouter = self._routeIntoMonitor
		containerModule.segmentUsed = self._noteSegmentUsed
		self._onTerminate(setattr, containerModule, "segmentRouter", None)
		self._onTerminate(setattr, containerModule, "segmentUsed", None)
		# Which display's keys are being pressed, for the per display panning direction.
		self._onTerminate(panning.remove)
		panning.install()
		# The arrow keys inside a browse mode table, which is a way of reading rather than a
		# way of displaying and so is not waited on a flow. See `tableArrows`.
		self._onTerminate(tableArrows.remove)
		tableArrows.install()
		# Which tables' headers are not spoken, which is also how a table is read rather than
		# displayed, and so is not waited on a flow either. See `tableHeaderSpeech`.
		self._onTerminate(tableHeaderSpeech.remove)
		tableHeaderSpeech.install()
		# Layered keys. Reads each device's layers and passes every key through while no layer
		# is on. See `keyLayerDispatch`.
		self._onTerminate(keyLayerDispatch.remove)
		keyLayerDispatch.install()
		self._keyLayersMenuItem = self._addKeyLayersMenuItem()
		self._onTerminate(self._removeKeyLayersMenuItem)
		# The reader's braille substitutions: written into the rules file every BrlMultiline table
		# includes, for the profiles in force, and again on every profile switch. See
		# `userBrailleTable`. Guarded, because a list that cannot be put in force must not stop the
		# rest of the add-on loading; removed on terminate whether or not it went in.
		self._onTerminate(userBrailleTable.remove)
		try:
			if userBrailleTable.install(self._handleBrailleRulesChanged):
				# The reader's personal tables were made again, after the add-on was updated or its
				# manifest written afresh, and NVDA reads which tables there are only as it starts.
				# Said after startup's own speech, or it would be cut off.
				wx.CallLater(
					5000,
					ui.message,
					# Translators: said at startup when the reader's personal braille tables had to be made
					# again and NVDA has to restart before they can be chosen.
					_("BrlMultiline: restart NVDA to use your personal braille tables."),
				)
		except Exception:
			log.error("BrlMultiline: could not put the braille substitutions in force", exc_info=True)
		self._substitutionsMenuItem = self._addSubstitutionsMenuItem()
		self._onTerminate(self._removeSubstitutionsMenuItem)
		categories = gui.settingsDialogs.NVDASettingsDialog.categoryClasses
		for panel in (BrailleMultilineSettingsPanel, FlowSettingsPanel, VirtualDisplaySettingsPanel):
			categories.append(panel)
			self._onTerminate(categories.remove, panel)
		# Registered before the handlers, so it runs after they are gone and nothing is left to
		# rebuild the display it releases, and before the patches and hooks above come out.
		self._onTerminate(self._releaseDisplay)
		displaySizeChanged.register(self._handleDisplayChanged)
		self._onTerminate(displaySizeChanged.unregister, self._handleDisplayChanged)
		displayChanged.register(self._handleDisplayChanged)
		self._onTerminate(displayChanged.unregister, self._handleDisplayChanged)
		# Settings are read through `config.conf`, which is profile aware, so switching
		# profile can change the layout, the focus segment and the panning direction
		# without any braille event firing.
		config.post_configProfileSwitch.register(self._handleProfileSwitch)
		self._onTerminate(config.post_configProfileSwitch.unregister, self._handleProfileSwitch)
		self._membersChanged = devicesModule.membersChangedAction()
		"""The composite display's own notification, or None if the driver cannot be reached.

		A member lost and reconnected leaves the display the same size, so `displaySizeChanged`
		does not fire, but the segments are named after physical displays and must be rebuilt.
		"""
		if self._membersChanged is not None:
			self._membersChanged.register(self._handleDisplayChanged)
			self._onTerminate(self._membersChanged.unregister, self._handleDisplayChanged)
		self.rebuildBuffer()

	def _onTerminate(self, undo, *args) -> None:
		"""Register one step of the teardown, to run on terminate in reverse order of registering.

		Each step that fails is logged and the rest still run, so one fault cannot leave NVDA
		patched. An installer that changes several things has its undo registered before it runs,
		so a failure part way through is undone too; such an undo is safe whatever went in.
		"""
		self._teardown.callback(_runLoggingFailure, undo, *args)

	def _releaseDisplay(self) -> None:
		"""Take what the add-on shows off the display, and give NVDA its own buffers back.

		Each part runs even if one before it fails.
		"""
		for part in (
			self._endFlowBand,
			self.graphicsMode.onTerminate,
			self.appLayouts.terminate,
			self.stopAllMonitoring,
			self._restoreOriginalBuffer,
			# Before the patches go, so a region already reading a row reads NVDA's line again.
			tableRowLine.clear,
		):
			_runLoggingFailure(part)

	def _endFlowBand(self) -> None:
		"""Stop the flow band for good, if there is one."""
		if self.flowBand is not None:
			self.flowBand.onTerminate()
			self.flowBand = None

	def _shutDown(self) -> None:
		"""Take out everything `_install` put in, and stop anything still queued from running.

		Shared by `terminate` and a start that failed part way, and safe to run twice.
		"""
		# Set first, so that anything already queued sees it even if a step below fails.
		self._terminated = True
		_runLoggingFailure(self.cancelPendingDrawings)
		try:
			self._teardown.close()
		finally:
			global _plugin
			if _plugin is self:
				_plugin = None

	def terminate(self):
		try:
			self._shutDown()
		finally:
			super().terminate()

	# Events

	def event_gainFocus(self, obj, nextHandler):
		"""Let the focus through, then finish anything that was waiting for it.

		For two things: routing into a column of a list view, and keeping a drawn chart on the
		point NVDA's chart navigation moved to (see `_followChart`). A routed column is set on
		the navigator object, which NVDA resets when the focus arrives, so it is put back after
		`nextHandler`. See `flowObjectTable.columnWantedAfterFocus`.
		"""
		nextHandler()
		try:
			from . import flowObjectTable

			flowObjectTable.columnWantedAfterFocus(obj)
		except Exception:
			log.debugWarning("Could not finish going to a table column", exc_info=True)
		try:
			self._followChart(obj)
		except Exception:
			log.debugWarning("Could not follow the chart navigation onto the display", exc_info=True)
		try:
			self.appLayouts.followFocus(obj)
		except Exception:
			log.debugWarning("Could not follow the focus with the app layout", exc_info=True)

	def _followChart(self, obj) -> None:
		"""Mark the point of the chart on the display that the screen reader has just moved to.

		Silently, since the screen reader has just said it, and only for a point of the chart
		that is drawn. See docs/design/excel-chart-plan.md.

		:param obj: the new focus.
		"""
		mode = self.graphicsMode
		if not mode.active:
			return
		key = getattr(mode.source, "chartKey", None)
		if key is None:
			return
		point = chartSource.pointOf(obj)
		if point is None or point.key != key:
			return
		mode.markPoint(point.index, point.seriesName, getattr(point, "series", None))

	def onPointMarked(self, index: int) -> None:
		"""The reader moved the mark on a chart, by a step or a press. Move the screen reader's own
		place in the chart to match, so its next arrow key goes on from there.

		Called by `GraphicsMode`, never for a mark the screen reader's navigation made. Only when
		the focus is in the very chart that is drawn; a reader who has gone back to the cells is
		left there.

		:param index: the point now marked, counting from 0.
		"""
		mode = self.graphicsMode
		key = getattr(mode.source, "chartKey", None) if mode.active else None
		if key is None:
			return
		try:
			focus = api.getFocusObject()
			chart = chartSource.chartOf(focus)
			if chart is None or chart.key() != key:
				return
			lead = getattr(focus, "brlMultilineLeadTo", None)
			if lead is not None:
				lead(index)
		except Exception:
			log.debugWarning("Could not move the chart navigation to the marked point", exc_info=True)

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

	@property
	def activePanels(self) -> tuple[BraillePanel, ...]:
		""":return: the claims laid over the view in force, in the order they were made.

		A copy: claims are changed only through L{activatePanel} and L{deactivatePanel}.
		"""
		return tuple(self._activePanels)

	@property
	def isTerminated(self) -> bool:
		""":return: whether L{terminate} has run, so work already queued should not."""
		return self._terminated

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

		Replaces the whole arrangement; prefer `activatePanel` where only part of the display
		is wanted. One activated view at a time. Call `restoreConfiguredView` to hand the
		display back.

		:param view: the view to show.
		:raises ValueError: if the view's panels do not tile the display, or a segment reaches
			cells that no display behind a composite one actually has.
		:raises LookupError: if the view's focus segment does not exist.
		"""
		numRows, numCols = self._displayDimensions()
		# Validate before storing, so a view that does not fit leaves the display as it was.
		# A view bypasses `deviceView`, so the hardware check is made here.
		view.validate(numRows, numCols)
		validateAgainstHardware(view, deviceMap(), numCols)
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

		EXPERIMENTAL. An owner is not told when its claim is rebuilt or dropped; expect a lease
		object with those callbacks, which will change the return type. Do not depend on it
		from another add-on yet.

		A panel with the same name as one already active replaces it. The panel is held by
		reference and read again on every rebuild, so it must not be mutated afterwards.

		:param panel: the claim to lay over the current view.
		:raises ValueError: if the claim falls outside the display, its segments do not fit
			within it, or it would put a segment over cells that no display behind a composite
			one actually has.
		:raises LookupError: if the claim would take the focus segment without offering one
			in its place.
		"""
		numRows, numCols = self._displayDimensions()
		candidate = [*self._panelsExcept(panel.name), panel]
		# Compose against the view in force to find out whether this claim can be honoured,
		# before storing anything. A claim that cannot leaves the display as it was.
		view, _kept = self._composeView(numRows, numCols, candidate, strict=True)
		view.validate(numRows, numCols)
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

	def _composeView(
		self,
		numRows: int,
		numCols: int,
		panels: list[BraillePanel],
		strict: bool,
	) -> tuple[SegmentView, list[BraillePanel]]:
		"""Lay a list of claims over the base view, in priority order.

		One loop for both callers, so that a claim `activatePanel` accepts is a claim the
		rebuild keeps.

		Each claim is composed into a candidate rather than into the view, so that a claim
		which reaches cells no hardware has is dropped whole rather than leaving the eviction
		it caused behind — including the eviction of a dead column mask.

		:param numRows: number of rows on the display.
		:param numCols: number of columns on the display.
		:param panels: the claims to lay over it.
		:param strict: raise on the first claim that does not fit, rather than dropping it.
		:return: the composed view, and the claims it kept, in priority order.
		:raises ValueError: if strict and a claim does not fit, or would put a segment over
			cells that no display behind a composite one has.
		:raises LookupError: if strict and a claim takes the focus segment without replacing it.
		"""
		devices = deviceMap()
		view = self.baseView(numRows, numCols, devices)
		kept: list[BraillePanel] = []
		for panel in _byPriority(panels):
			try:
				candidate = view.withPanel(panel, numRows, numCols)
				validateAgainstHardware(candidate, devices, numCols)
			except (ValueError, LookupError):
				if strict:
					raise
				log.warning(
					f"BrlMultiline: panel {panel.name!r} cannot be shown on "
					f"a {numRows} by {numCols} display; dropping it",
					exc_info=True,
				)
				continue
			view = candidate
			kept.append(panel)
		return view, kept

	def baseView(self, numRows: int, numCols: int, devices: list[DeviceInfo]) -> SegmentView:
		""":return: the view claims are laid over: an activated one, else the configured one.

		:param numRows: number of rows on the display.
		:param numCols: number of columns on the display.
		:param devices: the physical displays behind a composite one, empty for an ordinary one.
		"""
		if self._activeView is not None:
			try:
				self._activeView.validate(numRows, numCols)
				# Rechecked on every rebuild rather than only when the view was activated,
				# because the displays behind a composite one can change under a view that was
				# perfectly good when it was made.
				validateAgainstHardware(self._activeView, devices, numCols)
				return self._activeView
			except (ValueError, LookupError):
				# The display changed under the view and its panels no longer fit it.
				log.warning(
					f"BrlMultiline: view {self._activeView.name!r} cannot be shown on "
					f"a {numRows} by {numCols} display; returning to the configured view",
					exc_info=True,
				)
				self._activeView = None
		if devices:
			# Hooked in here because this is the one place that knows both the composite's own
			# configuration key and the keys of the displays behind it. It does its work once
			# and leaves a mark saying so, so every later rebuild costs one read.
			bmConfig.migrateReverseScrollButtons(
				bmConfig.getDisplayKey(),
				[device.displayKey for device in devices],
			)
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

		Claims are re-composed on every rebuild, so they survive a settings change or a
		display swap.
		"""
		view, kept = self._composeView(numRows, numCols, self._activePanels, strict=False)
		# Restored to the order they were claimed in. `kept` came out in priority order, and
		# storing that would make the sort cumulative: a claim that lost a tie once would keep
		# losing it, and the order a reader made their claims in would quietly stop meaning
		# anything.
		self._activePanels = [panel for panel in self._activePanels if panel in kept]
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
				f"BrlMultiline: could not build view {view.name!r}; falling back",
				exc_info=True,
			)
			self._activeView = None
			self._activePanels = []
			container = self._fallbackContainer(handler, dimensions)
		self._carryOverMonitors(container)
		# Before the flow is carried over, which asks whether a layout is up.
		self.appLayouts.onRebuilt()
		wasShowingMainBuffer = handler.buffer is handler.mainBuffer
		handler.mainBuffer = container
		if wasShowingMainBuffer:
			handler.buffer = container
		self._installMessageBuffer(handler, container)
		self._refreshDisplay()
		# After the display has been redrawn from the focus, so that a pinned object is
		# written over the fresh layout rather than under it.
		self.refreshMonitors()
		self._carryOverFlow(container)
		self._carryOverGraphics()
		self._applyFlow()

	def _applyFlow(self) -> None:
		"""Claim or give back the flow band, following the setting for this display and profile.

		While a drawing or an app layout is up, the flow is left entirely alone: stopping it
		would discard its controller and the reader's place. The priority ladder in `panels`
		decides who gets the cells; see `FlowBand.onSuspended`. This applies whether or not
		the two want the same rows.

		Called after every rebuild, so the setting follows profile switches and restarts.
		"""
		if self._terminated or self._applyingFlow:
			# Claiming the band rebuilds the display, which arrives back here. One pass does it.
			return
		if self.graphicsMode.active:
			# The claim stays in `_activePanels`, outranked, so leaving the figure gives the rows
			# back and `_carryOverFlow` redraws from the surviving controller.
			return
		if self.appLayouts.isUp:
			# The same for an app layout (see `panels.PRIORITY_APP_LAYOUT`), except where the
			# layout's focus segment asked for the flow, which draws there. Geometry is checked
			# again when the layout goes.
			if self.appLayouts.flowSegmentKey() is not None:
				self._startOrStopFlow(bmConfig.shouldClaimFlowBand())
			return
		wanted = bmConfig.shouldClaimFlowBand()
		if wanted and self._flowBandMoved():
			with self._changingFlow():
				self.stopFlow()
				self.startFlow()
			return
		self._startOrStopFlow(wanted)

	def _flowBandMoved(self) -> bool:
		""":return: whether the flow band showing now is not where the settings put it.

		A panel claim carries the rectangle it was activated with. Re-composing that panel
		after a settings or profile change preserves the old rectangle, so an enabled flow has
		to be reclaimed when its configured rows or display changed. False with no band.
		"""
		band = self.flowBand
		if band is None:
			return False
		segment = band.segment()
		try:
			return segment is None or segment.rect != band.bandRect()
		except Exception:
			# Treat an unreadable target as moved and let `startFlow` give the complete
			# account of why the replacement could not be shown.
			log.debugWarning("BrlMultiline: could not compare the flow band geometry", exc_info=True)
			return True

	def _startOrStopFlow(self, wanted: bool) -> None:
		"""Start the flow, or stop it, where that is not what it is doing now."""
		if wanted == (self.flowBand is not None):
			return
		with self._changingFlow():
			if wanted:
				self.startFlow()
			else:
				self.stopFlow()

	@contextlib.contextmanager
	def _changingFlow(self):
		"""Hold off L{_applyFlow} while the flow is started or stopped here.

		Claiming or giving back the band rebuilds the display, and the rebuild calls
		L{_applyFlow} again. One pass does it.
		"""
		self._applyingFlow = True
		try:
			yield
		finally:
			self._applyingFlow = False

	def startFlow(self) -> bool:
		"""Claim a band and read the reader's content as a flow in it.

		:return: whether the band is claimed. It is kept even with nothing to flow, showing
			the focus as an undivided display would.
		"""
		from .flowBand import FlowBand

		band = FlowBand(self)
		if not band.start():
			band.stop()
			log.debugWarning(f"BrlMultiline: the flow band could not be claimed: {band.lastError}")
			return False
		self.flowBand = band
		return True

	def stopFlow(self) -> None:
		"""Give the flow band back, leaving the configured layout in its place."""
		band = self.flowBand
		# Cleared first: giving the band back rebuilds the display, and the rebuild must not
		# find a band that is on its way out and try to draw it again.
		self.flowBand = None
		if band is not None:
			band.stop()

	def _carryOverFlow(self, container: DisplayContainer) -> None:
		"""Draw the flow again after a rebuild, or drop it if its band has gone.

		A re-composed claim comes back with empty cells, so its owner is told. A band with its
		rows redraws; one whose rows are gone is evicted; one whose rows went to a drawing or
		app layout is suspended, keeping its controller and the reader's place.

		:param container: the container just installed.
		"""
		band = self.flowBand
		if band is None:
			return
		if band.segment() is None:
			if self.graphicsMode.active or self.appLayouts.isUp:
				# Borrowed rather than lost. A drawing outranks the flow and has taken its rows
				# for as long as it is up; the claim is still in `_activePanels` and gets them
				# back when the figure goes. Keeping the band keeps its controller, and the
				# controller is where the reader's place in the document lives.
				band.onSuspended()
				return
			band.onEvicted()
			self.flowBand = None
			return
		band.onRebuilt()

	def _carryOverGraphics(self) -> None:
		"""Draw the figure again after a rebuild, or drop it if its claim has gone.

		As `_carryOverFlow`, but a graphics claim has no segments, so a claim missing from the
		active panels is one the rebuild dropped.
		"""
		mode = self.graphicsMode
		if not mode.active:
			return
		if any(panel.name == GRAPHICS_PANEL_NAME for panel in self._activePanels):
			mode.onRebuilt()
			return
		log.debugWarning("BrlMultiline: the graphics claim did not survive the rebuild; dropping the figure")
		mode.onEvicted()

	def _fallbackContainer(self, handler, dimensions) -> DisplayContainer:
		"""Build the simplest container the connected display can have.

		For when the intended arrangement could not be built. A composite gets one segment per
		physical display, since one segment across everything would lose text on a narrower
		display. An ordinary display gets NVDA's single segment.

		:param handler: the braille handler.
		:param dimensions: the display's dimensions.
		:return: the container.
		:raises ValueError: if even a single segment cannot be built, which leaves the caller
			nothing to install and the add-on nothing to do.
		"""
		numRows, numCols = dimensions.numRows, dimensions.numCols
		devices = deviceMap()
		if devices:
			try:
				return DisplayContainer(handler, deviceFallbackView(numRows, numCols, devices))
			except (ValueError, LookupError):
				# The bands do not describe this display, so there is no telling where the dead
				# columns are. Nothing here can mask them; the warning is all there is to give.
				log.error(
					f"BrlMultiline: could not arrange this display as its {len(devices)} displays "
					"either; showing it as one segment. Anything that lands on a display narrower "
					"than the widest will be lost.",
					exc_info=True,
				)
		return DisplayContainer(handler, singleSegmentView(numRows, numCols))

	def _messageRect(self, container: DisplayContainer) -> SegmentRect:
		""":return: the rectangle NVDA's flash messages should appear in.

		:param container: the container about to be shown.
		"""
		number = bmConfig.getMessageSegment()
		if number != -1:
			try:
				return container.segments[container.resolveSegmentNumber(number)].rect
			except LookupError:
				log.warning(
					f"BrlMultiline: no segment {number} for messages in this layout; "
					"using the segment that follows the focus",
				)
		return container.focusSegment.rect

	def _installMessageBuffer(self, handler, container: DisplayContainer) -> None:
		"""Put messages in a segment rather than across the whole display.

		NVDA's message buffer spans the full width from cell 0, which on a composite of unequal
		displays reaches cells no hardware has. See `messages`.

		Made once and retargeted, never replaced, because the handler tests
		`buffer is messageBuffer` to decide whether a message is showing.

		:param handler: the braille handler.
		:param container: the container just installed, for the segment rectangles.
		"""
		try:
			rect = self._messageRect(container)
			if self._messageBuffer is None:
				self._messageBuffer = MessageBuffer(handler, rect)
			else:
				self._messageBuffer.setRect(rect)
			if handler.messageBuffer is not self._messageBuffer:
				# The first install, or another add-on put its own buffer there since. Whatever it
				# is goes back on termination.
				self._originalMessageBuffer = handler.messageBuffer
				if handler.buffer is handler.messageBuffer:
					# A message is up in the buffer about to be replaced. Its regions belong to
					# that buffer, so it is taken down rather than carried over; the rebuild
					# redraws from the focus immediately afterwards.
					self._dismissShowingMessage(handler)
				handler.messageBuffer = self._messageBuffer
		except Exception:
			# Messages going to the wrong place is a poor reason to lose the whole layout.
			log.error("BrlMultiline: could not install the message buffer", exc_info=True)

	@staticmethod
	def _dismissShowingMessage(handler) -> None:
		"""Take down the message being shown, before the buffer showing it changes hands.

		Through NVDA's own `_dismissMessage`, which also returns to the main buffer, stops the
		timeout and notifies `_post_dismissBrailleMessage`. `shouldUpdate=False` because every
		caller redraws straight afterwards.

		:param handler: the braille handler, with a message showing.
		"""
		try:
			handler._dismissMessage(shouldUpdate=False)
		except Exception:
			# The two parts that must happen even so. The timer is the dangerous one: it fires
			# into `_dismissMessage`, whose precondition is that a message is showing, so left
			# running it would clear the main buffer some seconds later.
			log.error("BrlMultiline: could not dismiss the message being shown", exc_info=True)
			handler.buffer = handler.mainBuffer
			callLater = getattr(handler, "_messageCallLater", None)
			if callLater is not None:
				callLater.Stop()
				handler._messageCallLater = None

	def _restoreMessageBuffer(self) -> None:
		"""Give NVDA its own message buffer back."""
		if self._messageBuffer is None:
			return
		handler = braille.handler
		try:
			if handler is not None and handler.messageBuffer is self._messageBuffer:
				if handler.buffer is self._messageBuffer:
					# Dismissed rather than carried across, because the regions belong to a
					# buffer that is about to stop being the handler's.
					self._dismissShowingMessage(handler)
				handler.messageBuffer = self._originalMessageBuffer
		except Exception:
			log.error("BrlMultiline: could not restore NVDA's message buffer", exc_info=True)
		finally:
			self._messageBuffer = None
			self._originalMessageBuffer = None

	def _carryOverMonitors(self, container: DisplayContainer) -> None:
		"""Keep the pins whose segment survived into a new container, and drop the rest.

		A pin names its segment by key, so it survives any rebuild that keeps that segment:
		a settings change elsewhere on the display, a claim laid over other rows, a display
		reconnecting at the same size. It is dropped in three cases:

		1. Its segment is gone.
		2. A claim has taken over a segment of the same key, since the new owner would
			redraw over the pin and the two would fight for the cells.
		3. Its segment now follows the system focus, which `startMonitoring` also refuses.

		A pin whose segment has gone gets one chance at a new home first; see `rehomeMonitor`.

		:param container: the container about to be installed.
		"""

		def keeps(key: str) -> bool:
			return (
				container.hasKey(key)
				and not container.segmentForKey(key).isReserved
				and key != container.focusSegmentKey
			)

		survivors = {key: monitor for key, monitor in self._monitors.items() if keeps(key)}
		present = {device.driverName for device in deviceMap()}
		for key in self._monitors.keys() - survivors.keys():
			driverName = driverNameForSegmentKey(key)
			gone = driverName is not None and driverName not in present
			carrying = any(monitor is self._monitors[key] for monitor in self.focusMoves.pinsInFlight)
			if gone or carrying:
				# Re-homed only when its display has gone or a focus move is carrying it. Any
				# other loss of a segment is a decision, and the pin stays released.
				why = (
					f"{driverName} has gone"
					if gone
					else f"segment {key!r} is where the focus command put it and is not in the new layout"
				)
				home = self.rehomeMonitor(container, survivors)
				if home is not None:
					log.info(f"BrlMultiline: {why}; the object pinned to it moves to segment {home!r}")
					monitor = self._monitors[key]
					# The key in this dictionary is only how the plugin finds it. What decides
					# where it draws is the monitor's own key and the regions it has already
					# stamped, so it is told to move rather than merely filed differently.
					monitor.moveTo(home)
					survivors[home] = monitor
					continue
				# Nowhere in *this* pass, which for a pin the focus is carrying does not mean
				# nowhere: the command is still in the middle of rebuilding, and the pass that
				# gives the band's rows back has not happened yet. `_landPinsInFlight` settles
				# those once the move has finished, so this is not the release it looks like.
				log.info(
					f"BrlMultiline: {why} and there is nowhere left to put what was pinned to it, "
					f"so it {'waits for the move to settle' if carrying else 'is released'}",
				)
			else:
				log.debug(
					f"BrlMultiline: segment {key!r} is gone, claimed, or now follows the focus, "
					f"so its pinned object is released",
				)
		self._monitors = survivors

	def rehomeMonitor(self, container: DisplayContainer, taken: dict) -> str | None:
		"""Find somewhere for a pin whose segment has gone.

		The first segment in display order that is not the focus, not claimed and not already
		pinned. Never the focus segment, so with one segment left the pin is dropped.

		:param container: the container about to be installed.
		:param taken: the homes already spoken for, which are not offered twice.
		:return: the key to move to, or None to release the pin.
		"""
		for spec in container.specs:
			if spec.key in taken or spec.key == container.focusSegmentKey or spec.isReserved:
				continue
			return spec.key
		return None

	def _restoreOriginalBuffer(self) -> None:
		"""Put NVDA's own buffers back."""
		# First, and outside the early return below: the message buffer is installed
		# separately and has to come back whether or not the main buffer was ever replaced.
		self._restoreMessageBuffer()
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

		Both events are needed: `displaySizeChanged` misses a swap to a display of the same
		size, and `displayChanged` misses a size change under the same driver. Deferred,
		because this can run inside the handler's `displayDimensions` property.
		"""
		glyphFlow.forget()
		self._scheduleRebuild()

	def _handleProfileSwitch(self, **kwargs) -> None:
		"""Rebuild when NVDA switches configuration profile.

		The settings are profile aware, so a switch can change them with no braille event.
		Deferred, because the switch is still being applied. The glyph vocabulary's words are
		dropped too, since a profile can change the language.
		"""
		glyphFlow.forget()
		self._reloadKeyLayers()
		self._reportMemberDivergence()
		self._scheduleRebuild()

	def _reloadKeyLayers(self) -> None:
		"""Read the layered keys the new profile holds, and say which layer that turned off.

		A layer that is on stays on if the new profile still has it; otherwise it goes off and
		the reader is told.
		"""
		try:
			gone = keyLayerDispatch.reload()
		except Exception:
			log.error("BrlMultiline: could not read the layered keys after a profile switch", exc_info=True)
			return
		for _device, layer in gone:
			# Translators: reported when a profile switch removes the layer of keys that was on. The
			# placeholder is its name.
			ui.message(_("{name} layer off").format(name=keyLayerDispatch.layerName(layer)))

	def _reportMemberDivergence(self) -> None:
		"""Say so in the log when the configured members are not the ones the composite opened for.

		The member list lives only in the base configuration (see `vdConfig`), so this means it
		was edited without reopening the composite, which NVDA does not do while the driver
		name is unchanged. Compared against the composite's configured members, not the ones it
		is driving, so a display that is switched off is not reported. Said once per distinct
		divergence.
		"""
		running = configuredMembers()
		if running is None:
			return
		try:
			from brailleDisplayDrivers.brlMultilineVirtual import vdConfig

			configured = [spec.driverName for spec in vdConfig.getDevices()]
		except Exception:
			log.debugWarning("BrlMultiline: could not read the configured member list", exc_info=True)
			return
		if configured == running:
			self._reportedDivergence = None
			return
		divergence = (tuple(configured), tuple(running))
		if divergence == self._reportedDivergence:
			return
		self._reportedDivergence = divergence
		log.warning(
			f"BrlMultiline: the displays configured are {configured}, but the combined "
			f"display was opened for {running}. A changed list is not applied until the combined "
			"display is selected again in NVDA's braille settings.",
		)

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
		# A display that reconnected while a drawing is up, or a profile whose layers were just read,
		# can each change which layers should be on without the drawing changing. Both come here.
		self.reconcileKeyLayers()

	# Object monitoring

	@property
	def pins(self) -> dict[str, ObjectMonitor]:
		"""The reader's pins, by the key of the segment each is on.

		The plugin's own dictionary, not a copy: a focus move files a pin it carries under its new
		key here. See `focusDisplays`.
		"""
		return self._monitors

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
		# Pinning a table the reader is already reading in columns pins it in columns. Asked
		# of the request rather than of the band, because a one row display has no band and
		# that is exactly the arrangement this exists for: ask for columns on the Focus, pin
		# the table to the Monarch, read it there. Going through the band meant the answer
		# was always no in the one case it was built for.
		monitor.wantsColumns = wantsColumns(self.tableRequest.wanted, obj)
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
		# Before it is forgotten: a pin read as a flow has a controller attached to the
		# segment, and dropping the monitor without detaching it would leave the segment
		# drawing from a flow nothing owns any more.
		self._monitors[key].stop()
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
			self._monitors[key].stop()
			del self._monitors[key]
			if container is not None and container.hasKey(key):
				container.clear(key)

	def reportAboutTheDisplay(self, text: str) -> None:
		"""Say something about what the display is showing, without writing it there.

		Speech only, because `ui.message` would also flash the text over the very content it
		describes. Use `ui.message` for anything the display cannot show, such as "not in a
		table", where the flash is the message.

		:param text: what to say.
		"""
		try:
			import speech

			speech.speakMessage(text)
		except Exception:
			log.debugWarning("Could not speak a message about the display", exc_info=True)
			ui.message(text)

	def layOutPinnedTables(self, wanted: bool, obj) -> int:
		"""Show or stop showing the pins on one table in columns.

		So the column command reaches pins as well as the band. Only pins on this table.

		:param wanted: whether that table should be read in columns.
		:param obj: an object in the table, as the reader's own position gives it.
		:return: how many pins changed.
		"""
		from .flowTableSource import sameTable, tableAt

		here = tableAt(obj)
		if here is None:
			return 0
		changed = 0
		for monitor in list(self._monitors.values()):
			try:
				theirs = tableAt(monitor.pinned)
			except Exception:
				log.debugWarning("Could not tell what a pin is showing", exc_info=True)
				continue
			if theirs is None or not sameTable(theirs.key, here.key):
				continue
			if monitor.wantsColumns == wanted:
				continue
			monitor.wantsColumns = wanted
			changed += 1
		if changed:
			self.refreshMonitors()
		return changed

	def refreshMonitors(self, reveal: str | None = None) -> None:
		"""Redraw every pinned object. Called when a monitored object may have changed.

		The single place pins are drawn. A pin that cannot be drawn now is kept and waits for
		the next refresh.

		:param reveal: the key of a segment to show from the start of its content rather than
			where it was panned to, for a pin just made.
		"""
		if bmConfig.isSpeechOutputMode():
			# NVDA is showing speech on the display and handles no moves, so nothing would
			# refresh or remove a pin drawn now.
			log.debug("BrlMultiline: not drawing pinned objects while braille shows speech")
			return
		for key, monitor in list(self.everyMonitor().items()):
			monitor.refresh(reveal=key == reveal)

	def everyMonitor(self) -> dict[str, ObjectMonitor]:
		""":return: the reader's pins and the app layout's segments, which are refreshed alike.

		Held separately because only the reader's pins are carried, re-homed and counted. The
		keys cannot collide: the layout's are all under `appLayout.PANEL_NAME`.
		"""
		return {**self._monitors, **self.appLayouts.liveMonitors()}

	def _routeIntoMonitor(self, key: str, segment, position: int) -> bool:
		"""Take a routing key press in a segment showing an object, a pin or an app layout's.

		:return: whether the press was in such a segment, and so dealt with here.
		"""
		if key not in self.everyMonitor():
			return False
		return monitorRouting.route(segment, position)

	def refreshMonitorsInTurn(self, allowance: float) -> bool:
		"""Refresh the pins that a timer asked about, a slice of a core cycle at a time.

		Pins are re-read on a timer, so a pass stops when its allowance is spent and the next
		pass carries on from there. The pins a pass reads are fixed when it starts. The app
		layout and the pins take turns, so a slow layout cannot starve the pins. One slow pin
		is not shortened, but the next waits for the next cycle.

		:param allowance: how long this pass may spend before leaving the rest for the next.
		:return: whether any pin is still waiting, so the caller knows to come back at once
			rather than after another whole interval.
		"""
		if bmConfig.isSpeechOutputMode():
			# See `refreshMonitors`: a pin drawn beside speech has nothing to remove it.
			return False
		deadline = time.monotonic() + allowance
		if not self._pinsTurn:
			try:
				# The app layout first, through its own scheduler, within the same allowance: its
				# callables are calls into the application and its segments are read like pins.
				# A pass that runs out carries on in the next slice. See
				# `AppLayoutHost.refreshInTurn`.
				if self.appLayouts.refreshInTurn(deadline):
					return True
			except Exception:
				log.debugWarning("Could not bring the app layout up to date", exc_info=True)
			# The layout's pass is over, so the pins' comes next whatever this slice has left.
			# Starting the layout again first would let one slow pane take every slice.
			self._pinsTurn = True
			if not self._monitors:
				self._pinsTurn = False
				self._pinsToRead = None
				return False
			if time.monotonic() >= deadline:
				return True
		if self._pinsToRead is None:
			self._pinsToRead = list(self._monitors)
		while self._pinsToRead:
			monitor = self._monitors.get(self._pinsToRead.pop(0))
			if monitor is None:
				# Unpinned while this pass was running.
				continue
			monitor.refresh()
			if self._pinsToRead and time.monotonic() >= deadline:
				return True
		self._pinsToRead = None
		self._pinsTurn = False
		return False

	# Scrolling

	def scrollSegment(self, segmentNumber: int, forward: bool, gesture=None) -> None:
		"""Scroll one segment, whether or not it holds focus.

		:param segmentNumber: the segment to scroll.
		:param forward: True to scroll forward, False to scroll back.
		:param gesture: the gesture that asked, if any. Its display decides which way the
			panning keys go. `panning`'s record covers only NVDA's own panning commands.
		"""
		container = self.container
		if container is None:
			# Translators: reported when a command needs segments but none are configured.
			ui.message(_("BrlMultiline is not active"))
			return
		if panning.shouldReverseForGesture(gesture):
			forward = not forward
		if forward:
			container.scrollForward(segmentNumber)
		else:
			container.scrollBack(segmentNumber)

	def displaySegmentNumber(self, displayOrdinal: int, segmentOrdinal: int) -> int | None:
		"""Find a segment by which display it is on, reporting it if there is no such segment.

		:param displayOrdinal: which display, counting from 0 at the top.
		:param segmentOrdinal: which of that display's segments, counting from 0.
		:return: the segment's index, or None if there is no such segment now.
		"""
		container = self.container
		if container is None:
			# Translators: reported when a command needs segments but none are configured.
			ui.message(_("BrlMultiline is not active"))
			return None
		number = resolveDisplaySegment(container.rects, deviceMap(), displayOrdinal, segmentOrdinal)
		if number is None:
			ui.message(
				# Translators: reported when a command names a segment that the current
				# arrangement does not have. Placeholders are a segment number and the name of
				# one of the combined displays.
				_("No segment {segment} on {display}").format(
					segment=segmentOrdinal,
					display=_displayName(displayOrdinal),
				),
			)
		return number

	# Which of several displays follows the focus

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
		# Translators: input help message for a command. An app layout is an application
		# arranging the display around where the focus is.
		description=_("App layouts: sets them aside for now, or brings them back"),
		category=SCRIPT_CATEGORY,
	)
	def script_toggleAppLayouts(self, gesture):
		"""Set app layouts aside until NVDA restarts, or bring them back.

		Only means anything where the setting allows them, and says so where it does not,
		rather than flipping a state that would surprise the reader the day they allow it.
		"""
		if not bmConfig.shouldAllowAppLayouts():
			# Translators: reported when asked to set app layouts aside on a display where the
			# settings do not allow them at all.
			ui.message(_("Applications may not arrange this display. Allow it in the BrlMultiline settings"))
			return
		self._appLayoutsSuspended = not self._appLayoutsSuspended
		if self._appLayoutsSuspended:
			self.appLayouts.takeDown("set aside by the reader")
			# Translators: reported when app layouts have been set aside for now.
			ui.message(_("App layouts off"))
		else:
			# Translators: reported when app layouts are allowed again.
			ui.message(_("App layouts on"))
			# Brought back where the reader is, rather than at the next focus change.
			self.appLayouts.followFocus(api.getFocusObject())

	@script(
		# Translators: input help message for a command. An app layout is an application
		# arranging the display around where the focus is.
		description=_("App layouts: spreads them across combined displays, or keeps them on the focus display"),
		category=SCRIPT_CATEGORY,
	)
	def script_toggleAppLayoutSpread(self, gesture):
		"""Choose whether app layouts spread across displays, for the profile in force.

		Saved in the profile in force, so it can differ per application. A layout already up
		is placed again at once.
		"""
		spread = not bmConfig.shouldSpreadAppLayouts()
		bmConfig.setSpreadAppLayouts(spread)
		if spread:
			# Translators: reported when app layouts will be spread across combined displays.
			ui.message(_("App layouts spread across displays"))
		else:
			# Translators: reported when app layouts will be kept on the display holding the focus.
			ui.message(_("App layouts kept on the focus display"))
		self.appLayouts.placeAgain()

	def refreshAppLayoutInTurn(self, allowance: float) -> bool:
		"""Refresh the app layout's segments alone, within an allowance. See `patches.LAYOUT_QUIET_AGE`.

		The same scheduler as every other refresh of the layout, so a pass that runs out carries
		on from the segment after the last one read rather than starting again at the first.

		:return: whether the pass is unfinished, so the timer comes back at once.
		"""
		if bmConfig.isSpeechOutputMode():
			return False
		return self.appLayouts.refreshInTurn(time.monotonic() + allowance)

	def brlMultilineContentChanged(self, appModule=None) -> None:
		"""Tell BrlMultiline that what an app layout shows has changed. Part of the app module API.

		For an app module to call when it knows something arrived, such as a new message in
		a chat history or a new document in a reading pane, so the layout shows it now rather
		than on the next tick of the clock. Found by name on the running global plugins, so the
		caller imports nothing from this add-on:

		```python
		for plugin in globalPluginHandler.runningPlugins:
			changed = getattr(plugin, "brlMultilineContentChanged", None)
			if changed is not None:
				changed(self)
		```

		Cheap to call often. Calls close together are answered once, a moment after the first,
		and a call from an application whose layout is not up does nothing.

		:param appModule: the app module calling. A call from any other than the one whose
			layout is up is ignored. None means whichever is up.
		"""
		try:
			self.appLayouts.contentChanged(appModule)
		except Exception:
			log.debugWarning("Could not answer a change in the app layout", exc_info=True)

	def configuredFocusRect(self) -> SegmentRect | None:
		""":return: where the reader's own arrangement puts the focus, whatever claims lie over it.

		Read from the base view claims are composed over. None if it cannot be built, as while
		the display is changing.
		"""
		numRows, numCols = self._displayDimensions()
		try:
			view = self.baseView(numRows, numCols, deviceMap())
		except Exception:
			log.debugWarning("Could not read where the reader's arrangement puts the focus", exc_info=True)
			return None
		for spec in view.flatten():
			if spec.key == view.focusSegmentKey:
				return spec.rect
		return None

	def appLayoutsInForce(self) -> bool:
		""":return: whether an app module may arrange this display now: allowed, and not set aside."""
		return bmConfig.shouldAllowAppLayouts() and not self._appLayoutsSuspended

	def appLayoutRoom(self) -> appLayout.Room:
		"""The part of the display an app layout may have, the displays behind it, and the focus.

		Taken means holding a pin, or a claim other than the focus, such as a drawing. The flow
		band counts as free, since it is the focus, which a layout arranges. The focus
		rectangle is the reader's configured one, not the layout's. See `configuredFocusRect`.
		"""
		numRows, numCols = self._displayDimensions()
		container = self.container
		occupied = []
		within = None
		if container is not None:
			for spec in container.specs:
				if appLayout.isAppLayoutKey(spec.key):
					# The layout's own segments are its room, not something in its way, so the
					# room is the same whether or not a layout is up.
					continue
				if spec.key in self._monitors or (spec.isReserved and not spec.hostsSystemFocus):
					occupied.append(spec.rect)
			within = self.configuredFocusRect() or container.focusSegment.rect
		bands = [appLayout.Band(device.rowStart, device.numRows, device.numCols) for device in deviceMap()]
		return appLayout.Room(
			appLayout.freeArea(numRows, numCols, occupied, within),
			bands,
			within,
			bmConfig.shouldSpreadAppLayouts(),
		)

	@script(
		# Translators: input help message for a command.
		description=_("Moves the focus onto another of the combined braille displays"),
		category=SCRIPT_CATEGORY,
	)
	def script_changeFocusTrackingDisplay(self, gesture):
		self.focusMoves.changeDisplay()

	@script(
		# Translators: input help message for a command.
		description=_("Toggle reading the whole display as one flowing document"),
		category=SCRIPT_CATEGORY,
	)
	def script_toggleFlow(self, gesture):
		"""Turn the flowing reading of the display on or off, and remember the answer.

		The same switch as the flow settings, stored against the display in the profile in
		force.
		"""
		enabled = not bmConfig.isFlowEnabled()
		bmConfig.setFlowEnabled(enabled)
		self._applyFlow()
		if not enabled:
			# Translators: reported when the display stops reading as one flowing document.
			ui.message(_("Flow off"))
			return
		if self.flowBand is None:
			if not bmConfig.shouldClaimFlowBand():
				ui.message(
					# Translators: reported when the flow is turned on but no kind of content is
					# set to use it, which is chosen in the BrlMultiline flow settings.
					_("Flow on, but nothing is set to flow. See the BrlMultiline flow settings."),
				)
			else:
				# Translators: reported when a band for the flow could not be shown.
				ui.message(_("The flow could not be shown"))
			return
		# Translators: reported when the display starts reading as one flowing document.
		ui.message(_("Flow on"))

	@script(
		# Translators: input help message for a command.
		description=_("Flow: Toggles showing every line on one row, unwrapped, and panning across it"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowToggleUnwrapLines(self, gesture):
		"""Unwrap the lines on the band, or wrap them again, for the time being.

		The setting is not changed. See `FlowBand.unwrapLines` for how long the choice lasts.
		"""
		band = self.flowBand
		if band is None:
			# Translators: reported when a command needs the flow and it is off or cannot be shown.
			ui.message(_("The flow is not on"))
			return
		unwrapped = band.toggleUnwrapLines()
		self.reportAboutTheDisplay(
			# Translators: reported when every line is shown on one row, and the display pans across
			# it by its width rather than a long line continuing on the next row.
			_("Lines unwrapped")
			if unwrapped
			# Translators: reported when long lines continue on the next row of the display again.
			else _("Lines wrap"),
		)
		# Its layer of keys comes and goes with it. See `keyLayerContexts`.
		self.reconcileKeyLayers()

	@script(
		# Translators: input help message for a command.
		description=_("Flow: Pans unwrapped lines right by the width of the display"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowPanLinesRight(self, gesture):
		self._panLinesAcross(1)

	@script(
		# Translators: input help message for a command.
		description=_("Flow: Pans unwrapped lines left by the width of the display"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowPanLinesLeft(self, gesture):
		self._panLinesAcross(-1)

	def _panLinesAcross(self, by: int) -> None:
		"""Pan the band across its unwrapped lines, and say which cell of the lines is at the left.

		Nothing on the display shows how far across it is. Spoken only; see
		`reportAboutTheDisplay`.

		:param by: how many widths to move, negative for left.
		"""
		band = self.flowBand
		control = band.controller if band is not None else None
		if control is None or not control.isUnwrapped:
			# Translators: reported when a command pans across unwrapped lines and the lines on the
			# display wrap.
			ui.message(_("Lines are not unwrapped"))
			return
		moved = band.panAcross(by)
		where = control.describeAcross()
		if where is None:
			return
		first, _last, widest = where
		if moved:
			# Translators: reported after panning across unwrapped lines. The placeholder is the
			# column of the lines now at the left of the display, counted from 1.
			self.reportAboutTheDisplay(_("Column {column}").format(column=first))
		elif by < 0:
			# Translators: reported when panning left across unwrapped lines that are already at
			# their start.
			self.reportAboutTheDisplay(_("Start of the lines"))
		else:
			self.reportAboutTheDisplay(
				# Translators: reported when panning right across unwrapped lines and no line on the
				# display goes on any further. The placeholder is the column now at the left.
				_("Column {column}, no line goes further").format(column=first),
			)

	@script(
		# Translators: input help message for a command.
		description=_("Table: Toggles whether the headers of the table at the cursor are spoken"),
		category=SCRIPT_CATEGORY,
	)
	def script_toggleTableHeaderSpeech(self, gesture):
		"""Stop speaking the headers of the table the reader is in, or speak them again.

		For the table whose headers are a paragraph of help text, without a profile for its
		site. Speech only: braille shows the headers as it did. Works with or without a flow.
		See `tableHeaderSpeech`.
		"""
		ui.message(tableHeaderSpeech.toggle())

	@script(
		# Translators: input help message for a command.
		description=_("Table: Saves whether the headers of the table at the cursor are spoken"),
		category=SCRIPT_CATEGORY,
	)
	def script_saveTableHeaderSpeech(self, gesture):
		"""Save what the command above left for this table, so that it is heard that way every time.

		Into the table's saved layout, or a layout made for it that asks for nothing on the display.
		Works with or without a flow. See `tableHeaderSpeech.save`.
		"""
		ui.message(tableHeaderSpeech.save())

	@script(
		# Translators: input help message for a command.
		description=_("Table: Toggle table columns on or off"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowTableColumns(self, gesture):
		self.tables.toggleColumns()

	@script(
		# Translators: input help message for a command.
		description=_("Table: Toggle showing the table's rows on one line, cells divided by bars"),
		category=SCRIPT_CATEGORY,
	)
	def script_tableRowLine(self, gesture):
		self.tables.toggleRowLine()

	@script(
		# Translators: input help message for a command.
		description=_("Table: Show/Hide the current column"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowTableToggleColumn(self, gesture):
		self.tables.toggleCurrentColumn()

	@script(
		# Translators: input help message for a command.
		description=_("Table: Changes how the column you are in is trimmed when it does not fit"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowTableCutColumn(self, gesture):
		self.tables.cycleCurrentColumnTrim()

	@script(
		# Translators: input help message for a command.
		description=_("Table: Undo custom table layout"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowTableResetLayout(self, gesture):
		self.tables.resetLayout()

	@script(
		# Translators: input help message for a command.
		description=_("Table: Open table layout designer for the current table"),
		category=SCRIPT_CATEGORY,
	)
	@gui.blockAction.when(gui.blockAction.Context.MODAL_DIALOG_OPEN)
	def script_flowTableDesigner(self, gesture):
		self.tables.openDesigner()

	@script(
		# Translators: input help message for a command.
		description=_("Table: Save table layout"),
		category=SCRIPT_CATEGORY,
	)
	def script_rememberTableLayout(self, gesture):
		self.tables.rememberLayout()

	@script(
		# Translators: input help message for a command.
		description=_("Table: Delete saved table layout"),
		category=SCRIPT_CATEGORY,
	)
	def script_forgetTableLayout(self, gesture):
		self.tables.forgetLayout()

	@script(
		# Translators: input help message for a command.
		description=_("Table: Manage saved table layouts"),
		category=SCRIPT_CATEGORY,
	)
	@gui.blockAction.when(gui.blockAction.Context.MODAL_DIALOG_OPEN)
	def script_manageTableLayouts(self, gesture):
		self.tables.manageLayouts()

	@script(
		# Translators: input help message for a command.
		description=_("Table: Next columns"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowNextColumns(self, gesture):
		"""Move a table too wide to show at once, on the display the key was pressed on."""
		self.tables.turnColumnPage(1, gesture)

	@script(
		# Translators: input help message for a command.
		description=_("Table: Previous columns"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowPreviousColumns(self, gesture):
		"""Move a table back, on the display the key was pressed on."""
		self.tables.turnColumnPage(-1, gesture)

	def tablesInColumns(self) -> dict:
		""":return: every table laid out in columns, by the key of the segment showing it.

		The band and the pins together, and answering the same two questions — `columnPlan`
		and `turnColumnPage` — so that a command which found one does not have to know which
		it found.
		"""
		showing = {}
		band = self.flowBand
		if band is not None and band.columnPlan() is not None:
			segment = band.segment()
			if segment is not None:
				showing[segment.key] = band
		for key, monitor in self._monitors.items():
			if monitor.columnPlan() is not None:
				showing[key] = monitor
		return showing

	def segmentKeysOn(self, driverName: str | None) -> list[str]:
		""":return: the keys of the segments lying on one physical display, in display order.

		Empty for an unknown display. An ordinary display, without the combined display driver,
		has every segment.
		"""
		container = self.container
		if driverName is None or container is None:
			return []
		devices = deviceMap()
		if not devices:
			if driverName == _ordinaryDisplayName():
				return [segment.key for segment in container.segments]
			return []
		for device in devices:
			if device.driverName != driverName:
				continue
			return [container.segments[index].key for index in segmentsForDevice(container.rects, device)]
		return []

	# Quick navigation in a pinned document. See `pinQuickNav`.

	def _noteSegmentUsed(self, key: str, rect) -> None:
		"""Remember a segment a routing key or a pan reached, as the last used on its display."""
		devices = deviceMap()
		if not devices:
			display = _ordinaryDisplayName()
			if display is not None:
				self._lastSegmentOn[display] = key
			return
		for device in devices:
			if device.rowStart <= rect.row < device.rowEnd:
				self._lastSegmentOn[device.driverName] = key
				return

	def _pinnedDocuments(self, gesture) -> tuple[str | None, list[str]]:
		""":return: the pressed display, and the pinned documents a key there could be for.

		Those on the pressed display, in display order. Failing that, as with paging a table, every
		pinned document in display order: a key with no display behind it, an ordinary display, or a
		display holding none.
		"""
		monitors = self.everyMonitor()
		documents = {key for key, monitor in monitors.items() if pinQuickNav.documentOf(monitor) is not None}
		display = keyLayerDispatch.displayFor(gesture)
		onDisplay = [key for key in self.segmentKeysOn(display) if key in documents]
		if onDisplay:
			return display, onDisplay
		container = self.container
		order = [segment.key for segment in container.segments] if container is not None else []
		return display, [key for key in order if key in documents]

	def pinForQuickNav(self, gesture) -> tuple[str, ObjectMonitor] | None:
		"""Which pinned document a quick navigation key is for.

		The one the reader last panned or routed in on the pressed display, or chose with the next
		pinned document command, and otherwise the first. See `_pinnedDocuments`.

		:return: its segment key and monitor, or None if no pinned document is showing.
		"""
		display, keys = self._pinnedDocuments(gesture)
		if not keys:
			return None
		last = self._lastSegmentOn.get(display) if display is not None else None
		key = last if last in keys else keys[0]
		return key, self.everyMonitor()[key]

	def quickNavInPin(self, gesture, quickNavType, forward: bool) -> None:
		"""Move a pinned document to its next or previous item of a type, or say why not."""
		target = self.pinForQuickNav(gesture)
		if target is None:
			# Translators: said when a quick navigation key for a pinned document is pressed and no
			# pinned document is on the display.
			ui.message(_("No pinned document"))
			return
		key, monitor = target
		problem = pinQuickNav.move(monitor, quickNavType, forward)
		if problem is not None:
			ui.message(problem)

	@script(
		# Translators: input help message for a command.
		description=_("Pinned document: Moves quick navigation to the next pinned document on this display"),
		category=SCRIPT_CATEGORY,
	)
	def script_pinNextDocument(self, gesture):
		display, keys = self._pinnedDocuments(gesture)
		if not keys:
			# Translators: said when a quick navigation key for a pinned document is pressed and no
			# pinned document is on the display.
			ui.message(_("No pinned document"))
			return
		current = self.pinForQuickNav(gesture)[0]
		key = keys[(keys.index(current) + 1) % len(keys)]
		if display is not None:
			self._lastSegmentOn[display] = key
		ui.message(self.everyMonitor()[key].name)

	@script(
		# Translators: input help message for a command.
		description=_("Debug: Reports what the flow on the display has cost, and copies the detail"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowCost(self, gesture):
		"""Say what the flow has cost, and write the detail to the log.

		The measurement to check the fetch budget against on a real page.
		"""
		band = self.flowBand
		control = band.controller if band is not None else None
		if control is None:
			# Translators: reported when a command needs a flow on the display and there is none.
			ui.message(_("No flow is showing"))
			return
		from .flowDryRun import describeCost

		try:
			lines = describeCost(control)
		except Exception:
			log.error("Could not report what the flow has cost", exc_info=True)
			# Translators: reported when a diagnostic command fails.
			ui.message(_("Could not report the flow cost, see the log"))
			return
		detail = "\n".join(f"  {line}" for line in lines)
		log.info(f"BrlMultiline flow cost:\n{detail}")
		# The headline is spoken and the detail is copied, for the reason the dry run copies
		# its report: the numbers that settle a budget question are the ones nobody wants to
		# transcribe off a display by hand.
		from .flowDryRun import toClipboard

		toClipboard("BrlMultiline flow cost", lines)
		budget = control.source.budget
		ui.message(
			# Translators: reported by a diagnostic command. Placeholders are, in order, the
			# time in milliseconds the slowest single block took, the number of reading
			# operations counted, and how many of those ran out of their allowance.
			_("Slowest block {slowest} milliseconds, {operations} operations, {stops} ran out").format(
				slowest=f"{budget.slowest * 1000:.1f}",
				operations=budget.operations,
				stops=budget.stops,
			),
		)

	@script(
		# Translators: input help message for a command.
		description=_("Debug: Copies what a flowed reading of this object would show to the clipboard"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowDryRun(self, gesture):
		"""Read the object under the navigator as a flow, onto the clipboard.

		A diagnostic. It claims and moves nothing: the flow is a viewer. Copied to the
		clipboard so the report is easy to find, and also logged, in case NVDA crashes.
		"""
		from .flowDryRun import dryRun, toClipboard

		try:
			# The band is passed so the report is laid out at the geometry the reader is
			# actually feeling. Measured at the whole display, which on a composite is a
			# rectangle no band ever gets, every width-dependent answer in the report is
			# about a display nobody has — indent most of all.
			lines = dryRun(handler=braille.handler, band=self.flowBand)
		except Exception:
			log.error("The flow dry run failed", exc_info=True)
			# Translators: reported when a diagnostic command fails.
			ui.message(_("Flow dry run failed, see the log"))
			return
		verdict = next((line for line in lines if line.startswith("Reversibility")), "")
		log.info("BrlMultiline flow dry run:\n" + "\n".join(f"  {line}" for line in lines))
		if toClipboard("BrlMultiline flow dry run", lines):
			# Translators: reported after a diagnostic has been copied to the clipboard.
			# The placeholder is the number of lines copied.
			ui.message(_("Flow dry run copied, {count} lines").format(count=len(lines)))
		else:
			# Translators: reported when a diagnostic could not reach the clipboard and is
			# in the log instead. The placeholder is the number of lines written.
			ui.message(
				_("Clipboard refused it; flow dry run in the log, {count} lines").format(count=len(lines))
			)
		log.debug(verdict)

	@script(
		# Translators: input help message for a command. An app layout is an application
		# arranging the display around where the focus is.
		description=_("Debug: Copies what the focus's application would arrange the display as to the clipboard"),
		category=SCRIPT_CATEGORY,
	)
	def script_appLayoutReport(self, gesture):
		"""Ask the focus's app module for its layout here, and report what would be shown.

		Shows and claims nothing. Asks even where the setting does not allow app layouts, and
		says so, for app module authors.
		"""
		from .flowDryRun import toClipboard

		try:
			lines = self.appLayoutReport(api.getFocusObject())
		except Exception:
			log.error("The app layout report failed", exc_info=True)
			# Translators: reported when a diagnostic command fails.
			ui.message(_("App layout report failed, see the log"))
			return
		log.info("BrlMultiline app layout:\n" + "\n".join(f"  {line}" for line in lines))
		if toClipboard("BrlMultiline app layout", lines):
			# Translators: reported after a diagnostic has been copied to the clipboard.
			# The placeholder is the number of lines copied.
			ui.message(_("App layout report copied, {count} lines").format(count=len(lines)))
		else:
			# Translators: reported when a diagnostic could not reach the clipboard and is
			# in the log instead. The placeholder is the number of lines written.
			ui.message(
				_("Clipboard refused it; app layout report in the log, {count} lines").format(count=len(lines))
			)

	def appLayoutReport(self, focus) -> list[str]:
		"""What the focus's app module would arrange the display as, line by line.

		In English and untranslated, like the other debug reports: it is for an app module's
		author and for a bug report, and both are read against the code.

		:param focus: the object to ask about.
		:return: the report.
		"""
		appModule = getattr(focus, "appModule", None)
		appName = getattr(appModule, "appName", None) or "none"
		lines = [f"App module: {appName}"]
		lines.append(f"Shown now: {self.appLayouts.name!r}" if self.appLayouts.isUp else "Shown now: nothing")
		if not bmConfig.shouldAllowAppLayouts():
			lines.append("App layouts: not allowed on this display")
		elif self._appLayoutsSuspended:
			lines.append("App layouts: allowed, and set aside for now")
		else:
			lines.append("App layouts: allowed")
		area, bands, within, spread = self.appLayoutRoom()
		if area is None:
			lines.append("Free area: none, every row holds a pin")
			return lines
		lines.append(
			f"Free area: rows {area.row} to {area.endRow - 1}, cells {area.col} to {area.endCol - 1}",
		)
		if bands:
			lines.append(
				"Displays: "
				+ ", ".join(f"rows {band.rowStart} to {band.rowEnd - 1} of {band.numCols} cells" for band in bands),
			)
			lines.append(f"Spread across displays: {'yes' if spread else 'no'}")
			plan = appLayout.planFor(area, bands, within, spread)
			if plan.isSplit:
				lines.append(
					f"Focus display: rows {plan.focusBand.rowStart} to {plan.focusBand.rowEnd - 1}, alone; the rest on rows "
					f"{plan.otherBand.rowStart} to {plan.otherBand.rowEnd - 1}",
				)
			elif plan.area != area:
				lines.append(f"Focus display: rows {plan.area.row} to {plan.area.endRow - 1}, the whole layout")
		room = appLayout.displayArea(area, bands, within, spread)
		lines.append(f"Offered: {room.rows} rows, {room.columns} columns")
		if getattr(appModule, appLayout.LAYOUT_METHOD, None) is None:
			lines.append(f"Layout: the app module has no {appLayout.LAYOUT_METHOD} method")
			return lines
		layout, problem = appLayout.askAppModule(appModule, focus, room)
		if problem is not None:
			lines.append(f"Layout refused: {problem}")
			return lines
		if layout is None:
			lines.append("Layout: none here")
			return lines
		try:
			rects = appLayout.placeSegments(layout, area, bands, within, spread)
		except appLayout.LayoutError as error:
			lines.append(f"Layout refused: {error}")
			return lines
		lines.extend(appLayout.describeLayout(layout, rects))
		return lines

	# --- Graphics ------------------------------------------------------------------------
	#
	# Only the commands that put a drawing up are bound by default, since the reader's hand is
	# on the display. Everything used while a drawing is up is in a key layer, in
	# `keyLayers.defaultLayers`, which comes and goes with the drawing. See
	# docs/design/layered-keys-plan.md.
	#
	# The bound chords contain dot 7 or dot 8, which `hidBrailleStandard`'s gesture map never
	# uses. They are bound for the Monarch driver, not `hidBrailleStandard`, so displays that
	# cannot draw are not affected. All are rebindable in Input Gestures under BrlMultiline.

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Show or hide a drawing on the display"),
		category=SCRIPT_CATEGORY,
		gestures=["br(brlMultilineMonarch):space+dot7+dot8"],
	)
	def script_toggleGraphics(self, gesture):
		"""Put a figure on the part of the display that can draw, or take it off again.

		A display that cannot raise individual pins says so rather than doing nothing, because
		"nothing happened" is the one answer a reader cannot act on.
		"""
		mode = self.graphicsMode
		self.cancelPendingDrawings()
		# Collected rather than spoken, so the layers the drawing brought or took with it are said in
		# the same message as the drawing, once.
		self._layerChangesToSay = []
		try:
			if mode.active:
				mode.leave()
				entered = None
			else:
				entered = mode.enter()
		finally:
			changes, self._layerChangesToSay = self._layerChangesToSay, None
		if entered is None:
			# Translators: reported when a drawing is taken off the display.
			ui.message(", ".join([_("Drawing off"), *changes]))
			return
		if entered:
			ui.message(", ".join([mode.describe(), *changes]))
			return
		# Translators: reported when a drawing could not be shown. The placeholder is why.
		ui.message(mode.lastError or _("The drawing could not be shown"))

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Chart the selected cells"),
		category=SCRIPT_CATEGORY,
		gestures=["br(brlMultilineMonarch):space+dot5+dot7"],
	)
	def script_chartSelection(self, gesture):
		self.pictures.chartSelection()

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Draw the picture here"),
		category=SCRIPT_CATEGORY,
		gestures=["br(brlMultilineMonarch):space+dot1+dot4+dot7"],
	)
	def script_drawPicture(self, gesture):
		self.pictures.drawPicture()

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Draw the picture on the clipboard"),
		category=SCRIPT_CATEGORY,
	)
	def script_drawClipboardPicture(self, gesture):
		self.pictures.drawClipboardPicture()

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Draw only the colour last touched on a picture, or all colours again"),
		category=SCRIPT_CATEGORY,
	)
	def script_pictureOnlyColour(self, gesture):
		self.pictures.drawOnlyColour()

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Change how a picture is drawn"),
		category=SCRIPT_CATEGORY,
	)
	def script_pictureStyle(self, gesture):
		self.pictures.cycleStyle()

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Draw the picture as outlines"),
		category=SCRIPT_CATEGORY,
	)
	def script_pictureOutlines(self, gesture):
		self.pictures.chooseStyle(imageFigure.OUTLINES)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Draw the picture as single lines"),
		category=SCRIPT_CATEGORY,
	)
	def script_pictureLines(self, gesture):
		self.pictures.chooseStyle(imageFigure.LINES)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Draw the picture by brightness"),
		category=SCRIPT_CATEGORY,
	)
	def script_pictureBrightness(self, gesture):
		self.pictures.chooseStyle(imageFigure.SILHOUETTE)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Draw the picture by brightness, reversed"),
		category=SCRIPT_CATEGORY,
	)
	def script_pictureReversed(self, gesture):
		self.pictures.chooseStyle(imageFigure.REVERSED)

	def cancelPendingDrawings(self) -> None:
		"""Forget a picture file still loading and a page layout still being gathered.

		Only the last request counts, so every drawing command and `terminate` call this.
		"""
		self.pictures.cancelRequest()
		self.layouts.cancel()

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Show the glyph catalogue"),
		category=SCRIPT_CATEGORY,
	)
	def script_glyphCatalogue(self, gesture):
		"""Put every symbol the add-on knows on the panel, so a hand can compare them.

		Shapes can only be judged by touch, side by side. A routing press on one names it.
		Unbound by default.
		"""
		self.cancelPendingDrawings()
		mode = self.graphicsMode
		size = mode.drawingSize()
		if size is None:
			# Translators: reported when a drawing was asked for on a display that cannot draw.
			ui.message(_("This display cannot show graphics"))
			return
		buffer, describeAt = glyphs.catalogue(mode.newBuffer, size[0], size[1])
		if buffer is None:
			ui.message(mode.lastError or _("The drawing could not be shown"))
			return
		drawing = graphicsMode.Drawing(
			buffer,
			# Translators: the name of the glyph catalogue on the display. The placeholder is
			# how many symbols it holds.
			name=_("glyph catalogue, {count} symbols").format(count=len(glyphs.VOCABULARY)),
			describeAt=describeAt,
		)
		if not mode.enter(drawing):
			ui.message(mode.lastError or _("The drawing could not be shown"))
			return
		ui.message(mode.describe())

	@script(
		# Translators: input help message for a command.
		description=_("Overview: Draw the layout of the part of the page on screen, or of the window"),
		category=SCRIPT_CATEGORY,
	)
	def script_layoutOverview(self, gesture):
		self.layouts.draw("screen")

	@script(
		# Translators: input help message for a command.
		description=_("Overview: Draw the layout of the whole page"),
		category=SCRIPT_CATEGORY,
	)
	def script_layoutOverviewPage(self, gesture):
		self.layouts.draw("page")

	@script(
		# Translators: input help message for a command.
		description=_("Overview: Show the layout of the page, or of the window, as text"),
		category=SCRIPT_CATEGORY,
	)
	def script_layoutOverviewText(self, gesture):
		self.layouts.draw("text")

	@script(
		# Translators: input help message for a command.
		description=_("Overview: Show what is inside the area last pressed in a page's layout"),
		category=SCRIPT_CATEGORY,
	)
	def script_layoutDrillIn(self, gesture):
		self.layouts.drillIn()

	@script(
		# Translators: input help message for a command.
		description=_("Overview: Go back out to the area around, in a page's layout"),
		category=SCRIPT_CATEGORY,
	)
	def script_layoutBackOut(self, gesture):
		self.layouts.backOut()

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Magnify the drawing"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsZoomIn(self, gesture):
		self._zoomGraphics(1)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Shrink the drawing"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsZoomOut(self, gesture):
		self._zoomGraphics(-1)

	def _zoomGraphics(self, step: int) -> None:
		"""Change the magnification and say what it became.

		:param step: levels in, negative for out.
		"""
		mode = self.graphicsMode
		if not mode.active:
			# Translators: reported when a drawing command is used with no drawing on the display.
			ui.message(_("No drawing"))
			return
		marked = mode.markedPoint
		moved = mode.zoomBy(step)
		# Reported whether or not it moved, so the command never seems to have missed.
		said = mode.describe()
		movedMark = self._markMovedWords(mode, marked)
		if movedMark:
			said += ", " + movedMark
		if not moved:
			# And when it did not move, why.
			why = mode.zoomRefusal(step)
			if why:
				said += ", " + why
				said += self.pictures.captureSizeWords()
		ui.message(said)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Zoom a chart to one pin per point"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsZoomToPoints(self, gesture):
		"""Zoom a line chart to exactly where every point has a pin column of its own.

		Its own key, since it is rarely a doubling. O in the Monarch's chart layer.
		"""
		mode = self.graphicsMode
		if not mode.active:
			ui.message(_("No drawing"))
			return
		why = mode.pointZoomRefusal()
		if why:
			ui.message(why)
			return
		marked = mode.markedPoint
		if not mode.zoomToPoints():
			ui.message(", ".join((mode.describe(), _("this part will not draw"))))
			return
		ui.message(", ".join(part for part in (mode.describe(), self._markMovedWords(mode, marked)) if part))

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Show the next set of lines on a chart"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsNextView(self, gesture):
		self._changeGraphicsView(1)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Show the previous set of lines on a chart"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsPreviousView(self, gesture):
		self._changeGraphicsView(-1)

	def _changeGraphicsView(self, direction: int) -> None:
		"""Step through a chart's views, and say which lines are now showing.

		V in the Monarch's chart layer, with dot 7 to go back. See `chartLine.viewsFor`. The zoom
		and place are kept.

		:param direction: 1 for the next view, -1 for the previous.
		"""
		mode = self.graphicsMode
		if not mode.active:
			ui.message(_("No drawing"))
			return
		if not mode.hasViews:
			# Translators: reported when the chart view keys are used on a drawing that can
			# only be shown one way, such as a picture or a chart of a single line.
			ui.message(_("This drawing has only one view"))
			return
		if not mode.changeView(direction):
			# Translators: reported when a chart will not draw the lines just asked for at the
			# part of it the reader has zoomed into, so the lines they had are kept.
			ui.message(_("Those lines will not draw here"))
			return
		ui.message(mode.describe())

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Next point on a chart"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsNextPoint(self, gesture):
		"""Mark the next point of a chart and say it, turning a page if it is off the panel.

		Space with dot 4 in the Monarch's chart layer. See `GraphicsMode.stepPoint`, and
		`docs/design/chart-point-plan.md` for why a page and not a point.
		"""
		ui.message(self.graphicsMode.stepPoint(1))

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Previous point on a chart"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsPreviousPoint(self, gesture):
		ui.message(self.graphicsMode.stepPoint(-1))

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: First point on a chart"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsFirstPoint(self, gesture):
		ui.message(self.graphicsMode.pointToEnd(last=False))

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Last point on a chart"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsLastPoint(self, gesture):
		ui.message(self.graphicsMode.pointToEnd(last=True))

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Unmark the point on a chart"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsUnmarkPoint(self, gesture):
		ui.message(self.graphicsMode.unmarkPoint())

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Move the level line to the next line of a chart"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsNextLevel(self, gesture):
		"""On a line chart with several lines, the level line at the marked point follows one of
		them; this moves it to the next, and says that line's value at the point."""
		ui.message(self.graphicsMode.nextLevel())

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Move the drawing view up"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsPanUp(self, gesture):
		self._panGraphics(0, -1)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Move the drawing view down"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsPanDown(self, gesture):
		self._panGraphics(0, 1)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Move the drawing view left"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsPanLeft(self, gesture):
		self._panGraphics(-1, 0)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Move the drawing view right"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsPanRight(self, gesture):
		self._panGraphics(1, 0)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Move the drawing view down a whole panel"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsPanelDown(self, gesture):
		"""Move down by the whole height of the panel, for reading a tall drawing a panel at a
		time the way a page is read a screen at a time. Unbound by default."""
		self._panGraphics(0, 1, whole=True)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Move the drawing view up a whole panel"),
		category=SCRIPT_CATEGORY,
	)
	def script_graphicsPanelUp(self, gesture):
		"""Move up by the whole height of the panel. Unbound by default."""
		self._panGraphics(0, -1, whole=True)

	def _panGraphics(self, across: int, down: int, whole: bool = False) -> None:
		"""Move the visible part of the drawing, and say where it now starts.

		The pan scripts are written out rather than generated, because NVDA collects default
		gestures from the class body only.

		:param across: steps right, negative for left.
		:param down: steps down, negative for up.
		:param whole: whether a step is the whole panel rather than a quarter of it.
		"""
		mode = self.graphicsMode
		if not mode.active:
			ui.message(_("No drawing"))
			return
		if mode.zoom == GRAPHICS_FIT:
			# The whole drawing is on the panel, so there is nowhere to move to.
			# Translators: reported when panning is asked for but the whole drawing is already
			# on the display.
			ui.message(_("Whole drawing is shown; magnify to move around it"))
			return
		stepAcross, stepDown = mode.panelStep() if whole else mode.panStep()
		if not mode.panBy(across * stepAcross, down * stepDown):
			# Translators: reported when the drawing cannot be moved any further that way.
			ui.message(_("Edge of the drawing"))
			return
		# Said as a fraction of how far the window can move rather than as a source dot: the
		# dot number depends on how large the drawing happens to be, which is a fact about the
		# file and not about what the reader is feeling. See `GraphicsMode.positionWords`.
		# The note goes with it because a blank panel has to be accounted for every time it is
		# under a hand, not only on the zoom that first produced it.
		said = mode.positionWords()
		note = mode.note
		ui.message(", ".join(part for part in (said, note) if part))

	@staticmethod
	def _markMovedWords(mode, before) -> str:
		""":return: the chart point now marked, if moving the window moved the mark onto it.

		A zoom that leaves the marked point off the panel brings the mark to the edge it went off
		by (see `GraphicsMode._keepPointsOnPanel`), and the reader is told where it went, since
		that is where the next step starts.

		:param mode: the graphics mode.
		:param before: the marked point before the window moved.
		"""
		if before is None or mode.markedPoint == before:
			return ""
		return mode.markedWords()

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Show or hide the braille line beside the drawing"),
		category=SCRIPT_CATEGORY,
	)
	def script_toggleGraphicsTextLine(self, gesture):
		"""Give the drawing the whole display, or give the braille line back.

		On the Monarch, 96 by 40 pins instead of 96 by 35. Without the line, focus braille is
		dropped while the figure is up, unless the focus is on another display.
		"""
		mode = self.graphicsMode
		if not mode.active:
			ui.message(_("No drawing"))
			return
		hiding = bool(mode.textLines)
		if not mode.setTextLines(0 if hiding else 1):
			# Translators: reported when the drawing could not be resized.
			ui.message(_("The display would not give up those rows"))
			return
		if hiding:
			# Translators: reported when a drawing takes the whole display and the braille
			# line beside it goes away.
			ui.message(_("Full panel, no braille line"))
			return
		# Translators: reported when the braille line beside a drawing comes back.
		ui.message(_("Braille line back"))

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Reports the drawing on the display"),
		category=SCRIPT_CATEGORY,
	)
	def script_reportGraphics(self, gesture):
		ui.message(self.graphicsMode.describe())

	# Layered keys. See `keyLayerDispatch` and docs/design/layered-keys-plan.md. Every command here
	# is named script_keyLayer..., which is how a layer knows to let its keys through. The Monarch
	# chord for the layer key is l with dot 7, in the space the HID standard map leaves empty; the
	# keyboard one is not bound by NVDA.

	@script(
		# Translators: input help message for a command.
		description=_("Layered keys: Turns the layer on or off for the device it is pressed on"),
		category=SCRIPT_CATEGORY,
		gestures=["br(brlMultilineMonarch):space+dot1+dot2+dot3+dot7", "kb:NVDA+control+shift+l"],
	)
	def script_keyLayerToggle(self, gesture):
		self.toggleKeyLayer(keyLayerDispatch.deviceFor(gesture))

	_layerChangesToSay: list | None = None
	"""While a command is putting a drawing up or taking it off, the layers that came or went with it,
	to say in its message."""

	def onGraphicsChanged(self) -> None:
		"""What is drawn changed. Called by `GraphicsMode` whenever a figure goes up, comes down, is
		replaced or is evicted."""
		self.reconcileKeyLayers()

	def reconcileKeyLayers(self) -> None:
		"""Turn off the layers whose drawing has gone, turn on the ones that come on by themselves for
		what is drawn now, and say so. See `keyLayerDispatch.reconcile`.

		Also after a profile switch and a display reconnecting, which is why it is not only for graphics
		changes. Tables are not looked for here; see `keyLayerContexts`.
		"""
		try:
			present = keyLayerContexts.presentContexts(self, tables=False)
			ended, started = keyLayerDispatch.reconcile(
				present, self.keyLayerDevices(), keyLayerContexts.DETECTED
			)
		except Exception:
			log.error("BrlMultiline: could not change the layers for a drawing", exc_info=True)
			return
		words = []
		for _device, layer in ended:
			# Translators: reported when a layer of keys is turned off. The placeholder is its name.
			said = _("{name} layer off").format(name=keyLayerDispatch.layerName(layer))
			if said not in words:
				words.append(said)
		for _device, layer in started:
			# Translators: reported when a layer of keys is turned on. The placeholder is its name.
			said = _("{name} layer on").format(name=keyLayerDispatch.layerName(layer))
			if said not in words:
				words.append(said)
		if not words:
			return
		if self._layerChangesToSay is not None:
			self._layerChangesToSay.extend(words)
		else:
			# After whatever the command that changed the drawing says, rather than before it: a chart
			# is described and then its layer is named, not the other way round.
			wx.CallAfter(ui.message, ", ".join(words))

	def keyLayerDevices(self) -> list[str]:
		""":return: the devices a layer can come on for: every connected braille display, and the keyboard."""
		names = [info.driverName for info in deviceMap()]
		if not names:
			display = braille.handler.display if braille.handler else None
			if display is not None and display.name != "noBraille":
				names = [display.name]
		return [*names, keyLayers.KEYBOARD]

	def _addKeyLayersMenuItem(self):
		"""Put the layered keys dialog in NVDA's Preferences menu, after Input Gestures.

		Appended, and Input Gestures is the last item NVDA puts there, so it lands beside it. No menu
		at all in a secure session, where NVDA leaves Input Gestures out too.

		:return: the menu item, or None if there is no menu to add it to.
		"""
		try:
			import globalVars

			if globalVars.appArgs.secure:
				return None
			tray = gui.mainFrame.sysTrayIcon
			item = tray.preferencesMenu.Append(
				wx.ID_ANY,
				# Translators: the item in NVDA's Preferences menu that opens the layered keys dialog.
				_("BrlMultiline &layered keys..."),
			)
			tray.Bind(wx.EVT_MENU, self._onKeyLayersMenu, item)
			return item
		except Exception:
			log.debugWarning("BrlMultiline: could not add the layered keys menu item", exc_info=True)
			return None

	def _removeKeyLayersMenuItem(self) -> None:
		item = getattr(self, "_keyLayersMenuItem", None)
		if item is None:
			return
		self._keyLayersMenuItem = None
		try:
			tray = gui.mainFrame.sysTrayIcon
			# Unbound as well as removed: a binding left on the tray icon keeps this plugin alive after
			# the add-on reloads, and would call it if wx gave the id to another item.
			tray.Unbind(wx.EVT_MENU, source=item, handler=self._onKeyLayersMenu)
			tray.preferencesMenu.DestroyItem(item)
		except Exception:
			log.debugWarning("BrlMultiline: could not remove the layered keys menu item", exc_info=True)

	def _onKeyLayersMenu(self, event) -> None:
		from . import keyLayerDialog

		keyLayerDialog.openDialog()

	# Braille substitutions

	def _addSubstitutionsMenuItem(self):
		"""Put the braille substitutions dialog in NVDA's Preferences menu, after the layered keys.

		Not in a secure session, where NVDA leaves its own dictionaries out of the menu too and the list
		could not be saved.

		:return: the menu item, or None if there is no menu to add it to.
		"""
		try:
			import globalVars

			if globalVars.appArgs.secure:
				return None
			tray = gui.mainFrame.sysTrayIcon
			item = tray.preferencesMenu.Append(
				wx.ID_ANY,
				# Translators: the item in NVDA's Preferences menu that opens the braille substitutions dialog.
				_("BrlMultiline braille &substitutions..."),
			)
			tray.Bind(wx.EVT_MENU, self._onSubstitutionsMenu, item)
			return item
		except Exception:
			log.debugWarning("BrlMultiline: could not add the braille substitutions menu item", exc_info=True)
			return None

	def _removeSubstitutionsMenuItem(self) -> None:
		item = getattr(self, "_substitutionsMenuItem", None)
		if item is None:
			return
		self._substitutionsMenuItem = None
		try:
			tray = gui.mainFrame.sysTrayIcon
			# Unbound as well as removed, for the reason the layered keys item is.
			tray.Unbind(wx.EVT_MENU, source=item, handler=self._onSubstitutionsMenu)
			tray.preferencesMenu.DestroyItem(item)
		except Exception:
			log.debugWarning("BrlMultiline: could not remove the braille substitutions menu item", exc_info=True)

	def _onSubstitutionsMenu(self, event) -> None:
		from . import substitutionsDialog

		substitutionsDialog.openDialog()

	def _handleBrailleRulesChanged(self) -> None:
		"""Draw everything again after the reader's braille rules changed.

		What is on the display was translated with the old rules, and nothing about the change makes
		a braille event. NVDA's own regions are made again from the focus, as NVDA does when braille is
		first shown, and the flow band, whose renderings carry the rules' generation in their key, is
		rebuilt. Deferred, as a profile switch's rebuild is: this can run while the switch is applied.
		"""

		def redraw() -> None:
			if self._terminated or not braille.handler:
				return
			try:
				braille.handler.initialDisplay()
			except Exception:
				log.debugWarning("BrlMultiline: could not redraw after the braille rules changed", exc_info=True)

		wx.CallAfter(redraw)
		self._scheduleRebuild()

	@script(
		# Translators: input help message for a command.
		description=_("Opens the BrlMultiline braille substitutions"),
		category=SCRIPT_CATEGORY,
	)
	@gui.blockAction.when(gui.blockAction.Context.MODAL_DIALOG_OPEN)
	def script_showBrailleSubstitutions(self, gesture):
		from . import substitutionsDialog

		wx.CallAfter(substitutionsDialog.openDialog)

	@script(
		# Translators: input help message for a command.
		description=_("Layered keys: Opens the layered keys dialog"),
		category=SCRIPT_CATEGORY,
	)
	@gui.blockAction.when(gui.blockAction.Context.MODAL_DIALOG_OPEN)
	def script_keyLayerDialog(self, gesture):
		from . import keyLayerDialog

		wx.CallAfter(keyLayerDialog.openDialog)

	def toggleKeyLayer(self, device: str | None) -> None:
		"""Turn a device's layer off, or turn on the one for what is on the display, and say which.

		:param device: a braille display's driver name, or `keyboard`.
		"""
		if device is None:
			# Translators: reported when the layer key is pressed on something that cannot have layers.
			ui.message(_("No layers for this key"))
			return
		isOn, layer = keyLayerDispatch.toggle(device, keyLayerContexts.presentContexts(self, device=device))
		name = keyLayerDispatch.layerName(layer)
		if isOn:
			# Translators: reported when a layer of keys is turned on. The placeholder is its name.
			ui.message(_("{name} layer on").format(name=name))
		else:
			# Translators: reported when a layer of keys is turned off. The placeholder is its name.
			ui.message(_("{name} layer off").format(name=name))

	@script(
		# Translators: input help message for a command.
		description=_("Layered keys: Chooses which layer is on for the device it is pressed on"),
		category=SCRIPT_CATEGORY,
	)
	@gui.blockAction.when(gui.blockAction.Context.MODAL_DIALOG_OPEN)
	def script_keyLayerChoose(self, gesture):
		device = keyLayerDispatch.deviceFor(gesture)
		if device is None:
			ui.message(_("No layers for this key"))
			return
		layers = list(keyLayerDispatch.layerSet(device).layers)
		active = keyLayerDispatch.activeLayer(device)
		current = next(
			(index for index, layer in enumerate(layers) if active is not None and layer.id == active.id),
			0,
		)
		deviceName = self._keyLayerDeviceName(device)

		def ask():
			gui.mainFrame.prePopup()
			try:
				dialog = wx.SingleChoiceDialog(
					gui.mainFrame,
					# Translators: the message of a dialog asking which layer of keys to turn on. The
					# placeholder is the display, or keyboard.
					_("Which layer should be on for {device}?").format(device=deviceName),
					# Translators: the title of a dialog asking which layer of keys to turn on.
					_("Choose a layer"),
					[keyLayerDispatch.layerName(layer) for layer in layers],
				)
				try:
					dialog.SetSelection(current)
					if dialog.ShowModal() != wx.ID_OK:
						return
					chosen = dialog.GetSelection()
				finally:
					dialog.Destroy()
			finally:
				gui.mainFrame.postPopup()
			if 0 <= chosen < len(layers):
				layer = keyLayerDispatch.activate(device, layers[chosen].id)
				ui.message(_("{name} layer on").format(name=keyLayerDispatch.layerName(layer)))

		wx.CallAfter(ask)

	@script(
		# Translators: input help message for a command.
		description=_("Layered keys: Reports which layers are on"),
		category=SCRIPT_CATEGORY,
	)
	def script_keyLayerReport(self, gesture):
		on = keyLayerDispatch.activeLayers()
		if not on:
			# Translators: reported when no layer of keys is on.
			ui.message(_("No layers on"))
			return
		ui.message(
			", ".join(
				# Translators: one layer of keys that is on, in a report of them. Placeholders are
				# the layer's name and the display it is on, or keyboard.
				_("{name} layer on {device}").format(
					name=keyLayerDispatch.layerName(layer),
					device=self._keyLayerDeviceName(device),
				)
				for device, layer in on
			),
		)

	@script(
		# Translators: input help message for a command.
		description=_("Layered keys: Turns every layer off"),
		category=SCRIPT_CATEGORY,
	)
	def script_keyLayersOff(self, gesture):
		if keyLayerDispatch.allOff():
			# Translators: reported when every layer of keys is turned off.
			ui.message(_("All layers off"))
		else:
			ui.message(_("No layers on"))

	def _keyLayerDeviceName(self, device: str) -> str:
		""":return: what to call a device with layers when speaking of it."""
		if device == keyLayers.KEYBOARD:
			# Translators: the keyboard, as a device that has layers of keys.
			return _("keyboard")
		return displayDescriptions().get(device, device)

	@script(
		# Translators: input help message for a command.
		description=_("Misc: Reports the BrlMultiline segment layout"),
		category=SCRIPT_CATEGORY,
	)
	def script_reportLayout(self, gesture):
		"""Say what is on the display, and who claimed it.

		The summary names every claim and the rows it holds. The full breakdown of panels and
		segments, with rectangles, owners and which follows the focus, goes to the log at INFO.
		"""
		container = self.container
		if container is None:
			# Translators: reported when a command needs segments but none are configured.
			ui.message(_("BrlMultiline is not active"))
			return
		claims = list(self._activePanels)
		try:
			log.info("BrlMultiline layout:\n" + "\n".join(_layoutLines(container, claims)))
		except Exception:
			log.error("BrlMultiline: could not write the layout breakdown", exc_info=True)
		summary = _(
			# Translators: reports the current layout. Placeholders are the name of the view,
			# the number of panels, the number of segments, and the segment following the
			# focus.
			"{view} view, {panels} panels, {count} segments, focus in segment {focus}",
		).format(
			view=container.view.name,
			panels=len(container.panels),
			count=container.numSegments,
			focus=container.focusSegmentNumber,
		)
		if claims:
			summary += ". " + _(
				# Translators: follows the layout report, naming what has laid a claim over the
				# configured display and which rows it holds. The placeholder is that list.
				"Claimed: {claims}",
			).format(
				claims="; ".join(
					_(
						# Translators: one claim in the layout report. Placeholders are its name
						# and the first and last row it holds, counted from 1.
						"{name} rows {first} to {last}",
					).format(
						name=panel.name,
						first=panel.rect.row + 1,
						last=panel.rect.row + panel.rect.numRows,
					)
					for panel in claims
				),
			)
		ui.message(summary)


def _rectWords(rect) -> str:
	":return: a rectangle as rows and columns, counted from 1 as a reader counts them."
	return (
		f"rows {rect.row + 1} to {rect.row + rect.numRows}, cols {rect.col + 1} to {rect.col + rect.numCols}"
	)


def _layoutLines(container, claims: list) -> list[str]:
	"""Describe the whole arrangement, for the log.

	In English, like the other diagnostic reports.

	:param container: the live display.
	:param claims: the panels code has laid over the configured view, which is what
		separates "the reader configured this" from "something took it".
	:return: one line per fact, ready to join.
	"""
	claimed = {panel.name for panel in claims}
	lines = [f"  view: {container.view.name}"]
	lines.append(
		f"  display: {container.numRows} rows of {container.numCols}, "
		f"{container.numSegments} segments in {len(container.panels)} panels"
	)
	lines.append(f"  focus segment: {container.focusSegmentNumber} ({container.focusSegmentKey!r})")
	lines.append(f"  panels ({len(container.panels)}):")
	for panel in container.panels:
		mark = "  <- claimed by code" if panel.name in claimed else ""
		lines.append(f"    {panel.name}: {_rectWords(panel.rect)}{mark}")
	lines.append(f"  segments ({container.numSegments}):")
	for number, spec in enumerate(container.specs):
		notes = []
		if number == container.focusSegmentNumber:
			notes.append("focus")
		if spec.owner:
			notes.append(f"reserved by {spec.owner!r}")
		if spec.hostsSystemFocus:
			notes.append("hosts system focus")
		lines.append(
			f"    {number} {spec.key}: {_rectWords(spec.rect)}" + (f"  [{', '.join(notes)}]" if notes else "")
		)
	if claims:
		lines.append(f"  claims laid over the configured view: {', '.join(sorted(claimed))}")
	else:
		lines.append("  no claims; this is the configured view")
	return lines


def _byPriority(panels) -> list:
	"""Order claims so the highest priority is laid over the rest.

	Stable, so claims of equal rank keep the order they were made in.

	:param panels: the claims, in the order they were made.
	:return: the same claims, lowest priority first.
	"""
	return sorted(panels, key=lambda panel: getattr(panel, "priority", 0))


def _displayName(displayOrdinal: int) -> str:
	""":return: what to call one of the combined displays in a command description.

	Named rather than numbered, so that nothing has to explain why displays count from one
	while segments count from zero. Segments keep the numbering the rest of the add-on uses.
	"""
	names = (
		# Translators: the topmost of several combined braille displays.
		_("the first display"),
		# Translators: the second of several combined braille displays, from the top.
		_("the second display"),
		# Translators: the third of several combined braille displays, from the top.
		_("the third display"),
	)
	if displayOrdinal < len(names):
		return names[displayOrdinal]
	# Translators: one of several combined braille displays, counted from the top.
	return _("display {number}").format(number=displayOrdinal + 1)


def _makeDisplayScrollScript(displayOrdinal: int, segmentOrdinal: int, forward: bool):
	"""Build one scrolling script that names its segment by display.

	The stable way to bind panning. A segment's index counts across the whole display and
	moves whenever the layout changes, so a key bound to "segment 5" addresses something else
	after a rearrangement. "The second display's first segment" does not move.
	"""

	def scrollScript(self, gesture):
		number = self.displaySegmentNumber(displayOrdinal, segmentOrdinal)
		if number is not None:
			self.scrollSegment(number, forward, gesture)

	template = (
		_(
			# Translators: input help message for a command. Placeholders are a segment number,
			# counting from 0 on that display, and the name of one of the combined displays.
			"Navigation: Scrolls segment {segment} of {display} forward",
		)
		if forward
		else _(
			# Translators: input help message for a command. Placeholders are a segment number,
			# counting from 0 on that display, and the name of one of the combined displays.
			"Navigation: Scrolls segment {segment} of {display} back",
		)
	)
	scrollScript.__doc__ = template.format(
		segment=segmentOrdinal,
		display=_displayName(displayOrdinal),
	)
	scrollScript.category = SCRIPT_CATEGORY
	scrollScript.bypassInputHelp = False
	return scrollScript


def _makeDisplayMonitorScript(displayOrdinal: int, segmentOrdinal: int, start: bool):
	"""Build one object monitoring script that names its segment by display.

	Pinning has the same problem panning did: a segment's index moves under a binding made
	against it, and a display kept for monitored objects is exactly where that hurts.
	"""

	def monitorScript(self, gesture):
		number = self.displaySegmentNumber(displayOrdinal, segmentOrdinal)
		if number is None:
			return
		if start:
			self.startMonitoring(number)
		else:
			self.stopMonitoring(number)

	template = (
		_(
			# Translators: input help message for a command. Placeholders are a segment number,
			# counting from 0 on that display, and the name of one of the combined displays.
			"Monitoring: Shows the navigator object in segment {segment} of {display}",
		)
		if start
		else _(
			# Translators: input help message for a command. Placeholders are a segment number,
			# counting from 0 on that display, and the name of one of the combined displays.
			"Monitoring: Stops showing an object in segment {segment} of {display}",
		)
	)
	monitorScript.__doc__ = template.format(
		segment=segmentOrdinal,
		display=_displayName(displayOrdinal),
	)
	monitorScript.category = SCRIPT_CATEGORY
	return monitorScript


def _makeDisplayKeyLayerScript(displayOrdinal: int):
	"""Build one command that toggles the layer on a display named by where it is.

	For the keyboard, which cannot press a display's own layer key. Named by display rather than by
	driver so the same key reaches the same place, as the display relative scrolling commands do.
	"""

	def keyLayerScript(self, gesture):
		members = deviceMap()
		device = None
		if members:
			if displayOrdinal < len(members):
				device = members[displayOrdinal].driverName
		elif displayOrdinal == 0 and braille.handler and braille.handler.display:
			device = braille.handler.display.name
		if device is None or device == "noBraille":
			# Translators: reported when a command names one of the combined displays and there is
			# no such display. The placeholder is its name.
			ui.message(_("There is no {display}").format(display=_displayName(displayOrdinal)))
			return
		self.toggleKeyLayer(device)

	# Translators: input help message for a command. The placeholder is the name of one of the
	# combined displays.
	keyLayerScript.__doc__ = _("Layered keys: Turns the layer on or off for {display}").format(
		display=_displayName(displayOrdinal),
	)
	keyLayerScript.category = SCRIPT_CATEGORY
	return keyLayerScript


def _makeScrollScript(segmentNumber: int, forward: bool):
	"""Build one per segment scrolling script."""

	def scrollScript(self, gesture):
		self.scrollSegment(segmentNumber, forward, gesture)

	if forward:
		# Translators: input help message for a command. The placeholder is a segment number.
		scrollScript.__doc__ = _("Navigation: Scrolls segment {number} forward").format(number=segmentNumber)
	else:
		# Translators: input help message for a command. The placeholder is a segment number.
		scrollScript.__doc__ = _("Navigation: Scrolls segment {number} back").format(number=segmentNumber)
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

	Two families. The display relative ones name a segment by its display and its place on
	it, which survives rearrangements; the others count across the whole display. One set
	for every segment the add-on can offer. None are bound by default.
	"""
	for displayOrdinal in range(devicesModule.MAX_UI_DISPLAYS):
		setattr(
			GlobalPlugin,
			f"script_keyLayerToggleDisplay{displayOrdinal}",
			_makeDisplayKeyLayerScript(displayOrdinal),
		)
		for segmentOrdinal in range(devicesModule.MAX_UI_DISPLAY_SEGMENTS):
			suffix = f"Display{displayOrdinal}Segment{segmentOrdinal}"
			setattr(
				GlobalPlugin,
				f"script_scroll{suffix}Forward",
				_makeDisplayScrollScript(displayOrdinal, segmentOrdinal, True),
			)
			setattr(
				GlobalPlugin,
				f"script_scroll{suffix}Back",
				_makeDisplayScrollScript(displayOrdinal, segmentOrdinal, False),
			)
			setattr(
				GlobalPlugin,
				f"script_monitorObjectIn{suffix}",
				_makeDisplayMonitorScript(displayOrdinal, segmentOrdinal, True),
			)
			setattr(
				GlobalPlugin,
				f"script_stopMonitoring{suffix}",
				_makeDisplayMonitorScript(displayOrdinal, segmentOrdinal, False),
			)
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


def _ordinaryDisplayName() -> str | None:
	""":return: the driver name of a display connected on its own, without the combined display
	driver, or None. Its keys are its gestures' source, as a combined display's members' are."""
	if deviceMap():
		return None
	display = braille.handler.display if braille.handler else None
	name = getattr(display, "name", None)
	return None if name in (None, "noBraille") else name


def _makePinQuickNavScript(quickNavType, forward: bool):
	"""Build one quick navigation script for a pinned document."""

	def quickNavScript(self, gesture):
		self.quickNavInPin(gesture, quickNavType, forward)

	if forward:
		# Translators: input help message for a command. The placeholder is a kind of thing in a
		# document, such as heading or link.
		quickNavScript.__doc__ = _("Pinned document: Moves to the next {item}").format(item=quickNavType.label)
	else:
		# Translators: input help message for a command. The placeholder is a kind of thing in a
		# document, such as heading or link.
		quickNavScript.__doc__ = _("Pinned document: Moves to the previous {item}").format(item=quickNavType.label)
	quickNavScript.category = SCRIPT_CATEGORY
	return quickNavScript


def _generatePinQuickNavScripts() -> None:
	"""Add browse mode's quick navigation commands for a pinned document, next and previous of each
	type in `pinQuickNav.TYPES`. Bound in the pinned document layer; see `keyLayers`."""
	for quickNavType in pinQuickNav.TYPES:
		setattr(GlobalPlugin, f"script_pinNext{quickNavType.name}", _makePinQuickNavScript(quickNavType, True))
		setattr(
			GlobalPlugin,
			f"script_pinPrevious{quickNavType.name}",
			_makePinQuickNavScript(quickNavType, False),
		)


_generateSegmentScripts()
_generatePinQuickNavScripts()
