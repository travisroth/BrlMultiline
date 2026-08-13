# BrlMultiline: phase 0 spike for the virtual braille display driver.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Console spike: can NVDA drive a second braille display alongside the one it owns?

Everything in `docs/design/virtual-display-plan.md` rests on one assumption that cannot be
settled by reading NVDA's source: that a braille display driver can be constructed, written
to, and taken input from while `braille.handler.display` is some other driver. This module
is the experiment.

It is not part of the add-on and is not packaged: `spikes/` sits outside `addon/`, so the
build never sees it. It exists to be imported from the NVDA Python console (NVDA+control+z)
on a machine with two displays attached.

Run it like this, one line at a time::

	import sys; sys.path.insert(0, r"C:\\code\\BrlMultiline\\spikes")
	import virtualDisplaySpike as spike
	spike.primary()
	spike.devices()
	spike.start("hidBrailleStandard")
	spike.ruler()
	spike.rows()
	spike.write("hello from the second display")
	spike.watch()

Now press keys on **both** displays: routing keys, panning keys, and anything else. Then::

	spike.report()
	spike.pokePrimary()
	spike.stop()

`stop` is safe to call at any point and safe to call twice. Call it before closing the
console; a driver left running holds its port open.

What each question in the plan looks like when answered:

- Can two drivers be live at once? `start` returns without raising and `info` reports a
  sensible cell count.
- Can we write to the secondary? `ruler` puts feelable content on it and leaves the primary
  showing whatever NVDA was showing.
- Do the secondary's keys still reach NVDA? `report` shows events whose source is the
  secondary's driver name.
- Does the secondary disturb the primary? `pokePrimary` still speaks and brailles after a
  spell of pressing keys on the secondary.
- Can two displays of the *same* driver be told apart? `report` shows a driver reference and
  a thread name per event. If neither varies by device, they cannot, and the plan's "one
  display per driver name" limit stands.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, Optional

import braille
import config
import extensionPoints
import inputCore
from braille.display import getDisplayList
from braille.display.driver import BrailleDisplayDriver, _getDisplayDriver
from braille.display.gesture import BrailleDisplayGesture
from braille.regions.base import TextRegion
from logHandler import log

MAX_EVENTS = 200
"""How many gestures to keep. Enough for a long session of pressing keys, small enough that
`report` cannot flood the console."""

_secondary: Optional[BrailleDisplayDriver] = None
_events: Deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
_watching: bool = False
_originalHandleAck = None
"""`BrailleDisplayDriver._handleAck` as it was before `_isolateAck` replaced it."""


def _say(message: str) -> None:
	"""Print for the console and log, so a session leaves a trace in the NVDA log."""
	print(message)
	log.info(f"virtualDisplaySpike: {message}")


# --- Looking around before starting -------------------------------------------------


def primary() -> None:
	"""Report the display NVDA currently owns."""
	handler = braille.handler
	if handler is None:
		_say("No braille handler. NVDA is not far enough through startup.")
		return
	display = handler.display
	if display is None:
		_say("The braille handler has no display.")
		return
	dimensions = handler.displayDimensions
	_say(f"Primary driver: {display.name} ({display.description})")
	_say(f"Primary geometry: {display.numRows} rows of {display.numCols}, {display.numCells} cells")
	_say(f"Handler geometry: {dimensions.numRows} rows of {dimensions.numCols}")
	_say(f"Thread safe: {display.isThreadSafe}. Wants acknowledgements: {display.receivesAckPackets}")


def candidates() -> None:
	"""List the drivers NVDA could load, marking the primary.

	This says nothing about what hardware is attached. `BrailleDisplayDriver.check` returns
	True for most drivers whenever any serial port exists at all, so nearly every driver in
	the tree appears here on nearly every machine. Use `devices` to find real hardware.
	"""
	handler = braille.handler
	currentName = handler.display.name if handler and handler.display else None
	for name, description in getDisplayList():
		marker = " (currently in use as the primary)" if name == currentName else ""
		_say(f"{name}: {description}{marker}")


