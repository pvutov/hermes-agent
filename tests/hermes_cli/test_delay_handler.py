"""Tests for the /heartbeat and /delay CLI handlers (hermes_cli/cli_commands_heartbeat.py).

The handlers are driven against a stub CLI object, so every user-visible branch is asserted on the
text it actually prints. Importing ``cli`` is avoided on purpose (it reads the real hermes home,
which the test isolation guard refuses): the mixin is exercised directly.
"""

from __future__ import annotations

import pytest

from hermes_cli import cli_commands_heartbeat as handlers
from hermes_cli.cli_commands_heartbeat import CLISessionHeartbeatMixin
from hermes_cli.heartbeat import HeartbeatManager


@pytest.fixture()
def iso_home(tmp_path, monkeypatch):
    """Isolate SessionDB so heartbeat state never touches the real profile."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_cli import heartbeat as hb

    db = _FakeDB()
    monkeypatch.setattr(hb, "_get_session_db", lambda: db, raising=True)
    return db


@pytest.fixture()
def printed(monkeypatch):
    """Every line the handlers print, in order.

    ``_dim_line`` is stubbed to a plain two-space prefix: the real one lazily imports ``cli`` for
    the ANSI codes, and importing ``cli`` reads the real hermes home (which the isolation guard
    refuses). These cases assert the words, not the escapes.
    """
    lines: list[str] = []
    monkeypatch.setattr(handlers, "_cp", lambda *parts: lines.extend(parts))
    monkeypatch.setattr(handlers, "_dim_line", lambda text: f"  {text}")
    return lines


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


class _CLI(CLISessionHeartbeatMixin):
    """The smallest object the handlers need: a session and its heartbeat manager."""

    def __init__(self, session_id="cli-delay-sid"):
        self.session_id = session_id
        self.watchdog_starts = 0

    def _get_heartbeat_manager(self):
        return HeartbeatManager(session_id=self.session_id)

    def _start_heartbeat_watchdog(self):
        self.watchdog_starts += 1


def _text(printed) -> str:
    return "\n".join(printed)


class TestDelayHandler:
    def test_sets_a_once_delay_and_starts_the_watchdog(self, iso_home, printed):
        cli = _CLI()
        cli._handle_delay_command("/delay 90m look into the Jenkins warning")

        state = HeartbeatManager("cli-delay-sid").state
        assert state is not None
        assert (state.prompt, state.interval_seconds, state.once, state.status) == (
            "look into the Jenkins warning", 5400, True, "active")
        assert cli.watchdog_starts == 1
        text = _text(printed)
        assert "Delayed" in text and "1h30m" not in text  # format_interval(5400) == "90m"
        assert "90m" in text and "look into the Jenkins warning" in text

    def test_bare_interval_means_in_that_long(self, iso_home, printed):
        """Unlike /cron, "5h" needs no "in " prefix: a delay is one-shot by definition."""
        cli = _CLI()
        cli._handle_delay_command("/delay 5h check the build")
        assert HeartbeatManager("cli-delay-sid").state.interval_seconds == 18000

    def test_no_argument_prints_usage_and_sets_nothing(self, iso_home, printed):
        cli = _CLI()
        cli._handle_delay_command("/delay")
        assert "Usage: /delay" in _text(printed)
        assert HeartbeatManager("cli-delay-sid").state is None
        assert cli.watchdog_starts == 0

    def test_unparseable_interval_prints_usage_and_sets_nothing(self, iso_home, printed):
        cli = _CLI()
        cli._handle_delay_command("/delay soon check the build")
        assert "Usage: /delay" in _text(printed)
        assert HeartbeatManager("cli-delay-sid").state is None

    def test_below_the_floor_names_the_minimum_instead_of_the_usage(self, iso_home, printed):
        cli = _CLI()
        cli._handle_delay_command("/delay 5s check the build")
        text = _text(printed)
        assert "Interval too small" in text and "60" in text
        assert "Usage" not in text
        assert HeartbeatManager("cli-delay-sid").state is None

    def test_missing_prompt_is_refused_rather_than_set(self, iso_home, printed):
        cli = _CLI()
        cli._handle_delay_command("/delay 5h")
        assert "the prompt is required" in _text(printed)
        assert HeartbeatManager("cli-delay-sid").state is None

    def test_refuses_to_replace_an_active_recurring_heartbeat(self, iso_home, printed):
        """Two standing instructions in one session would silently drop one of them."""
        HeartbeatManager("cli-delay-sid").set("watch CI", 600)
        cli = _CLI()
        cli._handle_delay_command("/delay 5h look into the warning")

        text = _text(printed)
        assert "/heartbeat clear" in text  # names the way out
        state = HeartbeatManager("cli-delay-sid").state
        assert (state.prompt, state.once) == ("watch CI", False)  # the heartbeat survived
        assert cli.watchdog_starts == 0

    def test_replaces_an_earlier_pending_delay(self, iso_home, printed):
        """Only one delay can be pending, and a delay is the same kind of work as a delay."""
        HeartbeatManager("cli-delay-sid").delay("the old instruction", 600)
        cli = _CLI()
        cli._handle_delay_command("/delay 5h the new instruction")

        state = HeartbeatManager("cli-delay-sid").state
        assert (state.prompt, state.once) == ("the new instruction", True)


class TestHeartbeatHandler:
    def test_sets_a_recurring_heartbeat_that_is_not_a_delay(self, iso_home, printed):
        cli = _CLI()
        cli._handle_heartbeat_command("/heartbeat every 10m watch CI")

        state = HeartbeatManager("cli-delay-sid").state
        assert (state.prompt, state.interval_seconds, state.once) == ("watch CI", 600, False)
        assert cli.watchdog_starts == 1
        assert "Heartbeat set" in _text(printed)

    def test_the_every_prefix_is_optional(self, iso_home, printed):
        cli = _CLI()
        cli._handle_heartbeat_command("/heartbeat 10m watch CI")
        assert HeartbeatManager("cli-delay-sid").state.interval_seconds == 600

    def test_status_of_nothing_says_so(self, iso_home, printed):
        _CLI()._handle_heartbeat_command("/heartbeat status")
        assert "No heartbeat" in _text(printed)

    def test_clear_reports_nothing_to_clear_when_unset(self, iso_home, printed):
        _CLI()._handle_heartbeat_command("/heartbeat clear")
        assert "No heartbeat set" in _text(printed)

    def test_a_delay_reports_itself_as_a_delay_in_status(self, iso_home, printed):
        HeartbeatManager("cli-delay-sid").delay("look into the warning", 3600)
        _CLI()._handle_heartbeat_command("/heartbeat status")
        text = _text(printed)
        assert "Delay" in text and "once in" in text


class TestScheduleArgParsing:
    """The shared parser behind both commands (previously duplicated in each handler)."""

    @pytest.mark.parametrize("arg,interval,prompt", [
        ("5h look into this", 18000, "look into this"),
        ("every 10m watch CI", 600, "watch CI"),
        ("90m check the build", 5400, "check the build"),
        ("2d review the MR", 172800, "review the MR"),
        ("10m", 600, ""),                    # an interval with no prompt: the caller requires one
        ("banana x", None, "x"),             # not an interval at all
        ("", None, ""),
        ("30s too small", -1, "too small"),  # below the floor, told apart from unparseable
    ])
    def test_parses_the_documented_forms(self, arg, interval, prompt):
        assert handlers._parse_schedule_arg(arg) == (interval, prompt)

    def test_a_prompt_may_contain_spaces_and_keep_its_case(self):
        assert handlers._parse_schedule_arg("5h Look Into THIS Warning")[1] == "Look Into THIS Warning"
