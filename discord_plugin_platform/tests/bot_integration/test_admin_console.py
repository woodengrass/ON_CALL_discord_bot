from bot_integration import admin_console


async def test_plugin_list_prints_plugins(monkeypatch, capsys) -> None:
    """
    list 指令應列出 repository 回傳的外掛資料。
    """

    async def fake_list_plugins(status: str | None = None) -> list[dict]:
        return [
            {
                "plugin_id": "temp_role_punishment",
                "name": "temp_role_punishment",
                "latest_version": "1.0.0",
                "status": "approved",
            }
        ]

    monkeypatch.setattr(admin_console.repository, "list_plugins", fake_list_plugins)

    await admin_console.handle_command("admin plugin list")

    output = capsys.readouterr().out
    assert "temp_role_punishment" in output
    assert "status=approved" in output


async def test_review_list_prints_pending_plugins(monkeypatch, capsys) -> None:
    """
    review list 指令應只列出待審核外掛，方便操作者查審核佇列。
    """
    captured_status = None

    async def fake_list_plugins(status: str | None = None) -> list[dict]:
        nonlocal captured_status
        captured_status = status
        return [
            {
                "plugin_id": "pending_plugin",
                "name": "pending_plugin",
                "latest_version": "1.0.0",
                "status": "pending_review",
            }
        ]

    monkeypatch.setattr(admin_console.repository, "list_plugins", fake_list_plugins)

    await admin_console.handle_command("admin plugin review list")

    output = capsys.readouterr().out
    assert captured_status == "pending_review"
    assert "pending_plugin" in output


async def test_show_prints_plugin_details(monkeypatch, capsys) -> None:
    """
    show 指令應輸出單一外掛詳細資料與 manifest。
    """

    async def fake_get_plugin(plugin_id: str) -> dict | None:
        return {
            "plugin_id": plugin_id,
            "author_id": 1234,
            "name": "temp_role_punishment",
            "latest_version": "1.0.0",
            "status": "approved",
        }

    async def fake_get_plugin_manifest(plugin_id: str, version: str) -> str | None:
        return '{"name":"temp_role_punishment"}'

    monkeypatch.setattr(admin_console.repository, "get_plugin", fake_get_plugin)
    monkeypatch.setattr(admin_console.repository, "get_plugin_manifest", fake_get_plugin_manifest)

    await admin_console.handle_command("admin plugin show temp_role_punishment")

    output = capsys.readouterr().out
    assert "author=1234" in output
    assert "manifest=" in output


async def test_review_reject_keeps_quoted_reason(monkeypatch, capsys) -> None:
    """
    reject 指令必須用 shlex 保留含空白的退回原因。
    """
    captured_reason = ""

    async def fake_reject_plugin_version(
        plugin_id: str,
        reason_presets: list[str],
        custom_reason: str | None,
        flagged_capabilities: list[str],
    ) -> bool:
        nonlocal captured_reason
        assert reason_presets == []
        assert flagged_capabilities == []
        captured_reason = custom_reason
        return plugin_id == "temp_role_punishment"

    monkeypatch.setattr(admin_console.admin_operations, "reject_plugin_version", fake_reject_plugin_version)

    await admin_console.handle_command('admin plugin review reject temp_role_punishment "manifest 欄位不完整"')

    assert captured_reason == "manifest 欄位不完整"
    assert "已退回外掛" in capsys.readouterr().out


async def test_install_uses_manifest_required_capabilities(monkeypatch, capsys) -> None:
    """
    install 指令應解析最新版本 manifest，將 required_capabilities 全部授權安裝。
    """
    captured_installation: dict = {}

    async def fake_install_plugin(guild_id: int, plugin_id: str) -> None:
        captured_installation.update(
            {
                "guild_id": guild_id,
                "plugin_id": plugin_id,
            }
        )

    monkeypatch.setattr(admin_console.admin_operations, "install_plugin", fake_install_plugin)

    await admin_console.handle_command("admin plugin install 1111 temp_role_punishment")

    assert captured_installation == {
        "guild_id": 1111,
        "plugin_id": "temp_role_punishment",
    }
    assert "已安裝外掛" in capsys.readouterr().out


