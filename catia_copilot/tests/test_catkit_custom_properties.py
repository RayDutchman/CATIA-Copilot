"""CLI custom-property writes must not be limited to GUI preset fields."""
import json
import unittest
from unittest.mock import patch


class TestCliCustomProperties(unittest.TestCase):
    def test_arbitrary_names_and_values_reach_document_backend(self):
        from catia_copilot.catia import tool_service

        properties = {"客户自定义字段": "任意中文值", "Internal note": "unrestricted",
                      "设计状态": "项目自定义状态"}
        with patch.object(tool_service, "set_document_properties", return_value={
            "written_user_defined": list(properties), "skipped": [], "saved": False,
        }) as backend:
            result = json.loads(tool_service.tool_set_document_properties(
                file_path="part.CATPart", user_defined=properties,
            ))

        self.assertEqual(result["written_user_defined"], list(properties))
        backend.assert_called_once_with("part.CATPart", standard=None, user_defined=properties, save=False)

    def test_empty_input_keeps_existing_error(self):
        from catia_copilot.catia.tool_service import tool_set_document_properties

        self.assertEqual(json.loads(tool_set_document_properties()), {
            "error": "standard and user_defined cannot both be empty; provide at least one property to write"
        })

    def test_schema_describes_unrestricted_custom_properties(self):
        from catia_copilot.catia.tool_schema import tools_schema

        function = next(item["function"] for item in tools_schema
                        if item["function"]["name"] == "set_document_properties")
        text = function["description"] + function["parameters"]["properties"]["user_defined"]["description"]
        self.assertNotIn("must use the existing", text.lower())
        self.assertNotIn("设计状态", text)
        self.assertIn("not restricted", text.lower())

    def test_bom_custom_columns_are_explicit_not_preset_based(self):
        from catia_copilot.catia.tool_schema import tools_schema

        function = next(item["function"] for item in tools_schema
                        if item["function"]["name"] == "write_bom_to_catia")
        text = function["parameters"]["properties"]["custom_columns"]["description"]
        self.assertNotIn("Preset names", text)
        self.assertIn("explicitly", text.lower())
