#!/usr/bin/env python3
"""
HubSpot ↔ Mailchimp Sync - Simple Entry Point

Just run: python main.py

That's it. Everything happens automatically:
- Loads .env
- Syncs unsubscribes (Mailchimp → HubSpot)
- Archives opted-out contacts (HubSpot List 443 → Mailchimp)
- Syncs all configured lists bidirectionally
- Logs everything with timestamp
"""
import sys
import asyncio
import logging
from datetime import datetime
from pathlib import Path

# Load .env automatically
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("⚠️  python-dotenv not installed, using system environment")

# Setup logging to both file and console
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = log_dir / f"sync_{timestamp}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


async def main():
    """Run complete bidirectional sync."""
    from corev2.config.loader import load_config
    from corev2.clients.hubspot_client import HubSpotClient
    from corev2.clients.mailchimp_client import MailchimpClient
    from corev2.sync.unsubscribe_sync import UnsubscribeSyncEngine
    from corev2.planner.primary import SyncPlanner
    from corev2.executor.engine import SyncExecutor
    
    logger.info("="*70)
    logger.info("  HubSpot <-> Mailchimp Bidirectional Sync")
    logger.info("="*70)
    
    # Load config
    config_path = "corev2/config/production.yaml"
    logger.info(f"\nLoading config: {config_path}")
    config = load_config(config_path)
    
    # Report configured lists
    logger.info("\n📋 LISTS CONFIGURED FOR SYNC:")
    total_lists = 0
    for group_name, list_configs in config.hubspot.lists.items():
        if list_configs:
            logger.info(f"\n  {group_name}:")
            for lc in list_configs:
                total_lists += 1
                logger.info(f"    • List {lc.id} '{lc.name}' → Mailchimp tag '{lc.tag}'")
    logger.info(f"\n  📊 Total: {total_lists} lists active")
    
    # Report safety settings
    logger.info(f"\n🔒 SAFETY SETTINGS:")
    logger.info(f"  Run mode: {config.safety.run_mode}")
    logger.info(f"  Allow apply: {config.safety.allow_apply}")
    logger.info(f"  Allow archive: {config.safety.allow_archive}")
    if config.safety.test_contact_limit > 0:
        logger.info(f"  ⚠️  TEST MODE: Limited to {config.safety.test_contact_limit} contacts")
    else:
        logger.info(f"  ✓ FULL MODE: All contacts")
    
    # Initialize clients
    logger.info("\nInitializing API clients...")
    hs_client = HubSpotClient(
        api_key=config.hubspot.api_key.get_secret_value(),
        rate_limit=10.0
    )
    mc_client = MailchimpClient(
        api_key=config.mailchimp.api_key.get_secret_value(),
        server_prefix=config.mailchimp.server_prefix,
        audience_id=config.mailchimp.audience_id,
        rate_limit=10.0
    )
    
    try:
        async with hs_client, mc_client:
            # STEP 1: Mailchimp → HubSpot Unsubscribe Sync
            logger.info("\n" + "="*70)
            logger.info("STEP 1: Mailchimp → HubSpot Unsubscribe Sync")
            logger.info("="*70)
            
            unsub_engine = UnsubscribeSyncEngine(config, hs_client, mc_client)
            unsub_results = await unsub_engine.scan_and_sync()
            
            logger.info(f"\n✓ Results:")
            logger.info(f"  Found unsubscribed: {unsub_results['mailchimp_unsubscribed']}")
            logger.info(f"  Updated in HubSpot: {unsub_results['hubspot_updates']}")
            logger.info(f"  Skipped (already done): {unsub_results['skipped']}")
            if unsub_results['errors']:
                logger.warning(f"  ⚠️  Errors: {len(unsub_results['errors'])}")
            
            # STEP 1B: DISABLED - List 443 no longer exists
            # List 762 "Unsubscribed/Opted Out" is a DYNAMIC LIST - auto-managed by HubSpot when contacts opt out
            # Reverse direction (HubSpot → Mailchimp) handled via subscription status checks + archival reconciliation
            # NOTE: NEVER manually add/remove contacts from List 762 - it's criteria-based
            # logger.info("\n" + "="*70)
            # logger.info("STEP 1B: HubSpot List 443 (Opted Out) → Mailchimp Archive")
            # logger.info("="*70)
            # list443_results = await unsub_engine.sync_list_443_to_mailchimp()
            
            # STEP 2: Generate Primary Sync Plan
            logger.info("\n" + "="*70)
            logger.info("STEP 2: Generating Primary Sync Plan")
            logger.info("="*70)
            
            planner = SyncPlanner(config, hs_client, mc_client)
            contact_limit = config.safety.test_contact_limit if config.safety.test_contact_limit > 0 else None
            
            plan = await planner.generate_plan(contact_limit=contact_limit)
            
            logger.info(f"\n✓ Plan Generated:")
            logger.info(f"  Contacts scanned: {plan['summary']['total_contacts_scanned']}")
            logger.info(f"  Contacts needing changes: {plan['summary']['contacts_with_operations']}")
            logger.info(f"  Operations by type:")
            for op_type, count in plan['summary']['operations_by_type'].items():
                logger.info(f"    • {op_type}: {count}")
            
            # STEP 3: Execute Primary Sync
            logger.info("\n" + "="*70)
            logger.info("STEP 3: Executing Primary Sync Operations")
            logger.info("="*70)
            logger.info("🚨 LIVE EXECUTION - Applying changes...")
            
            executor = SyncExecutor(config, hs_client, mc_client, dry_run=False)
            results = await executor.execute_plan(plan)
            
            logger.info(f"\n✓ Execution Complete:")
            logger.info(f"  Total operations: {results['total_operations']}")
            logger.info(f"  ✓ Successful: {results['successful']}")
            logger.info(f"  ✗ Failed: {results['failed']}")
            logger.info(f"  ⊘ Skipped: {results['skipped']}")
            logger.info(f"  Contacts touched: {results['contacts_processed']}")
            
            # STEP 4: Secondary Sync (Mailchimp → HubSpot)
            if config.secondary_sync.enabled and config.secondary_sync.mappings:
                logger.info("\n" + "="*70)
                logger.info("STEP 4: Secondary Sync (Mailchimp → HubSpot)")
                logger.info("="*70)
                logger.info("Scanning Mailchimp for exit-tagged contacts...")
                
                from corev2.planner.secondary import SecondaryPlanner
                
                secondary_planner = SecondaryPlanner(config, hs_client, mc_client)
                secondary_plan = await secondary_planner.generate_plan()
                
                sec_summary = secondary_plan["summary"]
                logger.info(f"\n✓ Secondary Plan Generated:")
                logger.info(f"  Mailchimp scanned: {sec_summary['total_mailchimp_scanned']}")
                logger.info(f"  Exit-tagged found: {sec_summary['exit_tagged_contacts_found']}")
                logger.info(f"  Contacts with operations: {sec_summary['contacts_with_operations']}")
                for op_type, count in sec_summary.get("operations_by_type", {}).items():
                    logger.info(f"    • {op_type}: {count}")
                
                if secondary_plan["operations"]:
                    logger.info("\n🚨 Executing secondary sync operations...")
                    secondary_executor = SyncExecutor(config, hs_client, mc_client, dry_run=False)
                    sec_results = await secondary_executor.execute_plan(secondary_plan)
                    
                    logger.info(f"\n✓ Secondary Sync Complete:")
                    logger.info(f"  Total operations: {sec_results['total_operations']}")
                    logger.info(f"  ✓ Successful: {sec_results['successful']}")
                    logger.info(f"  ✗ Failed: {sec_results['failed']}")
                    logger.info(f"  ⊘ Skipped: {sec_results['skipped']}")
                else:
                    logger.info("\n  No exit-tagged contacts to process.")
            else:
                logger.info("\nℹ️  Secondary sync disabled or no mappings configured")
            
            logger.info("\n" + "="*70)
            logger.info("  ✓ SYNC COMPLETE")
            logger.info("="*70)
            logger.info(f"\nLog saved: {log_file}")
            
            return 0 if results['failed'] == 0 else 1
            
    except Exception as e:
        logger.error(f"\n❌ FATAL ERROR: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("\n\n⚠️  Interrupted by user")
        sys.exit(1)
