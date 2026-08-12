# BrlMultiline: patches to NVDA's braille handler.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The two places NVDA's braille handler has to be adjusted.

1. `_doNewObject` clears the whole buffer and appends every region to it. With several
	segments the regions have to be sorted first, and only the segments actually
	receiving regions may be cleared.
2. `scrollForward` and `scrollBack` are swapped when reversed panning is configured for
	the current display.

Both are installed and removed symmetrically, so that disabling the add-on restores
NVDA's own behaviour without a restart.
"""

import time

import config
import keyboardHandler
from braille.brailleHandler import BrailleHandler
from braille.constants import CONTEXTPRES_CHANGEDCONTEXT
from config.configFlags import TetherTo
from logHandler import log

from . import bmConfig, documentLines
from .container import DisplayContainer

_originalDoNewObject = None
_originalScrollForward = None
_originalScrollBack = None
_originalHandlePendingUpdate = None

MONITOR_REFRESH_INTERVAL = 0.4
"""Seconds between re-reads of a pinned object.

Fast enough that a pinned clock or status line is not visibly stale, slow enough that
reading a document's text through a `TextInfo` is not being done on every core cycle.
"""

_lastMonitorRefresh = 0.0


def _applyFocusToHardLeft(handler: BrailleHandler, regions: list) -> None:
	"""Apply NVDA's focusToHardLeft rule to one group of regions.

	Taken from `BrailleHandler._doNewObject`. Applying it per segment rather than across
	the whole display is the point: the rule is about a region's position within the
	group it is displayed with.
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
	if not isinstance(container, DisplayContainer) or container.numSegments == 1:
		# Nothing to sort. Let NVDA do exactly what it normally does.
		return _originalDoNewObject(self, regions)
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
	_populateDocumentLines(container)
	if self.buffer is self.mainBuffer:
		self.update()
	elif self.buffer is self.messageBuffer and keyboardHandler.keyCounter > self._keyCountForLastMessage:
		self._dismissMessage()


def _getMonitoredKeys() -> set[str]:
	""":return: the keys of segments holding a pinned object, which must not be written over.

	This is the runtime half of the reservation picture. The other half, panels that
	reserved their segments when the view was composed, is carried on the segments
	themselves and does not have to be gathered here.
	"""
	from . import getPlugin

	plugin = getPlugin()
	if plugin is None:
		return set()
	return set(plugin.monitoredKeys)


def _populateDocumentLines(container: DisplayContainer) -> None:
	"""Fill the free segments with the document lines around the caret, if configured.

	Also takes back lines the add-on placed but should no longer be showing, whether
	because the focus has left the document or because the setting has been turned off.
	"""
	if container.numSegments == 1:
		return
	if bmConfig.isSpeechOutputMode():
		# The display is showing speech. Lines written now would stay under the reader's
		# fingers beside it, since nothing handles caret moves in this mode.
		return
	monitored = _getMonitoredKeys()
	try:
		if bmConfig.shouldShowDocumentLines():
			changed = documentLines.populate(container, monitored)
		else:
			changed = documentLines.clearDocumentRegions(container, monitored)
		if changed:
			container.update()
	except Exception:
		log.debugWarning("Could not show document lines", exc_info=True)


def _refreshPinnedObjects() -> None:
	"""Re-read pinned objects, so that a pin shows the object as it is now.

	A pin shows something in a window the user is not working in, and nothing NVDA does
	announces that it has changed: there is no focus or caret event to redraw from, and
	events in a background window are not delivered at all unless something has asked for
	them. So the objects are re-read on a timer, and this is the timer — `_handlePendingUpdate`
	already runs on every core cycle, which is far more often than any of this needs, hence
	the interval. `ObjectMonitor.refresh` writes nothing when nothing has changed, so the
	cost between changes is a read of each pinned object and no display traffic.
	"""
	global _lastMonitorRefresh
	from . import getPlugin

	plugin = getPlugin()
	if plugin is None or not plugin.monitoredKeys:
		return
	now = time.monotonic()
	if now - _lastMonitorRefresh < MONITOR_REFRESH_INTERVAL:
		return
	_lastMonitorRefresh = now
	try:
		plugin.refreshMonitors()
	except Exception:
		log.debugWarning("Could not refresh pinned objects", exc_info=True)


