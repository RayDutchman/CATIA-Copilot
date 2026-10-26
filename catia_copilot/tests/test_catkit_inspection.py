"""CLI 新增对象操作的行为回归，不连接真实 CATIA。"""
from types import SimpleNamespace as NS
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from catia_copilot.catia import document_scope


class Collection:
    def __init__(self, *items):
        self.items = list(items)
        self.Count = len(items)

    def Item(self, index):
        return self.items[index - 1]


def document(name, path='', kind='PartDocument'):
    doc = NS(Name=name, FullName=path, Type=kind, Saved=True, ReadOnly=False,
             Product=NS(PartNumber=name), Activate=Mock(), SaveAs=Mock(), Close=Mock())
    if kind == 'PartDocument':
        doc.Part = NS(Name=name, Update=Mock())
    return doc


class TestTargets(unittest.TestCase):
    def setUp(self):
        self.a = document('A', r'C:\A.CATPart')
        self.b = document('B')
        self.app = NS(Documents=Collection(self.a, self.b), ActiveDocument=self.a)

    def test_resolve_unsaved_nonactive_without_activating(self):
        self.assertIs(document_scope.resolve_document(self.app, doc='B'), self.b)
        self.b.Activate.assert_not_called()

    def test_ambiguous_names_are_not_first_match(self):
        self.a.Name = 'B'
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            document_scope.resolve_document(self.app, doc='B')

    def test_target_conflict_and_type_mismatch(self):
        with self.assertRaisesRegex(ValueError, 'mutually exclusive'):
            document_scope.resolve_document(self.app, doc='B', active=True)
        with self.assertRaisesRegex(ValueError, 'ProductDocument'):
            document_scope.resolve_document(self.app, expected_type='ProductDocument')

    def test_missing_target_does_not_fallback_to_active(self):
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            document_scope.resolve_document(self.app, doc='missing')

    def test_empty_documents_report_failure(self):
        self.app.Documents = Collection()
        with self.assertRaisesRegex(ValueError, 'documents'):
            document_scope.resolve_document(self.app)

    def test_inaccessible_identity_fields_are_optional(self):
        class Unsaved:
            Name = 'Part1'
            @property
            def FullName(self):
                raise RuntimeError('unsaved')
        self.assertEqual(document_scope.describe_document(Unsaved()).key, 'Part1')


class TestDocumentOperations(unittest.TestCase):
    def setUp(self):
        from catia_copilot.catia import inspection_service
        self.service = inspection_service
        self.a = document('A', r'C:\A.CATPart')
        self.b = document('B', r'C:\B.CATPart')
        self.app = NS(Documents=Collection(self.a, self.b), ActiveDocument=self.a)
        p = patch.object(inspection_service, 'get_catia_v5_application', return_value=self.app)
        p.start()
        self.addCleanup(p.stop)

    def test_info_reads_explicit_document_without_activation(self):
        result = self.service.document_info(doc='B')
        self.assertEqual(result['name'], 'B')
        self.assertFalse(result['active'])
        self.assertEqual(result['type'], 'PartDocument')
        self.b.Activate.assert_not_called()

    def test_current_reads_active_identity(self):
        self.assertEqual(self.service.current_document()['key'], self.a.FullName)

    def test_actions_only_touch_target(self):
        self.service.activate_document(doc='B')
        self.b.Close.side_effect = lambda: setattr(self.app, 'Documents', Collection(self.a))
        self.service.close_document(doc='B')
        self.b.Activate.assert_called_once_with()
        self.b.Close.assert_called_once_with()
        self.a.Close.assert_not_called()

    def test_save_as_retains_source_and_verifies_new_path(self):
        def save(path):
            self.b.FullName = path
            Path(path).write_bytes(b'fixture')
        self.b.SaveAs.side_effect = save
        with tempfile.TemporaryDirectory() as temp, patch('os.remove') as remove:
            target = str((Path(temp) / 'Other.CATPart').resolve())
            result = self.service.save_document_as(target, doc='B')
            self.assertEqual(result['saved_path'], target)
            remove.assert_not_called()
        self.a.SaveAs.assert_not_called()

    def test_save_as_changed_full_name_without_file_is_not_success(self):
        self.b.SaveAs.side_effect = lambda path: setattr(self.b, 'FullName', path)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError, 'Save As'):
                self.service.save_document_as(str(Path(temp) / 'missing.CATPart'), doc='B')

    def test_cancelled_save_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, 'Save As'):
            self.service.save_document_as(r'C:\Other.CATPart', doc='B')

    def test_cancelled_close_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, 'Close'):
            self.service.close_document(doc='B')


