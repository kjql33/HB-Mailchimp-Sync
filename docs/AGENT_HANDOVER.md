# Agent Handover Brief — HubSpot ↔ Mautic Sync Pipeline
**Branch:** `v2/mautic`
**Repo:** `https://github.com/kjql33/HB-Mailchimp-Sync.git`
**Prepared:** 2026-05-11
**Prepared by:** GitHub Copilot (Ejas Deane, Solace Group)
**Status:** Code complete, verified, pushed. Ready for first live run.

---

## 1. What This System Does

This is a **production Python pipeline** that bidirectionally synchronises Solace Group's contacts between **HubSpot CRM** and **Mautic** (self-hosted email marketing automation).

It runs automatically every 8 hours via GitHub Actions. Every run:

1. Pushes eligible HubSpot contacts into Mautic with the correct campaign tag
2. Propagates unsubscribes and email bounces back to HubSpot
3. Detects contacts whose Mautic campaigns have finished (via "exit tags") and routes them into the correct HubSpot handover lists

It is a **port of an existing production Mailchimp system** (`main` branch). Both systems run on the same HubSpot lists and same business rules. The Mailchimp system is still live and running. The Mautic system is new and **not yet in production** — it needs a first run/test before going live on schedule.

---

## 2. Infrastructure

| Item | Detail |
|------|--------|
| **GitHub Repo** | `https://github.com/kjql33/HB-Mailchimp-Sync.git` |
| **This branch** | `v2/mautic` |
| **Default branch** | `main` (Mailchimp system — still live) |
| **Mautic server** | `https://accessibility-api.alphagnito.com` (self-hosted Docker, Ubuntu) |
| **Mautic credentials** | GitHub secrets `MAUTIC_USERNAME` / `MAUTIC_PASSWORD` |
| **HubSpot** | v3 API only, Bearer token auth (`HUBSPOT_PRIVATE_APP_TOKEN`) |
| **Python version** | 3.13 |
| **Schedule** | `0 0,8,16 * * *` — 00:00, 08:00, 16:00 UTC |
| **Runtime** | GitHub Actions ubuntu-latest |

### GitHub Actions — Important Note on Parallel Running
GitHub's `schedule:` trigger only fires on the **default branch** (`main`).
The `v2/mautic` workflow runs automatically via schedule only if `v2/mautic` is the default branch.

**Current setup:** Both branches coexist. The Mailchimp workflow runs on schedule from `main`. The Mautic workflow must be triggered **manually** via `workflow_dispatch` (Actions tab → Run workflow) until you change the default branch to `v2/mautic`.

When you're ready to switch the Mautic system to automatic:
1. Go to GitHub repo → Settings → Branches → change default branch to `v2/mautic`
2. The old Mailchimp workflow will stop auto-running (unless you want both, which requires a separate repo)

---

## 3. Three-Stage Pipeline Architecture

```
HubSpot CRM
     │
     │ Stage 1 (Primary Sync — every 8h)
     │ Reads 8 lists, applies exclusion matrix, resolves tag per contact
     ▼
  Mautic
  (self-hosted Docker)
     │
     │ Stage 2 (Inside Mautic — automated by campaigns)
     │ Mautic campaigns run email journeys. When complete, campaign
     │ automatically applies an "exit tag" to the contact.
     ▼
     │ Stage 3 (Secondary Sync — same 8h run)
     │ Detects exit-tagged contacts, routes to HubSpot handover lists,
     │ archives them from Mautic
     ▼
HubSpot CRM (handover lists)
```

---

## 4. Execution Flow (every run)

Inside a single `apply_mode()` call in `corev2/cli.py`:

```
STEP 0:  Health check — GET /api/contacts, auto-fix Docker permissions if 500
STEP 1:  Unsubscribe sync — scan all Mautic contacts for status=unsubscribed
         → opt them out in HubSpot Communication Preferences API
STEP 1B: Cleaned contact sync — scan for status=cleaned (hard bounced)
         → strip all Mautic tags + set hs_email_bad_address="true" in HubSpot
STEP 2:  Primary sync — execute the pre-generated operations plan
         (upsert contacts, apply/remove tags, orphan archival)
STEP 3:  Secondary sync — scan Mautic for exit tags, generate + execute
         handover operations (add to HS list, remove from source, untag, archive)
```

