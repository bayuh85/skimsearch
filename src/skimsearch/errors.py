"""User-facing errors. These are printed without a traceback."""


class SkimError(Exception):
    """The command cannot finish. The message is safe to show on a terminal."""


class IndexMissing(SkimError):
    """The project has no skimsearch index yet."""
