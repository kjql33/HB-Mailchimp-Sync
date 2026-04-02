# PIPELINE VERIFICATION REPORT
> **Generated**: 2 April 2026 — verified against live HubSpot & Mailchimp APIs  
> **Audience**: Solace Group Ltd (d0e267ecff) — 519 subscribed members

---

## End-to-End Pipeline Trace

Each row traces a contact's full lifecycle from HubSpot source list → Mailchimp entry tag → journey → exit tag → secondary sync → HubSpot handover.

### 1. Sanctioned

| Step | System | Detail | Verified |
|------|--------|--------|----------|
| **Source List** | HubSpot | List **969** — "Sanctioned" (MANUAL, 24 members) | ✅ |
| **Exclusions** | HubSpot | Blocked by: 762 (Unsubscribed), 773 (Manual Exclusion), 717 (Active Deals) | ✅ |
| **Entry Tag Applied** | Mailchimp | `Sanctioned` (tag id=25404, 3 members) | ✅ |
| **Journey Triggered** | Mailchimp | Journey **2514** — "Sanctioned" (status: sending) | ✅ |
| **Journey Trigger Step** | Mailchimp | Step 15059: `trigger-tag_added` → "Sanctioned" (tag_id=25404) | ✅ |
| **Journey Last Step** | Mailchimp | Step 15061: `action-add_remove_tag` → **add** "Sanctioned Finished" (tag_id=25405) | ✅ |
| **Journey Stats** | Mailchimp | 97 started, 3 in-progress, 91 completed | ✅ |
| **Exit Tag** | Mailchimp | `Sanctioned Finished` (tag id=25405, 0 members) | ✅ |
| **Secondary Sync** | Config | exit_tag → destination **1006** "Sanctioned Handover", remove_from_source=**true** | ✅ |
| **Handover List** | HubSpot | List **1006** — "Sanctioned Handover" (MANUAL, 73 members) | ✅ |

---

### 2. Recruitment

| Step | System | Detail | Verified |
|------|--------|--------|----------|
| **Source List** | HubSpot | List **719** — "Recruitment" (MANUAL, 17 members) | ✅ |
| **Exclusions** | HubSpot | Blocked by: 762, 773, 717 | ✅ |
| **Entry Tag Applied** | Mailchimp | `Recruitment` (tag id=21000, 15 members) | ✅ |
| **Journey Triggered** | Mailchimp | Journey **2490** — "Recruitment" (status: sending) | ✅ |
| **Journey Trigger Step** | Mailchimp | Step 14922: `trigger-tag_added` → "Recruitment" (tag_id=21000) | ✅ |
| **Journey Last Step** | Mailchimp | Step 14930: `action-add_remove_tag` → **add** "Recruitment Finished" (tag_id=25161) | ✅ |
| **Journey Stats** | Mailchimp | 89 started, 13 in-progress, 66 completed | ✅ |
| **Exit Tag** | Mailchimp | `Recruitment Finished` (tag id=25161, 0 members) | ✅ |
| **Secondary Sync** | Config | exit_tag → destination **947** "Recruitment Handover", remove_from_source=**true** | ✅ |
| **Handover List** | HubSpot | List **947** — "Recruitment Handover" (MANUAL, 64 members) | ✅ |

---

### 3. Competition

| Step | System | Detail | Verified |
|------|--------|--------|----------|
| **Source List** | HubSpot | List **720** — "Competition" (MANUAL, 6 members) | ✅ |
| **Exclusions** | HubSpot | Blocked by: 762, 773, 717 | ✅ |
| **Entry Tag Applied** | Mailchimp | `Competition` (tag id=21593, 5 members) | ✅ |
| **Journey Triggered** | Mailchimp | Journey **2491** — "Competition" (status: sending) | ✅ |
| **Journey Trigger Step** | Mailchimp | Step 14934: `trigger-tag_added` → "Competition" (tag_id=21593) | ✅ |
| **Journey Last Step** | Mailchimp | Step 14943: `action-add_remove_tag` → **add** "Competition Finished" (tag_id=25164) | ✅ |
| **Journey Stats** | Mailchimp | 46 started, 5 in-progress, 37 completed | ✅ |
| **Exit Tag** | Mailchimp | `Competition Finished` (tag id=25164, 0 members) | ✅ |
| **Secondary Sync** | Config | exit_tag → destination **948** "Competition Handover", remove_from_source=**true** | ✅ |
| **Handover List** | HubSpot | List **948** — "Competition Handover" (MANUAL, 37 members) | ✅ |

