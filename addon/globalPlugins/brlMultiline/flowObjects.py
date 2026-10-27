# BrlMultiline: reading a run of objects as a flow.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Blocks that are objects rather than lines of a document.

Lists, menus, trees and combo box choices, read as a run of objects. One object is one
block, drawn by NVDA's own `NVDAObjectRegion`, so each block says what a single line
display would. See docs/design/spatial-reading-plan.md, Sources, in two flavours.

1. Walking is a call into the application, so nothing reads ahead and the operation budget
   bounds fetching.
2. Nothing moves the selection, which is application state; the flow moves only its window.
3. The focused object still shows the cursor, to say which item the arrow keys act on.
4. Which objects are read this way is a registry; see `ObjectAdapter` and `register`.
5. The walk is over accessibility siblings, a wider set than NVDA's navigation, so each
   step is classified as a member, a decoration such as a separator, or the end of the run.
   See `ObjectAdapter.admits` and `isDecoration`.
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

Named rather than imported, as `flowForms.CONTROL_ROLES` is.
"""

TREE_ITEM_ROLES = frozenset({"TREEVIEWITEM"})
"""Roles read in visible order rather than as a run of siblings. See `VISIBLE_TREE`."""

TREE_ROLES = frozenset({"TREEVIEW", "TREE", "OUTLINE"})
"""What a tree item's containing control calls itself, where it says so.

Only to stop a climb early; the climb also stops at the first ancestor that is not a tree
item.
"""

TRANSPARENT_ROLES = frozenset({"GROUPING"})
"""Roles that hold a tree's rows without being rows themselves.

Some providers, including UIA, hang a tree item's children off a grouping, which is stepped
over in both directions. A named set, since a real boundary such as the tree control is also
not a tree item.
"""

MAX_WRAPPERS = 4
"""How many wrappers deep to look before giving up.

Bounded, so a malformed or cyclic hierarchy costs a few reads.
"""

FIRST_LEVEL = 1
"""The level an item of a run sits at when nothing says otherwise. See `_depthOf`."""

MAX_TREE_DEPTH = 64
"""How far a walk may climb or descend before it decides the tree is lying to it.

A bridge under load can report a cycle, so the walk is bounded, as `MAX_CHILDREN` is.
"""

MENU_ITEM_ROLES = frozenset({"MENUITEM", "CHECKMENUITEM", "RADIOMENUITEM"})
"""The roles one menu mixes freely.

A run is otherwise all of one role; a menu mixes check items and commands.
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

As a mouse click: menu items and tabs act, and everything else takes the focus.
"""

MAX_RUN_DEPTH = 4
"""How far inside a container a declared run is looked for. See `_firstDeclaredWithin`.

Members may be wrapped, as in Teams, two generations down. Shallow enough that a mistaken
declaration does not walk the whole tree.
"""

MAX_CHILDREN = 500
"""How many children of a container will be walked when finding where the focus sits.

A guard against very long lists. Walked one at a time, since NVDA's `children` builds the
whole list first. Past it the run is given up rather than time.
"""

MAX_GROUP_ITEMS = 2000
"""How far the walk to the end of a group will go when the container will not say.

Only the fallback: a container is first asked for its last child, one call. Reaching this
limit returns nothing, rather than a row partway through the group.
"""

MAX_RUN_SEARCH = 500
"""How many objects the search for a declared run may look at, all told.

One budget for the whole walk rather than per node, so the worst case is a number. The same
judgement as `MAX_CHILDREN`.
"""

SELECTION_NAMED = "brlMultilineFlowNamedFromSelection"
"""The attribute an application sets to say its objects are named after the selection.

Set on an overlay class, as `RUN_DECLARATION` is, and read by `namedFromTheSelection`. Such
an object is read while the reader is on it and not re-read afterwards.
"""

OUTLOOK = "outlook"
"""The application whose message rows are named from the selection. See `namedFromTheSelection`."""

OUTLOOK_ROW = "UIAGridRow"
"""NVDA's own name for the Outlook class whose `_get_name` reads `activeExplorer().selection`.

Matched against the names in an object's `mro`, because an overlay class is assembled per
object and is not this class by identity.
"""

RUN_DECLARATION = "brlMultilineFlowRun"
"""The attribute an application sets to say its objects read as a run.

This name and the three below are a public contract with application code. An app module
sets them on its overlay class with no import of this add-on, so it behaves the same when
BrlMultiline is absent. Unlike `register`, which needs an import and a load order.

The worked case is Microsoft Teams, whose messages are wrapped so no sibling walk or
`simpleNext` reaches the next one; only the app module knows how to step it.
"""

RUN_NEXT = "brlMultilineFlowNext"
"""A method an application may supply to step forward through its run, returning None at the end."""

RUN_PREVIOUS = "brlMultilineFlowPrevious"
"""A method an application may supply to step back through its run, returning None at the start."""

RUN_CONTAINER = "brlMultilineFlowRunContainer"
"""The attribute a container sets to say the run is somewhere inside it.

For a pin, which is handed the container rather than a member declaring itself with
`RUN_DECLARATION`. In Teams the members are grandchildren. Which to read first is the
container's to say, through `RUN_START`.
"""

RUN_START = "brlMultilineFlowRunStart"
"""A method a container may supply, returning the member to begin reading at.

