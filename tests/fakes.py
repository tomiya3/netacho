"""偽の client。役ごとに用意した JSON を順に返し、受け取った入力を残す。API は呼ばない。"""

import json
from types import SimpleNamespace


class _Stream:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.resp


NO_ISSUES = {"schema_version": 1, "issues": []}
NO_NOTES = {"schema_version": 1, "notes": []}
ROLE_MARKS = {"構成作家役": "writer", "照合役": "checker", "ノート役": "noter", "弟子役": "deshi"}


class ScriptedClient:
    def __init__(self, **replies):
        self.replies = {role: list(r) for role, r in replies.items()}
        self.replies.setdefault("deshi", [NO_ISSUES] * 4)  # 指摘なしが既定
        self.replies.setdefault("noter", [NO_NOTES] * 4)
        self.calls = {role: [] for role in ROLE_MARKS.values()}
        self.messages = self

    @staticmethod
    def role_of(kw) -> str:
        head = kw["system"][0]["text"]
        return next(role for mark, role in ROLE_MARKS.items() if f"あなたは{mark}" in head)

    def count_tokens(self, **kw):
        return SimpleNamespace(input_tokens=1000)

    def stream(self, **kw):
        role = self.role_of(kw)
        self.calls[role].append(kw)
        reply = self.replies[role].pop(0)
        text = reply(kw) if callable(reply) else json.dumps(reply, ensure_ascii=False)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100)
        block = SimpleNamespace(type="text", text=text)
        return _Stream(SimpleNamespace(content=[block], usage=usage, stop_reason="end_turn"))


def text_of(call) -> str:
    """呼び出しの user の本文を全部つなげる。"""
    return "\n".join(b["text"] for b in call["messages"][0]["content"])
