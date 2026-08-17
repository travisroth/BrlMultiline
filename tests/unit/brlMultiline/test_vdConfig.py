# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for where the combined display's member list is stored.

The list is which pieces of hardware are wired together, so it belongs in the base
configuration where a profile cannot change it. NVDA cannot honour a per profile answer
anyway: it does not reinitialise a display whose driver name has not changed, so the
composite would go on driving the members it opened while the configuration described a
different set.

Getting there is not just adding a name to a set. Everything NVDA does for a base only
section happens while the base configuration is being loaded, and this driver registers long
afterwards — so the section is neither created nor validated, and the reads have to survive
both. That is what most of these tests are about.
"""

import unittest

from ._stubs import installStubs, log, resetConfig

installStubs()

import config  # noqa: E402

from brailleDisplayDrivers.brlMultilineVirtual import vdConfig  # noqa: E402
from brailleDisplayDrivers.brlMultilineVirtual.virtualLayout import DeviceSpec  # noqa: E402

SECTION = vdConfig.CONFIG_SECTION
MONARCH = "hidBrailleStandard"
FOCUS = "freedomScientific"


class VdConfigTestCase(unittest.TestCase):
	def setUp(self):
		resetConfig()
		log.messages.clear()
		vdConfig.initialize()

	def tearDown(self):
		config.conf.deactivateProfiles()
		resetConfig()

	def freshStart(self):
		"""Put things back as they are when NVDA has just started.

		`BASE_ONLY_SECTIONS` is a class attribute, so within one test process it outlives the
		module that added to it. A real NVDA start has an empty one, and the migration depends
		on running before the section becomes base only — so a test of the migration has to
		undo both or it is testing the second run rather than the first.
		"""
		config.ConfigManager.BASE_ONLY_SECTIONS.discard(SECTION)
		vdConfig._initialised = False
		# And a base configuration with no value of its own, which is what a real one has:
		# nothing validated this section into existence, so the key does not exist until
		# something writes it. The reset between tests puts an empty one there.
		config.conf.profiles[0].get(SECTION, {}).pop("devices", None)

	def base(self):
		return config.conf.profiles[0].setdefault(SECTION, {})

	def names(self):
		return [spec.driverName for spec in vdConfig.getDevices()]


class TestWhereItIsStored(VdConfigTestCase):
	def test_theSectionIsBaseOnly(self):
		self.assertIn(SECTION, config.ConfigManager.BASE_ONLY_SECTIONS)

	def test_writingGoesToTheBaseConfiguration(self):
		vdConfig.setDevices([DeviceSpec(MONARCH), DeviceSpec(FOCUS)])
		self.assertEqual(self.base()["devices"], [MONARCH, FOCUS])

	def test_writingWithAProfileActiveStillGoesToBase(self):
		"""The bug this fixes: NVDA writes changed settings to the active profile."""
		profile = config.conf.activateProfile()
		vdConfig.setDevices([DeviceSpec(MONARCH)])
		self.assertEqual(self.base()["devices"], [MONARCH])
		self.assertNotIn(SECTION, profile)

	def test_theBaseSectionDoesNotDependOnTheRegistration(self):
		"""Two belts for one job, and this is the one that works on the very first run.

		`_makeBaseOnly` cannot have run before the first read of a fresh session, and it may
		fail if NVDA ever renames the set, so the reads and writes go to the base profile
		directly rather than trusting the redirect.
		"""
		self.freshStart()
		config.conf.activateProfile({SECTION: {"devices": [FOCUS]}})
		vdConfig._baseSection()["devices"] = [MONARCH]
		self.assertEqual(config.conf.profiles[0][SECTION]["devices"], [MONARCH])

	def test_readingIgnoresAProfilesCopy(self):
		vdConfig.setDevices([DeviceSpec(MONARCH)])
		config.conf.activateProfile({SECTION: {"devices": [FOCUS]}})
		self.assertEqual(self.names(), [MONARCH])

	def test_switchingProfileCannotChangeTheList(self):
		vdConfig.setDevices([DeviceSpec(MONARCH), DeviceSpec(FOCUS)])
		before = self.names()
		config.conf.activateProfile({SECTION: {"devices": []}})
		self.assertEqual(self.names(), before)
		config.conf.deactivateProfiles()
		self.assertEqual(self.names(), before)


class TestMigration(VdConfigTestCase):
	"""A list stored under a profile before the section was base only must not vanish."""

	def test_aListInAProfileIsMovedToBase(self):
		self.freshStart()
		config.conf.activateProfile({SECTION: {"devices": [MONARCH, FOCUS]}})
		vdConfig.initialize()
		self.assertEqual(self.base()["devices"], [MONARCH, FOCUS])
		self.assertEqual(self.names(), [MONARCH, FOCUS])

	def test_movingItIsReported(self):
		self.freshStart()
		config.conf.activateProfile({SECTION: {"devices": [MONARCH]}})
		vdConfig.initialize()
		self.assertTrue(
			any(level == "info" and "base configuration" in message for level, message in log.messages),
			log.messages,
		)

	def test_aListAlreadyInBaseIsNotOverwritten(self):
		"""Base is the authority. A profile's copy is stale by definition."""
		self.freshStart()
		self.base()["devices"] = [MONARCH]
		config.conf.activateProfile({SECTION: {"devices": [FOCUS]}})
		vdConfig.initialize()
		self.assertEqual(self.names(), [MONARCH])

	def test_aLeftoverProfileCopyIsReported(self):
		"""So that a puzzling ini file has an explanation, rather than being edited out."""
		self.freshStart()
		self.base()["devices"] = [MONARCH]
		config.conf.activateProfile({SECTION: {"devices": [FOCUS]}})
		vdConfig.initialize()
		self.assertTrue(
			any("profile's copy is ignored" in message for _level, message in log.messages),
			log.messages,
		)

	def test_nothingStoredMigratesNothing(self):
		self.freshStart()
		vdConfig.initialize()
		self.assertEqual(self.names(), [])
		self.assertFalse(self.base().get("devices"))

	def test_theMigrationRunsBeforeTheSectionBecomesBaseOnly(self):
		"""Ordering, asserted directly: afterwards a profile's copy is unreachable."""
		self.freshStart()
		self.assertNotIn(SECTION, config.ConfigManager.BASE_ONLY_SECTIONS)
		config.conf.activateProfile({SECTION: {"devices": [FOCUS]}})
		vdConfig.initialize()
		self.assertIn(SECTION, config.ConfigManager.BASE_ONLY_SECTIONS)
		self.assertEqual(self.names(), [FOCUS])


