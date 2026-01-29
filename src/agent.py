import os
import sys
import re
import json
import sqlite3
import logging
import hashlib
from datetime import datetime
from dotenv import load_dotenv
from ollama import Client


# --- CONFIGURATION & SETUP ---

# Load environment variables
load_dotenv()

# Constants
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'data', 'clientatech.db')
CACHE_DB_PATH = os.path.join(BASE_DIR, 'data', 'cache.db')
LOG_PATH = os.path.join(BASE_DIR, 'logs', 'agent.log')
FT_PATH = os.path.join(BASE_DIR, 'finetuning', 'dataset_finetuning_v2.jsonl')

# Models
MODEL_NAME = "qwen2.5-coder:14b"          # Logic & SQL
MODEL_NAME_FT = "llama3-finetuned:latest" # Analyst Persona

# Initialize Clients
client = Client()

# --- STRUCTURED LOGGING ---
class StructuredLogger:
	def __init__(self, log_path):
		os.makedirs(os.path.dirname(log_path), exist_ok=True)
		self.logger = logging.getLogger("ClientaTechAgent")
		self.logger.setLevel(logging.INFO)
		
		# File Handler (JSON Lines)
		file_handler = logging.FileHandler(log_path)
		file_handler.setFormatter(logging.Formatter('%(message)s'))
		self.logger.addHandler(file_handler)
		
		# Stream Handler (Optional - minimal output to console if needed, keeping it off for now to not clutter stdout)
		# stream_handler = logging.StreamHandler(sys.stdout)
		# self.logger.addHandler(stream_handler)

	def log(self, event_type, **kwargs):
		"""Logs an event as a JSON line."""
		entry = {
			"timestamp": datetime.now().isoformat(),
			"event": event_type,
			**kwargs
		}
		self.logger.info(json.dumps(entry, ensure_ascii=False))

logger = StructuredLogger(LOG_PATH)


# --- LLM WITH METRICS ---

def call_llm(model, messages, options=None, component="unknown"):
	"""Wraps Ollama chat with metrics logging."""
	start_time = datetime.now()
	try:
		# Call the model
		response = client.chat(model=model, messages=messages, options=options)
		end_time = datetime.now()
		duration_ms = (end_time - start_time).total_seconds() * 1000
		
		# Extract metrics (Ollama specific)
		prompt_tokens = response.get('prompt_eval_count', 0)
		eval_tokens = response.get('eval_count', 0)
		
		logger.log(
			event_type="llm_call",
			component=component,
			model=model,
			duration_ms=round(duration_ms, 2),
			tokens_in=prompt_tokens,
			tokens_out=eval_tokens,
			status="success"
		)
		return response
	except Exception as e:
		end_time = datetime.now()
		duration_ms = (end_time - start_time).total_seconds() * 1000
		logger.log(
			event_type="llm_call",
			component=component,
			model=model,
			duration_ms=round(duration_ms, 2),
			status="error",
			error=str(e)
		)
		raise e


# --- INFRASTRUCTURE (DB & CACHE) ---

def get_db_connection():
	"""Conecta ao banco de dados de negócio (SQLite)."""
	conn = sqlite3.connect(DB_PATH)
	conn.row_factory = sqlite3.Row
	return conn

def get_cache_connection():
	"""Conecta ao banco separado de Cache."""
	conn = sqlite3.connect(CACHE_DB_PATH)
	conn.row_factory = sqlite3.Row
	return conn

def get_schema():
	"""
	Recupera o schema do banco de dados dinamicamente. 
	Isso é injetado no prompt para que a LLM saiba quais tabelas e colunas existem.
	"""
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
	"""Cria a tabela de cache se não existir."""
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
	"""Verifica se a query já existe no cache."""
	query_hash = hashlib.md5(user_query.lower().strip().encode()).hexdigest()
	conn = get_cache_connection()
	row = conn.execute("SELECT sql_generated, intent FROM llm_cache WHERE query_hash = ?", (query_hash,)).fetchone()
	conn.close()
	return row if row else None

def save_cache(user_query, sql, intent):
	"""Salva um SQL válido no cache."""
	# Don't cache errors
	if "Error" in sql or "SELECT 'Error" in sql: 
		return 
		
	query_hash = hashlib.md5(user_query.lower().strip().encode()).hexdigest()
	conn = get_cache_connection()
	try:
		conn.execute("INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?, ?)", 
					(query_hash, user_query.strip(), sql, intent))
		conn.commit()
		logger.log("cache_update", action="save", intent=intent)
	except Exception as e:
		logger.log("cache_error", error=str(e))
	conn.close()


