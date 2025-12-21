"""
Simple script to initialize/recreate database schema.
Run this after changing the database name in your .env file.
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# This will automatically create the schema
from database import get_database_manager

if __name__ == "__main__":
    print("Initializing database schema...")
    try:
        db = get_database_manager()
        print("✓ Database schema initialized successfully!")
    except Exception as e:
        print(f"✗ Error: {e}")
        print("\nMake sure:")
        print("1. MySQL server is running")
        print("2. Database exists (or create it manually)")
        print("3. MYSQL_USER, MYSQL_PASSWORD, and MYSQL_DATABASE are set in .env")
        sys.exit(1)

