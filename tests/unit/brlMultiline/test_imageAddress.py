# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for drawing a web image from its own file rather than from the screen.

Found by a reader: the way to get an image at its own size was to route the mouse to it,
right-click and choose Copy image, and the right-click was unreliable enough -- the menu did
not open, or opened without focus -- to make the clipboard command hard to use at all. Chrome
turned out to say where the image's file is, as the `src` IAccessible2 attribute, which NVDA
already reads. So the picture command asks, loads the file in the background, and falls back to
the screen when it cannot.

The network is never touched here. `_download` and the background thread are stood in for.
"""

import base64
import io
import types
import unittest

from ._stubs import FakeHandler, callAfterQueue, flashedMessages, installStubs, resetPluginState

installStubs()

from . import test_plugin  # noqa: E402

from brlMultiline import imagePins, imageSource  # noqa: E402

needsPillow = unittest.skipIf(imagePins._Image is None, "Pillow is not installed")

if imagePins._Image is not None:
	from PIL import Image, ImageDraw
else:
	Image = ImageDraw = None

LOGO = "https://www.accessibilitypartners.com/wp-content/themes/genesis-accessibility/images/logo.png"


def png(width=325, height=97):
	""":return: the bytes of a PNG with a dark bar in it."""
	image = Image.new("RGB", (width, height), (255, 255, 255))
	ImageDraw.Draw(image).rectangle((20, 20, width - 20, height - 20), fill=(0, 0, 0))
	stored = io.BytesIO()
	image.save(stored, "PNG")
	return stored.getvalue()


def graphic(attributes=None, document=None, name="Logo for Accessibility Partners"):
	""":return: an object shaped like Chrome's image in browse mode."""
	obj = types.SimpleNamespace(
		name=name,
		description="",
		role="graphic",
		location=(10, 20, 325, 97),
		states=set(),
		IA2Attributes=attributes if attributes is not None else {"tag": "img", "src": LOGO},
	)
	if document is not None:
		obj.treeInterceptor = types.SimpleNamespace(documentConstantIdentifier=document)
	return obj


class TestFindingTheAddress(unittest.TestCase):
	def test_chromesSrcAttributeIsTheAddress(self):
		"""What Chrome answered in NVDA's console, on the logo this was found on."""
		self.assertEqual(imageSource.addressOf(graphic()), LOGO)

	def test_somethingWithoutOneHasNone(self):
		self.assertIsNone(imageSource.addressOf(graphic({"tag": "canvas"})))

	def test_nothingAtAllHasNone(self):
		self.assertIsNone(imageSource.addressOf(None))

	def test_anObjectThatWillNotSayHasNone(self):
		class Broken:
			@property
			def IA2Attributes(self):
				raise RuntimeError("COM went away")

		self.assertIsNone(imageSource.addressOf(Broken()))

	def test_aRelativeAddressIsResolvedAgainstThePage(self):
		found = imageSource.addressOf(
			graphic({"src": "images/logo.png"}, document="https://example.com/about/index.html"),
		)
		self.assertEqual(found, "https://example.com/about/images/logo.png")

	def test_aRelativeAddressWithNoPageIsLeftToTheScreen(self):
		self.assertIsNone(imageSource.addressOf(graphic({"src": "images/logo.png"})))

	def test_anEmbeddedImageIsAnAddress(self):
		self.assertTrue(imageSource.addressOf(graphic({"src": "data:image/png;base64,AAAA"})))

	def test_aFileAddressIsNotFetched(self):
		"""A page can name a network share as an image, and opening one offers the reader's
		Windows login to whoever runs it. The browser would not draw it either."""
		self.assertIsNone(imageSource.addressOf(graphic({"src": "file://attacker/share/x.png"})))

	def test_aBrowserOnlyAddressIsNotFetched(self):
		self.assertIsNone(imageSource.addressOf(graphic({"src": "blob:https://example.com/1234"})))


