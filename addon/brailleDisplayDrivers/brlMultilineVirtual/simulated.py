# BrlMultiline: a display that is not there.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A member of the composite with no hardware behind it, for testing without a display.

Constructed by the composite, never by NVDA, so it is not in NVDA's display list. It keeps what it
is shown, can draw when it imitates a Monarch, and raises keys when the viewer or a keyboard layer
presses them. See docs/design/braille-viewer-plan.md, The simulated member.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import NamedTuple

import braille.display.driver
import inputCore
from braille.display.gesture import BrailleDisplayGesture
from logHandler import log

from .virtualLayout import SIMULATED_DRIVER_NAMES

try:
	import addonHandler

	addonHandler.initTranslation()
except Exception:
	# Translation is a nicety; failing to set it up must not cost the user their braille.
	log.debugWarning("BrlMultiline: could not initialise translations", exc_info=True)


class Model(NamedTuple):
	"""A display the simulated member can imitate."""

	id: str
	numRows: int
	numCols: int
	pinWidth: int = 0
	"""Pin columns, or 0 for a model that cannot draw."""

	pinHeight: int = 0


MONARCH_8 = Model("monarch8", 8, 32, 96, 40)
MONARCH_10 = Model("monarch10", 10, 32, 96, 40)
FOCUS_80 = Model("focus80", 1, 80)
CELLS_40 = Model("cells40", 1, 40)
CUSTOM = "custom"
"""The model whose rows and columns are the `customRows` and `customCols` settings."""

MODELS = {model.id: model for model in (MONARCH_8, MONARCH_10, FOCUS_80, CELLS_40)}
DEFAULT_MODELS = {SIMULATED_DRIVER_NAMES[0]: MONARCH_8.id, SIMULATED_DRIVER_NAMES[1]: FOCUS_80.id}
"""What each simulated member imitates until a tester chooses, which is the author's own pair."""

MAX_ROWS = 20
MAX_COLS = 120

KEYS = ("panLeft", "panRight", "zoomIn", "zoomOut", "routing")
"""The keys a simulated member has: the Monarch's names for them, whatever its shape, so the
Monarch's layers fit it. See `keyLayers.defaultLayers`."""


def modelChoices() -> dict:
	""":return: model id to its label, in the order the settings offer them."""
	return {
		# Translators: a model the simulated braille display can imitate.
		MONARCH_8.id: _("Monarch, 8 rows of 32"),
		# Translators: a model the simulated braille display can imitate.
		MONARCH_10.id: _("Monarch, 10 rows of 32"),
		# Translators: a model the simulated braille display can imitate.
		FOCUS_80.id: _("Focus 80, 1 row of 80"),
		# Translators: a model the simulated braille display can imitate.
		CELLS_40.id: _("40 cells, 1 row"),
		# Translators: a model the simulated braille display can imitate: rows and columns the
		# tester chooses.
		CUSTOM: _("Custom size"),
	}


class _Choice(NamedTuple):
	"""One choice of a string setting, shaped as NVDA's `StringParameterInfo`."""

	id: str
	displayName: str


def _settings() -> list:
	""":return: the settings NVDA's braille settings offer, through the composite.

	Built when asked, since the setting classes live in NVDA and are only needed there.
	"""
	from autoSettingsUtils.driverSetting import DriverSetting, NumericDriverSetting

	return [
		DriverSetting(
			"model",
			# Translators: label for the setting choosing which display the simulated display imitates.
			_("Simulated &model"),
			defaultVal=MONARCH_8.id,
		),
		NumericDriverSetting(
			"customRows",
			# Translators: label for the rows of a simulated display of custom size.
			_("Custom &rows"),
			defaultVal=4,
			minVal=1,
			maxVal=MAX_ROWS,
		),
		NumericDriverSetting(
			"customCols",
			# Translators: label for the cells across a simulated display of custom size.
			_("Custom &cells per row"),
			defaultVal=40,
			minVal=1,
			maxVal=MAX_COLS,
		),
	]


