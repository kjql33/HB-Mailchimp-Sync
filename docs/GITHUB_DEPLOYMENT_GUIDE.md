# 🚀 GitHub Deployment Guide - corev2

**Date:** March 3, 2026 (updated April 2, 2026)  
**Purpose:** Complete guide for deploying corev2 to GitHub Actions with automated scheduling

---

## 📋 Prerequisites

### Required GitHub Secrets

Navigate to: **Repository → Settings → Secrets and variables → Actions**

Add these secrets:

| Secret Name | Description | Where to Find |
|------------|-------------|---------------|
| `HUBSPOT_PRIVATE_APP_TOKEN` | HubSpot Private App API token | HubSpot → Settings → Integrations → Private Apps |
| `MAILCHIMP_API_KEY` | Mailchimp API key | Mailchimp → Profile → Extras → API keys |
| `MAILCHIMP_AUDIENCE_ID` | Mailchimp audience/list ID | Mailchimp → Audience → Settings → Unique ID |
| `MAILCHIMP_DC` | Mailchimp data center (e.g., `us22`) | Extract from your Mailchimp API key or account URL |
| `TEAMS_WEBHOOK_URL` | Microsoft Teams webhook for alerts | Teams → Channel → Connectors → Incoming Webhook |

---

## 🔧 GitHub Actions Workflow Configuration

**File:** `.github/workflows/sync.yml`

### Schedule
- **Frequency:** Every 8 hours (00:00, 08:00, 16:00 UTC)
- **Cron:** `0 */8 * * *`
- **Manual trigger:** Available via Actions tab → "Run workflow"

### Workflow Steps

1. **Checkout code** - Pulls latest from repository
2. **Setup Python 3.13** - Installs Python environment
3. **Install dependencies** - Runs `pip install -r requirements-v2.txt`
4. **Debug environment** - Validates corev2 module import
5. **Generate plan** - Creates operations plan (dry-run)
   ```bash
   python -m corev2.cli plan \
     --config corev2/config/production.yaml \
     --output corev2/artifacts/plan_TIMESTAMP.json
   ```
6. **Apply plan** - Executes operations
   ```bash
   python -m corev2.cli apply \
     --plan corev2/artifacts/plan_TIMESTAMP.json
   ```
7. **Upload artifacts on failure** - Saves logs for debugging (7 day retention)

---

## 📂 What's Tracked in Git

### ✅ Included (Committed)
- `corev2/` - All source code
- `docs/` - Documentation (plans, guides, contracts)
- `requirements.txt` - Python dependencies
- `corev2/config/production.yaml` - Configuration (with `${ENV_VAR}` placeholders)
- `.github/workflows/sync.yml` - GitHub Actions workflow
- `README.md` - Project documentation

### ❌ Excluded (.gitignore)
- `.env` - Sensitive credentials (NEVER commit)
- `logs/` - Execution logs (generated at runtime)
- `raw_data/metadata/` - HubSpot/Mailchimp snapshots
- `raw_data/snapshots/` - Historical data
- `system_testing/` - Test results and audit files
- `__pycache__/` - Python bytecode
- `.venv/` - Virtual environment

---

## 🔐 Security Best Practices

### 1. Environment Variables
All sensitive data uses environment variable substitution in `production.yaml`:

```yaml
hubspot:
  api_key: ${HUBSPOT_PRIVATE_APP_TOKEN}  # ✅ Safe for git
```

### 2. Never Commit
- ❌ API keys directly in YAML files
- ❌ `.env` files with real credentials
- ❌ Local test scripts with hardcoded tokens
- ❌ Execution logs containing contact data

### 3. GitHub Secrets Scope
- Secrets are **encrypted** at rest
- Only accessible during workflow execution
- Not visible in logs (masked automatically)
- Separate from repository code

---

## 🚦 Deployment Checklist

### Step 1: Verify Secrets
```bash
# Check GitHub Secrets are configured (do this in GitHub UI)
Repository → Settings → Secrets and variables → Actions

Required:
✅ HUBSPOT_PRIVATE_APP_TOKEN
✅ MAILCHIMP_API_KEY  
✅ MAILCHIMP_AUDIENCE_ID
✅ MAILCHIMP_DC
✅ TEAMS_WEBHOOK_URL
```

### Step 2: Update Workflow (Already Done)
- [x] Changed schedule to every 8 hours
- [x] Updated to use corev2 (not main.py)
- [x] Using Python 3.13
- [x] Plan → Apply workflow
- [x] Artifact upload on failure

