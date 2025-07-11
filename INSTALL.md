# Quick Installation Guide

## 🚀 Quick Start (Recommended)

### 1. Run Automated Setup
```bash
python setup_data_management.py
```

This script will:
- ✅ Check Python version compatibility
- ✅ Install all required packages
- ✅ Create necessary directories
- ✅ Set up configuration files
- ✅ Test all imports
- ✅ Create database setup scripts

### 2. Start Databases with Docker (Easy)
```bash
# Start all databases
docker-compose up -d

# Or start specific ones
docker-compose up -d influxdb timescaledb redis
```

### 3. Test the System
```bash
# Run basic examples
python data/example_usage.py

# Test InfluxDB integration
python data/examples/influxdb_example.py

# Test TimescaleDB integration
python data/examples/timescaledb_example.py
```

## 📦 Manual Installation

### 1. Install Core Requirements
```bash
# Install main requirements
pip install -r requirements.txt

# Install data management dependencies
pip install -r requirements-data-management.txt
```

### 2. Database Setup Options

#### Option A: Docker (Recommended)
```bash
# InfluxDB
docker run -d --name influxdb -p 8086:8086 influxdb:latest

# TimescaleDB
docker run -d --name timescaledb -p 5432:5432 \
  -e POSTGRES_PASSWORD=password \
  timescale/timescaledb:latest-pg14

# Redis
docker run -d --name redis -p 6379:6379 redis:latest
```

#### Option B: Local Installation
```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install influxdb timescaledb-2-postgresql-14 redis-server

# macOS
brew install influxdb timescaledb redis

# Windows
# Download installers from official websites
```

### 3. Configuration

1. **Copy and edit configuration:**
   ```bash
   cp data/config_template.json config/data_config.json
   # Edit with your database credentials
   ```

2. **Set environment variables:**
   ```bash
   cp .env.example .env
   # Update with your credentials
   ```

## 🔧 Package Details

### Core Dependencies
```bash
# Data processing
pip install pandas>=1.3.0 numpy>=1.21.0 scipy>=1.7.0

# Storage and serialization
pip install pyarrow>=10.0.0 joblib>=1.2.0

# Database connectivity
pip install sqlalchemy>=1.4.0 redis>=4.3.0

# Time series databases
pip install influxdb-client>=1.36.0 psycopg2-binary>=2.9.0

# HTTP and async
pip install requests>=2.28.0 aiohttp>=3.8.0

# Caching and utilities
pip install cachetools>=5.0.0

# Performance (optional but recommended)
pip install numba>=0.56.0
```

### Trading Platform Integration
```bash
# MetaTrader 5
pip install MetaTrader5>=5.0.45

# cTrader (if needed)
pip install ctrader_open_api
```

## 🗄️ Database Configuration

### InfluxDB
```python
config = {
    "influxdb": {
        "enabled": True,
        "url": "http://localhost:8086",
        "token": "your-token",
        "org": "trading",
        "bucket": "market_data"
    }
}
```

### TimescaleDB
```python
config = {
    "timescaledb": {
        "enabled": True,
        "connection_string": "postgresql://user:pass@localhost:5432/trading_db",
        "table_name": "ohlcv_data"
    }
}
```

### Redis
```python
config = {
    "cache": {
        "use_redis": True,
        "redis_config": {
            "host": "localhost",
            "port": 6379,
            "db": 0
        }
    }
}
```

## 🧪 Testing Installation

### Quick Test
```python
from data import quick_data_setup, get_data_fast

# Test basic functionality
factory = quick_data_setup(["EURUSD"])
print("✅ Data management system ready!")
```

### Comprehensive Test
```python
from data import DataFactory, test_all_providers

# Test all configured providers
factory = DataFactory(config_path="config/data_config.json")
results = factory.test_all_providers()
print(f"Working providers: {results}")
```

## 🐛 Troubleshooting

### Common Issues

1. **Import Error: No module named 'influxdb_client'**
   ```bash
   pip install influxdb-client
   ```

2. **Connection refused to InfluxDB**
   ```bash
   # Check if InfluxDB is running
   docker ps | grep influxdb
   
   # Start InfluxDB
   docker start influxdb
   ```

3. **PostgreSQL connection error**
   ```bash
   # Check TimescaleDB status
   docker ps | grep timescale
   
   # Verify credentials
   docker exec -it timescaledb psql -U trading_user -d trading_db
   ```

4. **Redis connection error**
   ```bash
   # Test Redis
   docker exec -it redis redis-cli ping
   ```

### Performance Issues

1. **Slow queries:**
   - Enable database indexes
   - Use appropriate chunk sizes
   - Configure connection pooling

2. **High memory usage:**
   - Reduce cache sizes
   - Enable data compression
   - Use pagination for large datasets

3. **Network timeouts:**
   - Increase timeout values
   - Check database server resources
   - Verify network connectivity

## 📊 Service URLs

After running `docker-compose up -d`:

- **InfluxDB UI:** http://localhost:8086
- **Grafana:** http://localhost:3000 (admin/trading_admin)
- **Database Admin:** http://localhost:8080
- **Redis Admin:** http://localhost:8081

## 🔐 Security Notes

### Production Deployment

1. **Change default passwords:**
   ```bash
   # Update docker-compose.yml with strong passwords
   # Use environment variables for sensitive data
   ```

2. **Network security:**
   ```bash
   # Use SSL/TLS connections
   # Configure firewalls
   # Limit database access
   ```

3. **API tokens:**
   ```bash
   # Generate unique tokens for each environment
   # Rotate tokens regularly
   # Use minimal required permissions
   ```

## 📈 Next Steps

1. **Configure your data sources**
2. **Set up monitoring and alerting**
3. **Implement backup strategies**
4. **Scale horizontally as needed**
5. **Integrate with your trading strategies**

For detailed documentation, see `data/README.md`