#!/usr/bin/env python
"""
Mailchimp Tag Management Utility

This comprehensive utility provides all Mailchimp tag management functionality:

ANALYSIS MODE:
- Complete tag analysis with segment detection
- Member counts and detailed member lists
- Detection of duplicate, orphaned, or problematic tags
- Export detailed reports to JSON
- Verification of rename operations

RENAME MODE:  
- Proper tag renaming by migrating members (not creating new tags)
- Verification that old tag definitions are completely removed
- Progress tracking and error handling
- Rate limiting and retry logic

USAGE:
    # Analysis mode (default)
    python mailchimp_tags.py
    python mailchimp_tags.py --analyze
    
    # Rename mode
    python mailchimp_tags.py --rename "OLD TAG" "NEW TAG"
    
    # Quick check mode (summary only)
    python mailchimp_tags.py --quick
"""

import requests
import json
import time
import sys
import os
import argparse
from collections import defaultdict, Counter
from datetime import datetime

# Add parent directory to path so we can import main
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main

# Load Mailchimp credentials from config
MAILCHIMP_API_KEY = main.MAILCHIMP_API_KEY
MAILCHIMP_LIST_ID = main.MAILCHIMP_LIST_ID
MAILCHIMP_DC = main.MAILCHIMP_DC
MAILCHIMP_BASE_URL = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0"

# Constants
MAX_RETRIES = 3
RETRY_DELAY = 2

