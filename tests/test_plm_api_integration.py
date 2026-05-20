"""
PLM REST API 集成测试
=====================
直接调用 DocdokuPLM HTTP 接口，模拟 sync.py 的完整同步流程。
不依赖 CATIA、PySide6 或任何 UI 组件。

运行方式（需 PLM 服务运行在 127.0.0.1:8001）：
    python -m pytest tests/test_plm_api_integration.py -v

测试用的零件编号均带 _plmtest_ 前缀，测试结束后自动清理。
"""

import urllib.parse
import threading
import pytest
import requests

# ── 配置 ──────────────────────────────────────────────────────────────────────

BASE_URL  = "http://127.0.0.1:8001/docdoku-plm-server-rest/api"
WORKSPACE = "Workspace_0"
ADMIN_LOGIN    = "admin"
ADMIN_PASSWORD = "password"
# test1 账号用于"他人 checkout"场景
OTHER_LOGIN    = "test1"
OTHER_PASSWORD = "password"

# 测试零件前缀，便于识别和清理
TEST_PREFIX = "_plmtest_"


# ── 辅助类 ────────────────────────────────────────────────────────────────────

class PlmSession:
    """轻量 PLM HTTP 会话，封装登录和常用请求。"""

    def __init__(self, login: str, password: str):
        self.login_name = login
        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/json"
        self._login(login, password)

    def _login(self, login: str, password: str):
        resp = self.session.post(
            f"{BASE_URL}/auth/login",
            json={"login": login, "password": password},
            timeout=10,
        )
        assert resp.status_code == 200, f"登录失败 {resp.status_code}: {resp.text[:200]}"
        token = resp.headers.get("jwt") or resp.headers.get("JWT")
        assert token, "响应头中没有 jwt 字段"
        self.session.headers["Authorization"] = f"Bearer {token}"

    def get(self, path: str, **kw) -> requests.Response:
        return self.session.get(f"{BASE_URL}{path}", timeout=10, **kw)

    def post(self, path: str, json=None, **kw) -> requests.Response:
        return self.session.post(f"{BASE_URL}{path}", json=json, timeout=10, **kw)

    def put(self, path: str, json=None, **kw) -> requests.Response:
        return self.session.put(f"{BASE_URL}{path}", json=json, timeout=10, **kw)

    def delete(self, path: str, **kw) -> requests.Response:
        return self.session.delete(f"{BASE_URL}{path}", timeout=10, **kw)

    # ── 零件操作封装 ──────────────────────────────────────────────────────────

    def ws_path(self) -> str:
        return f"/workspaces/{urllib.parse.quote(WORKSPACE)}"

    def part_path(self, pn: str, ver: str = "A") -> str:
        return f"{self.ws_path()}/parts/{urllib.parse.quote(pn)}-{ver}"

    def create_part(self, pn: str, name: str = "", desc: str = "") -> dict:
        resp = self.post(
            f"{self.ws_path()}/parts",
            json={"number": pn, "name": name or pn, "description": desc},
        )
        assert resp.status_code in (200, 201), \
            f"创建零件失败 {resp.status_code}: {resp.text[:300]}"
        return resp.json()

    def get_part(self, pn: str, ver: str = "A") -> requests.Response:
        return self.get(self.part_path(pn, ver))

    def checkout(self, pn: str, ver: str = "A") -> requests.Response:
        return self.put(f"{self.part_path(pn, ver)}/checkout")

    def checkin(self, pn: str, ver: str = "A") -> requests.Response:
        return self.put(f"{self.part_path(pn, ver)}/checkin")

    def undo_checkout(self, pn: str, ver: str = "A") -> requests.Response:
        return self.put(f"{self.part_path(pn, ver)}/undocheckout")

    def update_iteration(self, pn: str, ver: str, iteration: int,
                         attrs: list, components: list) -> requests.Response:
        path = f"{self.part_path(pn, ver)}/iterations/{iteration}"
        return self.put(path, json={
            "instanceAttributes": attrs,
            "components": components,
        })

    def delete_part(self, pn: str, ver: str = "A") -> None:
        """尽力删除（忽略错误）。先 checkin（释放锁）再删除。
        
        注：PLM 的 undocheckout 在迭代 1 时返回 400（无法撤销第一次迭代），
        必须用 checkin 来释放 checkout 状态。
        """
        try:
            # 先 checkin 以释放锁（undocheckout 在 iter=1 时不可用）
            self.checkin(pn, ver)
        except Exception:
            pass
        try:
            resp = self.delete(self.part_path(pn, ver))
            # 某些版本的 PLM 用 DELETE /parts/{pn} 而非 /parts/{pn}-{ver}
            if resp.status_code == 405:
                self.delete(
                    f"{self.ws_path()}/parts/{urllib.parse.quote(pn)}"
                )
        except Exception:
            pass


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def admin() -> PlmSession:
    """Admin 会话，整个模块复用。"""
    return PlmSession(ADMIN_LOGIN, ADMIN_PASSWORD)


