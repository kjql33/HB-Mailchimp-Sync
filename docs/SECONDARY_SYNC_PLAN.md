# Secondary Sync Implementation Plan - corev2

**Status:** Planning Phase  
**Date Created:** 2026-02-24  
**Purpose:** Rebuild Mailchimp → HubSpot secondary sync in corev2 architecture

---

## 📋 Executive Summary

**What is Secondary Sync?**
Secondary sync is the **reverse workflow** that moves contacts FROM Mailchimp BACK TO HubSpot based on marketing journey completion signals (exit tags). This creates a complete bidirectional loop where contacts flow through marketing journeys and return to sales pipelines.

**The Complete Bidirectional Flow:**
```
1. PRIMARY SYNC (HubSpot → Mailchimp) ✅ WORKING
   - Contacts from HubSpot lists → Mailchimp for marketing
   - Tagged in Mailchimp based on source list
   - Smart exclusion/archival rules applied

2. MARKETING JOURNEY (Mailchimp) 
   - Contacts go through email sequences
   - Exit tags applied when journey complete
   - Examples: "qualified_leads", "hot_prospects", "converted"

3. SECONDARY SYNC (Mailchimp → HubSpot) ❌ NOT YET BUILT
   - Scans Mailchimp for contacts with exit tags
   - Imports to corresponding HubSpot destination lists
   - REMOVES from original source lists (anti-remarketing)
   - Optionally archives from Mailchimp
```

---

## 🏗️ Old System Analysis (core v1)

### Architecture Overview

**File:** `core/secondary_sync.py` (~1850 lines)  
**Class:** `MailchimpToHubSpotSync`

### Key Features That Worked Well ✅

1. **Exit Tag Scanning with Pagination**
   - Scans entire Mailchimp audience for specific tags
   - Pagination support for large audiences (1000 per page)
   - Efficient batch processing
   - Progress tracking with ETA calculations

2. **Source List Tracking**
   - Uses Mailchimp merge field `ORI_LISTS` to track original source
   - Enables precise anti-remarketing (only remove from original list)
   - Prevents accidental removal from manually-added lists
   - Manual override protection via `_via_` prefix in source tracking

3. **Anti-Remarketing System**
   - Groups contacts by source list for efficient processing
   - Batch removal from source lists (100 contacts per batch)
   - Validation before removal operations
   - Respects exclusion rules configuration

4. **Atomic Operations with Rollback**
   - Every operation tracked in rollback journal
   - Can reverse add/remove operations if failure occurs
   - Emergency rollback on critical errors
   - Operation stack for LIFO rollback ordering

5. **Batch API Operations**
   - Batch contact lookup (100 emails at a time)
   - Bulk list additions/removals
   - Rate limiting between batches
   - Memory optimization for large datasets

6. **Delayed Verification System**
   - Waits 5 minutes for HubSpot propagation
   - Retries verification up to 3 times
   - Separate verification for additions vs removals
   - Audit trail generation

