# BrlMultiline: reading a run of objects as a flow.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Blocks that are objects rather than lines of a document.

A browse mode document hands a flow its content as text, and `flowSources` cuts that text
into blocks. Most of what a reader meets is not a document: a list box, a menu, a tree, the
choices of a combo box. Those are runs of objects, and on a single line display a run of
objects is shown one at a time — which is the same loss of context spatial reading exists
to answer, in the place it is easiest to feel. Eight rows of a Monarch can hold eight items
of a list with the focused one among them.

One object is one block, and its braille is `NVDAObjectRegion` — NVDA's own presentation of
an object, with the name, role, value, states and position information the reader already
knows. Nothing here re-derives any of that. A block of this source says exactly what a
single line display would say about that object, and the flow's contribution is that seven
of its neighbours are under the same fingers.

Three things are different from a document source, and all three follow from what an object
is rather than from taste.

**Walking is expensive here.** Stepping a line in a virtual buffer stays inside NVDA's
process; stepping to the next object is a call into the application, and on a slow one a
run of them is exactly the lag this design has worried about from the start. So nothing
reads ahead: the source is asked for one more block only when the window is short, and the
operation budget bounds how many it will fetch. This is the case the budget was written
for.

**Nothing here moves anything.** Panning a document flow moves the browse mode cursor,
which is a reading position with no consequences. The equivalent for a list would be moving
the selection, and a selection is application state: it fires events, it changes what is
shown, and in a menu it can act. So an object flow moves the window and nothing else. The
reader's focus stays where they put it, and the display reads around it.

**The focused object still shows the cursor.** Not to move it — to say which of the eight
items on the display is the one the arrow keys will act on. Without it a list of eight
reads as eight equal things, and the reader has lost the one fact they had before.

Which objects are read this way is a registry rather than a list of roles buried in a
condition, because the answer is a judgement about a kind of control and other code may
have a better one. See `ObjectAdapter` and `register`.
"""

import dataclasses
from typing import Any, Callable, Optional

from logHandler import log

from .flow import BlockId, ByIdentity, FetchResult, SourceBlock

RUN_ROLES = frozenset(
	{
		"LISTITEM",
		"TREEVIEWITEM",
		"MENUITEM",
		"CHECKMENUITEM",
		"RADIOMENUITEM",
		"TAB",
	},
)
"""Roles whose objects come in runs, by `controlTypes.Role` member name.

These are the things a reader arrows through one of and wants the others of. Named rather
than imported for the reason `flowForms.CONTROL_ROLES` is: a role NVDA has not got costs
nothing and matches nothing, and the policy can be read without a running screen reader.
"""

CHOICE_ROLES = frozenset({"COMBOBOX", "LIST", "LISTBOX", "MENU", "TREEVIEW", "GROUPING"})
"""Roles whose *children* are the run, when the object itself has the focus.

A combo box the reader has opened is the case that matters: the choices are its children,
and on one line the reader is told the one they are on and nothing about the rest.
"""

MAX_CHILDREN = 500
"""How many children of a container will be looked at when finding where the focus sits.

A guard against a list of tens of thousands, which is a real thing in a file manager. The
flow only ever shows a band's worth, so failing to place the focus in a list longer than
this costs the reader the run rather than costing them time.
"""


def roleName(role) -> str:
	""":return: the name of a role, however it was given. See `flowForms.roleName`."""
	name = getattr(role, "name", None)
	if isinstance(name, str):
		return name.upper()
	return str(role).upper()


def _siblingNext(obj):
	""":return: the object after this one in its run, or None."""
	return getattr(obj, "next", None)


def _siblingPrevious(obj):
	""":return: the object before this one in its run, or None."""
	return getattr(obj, "previous", None)


@dataclasses.dataclass(frozen=True)
class ObjectAdapter:
	"""How one kind of object is read as a run.

	A registry entry rather than a branch in a condition: which objects read well this way is
	a judgement about a kind of control, and an add-on that owns such a control has a better
	one than this module does.
	"""

	name: str
	"""What to call it in a log."""

	matches: Callable[[Any], bool]
	"""Whether this adapter reads a given object."""

	start: Callable[[Any], Any] = lambda obj: obj
	"""Which object of the run the reader is on. The object itself, unless the run is its
	children — a combo box the reader has opened is on one of its choices, not on the box."""

	nextOf: Callable[[Any], Any] = _siblingNext
	previousOf: Callable[[Any], Any] = _siblingPrevious
	"""How to step through the run. Siblings, unless a control is stitched together some
	other way."""


def _isRunMember(obj) -> bool:
	""":return: whether an object is one of a run of siblings."""
	return roleName(getattr(obj, "role", None)) in RUN_ROLES


def _hasChoices(obj) -> bool:
	""":return: whether an object's children are a run the reader is choosing from."""
	if roleName(getattr(obj, "role", None)) not in CHOICE_ROLES:
		return False
	return _focusedChild(obj) is not None