# --- CORE LOGIC: INTENT & GENERATORS ---

def classify_intent(user_query):
	"""
	Decides the intent of the user query. Returns: 'PROFILE', 'HISTORY', 'RISK', 'ABSENCE', 'GENERAL', 'GREETING'
	Saída esperada: Um JSON com a categoria e o raciocínio.
	"""
	system_prompt = """# ROLE
	Classification Expert for ClientaTech.

	# GOAL
	Classify the user's question into one of the known Functional Scopes.

	# INSTRUCTIONS
	Analyze the user's query and map it to one of the following categories:

	1. PROFILE: Broad overview (e.g., "Me fale sobre X", "Dados da Y", "Status de Z")
	2. HISTORY: List of interactions/events (e.g., "Interações de X", "Histórico", "O que aconteceu com Y")
	3. RISK: Inference/Subjective (e.g., "Risco de Churn", "Clientes insatisfeitos", "Risco financeiro", or "Clientes Bons", "Clientes Ruins", "Melhores", "Piores")
	4. ABSENCE: Negative logic (e.g., "Clientes sem interação", "Quem sumiu")
	5. GENERAL: Aggregations & Lists & Specific clients queries (e.g., "Quais contratos vencem?", "Valor total?", "Total de clientes?", "Vencimentos", "Prazos", "Valor da empresa/cliente X")
	6. GREETING: Conversational/Meta (e.g., "Oi", "Olá", "O que você faz?", "Ajuda", "Quem é você?", "Exemplos")

	# OUTPUT FORMAT: JSON ONLY.
	{
		"category": "Category Name",
		"reasoning": "Brief explanation of why"
	}
	"""
	
	try:
		response = call_llm(
			model=MODEL_NAME,
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_query}
			],
			options={"temperature": 0.0}, # Deterministic
			component="intent_classifier"
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
			logger.log("intent_error", error="JSON Parse Error", content=content)
			intent = "GREETING" 

		# Validation/Fallback
		valid_intents = ['PROFILE', 'HISTORY', 'RISK', 'ABSENCE', 'GENERAL', 'GREETING']
		intent_clean = intent.upper().strip()

		if intent_clean in valid_intents:
			intent = intent_clean
		else:
			# Heurística Dinâmica
			found_intent = None
			for valid_item in valid_intents:
				if valid_item in intent_clean:
					found_intent = valid_item
					break 
			
			intent = found_intent if found_intent else "GREETING"
		
		return intent
	except Exception as e:
		logger.log("intent_critical_error", error=str(e))
		return "GREETING"

def _call_llm_sql(messages, user_query):
	"""
	Helper genérico para os Geradores de SQL.
	- Adiciona a query do usuário.
	- Chama a LLM.
	- Extrai e limpa o bloco de código SQL.
	- Valida se o output parece SQL (começa com SELECT/WITH).
	"""
	messages.append({"role": "user", "content": user_query})
	try:
		response = call_llm(
			model=MODEL_NAME, 
			messages=messages, 
			options={"temperature": 0.1},
			component="sql_generator"
		)
		content = response['message']['content'].strip()
		
		# Regex to extract code block if present
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
			print(content)
			return "SELECT 'Error: Model generated text instead of SQL' WHERE 0"
			
		return cleaned
	except Exception as e:
		return f"Error: {e}"

# --- SQL GENERATORS ---

