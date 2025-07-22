# 🔄 Updated Docker Compose Usage Guide

Your [`docker-compose.yml`](docker-compose.yml ) has been updated to support all the new environment variables from [`.env.development`](.env.development ).

## ✅ **New Features Added**

### **1. Redis Password Support**
```yaml
# Redis now supports optional password authentication
REDIS_PASSWORD=devredis123  # Set in .env file
```

### **2. Optional PostgreSQL**
```yaml
# Alternative database for state management
TRADING_STATE_POSTGRES_HOST=localhost
TRADING_STATE_POSTGRES_PORT=5432
TRADING_STATE_POSTGRES_DB=trading_states_dev
```

### **3. Enhanced Configuration**
- All new environment variables are now supported
- Better health checks with password support
- Optional services using Docker profiles

## 🚀 **How to Use**

### **Standard Setup (InfluxDB + Redis)**
```bash
# Development mode
export ENV_SUFFIX=.development
docker-compose up --build
```

### **With PostgreSQL (Alternative Database)**
```bash
# Start with PostgreSQL for state management
export ENV_SUFFIX=.development
docker-compose --profile postgres up --build
```

### **Production Mode**
```bash
# Production mode (no PostgreSQL needed)
export ENV_SUFFIX=.production
docker-compose up --build -d
```

## 🔧 **Environment Variable Support**

### **Redis Configuration**
- `REDIS_PASSWORD` - Optional Redis authentication
- `REDIS_MAX_MEMORY` - Memory limit (128mb dev, 256mb prod)
- `REDIS_PORT` - Port mapping (default: 6379)

### **PostgreSQL Configuration (Optional)**
- `TRADING_STATE_POSTGRES_HOST` - PostgreSQL host
- `TRADING_STATE_POSTGRES_PORT` - PostgreSQL port (default: 5432)
- `TRADING_STATE_POSTGRES_DB` - Database name
- `TRADING_STATE_POSTGRES_USER` - Database user
- `TRADING_STATE_POSTGRES_PASSWORD` - Database password

### **All Existing Variables Still Work**
- InfluxDB configuration
- CTrader/MT5 settings
- Dashboard configuration
- Logging settings
- Port mappings

## 🎯 **Key Benefits**

### ✅ **Backward Compatible**
- All existing configurations still work
- No changes needed for current deployments

### ✅ **Enhanced Security**
- Redis password authentication
- Proper health checks with authentication

### ✅ **Flexible Database Options**
- Use InfluxDB (default)
- Use PostgreSQL (optional with `--profile postgres`)
- Easy switching between environments

### ✅ **Development Friendly**
- PostgreSQL for local state management testing
- Redis password protection
- All environment-driven configuration

## 📝 **Quick Commands**

```bash
# Development with all services
export ENV_SUFFIX=.development
docker-compose --profile postgres up --build

# Production (standard services only)
export ENV_SUFFIX=.production  
docker-compose up --build -d

# Check services
docker-compose ps

# View logs
docker-compose logs -f trading-system

# Restart specific service
docker-compose restart redis
```

## 🔐 **Security Notes**

1. **Redis Password**: Now properly secured with authentication
2. **PostgreSQL**: Uses dedicated database with user credentials
3. **Environment Separation**: Clear separation between dev/prod configs
4. **Health Checks**: Enhanced with proper authentication

Your Docker setup now fully supports all the enhanced environment configurations! 🚀
