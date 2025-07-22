#!/bin/bash
# Production Script Runner with Enhanced Logging and Process Management
# Usage: ./production_runner.sh <script_name> [args]

set -e

SCRIPT_NAME=$1
SCRIPT_ARGS="${@:2}"
LOG_DIR="/app/logs/scripts"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
LOG_FILE="$LOG_DIR/${SCRIPT_NAME}_${TIMESTAMP}.log"
PID_DIR="/app/logs/pids"
PID_FILE="$PID_DIR/${SCRIPT_NAME}.pid"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() {
    echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1" | tee -a $LOG_FILE
}

print_error() {
    echo -e "${RED}[$(date '+%H:%M:%S')] ERROR:${NC} $1" | tee -a $LOG_FILE
}

print_warning() {
    echo -e "${YELLOW}[$(date '+%H:%M:%S')] WARN:${NC} $1" | tee -a $LOG_FILE
}

# Create necessary directories
mkdir -p $LOG_DIR
mkdir -p $PID_DIR

# Check if script is already running
if [ -f $PID_FILE ]; then
    EXISTING_PID=$(cat $PID_FILE)
    if ps -p $EXISTING_PID > /dev/null 2>&1; then
        print_warning "Script $SCRIPT_NAME is already running with PID $EXISTING_PID"
        print_warning "Use 'kill $EXISTING_PID' to stop it, or wait for completion"
        exit 1
    else
        print_warning "Removing stale PID file for $SCRIPT_NAME"
        rm -f $PID_FILE
    fi
fi

# Function to cleanup on exit
cleanup() {
    EXIT_CODE=$?
    if [ -f $PID_FILE ]; then
        rm -f $PID_FILE
    fi
    
    if [ $EXIT_CODE -eq 0 ]; then
        print_status "✅ Script $SCRIPT_NAME completed successfully"
    else
        print_error "❌ Script $SCRIPT_NAME failed with exit code $EXIT_CODE"
    fi
    
    print_status "📊 Execution Summary:"
    print_status "   Script: $SCRIPT_NAME"
    print_status "   Arguments: $SCRIPT_ARGS"
    print_status "   Log file: $LOG_FILE"
    print_status "   Duration: $(($(date +%s) - START_TIME)) seconds"
    
    exit $EXIT_CODE
}

# Set trap for cleanup
trap cleanup EXIT INT TERM

# Start execution
START_TIME=$(date +%s)
echo $$ > $PID_FILE

print_status "🚀 Production Script Runner"
print_status "================================"
print_status "Script: $SCRIPT_NAME"
print_status "Arguments: $SCRIPT_ARGS"
print_status "Log file: $LOG_FILE"
print_status "PID: $$"
print_status "Started: $(date)"
print_status "================================"

# Resource monitoring function
monitor_resources() {
    while true; do
        if [ -f $PID_FILE ]; then
            CPU_USAGE=$(ps -p $$ -o %cpu --no-headers | tr -d ' ')
            MEM_USAGE=$(ps -p $$ -o %mem --no-headers | tr -d ' ')
            echo "[$(date '+%H:%M:%S')] Resource Usage - CPU: ${CPU_USAGE}%, Memory: ${MEM_USAGE}%" >> $LOG_FILE
            sleep 60
        else
            break
        fi
    done &
}

# Start resource monitoring
monitor_resources

# Execute the script with full logging and error handling
{
    print_status "Executing: ./scripts/run_script.sh $SCRIPT_NAME $SCRIPT_ARGS"
    
    # Change to app directory
    cd /app
    
    # Execute the script
    ./scripts/run_script.sh $SCRIPT_NAME $SCRIPT_ARGS
    
} 2>&1 | tee -a $LOG_FILE

# Get exit code from the pipeline
EXIT_CODE=${PIPESTATUS[0]}

# Log completion
if [ $EXIT_CODE -eq 0 ]; then
    print_status "✅ Script execution successful"
else
    print_error "❌ Script execution failed with exit code $EXIT_CODE"
fi

exit $EXIT_CODE
