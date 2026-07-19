from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THEME = ROOT / "infrastructure/keycloak/theme/peerassist/login"


def test_peerassist_keycloak_theme_is_self_contained_and_localized() -> None:
    assert (THEME / "login.ftl").is_file()
    assert (THEME / "theme.properties").read_text(encoding="utf-8") == (
        "parent=base\nimport=common/keycloak\nstyles=css/login.css\n"
    )
    css = (THEME / "resources/css/login.css").read_text(encoding="utf-8")
    messages = (THEME / "resources/messages/messages_zh_CN.properties").read_text(
        encoding="utf-8"
    )
    assert "--pa-accent: #0f766e" in css
    assert ".pf-v5-c-login__container" in css
    assert "loginAccountTitle=登录 PeerAssist" in messages
    assert "doLogIn=登录" in messages


def test_keycloak_realm_and_image_install_peerassist_theme() -> None:
    realm = (ROOT / "infrastructure/keycloak/realm-template.json").read_text(encoding="utf-8")
    dockerfile = (ROOT / "infrastructure/compose/Dockerfile.keycloak").read_text(encoding="utf-8")
    assert '"loginTheme": "peerassist"' in realm
    assert '"defaultLocale": "zh-CN"' in realm
    assert "COPY infrastructure/keycloak/theme/peerassist /opt/keycloak/themes/peerassist" in dockerfile
