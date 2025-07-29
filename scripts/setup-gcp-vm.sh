#!/bin/bash
# Optimized GCP VM Setup Script for Trading System
# This script sets up a production-ready Google Cloud VM for the trading system

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Configuration
APP_USER="trading"
APP_DIR="/opt/trading-system"
LOG_DIR="/var/log/trading-system"
SERVICE_NAME="trading-system"

log "🚀 Starting GCP VM setup for Trading System"

# Check if running as root or with sudo
if [[ $EUID -eq 0 ]]; then
    warning "This script is running as root. Some commands may need adjustment."
fi

# Update system packages
log "📦 Updating system packages..."
sudo apt-get update -y
sudo apt-get upgrade -y

# Install essential packages
log "🔧 Installing essential packages..."
sudo apt-get install -y \
    curl \
    wget \
    git \
    unzip \
    htop \
    vim \
    tmux \
    jq \
    net-tools \
    software-properties-common \
    apt-transport-https \
    ca-certificates \
    gnupg \
    lsb-release \
    ufw \
    fail2ban \
    logrotate

# Install Docker
if ! command -v docker &> /dev/null; then
    log "🐳 Installing Docker..."
    
    # Add Docker's official GPG key
    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    
    # Add Docker repository
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
        $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    # Install Docker
    sudo apt-get update -y
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    
    # Add current user to docker group
    sudo usermod -aG docker $USER
    sudo usermod -aG docker ubuntu 2>/dev/null || true
    
    # Enable and start Docker
    sudo systemctl enable docker
    sudo systemctl start docker
    
    success "Docker installed successfully"
else
    success "Docker is already installed"
fi

# Install Google Cloud CLI
if ! command -v gcloud &> /dev/null; then
    log "☁️ Installing Google Cloud CLI..."
    
    curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /etc/apt/keyrings/cloud.google.gpg
    echo "deb [signed-by=/etc/apt/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee -a /etc/apt/sources.list.d/google-cloud-sdk.list
    
    sudo apt-get update -y
    sudo apt-get install -y google-cloud-cli
    
    success "Google Cloud CLI installed successfully"
else
    success "Google Cloud CLI is already installed"
fi

# Create application user (if not exists)
if ! id "$APP_USER" &>/dev/null; then
    log "👤 Creating application user: $APP_USER"
    sudo useradd -r -s /bin/bash -m -d /home/$APP_USER $APP_USER
    sudo usermod -aG docker $APP_USER
else
    success "User $APP_USER already exists"
fi

# Create application directories
log "📁 Creating application directories..."
sudo mkdir -p $APP_DIR/{logs,data,backtest_reports,cache,config}
sudo mkdir -p $LOG_DIR
sudo chown -R $APP_USER:$APP_USER $APP_DIR
sudo chown -R $APP_USER:$APP_USER $LOG_DIR

