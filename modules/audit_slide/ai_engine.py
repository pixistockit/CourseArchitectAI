import os
import json
import re
import time
import logging
import csv
import requests
from datetime import datetime

import google.generativeai as genai
from openai import OpenAI
import anthropic
from groq import Groq
from mistralai import Mistral
import boto3

import audit_slide.config as CFG
from audit_slide.prompts import get_batch_manager_prompt, get_batch_executor_prompt, get_research_query_prompt

# --- LOGGING SETUP ---
LOG_DIR = 'data/logs'
os.makedirs(LOG_DIR, exist_ok=True)

sys_logger = logging.getLogger('ai_system')
if not sys_logger.handlers:
    sys_handler = logging.FileHandler(os.path.join(LOG_DIR, 'system_debug.log'))
    sys_logger.addHandler(sys_handler)
    sys_logger.setLevel(logging.INFO)

ai_logger = logging.getLogger('ai_forensic')
if not ai_logger.handlers:
    ai_handler = logging.FileHandler(os.path.join(LOG_DIR, 'ai_forensic.log'), mode='a')
    fmt = logging.Formatter('\n' + '='*80 + '\n%(asctime)s - %(message)s')
    ai_handler.setFormatter(fmt)
    ai_logger.addHandler(ai_handler)
    ai_logger.setLevel(logging.INFO)

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
        self.csv_file = os.path.join(LOG_DIR, 'token_ledger.csv')
        self._init_csv()

    def _init_csv(self):
        if not os.path.exists(self.csv_file):
            with open(self.csv_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Timestamp', 'Agent', 'Provider', 'Model', 'Input_Tokens', 'Output_Tokens', 'Latency_Sec', 'Status'])

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
        self.ai_context_prompt = self._load_context_file()

    def _load_configs(self):
        llm_cfg = {}
        try:
            path = os.path.join(os.path.dirname(__file__), '../data/config/llm_config.json')
            with open(path, 'r') as f: llm_cfg = json.load(f)
        except: pass
        
        brand_cfg = {}
        try:
            path = os.path.join(os.path.dirname(__file__), '../data/config/brand_config.json')
            with open(path, 'r') as f: brand_cfg = json.load(f)
        except: pass
        
        return llm_cfg, brand_cfg

    def _load_context_file(self):
        ctx_path = os.path.join(os.path.dirname(__file__), 'assets', 'system_instruction.txt')
        if os.path.exists(ctx_path):
            try:
                with open(ctx_path, 'r') as f: return f.read().strip()
            except Exception as e: sys_logger.error(f"Failed to load system_instruction.txt: {e}")
        return "You are an Instructional Design Auditor. Analyze content for clarity, brevity, and impact."

    def get_client(self, provider):
        if provider in self.clients: return self.clients[provider]
        try:
            if provider == "GEMINI":
                key = self.config.get('gemini_api_key')
                if not key: raise ValueError("Missing GEMINI_API_KEY")
                genai.configure(api_key=key)
                self.clients[provider] = genai
                return genai
            elif provider == "OPENAI":
                client = OpenAI(api_key=self.config.get('openai_api_key'))
                self.clients[provider] = client
                return client
            elif provider == "ANTHROPIC":
                client = anthropic.Anthropic(api_key=self.config.get('anthropic_api_key'))
                self.clients[provider] = client
                return client
            elif provider == "GROQ":
                client = Groq(api_key=self.config.get('groq_api_key'))
                self.clients[provider] = client
                return client
            elif provider == "MISTRALAI":
                client = Mistral(api_key=self.config.get('mistral_api_key'))
                self.clients[provider] = client
                return client
            elif provider == "AWS_BEDROCK":
                client = boto3.client('bedrock-runtime', 
                                    aws_access_key_id=self.config.get('aws_access_key'), 
                                    aws_secret_access_key=self.config.get('aws_secret_key'), 
                                    region_name=self.config.get('aws_region', 'us-east-1'))
                self.clients[provider] = client
                return client
        except Exception as e:
            sys_logger.error(f"Provider {provider} init failed: {e}")
            return None

    def determine_provider(self, agent_role):
        config_key = f"{agent_role.lower()}_provider" 
        selected = self.config.get(config_key, "SAME_AS_AGENT_1")
        if selected == "SAME_AS_AGENT_1": selected = self.config.get("agent_1_provider", "GEMINI")
        return selected

    def query_vector_db(self, query_text):
        # FIX: Ensure clean strings
        account_id = str(getattr(CFG, 'cf_account_id', "")).strip()
        v_token = str(getattr(CFG, 'cf_vectorize_token', "")).strip()
        ai_token = str(getattr(CFG, 'cf_workers_ai_token', "")).strip()
        index_name = getattr(CFG, 'cf_index_name', "instructional-cadence-kb")

        # Handle complex object if passed accidentally (The Mistral Bug Fix)
        if not isinstance(query_text, str):
            ai_logger.warning(f"Vector DB received non-string query: {type(query_text)}. Converting.")
            query_text = str(query_text)

        if not all([account_id, v_token, ai_token]):
            ai_logger.info("AGENT_2: Credentials missing. Skipping DB.")
            return "Standard Best Practice: Maintain clarity."

        try:
            ai_logger.info(f"AGENT_2: Embedding query: {query_text}")
            
            emb_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/baai/bge-small-en-v1.5"
            emb_res = requests.post(emb_url, headers={"Authorization": f"Bearer {ai_token}"}, json={"text": [query_text]})
            
            if emb_res.status_code != 200: 
                ai_logger.error(f"Vector DB Embedding Error: {emb_res.text}")
                return "Vector DB Error: Embedding failed."
            
            vector = emb_res.json()['result']['data'][0]
            query_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/vectorize/indexes/{index_name}/query"
            query_res = requests.post(query_url, headers={"Authorization": f"Bearer {v_token}"}, json={"vector": vector, "topK": 2, "returnMetadata": True})
            
            if query_res.status_code != 200:
                ai_logger.error(f"Vector DB Query Error: {query_res.text}")
                return "Vector DB Query Failed."

            matches = query_res.json().get('result', {}).get('matches', [])
            if not matches: 
                ai_logger.info("AGENT_2: No matches found in DB.")
                return "No KB matches found."

            context = "\n".join([m.get('metadata', {}).get('text', '') for m in matches if m.get('metadata')])
            ai_logger.info(f"AGENT_2 SUCCESS: Retrieved {len(matches)} nodes.")
            ai_logger.info(f"AGENT_2 RESEARCH PREVIEW: {context[:200]}...") 
            return context
        except Exception as e:
            ai_logger.error(f"AGENT_2 FAILURE: {e}")
            return "Standard Best Practice."

    # --- HIERARCHICAL BATCH PROCESSING ---
    def analyze_batch(self, slides_list, total_slide_count=0):
        if not slides_list: return []
        
        # 1. FILTER EXEMPT SLIDES
        active_slides = []
        skipped_results = []
        
        exempt_first = self.brand_config.get('exempt_first_slide', False)
        exempt_last = self.brand_config.get('exempt_last_slide', False)
        specific_raw = self.brand_config.get('exempt_specific_slides', "")
        exempt_specific = []
        if specific_raw:
            try: exempt_specific = [int(x.strip()) for x in str(specific_raw).split(',') if x.strip().isdigit()]
            except: pass

        for s in slides_list:
            s_num = s.get('slide_number')
            is_exempt = False
            
            if exempt_first and s_num == 1: is_exempt = True
            if exempt_last and total_slide_count > 0 and s_num == total_slide_count: is_exempt = True
            if s_num in exempt_specific: is_exempt = True
            
            if is_exempt:
                ai_logger.info(f"Skipping Slide {s_num} (Exempt Rule)")
                skipped_results.append({
                    "slide_number": s_num,
                    "clarity_score": "N/A",
                    "tone_audit": "Slide marked as Exempt in settings.",
                    "remediation": {"option_a": {"label": "Exempt", "text": "No analysis performed."}, "option_b": {"label": "Exempt", "text": ""}}
                })
            else:
                active_slides.append(s)

        if not active_slides:
            # If all slides are exempt, just return the skipped list
            return skipped_results

        ai_logger.info(f"STARTING SMART BATCH: {len(active_slides)} Slides (Skipped {len(skipped_results)})")
        
        # 2. AGENT 1: TOPIC EXTRACTION
        sys_prompt, user_msg = get_batch_manager_prompt(active_slides)
        ai_logger.info("BATCH STEP 1: Agent 1 analyzing outline...")
        topic_summary = self.execute_agent("AGENT_1", user_msg, sys_prompt)
        ai_logger.info(f" -> Topic: {topic_summary}")

        # 3. AGENT 2: INTELLIGENT RESEARCH
        sys_prompt_a2, user_msg_a2 = get_research_query_prompt(topic_summary)
        ai_logger.info("BATCH STEP 2A: Agent 2 generating optimized query...")
        optimized_query = self.execute_agent("AGENT_2", user_msg_a2, sys_prompt_a2)
        ai_logger.info(f" -> Optimized Query: {optimized_query}")

        ai_logger.info("BATCH STEP 2B: Consulting Vector DB...")
        research_context = self.query_vector_db(optimized_query)
        
        # 4. AGENT 3: EXECUTION
        sys_prompt, user_msg = get_batch_executor_prompt(active_slides, research_context, self.ai_context_prompt, self.config, self.brand_config)
        ai_logger.info(f"BATCH STEP 3: Agent 3 executing full analysis (Level: {self.config.get('notes_scripting_level', 'Light')})...")
        raw_response = self.execute_agent("AGENT_3", user_msg, sys_prompt)
        
        # 5. PARSE & MERGE
        final_results = skipped_results
        try:
            ai_results = self._clean_json(raw_response)
            if isinstance(ai_results, list): final_results.extend(ai_results)
            elif isinstance(ai_results, dict) and "error" not in ai_results: final_results.append(ai_results)
            
            final_results.sort(key=lambda x: x.get('slide_number', 0))
            return final_results
        except Exception as e:
            ai_logger.error(f"Batch Parse Error: {e}")
            return final_results

    # --- HELPER: SAFE TEXT EXTRACTION (From your Lambda code) ---
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

        start_time = time.time()
        in_tokens, out_tokens = 0, 0
        response_text = ""

        try:
            if provider == "GEMINI":
                model = client.GenerativeModel(model_name)
                full = f"SYSTEM INSTRUCTION:\n{system_instruction}\n\nUSER PROMPT:\n{prompt}" if system_instruction else prompt
                res = model.generate_content(full)
                response_text = res.text
                if hasattr(res, 'usage_metadata'):
                    in_tokens = res.usage_metadata.prompt_token_count
                    out_tokens = res.usage_metadata.candidates_token_count

            elif provider == "MISTRALAI":
                msgs = [{"role": "user", "content": prompt}]
                if system_instruction: msgs.insert(0, {"role": "system", "content": system_instruction})
                
                res = client.chat.complete(model=model_name, messages=msgs)
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
            # IMPORTANT: Return clean error message so frontend doesn't hang
            return f"Agent Error: {str(e)}"

    def _clean_json(self, text):
        if not text: return {"error": "No response"}
        
        # 1. Strip Markdown
        clean = re.sub(r'```json\s*|```', '', text).strip()
        
        # 2. Sanitize Control Characters (The Fix for Batch 4 Crash)
        # Removes non-printable characters (0-31) except newlines (10) and carriage returns (13)
        # This fixes "Invalid control character" errors often caused by tabs or weird copy-paste chars
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