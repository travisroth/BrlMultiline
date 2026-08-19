# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for recognising a form control, and for what a flow does differently for one.

Two decisions live here and nowhere else: which roles count as controls, and how many rows
of a control's prompt belong above it. Both are policy rather than arithmetic, so both are
stated in one place and tested against that statement.

The recognition itself is worth testing because of how it is done. A block is classified
from the field commands of its own text, which in a browse mode document costs no call to
the application — the alternative, fetching an object per block, is what makes a page with
expensive accessibility calls unreadable, and is the thing this design has avoided from the
start.
"""

import unittest

from ._stubs import installStubs

installStubs()

import textInfos  # noqa: E402

from brlMultiline import flowForms  # noqa: E402


class FakeRole:
	"""An enumeration member, which is what NVDA puts in a control field's role."""

	def __init__(self, name):
		self.name = name


class FieldedInfo:
	"""A position whose block carries the given fields."""

	def __init__(self, fields, text=""):
		self.fields = fields
		self.text = text

	def copy(self):
		return FieldedInfo(self.fields, self.text)

	def expand(self, unit):
		pass

	def getTextWithFields(self, formatConfig=None):
		return [*self.fields, self.text]


def controlStart(roleName):
	return textInfos.FieldCommand("controlStart", textInfos.ControlField(role=FakeRole(roleName)))


class TestWhatCountsAsAControl(unittest.TestCase):
	def test_theThingsAReaderStopsAtAndAnswers(self):
		for name in ("EDITABLETEXT", "COMBOBOX", "CHECKBOX", "RADIOBUTTON", "BUTTON", "SLIDER"):
			with self.subTest(role=name):
				self.assertTrue(flowForms.isControlRole(FakeRole(name)))

	def test_aLinkIsNotOne(self):
		# A page of links would otherwise declare a blank row after each of them and spend
		# half the display on spacing.
		for name in ("LINK", "LISTITEM", "PARAGRAPH", "HEADING", "STATICTEXT"):
			with self.subTest(role=name):
				self.assertFalse(flowForms.isControlRole(FakeRole(name)))

	def test_nothingIsNotOne(self):
		self.assertFalse(flowForms.isControlRole(None))

	def test_aRoleGivenAsTextStillReads(self):
		"""So that a field carrying a bare string is classified rather than raising."""
		self.assertTrue(flowForms.isControlRole("editableText"))

	def test_aRoleNameIsTakenFromTheMemberNotItsNumber(self):
		self.assertEqual(flowForms.roleName(FakeRole("comboBox")), "COMBOBOX")


class TestRecognisingABlock(unittest.TestCase):
	def test_aBlockHoldingAControl(self):
		info = FieldedInfo([controlStart("EDITABLETEXT")], "Ada")
		self.assertTrue(flowForms.isControlAt(info, textInfos.UNIT_LINE))

	def test_aBlockOfProse(self):
		info = FieldedInfo([controlStart("PARAGRAPH")], "Some prose.")
		self.assertFalse(flowForms.isControlAt(info, textInfos.UNIT_LINE))

	def test_aBlockWithNoFieldsAtAll(self):
		self.assertFalse(flowForms.isControlAt(FieldedInfo([], "Some prose."), textInfos.UNIT_LINE))

	def test_aControlNestedAmongOtherFields(self):
		info = FieldedInfo(
			[controlStart("SECTION"), controlStart("PARAGRAPH"), controlStart("CHECKBOX")],
			"Remember me",
		)
		self.assertTrue(flowForms.isControlAt(info, textInfos.UNIT_LINE))

	def test_anEndFieldIsNotAStart(self):
		fields = [textInfos.FieldCommand("controlEnd", textInfos.ControlField(role=FakeRole("BUTTON")))]
		self.assertFalse(flowForms.isControlAt(FieldedInfo(fields), textInfos.UNIT_LINE))

	def test_aDocumentThatWillNotSayIsProse(self):
		"""Which costs the reader a row of context and nothing else."""

		class Difficult(FieldedInfo):
			def getTextWithFields(self, formatConfig=None):
				raise RuntimeError("no fields here")

		self.assertFalse(flowForms.isControlAt(Difficult([]), textInfos.UNIT_LINE))

	def test_theProbeIsBoundToTheReadingUnit(self):
		seen = []

		class Watching(FieldedInfo):
			def copy(self):
				return self

			def expand(self, unit):
				seen.append(unit)

		probe = flowForms.controlProbe(textInfos.UNIT_PARAGRAPH)
		probe(Watching([controlStart("BUTTON")]))
		self.assertEqual(seen, [textInfos.UNIT_PARAGRAPH])


class TestHowMuchContext(unittest.TestCase):
	"""A prompt goes above its control, and a prompt that filled the display would not help."""

	def test_aOneRowLabelSitsAboveTheControl(self):
		self.assertEqual(flowForms.contextRowsFor(previousRows=1, gapRows=0, bandRows=8), 1)

	def test_declaredSpacingIsCountedToo(self):
		"""Or the window would start a row inside the label rather than at its top."""
		self.assertEqual(flowForms.contextRowsFor(previousRows=1, gapRows=1, bandRows=8), 2)

	def test_aLongLabelIsShownByItsLastRows(self):
		# Half the band, so the control itself is still on the display.
		self.assertEqual(flowForms.contextRowsFor(previousRows=20, gapRows=0, bandRows=8), 4)

	def test_nothingAboveMeansNoContext(self):
		self.assertEqual(flowForms.contextRowsFor(previousRows=0, gapRows=0, bandRows=8), 0)

	def test_aSingleRowBandHasNoRoomForContext(self):
		"""On one row a prompt above the control would be the control not shown."""
		self.assertEqual(flowForms.contextRowsFor(previousRows=1, gapRows=0, bandRows=1), 0)

	def test_aTwoRowBandStillManagesOne(self):
		self.assertEqual(flowForms.contextRowsFor(previousRows=1, gapRows=0, bandRows=2), 1)


class TestWhoGetsAProbe(unittest.TestCase):
	def test_aBrowseModeDocumentDoes(self):
		from ._stubs import FakeTreeInterceptor

		probe = flowForms.probeFor(FakeTreeInterceptor(["a line"]), textInfos.UNIT_LINE)
		self.assertIsNotNone(probe)

	def test_anObjectDoesNot(self):
		# Prose in an edit control has no form fields, so the probe would cost a field read
		# per block and answer no every time.
		from ._stubs import FakeNavigatorObject

		self.assertIsNone(flowForms.probeFor(FakeNavigatorObject("a note"), textInfos.UNIT_LINE))


if __name__ == "__main__":
	unittest.main()
