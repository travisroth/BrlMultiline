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

**What the run is, and what is merely beside it.** NVDA's own navigation of a list or a
menu walks the items the reader can arrive at. This walks the accessibility siblings, which
is a wider set: a menu's separators are in it, and so is a button sitting at the bottom of
a list box. Handed straight to `NVDAObjectRegion` a separator reads as "unavailable"
followed by its dashes, which is true and useless — the reader is told a command is
disabled when what is there is a line drawn between two groups. So every step is classified
before it is read: another member of the run, a decoration between two of them, or
something that merely sits beside the run and ends it. See `ObjectAdapter.admits` and
`isDecoration`.
"""

import dataclasses
from typing import Any, Callable, Optional

from logHandler import log

from .flow import BlockId, FetchResult, SourceBlock

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

TREE_ITEM_ROLES = frozenset({"TREEVIEWITEM"})
"""Roles read in visible order rather than as a run of siblings. See `VISIBLE_TREE`."""

TREE_ROLES = frozenset({"TREEVIEW", "TREE", "OUTLINE"})
"""What a tree item's containing control calls itself, where it says so.

Only used to stop a walk climbing out of the tree it started in. A control that names
itself none of these still bounds the walk, because the climb stops at the first ancestor
that is not itself a tree item.
"""

MAX_TREE_DEPTH = 64
"""How far a walk may climb or descend before it decides the tree is lying to it.

A folder tree sixty four deep does not exist; a tree whose `parent` eventually points back
into itself does, because an accessibility bridge under load will answer anything. The walk
is bounded rather than trusted, for the same reason `MAX_CHILDREN` is.
"""

MENU_ITEM_ROLES = frozenset({"MENUITEM", "CHECKMENUITEM", "RADIOMENUITEM"})
"""The roles one menu mixes freely.

A run is otherwise all of one role — a list of list items, a tree of tree items — and a
menu is the exception: a check item and a plain command sitting together are one menu, and
showing only the ones whose role matches the item the reader happens to be on would give
them a menu with holes in it.
"""

CHOICE_ROLES = frozenset({"COMBOBOX", "LIST", "LISTBOX", "MENU", "TREEVIEW", "GROUPING"})
"""Roles whose *children* are the run, when the object itself has the focus.

A combo box the reader has opened is the case that matters: the choices are its children,
and on one line the reader is told the one they are on and nothing about the rest.
"""

DECORATION_ROLES = frozenset({"SEPARATOR"})
"""Roles that draw something rather than say something. See `isDecoration`."""

ACTIVATE_ROLES = frozenset({"TAB"}) | MENU_ITEM_ROLES
"""Roles a routing key acts on rather than merely goes to.

A routing key is the braille equivalent of clicking on what is under your finger, so the
rule is what a mouse click does to that kind of control. Clicking a menu item invokes it and
clicking a tab chooses it; clicking a list item or a tree item selects it and waits. So a
menu and a tab strip act, and everything else takes the focus — which is what "go there"
means, and what stops a finger landing on a list item the reader was reading past from
activating it.
"""

MAX_CHILDREN = 500
"""How many children of a container will be walked when finding where the focus sits.

A guard against a list of tens of thousands, which is a real thing in a file manager. The
children are walked one at a time from the first rather than asked for as a list: NVDA's
own `children` builds the whole list before anything can count it, so a limit applied
afterwards bounds this module's loop and not the work. The flow only ever shows a band's
worth, so failing to place the focus in a list longer than this costs the reader the run
rather than costing them time.
"""

LINE_CHARACTERS = frozenset("-_=~.*" + "‐‑‒–—―─━┄┅")
"""What a separator is drawn out of, for the fallback in `isDecoration`.

