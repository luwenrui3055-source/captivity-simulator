from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

from .configuration import load_config, render_placeholders
from .engine import run_command
from .prompts import build_assistant_prompt
from .protocol import directive_to_command
from .projection import project_payload
from .reference import get_reference, reference_tool_schema
from .server import _save_path


SERVER_NAME = "captivity-simulator-mcp"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
TOOL_NAME = "captivity_simulator"
REFERENCE_TOOL_NAME = "captivity_simulator_reference"
RESOURCE_URI = "captivity-simulator://save/default"


@dataclass
class RpcError(Exception):
    code: int
    message: str
    data: Any | None = None


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _params_object(params: Any) -> dict[str, Any]:
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise RpcError(-32602, "Invalid params: expected an object")
    return params


def _tool_definition() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "读取或推进本地囚禁模拟器存档。先查询状态，再提交当前待处理事件允许的一条命令。"
            "查询状态请使用 status；开始新游戏请使用 new_game route=captured_by_assistant 或 "
            "new_game route=capture_assistant。状态变化由规则引擎负责，并分别返回囚禁方与被囚禁方视图。"
            "在 captured_by_assistant 路线中，夜间自主行动属于被囚禁的用户，应由用户在网页端选择；AI 不得代替用户调用 night_action。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "查询状态时传 status；开始新游戏时传 new_game route=captured_by_assistant "
                        "或 new_game route=capture_assistant。也可提交当前步骤要求的中文方括号指令。"
                        "需要回应时，先把玩家自然语言映射到当前提示列出的固定回应与心情选项，不要自造枚举值；原话放入台词字段，含空格时用引号。"
                        "引擎接受原始规则命令，但请勿只传未带指令格式的自然语言描述。"
                    ),
                },
                "save_id": {
                    "type": "string",
                    "description": "本地存档标识，默认使用 default。",
                    "default": "default",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    }


def _reference_tool_definition() -> dict[str, Any]:
    schema = reference_tool_schema()["function"]
    return {
        "name": schema["name"],
        "description": schema["description"],
        "inputSchema": schema["parameters"],
    }


def _configured_status(save_id: str) -> dict[str, Any]:
    payload = run_command("status", save_path=_save_path(save_id))
    return render_placeholders(project_payload(payload, "assistant"), load_config())


def _call_tool(params: Any) -> dict[str, Any]:
    body = _params_object(params)
    name = str(body.get("name") or "")
    if name == REFERENCE_TOOL_NAME:
        arguments = body.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise RpcError(-32602, "Invalid params: tool arguments must be an object")
        reference = get_reference(str(arguments.get("分类") or arguments.get("category") or ""))
        return {
            "content": [{"type": "text", "text": reference}],
            "structuredContent": {"text": reference},
        }
    if name != TOOL_NAME:
        raise RpcError(-32602, f"Unknown tool: {name or '<missing>'}")

    arguments = body.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise RpcError(-32602, "Invalid params: tool arguments must be an object")

    raw_command = str(arguments.get("command") or "").strip()
    if not raw_command:
        raise RpcError(-32602, "Invalid params: command is required")
    save_id = str(arguments.get("save_id") or "default")
    save_path = _save_path(save_id)
    current = run_command("status", save_path=save_path)
    command = directive_to_command(raw_command, current) or raw_command
    config = load_config()
    actor_view = current.get("captor_view") if isinstance(current.get("captor_view"), dict) else {}
    if command.split(maxsplit=1)[0] == "night_action" and str(actor_view.get("captive") or "") != "assistant":
        message = (
            "当前夜间行动回合属于被囚禁的用户，请由用户在 5058 网页端选择；"
            "AI 是囚禁方，不能代替用户提交 night_action。"
        )
        return {
            "content": [{"type": "text", "text": message}],
            "structuredContent": {"ok": False, "text": message},
            "isError": True,
        }
    payload = run_command(command, save_path=save_path)
    projected = project_payload(payload, "assistant")
    configured = render_placeholders(projected, config)
    engine_text = render_placeholders(str(payload.get("text") or ""), config).strip()
    prompt = build_assistant_prompt(payload, config) if configured.get("ok") else (engine_text or str(configured.get("text") or ""))
    visible_text = str(prompt or configured.get("text") or "").strip()
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": visible_text}],
        "structuredContent": {"ok": bool(configured.get("ok")), "text": visible_text},
    }
    if configured.get("ok") is False:
        result["isError"] = True
    return result


def _read_resource(params: Any) -> dict[str, Any]:
    body = _params_object(params)
    uri = str(body.get("uri") or "")
    if uri != RESOURCE_URI:
        raise RpcError(-32602, f"Unknown resource: {uri or '<missing>'}")
    payload = _configured_status("default")
    return {
        "contents": [
            {
                "uri": RESOURCE_URI,
                "mimeType": "application/json",
                "text": _json_dump(payload),
            }
        ]
    }


def _dispatch(method: str, params: Any) -> dict[str, Any]:
    if method == "initialize":
        body = _params_object(params)
        protocol_version = str(body.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION)
        return {
            "protocolVersion": protocol_version,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False},
            },
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    if method in {"notifications/initialized", "ping"}:
        return {}
    if method == "tools/list":
        return {"tools": [_tool_definition(), _reference_tool_definition()]}
    if method == "tools/call":
        return _call_tool(params)
    if method == "resources/list":
        return {
            "resources": [
                {
                    "uri": RESOURCE_URI,
                    "name": "Captivity Simulator default save",
                    "description": "Current status payload for the default local save.",
                    "mimeType": "application/json",
                }
            ]
        }
    if method == "resources/read":
        return _read_resource(params)
    raise RpcError(-32601, f"Method not found: {method}")


def handle_request(message: Any) -> dict[str, Any] | list[dict[str, Any]] | None:
    if isinstance(message, list):
        if not message:
            return _error(None, -32600, "Invalid Request")
        responses = [item for item in (handle_request(entry) for entry in message) if item is not None]
        return responses or None
    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid Request")
    if "method" not in message:
        return None

    has_id = "id" in message
    request_id = message.get("id")
    method = message.get("method")
    if not isinstance(method, str):
        return _error(request_id if has_id else None, -32600, "Invalid Request") if has_id else None
    try:
        result = _dispatch(method, message.get("params"))
    except RpcError as exc:
        return _error(request_id, exc.code, exc.message, exc.data) if has_id else None
    except Exception as exc:  # pragma: no cover - stdout is reserved for MCP JSON-RPC.
        print(f"{SERVER_NAME}: internal error while handling {method}: {exc}", file=sys.stderr)
        return _error(request_id, -32603, "Internal error") if has_id else None
    return _response(request_id, result) if has_id else None


def serve_stdio() -> None:
    for raw_line in sys.stdin.buffer:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, "Parse error", {"detail": str(exc)})
        else:
            response = handle_request(message)
        if response is not None:
            sys.stdout.write(_json_dump(response) + "\n")
            sys.stdout.flush()


def main() -> None:
    serve_stdio()


if __name__ == "__main__":
    main()
