"""
DocdokuPLM REST API 客户端。

仅使用标准库（urllib），不引入任何第三方依赖。

典型用法：
    client = PlmApiClient("http://localhost:8001/docdoku-plm-server-rest/api")
    client.login("admin", "password")
    tpl_id = client.ensure_part_template("Workspace_0")
    pn, version = client.create_part("Workspace_0", "PART-001", "描述", tpl_id)
    client.update_iteration("Workspace_0", pn, version, 1, {"材料": "铝合金", "重量": "1.23"}, [])
    client.checkin_part("Workspace_0", pn, version)
"""

import base64
import http.cookiejar
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


class PlmApiError(Exception):
    """DocdokuPLM API 调用异常，携带 HTTP 状态码（status_code）。"""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class PlmApiClient:
    """DocdokuPLM REST API 客户端（无状态，JWT 存储于实例）。"""

    # 零件属性模板 ID（固定）
    TEMPLATE_ID = "CATIA_Standard"

    # 模板字段定义：(字段名, 类型)
    # 类型：TEXT 或 NUMBER
    _TEMPLATE_ATTRS = [
        # CATIA 内置属性
        ("中文名称",  "TEXT"),
        ("版本",      "TEXT"),
        ("定义",      "TEXT"),
        ("来源",      "TEXT"),
        # 用户自定义属性
        ("零件类型",  "TEXT"),
        ("设计状态",  "TEXT"),
        ("材料",      "TEXT"),
        ("重量",      "NUMBER"),
        ("物料编码",  "TEXT"),
        ("存货类别",  "TEXT"),
        ("规格型号",  "TEXT"),
        ("备注",      "TEXT"),
    ]

    def __init__(self, base_url: str):
        """
        参数：
            base_url: DocdokuPLM REST API 根地址，例如
                      "http://localhost:8001/docdoku-plm-server-rest/api"
        """
        self._base = base_url.rstrip("/")
        self._token: str | None = None
        self._basic_auth: str | None = None   # base64(login:password)，Basic Auth 兜底
        self._login: str | None = None        # 登录用户名，用于判断 checkout 持有者

        # Cookie jar：自动接收 Set-Cookie（JSESSIONID 等）并在后续请求中回传
        self._cj = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cj)
        )

    # ── 内部辅助 ─────────────────────────────────────────────────────────────

    def _headers(self, extra: dict | None = None) -> dict:
        """构造请求头，优先附加 JWT Bearer，次选 Basic Auth。"""
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Cloudflare 等 CDN/WAF 会对 Python-urllib 默认 UA 返回 403（Bot Protection）
            # 使用通用 User-Agent 规避此拦截
            "User-Agent": "Mozilla/5.0 (compatible; CATIACopilot/1.0)",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        elif self._basic_auth:
            h["Authorization"] = f"Basic {self._basic_auth}"
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
        return_response_headers: bool = False,
    ) -> Any:
        """发送 HTTP 请求，返回解析后的 JSON（或 None）。

        参数：
            method:                 HTTP 方法（GET / POST / PUT / DELETE）
            path:                   相对于 base_url 的路径（以 / 开头）
            body:                   请求体，自动序列化为 JSON
            expect_json:            为 False 时不解析响应体，直接返回 None
            extra_headers:          额外请求头
            return_response_headers: 为 True 时返回 (data, headers_dict) 二元组
        """
        url = self._base + path
        data = json.dumps(body).encode() if body is not None else None
        headers = self._headers(extra_headers)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        logger.debug(f"PLM {method} {url}")
        try:
            with self._opener.open(req, timeout=30) as resp:
                raw = resp.read()
                resp_headers = dict(resp.headers)
                parsed = json.loads(raw) if (expect_json and raw) else None
                if return_response_headers:
                    return parsed, resp_headers
                return parsed
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode(errors="replace")
            except Exception:
                pass
            # 针对常见状态码给出中文说明，避免将 HTML 错误页暴露给用户
            if exc.code == 401:
                raise PlmApiError(
                    f"{method} {path} 失败 [401]：认证失败，请检查用户名/密码是否正确，"
                    f"或重新登录后重试（会话可能已过期）。",
                    status_code=401,
                ) from exc
            if exc.code == 403:
                raise PlmApiError(
                    f"{method} {path} 失败 [403]：权限不足，当前用户无权执行此操作。"
                    "请确认该用户在工作空间中的角色为管理员或贡献者。",
                    status_code=403,
                ) from exc
            raise PlmApiError(
                f"{method} {path} 失败 [{exc.code}]: {body_text[:200]}",
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise PlmApiError(f"网络错误（{exc.reason}）：{url}") from exc

    # ── 认证 ─────────────────────────────────────────────────────────────────

    def login(self, login: str, password: str) -> None:
        """登录并保存认证凭据。

        认证优先级：
        1. 若响应体包含 ``jwt``/``token`` 字段 → Bearer JWT
        2. 若响应头包含 ``Authorization`` / ``X-Auth-Token`` → Bearer JWT（来自响应头）
        3. 若 cookie jar 收到会话 cookie（JSESSIONID 等）→ 会话 cookie（自动处理）
        4. 上述均无 → 回落至 Basic Auth（base64 编码的 login:password）

        参数：
            login:    用户名
            password: 密码
        """
        result, resp_headers = self._request(
            "POST", "/auth/login",
            {"login": login, "password": password},
            return_response_headers=True,
        )
        # 登录成功后记录用户名，供后续判断 checkout 持有者
        self._login = login

        # 诊断：打印登录响应体和关键响应头，便于排查 401
        logger.debug(f"PLM 登录响应体：{result}")
        logger.debug(f"PLM 登录响应头：{ {k: v for k, v in (resp_headers or {}).items()} }")

        # 1. 响应头 `jwt:` —— DocdokuPLM Payara 版本的标准做法
        #    Access-Control-Expose-Headers 明确声明了 jwt 头
        jwt_header = (resp_headers or {}).get("jwt") or (resp_headers or {}).get("Jwt")
        if jwt_header:
            self._token = jwt_header.strip()
            logger.info("PLM 登录成功（JWT Bearer，来自响应头 jwt）")
            return

        # 2. 响应体 jwt/token 字段（部分旧版本）
        token = (result or {}).get("jwt") or (result or {}).get("token")
        if token:
            self._token = token
            logger.info("PLM 登录成功（JWT Bearer，来自响应体）")
            return

        # 3. 响应头 Authorization / X-Auth-Token
        auth_header = (resp_headers or {}).get("Authorization") or \
                      (resp_headers or {}).get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            self._token = auth_header[7:].strip()
            logger.info("PLM 登录成功（JWT Bearer，来自响应头 Authorization）")
            return
        xauth = (resp_headers or {}).get("X-Auth-Token") or \
                (resp_headers or {}).get("x-auth-token")
        if xauth:
            self._token = xauth.strip()
            logger.info("PLM 登录成功（JWT Bearer，来自响应头 X-Auth-Token）")
            return

        # 3. 检查 cookie jar 是否收到会话 cookie
        has_session_cookie = any(True for _ in self._cj)
        if has_session_cookie:
            cookies_info = [(c.name, c.domain) for c in self._cj]
            logger.info(f"PLM 登录成功（Cookie 会话）：{cookies_info}")
            return

        # 4. 兜底：使用 Basic Auth（每次请求带 Authorization: Basic）
        self._basic_auth = base64.b64encode(
            f"{login}:{password}".encode()
        ).decode()
        logger.info("PLM 登录成功（Basic Auth 兜底）")

    # ── 零件模板 ──────────────────────────────────────────────────────────────

    def ensure_part_template(self, workspace: str) -> str:
        """确保零件属性模板 CATIA_Standard 存在，不存在则创建。

        返回模板 ID（即 TEMPLATE_ID）。
        """
        # DocdokuPLM REST 端点：/workspaces/{ws}/part-templates
        path = f"/workspaces/{urllib.parse.quote(workspace)}/part-templates"
        # 检查是否已存在
        try:
            templates = self._request("GET", path) or []
            for tpl in templates:
                if tpl.get("id") == self.TEMPLATE_ID:
                    logger.debug(f"PLM 模板 '{self.TEMPLATE_ID}' 已存在")
                    return self.TEMPLATE_ID
        except PlmApiError:
            pass

        # 创建模板
        attr_templates = [
            {
                "name": name,
                "type": atype,
                "mandatory": False,
                "locked": False,
            }
            for name, atype in self._TEMPLATE_ATTRS
        ]
        self._request("POST", path, {
            "id": self.TEMPLATE_ID,
            "mask": "",
            "attributeTemplates": attr_templates,
        })
        logger.info(f"PLM 模板 '{self.TEMPLATE_ID}' 创建成功")
        return self.TEMPLATE_ID

    # ── 零件 CRUD ─────────────────────────────────────────────────────────────

    def create_part(
        self,
        workspace: str,
        part_number: str,
        name: str,
        description: str,
        template_id: str | None = None,
    ) -> tuple[str, str, int]:
        """创建零件，返回 (零件号, 版本, 迭代号)。

        新建后服务端自动将零件置于 checkout 状态（创建者持有），
        响应体含 lastIterationNumber（通常为 1）。

        若零件已存在（HTTP 400-不唯一），直接重新抛出 PlmApiError，
        由调用方（sync.py）按策略决定如何处理已存在零件，不在此处吞掉。

        参数：
            name:        零件名称（对应 Nomenclature；为空时回退到零件编号）
            description: 描述文本
            template_id: 零件模板 ID；传 None 时不携带 templateId 字段
        """
        ws = urllib.parse.quote(workspace)
        path = f"/workspaces/{ws}/parts"
        payload: dict = {
            "number":      part_number,
            "name":        name or part_number,
            "description": description,
        }
        if template_id is not None:
            payload["templateId"] = template_id
        result = self._request("POST", path, payload) or {}
        version   = result.get("version", "A")
        iteration = int(result.get("lastIterationNumber", 1))
        logger.info(f"PLM 零件已创建：{part_number}-{version} iter{iteration}")
        return part_number, version, iteration

    def _get_latest_version(
        self, workspace: str, part_number: str
    ) -> tuple[str, str, int]:
        """获取已存在零件的最新版本号和最新迭代号。

        返回：(零件号, 版本, 最新迭代号)
        DocdokuPLM 端点须带版本后缀：/parts/{pn}-{ver}
        实际上零件版本几乎都从 A 开始，直接尝试 -A。
        """
        ws = urllib.parse.quote(workspace)
        pn = urllib.parse.quote(part_number)
        # 尝试常见版本序列 A→B→C
        for ver in ("A", "B", "C"):
            try:
                result = self._request("GET", f"/workspaces/{ws}/parts/{pn}-{ver}") or {}
                version   = result.get("version", ver)
                iteration = result.get("lastIterationNumber", 1)
                return part_number, version, iteration
            except PlmApiError as exc:
                if exc.status_code == 404:
                    continue
                # PLM-06（ProductManagerBean:3509 NPE）已于 2026-05-20 服务端修复，
                # 此处不再对 500 静默跳过，直接抛出，避免掩盖真实服务端错误。
                raise
        raise PlmApiError(f"零件 {part_number} 不存在（A/B/C 均未找到）", 404)

    def update_iteration(
        self,
        workspace: str,
        part_number: str,
        version: str,
        iteration: int,
        attr_values: dict[str, str],
        components: list[dict],
    ) -> None:
        """更新零件迭代的属性和子组件列表。

        参数：
            attr_values:  字段名→值 映射，重量字段自动转 NUMBER 类型
            components:   子组件列表，每项为 {"component": {"number": ..., "version": ...}}
        """
        ws = urllib.parse.quote(workspace)
        pn = urllib.parse.quote(part_number)
        path = f"/workspaces/{ws}/parts/{pn}-{version}/iterations/{iteration}"

        # 构造 instanceAttributes
        number_fields = {name for name, atype in self._TEMPLATE_ATTRS if atype == "NUMBER"}
        instance_attrs = []
        for name, value in attr_values.items():
            atype = "NUMBER" if name in number_fields else "TEXT"
            instance_attrs.append({"type": atype, "name": name, "value": value})

        self._request("PUT", path, {
            "instanceAttributes": instance_attrs,
            "components": components,
        })
        logger.debug(f"PLM 属性更新：{part_number}-{version} iter{iteration}")

    def checkout_part(self, workspace: str, part_number: str, version: str) -> int:
        """签出（Checkout）零件，返回新迭代号（lastIterationNumber）。

        DocdokuPLM 端点：PUT /workspaces/{ws}/parts/{pn}-{ver}/checkout
        签出成功后服务端创建新迭代，响应体含 lastIterationNumber。
        """
        ws = urllib.parse.quote(workspace)
        pn = urllib.parse.quote(part_number)
        result = self._request(
            "PUT",
            f"/workspaces/{ws}/parts/{pn}-{version}/checkout",
        )
        iteration = (result or {}).get("lastIterationNumber", 1)
        logger.info(f"PLM 签出：{part_number}-{version} iter{iteration}")
        return int(iteration)

    def checkin_part(self, workspace: str, part_number: str, version: str) -> None:
        """Check In 零件（锁定当前迭代）。"""
        ws = urllib.parse.quote(workspace)
        pn = urllib.parse.quote(part_number)
        self._request(
            "PUT",
            f"/workspaces/{ws}/parts/{pn}-{version}/checkin",
            expect_json=False,
        )
        logger.debug(f"PLM Check In：{part_number}-{version}")

    def delete_part(self, workspace: str, part_number: str, version: str) -> None:
        """删除零件（需先 checkin，零件必须处于未签出状态）。

        DocdokuPLM 端点：DELETE /workspaces/{ws}/parts/{pn}-{ver}
        主要用于"不新建模式"下探测创建后的清理回滚。
        """
        ws = urllib.parse.quote(workspace)
        pn = urllib.parse.quote(part_number)
        self._request(
            "DELETE",
            f"/workspaces/{ws}/parts/{pn}-{version}",
            expect_json=False,
        )
        logger.debug(f"PLM 零件已删除（探测回滚）：{part_number}-{version}")

    def force_undo_checkout(
        self, workspace: str, part_number: str, version: str
    ) -> None:
        """尝试撤销他人的 Checkout。

        DocdokuPLM 端点：PUT /workspaces/{ws}/parts/{pn}-{ver}/undocheckout

        已知限制（PLM-07）：
        - 此端点只能撤销当前用户自己的签出，admin 也无法撤销他人签出（返回 400）
        - iter=1（从未 checkin 过）时同样无法 undocheckout（返回 400）
        因此 FORCE_UNDO 策略在当前 DocdokuPLM 版本下实际无效，
        调用此方法几乎总会抛出 PlmApiError(400)，由 sync.py 捕获后降级为 SKIP。
        """
        ws = urllib.parse.quote(workspace)
        pn = urllib.parse.quote(part_number)
        self._request(
            "PUT",
            f"/workspaces/{ws}/parts/{pn}-{version}/undocheckout",
            expect_json=False,
        )
        logger.info(f"PLM 强制撤销签出：{part_number}-{version}")

    def upload_step(
        self,
        workspace: str,
        part_number: str,
        version: str,
        iteration: int,
        step_path: str,
    ) -> None:
        """将 STEP 文件作为 CAD 几何文件上传到零件迭代（触发 PLM 自动转换 obj）。

        端点：POST /files/{ws}/parts/{pn}/{ver}/{iter}/nativecad
        源码：PartBinaryResource.uploadNativeCADFile（@POST @Path("/{iter}/nativecad")）
        PLM 会自动触发格式转换生成 obj 用于三维预览。

        参数：
            step_path: 本地 .stp 文件的绝对路径
        """
        ws  = urllib.parse.quote(workspace,   safe="")
        pn  = urllib.parse.quote(part_number, safe="")
        filename = os.path.basename(step_path)
        fn_enc   = urllib.parse.quote(filename, safe="")
        # 源码：PartBinaryResource.uploadNativeCADFile
        # 路径常量：PartIteration.NATIVE_CAD_SUBTYPE = "nativecad"
        # 方法：POST，multipart/form-data
        url = (
            f"{self._base}/files/{ws}/parts/{pn}/{version}"
            f"/{iteration}/nativecad"
        )

        boundary = "----PlmGeomBoundaryA3c8d2e1"
        with open(step_path, "rb") as f:
            file_data = f.read()

        body_parts = [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="upload"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            file_data,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        body = b"".join(body_parts)

        headers = self._headers({
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        })

        # POST（源码 @POST 注解，与 upload_attached_file 一致）
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        logger.debug(f"PLM CAD 文件上传：{filename} → {part_number}-{version} iter{iteration}  URL={url}")
        try:
            with self._opener.open(req, timeout=120):
                pass
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode(errors="replace")
            except Exception:
                pass
            # 完整记录响应体，便于诊断端点/字段错误
            logger.warning(
                f"PLM CAD 文件上传失败 [{exc.code}]  URL={url}  "
                f"响应体：{body_text[:500]}"
            )
            if exc.code == 401:
                raise PlmApiError(
                    "CAD 文件上传失败 [401]：认证失败，请重新登录后重试。",
                    status_code=401,
                ) from exc
            if exc.code == 403:
                raise PlmApiError(
                    "CAD 文件上传失败 [403]：权限不足，当前用户无权上传文件。"
                    "请确认工作空间角色为管理员或贡献者。",
                    status_code=403,
                ) from exc
            raise PlmApiError(
                f"CAD 文件上传失败 [{exc.code}]: {body_text[:200]}",
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise PlmApiError(f"网络错误（{exc.reason}）：{url}") from exc
        logger.info(f"PLM CAD 文件上传成功：{filename}")


    def get_conversion_status(
        self,
        workspace: str,
        part_number: str,
        version: str,
        iteration: int,
    ) -> dict:
        """查询零件迭代的 CAD 转换状态。

        端点：GET /workspaces/{ws}/parts/{pn}-{ver}/iterations/{iter}/conversion
        路径规则：partNumber 与 partVersion 用 '-' 连接（PartsResource @Path "{partNumber}-{partVersion}"）
        返回字典含 succeed(bool)、pending(bool)、startDate、endDate。
        若该迭代从未上传过 CAD 文件（无转换记录），服务端返回空 body 或 null，
        此时本方法返回 {"succeed": False, "pending": False}。
        """
        ws  = urllib.parse.quote(workspace,   safe="")
        pn  = urllib.parse.quote(part_number, safe="")
        url = (
            f"{self._base}/workspaces/{ws}/parts/{pn}-{version}"
            f"/iterations/{iteration}/conversion"
        )
        result = self._request("GET", url)
        return result if isinstance(result, dict) else {"succeed": False, "pending": False}


    def retry_conversion(
        self,
        workspace: str,
        part_number: str,
        version: str,
        iteration: int,
    ) -> None:
        """重新触发零件迭代的 CAD → OBJ 转换任务。

        端点：PUT /workspaces/{ws}/parts/{pn}-{ver}/iterations/{iter}/conversion
        路径规则：partNumber 与 partVersion 用 '-' 连接（PartsResource @Path "{partNumber}-{partVersion}"）
        源码：PartResource.retryConversion（@PUT @Path("/iterations/{iter}/conversion")）
        要求：零件必须处于 checked-out 状态，否则回调会丢弃结果。
        """
        ws  = urllib.parse.quote(workspace,   safe="")
        pn  = urllib.parse.quote(part_number, safe="")
        url = (
            f"{self._base}/workspaces/{ws}/parts/{pn}-{version}"
            f"/iterations/{iteration}/conversion"
        )
        self._request("PUT", url)


    def upload_attached_file(
        self,
        workspace: str,
        part_number: str,
        version: str,
        iteration: int,
        file_path: str,
    ) -> None:
        """将文件作为普通附件上传到零件迭代。

        端点：POST /files/{ws}/parts/{pn}/{ver}/{iter}/attachedfiles
        适用于 CATPart、CATProduct、STP 等所有附件（不触发 PLM 格式转换）。
        注意：此端点路径前缀为 /files/，与业务端点 /workspaces/ 不同。

        参数：
            file_path: 本地文件的绝对路径
        """
        ws  = urllib.parse.quote(workspace,   safe="")
        pn  = urllib.parse.quote(part_number, safe="")
        # 附件端点 base_url 同根，只需替换路径前缀
        # self._base 形如 http://host/docdoku-plm-server-rest/api
        # 附件端点为   http://host/docdoku-plm-server-rest/api/files/...
        url = (
            f"{self._base}/files/{ws}/parts/{pn}/{version}"
            f"/{iteration}/attachedfiles"
        )

        filename = os.path.basename(file_path)
        boundary = "----PlmAttachBoundary8e4d1f7a"
        with open(file_path, "rb") as f:
            file_data = f.read()

        body_parts = [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="upload"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            file_data,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        body = b"".join(body_parts)

        headers = self._headers({
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        })

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        logger.debug(f"PLM 附件上传：{filename} → {part_number}-{version} iter{iteration}")
        try:
            with self._opener.open(req, timeout=120):
                pass
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode(errors="replace")
            except Exception:
                pass
            if exc.code == 401:
                raise PlmApiError("附件上传失败 [401]：认证失败", status_code=401) from exc
            if exc.code == 403:
                raise PlmApiError("附件上传失败 [403]：权限不足", status_code=403) from exc
            raise PlmApiError(
                f"附件上传失败 [{exc.code}]: {body_text[:200]}",
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise PlmApiError(f"网络错误（{exc.reason}）：{url}") from exc
        logger.info(f"PLM 附件上传成功：{filename}")

    # ── 零件查询（批量/详情）────────────────────────────────────────────────

    def list_parts(self, workspace: str, max_count: int = 500) -> list[dict]:
        """拉取工作区全量零件列表。

        返回每项包含 number / version / lastIterationNumber / tags /
        checkOutUser 等字段的字典列表。用于增量同步前建立本地缓存。

        参数：
            max_count: 最多拉取条数（DocdokuPLM 分页参数 count），默认 500
        """
        ws = urllib.parse.quote(workspace)
        result = self._request(
            "GET",
            f"/workspaces/{ws}/parts?start=0&count={max_count}",
        )
        return result or []

    def get_part_detail(
        self, workspace: str, part_number: str, version: str
    ) -> dict:
        """获取零件完整详情（含 instanceAttributes / tags / checkOutUser）。

        返回原始响应字典，调用方自行解析所需字段。
        """
        ws = urllib.parse.quote(workspace)
        pn = urllib.parse.quote(part_number)
        return self._request("GET", f"/workspaces/{ws}/parts/{pn}-{version}") or {}

    # ── 标签（Tag）操作 ───────────────────────────────────────────────────────

    def list_tags(self, workspace: str) -> list[dict]:
        """获取工作区所有标签。

        返回列表，每项形如 {"id": "已归档", "label": "已归档", "workspaceId": "..."}
        """
        ws = urllib.parse.quote(workspace)
        return self._request("GET", f"/workspaces/{ws}/tags") or []

    def update_part_tags(
        self,
        workspace: str,
        part_number: str,
        version: str,
        tags: list[str],
    ) -> None:
        """更新零件的标签列表（全量替换）。

        DocdokuPLM 的 PUT /parts/{pn}-{ver} 会覆盖整个 PartRevision，
        因此必须先 GET 当前完整数据，将 tags 字段替换后再 PUT 回去。
        若零件处于 checkout 状态，tags 字段仍可写入（不需要额外 checkout）。

        参数：
            tags: 标签 ID 字符串列表，如 ["已归档", "紧急"]
        """
        ws = urllib.parse.quote(workspace)
        pn = urllib.parse.quote(part_number)
        path = f"/workspaces/{ws}/parts/{pn}-{version}"

        # 先 GET 当前数据，避免覆盖其他字段
        current = self._request("GET", path) or {}

        # 只替换 tags 字段，其余字段原样保留
        payload = {
            "number":      current.get("number", part_number),
            "name":        current.get("name", part_number),
            "description": current.get("description", ""),
            "tags":        tags,
        }
        self._request("PUT", path, payload, expect_json=False)
        logger.debug(f"PLM 标签更新：{part_number}-{version} → {tags}")

    # ── 产品（Product）操作 ───────────────────────────────────────────────────

    def list_products(self, workspace: str) -> list[dict]:
        """获取工作区所有产品（Product）配置。

        返回列表，每项包含 id / designItemNumber / designItemName / description 等。
        """
        ws = urllib.parse.quote(workspace)
        return self._request("GET", f"/workspaces/{ws}/products") or []

    def create_product(
        self,
        workspace: str,
        product_id: str,
        design_item_number: str,
        description: str = "",
    ) -> dict:
        """创建产品（Product）。

        DocdokuPLM 中 Product 是产品结构的根节点视图，绑定到某个零件版本。
        若同名产品已存在，服务端返回 400 或 409，此时抛出 PlmApiError。
        调用方应捕获并判断是否为"已存在"场景。

        参数：
            product_id:          产品 ID（唯一字符串，如 "MyAssembly_Prod"）
            design_item_number:  根零件号（对应顶层 CATProduct 的 Part Number）
            description:         产品说明文本
        """
        ws = urllib.parse.quote(workspace)
        payload = {
            "id":                 product_id,
            "description":        description,
            "designItemNumber":   design_item_number,
            "designItemVersion":  "A",
        }
        result = self._request("POST", f"/workspaces/{ws}/products", payload) or {}
        logger.info(f"PLM 产品已创建：{product_id}（根零件 {design_item_number}）")
        return result

    # ── 用户查询 ─────────────────────────────────────────────────────────────

    def list_users(self, workspace: str) -> list[dict]:
        """获取工作区用户列表。

        返回列表，每项形如 {"login": "alice", "name": "Alice Zhang", ...}
        """
        ws = urllib.parse.quote(workspace)
        return self._request("GET", f"/workspaces/{ws}/users") or []