def devices(name: Optional[str] = None) -> None:
	"""List braille hardware that NVDA's detection can actually see right now.

	This is the one to use when deciding what to pass to `start`. It reports concrete
	`bdDetect.DeviceMatch` entries rather than driver classes, so a driver only appears if
	a device answering to it is attached.

	Bluetooth HID displays, which is how a Monarch presents, are found through the Bluetooth
	scan rather than the USB one, despite the latter's name covering HID as well. Both are
	reported here.

	:param name: a single driver to scan, or None for every driver that has detection data.
	"""
	import bdDetect

	# The device lists are cached for one core cycle, so a display connected a moment ago
	# may not be in them yet.
	if bdDetect.deviceInfoFetcher is not None:
		bdDetect.deviceInfoFetcher.invalidateCache()

	if name is not None:
		names = [name]
	else:
		names = [driverName for driverName, _description in getDisplayList()]

	found = 0
	for driverName in names:
		for label, lookup in (
			("usb or wired HID", bdDetect.getConnectedUsbDevicesForDriver),
			("bluetooth", bdDetect.getPossibleBluetoothDevicesForDriver),
		):
			try:
				matches = list(lookup(driverName))
			except LookupError:
				# This driver has no automatic detection data at all.
				continue
			except Exception:
				log.error(f"virtualDisplaySpike: {label} scan failed for {driverName}", exc_info=True)
				continue
			for match in matches:
				found += 1
				_say(f"{driverName} over {label}: {match.type} {match.id}")
				_say(f"   port: {match.port}")
	if not found:
		_say("No braille hardware detected. Check the display is on, paired and awake.")
	elif name is None:
		_say("Pass one of these driver names to start.")


def ports(name: str) -> None:
	"""List the ports a driver offers, for when `start` needs one naming explicitly.

	:param name: a driver name as printed by `candidates`.
	"""
	try:
		cls = _getDisplayDriver(name)
	except ImportError:
		_say(f"No driver named {name}.")
		return
	possible = cls.getPossiblePorts()
	if not possible:
		_say(f"{name} offers no ports. Call start with no port.")
		return
	for key, description in possible.items():
		_say(f"{key}: {description}")


def detection() -> None:
	"""Report whether NVDA's own automatic detection is running.

	This matters more than it looks. A live detector opens and closes candidate devices as
	it scans, so a display that is plainly present can still refuse to open for anything
	else. It is the first thing to rule out when a driver reports finding no display while
	`devices` shows the hardware.
	"""
	handler = braille.handler
	if handler is None:
		_say("No braille handler.")
		return
	configured = config.conf["braille"]["display"]
	detector = getattr(handler, "_detector", None)
	_say(f"Configured braille display setting: {configured!r}")
	_say(f"Automatic detection running: {detector is not None}")
	if detector is not None:
		_say("A running detector may be holding or cycling the device you are trying to open.")
		_say("Call pauseDetection() to stop it for this session, then try start again.")


def pauseDetection() -> None:
	"""Stop NVDA's automatic detection for this session.

	NVDA restarts it whenever the braille display setting is changed or a profile switches,
	so this is a diagnostic rather than a fix. It does not alter configuration.
	"""
	handler = braille.handler
	if handler is None:
		_say("No braille handler.")
		return
	if getattr(handler, "_detector", None) is None:
		_say("Automatic detection is not running.")
		return
	handler._disableDetection()
	_say("Stopped automatic detection. It will come back when the display setting changes.")


def probe(name: str, port: str = "auto") -> None:
	"""List the ports a driver would actually try, without starting it.

	`_getTryPorts` is the generator every driver iterates in its constructor. If it yields
	nothing, the driver's "no display found" error means it never made an attempt. If it
	yields something, the failure is at opening the port or at the device not answering, and
	`debugLogging` will show which.

	:param name: driver name.
	:param port: the port value to resolve, as passed to `start`.
	"""
	try:
		cls = _getDisplayDriver(name)
	except ImportError:
		_say(f"No driver named {name}.")
		return
	found = 0
	try:
		for match in cls._getTryPorts(port):
			found += 1
			_say(f"Would try: {match.type} {match.id}")
			_say(f"   port: {match.port}")
	except Exception:
		log.error(f"virtualDisplaySpike: probing {name} failed", exc_info=True)
		_say("Probing raised. See the log.")
		return
	if not found:
		_say(f"{name} would try nothing for port {port!r}. That is why it reports no display.")
	else:
		_say(f"{found} port(s) would be tried, so the failure is at opening or answering.")