class TestInspection(TestDocumentOperations):
    def test_update_uses_nonactive_part(self):
        self.service.update_part_document(doc='B')
        self.b.Part.Update.assert_called_once_with()
        self.a.Part.Update.assert_not_called()

    def test_mass_uses_target_product_not_active_document(self):
        from catia_copilot.catia import modeling
        analyze = NS(mass=2.0, get_gravity_center=lambda: (1, 2, 3), get_inertia=lambda: tuple(range(9)))
        with patch.object(modeling, 'wrap_product', return_value=NS(analyze=analyze)) as wrap:
            result = self.service.part_mass(doc='B')
        wrap.assert_called_once_with(self.b.Product)
        self.assertEqual(result['mass'], 2.0)
        self.assertEqual(result['units']['cog'], 'mm')
        self.assertEqual(result['units']['inertia'], 'kg*mm^2')

    def test_tree_reports_bodies_sketches_and_nested_sets(self):
        empty = Collection()
        inner = NS(Name='Nested', Bodies=empty, HybridBodies=empty, HybridShapes=Collection(NS(Name='Point.1')), HybridSketches=empty)
        outer = NS(Name='Geometry', Bodies=empty, HybridBodies=Collection(inner), HybridShapes=empty, HybridSketches=empty)
        body = NS(Name='Body.2', Shapes=Collection(NS(Name='Pad.1')), Sketches=Collection(NS(Name='Sketch.1')),
                  HybridBodies=empty, HybridShapes=empty, OrderedGeometricalSets=empty)
        self.b.Part.Bodies = Collection(body)
        self.b.Part.HybridBodies = Collection(outer)
        self.b.Part.OrderedGeometricalSets = empty
        result = self.service.part_tree(doc='B')
        self.assertEqual(result['tree']['children'][0]['children'][0]['name'], 'Pad.1')
        self.assertEqual(result['tree']['children'][1]['children'][0]['name'], 'Nested')
        self.assertTrue(result['complete'])


