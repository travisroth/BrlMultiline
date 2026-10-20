# BrlMultiline: showing an app layout, and following the focus with it.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The life of an app layout on the display: when it comes up, what it shows, when it goes.

`appLayout` is the contract and knows nothing of NVDA. This is the other half, and it owns
three things the contract cannot:

1. **Following the focus.** Asked on every focus change. A layout of the same name as the one
   up is kept as it is, at once, so moving between a chat's messages and its compose box costs
   nothing and loses no one's place. Anything else, a different layout, or none, waits until
   the focus has stayed put for L{SETTLE_SECONDS}, because focus in a web application passes
   through containers on its way and each would otherwise rebuild the display. Leaving the
   application is the exception: that takes the layout down at once, without asking, so an
   app module can never leave its arrangement on another application's display.
2. **Keeping secondary segments current.** A segment whose `show` is a callable is asked again
   on every focus change within the layout and on the pin timer every L{RESOLVE_SECONDS}. A
   different answer replaces what is shown, starting at the top; the same answer keeps it, so
   a reader partway down a message is not thrown back; None blanks the segment. A callable
   that raises keeps what it showed, and one that raises L{MAX_FAILURES} times running takes
   the layout down.
3. **Failure is the reader's own arrangement.** Anything that goes wrong takes the layout
   down and logs why. The reader is never left with a half built display.

The objects are shown by `ObjectMonitor`, the same machinery as a pin, so a secondary segment
reads as a flow, follows a growing chat and pans exactly as a pin does. They are kept apart
from the reader's pins, which the plugin keeps in `_monitors`: the layout's never count as
the reader's, are never carried to another segment, and go when the layout goes.

See `docs/design/app-layouts-plan.md`.
"""

import dataclasses
import time
from typing import TYPE_CHECKING, Any

import api
import wx
from logHandler import log

from . import bmConfig

from .appLayout import (
	PANEL_NAME,
	AppLayout,
	AppLayoutPanel,
	Band,
	LayoutError,
	askAppModule,
	claimFor,
	displayArea,
	placeSegments,
	resolveShown,
	segmentKey,
)
from .layout import SegmentRect
from .objectMonitor import ObjectMonitor

if TYPE_CHECKING:
	from . import GlobalPlugin

SETTLE_SECONDS = 0.25
"""How long the focus must stay put before a layout comes up, changes or goes.

Long enough to swallow the containers focus passes through in a web application on the way to
where it is going, short enough that the layout is there by the time the reader's hand is."""

RESOLVE_SECONDS = 1.0
"""How often a secondary segment's callable is asked again, over and above every focus change.

Slower than the pin timer, because asking means a call into the application, and this only
has to catch what loads after the focus has settled, such as a reading pane's document."""

MAX_FAILURES = 3
"""How many times running a segment's callable may raise before the layout is taken down."""


@dataclasses.dataclass
class Shown:
	"""The layout on the display now, and everything needed to keep it current."""

	layout: AppLayout
	area: SegmentRect
	bands: list[Band]
	rects: list[SegmentRect]
	panel: AppLayoutPanel
	appModule: Any
	"""The app module that offered it. Leaving it takes the layout down."""

	spread: bool = False
	"""Whether it was placed spread across displays. A change of mind places it again."""

	within: SegmentRect | None = None
	"""The reader's focus segment it was placed around. Moving the focus to another display
	changes it, and the layout is placed again so the focus goes where the reader sent it."""

	monitors: dict[str, ObjectMonitor] = dataclasses.field(default_factory=dict)
	"""What each secondary segment is showing, by segment key. A blank segment has none."""

	objects: dict[str, Any] = dataclasses.field(default_factory=dict)
	"""The object each secondary segment was last given, to tell a new answer from the same."""

	failures: dict[str, int] = dataclasses.field(default_factory=dict)
	"""How many times running each segment's callable has raised."""


def sameObject(old: Any, new: Any) -> bool:
	"""Whether two answers are the same object, as far as anyone can tell.

	Two reads of one element are often two Python objects, and on UI Automation they compare
	equal only through NVDA's own `__eq__`, which is a call into the application and can raise.
	A raise counts as different: that costs a reset of the reading position and never shows the
	wrong content.
	"""
	if old is new:
		return True
	if old is None or new is None:
		return False
	try:
		return bool(old == new)
	except Exception:
		return False


