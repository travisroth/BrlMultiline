# BrlMultiline: tests for routing in a segment that shows an object.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Routing in a monitored segment goes to what is pressed: a click where it would land on the
target, the focus and caret where it would not. Windows is a stand-in throughout."""

import types
import unittest

from ._stubs import callLaterQueue, flashedMessages, installStubs

installStubs()

from brlMultiline import monitorRouting  # noqa: E402
from brlMultiline.monitorRouting import (  # noqa: E402
	Target,
	expectedAt,
	isWithin,
	pointFor,
	regionAt,
	route,
	targetFor,
)


def _location(left, top, width, height):
	return types.SimpleNamespace(left=left, top=top, width=width, height=height)


class FakeObject:
	def __init__(self, location=None, windowHandle=7, focusFails=False):
		self.location = location if location is not None else _location(100, 200, 40, 20)
		self.windowHandle = windowHandle
		self.focused = False
		self._focusFails = focusFails

	def setFocus(self):
		if self._focusFails:
			raise RuntimeError("gone")
		self.focused = True


class FakeInfo:
	def __init__(self, point=(130, 210), objectAtStart=None):
		self._point = point
		self.NVDAObjectAtStart = objectAtStart
		self.caretPlaced = False

	@property
	def pointAtStart(self):
		if self._point is None:
			raise NotImplementedError
		return types.SimpleNamespace(x=self._point[0], y=self._point[1])

	def updateCaret(self):
		self.caretPlaced = True


class TextRegion:
	def __init__(self, obj, info):
		self.obj = obj
		self._info = info

	def getTextInfoForBraillePos(self, at):
		self.askedAt = at
		return self._info


class ObjectRegion:
	def __init__(self, obj):
		self.obj = obj


class FakeDesktop:
	"""Windows as far as routing asks it anything."""

	def __init__(self, hitsTheTarget=True):
		self.hitsTheTarget = hitsTheTarget
		self.broughtForward = []
		self.clicks = []

	def topWindow(self, obj):
		return 1000 + obj.windowHandle

	def bringForward(self, window):
		self.broughtForward.append(window)

	def isTheTargetsAt(self, obj, point):
		return self.hitsTheTarget

	def click(self, point):
		self.clicks.append(point)


class RoutingTestCase(unittest.TestCase):
	def setUp(self):
		callLaterQueue.pending.clear()
		flashedMessages.clear()
		self.desktop = FakeDesktop()
		self.original = monitorRouting.desktop
		monitorRouting.desktop = self.desktop

	def tearDown(self):
		monitorRouting.desktop = self.original
		callLaterQueue.pending.clear()

	def settle(self):
		"""Let the window come forward, and the caret settle after a focus."""
		while callLaterQueue.fire():
			pass


class TestWhatIsUnderTheKey(RoutingTestCase):
	def test_aFlowIsAsked(self):
		region = ObjectRegion(FakeObject())
		controller = types.SimpleNamespace(regionAt=lambda position: (region, position + 1))
		self.assertEqual(regionAt(types.SimpleNamespace(controller=controller), 4), (region, 5))

	def test_regionsAreReadAsNVDAReadsAPress(self):
		region = ObjectRegion(FakeObject())
		segment = types.SimpleNamespace(
			controller=None,
			windowEndPos=50,
			windowPosToBufferPos=lambda position: position + 10,
			bufferPosToRegionPos=lambda pos: (region, pos - 10),
		)
		self.assertEqual(regionAt(segment, 3), (region, 3))

	def test_pastTheEndOfTheWindowIsNothing(self):
		segment = types.SimpleNamespace(
			controller=None,
			windowEndPos=12,
			windowPosToBufferPos=lambda position: position + 10,
			bufferPosToRegionPos=lambda pos: (None, 0),
		)
		self.assertIsNone(regionAt(segment, 3))

	def test_textGivesAPosition(self):
		obj, info = FakeObject(), FakeInfo()
		region = TextRegion(obj, info)
		target = targetFor(region, 6)
		self.assertIs(target.obj, obj)
		self.assertIs(target.info, info)
		self.assertEqual(region.askedAt, 6)

	def test_aBrowseModeDocumentFocusesItsRootAndPlacesItsCursor(self):
		root = FakeObject()
		interceptor = types.SimpleNamespace(rootNVDAObject=root)
		target = targetFor(TextRegion(interceptor, FakeInfo()), 0)
		self.assertIs(target.obj, root)
		self.assertIs(target.treeInterceptor, interceptor)

	def test_anObjectGivesItself(self):
		obj = FakeObject()
		target = targetFor(ObjectRegion(obj), 0)
		self.assertIs(target.obj, obj)
		self.assertIsNone(target.info)


class TestWhereTheClickGoes(unittest.TestCase):
	def test_theStartOfTheText(self):
		self.assertEqual(pointFor(Target(FakeObject(), FakeInfo(point=(5, 6)))), (5, 6))

	def test_textThatCannotSayWhereIsNotClicked(self):
		"""The middle of the object it is in would put the caret somewhere else in it."""
		inner = FakeObject(location=_location(10, 20, 30, 40))
		target = Target(FakeObject(), FakeInfo(point=None, objectAtStart=inner))
		self.assertIsNone(pointFor(target))

	def test_theHitTestLooksForTheObjectTheTextStartsIn(self):
		inner = FakeObject()
		self.assertIs(expectedAt(Target(FakeObject(), FakeInfo(objectAtStart=inner))), inner)
		outer = FakeObject()
		self.assertIs(expectedAt(Target(outer, FakeInfo(objectAtStart=None))), outer)


class TestWhatCountsAsAHit(unittest.TestCase):
	def test_theTargetItself(self):
		target = FakeObject()
		self.assertTrue(isWithin(target, target))

	def test_somethingInsideTheTarget(self):
		target = FakeObject()
		inner = FakeObject()
		inner.parent = FakeObject()
		inner.parent.parent = target
		self.assertTrue(isWithin(inner, target))

	def test_aNeighbourInTheSameWindowIsNot(self):
		"""In a browser every control shares one window."""
		self.assertFalse(isWithin(FakeObject(windowHandle=7), FakeObject(windowHandle=7)))

	def test_aContainerAroundTheTargetIsNot(self):
		container = FakeObject()
		container.parent = None
		target = FakeObject()
		target.parent = container
		self.assertFalse(isWithin(container, target))

	def test_objectsThatCannotBeComparedAreNot(self):
		class Unreadable(FakeObject):
			def __eq__(self, other):
				raise RuntimeError("COM failed")

			__hash__ = object.__hash__

		self.assertFalse(isWithin(Unreadable(), FakeObject()))

	def test_theMiddleOfAnObject(self):
		self.assertEqual(pointFor(Target(FakeObject(location=_location(100, 200, 40, 20)))), (120, 210))

	def test_anObjectWithNoSizeHasNoPoint(self):
		self.assertIsNone(pointFor(Target(FakeObject(location=_location(0, 0, 0, 0)))))


class TestGoingThere(RoutingTestCase):
	def test_itsWindowComesForwardAndIsClicked(self):
		obj = FakeObject(windowHandle=7)
		route(self.segmentShowing(ObjectRegion(obj)), 0)
		self.assertEqual(self.desktop.broughtForward, [1007])
		self.assertEqual(self.desktop.clicks, [], "the click waits for the window")
		self.settle()
		self.assertEqual(self.desktop.clicks, [(120, 210)])
		self.assertFalse(obj.focused)

	def test_textIsClickedWhereItStarts(self):
		route(self.segmentShowing(TextRegion(FakeObject(), FakeInfo(point=(130, 210)))), 0)
		self.settle()
		self.assertEqual(self.desktop.clicks, [(130, 210)])

	def test_aCoveredWindowIsFocusedAndItsCaretPlaced(self):
		"""A click would land on whatever is on top."""
		self.desktop.hitsTheTarget = False
		obj, info = FakeObject(), FakeInfo()
		route(self.segmentShowing(TextRegion(obj, info)), 0)
		self.settle()
		self.assertEqual(self.desktop.clicks, [])
		self.assertTrue(obj.focused)
		self.assertTrue(info.caretPlaced)

	def test_aBrowseModeCursorIsPlacedRatherThanTheCaret(self):
		self.desktop.hitsTheTarget = False
		root, info = FakeObject(), FakeInfo()
		interceptor = types.SimpleNamespace(rootNVDAObject=root, selection=None)
		route(self.segmentShowing(TextRegion(interceptor, info)), 0)
		self.settle()
		self.assertTrue(root.focused)
		self.assertIs(interceptor.selection, info)
		self.assertFalse(info.caretPlaced)

	def test_somewhereThatCannotBeGoneToIsSaid(self):
		self.desktop.hitsTheTarget = False
		route(self.segmentShowing(ObjectRegion(FakeObject(focusFails=True))), 0)
		self.settle()
		self.assertEqual(flashedMessages, ["Cannot go there"])

	def test_paddingDoesNothingAndIsStillTaken(self):
		segment = types.SimpleNamespace(controller=types.SimpleNamespace(regionAt=lambda position: None))
		self.assertTrue(route(segment, 3))
		self.settle()
		self.assertEqual(self.desktop.broughtForward, [])

	def segmentShowing(self, region):
		return types.SimpleNamespace(controller=types.SimpleNamespace(regionAt=lambda position: (region, 0)))


if __name__ == "__main__":
	unittest.main()
