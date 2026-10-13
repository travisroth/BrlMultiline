# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""A configuration profile's layered keys change only what they change, as other NVDA settings do.

Found on the Monarch: the overview keys, added to the normal configuration, did nothing in
Outlook, whose profile held a whole copy of the layers saved before they were added. A key is now
looked for in the profile's layer, then the normal configuration's layer of the same name, then
left to NVDA; a key bound to blocked still traps it.
"""

import json
import unittest
from dataclasses import replace

from . import _stubs
from ._stubs import installStubs

installStubs()

from brlMultiline import bmConfig, keyLayers  # noqa: E402
from brlMultiline.keyLayers import (  # noqa: E402
	DEFAULT_ID,
	MONARCH,
	PASS,
	RUN,
	LayerSet,
	Target,
	decide,
	newLayer,
	overlayText,
	stacked,
)

from .test_keyLayers import PAN_UP, PLUGIN, SAY_LINE, ZOOM_IN, monarch, monarchLayers  # noqa: E402

OVERVIEW = Target.script(PLUGIN, "GlobalPlugin", "layoutOverview")
PICTURE = Target.script(PLUGIN, "GlobalPlugin", "drawPicture")
FOCUS = Target.script("globalCommands", "GlobalCommands", "reportCurrentFocus")


def withBinding(layers: LayerSet, layerId: str, key: str, target) -> LayerSet:
	layer = layers.get(layerId)
	bindings = dict(layer.bindings)
	if target is None:
		bindings.pop(monarch(key), None)
	else:
		bindings[monarch(key)] = target
	return layers.withLayer(replace(layer, bindings=bindings))


def run(layers, layerId, key):
	return decide(layers, layerId, [monarch(key)], lambda: None)


class TestStacking(unittest.TestCase):
	def setUp(self):
		self.normal = monarchLayers()

	def test_withNoProfileTheNormalLayers(self):
		layers, problem = stacked(MONARCH, [self.normal.toText()])
		self.assertIsNone(problem)
		self.assertEqual(layers.toText(), self.normal.toText())

	def test_aProfileThatStoresNothingIsTheNormalLayers(self):
		layers, _problem = stacked(MONARCH, [self.normal.toText(), ""])
		self.assertEqual(layers.toText(), self.normal.toText())

	def test_profileThenNormalThenNVDA(self):
		"""The lookup the reader asked for, key by key."""
		inProfile = withBinding(self.normal, DEFAULT_ID, "dot1+dot2+dot4+dot5", PICTURE)
		layers, _problem = stacked(MONARCH, [self.normal.toText(), overlayText(inProfile, self.normal)])
		self.assertEqual(run(layers, DEFAULT_ID, "dot1+dot2+dot4+dot5").target, PICTURE, "the profile's")
		self.assertEqual(run(layers, DEFAULT_ID, "leftDpadDown").target, SAY_LINE, "normal's")
		self.assertEqual(run(layers, DEFAULT_ID, "dot5").action, PASS, "NVDA's")

	def test_aKeyAddedToNormalLaterReachesTheProfile(self):
		inProfile = withBinding(self.normal, DEFAULT_ID, "dot1+dot2+dot4+dot5", PICTURE)
		profile = overlayText(inProfile, self.normal)
		later = withBinding(self.normal, DEFAULT_ID, "dot1+dot2+dot3+dot4", OVERVIEW)
		layers, _problem = stacked(MONARCH, [later.toText(), profile])
		self.assertEqual(run(layers, DEFAULT_ID, "dot1+dot2+dot3+dot4").target, OVERVIEW)
		self.assertEqual(run(layers, DEFAULT_ID, "dot1+dot2+dot4+dot5").target, PICTURE)

	def test_aKeyTheProfileChangesIsTheProfiles(self):
		inProfile = withBinding(self.normal, DEFAULT_ID, "leftDpadDown", FOCUS)
		layers, _problem = stacked(MONARCH, [self.normal.toText(), overlayText(inProfile, self.normal)])
		self.assertEqual(run(layers, DEFAULT_ID, "leftDpadDown").target, FOCUS)

	def test_aKeyTheProfileRemovesGoesToNVDA(self):
		"""Not back to normal's binding: the reader took it out in this profile."""
		inProfile = withBinding(self.normal, DEFAULT_ID, "leftDpadDown", None)
		layers, _problem = stacked(MONARCH, [self.normal.toText(), overlayText(inProfile, self.normal)])
		self.assertEqual(run(layers, DEFAULT_ID, "leftDpadDown").action, PASS)

	def test_aKeyTheProfileBlocksIsTrapped(self):
		inProfile = withBinding(self.normal, DEFAULT_ID, "leftDpadDown", Target.blocked())
		layers, _problem = stacked(MONARCH, [self.normal.toText(), overlayText(inProfile, self.normal)])
		decision = run(layers, DEFAULT_ID, "leftDpadDown")
		self.assertEqual((decision.action, decision.target), (RUN, Target.blocked()))

	def test_aLayerOnlyTheProfileHas(self):
		mine = newLayer(MONARCH, "mail", "Mail", bindings={monarch("dot1"): FOCUS})
		inProfile = self.normal.withLayer(mine)
		layers, _problem = stacked(MONARCH, [self.normal.toText(), overlayText(inProfile, self.normal)])
		self.assertEqual(layers.get("mail"), mine)

	def test_aLayerTheProfileRemoves(self):
		inProfile = LayerSet(MONARCH, tuple(layer for layer in self.normal.layers if layer.id != "chart"))
		layers, _problem = stacked(MONARCH, [self.normal.toText(), overlayText(inProfile, self.normal)])
		self.assertIsNone(layers.get("chart"))
		self.assertIsNotNone(layers.get("graphics"))

	def test_aPropertyTheProfileChanges(self):
		graphics = self.normal.get("graphics")
		inProfile = self.normal.withLayer(replace(graphics, style=keyLayers.ONE_SHOT, context=None))
		layers, _problem = stacked(MONARCH, [self.normal.toText(), overlayText(inProfile, self.normal)])
		self.assertEqual(layers.get("graphics").style, keyLayers.ONE_SHOT)
		self.assertIsNone(layers.get("graphics").context, "a context taken off stays off")
		self.assertEqual(layers.get("graphics").bindings, graphics.bindings)

	def test_whatAProfileSavesReadsBackAsItWasEdited(self):
		edited = withBinding(self.normal, "graphics", "zoomIn", ZOOM_IN)
		edited = withBinding(edited, DEFAULT_ID, "leftDpadDown", None)
		edited = edited.withLayer(newLayer(MONARCH, "mail", "Mail"))
		layers, _problem = stacked(MONARCH, [self.normal.toText(), overlayText(edited, self.normal)])
		self.assertEqual(layers.toText(), edited.toText())

	def test_aProfileStoresOnlyWhatItChanges(self):
		edited = withBinding(self.normal, DEFAULT_ID, "dot1+dot2+dot4+dot5", PICTURE)
		stored = json.loads(overlayText(edited, self.normal))
		self.assertTrue(stored["overlay"])
		self.assertEqual(
			stored["layers"],
			[{"id": DEFAULT_ID, "bindings": {monarch("dot1+dot2+dot4+dot5"): PICTURE.toStored()}}],
		)

	def test_aProfileThatChangesNothingStoresNothing(self):
		self.assertEqual(overlayText(self.normal, self.normal), "")

	def test_profilesStackInTheOrderTheyCameOn(self):
		first = overlayText(withBinding(self.normal, DEFAULT_ID, "dot1", FOCUS), self.normal)
		second = overlayText(withBinding(self.normal, DEFAULT_ID, "dot1", PICTURE), self.normal)
		layers, _problem = stacked(MONARCH, [self.normal.toText(), first, second])
		self.assertEqual(run(layers, DEFAULT_ID, "dot1").target, PICTURE)

	def test_withNoNormalLayersAProfileIsOverTheShippedOnes(self):
		factory = keyLayers.defaultLayers(MONARCH, PLUGIN)
		shipped = LayerSet(MONARCH, tuple(factory))
		profile = overlayText(withBinding(shipped, DEFAULT_ID, "dot1", FOCUS), shipped)
		layers, _problem = stacked(MONARCH, ["", profile], factory)
		self.assertEqual(run(layers, DEFAULT_ID, "dot1").target, FOCUS)
		self.assertEqual(layers.get("graphics"), shipped.get("graphics"))


