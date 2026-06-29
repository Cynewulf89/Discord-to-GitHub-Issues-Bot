# Utiliser une image Python  
FROM python:3.14-slim

# Définir le répertoire de travail   
WORKDIR /app 

# Copier les fichiers   
COPY . /app
COPY requirements.txt ./

# Installer les dépendances 
RUN apt-get update && \ 
    apt-get install -y --no-install-recommends && \ 
    pip install --no-cache-dir -r requirements.txt && \ 
    apt-get remove -y gcc libc-dev && \ 
    rm requirements.txt && \ 
    apt-get clean && \ 
    apt-get autoremove -y && \ 
    rm -rf /var/lib/apt/lists/* 

# Lancer l’application  
CMD ["python", "src/bot_discord_to_github_issues_bot/main.py"]
