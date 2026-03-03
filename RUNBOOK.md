# HubSpot \u2194 Mailchimp Sync V2 - Operations Runbook

**System:** `corev2` - Primary sync from HubSpot lists to Mailchimp tags  
**Version:** 2.0  
**Last Updated:** 2026-01-27

---

## \ud83d\udd12 Quick Reference

| Command | Purpose | Safety Level |
|---------|---------|--------------|
| `python -m corev2.cli plan` | Generate operations plan (dry-run) | \ud83d\udfe2 SAFE (read-only) |
| `python -m corev2.cli apply --plan <file>` | Execute planned operations | \ud83d\udd34 MUTATES DATA |

**Golden Rule:** ALWAYS `plan` before `apply`. Review plan files before execution.

---

## \ud83d\ude80 First Production Run (Recommended Sequence)

### 1. Plan Generation (Safe, Read-Only)

```bash
# Full scan with safety limits
python -m corev2.cli plan \\
  --config corev2/config/production.yaml \\
  --output corev2/artifacts/plan_production_$(date +%Y%m%d_%H%M%S).json
```

**Expected Output:**
- `Plan saved to: corev2/artifacts/plan_production_...json`
- Check log for warnings about compliance lists, strict mode skips

### 2. Plan Review (CRITICAL STEP)

```bash
# View plan summary
$plan = Get-Content corev2/artifacts/plan_production_*.json | ConvertFrom-Json
Write-Output \"Total contacts: $($plan.summary.total_contacts_scanned)\"
Write-Output \"With operations: $($plan.summary.contacts_with_operations)\"
$plan.summary.operations_by_type
```

**Review Checklist:**
- [ ] `total_contacts_scanned` matches expected list sizes
- [ ] `operations_by_type` looks reasonable (upsert + tag operations)
- [ ] Check for unexpected `archive_mc_member` operations (if archival disabled)
- [ ] Inspect sample operations: `$plan.operations[0..4]`
- [ ] Verify no compliance list contacts (762, 773) in operations

### 3. Small Batch Test (10-20 Contacts)

```bash
# Generate plan with contact limit
python -m corev2.cli plan \\
  --config corev2/config/production.yaml \\
  --output corev2/artifacts/plan_batch_small.json

# Edit production.yaml temporarily: test_contact_limit: 20

# Review + Apply
python -m corev2.cli apply --plan corev2/artifacts/plan_batch_small.json
```

**Validation:**
- Check `execution_journal.jsonl` for success/failures
- Sample verify 3-5 contacts in Mailchimp (correct tags, no duplicates)
- Confirm idempotency: Re-run same plan, verify `action=updated` (not created)

### 4. Full Production Run

```bash
# Remove contact_limit from production.yaml (set test_contact_limit: 0, allow_unlimited: true)

# Generate full plan
python -m corev2.cli plan \\
  --config corev2/config/production.yaml \\
  --output corev2/artifacts/plan_full_production.json

# REVIEW CAREFULLY

# Execute
python -m corev2.cli apply --plan corev2/artifacts/plan_full_production.json
```

---

## \ud83d\udd0d Inspecting Execution Journal

**Location:** `corev2/artifacts/execution_journal.jsonl`

```powershell
# View last 20 operations
Get-Content corev2/artifacts/execution_journal.jsonl -Tail 20 | ConvertFrom-Json | Format-List

# Check for failures
Get-Content corev2/artifacts/execution_journal.jsonl | ConvertFrom-Json | Where-Object { $_.result.success -eq $false }

# Get execution summary
Get-Content corev2/artifacts/execution_journal.jsonl | ConvertFrom-Json | Where-Object { $_.event -eq 'execution_completed' } | Select-Object -Last 1

# Filter by contact email
Get-Content corev2/artifacts/execution_journal.jsonl | ConvertFrom-Json | Where-Object { $_.email -like '*example@domain.com*' } | Format-List
```

---

## \ud83d\udd27 Common Operations

### Single Contact Sync (Deterministic Targeting)

```bash
# Plan for single contact
python -m corev2.cli plan \\
  --config corev2/config/production.yaml \\
  --only-email john.doe@example.com \\
  --output corev2/artifacts/plan_single_contact.json

# Review + Apply
python -m corev2.cli apply --plan corev2/artifacts/plan_single_contact.json
```

**Use Cases:**
- Testing new list configurations
- Fixing individual contact issues
- Verifying INV-004 tag replacement

### Rollback Bad Tag State

If a contact has wrong tags in Mailchimp:

1. **Check current state:**
   ```powershell
   # Using Mailchimp API directly
   $email = \"contact@example.com\"
   $hash = [System.BitConverter]::ToString(
       [System.Security.Cryptography.MD5]::Create().ComputeHash(
           [System.Text.Encoding]::UTF8.GetBytes($email.ToLower())
       )
   ).Replace(\"-\",\"\").ToLower()
   
   curl -u \"anystring:$env:MAILCHIMP_API_KEY\" \\
     \"https://$env:MAILCHIMP_DC.api.mailchimp.com/3.0/lists/$env:MAILCHIMP_LIST_ID/members/$hash\"
   ```

