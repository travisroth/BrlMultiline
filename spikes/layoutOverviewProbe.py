# BrlMultiline: spike for a screen layout overview of web pages.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

r"""Console spike: what can browse mode tell us about where things are on a web page?

The layout overview idea (`docs/design/layout-overview-plan.md`) draws a page's main areas as
labelled boxes in their real positions. Before designing it, four questions need answers that
reading source cannot give:

1. Do Chrome, Edge and Firefox report useful rectangles for landmarks, including ones scrolled
   out of view, and what happens to sticky or fixed headers?
2. Which IAccessible2 object attributes come through: tag, id, class, display, xml-roles?
3. Do layout-only divs with telling class names (sidebar, nav, content) survive into the
   accessibility tree at all, or does the browser prune them?
4. How long do the location calls take, and so how many can an overview afford?

Open a web page in browse mode, put focus in it, open the NVDA Python console
(NVDA+control+z), and run:

	import sys; sys.path.insert(0, r"C:\code\BrlMultiline\spikes")
	import layoutOverviewProbe as probe
	probe.dump(focus)      # landmarks, headings, search fields, frames, tables, with rectangles
	probe.tree(focus)      # the raw accessible tree a few levels down: tags, ids, classes
	probe.leaves(focus)    # rectangles of links, headings, graphics and fields, and their cost
	probe.save(focus)      # all three into a text report and a JSON file in spikes\layoutProbeOut
	probe.overview(focus)  # the add-on's region rules run on this page: its main areas and where

`focus` is the console's snapshot of what had focus when NVDA+control+z was pressed, so it is
the page, not the console. Every function takes it as its first argument. Called with no
argument, a function reads that same snapshot from the console itself, so `probe.save()` does
the same thing. If it says there is no browse mode document, close the console, click into the
page, and open it again.

`save` writes to the layoutProbeOut folder next to this file,
C:\code\BrlMultiline\spikes\layoutProbeOut, in files named after the site and the time, such as www.example.com-20261012-143005.txt and .json. It prints the full paths when it is
done. Pass `folder=r"C:\somewhere"` to save elsewhere.

After editing this file, reload it, since `import` is a no op once a module is loaded:

	import importlib; probe = importlib.reload(probe)

Coordinates are reported relative to the document's own top left corner, which is the top left
of the visible part of the page. A negative top, or a top past the viewport height, means the
element is scrolled out of view. Sizes are in screen pixels.

Nothing here changes the page, moves the caret or the focus. It only reads.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter
from typing import Any
from urllib.parse import unquote, urlparse

import api
from logHandler import log

#: Quick navigation types worth a rectangle for the overview. Heading levels beyond 3 are
#: rarely structural, so they are left out to keep the location calls down. Edits and buttons
#: are kept only when they look like search, since many sites hide the field behind a button.
CANDIDATE_TYPES = (
	"landmark",
	"heading1",
	"heading2",
	"heading3",
	"edit",
	"button",
	"frame",
	"table",
	"article",
)

#: Quick navigation types sampled by `leaves`, the raw material for splitting a page by geometry.
LEAF_TYPES = ("link", "heading", "graphic", "formField")

#: Class and id words that suggest what a block is. Only reported here, to see how often they
#: turn up; whether to trust them is a design question.
HINT_WORDS = re.compile(
	r"nav|menu|sidebar|side-bar|aside|header|masthead|banner|footer|search|content|main|article"
	r"|column|col-|left|right|toolbar|breadcrumb",
	re.IGNORECASE,
)


def _focusSnapshot():
	"""What had focus when the console opened, falling back to the live focus."""
	try:
		import pythonConsole

		snapshot = pythonConsole.consoleUI.console._namespaceSnapshotVars
		if snapshot and snapshot.get("focus") is not None:
			return snapshot["focus"]
	except Exception:
		pass
	return api.getFocusObject()


def _document(obj=None):
	"""The browse mode virtual buffer for obj, by default the console's focus snapshot.

	Returns None, with a message printed, when obj is not in a browse mode document.
	"""
	obj = obj or _focusSnapshot()
	ti = getattr(obj, "treeInterceptor", None)
	if ti is None or not hasattr(ti, "_iterNodesByType") or not hasattr(ti, "VBufHandle"):
		print("No browse mode document under focus. Click into the page and reopen the console.")
		return None
	if not ti.isReady:
		print("The document is still loading. Try again in a moment.")
		return None
	return ti


def _browser(doc) -> str:
	"""The browser's name and version, so reports from different browsers can be told apart."""
	try:
		app = doc.rootNVDAObject.appModule
		return f"{app.productName} {app.productVersion}"
	except Exception:
		return "unknown"


