"""役を Messages API + structured outputs で呼ぶ。

- 役の本文（roles/common.md + roles/<役>.md の `---` より上）を system に
- 出力は schemas/<名前>.json に適合する JSON。受け取った直後に検証してから返す
- 呼ぶ前に上限額を確かめ、役ごとのトークン・費用・時間を run.json に積む
- 役は 1 つずつ順に呼ぶ。自動の再試行はしない（費用を読めるようにするため）
"""

from __future__ import annotations

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
}


def role_prompt(name: str) -> str:
    """common.md と役の本文を連結。`---` より下は含めない。"""
    common = _body((ROLES_DIR / "common.md").read_text(encoding="utf-8"))
    role = _body((ROLES_DIR / f"{name}.md").read_text(encoding="utf-8"))
    return common + "\n\n" + role


def _body(text: str) -> str:
    return text.split("\n---\n", 1)[0].strip()


class Usage:
    """役ごとの集計。path を渡すと、呼ぶたびにそこへ書く（途中で落ちても費用が残る）。"""

    def __init__(self, path: Path | None = None) -> None:
        self.roles: dict[str, dict] = {}
        self.path = path
        self._lock = threading.Lock()

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
            "roles": roles,
            "total_cost_usd": round(sum(r["cost_usd"] for r in roles), 5),
            "total_seconds": round(sum(r["seconds"] for r in roles), 2),
        }


def call_role(client, role: str, user_content: list[dict], schema_name: str, usage: Usage, budget: Budget):
    """役を 1 回呼んで、schema 適合の JSON を返す。"""
    model, effort, max_tokens = ROLES[role]
    system = [{"type": "text", "text": role_prompt(role), "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content": user_content}]
    output_config = {
        "format": {"type": "json_schema", "schema": api_schema(schema_name)},
        "effort": effort,
    }
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
    return obj
