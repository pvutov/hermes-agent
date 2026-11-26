"""Registration/dispatch tests for the /delay slash command."""

import pytest


class TestDelayRegistration:
    def test_command_is_registered(self):
        from hermes_cli.commands import resolve_command

        d = resolve_command("delay")
        assert d is not None
        assert d.name == "delay"
        assert d.category == "Session"
        assert "<interval> <prompt>" in d.args_hint

    def test_listed_under_background_and_automation_in_help(self):
        from hermes_cli.commands import HELP_SESSION_SUBGROUPS

        assert "delay" in HELP_SESSION_SUBGROUPS["Background & Automation"]

    def test_handler_is_dispatched_by_name_fallback(self):
        """_slash_handler resolves _handle_<name>_command when there's no explicit table entry.

        Asserted against the mixin class directly: importing ``cli`` reads the real hermes home,
        which the test isolation guard refuses.
        """
        from hermes_cli.cli_commands_mixin import CLICommandsMixin

        # The dispatcher's name-fallback looks for _handle_<name>_command; if this is missing,
        # /delay would silently do nothing instead of erroring. It resolves through the MRO from
        # CLISessionHeartbeatMixin, so the assertion is on the composed class, not the new module.
        assert callable(getattr(CLICommandsMixin, "_handle_delay_command", None))

    def test_entry_is_not_cli_only(self):
        """/delay is a gateway command too: a messaging chat and a Desktop session run it."""
        from hermes_cli.commands import resolve_command

        assert resolve_command("delay").cli_only is False

    def test_listed_in_the_session_category_for_help(self):
        from hermes_cli.commands import COMMANDS_BY_CATEGORY

        assert any("/delay" in entries for entries in COMMANDS_BY_CATEGORY.values())

    @pytest.mark.parametrize("text,expect_interval,expect_prompt", [
        ("5h look into this jenkins warning", 18000, "look into this jenkins warning"),
        ("90m check the build", 5400, "check the build"),
        ("2d review the MR", 172800, "review the MR"),
        ("every 10m watch CI", 600, "watch CI"),   # the /heartbeat form, which /delay shares
    ])
    def test_the_handler_splits_the_argument_it_is_given(self, text, expect_interval, expect_prompt):
        """The handler's own splitter (test_delay_handler drives it through the handler itself)."""
        from hermes_cli.cli_commands_heartbeat import _parse_schedule_arg

        assert _parse_schedule_arg(text) == (expect_interval, expect_prompt)
