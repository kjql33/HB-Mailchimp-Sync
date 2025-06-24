#!/usr/bin/env python3
"""
notifications.py

Enhanced notification system for HubSpot→Mailchimp sync operations.
Provides Teams notifications for both critical failures and operational warnings.
"""

import json
import requests
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)

class NotificationLevel(Enum):
    """Notification severity levels"""
    INFO = "info"
    WARNING = "warning" 
    ERROR = "error"
    CRITICAL = "critical"

class TeamsNotifier:
    """Enhanced Teams notification system with multiple severity levels"""
    
    def __init__(self, webhook_url: str, fallback_to_console: bool = True):
        self.webhook_url = webhook_url
        self.fallback_to_console = fallback_to_console
        self.session_warnings = []
        self.session_errors = []
        self.session_info = []
        
    def add_warning(self, message: str, details: Optional[Dict] = None):
        """Track a warning-level issue"""
        self.session_warnings.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "details": details or {}
        })
        logger.warning(f"📨 NOTIFICATION TRACKED: {message}")
        
    def add_error(self, message: str, details: Optional[Dict] = None):
        """Track an error-level issue"""
        self.session_errors.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "details": details or {}
        })
        logger.error(f"📨 NOTIFICATION TRACKED: {message}")
        
    def add_info(self, message: str, details: Optional[Dict] = None):
        """Track an informational message"""
        self.session_info.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "details": details or {}
        })
        logger.info(f"📨 NOTIFICATION TRACKED: {message}")
        
    def should_send_notification(self) -> bool:
        """Determine if notification should be sent based on tracked issues"""
        # Send if we have any warnings or errors
        return len(self.session_warnings) > 0 or len(self.session_errors) > 0
        
    def get_notification_level(self) -> NotificationLevel:
        """Determine overall notification severity"""
        if self.session_errors:
            return NotificationLevel.ERROR
        elif self.session_warnings:
            return NotificationLevel.WARNING
        else:
            return NotificationLevel.INFO
            
    def send_notification(self, 
                         title: str = "HubSpot→Mailchimp Sync Alert",
                         force_send: bool = False,
                         include_summary: bool = True) -> bool:
        """Send Teams notification with collected issues"""
        
        if not force_send and not self.should_send_notification():
            logger.info("No issues to report - skipping notification")
            return True
            
        level = self.get_notification_level()
        
        # Build notification content
        sections = []
        
        # Summary section
        if include_summary:
            total_issues = len(self.session_warnings) + len(self.session_errors)
            summary_text = f"**Total Issues:** {total_issues}"
            if self.session_errors:
                summary_text += f" | **Errors:** {len(self.session_errors)}"
            if self.session_warnings:
                summary_text += f" | **Warnings:** {len(self.session_warnings)}"
                
            sections.append({
                "activityTitle": "📊 Summary",
                "text": summary_text
            })
        
        # Error details
        if self.session_errors:
            error_text = ""
            for i, error in enumerate(self.session_errors[-5:], 1):  # Show last 5 errors
                error_text += f"**{i}.** {error['message']}\n"
                if error['details']:
                    error_text += f"   *Details:* {json.dumps(error['details'], indent=2)}\n"
                error_text += f"   *Time:* {error['timestamp']}\n\n"
                
            sections.append({
                "activityTitle": "❌ Errors",
                "text": error_text[:1000] + ("..." if len(error_text) > 1000 else "")
            })
        
        # Warning details  
        if self.session_warnings:
            warning_text = ""
            for i, warning in enumerate(self.session_warnings[-5:], 1):  # Show last 5 warnings
                warning_text += f"**{i}.** {warning['message']}\n"
                if warning['details']:
                    warning_text += f"   *Details:* {json.dumps(warning['details'], indent=2)}\n"
                warning_text += f"   *Time:* {warning['timestamp']}\n\n"
                
            sections.append({
                "activityTitle": "⚠️ Warnings", 
                "text": warning_text[:1000] + ("..." if len(warning_text) > 1000 else "")
            })
        
        # Success info (if no errors/warnings)
        if not self.session_errors and not self.session_warnings and self.session_info:
            info_text = ""
            for info in self.session_info[-3:]:  # Show last 3 info messages
                info_text += f"✅ {info['message']}\n"
            sections.append({
                "activityTitle": "✅ Operations",
                "text": info_text
            })
        
        # Build Teams message card
        card = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": self._get_theme_color(level),
            "summary": title,
            "sections": [
                {
                    "activityTitle": title,
                    "activitySubtitle": f"Sync completed with {len(self.session_warnings + self.session_errors)} issues detected",
                    "activityImage": "https://img.icons8.com/color/48/000000/sync.png",
                    "facts": [
                        {"name": "Timestamp", "value": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")},
                        {"name": "Severity", "value": level.value.upper()},
                        {"name": "Errors", "value": str(len(self.session_errors))},
                        {"name": "Warnings", "value": str(len(self.session_warnings))}
                    ]
                }
            ] + sections
        }
        
        # Send to Teams
        try:
            response = requests.post(
                self.webhook_url,
                headers={"Content-Type": "application/json"},
                json=card,
                timeout=30
            )
            
            if response.status_code in [200, 202]:  # Teams often returns 202 (Accepted)
                logger.info(f"✅ Teams notification sent successfully ({level.value.upper()})")
                return True
            else:
                logger.error(f"❌ Teams notification failed: {response.status_code} - {response.text}")
                if self.fallback_to_console:
                    self._fallback_to_console(title, level)
                return False
                
        except Exception as e:
            logger.error(f"❌ Error sending Teams notification: {e}")
            if self.fallback_to_console:
                self._fallback_to_console(title, level)
            return False
    
    def _fallback_to_console(self, title: str, level: NotificationLevel):
        """Fallback to console output when Teams webhook is unavailable"""
        print(f"\n{'='*60}")
        print(f"📨 NOTIFICATION FALLBACK - {level.value.upper()}")
        print(f"📋 {title}")
        print(f"{'='*60}")
        
        if self.session_errors:
            print(f"\n❌ ERRORS ({len(self.session_errors)}):")
            for i, error in enumerate(self.session_errors[-5:], 1):  # Last 5 errors
                print(f"   {i}. {error['message']}")
                if error.get('details'):
                    print(f"      Details: {error['details']}")
        
        if self.session_warnings:
            print(f"\n⚠️  WARNINGS ({len(self.session_warnings)}):")
            for i, warning in enumerate(self.session_warnings[-3:], 1):  # Last 3 warnings
                print(f"   {i}. {warning['message']}")
                if warning.get('details'):
                    print(f"      Details: {warning['details']}")
        
        if self.session_info:
            print(f"\n📝 INFO ({len(self.session_info)}):")
            for i, info in enumerate(self.session_info[-3:], 1):  # Last 3 info items
                print(f"   {i}. {info['message']}")
        
        print(f"{'='*60}\n")
    
    def _get_theme_color(self, level: NotificationLevel) -> str:
        """Get Teams card color based on severity"""
        colors = {
            NotificationLevel.INFO: "28a745",      # Green
            NotificationLevel.WARNING: "ffc107",   # Yellow  
            NotificationLevel.ERROR: "dc3545",     # Red
            NotificationLevel.CRITICAL: "6f42c1"   # Purple
        }
        return colors.get(level, "17a2b8")  # Default blue
        
    def send_test_notification(self) -> bool:
        """Send a test notification to verify webhook setup"""
        self.add_info("Test notification sent successfully")
        return self.send_notification(
            title="🧪 Test Notification",
            force_send=True,
            include_summary=False
        )
        
    def clear_session(self):
        """Clear all tracked issues for new session"""
        self.session_warnings.clear()
        self.session_errors.clear()
        self.session_info.clear()
        logger.debug("Notification session cleared")
        
    def _fallback_to_console(self, title: str, level: NotificationLevel):
        """Fallback to console output when Teams webhook is unavailable"""
        print(f"\n{'='*60}")
        print(f"📨 NOTIFICATION FALLBACK - {level.value.upper()}")
        print(f"📋 {title}")
        print(f"{'='*60}")
        
        if self.session_errors:
            print(f"\n❌ ERRORS ({len(self.session_errors)}):")
            for i, error in enumerate(self.session_errors[-5:], 1):  # Last 5 errors
                print(f"   {i}. {error['message']}")
                if error.get('details'):
                    print(f"      Details: {error['details']}")
        
        if self.session_warnings:
            print(f"\n⚠️  WARNINGS ({len(self.session_warnings)}):")
            for i, warning in enumerate(self.session_warnings[-3:], 1):  # Last 3 warnings
                print(f"   {i}. {warning['message']}")
                if warning.get('details'):
                    print(f"      Details: {warning['details']}")
        
        if self.session_info:
            print(f"\n📝 INFO ({len(self.session_info)}):")
            for i, info in enumerate(self.session_info[-3:], 1):  # Last 3 info items
                print(f"   {i}. {info['message']}")
        
        print(f"{'='*60}\n")

