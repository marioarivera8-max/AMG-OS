"""
Shared pytest configuration for the AMG test suite.

The UI tests (test_ui_routes.py, test_ui_review_state.py, etc.) build the
FastAPI app via ``create_app()`` and hit routes directly. After the auth
module landed, ``create_app()`` installs the auth gate by default, which
would require AMG_SESSION_SECRET in every UI test. Defaulting auth OFF
here keeps those tests focused on route behavior rather than auth, which
gets its own dedicated test file (test_auth.py + test_cli_user.py) that
overrides this default via ``monkeypatch.setenv``.
"""
import os

os.environ.setdefault("AMG_AUTH_DISABLED", "1")