async def test_install_rejects_unapproved_plugin(monkeypatch, capsys) -> None:
    """
    install 指令不得安裝尚未核准的外掛。
    """
    install_called = False

    async def fake_install_plugin(guild_id: int, plugin_id: str) -> None:
        nonlocal install_called
        install_called = True
        raise admin_console.admin_operations.AdminOperationError("外掛尚未核准，不能安裝：temp_role_punishment")

    monkeypatch.setattr(admin_console.admin_operations, "install_plugin", fake_install_plugin)

    await admin_console.handle_command("admin plugin install 1111 temp_role_punishment")

    assert install_called is True
    assert "尚未核准" in capsys.readouterr().out


async def test_suspend_refreshes_suspension_cache(monkeypatch, capsys) -> None:
    """
    suspend 指令成功後應立即重新同步停權快取。
    """
    refresh_called = False
    fake_database = object()

    async def fake_request_suspend(plugin_id: str) -> bool:
        return plugin_id == "temp_role_punishment"

    async def fake_refresh_from_database(database_connection: object) -> None:
        nonlocal refresh_called
        assert database_connection is fake_database
        refresh_called = True

    monkeypatch.setattr(admin_console.admin_operations, "request_suspend", fake_request_suspend)
    monkeypatch.setattr(admin_console.suspension, "refresh_from_database", fake_refresh_from_database)
    monkeypatch.setattr(admin_console, "get_db", lambda: fake_database)

    await admin_console.handle_command("admin plugin suspend temp_role_punishment")

    assert refresh_called is True
    assert "已停權外掛" in capsys.readouterr().out


async def test_unsuspend_refreshes_suspension_cache(monkeypatch, capsys) -> None:
    """
    unsuspend 指令成功後也應立即重新同步停權快取，跟 suspend 對稱，
    否則已解除停權的外掛還要等最多 10 秒的輪詢週期才會真的恢復執行。
    """
    refresh_called = False
    fake_database = object()

    async def fake_request_unsuspend(plugin_id: str) -> bool:
        return plugin_id == "temp_role_punishment"

    async def fake_refresh_from_database(database_connection: object) -> None:
        nonlocal refresh_called
        assert database_connection is fake_database
        refresh_called = True

    monkeypatch.setattr(admin_console.admin_operations, "request_unsuspend", fake_request_unsuspend)
    monkeypatch.setattr(admin_console.suspension, "refresh_from_database", fake_refresh_from_database)
    monkeypatch.setattr(admin_console, "get_db", lambda: fake_database)

    await admin_console.handle_command("admin plugin unsuspend temp_role_punishment")

    assert refresh_called is True
    assert "已解除外掛停權" in capsys.readouterr().out


async def test_unsuspend_missing_plugin_reports_not_found(monkeypatch, capsys) -> None:
    async def fake_request_unsuspend(plugin_id: str) -> bool:
        return False

    monkeypatch.setattr(admin_console.admin_operations, "request_unsuspend", fake_request_unsuspend)

    await admin_console.handle_command("admin plugin unsuspend does_not_exist")

    assert "找不到外掛" in capsys.readouterr().out


async def test_quota_set_updates_installation_override(monkeypatch, capsys) -> None:
    """
    quota set 指令應解析 execution/action 配額並呼叫 repository。
    """
    captured_quota: dict = {}

    async def fake_set_quota_override(
        guild_id: int,
        plugin_id: str,
        execution_quota: int | None,
        action_quota: int | None,
    ) -> bool:
        captured_quota.update(
            {
                "guild_id": guild_id,
                "plugin_id": plugin_id,
                "execution_quota": execution_quota,
                "action_quota": action_quota,
            }
        )
        return True

    monkeypatch.setattr(
        admin_console.admin_operations,
        "set_quota_override",
        fake_set_quota_override,
    )

    await admin_console.handle_command(
        "admin plugin quota set 1111 temp_role_punishment execution=60 action=default"
    )

    assert captured_quota == {
        "guild_id": 1111,
        "plugin_id": "temp_role_punishment",
        "execution_quota": 60,
        "action_quota": None,
    }
    assert "已更新外掛安裝配額" in capsys.readouterr().out