# Global notifier instance
_notifier: Optional[TeamsNotifier] = None

def initialize_notifier(webhook_url: str) -> TeamsNotifier:
    """Initialize global notification system"""
    global _notifier
    _notifier = TeamsNotifier(webhook_url)
    logger.info("📨 Teams notification system initialized")
    return _notifier

def get_notifier() -> Optional[TeamsNotifier]:
    """Get the global notifier instance"""
    return _notifier

def notify_warning(message: str, details: Optional[Dict] = None):
    """Convenience function to add warning"""
    if _notifier:
        _notifier.add_warning(message, details)

def notify_error(message: str, details: Optional[Dict] = None):
    """Convenience function to add error"""
    if _notifier:
        _notifier.add_error(message, details)
        
def notify_info(message: str, details: Optional[Dict] = None):
    """Convenience function to add info"""
    if _notifier:
        _notifier.add_info(message, details)

def send_final_notification(title: str = "Sync Completed") -> bool:
    """Send final notification if any issues were tracked"""
    if _notifier:
        return _notifier.send_notification(title)
    return False

def reset_session():
    """Reset the current notification session"""
    if _notifier:
        _notifier.clear_session()

def send_test_notification(webhook_url: str) -> bool:
    """Send a test notification to verify webhook"""
    test_notifier = TeamsNotifier(webhook_url)
    return test_notifier.send_test_notification()

