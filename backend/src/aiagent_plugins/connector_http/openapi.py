"""OpenAPI 3.x / Swagger 2.0 → tool của connector HTTP (ADR-0023).

Mỗi thao tác (path + method) thành một tool; `binding` nhớ method, path, vị trí tham số. Schema được
làm sạch (inline `$ref` nội bộ, chỉ giữ từ khóa JSON Schema quen thuộc, giới hạn kích thước) để
model dùng được và core kiểm tra được.
"""

from __future__ import annotations

import json
import re
from typing import Any

from aiagent.plugin_sdk import RiskLevel, ToolSpec

MAX_SLUG = 40
MAX_DESCRIPTION = 900
MAX_SCHEMA_CHARS = 20_000
MAX_REF_DEPTH = 8
METHODS = ("get", "post", "put", "patch", "delete")
RISK = {
    "get": RiskLevel.READ,
    "post": RiskLevel.WRITE,
    "put": RiskLevel.WRITE,
    "patch": RiskLevel.WRITE,
    "delete": RiskLevel.DESTRUCTIVE,
}
# Tên tool chung của connector — thao tác OpenAPI không được trùng.
RESERVED = frozenset({"get", "send"})

_ARG_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_TYPES = frozenset({"string", "number", "integer", "boolean", "array", "object", "null"})
_KEEP = frozenset(
    {
        "type",
        "title",
        "description",
        "enum",
        "const",
        "format",
        "default",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
        "required",
    }
)


class OpenApiError(ValueError):
    pass