---

### 4. Sub Agents

| Step | System | Detail | Verified |
|------|--------|--------|----------|
| **Source List** | HubSpot | List **989** — "Sub Agents" (DYNAMIC, 17 members) | ✅ |
| **Feeder Lists** | HubSpot | 900 "EXP" (MANUAL, 100), 972 "Keller Williams Agents" (MANUAL, 213), 971 "IAD Agents" (MANUAL, 60) | ✅ |
| **Exclusions** | HubSpot | Blocked by: 762, 773, 717 | ✅ |
| **Entry Tag Applied** | Mailchimp | `Sub Agents` (tag id=25883, 0 members) | ✅ |
| **Journey Triggered** | Mailchimp | Journey **2506** — "Sub Agents" (status: sending) | ✅ |
| **Journey Trigger Step** | Mailchimp | Step 15014: `trigger-tag_added` → "Sub Agents" (tag_id=25883) | ✅ |
| **Journey Last Step** | Mailchimp | Step 15023: `action-add_remove_tag` → **add** "Sub Agents Finished" (tag_id=25718) | ✅ |
| **Journey Stats** | Mailchimp | 377 started, 0 in-progress, 341 completed | ✅ |
| **Exit Tag** | Mailchimp | `Sub Agents Finished` (tag id=25718, 0 members) | ✅ |
| **Secondary Sync** | Config | exit_tag → destination **1005** "Sub Agents Handover", remove_from_source=**false** (dynamic), additional_remove from 900, 972, 971 | ✅ |
| **Handover List** | HubSpot | List **1005** — "Sub Agents Handover" (MANUAL, 326 members) | ✅ |

---

### 5. New Agents

| Step | System | Detail | Verified |
|------|--------|--------|----------|
| **Source List** | HubSpot | List **945** — "New Agents" (MANUAL, 3 members) | ✅ |
| **Exclusions** | HubSpot | Blocked by: 762, 773, 717 | ✅ |
| **Entry Tag Applied** | Mailchimp | `New Agents` (tag id=25402, 3 members) | ✅ |
| **Journey Triggered** | Mailchimp | Journey **2513** — "New Agents" (status: sending) | ✅ |
| **Journey Trigger Step** | Mailchimp | Step 15055: `trigger-tag_added` → "New Agents" (tag_id=25402) | ✅ |
| **Journey Last Step** | Mailchimp | Step 15058: `action-add_remove_tag` → **add** "New Agents Finished" (tag_id=25403) | ✅ |
| **Journey Stats** | Mailchimp | 29 started, 1 in-progress, 27 completed | ✅ |
| **Exit Tag** | Mailchimp | `New Agents Finished` (tag id=25403, 0 members) | ✅ |
| **Secondary Sync** | Config | exit_tag → destination **949** "New Agents Handover", remove_from_source=**true** | ✅ |
| **Handover List** | HubSpot | List **949** — "New Agents Handover" (MANUAL, 24 members) | ✅ |

---

### 6. General (Branch Split)

#### 6a. General Single (branches ≤ 1 or missing)

| Step | System | Detail | Verified |
|------|--------|--------|----------|
| **Source List** | HubSpot | List **987** — "General Mailchimp Import" (DYNAMIC, 548 members) | ✅ |
| **Exclusions** | HubSpot | Blocked by: 762, 773, 717 | ✅ |
| **Branch Condition** | Config | `branches` property ≤ 1 or missing → `General Single` | ✅ |
| **Entry Tag Applied** | Mailchimp | `General Single` (tag id=25904, 0 members) | ✅ |
| **Journey Triggered** | Mailchimp | Journey **2476** — "General Single" (status: sending) | ✅ |
| **Journey Trigger Step** | Mailchimp | Step 15401: `trigger-tag_added` → "General Single" (tag_id=25904) | ✅ |
| **Journey Last Step** | Mailchimp | Step 14900: `action-add_remove_tag` → **add** "General Single Finished" (tag_id=25905) | ✅ |
| **Journey Stats** | Mailchimp | 4307 started, 400 in-progress, 3361 completed | ✅ |
| **Exit Tag** | Mailchimp | `General Single Finished` (tag id=25905, 0 members) | ✅ |
| **Secondary Sync** | Config | exit_tag → destination **946** "General Handover", remove_from_source=**false** | ✅ |
| **Handover List** | HubSpot | List **946** — "General Handover" (MANUAL, 3155 members) | ✅ |

