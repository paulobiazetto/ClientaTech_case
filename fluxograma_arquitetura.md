# Agente ClientaTech - Fluxograma da Arquitetura

Este diagrama ilustra a **Arquitetura de Roteamento (Router Architecture)** implementada no agente, mostrando como diferentes modelos são usados para estágios específicos (Roteamento/Geração de Código vs. Análise/Persona).

```mermaid
graph TD
    %% Nós
    User(("👤 Usuário"))
    subgraph CoreAgent["Núcleo do Agente (Orquestrador)"]
        Input["Consulta do Usuário"]
        Router{"🧠 Roteador de Intenção<br/>(Classificador)"}
        Cache[("⚡ Cache Semântico")]
    end

    subgraph Model1["Modelo 1: qwen2.5-coder:14b<br/>(Especialista em Lógica e Código)"]
        GenProfile["Especialista SQL: Perfil"]
        GenHistory["Especialista SQL: Histórico"]
        GenRisk["Especialista SQL: Risco"]
        GenAbsence["Especialista SQL: Ausência"]
        GenGeneral["Especialista SQL: Geral"]
    end

    subgraph ExecutionLayer["Camada de Execução"]
        Executor["⚙️ Executor SQLite"]
        DB[("🗄️ Banco de Dados")]
    end

    subgraph Model2["Modelo 2: llama3-finetuned<br/>(Persona Analista)"]
        Analyst["🗣️ Gerador de Resposta<br/>(Tom de Analista de Dados)"]
    end

    %% Fluxo
    User --> Input
    Input --> Cache
    Cache -- Hit (Encontrado) --> Executor
    Cache -- Miss (Não Encontrado) --> Router
    
    %% Roteamento
    Router -- PERFIL --> GenProfile
    Router -- HISTÓRICO --> GenHistory
    Router -- RISCO --> GenRisk
    Router -- AUSÊNCIA --> GenAbsence
    Router -- GERAL --> GenGeneral
    Router -- SAUDAÇÃO --> Greeting["Saudação Simples"]

    %% Geração
    GenProfile --> SQL["SQL Gerado"]
    GenHistory --> SQL
    GenRisk --> SQL
    GenAbsence --> SQL
    GenGeneral --> SQL

    %% Execução
    SQL --> Executor
    Executor <--> DB
    Executor --> Results["📊 Resultados Estruturados"]

    %% Análise
    Results --> Analyst
    IntentContext["Contexto da Intenção"] -.-> Analyst
    Greeting --> Output["Resposta Final"]
    Analyst --> Output

    Output --> User

    %% Estilos
    classDef model1 fill:#e1f5fe,stroke:#01579b,stroke-width:2px;
    classDef model2 fill:#f3e5f5,stroke:#4a148c,stroke-width:2px;
    classDef db fill:#fff3e0,stroke:#e65100,stroke-width:2px;
    
    class Router,GenProfile,GenHistory,GenRisk,GenAbsence,GenGeneral model1;
    class Analyst model2;
    class Cache,DB,Executor db;
```

## Detalhamento dos Componentes

1.  **Roteador de Intenção (`qwen2.5-coder:14b`)**
    *   **Função**: Classificação.
    *   **Tarefa**: Analisa a consulta bruta e a categoriza em um escopo funcional (ex: "Isso é uma pergunta sobre Risco?").
    *   **Por que este modelo?**: Requer forte raciocínio lógico para distinguir diferenças sutis (ex: "Silêncio" vs "Ausência").

2.  **Especialistas em SQL (`qwen2.5-coder:14b`)**
    *   **Função**: Geração de Código.
    *   **Tarefa**: Recebe a intenção específica e converte linguagem natural em sintaxe SQLite precisa.
    *   **Por que este modelo?**: Modelos 'Coder' são ajustados (fine-tuned) para correção de sintaxe e seguimento estrito de regras de esquema.

3.  **Executor**
    *   **Função**: Execução de determinística.
    *   **Tarefa**: Roda o SQL contra o banco de dados local para recuperar dados brutos (JSON/Dicts).

4.  **Analista (`llama3-finetuned`)**
    *   **Função**: Geração de Linguagem Natural.
    *   **Tarefa**: Pega os números brutos e a intenção do usuário para elaborar uma resposta profissional e útil (`pt-BR`).
    *   **Por que este modelo?**: Modelos de propósito geral (como Llama 3) são melhores em "falar" e manter uma persona/tom consistente do que modelos especializados em código.