Hyphens and underscores because that is what a toolkit usually names its line, and the
dashes and box drawing characters because some name it with the character they would draw.
The role is still the test; this only decides what counts as "nothing but a line" once a
toolkit has already claimed its separator is a disabled menu item."""


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


def _depthFromPositionInfo(obj) -> Optional[int]:
	"""How deep an object sits, on NVDA's own account.

	`positionInfo["level"]` is the answer wherever an object has one, and a surprising number
	of them do: `sysTreeView32` computes it by walking to the root, UIA reads it from the
	tree item pattern or the ARIA level, IA2 takes it from the object's attributes, and
	Outlook's own app module sets it by hand for the folder list. Deriving it here by counting
	parents would be a second, worse implementation of all of that, and would cost a call into
	the application per ancestor per block.

	An object with no level has no depth, and a flat run of list items is exactly that: the
	items of an ordinary list box report `indexInGroup` and no level at all, so they draw flat
	and today's reading is unchanged. That is the point — depth is drawn where a control says
	it has some, and nowhere else.

	:param obj: the object a block is being built for.
	:return: its one-based level, or None where it has none.
	"""
	try:
		info = getattr(obj, "positionInfo", None) or {}
		level = info.get("level")
	except Exception:
		log.debugWarning("Could not read an object's level", exc_info=True)
		return None
	try:
		level = int(level)
	except (TypeError, ValueError):
		return None
	# Zero and below are not levels. Some providers use 0 for "no level" rather than omitting
	# the key, and drawing that as a depth would put a whole run one level in for nothing.
	return level if level > 0 else None


def _isTreeItem(obj) -> bool:
	""":return: whether an object is an item of a tree rather than something else."""
	return obj is not None and roleName(getattr(obj, "role", None)) in TREE_ITEM_ROLES


def _isExpanded(obj) -> Optional[bool]:
	""":return: whether a node is showing its children, or None if it has no such state.

	Three answers rather than two, and the third is what stops a leaf being mistaken for a
	collapsed node. A leaf reports neither state — it has nothing to expand — and a caller
	that read that as "collapsed" would be right by accident here and wrong wherever the
	distinction matters, such as deciding whether the run's shape has changed.
	"""
	try:
		names = {roleName(state) for state in (getattr(obj, "states", None) or ())}
	except Exception:
		log.debugWarning("Could not read whether a node is expanded", exc_info=True)
		return None
	if "EXPANDED" in names:
		return True
	if "COLLAPSED" in names:
		return False
	return None


def _firstVisibleChild(obj):
	""":return: a node's first child if the reader can see it, else None.

	Gated on the state rather than on there being a child, because a tree control will hand
	back the children of a collapsed node perfectly happily — `sysTreeView32` builds them
	from the window's own item handles, which do not care what is on screen. Walking into
	them would put rows on the display that are not on the reader's screen, which is the one
	thing reading in visible order is defined not to do.
	"""
	if not _isExpanded(obj):
		return None
	try:
		return getattr(obj, "firstChild", None)
	except Exception:
		log.debugWarning("Could not read a node's first child", exc_info=True)
		return None


def _lastVisibleDescendant(obj):
	""":return: the last row of a node's visible subtree, which is the row before its next
	sibling.

	Reading backward is not the mirror of reading forward. Forward, a node is followed by its
	own first child; backward, a node is preceded by the *deepest last* thing under the
	sibling above it, because that is the row directly above it on the screen. Getting this
	wrong shows up as panning back landing several rows above where panning forward left,
	which is the reversibility property the whole anchor design rests on.
	"""
	node = obj
	for _ in range(MAX_TREE_DEPTH):
		child = _firstVisibleChild(node)
		if child is None:
			return node
		last = child
		for _ in range(MAX_CHILDREN):
			try:
				following = getattr(last, "next", None)
			except Exception:
				log.debugWarning("Could not walk a node's children", exc_info=True)
				break
			if following is None:
				break
			last = following
		node = last
	return node


def _treeNext(obj):
	""":return: the row below this one on the reader's screen, or None at the end of the tree.

	The child if there is a visible one, else the next sibling, else the next sibling of the
	nearest ancestor that has one. That last part is what today's sibling walk cannot do and
	what leaving a subtree requires.
	"""
	child = _firstVisibleChild(obj)
	if child is not None:
		return child
	node = obj
	for _ in range(MAX_TREE_DEPTH):
		if node is None:
			return None
		try:
			following = getattr(node, "next", None)
		except Exception:
			log.debugWarning("Could not walk on through a tree", exc_info=True)
			return None
		if following is not None:
			return following
		try:
			node = getattr(node, "parent", None)
		except Exception:
			log.debugWarning("Could not climb out of a subtree", exc_info=True)
			return None
		if not _isTreeItem(node):
			# Reached the tree control itself. There is no row after the last one.
			return None
	return None


def _treePrevious(obj):
	""":return: the row above this one on the reader's screen, or None at the top of the tree."""
	try:
		earlier = getattr(obj, "previous", None)
	except Exception:
		log.debugWarning("Could not walk back through a tree", exc_info=True)
		return None
	if earlier is not None:
		return _lastVisibleDescendant(earlier)
	try:
		parent = getattr(obj, "parent", None)
	except Exception:
		log.debugWarning("Could not climb to a node's parent", exc_info=True)
		return None
	return parent if _isTreeItem(parent) else None


