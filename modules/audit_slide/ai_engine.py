# /modules/audit_slide/ai_engine.py

import os
import json
import re
import time
import logging
import csv
import requests
from datetime import datetime

# --- Provider SDKs ---
try:
    # FIXED: Use the correct standard library namespace
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("⚠️ Google Generative AI SDK not found. Install via: pip install google-generativeai")

from openai import OpenAI
import anthropic
from groq import Groq
from mistralai.client import MistralClient
import boto3

# --- RELATIVE IMPORTS ---
from . import config as CFG
# FIXED: Added new prompt function to imports
from .prompts import get_batch_manager_prompt, get_batch_executor_prompt, get_research_query_prompt, get_executive_summary_prompt, get_summary_research_query_prompt

# --- LOGGING SETUP ---
sys_logger = logging.getLogger('platform_system')
ai_logger = logging.getLogger('audit_tool')

# --- MASTER MODEL CONFIGURATION ---
MODEL_MAPPING = {
    "AGENT_1": {
        "ANTHROPIC": "claude-3-sonnet-20240229",
        "AWS_BEDROCK": "anthropic.claude-3-haiku-20240307-v1:0",
        "GEMINI": "gemini-2.5-flash-lite", 
        "GROQ": "openai/gpt-oss-20b",
        "MISTRALAI": "mistral-small-latest",
        "OPENAI": "gpt-5-nano"
    },
    "AGENT_2": {
        "ANTHROPIC": None,
        "AWS_BEDROCK": "anthropic.claude-3-sonnet-20240229-v1:0",
        "GEMINI": "gemini-2.5-flash",
        "GROQ": "llama-3.3-70b-versatile",
        "MISTRALAI": "magistral-small-latest",
        "OPENAI": "gpt-4.1-mini"
    },
    "AGENT_3": {
        "ANTHROPIC": None,
        "AWS_BEDROCK": "amazon.nova-pro-v1:0",
        "GEMINI": "gemini-2.5-flash",
        "GROQ": "llama-3.3-70b-versatile",
        "MISTRALAI": "magistral-medium-latest",
        "OPENAI": "gpt-5"
    }
}

