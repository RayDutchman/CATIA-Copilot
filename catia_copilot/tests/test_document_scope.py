# -*- coding: utf-8 -*-
"""文档身份与建模运行边界的纯逻辑测试。"""

import unittest

from catia_copilot.catia.document_scope import describe_document, matches_document


class _Product:
    PartNumber = "PN-001"


class _Document:
    Name = "Sample.CATPart"
    FullName = r"D:\cad\Sample.CATPart"
    Type = "Part"
    Product = _Product()


class _UnsavedDocument:
    Name = "Part1.CATPart"
    FullName = ""
    Type = "Part"

    class Product:
        PartNumber = "TEMP-1"


class TestDocumentIdentity(unittest.TestCase):
    def test_saved_document_key_prefers_full_path(self):
        identity = describe_document(_Document())
        self.assertEqual(identity.key, r"D:\cad\Sample.CATPart")
        self.assertEqual(identity.part_number, "PN-001")
        self.assertTrue(matches_document(identity, identity.key))
        self.assertTrue(matches_document(identity, "PN-001"))
        self.assertTrue(matches_document(identity, "Sample.CATPart"))
        self.assertFalse(matches_document(identity, "Other.CATPart"))

    def test_unsaved_document_key_uses_name_and_part_number(self):
        identity = describe_document(_UnsavedDocument())
        self.assertEqual(identity.key, "Part1.CATPart|TEMP-1")
        self.assertTrue(matches_document(identity, identity.key))
        self.assertTrue(matches_document(identity, "TEMP-1"))
        self.assertTrue(matches_document(identity, None))

    def test_identity_is_serializable(self):
        identity = describe_document(_Document())
        self.assertEqual(identity.to_dict()["document_type"], "Part")
        self.assertNotIn("com_object", identity.to_dict())
