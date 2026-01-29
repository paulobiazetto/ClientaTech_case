import os
import sys
import json
import sqlite3
import logging
import hashlib
from datetime import datetime
from dotenv import load_dotenv
from ollama import Client

# --- 1. CONFIGURATION & SETUP ---

# Load environment variables
load_dotenv()

# Constants
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'data', 'clientatech.db')
CACHE_DB_PATH = os.path.join(BASE_DIR, 'data', 'cache.db')
LOG_PATH = os.path.join(BASE_DIR, 'logs', 'agent.log')
FT_PATH = os.path.join(BASE_DIR, 'finetuning', 'dataset_finetuning.jsonl')

# Models
MODEL_NAME = "qwen2.5-coder:14b"          # Logic & SQL
MODEL_NAME_FT = "llama3-finetuned:latest" # Analyst Persona

# Initialize Clients
client = Client()

# Logging Setup
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
	filename=LOG_PATH,
	level=logging.INFO,
	format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- 2. INFRASTRUCTURE (DB & CACHE) ---

def get_db_connection():
	"""Connects to the main business database."""
	conn = sqlite3.connect(DB_PATH)
	conn.row_factory = sqlite3.Row
	return conn

def get_cache_connection():
	"""Connects to the dedicated Cache DB."""
	conn = sqlite3.connect(CACHE_DB_PATH)
	conn.row_factory = sqlite3.Row
	return conn

def get_schema():
	"""Retrieves the business database schema for context."""
	schema = ""
	conn = get_db_connection()
	cursor = conn.cursor()
	
	tables = ['clientes', 'contratos', 'interacoes']
	for table in tables:
		cursor.execute(f"PRAGMA table_info({table})")
		columns = [f"{row['name']} ({row['type']})" for row in cursor.fetchall()]
		schema += f"Table {table}: {', '.join(columns)}\n"
	
	conn.close()
	return schema

def init_cache():
	"""Initializes the cache table in the dedicated DB."""
	conn = get_cache_connection()
	conn.execute('''
		CREATE TABLE IF NOT EXISTS llm_cache (
			query_hash TEXT PRIMARY KEY,
			user_query TEXT,
			sql_generated TEXT,
			intent TEXT
		)
	''')
	conn.commit()
	conn.close()

def get_cache(user_query):
	"""Checks for existing SQL in cache."""
	query_hash = hashlib.md5(user_query.lower().strip().encode()).hexdigest()
	conn = get_cache_connection()
	row = conn.execute("SELECT sql_generated, intent FROM llm_cache WHERE query_hash = ?", (query_hash,)).fetchone()
	conn.close()
	return row if row else None

def save_cache(user_query, sql, intent):
	"""Saves valid SQL to cache."""
	# Don't cache errors
	if "Error" in sql or "SELECT 'Error" in sql: 
		return 
		
	query_hash = hashlib.md5(user_query.lower().strip().encode()).hexdigest()
	conn = get_cache_connection()
	try:
		conn.execute("INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?, ?)", 
					(query_hash, user_query.strip(), sql, intent))
		conn.commit()
		logging.info(f"CACHE SAVED: {user_query} -> {intent}")
	except Exception as e:
		logging.error(f"CACHE ERROR: {e}")
	conn.close()

# --- 3. CORE LOGIC: INTENT & GENERATORS ---

