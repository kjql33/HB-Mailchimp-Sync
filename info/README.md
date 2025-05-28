# HubSpot → Mailchimp Sync Agent

A Python-based integration that synchronizes contacts from HubSpot Active Lists to a Mailchimp Audience using the HubSpot CRM v3 Lists API. This tool automates the process of keeping your marketing lists in sync, ensuring consistent communication across platforms.

## Setup Instructions

1. **Clone this repository**

2. **Create and activate a virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On macOS/Linux
   # OR
   .venv\Scripts\activate  # On Windows
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Create environment configuration:**
   Create a `.env` file with the following variables:
   ```bash
   # HubSpot API
   HUBSPOT_PRIVATE_TOKEN=your_hubspot_private_app_token

   # Mailchimp API
   MAILCHIMP_API_KEY=your_mailchimp_api_key
   MAILCHIMP_LIST_ID=your_mailchimp_audience_id
   MAILCHIMP_DC=your_mailchimp_data_center  # e.g., us20

   # Optional sync parameters (defaults in config.py)
   PAGE_SIZE=20               # Records per HubSpot page
   TEST_CONTACT_LIMIT=0       # 0 = no limit; >0 caps total contacts per list
   MAX_RETRIES=3              # Retry attempts for API calls
   RETRY_DELAY=2              # Delay (seconds) between retries
   REQUIRED_TAGS=COMPANY,CITY,INDUSTRY,PHONE,FNAME,LNAME
   LOG_LEVEL=INFO             # Logging verbosity (DEBUG, INFO, WARNING, ERROR)
   ```

5. **Configure HubSpot lists:**
   Edit the `HUBSPOT_LIST_IDS` list in `config.py` to specify which HubSpot lists to sync.

6. **Run the sync:**
   ```bash
   python sync.py
   ```

## How It Works

The sync agent maintains your Mailchimp audience in perfect alignment with your HubSpot lists through these core operations:

### Single-List Synchronization
1. **Fetch HubSpot Data**: Retrieves list memberships and contact details with pagination
2. **Mailchimp Operations**: Creates/updates contacts, sets status to "subscribed", applies list name as tag
3. **Cleanup**: Removes tags from contacts no longer in the HubSpot list

### Multi-List Management
- Processes each configured HubSpot list independently  
- Contacts in multiple lists receive multiple Mailchimp tags
- Global cleanup archives contacts not present in any tracked list

### Automatic Field Management
- Required merge fields auto-created in Mailchimp if missing
- Data validation and error handling for invalid contacts
- Comprehensive logging and retry mechanisms for reliability

### Core System Rules
1. **Tag Naming**: Mailchimp tags named after HubSpot list names (dynamically fetched)
2. **Multi-List Support**: Contacts can exist in multiple HubSpot lists = multiple Mailchimp tags
3. **Archival Philosophy**: Contacts not in ANY HubSpot list are archived from Mailchimp
4. **Force Subscribe**: All synced contacts forced to "subscribed" status
5. **Merge Field Management**: Required fields auto-created if missing

## Deployment Options

### GitHub Actions (Recommended)
- **Schedule**: Runs every 6 hours automatically
- **Manual Trigger**: Available from GitHub Actions tab
- **Error Handling**: Automatic Teams notification on failure
- **Configuration**: Already set up in `.github/workflows/sync.yml`

### Alternative Deployment Methods
- **Cron jobs**: For server-based deployments
- **Other schedulers**: Any system that can execute Python scripts

## Comprehensive Scenario Analysis

This section documents every possible contact movement and synchronization scenario between HubSpot and Mailchimp.

### Category 1: New Contact Scenarios

#### 1.1 Fresh Contact Addition
- **Trigger**: Contact appears in HubSpot list for first time
- **Action**: Contact created in Mailchimp with "subscribed" status, tagged with list name, all merge fields populated
- **Result**: New Mailchimp member with appropriate tag