def generate_profile_sql(user_query, schema):
	"""
	Especialista em PERFIL (Visão 360). Foca em joins precisos para trazer dados cadastrais + contratuais + última interação.
	"""
	system_prompt = f"""# ROLE
	Expert SQL Data Scientist (Profile Specialist).

	# GOAL
	Fetch the 'Rich Profile' data of a company.

	# CONTEXT
	Schema: {schema}

	# INSTRUCTIONS
	1. EXTRACT the Client Name from the query (no case sensitive).
	2. JOIN tables:
	   - Start with `clientes` table (base).
       - Join `contratos` on `id_cliente`.
       - Left Join `interacoes` on `id_cliente`.
	3. TARGET COLUMNS to Select:
	   - CALCULATED COLUMN: `CAST(julianday(contratos.data_fim) - julianday('now') AS INTEGER)` AS dias_para_expirar.
	   - CALCULATED COLUMN: `CAST(julianday('now') - julianday(MAX(interacoes.data)) AS INTEGER)` AS dias_desde_ultima_interacao.
	4. FILTER:
	   - Where `clientes.nome` matches the 'Name' (no case sensitive).
	5. CRITICAL: Handle case sensitivity by converting columns to lower case for comparisons. 
	   - Example: Use `LIKE` operator.

	# RULES
	1. SQLite Syntax Only.
	2. Output format MUST use the column names from the Schema (PT-BR). Only alias for calculated columns. Always use lower case column names.
	3. Answer strictly based on the provided text. Do not use outside knowledge or hallucinate facts. If the answer is not present, output is empty `[]`.
	4. Ensure handle case sensitivity by converting columns to lower case.
	"""
	messages = [{"role": "system", "content": system_prompt}]
	return _call_llm_sql(messages, user_query)

def generate_history_sql(user_query, schema):
	"""
	Especialista em HISTÓRICO. Foca em listar eventos ordenados cronologicamente.
	"""
	system_prompt = f"""# ROLE
	Expert SQL Data Scientist (History Specialist).

	# GOAL
	Fetch the list of interactions/events.

	# CONTEXT
	Schema: {schema}

	# INSTRUCTIONS
	1. Identify the Company/Client Name from the user text.
	2. DATA GOAL: Retrieve the chronologcal history of interactions.
	3. JOINS:
	   - Connect `interacoes` (source of events) with `clientes` (to filter by name).
	4. FIELDS:
	   - data, tipo, descrição.
	   - CALCULATED COLUMN: `CAST(julianday('now') - julianday(data) AS INTEGER)` AS dias_antes.
	5. ORDERING:
	   - Most recent events first (Descending).

	# RULES
	1. SQLite Syntax Only.
	2. Output format MUST use the column names from the Schema (PT-BR). Only alias for calculated columns. Always use lower case column names.
	3. Answer strictly based on the provided text. Do not use outside knowledge or hallucinate facts. If the answer is not present, output is empty `[]`.
	4. Ensure handle case sensitivity by converting columns to lower case.
	"""
	messages = [{"role": "system", "content": system_prompt}]
	return _call_llm_sql(messages, user_query)

def generate_risk_sql(user_query, schema):
	"""
	Especialista em RISCO.Gera queries analíticas. Não julga o risco no SQL, mas extrai as métricas (dias para expirar, dias de silêncio)
	para que o Analista (na próxima etapa) faça o julgamento subjetivo.
	"""
	system_prompt = f"""# ROLE
	Expert SQL Data Scientist (Risk Specialist).

	# GOAL
	Gather Risk Evidence (Global OR Specific Client).

	# CONTEXT
	Schema: {schema}

	# INSTRUCTIONS
	1. JOIN `clientes` (base) with `contratos` on `id_cliente`.
	2. EVIDENCE STRATEGY (Select Columns):
	   - CALCULATED COLUMN: `CAST(julianday(contratos.data_fim) - julianday('now') AS INTEGER)` AS dias_para_expirar.
	   - CALCULATED COLUMN: `CAST(julianday('now') - julianday(MAX(interacoes.data)) AS INTEGER)` AS dias_desde_ultima_interacao.
	3. Determine Context:
	   - GLOBAL RISK SCAN (e.g., "Quem está em risco?"): Filter `clientes.status = 'Ativo'`.
	   - SPECIFIC CLIENT CHECK (e.g., "Risco do cliente '%Name%'"): Filter `clientes.status = 'Ativo'` AND `clientes.nome LIKE '%Name%'`.
	4. RISK CRITERIA: Filter Aggregates `HAVING`:
	   - "Expirando em [X] dias" -> (dias_para_expirar <= X) OR "Sem interação há [Y] dias" -> (dias_desde_ultima_interacao >= Y).

	# RULES
	1. SQLite Syntax Only.
	2. Output format MUST use the column names from the Schema (PT-BR). Only alias for calculated columns. Always use lower case column names.
	3. Answer strictly based on the provided text. Do not use outside knowledge or hallucinate facts. If the answer is not present, output is empty `[]`.
	4. Ensure handle case sensitivity by converting columns to lower case.
	"""
	messages = [{"role": "system", "content": system_prompt}]
	return _call_llm_sql(messages, user_query)

