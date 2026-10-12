"""Client MCP tối giản (đồng bộ, chỉ dùng httpx) để gọi thẳng vidIQ MCP server
https://mcp.vidiq.com/mcp từ Python — không cần qua model AI trung gian, nên
điểm số do CODE đọc trực tiếp từ kết quả tool (không thể bị AI bịa).

Cấu hình trong .env:
    VIDIQ_API_KEY     API key tạo tại app.vidiq.com → Account Settings → MCP
                      (vidIQ MCP chỉ có ở gói Max)
    VIDIQ_CHANNEL_ID  (tùy chọn) ID kênh YouTube để vidIQ chấm điểm sát kênh hơn
    VIDIQ_MCP_URL     (tùy chọn) mặc định https://mcp.vidiq.com/mcp
    VIDIQ_AUTH_HEADER (tùy chọn) "bearer" (mặc định) hoặc "x-api-key"

Giao thức: MCP Streamable HTTP (JSON-RPC 2.0 qua POST, trả JSON hoặc SSE).
Tên tool / tham số của vidIQ có thể đổi theo thời gian nên client đọc
tools/list rồi tự khớp tham số theo inputSchema (xem `_build_args`).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import httpx

DEFAULT_URL = "https://mcp.vidiq.com/mcp"
PROTOCOL_VERSION = "2025-06-18"
CREDITS_PER_CALL = 5  # chấm tiêu đề / gợi ý tiêu đề: 5 credit mỗi lần (theo vidIQ)

# Tên tool theo workflow đã chạy thật trên Claude; khớp thêm theo từ khóa phòng khi vidIQ đổi tên.
SCORE_TOOL_HINTS = ("vidiq_score_title", "score_title", "title_score")
GENERATE_TOOL_HINTS = ("vidiq_generate_titles", "generate_titles", "title_ideas", "titles")
BALANCE_TOOL_HINTS = ("credit", "balance", "usage")


class VidIQError(RuntimeError):
    pass


class VidIQClient:
    def __init__(self, api_key: Optional[str] = None, url: Optional[str] = None,
                 channel_id: Optional[str] = None, timeout: float = 90.0):
        self.api_key = (api_key or os.environ.get("VIDIQ_API_KEY") or "").strip()
        if not self.api_key:
            raise VidIQError(
                "Thiếu VIDIQ_API_KEY trong .env. Tạo API key tại app.vidiq.com → Account Settings → MCP "
                "(vidIQ MCP chỉ có ở gói Max), dán vào .env rồi khởi động lại Streamlit."
            )
        self.url = (url or os.environ.get("VIDIQ_MCP_URL") or DEFAULT_URL).strip()
        self.channel_id = (channel_id if channel_id is not None else os.environ.get("VIDIQ_CHANNEL_ID", "")).strip()
        self.auth_mode = os.environ.get("VIDIQ_AUTH_HEADER", "bearer").strip().lower()
        self._http = httpx.Client(timeout=timeout)
        self._session_id: Optional[str] = None
        self._initialized = False
        self._next_id = 1
        self._tools: Optional[list[dict]] = None
        self.credits_used = 0

    # ------------------------------------------------------- tầng giao thức --

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "MCP-Protocol-Version": PROTOCOL_VERSION}
        if self.auth_mode == "x-api-key":
            h["X-API-Key"] = self.api_key
        else:
            h["Authorization"] = f"Bearer {self.api_key}"
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _post(self, payload: dict) -> Optional[dict]:
        try:
            resp = self._http.post(self.url, headers=self._headers(), json=payload)
        except httpx.HTTPError as e:
            raise VidIQError(f"Không kết nối được vidIQ MCP ({self.url}): {e}") from e
        if resp.status_code in (401, 403) and self.auth_mode != "x-api-key" and payload.get("method") == "initialize":
            # Thử kiểu header thứ 2 trước khi báo lỗi.
            self.auth_mode = "x-api-key"
            return self._post(payload)
        if resp.status_code in (401, 403):
            raise VidIQError(
                f"vidIQ từ chối API key (HTTP {resp.status_code}). Kiểm tra VIDIQ_API_KEY trong .env "
                "(tạo lại tại app.vidiq.com → Account Settings → MCP) và tài khoản có gói Max. "
                f"Chi tiết: {resp.text[:300]}"
            )
        if resp.status_code == 202 or not resp.content:
            return None
        if resp.status_code >= 400:
            raise VidIQError(f"vidIQ MCP trả lỗi HTTP {resp.status_code}: {resp.text[:500]}")
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            return self._parse_sse(resp.text, payload.get("id"))
        return resp.json()

    @staticmethod
    def _parse_sse(text: str, want_id) -> Optional[dict]:
        found = None
        for block in re.split(r"\r?\n\r?\n", text):
            data = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
            if not data:
                continue
            try:
                msg = json.loads(data)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == want_id and ("result" in msg or "error" in msg):
                return msg
            found = msg
        return found

    def _rpc(self, method: str, params: Optional[dict] = None) -> Any:
        msg_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        msg = self._post(payload)
        if msg is None:
            raise VidIQError(f"vidIQ không trả kết quả cho {method}.")
        if "error" in msg:
            err = msg["error"]
            raise VidIQError(f"vidIQ báo lỗi ({method}): {err.get('message')} {err.get('data') or ''}".strip())
        return msg.get("result")

    def connect(self) -> None:
        if self._initialized:
            return
        self._rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                 "clientInfo": {"name": "auto-video-affiliate", "version": "1.0"}})
        self._initialized = True
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> list[dict]:
        if self._tools is None:
            self.connect()
            tools, cursor = [], None
            while True:
                result = self._rpc("tools/list", {"cursor": cursor} if cursor else {}) or {}
                tools += result.get("tools", [])
                cursor = result.get("nextCursor")
                if not cursor:
                    break
            self._tools = tools
        return self._tools

    def call_tool(self, name: str, arguments: dict, credits: int = 0) -> tuple[str, Any]:
        """Trả về (text gộp từ content, structuredContent nếu có)."""
        self.connect()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments}) or {}
        self.credits_used += credits
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        text = "\n".join(texts)
        if result.get("isError"):
            raise VidIQError(f"Tool {name} báo lỗi: {text[:500]}")
        return text, result.get("structuredContent")

    # ------------------------------------------------------- tầng tiện ích --

    def find_tool(self, hints: tuple[str, ...]) -> Optional[dict]:
        tools = self.list_tools()
        for hint in hints:
            for t in tools:
                if t.get("name") == hint:
                    return t
        for hint in hints:
            for t in tools:
                if hint in t.get("name", "").lower():
                    return t
        return None

    def _build_args(self, tool: dict, values: dict) -> dict:
        """Khớp giá trị của mình vào đúng tên tham số trong inputSchema của tool.

        values: các khóa logic — title, type, channel_id, language, keyword, count.
        """
        props = (tool.get("inputSchema") or {}).get("properties") or {}
        aliases = {
            "title": ["title", "videoTitle", "video_title", "text"],
            "type": ["type", "titleType", "title_type", "format", "videoType", "video_type"],
            "channel_id": ["channelId", "channel_id", "channel"],
            "language": ["language", "lang", "languageCode", "language_code"],
            "keyword": ["keyword", "topic", "query", "description", "idea", "prompt", "subject", "keywords"],
            "count": ["count", "limit", "numTitles", "num_titles", "n"],
        }
        args = {}
        for key, value in values.items():
            if value in (None, ""):
                continue
            for name in aliases.get(key, [key]):
                if name in props:
                    schema = props[name]
                    if schema.get("type") == "array" and not isinstance(value, list):
                        value = [value]
                    enum = schema.get("enum")
                    if enum and value not in enum:
                        break  # giá trị không hợp lệ với schema -> bỏ, để mặc định của tool
                    args[name] = value
                    break
        if not props:  # tool không khai báo schema -> gửi theo tên mặc định
            args = {k: v for k, v in values.items() if v not in (None, "")}
            if "channel_id" in args:
                args["channelId"] = args.pop("channel_id")
        return args

    def score_title(self, title: str, title_type: str = "long") -> tuple[Optional[float], str]:
        tool = self.find_tool(SCORE_TOOL_HINTS)
        if tool is None:
            raise VidIQError("Không tìm thấy tool chấm điểm tiêu đề trong vidIQ MCP (vidiq_score_title). "
                             "Bấm 'Kiểm tra kết nối' để xem danh sách tool hiện có.")
        args = self._build_args(tool, {"title": title, "type": title_type, "channel_id": self.channel_id})
        text, structured = self.call_tool(tool["name"], args, credits=CREDITS_PER_CALL)
        return extract_score(structured, text), text

    def generate_titles(self, keyword: str, language: str = "vi") -> tuple[list[dict], str]:
        tool = self.find_tool(GENERATE_TOOL_HINTS[:3]) or self.find_tool(GENERATE_TOOL_HINTS[3:])
        if tool is None:
            raise VidIQError("Không tìm thấy tool gợi ý tiêu đề trong vidIQ MCP (vidiq_generate_titles).")
        args = self._build_args(tool, {"keyword": keyword, "language": language, "channel_id": self.channel_id})
        text, structured = self.call_tool(tool["name"], args, credits=CREDITS_PER_CALL)
        return extract_titles(structured, text), text

    def balance(self) -> Optional[str]:
        """Số credit còn lại (tool 0 credit) — None nếu vidIQ không có tool này."""
        tool = self.find_tool(BALANCE_TOOL_HINTS)
        if tool is None:
            return None
        text, structured = self.call_tool(tool["name"], {}, credits=0)
        return json.dumps(structured, ensure_ascii=False) if structured else text

    def close(self) -> None:
        self._http.close()


# ------------------------------------------------------- đọc kết quả tool --

_SCORE_KEYS = ("score", "overall_score", "overallScore", "title_score", "titleScore", "total_score", "totalScore")


def _walk(obj, depth: int = 0):
    if depth > 6:
        return
    yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, depth + 1)


def _json_in_text(text: str):
    text = (text or "").strip()
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except ValueError:
            pass
    return None


def extract_score(structured, text: str) -> Optional[float]:
    """Lấy điểm vidIQ trả về (0-100). Thứ tự: structuredContent → JSON trong text →
    dòng chữ kiểu 'Score: 87/100'. Không tìm thấy -> None (KHÔNG tự đoán)."""
    for source in (structured, _json_in_text(text)):
        if source is None:
            continue
        for node in _walk(source):
            if isinstance(node, dict):
                for key in _SCORE_KEYS:
                    val = node.get(key)
                    if isinstance(val, (int, float)) and 0 <= val <= 100:
                        return float(val)
                    if isinstance(val, dict):
                        inner = val.get("value") or val.get("score")
                        if isinstance(inner, (int, float)) and 0 <= inner <= 100:
                            return float(inner)
    m = re.search(r"(?:overall\s*)?(?:score|điểm)[^\d\n]{0,20}(\d{1,3}(?:[.,]\d+)?)\s*(?:/\s*100)?", text or "",
                  re.IGNORECASE)
    if m:
        val = float(m.group(1).replace(",", "."))
        if 0 <= val <= 100:
            return val
    return None


def extract_titles(structured, text: str) -> list[dict]:
    """[{title, score?}] từ kết quả gợi ý tiêu đề."""
    out: list[dict] = []
    for source in (structured, _json_in_text(text)):
        if source is None:
            continue
        for node in _walk(source):
            if isinstance(node, dict):
                t = node.get("title") or node.get("text") or node.get("name")
                if isinstance(t, str) and 10 <= len(t) <= 150:
                    out.append({"title": t.strip(), "score": extract_score(node, "")})
            elif isinstance(node, list) and node and all(isinstance(x, str) for x in node):
                out += [{"title": x.strip(), "score": None} for x in node if 10 <= len(x) <= 150]
        if out:
            break
    if not out:
        for line in (text or "").splitlines():
            line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip().strip('"“”')
            if 10 <= len(line) <= 150 and not line.endswith(":"):
                out.append({"title": line, "score": None})
    seen, uniq = set(), []
    for item in out:
        if item["title"].lower() not in seen:
            seen.add(item["title"].lower())
            uniq.append(item)
    return uniq
