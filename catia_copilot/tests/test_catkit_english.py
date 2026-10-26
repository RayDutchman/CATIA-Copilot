"""CLI-facing CATIA contracts and errors use English text."""

import json
import unittest
from unittest.mock import patch

from catia_copilot.catia import geometry_faces, modeling_contract, tool_schema, tool_service
from catia_copilot.constants_data import FILENAME_NOT_FOUND, FILENAME_UNSAVED


class CliEnglishTests(unittest.TestCase):
    def test_tool_schema_descriptions_are_english(self):
        specs = {spec["function"]["name"]: spec["function"]["description"]
                 for spec in tool_schema.tools_schema}
        for name, text in specs.items():
            self.assertFalse(any("\u4e00" <= char <= "\u9fff" for char in text), text)

    def test_modeling_script_schema_is_english(self):
        text = modeling_contract.build_run_modeling_script_description()
        self.assertIn("Key Constraints", text)
        self.assertNotRegex(text, r"[\u4e00-\u9fff]")

    def test_generated_filename_status_labels_are_english(self):
        self.assertEqual(FILENAME_NOT_FOUND, "Not found")
        self.assertEqual(FILENAME_UNSAVED, "Unsaved")

    def test_geometry_failure_message_is_english(self):
        with self.assertRaisesRegex(ValueError, "finite, non-zero vector"):
            geometry_faces.filter_faces_by_normal([], (0, 0, 0))

    def test_disconnected_tool_error_is_english(self):
        with patch.object(tool_service, "get_catia_v5_com_dispatch", return_value=None):
            result = json.loads(tool_service.tool_open_catia_file("unused.CATPart"))
        self.assertEqual(result["error"], "CATIA is not connected")
        self.assertNotRegex(result["error"], r"[\u4e00-\u9fff]")
