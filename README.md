# HubSpot → Mailchimp Sync Agent

# HubSpot → Mailchimp Sync Agent
A Python-based integration that synchronizes contacts from a HubSpot Active List to a Mailchimp Audience using the HubSpot CRM v3 Lists API. This tool automates the process of keeping your marketing lists in sync, ensuring consistent communication across platforms.

## Setup Instructions

1. Clone this repository
2. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On macOS/Linux
   # OR
   .venv\Scripts\activate  # On Windows
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Create a `.env` file with the following environment variables (required):
   ```bash
   # HubSpot API
   HUBSPOT_PRIVATE_TOKEN=your_hubspot_private_app_token
   # Comma-separated list of HubSpot list IDs to sync (e.g., "123,456"); falls back to single HUBSPOT_LIST_ID
   HUBSPOT_LIST_IDS=your_hubspot_list_id[,another_list_id,...]

   # Mailchimp API
   MAILCHIMP_API_KEY=your_mailchimp_api_key
   MAILCHIMP_LIST_ID=your_mailchimp_audience_id
   MAILCHIMP_DC=your_mailchimp_data_center  # e.g., us20

   # Optional sync parameters (defaults provided in config.py)
   PAGE_SIZE=20               # Number of records per HubSpot page
   TEST_CONTACT_LIMIT=0       # 0 = no limit; >0 caps total contacts per list
   MAX_RETRIES=3              # Number of retry attempts for API calls
   RETRY_DELAY=2              # Delay (seconds) between retries
   REQUIRED_TAGS=COMPANY,CITY,INDUSTRY,PHONE,FNAME,LNAME
   LOG_LEVEL=INFO             # Logging verbosity (DEBUG, INFO, WARNING, ERROR)
   ```
5. Run the sync script:
   ```bash
   python sync.py
   ```

## Deployment

This tool supports syncing multiple HubSpot lists. You can schedule it to run regularly using GitHub Actions, cron jobs, or other schedulers. Create a `.github/workflows/daily_sync.yml` workflow or configure your own automation to invoke:
```bash
python sync.py
```

## Sync Behavior Overview

This agent keeps your Mailchimp audience in lock-step with one or more HubSpot lists:

### 1. Single-List Sync (Micro-Level)
- Fetch HubSpot list memberships (with paging and optional TEST_CONTACT_LIMIT).
- Batch-read contact details; skip any without email.
- PUT to Mailchimp to subscribe/unarchive, update merge-fields, then apply the HubSpot list name as a tag.
- Untag (inactive) any Mailchimp members that still have this list’s tag but are no longer in the HubSpot list.

### 2. Multi-List Sync (Macro-Level)
- Loop over each `HUBSPOT_LIST_IDS` entry:
  - Set `MAILCHIMP_TAG` to the list’s human-readable name.
  - Run the single-list sync steps and collect all synced emails into a global set.
- After all lists, fetch every Mailchimp member email and archive (DELETE) those not in any HubSpot list.

### 3. Merge-Field Management
- The `REQUIRED_TAGS` in `config.py` drive which audience fields to enforce.
- Missing merge-fields are auto-created via the Mailchimp API before syncing.

### 4. Configuration & Retries
- All behavior is driven by `config.py`:
  - `HUBSPOT_LIST_IDS`, `PAGE_SIZE`, `TEST_CONTACT_LIMIT`, `MAX_RETRIES`, `RETRY_DELAY`, `REQUIRED_TAGS`, Mailchimp credentials, and log level.
- API calls retry up to `MAX_RETRIES` with backoff of `RETRY_DELAY` seconds.

### 5. Logging & Error Handling
- Detailed logs to both console and `sync.log` (configurable via `LOG_LEVEL`).
- HubSpot API errors abort only the current list; Mailchimp untag/archive errors are logged but do not stop the full run.
- Contacts missing an email are skipped with warnings.

### 6. Edge Cases
- Archived or unsubscribed Mailchimp contacts will be resurrected if they reappear in HubSpot.
- Contacts in multiple HubSpot lists end up with multiple tags in Mailchimp.
- Use `TEST_CONTACT_LIMIT` to cap contacts per list for safe testing.