Without it the container is searched for its first declared descendant, which is wrong for
a chat, where the newest is wanted.
"""

RUN_ADMITS = "brlMultilineFlowAdmits"
"""A method an application may supply to say whether another object is in the same run.

Rarely wanted: the default admits anything else carrying L{RUN_DECLARATION}, which is right
unless two different declared runs can sit next to each other.
"""

LINE_CHARACTERS = frozenset("-_=~.*" + "‐‑‒–—―─━┄┅")
"""What a separator is drawn out of, for the fallback in `isDecoration`.

The role is still the test; this is only the fallback."""


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

	`positionInfo["level"]`, which each API and NVDA's app modules fill in. An object with no
	level, such as an ordinary list item, has no depth and draws flat.

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

	None for a leaf, which reports neither state, so it is not taken as collapsed.
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


def _isTransparent(obj) -> bool:
	""":return: whether an object holds rows without being one. See `TRANSPARENT_ROLES`."""
	return obj is not None and roleName(getattr(obj, "role", None)) in TRANSPARENT_ROLES


def _rowBelow(obj):
	""":return: the first row inside an object, stepping through any wrapper in the way.

	:param obj: the node whose contents are wanted.
	:return: a tree item, or None if what is inside is neither a row nor a wrapper.
	"""
	node = obj
	for _ in range(MAX_WRAPPERS):
		try:
			child = getattr(node, "firstChild", None)
		except Exception:
			log.debugWarning("Could not read a node's first child", exc_info=True)
			return None
		if child is None:
			return None
		if _isTreeItem(child):
			return child
		if not _isTransparent(child):
			return None
		node = child
	return None


def _rowAbove(obj):
	""":return: the nearest ancestor that is not scenery, stepping through any wrapper.

	May be a parent row or the tree control itself; the caller tells them apart.

	:param obj: the node whose parent is wanted.
	:return: the ancestor, or None.
	"""
	node = obj
	for _ in range(MAX_WRAPPERS):
		try:
			parent = getattr(node, "parent", None)
		except Exception:
			log.debugWarning("Could not climb to a node's parent", exc_info=True)
			return None
		if parent is None or not _isTransparent(parent):
			return parent
		node = parent
	return None


def _firstVisibleChild(obj):
	""":return: a node's first child if the reader can see it, else None.

	Gated on the expanded state, since a tree control such as `sysTreeView32` gives back the
	children of a collapsed node too.

	The child may be a wrapper's child rather than this node's own. See `TRANSPARENT_ROLES`.
	"""
	if not _isExpanded(obj):
		return None
	return _rowBelow(obj)


def _lastVisibleDescendant(obj):
	""":return: the last row of a node's visible subtree, which is the row before its next
	sibling.

	Backward, a node is preceded by the deepest last row under the sibling above it, so
	panning back retraces panning forward.
	"""
	node = obj
	for _ in range(MAX_TREE_DEPTH):
		child = _firstVisibleChild(node)
		if child is None:
			return node
		# Asked of the first row's own container, one call (see `_lastChildOf`), since the
		# node's last child may be a wrapping grouping. Checked for being a row.
		last = _lastChildOf(_containerOf(child))
		if not _isTreeItem(last):
			last = child
			for _ in range(MAX_GROUP_ITEMS):
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

	The visible child, else the next sibling, else the next sibling of the nearest ancestor
	that has one.
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
		node = _rowAbove(node)
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
	parent = _rowAbove(obj)
	return parent if _isTreeItem(parent) else None


def _treeOf(obj):
	""":return: the control a tree item belongs to, or None.

	By climbing until something is not a tree item, since many trees' containers do not use
	a role in `TREE_ROLES`, which only stops the climb early.
	"""
	node = obj
	for _ in range(MAX_TREE_DEPTH):
		parent = _rowAbove(node)
		if parent is None:
			return None
		if roleName(getattr(parent, "role", None)) in TREE_ROLES or not _isTreeItem(parent):
			return parent
		node = parent
	return None


def _isSameTree(root, candidate) -> bool:
	""":return: whether a candidate is another visible row of the tree `root` is in.

	Not `_sameParent`, since a child of an expanded node is the next row but has a
	different parent.
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

	One for an item whose parent is the tree control. Bounded, and stops where `_treeOf`
	stops.
	"""
	if not _isTreeItem(obj):
		return None
	depth = 1
	node = obj
	for _ in range(MAX_TREE_DEPTH):
		parent = _rowAbove(node)
		if not _isTreeItem(parent):
			return depth
		# A wrapper between two rows is not a level. Counting it as one would draw a whole
		# subtree a step further in than the tree it is in, which is the same wrong picture
		# that made the counted depth worth preferring in the first place.
		depth += 1
		node = parent
	return depth


def _treeDepth(obj) -> Optional[int]:
	""":return: how deep a tree item sits, from the structure the walk went through.

	The counted depth wins where it found real nesting, so the depth drawn comes from the
	same structure the walk stepped through; Outlook's folder pane reports the same level for
	an account and the Inbox under it. Where the count is one, `positionInfo` is used, for an
	outline whose items are all direct children of the control.
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

	Same parent and roles of a kind, so a button in a list box or a separator in a menu is
	not taken as a member.
	"""
	if root is None or candidate is None:
		return False
	if not _isRunMember(candidate):
		return False
	role = roleName(getattr(candidate, "role", None))
	if not _sameKind(role, roleName(getattr(root, "role", None))):
		return False
	return _sameParent(root, candidate)


RUN_CONTAINERS = frozenset({"LIST", "LISTBOX", "TABLE", "DATAGRID", "TREEVIEW", "PANE"})
"""What a grouped list may call itself, and so what bounds the walk across its groups.