def slug(text: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if not text:
        text = "op"
    if text[0].isdigit():
        text = f"op_{text}"
    return text[:MAX_SLUG].rstrip("_")


def _ref(root: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        return None  # $ref ra file/URL khác: không theo
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def resolve(node: Any, root: dict[str, Any], depth: int = 0) -> Any:
    """Inline `$ref` nội bộ (giới hạn độ sâu, chống vòng lặp)."""
    if isinstance(node, dict) and isinstance(node.get("$ref"), str):
        if depth >= MAX_REF_DEPTH:
            return {}
        return resolve(_ref(root, node["$ref"]) or {}, root, depth + 1)
    return node


def clean_schema(node: Any, root: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Schema OpenAPI → JSON Schema gọn, hợp lệ với Draft 2020-12."""
    node = resolve(node, root, depth)
    if not isinstance(node, dict) or depth > MAX_REF_DEPTH:
        return {}
    out: dict[str, Any] = {}
    for key in _KEEP:
        if key in node:
            out[key] = node[key]
    kind = out.get("type")
    if isinstance(kind, list):
        kinds = [k for k in kind if k in _TYPES]
        out["type"] = kinds or "string"
    elif kind is not None and kind not in _TYPES:
        out.pop("type")  # vd. swagger "file"
    if node.get("nullable") is True and isinstance(out.get("type"), str):
        out["type"] = [out["type"], "null"]
    if isinstance(out.get("required"), list):
        out["required"] = [r for r in out["required"] if isinstance(r, str)]
    else:
        out.pop("required", None)
    if isinstance(out.get("description"), str):
        out["description"] = out["description"][:300]
    if isinstance(node.get("properties"), dict):
        out["properties"] = {
            str(k): clean_schema(v, root, depth + 1)
            for k, v in list(node["properties"].items())[:100]
        }
    if "items" in node:
        out["items"] = clean_schema(node["items"], root, depth + 1)
    extra = node.get("additionalProperties")
    if isinstance(extra, bool):
        out["additionalProperties"] = extra
    elif isinstance(extra, dict):
        out["additionalProperties"] = clean_schema(extra, root, depth + 1)
    for key in ("oneOf", "anyOf", "allOf"):
        if isinstance(node.get(key), list):
            out[key] = [clean_schema(s, root, depth + 1) for s in node[key][:20]]
    return out


def _schema_size_ok(schema: dict[str, Any]) -> bool:
    return len(json.dumps(schema, ensure_ascii=False, default=str)) <= MAX_SCHEMA_CHARS


def _path_allowed(path: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    return any(path == a.rstrip("/") or path.startswith(a.rstrip("/") + "/") for a in allowed)


def _param_schema(p: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    if "schema" in p:  # OpenAPI 3
        schema = clean_schema(p["schema"], root)
    else:  # Swagger 2: kiểu nằm thẳng trên tham số
        schema = clean_schema({k: v for k, v in p.items() if k not in ("name", "in")}, root)
    if isinstance(p.get("description"), str):
        schema["description"] = p["description"][:300]
    return schema


# Tham số file cho body multipart (model đưa nội dung dạng văn bản hoặc base64).
FILE_ARG: dict[str, Any] = {
    "type": "object",
    "description": "Tệp tải lên: text (nội dung văn bản) hoặc base64",
    "properties": {
        "filename": {"type": "string", "maxLength": 200},
        "content_type": {"type": "string", "maxLength": 100},
        "text": {"type": "string"},
        "base64": {"type": "string"},
    },
    "required": ["filename"],
    "additionalProperties": False,
}


def _media_kind(media_type: str) -> str | None:
    if media_type.endswith("json"):
        return "json"
    if media_type == "application/x-www-form-urlencoded":
        return "form"
    if media_type == "multipart/form-data":
        return "multipart"
    if media_type.startswith("text/"):
        return "text"
    return None


_MEDIA_ORDER = ("json", "form", "multipart", "text")  # ưu tiên JSON


def _is_binary(node: Any) -> bool:
    return isinstance(node, dict) and (
        node.get("format") in ("binary", "base64") or "contentMediaType" in node
    )


def _multipart_schema(raw: Any, root: dict[str, Any]) -> dict[str, Any]:
    """Schema multipart: trường nhị phân (format binary) → tham số file."""
    node = resolve(raw, root)
    schema = clean_schema(node, root)
    props = node.get("properties") if isinstance(node, dict) else None
    if isinstance(props, dict) and isinstance(schema.get("properties"), dict):
        for name, prop in props.items():
            prop = resolve(prop, root)
            if _is_binary(prop):
                schema["properties"][str(name)] = dict(FILE_ARG)
            elif (
                isinstance(prop, dict)
                and prop.get("type") == "array"
                and _is_binary(resolve(prop.get("items"), root))
            ):
                schema["properties"][str(name)] = {"type": "array", "items": dict(FILE_ARG)}
    return schema


def _body_schema(
    op: dict[str, Any], params: list[dict[str, Any]], root: dict[str, Any]
) -> tuple[dict[str, Any], bool, str] | None:
    """(schema, bắt buộc?, kiểu body: json|form|multipart|text) hoặc None nếu không có body."""
    body = resolve(op.get("requestBody"), root)
    if isinstance(body, dict):
        content = {
            str(k).split(";")[0].strip().lower(): v for k, v in (body.get("content") or {}).items()
        }
        kinds = {_media_kind(k): v for k, v in content.items()}
        for kind in _MEDIA_ORDER:
            media = kinds.get(kind)
            if not isinstance(media, dict):
                continue
            raw = media.get("schema") or {}
            if kind == "multipart":
                schema = _multipart_schema(raw, root)
            elif kind == "text":
                schema = {"type": "string"}
            else:
                schema = clean_schema(raw, root)
            return schema, bool(body.get("required")), kind
        return None
    form = [p for p in params if p.get("in") == "formData"]  # Swagger 2
    for p in params:
        if p.get("in") == "body":
            return clean_schema(p.get("schema") or {}, root), bool(p.get("required")), "json"
    if form:
        props: dict[str, Any] = {}
        multipart = False
        for p in form:
            if not isinstance(p.get("name"), str):
                continue
            if p.get("type") == "file":
                props[p["name"]] = dict(FILE_ARG)
                multipart = True
            else:
                props[p["name"]] = clean_schema(
                    {k: v for k, v in p.items() if k not in ("name", "in", "required")}, root
                )
        required = [p["name"] for p in form if p.get("required") and isinstance(p.get("name"), str)]
        form_schema: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            form_schema["required"] = required
        return form_schema, bool(required), "multipart" if multipart else "form"
    return None


def title_of(spec: dict[str, Any]) -> str:
    raw = spec.get("info")
    info: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return f"{info.get('title', 'API')} {info.get('version', '')}".strip()


def operations(
    spec: Any,
    prefix: str,
    *,
    allow_write: bool,
    allowed_paths: list[str],
    limit: int,
) -> list[ToolSpec]:
    if not isinstance(spec, dict) or not (
        str(spec.get("openapi", "")).startswith("3") or str(spec.get("swagger", "")) == "2.0"
    ):
        raise OpenApiError("Không phải tài liệu OpenAPI 3.x hoặc Swagger 2.0.")
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        raise OpenApiError("Tài liệu OpenAPI không có 'paths'.")
    tools: list[ToolSpec] = []
    used = set(RESERVED)
    for path, raw_item in paths.items():
        item = resolve(raw_item, spec)
        if not isinstance(path, str) or not isinstance(item, dict):
            continue
        if not _path_allowed(path, allowed_paths):
            continue
        common = [resolve(p, spec) for p in item.get("parameters") or []]
        for method in METHODS:
            op = item.get(method)
            if not isinstance(op, dict) or op.get("deprecated") is True:
                continue
            if method != "get" and not allow_write:
                continue
            merged: dict[tuple[str, str], dict[str, Any]] = {}
            for p in common + [resolve(p, spec) for p in op.get("parameters") or []]:
                if isinstance(p, dict) and isinstance(p.get("name"), str):
                    merged[(str(p.get("in")), p["name"])] = p
            params = list(merged.values())

            props: dict[str, Any] = {}
            required: list[str] = []
            where: dict[str, str] = {}
            skip = False
            for p in params:
                loc, name = p.get("in"), p["name"]
                if loc not in ("path", "query"):
                    continue  # header/cookie/body/formData: không đưa cho model
                if not _ARG_RE.match(name) or name == "body":
                    if loc == "path":
                        skip = True  # không điền được path → bỏ thao tác
                    continue
                props[name] = _param_schema(p, spec)
                where[name] = loc
                if loc == "path" or p.get("required"):
                    required.append(name)
            if skip:
                continue
            body = _body_schema(op, params, spec)
            body_kind: str | None = None
            if body is not None:
                props["body"], body_required, body_kind = body
                if body_required:
                    required.append("body")

            schema: dict[str, Any] = {
                "type": "object",
                "properties": props,
                "additionalProperties": False,
            }
            if required:
                schema["required"] = required
            if not _schema_size_ok(schema):
                continue

            name = slug(str(op.get("operationId") or f"{method}_{path}"))
            base, n = name, 2
            while name in used:
                suffix = f"_{n}"
                name = base[: MAX_SLUG - len(suffix)] + suffix
                n += 1
            used.add(name)
            summary = str(op.get("summary") or op.get("description") or "").strip()
            description = f"{method.upper()} {path}" + (f" — {summary}" if summary else "")
            tools.append(
                ToolSpec(
                    name=f"{prefix}.{name}",
                    description=description[:MAX_DESCRIPTION],
                    risk=RISK[method],
                    input_schema=schema,
                    binding={
                        "method": method.upper(),
                        "path": path,
                        "params": where,
                        "body": body_kind if body is not None else None,
                    },
                )
            )
            if len(tools) >= limit:
                return tools
    return tools