@pytest.fixture(scope="module")
def other() -> PlmSession:
    """test1 会话，用于他人 checkout 场景。"""
    try:
        return PlmSession(OTHER_LOGIN, OTHER_PASSWORD)
    except AssertionError:
        pytest.skip(f"账号 {OTHER_LOGIN} 不可用，跳过相关测试")


@pytest.fixture(autouse=True)
def cleanup_test_parts(admin):
    """每个测试结束后删除所有 _plmtest_ 开头的零件。"""
    yield
    resp = admin.get(f"{admin.ws_path()}/parts?start=0&length=500")
    if resp.status_code != 200:
        return
    data = resp.json()
    parts = data if isinstance(data, list) else data.get("partRevisions", [])
    for p in parts:
        pn  = p.get("number", "")
        ver = p.get("version", "A")
        if pn.startswith(TEST_PREFIX):
            admin.delete_part(pn, ver)


# ── T01: 基础连通性 ───────────────────────────────────────────────────────────

class TestT01Connectivity:
    """T01: 登录和基础连通性测试。"""

    def test_login_success(self):
        """正确账密登录成功，响应头含 jwt。"""
        resp = requests.post(
            f"{BASE_URL}/auth/login",
            json={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD},
            timeout=10,
        )
        assert resp.status_code == 200
        assert "jwt" in resp.headers or "JWT" in resp.headers, \
            f"响应头中无 jwt 字段，headers={dict(resp.headers)}"

    def test_login_wrong_password(self):
        """错误密码返回 4xx，不返回 200。"""
        resp = requests.post(
            f"{BASE_URL}/auth/login",
            json={"login": ADMIN_LOGIN, "password": "wrongpassword_xyz"},
            timeout=10,
        )
        assert resp.status_code in (401, 403, 400), \
            f"预期 4xx，实际 {resp.status_code}"

    def test_workspace_accessible(self, admin):
        """登录后可访问工作区列表，且 Workspace_0 存在。"""
        resp = admin.get("/workspaces")
        assert resp.status_code == 200
        data = resp.json()
        all_ws = data.get("allWorkspaces", [])
        ids = [w["id"] for w in all_ws]
        assert WORKSPACE in ids, f"工作区 {WORKSPACE} 不存在，当前：{ids}"

    def test_unauthenticated_request_rejected(self):
        """无 token 请求返回 401。"""
        resp = requests.get(
            f"{BASE_URL}/workspaces/{WORKSPACE}/parts",
            timeout=10,
        )
        assert resp.status_code == 401, \
            f"预期 401，实际 {resp.status_code}"


# ── T02: 全新零件创建 ─────────────────────────────────────────────────────────

