#!/usr/bin/env python3
"""
Setup script for PyStickies GitHub Gist integration

This script helps users configure their GitHub token and Gist ID for syncing notes.
"""

import os
import json
import sys


def setup_gist_config():
    """Interactive setup for GitHub Gist configuration"""
    print("=== PyStickies Gist Setup ===")
    print("This will help you sync your notes to GitHub Gist for backup and cross-device sync.")
    print()
    
    # Check if config already exists
    config_file = os.path.join(os.path.dirname(__file__), ".gist_config")
    existing_config = {}
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                existing_config = json.load(f)
            print("Found existing configuration:")
            if 'gist_id' in existing_config:
                print(f"  Gist ID: {existing_config['gist_id']}")
            print()
        except:
            pass
    
    # Get GitHub token
    print("1. GitHub Personal Access Token")
    print("   You need to create a GitHub token with 'gist' scope.")
    print("   Go to: https://github.com/settings/tokens")
    print("   Create a new token with 'gist' permission.")
    
    if 'github_token' in existing_config:
        print(f"   Current token: {'*' * 10}{existing_config['github_token'][-4:]}")
        use_existing = input("   Use existing token? (y/n): ").lower().strip() == 'y'
        if use_existing:
            github_token = existing_config['github_token']
        else:
            github_token = input("   Enter new token: ").strip()
    else:
        github_token = input("   Enter your GitHub token: ").strip()
    
    if not github_token:
        print("❌ Token is required.")
        return False
    
    # Test token and list existing gists
    print("\n2. Gist Configuration")
    
    # If we have an existing gist ID, ask if user wants to use it
    gist_id = None
    if 'gist_id' in existing_config:
        print(f"   Current Gist ID: {existing_config['gist_id']}")
        use_existing = input("   Use existing Gist? (y/n): ").lower().strip() == 'y'
        if use_existing:
            gist_id = existing_config['gist_id']
    
    if not gist_id:
        print("   You can:")
        print("   a) Create a new Gist (recommended)")
        print("   b) Use an existing Gist ID")
        choice = input("   Enter choice (a/b): ").lower().strip()
        
        if choice == 'b':
            gist_id = input("   Enter existing Gist ID: ").strip()
        else:
            gist_id = None  # Will create new gist
    
    # Save configuration
    config = {
        'github_token': github_token,
        'gist_id': gist_id
    }
    
    try:
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)
        print(f"\n✅ Configuration saved to {config_file}")
        
        # Set restrictive permissions for security
        os.chmod(config_file, 0o600)
        
        print("\nNext steps:")
        print("1. Restart PyStickies to apply changes")
        print("2. Your notes will automatically sync to Gist")
        if not gist_id:
            print("3. On first sync, a new private Gist will be created")
            print("   The Gist ID will be saved for future use")
        
        return True
        
    except Exception as e:
        print(f"❌ Failed to save configuration: {e}")
        return False


def show_help():
    """Show help information"""
    print("=== PyStickies Gist Help ===")
    print()
    print("Environment Variables (alternative to config file):")
    print("  PYSTICKIES_GITHUB_TOKEN  - Your GitHub personal access token")
    print("  PYSTICKIES_GIST_ID       - Your Gist ID (optional on first run)")
    print()
    print("GitHub Token Setup:")
    print("1. Go to https://github.com/settings/tokens")
    print("2. Click 'Generate new token'")
    print("3. Give it a name like 'PyStickies'")
    print("4. Select 'gist' scope only")
    print("5. Click 'Generate token'")
    print("6. Copy the token and use it in setup")
    print()
    print("Finding Gist ID:")
    print("1. Go to https://gist.github.com")
    print("2. Find your PyStickies Gist")
    print("3. The Gist ID is in the URL: https://gist.github.com/username/GIST_ID")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ['-h', '--help']:
        show_help()
    else:
        setup_gist_config()