class TestAWholeCopyFromAnEarlierVersion(unittest.TestCase):
	"""What Outlook's profile held: every layer, as they were when it was saved."""

	def setUp(self):
		old = monarchLayers()
		self.copy = withBinding(old, DEFAULT_ID, "dot1+dot2+dot4+dot5", PICTURE).toText()
		self.normal = withBinding(old, DEFAULT_ID, "dot1+dot2+dot3+dot4", OVERVIEW)

	def test_aKeyAddedToNormalAfterTheCopyReachesIt(self):
		layers, _problem = stacked(MONARCH, [self.normal.toText(), self.copy])
		self.assertEqual(run(layers, DEFAULT_ID, "dot1+dot2+dot3+dot4").target, OVERVIEW)

	def test_whatTheCopyAddsIsKept(self):
		layers, _problem = stacked(MONARCH, [self.normal.toText(), self.copy])
		self.assertEqual(run(layers, DEFAULT_ID, "dot1+dot2+dot4+dot5").target, PICTURE)

	def test_whatTheCopyChangesIsKept(self):
		copy = withBinding(monarchLayers(), "graphics", "leftDpadUp", FOCUS).toText()
		layers, _problem = stacked(MONARCH, [self.normal.toText(), copy])
		self.assertEqual(run(layers, "graphics", "leftDpadUp").target, FOCUS)

	def test_aLayerTheCopyLacksComesFromNormal(self):
		copy = LayerSet(MONARCH, (monarchLayers().default,)).toText()
		layers, _problem = stacked(MONARCH, [self.normal.toText(), copy])
		self.assertEqual(run(layers, "graphics", "leftDpadUp").target, PAN_UP)

	def test_savedAgainInTheProfileItBecomesOnlyItsChanges(self):
		layers, _problem = stacked(MONARCH, [self.normal.toText(), self.copy])
		stored = json.loads(overlayText(layers, self.normal))
		self.assertEqual(
			stored["layers"],
			[{"id": DEFAULT_ID, "bindings": {monarch("dot1+dot2+dot4+dot5"): PICTURE.toStored()}}],
		)


