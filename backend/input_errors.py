"""Client-safe input errors raised before any model call (standard library only).

The API layer imports this module on startup, so it must never import engine
dependencies. The message is a fixed Chinese sentence chosen by the raiser; it
never contains file contents, paths outside the project or credentials.
"""


class DocumentInputError(RuntimeError):
    """A document that cannot enter evaluation, with a stable machine code.

    Subclassing RuntimeError keeps the CLI and older callers that catch
    RuntimeError working unchanged.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