#### 1.2 Contact with Missing Email
- **Trigger**: HubSpot contact exists but email field is empty/invalid
- **Action**: Contact skipped with warning logged
- **Result**: No Mailchimp operation performed

#### 1.3 Contact with Partial Data
- **Trigger**: Contact has email but missing other fields
- **Action**: Contact created/updated with available data, missing fields as empty strings
- **Result**: Mailchimp member with partial data but proper tagging

### Category 2: Existing Contact Scenarios

#### 2.1 Contact Data Update
- **Trigger**: Existing contact's data changes in HubSpot
- **Action**: Mailchimp contact updated, status forced to "subscribed"
- **Result**: Updated Mailchimp member data

#### 2.2 Contact Status Resurrection
- **Trigger**: Previously archived/unsubscribed contact re-appears in HubSpot
- **Action**: Contact unarchived, forced to "subscribed", data refreshed
- **Result**: Reactivated Mailchimp member

#### 2.3 Contact Already Tagged
- **Trigger**: Contact already has correct Mailchimp tag
- **Action**: Data updated/refreshed, tag status verified
- **Result**: Refreshed contact with consistent tags

### Category 3: Multi-List Scenarios

#### 3.1 Contact Added to Additional List
- **Trigger**: Existing contact appears in new HubSpot list
- **Action**: Contact gets additional Mailchimp tag, existing tags remain
- **Result**: Contact with multiple Mailchimp tags

#### 3.2 Contact Removed from One List (Remains in Others)
- **Trigger**: Contact leaves one HubSpot list but remains in others
- **Action**: Specific list tag inactivated, other tags remain active
- **Result**: Contact with reduced tag set but not archived

#### 3.3 Contact Moves Between Lists
- **Trigger**: Contact leaves one list and joins another in same sync
- **Action**: Old tag inactivated, new tag applied, data updated
- **Result**: Contact with updated tag reflecting new membership

### Category 4: List Name Change Scenarios

#### 4.1 Successful List Name Rename
- **Trigger**: HubSpot list name changes between sync runs
- **Action**: Mailchimp tag renamed using native API, preserves all members
- **Result**: All previously tagged members have new tag name

#### 4.2 Failed List Name Rename (Fallback)
- **Trigger**: List name change detected but Mailchimp rename API fails
- **Action**: Fallback to manual untagging, members retagged in next sync
- **Result**: Temporary inconsistency resolved in subsequent sync

#### 4.3 Multiple List Renames
- **Trigger**: Multiple lists renamed simultaneously
- **Action**: Each list processed independently with history recorded
- **Result**: All tags updated independently

### Category 5: Contact Removal Scenarios

#### 5.1 Contact Removed from Single List (Multi-List Member)
- **Trigger**: Contact leaves one HubSpot list but remains in others
- **Action**: Specific list tag inactivated, contact remains in Mailchimp
- **Result**: Contact with reduced but not eliminated presence

#### 5.2 Contact Removed from All Lists
- **Trigger**: Contact no longer in any tracked HubSpot list
- **Action**: All tags inactivated during list syncs, contact archived in global cleanup
- **Result**: Contact completely removed from Mailchimp

#### 5.3 Contact Temporarily Missing (API Issue)
- **Trigger**: Contact missing due to HubSpot API error/timeout
- **Action**: Treated as removal if consistent across multiple syncs
- **Result**: Depends on persistence of issue

### Category 6: Edge Cases & Error Scenarios

#### 6.1 Mailchimp API Rate Limiting
- **Trigger**: Too many requests to Mailchimp API
- **Action**: Retry logic with exponential backoff, delays between operations
- **Result**: Eventually successful operation or logged failure

#### 6.2 Duplicate Email Addresses
- **Trigger**: Same email appears multiple times in HubSpot list
- **Action**: Email normalized to lowercase, single Mailchimp member created
- **Result**: Single member with final data state

