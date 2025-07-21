#!/usr/bin/env python3
"""
Integration Script for Enhanced State Management System

This script helps integrate the enhanced state management system
into your existing trading system with minimal changes.
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime
from typing import Dict, Any, Optional

# Add the project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.unified_state_manager import (
    UnifiedStateManager, get_state_manager, migrate_legacy_system,
    create_backward_compatible_manager
)
from utils.state_config import StateManagementConfig, create_default_config

logger = logging.getLogger(__name__)


def setup_logging(level: str = 'INFO'):
    """Setup basic logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('integration.log', encoding='utf-8')
        ]
    )


def check_requirements() -> bool:
    """Check if all required dependencies are available."""
    required_packages = [
        ('influxdb_client', 'influxdb-client'),
        ('asyncpg', 'asyncpg'),
        ('pydantic', 'pydantic')
    ]
    
    optional_packages = [
        ('fastapi', 'fastapi'),
        ('uvicorn', 'uvicorn'),
        ('websockets', 'websockets')
    ]
    
    missing_required = []
    missing_optional = []
    
    for package, pip_name in required_packages:
        try:
            __import__(package)
            print(f"✅ {package} available")
        except ImportError:
            missing_required.append(pip_name)
            print(f"❌ {package} missing")
    
    for package, pip_name in optional_packages:
        try:
            __import__(package)
            print(f"✅ {package} available (optional)")
        except ImportError:
            missing_optional.append(pip_name)
            print(f"⚠️  {package} missing (optional)")
    
    if missing_required:
        print(f"\n❌ Missing required packages. Install with:")
        print(f"pip install {' '.join(missing_required)}")
        return False
    
    if missing_optional:
        print(f"\n⚠️  Missing optional packages for full functionality:")
        print(f"pip install {' '.join(missing_optional)}")
    
    return True


def detect_existing_state_files(directory: str = '.') -> list:
    """Detect existing state files that can be migrated."""
    state_files = []
    
    # Common state file patterns
    patterns = [
        'state.json',
        'trading_state.json',
        '*_state.json',
        'pairs_trading.json'
    ]
    
    import glob
    for pattern in patterns:
        files = glob.glob(os.path.join(directory, pattern))
        state_files.extend(files)
    
    # Also check subdirectories
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('_state.json') or file.endswith('trading_state.json'):
                state_files.append(os.path.join(root, file))
    
    return list(set(state_files))  # Remove duplicates


def create_example_config(config_type: str = 'development') -> str:
    """Create an example configuration file."""
    config_file = f'state_config_{config_type}.json'
    
    if config_type == 'development':
        config = StateManagementConfig(
            database=DatabaseConfig(
                database_type='influxdb',
                influxdb_url='http://localhost:8086',
                influxdb_token='dev-token-replace-me',
                influxdb_org='trading-dev',
                influxdb_bucket='trading-states-dev'
            ),
            api=APIConfig(
                enabled=True,
                host='localhost',
                port=8000
            ),
            enable_validation=True,
            enable_file_fallback=True,
            log_level='DEBUG'
        )
    else:  # production
        config = StateManagementConfig(
            database=DatabaseConfig(
                database_type='postgresql',
                postgresql_host='localhost',
                postgresql_database='trading_states_prod',
                postgresql_username='trading_user',
                postgresql_password='${POSTGRES_PASSWORD}'
            ),
            api=APIConfig(
                enabled=True,
                host='0.0.0.0',
                port=8000,
                api_key='${API_KEY}'
            ),
            enable_validation=True,
            enable_file_fallback=False,
            log_level='INFO'
        )
    
    config.to_file(config_file)
    return config_file


def test_database_connection(config: StateManagementConfig) -> bool:
    """Test database connection with given configuration."""
    try:
        print(f"Testing {config.database.database_type} connection...")
        
        manager = UnifiedStateManager()
        manager.config = config
        
        success = manager.initialize()
        
        if success:
            print("✅ Database connection successful")
            
            # Test basic operations
            test_positions = {"TEST": {"direction": "long", "quantity": 1000}}
            test_pairs = {"TEST": {"position": "none", "cooldown": 0}}
            
            save_success = manager.save_trading_state(test_positions, test_pairs)
            if save_success:
                print("✅ State save test successful")
                
                loaded_state = manager.load_trading_state()
                if loaded_state:
                    print("✅ State load test successful")
                else:
                    print("⚠️  State load test failed")
            else:
                print("⚠️  State save test failed")
            
            manager.shutdown()
            return True
        else:
            print("❌ Database connection failed")
            return False
            
    except Exception as e:
        print(f"❌ Database test failed: {e}")
        return False


