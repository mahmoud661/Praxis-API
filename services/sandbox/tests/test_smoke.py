def test_import():
    """Smoke-test: the app module can be imported without external services."""
    import importlib
    importlib.import_module("app.infrastructure.config.env")
