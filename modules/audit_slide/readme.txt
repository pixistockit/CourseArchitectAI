Updated Project Structure
Based on your actual file system structure and the two new Python files (config.py and utils.py) we just created, here is the official structure going forward:

course-architect-ai/
└── audit-slide/
    ├── fixed_files/         # Location for automatically fixed PPTX files
    ├── input_files/         # Drop-off folder for PPTX files
    ├── output_reports/      # Location for generated QA reports
    ├── venv/                # Virtual Environment Directory
    ├── config.py            # Configuration settings (fonts, colors, rules)
    ├── qa_tool.py           # Main application entry point
    ├── requirements.txt     # Project dependencies
    └── utils.py             # WCAG math and general helper functions