# HubSpot <-> Mailchimp Sync (corev2)

Automated bidirectional sync between HubSpot CRM lists and a Mailchimp audience. Runs every 8 hours via GitHub Actions. Contacts flow from HubSpot -> Mailchimp with campaign tags, and exit-tagged contacts flow back from Mailchimp -> HubSpot handover lists.

---

## Quick Start

### Prerequisites
- Python 3.13+
- HubSpot Private App token (CRM Lists + Contacts scopes)
- Mailchimp API key
- Microsoft Teams webhook URL (for alerts)

### Setup

```bash
# Clone & install
git clone https://github.com/kjql33/HB-Mailchimp-Sync.git
cd HB-Mailchimp-Sync
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # macOS/Linux
pip install -r requirements-v2.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys (see Environment Variables below)
```

### Environment Variables (.env)

| Variable | Description |
|----------|-------------|
| `HUBSPOT_PRIVATE_APP_TOKEN` | HubSpot Private App API token |
| `MAILCHIMP_API_KEY` | Mailchimp API key (format: `key-dc`) |
| `MAILCHIMP_LIST_ID` | Mailchimp audience ID (e.g., `d0e267ecff`) |
| `TEAMS_WEBHOOK_URL` | Microsoft Teams webhook for alerts |

### Run Locally

```bash
# Validate config
python -m corev2.cli validate-config

# Generate plan (safe, read-only)
python -m corev2.cli plan

# Execute sync (LIVE - mutates HubSpot + Mailchimp)
python -m corev2.cli apply

# Plan + Apply in one command
python -m corev2.cli sync
```

---

## Architecture

```
corev2/
  cli.py                  # Main entrypoint (plan/apply/sync/validate-config)
  notifications.py        # Teams webhook alerts (audience cap, errors)
  config/
    production.yaml       # All list mappings, safety gates, secondary config
    schema.py             # Pydantic config models + validation
  clients/
    hubspot_client.py     # HubSpot CRM v3 API client
    mailchimp_client.py   # Mailchimp Marketing API client (+ audience stats)
  planner/
    primary.py            # Plan generator: HubSpot lists -> MC operations
    secondary.py          # Plan generator: MC exit tags -> HS operations
  executor/
    engine.py             # Operation executor + AudienceCapGuard
main.py                   # Thin wrapper (sets LOAD_DOTENV, calls cli.sync_mode)
```

### Execution Flow (every 8 hours)

```
Step 1: Unsubscribe Sync    Mailchimp unsubs -> HubSpot opt-out status
Step 2: Primary Sync        HubSpot lists -> Mailchimp (tags, subscribe, orphan cleanup)
Step 3: Secondary Sync      Mailchimp exit tags -> HubSpot handover lists + MC archive
```

---

## System Features

### Primary Sync (HubSpot -> Mailchimp)
- **8 HubSpot lists** across 4 groups synced to 1 Mailchimp audience
- **Branch split:** contacts tagged General Single/Multi based on HubSpot `branches` property
- **Exclusion matrix:** compliance (762, 773) + active deals (717) block sync
- **Manual Inclusion (list 784):** bypasses active deals, only blocked by compliance
- **Long Term Marketing (list 1032):** separate journey with Long Term tag variants
- **First-tag priority:** existing MC tags preserved, no mid-journey switching
- **Orphan archival:** contacts removed from HS lists -> untagged + archived in MC (max 100/run)
- **Auto-refresh list names:** YAML auto-patched from live HubSpot names each run

### Secondary Sync (Mailchimp -> HubSpot)
- **9 exit tag mappings** route contacts to HubSpot handover lists
- **Sub Agents cleanup:** removes from 3 additional static sublists (900, 972, 971)
- **Long Term mappings:** MC cleanup only (no HubSpot destination)
- **Exempt tags:** contacts with "Manual Inclusion" tag skipped entirely

### Safety
- **Audience cap:** 5,000 subscribed member hard limit with Teams alerts
- **Triple-lock safety gates:** run_mode + allow_apply + allow_unlimited + allow_archive
- **Archive exemptions:** VIP, DoNotArchive, Manual_Override, Manual Inclusion tags
- **Never resubscribe:** opted-out/cleaned contacts are never restored
- **Compliance lists validated at config load time**

### Monitoring
- **Teams alerts:** audience cap reached/warning, sync failures
- **Execution journal:** `corev2/artifacts/execution_journal.jsonl` logs every operation
- **Plan artifacts:** `corev2/artifacts/plan_*.json` for pre-execution review

---

## Key Documentation

| File | Description |
|------|-------------|
| `corev2/PRIMARY_SYNC_RULES.md` | Complete primary sync rules, invariants, verified behaviors |
| `corev2/SECONDARY_SYNC_RULES.md` | Exit tag mappings, operation chains, safety rules |
| `corev2/config/production.yaml` | All configuration (lists, exclusions, secondary, safety) |
| `docs/GITHUB_DEPLOYMENT_GUIDE.md` | GitHub Actions setup, secrets, deployment checklist |
| `docs/PIPELINE_VERIFICATION_REPORT.md` | Verification of all 9 sync pipelines against live APIs |

---

## GitHub Actions

**Schedule:** Every 8 hours (00:00, 08:00, 16:00 UTC)
**Workflow:** `.github/workflows/sync.yml`
**Runtime:** Python 3.13, Ubuntu latest
**Manual trigger:** Actions tab -> "Run workflow"

### Required Secrets

| Secret | Description |
|--------|-------------|
| `HUBSPOT_PRIVATE_APP_TOKEN` | HubSpot API token |
| `MAILCHIMP_API_KEY` | Mailchimp API key |
| `MAILCHIMP_AUDIENCE_ID` | Mailchimp audience ID |
| `TEAMS_WEBHOOK_URL` | Teams webhook for alerts |

---

## Configuration

All sync configuration lives in `corev2/config/production.yaml`. Key sections:

- **hubspot.lists** - 4 groups of HubSpot lists with tags + tag_overrides
- **exclusion_matrix** - per-group exclusion list mappings
- **secondary_sync** - 9 exit tag mappings + exempt_tags
- **mailchimp.audience_cap** - hard subscriber limit (5,000)
- **safety** - triple-lock gates for production mutations
- **archival** - exempt tags + max archives per run

See `corev2/config/schema.py` for Pydantic model definitions and validation rules.

---

## Mailchimp Customer Journeys

| Tag | Journey | Purpose |
|-----|---------|---------|
| General Single | General Single Journey | Single-branch contacts |
| General Multi | General Multi Journey | Multi-branch contacts |
| Recruitment | Recruitment Journey | Recruitment pipeline |
| Competition | Competition Journey | Competition pipeline |
| Sub Agents | Sub Agents Journey | Sub agents pipeline |
| New Agents | New Agents Journey | New agents pipeline |
| Sanctioned | Sanctioned Journey | Sanctioned agents pipeline |
| General Single Long Term | Long Term Single Journey | Long-term single-branch |
| General Multi Long Term | Long Term Multi Journey | Long-term multi-branch |

Each journey ends by applying a "Finished" exit tag, which triggers secondary sync.
