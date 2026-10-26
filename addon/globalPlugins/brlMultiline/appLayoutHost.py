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

CHANGE_SETTLE_SECONDS = 0.05
"""How long after an app module says its content changed the layout is read again.

Short, because the point of saying so is not waiting. Long enough that a burst of changes, a
message and its timestamp and its reactions arriving together, is read once."""

CHANGE_SLICE_SECONDS = 0.03
"""How long one pass after a change may spend before carrying on in the next core cycle.

The same as the pin timer's slice, `patches.MONITOR_SLICE_SECONDS`, so that being told about a
change does not let a layout take more of a cycle than polling would."""

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

	keys: dict[str, Any] = dataclasses.field(default_factory=dict)
	"""The L{SHOW_KEY_ATTRIBUTE} each object had when it was shown. See L{sameAnswer}."""

	failures: dict[str, int] = dataclasses.field(default_factory=dict)
	"""How many times running each segment's callable has raised."""


SHOW_KEY_ATTRIBUTE = "brlMultilineShowKey"
"""An attribute an app module may set on the object a `show` callable returns, naming what it shows.

For an element that an application reuses for different content, such as Outlook's reading pane,
which keeps one document window for every message. NVDA compares the window, so every message is
the same object and the reader's place would carry over from one to the next. Two answers whose
keys differ are different, and the new one starts at its top."""


def _showKey(obj: Any) -> Any:
	try:
		return getattr(obj, SHOW_KEY_ATTRIBUTE, None)
	except Exception:
		return None


def sameObject(old: Any, new: Any) -> bool:
	"""Whether two answers are the same object, as far as anyone can tell.

	Through NVDA's `__eq__`, which can call the application and raise. A raise counts as
	different, which costs only a reset of the reading position.
	"""
	if old is new:
		return True
	if old is None or new is None:
		return False
	try:
		return bool(old == new)
	except Exception:
		return False