def migrate_existing_states(state_files: list, config: StateManagementConfig) -> bool:
    """Migrate existing state files to the new system."""
    if not state_files:
        print("No state files found to migrate")
        return True
    
    print(f"Found {len(state_files)} state files to migrate:")
    for file in state_files:
        print(f"  - {file}")
    
    response = input("Proceed with migration? (y/N): ")
    if response.lower() != 'y':
        print("Migration cancelled")
        return False
    
    try:
        manager = UnifiedStateManager()
        manager.config = config
        manager.initialize()
        
        total_success = True
        
        for state_file in state_files:
            print(f"\nMigrating {state_file}...")
            
            success = manager.migrate_from_file(state_file, backup_original=True)
            
            if success:
                print(f"✅ Successfully migrated {state_file}")
            else:
                print(f"❌ Failed to migrate {state_file}")
                total_success = False
        
        if total_success:
            print("\n✅ All migrations completed successfully")
        else:
            print("\n⚠️  Some migrations failed")
        
        manager.shutdown()
        return total_success
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        return False


def create_integration_patch(target_file: str) -> str:
    """Create a patch file showing how to integrate with existing code."""
    patch_content = f"""
# Integration Patch for {target_file}
# Generated on {datetime.now().isoformat()}

# Replace existing state manager imports
# OLD:
# from utils.state_manager import TradingStateManager

# NEW:
from utils.unified_state_manager import get_state_manager

# Replace state manager initialization
# OLD:
# self.state_manager = TradingStateManager("state.json")

# NEW:
self.state_manager = get_state_manager(
    auto_migrate=True,
    legacy_state_path="state.json"  # Your existing state file
)

# Initialize the manager (add this line)
self.state_manager.initialize()

# All existing method calls remain the same:
# - save_trading_state()
# - load_trading_state()
# - get_portfolio_summary()

# Optional: Add enhanced features
# Create snapshots
# self.state_manager.create_snapshot("End of day snapshot")

# Get system status
# status = self.state_manager.get_system_status()

# Get state history for analysis
# history = self.state_manager.get_state_history(limit=100)

# Add to shutdown/cleanup method:
# self.state_manager.shutdown()
"""
    
    patch_file = f"{target_file}.integration_patch"
    with open(patch_file, 'w', encoding='utf-8') as f:
        f.write(patch_content)
    
    return patch_file


