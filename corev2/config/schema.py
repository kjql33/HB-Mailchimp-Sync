"""
Pydantic models for type-safe config validation.

Enforces behavioral invariants from V1:
- INV-001: Three-tier import stream architecture
- INV-002: Compliance lists (762, 773) NEVER in sync lists
- INV-010: Safety gate triple-lock for destructive actions
"""

from typing import List, Dict, Optional
from pydantic import BaseModel, Field, field_validator, SecretStr
from enum import Enum


class RunMode(str, Enum):
    """Environment tier (separate from orchestration mode)."""
    TEST = "test"
    DRY_RUN = "dry-run"
    PROD = "prod"


class ListConfig(BaseModel):
    """Single HubSpot list configuration."""
    id: str = Field(..., description="HubSpot list ID")
    name: str = Field(..., description="List name (for logging)")
    tag: str = Field(..., description="Mailchimp tag to apply for this list")
    additional_tags: List[str] = Field(default_factory=list, description="Extra tags for subdivisions (e.g., T2)")


class SupplementalTagConfig(BaseModel):
    """Supplemental tag configuration - does NOT sync the list itself, just adds tags to contacts already being synced."""
    list_id: str = Field(..., description="HubSpot list ID to scan")
    list_name: str = Field(..., description="List name (for logging)")
    parent_list_id: str = Field(..., description="Only tag contacts who are ALSO in this parent list")
    tag: str = Field(..., description="Tag to apply to contacts in both lists")


class ExclusionsConfig(BaseModel):
    """Exclusion lists applied to import streams."""
    critical: List[str] = Field(default_factory=list, description="Applied to ALL groups (762, 773)")
    active_deals: List[str] = Field(default_factory=list, description="Applied to GROUP 1+2 (717)")
    exit: List[str] = Field(default_factory=list, description="Applied to GROUP 1 only (700-703)")


class HubSpotConfig(BaseModel):
    """HubSpot API configuration."""
    api_key: SecretStr = Field(..., description="HubSpot private app token")
    lists: Dict[str, List[ListConfig]] = Field(
        ...,
        description="Three-tier import streams: general_marketing, special_campaigns, manual_override"
    )
    supplemental_tags: List[SupplementalTagConfig] = Field(
        default_factory=list,
        description="Lists that add tags to contacts already being synced (not synced themselves)"
    )
    exclusions: ExclusionsConfig


class MailchimpConfig(BaseModel):
    """Mailchimp API configuration."""
    api_key: SecretStr = Field(..., description="Mailchimp API key")
    server_prefix: str = Field(..., description="Server prefix (e.g., us1)")
    audience_id: str = Field(..., description="Primary audience list ID")


class SyncConfig(BaseModel):
    """Sync behavior configuration."""
    batch_size: int = Field(default=100, le=100, description="Max batch size (Mailchimp limit)")
    tag_prefix: str = Field(default="", description="Prefix for managed tags")
    ori_lists_field: str = Field(default="ORI_LISTS", description="Source tracking field name")
    force_subscribe: bool = Field(default=True, description="Force status='subscribed' on upsert")


class ExclusionMatrixGroupConfig(BaseModel):
    """Single import stream group configuration."""
    lists: List[str] = Field(..., description="List IDs in this group")
    exclude: List[str] = Field(..., description="Exclusion list IDs applied to this group")


class ExclusionMatrixConfig(BaseModel):
    """Three-tier import stream exclusion matrix (INV-001)."""
    general_marketing: ExclusionMatrixGroupConfig = Field(
        ..., description="GROUP 1: Excludes ALL (critical + active_deals + exit)"
    )
    special_campaigns: ExclusionMatrixGroupConfig = Field(
        ..., description="GROUP 2: Excludes critical + active_deals (bypasses exit)"
    )
    manual_override: ExclusionMatrixGroupConfig = Field(
        ..., description="GROUP 3: Excludes critical only (bypasses deals + exit)"
    )


class ArchivalConfig(BaseModel):
    """Smart archival configuration (INV-006)."""
    exempt_tags: List[str] = Field(default_factory=list, description="Tags that prevent archival")
    preservation_patterns: List[str] = Field(
        default_factory=list, description="Regex patterns for preservation (Manual_*, VIP, etc.)"
    )
    max_archive_per_run: int = Field(
        default=100,
        ge=1,
        description="Maximum number of members to archive per run (safety limit)"
    )


