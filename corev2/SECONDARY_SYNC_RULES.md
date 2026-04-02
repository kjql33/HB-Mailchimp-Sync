# Secondary Sync Rules
**Created:** 2026-03-06  
**Updated:** 2026-04-02  
**Status:** Live Production (wired into CLI `apply_mode()` Step 3)  
**Direction:** Mailchimp -> HubSpot (reverse of primary sync)  
**Purpose:** Route exit-tagged contacts from Mailchimp into HubSpot handover lists  
**Entrypoint:** `corev2/cli.py` - runs as Step 3 of `apply_mode()` (both GitHub Actions and local dev)  
**See also:** [PRIMARY_SYNC_RULES.md](PRIMARY_SYNC_RULES.md) for HubSpot -> Mailchimp rules

---

## OVERVIEW

The secondary sync scans Mailchimp for contacts tagged with "Finished" exit tags,
then routes them into the correct HubSpot handover list, cleans up Mailchimp, and archives.

**Trigger:** Contact is tagged in Mailchimp with an exit tag (e.g., "General Single Finished")  
**Result:** Contact lands in the correct HubSpot handover list, removed from Mailchimp

---

## EXIT TAG MAPPINGS (9 total)

| # | Exit Tag (Mailchimp) | Destination List (HubSpot) | Source List (HubSpot) | Remove from Source? | Notes |
|---|---|---|---|---|---|
| 1 | `General Single Finished` | 946 - General Handover | 987 - General Mailchimp Import | No (DYNAMIC) | branches <= 1 |
| 2 | `General Multi Finished` | 946 - General Handover | 987 - General Mailchimp Import | No (DYNAMIC) | branches > 1 |
| 3 | `Recruitment Finished` | 947 - Recruitment Handover | 719 - Recruitment | Yes (MANUAL) | |
| 4 | `Competition Finished` | 948 - Competition Handover | 720 - Competition | Yes (MANUAL) | |
| 5 | `Sub Agents Finished` | 1005 - Sub Agents Handover | 989 - Sub Agents | No (DYNAMIC) | + remove from 900, 972, 971 |
| 6 | `New Agents Finished` | 949 - New Agents Handover | 945 - New Agents | Yes (MANUAL) | |
| 7 | `Sanctioned Finished` | 1006 - Sanctioned Handover | 969 - Sanctioned | Yes (MANUAL) | |
| 8 | `Long Term Single Finished` | (none) | 1032 - Long Term Marketing | No | MC cleanup only |
| 9 | `Long Term Multi Finished` | (none) | 1032 - Long Term Marketing | No | MC cleanup only |

### Why Dynamic Lists Don't Need Removal
Lists 987 (General Mailchimp Import) and 989 (Sub Agents) are **DYNAMIC** - HubSpot auto-manages
membership based on filter criteria. No manual removal needed.

### Why Manual Lists DO Need Removal
Lists 719, 720, 945, 969 are **MANUAL/STATIC** - HubSpot will not auto-remove contacts.
The system must explicitly call `remove_contact_from_list` to prevent the contact from
remaining in both the source and handover lists simultaneously.

### Sub Agents Additional Removals
When "Sub Agents Finished" fires, the system also removes the contact from three static
sublists that feed dynamic list 989:
- **900** - EXP
- **972** - Keller Williams Agents
- **971** - IAD Agents

This ensures the contact is fully cleaned out of all Sub Agents pipelines.

### Long Term Mappings (No Destination)
Mappings 8 and 9 have NO destination list. When a Long Term journey completes:
- Contact stays in HubSpot list 1032
- Mailchimp tags are removed
- Contact is archived from Mailchimp
- No HubSpot handover list is involved (MC cleanup only)

---

## EXEMPT TAGS

```yaml
exempt_tags:
  - "Manual Inclusion"
```

Contacts with the `Manual Inclusion` tag in Mailchimp are **SKIPPED ENTIRELY** by secondary sync.
Even if they acquire a "Finished" exit tag, they will NOT be processed. This protects manually
included contacts (from list 784) from being accidentally archived.

---

## PER-CONTACT OPERATION CHAIN

For each exit-tagged contact found in Mailchimp, the following operations execute **in order**:

