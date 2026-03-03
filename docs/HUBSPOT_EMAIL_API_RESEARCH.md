# HubSpot Email API Research Brief

**Research Date:** February 24, 2026  
**Requested By:** Operations Team  
**Purpose:** Enable bulk email sending directly from HubSpot

---

## 1. Executive Summary

### Can We Do It?
**YES, but with important limitations:**
- ✅ HubSpot supports sending emails to lists via Marketing Email API
- ✅ Compatible with Private App authentication
- ❌ **CRITICAL:** Marketing emails require manual review/approval in HubSpot UI
- ❌ **CRITICAL:** Cannot be 100% automated via API (requires human in the loop)
- ✅ Transactional emails can be fully automated but NOT suitable for bulk marketing

### Best Method for Bulk Email to List 718 (~2000 contacts):
**Marketing Email API** - but requires hybrid approach:
1. API creates email draft + associates with list
2. Human logs into HubSpot to review & send
3. OR use Transactional Email API with templates (see limitations below)

### Bottom Line:
If you need **fully automated bulk emails**, HubSpot's Marketing Email API won't work alone. You'd need to either:
- Keep using Mailchimp for automated marketing campaigns
- Use HubSpot's Transactional Email API (but it's not designed for marketing)
- Accept manual review step in HubSpot UI

---

## 2. API Options

### Option A: Marketing Email API (v3)
**Purpose:** Create and send marketing emails/campaigns to lists  
**Best For:** Newsletters, promotions, announcements to segmented audiences

#### Endpoint Details:
```
BASE URL: https://api.hubapi.com/marketing/v3/emails

CREATE DRAFT:
POST /marketing/v3/emails
- Creates email draft

PUBLISH EMAIL:
POST /marketing/v3/emails/{emailId}/publish
- Publishes draft (makes it ready to send)

SEND EMAIL:
POST /marketing/v3/emails/{emailId}/send
- Sends published email to recipients
```

#### Authentication:
```python
headers = {
    "Authorization": f"Bearer {private_app_token}",
    "Content-Type": "application/json"
}
```

#### Request Payload Structure:
```json
{
  "name": "February Newsletter",
  "subject": "Important Updates for February 2026",
  "emailBody": "<html><body><h1>Hello {{contact.firstname}}</h1><p>Your content here</p></body></html>",
  "emailType": "BATCH_EMAIL",
  "fromName": "Solace Group",
  "replyTo": "noreply@solacegroup.com",
  "subscriptionId": 12345,  // Required: email subscription type
  "listIds": [718],  // Your target list
  "domain": "solacegroup.com"  // Sending domain
}
```

#### Prerequisites:
1. **Email Subscription Type** - Must exist in HubSpot (Settings > Marketing > Email > Subscriptions)
2. **Verified Sending Domain** - Must verify domain ownership (Settings > Marketing > Email > Sending Domains)
3. **Email Content Requirements:**
   - Must include unsubscribe link
   - Must comply with CAN-SPAM requirements
   - HubSpot auto-adds compliance elements

4. **Template (Optional but Recommended):**
   - Can use HubSpot email templates created in UI
   - Or send raw HTML with HubSpot tokens

#### Rate Limits:
- **100 requests per 10 seconds** (same as other HubSpot APIs)
- **Per portal daily send limits:**
  - Marketing Hub Starter: 1,000 emails/day
  - Marketing Hub Professional: 250,000 emails/day
  - Marketing Hub Enterprise: 1,000,000 emails/day

#### Critical Limitations:
1. **APPROVAL REQUIRED:** Marketing emails created via API go to "Draft" state
   - Must be manually reviewed in HubSpot UI
   - Must click "Send" or "Schedule" in UI
   - **Cannot fully automate the send process**

2. **Recipients Must Be HubSpot Contacts:**
   - All recipients must exist in HubSpot CRM
   - Must be in specified list(s)
   - Cannot send to arbitrary email addresses

