"""The local-only delivery notice must reach the terminal, not just the tool payload.

``cronjob(action="create"|"update")`` computes ``delivery_notice`` for a job whose output reaches
nobody, but both human-facing surfaces print their own summary rather than the tool's ``message``.
A notice that only travels in ``message`` is therefore invisible on exactly the surfaces that need
it — ``hermes cron create|edit`` (hermes_cli/cron.py) and ``/cron add|edit``
(hermes_cli/cli_cron_lines.py), the latter served to local gateway sessions too. These tests pin
the PRINT, so a payload-only regression fails here.

The tool-side payload is covered in tests/tools/test_cronjob_tools.py; this file deliberately does
not import ``cli`` (that reads the real profile home and trips the I/O guard).
"""

from __future__ import annotations

import argparse
import json

import pytest

from hermes_cli import cron as cron_cli

NOTICE = "This is a local-only cron job: its output is saved"


@pytest.fixture(autouse=True)
def _quiet_gateway_probe(monkeypatch):
    """Silence the scheduler nag: ``None`` is the documented 'probe failed' answer, which stays
    quiet, so the assertion cannot pass on some other warning's text."""
    monkeypatch.setattr(cron_cli, "_builtin_gateway_liveness", lambda: None)


def _create_result(**overrides) -> dict:
    result = {
        "success": True, "job_id": "job-1", "name": "Ping", "skill": None, "skills": [],
        "schedule": "every 2m", "repeat": "forever", "deliver": "local",
        "next_run_at": "2026-01-01T00:00:00+00:00", "job": {"job_id": "job-1", "enabled": True},
        "delivery_notice": NOTICE,
    }
    result.update(overrides)
    return result


def _update_result(**overrides) -> dict:
    job = {"job_id": "job-1", "name": "Renamed", "schedule": "every 2m", "skills": []}
    result = {"success": True, "job": job, "delivery_notice": NOTICE}
    result.update(overrides)
    return result


def _create_args() -> argparse.Namespace:
    return argparse.Namespace(
        schedule="every 2m", prompt="Ping", name="Ping", deliver=None, repeat=None,
        skill=None, skills=None, script=None, workdir=None, no_agent=False)


class TestClassicCliPrintsNotice:
    """``hermes cron create|edit`` — hermes_cli/cron.py."""

    def test_create_prints_the_notice(self, monkeypatch, capsys):
        monkeypatch.setattr(cron_cli, "_cron_api", lambda **kw: _create_result())
        assert cron_cli.cron_create(_create_args()) == 0
        out = capsys.readouterr().out
        assert NOTICE in out
        # One label only: the tool's sentence must not arrive pre-prefixed ("Note: Note: ...").
        assert "Note: Note" not in out

    def test_create_stays_quiet_without_a_notice(self, monkeypatch, capsys):
        result = _create_result()
        del result["delivery_notice"]
        monkeypatch.setattr(cron_cli, "_cron_api", lambda **kw: result)
        assert cron_cli.cron_create(_create_args()) == 0
        out = capsys.readouterr().out
        assert "Created job" in out
        assert NOTICE not in out

    def test_edit_prints_the_notice(self, monkeypatch, capsys):
        monkeypatch.setattr("cron.jobs.resolve_job_ref", lambda ref: {"id": "job-1", "skills": []})
        monkeypatch.setattr(cron_cli, "_cron_api", lambda **kw: _update_result())
        args = argparse.Namespace(job_id="job-1", deliver="origin")
        assert cron_cli.cron_edit(args) == 0
        out = capsys.readouterr().out
        assert "Updated job" in out
        assert NOTICE in out

    def test_edit_stays_quiet_without_a_notice(self, monkeypatch, capsys):
        monkeypatch.setattr("cron.jobs.resolve_job_ref", lambda ref: {"id": "job-1", "skills": []})
        result = _update_result()
        del result["delivery_notice"]
        monkeypatch.setattr(cron_cli, "_cron_api", lambda **kw: result)
        assert cron_cli.cron_edit(argparse.Namespace(job_id="job-1", name="Renamed")) == 0
        out = capsys.readouterr().out
        assert "Updated job" in out
        assert NOTICE not in out


class TestSlashCronPrintsNotice:
    """``/cron add|edit`` — hermes_cli/cli_cron_lines.py, also served to local gateway sessions."""

    @staticmethod
    def _lines(command: str, result: dict, monkeypatch) -> list[str]:
        from hermes_cli import cli_commands_mixin, cli_cron_lines

        # ``_cron`` resolves ``cli_commands_mixin._cron_api`` at call time (tests patch it there).
        monkeypatch.setattr(cli_commands_mixin, "_cron_api", lambda **kw: result)
        return cli_cron_lines.cron_command_lines(command)

    @pytest.mark.parametrize("verb", ["add", "create"])
    def test_add_prints_the_notice(self, verb, monkeypatch):
        text = "\n".join(self._lines(f'{verb} "every 2m" "Ping"', _create_result(), monkeypatch))
        assert "Created job" in text
        assert NOTICE in text
        assert "Note: Note" not in text

    def test_add_stays_quiet_without_a_notice(self, monkeypatch):
        result = _create_result()
        del result["delivery_notice"]
        text = "\n".join(self._lines('add "every 2m" "Ping"', result, monkeypatch))
        assert "Created job" in text
        assert NOTICE not in text

    def test_edit_prints_the_notice(self, monkeypatch):
        job = {"id": "job-1", "skills": []}
        monkeypatch.setattr("cron.get_job", lambda job_id: job, raising=False)
        text = "\n".join(self._lines(
            "edit job-1 --deliver origin", _update_result(), monkeypatch))
        assert "Updated job" in text
        assert NOTICE in text

    def test_edit_stays_quiet_without_a_notice(self, monkeypatch):
        job = {"id": "job-1", "skills": []}
        monkeypatch.setattr("cron.get_job", lambda job_id: job, raising=False)
        result = _update_result()
        del result["delivery_notice"]
        text = "\n".join(self._lines("edit job-1 --name Renamed", result, monkeypatch))
        assert "Updated job" in text
        assert NOTICE not in text


def test_tool_payload_carries_the_same_notice_text():
    """Guards the contract both surfaces read: the field is the bare sentence, so neither has to
    strip a label the tool already added."""
    from cron.job_args import _local_delivery_notice

    notice = _local_delivery_notice({"id": "job-1", "deliver": "origin", "origin": None}, None)
    assert notice and not notice.startswith("Note")
