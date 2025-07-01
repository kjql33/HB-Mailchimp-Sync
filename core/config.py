#!/usr/bin/env python3
"""
🎯 HUBSPOT ↔ MAILCHIMP BIDIRECTIONAL SYNC - PRODUCTION CONFIGURATION
===================================================================

THIS IS YOUR MAIN CONTROL CENTER - Everything you need to configure is here!

🚀 COMPLETE FLOW EXPLANATION:
============================

STEP 1: HubSpot → Mailchimp (PRIMARY SYNC)
   📋 Contacts from HubSpot lists → Mailchimp for marketing
   🏷️ Contacts get tagged in Mailchimp based on processing

STEP 2: Mailchimp → HubSpot (SECONDARY SYNC) 
   📥 Tagged contacts in Mailchimp → New HubSpot lists
   🚫 Contacts removed from original HubSpot lists (anti-remarketing)

⚙️ QUICK SETUP - EDIT THESE 3 SECTIONS BELOW:
=============================================

1️⃣ INPUT LISTS (Line ~55): Which HubSpot lists to sync TO Mailchimp
2️⃣ EXIT MAPPINGS (Line ~105): Which Mailchimp tags route to which HubSpot lists  
3️⃣ REMOVAL RULES (Line ~120): Anti-remarketing rules (remove from original lists)

🔧 HOW TO WIRE EVERYTHING:
=========================

📋 STEP 1 - SET INPUT LISTS:
   HUBSPOT_LIST_IDS = ["677", "123", "456"]
   ↳ These HubSpot lists will sync their contacts To Mailchimp

🏷️ STEP 2 - MAP EXIT TAGS TO DESTINATION LISTS:
   SECONDARY_SYNC_MAPPINGS = {
       "qualified_leads": "680",      # Mailchimp tag → HubSpot list
       "hot_prospects": "681",        # Tag "hot_prospects" → List 681
       "converted": "682"             # Tag "converted" → List 682
   }

🚫 STEP 3 - SET ANTI-REMARKETING RULES:
   LIST_EXCLUSION_RULES = {
       "677": ["680", "681", "682"],  # Remove from 677 when added to any of these
       "123": ["680", "682"],         # Remove from 123 when added to 680 or 682
   }

🎮 EXECUTION COMMANDS:
=====================
   python -m core.config              # Run full bidirectional sync
   python -m core.config --clean      # Clean logs first, then run

📊 EXAMPLE REAL-WORLD SETUP:
============================
   INPUT: HubSpot List 677 "Lead Nurture" → Mailchimp
   PROCESSING: Mailchimp applies tag "qualified_leads" to hot prospects  
   OUTPUT: Tagged contacts → HubSpot List 680 "Qualified Leads"
   CLEANUP: Contact removed from List 677 (no duplicate marketing)

⚡ PERFORMANCE: Set PERFORMANCE_MODE=AGGRESSIVE for 2x speed boost
"""

import os
import sys
from typing import Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv(override=True)

# =============================================================================
# 🎮 OPERATIONAL CONTROLS - Modify these for different run types
# =============================================================================

# Run Mode Selection
RUN_MODE = "BIDIRECTIONAL_SYNC"  # Options: "FULL_SYNC", "TEST_RUN", "TAG_RENAME_ONLY", "SECONDARY_SYNC_ONLY", "BIDIRECTIONAL_SYNC"

# =============================================================================
# 🔄 BIDIRECTIONAL SYNC CONTROLS
# =============================================================================

# Enable secondary sync (Mailchimp → HubSpot)
ENABLE_SECONDARY_SYNC = True  # Set to True when target lists are created

# Secondary sync mode settings
SECONDARY_SYNC_MODE = "FULL_SYNC"  # Options: "FULL_SYNC", "TEST_RUN"
SECONDARY_TEST_CONTACT_LIMIT = 0  # 0 = unlimited contacts

# Override from environment variables for testing
SECONDARY_SYNC_MODE = os.environ.get("SECONDARY_SYNC_MODE", SECONDARY_SYNC_MODE)
SECONDARY_TEST_CONTACT_LIMIT = int(os.environ.get("SECONDARY_TEST_CONTACT_LIMIT", SECONDARY_TEST_CONTACT_LIMIT))