### Step 3: Stage and Commit Changes
```powershell
# From workspace root
git add -A
git commit -m "feat: Deploy corev2 with 8-hour automated sync

- Updated workflow to run every 8 hours (00:00, 08:00, 16:00 UTC)
- Switched from v1 main.py to corev2 plan+apply workflow
- Added SECONDARY_SYNC_PLAN.md and AGENT_BRIEF_HUBSPOT_EMAIL_SENDER.md
- Updated Python to 3.13
- Improved artifact collection on failure
- Ready for production deployment"
```

### Step 4: Push to GitHub
```powershell
# Force push to override old content if needed
git push origin main
```

### Step 5: Enable Workflow
1. Go to **Actions** tab in GitHub
2. Select "HubSpot↔Mailchimp Bidirectional Sync (V2)"
3. Click "Enable workflow" if disabled
4. **Test:** Click "Run workflow" → "Run workflow"
5. Monitor execution in real-time

---

## 📊 Monitoring & Logs

### Viewing Workflow Runs
1. Navigate to **Actions** tab
2. Click on specific workflow run
3. Expand steps to see detailed logs
4. Download artifacts if run failed

### Log Files (If Failed)
Artifacts available for 7 days:
- `/tmp/plan_output.log` - Plan generation logs
- `/tmp/apply_output.log` - Apply execution logs
- `corev2/artifacts/plan_*.json` - Operations plan

### Success Indicators
```
✅ corev2 import successful
📋 Generating operations plan...
📄 Plan saved to: corev2/artifacts/plan_TIMESTAMP.json
⚙️ Applying operations plan...
✅ corev2 sync completed successfully in XX seconds
```

---

## 🐛 Troubleshooting

### Issue: "Environment variable not set"
**Cause:** Missing GitHub Secret  
**Fix:** Add the missing secret in Repository Settings → Secrets

### Issue: "Module corev2 not found"
**Cause:** requirements.txt not installed properly  
**Fix:** Check step "Install dependencies" succeeded

### Issue: "Plan generation failed"
**Cause:** Invalid configuration or API credentials  
**Fix:**
1. Check secrets are correct
2. Validate `production.yaml` syntax
3. Test API credentials locally first

### Issue: "Rate limit exceeded"
**Cause:** Too many API calls in short time  
**Fix:**
- Check schedule (8 hours should be safe)
- Reduce contact_limit in config for testing
- Wait 10 minutes and retry

---

## 🔄 Updating the Sync

### Local Development Workflow
1. Make changes locally in corev2/
2. Test with: `python -m corev2.cli plan --config corev2/config/production.yaml`
3. Commit changes: `git commit -am "fix: description"`
4. Push to GitHub: `git push origin branch-name`
5. GitHub Actions automatically runs on next schedule

### Configuration Changes
Edit `corev2/config/production.yaml`:
- Add/remove HubSpot lists
- Modify exclusion rules
- Adjust archival settings
- Commit and push changes

### Emergency Stop
1. Go to Repository → Settings → Actions → General
2. Click "Disable Actions" temporarily
3. Or delete `.github/workflows/sync.yml` from main branch

---

## 📈 Performance Metrics

**Expected Runtime (4,200 contacts):**
- Plan generation: ~3-5 minutes
- Apply execution: ~10-15 minutes
- **Total:** ~15-20 minutes per sync

**API Rate Limits:**
- HubSpot: 100 requests/10 seconds (safe with pagination delays)
- Mailchimp: 10 requests/second (batched operations)

**Schedule Coverage:**
- 8-hour intervals = 3 syncs per day
- Good balance: frequent enough to catch deal changes, infrequent enough to avoid rate limits

---

## ✅ Success Criteria

Your deployment is successful when:

1. ✅ All GitHub Secrets configured
2. ✅ Workflow runs without errors
3. ✅ Plan generated with expected contact counts
4. ✅ Operations applied successfully
5. ✅ John Dale (and similar cases) archived when moved to List 717
6. ✅ Logs show no rate limit errors
7. ✅ Sync completes in <20 minutes

---

## 🆘 Support

**Documentation:**
- [SECONDARY_SYNC_PLAN.md](./SECONDARY_SYNC_PLAN.md) - Future secondary sync implementation
- [AGENT_BRIEF_HUBSPOT_EMAIL_SENDER.md](./AGENT_BRIEF_HUBSPOT_EMAIL_SENDER.md) - Email sending research
- [README.md](../README.md) - System overview

**Common Commands:**
```powershell
# Local testing with real credentials
python -m corev2.cli plan --config corev2/config/production.yaml

# Test with specific contact
python -m corev2.cli plan --config corev2/config/production.yaml --only-email johndale@kwuk.com

# Validate configuration
python -c "from corev2.config import load_config; load_config('corev2/config/production.yaml')"
```

---

**Last Updated:** March 3, 2026  
**Status:** ✅ Ready for Production Deployment