def _treeOf(obj):
	""":return: the control a tree item belongs to, or None.

	Found by climbing until something is not a tree item, rather than by looking for a role
	in `TREE_ROLES`: a tree whose container calls itself a LIST, a GROUPING or nothing at all
	is common, and a membership test that only worked for well behaved controls would make
	the walk fall out of exactly the trees that need it most. The role set is only used to
	stop early where it does apply.
	"""
	node = obj
	for _ in range(MAX_TREE_DEPTH):
		try:
			parent = getattr(node, "parent", None)
		except Exception:
			log.debugWarning("Could not find which tree an item belongs to", exc_info=True)
			return None
		if parent is None:
			return None
		if roleName(getattr(parent, "role", None)) in TREE_ROLES or not _isTreeItem(parent):
			return parent
		node = parent
	return None


def _isSameTree(root, candidate) -> bool:
	""":return: whether a candidate is another visible row of the tree `root` is in.

	Not `_sameParent`, which is the whole difference. A child of an expanded node has a
	different parent from its own parent's siblings and is still the very next row on the
	screen; the sibling test threw exactly those away, which is how an expanded folder's
	contents were stepped over.
	"""
	if root is None or candidate is None:
		return False
	if not _isTreeItem(candidate) or not _isTreeItem(root):
		return False
	tree = _treeOf(root)
	if tree is None:
		# Nothing to compare against. Falling back to the sibling test keeps a malformed
		# tree readable as the flat run it was before this adapter existed.
		return _sameParent(root, candidate)
	other = _treeOf(candidate)
	if other is None:
		return False
	try:
		return bool(tree == other)
	except Exception:
		log.debugWarning("Could not compare two trees", exc_info=True)
		return False


def _countedTreeDepth(obj) -> Optional[int]:
	""":return: how deep a tree item sits, by counting the tree items above it.

	One for an item whose parent is the tree control itself. Bounded like every other walk
	here, and it stops at the first ancestor that is not a tree item, which is the same
	boundary `_treeOf` and the walk use.
	"""
	if not _isTreeItem(obj):
		return None
	depth = 1
	node = obj
	for _ in range(MAX_TREE_DEPTH):
		try:
			parent = getattr(node, "parent", None)
		except Exception:
			log.debugWarning("Could not count how deep a tree item sits", exc_info=True)
			return depth
		if not _isTreeItem(parent):
			return depth
		depth += 1
		node = parent
	return depth


