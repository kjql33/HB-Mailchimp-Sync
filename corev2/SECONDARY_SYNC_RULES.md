# Secondary Sync Rules & Verified Behaviors
**Created:** 2026-03-06  
**Updated:** 2026-04-01  
**Status:** Live Production (wired into CLI `apply_mode()` Step 3)  
**Direction:** Mailchimp → HubSpot (reverse of primary sync)  
**Purpose:** Route exit-tagged contacts from Mailchimp into HubSpot handover lists  
**Entrypoint:** `corev2/cli.py` — runs as Step 3 of `apply_mode()` (both GitHub Actions and local dev)  
**See also:** [PRIMARY_SYNC_RULES.md](PRIMARY_SYNC_RULES.md) for HubSpot → Mailchimp rules

---

## 📋 OVERVIEW

The secondary sync scans Mailchimp for contacts tagged with "Finished" exit tags,
then routes them into the correct HubSpot handover list, cleans up Mailchimp, and archives.

**Trigger:** Contact is tagged in Mailchimp with an exit tag (e.g., "General Finished")  
**Result:** Contact lands in the correct HubSpot handover list, removed from Mailchimp

---

## 🗺️ EXIT TAG MAPPINGS

| # | Exit Tag (Mailchimp) | Destination List (HubSpot) | Source List (HubSpot) | Remove from Source? |
|---|---|---|---|---|
| 1 | `General Finished` | 946 — General Handover | 987 — General | No (DYNAMIC) |
| 2 | `Recruitment Finished` | 947 — Recruitment Handover | 719 — Recruitment | Yes (MANUAL) |
| 3 | `Competition Finished` | 948 — Competition Handover | 720 — Competition | Yes (MANUAL) |
| 4 | `Sub Agents Finished` | 1005 — Sub Agents Handover | 989 — Network Agents | No (DYNAMIC) |
| 5 | `New Agents Finished` | 949 — New Agents Handover | 945 — New Agents | Yes (MANUAL) |
| 6 | `Sanctioned Finished` | 1006 — Sanctioned Handover | 969 — Sanctioned | Yes (MANUAL) |

**All list IDs verified against HubSpot API on 2026-03-06.**

### Why Dynamic Lists Don't Need Removal
Lists 987 (General) and 989 (Network Agents) are **DYNAMIC** — HubSpot auto-manages membership
based on filter criteria. When a contact is added to a handover list, the dynamic filter
automatically excludes them. No manual removal needed.

### Why Manual Lists DO Need Removal
Lists 719, 720, 945, 969 are **MANUAL/STATIC** — HubSpot will not auto-remove contacts.
The system must explicitly call `remove_contact_from_list` to prevent the contact from
remaining in both the source and handover lists simultaneously.

---

## ⚙️ PER-CONTACT OPERATION CHAIN

For each exit-tagged contact found in Mailchimp, the following operations execute **in order**:

| Step | Operation | Description | Condition |
|---|---|---|---|
| 1 | `add_hs_to_list` | Add contact to destination handover list in HubSpot | Always |
| 2 | `remove_hs_from_list` | Remove from source list in HubSpot | Manual lists only (719, 720, 945, 969) |
| 3 | `remove_mc_tag` | Remove ALL tags from Mailchimp (clean slate) | When `archive_after_sync: true` |
| 4 | `archive_mc_member` | Archive contact from Mailchimp | When `archive_after_sync: true` |

### Example: "Recruitment Finished" Contact
1. ✅ Add to HubSpot list **947** (Recruitment Handover)
2. ✅ Remove from HubSpot list **719** (Recruitment) — manual list
3. ✅ Remove ALL Mailchimp tags (Recruitment, Recruitment Finished, etc.)
4. ✅ Archive from Mailchimp — journey complete

### Example: "General Finished" Contact (from List 987)
1. ✅ Add to HubSpot list **946** (General Handover)
2. ⊘ *Skip* — list 987 is DYNAMIC, HubSpot auto-excludes
3. ✅ Remove ALL Mailchimp tags
4. ✅ Archive from Mailchimp

### Example: "General Finished" Contact (from Manual Inclusion List 784)
1. ✅ Add to HubSpot list **946** (General Handover)
2. ⊘ *Skip removal from 987* — may not be in 987 (404 = success)
3. ✅ Remove ALL Mailchimp tags
4. ✅ Archive from Mailchimp
5. ⚠️ Contact stays in list 784 permanently — secondary sync does NOT touch 784

---

## 🔒 SAFETY RULES

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
- `remove_hs_from_list`: Already removed = success
- `remove_mc_tag`: Tag doesn't exist = success
- `archive_mc_member`: Already archived = success

