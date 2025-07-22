#!/usr/bin/env python3
"""
Environment Setup for Enhanced State Management
Helps configure InfluxDB and other environment variables for the trading system.
"""

import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def create_env_file():
    """Create a .env file with default configuration."""
    env_content = """# Enhanced State Management Configuration
# InfluxDB Configuration
INFLUXDB_URL=http://localhost:8086
INFLUXDB_TOKEN=your-token-here
INFLUXDB_ORG=trading-org
INFLUXDB_BUCKET=trading-states

# State Management Configuration
STATE_ENABLE_FILE_FALLBACK=true
STATE_FALLBACK_DIRECTORY=./state_fallback
STATE_ENABLE_API=false
STATE_API_HOST=localhost
STATE_API_PORT=8080
STATE_DATABASE_TYPE=influxdb

# Logging Configuration
LOG_LEVEL=INFO
LOG_FILE=pairs_trading.log
"""
    
    env_path = Path('.env')
    if env_path.exists():
        print(f"⚠️  .env file already exists at {env_path.absolute()}")
        choice = input("Do you want to overwrite it? (y/N): ").strip().lower()
        if choice != 'y':
            print("Keeping existing .env file")
            return False
    
    try:
        with open(env_path, 'w') as f:
            f.write(env_content)
        print(f"✅ Created .env file at {env_path.absolute()}")
        print("\n📝 Please update the following values in your .env file:")
        print("   - INFLUXDB_TOKEN: Your actual InfluxDB token")
        print("   - INFLUXDB_URL: Your InfluxDB server URL if different")
        print("   - INFLUXDB_ORG: Your InfluxDB organization name")
        print("   - INFLUXDB_BUCKET: Your InfluxDB bucket name")
        return True
    except Exception as e:
        print(f"❌ Failed to create .env file: {e}")
        return False

def check_influxdb_config():
    """Check InfluxDB configuration."""
    print("\n🔍 Checking InfluxDB configuration...")
    
    # Check environment variables
    required_vars = [
        'INFLUXDB_URL',
        'INFLUXDB_TOKEN', 
        'INFLUXDB_ORG',
        'INFLUXDB_BUCKET'
    ]
    
    missing_vars = []
    for var in required_vars:
        value = os.getenv(var)
        if not value or value in ['your-token-here', 'dev-token-replace-me', 'your-influxdb-token']:
            missing_vars.append(var)
        else:
            print(f"   ✅ {var}: {value[:10]}..." if 'TOKEN' in var else f"   ✅ {var}: {value}")
    
    if missing_vars:
        print(f"\n❌ Missing or invalid configuration for: {', '.join(missing_vars)}")
        print("   Please set these environment variables or update your .env file")
        return False
    else:
        print("\n✅ All InfluxDB configuration variables are set")
        return True

def test_influxdb_connection():
    """Test InfluxDB connection."""
    print("\n🔌 Testing InfluxDB connection...")
    
    try:
        from influxdb_client import InfluxDBClient
        
        url = os.getenv('INFLUXDB_URL', 'http://localhost:8086')
        token = os.getenv('INFLUXDB_TOKEN')
        org = os.getenv('INFLUXDB_ORG')
        
        if not token or token in ['your-token-here', 'dev-token-replace-me']:
            print("❌ Invalid InfluxDB token")
            return False
        
        client = InfluxDBClient(url=url, token=token, org=org)
        
        # Test connection
        health = client.health()
        if health.status == "pass":
            print("✅ InfluxDB connection successful!")
            print(f"   Server: {url}")
            print(f"   Version: {health.version}")
            return True
        else:
            print(f"❌ InfluxDB health check failed: {health.status}")
            return False
            
    except ImportError:
        print("❌ influxdb-client package not installed")
        print("   Run: pip install influxdb-client")
        return False
    except Exception as e:
        print(f"❌ InfluxDB connection failed: {e}")
        if "401" in str(e) or "Unauthorized" in str(e):
            print("   This is likely an authentication error. Please check your token.")
        elif "connection" in str(e).lower():
            print("   This is likely a connection error. Please check if InfluxDB is running.")
        return False
    finally:
        try:
            client.close()
        except:
            pass

def setup_fallback_directory():
    """Setup fallback directory for file-based state management."""
    fallback_dir = os.getenv('STATE_FALLBACK_DIRECTORY', './state_fallback')
    try:
        os.makedirs(fallback_dir, exist_ok=True)
        print(f"✅ Fallback directory ready: {os.path.abspath(fallback_dir)}")
        return True
    except Exception as e:
        print(f"❌ Failed to create fallback directory: {e}")
        return False

def main():
    """Main setup function."""
    print("🚀 Enhanced State Management Environment Setup\n")
    
    # Create .env file if needed
    create_env_file()
    
    # Load environment variables from .env if it exists
    env_path = Path('.env')
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
            print("✅ Loaded environment variables from .env file")
        except ImportError:
            print("⚠️  python-dotenv not installed. Install with: pip install python-dotenv")
            print("   Manual loading of .env file required")
        except Exception as e:
            print(f"⚠️  Failed to load .env file: {e}")
    
    # Check configuration
    config_ok = check_influxdb_config()
    
    # Setup fallback directory
    fallback_ok = setup_fallback_directory()
    
    # Test connection if config is valid
    connection_ok = False
    if config_ok:
        connection_ok = test_influxdb_connection()
    
    # Summary
    print("\n📊 Setup Summary:")
    print(f"   Configuration: {'✅' if config_ok else '❌'}")
    print(f"   Fallback Directory: {'✅' if fallback_ok else '❌'}")
    print(f"   InfluxDB Connection: {'✅' if connection_ok else '❌'}")
    
    if not connection_ok and fallback_ok:
        print("\n💡 Tip: The system will use file fallback if InfluxDB is unavailable")
        print("   You can still run the trading system, but database features will be limited")
    
    if not config_ok:
        print("\n🔧 Next steps:")
        print("   1. Update your .env file with correct InfluxDB settings")
        print("   2. Make sure InfluxDB is running")
        print("   3. Run this script again to verify")

if __name__ == "__main__":
    main()
