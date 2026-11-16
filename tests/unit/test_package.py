import nightexpress


def test_version_is_semver() -> None:
    parts = nightexpress.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