async def test_quota_set_rejects_negative_value(monkeypatch, capsys) -> None:
    """
    quota override 不接受負數，避免產生難以理解的保護設定。
    """
    update_called = False

    async def fake_set_quota_override(
        guild_id: int,
        plugin_id: str,
        execution_quota: int | None,
        action_quota: int | None,
    ) -> bool:
        nonlocal update_called
        update_called = True
        return True

    monkeypatch.setattr(
        admin_console.admin_operations,
        "set_quota_override",
        fake_set_quota_override,
    )

    await admin_console.handle_command(
        "admin plugin quota set 1111 temp_role_punishment execution=-1 action=10"
    )

    assert update_called is False
    assert "配額不能是負數" in capsys.readouterr().out


async def test_install_rejects_invalid_guild_id(monkeypatch, capsys) -> None:
    """
    guild_id 格式錯誤時應在進 repository 前回報友善訊息。
    """
    get_plugin_called = False

    async def fake_get_plugin(plugin_id: str) -> dict | None:
        nonlocal get_plugin_called
        get_plugin_called = True
        return None

    monkeypatch.setattr(admin_console.repository, "get_plugin", fake_get_plugin)

    await admin_console.handle_command("admin plugin install -1 temp_role_punishment")

    assert get_plugin_called is False
    assert "guild_id 必須是正整數" in capsys.readouterr().out


async def test_uninstall_purges_message_cache_when_no_subscription_remains(monkeypatch, capsys) -> None:
    """
    uninstall 後若該伺服器不再有 edit/delete 訂閱，應立即清除 message cache。
    """
    purged_guild_ids: list[int] = []
    cleared_quota: list[tuple[int, str]] = []

    async def fake_uninstall_plugin(guild_id: int, plugin_id: str) -> bool:
        fake_clear_usage(guild_id, plugin_id)
        fake_purge_guild(guild_id)
        return True

    def fake_purge_guild(guild_id: int) -> None:
        purged_guild_ids.append(guild_id)

    def fake_clear_usage(guild_id: int, plugin_id: str) -> None:
        cleared_quota.append((guild_id, plugin_id))

    monkeypatch.setattr(admin_console.admin_operations, "uninstall_plugin", fake_uninstall_plugin)

    await admin_console.handle_command("admin plugin uninstall 1111 temp_role_punishment")

    assert purged_guild_ids == [1111]
    assert cleared_quota == [(1111, "temp_role_punishment")]
    assert "已移除外掛安裝" in capsys.readouterr().out


async def test_stats_prints_outcome_breakdown_and_timing(monkeypatch, capsys) -> None:
    """
    stats 指令應印出總筆數、各 outcome 比例與平均/p95 耗時，見 design.md 附錄 A.6.2。
    """
    captured_args: dict = {}

    async def fake_get_execution_stats(plugin_id: str, guild_id=None, since=None) -> dict:
        captured_args["plugin_id"] = plugin_id
        captured_args["guild_id"] = guild_id
        captured_args["since"] = since
        return {
            "total": 4,
            "outcome_counts": {"success": 3, "crashed": 1},
            "avg_execution_ms": 42.5,
            "p95_execution_ms": 99,
        }

    monkeypatch.setattr(admin_console.repository, "get_execution_stats", fake_get_execution_stats)

    await admin_console.handle_command(
        "admin plugin stats temp_role_punishment guild=1111 since=2025-01-01T00:00:00"
    )

    assert captured_args == {"plugin_id": "temp_role_punishment", "guild_id": 1111, "since": "2025-01-01T00:00:00"}
    output = capsys.readouterr().out
    assert "總執行次數=4" in output
    assert "success" in output and "75.0%" in output
    assert "crashed" in output and "25.0%" in output
    assert "平均耗時=42.5ms" in output
    assert "p95 耗時=99ms" in output


