<#import "user-profile-commons.ftl" as userProfileCommons>
<#import "register-commons.ftl" as registerCommons>
<!DOCTYPE html>
<html lang="${(locale.currentLanguageTag!'zh-CN')}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>${msg("registerTitle")}</title>
    <link rel="stylesheet" href="${url.resourcesPath}/css/login.css?v=20260722-register">
</head>
<body class="login-pf register-page">
<div class="pf-v5-c-login">
    <div class="pf-v5-c-login__container">
        <header id="kc-header">
            <div class="brand-lockup" aria-label="PeerAssist">
                <span class="brand-mark" aria-hidden="true">P</span>
                <span class="brand-identity">
                    <span class="brand-name">PeerAssist</span>
                    <span class="brand-context">教师智能审稿工作台</span>
                </span>
            </div>
        </header>
        <main class="pf-v5-c-login__main register-main">
            <div class="pf-v5-c-login__main-header">
                <p class="form-kicker">首次使用</p>
                <h1 class="pf-v5-c-title pf-m-3xl" id="kc-page-title">${msg("registerTitle")}</h1>
                <p class="form-subtitle">创建账号后即可加入审稿项目，上传论文并协作完善审稿意见。</p>
            </div>
            <div class="pf-v5-c-login__main-body">
                <#if message?has_content>
                    <div class="pf-v5-c-alert pf-m-inline pf-m-danger" role="alert">
                        <span>${kcSanitize(message.summary)?no_esc}</span>
                    </div>
                </#if>
                <form id="kc-register-form" class="pf-v5-c-form" action="${url.registrationAction}" method="post">
                    <@userProfileCommons.userProfileFormFields; callback, attribute>
                        <#if callback = "afterField">
                            <#if passwordRequired?? && (attribute.name == 'username' || (attribute.name == 'email' && realm.registrationEmailAsUsername))>
                                <div class="pf-v5-c-form__group">
                                    <label for="password" class="pf-v5-c-form__label">${msg("password")} *</label>
                                    <div class="registration-password-field">
                                        <input id="password" name="password" class="pf-v5-c-form-control" type="password" autocomplete="new-password" aria-invalid="<#if messagesPerField.existsError('password','password-confirm')>true<#else>false</#if>">
                                        <button class="password-toggle" type="button" aria-label="${msg('showPassword')}" aria-controls="password" data-password-toggle data-label-show="${msg('showPassword')}" data-label-hide="${msg('hidePassword')}">
                                            <span aria-hidden="true">显示</span>
                                        </button>
                                    </div>
                                    <#if messagesPerField.existsError('password')>
                                        <span class="pf-v5-c-form__helper-text pf-m-error" aria-live="polite">${kcSanitize(messagesPerField.get('password'))?no_esc}</span>
                                    </#if>
                                </div>
                                <div class="pf-v5-c-form__group">
                                    <label for="password-confirm" class="pf-v5-c-form__label">${msg("passwordConfirm")} *</label>
                                    <div class="registration-password-field">
                                        <input id="password-confirm" name="password-confirm" class="pf-v5-c-form-control" type="password" autocomplete="new-password" aria-invalid="<#if messagesPerField.existsError('password-confirm')>true<#else>false</#if>">
                                        <button class="password-toggle" type="button" aria-label="${msg('showPassword')}" aria-controls="password-confirm" data-password-toggle data-label-show="${msg('showPassword')}" data-label-hide="${msg('hidePassword')}">
                                            <span aria-hidden="true">显示</span>
                                        </button>
                                    </div>
                                    <#if messagesPerField.existsError('password-confirm')>
                                        <span class="pf-v5-c-form__helper-text pf-m-error" aria-live="polite">${kcSanitize(messagesPerField.get('password-confirm'))?no_esc}</span>
                                    </#if>
                                </div>
                            </#if>
                        </#if>
                    </@userProfileCommons.userProfileFormFields>

                    <@registerCommons.termsAcceptance/>

                    <#if recaptchaRequired?? && (recaptchaVisible!false)>
                        <div class="g-recaptcha" data-size="compact" data-sitekey="${recaptchaSiteKey}" data-action="${recaptchaAction}"></div>
                    </#if>

                    <div class="register-actions">
                        <#if recaptchaRequired?? && !(recaptchaVisible!false)>
                            <script>
                                function onSubmitRecaptcha() {
                                    document.getElementById("kc-register-form").requestSubmit();
                                }
                            </script>
                            <button id="kc-register" class="pf-v5-c-button pf-m-primary pf-m-block g-recaptcha" data-sitekey="${recaptchaSiteKey}" data-callback="onSubmitRecaptcha" data-action="${recaptchaAction}" type="submit">${msg("doRegister")}</button>
                        <#else>
                            <button id="kc-register" class="pf-v5-c-button pf-m-primary pf-m-block" type="submit">${msg("doRegister")}</button>
                        </#if>
                        <a class="back-to-login" href="${url.loginUrl}">${kcSanitize(msg("backToLogin"))?no_esc}</a>
                    </div>
                </form>
            </div>
            <div class="pf-v5-c-login__main-footer">
                <p class="privacy-note"><span aria-hidden="true"></span>论文内容仅对当前项目成员可见</p>
            </div>
        </main>
    </div>
</div>
<script type="module" src="${url.resourcesPath}/js/passwordVisibility.js"></script>
</body>
</html>
