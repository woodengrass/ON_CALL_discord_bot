from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from features.verification import cog as verification_cog


class FakeMember:
    """提供驗證核准測試所需的最小成員介面。"""

    def __init__(self, roles: list[object], remove_error: Exception | None = None) -> None:
        self.id = 200
        self.roles = roles
        self.add_roles = AsyncMock()
        self.remove_roles = AsyncMock(side_effect=remove_error)


class FakeReviewMember:
    """提供審核頻道建立測試所需且可作為權限覆寫鍵值的成員介面。"""

    def __init__(self) -> None:
        self.id = 200
        self.name = "member"
        self.mention = "<@200>"
        self.created_at = verification_cog.datetime.datetime.now(verification_cog.datetime.timezone.utc)


@pytest.mark.asyncio
async def test_approve_member_does_not_update_status_when_role_change_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """移除待驗證身分組失敗時，應回復已新增身分組且不得寫入 approved。"""
    restricted_role = object()
    verified_role = object()
    member = FakeMember([restricted_role], remove_error=RuntimeError("remove failed"))
    guild = SimpleNamespace(
        id=100,
        get_role=lambda role_id: {
            1: restricted_role,
            2: verified_role,
        }.get(role_id),
    )
    set_status = AsyncMock()
    add_log_entry = AsyncMock()
    monkeypatch.setattr(verification_cog, "set_status", set_status)
    monkeypatch.setattr(verification_cog, "add_log_entry", add_log_entry)
    monkeypatch.setattr(verification_cog.i18n, "get_text", MagicMock(return_value="approved"))

    verification = object.__new__(verification_cog.Verification)
    result = await verification._approve_member(
        guild,
        member,
        {"restricted_role_id": 1, "verified_role_id": 2},
        "verification_auto_approved",
    )

    assert result is False
    member.add_roles.assert_awaited_once_with(verified_role, reason="approved")
    assert member.remove_roles.await_count == 2
    member.remove_roles.assert_any_await(restricted_role, reason="approved")
    member.remove_roles.assert_any_await(verified_role, reason="驗證核准失敗，回復已驗證身分組")
    set_status.assert_not_awaited()
    add_log_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_member_updates_status_only_after_roles_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """身分組操作完整成功後，才應更新狀態並新增稽核紀錄。"""
    restricted_role = object()
    verified_role = object()
    member = FakeMember([restricted_role])
    guild = SimpleNamespace(
        id=100,
        get_role=lambda role_id: {
            1: restricted_role,
            2: verified_role,
        }.get(role_id),
    )
    set_status = AsyncMock()
    add_log_entry = AsyncMock()
    monkeypatch.setattr(verification_cog, "set_status", set_status)
    monkeypatch.setattr(verification_cog, "add_log_entry", add_log_entry)
    monkeypatch.setattr(verification_cog.i18n, "get_text", MagicMock(return_value="approved"))

    verification = object.__new__(verification_cog.Verification)
    result = await verification._approve_member(
        guild,
        member,
        {"restricted_role_id": 1, "verified_role_id": 2},
        "verification_auto_approved",
    )

    assert result is True
    set_status.assert_awaited_once_with(100, 200, "approved")
    add_log_entry.assert_awaited_once_with(100, 200, "verification_auto_approved", "approved")


@pytest.mark.asyncio
async def test_open_review_channel_cleans_up_when_message_send_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """審核訊息發送失敗時，應刪除新頻道並把建立中狀態復原。"""
    review_channel = SimpleNamespace(
        id=300,
        send=AsyncMock(side_effect=RuntimeError("send failed")),
        delete=AsyncMock(),
    )
    default_role = object()
    bot_member = object()
    member = FakeReviewMember()
    guild = SimpleNamespace(
        id=100,
        categories=[SimpleNamespace(name=verification_cog.REVIEW_CATEGORY_NAME)],
        default_role=default_role,
        me=bot_member,
        get_role=lambda role_id: None,
        create_text_channel=AsyncMock(return_value=review_channel),
    )
    set_review_channel = AsyncMock(return_value=True)
    reset_review_creation = AsyncMock(return_value=True)
    complete_review_creation = AsyncMock(return_value=True)
    monkeypatch.setattr(verification_cog, "set_review_channel", set_review_channel)
    monkeypatch.setattr(verification_cog, "reset_review_creation", reset_review_creation)
    monkeypatch.setattr(verification_cog, "complete_review_creation", complete_review_creation)
    monkeypatch.setattr(verification_cog.i18n, "get_text", MagicMock(return_value="text"))

    verification = object.__new__(verification_cog.Verification)
    result = await verification._open_review_channel(guild, member, {})

    assert result is None
    review_channel.delete.assert_awaited_once()
    reset_review_creation.assert_awaited_once_with(100, 200)
    complete_review_creation.assert_not_awaited()


