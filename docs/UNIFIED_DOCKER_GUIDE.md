# 🎯 Unified Docker Compose Guide

Your trading system now uses **ONE Docker Compose file** that works for both local development and production! This makes everything simpler and easier to maintain.

## ✅ **What Changed**

- ❌ **Before**: `docker-compose.yml` + `docker-compose.production.yml` (2 files)
- ✅ **Now**: `docker-compose.yml` (1 file) + environment variables

## 🚀 **How to Use**

### **Local Development**
```bash
# Copy environment template for development
cp .env.template .env.development

# Edit values for local development
nano .env.development

# Set environment for development
export ENV_SUFFIX=.development
export ENVIRONMENT=development
export SOURCE_MOUNT=./src

# Start in development mode
docker-compose up --build
```

### **Production Deployment**
```bash
# Copy environment template for production  
cp .env.template .env.production

# Edit values for production (secure passwords!)
nano .env.production

# Set environment for production
export ENV_SUFFIX=.production
export ENVIRONMENT=production
export SOURCE_MOUNT=""

# Start in production mode
docker-compose up --build -d
```

## 🔧 **Environment Variables**

The unified `docker-compose.yml` uses these key variables:

### **Environment Control**
- `ENV_SUFFIX` - Which .env file to use (.development, .production)
- `ENVIRONMENT` - Application environment (development, production)
- `SOURCE_MOUNT` - Mount source code for hot reload ("./src" or "")

### **Port Configuration**
- `DASHBOARD_PORT` - Dashboard port (default: 8050)
- `API_PORT` - API port (default: 8000)  
- `INFLUXDB_PORT` - InfluxDB port (default: 8086)
- `REDIS_PORT` - Redis port (default: 6379)

### **Resource Limits**
- `REDIS_MAX_MEMORY` - Redis memory limit (128mb dev, 256mb prod)
- `LOG_MAX_SIZE` - Log file size limit (5m dev, 10m prod)
- `LOG_MAX_FILES` - Number of log files to keep (2 dev, 3 prod)

## 📝 **Quick Commands**

### **Development Workflow**
```bash
# Development mode with hot reload
export ENV_SUFFIX=.development && docker-compose up --build

# Check logs
docker-compose logs -f trading-system

# Restart specific service
docker-compose restart trading-system

# Clean rebuild
docker-compose down && docker-compose up --build
```

### **Production Workflow**
```bash
# Production deployment
export ENV_SUFFIX=.production && docker-compose up --build -d

# Check status
docker-compose ps

# View logs
docker-compose logs --tail=50 trading-system

# Update with zero downtime
docker-compose up --build -d
```

## 🎯 **Benefits of Unified Approach**

### ✅ **Simplicity**
- **1 file** instead of 2
- **Same commands** for dev and prod
- **Environment-driven** configuration

### ✅ **Consistency**  
- **Same services** in dev and prod
- **Same volumes** and networks
- **Same health checks**

### ✅ **Flexibility**
- **Easy switching** between environments
- **Customizable** per environment
- **Version controlled** configuration

## 📊 **Environment File Examples**

### **Development (.env.development)**
```env
ENVIRONMENT=development
TRADING_MODE=backtest
LOG_LEVEL=DEBUG
ENV_SUFFIX=.development
SOURCE_MOUNT=./src
REDIS_MAX_MEMORY=128mb
LOG_MAX_SIZE=5m
```

### **Production (.env.production)**
```env
ENVIRONMENT=production
TRADING_MODE=live
LOG_LEVEL=INFO
ENV_SUFFIX=.production
SOURCE_MOUNT=""
REDIS_MAX_MEMORY=256mb
LOG_MAX_SIZE=10m
```

## 🔒 **GitHub Actions Integration**

The CI/CD pipeline automatically:
1. ✅ Uses the unified `docker-compose.yml`
2. ✅ Creates `.env.production` with GitHub Secrets
3. ✅ Sets production environment variables
4. ✅ Deploys with zero downtime

## 🎉 **Result**

You now have a **simpler, cleaner, and more maintainable** setup:
- **Fewer files** to manage
- **Consistent** across environments  
- **Easy** to switch between dev/prod
- **Environment-driven** configuration
- **Still works** with your simple CI/CD pipeline

**Perfect for your "fast, easy, and efficient" requirements!** 🚀
