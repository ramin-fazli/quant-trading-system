#!/usr/bin/env python3
"""
Setup script for the Trading System Data Management

This script installs all required dependencies and sets up the environment
for the comprehensive data management system.
"""

import subprocess
import sys
import os
from pathlib import Path

def run_command(command, description):
    """Run a command and handle errors"""
    print(f"\n{'='*50}")
    print(f"🔧 {description}")
    print(f"{'='*50}")
    
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(f"✅ {description} completed successfully")
        if result.stdout:
            print(f"Output: {result.stdout.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} failed")
        print(f"Error: {e.stderr.strip()}")
        return False

def check_python_version():
    """Check if Python version is compatible"""
    print("🐍 Checking Python version...")
    
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print(f"❌ Python {version.major}.{version.minor} is not supported")
        print("✅ Please upgrade to Python 3.8 or higher")
        return False
    
    print(f"✅ Python {version.major}.{version.minor}.{version.micro} is supported")
    return True

def install_requirements():
    """Install all requirements"""
    requirements_files = [
        "requirements.txt",
        "requirements-data-management.txt"
    ]
    
    for req_file in requirements_files:
        if Path(req_file).exists():
            if not run_command(f"pip install -r {req_file}", f"Installing {req_file}"):
                return False
        else:
            print(f"⚠️  {req_file} not found, skipping...")
    
    return True

def setup_directories():
    """Create necessary directories"""
    directories = [
        "data",
        "cache", 
        "logs",
        "config",
        "data/parquet",
        "data/csv",
        "data/examples"
    ]
    
    print("\n📁 Creating directories...")
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
        print(f"✅ Created: {directory}")
    
    return True

def create_config_files():
    """Create default configuration files"""
    print("\n⚙️  Creating configuration files...")
    
    # Create default config if it doesn't exist
    config_file = Path("config/data_config.json")
    if not config_file.exists():
        # Copy from template
        template_file = Path("data/config_template.json")
        if template_file.exists():
            import shutil
            shutil.copy(template_file, config_file)
            print(f"✅ Created: {config_file}")
        else:
            print(f"⚠️  Template not found: {template_file}")
    
    # Create .env file template
    env_file = Path(".env")
    if not env_file.exists():
        env_content = """# Data Management System Environment Variables

# MetaTrader 5 Configuration
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=

# InfluxDB Configuration
INFLUXDB_URL=http://localhost:8086
INFLUXDB_TOKEN=
INFLUXDB_ORG=trading
INFLUXDB_BUCKET=market_data

# TimescaleDB Configuration
TIMESCALEDB_URL=postgresql://trading_user:password@localhost:5432/trading_db

# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

# Alpha Vantage API
ALPHA_VANTAGE_API_KEY=

# Cache Configuration
CACHE_DIR=cache
CACHE_SIZE_MB=1024

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/data_manager.log
"""
        with open(env_file, 'w') as f:
            f.write(env_content)
        print(f"✅ Created: {env_file}")
    
    return True

def test_imports():
    """Test that all required packages can be imported"""
    print("\n🧪 Testing package imports...")
    
    test_packages = [
        ("pandas", "Data manipulation"),
        ("numpy", "Numerical computing"),
        ("pyarrow", "Columnar storage"),
        ("sqlalchemy", "Database ORM"),
        ("redis", "Redis client"),
        ("aiohttp", "Async HTTP client"),
        ("requests", "HTTP requests"),
        ("joblib", "Serialization"),
        ("cachetools", "Caching utilities"),
        ("numba", "JIT compilation (optional)")
    ]
    
    failed_imports = []
    
    for package, description in test_packages:
        try:
            __import__(package)
            print(f"✅ {package} - {description}")
        except ImportError as e:
            print(f"❌ {package} - {description} (FAILED)")
            failed_imports.append(package)
    
    # Test optional packages
    optional_packages = [
        ("influxdb_client", "InfluxDB client"),
        ("psycopg2", "PostgreSQL adapter"),
        ("MetaTrader5", "MT5 integration")
    ]
    
    print("\n🔧 Testing optional packages...")
    for package, description in optional_packages:
        try:
            __import__(package)
            print(f"✅ {package} - {description}")
        except ImportError:
            print(f"⚠️  {package} - {description} (Optional, not installed)")
    
    if failed_imports:
        print(f"\n❌ Some required packages failed to import: {failed_imports}")
        print("Please check the installation and try again.")
        return False
    
    print("\n✅ All required packages imported successfully!")
    return True

