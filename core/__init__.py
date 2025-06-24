"""
HubSpot ↔ Mailchimp Bidirectional Sync System - Core Package

This package contains the core functionality for bidirectional contact synchronization
between HubSpot and Mailchimp, including primary sync, secondary sync with anti-remarketing,
list management, and comprehensive notification systems.

Core modules:
- main: Configuration and execution control for all sync modes
- sync: Primary synchronization implementation (HubSpot → Mailchimp) 
- secondary_sync: Secondary synchronization implementation (Mailchimp → HubSpot)
- list_manager: HubSpot list management and anti-remarketing operations
- notifications: Enhanced Teams notification system for all sync scenarios
"""

__version__ = "2.0.0"

# Make modules available for import
__all__ = [
    'main',
    'sync', 
    'secondary_sync',
    'list_manager',
    'notifications'
]
