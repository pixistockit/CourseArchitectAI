import os
import logging
import csv
from datetime import datetime

class LoggerService:
    def __init__(self, base_data_path='data'):
        self.log_dir = os.path.join(base_data_path, 'logs')
        os.makedirs(self.log_dir, exist_ok=True)
        
        # System Log File (Global)
        self.system_log_file = os.path.join(self.log_dir, 'platform_system.log')
        
        # Setup Python Logger for System events
        self.sys_logger = logging.getLogger('platform_system')
        self.sys_logger.setLevel(logging.INFO)
        if not self.sys_logger.handlers:
            handler = logging.FileHandler(self.system_log_file)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.sys_logger.addHandler(handler)

    def log_system(self, level, message, ip=None):
        """Logs global system events (Stream A)."""
        msg = f"[{ip}] {message}" if ip else message
        if level.lower() == 'error':
            self.sys_logger.error(msg)
        elif level.lower() == 'warning':
            self.sys_logger.warning(msg)
        else:
            self.sys_logger.info(msg)

    def log_audit(self, report_id, level, message, agent='SYSTEM'):
        """Logs audit-specific events (Stream B) - Placeholder for file logging."""
        # In V3, this will write to /data/reports/{id}/logs/
        log_entry = f"[{level.upper()}] {agent}: {message}"
        self.sys_logger.info(f"[Audit {report_id}] {log_entry}")

    def get_recent_logs(self, limit=5):
        """
        Reads the tail of the system log file for the Dashboard.
        """
        logs = []
        if not os.path.exists(self.system_log_file):
            return []
            
        try:
            with open(self.system_log_file, 'r') as f:
                # Read all lines and take the last 'limit'
                lines = f.readlines()[-limit:]
                
            for line in reversed(lines): # Newest first
                # Parse standard log format: "2023-12-22 10:00:00,000 - INFO - Message"
                try:
                    parts = line.split(' - ', 2)
                    if len(parts) >= 3:
                        logs.append({
                            'timestamp': parts[0].split(',')[0],
                            'level': parts[1],
                            'message': parts[2].strip()
                        })
                    else:
                        # Fallback for unstructured lines
                        logs.append({'timestamp': '', 'level': 'INFO', 'message': line.strip()})
                except:
                    continue
        except Exception as e:
            print(f"Error reading logs: {e}")
            
        return logs