def classify_intent(user_query):
	"""
	Decides the intent of the user query.
	Returns: 'PROFILE', 'AGGREGATED', 'TEMPORAL', 'SEMANTIC', 'AMBIGUOUS', 'GREETING'
	"""
	system_prompt = """You are the Router Agent for a database assistant.
Your goal is to map the user's question to the single best category based on the intent descriptions below.

CATEGORIES:
1. PROFILE: Lookup of attributes for specific entities (e.g., "Status do cliente X", "Plano da empresa Y", "Quem é o cliente Z?", "Email do cliente X").
2. AGGREGATED: Statistics, math, counts, sums, averages, or grouping by segments (e.g., "Faturamento total", "Quantos clientes ativos?", "Média de valor", "Clientes por região").
3. TEMPORAL: Questions involving time windows, dates, deadlines, or duration (e.g., "Vencem em 30 dias", "Sem interação há 2 meses", "Renovações este mês", "Últimas interações").
4. SEMANTIC: Inference of abstract business concepts like Risk, Churn, Satisfaction, or "Best/Worst" lists (e.g., "Risco de churn", "Clientes insatisfeitos", "Quem preciso priorizar?", "Potenciais cancelamentos").
5. AMBIGUOUS: Subjective or vague questions where criteria aren't clear (e.g., "Esse cliente é bom?", "Como está a empresa X?", "A empresa Y dá trabalho?").
6. GREETING: Hello, help, identity, or meta-questions (e.g., "Oi", "O que você faz?", "Ajuda").

Output format: JSON ONLY.
{
	"category": "Category Name",
	"reasoning": "Brief explanation of why"
}
"""
	
	try:
		response = client.chat(
			model=MODEL_NAME,
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_query}
			],
			options={"temperature": 0.0} # Deterministic
		)
		content = response['message']['content'].strip()
		
		# Try to parse JSON
		try:
			 # Handle markdown code blocks if model encapsulates JSON
			 if "```json" in content:
				 import re
				 match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
				 if match: content = match.group(1)
				 
			 data = json.loads(content)
			 intent = data.get("category", "GREETING").strip().upper()
			 reasoning = data.get("reasoning", "No reasoning provided")
			 print(f"🤔 Raciocínio (DEBUG): {reasoning}")
			 
		except json.JSONDecodeError:
			 # Fallback if model fails strictly JSON
			 logging.warning(f"JSON Parse Error in Intent: {content}")
			 intent = "GREETING" 

		# Validation/Fallback
		# 1. Normalização básica (remove espaços extras e garante maiúsculas)
		valid_intents = ['PROFILE', 'AGGREGATED', 'TEMPORAL', 'SEMANTIC', 'AMBIGUOUS', 'GREETING']
		intent_clean = intent.upper().strip()

		if intent_clean in valid_intents:
			intent = intent_clean
		else:
			# 2. Heurística Dinâmica (O "Pulo do Gato")
			# Procura QUALQUER uma das intents válidas dentro da string suja
			found_intent = None
			for valid_item in valid_intents:
				if valid_item in intent_clean:
					found_intent = valid_item
					break # Encontrou? Para de procurar.
			
			# 3. Define o valor final ou o Fallback
			intent = found_intent if found_intent else "GREETING"
		
		# print(intent)
		# if intent not in valid_intents:
		# 	 # Heuristic fallback
		# 	 if "PROFILE" in intent: intent = "PROFILE"
		# 	 elif "RISK" in intent: intent = "RISK"
		# 	 elif "HISTORY" in intent: intent = "HISTORY"
		# 	 elif "ABSENCE" in intent: intent = "ABSENCE"
		# 	 elif "GENERAL" in intent: intent = "GENERAL"
		# 	 else: intent = "GREETING" # Safe fallback

		return intent
	except Exception as e:
		logging.error(f"Intent Classification Error: {e}")
		return "GREETING"

