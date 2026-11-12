"""Council's tests need the optional `council` extra. Without it they are skipped, so the ordinary test jobs stay as they were."""

try:
    import fastapi  # noqa: F401
    import nacl  # noqa: F401
    import platformdirs  # noqa: F401
    import psycopg  # noqa: F401
    import textual  # noqa: F401
    import uvicorn  # noqa: F401
except ImportError:
    collect_ignore_glob = ["*"]