def interactive_setup():
    """Interactive setup wizard."""
    print("🚀 Enhanced State Management System - Interactive Setup")
    print("=" * 60)
    
    # Step 1: Check requirements
    print("\n1. Checking requirements...")
    if not check_requirements():
        print("Please install missing packages before continuing.")
        return False
    
    # Step 2: Database selection
    print("\n2. Database Configuration")
    db_type = input("Choose database type (influxdb/postgresql) [influxdb]: ").strip().lower()
    if not db_type:
        db_type = 'influxdb'
    
    if db_type not in ['influxdb', 'postgresql']:
        print("Invalid database type")
        return False
    
    # Step 3: Configuration
    print(f"\n3. Configuring {db_type.upper()}...")
    
    if db_type == 'influxdb':
        url = input("InfluxDB URL [http://localhost:8086]: ").strip()
        if not url:
            url = 'http://localhost:8086'
        
        token = input("InfluxDB Token: ").strip()
        org = input("InfluxDB Organization [trading-org]: ").strip()
        if not org:
            org = 'trading-org'
        
        bucket = input("InfluxDB Bucket [trading-states]: ").strip()
        if not bucket:
            bucket = 'trading-states'
        
        config = StateManagementConfig(
            database=DatabaseConfig(
                database_type='influxdb',
                influxdb_url=url,
                influxdb_token=token,
                influxdb_org=org,
                influxdb_bucket=bucket
            )
        )
    
    else:  # postgresql
        host = input("PostgreSQL Host [localhost]: ").strip()
        if not host:
            host = 'localhost'
        
        port = input("PostgreSQL Port [5432]: ").strip()
        if not port:
            port = '5432'
        
        database = input("Database Name [trading_states]: ").strip()
        if not database:
            database = 'trading_states'
        
        username = input("Username [postgres]: ").strip()
        if not username:
            username = 'postgres'
        
        password = input("Password: ").strip()
        
        config = StateManagementConfig(
            database=DatabaseConfig(
                database_type='postgresql',
                postgresql_host=host,
                postgresql_port=int(port),
                postgresql_database=database,
                postgresql_username=username,
                postgresql_password=password
            )
        )
    
    # Step 4: Test connection
    print("\n4. Testing database connection...")
    if not test_database_connection(config):
        print("Database connection failed. Please check your configuration.")
        return False
    
    # Step 5: Save configuration
    config_file = 'state_management_config.json'
    config.to_file(config_file)
    print(f"\n✅ Configuration saved to {config_file}")
    
    # Step 6: Detect existing state files
    print("\n5. Detecting existing state files...")
    state_files = detect_existing_state_files()
    
    if state_files:
        print(f"Found {len(state_files)} state files")
        migrate = input("Migrate existing state files? (y/N): ").strip().lower()
        
        if migrate == 'y':
            migrate_existing_states(state_files, config)
    
    # Step 7: Generate integration code
    print("\n6. Generating integration code...")
    main_files = ['main.py', 'scripts/pair_trading/main.py']
    
    for main_file in main_files:
        if os.path.exists(main_file):
            patch_file = create_integration_patch(main_file)
            print(f"✅ Integration patch created: {patch_file}")
    
    # Step 8: Final instructions
    print("\n🎉 Setup Complete!")
    print("\nNext steps:")
    print("1. Review the generated configuration file")
    print("2. Apply the integration patches to your code")
    print("3. Test the system with your trading application")
    print("4. Consider enabling the REST API for external access")
    
    # Optional: Start API server
    api_start = input("\nStart REST API server now? (y/N): ").strip().lower()
    if api_start == 'y':
        config.api.enabled = True
        manager = UnifiedStateManager()
        manager.config = config
        manager.initialize()
        
        print("Starting API server...")
        try:
            manager.start_api_server_sync()
            print(f"✅ API server running on http://{config.api.host}:{config.api.port}")
            print("Press Ctrl+C to stop")
            
            import time
            while True:
                time.sleep(1)
                
        except KeyboardInterrupt:
            print("\nStopping API server...")
            manager.shutdown()
    
    return True


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Enhanced State Management System Integration Tool'
    )
    
    parser.add_argument('--check-requirements', action='store_true',
                       help='Check system requirements')
    parser.add_argument('--create-config', choices=['dev', 'prod'],
                       help='Create example configuration file')
    parser.add_argument('--test-connection', type=str,
                       help='Test database connection with config file')
    parser.add_argument('--migrate', type=str,
                       help='Migrate state file or directory')
    parser.add_argument('--interactive', action='store_true',
                       help='Run interactive setup wizard')
    parser.add_argument('--detect-states', action='store_true',
                       help='Detect existing state files')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose logging')
    
    args = parser.parse_args()
    
    # Setup logging
    log_level = 'DEBUG' if args.verbose else 'INFO'
    setup_logging(log_level)
    
    if args.check_requirements:
        return 0 if check_requirements() else 1
    
    elif args.create_config:
        config_file = create_example_config(args.create_config)
        print(f"✅ Example configuration created: {config_file}")
        return 0
    
    elif args.test_connection:
        try:
            config = StateManagementConfig.from_file(args.test_connection)
            return 0 if test_database_connection(config) else 1
        except Exception as e:
            print(f"❌ Failed to load config: {e}")
            return 1
    
    elif args.migrate:
        try:
            success = migrate_legacy_system(args.migrate)
            return 0 if success else 1
        except Exception as e:
            print(f"❌ Migration failed: {e}")
            return 1
    
    elif args.detect_states:
        state_files = detect_existing_state_files()
        if state_files:
            print(f"Found {len(state_files)} state files:")
            for file in state_files:
                print(f"  - {file}")
        else:
            print("No state files found")
        return 0
    
    elif args.interactive:
        return 0 if interactive_setup() else 1
    
    else:
        # Default: show help and run interactive setup
        parser.print_help()
        print("\nRunning interactive setup...")
        return 0 if interactive_setup() else 1


if __name__ == "__main__":
    sys.exit(main())