def _call_llm_sql(messages, user_query):
	"""Helper to append user query, call LLM, and clean code block output."""
	messages.append({"role": "user", "content": user_query})
	try:
		response = client.chat(model=MODEL_NAME, 
				messages=messages, 
				options={"temperature": 0.1}
				)
		content = response['message']['content'].strip()
		
		# Regex to extract code block if present
		import re
		match = re.search(r"```sql\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
		if match:
			return match.group(1).strip()
		
		# Fallback: check for generic code block
		match = re.search(r"```\s*(.*?)\s*```", content, re.DOTALL)
		if match:
			 return match.group(1).strip()
			 
		# Fallback: strict cleanup if no code block
		cleaned = content.replace("```sql", "").replace("```", "").strip()
		
		# Validation: Ensure it looks like SQL
		if not cleaned.upper().startswith("SELECT") and not cleaned.upper().startswith("WITH"):
			return "SELECT 'Error: Model generated text instead of SQL' WHERE 0"
			
		return cleaned
	except Exception as e:
		return f"Error: {e}"

# --- SQL GENERATORS ---

def generate_profile_sql(user_query, schema):
	"""Generates SQL specifically for Entity Profiles (360 view)."""
	system_prompt = f"""# Role: SQLite Expert (Profile Lookup)
# Schema: {schema}

# TASK:
Generate a SQL query to retrieve specific attributes of a client or contract.
1. Identify the entity name (Client) in the user prompt.
2. Use `LIKE %name%` for flexibility.
3. JOIN `clientes` with `contratos` (and `interacoes` if needed, taking the latest one).
4. Return columns: Name, Segment, Status, Plan, Monthly Value, Contract End Date.

# RULES:
- Output ONLY valid SQLite code inside markdown code blocks.
- Do NOT explain the code.
- Case insensitive search.
"""
	# # RULES:
	# 1. SQLite Syntax Only.
	# 2. Output format MUST be markdown code block safely.
	# 3. Answer strictly based on the provided text. Do not use outside knowledge or hallucinate facts. If the answer is not present, output is empty `[]`.

	messages = [{"role": "system", "content": system_prompt}]
	return _call_llm_sql(messages, user_query)

def generate_aggregate_sql(user_query, schema):
	"""Fallback for specific/general queries."""
	system_prompt = f"""# Role: SQLite Expert (Aggregations)
# Schema: {schema}

# TASK:
Generate a SQL query to calculate statistics.
1. Identify the metric: "Quantos" (COUNT), "Valor/Faturamento" (SUM valor_mensal), "Média" (AVG).
2. Identify groupings: "por segmento", "por status".
3. Apply filters: If asking for "Current/Atual", filter `status = 'Ativo'`.

# RULES:
- Output ONLY valid SQLite code inside markdown code blocks.
- Do NOT explain the code.
"""
	messages = [{"role": "system", "content": system_prompt}]
	return _call_llm_sql(messages, user_query)

def generate_temporal_sql(user_query, schema):
	"""Generates SQL specifically for Interaction Lists."""
	system_prompt = f"""# Role: SQLite Expert (Time & Dates)
# Schema: {schema}

# TASK:
Generate a SQL query based on time constraints.

# LOGIC PATTERNS:
1. "Expires in X days":
WHERE data_fim BETWEEN date('now') AND date('now', '+X days')

2. "No interaction in X days" (Absence):
WHERE id_cliente NOT IN (
	SELECT id_cliente FROM interacoes 
	WHERE data >= date('now', '-X days')
)
AND status = 'Ativo'

3. "History/Last X days":
WHERE data >= date('now', '-X days')

# RULES:
- USE `date('now')` for current date. DO NOT use `NOW()` or `CURDATE()`.
- Output ONLY valid SQLite code inside markdown code blocks.
- Case insensitive search.
"""
	messages = [{"role": "system", "content": system_prompt}]
	return _call_llm_sql(messages, user_query)

def generate_semantic_sql(user_query, schema):
	system_prompt = f"""# Role: SQLite Expert (Risk & Inference)
# Schema: {schema}

# TASK:
Translate business logic into SQL signals.

# DEFINITIONS:
1. "Risk of Churn" / "Risco":
- Criteria: Contract expires soon (< 60 days) OR No interaction recently (> 45 days).
- Query: Select clients matching these filters.

2. "Expansion" / "Upsell":
- Criteria: Active clients with high value or specific segments.

# STRATEGY:
Select `clientes.nome`, `contratos.data_fim`, `MAX(interacoes.data) as ultima_interacao`
FROM clientes
JOIN contratos ON ...
LEFT JOIN interacoes ON ...
GROUP BY clientes.id_cliente
HAVING (
	data_fim < date('now', '+60 days') 
	OR 
	ultima_interacao < date('now', '-45 days')
	OR
	ultima_interacao IS NULL
)
AND clientes.status = 'Ativo'

# RULES:
- Output ONLY valid SQLite code inside markdown code blocks.
- Case insensitive search.
"""
	messages = [{"role": "system", "content": system_prompt}]
	return _call_llm_sql(messages, user_query)

def generate_ambiguous_sql(user_query, schema):
	"""Generates SQL specifically for 'No Interaction' queries."""
	system_prompt = f"""# Role: SQLite Expert (Context Gathering)
# Schema: {schema}

# TASK:
The user asked a subjective question (e.g., "O cliente X é bom?", "Tem problemas?").
You cannot answer subjective questions with SQL.
Instead, FETCH ALL OBJECTIVE FACTS about the target client so the Analyst can answer.

# STRATEGY:
1. Identify the client name.
2. Retrieve:
- Tenure (data_cadastro)
- Financial value (valor_mensal)
- Interaction count in last 90 days (Count interacoes)
- Latest interaction type/description.

# RULES:
- Return a "Fact Sheet" query.
- Output ONLY valid SQLite code inside markdown code blocks.
- Case insensitive search.
"""
	messages = [{"role": "system", "content": system_prompt}]
	return _call_llm_sql(messages, user_query)



def generate_sql_router(user_query, schema):
	"""Routes the query to the correct generator (Cached)."""
	
	# 1. Check Cache
	cached = get_cache(user_query)
	if cached:
		print(f"⚡ Cache Hit! (Intent: {cached['intent']})")
		logging.info(f"CACHE HIT: {user_query}")
		return cached['sql_generated'], cached['intent']

	# 2. Logic (Router + Generator)
	intent = classify_intent(user_query)
	print(f"🧠 Intenção Detectada: {intent}")
	logging.info(f"INTENT: {intent} | QUERY: {user_query}")
	
	if intent == "PROFILE":
		sql = generate_profile_sql(user_query, schema)
	elif intent == "AGGREGATED":
		sql = generate_aggregate_sql(user_query, schema)
	elif intent == "TEMPORAL":
		sql = generate_temporal_sql(user_query, schema)
	elif intent == "SEMANTIC":
		sql = generate_semantic_sql(user_query, schema)
	elif intent == "GREETING":
		return None, "GREETING"
	else:
		sql = generate_ambiguous_sql(user_query, schema)
		
	logging.info(f"GENERATED SQL: {sql}")
	
	# 3. Save Cache (if valid)
	save_cache(user_query, sql, intent)
	return sql, intent

# --- 4. EXECUTION ---

def execute_sql(sql_query):
	"""Executes the generated SQL query safely."""
	try:
		conn = get_db_connection()
		cursor = conn.cursor()
		cursor.execute(sql_query)
		res = cursor.fetchall()
		result = [dict(row) for row in res]
		conn.close()
		return result, None
	except Exception as e:
		return None, str(e)

# --- 5. ANALYST PERSONA (RESPONSE GENERATION) ---

def load_few_shot_examples(n=5):
	"""Helper to load examples (currently unused but preserved)."""
	examples_text = ""
	try:
		import random
		with open(FT_PATH, 'r', encoding='utf-8') as f:
			lines = f.readlines()
			for line in lines[:n]: 
				data = json.loads(line)
				user = data['messages'][1]['content']
				assistant = data['messages'][2]['content']
				examples_text += f"\nUser Input: {user}\nAssistant Response:\n{assistant}\n---\n"
	except:
		pass
	return examples_text

def generate_final_response(user_query, sql_query, sql_result, intent):
	"""
	Generates the final response using the Intent as a Style Guide.
	"""
	today = datetime.now().strftime('%Y-%m-%d')
	# dynamic_examples = load_few_shot_examples(n=10) # Load diverse examples

	system_prompt = f"""# Role: ClientaTech AI Internal Analyst.
# Context: You are the final interface answering the user based on SQL data retrieved.
# Current Date: {today}
# User Intent: {intent}

# INPUT DATA:
User Question: "{user_query}"
Database Results: {sql_result}

# GENERAL RULES:
1. LANGUAGE: Portuguese (pt-BR).
2. TONE: Professional, analytical, and objective.
3. NO HALLUCINATION: If `Database Results` is empty `[]`, respond ONLY: "Não encontrei registros correspondentes na base de dados para essa consulta." (Unless intent is GREETING).
4. DATES: When mentioning dates, calculate relative time (e.g., "vence em 5 dias" instead of just "2025-10-10").
5. Difference between client status active/inactive and contract status active/closed.

# FORMATTING GUIDELINES BY INTENT:

# 1. IF INTENT = 'PROFILE' (Rich Card)
You MUST use the following format exactly (emojis included):
📌 Cliente: [Name]
📊 Status: [Status]
📄 Plano: [Plan]
💰 Valor Mensal: R$ [Value]

ℹ️ Observações:
- [Observation 1, e.g., "Contrato active until..."]
- [Observation 2, e.g., "Last interaction was..."]

# 2. IF INTENT = 'AGGREGATED' (Direct Answer)
- Provide the number/value directly.
- Example: "O faturamento mensal total é de R$ 50.000,00." or "Temos 15 clientes ativos no segmento Varejo."

# 3. IF INTENT = 'TEMPORAL' (Lists & Deadlines)
- Use bullet points.
- Compute the relative time and deadlines based on the Current Date.
- Sort by date if not already sorted.

# 4. IF INTENT = 'SEMANTIC' (Risk & Inferences)
- You MUST explain the criteria used by the SQL logic.
- Structure:
"Identifiquei [N] clientes com risco de churn.
Critério utilizado: Contrato vencendo em < 60 dias OU sem interação há > 45 dias.

Lista:
- **[Client Name]**: Vence em [Date] (Sem contato há [X] dias)."

# 5. IF INTENT = 'AMBIGUOUS' (Fact-Based Judgement)
- The user asked a subjective question (e.g., "Is client X good?").
- Use the objective data provided to give a balanced answer.
- DO NOT say "Yes" or "No". Say "Aqui estão os indicadores do cliente X:"
- List: Tenure (Tempo de casa), Payment Value, Interaction Frequency.

# 6. IF INTENT = 'GREETING'
- Be helpful and brief.
- Introduce yourself as "Agente ClientaTech".
- Offer help with: Perfis, Histórico, Riscos ou Dados Gerais.

# FINAL INSTRUCTION:
Synthesize the `Database Results` into the answer following the specific guidelines for `{intent}` above.
"""
	
	# system_prompt = f"""# Role: ClientaTech AI Analyst
	# # Context: You are answering a user query based on SQL data.
	# # CURRENT MODE: **{intent}**
	# # REFERENCE DATE: {today}

	# # INSTRUCTIONS BASED ON MODE:
	# - IF MODE == 'PROFILE': 
	#   1. You MUST use the "Rich Profile Card" style (Status, Plan, Value + Observations).
	#   2. You can use emojis to make the response more engaging.
	# - IF MODE == 'HISTORY': You MUST use a Bulleted List of events ordered by date.
	# - IF MODE == 'RISK': 
	#   1. COMPARE `contract_end_date` vs `{today}`.
	#   2. COMPARE `last_interaction` vs `{today}`.
	#   3. LOGIC: Risk = (Expires < X days) OR (Silence > Y days).
	#   4. SUBJECTIVITY HANDLING:
	# 	 - If user asks for "Bons/Melhores": Show clients with NO Risk (Active + Safe dates).
	# 	 - If user asks for "Ruins/Piores": Show clients WITH Risk.
	#   5. ALWAYS explicitly the criteria used to determine the risk.
	#   6. OUTPUT: List clients based on these logical criteria.
	# - IF MODE == 'ABSENCE': List the clients found.
	# - IF MODE == 'GENERAL': Answer directly and concisely.
	# - IF MODE == 'GREETING': 
	#   1. Introduce yourself as "ClientaTech AI Analyst".
	#   2. Briefly explain what you can do (Analyze Profiles, History, Risk, and General Data).
	#   3. Give 3 examples of short questions the user can ask.
	#   4. Be professional but welcoming.

	# # RULES:
	# 1. OUTPUT LANGUAGE: Portuguese (pt-BR).
	# 2. TRUTH: If data is empty `[]`, say "Não encontrei informações" (Except for GREETING).
	# 3. TONE: Professional.
	# 4. DATE MATH: Calculate days diff accurately.

	# # EXAMPLES:
	# {dynamic_examples}
	# """
	
	user_content = f"""
User Query: {user_query}
SQL Used: {sql_query}
Data Retrieved: {json.dumps(sql_result, ensure_ascii=False)}

Generate response for mode {intent}.
	"""
	
	try:
		response = client.chat(
			model=MODEL_NAME,
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_content}
			]
		)
		final_text = response['message']['content']
		logging.info(f"ANALYST RESPONSE: {final_text[:100]}...") # Log summary
		return final_text
	except Exception as e:
		logging.error(f"ANALYST ERROR: {e}")
		return f"Error response: {e}"

