# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""The image suites again, with Pillow switched off.

Pillow reaches NVDA by accident of its build rather than as a promise (see `imagePins`), so the
Python path is not a leftover: it is what a reader gets on any NVDA whose build leaves Pillow
out. Every test written against the picture pipeline is therefore run both ways. The originals
run with Pillow, which the test environment installs; this file generates a copy of each of
their test cases that runs without it.

Generated rather than written out, so that a test added to one of those files is covered both
ways without anybody remembering to.
"""

import unittest

from ._stubs import installStubs

installStubs()

from brlMultiline import imagePins  # noqa: E402

from . import test_image, test_imageHexagon, test_imagePins  # noqa: E402


def _withoutPillow(case: type) -> type:
	""":return: a subclass of a test case that runs every test with Pillow off."""

	def setUp(self):
		saved = imagePins._Image
		imagePins._Image = None
		self.addCleanup(setattr, imagePins, "_Image", saved)
		case.setUp(self)

	return type(f"{case.__name__}WithoutPillow", (case,), {"setUp": setUp, "__module__": __name__})


for _module in (test_image, test_imageHexagon, test_imagePins):
	for _name, _case in vars(_module).items():
		if (
			isinstance(_case, type)
			and issubclass(_case, unittest.TestCase)
			and _case.__module__ == _module.__name__
			and any(attribute.startswith("test") for attribute in dir(_case))
		):
			globals()[f"{_name}WithoutPillow"] = _withoutPillow(_case)