class TestT02CreatePart:
    """T02: 全新零件创建，验证响应结构。"""

    def test_create_simple_part(self, admin):
        """创建零件成功，响应含 version/lastIterationNumber/checkOutUser。"""
        pn = f"{TEST_PREFIX}simple_001"
        data = admin.create_part(pn, name="测试零件001", desc="自动化测试")

        assert data.get("number") == pn
        assert data.get("version") == "A", \
            f"版本应为 A，实际 {data.get('version')}"
        assert "lastIterationNumber" in data, "响应缺少 lastIterationNumber"
        # 新建零件由创建者 checkout，checkOutUser 应为创建者
        check_out_user = data.get("checkOutUser")
        assert check_out_user is not None, "新建零件 checkOutUser 不应为 null"
        assert check_out_user.get("login") == ADMIN_LOGIN, \
            f"checkOutUser.login 应为 {ADMIN_LOGIN}，实际 {check_out_user}"

    def test_create_part_checkoutuser_structure(self, admin):
        """验证 checkOutUser 是嵌套对象，不存在顶级 checkOutLogin 字段。"""
        pn = f"{TEST_PREFIX}struct_check"
        data = admin.create_part(pn)

        # 文档明确确认无顶级 checkOutLogin 字段
        assert "checkOutLogin" not in data, \
            "服务端不应有顶级 checkOutLogin 字段"
        cou = data.get("checkOutUser")
        assert isinstance(cou, dict), \
            f"checkOutUser 应为 dict，实际 {type(cou)}"
        assert "login" in cou, \
            f"checkOutUser 缺少 login 字段，实际 keys={list(cou.keys())}"

    def test_create_duplicate_part_returns_error(self, admin):
        """重复创建同一零件号返回 409 或 400。"""
        pn = f"{TEST_PREFIX}dup_test"
        admin.create_part(pn)
        resp = admin.post(
            f"{admin.ws_path()}/parts",
            json={"number": pn, "name": pn, "description": ""},
        )
        assert resp.status_code in (409, 400), \
            f"重复创建应返回 409/400，实际 {resp.status_code}: {resp.text[:200]}"

    def test_get_part_response_fields(self, admin):
        """GET /parts/{pn}-{ver} 响应体字段与 REST-API-Notes.md 对照。"""
        pn = f"{TEST_PREFIX}field_check"
        admin.create_part(pn)
        resp = admin.get_part(pn, "A")
        assert resp.status_code == 200
        data = resp.json()

        for field in ("number", "version", "lastIterationNumber",
                      "status", "workspaceId"):
            assert field in data, \
                f"响应缺少字段 {field}，实际 keys={list(data.keys())}"

        assert data["version"] == "A"
        assert data["workspaceId"] == WORKSPACE
        assert isinstance(data["lastIterationNumber"], int)


# ── T03: 重复同步幂等性 ───────────────────────────────────────────────────────

class TestT03Idempotency:
    """T03: checkout → update → checkin 完整流程，验证幂等性。"""

    def test_checkin_clears_checkoutuser(self, admin):
        """checkin 后 checkOutUser 变为 null。"""
        pn = f"{TEST_PREFIX}checkin_flow"
        data = admin.create_part(pn)
        ver = data.get("version", "A")
        iter_num = data.get("lastIterationNumber", 1)

        admin.update_iteration(pn, ver, iter_num, [], [])
        resp_ci = admin.checkin(pn, ver)
        assert resp_ci.status_code == 200, \
            f"checkin 失败 {resp_ci.status_code}: {resp_ci.text[:200]}"

        data_after = admin.get_part(pn, ver).json()
        assert data_after.get("checkOutUser") is None, \
            f"checkin 后 checkOutUser 应为 null，实际 {data_after.get('checkOutUser')}"

    def test_checkout_after_checkin_increments_iteration(self, admin):
        """checkin 后再次 checkout 成功，迭代号递增。"""
        pn = f"{TEST_PREFIX}recheckout"
        data = admin.create_part(pn)
        ver = data.get("version", "A")
        iter1 = data.get("lastIterationNumber", 1)

        admin.update_iteration(pn, ver, iter1, [], [])
        admin.checkin(pn, ver)

        resp_co = admin.checkout(pn, ver)
        assert resp_co.status_code == 200, \
            f"再次 checkout 失败 {resp_co.status_code}: {resp_co.text[:200]}"
        iter2 = resp_co.json().get("lastIterationNumber", 0)
        assert iter2 > iter1, \
            f"第二次 checkout 迭代号应递增：{iter1} → {iter2}"

    def test_double_checkout_returns_error(self, admin):
        """已经 checkout 的零件再次 checkout 应返回 4xx。"""
        pn = f"{TEST_PREFIX}double_co"
        data = admin.create_part(pn)  # 创建后自动 checkout
        ver = data.get("version", "A")

        resp = admin.checkout(pn, ver)
        assert resp.status_code >= 400, \
            f"重复 checkout 应返回 4xx，实际 {resp.status_code}: {resp.text[:200]}"

    def test_full_sync_cycle_twice(self, admin):
        """模拟两次完整同步：创建→checkin→checkout→update→checkin，迭代号递增。"""
        pn = f"{TEST_PREFIX}full_cycle"
        data = admin.create_part(pn)
        ver = data.get("version", "A")
        iter1 = data.get("lastIterationNumber", 1)

        # 第一次 checkin
        admin.update_iteration(pn, ver, iter1, [], [])
        admin.checkin(pn, ver)

        # 第二次同步
        resp_co = admin.checkout(pn, ver)
        iter2 = resp_co.json().get("lastIterationNumber", 0)
        admin.update_iteration(pn, ver, iter2, [], [])
        admin.checkin(pn, ver)

        final = admin.get_part(pn, ver).json()
        assert final.get("checkOutUser") is None, \
            "第二次 checkin 后应无 checkOutUser"
        assert final.get("lastIterationNumber") == iter2, \
            f"最终迭代号应为 {iter2}"