### SEC-006: Contact Limit
`contact_limit: 0` means unlimited. Can be set to a positive number to cap
processing during testing. Only affects secondary sync, not primary.

### SEC-007: Archive Gate
`archive_after_sync: true` enables steps 3 + 4 (untag + archive).
If set to `false`, the system only performs steps 1 + 2 (HubSpot list moves).

---

## 📊 HANDOVER LIST TRACKING

### Membership Timestamps (for 2-week tracking)
HubSpot's `/crm/v3/lists/{listId}/memberships` endpoint returns `membershipTimestamp`
for every contact — the exact moment they were added to that specific list.

**Verified 2026-03-06:** API returns ISO-8601 timestamps per record.

```json
{
  "results": [
    { "recordId": "123", "membershipTimestamp": "2026-03-06T14:30:00Z" }
  ]
}
```

This can be queried at any time to determine how long a contact has been in a handover list.
No local tracking needed — HubSpot stores this natively.

---

## 🏗️ IMPLEMENTATION FILES

| File | Purpose |
|---|---|
| `corev2/planner/secondary.py` | SecondaryPlanner — scans MC, generates operations |
| `corev2/config/schema.py` | SecondaryMappingConfig + SecondarySyncConfig models |
| `corev2/config/production.yaml` | 6 exit tag mappings + settings |
| `corev2/executor/engine.py` | Executor handlers (add_hs_to_list, remove_mc_tag, etc.) |
| `corev2/cli.py` | Step 3 of `apply_mode()` — runs secondary sync after primary |
| `main.py` | Thin wrapper — delegates to `cli.sync_mode()` |

---

## 🔄 EXECUTION FLOW (in corev2/cli.py)

```
apply_mode():
  STEP 1: Unsubscribe Sync (Mailchimp → HubSpot opt-outs + List 443 archive)
  STEP 2: Primary Sync (HubSpot → Mailchimp: tags, subscribe, orphan cleanup)
  STEP 3: Secondary Sync (Mailchimp → HubSpot exit tag routing)  ← THIS
    └─ Phase 1: Scan Mailchimp for exit-tagged contacts
    └─ Phase 2: Look up each in HubSpot, generate operations
    └─ Phase 3: Execute operations (add to list, remove, untag, archive)
```

sync_mode() = plan_mode() + apply_mode() in sequence (convenience for local dev).
GitHub Actions calls `python -m corev2.cli plan` then `python -m corev2.cli apply`.

---

## ✅ CONFIGURATION (production.yaml)

```yaml
secondary_sync:
  enabled: true
  archive_after_sync: true
  contact_limit: 0  # UNLIMITED
  mappings:
    - exit_tag: "General Finished"
      destination_list: "946"
      destination_name: "General Handover"
      source_list: "987"
      source_name: "General"
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
      source_name: "Network Agents"
      remove_from_source: false

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
```

---

## 🧪 TEST STATUS

| Test | Status | Date |
|---|---|---|
| Schema imports & validation | ✅ Passed | 2026-03-06 |
| Config loads with 6 mappings | ✅ Passed | 2026-03-06 |
| SecondaryPlanner imports | ✅ Passed | 2026-03-06 |
| Executor handlers exist | ✅ Passed | 2026-03-06 |
| All 12 list IDs verified vs HubSpot API | ✅ Passed | 2026-03-06 |
| membershipTimestamp confirmed available | ✅ Passed | 2026-03-06 |
| Live test with tagged contacts | ⏳ Pending | — |
| End-to-end production run | ⏳ Pending | — |

---

## 📝 VERIFIED BEHAVIORS (to be filled after testing)

### 1. Exit Tag Detection
**Status:** ⏳ Pending first test  
**Expected:** Scans all MC members, filters by exit tags, skips archived/cleaned

### 2. HubSpot List Addition
**Status:** ⏳ Pending first test  
**Expected:** Adds contact to correct handover list, idempotent

### 3. Source List Removal (Manual Lists)
**Status:** ⏳ Pending first test  
**Expected:** Removes from 719/720/945/969 when configured, skips 987/989

### 4. Tag Cleanup Before Archive
**Status:** ⏳ Pending first test  
**Expected:** Removes ALL tags from contact before archiving

### 5. Mailchimp Archive
**Status:** ⏳ Pending first test  
**Expected:** Archives contact from Mailchimp after tag cleanup

### 6. Contact Not in HubSpot
**Status:** ⏳ Pending first test  
**Expected:** Skips with warning, no operations generated

---

**Git Status:** Local only — NOT pushed to remote  
**Remote HEAD:** `3f5c99b` (primary sync fixes only)  
**Push Policy:** NEVER push without explicit user permission
