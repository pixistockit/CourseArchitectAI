# AuditSlide AI - Core Presentation Analyzer (v36 - Restoration Fix)
# ==============================================================================
# UPDATES:
# 1. FIX: Restored '_check_notes_formatting' which was missing in v35.
# 2. VERIFIED: All helper methods (clarity, notes, spelling, contrast) are present.
# ==============================================================================

import re
import os
import csv
import string
from datetime import datetime
from spellchecker import SpellChecker
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.enum.dml import MSO_FILL
from pptx.util import Pt

from .grammar_master import ClarityEngine
from .time_estimator import TimeEstimator

from .config import (
    EXEMPT_SHAPE_NAMES, TITLE_FONT_NAME, TITLE_FONT_SIZE_MIN, TITLE_FONT_SIZE_MAX,
    TITLE_MUST_BE_BOLD, BODY_FONT_SIZE_MIN, ALLOWED_BODY_FONTS,
    NOTES_FONT_NAME, NOTES_FONT_SIZE_MIN, NOTES_FONT_SIZE_MAX, NOTES_FONT_COLOR_RGB,
    THEME_FONT_MAPPING, WCAG_MIN_GRAPHIC_RATIO, REQUIRED_HEADERS, VALID_GAGNE_TERMS, GAGNE_CATEGORIES,
    EXEMPT_FIRST_SLIDE, EXEMPT_LAST_SLIDE, EXEMPT_SPECIFIC_SLIDES, ACTIVITY_KEYWORDS
)
from .utils import (
    rgb_pptx_to_tuple, shapes_overlap, get_relative_luminance,
    get_required_ratio, calculate_contrast_ratio, is_brand_color,
    find_closest_compliant_color
)

# Constants
BG_IS_IMAGE = -1
BG_IS_TABLE = -2
WHITE_RGB_TUPLE = (255, 255, 255)
BLACK_RGB_TUPLE = (0, 0, 0)

