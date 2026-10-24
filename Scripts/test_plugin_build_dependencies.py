#!/usr/bin/env python3
"""Check the workspace's plugin-to-app build edges on macOS.

Run: python3 Scripts/test_plugin_build_dependencies.py
The negative test removes each edge in memory, without modifying any project.
"""

import copy
import json
from pathlib import Path
import subprocess
import unittest
import xml.etree.ElementTree as ET


WORKSPACE = Path(__file__).resolve().parents[1]
LOOP_TARGET = "43776F8B1B8022E90074EA36"


def read_project(path):
    return json.loads(subprocess.check_output(
        ["plutil", "-convert", "json", "-o", "-", str(path)], text=True
    ))["objects"]


def expected_plugins():
    scheme = ET.parse(WORKSPACE / "LoopWorkspace.xcworkspace/xcshareddata/xcschemes/LoopWorkspace.xcscheme")
    return {
        (ref.attrib["ReferencedContainer"].removeprefix("container:"),
         ref.attrib["BlueprintIdentifier"]): ref.attrib["BlueprintName"]
        for ref in scheme.findall(".//BuildActionEntry/BuildableReference")
        if ref.attrib["BuildableName"].endswith(".loopplugin")
    }


def declared_plugins(objects):
    result = {}
    for dependency in objects[LOOP_TARGET]["dependencies"]:
        proxy = objects[objects[dependency]["targetProxy"]]
        container = objects[proxy["containerPortal"]]
        if container["isa"] != "PBXFileReference":
            continue
        project = (WORKSPACE / "Loop" / container["path"]).resolve()
        target = read_project(project / "project.pbxproj")[proxy["remoteGlobalIDString"]]
        key = (project.relative_to(WORKSPACE).as_posix(), proxy["remoteGlobalIDString"])
        result[key] = target["name"]
    return result


class PluginBuildDependencies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.objects = read_project(WORKSPACE / "Loop/Loop.xcodeproj/project.pbxproj")
        cls.expected = expected_plugins()

    def test_every_workspace_plugin_precedes_loop(self):
        self.assertTrue(self.expected, "No plugins found: verify the selected scheme")
        self.assertEqual(self.expected, declared_plugins(self.objects))

    def test_missing_edges_are_detected(self):
        tested = 0
        for dependency in self.objects[LOOP_TARGET]["dependencies"]:
            proxy = self.objects[self.objects[dependency]["targetProxy"]]
            if self.objects[proxy["containerPortal"]]["isa"] != "PBXFileReference":
                continue
            with self.subTest(plugin=proxy["remoteInfo"]):
                mutated = copy.deepcopy(self.objects)
                mutated[LOOP_TARGET]["dependencies"].remove(dependency)
                self.assertNotEqual(self.expected, declared_plugins(mutated))
                tested += 1
        self.assertEqual(len(self.expected), tested)


if __name__ == "__main__":
    unittest.main(verbosity=2)
