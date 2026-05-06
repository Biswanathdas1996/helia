"""Zoho Desk via a remote MCP server (Streamable HTTP + JSON-RPC 2.0).

Supports create and get ticket tools when the MCP server exposes them.

Environment:

- ``MCP_ENABLED`` — ``true`` / ``1`` to allow MCP usage (with ``MCP_ENDPOINT_URL``).
- ``MCP_ENDPOINT_URL`` — MCP HTTP endpoint (treated as sensitive).
- ``MCP_AUTH_TOKEN`` — optional ``Authorization: Bearer`` value.
- ``ZOHO_ORG_ID`` — required for ``ZohoDesk_*`` tool query/path params (same as REST).
- ``ZOHO_DEPARTMENT_ID``, ``ZOHO_CONTACT_ID`` — optional ticket routing hints (create only).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger("api-server.mcp")

# Streamable HTTP clients MUST advertise both per MCP spec.
_MCP_ACCEPT = "application/json, text/event-stream"

_TICKET_ID_IN_TEXT = re.compile(
    r'"(?:id|ticketId|ticket_id)"\s*:\s*"([^"]+)"|"(?:id|ticketId|ticket_id)"\s*:\s*(\d+)',
    re.I,
)

_PRIORITY_LABEL = {"low": "Low", "medium": "Medium", "high": "High", "urgent": "Urgent"}


def _desk_priority_label(priority: str) -> str:
    return _PRIORITY_LABEL.get(str(priority).strip().lower(), "Medium")


def _jsonrpc_id_matches(message_id: Any, expect: int) -> bool:
    if message_id == expect:
        return True
    try:
        return int(message_id) == expect
    except (TypeError, ValueError):
        return str(message_id) == str(expect)


def _jsonrpc_response_from_sse(body: str, request_id: int) -> Optional[Dict[str, Any]]:
    """Pull JSON-RPC envelope(s) from an MCP SSE POST response body."""
    fallbacks: List[Dict[str, Any]] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if _jsonrpc_id_matches(obj.get("id"), request_id):
            return obj
        if "result" in obj or "error" in obj:
            fallbacks.append(obj)
    return fallbacks[-1] if fallbacks else None


def _decode_tool_result_payload(response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize ``tools/call`` JSON-RPC ``result`` into a dict for Zoho parsing."""
    result = response.get("result")
    if not isinstance(result, dict):
        return None

    if result.get("isError"):
        log.warning("MCP tool reported isError: %s", result)

    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured

    content_list = result.get("content")
    if not isinstance(content_list, list):
        content_list = []

    for content_item in content_list:
        if not isinstance(content_item, dict):
            continue
        if content_item.get("type") != "text":
            continue
        text_value = content_item.get("text")
        if text_value is None:
            continue
        if isinstance(text_value, dict):
            return text_value
        if isinstance(text_value, str):
            try:
                parsed = json.loads(text_value)
            except (json.JSONDecodeError, TypeError):
                m = _TICKET_ID_IN_TEXT.search(text_value)
                if m:
                    tid = (m.group(1) or m.group(2) or "").strip()
                    if tid:
                        return {"id": tid, "raw_text": text_value}
                return {"raw_text": text_value}
            else:
                return parsed if isinstance(parsed, dict) else {"raw_text": text_value}

    if any(
        result.get(k) is not None and str(result.get(k)).strip()
        for k in ("id", "ticketId", "ticket_id")
    ):
        return dict(result)

    return None


