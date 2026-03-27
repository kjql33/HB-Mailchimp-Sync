# 🎯 HubSpot ↔ Mailchimp Bidirectional Sync

**Production bidirectional synchronization between HubSpot and Mailchimp with intelligent compliance handling, anti-remarketing protection, and exit tag routing.**

**Last Updated:** 2026-03-06

## 🚀 Quick Start

```bash
# Run the full bidirectional sync (primary + secondary)
python main.py
```

**Automated:** Runs every 8 hours via GitHub Actions (`0 */8 * * *`)

## 🔧 Environment Setup

**Dependencies are required:**

```bash
pip install -r requirements-v2.txt
```

This installs:
- `python-dotenv==1.0.0` - Environment variable management
- `mailchimp-marketing==3.0.80` - Mailchimp API client
- `hubspot-api-client==9.0.0` - HubSpot API client
- `requests==2.31.0` - HTTP library

## 🔄 System Architecture

### Sync Flow (main.py)

| Step | Direction | Purpose |
|---|---|---|
| STEP 1 | MC → HS | Unsubscribe sync (opt-out propagation) |
| STEP 2 | HS → MC | Primary sync plan (generate operations) |
| STEP 3 | HS → MC | Primary sync execution (tags, upserts, archival) |
| STEP 4 | MC → HS | Secondary sync (exit tag routing into handover lists) |

### Key Documents

| Document | Location | Purpose |
|---|---|---|
| **Primary Sync Rules** | [corev2/PRIMARY_SYNC_RULES.md](corev2/PRIMARY_SYNC_RULES.md) | Verified rules for HubSpot → Mailchimp sync |
| **Secondary Sync Rules** | [corev2/SECONDARY_SYNC_RULES.md](corev2/SECONDARY_SYNC_RULES.md) | Rules for Mailchimp → HubSpot exit tag routing |
| **Deployment Guide** | [docs/GITHUB_DEPLOYMENT_GUIDE.md](docs/GITHUB_DEPLOYMENT_GUIDE.md) | GitHub Actions setup |
| **Secondary Sync Plan** | [docs/SECONDARY_SYNC_PLAN.md](docs/SECONDARY_SYNC_PLAN.md) | Original design document |
| **V2 Architecture** | [SYSTEM_OVERVIEW_V2_PLANNING.md](SYSTEM_OVERVIEW_V2_PLANNING.md) | V2 architecture design |

### HubSpot Lists

**Source Lists (synced to Mailchimp):**
| ID | Name | Type | Tag |
|---|---|---|---|
| 969 | Sanctioned | MANUAL | Sanctioned |
| 719 | Recruitment | MANUAL | Recruitment |
| 720 | Competition | MANUAL | Competition |
| 989 | Network Agents | DYNAMIC | EXP |
| 945 | New Agents | MANUAL | New agents |
| 987 | General | DYNAMIC | General |

**Handover Lists (secondary sync destinations):**
| ID | Name | Exit Tag | Source |
|---|---|---|---|
| 946 | General Handover | General Finished | 987 |
| 947 | Recruitment Handover | Recruitment Finished | 719 |
| 948 | Competition Handover | Competition Finished | 720 |
| 1005 | Sub Agents Handover | Sub Agents Finished | 989 |
| 949 | New Agents Handover | New Agents Finished | 945 |
| 1006 | Sanctioned Handover | Sanctioned Finished | 969 |

**Exclusion Lists (never sync):**
| ID | Name | Type |
|---|---|---|
| 762 | Unsubscribed / Opted Out | DYNAMIC |
| 773 | Manual Disengagement | DYNAMIC |
| 717 | Active Deals | DYNAMIC |

---

## 📚 Historical Context (Pre-Reset v1)

**⚠️ The sections below describe the pre-reset system and are kept for design pattern reference only.**

### For PM / Operator (Historical)

**Canonical documentation reading order (pre-reset):**

1. **[DRY_RUN_VERIFICATION_COMPLETE.md](DRY_RUN_VERIFICATION_COMPLETE.md)** - Historical data baseline (Nov 26, 2025)
2. **[contact_universe_report.md](system_testing/audit_results/contact_universe_report.md)** - Pre-reset HS↔MC reconciliation
3. **[PHASE5A_5B_5C_EXECUTION_POLICY.md](PHASE5A_5B_5C_EXECUTION_POLICY.md)** - Historical phase boundaries
4. **[PHASE7_LIVE_HTTP_IMPLEMENTATION.md](PHASE7_LIVE_HTTP_IMPLEMENTATION.md)** - v1 HTTP layer implementation