# ── T04: 含空格零件名 URL encode ─────────────────────────────────────────────

class TestT04SpaceInPartNumber:
    """T04: 含空格零件名的 URL encode 正确性。"""

    def test_create_part_with_space(self, admin):
        """含空格的零件号能成功创建。"""
        pn = f"{TEST_PREFIX}front wing test"
        data = admin.create_part(pn, name="含空格测试零件")
        assert data.get("number") == pn, \
            f"零件号应为 '{pn}'，实际 '{data.get('number')}'"

    def test_get_part_with_space_percent20_encoded(self, admin):
        """含空格零件名 GET 时 %20 encode 正确可取到零件。"""
        pn = f"{TEST_PREFIX}space part 002"
        admin.create_part(pn)

        encoded_pn = urllib.parse.quote(pn, safe='')
        resp = admin.get(f"{admin.ws_path()}/parts/{encoded_pn}-A")
        assert resp.status_code == 200, \
            f"含空格零件 GET 失败 {resp.status_code}: {resp.text[:200]}"
        assert resp.json().get("number") == pn

    def test_plus_sign_encoding_does_not_match_space(self, admin):
        """+ 号不应被服务端路径解析为空格（RFC 3986 路径段语义）。
        
        注：服务端 PLM-06 bug 导致找不到零件时触发 NPE 返回 500 而非 404，
        因此此测试允许 404 或 500，但不允许返回 200 且零件号匹配。
        """
        pn = f"{TEST_PREFIX}space part 003"
        admin.create_part(pn)

        wrong_encoded = pn.replace(" ", "+")
        resp = admin.get(f"{admin.ws_path()}/parts/{wrong_encoded}-A")
        if resp.status_code == 200:
            returned_pn = resp.json().get("number", "")
            assert returned_pn != pn, \
                "服务端不应将路径中的 + 号识别为空格"
        else:
            # 服务端 PLM-06 bug：找不到零件时可能返回 500 NPE 而非 404
            assert resp.status_code in (404, 500), \
                f"+ 号编码预期 404 或 500（PLM-06 NPE），实际 {resp.status_code}"

    def test_checkout_checkin_part_with_space(self, admin):
        """含空格零件名的 checkout/checkin 全流程可通过。"""
        pn = f"{TEST_PREFIX}wing final test"
        data = admin.create_part(pn)
        ver = data.get("version", "A")
        iter1 = data.get("lastIterationNumber", 1)

        admin.update_iteration(pn, ver, iter1, [], [])
        resp_ci = admin.checkin(pn, ver)
        assert resp_ci.status_code == 200, \
            f"含空格零件 checkin 失败 {resp_ci.status_code}: {resp_ci.text[:200]}"

        resp_co = admin.checkout(pn, ver)
        assert resp_co.status_code == 200, \
            f"含空格零件再次 checkout 失败 {resp_co.status_code}: {resp_co.text[:200]}"


