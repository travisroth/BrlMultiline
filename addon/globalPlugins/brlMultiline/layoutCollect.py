# BrlMultiline: gathering what the browser reports about a page's layout.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""What the browser reports about where things are on a page, gathered for `layoutRegions`.

The rules in `layoutRegions` are pure and are tested against pages saved by
`spikes/layoutOverviewProbe.py`. This module gathers the same three things live, in the same
shape, so that what the add-on finds on a page is what a saved copy of it would give:

1. **Candidates**: landmarks, headings of levels 1 and 2, search fields and buttons, frames and
   data tables, each with its rectangle and the IAccessible2 attributes the rules read. Found
   through browse mode's own quick navigation, which is how the Elements List finds them.
2. **Leaves**: rectangles of links, headings, pictures and fields, for splitting an area by the
   gaps between its content.
3. **The accessible tree**, only on a page with no landmarks: there the layout tables and the
   divs with telling ids are all there is, and they are not in browse mode's buffer.

**Every rectangle is a call into the browser**, about 6 milliseconds each on the probe's pages,
and a busy page wants several hundred. Done in one go that is seconds of NVDA saying nothing, so
the work is cut into batches with a return to NVDA's message loop between them, and each part
has a limit. The probe findings in `docs/design/layout-overview-plan.md` give the costs.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import wx
from logHandler import log

from .layoutRegions import LANDMARKS, mentionsSearch

#: Quick navigation types whose rectangles the rules use. Level 3 headings are left out: on a
#: news page there are sixty of them, they cost most of the time, and they name stories rather
#: than areas.
CANDIDATE_TYPES = ("landmark", "heading1", "heading2", "edit", "button", "frame", "table")
#: The kinds of content sampled for splitting areas by their gaps, each with its share of
#: `MAX_LEAVES`. Shared out rather than taken in turn, since a news page has hundreds of links
#: and taking links first spent the whole budget before a picture or a field was seen (found in
#: review). A share one kind leaves unused goes to the others.
LEAF_SHARES = (("link", 0.5), ("heading", 0.2), ("graphic", 0.2), ("formField", 0.1))
#: The most leaves fetched. Enough to find a news page's columns on the probe's pages.
MAX_LEAVES = 300
#: How deep, and how far, the tree is walked on a page without landmarks. Twelve reaches the
#: columns of a page laid out with tables nested four deep.
TREE_DEPTH = 12
TREE_NODES = 600
#: Calls into the browser between returns to NVDA's message loop.
BATCH = 25
#: Past this, gathering stops where it is and the rules work with what there is.
TIME_LIMIT = 8.0
#: Roles whose children are text, not layout, so the tree walk does not go into them.
LEAF_ROLE_NAMES = frozenset(("STATICTEXT", "LINK", "HEADING", "GRAPHIC"))


class Collector:
	"""Gathers one page's layout in batches, then hands it to `done`.

	`done` is called once, with the gathered data in the shape `layoutRegions.fromProbe` reads,
	or with None and a reason. `cancel` stops it, and `done` is then never called.
	"""

	def __init__(self, document, done: Callable[[dict[str, Any] | None, str], None]):
		self.document = document
		self.done = done
		self.cancelled = False
		self.started = 0.0
		self.incomplete: list[str] = []
		self._steps: Iterator[None] | None = None

	def start(self) -> None:
		self.started = time.perf_counter()
		self._steps = self._gather()
		wx.CallAfter(self._step)

	def cancel(self) -> None:
		self.cancelled = True

	@property
	def overTime(self) -> bool:
		return time.perf_counter() - self.started > TIME_LIMIT

	def _step(self) -> None:
		if self.cancelled or self._steps is None:
			return
		try:
			next(self._steps)
		except StopIteration:
			return
		except Exception:
			log.error("BrlMultiline: gathering the page layout failed", exc_info=True)
			self.done(None, "error")
			return
		wx.CallAfter(self._step)

	def _gather(self) -> Iterator[None]:
		doc = self.document
		location = doc.rootNVDAObject.location
		if not location:
			self.done(None, "no location")
			return
		viewport = tuple(location)
		calls = 0
		records: list[dict[str, Any]] = []
		links = [item.textInfo._startOffset for item in doc._iterNodesByType("link")]
		yield
		seen = set()
		for nodeType in CANDIDATE_TYPES:
			if self.overTime:
				break
			try:
				items = list(doc._iterNodesByType(nodeType))
			except NotImplementedError:
				continue
			for item in items:
				if item.vbufFieldIdentifier in seen:
					continue
				seen.add(item.vbufFieldIdentifier)
				label = item.label or ""
				if nodeType == "button" and not mentionsSearch(label):
					# A button is only wanted when it is search, and its label, read from the
					# buffer, costs nothing to check; locating every button does not.
					continue
				record = _record(item, nodeType, label, viewport, links)
				calls += 1
				if record is not None:
					records.append(record)
				if calls % BATCH == 0:
					yield
					if self.overTime:
						break
		if self.overTime:
			self.incomplete.append("candidates")
		records.sort(key=lambda r: (r["start"], -r["end"]))
		hasLandmarks = any(_isMeaningful(r) for r in records)
		leaves: list[dict[str, Any]] = []
		pools = {}
		for nodeType, _share in LEAF_SHARES:
			try:
				pools[nodeType] = list(doc._iterNodesByType(nodeType))
			except NotImplementedError:
				pools[nodeType] = []
		for nodeType, item in _sample(pools, MAX_LEAVES):
			if self.overTime:
				self.incomplete.append("leaves")
				break
			rect = _relative(_location(item), viewport)
			if rect and rect[2] > 0 and rect[3] > 0:
				leaves.append({"type": nodeType, "start": item.textInfo._startOffset, "rect": rect})
			calls += 1
			if calls % BATCH == 0:
				yield
		if sum(len(pool) for pool in pools.values()) > MAX_LEAVES:
			self.incomplete.append("sampled")
		tree: list[dict[str, Any]] = []
		if not hasLandmarks:
			for _ in _walk(doc.rootNVDAObject, viewport, tree, self):
				yield
			if len(tree) >= TREE_NODES or self.overTime:
				self.incomplete.append("tree")
		payload = {
			"candidates": {
				"url": doc.documentConstantIdentifier,
				"viewport": list(viewport),
				"records": records,
			},
			"tree": {"nodes": tree},
			"leaves": {"leaves": leaves},
			# What was cut short, so an overview of part of a page does not pass for the whole.
			# "sampled" alone is normal on a busy page; the others mean a limit was reached.
			"incomplete": self.incomplete,
		}
		log.debug(
			f"BrlMultiline: page layout gathered in {time.perf_counter() - self.started:.2f}s, "
			f"{len(records)} candidates, {len(leaves)} leaves, {len(tree)} tree objects",
		)
		self.done(payload, "")


