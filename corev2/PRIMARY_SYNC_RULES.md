# Primary Sync Rules
**Last Updated:** 2026-05-11
**Scope:** Primary sync (HubSpot → Mautic)
**Status:** Live Production
**Mode:** Unlimited contacts, archival enabled, audience cap 5,000
**Schedule:** Every 8 hours via GitHub Actions (00:00, 08:00, 16:00 UTC)
**Entrypoint:** `corev2/cli.py` → `plan` then `apply`
**Config:** `corev2/config/production.yaml`
**See also:** [SECONDARY_SYNC_RULES.md](SECONDARY_SYNC_RULES.md) for Mautic → HubSpot exit tag routing

---

## SYSTEM OVERVIEW

The primary sync reads HubSpot lists, applies business rules (exclusion matrix, tag overrides, first-tag priority), and syncs eligible contacts into Mautic with appropriate tags. Mautic campaigns then drive email journeys based on these tags.

### Execution Flow (every 8 hours)
```
Step 1:  Unsubscribe Sync       - Mautic unsubs → HubSpot opt-out status
Step 1B: Cleaned Contact Sync   - Mautic cleaned/bounced → strip tags + set hs_email_bad_address
Step 2:  Primary Sync (THIS)    - HubSpot lists → Mautic (tags, subscribe, orphan cleanup)
Step 3:  Secondary Sync         - Mautic exit tags → HubSpot handover lists
```

---

## SYNCED LISTS & TAGS

### GROUP 1: General Marketing
Priority order (first match wins): Sanctioned → Recruitment → Competition → Sub Agents → New Agents → General

| List ID | HubSpot Name | Mautic Tag | Tag Override | Type |
|---------|-------------|------------|-------------|------|
| 969 | Sanctioned | `Sanctioned` | - | Manual |
| 719 | Recruitment | `Recruitment` | - | Manual |
| 720 | Competition | `Competition` | - | Manual |
| 1050 | Sub Agents | `Sub Agents` | - | Static |
| 945 | New Agents | `New Agents` | - | Manual |
| 987 | General Mailchimp Import | `General Single` | branches > 1 → `General Multi` | Dynamic |

### GROUP 2: Special Campaigns
Empty — reserved for future use.

### GROUP 3: Manual Override
| List ID | HubSpot Name | Mautic Tag | Tag Override | Additional Tags |
|---------|-------------|------------|-------------|----------------|
| 784 | Manual Inclusion MC | `General Single` | branches > 1 → `General Multi` | `Manual Inclusion` |

**Purpose:** Force-sync contacts that would normally be blocked by Active Deals (717). Only compliance lists (762, 773) can block GROUP 3.

### GROUP 4: Long Term Marketing
| List ID | HubSpot Name | Mautic Tag | Tag Override |
|---------|-------------|------------|-------------|
| 1032 | Long Term Marketing | `General Single Long Term` | branches > 1 → `General Multi Long Term` |

**Purpose:** Separate long-term marketing journey for contacts that need a different cadence. Same exclusions as GROUP 1.

---

## EXCLUSION MATRIX

| Group | Sync Lists | Excluded By | Effect |
|-------|-----------|------------|--------|
| GROUP 1 (General Marketing) | 969, 719, 720, 1050, 945, 987 | 762, 773, **717** | Blocked by compliance + active deals |
| GROUP 2 (Special Campaigns) | (empty) | 762, 773, 717 | — |
| GROUP 3 (Manual Override) | 784 | 762, 773 | Bypasses active deals (717) |
| GROUP 4 (Long Term Marketing) | 1032 | 762, 773, **717** | Same as GROUP 1 |

**Exclusion Lists (NEVER modify manually — all DYNAMIC):**
- **762** — Unsubscribed/Opted Out (auto-populated by HubSpot when contacts opt out)
- **773** — Manual Disengagement (criteria-based dynamic list)
- **717** — Active Deals (auto-managed by HubSpot deal pipeline)

### Exclusion Cleanup
If a contact is already in Mautic but later appears in an exclusion list:
1. Contact is detected during plan generation (two-pass reconciliation)
2. All Mautic tags removed
3. Contact archived from Mautic
4. Contact removed from any manual sync lists in HubSpot

---

## BRANCH SPLIT (TAG OVERRIDES)

Contacts from lists 719, 987, 784, and 1032 are tagged based on the HubSpot `branches` property:

