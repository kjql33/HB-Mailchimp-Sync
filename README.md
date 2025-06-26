# 🎯 HubSpot ↔ Mailchimp Bidirectional Sync

**Production-ready bidirectional synchronization between HubSpot and Mailchimp with intelligent compliance handling and anti-remarketing protection.**

## 🚀 Quick Start

```bash
# Run the bidirectional sync
python -m core.config

# Full documentation and setup guides
open info/README.md
```

## 📁 Project Structure

- **`core/`** - Core sync functionality and configuration
- **`info/README.md`** - Complete setup and usage documentation  
- **`.github/workflows/`** - GitHub Actions automation

## 🎯 Current Status

- ✅ **Production Ready**: Unlimited contact processing, robust error handling
- ✅ **Bidirectional Sync**: Full HubSpot ↔ Mailchimp synchronization
- ✅ **Compliance Handling**: Silent processing of unsubscribed/bounced contacts
- ✅ **Anti-Remarketing**: Automatic contact removal to prevent duplicate marketing
- ✅ **GitHub Actions**: Automated scheduled runs with Teams notifications

## 🔧 Configuration

All settings in `core/config.py`:
- **Current Lists**: `["718", "719", "720"]` (production lists)
- **Contact Limit**: `0` (unlimited - production ready)
- **Secondary Sync**: Enabled with comprehensive exit tag mappings
- **Compliance**: Silent handling with no Teams notifications

## 📋 Features

- **Scalable**: Handles hundreds of contacts per list with proper pagination
- **Reliable**: Comprehensive error handling and retry logic
- **Intelligent**: Compliance state detection and silent processing
- **Auditable**: Complete logging and notification system
- **Flexible**: Multiple sync modes and configuration options
