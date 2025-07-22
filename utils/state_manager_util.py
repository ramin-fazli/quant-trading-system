#!/usr/bin/env python3
"""
Trading State Management Utility
================================

This utility helps manage trading state restoration and inspection.
Use this to check, restore, or backup trading states.
"""

import os
import sys
from datetime import datetime

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Load environment variables
try:
    from dotenv import load_dotenv
    
    env_path = os.path.join(project_root, '.env')
    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"✅ Loaded environment from: {env_path}")
    else:
        print(f"⚠️ .env file not found at: {env_path}")
        
except ImportError:
    print("⚠️ python-dotenv not installed")

def check_trading_state():
    """Check current trading state in the database"""
    try:
        from utils.unified_state_manager import UnifiedStateManager
        
        print("Trading State Inspection")
        print("=" * 40)
        
        # Initialize state manager
        print("🔄 Initializing state manager...")
        state_manager = UnifiedStateManager(auto_migrate=True)
        
        # Give it a moment to fully initialize
        import time
        time.sleep(2)
        
        print("🔍 Debugging state manager initialization...")
        
        # Check if state manager has required components
        print(f"  Enhanced manager available: {hasattr(state_manager, 'enhanced_manager')}")
        if hasattr(state_manager, 'enhanced_manager') and state_manager.enhanced_manager:
            print(f"  Enhanced manager type: {type(state_manager.enhanced_manager)}")
            
            # Try to manually initialize if needed
            try:
                if hasattr(state_manager.enhanced_manager, 'initialize'):
                    print("  Attempting manual initialization...")
                    init_result = state_manager.enhanced_manager.initialize()
                    print(f"  Manual initialization result: {init_result}")
            except Exception as e:
                print(f"  Manual initialization error: {e}")
        
        # Try health check with detailed info
        try:
            health_result = state_manager.health_check()
            print(f"  Health check result: {health_result}")
        except Exception as e:
            print(f"  Health check error: {e}")
            
        # Get system status regardless of health check
        try:
            status = state_manager.get_system_status()
            print(f"  System status: {status}")
        except Exception as e:
            print(f"  Cannot get system status: {e}")
            return False
            
        if not status.get('initialized', False):
            print("⚠️ State manager not fully initialized, but continuing...")
            # Continue anyway to see what we can get
        
        # Get system status
        status = state_manager.get_system_status()
        print(f"Database Type: {status.get('database_type', 'Unknown')}")
        print(f"Status: {status.get('status', 'Unknown')}")
        print(f"Connected: {status.get('database_connected', False)}")
        print()
        
        # Get all positions
        positions = state_manager.get_all_positions()
        print(f"📊 Total Positions: {len(positions)}")
        
        if positions:
            print("\nPosition Details:")
            for i, position in enumerate(positions, 1):
                symbol1 = position.get('symbol1', 'N/A')
                symbol2 = position.get('symbol2', 'N/A')
                direction = position.get('direction', 'N/A')
                entry_price = position.get('entry_price', 0)
                quantity = position.get('quantity', 0)
                entry_time = position.get('entry_time', 'N/A')
                
                print(f"  {i}. {symbol1}-{symbol2}: {direction}")
                print(f"     Entry: {entry_price} | Qty: {quantity}")
                print(f"     Time: {entry_time}")
        
        # Get current trading state
        current_state = state_manager.load_trading_state()
        if current_state:
            print(f"\n🔄 Current Trading State Available")
            metadata = current_state.get('metadata', {})
            if 'session_info' in metadata:
                session = metadata['session_info']
                print(f"  Data Provider: {session.get('data_provider', 'N/A')}")
                print(f"  Broker: {session.get('broker', 'N/A')}")
                print(f"  Strategy: {session.get('strategy', 'N/A')}")
                print(f"  Last Save: {session.get('save_time', 'N/A')}")
            
            pair_states = current_state.get('pair_states', {})
            if pair_states:
                print(f"\n📈 Pair States: {len(pair_states)}")
                for pair, state in list(pair_states.items())[:5]:  # Show first 5
                    position = state.get('position', 'none')
                    cooldown = state.get('cooldown', 0)
                    print(f"  {pair}: {position} (cooldown: {cooldown})")
                if len(pair_states) > 5:
                    print(f"  ... and {len(pair_states) - 5} more")
        else:
            print("\n⚠️ No current trading state found")
        
        # Get portfolio summary
        portfolio = state_manager.get_portfolio_summary()
        print(f"\n💰 Portfolio Summary:")
        print(f"  Total Value: {portfolio.get('total_value', 0)}")
        print(f"  Open Positions: {portfolio.get('open_positions', 0)}")
        print(f"  Total P&L: {portfolio.get('total_pnl', 0)}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error checking trading state: {e}")
        return False