3. **Subscription Type Required:**
   - Contacts must be subscribed to the subscription type
   - HubSpot enforces opt-out preferences
   - Will automatically exclude unsubscribed contacts

4. **Domain Verification:**
   - Sending domain must be verified in HubSpot
   - DNS records must be configured
   - One-time setup but critical

---

### Option B: Transactional Email API (Single Send API v3)
**Purpose:** Send one-off transactional emails (receipts, confirmations, alerts)  
**Best For:** Automated individual messages, NOT bulk marketing

#### Endpoint Details:
```
POST https://api.hubapi.com/crm/v3/objects/emails
- Creates email engagement on contact record

OR (newer single send):
POST https://api.hubapi.com/marketing/v3/transactional/single-email/send
- Sends transactional email using template
```

#### Authentication:
```python
headers = {
    "Authorization": f"Bearer {private_app_token}",
    "Content-Type": "application/json"
}
```

#### Request Payload Structure:
```json
{
  "emailId": 123456789,  // Email template ID
  "message": {
    "to": "recipient@example.com",
    "from": "noreply@solacegroup.com",
    "sendId": "unique-send-identifier"
  },
  "contactProperties": {
    "firstname": "John",
    "lastname": "Doe"
  },
  "customProperties": {
    "order_number": "12345",
    "custom_field": "value"
  }
}
```

#### Prerequisites:
1. **Transactional Email Template:**
   - Must create template in HubSpot UI (Marketing > Email > Templates)
   - Get template's `emailId` from URL or API
   - Templates use HubSpot's drag-and-drop or custom HTML

2. **Sending Domain Verification** (same as Marketing Email)

3. **Contact Existence (Recommended):**
   - Best practice to send to existing contacts
   - Can create contact on-the-fly if needed

#### Rate Limits:
- **100 requests per 10 seconds** (standard HubSpot limit)
- **Daily send limits based on tier:**
  - Free/Starter: Limited (exact limit varies)
  - Professional: Higher limits
  - Enterprise: Highest limits

#### Critical Limitations:
1. **NOT FOR BULK MARKETING:**
   - Designed for 1:1 transactional emails
   - Sending to 2000 recipients would require 2000 API calls
   - Would take ~20 minutes minimum (rate limit constraints)
   - Violates HubSpot's acceptable use policy if used for marketing

2. **Template Required:**
   - Cannot send arbitrary HTML
   - Must use predefined HubSpot templates
   - Templates must be created in UI first

3. **No Automatic List Expansion:**
   - Must specify each recipient individually
   - Cannot say "send to List 718"
   - Would need to fetch list members then loop

4. **Compliance Risk:**
   - Transactional emails bypass opt-out preferences
   - Using for marketing could violate CAN-SPAM/GDPR
   - Could get HubSpot account flagged

---

### Option C: Email Engagement via CRM API (Logging Only)
**Purpose:** Log email sends in contact timeline (NOT actual sending)  
**Best For:** Recording external email activity in HubSpot

#### Endpoint:
```
POST /crm/v3/objects/emails
```

#### What It Does:
- Creates email engagement record on contact
- Shows in contact's timeline
- **DOES NOT send actual email**
- Just for logging/tracking

#### Use Case:
If you send emails via Mailchimp and want to log them in HubSpot, you'd use this endpoint. Not relevant for your use case.

---

## 3. Recommended Approach

Given your requirement to **send bulk email to List 718 (~2000 contacts)**, here are three approaches:

### Approach 1: Semi-Automated Marketing Email (RECOMMENDED)
**Pros:** Compliant, uses proper marketing infrastructure, respects opt-outs  
**Cons:** Requires manual review/send step

**Workflow:**
1. API creates email draft with content
2. API associates draft with List 718
3. Human reviews in HubSpot UI
4. Human clicks "Send" or "Schedule"

**Best For:** Legitimate marketing campaigns where quality review is acceptable

---

### Approach 2: Hybrid - Create in HubSpot, Monitor & Send
**Pros:** More automated than Approach 1  
**Cons:** Complex, still requires some manual intervention

