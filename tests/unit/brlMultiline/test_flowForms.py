# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for recognising a form control, and for what a flow does differently for one.

Two decisions live here and nowhere else: which roles count as controls, and how many rows
of a control's prompt belong above it. Both are policy rather than arithmetic, so both are
stated in one place and tested against that statement.

The recognition is asked of the object NVDA hands the band on a focus change. Reading it out
of the document's own field commands was tried first and was wrong twice over — see the
module docstring — and the case that proves it is a reader tabbing into an edit field, where
browse mode drops into focus mode and the reading position goes inside the control.
"""

import unittest

from ._stubs import installStubs

installStubs()

from brlMultiline import flowForms  # noqa: E402


class FakeRole:
	"""An enumeration member, which is what NVDA gives an object as its role."""

	def __init__(self, name):
		self.name = name


class FakeObject:
	"""An object with a role, which is all this policy asks of one."""

	def __init__(self, role, states=()):
		self.role = role
		self.states = set(states)


class TestWhatCountsAsAControl(unittest.TestCase):
	def test_theThingsAReaderStopsAtAndAnswers(self):
		for name in ("EDITABLETEXT", "COMBOBOX", "CHECKBOX", "RADIOBUTTON", "BUTTON", "SLIDER"):
			with self.subTest(role=name):
				self.assertTrue(flowForms.isControlRole(FakeRole(name)))

	def test_aLinkIsNotOne(self):
		# A page of links would otherwise spend a row of spacing on each of them.
		for name in ("LINK", "LISTITEM", "PARAGRAPH", "HEADING", "STATICTEXT"):
			with self.subTest(role=name):
				self.assertFalse(flowForms.isControlRole(FakeRole(name)))

	def test_nothingIsNotOne(self):
		self.assertFalse(flowForms.isControlRole(None))

	def test_aRoleGivenAsTextStillReads(self):
		"""So that an object carrying a bare string is classified rather than raising."""
		self.assertTrue(flowForms.isControlRole("editableText"))

	def test_aRoleNameIsTakenFromTheMemberNotItsNumber(self):
		self.assertEqual(flowForms.roleName(FakeRole("comboBox")), "COMBOBOX")


class TestRecognisingWhatTheReaderArrivedAt(unittest.TestCase):
	"""Asked of the object, which is the only account right in both browse and focus mode."""

	def test_anEditFieldIsAControl(self):
		self.assertTrue(flowForms.isControlObject(FakeObject(FakeRole("EDITABLETEXT"))))

	def test_anEditFieldOwnsEditableText(self):
		self.assertTrue(flowForms.isEditableObject(FakeObject(FakeRole("EDITABLETEXT"))))

	def test_aCustomEditableObjectOwnsEditableText(self):
		self.assertTrue(
			flowForms.isEditableObject(
				FakeObject(FakeRole("DOCUMENT"), states={FakeRole("EDITABLE")}),
			)
		)

	def test_aReadOnlyDocumentDoesNotOwnEditableText(self):
		self.assertFalse(flowForms.isEditableObject(FakeObject(FakeRole("DOCUMENT"))))

	def test_aComboBoxIsAControl(self):
		# The two the hardware run found missing their prompt, because tabbing to either
		# drops browse mode into focus mode and puts the reading position inside them.
		self.assertTrue(flowForms.isControlObject(FakeObject(FakeRole("COMBOBOX"))))

	def test_aCheckBoxIsAControl(self):
		self.assertTrue(flowForms.isControlObject(FakeObject(FakeRole("CHECKBOX"))))

	def test_aParagraphIsNot(self):
		self.assertFalse(flowForms.isControlObject(FakeObject(FakeRole("PARAGRAPH"))))

	def test_nothingIsNot(self):
		self.assertFalse(flowForms.isControlObject(None))

	def test_anObjectWithNoRoleIsNot(self):
		self.assertFalse(flowForms.isControlObject(object()))

	def test_anObjectThatWillNotSayIsNot(self):
		"""Which costs the reader a row of context and nothing else."""

		class Difficult:
			@property
			def role(self):
				raise RuntimeError("cannot say")

		self.assertFalse(flowForms.isControlObject(Difficult()))


class TestHowMuchContext(unittest.TestCase):
	"""A prompt goes above its control, and a prompt that filled the display would not help."""

	def test_aOneRowPromptSitsAboveTheControl(self):
		self.assertEqual(flowForms.contextRowsFor(previousRows=1, gapRows=0, bandRows=8), 1)

	def test_declaredSpacingIsCountedToo(self):
		"""Or the window would start a row inside the prompt rather than at its top."""
		self.assertEqual(flowForms.contextRowsFor(previousRows=1, gapRows=1, bandRows=8), 2)

	def test_aLongPromptIsShownByItsLastRows(self):
		# Half the band, so the control itself is still on the display.
		self.assertEqual(flowForms.contextRowsFor(previousRows=20, gapRows=0, bandRows=8), 4)

	def test_nothingAboveMeansNoContext(self):
		self.assertEqual(flowForms.contextRowsFor(previousRows=0, gapRows=0, bandRows=8), 0)

	def test_aSingleRowBandHasNoRoomForContext(self):
		"""On one row a prompt above the control would be the control not shown."""
		self.assertEqual(flowForms.contextRowsFor(previousRows=1, gapRows=0, bandRows=1), 0)

	def test_aTwoRowBandStillManagesOne(self):
		self.assertEqual(flowForms.contextRowsFor(previousRows=1, gapRows=0, bandRows=2), 1)


if __name__ == "__main__":
	unittest.main()
