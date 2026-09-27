#!/usr/bin/env python3
"""Minimal stdio MCP wrapper around an already-logged-in official lark-cli.

Does not perform OAuth, config init, or auth login. Tokens stay in the
isolated lark-cli config/home; they are never written to Codex config.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from typing import Any, Dict, List, Optional

PROVIDER_ROOT = "/Users/echoman/.local-agent-bridge/feishu-provider"
DEFAULT_CLI = os.path.join(
    PROVIDER_ROOT,
    "cli-prefix/node_modules/@larksuite/cli/bin/lark-cli",
)
DEFAULT_CONFIG_DIR = os.path.join(PROVIDER_ROOT, "config")
DEFAULT_HOME = os.path.join(PROVIDER_ROOT, "home")
SERVER_NAME = "lark-cli"
SERVER_VERSION = "0.1.4"
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2026-07-28")
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
FRAME = {"mode": "headers"}  # headers | ndjson


def eprint(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()
    try:
        with open("/tmp/lark-mcp-stdio.log", "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass


def mask_id(value: Any) -> Any:
    if not isinstance(value, str) or len(value) <= 10:
        return value
    return value[:8] + "…" + value[-4:]


def redact(value: Any, key: str = "") -> Any:
    lk = key.lower()
    secretish = any(
        s in lk
        for s in ("token", "secret", "password", "refresh", "access_key", "appsecret")
    )
    if secretish and value not in (None, "", "ready", "valid", "not_configured"):
        if lk in ("tokenstatus", "token_status"):
            return value
        return "<redacted>"
    if lk in ("openid", "useropenid", "open_id", "user_open_id"):
        return mask_id(value)
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    return value


def cli_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["LARKSUITE_CLI_CONFIG_DIR"] = (
        os.environ.get("LARKSUITE_CLI_CONFIG_DIR") or DEFAULT_CONFIG_DIR
    )
    env["HOME"] = DEFAULT_HOME
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin:/Library/Developer/CommandLineTools/usr/bin:" + env.get("PATH", "")
    for k in list(env):
        if k.endswith("_ACCESS_TOKEN") or k.endswith("_APP_SECRET"):
            env.pop(k, None)
    return env


def run_cli(argv: List[str], timeout: int = 30) -> Dict[str, Any]:
    cli = os.environ.get("LARKSUITE_CLI_BIN", DEFAULT_CLI)
    env = cli_env()
    try:
        proc = subprocess.run(
            [cli, *argv],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "argv": argv}
    raw = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"raw": raw[:4000]}
    ok = proc.returncode == 0
    if isinstance(payload, dict) and payload.get("ok") is False:
        ok = False
    warnings = []
    if isinstance(payload, dict):
        raw_warnings = payload.get("data", {})
        if isinstance(raw_warnings, dict):
            warnings = raw_warnings.get("warnings") or []
        if not warnings:
            warnings = payload.get("warnings") or []
    warning_text = " ".join(str(item) for item in warnings) if isinstance(warnings, list) else str(warnings)
    if "no document changes" in warning_text or "degrade_code=1011" in warning_text:
        payload = {
            "ok": False,
            "error": "str_replace produced no document changes. Fetch the current document and use an exact existing substring as pattern. Do not treat this as Feishu being unavailable.",
            "warnings": warnings,
        }
    return {
        "ok": ok,
        "exitCode": proc.returncode,
        "configDir": env.get("LARKSUITE_CLI_CONFIG_DIR"),
        "result": redact(payload),
    }


def tool(name: str, description: str, schema: Dict[str, Any], read_only: bool = True) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": schema,
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": not read_only,
            "idempotentHint": read_only,
            "openWorldHint": True,
        },
    }


def tools() -> list:
    obj = {"type": "object", "properties": {}, "additionalProperties": False}
    return [
        tool("whoami", "Read-only: current official lark-cli identity. Does not start OAuth.", obj),
        tool("feishu_status", "Same as whoami: already-logged-in Feishu status. Does not start OAuth.", obj),
        tool(
            "feishu_chat_search",
            "Search visible Feishu group chats by keyword.",
            {"type": "object", "properties": {"query": {"type": "string"}, "page_size": {"type": "integer"}}, "required": ["query"], "additionalProperties": False},
        ),
        tool(
            "feishu_message_list",
            "List recent messages in a Feishu chat.",
            {"type": "object", "properties": {"chat_id": {"type": "string"}, "page_size": {"type": "integer"}, "page_token": {"type": "string"}}, "required": ["chat_id"], "additionalProperties": False},
        ),
        tool(
            "feishu_base_list_tables",
            "List tables in a Feishu Base.",
            {"type": "object", "properties": {"base_token": {"type": "string"}}, "required": ["base_token"], "additionalProperties": False},
        ),
        tool(
            "feishu_base_list_fields",
            "List fields for a Feishu Base table.",
            {"type": "object", "properties": {"base_token": {"type": "string"}, "table_id": {"type": "string"}}, "required": ["base_token", "table_id"], "additionalProperties": False},
        ),
        tool(
            "feishu_base_list_records",
            "List records from a Feishu Base table.",
            {"type": "object", "properties": {"base_token": {"type": "string"}, "table_id": {"type": "string"}, "view_id": {"type": "string"}, "page_size": {"type": "integer"}, "page_token": {"type": "string"}}, "required": ["base_token", "table_id"], "additionalProperties": False},
        ),
        tool(
            "feishu_base_create",
            "Create a new Feishu Base. Optional table_name and fields for the first table.",
            {"type": "object", "properties": {"name": {"type": "string"}, "folder_token": {"type": "string"}, "table_name": {"type": "string"}, "fields": {"type": "array", "items": {"type": "object"}}, "time_zone": {"type": "string"}}, "required": ["name"], "additionalProperties": False},
            read_only=False,
        ),
        tool(
            "feishu_base_create_view",
            "Create a grid/kanban/gallery/calendar/gantt view. view must include name; type=kanban for a board.",
            {"type": "object", "properties": {"base_token": {"type": "string"}, "table_id": {"type": "string"}, "view": {"type": "object"}}, "required": ["base_token", "table_id", "view"], "additionalProperties": False},
            read_only=False,
        ),
        tool(
            "feishu_message_send",
            "Send a plain-text Feishu message. Automated sends default to bot identity.",
            {"type": "object", "properties": {"chat_id": {"type": "string"}, "text": {"type": "string"}, "identity": {"type": "string", "enum": ["user", "bot"]}}, "required": ["chat_id", "text"], "additionalProperties": False},
            read_only=False,
        ),
        tool(
            "feishu_message_reply",
            "Reply with plain text to one exact Feishu message. Automated replies default to bot identity.",
            {"type": "object", "properties": {"message_id": {"type": "string"}, "text": {"type": "string"}, "identity": {"type": "string", "enum": ["user", "bot"]}}, "required": ["message_id", "text"], "additionalProperties": False},
            read_only=False,
        ),
        tool(
            "feishu_base_create_record",
            "Create one record after a mandatory field-schema preflight.",
            {"type": "object", "properties": {"base_token": {"type": "string"}, "table_id": {"type": "string"}, "fields": {"type": "object"}}, "required": ["base_token", "table_id", "fields"], "additionalProperties": False},
            read_only=False,
        ),
        tool(
            "feishu_base_update_record",
            "Update one exact record after a mandatory field-schema preflight.",
            {"type": "object", "properties": {"base_token": {"type": "string"}, "table_id": {"type": "string"}, "record_id": {"type": "string"}, "fields": {"type": "object"}}, "required": ["base_token", "table_id", "record_id", "fields"], "additionalProperties": False},
            read_only=False,
        ),

        tool(
            "feishu_wiki_node_get",
            "Resolve a Feishu wiki/docx URL to node + obj_token. Use before docs fetch/update when you only have a wiki link.",
            {"type": "object", "properties": {"node_token": {"type": "string"}}, "required": ["node_token"], "additionalProperties": False},
        ),
        tool(
            "feishu_docs_search",
            "Search Feishu docs and wiki by keyword. Does not start OAuth.",
            {"type": "object", "properties": {"query": {"type": "string"}, "page_size": {"type": "integer"}}, "required": ["query"], "additionalProperties": False},
        ),
        tool(
            "feishu_docs_fetch",
            "Read a Feishu cloud document. doc is a URL or token. Prefer markdown for review.",
            {"type": "object", "properties": {"doc": {"type": "string"}, "doc_format": {"type": "string", "enum": ["markdown", "xml", "im-markdown"]}}, "required": ["doc"], "additionalProperties": False},
        ),
        tool(
            "feishu_docs_update",
            "Edit a Feishu cloud document. Do not ask the user for extra authorization. command=overwrite is allowed by default for full-document rewrites. Use str_replace only for small exact edits from the latest fetch. A str_replace miss is invalid_argument, not UNAVAILABLE. Not for image upload.",
            {"type": "object", "properties": {
                "doc": {"type": "string"},
                "command": {"type": "string", "enum": ["str_replace", "overwrite", "append", "block_move_after", "block_delete"]},
                "pattern": {"type": "string"},
                "content": {"type": "string"},
                "doc_format": {"type": "string", "enum": ["markdown", "xml"]},
                "block_id": {"type": "string"},
                "src_block_ids": {"type": "string"}
            }, "required": ["doc", "command"], "additionalProperties": False},
            read_only=False,
        ),
        tool(
            "feishu_docs_media_insert",
            "Insert an image you created into a Feishu cloud doc. Do not ask permission. file must be an absolute path that already exists on this Mac. First write PNG/JPG/SVG into the Local Agent Bridge workspace; SVG is converted automatically. Then call this tool. Then feishu_docs_update command=block_move_after to place it. Chat-bubble Image Gen files, /mnt/data, and Downloads are invalid.",
            {"type": "object", "properties": {
                "doc": {"type": "string"},
                "file": {"type": "string"},
                "width": {"type": "integer"},
                "caption": {"type": "string"},
                "align": {"type": "string", "enum": ["left", "center", "right"]}
            }, "required": ["doc", "file"], "additionalProperties": False},
            read_only=False,
        ),
        tool(
            "feishu_cli",
            "Escape hatch: pass argv through to already-logged-in official lark-cli. Do not pass auth/login. Prefer named send/reply/record tools.",
            {"type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}}, "yes": {"type": "boolean"}}, "required": ["argv"], "additionalProperties": False},
            read_only=False,
        ),
    ]


def normalize_base_create_fields(fields: Any) -> List[Dict[str, Any]]:
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except json.JSONDecodeError as exc:
            raise ValueError("fields must be a JSON array of objects") from exc
    if not isinstance(fields, list):
        raise ValueError("fields must be a JSON array of objects")
    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(fields, start=1):
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except json.JSONDecodeError as exc:
                raise ValueError(f"fields item {index} must be an object") from exc
        if not isinstance(item, dict):
            raise ValueError(f"fields item {index} must be an object")
        normalized.append(item)
    return normalized


def argv_for(name: str, arguments: Dict[str, Any]) -> List[str]:
    if name in ("whoami", "feishu_status"):
        return ["whoami"]
    if name == "feishu_chat_search":
        argv = ["im", "+chat-search", "--query", str(arguments["query"])]
        if arguments.get("page_size"):
            argv += ["--page-size", str(int(arguments["page_size"]))]
        return argv + ["--format", "json", "--as", "user"]
    if name == "feishu_message_list":
        argv = ["im", "+chat-messages-list", "--chat-id", str(arguments["chat_id"])]
        if arguments.get("page_size"):
            argv += ["--page-size", str(int(arguments["page_size"]))]
        if arguments.get("page_token"):
            argv += ["--page-token", str(arguments["page_token"])]
        return argv + ["--format", "json", "--as", "user"]
    if name == "feishu_base_list_tables":
        return ["base", "+table-list", "--base-token", str(arguments["base_token"]), "--format", "json", "--as", "user"]
    if name == "feishu_base_list_fields":
        return ["base", "+field-list", "--base-token", str(arguments["base_token"]), "--table-id", str(arguments["table_id"]), "--format", "json", "--as", "user"]
    if name == "feishu_base_list_records":
        argv = ["base", "+record-list", "--base-token", str(arguments["base_token"]), "--table-id", str(arguments["table_id"])]
        if arguments.get("view_id"):
            argv += ["--view-id", str(arguments["view_id"])]
        if arguments.get("page_size"):
            argv += ["--page-size", str(int(arguments["page_size"]))]
        if arguments.get("page_token"):
            argv += ["--page-token", str(arguments["page_token"])]
        return argv + ["--format", "json", "--as", "user"]
    if name == "feishu_base_create":
        argv = ["base", "+base-create", "--name", str(arguments["name"])]
        if arguments.get("folder_token"):
            argv += ["--folder-token", str(arguments["folder_token"])]
        if arguments.get("table_name"):
            argv += ["--table-name", str(arguments["table_name"])]
        if arguments.get("fields") is not None:
            fields = normalize_base_create_fields(arguments["fields"])
            argv += ["--fields", json.dumps(fields, ensure_ascii=False)]
        if arguments.get("time_zone"):
            argv += ["--time-zone", str(arguments["time_zone"])]
        return argv + ["--format", "json", "--as", "user"]
    if name == "feishu_base_create_view":
        import json as _json
        view = arguments.get("view")
        if isinstance(view, str):
            view = _json.loads(view)
        if not isinstance(view, dict) or not view:
            view = {}
            if arguments.get("name"):
                view["name"] = arguments["name"]
            if arguments.get("type"):
                view["type"] = arguments["type"]
        argv = [
            "base", "+view-create",
            "--base-token", str(arguments["base_token"]),
            "--table-id", str(arguments["table_id"]),
            "--json", _json.dumps(view, ensure_ascii=False),
        ]
        return argv + ["--format", "json", "--as", "user"]
    if name == "feishu_message_send":
        return [
            "im", "+messages-send",
            "--chat-id", _require_text(arguments, "chat_id"),
            "--text", _require_text(arguments, "text"),
            "--as", _identity(arguments),
            "--idempotency-key", uuid.uuid4().hex[:32],
            "--format", "json",
        ]
    if name == "feishu_message_reply":
        return [
            "im", "+messages-reply",
            "--message-id", _require_text(arguments, "message_id"),
            "--text", _require_text(arguments, "text"),
            "--as", _identity(arguments),
            "--idempotency-key", uuid.uuid4().hex[:32],
            "--format", "json",
        ]
    if name == "feishu_base_create_record":
        fields = _require_object(arguments, "fields")
        return [
            "base", "+record-upsert",
            "--base-token", _require_text(arguments, "base_token"),
            "--table-id", _require_text(arguments, "table_id"),
            "--json", json.dumps(fields, ensure_ascii=False),
            "--format", "json",
            "--as", "user",
        ]
    if name == "feishu_base_update_record":
        fields = _require_object(arguments, "fields")
        return [
            "base", "+record-upsert",
            "--base-token", _require_text(arguments, "base_token"),
            "--table-id", _require_text(arguments, "table_id"),
            "--record-id", _require_text(arguments, "record_id"),
            "--json", json.dumps(fields, ensure_ascii=False),
            "--format", "json",
            "--as", "user",
        ]

    if name == "feishu_wiki_node_get":
        return [
            "wiki", "+node-get",
            "--node-token", _require_text(arguments, "node_token"),
            "--as", "user",
            "--format", "json",
        ]
    if name == "feishu_docs_search":
        argv = [
            "docs", "+search",
            "--query", _require_text(arguments, "query"),
            "--as", "user",
            "--format", "json",
        ]
        if arguments.get("page_size"):
            argv += ["--page-size", str(int(arguments["page_size"]))]
        return argv
    if name == "feishu_docs_fetch":
        argv = [
            "docs", "+fetch",
            "--doc", _require_text(arguments, "doc"),
            "--as", "user",
        ]
        fmt = arguments.get("doc_format") or "markdown"
        argv += ["--doc-format", str(fmt)]
        return argv
    if name == "feishu_docs_update":
        command = _require_text(arguments, "command")
        allowed = {"str_replace", "overwrite", "append", "block_move_after", "block_delete"}
        if command not in allowed:
            raise ValueError("unsupported docs update command")
        argv = [
            "docs", "+update",
            "--doc", _require_text(arguments, "doc"),
            "--command", command,
            "--as", "user",
        ]
        if arguments.get("pattern"):
            argv += ["--pattern", str(arguments["pattern"])]
        if arguments.get("content") is not None:
            argv += ["--content", str(arguments["content"])]
        if arguments.get("doc_format"):
            argv += ["--doc-format", str(arguments["doc_format"])]
        if arguments.get("block_id"):
            argv += ["--block-id", str(arguments["block_id"])]
        if arguments.get("src_block_ids"):
            argv += ["--src-block-ids", str(arguments["src_block_ids"])]
        return argv
    if name == "feishu_docs_media_insert":
        path = _require_text(arguments, "file")
        if not path.startswith("/"):
            raise ValueError("file must be an absolute path on this Mac")
        if not os.path.isfile(path):
            raise ValueError(
                "file does not exist on this Mac: %s. Save into the Local Agent Bridge workspace. Do not use chat sandbox paths or Downloads if the shell sandbox blocked the write." % path
            )
        if path.lower().endswith(".svg"):
            tmp = os.environ.get("TMPDIR") or "/tmp"
            os.makedirs(tmp, exist_ok=True)
            converted = os.path.join(tmp, os.path.basename(path) + ".png")
            proc = subprocess.run(["/usr/bin/qlmanage", "-t", "-s", "2800", "-o", tmp, path], capture_output=True, timeout=20)
            if proc.returncode != 0 or not os.path.isfile(converted):
                raise ValueError("svg to png conversion failed")
            path = converted
        argv = [
            "docs", "+media-insert",
            "--doc", _require_text(arguments, "doc"),
            "--file", path,
            "--as", "user",
            "--type", "image",
        ]
        if arguments.get("width"):
            argv += ["--width", str(int(arguments["width"]))]
        if arguments.get("caption"):
            argv += ["--caption", str(arguments["caption"])]
        if arguments.get("align"):
            argv += ["--align", str(arguments["align"])]
        return argv
    if name == "feishu_cli":
        return validate_feishu_cli_argv(arguments)
    raise KeyError(name)


FORBIDDEN_CLI_TOKENS = frozenset({"auth", "login", "config", "profile", "update"})
MAX_CLI_ARGV = 40
MAX_CLI_ARGV_ITEM_LEN = 8000


def _require_text(arguments: Dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _require_object(arguments: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = arguments.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a JSON object")
    return value


def _identity(arguments: Dict[str, Any]) -> str:
    value = arguments.get("identity")
    if value in (None, "", "bot"):
        return "bot"
    if value == "user":
        return "user"
    raise ValueError("identity must be user or bot")


def validate_feishu_cli_argv(arguments: Dict[str, Any]) -> List[str]:
    argv = arguments.get("argv")
    if not isinstance(argv, list):
        raise ValueError("feishu_cli requires argv: array of strings")
    if not argv:
        raise ValueError("feishu_cli argv must not be empty")
    if len(argv) > MAX_CLI_ARGV:
        raise ValueError("feishu_cli argv is limited to 40 items")
    out: List[str] = []
    for item in argv:
        if not isinstance(item, str):
            raise ValueError("feishu_cli argv items must be strings")
        if item == "":
            raise ValueError("feishu_cli argv item must not be empty")
        if len(item) > MAX_CLI_ARGV_ITEM_LEN:
            raise ValueError("feishu_cli argv item is too long")
        if item.lower() in FORBIDDEN_CLI_TOKENS:
            raise ValueError(
                "feishu_cli refuses auth/login/config/profile/update; "
                "do not start a second OAuth"
            )
        out.append(item)
    if arguments.get("yes") is True and "--yes" not in out:
        out.append("--yes")
    return out


def _field_identifiers(payload: Any) -> set:
    found = set()

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            found.add(value.strip())

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            add(node.get("field_name"))
            add(node.get("fieldName"))
            add(node.get("field_id"))
            add(node.get("fieldId"))
            add(node.get("name"))
            add(node.get("id"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


def execute_record_write(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    fields = _require_object(arguments, "fields")
    base_token = _require_text(arguments, "base_token")
    table_id = _require_text(arguments, "table_id")
    if name == "feishu_base_update_record":
        _require_text(arguments, "record_id")
    preflight = run_cli(
        [
            "base",
            "+field-list",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--format",
            "json",
            "--as",
            "user",
        ]
    )
    if not preflight.get("ok"):
        result = dict(preflight)
        result.setdefault("error", "field-list preflight failed")
        return result
    allowed = _field_identifiers(preflight.get("result"))
    unknown = [key for key in fields.keys() if key not in allowed]
    if unknown:
        return {
            "ok": False,
            "error": "unknown field keys: " + ", ".join(unknown),
        }
    return run_cli(argv_for(name, arguments))


def call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if name in ("feishu_base_create_record", "feishu_base_update_record"):
        return execute_record_write(name, arguments)
    return run_cli(argv_for(name, arguments))


def handle(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = msg.get("method")
    msg_id = msg.get("id")
    if method is None:
        return None
    if msg_id is None and str(method).startswith("notifications/"):
        return None
    if method == "initialize":
        requested = (
            (msg.get("params") or {}).get("protocolVersion")
            or DEFAULT_PROTOCOL_VERSION
        )
        protocol = (
            requested
            if requested in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION
        )
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Official lark-cli wrapper using the already-logged-in Feishu "
                    "user. Do not ask for extra authorization. Docs: feishu_docs_fetch, "
                    "feishu_docs_update command=overwrite by default, feishu_docs_media_insert "
                    "after writing a local PNG/JPG/SVG. Then block_move_after. Do not use "
                    "chat-bubble images, /mnt/data, Downloads, or office_im for cloud docs. "
                    "Do not run auth login or config init. feishu_cli is escape hatch only."
                ),
            },
        }
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools()}}
    if method == "tools/call":
        name = (msg.get("params") or {}).get("name")
        arguments = (msg.get("params") or {}).get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            result = call_tool(name, arguments)
        except KeyError:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"unknown tool: {name}"}],
                    "isError": True,
                },
            }
        except ValueError as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False, indent=2),
                    }
                ],
                "isError": not result.get("ok", False),
            },
        }
    if msg_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def read_message() -> Optional[Dict[str, Any]]:
    header_line = sys.stdin.buffer.readline()
    if not header_line:
        return None
    if header_line.lower().startswith(b"content-length:"):
        FRAME["mode"] = "headers"
        length = int(header_line.split(b":", 1)[1].strip())
        while True:
            line = sys.stdin.buffer.readline()
            if line in (b"\r\n", b"\n", b""):
                break
        body = sys.stdin.buffer.read(length)
        eprint("mcp-stdio frame=headers bytes=%s" % length)
        return json.loads(body.decode("utf-8"))
    raw = header_line.strip()
    if not raw:
        return read_message()
    FRAME["mode"] = "ndjson"
    eprint("mcp-stdio frame=ndjson bytes=%s" % len(raw))
    return json.loads(raw.decode("utf-8"))


def write_message(msg: Dict[str, Any]) -> None:
    data = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if FRAME["mode"] == "ndjson":
        sys.stdout.buffer.write(data + b"\n")
    else:
        sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
        sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def main() -> int:
    os.environ.setdefault("LARKSUITE_CLI_CONFIG_DIR", DEFAULT_CONFIG_DIR)
    eprint("mcp-stdio starting")
    while True:
        try:
            msg = read_message()
        except Exception as exc:
            eprint(f"mcp-stdio read error: {exc}")
            return 1
        if msg is None:
            eprint("mcp-stdio eof")
            return 0
        eprint("mcp-stdio msg method=%s id=%s" % (msg.get("method"), msg.get("id")))
        try:
            resp = handle(msg)
        except Exception as exc:
            eprint(f"mcp-stdio handle error: {exc}")
            if "id" in msg:
                write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": msg.get("id"),
                        "error": {"code": -32603, "message": str(exc)},
                    }
                )
            continue
        if resp is not None:
            write_message(resp)


if __name__ == "__main__":
    sys.exit(main())
