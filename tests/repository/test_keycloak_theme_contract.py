from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THEME = ROOT / "infrastructure/keycloak/theme/peerassist/login"


def test_peerassist_keycloak_theme_is_self_contained_and_localized() -> None:
    assert (THEME / "login.ftl").is_file()
    assert (THEME / "register.ftl").is_file()
    assert (THEME / "register.ftl").stat().st_mode & 0o004
    assert (THEME / "resources/css/login.css").stat().st_mode & 0o004
    properties = (THEME / "theme.properties").read_text(encoding="utf-8")
    assert "parent=base" in properties
    assert "styles=css/login.css" in properties
    assert "cacheThemes=false" in properties
    assert "cacheTemplates=false" in properties
    assert "kcInputClass=pf-v5-c-form-control" in properties
    css = (THEME / "resources/css/login.css").read_text(encoding="utf-8")
    messages = (THEME / "resources/messages/messages_zh_CN.properties").read_text(
        encoding="utf-8"
    )
    assert "--pa-accent: #0f766e" in css
    assert ".pf-v5-c-login__container" in css
    assert ".brand-context" in css
    assert "loginAccountTitle=登录 PeerAssist" in messages
    assert "doLogIn=登录" in messages
    assert "registerTitle=创建账号" in messages
    assert "email=邮箱" in messages


def test_peerassist_registration_uses_the_same_branded_form_structure() -> None:
    template = (THEME / "register.ftl").read_text(encoding="utf-8")
    css = (THEME / "resources/css/login.css").read_text(encoding="utf-8")

    assert 'class="login-pf register-page"' in template
    assert 'class="pf-v5-c-login__container"' in template
    assert 'id="kc-register-form"' in template
    assert 'class="pf-v5-c-form-control"' in template
    assert ".register-page .pf-v5-c-login__container" in css
    assert ".registration-password-field" in css


def test_peerassist_login_is_a_compact_single_task_entrypoint() -> None:
    template = (THEME / "login.ftl").read_text(encoding="utf-8")
    css = (THEME / "resources/css/login.css").read_text(encoding="utf-8")

    assert 'class="brand-context"' in template
    assert "brand-features" not in template
    assert ".pf-v5-c-login__container {" in css
    assert "grid-template-columns" not in css
    assert "width: min(460px, 100%)" in css


def test_keycloak_realm_and_image_install_peerassist_theme() -> None:
    realm = (ROOT / "infrastructure/keycloak/realm-template.json").read_text(encoding="utf-8")
    dockerfile = (ROOT / "infrastructure/compose/Dockerfile.keycloak").read_text(encoding="utf-8")
    assert '"loginTheme": "peerassist"' in realm
    assert '"defaultLocale": "zh-CN"' in realm
    assert "COPY infrastructure/keycloak/theme/peerassist /opt/keycloak/themes/peerassist" in dockerfile
