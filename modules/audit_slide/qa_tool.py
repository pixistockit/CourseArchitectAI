# /modules/audit_slide/qa_tool.py - STRICT USER LOGGING ISOLATION

import os
import sys
import json
import logging
import shutil
from datetime import datetime
from pptx import Presentation

# --- PATH SETUP FOR SERVICES ---
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

try:
    from .analyzer import PptxAnalyzer
    from .ai_engine import AIEngine
    from .report_generator import generate_html_report, generate_spa_report, generate_ai_context_report
    
    # Import Central Logger
    from services.logger_service import LoggerService
    central_logger = LoggerService()
except ImportError as e:
    print(f"CRITICAL ERROR: Missing modules in qa_tool.py. {e}")
    central_logger = None

# --- LOCAL DEBUG FALLBACK ---
# This remains as a temporary scratchpad for the Python process itself, 
# but is NOT the official record.
LOG_DIR = 'data/logs'
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, 'audit_debug.log')

logger = logging.getLogger('audit_tool')
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    fh = logging.FileHandler(log_file, mode='a')
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(fh)

def _user_log(report_id, message, agent="ORCHESTRATOR"):
    """Helper to ensure we ONLY write to the specific User/Session log."""
    if central_logger and report_id:
        central_logger.log_audit(report_id, "INFO", message, agent=agent)

def run_audit_slide(pptx_path, output_dir):
    filename = os.path.basename(pptx_path)
    
    # 1. Extract Report ID from the path (e.g., data/reports/{UUID})
    # This ensures logs go to the specific user session folder.
    report_id = os.path.basename(output_dir)
    
    logger.info(f"🚀 STARTING AUDIT: {filename}")
    
    # --- LOGGING: USER STREAM START ---
    _user_log(report_id, f"Session Initiated for file: {filename}", agent="SESSION_MGR")

    os.makedirs(output_dir, exist_ok=True)

    # 2. CHECK MASTER SLIDE COUNT
    master_count = 1
    try:
        temp_prs = Presentation(pptx_path)
        master_count = len(temp_prs.slide_masters)
        _user_log(report_id, f"Metadata Check: {master_count} Master Slides detected.", agent="PARSER")
    except Exception as e:
        logger.warning(f"Could not count masters: {e}")

    # 3. RUN FORENSIC ANALYSIS
    logger.info("Initializing PptxAnalyzer...")
    _user_log(report_id, "Initializing Forensic Analyzer Engine...", agent="ANALYZER")
    
    analyzer = PptxAnalyzer(pptx_path)
    issues = analyzer.run_analysis()
    
    total = analyzer.total_slides_checked
    error_count = len(issues)
    _user_log(report_id, f"Forensic Scan Complete. Scanned {total} slides. Found {error_count} issues.", agent="ANALYZER")

    # 4. PREPARE DATA
    gagne = getattr(analyzer, 'gagne_metrics', {"Present Content": 0, "Elicit Performance": 0, "Other": 0})
    
    pct_present = (gagne.get("Present Content", 0) / total * 100) if total > 0 else 0
    pct_elicit = (gagne.get("Elicit Performance", 0) / total * 100) if total > 0 else 0
    
    wcag_fail_slides = set(i['slide'] for i in issues if "WCAG" in i.get('check', '') and i['result'] == 'FAIL')
    wcag_rate = ((total - len(wcag_fail_slides)) / total * 100) if total > 0 else 100

    grouped_issues = {}
    for issue in issues:
        s_num = issue['slide']
        if s_num not in grouped_issues: grouped_issues[s_num] = []
        grouped_issues[s_num].append(issue)

    slide_data_map = {}
    if hasattr(analyzer, 'slide_content_map'):
        for item in analyzer.slide_content_map:
            slide_data_map[item['slide_number']] = item

    # 5. RUN AI ANALYSIS (Batch)
    logger.info("Sending slide batch to AI Engine...")
    _user_log(report_id, "Packaging slide text for AI Context Engine...", agent="AI_BRIDGE")
    
    ai_engine = AIEngine()
    ai_results = ai_engine.analyze_batch(
        slides_list=analyzer.slide_content_map,
        total_slide_count=total
    )
    _user_log(report_id, f"AI Analysis Complete. Processed insights for {len(ai_results)} slides.", agent="AI_ENGINE")

    # 6. CONSTRUCT FINAL DATA
    final_score = round(wcag_rate, 1)
    
    full_data = {
        "summary": {
            "presentation_name": filename,
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
                "wcag_compliance_rate": final_score
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
        "slide_content": slide_data_map, 
        "ai_analysis": ai_results        
    }

    # 7. SAVE ARTIFACTS
    json_path = os.path.join(output_dir, 'audit_report.json')
    with open(json_path, "w", encoding='utf-8') as f:
        json.dump(full_data, f, indent=4)
    
    _user_log(report_id, "Generating HTML Reports...", agent="REPORT_GEN")
    
    generate_html_report(
        data=full_data, 
        output_path=os.path.join(output_dir, 'Printable Executive Summary.html')
    )
    
    generate_spa_report(
        data=full_data,
        output_path=os.path.join(output_dir, 'ID Workstation.html')
    )
    
    generate_ai_context_report(analyzer, output_dir)

    print(f"✅ Audit Complete. All assets generated in: {output_dir}")
    
    # --- LOGGING: USER STREAM SUCCESS ---
    _user_log(report_id, "Workflow Completed Successfully. All assets generated.", agent="ORCHESTRATOR")
    
    return json_path

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_output = f"output_{os.path.basename(sys.argv[1])}_{timestamp}"
        run_audit_slide(sys.argv[1], default_output)
    else:
        print("Usage: python -m modules.audit_slide.qa_tool [path_to_pptx]")