class TestReadingTheAddress(unittest.TestCase):
	def setUp(self):
		self.asked = []
		self.real = imageSource._download
		imageSource._download = self.download
		self.addCleanup(setattr, imageSource, "_download", self.real)
		self.served = b"file bytes"

	def download(self, address):
		self.asked.append(address)
		return self.served

	def test_anEmbeddedImageIsDecodedWithoutTheNetwork(self):
		raw = imageSource.readAddress("data:image/png;base64," + base64.b64encode(b"\x89PNG").decode())
		self.assertEqual(raw, b"\x89PNG")
		self.assertEqual(self.asked, [])

	def test_aPercentEncodedOneToo(self):
		self.assertEqual(imageSource.readAddress("data:image/svg+xml,%3Csvg%3E"), b"<svg>")

	def test_aWebAddressIsDownloaded(self):
		self.assertEqual(imageSource.readAddress(LOGO), b"file bytes")
		self.assertEqual(self.asked, [LOGO])

	def test_anythingElseIsRefused(self):
		with self.assertRaises(imagePins.ImageRefused):
			imageSource.readAddress("file:///C:/Windows/win.ini")

	def test_theOpenerSpeaksOnlyHttp(self):
		"""So a server cannot redirect a download to a file or to FTP."""
		opener = imageSource._opener()
		self.assertEqual(set(opener.handle_open), {"http", "https"})


@needsPillow
class TestLoadingInTheBackground(unittest.TestCase):
	def setUp(self):
		callAfterQueue.discard()
		self.started = []
		self.realStart = imageSource._startBackground
		self.realDownload = imageSource._download
		imageSource._startBackground = self.started.append
		self.addCleanup(setattr, imageSource, "_startBackground", self.realStart)
		self.addCleanup(setattr, imageSource, "_download", self.realDownload)
		self.results = []

	def done(self, picture, why):
		self.results.append((picture, why))

	def test_nothingHappensOnTheMainThread(self):
		"""The work is handed to a thread; nothing is loaded or answered until it runs."""
		imageSource._download = lambda address: png()
		imageSource.fetchPicture(LOGO, "logo", self.done)
		self.assertEqual(len(self.started), 1)
		self.assertEqual(self.results, [])

	def test_thePictureComesBackThroughTheMainThread(self):
		imageSource._download = lambda address: png()
		imageSource.fetchPicture(LOGO, "logo", self.done)
		self.started[0]()
		self.assertEqual(self.results, [], "not from the loading thread itself")
		callAfterQueue.flush()
		picture, why = self.results[0]
		self.assertIsNone(why)
		self.assertEqual((picture.width, picture.height), (325, 97))
		self.assertEqual(picture.name, "logo")
		self.assertFalse(picture.fromScreen)

	def test_aFailureComesBackWithItsReason(self):
		def refuse(address):
			raise OSError("no network")

		imageSource._download = refuse
		imageSource.fetchPicture(LOGO, "logo", self.done)
		self.started[0]()
		callAfterQueue.flush()
		self.assertIsNone(self.results[0][0])
		self.assertIsInstance(self.results[0][1], OSError)

	def test_somethingPillowCannotReadIsAFailure(self):
		"""An SVG logo, for instance. The screen capture has what the browser drew of it."""
		imageSource._download = lambda address: b"<svg xmlns='http://www.w3.org/2000/svg'/>"
		imageSource.fetchPicture(LOGO, "logo", self.done)
		self.started[0]()
		callAfterQueue.flush()
		self.assertIsNone(self.results[0][0])
		self.assertIsInstance(self.results[0][1], imagePins.ImageRefused)


