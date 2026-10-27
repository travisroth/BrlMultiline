# BrlMultiline: patches to NVDA's braille handler.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The places NVDA's braille handler has to be adjusted.

1. `_doNewObject` clears the whole buffer and appends every region to it. With several
	segments the regions have to be sorted first, and only the segments actually
	receiving regions may be cleared.
2. `scrollForward` and `scrollBack` pan the display whose panning key was pressed, in the
	direction that display is configured for.
3. `_handlePendingUpdate` refreshes the document line regions and the pinned objects, neither
	of which NVDA has any reason to mark as needing an update.
4. `VirtualBuffer._handleUpdate` is where NVDA learns that a browse mode document changed
	under it. The band wants to know as well, and nothing else says so.
5. `TextInfoRegion._addFieldText` and `TextInfoRegion.update` record which stretches of a
	document's braille NVDA wrote itself, which a shape may be drawn over. See
	`glyphFlow.FIELDS`.
	`TextInfoRegion.update` is also where a browse mode region is adopted to read a table's row
	on one line, while the reader has asked for that. See `tableRowLine`.

All are installed and removed symmetrically, so disabling the add-on restores NVDA without a
restart, but a method another add-on has since replaced is left alone. See `remove`.
"""

import time

import config
import keyboardHandler
from braille.brailleHandler import BrailleHandler
from braille.constants import CONTEXTPRES_CHANGEDCONTEXT
from config.configFlags import TetherTo
from logHandler import log

from . import glyphFlow, panning, tableRowLine
from .container import DisplayContainer

_owners: dict[str, object] = {}
"""The class each patched name belongs to, since they are no longer all on one class."""

_ATTRIBUTES = {"textInfoUpdate": "update"}
"""Where an entry's key is not the name of the attribute it replaces.

Two classes can share a method name, such as `update`, so the key stays unique.
"""


def _attributeFor(name: str) -> str:
	""":return: the attribute an entry replaces. Its own key unless `_ATTRIBUTES` says otherwise.

	:param name: the entry's key.
	"""
	return _ATTRIBUTES.get(name, name)


_originals: dict[str, object] = {}
"""NVDA's own methods, by the name each one is patched under.

An entry lives as long as this module's replacement is in place, and outlives L{remove} when
the replacement could not be taken back — because in that case it is still being delegated to.
"""

_installedMethods: dict[str, object] = {}
"""What was put in their place, so that L{remove} can tell whether it is still there."""

MONITOR_REFRESH_INTERVAL = 0.4
"""Seconds between re-reads of a pinned object.

Fast enough that a pinned clock or status line is not visibly stale, slow enough that
reading a document's text through a `TextInfo` is not being done on every core cycle.
"""

MONITOR_SLICE_SECONDS = 0.03
"""How long one core cycle may spend on pins before leaving the rest to the next one.

Shared by the pins in rotation, so a cycle's cost does not grow with them. See
`GlobalPlugin.refreshMonitorsInTurn`.
"""

MONITOR_QUIET_AGE = 2.0
"""How stale a pin may get while the reader is typing before it is refreshed regardless.

Typing has the core cycle, but not indefinitely, or a pinned clock would stop while typing.
"""

LAYOUT_QUIET_AGE = MONITOR_REFRESH_INTERVAL
"""How stale an app layout's segments may get while the reader is typing.

Not `MONITOR_QUIET_AGE`, since an app layout's segments are in the window being typed in. They
keep the ordinary interval while the reader's pins wait.
"""

_lastMonitorRefresh = 0.0
_monitorsUnfinished = False
"""Whether a slice ran out with pins still waiting, so the rest are due at once."""

_monitorsRefreshedAt = 0.0
"""When the pins were last all refreshed, which is what `MONITOR_QUIET_AGE` is measured
against. Not `_lastMonitorRefresh`, which a part-finished rotation also moves."""

_layoutRefreshedAt = 0.0
"""When an app layout's segments were last all refreshed, measured against `LAYOUT_QUIET_AGE`."""

_layoutUnfinished = False
"""Whether a layout-only pass while the reader typed ran out with segments still waiting, so the
rest are due at once rather than after another interval."""


def _applyFocusToHardLeft(handler: BrailleHandler, regions: list) -> None:
	"""Apply NVDA's focusToHardLeft rule to one group of regions.

	From `BrailleHandler._doNewObject`, applied per segment.
	"""
	if not (
		handler.getTether() == TetherTo.FOCUS.value
		and config.conf["braille"]["focusContextPresentation"] == CONTEXTPRES_CHANGEDCONTEXT
	):
		return
	focusToHardLeftSet = False
	for region in regions:
		if region.focusToHardLeft:
			focusToHardLeftSet = True
		elif not focusToHardLeftSet and getattr(region, "_focusAncestorIndex", None) is None:
			# Displaying a new object with the same ancestry as the previous one, so
			# anchor this region at the left of its segment.
			region.focusToHardLeft = True
			focusToHardLeftSet = True


