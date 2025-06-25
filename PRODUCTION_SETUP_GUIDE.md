# 🎯 PRODUCTION SETUP GUIDE - How to Wire Everything

**Location**: Edit `core/config.py` - All settings are in one file!

## 📋 Step 1: Set Your Input Lists (Line ~55)

**What it does**: Tells the system which HubSpot lists to sync TO Mailchimp

```python
HUBSPOT_LIST_IDS = [
    "677",  # Your first list
    "123",  # Your second list  
    "456",  # Add as many as needed
]
```

**Real Example**:
```python
HUBSPOT_LIST_IDS = [
    "677",  # Lead Nurture List
    "890",  # Cold Prospects
    "432",  # Newsletter Subscribers
]
```

## 🏷️ Step 2: Map Exit Tags to Destination Lists (Line ~105)

**What it does**: When Mailchimp tags a contact, this tells the system which HubSpot list to move them to

```python
SECONDARY_SYNC_MAPPINGS = {
    "qualified_leads": "680",      # Tag → List ID
    "hot_prospects": "681",        # Tag → List ID
    "converted": "682",            # Tag → List ID
}
```

**Real Example**:
```python
SECONDARY_SYNC_MAPPINGS = {
    "qualified_leads": "680",      # Qualified prospects go to list 680
    "demo_booked": "681",          # Demo bookings go to list 681
    "closed_won": "682",           # Customers go to list 682
    "nurture_more": "683",         # Need more nurturing go to list 683
}
```

## 🚫 Step 3: Set Anti-Remarketing Rules (Line ~125)

**What it does**: Removes contacts from old lists when they move to new ones (prevents duplicate marketing)

```python
LIST_EXCLUSION_RULES = {
    "677": ["680", "681", "682"],  # Remove from 677 when added to any of these
    "890": ["680", "682"],         # Remove from 890 when added to these two
}
```

**Real Example**:
```python
LIST_EXCLUSION_RULES = {
    "677": ["680", "681", "682", "683"],  # Lead Nurture → Remove when qualified/demo/won/more nurture
    "890": ["680", "681", "682"],         # Cold Prospects → Remove when qualified/demo/won
    "432": ["682"],                       # Newsletter → Only remove when they become customers
}
```

## 🔄 Complete Flow Example

### Before Setup:
- HubSpot List 677 "Lead Nurture" has 1000 contacts
- HubSpot List 680 "Qualified Leads" is empty

### After Setup:
1. **Primary Sync**: Contacts from List 677 → Mailchimp (with source tracking)
2. **Mailchimp Processing**: Marketing team tags 50 contacts as "qualified_leads"
3. **Secondary Sync**: Those 50 contacts → HubSpot List 680 "Qualified Leads"
4. **Anti-Remarketing**: Those 50 contacts removed from List 677 "Lead Nurture"

### Result:
- List 677 now has 950 contacts (untouched prospects)
- List 680 now has 50 contacts (qualified prospects)
- No duplicate marketing to qualified prospects

## ⚡ Quick Commands

```bash
# Test your setup (limited contacts)
RUN_MODE="TEST_RUN" python -m core.config

# Run full production sync
python -m core.config

# Clean logs first, then run
python -m core.config --clean
```

## 🔍 How to Find Your List IDs

### HubSpot List IDs:
1. Go to HubSpot → Contacts → Lists
2. Click on a list 
3. Look at the URL: `...lists/123/contacts` → ID is `123`

### Mailchimp Tags:
1. Go to Mailchimp → Audience → Tags
2. Create tags with clear names like "qualified_leads"
3. Use these exact tag names in `SECONDARY_SYNC_MAPPINGS`

---

**That's it!** Edit those 3 sections in `core/config.py` and you're ready to sync!
