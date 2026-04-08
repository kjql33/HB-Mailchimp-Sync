"""
CLI entrypoint for HubSpot ↔ Mautic sync.

Modes:
  validate-config  Validate config file only (no API calls)
  plan             Scan HubSpot/Mautic and generate operations plan (read-only)
  apply            Execute operations from a plan (live mutations)
  sync             plan + apply in one command (convenience for local dev)

Environment Variables (all required unless noted):
  HUBSPOT_PRIVATE_APP_TOKEN    HubSpot private app token
  MAUTIC_BASE_URL              Mautic root URL (no trailing slash)
  MAUTIC_USERNAME              Mautic admin username
  MAUTIC_PASSWORD              Mautic admin password
  TEAMS_WEBHOOK_URL            (optional) Microsoft Teams webhook
  TEAMS_NOTIFICATIONS_ENABLED  (optional) "true" to enable Teams alerts
  LOAD_DOTENV=1                (local dev only) auto-load .env file

GitHub Actions:
  Set all required variables as repository secrets.
  See .github/workflows/sync.yml for the full workflow.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Auto-load .env if it exists in the project root (local dev / Ubuntu server)
try:
    from dotenv import load_dotenv
    for _env_path in [Path(__file__).parent.parent / ".env", Path.cwd() / ".env"]:
        if _env_path.exists():
            load_dotenv(dotenv_path=_env_path)
            break
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Mode implementations
# ----------------------------------------------------------------------

def validate_config_mode(config_path: Path) -> int:
    try:
        from corev2.config.loader import load_config
        config = load_config(str(config_path))
        total_lists = sum(len(v) for v in config.hubspot.lists.values())
        logger.info(
            f"Config valid: {total_lists} lists, "
            f"{len(config.secondary_sync.mappings)} secondary mappings, "
            f"cap={config.mautic.audience_cap}"
        )
        return 0
    except Exception as e:
        logger.error(f"Config validation failed: {e}")
        return 1


def plan_mode(
    config_path: Path,
    output_path: Path,
    only_email: Optional[str] = None,
    only_vid: Optional[str] = None,
) -> int:
    try:
        from corev2.config.loader import load_config, compute_config_hash
        from corev2.clients.hubspot_client import HubSpotClient
        from corev2.clients.mautic_client import MauticClient
        from corev2.planner.primary import SyncPlanner

        config = load_config(str(config_path))
        config_hash = compute_config_hash(config)
        logger.info(f"Config hash: {config_hash}")

        hs_client = HubSpotClient(
            api_key=config.hubspot.api_key.get_secret_value(), rate_limit=10.0
        )
        mc_client = MauticClient(
            base_url=config.mautic.base_url,
            username=config.mautic.username.get_secret_value(),
            password=config.mautic.password.get_secret_value(),
            rate_limit=10.0,
        )
        planner = SyncPlanner(config, hs_client, mc_client)
        contact_limit = config.safety.test_contact_limit or None

        async def run():
            async with hs_client, mc_client:
                return await planner.generate_plan(
                    contact_limit=contact_limit,
                    only_email=only_email,
                    only_vid=only_vid,
                )

        plan = asyncio.run(run())
        plan["metadata"]["config_hash"] = config_hash
        plan["metadata"]["config_file"] = str(config_path.resolve())

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2)

        logger.info(f"Plan saved: {output_path}")
        logger.info(f"  Contacts scanned:       {plan['summary']['total_contacts_scanned']}")
        logger.info(f"  Contacts with ops:      {plan['summary']['contacts_with_operations']}")
        logger.info(f"  Operations by type:     {plan['summary']['operations_by_type']}")
        return 0

    except Exception as e:
        logger.error(f"Plan generation failed: {e}")
        import traceback; traceback.print_exc()
        return 1


def apply_mode(plan_path: Path, dry_run: bool = False) -> int:
    try:
        from corev2.config.loader import load_config, compute_config_hash
        from corev2.config.schema import RunMode
        from corev2.clients.hubspot_client import HubSpotClient
        from corev2.clients.mautic_client import MauticClient
        from corev2.executor.engine import SyncExecutor, AudienceCapGuard
        from corev2.sync.unsubscribe_sync import UnsubscribeSyncEngine
        from corev2.planner.secondary import SecondaryPlanner

        # Load plan
        with open(plan_path, encoding="utf-8") as f:
            plan_data = json.load(f)

        config_file = plan_data.get("metadata", {}).get("config_file")
        if not config_file:
            raise ValueError("Plan missing config_file in metadata - regenerate plan")

        config = load_config(config_file)

        # Config hash verification
        current_hash = compute_config_hash(config)
        plan_hash = plan_data.get("metadata", {}).get("config_hash")
        if plan_hash and current_hash != plan_hash:
            raise ValueError(
                f"Config hash mismatch - plan was generated with a different config.\n"
                f"  Plan hash:    {plan_hash}\n"
                f"  Current hash: {current_hash}\n"
                f"Regenerate the plan with: python -m corev2.cli plan"
            )

        # Safety gates (live apply only)
        if not dry_run:
            if config.safety.run_mode != RunMode.PROD:
                raise ValueError(f"run_mode must be 'prod' (current: '{config.safety.run_mode.value}')")
            if not config.safety.allow_apply:
                raise ValueError("allow_apply=false - set to true in config to enable live mutations")
            if config.safety.test_contact_limit == 0 and not config.safety.allow_unlimited:
                raise ValueError("test_contact_limit=0 requires allow_unlimited=true")
            has_archive = any(
                any(op.get("type") == "archive_mc_member" for op in c.get("operations", []))
                for c in plan_data.get("operations", [])
            )
            if has_archive and not config.safety.allow_archive:
                raise ValueError("Plan contains archive operations but allow_archive=false")
            logger.info("All safety gates passed")
        else:
            logger.info("DRY-RUN MODE - no mutations will be made")

        # Initialise clients
        hs_client = HubSpotClient(
            api_key=config.hubspot.api_key.get_secret_value(), rate_limit=10.0
        )
        mc_client = MauticClient(
            base_url=config.mautic.base_url,
            username=config.mautic.username.get_secret_value(),
            password=config.mautic.password.get_secret_value(),
            rate_limit=10.0,
        )

        webhook_url = (
            config.notifications.webhook_url
            if config.notifications.enabled else None
        )

        async def run():
            async with hs_client, mc_client:

                # STEP 1: Unsubscribe sync (Mautic → HubSpot)
                if not dry_run:
                    logger.info("STEP 1: Mautic → HubSpot unsubscribe sync...")
                    unsub = UnsubscribeSyncEngine(config, hs_client, mc_client)
                    unsub_results = await unsub.scan_and_sync()
                    logger.info(f"  Unsubscribed found: {unsub_results['mailchimp_unsubscribed']}")
                    logger.info(f"  HubSpot updates:    {unsub_results['hubspot_updates']}")
                    logger.info(f"  Skipped:            {unsub_results['skipped']}")
                else:
                    logger.info("STEP 1: Skipped (dry-run)")

                # STEP 2: Primary sync (HubSpot → Mautic)
                logger.info("STEP 2: Executing primary sync operations...")
                cap_guard = None
                if not dry_run:
                    cap_guard = AudienceCapGuard(mc_client, config.mautic.audience_cap, webhook_url)
                    can_proceed = await cap_guard.preflight_check()
                    if not can_proceed:
                        logger.error("Audience cap reached - aborting")
                        return {"aborted": True, "abort_reason": "audience_cap_reached"}

                executor = SyncExecutor(config, hs_client, mc_client, dry_run=dry_run)
                results = await executor.execute_plan(plan_data, cap_guard=cap_guard)
                logger.info(
                    f"  Primary sync: {results['successful']} ok, "
                    f"{results['failed']} failed, {results['skipped']} skipped"
                )

                # STEP 3: Secondary sync (Mautic → HubSpot exit tags)
                logger.info("STEP 3: Secondary sync (exit tags)...")
                if config.secondary_sync.enabled and config.secondary_sync.mappings:
                    sec_planner = SecondaryPlanner(config, hs_client, mc_client)
                    sec_plan = await sec_planner.generate_plan()
                    logger.info(f"  Exit-tagged found: {sec_plan['summary']['exit_tagged_contacts_found']}")
                    logger.info(f"  Exempt skipped:    {sec_plan['summary']['exempt_contacts_skipped']}")

                    if sec_plan["operations"]:
                        sec_executor = SyncExecutor(config, hs_client, mc_client, dry_run=dry_run)
                        sec_results = await sec_executor.execute_plan(
                            sec_plan, cap_guard=cap_guard
                        )
                        logger.info(
                            f"  Secondary sync: {sec_results['successful']} ok, "
                            f"{sec_results['failed']} failed"
                        )
                    else:
                        logger.info("  No exit-tagged contacts to process")
                else:
                    logger.info("  Secondary sync disabled in config")

                return results

        results = asyncio.run(run())

        if results.get("aborted"):
            logger.error(f"Sync aborted: {results.get('abort_reason')}")
            return 1
        if results.get("failed", 0) > 0:
            return 1
        return 0

    except Exception as e:
        logger.error(f"Execution failed: {e}")
        import traceback; traceback.print_exc()
        return 1


def sync_mode(config_path: Path) -> int:
    """Convenience: plan then apply in one command."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    plan_path = Path(f"corev2/artifacts/plan_{timestamp}.json")

    logger.info("Running full sync (plan + apply)...")
    rc = plan_mode(config_path, plan_path)
    if rc != 0:
        return rc
    return apply_mode(plan_path)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="HubSpot ↔ Mautic Sync Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Validate config (no API calls)
  python -m corev2.cli validate-config

  # Generate plan (read-only)
  python -m corev2.cli plan --output corev2/artifacts/plan.json

  # Dry-run apply (simulate, no mutations)
  python -m corev2.cli apply --plan corev2/artifacts/plan.json --dry-run

  # Live apply
  python -m corev2.cli apply --plan corev2/artifacts/plan.json

  # Full sync (plan + apply)
  python -m corev2.cli sync

  # Debug single contact
  python -m corev2.cli plan --only-email user@example.com --output /tmp/debug.json
        """,
    )
    parser.add_argument(
        "mode",
        choices=["validate-config", "plan", "apply", "sync"],
        help="Execution mode",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("corev2/config/production.yaml"),
        help="Config YAML path (default: corev2/config/production.yaml)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(f"corev2/artifacts/plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"),
        help="Plan output path (plan mode)",
    )
    parser.add_argument("--plan", type=Path, help="Plan JSON path (apply mode)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without mutations")
    parser.add_argument("--only-email", type=str, help="Filter to single contact by email")
    parser.add_argument("--only-vid", type=str, help="Filter to single contact by VID")

    args = parser.parse_args()

    try:
        if args.mode == "validate-config":
            return validate_config_mode(args.config)
        elif args.mode == "plan":
            return plan_mode(args.config, args.output, args.only_email, args.only_vid)
        elif args.mode == "apply":
            if not args.plan:
                logger.error("--plan required for apply mode")
                return 1
            return apply_mode(args.plan, dry_run=args.dry_run)
        elif args.mode == "sync":
            return sync_mode(args.config)
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        return 130

    return 0


if __name__ == "__main__":
    sys.exit(main())