# ── T05: checkOutUser 字段验证 ────────────────────────────────────────────────

class TestT05CheckOutUser:
    """T05: checkOutUser 字段结构及他人 checkout 场景。"""

    def test_unchecked_part_has_null_checkoutuser(self, admin):
        """checkin 后 checkOutUser 为 null，客户端代码 (x or {}).get('login') 返回 None。"""
        pn = f"{TEST_PREFIX}co_null_check"
        data = admin.create_part(pn)
        ver = data.get("version", "A")
        iter1 = data.get("lastIterationNumber", 1)
        admin.update_iteration(pn, ver, iter1, [], [])
        admin.checkin(pn, ver)

        d = admin.get_part(pn, ver).json()
        # 模拟 sync.py 中的读取方式
        login = (d.get("checkOutUser") or {}).get("login")
        assert login is None, \
            f"checkin 后 checkOutUser.login 应为 None，实际 '{login}'"

    def test_checked_out_by_self_has_login(self, admin):
        """自己 checkout 的零件，checkOutUser.login 等于自己的登录名。"""
        pn = f"{TEST_PREFIX}co_self_check"
        data = admin.create_part(pn)

        login = (data.get("checkOutUser") or {}).get("login")
        assert login == ADMIN_LOGIN, \
            f"checkOutUser.login 应为 '{ADMIN_LOGIN}'，实际 '{login}'"

    def test_checked_out_by_other_user(self, admin, other):
        """他人 checkout 的零件，查询到的 checkOutUser.login 是对方账号。"""
        pn = f"{TEST_PREFIX}co_other_check"
        data = other.create_part(pn, name="他人签出测试")
        ver = data.get("version", "A")

        resp = admin.get_part(pn, ver)
        assert resp.status_code == 200
        login = (resp.json().get("checkOutUser") or {}).get("login")
        assert login == OTHER_LOGIN, \
            f"他人 checkout 时 checkOutUser.login 应为 '{OTHER_LOGIN}'，实际 '{login}'"

    def test_other_checkout_blocks_self_checkout(self, admin, other):
        """他人 checkout 的零件，自己尝试 checkout 应返回 4xx。"""
        pn = f"{TEST_PREFIX}co_block_test"
        data = other.create_part(pn)
        ver = data.get("version", "A")

        resp = admin.checkout(pn, ver)
        assert resp.status_code in (400, 403, 409), \
            f"他人已 checkout 时应返回 4xx，实际 {resp.status_code}: {resp.text[:200]}"


# ── T06: 不存在零件返回 404 ───────────────────────────────────────────────────