**Workflow:**
1. API creates draft email
2. API publishes email (if approval gates allow)
3. API sends email (if permitted by account settings)
4. Monitor via webhooks for delivery status

**Reality Check:** HubSpot's approval gates may still block this depending on account settings

---

### Approach 3: Continue Using Mailchimp for Bulk, HubSpot for CRM
**Pros:** Fully automated, proven working system  
**Cons:** Doesn't meet "send from HubSpot" requirement

**Workflow:**
1. Keep existing Mailchimp integration
2. Use HubSpot for contact management
3. Sync lists to Mailchimp for campaigns
4. Log sends back to HubSpot via CRM API

**Reality:** This is what most companies do

---

## 4. Code Example: Complete Workflow

### Scenario: Send marketing email to List 718

```python
"""
HubSpot Marketing Email - Complete Example
Sends bulk marketing email to a HubSpot list
"""

import asyncio
from typing import Dict, Optional, List
from corev2.clients.hubspot_client import HubSpotClient


class HubSpotEmailSender:
    """Send marketing emails via HubSpot Marketing Email API."""
    
    def __init__(self, api_key: str):
        self.client = HubSpotClient(api_key=api_key)
    
    async def create_email_draft(
        self,
        name: str,
        subject: str,
        html_body: str,
        from_name: str,
        reply_to: str,
        list_ids: List[int],
        subscription_id: int
    ) -> Dict:
        """
        Create email draft in HubSpot.
        
        Args:
            name: Internal name for email
            subject: Email subject line
            html_body: HTML content (can include HubSpot tokens like {{contact.firstname}})
            from_name: Sender display name
            reply_to: Reply-to email address
            list_ids: List IDs to send to (e.g., [718])
            subscription_id: Subscription type ID
        
        Returns:
            Dict with email ID and status
        """
        endpoint = "/marketing/v3/emails"
        
        payload = {
            "name": name,
            "subject": subject,
            "emailBody": html_body,
            "emailType": "BATCH_EMAIL",
            "fromName": from_name,
            "replyTo": reply_to,
            "subscriptionId": subscription_id,
            "listIds": list_ids,
            # Optional fields:
            # "domain": "yourdomain.com",  # If you have verified domain
            # "language": "en",
            # "preheader": "Preview text here",
        }
        
        result = await self.client.post(endpoint, json=payload)
        
        if result["status"] == 201:
            email_data = result["data"]
            return {
                "success": True,
                "email_id": email_data.get("id"),
                "state": email_data.get("state"),  # Should be "DRAFT"
                "message": "Email draft created successfully"
            }
        else:
            return {
                "success": False,
                "error": result["data"],
                "message": "Failed to create email draft"
            }
    
    async def publish_email(self, email_id: str) -> Dict:
        """
        Publish email draft (makes it ready to send).
        
        Note: Depending on HubSpot account settings, this may require
        manual approval in the UI.
        
        Args:
            email_id: Email ID from create_email_draft
        
        Returns:
            Dict with publication status
        """
        endpoint = f"/marketing/v3/emails/{email_id}/publish"
        
        result = await self.client.post(endpoint, json={})
        
        if result["status"] == 200:
            return {
                "success": True,
                "email_id": email_id,
                "message": "Email published successfully"
            }
        else:
            return {
                "success": False,
                "error": result["data"],
                "message": "Failed to publish email - may require manual review"
            }
    
    async def send_email(
        self,
        email_id: str,
        send_immediately: bool = True,
        scheduled_time: Optional[int] = None
    ) -> Dict:
        """
        Send published email.
        
        **WARNING:** This may fail if account requires manual approval.
        Most HubSpot accounts require clicking "Send" in UI for marketing emails.
        
        Args:
            email_id: Email ID from create_email_draft
            send_immediately: If True, sends now. If False, requires scheduled_time
            scheduled_time: Unix timestamp (milliseconds) for scheduled send
        
        Returns:
            Dict with send status
        """
        endpoint = f"/marketing/v3/emails/{email_id}/send"
        
        payload = {}
        if not send_immediately and scheduled_time:
            payload["scheduledTime"] = scheduled_time
        
        result = await self.client.post(endpoint, json=payload)
        
        if result["status"] == 200:
            return {
                "success": True,
                "email_id": email_id,
                "message": "Email sent successfully"
            }
        else:
            return {
                "success": False,
                "error": result["data"],
                "message": "Failed to send email - likely requires manual approval in UI"
            }
    
    async def get_email_details(self, email_id: str) -> Dict:
        """Get email details including send status."""
        endpoint = f"/marketing/v3/emails/{email_id}"
        
        result = await self.client.get(endpoint)
        
        if result["status"] == 200:
            return {
                "success": True,
                "data": result["data"]
            }
        else:
            return {
                "success": False,
                "error": result["data"]
            }


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

async def send_bulk_email_example():
    """
    Example: Send email to List 718
    """
    
    # Initialize client
    api_key = "pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"  # Your Private App token
    sender = HubSpotEmailSender(api_key)
    
    # Email content
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
    </head>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h1>Hello {{contact.firstname}}!</h1>
        
        <p>We hope this email finds you well.</p>
        
        <p>This is an important update from Solace Group regarding your account.</p>
        
        <p>Best regards,<br>
        The Solace Team</p>
        
        <!-- HubSpot automatically adds unsubscribe link -->
    </body>
    </html>
    """
    
    # Step 1: Create email draft
    print("Creating email draft...")
    create_result = await sender.create_email_draft(
        name="February 2026 Newsletter - List 718",
        subject="Important Update from Solace Group",
        html_body=html_content,
        from_name="Solace Group",
        reply_to="info@solacegroup.com",
        list_ids=[718],  # Your list ID
        subscription_id=12345  # REPLACE with your actual subscription type ID
    )
    
    if not create_result["success"]:
        print(f"Failed to create draft: {create_result['error']}")
        return
    
    email_id = create_result["email_id"]
    print(f"✅ Created email draft: {email_id}")
    print(f"   State: {create_result['state']}")
    
    # Step 2: Publish email
    print("\nPublishing email...")
    publish_result = await sender.publish_email(email_id)
    
    if not publish_result["success"]:
        print(f"⚠️  Failed to publish: {publish_result['error']}")
        print("   You may need to manually publish in HubSpot UI")
        print(f"   Go to: Marketing > Email > Email Tools > {email_id}")
        return
    
    print(f"✅ Published email: {email_id}")
    
    # Step 3: Send email
    print("\nAttempting to send email...")
    send_result = await sender.send_email(email_id, send_immediately=True)
    
    if not send_result["success"]:
        print(f"⚠️  Failed to send: {send_result['error']}")
        print("   MANUAL ACTION REQUIRED:")
        print(f"   1. Go to HubSpot: Marketing > Email")
        print(f"   2. Find email: {email_id}")
        print(f"   3. Review and click 'Send'")
        return
    
    print(f"✅ Email sent successfully!")
    
    # Step 4: Check status
    print("\nChecking email status...")
    details_result = await sender.get_email_details(email_id)
    
    if details_result["success"]:
        email_data = details_result["data"]
        print(f"   State: {email_data.get('state')}")
        print(f"   Send time: {email_data.get('publishDate')}")
        print(f"   Recipients: {email_data.get('counters', {}).get('sent', 'N/A')}")


async def find_subscription_types():
    """
    Helper: Find available subscription types in HubSpot.
    
    You need a subscription type ID to send marketing emails.
    This endpoint lists all available types.
    """
    api_key = "pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    client = HubSpotClient(api_key=api_key)
    
    # Note: This endpoint may require different permissions
    endpoint = "/email/public/v1/subscriptions"
    
    result = await client.get(endpoint)
    
    if result["status"] == 200:
        subscriptions = result["data"].get("subscriptionDefinitions", [])
        print("Available subscription types:")
        for sub in subscriptions:
            print(f"  - ID: {sub.get('id')} | Name: {sub.get('name')} | Type: {sub.get('type')}")
    else:
        print(f"Failed to fetch subscriptions: {result['data']}")


# ============================================================================
# ALTERNATIVE: Transactional Email (NOT RECOMMENDED FOR BULK)
# ============================================================================

async def send_transactional_email_to_list_INEFFICIENT():
    """
    Example: Send transactional email to list members.
    
    **NOT RECOMMENDED** - This is inefficient and violates HubSpot's acceptable use.
    Shown for completeness only.
    """
    api_key = "pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    client = HubSpotClient(api_key=api_key)
    
    # Step 1: Fetch all members of List 718
    print("Fetching list members...")
    members = []
    async for contact in client.get_list_members("718", properties=["email", "firstname", "lastname"]):
        members.append(contact)
    
    print(f"Found {len(members)} contacts in list")
    
    # Step 2: Send transactional email to each (SLOW!)
    template_id = 123456789  # Your transactional template ID
    
    sent_count = 0
    failed_count = 0
    
    for contact in members:
        email = contact.get("email")
        if not email:
            continue
        
        # Send individual transactional email
        endpoint = "/marketing/v3/transactional/single-email/send"
        
        payload = {
            "emailId": template_id,
            "message": {
                "to": email,
                "from": "noreply@solacegroup.com"
            },
            "contactProperties": {
                "firstname": contact.get("firstname", ""),
                "lastname": contact.get("lastname", "")
            }
        }
        
        result = await client.post(endpoint, json=payload)
        
        if result["status"] == 200:
            sent_count += 1
        else:
            failed_count += 1
            print(f"Failed to send to {email}: {result['data']}")
        
        # Rate limiting - this will take ~20 minutes for 2000 contacts!
        await asyncio.sleep(0.1)
    
    print(f"\nResults: {sent_count} sent, {failed_count} failed")
    print(f"⚠️  This approach is NOT recommended for marketing emails!")


# Run example
if __name__ == "__main__":
    asyncio.run(send_bulk_email_example())
    # asyncio.run(find_subscription_types())
```