def debugLogging(enable: bool = True) -> None:
	"""Raise NVDA's log level so swallowed exceptions become visible.

	Drivers log failed port attempts through `log.debugWarning`, which is invisible at the
	default level. That is why a driver reporting no display gives no clue why. Turn this on,
	run `start` again, and the real exception per port appears in the log.

	:param enable: True to log at debug level, False to go back to info.
	"""
	import logging

	log.setLevel(logging.DEBUG if enable else logging.INFO)
	_say(f"Log level set to {'debug' if enable else 'info'}.")


# --- Starting and stopping the secondary --------------------------------------------


def start(name: str, port: Optional[Any] = None, isolateAck: bool = True, force: bool = False) -> None:
	"""Construct a second driver alongside the one NVDA owns.

	This follows `BrailleHandler._setDisplay` exactly: `__new__`, then `__init__` through
	`callWithSupportedKwargs` so that a driver taking no port still works, then
	`initSettings`. Nothing here is privileged; that is the point being tested.

	:param name: driver name, as printed by `devices` or `candidates`.
	:param port: an explicit port. Either a string as printed by `ports`, or a
		`bdDetect.DeviceMatch`, which is what `BrailleDisplayDriver._getTryPorts` expects
		when a display has been detected rather than configured. Omitted means the
		configured port for that driver if there is one, and the driver's own default
		otherwise — which for `hidBrailleStandard` is "auto", covering USB and Bluetooth.
	:param isolateAck: keep the secondary's acknowledgement handling away from the braille
		handler's single ack timer. See `_isolateAck`. Pass False to watch the collision the
		plan predicts.
	:param force: allow starting the same driver the primary is using. Expected to fail at
		the port, and to confuse the driver's settings if it does not.
	"""
	global _secondary
	if _secondary is not None:
		_say(f"Already running {_secondary.name}. Call stop first.")
		return
	handler = braille.handler
	primaryName = handler.display.name if handler and handler.display else None
	if name == primaryName and not force:
		_say(f"{name} is already the primary. Two displays of one driver share a settings")
		_say("section and produce indistinguishable gestures. Pass force=True to try anyway.")
		return
	try:
		cls = _getDisplayDriver(name)
	except ImportError:
		_say(f"No driver named {name}.")
		return

	kwargs: dict[str, Any] = {}
	if port is not None:
		kwargs["port"] = port
		_say(f"Using the port given: {port!r}")
	else:
		try:
			kwargs["port"] = config.conf["braille"][name]["port"]
		except KeyError:
			_say(f"No port configured for {name}, so the driver's own default will be used.")
		else:
			_say(f"Using the port configured for {name}: {kwargs['port']!r}")
			_say("A stale value here is a common cause of failure. Pass port='auto' to ignore it.")

	if isolateAck:
		_isolateAck()
	try:
		driver = cls.__new__(cls)
		extensionPoints.callWithSupportedKwargs(driver.__init__, **kwargs)
		driver.initSettings()
	except Exception:
		log.error("virtualDisplaySpike: could not start the secondary", exc_info=True)
		_say(f"Could not start {name}. See the log for the traceback.")
		_say("A driver raising 'no display found' means every port it tried failed to answer.")
		_say(f"Call devices({name!r}) to see whether the hardware is visible at all.")
		if isolateAck:
			_restoreAck()
		return
	_secondary = driver
	_say(f"Started {name} as a secondary while the primary is {primaryName}.")
	info()


def info() -> None:
	"""Report the secondary, and the facts the driver design depends on."""
	driver = _secondary
	if driver is None:
		_say("No secondary running.")
		return
	import baseObject

	_say(f"Secondary driver: {driver.name} ({driver.description})")
	_say(f"Secondary geometry: {driver.numRows} rows of {driver.numCols}, {driver.numCells} cells")
	_say(f"Thread safe: {driver.isThreadSafe}. Wants acknowledgements: {driver.receivesAckPackets}")
	_say(f"Timeout: {driver.timeout} seconds")
	_say(f"Has its own scripts: {isinstance(driver, baseObject.ScriptableObject)}")
	_say(f"Has a gesture map: {driver.gestureMap is not None}")
	_say(f"Acknowledgement handling isolated: {_originalHandleAck is not None}")


def stop() -> None:
	"""Terminate the secondary and undo everything this module changed.

	Each step is guarded separately, so a driver that fails to terminate still gets dropped
	and the gesture watch still gets removed.
	"""
	global _secondary
	unwatch()
	driver = _secondary
	_secondary = None
	if driver is not None:
		try:
			driver.terminate()
			_say(f"Terminated {driver.name}.")
		except Exception:
			log.error("virtualDisplaySpike: error terminating the secondary", exc_info=True)
			_say("The secondary raised while terminating. See the log. It has been dropped anyway.")
	_restoreAck()


