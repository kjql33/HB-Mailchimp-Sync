"""
Pydantic config models - updated for full production rule set.

Changes from previous version:
- MailchimpConfig replaced by MauticConfig
- ListConfig now supports tag_overrides (branch split) and additional_tags
- ExclusionMatrixConfig now has 4 groups (added long_term_marketing)
- SecondarySyncConfig supports additional_remove_lists and optional destination_list
- MauticConfig replaces audience_id with audience_cap
- SafetyConfig unchanged
- Teams notifications config added
"""

from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field, field_validator, SecretStr
from enum import Enum


class RunMode(str, Enum):
    TEST = "test"
    DRY_RUN = "dry-run"
    PROD = "prod"


class TagOverrideConfig(BaseModel):
    """Conditional tag override based on a HubSpot property value."""
    condition: str = Field(..., description="e.g. 'branches > 1'")
    tag: str = Field(..., description="Tag to apply when condition is true")


class ListConfig(BaseModel):
    """Single HubSpot list configuration."""
    id: str
    name: str
    tag: str
    additional_tags: List[str] = Field(default_factory=list)
    tag_overrides: List[TagOverrideConfig] = Field(
        default_factory=list,
        description="Conditional tag overrides based on contact properties"
    )


class SupplementalTagConfig(BaseModel):
    """Supplemental tag - adds extra tag to contacts already being synced."""
    list_id: str
    list_name: str
    parent_list_id: str
    tag: str


class ExclusionsConfig(BaseModel):
    """Exclusion lists."""
    critical: List[str] = Field(default_factory=list)
    active_deals: List[str] = Field(default_factory=list)
    exit: List[str] = Field(default_factory=list)


class HubSpotConfig(BaseModel):
    """HubSpot API configuration."""
    api_key: SecretStr
    lists: Dict[str, List[ListConfig]]
    supplemental_tags: List[SupplementalTagConfig] = Field(default_factory=list)
    exclusions: ExclusionsConfig


class MauticConfig(BaseModel):
    """Mautic API configuration - replaces MailchimpConfig."""
    base_url: str = Field(..., description="Root Mautic URL e.g. https://mautic.yourdomain.com")
    username: SecretStr
    password: SecretStr
    audience_cap: int = Field(default=5000, description="Hard subscriber limit - blocks sync if reached")


# Legacy alias so any code referencing mailchimp_config still resolves
MailchimpConfig = MauticConfig


class SyncConfig(BaseModel):
    batch_size: int = Field(default=100, le=200)
    tag_prefix: str = Field(default="")
    ori_lists_field: str = Field(default="ORI_LISTS")
    force_subscribe: bool = Field(default=True)


class ExclusionMatrixGroupConfig(BaseModel):
    lists: List[str]
    exclude: List[str]


class ExclusionMatrixConfig(BaseModel):
    """Four-group exclusion matrix (updated from 3 groups)."""
    general_marketing: ExclusionMatrixGroupConfig
    special_campaigns: ExclusionMatrixGroupConfig
    manual_override: ExclusionMatrixGroupConfig
    long_term_marketing: ExclusionMatrixGroupConfig = Field(
        default_factory=lambda: ExclusionMatrixGroupConfig(lists=[], exclude=["762", "773", "717"])
    )


class ArchivalConfig(BaseModel):
    exempt_tags: List[str] = Field(default_factory=list)
    preservation_patterns: List[str] = Field(default_factory=list)
    max_archive_per_run: int = Field(default=100, ge=1)


class AdditionalRemoveList(BaseModel):
    """Additional HubSpot list to remove contact from during secondary sync."""
    list_id: str
    list_name: str


class SecondaryMappingConfig(BaseModel):
    """Single exit tag mapping. destination_list is optional (Long Term = MC cleanup only)."""
    exit_tag: str
    destination_list: Optional[str] = Field(default=None)
    destination_name: Optional[str] = Field(default=None)
    source_list: str
    source_name: str
    remove_from_source: bool = Field(default=False)
    additional_remove_lists: List[AdditionalRemoveList] = Field(
        default_factory=list,
        description="Extra HubSpot lists to remove contact from (e.g. Sub Agents sublists 900, 972, 971)"
    )