class SimulatedGesture(BrailleDisplayGesture):
	"""A key on a simulated member, raised as that member's own."""

	def __init__(self, source: str, keyId: str, cellIndexes: list[int] | None = None):
		"""
		:param source: the member's driver name.
		:param keyId: one of `KEYS`.
		:param cellIndexes: the cells pressed, on the member's own display, for `routing`.
		"""
		super().__init__()
		self.source = source
		self.id = keyId
		if cellIndexes:
			self.cellIndexes = list(cellIndexes)


class SimulatedDisplay(braille.display.driver.BrailleDisplayDriver):
	"""A display that keeps what it is shown and imitates a model's shape.

	The composite assigns `onShapeChanged` once the member is in a slot.
	"""

	name = ""
	description = ""
	isThreadSafe = False
	"""No I/O, so nothing to move off the main thread."""

	@classmethod
	def check(cls) -> bool:
		return True

	def __init__(self, port: str | None = None):
		"""
		:param port: accepted and ignored, as the composite passes the member's.
		"""
		super().__init__()
		self.onShapeChanged: Callable[[], None] | None = None
		"""Called after the shape changes, so the composite lays itself out again."""
		self._lock = threading.Lock()
		"""Guards the cells and overlays, which the main thread reads and writes."""
		self._modelId = DEFAULT_MODELS.get(self.name, MONARCH_8.id)
		self._customRows = 4
		self._customCols = 40
		self.cells: list[int] = []
		"""What the display was last shown, row major."""
		self._overlays: dict[str, tuple[int, int, object]] = {}
		self.lastTouch: tuple[int, int] | None = None
		"""The pin the viewer last pointed at, or None. As the Monarch's."""
		self.lastRoutingPin: tuple[int, int] | None = None
		"""Where the pointer was at the last routing press, kept after it moves away. As the Monarch's."""
		self._applyShape()

	@property
	def supportedSettings(self) -> list:
		return _settings()

	@property
	def availableModels(self) -> dict:
		"""NVDA's naming: the choices for the `model` setting."""
		return {modelId: _Choice(modelId, label) for modelId, label in modelChoices().items()}

	@property
	def model(self) -> str:
		return self._modelId

	@model.setter
	def model(self, value: str) -> None:
		if value != CUSTOM and value not in MODELS:
			log.warning(f"BrlMultiline: {self.name} has no model {value!r}; keeping {self._modelId!r}")
			return
		self._modelId = value
		self._reshape()

	@property
	def customRows(self) -> int:
		return self._customRows

	@customRows.setter
	def customRows(self, value: int) -> None:
		self._customRows = max(1, min(MAX_ROWS, int(value)))
		if self._modelId == CUSTOM:
			self._reshape()

	@property
	def customCols(self) -> int:
		return self._customCols

	@customCols.setter
	def customCols(self, value: int) -> None:
		self._customCols = max(1, min(MAX_COLS, int(value)))
		if self._modelId == CUSTOM:
			self._reshape()

	@property
	def shape(self) -> Model:
		""":return: the model being imitated, with the custom size filled in."""
		if self._modelId == CUSTOM:
			return Model(CUSTOM, self._customRows, self._customCols)
		return MODELS[self._modelId]

	@property
	def canDraw(self) -> bool:
		return self.shape.pinWidth > 0

	def _applyShape(self) -> None:
		shape = self.shape
		self.numRows = shape.numRows
		self.numCols = shape.numCols

	def _reshape(self) -> None:
		"""Take on the shape of the model now chosen, and tell the composite if it changed."""
		before = (self.numRows, self.numCols)
		self._applyShape()
		with self._lock:
			self.cells = []
			if not self.canDraw:
				self._overlays.clear()
		if before == (self.numRows, self.numCols) or self.onShapeChanged is None:
			return
		try:
			self.onShapeChanged()
		except Exception:
			log.error(f"BrlMultiline: could not lay the display out again for {self.name}", exc_info=True)

	def display(self, cells: list[int]) -> None:
		with self._lock:
			self.cells = list(cells)

	# Drawing. The add-on finds a display that can draw by these attributes being there (see
	# `graphics._isDrawable`), so a model that cannot draw raises AttributeError for each of them.

	def _requireDrawing(self, name: str) -> None:
		if not self.canDraw:
			raise AttributeError(name)

	@property
	def graphicsSize(self) -> tuple[int, int]:
		self._requireDrawing("graphicsSize")
		shape = self.shape
		return shape.pinWidth, shape.pinHeight

	@property
	def newGraphicsBuffer(self):
		self._requireDrawing("newGraphicsBuffer")
		return self._newGraphicsBuffer

	@property
	def setGraphicsOverlay(self):
		self._requireDrawing("setGraphicsOverlay")
		return self._setGraphicsOverlay

	@property
	def clearGraphicsOverlay(self):
		self._requireDrawing("clearGraphicsOverlay")
		return self._clearGraphicsOverlay

	def _newGraphicsBuffer(self, width: int | None = None, height: int | None = None):
		from brailleDisplayDrivers.brlMultilineMonarch.pinBuffer import PinBuffer

		pinWidth, pinHeight = self.graphicsSize
		return PinBuffer(pinWidth if width is None else width, pinHeight if height is None else height)

	def _setGraphicsOverlay(self, key: str, x: int, y: int, buffer) -> None:
		with self._lock:
			self._overlays[key] = (x, y, buffer)

	def _clearGraphicsOverlay(self, key: str | None = None) -> None:
		with self._lock:
			if key is None:
				self._overlays.clear()
			else:
				self._overlays.pop(key, None)

	@property
	def overlays(self) -> dict[str, tuple[int, int, object]]:
		""":return: a copy of the drawings up, by key, as left column, top row and buffer."""
		with self._lock:
			return dict(self._overlays)

	# Input, from the viewer and from keyboard layers.

	def touch(self, x: int, y: int) -> None:
		"""Point at a pin, as a finger on the Monarch does.

		:param x: pin column.
		:param y: pin row.
		"""
		self.lastTouch = (x, y)
		self.lastRoutingPin = (x, y)

	def release(self) -> None:
		"""Stop pointing. `lastRoutingPin` is kept, as the Monarch keeps it."""
		self.lastTouch = None

	def pressKey(self, keyId: str, cellIndex: int | None = None) -> None:
		"""Raise one of this display's keys, as its read loop would.

		:param keyId: one of `KEYS`.
		:param cellIndex: for `routing`, the cell pressed on this display.
		"""
		gesture = SimulatedGesture(self.name, keyId, [cellIndex] if cellIndex is not None else None)
		try:
			inputCore.manager.executeGesture(gesture)
		except inputCore.NoInputGestureAction:
			pass

	@property
	def gestureMap(self):
		return _gestureMap(self.name)