# Archive processed contacts from Mailchimp after successful import
ENABLE_MAILCHIMP_ARCHIVAL = True  # Production setting - archive processed contacts

# =============================================================================
# 📋 INPUT LISTS (HubSpot → Mailchimp) - EDIT HERE FOR STEP 1
# =============================================================================

# ✅ STEP 1: ADD YOUR HUBSPOT LISTS HERE
# These are the HubSpot lists that will sync their contacts TO Mailchimp for processing
# Example: If you have lists 677, 123, 456 that need marketing processing, add them here

HUBSPOT_LIST_IDS = [
    "718",  # Production marketing list 1
    "719",  # Production marketing list 2  
    "720",  # Production marketing list 3
    "751",  # Production marketing list 4
]

# ⚠️ IMPORTANT: Each contact gets tagged with their source list ID in Mailchimp
# This allows tracking where they came from for smart removal later

# =============================================================================
# 🚫 HARD EXCLUDE LISTS (Pre-sync Filter) - EDIT HERE FOR EXCLUSIONS
# =============================================================================

# ✅ HARD EXCLUDE: Contacts in these HubSpot lists will NEVER be synced to Mailchimp
# Use this for contacts you're in active talks with, VIP clients, or anyone who should never receive marketing
# Format: ["list_id_1", "list_id_2", "list_id_3"]
HARD_EXCLUDE_LISTS = [
    "717",  # Active deal association - don't market to these contacts
    # "456",  # Example: Active Sales Discussions - No marketing
    # "789",  # Example: Opted Out - Hard exclude
]

# 🎯 HOW HARD EXCLUDE WORKS:
# 1. Before syncing any contact from 718, 719, 720, or 751 to Mailchimp
# 2. System checks if contact is also in ANY hard exclude list
# 3. If found in exclude list → Skip completely (no sync to Mailchimp)
# 4. If NOT found in exclude list → Proceed with normal sync

# 💡 EXAMPLE USAGE:
# If you have 100 contacts in list 718, but 3 of them are also in list 123 (VIP Clients)
# Then only 97 contacts will sync to Mailchimp - the 3 VIP contacts are protected

# Test/Development Settings
TEST_CONTACT_LIMIT = 0      # 0 = unlimited contacts (ready for production)
ENABLE_DRY_RUN = False      # Set True to simulate without actual changes

# =============================================================================
# 🔐 API CREDENTIALS
# =============================================================================

# HubSpot API Configuration
HUBSPOT_PRIVATE_TOKEN = os.getenv("HUBSPOT_PRIVATE_TOKEN", "")

# Mailchimp API Configuration  
MAILCHIMP_API_KEY = os.getenv("MAILCHIMP_API_KEY", "").strip()
MAILCHIMP_LIST_ID = os.getenv("MAILCHIMP_LIST_ID", "").strip()

# Extract datacenter from API key to avoid GitHub Actions secret newline issues
def get_mailchimp_datacenter():
    """
    Extract datacenter from Mailchimp API key.
    
    Mailchimp API keys are formatted as: <key>-<datacenter>
    This eliminates dependency on the problematic MAILCHIMP_DC secret.
    """
    if not MAILCHIMP_API_KEY:
        return ""
    
    if '-' in MAILCHIMP_API_KEY:
        extracted_dc = MAILCHIMP_API_KEY.split('-')[-1].strip()
        print(f"📍 Extracted datacenter from API key: {extracted_dc}")
        return extracted_dc
    else:
        # Fallback to environment variable if API key format is unexpected
        dc_from_env = os.getenv("MAILCHIMP_DC", "").strip()
        print(f"⚠️  Could not extract datacenter from API key, using environment variable: {dc_from_env}")
        return dc_from_env

MAILCHIMP_DC = get_mailchimp_datacenter()

# Microsoft Teams Notifications
TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL", "")

# =============================================================================
# ⚙️ SYNC PARAMETERS
# =============================================================================

# Processing settings
PAGE_SIZE = int(os.getenv("PAGE_SIZE", 20))               # records per HubSpot page
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))           # API retry attempts
RETRY_DELAY = int(os.getenv("RETRY_DELAY", 2))           # seconds between retries

# =============================================================================
# 📋 DATA MAPPING
# =============================================================================

