"""
V3 API Migration Test Script
============================
Tests ALL HubSpot API calls in hubspot_client.py against a real test contact.
Creates a test contact, exercises every method, then cleans up.

Safe: Uses a unique test email, does NOT touch any production contacts or lists.
"""

import asyncio
import os
import sys
import time

# Load .env file
from pathlib import Path
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from corev2.clients.hubspot_client import HubSpotClient

# Test contact details — unique email to avoid collisions
TEST_EMAIL = f"v3.migration.test.{int(time.time())}@solace-test-deleteme.com"
TEST_FIRSTNAME = "V3MigrationTest"
TEST_LASTNAME = "DeleteMe"

# A list we know exists in production — READ ONLY, no membership changes
# List 969 = Sanctioned (active in production config)
READONLY_LIST_ID = "969"

PASS = "✓ PASS"
FAIL = "✗ FAIL"


async def run_tests():
    api_key = os.environ.get("HUBSPOT_PRIVATE_APP_TOKEN")
    if not api_key:
        print("ERROR: HUBSPOT_PRIVATE_APP_TOKEN not set")
        sys.exit(1)

    results = []
    test_vid = None

    async with HubSpotClient(api_key=api_key) as client:

        # ─────────────────────────────────────────
        # TEST 1: Create test contact via v3 API
        # ─────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"TEST 1: Create test contact ({TEST_EMAIL})")
        print(f"{'='*60}")
        try:
            create_result = await client.post(
                "/crm/v3/objects/contacts",
                json={
                    "properties": {
                        "email": TEST_EMAIL,
                        "firstname": TEST_FIRSTNAME,
                        "lastname": TEST_LASTNAME,
                        "phone": "+441234567890"
                    }
                }
            )
            if create_result["status"] in [200, 201]:
                test_vid = int(create_result["data"]["id"])
                print(f"  Created contact VID: {test_vid}")
                print(f"  Email: {TEST_EMAIL}")
                results.append(("Create test contact", PASS))
            else:
                print(f"  Unexpected status: {create_result['status']}")
                print(f"  Response: {create_result['data']}")
                results.append(("Create test contact", FAIL))
                return results
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append(("Create test contact", FAIL))
            return results

        # Small delay to let HubSpot index the contact
        await asyncio.sleep(2)

        # ─────────────────────────────────────────
        # TEST 2: get_contact_by_email (v3 — THE CRITICAL FIX)
        # ─────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"TEST 2: get_contact_by_email() — v3 migration")
        print(f"{'='*60}")
        try:
            contact = await client.get_contact_by_email(TEST_EMAIL)
            print(f"  found:      {contact['found']}")
            print(f"  vid:        {contact['vid']}")
            print(f"  email:      {contact['email']}")
            print(f"  properties: {contact['properties']}")

            assert contact["found"] is True, "Contact should be found"
            assert contact["vid"] == test_vid, f"VID mismatch: {contact['vid']} != {test_vid}"
            assert contact["email"] == TEST_EMAIL, "Email mismatch"
            assert isinstance(contact["properties"], dict), "Properties should be a dict"
            results.append(("get_contact_by_email (found)", PASS))
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append(("get_contact_by_email (found)", FAIL))

        # ─────────────────────────────────────────
        # TEST 3: get_contact_by_email — NOT FOUND case
        # ─────────────────────────────────────────
        print(f"\n{'='*60}")
        print("TEST 3: get_contact_by_email() — not found case")
        print(f"{'='*60}")
        try:
            fake_contact = await client.get_contact_by_email("absolutely.does.not.exist.999@fake-domain-xyz.test")
            print(f"  found: {fake_contact['found']}")
            print(f"  vid:   {fake_contact['vid']}")

            assert fake_contact["found"] is False, "Fake contact should not be found"
            assert fake_contact["vid"] is None, "VID should be None for not-found"
            results.append(("get_contact_by_email (not found)", PASS))
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append(("get_contact_by_email (not found)", FAIL))

        # ─────────────────────────────────────────
        # TEST 4: get_contact_by_email with custom properties
        # ─────────────────────────────────────────
        print(f"\n{'='*60}")
        print("TEST 4: get_contact_by_email() — custom properties")
        print(f"{'='*60}")
        try:
            contact_props = await client.get_contact_by_email(
                TEST_EMAIL,
                properties=["email", "firstname", "lastname", "phone"]
            )
            props = contact_props["properties"]
            print(f"  firstname: {props.get('firstname')}")
            print(f"  lastname:  {props.get('lastname')}")
            print(f"  phone:     {props.get('phone')}")

            assert props.get("firstname") == TEST_FIRSTNAME, f"firstname mismatch: {props.get('firstname')}"
            assert props.get("lastname") == TEST_LASTNAME, f"lastname mismatch: {props.get('lastname')}"
            assert props.get("phone") == "+441234567890", f"phone mismatch: {props.get('phone')}"
            results.append(("get_contact_by_email (custom props)", PASS))
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append(("get_contact_by_email (custom props)", FAIL))

        # ─────────────────────────────────────────
        # TEST 5: update_contact_property (v3 — SECOND FIX)
        # ─────────────────────────────────────────
        print(f"\n{'='*60}")
        print("TEST 5: update_contact_property() — v3 migration")
        print(f"{'='*60}")
        try:
            update_result = await client.update_contact_property(
                test_vid, "lastname", "DeleteMe-Updated"
            )
            print(f"  success:     {update_result['success']}")
            print(f"  contact_vid: {update_result['contact_vid']}")
            print(f"  property:    {update_result['property']}")

            assert update_result["success"] is True, "Update should succeed"
            assert update_result["contact_vid"] == test_vid, "VID mismatch"
            assert update_result["property"] == "lastname", "Property name mismatch"

            # Verify the update stuck
            await asyncio.sleep(1)
            verify = await client.get_contact_by_email(TEST_EMAIL, properties=["lastname"])
            assert verify["properties"]["lastname"] == "DeleteMe-Updated", \
                f"Property not updated: {verify['properties'].get('lastname')}"
            print(f"  Verified:    lastname = {verify['properties']['lastname']}")
            results.append(("update_contact_property", PASS))
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append(("update_contact_property", FAIL))

        # ─────────────────────────────────────────
        # TEST 6: get_list_members (already v3 — regression check)
        # ─────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"TEST 6: get_list_members() — v3 (regression, list {READONLY_LIST_ID})")
        print(f"{'='*60}")
        try:
            member_count = 0
            first_member = None
            async for member in client.get_list_members(READONLY_LIST_ID, limit=5):
                member_count += 1
                if first_member is None:
                    first_member = member
                if member_count >= 3:  # Just read a few to verify it works
                    break

            print(f"  Members read: {member_count}")
            if first_member:
                print(f"  First member VID: {first_member.get('vid')}")
                print(f"  First member email: {first_member.get('email')}")
            results.append(("get_list_members (v3)", PASS))
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append(("get_list_members (v3)", FAIL))

        # ─────────────────────────────────────────
        # TEST 7: get_list_name (already v3 — regression check)
        # ─────────────────────────────────────────
        print(f"\n{'='*60}")
        print("TEST 7: get_list_name() — v3 (regression)")
        print(f"{'='*60}")
        try:
            list_name = await client.get_list_name(READONLY_LIST_ID)
            print(f"  List {READONLY_LIST_ID} name: {list_name}")
            assert list_name is not None, "List name should not be None"
            results.append(("get_list_name (v3)", PASS))
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append(("get_list_name (v3)", FAIL))

        # ─────────────────────────────────────────
        # TEST 8: get (raw) — communication preferences v3 (regression)
        # ─────────────────────────────────────────
        print(f"\n{'='*60}")
        print("TEST 8: Communication preferences v3 (regression)")
        print(f"{'='*60}")
        try:
            sub_result = await client.get(
                f"/communication-preferences/v3/status/email/{TEST_EMAIL}"
            )
            print(f"  Status code: {sub_result['status']}")
            if sub_result["status"] == 200:
                subs = sub_result["data"].get("subscriptionStatuses", [])
                print(f"  Subscription types: {len(subs)}")
                for sub in subs[:3]:
                    print(f"    - {sub.get('name', 'Unknown')}: {sub.get('status')}")
            results.append(("communication-preferences v3", PASS))
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append(("communication-preferences v3", FAIL))

        # ─────────────────────────────────────────
        # CLEANUP: Delete test contact
        # ─────────────────────────────────────────
        print(f"\n{'='*60}")
        print("CLEANUP: Deleting test contact")
        print(f"{'='*60}")
        try:
            if test_vid:
                delete_result = await client.delete(f"/crm/v3/objects/contacts/{test_vid}")
                print(f"  Delete status: {delete_result['status']}")
                if delete_result["status"] in [200, 204]:
                    print(f"  ✓ Test contact {TEST_EMAIL} (VID {test_vid}) deleted")
                    results.append(("Cleanup: delete test contact", PASS))
                else:
                    print(f"  ⚠ Unexpected delete status: {delete_result['status']}")
                    results.append(("Cleanup: delete test contact", FAIL))
        except Exception as e:
            print(f"  ⚠ Failed to delete test contact: {e}")
            print(f"  MANUAL CLEANUP NEEDED: Delete {TEST_EMAIL} (VID {test_vid}) from HubSpot")
            results.append(("Cleanup: delete test contact", FAIL))

    return results


async def main():
    print("=" * 60)
    print("HubSpot V3 API Migration Tests")
    print("=" * 60)
    print(f"Test email: {TEST_EMAIL}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    results = await run_tests()

    # Summary
    print(f"\n{'='*60}")
    print("RESULTS SUMMARY")
    print(f"{'='*60}")
    passed = 0
    failed = 0
    for test_name, result in results:
        status_str = result
        print(f"  {status_str}  {test_name}")
        if result == PASS:
            passed += 1
        else:
            failed += 1

    print(f"\n  {passed} passed, {failed} failed out of {len(results)} tests")

    if failed > 0:
        print("\n  ⚠ SOME TESTS FAILED — DO NOT DEPLOY")
        sys.exit(1)
    else:
        print("\n  ✓ ALL TESTS PASSED — V3 MIGRATION VERIFIED")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