def _viewport(doc) -> tuple | None:
	"""The document's visible rectangle on screen as (left, top, width, height)."""
	loc = doc.rootNVDAObject.location
	return tuple(loc) if loc else None


def _relative(loc, viewport) -> list | None:
	if not loc:
		return None
	return [loc[0] - viewport[0], loc[1] - viewport[1], loc[2], loc[3]]


def _whereWords(rect, viewport) -> str:
	"""A rough, readable position, so the console output can be read without arithmetic."""
	if rect is None:
		return "no location"
	left, top, width, height = rect
	vw, vh = viewport[2], viewport[3]
	if width <= 0 or height <= 0:
		return "zero size"
	if top + height <= 0:
		return "above the visible part"
	if top >= vh:
		return f"below the visible part, {top // max(vh, 1)} screens down"
	cx = left + width / 2
	cy = top + height / 2
	horizontal = "left" if cx < vw / 3 else "right" if cx > vw * 2 / 3 else "center"
	vertical = "top" if cy < vh / 3 else "bottom" if cy > vh * 2 / 3 else "middle"
	shape = ""
	if width >= vw * 0.8:
		shape = ", full width"
	elif height >= vh * 0.8 and width <= vw * 0.35:
		shape = ", tall and narrow"
	return f"{vertical} {horizontal}{shape}, {width} by {height}"


def _attrs(obj) -> dict:
	try:
		return dict(obj.IA2Attributes)
	except Exception:
		log.debugWarning("IA2Attributes failed", exc_info=True)
		return {}


def _hints(attrs: dict) -> list:
	words = []
	for key in ("id", "class"):
		value = attrs.get(key, "")
		words.extend(sorted({m.group(0).lower() for m in HINT_WORDS.finditer(value)}))
	return words


def _linkOffsets(doc) -> list:
	"""Start offsets of every link, read from the buffer alone, with no calls to the browser."""
	return [item.textInfo._startOffset for item in doc._iterNodesByType("link")]


def _isSearchField(attrs: dict, name: str) -> bool:
	"""Search by the add-on's own word-aware test, so the probe and the add-on agree on it."""
	if attrs.get("text-input-type") == "search":
		return True
	return _layoutRegions().mentionsSearch(
		name or "",
		attrs.get("placeholder", ""),
		attrs.get("id", ""),
		attrs.get("class", ""),
	)


