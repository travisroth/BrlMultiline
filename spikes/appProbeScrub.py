# BrlMultiline: make an application layout capture safe to keep as a test fixture.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

r"""Replace the personal names in a capture from `appLayoutProbe.save` before it is kept.

A capture holds whatever the window showed: window titles naming a document or an account,
message subjects, file and folder names, device and computer names. The region rules read only
the names of panes, groups and landmarks, and names that mention search, so every name is
replaced by "Name 1", "Name 2" and so on, except the names of an application's own furniture
in `KEPT`. A name that mentions search keeps the word, "Search Name 3", since Explorer's search
box is called after the folder. Programmers' identifiers are not kept as a kind: a folder can
be called "BrlMultiline" as easily as a window "NUIDocumentWindow", so the identifiers wanted
are listed in `KEPT` like any other name. Automation ids that look like a file name or an
address are replaced, and so is an id found inside a replaced name, as an Excel sheet's is; and
the focus's name.

Run it outside NVDA:

	uv run python spikes\appProbeScrub.py spikes\layoutProbeOut\explorer-20261013-111606.json tests\fixtures\appLayout\explorer.json

Then read the result before committing it. The list of kept names is short on purpose, and a
name that is a person's and happens to be a word on it would get through.
"""

from __future__ import annotations

import json
import re
import sys

#: Names an application gives its own parts, the same for every person, kept as they are.
KEPT = frozenset(
	(
		# File Explorer
		"Control Host",
		"Items View",
		"Navigation Pane",
		"Property Field",
		"Shell Folder View",
		"Status bar",
		"View Modes",
		# Office
		"Add-ins",
		"Alignment",
		"Apps",
		"Bottom Pane",
		"Business Bar",
		"Cells",
		"Clipboard",
		"Delete",
		"Disclaimer",
		"Editing",
		"Favorites",
		"Find",
		"Font",
		"Form Regions",
		"Home",
		"Horizontal",
		"Language",
		"Lower Ribbon",
		"Mail Folders",
		"Message",
		"Message Actions",
		"Message Bars",
		"Move",
		"New",
		"Number",
		"Quick Access Toolbar",
		"Quick Steps",
		"Respond",
		"Ribbon",
		"Ribbon Tabs",
		"Send/Receive",
		"Speech",
		"Status Bar",
		"Styles",
		"Table View",
		"Tags",
		"Top Pane",
		"Vertical",
		# askSam
		"Application",
		"Attachments",
		"Folders",
		"Formatting",
		"Search Line",
		"Standard",
		"View Document Type",
		"View First",
		"View Last",
		"View Next",
		"View Previous",
		"Workspace",
		# Settings
		"Bluetooth devices",
		"Browse more backgrounds, colors, and themes",
		"Cloud storage",
		"Get help",
		"Give feedback",
		"Installed apps",
		"Mouse pointer and touch",
		"Personalize your device",
		"Printers & scanners",
		"Recommended settings",
		"Select a theme to apply",
		"Settings",
		"View all devices",
		# Programmers' names for parts, which the rules must see to leave out
		"ASEditorWnd",
		"ASScrollWnd",
		"AutoSave",
		"AutoSum",
		"Document12",
		"DropShadowTop",
		"FolderBar",
		"MsoDockBottom",
		"MsoDockLeft",
		"MsoDockTop",
		"NUIDocumentWindow",
		# Notepad and every window
		"Search",
		"Search People",
		"Text editor",
		"System",
		"Minimize",
		"Maximize",
		"Restore",
		"Close",
	),
)

_FILE_OR_ADDRESS = re.compile(r"@|\.[A-Za-z0-9]{1,4}$")


class Scrubber:
	def __init__(self):
		self.names: dict[str, str] = {}
		self.ids: dict[str, str] = {}

	def name(self, value):
		if not value:
			return value
		clean = " ".join(str(value).split())
		if clean in KEPT:
			return value
		if clean not in self.names:
			stand = f"Name {len(self.names) + 1}"
			self.names[clean] = f"Search {stand}" if "search" in clean.lower() else stand
		return self.names[clean]

	def automationId(self, value):
		if not value or not _FILE_OR_ADDRESS.search(str(value)):
			return value
		return self.ids.setdefault(str(value), f"id{len(self.ids) + 1}")

	def record(self, record: dict) -> dict:
		record = dict(record)
		original = " ".join(str(record.get("name") or "").split())
		if "name" in record:
			record["name"] = self.name(record["name"])
		if "automationId" in record:
			ident = str(record["automationId"])
			if original and record["name"] != original and len(ident) > 2 and ident in original:
				# An id that is part of the name replaced, as an Excel sheet's id is its name.
				record["automationId"] = self.ids.setdefault(ident, f"id{len(self.ids) + 1}")
			else:
				record["automationId"] = self.automationId(record["automationId"])
		if "children" in record:
			record["children"] = [self.record(c) for c in record["children"]]
		return record


def scrub(data: dict) -> dict:
	scrubber = Scrubber()
	data = dict(data)
	data["window"] = scrubber.record(data["window"])
	if data.get("focus"):
		focus = dict(data["focus"])
		focus["name"] = "Focused item"
		data["focus"] = focus
	return data


if __name__ == "__main__":
	source, target = sys.argv[1], sys.argv[2]
	with open(source, encoding="utf-8") as f:
		data = json.load(f)
	with open(target, "w", encoding="utf-8") as f:
		json.dump(scrub(data), f, indent=1)
	print(f"Wrote {target}")
