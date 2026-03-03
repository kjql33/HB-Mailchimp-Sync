# 🤖 Agent Brief: HubSpot Email Sender Implementation

**Date:** 2026-02-24  
**Purpose:** Brief for Claude agent to implement HubSpot email sending capability  
**Target:** Send emails to HubSpot lists via API

---

## 📋 Mission Statement

Build a Python tool that can send emails to contacts in HubSpot lists directly via the HubSpot API, creating a complete email campaign workflow that integrates with our existing sync system.

---

## 🎯 System Context

### Current System Architecture

You are working within a **mature Python codebase** that manages bidirectional sync between HubSpot and Mailchimp.

**Key facts about the existing system:**

1. **Location:** `c:\Users\EjasDeane\OneDrive - Deane Holdings\Solace Group - Documents\Operations\CRM\Zap Migration`

2. **Architecture:** corev2 (modern refactored version)
   - `corev2/clients/` - API client wrappers
   - `corev2/planner/` - Operation planning (read-only)
   - `corev2/executor/` - Operation execution (write)
   - `corev2/config/` - YAML configuration + Pydantic schemas

3. **Authentication:**
   - HubSpot: Private App Token (environment variable `HUBSPOT_PRIVATE_APP_TOKEN`)
   - Stored in `.env` file (not committed to git)
   - Already working for all HubSpot CRM APIs

4. **Existing HubSpot Integration:**
   - `corev2/clients/hubspot_client.py` - HTTP client with rate limiting
   - Full CRM API coverage (contacts, lists, properties)
   - Communication Preferences API (for opt-outs)
   - Batch operations support

5. **Lists in Use:**
   - List 718: "General" (~2000 contacts)
   - List 719: "Recruitment" (~500 contacts)  
   - List 720: "Competition" (~300 contacts)
   - List 762: "Opted Out" (exclusion list - NEVER email)
   - List 773: "Test Contacts" (exclusion list)
   - List 717: "Active Deals" (exclusion list)

6. **Exclusion Logic:**
   - System NEVER touches contacts in Lists 762, 773, 717
   - Respects Communication Preferences opt-out status
   - GDPR/compliance-aware

---

## 🔍 HubSpot Email API Research Summary

### Available Email APIs

#### 1. **Marketing Email API (v3)** ⭐ RECOMMENDED FOR CAMPAIGNS

**Endpoint:** `POST https://api.hubapi.com/marketing-emails/v1/emails`

**What it does:**
- Creates marketing email drafts
- Associates emails with HubSpot lists
- Supports full HTML content and templates

**Critical Limitation:**
- ❌ **Cannot programmatically SEND emails**
- ✅ Can create drafts, but requires manual "Send" button click in HubSpot UI
- HubSpot enforces manual review for anti-spam compliance

**Use Case:** Semi-automated workflow where script prepares email, human reviews and sends

#### 2. **Transactional Email API** ❌ NOT SUITABLE

**Endpoint:** `POST https://api.hubapi.com/marketing/v3/transactional/single-send`

**What it does:**
- Sends individual one-off emails
- Fully automated (no manual review)

**Why NOT suitable:**
- Intended for automated notifications (order confirmations, password resets, etc.)
- **Policy violation** to use for marketing campaigns
- Would require 2000 individual API calls for 2000 contacts
- Rate limited to prevent bulk use

#### 3. **Current System (Mailchimp)** ✅ FULLY AUTOMATED

**What it does:**
- Fully automated campaign sends
- Rich analytics and A/B testing
- Deliverability optimization

**Why it works:**
- Mailchimp's business model is marketing automation
- No manual review requirements
- Current sync system already integrates well

---

## 🎯 Implementation Options

### Option A: Semi-Automated HubSpot Email 🟡 HYBRID APPROACH

**Workflow:**
1. Python script creates email draft via Marketing Email API
2. Associates with List 718
3. Sets content (HTML + text)
4. **Human logs into HubSpot (2-minute task)**
5. Reviews email in draft list
6. Clicks "Send" button

**Pros:**
- Native HubSpot integration
- No Mailchimp dependency for this workflow
- Email lives in HubSpot ecosystem

**Cons:**
- Requires manual step (not fully automated)
- Cannot schedule sends via API
- Human must be available to click "Send"

