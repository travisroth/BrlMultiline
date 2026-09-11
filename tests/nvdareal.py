# BrlMultiline: running the add-on inside a real NVDA.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Import and exercise the add-on against a real NVDA, with no screen reader running.

The unit suite runs against `_stubs.py`, which is the right trade for arithmetic and for
lifecycle: it is fast, it needs no NVDA, and it can arrange states hardware would not give
you. What it cannot do is notice that NVDA's own API has moved, or that a function is being
called with arguments it does not take, or that a metaclass puts something somewhere other
than where the code looks for it. A stub agrees with whatever the code believes.

This is the other half. It loads NVDA's real modules, appends the add-on to the package paths
the way `addonHandler` does at runtime, and imports the add-on into it. Everything it reports
is of the form "this loads against the real NVDA and the functions it calls exist with the
signatures it uses". That is a large share of what breaks an add-on between NVDA releases,
and none of what a finger finds: nothing here touches hardware, wx, the event loop, or a
braille cell.

**It is safe to run while NVDA is running.** Separate process, and NVDA's own bootstrap sets
`disableAddons` and points the configuration path at its test directory, so this never reads
or writes the real configuration and never reaches a display.

**Use NVDA's interpreter, not this project's.** NVDA's virtual environment carries an editable
install that puts its `source` directory on the path, along with the compiled pieces —
liblouis, NVDAHelper, comtypes interfaces — that no amount of path arranging can substitute
for::

	C:/code/nvda/.venv/Scripts/python.exe tests/nvdareal.py

Point it at a checkout elsewhere with the `BRLMULTILINE_NVDA` environment variable.

**Why NVDA's own test bootstrap rather than our own.** `tests/unit/__init__.py` in the NVDA
source calls itself "ugly bootstrapping code" and it is, but the ugliness is load bearing:
config, languageHandler, appModuleHandler, vision, characterProcessing, speech, braille and
braille.input have to be initialised in that order, and importing them in any other order
walks into circular imports — `garbageHandler` against `config` against `baseObject`, then
`speech` against `tones` against `nvwave`, then `inputCore` against `gui` against `ui` against
`speech`. NVDA already encodes the order that works. It is loaded here by file path rather
than by importing `tests.unit`, because this project has a `tests` package of its own and
which one wins would otherwise depend on how the script was invoked.

What it has caught so far, none of which the stubs could:

1. `louisHelper.translate` needs the table list NVDA's own regions pass it, and answers.
2. A script's default gestures are collected by the metaclass into the mangled `__gestures`
   name and not into `_scriptDecoratorGestures`. Looking in the wrong one showed no bindings
   at all, which would have shipped commands that silently never bound.
3. A metaclass never sees a script attached with `setattr` after the class body, so a
   generated command cannot carry a default gesture.
4. The Monarch driver imports against the real `hidBrailleStandard`, which is how a reported
   regression was shown to be an unplugged display rather than a fault.

And one thing it cannot do, which is worth knowing before trusting its device report: see
`connectedBrailleDisplays`. A display NVDA is already driving is invisible to detection run
from outside NVDA, because identifying it means opening it and NVDA holds it exclusively.
`heldElsewhere` is the other half of that answer and both are printed together.
"""

import argparse
import importlib.util
import os
import sys
from pathlib import Path

DEFAULT_NVDA = Path("C:/code/nvda")
"""Where the NVDA source checkout is, when the environment does not say otherwise."""

NVDA_ENV = "BRLMULTILINE_NVDA"
"""Environment variable naming an NVDA source checkout to use instead."""

ADDON = Path(__file__).resolve().parent.parent / "addon"
"""This add-on's root, resolved before anything changes the working directory.