# PRIMARY SYNC (HubSpot → Mailchimp) - Existing mappings
# Mailchimp merge fields to maintain
REQUIRED_TAGS = [
    "FNAME",    # first name
    "LNAME",    # last name
    "COMPANY",  # company name
    "PHONE",    # phone number
    "ADDRESS",  # street address
    "ADDRESS2", # street address 2
    "CITY",     # city
    "STATE",    # state/region
    "POSTCODE", # postal code
    "COUNTRY",  # country/region
    "BRANCHES"  # branch assignment
]

# 🔍 SOURCE LIST TRACKING FIELD
# This field stores the original HubSpot list ID(s) for each contact in Mailchimp
# Enables source-aware removal instead of broadcast removal
ORI_LISTS_FIELD = "ORI_LISTS"
REQUIRED_TAGS.append(ORI_LISTS_FIELD)

# =============================================================================
# 🏷️ EXIT TAGS → OUTPUT LISTS (Mailchimp → HubSpot) - EDIT HERE FOR STEP 2
# =============================================================================

# ✅ STEP 2: MAP YOUR MAILCHIMP EXIT TAGS TO HUBSPOT DESTINATION LISTS
# When a contact gets tagged in Mailchimp with an "exit tag", they move to a new HubSpot list
# Format: "mailchimp_tag_name": "hubspot_list_id"

SECONDARY_SYNC_MAPPINGS = {
    # PRODUCTION MAPPINGS (currently active):
    "handover_to_sales": "700",
    "archive_engaged_competition_twice": "703",
    "archive_engaged_competition_once": "702",
    "archive_engaged_competition_never": "701",
    "archive_engaged_recruitment_twice": "703",
    "archive_engaged_recruitment_once": "702",
    "archive_engaged_recruitment_never": "701",
    "archive_engaged_general_twice": "703",
    "archive_engaged_general_once": "702",
    "archive_engaged_general_never": "701", 


    # Keep test mapping for reference:
    # "engaged_test_tag": "676",         # TEST: Engaged contacts → List 676
}

# 💡 MAILCHIMP TAG NAMING TIPS:
# - Use clear, descriptive names: "qualified_leads" not "ql1"
# - Use underscores, not spaces: "hot_prospects" not "hot prospects"  
# - Be consistent: "demo_scheduled", "call_scheduled", "meeting_scheduled"

# =============================================================================
# 🚫 EXCLUSION RULES (Anti-remarketing) - EDIT HERE FOR STEP 3
# =============================================================================

# ✅ STEP 3: SET UP ANTI-REMARKETING RULES
# These rules specify which source lists should have contacts removed when routed to destination lists
# This prevents contacts from receiving duplicate marketing from both old and new lists

LIST_EXCLUSION_RULES = {
    # Format: "source_list_id": ["destination_list_1", "destination_list_2", ...]
    "718": ["700", "701", "702", "703"], # Remove from 718 when added to ANY destination list
    "719": ["700", "701", "702", "703"], # Remove from 719 when added to ANY destination list  
    "720": ["700", "701", "702", "703"], # Remove from 720 when added to ANY destination list
    "751": ["700", "701", "702", "703"], # Remove from 751 when added to ANY destination list
    
    # Keep test rule for reference:
    # "677": ["676"],  # TEST: If added to list 676, remove from 677
}

# 🎯 RULE EXAMPLES:
# If contact moves from List 677 "Lead Nurture" → List 680 "Qualified Leads"
# Then contact is automatically removed from List 677 to prevent duplicate marketing

# Exit tags that trigger contact removal from source lists (anti-remarketing)
REMOVAL_TRIGGER_TAGS = list(SECONDARY_SYNC_MAPPINGS.keys())

# Processing delay settings for secondary sync
SECONDARY_SYNC_DELAY_HOURS = 0  # Wait time before processing exit-tagged contacts (set to 0 for testing)

# =============================================================================
# 🗂️ STORAGE SETTINGS
# =============================================================================

# Logging level
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Directory for raw data exports
RAW_DATA_DIR = os.getenv("RAW_DATA_DIR", "raw_data")

# =============================================================================
# ⚡ PERFORMANCE CONFIGURATION
# =============================================================================

