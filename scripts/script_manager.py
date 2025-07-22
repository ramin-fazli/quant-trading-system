#!/usr/bin/env python3
"""
Web-based Script Manager for Trading System
Provides REST API for script execution and monitoring
"""

import os
import sys
import json
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Configuration
SCRIPT_DIR = "/app/scripts"
LOG_DIR = "/app/logs/scripts"
PID_DIR = "/app/logs/pids"

# Ensure directories exist
Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
Path(PID_DIR).mkdir(parents=True, exist_ok=True)

# Available scripts configuration
AVAILABLE_SCRIPTS = {
    "main": {
        "name": "Main Trading System",
        "description": "Run the main pairs trading system",
        "icon": "🚀",
        "examples": [
            "--mode live --data-provider ctrader --broker ctrader",
            "--mode backtest --data-provider ctrader --broker ctrader"
        ]
    },
    "backtest": {
        "name": "Backtest Engine",
        "description": "Run backtesting analysis",
        "icon": "📈",
        "examples": [
            "--symbol EURUSD --period 30",
            "--start-date 2024-01-01 --end-date 2024-12-31"
        ]
    },
    "dashboard": {
        "name": "Web Dashboard",
        "description": "Start the trading dashboard",
        "icon": "📊",
        "examples": [
            "--port 8050",
            "--debug"
        ]
    },
    "data-collection": {
        "name": "Data Collection",
        "description": "Collect market data",
        "icon": "💾",
        "examples": [
            "--provider ctrader --symbols EURUSD,GBPUSD",
            "--historical --days 30"
        ]
    },
    "portfolio-analysis": {
        "name": "Portfolio Analysis",
        "description": "Analyze portfolio performance",
        "icon": "📋",
        "examples": [
            "--report-type summary",
            "--export-format pdf"
        ]
    },
    "health-check": {
        "name": "System Health Check",
        "description": "Check system health and diagnostics",
        "icon": "🔧",
        "examples": [
            "",
            "--verbose"
        ]
    }
}

# Store running processes
running_processes = {}

def get_script_status(script_name):
    """Get the status of a script"""
    pid_file = Path(PID_DIR) / f"{script_name}.pid"
    
    if pid_file.exists():
        try:
            with open(pid_file, 'r') as f:
                pid = int(f.read().strip())
            
            # Check if process is still running
            try:
                os.kill(pid, 0)  # Send signal 0 to check if process exists
                return {"status": "running", "pid": pid}
            except OSError:
                # Process doesn't exist, remove stale PID file
                pid_file.unlink()
                return {"status": "stopped", "pid": None}
        except (ValueError, IOError):
            return {"status": "error", "pid": None}
    
    return {"status": "stopped", "pid": None}

def get_script_logs(script_name, lines=50):
    """Get recent logs for a script"""
    log_files = list(Path(LOG_DIR).glob(f"{script_name}_*.log"))
    
    if not log_files:
        return []
    
    # Get the most recent log file
    latest_log = max(log_files, key=lambda f: f.stat().st_mtime)
    
    try:
        with open(latest_log, 'r') as f:
            all_lines = f.readlines()
            return all_lines[-lines:] if len(all_lines) > lines else all_lines
    except IOError:
        return []

