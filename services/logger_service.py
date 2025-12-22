# /services/logger_service.py

import os
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime

class LoggerService:
    """
    A centralized logging service for the AuditSlide SaaS Platform.
    Manages two distinct log streams:
    - Stream A (System): For global, platform-level events (e.g., auth, errors).
    - Stream B (Audit): For specific, user-session events (the "flight recorder").
    """
    def __init__(self, base_data_path='data'):
        self.system_log_dir = os.path.join(base_data_path, 'logs')
        self.audit_log_base_dir = os.path.join(base_data_path, 'reports')
        os.makedirs(self.system_log_dir, exist_ok=True)
        
        self.system_logger = self._setup_system_logger()

    def _setup_system_logger(self):
        """Initializes the Stream A system logger."""
        logger = logging.getLogger('platform_system')
        logger.setLevel(logging.INFO)

        if logger.hasHandlers():
            logger.handlers.clear()

        log_file = os.path.join(self.system_log_dir, 'platform_system.log')
        # Use TimedRotatingFileHandler for automatic rotation and cleanup
        handler = TimedRotatingFileHandler(log_file, when="midnight", interval=1, backupCount=30)
        # Add user and IP to the format for better system tracking
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - User: %(user)s - IP: %(ip)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        return logger

    def _get_audit_logger(self, report_id):
        """Initializes or gets a Stream B audit logger for a specific report_id."""
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
        """Truncates messages from specific agents to protect user IP."""
        if agent in ["AGENT_1_PARSER", "AGENT_3_GENERATOR"] and len(message) > 250:
            return f"{message[:250]}... [TRUNCATED FOR PRIVACY]"
        return message

    def log_system(self, level, message, user='SYSTEM', ip='N/A'):
        """
        Logs a message to the global system log (Stream A).
        """
        # The standard logging library doesn't directly support passing extra fields to the formatter
        # A common workaround is to use a LogRecord factory or filter, but for simplicity, we format here.
        formatted_message = f"User: {user} - IP: {ip} - {message}"
        log_func = getattr(self.system_logger, level.lower(), self.system_logger.info)
        log_func(formatted_message)


    def log_audit(self, report_id, level, message, agent='GENERAL'):
        """
        Logs a message to a specific audit's flight recorder (Stream B).
        """
        audit_logger = self._get_audit_logger(report_id)
        sanitized_message = self._sanitize_message(agent, message)
        
        # Similar to system logger, format message to include agent
        formatted_message = f"AGENT: {agent} - {sanitized_message}"
        log_func = getattr(audit_logger, level.lower(), audit_logger.debug)
        log_func(formatted_message)