@needsPillow
class TestAFileThatDecodesTooLarge(unittest.TestCase):
	"""The download limit bounds the file, not the memory decoding it takes.

	Found in review: a few kilobytes of PNG can declare a picture of a hundred million pixels,
	and the size was checked only after the pixels had been decoded. The limits are lowered
	here so the tests stay small; what is tested is the order of the checks, not the numbers.
	"""

	def setUp(self):
		self.saved = (imageSource.DECODE_PIXELS, Image.MAX_IMAGE_PIXELS)
		self.addCleanup(self.restore)

	def restore(self):
		imageSource.DECODE_PIXELS, Image.MAX_IMAGE_PIXELS = self.saved

	def encoded(self, width, height, kind):
		image = Image.new("RGB", (width, height), (255, 255, 255))
		ImageDraw.Draw(image).rectangle((20, 20, width // 2, height // 2), fill=(0, 0, 0))
		stored = io.BytesIO()
		image.save(stored, kind)
		return stored.getvalue()

	def test_aFileDeclaringTooManyPixelsIsRefusedWithoutDecoding(self):
		imageSource.DECODE_PIXELS = 2_000_000
		raw = self.encoded(2000, 1500, "PNG")
		decoded = []
		realLoad = Image.Image.load

		def load(image):
			decoded.append(image.size)
			return realLoad(image)

		Image.Image.load = load
		self.addCleanup(setattr, Image.Image, "load", realLoad)
		with self.assertRaises(imageSource.ImageRefused) as refused:
			imageSource.pictureFromBytes(raw, "big")
		self.assertIn("2000 by 1500, too large", str(refused.exception))
		self.assertEqual(decoded, [], "the pixels were decoded before the size was checked")

	def test_aLargeJpegIsDecodedSmallerInsteadOfRefused(self):
		"""A photograph is the ordinary large picture, and a JPEG can be decoded at a half or a
		quarter for nothing, so its full size does not count against the limit."""
		imageSource.DECODE_PIXELS = 4_000_000
		picture = imageSource.pictureFromBytes(self.encoded(4000, 3000, "JPEG"), "photo")
		self.assertIsNotNone(picture)
		with self.assertRaises(imageSource.ImageRefused):
			imageSource.pictureFromBytes(self.encoded(4000, 3000, "PNG"), "the same, not a JPEG")

	def test_pillowsOwnWarningIsARefusal(self):
		"""Pillow only warns below twice its limit, and a warning does not stop anything."""
		Image.MAX_IMAGE_PIXELS = 500 * 500
		with self.assertRaises(imageSource.ImageRefused):
			imageSource.pictureFromBytes(self.encoded(600, 600, "PNG"), "suspicious")

	def test_anOrdinaryPictureStillLoads(self):
		picture = imageSource.pictureFromBytes(png(), "logo")
		self.assertEqual((picture.width, picture.height), (325, 97))


@needsPillow
class TestThePictureCommand(unittest.TestCase):
	"""The plugin's side: file first, screen when the file will not come."""

	def setUp(self):
		resetPluginState()
		import api
		import braille

		callAfterQueue.discard()
		braille.handler = FakeHandler(test_plugin.MONARCH_ROWS, test_plugin.MONARCH_COLS)
		self.plugin = test_plugin.GlobalPlugin()
		self.addCleanup(self.tidy)
		self.mode = test_plugin.TestDrawingThePictureHere.FakeMode()
		self.plugin.graphicsMode = self.mode
		self.navigator = graphic()
		self.realNavigator = api.getNavigatorObject
		api.getNavigatorObject = lambda: self.navigator
		self.screens = []
		self.realCapture = imageSource.captureNavigator
		imageSource.captureNavigator = self.screen
		self.realStart = imageSource._startBackground
		self.realDownload = imageSource._download
		self.started = []
		imageSource._startBackground = self.started.append
		imageSource._download = lambda address: png()
		flashedMessages.clear()

	def tidy(self):
		import api

		api.getNavigatorObject = self.realNavigator
		imageSource.captureNavigator = self.realCapture
		imageSource._startBackground = self.realStart
		imageSource._download = self.realDownload
		callAfterQueue.discard()
		try:
			if not self.plugin._terminated:
				self.plugin.terminate()
		except Exception:
			pass

	def screen(self, obj=None):
		self.screens.append(obj)
		greys = bytearray(0 if 30 <= x < 60 else 255 for y in range(40) for x in range(96))
		picture = imagePins.Picture(greys, 96, 40, "from the screen")
		picture.fromScreen = True
		return picture

	def finish(self):
		for work in self.started:
			work()
		self.started.clear()
		callAfterQueue.flush()

	def test_theFileIsDrawnAndTheScreenIsNotRead(self):
		self.plugin.script_drawPicture(None)
		self.assertEqual(self.mode.shown, [], "nothing until the file has loaded")
		self.finish()
		self.assertEqual(len(self.mode.shown), 1)
		self.assertEqual(self.screens, [])
		self.assertFalse(self.plugin._picture.fromScreen)
		self.assertEqual((self.plugin._picture.width, self.plugin._picture.height), (325, 97))

	def test_aFileThatWillNotLoadFallsBackToTheScreen(self):
		def refuse(address):
			raise OSError("no network")

		imageSource._download = refuse
		self.plugin.script_drawPicture(None)
		self.finish()
		self.assertEqual(len(self.mode.shown), 1)
		self.assertTrue(self.plugin._picture.fromScreen)

	def test_andTheScreenIsCopiedForTheObjectThatWasPressedOn(self):
		"""The navigator may have moved while the file was failing to load."""

		def refuse(address):
			raise OSError("no network")

		imageSource._download = refuse
		pressedOn = self.navigator
		self.plugin.script_drawPicture(None)
		self.navigator = graphic(name="something else")
		self.finish()
		self.assertIs(self.screens[0], pressedOn)

	def test_somethingWithNoAddressIsCopiedAtOnce(self):
		self.navigator = graphic({"tag": "canvas"})
		self.plugin.script_drawPicture(None)
		self.assertEqual(self.started, [])
		self.assertEqual(len(self.mode.shown), 1)

	def test_onlyTheLastPressCounts(self):
		"""A slow file arriving after the reader has asked for something since is dropped."""
		self.plugin.script_drawPicture(None)
		first = self.started[:]
		self.started.clear()
		self.navigator = graphic({"tag": "canvas"})
		self.plugin.script_drawPicture(None)
		shownNow = len(self.mode.shown)
		for work in first:
			work()
		callAfterQueue.flush()
		self.assertEqual(len(self.mode.shown), shownNow)

	def test_withoutPillowTheScreenIsCopiedAtOnce(self):
		"""Nothing could decode the file, so nothing is fetched."""
		saved = imagePins._Image
		imagePins._Image = None
		self.addCleanup(setattr, imagePins, "_Image", saved)
		self.plugin.script_drawPicture(None)
		self.assertEqual(self.started, [])
		self.assertEqual(len(self.screens), 1)

	def test_aClipboardDrawingIsNotReplacedByALateFile(self):
		self.plugin.script_drawPicture(None)
		imageSource.captureClipboard, real = (lambda: self.screen()), imageSource.captureClipboard
		self.addCleanup(setattr, imageSource, "captureClipboard", real)
		self.plugin.script_drawClipboardPicture(None)
		shownNow = len(self.mode.shown)
		self.finish()
		self.assertEqual(len(self.mode.shown), shownNow)

	def test_aChartChosenWhileTheFileLoadsIsNotReplaced(self):
		"""Found in review: the chart went up, then the late picture went up over it."""
		import types

		chart = types.SimpleNamespace(name="chart")
		offer = types.SimpleNamespace(key="bar", draw=lambda *args: chart)
		self.plugin.script_drawPicture(None)
		self.plugin.drawChart(offer)
		self.finish()
		self.assertEqual(self.mode.shown, [chart])

	def test_turningTheDrawingOffDropsTheFile(self):
		"""The reader took the drawing off while the file loaded; it must not come back by itself."""
		self.mode.active = True
		self.plugin.script_drawPicture(None)
		self.plugin.script_toggleGraphics(None)
		self.finish()
		self.assertFalse(self.mode.active)
		self.assertEqual(self.mode.shown, [])

	def test_aFileArrivingAfterShutdownDrawsNothing(self):
		"""Found in review: the late file drew on a display NVDA had already let go of."""
		self.plugin.script_drawPicture(None)
		self.plugin.terminate()
		self.finish()
		self.assertEqual(self.mode.shown, [])

	def test_aFileArrivingPartWayThroughShutdownDrawsNothing(self):
		"""Teardown sets the flag before anything else, so a file landing between that and the
		request being dropped is still refused."""
		self.plugin.script_drawPicture(None)
		self.plugin._terminated = True
		self.finish()
		self.assertEqual(self.mode.shown, [])
