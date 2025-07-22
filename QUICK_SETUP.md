# Quick CI/CD Setup Guide

This guide will help you set up an automated deployment to AWS EC2 in under 30 minutes.

## Prerequisites

1. **AWS EC2 Instance** (Ubuntu 22.04 LTS recommended)
2. **GitHub Repository** for this project
3. **SSH Key Pair** for EC2 access

## Step 1: Prepare Your EC2 Instance

### Launch EC2 Instance
- Choose Ubuntu 22.04 LTS AMI
- Instance type: t3.medium or larger (for InfluxDB + Redis)
- Security Group: Allow ports 22 (SSH), 80 (HTTP), 443 (HTTPS), 8086 (InfluxDB)
- Create or use existing key pair

### Connect to EC2 and Install Dependencies
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker ubuntu

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Create deployment directory
mkdir -p /opt/trading-system
sudo chown ubuntu:ubuntu /opt/trading-system

# Logout and login again for Docker group changes
exit
```

## Step 2: Configure GitHub Secrets

In your GitHub repository, go to Settings > Secrets and variables > Actions, then add:

### Required Secrets
```
EC2_HOST=your-ec2-public-ip
EC2_USER=ubuntu
EC2_SSH_KEY=your-private-ssh-key-content

# InfluxDB Configuration
INFLUXDB_ADMIN_TOKEN=your-secure-random-token
INFLUXDB_ADMIN_PASSWORD=your-secure-password
INFLUXDB_BUCKET=trading_data
INFLUXDB_ORG=trading_org

# CTrader Configuration
CTRADER_CLIENT_ID=your-ctrader-client-id
CTRADER_SECRET=your-ctrader-secret
CTRADER_ACCESS_TOKEN=your-access-token
CTRADER_REFRESH_TOKEN=your-refresh-token

# MT5 Configuration
MT5_LOGIN=your-mt5-login
MT5_PASSWORD=your-mt5-password
MT5_SERVER=your-mt5-server

# Additional Settings
REDIS_PASSWORD=your-redis-password
SECRET_KEY=your-flask-secret-key
```

### How to Generate Secure Tokens
```bash
# Generate random tokens
openssl rand -hex 32  # For INFLUXDB_ADMIN_TOKEN
openssl rand -hex 24  # For REDIS_PASSWORD
openssl rand -hex 32  # For SECRET_KEY
```

## Step 3: SSH Key Setup

### Generate SSH Key (if you don't have one)
```bash
ssh-keygen -t rsa -b 4096 -C "your-email@example.com"
```

### Copy Public Key to EC2
```bash
ssh-copy-id -i ~/.ssh/id_rsa.pub ubuntu@your-ec2-ip
```

### Copy Private Key to GitHub Secret
```bash
cat ~/.ssh/id_rsa  # Copy this entire content to EC2_SSH_KEY secret
```

## Step 4: Deploy

1. **Push to Main Branch**: Any push to the `main` branch will trigger deployment
2. **Monitor Deployment**: Check the Actions tab in GitHub for deployment progress
3. **Check Status**: SSH to your EC2 and run:
   ```bash
   cd /opt/trading-system
   docker-compose ps
   docker-compose logs
   ```

## Step 5: Verify Deployment

### Check Services
```bash
# Check all services are running
docker-compose ps

# Check logs
docker-compose logs trading-system
docker-compose logs influxdb
docker-compose logs redis

# Check health
curl http://localhost:5000/health  # Trading system
curl http://localhost:8086/health  # InfluxDB
```

### Access Your Application
- Trading Dashboard: `http://your-ec2-ip:5000`
- InfluxDB UI: `http://your-ec2-ip:8086`

## Troubleshooting

### Common Issues

1. **Docker Permission Denied**
   ```bash
   sudo usermod -aG docker ubuntu
   # Logout and login again
   ```

2. **Port Already in Use**
   ```bash
   sudo netstat -tulpn | grep :5000
   sudo kill -9 <process-id>
   ```

3. **InfluxDB Connection Issues**
   ```bash
   docker-compose logs influxdb
   # Check if INFLUXDB_ADMIN_TOKEN is set correctly
   ```

4. **SSH Connection Issues**
   - Verify EC2 security group allows SSH (port 22)
   - Check SSH key format in GitHub secrets
   - Ensure EC2_HOST is the public IP

### Logs and Debugging
```bash
# View detailed logs
docker-compose logs -f trading-system

# Check container status
docker ps -a

# Restart services
docker-compose restart trading-system

# Full restart
docker-compose down && docker-compose up -d
```

## Security Notes

1. **Change default passwords** after first deployment
2. **Use environment-specific secrets** for production
3. **Enable EC2 security groups** to restrict access
4. **Regular updates**: The CI/CD will handle application updates, but update system packages regularly

## Monitoring

### Check System Resources
```bash
# Disk usage
df -h

# Memory usage
free -h

# CPU usage
top

# Docker stats
docker stats
```

### Application Health
The trading system includes health checks at `/health` endpoint that monitors:
- Database connectivity
- Redis connectivity
- System resources
- Service status

## Next Steps

1. **SSL/HTTPS**: Set up Let's Encrypt for secure connections
2. **Domain**: Configure a domain name instead of IP
3. **Monitoring**: Add Prometheus + Grafana for detailed monitoring
4. **Backups**: Set up automated InfluxDB backups
5. **Scaling**: Consider auto-scaling groups for high availability

Your CI/CD pipeline is now ready! Every push to `main` will automatically deploy your latest changes to EC2.