def _treeDepth(obj) -> Optional[int]:
	""":return: how deep a tree item sits, from the structure the walk went through.

	The counted depth wins wherever there is one to count, and that is the opposite of the
	priority everywhere else in this module. The reason is a hardware report, and it is the
	rule worth keeping rather than the case that produced it: **the depth drawn has to come
	from the same structure the walk stepped through.** A walk that descends into a node's
	child and then draws that child at its parent's indent is telling the reader two
	different things about one tree.

	Outlook's folder pane is where they part. Its account row and the Inbox beneath it both
	report `positionInfo["level"]` of 1, while a folder inside a folder reports 2 — the
	folders are numbered from one and the account row is not counted. The walk had already
	descended into the account's `firstChild` to reach the Inbox, so the structure said one
	deeper and the label said the same, and the whole account's contents drew flush with the
	account. Counting says 2 and is right.

	`positionInfo` is still the answer where there is no structure to count, and that is not
	a rare case: an outline whose items are all direct children of the control, with the
	level carried as an attribute, is a real shape and the only thing that knows its depth is
	the attribute. So the count is consulted first and believed only when it found genuine
	nesting; a count of one means "nothing above me", which is exactly when the label is
	worth more than the structure.

	It costs a `parent` read per level rather than one `positionInfo` read. That is the price
	of agreeing with the walk, and the walk is doing the same reads a step later anyway.
	"""
	counted = _countedTreeDepth(obj)
	if counted is not None and counted > 1:
		return counted
	told = _depthFromPositionInfo(obj)
	if told is not None:
		return told
	return counted


def _startAtCurrent(root, current):
	""":return: where the reader is in a run whose members they are on directly."""
	return current if current is not None else root


def _sameKind(first: str, second: str) -> bool:
	""":return: whether two roles are the same kind of thing to read in one run."""
	if first == second:
		return True
	return first in MENU_ITEM_ROLES and second in MENU_ITEM_ROLES


def _sameParent(first, second) -> bool:
	""":return: whether two objects hang under one container."""
	try:
		above = getattr(first, "parent", None)
		other = getattr(second, "parent", None)
		if above is None or other is None:
			return False
		return bool(above == other)
	except Exception:
		log.debugWarning("Could not compare two objects' parents", exc_info=True)
		return False


def _isSameRun(root, candidate) -> bool:
	""":return: whether a candidate is another member of the run `root` belongs to.

	Sharing a parent is not enough on its own. A list box holds a button as often as not,
	and a menu holds its separators, and either one read through the list item adapter is
	presented to the reader as one of the things they are arrowing through.
	"""
	if root is None or candidate is None:
		return False
	role = roleName(getattr(candidate, "role", None))
	if role not in RUN_ROLES:
		return False
	if not _sameKind(role, roleName(getattr(root, "role", None))):
		return False
	return _sameParent(root, candidate)


def _isChild(root, candidate) -> bool:
	""":return: whether a candidate is one of a container's own children."""
	if root is None or candidate is None:
		return False
	try:
		parent = getattr(candidate, "parent", None)
		return parent is not None and bool(parent == root)
	except Exception:
		log.debugWarning("Could not tell whether an object is a child of a run", exc_info=True)
		return False


def _activatesOnRoute(obj) -> bool:
	""":return: whether a routing key on this object acts on it. See `ACTIVATE_ROLES`."""
	return roleName(getattr(obj, "role", None)) in ACTIVATE_ROLES


def _hasAction(obj) -> bool:
	""":return: whether an object has anything to do, as far as it will say."""
	try:
		if getattr(obj, "actionCount", 0):
			return True
		return bool(obj.getActionName())
	except Exception:
		# NotImplementedError is the ordinary answer from an object with no action.
		return False