# Set up log rotation
log "📊 Configuring log rotation..."
sudo tee /etc/logrotate.d/trading-system > /dev/null << EOF
$LOG_DIR/*.log {
    daily
    missingok
    rotate 30
    compress
    notifempty
    create 644 $APP_USER $APP_USER
    postrotate
        /usr/bin/docker kill -s USR1 trading-system 2>/dev/null || true
    endscript
}

$APP_DIR/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    notifempty
    create 644 $APP_USER $APP_USER
}
EOF

# Configure firewall
log "🔥 Configuring UFW firewall..."
sudo ufw --force reset
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow 8050/tcp comment "Trading System Dashboard"
sudo ufw allow 8080/tcp comment "Trading System API"
sudo ufw --force enable

# Configure fail2ban
log "🛡️ Configuring fail2ban..."
sudo tee /etc/fail2ban/jail.local > /dev/null << EOF
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
EOF

sudo systemctl enable fail2ban
sudo systemctl restart fail2ban

# Create Docker daemon configuration for better performance
log "⚙️ Optimizing Docker configuration..."
sudo tee /etc/docker/daemon.json > /dev/null << EOF
{
    "log-driver": "json-file",
    "log-opts": {
        "max-size": "100m",
        "max-file": "5"
    },
    "storage-driver": "overlay2",
    "live-restore": true,
    "userland-proxy": false,
    "experimental": false,
    "metrics-addr": "127.0.0.1:9323",
    "default-ulimits": {
        "nofile": {
            "Hard": 64000,
            "Name": "nofile",
            "Soft": 64000
        }
    }
}
EOF

sudo systemctl restart docker

# Create systemd service for the trading system (backup method)
log "🔧 Creating systemd service..."
sudo tee /etc/systemd/system/trading-system.service > /dev/null << EOF
[Unit]
Description=Trading System Docker Container
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/docker start trading-system
ExecStop=/usr/bin/docker stop trading-system
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable trading-system.service

# Set up monitoring script
log "📊 Creating monitoring script..."
sudo tee /usr/local/bin/trading-system-monitor.sh > /dev/null << 'EOF'
#!/bin/bash
# Trading System Monitoring Script

CONTAINER_NAME="trading-system"
LOG_FILE="/var/log/trading-system/monitor.log"

# Function to log with timestamp
log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# Check if container is running
if ! docker ps | grep -q "$CONTAINER_NAME"; then
    log_message "ERROR: Container $CONTAINER_NAME is not running"
    
    # Try to start the container
    if docker start "$CONTAINER_NAME" 2>/dev/null; then
        log_message "INFO: Successfully restarted container $CONTAINER_NAME"
    else
        log_message "ERROR: Failed to restart container $CONTAINER_NAME"
    fi
else
    # Check container health
    CONTAINER_STATUS=$(docker inspect -f '{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null)
    if [ "$CONTAINER_STATUS" = "running" ]; then
        # Check if the service is responding
        if curl -f http://localhost:8050 >/dev/null 2>&1; then
            log_message "INFO: Service is healthy"
        else
            log_message "WARNING: Service is not responding on port 8050"
        fi
    else
        log_message "WARNING: Container status is $CONTAINER_STATUS"
    fi
fi

# Log system resources
MEMORY_USAGE=$(free | grep Mem | awk '{printf "%.1f", $3/$2 * 100.0}')
DISK_USAGE=$(df -h / | awk 'NR==2{print $5}' | sed 's/%//')

if (( $(echo "$MEMORY_USAGE > 90" | bc -l) )); then
    log_message "WARNING: High memory usage: ${MEMORY_USAGE}%"
fi

if (( DISK_USAGE > 90 )); then
    log_message "WARNING: High disk usage: ${DISK_USAGE}%"
fi
EOF

sudo chmod +x /usr/local/bin/trading-system-monitor.sh

# Set up cron job for monitoring
log "⏰ Setting up monitoring cron job..."
(crontab -l 2>/dev/null; echo "*/5 * * * * /usr/local/bin/trading-system-monitor.sh") | crontab -

# Create health check script
log "🏥 Creating health check script..."
sudo tee /usr/local/bin/trading-system-health.sh > /dev/null << 'EOF'
#!/bin/bash
# Trading System Health Check Script

CONTAINER_NAME="trading-system"

echo "=== Trading System Health Check ==="
echo "Date: $(date)"
echo

# Container status
echo "Container Status:"
if docker ps -f name="$CONTAINER_NAME" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -q "$CONTAINER_NAME"; then
    docker ps -f name="$CONTAINER_NAME" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    echo "✅ Container is running"
else
    echo "❌ Container is not running"
fi
echo

# Resource usage
echo "Resource Usage:"
echo "Memory: $(free -h | grep '^Mem:' | awk '{print $3 "/" $2}')"
echo "Disk: $(df -h / | awk 'NR==2{print $3 "/" $2 " (" $5 " used)"}')"
echo "CPU Load: $(uptime | awk -F'load average:' '{print $2}')"
echo

# Service connectivity
echo "Service Connectivity:"
if curl -f http://localhost:8050 >/dev/null 2>&1; then
    echo "✅ Dashboard (8050) is accessible"
else
    echo "❌ Dashboard (8050) is not accessible"
fi

if curl -f http://localhost:8080 >/dev/null 2>&1; then
    echo "✅ API (8080) is accessible"
else
    echo "❌ API (8080) is not accessible"
fi
echo

# Recent logs
echo "Recent Container Logs (last 10 lines):"
docker logs --tail 10 "$CONTAINER_NAME" 2>/dev/null || echo "No logs available"
EOF

sudo chmod +x /usr/local/bin/trading-system-health.sh

# Create update script
log "🔄 Creating update script..."
sudo tee /usr/local/bin/trading-system-update.sh > /dev/null << 'EOF'
#!/bin/bash
# Trading System Update Script

CONTAINER_NAME="trading-system"
IMAGE_NAME="${IMAGE_NAME:-europe-west3-docker.pkg.dev/PROJECT_ID/trading-system/pair-trading-system:latest}"

echo "🔄 Updating Trading System..."

# Authenticate with GCP (if needed)
if command -v gcloud &> /dev/null; then
    gcloud auth configure-docker europe-west3-docker.pkg.dev --quiet
