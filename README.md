# HubSpot ↔ Mautic Sync Pipeline

Bidirectional sync between HubSpot CRM and Mautic marketing automation.
Runs every 8 hours via GitHub Actions. All business rules are enforced deterministically.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Pipeline Flow](#pipeline-flow)
3. [Quick Start](#quick-start)
4. [Configuration](#configuration)
5. [Running Locally](#running-locally)
6. [GitHub Actions Setup](#github-actions-setup)
7. [HubSpot Lists & Tags](#hubspot-lists--tags)
8. [Secondary Sync - Exit Tags](#secondary-sync--exit-tags)
9. [Safety Gates](#safety-gates)
10. [Audience Cap Guard](#audience-cap-guard)
11. [Troubleshooting](#troubleshooting)
12. [File Structure](#file-structure)

---

## Architecture

```
HubSpot CRM  ---- Stage 1 (Primary Sync) ----►  Mautic
              ◄--- Stage 3 (Secondary Sync) ----  Mautic
                         ▲
                Stage 2: Email journeys run in Mautic
                         (Ejas applies exit tags manually)
```

### Three-Stage Pipeline

| Stage   | Direction        | Trigger                   | What it does                                      |
| ------- | ---------------- | ------------------------- | ------------------------------------------------- |
| Stage 1 | HubSpot → Mautic | Every 8 hours (scheduled) | Creates/updates contacts with correct tags        |
| Stage 2 | Inside Mautic    | Ejas applies exit tags    | Email journeys run until contact finishes         |
| Stage 3 | Mautic → HubSpot | Every 8 hours (scheduled) | Moves finished contacts to HubSpot handover lists |

---

## Pipeline Flow

### Stage 1 - Primary Sync (HubSpot → Mautic)

1. Scans all 8 HubSpot lists using batch API (100 contacts per API call)
2. Applies 4-group exclusion matrix:
   - **Excluded always**: Lists 762 (Unsubscribed), 773 (Manual Disengagement)
   - **Excluded from General/SubAgents/etc**: List 717 (Active Deals)
   - **Manual Override (784)**: bypasses Active Deals exclusion
3. Resolves correct Mautic tag per contact:
   - `branches > 1` → General Multi; else → General Single
   - Recruitment, Competition, Sub Agents, New Agents, Sanctioned → own tags
4. Preserves existing tags (first-tag priority, INV-004)
5. Creates/updates contacts in Mautic
6. Applies/removes tags
7. Enforces audience cap (5,000 hard limit)

### Stage 2 - Email Journey (Inside Mautic)

Ejas builds and runs email campaigns in Mautic targeting each tag group.
When a contact completes a journey, the **exit tag** is applied in Mautic:

| Tag applied in Mautic       | Meaning                                  |
| --------------------------- | ---------------------------------------- |
| `General Single Finished`   | General journey complete (1 branch)      |
| `General Multi Finished`    | General journey complete (2+ branches)   |
| `Recruitment Finished`      | Recruitment journey complete             |
| `Competition Finished`      | Competition journey complete             |
| `Sub Agents Finished`       | Sub Agents journey complete              |
| `New Agents Finished`       | New Agents journey complete              |
| `Sanctioned Finished`       | Sanctioned journey complete              |
| `Long Term Single Finished` | Long Term (1 branch) journey complete    |
| `Long Term Multi Finished`  | Long Term (2+ branches) journey complete |

### Stage 3 - Secondary Sync (Mautic → HubSpot)

1. Scans all Mautic contacts for exit tags
2. Skips contacts with `Manual Inclusion` tag (SEC-008)
3. For each exit-tagged contact:
   - Adds to HubSpot handover list (destination_list)
   - Removes from source lists (if remove_from_source=true)
   - Sub Agents Finished: also removes from lists 900, 972, 971
   - Long Term Finished: Mautic cleanup only (no HubSpot destination)
   - Removes all tags from Mautic
   - Archives contact from Mautic

---

## Quick Start

### Prerequisites

- Python 3.12+
- HubSpot Private App with scopes: `crm.lists.read`, `crm.lists.write`, `crm.objects.contacts.read`, `crm.objects.contacts.write`
- Mautic 7 with API enabled and HTTP Basic Auth enabled

### Install

```bash
git clone https://github.com/your-org/HB-Mautic-Sync-V3.git
cd HB-Mautic-Sync-V3
pip install -r requirements.txt
```

### Set up credentials

```bash
cp .env.example .env
nano .env
```

Fill in:

```
HUBSPOT_PRIVATE_APP_TOKEN=pat-eu1-...
MAUTIC_BASE_URL=https://your-mautic-domain.com
MAUTIC_USERNAME=admin@yourdomain.com
MAUTIC_PASSWORD=your_password
```

### Validate config

```bash
python -m corev2.cli validate-config
```

Expected output:

```
Config valid: 8 lists, 9 secondary mappings, cap=5000
```

---

## Configuration

Config file: `corev2/config/production.yaml`

All sensitive values use `${ENV_VAR}` placeholders resolved at runtime.

### Key settings

```yaml
mautic:
  audience_cap: 5000 # Hard subscriber limit

safety:
  run_mode: "prod" # Must be "prod" for live apply
  allow_apply: true # Must be true for live apply
  allow_archive: true # Must be true to archive contacts
  allow_unlimited: true # Must be true for full sync (test_contact_limit=0)
  test_contact_limit: 0 # 0 = no limit
  enable_hubspot_writes: false # Set true to write ORI_LISTS back to HubSpot

notifications:
  enabled: false # Set true to enable Teams alerts
  webhook_url: "" # Microsoft Teams incoming webhook URL
```

### Adding a new HubSpot list

1. Add to the appropriate group in `production.yaml` under `hubspot.lists`
2. Add the list ID to the matching group under `exclusion_matrix`
3. Add a secondary sync mapping if needed under `secondary_sync.mappings`
4. Regenerate and apply: `python -m corev2.cli sync`

---

## Running Locally

### Full sync (plan + apply)

```bash
python -m corev2.cli sync
```

### Step-by-step

```bash
# Step 1: Generate plan (read-only, no mutations)
python -m corev2.cli plan --output corev2/artifacts/plan.json

# Step 2: Review the plan
cat corev2/artifacts/plan.json | python3 -m json.tool | head -50

# Step 3: Dry-run apply (simulate, no mutations)
python -m corev2.cli apply --plan corev2/artifacts/plan.json --dry-run

# Step 4: Live apply
python -m corev2.cli apply --plan corev2/artifacts/plan.json
```

### Debug single contact

```bash
# By email
python -m corev2.cli plan --only-email user@example.com --output /tmp/debug.json

# By HubSpot VID
python -m corev2.cli plan --only-vid 123456789 --output /tmp/debug.json
```

---

## GitHub Actions Setup

### 1. Push code to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/your-org/HB-Mautic-Sync-V3.git
git push -u origin main
```

### 2. Add repository secrets

Go to: **GitHub repo → Settings → Secrets and variables → Actions → New repository secret**

Add these secrets:

| Secret name                   | Value                            |
| ----------------------------- | -------------------------------- |
| `HUBSPOT_PRIVATE_APP_TOKEN`   | Your HubSpot private app token   |
| `MAUTIC_BASE_URL`             | `https://your-mautic-domain.com` |
| `MAUTIC_USERNAME`             | Mautic admin email               |
| `MAUTIC_PASSWORD`             | Mautic admin password            |
| `TEAMS_WEBHOOK_URL`           | (optional) Teams webhook URL     |
| `TEAMS_NOTIFICATIONS_ENABLED` | (optional) `true` or `false`     |

### 3. Enable GitHub Actions

Go to **Actions** tab in your repo and enable workflows.

### 4. Test with manual trigger

Go to **Actions → HubSpot ↔ Mautic Bidirectional Sync → Run workflow**

Options:

- **Dry run**: tick to simulate without mutations
- **Only email**: enter a single email to debug one contact

### 5. Schedule

The workflow runs automatically at:

- 00:00 UTC
- 08:00 UTC
- 16:00 UTC

To change the schedule, edit `.github/workflows/sync.yml`:

```yaml
- cron: "0 0,8,16 * * *"
```

---

## HubSpot Lists & Tags

### Sync lists (configured in production.yaml)

| List ID | Name             | Tag applied in Mautic                                   |
| ------- | ---------------- | ------------------------------------------------------- |
| 969     | Sanctioned       | `Sanctioned`                                            |
| 719     | Recruitment      | `Recruitment`                                           |
| 720     | Competition      | `Competition`                                           |
| 989     | Sub Agents       | `Sub Agents`                                            |
| 945     | New Agents       | `New Agents`                                            |
| 987     | General          | `General Single` or `General Multi` (branch split)      |
| 784     | Manual Inclusion | `General Single` + `Manual Inclusion` tag               |
| 1032    | Long Term        | `General Single Long Term` or `General Multi Long Term` |

### Exclusion lists (never synced)

| List ID | Name                   | Type                           |
| ------- | ---------------------- | ------------------------------ |
| 762     | Unsubscribed/Opted Out | DYNAMIC (HubSpot auto-manages) |
| 773     | Manual Disengagement   | DYNAMIC (criteria-based)       |
| 717     | Active Deals           | DYNAMIC (deal pipeline)        |

### Branch split rule

Contacts in lists 987, 784, 1032 get `General Multi` if `branches > 1`,
otherwise `General Single`.

---

## Secondary Sync - Exit Tags

When Ejas finishes a campaign in Mautic, apply the exit tag to the contact:

```bash
# Via Mautic API (for testing)
curl -u "admin@yourdomain.com:password" \
  -X PATCH \
  -H "Content-Type: application/json" \
  -d '{"tags":["General Single Finished"]}' \
  "https://your-mautic.com/api/contacts/{id}/edit"
```

### Exit tag mappings

| Exit tag                    | HubSpot destination             | Notes                           |
| --------------------------- | ------------------------------- | ------------------------------- |
| `General Single Finished`   | List 946 (General Handover)     |                                 |
| `General Multi Finished`    | List 946 (General Handover)     |                                 |
| `Recruitment Finished`      | List 947 (Recruitment Handover) | Removed from list 719           |
| `Competition Finished`      | List 948 (Competition Handover) | Removed from list 720           |
| `Sub Agents Finished`       | List 1005 (Sub Agents Handover) | Also removed from 900, 972, 971 |
| `New Agents Finished`       | List 949 (New Agents Handover)  | Removed from list 945           |
| `Sanctioned Finished`       | List 1006 (Sanctioned Handover) | Removed from list 969           |
| `Long Term Single Finished` | None (MC cleanup only)          |                                 |
| `Long Term Multi Finished`  | None (MC cleanup only)          |                                 |

### Exempt from secondary sync

Contacts with the `Manual Inclusion` tag are **never processed** by secondary sync (SEC-008).

---

## Safety Gates

The apply mode enforces all safety gates before making any mutations:

| Gate            | Config key               | Required value                      |
| --------------- | ------------------------ | ----------------------------------- |
| Run mode        | `safety.run_mode`        | `"prod"`                            |
| Apply enabled   | `safety.allow_apply`     | `true`                              |
| Unlimited mode  | `safety.allow_unlimited` | `true` (when test_contact_limit=0)  |
| Archive enabled | `safety.allow_archive`   | `true` (if plan has archive ops)    |
| Config hash     | (automatic)              | Plan hash must match current config |

If any gate fails, the sync aborts with a clear error message.

---

## Audience Cap Guard

Mautic audience cap is set to **5,000 contacts** by default.

Behaviour:

- **Pre-flight**: fetches live count before sync starts; aborts if >= 5,000
- **Per-contact**: skips new upserts if cap reached mid-sync
- **Re-check**: re-fetches live count every 10 new subscribes
- **Warning**: sends Teams alert if < 50 slots remain
- **Alert**: sends Teams alert when cap is reached

To change the cap, update `production.yaml`:

```yaml
mautic:
  audience_cap: 5000
```

---

## Troubleshooting

### "Config hash mismatch"

The plan was generated with a different config than what is currently active.
Regenerate the plan: `python -m corev2.cli plan`

### "audience_cap_reached"

Mautic has hit the 5,000 contact limit. Either:

1. Archive completed contacts (run Stage 3 with exit-tagged contacts)
2. Increase `audience_cap` in `production.yaml`

### Tags not applied (404 error)

Mautic 7 uses `PATCH /api/contacts/{id}/edit` for tag operations.
Do NOT use `/api/contacts/{id}/tags/edit` (removed in Mautic 7).

### "This value is too long" (firstname/lastname)

Mautic has a 64-character limit on text fields. The client automatically
truncates these fields. If you see this error, it is from a legacy version

- update to this version.

### Contact not found after creation (tag fails)

The client invalidates the ID cache before tag operations to handle
newly-created contacts. If you still see this, check that the `upsert_member`
returned a successful `created` or `updated` action before the tag op ran.

### HubSpot 403 "missing scopes"

Your HubSpot private app is missing list write permissions. Add:

- `crm.lists.read`
- `crm.lists.write`

Then regenerate the token in HubSpot and update `HUBSPOT_PRIVATE_APP_TOKEN`.

---

## File Structure

```
HB-Mautic-Sync-V3/
├-- .env.example                    # Credentials template
├-- .gitignore                      # Protects .env
├-- main.py                         # Entry point (local dev)
├-- requirements.txt
├-- .github/
│   └-- workflows/
│       └-- sync.yml                # GitHub Actions (8-hourly schedule)
├-- corev2/
│   ├-- cli.py                      # CLI modes: validate-config, plan, apply, sync
│   ├-- notifications.py            # Microsoft Teams adaptive card alerts
│   ├-- clients/
│   │   ├-- http_base.py            # Retry/backoff/circuit-breaker base client
│   │   ├-- hubspot_client.py       # HubSpot API (batch-optimised)
│   │   └-- mautic_client.py        # Mautic REST API (all endpoints verified)
│   ├-- config/
│   │   ├-- schema.py               # Pydantic models (V2Config, all rules)
│   │   ├-- loader.py               # YAML + env var resolution
│   │   └-- production.yaml         # Live config (8 lists, 9 mappings)
│   ├-- planner/
│   │   ├-- primary.py              # HubSpot → Mautic plan generation
│   │   ├-- secondary.py            # Mautic → HubSpot exit tag handling
│   │   └-- reconciliation.py       # Orphan detection and archival
│   ├-- executor/
│   │   └-- engine.py               # Executes plan, AudienceCapGuard, JSONL journal
│   ├-- sync/
│   │   └-- unsubscribe_sync.py     # Mautic unsubscribes → HubSpot opt-out
│   └-- artifacts/                  # Generated plans and execution journals
└-- logs/                           # Sync run logs (committed by GitHub Actions)
```
