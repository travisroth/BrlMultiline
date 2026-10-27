# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Each feature object's host protocol is true to the plugin and to the feature.

Read from the source, so that a protocol stays the list of what its feature uses of the plugin
without a type checker. See docs/design/architecture.md, Module map.
"""

import ast
import os
import re
import unittest

PACKAGE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "addon", "globalPlugins", "brlMultiline")

HOSTS = {
	"PictureHost": ("pictureCommands", ["pictureCommands"]),
	"LayoutHost": ("layoutOverview", ["layoutOverview"]),
	"TableHost": ("tableCommands", ["tableCommands"]),
	"FocusHost": ("focusDisplays", ["focusDisplays"]),
	"ContextHost": ("keyLayerContexts", ["keyLayerContexts", "keyLayerCommands"]),
}
"""Each protocol: the module defining it, and the modules whose use of the plugin it lists."""


def source(module: str) -> str:
	with open(os.path.join(PACKAGE, module + ".py"), encoding="utf-8") as file:
		return file.read()


def classNamed(tree: ast.Module, name: str) -> ast.ClassDef:
	return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)


def isProperty(function: ast.FunctionDef) -> bool:
	return any(isinstance(d, ast.Name) and d.id == "property" for d in function.decorator_list)


def parameters(function: ast.FunctionDef) -> list[str]:
	return [arg.arg for arg in function.args.args]


def protocolMembers(name: str) -> tuple[dict[str, ast.FunctionDef], set[str]]:
	""":return: a protocol's methods and properties by name, and its plain attributes."""
	module, _users = HOSTS[name]
	protocol = classNamed(ast.parse(source(module)), name)
	functions = {node.name: node for node in protocol.body if isinstance(node, ast.FunctionDef)}
	attributes = {
		node.target.id
		for node in protocol.body
		if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
	}
	return functions, attributes


def pluginMembers() -> tuple[dict[str, ast.FunctionDef], set[str]]:
	""":return: the plugin's methods and properties by name, and the attributes it assigns."""
	text = source("__init__")
	plugin = classNamed(ast.parse(text), "GlobalPlugin")
	functions = {node.name: node for node in plugin.body if isinstance(node, ast.FunctionDef)}
	attributes = set(re.findall(r"\bself\.(\w+)\s*(?::[^=\n]+)?=(?!=)", text))
	return functions, attributes


def usedOfThePlugin(modules: list[str]) -> set[str]:
	""":return: the plugin members the modules reach, as `plugin.name` or `getattr(plugin, "name")`."""
	used: set[str] = set()
	for module in modules:
		text = source(module)
		used |= set(re.findall(r"\bplugin\.(\w+)", text))
		used |= set(re.findall(r'getattr\(plugin, "(\w+)"', text))
	return used


class TestHostProtocols(unittest.TestCase):
	def test_aFeatureUsesOnlyWhatItsProtocolLists(self):
		for name, (_module, users) in HOSTS.items():
			with self.subTest(protocol=name):
				functions, attributes = protocolMembers(name)
				self.assertEqual(usedOfThePlugin(users) - set(functions) - attributes, set())

	def test_aProtocolListsNothingItsFeatureDoesNotUse(self):
		for name, (_module, users) in HOSTS.items():
			with self.subTest(protocol=name):
				functions, attributes = protocolMembers(name)
				self.assertEqual((set(functions) | attributes) - usedOfThePlugin(users), set())

	def test_thePluginHasEveryAttributeAProtocolLists(self):
		pluginFunctions, pluginAttributes = pluginMembers()
		for name in HOSTS:
			_functions, attributes = protocolMembers(name)
			with self.subTest(protocol=name):
				self.assertEqual(attributes - pluginAttributes - set(pluginFunctions), set())

	def test_thePluginHasEveryMethodAndPropertyAProtocolLists(self):
		pluginFunctions, _attributes = pluginMembers()
		for name in HOSTS:
			functions, _attributes = protocolMembers(name)
			for member, function in functions.items():
				with self.subTest(protocol=name, member=member):
					self.assertIn(member, pluginFunctions)
					onPlugin = pluginFunctions[member]
					self.assertEqual(isProperty(function), isProperty(onPlugin))
					self.assertEqual(parameters(function), parameters(onPlugin))


if __name__ == "__main__":
	unittest.main()