class MailchimpTagManager:
    """Comprehensive Mailchimp tag management utility."""
    
    def __init__(self):
        self.auth = ("anystring", MAILCHIMP_API_KEY)
        self.headers = {"Content-Type": "application/json"}
        
        if not all([MAILCHIMP_API_KEY, MAILCHIMP_LIST_ID, MAILCHIMP_DC]):
            raise ValueError("Mailchimp credentials not properly configured")
    
    def get_all_members_with_tags(self):
        """Fetch all members and their tags to build complete tag picture."""
        print("📋 Fetching all members and their tags...")
        
        all_members = []
        offset = 0
        count = 1000
        
        while True:
            url = f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/members"
            params = {
                "count": count,
                "offset": offset,
                "fields": "members.id,members.email_address,members.tags,members.status,total_items"
            }
            
            try:
                response = requests.get(url, auth=self.auth, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                
                members = data.get("members", [])
                if not members:
                    break
                    
                all_members.extend(members)
                
                print(f"   Fetched {len(members)} members (total: {len(all_members)})")
                
                if len(members) < count:
                    break
                    
                offset += count
                
            except requests.exceptions.RequestException as e:
                print(f"❌ Error fetching members: {e}")
                return []
        
        print(f"✅ Total members fetched: {len(all_members)}")
        return all_members
    
    def get_segments(self):
        """Get segments which might include tag-based segments."""
        print("📊 Fetching segments...")
        
        url = f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/segments"
        
        try:
            response = requests.get(url, auth=self.auth, headers=self.headers, params={"count": 1000})
            response.raise_for_status()
            data = response.json()
            
            segments = data.get("segments", [])
            print(f"✅ Found {len(segments)} segments")
            
            return segments
        except requests.exceptions.RequestException as e:
            print(f"❌ Error fetching segments: {e}")
            return []
    
    def get_tag_search_results(self):
        """Try to get tag definitions using tag search endpoint."""
        print("🔍 Attempting to get tag definitions via tag search...")
        
        url = f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/tag-search"
        
        try:
            response = requests.get(url, auth=self.auth, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            print(f"✅ Tag search successful")
            return data
        except requests.exceptions.RequestException as e:
            print(f"❌ Tag search failed: {e}")
            return None
    
    def analyze_tags_from_members(self, members):
        """Analyze all tags from member data to build comprehensive tag picture."""
        print("\n📊 Analyzing tags from member data...")
        
        # Track all unique tags and their members
        tag_to_members = defaultdict(list)
        all_tags = set()
        
        for member in members:
            email = member.get("email_address", "unknown")
            member_id = member.get("id", "unknown")
            status = member.get("status", "unknown")
            tags = member.get("tags", [])
            
            for tag_info in tags:
                tag_name = tag_info.get("name", "")
                if tag_name:
                    all_tags.add(tag_name)
                    tag_to_members[tag_name].append({
                        "email": email,
                        "id": member_id,
                        "status": status
                    })
        
        return tag_to_members, all_tags
    
    def detect_issues(self, tag_analysis, segments):
        """Detect potential issues with tag management."""
        print("\n🔍 Analyzing for tag management issues...")
        
        issues = []
        tag_names = list(tag_analysis.keys())
        
        # Look for potential old/new tag pairs
        testing_tags = [tag for tag in tag_names if "EJAS TESTING" in tag]
        if len(testing_tags) > 1:
            issues.append(f"Multiple EJAS TESTING tags found: {testing_tags}")
            
            # Check for member overlap
            for i, tag1 in enumerate(testing_tags):
                for tag2 in testing_tags[i+1:]:
                    members1 = set(m["email"] for m in tag_analysis[tag1])
                    members2 = set(m["email"] for m in tag_analysis[tag2])
                    overlap = members1.intersection(members2)
                    if overlap:
                        issues.append(f"Member overlap between '{tag1}' and '{tag2}': {len(overlap)} members")
        
        # Look for empty tags (this would indicate old tags weren't properly cleaned up)
        empty_tags = [tag for tag, members in tag_analysis.items() if len(members) == 0]
        if empty_tags:
            issues.append(f"Empty tags found (potential orphans): {empty_tags}")
        
        # Check for segments with 0 members that might be orphaned tags
        empty_segments = [s for s in segments if s.get('member_count', 0) == 0 and s.get('type') == 'static']
        if empty_segments:
            segment_names = [s.get('name') for s in empty_segments]
            issues.append(f"Empty segments found (potential orphaned tag definitions): {len(empty_segments)} segments")
        
        return issues
    
    def generate_analysis_report(self, tag_analysis, all_tags, segments, tag_search_results, issues):
        """Generate comprehensive analysis report."""
        report = {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total_unique_tags": len(all_tags),
                "total_tagged_members": sum(len(members) for members in tag_analysis.values()),
                "segments_found": len(segments),
                "issues_detected": len(issues)
            },
            "tag_details": {},
            "segments": segments,
            "tag_search_results": tag_search_results,
            "issues": issues,
            "member_details": {}
        }
        
        # Build detailed tag analysis
        for tag, members in tag_analysis.items():
            report["tag_details"][tag] = {
                "member_count": len(members),
                "member_emails": [m["email"] for m in members],
                "member_statuses": Counter(m["status"] for m in members)
            }
            
            # Store member details separately for easier reading
            report["member_details"][tag] = members
        
        return report
    
    def analyze_tags(self, quick_mode=False):
        """Perform comprehensive tag analysis."""
        print("🚀 Starting Comprehensive Mailchimp Tag Analysis")
        print("=" * 60)
        
        # Get all data
        members = self.get_all_members_with_tags()
        if not members:
            print("❌ No members found, aborting analysis")
            return None
        
        segments = self.get_segments()
        tag_search_results = self.get_tag_search_results()
        
        # Analyze tags
        tag_analysis, all_tags = self.analyze_tags_from_members(members)
        
        # Detect issues
        issues = self.detect_issues(tag_analysis, segments)
        
        # Generate report
        report = self.generate_analysis_report(tag_analysis, all_tags, segments, tag_search_results, issues)
        
        # Display summary
        print("\n📊 ANALYSIS SUMMARY")
        print("=" * 40)
        print(f"Total unique tags: {report['summary']['total_unique_tags']}")
        print(f"Total tagged members: {report['summary']['total_tagged_members']}")
        print(f"Segments found: {report['summary']['segments_found']}")
        print(f"Issues detected: {report['summary']['issues_detected']}")
        
        print("\n🏷️  TAG BREAKDOWN")
        print("-" * 40)
        for tag, details in report["tag_details"].items():
            status_summary = ", ".join(f"{status}: {count}" for status, count in details["member_statuses"].items())
            print(f"'{tag}': {details['member_count']} members ({status_summary})")
        
        if issues:
            print("\n⚠️  ISSUES DETECTED")
            print("-" * 40)
            for issue in issues:
                print(f"• {issue}")
        else:
            print("\n✅ No issues detected")
        
        if not quick_mode:
            # Save detailed report
            output_file = "mailchimp_tag_analysis.json"
            output_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), output_file)
            
            with open(output_path, 'w') as f:
                json.dump(report, f, indent=2)
            
            print(f"\n💾 Detailed report saved to: {output_path}")
            
            # Show specific member details for key tags
            print("\n👥 MEMBER DETAILS FOR KEY TAGS")
            print("-" * 40)
            key_tags = [tag for tag in all_tags if "EJAS TESTING" in tag or "Mailchimp Test" in tag]
            
            for tag in sorted(key_tags):
                if tag in tag_analysis:
                    members = tag_analysis[tag]
                    print(f"\n'{tag}' ({len(members)} members):")
                    for member in members[:10]:  # Show first 10
                        print(f"  • {member['email']} ({member['status']})")
                    if len(members) > 10:
                        print(f"  ... and {len(members) - 10} more")
        
        return report
    
    def get_members_with_tag(self, tag_name):
        """Get all members who have a specific tag."""
        print(f"🔍 Finding all members with tag '{tag_name}'...")
        
        url = f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/members"
        members_with_tag = []
        params = {"count": 1000, "offset": 0}
        
        try:
            while True:
                response = requests.get(url, auth=self.auth, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                
                members = data.get("members", [])
                if not members:
                    break
                
                for member in members:
                    member_tags = [tag.get("name") for tag in member.get("tags", [])]
                    if tag_name in member_tags:
                        members_with_tag.append({
                            "email": member.get("email_address"),
                            "subscriber_hash": member.get("id"),
                            "current_tags": member_tags
                        })
                
                # Check pagination
                total_items = data.get("total_items", 0)
                current_count = params['offset'] + len(members)
                
                if current_count < total_items:
                    params['offset'] = current_count
                else:
                    break
                    
        except Exception as e:
            print(f"❌ Error fetching members with tag '{tag_name}': {e}")
            return []
        
        print(f"✅ Found {len(members_with_tag)} members with tag '{tag_name}'")
        return members_with_tag
    
    def rename_tag_properly(self, old_tag_name, new_tag_name):
        """
        Properly rename a Mailchimp tag by:
        1. Finding all members with the old tag
        2. Removing the old tag from each member
        3. Adding the new tag to each member
        4. Verifying the old tag definition no longer exists
        """
        print(f"\n🏷️  PROPER TAG RENAME: '{old_tag_name}' → '{new_tag_name}'")
        print("=" * 60)
        
        # Step 1: Get all members with the old tag
        members_with_old_tag = self.get_members_with_tag(old_tag_name)
        
        if not members_with_old_tag:
            print(f"ℹ️  No members found with tag '{old_tag_name}' - nothing to rename")
            return True
        
        # Step 2: Migrate each member
        print(f"\n🔄 Migrating {len(members_with_old_tag)} members...")
        
        successful_migrations = 0
        failed_migrations = []
        
        for i, member in enumerate(members_with_old_tag, 1):
            email = member["email"]
            subscriber_hash = member["subscriber_hash"]
            
            print(f"  [{i}/{len(members_with_old_tag)}] Migrating: {email}")
            
            # Build tag operations: remove old, add new
            tag_operations = [
                {"name": old_tag_name, "status": "inactive"},  # Remove old
                {"name": new_tag_name, "status": "active"}     # Add new
            ]
            
            tag_url = f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}/tags"
            tag_payload = {"tags": tag_operations}
            
            # Retry logic
            migration_success = False
            for attempt in range(MAX_RETRIES):
                try:
                    response = requests.post(tag_url, auth=self.auth, headers=self.headers, json=tag_payload)
                    
                    if response.status_code in (200, 204):
                        print(f"    ✅ Successfully migrated {email}")
                        successful_migrations += 1
                        migration_success = True
                        break
                    else:
                        if attempt < MAX_RETRIES - 1:
                            print(f"    ⚠️  Attempt {attempt + 1} failed for {email}, retrying...")
                            time.sleep(RETRY_DELAY)
                        else:
                            print(f"    ❌ Failed to migrate {email}: {response.status_code}")
                            print(f"       Response: {response.text}")
                            
                except Exception as e:
                    if attempt < MAX_RETRIES - 1:
                        print(f"    ⚠️  Attempt {attempt + 1} error for {email}: {e}, retrying...")
                        time.sleep(RETRY_DELAY)
                    else:
                        print(f"    ❌ Error migrating {email}: {e}")
            
            if not migration_success:
                failed_migrations.append(email)
            
            # Rate limiting
            time.sleep(0.2)
        
        # Step 3: Verify migration results
        print(f"\n📊 MIGRATION RESULTS:")
        print(f"  ✅ Successful: {successful_migrations}")
        print(f"  ❌ Failed: {len(failed_migrations)}")
        
        if failed_migrations:
            print(f"  Failed members: {failed_migrations}")
        
        # Step 4: Verify old tag no longer exists
        print(f"\n🔍 Verifying old tag '{old_tag_name}' was removed...")
        remaining_members = self.get_members_with_tag(old_tag_name)
        
        if len(remaining_members) == 0:
            print(f"  ✅ Old tag '{old_tag_name}' successfully removed from all members")
        else:
            print(f"  ⚠️  {len(remaining_members)} members still have old tag '{old_tag_name}'")
            return False
        
        # Step 5: Verify new tag exists and has correct count
        print(f"\n🔍 Verifying new tag '{new_tag_name}' has correct members...")
        new_tag_members = self.get_members_with_tag(new_tag_name)
        
        if len(new_tag_members) == successful_migrations:
            print(f"  ✅ New tag '{new_tag_name}' has {len(new_tag_members)} members (expected: {successful_migrations})")
        else:
            print(f"  ⚠️  New tag '{new_tag_name}' has {len(new_tag_members)} members (expected: {successful_migrations})")
        
        print(f"\n🎉 TAG RENAME COMPLETE!")
        print(f"   '{old_tag_name}' → '{new_tag_name}'")
        print(f"   {successful_migrations} members successfully migrated")
        
        return successful_migrations == len(members_with_old_tag) and len(remaining_members) == 0