def _doNewObjectMultiSegment(self: BrailleHandler, regions) -> None:
	"""Replacement for `BrailleHandler._doNewObject` that knows about segments."""
	container = self.mainBuffer
	if not isinstance(container, DisplayContainer):
		return _originals["_doNewObject"](self, regions)
	if container.numSegments == 1 and not container.specs[0].ownerDrawsFocus:
		# Nothing to sort. Let NVDA do exactly what it normally does.
		#
		# Unless that one segment draws its own focus content, as a whole display flow does.
		# See `panels.SegmentSpec.ownerDrawsFocus`.
		return _originals["_doNewObject"](self, regions)
	self.autoScroll(enable=False)
	grouped: dict[int, list] = {index: [] for index in range(container.numSegments)}
	for region in regions:
		index = container.resolvePlacementTarget(region)
		if index is None:
			# The region names a segment that no longer exists, so the claim that owned it
			# has gone. Dropping it is the point: showing it in the focus segment would
			# write a departed panel's content over the user's ordinary braille.
			continue
		# Note where it went, so that later calls handed a region but no segment (focus,
		# scrollTo) can find their way back. Recorded separately from `targetSegment`,
		# which belongs to whoever claimed the region, not to this bookkeeping.
		container.recordPlacement(region, index)
		grouped[index].append(region)
	for index, group in grouped.items():
		if not group:
			# A segment receiving nothing keeps what it already had.
			continue
		if _ownerTookTheFocus(container.segments[index], group):
			# The segment's owner draws its own focus content, so NVDA's regions are handed
			# over rather than written in beside what the owner drew. Without this the two
			# producers fight, and this loop wins by clearing the segment on every focus
			# change. See `SegmentSpec.exclusive`.
			grouped[index] = []
			continue
		container.clear(index)
		_applyFocusToHardLeft(self, group)
		for region in group:
			container.segments[index].append(region)
	container.update()
	for index, group in grouped.items():
		if not group:
			continue
		# The last region of each group receives focus within its own segment.
		container.focus(group[-1])
		self.scrollToCursorOrSelection(group[-1])
	if self.buffer is self.mainBuffer:
		self.update()
	elif self.buffer is self.messageBuffer and keyboardHandler.keyCounter > self._keyCountForLastMessage:
		self._dismissMessage()


def _ownerTookTheFocus(segment, regions) -> bool:
	"""Offer a focus change to a segment whose owner draws its own content.

	:param segment: the segment the regions were destined for.
	:param regions: the regions NVDA built for the new focus.
	:return: whether the owner dealt with them.
	"""
	accept = getattr(segment, "acceptFocusRegions", None)
	if accept is None:
		return False
	try:
		return bool(accept(regions))
	except Exception:
		log.debugWarning(f"An owner could not take the focus in {segment!r}", exc_info=True)
		return False


def _refreshPinnedObjects(busy: bool = False) -> None:
	"""Re-read pinned objects, so that a pin shows the object as it is now.

	The timer pins are re-read on, since nothing reports a change in a background window; see
	`objectMonitor`. A cycle with an NVDA update pending is left to it, and a cycle that does
	refresh spends `MONITOR_SLICE_SECONDS` rather than however long the pins take.

	:param busy: whether NVDA had an update of its own pending on this core cycle, which is
		what a keystroke looks like from here.
	"""
	global \
		_lastMonitorRefresh, \
		_monitorsUnfinished, \
		_monitorsRefreshedAt, \
		_layoutRefreshedAt, \
		_layoutUnfinished
	from . import getPlugin

	plugin = getPlugin()
	layoutUp = plugin is not None and plugin.appLayouts.isUp
	if plugin is None or not (plugin.monitoredKeys or layoutUp):
		# An app layout counts even with every segment blank, since a blank reading pane is
		# waiting for its document and only this clock will notice it arrive.
		return
	now = time.monotonic()
	unfinished = _monitorsUnfinished or (_layoutUnfinished and layoutUp)
	if not unfinished and now - _lastMonitorRefresh < MONITOR_REFRESH_INTERVAL:
		return
	if busy and now - _monitorsRefreshedAt < MONITOR_QUIET_AGE:
		if layoutUp and (_layoutUnfinished or now - _layoutRefreshedAt >= LAYOUT_QUIET_AGE):
			# The reader's pins wait for the keystroke; the layout's segments do not. A pass
			# that ran out carries on next cycle, and the layout counts as refreshed only once
			# a whole pass has finished.
			try:
				_layoutUnfinished = bool(plugin.refreshAppLayoutInTurn(MONITOR_SLICE_SECONDS))
			except Exception:
				log.debugWarning("Could not refresh the app layout", exc_info=True)
				_layoutUnfinished = False
			if not _layoutUnfinished:
				_layoutRefreshedAt = now
		return
	# The whole pass reads the layout first and carries any of it left over.
	_layoutUnfinished = False
	_lastMonitorRefresh = now
	try:
		_monitorsUnfinished = bool(plugin.refreshMonitorsInTurn(MONITOR_SLICE_SECONDS))
	except Exception:
		log.debugWarning("Could not refresh pinned objects", exc_info=True)
		_monitorsUnfinished = False
	if not _monitorsUnfinished:
		_monitorsRefreshedAt = now
		_layoutRefreshedAt = now