**GitHub Actions splits plan and apply:**
```
python -m corev2.cli plan --config ... --output ... --skip-health-check
python -m corev2.cli apply --plan ... --skip-health-check
```
`--skip-health-check`: Docker permission check can only run directly on the Ubuntu server, not from the GH Actions runner.

---

## 5. HubSpot Lists Reference

### Source lists (synced TO Mautic)

| List ID | Name | Type | Mautic Tag | Group |
|---------|------|------|------------|-------|
| 969 | Sanctioned | MANUAL | `Sanctioned` | GROUP 1 |
| 719 | Recruitment | MANUAL | `Recruitment` (or `General Multi` if branches > 1) | GROUP 1 |
| 720 | Competition | MANUAL | `Competition` | GROUP 1 |
| 1050 | Sub Agents | STATIC | `Sub Agents` | GROUP 1 |
| 945 | New Agents | MANUAL | `New Agents` | GROUP 1 |
| 987 | General Mailchimp Import | DYNAMIC | `General Single` / `General Multi` | GROUP 1 |
| 784 | Manual Inclusion MC | MANUAL | `General Single` / `General Multi` + `Manual Inclusion` | GROUP 3 |
| 1032 | Long Term Marketing | MANUAL | `General Single Long Term` / `General Multi Long Term` | GROUP 4 |

### Exclusion lists (block sync — NEVER add these to sync lists)

| List ID | Name | Type | Blocks |
|---------|------|------|--------|
| 762 | Unsubscribed / Opted Out | DYNAMIC | Groups 1, 3, 4 |
| 773 | Manual Disengagement | DYNAMIC | Groups 1, 3, 4 |
| 717 | Active Deals | DYNAMIC | Groups 1, 4 only (GROUP 3 bypasses this) |

### Handover lists (secondary sync destinations)

| List ID | Name | Filled by exit tag |
|---------|------|-------------------|
| 946 | General Handover | General Single/Multi Finished |
| 947 | Recruitment Handover | Recruitment Finished |
| 948 | Competition Handover | Competition Finished |
| 1005 | Sub Agents Handover | Sub Agents Finished |
| 949 | New Agents Handover | New Agents Finished |
| 1006 | Sanctioned Handover | Sanctioned Finished |

### Sub Agents feeder lists (also cleaned on Sub Agents Finished)

| List ID | Name |
|---------|------|
| 900 | EXP |
| 972 | Keller Williams Agents |
| 971 | IAD Agents |

---

## 6. Mautic Tags Reference

### Entry tags (applied by this system)

| Tag | Applied to contacts from |
|-----|--------------------------|
| `Sanctioned` | List 969 |
| `Recruitment` | List 719 (branches ≤ 1) |
| `Competition` | List 720 |
| `Sub Agents` | List 1050 |
| `New Agents` | List 945 |
| `General Single` | Lists 987, 784 (branches ≤ 1) |
| `General Multi` | Lists 987, 784, 719 (branches > 1) |
| `Manual Inclusion` | List 784 (additional tag, always applied) |
| `General Single Long Term` | List 1032 (branches ≤ 1) |
| `General Multi Long Term` | List 1032 (branches > 1) |

### Exit tags (applied by Mautic campaigns, read by secondary sync)

| Exit Tag | Routes to |
|----------|-----------|
| `General Single Finished` | HubSpot 946 |
| `General Multi Finished` | HubSpot 946 |
| `Recruitment Finished` | HubSpot 947 |
| `Competition Finished` | HubSpot 948 |
| `Sub Agents Finished` | HubSpot 1005 |
| `New Agents Finished` | HubSpot 949 |
| `Sanctioned Finished` | HubSpot 1006 |
| `Long Term Single Finished` | Mautic cleanup only (no HS destination) |
| `Long Term Multi Finished` | Mautic cleanup only (no HS destination) |

### Archival exempt tags (contacts with these are NEVER archived by the system)
`VIP`, `DoNotArchive`, `Manual_Override`, `Manual Inclusion`

---

## 7. Mautic API Behaviour (Critical Notes)

The Mautic API is non-standard in several ways:

