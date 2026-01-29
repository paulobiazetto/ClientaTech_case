# 🤖 ClientaTech AI Agent

O **ClientaTech AI Agent** é um assistente inteligente baseado em SQL e LLMs locais (via Ollama), projetado para analisar dados de clientes, contratos e interações. Ele utiliza uma arquitetura de roteamento semântico para classificar a intenção do usuário (Perfil, Risco, Histórico, etc.) e gerar consultas SQL precisas apenas quando necessário.

---

## 📂 Estrutura do Projeto

O projeto foi reorganizado para melhor modularidade:

*   **`src/`**: Código fonte principal da aplicação.
    *   `agent.py`: O "cérebro" do agente. Contém a lógica de conexão com LLM, Roteador Semântico e geradores SQL.
    *   `app_ui.py`: Interface Web interativa construída com **Streamlit**.
*   **`database/`**: Scripts de configuração e arquivos do banco de dados principal.
    *   `clientatech.db` / `clientatech_v2.db`: Banco de dados SQLite simulando o CRM.
    *   `setup_database.py`: Script para recriar o banco de dados com dados fictícios.
*   **`data/`**: Arquivos de dados auxiliares e cache.
    *   `cache.db`: Cache semântico para evitar chamadas repetitivas ao LLM/Banco.
*   **`finetuning/`**: Datasets e scripts para treinamento/ajuste de modelos.
    *   `dataset_finetuning.jsonl`: Arquivo de exemplos (Few-Shot/Fine-tuning).
    *   `generate_dataset.py`: Script gerador de datasets sintéticos.
*   **`logs/`**: Logs de execução do sistema.
    *   `agent.log`: Registro de atividades, erros e debug.

---

## 🚀 Como Executar

### 1. Pré-requisitos

*   **Python 3.8+** (Para execução local sem Docker)
*   **Docker & Docker Compose** (Recomendado)
*   **Ollama** instalado no host e rodando com o modelo `qwen2.5-coder:14b`.

### 2. Configuração do Banco de Dados

Antes de rodar, gere o banco de dados de teste:

```bash
python database/setup_database.py
```

### 3. Rodando com Docker (Recomendado)

A aplicação foi dockerizada para facilitar a execução da interface web.

1.  **Inicie o container:**

```bash
docker-compose up --build
```

2.  Acesse a aplicação no navegador em: `http://localhost:8502`

> **Nota:** O Docker está configurado para se conectar ao Ollama rodando na sua máquina local ("host"). Certifique-se de que o Ollama está rodando (`ollama serve`).

### 4. Rodando Manualmente (Sem Docker)

1.  Instale as dependências:
    ```bash
    pip install -r requirements.txt
    ```

2.  Execute a Interface Web:
    ```bash
    streamlit run src/app_ui.py
    ```

3.  Ou execute o Agente no Terminal:
    ```bash
    python src/agent.py
    ```

---

## 🧠 Arquitetura

1.  **Input do Usuário**: A pergunta entra no sistema.
2.  **Roteador Semântico (`classify_intent`)**: O LLM classifica a intenção (PROFILE, RISK, HISTORY, etc.).
3.  **Geração de SQL**: Um prompt especializado na intenção gera o SQL correto.
4.  **Execução Segura**: O SQL é executado no `clientatech.db`.
5.  **Analista Persona**: O LLM recebe os dados brutos e gera uma resposta em linguagem natural, formatada especificamente para a intenção (ex: Ficha cadastral com emojis, Alerta de Risco, etc.).

## 🛠️ Tecnologias

*   **Linguagem**: Python
*   **LLM Engine**: Ollama (Local)
*   **Container**: Docker
*   **Frontend**: Streamlit