**Code Structure:**
```python
# corev2/email/hubspot_email.py

class HubSpotEmailCampaign:
    async def create_draft(
        self,
        subject: str,
        html_content: str,
        text_content: str,
        list_id: str,
        from_email: str,
        campaign_name: str
    ) -> str:
        """
        Create email draft in HubSpot.
        Returns draft ID.
        """
        pass
    
    async def get_draft_status(self, draft_id: str) -> Dict:
        """Check if draft has been sent."""
        pass
```

### Option B: Keep Mailchimp for Campaigns ✅ CURRENT SYSTEM

**Workflow:**
1. Contacts sync to Mailchimp (already happening)
2. Send campaign via Mailchimp API (fully automated)
3. Track engagement in Mailchimp
4. Secondary sync brings qualified leads back to HubSpot

**Pros:**
- Fully automated (no human intervention)
- Rich campaign features (A/B testing, send-time optimization)
- Better deliverability (Mailchimp specializes in this)
- Already working and production-tested

**Cons:**
- Dependency on external service
- Contacts must exist in Mailchimp first
- Another platform to manage

### Option C: Hybrid Approach 🎯 BEST OF BOTH WORLDS

**Workflow:**
1. **General campaigns** → Mailchimp (automated)
2. **One-off sends** → HubSpot (semi-automated)
3. **Transactional emails** → HubSpot Transactional API (automated)

**Use Cases:**
- Weekly newsletter → Mailchimp
- Event invitation to directors → HubSpot Marketing Email (human reviews before send)
- Invoice sent → HubSpot Transactional API

---

## 🔨 Implementation Plan

### Phase 1: Email Draft Creator

**Goal:** Python tool that creates email drafts in HubSpot Marketing Editor

**File:** `corev2/email/campaign_drafter.py`

**Key Methods:**
```python
class HubSpotCampaignDrafter:
    def __init__(self, config: V2Config, hs_client: HubSpotClient):
        self.config = config
        self.hs = hs_client
    
    async def create_email_draft(
        self,
        campaign_name: str,
        subject: str,
        preview_text: str,
        html_content: str,
        text_content: str,
        target_list_id: str,
        from_email: str,
        from_name: str
    ) -> Dict:
        """
        Create email draft in HubSpot Marketing.
        
        Returns:
            {
                "draft_id": "123456789",
                "edit_url": "https://app.hubspot.com/...",
                "status": "draft",
                "created_at": "2026-02-24T10:30:00Z"
            }
        """
        
        # 1. Create email object
        email_data = {
            "name": campaign_name,
            "subject": subject,
            "previewText": preview_text,
            "emailBody": html_content,
            "emailType": "BATCH_EMAIL",
            "state": "DRAFT"
        }
        
        result = await self.hs.post("/marketing-emails/v1/emails", json=email_data)
        draft_id = result['data']['id']
        
        # 2. Associate with list
        await self._associate_with_list(draft_id, target_list_id)
        
        # 3. Set sender details
        await self._set_from_address(draft_id, from_email, from_name)
        
        return {
            "draft_id": draft_id,
            "edit_url": f"https://app.hubspot.com/email/{self.config.hubspot.portal_id}/edit/{draft_id}",
            "status": "draft"
        }
    
    async def _associate_with_list(self, draft_id: str, list_id: str):
        """Associate email draft with recipient list."""
        pass
    
    async def _set_from_address(self, draft_id: str, email: str, name: str):
        """Set from email and name."""
        pass
    
    async def get_draft_url(self, draft_id: str) -> str:
        """Get edit URL for draft in HubSpot UI."""
        return f"https://app.hubspot.com/email/{self.config.hubspot.portal_id}/edit/{draft_id}"
```

### Phase 2: CLI Tool

**File:** `corev2/email/cli.py`

**Usage:**
```bash
python -m corev2.email.cli create-campaign \
  --name "Monthly Newsletter - March 2026" \
  --subject "🚀 What's New in Property Tech" \
  --list 718 \
  --template templates/newsletter.html \
  --from-email "updates@solacegroup.com" \
  --from-name "Solace Group Team"
```

