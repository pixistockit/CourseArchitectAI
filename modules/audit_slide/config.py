import os
import json

# --- CONFIG LOADER ---
# Attempts to load user settings from data/config/brand_config.json
# This allows the Dashboard UI to override the defaults below.
USER_CONFIG = {}
try:
    config_path = os.path.join(os.path.dirname(__file__), '../data/config/brand_config.json')
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            USER_CONFIG = json.load(f)
except: pass

# --- 1. SHAPE EXEMPTIONS ---
EXEMPT_SHAPE_NAMES = ["block", "cover", "mask", "clicktrigger"]

# --- 2. FONT RULES ---

# Title Rules
TITLE_FONT_NAME = USER_CONFIG.get('title_font', "Rockwell") # Fixer Target
TITLE_FONT_SIZE_MIN = 36
TITLE_FONT_SIZE_MAX = 60
TITLE_MUST_BE_BOLD = True

# Body Rules
BODY_FONT_NAME = USER_CONFIG.get('body_font', "Calibri") # Fixer Target (The "Safe" font)
BODY_FONT_SIZE_MIN = 25

# Allowed Fonts (Analyzer Rules)
# If user provided a list in UI, use it. Otherwise default to the list.
_user_allowed = USER_CONFIG.get('allowed_fonts', [])
if _user_allowed:
    # Ensure BODY_FONT_NAME is in the allowed list so we don't flag our own fix
    if BODY_FONT_NAME not in _user_allowed:
        _user_allowed.append(BODY_FONT_NAME)
    ALLOWED_BODY_FONTS = _user_allowed
else:
    # Default Fallback
    ALLOWED_BODY_FONTS = ["Calibri", "MV Boli", "Arial"]

# Notes Rules
NOTES_FONT_NAME = USER_CONFIG.get('notes_font', "Calibri")
NOTES_FONT_SIZE_MIN = 11
NOTES_FONT_SIZE_MAX = 12
NOTES_FONT_COLOR_RGB = (0, 0, 0) # Black

# --- 3. THEME FONT RESOLUTION ---
THEME_FONT_MAPPING = {
    "+mj-lt": TITLE_FONT_NAME,
    "+mn-lt": BODY_FONT_NAME
}

# --- 4. BRAND COLOR PALETTE ---
# (Reference Only - Analyzer)
BRAND_COLORS_RGB = [
    (0, 0, 0),        # Black
    (255, 255, 255),  # White
    (68, 129, 172),   # #4481AC (Blue)
    (255, 145, 77),   # #FF914D (Orange)
    (217, 217, 217),  # #D9D9D9 (Light Gray)
    (248, 215, 194),  # #F8D7C2 (Pale Orange)
    (188, 221, 244),  # #BCDDF4 (Light Blue)
    (254, 229, 153),  # #FEE599 (Pale Yellow)
    (47, 84, 150),    # #2F5496 (Dark Blue)
    (111, 59, 85)     # #6F3B55 (Maroon/Dark Red)
]

# --- 5. WCAG CONSTANTS ---
WCAG_RATIO_NORMAL = 4.5
WCAG_RATIO_LARGE = 3.0
WCAG_MIN_LARGE_FONT_SIZE = 18
WCAG_MIN_GRAPHIC_RATIO = 3.0

# --- 6. INSTRUCTIONAL DESIGN SCHEMA (NOTES) ---
REQUIRED_HEADERS = [
    "Instructional Activity:", "Instructional Time:", "Materials:", "Do:", "Talking Points:" 
]

VALID_GAGNE_TERMS = [
    "gain attention", "provide direction", "direction", "provide the objectives",
    "recall", "present content", "provide guided learning", "elicit performance",
    "provide feedback", "evaluate performance", "enhance retention and transfer"
]

GAGNE_CATEGORIES = {
    "Present Content": "Present Content",
    "Elicit Performance": "Elicit Performance",
    "Provide Feedback": "Other",
    "Evaluate Performance": "Other",
    "Gain Attention": "Other",
    "Recall": "Other",
    "Enhance Retention": "Other",
    "Provide Direction": "Other"
}

# --- 7. SLIDE EXEMPTION RULES ---
EXEMPT_FIRST_SLIDE = True
EXEMPT_LAST_SLIDE = True
EXEMPT_SPECIFIC_SLIDES = []

# --- 8. CLARITY & STYLE RULES ---
TARGET_READING_GRADE_LEVEL = 9  # Default: 9th Grade
WEASEL_WORDS = ["basically", "sort of", "kind of", "hopefully", "try to", "maybe", "perhaps", "I think", "various", "attempt to"]
CLIENT_BLACKLIST = {
    "utilize": "use", "facilitate": "help", "implement": "do/start", "leverage": "use",
    "synergy": "cooperation", "methodology": "method", "optimize": "improve", "disseminate": "send/share"
}

# --- 9. AI COST ESTIMATION ---
AI_MODEL_RATES = {
    "Gemini 2.5 Pro": 1.25, "Gemini 2.5 Flash": 0.15, "GPT-4o": 2.50, "GPT-4o Mini": 0.15,
    "Claude 3.5 Sonnet": 3.00, "Mistral Large 2": 2.00, "Llama 3.3 70B (Groq)": 0.59, "Amazon Nova Pro": 0.80
}

# --- 10. TIME ESTIMATION SETTINGS ---
WPM_READING_SPEED = 130
BUFFER_STANDARD_SLIDE = 0.5
BUFFER_ACTIVITY_SLIDE = 5.0
ACTIVITY_KEYWORDS = ["activity", "discussion", "exercise", "group work", "lab", "breakout"]

# --- 11. CLOUDFLARE CONFIGURATION ---
cf_account_id = "8c2fef5ed2716aada8d3a4cbbf0005b8"
cf_vectorize_token = "3Y556X4XIqp296vv7EkV7eJr4ovDd0ZmnOu1QnLs"
cf_workers_ai_token = "kxRQq6MI2Ctx-cFYcjnkGDNot4grpAxvMYfLn3H3"
cf_index_name = "instructional-cadence-kb"