2. **Regenerate plan for that contact** (will correct tags via INV-004)

3. **Apply:**
   ```bash
   python -m corev2.cli apply --plan corev2/artifacts/plan_single_contact.json
   ```

---

## \u26a0\ufe0f Safety Gates & Limits

### Configuration Flags

| Flag | Default | Purpose |
|------|---------|---------|
| `run_mode` | `test` | Must be `\"prod\"` for production |
| `allow_apply` | `false` | Must be `true` to enable mutations |
| `allow_archive` | `false` | Must be `true` if plan has archive ops |
| `test_contact_limit` | 0 | Max contacts per run (0=unlimited) |
| `allow_unlimited` | `false` | Required when `test_contact_limit=0` |

### Archival Safety

| Setting | Default | Purpose |
|---------|---------|---------|
| `archival.max_archive_per_run` | 25 | Max members archived per execution |
| `archival.exempt_tags` | `[VIP, DoNotArchive]` | Tags preventing archival |
| `archival.preservation_patterns` | `[^Manual_.*, ^Custom_.*]` | Regex patterns for preservation |

**Example:** If 100 orphaned members exist, only 25 will be archived per run (prevents mass archival accidents).

---

## \ud83d\udc1e Troubleshooting

### Issue: \"Plan generation failed: Compliance list X found in lists\"

**Cause:** Config validation detected compliance list (762, 773) in exclusion_matrix.lists  
**Fix:** Remove compliance lists from `general_marketing.lists`, `special_campaigns.lists`, etc. They must ONLY be in `exclude` arrays.

### Issue: \"STRICT MODE: Skipping contact\"

**Cause:** Mailchimp API failed (non-404 error) when fetching member tags  
**Impact:** Contact skipped (zero operations generated)  
**Fix:** Check Mailchimp API health, rate limits, network connectivity

### Issue: Contact has 2 source tags (violates INV-004)

**Cause:** Manual tag addition or previous system bug  
**Fix:** Regenerate plan for contact \u2192 INV-004 enforcement will remove old tag  
**Example:**
```bash
python -m corev2.cli plan --only-email problem@example.com --config production.yaml --output fix.json
python -m corev2.cli apply --plan fix.json
```

### Issue: Archived member not re-subscribing (INV-005)

**Expected Behavior:** INV-005 prevents resubscribing archived/unsubscribed members  
**If Unwanted:** Member must manually resubscribe (required by email compliance laws)

---

## \ud83d\udcca Rate Limits & Performance

### API Rate Limits

| Service | Limit | Config |
|---------|-------|--------|
| HubSpot | 10 req/sec | `HUBSPOT_PAGE_DELAY=0.1` |
| Mailchimp | 10 req/sec | `MAILCHIMP_UPSERT_DELAY=0.05` |

### Performance Benchmarks

| Operation | Speed | Notes |
|-----------|-------|-------|
| Plan generation | ~0.5s/contact | Includes HubSpot + Mailchimp API calls |
| Apply execution | ~1.15s/contact | Batch of 20 contacts = 23 seconds |
| Archival reconciliation | ~100 members/sec | Paginated scan (1000/page) |

**Recommended:** For large syncs (500+ contacts), run during off-peak hours.

---

## \ud83d\udcdd Configuration Files

### Production Config Location

`corev2/config/production.yaml` - **NEVER COMMIT SECRETS**

### Environment Variables (.env)

```bash
# HubSpot
HUBSPOT_PRIVATE_APP_TOKEN=pat-eu1-xxx

# Mailchimp
MAILCHIMP_API_KEY=xxx-us22
MAILCHIMP_LIST_ID=xxx
MAILCHIMP_DC=us22

# Optional: Teams notifications
TEAMS_WEBHOOK_URL=https://...
```

---

## \ud83d\udcca Monitoring

### Daily Health Check

```bash
# Run plan (no apply) to detect issues
python -m corev2.cli plan --config production.yaml --output health_check.json

# Check for anomalies
$plan = Get-Content health_check.json | ConvertFrom-Json
if ($plan.summary.contacts_with_operations -gt 1000) {
    Write-Warning \"Unusually high operation count - investigate\"
}
```

### Execution Journal Alerts

Monitor for:
- Failed operations: `result.success == false`
- Strict mode skips: `STRICT MODE` in logs
- High archive counts: `archive_mc_member > 50`

---

## \ud83d\udd12 Security & Compliance

- **Data Residency:** HubSpot EU, Mailchimp US (check GDPR requirements)
- **API Keys:** Store in `.env`, NEVER in git
- **Audit Trail:** `execution_journal.jsonl` is append-only log
- **Compliance:** Lists 762 (Unsubscribed) and 773 (Manual Disengagement) NEVER synced (INV-002)

---

## \ud83d\udd17 References

- SPEC.md - Invariants and verification log
- corev2/config/schema.py - Configuration schema
- execution_journal.jsonl - Operation audit trail
- SYSTEM_OVERVIEW_V2_PLANNING.md - Architecture details
