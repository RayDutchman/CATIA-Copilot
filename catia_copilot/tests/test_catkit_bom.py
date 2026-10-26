"""使用替身验证 BOM 业务，不连接真实 CATIA。"""

import unittest
from unittest.mock import patch

from catia_copilot.catia import bom_collect_v3
from catia_copilot.catia import bom_service


class _Collection:
    def __init__(self, items, error=None):
        self.items = list(items)
        self.error = error
        self.Count = len(self.items)

    def Item(self, index):
        if self.error:
            raise self.error
        return self.items[index - 1]


class _UserProperties:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def Item(self, name):
        if name not in self.values:
            raise KeyError(name)
        return type("Property", (), {"Value": self.values[name]})()

    def CreateString(self, name, value):
        self.values[name] = value


class _Product:
    def __init__(self, pn, name=None, children=(), filepath="", user_properties=None):
        self.PartNumber = pn
        self.Name = name or pn
        self.Products = _Collection(children)
        self.ReferenceProduct = self
        self.Parent = type("Parent", (), {"FullName": filepath})()
        self.UserRefProperties = _UserProperties(user_properties)

    def GetWorkMode(self):
        return 0

    def ApplyWorkMode(self, _mode):
        return None


class _Document:
    def __init__(self, name, product):
        self.Name = name
        self.FullName = product.Parent.FullName
        self.Type = "Product"
        self.Product = product


class _Application:
    def __init__(self, documents, active):
        self.Documents = _Collection(documents)
        self.ActiveDocument = active


