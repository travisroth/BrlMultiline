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

from typing import NamedTuple

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

from . import bmConfig, panning, patches, tableArrows
from .container import DisplayContainer
from .flowTableSource import wantsColumns
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

BREAK = chr(10)
"""A line break in a dialog's text, spelled so no escape has to survive a shell."""

_plugin = None


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

	What it was matters as much as where it went. A pin on eight rows is read as a flow and
	a pin on one row cannot be, so the same swap can leave a chat that scrolled showing a
	single unscrollable line — silently, until this said so.
	"""

	monitor: ObjectMonitor
	home: str
	"""The segment it was filed under. What it ends up on is settled by the rebuild."""

	wasFlowing: bool


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

		self._pinsInFlight: list[ObjectMonitor] = []
		"""Pins a focus move is carrying, until the display it caused has settled.

		A pin displaced by the focus is moved before the display is rebuilt, and the segment
		it is moved to may not be in the display that gets built: the flow band claims the
		display the focus is on, so the rows the focus leaves come back as segments of the
		reader's own layout under different keys. Without this the carry-over saw a pin on a
		key that had gone and released it — the reader traded the pin for nothing.

		The monitors themselves rather than the keys they are filed under, because one move
		rebuilds the display more than once: claiming or releasing the band rebuilds it
		again, so a pin can be re-homed by the first pass and have to survive the second
		under a key nobody predicted.

		Held only across the rebuilding one move causes, so that a pin released later for an
		ordinary reason is still released.
		"""
		self._messageBuffer: MessageBuffer | None = None
		"""NVDA's flash messages, confined to one segment. Made on the first rebuild.

		Made once and retargeted, never replaced: the handler decides whether a message is
		showing by comparing `buffer is messageBuffer`, so swapping the object under it would
		leave a message that could not be dismissed. See L{_installMessageBuffer}.
		"""
		self._originalMessageBuffer = None
		"""NVDA's own, kept to be put back on termination."""
		self._rebuildPending = False
		self._reportedDivergence: tuple[tuple[str, ...], tuple[str, ...]] | None = None
		"""The last mismatch between the configured members and the ones the composite was
		opened for, so that it is said once rather than on every profile switch."""
		self._terminated = False
		"""Set by L{terminate}, so that work already queued does not run afterwards."""
		self._activeView: SegmentView | None = None
		"""A view installed by code rather than by the settings, if any.

		While this is set it takes over the display wholesale; clearing it returns to the
		view the user configured.
		"""
		self.flowBand = None
		"""The flow claiming part of the display, or None. See L{_applyFlow}."""

		self.tableWanted = None
		"""The table the reader has asked to see in columns, as a document and an identifier.

		Held here rather than on the band because a one row display has no band and the reader
		still wants to ask — see `flowBand.MIN_BAND_ROWS`. `FlowBand.tableWanted` reads and
		writes this, so there is one answer whether or not a band is showing it.
		"""

		self.tableRefused = None
		"""A table whose saved layout the reader has just turned off, for as long as they are
		in it.

		A saved layout applies itself the moment the reader arrives, so turning it off has to
		mean something for longer than the redraw that follows — otherwise the command drops
		the request and the next redraw puts it straight back, which is a toggle that does
		nothing. Cleared when they leave the table, since coming back to it is asking again.
		See `FlowBand._refusedTable`, and `script_forgetTableLayout` for the way to make it
		stop for good.
		"""

		self._applyingFlow = False
		"""Guards L{_applyFlow} against itself: claiming the band rebuilds the display."""

		self.graphicsMode = GraphicsMode(self)
		"""The drawing on the display, and the zoom and origin that make it readable.

		Built whether or not the display can draw: it answers `active` as False and reports
		why when asked to show something, which is a better failure than a missing attribute
		on hardware that has no pins to raise.
		"""

		self._activePanels: list[BraillePanel] = []
		"""Claims laid over whatever view is in force, in the order they were made.

		Kept across rebuilds and re-composed each time, so that a claim survives a settings
		change or a display swap. A claim that no longer fits is dropped and reported.
		"""
		bmConfig.initialize()
		patches.install()
		# Which display's keys are being pressed, for the per display panning direction.
		panning.install()
		# The arrow keys inside a browse mode table, which is a way of reading rather than a
		# way of displaying and so is not waited on a flow. See `tableArrows`.
		tableArrows.install()
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(BrailleMultilineSettingsPanel)
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(FlowSettingsPanel)
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(VirtualDisplaySettingsPanel)
		displaySizeChanged.register(self._handleDisplayChanged)
		displayChanged.register(self._handleDisplayChanged)
		# Settings are read through `config.conf`, which is profile aware, so switching
		# profile can change the layout, the focus segment and the panning direction
		# without any braille event firing.
		config.post_configProfileSwitch.register(self._handleProfileSwitch)
		self._membersChanged = devicesModule.membersChangedAction()
		"""The composite display's own notification, or None if the driver cannot be reached.

		NVDA has nothing that means what this means. A member lost and reconnected leaves the
		display exactly as big as it was, so `displaySizeChanged` never fires, while the
		segments here are named after physical displays and have to be built again.
		"""
		if self._membersChanged is not None:
			self._membersChanged.register(self._handleDisplayChanged)
		self.rebuildBuffer()

	def terminate(self):
		# Set first, so that anything already queued sees it even if the teardown below
		# raises part way through.
		self._terminated = True
		try:
			displaySizeChanged.unregister(self._handleDisplayChanged)
			displayChanged.unregister(self._handleDisplayChanged)
			config.post_configProfileSwitch.unregister(self._handleProfileSwitch)
			if self._membersChanged is not None:
				self._membersChanged.unregister(self._handleDisplayChanged)
			if self.flowBand is not None:
				self.flowBand.onTerminate()
				self.flowBand = None
			self.graphicsMode.onTerminate()
			self.stopAllMonitoring()
			self._restoreOriginalBuffer()
			patches.remove()
			panning.remove()
			tableArrows.remove()
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(BrailleMultilineSettingsPanel)
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(FlowSettingsPanel)
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(VirtualDisplaySettingsPanel)
		except Exception:
			log.error("Error while terminating BrlMultiline", exc_info=True)
		finally:
			global _plugin
			_plugin = None
			super().terminate()

	# Events

	def event_gainFocus(self, obj, nextHandler):
		"""Let the focus through, then finish anything that was waiting for it.

		The one event this add-on handles, and it is here for one thing: routing into a
		column of a list view. The cell cannot take the focus, so the row is focused and the
		navigator object is taken the rest of the way — and NVDA moves the navigator object
		to whatever takes the focus, after the focus has arrived, which is after the routing
		key has finished. The column asked for was set and then quietly undone.

		After `nextHandler` rather than before, so that everything NVDA does with a new focus
		has been done before the column is put back. Costs a `None` test on every focus
		change and nothing else. See `flowObjectTable.columnWantedAfterFocus`.
		"""
		nextHandler()
		try:
			from . import flowObjectTable

			flowObjectTable.columnWantedAfterFocus(obj)
		except Exception:
			log.debugWarning("Could not finish going to a table column", exc_info=True)

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
		:raises ValueError: if the view's panels do not tile the display, or a segment reaches
			cells that no display behind a composite one actually has.
		:raises LookupError: if the view's focus segment does not exist.
		"""
		numRows, numCols = self._displayDimensions()
		# Validate before storing, so a view that does not fit leaves the display as it was.
		# A view arrives whole and bypasses `deviceView`, so it is the one place a caller can
		# put a segment over cells that reach no hardware without being told.
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
		:raises ValueError: if a claim does not fit, or would put a segment over cells that no
			display behind a composite one has.
		:raises LookupError: if a claim takes the focus segment without replacing it.
		"""
		devices = deviceMap()
		view = self._baseView(numRows, numCols, devices)
		for panel in _byPriority(panels):
			view = view.withPanel(panel, numRows, numCols)
		validateAgainstHardware(view, devices, numCols)
		return view

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

		Claims are re-composed on every rebuild rather than being baked into a stored view,
		so that a settings change or a display swap is answered by rebuilding the same
		claims against the new base rather than by discarding them.
		"""
		devices = deviceMap()
		view = self._baseView(numRows, numCols, devices)
		kept: list[BraillePanel] = []
		for panel in _byPriority(self._activePanels):
			try:
				candidate = view.withPanel(panel, numRows, numCols)
				# Composed into a candidate rather than into `view`, so that a claim which
				# reaches cells no hardware has is dropped whole rather than leaving the
				# eviction it caused behind — including the eviction of a dead column mask.
				validateAgainstHardware(candidate, devices, numCols)
			except (ValueError, LookupError):
				log.warning(
					f"BrlMultiline: panel {panel.name!r} cannot be shown on "
					f"a {numRows} by {numCols} display; dropping it",
					exc_info=True,
				)
				continue
			view = candidate
			kept.append(panel)
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

		**A drawing outranks the flow, and this is where that is honoured.** The priority
		ladder in `panels` decides who wins the cells, but letting the flow claim rows it is
		going to lose would still cost a claim, an eviction and a teardown on every rebuild —
		and the flow is on most of the time, so that is most rebuilds. So while a figure is up
		the flow simply does not claim, and leaving the figure rebuilds the display and brings
		it straight back.

		Not conditioned on the two wanting the same rows, deliberately. Both default to the
		tallest display — `devices.preferredDevice` with nothing named — so in practice they
		always do, and a rule that reads "while a drawing is up, the flow waits" is one a
		reader can hold in their head.

		Called after every rebuild, which is what makes the setting answer a profile switch.
		NVDA switches profile when the foreground application changes, the switch rebuilds
		the display, and the rebuild arrives here — so a profile for one browser can read as
		a flow while the normal configuration goes on as before, with nothing to press.

		This is also what makes the flow survive a restart. It was a command and nothing
		else until now, which meant a reader who wanted it had to ask for it again after
		every display change, every profile switch and every session.
		"""
		if self._terminated or self._applyingFlow:
			# Claiming the band rebuilds the display, which arrives back here. One pass does it.
			return
		wanted = bmConfig.shouldClaimFlowBand() and not self.graphicsMode.active
		active = self.flowBand is not None
		geometryChanged = False
		if wanted and active:
			# A panel claim carries the rectangle it was activated with. Re-composing that
			# panel after a settings or profile change preserves the old rectangle, so an
			# enabled flow has to be reclaimed when its configured rows or display changed.
			segment = self.flowBand.segment()
			try:
				geometryChanged = segment is None or segment.rect != self.flowBand.bandRect()
			except Exception:
				# Treat an unreadable target as changed and let `startFlow` give the complete
				# account of why the replacement could not be shown.
				log.debugWarning("BrlMultiline: could not compare the flow band geometry", exc_info=True)
				geometryChanged = True
		if wanted == active and not geometryChanged:
			return
		self._applyingFlow = True
		try:
			if geometryChanged:
				self.stopFlow()
				self.startFlow()
			elif wanted:
				self.startFlow()
			else:
				self.stopFlow()
		finally:
			self._applyingFlow = False

	def startFlow(self) -> bool:
		"""Claim a band and read the reader's content as a flow in it.

		:return: whether the band is claimed. It is kept whether or not there is anything to
			flow at this moment, since a band with nothing to show presents the focus as an
			undivided display would and lights up when the reader reaches a document.
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

		Geometry survives a rebuild and content does not: a claim is re-composed and its
		cells come back empty, with nothing telling its owner to redraw them. This is that
		telling, and it is the first use of the lifecycle the claim contract was missing.

		:param container: the container just installed.
		"""
		band = self.flowBand
		if band is None:
			return
		if band.segment() is None:
			band.onEvicted()
			self.flowBand = None
			return
		band.onRebuilt()

	def _carryOverGraphics(self) -> None:
		"""Draw the figure again after a rebuild, or drop it if its claim has gone.

		The same gap `_carryOverFlow` closes, answered differently because a graphics claim
		has no segments to look for. Its claim is its own evidence: the rebuild drops a panel
		that no longer fits, so a claim missing from the active panels is a claim that is gone.
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

		The arrangement to show when the one that should have been shown could not be built.
		For a composite that is one segment per physical display rather than one segment across
		everything, because a segment across everything covers the columns a narrower display
		does not have — and text put there is dropped on the way to the hardware with nothing
		to say where it went. An emergency is a poor moment to start losing text silently.

		Ordinary displays keep the single segment, which is NVDA's own arrangement and cannot
		be wrong about hardware there is only one of.

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

		NVDA's own message buffer wraps to the display's full width and starts at cell 0, so
		on a divided display messages land wherever the top left happens to be, and on a
		composite of displays of unequal width the overhang is written to cells that reach no
		hardware. See `messages`.

		The buffer object is made once and retargeted afterwards, never replaced. The handler
		decides whether a message is showing by comparing `buffer is messageBuffer`, so a
		replacement made while a message was up would leave one that could never be dismissed.

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
				# Either the first install, or something has put a buffer of its own there since.
				# Whatever it is, it is what has to go back on termination — otherwise this
				# restores a buffer that stopped being NVDA's while another add-on's is thrown
				# away. (Not NVDA itself: `messageBuffer` is assigned once, in
				# `BrailleHandler.__init__`, so a rebuilt one comes with a whole new handler.)
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

		NVDA's own dismissal, because a message is not only some cells: `_dismissMessage`
		clears it, returns the display to the main buffer, stops the timeout and tells
		`_post_dismissBrailleMessage`. Doing part of that is what left the display showing an
		empty buffer belonging to nobody — until the next key press moved it on, and with
		messages configured to be shown indefinitely there is no next anything.

		`shouldUpdate=False` because every caller here redraws straight afterwards, from the
		buffer that is by then the right one.

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
		3. Its segment now follows the system focus. `startMonitoring` refuses the focus
			segment for that reason, and a settings change can move the focus onto a
			segment that was pinned when the pin was made. Keeping it would let
			`refreshMonitors` clear the freshly drawn focus content and replace it with the
			pinned object.

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
				# Two reasons to look for somewhere else rather than let go. The display this
				# pin was on has gone; or the focus command moved it a moment ago onto a
				# segment this rebuild has dissolved, which is the flow band handing its rows
				# back as the focus leaves them. Every other way a segment can go — a claim
				# taking it, the focus moving onto it, the user rearranging — is a decision,
				# and a pin released by a decision stays released.
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

		This is what a display disappearing costs, made as small as it can honestly be made. If
		what is left has room — a segment that is not the focus, not claimed by a panel, and not
		already showing something pinned — the object moves there and the reader keeps it. If
		what is left is a single segment, it does not: that segment follows the focus, and
		taking it for a pinned object would leave the reader with a display showing something
		they did not ask to be looking at and no focus at all. Then the pin is dropped, and
		reconnecting or rearranging is the user's move.

		The first free segment in display order, because there is nothing better to go on: the
		segment the pin came from is not there to be near.

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
		self._reportMemberDivergence()
		self._scheduleRebuild()

	def _reportMemberDivergence(self) -> None:
		"""Say so in the log when the configured members are not the ones the composite opened for.

		A profile can no longer cause this: `vdConfig` stores the member list in the base
		configuration only, for the reasons in that module. What is left is the honest case —
		the list was edited without the composite being reopened, which the settings panel does
		for the user but a console edit does not. NVDA does not reinitialise a display whose
		driver name has not changed, so the composite goes on driving the members it opened.

		What is compared matters here, and getting it wrong is worse than not looking. The right
		side is the composite's *configured* members, not the ones it is driving: a display that
		is switched off is not being driven and is not a changed list. Comparing against the
		driven ones reported every disconnected display as a configuration problem, on every
		profile switch, for as long as it stayed away — a hardware run with two profiles and one
		display powered down filled the log with exactly that.

		Said once per distinct divergence, for the same reason. This runs on every profile switch
		because that is a cheap moment to look, not because a profile is expected to be the cause,
		and a real divergence does not become truer for being repeated.
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
		monitor.wantsColumns = wantsColumns(self.tableWanted, obj)
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

		False when they are in the one asked for, and false when they are in no table at all,
		which is what makes pressing the command outside a table the way to clear a request
		left behind.

		:param obj: the object the reader is on.
		"""
		from .flowTableSource import tableAt

		try:
			handle = tableAt(obj)
		except Exception:
			log.debugWarning("Could not look for the table the reader is in", exc_info=True)
			return False
		return handle is not None and not wantsColumns(self.tableWanted, obj)

	def _wantTableHere(self, obj) -> bool:
		"""Record that the table an object is in should be read in columns.

		What `FlowBand.layOutTable` does without the half that shows it, for the case where
		there is nothing to show it in.

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
		self.tableWanted = handle.key
		return True

	def reportAboutTheDisplay(self, text: str) -> None:
		"""Say something about what the display is showing, without writing it there.

		**`ui.message` writes to the display as well as speaking**, and a message written to
		the display sits on top of what is there until it times out. For a message *about*
		what is on the display that is the worst of both: the reader turns to the next page
		of columns, the display flashes the sentence describing the page, and the page itself
		— which is the thing they pressed the key to feel — arrives when the flash expires.
		The words are a description of what their hands were already on.

		So the speech half only, which is what `ui.message` does with the other half of
		itself. The display keeps showing the table, and it is showing the answer.

		This is for messages the display is already answering. A message about something the
		display cannot show — that there is no table here, that nothing is laid out — keeps
		`ui.message`, because for a reader who is not listening the flash *is* the message.

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

		So that changing your mind reaches the pins as well as the band, without unpinning and
		pinning again. Only the pins on *this* table: another pin showing another table is not
		what the reader was talking about.

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

	def scrollSegment(self, segmentNumber: int, forward: bool, gesture=None) -> None:
		"""Scroll one segment, whether or not it holds focus.

		:param segmentNumber: the segment to scroll.
		:param forward: True to scroll forward, False to scroll back.
		:param gesture: the gesture that asked, if there was one. Its display decides which
			way the panning keys go, since the keys are on one piece of hardware. Taken from
			the gesture rather than from `panning`'s record, which is kept only for NVDA's own
			panning commands.
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

		Numbered by `views.deviceSegmentKeys` rather than off the display as it stands,
		because the number this ends in is written to the configuration and that is the
		numbering the configuration means. A claim laid over the display — the flow band is
		one — evicts the segments it covers, so a number read off the container would name
		a different segment while the claim was up and change again when it was given back.

		Empty for an ordinary display, which has no such choice to make, and empty when
		something has taken the whole layout over, since then the setting is not what
		decides.
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
			# An activated view is a claim about what the whole display is being used for, and
			# it names its own focus segment. The setting is not read while it is up, so
			# writing one would be a command that reported success and did nothing.
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

		A pin and the focus cannot share a segment: `refreshMonitors` would draw the pinned
		object over the focus content that was just written there, and the two would fight
		for the cells on every redraw. `startMonitoring` refuses the focus segment for that
		reason and `_carryOverMonitors` releases a pin the focus has moved onto.

		Released quietly, which is what made this worth asking first. Rearranging the layout
		in the settings is a decision made while looking at the layout; pressing a key to move
		the focus is a decision about the focus, and the pin is not in the reader's head at
		that moment. Losing it is then a surprise rather than a choice.

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

		The configuration's numbering rather than the container's, for the reason
		`focusDisplayTargets` gives: a claim renumbers what the container holds and this
		number is going into the configuration.
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

		The segment the focus is *leaving* comes first, because that is the swap the reader
		is really asking for: the two things trade places, and moving the focus back trades
		them back. After it, the rest of the display in order, so that a reader who has
		divided a single row display into several — which this add-on allows on purpose, and
		which gets crowded fast but is theirs to decide — has those segments offered too.

		A claim on the segment the focus is leaving does not disqualify it, and that is not a
		liberty: the flow band claims the display *the focus is on*, by design and at this
		reader's request, so moving the focus takes the band with it and gives those rows
		back. The alternative was reported from hardware. With the focus and its band on a
		Monarch and a chat pinned to a Focus 80, toggling back found the Focus 80 wanted by
		the focus and the whole Monarch owned by the band — no home anywhere, a dialog about
		losing the pin, and a pin lost on the way to a display that was about to be free.

		What the claim leaves behind may not be a segment of this key, or of any key: a
		rectangle claimed as one panel can come back divided. So this answers where a pin
		could go, not where it will end up. `_carryOverMonitors` settles that against the
		display that actually gets built, and re-homes a pin whose provisional segment did
		not survive rather than dropping it.

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
			Keeping the position is what makes moving back and forth a round trip on two
			displays divided alike, rather than a walk towards the bottom.
		:param keep: pins to carry somewhere else rather than let go, as the key they are on
			now to the key they should move to. Anything not named here is released by
			`_carryOverMonitors` as it always was.
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

		Most of them are already there: the carry-over files a pin whose segment survived,
		and re-homes one whose segment did not. What is left is a pin that had nowhere to go
		in the middle of the rebuilding — the pass before the flow band gave its rows back —
		and was held rather than released. That is the pass this answers for.

		Released here, once, if there really is nowhere. Then the reader is told, which is
		the whole difference between a pin that was traded away and one that vanished.
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

		Said after the rebuild rather than before it, because before it this was a promise.
		The display that gets built decides where a pin lands and how many rows it has there,
		and both can differ from what was intended: a segment can dissolve into the reader's
		own layout, and the rows on the other display may be fewer.

		Two things worth saying, and the second was the reported one. A pin that could not be
		kept must be reported, or the reader is told their object moved and finds it gone. And
		a pin that is no longer read as a flow must be reported, because that is a chat which
		scrolled becoming a single line that does not — nothing on the display says why, and
		the reader's account of it was that panning had broken.

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

		Before, deliberately. `_carryOverMonitors` decides what survives by looking at where
		each pin *is* when the new container arrives, so a pin already moved is simply a pin
		whose segment survived — the carry-over needs to know nothing about why it moved.

		Except when the home was a segment the move itself dissolves, which is the flow band
		giving its rows back as the focus leaves. Then the key the pin was filed under is not
		in the new display at all, and the carry-over would drop it as gone. So what moved is
		recorded here, and the carry-over gives those pins a home in the display that was
		actually built. See L{_pinsMovedByTheFocus}.

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

		The reader may keep as many as there are free segments and no more. Which ones is
		theirs to say, and so is not moving at all: forgetting a pin is exactly how this
		command loses one, and cancelling is the answer to that.

		The one seam wx is behind on this path, so the decision above can be tested without a
		running application.

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

		Reached only when there are more than two to choose between, where a command that
		toggled would be a command that walked round a ring. The one seam wx is behind, so
		everything above can be tested without a running application.

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
		# Translators: input help message for a command.
		description=_("Moves the focus onto another of the combined braille displays"),
		category=SCRIPT_CATEGORY,
	)
	def script_changeFocusTrackingDisplay(self, gesture):
		"""Put the segment that follows the system focus on another of the displays.

		Which display the focus is on is a setting, and on a display made of two it is a
		setting with two answers — so pressing this is the whole of it, and there is no
		number to work out. With more than two, a choice has to be made rather than
		guessed, and the list is the honest way to make it: a command that cycled would
		leave a reader with four displays pressing it three times and counting.

		The answer is stored, in the profile in force, so it survives a rebuild and a
		restart and can differ per application, as every other setting here can.
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

		Three cases, and the reader is only asked about the third.

		**Nothing in the way.** The focus moves.

		**One pin, and somewhere to put it.** They trade places: the pin goes to the segment
		the focus is leaving, which is free by definition, and moving the focus back trades
		them back. No question, because there is nothing to decide — this is what the reader
		meant, and asking would turn a one-key toggle into a two-step operation, which is the
		thing the command exists to avoid.

		**Anything else.** More than one pin is in the way, or there is nowhere to put what
		is. Then it is a decision: which to keep, or not to move at all — a reader who had
		forgotten the pin was there wants the chance to leave things as they were, and one
		who wants to keep more than there is room for can divide a segment further and try
		again. Even a single row display can be divided, which gets crowded fast and is
		theirs to judge.

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

		The same switch the flow settings hold, so a reader who turns it off from the
		keyboard finds it off when they open the dialog, and a display or profile that has
		it on gets it back without the command being pressed again. Stored against the
		display in the profile in force, which is what makes it possible to have it on in
		one application and off in another.
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
		description=_("Table: Toggle table columns on or off"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowTableColumns(self, gesture):
		"""Read the table the reader is in as columns, or stop.

		A command rather than something that happens by itself, and the reason is not effort.
		Most tables on a web page are not tables of data — they are how the page is laid out —
		and turning those into columns would wreck pages that read perfectly well today. NVDA
		declines to call a layout table a table at all unless the reader has said otherwise,
		which is most of the protection, and a command the reader presses is the rest of it.

		It is also one keystroke to undo, which is what makes a layout that comes out wrong
		cost nothing. The end state is a layout remembered against the table so that a
		watchlist comes up laid out; this is what exists before that, and it stays afterwards
		as the way to say "not this one" and "this one too".
		"""
		band = self.flowBand
		target = api.getNavigatorObject()
		# Off only when the request is about where the reader is standing, or when they are
		# not in a table at all — pressing it outside one is how a request left behind is
		# cleared. Pressing it in a *different* table means that table, not this one off: with
		# no band nothing drops the old request, so the press that should have laid out the
		# second table was turning the first one off instead.
		if self.tableWanted is not None and not self._inAnotherTable(target):
			# Remembered before the request is dropped, because dropping it is what lets a
			# saved layout put it back. See `tableRefused`.
			self.tableRefused = self.tableWanted
			if band is not None:
				band.clearTable()
			else:
				self.tableWanted = None
			self.layOutPinnedTables(False, target)
			# Translators: reported when a table stops being laid out in columns.
			self.reportAboutTheDisplay(_("Table columns off"))
			return
		# A band with no flow on it is not a reason to refuse: a table in a document whose kind
		# of content the flow settings have turned off is still a table the reader can ask for
		# by name, and `FlowBand.refresh` lets a named table through those settings for that
		# reason. **No band at all is not a reason either.** On a one row display there is no
		# band to claim, and the reader still wants to say "this table, in columns" so that
		# pinning it to a display with room carries the layout.
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

	def _columnUnderTheCursor(self):
		""":return: the band and the table column the cursor is in, or (None, 0).

		The column the *reader* is in rather than the one at the left of the display: these
		commands are "do this to what I am reading", and on a table wide enough to need pages
		the two are often not the same column.
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

		What a reader does while exploring a table they have not arranged: a column of icons, a
		column of internal identifiers, a column repeating what the one beside it says. Working
		out which columns are worth the display is what the first minute in a table *is*, and
		it should not need a dialog.
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

		The reported case, in one keystroke: a column whose cells begin with six lines of help
		text somebody thought was useful reads as that help text on every row, and the thing
		that names the row is at the other end.
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
				if drawn is not None
				and overflow == drawn.overflow
				and (not keep or keep == drawn.keep)
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

		The way out of an experiment. What was remembered for this table is still remembered —
		`script_forgetTableLayout` is how that goes — so a reader who has been trying things on
		a table they arranged last week gets that arrangement back rather than nothing.
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

		The other half of the band commands: those answer what a keystroke can answer while
		reading, and this is for a table the reader comes back to — what to call a column whose
		heading is unreadable, how much room the description may have, which column is repeated
		on every page, where a page begins. See `flowTableDesigner`.

		**Asks for the dialog and returns.** The dialog itself is opened from the event loop:
		a script runs inside `queueHandler.pumpAll`, and a modal dialog shown there never
		gives the pump back — NVDA froze on hardware the first time this was pressed. See
		`flowTableDesigner.arrangeTheTable`.
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

		The end the column command was always pointed at: a watchlist that comes up laid out.
		**What is saved is what the reader arranged**, which for one press and nothing else is
		the request alone — read this table in columns — and after the band commands or the
		dialog is whatever they said there. Never the columns as they came out: those are a
		measurement, and writing one down as though it were a decision froze a reader's first
		column out of their watchlist for good.
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
		if not flowTableLayouts.remember(handle, flowTableLayouts.layoutFrom(plan, handle, arranged)):
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
		if not flowTableLayouts.forget(handle):
			# Translators: reported when a table has no saved layout to forget.
			ui.message(_("This table has no saved layout"))
			return
		band = self.flowBand
		if band is not None and band.tableWanted is not None:
			band.clearTable()
		self.tableWanted = None
		self.tableRefused = None
		# Translators: reported when a table's saved layout is dropped.
		ui.message(_("Table layout deleted"))

	def _tableHere(self):
		""":return: the table the reader is in, or None, without disturbing anything.

		The navigator object rather than the focus, which is what the column command uses and
		what a reader means by "the table I am in": on a one row display there is no band, and
		the pin they are working with is where the navigator object is.
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

	def _segmentKeysOn(self, driverName: str | None) -> list[str]:
		""":return: the keys of the segments lying on one physical display, in display order.

		Empty for an unknown display, and for an ordinary one where there is nothing to
		choose between.
		"""
		container = self.container
		if driverName is None or container is None:
			return []
		for device in deviceMap():
			if device.driverName != driverName:
				continue
			return [container.segments[index].key for index in segmentsForDevice(container.rects, device)]
		return []

	def _tableToPage(self, gesture) -> object:
		""":return: which table a paging command should move, or None if there is none.

		**The display whose key was pressed, first.** The reader put a table on the Monarch
		precisely so that it is not where they are working, so "the table in front of you" is
		the wrong answer for it: the answer is the table under the hand that pressed. That is
		the same rule the panning keys already follow, and it is what the reader asked for.

		Failing that — no key behind the command, an ordinary display, or nothing laid out on
		the pressed one — the first table in display order, which on a display showing one is
		the only one there is.

		:param gesture: the gesture that ran the command, if there was one.
		"""
		showing = self.tablesInColumns()
		if not showing:
			return None
		container = self.container
		order = [segment.key for segment in container.segments] if container is not None else []
		pressed = self._segmentKeysOn(getattr(gesture, "source", None))
		for key in [*pressed, *order]:
			if key in showing:
				return showing[key]
		return next(iter(showing.values()))

	def _turnColumnPage(self, by: int, gesture=None) -> None:
		"""Move a table by pages of columns, and say where it landed.

		Said rather than left to the fingers, because which page of a twenty-nine column table
		is on the display is exactly what the display cannot tell you: every page looks like a
		table, and the headers are on the first row of the table rather than on the band.

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

		The measurement the budget was always meant to be checked against. A budget nobody
		compares with real reading is a number somebody guessed, and a guessed number here
		has twice turned out to be the fault rather than the safety limit — so this is what
		a reader on a heavy page can press to say something better than "it felt slow".
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

		A diagnostic for the spatial reading work. It claims nothing and moves nothing: the
		flow it builds is a viewer, so the browse mode cursor stays where the reader left it.

		The clipboard rather than the log, because the log is where a report goes to be
		fished for. A hardware run means reproducing something on a display and then reading
		back what happened, and asking the reader to find their report among everything else
		NVDA said while they were doing it is a tax on the slowest part of this project. The
		log is still written, since it is what survives a session that crashed.
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

	# --- Graphics ------------------------------------------------------------------------
	#
	# The only commands in this add-on that arrive bound, and the reason is where the
	# reader's hands are. Everything else is unbound on purpose: a reader assigns what they
	# want, and an add-on helping itself to keystrokes is a nuisance. A drawing is different.
	# The reader is at the display with a hand on the panel, and a command that needs the
	# keyboard means taking that hand off the figure — which for zoom and pan is exactly the
	# wrong moment, since what they are keeping track of is where their finger was.
	#
	# The chords are chosen to be collision free rather than mnemonic. `hidBrailleStandard`'s
	# gesture map, which the Monarch driver inherits whole, uses no chord containing dot 7 or
	# dot 8 anywhere, so every chord here sits in space the standard map left empty and none
	# of them shadows an existing binding. Dot 7 added to the four arrow chords that map
	# already defines — dots 1, 4, 3 and 6 — pans, which is the one piece of mnemonic going:
	# the arrow you already know, with a modifier on it.
	#
	# Named for the Monarch driver rather than for `hidBrailleStandard`, deliberately. The
	# gesture offers both identifiers, and binding the standard one would take these chords
	# on every HID braille display, including ones that cannot draw and would answer nothing
	# but "No drawing". All of it is rebindable in Input Gestures under BrlMultiline.

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
		if mode.active:
			mode.leave()
			# Translators: reported when a drawing is taken off the display.
			ui.message(_("Drawing off"))
			return
		if mode.enter():
			ui.message(mode.describe())
			return
		# Translators: reported when a drawing could not be shown. The placeholder is why.
		ui.message(mode.lastError or _("The drawing could not be shown"))

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Magnify the drawing"),
		category=SCRIPT_CATEGORY,
		gestures=["br(brlMultilineMonarch):space+dot8"],
	)
	def script_graphicsZoomIn(self, gesture):
		self._zoomGraphics(1)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Shrink the drawing"),
		category=SCRIPT_CATEGORY,
		gestures=["br(brlMultilineMonarch):space+dot7"],
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
		mode.zoomBy(step)
		# Reported whether or not it moved: at the ends of the ladder the useful answer is
		# still where the reader now is, and silence would read as the command having missed.
		# `describe` says "whole drawing" at the bottom of the ladder rather than a number,
		# because a magnification figure for a compressed drawing means nothing to a reader.
		ui.message(mode.describe())

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Move the drawing view up"),
		category=SCRIPT_CATEGORY,
		gestures=["br(brlMultilineMonarch):space+dot1+dot7"],
	)
	def script_graphicsPanUp(self, gesture):
		self._panGraphics(0, -1)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Move the drawing view down"),
		category=SCRIPT_CATEGORY,
		gestures=["br(brlMultilineMonarch):space+dot4+dot7"],
	)
	def script_graphicsPanDown(self, gesture):
		self._panGraphics(0, 1)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Move the drawing view left"),
		category=SCRIPT_CATEGORY,
		gestures=["br(brlMultilineMonarch):space+dot3+dot7"],
	)
	def script_graphicsPanLeft(self, gesture):
		self._panGraphics(-1, 0)

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Move the drawing view right"),
		category=SCRIPT_CATEGORY,
		gestures=["br(brlMultilineMonarch):space+dot6+dot7"],
	)
	def script_graphicsPanRight(self, gesture):
		self._panGraphics(1, 0)

	def _panGraphics(self, across: int, down: int) -> None:
		"""Move the visible part of the drawing, and say where it now starts.

		Written out four times rather than generated, unlike the segment commands, because a
		generated script cannot carry a default gesture: the metaclass collects a decorator's
		gestures from the class body, and anything attached afterwards is never seen.

		:param across: steps right, negative for left.
		:param down: steps down, negative for up.
		"""
		mode = self.graphicsMode
		if not mode.active:
			ui.message(_("No drawing"))
			return
		if mode.zoom == GRAPHICS_FIT:
			# Not an edge and not a failure: at the bottom of the zoom ladder the whole drawing
			# is on the panel, so there is nowhere to move to and nothing off the display to go
			# looking for. Saying that is more use than saying the pan did not happen.
			# Translators: reported when panning is asked for but the whole drawing is already
			# on the display.
			ui.message(_("Whole drawing is shown; magnify to move around it"))
			return
		stepAcross, stepDown = mode.panStep()
		if not mode.panBy(across * stepAcross, down * stepDown):
			# Translators: reported when the drawing cannot be moved any further that way.
			ui.message(_("Edge of the drawing"))
			return
		# Said as a fraction of how far the window can move rather than as a source dot: the
		# dot number depends on how large the drawing happens to be, which is a fact about the
		# file and not about what the reader is feeling. See `GraphicsMode.positionWords`.
		ui.message(mode.positionWords())

	@script(
		# Translators: input help message for a command.
		description=_("Graphics: Show or hide the braille line beside the drawing"),
		category=SCRIPT_CATEGORY,
		gestures=["br(brlMultilineMonarch):space+dot2+dot7"],
	)
	def script_toggleGraphicsTextLine(self, gesture):
		"""Give the drawing the whole display, or give the braille line back.

		A figure on the Monarch is 96 by 35 pins with a line kept beside it and 96 by 40
		without — about a seventh more, and the seventh across the middle of the panel where
		a reader's hands already are. Worth having at times, which is why it is a command
		rather than a setting decided once.

		The cost is stated rather than hidden: with no line left, NVDA's focus braille has
		nowhere to go and is dropped for as long as the figure is up. A reader with a second
		display does not pay it at all — their focus segment is over there, untouched.
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

	@script(
		# Translators: input help message for a command.
		description=_("Misc: Reports the BrlMultiline segment layout"),
		category=SCRIPT_CATEGORY,
	)
	def script_reportLayout(self, gesture):
		"""Say what is on the display, and who claimed it.

		The counts alone were not enough to debug with. A reader whose display had stopped
		dividing was told "devices+flow view, 3 panels, 2 segments", which contains the
		answer — the `+` means a claim was laid over the configured view, and the flow had
		taken the rows the segments were meant to occupy — but only to someone who knows how
		`SegmentView.withPanel` names a composed view. What was missing was plain: which
		claim, and which rows.

		So the summary now names every claim and the rows it holds, and the full breakdown —
		every panel and every segment, with its rectangle, its owner and whether it follows
		the focus — goes to the log at INFO, where it needs no debug logging to appear and can
		be sent on.
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
		f"rows {rect.row + 1} to {rect.row + rect.numRows}, "
		f"cols {rect.col + 1} to {rect.col + rect.numCols}"
	)