def _focusedChild(obj):
	"""Find which child of a container the reader is on.

	The selected one, or the focused one, or the first — in that order, because a container
	that reports neither is still worth reading from its start.

	:param obj: the container.
	:return: the child to start at, or None if it has none.
	"""
	try:
		children = getattr(obj, "children", None) or ()
	except Exception:
		log.debugWarning("Could not read an object's children", exc_info=True)
		return None
	first = None
	for index, child in enumerate(children):
		if index >= MAX_CHILDREN:
			break
		if first is None:
			first = child
		if _isChosen(child):
			return child
	return first


def _isChosen(obj) -> bool:
	""":return: whether an object is the selected or focused one of its run."""
	try:
		states = getattr(obj, "states", None) or ()
		names = {roleName(state) for state in states}
	except Exception:
		return False
	return bool(names & {"SELECTED", "FOCUSED"})


SIBLING_RUN = ObjectAdapter(name="siblings", matches=_isRunMember)
"""A list item, a tree item, a menu item: the reader is in a run and the run is its siblings."""

CHOICES = ObjectAdapter(name="choices", matches=_hasChoices, start=_focusedChild)
"""A container the reader is choosing from, whose run is its children."""

_adapters: list[ObjectAdapter] = [SIBLING_RUN, CHOICES]
"""The adapters, in the order they are asked. First match wins."""


def register(adapter: ObjectAdapter, first: bool = True) -> None:
	"""Add a way of reading a kind of object as a run.

	:param adapter: the adapter.
	:param first: ask it before the built-in ones, which is what an add-on registering for
		its own control wants: the built-in answer for a list item is a guess about every
		list item there is, and a specific one is better.
	"""
	if first:
		_adapters.insert(0, adapter)
	else:
		_adapters.append(adapter)


def unregister(adapter: ObjectAdapter) -> None:
	"""Take an adapter back out, so an add-on can be disabled without leaving its judgement."""
	if adapter in _adapters:
		_adapters.remove(adapter)


def adapterFor(obj) -> Optional[ObjectAdapter]:
	""":return: how to read an object as a run, or None if it is not read that way.

	An object with no adapter is left to NVDA, which presents it as it always has. That is
	the honest answer for most things: a button is one object and has no run to show.
	"""
	if obj is None:
		return None
	for adapter in _adapters:
		try:
			if adapter.matches(obj):
				return adapter
		except Exception:
			log.debugWarning(f"The {adapter.name} adapter could not judge {obj!r}", exc_info=True)
	return None


def regionFactory(live: bool = False) -> Callable:
	"""Build the region class object blocks are read through.

	:param live: whether this flow is the one the reader is working in, which decides only
		whether the focused block shows a cursor. It never decides whether anything moves:
		an object flow moves nothing. See the module docstring.
	:return: a callable taking an object and returning its region.
	"""
	from braille.regions.NVDAObject import NVDAObjectRegion

	class FlowObjectRegion(NVDAObjectRegion):
		"""One object as a block, said the way NVDA says it."""

		def __init__(self, obj) -> None:
			super().__init__(obj)
			self.isActive = False
			"""Whether this is the object the reader is on. Only that one shows a cursor."""

			self.dirty = False
			"""Set when this block was read again, so the band knows to lay it out afresh."""

			self.onMoved = None
			"""Unused here, and present because the controller looks for it. Nothing in an
			object flow moves the reader's place, so nothing ever calls it."""

		def update(self) -> None:
			# Before the base class, which is what turns a position into `brailleCursorPos`.
			# The start of the block: what the cursor says here is "this is the one you are
			# on", not "this is the character you are at".
			self.cursorPos = 0 if (self.isActive and live) else None
			super().update()
			self.dirty = True

		def routeTo(self, braillePos: int) -> None:
			"""Go to an object, or act on the one already there.

			NVDA's own region acts on the object outright. That is right for the block the
			reader is on and wrong for the other seven, which are only on the display because
			this flow put them there: a finger landing on a list item the reader was reading
			past must not activate it. So a press elsewhere moves the focus, which is what
			"go there" means, and a press where they already are does what it always did.

			The precedent is NVDA's own `ReviewNVDAObjectRegion`, which focuses before acting
			for exactly this reason.
			"""
			if self.isActive:
				super().routeTo(braillePos)
				return
			try:
				if getattr(self.obj, "isFocusable", False) and not getattr(self.obj, "hasFocus", False):
					self.obj.setFocus()
			except Exception:
				log.debugWarning(f"Could not move the focus to {self.obj!r}", exc_info=True)

		def __repr__(self) -> str:
			return f"<FlowObjectRegion {getattr(self, 'rawText', '')!r}>"

	def factory(obj):
		return FlowObjectRegion(obj)

	return factory