NVDA's bootstrap chdirs into its own `source`, because braille loads liblouis by a relative
path. Every path here is therefore absolute and computed at import time; a relative one would
mean something different after `bootstrap` than before it.
"""

_bootstrapped = False


def nvdaRoot() -> Path:
	""":return: the NVDA source checkout to run against.

	:raises SystemExit: if it is not there, with the two ways to fix it.
	"""
	root = Path(os.environ.get(NVDA_ENV, "")) if os.environ.get(NVDA_ENV) else DEFAULT_NVDA
	if not (root / "source" / "nvda.pyw").is_file():
		raise SystemExit(
			f"No NVDA source checkout at {root}.\n"
			f"Set {NVDA_ENV} to one, or clone NVDA to {DEFAULT_NVDA}.",
		)
	return root


def bootstrap(root: Path | None = None) -> None:
	"""Bring a real NVDA up far enough to import add-on code into it.

	Idempotent, so a script may call it without checking. Leaves the working directory inside
	NVDA's `source`, which is NVDA's own doing and is why `ADDON` is absolute.

	:param root: the NVDA checkout, or None to find one.
	:raises SystemExit: if this is not NVDA's own interpreter, or the checkout is unusable.
	"""
	global _bootstrapped
	if _bootstrapped:
		return
	root = root or nvdaRoot()
	try:
		import globalVars  # noqa: F401
	except ImportError:
		raise SystemExit(
			"NVDA's `source` is not importable, so this is not NVDA's interpreter. Run:\n"
			f"  {root}/.venv/Scripts/python.exe tests/nvdareal.py",
		) from None
	unit = root / "tests" / "unit" / "__init__.py"
	if not unit.is_file():
		raise SystemExit(f"No unit test bootstrap at {unit}; is this an NVDA checkout?")
	# Loaded by path, under a name of our own. Importing `tests.unit` would be ambiguous:
	# this project has a `tests` package too, and which one answers depends on how the script
	# was started. `submodule_search_locations` is what makes the relative import inside it
	# (`from .objectProvider import ...`) resolve, and what its own `__path__[0]` assignment
	# needs to exist.
	name = "_brlMultilineNvdaBootstrap"
	spec = importlib.util.spec_from_file_location(
		name,
		unit,
		submodule_search_locations=[str(unit.parent)],
	)
	module = importlib.util.module_from_spec(spec)
	sys.modules[name] = module
	spec.loader.exec_module(module)
	_bootstrapped = True


def loadDrivers(names: "list[str] | None" = None) -> dict:
	"""Import the add-on's braille display drivers into NVDA.

	Appends the add-on's driver directory to `brailleDisplayDrivers.__path__`, which is what
	`addonHandler` does for an installed add-on. Nothing else makes a driver in an add-on
	importable by the name NVDA knows it by, and the name matters: the drivers import each
	other absolutely, as `brailleDisplayDrivers.brlMultilineVirtual`.

	:param names: which drivers, or None for both of ours.
	:return: the imported modules by name.
	"""
	bootstrap()
	import brailleDisplayDrivers

	path = str(ADDON / "brailleDisplayDrivers")
	if path not in brailleDisplayDrivers.__path__:
		brailleDisplayDrivers.__path__.append(path)
	loaded = {}
	for name in names or ("brlMultilineMonarch", "brlMultilineVirtual"):
		loaded[name] = importlib.import_module(f"brailleDisplayDrivers.{name}")
	return loaded


def loadPlugin():
	"""Import the add-on's global plugin package into NVDA.

	:return: the `brlMultiline` package.
	"""
	bootstrap()
	import addonHandler

	# Outside an installed add-on NVDA refuses to set up translations for this code, raising
	# "Code does not belong to an addon package". That is a property of importing from a
	# script rather than from an add-on directory NVDA scanned, not a fault in the add-on, and
	# the underscore is already in builtins from NVDA's own bootstrap.
	addonHandler.initTranslation = lambda: None
	path = str(ADDON / "globalPlugins")
	if path not in sys.path:
		sys.path.insert(0, path)
	return importlib.import_module("brlMultiline")


def connectedBrailleDisplays() -> list:
	"""Ask NVDA what braille hardware it can match, the way its own detection does.

	**Read this with `heldElsewhere` beside it, or it will mislead you.** A braille display
	that NVDA is already driving does not appear here, because it cannot. `bdDetect` decides
	a HID device is a braille display by its usage page, `hwPortUtils` reads that by opening
	the device, and `hwIo.hid.Hid` opens exclusively — so while NVDA holds the display, an
	outside process enumerates it with no usage page at all and no match is made. The device
	is there; this cannot see what it is.

	So an empty answer means one of two quite different things, and only `heldElsewhere` tells
	them apart: nothing is plugged in, or something is plugged in and busy. "The display is
	not attached" is a claim that needs both.

	:return: one entry per match, as (driverName, protocol, id).
	"""
	bootstrap()
	import bdDetect

	found = []
	for lookup in (
		bdDetect.getDriversForConnectedUsbDevices,
		bdDetect.getDriversForPossibleBluetoothDevices,
	):
		for driverName, match in lookup():
			found.append((driverName, str(match.type), match.id))
	return found


def heldElsewhere() -> list:
	"""HID devices present but unreadable, which is what a display in use looks like.

	A device whose usage page could not be read is one another process has open exclusively.
	On this machine that is almost always NVDA itself driving the display, which is why an
	empty `connectedBrailleDisplays` while one of these is listed means the opposite of what
	it appears to mean.

	:return: one entry per unreadable device, as (hardwareID, devicePath).
	"""
	bootstrap()
	import bdDetect

	return [
		(device.get("hardwareID", "?"), device.get("devicePath", "?"))
		for device in bdDetect.deviceInfoFetcher.hidDevices
		if "HIDUsagePage" not in device
	]


def logPath(root: Path | None = None) -> Path:
	""":return: where NVDA run from source writes its log.

	The previous session is beside it as `nvda-old.log`, which is usually the one wanted: a
	problem is reported after the session that had it has been restarted.

	:param root: the NVDA checkout, or None to find one.
	"""
	return (root or nvdaRoot()) / "source" / "nvda.log"


def tailLog(lines: int = 40, root: Path | None = None) -> str:
	"""Read the end of NVDA's log.

	:param lines: how many lines.
	:param root: the NVDA checkout, or None to find one.
	:return: the last lines, or a note saying there is no log.
	"""
	path = logPath(root)
	if not path.is_file():
		return f"No log at {path}. NVDA has not been run from this checkout."
	text = path.read_text(encoding="utf-8", errors="replace").splitlines()
	return "\n".join(text[-lines:])


# --- The default checks ----------------------------------------------------------------
#
# Each returns a list of failures, empty when it passed, so that one broken check does not
# hide the rest. They are ordered so that the cheapest and most fundamental fails first: if
# the drivers do not import, nothing said about gestures is worth reading.


def checkDrivers() -> list:
	""":return: what is wrong with the drivers, empty if nothing."""
	failures = []
	drivers = loadDrivers()
	monarch = drivers["brlMultilineMonarch"].BrailleDisplayDriver
	# The capability set `graphics.py` duck types for. A rename here breaks graphics silently:
	# the add-on would decide the display cannot draw and quietly fall back to text.
	for name in (
		"graphicsSize",
		"newGraphicsBuffer",
		"setGraphicsOverlay",
		"clearGraphicsOverlay",
		"lastTouch",
		"lastRoutingPin",
	):
		if not hasattr(monarch, name):
			failures.append(f"the Monarch driver no longer publishes {name}")
	buffer = monarch.newGraphicsBuffer(object(), 6, 3)
	if (buffer.width, buffer.height) != (6, 3):
		failures.append(f"a sized graphics buffer came back {buffer.width} by {buffer.height}")
	return failures


def checkPlugin() -> list:
	""":return: what is wrong with the plugin, empty if nothing."""
	failures = []
	plugin = loadPlugin()
	from brlMultiline import graphics, graphicsMode  # noqa: F401
	from brlMultiline.panels import GraphicsPanel
	from brlMultiline.views import GraphicsPanel as reExported

	if GraphicsPanel is not reExported:
		failures.append("views re-exports a different GraphicsPanel than panels defines")
	if graphicsMode.labelCells("hi") == []:
		failures.append("braille translation answered nothing, so a caption would be silent")
	if not hasattr(plugin, "GlobalPlugin"):
		failures.append("the plugin package has no GlobalPlugin")
	failures.extend(_patchTargets())
	failures.extend(_captureTargets())
	return failures


def _captureTargets() -> list:
	""":return: what the image path reaches for in NVDA and did not find.

	The same argument as `_patchTargets`, for the other thing a stub suite cannot see. The
	capture is three calls into NVDA — the navigator object, `ScreenBitmap`, and the pixel
	fields it hands back — and a rename in any of them would reach a reader as a picture
	command that refuses everything, which looks exactly like pointing at the wrong thing.
	"""
	failures = []
	import api
	import screenBitmap

	if not hasattr(api, "getNavigatorObject"):
		failures.append("api has no getNavigatorObject, so there is nothing to draw")
	grabber = getattr(screenBitmap, "ScreenBitmap", None)
	if grabber is None:
		failures.append("screenBitmap has no ScreenBitmap, so the screen cannot be copied")
		return failures
	try:
		pixels = grabber(2, 2).captureImage(0, 0, 2, 2)
		pixel = pixels[0][0]
		for field in ("rgbRed", "rgbGreen", "rgbBlue"):
			if not hasattr(pixel, field):
				failures.append(f"a captured pixel has no {field}, so brightness cannot be read")
	except Exception as reason:
		failures.append(f"the screen would not be captured: {reason}")
	return failures


def _patchTargets() -> list:
	""":return: every patch naming something the real class does not have.

	The one thing a unit suite cannot check about a patch: the stubs have whatever this
	add-on decided they should have. A method renamed in NVDA shows up here rather than as a
	feature that silently stopped working — which is what a missing `_addFieldText` would be,
	since without it a document simply gets no glyphs and says nothing about why.
	"""
	from brlMultiline import patches

	failures = []
	for name, (owner, _replacement) in patches._replacements().items():
		attribute = patches._attributeFor(name)
		if not hasattr(owner, attribute):
			failures.append(f"{owner.__name__} has no {attribute} to patch, wanted for {name}")
	return failures


def checkGeometry() -> list:
	"""Check the cell to pin transform against both pitches, in real NVDA.

	The arithmetic is covered by the unit suite. What this adds is that it is being done with
	the real `SegmentRect` and the real `GraphicsSurface`, so a field renamed under either
	shows up here rather than in a hardware session.

	:return: what disagreed, empty if nothing.
	"""
	failures = []
	loadPlugin()
	from brlMultiline.graphics import GraphicsSurface
	from brlMultiline.layout import SegmentRect

	for numRows, perRow in ((8, 5), (10, 4)):
		surface = GraphicsSurface(
			driver=None,
			pinWidth=96,
			pinHeight=40,
			numRows=numRows,
			numCols=32,
			rowStart=1,
		)
		if surface.pinsPerRow != perRow:
			failures.append(f"at {numRows} rows a line is {surface.pinsPerRow} pins, expected {perRow}")
		# A claim on the member's own rows, offset by a band, must come back at its own origin.
		pins = surface.pinRectForCells(SegmentRect(row=1, col=0, numRows=numRows, numCols=32))
		if (pins.x, pins.y, pins.width, pins.height) != (0, 0, 96, 40):
			failures.append(f"at {numRows} rows a whole-display claim mapped to {pins}")
		if surface.cellForPin(0, 0) != (1, 0):
			failures.append(f"at {numRows} rows pin 0,0 mapped back to {surface.cellForPin(0, 0)}")
	return failures


def checkGestures() -> list:
	"""Check the graphics commands arrive bound.

	The metaclass collects a decorated script's gestures into the mangled `__gestures` name at
	class creation, and only for scripts in the class body. Both halves of that have already
	been got wrong once, and neither is visible to a stub.

	:return: what is unbound that should not be, empty if nothing.
	"""
	failures = []
	plugin = loadPlugin()
	bound = getattr(plugin.GlobalPlugin, "_GlobalPlugin__gestures", {})
	wanted = {
		"toggleGraphics",
		"graphicsZoomIn",
		"graphicsZoomOut",
		"graphicsPanUp",
		"graphicsPanDown",
		"graphicsPanLeft",
		"graphicsPanRight",
		"chartSelection",
		"drawPicture",
		"pictureStyle",
	}
	missing = wanted - set(bound.values())
	if missing:
		failures.append(f"these graphics commands arrived unbound: {', '.join(sorted(missing))}")
	for identifier, script in sorted(bound.items()):
		if script in wanted and not hasattr(plugin.GlobalPlugin, f"script_{script}"):
			failures.append(f"{identifier} is bound to {script}, which does not exist")
	return failures


CHECKS = (
	("drivers", checkDrivers),
	("plugin", checkPlugin),
	("geometry", checkGeometry),
	("gestures", checkGestures),
)


def runChecks() -> int:
	"""Run every check and report.

	:return: a process exit code, 0 if everything passed.
	"""
	failed = 0
	for name, check in CHECKS:
		try:
			failures = check()
		except Exception as error:  # noqa: BLE001 - the report is the point, not the traceback.
			import traceback

			print(f"ERROR {name}: {type(error).__name__}: {error}")
			traceback.print_exc()
			failed += 1
			continue
		if failures:
			failed += 1
			for failure in failures:
				print(f"FAIL  {name}: {failure}")
		else:
			print(f"ok    {name}")
	for driverName, protocol, identifier in connectedBrailleDisplays():
		print(f"      braille display detected: {driverName} over {protocol}, {identifier}")
	busy = heldElsewhere()
	for hardwareID, _path in busy:
		print(f"      device present but held by another process: {hardwareID}")
	if not connectedBrailleDisplays() and not busy:
		print("      no braille display detected, which is not a failure")
	print("all checks passed" if not failed else f"{failed} check(s) failed")
	return 1 if failed else 0


def main(argv: "list[str] | None" = None) -> int:
	""":return: a process exit code."""
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument("--log", nargs="?", type=int, const=40, help="print the last N lines of NVDA's log")
	parser.add_argument("--displays", action="store_true", help="list the braille hardware NVDA can see")
	args = parser.parse_args(argv)
	if args.log is not None:
		print(f"--- {logPath()} ---")
		print(tailLog(args.log))
		return 0
	if args.displays:
		print("matched by bdDetect:")
		for entry in connectedBrailleDisplays():
			print("  ", entry)
		print("present but held by another process, so unidentifiable from here:")
		for hardwareID, path in heldElsewhere():
			print("  ", hardwareID, path)
		return 0
	return runChecks()


if __name__ == "__main__":
	sys.exit(main())
