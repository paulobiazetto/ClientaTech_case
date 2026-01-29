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

# -----------------------------------------------------------------------------
# ARQUITETURA GERAL: AGENTE COM ROUTER DE INTENÇÕES (INTENT ROUTER PATTERN)
# -----------------------------------------------------------------------------
# Este agente segue o padrão de design "Router Architecture".
# Fluxo de processamento:
# 1. Usuário envia uma pergunta.
# 2. Router (Intent Classifier): Analisa a pergunta e decide qual "Especialista" deve resolver.
#    - Ex: Pergunta sobre dados cadastrais -> Especialista de Perfil (Profile).
#    - Ex: Pergunta sobre risco de saída -> Especialista de Risco (Risk).
# 3. Generator (SQL Expert): O especialista selecionado gera o SQL específico para aquela intenção.
# 4. Executor: O SQL é executado no banco de dados SQLite.
# 5. Analyst (Response Generator): Um modelo "Analista" pega os dados brutos e gera a resposta final em linguagem natural.
# -----------------------------------------------------------------------------

# --- 1. CONFIGURAÇÃO E SETUP ---

# Carrega variáveis de ambiente (ex: chaves de API, credenciais - embora aqui usemos Ollama local)
load_dotenv()

# Constantes de Caminhos
# Define os caminhos absolutos para garantir que o agente rode de qualquer diretório
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'data', 'clientatech.db')         # Banco de Dados Principal
CACHE_DB_PATH = os.path.join(BASE_DIR, 'data', 'cache.db')         # Banco de Cache Semântico
LOG_PATH = os.path.join(BASE_DIR, 'logs', 'agent.log')             # Arquivo de Logs Estruturados
FT_PATH = os.path.join(BASE_DIR, 'finetuning', 'dataset_finetuning_v2.jsonl')

# Modelos LLM
# Usamos dois modelos distintos para otimizar custo/performance:
# 1. qwen2.5-coder:14b -> Especialistas em Lógica, Code e SQL (Mais preciso para sintaxe).
# 2. llama3-finetuned -> Especialista em Conversação e Análise (Pode ter personalidade/tom ajustado).
MODEL_NAME = "qwen2.5-coder:14b"          
MODEL_NAME_FT = "llama3-finetuned:latest" 

# Inicializa o cliente Ollama (comunicação com a LLM rodando localmente)
client = Client()

# --- 2. LOGGING ESTRUTURADO (OBSERVABILIDADE) ---
# Em vez de logs de texto simples, usamos JSON Logs.
# Isso permite que ferramentas (como Grafana, ELK Stack, CloudWatch) analisem métricas automaticamente.
# Métricas capturadas: Latência (duration_ms), Tokens usados, Status (sucesso/erro), Componente.

class StructuredLogger:
	def __init__(self, log_path):
		os.makedirs(os.path.dirname(log_path), exist_ok=True)
		self.logger = logging.getLogger("ClientaTechAgent")
		self.logger.setLevel(logging.INFO)
		
		# File Handler: Escreve cada log como uma linha JSON
		file_handler = logging.FileHandler(log_path)
		file_handler.setFormatter(logging.Formatter('%(message)s'))
		self.logger.addHandler(file_handler)

	def log(self, event_type, **kwargs):
		"""
		Registra um evento.
		event_type: Nome do evento (ex: 'llm_call', 'sql_execution').
		kwargs: Dados arbitrários (ex: duration_ms=120, model='llama3').
		"""
		entry = {
			"timestamp": datetime.now().isoformat(),
			"event": event_type,
			**kwargs
		}
		self.logger.info(json.dumps(entry, ensure_ascii=False))

logger = StructuredLogger(LOG_PATH)


# --- 3. WRAPPER DE LLM COM MÉTRICAS ---
# Esta função centraliza todas as chamadas ao Ollama.
# Objetivo: Garantir que TODA chamada seja logada com métricas de performance.

def call_llm(model, messages, options=None, component="unknown"):
	"""
	Envolve o client.chat do Ollama para adicionar instrumentação.
	"""
	start_time = datetime.now()
	try:
		# Chama o modelo
		response = client.chat(model=model, messages=messages, options=options)
		end_time = datetime.now()
		duration_ms = (end_time - start_time).total_seconds() * 1000
		
		# Extrai métricas específicas do Ollama (tokens de prompt e completion)
		prompt_tokens = response.get('prompt_eval_count', 0)
		eval_tokens = response.get('eval_count', 0)
		
		# Loga o sucesso
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
		# Loga erro em caso de falha (importante para debug)
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


