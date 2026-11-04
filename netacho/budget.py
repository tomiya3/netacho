"""費用の管理。呼ぶ前に上限額を出し、1 回の実行と全体の予算を超えるなら呼ばない。

上限額 = 入力トークン数 × 入力単価 + max_tokens × 出力単価。キャッシュの割引は見込まない。
実際にかかった費用は `out/ledger.json` に足していく。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "out" / "ledger.json"
RUN_LIMIT_USD = 5.0
TOTAL_LIMIT_USD = 30.0
# USD / 1M トークン（入力、出力、キャッシュ読み、キャッシュ書き）
# 出典: https://platform.claude.com/docs/ja/about-claude/models/overview（2026-11-03 確認）
PRICE = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
}


class BudgetError(RuntimeError):
    pass


def _price(model: str) -> tuple[float, float, float, float]:
    if model not in PRICE:
        raise KeyError(f"単価表に無いモデル: {model}（budget.PRICE に出典付きで足す）")
    return PRICE[model]


def upper_bound(model: str, input_tokens: int, max_tokens: int) -> float:
    pi, po, _, _ = _price(model)
    return (input_tokens * pi + max_tokens * po) / 1e6


def cost_of(model: str, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int) -> float:
    pi, po, pr, pw = _price(model)
    return (input_tokens * pi + output_tokens * po + cache_read * pr + cache_write * pw) / 1e6


class Budget:
    """1 回の実行につき 1 つ作る。累計はファイルに持つので、実行をまたいで効く。"""

    def __init__(self, ledger: Path = LEDGER) -> None:
        self.ledger = ledger
        self.run_spent = 0.0

    def total_spent(self) -> float:
        if not self.ledger.exists():
            return 0.0
        return float(json.loads(self.ledger.read_text(encoding="utf-8"))["total_usd"])

    def check(self, role: str, model: str, input_tokens: int, max_tokens: int) -> float:
        """上限額を返す。予算を超えるなら BudgetError（呼ばない）。"""
        upper = upper_bound(model, input_tokens, max_tokens)
        if self.run_spent + upper > RUN_LIMIT_USD:
            raise BudgetError(f"{role}: 1 回の予算を超える（使用 ${self.run_spent:.2f} + 上限 ${upper:.2f})")
        total = self.total_spent()
        if total + upper > TOTAL_LIMIT_USD:
            raise BudgetError(f"{role}: 全体の予算を超える（累計 ${total:.2f} + 上限 ${upper:.2f})")
        return upper

    def record(self, role: str, model: str, cost: float) -> None:
        self.run_spent += cost
        data = {"total_usd": 0.0, "entries": []}
        if self.ledger.exists():
            data = json.loads(self.ledger.read_text(encoding="utf-8"))
        data["total_usd"] = round(data["total_usd"] + cost, 5)
        at = datetime.now(UTC).isoformat(timespec="seconds")
        data["entries"].append({"at": at, "role": role, "model": model, "cost_usd": round(cost, 5)})
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ledger.with_name(self.ledger.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.ledger)
