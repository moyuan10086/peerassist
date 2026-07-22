from pathlib import Path


def test_authenticated_workspace_persists_an_explicit_project_selection() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")

    assert 'const ACTIVE_PROJECT_STORAGE_KEY = "peerassist.activeProjectId"' in source
    assert "async function listPlatformProjects(" in source
    assert "setSelectedProjectId" in source
    assert "localStorage.setItem(ACTIVE_PROJECT_STORAGE_KEY" in source
    assert 'aria-label="当前审稿项目"' in source


def test_workspace_has_a_teacher_friendly_empty_project_state() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")

    assert "function ProjectOnboarding(" in source
    assert "还没有可用的审稿项目" in source
    assert "创建第一个项目" in source
    assert "联系管理员将你加入项目" in source


def test_upload_uses_the_selected_project_instead_of_the_first_project() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")

    upload = source[source.index("async function uploadPaper(") : source.index("async function logout(")]
    assert "selectedProject" in upload
    assert "resolvePlatformProject()" not in upload


def test_transient_pdf_probe_failure_does_not_forget_the_active_paper() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")
    probe = source[source.index('fetch(activePdfUrl, { method: "HEAD"') : source.index("const showToast")]

    assert "[404, 410].includes(response.status)" in probe
    assert ".catch(() => setActiveContextReady(true))" in probe
