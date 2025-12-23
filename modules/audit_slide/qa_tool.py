# /modules/audit_slide/qa_tool.py - DEFINITIVE VERSION

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

# --- LOGGING SETUP ---
logger = logging.getLogger('audit_tool')

def _enforce_single_session(active_dir):
    """
    Sustainability Fix: Delete all report folders EXCEPT the current active one.
    """
    try:
        reports_root = os.path.dirname(active_dir)
        if os.path.basename(reports_root) != 'reports':
            logger.warning(f"Cleanup Skipped: Parent dir is '{os.path.basename(reports_root)}', expected 'reports'")
            return

        for item in os.listdir(reports_root):
            item_path = os.path.join(reports_root, item)
            if os.path.isdir(item_path) and item_path != active_dir:
                logger.info(f"🧹 Auto-Cleaning Old Session: {item}")
                shutil.rmtree(item_path)
    except Exception as e:
        logger.warning(f"⚠️ Auto-Cleanup Warning: {e}")

def run_audit_slide(pptx_path, output_dir):
    """
    Main function to run the full forensic audit on a PowerPoint file.
    This is the complete orchestration logic, merging original features with the new architecture.
    """
    logger.info(f"🚀 STARTING AUDIT: {os.path.basename(pptx_path)}")
    os.makedirs(output_dir, exist_ok=True)
    
    # --- FEATURE RESTORED: Activate Single Session Mode ---
    _enforce_single_session(output_dir)

    # 1. FEATURE RESTORED: Check Master Slide Count
    master_count = 1
    try:
        temp_prs = Presentation(pptx_path)
        master_count = len(temp_prs.slide_masters)
        logger.info(f"Detected {master_count} Master Slides.")
    except Exception as e:
        logger.warning(f"Could not count masters: {e}")

    # 2. Initialize Engines
    analyzer = PptxAnalyzer(pptx_path)
    ai_engine = AIEngine()
    
    # 3. Perform Forensic Analysis
    logger.info("Running forensic analysis on presentation...")
    analyzer.run_full_analysis()
    analyzer.summary_data['master_slide_count'] = master_count # Add master count to summary
    logger.info("Forensic analysis complete.")
    
    # 4. Prepare the base data structure from the analyzer
    analysis_data = {
        'summary': analyzer.summary_data,
        'slides': analyzer.slide_content_map,
        'forensic_data': analyzer.forensic_data
    }
    
    # 5. Run the full multi-agent AI Analysis Batch
    logger.info("Sending slide batch to AI Engine for multi-agent analysis...")
    ai_results = ai_engine.analyze_batch(
        slides_list=analyzer.slide_content_map,
        total_slide_count=analyzer.total_slides_checked
    )
    logger.info("AI analysis batch complete.")
    
    # 6. Merge AI Results into the Main Data Object
    final_report_data = analysis_data
    final_report_data['ai_analysis'] = ai_results

    # 7. Save the master JSON report (The single source of truth)
    json_path = os.path.join(output_dir, 'audit_report.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(final_report_data, f, indent=4)
    logger.info(f"✅ Master audit_report.json generated: {json_path}")
    
    # 8. Generate all derivative report assets using the modular report_generator
    logger.info("Generating static report assets...")
    
    generate_html_report(
        data=final_report_data,
        output_path=os.path.join(output_dir, 'Printable Executive Summary.html')
    )
    
    # Generate the static SPA for legacy purposes or as a backup
    generate_spa_report(
        data=final_report_data,
        output_path=os.path.join(output_dir, 'ID Workstation.html')
    )
    
    generate_ai_context_report(analyzer, output_dir)

    print(f"✅ Audit Complete. All assets generated in: {output_dir}")
    logger.info(f"✅ Audit Complete. All assets generated in: {output_dir}")
    
    return json_path

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_output = f"output_{os.path.basename(sys.argv[1])}_{timestamp}"
        run_audit_slide(sys.argv[1], default_output)
    else:
        print("Usage: python -m modules.audit_slide.qa_tool [path_to_pptx]")