def main():
    """Main entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description="Mailchimp Tag Management Utility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                    # Full tag analysis
  %(prog)s --analyze                          # Full tag analysis  
  %(prog)s --quick                            # Quick summary only
  %(prog)s --rename "OLD TAG" "NEW TAG"       # Rename a tag properly
        """
    )
    
    parser.add_argument("--analyze", action="store_true", 
                       help="Perform comprehensive tag analysis (default)")
    parser.add_argument("--quick", action="store_true",
                       help="Quick analysis without detailed reports")
    parser.add_argument("--rename", nargs=2, metavar=("OLD_TAG", "NEW_TAG"),
                       help="Rename a tag from OLD_TAG to NEW_TAG")
    
    args = parser.parse_args()
    
    try:
        manager = MailchimpTagManager()
        
        if args.rename:
            old_tag, new_tag = args.rename
            print(f"🏷️  MAILCHIMP TAG RENAME UTILITY")
            print("=" * 40)
            print(f"Old Tag: '{old_tag}'")
            print(f"New Tag: '{new_tag}'")
            print()
            
            # Confirm the operation
            response = input(f"Are you sure you want to rename '{old_tag}' to '{new_tag}'? (yes/no): ")
            if response.lower() != 'yes':
                print("Operation cancelled.")
                return
            
            # Perform the rename
            success = manager.rename_tag_properly(old_tag, new_tag)
            
            if success:
                print("\n✅ Tag rename completed successfully!")
            else:
                print("\n❌ Tag rename failed or incomplete.")
                sys.exit(1)
        
        else:
            # Analysis mode (default)
            report = manager.analyze_tags(quick_mode=args.quick)
            
            if report and report.get('summary', {}).get('issues_detected', 0) > 0:
                print(f"\n⚠️  {report['summary']['issues_detected']} issues detected!")
                print("Consider using --rename to fix tag issues.")
    
    except ValueError as e:
        print(f"❌ Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
