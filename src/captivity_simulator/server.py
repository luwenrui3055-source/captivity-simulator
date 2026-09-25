from __future__ import annotations

import os
import re
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, Response

from .adapter import AdapterError, request_assistant
from .configuration import load_config, render_placeholders
from .engine import run_command
from .prompts import build_assistant_prompt
from .projection import project_payload
from .protocol import directive_to_command
from .settings import DATA_DIR, PROJECT_ROOT


WEB_DIST = PROJECT_ROOT / "web" / "dist"


def _safe_save_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "default"))[:80].strip("._-")
    return cleaned or "default"


def _save_path(save_id: str) -> Path:
    return DATA_DIR / "saves" / f"{_safe_save_id(save_id)}.json"


def _configured_result(payload: dict, config: dict) -> dict:
    return render_placeholders(payload, config)


def _pending_type(payload: dict) -> str:
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    pending = state.get("pending_event") if isinstance(state.get("pending_event"), dict) else {}
    return str(pending.get("type") or "")


def _assistant_has_pending(payload: dict) -> bool:
    for key in ("captor_view", "captive_view", "state"):
        view = payload.get(key) if isinstance(payload.get(key), dict) else {}
        pending = view.get("pending_event") if isinstance(view.get("pending_event"), dict) else {}
        if str(pending.get("actor") or "") == "assistant":
            return True
    return False


def _allows_required_followup(current_type: str, next_type: str) -> bool:
    if current_type == "monitor_gate" and next_type in {"monitor_handle", "day_plan_choice"}:
        return True
    if current_type == "monitor_handle" and next_type in {"day_plan_choice", "process_write"}:
        return True
    if current_type == "bell_response_choice" and next_type == "day_plan_choice":
        return True
    if current_type == "night_action_choice" and next_type in {"bell_voice_reveal", "item_secret_reveal"}:
        return True
    if current_type == "item_secret_reveal" and next_type == "item_secret_reveal":
        return True
    if next_type == "escape_choice":
        return True
    return current_type == "escape_choice" and next_type in {"process_write", "process_reaction_write"}


def _is_out_of_band_command(command: str) -> bool:
    return str(command or "").startswith(("gift_item ", "revoke_item "))


# ==== Basic Auth ====

def _load_htpasswd_users(path: str) -> dict:
    """读取htpasswd文件，返回 {username: hash} 字典。"""
    users = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or ":" not in line:
                    continue
                username, pwhash = line.split(":", 1)
                users[username] = pwhash
    except FileNotFoundError:
        return {}
    return users


def _verify_htpasswd(users: dict, username: str, password: str) -> bool:
    """用passlib校验htpasswd hash。支持bcrypt/apr1/sha/crypt/plaintext。"""
    pwhash = users.get(username)
    if not pwhash:
        return False
    try:
        from passlib.context import CryptContext
        ctx = CryptContext(schemes=["bcrypt", "apr_md5_crypt", "des_crypt", "sha256_crypt", "sha512_crypt", "plaintext"])
        return ctx.verify(password, pwhash)
    except Exception:
        return False


def _require_basic_auth(app: Flask) -> None:
    """如果配了CAPTIVITY_BASIC_AUTH_FILE环境变量，全局启用Basic Auth。"""
    auth_file = os.environ.get("CAPTIVITY_BASIC_AUTH_FILE", "").strip()
    if not auth_file or not os.path.isfile(auth_file):
        app.logger.warning("[auth] Basic Auth disabled (no htpasswd file)")
        return

    users = _load_htpasswd_users(auth_file)
    if not users:
        app.logger.warning("[auth] htpasswd file is empty, auth disabled")
        return

    app.logger.info("[auth] Basic Auth enabled with %d user(s)", len(users))

    @app.before_request
    def _check_auth():
        # 允许健康检查匿名访问（Zeabur自动探活用）
        if request.path == "/api/health":
            return None
        auth = request.authorization
        if auth and _verify_htpasswd(users, auth.username or "", auth.password or ""):
            return None
        return Response(
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="captivity-simulator"'},
        )


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)

    _require_basic_auth(app)

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "game": "captivity_simulator"})

    @app.get("/api/config")
    def public_config():
        config = load_config()
        return jsonify({"actors": config.get("actors") or {}, "ai_enabled": bool((config.get("ai") or {}).get("enabled"))})

    @app.post("/api/game/command")
    def game_command():
        body = request.get_json(silent=True) or {}
        config = load_config()
        result = run_command(str(body.get("command") or "status"), save_path=_save_path(str(body.get("save_id") or "default")))
        projected = project_payload(result, "user")
        return jsonify(_configured_result(projected, config)), 200 if result.get("ok") else 400

    @app.post("/api/game/sync-assistant")
    def sync_assistant():
        body = request.get_json(silent=True) or {}
        save_id = str(body.get("save_id") or "default")
        config = load_config()
        payload = run_command("status", save_path=_save_path(save_id))
        player_message = str(body.get("message") or "")
        result = payload
        sync_result = "no_directive"
        current_type = _pending_type(result)
        for round_index in range(3):
            prompt = build_assistant_prompt(result, config, message=player_message if round_index == 0 else "")
            try:
                reply_text = request_assistant(
                    prompt,
                    config,
                    player_message=player_message if round_index == 0 else "",
                )
            except AdapterError as exc:
                response = _configured_result(project_payload(result, "user"), config)
                response.update({"ok": False, "error": str(exc), "sync_result": "adapter_required"})
                return jsonify(response), 409
            command = directive_to_command(reply_text, result)
            if command and not _assistant_has_pending(result) and not _is_out_of_band_command(command):
                command = ""
            if not command:
                break
            result = run_command(command, save_path=_save_path(save_id))
            sync_result = "applied"
            if not result.get("ok") or not _assistant_has_pending(result):
                break
            next_type = _pending_type(result)
            if round_index >= 2 or not _allows_required_followup(current_type, next_type):
                break
            current_type = next_type
        response = _configured_result(project_payload(result, "user"), config)
        response.update({"sync_result": sync_result})
        return jsonify(response), 200 if response.get("ok") else 400

    @app.get("/")
    @app.get("/<path:path>")
    def web(path: str = "index.html"):
        target = WEB_DIST / path
        if path != "index.html" and target.is_file():
            return send_from_directory(WEB_DIST, path)
        if (WEB_DIST / "index.html").is_file():
            return send_from_directory(WEB_DIST, "index.html")
        return "Web build not found. Run: cd web && npm install && npm run build", 503

    return app


def main() -> None:
    config = load_config()
    server = config.get("server") if isinstance(config.get("server"), dict) else {}
    # Docker/Zeabur环境优先监听0.0.0.0；环境变量CAPTIVITY_HOST/PORT可覆盖
    default_host = os.environ.get("CAPTIVITY_HOST") or str(server.get("host") or "127.0.0.1")
    default_port = int(os.environ.get("CAPTIVITY_PORT") or server.get("port") or 5058)
    create_app().run(
        host=default_host,
        port=default_port,
        debug=False,
    )


if __name__ == "__main__":
    main()
