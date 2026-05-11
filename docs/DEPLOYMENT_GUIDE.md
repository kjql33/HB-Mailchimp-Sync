# Deployment & Operations Guide — HubSpot ↔ Mautic Sync
**Branch:** `v2/mautic`
**Last Updated:** 2026-05-11

---

## Prerequisites

- Python 3.13+
- Git
- Access to the GitHub repository (`kjql33/HB-Mailchimp-Sync`, branch `v2/mautic`)
- HubSpot Private App token (scopes: `crm.lists.read`, `crm.lists.write`, `crm.objects.contacts.read`, `crm.objects.contacts.write`)
- Mautic admin credentials for `https://accessibility-api.alphagnito.com`

---

## GitHub Secrets Setup

Go to: **GitHub repo → Settings → Secrets and variables → Actions → New repository secret**

| Secret | Required | Value |
|--------|----------|-------|
| `HUBSPOT_PRIVATE_APP_TOKEN` | ✅ | HubSpot Private App token |
| `MAUTIC_BASE_URL` | ✅ | `https://accessibility-api.alphagnito.com` |
| `MAUTIC_USERNAME` | ✅ | Mautic admin username |
| `MAUTIC_PASSWORD` | ✅ | Mautic admin password |
| `TEAMS_WEBHOOK_URL` | optional | Teams incoming webhook URL for alerts |
| `TEAMS_NOTIFICATIONS_ENABLED` | optional | `true` to enable Teams alerts |

---

## Local Setup

```bash
# Clone the repo and checkout the Mautic branch
git clone https://github.com/kjql33/HB-Mailchimp-Sync.git
cd HB-Mailchimp-Sync
git checkout v2/mautic

# Create and activate virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up credentials
cp .env.example .env
# Edit .env with your credentials:
# HUBSPOT_PRIVATE_APP_TOKEN=pat-eu1-...
# MAUTIC_BASE_URL=https://accessibility-api.alphagnito.com
# MAUTIC_USERNAME=admin@yourdomain.com
# MAUTIC_PASSWORD=your_password
```

---

## First Run Procedure

### Step 1: Validate config (no API calls)
```bash
python -m corev2.cli validate-config
```
Expected output:
```
Config valid: 8 lists, 9 secondary mappings, cap=5000
```

### Step 2: Generate plan (read-only)
```bash
python -m corev2.cli plan --output corev2/artifacts/plan.json
```
Review the plan output:
- `total_contacts_scanned` — how many contacts were found in HubSpot lists
- `contacts_with_operations` — how many need changes in Mautic
- `operations_by_type` — breakdown of what will happen

### Step 3: Dry-run apply (simulate, no mutations)
```bash
python -m corev2.cli apply --plan corev2/artifacts/plan.json --dry-run
```
All operations simulated, nothing written to Mautic or HubSpot.

### Step 4: Live apply
```bash
python -m corev2.cli apply --plan corev2/artifacts/plan.json
```

### Full sync (plan + apply in one command)
```bash
python -m corev2.cli sync
```

---

## Debugging a Single Contact

Useful for verifying a specific contact without processing the full audience:

```bash
# By email
python -m corev2.cli plan --only-email user@example.com --output /tmp/debug.json
cat /tmp/debug.json

# Dry-run apply for that contact only
python -m corev2.cli apply --plan /tmp/debug.json --dry-run
```

---

## GitHub Actions Usage

### Manual trigger (current primary method)
1. Go to: **GitHub repo → Actions**
2. Click **HubSpot ↔ Mautic Bidirectional Sync**
3. Click **Run workflow**
4. Options:
   - **dry_run**: `true` to simulate without mutations
   - **only_email**: enter email to debug a single contact

### Automated scheduling
The workflow is scheduled for `0 0,8,16 * * *` (00:00, 08:00, 16:00 UTC) **but only fires from the default branch**.

To enable automatic scheduling:
1. Go to GitHub repo → Settings → Branches
2. Change default branch from `main` to `v2/mautic`

### Workflow steps
```
1. Checkout repository
2. Set up Python 3.13
3. Install dependencies (pip install -r requirements.txt)
4. Validate config
5. Generate operations plan  (--skip-health-check)
6. Apply plan               (--skip-health-check)
7. Commit logs and artifacts to repo
8. Upload debug artifacts on failure (14 day retention)
```

`--skip-health-check`: The Mautic Docker health check and auto-fix requires SSH access to the server. GitHub Actions runners cannot do this — skip it in CI.

---

## Monitoring

### GitHub Actions logs
Each run produces logs in the Actions tab. Key things to look for:
- **Plan step**: "Total unique contacts: X", "Contacts with operations: Y"
- **Apply step**: "successful: X, failed: Y, skipped: Z"
- Any `ERROR` lines in the log output

