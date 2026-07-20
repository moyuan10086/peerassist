from pathlib import Path


def test_model_settings_frontend_formats_api_errors_and_sends_csrf() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")

    assert "function apiErrorMessage(" in source
    assert source.count("apiErrorMessage(payload,") >= 3
    drawer = source[source.index("function ModelSettingsDrawer(") : source.index("function StatusStrip(")]
    assert '"X-CSRF-Token": cookieValue("peerassist_csrf")' in drawer
    assert "payload.error || \"模型列表拉取失败\"" not in drawer