# --- 6. MAIN APPLICATION LOOP ---

def main():
	print(f"🤖 **ClientaTech AI Agent [Router Architecture]** initialized.")
	
	# Initialize Infrastructure
	init_cache()
	schema = get_schema()
	
	while True:
		try:
			user_query = input("\n👤 Você: ")
			if user_query.lower() in ['exit', 'quit', 'sair']:
				print("👋 Encerrando...")
				break
			
			# Step 1 & 2: Route and Generate SQL
			print("⏳ Processando...")
			sql_query, intent = generate_sql_router(user_query, schema)
			
			if intent == "GREETING":
				final_response = generate_final_response(user_query, "SKIP", [], intent)
				print(f"\n{final_response}")
				continue
			
			if "Error" in sql_query:
				print(f"❌ {sql_query}")
				continue
				
			print(f"🔍 SQL (Intenção: {intent}): {sql_query}")
			
			# Step 3: Execute
			print("⏳ Executando...")
			result, error = execute_sql(sql_query)
			
			if error:
				print(f"❌ Erro na execução: {error}")
				continue
			
			print(f"📊 Resultados encontrados: {len(result) if result else 0}")
			
			# Step 4: Analyst
			print("⏳ Formatando resposta...")
			final_response = generate_final_response(user_query, sql_query, result, intent)
			print(f"\n{final_response}")
			
		except KeyboardInterrupt:
			break

if __name__ == "__main__":
	main()