#### 6b. General Multi (branches > 1)

| Step | System | Detail | Verified |
|------|--------|--------|----------|
| **Source List** | HubSpot | List **987** — "General Mailchimp Import" (DYNAMIC, 548 members) | ✅ |
| **Branch Condition** | Config | `branches` property > 1 → tag override to `General Multi` | ✅ |
| **Entry Tag Applied** | Mailchimp | `General Multi` (tag id=25892, 0 members) | ✅ |
| **Journey Triggered** | Mailchimp | Journey **2561** — "General Multi" (status: sending) | ✅ |
| **Journey Trigger Step** | Mailchimp | Step 15363: `trigger-tag_added` → "General Multi" (tag_id=25892) | ✅ |
| **Journey Last Step** | Mailchimp | Step 15371: `action-add_remove_tag` → **add** "General Multi Finished" (tag_id=25894) | ✅ |
| **Journey Stats** | Mailchimp | 0 started, 0 in-progress, 0 completed | ✅ |
| **Exit Tag** | Mailchimp | `General Multi Finished` (tag id=25894, 0 members) | ✅ |
| **Secondary Sync** | Config | exit_tag → destination **946** "General Handover", remove_from_source=**false** | ✅ |
| **Handover List** | HubSpot | List **946** — "General Handover" (MANUAL, 3155 members) | ✅ |

> **Note**: Both General Single and General Multi route to the same handover list 946.

---

### 7. Manual Inclusion (GROUP 3 — Active Deals Override)

| Step | System | Detail | Verified |
|------|--------|--------|----------|
| **Source List** | HubSpot | List **784** — "Manual Inclusion MC" (MANUAL, 0 members) | ✅ |
| **Exclusions** | HubSpot | Blocked by: 762, 773 **only** — 717 Active Deals is **bypassed** | ✅ |
| **Branch Condition** | Config | Same as General: ≤1 → `General Single`, >1 → `General Multi` | ✅ |
| **Additional Tag** | Mailchimp | `Manual Inclusion` (tag id=25903, 0 members) — applied alongside entry tag | ✅ |
| **Journey Triggered** | Mailchimp | Same journeys as General (2476 / 2561) based on branch split | ✅ |
| **Secondary Sync** | Config | **EXEMPT** — contacts with `Manual Inclusion` tag are skipped entirely | ✅ |
| **Archival** | Config | **EXEMPT** — `Manual Inclusion` in archival exempt_tags | ✅ |
| **Net Effect** | — | Contact stays in MC with tag permanently; never handed over or archived | ✅ |

---

### 8. Long Term Marketing (GROUP 4)

#### 8a. Long Term Single (branches ≤ 1 or missing)

| Step | System | Detail | Verified |
|------|--------|--------|----------|
| **Source List** | HubSpot | List **1032** — "Long Term Marketing" (MANUAL, 0 members) | ✅ |
| **Exclusions** | HubSpot | Blocked by: 762, 773, 717 | ✅ |
| **Branch Condition** | Config | `branches` property ≤ 1 or missing → `General Single Long Term` | ✅ |
| **Entry Tag Applied** | Mailchimp | `General Single Long Term` (tag id=25908, 0 members) | ✅ |
| **Journey Triggered** | Mailchimp | Journey **2558** — "Long Term Marketing Single" (status: sending) | ✅ |
| **Journey Trigger Step** | Mailchimp | Step 15338: `trigger-tag_added` → "General Single Long Term" (tag_id=25908) | ✅ |
| **Journey Last Step** | Mailchimp | Step 15345: `action-add_remove_tag` → **add** "Long Term Single Finished" (tag_id=25909) | ✅ |
| **Journey Stats** | Mailchimp | 0 started, 0 in-progress, 0 completed | ✅ |
| **Exit Tag** | Mailchimp | `Long Term Single Finished` (tag id=25909, 0 members) | ✅ |
| **Secondary Sync** | Config | **No HubSpot destination** — MC cleanup only (untag + archive), remove_from_source=**false** | ✅ |
| **Net Effect** | — | Contact stays in HS list 1032, gets untagged + archived in MC | ✅ |

#### 8b. Long Term Multi (branches > 1)

