# AuditSlide AI - Utility Functions (v2)
# ==============================================================================
# UPDATES: Added 'find_closest_compliant_color' for smart WCAG remediation.
# ==============================================================================
import math
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Pt

from .config import BRAND_COLORS_RGB, WCAG_RATIO_NORMAL, WCAG_RATIO_LARGE, WCAG_MIN_LARGE_FONT_SIZE

# --- COLOR CONVERSION & MATH ---

def rgb_to_hex(rgb: tuple) -> str:
    """Converts an RGB tuple (0-255) to a hexadecimal string."""
    return f'#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}'

def rgb_pptx_to_tuple(rgb_val) -> tuple:
    """Robustly handles both python-pptx RGBColor objects and legacy integer values."""
    try:
        hex_str = str(rgb_val)
        if len(hex_str) == 6:
            r = int(hex_str[:2], 16)
            g = int(hex_str[2:4], 16)
            b = int(hex_str[4:], 16)
            return (r, g, b)
    except (ValueError, TypeError):
        pass

    if isinstance(rgb_val, int):
        # VBA Long is usually BGR
        r = rgb_val & 0xFF
        g = (rgb_val >> 8) & 0xFF
        b = (rgb_val >> 16) & 0xFF
        return (r, g, b)
    
    return (0, 0, 0)

def linearize_rgb(v: float) -> float:
    if v <= 0.03928:
        return v / 12.92
    else:
        return ((v + 0.055) / 1.055) ** 2.4

def get_relative_luminance(rgb_tuple: tuple) -> float:
    r = rgb_tuple[0] / 255.0
    g = rgb_tuple[1] / 255.0
    b = rgb_tuple[2] / 255.0
    return (0.2126 * linearize_rgb(r)) + (0.7152 * linearize_rgb(g)) + (0.0722 * linearize_rgb(b))

def calculate_contrast_ratio(rgb1: tuple, rgb2: tuple) -> float:
    """Returns contrast ratio between two RGB tuples."""
    l1 = get_relative_luminance(rgb1)
    l2 = get_relative_luminance(rgb2)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)

# --- SMART REMEDIATION LOGIC ---

def adjust_color_brightness(rgb: tuple, factor: float) -> tuple:
    """
    Darkens or lightens a color by a factor.
    factor < 1.0 : Darken
    factor > 1.0 : Lighten
    """
    r, g, b = rgb
    # Adjust and clamp between 0-255
    new_r = max(0, min(255, int(r * factor)))
    new_g = max(0, min(255, int(g * factor)))
    new_b = max(0, min(255, int(b * factor)))
    return (new_r, new_g, new_b)

def find_closest_compliant_color(fg_rgb: tuple, bg_rgb: tuple, required_ratio: float) -> tuple:
    """
    Iteratively adjusts the foreground color (fg_rgb) to meet the required_ratio 
    against the background (bg_rgb) while preserving the original hue as much as possible.
    """
    current_ratio = calculate_contrast_ratio(fg_rgb, bg_rgb)
    
    # If it already passes, return original
    if current_ratio >= required_ratio:
        return fg_rgb

    bg_lum = get_relative_luminance(bg_rgb)
    fg_lum = get_relative_luminance(fg_rgb)

    # Decide direction: Should we lighten or darken the text?
    # Generally, if BG is dark (lum < 0.5), we want lighter text.
    # If BG is bright (lum > 0.5), we want darker text.
    
    # Special case: If contrast is very low, standard heuristic might fail. 
    # We compare luminances to find the "natural" direction.
    if bg_lum > fg_lum:
        # Background is brighter than text -> Darken text
        step = 0.95 # Darken by 5% each step
        direction = "darken"
    else:
        # Background is darker than text -> Lighten text
        step = 1.05 # Lighten by 5% each step
        direction = "lighten"

    # Iteration limit to prevent infinite loops
    max_iter = 50 
    candidate_rgb = fg_rgb

    for _ in range(max_iter):
        candidate_rgb = adjust_color_brightness(candidate_rgb, step)
        new_ratio = calculate_contrast_ratio(candidate_rgb, bg_rgb)
        
        if new_ratio >= required_ratio:
            return candidate_rgb
        
        # Boundary check: If we hit pure black or pure white, stop
        if direction == "darken" and sum(candidate_rgb) == 0:
            return (0, 0, 0)
        if direction == "lighten" and sum(candidate_rgb) >= 765: # 255*3
            return (255, 255, 255)

    # Fallback: If heuristic failed, return Black or White based on BG luminance
    return (0, 0, 0) if bg_lum > 0.5 else (255, 255, 255)

# --- SHAPE/ID HELPERS ---

def get_point_size(text_run) -> float:
    if text_run.font.size:
        return text_run.font.size.pt
    return 0.0

def is_large_text(text_run) -> bool:
    size = get_point_size(text_run)
    bold = text_run.font.bold
    return (size >= WCAG_MIN_LARGE_FONT_SIZE and bold) or (size >= 24)

def get_required_ratio(text_run) -> float:
    return WCAG_RATIO_LARGE if is_large_text(text_run) else WCAG_RATIO_NORMAL

def is_brand_color(rgb_tuple: tuple) -> bool:
    return rgb_tuple in BRAND_COLORS_RGB

def shapes_overlap(s1, s2) -> bool:
    return (s1.left < s2.left + s2.width) and \
           (s1.left + s1.width > s2.left) and \
           (s1.top < s2.top + s2.height) and \
           (s1.top + s1.height > s2.top)