def _handleUpdateTellingTheBand(self) -> None:
	"""Tell the band that a browse mode document changed, then let NVDA have the news.

	Reached through `nvdaControllerInternal_vbufChangeNotify` whenever the buffer re-renders a
	subtree an accessibility event touched, not only for ARIA. NVDA itself refreshes only the
	caret's line. The band is told first, since it only schedules work, and a failure here
	never costs NVDA its update.
	"""
	try:
		_tellTheBand(self)
	except Exception:
		log.debugWarning("Could not pass on a document change", exc_info=True)
	original = _originals.get("_handleUpdate")
	if original is not None:
		original(self)


def _tellTheBand(document) -> None:
	"""Pass one document change on to the flow band, if there is one.

	:param document: the virtual buffer that changed.
	"""
	from . import getPlugin

	plugin = getPlugin()
	band = getattr(plugin, "flowBand", None) if plugin is not None else None
	if band is None:
		return
	band.documentChanged(document)


def _handlePendingUpdateRefreshingPins(self: BrailleHandler) -> None:
	"""Refresh pinned objects after NVDA has handled its own pending updates.

	Whether anything was pending is noted first, since `_handlePendingUpdate` clears its set.
	Pins are refreshed either way, as the caret does not drive them.
	"""
	hadPendingUpdate = bool(self._regionsPendingUpdate)
	_originals["_handlePendingUpdate"](self)
	_refreshPinnedObjects(busy=hadPendingUpdate)


def _nativeScroll(handler: BrailleHandler, forward: bool) -> None:
	"""Pan for NVDA's own panning commands, on the display whose key was pressed.

	Two corrections to what NVDA would do, both from the keys belonging to one piece of
	hardware: the direction is the pressed display's, and so is the segment.

	While a message is up, only the display it is on pans it, as NVDA does; another display's
	keys pan that display's own segment.

	:param handler: the braille handler.
	:param forward: which of the two commands was run, before reversal.
	"""
	source = panning.sourceForNativeScroll()
	if panning.shouldReverseForSource(source):
		forward = not forward
	buffer = handler.buffer
	if isinstance(buffer, DisplayContainer):
		container = buffer
	elif (
		getattr(buffer, "drawsMainBufferAround", False)
		and isinstance(handler.mainBuffer, DisplayContainer)
		and not panning.sourceHolds(source, buffer.rect)
	):
		# A key on a display the message is not on. That display's segment is drawn around the
		# message, so panning it is seen at once. See `segments.isShowing`.
		container = handler.mainBuffer
	else:
		container = None
	segment = panning.nativeSegmentForSource(source, container)
	if segment is None:
		# No key press behind this scroll, an ordinary display, or a message showing. NVDA's
		# own behaviour, which for the container means the segment following the focus.
		return _originals["scrollForward" if forward else "scrollBack"](handler)
	if forward:
		container.scrollForward(segment)
	else:
		container.scrollBack(segment)


def _scrollForwardMaybeReversed(self: BrailleHandler) -> None:
	return _nativeScroll(self, forward=True)


def _scrollBackMaybeReversed(self: BrailleHandler) -> None:
	return _nativeScroll(self, forward=False)


def _addFieldTextRecordingProvenance(self, *args, **kwargs) -> None:
	"""Note which stretch of a document's braille NVDA has just written itself.

	All control field text goes through this method, the document's words through another
	branch. Wrapped, recording afterwards, so a changed NVDA build changes what is recorded.
	"""
	before = len(getattr(self, "rawText", "") or "")
	_originals["_addFieldText"](self, *args, **kwargs)
	glyphFlow.recordFieldText(self, before, len(getattr(self, "rawText", "") or ""))


