"""
CLI entrypoint for V2 sync system.

Modes:
  validate-config: Load and validate config without any API calls
  plan: Generate operations_plan.json (dry-run, no mutations)
  apply: Execute operations from plan with safety gates enforced

Environment Variables:
  LOAD_DOTENV=1  - Load .env file (dev/local only, NOT for production)
  
  Production runs should set env vars directly, not rely on .env files.
  CI/scheduled tasks must export variables before running CLI.
"""

import argparse
import sys
import logging
import os
from pathlib import Path
from typing import Optional

# Optional .env loading (dev/local only, gated by env var)
if os.getenv("LOAD_DOTENV") == "1":
    from dotenv import load_dotenv
    load_dotenv()
    logging.info("Loaded environment variables from .env (LOAD_DOTENV=1)")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


def validate_config_mode(config_path: Path) -> int:
    """Validate config file and exit."""
    try:
        from corev2.config.loader import load_config
        
        logger.info(f"Loading config from: {config_path}")
        config = load_config(str(config_path))
        
        logger.info("Ô£ô Config loaded successfully")
        logger.info(f"  HubSpot lists configured: {len(config.hubspot.lists)}")
        logger.info(f"  Safety gates: TEST_CONTACT_LIMIT={config.safety.test_contact_limit}, "
                   f"RUN_MODE={config.safety.run_mode}, ALLOW_ARCHIVE={config.safety.allow_archive}")
        
        return 0
    except Exception as e:
        logger.error(f"Ô£ù Config validation failed: {e}")
        return 1


