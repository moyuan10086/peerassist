import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_wheel_manifest_contains_the_pipeline_entry_module() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = configuration["tool"]["hatch"]["build"]["targets"]["wheel"]

    assert wheel["force-include"]["src/pipeline_full.py"] == "pipeline_full.py"
