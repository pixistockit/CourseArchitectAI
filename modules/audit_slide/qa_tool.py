# /modules/audit_slide/qa_tool.py - DEFINITIVE RESTORATION

import os
import json
import logging
import shutil
from datetime import datetime
from pptx import Presentation

# --- CORRECTED RELATIVE IMPORTS ---
try:
    from .analyzer import PptxAnalyzer
    from .ai_engine import AIEngine
    from .report_generator import generate_html_report, generate_spa_report, generate_ai_context_report
except ImportError as e:
    print(f"CRITICAL ERROR: Missing modules in qa_tool.py. {e}")
    raise e

# --- SETUP LOGGING (Restoring Original File Logger) ---
LOG_DIR = 'data/logs'
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, 'audit_debug.log')

logger = logging.getLogger('audit_tool')
logger.setLevel(logging.DEBUG)
# Ensure we don't duplicate handlers if re-imported
if not logger.handlers:
    fh = logging.FileHandler(log_file, mode='a')
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(fh)

def _enforce_single_session(active_dir):
    """
    Sustainability Fix: Delete all report folders EXCEPT the current active one.
    """
    try:
        reports_root = os.path.dirname(active_dir)
        if os.path.basename(reports_root) != 'reports':
            return
        for item in os.listdir(reports_root):
            item_path = os.path.join(reports_root, item)
            if os.path.isdir(item_path) and item_path != active_dir:
                logger.info(f"🧹 Auto-Cleaning Old Session: {item}")
                shutil.rmtree(item_path)
    except Exception as e:
        logger.warning(f"⚠️ Auto-Cleanup Warning: {e}")

def run_audit_slide(pptx_path, output_dir):
    logger.info(f"🚀 STARTING AUDIT: {os.path.basename(pptx_path)}")
    os.makedirs(output_dir, exist_ok=True)
    
    # --- Single Session Mode (Disabled by default for SaaS safety) ---
    # _enforce_single_session(output_dir)

    # 1. CHECK MASTER SLIDE COUNT
    master_count = 1
    try:
        temp_prs = Presentation(pptx_path)
        master_count = len(temp_prs.slide_masters)
        logger.info(f"Detected {master_count} Master Slides.")
    except Exception as e:
        logger.warning(f"Could not count masters: {e}")

    # 2. RUN ANALYSIS
    logger.info("Initializing PptxAnalyzer...")
    analyzer = PptxAnalyzer(pptx_path)
    
    # Execute the scan
    issues = analyzer.run_analysis()
    
    logger.info(f"Analysis Complete. Found {len(issues)} raw issues.")

    # 3. PREPARE DATA (Restoring Original Logic)
    total = analyzer.total_slides_checked
    # Use .get() for safety in case analyzer didn't populate gagne_metrics
    gagne = getattr(analyzer, 'gagne_metrics', {"Present Content": 0, "Elicit Performance": 0, "Other": 0})
    
    pct_present = (gagne.get("Present Content", 0) / total * 100) if total > 0 else 0
    pct_elicit = (gagne.get("Elicit Performance", 0) / total * 100) if total > 0 else 0
    
    wcag_fail_slides = set(i['slide'] for i in issues if "WCAG" in i.get('check', '') and i['result'] == 'FAIL')
    wcag_rate = ((total - len(wcag_fail_slides)) / total * 100) if total > 0 else 100

    # 3a. Group Issues
    grouped_issues = {}
    for issue in issues:
        s_num = issue['slide']
        if s_num not in grouped_issues: grouped_issues[s_num] = []
        grouped_issues[s_num].append(issue)

    # 3b. Extract Slide Content
    slide_data_map = {}
    if hasattr(analyzer, 'slide_content_map'):
        for item in analyzer.slide_content_map:
            slide_data_map[item['slide_number']] = item
    else:
        logger.warning("Analyzer missing slide_content_map.")

    # 4. RUN AI ANALYSIS (Batch)
    logger.info("Sending slide batch to AI Engine...")
    ai_engine = AIEngine()
    ai_results = ai_engine.analyze_batch(
        slides_list=analyzer.slide_content_map,
        total_slide_count=total
    )

    # 5. CONSTRUCT FULL DATA (Matching Original Structure)
    full_data = {
        "summary": {
            "presentation_name": os.path.basename(pptx_path),
            "date_generated": datetime.now().isoformat(),
            "master_slide_count": master_count,
            "total_slides_checked": total,
            "total_errors": len([i for i in issues if i.get('result') in ('FAIL', 'WARNING')]),
            "wcag_fails": len(wcag_fail_slides),
            "manual_reviews": len([i for i in issues if i.get('result') == 'MANUAL REVIEW']),
            "executive_metrics": {
                "slides_present_content": f"{gagne.get('Present Content', 0)} ({round(pct_present, 1)}%)",
                "slides_elicit_performance": f"{gagne.get('Elicit Performance', 0)} ({round(pct_elicit, 1)}%)",
                "other_slides": gagne.get('Other', 0),
                "wcag_compliance_rate": round(wcag_rate, 1)
            },
            "content_metrics": {
                "reading_complexity_fails": len([i for i in issues if "Reading Level" in i.get('check', '')]),
                "passive_voice_count": len([i for i in issues if "Tone" in i.get('check', '') and "Passive" in i.get('details', '')]),
                "jargon_count": len([i for i in issues if "Jargon" in i.get('check', '')])
            },
            "pacing_metrics": getattr(analyzer, 'pacing_data', {})
        },
        "detailed_issues": issues,
        "grouped_issues": grouped_issues,
        "slide_content": slide_data_map, # Used by HTML report
        "ai_analysis": ai_results        # Used by Workstation
    }

    # 6. SAVE ARTIFACTS
    
    # A. JSON
    json_path = os.path.join(output_dir, 'audit_report.json')
    with open(json_path, "w", encoding='utf-8') as f:
        json.dump(full_data, f, indent=4)
    logger.info(f"Saved JSON: {json_path}")

    # B. HTML Reports (Using Modular Generator)
    logger.info("Generating static reports...")
    
    generate_html_report(
        data=full_data, 
        output_path=os.path.join(output_dir, 'Printable Executive Summary.html')
    )
    
    generate_spa_report(
        data=full_data,
        output_path=os.path.join(output_dir, 'ID Workstation.html')
    )
    
    # C. Transcript
    generate_ai_context_report(analyzer, output_dir)

    print(f"✅ Audit Complete. All assets generated in: {output_dir}")
    print(f"📄 Logs: {log_file}")
    
    return json_path

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_output = f"output_{os.path.basename(sys.argv[1])}_{timestamp}"
        run_audit_slide(sys.argv[1], default_output)
    else:
        print("Usage: python -m modules.audit_slide.qa_tool [path_to_pptx]")
