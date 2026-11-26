"""``/heartbeat`` and ``/delay`` — the CLI's session-heartbeat slash commands.

Split out of ``cli_commands_mixin`` (file-line ratchet #68779): the two commands are one family —
``/delay`` is a heartbeat with ``once`` set — so they read best together, next to the manager they
drive. ``CLICommandsMixin`` inherits this class, so the handlers and ``_session_manager`` keep their
names and every existing ``self._handle_heartbeat_command(...)`` call site is unchanged.

``cli.py``-internal symbols are imported lazily inside the helpers (``cli`` imports the mixin).
"""

from __future__ import annotations

from typing import Optional

from hermes_cli.cli_commands_session_tools import _command_arg, _cp, _dim_line, _t


def _parse_schedule_arg(arg: str) -> tuple[Optional[int], str]:
    """``(interval_seconds, prompt)`` from ``<interval> <prompt>`` or ``every <interval> <prompt>``.

    ``interval`` is None when the first word is not an interval, and -1 when it is below the floor
    (``parse_interval``'s convention) so the caller can report the two apart. Shared by
    ``/heartbeat`` and ``/delay``, which differ only in what they do with the interval once parsed.
    """
    from hermes_cli.heartbeat import parse_interval

    tokens = arg.split(None, 2)
    if tokens and tokens[0].lower() == "every" and len(tokens) >= 2:
        return parse_interval(f"every {tokens[1]}"), (tokens[2] if len(tokens) > 2 else "")
    if not tokens:
        return None, ""
    interval = parse_interval(tokens[0])
    return interval, arg[len(tokens[0]):].strip()


def _interval_too_small(key: str) -> str:
    """The ``<key>`` line: a triggered sub-floor interval, naming the active minimum."""
    from hermes_cli.heartbeat import MIN_INTERVAL_SECONDS

    return _t(key, min_seconds=MIN_INTERVAL_SECONDS)


class CLISessionHeartbeatMixin:
    """``/heartbeat`` (recurring re-entry) and ``/delay`` (one-shot deferred work) for the CLI."""

    def _session_manager(self, getter, label: str):
        """The session-scoped manager from ``getter()``, or None after the standard dim
        "<label> unavailable (no active session)." line."""
        mgr = getter()
        if mgr is None:
            _cp(_dim_line(_t("shared.unavailable_no_session", label=label)))
        return mgr

    def _handle_heartbeat_command(self, cmd: str) -> None:
        """Dispatch /heartbeat: set / status / pause / resume / clear. ``/heartbeat every 10m <prompt>``
        sets the session's one recurring instruction, injected as a normal user turn when due.
        Session-scoped and in-process — use `hermes cron` for durable schedules."""
        from hermes_cli.heartbeat import format_interval
        arg = _command_arg(cmd)
        lower = arg.lower()
        mgr = self._session_manager(self._get_heartbeat_manager, _t("heartbeat.label"))
        if mgr is None:
            return
        if not arg or lower == "status":
            _cp(f"  {mgr.status_line()}")
        elif lower == "pause":
            state = mgr.pause()
            _cp(f"  {_t('heartbeat.paused', prompt=state.prompt)}" if state
                else _dim_line(_t("heartbeat.none_set")))
        elif lower == "resume":
            state = mgr.resume()
            if state is None:
                _cp(_dim_line(_t("heartbeat.none_to_resume")))
            else:
                self._start_heartbeat_watchdog()
                _cp(f"  {_t('heartbeat.resumed', interval=format_interval(state.interval_seconds), prompt=state.prompt)}")
        elif lower in {"clear", "stop", "off"}:
            _cp(f"  {_t('heartbeat.cleared')}" if mgr.clear() else _dim_line(_t("heartbeat.none_set")))
        else:
            self._heartbeat_set(mgr, arg)

    def _heartbeat_set(self, mgr, arg: str) -> None:
        """Set: ``/heartbeat every 10m <prompt>`` (also accepts ``10m <prompt>``)."""
        from hermes_cli.heartbeat import format_interval
        interval, prompt = _parse_schedule_arg(arg)
        if interval is None:
            return _cp(f"  {_t('heartbeat.usage')}", _dim_line(_t("heartbeat.usage_also")))
        if interval < 0:
            return _cp(f"  {_interval_too_small('heartbeat.interval_too_small')}")
        if not prompt.strip():
            return _cp(f"  {_t('heartbeat.prompt_required')}")
        state = self._heartbeat_attempt(_t("heartbeat.invalid_label"), mgr.set, prompt, interval)
        if state is None:
            return
        self._start_heartbeat_watchdog()
        _cp(f"  {_t('heartbeat.set', interval=format_interval(state.interval_seconds), prompt=state.prompt)}",
            _dim_line(_t("heartbeat.set_note")))

    def _handle_delay_command(self, cmd: str) -> None:
        """Dispatch /delay <interval> <prompt> — defer one instruction into THIS session.

        Implemented on the session heartbeat (one-shot): the deferred prompt re-enters this
        conversation, with its context intact, as an ordinary user turn once the interval elapses
        and the session is idle. Nothing is inferred at delay time, so a /delay costs no backend
        load until it fires.
        """
        from hermes_cli.heartbeat import format_interval
        # Bare "5h" means "in 5h" here: unlike /cron, a delay is one-shot by definition, so the
        # explicit "in " prefix the schedule parser needs would be noise.
        interval, prompt = _parse_schedule_arg(_command_arg(cmd))
        if interval is None:
            return _cp(f"  {_t('delay.usage')}", _dim_line(_t("delay.usage_also")))
        if interval < 0:
            return _cp(f"  {_interval_too_small('delay.interval_too_small')}")
        if not prompt:
            return _cp(f"  {_t('delay.prompt_required')}")
        mgr = self._session_manager(self._get_heartbeat_manager, _t("heartbeat.label"))
        if mgr is None:
            return
        # A delay is a single instruction: replacing a standing heartbeat would silently drop it,
        # so refuse rather than clobber. An earlier one-shot may be replaced (it is the same kind
        # of work, and only one delay can be pending).
        if mgr.is_active() and not getattr(mgr.state, "once", False):
            return _cp(f"  {_t('delay.heartbeat_active')}")
        state = self._heartbeat_attempt(_t("delay.invalid_label"), mgr.delay, prompt, interval)
        if state is None:
            return
        self._start_heartbeat_watchdog()
        _cp(f"  {_t('delay.set', interval=format_interval(state.interval_seconds), prompt=state.prompt)}",
            _dim_line(_t("delay.set_note")))

    @staticmethod
    def _heartbeat_attempt(label: str, fn, *args):
        """``fn(*args)``, or None after printing ``  <label>: <exc>`` for a manager refusal."""
        try:
            return fn(*args)
        except ValueError as exc:
            _cp(f"  {label}: {exc}")
            return None