class PptxAnalyzer:
    def __init__(self, pptx_path: str):
        self.prs = Presentation(pptx_path)
        self.total_slides_checked = 0
        self.qa_report = []
        
        self.gagne_metrics = { "Present Content": 0, "Elicit Performance": 0, "Other": 0 }
        
        self.pacing_data = {
            "total_planned_mins": 0, "total_estimated_mins": 0, "total_projected_mins": 0, "sections": [] 
        }
        self.current_section = {"name": "Introduction", "planned": 0, "estimated": 0, "projected": 0, "slide_count": 0}
        
        self.in_activity_sequence = False
        self.current_sequence_is_funded = False 
        self.debug_log = [] 

        self.slide_content_map = [] 

        self.spell = SpellChecker()
        self.spell.word_frequency.load_words(['pptx', 'wcag', 'gagne', 'rgb', 'id', 'qa', 'calibri', 'rockwell', "today's", "it's", "don't", "can't", "you'll"])
        self.clarity = ClarityEngine()
        self.timer = TimeEstimator()

    def _extract_slide_text_content(self, slide):
        title = ""
        text_parts = []
        if slide.shapes.title and slide.shapes.title.text:
            title = slide.shapes.title.text.strip()
            
        # --- VISUAL CONTEXT COUNTERS ---
        img_count = 0
        chart_count = 0
        table_count = 0
        group_count = 0

        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text:
                clean_t = shape.text.strip()
                if clean_t and clean_t != title:
                    text_parts.append(clean_t)
            
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE: img_count += 1
            if shape.has_chart: chart_count += 1
            if shape.has_table: table_count += 1
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP: group_count += 1
        
        notes = ""
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            
        layout_name = slide.slide_layout.name if slide.slide_layout else "Unknown"

        return {
            "title": title,
            "text": " ".join(text_parts),
            "notes": notes,
            "visual_context": {
                "layout": layout_name,
                "images": img_count,
                "charts": chart_count,
                "tables": table_count,
                "groups": group_count
            }
        }

    def _strip_citations_for_analysis(self, text):
        if not text: return ""
        text_lower = text.lower()
        headers = [r"source\s*:", r"sources\s*:", r"references?\s*:", r"bibliography\s*:", r"works\s+cited\s*:"]
        
        best_idx = len(text)
        found = False
        
        for h in headers:
            match = re.search(h, text_lower)
            if match:
                for m in re.finditer(h, text_lower):
                    if m.start() > len(text) * 0.5: 
                        best_idx = m.start()
                        found = True
        
        if found: return text[:best_idx].strip()

        split_point = int(len(text) * 0.8)
        bottom_chunk = text_lower[split_point:]
        if "http" in bottom_chunk:
            idx = bottom_chunk.find("http")
            return text[:split_point + idx].strip()
            
        return text

    def _check_single_quotes(self, text, shape_name, slide_index, slide_issues):
        if not text: return
        double_quote_pattern = r'([“"])(.*?)\1'
        safe_zones = []
        for match in re.finditer(double_quote_pattern, text, re.DOTALL):
            safe_zones.append(match.span())

        single_quote_pattern = r"(?<!\w)['‘](.+?)['’](?!\w)"
        offenders = []
        for match in re.finditer(single_quote_pattern, text, re.DOTALL):
            s_start, s_end = match.span()
            phrase = match.group(0)
            is_safe = False
            for (d_start, d_end) in safe_zones:
                if d_start <= s_start and s_end <= d_end:
                    is_safe = True; break
            if not is_safe:
                clean_phrase = phrase.strip()
                if len(clean_phrase) > 2: offenders.append(clean_phrase)

        if offenders:
            details_str = ", ".join(offenders)
            slide_issues.append({"slide": slide_index, "check": "Style - Punctuation", "shape_name": shape_name, "result": "FAIL", "details": f"Do not use single quotes for emphasis. Found: {details_str}"})

    def _resolve_font_name(self, font_name: str) -> str:
        if not font_name: return "Calibri"
        normalized_name = font_name.lower().strip()
        return THEME_FONT_MAPPING.get(normalized_name, font_name)

    def _is_exempt_shape(self, shape) -> bool:
        s_name = shape.name.lower()
        if shape.is_placeholder:
            ph_type = shape.placeholder_format.type
            if ph_type in (PP_PLACEHOLDER.SLIDE_NUMBER, PP_PLACEHOLDER.FOOTER, PP_PLACEHOLDER.DATE): return True
        if any(name in s_name for name in EXEMPT_SHAPE_NAMES): return True
        if shape.has_text_frame and shape.text_frame.text and shape.text_frame.text.strip():
            text_lower = shape.text_frame.text.lower()
            if "copyright" in text_lower or "rights reserved" in text_lower:
                if shape.top > (self.prs.slide_height * 0.8): return True
        return False
    
    def _is_decorative(self, shape) -> bool:
        try: return shape.element.nvSpPr.cNvPr.get("decorative") == "1"
        except AttributeError: return False

    def _determine_real_background(self, target_shp, slide) -> tuple:
        try:
            if target_shp.fill.type == MSO_FILL.SOLID and target_shp.fill.visible:
                alpha = target_shp.fill.fore_color.alpha
                if alpha is None or alpha == 1.0: return rgb_pptx_to_tuple(target_shp.fill.fore_color.rgb)
            elif target_shp.fill.type in (MSO_FILL.PICTURE, MSO_FILL.TEXTURE): return BG_IS_IMAGE
        except AttributeError: pass
        best_z = -1; best_bg_color = None; target_index = -1
        for i, s in enumerate(slide.shapes):
            if s.shape_id == target_shp.shape_id: target_index = i; break
        for i, candidate_shp in enumerate(slide.shapes):
            if i < target_index and shapes_overlap(target_shp, candidate_shp):
                if candidate_shp.shape_id == target_shp.shape_id: continue
                bg_color = None
                try:
                    if candidate_shp.shape_type == MSO_SHAPE_TYPE.TABLE: bg_color = BG_IS_TABLE 
                    elif candidate_shp.fill.type == MSO_FILL.SOLID:
                        if candidate_shp.fill.fore_color.alpha is None or candidate_shp.fill.fore_color.alpha == 1.0:
                            try: bg_color = rgb_pptx_to_tuple(candidate_shp.fill.fore_color.rgb)
                            except AttributeError: pass 
                    elif candidate_shp.fill.type in (MSO_FILL.PICTURE, MSO_FILL.TEXTURE): bg_color = BG_IS_IMAGE
                except AttributeError: pass
                if bg_color: best_bg_color = bg_color
        if best_bg_color: return best_bg_color
        return WHITE_RGB_TUPLE

    def _check_spelling(self, text, shape_name, slide_index, slide_issues):
        if not text or len(text) < 3: return
        text_no_url = re.sub(r'http\S+|www\.\S+|[\w\.-]+@[\w\.-]+', '', text)
        text_norm = text_no_url.replace("’", "'").replace("‘", "'")
        text_spaced = re.sub(r'([a-z])([A-Z])', r'\1 \2', text_norm)
        text_spaced = re.sub(r'(\.)([A-Z])', r'\1 \2', text_spaced)
        text_spaced = re.sub(r'[-_/]', ' ', text_spaced)
        clean_text = re.sub(r"[^\w\s']", '', text_spaced)
        words = clean_text.split()
        candidates = []
        for w in words:
            w_clean = w.strip("'")
            if not w_clean: continue
            if any(char.isdigit() for char in w_clean): continue
            if w_clean.isupper() and len(w_clean) > 1: continue
            if len(w_clean) > 3: candidates.append(w_clean)
        if not candidates: return
        unknown = self.spell.unknown(candidates)
        if unknown:
            safe_words = {"youtube", "linkedin", "tiktok", "instagram", "video", "intro", "outro", "agenda", "module"}
            true_typos = [w for w in unknown if w.lower() not in safe_words]
            if true_typos:
                typo_str = ", ".join(list(true_typos)[:5])
                slide_issues.append({"slide": slide_index, "check": "Copyediting - Spelling", "shape_name": shape_name, "result": "WARNING", "details": f"Potential typos found: {typo_str}..."})

    def _check_clarity_and_style(self, text, shape_name, slide_index, slide_issues, context="slide"):
        if not text or len(text.split()) < 4: return
        res = self.clarity.check_reading_level(text, context)
        if res:
            raw_msg = res['details']
            grade_match = re.search(r"Grade\s+(\d+\.\d+)", raw_msg)
            if grade_match:
                rounded_grade = f"{float(grade_match.group(1)):.1f}"
                raw_msg = raw_msg.replace(grade_match.group(1), rounded_grade)
            clean_snippet = text.strip().replace('\n', ' ')
            if len(clean_snippet) > 60: clean_snippet = clean_snippet[:60] + "..."
            res['details'] = f"{raw_msg} Context: \"{clean_snippet}\""
            res.update({"slide": slide_index, "check": "Clarity - Reading Level", "shape_name": shape_name})
            slide_issues.append(res)
        res = self.clarity.check_passive_voice(text, context)
        if res:
            res.update({"slide": slide_index, "check": "Clarity - Tone", "shape_name": shape_name})
            slide_issues.append(res)
        res = self.clarity.check_bad_habits(text)
        if res:
            res.update({"slide": slide_index, "check": "Brand Voice - Jargon", "shape_name": shape_name})
            slide_issues.append(res)

    def _check_reading_order(self, slide, slide_index, slide_issues):
        title_candidates = []
        if slide.shapes.title: title_candidates.append(slide.shapes.title)
        for shape in slide.shapes:
            if shape.is_placeholder and shape.placeholder_format.type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
                if shape not in title_candidates: title_candidates.append(shape)
        if not title_candidates:
            slide_issues.append({"slide": slide_index, "check": "Accessibility Reading Order", "shape_name": "Slide", "result": "FAIL", "details": "Slide missing standard Title placeholder."})
            return
        title_candidates.sort(key=lambda s: s.top)
        target_title = title_candidates[0] 
        title_idx = -1
        for i, s in enumerate(slide.shapes):
            if s.shape_id == target_title.shape_id: title_idx = i; break
        for i in range(title_idx + 1, len(slide.shapes)):
            s = slide.shapes[i]
            if shapes_overlap(target_title, s) and not self._is_exempt_shape(s):
                slide_issues.append({"slide": slide_index, "check": "Accessibility Reading Order", "shape_name": s.name, "result": "FAIL", "details": f"Object '{s.name}' obscures the Title. Check reading order."})

    def _check_hyperlinks(self, shape, slide_index, slide_issues):
        if not shape.has_text_frame: return
        for p in shape.text_frame.paragraphs:
            for r in p.runs:
                if r.hyperlink.address:
                    link = r.hyperlink.address
                    if not link.startswith("#"): slide_issues.append({"slide": slide_index, "check": "Usability - Links", "shape_name": shape.name, "result": "INFO", "details": f"External Link found: {link}"})

    def _check_citations(self, text, shape_name, slide_index, slide_issues):
        facilitation_keywords = ["Talking Points:", "Instructional Activity:", "Do:", "Say:", "Transition:", "Instructor Guide", "Script:"]
        if any(k in text for k in facilitation_keywords): return 
        citation_pattern = r"\([A-Za-z\s&]+,\s?\d{4}\)"
        source_header_pattern = r"(?i)(Source:|Sources:|Reference:|References:|Bibliography:|Works Cited:)"
        numeric_pattern = r"(\[\d+\]|\(\d+\)|Source\s+\d+)"
        if not (bool(re.search(citation_pattern, text)) or bool(re.search(source_header_pattern, text)) or bool(re.search(numeric_pattern, text))):
            if len(text.split()) > 50: 
                slide_issues.append({"slide": slide_index, "check": "Instructional Design - Citations", "shape_name": shape_name, "result": "WARNING", "details": "Large text block appears missing APA citation or Source header."})
    
    def _check_alt_text(self, shape, slide_index, slide_issues):
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            if self._is_decorative(shape): return
            alt_text = ""
            try: alt_text = shape.element.nvSpPr.cNvPr.get("descr", "")
            except: pass
            if not alt_text: slide_issues.append({"slide": slide_index, "check": "Accessibility - Alt Text", "shape_name": shape.name, "result": "FAIL", "details": "Image missing Alt Text."})

    def _check_fonts(self, shape, slide_index, slide_issues, is_ghost_title=False):
        if not shape.has_text_frame or not shape.text_frame.text.strip(): return
        if is_ghost_title: return
        is_title = False
        if shape.is_placeholder and shape.placeholder_format.type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE): is_title = True
        for paragraph in shape.text_frame.paragraphs:
            for run in paragraph.runs:
                if not run.text.strip(): continue
                if run.font.name is None: raw_font_name = "+mj-lt" if is_title else "+mn-lt"
                else: raw_font_name = run.font.name
                f_name = self._resolve_font_name(raw_font_name)
                f_size = run.font.size.pt if run.font.size else 0
                f_bold = run.font.bold
                is_visually_bold = (f_bold is True or f_bold is None or any(w in raw_font_name.lower() for w in ["bold", "black", "heavy"]))
                txt_sample = run.text.strip()[:20] + "..."
                if is_title:
                    if f_name != TITLE_FONT_NAME: slide_issues.append({"slide": slide_index, "check": "Font Rules", "shape_name": shape.name, "result": "FAIL", "details": f"Title font is '{raw_font_name}' (Should be {TITLE_FONT_NAME}). Text: '{txt_sample}'"})
                    if f_size > 0 and (f_size < TITLE_FONT_SIZE_MIN or f_size > TITLE_FONT_SIZE_MAX): slide_issues.append({"slide": slide_index, "check": "Font Rules", "shape_name": shape.name, "result": "FAIL", "details": f"Title size is {f_size}pt. Text: '{txt_sample}'"})
                    if TITLE_MUST_BE_BOLD and not is_visually_bold: slide_issues.append({"slide": slide_index, "check": "Font Rules", "shape_name": shape.name, "result": "FAIL", "details": f"Title is not Bold. Text: '{txt_sample}'"})
                else:
                    if f_size < BODY_FONT_SIZE_MIN and f_size > 0: slide_issues.append({"slide": slide_index, "check": "Font Rules", "shape_name": shape.name, "result": "FAIL", "details": f"Font size {f_size}pt is below minimum ({BODY_FONT_SIZE_MIN}pt). Text: '{txt_sample}'"})
                    if f_name not in ALLOWED_BODY_FONTS: slide_issues.append({"slide": slide_index, "check": "Font Rules", "shape_name": shape.name, "result": "FAIL", "details": f"Unauthorized font found: '{raw_font_name}'. Text: '{txt_sample}'"})

    def _check_brand_colors(self, shape, slide_index, slide_issues):
        try:
            if shape.fill.type == MSO_FILL.SOLID and shape.fill.visible:
                rgb = rgb_pptx_to_tuple(shape.fill.fore_color.rgb)
                if not is_brand_color(rgb): slide_issues.append({"slide": slide_index, "check": "Brand Consistency", "shape_name": shape.name, "result": "FAIL", "details": f"Non-brand object color found: {rgb}"})
        except (AttributeError, TypeError): pass

    def _check_non_text_contrast(self, shape, slide, slide_index, slide_issues):
        if shape.has_text_frame and shape.text_frame.text.strip(): return
        try:
            if shape.fill.type == MSO_FILL.SOLID and shape.fill.visible:
                alpha = shape.fill.fore_color.alpha
                if alpha is not None and alpha < 1.0: return 
                obj_rgb = rgb_pptx_to_tuple(shape.fill.fore_color.rgb)
                bg_rgb = self._determine_real_background(shape, slide)
                if bg_rgb in (BG_IS_IMAGE, BG_IS_TABLE):
                    slide_issues.append({"slide": slide_index, "check": "WCAG Graphic Contrast", "shape_name": shape.name, "result": "MANUAL REVIEW", "details": "Object over Image/Table."})
                    return
                ratio = calculate_contrast_ratio(obj_rgb, bg_rgb)
                if ratio < WCAG_MIN_GRAPHIC_RATIO:
                    suggested = find_closest_compliant_color(obj_rgb, bg_rgb, WCAG_MIN_GRAPHIC_RATIO)
                    slide_issues.append({"slide": slide_index, "check": "WCAG Graphic Contrast", "shape_name": shape.name, "result": "FAIL", "ratio": f"{ratio:.1f}:1 (Req: {WCAG_MIN_GRAPHIC_RATIO}:1)", "colors_used": f"Obj: {obj_rgb} on BG: {bg_rgb}", "suggested_fix_color": suggested, "details": f"Low contrast graphics. Ratio: {ratio:.1f}:1"})
        except (AttributeError, TypeError): pass

    # --- RESTORED: NOTES FORMATTING CHECK (Was Missing) ---
    def _check_notes_formatting(self, slide, slide_index, slide_issues):
        if not slide.has_notes_slide: return
        notes_tf = slide.notes_slide.notes_text_frame
        if not notes_tf.text.strip(): return
        
        full_text = notes_tf.text
        clean_text = self._strip_citations_for_analysis(full_text)
        
        self._check_single_quotes(clean_text, "Speaker Notes", slide_index, slide_issues)
        self._check_spelling(clean_text, "Speaker Notes", slide_index, slide_issues)
        self._check_citations(full_text, "Speaker Notes", slide_index, slide_issues)

        for paragraph in notes_tf.paragraphs:
            for run in paragraph.runs:
                if not run.text.strip(): continue
                if run.font.name is None: raw_font_name = "+mn-lt" 
                else: raw_font_name = run.font.name
                f_name = self._resolve_font_name(raw_font_name)
                f_size = run.font.size.pt if run.font.size else 12
                if f_name != NOTES_FONT_NAME: 
                    slide_issues.append({"slide": slide_index, "check": "Notes Formatting", "shape_name": "Speaker Notes", "result": "FAIL", "details": f"Notes font is '{raw_font_name}' (Should be {NOTES_FONT_NAME})"})
                if not (NOTES_FONT_SIZE_MIN <= f_size <= NOTES_FONT_SIZE_MAX): 
                    slide_issues.append({"slide": slide_index, "check": "Notes Formatting", "shape_name": "Speaker Notes", "result": "FAIL", "details": f"Notes size is {f_size}pt (Should be {NOTES_FONT_SIZE_MIN}-{NOTES_FONT_SIZE_MAX})"})
                try:
                    if run.font.color.rgb:
                        c_rgb = rgb_pptx_to_tuple(run.font.color.rgb)
                        if c_rgb != NOTES_FONT_COLOR_RGB: 
                            slide_issues.append({"slide": slide_index, "check": "Notes Formatting", "shape_name": "Speaker Notes", "result": "FAIL", "details": "Notes font color is not Black."})
                except AttributeError: pass

    def _check_notes_content(self, slide, slide_index, slide_issues):
        if not slide.has_notes_slide: return
        notes_tf = slide.notes_slide.notes_text_frame
        if not notes_tf.text.strip(): return
        notes_text = notes_tf.text; notes_lower = notes_text.lower()
        header_positions = {}
        for header in REQUIRED_HEADERS:
            idx = notes_lower.find(header.lower())
            if idx != -1: header_positions[header] = idx
            else:
                if header == "Instructional Activity:" or header == "Instructional Time:": 
                    slide_issues.append({"slide": slide_index, "check": "Instructional Design", "shape_name": "Speaker Notes", "result": "FAIL", "details": f"Missing critical header: '{header}'"})
        sorted_headers = sorted(header_positions.items(), key=lambda x: x[1])
        for i, (header_name, start_idx) in enumerate(sorted_headers):
            content_start = start_idx + len(header_name)
            if i < len(sorted_headers) - 1: content_end = sorted_headers[i+1][1]
            else: content_end = len(notes_text)
            content_chunk = notes_text[content_start:content_end].strip()
            if not content_chunk:
                slide_issues.append({"slide": slide_index, "check": "Instructional Design", "shape_name": "Speaker Notes", "result": "FAIL", "details": f"Header '{header_name}' appears empty."})
                continue
            if header_name == "Instructional Activity:":
                found_gagne = False; chunk_lower = content_chunk.lower(); term_found = "Other"
                for term in VALID_GAGNE_TERMS:
                    if term in chunk_lower:
                        found_gagne = True
                        for k, v in GAGNE_CATEGORIES.items():
                            if k.lower() in chunk_lower: term_found = v; break
                        break
                self.gagne_metrics[term_found] = self.gagne_metrics.get(term_found, 0) + 1
                if not found_gagne: 
                    slide_issues.append({"slide": slide_index, "check": "Instructional Design", "shape_name": "Speaker Notes", "result": "FAIL", "details": f"Instructional Activity '{content_chunk[:30]}...' does not match valid Gagne terms."})

    def analyze_slide(self, slide, slide_index, is_exempt=False):
        self.total_slides_checked += 1
        
        content_data = self._extract_slide_text_content(slide)
        self.slide_content_map.append({
            "slide_number": slide_index,
            "title": content_data['title'],
            "full_text": content_data['text'],
            "notes": content_data['notes'],
            "visual_context": content_data['visual_context'] 
        })

        slide_issues = []
        if is_exempt:
            slide_issues.append({"slide": slide_index, "check": "Status", "shape_name": "Slide", "result": "EXEMPT", "details": "This slide was excluded from automated analysis."})
            self.qa_report.extend(slide_issues)
            self.debug_log.append({"Slide": slide_index, "Type": "EXEMPT", "Activity?": "N/A", "Sequence?": "N/A", "Planned": 0, "Implicit": 0, "Projected": 0, "Delta": 0, "Funded?": "N/A", "Notes": "Exempt"})
            return

        is_new_section = False
        layout_name = slide.slide_layout.name.lower() if slide.slide_layout else ""
        slide_title = slide.shapes.title.text.lower() if slide.shapes.title and slide.shapes.title.text else ""
        
        if "header" in layout_name or "section" in layout_name or "title" in layout_name:
            if slide_index > 1: is_new_section = True
            self.in_activity_sequence = False
            self.current_sequence_is_funded = False
        
        current_is_activity = False
        if any(k in layout_name for k in ACTIVITY_KEYWORDS) or any(k in slide_title for k in ACTIVITY_KEYWORDS):
            current_is_activity = True
        
        notes_txt = slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else ""
        if not current_is_activity and notes_txt:
            if "elicit performance" in notes_txt.lower(): current_is_activity = True

        explicit = self.timer.extract_explicit_time(notes_txt)
        sequence_status = "None"
        apply_heavy_tax = False
        
        if current_is_activity:
            if not self.in_activity_sequence:
                self.in_activity_sequence = True
                sequence_status = "START"
                if explicit > 0:
                    self.current_sequence_is_funded = True
                    apply_heavy_tax = False
                else:
                    self.current_sequence_is_funded = False
                    apply_heavy_tax = True
            else:
                sequence_status = "CONTINUE"
                if explicit > 0:
                    self.current_sequence_is_funded = True
                    apply_heavy_tax = False
                else:
                    apply_heavy_tax = False
        else:
            self.in_activity_sequence = False
            self.current_sequence_is_funded = False
            sequence_status = "RESET"

        implicit = self.timer.calculate_implicit_time(notes_txt, is_activity=apply_heavy_tax)
        projected = 0
        if explicit > 0: projected = explicit
        else:
            if self.current_sequence_is_funded and current_is_activity: projected = 0 
            else: projected = implicit

        delta = round(projected - explicit, 1) if explicit > 0 else 0
        debug_note = ""
        if self.current_sequence_is_funded and current_is_activity and explicit == 0:
            debug_note = "Covered by Sequence Funding"
        
        self.debug_log.append({
            "Slide": slide_index, "Type": "Activity" if current_is_activity else "Content", "Activity?": str(current_is_activity),
            "Sequence?": sequence_status, "Planned": explicit, "Implicit": implicit, "Projected": projected,
            "Delta": delta, "Funded?": str(self.current_sequence_is_funded), "Notes": debug_note
        })

        self.pacing_data["total_planned_mins"] += explicit
        self.pacing_data["total_estimated_mins"] += implicit
        self.pacing_data["total_projected_mins"] += projected
        
        if is_new_section:
            self.pacing_data["sections"].append(self.current_section)
            t = slide.shapes.title.text if slide.shapes.title and slide.shapes.title.text else f"Section {len(self.pacing_data['sections'])+1}"
            self.current_section = {"name": t, "planned": 0, "estimated": 0, "projected": 0, "slide_count": 0}
        
        self.current_section["planned"] += explicit
        self.current_section["estimated"] += implicit
        self.current_section["projected"] += projected
        self.current_section["slide_count"] += 1

        title_shapes = []
        for shape in slide.shapes:
            if shape.is_placeholder and shape.placeholder_format.type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE): title_shapes.append(shape)
        ghost_title_ids = []
        if len(title_shapes) > 1:
            title_shapes.sort(key=lambda s: s.top)
            for ghost in title_shapes[1:]: ghost_title_ids.append(ghost.shape_id)

        self._check_reading_order(slide, slide_index, slide_issues)

        for shape in slide.shapes:
            if self._is_exempt_shape(shape): continue
            is_ghost = shape.shape_id in ghost_title_ids
            self._check_fonts(shape, slide_index, slide_issues, is_ghost_title=is_ghost)
            self._check_brand_colors(shape, slide_index, slide_issues)
            self._check_non_text_contrast(shape, slide, slide_index, slide_issues)
            self._check_hyperlinks(shape, slide_index, slide_issues)
            self._check_alt_text(shape, slide_index, slide_issues)
            
            if shape.has_text_frame and shape.text_frame.text and shape.text_frame.text.strip():
                if not is_ghost:
                    clean_screen_text = self._strip_citations_for_analysis(shape.text_frame.text)
                    self._check_single_quotes(clean_screen_text, shape.name, slide_index, slide_issues)
                    
                    self._check_spelling(clean_screen_text, shape.name, slide_index, slide_issues)
                    self._check_clarity_and_style(clean_screen_text, shape.name, slide_index, slide_issues, context="slide")

            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    for run in paragraph.runs:
                        if run.text.strip():
                            try: text_rgb = rgb_pptx_to_tuple(run.font.color.rgb)
                            except (AttributeError, TypeError): text_rgb = BLACK_RGB_TUPLE 
                            bg_rgb = self._determine_real_background(shape, slide)
                            if bg_rgb in (BG_IS_IMAGE, BG_IS_TABLE):
                                issue_type = "Image/Texture BG" if bg_rgb == BG_IS_IMAGE else "Table BG"
                                slide_issues.append({"slide": slide_index, "check": "WCAG Text Contrast", "shape_name": shape.name, "result": "MANUAL REVIEW", "details": f"Text over {issue_type}. Text Sample: '{run.text.strip()[:20]}...'"})
                                continue
                            ratio = calculate_contrast_ratio(text_rgb, bg_rgb)
                            required_ratio = get_required_ratio(run)
                            if ratio < required_ratio:
                                suggested = find_closest_compliant_color(text_rgb, bg_rgb, required_ratio)
                                slide_issues.append({"slide": slide_index, "check": "WCAG Text Contrast", "shape_name": shape.name, "result": "FAIL", "ratio": f"{ratio:.1f}:1 (Req: {required_ratio:.1f}:1)", "colors_used": f"Text: {text_rgb} on BG: {bg_rgb}", "suggested_fix_color": suggested, "details": f"Contrast failure ({ratio:.1f}:1). Text: '{run.text.strip()[:20]}...'"})
        
        if slide.has_notes_slide:
            notes_tf = slide.notes_slide.notes_text_frame
            if notes_tf.text.strip():
                self._check_notes_formatting(slide, slide_index, slide_issues)
                self._check_notes_content(slide, slide_index, slide_issues)

        self.qa_report.extend(slide_issues)

    def run_analysis(self):
        total_count = len(self.prs.slides)
        exempt_indices = set(EXEMPT_SPECIFIC_SLIDES)
        if EXEMPT_FIRST_SLIDE: exempt_indices.add(1)
        if EXEMPT_LAST_SLIDE: exempt_indices.add(total_count)
        for i, slide in enumerate(self.prs.slides):
            idx = i + 1; is_exempt = idx in exempt_indices
            self.analyze_slide(slide, idx, is_exempt=is_exempt)
        
        self.pacing_data["sections"].append(self.current_section)
        
        return self.qa_report

    def export_debug_log(self, output_dir):
        csv_path = os.path.join(output_dir, f"Pacing_Debug_Log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        headers = ["Slide", "Type", "Activity?", "Sequence?", "Planned", "Implicit", "Projected", "Delta", "Funded?", "Notes"]
        try:
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                writer.writerows(self.debug_log)
            return csv_path
        except Exception as e:
            print(f"❌ Log Export Failed: {e}")
            return None