class TestBomCollection(unittest.TestCase):
    def test_part_number_reuses_existing_workbench_character_rules(self):
        child = _Product('PN', filepath='C:/cad/pn.CATPart')
        root = _Product('ROOT', children=[child], filepath='C:/cad/root.CATProduct')
        document = _Document('root.CATProduct', root)
        app = _Application([document], document)
        values = ['', '  ', '含中文', 'A/B', 'A:B', 'A\x01B']
        changes = [{'node': '0.0', 'scope': 'master', 'column': 'Part Number', 'value': value}
                   for value in values]
        with patch.object(bom_service, 'get_catia_v5_application', return_value=app), \
             patch.object(bom_service, 'write_cell', return_value=True) as write:
            result = bom_service.edit_bom(changes)
        self.assertEqual(result['applied_count'], 0)
        self.assertEqual(result['failed_count'], len(values))
        write.assert_not_called()

    def test_collector_accepts_existing_root_product_without_connecting(self):
        import inspect

        parameter = inspect.signature(bom_collect_v3.collect_bom_part_masters).parameters["root_product"]
        self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(parameter.default)

        root = _Product("ROOT")
        with patch.object(bom_collect_v3, "get_catia_v5_application", side_effect=AssertionError("connected")):
            root_key, masters, _ = bom_collect_v3.collect_bom_part_masters(
                "must-not-load.CATProduct", [], [], root_product=root
            )
        self.assertEqual(root_key, "ROOT")
        self.assertIs(masters["ROOT"]["_product"], root)

    def test_explicit_non_active_unsaved_document_is_collected(self):
        target = _Document("target.CATProduct", _Product("TARGET"))
        active = _Document("active.CATProduct", _Product("ACTIVE"))
        app = _Application([active, target], active)
        seen = {}

        def collect(root_key, masters):
            seen["root_key"] = root_key
            return [{"Level": 0, "Part Number": root_key, "Quantity": 1, "_product": target.Product}]

        with patch("catia_copilot.catia.bom_service.get_catia_v5_application", return_value=app), \
             patch("catia_copilot.catia.bom_service.collect_bom_part_masters", return_value=("TARGET", {}, {})) as collect_masters, \
             patch("catia_copilot.catia.bom_service.iter_hierarchical_rows", side_effect=collect):
            result = bom_service.collect_bom(doc="target.CATProduct")

        self.assertEqual(seen["root_key"], "TARGET")
        self.assertIs(collect_masters.call_args.kwargs["root_product"], target.Product)
        self.assertEqual(result["rows"][0]["Part Number"], "TARGET")
        self.assertNotIn("_product", result["rows"][0])

    def test_collection_supports_three_views_and_attaches_serializable_targets(self):
        root = _Product("ROOT")
        document = _Document("root.CATProduct", root)
        app = _Application([document], document)
        views = {}
        for view in ("full", "hierarchical", "summary"):
            with patch("catia_copilot.catia.bom_service.get_catia_v5_application", return_value=app), \
                 patch("catia_copilot.catia.bom_service.collect_bom_part_masters", return_value=("ROOT", {}, {})), \
                 patch("catia_copilot.catia.bom_service.iter_full_rows", return_value=[{"Level": 0, "Part Number": "ROOT", "_product": root}]), \
                 patch("catia_copilot.catia.bom_service.iter_hierarchical_rows", return_value=[{"Level": 0, "Part Number": "ROOT", "_product": root}]), \
                 patch("catia_copilot.catia.bom_service.flatten_bom_to_summary", return_value=[{"Level": 0, "Part Number": "ROOT", "_product": root}]):
                views[view] = bom_service.collect_bom(view=view)
        self.assertEqual(set(views), {"full", "hierarchical", "summary"})
        row = views["full"]["rows"][0]
        self.assertEqual(row["targets"][0]["node"], "0")
        self.assertEqual(views["full"]["row_count"], 1)
        self.assertEqual(views["full"]["columns"], list(bom_service.BOM_DEFAULT_COLUMNS))
        self.assertNotIn("_product", row)
        self.assertFalse(any(key.startswith("_") for key in row))
        self.assertFalse(any(key.startswith("_") for key in row["targets"][0]))

    def test_custom_columns_cannot_replace_reserved_row_fields(self):
        root = _Product("ROOT")
        document = _Document("root.CATProduct", root)
        app = _Application([document], document)
        with patch("catia_copilot.catia.bom_service.get_catia_v5_application", return_value=app):
            with self.assertRaisesRegex(ValueError, "保留字段"):
                bom_service.collect_bom(custom_columns=["Quantity"])

    def test_duplicate_references_have_distinct_full_paths_and_grouped_hierarchical_targets(self):
        first = _Product("PN", "PN.1", filepath="C:/cad/pn.CATPart")
        second = _Product("PN", "PN.1", filepath="C:/cad/pn.CATPart")
        root = _Product("ROOT", children=[first, second], filepath="C:/cad/root.CATProduct")
        document = _Document("root.CATProduct", root)
        app = _Application([document], document)
        root_pm = {
            "part_number": "ROOT", "pm_key": "ROOT", "_product": root,
            "filepath": "C:/cad/root.CATProduct", "instances": [
                {"inst_key": id(first), "pn": "PN", "pm_key": "PN", "instance_name": "PN.1", "product": first},
                {"inst_key": id(second), "pn": "PN", "pm_key": "PN", "instance_name": "PN.1", "product": second},
            ],
        }
        part_pm = {
            "part_number": "PN", "pm_key": "PN", "filepath": "C:/cad/pn.CATPart",
            "instances": [], "type": "",
        }
        with patch("catia_copilot.catia.bom_service.get_catia_v5_application", return_value=app), \
             patch("catia_copilot.catia.bom_service.collect_bom_part_masters", return_value=("ROOT", {"ROOT": root_pm, "PN": part_pm}, {})):
            full = bom_service.collect_bom(view="full", columns=["Part Number", "Instance Name"])
            hierarchical = bom_service.collect_bom(view="hierarchical")
        full_part_rows = [row for row in full["rows"] if row["Part Number"] == "PN"]
        self.assertEqual([row["targets"][0]["node"] for row in full_part_rows], ["0.0", "0.1"])
        hierarchical_part_row = next(row for row in hierarchical["rows"] if row["Part Number"] == "PN")
        self.assertEqual(hierarchical_part_row["Quantity"], 2)
        self.assertEqual([target["node"] for target in hierarchical_part_row["targets"]], ["0.0", "0.1"])
        self.assertEqual(full["columns"], ["Part Number", "Instance Name"])

    def test_hierarchical_child_targets_are_scoped_to_their_parent_paths(self):
        a_child = _Product("PN", "PN.1", filepath="C:/cad/pn.CATPart")
        b_child = _Product("PN", "PN.2", filepath="C:/cad/pn.CATPart")
        assembly_a = _Product("A", "A.1", [a_child], "C:/cad/a.CATProduct")
        assembly_b = _Product("B", "B.1", [b_child], "C:/cad/b.CATProduct")
        root = _Product("ROOT", children=[assembly_a, assembly_b], filepath="C:/cad/root.CATProduct")
        doc = _Document("root.CATProduct", root)
        app = _Application([doc], doc)

        def instance(product, pn, key):
            return {"inst_key": id(product), "pn": pn, "pm_key": key,
                    "instance_name": product.Name, "product": product}

        masters = {
            "ROOT": {"part_number": "ROOT", "pm_key": "ROOT", "_product": root,
                     "filepath": "C:/cad/root.CATProduct",
                     "instances": [instance(assembly_a, "A", "A"), instance(assembly_b, "B", "B")]},
            "A": {"part_number": "A", "pm_key": "A", "filepath": "C:/cad/a.CATProduct",
                  "type": "产品", "instances": [instance(a_child, "PN", "PN")]},
            "B": {"part_number": "B", "pm_key": "B", "filepath": "C:/cad/b.CATProduct",
                  "type": "产品", "instances": [instance(b_child, "PN", "PN")]},
            "PN": {"part_number": "PN", "pm_key": "PN", "filepath": "C:/cad/pn.CATPart",
                   "type": "零件", "instances": []},
        }
        with patch("catia_copilot.catia.bom_service.get_catia_v5_application", return_value=app), \
             patch("catia_copilot.catia.bom_service.collect_bom_part_masters", return_value=("ROOT", masters, {})):
            result = bom_service.collect_bom(view="hierarchical")
        child_rows = [row for row in result["rows"] if row["Part Number"] == "PN"]
        self.assertEqual([row["targets"][0]["node"] for row in child_rows], ["0.0.0", "0.1.0"])

    def test_backend_collect_uses_real_collector_and_forwards_progress_safely(self):
        from contextlib import redirect_stderr
        from io import StringIO
        from catia_copilot.cli import backend

        part_ref = _Product("P1", "P1", filepath="C:/cad/P1.CATPart",
                            user_properties={"Note": "steel"})
        first = _Product("P1", "P1.1", filepath="C:/cad/P1.CATPart")
        second = _Product("P1", "P1.2", filepath="C:/cad/P1.CATPart")
        first.ReferenceProduct = part_ref
        second.ReferenceProduct = part_ref
        root = _Product("ROOT", "ROOT", [first, second], "C:/cad/ROOT.CATProduct")
        document = _Document("ROOT.CATProduct", root)
        app = _Application([document], document)
        errors = StringIO()

        with patch("catia_copilot.catia.bom_service.get_catia_v5_application", return_value=app), \
             patch.object(backend.pythoncom, "CoInitialize"), \
             patch.object(backend.pythoncom, "CoUninitialize"), \
             redirect_stderr(errors):
            result = backend.execute("collect_bom", {
                "view": "full",
                "columns": ["Part Number", "Instance Name", "Revision", "Note", "Part Number"],
                "custom_columns": ["Note"],
            })

        self.assertEqual(result["columns"], ["Part Number", "Instance Name", "Revision", "Note"])
        self.assertEqual(result["row_count"], 3)
        self.assertEqual([row["targets"][0]["node"] for row in result["rows"]], ["0", "0.0", "0.1"])
        self.assertEqual(result["rows"][1]["Note"], "steel")
        self.assertIn("进度", errors.getvalue())

        with patch("catia_copilot.catia.bom_service.get_catia_v5_application", return_value=app), \
             patch.object(backend.pythoncom, "CoInitialize"), \
             patch.object(backend.pythoncom, "CoUninitialize"), \
             redirect_stderr(errors):
            edited = backend.execute("edit_bom", {"changes": [
                {"node": "0.0", "scope": "master", "column": "Revision", "value": "B"},
                {"node": "0.0", "scope": "master", "column": "Quantity", "value": "3"},
                {"node": "0.1", "scope": "instance", "column": "Instance Name", "value": "Chosen"},
            ]})
        self.assertEqual((edited["applied_count"], edited["failed_count"]), (2, 1))
        self.assertEqual(part_ref.Revision, "B")
        self.assertEqual(second.Name, "Chosen")

        with patch("catia_copilot.catia.bom_service.get_catia_v5_application", return_value=app), \
             patch.object(backend.pythoncom, "CoInitialize"), \
             patch.object(backend.pythoncom, "CoUninitialize"), \
             redirect_stderr(errors):
            summary = backend.execute("collect_bom", {
                "view": "summary",
                "columns": ["Level", "Part Number", "Quantity", "Note", "Part Number"],
                "custom_columns": ["Note"],
            })
        self.assertEqual(summary["columns"], ["Part Number", "Quantity", "Note"])

    def test_real_collector_reports_same_pm_key_with_different_files(self):
        first_ref = _Product("PN", "PN", filepath="C:/cad/first.CATPart")
        second_ref = _Product("PN", "PN", filepath="C:/cad/second.CATPart")
        first = _Product("PN", "PN.1", filepath="C:/cad/first.CATPart")
        second = _Product("PN", "PN.2", filepath="C:/cad/second.CATPart")
        first.ReferenceProduct = first_ref
        second.ReferenceProduct = second_ref
        root = _Product("ROOT", "ROOT", [first, second], "C:/cad/ROOT.CATProduct")
        doc = _Document("ROOT.CATProduct", root)
        app = _Application([doc], doc)
        with patch("catia_copilot.catia.bom_service.get_catia_v5_application", return_value=app):
            with self.assertRaisesRegex(ValueError, "歧义"):
                bom_service.collect_bom()

    def test_collector_options_reject_legacy_keywords(self):
        root = _Product("ROOT", "ROOT", filepath="C:/cad/ROOT.CATProduct")
        doc = _Document("ROOT.CATProduct", root)
        app = _Application([doc], doc)
        with patch("catia_copilot.catia.bom_service.get_catia_v5_application", return_value=app):
            with self.assertRaisesRegex(TypeError, "file_path"):
                bom_service.collect_bom(file_path="not-an-input.CATProduct")

    def test_custom_columns_cannot_shadow_filename_or_internal_names_in_edit(self):
        root = _Product("ROOT", "ROOT", filepath="C:/cad/ROOT.CATProduct")
        doc = _Document("ROOT.CATProduct", root)
        app = _Application([doc], doc)
        with patch("catia_copilot.catia.bom_service.get_catia_v5_application", return_value=app):
            for reserved in ("Filename", "_internal"):
                with self.subTest(reserved=reserved), self.assertRaises(ValueError):
                    bom_service.edit_bom([], custom_columns=[reserved])

    def test_product_tree_read_errors_are_not_silently_treated_as_leaf(self):
        root = _Product("ROOT", "ROOT", filepath="C:/cad/ROOT.CATProduct")
        root.Products = _Collection([None], error=RuntimeError("COM collection failure"))
        doc = _Document("ROOT.CATProduct", root)
        app = _Application([doc], doc)
        with patch("catia_copilot.catia.bom_service.get_catia_v5_application", return_value=app):
            with self.assertRaisesRegex(ValueError, "Product tree.*0"):
                bom_service.collect_bom()

    def test_embedded_components_under_distinct_hosts_do_not_collide_by_part_number(self):
        component_a = _Product("CMP", "CMP.1", filepath="C:/cad/A.CATProduct")
        component_b = _Product("CMP", "CMP.1", filepath="C:/cad/B.CATProduct")
        host_a = _Product("A", "A.1", [component_a], "C:/cad/A.CATProduct")
        host_b = _Product("B", "B.1", [component_b], "C:/cad/B.CATProduct")
        root = _Product("ROOT", "ROOT", [host_a, host_b], "C:/cad/ROOT.CATProduct")
        doc = _Document("ROOT.CATProduct", root)
        app = _Application([doc], doc)
        with patch("catia_copilot.catia.bom_service.get_catia_v5_application", return_value=app):
            result = bom_service.collect_bom(view="full")
        component_rows = [row for row in result["rows"] if row["Part Number"] == "CMP"]
        self.assertEqual(len(component_rows), 2)

    def test_missing_or_ambiguous_document_is_reported(self):
        a = _Document("same.CATProduct", _Product("SAME"))
        b = _Document("same.CATProduct", _Product("SAME"))
        app = _Application([a, b], a)
        with patch("catia_copilot.catia.bom_service.get_catia_v5_application", return_value=app):
            with self.assertRaisesRegex(ValueError, "歧义"):
                bom_service.collect_bom(doc="same.CATProduct")
            with self.assertRaisesRegex(ValueError, "不存在"):
                bom_service.collect_bom(doc="missing")