def generate_absence_sql(user_query, schema):
	"""
	Especialista em AUSÊNCIA/SILÊNCIO.
	Lida com "lógica negativa" (NOT IN), que é difícil para LLMs generalistas.
	"""
	system_prompt = f"""# ROLE
	Expert SQL Data Scientist (Absence Specialist).

	# GOAL
	Identify "Absent" clients based on the User's definition (Silence OR Status).

	# CONTEXT
	Schema: {schema}

	# INSTRUCTIONS
	1. DECIDE: Is the user asking for "No Contact" (Silence) OR "Inactive Status"? or Both?
	2. IF "OPERATIONAL SILENCE" (No recent contact):
	   - Join tables.
	   - Logic: `id_cliente` NOT IN (SELECT id_cliente FROM interacoes WHERE data >= calculated_threshold).
	   - Threshold: Use user's specific days (e.g., "15 days") or infer default.
	3. IF "STRUCTURAL INACTIVITY" (Status Inativo):
	   - Join tables.
	   - Logic: `clientes.status = 'Inativo'`.
	4. MUST INCLUDE: `CAST(julianday('now') - julianday(MAX(interacoes.data)) AS INTEGER)` AS dias_desde_ultima_interacao.

	# RULES
	1. SQLite Syntax Only.
	2. Output format MUST use the column names from the Schema (PT-BR). Only alias for calculated columns. Always use lower case column names.
	3. Answer strictly based on the provided text. Do not use outside knowledge or hallucinate facts. If the answer is not present, output is empty `[]`.
	4. Ensure handle case sensitivity by converting columns to lower case.
	"""
	messages = [{"role": "system", "content": system_prompt}]
	return _call_llm_sql(messages, user_query)

def generate_general_sql(user_query, schema):
	"""
	Especialista Generalista (Fallback).
	Lida com agregações (Soma, Contagem) e buscas simples.
	"""
	system_prompt = f"""# ROLE
	Expert SQL Data Scientist.

	# GOAL
	General SQL Queries (Aggregations, Financials, Dates).

	# CONTEXT
	Schema: {schema}

	# INSTRUCTIONS
	1. SYNONYM MAPPING:
	   - "Faturamento", "Valor", "Mensalidade" -> `contratos.valor_mensal`
	   - "Cliente", "Empresa", "Loja" -> `clientes.nome`
	   - "Vencimento", "Expira" -> `contratos.data_fim`
	2. JOIN LOGIC:
	   - Specific Company -> JOIN `contratos` + `clientes`.
	   - Active/Valid -> WHERE `status` = 'Ativo'.
	   - Total/Revenue -> SELECT SUM(valor_mensal).

	# RULES
	1. SQLite Syntax Only.
	2. Output format MUST use the column names from the Schema (PT-BR). Only alias for calculated columns. Always use lower case column names.
	3. Answer strictly based on the provided text. Do not use outside knowledge or hallucinate facts. If the answer is not present, output is empty `[]`.
	4. Ensure handle case sensitivity by converting columns to lower case.
	"""
	messages = [{"role": "system", "content": system_prompt}]
	return _call_llm_sql(messages, user_query)

def generate_sql_router(user_query, schema):
	"""
	Função Orquestradora do Router de SQL. Conecta o Intent Classifier aos Geradores.
	"""
	
	# Verifica Cache (Performance First)
	cached = get_cache(user_query)
	if cached:
		print(f"⚡ Cache Hit! (Intent: {cached['intent']})")
		logger.log("cache_hit", intent=cached['intent'], query=user_query)
		return cached['sql_generated'], cached['intent']

	# Logic (Roteamento + Generator)
	intent = classify_intent(user_query)
	print(f"🧠 Intenção Detectada: {intent}")
	logger.log("intent_route", intent=intent, query=user_query)
	
	if intent == "PROFILE":
		sql = generate_profile_sql(user_query, schema)
	elif intent == "HISTORY":
		sql = generate_history_sql(user_query, schema)
	elif intent == "RISK":
		sql = generate_risk_sql(user_query, schema)
	elif intent == "ABSENCE":
		sql = generate_absence_sql(user_query, schema)
	elif intent == "GREETING":
		return None, "GREETING"
	else:
		sql = generate_general_sql(user_query, schema)
		
	logger.log("sql_generated", sql=sql, intent=intent)
	
	# Save Cache (if valid!)
	save_cache(user_query, sql, intent)
	return sql, intent


