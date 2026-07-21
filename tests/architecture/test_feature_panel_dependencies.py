import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_feature_panels_do_not_import_hubs() -> None:
    """feature 面板不得反向匯入 hub 組合層。"""
    violations: list[str] = []
    for panel_path in (PROJECT_ROOT / "features").glob("*/panel.py"):
        tree = ast.parse(panel_path.read_text(encoding="utf-8-sig"), filename=str(panel_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "hubs" or alias.name.startswith("hubs.") for alias in node.names):
                    violations.append(f"{panel_path.relative_to(PROJECT_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "hubs" or node.module.startswith("hubs."):
                    violations.append(f"{panel_path.relative_to(PROJECT_ROOT)}:{node.lineno}")

    assert violations == []
