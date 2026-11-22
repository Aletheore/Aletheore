def symbol_names(entries: list[dict]) -> list[str]:
    return [entry["name"] for entry in entries]


def pytest_ignore_collect(collection_path, config):
    """Council has its own test job (.github/workflows/council.yml) and a large suite: a plain `pytest` leaves it out, while `pytest tests/council`
    (or any path or option naming council) runs it."""
    if "/tests/council/" in collection_path.as_posix() + "/" and not any("council" in str(arg) for arg in config.invocation_params.args):
        return True
    return None