Where the walk stops, so it does not climb past the last group into a toolbar.
"""

GROUP_ROLES = frozenset({"GROUPING"})
"""What a list puts between itself and its items when it groups them.

As Outlook's message list groups by day. Narrow, since a grouping is usually a real
boundary; crossed only for a run member in one of these inside a list.
"""

MAX_GROUPS = 200
"""How many groups a walk may cross before it decides the list is lying to it.

A bound against cycles, as `MAX_TREE_DEPTH` is.
"""


def _groupOf(obj):
	""":return: the group an item sits in, or None if its list does not group its items.

	The parent must be a grouping, held inside a list, so an ordinary list box answers no.

	:param obj: a run member.
	"""
	try:
		group = getattr(obj, "parent", None)
	except Exception:
		log.debugWarning("Could not ask an object what it is in", exc_info=True)
		return None
	if group is None or roleName(getattr(group, "role", None)) not in GROUP_ROLES:
		return None
	return group if _listHolding(group) is not None else None


def _listHolding(group):
	""":return: the list a group belongs to, or None.

	What bounds the walk. See `RUN_CONTAINERS`.

	:param group: the grouping.
	"""
	try:
		above = getattr(group, "parent", None)
	except Exception:
		log.debugWarning("Could not ask a group what holds it", exc_info=True)
		return None
	return above if above is not None and roleName(getattr(above, "role", None)) in RUN_CONTAINERS else None


def _listOf(obj):
	""":return: the grouped list an object belongs to, or None.

	Asked of an item and of a group heading alike, because both are rows of the same run.
	"""
	if roleName(getattr(obj, "role", None)) in GROUP_ROLES:
		return _listHolding(obj)
	group = _groupOf(obj)
	return _listHolding(group) if group is not None else None


def _isInAGroupedList(obj) -> bool:
	""":return: whether an object is a row of a list whose items are grouped.

	**Narrow on purpose.** This adapter is asked before the ordinary sibling run, and what it
	does differently is cross a boundary that is real in most controls; so it matches only the
	shape it was written for — a run member inside a grouping inside a list, or the grouping
	itself.

	A heading matches on the list that holds it and on nothing else. Asking whether it had a
	child the reader could reach as well meant a *collapsed* day was not a grouped list: the
	generic run took it instead and began at a message Outlook still keeps in the tree behind
	a closed group, and the walk across the days was lost at exactly the row that names them.
	A group with nothing open under it is a row of its own, and `_groupedNext` steps from it
	to the day beside it.
	"""
	if roleName(getattr(obj, "role", None)) in GROUP_ROLES:
		return _listHolding(obj) is not None
	return _isRunMember(obj) and _groupOf(obj) is not None


def _isInTheSameList(root, candidate) -> bool:
	""":return: whether two rows belong to one grouped list.

	Compares the list they are in, since items of different groups have different parents.
	Headings are admitted too; see `_groupedNext`.
	"""
	if root is None or candidate is None:
		return False
	if not (_isRunMember(candidate) or roleName(getattr(candidate, "role", None)) in GROUP_ROLES):
		return False
	here = _listOf(root)
	there = _listOf(candidate)
	if here is None or there is None:
		return False
	return _isTheSameObject(here, there)


def _isTheSameObject(first, second) -> bool:
	""":return: whether two references are to one object, by equality where identity fails."""
	if first is second:
		return True
	try:
		return bool(first == second)
	except Exception:
		log.debugWarning("Could not compare two containers", exc_info=True)
		return False


def _firstInGroup(group):
	""":return: the first item of a group, or None where it holds none the reader can reach.

	Nothing is descended into while a group is closed: a collapsed day is a heading and no
	rows, exactly as it is on the screen.
	"""
	if _isExpanded(group) is False:
		return None
	try:
		found = getattr(group, "firstChild", None)
	except Exception:
		log.debugWarning("Could not look inside a group", exc_info=True)
		return None
	return found if _isRunMember(found) else None


def _containerOf(obj):
	""":return: the object's own parent, whatever that parent is, or None.

	Not `_rowAbove`, which climbs past scenery. This is what can be asked for its last child.
	"""
	if obj is None:
		return None
	try:
		return getattr(obj, "parent", None)
	except Exception:
		log.debugWarning("Could not read an object's parent", exc_info=True)
		return None


def _lastChildOf(container):
	""":return: a container's last child as it says itself, or None where it will not say.

	One call, instead of walking from the first child. The caller checks the answer belongs
	to the run, since a list may end with a footer or placeholder.
	"""
	if container is None:
		return None
	try:
		return getattr(container, "lastChild", None)
	except Exception:
		log.debugWarning("Could not ask a container for its last child", exc_info=True)
		return None


def _lastInGroup(group):
	""":return: the last item of a group, or None. See `_firstInGroup`.

	None also when it cannot be answered; `_groupedPrevious` then offers the group heading
	rather than a guessed end. See `MAX_GROUP_ITEMS`.
	"""
	found = _firstInGroup(group)
	if found is None:
		return None
	last = _lastChildOf(group)
	if last is found:
		return found
	if last is not None and _isRunMember(last):
		return last
	for _ in range(MAX_GROUP_ITEMS):
		try:
			after = getattr(found, "next", None)
		except Exception:
			log.debugWarning("Could not walk to the end of a group", exc_info=True)
			return found
		if not _isRunMember(after):
			return found
		found = after
	log.debugWarning(f"A group held more than {MAX_GROUP_ITEMS} rows, so where it ends is unknown")
	return None


def _groupBeside(group, forward: bool):
	""":return: the next or previous group of the same list, or None at its end.

	Anything between two groups that is not a group is stepped over, bounded by `MAX_GROUPS`:
	a list may put a separator or an empty pane between its days, and neither is a row.
	"""
	node = group
	for _ in range(MAX_GROUPS):
		try:
			node = getattr(node, "next" if forward else "previous", None)
		except Exception:
			log.debugWarning("Could not walk to the next group", exc_info=True)
			return None
		if node is None:
			return None
		if roleName(getattr(node, "role", None)) in GROUP_ROLES:
			return node
	return None


def _groupedNext(obj):
	""":return: the row below this one on the screen, crossing group boundaries.

	In screen order: a heading, its group's items, then the next heading. The heading is a
	row, so the reader knows the group changed.
	"""
	if roleName(getattr(obj, "role", None)) in GROUP_ROLES:
		inside = _firstInGroup(obj)
		return inside if inside is not None else _groupBeside(obj, True)
	after = _siblingNext(obj)
	if _isRunMember(after):
		return after
	group = _groupOf(obj)
	return _groupBeside(group, True) if group is not None else None


def _groupedPrevious(obj):
	""":return: the row above this one on the screen. The mirror of `_groupedNext`.

	Not its exact mirror, and that is the point: above the first message of a day is that
	day's heading, and above a heading is the *last* message of the day before it.
	"""
	if roleName(getattr(obj, "role", None)) in GROUP_ROLES:
		before = _groupBeside(obj, False)
		if before is None:
			return None
		return _lastInGroup(before) or before
	previous = _siblingPrevious(obj)
	if _isRunMember(previous):
		return previous
	return _groupOf(obj)


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

	Such as a menu separator, which the sibling walk meets. The role is the test, with a narrow
	fallback for a toolkit that exposes its line as a disabled item: not focusable, no action,
	and named only with line characters. Disabled alone is never enough.

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

	A registry entry, so an add-on that owns a control can supply its own.
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

	Depth is part of a control's shape, which an adapter may know better than NVDA. How it is
	drawn is `flowIndent`'s concern.
	"""

	openChildOf: Callable[[Any], Any] = lambda obj: None
	"""The first row inside one of these, or None where it is showing none.

	Asked only of an open node, since a provider that fetches children on demand reports the
	node open before it has any. See `shapeChanged`.
	"""

	expandedOf: Callable[[Any], Optional[bool]] = lambda obj: None
	"""Whether one of these is showing its children, or None where the idea does not apply.

	How the band notices a node being expanded, which sends no event. See
	`ObjectFlowSource.shapeChanged`.
	"""