class AppLayoutHost:
	"""Shows the focus's app layout, and takes it down. One per plugin."""

	def __init__(self, plugin: "GlobalPlugin") -> None:
		self.plugin = plugin
		self.shown: Shown | None = None
		self._pending: tuple | None = None
		"""What the focus asked for, once it settles: (whether there is a layout, the focus, its
		app module). Only the intent is kept. The room and the layout itself are worked out
		again when it settles, since a pin, a claim or a setting may have changed meanwhile."""
		self._timer = None
		self._resolvedAt = 0.0
		self._resolveFrom = 0
		"""Where an unfinished pass of the callables carries on. See L{resolveIfDue}."""

	# What the plugin asks.

	@property
	def isUp(self) -> bool:
		""":return: whether a layout's claim is among the plugin's active ones."""
		shown = self.shown
		return shown is not None and any(panel is shown.panel for panel in self.plugin._activePanels)

	@property
	def name(self) -> str | None:
		""":return: the name of the layout up, or None."""
		return self.shown.layout.name if self.shown is not None else None

	def liveMonitors(self) -> dict[str, ObjectMonitor]:
		""":return: the secondary segments' monitors whose segments are on the display now.

		A drawing outranks a layout and takes its rows while it is up; the monitors wait rather
		than being refreshed into segments that are not there.
		"""
		shown = self.shown
		container = self.plugin.container
		if shown is None or container is None:
			return {}
		return {key: monitor for key, monitor in shown.monitors.items() if container.hasKey(key)}

	# Following the focus.

	def followFocus(self, focus: Any) -> None:
		"""Answer a focus change: keep, change, show or take down the layout.

		:param focus: the new focus.
		"""
		plugin = self.plugin
		if not plugin.appLayoutsInForce():
			self._cancelPending()
			self.takeDown("app layouts are not in force")
			return
		appModule = getattr(focus, "appModule", None)
		if self.shown is not None and appModule is not self.shown.appModule:
			# At once and without asking, so an app module that raises, hangs or forgets can
			# never leave its arrangement on another application's display.
			self._cancelPending()
			self.takeDown("the focus left the application")
		if appModule is None or not hasattr(appModule, "brlMultilineLayout"):
			self._cancelPending()
			return
		area, bands, within, spread = plugin.appLayoutRoom()
		layout = None
		if area is not None:
			layout, problem = askAppModule(appModule, focus, displayArea(area, bands, within, spread))
			if problem is not None:
				log.debugWarning(
					f"BrlMultiline: the app layout of {_appName(appModule)} was refused: {problem}"
				)
		if (
			self.shown is not None
			and layout is not None
			and layout.name == self.shown.layout.name
			and spread == self.shown.spread
		):
			# The same layout: kept as it is, at once. A focus change within it is when a
			# message list's selection moves, which is the moment its reading pane changes.
			self._cancelPending()
			self.resolve()
			return
		if self.shown is None and layout is None:
			self._cancelPending()
			return
		self._pending = (layout is not None, focus, appModule)
		if self._timer is not None:
			self._timer.Stop()
		self._timer = wx.CallLater(int(SETTLE_SECONDS * 1000), self._settle)

	def _cancelPending(self) -> None:
		self._pending = None
		if self._timer is not None:
			self._timer.Stop()
			self._timer = None

	def _settle(self) -> None:
		"""The focus has stayed put: show what it last asked for, or take the layout down.

		**The room is worked out again now, not taken from when the focus arrived.** A reader
		who pins something while the layout waits, or code that claims rows, changes what is
		free, and a layout placed in the old room would cover the new pin and release it. So
		the app module is asked again against the room as it is.
		"""
		self._timer = None
		pending, self._pending = self._pending, None
		if pending is None or self.plugin._terminated:
			return
		wanted, focus, appModule = pending
		if not self.plugin.appLayoutsInForce():
			self.takeDown("app layouts are not in force")
			return
		if not wanted:
			self.takeDown("the focus has no layout here")
			return
		self._placeFor(focus, appModule)

	def _placeFor(self, focus: Any, appModule: Any) -> bool:
		"""Ask the app module against the room as it is now, and show what it answers.

		:return: whether a layout is up.
		"""
		area, bands, within, spread = self.plugin.appLayoutRoom()
		if area is None:
			self.takeDown("there is no room left")
			return False
		layout, problem = askAppModule(appModule, focus, displayArea(area, bands, within, spread))
		if layout is None:
			self.takeDown(problem or "the focus has no layout here")
			return False
		return self.show(layout, area, bands, appModule, within, spread)

	# Showing and taking down.

	def show(
		self,
		layout: AppLayout,
		area: SegmentRect,
		bands: list[Band],
		appModule: Any,
		within: SegmentRect | None = None,
		spread: bool = False,
	) -> bool:
		"""Put a layout on the display, replacing any that is up.

		:param within: the focus segment's rectangle, which keeps the focus on its display.
		:param spread: whether the reader wants the layout spread across displays.
		:return: whether it is up. If not, the reader's own arrangement is, and the log says why.
		"""
		try:
			rects = placeSegments(layout, area, bands, within, spread)
		except LayoutError as error:
			log.debugWarning(f"BrlMultiline: app layout {layout.name!r} cannot be placed: {error}")
			self.takeDown("the layout could not be placed")
			return False
		self._stopMonitors()
		# Only the rows the layout uses, so a display it leaves alone keeps the reader's own
		# arrangement rather than going blank.
		panel = AppLayoutPanel(claimFor(area, rects), rects, layout.focusIndex)
		# Before the claim, because claiming rebuilds the display and the rebuild asks whether
		# a layout is up: the flow waits for one rather than fighting it for the rows.
		self.shown = Shown(
			layout=layout,
			area=area,
			bands=list(bands),
			rects=rects,
			panel=panel,
			appModule=appModule,
			spread=spread,
			within=within,
		)
		try:
			self.plugin.activatePanel(panel)
		except (ValueError, LookupError):
			log.warning(f"BrlMultiline: app layout {layout.name!r} cannot be shown", exc_info=True)
			self.shown = None
			self.plugin.deactivatePanel(PANEL_NAME)
			return False
		log.debug(f"BrlMultiline: app layout {layout.name!r} is up, {len(rects)} segments in {area}")
		self.resolve(reveal=True)
		return True

	def takeDown(self, reason: str = "") -> None:
		"""Take the layout down and give the reader their own arrangement back."""
		if self.shown is None:
			return
		name = self.shown.layout.name
		self._stopMonitors()
		self.shown = None
		log.debug(f"BrlMultiline: app layout {name!r} taken down: {reason or 'asked'}")
		self.plugin.deactivatePanel(PANEL_NAME)

	def onRebuilt(self) -> None:
		"""After the display was rebuilt: notice a layout that was dropped or is no longer allowed.

		A display that changed size, a profile switch or a settings change rebuilds with the
		claim re-composed, and a claim that no longer fits is dropped by the rebuild. A setting
		turned off is answered after the rebuild, since taking the claim down is a rebuild too.
		"""
		shown = self.shown
		if shown is None:
			return
		if not any(panel is shown.panel for panel in self.plugin._activePanels):
			log.debug(f"BrlMultiline: app layout {shown.layout.name!r} was dropped by a rebuild")
			self._stopMonitors()
			self.shown = None
			return
		if not self.plugin.appLayoutsInForce():
			wx.CallAfter(self.takeDown, "app layouts are no longer in force")
			return
		if bmConfig.shouldSpreadAppLayouts() != shown.spread:
			# Turned over in the settings, or by a profile switch. Placed again after the
			# rebuild, since placing it is a rebuild too.
			wx.CallAfter(self.placeAgain)
			return
		configured = self.plugin.configuredFocusRect()
		if configured is not None and configured != shown.within:
			# The reader moved the focus to another display, or changed which segment follows
			# it. The layout's claim would otherwise go on offering its old focus segment, and
			# the focus would stay where the reader had just moved it from.
			wx.CallAfter(self.placeAgain)

	def placeAgain(self) -> None:
		"""Place the layout up again from where the focus is, as when spreading is turned over.

		Asked afresh rather than moved, because the app module is told a different area when
		the layout is spread, and may want to arrange it differently.
		"""
		if self.shown is None or self.plugin._terminated:
			return
		self._cancelPending()
		self._placeFor(api.getFocusObject(), self.shown.appModule)

	def terminate(self) -> None:
		self._cancelPending()
		self._stopMonitors()
		self.shown = None

	# Keeping secondary segments current.

	def resolveIfDue(self, deadline: float | None = None) -> bool:
		"""Ask the callables again if L{RESOLVE_SECONDS} have passed. Called from the pin timer.

		:param deadline: when the timer's slice of this core cycle runs out, by
			`time.monotonic`. The callables are calls into the application, so they share the
			slice with the pins rather than running before its clock starts. One slow call
			cannot be cut short, but the next is left for the next pass.
		:return: whether a pass is unfinished, so the timer comes back at once.
		"""
		if self.shown is None:
			self._resolveFrom = 0
			return False
		if not self._resolveFrom and time.monotonic() - self._resolvedAt < RESOLVE_SECONDS:
			return False
		return self.resolve(deadline=deadline)

	def resolve(self, reveal: bool = False, deadline: float | None = None) -> bool:
		"""Bring every secondary segment up to date with what it should show.

		:param reveal: whether this is the layout's first showing, when every segment is
			filled from the start.
		:param deadline: when to stop and leave the rest for the next pass. See L{resolveIfDue}.
		:return: whether the pass stopped at the deadline with segments still to ask.
		"""
		shown = self.shown
		if shown is None:
			self._resolveFrom = 0
			return False
		segments = shown.layout.segments
		start = self._resolveFrom if deadline is not None and self._resolveFrom < len(segments) else 0
		self._resolveFrom = 0
		for index in range(start, len(segments)):
			segment = segments[index]
			if segment.isFocus:
				continue
			key = segmentKey(index)
			if not reveal and not segment.isResolved:
				continue
			obj, problem = resolveShown(segment)
			if problem is not None:
				if not self._failed(index, f"the callable {problem}"):
					return False
			elif reveal or not sameObject(shown.objects.get(key), obj):
				installed = self._showIn(key, obj)
				if self.shown is not shown:
					# Showing it rebuilt the display and the rebuild dropped the layout.
					return False
				if installed:
					shown.failures[key] = 0
				elif not self._failed(index, "its object could not be shown"):
					return False
			else:
				shown.failures[key] = 0
			if deadline is not None and time.monotonic() >= deadline and index + 1 < len(segments):
				self._resolveFrom = index + 1
				return True
		self._resolvedAt = time.monotonic()
		return False

	def _failed(self, index: int, problem: str) -> bool:
		"""Count a failure for a segment, taking the layout down after L{MAX_FAILURES} running.

		:return: whether the layout is still up.
		"""
		shown = self.shown
		key = segmentKey(index)
		failures = shown.failures.get(key, 0) + 1
		shown.failures[key] = failures
		log.debugWarning(f"BrlMultiline: segment {index} of app layout {shown.layout.name!r}: {problem}")
		if failures >= MAX_FAILURES:
			self.takeDown(f"segment {index} kept failing")
			return False
		return True

	def _showIn(self, key: str, obj: Any) -> bool:
		"""Show an object in a secondary segment from its start, or blank it for None.

		**All or nothing.** The replacement is built before what it replaces is let go, and the
		object is recorded as shown only once it is. An element that has vanished can raise as
		soon as it is touched, and recording it first left the segment with no monitor and the
		next pass believing the answer was already on the display, so it never tried again.

		**Nothing is drawn beside speech.** While braille shows speech the segment's regions are
		emptied and the monitor kept, and the pin timer draws it once braille is back, exactly
		as it does for the reader's own pins.

		:return: whether the segment now shows the object, or is blank for None.
		"""
		shown = self.shown
		container = self.plugin.container
		present = container is not None and container.hasKey(key)
		speech = bmConfig.isSpeechOutputMode()
		monitor = None
		if obj is not None:
			try:
				monitor = ObjectMonitor(obj, key)
			except Exception:
				log.debugWarning(f"BrlMultiline: could not show an object in {key!r}", exc_info=True)
				return False
		old = shown.monitors.pop(key, None)
		if old is not None:
			try:
				old.stop()
			except Exception:
				log.debugWarning(f"BrlMultiline: could not stop what {key!r} showed", exc_info=True)
		if monitor is None or speech:
			# "No message selected" is a true thing for a reading pane to say, and stale
			# content is not. Beside speech, nothing of the layout's is drawn either.
			if present:
				container.clear(key)
				if not speech:
					container.update()
					container.updateDisplay()
		if monitor is not None:
			if present and not speech:
				try:
					monitor.refresh(reveal=True)
				except Exception:
					log.debugWarning(f"BrlMultiline: could not draw {key!r}", exc_info=True)
					try:
						monitor.stop()
					except Exception:
						pass
					return False
			shown.monitors[key] = monitor
		shown.objects[key] = obj
		return True

	def _stopMonitors(self) -> None:
		shown = self.shown
		if shown is None:
			return
		for monitor in shown.monitors.values():
			try:
				monitor.stop()
			except Exception:
				log.debugWarning("Could not stop an app layout segment", exc_info=True)
		shown.monitors.clear()


def _appName(appModule: Any) -> str:
	return getattr(appModule, "appName", None) or type(appModule).__name__