class PerformanceConfig:
    """Dynamic performance configuration based on environment variables"""
    
    def __init__(self):
        self.load_config()
    
    def load_config(self):
        """Load performance configuration from environment variables"""
        
        # =============================================================================
        # 🚀 API TIMING OPTIMIZATIONS
        # =============================================================================
        
        # Mailchimp API delays (default: conservative, optimized: aggressive)
        self.mailchimp_upsert_delay = float(os.getenv("MAILCHIMP_UPSERT_DELAY", "2.0"))    # Default: 2s, Optimized: 0.1s
        self.mailchimp_tag_delay = float(os.getenv("MAILCHIMP_TAG_DELAY", "1.0"))          # Default: 1s, Optimized: 0.05s
        self.mailchimp_verify_delay = float(os.getenv("MAILCHIMP_VERIFY_DELAY", "1.0"))    # Default: 1s, Optimized: 0.1s
        
        # HubSpot API delays
        self.hubspot_page_delay = float(os.getenv("HUBSPOT_PAGE_DELAY", "0.5"))            # Default: 0.5s, Optimized: 0.1s
        
        # =============================================================================
        # 🔍 VERIFICATION & VALIDATION
        # =============================================================================
        
        # Verification modes: FULL, FAST, MINIMAL
        self.verification_mode = os.getenv("MAILCHIMP_VERIFICATION_MODE", "FULL")
        
        # Skip verification for successful operations (reduces API calls by ~33%)
        self.skip_success_verification = os.getenv("SKIP_SUCCESS_VERIFICATION", "false").lower() == "true"
        
        # Batch verification (verify multiple contacts in one call where possible)
        self.enable_batch_verification = os.getenv("ENABLE_BATCH_VERIFICATION", "false").lower() == "true"
        
        # =============================================================================
        # 📦 BATCH PROCESSING
        # =============================================================================
        
        # Enable batch operations where API supports it
        self.enable_batch_processing = os.getenv("ENABLE_BATCH_PROCESSING", "false").lower() == "true"
        
        # Batch sizes for different operations
        self.batch_size_upsert = int(os.getenv("BATCH_SIZE_UPSERT", "10"))                 # Mailchimp batch upsert
        self.batch_size_tagging = int(os.getenv("BATCH_SIZE_TAGGING", "50"))               # Mailchimp batch tagging
        
        # =============================================================================
        # ⚡ RATE LIMITING
        # =============================================================================
        
        # Rate limiting modes: CONSERVATIVE, ADAPTIVE, AGGRESSIVE
        self.rate_limit_mode = os.getenv("API_RATE_LIMIT_MODE", "CONSERVATIVE")
        
        # Adaptive rate limiting (adjust delays based on API response times)
        self.enable_adaptive_delays = os.getenv("ENABLE_ADAPTIVE_DELAYS", "false").lower() == "true"
        
        # =============================================================================
        # 🔄 RETRY LOGIC
        # =============================================================================
        
        # Retry configuration (from main config but can be overridden for performance)
        self.max_retries = int(os.getenv("MAX_RETRIES", "3"))
        self.retry_delay = float(os.getenv("RETRY_DELAY", "2.0"))
        
        # Exponential backoff (more efficient than fixed delays)
        self.enable_exponential_backoff = os.getenv("ENABLE_EXPONENTIAL_BACKOFF", "false").lower() == "true"
        
        # =============================================================================
        # 💾 MEMORY & RESOURCE OPTIMIZATION  
        # =============================================================================
        
        # Process contacts in chunks to manage memory
        self.contact_processing_chunk_size = int(os.getenv("CONTACT_CHUNK_SIZE", "100"))
        
        # Clear caches periodically
        self.enable_memory_cleanup = os.getenv("ENABLE_MEMORY_CLEANUP", "true").lower() == "true"
        
    def get_optimized_delays(self) -> Dict[str, float]:
        """Get current delay configuration"""
        return {
            'upsert_delay': self.mailchimp_upsert_delay,
            'tag_delay': self.mailchimp_tag_delay,
            'verify_delay': self.mailchimp_verify_delay,
            'page_delay': self.hubspot_page_delay
        }
    
    def is_fast_mode(self) -> bool:
        """Check if running in fast/optimized mode"""
        return (self.mailchimp_upsert_delay < 1.0 or 
                self.rate_limit_mode == "AGGRESSIVE" or
                self.skip_success_verification)
    
    def get_verification_config(self) -> Dict[str, Any]:
        """Get verification settings"""
        return {
            'mode': self.verification_mode,
            'skip_success': self.skip_success_verification,
            'batch_enabled': self.enable_batch_verification
        }
    
    def get_batch_config(self) -> Dict[str, Any]:
        """Get batch processing settings"""
        return {
            'enabled': self.enable_batch_processing,
            'upsert_size': self.batch_size_upsert,
            'tagging_size': self.batch_size_tagging
        }
    
    def get_rate_limit_config(self) -> Dict[str, Any]:
        """Get rate limiting settings"""
        return {
            'mode': self.rate_limit_mode,
            'adaptive': self.enable_adaptive_delays
        }
    
    def apply_performance_profile(self, profile: str) -> bool:
        """Apply a predefined performance profile"""
        profiles = {
            'CONSERVATIVE': {
                'MAILCHIMP_UPSERT_DELAY': '2.0',
                'MAILCHIMP_TAG_DELAY': '1.0',
                'MAILCHIMP_VERIFY_DELAY': '1.0',
                'HUBSPOT_PAGE_DELAY': '0.5',
                'MAILCHIMP_VERIFICATION_MODE': 'FULL',
                'ENABLE_BATCH_PROCESSING': 'false',
                'API_RATE_LIMIT_MODE': 'CONSERVATIVE',
                'SKIP_SUCCESS_VERIFICATION': 'false'
            },
            'AGGRESSIVE': {
                'MAILCHIMP_UPSERT_DELAY': '0.1',
                'MAILCHIMP_TAG_DELAY': '0.05',
                'MAILCHIMP_VERIFY_DELAY': '0.1',
                'HUBSPOT_PAGE_DELAY': '0.1',
                'MAILCHIMP_VERIFICATION_MODE': 'MINIMAL',
                'ENABLE_BATCH_PROCESSING': 'true',
                'API_RATE_LIMIT_MODE': 'AGGRESSIVE',
                'SKIP_SUCCESS_VERIFICATION': 'true'
            }
        }
        
        if profile in profiles:
            for key, value in profiles[profile].items():
                os.environ[key] = value
            
            # Reload configuration
            self.load_config()
            return True
        
        return False
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get a summary of current performance configuration"""
        return {
            'profile': 'AGGRESSIVE' if self.is_fast_mode() else 'CONSERVATIVE',
            'delays': self.get_optimized_delays(),
            'verification': self.get_verification_config(),
            'batch_processing': self.get_batch_config(),
            'rate_limiting': self.get_rate_limit_config(),
            'estimated_speedup': self._calculate_estimated_speedup()
        }
    
    def _calculate_estimated_speedup(self) -> float:
        """Calculate estimated speedup factor compared to conservative settings"""
        # Base calculation on delay reductions
        conservative_total_delay = 2.0 + 1.0 + 1.0  # upsert + tag + verify
        current_total_delay = self.mailchimp_upsert_delay + self.mailchimp_tag_delay + self.mailchimp_verify_delay
        
        delay_speedup = conservative_total_delay / max(current_total_delay, 0.1)
        
        # Additional speedup from batch processing and reduced verification
        batch_speedup = 1.5 if self.enable_batch_processing else 1.0
        verification_speedup = 1.3 if self.skip_success_verification else 1.0
        
        total_speedup = delay_speedup * batch_speedup * verification_speedup
        
        return round(total_speedup, 2)

# Global performance configuration instance
perf_config = PerformanceConfig()

# =============================================================================
# 🚀 EXECUTION FUNCTIONS
# =============================================================================

def clean_workspace():
    """Clean logs and raw_data directories for fresh start"""
    import shutil
    
    directories = ["logs", "raw_data"]
    for directory in directories:
        if os.path.exists(directory):
            shutil.rmtree(directory)
            print(f"🧹 Cleaned {directory}/")
        os.makedirs(directory, exist_ok=True)
        print(f"📁 Created fresh {directory}/")

def run_sync():
    """Execute the main sync operation"""
    from . import sync
    
    print(f"🎮 Running in {RUN_MODE} mode")
    print(f"📋 Processing {len(HUBSPOT_LIST_IDS)} HubSpot list(s)")
    if RUN_MODE == "TEST_RUN":
        print(f"🧪 Test mode: Limited to {TEST_CONTACT_LIMIT} contacts")
    
    sync.main()

def run_secondary_sync():
    """Execute secondary sync operation (Mailchimp → HubSpot)"""
    from . import secondary_sync
    
    print(f"🔄 Running SECONDARY SYNC in {SECONDARY_SYNC_MODE} mode")
    print(f"📥 Processing Mailchimp exit tags → HubSpot lists")
    if SECONDARY_SYNC_MODE == "TEST_RUN":
        print(f"🧪 Test mode: Limited to {SECONDARY_TEST_CONTACT_LIMIT} contacts")
    
    secondary_sync.main()

def run_bidirectional_sync():
    """Execute full bidirectional sync operation"""
    print("🔄 Running BIDIRECTIONAL SYNC")
    print("="*60)
    
    # Phase 1: Primary sync (HubSpot → Mailchimp)
    print("📤 Phase 1: HubSpot → Mailchimp sync")
    print("-"*30)
    run_sync()
    
    print("\n" + "="*60)
    
    # Phase 2: Secondary sync (Mailchimp → HubSpot) 
    if ENABLE_SECONDARY_SYNC:
        print("📥 Phase 2: Mailchimp → HubSpot sync")
        print("-"*30)
        run_secondary_sync()
    else:
        print("⏭️ Phase 2: Secondary sync disabled")
    
    print("\n✅ Bidirectional sync completed!")

def validate_configuration():
    """Validate configuration settings before execution"""
    errors = []
    warnings = []
    
    # Check API credentials
    if not HUBSPOT_PRIVATE_TOKEN:
        errors.append("HUBSPOT_PRIVATE_TOKEN not configured")
    if not MAILCHIMP_API_KEY:
        errors.append("MAILCHIMP_API_KEY not configured")
    if not MAILCHIMP_LIST_ID:
        errors.append("MAILCHIMP_LIST_ID not configured")
    
    # Check secondary sync configuration
    if ENABLE_SECONDARY_SYNC:
        if not SECONDARY_SYNC_MAPPINGS:
            warnings.append("SECONDARY_SYNC_MAPPINGS is empty")
        if not LIST_EXCLUSION_RULES:
            warnings.append("LIST_EXCLUSION_RULES is empty - no anti-remarketing protection")
    
    # Print validation results
    if errors:
        print("❌ CONFIGURATION ERRORS:")
        for error in errors:
            print(f"   • {error}")
        return False
    
    if warnings:
        print("⚠️ CONFIGURATION WARNINGS:")
        for warning in warnings:
            print(f"   • {warning}")
    
    print("✅ Configuration validated")
    return True

def main():
    """Main execution function"""
    print("="*60)
    print("🎯 HUBSPOT ↔ MAILCHIMP SYNC CONTROL CENTER")
    print("="*60)
    
    # Validate configuration
    if not validate_configuration():
        print("❌ Configuration errors detected. Please fix and try again.")
        return
    
    # Show current settings
    print(f"Mode: {RUN_MODE}")
    print(f"Primary Lists: {HUBSPOT_LIST_IDS}")
    print(f"Secondary Sync: {'Enabled' if ENABLE_SECONDARY_SYNC else 'Disabled'}")
    if RUN_MODE == "TEST_RUN":
        print(f"Test Limit: {TEST_CONTACT_LIMIT}")
    if ENABLE_SECONDARY_SYNC and SECONDARY_SYNC_MODE == "TEST_RUN":
        print(f"Secondary Test Limit: {SECONDARY_TEST_CONTACT_LIMIT}")
    print("-"*60)
    
    # Execute based on mode
    if len(sys.argv) > 1 and sys.argv[1] == "--clean":
        clean_workspace()
        print("✅ Workspace cleaned. Run again without --clean to sync.")
        return
    
    # Route to appropriate sync function
    if RUN_MODE == "SECONDARY_SYNC_ONLY":
        if not ENABLE_SECONDARY_SYNC:
            print("❌ Secondary sync is disabled. Enable ENABLE_SECONDARY_SYNC first.")
            return
        run_secondary_sync()
    elif RUN_MODE == "BIDIRECTIONAL_SYNC":
        run_bidirectional_sync()
    else:
        # Default: primary sync modes (FULL_SYNC, TEST_RUN, TAG_RENAME_ONLY)
        run_sync()
    
    print("✅ Sync operations completed.")

if __name__ == "__main__":
    main()
