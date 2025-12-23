import re
import textstat
from .config import TARGET_READING_GRADE_LEVEL, WEASEL_WORDS, CLIENT_BLACKLIST

class ClarityEngine:
    def __init__(self):
        pass

    def check_reading_level(self, text, context="slide"):
        """
        Checks Flesch-Kincaid Grade Level against the Configured Target.
        """
        # Skip short text (headers, bullets) to avoid skewing data
        if len(text.split()) < 7: 
            return None
        
        try:
            grade = textstat.flesch_kincaid_grade(text)
        except:
            return None # Fail gracefully on weird text/symbols

        # SLIDE CONTEXT (Strict Enforcement of the Ceiling)
        if context == "slide":
            if grade > TARGET_READING_GRADE_LEVEL:
                return {
                    "result": "WARNING",
                    "details": f"Reading Level High: Grade {grade} (Target: < {TARGET_READING_GRADE_LEVEL}). Simplify sentence structure."
                }
        
        # NOTES CONTEXT (Lenient - Instructors are experts, but don't go crazy)
        elif context == "notes":
            # We allow notes to be higher than slides, but usually capped at College Level (14-16)
            # to ensure the instructor can actually speak it out loud fluently.
            instructor_cap = max(TARGET_READING_GRADE_LEVEL + 4, 14) 
            
            if grade > instructor_cap:
                return {
                    "result": "INFO",
                    "details": f"Note Complexity: Grade {grade}. Ensure this is easy to read aloud."
                }
        return None

    def check_passive_voice(self, text, context="slide"):
        """
        Passive voice kills engagement. 
        - Slides: WARNING (Learners need direct instructions).
        - Notes: INFO (Instructors might speak passively, but active is better).
        """
        # Regex: "to be" verb + word ending in "ed" (heuristic)
        passive_pattern = r"\b(am|is|are|was|were|be|been|being)\s+\w+ed\b"
        matches = re.findall(passive_pattern, text.lower())
        
        if matches:
            severity = "WARNING" if context == "slide" else "INFO"
            return {
                "result": severity,
                "details": "Passive Voice detected. Use Active Voice for stronger authority."
            }
        return None

    def check_bad_habits(self, text):
        """
        Checks for Weasel Words and Blacklisted Jargon.
        """
        text_lower = text.lower()
        issues = []
        
        # 1. Check Weasel Words (Weak Authority)
        for word in WEASEL_WORDS:
            # Check for word with spaces around it to avoid partial matches
            if re.search(r'\b' + re.escape(word) + r'\b', text_lower):
                issues.append(f"Avoid '{word}'")
        
        # 2. Check Client Blacklist (Jargon)
        for complex_w, simple_w in CLIENT_BLACKLIST.items():
            if re.search(r'\b' + re.escape(complex_w) + r'\b', text_lower):
                issues.append(f"Replace '{complex_w}' with '{simple_w}'")
                
        if issues:
            # Limit to 3 to keep report clean
            details = "Style Suggestions: " + ", ".join(issues[:3])
            return {
                "result": "WARNING",
                "details": details
            }
        return None