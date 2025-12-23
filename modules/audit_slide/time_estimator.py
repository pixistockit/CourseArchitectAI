# audit_slide/time_estimator.py
import re
from .config import WPM_READING_SPEED, BUFFER_STANDARD_SLIDE, BUFFER_ACTIVITY_SLIDE

class TimeEstimator:
    def __init__(self):
        self.header_pattern = r"(?i)(?:Instructional )?(?:Time|Duration|Est\.? Time):\s*(.+)"
        self.time_parsers = [
            (r'(?:(\d+(?:\.\d+)?)\s*(?:hours?|hrs?))', 60), 
            (r'(?:(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|m\b))', 1),
            (r'(?:(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s\b))', 1/60),
            (r'(\d+):(\d{2})', "colon_format")
        ]

    def extract_explicit_time(self, notes_text):
        """Looks for 'Time: X' header and parses it into minutes."""
        if not notes_text: return 0
        match = re.search(self.header_pattern, notes_text)
        if match:
            raw_time = match.group(1).lower()
            return self._parse_duration_string(raw_time)
        return 0

    def calculate_implicit_time(self, notes_text, is_activity=False):
        """
        Calculates time based on Word Count + Contextual Buffer.
        is_activity=True adds the 5-minute facilitation buffer.
        """
        buffer_time = BUFFER_ACTIVITY_SLIDE if is_activity else BUFFER_STANDARD_SLIDE
        
        if not notes_text: 
            return buffer_time
            
        word_count = len(notes_text.split())
        reading_time = word_count / WPM_READING_SPEED
        return round(reading_time + buffer_time, 1)

    def _parse_duration_string(self, time_str):
        total_minutes = 0.0
        colon_match = re.search(r'(\d+):(\d{2})', time_str)
        if colon_match:
            parts = [int(p) for p in colon_match.groups()]
            return parts[0] + (parts[1] / 60)

        for pattern, multiplier in self.time_parsers:
            if multiplier == "colon_format": continue
            matches = re.findall(pattern, time_str)
            for val in matches:
                try: total_minutes += float(val) * multiplier
                except: pass
                
        return round(total_minutes, 1)