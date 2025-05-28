# Test Utilities

This folder contains testing and debugging utilities for the HubSpot-Mailchimp sync system.

## Available Tools

### `mailchimp_tags.py` - Comprehensive Mailchimp Tag Management
**Primary utility for all Mailchimp tag operations**

**Analysis Mode (Default):**
- Complete tag analysis with segment detection
- Member counts and detailed member lists  
- Detection of duplicate, orphaned, or problematic tags
- Export detailed reports to JSON
- Verification of rename operations

**Rename Mode:**
- Proper tag renaming by migrating members (not creating new tags)
- Verification that old tag definitions are completely removed
- Progress tracking and error handling
- Rate limiting and retry logic

**Usage Examples:**
```bash
# Full tag analysis (generates JSON report)
python mailchimp_tags.py

# Quick summary only (no JSON report)
python mailchimp_tags.py --quick

# Properly rename a tag (migrates members, removes old tag definition)
python mailchimp_tags.py --rename "OLD TAG NAME" "NEW TAG NAME"
```

**Key Features:**
- ✅ Detects orphaned tag definitions (empty segments)
- ✅ Identifies member overlap between similar tags
- ✅ Proper tag renaming vs create+migrate
- ✅ Comprehensive error handling and retry logic
- ✅ Rate limiting to respect Mailchimp API limits
- ✅ Detailed progress reporting

---

### `debug_hubspot.py` - HubSpot API Debugging
**Utility for troubleshooting HubSpot API access and authentication**

Helps diagnose issues with:
- HubSpot authentication
- List access permissions
- API response debugging

---

### `comprehensive_notification_test.py` - Teams Notification System Testing
**Complete test suite for the notification system covering all error scenarios**

**Verified Functionality:**
- ✅ 25+ notification scenarios tested successfully
- ✅ All Teams notifications sent to production webhook during testing
- ✅ Multi-level severity system (INFO, WARNING, ERROR, CRITICAL)
- ✅ Session tracking and issue accumulation
- ✅ Rich Teams message cards with color coding
- ✅ Dual notification system (Python + GitHub Actions)

**Usage:**
```bash
# Run comprehensive notification test suite
python comprehensive_notification_test.py
```

**Coverage:**
- Environment validation failures
- API timeout and retry scenarios
- Contact processing issues (missing emails, invalid formats)
- Data truncation warnings
- Tag operation failures and recoveries
- Contact status warnings (pending, cleaned, archived)
- Merge field validation and creation failures
- Contact removal failures
- Rate limiting scenarios
- Complete script failures
- GitHub Actions webhook integration

---

## Generated Reports

**`../mailchimp_tag_analysis.json`** - Detailed analysis report containing:
- Complete member lists for each tag
- Segment information
- Issue detection results
- Tag search API results
- Comprehensive metadata

## Best Practices

1. **Always use `mailchimp_tags.py --quick` first** to get a quick overview
2. **Use the rename mode for proper tag management** instead of manual operations
3. **Review the generated JSON reports** for detailed analysis
4. **Keep this folder clean** - avoid creating duplicate analysis tools
