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
    
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
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
                return False
                
        except Exception as e:
            logger.error(f"❌ Error sending Teams notification: {e}")
            return False
    
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