**Pre-Reset Safety Status (Historical):**

- ⚠️ **System was locked in DRY_RUN mode**; all `--live` execution blocked
- ⚠️ **Phase 5C / A1 (member creation) was explicitly deferred**
- ✅ **Mailchimp Reset Event (Dec 2025)** superseded all planned Phase 7B execution

## 📁 Project Structure

- **`core/`** - Core sync functionality and configuration
- **`info/README.md`** - Complete setup and usage documentation  
- **`.github/workflows/`** - GitHub Actions automation

## 🎯 Current Status

**System State: POST-RESET / V2 Architecture Planning**

### ⚠️ Mailchimp Reset Event (December 2025)

**Major System Change:**
- 📅 **December 2025**: Manual Mailchimp reset executed
- 🧹 **All MC tags removed** from entire audience (~2,400 contacts)
- 📦 **All MC contacts archived** (clean slate)
- 🎯 **HubSpot designated as sole source of truth**

**Mission Change:**
- ❌ **Old mission**: Repair MC using historical anomaly fix plans (Phase 7B)
- ✅ **New mission**: Build robust v2 sync/reconciliation engine for fresh-start paradigm

### Historical Baselines (Pre-Reset) — Reference Only

**These baselines are NO LONGER VALID for live execution:**
- ✅ **Phase 7B Baseline (Nov 28, 2025)**: 20251128_174732 - 3,886 unique emails (3,542 HS, 2,417 MC)
  - 4 categories operational (A3, A4, A2, A5) = 1,924 contacts
  - **Status**: Historical reference for regression tests only
- ✅ **Historical DRY_RUN (Nov 26)**: 1,382 planned actions, 0 errors
  - **Status**: Test fixture for plumbing validation
- ✅ **Phase 7 Plumbing Verification (Nov 27)**: HTTP clients, safety model validated
  - **Status**: Design patterns remain valuable
- ⚠️ **Phase 7B Unlock Plan (Dec 3)**: Safety lock removal strategy documented but NOT implemented
  - **Status**: Obsolete (will not be executed against pre-reset baseline)

### Current Work (Post-Reset)

- 🔄 **Fresh diagnostic audit pending**: Analyze HS vs empty/archived MC state
- 📋 **V2 architecture design**: Core sync engine rebuild for fresh-start world
- 🔒 **All safety locks remain active**: No changes to code safety model
- ⏸️ **Phase 5C (A1) still deferred**: Member creation awaits v2 sync design + campaign strategy

**Phase 5A Scope (Phase 7B Baseline - 28 Nov):**
- **OPERATIONAL (4 categories):** 254 contacts (A3: 7, A4: 42, A2: 205)
- **BLOCKED:** A7_orphans (424 contacts) - taxonomy mismatch; A7_exit (0 contacts) - no anomalies detected
- **Mailchimp operations:** 255 (tag stripping, archival, list management)
- **HubSpot operations:** 114 (list additions/removals for A4)
- Zero new members created, zero new tags added

**Phase 5B Scope (Phase 7B Baseline - 28 Nov):**
- 1,670 contacts: ORI_LISTS metadata updates only (A5)

**Immediate Operational Readiness:**
- **Ready for Live Execution:** 1,924 contacts (A3, A4, A2, A5) - DRY_RUN verified with 0 errors
- **Pending PM Decision:** Proceed with 4 categories OR await A7 taxonomy fix (additional 424 contacts)

**For Contact Universe Details:** See `contact_universe_report.md` (15 active lists tracked)

---

## 🛤️ Path to Phase 7B (Live Execution)

**Current State:** Phase 7B baseline generated and verified. 4 of 6 categories operational (A3, A4, A2, A5). System locked in DRY_RUN mode pending PM authorization.

### Canonical Documentation Set (Updated 2025-12-03)

For PM/governance, the authoritative documentation is:

