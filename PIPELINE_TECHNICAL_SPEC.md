# HubSpot ↔ Mailchimp Sync Pipeline — Technical Specification

**Prepared for:** Yogesh B. (Migration Assessment)
**Date:** 27 March 2026
**Repository:** https://github.com/kjql33/HB-Mailchimp-Sync.git (branch: `main`)
**Runtime:** GitHub Actions (Ubuntu, Python 3.13) — scheduled every 8 hours

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [Authentication & Credentials](#3-authentication--credentials)
4. [HubSpot List Structure](#4-hubspot-list-structure)
5. [Mailchimp Audience Structure](#5-mailchimp-audience-structure)
6. [Sync Flow — Step by Step](#6-sync-flow--step-by-step)
7. [All Mailchimp API Endpoints Used](#7-all-mailchimp-api-endpoints-used)
8. [All HubSpot API Endpoints Used](#8-all-hubspot-api-endpoints-used)
9. [Operation Types](#9-operation-types)
10. [Business Rules & Priority Logic](#10-business-rules--priority-logic)
11. [Rate Limiting & Resilience](#11-rate-limiting--resilience)
12. [Configuration Schema](#12-configuration-schema)
13. [Data Model & Contact Lifecycle](#13-data-model--contact-lifecycle)
14. [Current Scale](#14-current-scale)
15. [Migration Considerations](#15-migration-considerations)

---

## 1. System Overview

This is a **bidirectional sync pipeline** between HubSpot (CRM) and Mailchimp (email marketing). It runs as an automated Python process on GitHub Actions every 8 hours.

**What it does in plain terms:**

- **Primary Sync (HubSpot → Mailchimp):** Takes contacts from HubSpot lists and creates/updates them in Mailchimp with the correct tag (e.g. "General", "Recruitment", "Sanctioned"). Each contact gets exactly ONE tag based on which HubSpot list they belong to.

- **Secondary Sync (Mailchimp → HubSpot):** When a contact is tagged with an exit tag in Mailchimp (e.g. "General Finished"), the system moves them to a handover list in HubSpot, removes them from their source list (if static), strips all tags, and archives them in Mailchimp.

- **Unsubscribe Sync (Mailchimp → HubSpot):** When someone unsubscribes in Mailchimp, the system opts them out of all communication preferences in HubSpot and cleans up associated company records.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     GitHub Actions (cron: every 8h)             │
│                     ubuntu-latest, Python 3.13                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  main.py                                                        │
│  ├── STEP 1: Unsubscribe Sync (MC → HS)                       │
│  │   └── Scan MC unsubscribes → opt out in HS                  │
│  │                                                              │
│  ├── STEP 2: Generate Primary Sync Plan                        │
│  │   └── Scan HS lists → determine tags → diff vs MC           │
│  │                                                              │
│  ├── STEP 3: Execute Primary Sync (HS → MC)                   │
│  │   └── Upsert members, apply/remove tags, archive orphans    │
│  │                                                              │
│  └── STEP 4: Secondary Sync (MC → HS)                         │
│      └── Scan MC exit tags → move to HS handover lists         │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  corev2/                                                        │
│  ├── config/          Config loader + schema (Pydantic)         │
│  ├── clients/         HTTP clients (aiohttp, rate-limited)      │
│  │   ├── hubspot_client.py                                      │
│  │   ├── mailchimp_client.py                                    │
│  │   └── http_base.py     (retry, circuit breaker, rate limit)  │
│  ├── planner/                                                   │
│  │   ├── primary.py       Primary sync plan generator           │
│  │   └── secondary.py     Secondary sync plan generator         │
│  ├── executor/                                                  │
│  │   └── engine.py        Executes all operation types          │
│  └── sync/                                                      │
│      └── unsubscribe_sync.py   MC→HS unsubscribe handler       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
         │                                    │
         ▼                                    ▼
┌─────────────────┐                ┌─────────────────────┐
│    HubSpot      │                │     Mailchimp       │
│    (CRM)        │                │  (Email Marketing)  │
│                 │                │                     │
│  6 Import Lists │◄──────────────►│  1 Audience         │
│  6 Handover     │   Bidirectional│  Tags = segments    │
│  3 Exclusion    │      Sync      │  All contacts in    │
│                 │                │  single list        │
└─────────────────┘                └─────────────────────┘
```

---

## 3. Authentication & Credentials

### Mailchimp
- **Method:** HTTP Basic Auth
- **Header:** `Authorization: Basic base64("anystring:{api_key}")`
- **Base URL:** `https://{server_prefix}.api.mailchimp.com/3.0`
- **Server prefix:** `us22` (derived from API key suffix)
- **Audience ID:** Single audience (stored as `MAILCHIMP_LIST_ID` / `MAILCHIMP_AUDIENCE_ID`)

### HubSpot
- **Method:** Bearer Token (Private App)
- **Header:** `Authorization: Bearer {token}`
- **Base URL:** `https://api.hubapi.com`

### Environment Variables Required
| Variable | Purpose |
|---|---|
| `HUBSPOT_PRIVATE_APP_TOKEN` | HubSpot Private App token |
| `MAILCHIMP_API_KEY` | Mailchimp API key (includes server prefix after `-`) |
| `MAILCHIMP_LIST_ID` | Mailchimp audience/list ID |
| `MAILCHIMP_DC` | Mailchimp data centre prefix (e.g. `us22`) |

---

## 4. HubSpot List Structure

### Import Lists (Source — contacts enter here)

These are the lists that feed contacts INTO the email marketing platform.

| List ID | Name | Type | Current Size | Mailchimp Tag | Priority |
|---|---|---|---|---|---|
| 969 | Sanctioned | MANUAL | 45 | `Sanctioned` | 1 (highest) |
| 719 | Recruitment | MANUAL | 54 | `Recruitment` | 2 |
| 720 | Competition | MANUAL | 26 | `Competition` | 3 |
| 989 | Network Agents | DYNAMIC | 18 | `EXP` | 4 |
| 945 | New Agents | MANUAL | 4 | `New agents` | 5 |
| 987 | General | DYNAMIC | 875 | `General` | 6 (lowest, catch-all) |

**Priority rule:** If a contact is in multiple import lists, they get tagged with the HIGHEST priority list's tag only. A contact in both Recruitment (719) and General (987) gets the tag "Recruitment", not "General".

**Dynamic vs Manual:**
- **DYNAMIC lists** (987, 989): HubSpot auto-manages membership based on filter criteria. Contacts cannot be manually added/removed.
- **MANUAL lists** (719, 720, 945, 969): Contacts are manually added/removed by staff.

### Handover/Exit Lists (Destination — contacts move here when finished)

| List ID | Name | Type | Current Size | Receives From |
|---|---|---|---|---|
| 946 | General Handover | MANUAL | 2,821 | General Finished tag |
| 947 | Recruitment Handover | MANUAL | 35 | Recruitment Finished tag |
| 948 | Competition Handover | MANUAL | 18 | Competition Finished tag |
| 949 | New Agents Handover | MANUAL | 25 | New Agents Finished tag |
| 1005 | Sub Agents Handover | MANUAL | 325 | Sub Agents Finished tag |
| 1006 | Sanctioned Handover | MANUAL | 59 | Sanctioned Finished tag |

### Exclusion Lists (contacts in these are excluded from ALL marketing)

| List ID | Name | Type | Current Size | Purpose |
|---|---|---|---|---|
| 762 | Unsubscribed / Opted Out | DYNAMIC | 411 | Auto-populated when contacts opt out |
| 773 | Manual Exclusion from MC | MANUAL | 2 | Manual disengagement |
| 717 | All Active Deals | DYNAMIC | 167 | Contacts with open sales deals |

### Dynamic List Filter Example — General (987)

The General list is a DYNAMIC list with the following filter:
```
(IN list 651 "T2 Director Name Company Email - Chennai"
 OR IN list 180 "Apollo list 1100 Decision Makers"
 OR IN list 970 "Directors Found"
 OR IN list 988 "T1 (NON App) Director Name & Email - UK"
 OR IN list 991 "Apollo 2026")
AND
(NOT IN list 989 "Network Agents"
 AND NOT IN list 945 "New Agents"
 AND NOT IN list 969 "Sanctioned"
 AND NOT IN list 719 "Recruitment"
 AND NOT IN list 720 "Competition")
```

This means General is a "catch-all" — anyone in the source data lists who isn't specifically categorised into a higher-priority list.

---

## 5. Mailchimp Audience Structure

### Single Audience Model
All contacts live in **one Mailchimp audience**. Segmentation is done entirely via **tags**.

### Tags Used

**Import tags (applied by primary sync):**
| Tag | Applied When |
|---|---|
| `General` | Contact is in General list (987) and no higher-priority list |
| `Recruitment` | Contact is in Recruitment list (719) |
| `Competition` | Contact is in Competition list (720) |
| `Sanctioned` | Contact is in Sanctioned list (969) |
| `New agents` | Contact is in New Agents list (945) |
| `EXP` | Contact is in Network Agents list (989) |

**Exit tags (applied manually by staff in Mailchimp to trigger secondary sync):**
| Tag | Triggers |
|---|---|
| `General Finished` | Move to General Handover (946) |
| `Recruitment Finished` | Move to Recruitment Handover (947) |
| `Competition Finished` | Move to Competition Handover (948) |
| `Sub Agents Finished` | Move to Sub Agents Handover (1005) |
| `New Agents Finished` | Move to New Agents Handover (949) |
| `Sanctioned Finished` | Move to Sanctioned Handover (1006) |

**Single-tag enforcement:** Each contact has exactly ONE import tag at any time. When the tag changes (e.g. contact moves from General to Recruitment in HubSpot), the old tag is removed and the new one applied.

### Contact Statuses in Mailchimp
| Status | Meaning |
|---|---|
| `subscribed` | Active, receiving emails |
| `unsubscribed` | Opted out (synced back to HubSpot) |
| `cleaned` | Bounced/invalid email (Mailchimp auto-manages) |
| `archived` | Removed from active audience (our system archives after exit processing) |

---

## 6. Sync Flow — Step by Step

### STEP 1: Unsubscribe Sync (Mailchimp → HubSpot)

**Purpose:** Ensure anyone who unsubscribes in Mailchimp is also opted out in HubSpot.

```
1. Scan entire Mailchimp audience for members with status="unsubscribed"
2. For each unsubscribed contact:
   a. Look up contact in HubSpot by email
   b. Get their HubSpot communication preferences
   c. If not already opted out → call HubSpot Communication Preferences API
      to unsubscribe from ALL subscription types
   d. Clean up associated company records (remove matching email/phone from company)
```

**HubSpot Subscription Types managed:**
| ID | Name |
|---|---|
| 289137114 | One to One |
| 289137112 | Marketing Information |

### STEP 2 + 3: Primary Sync (HubSpot → Mailchimp)

**Purpose:** Keep Mailchimp in sync with HubSpot list memberships.

```
Phase 1 — Scan & Aggregate:
1. For each of the 6 import lists, paginate through all members via HubSpot Lists API
2. For each exclusion list (762, 773, 717), paginate and collect member IDs
3. Build a map: {email → set of list IDs they belong to}

Phase 2 — Determine Target Tag:
4. For each contact, apply the exclusion matrix:
   - If contact is in any exclusion list → SKIP (no tag, no sync)
5. Apply priority order across lists:
   - Check Sanctioned first, then Recruitment, Competition, Network Agents, New Agents, General
   - First matching list → that's the tag
6. Single-tag enforcement (INV-004a):
   - If contact already has a tag in Mailchimp, and it's a valid import tag, KEEP IT
   - This prevents "campaign switching" — a contact stays on their original tag

Phase 3 — Generate Operations:
7. For each contact that needs changes:
   a. upsert_mc_member: Create or update in Mailchimp (subscribe if new)
   b. remove_mc_tag: Remove any old/wrong tags
   c. apply_mc_tag: Apply the correct tag
   d. update_hs_property: Write "ORI_LISTS" property back to HubSpot

Phase 4 — Archival Reconciliation:
8. For contacts in exclusion lists who exist in Mailchimp:
   a. archive_mc_member: Remove from active audience
   b. remove_hs_from_list: Remove from HubSpot import lists (manual lists only)
```

### STEP 4: Secondary Sync (Mailchimp → HubSpot)

**Purpose:** Process contacts tagged with "Finished" exit tags — move them out.

```
Phase 1 — Scan Mailchimp:
1. Paginate through entire Mailchimp audience
2. Find contacts with any of the 6 exit tags
3. Skip contacts with status "cleaned" or "archived" (already processed)

Phase 2 — Generate & Execute Operations:
4. For each exit-tagged contact:
   a. Look up in HubSpot by email → get contact VID
   b. add_hs_to_list: Add to the correct handover/destination list
   c. remove_hs_from_list: Remove from source list (ONLY for manual/static lists)
      - Lists 719, 720, 945, 969 → manual removal required
      - Lists 987, 989 → dynamic, auto-excluded by filter, no removal needed
   d. remove_mc_tag: Remove ALL tags (clean slate before archive)
   e. archive_mc_member: Archive from Mailchimp (journey complete)
```

---

## 7. All Mailchimp API Endpoints Used

| # | Method | Endpoint | Purpose | Request Body | Response |
|---|---|---|---|---|---|
| 1 | `GET` | `/lists/{audience_id}/members/{subscriber_hash}` | Get single member by email | — | `{status, tags, merge_fields, email_address}` |
| 2 | `PUT` | `/lists/{audience_id}/members/{subscriber_hash}` | Upsert (create or update) member | `{email_address, status_if_new: "subscribed", merge_fields}` | `{status, email_address}` |
| 3 | `PATCH` | `/lists/{audience_id}/members/{subscriber_hash}` | Update member fields or status | `{merge_fields}` or `{status: "unsubscribed"}` | `{status}` |
| 4 | `POST` | `/lists/{audience_id}/members/{subscriber_hash}/tags` | Add tags | `{tags: [{name: "General", status: "active"}]}` | 204 |
| 5 | `POST` | `/lists/{audience_id}/members/{subscriber_hash}/tags` | Remove tags | `{tags: [{name: "General", status: "inactive"}]}` | 204 |
| 6 | `DELETE` | `/lists/{audience_id}/members/{subscriber_hash}` | Archive (delete) member | — | 204 (404 = already archived) |
| 7 | `GET` | `/lists/{audience_id}/members` | List all members (paginated) | `?count=1000&offset=N` | `{members: [...]}` |

**`subscriber_hash`** = `MD5(email.lower())`

**Key behaviors:**
- `PUT` upsert: creates if not exists, updates if exists. `status_if_new: "subscribed"` only applies for new contacts.
- Tag operations are idempotent — adding a tag that already exists is a no-op.
- Archive (`DELETE`) is idempotent — 404 means already archived, treated as success.
- When restoring an archived contact via `PUT`, the system removes all old tags first to prevent stale tag accumulation.

---

## 8. All HubSpot API Endpoints Used

| # | Method | Endpoint | Purpose | Request Body | Response |
|---|---|---|---|---|---|
| 1 | `GET` | `/crm/v3/lists/{list_id}/memberships` | Get list members (paginated, cursor) | `?limit=100&after={cursor}` | `{results: [{recordId}], paging: {next: {after}}}` |
| 2 | `GET` | `/crm/v3/objects/contacts/{record_id}` | Get contact by ID | `?properties=email,firstname,lastname` | `{properties: {email, ...}}` |
| 3 | `GET` | `/contacts/v1/contact/email/{email}/profile` | Lookup contact by email | `?property=email&property=firstname&...` | `{vid, properties: {...}}` |
| 4 | `PUT` | `/crm/v3/lists/{list_id}/memberships/add` | Add contact to list | `["{vid}"]` (JSON array) | 200/204 |
| 5 | `PUT` | `/crm/v3/lists/{list_id}/memberships/remove` | Remove contact from list | `["{vid}"]` (JSON array) | 200/204 |
| 6 | `POST` | `/contacts/v1/contact/vid/{vid}/profile` | Update contact property | `{properties: [{property, value}]}` | 200 |
| 7 | `GET` | `/communication-preferences/v3/status/email/{email}` | Get subscription statuses | — | `{subscriptionStatuses: [{id, name, status}]}` |
| 8 | `POST` | `/communication-preferences/v3/unsubscribe` | Unsubscribe from type | `{emailAddress, subscriptionId, legalBasis, legalBasisExplanation}` | 200 |
| 9 | `GET` | `/crm/v3/objects/contacts/{vid}/associations/companies` | Get company associations | — | `{results: [{id}]}` |
| 10 | `GET` | `/crm/v3/objects/companies/{id}` | Get company details | `?properties=name,email,phone` | `{properties: {...}}` |
| 11 | `PATCH` | `/crm/v3/objects/companies/{id}` | Clear company fields | `{properties: {email: "", phone: ""}}` | 200 |

---

## 9. Operation Types

The executor handles these discrete operation types:

| Operation | Direction | API | What it does |
|---|---|---|---|
| `upsert_mc_member` | → Mailchimp | `PUT /members/{hash}` | Create or update contact in Mailchimp. Sets `status_if_new: "subscribed"`. |
| `apply_mc_tag` | → Mailchimp | `POST /members/{hash}/tags` | Add a tag to a contact (e.g. "General"). |
| `remove_mc_tag` | → Mailchimp | `POST /members/{hash}/tags` | Remove tag(s) from a contact. Supports single or multiple tags. |
| `unsubscribe_mc_member` | → Mailchimp | `PATCH /members/{hash}` | Set status to "unsubscribed". |
| `archive_mc_member` | → Mailchimp | `DELETE /members/{hash}` | Archive (delete) member. 404 = already done. |
| `update_hs_property` | → HubSpot | `POST /contacts/v1/.../profile` | Write ORI_LISTS property to HubSpot contact. |
| `add_hs_to_list` | → HubSpot | `PUT /lists/{id}/memberships/add` | Add contact to a HubSpot list. "Already in list" = success. |
| `remove_hs_from_list` | → HubSpot | `PUT /lists/{id}/memberships/remove` | Remove contact from a HubSpot list. 404 = already removed. |

---

## 10. Business Rules & Priority Logic

### Rule 1: Exclusion Matrix (INV-001)
Contacts in any exclusion list (762 Unsub, 773 Disengage, 717 Active Deals) are **excluded from ALL marketing sync**. They are not tagged, not synced, and if they exist in Mailchimp they are archived.

### Rule 2: Single-Tag Enforcement (INV-004)
Each contact gets exactly **one** import tag. Priority order determines which:
1. Sanctioned (highest)
2. Recruitment
3. Competition
4. Network Agents (tagged as "EXP")
5. New Agents
6. General (lowest, catch-all)

### Rule 3: First-Tag Priority (INV-004a)
If a contact already has a valid import tag in Mailchimp, **keep it**. Don't switch tags even if HubSpot list membership changes. This prevents disrupting ongoing campaigns.

### Rule 4: Anti-Remarketing
When a contact receives an exit tag (e.g. "General Finished"):
1. They are added to the correct handover list in HubSpot
2. They are removed from their source import list (if it's a manual/static list)
3. All Mailchimp tags are stripped
4. They are archived in Mailchimp
5. They will **never be re-synced** because the handover list is not in the import list set

### Rule 5: Dynamic List Auto-Exclusion
Dynamic lists (987 General, 989 Network Agents) have filter criteria that automatically exclude contacts in handover lists. No manual removal needed — HubSpot handles it.

---

## 11. Rate Limiting & Resilience

### Rate Limiting (Token Bucket)
| Platform | Rate | Burst Capacity |
|---|---|---|
| Mailchimp | 10 requests/sec | 20 (2× rate) |
| HubSpot | 10 requests/sec (configured) | 20 |

### Retry with Exponential Backoff
- **Max retries:** 5
- **Backoff:** 1s → 2s → 4s → 8s → 16s → 32s (capped)
- **Jitter:** ±20% randomization
- **429 (rate limit):** Respects `Retry-After` header if present
- **5xx errors:** Retried with backoff
- **4xx errors (non-429):** NOT retried, raised immediately

### Circuit Breaker
- **Threshold:** 5 consecutive failures → circuit OPEN (stops all requests)
- **Timeout:** 60 seconds → HALF_OPEN (allows 1 test request)
- **Recovery:** If test request succeeds → CLOSED (normal operation resumes)

---

## 12. Configuration Schema

The system is configured via YAML (`corev2/config/production.yaml`) with environment variable substitution (`${ENV_VAR}`).

### Key Config Sections

**HubSpot:**
- API key, list definitions (grouped into 3 tiers), exclusion list IDs, supplemental tags

**Mailchimp:**
- API key, server prefix, audience ID

**Sync:**
- Batch size (100), tag prefix (""), ORI_LISTS field name, force_subscribe (true)

**Exclusion Matrix:**
- Three tiers of lists, each with their own exclusion criteria
- INV-002 validator ensures lists 762 and 773 appear in every tier's exclusions

**Secondary Sync:**
- Enabled/disabled, archive after sync, contact limit, 6 exit tag → handover list mappings

**Safety:**
- Run mode (test/dry-run/prod), allow_apply, allow_archive, test_contact_limit, enable_hubspot_writes (ORI_LISTS)

---

## 13. Data Model & Contact Lifecycle

### Contact Journey

```
                    ┌──────────────┐
                    │  New Contact  │
                    │ (added to HS  │
                    │  import list) │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ Primary Sync  │  Every 8 hours
                    │ HS → MC       │
                    │               │
                    │ • Upsert in MC│
                    │ • Apply tag   │
                    │ • Write ORI   │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ Active in MC  │  Receiving campaigns
                    │ (subscribed)  │  based on tag
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
     ┌────────▼───┐  ┌─────▼─────┐  ┌──▼──────────┐
     │ Unsubscribe│  │ Exit Tag  │  │ Exclusion   │
     │ in MC      │  │ Applied   │  │ List added  │
     └────────┬───┘  │ by staff  │  │ (762/773/717│
              │      └─────┬─────┘  └──┬──────────┘
              │            │           │
     ┌────────▼───┐  ┌─────▼─────┐  ┌──▼──────────┐
     │Step 1 Sync │  │Step 4 Sync│  │Step 2-3     │
     │MC → HS     │  │MC → HS    │  │Reconcile    │
     │            │  │           │  │             │
     │• Opt out   │  │• Add to   │  │• Archive MC │
     │  in HS     │  │  handover │  │• Remove from│
     │• Clean co. │  │• Remove   │  │  HS lists   │
     └────────────┘  │  from src │  └─────────────┘
                     │• Strip tag│
                     │• Archive  │
                     └───────────┘
```

### Contact Data Stored in Mailchimp

| Field | Source | Purpose |
|---|---|---|
| `email_address` | HubSpot contact email | Primary identifier |
| `status` | Managed by system | subscribed/unsubscribed/cleaned/archived |
| `tags` | Sync pipeline | Segmentation (one import tag per contact) |
| `merge_fields` | HubSpot properties | FNAME, LNAME (if configured) |

### Contact Properties Used in HubSpot

| Property | Purpose |
|---|---|
| `email` | Contact identifier |
| `firstname` / `lastname` | Synced to Mailchimp merge fields |
| `phone` | Cleaned from company records on unsubscribe |
| `ORI_LISTS` | Written by sync — records which import lists contact belongs to |
| `hs_email_optout` | Used by dynamic exclusion list 762 |

---

## 14. Current Scale

| Metric | Count |
|---|---|
| Total contacts in import lists | ~1,022 |
| Total contacts in handover lists | ~3,283 |
| Total contacts in exclusion lists | ~580 |
| Mailchimp active (non-archived) audience | ~600 |
| Mailchimp archived (processed exits) | ~3,000+ |
| Operations per primary sync run | ~8,000 |
| Operations per secondary sync run | Varies (0 if no new exit tags) |
| API calls per full run | ~10,000–15,000 |
| Run frequency | Every 8 hours (0:00, 8:00, 16:00 UTC) |
| Typical run duration | ~10-15 minutes |

---

## 15. Migration Considerations

### What the replacement platform MUST support (API equivalents needed)

**Contact management:**
- Create/update contacts by email (upsert semantics)
- Get contact by email
- List all contacts with pagination
- Archive/delete contacts
- Get contact status (subscribed, unsubscribed, etc.)

**Tagging / Segmentation:**
- Add tags to contacts
- Remove tags from contacts
- Query contacts by tag
- Tags must support arbitrary string names

**Authentication:**
- API key or token-based auth
- Must support programmatic access from Python/GitHub Actions

**Webhook or polling:**
- Ability to detect unsubscribes (either via polling status or webhooks)

### What does NOT need to change
- All HubSpot API calls remain identical
- List structure in HubSpot stays the same
- Priority logic, exclusion matrix, business rules — all stay
- GitHub Actions workflow stays (just swap the target API)

### What DOES need to change
- Every Mailchimp API call in `corev2/clients/mailchimp_client.py` → rewrite for new platform's API
- Tag add/remove endpoints and payload format
- Member upsert endpoint and payload format
- Archive/delete endpoint
- Pagination approach (offset-based in Mailchimp, may differ)
- `subscriber_hash` calculation (MD5 of email — Mailchimp-specific)
- Authentication header format

### Python Dependencies
```
pydantic>=2.0.0       # Config validation
pyyaml>=6.0           # Config loading
python-dotenv>=1.0.0  # Environment variables
aiohttp>=3.9.0        # Async HTTP client
aiohttp-retry>=2.8.0  # HTTP retry logic
```

### Files that need modification for migration
| File | Change Required |
|---|---|
| `corev2/clients/mailchimp_client.py` | **Full rewrite** — all 7 endpoints change |
| `corev2/clients/http_base.py` | Minor — auth header format may change |
| `corev2/config/production.yaml` | Update Mailchimp section (server, audience, etc.) |
| `corev2/config/schema.py` | Update MailchimpConfig fields if needed |
| `corev2/executor/engine.py` | Update 5 Mailchimp operation handlers |
| `corev2/sync/unsubscribe_sync.py` | Update Mailchimp member scanning |
| `corev2/planner/primary.py` | Update Mailchimp state checking |
| `corev2/planner/secondary.py` | Update Mailchimp tag scanning |

### Files that need NO changes
| File | Reason |
|---|---|
| `corev2/clients/hubspot_client.py` | HubSpot stays the same |
| `corev2/config/loader.py` | Generic config loader |
| `.github/workflows/sync.yml` | Just update env var names if different |
| `main.py` | Orchestration logic unchanged |

---

*End of technical specification. Repository access can be granted for full code review.*