def setup_database_examples():
    """Create database setup examples"""
    print("\n🗄️  Creating database setup examples...")
    
    # InfluxDB setup script
    influxdb_setup = """#!/bin/bash
# InfluxDB Setup Script

echo "Setting up InfluxDB..."

# Start InfluxDB with Docker
docker run -d --name influxdb \\
  -p 8086:8086 \\
  -v influxdb-storage:/var/lib/influxdb2 \\
  influxdb:latest

echo "InfluxDB started on http://localhost:8086"
echo "Complete setup through the web UI:"
echo "1. Create initial user and organization"
echo "2. Create bucket named 'market_data'"
echo "3. Generate API token"
echo "4. Update .env file with token"
"""
    
    with open("setup_influxdb.sh", 'w') as f:
        f.write(influxdb_setup)
    
    # TimescaleDB setup script
    timescaledb_setup = """#!/bin/bash
# TimescaleDB Setup Script

echo "Setting up TimescaleDB..."

# Start TimescaleDB with Docker
docker run -d --name timescaledb \\
  -p 5432:5432 \\
  -e POSTGRES_PASSWORD=password \\
  -v timescaledb-storage:/var/lib/postgresql/data \\
  timescale/timescaledb:latest-pg14

echo "Waiting for database to start..."
sleep 10

# Create trading database
docker exec -it timescaledb psql -U postgres -c "CREATE DATABASE trading_db;"
docker exec -it timescaledb psql -U postgres -d trading_db -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"

echo "TimescaleDB setup complete!"
echo "Connection: postgresql://postgres:password@localhost:5432/trading_db"
"""
    
    with open("setup_timescaledb.sh", 'w') as f:
        f.write(timescaledb_setup)
    
    # Redis setup script
    redis_setup = """#!/bin/bash
# Redis Setup Script

echo "Setting up Redis..."

# Start Redis with Docker
docker run -d --name redis \\
  -p 6379:6379 \\
  -v redis-storage:/data \\
  redis:latest redis-server --appendonly yes

echo "Redis started on localhost:6379"
"""
    
    with open("setup_redis.sh", 'w') as f:
        f.write(redis_setup)
    
    # Make scripts executable
    if os.name != 'nt':  # Not Windows
        os.chmod("setup_influxdb.sh", 0o755)
        os.chmod("setup_timescaledb.sh", 0o755)
        os.chmod("setup_redis.sh", 0o755)
    
    print("✅ Database setup scripts created")
    return True

def main():
    """Main setup function"""
    print("🚀 Trading System Data Management Setup")
    print("=" * 50)
    
    # Check Python version
    if not check_python_version():
        sys.exit(1)
    
    # Setup directories
    if not setup_directories():
        print("❌ Failed to create directories")
        sys.exit(1)
    
    # Install requirements
    if not install_requirements():
        print("❌ Failed to install requirements")
        sys.exit(1)
    
    # Create config files
    if not create_config_files():
        print("❌ Failed to create configuration files")
        sys.exit(1)
    
    # Test imports
    if not test_imports():
        print("❌ Import tests failed")
        sys.exit(1)
    
    # Setup database examples
    if not setup_database_examples():
        print("❌ Failed to create database setup scripts")
        sys.exit(1)
    
    print("\n" + "=" * 50)
    print("🎉 Setup completed successfully!")
    print("=" * 50)
    
    print("\n📋 Next Steps:")
    print("1. Update .env file with your database credentials")
    print("2. Run database setup scripts if needed:")
    print("   - ./setup_influxdb.sh")
    print("   - ./setup_timescaledb.sh") 
    print("   - ./setup_redis.sh")
    print("3. Test the system with: python data/example_usage.py")
    print("4. Check specific database examples in data/examples/")
    
    print("\n📚 Documentation:")
    print("- Main README: data/README.md")
    print("- Configuration: data/config_template.json")
    print("- Examples: data/examples/")
    
    print("\n🔧 Troubleshooting:")
    print("- Check logs in logs/ directory")
    print("- Verify database connections")
    print("- Update configuration files as needed")

if __name__ == "__main__":
    main()