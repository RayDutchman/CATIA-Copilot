"""PLM 结构化事件生产端测试（i18n Phase 3 Task 3.1）。

覆盖目标：
1. 所有影响 UI 结果/进度的状态均通过 SyncEvent 携带固定 code 与明确 PN，
   而不是让 UI 解析中文日志；
2. 终态 node_done / node_skip / node_fail 每 PN 至多一次（create / 本人签出 /
   他人签出跳过 均依靠 uploaded_pns 去重）；
3. update / checkin 失败时事件 code 正确（update-failed / checkin-failed）；
4. 零件号包含 "<" / "|" 时，event.part_number 完整保留（不按 "<" 截断）；
5. 文本回调与结构化回调同时可用，且文本输出与旧版逐字一致。

说明：使用 FakePlmClient 替身，所有上传选项关闭，避免触发 CATIA COM；
文件路径为空，避免签入后尝试写回 CATIA 属性。
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from catia_copilot.plm import sync  # noqa: E402
from catia_copilot.plm.api_client import PlmApiError  # noqa: E402


class _FakePlmClient:
    """极简 PLM 客户端替身：记录调用，并按配置模拟成功 / 失败路径。

    parts: dict[pn -> {"version", "checkOutUser" (str|None), "iteration"}]
    新建零件（不在 parts 中）create_part 成功；已存在零件抛 400 already exists。
    """

    def __init__(self, login: str = "alice") -> None:
        self._login = login
        self.calls: list[tuple] = []
        self.parts: dict = {}
        self.update_error_pns: set[str] = set()
        self.checkin_error_pns: set[str] = set()
        self.checkout_error_pns: set[str] = set()

    def _log(self, name: str, *args) -> None:
        self.calls.append((name, args))

    def ensure_part_template(self, workspace: str) -> str:
        self._log("ensure_part_template", workspace)
        return "TPL"

    def get_part_head(self, workspace: str, pn: str) -> dict:
        self._log("get_part_head", workspace, pn)
        p = self.parts.get(pn)
        if p is None:
            return {"version": "A", "checkOutUser": None,
                    "partIterations": [{"iteration": 1}]}
        return {
            "version": p.get("version", "A"),
            "checkOutUser": ({"login": p["checkOutUser"]} if p.get("checkOutUser") else None),
            "partIterations": [{"iteration": p.get("iteration", 1)}],
        }

    def get_latest_version(self, workspace: str, pn: str) -> tuple:
        self._log("get_latest_version", workspace, pn)
        p = self.parts.get(pn, {})
        return pn, p.get("version", "A"), p.get("iteration", 1)

    def create_part(self, workspace: str, pn: str, name: str, desc: str, tpl) -> tuple:
        self._log("create_part", workspace, pn, name, desc, tpl)
        if pn in self.parts:
            raise PlmApiError("Part already exists", 400)
        self.parts[pn] = {"version": "A", "checkOutUser": self._login, "iteration": 1}
        return pn, "A", 1

    def checkout_part(self, workspace: str, pn: str, version: str) -> int:
        self._log("checkout_part", workspace, pn, version)
        if pn in self.checkout_error_pns:
            raise PlmApiError("checkout boom", 500)
        self.parts[pn]["checkOutUser"] = self._login
        self.parts[pn]["iteration"] = self.parts[pn].get("iteration", 1) + 1
        return self.parts[pn]["iteration"]

    def checkin_part(self, workspace: str, pn: str, version: str) -> None:
        self._log("checkin_part", workspace, pn, version)
        if pn in self.checkin_error_pns:
            raise PlmApiError("checkin boom", 500)
        self.parts[pn]["checkOutUser"] = None

    def force_undo_checkout(self, workspace: str, pn: str, version: str) -> None:
        self._log("force_undo_checkout", workspace, pn, version)
        self.parts[pn]["checkOutUser"] = None

    def delete_part(self, workspace: str, pn: str, version: str) -> None:
        self._log("delete_part", workspace, pn, version)
        self.parts.pop(pn, None)

    def update_iteration(self, workspace: str, pn: str, version: str, iteration: int,
                         attrs: dict, components) -> None:
        self._log("update_iteration", workspace, pn, version, iteration, attrs, components)
        if pn in self.update_error_pns:
            raise PlmApiError("update boom", 500)

    def list_parts(self, workspace: str, max_count: int = 500) -> list[dict]:
        self._log("list_parts", workspace)
        return [{"number": pn} for pn in self.parts]


def _node(pn, attrs=None, children=None):
    """构造最小 BomNode：无文件路径、非部件节点，避免触发 COM/文件副作用。"""
    return sync.BomNode(part_number=pn, attrs=attrs or {}, children=list(children or []))


def _run(bom_root, client, options=None):
    events: list = []
    texts: list = []
    result = sync.sync_bom_to_plm(
        bom_root, client, "WS1",
        options=options if options is not None else sync.SyncOptions(incremental=False),
        progress_callback=texts.append,
        progress_callback_structured=events.append,
    )
    return result, events, texts


def _terminals(events):
    return [e for e in events if e.type in ("node_done", "node_skip", "node_fail")]


class TestPlmEventCodes(unittest.TestCase):
    def test_codes_are_ascii_and_no_delimiters(self) -> None:
        codes = [
            sync.CODE_SOURCE_CREATED, sync.CODE_SOURCE_UPDATED,
            sync.CODE_SOURCE_SKIPPED, sync.CODE_SOURCE_UNCHANGED, sync.CODE_SOURCE_FAILED,
            sync.CODE_UPDATE_WRITTEN, sync.CODE_UPDATE_UPDATE_FAILED,
            sync.CODE_UPDATE_UPLOADED, sync.CODE_UPDATE_UPLOAD_FAILED,
            sync.CODE_UPDATE_CONVERTING, sync.CODE_UPDATE_CONVERTED,
            sync.CODE_UPDATE_CONVERSION_FAILED,
            sync.CODE_CHECKIN_CHECKED_IN, sync.CODE_CHECKIN_CHECKIN_FAILED,
            sync.CODE_CHECKIN_RETAINED,
        ]
        for c in codes:
            self.assertTrue(c.isascii(), f"状态码必须为 ASCII：{c}")
            self.assertFalse(any(ch in c for ch in "<|>"), f"状态码不得含分隔符：{c}")


class TestPlmEventProduction(unittest.TestCase):
    def test_create_and_own_checkout_dedup_per_pn(self) -> None:
        client = _FakePlmClient(login="alice")
        client.parts["P-MINE"] = {"version": "A", "checkOutUser": "alice", "iteration": 1}
        root = _node("ASSY", children=[
            _node("P-NEW"), _node("P-NEW"),   # 新建去重
            _node("P-MINE"), _node("P-MINE"),  # 本人签出去重
        ])
        result, events, _ = _run(root, client)

        # 每 PN 至多一次终态事件
        by_pn: dict = {}
        for e in _terminals(events):
            by_pn.setdefault(e.part_number, []).append(e)
        for pn, es in by_pn.items():
            self.assertEqual(len(es), 1, f"{pn} 终态事件应去重：{es}")
        self.assertEqual(set(by_pn), {"ASSY", "P-NEW", "P-MINE"})

        self.assertEqual(result.created, 2)   # ASSY, P-NEW
        self.assertEqual(result.updated, 1)   # P-MINE（已签出-本人）
        self.assertEqual(result.skipped, 0)
        self.assertEqual(result.failed, 0)

        done = {pn: es[0] for pn, es in by_pn.items() if es[0].type == "node_done"}
        d_new = done["P-NEW"]
        self.assertEqual(d_new.source_code, sync.CODE_SOURCE_CREATED)
        self.assertEqual(d_new.update_code, sync.CODE_UPDATE_WRITTEN)
        self.assertEqual(d_new.checkin_code, sync.CODE_CHECKIN_CHECKED_IN)
        self.assertEqual(done["P-MINE"].source_code, sync.CODE_SOURCE_UPDATED)
        for e in by_pn.values():
            self.assertTrue(e[0].part_number, "终态事件必须携带明确 PN")

    def test_other_checkout_skip_dedup(self) -> None:
        client = _FakePlmClient(login="alice")
        client.parts["P-OTHER"] = {"version": "A", "checkOutUser": "bob", "iteration": 1}
        root = _node("ASSY", children=[_node("P-OTHER"), _node("P-OTHER")])
        result, events, _ = _run(root, client)

        skip = [e for e in events if e.type == "node_skip" and e.part_number == "P-OTHER"]
        self.assertEqual(len(skip), 1, "他人签出跳过事件每 PN 至多一次")
        self.assertEqual(skip[0].source_code, sync.CODE_SOURCE_SKIPPED)
        self.assertEqual(skip[0].source, "跳过-被@bob")
        self.assertEqual(result.skipped, 1)

    def test_update_failure_code(self) -> None:
        client = _FakePlmClient()
        client.parts["P-UPD"] = {"version": "A", "checkOutUser": None, "iteration": 1}
        client.update_error_pns = {"P-UPD"}
        result, events, _ = _run(_node("P-UPD"), client)

        done = [e for e in events if e.type == "node_done"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0].part_number, "P-UPD")
        self.assertEqual(done[0].update, "✗ 更新失败")
        self.assertEqual(done[0].update_code, sync.CODE_UPDATE_UPDATE_FAILED)
        self.assertEqual(done[0].checkin_code, sync.CODE_CHECKIN_CHECKED_IN)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.updated, 0)

    def test_checkin_failure_code(self) -> None:
        client = _FakePlmClient()
        client.parts["P-CHK"] = {"version": "A", "checkOutUser": None, "iteration": 1}
        client.checkin_error_pns = {"P-CHK"}
        result, events, _ = _run(_node("P-CHK"), client)

        done = [e for e in events if e.type == "node_done"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0].part_number, "P-CHK")
        self.assertEqual(done[0].checkin, "✗ 签入失败")
        self.assertEqual(done[0].checkin_code, sync.CODE_CHECKIN_CHECKIN_FAILED)
        self.assertEqual(done[0].update_code, sync.CODE_UPDATE_WRITTEN)

    def test_part_number_with_special_chars_kept(self) -> None:
        pn = "ABC<DISK|HDD"
        client = _FakePlmClient()
        root = _node(pn, attrs={"Nomenclature": "排风<A|B"})
        result, events, _ = _run(root, client)

        done = [e for e in events if e.type == "node_done"]
        self.assertEqual(len(done), 1)
        # 结构化事件必须携带完整 PN，不按 "<" 截断（文本解析遗留问题）
        self.assertEqual(done[0].part_number, pn)
        self.assertEqual(result.created, 1)
        self.assertEqual(result.failed, 0)


class TestPlmDualChannel(unittest.TestCase):
    def test_makecb_dual_channel(self) -> None:
        texts: list = []
        structs: list = []
        cb = sync._makecb(texts.append, structs.append)

        line = sync._log_row("新建", "属性已写入", "已签入", "P-Z")
        cb(sync.SyncEvent(
            type="node_done", part_number="P-Z",
            source="新建", update="属性已写入", checkin="已签入",
            source_code=sync.CODE_SOURCE_CREATED,
            update_code=sync.CODE_UPDATE_WRITTEN,
            checkin_code=sync.CODE_CHECKIN_CHECKED_IN,
            message=line, _text_line=line,
        ))
        cb("hello")

        # 文本回调收到与旧版逐字一致的日志行
        self.assertEqual(texts, [line, "hello"])
        # 结构化回调收到同源事件
        self.assertEqual([e.type for e in structs], ["node_done", "summary"])
        done = structs[0]
        self.assertEqual(done.part_number, "P-Z")
        self.assertEqual(done.source_code, "created")
        self.assertEqual(done.checkin_code, "checked-in")

    def test_real_sync_drives_both_channels(self) -> None:
        client = _FakePlmClient()
        result, events, texts = _run(_node("P-NEW"), client)

        done = [e for e in events if e.type == "node_done"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0].source_code, sync.CODE_SOURCE_CREATED)
        # 文本通道仍含旧式逐字终态行（Task 3.2 前 UI 文本解析仍依赖）
        self.assertIn(sync._log_row("新建", "属性已写入", "已签入", "P-NEW"), texts)
        self.assertEqual(result.created, 1)


if __name__ == "__main__":
    unittest.main()