"""
Mailchimp → HubSpot unsubscribe sync module.

Scans for contacts who unsubscribed in Mailchimp and syncs that status to HubSpot.
Uses Communication Preferences API to properly opt out contacts.
"""
import asyncio
import logging
from typing import Dict, List, Any
from corev2.clients.hubspot_client import HubSpotClient
from corev2.clients.mailchimp_client import MailchimpClient
from corev2.config.schema import V2Config


logger = logging.getLogger(__name__)


# HubSpot subscription IDs (from List 443 dynamic filter)
ONE_TO_ONE_SUBSCRIPTION_ID = "289137114"  # "One to One" emails
MARKETING_SUBSCRIPTION_ID = "289137112"   # "Marketing Information"

# HubSpot List 443: "Unsubscribed/ Opted Out" dynamic list
# Filter: hs_email_optout=true OR subscription-specific opt-outs
OPTED_OUT_LIST_ID = "443"


class UnsubscribeSyncEngine:
    """Syncs Mailchimp unsubscribes to HubSpot."""
    
    def __init__(
        self,
        config: V2Config,
        hs_client: HubSpotClient,
        mc_client: MailchimpClient
    ):
        self.config = config
        self.hs_client = hs_client
        self.mc_client = mc_client
        
        # Build source tags from config
        self.source_tags = set()
        for group_name, list_configs in config.hubspot.lists.items():
            for list_config in list_configs:
                self.source_tags.add(list_config.tag)
    
    async def scan_and_sync(self) -> Dict[str, Any]:
        """
        Scan Mailchimp for unsubscribed contacts and sync to HubSpot.
        
        Returns:
            {
                "mailchimp_unsubscribed": int,
                "hubspot_updates": int,
                "skipped": int,
                "errors": List[Dict]
            }
        """
        logger.info("Starting Mailchimp → HubSpot unsubscribe sync...")
        
        summary = {
            "mailchimp_unsubscribed": 0,
            "hubspot_updates": 0,
            "skipped": 0,
            "errors": []
        }
        
        # Scan Mailchimp for ALL unsubscribed contacts
        # NOTE: We process all unsubscribed (not just those with tags) to catch compliance state
        # contacts immediately. Step 1 verification checks if contact exists in HubSpot.
        unsubscribed_contacts = []
        
        async for member in self.mc_client.get_all_members(count=500):
            member_tags = set(member.get('tags', []))
            
            # FIXED: Removed "and has_our_tags" condition to catch ALL unsubscribed
            # This prevents multi-run lag for contacts without tags yet
            if member.get('status') == 'unsubscribed':
                unsubscribed_contacts.append({
                    "email": member.get('email_address'),
                    "tags": list(member_tags)
                })
        
        summary["mailchimp_unsubscribed"] = len(unsubscribed_contacts)
        logger.info(f"Found {len(unsubscribed_contacts)} unsubscribed contacts in Mailchimp")
        
        if not unsubscribed_contacts:
            logger.info("No unsubscribes to sync")
            return summary
        
        # For each unsubscribed contact, opt them out in HubSpot
        for contact in unsubscribed_contacts:
            email = contact["email"]
            
            try:
                logger.info(f"Processing {email}...")
                
                # Get contact from HubSpot
                contact_result = await self.hs_client.get_contact_by_email(email)
                
                if not contact_result or not contact_result.get('found'):
                    logger.warning(f"  Contact {email} not found in HubSpot")
                    summary["errors"].append({
                        "email": email,
                        "error": "Contact not found in HubSpot"
                    })
                    continue
                
                vid = contact_result.get('vid') or contact_result.get('id')
                props = contact_result.get('properties', {})
                
                # Get contact phone for company cleanup
                contact_phone = props.get('phone', {}).get('value') if isinstance(props.get('phone'), dict) else props.get('phone')
                
                # Check current subscription status
                try:
                    sub_status_result = await self.hs_client.get(
                        f'/communication-preferences/v3/status/email/{email}'
                    )
                    
                    if sub_status_result.get('status') != 200:
                        logger.warning(f"  Could not get subscription status for {email}")
                        summary["skipped"] += 1
                        continue
                    
                    subscriptions = sub_status_result.get('data', {}).get('subscriptionStatuses', [])
                    
                    # Check if already unsubscribed from all
                    all_unsubscribed = all(
                        sub.get('status') in ['NOT_SUBSCRIBED', 'OPT_OUT']
                        for sub in subscriptions
                    )
                    
                    if all_unsubscribed:
                        logger.info(f"  {email} already unsubscribed - skipping")
                        summary["skipped"] += 1
                        continue
                    
                    # Unsubscribe from ALL subscription types
                    logger.info(f"  Unsubscribing {email} from all email types...")
                    unsubbed_count = 0
                    
                    for sub in subscriptions:
                        sub_id = sub.get('id')
                        sub_name = sub.get('name', 'Unknown')
                        current_status = sub.get('status')
                        
                        if current_status in ['NOT_SUBSCRIBED', 'OPT_OUT']:
                            continue
                        
                        try:
                            unsub_result = await self.hs_client.post(
                                '/communication-preferences/v3/unsubscribe',
                                json={
                                    'emailAddress': email,
                                    'subscriptionId': str(sub_id),
                                    'legalBasis': 'LEGITIMATE_INTEREST_OTHER',
                                    'legalBasisExplanation': 'Contact unsubscribed in Mailchimp'
                                }
                            )
                            
                            if unsub_result.get('status') in [200, 204]:
                                unsubbed_count += 1
                                logger.info(f"    ✓ Unsubscribed from: {sub_name}")
                        except Exception as unsub_err:
                            error_msg = str(unsub_err)
                            if "already" in error_msg.lower():
                                unsubbed_count += 1
                            else:
                                logger.warning(f"    ⚠️  Failed to unsubscribe from {sub_name}: {unsub_err}")
                    
                    if unsubbed_count > 0:
                        logger.info(f"  ✓ Unsubscribed {email} from {unsubbed_count} subscription types")
                        summary["hubspot_updates"] += 1
                        
                        # Clean up company email/phone if they match the contact
                        try:
                            await self._clean_company_contact_info(email, contact_phone, vid)
                        except Exception as company_err:
                            logger.warning(f"  ⚠️  Company cleanup warning: {company_err}")
                    else:
                        summary["skipped"] += 1
                        
                except Exception as sub_err:
                    logger.error(f"  ✗ Failed to process subscriptions for {email}: {sub_err}")
                    summary["errors"].append({
                        "email": email,
                        "error": str(sub_err)
                    })
            
            except Exception as e:
                logger.error(f"  Error processing {email}: {e}")
                summary["errors"].append({
                    "email": email,
                    "error": str(e)
                })
        
        logger.info(f"\n✓ Mailchimp → HubSpot Unsubscribe Sync Complete:")
        logger.info(f"  • Unsubscribed found: {summary['mailchimp_unsubscribed']}")
        logger.info(f"  • Opted out in HubSpot: {summary['hubspot_updates']}")
        logger.info(f"  • Already opted out: {summary['skipped']}")
        logger.info(f"  • Errors: {len(summary['errors'])}")
        
        return summary
    
    async def sync_list_443_to_mailchimp(self) -> Dict[str, Any]:
        """
        Reverse sync: Check HubSpot List 443 (Opted Out) and archive any members in Mailchimp.
        
        This ensures contacts who opt out in HubSpot (and get added to List 443 automatically)
        are also archived in Mailchimp and removed from source lists.
        
        Returns:
            {
                "list_443_members": int,
                "archived_in_mailchimp": int,
                "already_archived": int,
                "errors": List[Dict]
            }
        """
        logger.info("Starting List 443 → Mailchimp archive sync...")
        
        summary = {
            "list_443_members": 0,
            "archived_in_mailchimp": 0,
            "already_archived": 0,
            "errors": []
        }
        
        try:
            # Get all contacts in List 443 (v3 API)
            logger.info(f"Fetching members of List {OPTED_OUT_LIST_ID} (Opted Out)...")
            list_members = []
            
            async for contact in self.hs_client.get_list_members(
                str(OPTED_OUT_LIST_ID),
                properties=["email", "firstname", "lastname"]
            ):
                list_members.append(contact)
            
            summary["list_443_members"] = len(list_members)
            logger.info(f"Found {len(list_members)} contacts in List 443")
            
            if not list_members:
                return summary
            
            # Check each contact in Mailchimp
            for contact in list_members:
                vid = contact.get('vid')
                email = contact.get('email')
                
                try:
                    logger.info(f"Checking {email} in Mailchimp...")
                    
                    # Check if contact exists in Mailchimp
                    member = await self.mc_client.get_member(email)
                    
                    if not member:
                        logger.info(f"  {email} not in Mailchimp - skipping")
                        continue
                    
                    current_status = member.get('status')
                    
                    if current_status == 'archived':
                        logger.info(f"  {email} already archived in Mailchimp")
                        summary["already_archived"] += 1
                        continue
                    
                    # Archive the member
                    logger.info(f"  Archiving {email} in Mailchimp (opted out in HubSpot)")
                    archive_result = await self.mc_client.archive_member(email)
                    
                    if archive_result:
                        logger.info(f"  ✓ Archived {email}")
                        summary["archived_in_mailchimp"] += 1
                    else:
                        logger.error(f"  ✗ Failed to archive {email}")
                        summary["errors"].append({
                            "email": email,
                            "error": "Archive operation failed"
                        })
                
                except Exception as e:
                    logger.error(f"  Error processing {email}: {e}")
                    summary["errors"].append({
                        "email": email,
                        "error": str(e)
                    })
            
            logger.info(f"List 443 sync complete: {summary['archived_in_mailchimp']} archived, "
                       f"{summary['already_archived']} already archived, "
                       f"{len(summary['errors'])} errors")
            
        except Exception as e:
            logger.error(f"Failed to fetch List 443 members: {e}")
            summary["errors"].append({
                "error": f"Failed to fetch list: {str(e)}"
            })
        
        return summary
    
    async def _clean_company_contact_info(self, contact_email: str, contact_phone: str, vid: str):
        """
        Check associated companies and remove email/phone if they match the contact.
        This prevents opted-out email addresses from being used for company communications.
        
        Args:
            contact_email: Contact's email address
            contact_phone: Contact's phone number
            vid: Contact VID
        """
        try:
            # Get company associations
            associations_result = await self.hs_client.get(
                f'/crm/v3/objects/contacts/{vid}/associations/companies'
            )
            
            if associations_result.get('status') != 200:
                return
            
            data = associations_result.get('data', {})
            companies = data.get('results', [])
            
            for company_assoc in companies:
                company_id = company_assoc.get('id')
                
                # Get company details
                company_result = await self.hs_client.get(
                    f'/crm/v3/objects/companies/{company_id}',
                    params={'properties': 'name,email,phone'}
                )
                
                if company_result.get('status') != 200:
                    continue
                
                company_data = company_result.get('data', {})
                company_props = company_data.get('properties', {})
                
                company_name = company_props.get('name')
                company_email = company_props.get('email')
                company_phone = company_props.get('phone')
                
                properties_to_clear = {}
                
                # Check exact email match (case-insensitive)
                if company_email and company_email.lower() == contact_email.lower():
                    properties_to_clear['email'] = ''
                    logger.info(f"  → Removing email from company '{company_name}' (ID: {company_id})")
                
                # Check phone match (normalize for comparison)
                if company_phone and contact_phone:
                    clean_company_phone = ''.join(c for c in company_phone if c.isdigit())
                    clean_contact_phone = ''.join(c for c in contact_phone if c.isdigit())
                    
                    if clean_company_phone and clean_company_phone == clean_contact_phone:
                        properties_to_clear['phone'] = ''
                        logger.info(f"  → Removing phone from company '{company_name}' (ID: {company_id})")
                
                # Update company if needed
                if properties_to_clear:
                    try:
                        update_result = await self.hs_client.patch(
                            f'/crm/v3/objects/companies/{company_id}',
                            json={'properties': properties_to_clear}
                        )
                        
                        if update_result.get('status') == 200:
                            logger.info(f"  ✓ Cleaned company '{company_name}'")
                    except Exception as e:
                        logger.warning(f"  ⚠️  Failed to clean company '{company_name}': {e}")
        
        except Exception as e:
            logger.warning(f"  ⚠️  Company cleanup error: {e}")
