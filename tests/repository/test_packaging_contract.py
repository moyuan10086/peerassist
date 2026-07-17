import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_platform_extra_contains_runtime_dependencies() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = configuration["project"]["optional-dependencies"]["platform"]
    normalized = {dependency.split("[", 1)[0].split("<", 1)[0].split(">", 1)[0].split("=", 1)[0] for dependency in dependencies}

    assert {
        "fastapi",
        "uvicorn",
        "sqlalchemy",
        "psycopg",
        "alembic",
        "pyjwt",
        "cryptography",
        "boto3",
        "httpx",
    } <= {dependency.lower() for dependency in normalized}


def test_wheel_manifest_contains_runtime_modules_and_frontend() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = configuration["tool"]["hatch"]["build"]["targets"]["wheel"]
    sdist = configuration["tool"]["hatch"]["build"]["targets"]["sdist"]

    force_include = wheel["force-include"]
    assert force_include["src/pipeline_full.py"] == "pipeline_full.py"
    assert force_include["RefCopilot/src/refcopilot"] == "refcopilot"
    assert force_include["web/peerassist-workspace/dist"] == "web/peerassist-workspace/dist"
    assert "src/peerassist" in wheel["packages"]
    assert "services" in wheel["packages"]
    assert (ROOT / "src/peerassist/platform/__init__.py").is_file()
    assert (ROOT / "services/api/__init__.py").is_file()
    assert (ROOT / "services/worker/__init__.py").is_file()
    assert "RefCopilot/src/refcopilot" in sdist["include"]
    assert "web/peerassist-workspace/dist" in sdist["include"]
    assert "services" in sdist["include"]
