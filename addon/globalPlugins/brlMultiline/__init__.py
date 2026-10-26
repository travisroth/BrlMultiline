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
from typing import Callable, NamedTuple, Optional

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
from . import chartDraw, chartMenu, chartSource, glyphFlow, glyphs, graphicsMode
from . import appCollect, appLayout, appRegions, layoutCollect, layoutDraw, layoutRegions
from .appLayoutHost import AppLayoutHost
from . import image as imageFigure, imagePins, imageSource
from .flowTableSource import TableRequest, wantsColumns
from .graphics import findSurface as graphicsFindSurface
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
	deviceSegmentKeys,
	deviceView,
	driverNameForSegmentKey,
	singleSegmentView,
	validateAgainstHardware,
	viewFromConfig,
)

addonHandler.initTranslation()

# Translators: the name of the input gestures category for this add-on's commands.
SCRIPT_CATEGORY = _("BrlMultiline")

class _LayoutView:
	"""An overview of a page's layout that is up: what it was drawn from, and where the reader is.

	Kept so that drilling into an area and backing out draw from the same reading of the page,
	rather than reading it again, and so that the area last pressed is what a drill goes into.
	"""

	def __init__(self, page, view: str, viewport, document, identity=None, app: bool = False):
		self.page = page
		self.view = view
		self.viewport = viewport
		self.document = document
		#: Whether this is an application window's layout rather than a web page's: there is no
		#: page to scroll, and moving to an area moves the focus. See `_moveToAppArea`.
		self.app = app
		#: Which page and which reading of it the layout is of; see `_pageIdentity`.
		self.identity = tuple(identity) if identity else _pageIdentity(document)
		#: The area drilled into, or None for the whole page or screen.
		self.focus = None
		#: The areas drilled through to get here, outermost first; None stands for the whole.
		self.trail: list = []
		#: The area the last routing press landed in, which a drill goes into.
		self.pressed = None
		#: The drawing on the display, so a drill can tell it is still this overview that is up.
		self.drawing = None
		#: Key letters given out, by region, kept for every drawing of this reading of the page,
		#: so an area keeps its letter when drilled into, backed out of, zoomed or panned.
		self.keys: dict = {}


#: The role NVDA names each count of a page's layout by, in its braille labels, and the glyph
#: drawn for it where there is one.
LAYOUT_COUNT_ROLES = {
	"links": ("LINK", glyphs.LINK),
	"buttons": ("BUTTON", glyphs.BUTTON),
	"fields": ("EDITABLETEXT", glyphs.EDIT),
	"tabs": ("TAB", None),
	"sections": ("SECTION", None),
}


def _layoutCountStyle() -> "layoutDraw.CountStyle":
	""":return: how a page layout's counts are drawn for this reader and this display.

	NVDA's own braille abbreviation for each role, in the reader's language, or the role's
	glyph where glyphs are on and the display can show them.
	"""
	words = dict(layoutDraw.COUNT_WORDS)
	written = {}
	try:
		from braille.labels import roleLabels

		written = {getattr(member, "name", ""): label for member, label in roleLabels.items()}
	except Exception:
		log.debugWarning("BrlMultiline: NVDA's braille labels could not be read", exc_info=True)
	for count, (role, _glyph) in LAYOUT_COUNT_ROLES.items():
		label = written.get(role)
		if not label:
			# As NVDA's braille does for a role with no abbreviation: the role's own name.
			try:
				import controlTypes

				label = getattr(getattr(controlTypes.Role, role, None), "displayString", None)
			except Exception:
				label = None
		if label:
			words[count] = label
	shapes = {}
	surface = graphicsFindSurface()
	if surface is not None and glyphFlow.enabled() and glyphs.supported(surface.driver):
		for count, (_role, glyph) in LAYOUT_COUNT_ROLES.items():
			if glyph is not None:
				shapes[count] = glyphs.patternRows(glyph.dots)
	return layoutDraw.CountStyle(words, shapes)


def _pageIdentity(document) -> tuple:
	""":return: which page a browse mode document shows, and which reading of it: its address and
	its buffer. Browse mode can read a page again, or a page can become another, under the same
	document object, so the object alone does not say the text is the text a layout was made of.
	"""
	return (getattr(document, "documentConstantIdentifier", None), getattr(document, "VBufHandle", None))


LAYOUT_DEPTH = 3
"""Levels of areas a drawing of a page's layout shows: the main areas, the areas inside them,
and those inside those. Forms, search, tab strips and the like are drawn deeper still; see
`layoutDraw.MAJOR_KINDS`."""

LAYOUT_SMALLEST_VIEW = 200
"""Pixels of a web page, top to bottom, that the most magnified view of its layout shows."""

BREAK = chr(10)
"""A line break in a dialog's text, spelled so no escape has to survive a shell."""

_plugin = None

AFTER_DIALOG_MILLIS = 300
"""How long an answer to a dialog waits, so the focus coming back does not talk over it."""


def _sayAfterDialog(text: str) -> None:
	"""Say something once a dialog has closed and the focus is back where it was.

	NVDA announces the focus returning from a dialog, which would cut off anything said at once.

	:param text: what to say.
	"""
	wx.CallLater(AFTER_DIALOG_MILLIS, ui.message, text)


class FocusDisplayTarget(NamedTuple):
	"""One of the displays behind a composite, considered as somewhere to put the focus."""

	driverName: str
	"""The NVDA driver behind it, which is what names it in the configuration."""

	label: str
	"""What to call it when there is a choice to announce or to show in a list."""

	segments: list[int]
	"""The segments lying on it, numbered as the configuration numbers them, in display order.

	Never empty: a display holding no segment of its own is not somewhere the focus can go,
	and is left out of the list rather than offered and refused.
	"""

	holdsFocus: bool
	"""Whether the segment following the focus is one of these."""


def getPlugin() -> "GlobalPlugin | None":
	""":return: the running plugin instance, or None if it is not loaded."""
	return _plugin


class CarriedPin(NamedTuple):
	"""A pin the focus move took with it, and what it was before it went.

	What it was matters, because a pin moved from many rows to one stops being a flow, and
	the reader is told.
	"""

	monitor: ObjectMonitor
	home: str
	"""The segment it was filed under. What it ends up on is settled by the rebuild."""

	wasFlowing: bool