# Notification scenarios mapped from README analysis
NOTIFICATION_SCENARIOS = {
    "api_auth_failure": "API authentication failed - sync aborted",
    "list_access_denied": "Unable to access HubSpot list",
    "contact_missing_email": "Contact skipped due to missing/invalid email",
    "contact_data_truncated": "Contact data truncated to fit Mailchimp limits",
    "tag_application_failed": "Contact upserted but tag application failed",
    "tag_verification_failed": "Tag applied but verification failed",
    "merge_field_missing": "Required merge field missing from Mailchimp",
    "api_rate_limited": "API rate limit exceeded, operations delayed",
    "contact_verification_failed": "Contact status verification failed after upsert",
    "list_name_change_detected": "HubSpot list name changed, tag rename attempted",
    "tag_rename_failed": "Tag rename operation failed, fallback required",
    "network_timeout": "Network timeout during API operation",
    "batch_processing_error": "Error processing contact batch",
    "invalid_email_format": "Invalid email format rejected by Mailchimp",
    "duplicate_contact": "Duplicate contact email detected",
    "contact_status_warning": "Contact has unexpected status in Mailchimp",
    "merge_field_creation_failed": "Failed to create required merge field",
    "data_type_mismatch": "Data type conversion required for Mailchimp"
}

def send_secondary_sync_notification(stats: Dict, 
                                   mappings: Dict[str, str], 
                                   summary_report: str,
                                   is_error: bool = False) -> bool:
    """
    Send Teams notification for secondary sync operations (Mailchimp → HubSpot)
    
    Args:
        stats: Secondary sync statistics
        mappings: Exit tag to HubSpot list mappings 
        summary_report: Full summary report text
        is_error: Whether this is an error notification
    """
    from . import config
    
    try:
        # Determine notification type and color
        if is_error:
            title = "❌ Secondary Sync Failed"
            theme_color = "dc3545"  # Red
            severity = "ERROR"
        elif stats.get('errors', 0) > 0:
            title = "⚠️ Secondary Sync Completed with Issues"
            theme_color = "ffc107"  # Yellow
            severity = "WARNING"
        else:
            title = "✅ Secondary Sync Completed Successfully"
            theme_color = "28a745"  # Green
            severity = "SUCCESS"
        
        # Build facts section
        facts = [
            {"name": "Timestamp", "value": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")},
            {"name": "Sync Direction", "value": "Mailchimp → HubSpot"},
            {"name": "Mode", "value": config.SECONDARY_SYNC_MODE},
            {"name": "Severity", "value": severity}
        ]
        
        if not is_error:
            facts.extend([
                {"name": "Contacts Processed", "value": str(stats.get('contacts_processed', 0))},
                {"name": "Contacts Imported", "value": str(stats.get('contacts_imported', 0))},
                {"name": "Contacts Removed (Anti-Remarketing)", "value": str(stats.get('contacts_removed_from_source', 0))},
                {"name": "Contacts Archived", "value": str(stats.get('contacts_archived', 0))},
                {"name": "Errors", "value": str(stats.get('errors', 0))}
            ])
        
        # Build sections
        sections = [
            {
                "activityTitle": title,
                "activitySubtitle": "Mailchimp exit tags → HubSpot lists synchronization",
                "activityImage": "https://img.icons8.com/color/48/000000/import.png",
                "facts": facts
            }
        ]
        
        # Add mapping details if successful
        if not is_error and mappings:
            mapping_text = ""
            for exit_tag, target_list in mappings.items():
                processed_count = 0
                # Count contacts processed for this specific tag (would need to be passed from stats)
                mapping_text += f"• **{exit_tag}** → HubSpot List **{target_list}** ({processed_count} contacts)\n"
            
            sections.append({
                "activityTitle": "📋 Processed Mappings",
                "text": mapping_text
            })
        
        # Add summary report (truncated)
        if summary_report and not is_error:
            # Truncate summary for Teams (max ~1000 chars per section)
            truncated_summary = summary_report[:800] + ("..." if len(summary_report) > 800 else "")
            sections.append({
                "activityTitle": "📊 Summary Report",
                "text": f"```\n{truncated_summary}\n```"
            })
        elif is_error:
            sections.append({
                "activityTitle": "❌ Error Details",
                "text": summary_report[:800] + ("..." if len(summary_report) > 800 else "")
            })
        
        # Build Teams message card
        card = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": theme_color,
            "summary": title,
            "sections": sections
        }
        
        # Send notification
        response = requests.post(
            config.TEAMS_WEBHOOK_URL,
            headers={"Content-Type": "application/json"},
            json=card,
            timeout=30
        )
        
        if response.status_code in [200, 202]:
            logger.info(f"✅ Secondary sync Teams notification sent successfully ({severity})")
            return True
        else:
            logger.error(f"❌ Secondary sync Teams notification failed: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error sending secondary sync Teams notification: {e}")
        return False