class TestT06NotFound:
    """T06: 不存在的零件/版本行为，验证 _get_latest_version 逻辑。
    
    重要发现（PLM-06 扩展）：
    服务端 ProductManagerBean.isCheckoutByAnotherUser 存在全局性 NPE，
    导致所有 GET /parts/{pn}-{ver}（包括零件/版本不存在的情况）均返回 500，
    而非预期的 404。这影响 api_client._get_latest_version 的 404 continue 逻辑。
    本组测试将断言改为允许 404 或 500，并标注服务端 bug。
    """

    def test_get_nonexistent_part_returns_404_or_500(self, admin):
        """GET 不存在的零件号，服务端应返回 404；实际因 PLM-06 NPE 返回 500。"""
        pn = f"{TEST_PREFIX}does_not_exist_xyz"
        resp = admin.get_part(pn, "A")
        # PLM-06 bug：实际返回 500 NPE，记录为服务端问题
        assert resp.status_code in (404, 500), \
            f"不存在零件应返回 404（实际因 PLM-06 返回 500），实际 {resp.status_code}"
        if resp.status_code == 500:
            assert "NullPointerException" in resp.text or "isCheckoutByAnotherUser" in resp.text, \
                f"500 响应体应含 NPE 信息，实际：{resp.text[:200]}"

    def test_get_nonexistent_version_returns_404_or_500(self, admin):
        """零件号存在但版本不存在（如 -Z），服务端应返回 404；实际因 PLM-06 返回 500。"""
        pn = f"{TEST_PREFIX}no_version_z"
        admin.create_part(pn)

        resp = admin.get_part(pn, "Z")
        assert resp.status_code in (404, 500), \
            f"不存在版本应返回 404（实际因 PLM-06 返回 500），实际 {resp.status_code}"

    def test_version_a_exists_b_returns_404_or_500(self, admin):
        """模拟 _get_latest_version A→B→C 回退：A 存在时 B 应返回 404（实际返回 500）。
        
        这导致 api_client._get_latest_version 对 B/C 版本查询时会 raise 而非 continue，
        理论上不影响（只需 A 能查到），但需记录此 PLM bug。
        """
        pn = f"{TEST_PREFIX}version_fallback"
        data = admin.create_part(pn)
        assert data.get("version") == "A", \
            f"首次创建版本应为 A，实际 {data.get('version')}"

        assert admin.get_part(pn, "A").status_code == 200
        resp_b = admin.get_part(pn, "B")
        assert resp_b.status_code in (404, 500), \
            f"B 版本应返回 404（PLM-06 实际返回 500），实际 {resp_b.status_code}"


# ── T07: 属性更新 ────────────────────────────────────────────────────────────

class TestT07UpdateAttributes:
    """T07: instanceAttributes 写入验证。"""

    def test_update_text_attribute(self, admin):
        """TEXT 类型属性写入成功（HTTP 200）。"""
        pn = f"{TEST_PREFIX}attr_text"
        data = admin.create_part(pn)
        ver = data.get("version", "A")
        iter_num = data.get("lastIterationNumber", 1)

        attrs = [{"type": "TEXT", "name": "Nomenclature", "value": "测试零件名称"}]
        resp = admin.update_iteration(pn, ver, iter_num, attrs, [])
        assert resp.status_code == 200, \
            f"属性更新失败 {resp.status_code}: {resp.text[:300]}"

    def test_update_number_attribute(self, admin):
        """NUMBER 类型属性写入成功。"""
        pn = f"{TEST_PREFIX}attr_number"
        data = admin.create_part(pn)
        ver = data.get("version", "A")
        iter_num = data.get("lastIterationNumber", 1)

        attrs = [{"type": "NUMBER", "name": "Weight", "value": "1.23"}]
        resp = admin.update_iteration(pn, ver, iter_num, attrs, [])
        assert resp.status_code == 200, \
            f"NUMBER 属性更新失败 {resp.status_code}: {resp.text[:300]}"

    def test_update_with_child_component(self, admin):
        """写入子组件引用（components 字段），父零件可引用子零件。"""
        child_pn  = f"{TEST_PREFIX}child_comp"
        parent_pn = f"{TEST_PREFIX}parent_comp"

        # 先创建子零件并 checkin
        c_data = admin.create_part(child_pn)
        c_ver  = c_data.get("version", "A")
        c_iter = c_data.get("lastIterationNumber", 1)
        admin.update_iteration(child_pn, c_ver, c_iter, [], [])
        admin.checkin(child_pn, c_ver)

        # 创建父零件，写入子组件引用
        p_data = admin.create_part(parent_pn)
        p_ver  = p_data.get("version", "A")
        p_iter = p_data.get("lastIterationNumber", 1)

        components = [{"component": {"number": child_pn, "version": c_ver}}]
        resp = admin.update_iteration(parent_pn, p_ver, p_iter, [], components)
        assert resp.status_code == 200, \
            f"写入子组件引用失败 {resp.status_code}: {resp.text[:300]}"


# ── T08: 深层嵌套 BOM 后序同步 ───────────────────────────────────────────────