**Output:**
```
✅ Email draft created successfully!

   Draft ID: 123456789
   Campaign: Monthly Newsletter - March 2026
   Recipients: List 718 (General) - 2,043 contacts
   
   🔗 Edit & Send: https://app.hubspot.com/email/12345/edit/123456789
   
   📋 Next Steps:
   1. Click the link above
   2. Review content and recipients
   3. Click "Send" or "Schedule"
   
   ⏱️  Estimated send time: ~30 minutes for 2,043 contacts
```

### Phase 3: Template System

**File:** `corev2/email/templates/`

**Structure:**
```
templates/
├── base.html           # Base template with header/footer
├── newsletter.html     # Newsletter template
├── event.html          # Event invitation template
└── announcement.html   # General announcement template
```

**Template Variables:**
```html
<!-- templates/newsletter.html -->
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{{ subject }}</title>
</head>
<body>
    <h1>{{ headline }}</h1>
    <p>Hi {{ contact.firstname }},</p>
    
    {{ content }}
    
    <footer>
        <p>
            You're receiving this because you're on our {{ list_name }} list.
            <a href="{{ unsubscribe_link }}">Unsubscribe</a>
        </p>
    </footer>
</body>
</html>
```

**Rendering:**
```python
from jinja2 import Template

template = Template(open("templates/newsletter.html").read())
html = template.render(
    subject="March Newsletter",
    headline="Property Tech Updates",
    content=newsletter_content,
    list_name="General Marketing",
    unsubscribe_link="{{ unsubscribe_link }}"  # HubSpot token
)
```

### Phase 4: Safety & Compliance

**Pre-Send Validation:**
```python
async def validate_campaign(
    self,
    draft_id: str,
    target_list_id: str
) -> Dict[str, Any]:
    """
    Validate campaign before human sends.
    
    Checks:
    - No contacts in exclusion lists (762, 773, 717)
    - All recipients have valid email addresses
    - From address is verified in HubSpot
    - Unsubscribe link present
    - Subject line not empty
    - HTML content not empty
    
    Returns validation report.
    """
    
    issues = []
    
    # 1. Check exclusion lists
    list_members = await self._get_list_members(target_list_id)
    opted_out = await self._check_opt_out_status(list_members)
    
    if opted_out:
        issues.append({
            "severity": "ERROR",
            "message": f"{len(opted_out)} contacts are opted out and will not receive email",
            "contacts": opted_out[:10]  # Sample
        })
    
    # 2. Check unsubscribe link
    html_content = await self._get_draft_content(draft_id)
    if "{{ unsubscribe_link }}" not in html_content and "{{unsubscribe_link}}" not in html_content:
        issues.append({
            "severity": "ERROR",
            "message": "Missing unsubscribe link - required by law"
        })
    
    # 3. Check from address
    from_email = await self._get_from_email(draft_id)
    if not await self._is_verified_sender(from_email):
        issues.append({
            "severity": "WARNING",
            "message": f"From address {from_email} may not be verified - check HubSpot settings"
        })
    
    return {
        "valid": len([i for i in issues if i['severity'] == 'ERROR']) == 0,
        "issues": issues,
        "estimated_recipients": len(list_members) - len(opted_out)
    }
```

### Phase 5: Integration with Existing System

**Hook into main.py:**
```python
# main.py

# STEP 5: Email Campaign Management (if drafts requested)
if config.email.campaigns_to_create:
    logger.info("\n" + "="*70)
    logger.info("STEP 5: Email Campaign Draft Creation")
    logger.info("="*70)
    
    from corev2.email.campaign_drafter import HubSpotCampaignDrafter
    
    drafter = HubSpotCampaignDrafter(config, hs_client)
    
    for campaign_config in config.email.campaigns_to_create:
        logger.info(f"\nCreating draft: {campaign_config['name']}")
        
        draft = await drafter.create_email_draft(
            campaign_name=campaign_config['name'],
            subject=campaign_config['subject'],
            preview_text=campaign_config.get('preview', ''),
            html_content=campaign_config['html'],
            text_content=campaign_config.get('text', ''),
            target_list_id=campaign_config['list_id'],
            from_email=campaign_config['from_email'],
            from_name=campaign_config.get('from_name', 'Solace Group')
        )
        
        # Validate
        validation = await drafter.validate_campaign(
            draft['draft_id'],
            campaign_config['list_id']
        )
        
        if validation['valid']:
            logger.info(f"✅ Draft created successfully")
            logger.info(f"   Edit & Send: {draft['edit_url']}")
            logger.info(f"   Recipients: {validation['estimated_recipients']}")
        else:
            logger.warning(f"⚠️  Draft created with validation issues:")
            for issue in validation['issues']:
                logger.warning(f"   [{issue['severity']}] {issue['message']}")
else:
    logger.info("\nℹ️  No email campaigns configured")
```