def _sample(pools: dict[str, list], limit: int) -> list[tuple[str, object]]:
	"""Up to limit items across the pools, each kind its share, spread through the page.

	Within a kind the items taken are spread evenly through the page rather than its first so
	many, so the bottom of a long page is not left out.
	"""
	shares = dict(LEAF_SHARES)
	quota = {kind: int(limit * shares[kind]) for kind in pools}
	spare = sum(max(0, quota[kind] - len(pool)) for kind, pool in pools.items())
	hungry = [kind for kind, pool in pools.items() if len(pool) > quota[kind]]
	while spare > 0 and hungry:
		each = max(1, spare // len(hungry))
		for kind in list(hungry):
			extra = min(each, len(pools[kind]) - quota[kind], spare)
			quota[kind] += extra
			spare -= extra
			if quota[kind] >= len(pools[kind]):
				hungry.remove(kind)
	chosen = []
	for kind, pool in pools.items():
		count = min(quota[kind], len(pool))
		if count <= 0:
			continue
		step = len(pool) / count
		chosen += [(kind, pool[int(i * step)]) for i in range(count)]
	return chosen


def _location(item):
	try:
		obj = item.obj
		return obj.location if obj else None
	except Exception:
		return None


def _relative(location, viewport) -> list[int] | None:
	if not location:
		return None
	return [location[0] - viewport[0], location[1] - viewport[1], location[2], location[3]]


def _attributes(obj) -> dict[str, str]:
	try:
		return dict(obj.IA2Attributes)
	except Exception:
		return {}


def _record(item, nodeType: str, label: str, viewport, links: list[int]) -> dict[str, Any] | None:
	"""One candidate as the rules read it, or None for a field or button that is not search."""
	try:
		obj = item.obj
	except Exception:
		obj = None
	if obj is None:
		return None
	attrs = _attributes(obj)
	name = obj.name or ""
	if nodeType in ("edit", "button"):
		isSearch = attrs.get("text-input-type") == "search" or mentionsSearch(
			name,
			label,
			attrs.get("placeholder", ""),
			attrs.get("id", ""),
			attrs.get("class", ""),
		)
		if not isSearch:
			return None
		nodeType = "search field" if nodeType == "edit" else "search button"
	start, end = item.textInfo._startOffset, item.textInfo._endOffset
	return {
		"type": nodeType,
		"label": label,
		"name": name,
		"tag": attrs.get("tag"),
		"xmlRoles": attrs.get("xml-roles"),
		"id": attrs.get("id"),
		"class": attrs.get("class"),
		"rect": _relative(obj.location, viewport),
		"start": start,
		"end": end,
		"textLength": end - start,
		"links": sum(1 for offset in links if start <= offset < end),
	}


def _isMeaningful(record: dict[str, Any]) -> bool:
	word = (record.get("xmlRoles") or "").split(" ")[0]
	if record["type"] != "landmark" or word not in LANDMARKS:
		return False
	return word not in ("form", "region") or bool(record.get("name"))


def _walk(root, viewport, out: list[dict[str, Any]], collector: Collector) -> Iterator[None]:
	"""Walk the accessible tree into out, a batch at a time, not into text."""
	stack = [(root, 0)]
	calls = 0
	while stack and len(out) < TREE_NODES and not collector.overTime:
		obj, depth = stack.pop()
		attrs = _attributes(obj)
		try:
			role = obj.role.name
			childCount = obj.childCount
			location = obj.location
		except Exception:
			continue
		out.append(
			{
				"depth": depth,
				"role": role,
				"name": (obj.name or "")[:80],
				"tag": attrs.get("tag"),
				"id": attrs.get("id"),
				"class": attrs.get("class"),
				"xmlRoles": attrs.get("xml-roles"),
				"rect": _relative(location, viewport),
			},
		)
		if childCount and depth < TREE_DEPTH and role not in LEAF_ROLE_NAMES:
			try:
				children = list(obj.children)
			except Exception:
				children = []
			# Pushed in reverse so they come off the stack in order: the rules read the list
			# as a tree walked top to bottom, depth first, as the probe saves it.
			stack.extend((child, depth + 1) for child in reversed(children))
		calls += 1
		if calls % BATCH == 0:
			yield
