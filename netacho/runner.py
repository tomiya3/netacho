"""役を Messages API + structured outputs で呼ぶ。

- 役の本文（roles/common.md + roles/<役>.md の `---` より上）を system に
- 出力は schemas/<名前>.json に適合する JSON。受け取った直後に検証してから返す
- 呼ぶ前に上限額を確かめ、役ごとのトークン・費用・時間を run.json に積む
- 役は 1 つずつ順に呼ぶ。自動の再試行はしない（費用を読めるようにするため）
- `--reuse` のキャッシュキーは、入力・役の本文・モデル・schema のハッシュ
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from .budget import Budget, cost_of
from .schema import api_schema, validate

ROOT = Path(__file__).resolve().parent.parent
ROLES_DIR = ROOT / "roles"
OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
# 役：(モデル, effort, max_tokens)
ROLES = {
    "writer": (OPUS, "high", 32000),
    "checker": (SONNET, "medium", 8000),
    "noter": (SONNET, "medium", 4000),
    "deshi": (OPUS, "high", 8000),
    "scribe": (OPUS, "high", 32000),
    "asker": (OPUS, "high", 4000),
}


def make_client():
    """SDK の自動再試行も切る（呼んだ回数と費用を一致させるため）。"""
    import anthropic

    return anthropic.Anthropic(max_retries=0)


def check_auth(client) -> None:
    """長い処理の前に認証を確かめる。count_tokens は課金されない。認証が無ければ例外。"""
    client.messages.count_tokens(model=SONNET, messages=[{"role": "user", "content": "確認"}])


class RoleCache:
    """役の出力の控え。演者のデータを含むので repo の外に置く。"""

    def __init__(self, directory: Path, *, reuse: bool) -> None:
        self.directory = directory
        self.reuse = reuse

    @staticmethod
    def key(request: dict) -> str:
        raw = json.dumps(request, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict | None:
        path = self.directory / f"{key}.json"
        if self.reuse and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def put(self, key: str, obj: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{key}.json"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def role_prompt(name: str) -> str:
    """common.md と役の本文を連結。`---` より下は含めない。"""
    common = _body((ROLES_DIR / "common.md").read_text(encoding="utf-8"))
    role = _body((ROLES_DIR / f"{name}.md").read_text(encoding="utf-8"))
    return common + "\n\n" + role


def _body(text: str) -> str:
    return text.split("\n---\n", 1)[0].strip()


class Usage:
    """役ごとの集計。path を渡すと、呼ぶたびにそこへ書く（途中で落ちても費用が残る）。"""

    def __init__(self, path: Path | None = None, extra: dict | None = None) -> None:
        self.roles: dict[str, dict] = {}
        self.path = path
        self.extra = extra or {}  # run.json の役以外の欄
        self._lock = threading.Lock()

    def restore(self, doc: dict) -> None:
        """前の実行の分を引き継ぐ（--reuse や、build の後の note で積み足すとき）。"""
        self.roles = {r["name"]: dict(r) for r in doc["roles"]}
        keep = ("roles", "total_cost_usd", "total_seconds")
        self.extra = {**{k: v for k, v in doc.items() if k not in keep}, **self.extra}

    def add(self, role: str, model: str, resp_usage, seconds: float) -> float:
        """積んで、この呼び出しの費用を返す。"""
        with self._lock:
            cost = self._add(role, model, resp_usage, seconds)
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_name(self.path.name + ".tmp")
                tmp.write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(self.path)
            return cost

    def _add(self, role: str, model: str, resp_usage, seconds: float) -> float:
        r = self.roles.setdefault(
            role,
            {
                "name": role,
                "model": model,
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cost_usd": 0.0,
                "seconds": 0.0,
            },
        )
        inp = resp_usage.input_tokens
        out = resp_usage.output_tokens
        cr = getattr(resp_usage, "cache_read_input_tokens", 0) or 0
        cw = getattr(resp_usage, "cache_creation_input_tokens", 0) or 0
        cost = cost_of(model, inp, out, cr, cw)
        r["calls"] += 1
        r["input_tokens"] += inp
        r["output_tokens"] += out
        r["cache_read_input_tokens"] += cr
        r["cache_creation_input_tokens"] += cw
        r["cost_usd"] = round(r["cost_usd"] + cost, 5)
        r["seconds"] = round(r["seconds"] + seconds, 2)
        return cost

    def to_json(self) -> dict:
        roles = list(self.roles.values())
        return {
            **self.extra,
            "roles": roles,
            "total_cost_usd": round(sum(r["cost_usd"] for r in roles), 5),
            "total_seconds": round(sum(r["seconds"] for r in roles), 2),
        }


def call_role(
    client,
    role: str,
    user_content: list[dict],
    schema_name: str,
    usage: Usage,
    budget: Budget,
    cache: RoleCache | None = None,
):
    """役を 1 回呼んで、schema 適合の JSON を返す。キャッシュに当たれば呼ばない。"""
    model, effort, max_tokens = ROLES[role]
    system = [{"type": "text", "text": role_prompt(role), "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content": user_content}]
    output_config = {
        "format": {"type": "json_schema", "schema": api_schema(schema_name)},
        "effort": effort,
    }
    key = RoleCache.key(
        {"role": role, "model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        | {"output_config": output_config}
    )
    if cache is not None and (hit := cache.get(key)) is not None:
        validate(hit, schema_name)
        return hit
    # schema もトークンに数えるので、count_tokens にも同じ output_config を渡す
    counted = client.messages.count_tokens(
        model=model, system=system, messages=messages, output_config=output_config
    )
    budget.check(role, model, counted.input_tokens, max_tokens)  # 超えるなら呼ばない
    t0 = time.time()
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        output_config=output_config,
    ) as stream:
        resp = stream.get_final_message()
    # 検証で落ちても課金はされているので先に積む
    budget.record(role, model, usage.add(role, model, resp.usage, time.time() - t0))
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"{role}: refusal ({getattr(resp, 'stop_details', None)})")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError(f"{role}: max_tokens で途中終了（JSON が切れている）")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        raise RuntimeError(f"{role}: text ブロックが無い")
    obj = json.loads(text)
    validate(obj, schema_name)
    if cache is not None:
        cache.put(key, obj)
    return obj
