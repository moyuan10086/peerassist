<!DOCTYPE html>
<html lang="${(locale.currentLanguageTag!'zh-CN')}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>${msg("loginAccountTitle")}</title>
    <link rel="stylesheet" href="${url.resourcesPath}/css/login.css">
</head>
<body class="login-pf">
<div class="pf-v5-c-login">
    <div class="pf-v5-c-login__container">
        <header id="kc-header">
            <div class="brand-lockup" aria-label="PeerAssist">
                <span class="brand-mark" aria-hidden="true">PA</span>
                <span class="brand-name">PeerAssist</span>
            </div>
            <div class="brand-copy">
                <p class="brand-kicker">教师智能审稿工作台</p>
                <h2>让每一篇论文，<br>都得到有依据的反馈。</h2>
                <p>集中阅读、核查证据、整理审稿意见，把时间留给真正重要的学术判断。</p>
            </div>
            <div class="brand-foot">安全登录 · 论文数据仅对项目成员可见</div>
        </header>
        <main class="pf-v5-c-login__main">
            <div class="pf-v5-c-login__main-header">
                <p class="form-kicker">欢迎回来</p>
                <h1 class="pf-v5-c-title pf-m-3xl" id="kc-page-title">${msg("loginAccountTitle")}</h1>
                <p class="form-subtitle">登录后继续处理你的审稿项目</p>
            </div>
            <div class="pf-v5-c-login__main-body">
                <#if message?has_content>
                    <div class="pf-v5-c-alert pf-m-inline pf-m-danger" role="alert">
                        <span>${kcSanitize(message.summary)?no_esc}</span>
                    </div>
                </#if>
                <#if realm.password>
                    <form id="kc-form-login" class="pf-v5-c-form pf-v5-u-w-100" action="${url.loginAction}" method="post" novalidate="novalidate">
                        <#if !usernameHidden??>
                            <div class="pf-v5-c-form__group">
                                <label for="username" class="pf-v5-c-form__label">
                                    <span class="pf-v5-c-form__label-text">${msg("username")}</span>
                                </label>
                                <input id="username" name="username" class="pf-v5-c-form-control" value="${(login.username!'')}" type="text" autofocus autocomplete="username">
                            </div>
                        </#if>
                        <div class="pf-v5-c-form__group">
                            <label for="password" class="pf-v5-c-form__label">
                                <span class="pf-v5-c-form__label-text">${msg("password")}</span>
                            </label>
                            <div class="password-field">
                                <input id="password" name="password" class="pf-v5-c-form-control" type="password" autofocus="${(usernameHidden??)?c}" autocomplete="current-password">
                                <button type="button" class="password-toggle" id="password-toggle" aria-label="${msg("showPassword")}" title="${msg("showPassword")}">
                                    <span aria-hidden="true">显示</span>
                                </button>
                            </div>
                            <#if realm.rememberMe && !usernameHidden??>
                                <label class="pf-v5-c-check pf-v5-u-mt-sm">
                                    <input id="rememberMe" name="rememberMe" type="checkbox" value="on" <#if login.rememberMe??>checked</#if>>
                                    <span>${msg("rememberMe")}</span>
                                </label>
                            </#if>
                        </div>
                        <input type="hidden" id="id-hidden-input" name="credentialId" <#if auth.selectedCredential?has_content>value="${auth.selectedCredential}"</#if>>
                        <button class="pf-v5-c-button pf-m-primary pf-m-block" name="login" id="kc-login" type="submit">${msg("doLogIn")}</button>
                    </form>
                </#if>
            </div>
            <div class="pf-v5-c-login__main-footer">
                <#if realm.registrationAllowed && !registrationDisabled??>
                    <div id="kc-registration-container">
                        <div id="kc-registration"><span>${msg("noAccount")} <a href="${url.registrationUrl}">${msg("doRegister")}</a></span></div>
                    </div>
                </#if>
            </div>
        </main>
    </div>
</div>
<script>
    (function () {
        var input = document.getElementById("password");
        var toggle = document.getElementById("password-toggle");
        if (!input || !toggle) return;
        toggle.addEventListener("click", function () {
            var visible = input.type === "text";
            input.type = visible ? "password" : "text";
            toggle.setAttribute("aria-label", visible ? "${msg("showPassword")}" : "${msg("hidePassword")}");
            toggle.setAttribute("title", visible ? "${msg("showPassword")}" : "${msg("hidePassword")}");
            toggle.querySelector("span").textContent = visible ? "显示" : "隐藏";
        });
    })();
</script>
</body>
</html>