| Condition | List 719 Tag | List 987 Tag | List 784 Tag | List 1032 Tag |
|-----------|-------------|-------------|-------------|---------------|
| branches ≤ 1 (or empty) | `Recruitment` | `General Single` | `General Single` | `General Single Long Term` |
| branches > 1 | `General Multi` | `General Multi` | `General Multi` | `General Multi Long Term` |

List 784 also receives the additional tag `Manual Inclusion` regardless of branch count.

**Note:** List 719 (Recruitment) with `branches > 1` maps to `General Multi`, routing multi-branch recruitment contacts into the General Multi campaign instead of Recruitment.

---

## INVARIANT RULES

### INV-001: Import Stream Architecture
Four groups with priority order: General Marketing → Special Campaigns → Manual Override → Long Term Marketing. First group match wins.

### INV-002: Compliance Lists Never Synced
Lists 762 and 773 are NEVER included in sync lists. Validated at config load time by Pydantic schema validator.

### INV-004: Single-Tag Enforcement
Each contact gets exactly ONE primary campaign tag in Mautic. No dual enrollment.

### INV-004a: First-Tag Priority
If a contact already has a source tag in Mautic, it is preserved. The system will NOT switch a contact's tag even if their HubSpot list membership changes. This prevents mid-journey disruption.
- Exception: Fresh Mautic install (0 contacts) — per-contact lookup skipped for performance.

### INV-005: Never Resubscribe Opted-Out
Contacts with `unsubscribed` or `cleaned` status in Mautic are NEVER resubscribed. Only merge fields are updated. Archived contacts CAN be restored if they reappear in an active HubSpot list.

### INV-006: Smart Archival
Orphaned contacts (in Mautic with a source tag but NOT in any HubSpot sync list) are untagged then archived. Exempt tags prevent archival: `VIP`, `DoNotArchive`, `Manual_Override`, `Manual Inclusion`.
- Max 100 archives per run (safety limit, configurable via `archival.max_archive_per_run`)
- Preservation patterns: `^Manual_.*`, `^Custom_.*`, `^Team_.*`

### INV-008: ORI_LISTS Tracking
The HubSpot custom property `ORI_LISTS` is updated with comma-separated list IDs for each contact. Currently **disabled** (`enable_hubspot_writes: false`).

### INV-010: Triple-Lock Safety Gates
All four gates must pass before live mutations:
1. `run_mode: prod`
2. `allow_apply: true`
3. `test_contact_limit: 0` requires `allow_unlimited: true`
4. Archive operations require `allow_archive: true`

Config hash verification: `apply` mode verifies its plan was generated by the current config — prevents stale plan execution.

---

## MAUTIC AUDIENCE CAP

**Hard limit: 5,000 contacts**

The `AudienceCapGuard` (in `corev2/executor/engine.py`) enforces this:

| Phase | Action |
|-------|--------|
| **Pre-flight** | Fetches live contact count from Mautic API. If already ≥ 5,000 → entire run **aborts** + Teams alert |
| **Per-contact** | Before each `upsert_mc_member`, checks remaining slots. If cap hit → **skips contact** |
| **Re-check** | Every 10 new subscribers, re-fetches live count from Mautic API |
| **Proximity warning** | If < 50 slots remain at pre-flight → Teams warning sent |
| **Alert** | Immediate Teams webhook when cap is reached |

Only `created` and `restored_from_archive` upsert results count toward the cap. Existing contacts getting tag/field updates do NOT consume a slot.

The cap guard is shared across primary and secondary executors in the same run.

---

## TEAMS NOTIFICATIONS

Module: `corev2/notifications.py`

| Event | Alert Type |
|-------|-----------|
| Audience cap reached | Immediate alert with count/cap/skipped stats |
| Approaching cap (< 50 slots) | Warning with remaining slots |

Config: `notifications.enabled` + `TEAMS_WEBHOOK_URL` secret.

---

## VERIFIED BEHAVIORS