@pytest.mark.asyncio
async def test_human_check_recovers_missing_entry_with_restricted_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Bot 離線期間加入且沒有紀錄的未驗證成員點擊按鈕時，應補上待驗證身分組與 pending 紀錄。
    """
    restricted_role = object()
    verified_role = object()
    member = SimpleNamespace(id=200, roles=[], add_roles=AsyncMock())
    response = SimpleNamespace(send_message=AsyncMock())
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=100, get_role=lambda role_id: {1: restricted_role, 2: verified_role}.get(role_id)),
        user=member,
        response=response,
    )
    monkeypatch.setattr(verification_cog, "get_entry", AsyncMock(return_value=None))
    create_pending = AsyncMock(return_value=True)
    monkeypatch.setattr(verification_cog, "create_pending", create_pending)
    monkeypatch.setattr(verification_cog, "calculate_risk_score", MagicMock(return_value=2))
    monkeypatch.setattr(verification_cog, "claim_review_creation", AsyncMock(return_value=False))
    monkeypatch.setattr(verification_cog.i18n, "get_text", MagicMock(return_value="text"))

    verification = object.__new__(verification_cog.Verification)
    verification.get_config = MagicMock(return_value={
        "enabled": True,
        "restricted_role_id": 1,
        "verified_role_id": 2,
        "risk_threshold": 2,
    })

    await verification._handle_human_check(interaction)

    member.add_roles.assert_awaited_once_with(restricted_role, reason="text")
    create_pending.assert_awaited_once_with(100, 200, 2)
    response.send_message.assert_awaited_once_with("text", ephemeral=True)


@pytest.mark.asyncio
async def test_human_check_preserves_unrecorded_verified_member(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    沒有紀錄但已具已驗證身分組的成員不應被降為待驗證或重建 pending 紀錄。
    """
    restricted_role = object()
    verified_role = object()
    member = SimpleNamespace(id=200, roles=[verified_role], add_roles=AsyncMock())
    response = SimpleNamespace(send_message=AsyncMock())
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=100, get_role=lambda role_id: {1: restricted_role, 2: verified_role}.get(role_id)),
        user=member,
        response=response,
    )
    create_pending = AsyncMock()
    monkeypatch.setattr(verification_cog, "get_entry", AsyncMock(return_value=None))
    monkeypatch.setattr(verification_cog, "create_pending", create_pending)
    monkeypatch.setattr(verification_cog.i18n, "get_text", MagicMock(return_value="approved"))

    verification = object.__new__(verification_cog.Verification)
    verification.get_config = MagicMock(return_value={
        "enabled": True,
        "restricted_role_id": 1,
        "verified_role_id": 2,
    })

    await verification._handle_human_check(interaction)

    member.add_roles.assert_not_awaited()
    create_pending.assert_not_awaited()
    response.send_message.assert_awaited_once_with("approved", ephemeral=True)


@pytest.mark.asyncio
async def test_human_check_does_not_create_entry_when_restricted_role_addition_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    補發待驗證身分組失敗時不得建立 pending 紀錄，避免未受限制的成員被視為驗證中。
    """
    restricted_role = object()
    verified_role = object()
    member = SimpleNamespace(id=200, roles=[], add_roles=AsyncMock(side_effect=RuntimeError("role failed")))
    response = SimpleNamespace(send_message=AsyncMock())
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=100, get_role=lambda role_id: {1: restricted_role, 2: verified_role}.get(role_id)),
        user=member,
        response=response,
    )
    create_pending = AsyncMock()
    monkeypatch.setattr(verification_cog, "get_entry", AsyncMock(return_value=None))
    monkeypatch.setattr(verification_cog, "create_pending", create_pending)
    monkeypatch.setattr(verification_cog.i18n, "get_text", MagicMock(return_value="role error"))

    verification = object.__new__(verification_cog.Verification)
    verification.get_config = MagicMock(return_value={
        "enabled": True,
        "restricted_role_id": 1,
        "verified_role_id": 2,
    })

    await verification._handle_human_check(interaction)

    create_pending.assert_not_awaited()
    response.send_message.assert_awaited_once_with("role error", ephemeral=True)
