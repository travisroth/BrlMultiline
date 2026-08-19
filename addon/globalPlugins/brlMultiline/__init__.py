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

from . import bmConfig, panning, patches
from .container import DisplayContainer
from . import devices as devicesModule
from .devices import DeviceInfo, configuredMembers, deviceMap, resolveDisplaySegment
from .layout import SegmentRect
from .messages import MessageBuffer
from .objectMonitor import ObjectMonitor
from .panels import BraillePanel
from .settingsPanel import BrailleMultilineSettingsPanel, VirtualDisplaySettingsPanel
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
		self._activePanels: list[BraillePanel] = []
		"""Claims laid over whatever view is in force, in the order they were made.

		Kept across rebuilds and re-composed each time, so that a claim survives a settings
		change or a display swap. A claim that no longer fits is dropped and reported.
		"""
		bmConfig.initialize()
		patches.install()
		# Which display's keys are being pressed, for the per display panning direction.
		panning.install()
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(BrailleMultilineSettingsPanel)
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
			self.stopAllMonitoring()
			self._restoreOriginalBuffer()
			patches.remove()
			panning.remove()
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
		for panel in panels:
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
		for panel in self._activePanels:
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
			if driverName is not None and driverName not in present:
				# The display this pin was on has gone. Every other way a segment can go —
				# a claim taking it, the focus moving onto it, the user rearranging — is a
				# decision, and a pin released by a decision stays released.
				home = self._rehomeMonitor(container, survivors)
				if home is not None:
					log.info(
						f"BrlMultiline: {driverName} has gone; the object pinned to it moves to "
						f"segment {home!r}",
					)
					monitor = self._monitors[key]
					# The key in this dictionary is only how the plugin finds it. What decides
					# where it draws is the monitor's own key and the regions it has already
					# stamped, so it is told to move rather than merely filed differently.
					monitor.moveTo(home)
					survivors[home] = monitor
					continue
				log.info(
					f"BrlMultiline: {driverName} has gone and there is nowhere left to put what "
					f"was pinned to it, so it is released",
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
		description=_("Writes what a flowed reading of this object would show to the log"),
		category=SCRIPT_CATEGORY,
	)
	def script_flowDryRun(self, gesture):
		"""Read the object under the navigator as a flow, into the log.

		A diagnostic for the spatial reading work, which is not yet on the display. It
		claims nothing and moves nothing: the flow it builds is a viewer, so the browse
		mode cursor stays where the reader left it.
		"""
		from .flowDryRun import dryRun

		try:
			lines = dryRun(handler=braille.handler)
		except Exception:
			log.error("The flow dry run failed", exc_info=True)
			# Translators: reported when a diagnostic command fails.
			ui.message(_("Flow dry run failed, see the log"))
			return
		verdict = next((line for line in lines if line.startswith("Reversibility")), "")
		# Translators: reported after a diagnostic has written its result to the log.
		# The placeholder is the number of lines written.
		ui.message(_("Flow dry run written to the log, {count} lines").format(count=len(lines)))
		log.debug(verdict)

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
		ui.message(
			_(
				# Translators: reports the current layout. Placeholders are the name of the view,
				# the number of panels, the number of segments, and the segment following the
				# focus.
				"{view} view, {panels} panels, {count} segments, focus in segment {focus}",
			).format(
				view=container.view.name,
				panels=len(container.panels),
				count=container.numSegments,
				focus=container.focusSegmentNumber,
			),
		)


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
			"Scrolls segment {segment} of {display} forward",
		)
		if forward
		else _(
			# Translators: input help message for a command. Placeholders are a segment number,
			# counting from 0 on that display, and the name of one of the combined displays.
			"Scrolls segment {segment} of {display} back",
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
			"Shows the navigator object in segment {segment} of {display}",
		)
		if start
		else _(
			# Translators: input help message for a command. Placeholders are a segment number,
			# counting from 0 on that display, and the name of one of the combined displays.
			"Stops showing an object in segment {segment} of {display}",
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