| Operation | API call | Notes |
|-----------|----------|-------|
| Add tag | `PATCH /api/contacts/{id}/edit` with `{"tags": ["tagname"]}` | Never use PUT |
| Remove tag | `PATCH /api/contacts/{id}/edit` with `{"tags": ["-tagname"]}` | Prefix `-` to remove |
| Unsubscribe | `POST /api/contacts/{id}/dnc/email/add` with `{"reason": 1}` | reason=1 = unsubscribed |
| Archive | `DELETE /api/contacts/{id}/delete` | 404 = already archived = success |
| Get contact | `GET /api/contacts` with `search=email@example.com` | No direct email lookup endpoint |
| List all | `GET /api/contacts?limit=200&start=N` | Pagination only, no status filter |

Status is **derived** from the contact object, not a direct field:
- `isPublished: false` → `archived`
- `doNotContact[].reason == 2` → `cleaned`
- `doNotContact[].reason == 1` → `unsubscribed`
- otherwise → `subscribed`

**Contact ID caching:** `mautic_client.py` maintains a session-scoped `_id_cache` dict (email → mautic_id) to avoid redundant lookup calls within a single run.

---

## 8. File Structure

```
mautic_system/
├── main.py                         Thin wrapper: loads .env, calls cli.sync_mode()
├── requirements.txt                pydantic>=2, pyyaml, python-dotenv, aiohttp
├── .env.example                    Template — copy to .env for local dev
├── README.md                       Operator guide
├── deploy_me.md                    Quick Ubuntu server deploy commands
├── server_setup.sh                 Ubuntu server setup script
│
├── .github/workflows/sync.yml      GitHub Actions: schedule + manual trigger
│
└── corev2/                         Main package
    ├── cli.py                      Entrypoint: validate-config, plan, apply, sync
    ├── notifications.py            Microsoft Teams webhook (audience cap alerts)
    ├── health.py                   Mautic API health check + Docker auto-fix
    │
    ├── PRIMARY_SYNC_RULES.md       ← Business rules: HubSpot → Mautic
    ├── SECONDARY_SYNC_RULES.md     ← Business rules: Mautic → HubSpot
    │
    ├── config/
    │   ├── production.yaml         All business config (list IDs, tags, safety gates)
    │   ├── schema.py               Pydantic v2 models + INV-002 validator
    │   └── loader.py               load_config() + compute_config_hash()
    │
    ├── clients/
    │   ├── mautic_client.py        Mautic REST API client (aiohttp, HTTP Basic Auth)
    │   ├── hubspot_client.py       HubSpot v3 API client (batch-optimised)
    │   └── http_base.py            Shared retry/rate-limit base class
    │
    ├── planner/
    │   ├── primary.py              SyncPlanner: generates operations_plan.json
    │   ├── secondary.py            SecondaryPlanner: scans exit tags
    │   └── reconciliation.py       ArchivalReconciliation: orphan detection (INV-006)
    │
    ├── executor/
    │   └── engine.py               SyncExecutor + AudienceCapGuard: executes plan
    │
    ├── sync/
    │   └── unsubscribe_sync.py     Step 1 + 1B: unsub/bounce propagation to HubSpot
    │
    └── artifacts/                  Generated at runtime (plans, journals) — gitignored
```

---

## 9. Configuration Deep-Dive

All config lives in `corev2/config/production.yaml`. Sensitive values use `${ENV_VAR}` placeholders resolved at runtime. **Never hardcode credentials in this file.**

### Key safety settings

```yaml
safety:
  run_mode: prod          # Must be "prod" for live apply
  allow_apply: true       # Must be true for any mutations
  allow_archive: true     # Must be true for archival ops
  allow_unlimited: true   # Must be true when test_contact_limit=0
  test_contact_limit: 0   # 0 = process ALL contacts
  enable_hubspot_writes: false  # Set true to write ORI_LISTS back to HubSpot
```

### Config hash verification

When `apply_mode()` runs, it verifies the plan was generated from the **current** config by comparing SHA-256 hashes. If you edit `production.yaml` between plan and apply, the apply will refuse to run — you must regenerate the plan.

### Adding a new HubSpot list