class MCPHttpClient:
    """Minimal MCP session client for invoking Zoho Desk tools over HTTP."""

    def __init__(self) -> None:
        self.endpoint_url = (os.environ.get("MCP_ENDPOINT_URL") or "").strip()
        self.session_id: Optional[str] = None
        self.request_id = 0
        self.is_initialized = False
        self._available_tool_names: List[str] = []

    @staticmethod
    def enabled() -> bool:
        return os.environ.get("MCP_ENABLED", "").lower() in ("1", "true", "yes") and bool(
            (os.environ.get("MCP_ENDPOINT_URL") or "").strip()
        )

    async def initialize(self) -> bool:
        if not self.enabled() or not self.endpoint_url:
            log.debug("MCP disabled or no endpoint URL configured.")
            return False

        try:
            response = await self._send_jsonrpc(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "helia-api-server",
                        "version": "1.0.0",
                    },
                },
            )

            if response and "result" in response:
                server_info = response["result"].get("serverInfo", {})
                log.info(
                    "MCP connected: %s v%s",
                    server_info.get("name", "unknown"),
                    server_info.get("version", "?"),
                )
                await self._send_jsonrpc_notification("notifications/initialized", {})
                self.is_initialized = True
                return True

            err = response.get("error", {}) if response else {}
            log.warning("MCP handshake failed: %s", err)
            return False
        except Exception as exc:  # pragma: no cover - network
            log.warning("MCP initialization error: %s", exc)
            return False

    async def list_tools(self) -> List[Dict[str, Any]]:
        if not self.is_initialized:
            await self.initialize()

        response = await self._send_jsonrpc("tools/list", {})
        if response and "result" in response:
            tools = response["result"].get("tools", [])
            self._available_tool_names = [t.get("name", "") for t in tools if isinstance(t, dict)]
            return tools
        return []

    async def _resolve_tool_name(self, preferred: str, aliases: List[str]) -> str:
        if not self._available_tool_names:
            await self.list_tools()

        if preferred in self._available_tool_names:
            return preferred

        for alias in aliases:
            if alias in self._available_tool_names:
                return alias

        candidates = [preferred, *aliases]
        normalized_candidates = {c.lower().replace("-", "_").replace(" ", "") for c in candidates}
        for actual in self._available_tool_names:
            norm_actual = actual.lower().replace("-", "_").replace(" ", "")
            if norm_actual in normalized_candidates:
                return actual

        return preferred

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.is_initialized:
            success = await self.initialize()
            if not success:
                return None

        response = await self._send_jsonrpc(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments,
            },
            timeout=120.0,
        )

        if response and "result" in response:
            decoded = _decode_tool_result_payload(response)
            if decoded is not None:
                return decoded
            # Unusual shapes: return whole result for upstream coercion / logging
            inner = response["result"]
            return inner if isinstance(inner, dict) else {"content": response["result"]}

        if response and "error" in response:
            log.warning("MCP tool call error: %s", response["error"])
            return {"error": True, "message": str(response["error"])}

        if response and response.get("accepted"):
            return {
                "accepted": True,
                "success": False,
                "message": "MCP request accepted; no immediate tool result returned.",
            }

        return None

    async def create_ticket(
        self,
        subject: str,
        description: str,
        email: str,
        contact_name: str,
        phone: str = "",
        priority: str = "Medium",
        category: str = "General",
    ) -> Optional[Dict[str, Any]]:
        """Create a Desk ticket via the MCP server's ticket tool."""
        tool_name = await self._resolve_tool_name(
            "create_ticket",
            ["ZohoDesk_createTicket"],
        )

        prio = _desk_priority_label(priority)

        payload: Dict[str, Any] = {
            "subject": subject,
            "description": description,
            "email": email,
            "contactName": contact_name,
            "contact_name": contact_name,
            "phone": phone,
            "priority": prio,
            "category": category,
            "channel": "Chat",
        }

        if tool_name == "ZohoDesk_createTicket":
            org_id = (os.environ.get("ZOHO_ORG_ID") or "").strip()
            if not org_id:
                log.warning("ZOHO_ORG_ID is required for ZohoDesk_createTicket")
                return None

            contact_last = contact_name or "Customer"
            body: Dict[str, Any] = {
                "subject": subject,
                "description": description,
                "phone": phone,
                "priority": prio,
                "category": category,
                "channel": "Chat",
                "status": "Open",
            }
            dept = (os.environ.get("ZOHO_DEPARTMENT_ID") or "").strip()
            if dept:
                body["departmentId"] = dept
            cid = (os.environ.get("ZOHO_CONTACT_ID") or "").strip()
            if cid:
                # Existing Desk contact — avoid nested ``contact.email``; Zoho validates it and may reject blanks/mismatches.
                body["contactId"] = cid
            else:
                body["email"] = email
                body["contact"] = {
                    "lastName": contact_last,
                    "email": email,
                }

            payload = {
                "body": body,
                "query_params": {"orgId": org_id},
            }

        return await self.call_tool(tool_name, payload)

    async def get_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single Desk ticket via the MCP server's get-ticket tool."""
        tid = (ticket_id or "").strip()
        if not tid:
            return None

        tool_name = await self._resolve_tool_name(
            "get_ticket",
            [
                "ZohoDesk_getTicket",
                "ZohoDesk_get_ticket",
                "getTicket",
                "ZohoDesk_getTicketById",
            ],
        )

        payload: Dict[str, Any] = {
            "ticketId": tid,
            "ticket_id": tid,
        }

        tl = tool_name.lower()
        use_zoho_path = tl.startswith("zohodesk_") and "get" in tl and "ticket" in tl and "tickets" not in tl

        if use_zoho_path:
            org_id = (os.environ.get("ZOHO_ORG_ID") or "").strip()
            if not org_id:
                log.warning("ZOHO_ORG_ID is required for %s", tool_name)
                return None
            payload = {
                "path_params": {"ticket_id": tid},
                "query_params": {"orgId": org_id},
            }

        return await self.call_tool(tool_name, payload)

    async def _send_jsonrpc(
        self,
        method: str,
        params: Dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> Optional[Dict[str, Any]]:
        self.request_id += 1
        rpc_id = self.request_id
        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": method,
            "params": params,
        }

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": _MCP_ACCEPT,
        }

        token = (os.environ.get("MCP_AUTH_TOKEN") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(self.endpoint_url, json=payload, headers=headers)

                new_session_id = response.headers.get("Mcp-Session-Id")
                if new_session_id:
                    self.session_id = new_session_id

                if response.status_code == 202:
                    return {"accepted": True}
                if response.status_code != 200:
                    log.warning("MCP HTTP error %s: %s", response.status_code, response.text[:200])
                    return None

                ct = (response.headers.get("content-type") or "").lower()
                if "text/event-stream" in ct:
                    sse_body = response.text
                    sse_rpc = _jsonrpc_response_from_sse(sse_body, rpc_id)
                    if sse_rpc:
                        return sse_rpc
                    log.warning(
                        "MCP SSE response missing matching JSON-RPC id=%s (body prefix %.120s)",
                        rpc_id,
                        sse_body[:120],
                    )
                    return None

                try:
                    return response.json()
                except json.JSONDecodeError:
                    sse_retry = _jsonrpc_response_from_sse(response.text, rpc_id)
                    if sse_retry:
                        return sse_retry
                    log.warning(
                        "MCP response not JSON (Content-Type=%s prefix %.200s)",
                        ct,
                        response.text[:200],
                    )
                    return None
        except httpx.TimeoutException:
            log.warning("MCP request timed out for method: %s", method)
            return None
        except Exception as exc:  # pragma: no cover - network
            log.warning("MCP HTTP request failed: %s", exc)
            return None

    async def _send_jsonrpc_notification(self, method: str, params: Dict[str, Any]) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

        headers: Dict[str, str] = {"Content-Type": "application/json", "Accept": _MCP_ACCEPT}
        token = (os.environ.get("MCP_AUTH_TOKEN") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(self.endpoint_url, json=payload, headers=headers)
        except Exception:
            pass


_mcp_client: Optional[MCPHttpClient] = None


def get_mcp_client() -> MCPHttpClient:
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPHttpClient()
    return _mcp_client