| Step | System | Detail | Verified |
|------|--------|--------|----------|
| **Source List** | HubSpot | List **1032** — "Long Term Marketing" (MANUAL, 0 members) | ✅ |
| **Branch Condition** | Config | `branches` property > 1 → tag override to `General Multi Long Term` | ✅ |
| **Entry Tag Applied** | Mailchimp | `General Multi Long Term` (tag id=25907, 0 members) | ✅ |
| **Journey Triggered** | Mailchimp | Journey **2564** — "Long Term Marketing Multi" (status: sending) | ✅ |
| **Journey Trigger Step** | Mailchimp | Step 15402: `trigger-tag_added` → "General Multi Long Term" (tag_id=25907) | ✅ |
| **Journey Last Step** | Mailchimp | Step 15409: `action-add_remove_tag` → **add** "Long Term Multi Finished" (tag_id=25906) | ✅ |
| **Journey Stats** | Mailchimp | 0 started, 0 in-progress, 0 completed | ✅ |
| **Exit Tag** | Mailchimp | `Long Term Multi Finished` (tag id=25906, 0 members) | ✅ |
| **Secondary Sync** | Config | **No HubSpot destination** — MC cleanup only (untag + archive), remove_from_source=**false** | ✅ |
| **Net Effect** | — | Contact stays in HS list 1032, gets untagged + archived in MC | ✅ |

---

## Exclusion Matrix Summary

| Group | Lists | Blocked By | Notes |
|-------|-------|------------|-------|
| **1: general_marketing** | 969, 719, 720, 989, 945, 987 | 762 (Unsubscribed/ Opted Out), 773 (Manual Exclusion from MC), 717 (All Active Deals) | Standard exclusions |
| **2: special_campaigns** | *(empty)* | 762, 773, 717 | Reserved |
| **3: manual_override** | 784 | 762, 773 | **717 bypassed** — allows marketing to contacts in active deals |
| **4: long_term_marketing** | 1032 | 762, 773, 717 | Same as GROUP 1 |

---

## Tag Inventory (19 total)

| Tag | MC ID | Type | Members | Used By |
|-----|-------|------|---------|---------|
| Sanctioned | 25404 | Entry | 3 | List 969 |
| Recruitment | 21000 | Entry | 15 | List 719 |
| Competition | 21593 | Entry | 5 | List 720 |
| Sub Agents | 25883 | Entry | 0 | List 989 |
| New Agents | 25402 | Entry | 3 | List 945 |
| General Single | 25904 | Entry | 0 | Lists 987, 784 (branches ≤ 1) |
| General Multi | 25892 | Entry | 0 | Lists 987, 784 (branches > 1) |
| Manual Inclusion | 25903 | Additional | 0 | List 784 |
| General Single Long Term | 25908 | Entry | 0 | List 1032 (branches ≤ 1) |
| General Multi Long Term | 25907 | Entry | 0 | List 1032 (branches > 1) |
| Sanctioned Finished | 25405 | Exit | 0 | Journey 2514 → List 1006 |
| Recruitment Finished | 25161 | Exit | 0 | Journey 2490 → List 947 |
| Competition Finished | 25164 | Exit | 0 | Journey 2491 → List 948 |
| Sub Agents Finished | 25718 | Exit | 0 | Journey 2506 → List 1005 |
| New Agents Finished | 25403 | Exit | 0 | Journey 2513 → List 949 |
| General Single Finished | 25905 | Exit | 0 | Journey 2476 → List 946 |
| General Multi Finished | 25894 | Exit | 0 | Journey 2561 → List 946 |
| Long Term Single Finished | 25909 | Exit | 0 | Journey 2558 → MC cleanup |
| Long Term Multi Finished | 25906 | Exit | 0 | Journey 2564 → MC cleanup |

---

## Auto-Refresh Feature

Starting from this session, every `plan` run automatically:
1. Fetches the current name of every referenced HubSpot list via `/crm/v3/lists/{id}`
2. Compares against the `name`, `source_name`, `destination_name`, and `list_name` fields in `production.yaml`
3. Updates any mismatches in-place (preserving comments and formatting)
4. Logs all changes before proceeding with plan generation

This ensures config names always match HubSpot — zero manual maintenance required.

---

## Final Status: ✅ ALL CLEAN

- **20 HubSpot lists** — all exist and resolve correctly
- **19 Mailchimp tags** — all exist with correct IDs
- **9 Customer Journeys** — all active (`sending`), triggers match entry tags, last steps add correct exit tags
- **9 Secondary sync mappings** — fully configured
- **4 Exclusion matrix groups** — all correct
- **Config names** — now match exact HubSpot list names
- **`New Agents` tag** — fixed to uppercase A, matching Mailchimp exactly
- **No orphaned tags, lists, or journeys detected**