class TestT08NestedBOM:
    """T08: 3 层 BOM 后序遍历同步，验证父级引用正确。"""

    def test_three_level_bom_sync(self, admin):
        """
        BOM 结构：root → sub → leaf
        后序：leaf 先同步，root 最后。
        验证：每层 checkin 后 checkOutUser 为 null。
        """
        leaf_pn = f"{TEST_PREFIX}bom_leaf"
        sub_pn  = f"{TEST_PREFIX}bom_sub"
        root_pn = f"{TEST_PREFIX}bom_root"

        # leaf：创建 → update → checkin
        d = admin.create_part(leaf_pn)
        admin.update_iteration(leaf_pn, d["version"], d["lastIterationNumber"], [], [])
        admin.checkin(leaf_pn, d["version"])

        # sub：创建 → 引用 leaf → checkin
        d = admin.create_part(sub_pn)
        resp = admin.update_iteration(
            sub_pn, d["version"], d["lastIterationNumber"],
            [], [{"component": {"number": leaf_pn, "version": "A"}}]
        )
        assert resp.status_code == 200, f"sub update 失败: {resp.text[:200]}"
        admin.checkin(sub_pn, d["version"])

        # root：创建 → 引用 sub → checkin
        d = admin.create_part(root_pn)
        resp = admin.update_iteration(
            root_pn, d["version"], d["lastIterationNumber"],
            [], [{"component": {"number": sub_pn, "version": "A"}}]
        )
        assert resp.status_code == 200, f"root update 失败: {resp.text[:200]}"
        admin.checkin(root_pn, d["version"])

        # 验证所有层 checkin 成功（checkOutUser=null）
        for pn in (leaf_pn, sub_pn, root_pn):
            d = admin.get_part(pn, "A").json()
            assert (d.get("checkOutUser") is None), \
                f"{pn} 应已 checkin（checkOutUser=null），实际 {d.get('checkOutUser')}"

    def test_shared_part_referenced_by_two_parents(self, admin):
        """同一子零件被两个父零件引用，只存在一个 revision（A 版本，无 B 版本）。
        
        注：B 版本查询因 PLM-06 NPE 返回 500 而非 404，允许两者。
        """
        shared_pn  = f"{TEST_PREFIX}shared_child"
        parent1_pn = f"{TEST_PREFIX}parent_one"
        parent2_pn = f"{TEST_PREFIX}parent_two"

        d = admin.create_part(shared_pn)
        admin.update_iteration(shared_pn, d["version"], d["lastIterationNumber"], [], [])
        admin.checkin(shared_pn, d["version"])

        for parent_pn in (parent1_pn, parent2_pn):
            d = admin.create_part(parent_pn)
            resp = admin.update_iteration(
                parent_pn, d["version"], d["lastIterationNumber"],
                [], [{"component": {"number": shared_pn, "version": "A"}}]
            )
            assert resp.status_code == 200, \
                f"{parent_pn} 引用共享零件失败: {resp.text[:200]}"

        # 共享零件 A 版本存在，B 版本不存在（PLM-06 bug 导致返回 500 而非 404）
        assert admin.get_part(shared_pn, "A").status_code == 200
        resp_b = admin.get_part(shared_pn, "B")
        assert resp_b.status_code in (404, 500), \
            f"B 版本应返回 404/500，实际 {resp_b.status_code}"


# ── T09: 强制撤销他人签出 ─────────────────────────────────────────────────────