class TokenTracker:
    def __init__(self):
        self.csv_file = os.path.join('data', 'logs', 'token_ledger.csv')
        self._init_csv()

    def _init_csv(self):
        if not os.path.exists(self.csv_file):
            try:
                with open(self.csv_file, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['Timestamp', 'Agent', 'Provider', 'Model', 'Input_Tokens', 'Output_Tokens', 'Latency_Sec', 'Status'])
            except Exception: pass

    def log_usage(self, agent, provider, model, input_tok, output_tok, latency, status="SUCCESS"):
        try:
            with open(self.csv_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([datetime.now().isoformat(), agent, provider, model, input_tok, output_tok, round(latency, 2), status])
        except Exception as e:
            sys_logger.error(f"Failed to log tokens: {e}")

class AIEngine:
    def __init__(self):
        self.config, self.brand_config = self._load_configs()
        self.tracker = TokenTracker()
        self.clients = {}
        # Ensure directory exists before logger init
        os.makedirs(os.path.join('data', 'logs'), exist_ok=True)
        self._setup_debug_logging() 
        self.ai_context_prompt = self._load_context_file()

    def _setup_debug_logging(self):
        """Forces a FileHandler onto the audit_tool logger."""
        log_path = os.path.join('data', 'logs', 'vector_db_debug.log')
        # Remove existing handlers to prevent duplication/locking
        if ai_logger.hasHandlers():
            ai_logger.handlers.clear()
            
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        ai_logger.addHandler(file_handler)
        ai_logger.setLevel(logging.INFO)

    def _load_configs(self):
        llm_cfg_path = os.path.join('data', 'config', 'llm_config.json')
        brand_cfg_path = os.path.join('data', 'config', 'brand_config.json')
        
        llm_cfg = {}
        if os.path.exists(llm_cfg_path):
            with open(llm_cfg_path, 'r') as f: llm_cfg = json.load(f)
        
        brand_cfg = {}
        if os.path.exists(brand_cfg_path):
            with open(brand_cfg_path, 'r') as f: brand_cfg = json.load(f)
            
        return llm_cfg, brand_cfg
    
    # --- UPDATED: CONTEXT-AWARE EXECUTIVE SUMMARY ---
    def generate_executive_summary(self, summary_data, report_id):
        """
        Generates a global summary enriched with Vector DB research AND Project Transcript.
        """
        ai_logger.info("Step 1: Preparing Context for Executive Summary...")
        
        # 1. Load Transcript for Content Context
        transcript_path = os.path.join('data', 'reports', report_id, 'project_transcript.txt')
        transcript_context = ""
        if os.path.exists(transcript_path):
            try:
                with open(transcript_path, 'r') as f:
                    # Read first 15,000 chars to avoid token limits but get good context
                    transcript_context = f.read(15000) 
                ai_logger.info(f"Loaded Transcript Context ({len(transcript_context)} chars)")
            except Exception as e:
                ai_logger.error(f"Failed to read transcript: {e}")

        # 2. Ask AI to generate query based on Data + Transcript
        q_sys, q_user = get_summary_research_query_prompt(summary_data)
        search_query = self.execute_agent("AGENT_2", q_user, q_sys)
        ai_logger.info(f" -> Generated Search Query: {search_query}")
        
        # 3. Query Vector DB
        research_context = self.query_vector_db(search_query)
        
        # 4. Generate Final Report with ALL Contexts
        ai_logger.info("Step 2: Generating Final Executive Dashboard...")
        
        # We need to update prompts.py to accept transcript_context
        sys_prompt, user_msg = get_executive_summary_prompt(summary_data, research_context, transcript_context)
        
        summary_text = self.execute_agent("AGENT_2", user_msg, sys_prompt)
        
        # Clean up markdown code blocks
        clean_text = summary_text.replace("```html", "").replace("```", "").strip()
        
        # FIX: Ensure section headers are wrapped in <h4> if the AI forgot them
        # This catches lines like "PROJECT HEALTH" and wraps them
        lines = clean_text.split('\n')
        final_lines = []
        for line in lines:
            stripped = line.strip()
            # If line is all caps, short, and not already wrapped
            if stripped.isupper() and len(stripped) < 50 and not stripped.startswith('<h'):
                final_lines.append(f"<h4>{stripped}</h4>")
            else:
                final_lines.append(line)
        
        return "\n".join(final_lines)

    def _load_context_file(self):
        ctx_path = os.path.join('modules', 'audit_slide', 'assets', 'system_instruction.txt')
        if os.path.exists(ctx_path):
            try:
                with open(ctx_path, 'r') as f: return f.read().strip()
            except Exception as e: sys_logger.error(f"Failed to load system_instruction.txt: {e}")
        return "You are an Instructional Design Auditor. Analyze content for clarity, brevity, and impact."

    def get_client(self, provider):
        if provider in self.clients: return self.clients[provider]
        try:
            client = None
            if provider == "GEMINI":
                # FIXED: Correct initialization for standard Google SDK
                api_key = self.config.get('gemini_api_key')
                if api_key and GEMINI_AVAILABLE:
                    genai.configure(api_key=api_key)
                    client = genai
                else:
                    sys_logger.error("Gemini Key Missing or SDK not found")
            
            elif provider == "OPENAI":
                client = OpenAI(api_key=self.config.get('openai_api_key'))
            elif provider == "ANTHROPIC":
                client = anthropic.Anthropic(api_key=self.config.get('anthropic_api_key'))
            elif provider == "GROQ":
                client = Groq(api_key=self.config.get('groq_api_key'))
            elif provider == "MISTRALAI":
                client = MistralClient(api_key=self.config.get('mistral_api_key'))
            elif provider == "AWS_BEDROCK":
                client = boto3.client('bedrock-runtime', 
                                    aws_access_key_id=self.config.get('aws_access_key'), 
                                    aws_secret_access_key=self.config.get('aws_secret_key'), 
                                    region_name=self.config.get('aws_region', 'us-east-1'))
            if client:
                self.clients[provider] = client
                return client
        except Exception as e:
            sys_logger.error(f"Provider {provider} init failed: {e}")
        return None

    def determine_provider(self, agent_role):
        config_key = f"{agent_role.lower()}_provider"
        selected = self.config.get(config_key, "SAME_AS_AGENT_1")
        if selected == "SAME_AS_AGENT_1":
            selected = self.config.get("agent_1_provider", "GEMINI")
        return selected
    
    # --- VECTOR DATABASE LOOKUP ---
    def query_vector_db(self, query_text):
        # 1. Credentials
        account_id = str(getattr(CFG, 'cf_account_id', "")).strip()
        v_token = str(getattr(CFG, 'cf_vectorize_token', "")).strip()
        ai_token = str(getattr(CFG, 'cf_workers_ai_token', "")).strip()
        index_name = getattr(CFG, 'cf_index_name', "instructional-cadence-kb")

        # 2. Safety Check
        if not isinstance(query_text, str):
            ai_logger.warning(f"Vector DB received non-string query: {type(query_text)}. Converting.")
            query_text = str(query_text)

        if not all([account_id, v_token, ai_token]):
            ai_logger.info("AGENT_2: Credentials missing. Skipping DB.")
            return "Standard Best Practice: Maintain clarity."

        try:
            # Log the exact query
            ai_logger.info(f"AGENT_2: Embedding query: '{query_text}'")
            
            # 3. Generate Embedding
            emb_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/baai/bge-small-en-v1.5"
            emb_res = requests.post(emb_url, headers={"Authorization": f"Bearer {ai_token}"}, json={"text": [query_text]})
            
            if emb_res.status_code != 200: 
                ai_logger.error(f"Vector DB Embedding Error: {emb_res.text}")
                return "Vector DB Error: Embedding failed."
            
            vector = emb_res.json()['result']['data'][0]

            # 4. Query Index
            query_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/vectorize/indexes/{index_name}/query"
            query_res = requests.post(query_url, headers={"Authorization": f"Bearer {v_token}"}, json={"vector": vector, "topK": 5, "returnMetadata": True})
            
            if query_res.status_code != 200:
                ai_logger.error(f"Vector DB Query Error: {query_res.text}")
                return "Vector DB Query Failed."

            # 5. Extract Matches
            matches = query_res.json().get('result', {}).get('matches', [])
            if not matches: 
                ai_logger.info("AGENT_2: No matches found in DB.")
                return "No KB matches found."

            context = "\n".join([m.get('metadata', {}).get('text', '') for m in matches if m.get('metadata')])
            
            # --- FIXED: Log FULL context for troubleshooting ---
            ai_logger.info(f"AGENT_2 SUCCESS: Retrieved {len(matches)} nodes.")
            ai_logger.info(f"=== FULL VECTOR CONTEXT START ===\n{context}\n=== FULL VECTOR CONTEXT END ===")
            
            return context

        except Exception as e:
            ai_logger.error(f"AGENT_2 FAILURE: {e}")
            return "Standard Best Practice."

    def analyze_batch(self, slides_list, total_slide_count=0):
        if not slides_list: return []
        
        active_slides, skipped_results = self._filter_exempt_slides(slides_list, total_slide_count)
        if not active_slides: return skipped_results

        ai_logger.info(f"STARTING SMART BATCH: {len(active_slides)} Slides (Skipped {len(skipped_results)})")
        
        # --- BATCH PIPELINE ---
        # 1. Agent 1 (Manager): Analyze Outline
        sys_prompt, user_msg = get_batch_manager_prompt(active_slides)
        ai_logger.info("BATCH STEP 1: Agent 1 analyzing outline...")
        topic_summary = self.execute_agent("AGENT_1", user_msg, sys_prompt)
        ai_logger.info(f" -> Topic: {topic_summary}")

        # 2. Agent 2 (Researcher): Generate Query & Lookup
        sys_prompt_a2, user_msg_a2 = get_research_query_prompt(topic_summary)
        ai_logger.info("BATCH STEP 2A: Agent 2 generating optimized query...")
        optimized_query = self.execute_agent("AGENT_2", user_msg_a2, sys_prompt_a2)
        ai_logger.info(f" -> Optimized Query: {optimized_query}")

        ai_logger.info("BATCH STEP 2B: Consulting Vector DB...")
        research_context = self.query_vector_db(optimized_query)
        
        # 3. Agent 3 (Executor): Full Analysis
        sys_prompt, user_msg = get_batch_executor_prompt(active_slides, research_context, self.ai_context_prompt, self.config, self.brand_config)
        ai_logger.info(f"BATCH STEP 3: Agent 3 executing full analysis...")
        raw_response = self.execute_agent("AGENT_3", user_msg, sys_prompt)
        
        # 4. Merge Results
        final_results = skipped_results
        try:
            ai_results = self._clean_json(raw_response)
            if isinstance(ai_results, list): final_results.extend(ai_results)
            final_results.sort(key=lambda x: x.get('slide_number', 0))
            return final_results
        except Exception as e:
            ai_logger.error(f"Batch Parse Error: {e}")
            return final_results

    def _filter_exempt_slides(self, slides_list, total_slide_count):
        active_slides, skipped_results = [], []
        exempt_first = self.brand_config.get('exempt_first_slide', False)
        exempt_last = self.brand_config.get('exempt_last_slide', False)
        specific_raw = self.brand_config.get('exempt_specific_slides', "")
        exempt_specific = [int(x.strip()) for x in str(specific_raw).split(',') if x.strip().isdigit()] if specific_raw else []

        for s in slides_list:
            s_num = s.get('slide_number')
            if (exempt_first and s_num == 1) or \
               (exempt_last and total_slide_count > 0 and s_num == total_slide_count) or \
               (s_num in exempt_specific):
                ai_logger.info(f"Skipping Slide {s_num} (Exempt Rule)")
                skipped_results.append({"slide_number": s_num, "clarity_score": "N/A", "tone_audit": "Slide marked as Exempt in settings."})
            else:
                active_slides.append(s)
        return active_slides, skipped_results

    # --- HELPER: SAFE TEXT EXTRACTION ---
    def safe_extract_text(self, data):
        """Recursively extracts string content from Mistral/Complex response objects."""
        text_content = ""
        
        if isinstance(data, str): return data
        
        if isinstance(data, list):
            for item in data: text_content += self.safe_extract_text(item)
        
        elif hasattr(data, 'text') and isinstance(data.text, str): return data.text
            
        elif isinstance(data, dict):
            if 'text' in data and isinstance(data['text'], str): return data['text']
            # Mistral specific 'thinking' block bypass
            if 'thinking' in data: return "" 
            for value in data.values(): text_content += self.safe_extract_text(value)

        elif not isinstance(data, (int, float, bool, type(None))):
            for attr in ['text', 'content']:
                if hasattr(data, attr):
                    attr_value = getattr(data, attr)
                    if isinstance(attr_value, str): text_content += attr_value
                    elif isinstance(attr_value, (list, dict)): text_content += self.safe_extract_text(attr_value)

        return text_content

    def execute_agent(self, agent_role, prompt, system_instruction=None):
        provider = self.determine_provider(agent_role)
        client = self.get_client(provider)
        model_name = MODEL_MAPPING.get(agent_role, {}).get(provider)
        
        if not client or not model_name: 
            return f"Error: Provider {provider} Missing or Failed to Init"

        start_time = time.time(); in_tokens, out_tokens = 0, 0; response_text = ""
        try:
            if provider == "GEMINI":
                # FIXED: Standard SDK usage
                model = client.GenerativeModel(model_name)
                full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
                res = model.generate_content(full_prompt)
                response_text = res.text
                if hasattr(res, 'usage_metadata'):
                    in_tokens = res.usage_metadata.prompt_token_count
                    out_tokens = res.usage_metadata.candidates_token_count
            
            elif provider == "MISTRALAI":
                msgs = [{"role": "user", "content": prompt}]
                if system_instruction: msgs.insert(0, {"role": "system", "content": system_instruction})
                
                res = client.chat(model=model_name, messages=msgs)
                # FIX: Use safe extractor for Mistral
                response_text = self.safe_extract_text(res.choices[0].message.content)
                
                if hasattr(res, 'usage'): in_tokens, out_tokens = res.usage.prompt_tokens, res.usage.completion_tokens

            elif provider in ["OPENAI", "GROQ"]:
                msgs = [{"role": "user", "content": prompt}]
                if system_instruction: msgs.insert(0, {"role": "system", "content": system_instruction})
                res = client.chat.completions.create(model=model_name, messages=msgs)
                response_text = res.choices[0].message.content
                if hasattr(res, 'usage'): in_tokens, out_tokens = res.usage.prompt_tokens, res.usage.completion_tokens

            elif provider == "ANTHROPIC":
                res = client.messages.create(model=model_name, max_tokens=4096, system=system_instruction or "", messages=[{"role": "user", "content": prompt}])
                response_text = res.content[0].text
                if hasattr(res, 'usage'): in_tokens, out_tokens = res.usage.input_tokens, res.usage.output_tokens

            elif provider == "AWS_BEDROCK":
                body = json.dumps({"prompt": f"\n\nHuman: {system_instruction}\n{prompt}\n\nAssistant:", "max_tokens_to_sample": 4096})
                res = client.invoke_model(body=body, modelId=model_name)
                response_text = json.loads(res.get('body').read())['completion']

            self.tracker.log_usage(agent_role, provider, model_name, in_tokens, out_tokens, time.time() - start_time, "SUCCESS")
            return response_text

        except Exception as e:
            sys_logger.error(f"CRITICAL: Agent {agent_role} crash on {provider}: {e}")
            self.tracker.log_usage(agent_role, provider, model_name, 0, 0, time.time() - start_time, "ERROR")
            return f"Agent Error: {str(e)}"

    def _clean_json(self, text):
        if not text: return {"error": "No response"}
        
        # 1. Strip Markdown
        clean = re.sub(r'```json\s*|```', '', text).strip()
        
        # 2. Sanitize Control Characters
        clean = re.sub(r'[\x00-\x09\x0B\x0C\x0E-\x1F]', '', clean)

        try: 
            return json.loads(clean)
        except:
            # Fallback regex extraction
            match_obj = re.search(r'(\{.*\})', clean, re.DOTALL)
            match_list = re.search(r'(\[.*\])', clean, re.DOTALL)
            try:
                if match_list: return json.loads(match_list.group(1))
                if match_obj: return json.loads(match_obj.group(1))
            except:
                pass # Failed even with regex
            
            ai_logger.error(f"JSON Parse Failed. Raw text sample: {clean[:200]}")
            return []