---

## 📝 Configuration Schema

**File:** `corev2/config/production.yaml`

```yaml
# Email campaign configuration
email:
  # Enable email draft creation
  enabled: false
  
  # Default from address (must be verified in HubSpot)
  default_from_email: "updates@solacegroup.com"
  default_from_name: "Solace Group Team"
  
  # Portal ID for URL generation
  portal_id: "12345678"
  
  # Campaigns to create on next run (cleared after creation)
  campaigns_to_create: []
  
  # Example campaign configuration:
  # campaigns_to_create:
  #   - name: "March Newsletter"
  #     subject: "🚀 Property Tech Updates"
  #     preview: "See what's new this month"
  #     list_id: "718"
  #     template: "templates/newsletter.html"
  #     variables:
  #       headline: "What's New in March"
  #       content: "..."
  #     from_email: "updates@solacegroup.com"
  #     from_name: "Solace Team"
```

**File:** `corev2/config/schema.py`

```python
class EmailCampaignConfig(BaseModel):
    """Single email campaign configuration"""
    name: str
    subject: str
    preview: Optional[str] = ""
    list_id: str
    template: str
    variables: Dict[str, Any] = {}
    from_email: str
    from_name: Optional[str] = "Solace Group Team"

class EmailConfig(BaseModel):
    """Email system configuration"""
    enabled: bool = False
    default_from_email: str
    default_from_name: str = "Solace Group Team"
    portal_id: str
    campaigns_to_create: List[EmailCampaignConfig] = []

class V2Config(BaseModel):
    hubspot: HubSpotConfig
    mailchimp: MailchimpConfig
    sync: SyncConfig
    email: EmailConfig  # NEW
    # ... rest of config
```

---

## 🔒 Security & Compliance

### 1. Opt-Out Enforcement

```python
# NEVER send to opted-out contacts
async def filter_opted_out_contacts(self, contact_ids: List[str]) -> List[str]:
    """
    Remove opted-out contacts from recipient list.
    Checks:
    - Communication Preferences opt-out status
    - Exclusion list membership (762, 773, 717)
    """
    filtered = []
    
    for contact_id in contact_ids:
        # Check opt-out status
        opt_status = await self.hs.get(
            f"/communication-preferences/v3/status/email/{contact_id}"
        )
        
        if opt_status.get('data', {}).get('opted_out'):
            continue  # Skip opted-out
        
        # Check exclusion lists
        lists = await self._get_contact_lists(contact_id)
        if any(list_id in ['762', '773', '717'] for list_id in lists):
            continue  # Skip excluded
        
        filtered.append(contact_id)
    
    return filtered
```

### 2. Unsubscribe Link Requirement

```python
# Validate unsubscribe link present
def validate_unsubscribe_link(html_content: str) -> bool:
    """Ensure unsubscribe link is present in email."""
    required_tokens = [
        "{{ unsubscribe_link }}",
        "{{unsubscribe_link}}",
        "{% unsubscribe_url %}"
    ]
    
    return any(token in html_content for token in required_tokens)
```

### 3. Rate Limiting

```python
# Respect HubSpot rate limits
# Marketing Email API: 250 requests/10 seconds
class HubSpotEmailClient(HubSpotClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.email_rate_limit = 25.0  # 25 req/sec (safe margin)
```

---

## 🧪 Testing Strategy

### Unit Tests

