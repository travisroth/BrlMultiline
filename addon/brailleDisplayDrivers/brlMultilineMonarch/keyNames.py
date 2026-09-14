# BrlMultiline: names for the Monarch's keys that NVDA cannot tell apart.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Names for keys that the HID braille driver names by usage alone.

`hidBrailleStandard.InputGesture` builds a key's name from its usage and nothing else. The
Monarch has two d-pads, and its descriptor declares the same d-pad usages for both: once under
left controls and once under right controls, each with its own data index. Established on
hardware on 14 September 2026, pressing up on the left pad and then on the right:

	usage 0x216 = 1  [data index 18, collection 3 (link usage 0x20D)]
	usage 0x216 = 1  [data index 14, collection 2 (link usage 0x20E)]

NVDA named both presses `dpadUp`. The firmware says which pad is which, and the name discards
it. JAWS separates the two pads, which is what raised the question.

So a key whose usage is declared in more than one collection is given the side its collection
names: `leftDpadUp` and `rightDpadUp`. It is worked out from the descriptor rather than written
for the d-pad, so a joystick or rocker declared twice is treated the same, and a key declared
once is never renamed.

The two zoom keys are named too. They are usages 0x220 and 0x221, one past the last usage the
braille page defines, so NVDA calls them `brailleUsage544` and `brailleUsage545`. Input help on
the same day showed 544 is the key the Monarch's own software zooms in with.

**Every name here is offered in addition to NVDA's, never instead of it.** See
`InputGesture._get_identifiers` in the driver for the order, and why nothing bound today stops
matching.

Imports nothing from NVDA, so it is unit tested without one, like `monarch.py`.
"""

from typing import Iterable, NamedTuple, Optional

BRAILLE_PAGE = 0x41

SIDE_BY_LINK_USAGE = {
	0x20C: "face",
	0x20D: "left",
	0x20E: "right",
	0x20F: "top",
}
"""The braille page's control collections, and the word each gives a key inside it.

`BRAILLE_FACE_CONTROLS` to `BRAILLE_TOP_CONTROLS`. A key declared twice under some other
collection is left with NVDA's name: guessing a word for a collection the standard does not
describe would be a name the reader could not predict.
"""

NAMED_USAGES = {
	0x220: "zoomIn",
	0x221: "zoomOut",
}
"""Usages past the end of the braille page that the Monarch uses, and what they are.

Both are vendor usages, which is safe here only because this driver refuses any device that is
not a Monarch.
"""

NEVER_RENAMED = range(0x201, 0x20C)
"""Dots 1 to 8 and the three spaces.

Braille input identifiers and every chord in NVDA's gesture map are built from these names, so
they keep NVDA's whatever the descriptor says. A Monarch with space declared twice would still
type.
"""

ROUTER_SETS = range(0xFA, 0xFD)
"""`ROUTER_SET_1` to `ROUTER_SET_3`. Routing keys are never renamed."""

ROUTER_SET_1 = 0xFA
"""The one router set NVDA takes out of a gesture's key names and turns into a routing name.

