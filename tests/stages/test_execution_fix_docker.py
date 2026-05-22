from __future__ import annotations

from fact_generation.execution.nodes.fix import (
    _add_docker_extra_index_url,
    _add_extra_pip_package,
    _extract_pip_install_requests,
    _is_source_edit_command,
)


def test_extract_pip_install_requests_skips_bootstrap_and_keeps_indexes() -> None:
    cmd = [
        "bash",
        "-lc",
        "python -m pip install --upgrade pip setuptools wheel && "
        "python -m pip install --index-url https://download.pytorch.org/whl/cpu "
        "'torch==2.6.0+cpu'",
    ]

    packages, indexes = _extract_pip_install_requests(cmd)

    assert packages == ["torch==2.6.0+cpu"]
    assert indexes == ["https://download.pytorch.org/whl/cpu"]


def test_add_extra_pip_package_replaces_bare_package_with_specific_spec() -> None:
    cfg = {"docker_extra_pip_packages": "torch numpy", "docker_paper_image": "stale-image"}

    changed = _add_extra_pip_package(cfg, "torch==2.6.0+cpu")

    assert changed is True
    assert cfg["docker_extra_pip_packages"] == "torch==2.6.0+cpu numpy"
    assert "docker_paper_image" not in cfg


def test_add_docker_extra_index_url_invalidates_cached_image() -> None:
    cfg = {"docker_pip_extra_index_url": "https://pypi.org/simple", "docker_paper_image": "stale-image"}

    changed = _add_docker_extra_index_url(cfg, "https://download.pytorch.org/whl/cpu")

    assert changed is True
    assert cfg["docker_pip_extra_index_url"] == "https://pypi.org/simple https://download.pytorch.org/whl/cpu"
    assert "docker_paper_image" not in cfg


def test_source_edit_command_detection_blocks_python_write_text() -> None:
    cmd = [
        "python",
        "-c",
        "from pathlib import Path; Path('paper.py').write_text('patched')",
    ]

    assert _is_source_edit_command(cmd) is True
    assert _is_source_edit_command(["python", "-c", "print('diagnostic only')"]) is False


def test_source_edit_command_detection_allows_metric_artifacts() -> None:
    cmd = [
        "bash",
        "-lc",
        "python - <<'PY'\n"
        "import pathlib\n"
        "pathlib.Path('metrics').mkdir(exist_ok=True)\n"
        "pathlib.Path('metrics/import_smoke_metrics.json').write_text('{}')\n"
        "PY",
    ]

    assert _is_source_edit_command(cmd) is False
