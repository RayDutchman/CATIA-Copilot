"""
plm-unified FastAPI 后端的 API 客户端。

接口与 PlmApiClient（DocDoku 客户端）完全兼容，可作为替换品传入 sync_bom_to_plm()。

关键差异（内部处理，调用方无感知）：
  - 认证：POST /api/auth/token（OAuth2 form-urlencoded）→ Bearer JWT
  - 端点路径：/api/parts/* 而非 /workspaces/{ws}/parts/*
  - 响应字段映射：latestVersion → version，checkoutUserId(UUID) → checkOutUser.login 等
  - workspace 参数保留（兼容旧调用签名），但内部改用 workspace_id（UUID）查询

使用方式（与 PlmApiClient 相同）：
    client = UnifiedPlmClient("http://localhost:8010")
    client.login("admin", "password")
    client.ensure_part_template("Workspace_0")    # → None，无需模板
    pn, ver, iter = client.create_part("Workspace_0", "PART-001", "名称", "描述")
    client.checkout_part("Workspace_0", "PART-001", "A")   # → int 迭代号
    client.checkin_part("Workspace_0", "PART-001", "A")
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Optional

import requests

from catia_copilot.plm.api_client import PlmApiError

logger = logging.getLogger(__name__)


class UnifiedPlmClient:
    """plm-unified FastAPI 后端 HTTP 客户端。

    接口与 PlmApiClient 完全兼容，供 sync.py / plm_workbench.py 无缝切换后端。
    所有接受 workspace 参数的方法保留该参数（兼容旧调用签名），但内部通过
    workspace_id（UUID）与 plm-unified 通信。workspace_id 在 login() 时通过
    GET /api/workspaces?name=... 或 GET /api/workspaces 查找并缓存。
    """

    def __init__(self, base_url: str) -> None:
        """
        参数：
            base_url: plm-unified FastAPI 根地址，例如 "http://localhost:8010"
        """
        self._base = base_url.rstrip("/")
        self._login_name: str | None = None   # 登录用户名，供 checkout 持有者判断
        self._access_token: str | None = None
        self._workspace_id_cache: dict[str, str] = {}  # workspace name → UUID
        self._user_cache: dict[str, str] = {}           # user UUID → username（懒加载）

        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; CATIACopilot/1.0)",
        })

    # ── 认证 ─────────────────────────────────────────────────────────────────

    def login(self, login: str, password: str) -> None:
        """登录并保存 JWT，后续请求自动携带 Authorization: Bearer。"""
        resp = requests.post(
            f"{self._base}/api/auth/token",
            data={"username": login, "password": password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise PlmApiError(f"登录失败：{resp.text}", resp.status_code)
        body = resp.json()
        self._access_token = body["access_token"]
        self._login_name = login
        self._session.headers.update({"Authorization": f"Bearer {self._access_token}"})
        logger.info("UnifiedPlmClient 已登录为 %s", login)

    # ── 内部辅助 ─────────────────────────────────────────────────────────────

    def _ensure_logged_in(self) -> None:
        if not self._access_token:
            raise PlmApiError("尚未登录，请先调用 login()", 401)

    def _get(self, path: str, params: dict | None = None) -> Any:
        self._ensure_logged_in()
        resp = self._session.get(f"{self._base}{path}", params=params, timeout=30)
        if resp.status_code >= 400:
            raise PlmApiError(
                f"GET {path} 失败 ({resp.status_code}): {resp.text}",
                resp.status_code,
            )
        return resp.json()

    def _post(self, path: str, json: Any = None, params: dict | None = None) -> Any:
        self._ensure_logged_in()
        resp = self._session.post(
            f"{self._base}{path}", json=json, params=params, timeout=30
        )
        if resp.status_code >= 400:
            raise PlmApiError(
                f"POST {path} 失败 ({resp.status_code}): {resp.text}",
                resp.status_code,
            )
        return resp.json()

    def _put(self, path: str, json: Any = None, params: dict | None = None) -> Any:
        self._ensure_logged_in()
        resp = self._session.put(
            f"{self._base}{path}", json=json, params=params, timeout=30
        )
        if resp.status_code >= 400:
            raise PlmApiError(
                f"PUT {path} 失败 ({resp.status_code}): {resp.text}",
                resp.status_code,
            )
        return resp.json()

    def _get_workspace_id(self, workspace: str) -> str:
        """将 workspace 名称转换为 UUID，带本地缓存。"""
        if workspace in self._workspace_id_cache:
            return self._workspace_id_cache[workspace]
        # 获取工作空间列表，按名称匹配
        try:
            resp = self._get("/api/workspaces")
            # 兼容两种返回格式：list 或 {"workspaces": [...]}
            items = resp if isinstance(resp, list) else resp.get("workspaces", [])
            for item in items:
                if item.get("name") == workspace:
                    ws_id = str(item["id"])
                    self._workspace_id_cache[workspace] = ws_id
                    return ws_id
            logger.warning(
                "UnifiedPlmClient: workspace '%s' 未在列表中找到，降级使用名称作为 ID", workspace
            )
        except Exception as exc:
            logger.warning(
                "UnifiedPlmClient: 获取 workspace 列表失败（%s），降级使用名称作为 ID", exc
            )
        # 回退：直接把 workspace 当成 UUID 使用
        self._workspace_id_cache[workspace] = workspace
        return workspace

    def _resolve_username(self, user_id: str | None) -> str:
        """将用户 UUID 解析为 username，带本地缓存（懒加载）。"""
        if not user_id:
            return ""
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        try:
            data = self._get(f"/api/users/{user_id}")
            username = data.get("username") or data.get("realName") or str(user_id)
            self._user_cache[user_id] = username
            return username
        except Exception:
            # 查询失败时降级用 UUID 字符串
            self._user_cache[user_id] = str(user_id)
            return str(user_id)

    # ── 零件头信息 ────────────────────────────────────────────────────────────

    def get_part_head(self, workspace: str, part_number: str) -> dict:
        """获取零件最新版本的完整信息，响应字段兼容 DocDoku 格式。

        返回字段：number, name, version, lastIterationNumber,
                  checkOutUser, authorLogin, modificationDate,
                  lifecycleState, type, partIterations（简化）
        """
        ws_id = self._get_workspace_id(workspace)
        pn = urllib.parse.quote(part_number, safe="")
        data = self._get(f"/api/parts/{pn}", params={"workspace_id": ws_id})

        revisions = data.get("revisions") or []
        latest_rev = revisions[-1] if revisions else {}
        iterations = latest_rev.get("iterations") or []
        last_iter = iterations[-1] if iterations else {}

        # 签出人：UUID → username
        checkout_user_id = latest_rev.get("checkoutUserId") or data.get("checkoutUserId")
        checkout_login = self._resolve_username(checkout_user_id) if checkout_user_id else None

        # 作者：从最新版本的第一个迭代取
        first_iter = iterations[0] if iterations else {}
        author_id = first_iter.get("authorId") or latest_rev.get("authorId")
        author_login = self._resolve_username(author_id) if author_id else ""

        # 构建兼容旧 DocDoku 的响应结构
        return {
            "number":              data.get("number", part_number),
            "name":                data.get("name", ""),
            "version":             latest_rev.get("version", "A"),
            "lastIterationNumber": len(iterations),
            "checkOutUser":        {"login": checkout_login} if checkout_login else None,
            "authorLogin":         author_login,
            "modificationDate":    data.get("updatedAt", ""),
            "lifecycleState":      data.get("latestStatus", "WIP"),
            "type":                data.get("type", ""),
            # 简化版 partIterations，供 sync.py 兼容访问
            "partIterations": [
                {
                    "iteration":        it.get("iteration", i + 1),
                    "modificationDate": it.get("createdAt", ""),
                    "checkInDate":      it.get("checkInDate", ""),
                    "instanceAttributes": [],
                    "attachedFiles":    [],
                    "nativeCADFile":    None,
                    "components":       [],
                }
                for i, it in enumerate(iterations)
            ],
            # 原始数据透传，供特殊场景访问
            "_raw": data,
        }

    def get_latest_version(
        self, workspace: str, part_number: str
    ) -> tuple[str, str, int]:
        """获取零件最新版本和最新迭代号。

        返回：(零件号, 版本, 最新迭代号)  ← 与 PlmApiClient 完全一致的 3-tuple
        """
        result = self.get_part_head(workspace, part_number)
        return (
            part_number,
            result.get("version", "A"),
            int(result.get("lastIterationNumber", 1)),
        )

    # 兼容别名
    _get_latest_version = get_latest_version

    def get_part_detail(self, workspace: str, part_number: str, version: str) -> dict:
        """获取零件完整详情（兼容 api_client 同名方法）。"""
        return self.get_part_head(workspace, part_number)

    # ── 零件 CRUD ─────────────────────────────────────────────────────────────

    def create_part(
        self,
        workspace: str,
        part_number: str,
        name: str,
        description: str = "",
        template_id: str | None = None,
    ) -> tuple[str, str, int]:
        """创建零件（三层原子：master + revision A + iteration 1）。

        返回：(零件号, 版本, 迭代号) ← 与 PlmApiClient 完全一致

        plm-unified 创建后自动处于 WIP 状态，无需额外签出。
        """
        ws_id = self._get_workspace_id(workspace)
        payload: dict = {
            "number":      part_number,
            "name":        name or part_number,
            "workspaceId": ws_id,
        }
        if description:
            payload["description"] = description
        result = self._post("/api/parts", json=payload)

        # 从响应里取版本和迭代号
        revisions = result.get("revisions") or []
        latest_rev = revisions[-1] if revisions else {}
        iterations = latest_rev.get("iterations") or []
        version = latest_rev.get("version", "A")
        iteration = len(iterations) or 1
        logger.info("PLM 零件已创建：%s-%s iter%d", part_number, version, iteration)
        return part_number, version, iteration

    def checkout_part(self, workspace: str, part_number: str, version: str) -> int:
        """签出零件，返回新迭代号（int）← 与 PlmApiClient 完全一致。"""
        ws_id = self._get_workspace_id(workspace)
        pn = urllib.parse.quote(part_number, safe="")
        result = self._put(
            f"/api/parts/{pn}/{version}/checkout",
            params={"workspace_id": ws_id},
        )
        # plm-unified CheckoutResponse 里没有 lastIterationNumber，
        # 需要再次查询获取最新迭代号
        try:
            head = self.get_part_head(workspace, part_number)
            iteration = int(head.get("lastIterationNumber", 1))
        except Exception:
            iteration = 1
        logger.info("PLM 签出：%s-%s iter%d", part_number, version, iteration)
        return iteration

    def checkin_part(
        self,
        workspace: str,
        part_number: str,
        version: str,
        iteration_note: str = "",
    ) -> None:
        """签入零件版本。"""
        ws_id = self._get_workspace_id(workspace)
        pn = urllib.parse.quote(part_number, safe="")
        params: dict = {"workspace_id": ws_id}
        if iteration_note:
            params["iteration_note"] = iteration_note
        self._put(f"/api/parts/{pn}/{version}/checkin", params=params)
        logger.info("PLM 签入：%s-%s", part_number, version)

    def force_undo_checkout(
        self, workspace: str, part_number: str, version: str
    ) -> None:
        """强制撤销他人签出（plm-unified 暂不支持，抛 501）。"""
        raise PlmApiError("plm-unified 暂不支持强制撤销他人签出", 501)

    def delete_part(self, workspace: str, part_number: str, version: str) -> None:
        """删除零件（plm-unified 暂未实现，抛 501）。"""
        raise PlmApiError("plm-unified 暂不支持删除零件", 501)

    # ── 迭代内容 ──────────────────────────────────────────────────────────────

    def update_iteration(
        self,
        workspace: str,
        part_number: str,
        version: str,
        iteration: int,
        attr_values: dict[str, str] | None = None,
        components: list[dict] | None = None,
        iteration_note: str = "",
    ) -> None:
        """更新零件迭代的属性和子组件列表。

        参数签名与 PlmApiClient 一致：
            attr_values:  字段名→值 映射（plm-unified 暂存于 iterationNote，仅记录）
            components:   子组件列表，None 表示不更新装配关系
        """
        ws_id = self._get_workspace_id(workspace)
        pn = urllib.parse.quote(part_number, safe="")

        # plm-unified 的 IterationUpdateRequest：iterationNote + components
        body: dict = {"iterationNote": iteration_note or ""}

        if components is not None:
            # 将 DocDoku 格式的 components 转换为 plm-unified 格式
            body["components"] = [
                {
                    "componentNumber": (
                        c.get("componentNumber")
                        or (c.get("component") or {}).get("number", "")
                    ),
                    "amount":    c.get("amount", 1.0),
                    "unit":      c.get("unit"),
                    "optional":  c.get("optional", False),
                    "order":     c.get("order", 0),
                    "comment":   c.get("comment"),
                    "cadInstances": c.get("cadInstances") or [],
                }
                for c in components
            ]

        self._put(
            f"/api/parts/{pn}/{version}/iterations/{iteration}",
            json=body,
            params={"workspace_id": ws_id},
        )
        logger.debug("PLM 迭代更新：%s-%s iter%d", part_number, version, iteration)

    def update_part_tags(
        self,
        workspace: str,
        part_number: str,
        version: str,
        tags: list[str],
    ) -> None:
        """更新零件标签列表（全量替换）。"""
        ws_id = self._get_workspace_id(workspace)
        pn = urllib.parse.quote(part_number, safe="")
        # plm-unified 的 PUT /api/parts/{pn} 更新零件主数据
        self._put(
            f"/api/parts/{pn}",
            json={"tags": tags},
            params={"workspace_id": ws_id},
        )
        logger.debug("PLM 标签更新：%s-%s → %s", part_number, version, tags)

    # ── 文件上传 ──────────────────────────────────────────────────────────────

    def upload_step(
        self,
        workspace: str,
        part_number: str,
        version: str,
        iteration: int,
        step_path: str,
    ) -> None:
        """上传 STP 文件，触发 Kafka 转换任务。"""
        self._ensure_logged_in()
        ws_id = self._get_workspace_id(workspace)
        pn = urllib.parse.quote(part_number, safe="")
        import os
        filename = os.path.basename(step_path)
        with open(step_path, "rb") as f:
            resp = self._session.put(
                f"{self._base}/api/parts/{pn}/{version}/iterations/{iteration}/nativecad",
                files={"file": (filename, f)},
                params={"workspace_id": ws_id, "workspace_name": workspace},
                timeout=120,
            )
        if resp.status_code >= 400:
            raise PlmApiError(f"上传 STP 失败 ({resp.status_code}): {resp.text}", resp.status_code)
        logger.info("PLM STP 已上传：%s-%s iter%d", part_number, version, iteration)

    def upload_attached_file(
        self,
        workspace: str,
        part_number: str,
        version: str,
        iteration: int,
        file_path: str,
    ) -> None:
        """上传附件（暂不实现，静默跳过，不阻断主流程）。

        未来实现路径：POST /api/attachments/upload（multipart）
        """
        logger.warning(
            "upload_attached_file: plm-unified 附件上传暂未实现，已跳过 %s",
            file_path,
        )

    # ── 转换状态 ──────────────────────────────────────────────────────────────

    def get_conversion_status(
        self,
        workspace: str,
        part_number: str,
        version: str,
        iteration: int,
    ) -> dict:
        """查询 CAD 转换状态，返回 {pending, succeed, startDate, endDate}。"""
        ws_id = self._get_workspace_id(workspace)
        pn = urllib.parse.quote(part_number, safe="")
        return self._get(
            f"/api/parts/{pn}/{version}/iterations/{iteration}/conversion",
            params={"workspace_id": ws_id},
        )

    def retry_conversion(
        self,
        workspace: str,
        part_number: str,
        version: str,
        iteration: int,
    ) -> None:
        """触发重新转换（PUT conversion 端点）。"""
        ws_id = self._get_workspace_id(workspace)
        pn = urllib.parse.quote(part_number, safe="")
        self._put(
            f"/api/parts/{pn}/{version}/iterations/{iteration}/conversion",
            params={"workspace_id": ws_id},
        )

    # ── 查询/列举 ─────────────────────────────────────────────────────────────

    def list_parts(self, workspace: str, max_count: int = 500) -> list[dict]:
        """列出工作空间内所有零件。"""
        ws_id = self._get_workspace_id(workspace)
        return self._get("/api/parts", params={"workspace_id": ws_id, "limit": max_count}) or []

    def search_parts_summary(
        self,
        workspace: str,
        part_numbers: list[str],
        progress_callback=None,
    ) -> dict[str, dict | None]:
        """按零件号列表批量查询，返回 {pn: summary_dict | None}。"""
        result: dict[str, dict | None] = {}
        total = len(part_numbers)
        for i, pn in enumerate(part_numbers):
            if progress_callback:
                progress_callback(i, total)
            try:
                detail = self.get_part_head(workspace, pn)
                result[pn] = self.extract_part_summary(detail)
            except PlmApiError as exc:
                result[pn] = None if exc.status_code == 404 else None
            except Exception:
                result[pn] = None
        if progress_callback:
            progress_callback(total, total)
        return result

    def extract_part_summary(self, detail: dict) -> dict:
        """从 get_part_head 返回的 dict 提取扁平摘要，格式同 PlmApiClient。"""
        cout_raw = detail.get("checkOutUser")
        if isinstance(cout_raw, dict):
            checkout_user = cout_raw.get("login") or cout_raw.get("name") or ""
        else:
            checkout_user = str(cout_raw) if cout_raw else ""
        return {
            "number":             detail.get("number", ""),
            "version":            detail.get("version", ""),
            "lastIterationNumber": detail.get("lastIterationNumber", 0),
            "name":               detail.get("name", ""),
            "checkOutUser":       checkout_user,
            "authorLogin":        detail.get("authorLogin", ""),
            "modificationDate":   detail.get("modificationDate", ""),
            "lifecycleState":     detail.get("lifecycleState", ""),
            "type":               detail.get("type", ""),
            "tags":               detail.get("tags", []),
        }

    # ── 模板 / 产品（存根，兼容旧调用签名）────────────────────────────────────

    def ensure_part_template(self, workspace: str) -> None:
        """plm-unified 不需要零件模板，返回 None 兼容旧调用。"""
        return None

    def create_product(self, workspace: str, *args, **kwargs) -> None:
        """ConfigurationItem（plm-unified 暂不实现）。"""
        pass

    # ── 其他兼容方法 ──────────────────────────────────────────────────────────

    def list_users(self, workspace: str) -> list[dict]:
        """列出工作空间用户。"""
        ws_id = self._get_workspace_id(workspace)
        return self._get("/api/users", params={"workspace_id": ws_id}) or []

    def list_tags(self, workspace: str) -> list[dict]:
        """列出标签。"""
        ws_id = self._get_workspace_id(workspace)
        return self._get("/api/tags", params={"workspace_id": ws_id}) or []

    def list_products(self, workspace: str) -> list[dict]:
        """列出产品（plm-unified 暂无专用端点，返回空列表）。"""
        return []
