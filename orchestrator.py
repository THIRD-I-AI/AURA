#!/usr/bin/env python3
"""
AURA Orchestrator - Start all backend services in parallel
Manages lifecycle of all microservices with unified logging
"""

import subprocess
import sys
import os
import signal
import time
from pathlib import Path
from typing import List, Dict

# Service definitions with module import paths
SERVICES = [
    {
        "name": "API Gateway",
        "port": 8000,
        "module": "aurabackend.api_gateway.main:app",
        "timeout": 5,
    },
    {
        "name": "Code Generation Service",
        "port": 8001,
        "module": "aurabackend.code_generation_service.main:code_gen_app",
        "timeout": 5,
    },
    {
        "name": "Execution Service",
        "port": 8003,
        "module": "aurabackend.execution_sandbox.main:execution_app",
        "timeout": 5,
    },
    {
        "name": "Scheduler Service",
        "port": 8004,
        "module": "aurabackend.scheduler_service.main:scheduler_app",
        "timeout": 5,
    },
    {
        "name": "Orchestration Service",
        "port": 8006,
        "module": "aurabackend.orchestration_service.main:app",
        "timeout": 5,
    },
    {
        "name": "Connector Service",
        "port": 8002,
        "module": "aurabackend.connectors.main:app",
        "timeout": 5,
    },
    {
        "name": "Insights Service",
        "port": 8005,
        "module": "aurabackend.insights.main:app",
        "timeout": 5,
    },
]

# Color codes for terminal output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    CYAN = '\033[96m'