```python
# tests/test_email_drafter.py

@pytest.mark.asyncio
async def test_create_draft():
    """Test email draft creation"""
    config = load_test_config()
    hs_client = MockHubSpotClient()
    
    drafter = HubSpotCampaignDrafter(config, hs_client)
    
    draft = await drafter.create_email_draft(
        campaign_name="Test Campaign",
        subject="Test Subject",
        preview_text="Preview",
        html_content="<h1>Test</h1>",
        text_content="Test",
        target_list_id="718",
        from_email="test@example.com",
        from_name="Test Sender"
    )
    
    assert draft['draft_id'] is not None
    assert draft['status'] == 'draft'
    assert 'edit_url' in draft

@pytest.mark.asyncio
async def test_validate_campaign_missing_unsubscribe():
    """Test validation catches missing unsubscribe link"""
    drafter = HubSpotCampaignDrafter(config, hs_client)
    
    validation = await drafter.validate_campaign(
        draft_id="123",
        html_content="<h1>No unsubscribe link</h1>",
        target_list_id="718"
    )
    
    assert not validation['valid']
    assert any('unsubscribe' in issue['message'].lower() 
               for issue in validation['issues'])
```

### Integration Tests

```python
# tests/integration/test_email_workflow.py

@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_campaign_workflow():
    """Test complete campaign creation workflow"""
    # 1. Create draft
    draft = await create_test_campaign()
    
    # 2. Validate
    validation = await validate_campaign(draft['draft_id'])
    assert validation['valid']
    
    # 3. Check draft accessible in HubSpot API
    draft_details = await get_draft_details(draft['draft_id'])
    assert draft_details['state'] == 'DRAFT'
    
    # 4. Cleanup
    await delete_draft(draft['draft_id'])
```

---

## 📊 Success Metrics

### Functional Requirements
- ✅ Create email drafts via API
- ✅ Associate with HubSpot lists
- ✅ Set HTML + text content
- ✅ Validate opt-out compliance
- ✅ Generate edit URLs for manual send
- ✅ Support template rendering
- ✅ Respect exclusion lists

### Performance Requirements
- ✅ Draft creation < 5 seconds
- ✅ Validation scan < 10 seconds for 2000 contacts
- ✅ Respects HubSpot rate limits
- ✅ No API throttling errors

### Usability Requirements
- ✅ Clear CLI interface
- ✅ Actionable error messages
- ✅ Edit URL provided for easy access
- ✅ Validation warnings before send
- ✅ Recipient count displayed

---

## 🚧 Known Limitations

1. **Manual Send Required**
   - HubSpot API does not allow programmatic send
   - Human must click "Send" in UI
   - This is intentional anti-spam protection

2. **Template Feature Limitations**
   - Cannot create custom email templates via API
   - Must use drag-and-drop editor or HTML
   - Template variables limited to HubSpot tokens

3. **Scheduling**
   - Cannot schedule sends via API
   - Must schedule in HubSpot UI after draft creation

4. **A/B Testing**
   - A/B test setup requires HubSpot UI
   - Cannot configure via API

5. **Analytics**
   - Email analytics available in HubSpot UI
   - API access to analytics is read-only post-send

---

## 🎯 Alternative: Mailchimp Campaign API

**If full automation is required:**

```python
# Use Mailchimp instead for fully automated sends

from corev2.clients.mailchimp_client import MailchimpClient

async def send_mailchimp_campaign(
    mc_client: MailchimpClient,
    subject: str,
    html_content: str,
    list_tag: str  # "General", "Recruitment", etc.
):
    """
    Send campaign via Mailchimp (fully automated).
    """
    
    # 1. Create campaign
    campaign = await mc_client.post("/campaigns", json={
        "type": "regular",
        "recipients": {
            "segment_opts": {
                "match": "all",
                "conditions": [{
                    "condition_type": "StaticSegment",
                    "field": "static_segment",
                    "op": "static_is",
                    "value": list_tag
                }]
            }
        },
        "settings": {
            "subject_line": subject,
            "from_name": "Solace Group",
            "reply_to": "updates@solacegroup.com",
            "title": f"Campaign - {subject}"
        }
    })
    
    campaign_id = campaign['data']['id']
    
    # 2. Set content
    await mc_client.put(f"/campaigns/{campaign_id}/content", json={
        "html": html_content
    })
    
    # 3. Send immediately
    await mc_client.post(f"/campaigns/{campaign_id}/actions/send")
    
    return campaign_id
```

**Pros:**
- ✅ Fully automated (no manual step)
- ✅ Rich analytics
- ✅ A/B testing via API
- ✅ Scheduling via API
- ✅ Better deliverability

**Cons:**
- ❌ Requires Mailchimp subscription
- ❌ Contacts must be synced first
- ❌ Another platform to manage