# --- 4. INFRAESTRUTURA (BANCO DE DADOS E CACHE) ---

def get_db_connection():
	"""Conecta ao banco de dados de negócio (SQLite)."""
	conn = sqlite3.connect(DB_PATH)
	conn.row_factory = sqlite3.Row # Permite acessar colunas pelo nome (row['nome'])
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

# --- CACHE SEMÂNTICO SIMPLIFICADO ---
# Armazena o par (Mensagem do Usuário -> SQL Gerado).
# Se o usuário fizer a mesma pergunta novamente, evitamos a chamada cara de LLM (geração de SQL).
# Nota: É um cache exato (hash md5). Caches puramente semânticos usariam embeddings vetoriais.

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
	# Segurança: Não cachear SQLs que deram erro
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


# --- 5. LÓGICA CORE: INTENÇÃO & GERADORES (ROUTER PATTERN) ---

def classify_intent(user_query):
	"""
	O 'Cérebro' do Router. Decide qual caminho seguir.
	Usa um prompt especializado em Classificação Taxonômica.
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
		# Temperatura 0.0 é crucial para tarefas de classificação (exige determinismo)
		response = call_llm(
			model=MODEL_NAME,
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_query}
			],
			options={"temperature": 0.0},
			component="intent_classifier"
		)
		content = response['message']['content'].strip()
		
		# Parser Robusto de JSON
		# LLMs às vezes colocam markdown em volta do JSON (```json ... ```). Precisamos limpar isso.
		try:
			 if "```json" in content:
				 import re
				 match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
				 if match: content = match.group(1)
				 
			 data = json.loads(content)
			 intent = data.get("category", "GREETING").strip().upper()
			 reasoning = data.get("reasoning", "No reasoning provided")
			 print(f"🤔 Raciocínio (DEBUG): {reasoning}")
			 
		except json.JSONDecodeError:
			 # Fallback seguro: se o JSON falhar, assume saudação para não quebrar.
			 logger.log("intent_error", error="JSON Parse Error", content=content)
			 intent = "GREETING" 

		# Validação Final e Heurística de Fallback
		valid_intents = ['PROFILE', 'HISTORY', 'RISK', 'ABSENCE', 'GENERAL', 'GREETING']
		intent_clean = intent.upper().strip()

		if intent_clean in valid_intents:
			intent = intent_clean
		else:
			# Se a LLM retornar algo estranho, tenta encontrar a intenção dentro da string retornada
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
	- Chama a LLM com temperatura baixa (0.1 para precisão).
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
		
		# Extração Regex: Procura por blocos ```sql ... ```
		match = re.search(r"```sql\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
		if match:
			return match.group(1).strip()
		
		# Fallback 1: Bloco genérico ``` ... ```
		match = re.search(r"```\s*(.*?)\s*```", content, re.DOTALL)
		if match:
			 return match.group(1).strip()
			 
		# Fallback 2: Limpeza manual
		cleaned = content.replace("```sql", "").replace("```", "").strip()
		
		# Validação de Segurança (Read-Only)
		# Garante que o modelo não gerou comandos destrutivos ou texto puro.
		if not cleaned.upper().startswith("SELECT") and not cleaned.upper().startswith("WITH"):
			print(content)
			# Gera um SQL que retorna um erro explícito para o sistema tratar
			return "SELECT 'Error: Model generated text instead of SQL' WHERE 0"
			
		return cleaned
	except Exception as e:
		return f"Error: {e}"

# --- 6. GERADORES DE SQL ESPECIALIZADOS ---
# Cada função abaixo representa um "Prompt Expert" otimizado para um tipo de tarefa.
# Todos seguem a estrutura padronizada: ROLE, GOAL, CONTEXT, INSTRUCTIONS, RULES.

def generate_profile_sql(user_query, schema):
	"""
	Especialista em PERFIL (Visão 360).
	Foca em joins precisos para trazer dados cadastrais + contratuais + última interação.
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
	Especialista em HISTÓRICO.
	Foca em listar eventos ordenados cronologicamente.
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
	Especialista em RISCO.
	Gera queries analíticas. Não julga o risco no SQL, mas extrai as métricas (dias para expirar, dias de silêncio)
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
	Função Orquestradora do Router de SQL.
	Conecta o Intent Classifier aos Geradores.
	"""
	
	# 1. Verifica Cache (Performance First)
	cached = get_cache(user_query)
	if cached:
		print(f"⚡ Cache Hit! (Intent: {cached['intent']})")
		logger.log("cache_hit", intent=cached['intent'], query=user_query)
		return cached['sql_generated'], cached['intent']

	# 2. Roteamento (Lógica)
	intent = classify_intent(user_query)
	print(f"🧠 Intenção Detectada: {intent}")
	logger.log("intent_route", intent=intent, query=user_query)
	
	# Despacha para o especialista correto
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
	
	# 3. Salva no Cache para futuro
	save_cache(user_query, sql, intent)
	return sql, intent


# --- 7. EXEUÇÃO DE SQL ---

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
		# Converte Row objects para dicts
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


# --- 8. ANALYST PERSONA (GERAÇÃO DE RESPOSTA) ---

def load_few_shot_examples(n=5):
	"""Helper para carregar exemplos de Few-Shot do dataset (opcional)."""
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
	print(sql_result) # Debug visual
	
	today = datetime.now().strftime('%Y-%m-%d')
	
	# Prompt Dinâmico (Orientado a Templates por Intenção)
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
		3. FILTER: Only show clients with Risk.
		4. ALWAYS explicitly the criteria used to determine the risk.
		5. OUTPUT: List clients based on these logical criteria.
	- IF MODE == 'ABSENCE': 
		- List the clients found.
		- Mention `dias_desde_ultima_interacao` explicitly (e.g. "Sem contato há X dias").
	- IF MODE == 'GENERAL': Answer directly and concisely.
	- IF MODE == 'GREETING': 
		1. Introduce yourself as "ClientaTech AI Analyst".
		2. Briefly explain what you can do (Analyze Profiles, History, Risk, and General Data).
		3. Give 3 examples of short questions the user can ask.
		4. Be professional but welcoming.

	# RULES
	1. OUTPUT LANGUAGE: Portuguese (pt-BR).
	2. TRUTH: If data is empty `[]`, say "Não encontrei informações" (Except for GREETING).
	3. TONE: Professional. Can use emojis to make the response more engaging.
	4. LOOK for calculated columns in the SQL result (e.g. 'dias_para_expirar', 'dias_desde_ultima_interacao') to explain timestamps.
	"""
	
	user_content = f"""
	User Query: {user_query}
	SQL Used: {sql_query}
	Data Retrieved: {json.dumps(sql_result, ensure_ascii=False)}
	
	Generate response for mode {intent}.
	"""
	
	try:
		response = call_llm(
			model=MODEL_NAME, # Pode usar MODEL_NAME_FT aqui se tiver o modelo finetunado
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


# --- 9. LOOP PRINCIPAL DA APLICAÇÃO ---

def main():
	print(f"🤖 **ClientaTech AI Agent [Router Architecture]** initialized.")
	
	# Inicialização
	init_cache()
	schema = get_schema()
	
	# Loop Interativo (Ouvindo CLI)
	while True:
		try:
			user_query = input("\n👤 Você: ")
			if user_query.lower() in ['exit', 'quit', 'sair']:
				print("👋 Encerrando...")
				break
			
			# Passo 1 e 2: Roteamento e Geração de SQL
			print("⏳ Processando...")
			sql_query, intent = generate_sql_router(user_query, schema)
			
			# Tratamento especial para saudações (bypass do SQL)
			if intent == "GREETING":
				final_response = generate_final_response(user_query, "SKIP", [], intent)
				print(f"\n{final_response}")
				continue
			
			# Tratamento de erro na geração
			if "Error" in sql_query:
				print(f"❌ {sql_query}")
				continue
				
			print(f"🔍 SQL (Intenção: {intent}): {sql_query}")
			
			# Passo 3: Execução
			print("⏳ Executando...")
			result, error = execute_sql(sql_query)
			
			if error:
				print(f"❌ Erro na execução: {error}")
				continue
			
			print(f"📊 Resultados encontrados: {len(result) if result else 0}")
			
			# Passo 4: Resposta do Analista
			print("⏳ Formatando resposta...")
			final_response = generate_final_response(user_query, sql_query, result, intent)
			print(f"\n{final_response}")
			
		except KeyboardInterrupt:
			break

if __name__ == "__main__":
	main()