def plan_mode(config_path: Path, output_path: Path, only_email: Optional[str] = None, only_vid: Optional[str] = None) -> int:
    """Generate operations plan (dry-run)."""
    try:
        from corev2.config.loader import load_config, compute_config_hash
        from corev2.planner.primary import SyncPlanner
        from corev2.clients.hubspot_client import HubSpotClient
        from corev2.clients.mailchimp_client import MailchimpClient
        import json
        import asyncio
        
        logger.info(f"Loading config from: {config_path}")
        config = load_config(str(config_path))
        
        config_hash = compute_config_hash(config)
        logger.info(f"Config hash: {config_hash}")
        
        logger.info("Initializing API clients (read-only mode)...")
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
        
        logger.info("Generating operations plan...")
        planner = SyncPlanner(config, hs_client, mc_client)
        
        # Use test_contact_limit if set
        contact_limit = config.safety.test_contact_limit if config.safety.test_contact_limit > 0 else None
        
        # Log contact filters if set
        if only_email:
            logger.info(f"­ƒÄ» Filtering to single contact: {only_email}")
        if only_vid:
            logger.info(f"­ƒÄ» Filtering to single contact VID: {only_vid}")
        
        # Run async plan generation with context managers
        async def generate_plan_with_clients():
            async with hs_client, mc_client:
                return await planner.generate_plan(
                    contact_limit=contact_limit,
                    only_email=only_email,
                    only_vid=only_vid
                )
        
        plan = asyncio.run(generate_plan_with_clients())
        
        # Add config hash to metadata
        plan["metadata"]["config_hash"] = config_hash
        plan["metadata"]["config_file"] = str(config_path)
        
        # Save plan
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(plan, f, indent=2)
        
        logger.info(f"Ô£ô Plan saved to: {output_path}")
        logger.info(f"  Total contacts scanned: {plan['summary']['total_contacts_scanned']}")
        logger.info(f"  Contacts with operations: {plan['summary']['contacts_with_operations']}")
        logger.info(f"  Operations by type: {plan['summary']['operations_by_type']}")
        
        return 0
    except Exception as e:
        logger.error(f"Ô£ù Plan generation failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


def apply_mode(plan_path: Path, dry_run: bool = False) -> int:
    """Execute operations from plan (LIVE MUTATIONS unless dry_run=True)."""
    try:
        from corev2.config.loader import load_config, compute_config_hash
        from corev2.config.schema import RunMode
        from corev2.executor.engine import SyncExecutor
        from corev2.clients.hubspot_client import HubSpotClient
        from corev2.clients.mailchimp_client import MailchimpClient
        import json
        import asyncio
        
        # Load plan
        logger.info(f"Loading operations plan from: {plan_path}")
        with open(plan_path, encoding='utf-8') as f:
            plan_data = json.load(f)
        
        # Load config referenced in plan
        config_path = plan_data.get("metadata", {}).get("config_file")
        if not config_path:
            raise ValueError("Plan missing config_file reference in metadata")
        
        logger.info(f"Loading config from: {config_path}")
        config = load_config(config_path)
        
        # Verify config hash matches (plan was generated with same config)
        current_hash = compute_config_hash(config)
        plan_hash = plan_data.get("metadata", {}).get("config_hash")
        if plan_hash and current_hash != plan_hash:
            raise ValueError(
                f"Config hash mismatch! Plan was generated with different config.\n"
                f"  Plan hash:    {plan_hash}\n"
                f"  Current hash: {current_hash}\n"
                f"Regenerate plan with current config."
            )
        
        # If not dry-run, enforce safety gates
        if not dry_run:
            logger.info("­ƒöÆ Checking safety gates for LIVE execution...")
            
            # SAFETY GATE 1: run_mode must be prod
            if config.safety.run_mode != RunMode.PROD:
                raise ValueError(
                    f"Cannot apply: run_mode={config.safety.run_mode.value} (must be 'prod')"
                )
            
            # SAFETY GATE 2: allow_apply must be true
            if not config.safety.allow_apply:
                raise ValueError(
                    "Cannot apply: allow_apply=false\n"
                    "Set allow_apply=true in safety config to enable LIVE mutations"
                )
            
            # SAFETY GATE 3: test_contact_limit check
            if config.safety.test_contact_limit == 0:
                if not config.safety.allow_unlimited:
                    raise ValueError(
                        "Cannot apply: test_contact_limit=0 requires allow_unlimited=true"
                    )
                else:
                    logger.warning("ÔÜá´©Å  UNLIMITED MODE: Processing all contacts")
            else:
                logger.warning(
                    f"ÔÜá´©Å  LIMITED MODE: test_contact_limit={config.safety.test_contact_limit}"
                )
            
            # SAFETY GATE 4: archival check
            has_archive_ops = any(
                any(op.get("type") == "archive_mc_member" for op in contact["operations"])
                for contact in plan_data.get("operations", [])
            )
            if has_archive_ops and not config.safety.allow_archive:
                raise ValueError(
                    "Cannot apply: Plan contains archive operations but allow_archive=false"
                )
            
            logger.info("Ô£ô All safety gates passed")
            logger.info(f"  run_mode: {config.safety.run_mode.value}")
            logger.info(f"  allow_apply: {config.safety.allow_apply}")
            logger.info(f"  test_contact_limit: {config.safety.test_contact_limit}")
            logger.info(f"  allow_archive: {config.safety.allow_archive}")
        else:
            logger.info("­ƒº¬ DRY-RUN MODE: Simulating operations (no mutations)")
        
        # Initialize clients
        logger.info("Initializing API clients...")
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
        
        # Execute plan
        async def run_execution():
            async with hs_client, mc_client:
                # STEP 1: Sync unsubscribes from Mailchimp ÔåÆ HubSpot
                if not dry_run:
                    logger.info("­ƒöä Step 1: Syncing Mailchimp unsubscribes to HubSpot...")
                    from corev2.sync.unsubscribe_sync import UnsubscribeSyncEngine
                    
                    unsub_engine = UnsubscribeSyncEngine(config, hs_client, mc_client)
                    unsub_results = await unsub_engine.scan_and_sync()
                    
                    logger.info(f"Ô£ô Unsubscribe sync complete:")
                    logger.info(f"  Mailchimp unsubscribed: {unsub_results['mailchimp_unsubscribed']}")
                    logger.info(f"  HubSpot updates: {unsub_results['hubspot_updates']}")
                    logger.info(f"  Skipped (already unsubscribed): {unsub_results['skipped']}")
                    if unsub_results['errors']:
                        logger.warning(f"  Errors: {len(unsub_results['errors'])}")
                    
                    # STEP 1B: DISABLED - List 443 no longer exists
                    # List 762 "Unsubscribed/Opted Out" is a DYNAMIC LIST - auto-managed by HubSpot when contacts opt out
                    # Reverse direction (HubSpot ÔåÆ Mailchimp) handled via subscription status checks + archival reconciliation
                    # NOTE: NEVER manually add/remove contacts from List 762 - it's criteria-based
                    # logger.info("­ƒöä Step 1B: Syncing HubSpot List 443 (Opted Out) to Mailchimp...")
                    # list443_results = await unsub_engine.sync_list_443_to_mailchimp()
                
                # STEP 2: Execute primary sync operations
                logger.info("­ƒöä Step 2: Executing primary sync operations...")
                executor = SyncExecutor(config, hs_client, mc_client, dry_run=dry_run)
                primary_results = await executor.execute_plan(plan_data)
                
                # STEP 3: Secondary Sync (Mailchimp exit tags → HubSpot handover lists)
                secondary_results = None
                if not dry_run and config.secondary_sync.enabled and config.secondary_sync.mappings:
                    logger.info("Step 3: Secondary Sync (Mailchimp exit tags → HubSpot handover lists)...")
                    from corev2.planner.secondary import SecondaryPlanner
                    
                    secondary_planner = SecondaryPlanner(config, hs_client, mc_client)
                    secondary_plan = await secondary_planner.generate_plan()
                    
                    sec_summary = secondary_plan["summary"]
                    logger.info(f"  Mailchimp scanned: {sec_summary['total_mailchimp_scanned']}")
                    logger.info(f"  Exit-tagged found: {sec_summary['exit_tagged_contacts_found']}")
                    logger.info(f"  Contacts with operations: {sec_summary['contacts_with_operations']}")
                    for op_type, count in sec_summary.get("operations_by_type", {}).items():
                        logger.info(f"    {op_type}: {count}")
                    
                    if secondary_plan["operations"]:
                        has_sec_archive = any(
                            any(op.get("type") == "archive_mc_member" for op in contact["operations"])
                            for contact in secondary_plan.get("operations", [])
                        )
                        if has_sec_archive and not config.safety.allow_archive:
                            logger.warning("Secondary sync has archive ops but allow_archive=false, skipping execution")
                        else:
                            logger.info("Executing secondary sync operations...")
                            sec_executor = SyncExecutor(config, hs_client, mc_client, dry_run=False)
                            secondary_results = await sec_executor.execute_plan(secondary_plan)
                            
                            logger.info("Secondary Sync Complete:")
                            logger.info(f"  Total operations: {secondary_results['total_operations']}")
                            logger.info(f"  Successful: {secondary_results['successful']}")
                            logger.info(f"  Failed: {secondary_results['failed']}")
                            logger.info(f"  Skipped: {secondary_results['skipped']}")
                    else:
                        logger.info("  No exit-tagged contacts to process.")
                elif not dry_run and not config.secondary_sync.enabled:
                    logger.info("Secondary sync disabled in config")
                elif dry_run:
                    logger.info("DRY-RUN: Skipping secondary sync")
                
                return primary_results, secondary_results
        
        if not dry_run:
            logger.info("­ƒÜ¿ EXECUTING LIVE OPERATIONS ­ƒÜ¿")
        
        primary_results, secondary_results = asyncio.run(run_execution())
        
        logger.info("Primary Sync Complete:")
        logger.info(f"  Total operations: {primary_results['total_operations']}")
        logger.info(f"  Successful: {primary_results['successful']}")
        logger.info(f"  Failed: {primary_results['failed']}")
        logger.info(f"  Skipped: {primary_results['skipped']}")
        logger.info(f"  Contacts processed: {primary_results['contacts_processed']}")
        
        total_failed = primary_results['failed']
        if secondary_results:
            total_failed += secondary_results['failed']
        
        if total_failed > 0:
            return 1
        
        return 0
    except Exception as e:
        logger.error(f"Ô£ù Execution failed: {e}")
        import traceback
        traceback.print_exc()
        return 1



def sync_mode(config_path: Path, dry_run: bool = False) -> int:
    """Full sync pipeline: plan + apply + secondary in one command."""
    from datetime import datetime
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(f"corev2/artifacts/plan_{timestamp}.json")
    
    logger.info("=" * 70)
    logger.info("  Full Sync: Plan \u2192 Apply \u2192 Secondary")
    logger.info("=" * 70)
    
    # Generate plan
    result = plan_mode(config_path, output_path)
    if result != 0:
        return result
    
    # Apply plan (includes unsubscribe sync + primary + secondary)
    return apply_mode(output_path, dry_run=dry_run)


def main():
    parser = argparse.ArgumentParser(
        description="V2 HubSpot Ôåö Mailchimp Sync",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Validate config
  python -m corev2.cli validate-config --config config.yaml
  
  # Generate plan (dry-run)
  python -m corev2.cli plan --config config.yaml --output plan.json
  
  # Dry-run apply (simulate execution)
  python -m corev2.cli apply --plan plan.json --dry-run
  
  # Execute plan (LIVE MUTATIONS - requires safety gates)
  python -m corev2.cli apply --plan plan.json
  
  # Full sync in one command (plan + apply + secondary)
  python -m corev2.cli sync --config config.yaml
        """
    )
    
    parser.add_argument("mode", choices=["validate-config", "plan", "apply", "sync"],
                       help="Execution mode")
    parser.add_argument("--config", type=Path, default=Path("corev2/config/defaults.yaml"),
                       help="Path to config YAML file")
    parser.add_argument("--output", type=Path, default=Path("corev2/artifacts/operations_plan.json"),
                       help="Output path for plan (plan mode only)")
    parser.add_argument("--plan", type=Path,
                       help="Path to operations plan JSON (apply mode only)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Simulate apply without mutations (apply mode only)")
    parser.add_argument("--only-email", type=str,
                       help="Filter to single contact by email (plan mode only)")
    parser.add_argument("--only-vid", type=str,
                       help="Filter to single contact by VID (plan mode only)")
    
    args = parser.parse_args()
    
    try:
        if args.mode == "validate-config":
            return validate_config_mode(args.config)
        
        elif args.mode == "plan":
            return plan_mode(
                args.config,
                args.output,
                only_email=getattr(args, 'only_email', None),
                only_vid=getattr(args, 'only_vid', None)
            )
        
        elif args.mode == "apply":
            if not args.plan:
                logger.error("--plan required for apply mode")
                return 1
            return apply_mode(args.plan, dry_run=args.dry_run)
        
        elif args.mode == "sync":
            return sync_mode(args.config, dry_run=args.dry_run)
    
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