def collect(doc=None) -> dict | None:
	"""Gather every candidate with its rectangle and attributes. Returns the data `dump` prints."""
	if doc is None:
		return None
	viewport = _viewport(doc)
	if viewport is None:
		print("The document reports no location.")
		return None
	started = time.perf_counter()
	links = _linkOffsets(doc)
	linkScanMs = (time.perf_counter() - started) * 1000
	records = []
	seen = set()
	locateMs = []
	for nodeType in CANDIDATE_TYPES:
		try:
			items = list(doc._iterNodesByType(nodeType))
		except NotImplementedError:
			continue
		for item in items:
			ident = item.vbufFieldIdentifier
			if ident in seen:
				continue
			seen.add(ident)
			start, end = item.textInfo._startOffset, item.textInfo._endOffset
			t0 = time.perf_counter()
			obj = item.obj
			loc = obj.location if obj else None
			attrs = _attrs(obj) if obj else {}
			locateMs.append((time.perf_counter() - t0) * 1000)
			name = obj.name if obj else None
			if nodeType in ("edit", "button") and not _isSearchField(attrs, name):
				continue
			rect = _relative(loc, viewport)
			records.append(
				{
					"type": {"edit": "search field", "button": "search button"}.get(nodeType, nodeType),
					"label": item.label,
					"role": obj.role.name if obj else None,
					"name": name,
					"tag": attrs.get("tag"),
					"xmlRoles": attrs.get("xml-roles"),
					"id": attrs.get("id"),
					"class": attrs.get("class"),
					"display": attrs.get("display"),
					"hints": _hints(attrs),
					"attributeKeys": sorted(attrs),
					"rect": rect,
					"where": _whereWords(rect, viewport),
					"start": start,
					"end": end,
					"textLength": end - start,
					"links": sum(1 for o in links if start <= o < end),
				},
			)
	records.sort(key=lambda r: (r["start"], -r["end"]))
	_nest(records)
	return {
		"browser": _browser(doc),
		"url": doc.documentConstantIdentifier,
		"viewport": list(viewport),
		"totalLinks": len(links),
		"linkScanMs": round(linkScanMs, 1),
		"locateCount": len(locateMs),
		"locateMsTotal": round(sum(locateMs), 1),
		"locateMsMax": round(max(locateMs), 1) if locateMs else 0,
		"records": records,
	}


def _nest(records: list) -> None:
	"""Give each record a depth from offset containment among landmarks, regions and articles."""
	stack = []
	for r in records:
		while stack and not (stack[-1]["start"] <= r["start"] and r["end"] <= stack[-1]["end"]):
			stack.pop()
		r["depth"] = len(stack)
		if r["type"] in ("landmark", "article", "frame"):
			stack.append(r)


def _report(data: dict) -> str:
	lines = [
		f"Browser: {data.get('browser')}",
		f"URL: {data['url']}",
		"Viewport: {2} by {3} at {0}, {1}".format(*data["viewport"]),
		f"Links in buffer: {data['totalLinks']}, scanned in {data['linkScanMs']} ms",
		f"Objects located: {data['locateCount']}, {data['locateMsTotal']} ms in all, "
		f"slowest {data['locateMsMax']} ms",
		"",
	]
	if not any(r["type"] == "landmark" for r in data["records"]):
		lines.append("No landmarks on this page.")
	for r in data["records"]:
		indent = "  " * r["depth"]
		title = r["label"] or r["name"] or ""
		lines.append(f"{indent}{r['type']}: {title}")
		lines.append(f"{indent}  where: {r['where']}")
		lines.append(
			f"{indent}  text {r['textLength']} characters, {r['links']} links"
			+ (
				f", link density {r['links'] * 100 / max(r['textLength'], 1):.1f} per 100 characters"
				if r["textLength"]
				else ""
			),
		)
		ident = ", ".join(f"{k} {r[k]!r}" for k in ("tag", "xmlRoles", "id", "class", "display") if r[k])
		lines.append(f"{indent}  {ident or 'no tag, id or class'}")
		if r["hints"]:
			lines.append(f"{indent}  hint words: {', '.join(r['hints'])}")
	keys = Counter(k for r in data["records"] for k in r["attributeKeys"])
	lines.append("")
	lines.append("IAccessible2 attribute keys seen, with how many objects had each:")
	lines.append("  " + ", ".join(f"{k} {n}" for k, n in sorted(keys.items())))
	return "\n".join(lines)


def dump(obj=None) -> None:
	"""Print landmarks, top headings, search fields, frames, tables and articles with positions."""
	data = collect(_document(obj))
	if data:
		print(_report(data))
	return None


