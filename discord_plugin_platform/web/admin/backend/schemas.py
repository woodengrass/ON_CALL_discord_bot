"""
Pydantic 請求/回應驗證模型，只涵蓋平台操作者的動作，見 design.md 第 3.5、H.1、H.3 節。
跟 web/public/backend/schemas.py（一般使用者能做的操作）完全分開，不共用模型。
"""

from pydantic import BaseModel


class PluginSummary(BaseModel):
    plugin_id: str
    author_id: int
    name: str
    latest_version: str
    status: str
    pricing_tier: str


class PluginDetail(PluginSummary):
    manifest_json: str
    source_code: str
    required_capabilities: list[str]


class TierConfigInput(BaseModel):
    allowed: bool
    execution_quota: int | None = None
    action_quota: int | None = None
    storage_key_length_limit: int | None = None
    storage_value_bytes_limit: int | None = None
    storage_keys_per_installation_limit: int | None = None
    instruction_limit: int | None = None
    memory_limit_bytes: int | None = None


class ApprovePluginRequest(BaseModel):
    tier_configs: dict[str, TierConfigInput]


class RejectPluginRequest(BaseModel):
    reason_presets: list[str] = []
    custom_reason: str | None = None
    flagged_capabilities: list[str] = []


class BanPluginRequest(BaseModel):
    reason: str


class PricingTierRequest(BaseModel):
    pricing_tier: str


class ResourceTierCreateRequest(BaseModel):
    tier_name: str
    display_order: int
    description: str


class ResourceTierUpdateRequest(BaseModel):
    display_order: int
    description: str


class ResourceTierResponse(BaseModel):
    tier_name: str
    display_order: int
    description: str


class GuildResourceTierRequest(BaseModel):
    tier_name: str


class InstallationBlockRequest(BaseModel):
    reason: str | None = None


class QuotaOverrideRequest(BaseModel):
    execution_quota: int | None = None
    action_quota: int | None = None


class ResourceOverridesRequest(BaseModel):
    storage_key_length_limit: int | None = None
    storage_value_bytes_limit: int | None = None
    storage_keys_per_installation_limit: int | None = None
    instruction_limit: int | None = None
    memory_limit_bytes: int | None = None


class InstallationResponse(BaseModel):
    guild_id: int
    plugin_id: str
    installed_version: str
    enabled: bool | None = None
    execution_quota_override: int | None
    action_quota_override: int | None
    resource_overrides_json: str | None


class ExecutionStatsResponse(BaseModel):
    total: int
    outcome_counts: dict[str, int]
    avg_execution_ms: float | None
    p95_execution_ms: int | None
