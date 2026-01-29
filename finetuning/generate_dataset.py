import json
import random
import datetime

import os

def generate_robust_dataset_v3():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(BASE_DIR, 'dataset_finetuning.jsonl')
    
    system_prompt = (
        "Role: ClientaTech Advanced Analyst. "
        "Task: Receive structured data and User Intent, then generate a highly standardized, visual response in Portuguese (pt-BR). "
        "Rules: "
        "1. STRICTLY follow the formatting style for the given INTENT. "
        "2. Use specific Emojis for each metric. "
        "3. Never explain the SQL. Just present the insights. "
        "4. If data is empty, handle gracefully."
    )

    # --- DATA ASSETS ---
    companies = ["CyberDyne", "Acme Corp", "Stark Ind", "Wayne Ent", "Globex", "Massive Dynamic", 
                 "InGen", "Tyrell Corp", "Umbrella", "Oscorp", "LexCorp", "Roxxon", "Veidt Ent", 
                 "Soylent", "MomCorp", "Aperture", "Black Mesa", "CyberLife", "Delos", "E-Corp"]
    plans = ["Starter", "Pro", "Enterprise", "Corporate", "Unlimited"]
    segments = ["Tecnologia", "Varejo", "Saúde", "Finanças", "Indústria", "Educação"]

    def random_date(start_year=2024, end_year=2026):
        start = datetime.date(start_year, 1, 1)
        end = datetime.date(end_year, 12, 31)
        return start + datetime.timedelta(days=random.randint(0, (end - start).days))

    examples = []

    # ==========================================
    # 1. PROFILE INTENT (Diverse Queries)
    # ==========================================
    print("generating PROFILES (Diverse)...")
    profile_templates = [
        "Me fale sobre a empresa {comp}",
        "Dados da {comp}",
        "Qual o status da {comp}?",
        "Qual o plano contratado pela {comp}?",
        "Resumo do cliente {comp}",
        "A {comp} está ativa?",
        "Ficha cadastral da {comp}"
    ]
    
    for _ in range(25):
        comp = random.choice(companies)
        template = random.choice(profile_templates)
        question = template.format(comp=comp)
        
        status = random.choice(["Ativo", "Ativo", "Ativo", "Inativo"])
        plan = random.choice(plans)
        val = random.randint(10, 500) * 100
        end_date = random_date(2025, 2028)
        last_int = random_date(2025, 2025)
        
        data = [{
            "nome": comp, "status": status, "plano": plan, 
            "valor": float(val), "data_fim": str(end_date), "ult_interacao": str(last_int)
        }]
        
        status_icon = "✅" if status == "Ativo" else "🔴"
        
        # Robust Profile Response covering all potential questions
        response = f"""📌 Cliente: {comp}
📊 Status: {status_icon} {status}
📄 Plano: {plan}
💰 Valor Mensal R$ {val:,.2f}

ℹ️ Observações:
- 📅 Vencimento do Contrato {end_date.strftime('%d/%m/%Y')}
- 🗣️ Última Interação {last_int.strftime('%d/%m/%Y')} """
        
        examples.append({"q": question, "intent": "PROFILE", "data": data, "response": response})

    # ==========================================
    # 2. RISK INTENT (Diverse Queries)
    # ==========================================
    print("generating RISKS (Diverse)...")
    risk_templates = [
         "Quais clientes apresentam risco de churn?",
         "Existe algum cliente em risco?",
         "Análise de cancelamento",
         "Quem está para sair?",
         "Clientes insatisfeitos",
         "Risco financeiro na base?"
    ]

    for _ in range(15):
        question = random.choice(risk_templates)
        c1 = random.choice(companies)
        
        data = [
            {"nome": c1, "data_fim": str(random_date(2025,2025)), "ult_interacao": str(random_date(2024,2024))}
        ]
        
        response = f"""🚨 Alerta de Risco de Churn

Identifiquei clientes com indicadores críticos:
1.  🔴 {c1}
-   ⏳ Vence em: {data[0]['data_fim']}
-   🔇 Silêncio: Sem contato desde {data[0]['ult_interacao']}.
-   ⚠️ Diagnóstico: Risco Iminente."""
        
        examples.append({"q": question, "intent": "RISK", "data": data, "response": response})

    # ==========================================
    # 3. GENERAL INTENT (Agregations & KPIs)
    # ==========================================
    print("generating GENERAL (Diverse)...")
    general_scenarios = [
        ("Qual o faturamento mensal atual?", "💰 R$ {val:,.2f}", "indicator"),
        ("Quantos clientes por segmento?", "📊 Distribuição:\n* Tech: {n1}\n* Varejo: {n2}", "distribution"),
        ("Qual a média dos contratos?", "💰 Ticket Médio: R$ {val:,.2f}", "indicator"),
        ("Total de clientes ativos?", "✅ Total: {n1} clientes", "count"),
        ("Qual o maior contrato?", "🏆 Maior Contrato: {comp} (R$ {val:,.2f})", "record")
    ]
    
    for _ in range(15):
        scenario = random.choice(general_scenarios)
        question = scenario[0]
        
        val = random.randint(1000, 50000)
        n1 = random.randint(5, 50)
        n2 = random.randint(5, 50)
        comp = random.choice(companies)
        
        formatted_response = scenario[1].format(val=val, n1=n1, n2=n2, comp=comp)
        
        # Mock Data Structure based on type
        if scenario[2] == "indicator": data = [{"valor": val}]
        elif scenario[2] == "distribution": data = [{"seg": "Tech", "qtd": n1}, {"seg": "Varejo", "qtd": n2}]
        elif scenario[2] == "record": data = [{"nome": comp, "valor": val}]
        else: data = [{"count": n1}]

        response = f"""📊 Relatório Gerencial\n\n{formatted_response}"""
        examples.append({"q": question, "intent": "GENERAL", "data": data, "response": response})

    # ==========================================
    # 4. HISTORY & ABSENCE (Standard)
    # ==========================================
    # Keeping these simple but iterating logic
    print("generating HISTORY & ABSENCE...")
    for _ in range(10):
        c = random.choice(companies)
        examples.append({
            "q": f"Interações da {c}", 
            "intent": "HISTORY", 
            "data": [{"data": "2025-01-01", "tipo": "Vendas"}], 
            "response": f"📜 Histórico: {c}\n\n* 📆 01/01/2025 - Vendas"
        })
        
    for _ in range(8):
         examples.append({
            "q": "Clientes sem contato", 
            "intent": "ABSENCE", 
            "data": [{"nome": "InGen"}], \
            "response": "📉 Ausência de Contato\n\n* 🔇 InGen"
        })

    # OUTPUT
    total = len(examples)
    print(f"🔄 Gerando dataset v3 com {total} exemplos variados...")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        for ex in examples:
            user_content_str = (
                f"User Query: {ex['q']}\n"
                f"Intent Detected: {ex['intent']}\n"
                f"Data Retrieved: {json.dumps(ex['data'], ensure_ascii=False)}"
            )
            entry = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content_str},
                    {"role": "assistant", "content": ex['response']}
                ]
            }
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            
    print(f"✅ Sucesso: {total} exemplos gerados em '{output_file}'.")

if __name__ == "__main__":
    generate_robust_dataset_v3()
