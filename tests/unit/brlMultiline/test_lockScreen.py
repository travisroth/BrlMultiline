# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""While Windows is locked, a pin below the lock screen is neither read nor shown.

NVDA clears the display as Windows locks and refuses to build a region for an object below the
lock screen, but a pin keeps the regions it already has and refreshes them on a timer, and a
flow's segment fills itself again when it is cleared. NVDA's `utils.security` is faked here,
since the stubs have none.
"""

import sys
import types
import unittest

from ._stubs import CONFIG, installStubs, loadPlugin

installStubs()
loadPlugin()

from .test_objectMonitor import PINNED_KEY, MonitorTestCase  # noqa: E402


class FakeAction:
	"""An extension point that keeps its handlers, as NVDA's `extensionPoints.Action` does."""

	def __init__(self):
		self.handlers = []

	def register(self, handler):
		self.handlers.append(handler)

	def unregister(self, handler):
		self.handlers.remove(handler)

	def notify(self, **kwargs):
		for handler in list(self.handlers):
			handler(**kwargs)


class LockScreenTestCase(MonitorTestCase):
	"""A pin, with NVDA's lock screen test and notice faked before the plugin starts."""

	def setUp(self):
		self.locked = False
		self.aboveTheLockScreen = []
		self.lockChanged = FakeAction()
		security = types.ModuleType("utils.security")
		security.objectBelowLockScreenAndWindowsIsLocked = self.isBelow
		security.post_sessionLockStateChanged = self.lockChanged
		utils = sys.modules.get("utils") or types.ModuleType("utils")
		self.addCleanup(self.restoreModules, sys.modules.get("utils"), sys.modules.get("utils.security"))
		utils.security = security
		sys.modules["utils"] = utils
		sys.modules["utils.security"] = security
		super().setUp()

	@staticmethod
	def restoreModules(utils, security):
		for name, module in (("utils", utils), ("utils.security", security)):
			if module is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = module

	def isBelow(self, obj, shouldLog=True):
		return self.locked and not any(obj is above for above in self.aboveTheLockScreen)

	@property
	def monitor(self):
		return self.plugin._monitors[PINNED_KEY]

	def lock(self):
		"""Lock Windows as NVDA sees it: the display cleared, then the notice sent."""
		self.locked = True
		self.container.clear()
		self.lockChanged.notify(isNowLocked=True)

	def unlock(self):
		self.locked = False
		self.lockChanged.notify(isNowLocked=False)


class TestAPinWhileLocked(LockScreenTestCase):
	"""Eight single row segments, so the pin is shown through its regions."""

	def test_lockingTakesThePinOffTheDisplay(self):
		self.pinDocument(caretIndex=2)
		self.lock()
		self.assertEqual(self.segment.regions, [])

	def test_itStaysOffAtTheNextRefresh(self):
		self.pinDocument(caretIndex=2)
		self.lock()
		self.plugin.refreshMonitors()
		self.assertEqual(self.segment.regions, [])

	def test_theObjectIsNotReadWhileLocked(self):
		self.pinDocument(caretIndex=2)
		self.lock()
		reads = []
		for region in self.monitor.regions:
			region.update = lambda: reads.append(1)
		self.plugin.refreshMonitors()
		self.assertEqual(reads, [])

	def test_refreshingWhileLockedWritesNothing(self):
		self.pinDocument(caretIndex=2)
		self.lock()
		before = self.handler.updateCount
		self.plugin.refreshMonitors()
		self.plugin.refreshMonitors()
		self.assertEqual(self.handler.updateCount, before)

	def test_unlockingPutsThePinBack(self):
		self.pinDocument(caretIndex=2)
		self.lock()
		self.unlock()
		self.assertEqual(self.pinnedRegion.rawText, "line 2")

	def test_itComesBackOnTheLineItWasPannedTo(self):
		self.pinDocument(caretIndex=2)
		self.pan(forward=True)
		self.lock()
		self.unlock()
		self.assertEqual(self.pinnedRegion.rawText, "line 3")

	def test_whatChangedWhileLockedIsShownOnUnlocking(self):
		self.pinDocument(caretIndex=2)
		self.lock()
		self.lines[2] = "changed"
		self.unlock()
		self.assertEqual(self.pinnedRegion.rawText, "changed")

	def test_anObjectAboveTheLockScreenIsStillShown(self):
		interceptor = self.pinDocument(caretIndex=2)
		self.aboveTheLockScreen.append(interceptor)
		self.locked = True
		self.plugin.refreshMonitors()
		self.assertEqual(self.pinnedRegion.rawText, "line 2")

	def test_aPinMadeWhileLockedIsNotShown(self):
		self.locked = True
		self.pinDocument(caretIndex=2)
		self.assertEqual(self.segment.regions, [])

	def test_terminatingStopsListeningForTheLock(self):
		self.plugin.terminate()
		self.assertEqual(self.lockChanged.handlers, [])


class TestAFlowingPinWhileLocked(LockScreenTestCase):
	"""Two segments of four rows, so the pin is read as a flow, whose segment refills on a clear."""

	segmentCount = 2

	def setUp(self):
		super().setUp()
		CONFIG["focusSegment"] = 0
		self.plugin.rebuildBuffer()

	def test_lockingDetachesTheFlow(self):
		self.pinDocument(caretIndex=0)
		self.lock()
		self.assertIsNone(self.segment.controller)
		self.assertEqual(self.segment.regions, [])

	def test_thePinKeepsItsFlow(self):
		self.pinDocument(caretIndex=0)
		control = self.monitor.controller
		self.lock()
		self.assertIs(self.monitor.controller, control)

	def test_unlockingAttachesItAgain(self):
		self.pinDocument(caretIndex=0)
		self.lock()
		self.unlock()
		self.assertIs(self.segment.controller, self.monitor.controller)

	def test_theFlowIsNotReadWhileLocked(self):
		self.pinDocument(caretIndex=0)
		self.lock()
		reads = []
		self.monitor.controller.rereadContent = lambda: reads.append(1)
		self.plugin.refreshMonitors()
		self.assertEqual(reads, [])


if __name__ == "__main__":
	unittest.main()