### Execution journal
`corev2/artifacts/execution_journal.jsonl` — appended each run, committed to the repo by the workflow. Contains per-operation events with timestamps.

### Teams notifications
If `TEAMS_NOTIFICATIONS_ENABLED=true` and `TEAMS_WEBHOOK_URL` is set, the system sends alerts when:
- Audience cap (5,000) is reached or approaching (< 50 slots remaining)

---

## Config Changes

### Modifying production.yaml
After any change to `production.yaml`, you must regenerate the plan before applying:
```bash
python -m corev2.cli plan --output corev2/artifacts/plan.json
```
The apply step verifies a config hash from the plan. A stale plan from a different config version will be rejected.

### Adding a new HubSpot list
1. Add the list under the correct group in `production.yaml` → `hubspot.lists.{group}`
2. Add the list ID to `exclusion_matrix.{group}.lists`
3. If it has an exit journey, add a mapping under `secondary_sync.mappings`
4. Validate: `python -m corev2.cli validate-config`
5. Test: `python -m corev2.cli sync --dry-run` (or full plan + dry-run apply)

---

## Mautic Server Operations

### Log in
URL: `https://accessibility-api.alphagnito.com/s/login`
Credentials: from GitHub secrets `MAUTIC_USERNAME` / `MAUTIC_PASSWORD`

### Check API health
```bash
curl -u username:password https://accessibility-api.alphagnito.com/api/contacts?limit=1
```
Expected: HTTP 200 with JSON contact list.

### Fix Docker permissions (if Mautic returns 500)
SSH into the Ubuntu server, then:
```bash
docker exec mautic chown -R www-data:www-data /var/www/html
docker exec mautic chmod -R 755 /var/www/html/var
docker exec --user www-data mautic php bin/console cache:clear
docker exec mautic chown -R www-data:www-data /var/www/html/var
```

### Restart Mautic container
```bash
docker restart mautic
```

### View Mautic logs
```bash
docker logs mautic --tail 50
```

---

## Safety Gates

Before any live mutation, ALL four gates must pass (checked in `apply_mode()`):

| Gate | Config key | Required value |
|------|------------|----------------|
| Run mode | `safety.run_mode` | `prod` |
| Allow apply | `safety.allow_apply` | `true` |
| Unlimited contacts | `safety.allow_unlimited` | `true` (when `test_contact_limit=0`) |
| Allow archival | `safety.allow_archive` | `true` |

To run with a contact limit for testing, set `test_contact_limit: 10` (and remove `allow_unlimited: true`).

---

## Troubleshooting

### "Config hash mismatch"
The plan was generated with a different version of `production.yaml`.
Fix: regenerate the plan: `python -m corev2.cli plan`

### "Mautic API is not healthy"
Mautic is returning HTTP 500. Fix Docker permissions (see above). Note: auto-fix only works when running locally on the server, not from GitHub Actions.

### "run_mode must be 'prod'"
`production.yaml` has `run_mode: staging` or similar. Change to `prod`.

### "allow_apply=false"
Set `allow_apply: true` in `production.yaml`.

### High `failed` count in executor
Check the execution journal (`corev2/artifacts/execution_journal.jsonl`) for the specific errors. Common causes:
- Mautic API rate limit (the client has built-in retry with backoff)
- Contact not found in HubSpot (warning, not error — contact skipped)
- Network timeouts (transient — will resolve on next run)

### Plan generates 0 operations
Normal if Mautic already has all contacts at the correct tags. The system is idempotent — re-running when state matches produces no operations.

---

## Key API Endpoints

### Mautic
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/contacts?search={email}` | Look up contact by email |
| POST | `/api/contacts/new` | Create contact |
| PATCH | `/api/contacts/{id}/edit` | Update contact / apply/remove tags |
| POST | `/api/contacts/{id}/dnc/email/add` | Unsubscribe (reason=1) |
| DELETE | `/api/contacts/{id}/delete` | Archive contact |
| GET | `/api/contacts?limit=200&start=N` | Paginate all contacts |

### HubSpot (v3 only)
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/crm/v3/lists/{id}/memberships` | Get list members |
| POST | `/crm/v3/objects/contacts/batch/read` | Batch read contact details |
| POST | `/crm/v3/objects/contacts/search` | Look up contact by email |
| PUT | `/crm/v3/lists/{id}/memberships/add` | Add contact to list |
| PUT | `/crm/v3/lists/{id}/memberships/remove` | Remove contact from list |
| PATCH | `/crm/v3/objects/contacts/{id}` | Update contact property |
| POST | `/crm/v3/objects/contacts/{id}/subscriptions/status` | Opt out |

---