RUN_PARENTS = frozenset({"LIST", "LISTBOX"})
"""Containers all of whose children are one run, whatever the children call themselves.

For list items that report another role, such as `CHECKBOX` in a checkbox list view. Narrower
than `RUN_CONTAINERS`: no pane, which holds anything; no tree, which `VISIBLE_TREE` reads; no
table, which `flowTableSource` reads.
"""


def _isRunMember(obj) -> bool:
	""":return: whether an object is one of a run of siblings.

	What it calls itself, or what holds it. See `RUN_PARENTS`.
	"""
	if roleName(getattr(obj, "role", None)) in RUN_ROLES:
		return True
	try:
		parent = getattr(obj, "parent", None)
	except Exception:
		log.debugWarning("Could not ask an object what holds it", exc_info=True)
		return False
	return parent is not None and roleName(getattr(parent, "role", None)) in RUN_PARENTS


def _hasChoices(obj) -> bool:
	""":return: whether an object's children are a run the reader is choosing from."""
	if roleName(getattr(obj, "role", None)) not in CHOICE_ROLES:
		return False
	return _focusedChild(obj) is not None


def _focusedChild(obj):
	"""Find which child of a container the reader is on.

	The selected one, the focused one, or the first. Walked from `firstChild`; see
	`MAX_CHILDREN`.

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

	In an open combo box the focus moves to the choice itself, so the answer is where the
	reader is, without walking the container's children again.
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


def _rowTextFor(obj, headers: Optional[dict] = None) -> str:
	""":return: a row built from its own cells, or "" where that cannot be done.

	Failures are contained here, so such a row is read the ordinary way.

	:param headers: where to remember what each column is called, so that a band of rows asks
		the platform once per column. Kept by the source, since a run is one list.
	"""
	from . import flowObjectTable

	try:
		return flowObjectTable.rowTextOf(obj, headers=headers)
	except Exception:
		log.debugWarning("Could not build a row from its cells", exc_info=True)
		return ""


class _NamedAs:
	"""An object with one property answered differently, for the length of one reading.

	Every attribute but `name` is the real object's. Made and thrown away inside
	`FlowObjectRegion.update`, which goes back to the real object before anything can route
	into it. This keeps NVDA's own `NVDAObjectRegion` presentation, changing only the name.
	"""

	def __init__(self, obj, name: str) -> None:
		self._obj = obj
		self.name = name

	def __getattr__(self, attribute: str):
		return getattr(self._obj, attribute)

	def __repr__(self) -> str:
		return f"<{self._obj!r} named {self.name!r}>"


def namedFromTheSelection(obj) -> bool:
	""":return: whether an object's text describes what is selected rather than what it is.

	NVDA's Outlook module builds a message row's name partly from the current selection, so
	re-read elsewhere it describes another row. Declared through `SELECTION_NAMED`, as with
	`RUN_DECLARATION`, and known here for Outlook's grid rows by NVDA's class name. If NVDA
	renames that class, only Outlook is affected.

	:param obj: the object about to be read again.
	"""
	try:
		if bool(getattr(obj, SELECTION_NAMED, False)):
			return True
	except Exception:
		log.debugWarning("Could not ask an object how it is named", exc_info=True)
		return False
	return _isAnOutlookMessageRow(obj)


def _isAnOutlookMessageRow(obj) -> bool:
	""":return: whether an object is one of Outlook's message rows.

	By application, and by `UIAGridRow` in the class's `mro`, the class in
	`appModules/outlook.py` whose `_get_name` reads the selection.
	"""
	try:
		if getattr(getattr(obj, "appModule", None), "appName", None) != OUTLOOK:
			return False
		return any(cls.__name__.startswith(OUTLOOK_ROW) for cls in type(obj).__mro__)
	except Exception:
		log.debugWarning("Could not ask what an object is", exc_info=True)
		return False


def _isDeclaredRun(obj) -> bool:
	""":return: whether an application has declared that its object reads as a run.

	Read off the object rather than through `register`, so the app module needs no import.
	See `RUN_DECLARATION`.
	"""
	try:
		return bool(getattr(obj, RUN_DECLARATION, False))
	except Exception:
		# An object that raises on an attribute read is one to leave to NVDA.
		log.debugWarning("Could not read a run declaration", exc_info=True)
		return False


def _declaresRunInside(obj) -> bool:
	""":return: whether a container has declared that a run of its own lies inside it."""
	try:
		return bool(getattr(obj, RUN_CONTAINER, False))
	except Exception:
		log.debugWarning("Could not read a run container declaration", exc_info=True)
		return False


def _matchesDeclared(obj) -> bool:
	""":return: whether this adapter reads an object, as a member of a run or as its container."""
	return _isDeclaredRun(obj) or _declaresRunInside(obj)


def _firstDeclaredWithin(root, depth: int = MAX_RUN_DEPTH, budget: int = MAX_RUN_SEARCH):
	"""Search a container for a member of the run it says it holds.

	The fallback for a container with no `RUN_START`. One budget for the whole search (see
	`MAX_RUN_SEARCH`), to a limited depth, since members may be grandchildren as in Teams.
	Shallowest first.

	:param root: the container to search.
	:param depth: how many generations below it to look.
	:param budget: how many objects may be looked at, over the whole search.
	:return: the first declared member found, or None.
	"""
	if root is None or depth <= 0:
		return None
	looked = 0
	generation = [(root, depth)]
	while generation:
		below = []
		for node, left in generation:
			child = _firstChildOf(node)
			while child is not None:
				if looked >= budget:
					log.debugWarning(f"Gave up looking for a declared run inside {root!r}")
					return None
				looked += 1
				if _isDeclaredRun(child):
					return child
				if left > 1:
					below.append((child, left - 1))
				child = _nextOf(child)
		generation = below
	return None


def _firstChildOf(obj):
	""":return: an object's first child, or None where it has none or will not say."""
	try:
		return getattr(obj, "firstChild", None)
	except Exception:
		log.debugWarning("Could not read an object's first child", exc_info=True)
		return None


