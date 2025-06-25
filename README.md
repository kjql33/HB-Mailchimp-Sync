# HubSpot ↔ Mailchimp Bidirectional Sync

## 🚀 Quick Start

```bash
# Primary sync (HubSpot → Mailchimp) - Currently operational
python main.py

# Full documentation and setup guides
open info/README.md
```

## 📁 Documentation Structure

- **`info/README.md`** - Complete setup and usage documentation
- **`Tests/`** - All test scripts and validation tools
- **`info/IMPLEMENTATION_SUMMARY.md`** - What was built and what's next

## 🎯 Current Status

- ✅ **Primary Sync**: Operational (HubSpot → Mailchimp)  
- ✅ **Secondary Sync**: Implemented and ready (Mailchimp → HubSpot)
- ✅ **Anti-Remarketing**: Built-in protection against re-marketing
- ✅ **Multiple Modes**: Test, production, bidirectional, and secondary-only modes

## 🔧 Configuration

All settings in `core/main.py`:
- **Current Mode**: `TEST_RUN` (5 contacts, safe for testing)
- **Primary Lists**: `["692"]` (operational)
- **Secondary Sync**: Disabled by default for safety

## 📋 Next Steps

1. **Continue current operations**: Primary sync works perfectly as-is
2. **When ready for bidirectional**: Create HubSpot destination lists and update `SECONDARY_SYNC_MAPPINGS`
3. **Enable secondary sync**: Set `ENABLE_SECONDARY_SYNC = True` in `core/main.py`

See `IMPLEMENTATION_SUMMARY.md` for complete roadmap.
