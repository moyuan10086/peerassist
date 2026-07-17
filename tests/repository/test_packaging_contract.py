import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_wheel_manifest_contains_runtime_modules_and_frontend() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = configuration["tool"]["hatch"]["build"]["targets"]["wheel"]

    force_include = wheel["force-include"]
    assert force_include["src/pipeline_full.py"] == "pipeline_full.py"
    assert force_include["RefCopilot/src/refcopilot"] == "refcopilot"
    assert force_include["web/peerassist-workspace/dist"] == "web/peerassist-workspace/dist"