| Step | Operation | Description | Condition |
|---|---|---|---|
| 1 | `add_hs_to_list` | Add contact to destination handover list | Only if destination_list is set |
| 2 | `remove_hs_from_list` | Remove from source list in HubSpot | Manual lists only (719, 720, 945, 969) |
| 2b | `remove_hs_from_list` | Remove from additional lists | Sub Agents only (900, 972, 971) |
| 3 | `remove_mc_tag` | Remove ALL tags from Mailchimp (clean slate) | When `archive_after_sync: true` |
| 4 | `archive_mc_member` | Archive contact from Mailchimp | When `archive_after_sync: true` |

### Example: "Recruitment Finished" Contact
1. Add to HubSpot list **947** (Recruitment Handover)
2. Remove from HubSpot list **719** (Recruitment) - manual list
3. Remove ALL Mailchimp tags (Recruitment, Recruitment Finished, etc.)
4. Archive from Mailchimp - journey complete

### Example: "General Single Finished" Contact
1. Add to HubSpot list **946** (General Handover)
2. Skip removal - list 987 is DYNAMIC
3. Remove ALL Mailchimp tags
4. Archive from Mailchimp

### Example: "Sub Agents Finished" Contact
1. Add to HubSpot list **1005** (Sub Agents Handover)
2. Skip removal from 989 - DYNAMIC
3. Remove from lists **900** (EXP), **972** (Keller Williams), **971** (IAD Agents)
4. Remove ALL Mailchimp tags
5. Archive from Mailchimp

### Example: "Long Term Single Finished" Contact
1. NO HubSpot handover (no destination list)
2. Contact stays in list 1032
3. Remove ALL Mailchimp tags
4. Archive from Mailchimp - MC cleanup only

### Example: Contact with "Manual Inclusion" Tag
1. SKIPPED entirely - exempt_tags match
2. Even if "General Single Finished" tag exists, NO operations generated
3. Contact stays in Mailchimp untouched

---

## SAFETY RULES

### SEC-001: No Global Archival
Secondary sync **only** archives contacts it processes (those with exit tags).
It does NOT run any global archival sweep. Primary sync handles that separately.

### SEC-002: Exit Tags Are the Trigger
Only contacts with a configured exit tag in Mailchimp are processed.
No exit tag = no action. The system never guesses or infers.

### SEC-003: HubSpot Lookup Required
Every contact must be found in HubSpot by email before any operations execute.
If a contact has an exit tag but does NOT exist in HubSpot, it is **skipped** with a warning.

### SEC-004: Untag Before Archive
Tags are removed from Mailchimp **before** archiving. This ensures a clean state
and prevents stale tag data if the contact is ever unarchived later.

### SEC-005: Idempotent Operations
- `add_hs_to_list`: Already in list = success (no duplicate add)
- `remove_hs_from_list`: Already removed = success (404 = ok)
- `remove_mc_tag`: Tag doesn't exist = success
- `archive_mc_member`: Already archived = success

### SEC-006: Contact Limit
`contact_limit: 0` means unlimited. Can be set to a positive number to cap
processing during testing.

### SEC-007: Archive Gate
`archive_after_sync: true` enables steps 3 + 4 (untag + archive).
If set to `false`, the system only performs steps 1 + 2 (HubSpot list moves).

### SEC-008: Exempt Tags
Contacts with any tag in `exempt_tags` are NEVER processed by secondary sync.
Currently: `["Manual Inclusion"]`.

### SEC-009: Optional Destination List
Mappings without a `destination_list` (Long Term Single/Multi Finished) skip the
`add_hs_to_list` step entirely. Only MC cleanup (untag + archive) is performed.

### SEC-010: Audience Cap Integration
Secondary sync shares the `AudienceCapGuard` with primary sync. The cap is checked
before any operations that might add subscribers (though secondary sync typically
archives rather than adds).

---

## IMPLEMENTATION FILES

| File | Purpose |
|---|---|
| `corev2/planner/secondary.py` | SecondaryPlanner - scans MC, generates operations |
| `corev2/config/schema.py` | SecondaryMappingConfig + SecondarySyncConfig models |
| `corev2/config/production.yaml` | 9 exit tag mappings + settings |
| `corev2/executor/engine.py` | Executor handlers (add_hs_to_list, remove_mc_tag, etc.) |
| `corev2/cli.py` | Step 3 of `apply_mode()` - runs secondary sync after primary |
| `corev2/notifications.py` | Teams webhook alerts (audience cap, errors) |
| `main.py` | Thin wrapper - delegates to `cli.sync_mode()` |

