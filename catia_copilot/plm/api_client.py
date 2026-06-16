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
        ("中文名称",      "TEXT"),
        ("版本",          "TEXT"),
        ("定义",          "TEXT"),
        ("来源",          "TEXT"),
        # 用户自定义属性
        ("零件类型",      "TEXT"),
        ("设计状态",      "TEXT"),
        ("材料",          "TEXT"),
        ("重量",          "NUMBER"),
        ("物料编码",      "TEXT"),
        ("存货类别",      "TEXT"),
        ("规格型号",      "TEXT"),
        ("备注",          "TEXT"),
        # PLM 同步状态字段（由程序自动维护，工程师不手动编辑）
        ("PLM_Version",   "TEXT"),
        ("PLM_Iteration", "NUMBER"),
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
        result = self.get_part_head(workspace, part_number)
        return part_number, result.get("version", "A"), int(result.get("lastIterationNumber", 1))

    def get_part_head(self, workspace: str, part_number: str) -> dict:
        """获取零件最新版本的完整 PartRevision 响应字典。

        直接 GET /parts/{pn}-{ver}，依次尝试版本 A/B/C，返回第一个存在版本的完整响应。
        若零件不存在则抛 PlmApiError(404)。
        比 search_parts 更可靠：精确匹配，不受前缀/模糊匹配影响。
        """
        ws = urllib.parse.quote(workspace)
        pn = urllib.parse.quote(part_number)
        for ver in ("A", "B", "C"):
            try:
                result = self._request("GET", f"/workspaces/{ws}/parts/{pn}-{ver}") or {}
                return result
            except PlmApiError as exc:
                if exc.status_code == 404:
                    continue
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

    # ── 零件搜索 ─────────────────────────────────────────────────────────────

    def search_parts(
        self,
        workspace: str,
        number: str = "",
        q: str = "",
        fetch_head_only: bool = True,
        page_from: int = 0,
        size: int = 50,
    ) -> list[dict]:
        """按零件号或关键词搜索零件（DocdokuPLM 搜索端点）。

        端点：GET /workspaces/{ws}/parts/search
        参数：
            number:          按零件号精确/前缀搜索
            q:               通用关键词
            fetch_head_only: 仅返回最新版本（默认 True）
            page_from:       分页偏移
            size:            每页条数（默认 50）
        返回同 list_parts() 格式的列表。
        """
        ws     = urllib.parse.quote(workspace)
        params: dict[str, str] = {
            "fetchHeadOnly": str(fetch_head_only).lower(),
            "from":          str(page_from),
            "size":          str(size),
        }
        if number:
            params["number"] = number
        if q:
            params["q"] = q
        qs = urllib.parse.urlencode(params)
        return self._request("GET", f"/workspaces/{ws}/parts/search?{qs}") or []

    # ── 零件结构（BOM 树）查询 ────────────────────────────────────────────────

    def get_part_bom(
        self,
        workspace: str,
        product_id: str,
        config_spec: str = "wip",
        path: str = "-1",
    ) -> dict:
        """获取产品 BOM 树（递归子装配结构）。

        端点：GET /workspaces/{ws}/products/{ciId}/bom
        参数：
            product_id:  产品 ID（配置项 ID，通常等于顶层零件号）
            config_spec: 配置规格（"latest" / "wip"，默认 "wip" 显示未签入的 WIP）
            path:        路径（"-1" 表示从根开始）
        返回 ComponentDTO，含嵌套 components 数组。
        """
        ws  = urllib.parse.quote(workspace)
        pid = urllib.parse.quote(product_id)
        qs  = urllib.parse.urlencode({"configSpec": config_spec, "path": path})
        return self._request("GET", f"/workspaces/{ws}/products/{pid}/bom?{qs}") or {}

    def get_product_filter(
        self,
        workspace: str,
        product_id: str,
        config_spec: str = "wip",
        path: str = "-1",
        depth: int = -1,
        link_type: str = "",
        diverge: bool = False,
    ) -> dict:
        """获取产品结构过滤后的嵌套 BOM 树（ComponentDTO 递归树）。

        端点：GET /workspaces/{ws}/products/{ciId}/filter
        参数：
            product_id:  产品 ID（配置项 ID）
            config_spec: 配置规格，"wip"（含未签入）或 "latest"（最新已签入）
            path:        起始路径，"-1" 表示从根开始
            depth:       展开深度，-1 表示完全展开（所有层级）
            link_type:   链接类型过滤（空字符串表示不过滤）
            diverge:     是否展开发散节点
        返回 ComponentDTO（嵌套结构）：
            {
              "partNumber": str,
              "partVersion": str,
              "partIteration": int,
              "checkOutUser": dict | null,
              "components": [ComponentDTO, ...],   ← 递归子节点
              "cadInstances": [...],
              ...
            }
        """
        ws  = urllib.parse.quote(workspace)
        pid = urllib.parse.quote(product_id)
        params: dict[str, str] = {
            "configSpec": config_spec,
            "path":       path,
            "depth":      str(depth),
            "diverge":    str(diverge).lower(),
        }
        if link_type:
            params["linkType"] = link_type
        qs = urllib.parse.urlencode(params)
        return self._request("GET", f"/workspaces/{ws}/products/{pid}/filter?{qs}") or {}

    def get_products_by_part(
        self,
        workspace: str,
        part_number: str,
    ) -> list[dict]:
        """获取以指定零件号为根的所有产品（ConfigurationItem）。

        通过 list_products 获取全量产品列表，过滤出 designItemNumber 匹配的项。
        返回列表，每项含 id / designItemNumber / description 等字段。
        注意：PLM 前端创建 Product 时 id 可能与 designItemNumber 不同，
        此方法同时按两个字段匹配。
        """
        all_products = self.list_products(workspace)
        pn_lower = part_number.strip().lower()
        result = []
        for p in all_products:
            din = str(p.get("designItemNumber") or "").lower()
            pid = str(p.get("id") or "").lower()
            if din == pn_lower or pid == pn_lower:
                result.append(p)
        return result

    def get_part_components_flat(
        self,
        workspace: str,
        part_number: str,
        version: str = "A",
        max_depth: int = 20,
        _depth: int = 0,
        _seen: set | None = None,
    ) -> list[dict]:
        """递归拼装 Part BOM 树，返回扁平行列表（含层级信息）。

        DocdokuPLM 没有 Part 子树端点，此方法通过逐层调用
        get_part_detail 递归遍历 partIterations[-1].components
        手动拼装完整树结构。

        返回列表，每行 dict 包含：
            part_number (str)       — 零件号
            version     (str)       — 版本
            iteration   (int)       — 最新迭代号
            name        (str)       — 零件名
            check_out_user (str)    — 签出人（空字符串表示未签出）
            depth       (int)       — 层级深度（根节点=0）
            parent_pn   (str|None)  — 父零件号，根节点为 None
            quantity    (int)       — 使用数量（来自 PartUsageLinkDTO.amount）

        注意：
        - 已访问的零件号+版本组合不再展开子树（防止循环引用）
        - 网络错误或访问受限的节点跳过（不中断整体遍历）
        - 对于 Docdoku 的版本号，实际存储为单字母（"A"/"B"等），
          PartUsageLinkDTO.component.version 有时为 null，
          此时回退为 "A"
        """
        if _seen is None:
            _seen = set()

        key = f"{part_number}-{version}"
        if key in _seen or _depth > max_depth:
            return []
        _seen.add(key)

        rows: list[dict] = []

        try:
            detail = self.get_part_detail(workspace, part_number, version)
        except Exception as exc:
            logger.debug(f"get_part_components_flat: 跳过 {key} — {exc}")
            return []

        # 取最新迭代
        part_iters = detail.get("partIterations") or []
        if not part_iters:
            return []
        latest = part_iters[-1]
        iter_num  = int(latest.get("iteration") or 0)
        part_name = str(detail.get("name") or detail.get("number") or part_number)

        checkout_raw = detail.get("checkOutUser")
        if isinstance(checkout_raw, dict):
            check_out_user = str(checkout_raw.get("login") or checkout_raw.get("name") or "")
        else:
            check_out_user = str(checkout_raw or "")

        # 根节点自身（_depth==0 时由调用方决定是否追加，此处统一追加）
        rows.append({
            "part_number":    part_number,
            "version":        version,
            "iteration":      iter_num,
            "name":           part_name,
            "check_out_user": check_out_user,
            "depth":          _depth,
            "parent_pn":      None if _depth == 0 else None,  # 由递归调用方填入
            "quantity":       1,
        })

        # 递归子件
        components = latest.get("components") or []
        for comp_link in components:
            comp = comp_link.get("component") or {}
            child_pn  = str(comp.get("number") or "").strip()
            child_ver = str(comp.get("version") or "A").strip() or "A"
            quantity  = int(comp_link.get("amount") or 1)
            if not child_pn:
                continue
            child_rows = self.get_part_components_flat(
                workspace, child_pn, child_ver,
                max_depth=max_depth,
                _depth=_depth + 1,
                _seen=_seen,
            )
            # 填入 parent_pn 和 quantity
            if child_rows:
                child_rows[0]["parent_pn"] = part_number
                child_rows[0]["quantity"]  = quantity
            rows.extend(child_rows)

        return rows

    def list_part_attachments(
        self,
        workspace: str,
        part_number: str,
        version: str,
        iteration: int | str,
    ) -> list[str]:
        """获取零件迭代的附件文件名列表（attachedFiles + nativeCADFile）。

        通过 get_part_detail 获取完整的 PartRevisionDTO，
        从中提取指定迭代的所有附加文件名。
        iteration 参数接受 int 或 str，内部统一转为 int 比较。
        当 iteration 为 0 或空字符串时，返回最新迭代（lastIterationNumber）的附件。
        """
        try:
            target_iter = int(iteration)
        except (ValueError, TypeError):
            target_iter = 0  # 回退到最新迭代

        detail = self.get_part_detail(workspace, part_number, version)
        part_iterations = detail.get("partIterations") or []

        # iteration=0 表示取最新迭代（lastIterationNumber）
        if target_iter == 0:
            target_iter = int(detail.get("lastIterationNumber") or 0)

        result: list[str] = []
        for it in part_iterations:
            if int(it.get("iteration", -1)) == target_iter:
                # 普通附件
                for f in (it.get("attachedFiles") or []):
                    name = f.get("name") or f.get("fileName") or ""
                    if name:
                        result.append(name)
                # 原生 CAD 文件（nativeCADFile 是独立字段）
                native = it.get("nativeCADFile")
                if native and isinstance(native, dict):
                    name = native.get("name") or native.get("fileName") or ""
                    if name and name not in result:
                        result.append(name)
                return result
        return result

    def download_attached_file(
        self,
        workspace: str,
        part_number: str,
        version: str,
        iteration: int,
        filename: str,
        dest_path: str,
        sub_type: str = "attachedfiles",
        progress_cb=None,
    ) -> None:
        """下载零件迭代的附件到本地路径。

        端点：GET /files/{ws}/parts/{pn}/{ver}/{iter}/{subType}/{fileName}
        参数：
            sub_type:    "attachedfiles"（普通附件）或 "nativecad"（CAD 几何文件）
            dest_path:   本地保存路径（完整文件路径）
            progress_cb: 可选回调 progress_cb(downloaded_bytes, total_bytes, speed_bps)
                         其中 speed_bps 为本次调用的瞬时速度（字节/秒），total_bytes 为 0
                         表示服务端未返回 Content-Length。
        """
        import time as _time_mod

        ws  = urllib.parse.quote(workspace,   safe="")
        pn  = urllib.parse.quote(part_number, safe="")
        fn  = urllib.parse.quote(filename,    safe="")
        url = f"{self._base}/files/{ws}/parts/{pn}/{version}/{iteration}/{sub_type}/{fn}"

        # 文件下载：Accept 必须为 */* 而非 application/json，否则服务器返回 406
        headers = self._headers({"Content-Type": None, "Accept": "*/*"})
        # 去掉 Content-Type（GET 请求不需要）
        headers = {k: v for k, v in headers.items() if k != "Content-Type"}
        req = urllib.request.Request(url, headers=headers, method="GET")

        import os as _os_mod
        _os_mod.makedirs(_os_mod.path.dirname(dest_path), exist_ok=True)

        try:
            with self._opener.open(req, timeout=300) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                chunk_size = 65536  # 64 KB
                t0 = _time_mod.monotonic()
                with open(dest_path, "wb") as fout:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        fout.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb:
                            elapsed = _time_mod.monotonic() - t0
                            speed   = downloaded / elapsed if elapsed > 0 else 0
                            progress_cb(downloaded, total, speed)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode(errors="replace")
            except Exception:
                pass
            raise PlmApiError(
                f"下载附件失败 {part_number}/{filename}: HTTP {exc.code} — {body[:200]}",
                exc.code,
            ) from exc
        logger.debug(f"PLM 附件已下载：{part_number}-{version}/{iteration}/{filename} → {dest_path}")

    def get_part_iterations_detail(
        self,
        workspace: str,
        part_number: str,
        version: str,
    ) -> list[dict]:
        """获取零件所有迭代的详情列表（用于附件弹窗迭代切换）。

        通过 get_part_detail 获取完整的 PartRevisionDTO，
        返回 partIterations 列表，按 iteration 号升序排序。

        每项 dict 结构（PartIterationDTO 字段子集）：
            iteration        (int)    — 迭代号
            iterationNote    (str)    — 迭代备注
            checkInDate      (str)    — 签入时间（ISO 格式，可能为 null）
            modificationDate (str)    — 修改时间
            author           (dict)   — {"login": str, "name": str}
            attachedFiles    (list)   — [{name, fullName, ...}]
            nativeCADFile    (dict|None) — 原生 CAD 文件信息
            components       (list)   — BOM 子件（1级）
        """
        detail = self.get_part_detail(workspace, part_number, version)
        iterations = detail.get("partIterations") or []
        # 按迭代号升序排序
        return sorted(iterations, key=lambda it: int(it.get("iteration") or 0))

    def extract_part_summary(self, part_detail: dict) -> dict:
        """从 get_part_detail 返回的原始 dict 中提取常用字段，返回摘要 dict。

        返回字段：
            number           (str)
            version          (str)
            lastIterationNumber (int)
            name             (str)
            checkOutUser     (str)   — login，未签出为空字符串
            modificationDate (str)   — 最新迭代的签入/修改时间（ISO 格式）
            authorLogin      (str)   — 最新迭代的作者 login
            lifecycleState   (str)   — instanceAttributes 中"设计状态"属性值
            tags             (list)  — 标签 ID 列表
        """
        # 基础字段
        number  = str(part_detail.get("number") or "")
        version = str(part_detail.get("version") or "")
        last_iter = int(part_detail.get("lastIterationNumber") or 0)
        name    = str(part_detail.get("name") or "")

        # 签出人
        cout_raw = part_detail.get("checkOutUser")
        if isinstance(cout_raw, dict):
            check_out_user = str(cout_raw.get("login") or cout_raw.get("name") or "")
        else:
            check_out_user = str(cout_raw or "")

        # 从最新迭代提取 modificationDate / author / lifecycleState
        iterations = part_detail.get("partIterations") or []
        # 找最新迭代（iteration 号最大）
        latest_it: dict = {}
        max_iter_num = -1
        for it in iterations:
            n = int(it.get("iteration") or 0)
            if n > max_iter_num:
                max_iter_num = n
                latest_it = it

        # 修改时间（优先 checkInDate，其次 modificationDate）
        mod_date = ""
        if latest_it:
            mod_date = (
                str(latest_it.get("checkInDate") or "")
                or str(latest_it.get("modificationDate") or "")
            )

        # 作者 login
        author_login = ""
        if latest_it:
            author = latest_it.get("author")
            if isinstance(author, dict):
                author_login = str(author.get("login") or author.get("name") or "")

        # 生命周期状态（instanceAttributes 中名为"设计状态"的属性）
        lifecycle_state = ""
        if latest_it:
            for attr in (latest_it.get("instanceAttributes") or []):
                attr_name = str(attr.get("name") or "")
                if attr_name in ("设计状态", "lifecycleState", "Lifecycle State"):
                    lifecycle_state = str(attr.get("value") or "")
                    break

        # 标签
        tags_raw = part_detail.get("tags") or []
        tags = [str(t.get("id") or t) for t in tags_raw if t]

        return {
            "number":               number,
            "version":              version,
            "lastIterationNumber":  last_iter,
            "name":                 name,
            "checkOutUser":         check_out_user,
            "modificationDate":     mod_date,
            "authorLogin":          author_login,
            "lifecycleState":       lifecycle_state,
            "tags":                 tags,
        }

    def search_parts_summary(
        self,
        workspace: str,
        part_numbers: list[str],
        progress_callback=None,
    ) -> dict[str, dict]:
        """按零件号列表批量查询 PLM，返回 {pn: summary_dict}。

        每个 summary_dict 的格式与 extract_part_summary() 返回值相同。
        对查询失败的零件号，返回 None 值（表示 PLM 中不存在）。

        Args:
            part_numbers:      零件号列表
            progress_callback: 可选，progress_callback(done: int, total: int)
        """
        result: dict[str, dict] = {}
        total = len(part_numbers)

        for i, pn in enumerate(part_numbers):
            if progress_callback:
                progress_callback(i, total)
            try:
                # search_parts 支持前缀匹配，必须校验返回的 number 与 pn 精确相同
                parts = self.search_parts(workspace, number=pn, size=10)
                # 精确匹配（大小写不敏感）
                exact = [p for p in parts if str(p.get("number", "")).lower() == pn.lower()]
                if not exact:
                    result[pn] = None
                    continue
                p = exact[0]
                ver = str(p.get("version") or "A")
                # 获取完整详情（含 partIterations）
                detail = self.get_part_detail(workspace, pn, ver)
                result[pn] = self.extract_part_summary(detail)
            except Exception as exc:
                logger.debug(f"search_parts_summary: 查询 {pn} 失败 — {exc}")
                result[pn] = None

        if progress_callback:
            progress_callback(total, total)

        return result