def backup_trading_state():
    """Create a backup of current trading state"""
    try:
        from utils.unified_state_manager import UnifiedStateManager
        import json
        
        print("Creating Trading State Backup")
        print("=" * 35)
        
        state_manager = UnifiedStateManager(auto_migrate=True)
        
        # Give it a moment to fully initialize
        import time
        time.sleep(2)
        
        # Try health check but continue anyway
        health_result = state_manager.health_check()
        if not health_result:
            print("⚠️ State manager health check failed, but continuing...")
        else:
            print("✅ State manager ready")
        
        # Get all state data
        positions = state_manager.get_all_positions()
        current_state = state_manager.load_trading_state()
        portfolio = state_manager.get_portfolio_summary()
        
        backup_data = {
            'backup_timestamp': datetime.now().isoformat(),
            'positions': positions,
            'current_state': current_state,
            'portfolio_summary': portfolio,
            'system_status': state_manager.get_system_status()
        }
        
        # Save to file
        backup_filename = f"trading_state_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        backup_path = os.path.join(project_root, 'state_fallback', backup_filename)
        
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        
        with open(backup_path, 'w') as f:
            json.dump(backup_data, f, indent=2, default=str)
        
        print(f"✅ Backup created: {backup_filename}")
        print(f"   Positions: {len(positions)}")
        print(f"   Location: {backup_path}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error creating backup: {e}")
        return False

def restore_from_backup(backup_file: str):
    """Restore trading state from a backup file"""
    try:
        from utils.unified_state_manager import UnifiedStateManager
        import json
        
        print(f"Restoring from backup: {backup_file}")
        print("=" * 40)
        
        if not os.path.exists(backup_file):
            print(f"❌ Backup file not found: {backup_file}")
            return False
        
        with open(backup_file, 'r') as f:
            backup_data = json.load(f)
        
        state_manager = UnifiedStateManager(auto_migrate=True)
        
        # Give it a moment to fully initialize
        import time
        time.sleep(2)
        
        if not state_manager.health_check():
            print("❌ State manager not available")
            return False
        
        # Restore positions
        positions = backup_data.get('positions', [])
        current_state = backup_data.get('current_state', {})
        
        if current_state and positions:
            success = state_manager.save_trading_state(
                positions=positions,
                pair_states=current_state.get('pair_states', {}),
                metadata=current_state.get('metadata', {}),
                description=f"Restored from backup: {os.path.basename(backup_file)}"
            )
            
            if success:
                print(f"✅ Successfully restored {len(positions)} positions")
                print(f"✅ Backup timestamp: {backup_data.get('backup_timestamp', 'Unknown')}")
                return True
            else:
                print("❌ Failed to restore trading state")
                return False
        else:
            print("❌ No valid state data found in backup")
            return False
            
    except Exception as e:
        print(f"❌ Error restoring from backup: {e}")
        return False

def main():
    """Main utility function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Trading State Management Utility')
    parser.add_argument('action', choices=['check', 'backup', 'restore'], 
                       help='Action to perform')
    parser.add_argument('--file', '-f', 
                       help='Backup file for restore operation')
    
    args = parser.parse_args()
    
    if args.action == 'check':
        success = check_trading_state()
    elif args.action == 'backup':
        success = backup_trading_state()
    elif args.action == 'restore':
        if not args.file:
            print("❌ Restore requires --file argument")
            success = False
        else:
            success = restore_from_backup(args.file)
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