# --- EXECUTION ---

def execute_sql(sql_query):
	"""
	Executa o SQL gerado no banco físico.
	Usa fetchall para recuperar dados e converte para lista de dicionários (JSON-friendly).
	"""
	start_time = datetime.now()
	try:
		conn = get_db_connection()
		cursor = conn.cursor()
		cursor.execute(sql_query)
		res = cursor.fetchall()
		result = [dict(row) for row in res]
		conn.close()
		
		end_time = datetime.now()
		duration_ms = (end_time - start_time).total_seconds() * 1000
		logger.log(
			event_type="sql_execution",
			duration_ms=round(duration_ms, 2),
			rows=len(result),
			status="success"
		)
		return result, None
	except Exception as e:
		end_time = datetime.now()
		duration_ms = (end_time - start_time).total_seconds() * 1000
		logger.log(
			event_type="sql_execution",
			duration_ms=round(duration_ms, 2),
			status="error",
			error=str(e)
		)
		return None, str(e)


# --- ANALYST PERSONA (RESPONSE GENERATION) ---

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
	O 'Analista' final. Pega os dados estruturados (SQL Result) e os transforma em uma resposta humana.
	O Prompt muda dinamicamente baseado na INTENÇÃO (Style Guide).
	"""
	today = datetime.now().strftime('%Y-%m-%d')
	# dynamic_examples = load_few_shot_examples(n=10) # Load diverse examples
	
	system_prompt = f"""# ROLE
	ClientaTech AI Analyst.

	# GOAL
	Answer a user query based on SQL data.

	# CONTEXT
	MODE: {intent}
	CURRENT_DATE: {today}

	# INSTRUCTIONS
	- IF MODE == 'PROFILE': 
		1. You MUST use the "Rich Profile Card" style (Status, Plan, Value + Observations).
		2. You can use emojis to make the response more engaging.
		Example:
		📌 Cliente: [Name]
		📊 Status: [Status]
		📄 Plano: [Plan]
		💰 Valor Mensal: R$ [Value]

		ℹ️ Observações:
		* [Observation 1, e.g., "Contrato active until..."]
		* [Observation 2, e.g., "Last interaction was..."]
	- IF MODE == 'HISTORY': 
		- You MUST use a Bulleted List of events.
		- FORMAT: "Date - Description (X days ago)".
	- IF MODE == 'RISK': 
		1. LOGIC: Risk = (dias_para_expirar < X) days OR (dias_desde_ultima_interacao > Y) days.
		2. SUBJECTIVITY HANDLING:
			- If user asks for "Bons/Melhores": Show clients with NO Risk (Active + Safe dates).
			- If user asks for "Ruins/Piores": Show clients WITH Risk.
		3. ALWAYS explicitly the criteria used to determine the risk.
		4. OUTPUT: List clients based on these logical criteria.
	- IF MODE == 'ABSENCE': 
		1. List the clients found.
		2. Mention `dias_desde_ultima_interacao` explicitly (e.g. "Sem contato há X dias").
	- IF MODE == 'GENERAL': Answer directly and concisely.
	- IF MODE == 'GREETING': 
		1. Introduce yourself as "ClientaTech AI Analyst".
		2. Briefly explain what you can do (Analyze Clients/Companies Profiles, History, Risk, and General Data).
		3. Give 3 examples of short questions the user can ask.
		4. Be professional but welcoming.

	# RULES
	1. OUTPUT LANGUAGE: Portuguese (pt-BR).
	2. TRUTH: If data is empty `[]`, say "Não encontrei informações" (Except for GREETING).
	3. TONE: Professional. Can use emojis to make the response more engaging.
	4. LOOK for calculated columns in the SQL result (e.g. 'dias_para_expirar', 'dias_desde_ultima_interacao') to explain timestamps.
	"""
	# EXAMPLES:
    # {dynamic_examples}
	
	user_content = f"""
	User Query: {user_query}
	SQL Used: {sql_query}
	Data Retrieved: {json.dumps(sql_result, ensure_ascii=False)}
	
	Generate response for mode {intent}.
	"""
	
	try:
		response = call_llm(
			model=MODEL_NAME,
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_content}
			],
			component="analyst_response"
		)
		final_text = response['message']['content']
		return final_text
	except Exception as e:
		logger.log("analyst_error", error=str(e))
		return f"Error response: {e}"


# --- MAIN APPLICATION LOOP ---

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
