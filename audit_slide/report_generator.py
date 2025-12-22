import os
import json

def generate_html_report(data, output_path):
    """Generates the static Executive Summary (report.html)."""
    _inject_data_into_template('report.html', data, output_path)

def generate_spa_report(data, output_path):
    """Generates the static ID Workstation (report_spa.html)."""
    _inject_data_into_template('report_spa.html', data, output_path)

def generate_ai_context_report(analyzer, target_dir):
    """
    Generates the text transcript of the presentation.
    RENAMED: project_transcript.txt (was AI_Context.txt)
    """
    # FIX: New filename to avoid confusion with System Instructions
    txt_path = os.path.join(target_dir, "project_transcript.txt")
    
    try:
        with open(txt_path, "w", encoding="utf-8") as f:
            pres_name = getattr(analyzer, 'presentation_name', "Unknown Presentation")
            f.write(f"--- COURSE AUDIT TRANSCRIPT: {os.path.basename(pres_name)} ---\n")
            f.write(f"Total Slides: {analyzer.total_slides_checked}\n")
            f.write("This file represents the raw text and visual context extracted for analysis.\n\n")
            
            source_data = getattr(analyzer, 'slide_content_map', [])
            
            if not source_data:
                print("⚠️ Warning: No slide content found in analyzer.slide_content_map")

            for slide in source_data:
                s_num = slide.get('slide_number', slide.get('slide', 'N/A'))
                vis = slide.get('visual_context', {})
                layout = vis.get('layout', 'Unknown')
                imgs = vis.get('images', 0)
                
                f.write(f"=== SLIDE {s_num} [Layout: {layout} | Images: {imgs}] ===\n")
                f.write(f"TITLE: {slide.get('title', '')}\n")
                
                text_content = slide.get('full_text', slide.get('content', ''))
                f.write(f"TEXT CONTENT:\n{text_content}\n")
                
                notes = slide.get('notes', '')
                if notes:
                    f.write(f"\nSPEAKER NOTES:\n{notes}\n")
                
                f.write("-" * 40 + "\n")
                
        print(f"✅ Project Transcript generated: {txt_path}")
    except Exception as e:
        print(f"❌ Error generating Transcript: {e}")
    return txt_path

def _inject_data_into_template(template_name, data, output_path):
    template_dir = os.path.join(os.path.dirname(__file__), 'templates')
    template_path = os.path.join(template_dir, template_name)
    
    if not os.path.exists(template_path):
        print(f"❌ Error: Template {template_name} not found in {template_dir}")
        return

    with open(template_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    data_json = json.dumps(data)
    injection = f"const auditData = {data_json};"
    final_html = html_content.replace('/* INSERT_JSON_HERE */', injection)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(final_html)
    print(f"✅ Static report generated: {os.path.basename(output_path)}")