def _nextOf(obj):
	""":return: the object after this one, or None where there is none or it will not say."""
	try:
		return getattr(obj, "next", None)
	except Exception:
		log.debugWarning("Could not read the object after one", exc_info=True)
		return None


def _declaredStart(root, current):
	"""Which member of a declared run to begin reading at.

	Where the reader is, if on a member; else what the container says, as for a pin; else the
	first member found inside it.

	:param root: what the run was found from: a member, or a container holding one.
	:param current: where the reader is now, which may be the container itself.
	:return: the member to read at, or None if the run has nothing in it.
	"""
	if _isDeclaredRun(current):
		return current
	if root is None:
		return current
	starter = getattr(root, RUN_START, None)
	if starter is not None:
		try:
			found = starter()
		except Exception:
			log.debugWarning("A container could not say where its run starts", exc_info=True)
			found = None
		if found is not None:
			return found
	if _isDeclaredRun(root):
		return root
	return _firstDeclaredWithin(root)


def _admitsDeclared(root, candidate) -> bool:
	""":return: whether a candidate belongs to a declared run.

	The application's own answer, else any object with the same declaration. Not
	`_sameParent`, since declared members need not be siblings.
	"""
	if root is None or candidate is None:
		return False
	own = getattr(root, RUN_ADMITS, None)
	if own is not None:
		try:
			return bool(own(candidate))
		except Exception:
			log.debugWarning("An application could not judge its own run's membership", exc_info=True)
			return False
	return _isDeclaredRun(candidate)