1. **`README.md`** (this file) - Environment, status, documentation index
2. **`PHASE7B_UNLOCK_PLAN.md`** - **NEW** - Proposed safety lock removal strategy for live execution
3. **`PHASE7_DRY_RUN_PLUMBING_VERIFIED.md`** - Phase 7B baseline verification (28 Nov) + DRY_RUN results
4. **`PHASE5A_5B_5C_EXECUTION_POLICY.md`** - Phase boundaries + Phase 7B/7C scope + authorization gates
5. **`PHASE7_LIVE_HTTP_IMPLEMENTATION.md`** - HTTP behavior, safety model, retry/idempotency
6. **`DRY_RUN_VERIFICATION_COMPLETE.md`** - Historical 26 Nov baseline (archival reference)
7. **`contact_universe_report.md`** / **`reconciliation_log_master.md`** - Data baseline sanity checks

### Current Baseline Classification

**Phase 7B Baseline (28 Nov 2025, 17:47:32) - AUTHORIZED:**
- `fix_plan_master_20251128_174732.csv` + snapshots (HS, MC)
- Generated via `enrich_fix_plan.py` from fresh audit
- **Operational for 4 categories:** A3 (7), A4 (42), A2 (205), A5 (1,670) = 1,924 contacts
- **Excluded from Phase 7B:** A7 (424 contacts - taxonomy reconciliation), A1 (1,621 contacts - Phase 5C deferred)

**Historical Baselines (Test Harnesses Only - NOT for live execution):**
- `fix_plan_master_20251119_172514.csv` (Nov 19) - DRY_RUN plumbing verification
- `fix_plan_master_20251126_*` (Nov 26) - Historical DRY_RUN verification

### Phase 7B Readiness Status

**Gates Satisfied:**
- ✅ **Gate 1:** Fresh aligned baseline (20251128_174732)
- ✅ **Gate 2:** DRY_RUN re-verification complete (4 of 6 categories, 0 errors)
- ✅ **Gate 3:** Governance documentation updated
- ⏸️ **Gate 4:** PENDING PM AUTHORIZATION

**Next Steps (Requires PM Authorization):**

1. **PM Reviews Unlock Plan:**
   - See `PHASE7B_UNLOCK_PLAN.md` for proposed code changes
   - Category whitelist approach (orchestrator + individual scripts)
   - Test batch → manual verification → full run workflow

2. **Category-by-Category Execution (Recommended Order):**
   - Start with **A3** (7 contacts) - smallest scope, critical compliance
   - Then **A4** (42 contacts) - unsubscribe sync
   - Then **A2** (205 contacts) - MC-only archival
   - Finally **A5** (1,670 contacts) - ORI_LISTS metadata (Phase 5B)

3. **Each Category Follows Pattern:**
   - Code unlock (orchestrator + script safety lock removal)
   - Test batch with `--limit` flag
   - Manual UI verification in HubSpot/Mailchimp
   - Go/no-go decision
   - Full category run if test batch clean
   - Post-execution audit (system_audit.py)
- Zero errors across all categories

**Gate 3 - Governance Documentation:**
- Update `PHASE7_DRY_RUN_PLUMBING_VERIFIED.md` with new baseline timestamp
- Update `PHASE5A_5B_5C_EXECUTION_POLICY.md` with Phase 7B authorization decision
- Document Phase 7B scope: 5A (removal/archival) + optionally 5B (metadata)
- Confirm Phase 5C (A1 member creation) remains DEFERRED

**Gate 4 - Safety Model Review:**
- Review rollout sequence in `PHASE7_LIVE_HTTP_IMPLEMENTATION.md` Section 11
- Prepare rollback procedures for each category
- Define manual verification checkpoints (HS/MC UI spot-checks)
- Agree on first test category (recommend: A3, only 3 records)

**Gate 5 - Campaign Strategy (Phase 5C ONLY):**
- Not required for Phase 5A/5B
- If A1 ever considered: requires separate campaign strategy document + PM authorization

**Execution Order (when gates satisfied):**
1. A3 (3) → A4 (55) → A2 (192) → A7_orphans (119) → A7_exit (306) → A5 (509)
2. Each category: test batch with `--limit` → UI verification → full run if clean
3. Manual spot-checks between categories, post-execution audits

**Phase 5C Status:**
- A1 (member creation) **explicitly DEFERRED**
- Requires campaign strategy doc + separate authorization
- Not bundled with Phase 7B

---

## 📋 Key Features

- **Scalable**: Handles thousands of contacts with proper pagination
- **Safe**: Triple-layered safety model (DRY_RUN + ALLOW_WRITE + orchestration locks)
- **Idempotent**: All operations can be safely retried
- **Auditable**: Comprehensive logging with PII redaction
- **Testable**: --limit flag for controlled test batches
