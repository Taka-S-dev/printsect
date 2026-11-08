"""Errors that the command line turns into `error: ...` and an exit code."""


class PsectError(Exception):
    """A failure the user can act on. Exit code 1."""

    exit_code = 1


class ConfigError(PsectError):
    """The configuration is missing or wrong. Exit code 2."""

    exit_code = 2
