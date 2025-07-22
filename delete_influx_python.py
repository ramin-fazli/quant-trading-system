#!/usr/bin/env python3
"""
Delete InfluxDB trading state data using direct API calls
"""

import os
from datetime import datetime, timedelta
from influxdb_client import InfluxDBClient
from influxdb_client.client.delete_api import DeleteApi

# Load environment variables
def load_env():
    """Load environment variables from .env file"""
    env_file = '.env'
    if os.path.exists(env_file):
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()

def get_influx_connection():
    """Get InfluxDB connection using environment variables"""
    load_env()
    
    url = os.getenv('INFLUXDB_URL', 'http://localhost:8086')
    token = os.getenv('INFLUXDB_TOKEN')
    org = os.getenv('INFLUXDB_ORG', 'trading_org')
    bucket = os.getenv('INFLUXDB_BUCKET', 'trading_data')
    
    if not token:
        print("❌ INFLUXDB_TOKEN not found in environment variables")
        return None, None, None, None
    
    client = InfluxDBClient(url=url, token=token, org=org)
    return client, org, bucket, token

def list_measurements():
    """List all measurements in the bucket"""
    client, org, bucket, token = get_influx_connection()
    if not client:
        return
    
    try:
        query_api = client.query_api()
        
        # Query to get all measurements
        query = f'''
        import "influxdata/influxdb/schema"
        schema.measurements(bucket: "{bucket}")
        '''
        
        print(f"📊 Querying measurements in bucket '{bucket}'...")
        result = query_api.query(query)
        
        measurements = []
        for table in result:
            for record in table.records:
                measurements.append(record.get_value())
        
        if measurements:
            print(f"📋 Found {len(measurements)} measurements:")
            for measurement in measurements:
                print(f"   - {measurement}")
                
                # Count records in each measurement
                count_query = f'''
                from(bucket: "{bucket}")
                    |> range(start: 0)
                    |> filter(fn: (r) => r["_measurement"] == "{measurement}")
                    |> count()
                '''
                
                try:
                    count_result = query_api.query(count_query)
                    total_records = 0
                    for table in count_result:
                        for record in table.records:
                            value = record.get_value()
                            if value is not None:
                                total_records += value
                    print(f"     📊 Records: {total_records}")
                except Exception as e:
                    print(f"     ⚠️ Could not count records: {e}")
        else:
            print("✅ No measurements found - bucket is clean")
        
        client.close()
        
    except Exception as e:
        print(f"❌ Error listing measurements: {e}")
        if client:
            client.close()

def delete_measurement(measurement_name):
    """Delete a specific measurement"""
    client, org, bucket, token = get_influx_connection()
    if not client:
        return False
    
    try:
        delete_api = client.delete_api()
        
        print(f"🗑️ Deleting measurement '{measurement_name}' from bucket '{bucket}'...")
        
        # Delete all data for this measurement
        delete_api.delete(
            start=datetime(1970, 1, 1),
            stop=datetime.now() + timedelta(days=1),
            predicate=f'_measurement="{measurement_name}"',
            bucket=bucket,
            org=org
        )
        
        print(f"✅ Successfully deleted '{measurement_name}' measurement")
        client.close()
        return True
        
    except Exception as e:
        print(f"❌ Error deleting measurement '{measurement_name}': {e}")
        if client:
            client.close()
        return False

def delete_all_trading_data():
    """Delete all trading-related measurements"""
    measurements = [
        "positions",
        "pair_states", 
        "portfolio",
        "trading_state",
        "enhanced_positions",
        "enhanced_portfolio",
        "unified_state"
    ]
    
    print("🗑️ Deleting all trading measurements...")
    success_count = 0
    
    for measurement in measurements:
        if delete_measurement(measurement):
            success_count += 1
    
    print(f"✅ Successfully deleted {success_count}/{len(measurements)} measurements")

def main():
    print("🗑️ InfluxDB Trading State Deletion Tool (Python API)")
    print("=" * 60)
    print("📋 This tool will use your .env file for InfluxDB credentials")
    print()
    
    # Test connection
    client, org, bucket, token = get_influx_connection()
    if not client:
        print("❌ Cannot connect to InfluxDB. Check your .env file.")
        return
    
    print(f"✅ Connected to InfluxDB")
    print(f"   URL: {os.getenv('INFLUXDB_URL', 'http://localhost:8086')}")
    print(f"   Org: {org}")
    print(f"   Bucket: {bucket}")
    client.close()
    print()
    
    while True:
        print("Options:")
        print("1. List current measurements")
        print("2. Delete 'positions' measurement")
        print("3. Delete 'pair_states' measurement") 
        print("4. Delete 'portfolio' measurement")
        print("5. Delete 'trading_state' measurement")
        print("6. Delete 'enhanced_positions' measurement")
        print("7. Delete 'enhanced_portfolio' measurement")
        print("8. Delete 'unified_state' measurement")
        print("9. Delete ALL trading measurements (COMPLETE RESET)")
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
            confirm = input("⚠️ Delete 'unified_state' measurement? (yes/no): ").strip().lower()
            if confirm == "yes":
                delete_measurement("unified_state")
        elif choice == "9":
            confirm = input("⚠️ DELETE ALL TRADING MEASUREMENTS? This cannot be undone! (yes/no): ").strip().lower()
            if confirm == "yes":
                delete_all_trading_data()
        elif choice == "0":
            print("👋 Goodbye!")
            break
        else:
            print("❌ Invalid choice. Please try again.")

if __name__ == "__main__":
    main()