---

## 🔄 Development Workflow

### Step 1: Research & Planning ✅
- Understand HubSpot Email API limitations
- Document what's possible vs. what's not
- Decide on semi-automated vs. Mailchimp approach

### Step 2: Core Implementation
1. Create `corev2/email/campaign_drafter.py`
2. Implement draft creation
3. Add list association
4. Add validation logic

### Step 3: CLI Tool
1. Create `corev2/email/cli.py`
2. Add `create-campaign` command
3. Add argument parsing
4. Add output formatting

### Step 4: Template System
1. Create `corev2/email/templates/` directory
2. Add base template with header/footer
3. Add campaign-specific templates
4. Implement Jinja2 rendering

### Step 5: Integration
1. Add to `main.py` workflow
2. Add configuration schema
3. Add to production.yaml
4. Update documentation

### Step 6: Testing
1. Unit tests for drafter
2. Integration tests for API calls
3. Manual test with real HubSpot account
4. Validate end-to-end workflow

### Step 7: Documentation
1. Update README with email capabilities
2. Add example configurations
3. Document manual send process
4. Create troubleshooting guide

---

## 📚 Resources & References

### HubSpot Documentation
- [Marketing Email API](https://developers.hubspot.com/docs/api/marketing/marketing-email)
- [Transactional Email API](https://developers.hubspot.com/docs/api/marketing/transactional-email)
- [Communication Preferences API](https://developers.hubspot.com/docs/api/marketing/subscriptions)

### Existing Codebase
- `corev2/clients/hubspot_client.py` - Base HTTP client
- `corev2/clients/mailchimp_client.py` - Mailchimp integration
- `corev2/config/schema.py` - Configuration models
- `main.py` - Main entry point

### Similar Implementations
- `corev2/planner/primary.py` - Example of planner pattern
- `corev2/executor/engine.py` - Example of executor pattern
- `corev2/sync/unsubscribe_sync.py` - Example of compliance sync

---

## ✅ Implementation Checklist

### Prerequisites
- [ ] HubSpot Private App token with Marketing Email scopes
- [ ] Verified sender email address in HubSpot
- [ ] Test list created in HubSpot (with test contacts only)
- [ ] Email templates designed (HTML + text versions)

### Core Features
- [ ] Draft creation API integration
- [ ] List association logic
- [ ] Content setting (HTML + text)
- [ ] From address configuration
- [ ] Subject line configuration
- [ ] Preview text configuration

### Validation & Safety
- [ ] Opt-out status checking
- [ ] Exclusion list filtering
- [ ] Unsubscribe link validation
- [ ] From address verification
- [ ] Recipient count estimation
- [ ] Error handling for API failures

### User Interface
- [ ] CLI command for draft creation
- [ ] Template rendering with Jinja2
- [ ] Clear output with edit URL
- [ ] Validation report display
- [ ] Recipient count display

### Testing
- [ ] Unit tests for drafter class
- [ ] Mock HubSpot responses
- [ ] Validation logic tests
- [ ] Template rendering tests
- [ ] Integration test with test list
- [ ] End-to-end workflow test

### Documentation
- [ ] README section on email campaigns
- [ ] Configuration examples
- [ ] Template creation guide
- [ ] Manual send process docs
- [ ] Troubleshooting guide

---

## 🚀 Quick Start for Agent

**Your first task:**

1. Read this brief thoroughly
2. Review `corev2/clients/hubspot_client.py` to understand HTTP client structure
3. Create `corev2/email/campaign_drafter.py` with basic draft creation
4. Test against HubSpot API (use test list, not production)
5. Report back with:
   - Draft creation working? (yes/no)
   - Edit URL generated? (yes/no)
   - Any API errors encountered?
   - Next blockers or questions?

**Success Criteria:**
You've succeeded when you can run a Python script that creates a draft email in HubSpot, associates it with a test list, and provides a clickable URL for the user to review and send.

**Time Estimate:** 2-4 hours for core functionality

**Remember:**
- Semi-automated is OKAY (manual send is required by HubSpot)
- Safety first (never email opted-out contacts)
- Clear error messages (user needs to know what went wrong)
- Test with small lists first (10-20 contacts)

Good luck! 🚀