---

## 5. Gotchas & Limitations

### Critical Issues:

1. **Manual Approval Required for Marketing Emails**
   - Most HubSpot accounts require manual review before sending
   - Cannot be fully automated via API
   - Must log into HubSpot UI to click "Send"
   - **This is the biggest blocker for full automation**

2. **Subscription Type ID Required**
   - Must find your subscription type ID first
   - Use `/email/public/v1/subscriptions` endpoint
   - Common types: "Blog Subscription", "Marketing Updates", etc.
   - If you don't have one, create in HubSpot UI first

3. **Domain Verification is Mandatory**
   - Cannot send from unverified domain
   - Requires DNS configuration (SPF, DKIM)
   - One-time setup but must be done before first send
   - Can take 24-48 hours for DNS propagation

4. **Recipients Must Be HubSpot Contacts**
   - Cannot send to arbitrary email addresses
   - All 2000 contacts must exist in your HubSpot CRM
   - Must be members of the target list
   - Good news: You already have this based on your codebase

5. **Opt-Out Enforcement**
   - HubSpot automatically excludes unsubscribed contacts
   - Cannot override this (compliance protection)
   - Final send count may be less than list size
   - Check subscription status before expecting delivery

### Performance Considerations:

6. **Rate Limits Apply to All Operations**
   - Creating draft, publishing, sending all count toward limit
   - 100 requests per 10 seconds
   - For bulk operations, stay well below limit
   - Add delays between API calls