class TestBomEditing(unittest.TestCase):
    def setUp(self):
        self.shared_ref = _Product("PN", filepath="C:/cad/pn.CATPart")
        self.first = _Product("PN", "PN.1", filepath="C:/cad/pn.CATPart")
        self.second = _Product("PN", "PN.2", filepath="C:/cad/pn.CATPart")
        self.first.ReferenceProduct = self.shared_ref
        self.second.ReferenceProduct = self.shared_ref
        self.root = _Product("ROOT", children=[self.first, self.second])
        self.document = _Document("root.CATProduct", self.root)
        self.app = _Application([self.document], self.document)
        self.masters = {
            "ROOT": {"part_number": "ROOT", "filepath": "C:/cad/root.CATProduct", "revision": "", "instances": []},
            "PN": {"part_number": "PN", "filepath": "C:/cad/pn.CATPart", "revision": ""},
        }
        self.info_first = {"pn": "PN", "pm_key": "PN", "instance_name": self.first.Name,
                           "product": self.first}
        self.info_second = {"pn": "PN", "pm_key": "PN", "instance_name": self.second.Name,
                            "product": self.second}
        self.masters["ROOT"]["instances"] = [self.info_first, self.info_second]
        self.inst_index = {id(self.first): self.info_first, id(self.second): self.info_second}
        self.collect_patch = patch(
            "catia_copilot.catia.bom_service.collect_bom_part_masters",
            return_value=("ROOT", self.masters, self.inst_index),
        )
        self.collect_patch.start()
        self.addCleanup(self.collect_patch.stop)
        self.app_patch = patch("catia_copilot.catia.bom_service.get_catia_v5_application", return_value=self.app)
        self.app_patch.start()
        self.addCleanup(self.app_patch.stop)

    def test_instance_name_targets_only_selected_node(self):
        with patch("catia_copilot.catia.bom_service.write_cell", return_value=True) as writer:
            result = bom_service.edit_bom([
                {"node": "0.1", "scope": "instance", "column": "Instance Name", "value": "Chosen"}
            ])
        self.assertTrue(result["success"])
        self.assertIs(writer.call_args.args[0], self.second)

    def test_root_instance_name_is_rejected_and_empty_node_alias_is_invalid(self):
        with patch("catia_copilot.catia.bom_service.write_cell") as writer:
            result = bom_service.edit_bom([
                {"node": "0", "scope": "instance", "column": "Instance Name", "value": "RootName"},
                {"node": "", "scope": "instance", "column": "Instance Name", "value": "Alias"},
            ])
        self.assertEqual(result["failed_count"], 2)
        self.assertIn("根节点", result["results"][0]["error"])
        self.assertIn("node", result["results"][1]["error"])
        writer.assert_not_called()

    def test_repeated_shared_master_assignment_is_written_once(self):
        with patch("catia_copilot.catia.bom_service.write_cell", return_value=True) as writer:
            result = bom_service.edit_bom([
                {"node": "0.0", "scope": "master", "column": "Revision", "value": "B"},
                {"node": "0.1", "scope": "master", "column": "Revision", "value": "B"},
            ])
        self.assertEqual(result["applied_count"], 2)
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(self.masters["PN"]["revision"], "B")

    def test_readonly_and_unknown_master_columns_are_rejected(self):
        with patch("catia_copilot.catia.bom_service.write_cell") as writer:
            result = bom_service.edit_bom([
                {"node": "0.0", "scope": "master", "column": "Quantity", "value": "3"},
                {"node": "0.0", "scope": "master", "column": "Mystery", "value": "x"},
            ])
        self.assertFalse(result["success"])
        self.assertEqual(result["failed_count"], 2)
        writer.assert_not_called()

    def test_partial_failure_does_not_stop_later_edits(self):
        with patch("catia_copilot.catia.bom_service.write_cell", side_effect=[False, True]) as writer:
            result = bom_service.edit_bom([
                {"node": "0.0", "scope": "master", "column": "Revision", "value": "A"},
                {"node": "0.0", "scope": "master", "column": "Nomenclature", "value": "Part"},
            ])
        self.assertFalse(result["success"])
        self.assertEqual((result["applied_count"], result["failed_count"]), (1, 1))
        self.assertEqual(writer.call_count, 2)

    def test_failed_part_number_write_does_not_change_later_resolution(self):
        with patch("catia_copilot.catia.bom_service.rename_part_master", wraps=bom_service.rename_part_master) as rename, \
             patch("catia_copilot.catia.bom_service.write_cell", side_effect=[False, True]) as writer:
            result = bom_service.edit_bom([
                {"node": "0.0", "scope": "master", "column": "Part Number", "value": "NEW"},
                {"node": "0.0", "scope": "master", "column": "Revision", "value": "C"},
            ])
        self.assertEqual(result["failed_count"], 1)
        self.assertIs(writer.call_args_list[1].args[0], self.first)
        self.assertEqual(self.masters["PN"]["part_number"], "PN")
        self.assertEqual([item["pn"] for item in self.masters["ROOT"]["instances"]], ["PN", "PN"])
        self.assertEqual(rename.call_args.args[1], {"PN": [id(self.first), id(self.second)]})

    def test_part_number_conflict_is_rejected_before_com_write(self):
        with patch("catia_copilot.catia.bom_service.write_cell") as writer:
            result = bom_service.edit_bom([
                {"node": "0", "scope": "master", "column": "Part Number", "value": "PN"}
            ])
        self.assertEqual(result["failed_count"], 1)
        self.assertIn("冲突", result["results"][0]["error"])
        writer.assert_not_called()

    def test_failed_part_master_rename_stops_before_com_write(self):
        with patch("catia_copilot.catia.bom_service.rename_part_master", return_value=False), \
             patch("catia_copilot.catia.bom_service.write_cell") as writer:
            result = bom_service.edit_bom([
                {"node": "0.0", "scope": "master", "column": "Part Number", "value": "NEW"}
            ])
        self.assertEqual(result["failed_count"], 1)
        self.assertIn("缓存", result["results"][0]["error"])
        writer.assert_not_called()

    def test_same_part_number_in_different_files_is_ambiguous_for_master_edit(self):
        other = _Product("PN", "PN.3", filepath="C:/other/pn.CATPart")
        self.root.Products = _Collection([self.first, other])
        with patch("catia_copilot.catia.bom_service.write_cell") as writer:
            result = bom_service.edit_bom([
                {"node": "0.0", "scope": "master", "column": "Revision", "value": "B"}
            ])
        self.assertEqual(result["failed_count"], 1)
        self.assertIn("歧义", result["results"][0]["error"])
        writer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