def _textInfoUpdateForgettingProvenance(self) -> None:
	"""Forget the last reading's fields, because this one begins by emptying the text.

	Emptied, not removed. See `glyphFlow.FIELDS`.
	"""
	glyphFlow.restartFields(self)
	if tableRowLine.adopt(self):
		# Now a region that reads a table row where one was asked for, and NVDA's own line
		# everywhere else. Updated again as what it has become. See `tableRowLine`.
		self.update()
		return
	_originals["textInfoUpdate"](self)


def _textInfoRegionClass():
	""":return: NVDA's `TextInfoRegion`, or None where there is none to patch.

	Imported here, as `_virtualBufferClass` does. Without it a document gets no shapes.
	"""
	try:
		from braille.regions.textInfo import TextInfoRegion

		return TextInfoRegion
	except Exception:
		log.debugWarning("No text region class to patch", exc_info=True)
		return None


def _virtualBufferClass():
	""":return: NVDA's `VirtualBuffer`, or None where there is none to patch.

	Imported here, since it can fail and the other patches must still go in.
	"""
	try:
		import virtualBuffers

		return virtualBuffers.VirtualBuffer
	except Exception:
		log.debugWarning("No virtual buffer class to patch", exc_info=True)
		return None


def _replacements() -> dict[str, tuple]:
	""":return: what this module installs, by the name each one replaces.

	Each entry carries its class. The same function objects every call, since `remove` checks
	identity.
	"""
	entries = {
		"_doNewObject": (BrailleHandler, _doNewObjectMultiSegment),
		"scrollForward": (BrailleHandler, _scrollForwardMaybeReversed),
		"scrollBack": (BrailleHandler, _scrollBackMaybeReversed),
		"_handlePendingUpdate": (BrailleHandler, _handlePendingUpdateRefreshingPins),
	}
	buffers = _virtualBufferClass()
	if buffers is not None:
		entries["_handleUpdate"] = (buffers, _handleUpdateTellingTheBand)
	regions = _textInfoRegionClass()
	if regions is not None and hasattr(regions, "_addFieldText"):
		entries["_addFieldText"] = (regions, _addFieldTextRecordingProvenance)
		entries["textInfoUpdate"] = (regions, _textInfoUpdateForgettingProvenance)
	return entries


def liveUpdatesInstalled(document=None) -> bool:
	""":return: whether a change to a document would be passed on to the band.

	The band polls only on no, so a wrong yes silently costs updates. No when the method is no
	longer ours, or the document is not a `VirtualBuffer`, as a UIA document is not.

	:param document: the document being read, or None to ask only whether the patch is in.
	"""
	if getattr(_owners.get("_handleUpdate"), "_handleUpdate", None) is not _installedMethods.get(
		"_handleUpdate",
	):
		return False
	if document is None:
		return True
	buffers = _virtualBufferClass()
	return buffers is not None and isinstance(document, buffers)


def install() -> None:
	"""Install the patches. Safe to call when they are already installed."""
	for name, (owner, replacement) in _replacements().items():
		if name in _originals:
			# Already installed, or left in place by a removal that found something else on
			# top. Installing over that would either undo the other add-on or, if it wrapped
			# this module's method rather than replacing it, put this one on top of a wrapper
			# that calls it — a loop with no end.
			log.debug(f"BrlMultiline: {name} is already patched")
			continue
		attribute = _attributeFor(name)
		_owners[name] = owner
		_originals[name] = getattr(owner, attribute)
		_installedMethods[name] = replacement
		setattr(owner, attribute, replacement)
	log.debug("BrlMultiline patches installed")


def remove() -> None:
	"""Restore NVDA's own methods. Safe to call when nothing is installed.

	Only where the method is still this module's, or another add-on's work would be undone. A
	method left in place keeps its `_originals` entry, which also stops a second install.
	"""
	for name in list(_originals):
		owner = _owners.get(name, BrailleHandler)
		attribute = _attributeFor(name)
		if getattr(owner, attribute, None) is not _installedMethods.get(name):
			log.debugWarning(
				f"BrlMultiline: {name} has been replaced since it was patched; "
				"leaving it as it is rather than undoing whatever replaced it",
			)
			continue
		setattr(owner, attribute, _originals.pop(name))
		_installedMethods.pop(name, None)
		_owners.pop(name, None)
	log.debug("BrlMultiline patches removed")