class SecondarySyncConfig(BaseModel):
    enabled: bool = Field(default=False)
    archive_after_sync: bool = Field(default=True)
    contact_limit: int = Field(default=0, ge=0)
    exempt_tags: List[str] = Field(
        default_factory=list,
        description="Contacts with these tags are skipped entirely by secondary sync"
    )
    mappings: List[SecondaryMappingConfig] = Field(default_factory=list)


class TeamsNotificationsConfig(BaseModel):
    """Microsoft Teams webhook notifications config."""
    enabled: bool = Field(default=False)
    webhook_url: Optional[str] = Field(default=None)


class SafetyConfig(BaseModel):
    test_contact_limit: int = Field(default=0, ge=0)
    run_mode: RunMode = Field(default=RunMode.TEST)
    allow_archive: bool = Field(default=False)
    allow_apply: bool = Field(default=False)
    allow_unlimited: bool = Field(default=False)
    enable_hubspot_writes: bool = Field(default=True)


class V2Config(BaseModel):
    """Root configuration model."""
    hubspot: HubSpotConfig
    mautic: MauticConfig
    sync: SyncConfig
    exclusion_matrix: ExclusionMatrixConfig
    list_exclusion_rules: Dict[str, List[str]] = Field(default_factory=dict)
    secondary_sync: SecondarySyncConfig = Field(default_factory=SecondarySyncConfig)
    archival: ArchivalConfig
    safety: SafetyConfig
    notifications: TeamsNotificationsConfig = Field(default_factory=TeamsNotificationsConfig)

    @property
    def mailchimp(self) -> MauticConfig:
        """Legacy alias so existing code using config.mailchimp still works."""
        return self.mautic

    @field_validator("exclusion_matrix")
    @classmethod
    def validate_compliance_lists_never_synced(cls, v, info):
        """INV-002: Compliance lists 762 and 773 must never appear in sync lists."""
        compliance_lists = {"762", "773"}
        for group_name in ["general_marketing", "special_campaigns", "manual_override", "long_term_marketing"]:
            group = getattr(v, group_name)
            for clist in compliance_lists:
                if clist not in group.exclude:
                    raise ValueError(f"INV-002 VIOLATION: {clist} not in {group_name}.exclude")
            for clist in compliance_lists:
                if clist in group.lists:
                    raise ValueError(f"INV-002 VIOLATION: {clist} found in {group_name}.lists")
        if info.data and "hubspot" in info.data:
            hs = info.data["hubspot"]
            if hasattr(hs, "lists"):
                declared = {lc.id for gl in hs.lists.values() for lc in gl}
                for group_name in ["general_marketing", "special_campaigns", "manual_override", "long_term_marketing"]:
                    group = getattr(v, group_name)
                    for lid in group.lists:
                        if lid not in declared:
                            raise ValueError(f"exclusion_matrix.{group_name}.lists references undeclared list ID: {lid}")
        return v

    @field_validator("hubspot")
    @classmethod
    def validate_list_ids_unique(cls, v):
        seen = []
        for group_name, group_lists in v.lists.items():
            for lc in group_lists:
                if lc.id in seen:
                    raise ValueError(f"Duplicate list ID: {lc.id} in {group_name}")
                seen.append(lc.id)
        return v

    def is_production_ready(self) -> bool:
        return (
            self.safety.run_mode == RunMode.PROD
            and self.safety.allow_apply
            and (self.safety.test_contact_limit > 0 or self.safety.allow_unlimited)
        )

    def can_archive(self) -> bool:
        return self.safety.allow_archive

    def get_all_source_tags(self) -> set:
        """Return all possible import tags from config (used for orphan detection)."""
        tags = set()
        for group_lists in self.hubspot.lists.values():
            for lc in group_lists:
                tags.add(lc.tag)
                tags.update(lc.additional_tags)
                for override in lc.tag_overrides:
                    tags.add(override.tag)
        return tags

    def get_all_exit_tags(self) -> set:
        """Return all configured exit tags."""
        return {m.exit_tag for m in self.secondary_sync.mappings}