7. **Email Content Requirements**
   - Must include proper HTML structure
   - HubSpot tokens use `{{contact.property}}` syntax
   - Unsubscribe link auto-added (don't add manually)
   - Test emails first before sending to full list

8. **No Built-in A/B Testing via API**
   - A/B tests must be configured in UI
   - Cannot create A/B test variants via API
   - If you need this, use UI workflow

### API Quirks:

9. **Email IDs vs Template IDs**
   - Marketing Email API uses "email ID" (numeric)
   - Transactional API uses "template ID" (different numeric)
   - Don't confuse the two
   - Get IDs from respective endpoints

10. **State Transitions**
    - Emails go through states: DRAFT → PUBLISHED → SENT
    - Cannot skip states
    - Cannot send unpublished email
    - Cannot edit published email (must clone)

11. **Private App Token Permissions**
    - Ensure token has `marketing_email` scope
    - May need to recreate token with additional scopes
    - Check in HubSpot: Settings > Integrations > Private Apps

12. **Error Messages Can Be Vague**
    - "Bad Request" errors don't always specify what's wrong
    - Common causes: missing subscription ID, unverified domain, invalid list ID
    - Check all prerequisites before debugging

### Alternative Solutions:

13. **If You Need Full Automation:**
    - Consider HubSpot Workflows instead of API
    - Workflows can auto-send emails based on triggers
    - Set up: List membership = trigger → Send email
    - Fully automated, no API needed
    - But requires configuration in HubSpot UI

14. **Hybrid Approach:**
    - Use API to create draft + associate with list
    - Use HubSpot Workflow to auto-send when published
    - Requires one-time workflow setup
    - Then API can trigger by publishing email

15. **Continue Using Mailchimp:**
    - Your existing sync system works
    - Mailchimp has better bulk email automation
    - HubSpot is better for CRM, not always for email
    - Many companies use both in tandem

---

## 6. Required Pre-Setup Checklist

Before attempting to send your first email via API, complete these in HubSpot UI:

- [ ] **Verify Sending Domain**
  - Go to: Settings > Marketing > Email > Sending Domains
  - Add your domain (e.g., solacegroup.com)
  - Configure DNS records (SPF, DKIM, DMARC)
  - Wait for verification (can take 24-48 hours)

- [ ] **Create/Verify Subscription Type**
  - Go to: Settings > Marketing > Email > Subscriptions
  - Create subscription type (e.g., "Newsletter")
  - Note the subscription ID (need for API)
  - Ensure contacts are subscribed to this type

- [ ] **Create Private App with Correct Scopes**
  - Go to: Settings > Integrations > Private Apps
  - Create new app or edit existing
  - Enable scopes:
    - `crm.objects.contacts.read`
    - `crm.lists.read`
    - `marketing_email.read`
    - `marketing_email.write`
  - Copy token (starts with `pat-na1-...`)

- [ ] **Create Email Template (Optional but Recommended)**
  - Go to: Marketing > Email > Templates
  - Create new template with drag-and-drop or HTML
  - Test template with sample contact
  - Note template ID if using transactional API

- [ ] **Test with Small List First**
  - Create test list with 5-10 contacts (including yourself)
  - Send via API to test list
  - Verify delivery and formatting
  - Check spam folder
  - Only then proceed to larger lists

- [ ] **Configure Email Sending Settings**
  - Go to: Settings > Marketing > Email
  - Verify "From Name" and "From Email"
  - Set up footer (company address, etc.)
  - Configure unsubscribe settings

---

## 7. Recommended Implementation Path

### Phase 1: Setup & Testing (Week 1)
1. Complete pre-setup checklist above
2. Verify domain and DNS records
3. Create test subscription type
4. Create test email template
5. Test API with single email to yourself

### Phase 2: Integration (Week 2)
1. Add email methods to your `HubSpotClient` class
2. Implement `create_email_draft()` method
3. Implement `publish_email()` method
4. Implement `get_email_details()` for monitoring
5. Add error handling and logging

### Phase 3: Workflow Decision (Week 3)
**Option A: Accept Manual Review**
- API creates draft
- Human reviews in UI
- Human clicks Send
- Simplest, most compliant

**Option B: Hybrid with Workflows**
- API creates draft
- HubSpot Workflow auto-sends on publish
- One-time workflow setup
- Then fully automated

**Option C: Continue Mailchimp**
- Keep existing system
- HubSpot for CRM only
- Mailchimp for email campaigns
- Sync between systems

### Phase 4: Production Rollout (Week 4)
1. Test with small list (50-100 contacts)
2. Monitor delivery rates
3. Check spam reports
4. Collect feedback
5. Scale to full lists

---

## 8. Code Integration into Your Existing System

To integrate into your current codebase, add these methods to [corev2/clients/hubspot_client.py](corev2/clients/hubspot_client.py):

```python
# Add to HubSpotClient class

async def create_marketing_email(
    self,
    name: str,
    subject: str,
    html_body: str,
    from_name: str,
    reply_to: str,
    list_ids: List[int],
    subscription_id: int
) -> Dict[str, Any]:
    """Create marketing email draft."""
    endpoint = "/marketing/v3/emails"
    
    payload = {
        "name": name,
        "subject": subject,
        "emailBody": html_body,
        "emailType": "BATCH_EMAIL",
        "fromName": from_name,
        "replyTo": reply_to,
        "subscriptionId": subscription_id,
        "listIds": list_ids
    }
    
    return await self.post(endpoint, json=payload)

async def publish_marketing_email(self, email_id: str) -> Dict[str, Any]:
    """Publish marketing email draft."""
    endpoint = f"/marketing/v3/emails/{email_id}/publish"
    return await self.post(endpoint, json={})

async def send_marketing_email(
    self,
    email_id: str,
    scheduled_time: Optional[int] = None
) -> Dict[str, Any]:
    """Send published marketing email."""
    endpoint = f"/marketing/v3/emails/{email_id}/send"
    
    payload = {}
    if scheduled_time:
        payload["scheduledTime"] = scheduled_time
    
    return await self.post(endpoint, json=payload)

async def get_marketing_email(self, email_id: str) -> Dict[str, Any]:
    """Get marketing email details."""
    endpoint = f"/marketing/v3/emails/{email_id}"
    return await self.get(endpoint)
```

---

## 9. Final Recommendation

### For sending bulk email to List 718 (~2000 contacts):

**Use Marketing Email API with semi-automation:**

1. **Create Python script** that:
   - Generates email content
   - Calls API to create draft
   - Associates with List 718
   - Logs email ID

2. **Manual review step:**
   - Human logs into HubSpot
   - Reviews email preview
   - Clicks "Send" or "Schedule"

3. **Why this approach:**
   - ✅ Compliant with HubSpot policies
   - ✅ Respects contact opt-out preferences
   - ✅ Works within HubSpot's design
   - ✅ Quality control before send
   - ✅ Uses your existing contact data
   - ⚠️  Requires human in the loop (but only 2 minutes of work)

### Alternative: If you must have 100% automation:

**Keep using Mailchimp for campaigns, HubSpot for CRM:**
- Your current sync system works well
- Mailchimp is designed for automated marketing campaigns
- HubSpot is designed for CRM + manual campaign review
- Many companies use both tools together
- Sync lists from HubSpot → Mailchimp → Send → Log back to HubSpot

---

## Questions for Your Team:

1. **Can you accept a manual review step before sending?**
   - If YES → Use Marketing Email API (recommended)
   - If NO → Continue with Mailchimp or explore HubSpot Workflows

2. **What subscription types exist in your HubSpot?**
   - Need to find subscription type ID
   - Run the helper script to list them

3. **Is your sending domain verified?**
   - Check Settings > Marketing > Email > Sending Domains
   - If not, start DNS verification process now

4. **What's the use case for these emails?**
   - Newsletters → Marketing Email API
   - Transactional → Transactional API
   - Automated based on behavior → HubSpot Workflows

---

**Next Steps:**
1. Review this document with your team
2. Decide which approach fits your needs
3. Complete pre-setup checklist
4. Run test script with small list
5. Make go/no-go decision on HubSpot vs Mailchimp for campaigns