def _layoutLines(container, claims: list) -> list[str]:
	"""Describe the whole arrangement, for the log.

	English rather than translated, like the flow dry run's output and for the same reason:
	this is read by whoever is diagnosing a display, often from a log someone else sent.

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
			f"    {number} {spec.key}: {_rectWords(spec.rect)}"
			+ (f"  [{', '.join(notes)}]" if notes else "")
		)
	if claims:
		lines.append(f"  claims laid over the configured view: {', '.join(sorted(claimed))}")
	else:
		lines.append("  no claims; this is the configured view")
	return lines


def _byPriority(panels) -> list:
	"""Order claims so the highest priority is laid over the rest.

	Stable, so claims of equal rank keep the order they were made in, which is what decided
	them before the ladder existed.

	:param panels: the claims, in the order they were made.
	:return: the same claims, lowest priority first.
	"""
	return sorted(panels, key=lambda panel: getattr(panel, "priority", 0))


def _columnName(column) -> str:
	""":return: what to call one column of a table when saying which ones are showing.

	Its heading, where the table has one to give. Where it has none the number, said as a
	number *of* something: a message list declares no headings and has no header row to
	borrow them from, so the reader turning to the next page of one heard "six, seven" —
	numbers with nothing attached to them, which is a worse answer than the column count.

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

	Two families, and they answer different questions. The display relative ones name a
	segment by which display it is on and how far down that display it sits, which survives
	every rearrangement that keeps the displays — bind these to the keys you keep reaching
	for. The ones that name a segment outright count across the whole display, so they can
	reach a segment the first family cannot, at the cost of moving when the layout does.

	One set is generated for every segment the add-on can offer, whether or not the current
	layout has that many. None are bound by default: assign the ones wanted through NVDA's
	Input Gestures dialog, ideally to keys on the display itself.
	"""
	for displayOrdinal in range(devicesModule.MAX_UI_DISPLAYS):
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


_generateSegmentScripts()