def _declaredStep(obj, attribute: str, fallback):
	"""Step through a declared run, by the application's own walk where it has one.

	Without a walk the objects are taken as siblings; a wrong step ends the run in
	`ObjectFlowSource._step`, giving a short reading rather than a wrong one.

	:param obj: where the walk is now.
	:param attribute: the method an application may supply for this direction.
	:param fallback: how to step when it supplies none.
	:return: the next object of the run, or None at its end.
	"""
	walk = getattr(obj, attribute, None)
	if walk is None:
		return fallback(obj)
	try:
		return walk()
	except Exception:
		# The application's own walk failed. Ending the run is the honest answer: falling
		# back to siblings here would step somewhere the application has already said is not
		# how its run is joined together.
		log.debugWarning(f"An application's {attribute} failed", exc_info=True)
		return None


def _declaredNext(obj):
	""":return: the object after this one in a declared run, or None."""
	return _declaredStep(obj, RUN_NEXT, _siblingNext)


def _declaredPrevious(obj):
	""":return: the object before this one in a declared run, or None."""
	return _declaredStep(obj, RUN_PREVIOUS, _siblingPrevious)


VISIBLE_TREE = ObjectAdapter(
	name="visibleTree",
	matches=_isTreeItem,
	admits=_isSameTree,
	nextOf=_treeNext,
	previousOf=_treePrevious,
	depthOf=_treeDepth,
	expandedOf=_isExpanded,
	openChildOf=_firstVisibleChild,
)
"""A tree, read down the screen rather than along one generation of it.

A sibling walk would step over an expanded node's contents. Visible order, decision 1 of
docs/design/structured-presentation-plan.md. Expanding a node is noticed through
`shapeChanged`.
"""

GROUPED_LIST = ObjectAdapter(
	name="groupedList",
	matches=_isInAGroupedList,
	admits=_isInTheSameList,
	nextOf=_groupedNext,
	previousOf=_groupedPrevious,
	expandedOf=_isExpanded,
	openChildOf=_firstInGroup,
)
"""A list whose items are grouped, read down the screen across the groups.

Such as Outlook's message list grouped by day, where the sibling run would stop at each
group. The tree's walk over a list: down a group, across to the next heading, which is a row;
a collapsed group is a heading alone. Membership is the list, which stops the climb. Asked
before `SIBLING_RUN` and matching only a run member in a grouping in a list.
"""

SIBLING_RUN = ObjectAdapter(name="siblings", matches=_isRunMember)
"""A list item, a tree item, a menu item: the reader is in a run and the run is its siblings."""

CHOICES = ObjectAdapter(name="choices", matches=_hasChoices, start=_chosenChild, admits=_isChild)
"""A container the reader is choosing from, whose run is its children."""

DECLARED_RUN = ObjectAdapter(
	name="declared",
	matches=_matchesDeclared,
	start=_declaredStart,
	admits=_admitsDeclared,
	nextOf=_declaredNext,
	previousOf=_declaredPrevious,
)
"""A run an application's own code has declared, and usually stepped through itself.

Asked before the built-in adapters, because the built-in answer for a kind of control is a
guess about every control of that kind and the application's is about this one.
"""

_adapters: list[ObjectAdapter] = [DECLARED_RUN, VISIBLE_TREE, GROUPED_LIST, SIBLING_RUN, CHOICES]
"""The adapters, in the order they are asked. First match wins."""


def register(adapter: ObjectAdapter, first: bool = True) -> None:
	"""Add a way of reading a kind of object as a run.

	:param adapter: the adapter.
	:param first: ask it before the built-in ones, as an add-on for its own control wants.
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
	objectRegion = _objectRegionClass(live, adapter)
	decorationRegion = _decorationRegionClass()

	def factory(obj):
		if isDecoration(obj):
			return decorationRegion(obj)
		return objectRegion(obj)

	return factory


class _LineMoves:
	"""NVDA's line commands on a block of a run, passed to its `onLine`."""

	onLine = None

	def nextLine(self) -> None:
		"""Move on by one item, as the braille display's line command asks.

		The window only, since here the cursor is a selection. See the module docstring.
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


def _objectRegionClass(live: bool, adapter: Optional[ObjectAdapter]) -> type:
	""":return: the region class an object block is read through. See `regionFactory`."""
	from braille.regions.NVDAObject import NVDAObjectRegion

	class FlowObjectRegion(_LineMoves, NVDAObjectRegion):
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

			self.fromItsOwnCells = False
			"""Whether to build this row's line out of its cells rather than from its name.

			Set by the source before the first reading, since only the source knows which
			object the reader is on. See `ObjectFlowSource._buildBlock` and
			`flowObjectTable.rowTextOf`."""

			self.columnHeaders = None
			"""Where to remember what each column of this list is called, from the source."""

			self.onLine = None
			"""Called with a direction when NVDA's line commands reach this region.

			They must reach something. `script_braille_nextLine` calls `regions[-1].nextLine()`
			with no check beyond the list being non-empty, and a region that has no such method
			raises where the reader pressed a key. See the last-region audit."""

		def update(self) -> None:
			# Before the base class, which is what turns a position into `brailleCursorPos`.
			# The start of the block: what the cursor says here is "this is the one you are
			# on", not "this is the character you are at".
			self.cursorPos = 0 if (self.isActive and live) else None
			said = _rowTextFor(self.obj, self.columnHeaders) if self.fromItsOwnCells else ""
			if not said:
				super().update()
				self.dirty = True
				return
			# The object goes back before anything else can reach it: routing, acting and the
			# controller all use `self.obj`, and only the reading is done through the
			# stand-in. See L{_NamedAs}.
			row = self.obj
			self.obj = _NamedAs(row, said)
			try:
				super().update()
			finally:
				self.obj = row
			self.dirty = True

		def routeTo(self, braillePos: int) -> None:
			"""Go to an object, or act on the one already there.

			A press on the reader's own block acts, as NVDA's region does; a press elsewhere moves
			the focus there. Where the adapter says a routing key acts, as on a tab strip, the
			focus is taken and then the action follows, as `ReviewNVDAObjectRegion` does.
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

	return FlowObjectRegion


