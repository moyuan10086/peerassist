from pathlib import Path


def test_unauthenticated_login_uses_a_dedicated_entry_layout() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")

    assert "function LoginLayout(" in source
    assert 'if (activeWindow === "login" && !authSession.authenticated)' in source
    assert "return <LoginLayout available={authSession.available} />;" in source


def test_login_layout_has_one_primary_sign_in_action() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")
    login_layout = source[source.index("function LoginLayout(") : source.index("function AdminWindow(")]

    assert 'className="login-entry-primary"' in login_layout
    assert login_layout.count('>\n            <LogIn') == 1
    assert 'window.location.assign("/api/v1/auth/login?return_path=/paper")' in login_layout