_gestureMaps: dict = {}


def _gestureMap(name: str):
	""":return: the keys NVDA binds on a simulated member, before any layer."""
	cached = _gestureMaps.get(name)
	if cached is not None:
		return cached
	_gestureMaps[name] = gestureMap = inputCore.GlobalGestureMap(
		{
			"globalCommands.GlobalCommands": {
				"braille_scrollBack": (f"br({name}):panLeft",),
				"braille_scrollForward": (f"br({name}):panRight",),
				"braille_routeTo": (f"br({name}):routing",),
			},
		},
	)
	return gestureMap


def _subclass(name: str, label: str) -> type[SimulatedDisplay]:
	return type(f"SimulatedDisplay_{name}", (SimulatedDisplay,), {"name": name, "description": label})


def driverClassFor(name: str) -> type[SimulatedDisplay]:
	""":return: the class for one of `SIMULATED_DRIVER_NAMES`.

	:raises KeyError: for any other name.
	"""
	return _CLASSES[name]


_CLASSES = {
	SIMULATED_DRIVER_NAMES[0]: _subclass(
		SIMULATED_DRIVER_NAMES[0],
		# Translators: the name of the first simulated braille display, for testing without hardware.
		_("Simulated display 1"),
	),
	SIMULATED_DRIVER_NAMES[1]: _subclass(
		SIMULATED_DRIVER_NAMES[1],
		# Translators: the name of the second simulated braille display, for testing without hardware.
		_("Simulated display 2"),
	),
}