class SafetyConfig(BaseModel):
    """Safety gates for destructive actions (INV-010 triple-lock)."""
    test_contact_limit: int = Field(
        default=0,
        ge=0,
        description="Max contacts to process (0=unlimited, requires allow_unlimited=true)"
    )
    run_mode: RunMode = Field(
        default=RunMode.TEST,
        description="Environment tier. Must be 'prod' for production"
    )
    allow_archive: bool = Field(
        default=False,
        description="Enable archival via DELETE. Required if plan contains archive ops"
    )
    allow_apply: bool = Field(
        default=False,
        description="Enable LIVE mutations. Must be true for apply mode"
    )
    allow_unlimited: bool = Field(
        default=False,
        description="Allow test_contact_limit=0 (unlimited processing)"
    )
    enable_hubspot_writes: bool = Field(
        default=True,
        description="Enable HubSpot property updates (update_hs_property operations). Set false to skip."
    )


class V2Config(BaseModel):
    """Root configuration model."""
    hubspot: HubSpotConfig
    mailchimp: MailchimpConfig
    sync: SyncConfig
    exclusion_matrix: ExclusionMatrixConfig
    list_exclusion_rules: Dict[str, List[str]] = Field(
        ..., description="Anti-remarketing map: source_list → [destination_lists_to_remove_from]"
    )
    secondary_sync_mappings: Dict[str, str] = Field(
        ..., description="Exit tag → destination HubSpot list ID"
    )
    archival: ArchivalConfig
    safety: SafetyConfig
    
    @field_validator("exclusion_matrix")
    @classmethod
    def validate_compliance_lists_never_synced(cls, v: ExclusionMatrixConfig, info) -> ExclusionMatrixConfig:
        """INV-002: Compliance lists (762, 773) NEVER in sync lists."""
        compliance_lists = {"762", "773"}
        
        # Check all groups exclude compliance lists
        for group_name in ["general_marketing", "special_campaigns", "manual_override"]:
            group = getattr(v, group_name)
            
            # Compliance lists must be in exclusion list
            for clist in compliance_lists:
                if clist not in group.exclude:
                    raise ValueError(
                        f"INV-002 VIOLATION: Compliance list {clist} not in {group_name}.exclude"
                    )
            
            # Compliance lists NEVER in sync lists
            for clist in compliance_lists:
                if clist in group.lists:
                    raise ValueError(
                        f"INV-002 VIOLATION: Compliance list {clist} found in {group_name}.lists"
                    )
        
        # Validate exclusion_matrix references only declared list IDs
        if info.data and "hubspot" in info.data:
            hubspot_config = info.data["hubspot"]
            if hasattr(hubspot_config, "lists"):
                all_declared_ids = set()
                for group_lists in hubspot_config.lists.values():
                    for list_config in group_lists:
                        all_declared_ids.add(list_config.id)
                
                # Check all exclusion_matrix list references
                for group_name in ["general_marketing", "special_campaigns", "manual_override"]:
                    group = getattr(v, group_name)
                    for list_id in group.lists:
                        if list_id not in all_declared_ids:
                            raise ValueError(
                                f"exclusion_matrix.{group_name}.lists references undeclared list ID: {list_id}"
                            )
        
        return v
    
    @field_validator("hubspot")
    @classmethod
    def validate_list_ids_unique(cls, v: HubSpotConfig) -> HubSpotConfig:
        """List IDs must be unique across all groups."""
        all_list_ids = []
        for group_name, group_lists in v.lists.items():
            for list_config in group_lists:
                if list_config.id in all_list_ids:
                    raise ValueError(f"Duplicate list ID: {list_config.id} in {group_name}")
                all_list_ids.append(list_config.id)
        return v
    
    def is_production_ready(self) -> bool:
        """Check if all safety gates are satisfied for production."""
        return (
            self.safety.run_mode == RunMode.PROD and
            self.safety.allow_apply and
            (self.safety.test_contact_limit > 0 or self.safety.allow_unlimited)
        )
    
    def can_archive(self) -> bool:
        """Check if archival operations are allowed."""
        return self.safety.allow_archive
