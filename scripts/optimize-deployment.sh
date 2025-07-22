#!/bin/bash

# Quick deployment optimization for trading system
# This script makes the deployment faster by pre-pulling images and optimizing Docker

set -e

echo "🚀 Optimizing deployment speed..."

# Pre-pull Docker images to save time during deployment
echo "📥 Pre-pulling Docker images..."
docker pull python:3.11-slim-bullseye
docker pull influxdb:2.7-alpine
docker pull redis:7-alpine

# Optimize Docker daemon settings for faster builds
echo "⚡ Optimizing Docker settings..."
sudo tee /etc/docker/daemon.json > /dev/null <<EOF
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "storage-driver": "overlay2",
  "storage-opts": [
    "overlay2.override_kernel_check=true"
  ],
  "dns": ["8.8.8.8", "8.8.4.4"],
  "max-concurrent-downloads": 10,
  "max-concurrent-uploads": 10
}
EOF

# Restart Docker to apply optimizations
sudo systemctl restart docker

# Create Docker build cache directory
mkdir -p ~/.docker/buildx

# Enable Docker BuildKit for faster builds
echo 'export DOCKER_BUILDKIT=1' >> ~/.bashrc
echo 'export COMPOSE_DOCKER_CLI_BUILD=1' >> ~/.bashrc

# Pre-create volumes for faster startup
docker volume create trading_influxdb-data
docker volume create trading_redis-data

# Download and cache pip packages
echo "📦 Pre-caching Python packages..."
mkdir -p /tmp/pip-cache
cd /opt/trading-system
if [ -f requirements.txt ]; then
    python3 -m pip download -d /tmp/pip-cache -r requirements.txt
    echo "✅ Python packages cached"
fi

# Set up log rotation to prevent disk space issues
sudo tee /etc/logrotate.d/docker-containers > /dev/null <<EOF
/var/lib/docker/containers/*/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
}
EOF

# Enable swap accounting for better container memory management
if ! grep -q 'cgroup_enable=memory swapaccount=1' /proc/cmdline; then
    echo "⚙️ Enabling swap accounting (requires reboot)..."
    sudo sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="/&cgroup_enable=memory swapaccount=1 /' /etc/default/grub
    sudo update-grub
    echo "⚠️ Reboot required for swap accounting changes"
fi

# Create monitoring script
sudo tee /usr/local/bin/trading-status > /dev/null <<'EOF'
#!/bin/bash
echo "📊 Trading System Status"
echo "=========================="
cd /opt/trading-system/current 2>/dev/null || cd /opt/trading-system
echo "🐳 Container Status:"
docker-compose ps
echo ""
echo "💾 Disk Usage:"
df -h /opt/trading-system
echo ""
echo "🧠 Memory Usage:"
free -h
echo ""
echo "🔗 Service URLs:"
PUBLIC_IP=$(curl -s http://checkip.amazonaws.com/)
echo "Dashboard: http://$PUBLIC_IP:8050"
echo "InfluxDB: http://$PUBLIC_IP:8086"
EOF

sudo chmod +x /usr/local/bin/trading-status

# Create quick update script
sudo tee /usr/local/bin/trading-update > /dev/null <<'EOF'
#!/bin/bash
echo "🔄 Quick update from GitHub..."
cd /opt/trading-system/current
git pull origin main
docker-compose up --build -d
echo "✅ Update complete!"
EOF

sudo chmod +x /usr/local/bin/trading-update

# Create backup script
sudo tee /usr/local/bin/trading-backup > /dev/null <<'EOF'
#!/bin/bash
echo "💾 Creating backup..."
BACKUP_DIR="/opt/trading-system/backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p $BACKUP_DIR
cd /opt/trading-system/current

# Backup configuration and data
cp -r .env.production $BACKUP_DIR/
docker-compose exec influxdb influx backup /tmp/backup
docker cp $(docker-compose ps -q influxdb):/tmp/backup $BACKUP_DIR/influxdb-backup
docker-compose exec redis redis-cli BGSAVE
docker cp $(docker-compose ps -q redis):/data/dump.rdb $BACKUP_DIR/redis-backup.rdb

echo "✅ Backup created at $BACKUP_DIR"
EOF

sudo chmod +x /usr/local/bin/trading-backup

echo "🎉 Optimization complete!"
echo ""
echo "Available commands:"
echo "  trading-status  - Check system status"
echo "  trading-update  - Quick update from git"
echo "  trading-backup  - Create backup"
echo ""
echo "Your system is now optimized for fast deployments!"