class ObjectFlowSource:
	"""Blocks read from a run of objects.

	Answers the same three questions `DocumentFlowSource` does, so the controller above it
	cannot tell the two apart. What it does not share is any idea of a reading unit or a
	text position: an object is its own identity, and a run is walked rather than measured.
	"""

	def __init__(
		self,
		obj,
		adapter: ObjectAdapter,
		regionFactory: Callable,
		generation: int = 0,
		budget=None,
	) -> None:
		"""
		:param obj: what the reader is on.
		:param adapter: how to walk the run it belongs to.
		:param regionFactory: builds a region for one object.
		:param generation: bumped by the caller when the run is replaced, so that a block of
			an old reading can never match one of the new.
		:param budget: how much work a fetch may do. One is made if none is given.
		"""
		from .flowSources import FetchBudget

		self.obj = obj
		self.adapter = adapter
		self.regionFactory = regionFactory
		self.generation = generation
		self.budget = budget if budget is not None else FetchBudget()
		self.unit = "object"
		"""What a block is here, for the log and for the dry run's report."""

		self._objects = ByIdentity()
		"""The object behind each block, by identity.

		Not a dictionary: an `NVDAObject` compares usefully and hashes by identity, which are
		not the same thing — two objects for one control are equal and hash apart. See
		`flow.ByIdentity`."""

	# Reading.

	def blockAtCursor(self, atObject=None) -> FetchResult:
		"""The block the reader is on.

		:param atObject: an object to read at instead, which an object flow has no use for:
			the run is already the thing the reader arrived at. Accepted so that a source of
			either kind answers the same call.
		:return: the block, or an error if the run could not be read.
		"""
		self.budget.startUnlessActive()
		try:
			start = self.adapter.start(atObject if atObject is not None else self.obj)
		except Exception as error:
			log.debugWarning("Could not find where the reader is in this run", exc_info=True)
			return FetchResult.failed(f"no starting object: {error!r}")
		if start is None:
			return FetchResult.failed("this run has nothing in it")
		return self._blockAt(start)

	def blockAfter(self, blockId: BlockId) -> FetchResult:
		"""The block after one already fetched."""
		return self._step(blockId, forward=True)

	def blockBefore(self, blockId: BlockId) -> FetchResult:
		"""The block before one already fetched."""
		return self._step(blockId, forward=False)

	def _step(self, blockId: BlockId, forward: bool) -> FetchResult:
		"""Walk one object in a direction.

		Each step is a call into the application, which is the expensive kind of reading and
		the reason nothing here is done speculatively.
		"""
		self.budget.startUnlessActive()
		current = self._objects.get(blockId.bookmark)
		if current is None:
			return FetchResult.failed(f"no cached object for {blockId}")
		began = self.budget.clock()
		try:
			found = self.adapter.nextOf(current) if forward else self.adapter.previousOf(current)
		except Exception as error:
			log.debugWarning(f"Could not walk {'on' if forward else 'back'} a run", exc_info=True)
			return FetchResult.failed(f"could not walk the run: {error!r}")
		finally:
			self.budget.observe(self.budget.clock() - began)
		if found is None:
			return FetchResult.endOfStream()
		return self._blockAt(found)

	def _blockAt(self, obj) -> FetchResult:
		""":return: a result carrying the block for one object."""
		began = self.budget.clock()
		try:
			return FetchResult.found(self._buildBlock(obj))
		except Exception as error:
			log.debugWarning("Could not build a block for an object", exc_info=True)
			return FetchResult.failed(f"could not build a block: {error!r}")
		finally:
			self.budget.observe(self.budget.clock() - began)

	def _buildBlock(self, obj) -> SourceBlock:
		"""Build one block from an object, and remember which object it was."""
		blockId = BlockId(generation=self.generation, bookmark=obj, unit=self.unit)
		self._objects.set(obj, obj)
		region = self.regionFactory(obj)
		region.update()
		return SourceBlock(
			blockId=blockId,
			region=region,
			isBlank=not (getattr(region, "rawText", "") or "").strip(),
		)

	def holds(self, obj) -> bool:
		""":return: whether this run has already read an object."""
		return self._objects.get(obj) is not None

	def forget(self) -> None:
		"""Drop every remembered object, after the run has been replaced."""
		self._objects.clear()

	def __repr__(self) -> str:
		return f"<ObjectFlowSource {self.obj!r} by {self.adapter.name} generation {self.generation}>"


def belongsTo(source: "ObjectFlowSource", obj) -> bool:
	"""Whether an object is part of the run a source is already reading.

	The question the band asks on every focus change, and in a list the focus changes on
	every arrow key: getting it wrong means throwing the run away and walking it again for
	each item the reader passes, which here is a call into the application per step.

	Three ways of belonging, in the order they are cheap: this object has already been read;
	it is a child of what the run was started from, which is a combo box's choices; or it
	shares a parent with it, which is the next item of a list.

	:param source: the run being read.
	:param obj: what the reader has moved to.
	:return: whether to keep reading the run rather than start again.
	"""
	if obj is None:
		return False
	if source.holds(obj):
		return True
	try:
		parent = getattr(obj, "parent", None)
		if parent is None:
			return False
		return bool(parent == source.obj or parent == getattr(source.obj, "parent", None))
	except Exception:
		log.debugWarning("Could not tell whether an object is in this run", exc_info=True)
		return False
