import json
import random
from datetime import datetime, timedelta
import os

def generate_robust_finetuning():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(BASE_DIR, 'dataset_finetuning_v2.jsonl')
    
    # SYSTEM PROMPT (O mesmo de Produção)
    system_prompt = (
        "Role: ClientaTech AI Analyst. "
        "Task: Transform raw SQL data into a professional response based on user Intent. "
        "Rules: "
        "1. LANGUAGE: Portuguese (pt-BR). "
        "2. IF DATA IS EMPTY: Respond 'Não encontrei registros correspondentes.' (Except Greeting). "
        "3. PROFILE INTENT: Use the fixed 'Rich Card' format with emojis. "
        "4. SEMANTIC INTENT: Always explain the criteria used (e.g., 'Critério: Vence em < 60 dias'). "
        "5. AMBIGUOUS INTENT: Be objective. Do not say 'Good/Bad', show the numbers. "
        "6. DATES: Use relative time (e.g., 'vence em 5 dias')."
    )

    # --- 1. GERADORES DE DADOS SINTÉTICOS ---
    prefixes = ["Neo", "Alpha", "Omega", "Prime", "Global", "Vortex", "Horizon", "Blue", "Iron", "Silver", "Nexus", "Quantum", "Hyper", "Star", "Mega", "Ultra", "Dyna"]
    suffixes = ["Tech", "Solutions", "Systems", "Corp", "Varejo", "Logistics", "Consulting", "Brasil", "Global", "Group", "Foods", "Pharma", "Soft", "Energy", "Labs"]
    
    def get_random_company():
        return f"{random.choice(prefixes)}{random.choice(suffixes)}"

    def get_unique_companies(n=2):
        """Gera N empresas garantindo que não sejam iguais"""
        comps = set()
        while len(comps) < n:
            comps.add(get_random_company())
        return list(comps)

    def get_random_price():
        base = random.randint(15, 150) * 100 
        variation = random.choice([0, 50, 90, 99])
        return float(base + variation)

    def get_date(offset_days):
        return (datetime.now() + timedelta(days=offset_days)).strftime("%Y-%m-%d")

    plans = ["Basic", "Starter", "Standard", "Pro", "Advanced", "Premium", "Gold", "Platinum", "Enterprise", "Ultimate"]
    segments = ["Varejo", "Tecnologia", "Saúde", "Finanças", "Educação", "Indústria", "Agro", "Logística"]

    examples = []

    # --- 2. TEMPLATES DE PERGUNTAS (VARIEDADE LINGUÍSTICA) ---
    
    templates_profile = [
        "Dados da {comp}", "Quem é o cliente {comp}?", "Me fale sobre a {comp}",
        "Qual o status da empresa {comp}?", "Ficha técnica da {comp}", 
        "Perfil do cliente {comp}", "Resumo contratual da {comp}",
        "A empresa {comp} está ativa?", "Mostre as informações da {comp}"
    ]

    templates_aggregated_fat = [
        "Qual o faturamento mensal?", "Quanto a empresa fura por mês?", 
        "Valor total dos contratos mensais", "Receita mensal atual", 
        "Somatória das mensalidades", "Total de faturamento"
    ]

    templates_aggregated_seg = [
        "Quantos clientes temos em {seg}?", "Número de empresas do setor {seg}",
        "Contagem de clientes por segmento: {seg}", "Total no segmento {seg}",
        "Distribuição de clientes em {seg}"
    ]

    templates_temporal = [
        "Quais contratos vencem este mês?", "Lista de contratos expirando", 
        "O que vence nos próximos dias?", "Clientes com contrato acabando",
        "Prazos de vencimento próximos", "Renovações pendentes"
    ]

    templates_semantic_risk = [
        "Quais clientes têm risco de churn?", "Quem está em risco de sair?", 
        "Lista de clientes com probabilidade de cancelamento", "Alerta de churn",
        "Clientes críticos (risco)", "Empresas com risco de evasão"
    ]

    templates_ambiguous = [
        "O cliente {comp} é bom?", "A {comp} vale a pena?", 
        "Como está a situação da {comp}?", "A empresa {comp} é confiável?",
        "Qual a reputação da {comp}?", "Devo me preocupar com a {comp}?"
    ]

    # ==============================================================================
    # 3. GERAÇÃO DE EXEMPLOS (LOOP PRINCIPAL)
    # ==============================================================================

    # --- A. PROFILE ---
    print("🔹 Gerando PROFILE (Com Lógica de Status Realista)...")
    
    for _ in range(40):
        # 1. Escolhe empresa e pergunta dinamicamente
        comp = get_random_company()
        template = random.choice(templates_profile)
        question = template.format(comp=comp) # Ex: "A empresa NeoTech está ativa?"
        
        # 2. Gera datas variadas (incluindo passadas/vencidas)
        days_end = random.randint(-100, 400) 
        days_last = random.randint(1, 90)
        
        # 3. LÓGICA DE CONSISTÊNCIA (Status vs Data)
        # Se data_fim < 0, o contrato venceu. Status NÃO pode ser 'Ativo'.
        if days_end < 0:
            status = random.choice(["Inativo", "Suspenso", "Bloqueado"])
            # Texto visual para o Analista usar na resposta
            # obs_vencimento = f"⚠️ VENCIDO há {abs(days_end)} dias"
        elif days_end < 30:
            status = "Ativo"
            # obs_vencimento = f"⚠️ Vence em {days_end} dias" # Urgência
        else:
            status = "Ativo"
            # obs_vencimento = f"Vence em {days_end} dias" # Normal

        plan = random.choice(plans)
        val = get_random_price()

        # 4. Monta o JSON (Simulando o retorno do SQL)
        raw_data = [{
            "nome": comp, 
            "status": status, 
            "plano": plan, 
            "valor_mensal": val, 
            "data_fim": get_date(days_end), 
            "ultima_interacao": get_date(-days_last),
            "dias_para_expirar": days_end,
            "dias_desde_ultima_interacao": days_last
        }]
        
        # 5. O USER_MSG FINAL (Aqui está a resposta da sua pergunta)
        # Note que usamos 'question' (variável) e não a string fixa
        user_msg = f"Intent: PROFILE\nData: {json.dumps(raw_data, ensure_ascii=False)}\nQuery: {question}"
        
        # 6. RESPOSTA ESPERADA (Target do Fine-Tuning)
        # [cite_start]Segue o formato obrigatório do case [cite: 71]
        response = f"""📌 Cliente: {comp}
📊 Status: {status}
📄 Plano: {plan}
💰 Valor Mensal: R$ {val:,.2f}

ℹ️ Observações:
- Contrato: {get_date(days_end)}
- Última interação há {days_last} dias"""
        
        examples.append({"input": user_msg, "output": response})

    # --- B. AGGREGATED ---
    templates_aggregated_seg = [
        ("Quantos clientes ativos em {seg}?", "ativos"),
        ("Total de clientes em {seg}?", "registrados"),
        ("Número de contratos em {seg}?", "na base")
    ]

    for _ in range(10):
        seg = random.choice(segments)
        qtd = random.randint(5, 100)
        
        # Escolhe um par (pergunta, termo_contexto)
        tpl_query, termo = random.choice(templates_aggregated_seg)
        question = tpl_query.format(seg=seg)
        
        user_msg = f"Intent: AGGREGATED\nData: {json.dumps([{'segmento': seg, 'qtd': qtd}])}\nQuery: {question}"
        
        # O modelo aprende a usar o termo que faz sentido com a pergunta, 
        # ou usa um termo genérico se o dado for apenas um número.
        response = f"O segmento {seg} possui um total de {qtd} clientes {termo}."
        
        examples.append({"input": user_msg, "output": response})

    # --- C. TEMPORAL ---
    print("🔹 Gerando TEMPORAL...")
    for _ in range(10):
        c1, c2 = get_unique_companies(2) # SOLUÇÃO PARA O SEU PROBLEMA
        d1, d2 = random.randint(1, 15), random.randint(16, 30)
        
        raw_data = [
            {"nome": c1, "data_fim": get_date(d1)},
            {"nome": c2, "data_fim": get_date(d2)}
        ]
        question = random.choice(templates_temporal)
        user_msg = f"Intent: TEMPORAL\nData: {json.dumps(raw_data)}\nQuery: {question}"
        
        response = f"""Encontrei os seguintes vencimentos para o período:

* {c1}: Vence em {d1} dias ({get_date(d1)})
* {c2}: Vence em {d2} dias ({get_date(d2)})"""
        examples.append({"input": user_msg, "output": response})

    # --- D. SEMANTIC (Risco - Análise Dinâmica) ---
    print("🔹 Gerando SEMANTIC (Evidência, não Regra Fixa)...")
    for _ in range(10):
        comp = get_random_company()
        question = random.choice(templates_semantic_risk)
        
        # Geramos dados que justificam o risco, mas com valores variados
        days_to_expire = random.randint(1, 59) # Qualquer valor abaixo de 60
        days_silence = random.randint(46, 120) # Qualquer valor acima de 45
        
        raw_data = [{
            "nome": comp, 
            "data_fim": get_date(days_to_expire), 
            "dias_para_expirar": days_to_expire, 
            "dias_desde_ultima_interacao": days_silence
        }]
        
        user_msg = f"Intent: SEMANTIC\nData: {json.dumps(raw_data)}\nQuery: {question}"
        
        # ESTRATÉGIA: O modelo explica o PORQUÊ com base nos números que ele VÊ, 
        # em vez de citar uma regra estática que ele "decorou".
        
        # Variações de explicação para ele aprender a raciocinar
        phrasing = random.choice([
            "Identifiquei indicadores críticos de retenção:",
            "Este cliente apresenta alto risco de churn devido à combinação de fatores:",
            "Alerta de risco gerado pelos seguintes critérios:"
        ])

        response = f"""{phrasing}

🚨 Diagnóstico:
* Vencimento Iminente: O contrato vence em apenas {days_to_expire} dias.
* Baixo Engajamento: Não há registro de interação há {days_silence} dias.

Recomenda-se ação proativa imediata para garantir a renovação."""
        
        examples.append({"input": user_msg, "output": response})

    # --- F. HISTORY (New w/ dias_antes) ---
    print("🔹 Gerando HISTORY (Time Aware - PT keys)...")
    templates_history = ["Histórico do {comp}", "Interações da {comp}", "O que houve com a {comp}?"]
    
    for _ in range(10):
        comp = get_random_company()
        question = random.choice(templates_history).format(comp=comp)
        
        # Simulate 2 interactions
        d1, d2 = random.randint(2, 5), random.randint(10, 30)
        raw_data = [
            {"data": get_date(-d1), "tipo": "Suporte", "descricao": "Ticket aberto", "dias_antes": d1},
            {"data": get_date(-d2), "tipo": "Vendas", "descricao": "Reunião Mensal", "dias_antes": d2}
        ]
        
        user_msg = f"Intent: HISTORY\nData: {json.dumps(raw_data)}\nQuery: {question}"
        
        response = f"""Histórico recente de {comp}:

* 📞 {get_date(-d1)} - Suporte: Ticket aberto (há {d1} dias)
* 🤝 {get_date(-d2)} - Vendas: Reunião Mensal (há {d2} dias)"""
        
        examples.append({"input": user_msg, "output": response})

    # --- G. ABSENCE (New w/ dias_desde_ultima_interacao) ---
    print("🔹 Gerando ABSENCE...")
    for _ in range(10):
        comp = get_random_company()
        days_silence = random.randint(60, 200)
        
        raw_data = [{"nome": comp, "dias_desde_ultima_interacao": days_silence}]
        user_msg = f"Intent: ABSENCE\nData: {json.dumps(raw_data)}\nQuery: 'Clientes sem contato'"
        
        response = f"""⚠️ Alerta de Inatividade

O cliente {comp} está sem nenhuma interação há {days_silence} dias. 
Recomendamos um contato de reaquecimento urgente."""
        
        examples.append({"input": user_msg, "output": response})

    # --- E. AMBIGUOUS (20 exemplos) ---
    # --- E. AMBIGUOUS (Correção: Frequência em vez de Julgamento) ---
    print("🔹 Gerando AMBIGUOUS (Neutro)...")
    for _ in range(10):
        comp = get_random_company()
        question = random.choice(templates_ambiguous).format(comp=comp)
        
        tenure = random.randint(1, 5)
        inters = random.randint(0, 25) # Variar bem
        val = get_random_price()
        
        raw_data = [{"nome": comp, "anos_casa": tenure, "interacoes_90d": inters, "valor": val}]
        user_msg = f"Intent: AMBIGUOUS\nData: {json.dumps(raw_data)}\nQuery: {question}"
        
        # LÓGICA CORRETA: Transformar o número em frequência observável, sem adjetivar.
        if inters == 0:
            obs_interacao = "Nenhuma interação registrada no período"
        elif inters <= 3:
            obs_interacao = "Média de 1 interação/mês"
        else:
            # Arredondamento simples para dar noção de volume
            media = round(inters / 3, 1) 
            obs_interacao = f"Média aprox. de {media} interações/mês"
        
        # O modelo apresenta a MÉDIA (Fato Matemático), não a OPINIÃO "Bom/Ruim".
        response = f"""Analise os indicadores objetivos da empresa {comp}:

* Tempo de Casa: {tenure} anos
* Interações (90 dias): {inters} ({obs_interacao})
* Valor Contrato: R$ {val:,.2f}

Estes dados permitem avaliar a saúde da conta com base no histórico recente."""
        
        examples.append({"input": user_msg, "output": response})

    # --- F. GREETING & EMPTY (Fixos) ---
    # ... (Manter código anterior para greetings e empty cases) ...

    # SAVE
    random.shuffle(examples) # Importante embaralhar!
    print(f"\n🔄 Salvando {len(examples)} exemplos variados...")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        for ex in examples:
            json_line = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": ex['input']},
                    {"role": "assistant", "content": ex['output']}
                ]
            }
            f.write(json.dumps(json_line, ensure_ascii=False) + '\n')
            
    print(f"✅ Dataset gerado em: {output_file}")

if __name__ == "__main__":
    generate_robust_finetuning()
        
        
        
  