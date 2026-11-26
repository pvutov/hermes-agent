"""Tests for /delay — a one-shot deferred instruction built on the session heartbeat."""

import time

import pytest


@pytest.fixture()
def iso_home(tmp_path, monkeypatch):
    """Isolate SessionDB so heartbeat state never touches the real profile."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_cli import heartbeat as hb

    db = _FakeDB()  # one store per test: the rows mapping must not leak between cases
    monkeypatch.setattr(hb, "_get_session_db", lambda: db, raising=True)
    return db


def _elapse(mgr, db, seconds):
    """Move the delay's deadline into the past, IN THE STORE.

    due_prompt() rereads the stored row and compare-and-sets against it, so poking
    ``mgr.state.created_at`` alone would be discarded as a stale claim.
    """
    from hermes_cli.heartbeat import save_heartbeat

    mgr.state.created_at = time.time() - seconds
    save_heartbeat(mgr.session_id, mgr.state)


class _FakeDB:
    """Minimal SessionDB stand-in: the meta store heartbeat persistence uses."""

    class _Store:
        def __init__(self, rows):
            self._rows = rows

        def get(self, key):
            return self._rows.get(key)

        def set(self, key, value):
            self._rows[key] = value

        def list_prefix(self, prefix):
            return [_Entry(k, v) for k, v in self._rows.items() if k.startswith(prefix)]

    def __init__(self):
        self.rows = {}
        self.state_meta = self._Store(self.rows)


class _Entry:
    def __init__(self, key, value):
        self.key, self.value = key, value


class TestDelayState:
    def test_once_renders_as_a_delayed_instruction_not_a_cadence(self):
        from hermes_cli.heartbeat import HeartbeatState

        s = HeartbeatState(prompt="check build", interval_seconds=18000, once=True)
        rendered = s.render_prompt()
        assert "deferred for 5h" in rendered
        # The recurring wording would make a one-shot sound like the first of a series.
        assert "recurring" not in rendered
        assert "check build" in rendered

    def test_recurring_still_renders_as_heartbeat(self):
        from hermes_cli.heartbeat import HeartbeatState

        s = HeartbeatState(prompt="watch ci", interval_seconds=600)
        assert "recurring instruction" in s.render_prompt()

    def test_once_survives_json_round_trip(self):
        from hermes_cli.heartbeat import HeartbeatState

        s = HeartbeatState(prompt="x", interval_seconds=60, once=True)
        assert HeartbeatState.from_json(s.to_json()).once is True

    def test_legacy_row_without_once_defaults_to_recurring(self):
        from hermes_cli.heartbeat import HeartbeatState

        # Rows written before this field existed must not become one-shots.
        legacy = HeartbeatState.from_json('{"prompt":"x","interval_seconds":60,"status":"active"}')
        assert legacy.once is False


class TestDelayManager:
    def test_delay_is_not_due_until_the_interval_elapses(self, iso_home):
        from hermes_cli.heartbeat import HeartbeatManager

        mgr = HeartbeatManager("sess-wait")
        mgr.delay("look into the warning", 3600)
        assert mgr.due_prompt() is None
        assert mgr.is_active() is True

    def test_fires_once_then_retires(self, iso_home):
        from hermes_cli.heartbeat import HeartbeatManager

        mgr = HeartbeatManager("sess-once")
        mgr.delay("look into the warning", 60)
        _elapse(mgr, iso_home, 120)

        assert mgr.due_prompt() is not None
        assert mgr.due_prompt() is None          # never twice
        assert mgr.is_active() is False

    def test_retired_delay_is_gone_for_a_fresh_manager(self, iso_home):
        from hermes_cli.heartbeat import HeartbeatManager

        mgr = HeartbeatManager("sess-fresh")
        mgr.delay("x", 60)
        _elapse(mgr, iso_home, 120)
        mgr.due_prompt()

        fresh = HeartbeatManager("sess-fresh")
        assert fresh.is_active() is False
        assert fresh.due_prompt() is None

    def test_abandon_restores_a_fired_delay_whose_turn_never_started(self, iso_home):
        """The reason this matters: a delay retires as it fires, so losing the turn would lose
        the deferred work entirely. abandon_fire must put it back."""
        from hermes_cli.heartbeat import HeartbeatManager

        mgr = HeartbeatManager("sess-abandon")
        mgr.delay("must not be lost", 60)
        _elapse(mgr, iso_home, 120)

        assert mgr.due_prompt() is not None
        assert mgr.abandon_fire() is True
        assert mgr.is_active() is True
        assert mgr.due_prompt() is not None

    def test_recurring_heartbeat_is_unaffected(self, iso_home):
        from hermes_cli.heartbeat import HeartbeatManager

        mgr = HeartbeatManager("sess-hb")
        mgr.set("watch ci", 60)
        _elapse(mgr, iso_home, 120)

        assert mgr.due_prompt() is not None
        assert mgr.is_active() is True   # recurring survives its fire
        assert mgr.due_prompt() is None  # and the anchor moved

    def test_status_line_calls_a_delay_a_delay(self, iso_home):
        from hermes_cli.heartbeat import HeartbeatManager

        mgr = HeartbeatManager("sess-status")
        mgr.delay("check build", 3600)
        line = mgr.status_line()
        assert "Delay" in line and "once in" in line
        assert "Heartbeat (every" not in line


class TestDelayIntervalParsing:
    @pytest.mark.parametrize("token,seconds", [
        ("5h", 18000), ("90m", 5400), ("2d", 172800), ("60s", 60), ("every 10m", 600),
    ])
    def test_accepts_the_documented_forms(self, token, seconds):
        from hermes_cli.heartbeat import parse_interval

        assert parse_interval(token) == seconds

    def test_below_the_floor_is_reported_separately_from_unparseable(self):
        from hermes_cli.heartbeat import parse_interval

        assert parse_interval("30s") == -1   # too small
        assert parse_interval("banana") is None  # not an interval
