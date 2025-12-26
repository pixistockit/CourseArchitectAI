# /modules/audit_slide/prompts.py

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

    **PART 3: GAGNÉ EVENT & INTEGRITY CHECK**
    Check the "Instructional Activity" header against the slide content.
    - **PRACTICE vs GUIDANCE:** If learners are solving a problem (e.g., "Guided Learning" or "Scenario"), this is **Elicit Performance**, NOT "Provide Guidance". If mislabeled, warn the user in `tone_audit`.
    - **ASSESSMENT:** If the slide is a Quiz or Knowledge Check, it must be **Assess Performance**. If labeled "Guidance" or "Content", warn the user.
    - **VALID EVENTS:** Gain Attention, Inform Objectives, Stimulate Recall, Present Content, Provide Guidance, Elicit Performance, Provide Feedback, Assess Performance, Enhance Retention.

    Current Setting: **{scripting_level.upper()} SCRIPTING**
    {scripting_instruction}
    
    **PART 4: SLIDE BATCH DATA**
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
    **PART 5: REQUIRED OUTPUT FORMAT**
    Return a JSON List with this exact structure for every slide:
    [
        {
            "slide_number": <int>,
            "clarity_score": <int 1-10>,
            "tone_audit": "<string> (Include MISLABEL WARNING here if Gagné event is wrong)",
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


# --- NEW: Research Query Generator for Executive Summary ---
def get_summary_research_query_prompt(summary_data: dict) -> tuple:
    """
    Asks the AI to identify the biggest weak spot in the audit and 
    formulate a Vector DB query to find relevant ID theory.
    """
    context_str = json.dumps(summary_data, indent=2)
    
    system_prompt = "You are an Expert Instructional Designer. Your goal is to research best practices for specific course deficits."
    
    user_message = f"""
    Analyze the following audit metrics:
    {context_str}
    
    **TASK:**
    1. Identify the SINGLE most critical issue (e.g., Low WCAG Score, Low Active Learning/Gagne Interactivity, or Poor Pacing).
    2. Write a search query to find "Adult Learning Theory" and "Instructional Design Best Practices" specifically for solving that issue.
    
    **OUTPUT:** Return ONLY the search query string. (e.g., "adult learning theory strategies for improving accessibility and wcag compliance in elearning")
    """
    return system_prompt, user_message

# --- UPDATED: Executive Summary Agent (Now with Context) ---
# --- NEW: Research Query Generator for Executive Summary ---
def get_summary_research_query_prompt(summary_data: dict) -> tuple:
    """
    Asks the AI to identify the biggest weak spot in the audit and 
    formulate a Vector DB query to find relevant ID theory.
    """
    context_str = json.dumps(summary_data, indent=2)
    
    system_prompt = "You are an Expert Instructional Designer. Your goal is to research best practices for specific course deficits."
    
    user_message = f"""
    Analyze the following audit metrics:
    {context_str}
    
    **TASK:**
    1. Identify the SINGLE most critical issue (e.g., Low WCAG Score, Low Active Learning/Gagne Interactivity, or Poor Pacing).
    2. Write a search query to find "Adult Learning Theory" and "Instructional Design Best Practices" specifically for solving that issue.
    
    **OUTPUT:** Return ONLY the search query string. (e.g., "adult learning theory strategies for improving accessibility and wcag compliance in elearning")
    """
    return system_prompt, user_message

# --- UPDATED: Executive Summary Agent (Full Context) ---
def get_executive_summary_prompt(summary_data: dict, research_context: str = "", transcript_context: str = "") -> tuple:
    """
    Generates the HTML dashboard, grounded in Research AND actual Slide Content.
    """
    context_str = json.dumps(summary_data, indent=2)
    
    system_prompt = """
    You are a Senior Instructional Design Consultant.
    Your goal is to generate the HTML content for an "Executive Insight Dashboard".
    
    **OUTPUT FORMAT:**
    Return ONLY raw HTML. Do not include markdown code blocks.
    Structure: 4 `div` elements with class `exec-card`.
    
    **CRITICAL ANALYSIS RULES (BENCHMARKS):**
    1. **PEDAGOGY (Gagne Events):**
       - **Gain Attention / Objectives:** 2-10% is NORMAL. Do NOT flag this as "critically low". It is a transitional event.
       - **Present Content:** 30-50% is Balanced. >60% is "Lecture-Heavy".
       - **Elicit Performance / Feedback:** >30% is EXCELLENT (High Active Learning). Praise this if found.
       
    2. **PACING:**
       - If "Projected" vs "Planned" variance is < 15%, praise the "Accurate Scoping".
       - Only flag pacing if the variance is significant (>20%).
       
    3. **COMPLIANCE (Health):**
       - WCAG < 70% is Critical. Be firm here.
    
    **TONE:**
    Constructive, specific, and professional.
    """
    
    user_message = f"""
    **PART 1: AUDIT METRICS**
    {context_str}
    
    **PART 2: ID RESEARCH (Best Practices)**
    {research_context}
    
    **PART 3: COURSE CONTENT PREVIEW (Transcript)**
    {transcript_context}
    
    **TASK:**
    Generate 4 HTML cards (`exec-card`) based on the data, research, and content.
    
    **HTML STRUCTURE PER CARD:**
    <div class="exec-card">
        <div class="card-icon"><i class="[ICON_CLASS]"></i></div>
        <h4>[SECTION TITLE]</h4>
        <p>[1-Sentence Summary]</p>
        <ul>
            <li>[Specific Insight 1]</li>
            <li>[Specific Insight 2]</li>
            <li>[Specific Insight 3]</li>
        </ul>
    </div>
    
    **THE 4 SECTIONS:**
    1. **Project Health:** Focus on WCAG/Errors. (Icon: fas fa-heartbeat)
    2. **Pedagogy & Engagement:** Focus on Gagne/Interactivity. **Reference specific topics from the Transcript** (e.g., "The section on 'Binary'...") to prove you read the content. (Icon: fas fa-brain)
    3. **Content Quality:** Focus on Readability/Tone. Use the Research to explain *why* readability matters. (Icon: fas fa-feather-alt)
    4. **Delivery & Pacing:** Compare Projected vs Planned time. (Icon: fas fa-stopwatch)
    """
    return system_prompt, user_message