class ServiceOrchestrator:
    def __init__(self):
        self.processes: Dict[str, subprocess.Popen] = {}
        self.project_root = Path(__file__).parent
        # Setup PYTHONPATH to include project root for imports
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = str(self.project_root)

    def log(self, service_name: str, message: str, level: str = "INFO"):
        """Log message with service prefix"""
        timestamp = time.strftime("%HH:%MM:%SS")
        
        if level == "ERROR":
            color = Colors.FAIL
        elif level == "SUCCESS":
            color = Colors.OKGREEN
        elif level == "WARNING":
            color = Colors.WARNING
        else:
            color = Colors.OKBLUE
        
        print(f"{color}[{timestamp}] [{service_name:20}] {message}{Colors.ENDC}")

    def start_service(self, service: Dict) -> bool:
        """Start a single service using uvicorn with port verification"""
        import socket
        
        name = service["name"]
        module_app = service["module"]
        port = service["port"]
        timeout = service.get("timeout", 5)
        
        try:
            self.log(name, f"Starting on port {port}...", "INFO")
            
            # Use uvicorn to run the FastAPI app directly from module
            command = [
                sys.executable,
                "-m",
                "uvicorn",
                module_app,
                "--host", "127.0.0.1",
                "--port", str(port),
            ]
            
            # Start process with PYTHONPATH set correctly
            process = subprocess.Popen(
                command,
                cwd=str(self.project_root),
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
            )
            
            self.processes[name] = process
            
            # Check if process started and port is active
            start_time = time.time()
            port_available = False
            
            while time.time() - start_time < timeout:
                # Check if process is still running
                if process.poll() is not None:
                    stdout_data, _ = process.communicate()
                    if stdout_data:
                        self.log(name, f"Start failed: {stdout_data[:200]}", "ERROR")
                    else:
                        self.log(name, f"Failed to start (exited immediately)", "ERROR")
                    return False
                
                # Check if port is listening
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(0.5)
                    result = sock.connect_ex(('127.0.0.1', port))
                    sock.close()
                    
                    if result == 0:
                        port_available = True
                        break
                except Exception:
                    pass
                
                time.sleep(0.2)
            
            if port_available:
                self.log(name, f"Started (PID: {process.pid})", "SUCCESS")
                return True
            else:
                self.log(name, f"Failed to start - port {port} not available after {timeout}s", "ERROR")
                process.terminate()
                return False
                
        except Exception as e:
            self.log(name, f"Start failed: {str(e)}", "ERROR")
            return False

    def monitor_services(self):
        """Monitor running services and stream their output"""
        import select
        
        if not self.processes:
            return
        
        self.log("Orchestrator", "Monitoring all services (press Ctrl+C to stop)", "INFO")
        
        try:
            while True:
                # Check if any process has died
                dead = []
                for name, process in self.processes.items():
                    if process.poll() is not None:
                        self.log(name, f"Process died (exit code: {process.poll()})", "ERROR")
                        dead.append(name)
                
                # Remove dead processes
                for name in dead:
                    del self.processes[name]
                
                if not self.processes:
                    self.log("Orchestrator", "All services have stopped", "ERROR")
                    break
                
                # Brief sleep to avoid busy-waiting
                time.sleep(1)
                
        except KeyboardInterrupt:
            # User pressed Ctrl+C - stop all services gracefully
            self.log("Orchestrator", "Received interrupt signal", "WARNING")
            self.stop_all()
            raise  # Re-raise to let main() handle it

    def start_all(self):
        """Start all services"""
        print(f"\n{Colors.HEADER}{Colors.BOLD}")
        print("=" * 80)
        print("AURA Backend Orchestrator".center(80))
        print("=" * 80)
        print(f"{Colors.ENDC}\n")
        
        self.log("Orchestrator", f"Project root: {self.project_root}", "INFO")
        self.log("Orchestrator", f"Starting {len(SERVICES)} services...", "INFO")
        print()
        
        success_count = 0
        for service in SERVICES:
            if self.start_service(service):
                success_count += 1
        
        print()
        self.log(
            "Orchestrator",
            f"Started {success_count}/{len(SERVICES)} services",
            "SUCCESS" if success_count == len(SERVICES) else "WARNING",
        )
        
        if success_count == len(SERVICES):
            print(f"\n{Colors.OKGREEN}{Colors.BOLD}")
            print("[OK] All services started successfully!")
            print(f"{Colors.ENDC}\n")
            print(f"{Colors.CYAN}Backend available at: http://localhost:8000{Colors.ENDC}")
            print(f"{Colors.CYAN}Frontend should connect via: /api (Vite proxy){Colors.ENDC}\n")
        else:
            print(f"\n{Colors.FAIL}[FAILED] Some services failed to start!{Colors.ENDC}\n")
            return False
        
        # Monitor services
        self.monitor_services()
        return True

    def stop_all(self):
        """Stop all running services gracefully"""
        if not self.processes:
            return
        
        print(f"\n{Colors.WARNING}Shutting down services...{Colors.ENDC}\n")
        
        # Try graceful shutdown first
        for name, process in self.processes.items():
            if process.poll() is None:
                self.log(name, "Sending SIGTERM...", "WARNING")
                process.terminate()
        
        # Wait for graceful shutdown
        time.sleep(2)
        
        # Force kill if still running
        for name, process in self.processes.items():
            if process.poll() is None:
                self.log(name, "Sending SIGKILL...", "ERROR")
                process.kill()
        
        # Wait for all to die
        for process in self.processes.values():
            process.wait()
        
        self.log("Orchestrator", "All services stopped", "SUCCESS")

    def handle_signal(self, signum, frame):
        """Handle Ctrl+C signal"""
        self.stop_all()
        sys.exit(0)


def main():
    """Main entry point"""
    orchestrator = ServiceOrchestrator()
    
    # Register signal handlers
    signal.signal(signal.SIGINT, orchestrator.handle_signal)
    signal.signal(signal.SIGTERM, orchestrator.handle_signal)
    
    try:
        success = orchestrator.start_all()
        if not success:
            sys.exit(1)
        
        # Keep orchestrator alive - monitor_services() runs the infinite loop
        # If we reach here, all services have stopped or user pressed Ctrl+C
        
    except KeyboardInterrupt:
        print(f"\n{Colors.WARNING}[Orchestrator] Received interrupt signal{Colors.ENDC}")
        orchestrator.stop_all()
        sys.exit(0)
    except Exception as e:
        print(f"{Colors.FAIL}Fatal error: {str(e)}{Colors.ENDC}")
        orchestrator.stop_all()
        sys.exit(1)


if __name__ == "__main__":
    main()