def walkTree(doc, maxDepth: int = 5, maxNodes: int = 300) -> dict | None:
	"""Walk the browser's accessible tree, not the buffer, to see which containers survive.

	The buffer hides generic containers; the tree shows whether a div class="sidebar" is still
	there as an object of its own, which decides whether class names are any use at all.
	"""
	if doc is None:
		return None
	viewport = _viewport(doc)
	nodes = []
	started = time.perf_counter()

	def visit(obj, depth):
		if len(nodes) >= maxNodes or depth > maxDepth:
			return
		attrs = _attrs(obj)
		try:
			childCount = obj.childCount
		except Exception:
			childCount = 0
		nodes.append(
			{
				"depth": depth,
				"role": obj.role.name,
				"name": (obj.name or "")[:60],
				"tag": attrs.get("tag"),
				"id": attrs.get("id"),
				"class": attrs.get("class"),
				"xmlRoles": attrs.get("xml-roles"),
				"display": attrs.get("display"),
				"hints": _hints(attrs),
				"children": childCount,
				"rect": _relative(obj.location, viewport),
			},
		)
		# Leaves of text are noise here; containers are the point.
		if childCount and obj.role.name not in ("STATICTEXT", "LINK", "HEADING", "GRAPHIC"):
			for child in obj.children:
				visit(child, depth + 1)

	visit(doc.rootNVDAObject, 0)
	return {
		"nodes": nodes,
		"ms": round((time.perf_counter() - started) * 1000, 1),
		"truncated": len(nodes) >= maxNodes,
	}


def _treeReport(data: dict, viewport) -> str:
	lines = [
		f"{len(data['nodes'])} objects in {data['ms']} ms"
		+ (", stopped at the node limit" if data["truncated"] else ""),
	]
	withClass = sum(1 for n in data["nodes"] if n["class"])
	withId = sum(1 for n in data["nodes"] if n["id"])
	withHints = sum(1 for n in data["nodes"] if n["hints"])
	lines.append(f"With a class: {withClass}. With an id: {withId}. With hint words: {withHints}.")
	for n in data["nodes"]:
		bits = [n["role"].lower()]
		for k in ("tag", "xmlRoles", "id", "class"):
			if n[k]:
				bits.append(f"{k} {n[k]!r}")
		if n["name"]:
			bits.append(f"name {n['name']!r}")
		bits.append(f"{n['children']} children")
		bits.append(_whereWords(n["rect"], viewport))
		lines.append("  " * n["depth"] + ", ".join(bits))
	return "\n".join(lines)


def tree(obj=None, maxDepth: int = 5, maxNodes: int = 300) -> None:
	"""Print the accessible tree a few levels down, with tags, ids, classes and positions."""
	doc = _document(obj)
	if doc is None:
		return
	data = walkTree(doc, maxDepth, maxNodes)
	if data:
		print(_treeReport(data, _viewport(doc)))


