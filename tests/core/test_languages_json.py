import json
from pathlib import Path
from typing import Any

LANGUAGES_JSON_PATH = Path(__file__).resolve().parents[2] / "locales" / "languages.json"
EXPECTED_LANGUAGE_KEYS = frozenset({"zh-TW", "zh-CN", "en-US"})


def _raise_on_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """object_pairs_hook：同一物件內出現重複 key 時直接 fail，避免被靜默覆蓋掩蓋。

    Args:
        pairs: json 模組在解析一個物件時依序收集到的 (key, value) 列表。

    Returns:
        還原成一般 dict 供 json.load 正常使用。
    """
    seen: dict[str, Any] = {}
    for key, value in pairs:
        assert key not in seen, f"languages.json 出現重複 key，後者已靜默覆蓋前者：{key!r}"
        seen[key] = value
    return seen


def _load_languages_json() -> dict[str, Any]:
    """讀取 locales/languages.json 並在讀取過程中偵測重複 key。

    Returns:
        解析後的翻譯字典結構。
    """
    with LANGUAGES_JSON_PATH.open(encoding="utf-8") as file:
        return json.load(file, object_pairs_hook=_raise_on_duplicate_keys)


def _collect_translation_leaves(node: Any, path: str, leaves: dict[str, dict[str, str]]) -> None:
    """遞迴走訪翻譯字典，收集所有語系葉節點（值皆為字串的物件）。

    Args:
        node: 目前走訪到的節點，可能是 dict 或其他 JSON 值。
        path: 目前節點在字典中的路徑，用於錯誤訊息定位。
        leaves: 累積收集結果的字典，key 為路徑，value 為該葉節點的語系字典。
    """
    if not isinstance(node, dict):
        return

    if node and all(isinstance(value, str) for value in node.values()):
        leaves[path] = node
        return

    for key, value in node.items():
        _collect_translation_leaves(value, f"{path}/{key}", leaves)


def test_languages_json_has_no_duplicate_keys() -> None:
    """任何層級的物件都不應該有重複 key，否則後者會靜默覆蓋前者。"""
    _load_languages_json()


def test_languages_json_leaf_language_keys_match_exactly() -> None:
    """每個翻譯葉節點都必須剛好包含三語系，多一個或少一個都要 fail。"""
    data = _load_languages_json()
    leaves: dict[str, dict[str, str]] = {}
    for section, content in data.items():
        _collect_translation_leaves(content, section, leaves)

    assert leaves, "languages.json 中沒有找到任何翻譯葉節點，請確認結構是否改變。"

    mismatched: dict[str, str] = {}
    for path, leaf in leaves.items():
        actual_keys = frozenset(leaf.keys())
        if actual_keys != EXPECTED_LANGUAGE_KEYS:
            missing = EXPECTED_LANGUAGE_KEYS - actual_keys
            extra = actual_keys - EXPECTED_LANGUAGE_KEYS
            mismatched[path] = f"缺少: {sorted(missing)}, 多餘: {sorted(extra)}"

    assert not mismatched, "以下翻譯節點的語系 key 集合不一致：\n" + "\n".join(
        f"  {path}: {detail}" for path, detail in mismatched.items()
    )


def test_languages_json_has_no_empty_translation_values() -> None:
    """翻譯內容不應該是空字串，否則等於該語系沒有翻譯卻不易察覺。"""
    data = _load_languages_json()
    leaves: dict[str, dict[str, str]] = {}
    for section, content in data.items():
        _collect_translation_leaves(content, section, leaves)

    empty_entries = [
        f"{path}/{language}"
        for path, leaf in leaves.items()
        for language, text in leaf.items()
        if text.strip() == ""
    ]

    assert not empty_entries, "以下翻譯節點的內容為空字串：\n" + "\n".join(empty_entries)