async def test_stats_reports_no_records_when_empty(monkeypatch, capsys) -> None:
    async def fake_get_execution_stats(plugin_id: str, guild_id=None, since=None) -> dict:
        return {"total": 0, "outcome_counts": {}, "avg_execution_ms": None, "p95_execution_ms": None}

    monkeypatch.setattr(admin_console.repository, "get_execution_stats", fake_get_execution_stats)

    await admin_console.handle_command("admin plugin stats temp_role_punishment")

    assert "沒有任何執行紀錄" in capsys.readouterr().out


async def test_stats_rejects_unknown_argument(monkeypatch, capsys) -> None:
    await admin_console.handle_command("admin plugin stats temp_role_punishment bogus=1")

    assert "指令執行失敗" in capsys.readouterr().out


async def test_ban_requires_reason(monkeypatch, capsys) -> None:
    ban_called = False

    async def fake_ban_plugin_version(plugin_id: str, reason: str) -> bool:
        nonlocal ban_called
        ban_called = True
        return True

    monkeypatch.setattr(admin_console.admin_operations, "ban_plugin_version", fake_ban_plugin_version)

    await admin_console.handle_command("admin plugin ban temp_role_punishment")

    assert ban_called is False
    assert "必須提供封鎖原因" in capsys.readouterr().out


async def test_ban_bans_plugin_with_reason(monkeypatch, capsys) -> None:
    captured: dict = {}

    async def fake_ban_plugin_version(plugin_id: str, reason: str) -> bool:
        captured["plugin_id"] = plugin_id
        captured["reason"] = reason
        return True

    monkeypatch.setattr(admin_console.admin_operations, "ban_plugin_version", fake_ban_plugin_version)

    await admin_console.handle_command('admin plugin ban temp_role_punishment "violates policy"')

    assert captured == {"plugin_id": "temp_role_punishment", "reason": "violates policy"}
    assert "已永久封鎖外掛" in capsys.readouterr().out


async def test_unban_reports_not_found(monkeypatch, capsys) -> None:
    async def fake_unban_plugin(plugin_id: str) -> bool:
        return False

    monkeypatch.setattr(admin_console.admin_operations, "unban_plugin", fake_unban_plugin)

    await admin_console.handle_command("admin plugin unban temp_role_punishment")

    assert "找不到外掛" in capsys.readouterr().out


async def test_pricing_sets_valid_tier(monkeypatch, capsys) -> None:
    captured: dict = {}

    async def fake_set_plugin_pricing_tier(plugin_id: str, pricing_tier: str) -> bool:
        captured["plugin_id"] = plugin_id
        captured["pricing_tier"] = pricing_tier
        return True

    monkeypatch.setattr(admin_console.admin_operations, "set_plugin_pricing_tier", fake_set_plugin_pricing_tier)

    await admin_console.handle_command("admin plugin pricing temp_role_punishment paid")

    assert captured == {"plugin_id": "temp_role_punishment", "pricing_tier": "paid"}
    assert "已設定計價分類為 paid" in capsys.readouterr().out


async def test_pricing_rejects_invalid_tier(monkeypatch, capsys) -> None:
    set_called = False

    async def fake_set_plugin_pricing_tier(plugin_id: str, pricing_tier: str) -> bool:
        nonlocal set_called
        set_called = True
        return True

    monkeypatch.setattr(admin_console.admin_operations, "set_plugin_pricing_tier", fake_set_plugin_pricing_tier)

    await admin_console.handle_command("admin plugin pricing temp_role_punishment gold")

    assert set_called is False
    assert "只能是 free 或 paid" in capsys.readouterr().out