#### 6.3 Invalid Email Formats
- **Trigger**: HubSpot contains malformed email addresses
- **Action**: Mailchimp API rejects, error logged, sync continues
- **Result**: Invalid contacts skipped

#### 6.4 Missing Required Merge Fields
- **Trigger**: Mailchimp audience lacks required merge fields
- **Action**: Missing fields auto-created, sync aborts if creation fails
- **Result**: Audience structure updated automatically

### Category 7: Configuration Change Scenarios

#### 7.1 New HubSpot List Added
- **Trigger**: Additional list ID added to HUBSPOT_LIST_IDS
- **Action**: New list processed like existing lists
- **Result**: Expanded sync scope with new tag category

#### 7.2 HubSpot List Removed
- **Trigger**: List ID removed from HUBSPOT_LIST_IDS
- **Action**: List no longer processed, existing tags become orphaned
- **Result**: Reduced sync scope with potential member cleanup

#### 7.3 Required Merge Fields Modified
- **Trigger**: REQUIRED_TAGS configuration changed
- **Action**: New fields auto-created, removed fields remain unused
- **Result**: Audience structure adapted to new requirements

### Category 8: API Integration & Authentication Scenarios

#### 8.1 HubSpot API Authentication Failure
- **Trigger**: Invalid or expired HUBSPOT_PRIVATE_TOKEN
- **Action**: Sync aborts with authentication error, no operations performed
- **Result**: Complete sync failure with clear error message

#### 8.2 Mailchimp API Authentication Failure
- **Trigger**: Invalid MAILCHIMP_API_KEY or incorrect data center
- **Action**: Sync aborts with authentication error, no operations performed
- **Result**: Complete sync failure with clear error message

#### 8.3 HubSpot API Rate Limiting
- **Trigger**: Exceeding HubSpot API rate limits during list/contact fetching
- **Action**: Retry with exponential backoff, delays between requests
- **Result**: Eventually successful operation or logged failure after max retries

#### 8.4 Mixed API Availability
- **Trigger**: One API service available, other unavailable
- **Action**: Fail fast if either service unavailable at start
- **Result**: No partial operations, clean failure state

### Category 9: Status Management & Subscription Scenarios

#### 9.1 Unsubscribed Contact Re-sync
- **Trigger**: Contact with "unsubscribed" status in Mailchimp appears in HubSpot list
- **Action**: Contact data updated but status forced to "subscribed"
- **Result**: Previously unsubscribed contact reactivated

#### 9.2 Archived Contact Re-sync
- **Trigger**: Archived Mailchimp contact appears in HubSpot list
- **Action**: Contact unarchived, status set to "subscribed", data refreshed
- **Result**: Archived contact fully restored to active status

#### 9.3 Cleaned Contact Re-sync
- **Trigger**: Contact with "cleaned" status appears in HubSpot list
- **Action**: Contact status forced to "subscribed", warning logged about email validity
- **Result**: Contact reactivated despite previous email issues

#### 9.4 Pending Contact Confirmation
- **Trigger**: Contact has "pending" status due to double opt-in requirements
- **Action**: Contact updated but status remains "pending" until user confirmation
- **Result**: Contact exists but may not be visible in standard Mailchimp views

### Category 10: Merge Field & Data Structure Scenarios

#### 10.1 Missing Merge Field Auto-Creation
- **Trigger**: REQUIRED_TAGS field doesn't exist in Mailchimp audience
- **Action**: Field automatically created with appropriate type and name
- **Result**: Audience structure expanded to accommodate sync requirements

#### 10.2 Merge Field Creation Failure
- **Trigger**: Auto-creation of required merge field fails
- **Action**: Sync aborts with clear error message about field creation
- **Result**: No data operations until field issue resolved

#### 10.3 Data Type Mismatch
- **Trigger**: HubSpot data doesn't match Mailchimp field type expectations
- **Action**: Data converted to string format, warnings logged for review
- **Result**: Data preserved as text even if type mismatch occurs