def _pictureRoot(picture):
	""":return: the captured picture another was made from, or the picture itself."""
	while picture is not None and getattr(picture, "derivedFrom", None) is not None:
		picture = picture.derivedFrom
	return picture


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

		self._pinsInFlight: list[ObjectMonitor] = []
		"""Pins a focus move is carrying, until the display it caused has settled.

		A displaced pin is moved before the rebuild, and the segment it moves to may come back
		under a different key once the flow band has claimed or released rows, so the
		carry-over must not release it. Held as monitors rather than keys, because one move
		can rebuild the display more than once. Held only across that one move's rebuilds.
		"""
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

		self._lastChartKind = None
		"""Which chart the reader picked last time, so the list opens on it.

		Not remembered across sessions.
		"""

		self._picture = None
		"""The pixels of the last thing the reader asked to be drawn, or None.

		Held so that changing the style redraws the same picture rather than copying the
		screen again, which may show something else by then.
		"""

		self._pictureDrawing = None
		"""The figure last made from `_picture`, so the style key can tell whether it is still
		what the display is showing.

		So the style key does not put an old picture back over something drawn since, such as
		a chart. The figure rather than a flag, since nothing here is told when the mode is left.
		"""

		self._pictureStyle = imageFigure.OUTLINES
		"""How pictures are being drawn, as a mode and whether it is reversed.

		Carried from one picture to the next within a session.
		"""
		self._pictureRequest = None
		"""The picture file being loaded for the last press, if one is; see `script_drawPicture`."""
		self._layoutCollector = None
		"""The page layout being gathered for an overview, if one is; see `_layoutOverview`."""
		self._layoutView: Optional[_LayoutView] = None
		"""The overview of a page's layout last drawn, for drilling into it; see `_LayoutView`."""

		self._activePanels: list[BraillePanel] = []
		"""Claims laid over whatever view is in force, in the order they were made.

		Kept across rebuilds and re-composed each time, so that a claim survives a settings
		change or a display swap. A claim that no longer fits is dropped and reported.
		"""
		try:
			self._install()
		except BaseException:
			# Whatever was put in place before the failure is taken out again, as on terminate.
			self._teardown.close()
			raise

	def _install(self) -> None:
		"""Put the add-on in place, registering each undo with `_onTerminate` as it goes."""
		bmConfig.initialize()
		patches.install()
		self._onTerminate(patches.remove)
		# A routing key in a segment showing an object goes to that object. See `monitorRouting`.
		containerModule.segmentRouter = self._routeIntoMonitor
		containerModule.segmentUsed = self._noteSegmentUsed
		self._onTerminate(setattr, containerModule, "segmentRouter", None)
		self._onTerminate(setattr, containerModule, "segmentUsed", None)
		# Which display's keys are being pressed, for the per display panning direction.
		panning.install()
		self._onTerminate(panning.remove)
		# The arrow keys inside a browse mode table, which is a way of reading rather than a
		# way of displaying and so is not waited on a flow. See `tableArrows`.
		tableArrows.install()
		self._onTerminate(tableArrows.remove)
		# Which tables' headers are not spoken, which is also how a table is read rather than
		# displayed, and so is not waited on a flow either. See `tableHeaderSpeech`.
		tableHeaderSpeech.install()
		self._onTerminate(tableHeaderSpeech.remove)
		# Layered keys. Reads each device's layers and passes every key through while no layer
		# is on. See `keyLayerDispatch`.
		keyLayerDispatch.install()
		self._onTerminate(keyLayerDispatch.remove)
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
		patched.
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

	def terminate(self):
		# Set first, so that anything already queued sees it even if a step below fails.
		self._terminated = True
		_runLoggingFailure(self._cancelPictureRequest)
		try:
			self._teardown.close()
		finally:
			global _plugin
			_plugin = None
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
		view = self._baseView(numRows, numCols, devices)
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

	def _baseView(self, numRows: int, numCols: int, devices: list[DeviceInfo]) -> SegmentView:
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

		A pin whose segment has gone gets one chance at a new home first; see L{_rehomeMonitor}.

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
			carrying = any(monitor is self._monitors[key] for monitor in self._pinsInFlight)
			if gone or carrying:
				# Re-homed only when its display has gone or a focus move is carrying it. Any
				# other loss of a segment is a decision, and the pin stays released.
				why = (
					f"{driverName} has gone"
					if gone
					else f"segment {key!r} is where the focus command put it and is not in the new layout"
				)
				home = self._rehomeMonitor(container, survivors)
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

	def _rehomeMonitor(self, container: DisplayContainer, taken: dict) -> str | None:
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

	def _inAnotherTable(self, obj) -> bool:
		""":return: whether the reader is in a table other than the one already asked for.

		False in the table asked for and in no table at all, so pressing the command outside a
		table clears a request left behind.

		:param obj: the object the reader is on.
		"""
		from .flowTableSource import tableAt

		try:
			handle = tableAt(obj)
		except Exception:
			log.debugWarning("Could not look for the table the reader is in", exc_info=True)
			return False
		return handle is not None and not wantsColumns(self.tableRequest.wanted, obj)

	def _wantTableHere(self, obj) -> bool:
		"""Record that the table an object is in should be read in columns.

		`FlowBand.layOutTable` without showing it, for when there is no band.

		:param obj: the object the reader is on.
		:return: whether they were in a table.
		"""
		from .flowTableSource import logExplanation, tableAt

		try:
			handle = tableAt(obj)
		except Exception:
			log.debugWarning("Could not look for a table to lay out", exc_info=True)
			return False
		if handle is None:
			# The same account the band writes when it refuses, because this is the same
			# refusal reaching the reader through the display that has no band.
			logExplanation(obj, "asked for a table in columns and found none")
			return False
		self.tableRequest.wanted = handle.key
		return True

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

	def focusDisplayTargets(self) -> list[FocusDisplayTarget]:
		""":return: the displays the focus could be made to follow, in stacking order.

		Numbered by `views.deviceSegmentKeys`, the configuration's numbering, not the
		container's, which changes while a claim such as the flow band is up. Empty for an
		ordinary display, and while an activated view has the whole display.
		"""
		handler = braille.handler
		if handler is None or handler.displaySize == 0:
			return []
		devices = deviceMap()
		if not devices:
			return []
		dimensions = handler.displayDimensions
		try:
			view = self._baseView(dimensions.numRows, dimensions.numCols, devices)
		except Exception:
			log.debugWarning("BrlMultiline: could not read the display's arrangement", exc_info=True)
			return []
		if view.name != "devices":
			# An activated view names its own focus segment, so the setting is not read.
			return []
		try:
			keys = deviceSegmentKeys(devices, dimensions.numCols)
		except ValueError:
			log.debugWarning("BrlMultiline: could not number the display's segments", exc_info=True)
			return []
		focusNumber = self._configuredFocusSegment(len(keys))
		names = displayDescriptions()
		targets = []
		for device in devices:
			numbers = [
				number for number, key in enumerate(keys) if driverNameForSegmentKey(key) == device.driverName
			]
			if not numbers:
				continue
			targets.append(
				FocusDisplayTarget(
					driverName=device.driverName,
					# Translators: names one of several combined braille displays, when choosing
					# which of them follows the focus. Placeholders are its name and its height.
					label=_("{display}, {rows} rows").format(
						display=names.get(device.driverName, device.driverName),
						rows=device.numRows,
					),
					segments=numbers,
					holdsFocus=focusNumber in numbers,
				),
			)
		return targets

	def _configuredFocusSegment(self, numSegments: int) -> int:
		""":return: the segment the configuration says follows the focus, as an index.

		:param numSegments: how many segments the arrangement has, for resolving -1 and for
			refusing a number left over from a larger display.
		"""
		number = bmConfig.getFocusSegment()
		if number == -1 or not 0 <= number < numSegments:
			return numSegments - 1
		return number

	def focusSegmentFor(self, target: FocusDisplayTarget, position: int = 0) -> int:
		""":return: the segment the focus would take on a display, in the configuration's numbering.

		:param target: the display it would move to.
		:param position: how far down its display the focus segment is now, kept if the new
			display is divided finely enough and clamped to its last segment if it is not.
		"""
		return target.segments[min(max(position, 0), len(target.segments) - 1)]

	def pinsDisplacedBy(self, number: int) -> list[str]:
		"""Which pinned objects the focus would evict by landing where it is going.

		A pin and the focus cannot share a segment, so `_carryOverMonitors` would release the
		pin quietly. Asked first, because a reader moving the focus is not thinking about the
		pin.

		:param number: the segment the focus is going to, in the configuration's numbering.
		:return: the keys of the segments whose pins would go, in display order.
		"""
		container = self.container
		if container is None or not self._monitors:
			return []
		try:
			key = self.segmentKeyFor(number)
		except LookupError:
			return []
		if key is None:
			return []
		return [spec.key for spec in container.specs if spec.key == key and spec.key in self._monitors]

	def segmentKeyFor(self, number: int) -> str | None:
		""":return: the segment a configured number names, or None if it names none.

		The configuration's numbering, as in `focusDisplayTargets`.
		"""
		handler = braille.handler
		devices = deviceMap()
		if handler is None or not devices:
			return None
		try:
			keys = deviceSegmentKeys(devices, handler.displayDimensions.numCols)
		except ValueError:
			log.debugWarning("BrlMultiline: could not number the display's segments", exc_info=True)
			return None
		if number == -1:
			number = len(keys) - 1
		return keys[number] if 0 <= number < len(keys) else None

	def homesForDisplacedPins(self, number: int) -> list[str]:
		"""Where a pin evicted by the focus could go instead, best first.

		The segment the focus is leaving comes first, so the two trade places and moving back
		trades them back; then the rest of the display in order. The segment being left
		counts even if the flow band claims it, since the band moves with the focus.

		This is where a pin could go, not where it will end up: `_carryOverMonitors` settles
		that against the display actually built.

		:param number: the segment the focus is going to.
		:return: the keys of segments that could hold a pin, best first.
		"""
		container = self.container
		if container is None:
			return []
		going = self.segmentKeyFor(number)
		taken = set(self._monitors)
		homes: list[str] = []
		ordered = [container.focusSegmentKey] + [spec.key for spec in container.specs]
		for key in ordered:
			if key in homes or key == going or key in taken or not container.hasKey(key):
				continue
			if container.segmentForKey(key).isReserved and key != container.focusSegmentKey:
				continue
			homes.append(key)
		return homes

	def moveFocusToDisplay(
		self,
		target: FocusDisplayTarget,
		position: int = 0,
		keep: "dict[str, str] | None" = None,
	) -> None:
		"""Make one of the combined displays the one that follows the system focus.

		:param target: the display to move it to.
		:param position: how far down its display the focus segment is now, kept if the new
			display is divided finely enough and clamped to its last segment if it is not.
		:param keep: pins to carry rather than let go, from the key they are on now to the key
			they should move to. Others are released by `_carryOverMonitors`.
		"""
		number = self.focusSegmentFor(target, position)
		carried = self._carryPinsClearOfTheFocus(keep or {})
		bmConfig.setFocusSegment(number)
		try:
			# One move can rebuild the display several times: the band is claimed or given
			# back as the focus arrives, and claiming rebuilds again. The pins are in flight
			# for all of it, and land only when it has finished.
			self.rebuildBuffer()
		finally:
			self._landPinsInFlight()
		ui.message(
			# Translators: reported when the focus is moved onto one of several combined
			# displays. Placeholders are the display's name and the segment number.
			_("Focus on {display}, segment {number}").format(display=target.label, number=number),
		)
		for message in self._whatBecameOfTheCarriedPins(carried):
			ui.message(message)

	def _landPinsInFlight(self) -> None:
		"""Put the pins a focus move carried into the display it ended up building.

		Handles a pin that was held, not released, because it had nowhere to go part way
		through the rebuilds. If there is still nowhere, it is released here and the reader is
		told.
		"""
		inFlight, self._pinsInFlight = self._pinsInFlight, []
		container = self.container
		landed = False
		for monitor in inFlight:
			if any(monitor is filed for filed in self._monitors.values()):
				continue
			home = self._rehomeMonitor(container, self._monitors) if container is not None else None
			if home is None:
				log.info(
					f"BrlMultiline: nowhere on this display for the object pinned to "
					f"{monitor.segmentKey!r}, so moving the focus released it",
				)
				continue
			log.info(f"BrlMultiline: the pin the focus move carried lands on segment {home!r}")
			monitor.moveTo(home)
			self._monitors[home] = monitor
			landed = True
		if landed:
			# Drawn where it landed, rather than waiting for the refresh tick to notice.
			self.refreshMonitors()

	def _whatBecameOfTheCarriedPins(self, carried: "list[CarriedPin]") -> list[str]:
		""":return: what to tell the reader about the pins a focus move took with it.

		After the rebuild, which decides where each pin landed and how many rows it has.
		Reports a pin that could not be kept, and a pin that is no longer read as a flow.

		:param carried: what the move took with it, before the rebuild.
		"""
		if not carried:
			return []
		alive = {id(monitor) for monitor in self._monitors.values()}
		kept = [pin for pin in carried if id(pin.monitor) in alive]
		messages = []
		if len(kept) > 1:
			messages.append(
				# Translators: reported when moving the focus moves pinned objects out of its
				# way. The placeholder is how many objects moved.
				_("{count} pinned objects moved").format(count=len(kept)),
			)
		elif kept:
			# Translators: reported when moving the focus moves one pinned object out of its
			# way, so that it is not lost.
			messages.append(_("The pinned object moved with it"))
		flattened = sum(
			1 for pin in kept if pin.wasFlowing and getattr(pin.monitor, "controller", None) is None
		)
		if flattened:
			messages.append(
				# Translators: reported when a pinned object that was being read as a flow —
				# a list, a document, a table — lands somewhere with too few rows for one, so
				# it is now a single line that cannot be panned through.
				_("Too few rows there to read it as a flow"),
			)
		lost = len(carried) - len(kept)
		if lost:
			messages.append(
				# Translators: reported when moving the focus could not keep a pinned object
				# after all. The placeholder is how many were lost.
				_("{count} pinned objects could not be kept").format(count=lost)
				if lost > 1
				# Translators: reported when moving the focus could not keep the one pinned
				# object it was carrying.
				else _("The pinned object could not be kept"),
			)
		return messages

	def _carryPinsClearOfTheFocus(self, keep: "dict[str, str]") -> "list[CarriedPin]":
		"""Move pins to their new homes before the display is rebuilt.

		Before the rebuild, so `_carryOverMonitors` sees each pin where it now is. A home the
		move itself dissolves, such as rows the flow band gives back, is handled through
		`_pinsInFlight`.

		:param keep: the key each pin is on now, to the key it should move to.
		:return: what moved, so the caller can say what became of it.
		"""
		carried: list[CarriedPin] = []
		for key, home in keep.items():
			monitor = self._monitors.get(key)
			if monitor is None or home == key:
				continue
			wasFlowing = getattr(monitor, "controller", None) is not None
			try:
				monitor.moveTo(home)
			except Exception:
				log.debugWarning(f"Could not move the pin on segment {key!r}", exc_info=True)
				continue
			del self._monitors[key]
			self._monitors[home] = monitor
			carried.append(CarriedPin(monitor=monitor, home=home, wasFlowing=wasFlowing))
		self._pinsInFlight = [pin.monitor for pin in carried]
		return carried

	def _askAboutDisplacedPins(
		self,
		target: FocusDisplayTarget,
		position: int,
		displaced: list[str],
		homes: list[str],
	) -> None:
		"""Ask which pinned objects to keep before moving the focus over them.

		The reader may keep as many as there are free segments, or cancel the move. The only
		place on this path that uses wx, so the rest can be tested without it.

		:param target: the display the focus would move to.
		:param position: how far down it the focus would sit.
		:param displaced: the keys of the pins in the way, in display order.
		:param homes: where they could go instead, best first.
		"""
		names = [self._pinDescription(key) for key in displaced]
		room = len(homes)

		def ask():
			gui.mainFrame.prePopup()
			try:
				if not room:
					answer = gui.messageBox(
						# Translators: a dialog shown when moving the focus would release
						# pinned objects and there is nowhere else to put them. The
						# placeholder is a list of what is pinned.
						_(
							"Moving the focus here would release these pinned objects, "
							"and there is nowhere else on the display to put them:{objects}"
							"{gap}Move the focus anyway?"
						).format(
							objects=BREAK + BREAK.join(names),
							gap=BREAK + BREAK,
						),
						# Translators: the title of that dialog.
						_("Moving the focus"),
						wx.YES_NO | wx.ICON_WARNING,
					)
					if answer == wx.YES:
						self.moveFocusToDisplay(target, position)
					return
				dialog = wx.MultiChoiceDialog(
					gui.mainFrame,
					# Translators: a dialog asking which pinned objects to keep when moving
					# the focus would release them. Placeholders are how many objects are in
					# the way and how many there is room to keep.
					_(
						"Moving the focus here would release {count} pinned objects. "
						"There is room to keep {room}. Choose which to keep; the rest are released."
					).format(count=len(displaced), room=room),
					# Translators: the title of that dialog.
					_("Moving the focus"),
					names,
				)
				try:
					dialog.SetSelections(list(range(min(room, len(names)))))
					if dialog.ShowModal() != wx.ID_OK:
						return
					chosen = list(dialog.GetSelections())
				finally:
					dialog.Destroy()
			finally:
				gui.mainFrame.postPopup()
			keep = {}
			for index in chosen[:room]:
				if 0 <= index < len(displaced):
					keep[displaced[index]] = homes[len(keep)]
			self.moveFocusToDisplay(target, position, keep=keep)

		wx.CallAfter(ask)

	def _pinDescription(self, key: str) -> str:
		""":return: what to call a pinned object in a dialog."""
		monitor = self._monitors.get(key)
		name = getattr(monitor, "name", None) or key
		container = self.container
		try:
			number = container.numberForKey(key) if container is not None else None
		except LookupError:
			number = None
		if number is None:
			return str(name)
		# Translators: names a pinned object and the segment it is in, in a dialog listing
		# them. Placeholders are the object's name and the segment number.
		return _("{name}, segment {number}").format(name=name, number=number)

	def _chooseFocusDisplay(self, targets: list[FocusDisplayTarget], position: int) -> None:
		"""Ask which display should follow the focus, then move it there.

		Only with more than two displays. The only place on this path that uses wx.

		:param targets: the displays to offer, in stacking order.
		:param position: passed on to L{moveFocusWithItsPins}.
		"""
		current = next((index for index, target in enumerate(targets) if target.holdsFocus), 0)

		def ask():
			# prePopup before the dialog is made and postPopup after it has gone, which is
			# how NVDA opens one: it raises its own frame first so the dialog can take the
			# focus, and puts things back afterwards.
			gui.mainFrame.prePopup()
			try:
				dialog = wx.SingleChoiceDialog(
					gui.mainFrame,
					# Translators: the message of a dialog asking which of several combined
					# braille displays should follow the system focus.
					_("Which display should follow the focus?"),
					# Translators: the title of a dialog asking which of several combined
					# braille displays should follow the system focus.
					_("Follow the focus"),
					[target.label for target in targets],
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
			if 0 <= chosen < len(targets):
				# Through the pin-preserving move, exactly as the two-display toggle goes:
				# choosing a display from a list is the same request as toggling to it, and a
				# pin on the one chosen was silently released while this called the plain move
				# directly.
				self.moveFocusWithItsPins(targets[chosen], position)

		wx.CallAfter(ask)

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
			view = self._baseView(numRows, numCols, deviceMap())
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
		"""Put the segment that follows the system focus on another of the displays.

		With two displays this toggles; with more, it offers a list. Stored in the profile in
		force.
		"""
		targets = self.focusDisplayTargets()
		if len(targets) < 2:
			ui.message(
				# Translators: reported when a command that moves the focus from one braille
				# display to another is pressed with only one display to put it on.
				_("There is only one display for the focus to follow"),
			)
			return
		here = next((target for target in targets if target.holdsFocus), None)
		if here is None:
			# The focus is on cells no display of its own owns, which a claim over the whole
			# composite can do. Anywhere is as good as anywhere, so start at the top.
			position = 0
		else:
			total = sum(len(target.segments) for target in targets)
			position = here.segments.index(self._configuredFocusSegment(total))
		if len(targets) > 2:
			self._chooseFocusDisplay(targets, position)
			return
		other = next(target for target in targets if target is not here)
		self.moveFocusWithItsPins(other, position)

	def moveFocusWithItsPins(self, target: FocusDisplayTarget, position: int = 0) -> None:
		"""Move the focus to a display, taking any pin it would evict with it.

		1. Nothing in the way: the focus moves.
		2. One pin and somewhere to put it: they trade places, without asking.
		3. Anything else: the reader chooses which pins to keep, or cancels.

		:param target: the display to move the focus to.
		:param position: how far down its display the focus segment is now.
		"""
		number = self.focusSegmentFor(target, position)
		displaced = self.pinsDisplacedBy(number)
		homes = self.homesForDisplacedPins(number)
		if not displaced:
			self.moveFocusToDisplay(target, position)
			return
		if len(displaced) == 1 and homes:
			self.moveFocusToDisplay(target, position, keep={displaced[0]: homes[0]})
			return
		self._askAboutDisplacedPins(target, position, displaced, homes)

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
		"""Read the table the reader is in as columns, or stop.

		A command rather than automatic, because most web tables are for layout. Saved layouts
		apply by themselves; see `script_rememberTableLayout`.
		"""
		band = self.flowBand
		target = api.getNavigatorObject()
		# Off only when the request is about where the reader is standing, or when they are
		# not in a table at all — pressing it outside one is how a request left behind is
		# cleared. Pressing it in a *different* table means that table, not this one off: with
		# no band nothing drops the old request, so the press that should have laid out the
		# second table was turning the first one off instead.
		if self.tableRequest.wanted is not None and not self._inAnotherTable(target):
			# Remembered before the request is dropped, because dropping it is what lets a
			# saved layout put it back. See `TableRequest.refused`.
			self.tableRequest.refused = self.tableRequest.wanted
			if band is not None:
				band.clearTable()
			else:
				self.tableRequest.wanted = None
			self.layOutPinnedTables(False, target)
			# Translators: reported when a table stops being laid out in columns.
			self.reportAboutTheDisplay(_("Table columns off"))
			return
		# Neither an empty band nor no band is a reason to refuse: `FlowBand.refresh` lets a
		# requested table through the flow settings, and on a one row display the request
		# carries to a pin.
		if band is None or not band.isClaimed:
			if not self._wantTableHere(target):
				# Translators: reported when a command needs the cursor to be in a table.
				ui.message(_("Not in a table"))
				return
			self.layOutPinnedTables(True, target)
			ui.message(
				# Translators: reported when a table is asked for in columns while there is no
				# room on the display to show it, so that pinning it elsewhere shows it that
				# way.
				_("Table columns on, for a pinned copy; there is no band to show it here"),
			)
			return
		if not band.layOutTable():
			from .flowBand import NO_LAYOUT, NOT_READ

			if band.tableProblem == NOT_READ:
				ui.message(
					# Translators: reported when the cursor is in a table but nothing could be
					# read out of it, so there were no columns to arrange. Said apart from the
					# message below because this one is often a moment that has passed and is
					# worth trying again, where that one is about this display.
					_("Nothing could be read from this table; the NVDA log says why"),
				)
				return
			if band.tableProblem == NO_LAYOUT:
				ui.message(
					# Translators: reported when the cursor is in a table but it could not be
					# shown in columns. The NVDA log says which step it was.
					_("This table could not be laid out in columns; the NVDA log says why"),
				)
				return
			# Translators: reported when a command needs the cursor to be in a table.
			ui.message(_("Not in a table"))
			return
		self.layOutPinnedTables(True, target)
		control = band.controller
		plan = getattr(getattr(control, "renderer", None), "columnPlan", None)
		if plan is None:
			# Translators: reported when a table is laid out in columns.
			self.reportAboutTheDisplay(_("Table columns on"))
			return
		# What it cost is said, not only that it worked. A column narrower than its content
		# cuts every one of its cells at the same place, which is what makes the shape down
		# the display readable and also what leaves the reader feeling clipped words with
		# nothing to say they are clipped. Three columns of real text on a thirty-two cell
		# band is the ordinary case for it, not the extreme one.
		said = [
			# Translators: reported when a table is laid out in columns. The placeholder is
			# how many columns are shown.
			_("Table columns on, {shown} columns").format(shown=len(plan.columns)),
		]
		if plan.narrowed and plan.cuts:
			said.append(
				# Translators: reported after the above when some columns are too narrow for
				# what they hold and long values are being cut off. The placeholder is how
				# many columns are affected.
				_("{cut} cut to fit").format(cut=len(plan.narrowed)),
			)
		elif plan.narrowed:
			said.append(
				# Translators: reported after the above when some columns are too narrow for
				# what they hold, so long values continue on the next row. The placeholder is
				# how many columns are affected.
				_("{wrapped} wrapping").format(wrapped=len(plan.narrowed)),
			)
		if not plan.showsEverything:
			first, last, total = plan.whereItIs
			said.append(
				# Translators: reported after the above when a table has more columns than the
				# display can show at once. Placeholders are, in order, the first and last of
				# the columns being shown and how many columns there are in all.
				_("columns {first} to {last} of {total}").format(
					first=first,
					last=last,
					total=total,
				),
			)
		self.reportAboutTheDisplay(", ".join(said))

	@script(
		# Translators: input help message for a command.
		description=_("Table: Toggle showing the table's rows on one line, cells divided by bars"),
		category=SCRIPT_CATEGORY,
	)
	def script_tableRowLine(self, gesture):
		"""Read the table the caret is in a row at a time on NVDA's one line, or stop.

		For a display with no band. See `tableRowLine`. On and off follow the same rule as
		`script_flowTableColumns`.
		"""
		from . import flowObjectTable, flowTableLayouts
		from .flowTableSource import sameTable

		handle = self._tableHere()
		asked = tableRowLine.wanted()
		if asked is not None and (handle is None or sameTable(handle.key, asked.key)):
			tableRowLine.clear()
			tableRowLine.redraw(braille.handler)
			# Translators: reported when a table stops being read a row at a time on one line.
			self.reportAboutTheDisplay(_("Table rows off"))
			return
		if handle is None:
			# Translators: reported when a command needs the cursor to be in a table.
			ui.message(_("Not in a table"))
			return
		if isinstance(handle.document, flowObjectTable.ObjectTable):
			# A list or a worksheet is shown by NVDA a cell or an item at a time through regions
			# of its own, not through the browse mode line this replaces.
			ui.message(
				# Translators: reported when rows on one line are asked for in a table that is not
				# on a web page or in a document read in browse mode.
				_("Rows on one line are for tables in browse mode"),
			)
			return
		try:
			layout = flowTableLayouts.layoutFor(handle)
		except Exception:
			log.debugWarning("Could not look for a saved layout for this table", exc_info=True)
			layout = None
		# A saved layout's choice of columns and their order, where the reader made one, so
		# that the line says what the columns would. Everything else about a layout is widths.
		tableRowLine.request(handle, layout.columns if layout is not None else ())
		tableRowLine.redraw(braille.handler)
		# Translators: reported when a table starts being read a row at a time on one line.
		self.reportAboutTheDisplay(_("Table rows on one line"))

	def _columnUnderTheCursor(self):
		""":return: the band and the table column the cursor is in, or (None, 0).

		The column the reader is in, not the one at the left of the display.
		"""
		band = self.flowBand
		if band is None or band.columnPlan() is None:
			return None, 0
		source = getattr(band.controller, "source", None)
		try:
			return band, int(getattr(source, "column", 0) or 0)
		except (TypeError, ValueError):
			return band, 0

	def _sayAboutTheColumn(self, band, column: int, said: str) -> None:
		"""Report a change to one column, naming it the way the reader knows it.

		:param band: the band showing the table.
		:param column: the table's own column number.
		:param said: what happened to it.
		"""
		plan = band.columnPlan()
		found = next((item for item in plan.columns if item.index == column), None) if plan else None
		# The heading where the column has one, and "column 6" where it has not — the same
		# answer the paging report gives, since a bare number is not a name.
		name = (
			_columnName(found)
			if found is not None
			# Translators: how a column is named when it is not one the layout is drawing. The
			# placeholder is which column of the table it is.
			else _("column {index}").format(index=column)
		)
		self.reportAboutTheDisplay(f"{name}: {said}")

	@script(
		# Translators: input help message for a command.
		description=_("Table: Show/Hide the current column"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowTableToggleColumn(self, gesture):
		"""Take the column the cursor is in out of the layout, or put it back.

		For exploring a table without opening the designer.
		"""
		band, column = self._columnUnderTheCursor()
		if band is None or not column:
			# Translators: reported when a command needs a table laid out in columns and there
			# is none on the display.
			ui.message(_("No table columns are showing"))
			return
		plan = band.columnPlan()
		showing = [item.index for item in plan.columns]
		if column in showing and len(showing) < 2:
			# Translators: reported when hiding a column would leave a table with none.
			ui.message(_("This is the only column showing"))
			return
		wanted = [index for index in showing if index != column] if column in showing else None
		if wanted is None:
			# Back again, in the table's own order, which is where the reader will expect it.
			wanted = sorted({*showing, column})
		if not band.showTheseColumns(wanted):
			return
		self._sayAboutTheColumn(
			band,
			column,
			# Translators: reported when a column is taken out of a table's layout.
			_("hidden") if column not in wanted else _("showing"),
		)

	@script(
		# Translators: input help message for a command.
		description=_("Table: Changes how the column you are in is trimmed when it does not fit"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowTableCutColumn(self, gesture):
		"""Cycle one column between wrapping, cut at the start, and cut at the end.

		For a column whose useful text is at the end of each cell.
		"""
		from . import flowTable

		band, column = self._columnUnderTheCursor()
		if band is None or not column:
			# Translators: reported when a command needs a table laid out in columns and there
			# is none on the display.
			ui.message(_("No table columns are showing"))
			return
		# From what the reader is feeling rather than from what they have said: a column they
		# have said nothing about is being drawn some way, and the next press should move on
		# from *that* rather than from the start of the list.
		plan = band.columnPlan()
		drawn = next((item for item in plan.columns if item.index == column), None) if plan else None
		order = (
			(flowTable.WRAP, "", _("wrapped")),
			(flowTable.TRUNCATE, flowTable.KEEP_START, _("cut, keeping the start")),
			(flowTable.TRUNCATE, flowTable.KEEP_END, _("cut, keeping the end")),
		)
		here = next(
			(
				position
				for position, (overflow, keep, _said) in enumerate(order)
				if drawn is not None and overflow == drawn.overflow and (not keep or keep == drawn.keep)
			),
			-1,
		)
		overflow, keep, said = order[(here + 1) % len(order)]
		if band.arrangeColumn(column, overflow=overflow, keep=keep):
			self._sayAboutTheColumn(band, column, said)

	@script(
		# Translators: input help message for a command.
		description=_("Table: Undo custom table layout"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowTableResetLayout(self, gesture):
		"""Drop what has been arranged from the band, without touching what was saved.

		A saved layout comes back; `script_forgetTableLayout` removes it.
		"""
		band = self.flowBand
		if band is None or band.columnPlan() is None:
			# Translators: reported when a command needs a table laid out in columns and there
			# is none on the display.
			ui.message(_("No table columns are showing"))
			return
		band.arrangeTable(None)
		self.reportAboutTheDisplay(
			# Translators: reported when the columns a reader arranged are given up, so the
			# table is laid out the way it would be if they had arranged nothing.
			_("Reading this table as it comes"),
		)

	@script(
		# Translators: input help message for a command.
		description=_("Table: Open table layout designer for the current table"),
		category=SCRIPT_CATEGORY,
	)
	@gui.blockAction.when(gui.blockAction.Context.MODAL_DIALOG_OPEN)
	def script_flowTableDesigner(self, gesture):
		"""Open the designer for the table on the display.

		For arrangements a keystroke cannot make, such as column names and widths. See
		`flowTableDesigner`. Only asks for the dialog: a modal dialog shown from a script,
		inside `queueHandler.pumpAll`, would hang NVDA. See `flowTableDesigner.arrangeTheTable`.
		"""
		from . import flowTableDesigner

		band = self.flowBand
		if band is None or band.columnPlan() is None:
			# Translators: reported when a command needs a table laid out in columns and there
			# is none on the display.
			ui.message(_("No table columns are showing"))
			return
		try:
			flowTableDesigner.arrangeTheTable(band)
		except Exception:
			log.debugWarning("Could not arrange the table", exc_info=True)

	@script(
		# Translators: input help message for a command.
		description=_("Table: Save table layout"),
		category=SCRIPT_CATEGORY,
	)
	def script_rememberTableLayout(self, gesture):
		"""Save the layout on the display against the table it is showing.

		Saves what the reader arranged, never the measured columns, so a measurement is not
		stored as a decision.
		"""
		from . import flowTableLayouts

		band = self.flowBand
		plan = band.columnPlan() if band is not None else None
		if plan is None:
			# Translators: reported when a command needs a table laid out in columns and there
			# is none on the display.
			ui.message(_("No table columns are showing"))
			return
		handle = self._tableHere()
		if handle is None:
			# Translators: reported when a command needs the cursor to be in a table.
			ui.message(_("Not in a table"))
			return
		arranged = band.tableLayoutInForce
		try:
			saved = flowTableLayouts.remember(handle, flowTableLayouts.layoutFrom(plan, handle, arranged))
		except flowTableLayouts.LayoutsNotSaved:
			ui.message(
				# Translators: reported when saving table layouts to the configuration failed.
				_("The table layout could not be saved, see the log"),
			)
			return
		if not saved:
			ui.message(
				# Translators: reported when a table's layout cannot be saved because nothing
				# about the table or its window is stable enough to recognise it again.
				_("This table cannot be recognised again, so its layout cannot be saved"),
			)
			return
		self.reportAboutTheDisplay(
			# Translators: reported when a table's layout is saved, so that the table comes up
			# in columns whenever the reader is in it.
			_("This table will display in columns"),
		)

	@script(
		# Translators: input help message for a command.
		description=_("Table: Delete saved table layout"),
		category=SCRIPT_CATEGORY,
	)
	def script_forgetTableLayout(self, gesture):
		"""Drop the saved layout for this table, so it reads as the page around it again."""
		from . import flowTableLayouts

		handle = self._tableHere()
		if handle is None:
			# Translators: reported when a command needs the cursor to be in a table.
			ui.message(_("Not in a table"))
			return
		try:
			forgotten = flowTableLayouts.forget(handle)
		except flowTableLayouts.LayoutsNotSaved:
			# Translators: reported when deleting a saved table layout from the configuration failed.
			ui.message(_("The table layout could not be deleted, see the log"))
			return
		if not forgotten:
			# Translators: reported when a table has no saved layout to forget.
			ui.message(_("This table has no saved layout"))
			return
		band = self.flowBand
		if band is not None and band.tableWanted is not None:
			band.clearTable()
		self.tableRequest.wanted = None
		self.tableRequest.refused = None
		# Translators: reported when a table's saved layout is dropped.
		ui.message(_("Table layout deleted"))

	@script(
		# Translators: input help message for a command.
		description=_("Table: Manage saved table layouts"),
		category=SCRIPT_CATEGORY,
	)
	@gui.blockAction.when(gui.blockAction.Context.MODAL_DIALOG_OPEN)
	def script_manageTableLayouts(self, gesture):
		"""List every saved table layout, and fix the ones that stopped applying.

		Opened over the table the reader is in, if they are in one, so that a layout whose site changed
		its address can be pointed at this table with one button. Works outside a table too, for
		renaming and deleting. See `flowTableManager`.
		"""
		from . import flowTableManager

		try:
			flowTableManager.manageLayouts(self._tableHere(), afterwards=self._takeUpSavedLayouts)
		except Exception:
			log.debugWarning("Could not open the saved table layouts", exc_info=True)

	def _takeUpSavedLayouts(self) -> None:
		"""Show the table the reader is in the way the saved layouts now say, after the manager changed them.

		The band offers a saved layout once per table, so without this a layout just pointed at this
		table would not come up until the reader left the table and came back.
		"""
		band = self.flowBand
		if band is None:
			return
		band._layoutOffered = None
		if not band.clearTable():
			band.refresh(force=True)

	def _tableHere(self):
		""":return: the table the reader is in, or None, without disturbing anything.

		From the navigator object, as the column command uses.
		"""
		from .flowTableSource import logExplanation, tableAt

		obj = api.getNavigatorObject()
		try:
			handle = tableAt(obj)
		except Exception:
			log.debugWarning("Could not look for the table the reader is in", exc_info=True)
			return None
		if handle is None:
			logExplanation(obj, "asked about the table the reader is in and found none")
		return handle

	@script(
		# Translators: input help message for a command.
		description=_("Table: Next columns"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowNextColumns(self, gesture):
		"""Move a table too wide to show at once, on the display the key was pressed on."""
		self._turnColumnPage(1, gesture)

	@script(
		# Translators: input help message for a command.
		description=_("Table: Previous columns"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowPreviousColumns(self, gesture):
		"""Move a table back, on the display the key was pressed on."""
		self._turnColumnPage(-1, gesture)

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

	def _tableToPage(self, gesture) -> object:
		""":return: which table a paging command should move, or None if there is none.

		The table on the display whose key was pressed, as the panning keys do; failing that,
		the first table in display order.

		:param gesture: the gesture that ran the command, if there was one.
		"""
		showing = self.tablesInColumns()
		if not showing:
			return None
		container = self.container
		order = [segment.key for segment in container.segments] if container is not None else []
		pressed = self.segmentKeysOn(keyLayerDispatch.displayFor(gesture))
		for key in [*pressed, *order]:
			if key in showing:
				return showing[key]
		return next(iter(showing.values()))

	def _turnColumnPage(self, by: int, gesture=None) -> None:
		"""Move a table by pages of columns, and say where it landed.

		Said, because the display cannot show which page of columns it is on.

		:param by: how many pages to move, negative for back.
		:param gesture: the gesture that ran the command, which says which display's table is
			meant. See `_tableToPage`.
		"""
		band = self._tableToPage(gesture)
		plan = band.columnPlan() if band is not None else None
		if plan is None:
			# Translators: reported when a command needs a table laid out in columns and there
			# is none on the display.
			ui.message(_("No table columns are showing"))
			return
		if plan.showsEverything:
			# Translators: reported when a table's columns all fit on the display already, so
			# there is nowhere to move to.
			ui.message(_("The whole table is showing"))
			return
		if not band.turnColumnPage(by):
			first, last, _total = plan.whereItIs
			self.reportAboutTheDisplay(
				# Translators: reported when a command would move past the first or last
				# columns of a table. Placeholders are the first and last columns showing.
				_("Columns {first} to {last}, no further").format(first=first, last=last),
			)
			return
		now = band.columnPlan()
		labels = ", ".join(_columnName(place.column) for place in now.placements())
		first, last, total = now.whereItIs
		self.reportAboutTheDisplay(
			# Translators: reported after moving across a table's columns. Placeholders are,
			# in order, the first and last of the columns now showing, how many columns there
			# are in all, and the names of the ones on the display.
			_("Columns {first} to {last} of {total}: {columns}").format(
				first=first,
				last=last,
				total=total,
				columns=labels,
			),
		)

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
		self._cancelPictureRequest()
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
		"""Draw what the reader has selected as a tactile chart, of a type they choose.

		A routing press on the chart says what is under the finger. Asks which chart rather
		than guessing, since the same cells can mean different charts; the list holds only
		charts that will draw. Every failure says a reason the reader can act on.
		"""
		self._cancelPictureRequest()
		if self.graphicsMode.drawingSize() is None:
			# Translators: reported when a drawing was asked for on a display that cannot draw.
			ui.message(_("This display cannot show graphics"))
			return
		if self._drawFocusedChart():
			return
		try:
			offers = chartMenu.offersFor(chartSource.gridFromFocus())
		except chartSource.NoNumbers as refusal:
			ui.message(str(refusal))
			return
		except Exception:
			log.error("BrlMultiline: could not read the selection to chart", exc_info=True)
			# Translators: reported when charting failed for a reason worth a log entry.
			ui.message(_("The chart could not be made, see the log"))
			return
		if not offers:
			# Translators: reported when nothing selected can be drawn as any chart.
			ui.message(_("There is nothing here that can be charted"))
			return
		if len(offers) == 1:
			# Nothing to choose between, so nothing to ask. The prompt exists because a
			# selection can mean several charts, not as a step on the way to every chart.
			self.drawChart(offers[0])
			return
		self._chooseChart(offers)

	def _drawFocusedChart(self) -> bool:
		"""Draw the chart the reader is on, if they are on one: the application's own chart.

		Asked before the selection, and with no dialog, since the chart already says what it is.
		See `docs/design/excel-chart-plan.md`.

		:return: whether the reader was on a chart, whatever came of drawing it.
		"""
		try:
			chart = chartSource.chartFromFocus()
		except chartSource.CallCancelled:
			ui.message(_("The application did not answer in time; try again"))
			return True
		except Exception:
			log.debugWarning("Could not ask the focus for a chart", exc_info=True)
			return False
		if chart is None:
			return False
		try:
			definition = chart.definition()
			log.info(
				f"BrlMultiline: charting {definition.typeName or 'a chart'} {definition.title!r}, "
				f"{len(definition.series)} series of {len(definition.categories)} points",
			)
			offer = chartMenu.offerForChart(definition)
		except chartSource.NoNumbers as refusal:
			log.info(f"BrlMultiline: chart refused: {refusal}")
			ui.message(str(refusal))
			return True
		except chartSource.CallCancelled:
			ui.message(_("The application did not answer in time; try again"))
			return True
		except Exception:
			log.error("BrlMultiline: could not read the chart", exc_info=True)
			ui.message(_("The chart could not be made, see the log"))
			return True
		self.drawChart(offer, remember=False)
		return True

	def _chooseChart(self, offers: list) -> None:
		"""Ask which of several charts to draw, then draw it.

		The only place on this path that uses wx; what is on offer is decided in `chartMenu`.

		:param offers: the charts that can be drawn, in the order to offer them.
		"""
		opening = 0
		for index, offer in enumerate(offers):
			if offer.key == self._lastChartKind:
				opening = index
				break

		def ask():
			# prePopup before the dialog is made and postPopup after it has gone, which is
			# how NVDA opens one: it raises its own frame first so the dialog can take the
			# focus, and puts things back afterwards.
			gui.mainFrame.prePopup()
			try:
				dialog = wx.SingleChoiceDialog(
					gui.mainFrame,
					# Translators: the message of a dialog asking which kind of chart to draw
					# from the cells the reader has selected.
					_("Which chart?"),
					# Translators: the title of a dialog asking which kind of chart to draw
					# from the cells the reader has selected.
					_("Chart the selection"),
					[offer.label for offer in offers],
				)
				try:
					dialog.SetSelection(opening)
					if dialog.ShowModal() != wx.ID_OK:
						return
					chosen = dialog.GetSelection()
				finally:
					dialog.Destroy()
			finally:
				gui.mainFrame.postPopup()
			if 0 <= chosen < len(offers):
				self.drawChart(offers[chosen], say=_sayAfterDialog)

		wx.CallAfter(ask)

	def drawChart(self, offer, say: Optional[Callable] = None, remember: bool = True) -> None:
		"""Draw one of the charts on offer and put it on the display.

		The size is read again here, since the display may have changed while a dialog was open.

		:param offer: what to draw, from `chartMenu.offersFor`.
		:param say: speaks the answer, `ui.message` if not given. `_sayAfterDialog` when the
			chart was chosen in the dialog.
		:param remember: whether this is the kind to open the chart dialog on next time. Not for
			an application's own chart, which was never chosen in the dialog.
		"""
		say = say or ui.message
		# Again here, for a picture pressed for while the chart dialog was open.
		self._cancelPictureRequest()
		mode = self.graphicsMode
		size = mode.drawingSize()
		if size is None:
			# Translators: reported when a drawing was asked for on a display that cannot draw.
			say(_("This display cannot show graphics"))
			return
		try:
			drawing = offer.draw(mode.newBuffer, size[0], size[1], graphicsMode.labelCells)
		except (chartSource.NoNumbers, chartDraw.ChartRefused) as refusal:
			# Logged as well as said: a refusal said over the focus coming back was heard as
			# nothing happening, and the log had nothing to show for it either.
			log.info(f"BrlMultiline: {offer.key} chart refused: {refusal}")
			say(str(refusal))
			return
		except Exception:
			log.error("BrlMultiline: could not chart the selection", exc_info=True)
			# Translators: reported when charting failed for a reason worth a log entry.
			say(_("The chart could not be made, see the log"))
			return
		if remember:
			self._lastChartKind = offer.key
		if not mode.enter(drawing):
			say(mode.lastError or _("The drawing could not be shown"))
			return
		say(mode.describe())

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Draw the picture here"),
		category=SCRIPT_CATEGORY,
		gestures=["br(brlMultilineMonarch):space+dot1+dot4+dot7"],
	)
	def script_drawPicture(self, gesture):
		"""Put whatever the reader is pointing at on the pins as a tactile picture.

		The navigator object, whatever its role, copied off the screen. A web image whose
		address the browser gives is loaded from its file instead, in the background, which
		gives full size and works with the screen curtain on; if it will not load, the screen
		copy is used.
		"""
		obj = None
		try:
			obj = api.getNavigatorObject()
		except Exception:
			log.debugWarning("BrlMultiline: no navigator object to draw", exc_info=True)
		address = imageSource.addressOf(obj) if imagePins.hasPillow() else None
		if address is None:
			self._cancelPictureRequest()
			self._drawPicture(again=False)
			return
		# Only the last press counts. A reader who presses again, or moves on and presses on
		# another picture, while a slow file is still loading must not have the first one
		# arrive afterwards and replace what they asked for since.
		request = object()
		self._pictureRequest = request

		def arrived(picture, why):
			# The request is cancelled at shutdown too; checking both is for a file that arrives
			# while the plugin is part way through coming down.
			if self._terminated or self._pictureRequest is not request:
				return
			self._pictureRequest = None
			if picture is not None:
				self._drawPicture(again=False, capture=lambda: picture)
				return
			log.debug(f"BrlMultiline: a picture's own file did not load, copying the screen: {why}")
			self._drawPicture(again=False, capture=lambda: imageSource.captureNavigator(obj))

		imageSource.fetchPicture(address, imageSource.nameFor(obj), arrived)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Draw the picture on the clipboard"),
		category=SCRIPT_CATEGORY,
	)
	def script_drawClipboardPicture(self, gesture):
		"""Draw an image copied to the clipboard, or an image file copied in File Explorer.

		Works with the screen curtain on and gives the image at its own size. Unbound by
		default.
		"""
		# A newer request than any picture file still loading, which must not land on top.
		self._cancelPictureRequest()
		self._drawPicture(again=False, capture=lambda: imageSource.captureClipboard())

	def _cancelPictureRequest(self) -> None:
		"""Forget a picture file still loading, so it is never drawn when it arrives.

		Only the last request counts, so every other drawing command and `terminate` call
		this. Also stops a page layout still being gathered for an overview.
		"""
		self._pictureRequest = None
		if self._layoutCollector is not None:
			self._layoutCollector.cancel()
			self._layoutCollector = None

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Draw only the colour last touched on a picture, or all colours again"),
		category=SCRIPT_CATEGORY,
	)
	def script_pictureOnlyColour(self, gesture):
		"""Redraw the picture with only the colour under the last routing press, or undo that.

		Draws that colour alone in the current style; pressed again, every colour comes back.
		Keeps the reader's zoom and place, as a change of style does.
		"""
		mode = self.graphicsMode
		picture = self._picture
		if picture is None or not mode.active or mode.source is not self._pictureDrawing:
			ui.message(_("There is no picture to change"))
			return
		if picture.derivedFrom is not None:
			self._drawPicture(
				again=True,
				picture=picture.derivedFrom,
				# Translators: reported when a picture will not draw with all its colours back at
				# the part of it the reader has magnified.
				cannotHere=_("All the colours will not draw the part you are on"),
			)
			return
		if picture.colour is None:
			# Translators: reported when only one colour of a picture was asked for, and the
			# picture was captured without its colours, which needs the Pillow library.
			ui.message(_("This picture has no colours to choose from; that needs the Pillow library"))
			return
		if picture.touched is None:
			# Translators: reported when only one colour of a picture was asked for before any
			# colour had been touched. Says how to choose one.
			ui.message(_("Press a routing key on the colour you want first"))
			return
		paper = picture.paper
		fromPaper = max(abs(a - b) for a, b in zip(picture.touched, paper)) if paper is not None else 255
		if fromPaper < imagePins.INK_FROM_PAPER:
			# The background colour: the press almost certainly missed.
			# Translators: reported when only one colour of a picture was asked for and the colour
			# last touched is the background. Says how to choose a colour that is drawn.
			ui.message(_("That is the background; press a routing key on a line or shape first"))
			return
		chosen = picture.ofOneColour(picture.touched, picture.name)
		if chosen is None:
			ui.message(_("The picture could not be drawn, see the log"))
			return
		self._drawPicture(
			again=True,
			picture=chosen,
			# Translators: reported when one colour of a picture will not draw at the part of it
			# the reader has magnified, usually because that colour is not there.
			cannotHere=_("That colour will not draw the part you are on"),
		)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Change how a picture is drawn"),
		category=SCRIPT_CATEGORY,
	)
	def script_pictureStyle(self, gesture):
		"""Draw the same picture the other way, without copying the screen again.

		Cycles through outlines, single lines, brightness and brightness reversed, since which
		reads best depends on the picture. Uses the pixels kept from the first press. See
		`_picture`.
		"""
		self._choosePictureStyle(imageFigure.nextStyle(*self._pictureStyle))

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Draw the picture as outlines"),
		category=SCRIPT_CATEGORY,
	)
	def script_pictureOutlines(self, gesture):
		self._choosePictureStyle(imageFigure.OUTLINES)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Draw the picture as single lines"),
		category=SCRIPT_CATEGORY,
	)
	def script_pictureLines(self, gesture):
		self._choosePictureStyle(imageFigure.LINES)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Draw the picture by brightness"),
		category=SCRIPT_CATEGORY,
	)
	def script_pictureBrightness(self, gesture):
		self._choosePictureStyle(imageFigure.SILHOUETTE)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Draw the picture by brightness, reversed"),
		category=SCRIPT_CATEGORY,
	)
	def script_pictureReversed(self, gesture):
		self._choosePictureStyle(imageFigure.REVERSED)

	def _choosePictureStyle(self, style: tuple) -> None:
		"""Draw the picture up in one style, the one the cycling key or a direct key asked for.

		There is a key for each style as well as the cycling one.

		:param style: one of `image.STYLES`.
		"""
		mode = self.graphicsMode
		if self._picture is None or not mode.active or mode.source is not self._pictureDrawing:
			# Whether the held picture is what is being read. See `_pictureDrawing`.
			# Translators: reported when the style key was pressed and no picture is up.
			ui.message(_("There is no picture to change"))
			return
		if tuple(style) == tuple(self._pictureStyle):
			# Already drawn that way. Composing it again would cost a moment and change nothing
			# under the hand, so this only says what is there.
			ui.message(mode.describe())
			return
		# Passed in rather than set here, and kept only if it draws, so a refused style is not
		# left chosen.
		self._drawPicture(again=True, style=tuple(style))

	def _drawPicture(
		self,
		again: bool,
		capture=None,
		picture=None,
		cannotHere: "str | None" = None,
		style: "tuple | None" = None,
	) -> None:
		"""Capture if needed, compose, and put a picture on the display.

		The size is read here, since the drawing's rectangle can change between presses.

		:param again: whether to re-use the pixels already captured rather than copying the
			screen. True for a change of style, False for a new picture.
		:param capture: where a new picture comes from, `imageSource.captureNavigator` if None.
		:param picture: a picture to draw in place of the one held, made from it -- one colour
			of it, or the original back again. Drawn keeping the reader's place.
		:param cannotHere: what to say if that keeps the place and the place will not draw.
		:param style: a style to draw in, kept as the style only if the drawing is shown. The
			style already chosen if None.
		"""
		if style is None:
			style = self._pictureStyle
		mode = self.graphicsMode
		# Whatever the reader has already decided about the braille line beside the drawing.
		# Asking for the default instead would put the line back on a panel they had given
		# whole to the picture, and shrink the drawing, while claiming to change its style.
		# None for a first drawing, which the mode decides: see `GraphicsMode.openingTextLines`.
		textLines = mode.textLines if mode.active else None
		size = mode.drawingSize(textLines)
		if size is None:
			# Translators: reported when a drawing was asked for on a display that cannot draw.
			ui.message(_("This display cannot show graphics"))
			return
		# Held locally until the whole of this has worked. Assigned straight to `self._picture`
		# it went wrong quietly: capture B succeeds, composing or claiming for B fails, picture
		# A is still on the display -- and the cached pixels are now B while the drawing and
		# the mode still hold A. The style key checks that the drawing on the display is the
		# last picture drawing, which it is, so the next press would draw B over A.
		captured = picture if picture is not None else self._picture
		try:
			if not again or captured is None:
				captured = capture() if capture is not None else imageSource.captureNavigator()
			drawing = imageFigure.figureFor(
				mode.newBuffer,
				captured,
				size[0],
				size[1],
				style[0],
				style[1],
			)
		except imagePins.ImageRefused as refusal:
			# Every refusal here names what went wrong — too small, not on the screen, nothing
			# in it to feel — because a reader told only that it failed cannot tell whether to
			# move, to change the style, or to give up.
			ui.message(str(refusal))
			return
		except Exception:
			log.error("BrlMultiline: could not draw the picture", exc_info=True)
			# Translators: reported when drawing a picture failed for a reason worth a log entry.
			ui.message(_("The picture could not be drawn, see the log"))
			return
		# A change of style on the picture already up keeps the reader where they were. Going
		# through `enter` would reset the zoom and both origins, so switching styles to compare
		# them would move the reader off the part they were comparing.
		# One colour of a picture is the same pixels and starts from the same box, so it keeps
		# the place as a change of style does.
		keepingPlace = (
			again
			and mode.active
			and self._picture is not None
			and _pictureRoot(captured) is _pictureRoot(self._picture)
			and mode.source is self._pictureDrawing
			and self._pictureDrawing is not None
		)
		if keepingPlace:
			if not mode.replaceSource(drawing):
				ui.message(
					cannotHere
					# Translators: reported when a picture will not draw in the style just asked
					# for, at the part of it the reader has magnified.
					or _("This style will not draw the part you are on"),
				)
				return
		elif not mode.enter(drawing, textLines=textLines):
			ui.message(mode.lastError or _("The drawing could not be shown"))
			return
		# Committed together, so the pixels and the drawing made from them can never be two
		# different pictures.
		self._picture = captured
		self._pictureDrawing = drawing
		self._pictureStyle = tuple(style)
		ui.message(mode.describe())

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
		self._cancelPictureRequest()
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
		"""Draw the main areas of the web page on screen as labelled boxes in their places.

		An outline for each area with a short label or key letter, and a press inside a box
		names the area. See docs/design/layout-overview-plan.md.

		Outside a web page in browse mode, it draws the window the focus is in instead: its
		toolbars, ribbon, panes, lists and status bar, from UI Automation. See `appRegions`.

		Unbound by default, like the other drawings that are not used while reading.
		"""
		self._layoutOverview("screen")

	@script(
		# Translators: input help message for a command.
		description=_("Overview: Draw the layout of the whole page"),
		category=SCRIPT_CATEGORY,
	)
	def script_layoutOverviewPage(self, gesture):
		"""The same overview for the whole page, top to bottom, rather than the screen's worth.
		In an application, where there is no page to scroll, the same as the window's."""
		self._layoutOverview("page")

	@script(
		# Translators: input help message for a command.
		description=_("Overview: Show the layout of the page, or of the window, as text"),
		category=SCRIPT_CATEGORY,
	)
	def script_layoutOverviewText(self, gesture):
		"""The page's main areas and where they are, as lines of text in a window.

		For a display that cannot draw, and for speech: "banner: across the top", "search: top
		right", "navigation: down the left side". Also what the drawing commands fall back to
		on a display without graphics.
		"""
		self._layoutOverview("text")

	def _layoutOverview(self, view: str) -> None:
		"""Gather the focused page's layout a batch at a time, then draw or show it.

		Gathering takes a second or more on a busy page, so it is done in batches with NVDA
		free to speak between them, and only the last request counts: another overview, a
		chart, a picture or the drawing turned off in the meantime cancels it.
		"""
		self._cancelPictureRequest()
		document = getattr(api.getFocusObject(), "treeInterceptor", None)
		if document is None or not hasattr(document, "_iterNodesByType") or not hasattr(document, "VBufHandle"):
			self._appLayoutOverview(view)
			return
		if not getattr(document, "isReady", True):
			# Translators: reported when the layout overview is asked for while a page loads.
			ui.message(_("The page is still loading"))
			return
		if view != "text" and self.graphicsMode.drawingSize() is None:
			view = "text"
		collector = None

		def gathered(payload, why):
			if self._terminated or self._layoutCollector is not collector:
				return
			self._layoutCollector = None
			if payload is None:
				log.debug(f"BrlMultiline: no page layout: {why}")
				# Translators: reported when the layout of a web page could not be worked out.
				ui.message(_("Could not read the layout of this page"))
				return
			self._showLayout(payload, view, document)

		collector = layoutCollect.Collector(document, gathered)
		self._layoutCollector = collector
		# Translators: reported while the add-on reads where things are on a web page.
		ui.message(_("Reading the page layout"))
		collector.start()

	def _appLayoutOverview(self, view: str) -> None:
		"""Gather the layout of the window the focus is in, a batch at a time, then draw or show it.

		The same overview as a web page's, from UI Automation rather than browse mode: see
		`appCollect` and `appRegions`. A window has no page to scroll, so its whole is its screen.
		"""
		window = api.getForegroundObject()
		if window is None or not getattr(window, "windowHandle", None):
			# Translators: reported when the layout overview is asked for with no window to read.
			ui.message(_("No window to read the layout of"))
			return
		if view != "text" and self.graphicsMode.drawingSize() is None:
			view = "text"
		collector = None

		def gathered(payload, why):
			if self._terminated or self._layoutCollector is not collector:
				return
			self._layoutCollector = None
			if payload is None:
				log.debug(f"BrlMultiline: no window layout: {why}")
				# Translators: reported when the layout of an application window could not be read.
				ui.message(_("Could not read the layout of this window"))
				return
			self._showLayout(payload, view)

		collector = appCollect.AppCollector(window, api.getFocusObject(), gathered)
		self._layoutCollector = collector
		# Translators: reported while the add-on reads where things are in an application window.
		ui.message(_("Reading the window layout"))
		collector.start()

	def _showLayout(self, payload: dict, view: str, document=None) -> None:
		"""Work out the areas from what was gathered, a page's or a window's, and draw or show them."""
		app = payload.get("kind") == "uia"
		try:
			if app:
				page = appRegions.findAppRegions(
					payload["window"], payload.get("windowRect"), (payload.get("focus") or {}).get("path")
				)
				viewport = page.box
				# A window does not scroll: its whole is what is on screen.
				view = "screen" if view == "page" else view
			else:
				root, viewport = layoutRegions.fromProbe(payload)
				page = layoutRegions.findRegions(root, viewport)
		except Exception:
			log.error("BrlMultiline: could not work out the layout", exc_info=True)
			if app:
				ui.message(_("Could not read the layout of this window"))
			else:
				ui.message(_("Could not read the layout of this page"))
			return
		# Any limit reached other than sampling, which is normal on a busy page, means part of the
		# page was not read, and an overview of part of a page must not pass for the whole.
		partial = any(why != "sampled" for why in payload.get("incomplete", ()))
		if app:
			# Translators: said after the layout of an application window when only part of it
			# could be read in the time allowed, such as a slow older application.
			partialNote = _("Only part of the window could be read")
		else:
			# Translators: said after the layout of a web page when only part of it could be read
			# in the time allowed, such as a very long or slow page.
			partialNote = _("Only part of the page could be read")
		if view == "text":
			lines = layoutRegions.describe(page, 3)
			if not lines:
				if app:
					# Translators: reported when no areas were found in an application window.
					ui.message(_("No areas found in this window"))
				else:
					# Translators: reported when no areas were found on a web page.
					ui.message(_("No areas found on this page"))
				return
			if partial:
				lines.append(partialNote)
			if app:
				# Translators: the title of the window showing an application window's layout as
				# text.
				title = _("Window layout")
			else:
				# Translators: the title of the window showing a web page's layout as text.
				title = _("Page layout")
			ui.browseableMessage("\n".join(lines), title=title)
			return
		mode = self.graphicsMode
		size = mode.drawingSize()
		if size is None:
			ui.message(_("This display cannot show graphics"))
			return
		state = _LayoutView(page, view, viewport, document, payload.get("identity"), app=app)
		message = self._enterLayout(state)
		if message is None:
			return
		self._layoutView = state
		if partial:
			message = f"{message}. {partialNote}"
		ui.message(message)

	def _enterLayout(self, state: "_LayoutView") -> Optional[str]:
		"""Draw the overview for where the reader is in it and put it up.

		:return: what to say, or None when it could not be drawn, the reason having been said.
		"""
		mode = self.graphicsMode
		size = mode.drawingSize()
		if size is None:
			ui.message(_("This display cannot show graphics"))
			return None
		if state.focus is not None:
			# Translators: the name of the drawing of one area of a web page's layout, drilled
			# into. The placeholder is the area's name, such as "navigation, Main".
			name = _("layout of {area}").format(area=state.focus.title)
		elif state.app:
			# Translators: the name of the drawing of an application window's layout.
			name = _("layout of the window")
		elif state.view == "page":
			# Translators: the name of the drawing of a whole web page's layout.
			name = _("layout of the whole page")
		else:
			# Translators: the name of the drawing of the layout of the part of a web page on
			# screen.
			name = _("layout of the page on screen")
		drawing = self._layoutDrawing(
			state.page,
			state.view,
			state.viewport,
			size,
			name,
			state.document,
			focus=state.focus,
			state=state,
		)
		if drawing is None or not mode.enter(drawing):
			ui.message(mode.lastError or _("The drawing could not be shown"))
			return None
		state.drawing = drawing
		# The key to the letters on the panel is the drawing's note, which this includes.
		return mode.describe()

	def _layoutUp(self) -> Optional["_LayoutView"]:
		""":return: the overview of a page's layout, if that is what is on the display."""
		state = self._layoutView
		mode = self.graphicsMode
		if state is None or not mode.active or mode.source is not state.drawing:
			# Translators: said when drilling into a page's layout is asked for with no layout
			# on the display.
			ui.message(_("No page layout is shown"))
			return None
		return state

	@script(
		# Translators: input help message for a command.
		description=_("Overview: Show what is inside the area last pressed in a page's layout"),
		category=SCRIPT_CATEGORY,
	)
	def script_layoutDrillIn(self, gesture):
		"""Draw the area last pressed as the whole drawing, with the areas inside it.

		Positions are then said within that area. Dot 8 in the Monarch's graphics layer.
		"""
		state = self._layoutUp()
		if state is None:
			return
		region = state.pressed
		if region is None:
			# Translators: said when drilling into a page's layout is asked for before any area
			# has been pressed, since the drill goes into the area last pressed.
			ui.message(_("Press a routing key in an area first"))
			return
		if not region.children:
			# Translators: said when the area asked to be drilled into has no areas inside it.
			# The placeholder is the area's name.
			ui.message(_("Nothing inside {area}").format(area=region.title))
			return
		before = (state.focus, list(state.trail), state.pressed)
		state.trail.append(state.focus)
		state.focus, state.pressed = region, None
		message = self._enterLayout(state)
		if message is None:
			state.focus, state.trail, state.pressed = before
			return
		ui.message(message)

	@script(
		# Translators: input help message for a command.
		description=_("Overview: Go back out to the area around, in a page's layout"),
		category=SCRIPT_CATEGORY,
	)
	def script_layoutBackOut(self, gesture):
		"""Draw the area around the one drilled into, or the whole page or screen. Dot 7 on the
		Monarch in the graphics layer, as backspace goes back."""
		state = self._layoutUp()
		if state is None:
			return
		if state.focus is None:
			# Translators: said when backing out of a page's layout is asked for and the whole
			# page, or the whole screen, is already shown.
			ui.message(_("Already showing all of it"))
			return
		before = (state.focus, list(state.trail), state.pressed)
		# Came out of this area, so a drill straight back in returns to it.
		state.pressed = state.focus
		state.focus = state.trail.pop() if state.trail else None
		message = self._enterLayout(state)
		if message is None:
			state.focus, state.trail, state.pressed = before
			return
		ui.message(message)

	def _layoutDrawing(
		self,
		page,
		view: str,
		viewport,
		size: tuple,
		name: str,
		document=None,
		focus=None,
		state: Optional["_LayoutView"] = None,
	):
		"""The drawing of a page's layout, which draws itself again when zoomed or moved.

		1. The page is drawn tall, a screen of it to a panel's height, opening at its top. See
		   `Drawing.opensAcross`.
		2. Zooming and moving compose the view again through `Drawing.redraw`, so labels stay
		   at braille size and areas keep their key letters.
		3. A second press in a box moves the browse mode cursor there. See `_moveToArea`.

		:return: the drawing, or None.
		"""
		mode = self.graphicsMode
		width, height = size
		keys: dict = state.keys if state is not None else {}
		style = _layoutCountStyle()
		whole = layoutDraw.focusWindow(page, view, viewport, focus)
		if view == "page":
			screens = whole.height / max(viewport.height, 1)
			source = layoutDraw.compose(
				mode.newBuffer,
				page,
				width,
				max(height, round(height * screens)),
				view,
				viewport,
				graphicsMode.labelCells,
				LAYOUT_DEPTH,
				keys=keys,
				focus=focus,
				style=style,
			)
		else:
			# The screen as tall as it has to be for everything on it to be drawn, rather than
			# squeezed onto one panel with areas left out. See `layoutDraw.composeFitting`.
			source = layoutDraw.composeFitting(
				mode.newBuffer,
				page,
				width,
				height,
				view,
				viewport,
				graphicsMode.labelCells,
				LAYOUT_DEPTH,
				keys=keys,
				focus=focus,
				style=style,
			)
		if source is None:
			return None

		def describer(overview):
			# A press says what is there, and remembers the area, which a drill goes into.
			def describeAt(x, y):
				box = overview.boxAt(x, y)
				if state is not None and box is not None:
					state.pressed = box.region
				return overview.describeAt(x, y)

			return describeAt

		identity = state.identity if state is not None else _pageIdentity(document)

		def activator(overview):
			app = state is not None and state.app

			def activateAt(x, y):
				box = overview.boxAt(x, y)
				if box is None:
					return
				if app:
					self._moveToAppArea(box.region, identity)
				else:
					self._moveToArea(document, box.region, identity)

			return activateAt

		def areaOf(overview):
			# The region itself is the area, whatever view it is drawn in, so a press in the
			# sidebar and the next one a cell away, or after a move, are on the same area.
			def areaAt(x, y):
				box = overview.boxAt(x, y)
				return None if box is None else id(box.region)

			return areaAt

		def redraw(offset, span, pinWidth, pinHeight, top=0.0, down=1.0):
			window = source.windowAt(offset, span, top, down)
			part = layoutDraw.compose(
				mode.newBuffer,
				page,
				pinWidth,
				pinHeight,
				view,
				viewport,
				graphicsMode.labelCells,
				LAYOUT_DEPTH,
				window=window,
				keys=keys,
				focus=focus,
				style=style,
			)
			if part is None:
				return None
			key = part.keyText()
			return graphicsMode.Drawing(
				part.buffer,
				name=name,
				describeAt=describer(part),
				activateAt=activator(part),
				areaAt=areaOf(part),
				# Translators: the key to the letters on the part of a page's layout that is on
				# the display. The placeholder is a list of letters with what each stands for.
				note=_("Key: {key}").format(key=key) if key else "",
			)

		sourceKey = source.keyText()
		notes = [_("Key: {key}").format(key=sourceKey)] if sourceKey else []
		if source.unreadable:
			notes.append(
				# Translators: said after a drawing of a web page's layout when some areas on it
				# could not be drawn big enough to find, even with the drawing grown taller. The
				# placeholder is how many.
				_("{count} areas too small to show; magnify to see them").format(count=source.unreadable),
			)
		drawing = graphicsMode.Drawing(
			source.buffer,
			name=name,
			describeAt=describer(source),
			activateAt=activator(source),
			areaAt=areaOf(source),
			note=". ".join(notes),
			redraw=redraw,
			# How far in the zoom can go, as the mode counts it: a window no smaller than about
			# 200 pixels of the page, past which there is nothing more to see in a layout.
			points=max(1, round(whole.height * graphicsMode.MIN_WINDOW_POINTS / LAYOUT_SMALLEST_VIEW)),
			windowsVertically=True,
			# A view taller than the panel opens at its top, a panel wide, and is read by moving
			# down it with the panning keys.
			opensAcross=source.buffer.height > height,
		)
		return drawing

	def _moveToArea(self, document, region, identity: Optional[tuple] = None) -> None:
		"""Put the browse mode cursor where an area of the page starts, and leave the drawing.

		Like quick navigation: the area's name is said, then the line there. The drawing comes
		down only when its display also shows the focus.

		The start is found again by the area's first element (`Region.anchor`), since browse
		mode can re-read a page under the same document; an offset is used only while the buffer
		is unchanged. Refused, with a reason, when the area has no place in the text, the page
		has changed, or the element is gone.

		:param document: the browse mode document the layout was read from.
		:param region: the area pressed.
		:param identity: the page and buffer the layout was read from; see `_pageIdentity`.
		"""
		import controlTypes
		import speech
		import textInfos
		from textInfos.offsets import Offsets

		if region.start is None:
			# Translators: said when a reader asks to move to an area of a page's layout that
			# has no place in the page's text to move to. The placeholder is the area's name.
			ui.message(_("No place in the page to move to for {area}").format(area=region.title))
			return
		# Translators: said when a reader asks to move to an area of a page's layout, but the
		# page has changed since its layout was read, so the layout no longer fits it.
		changed = _("The page has changed; read its layout again")
		current = getattr(api.getFocusObject(), "treeInterceptor", None)
		if document is None or current is not document or not getattr(document, "isAlive", True):
			ui.message(changed)
			return
		page, buffer = identity or (None, None)
		pageNow, bufferNow = _pageIdentity(document)
		if page is not None and pageNow != page:
			ui.message(changed)
			return
		start = region.start
		anchor = getattr(region, "anchor", None)
		try:
			if anchor is not None:
				# Its first element, wherever it now is; gone, the lookup says so.
				probe = document.makeTextInfo(textInfos.POSITION_FIRST)
				start = probe._getOffsetsFromFieldIdentifier(*anchor)[0]
			elif buffer is not None and bufferNow != buffer:
				# No element to find again, and a different reading of the page: the offset
				# belongs to that other text.
				ui.message(changed)
				return
			info = document.makeTextInfo(Offsets(start, start))
		except Exception:
			log.debugWarning("BrlMultiline: an area's start is not in the page now", exc_info=True)
			ui.message(changed)
			return
		if self.graphicsMode.holdsFocus():
			self._cancelPictureRequest()
			self.graphicsMode.leave()
		if getattr(document, "passThrough", False):
			import browseMode

			document.passThrough = False
			browseMode.reportPassThrough(document)
		document._set_selection(info, reason=controlTypes.OutputReason.QUICKNAV)
		speech.speakMessage(region.title)
		line = info.copy()
		line.expand(textInfos.UNIT_LINE)
		speech.speakTextInfo(line, reason=controlTypes.OutputReason.QUICKNAV)

	def _moveToAppArea(self, region, identity: Optional[tuple] = None) -> None:
		"""Move to an area of an application window: the focus to it, or to the first thing in it
		that can take the focus, or the navigator object to it when nothing in it can.

		The application counterpart of `_moveToArea`, moving as F6 between panes does. Found
		again by the element's runtime id, and refused, with a reason, when the window is no
		longer in front or the element is gone.

		:param region: the area pressed, whose `anchor` is its element's runtime id.
		:param identity: the window the layout was read from, as `[window handle]`.
		"""
		import controlTypes
		import speech

		# Translators: said when a reader asks to move to an area of a window's layout, but the
		# window has changed since its layout was read, so the layout no longer fits it.
		changed = _("The window has changed; read its layout again")
		# Translators: said when a reader asks to move to an area of a window's layout while
		# another window is in front.
		notInFront = _("That window is no longer in front")
		hwnd = identity[0] if identity else None

		def inFront() -> bool:
			# Asked of Windows, not of NVDA's foreground object, which is only brought up to date by
			# events this thread cannot take while it is waiting on the lookups below.
			return bool(hwnd) and appCollect.foregroundWindow() == hwnd

		if not inFront():
			ui.message(notInFront)
			return
		anchor = getattr(region, "anchor", None)
		element = appCollect.findElement(hwnd, anchor) if anchor else None
		record = getattr(region, "record", None) or {}
		if element is None and record.get("hwnd"):
			# A window of its own, found by its handle when its runtime id finds nothing.
			element = appCollect.elementOfWindow(record["hwnd"], hwnd)
		if element is None:
			ui.message(changed)
			return
		target = appCollect.focusableWithin(element)
		if not inFront():
			# The lookups can take long enough for the reader to switch windows; then the move
			# is stale.
			ui.message(notInFront)
			return
		if self.graphicsMode.holdsFocus():
			self._cancelPictureRequest()
			self.graphicsMode.leave()
		speech.speakMessage(region.title)
		if target is not None:
			try:
				# NVDA reports the focus when it arrives, as for any move of the focus.
				target.SetFocus()
				return
			except Exception:
				log.debugWarning("BrlMultiline: could not focus an area", exc_info=True)
		try:
			import UIAHandler
			from NVDAObjects.UIA import UIA

			obj = UIA(UIAElement=element.BuildUpdatedCache(UIAHandler.handler.baseCacheRequest))
		except Exception:
			log.debugWarning("BrlMultiline: could not make an object of an area", exc_info=True)
			ui.message(changed)
			return
		api.setNavigatorObject(obj)
		speech.speakObject(obj, reason=controlTypes.OutputReason.QUERY)

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
				said += self._captureSizeWords(mode)
		ui.message(said)

	def _captureSizeWords(self, mode) -> str:
		""":return: how big the capture was, when that is what answered the reader question.

		Said on a refused zoom of a picture, since what limits zoom is how many screen pixels
		were captured. Empty unless the drawing is the captured picture.
		"""
		if self._picture is None or mode.source is not self._pictureDrawing:
			return ""
		size = imageSource.sizeWords(self._picture)
		if not size:
			return ""
		# Translators: added when a picture will not magnify any further, saying how many
		# screen pixels were captured, which is what limits it. The placeholder is a size
		# such as "160 by 24".
		return ", " + _("captured at {size}").format(size=size)

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


def _columnName(column) -> str:
	""":return: what to call one column of a table when saying which ones are showing.

	Its heading, or failing that "column" and its number.

	:param column: the column, as the layout planned it.
	"""
	if column.label:
		return column.label
	# Translators: how a column with no heading of its own is named when a table's columns
	# are reported. The placeholder is which column of the table it is.
	return _("column {index}").format(index=column.index)


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
