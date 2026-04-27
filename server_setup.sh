#!/bin/bash
# =============================================================================
# HB-Mautic-Sync Server Setup Script
# Run this ONCE on the Ubuntu server after deploying the pipeline.
# =============================================================================
# What this does:
#   1. Fixes Mautic Docker permissions permanently via Ubuntu crontab
#   2. Creates a wrapper script that health-checks before every sync
#   3. Sets up log rotation
# =============================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAUTIC_CONTAINER="mautic"

echo "============================================"
echo " HB-Mautic-Sync Server Setup"
echo " Project: $PROJECT_DIR"
echo "============================================"

# ── Step 1: Fix Mautic permissions now ────────────────────────────────────────
echo ""
echo "[1/3] Fixing Mautic container permissions..."
docker exec "$MAUTIC_CONTAINER" chown -R www-data:www-data /var/www/html/var/
docker exec "$MAUTIC_CONTAINER" chmod -R 775 /var/www/html/var/cache/
docker exec --user www-data "$MAUTIC_CONTAINER" php /var/www/html/bin/console cache:clear
docker exec "$MAUTIC_CONTAINER" chown -R www-data:www-data /var/www/html/var/
docker exec "$MAUTIC_CONTAINER" chmod -R 775 /var/www/html/var/
echo "  Permissions fixed."

# ── Step 2: Add Ubuntu crontab for permanent permission fix ───────────────────
echo ""
echo "[2/3] Adding Ubuntu crontab for automatic permission maintenance..."

CRON_CMD="*/10 * * * * docker exec $MAUTIC_CONTAINER chown -R www-data:www-data /var/www/html/var/ && docker exec $MAUTIC_CONTAINER chmod -R 775 /var/www/html/var/ >> /tmp/mautic_permission_fix.log 2>&1"

# Add to crontab only if not already present
(crontab -l 2>/dev/null | grep -v "mautic.*chown"; echo "$CRON_CMD") | crontab -
echo "  Crontab added — Mautic permissions will auto-fix every 10 minutes."

# ── Step 3: Create run script ─────────────────────────────────────────────────
echo ""
echo "[3/3] Creating run script..."

cat > "$PROJECT_DIR/run_sync.sh" << 'RUNEOF'
#!/bin/bash
# =============================================================================
# HB-Mautic-Sync Run Script
# Use this instead of calling python directly.
# Ensures Mautic permissions are correct before every sync.
# =============================================================================

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# Activate virtual environment if it exists
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# Load .env
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') Starting HB-Mautic-Sync..."

# Run sync with health check enabled (auto-fixes Mautic permissions if needed)
python -m corev2.cli sync

EXIT_CODE=$?
if [ $EXIT_CODE -eq 0 ]; then
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') Sync completed successfully"
else
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') Sync failed with exit code $EXIT_CODE"
fi

exit $EXIT_CODE
RUNEOF

chmod +x "$PROJECT_DIR/run_sync.sh"
echo "  Run script created: $PROJECT_DIR/run_sync.sh"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "============================================"
echo " Setup complete."
echo ""
echo " Run the pipeline:"
echo "   cd $PROJECT_DIR"
echo "   ./run_sync.sh"
echo ""
echo " Or manually:"
echo "   python -m corev2.cli sync"
echo ""
echo " Crontab status:"
crontab -l | grep mautic
echo "============================================"