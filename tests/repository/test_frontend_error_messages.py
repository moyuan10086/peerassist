from pathlib import Path


def test_frontend_maps_platform_error_codes_to_actionable_chinese() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")
    formatter = source[source.index("function apiErrorMessage(") : source.index("function idempotencyKey(")]

    assert 'invalid_upload: "只支持有效的 PDF 文件，请重新选择论文。"' in formatter
    assert 'payload_too_large: "PDF 文件超过 100 MB，请压缩后重新上传。"' in formatter
    assert 'authentication_required: "登录状态已失效，请重新登录。"' in formatter
    assert 'forbidden: "当前账号没有执行此操作的权限，请联系管理员。"' in formatter
    assert 'dependency_unavailable: "服务暂时不可用，请稍后重试。"' in formatter
    assert 'stale_version: "任务已被其他操作更新，请刷新后重试。"' in formatter


def test_upload_validates_pdf_before_sending_the_request() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")
    upload = source[source.index("async function uploadPaper(") : source.index("async function logout(")]

    assert 'file.name.toLowerCase().endsWith(".pdf")' in upload
    assert "file.size > MAX_PDF_SIZE_BYTES" in upload
    assert "请选择 PDF 文件" in upload
    assert "PDF 文件超过 100 MB" in upload
