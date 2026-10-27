# BrlMultiline: the commands that put a chart or a picture on the display.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The commands that put a chart or a picture on the display, and what they keep between presses.

The plugin's scripts call into this; the drawing itself is `GraphicsMode`'s. Kept here: which
chart was picked last, the picture last captured and the drawing made from it, the style
pictures are drawn in, and the picture file still loading, if any.
"""

from __future__ import annotations

from typing import Callable, Optional, Protocol, TYPE_CHECKING

import addonHandler
import api
import gui
import ui
import wx
from logHandler import log

from . import chartDraw, chartMenu, chartSource, graphicsMode
from . import image as imageFigure, imagePins, imageSource

addonHandler.initTranslation()

if TYPE_CHECKING:
	from .graphicsMode import GraphicsMode


AFTER_DIALOG_MILLIS = 300
"""How long an answer to a dialog waits, so the focus coming back does not talk over it."""


def sayAfterDialog(text: str) -> None:
	"""Say something once a dialog has closed and the focus is back where it was.

	NVDA announces the focus returning from a dialog, which would cut off anything said at once.

	:param text: what to say.
	"""
	wx.CallLater(AFTER_DIALOG_MILLIS, ui.message, text)


def _pictureRoot(picture):
	""":return: the captured picture another was made from, or the picture itself."""
	while picture is not None and getattr(picture, "derivedFrom", None) is not None:
		picture = picture.derivedFrom
	return picture


class PictureHost(Protocol):
	"""What `PictureCommands` uses of the plugin. See docs/design/architecture.md, Module map."""

	graphicsMode: GraphicsMode

	@property
	def isTerminated(self) -> bool: ...

	def cancelPendingDrawings(self) -> None: ...


class PictureCommands:
	"""Charts and pictures put on the display, for the plugin's graphics scripts."""

	def __init__(self, plugin: PictureHost):
		"""
		:param plugin: the global plugin, for its graphics mode and to cancel other drawings.
		"""
		self.plugin = plugin

		self.lastChartKind = None
		"""Which chart the reader picked last time, so the list opens on it. Not remembered across
		sessions."""

		self.picture = None
		"""The pixels of the last thing the reader asked to be drawn, or None.

		Held so that changing the style redraws the same picture rather than copying the screen
		again, which may show something else by then.
		"""

		self.drawing = None
		"""The figure last made from `picture`, so a style key can tell whether it is still what
		the display shows rather than, say, a chart drawn since."""

		self.style = imageFigure.OUTLINES
		"""How pictures are being drawn, as a mode and whether it is reversed. Carried from one
		picture to the next within a session."""

		self._request = None
		"""The picture file being loaded for the last press, if one is; see `drawPicture`."""

	@property
	def _mode(self):
		return self.plugin.graphicsMode

	def isShowingPicture(self) -> bool:
		""":return: whether the held picture is what the display is showing."""
		mode = self._mode
		return self.picture is not None and mode.active and mode.source is self.drawing

	def cancelRequest(self) -> None:
		"""Forget a picture file still loading, so it is never drawn when it arrives."""
		self._request = None

	# --- Charts ---

	def chartSelection(self) -> None:
		"""Draw what the reader has selected as a tactile chart, of a type they choose.

		A routing press on the chart says what is under the finger. Asks which chart rather
		than guessing, since the same cells can mean different charts; the list holds only
		charts that will draw. Every failure says a reason the reader can act on.
		"""
		self.plugin.cancelPendingDrawings()
		if self._mode.drawingSize() is None:
			# Translators: reported when a drawing was asked for on a display that cannot draw.
			ui.message(_("This display cannot show graphics"))
			return
		if self.drawFocusedChart():
			return
		try:
			offers = chartMenu.offersFor(chartSource.gridFromFocus())
		except chartSource.NoNumbers as refusal:
			ui.message(str(refusal))
			return
		except Exception:
			log.error("BrlMultiline: could not read the selection to chart", exc_info=True)
			# Translators: reported when charting failed for a reason worth a log entry.
			ui.message(_("The chart could not be made, see the log"))
			return
		if not offers:
			# Translators: reported when nothing selected can be drawn as any chart.
			ui.message(_("There is nothing here that can be charted"))
			return
		if len(offers) == 1:
			# Nothing to choose between, so nothing to ask. The prompt exists because a
			# selection can mean several charts, not as a step on the way to every chart.
			self.drawChart(offers[0])
			return
		self._chooseChart(offers)

	def drawFocusedChart(self) -> bool:
		"""Draw the chart the reader is on, if they are on one: the application's own chart.

		Asked before the selection, and with no dialog, since the chart already says what it is.
		See `docs/design/excel-chart-plan.md`.

		:return: whether the reader was on a chart, whatever came of drawing it.
		"""
		try:
			chart = chartSource.chartFromFocus()
		except chartSource.CallCancelled:
			ui.message(_("The application did not answer in time; try again"))
			return True
		except Exception:
			log.debugWarning("Could not ask the focus for a chart", exc_info=True)
			return False
		if chart is None:
			return False
		try:
			definition = chart.definition()
			log.info(
				f"BrlMultiline: charting {definition.typeName or 'a chart'} {definition.title!r}, "
				f"{len(definition.series)} series of {len(definition.categories)} points",
			)
			offer = chartMenu.offerForChart(definition)
		except chartSource.NoNumbers as refusal:
			log.info(f"BrlMultiline: chart refused: {refusal}")
			ui.message(str(refusal))
			return True
		except chartSource.CallCancelled:
			ui.message(_("The application did not answer in time; try again"))
			return True
		except Exception:
			log.error("BrlMultiline: could not read the chart", exc_info=True)
			ui.message(_("The chart could not be made, see the log"))
			return True
		self.drawChart(offer, remember=False)
		return True

	def _chooseChart(self, offers: list) -> None:
		"""Ask which of several charts to draw, then draw it.

		The only place on this path that uses wx; what is on offer is decided in `chartMenu`.

		:param offers: the charts that can be drawn, in the order to offer them.
		"""
		opening = 0
		for index, offer in enumerate(offers):
			if offer.key == self.lastChartKind:
				opening = index
				break

		def ask():
			# prePopup before the dialog is made and postPopup after it has gone, which is
			# how NVDA opens one: it raises its own frame first so the dialog can take the
			# focus, and puts things back afterwards.
			gui.mainFrame.prePopup()
			try:
				dialog = wx.SingleChoiceDialog(
					gui.mainFrame,
					# Translators: the message of a dialog asking which kind of chart to draw
					# from the cells the reader has selected.
					_("Which chart?"),
					# Translators: the title of a dialog asking which kind of chart to draw
					# from the cells the reader has selected.
					_("Chart the selection"),
					[offer.label for offer in offers],
				)
				try:
					dialog.SetSelection(opening)
					if dialog.ShowModal() != wx.ID_OK:
						return
					chosen = dialog.GetSelection()
				finally:
					dialog.Destroy()
			finally:
				gui.mainFrame.postPopup()
			if 0 <= chosen < len(offers):
				self.drawChart(offers[chosen], say=sayAfterDialog)

		wx.CallAfter(ask)

	def drawChart(self, offer, say: Optional[Callable] = None, remember: bool = True) -> None:
		"""Draw one of the charts on offer and put it on the display.

		The size is read again here, since the display may have changed while a dialog was open.

		:param offer: what to draw, from `chartMenu.offersFor`.
		:param say: speaks the answer, `ui.message` if not given. `sayAfterDialog` when the
			chart was chosen in the dialog.
		:param remember: whether this is the kind to open the chart dialog on next time. Not for
			an application's own chart, which was never chosen in the dialog.
		"""
		say = say or ui.message
		# Again here, for a picture pressed for while the chart dialog was open.
		self.plugin.cancelPendingDrawings()
		mode = self._mode
		size = mode.drawingSize()
		if size is None:
			# Translators: reported when a drawing was asked for on a display that cannot draw.
			say(_("This display cannot show graphics"))
			return
		try:
			drawing = offer.draw(mode.newBuffer, size[0], size[1], graphicsMode.labelCells)
		except (chartSource.NoNumbers, chartDraw.ChartRefused) as refusal:
			# Logged as well as said: a refusal said over the focus coming back was heard as
			# nothing happening, and the log had nothing to show for it either.
			log.info(f"BrlMultiline: {offer.key} chart refused: {refusal}")
			say(str(refusal))
			return
		except Exception:
			log.error("BrlMultiline: could not chart the selection", exc_info=True)
			# Translators: reported when charting failed for a reason worth a log entry.
			say(_("The chart could not be made, see the log"))
			return
		if remember:
			self.lastChartKind = offer.key
		if not mode.enter(drawing):
			say(mode.lastError or _("The drawing could not be shown"))
			return
		say(mode.describe())

	# --- Pictures ---

	def drawPicture(self) -> None:
		"""Put whatever the reader is pointing at on the pins as a tactile picture.

		The navigator object, whatever its role, copied off the screen. A web image whose
		address the browser gives is loaded from its file instead, in the background, which
		gives full size and works with the screen curtain on; if it will not load, the screen
		copy is used.
		"""
		obj = None
		try:
			obj = api.getNavigatorObject()
		except Exception:
			log.debugWarning("BrlMultiline: no navigator object to draw", exc_info=True)
		address = imageSource.addressOf(obj) if imagePins.hasPillow() else None
		if address is None:
			self.plugin.cancelPendingDrawings()
			self._draw(again=False)
			return
		# Only the last press counts. A reader who presses again, or moves on and presses on
		# another picture, while a slow file is still loading must not have the first one
		# arrive afterwards and replace what they asked for since.
		request = object()
		self._request = request

		def arrived(picture, why):
			# The request is cancelled at shutdown too; checking both is for a file that arrives
			# while the plugin is part way through coming down.
			if self.plugin.isTerminated or self._request is not request:
				return
			self._request = None
			if picture is not None:
				self._draw(again=False, capture=lambda: picture)
				return
			log.debug(f"BrlMultiline: a picture's own file did not load, copying the screen: {why}")
			self._draw(again=False, capture=lambda: imageSource.captureNavigator(obj))

		imageSource.fetchPicture(address, imageSource.nameFor(obj), arrived)

	def drawClipboardPicture(self) -> None:
		"""Draw an image copied to the clipboard, or an image file copied in File Explorer.

		Works with the screen curtain on and gives the image at its own size.
		"""
		# A newer request than any picture file still loading, which must not land on top.
		self.plugin.cancelPendingDrawings()
		self._draw(again=False, capture=lambda: imageSource.captureClipboard())

	def drawOnlyColour(self) -> None:
		"""Redraw the picture with only the colour under the last routing press, or undo that.

		Draws that colour alone in the current style; pressed again, every colour comes back.
		Keeps the reader's zoom and place, as a change of style does.
		"""
		picture = self.picture
		if not self.isShowingPicture():
			ui.message(_("There is no picture to change"))
			return
		if picture.derivedFrom is not None:
			self._draw(
				again=True,
				picture=picture.derivedFrom,
				# Translators: reported when a picture will not draw with all its colours back at
				# the part of it the reader has magnified.
				cannotHere=_("All the colours will not draw the part you are on"),
			)
			return
		if picture.colour is None:
			# Translators: reported when only one colour of a picture was asked for, and the
			# picture was captured without its colours, which needs the Pillow library.
			ui.message(_("This picture has no colours to choose from; that needs the Pillow library"))
			return
		if picture.touched is None:
			# Translators: reported when only one colour of a picture was asked for before any
			# colour had been touched. Says how to choose one.
			ui.message(_("Press a routing key on the colour you want first"))
			return
		paper = picture.paper
		fromPaper = max(abs(a - b) for a, b in zip(picture.touched, paper)) if paper is not None else 255
		if fromPaper < imagePins.INK_FROM_PAPER:
			# The background colour: the press almost certainly missed.
			# Translators: reported when only one colour of a picture was asked for and the colour
			# last touched is the background. Says how to choose a colour that is drawn.
			ui.message(_("That is the background; press a routing key on a line or shape first"))
			return
		chosen = picture.ofOneColour(picture.touched, picture.name)
		if chosen is None:
			ui.message(_("The picture could not be drawn, see the log"))
			return
		self._draw(
			again=True,
			picture=chosen,
			# Translators: reported when one colour of a picture will not draw at the part of it
			# the reader has magnified, usually because that colour is not there.
			cannotHere=_("That colour will not draw the part you are on"),
		)

	def cycleStyle(self) -> None:
		"""Draw the same picture the next way, without copying the screen again.

		Cycles through outlines, single lines, brightness and brightness reversed, since which
		reads best depends on the picture. Uses the pixels kept from the first press.
		"""
		self.chooseStyle(imageFigure.nextStyle(*self.style))

	def chooseStyle(self, style: tuple) -> None:
		"""Draw the picture up in one style, the one the cycling key or a direct key asked for.

		:param style: one of `image.STYLES`.
		"""
		if not self.isShowingPicture():
			# Translators: reported when the style key was pressed and no picture is up.
			ui.message(_("There is no picture to change"))
			return
		if tuple(style) == tuple(self.style):
			# Already drawn that way. Composing it again would cost a moment and change nothing
			# under the hand, so this only says what is there.
			ui.message(self._mode.describe())
			return
		# Passed in rather than set here, and kept only if it draws, so a refused style is not
		# left chosen.
		self._draw(again=True, style=tuple(style))

	def captureSizeWords(self) -> str:
		""":return: how big the capture was, when that is what answered the reader's question.

		Said on a refused zoom of a picture, since what limits zoom is how many screen pixels
		were captured. Empty unless the drawing is the captured picture.
		"""
		if self.picture is None or self._mode.source is not self.drawing:
			return ""
		size = imageSource.sizeWords(self.picture)
		if not size:
			return ""
		# Translators: added when a picture will not magnify any further, saying how many
		# screen pixels were captured, which is what limits it. The placeholder is a size
		# such as "160 by 24".
		return ", " + _("captured at {size}").format(size=size)

	def _draw(
		self,
		again: bool,
		capture=None,
		picture=None,
		cannotHere: "str | None" = None,
		style: "tuple | None" = None,
	) -> None:
		"""Capture if needed, compose, and put a picture on the display.

		The size is read here, since the drawing's rectangle can change between presses.

		:param again: whether to re-use the pixels already captured rather than copying the
			screen. True for a change of style, False for a new picture.
		:param capture: where a new picture comes from, `imageSource.captureNavigator` if None.
		:param picture: a picture to draw in place of the one held, made from it -- one colour
			of it, or the original back again. Drawn keeping the reader's place.
		:param cannotHere: what to say if that keeps the place and the place will not draw.
		:param style: a style to draw in, kept as the style only if the drawing is shown. The
			style already chosen if None.
		"""
		if style is None:
			style = self.style
		mode = self._mode
		# Whatever the reader has already decided about the braille line beside the drawing.
		# Asking for the default instead would put the line back on a panel they had given
		# whole to the picture, and shrink the drawing, while claiming to change its style.
		# None for a first drawing, which the mode decides: see `GraphicsMode.openingTextLines`.
		textLines = mode.textLines if mode.active else None
		size = mode.drawingSize(textLines)
		if size is None:
			# Translators: reported when a drawing was asked for on a display that cannot draw.
			ui.message(_("This display cannot show graphics"))
			return
		# Held locally until the whole of this has worked, so the cached pixels never change
		# while the drawing on the display is still the previous picture's.
		captured = picture if picture is not None else self.picture
		try:
			if not again or captured is None:
				captured = capture() if capture is not None else imageSource.captureNavigator()
			drawing = imageFigure.figureFor(
				mode.newBuffer,
				captured,
				size[0],
				size[1],
				style[0],
				style[1],
			)
		except imagePins.ImageRefused as refusal:
			# Every refusal names what went wrong, so the reader can tell whether to move, to
			# change the style, or to give up.
			ui.message(str(refusal))
			return
		except Exception:
			log.error("BrlMultiline: could not draw the picture", exc_info=True)
			# Translators: reported when drawing a picture failed for a reason worth a log entry.
			ui.message(_("The picture could not be drawn, see the log"))
			return
		# A change of style, or one colour, of the picture already up keeps the reader's zoom
		# and place, which `enter` would reset.
		keepingPlace = (
			again
			and mode.active
			and self.picture is not None
			and _pictureRoot(captured) is _pictureRoot(self.picture)
			and mode.source is self.drawing
			and self.drawing is not None
		)
		if keepingPlace:
			if not mode.replaceSource(drawing):
				ui.message(
					cannotHere
					# Translators: reported when a picture will not draw in the style just asked
					# for, at the part of it the reader has magnified.
					or _("This style will not draw the part you are on"),
				)
				return
		elif not mode.enter(drawing, textLines=textLines):
			ui.message(mode.lastError or _("The drawing could not be shown"))
			return
		# Committed together, so the pixels and the drawing made from them can never be two
		# different pictures.
		self.picture = captured
		self.drawing = drawing
		self.style = tuple(style)
		ui.message(mode.describe())
