import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLATFORM_ROOT = ROOT / "src/peerassist/platform"
FORBIDDEN = {"fastapi", "sqlalchemy", "boto3", "jwt", "keycloak", "minio"}


def imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for source in path.rglob("*.py"):
        if "adapters" in source.relative_to(path).parts:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".", 1)[0])
    return roots


def test_platform_domain_does_not_import_provider_frameworks() -> None:
    assert PLATFORM_ROOT.is_dir()
    assert imported_roots(PLATFORM_ROOT).isdisjoint(FORBIDDEN)