def _handlePendingUpdateWithDocumentLines(self: BrailleHandler) -> None:
	"""Refresh the document line regions after NVDA has handled its own pending updates.

	NVDA only marks the region belonging to the caret as needing an update, so the regions
	showing neighbouring lines have to be refreshed here.

	`_handlePendingUpdate` runs on every core cycle and clears its own pending set before
	returning, so whether there was anything to do has to be noted before delegating to it.
	Refreshing regardless would re-read the caret and retranslate every row many times a
	second while nothing at all was changing.

	Pinned objects are the exception, and are refreshed whether or not anything was pending:
	what they show is not driven by the caret, so a pending update is no signal at all for
	them.
	"""
	hadPendingUpdate = bool(self._regionsPendingUpdate)
	_originalHandlePendingUpdate(self)
	_refreshPinnedObjects()
	if not hadPendingUpdate:
		return
	container = self.mainBuffer
	if not isinstance(container, DisplayContainer) or container.numSegments == 1:
		return
	if not bmConfig.shouldShowDocumentLines():
		return
	if bmConfig.isSpeechOutputMode():
		# The display is showing speech, not the document the caret is in.
		return
	try:
		regions = [
			region
			for segment in container.segments
			for region in segment.regions
			if isinstance(region, documentLines.TextInfoPositionRegion) and region.lineOffset != 0
		]
		if not regions:
			return
		for region in regions:
			region.update()
		container.update()
		container.updateDisplay()
	except Exception:
		log.debugWarning("Could not refresh document lines", exc_info=True)


def _scrollForwardMaybeReversed(self: BrailleHandler) -> None:
	if bmConfig.shouldReverseScrollButtons():
		return _originalScrollBack(self)
	return _originalScrollForward(self)


def _scrollBackMaybeReversed(self: BrailleHandler) -> None:
	if bmConfig.shouldReverseScrollButtons():
		return _originalScrollForward(self)
	return _originalScrollBack(self)


def install() -> None:
	"""Install the patches. Safe to call when they are already installed."""
	global _originalDoNewObject, _originalScrollForward, _originalScrollBack
	global _originalHandlePendingUpdate
	if _originalDoNewObject is not None:
		log.debug("BrlMultiline patches already installed")
		return
	_originalDoNewObject = BrailleHandler._doNewObject
	_originalScrollForward = BrailleHandler.scrollForward
	_originalScrollBack = BrailleHandler.scrollBack
	_originalHandlePendingUpdate = BrailleHandler._handlePendingUpdate
	BrailleHandler._doNewObject = _doNewObjectMultiSegment
	BrailleHandler.scrollForward = _scrollForwardMaybeReversed
	BrailleHandler.scrollBack = _scrollBackMaybeReversed
	BrailleHandler._handlePendingUpdate = _handlePendingUpdateWithDocumentLines
	log.debug("BrlMultiline patches installed")


def remove() -> None:
	"""Restore NVDA's own methods. Safe to call when nothing is installed."""
	global _originalDoNewObject, _originalScrollForward, _originalScrollBack
	global _originalHandlePendingUpdate
	if _originalDoNewObject is None:
		return
	BrailleHandler._doNewObject = _originalDoNewObject
	BrailleHandler.scrollForward = _originalScrollForward
	BrailleHandler.scrollBack = _originalScrollBack
	BrailleHandler._handlePendingUpdate = _originalHandlePendingUpdate
	_originalDoNewObject = None
	_originalScrollForward = None
	_originalScrollBack = None
	_originalHandlePendingUpdate = None
	log.debug("BrlMultiline patches removed")
