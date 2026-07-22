"""Smoke the public first-use workflow with a real Chromium browser."""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def _load_environment() -> None:
    environment_path = Path(os.environ.get("PEERASSIST_ENV_FILE", "/tmp/peerassist-m1-final/environment"))
    for line in environment_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)


def main() -> None:
    _load_environment()
    origin = os.environ["PEERASSIST_PUBLIC_ORIGIN"]
    chromium = os.environ.get(
        "PEERASSIST_CHROMIUM_PATH",
        "/root/.cache/selenium/chrome/linux64/148.0.7778.167/chrome",
    )
    bad_responses: list[tuple[int, str]] = []
    page_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=chromium,
            args=["--no-sandbox"],
        )
        context = browser.new_context()
        page = context.new_page()
        page.on(
            "response",
            lambda response: bad_responses.append((response.status, response.url))
            if response.status >= 400
            else None,
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        page.goto(f"{origin}/login", wait_until="domcontentloaded", timeout=30_000)
        page.get_by_role("main").get_by_role("button", name="登录", exact=True).click()
        page.wait_for_load_state("domcontentloaded", timeout=30_000)
        page.locator('input[name="username"]').fill(os.environ["PEERASSIST_OIDC_USERNAME"])
        page.locator('input[name="password"]').fill(os.environ["PEERASSIST_OIDC_USER_PASSWORD"])
        page.get_by_role("button", name="Sign In").click()
        page.wait_for_load_state("domcontentloaded", timeout=30_000)
        page.wait_for_timeout(1_000)

        project_select = page.get_by_label("当前审稿项目")
        assert project_select.count() == 1, "project selector is not visible"
        selected_project = project_select.input_value()
        assert selected_project, "no project was selected"
        stored_project = page.evaluate("localStorage.getItem('peerassist.activeProjectId')")
        assert stored_project == selected_project, "selected project was not persisted"

        page.get_by_role("button", name="上传论文 / 智能审稿").click()
        page.locator('input[type="file"]').set_input_files(
            {"name": "not-a-paper.txt", "mimeType": "text/plain", "buffer": b"not a pdf"}
        )
        page.get_by_role("button", name="上传并开始审稿").click()
        page.get_by_text("请选择 PDF 文件，Word 或图片暂不支持。").wait_for(timeout=5_000)

        paper_buttons = page.locator(".job-card").get_by_role("button", name="阅读论文", exact=True)
        if paper_buttons.count():
            paper_buttons.first.click()
            page.wait_for_url(f"{origin}/paper", timeout=10_000)
            active_paper = page.evaluate("localStorage.getItem('peerassist.activePaperId')")
            assert active_paper, "opening a review did not persist the paper"
            page.reload(wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(1_500)
            restored_paper = page.evaluate("localStorage.getItem('peerassist.activePaperId')")
            assert restored_paper == active_paper, (
                "paper context was lost after refresh; "
                f"active={active_paper!r} restored={restored_paper!r} responses={bad_responses[-10:]!r}"
            )

        body = page.locator("body").inner_text()
        assert "The operation could not be completed." not in body
        assert "The request could not be validated." not in body
        assert not bad_responses, f"HTTP errors: {bad_responses[:10]}"
        assert not page_errors, f"page errors: {page_errors[:10]}"
        browser.close()

    print("first-use browser smoke passed")


if __name__ == "__main__":
    main()