def sameAnswer(old: Any, shownKey: Any, new: Any) -> bool:
	"""Whether an answer shows what the segment already shows.

	:param old: the object the segment was last given.
	:param shownKey: its `SHOW_KEY_ATTRIBUTE` when it was shown, kept then, since the same
		object may come back with its key changed.
	:param new: the answer now.

	When the key shown and the answer's both exist, different keys are different content, even
	from the same object. The same keys still have to be the same object, so a key can never keep
	an object that was replaced.
	"""
	newKey = _showKey(new)
	if shownKey is not None and newKey is not None and shownKey != newKey:
		return False
	return sameObject(old, new)


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
		self._changeTimer = None
		"""Set while a change an app module reported is waiting to be read. See L{contentChanged}."""
		self._dirty = False
		"""Whether an app module said its content changed since the callables were last asked."""
		self._toRead: list[str] | None = None
		"""The segments the pass of L{refreshInTurn} under way has still to read, or None between
		passes. Fixed when the pass starts, so a pass that runs out carries on with what it had
		left rather than starting another, and ends once each segment has had its turn."""
		self._fresh: set[str] = set()
		"""Segments drawn by the callables' phase of the pass under way, not to be read again in it."""

	# What the plugin asks.

	@property
	def isUp(self) -> bool:
		""":return: whether a layout's claim is among the plugin's active ones."""
		shown = self.shown
		return shown is not None and any(panel is shown.panel for panel in self.plugin.activePanels)

	@property
	def name(self) -> str | None:
		""":return: the name of the layout up, or None."""
		return self.shown.layout.name if self.shown is not None else None

	def flowSegmentKey(self) -> str | None:
		""":return: the key of the layout's focus segment when the reader's flow draws there, or None.

		Asked by the flow band for its segment. See `AppLayoutPanel`.
		"""
		if not self.isUp:
			return None
		return self.shown.panel.flowKey

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

		The room is worked out again now, since a pin or claim made while waiting changes it.
		"""
		self._timer = None
		pending, self._pending = self._pending, None
		if pending is None or self.plugin.isTerminated:
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
		panel = AppLayoutPanel(
			claimFor(area, rects),
			rects,
			layout.focusIndex,
			flowFocus=layout.segments[layout.focusIndex].flow,
		)
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
		self._cancelChange()
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
		if not any(panel is shown.panel for panel in self.plugin.activePanels):
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
		if self.shown is None or self.plugin.isTerminated:
			return
		self._cancelPending()
		self._placeFor(api.getFocusObject(), self.shown.appModule)

	def terminate(self) -> None:
		self._cancelPending()
		self._cancelChange()
		self._stopMonitors()
		self.shown = None

	# Keeping secondary segments current.
	#
	# One scheduler, `refreshInTurn`, does all of it for every caller: the pin timer, the
	# faster pass while the reader types, and an app module saying its content changed. Each
	# caller hands it a deadline and comes back when it says the pass is unfinished, so no
	# caller can spend more than its slice and every segment gets its turn.

	def contentChanged(self, appModule: Any = None) -> None:
		"""An app module says what its layout shows has changed: read it again shortly.

		See `GlobalPlugin.brlMultilineContentChanged`, which is how app modules reach this.
		It marks the layout as changed and asks for a prompt pass of L{refreshInTurn}, which
		asks the callables again and reads every segment, within a slice and in turn. Calls
		while one is waiting are folded into it.

		:param appModule: the app module saying so, or None for whichever layout is up.
		"""
		shown = self.shown
		if shown is None or (appModule is not None and appModule is not shown.appModule):
			return
		self._dirty = True
		if self._changeTimer is not None:
			return
		self._changeTimer = wx.CallLater(int(CHANGE_SETTLE_SECONDS * 1000), self._readChange)

	def _readChange(self) -> None:
		"""Read the layout again after an app module said it changed, a slice at a time."""
		self._changeTimer = None
		if self.shown is None or self.plugin.isTerminated or bmConfig.isSpeechOutputMode():
			return
		if self.refreshInTurn(time.monotonic() + CHANGE_SLICE_SECONDS) and self.shown is not None:
			# Carried on at once rather than after the pin timer's interval, which is the
			# point of having been told.
			self._changeTimer = wx.CallLater(1, self._readChange)

	def _cancelChange(self) -> None:
		if self._changeTimer is not None:
			self._changeTimer.Stop()
			self._changeTimer = None

	def refreshInTurn(self, deadline: float) -> bool:
		"""Keep the layout current: one slice of asking the callables and reading the segments.

		Two phases, each item of each checked against the one deadline:

		1. **The callables**, when they are due, when an app module said something changed, or
		   when the last pass stopped among them. See L{resolve}.
		2. **The segments**, each read again once. Which ones is settled when the pass starts, and
		   a slice that runs out leaves the rest of that list for the next, so a pass ends after
		   every segment has had one turn, however slow each is. A segment the first phase has
		   just drawn is not read again straight after.

		The deadline is checked before every item, the first included, so a slice the first
		phase spent leaves the second for the next pass rather than starting it late. One slow
		call into the application cannot be cut short; the next is what waits.

		:param deadline: when this slice runs out, by `time.monotonic`.
		:return: whether the pass is unfinished, so the caller comes back at once.
		"""
		shown = self.shown
		if shown is None:
			self._resolveFrom = 0
			self._toRead = None
			self._fresh.clear()
			return False
		if bmConfig.isSpeechOutputMode():
			return False
		due = time.monotonic() - self._resolvedAt >= RESOLVE_SECONDS
		if self._resolveFrom or self._dirty or due:
			if not self._resolveFrom:
				self._dirty = False
			if self.resolve(deadline=deadline, fresh=self._fresh):
				return True
			if self.shown is not shown:
				return False
		if self._toRead is None:
			self._toRead = list(self.liveMonitors())
		while self._toRead:
			if time.monotonic() >= deadline:
				return True
			key = self._toRead.pop(0)
			if key in self._fresh:
				continue
			monitor = self.liveMonitors().get(key)
			if monitor is not None:
				monitor.refresh()
			if self.shown is not shown:
				return False
		self._toRead = None
		self._fresh.clear()
		return False

	def resolveIfDue(self, deadline: float | None = None) -> bool:
		"""Ask the callables again if L{RESOLVE_SECONDS} have passed or a pass is unfinished.

		:return: whether the pass stopped at the deadline with callables still to ask.
		"""
		if self.shown is None:
			self._resolveFrom = 0
			return False
		if not self._resolveFrom and time.monotonic() - self._resolvedAt < RESOLVE_SECONDS:
			return False
		return self.resolve(deadline=deadline)

	def resolve(
		self,
		reveal: bool = False,
		deadline: float | None = None,
		fresh: set | None = None,
	) -> bool:
		"""Bring every secondary segment up to date with what it should show.

		:param reveal: whether this is the layout's first showing, when every segment is
			filled from the start.
		:param deadline: when to stop and leave the rest for the next pass, checked before
			each callable. See L{refreshInTurn}.
		:param fresh: collects the keys of segments drawn here, so the pass reading the
			segments next does not read them again.
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
			if deadline is not None and index > start and time.monotonic() >= deadline:
				self._resolveFrom = index
				return True
			obj, problem = resolveShown(segment)
			if problem is not None:
				if not self._failed(index, f"the callable {problem}"):
					return False
			elif reveal or not sameAnswer(shown.objects.get(key), shown.keys.get(key), obj):
				installed = self._showIn(key, obj)
				if self.shown is not shown:
					# Showing it rebuilt the display and the rebuild dropped the layout.
					return False
				if installed:
					shown.failures[key] = 0
					if fresh is not None:
						fresh.add(key)
				elif not self._failed(index, "its object could not be shown"):
					return False
			else:
				shown.failures[key] = 0
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

		All or nothing: on failure the segment goes back to its old monitor and object (see
		`_restore`), and the caller counts it towards `MAX_FAILURES`. The old monitor stops
		before the new one draws, since stopping detaches the segment's flow. Beside speech the
		regions are emptied and the pin timer draws once braille is back.

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
		try:
			if monitor is None or speech:
				# "No message selected" is a true thing for a reading pane to say, and stale
				# content is not. Beside speech, nothing of the layout's is drawn either.
				if present:
					container.clear(key)
					if not speech:
						container.update()
						container.updateDisplay()
			if monitor is not None and present and not speech:
				monitor.refresh(reveal=True)
		except Exception:
			log.debugWarning(f"BrlMultiline: could not draw {key!r}", exc_info=True)
			if monitor is not None:
				try:
					monitor.stop()
				except Exception:
					pass
			self._restore(key, old)
			return False
		if monitor is not None:
			shown.monitors[key] = monitor
		shown.objects[key] = obj
		shown.keys[key] = _showKey(obj)
		return True

	def _restore(self, key: str, old: ObjectMonitor | None) -> None:
		"""Put a segment back as it was before a replacement that failed, drawn again if it can be.

		Its recorded object is left as it was, which is the old one, so a later answer of the
		old object finds the old monitor showing it, and a later answer of the new one tries
		the new one again.
		"""
		shown = self.shown
		if old is None:
			return
		shown.monitors[key] = old
		container = self.plugin.container
		if container is None or not container.hasKey(key) or bmConfig.isSpeechOutputMode():
			return
		try:
			old.refresh(reveal=False)
		except Exception:
			# Drawn by the next pass instead; it is filed, so it will be read.
			log.debugWarning(f"BrlMultiline: could not draw {key!r} again after a failure", exc_info=True)

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
