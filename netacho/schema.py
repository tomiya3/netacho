"""役の出力の検証。runner が JSON を受け取った直後、保存の前に呼ぶ。

形は `schemas/*.json`（JSON Schema draft-07）。同じファイルを Messages API の
structured outputs にも渡す。
"""

from __future__ import annotations

import copy
import json
from functools import cache
from pathlib import Path

from jsonschema import Draft7Validator

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


class SchemaError(ValueError):
    pass


@cache
def load(name: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{name}.json").read_text(encoding="utf-8"))


# structured outputs が受け付けないキーワード（400 になる）。検証は手元の validate で行う
_API_UNSUPPORTED = ("minimum", "maximum", "multipleOf", "minLength", "maxLength", "maxItems", "minItems")


@cache
def api_schema(name: str) -> dict:
    """Messages API の output_config.format に渡す形。未対応の制約を description に退避する。"""
    s = copy.deepcopy(load(name))
    s.pop("$schema", None)
    s.pop("title", None)
    _strip(s)
    return s


def _strip(node) -> None:
    if isinstance(node, dict):
        notes = [f"{k}={node.pop(k)}" for k in list(node.keys()) if k in _API_UNSUPPORTED]
        if notes:
            node["description"] = (node.get("description", "") + " (" + ", ".join(notes) + ")").strip()
        for v in node.values():
            _strip(v)
    elif isinstance(node, list):
        for v in node:
            _strip(v)


def validate(obj, name: str) -> None:
    """schema に合わなければ SchemaError（最初の数件をまとめて）。"""
    errors = sorted(Draft7Validator(load(name)).iter_errors(obj), key=lambda e: list(e.path))
    if errors:
        msgs = [f"{'/'.join(str(p) for p in e.path) or '(root)'}: {e.message}" for e in errors[:5]]
        raise SchemaError(f"{name}.json に合わない: " + " | ".join(msgs))
