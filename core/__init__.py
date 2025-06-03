"""
HubSpot to Mailchimp Sync System - Core Package

This package contains the core functionality for syncing contacts between HubSpot and Mailchimp,
including the main sync logic, configuration management, and notification systems.

Core modules:
- main: Configuration and execution control
- sync: Main synchronization implementation
- notifications: Teams notification system
"""

__version__ = "1.0.0"

# Make modules available for import
__all__ = [
    'main',
    'sync', 
    'notifications'
]
