# Bot Discord vers GitHub Issues

Bot permettant d'écrire des issues sur Discord et de les envoyer automatiquement sur votre projet GitHub.

## Configuration

Créer un fichier `.env` à la racine du projet avec les variables suivantes :

```
DISCORD_TOKEN=
GITHUB_TOKEN=
GITHUB_OWNER=
GITHUB_REPO=
ISSUES_CHANNEL_ID=
STAFF_ROLE=
```

### Exemple de configuration

```
DISCORD_TOKEN=dfdffdfdfdfdfdg
GITHUB_TOKEN=github_pat_dfgdfgdfgfdg
GITHUB_OWNER=user72
GITHUB_REPO=projet99
ISSUES_CHANNEL_ID=11223414
STAFF_ROLE=Team
PROJECT_ID=(indiquer ici l'id du kanban )
PROJECT_FIELD_STATUS=Status
PROJECT_STATUS_TODO=No Status

## Token GitHub

Le `GITHUB_TOKEN` nécessite les permissions suivantes :

### Repository permissions
- Contents
- Issue  
- Workflows

## Installation

Installer les dépendances :

```bash
pip install -r requirements.txt
```

## Utilisation

Lancer le bot :

```bash
python3 bot.py
```