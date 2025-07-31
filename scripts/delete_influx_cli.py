#!/usr/bin/env python3
"""
Simple script to delete stored trading state from InfluxDB using influx CLI
"""

import os
import subprocess
import sys

def run_influx_command(cmd):
    """Run an influx CLI command"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            return True, result.stdout
        else:
            return False, result.stderr
    except Exception as e:
        return False, str(e)

def delete_measurement(measurement_name):
    """Delete a specific measurement from InfluxDB"""
    print(f"🗑️ Deleting {measurement_name} measurement...")
    
    # InfluxDB v2 delete command
    delete_cmd = f'''influx delete \
        --bucket trading_data \
        --start 1970-01-01T00:00:00Z \
        --stop 2030-01-01T00:00:00Z \
        --predicate '_measurement="{measurement_name}"' '''
    
    success, output = run_influx_command(delete_cmd)
    
    if success:
        print(f"✅ Successfully deleted {measurement_name}")
    else:
        print(f"❌ Failed to delete {measurement_name}: {output}")
    
    return success

def list_measurements():
    """List all measurements in the trading_data bucket"""
    print("📊 Listing measurements in trading_data bucket...")
    
    list_cmd = '''influx query 'import "influxdata/influxdb/schema" schema.measurements(bucket: "trading_data")' '''
    
    success, output = run_influx_command(list_cmd)
    
    if success:
        print("📋 Current measurements:")
        print(output)
    else:
        print(f"❌ Failed to list measurements: {output}")
    
    return success

def main():
    print("🗑️ InfluxDB Trading State Deletion Tool (CLI Version)")
    print("=" * 60)
    print("⚠️ Make sure InfluxDB CLI is installed and configured")
    print("⚠️ Make sure you have the correct bucket name 'trading_data'")
    print()
    
    # Check if influx CLI is available
    success, _ = run_influx_command("influx version")
    if not success:
        print("❌ InfluxDB CLI not found. Please install it first:")
        print("   https://docs.influxdata.com/influxdb/v2.0/tools/influx-cli/")
        return
    
    while True:
        print("\nOptions:")
        print("1. List current measurements")
        print("2. Delete 'positions' measurement")
        print("3. Delete 'pair_states' measurement") 
        print("4. Delete 'portfolio' measurement")
        print("5. Delete 'trading_state' measurement")
        print("6. Delete 'enhanced_positions' measurement")
        print("7. Delete 'enhanced_portfolio' measurement")
        print("8. Delete ALL trading measurements (COMPLETE RESET)")
        print("9. Manual delete (enter measurement name)")
        print("0. Exit")
        
        choice = input("\nEnter your choice (0-9): ").strip()
        
        if choice == "1":
            list_measurements()
        elif choice == "2":
            confirm = input("⚠️ Delete 'positions' measurement? (yes/no): ").strip().lower()
            if confirm == "yes":
                delete_measurement("positions")
        elif choice == "3":
            confirm = input("⚠️ Delete 'pair_states' measurement? (yes/no): ").strip().lower()
            if confirm == "yes":
                delete_measurement("pair_states")
        elif choice == "4":
            confirm = input("⚠️ Delete 'portfolio' measurement? (yes/no): ").strip().lower()
            if confirm == "yes":
                delete_measurement("portfolio")
        elif choice == "5":
            confirm = input("⚠️ Delete 'trading_state' measurement? (yes/no): ").strip().lower()
            if confirm == "yes":
                delete_measurement("trading_state")
        elif choice == "6":
            confirm = input("⚠️ Delete 'enhanced_positions' measurement? (yes/no): ").strip().lower()
            if confirm == "yes":
                delete_measurement("enhanced_positions")
        elif choice == "7":
            confirm = input("⚠️ Delete 'enhanced_portfolio' measurement? (yes/no): ").strip().lower()
            if confirm == "yes":
                delete_measurement("enhanced_portfolio")
        elif choice == "8":
            confirm = input("⚠️ DELETE ALL TRADING MEASUREMENTS? This cannot be undone! (yes/no): ").strip().lower()
            if confirm == "yes":
                measurements = ["positions", "pair_states", "portfolio", "trading_state", 
                              "enhanced_positions", "enhanced_portfolio"]
                for measurement in measurements:
                    delete_measurement(measurement)
        elif choice == "9":
            measurement = input("Enter measurement name to delete: ").strip()
            if measurement:
                confirm = input(f"⚠️ Delete '{measurement}' measurement? (yes/no): ").strip().lower()
                if confirm == "yes":
                    delete_measurement(measurement)
        elif choice == "0":
            print("👋 Goodbye!")
            break
        else:
            print("❌ Invalid choice. Please try again.")

if __name__ == "__main__":
    main()