---

## EXECUTION FLOW (in corev2/cli.py)

```
apply_mode():
  STEP 1: Unsubscribe Sync (Mailchimp -> HubSpot opt-outs)
  STEP 2: Primary Sync (HubSpot -> Mailchimp: tags, subscribe, orphan cleanup)
  STEP 3: Secondary Sync (Mailchimp -> HubSpot exit tag routing)  <-- THIS
    Phase 1: Scan Mailchimp for exit-tagged contacts
    Phase 2: Filter out exempt_tags contacts
    Phase 3: Look up each in HubSpot, generate operations
    Phase 4: Execute operations (add to list, remove, untag, archive)
```

sync_mode() = plan_mode() + apply_mode() in sequence (convenience for local dev).
GitHub Actions calls `python -m corev2.cli plan` then `python -m corev2.cli apply`.

---

## PRODUCTION CONFIG (production.yaml)

```yaml
secondary_sync:
  enabled: true
  archive_after_sync: true
  contact_limit: 0  # UNLIMITED
  exempt_tags:
    - "Manual Inclusion"
  mappings:
    - exit_tag: "General Single Finished"
      destination_list: "946"
      destination_name: "General Handover"
      source_list: "987"
      source_name: "General Mailchimp Import"
      remove_from_source: false

    - exit_tag: "General Multi Finished"
      destination_list: "946"
      destination_name: "General Handover"
      source_list: "987"
      source_name: "General Mailchimp Import"
      remove_from_source: false

    - exit_tag: "Recruitment Finished"
      destination_list: "947"
      destination_name: "Recruitment Handover"
      source_list: "719"
      source_name: "Recruitment"
      remove_from_source: true

    - exit_tag: "Competition Finished"
      destination_list: "948"
      destination_name: "Competition Handover"
      source_list: "720"
      source_name: "Competition"
      remove_from_source: true

    - exit_tag: "Sub Agents Finished"
      destination_list: "1005"
      destination_name: "Sub Agents Handover"
      source_list: "989"
      source_name: "Sub Agents"
      remove_from_source: false
      additional_remove_lists:
        - list_id: "900"
          list_name: "EXP"
        - list_id: "972"
          list_name: "Keller Williams Agents"
        - list_id: "971"
          list_name: "IAD Agents"

    - exit_tag: "New Agents Finished"
      destination_list: "949"
      destination_name: "New Agents Handover"
      source_list: "945"
      source_name: "New Agents"
      remove_from_source: true

    - exit_tag: "Sanctioned Finished"
      destination_list: "1006"
      destination_name: "Sanctioned Handover"
      source_list: "969"
      source_name: "Sanctioned"
      remove_from_source: true

    - exit_tag: "Long Term Single Finished"
      source_list: "1032"
      source_name: "Long Term Marketing"
      remove_from_source: false

    - exit_tag: "Long Term Multi Finished"
      source_list: "1032"
      source_name: "Long Term Marketing"
      remove_from_source: false
```

---

## VERIFIED BEHAVIORS

| # | Feature | Status | Notes |
|---|---------|--------|-------|
| 1 | Exit tag detection | VERIFIED | Scans all MC members, filters by configured exit tags |
| 2 | HubSpot list addition | VERIFIED | Adds to correct handover list, idempotent |
| 3 | Source list removal (manual) | VERIFIED | Removes from 719/720/945/969, skips dynamic 987/989 |
| 4 | Sub Agents additional removals | VERIFIED | Cleans 900, 972, 971 on Sub Agents Finished |
| 5 | Tag cleanup before archive | VERIFIED | Removes ALL tags before archiving |
| 6 | Mailchimp archive | VERIFIED | Archives after tag cleanup |
| 7 | HubSpot lookup required | VERIFIED | Skips with warning if not found |
| 8 | Exempt tags (Manual Inclusion) | VERIFIED | Contacts with exempt tags entirely skipped |
| 9 | Long Term MC cleanup only | VERIFIED | No HubSpot handover, untag + archive only |
| 10 | Audience cap integration | VERIFIED | Shared guard with primary sync |

---