7. **Import List Custom Property Tracking**
   - Updates `import_lists` property with friendly source names
   - Appends to existing values (doesn't overwrite)
   - Provides audit trail of contact journey
   - Uses semicolon-separated format

### Configuration Model

```python
# Old core v1 config structure:

SECONDARY_SYNC_MAPPINGS = {
    "qualified_leads": "680",      # Mailchimp tag → HubSpot list ID
    "hot_prospects": "681",
    "converted": "682"
}

LIST_EXCLUSION_RULES = {
    "718": ["680", "681", "682"],  # Remove from 718 when added to any of these
    "719": ["680", "681"],         # Remove from 719 when added to 680 or 681
}

REMOVAL_TRIGGER_TAGS = list(SECONDARY_SYNC_MAPPINGS.keys())  # Auto-derived

ORI_LISTS_FIELD = "ORILISTS"  # Mailchimp merge field for source tracking

SECONDARY_SYNC_MODE = "LIVE_RUN"  # or "TEST_RUN"
SECONDARY_TEST_CONTACT_LIMIT = 10

ENABLE_MAILCHIMP_ARCHIVAL = True  # Archive from MC after import
```

### Workflow Phases (Old System)

**Phase 1: Get Exit-Tagged Contacts**
- Scan Mailchimp audience for each exit tag
- Paginate through entire list (1000 per page)
- Extract contact data + source list tracking
- Log progress with ETA

**Phase 2: Import to HubSpot Destination Lists**
- Batch lookup contacts in HubSpot by email (100 at a time)
- Validate destination lists exist
- Bulk add contacts to destination lists (100 per batch)
- Update `import_lists` custom property with source tracking
- Record reversible actions for rollback

**Phase 3: Remove from Source Lists (Anti-Remarketing)**
- Group contacts by their original source list ID
- For each source list, check if exclusion rules apply
- Batch remove contacts from source lists (100 per batch)
- Skip contacts with manual override protection (`_via_`)
- Record reversible actions for rollback

**Phase 4: Archive from Mailchimp (Optional)**
- If `ENABLE_MAILCHIMP_ARCHIVAL = True`
- Delete/archive each contact from Mailchimp
- Note: Archival is NOT reversible
- Rate limited to avoid API throttling

**Phase 5: Delayed Verification**
- Wait 5 minutes for HubSpot propagation
- Verify contacts added to destination lists
- Verify contacts removed from source lists
- Retry verification up to 3 times
- Generate audit reports

### What We Learned from v1

**✅ Good Decisions:**
1. Source list tracking via Mailchimp merge fields
2. Batch API operations for performance
3. Atomic operations with rollback capability
4. Delayed verification to handle HubSpot propagation
5. Progress tracking for long operations
6. Manual override protection

**❌ Issues to Fix:**
1. **Tightly coupled to old config system** - Uses direct imports from `config.py`
2. **No separation of planning vs execution** - Does everything in one monolithic class
3. **Mixed concerns** - Single class handles API calls, business logic, and orchestration
4. **Hard to test** - No dependency injection, difficult to unit test
5. **No dry-run mode** - Can't preview operations before executing
6. **Expensive verification** - 5 minute wait + retries for every operation
7. **No operation journaling** - Can't resume from failures
8. **Limited observability** - Basic logging, no structured metrics

---

## 🎯 corev2 Architecture Design

### Design Principles

1. **Separation of Concerns**
   - Planner generates operations (read-only)
   - Executor applies operations (write)
   - Clear boundary between planning and execution

2. **Dependency Injection**
   - Clients passed to planner/executor
   - Easy to mock for testing
   - No global state

3. **Configuration-Driven**
   - Uses corev2 pydantic config schema
   - Type-safe configuration
   - Validation at load time

4. **Operation-Based Architecture**
   - Operations are data structures (JSON-serializable)
   - Can save/resume plans
   - Dry-run mode is just "plan without execute"

5. **Idempotent Operations**
   - Safe to re-run
   - Skip if already in desired state
   - No side effects on retry

### Folder Structure

```
corev2/
├── config/
│   ├── schema.py                    # Config models (ALREADY HAS secondary_sync_mappings)
│   ├── production.yaml              # Config file (placeholder exists)
│   └── defaults.yaml
│
├── planner/
│   ├── primary.py                   # ✅ HubSpot → Mailchimp (DONE)
│   └── secondary.py                 # ❌ Mailchimp → HubSpot (TO BUILD)
│
├── executor/
│   └── engine.py                    # ✅ Executes operations (ALREADY SUPPORTS NEEDED OPS)
│
├── sync/
│   └── unsubscribe_sync.py          # ✅ Opt-out sync (DONE)
│
└── clients/
    ├── hubspot_client.py            # ✅ HubSpot API wrapper
    └── mailchimp_client.py          # ✅ Mailchimp API wrapper
```

### Operation Types

The executor **already supports** these operation types (no changes needed):

```python
# Operations the secondary sync planner will generate:

{
  "operation": "add_hs_to_list",
  "contact_email": "user@example.com",
  "list_id": "680",
  "reason": "secondary_sync_exit_tag_qualified_leads"
}

{
  "operation": "remove_hs_from_list", 
  "contact_email": "user@example.com",
  "list_id": "718",
  "reason": "anti_remarketing_from_source_list"
}

{
  "operation": "archive_mc_member",
  "contact_email": "user@example.com",
  "reason": "secondary_sync_cleanup"
}

{
  "operation": "update_hs_property",
  "contact_email": "user@example.com",
  "property": "import_lists",
  "value": "Recruitment; General; Qualified Leads (via qualified_leads exit tag)",
  "append": true
}
```

### Configuration Schema

```yaml
# corev2/config/production.yaml

# Secondary sync mappings (Mailchimp exit tag → HubSpot list)
secondary_sync_mappings:
  qualified_leads: "680"
  hot_prospects: "681"
  converted: "682"
  not_interested: "762"  # Route to opted-out list

# Anti-remarketing rules (source list → destination lists to remove from)
list_exclusion_rules:
  "718": ["680", "681", "682"]  # General → remove when moved to any destination
  "719": ["680", "681"]         # Recruitment → remove when qualified/hot
  "720": ["682"]                # Competition → remove when converted

# Archival settings
archival:
  archive_after_secondary_sync: true  # Archive from MC after importing to HS
  max_archive_per_run: 100
```

### Data Flow

```
1. SECONDARY PLANNER (corev2/planner/secondary.py)
   ↓
   ├─→ Scan Mailchimp for exit tags
   ├─→ Look up contacts in HubSpot
   ├─→ Generate operations:
   │     • add_hs_to_list (destination)
   │     • remove_hs_from_list (source, if exclusion rule exists)
   │     • update_hs_property (import_lists tracking)
   │     • archive_mc_member (if enabled)
   ↓
   
2. OPERATION PLAN (JSON file)
   {
     "plan_type": "secondary_sync",
     "timestamp": "2026-02-24T10:30:00Z",
     "summary": {
       "total_contacts": 150,
       "operations_by_type": {
         "add_hs_to_list": 150,
         "remove_hs_from_list": 120,
         "update_hs_property": 150,
         "archive_mc_member": 150
       }
     },
     "operations": [...]
   }
   ↓
   
3. EXECUTOR (corev2/executor/engine.py)
   ↓
   ├─→ Load plan from file
   ├─→ Execute operations sequentially
   ├─→ Log results to execution_journal.jsonl
   ├─→ Return summary
   ↓
   
4. EXECUTION JOURNAL (JSONL)
   {"timestamp": "...", "operation": "add_hs_to_list", "email": "...", "result": "success"}
   {"timestamp": "...", "operation": "remove_hs_from_list", "email": "...", "result": "success"}
   ...
```

---

## 🔨 Implementation Plan

### Phase 1: Core Planner Implementation

**File:** `corev2/planner/secondary.py`

**Class:** `SecondaryPlanner`

```python
class SecondaryPlanner:
    """Generates secondary sync operations (Mailchimp → HubSpot)"""
    
    def __init__(self, config: V2Config, hs_client: HubSpotClient, mc_client: MailchimpClient):
        self.config = config
        self.hs = hs_client
        self.mc = mc_client
    
    async def generate_plan(self, contact_limit: Optional[int] = None) -> Dict:
        """
        Scan Mailchimp for exit-tagged contacts and generate operations.
        
        Returns:
            Plan dict with operations and summary
        """
        pass
```

**Key Methods:**

1. **`scan_mailchimp_for_exit_tags()`**
   - Iterate through configured `secondary_sync_mappings`
   - For each exit tag, scan Mailchimp audience
   - Paginate through results (1000 per page)
   - Extract: email, tags, merge_fields (for source tracking)
   - Return: Dict[tag, List[contact_data]]

2. **`batch_lookup_hubspot_contacts(emails: List[str])`**
   - Use HubSpot batch API to find contact IDs
   - Batch size: 100 emails
   - Return: Dict[email, vid]

3. **`generate_operations_for_contact(contact_data, exit_tag, destination_list)`**
   - Generate `add_hs_to_list` for destination
   - Check if source list has exclusion rules
   - If yes, generate `remove_hs_from_list` for source
   - Generate `update_hs_property` for import_lists tracking
   - If archival enabled, generate `archive_mc_member`
   - Return: List[operation]

4. **`build_plan_summary(operations: List[Dict])`**
   - Count operations by type
   - Count unique contacts
   - Calculate expected API calls
   - Return: summary dict

### Phase 2: Configuration Enhancement

**File:** `corev2/config/production.yaml`

**Add:**
```yaml
# Secondary sync configuration
secondary_sync_mappings:
  # Example mappings (configure based on marketing journeys)
  # qualified_leads: "680"
  # hot_prospects: "681"
  {}  # Empty by default until journeys configured

# Anti-remarketing rules
list_exclusion_rules:
  # Example: Remove from General (718) when moved to Qualified (680)
  # "718": ["680", "681", "682"]
  {}  # Empty by default

# Source tracking field in Mailchimp
mailchimp:
  source_tracking_field: "ORILISTS"  # Merge field name for source list tracking
```

**File:** `corev2/config/schema.py`

**Enhance MailchimpConfig:**
```python
class MailchimpConfig(BaseModel):
    api_key: SecretStr
    server_prefix: str
    audience_id: str
    source_tracking_field: str = Field(
        default="ORILISTS",
        description="Mailchimp merge field name for tracking original source list"
    )
```

### Phase 3: Integration into main.py

**File:** `main.py`

**Add after primary sync (Step 3):**

```python
# STEP 4: Secondary Sync (Mailchimp → HubSpot)
if config.secondary_sync_mappings:
    logger.info("\n" + "="*70)
    logger.info("STEP 4: Secondary Sync (Mailchimp → HubSpot)")
    logger.info("="*70)
    
    from corev2.planner.secondary import SecondaryPlanner
    
    secondary_planner = SecondaryPlanner(config, hs_client, mc_client)
    secondary_plan = await secondary_planner.generate_plan(
        contact_limit=contact_limit
    )
    
    logger.info(f"\n✓ Secondary Plan Generated:")
    logger.info(f"  Exit-tagged contacts: {secondary_plan['summary']['total_contacts']}")
    logger.info(f"  Operations by type:")
    for op_type, count in secondary_plan['summary']['operations_by_type'].items():
        logger.info(f"    • {op_type}: {count}")
    
    # Execute secondary sync
    secondary_results = await executor.execute_plan(secondary_plan)
    
    logger.info(f"\n✓ Secondary Sync Complete:")
    logger.info(f"  Total operations: {secondary_results['total_operations']}")
    logger.info(f"  ✓ Successful: {secondary_results['successful']}")
    logger.info(f"  ✗ Failed: {secondary_results['failed']}")
else:
    logger.info("\nℹ️  Secondary sync disabled (no mappings configured)")
```

### Phase 4: Testing Strategy

**Unit Tests:**
- Test exit tag scanning with mock Mailchimp data
- Test operation generation for various scenarios
- Test source list exclusion rule logic
- Test batch lookup with different response sizes

**Integration Tests:**
- Create test contact in Mailchimp with exit tag
- Run secondary sync plan generation
- Verify operations generated correctly
- Apply plan (in test environment)
- Verify contact moved to destination list
- Verify contact removed from source list
- Verify import_lists property updated

**Load Tests:**
- Test with 1000+ exit-tagged contacts
- Verify pagination works correctly
- Check memory usage during large scans
- Validate batch operations scale properly

### Phase 5: Observability & Monitoring

**Metrics to Track:**
- Exit-tagged contacts found per tag
- Contacts successfully imported to HubSpot
- Contacts removed from source lists
- Contacts archived from Mailchimp
- Operations failed (by type)
- API calls made (by endpoint)
- Operation execution time

**Logging:**
- Structured log events for each operation
- Progress tracking for large batches
- Error details with contact email (PII-safe)
- Summary report at completion

**Alerts:**
- Failed secondary sync runs
- High failure rate (>10%)
- Unexpected contact counts
- API rate limit hits

---

## 📊 Performance Optimizations

### From v1 Experience

1. **Batch API Calls**
   - Lookup 100 contacts at once (not 1 at a time)
   - Add/remove 100 contacts per list operation
   - Reduces API calls by 100x

2. **Pagination**
   - Scan Mailchimp in chunks of 1000
   - Don't load entire audience into memory
   - Process and discard pages as you go

3. **Smart Filtering**
   - Only scan for configured exit tags
   - Skip contacts already processed (via timestamp)
   - Use Mailchimp's tag filtering if available

4. **Rate Limiting**
   - Respect API limits (10 req/sec for both platforms)
   - Add delays between batch operations
   - Monitor 429 responses and back off

5. **Memory Management**
   - Stream operations to file (don't hold all in memory)
   - Use generators for large result sets
   - Clear processed batches from memory

### New Optimizations for v2

1. **Incremental Sync**
   - Track last sync timestamp
   - Only scan contacts added/tagged since last run
   - Reduces scan time by 90%+ on subsequent runs

2. **Parallel Processing**
   - Scan multiple exit tags in parallel
   - Independent tag scans don't block each other
   - Use asyncio for concurrent API calls

3. **Caching**
   - Cache HubSpot list metadata
   - Cache contact ID lookups (with TTL)
   - Reduce redundant API calls

4. **Smart Scheduling**
   - Run secondary sync less frequently (e.g., hourly)
   - Primary sync can run more often (e.g., every 15 min)
   - Stagger sync times to avoid API congestion

---

## 🛡️ Safety & Rollback

### Safety Gates

1. **Dry-Run Mode**
   - Generate plan without executing
   - Review operations before applying
   - Catch issues in planning phase

2. **Contact Limits**
   - Test with small batches first (10-20 contacts)
   - Gradually increase batch size
   - Full production only after validation

3. **Exclusion List Checks**
   - Never modify contacts in Lists 762, 773, 717
   - Skip opted-out contacts
   - Respect manual override flags

4. **Validation Before Execution**
   - Verify destination lists exist
   - Check source list validity
   - Confirm contact exists in HubSpot before operations

### Rollback Strategy

**Unlike v1, corev2 uses operation journaling instead of in-memory rollback:**

1. **Save operations to execution_journal.jsonl**
   - Every operation logged before execution
   - Result logged after execution
   - Timestamp + email + operation type + result

2. **Manual Rollback Process**
   - Read journal to see what was done
   - Generate inverse operations
   - Apply inverse operations as new plan

3. **Prevention Over Rollback**
   - Dry-run first ALWAYS
   - Start with small batches
   - Verify results before scaling up

**Why This is Better:**
- No memory overhead for rollback journal
- Can rollback even after process restart
- Audit trail persists indefinitely
- Simpler implementation (no complex rollback logic)

---

## 🚀 Rollout Plan

### Week 1: Core Implementation
- [ ] Create `corev2/planner/secondary.py`
- [ ] Implement `scan_mailchimp_for_exit_tags()`
- [ ] Implement `batch_lookup_hubspot_contacts()`
- [ ] Implement `generate_operations_for_contact()`
- [ ] Write unit tests

### Week 2: Configuration & Integration
- [ ] Add configuration to `production.yaml`
- [ ] Update schema validation
- [ ] Integrate into `main.py`
- [ ] Add logging and metrics
- [ ] Write integration tests

### Week 3: Testing & Validation
- [ ] Test with sample exit-tagged contacts
- [ ] Verify all operation types work
- [ ] Test anti-remarketing logic
- [ ] Load test with 1000+ contacts
- [ ] Performance profiling

### Week 4: Production Rollout
- [ ] Configure first exit tag mapping (qualified_leads)
- [ ] Run dry-run and review operations
- [ ] Execute on small batch (10 contacts)
- [ ] Manual verification in both systems
- [ ] Full production run if successful
- [ ] Monitor metrics and errors

---

## 📝 Example Configuration

### Real-World Scenario

**Marketing Journey:**
1. Contacts added to HubSpot List 718 "General Marketing"
2. Synced to Mailchimp with "General" tag
3. Go through 5-email nurture sequence
4. Responders get exit tag "qualified_leads"
5. Secondary sync imports to HubSpot List 680 "Qualified Leads"
6. Removed from List 718 to prevent re-marketing
7. Archived from Mailchimp (journey complete)

**Configuration:**
```yaml
secondary_sync_mappings:
  qualified_leads: "680"  # Mailchimp tag → HubSpot list

list_exclusion_rules:
  "718": ["680"]  # Remove from General when moved to Qualified

archival:
  archive_after_secondary_sync: true
```

**Expected Operations for 1 Contact:**
1. `add_hs_to_list` (List 680 "Qualified Leads")
2. `remove_hs_from_list` (List 718 "General Marketing")
3. `update_hs_property` (import_lists = "General; Qualified Leads (via qualified_leads)")
4. `archive_mc_member` (remove from Mailchimp)

---

## 🎯 Success Criteria

### Functional Requirements
- ✅ Scans Mailchimp for all configured exit tags
- ✅ Correctly identifies source list from merge fields
- ✅ Imports contacts to destination HubSpot lists
- ✅ Removes from source lists per exclusion rules
- ✅ Updates import_lists property for audit trail
- ✅ Archives from Mailchimp if enabled
- ✅ Handles pagination for large audiences
- ✅ Idempotent (safe to re-run)

### Performance Requirements
- ✅ Processes 1000 contacts in < 15 minutes
- ✅ Uses batch APIs (not individual calls)
- ✅ Respects rate limits (no 429 errors)
- ✅ Memory usage < 500MB for 10k contacts

### Reliability Requirements
- ✅ Dry-run mode for validation
- ✅ Handles API errors gracefully
- ✅ Logs all operations to journal
- ✅ Can resume from failures
- ✅ No data loss on network errors

### Observability Requirements
- ✅ Structured logging with metrics
- ✅ Summary report after each run
- ✅ Execution journal for audit
- ✅ Error tracking by type
- ✅ Performance metrics logged

---

## 🤔 Open Questions

1. **How often should secondary sync run?**
   - Hourly? Daily? On-demand?
   - Depends on marketing journey length

2. **Should we verify operations post-execution?**
   - v1 had 5-minute delayed verification
   - Worth the complexity? Or trust API responses?

3. **Incremental sync vs full scan?**
   - Track last sync timestamp?
   - Or always scan all tags?

4. **What happens to contacts with multiple exit tags?**
   - Import to multiple destination lists?
   - Or enforce one tag per contact?

5. **Source list tracking for manually-added contacts?**
   - What if contact has no ORILISTS field?
   - Skip anti-remarketing? Or use default behavior?

---

## 📚 References

- Old implementation: `core/secondary_sync.py` (Zap Versions folder)
- Primary sync planner: `corev2/planner/primary.py`
- Executor: `corev2/executor/engine.py`
- Config schema: `corev2/config/schema.py`

---

**Next Steps:** Review plan, answer open questions, then proceed with Week 1 implementation.
