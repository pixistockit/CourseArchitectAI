import json

# --- Agent 1: The Manager (Topic Identifier) ---
def get_batch_manager_prompt(slides_meta: list) -> tuple:
    outline_text = ""
    for s in slides_meta:
        layout = s.get('visual_context', {}).get('layout', 'Unknown')
        outline_text += f"Slide {s.get('slide_number')}: {s.get('title', 'Untitled')} [{layout}]\n"

    system_prompt = "You are a Curriculum Architect. Your goal is to identify the core pedagogical topic of a course section."
    
    user_message = f"""
    Review this Course Outline (Slide Titles & Layouts):
    {outline_text}
    TASK: Summarize the primary instructional topic and learning strategy of this section in 1 concise sentence.
    """
    return system_prompt, user_message

# --- Agent 2: The Researcher (Query Optimizer) ---
def get_research_query_prompt(topic_summary: str) -> tuple:
    system_prompt = """
    You are an Expert Instructional Design Researcher.
    Your goal is to craft a targeted search query for a Vector Database containing the "Science of Instruction" knowledge base.
    
    **THEORETICAL FRAMEWORK REQUIREMENTS:**
    You must construct a query that connects the user's TOPIC to specific principles from:
    1. **Gagné's 9 Events of Instruction**
    2. **Mayer's 12 Principles of Multimedia Learning**
    3. **Adult Learning Theory (Andragogy)**
    
    **OUTPUT RULE:**
    Return ONLY the optimized search query string. Do not explain.
    """
    
    user_message = f"""
    **COURSE TOPIC:**
    "{topic_summary}"
    
    **TASK:**
    Convert this topic into a search query that looks for the most relevant ID best practices for this specific type of content.
    """
    return system_prompt, user_message

# --- Agent 3: The Executor (Batch Analyst) ---
def get_batch_executor_prompt(slides_batch: list, research_context: str, ai_context_file: str, config: dict, brand_config: dict) -> tuple:
    """
    Generates the Mega-Prompt for Agent 3.
    """
    scripting_level = config.get('notes_scripting_level', 'Light')
    
    scripting_instruction = ""
    if scripting_level == 'Basic':
        scripting_instruction = "**NOTES REWRITE RULES (BASIC):** Start bullets with verbs. Directive tone. No fluff."
    elif scripting_level == 'Heavy':
        scripting_instruction = "**NOTES REWRITE RULES (HEAVY):** Write word-for-word script. Conversational and thorough."
    else: # Light
        scripting_instruction = "**NOTES REWRITE RULES (LIGHT):** Conversational but scannable. Spoken tone. Leave room for expertise."

    headers = brand_config.get('required_headers', [])
    header_str = ", ".join([f"'{h}'" for h in headers]) if headers else "None defined"

    system_prompt = f"""
    {ai_context_file}
    
    **CRITICAL OUTPUT INSTRUCTION:**
    You are analyzing a BATCH of slides. You must return a strict JSON LIST of objects.
    """

    user_message = f"""
    **PART 1: INSTRUCTIONAL DESIGN RESEARCH**
    Use this research to guide your critique:
    {research_context}
    
    **PART 2: SCRIPTING & ANALYSIS RULES**
    1. **Analyze Notes First:** Evaluate the existing Speaker Notes against the {scripting_level} standard.
    2. **NOISE REDUCTION:** - If the existing notes are already clear (Clarity > 7) and match the tone, DO NOT rewrite them. Return `null` or empty string for `suggested_notes`.
       - If the original notes are empty, return `null` unless the slide visual is complex and requires explanation.
       - ONLY rewrite if there is a genuine need for improvement (e.g., too brief, wrong tone, confusing).
    3. **FORMATTING:** - If you rewrite, you MUST use the exact same bullet point style (e.g., hyphens, dots) as the original.
       - Preserve all "CLICK" cues exactly as they appear in the original text (same position, same format).
    4. **HEADERS:** Preserve these headers if found: [{header_str}].

    Current Setting: **{scripting_level.upper()} SCRIPTING**
    {scripting_instruction}
    
    **PART 3: SLIDE BATCH DATA**
    Analyze the following slides:
    """
    
    for slide in slides_batch:
        s_num = slide.get('slide_number')
        txt = slide.get('full_text', slide.get('text', ''))
        notes = slide.get('notes', '')
        vis = slide.get('visual_context', {})
        vis_str = f"Layout: {vis.get('layout', 'N/A')}, Images: {vis.get('images', 0)}"
        
        user_message += f"""
        --- SLIDE {s_num} [{vis_str}] ---
        ON-SCREEN TEXT: {txt}
        SPEAKER NOTES: {notes}
        """

    user_message += """
    **PART 4: REQUIRED OUTPUT FORMAT**
    Return a JSON List with this exact structure for every slide:
    [
        {
            "slide_number": <int>,
            "clarity_score": <int 1-10>,
            "tone_audit": "<string>",
            "suggested_notes": <string OR null (Only if rewrite is needed)>,
            "remediation": {
                "option_a": { "label": "Polish Visuals", "text": "<string>" },
                "option_b": { "label": "Simplify Visuals", "text": "<string>" }
            }
        },
        ...
    ]
    """
    
    return system_prompt, user_message