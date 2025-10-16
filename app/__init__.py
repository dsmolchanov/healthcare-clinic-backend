"""Package init for the healthcare backend."""

__all__ = ["app"]


def __getattr__(name):  # pragma: no cover - trivial accessor
    if name == "app":
        from .main import app as fastapi_app

        return fastapi_app
    raise AttributeError(name)