| # | Feature | Status | Notes |
|---|---------|--------|-------|
| 1 | Multi-list sync (8 lists, 4 groups) | VERIFIED | Code-verified May 2026 |
| 2 | Idempotent operations | VERIFIED | Re-runs generate 0 changes if state matches |
| 3 | Orphan detection & archival | VERIFIED | Untag → archive, max 100/run |
| 4 | Compliance state handling | VERIFIED | Skips unsubscribed/bounced, never resubscribes |
| 5 | Exclusion list enforcement | VERIFIED | 762, 773, 717 properly excluded |
| 6 | Unsubscribe sync (Mautic → HS) | VERIFIED | Step 1 of every run |
| 17 | Cleaned contact bounce sync (Mautic → HS) | VERIFIED | Step 1B: strips tags, sets hs_email_bad_address=true |
| 7 | Tag cleanup on restore from archive | VERIFIED | Removes ALL old tags before applying new |
| 8 | Restore from archive | VERIFIED | Archived contacts restored when back in active list |
| 9 | First-tag priority (INV-004a) | VERIFIED | Existing tags preserved, no campaign switching |
| 10 | Merge fields update | VERIFIED | FNAME/LNAME propagated from HubSpot |
| 11 | Branch split (tag overrides) | VERIFIED | branches > 1 → Multi tag variant |
| 12 | Manual Inclusion (list 784) | VERIFIED | Bypasses 717, gets "Manual Inclusion" additional tag |
| 13 | Long Term Marketing (list 1032) | VERIFIED | Separate Long Term tags, same exclusions as GROUP 1 |
| 14 | Audience cap (5,000) | VERIFIED | Pre-flight + per-contact + re-check + Teams alert |
| 15 | Auto-refresh list names | NOT PORTED | Main branch only — manual YAML update required if HubSpot lists renamed |
| 16 | Exclusion cleanup | VERIFIED | Contacts entering exclusion lists → removed from Mautic + HS sync lists |

---

## CLI COMMAND REFERENCE

```bash
# Validate config only (no API calls)
python -m corev2.cli validate-config

# Generate plan only (safe, read-only)
python -m corev2.cli plan

# Execute plan (LIVE — runs unsub sync + primary + secondary)
python -m corev2.cli apply

# Plan + Apply in one command (local dev convenience)
python -m corev2.cli sync

# Debug single contact
python -m corev2.cli plan --only-email user@example.com --output /tmp/debug.json

# Dry-run apply (no mutations)
python -m corev2.cli apply --plan corev2/artifacts/plan.json --dry-run
```

---

## PRODUCTION CONFIG SNAPSHOT

**File:** `corev2/config/production.yaml`
**Last verified:** 2026-05-11

```yaml
hubspot:
  lists:
    general_marketing:
      - {id: "969", name: "Sanctioned", tag: "Sanctioned"}
      - {id: "719", name: "Recruitment", tag: "Recruitment"}
      - {id: "720", name: "Competition", tag: "Competition"}
      - {id: "1050", name: "Sub Agents", tag: "Sub Agents"}
      - {id: "945", name: "New Agents", tag: "New Agents"}
      - id: "987"
        name: "General Mailchimp Import"
        tag: "General Single"
        tag_overrides:
          - condition: "branches > 1"
            tag: "General Multi"
    special_campaigns: []
    manual_override:
      - id: "784"
        name: "Manual Inclusion MC"
        tag: "General Single"
        additional_tags: ["Manual Inclusion"]
        tag_overrides:
          - condition: "branches > 1"
            tag: "General Multi"
    long_term_marketing:
      - id: "1032"
        name: "Long Term Marketing"
        tag: "General Single Long Term"
        tag_overrides:
          - condition: "branches > 1"
            tag: "General Multi Long Term"

exclusion_matrix:
  general_marketing:
    lists: ["969", "719", "720", "1050", "945", "987"]
    exclude: ["762", "773", "717"]
  special_campaigns:
    lists: []
    exclude: ["762", "773", "717"]
  manual_override:
    lists: ["784"]
    exclude: ["762", "773"]
  long_term_marketing:
    lists: ["1032"]
    exclude: ["762", "773", "717"]

mautic:
  base_url: ${MAUTIC_BASE_URL}
  username: ${MAUTIC_USERNAME}
  password: ${MAUTIC_PASSWORD}
  audience_cap: 5000

safety:
  run_mode: prod
  allow_apply: true
  allow_archive: true
  allow_unlimited: true
  enable_hubspot_writes: false

archival:
  exempt_tags: [VIP, DoNotArchive, Manual_Override, "Manual Inclusion"]
  preservation_patterns: ["^Manual_.*", "^Custom_.*", "^Team_.*"]
  max_archive_per_run: 100
```

---
