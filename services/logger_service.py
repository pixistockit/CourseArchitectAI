import os
import logging
import shutil
import time
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime

class LoggerService:
    """
    A centralized logging service for the AuditSlide SaaS Platform.
    Manages two distinct log streams with separate retention policies:
    
    1. Stream A (System): /data/logs/platform_system.log
       - Tracks: Global errors, Auth, API usage.
       - Retention: 30 Days (Managed by TimedRotatingFileHandler).
       
    2. Stream B (User/Audit): /data/reports/{id}/logs/audit_flight_recorder.log
       - Tracks: specific session logic, AI reasoning.
       - Retention: 7 Days (Managed by cleanup_user_logs on startup).
       - Privacy: Content inputs for Agent 1 & 3 are truncated.
    """
    def __init__(self, base_data_path='data'):
        self.system_log_dir = os.path.join(base_data_path, 'logs')
        self.audit_log_base_dir = os.path.join(base_data_path, 'reports')
        os.makedirs(self.system_log_dir, exist_ok=True)
        
        # Initialize System Stream
        self.system_logger = self._setup_system_logger()
        
        # Run Startup Cleanup Task (Enforce 7-day retention for user logs)
        self.cleanup_user_logs(retention_days=7)

    def _setup_system_logger(self):
        """Initializes the Stream A system logger with 30-day rotation."""
        logger = logging.getLogger('platform_system')
        logger.setLevel(logging.INFO)

        if logger.hasHandlers():
            logger.handlers.clear()

        log_file = os.path.join(self.system_log_dir, 'platform_system.log')
        
        # RETENTION POLICY: Keep 30 days of system logs
        handler = TimedRotatingFileHandler(log_file, when="midnight", interval=1, backupCount=30)
        
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        return logger

    def _get_audit_logger(self, report_id):
        """Initializes Stream B logger for a specific session."""
        report_log_dir = os.path.join(self.audit_log_base_dir, str(report_id), 'logs')
        os.makedirs(report_log_dir, exist_ok=True)
        
        logger_name = f'audit_{report_id}'
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG) 

        if not logger.handlers:
            log_file = os.path.join(report_log_dir, 'audit_flight_recorder.log')
            handler = logging.FileHandler(log_file, mode='a')
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - AGENT: %(agent)s - %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        
        return logger

    def _sanitize_message(self, agent, message):
        """
        PRIVACY POLICY:
        - Agent 1 (Parser) & Agent 3 (Generator) deal with raw user content. Truncate to protect IP.
        - Agent 2 (Researcher) deals with logic/vectors. Keep full for debugging.
        """
        agent_upper = str(agent).upper()
        
        # Identify Sensitive Agents
        is_sensitive = "AGENT_1" in agent_upper or "AGENT_3" in agent_upper
        
        if is_sensitive and len(message) > 500:
            return f"{message[:500]}... [TRUNCATED FOR PRIVACY - {len(message)-500} chars removed]"
            
        return message

    def cleanup_user_logs(self, retention_days=7):
        """
        Maintenance Task: Scans report folders and deletes LOG directories older than 7 days.
        Does NOT delete the report itself (JSON/HTML), only the heavy log files.
        """
        now = time.time()
        cutoff = now - (retention_days * 86400) # seconds in a day
        deleted_count = 0
        
        if not os.path.exists(self.audit_log_base_dir):
            return

        try:
            for report_id in os.listdir(self.audit_log_base_dir):
                report_path = os.path.join(self.audit_log_base_dir, report_id)
                log_dir = os.path.join(report_path, 'logs')
                
                # Check if the 'logs' folder exists and is older than retention period
                if os.path.exists(log_dir) and os.path.isdir(log_dir):
                    dir_mtime = os.path.getmtime(log_dir)
                    if dir_mtime < cutoff:
                        shutil.rmtree(log_dir) # Delete only the logs folder
                        deleted_count += 1
                        
            if deleted_count > 0:
                self.log_system('INFO', f"Startup Cleanup: Removed {deleted_count} expired user log directories (> {retention_days} days).")
                
        except Exception as e:
            # Log to system stream if cleanup fails
            self.log_system('ERROR', f"Log retention cleanup failed: {e}")

    def log_system(self, level, message, ip=None):
        """Logs to /data/logs/platform_system.log"""
        msg = f"IP: {ip} - {message}" if ip else message
        log_func = getattr(self.system_logger, level.lower(), self.system_logger.info)
        log_func(msg)

    def log_audit(self, report_id, level, message, agent='SYSTEM'):
        """Logs to /data/reports/{id}/logs/audit_flight_recorder.log"""
        try:
            audit_logger = self._get_audit_logger(report_id)
            
            # Apply Privacy Truncation
            clean_message = self._sanitize_message(agent, message)
            
            extra_info = {'agent': agent}
            log_func = getattr(audit_logger, level.lower(), audit_logger.debug)
            log_func(clean_message, extra=extra_info)
        except Exception as e:
            # Fallback to system log if audit logging fails
            self.log_system('ERROR', f"Failed to write audit log for {report_id}: {e}")

    def get_recent_logs(self, limit=5):
        """Reads the tail of the system log file for the Dashboard."""
        logs = []
        log_file = os.path.join(self.system_log_dir, 'platform_system.log')
        if not os.path.exists(log_file):
            return []
            
        try:
            with open(log_file, 'r') as f:
                lines = f.readlines()[-limit:]
                
            for line in reversed(lines):
                try:
                    parts = line.split(' - ', 2)
                    if len(parts) >= 3:
                        logs.append({
                            'timestamp': parts[0].split(',')[0],
                            'level': parts[1],
                            'message': parts[2].strip()
                        })
                    else:
                        logs.append({'timestamp': '', 'level': 'INFO', 'message': line.strip()})
                except:
                    continue
        except Exception:
            pass
        return logs
