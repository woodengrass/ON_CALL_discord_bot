"""
合併前驗證腳本，CONVENTIONS.md 規定任何分支合併回 main 前必須在該分支 worktree 跑一次。

依序執行：主專案 pytest → 主專案 ruff → 子專案 pytest → 子專案 ruff。四項全綠才能請使用者
確認合併。會跑完所有步驟再總結（不會第一步失敗就跳過後面），方便一次看到全部問題。
輸出刻意用 ASCII——Windows 主控台預設 cp950，繁中輸出在部分終端機會變亂碼，
而這個腳本的重點是讓任何環境的任何 agent 都能無歧義讀到 PASS/FAIL。

主專案 ruff 這一步刻意排除 discord_plugin_platform/、rpc_protocol/ 這些有自己獨立
pyproject.toml/ruff 設定的子專案目錄——它們各自的 ruff 由對應的子專案步驟負責，主專案
這一步只查主專案自己的程式碼，避免重複掃描或用錯設定。2026-07 這裡曾經漏掉主專案 ruff
這一步，導致主專案累積 24 個從未被抓到的問題（含 2 個真正的 undefined-name），這條步驟
就是補上這個缺口，不要再拿掉。

用法：python scripts/verify_before_merge.py
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PLATFORM_ROOT = PROJECT_ROOT / "discord_plugin_platform"
SUB_PROJECT_EXCLUDES = ["discord_plugin_platform", "rpc_protocol"]

VERIFICATION_STEPS: list[tuple[str, list[str], Path]] = [
    ("main-project pytest", [sys.executable, "-m", "pytest", "-q"], PROJECT_ROOT),
    (
        "main-project ruff",
        [sys.executable, "-m", "ruff", "check", "."]
        + [flag for name in SUB_PROJECT_EXCLUDES for flag in ("--exclude", name)],
        PROJECT_ROOT,
    ),
    ("plugin-platform pytest", [sys.executable, "-m", "pytest", "-q"], PLUGIN_PLATFORM_ROOT),
    ("plugin-platform ruff", [sys.executable, "-m", "ruff", "check", "."], PLUGIN_PLATFORM_ROOT),
]


def run_step(step_name: str, command: list[str], working_directory: Path) -> bool:
    """
    執行單一驗證步驟並即時輸出結果。

    Args:
        step_name: 步驟顯示名稱
        command: 要執行的指令
        working_directory: 指令的工作目錄

    Returns:
        True 表示該步驟通過
    """
    print(f"\n=== {step_name} ({working_directory}) ===")
    completed_process = subprocess.run(command, cwd=working_directory)
    passed = completed_process.returncode == 0
    print(f"=== {step_name}: {'PASS' if passed else 'FAIL'} ===")
    return passed


def main() -> int:
    """
    跑完全部驗證步驟並總結。

    Returns:
        0 表示全部通過；1 表示至少一項失敗
    """
    results: list[tuple[str, bool]] = []
    for step_name, command, working_directory in VERIFICATION_STEPS:
        results.append((step_name, run_step(step_name, command, working_directory)))

    print("\n========== pre-merge verification summary ==========")
    for step_name, passed in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {step_name}")
    all_passed = all(passed for _, passed in results)
    print("ALL GREEN - ready to ask the user to merge." if all_passed else "FAILED - do not merge until fixed.")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
