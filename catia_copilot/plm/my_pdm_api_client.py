"""
myPDM REST API 客户端。

仅使用标准库（urllib），不引入任何第三方依赖。

认证：JWT (HS256)，access_token 内存保存，refresh_token QSettings 持久化。
401 时自动使用 refresh_token 刷新，刷新失败则触发重新登录回调。

典型用法：
    client = MyPdmApiClient("https://192.168.1.x:8443/api")
    client.login("engineer1", "password")
    user = client.get_me()
    parts = client.list_parts()
"""
from __future__ import annotations

import json
import logging
import os as _os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from PySide6.QtCore import QSettings

from catia_copilot.plm.my_pdm_schemas import (
    TokenResponse,
    UserResponse,
    BomMatchResult,
    BomSyncResponse,
    CadNamingConfig,
    AttachmentResponse,
    PartCreateRequest,
    PartCreateResponse,
    has_permission,
)

logger = logging.getLogger(__name__)


class MyPdmApiError(Exception):
    """myPDM API 调用异常，携带 HTTP 状态码。"""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class MyPdmApiClient:
    """myPDM REST API 客户端。

    JWT 管理：
    - access_token 保存在实例内存中
    - refresh_token 通过 QSettings 持久化
    - 401 时自动尝试刷新，失败则回调 reauth_callback
    """

    _SETTINGS_ORG = "CATIACompanion"
    _SETTINGS_APP = "MyPdmAuth"

    def __init__(self, base_url: str):
        self._base = base_url.rstrip("/")
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._user: UserResponse | None = None
        self._reauth_callback: Callable[[], None] | None = None

        # 从 QSettings 恢复 refresh_token
        s = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
        saved_refresh = s.value("refresh_token")
        if saved_refresh:
            self._refresh_token = str(saved_refresh)

    def set_reauth_callback(self, callback: Callable[[], None]) -> None:
        """设置重新登录回调（当 refresh 也失败时触发）。"""
        self._reauth_callback = callback

    @property
    def is_authenticated(self) -> bool:
        return self._access_token is not None

    @property
    def current_user(self) -> UserResponse | None:
        return self._user

    # ── 内部辅助 ──────────────────────────────────────────────────────

    def _headers(self, extra: dict | None = None) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; CATIACopilot/2.1)",
        }
        if self._access_token:
            h["Authorization"] = f"Bearer {self._access_token}"
        if extra:
            h.update(extra)
        return h

    def _request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        expect_json: bool = True,
        extra_headers: dict | None = None,
        auto_retry: bool = True,
    ) -> Any:
        """发送 HTTP 请求，返回解析后的 JSON。

        参数：
            auto_retry: 遇到 401 是否自动尝试 refresh → 重试
        """
        url = self._base + path
        data = json.dumps(body).encode() if body is not None else None
        headers = self._headers(extra_headers)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        logger.debug(f"myPDM {method} {url}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if (expect_json and raw) else None
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode(errors="replace")
            except Exception:
                pass

            # 401 自动刷新重试
            if exc.code == 401 and auto_retry and self._refresh_token:
                logger.info("myPDM 收到 401，尝试刷新 token...")
                if self._do_refresh():
                    return self._request(
                        method, path, body,
                        expect_json=expect_json,
                        extra_headers=extra_headers,
                        auto_retry=False,
                    )
                else:
                    if self._reauth_callback:
                        self._reauth_callback()
                    raise MyPdmApiError("认证已过期，请重新登录", status_code=401) from exc

            if exc.code == 403:
                raise MyPdmApiError(
                    f"{method} {path} 失败 [403]：权限不足",
                    status_code=403,
                ) from exc
            raise MyPdmApiError(
                f"{method} {path} 失败 [{exc.code}]: {body_text[:200]}",
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise MyPdmApiError(f"网络错误（{exc.reason}）：{url}") from exc

    def _do_refresh(self) -> bool:
        """尝试用 refresh_token 获取新 token。成功返回 True。"""
        if not self._refresh_token:
            return False
        try:
            url = self._base + "/auth/refresh"
            body = json.dumps({"refresh_token": self._refresh_token}).encode()
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                result = json.loads(raw) if raw else {}
                token_resp = TokenResponse(
                    access_token=result.get("access_token", ""),
                    refresh_token=result.get("refresh_token", ""),
                    token_type=result.get("token_type", "bearer"),
                )
                if token_resp.access_token:
                    self._access_token = token_resp.access_token
                    self._refresh_token = token_resp.refresh_token
                    s = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
                    s.setValue("refresh_token", token_resp.refresh_token)
                    logger.info("myPDM token 刷新成功")
                    return True
        except Exception as exc:
            logger.warning(f"myPDM token 刷新失败: {exc}")
        return False

    # ── 认证 ──────────────────────────────────────────────────────────

    def login(self, username: str, password: str) -> UserResponse:
        """登录并获取 JWT token。返回当前用户信息。"""
        form_data = urllib.parse.urlencode({
            "username": username,
            "password": password,
        }).encode()

        url = self._base + "/auth/token"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        req = urllib.request.Request(url, data=form_data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                result = json.loads(raw) if raw else {}
                self._access_token = result.get("access_token", "")
                self._refresh_token = result.get("refresh_token", "")

                s = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
                s.setValue("refresh_token", self._refresh_token)

                self._user = self.get_me()
                logger.info(f"myPDM 登录成功：{self._user.real_name} ({self._user.role})")
                return self._user
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode(errors="replace")
            except Exception:
                pass
            if exc.code == 401:
                raise MyPdmApiError("用户名或密码错误", status_code=401) from exc
            raise MyPdmApiError(
                f"登录失败 [{exc.code}]: {body_text[:200]}",
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise MyPdmApiError(f"无法连接到服务器：{exc.reason}") from exc

    def logout(self) -> None:
        """清除认证状态。"""
        self._access_token = None
        self._refresh_token = None
        self._user = None
        s = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
        s.remove("refresh_token")

    # ── 用户信息 ──────────────────────────────────────────────────────

    def get_me(self) -> UserResponse:
        result = self._request("GET", "/auth/me") or {}
        return UserResponse(
            id=str(result.get("id", "")),
            username=str(result.get("username", "")),
            real_name=str(result.get("real_name", "")),
            role=str(result.get("role", "")),
            department=result.get("department"),
            phone=result.get("phone"),
            status=str(result.get("status", "active")),
            created_at=str(result.get("created_at", "")),
            updated_at=str(result.get("updated_at", "")),
        )

    # ── 健康检查 ──────────────────────────────────────────────────────

    def health(self) -> bool:
        """测试后端是否可达。返回 True 表示可达。"""
        try:
            url = self._base.replace("/api", "/health") if "/api" in self._base else self._base + "/health"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception:
            return False

    # ── 零件 CRUD ─────────────────────────────────────────────────────

    def list_parts(self) -> list[dict]:
        """获取零件列表。"""
        return self._request("GET", "/parts") or []

    def create_part(self, data: PartCreateRequest) -> PartCreateResponse:
        result = self._request("POST", "/parts", {
            "code": data.code,
            "name": data.name or data.code,
            "spec": data.spec,
            "type": data.type,
        }) or {}
        return PartCreateResponse(
            id=str(result.get("id", "")),
            code=str(result.get("code", data.code)),
            name=str(result.get("name", data.name)),
            version=str(result.get("version", "A")),
        )

    def get_part(self, part_id: str) -> dict:
        """获取零件详情。"""
        return self._request("GET", f"/parts/{urllib.parse.quote(str(part_id))}") or {}

    def update_part(self, part_id: str, data: dict) -> dict:
        """更新零件信息。"""
        return self._request("PUT", f"/parts/{urllib.parse.quote(str(part_id))}", data) or {}

    # ── 签出/签入 ─────────────────────────────────────────────────────

    def checkout(self, revision_id: str) -> dict:
        """签出零件版本。"""
        rid = urllib.parse.quote(str(revision_id))
        return self._request("POST", f"/parts/revisions/{rid}/checkout") or {}

    def checkin(self, revision_id: str) -> None:
        """签入零件版本。"""
        rid = urllib.parse.quote(str(revision_id))
        self._request("POST", f"/parts/revisions/{rid}/checkin", expect_json=False)

    def undocheckout(self, revision_id: str) -> None:
        """撤销签出。"""
        rid = urllib.parse.quote(str(revision_id))
        self._request("POST", f"/parts/revisions/{rid}/undocheckout", expect_json=False)

    # ── CAD BOM 匹配与同步 ────────────────────────────────────────────

    def cad_bom_match(self, items: list[dict]) -> list[BomMatchResult]:
        """批量匹配 CAD BOM 项到 PDM 零部件。

        参数：
            items: [{"code": str, "version": str | None}, ...]
        返回：
            BomMatchResult 列表
        """
        result = self._request("POST", "/parts/cad/bom-match", {"items": items}) or {}
        raw_results = result.get("results", [])
        return [
            BomMatchResult(
                code=str(r.get("code", "")),
                version=str(r.get("version", "")),
                match_status=str(r.get("match_status", "unknown")),
                master_id=r.get("master_id"),
                revision_id=r.get("revision_id"),
                name=r.get("name"),
                spec=r.get("spec"),
                checkout_status=r.get("checkout_status"),
                checkout_user=r.get("checkout_user"),
                latest_version=r.get("latest_version"),
            )
            for r in raw_results
        ]

    def cad_bom_sync(self, revision_id: str, children: list[dict]) -> BomSyncResponse:
        """同步 CATIA 装配体的直接子项 BOM。

        参数：
            revision_id: 父零部件版本 ID
            children: [{"code": str, "name": str, "spec": str, "quantity": int, "instances": list}, ...]
        """
        rid = urllib.parse.quote(str(revision_id))
        result = self._request("POST", f"/parts/revisions/{rid}/cad/bom-sync", {"children": children}) or {}
        return BomSyncResponse(
            created=int(result.get("created", 0)),
            updated=int(result.get("updated", 0)),
            skipped=int(result.get("skipped", 0)),
            details=result.get("details", []),
        )

    # ── 附件 ──────────────────────────────────────────────────────────

    def list_attachments(self, revision_id: str, category: str | None = None) -> list[AttachmentResponse]:
        """获取版本附件列表。"""
        rid = urllib.parse.quote(str(revision_id))
        path = f"/parts/revisions/{rid}/attachments"
        if category:
            path += f"?category={urllib.parse.quote(category)}"
        results = self._request("GET", path) or []
        return [
            AttachmentResponse(
                id=str(r.get("id", "")),
                file_name=str(r.get("file_name", "")),
                file_size=int(r.get("file_size", 0)),
                category=str(r.get("category", "cad")),
            )
            for r in results
        ]

    def upload_attachment(
        self,
        revision_id: str,
        file_path: str,
        category: str = "cad",
        overwrite: bool = False,
    ) -> AttachmentResponse:
        """上传附件到零件版本（整包上传，适用于 <100MB 文件）。"""
        rid = urllib.parse.quote(str(revision_id))
        filename = _os.path.basename(file_path)
        boundary = "----MyPdmUploadBoundary9f2a7c41"

        with open(file_path, "rb") as f:
            file_data = f.read()

        body_parts = [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            file_data,
            b"\r\n",
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="category"\r\n\r\n'.encode(),
            f"{category}\r\n".encode(),
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="overwrite"\r\n\r\n'.encode(),
            f"{str(overwrite).lower()}\r\n".encode(),
            f"--{boundary}--\r\n".encode(),
        ]
        body = b"".join(body_parts)

        url = self._base + f"/parts/revisions/{rid}/attachments"
        headers = self._headers({
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        })
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        logger.debug(f"myPDM 附件上传：{filename} → revision={revision_id} category={category}")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
                result = json.loads(raw) if raw else {}
                return AttachmentResponse(
                    id=str(result.get("id", "")),
                    file_name=str(result.get("file_name", filename)),
                    file_size=int(result.get("file_size", len(file_data))),
                    category=category,
                )
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode(errors="replace")
            except Exception:
                pass
            raise MyPdmApiError(
                f"附件上传失败 [{exc.code}]: {body_text[:200]}",
                status_code=exc.code,
            ) from exc

    def delete_attachment(self, revision_id: str, attachment_id: str) -> None:
        """删除附件。"""
        rid = urllib.parse.quote(str(revision_id))
        aid = urllib.parse.quote(str(attachment_id))
        self._request("DELETE", f"/parts/revisions/{rid}/attachments/{aid}", expect_json=False)

    # ── 配置 ──────────────────────────────────────────────────────────

    def get_cad_naming(self) -> CadNamingConfig:
        """获取 CAD 附件命名前缀配置。"""
        result = self._request("GET", "/settings/cad-naming") or {}
        return CadNamingConfig(
            pdfPartPrefix=str(result.get("pdfPartPrefix", "")),
            pdfAssemblyPrefix=str(result.get("pdfAssemblyPrefix", "")),
            stpPrefix=str(result.get("stpPrefix", "")),
        )

    # ── 权限检查 ──────────────────────────────────────────────────────

    def can(self, permission: str) -> bool:
        """检查当前用户是否拥有某权限。"""
        if not self._user:
            return False
        return has_permission(self._user.role, permission)
