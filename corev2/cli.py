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


async def _refresh_list_names(config_path: Path, hs_client) -> None:
    """Fetch current HubSpot list names and update the YAML config file if any differ."""
    import yaml, re

    with open(config_path, encoding="utf-8") as f:
        raw_text = f.read()

    # Collect every list ID referenced in config
    list_ids: set[str] = set()
    raw_data = yaml.safe_load(raw_text)
    for group in (raw_data.get("hubspot", {}).get("lists", {}) or {}).values():
        for entry in group or []:
            if isinstance(entry, dict) and "id" in entry:
                list_ids.add(str(entry["id"]))
    for mapping in (raw_data.get("secondary_sync", {}) or {}).get("mappings", []):
        for key in ("source_list", "destination_list"):
            if mapping.get(key):
                list_ids.add(str(mapping[key]))
        for extra in mapping.get("additional_remove_lists", []):
            if extra.get("list_id"):
                list_ids.add(str(extra["list_id"]))

    # Fetch live names from HubSpot
    live_names: dict[str, str] = {}
    for lid in sorted(list_ids):
        name = await hs_client.get_list_name(lid)
        if name:
            live_names[lid] = name

    if not live_names:
        return

    # Walk through YAML and patch name fields in-place (preserves comments/formatting)
    updated_text = raw_text
    changes: list[str] = []

    # Pattern: id: "NNN"\n        name: "OLD"  → update OLD with live name
    def _replace_list_name(m):
        lid = m.group(1)
        old_name = m.group(2)
        new_name = live_names.get(lid, old_name)
        if old_name != new_name:
            changes.append(f"  List {lid}: \"{old_name}\" → \"{new_name}\"")
        return f'id: "{lid}"\n{m.group(3)}name: "{new_name}"'

    updated_text = re.sub(
        r'id: "(\d+)"\n(\s+)name: "([^"]*)"',
        lambda m: _replace_list_name(type("M", (), {"group": lambda s, i: [None, m.group(1), m.group(3), m.group(2)][i]})()),
        updated_text,
    )

    # Simpler approach: direct per-field patching
    updated_text = raw_text  # reset
    changes.clear()

    for lid, live_name in live_names.items():
        # Primary list names:  id: "LID"\n<spaces>name: "OLD"
        pattern = re.compile(rf'(id: "{lid}"\n\s+name: )"([^"]*)"')
        match = pattern.search(updated_text)
        if match and match.group(2) != live_name:
            changes.append(f"  List {lid} name: \"{match.group(2)}\" → \"{live_name}\"")
            updated_text = pattern.sub(rf'\1"{live_name}"', updated_text, count=1)

        # source_name for this list ID:  source_list: "LID"\n<spaces>source_name: "OLD"
        pattern = re.compile(rf'(source_list: "{lid}"\n\s+source_name: )"([^"]*)"')
        for match in pattern.finditer(updated_text):
            if match.group(2) != live_name:
                changes.append(f"  List {lid} source_name: \"{match.group(2)}\" → \"{live_name}\"")
        updated_text = pattern.sub(rf'\1"{live_name}"', updated_text)

        # destination_name:  destination_list: "LID"\n<spaces>destination_name: "OLD"
        pattern = re.compile(rf'(destination_list: "{lid}"\n\s+destination_name: )"([^"]*)"')
        for match in pattern.finditer(updated_text):
            if match.group(2) != live_name:
                changes.append(f"  List {lid} destination_name: \"{match.group(2)}\" → \"{live_name}\"")
        updated_text = pattern.sub(rf'\1"{live_name}"', updated_text)

        # additional_remove_lists:  list_id: "LID"\n<spaces>list_name: "OLD"
        pattern = re.compile(rf'(list_id: "{lid}"\n\s+list_name: )"([^"]*)"')
        for match in pattern.finditer(updated_text):
            if match.group(2) != live_name:
                changes.append(f"  List {lid} list_name: \"{match.group(2)}\" → \"{live_name}\"")
        updated_text = pattern.sub(rf'\1"{live_name}"', updated_text)

    if changes:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(updated_text)
        logger.info("Auto-updated config list names from HubSpot:")
        for c in changes:
            logger.info(c)
    else:
        logger.info("All config list names match HubSpot — no updates needed.")


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

        # Auto-refresh list names in YAML before planning
        async def refresh_names():
            async with hs_client:
                await _refresh_list_names(config_path, hs_client)

        asyncio.run(refresh_names())

        # Reload config after potential name updates (hash must reflect current file)
        config = load_config(str(config_path))
        config_hash = compute_config_hash(config)
        planner = SyncPlanner(config, hs_client, mc_client)

        # Re-init client (session was closed by refresh_names)
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
                # ── Audience cap pre-flight ────────────────────────────
                from corev2.executor.engine import AudienceCapGuard
                cap_guard = AudienceCapGuard(
                    mc_client,
                    cap=config.mailchimp.audience_cap,
                    recheck_interval=10,
                )
                if cap_guard.enabled and not dry_run:
                    can_proceed = await cap_guard.preflight()
                    if not can_proceed:
                        logger.error(
                            f"ABORT: Mailchimp audience already at cap "
                            f"({cap_guard.live_count:,} / {cap_guard.cap:,}). "
                            f"No contacts will be synced."
                        )
                        return {
                            "total_operations": 0,
                            "successful": 0,
                            "failed": 0,
                            "skipped": 0,
                            "contacts_processed": 0,
                            "dry_run": dry_run,
                            "audience_cap": {
                                "cap": cap_guard.cap,
                                "current_count": cap_guard.current_count,
                                "cap_reached": True,
                                "aborted_preflight": True,
                            },
                        }, None
                # ── End cap pre-flight ─────────────────────────────────

                # STEP 1: Sync unsubscribes from Mailchimp → HubSpot
                if not dry_run:
                    logger.info("🔄 Step 1: Syncing Mailchimp unsubscribes to HubSpot...")
                    from corev2.sync.unsubscribe_sync import UnsubscribeSyncEngine
                    
                    unsub_engine = UnsubscribeSyncEngine(config, hs_client, mc_client)
                    unsub_results = await unsub_engine.scan_and_sync()
                    
                    logger.info(f"✔ Unsubscribe sync complete:")
                    logger.info(f"  Mailchimp unsubscribed: {unsub_results['mailchimp_unsubscribed']}")
                    logger.info(f"  HubSpot updates: {unsub_results['hubspot_updates']}")
                    logger.info(f"  Skipped (already unsubscribed): {unsub_results['skipped']}")
                    if unsub_results['errors']:
                        logger.warning(f"  Errors: {len(unsub_results['errors'])}")

                    # STEP 1B: Sync cleaned (hard-bounced) contacts from Mailchimp → HubSpot
                    logger.info("🔄 Step 1B: Syncing Mailchimp cleaned (hard-bounce) contacts to HubSpot...")
                    cleaned_results = await unsub_engine.scan_cleaned_and_sync()
                    logger.info(f"✔ Cleaned contact sync complete:")
                    logger.info(f"  Mailchimp cleaned: {cleaned_results['mailchimp_cleaned']}")
                    logger.info(f"  Tags stripped: {cleaned_results['tags_removed']}")
                    logger.info(f"  HubSpot flagged: {cleaned_results['hubspot_flagged']}")
                    logger.info(f"  Not in HubSpot: {cleaned_results['not_in_hubspot']}")
                    if cleaned_results['errors']:
                        logger.warning(f"  Errors: {len(cleaned_results['errors'])}")

                    # NOTE: STEP 1B (List 443 reverse sync) is DISABLED - List 443 no longer exists
                    # List 762 "Unsubscribed/Opted Out" is DYNAMIC - auto-managed by HubSpot
                    # NOTE: NEVER manually add/remove contacts from List 762 - it's criteria-based
                    # logger.info("🔄 Step 1B: Syncing HubSpot List 443 (Opted Out) to Mailchimp...")
                    # list443_results = await unsub_engine.sync_list_443_to_mailchimp()
                
                # STEP 2: Execute primary sync operations
                logger.info("🔄 Step 2: Executing primary sync operations...")
                executor = SyncExecutor(config, hs_client, mc_client, dry_run=dry_run, cap_guard=cap_guard)
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
                            sec_executor = SyncExecutor(config, hs_client, mc_client, dry_run=False, cap_guard=cap_guard)
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
            logger.info("🚀 EXECUTING LIVE OPERATIONS 🚀")
        
        result = asyncio.run(run_execution())

        # Preflight abort returns a single tuple element
        if isinstance(result, tuple) and len(result) == 2:
            primary_results, secondary_results = result
        else:
            primary_results, secondary_results = result, None

        # Handle preflight abort (cap already reached before any ops)
        if primary_results.get("audience_cap", {}).get("aborted_preflight"):
            cap_info = primary_results["audience_cap"]
            logger.error(
                f"RUN ABORTED: Mailchimp audience at cap "
                f"({cap_info['current_count']:,} / {cap_info['cap']:,})"
            )
            return 1
        
        logger.info("Primary Sync Complete:")
        logger.info(f"  Total operations: {primary_results['total_operations']}")
        logger.info(f"  Successful: {primary_results['successful']}")
        logger.info(f"  Failed: {primary_results['failed']}")
        logger.info(f"  Skipped: {primary_results['skipped']}")
        logger.info(f"  Contacts processed: {primary_results['contacts_processed']}")

        # Log audience cap stats if present
        cap_info = primary_results.get("audience_cap")
        if cap_info:
            logger.info(f"  Audience cap: {cap_info.get('current_count', '?'):,} / {cap_info['cap']:,}")
            if cap_info.get("cap_reached"):
                logger.warning(f"  ⚠ CAP REACHED — {cap_info.get('contacts_skipped', 0)} contacts skipped")
        
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