def isDecoration(obj) -> bool:
	"""Whether an object is something drawn between items rather than one of them.

	A menu separator is the case, and a flow meets it because it walks the accessibility
	siblings rather than the focusable items NVDA's own navigation walks. It is not a
	disabled command and must not be read as one.

	The role is the test. What follows it is a fallback for a toolkit that exposes its line
	as a disabled menu item, and it is deliberately narrow: an object that cannot be
	focused, has nothing to do, and is named with nothing but dashes. Disabled is never the
	test on its own — a real command that is temporarily unavailable is meaningful content,
	and the reader is entitled to be told that it is unavailable.

	:param obj: the object stepped onto.
	:return: whether to show a blank row instead of reading it.
	"""
	if obj is None:
		return False
	try:
		role = roleName(getattr(obj, "role", None))
		if role in DECORATION_ROLES:
			return True
		if role not in MENU_ITEM_ROLES:
			return False
		if getattr(obj, "isFocusable", False) or _hasAction(obj):
			return False
		name = (getattr(obj, "name", None) or "").strip()
		return bool(name) and all(character in LINE_CHARACTERS for character in name)
	except Exception:
		log.debugWarning("Could not tell whether an object is decoration", exc_info=True)
		return False


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

	start: Callable[[Any, Any], Any] = _startAtCurrent
	"""Which object of the run the reader is on, given what the run was found from and where
	they are now. The two are the same for a list item; for a combo box the run was found
	from the box and the reader is on one of its choices."""

	admits: Callable[[Any, Any], bool] = _isSameRun
	"""Whether an object is part of the run, given what the run was found from.

	Asked of every step and of every focus change, so that neither walks out of the run into
	whatever happens to sit beside it."""

	activates: Callable[[Any], bool] = _activatesOnRoute
	"""Whether a routing key on one of these acts on it rather than going to it."""

	nextOf: Callable[[Any], Any] = _siblingNext
	previousOf: Callable[[Any], Any] = _siblingPrevious
	"""How to step through the run. Siblings, unless a control is stitched together some
	other way."""

	depthOf: Callable[[Any], Optional[int]] = _depthFromPositionInfo
	"""How deep in its structure one of these sits, or None where the run is flat.

	Here rather than in the layout for the reason the rest of this record is here: a control
	that knows better than NVDA about its own shape is exactly the case an add-on registers an
	adapter for, and depth is part of shape. What is drawn from the number is `flowIndent`'s
	decision and no adapter's business.
	"""

	expandedOf: Callable[[Any], Optional[bool]] = lambda obj: None
	"""Whether one of these is showing its children, or None where the idea does not apply.

	A run whose members cannot be opened answers None to everything and pays nothing. Where
	it does apply the answer is what tells the band that the run has changed shape under the
	reader — expanding a node is not a focus change and NVDA reports it through no event the
	band sees. See `ObjectFlowSource.shapeChanged`.
	"""


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

	Walked from `firstChild` rather than taken from `children`, which builds the whole list
	before a limit can be applied to it. See `MAX_CHILDREN`.

	:param obj: the container.
	:return: the child to start at, or None if it has none.
	"""
	try:
		child = getattr(obj, "firstChild", None)
	except Exception:
		log.debugWarning("Could not read a container's first child", exc_info=True)
		return None
	first = child
	for _ in range(MAX_CHILDREN):
		if child is None:
			break
		if _isChosen(child):
			return child
		try:
			child = getattr(child, "next", None)
		except Exception:
			log.debugWarning("Could not walk a container's children", exc_info=True)
			break
	return first


def _chosenChild(root, current):
	""":return: which of a container's children the reader is on.

	The focus moves to the choice itself the moment the reader arrows within an open combo
	box, and from then on the answer is simply where they are. Asking the container again
	would walk its children on every keypress; asking the *choice* for its own children,
	which is what an earlier version did, found nothing at all and lost the run.
	"""
	if current is not None and current is not root and _isChild(root, current):
		return current
	return _focusedChild(root)


def _isChosen(obj) -> bool:
	""":return: whether an object is the selected or focused one of its run."""
	try:
		states = getattr(obj, "states", None) or ()
		names = {roleName(state) for state in states}
	except Exception:
		return False
	return bool(names & {"SELECTED", "FOCUSED"})


VISIBLE_TREE = ObjectAdapter(
	name="visibleTree",
	matches=_isTreeItem,
	admits=_isSameTree,
	nextOf=_treeNext,
	previousOf=_treePrevious,
	depthOf=_treeDepth,
	expandedOf=_isExpanded,
)
"""A tree, read down the screen rather than along one generation of it.