def sampleLeaves(doc, limit: int = 400) -> dict | None:
	"""Rectangles of small content elements, the input a geometric page split would use."""
	if doc is None:
		return None
	viewport = _viewport(doc)
	rects = []
	times = []
	for nodeType in LEAF_TYPES:
		try:
			items = doc._iterNodesByType(nodeType)
		except NotImplementedError:
			continue
		for item in items:
			if len(rects) >= limit:
				break
			t0 = time.perf_counter()
			obj = item.obj
			loc = obj.location if obj else None
			times.append((time.perf_counter() - t0) * 1000)
			rect = _relative(loc, viewport)
			if rect and rect[2] > 0 and rect[3] > 0:
				rects.append({"type": nodeType, "start": item.textInfo._startOffset, "rect": rect})
	times.sort()
	return {
		"count": len(rects),
		"limit": limit,
		"msTotal": round(sum(times), 1),
		"msMedian": round(times[len(times) // 2], 2) if times else 0,
		"msMax": round(times[-1], 1) if times else 0,
		"leaves": rects,
	}


def _leavesReport(data: dict, viewport) -> str:
	vh = max(viewport[3], 1)
	visible = sum(1 for r in data["leaves"] if 0 <= r["rect"][1] < vh)
	lowest = max((r["rect"][1] + r["rect"][3] for r in data["leaves"]), default=0)
	return "\n".join(
		(
			f"{data['count']} rectangles (limit {data['limit']}), {visible} in the visible part",
			f"Time: {data['msTotal']} ms in all, median {data['msMedian']} ms, slowest {data['msMax']} ms",
			f"Lowest edge found: {lowest} pixels, about {lowest / vh:.1f} screens of page",
		),
	)


def leaves(obj=None, limit: int = 400) -> None:
	"""Print how many leaf rectangles were found and what they cost to fetch."""
	doc = _document(obj)
	if doc is None:
		return
	data = sampleLeaves(doc, limit)
	if data:
		print(_leavesReport(data, _viewport(doc)))


def save(
	obj=None,
	folder: str | None = None,
	maxDepth: int = 5,
	maxNodes: int = 300,
	limit: int = 400,
) -> None:
	"""Run all three probes and write a readable report and a JSON file, named after the site.

	The JSON is meant for working out the layout rules offline, in unit tests, without a browser.
	"""
	doc = _document(obj)
	if doc is None:
		return
	viewport = _viewport(doc)
	data = collect(doc)
	if data is None:
		return
	treeData = walkTree(doc, maxDepth, maxNodes)
	leafData = sampleLeaves(doc, limit)
	folder = folder or os.path.join(os.path.dirname(os.path.abspath(__file__)), "layoutProbeOut")
	os.makedirs(folder, exist_ok=True)
	url = urlparse(data["url"] or "")
	# A local file has no host, so name it after the file instead.
	host = url.netloc or os.path.splitext(os.path.basename(unquote(url.path)))[0] or "page"
	stem = os.path.join(folder, f"{re.sub(r'[^A-Za-z0-9.-]', '_', host)}-{time.strftime('%Y%m%d-%H%M%S')}")
	payload: dict[str, Any] = {"candidates": data, "tree": treeData, "leaves": leafData}
	report = "\n\n".join(
		(
			"Overview\n" + "\n".join(_overviewLines(payload)),
			_report(data),
			"Accessible tree\n" + _treeReport(treeData, viewport),
			"Leaves\n" + _leavesReport(leafData, viewport),
		),
	)
	with open(stem + ".txt", "w", encoding="utf-8") as f:
		f.write(report)
	with open(stem + ".json", "w", encoding="utf-8") as f:
		json.dump(payload, f, indent=1)
	print(f"Saved {stem}.txt and {stem}.json")


_rules: tuple = (None, None)


def _layoutRegions():
	"""The add-on's region rules, loaded from source again whenever the file changes, so an edit
	needs no NVDA restart."""
	global _rules
	import importlib.util

	path = os.path.join(
		os.path.dirname(os.path.abspath(__file__)),
		"..",
		"addon",
		"globalPlugins",
		"brlMultiline",
		"layoutRegions.py",
	)
	changed = os.path.getmtime(path)
	if _rules[0] == changed:
		return _rules[1]
	spec = importlib.util.spec_from_file_location("layoutRegionsProbeCopy", path)
	module = importlib.util.module_from_spec(spec)
	# Registered before it runs: dataclasses look their own module up while being defined.
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	_rules = (changed, module)
	return module


def _overviewLines(payload: dict, depth: int = 3) -> list:
	try:
		rules = _layoutRegions()
		root, viewport = rules.fromProbe(payload)
		return rules.describe(rules.findRegions(root, viewport), depth)
	except Exception:
		log.error("Layout overview failed", exc_info=True)
		return ["The overview failed; the NVDA log has the error."]


def overview(obj=None, depth: int = 3, maxDepth: int = 12, maxNodes: int = 600, limit: int = 400) -> None:
	"""Run the add-on's region rules on this page and print the overview they find.

	The same rules the unit tests check, fed the same data `save` writes, so what this prints is
	what a saved copy of the page would give. `depth` is how many levels of areas to show. The
	tree is walked deeper than `save` walks it by default, since old pages nest layout tables.
	"""
	doc = _document(obj)
	if doc is None:
		return
	data = collect(doc)
	if data is None:
		return
	payload = {
		"candidates": data,
		"tree": walkTree(doc, maxDepth, maxNodes),
		"leaves": sampleLeaves(doc, limit),
	}
	print("\n".join(_overviewLines(payload, depth)))
