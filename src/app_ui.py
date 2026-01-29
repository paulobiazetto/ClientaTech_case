import streamlit as st
import agent
import uuid

# Page Config
st.set_page_config(
    page_title="ClientaTech AI",
    page_icon="🤖",
    layout="wide"
)

# Initialize Backend Infrastructure
agent.init_cache()

# --- 1. GERENCIAMENTO DE ESTADO & SEGURANÇA ---

# Inicializa o dicionário de chats
if "chats" not in st.session_state:
    initial_id = str(uuid.uuid4())
    st.session_state.chats = {
        initial_id: {"title": "Nova Conversa", "messages": []}
    }
    st.session_state.current_chat_id = initial_id

# Inicializa o ID atual (caso não exista)
if "current_chat_id" not in st.session_state:
    st.session_state.current_chat_id = list(st.session_state.chats.keys())[0]

# SAFETY CHECK: Garante que o chat atual realmente existe no dicionário
# (Previne erros se um chat for deletado ou o estado corrompido)
if st.session_state.current_chat_id not in st.session_state.chats:
    st.session_state.current_chat_id = list(st.session_state.chats.keys())[0]

def create_new_chat():
    new_id = str(uuid.uuid4())
    st.session_state.chats[new_id] = {"title": "Nova Conversa", "messages": []}
    st.session_state.current_chat_id = new_id

# --- 2. SIDEBAR (Layout Refinado) ---
with st.sidebar:
    # Título e Subtítulo
    st.title("🤖 ClientaTech")
    st.caption("🚀 AI Agentic Analyst")
    
    st.markdown("---")

    # Botão de Ação Principal
    if st.button("➕ Nova Conversa", use_container_width=True, type="primary"):
        create_new_chat()
        st.rerun()
    
    # Espaçamento visual para separar a ação do histórico
    st.markdown("<br>", unsafe_allow_html=True) 
    
    # Cabeçalho do Histórico
    st.subheader("📜 Histórico")
    
    # Lista de Conversas
    chat_ids = list(st.session_state.chats.keys())
    
    # Loop reverso para conversas novas no topo
    for chat_id in reversed(chat_ids):
        chat_data = st.session_state.chats[chat_id]
        
        # LÓGICA VISUAL: 
        # Se for o chat atual, botão é "primary" (destaque). Se não, "secondary".
        # Isso elimina a necessidade da coluna com o ícone "📍", deixando mais simétrico.
        is_active = (chat_id == st.session_state.current_chat_id)
        button_type = "primary" if is_active else "secondary"
        
        # Ícone dinâmico no texto do botão
        icon = "📂" if is_active else "💬"
        label = f"{icon} {chat_data['title']}"
        
        if st.button(label, key=chat_id, use_container_width=True, type=button_type):
            st.session_state.current_chat_id = chat_id
            st.rerun()

# --- 3. ÁREA PRINCIPAL ---

current_chat = st.session_state.chats[st.session_state.current_chat_id]
messages = current_chat["messages"]

# TELA DE BOAS-VINDAS (Se não houver mensagens)
if not messages:
    # Cria um container centralizado para melhor estética
    with st.container():
        st.markdown("""
        <div style='text-align: center; padding-top: 50px; color: #666;'>
            <h1>Olá! 👋</h1>
            <p>Sou seu Analista de Dados Inteligente.</p>
            <p><i>Pergunte sobre contratos, analise riscos ou extraia insights de clientes.</i></p>
        </div>
        """, unsafe_allow_html=True)

# Exibe Histórico de Mensagens
for message in messages:
    with st.chat_message(message["role"]):
        # Mostra o pensamento (SQL) em um expander discreto
        if "sql" in message and message["sql"]:
            with st.expander(f"🧠 Processo Lógico ({message.get('intent', 'Query')})"):
                st.code(message["sql"], language="sql")
        
        st.markdown(message["content"])

# --- 4. ÁREA DE INPUT ---

# Espaçador para garantir que o input não fique colado na última mensagem
st.markdown("<br>", unsafe_allow_html=True)

# Container de Status (Fixo acima do input)
status_container = st.empty()
status_container.caption("🟢 **Sistema Online**")

if prompt := st.chat_input("Digite sua pergunta de negócio aqui..."):
    
    # 1. Feedback Imediato
    status_container.caption("🚀 **Iniciando Agente...**")
    
    # 2. Define Título Inteligente (apenas na 1ª mensagem)
    if len(messages) == 0:
        words = prompt.split()
        title_summary = " ".join(words[:4]) + ("..." if len(words) > 4 else "")
        st.session_state.chats[st.session_state.current_chat_id]["title"] = title_summary
    
    # 3. Renderiza msg do usuário
    messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 4. Processamento do Agente
    with st.chat_message("assistant"):
        generated_sql = None
        detected_intent = None
        final_response_text = ""

        try:
            # Container de Status Expandido
            with st.status("🔍 **Analisando dados...**", expanded=True) as status_box:
                
                st.write("🧠 *Compreendendo contexto e gerando query...*")
                schema = agent.get_schema()
                generated_sql, detected_intent = agent.generate_sql_router(prompt, schema)
                
                if detected_intent == "GREETING":
                     status_box.update(label="💬 **Conversando...**", state="complete", expanded=False)
                     final_response_text = agent.generate_final_response(prompt, "SKIP", [], detected_intent)
                
                elif "Error" in generated_sql:
                    status_box.update(label="❌ Falha no Raciocínio", state="error")
                    final_response_text = f"Não consegui processar a lógica: {generated_sql}"
                else:
                    st.write("⚙️ *Executando busca no banco de dados...*")
                    result, error = agent.execute_sql(generated_sql)
                    
                    if error:
                        status_box.update(label="❌ Erro de Execução SQL", state="error")
                        final_response_text = f"Erro técnico ao consultar dados: {error}"
                    else:
                        st.write("📝 *Sintetizando resposta executiva...*")
                        final_response_text = agent.generate_final_response(prompt, generated_sql, result, detected_intent)
                        status_box.update(label="✅ **Análise Concluída**", state="complete", expanded=False)

            # Exibe SQL gerado (se válido)
            if generated_sql and "Error" not in generated_sql:
                with st.expander(f"🧠 Ver Query SQL ({detected_intent})"):
                    st.code(generated_sql, language="sql")
            
            # Exibe Resposta Final
            st.markdown(final_response_text)
            
            # Salva no Histórico
            messages.append({
                "role": "assistant", 
                "content": final_response_text,
                "sql": generated_sql if (generated_sql and "Error" not in generated_sql) else None,
                "intent": detected_intent
            })
            
            # Atualiza a sidebar para refletir novo título (se for o caso)
            if len(messages) == 2:
                st.rerun()

        except Exception as e:
            st.error(f"Erro Crítico no Sistema: {e}")
            status_container.error("❌ Erro Crítico")
        
        finally:
            status_container.caption("🟢 **Sistema Online**")