async def test_resource_sets_overrides(monkeypatch, capsys) -> None:
    captured: dict = {}

    async def fake_set_installation_resource_overrides(guild_id: int, plugin_id: str, overrides: dict) -> bool:
        captured.update({"guild_id": guild_id, "plugin_id": plugin_id, "overrides": overrides})
        return True

    monkeypatch.setattr(
        admin_console.admin_operations, "set_installation_resource_overrides", fake_set_installation_resource_overrides
    )

    await admin_console.handle_command(
        "admin plugin resource 1111 temp_role_punishment instruction_limit=2000000 memory_limit_bytes=67108864"
    )

    assert captured == {
        "guild_id": 1111,
        "plugin_id": "temp_role_punishment",
        "overrides": {"instruction_limit": 2000000, "memory_limit_bytes": 67108864},
    }
    assert "已更新資源覆蓋" in capsys.readouterr().out


async def test_resource_with_no_arguments_clears_overrides(monkeypatch, capsys) -> None:
    captured: dict = {}

    async def fake_set_installation_resource_overrides(guild_id: int, plugin_id: str, overrides: dict) -> bool:
        captured["overrides"] = overrides
        return True

    monkeypatch.setattr(
        admin_console.admin_operations, "set_installation_resource_overrides", fake_set_installation_resource_overrides
    )

    await admin_console.handle_command("admin plugin resource 1111 temp_role_punishment")

    assert captured["overrides"] == {}
    assert "已清除資源覆蓋" in capsys.readouterr().out


async def test_resource_rejects_unknown_field(monkeypatch, capsys) -> None:
    await admin_console.handle_command("admin plugin resource 1111 temp_role_punishment bogus_field=1")

    assert "未知的資源覆蓋欄位" in capsys.readouterr().out


async def test_tier_list_prints_tiers(monkeypatch, capsys) -> None:
    async def fake_list_resource_tiers() -> list[dict]:
        return [{"tier_name": "default", "display_order": 0, "description": "平台預設方案"}]

    monkeypatch.setattr(admin_console.repository, "list_resource_tiers", fake_list_resource_tiers)

    await admin_console.handle_command("admin plugin tier list")

    output = capsys.readouterr().out
    assert "default" in output and "order=0" in output


async def test_tier_create(monkeypatch, capsys) -> None:
    captured: dict = {}

    async def fake_create_resource_tier(tier_name: str, display_order: int, description: str) -> None:
        captured.update({"tier_name": tier_name, "display_order": display_order, "description": description})

    monkeypatch.setattr(admin_console.admin_operations, "create_resource_tier", fake_create_resource_tier)

    await admin_console.handle_command('admin plugin tier create large 10 "large guild"')

    assert captured == {"tier_name": "large", "display_order": 10, "description": "large guild"}
    assert "已建立資源方案 large" in capsys.readouterr().out


async def test_tier_delete_with_force(monkeypatch, capsys) -> None:
    captured: dict = {}

    async def fake_delete_resource_tier(tier_name: str, force: bool = False) -> bool:
        captured.update({"tier_name": tier_name, "force": force})
        return True

    monkeypatch.setattr(admin_console.admin_operations, "delete_resource_tier", fake_delete_resource_tier)

    await admin_console.handle_command("admin plugin tier delete large force")

    assert captured == {"tier_name": "large", "force": True}
    assert "已刪除資源方案 large" in capsys.readouterr().out


async def test_tier_config_show(monkeypatch, capsys) -> None:
    async def fake_get_plugin_tier_config(plugin_id: str, tier_name: str) -> dict | None:
        assert (plugin_id, tier_name) == ("temp_role_punishment", "default")
        return {"allowed": True}

    monkeypatch.setattr(admin_console.repository, "get_plugin_tier_config", fake_get_plugin_tier_config)

    await admin_console.handle_command("admin plugin tier config show default temp_role_punishment")

    assert "temp_role_punishment@default" in capsys.readouterr().out


