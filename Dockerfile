FROM python:3.9-slim

WORKDIR /app

# Instalar dependências de sistema
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 1. Copia APENAS o requirements primeiro (para usar o cache do Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. Copia o restante do código DEPOIS
COPY . .

EXPOSE 8502

# Variáveis de ambiente
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_SERVER_PORT=8502
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0

# O comando final (verifique se o caminho src/app_ui.py bate com sua pasta local devido ao volume)
CMD ["streamlit", "run", "src/app_ui.py", "--server.address=0.0.0.0", "--server.enableCORS=false", "--server.enableXsrfProtection=false"]