class TestT09ForceUndoCheckout:
    """T09: checkout 撤销行为测试。
    
    已知限制：admin 无法通过 PUT /undocheckout 撤销他人签出，
    服务端返回 400（"无法撤销……非自己签出的目录"）。
    这意味着 sync.py 的 FORCE_UNDO 策略在当前 PLM 配置下无效，
    需记录为 PLM 端权限配置问题。
    """

    def test_admin_cannot_undo_other_checkout(self, admin, other):
        """admin 尝试撤销 test1 的 checkout，返回 400（无权限）。
        
        这是新发现的限制：undocheckout 只能撤销自己的签出，
        即使是 admin 也不能强制撤销他人。
        """
        pn = f"{TEST_PREFIX}force_undo_test"
        data = other.create_part(pn)
        ver  = data.get("version", "A")

        # 确认 test1 的 checkout 有效
        login = (admin.get_part(pn, ver).json().get("checkOutUser") or {}).get("login")
        assert login == OTHER_LOGIN, f"应为 {OTHER_LOGIN} checkout，实际 {login}"

        # admin 尝试强制撤销 → 应返回 400（权限不足）
        resp_undo = admin.put(f"{admin.part_path(pn, ver)}/undocheckout")
        assert resp_undo.status_code == 400, \
            f"admin 撤销他人 checkout 应返回 400，实际 {resp_undo.status_code}: {resp_undo.text[:200]}"

    def test_user_can_release_own_checkout_via_checkin(self, other, admin):
        """用户可通过 checkin 释放自己的 checkout（undocheckout 在 iter=1 不可用）。
        
        发现：PLM 的 undocheckout 在第一次迭代（iter=1）时返回 400，
        必须用 checkin 来释放 checkout 状态。
        """
        pn = f"{TEST_PREFIX}self_undo_test"
        data = other.create_part(pn)
        ver  = data.get("version", "A")
        iter_num = data.get("lastIterationNumber", 1)

        # 用 checkin 释放（undocheckout 在 iter=1 返回 400）
        resp_ci = other.checkin(pn, ver)
        assert resp_ci.status_code == 200, \
            f"自己 checkin 失败 {resp_ci.status_code}: {resp_ci.text[:200]}"

        # 验证已释放
        login_after = (admin.get_part(pn, ver).json().get("checkOutUser") or {}).get("login")
        assert login_after is None, \
            f"checkin 后 checkOutUser 应为 null，实际 '{login_after}'"

    def test_after_checkin_other_can_checkout(self, admin, other):
        """test1 checkin 后，admin 可以 checkout。"""
        pn = f"{TEST_PREFIX}undo_then_co"
        data = other.create_part(pn)
        ver  = data.get("version", "A")
        iter_num = data.get("lastIterationNumber", 1)

        # test1 checkin 释放
        other.checkin(pn, ver)

        # admin 可以 checkout
        resp_co = admin.checkout(pn, ver)
        assert resp_co.status_code == 200, \
            f"test1 checkin 后 admin checkout 失败 {resp_co.status_code}: {resp_co.text[:200]}"


# ── T10: 并发请求稳定性 ──────────────────────────────────────────────────────

class TestT10Concurrency:
    """T10: 并发请求，服务端不崩溃，结果一致。"""

    def test_concurrent_part_creation(self, admin):
        """10 个线程并发创建不同零件号，全部成功。"""
        errors  = []
        created = []

        def create_one(idx):
            pn = f"{TEST_PREFIX}concurrent_{idx:03d}"
            try:
                resp = admin.post(
                    f"{admin.ws_path()}/parts",
                    json={"number": pn, "name": pn, "description": "并发测试"},
                )
                if resp.status_code in (200, 201):
                    created.append(pn)
                else:
                    errors.append(f"{pn}: {resp.status_code}")
            except Exception as e:
                errors.append(f"{pn}: {e}")

        threads = [threading.Thread(target=create_one, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert len(errors) == 0, f"并发创建出现错误：{errors}"
        assert len(created) == 10, f"预期创建 10 个，实际 {len(created)}"

    def test_concurrent_read_same_part(self, admin):
        """10 个线程并发读取同一零件，全部返回 200，结果一致。"""
        pn = f"{TEST_PREFIX}concurrent_read"
        data = admin.create_part(pn)
        ver  = data.get("version", "A")

        results = []
        errors  = []

        def read_one(_):
            try:
                resp = admin.get_part(pn, ver)
                results.append(resp.status_code)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=read_one, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert len(errors) == 0, f"并发读取出现异常：{errors}"
        assert all(s == 200 for s in results), \
            f"并发读取应全部 200，实际：{results}"