The reading the sibling walk could not give. A tree item's `next` is its next *sibling*, so
a run built from it steps over everything inside an expanded node — in Outlook's Go To
Folder dialog, an open folder's contents were simply absent — and `_sameParent` would have
thrown those rows out even if the walk had reached them.

Visible order, which is decision 1 of the structured presentation plan: what the arrow keys
do, and what is on the screen. A collapsed node's children are not rows, and its expanding
is a change the band has to notice without a focus event to tell it.
"""

SIBLING_RUN = ObjectAdapter(name="siblings", matches=_isRunMember)
"""A list item, a tree item, a menu item: the reader is in a run and the run is its siblings."""

CHOICES = ObjectAdapter(name="choices", matches=_hasChoices, start=_chosenChild, admits=_isChild)
"""A container the reader is choosing from, whose run is its children."""

_adapters: list[ObjectAdapter] = [VISIBLE_TREE, SIBLING_RUN, CHOICES]
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


def regionFactory(live: bool = False, adapter: Optional[ObjectAdapter] = None) -> Callable:
	"""Build the regions object blocks are read through.

	:param live: whether this flow is the one the reader is working in, which decides only
		whether the focused block shows a cursor. It never decides whether anything moves:
		an object flow moves nothing. See the module docstring.
	:param adapter: the run being read, asked whether a routing key acts or goes.
	:return: a callable taking an object and returning its region.
	"""
	from braille.regions.base import Region
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

			self.onLine = None
			"""Called with a direction when NVDA's line commands reach this region.

			They must reach something. `script_braille_nextLine` calls `regions[-1].nextLine()`
			with no check beyond the list being non-empty, and a region that has no such method
			raises where the reader pressed a key. See the last-region audit."""

		def nextLine(self) -> None:
			"""Move on by one item, as the braille display's line command asks.

			The window only. Everywhere else in a flow this command takes the cursor with it,
			and here the cursor is a selection: stepping it would arrow through a list the
			reader is reading, activating things in a menu. So the display moves and the
			reader's place does not, which is the rule for the whole of this source.
			"""
			self._line(True)

		def previousLine(self, start: bool = False) -> None:
			"""Move back by one item. See `nextLine`."""
			self._line(False)

		def _line(self, forward: bool) -> None:
			if self.onLine is None:
				return
			try:
				self.onLine(forward)
			except Exception:
				log.debugWarning("Could not move a run by a line", exc_info=True)

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

			The exception is a run whose adapter says a routing key acts — a tab strip, where
			going to a tab is choosing it. There the focus is taken first and the action
			follows, which is exactly what NVDA's own `ReviewNVDAObjectRegion` does.
			"""
			if self.isActive:
				super().routeTo(braillePos)
				return
			self._takeFocus()
			if adapter is not None and adapter.activates(self.obj):
				super().routeTo(braillePos)

		def _takeFocus(self) -> None:
			"""Move the system focus here, if it will come."""
			try:
				if getattr(self.obj, "isFocusable", False) and not getattr(self.obj, "hasFocus", False):
					self.obj.setFocus()
			except Exception:
				log.debugWarning(f"Could not move the focus to {self.obj!r}", exc_info=True)

		def __repr__(self) -> str:
			return f"<FlowObjectRegion {getattr(self, 'rawText', '')!r}>"

	class FlowDecorationRegion(Region):
		"""A line drawn between groups, shown as the blank row it is.

		Blank rather than absent: the separator is what the menu's author put between two
		groups, and a blank row is how that grouping reads under the fingers. It holds no
		text, so nothing routes into it and no cursor ever sits in it.
		"""

		def __init__(self, obj) -> None:
			super().__init__()
			self.obj = obj
			self.isActive = False
			self.dirty = False
			self.onMoved = None
			self.onLine = None
			self.update()

		def update(self) -> None:
			self.rawText = ""
			self.brailleCells = []
			self.brailleToRawPos = []
			self.rawToBraillePos = []
			self.brailleCursorPos = None
			self.cursorPos = None

		def routeTo(self, braillePos: int) -> None:
			"""Nothing: there is nothing here to go to."""

		def nextLine(self) -> None:
			"""Move the window on, as any block of a run does. See `FlowObjectRegion`."""
			self._line(True)

		def previousLine(self, start: bool = False) -> None:
			"""Move the window back. See `nextLine`."""
			self._line(False)

		def _line(self, forward: bool) -> None:
			if self.onLine is None:
				return
			try:
				self.onLine(forward)
			except Exception:
				log.debugWarning("Could not move a run by a line", exc_info=True)

		def __repr__(self) -> str:
			return f"<FlowDecorationRegion {getattr(self.obj, 'role', None)}>"

	def factory(obj):
		if isDecoration(obj):
			return FlowDecorationRegion(obj)
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
		:param obj: what the reader was on when this run was found. The run is defined by it
			and never redefined: see `runRoot`.
		:param adapter: how to walk the run it belongs to.
		:param regionFactory: builds a region for one object.
		:param generation: bumped by the caller when the run is replaced, so that a block of
			an old reading can never match one of the new.
		:param budget: how much work a fetch may do. One is made if none is given.
		"""
		from .flowSources import FetchBudget

		self.runRoot = obj
		"""What the run was found from, and what decides what belongs to it.

		Fixed for the life of the source. It was once the same attribute as `obj` below, and
		moving the focus into an open combo box then replaced the box with the choice the
		reader had arrowed to — after which the run was that choice's own children, which is
		nothing at all, and the flow lost the list it had been showing."""

		self.obj = obj
		"""Where in the run the reader is now. See `setCurrent`."""

		self.adapter = adapter
		self.regionFactory = regionFactory
		self.generation = generation
		self.budget = budget if budget is not None else FetchBudget()
		self.unit = "object"
		"""What a block is here, for the log and for the dry run's report."""

		self._expanded: Optional[bool] = None
		"""Whether the object the reader is on was showing its children when last looked at.

		None until something has looked, which is what makes the first `shapeChanged` answer
		no rather than reporting a change from nothing."""

	def setCurrent(self, obj) -> None:
		"""Say where in the run the reader has moved to.

		:param obj: the object they are on now, which the run itself is unchanged by.
		"""
		self.obj = obj
		# Forgotten rather than re-read, so that arriving somewhere already open does not
		# read as something having just been opened. The next `shapeChanged` fills it in and
		# answers no, which is the honest answer for a reader who has only just got here.
		self._expanded = None

	def shapeChanged(self) -> bool:
		"""Whether the run has been opened or closed under the reader since it was last read.

		Expanding a node in a tree changes what the rows below it are, and NVDA reports it
		through no event the band sees: the focus does not move, so there are no fresh focus
		regions, and the object is the same one the band was already reading. A band that
		asked only on a focus change went on showing the closed folder.

		One object is consulted, the one the reader is on, because that is the only one they
		can have opened. Asking every object on the band would be eight calls into the
		application on every redraw, to answer a question about one of them.

		:return: whether to read the run again.
		"""
		obj = self.obj
		if obj is None:
			return False
		try:
			now = self.adapter.expandedOf(obj)
		except Exception:
			log.debugWarning("Could not tell whether a node has been opened", exc_info=True)
			return False
		was = self._expanded
		self._expanded = now
		return was is not None and now is not None and was != now

	# Reading.

	def blockAtCursor(self, atObject=None) -> FetchResult:
		"""The block the reader is on.

		:param atObject: an object to read at instead of where the source last knew the
			reader to be.
		:return: the block, or an error if the run could not be read.
		"""
		self.budget.startUnlessActive()
		current = atObject if atObject is not None else self.obj
		try:
			start = self.adapter.start(self.runRoot, current)
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
		the reason nothing here is done speculatively. What the step lands on is classified
		before it is read, because the siblings of a list item are not all list items.
		"""
		self.budget.startUnlessActive()
		current = blockId.bookmark
		if current is None:
			return FetchResult.failed(f"no object for {blockId}")
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
		if isDecoration(found):
			# A blank row, and walked through rather than stopped at: the groups either side
			# of a separator are one menu, and ending the run at the line would hide half of
			# it. Reading it costs a block of the budget as any other step does.
			return self._blockAt(found, decoration=True)
		if not self.holds(found):
			# Beside the run rather than in it — the button under a list box, the toolbar
			# after a menu. The run ends here, and NVDA presents that object as it always has
			# if the reader goes to it.
			return FetchResult.endOfStream()
		return self._blockAt(found)

	def _blockAt(self, obj, decoration: bool = False) -> FetchResult:
		""":return: a result carrying the block for one object."""
		began = self.budget.clock()
		try:
			return FetchResult.found(self._buildBlock(obj, decoration))
		except Exception as error:
			log.debugWarning("Could not build a block for an object", exc_info=True)
			return FetchResult.failed(f"could not build a block: {error!r}")
		finally:
			self.budget.observe(self.budget.clock() - began)

	def _buildBlock(self, obj, decoration: bool = False) -> SourceBlock:
		"""Build one block from an object.

		The object is the block's own bookmark, so there is nothing here to remember:
		walking on from a block starts from the object that block was built for. An earlier
		version kept a table of every object it had ever read, which grew for the life of the
		run and held on to each of them.
		"""
		blockId = BlockId(generation=self.generation, bookmark=obj, unit=self.unit)
		region = self.regionFactory(obj)
		region.update()
		if obj is self.obj:
			# Noted while it is in hand, so that a redraw can tell an opened node from one
			# that was open when the reader arrived without a second call for it.
			try:
				self._expanded = self.adapter.expandedOf(obj)
			except Exception:
				log.debugWarning("Could not note whether a node is open", exc_info=True)
		return SourceBlock(
			blockId=blockId,
			region=region,
			isBlank=not (getattr(region, "rawText", "") or "").strip(),
			isDecoration=decoration,
			depth=self._depthOf(obj),
		)

	def _depthOf(self, obj) -> Optional[int]:
		""":return: how deep an object sits, or None if the adapter will not say.

		Asked once per block and never speculatively, like everything else in this source: it
		can be a call into the application, and a run is walked one object at a time precisely
		so that nothing is read that the reader will not see.
		"""
		try:
			return self.adapter.depthOf(obj)
		except Exception:
			log.debugWarning(f"The {self.adapter.name} adapter could not say how deep an object sits", exc_info=True)
			return None

	def holds(self, obj) -> bool:
		""":return: whether an object is part of the run this source is reading."""
		if obj is None:
			return False
		if obj is self.runRoot:
			return True
		try:
			if bool(obj == self.runRoot):
				return True
			return bool(self.adapter.admits(self.runRoot, obj))
		except Exception:
			log.debugWarning("Could not tell whether an object is in this run", exc_info=True)
			return False

	def forget(self) -> None:
		"""Kept for the shape of a source. There is nothing cached here to drop."""

	def __repr__(self) -> str:
		return f"<ObjectFlowSource {self.runRoot!r} by {self.adapter.name} generation {self.generation}>"


def belongsTo(source: "ObjectFlowSource", obj) -> bool:
	"""Whether an object is part of the run a source is already reading.

	The question the band asks on every focus change, and in a list the focus changes on
	every arrow key: getting it wrong means throwing the run away and walking it again for
	each item the reader passes, which here is a call into the application per step.

	:param source: the run being read.
	:param obj: what the reader has moved to.
	:return: whether to keep reading the run rather than start again.
	"""
	return source.holds(obj)