async def test_tier_config_set_requires_allowed(monkeypatch, capsys) -> None:
    update_called = False

    async def fake_update_plugin_tier_config(plugin_id: str, tier_name: str, config: dict) -> None:
        nonlocal update_called
        update_called = True

    monkeypatch.setattr(admin_console.admin_operations, "update_plugin_tier_config", fake_update_plugin_tier_config)

    await admin_console.handle_command(
        "admin plugin tier config set default temp_role_punishment execution_quota=10"
    )

    assert update_called is False
    assert "必須提供 allowed=true 或 allowed=false" in capsys.readouterr().out


async def test_tier_config_set_updates_config(monkeypatch, capsys) -> None:
    captured: dict = {}

    async def fake_update_plugin_tier_config(plugin_id: str, tier_name: str, config: dict) -> None:
        captured.update({"plugin_id": plugin_id, "tier_name": tier_name, "config": config})

    monkeypatch.setattr(admin_console.admin_operations, "update_plugin_tier_config", fake_update_plugin_tier_config)

    await admin_console.handle_command(
        "admin plugin tier config set default temp_role_punishment allowed=true execution_quota=10"
    )

    assert captured == {
        "plugin_id": "temp_role_punishment",
        "tier_name": "default",
        "config": {"execution_quota": 10, "allowed": True},
    }
    assert "已更新" in capsys.readouterr().out


async def test_guild_tier_show(monkeypatch, capsys) -> None:
    async def fake_get_guild_resource_tier(guild_id: int) -> str:
        assert guild_id == 1111
        return "default"

    monkeypatch.setattr(admin_console.repository, "get_guild_resource_tier", fake_get_guild_resource_tier)

    await admin_console.handle_command("admin guild tier show 1111")

    assert "套用方案：default" in capsys.readouterr().out


async def test_guild_tier_set(monkeypatch, capsys) -> None:
    captured: dict = {}

    async def fake_set_guild_resource_tier(guild_id: int, tier_name: str) -> None:
        captured.update({"guild_id": guild_id, "tier_name": tier_name})

    monkeypatch.setattr(admin_console.admin_operations, "set_guild_resource_tier", fake_set_guild_resource_tier)

    await admin_console.handle_command("admin guild tier set 1111 large")

    assert captured == {"guild_id": 1111, "tier_name": "large"}
    assert "已將伺服器 1111 套用方案 large" in capsys.readouterr().out


async def test_guild_block_list(monkeypatch, capsys) -> None:
    async def fake_list_plugin_installation_blocks(guild_id: int) -> list[dict]:
        assert guild_id == 1111
        return [{"plugin_id": "temp_role_punishment", "blocked_at": "2026-01-01T00:00:00", "reason": "abuse"}]

    monkeypatch.setattr(
        admin_console.repository, "list_plugin_installation_blocks", fake_list_plugin_installation_blocks
    )

    await admin_console.handle_command("admin guild block list 1111")

    output = capsys.readouterr().out
    assert "temp_role_punishment" in output and "abuse" in output


async def test_guild_block_add(monkeypatch, capsys) -> None:
    captured: dict = {}

    async def fake_block_plugin_installation(guild_id: int, plugin_id: str, reason: str | None = None) -> None:
        captured.update({"guild_id": guild_id, "plugin_id": plugin_id, "reason": reason})

    monkeypatch.setattr(admin_console.admin_operations, "block_plugin_installation", fake_block_plugin_installation)

    await admin_console.handle_command('admin guild block add 1111 temp_role_punishment "spam behavior"')

    assert captured == {"guild_id": 1111, "plugin_id": "temp_role_punishment", "reason": "spam behavior"}
    assert "已封鎖伺服器 1111 安裝外掛 temp_role_punishment" in capsys.readouterr().out


async def test_guild_block_remove(monkeypatch, capsys) -> None:
    async def fake_unblock_plugin_installation(guild_id: int, plugin_id: str) -> bool:
        return True

    monkeypatch.setattr(
        admin_console.admin_operations, "unblock_plugin_installation", fake_unblock_plugin_installation
    )

    await admin_console.handle_command("admin guild block remove 1111 temp_role_punishment")

    assert "已解除封鎖" in capsys.readouterr().out
