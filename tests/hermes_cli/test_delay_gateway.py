"""The gateway-side paths of /delay: the CLI's remote proxy and ``session.automation``.

A CLI attached to a shared gateway drives its heartbeat through a proxy manager, so ``/delay`` must
be reachable there too — the local ``HeartbeatManager`` alone is not the whole surface.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


class _Conn:
    """A fake gateway: runs every call against the real managers on a private store."""

    def __init__(self):
        self.calls = []

    def call(self, method, **params):
        from gateway.session_viewer_automation import invoke
        self.calls.append((params["kind"], params["call"]))
        return invoke(SimpleNamespace(), params["kind"], params["session_id"], params["call"],
                      params["args"], params["kwargs"])


@pytest.fixture()
def cli(tmp_path, monkeypatch):
    from hermes_cli import goals
    from hermes_cli.cli_loops_mixin import CLILoopsMixin
    from hermes_cli.gateway_session_store import GatewaySessionStore
    from persistence_assembly import open_persistence, profile_scope

    handle = open_persistence(profile_scope(home=tmp_path))
    monkeypatch.setattr(goals, "_get_session_db", lambda: handle)
    store = GatewaySessionStore.__new__(GatewaySessionStore)
    store.connection = _Conn()
    obj = CLILoopsMixin.__new__(CLILoopsMixin)
    obj._session_db, obj._gateway_session_open, obj.session_id = store, True, "sid-delay"
    yield SimpleNamespace(cli=obj, conn=store.connection, db=handle)
    handle.close()


def test_the_proxy_has_a_delay_method_that_reaches_the_gateway(cli):
    """Without this the /delay handler raised AttributeError on a gateway-attached CLI."""
    mgr = cli.cli._get_heartbeat_manager()
    assert type(mgr).__name__ == "GatewayHeartbeatManager"

    state = mgr.delay("look into the warning", 3600)

    assert (mgr.kind, "delay") == ("heartbeat", cli.conn.calls[-1][1])
    assert state.once is True and state.interval_seconds == 3600
    # The row the gateway owns, not this process's copy.
    stored = json.loads(cli.db.state_meta.get("heartbeat:sid-delay"))
    assert (stored["prompt"], stored["interval_seconds"], stored["once"]) == (
        "look into the warning", 3600, True)


def test_a_delay_set_through_the_proxy_reports_as_a_delay(cli):
    """The read helpers run on the mirrored state, so main's status_line sees the one-shot."""
    mgr = cli.cli._get_heartbeat_manager()
    mgr.delay("look into the warning", 3600)
    assert mgr.is_active() is True
    assert "Delay" in mgr.status_line() and "once in" in mgr.status_line()


def test_the_gateway_accepts_the_delay_call_it_is_sent(cli):
    """`session.automation` refuses a call it does not list, so 'delay' must be in the allowlist."""
    from gateway.session_viewer_automation import _CALLS, _WATCHDOG_CALLS

    assert "delay" in _CALLS["heartbeat"]
    # A delay starts the viewer's watchdog exactly like /heartbeat set does.
    assert "delay" in _WATCHDOG_CALLS


def test_a_proxied_delay_fires_once_and_retires(cli):
    import time

    mgr = cli.cli._get_heartbeat_manager()
    mgr.delay("must not be lost", 60)
    stored = json.loads(cli.db.state_meta.get("heartbeat:sid-delay"))
    stored["created_at"] = time.time() - 120
    cli.db.state_meta.set("heartbeat:sid-delay", json.dumps(stored))
    mgr.refresh()

    assert mgr.due_prompt() is not None
    assert mgr.due_prompt() is None  # never twice
    assert mgr.is_active() is False
