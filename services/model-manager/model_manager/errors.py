from __future__ import annotations


class ModelManagerError(RuntimeError):
    """Base exception rendered as a concise user-facing CLI error."""


class InvalidRepository(ModelManagerError):
    pass


class RepositoryAccessError(ModelManagerError):
    pass


class NoGGUFFiles(ModelManagerError):
    pass


class AmbiguousSelection(ModelManagerError):
    def __init__(self, message: str, choices: list[str]) -> None:
        super().__init__(message)
        self.choices = choices


class DownloadError(ModelManagerError):
    pass


class IntegrityError(ModelManagerError):
    pass


class ModelNotFound(ModelManagerError):
    pass


class UnsafePath(ModelManagerError):
    pass