fi

# Pull latest image
echo "📥 Pulling latest image..."
docker pull "$IMAGE_NAME"

# Stop and remove old container
echo "🛑 Stopping old container..."
docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true

# Start new container (this should be handled by deployment script)
echo "✅ Update preparation completed. Container will be started by deployment process."
EOF

sudo chmod +x /usr/local/bin/trading-system-update.sh

# Set up system optimizations
log "⚡ Applying system optimizations..."

# Increase file descriptor limits
sudo tee -a /etc/security/limits.conf > /dev/null << EOF
# Trading System optimizations
$APP_USER soft nofile 65536
$APP_USER hard nofile 65536
root soft nofile 65536
root hard nofile 65536
EOF

# Optimize kernel parameters
sudo tee /etc/sysctl.d/99-trading-system.conf > /dev/null << EOF
# Trading System kernel optimizations
net.core.rmem_max = 134217728
net.core.wmem_max = 134217728
net.ipv4.tcp_rmem = 4096 65536 134217728
net.ipv4.tcp_wmem = 4096 65536 134217728
net.core.netdev_max_backlog = 5000
vm.swappiness = 10
vm.dirty_ratio = 15
vm.dirty_background_ratio = 5
EOF

sudo sysctl -p /etc/sysctl.d/99-trading-system.conf

# Create backup script
log "💾 Creating backup script..."
sudo tee /usr/local/bin/trading-system-backup.sh > /dev/null << 'EOF'
#!/bin/bash
# Trading System Backup Script

BACKUP_DIR="/opt/backups/trading-system"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=30

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Backup application data
echo "📦 Creating backup for $DATE..."
tar -czf "$BACKUP_DIR/trading-system-data-$DATE.tar.gz" \
    -C /opt/trading-system \
    data cache config 2>/dev/null || true

# Backup logs
tar -czf "$BACKUP_DIR/trading-system-logs-$DATE.tar.gz" \
    -C /opt/trading-system \
    logs 2>/dev/null || true

# Clean old backups
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +$RETENTION_DAYS -delete

echo "✅ Backup completed: $BACKUP_DIR/trading-system-*-$DATE.tar.gz"
EOF

sudo chmod +x /usr/local/bin/trading-system-backup.sh

# Set up daily backup cron job
(crontab -l 2>/dev/null; echo "0 2 * * * /usr/local/bin/trading-system-backup.sh") | crontab -

# Install additional monitoring tools
log "📊 Installing monitoring tools..."
sudo apt-get install -y iostat iotop nethogs

# Create startup script
log "🚀 Creating startup script..."
sudo tee /usr/local/bin/trading-system-start.sh > /dev/null << 'EOF'
#!/bin/bash
# Trading System Startup Script

echo "🚀 Starting Trading System..."

# Wait for Docker to be ready
sleep 5

# Check if container exists and start it
if docker ps -a -f name=trading-system | grep -q trading-system; then
    echo "📦 Starting existing container..."
    docker start trading-system
else
    echo "⚠️ No existing container found. Please run deployment first."
fi
EOF

sudo chmod +x /usr/local/bin/trading-system-start.sh

# Final system cleanup and preparation
log "🧹 Final system cleanup..."
sudo apt-get autoremove -y
sudo apt-get autoclean

# Display summary
success "✅ GCP VM setup completed successfully!"
echo
echo "📋 Setup Summary:"
echo "  • System packages updated and optimized"
echo "  • Docker installed and configured"
echo "  • Google Cloud CLI installed"
echo "  • Application user created: $APP_USER"
echo "  • Application directory: $APP_DIR"
echo "  • Firewall configured (ports 22, 8050, 8080)"
echo "  • Monitoring and health check scripts installed"
echo "  • System optimizations applied"
echo "  • Backup system configured"
echo
echo "🔧 Useful Commands:"
echo "  • Health check: sudo /usr/local/bin/trading-system-health.sh"
echo "  • Monitor logs: sudo tail -f /var/log/trading-system/monitor.log"
echo "  • View container: docker ps -f name=trading-system"
echo "  • Container logs: docker logs trading-system"
echo "  • System status: systemctl status trading-system"
echo
echo "🌐 Service URLs (after deployment):"
echo "  • Dashboard: http://$(curl -s http://checkip.amazonaws.com/):8050"
echo "  • API: http://$(curl -s http://checkip.amazonaws.com/):8080"
echo
warning "⚠️ Please reboot the system to ensure all changes take effect:"
echo "sudo reboot"