@app.route('/')
def dashboard():
    """Main dashboard"""
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Trading System Script Manager</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
            .container { max-width: 1200px; margin: 0 auto; }
            .header { background: #2c3e50; color: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; }
            .script-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
            .script-card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .script-title { font-size: 1.2em; font-weight: bold; margin-bottom: 10px; }
            .script-status { padding: 5px 10px; border-radius: 4px; font-size: 0.9em; margin-bottom: 10px; }
            .status-running { background: #d4edda; color: #155724; }
            .status-stopped { background: #f8d7da; color: #721c24; }
            .btn { padding: 10px 15px; border: none; border-radius: 4px; cursor: pointer; margin: 5px; }
            .btn-primary { background: #007bff; color: white; }
            .btn-danger { background: #dc3545; color: white; }
            .btn-info { background: #17a2b8; color: white; }
            .logs { background: #f8f9fa; padding: 10px; border-radius: 4px; font-family: monospace; font-size: 0.8em; max-height: 200px; overflow-y: auto; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🚀 Trading System Script Manager</h1>
                <p>Manage and monitor your trading system scripts</p>
            </div>
            
            <div class="script-grid" id="scriptGrid">
                <!-- Scripts will be loaded here -->
            </div>
        </div>
        
        <script>
            async function loadScripts() {
                try {
                    const response = await fetch('/api/scripts');
                    const data = await response.json();
                    
                    const grid = document.getElementById('scriptGrid');
                    grid.innerHTML = '';
                    
                    for (const [scriptName, scriptInfo] of Object.entries(data.scripts)) {
                        const status = data.status[scriptName];
                        const card = createScriptCard(scriptName, scriptInfo, status);
                        grid.appendChild(card);
                    }
                } catch (error) {
                    console.error('Error loading scripts:', error);
                }
            }
            
            function createScriptCard(scriptName, scriptInfo, status) {
                const card = document.createElement('div');
                card.className = 'script-card';
                
                const statusClass = status.status === 'running' ? 'status-running' : 'status-stopped';
                const statusText = status.status === 'running' ? `Running (PID: ${status.pid})` : 'Stopped';
                
                card.innerHTML = `
                    <div class="script-title">${scriptInfo.icon} ${scriptInfo.name}</div>
                    <div class="script-status ${statusClass}">${statusText}</div>
                    <p>${scriptInfo.description}</p>
                    <div>
                        <button class="btn btn-primary" onclick="runScript('${scriptName}')">Run</button>
                        <button class="btn btn-danger" onclick="stopScript('${scriptName}')" ${status.status !== 'running' ? 'disabled' : ''}>Stop</button>
                        <button class="btn btn-info" onclick="showLogs('${scriptName}')">Logs</button>
                    </div>
                    <div id="logs-${scriptName}" class="logs" style="display: none;"></div>
                `;
                
                return card;
            }
            
            async function runScript(scriptName) {
                const args = prompt(`Enter arguments for ${scriptName} (or leave empty):`);
                if (args === null) return;
                
                try {
                    const response = await fetch(`/api/run/${scriptName}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ args: args })
                    });
                    const result = await response.json();
                    alert(result.message || 'Script started');
                    loadScripts();
                } catch (error) {
                    alert('Error starting script: ' + error.message);
                }
            }
            
            async function stopScript(scriptName) {
                if (!confirm(`Stop ${scriptName}?`)) return;
                
                try {
                    const response = await fetch(`/api/stop/${scriptName}`, { method: 'POST' });
                    const result = await response.json();
                    alert(result.message || 'Script stopped');
                    loadScripts();
                } catch (error) {
                    alert('Error stopping script: ' + error.message);
                }
            }
            
            async function showLogs(scriptName) {
                const logsDiv = document.getElementById(`logs-${scriptName}`);
                
                if (logsDiv.style.display === 'none') {
                    try {
                        const response = await fetch(`/api/logs/${scriptName}`);
                        const data = await response.json();
                        logsDiv.innerHTML = data.logs.join('') || 'No logs available';
                        logsDiv.style.display = 'block';
                    } catch (error) {
                        logsDiv.innerHTML = 'Error loading logs';
                        logsDiv.style.display = 'block';
                    }
                } else {
                    logsDiv.style.display = 'none';
                }
            }
            
            // Auto-refresh every 30 seconds
            setInterval(loadScripts, 30000);
            
            // Initial load
            loadScripts();
        </script>
    </body>
    </html>
    """
    return render_template_string(html_template)

@app.route('/api/scripts')
def api_scripts():
    """Get available scripts and their status"""
    status = {}
    for script_name in AVAILABLE_SCRIPTS.keys():
        status[script_name] = get_script_status(script_name)
    
    return jsonify({
        "scripts": AVAILABLE_SCRIPTS,
        "status": status
    })

@app.route('/api/run/<script_name>', methods=['POST'])
def api_run_script(script_name):
    """Run a script"""
    if script_name not in AVAILABLE_SCRIPTS:
        return jsonify({"error": "Unknown script"}), 400
    
    # Check if already running
    status = get_script_status(script_name)
    if status["status"] == "running":
        return jsonify({"error": f"Script {script_name} is already running"}), 400
    
    data = request.get_json() or {}
    args = data.get('args', '')
    
    try:
        # Run script in background
        cmd = ['/app/scripts/production_runner.sh', script_name] + args.split() if args else ['/app/scripts/production_runner.sh', script_name]
        
        process = subprocess.Popen(
            cmd,
            cwd='/app',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid
        )
        
        running_processes[script_name] = process
        
        return jsonify({
            "message": f"Script {script_name} started successfully",
            "script": script_name,
            "args": args,
            "pid": process.pid
        })
    
    except Exception as e:
        logger.error(f"Error starting script {script_name}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/stop/<script_name>', methods=['POST'])
def api_stop_script(script_name):
    """Stop a running script"""
    if script_name not in AVAILABLE_SCRIPTS:
        return jsonify({"error": "Unknown script"}), 400
    
    status = get_script_status(script_name)
    if status["status"] != "running":
        return jsonify({"error": f"Script {script_name} is not running"}), 400
    
    try:
        pid = status["pid"]
        os.kill(pid, 15)  # SIGTERM
        
        # Wait a bit, then force kill if necessary
        time.sleep(2)
        try:
            os.kill(pid, 0)  # Check if still running
            os.kill(pid, 9)  # SIGKILL
        except OSError:
            pass  # Process already terminated
        
        return jsonify({"message": f"Script {script_name} stopped successfully"})
    
    except Exception as e:
        logger.error(f"Error stopping script {script_name}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/logs/<script_name>')
def api_get_logs(script_name):
    """Get logs for a script"""
    if script_name not in AVAILABLE_SCRIPTS:
        return jsonify({"error": "Unknown script"}), 400
    
    lines = request.args.get('lines', 50, type=int)
    logs = get_script_logs(script_name, lines)
    
    return jsonify({
        "script": script_name,
        "logs": logs,
        "lines": len(logs)
    })

@app.route('/api/status')
def api_system_status():
    """Get overall system status"""
    status = {}
    for script_name in AVAILABLE_SCRIPTS.keys():
        status[script_name] = get_script_status(script_name)
    
    return jsonify({
        "timestamp": datetime.now().isoformat(),
        "scripts": status,
        "system": {
            "log_dir": LOG_DIR,
            "pid_dir": PID_DIR,
            "script_dir": SCRIPT_DIR
        }
    })

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "script-manager"
    })

if __name__ == '__main__':
    logger.info("🌐 Starting Script Manager Web API...")
    logger.info(f"Dashboard: http://localhost:9000")
    logger.info(f"API: http://localhost:9000/api/scripts")
    
    app.run(
        host='0.0.0.0',
        port=9000,
        debug=os.getenv('ENVIRONMENT') != 'production'
    )
