"""土台（費用の管理、役の呼び出し、schema、前処理の純粋な部分）のテスト。API は呼ばない。"""

import json
from types import SimpleNamespace

import pytest

from netacho import budget as budget_module
from netacho import runner, schema
from netacho.budget import Budget, BudgetError, cost_of, upper_bound
from netacho.preprocess.speech import number_segments

OPUS = "claude-opus-5-5"


def test_upper_bound_ignores_cache_discount():
    assert upper_bound(OPUS, 1_000_000, 1_000_000) == 24.0


def test_cost_of_counts_cache():
    assert cost_of(OPUS, 0, 0, 1_000_000, 1_000_000) == pytest.approx(5.2)


def test_unknown_model_raises():
    with pytest.raises(KeyError):
        upper_bound("unknown", 1, 1)


def test_budget_stops_over_run_limit(tmp_path):
    b = Budget(tmp_path / "ledger.json")
    b.check("writer", OPUS, 0, 100_000)  # 上限 $2
    b.record("writer", OPUS, 4.0)
    with pytest.raises(BudgetError, match="1 回"):
        b.check("writer", OPUS, 0, 100_000)


def test_budget_stops_over_total_across_runs(tmp_path):
    ledger = tmp_path / "ledger.json"
    Budget(ledger).record("writer", OPUS, 29.5)
    assert Budget(ledger).total_spent() == 29.5
    with pytest.raises(BudgetError, match="全体"):
        Budget(ledger).check("writer", OPUS, 0, 100_000)


def test_ledger_accumulates(tmp_path):
    ledger = tmp_path / "out" / "ledger.json"
    Budget(ledger).record("writer", OPUS, 0.5)
    Budget(ledger).record("deshi", OPUS, 0.25)
    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert data["total_usd"] == 0.75
    assert [e["role"] for e in data["entries"]] == ["writer", "deshi"]


def test_number_segments():
    segs = number_segments(
        [{"start": 0.0, "end": 1.0}, {"start": 1.5, "end": 2.0}, {"start": 1.9, "end": 3.0}]
    )
    assert [s["id"] for s in segs] == ["seg-0001", "seg-0002", "seg-0003"]
    assert [s["pause_after"] for s in segs] == [0.5, 0.0, None]  # 重なりは 0、最後は None


@pytest.fixture
def toy_schema(tmp_path, monkeypatch):
    (tmp_path / "toy.json").write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "toy",
                "type": "object",
                "properties": {"n": {"type": "integer", "minimum": 1, "description": "数"}},
                "required": ["n"],
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "SCHEMA_DIR", tmp_path)
    schema.load.cache_clear()
    schema.api_schema.cache_clear()
    yield
    schema.load.cache_clear()
    schema.api_schema.cache_clear()


def test_api_schema_moves_unsupported_to_description(toy_schema):
    s = schema.api_schema("toy")
    assert "$schema" not in s and "title" not in s
    assert "minimum" not in s["properties"]["n"]
    assert "minimum=1" in s["properties"]["n"]["description"]


def test_validate_keeps_full_constraints(toy_schema):
    schema.validate({"n": 1}, "toy")
    with pytest.raises(schema.SchemaError):
        schema.validate({"n": 0}, "toy")


class FakeStream:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.resp


class FakeClient:
    """count_tokens と stream だけを持つ偽物。呼ばれた回数を数える。"""

    def __init__(self, text='{"n": 1}', stop_reason="end_turn", input_tokens=100):
        self.stream_calls = 0
        self.text, self.stop_reason, self.input_tokens = text, stop_reason, input_tokens
        self.messages = self

    def count_tokens(self, **kw):
        return SimpleNamespace(input_tokens=self.input_tokens)

    def stream(self, **kw):
        self.stream_calls += 1
        self.kwargs = kw
        usage = SimpleNamespace(input_tokens=1000, output_tokens=1000)
        block = SimpleNamespace(type="text", text=self.text)
        return FakeStream(SimpleNamespace(content=[block], usage=usage, stop_reason=self.stop_reason))


@pytest.fixture
def roles(tmp_path, monkeypatch):
    (tmp_path / "common.md").write_text("共通\n---\n下は使わない", encoding="utf-8")
    (tmp_path / "writer.md").write_text("役の本文", encoding="utf-8")
    monkeypatch.setattr(runner, "ROLES_DIR", tmp_path)


def _call(client, tmp_path):
    b = Budget(tmp_path / "ledger.json")
    usage = runner.Usage(tmp_path / "run.json")
    return runner.call_role(client, "writer", [{"type": "text", "text": "x"}], "toy", usage, b), b


def test_call_role_records_cost_and_returns_json(toy_schema, roles, tmp_path):
    client = FakeClient()
    obj, b = _call(client, tmp_path)
    assert obj == {"n": 1}
    assert client.kwargs["system"][0]["text"] == "共通\n\n役の本文"  # --- より下は含めない
    assert client.kwargs["output_config"]["effort"] == "high"
    assert b.total_spent() == pytest.approx(0.024)
    assert json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))["roles"][0]["calls"] == 1


def test_call_role_over_budget_never_calls_api(toy_schema, roles, tmp_path):
    client = FakeClient(input_tokens=10_000_000)
    with pytest.raises(BudgetError):
        _call(client, tmp_path)
    assert client.stream_calls == 0


def test_call_role_does_not_retry_but_keeps_cost(toy_schema, roles, tmp_path):
    client = FakeClient(text='{"n": 0}')
    with pytest.raises(schema.SchemaError):
        _call(client, tmp_path)
    assert client.stream_calls == 1
    assert Budget(tmp_path / "ledger.json").total_spent() > 0  # 検証で落ちても費用は残る


def test_call_role_max_tokens_is_error(toy_schema, roles, tmp_path):
    with pytest.raises(RuntimeError, match="max_tokens"):
        _call(FakeClient(stop_reason="max_tokens"), tmp_path)


def test_default_ledger_is_under_out():
    assert budget_module.LEDGER.parent.name == "out"