class TestProductOperations(TestDocumentOperations):
    def test_product_tree_reports_recursive_read_error(self):
        from catia_copilot.catia import assembly_reader
        self.b.FullName = r'C:\B.CATProduct'
        self.b.Product = NS(Name='Root')
        with patch.object(assembly_reader, 'get_bom_node_type', return_value=assembly_reader.BomNodeType.PRODUCT), \
             patch.object(assembly_reader, '_read_product_position', return_value=None):
            result = self.service.product_tree(doc='B')
        self.assertFalse(result['complete'])
        self.assertTrue(result['errors'])

    def test_drawing_unknown_link_is_an_error_not_empty_identity(self):
        links = NS(FirstLink=lambda: NS(), NextLink=lambda: None)
        view = NS(Name='Front', IsGenerative=lambda: True, GenerativeLinks=links, GenerativeBehavior=NS(Document=NS()))
        drawing = document('Drawing', r'C:\Drawing.CATDrawing', 'DrawingDocument')
        drawing.Sheets = Collection(NS(Name='Sheet.1', Views=Collection(view)))
        self.app.Documents = Collection(drawing)
        result = self.service.drawing_info(doc='Drawing')
        self.assertFalse(result['complete'])
        self.assertTrue(result['errors'])
        self.assertEqual(result['sheets'][0]['views'][0]['links'], [])

    def test_none_collections_are_empty_not_failed(self):
        self.b.Part.Bodies = None
        self.b.Part.HybridBodies = None
        self.b.Part.OrderedGeometricalSets = None
        self.assertTrue(self.service.part_tree(doc='B')['complete'])

    def test_remeasure_labels_actual_internal_units(self):
        with patch('catia_copilot.catia.mass_props_collect.remeasure_part_mass_props', return_value={'weight': 1}):
            result = self.service.remeasure_product_part(r'C:\B.CATPart')
        self.assertEqual(result['units'], {'weight': 'kg', 'cog': 'mm', 'inertia': 'kg*mm^2'})

    def test_product_tree_passes_explicit_document_and_selects_subtree(self):
        self.b.FullName = r'C:\B.CATProduct'
        del self.b.Part
        tree = {'path': '0', 'children': [{'path': '0.0', 'children': []}, {'path': '0.1', 'children': []}]}
        with patch('catia_copilot.catia.assembly_reader.read_assembly_tree', return_value=tree) as read:
            result = self.service.product_tree(doc='B', node='0.1')
        self.assertEqual(result['tree']['path'], '0.1')
        self.assertIs(read.call_args.kwargs['document'], self.b)

    def test_merge_mpd_roundtrip_and_overwrite(self):
        from catia_copilot.catia.mass_props_collect import save_rows, load_rows
        with tempfile.TemporaryDirectory() as temp:
            first, second, target = [Path(temp) / name for name in ('a.mpd', 'b.mpd', 'result.mpd')]
            save_rows([{'marker': 'a'}], str(first))
            save_rows([{'marker': 'b'}], str(second))
            self.service.merge_mass_files([str(first), str(second)], str(target))
            self.assertEqual([row['marker'] for row in load_rows(str(target))], ['a', 'b'])
            with self.assertRaises(FileExistsError):
                self.service.merge_mass_files([str(first)], str(target))

    def test_remeasure_reports_missing_measurement(self):
        with patch('catia_copilot.catia.mass_props_collect.remeasure_part_mass_props', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'measurement'):
                self.service.remeasure_product_part(r'C:\B.CATPart')

    def test_product_export_reuses_converter_and_records_partial_failure(self):
        with patch('catia_copilot.catia.conversion.convert_part_to_step', side_effect=[1, 0]) as convert:
            result = self.service.export_product_step([r'C:\A.CATProduct', r'C:\B.CATProduct'])
        self.assertEqual(result['exported_count'], 1)
        self.assertEqual(result['failed_count'], 1)
        self.assertFalse(result['success'])
        self.assertEqual(convert.call_count, 2)

    def test_product_export_rejects_node_and_wrong_file_type(self):
        with self.assertRaises(ValueError):
            self.service.export_product_step([r'C:\A.CATPart'])

    def test_tree_missing_collection_is_not_empty_success(self):
        result = self.service.part_tree(doc='B')
        self.assertFalse(result['complete'])
        self.assertTrue(result['errors'])

    def test_drawing_links_use_actual_link_objects(self):
        linked = document('Model', r'C:\Model.CATPart')
        links = NS(FirstLink=lambda: linked, NextLink=Mock(side_effect=RuntimeError('NextLink end fails on CATIA')))
        view = NS(Name='Front', IsGenerative=lambda: True, GenerativeLinks=links,
                  GenerativeBehavior=NS(Document=NS(Parent=linked)))
        drawing = document('Drawing', r'C:\Drawing.CATDrawing', 'DrawingDocument')
        drawing.Sheets = Collection(NS(Name='Sheet.1', Views=Collection(view)))
        self.app.Documents = Collection(drawing)
        result = self.service.drawing_info(doc='Drawing')
        self.assertEqual(result['sheets'][0]['views'][0]['links'][0]['full_name'], linked.FullName)
        self.assertTrue(result['complete'])


if __name__ == '__main__':
    unittest.main()