def send_bidirectional_sync_notification(primary_stats: Dict,
                                       secondary_stats: Dict,
                                       total_duration: str,
                                       has_errors: bool = False) -> bool:
    """
    Send Teams notification for bidirectional sync operations
    
    Args:
        primary_stats: Primary sync (HubSpot → Mailchimp) statistics
        secondary_stats: Secondary sync (Mailchimp → HubSpot) statistics
        total_duration: Total sync duration
        has_errors: Whether either sync had errors
    """
    from . import config
    
    try:
        # Determine notification type
        if has_errors:
            title = "⚠️ Bidirectional Sync Completed with Issues"
            theme_color = "ffc107"  # Yellow
            severity = "WARNING"
        else:
            title = "✅ Bidirectional Sync Completed Successfully"
            theme_color = "28a745"  # Green
            severity = "SUCCESS"
        
        # Build facts section
        facts = [
            {"name": "Timestamp", "value": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")},
            {"name": "Sync Type", "value": "Bidirectional (HubSpot ↔ Mailchimp)"},
            {"name": "Total Duration", "value": total_duration},
            {"name": "Severity", "value": severity},
            {"name": "Primary Mode", "value": config.RUN_MODE},
            {"name": "Secondary Mode", "value": config.SECONDARY_SYNC_MODE if config.ENABLE_SECONDARY_SYNC else "Disabled"}
        ]
        
        # Build sections
        sections = [
            {
                "activityTitle": title,
                "activitySubtitle": "Complete bidirectional contact synchronization",
                "activityImage": "https://img.icons8.com/color/48/000000/sync.png",
                "facts": facts
            }
        ]
        
        # Primary sync details
        primary_text = f"""
**Direction:** HubSpot → Mailchimp
**Contacts Processed:** {primary_stats.get('contacts_processed', 0)}
**Contacts Synced:** {primary_stats.get('contacts_synced', 0)}
**Errors:** {primary_stats.get('errors', 0)}
"""
        sections.append({
            "activityTitle": "📤 Primary Sync Results",
            "text": primary_text
        })
        
        # Secondary sync details (if enabled)
        if config.ENABLE_SECONDARY_SYNC and secondary_stats:
            secondary_text = f"""
**Direction:** Mailchimp → HubSpot  
**Contacts Processed:** {secondary_stats.get('contacts_processed', 0)}
**Contacts Imported:** {secondary_stats.get('contacts_imported', 0)}
**Contacts Removed (Anti-Remarketing):** {secondary_stats.get('contacts_removed_from_source', 0)}
**Contacts Archived:** {secondary_stats.get('contacts_archived', 0)}
**Errors:** {secondary_stats.get('errors', 0)}
"""
            sections.append({
                "activityTitle": "📥 Secondary Sync Results", 
                "text": secondary_text
            })
        else:
            sections.append({
                "activityTitle": "📥 Secondary Sync",
                "text": "Secondary sync was disabled for this operation"
            })
        
        # Build Teams message card
        card = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions", 
            "themeColor": theme_color,
            "summary": title,
            "sections": sections
        }
        
        # Send notification
        response = requests.post(
            config.TEAMS_WEBHOOK_URL,
            headers={"Content-Type": "application/json"},
            json=card,
            timeout=30
        )
        
        if response.status_code in [200, 202]:
            logger.info(f"✅ Bidirectional sync Teams notification sent successfully ({severity})")
            return True
        else:
            logger.error(f"❌ Bidirectional sync Teams notification failed: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error sending bidirectional sync Teams notification: {e}")
        return False


