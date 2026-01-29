import sqlite3
import random
from datetime import datetime, timedelta

import os

def setup_database():
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DB_PATH = os.path.join(BASE_DIR, 'data', 'clientatech.db')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Create Tables
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS clientes (
        id_cliente INTEGER PRIMARY KEY,
        nome STRING NOT NULL,
        segmento STRING,
        status STRING, -- 'Ativo', 'Inativo'
        data_cadastro DATE
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS contratos (
        id_contrato INTEGER PRIMARY KEY,
        id_cliente INTEGER,
        plano STRING, -- 'Basic', 'Pro', 'Enterprise'
        valor_mensal FLOAT,
        data_inicio DATE,
        data_fim DATE,
        status STRING, -- 'Ativo', 'Encerrado'
        FOREIGN KEY(id_cliente) REFERENCES clientes(id_cliente)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS interacoes (
        id_interacao INTEGER PRIMARY KEY,
        id_cliente INTEGER,
        tipo STRING, -- 'Suporte', 'Vendas', 'Financeiro'
        descricao TEXT,
        data DATETIME,
        FOREIGN KEY(id_cliente) REFERENCES clientes(id_cliente)
    )
    ''')

    # 2. Clear existing data to avoid duplicates on re-run
    cursor.execute('DELETE FROM interacoes')
    cursor.execute('DELETE FROM contratos')
    cursor.execute('DELETE FROM clientes')

    # 3. Generate Dummy Data
    segmentos = ['Varejo', 'Tecnologia', 'Saúde', 'Finanças', 'Educação']
    planos = {'Basic': 1500.0, 'Pro': 3500.0, 'Enterprise': 8000.0}
    nomes_empresas = [
        'TechSolutions', 'MegaVarejo', 'SaudeMais', 'EducaNet', 'FinanceOne',
        'AlphaLog', 'BetaConstruct', 'GamaServices', 'DeltaTrade', 'EpsilonFood',
        'ZetaPharma', 'EtaEnergy', 'ThetaSystems', 'IotaSoft', 'KappaConsulting'
    ]

    clientes = []
    # Create 15 clients
    for i, nome in enumerate(nomes_empresas, 1):
        segmento = random.choice(segmentos)
        status = 'Ativo' if random.random() > 0.2 else 'Inativo' # 80% active
        data_cadastro = datetime.now() - timedelta(days=random.randint(60, 730))
        
        cursor.execute('''
            INSERT INTO clientes (id_cliente, nome, segmento, status, data_cadastro)
            VALUES (?, ?, ?, ?, ?)
        ''', (i, nome, segmento, status, data_cadastro.strftime('%Y-%m-%d')))
        clientes.append({'id': i, 'status': status, 'nome': nome})

    # Create Contracts for each client
    for cliente in clientes:
        plano_nome = random.choice(list(planos.keys()))
        valor = plans_val = planos[plano_nome]
        
        # If client is active, contract is likely active. If inactive, contract ended.
        if cliente['status'] == 'Ativo':
            contrato_status = 'Ativo'
            # Start date 1-12 months ago
            dt_inicio = datetime.now() - timedelta(days=random.randint(30, 365))
            # End date in future (unless about to churn/expire)
            # Create some expiring soon scenarios
            if random.random() < 0.3: # 30% chance of expiring soon (< 30 days)
                dt_fim = datetime.now() + timedelta(days=random.randint(1, 29))
            else:
                dt_fim = datetime.now() + timedelta(days=random.randint(60, 365))
        else:
            contrato_status = 'Encerrado'
            dt_inicio = datetime.now() - timedelta(days=random.randint(400, 700))
            dt_fim = dt_inicio + timedelta(days=365)

        cursor.execute('''
            INSERT INTO contratos (id_cliente, plano, valor_mensal, data_inicio, data_fim, status)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (cliente['id'], plano_nome, valor, dt_inicio.strftime('%Y-%m-%d'), dt_fim.strftime('%Y-%m-%d'), contrato_status))

    # Create Interactions
    # Risk scenario: Active client, contract expiring soon, NO interaction recently.
    tipos_interacao = ['Suporte', 'Vendas', 'Financeiro']
    descricoes = [
        'Dúvida sobre fatura', 'Solicitação de upgrade', 'Problema no login',
        'Reunião trimestral', 'Reclamação de lentidão', 'Elogio ao atendimento',
        'Dúvida contratual', 'Pedido de cancelamento', 'Treinamento de equipe'
    ]

    for cliente in clientes:
        num_interacoes = random.randint(0, 5)
        
        # Manually force some Churn Risk scenarios
        # If client is 'EpsilonFood' (hardcoded for demo), make them High Risk: 
        # Active, Expiring soon (handled in contract loop probabilistic), ZERO recent interactions.
        if cliente['nome'] == 'EpsilonFood':
            # Let's verify contract logic later or force it here if needed. 
            # Actually, simpler: Just give them OLD interactions only.
            num_interacoes = 2
            last_date_base = datetime.now() - timedelta(days=90) # No interaction in last 90 days
        else:
            last_date_base = datetime.now()

        for _ in range(num_interacoes):
            tipo = random.choice(tipos_interacao)
            desc = random.choice(descricoes)
            
            if cliente['nome'] == 'EpsilonFood':
                dt_interacao = last_date_base - timedelta(days=random.randint(1, 100))
            else:
                 # Random distribution over last year
                dt_interacao = datetime.now() - timedelta(days=random.randint(1, 300))

            cursor.execute('''
                INSERT INTO interacoes (id_cliente, tipo, descricao, data)
                VALUES (?, ?, ?, ?)
            ''', (cliente['id'], tipo, desc, dt_interacao.strftime('%Y-%m-%d %H:%M:%S')))

    conn.commit()
    print("Database 'clientatech.db' setup successfully!")
    print(f"Created {len(clientes)} clients and related data.")
    conn.close()

if __name__ == "__main__":
    setup_database()