class TestReadingEachProfile(unittest.TestCase):
	"""bmConfig reads each profile's own text rather than only the most specific one's."""

	def setUp(self):
		import config

		self.config = config
		self.addCleanup(config.conf.deactivateProfiles)
		bmConfig.setKeyLayerText(MONARCH, "normal's")

	def test_theNormalConfigurationAlone(self):
		self.assertEqual(bmConfig.keyLayerTexts(MONARCH), ["normal's"])
		self.assertFalse(bmConfig.keyLayersGoToProfile())

	def test_eachProfileInForceInOrder(self):
		self.config.conf.activateProfile({})
		self.config.conf.activateProfile(
			{bmConfig.CONFIG_SECTION: {"keyLayerDevices": {MONARCH: {"layers": "outlook's"}}}}
		)
		self.assertEqual(bmConfig.keyLayerTexts(MONARCH), ["normal's", "", "outlook's"])
		self.assertTrue(bmConfig.keyLayersGoToProfile())


class TestSavingInAProfile(unittest.TestCase):
	"""The layered keys dialog saves through `keyLayerDispatch.save`."""

	def setUp(self):
		from brlMultiline import keyLayerDispatch

		self.dispatch = keyLayerDispatch
		self.normal = monarchLayers().toText()
		self.profile = {}
		saved = (bmConfig.keyLayerTexts, bmConfig.keyLayersGoToProfile, bmConfig.setKeyLayerText)
		bmConfig.keyLayerTexts = lambda device: [self.normal, self.profile.get(device, "")]
		bmConfig.keyLayersGoToProfile = lambda: True
		bmConfig.setKeyLayerText = self.profile.__setitem__

		def restore():
			bmConfig.keyLayerTexts, bmConfig.keyLayersGoToProfile, bmConfig.setKeyLayerText = saved
			keyLayerDispatch._sets.clear()

		self.addCleanup(restore)

	def test_onlyTheChangeIsStoredInTheProfile(self):
		layers = self.dispatch.layerSet(MONARCH)
		self.dispatch.save(withBinding(layers, DEFAULT_ID, "dot1", FOCUS))
		stored = json.loads(self.profile[MONARCH])
		self.assertTrue(stored["overlay"])
		self.assertEqual(
			stored["layers"], [{"id": DEFAULT_ID, "bindings": {monarch("dot1"): FOCUS.toStored()}}]
		)
		self.assertEqual(self.normal, monarchLayers().toText(), "the normal configuration is not touched")

	def test_readAgainItIsTheEditedLayers(self):
		edited = withBinding(self.dispatch.layerSet(MONARCH), DEFAULT_ID, "dot1", FOCUS)
		self.dispatch.save(edited)
		self.dispatch._sets.clear()
		self.assertEqual(self.dispatch.layerSet(MONARCH).toText(), edited.toText())


if __name__ == "__main__":
	unittest.main()
	_stubs  # noqa: B018