def _decorationRegionClass() -> type:
	""":return: the region class a line between groups is read through. See `regionFactory`."""
	from braille.regions.base import Region

	class FlowDecorationRegion(_LineMoves, Region):
		"""A line drawn between groups, shown as the blank row it is.

		Blank rather than absent, so the grouping can be felt. Holds no text, so nothing routes
		into it.
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

		def __repr__(self) -> str:
			return f"<FlowDecorationRegion {getattr(self.obj, 'role', None)}>"

	return FlowDecorationRegion


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

		Fixed for the life of the source, separate from `obj`, which moves with the reader."""

		self.obj = obj
		"""Where in the run the reader is now. See `setCurrent`."""

		self.adapter = adapter
		self.regionFactory = regionFactory
		self.generation = generation
		self.budget = budget if budget is not None else FetchBudget()
		self.unit = "object"
		"""What a block is here, for the log and for the dry run's report."""

		self._columnHeaders: dict = {}
		"""What each column of this list is called, filled in as rows are read.

		Kept for the life of the source, so each column is asked once. See
		`flowObjectTable.rowTextOf`."""

		self._shape: Optional[tuple] = None
		"""What the run looked like under the object the reader is on, when last looked at.

		Whether it was open, and whether it was showing anything. None until something has
		looked, which is what makes the first `shapeChanged` answer no rather than reporting
		a change from nothing. See `shapeChanged`."""

	def setCurrent(self, obj) -> None:
		"""Say where in the run the reader has moved to.

		:param obj: the object they are on now, which the run itself is unchanged by.
		"""
		self.obj = obj
		# Forgotten rather than re-read, so that arriving somewhere already open does not
		# read as something having just been opened. The next `shapeChanged` fills it in and
		# answers no, which is the honest answer for a reader who has only just got here.
		self._shape = None

	def shapeChanged(self) -> bool:
		"""Whether the run has been opened or closed under the reader since it was last read.

		Expanding a node sends no event the band sees. Only the reader's own object is asked,
		since it is the only one they can have opened. Both the state and, for an open node,
		whether it shows any rows yet are watched, since children can arrive after the state
		changes. Flat lists and leaves pay nothing.

		:return: whether to read the run again.
		"""
		obj = self.obj
		if obj is None:
			return False
		was, hadChild = self._shape if self._shape is not None else (None, None)
		now, hasChild = self._shapeUnder(obj)
		self._shape = (now, hasChild)
		if was is None or now is None:
			# Nothing to compare with, or nothing that can be opened at all. A leaf reports
			# neither state, and reading that as "closed" would make arriving on one look like
			# something having just shut.
			return False
		if was != now:
			return True
		# Open before and open now, so the only change left that moves rows is the contents
		# arriving or going away underneath it.
		return now is True and hadChild is not None and hasChild != hadChild

	def _shapeUnder(self, obj) -> tuple:
		""":return: whether a node is open, and whether it is showing anything. See
		`shapeChanged`."""
		try:
			expanded = self.adapter.expandedOf(obj)
		except Exception:
			log.debugWarning("Could not tell whether a node has been opened", exc_info=True)
			return (None, None)
		if expanded is not True:
			return (expanded, None)
		try:
			return (True, self.adapter.openChildOf(obj) is not None)
		except Exception:
			log.debugWarning("Could not tell whether an open node is showing anything", exc_info=True)
			return (True, None)

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

		A call into the application. What it lands on is classified before it is read.
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
			return FetchResult.endOfStream(f"nothing {'after' if forward else 'before'} this in the run")
		if isDecoration(found):
			# A blank row, and walked through rather than stopped at: the groups either side
			# of a separator are one menu, and ending the run at the line would hide half of
			# it. Reading it costs a block of the budget as any other step does.
			return self._blockAt(found, decoration=True)
		if not self.holds(found):
			# Beside the run rather than in it — the button under a list box, the toolbar
			# after a menu. The run ends here, and NVDA presents that object as it always has
			# if the reader goes to it.
			self._sayWhereItEnded(current, found)
			return FetchResult.endOfStream("the next object is beside this run rather than in it")
		return self._blockAt(found)

	def _sayWhereItEnded(self, current, found) -> None:
		"""Write down what the walk stopped at, for the log.

		The end of a run is a normal answer that would otherwise log nothing. Logs the adapter,
		both objects, their roles and parents, once per ending.

		:param current: the object walked from.
		:param found: what the walk landed on and would not admit.
		"""
		try:
			# Imported here rather than at the top: `flowBuild` reads this module, so the other
			# way round is a circle, and this runs once at the end of a run.
			from .flowBuild import describeObject

			log.debug(
				f"BrlMultiline run: the {self.adapter.name} run ends after "
				f"{describeObject(current)} — the next object is {describeObject(found)}, "
				f"whose parent is {describeObject(getattr(found, 'parent', None))} against "
				f"{describeObject(getattr(current, 'parent', None))}.",
			)
		except Exception:
			log.debugWarning("Could not say where a run ended", exc_info=True)

	def blockAt(self, blockId: BlockId) -> FetchResult:
		""":return: one block again, read afresh from the object it was built for.

		An object is its own bookmark, so this is simple. It lets a pinned list keep up through
		`FlowController.rereadContent`. Structural change is `shapeChanged`'s concern. Refused
		only for an object named from the selection (see `namedFromTheSelection`), which keeps
		its old block; see `FlowController._rereadBlocks`.
		"""
		obj = blockId.bookmark
		if obj is None:
			return FetchResult.failed(f"no object behind {blockId}")
		if not self.isCurrentObject(obj) and namedFromTheSelection(obj):
			return FetchResult.failed("this object is named from the selection, not from itself")
		self.budget.startUnlessActive()
		return self._blockAt(obj, decoration=isDecoration(obj))

	def sameObject(self, first, second) -> bool:
		""":return: whether two references are to the same object. See L{sameObject}.

		A method as well as a function so that a caller holding only a source — the controller
		does — can ask without knowing which module the rule lives in.
		"""
		return sameObject(first, second)

	def neighbourOf(self, obj, forward: bool):
		""":return: the object the run puts beside this one, or None at its end.

		The step `_step` takes, without building a block, for
		`FlowController.runStillHoldsTheBand`. Decorations are neighbours too, matching the band.

		:param obj: where to step from.
		:param forward: which way.
		"""
		if obj is None:
			return None
		try:
			return self.adapter.nextOf(obj) if forward else self.adapter.previousOf(obj)
		except Exception:
			log.debugWarning(f"Could not look {'on' if forward else 'back'} from an object", exc_info=True)
			return None

	def isCurrentObject(self, obj) -> bool:
		""":return: whether one object is the one the run is being read from.

		By equality; see `sameObject`. A run with no current object answers no.

		:param obj: the object to compare with the current one.
		"""
		return self.obj is not None and sameObject(obj, self.obj)

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

		The object is the block's own bookmark, so nothing else is remembered.
		"""
		blockId = BlockId(generation=self.generation, bookmark=obj, unit=self.unit)
		region = self.regionFactory(obj)
		# A row whose name is about the selection is read from its own cells while the reader
		# is somewhere else, and from its name while they are on it — where the selection *is*
		# this row and every word of the name is true of it. See `flowObjectTable.rowTextOf`.
		region.fromItsOwnCells = namedFromTheSelection(obj) and not self.isCurrentObject(obj)
		region.columnHeaders = self._columnHeaders
		region.update()
		if obj is self.obj:
			# Noted while it is in hand, so that a redraw can tell an opened node from one
			# that was open when the reader arrived without a second call for it.
			self._shape = self._shapeUnder(obj)
		return SourceBlock(
			blockId=blockId,
			region=region,
			isBlank=not (getattr(region, "rawText", "") or "").strip(),
			isDecoration=decoration,
			depth=self._depthOf(obj),
		)

	def _depthOf(self, obj) -> Optional[int]:
		"""How deep an object sits.

		The adapter's answer, or the first level when it has none. A flat list still draws at
		the left margin, but every item gets the hanging indent for wrapped rows, which
		`IndentPlan` needs a depth for. Documents do not come through here. Asked once per block.

		:param obj: the object a block is being built for.
		:return: its one-based level.
		"""
		try:
			told = self.adapter.depthOf(obj)
		except Exception:
			log.debugWarning(
				f"The {self.adapter.name} adapter could not say how deep an object sits", exc_info=True
			)
			told = None
		return FIRST_LEVEL if told is None else told

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


def sameObject(first, second) -> bool:
	""":return: whether two references are to the same thing.

	Equality, not identity, since NVDA builds a fresh wrapper on every fetch. An object that
	will not compare counts as different, which costs only a re-read.
	"""
	if first is second:
		return True
	if first is None or second is None:
		return False
	try:
		return bool(first == second)
	except Exception:
		log.debugWarning("Could not compare two objects", exc_info=True)
		return False


def belongsTo(source: "ObjectFlowSource", obj) -> bool:
	"""Whether an object is part of the run a source is already reading.

	Asked on every focus change, which in a list is every arrow key, so a wrong no would walk
	the run again each time.

	:param source: the run being read.
	:param obj: what the reader has moved to.
	:return: whether to keep reading the run rather than start again.
	"""
	return source.holds(obj)
