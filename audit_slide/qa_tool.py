# AuditSlide AI - Main Tool Entry Point (Hybrid V4 - AI Enabled)
# Updates: Multi-Master Detection, Content Extraction for AI Agent, Auto-Cleanup
import os
import json
import logging
import shutil  # Required for deleting old folders
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from pptx import Presentation # Needed for Master Slide Count

# Import the Golden Logic
try:
    from .analyzer import PptxAnalyzer
    from audit_slide.ai_engine import AIEngine
except ImportError as e:
    print(f"CRITICAL ERROR: Missing modules. {e}")
    raise e

# --- SETUP LOGGING ---
LOG_DIR = 'data/logs'
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, 'audit_debug.log')

logger = logging.getLogger('audit_tool')
logger.setLevel(logging.DEBUG)
if logger.hasHandlers(): logger.handlers.clear()
fh = logging.FileHandler(log_file, mode='w')
fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(fh)

def _enforce_single_session(active_dir):
    """
    Sustainability Fix: Delete all report folders EXCEPT the current active one.
    This prevents /data/reports from ballooning with duplicate uploads.
    """
    try:
        # active_dir is .../data/reports/<uuid>
        reports_root = os.path.dirname(active_dir)
        
        # Safety Check: Ensure we are actually operating in the 'reports' folder
        if os.path.basename(reports_root) != 'reports':
            logger.warning(f"Cleanup Skipped: Parent dir is '{os.path.basename(reports_root)}', expected 'reports'")
            return

        # Iterate through all folders in data/reports/
        for item in os.listdir(reports_root):
            item_path = os.path.join(reports_root, item)
            
            # If it is a directory and NOT our current active session -> DELETE IT
            if os.path.isdir(item_path) and item_path != active_dir:
                logger.info(f"🧹 Auto-Cleaning Old Session: {item}")
                shutil.rmtree(item_path) # Recursively delete the folder and files
                
    except Exception as e:
        logger.warning(f"⚠️ Auto-Cleanup Warning: {e}")