1. Add to the correct group in `production.yaml` under `hubspot.lists`
2. Add the list ID to the matching group under `exclusion_matrix.{group}.lists`
3. Add a secondary mapping under `secondary_sync.mappings` if the list has an exit journey
4. Run `python -m corev2.cli validate-config` to check
5. Run a dry-run sync to verify: `python -m corev2.cli sync --dry-run`

---

## 10. GitHub Actions — Required Secrets

Go to: **GitHub repo → Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|--------|-------|
| `HUBSPOT_PRIVATE_APP_TOKEN` | HubSpot Private App token (scopes: crm.lists.read/write, crm.objects.contacts.read/write) |
| `MAUTIC_BASE_URL` | `https://accessibility-api.alphagnito.com` |
| `MAUTIC_USERNAME` | Mautic admin username |
| `MAUTIC_PASSWORD` | Mautic admin password |
| `TEAMS_WEBHOOK_URL` | (optional) Teams incoming webhook URL |
| `TEAMS_NOTIFICATIONS_ENABLED` | (optional) `true` to enable Teams alerts |

All Mautic credentials were set up by Yogesh (BThiruyogeshwaran) during initial deployment.

### Manual workflow trigger
Actions tab → **HubSpot ↔ Mautic Bidirectional Sync** → **Run workflow**
- **Dry run**: simulate only, no mutations
- **Only email**: debug a single contact

---

## 11. Mautic Server

- **URL**: `https://accessibility-api.alphagnito.com`
- **Login**: `https://accessibility-api.alphagnito.com/s/login`
- **Credentials**: in GitHub secrets `MAUTIC_USERNAME` / `MAUTIC_PASSWORD`
- **Hosting**: self-hosted Docker on Ubuntu server
- **API auth**: HTTP Basic Auth (username:password)

### Mautic campaigns
Yogesh set up all campaigns prior to handover. All campaigns were active at time of handover.
Before first live sync run, verify:
1. Log in to Mautic
2. Go to **Campaigns** — review which are "Active" vs "Inactive"
3. Any campaign you don't want running yet should be set to **Inactive**
4. Each campaign should have a trigger on an entry tag and a final step that applies the exit tag

### Docker permissions issue
If Mautic returns HTTP 500, it's a Docker file permissions error. The `health.py` module auto-fixes this when run directly on the server (not from GitHub Actions). The fix sequence:
```bash
docker exec mautic chown -R www-data:www-data /var/www/html
docker exec mautic chmod -R 755 /var/www/html/var
docker exec --user www-data mautic php bin/console cache:clear
docker exec mautic chown -R www-data:www-data /var/www/html/var
```

---

## 12. What Was Built / Current State

### Development history

| Date | Who | What |
|------|-----|------|
| Apr 8, 2026 | Yogesh (BThiruyogeshwaran) | Initial Mautic v3 pipeline — ported from Mailchimp |
| Apr 15, 2026 | Yogesh | HubSpot API v1 → v3 migration (v1 sunset Apr 30) |
| Apr 27, 2026 | Yogesh | Docker permission fixes |
| May 11, 2026 | Copilot (Ejas) | Full code review + 6 bugs fixed (see below) |

### Bugs fixed in commit `dcb45bb` + `554d6b2` (May 11, 2026)

| # | Severity | Bug | Fix |
|---|----------|-----|-----|
| 1 | CRITICAL | `production.yaml`: Sub Agents list ID was `989` (wrong) | Changed to `1050` (the static list) |
| 2 | CRITICAL | Sub Agents secondary sync: `remove_from_source: false` | Changed to `true` (static list needs manual removal) |
| 3 | CRITICAL | `scan_cleaned_and_sync()` (Step 1B) was entirely absent | Added full method to `unsubscribe_sync.py` + wired into `cli.py` |
| 4 | HIGH | Archival reconciliation `ArchivalReconciliation.scan_for_orphans()` was never called | Wired into `primary.py generate_plan()` |
| 5 | HIGH | Exclusion cleanup missing: contacts entering exclusion lists not removed from HubSpot lists | Added 2-pass logic to `primary.py` — generates `remove_hs_from_list` ops |
| 6 | HIGH | GitHub Actions: Python 3.12, outdated action versions, missing `git pull --rebase` | Updated to Python 3.13, `actions/checkout@v5`, `setup-python@v6` |