class TestReadingAnUnvalidatedSection(VdConfigTestCase):
	"""Nothing validated this section into existence, so the reads have to cope alone.

	Each of these would otherwise cost the user braille entirely: the driver's constructor
	raises when it cannot read its configuration, and NVDA falls back to no braille.
	"""

	def test_aSingleEntryStoredAsAStringIsOneDisplay(self):
		"""configobj's own wart, and the one that would have been hardest to diagnose.

		Without validation a list of exactly one entry reads back as a bare string, so this
		would otherwise be seventeen displays, one per character.
		"""
		self.base()["devices"] = FOCUS
		self.assertEqual(self.names(), [FOCUS])

	def test_anEmptyStringIsNoDisplays(self):
		self.base()["devices"] = "   "
		self.assertEqual(self.names(), [])

	def test_aMissingKeyIsNoDisplays(self):
		self.base().pop("devices", None)
		self.assertEqual(self.names(), [])

	def test_aMissingSectionIsNoDisplays(self):
		config.conf.profiles[0].pop(SECTION, None)
		self.assertEqual(self.names(), [])

	def test_aValueThatIsNotAListIsReported(self):
		self.base()["devices"] = 42
		self.assertEqual(self.names(), [])
		self.assertTrue(any(level == "error" for level, _message in log.messages), log.messages)

	def test_aBrokenConfigurationIsReportedRatherThanRaised(self):
		original = config.conf.profiles
		del config.conf.profiles
		try:
			self.assertEqual(self.names(), [])
		finally:
			config.conf.profiles = original
		self.assertTrue(any(level == "error" for level, _message in log.messages), log.messages)


class TestTheUsersOwnMistakes(VdConfigTestCase):
	"""Content errors still raise, because the user needs to hear about those."""

	def test_oneDriverTwiceIsRefusedWhenRead(self):
		self.base()["devices"] = [FOCUS, FOCUS]
		with self.assertRaises(ValueError):
			vdConfig.getDevices()

	def test_oneDriverTwiceIsRefusedWhenWritten(self):
		with self.assertRaises(ValueError):
			vdConfig.setDevices([DeviceSpec(FOCUS), DeviceSpec(FOCUS)])

	def test_theVirtualDisplayCannotBeItsOwnMember(self):
		self.base()["devices"] = ["brlMultilineVirtual"]
		with self.assertRaises(ValueError):
			vdConfig.getDevices()

	def test_aRefusedWriteChangesNothing(self):
		vdConfig.setDevices([DeviceSpec(MONARCH)])
		with self.assertRaises(ValueError):
			vdConfig.setDevices([DeviceSpec(FOCUS), DeviceSpec(FOCUS)])
		self.assertEqual(self.names(), [MONARCH])


class TestPortsSurvive(VdConfigTestCase):
	def test_aPortIsStoredAndReadBack(self):
		vdConfig.setDevices([DeviceSpec(FOCUS, "COM4")])
		self.assertEqual(self.base()["devices"], ["freedomScientific|COM4"])
		self.assertEqual(vdConfig.getDevices(), [DeviceSpec(FOCUS, "COM4")])

	def test_aNameAloneMeansDetectAfresh(self):
		vdConfig.setDevices([DeviceSpec(FOCUS)])
		self.assertEqual(self.base()["devices"], [FOCUS])
		self.assertEqual(vdConfig.getDevices()[0].port, "auto")