#### 10.4 Oversized Data Fields
- **Trigger**: HubSpot contact data exceeds Mailchimp field size limits
- **Action**: Data truncated to fit limits, warning logged with original length
- **Result**: Partial data preserved with notification of truncation

### Category 11: Performance & Scaling Scenarios

#### 11.1 Large List Processing
- **Trigger**: HubSpot list contains thousands of contacts
- **Action**: Batch processing with pagination, progress indicators shown
- **Result**: All contacts processed efficiently with memory management

#### 11.2 Multiple Large Lists
- **Trigger**: Multiple lists each containing large contact sets
- **Action**: Lists processed sequentially, global deduplication at end
- **Result**: All lists synced with proper memory cleanup between lists

#### 11.3 Mailchimp API Rate Limit Reached
- **Trigger**: High-frequency operations exceed Mailchimp rate limits
- **Action**: Automatic rate limiting with configurable delays between operations
- **Result**: Operations complete successfully with extended timeline

#### 11.4 Network Timeout Scenarios
- **Trigger**: Slow network connections cause API timeouts
- **Action**: Retry with increased timeout values, exponential backoff
- **Result**: Operations eventually succeed or fail gracefully after max attempts

## System Guarantees & Invariants

### What the System ALWAYS Ensures:
- **Email Uniqueness**: One Mailchimp member per email address
- **Tag Consistency**: List membership accurately reflected in tags
- **Data Freshness**: Mailchimp data updated with latest HubSpot information
- **Status Normalization**: All synced members forced to "subscribed"
- **Audit Trail**: Complete logging of all operations and changes

### What the System NEVER Does:
- **Duplicate Members**: Same email cannot create multiple Mailchimp entries
- **Delete Without Cause**: Members only archived if not in any tracked list
- **Ignore Errors**: All failures logged and tracked for investigation
- **Skip Verification**: Tag application and member status always verified
- **Lose Historical Data**: Operations preserve creation dates and timestamps

## Failure Modes & Recovery

### Recoverable Failures:
- API rate limits (handled with retry logic)
- Temporary network issues (retry with backoff)
- Invalid individual contact data (skip and continue)
- Tag application failures (retry in next sync)

### Non-Recoverable Failures:
- Invalid API credentials (abort entire sync)
- Missing required merge fields that can't be created (abort sync)
- Malformed configuration data (abort with error)
- HubSpot list access permissions issues (skip list, continue others)

## Performance Characteristics

### Optimization Strategies:
- Batch processing for large contact sets
- Progress indicators for long operations
- Configurable rate limiting and delays
- Efficient pagination for large datasets

### Scalability Limits:
- Bounded by Mailchimp API rate limits
- Memory usage scales with largest single list size
- Processing time linear with total contact count
- Storage grows with historical data retention (auto-pruned after 7 days)

## Recent Updates & Improvements

### Tag Renaming Enhancement
**Major Improvement**: Implemented proper tag renaming functionality using Mailchimp's native API instead of the previous inefficient approach.

**Benefits**:
- Instant tag renaming with single API call using `/lists/{list_id}/segments/{tag_id}` endpoint
- Preserves all member associations and segment history
- Maintains Mailchimp's segment timestamps properly
- Includes fallback to previous method if native API fails

### Data Storage Architecture
The system uses a sophisticated file-based storage system for tracking and auditing:

- **`raw_data/metadata/`**: Timestamped HubSpot list metadata snapshots
- **`raw_data/snapshots/`**: Contact and membership data organized by date
- **`list_name_map.json`**: Current list name mapping for change detection
- **`list_name_history.json`**: Complete historical record of all list name changes
- **Retention Policy**: Raw data automatically pruned after 7 days (configurable)

### Workflow Automation
- **Schedule**: Runs every 6 hours via GitHub Actions
- **Manual Trigger**: Can be triggered manually from GitHub Actions tab
- **Error Handling**: Automatic Teams notification on failure
- **CI Integration**: Exits with error code on sync failures
