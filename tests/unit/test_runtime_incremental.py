"""Compact streaming reducer retains trading semantics without copying raw history."""

from ai_trading_lab.runtime.engine import reducer
from tests.unit.test_experiment_runner import tape_with_exit
from tests.unit.test_runtime_engine import config, tick


def test_incremental_matches_replay_and_checkpoints_exclude_original_evidence() -> None:
    cfg = config()
    first, second, third = tape_with_exit()
    tape = (tick(first, 1), tick(second, 2), tick(third, 3))
    from ai_trading_lab.runtime.engine import RuntimeReducer

    runtime = RuntimeReducer(cfg)
    for index, event in enumerate(tape, 1):
        state = runtime.apply(event)
        expected = reducer(cfg, tape[:index])
        assert state == {k: v for k, v in expected.items() if k not in {"history", "native_events"}}
        assert "source_entry" not in str(state)
    count = state["history_count"]
    assert isinstance(count, int) and count >= 2


def test_replay_records_compact_history_digest_count() -> None:
    first, second, third = tape_with_exit()
    state = reducer(config(), (tick(first, 1), tick(second, 2), tick(third, 3)))
    commands = state["history"]
    assert isinstance(commands, list)
    assert state.get("history_count") == len(commands)