### Latest commits on v2/mautic

```
554d6b2  fix: add exclusion cleanup - remove_hs_from_list ops for contacts entering exclusion lists
dcb45bb  fix: port all production rules from main to Mautic branch
7489fc1  fixed permission issues  (Yogesh's delivery)
```

### All rules verified
Every invariant in PRIMARY_SYNC_RULES.md (INV-001 through INV-010) and every rule in SECONDARY_SYNC_RULES.md (SEC-001 through SEC-010) has been cross-checked against the actual code and confirmed present.

---

## 13. Known Limitations

| Item | Detail | Impact |
|------|--------|--------|
| Auto-refresh list names | Main branch auto-patches YAML when HubSpot list names drift. Not ported to Mautic branch. | Low — only affects if a HubSpot list is renamed. Fix: update `production.yaml` manually. |
| No status filter on Mautic member scan | Mautic API doesn't support filtering contacts by status server-side. All contacts are fetched and `_derive_status()` filters locally. | Slightly slower scans at large scale. Acceptable up to ~5,000 contacts. |
| Mautic `_refresh_list_names()` | Not applicable — Mautic doesn't use list names in the same way. | Non-issue. |
| Scheduled workflow requires default branch | GH Actions `schedule:` only runs on default branch (`main`). The Mautic workflow must be manually triggered until `v2/mautic` becomes default. | Manual trigger required for now. |
| Mautic campaigns must be set up manually | The campaigns (journeys) in Mautic that apply exit tags must be configured by the operator. The sync system only reads/writes contacts and tags — it doesn't create or manage Mautic campaigns. | Operator must configure campaigns per the exit tag table in Section 6. |

---

## 14. First Steps When You Take Over

### Immediate (before first run)
1. **Log into Mautic** at `https://accessibility-api.alphagnito.com/s/login`
2. **Check campaigns** — ensure only campaigns you want running are set to Active
3. **Verify GitHub secrets** — all 4 required secrets must be set (see Section 10)

### First live test (dry run)
```bash
# Trigger from GitHub Actions:
Actions → HubSpot ↔ Mautic Bidirectional Sync → Run workflow → dry_run: true
```
Check the workflow log for any errors. No mutations will happen in dry-run mode.

### First live run
```bash
# Trigger from GitHub Actions (no dry_run):
Actions → HubSpot ↔ Mautic Bidirectional Sync → Run workflow
```
Watch the run. Check:
- Plan step: how many contacts scanned, operations generated
- Apply step: successful/failed/skipped counts
- No audience cap errors

### Debug a single contact
```bash
# In GitHub Actions → Run workflow → only_email field:
user@example.com
```

### When ready for automatic scheduling
Go to GitHub repo → Settings → Branches → change default branch to `v2/mautic`.

---

## 15. Parallel Running (Mailchimp + Mautic)

The `main` branch (Mailchimp) and `v2/mautic` (Mautic) can run **simultaneously** against the same HubSpot data.

**There are no conflicts** because:
- Both systems write to different marketing platforms (Mailchimp vs Mautic)
- Both read the same HubSpot lists (read-only for primary sync)
- Both write to the same HubSpot handover lists — but the operations are idempotent (adding a contact to a list they're already in = success)
- Unsubscribes propagate to HubSpot from both — also idempotent

The main consideration is that currently **only `main` runs on schedule**. `v2/mautic` must be manually triggered or the default branch changed.

---

## 16. Reference Documents in This Repo

| File | What it is |
|------|-----------|
| `README.md` | Operator guide: architecture, quick start, config, GitHub Actions setup |
| `corev2/PRIMARY_SYNC_RULES.md` | Complete primary sync business rules (HubSpot → Mautic) |
| `corev2/SECONDARY_SYNC_RULES.md` | Complete secondary sync rules (Mautic → HubSpot exit tags) |
| `docs/AGENT_HANDOVER.md` | This file — comprehensive handover brief |
| `docs/DEPLOYMENT_GUIDE.md` | Step-by-step deployment and operations guide |
| `.env.example` | Environment variable template |
| `deploy_me.md` | Quick Ubuntu server deploy commands |

---
