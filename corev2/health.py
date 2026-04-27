"""
Mautic Health Check Utility.

Runs before every sync to ensure Mautic API is reachable.
If the API returns 500 (permissions issue), this module
auto-fixes the Docker container permissions and retries.

Root cause of 500 errors:
  /var/www/html/var/cache/prod/jms_serializer_default is not writable.
  This happens when cache:clear is run as root instead of www-data,
  or when the container restarts and recreates cache dirs as root.

Auto-fix steps:
  1. chown -R www-data:www-data /var/www/html/var/
  2. chmod -R 775 /var/www/html/var/cache/
  3. cache:clear as www-data
  4. chown again (cache:clear recreates dirs)
  5. Retry API check
"""

import asyncio
import logging
import subprocess
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_HEALTH_CHECK_URL = "/api/contacts"
_HEALTH_CHECK_PARAMS = {"limit": 1, "start": 0}
_MAX_FIX_ATTEMPTS = 2
_MAUTIC_CONTAINER = "mautic"


def _run_docker_cmd(cmd: str) -> tuple:
    """Run a docker exec command on the Mautic container."""
    full_cmd = f"docker exec {_MAUTIC_CONTAINER} {cmd}"
    result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def _run_docker_cmd_as_user(cmd: str, user: str = "www-data") -> tuple:
    """Run a docker exec command as a specific user."""
    full_cmd = f"docker exec --user {user} {_MAUTIC_CONTAINER} {cmd}"
    result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def fix_mautic_permissions() -> bool:
    """
    Fix Mautic container file permissions.
    Returns True if fix was applied successfully.
    """
    logger.warning("Applying Mautic permission fix...")

    steps = [
        ("chown -R www-data:www-data /var/www/html/var/", "root"),
        ("chmod -R 775 /var/www/html/var/cache/", "root"),
    ]

    for cmd, user in steps:
        if user == "root":
            rc, out, err = _run_docker_cmd(cmd)
        else:
            rc, out, err = _run_docker_cmd_as_user(cmd, user)
        if rc != 0:
            logger.error(f"Permission fix step failed: {cmd} — {err}")
            return False

    # Clear cache as www-data
    logger.info("Clearing Mautic cache as www-data...")
    rc, out, err = _run_docker_cmd_as_user(
        "php /var/www/html/bin/console cache:clear", "www-data"
    )
    if rc != 0:
        logger.error(f"Cache clear failed: {err}")
        return False

    # Fix permissions again after cache:clear recreates dirs
    rc, out, err = _run_docker_cmd("chown -R www-data:www-data /var/www/html/var/")
    if rc != 0:
        logger.error(f"Post cache-clear chown failed: {err}")
        return False

    rc, out, err = _run_docker_cmd("chmod -R 775 /var/www/html/var/")
    if rc != 0:
        logger.error(f"Post cache-clear chmod failed: {err}")
        return False

    logger.info("Mautic permission fix applied successfully")
    return True


async def check_mautic_health(
    base_url: str,
    username: str,
    password: str,
    auto_fix: bool = True,
) -> bool:
    """
    Check Mautic API health before running the pipeline.

    If the API returns 500 and auto_fix=True, attempts to fix
    Docker container permissions and retries.

    Args:
        base_url:  Mautic root URL
        username:  Mautic admin username
        password:  Mautic admin password
        auto_fix:  Whether to auto-fix permissions on 500 errors

    Returns:
        True if API is healthy, False if it cannot be fixed.
    """
    import base64
    url = base_url.rstrip("/") + _HEALTH_CHECK_URL
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}"}

    for attempt in range(_MAX_FIX_ATTEMPTS + 1):
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers, params=_HEALTH_CHECK_PARAMS) as resp:
                    status = resp.status
                    if status == 200:
                        data = await resp.json(content_type=None)
                        total = data.get("total", 0)
                        logger.info(f"Mautic API healthy — {total} contacts")
                        return True
                    elif status == 500:
                        logger.warning(f"Mautic API returned 500 (attempt {attempt + 1}/{_MAX_FIX_ATTEMPTS + 1})")
                        if auto_fix and attempt < _MAX_FIX_ATTEMPTS:
                            fixed = fix_mautic_permissions()
                            if not fixed:
                                logger.error("Auto-fix failed — cannot proceed")
                                return False
                            logger.info("Retrying API check after fix...")
                            await asyncio.sleep(3)
                            continue
                        else:
                            logger.error("Mautic API still returning 500 after fix attempts")
                            return False
                    else:
                        logger.error(f"Mautic API returned unexpected status: {status}")
                        return False

        except aiohttp.ClientError as e:
            logger.error(f"Mautic API connection error: {e}")
            if attempt < _MAX_FIX_ATTEMPTS:
                await asyncio.sleep(5)
                continue
            return False

    return False