def send_configuration_validation_notification(validation_results: Dict,
                                              is_error: bool = False) -> bool:
    """
    Send Teams notification for configuration validation results
    
    Args:
        validation_results: Results from list configuration validation
        is_error: Whether validation failed
    """
    from . import config
    
    try:
        if is_error:
            title = "❌ Configuration Validation Failed"
            theme_color = "dc3545"  # Red
            severity = "ERROR"
        else:
            title = "✅ Configuration Validation Passed"
            theme_color = "28a745"  # Green
            severity = "SUCCESS"
        
        # Build facts
        facts = [
            {"name": "Timestamp", "value": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")},
            {"name": "Validation Type", "value": "Secondary Sync Configuration"},
            {"name": "Severity", "value": severity},
            {"name": "Target Lists", "value": str(len(config.SECONDARY_SYNC_MAPPINGS))},
            {"name": "Source Lists", "value": str(len(config.LIST_EXCLUSION_RULES))}
        ]
        
        sections = [
            {
                "activityTitle": title,
                "activitySubtitle": "HubSpot list configuration validation for bidirectional sync",
                "activityImage": "https://img.icons8.com/color/48/000000/checklist.png",
                "facts": facts
            }
        ]
        
        # Add validation details
        if is_error and 'errors' in validation_results:
            error_text = ""
            for error in validation_results['errors'][:5]:  # Show first 5 errors
                error_text += f"• {error}\n"
            
            sections.append({
                "activityTitle": "❌ Validation Errors",
                "text": error_text
            })
        
        # Add configuration summary
        if 'summary' in validation_results:
            summary = validation_results['summary']
            config_text = f"""
**Secondary Sync Mappings:** {summary.get('total_target_lists', 0)}
**Exclusion Rules:** {summary.get('total_source_lists', 0)}
**Status:** {'All lists validated successfully' if not is_error else 'Validation failed - check configuration'}
"""
            sections.append({
                "activityTitle": "📋 Configuration Summary",
                "text": config_text
            })
        
        # Build Teams message card
        card = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": theme_color,
            "summary": title,
            "sections": sections
        }
        
        # Send notification
        response = requests.post(
            config.TEAMS_WEBHOOK_URL,
            headers={"Content-Type": "application/json"},
            json=card,
            timeout=30
        )
        
        if response.status_code in [200, 202]:
            logger.info(f"✅ Configuration validation Teams notification sent successfully ({severity})")
            return True
        else:
            logger.error(f"❌ Configuration validation Teams notification failed: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error sending configuration validation Teams notification: {e}")
        return False