# --- Writing to the secondary --------------------------------------------------------


def _cellsForText(text: str) -> list[int]:
	"""Translate text to cells using NVDA's configured output table."""
	region = TextRegion(text)
	region.update()
	return list(region.brailleCells)


def writeCells(cells: list[int]) -> None:
	"""Write a raw cell array to the secondary, padded or truncated to fit.

	:param cells: dot patterns, one per cell, in row major order.
	"""
	driver = _secondary
	if driver is None:
		_say("No secondary running.")
		return
	total = driver.numCells
	cells = list(cells[:total])
	cells.extend([0] * (total - len(cells)))
	try:
		driver.display(cells)
	except Exception:
		log.error("virtualDisplaySpike: the secondary raised while displaying", exc_info=True)
		_say("The secondary raised while displaying. See the log.")


def write(text: str) -> None:
	"""Write text to the secondary.

	On a multi row display the text simply runs on across the rows: this writes cells, not
	regions, and none of the add-on's layout code is involved.

	:param text: what to show.
	"""
	writeCells(_cellsForText(text))


def ruler() -> None:
	"""Fill the secondary with a repeating digit ruler, for feeling cell positions.

	The point is to confirm the width the driver reports is the width the hardware has, and
	that cell zero is where it is expected to be.
	"""
	driver = _secondary
	if driver is None:
		_say("No secondary running.")
		return
	digits = "1234567890"
	text = (digits * (driver.numCells // len(digits) + 1))[: driver.numCells]
	write(text)
	_say(f"Wrote a ruler of {driver.numCells} cells.")


def rows() -> None:
	"""Label each row of the secondary with its own row number.

	This is the check behind the plan's stacking model: it confirms that a flat cell array
	really is row major on this hardware, and that row zero is the top one.
	"""
	driver = _secondary
	if driver is None:
		_say("No secondary running.")
		return
	cells: list[int] = []
	for rowIndex in range(driver.numRows):
		rowCells = _cellsForText(f"row {rowIndex}")[: driver.numCols]
		rowCells.extend([0] * (driver.numCols - len(rowCells)))
		cells.extend(rowCells)
	writeCells(cells)
	_say(f"Labelled {driver.numRows} rows.")


def clear() -> None:
	"""Blank the secondary."""
	driver = _secondary
	if driver is None:
		_say("No secondary running.")
		return
	writeCells([0] * driver.numCells)


def pokePrimary(text: str = "primary still works") -> None:
	"""Put a message on the primary, to check the secondary has not disturbed it.

	Worth calling after a spell of pressing keys on the secondary, which is when an
	acknowledgement collision would show itself as a primary that has stopped updating.

	:param text: the message to show.
	"""
	if braille.handler is None:
		_say("No braille handler.")
		return
	braille.handler.message(text)
	_say(f"Sent to the primary: {text}")


# --- Watching gestures ---------------------------------------------------------------


def _findDriverReference(gesture: BrailleDisplayGesture) -> Optional[str]:
	"""Find an attribute on a gesture holding a driver instance, if there is one.

	This is the question behind the plan's "one display per driver name" limit. Two displays
	of one driver produce identical `source` strings, so the only way to tell them apart
	would be a reference from the gesture back to the instance that made it. Most drivers do
	not keep one, but this checks rather than assumes.
	"""
	for name, value in vars(gesture).items():
		if isinstance(value, BrailleDisplayDriver):
			return f"{name}={value.name}@{id(value):x}"
	return None


def _onGesture(gesture: Any = None, **kwargs: Any) -> bool:
	"""Record braille display gestures. Registered on `inputCore.decide_executeGesture`.

	Always returns True: this watches, it never cancels. It must also never raise, since a
	decider that throws would break input for the whole session.
	"""
	try:
		if isinstance(gesture, BrailleDisplayGesture):
			secondaryName = _secondary.name if _secondary is not None else None
			_events.append(
				{
					"time": time.strftime("%H:%M:%S"),
					"source": gesture.source,
					"model": gesture.model,
					"id": gesture.id,
					"cellIndexes": gesture.cellIndexes,
					"identifiers": list(gesture.identifiers),
					"fromSecondary": gesture.source == secondaryName,
					"thread": threading.current_thread().name,
					"driverRef": _findDriverReference(gesture),
				},
			)
	except Exception:
		log.error("virtualDisplaySpike: error recording a gesture", exc_info=True)
	return True


def watch() -> None:
	"""Start recording braille display gestures from both displays."""
	global _watching
	if _watching:
		_say("Already watching.")
		return
	inputCore.decide_executeGesture.register(_onGesture)
	_watching = True
	_say("Watching. Press keys on both displays, then call report.")


def unwatch() -> None:
	"""Stop recording. Recorded events are kept; `clearEvents` discards them."""
	global _watching
	if not _watching:
		return
	inputCore.decide_executeGesture.unregister(_onGesture)
	_watching = False
	_say("Stopped watching.")


def clearEvents() -> None:
	"""Discard recorded gestures."""
	_events.clear()
	_say("Cleared recorded gestures.")


def report(count: int = 20) -> None:
	"""Print the most recent gestures, oldest first.

	:param count: how many to print.
	"""
	if not _events:
		_say("No gestures recorded. Call watch, then press a key on a display.")
		return
	recent = list(_events)[-count:]
	_say(f"{len(recent)} of {len(_events)} recorded gestures, oldest first:")
	for index, event in enumerate(recent):
		origin = "secondary" if event["fromSecondary"] else "primary or other"
		_say(f"{index}. {event['time']} {origin}: br({event['source']}):{event['id']}")
		if event["cellIndexes"]:
			_say(f"   cells addressed: {event['cellIndexes']}")
		_say(f"   thread: {event['thread']}, driver reference: {event['driverRef']}")
	summary()


def summary() -> None:
	"""Summarise what the recorded gestures say about the plan's assumptions."""
	if not _events:
		_say("Nothing recorded.")
		return
	sources = sorted({event["source"] for event in _events})
	threads = sorted({event["thread"] for event in _events})
	refs = sorted({str(event["driverRef"]) for event in _events})
	fromSecondary = sum(1 for event in _events if event["fromSecondary"])
	_say(f"Sources seen: {', '.join(sources)}")
	_say(f"Threads seen: {', '.join(threads)}")
	_say(f"Driver references seen: {', '.join(refs)}")
	_say(f"Gestures from the secondary: {fromSecondary} of {len(_events)}")
	if fromSecondary:
		_say("The secondary's keys reach NVDA. The plan's input assumption holds.")
	else:
		_say("Nothing arrived from the secondary. Its input path needs investigating.")


# --- Acknowledgement isolation -------------------------------------------------------


def _spikeHandleAck(self: BrailleDisplayDriver) -> None:
	"""Replacement for `BrailleDisplayDriver._handleAck` that leaves the primary alone.

	NVDA's version cancels `braille.handler.ackTimerHandle` and calls
	`braille.handler._writeCellsInBackground()`. There is one such timer, belonging to
	whichever display the handler owns, so a secondary acknowledging a packet would cancel
	the primary's timer and queue a write for the wrong device.

	Patching the base class rather than the instance is deliberate: drivers that override
	`_handleAck` reach this through `super()`, so a class level patch covers them too. The
	Focus is exactly such a driver, and its own override flushes cells queued while it was
	waiting, which still happens.

	The real virtual driver will need the same split, with a waitable timer per device
	rather than merely clearing the flag.
	"""
	if not self.receivesAckPackets:
		raise NotImplementedError("This display driver does not support ACK packet handling")
	if braille.handler is not None and self is braille.handler.display:
		return _originalHandleAck(self)
	self._awaitingAck = False
	return None


def _isolateAck() -> None:
	"""Install `_spikeHandleAck`. Safe to call when it is already installed."""
	global _originalHandleAck
	if _originalHandleAck is not None:
		return
	_originalHandleAck = BrailleDisplayDriver._handleAck
	BrailleDisplayDriver._handleAck = _spikeHandleAck
	_say("Acknowledgement handling isolated: a secondary can no longer cancel the primary's timer.")


def _restoreAck() -> None:
	"""Put NVDA's own `_handleAck` back. Safe to call when nothing is installed."""
	global _originalHandleAck
	if _originalHandleAck is None:
		return
	BrailleDisplayDriver._handleAck = _originalHandleAck
	_originalHandleAck = None
	_say("Acknowledgement handling restored to NVDA's own.")
