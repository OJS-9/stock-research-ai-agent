"""
Script to recreate the database schema.
Creates the database if it doesn't exist, then initializes all tables.
"""

import os
import sys
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import Error

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Load .env file if it exists and is readable
try:
    load_dotenv()
except Exception as e:
    print(f"Warning: Could not load .env file: {e}")
    print("Continuing with environment variables...")

def create_database_if_not_exists():
    """Create the database if it doesn't exist."""
    db_name = os.getenv('MYSQL_DATABASE')
    if not db_name:
        raise ValueError("MYSQL_DATABASE environment variable not set")
    
    config = {
        'host': os.getenv('MYSQL_HOST', 'localhost'),
        'port': int(os.getenv('MYSQL_PORT', 3306)),
        'user': os.getenv('MYSQL_USER'),
        'password': os.getenv('MYSQL_PASSWORD'),
    }
    
    if not all([config['user'], config['password']]):
        raise ValueError(
            "MySQL configuration incomplete. Set MYSQL_USER and MYSQL_PASSWORD environment variables."
        )
    
    connection = None
    try:
        # Connect without specifying database
        connection = mysql.connector.connect(**config)
        cursor = connection.cursor()
        
        # Create database if it doesn't exist
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        connection.commit()
        print(f"✓ Database '{db_name}' ready")
        
    except Error as e:
        raise RuntimeError(f"Failed to create database: {e}")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()

def recreate_schema():
    """Recreate the database schema."""
    print("Recreating database schema...")
    
    # First, ensure database exists
    create_database_if_not_exists()
    
    # Now initialize the schema using DatabaseManager
    from database import DatabaseManager
    
    try:
        db_manager = DatabaseManager()
        db_manager.init_schema()
        print("✓ Schema recreation complete!")
    except Exception as e:
        print(f"✗ Error recreating schema: {e}")
        sys.exit(1)

if __name__ == "__main__":
    recreate_schema()