def run_audit_slide(pptx_path, output_dir=None):
    logger.info(f"🚀 STARTING AUDIT (AI Enabled): {os.path.basename(pptx_path)}")
    print(f"🚀 Starting Forensic Audit: {os.path.basename(pptx_path)}")
    
    if output_dir: 
        target_dir = output_dir
    else: 
        target_dir = os.path.dirname(os.path.abspath(pptx_path))
        
    base_name = os.path.splitext(os.path.basename(pptx_path))[0]
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    # --- ACTIVATE SINGLE SESSION MODE ---
    # This runs immediately, wiping all old UUID folders before we start work.
    _enforce_single_session(target_dir)
    # ------------------------------------

    # 1. CHECK MASTER SLIDE COUNT (New Feature)
    master_count = 1
    try:
        # We open briefly just to check structure before deep analysis
        temp_prs = Presentation(pptx_path)
        master_count = len(temp_prs.slide_masters)
        logger.info(f"Detected {master_count} Master Slides.")
    except Exception as e:
        logger.warning(f"Could not count masters: {e}")

    # 2. RUN ANALYSIS (The Source of Truth)
    try:
        logger.info("Initializing PptxAnalyzer...")
        analyzer = PptxAnalyzer(pptx_path)
        issues = analyzer.run_analysis()
        logger.info(f"Analysis Complete. Found {len(issues)} raw issues.")
    except Exception as e:
        logger.error(f"❌ Analysis Failed: {e}", exc_info=True)
        raise e

    # 3. PREPARE DATA
    total = analyzer.total_slides_checked
    gagne = analyzer.gagne_metrics
    
    pct_present = (gagne["Present Content"] / total * 100) if total > 0 else 0
    pct_elicit = (gagne["Elicit Performance"] / total * 100) if total > 0 else 0
    
    wcag_fail_slides = set(i['slide'] for i in issues if "WCAG" in i.get('check', '') and i['result'] == 'FAIL')
    wcag_rate = ((total - len(wcag_fail_slides)) / total * 100) if total > 0 else 100

    # 3a. Group Issues
    grouped_issues = {}
    for issue in issues:
        s_num = issue['slide']
        if s_num not in grouped_issues: grouped_issues[s_num] = []
        grouped_issues[s_num].append(issue)

    # 3b. Extract Slide Content for AI Agent (Critical Update)
    slide_data_map = {}
    if hasattr(analyzer, 'slide_content_map'):
        for item in analyzer.slide_content_map:
            slide_data_map[item['slide_number']] = item
    else:
        logger.warning("Analyzer missing slide_content_map. AI Agent will not function.")

    full_data = {
        "summary": {
            "presentation_name": os.path.basename(pptx_path),
            "date_generated": datetime.now().isoformat(),
            "master_slide_count": master_count, # <-- Added for UX Warning
            "total_slides_checked": total,
            "total_errors": len([i for i in issues if i.get('result') in ('FAIL', 'WARNING')]),
            "wcag_fails": len(wcag_fail_slides),
            "manual_reviews": len([i for i in issues if i.get('result') == 'MANUAL REVIEW']),
            "executive_metrics": {
                "slides_present_content": f"{gagne['Present Content']} ({round(pct_present, 1)}%)",
                "slides_elicit_performance": f"{gagne['Elicit Performance']} ({round(pct_elicit, 1)}%)",
                "other_slides": gagne['Other'],
                "wcag_compliance_rate": round(wcag_rate, 1)
            },
            "content_metrics": {
                "reading_complexity_fails": len([i for i in issues if "Reading Level" in i.get('check', '')]),
                "passive_voice_count": len([i for i in issues if "Tone" in i.get('check', '') and "Passive" in i.get('details', '')]),
                "jargon_count": len([i for i in issues if "Jargon" in i.get('check', '')])
            },
            "pacing_metrics": analyzer.pacing_data
        },
        "detailed_issues": issues,
        "grouped_issues": grouped_issues,
        "slide_content": slide_data_map # <-- Added for AI Agent
    }

    # 4. GENERATE ARTIFACTS
    
    # A. JSON Data
    json_path = os.path.join(target_dir, f"audit_report.json")
    with open(json_path, "w") as f: json.dump(full_data, f, indent=4)
    logger.info(f"Saved JSON: {json_path}")

    # B. RENDER REPORTS (Universal Injection Method)
    # We switch to .replace() for both to ensure the large JSON object 
    # is available to the client-side JS for the AI Agent.
    try:
        template_dir = os.path.join(os.path.dirname(__file__), 'templates')
        
        # Prepare the JS Data String
        json_str = json.dumps(full_data, indent=2)
        js_injection = f"const auditData = {json_str};"

        def inject_and_save(template_name, output_name):
            with open(os.path.join(template_dir, template_name), 'r') as f:
                html_content = f.read()
            
            final_html = html_content.replace('/* INSERT_JSON_HERE */', js_injection)
            
            with open(os.path.join(target_dir, output_name), 'w') as f:
                f.write(final_html)
            logger.info(f"Generated {output_name}")

        # 1. Executive Summary
        inject_and_save('report.html', 'Printable Executive Summary.html')

        # 2. ID Workstation
        inject_and_save('report_spa.html', 'ID Workstation.html')

    except Exception as e:
        logger.error(f"Report Generation Failed: {e}")
        print(f"Report Gen Error: {e}")

    # C. AI Context (Optional)
    try:
        from audit_slide.report_generator import generate_ai_context_report
        generate_ai_context_report(analyzer, target_dir)
    except: pass

    print(f"✅ Audit Complete. Results in: {target_dir}")
    print(f"📄 Logs: {log_file}")
    
    return json_path

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        run_audit_slide(sys.argv[1])
    else:
        print("Usage: python -m audit_slide.qa_tool [path_to_pptx]")