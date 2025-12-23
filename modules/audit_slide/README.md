This is the definitive technical manual for the AuditSlide AI project. It synthesizes every architectural decision, logic engine, and library integration we have built during Phase 1.

This document is designed for the **Lead Developer** or **DevOps Engineer** maintaining the system.

---

# 🛠️ AuditSlide AI: Developer & Architecture Guide (v1.0)

**AuditSlide AI** is a headless, Python-based Quality Assurance engine designed to programmatically audit PowerPoint (`.pptx`) files. It enforces strict WCAG 2.1 AA Accessibility standards, Instructional Design (Gagné) frameworks, and Corporate Branding rules using advanced Z-Order geometry analysis and linguistic algorithms.

---

## 1. System Architecture

The project follows a modular "Engine-Config-Reporter" architecture to ensure scalability for future AI integration.

### 📂 Directory Structure

```text
course-architect-ai/
├── audit_slide/
│   ├── __init__.py          # Package initialization
│   ├── analyzer.py          # THE BRAIN: Z-order logic, contrast math, main loop
│   ├── config.py            # THE RULES: User-editable constants (Fonts, Colors, Grades)
│   ├── grammar_master.py    # THE LINGUIST: Textstat & Regex engines for clarity/tone
│   ├── qa_tool.py           # THE ORCHESTRATOR: File I/O, report aggregation, Jinja2 rendering
│   ├── utils.py             # THE TOOLKIT: Low-level hex/rgb conversion, contrast math
│   ├── input_files/         # Drop target for .pptx files
│   ├── output_reports/      # Destination for JSON, HTML, and TXT artifacts
│   └── templates/           # Jinja2 HTML templates
│       ├── report.html      # "Printable" Tiered Dashboard Template
│       └── report_spa.html  # "Workstation" Single Page App Template
├── venv/                    # Python Virtual Environment
└── requirements.txt         # Dependency manifest

```

---

## 2. Core Logic Engines

### 2.1. The Analyzer Engine (`analyzer.py`)

This is the core processor. It does not just "read" slides; it "perceives" them spatially.

* **Z-Order Raycasting:** Iterates through the visual stack (back-to-front) to determine the *actual* background color behind text, accounting for overlapping shapes.
* **Smart Exemption:** Checks slide indices against `EXEMPT_FIRST_SLIDE` and `EXEMPT_LAST_SLIDE` to skip title/legal slides while preserving index integrity.
* **Decorative Logic:** Parses raw XML (`cNvPr`) to respect the "Mark as Decorative" flag in PowerPoint, reducing false positives for Alt Text.

### 2.2. The Clarity Engine (`grammar_master.py`)

A specialized linguistic module separating logic for **Learners** vs. **Instructors**.

* **Algorithm:** Uses `textstat` to calculate **Flesch-Kincaid Grade Levels**.
* **Context Awareness:**
* *Slides:* Strict ceiling (default Grade 9).
* *Notes:* Lenient ceiling (Grade 14+) to allow for instructor expertise.


* **Regex Pattern Matching:** Scans for "Passive Voice" constructions (`to be` + `past participle`) and "Weasel Words" defined in the blacklist.

### 2.3. The Orchestrator (`qa_tool.py`)

Manages the pipeline and artifact generation.

* **Data Aggregation:** Compiles raw findings into a `summary` dictionary with high-level KPIs (WCAG %, Gagne Distribution).
* **Twin-Pack Generation:** Renders the data twice—once into `report.html` (Static/Print) and once into `report_spa.html` (Interactive App) using `jinja2`.
* **AI Prep:** Extracts raw text into a clean `.txt` stream for future LLM ingestion.

---

## 3. Development Setup

### 3.1. Prerequisites

* **OS:** Linux (Ubuntu/Debian recommended for server), macOS, or Windows.
* **Runtime:** Python 3.8 or higher.

### 3.2. Installation & Environment

Run these commands to set up the environment from scratch.

```bash
# 1. Navigate to project root
cd /home/ubuntu/course-architect-ai

# 2. Create Virtual Environment
python3 -m venv venv

# 3. Activate Environment
source venv/bin/activate

# 4. Install Dependencies
pip install python-pptx pyspellchecker textstat jinja2

# 5. Verify Installation
pip list

```

### 3.3. Project Initialization

Ensure the directory structure exists before running.

```bash
mkdir -p audit_slide/input_files
mkdir -p audit_slide/output_reports
mkdir -p audit_slide/templates
touch audit_slide/__init__.py

```

---

## 4. Execution Workflow

### Step 1: Input

Place the target PowerPoint file (`.pptx`) into the input directory:
`/course-architect-ai/audit_slide/input_files/`

### Step 2: Run Analysis

Execute the module from the project root:

```bash
python -m audit_slide.qa_tool

```

### Step 3: Retrieve Artifacts

The tool generates 4 files in `audit_slide/output_reports/`:

1. `*_QA_Data_*.json` (Raw Data for Dashboards)
2. `*_QA_Printable_*.html` (Glossy PDF-ready report)
3. `*_QA_Workstation_*.html` (Interactive Web App)
4. `*_AI_Context_*.txt` (LLM Context Dump)

---

## 5. Configuration (`config.py`)

All business logic is decoupled from code. Modify `config.py` to change audit behavior.

| Variable | Description |
| --- | --- |
| **`BRAND_COLORS_RGB`** | List of `(R, G, B)` tuples. Any object color not in this list flags a branding error. |
| **`TITLE_FONT_NAME`** | Strict font family for Titles (e.g., "Rockwell"). |
| **`TARGET_READING_GRADE_LEVEL`** | The ceiling for text complexity (Default: 9). |
| **`CLIENT_BLACKLIST`** | Dictionary of `{ "bad_word": "suggestion" }` for jargon policing. |
| **`EXEMPT_FIRST_SLIDE`** | `True/False`. Ignored during analysis to prevent Title Slide noise. |

---

## 6. Future Roadmap

### Phase 2: The Fixer Agent (Automated Remediation)

* **Goal:** Build `fixer.py` to ingest the JSON report and apply safe edits.
* **Scope:**
* Auto-change Fonts to Config Standard.
* Auto-adjust Font Size if `< Min`.
* Auto-darken text color to nearest WCAG-compliant shade (using `suggested_fix_color` from JSON).



### Phase 3: The Enterprise Dashboard

* **Goal:** Move from local HTML reports to a hosted Web Dashboard.
* **Stack:** Streamlit or React/Node.
* **Features:**
* Historical tracking of QA scores over time.
* "Click-to-Verify" buttons for Hyperlink testing (safely server-side).
* Multi-file batch upload and processing.



---

*© 2025 Course Architect AI - Internal Developer Documentation*