Keys in the other two sets get an ordinary name each, so they occupy a place in `keyNames`
and have to be counted when lining names up with data indices. See L{specificId}.
"""


class KeyName(NamedTuple):
	"""A key that gets a name of its own."""

	nvda: str
	"""What NVDA calls it. Checked before it is replaced, so a change to how NVDA names keys
	costs the new name rather than attaching it to the wrong key."""

	ours: str
	"""What it is called here."""


class ButtonDeclaration(NamedTuple):
	"""One input button, as the descriptor declares it. Ranges are expanded to one per index."""

	dataIndex: int
	usagePage: int
	usage: int
	linkCollection: int
	linkUsage: int


def declarationsFromCaps(buttonCaps: Iterable) -> list[ButtonDeclaration]:
	"""Expand HID button capabilities into one declaration per data index.

	:param buttonCaps: `HIDP_BUTTON_CAPS`, or anything with the same fields.
	:return: the declarations, in the order the capabilities listed them.
	"""
	declarations: list[ButtonDeclaration] = []
	for cap in buttonCaps:
		if cap.IsRange:
			r = cap.u1.Range
			for index in range(r.DataIndexMin, r.DataIndexMax + 1):
				declarations.append(
					ButtonDeclaration(
						index,
						cap.UsagePage,
						r.UsageMin + (index - r.DataIndexMin),
						cap.LinkCollection,
						cap.LinkUsage,
					),
				)
		else:
			nr = cap.u1.NotRange
			declarations.append(
				ButtonDeclaration(nr.DataIndex, cap.UsagePage, nr.Usage, cap.LinkCollection, cap.LinkUsage),
			)
	return declarations


def namesByDataIndex(declarations: Iterable[ButtonDeclaration]) -> dict[int, KeyName]:
	"""Work out which keys get a name of their own, and what it is.

	:param declarations: every input button the device declares.
	:return: data index to the names, NVDA's and ours. Keys NVDA already names well are absent,
		so an empty map means every gesture keeps exactly the identifiers it had.
	"""
	declarations = [d for d in declarations if d.usagePage == BRAILLE_PAGE]
	collectionsByUsage: dict[int, set[int]] = {}
	for declaration in declarations:
		if declaration.linkUsage in ROUTER_SETS:
			continue
		collectionsByUsage.setdefault(declaration.usage, set()).add(declaration.linkCollection)
	names: dict[int, KeyName] = {}
	for declaration in declarations:
		if declaration.linkUsage in ROUTER_SETS or declaration.usage in NEVER_RENAMED:
			continue
		usage = declaration.usage
		if usage in NAMED_USAGES:
			names[declaration.dataIndex] = KeyName(f"brailleUsage{usage}", NAMED_USAGES[usage])
			continue
		if len(collectionsByUsage.get(usage, ())) < 2:
			continue
		side = SIDE_BY_LINK_USAGE.get(declaration.linkUsage)
		plain = standardName(usage)
		if side is None or plain is None:
			continue
		names[declaration.dataIndex] = KeyName(plain, side + plain[0].upper() + plain[1:])
	return names


def routingDataIndices(declarations: Iterable[ButtonDeclaration]) -> frozenset[int]:
	""":return: the data indices NVDA folds into a routing name rather than naming one by one."""
	return frozenset(
		d.dataIndex for d in declarations if d.usagePage == BRAILLE_PAGE and d.linkUsage == ROUTER_SET_1
	)


_STANDARD_NAMES = {
	0x20C: "faceControls",
	0x20D: "leftControls",
	0x20E: "rightControls",
	0x20F: "topControls",
	0x210: "joystickCenter",
	0x211: "joystickUp",
	0x212: "joystickDown",
	0x213: "joystickLeft",
	0x214: "joystickRight",
	0x215: "dpadCenter",
	0x216: "dpadUp",
	0x217: "dpadDown",
	0x218: "dpadLeft",
	0x219: "dpadRight",
	0x21A: "panLeft",
	0x21B: "panRight",
	0x21C: "rockerUp",
	0x21D: "rockerDown",
	0x21E: "rockerPress",
}
"""NVDA's names for the braille page's control usages, as `_usageIDToGestureName` builds them.

Written out rather than derived, because deriving them means importing NVDA. A name here that
drifted from NVDA's would only cost a side name, never a binding, because L{specificId} renames
nothing unless NVDA's name for the key is the one recorded.
"""


def standardName(usage: int) -> Optional[str]:
	""":return: NVDA's name for a braille page control usage, or None for one not listed."""
	return _STANDARD_NAMES.get(usage)


def specificId(
	nvdaNames: list[str],
	dataIndices: Iterable[int],
	routing: frozenset[int],
	names: dict[int, KeyName],
) -> Optional[str]:
	"""Build a gesture's id with the keys that have names of their own renamed.

	Mirrors how `hidBrailleStandard.InputGesture` builds `id`: one name per key, in the order the
	data indices arrive, leaving out router set 1, then the routing name after them all. Rather
	than trusting that the order still holds, every name replaced is first checked to be the one
	NVDA gave that key.

	:param nvdaNames: the names NVDA gave the gesture's keys, its `keyNames`, in order.
	:param dataIndices: the gesture's data indices, in the order NVDA walked them.
	:param routing: from L{routingDataIndices}.
	:param names: from L{namesByDataIndex}.
	:return: the id with names of our own, or None when no key has one, or when NVDA's names do
		not line up with the data indices as expected — in which case nothing is renamed at all.
	"""
	specific = list(nvdaNames)
	position = 0
	renamed = False
	for index in dataIndices:
		if index in routing:
			continue
		if position >= len(specific):
			return None
		name = names.get(index)
		if name is not None:
			if specific[position] != name.nvda:
				return None
			specific[position] = name.ours
			renamed = True
		position += 1
	if not renamed:
		return None
	return "+".join(specific)
