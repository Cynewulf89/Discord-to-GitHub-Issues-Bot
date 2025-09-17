import discord
from discord.ext import commands
import requests  # type: ignore
import aiohttp
import asyncio
import base64
from datetime import datetime
import os
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

# Tokens depuis variables d'environnement
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN') or ''
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN') or ''
GITHUB_OWNER = os.getenv('GITHUB_OWNER') or ''
GITHUB_REPO = os.getenv('GITHUB_REPO') or ''
ISSUES_CHANNEL_ID = int(os.getenv('ISSUES_CHANNEL_ID', '0'))
STAFF_ROLE = os.getenv('STAFF_ROLE', 'Staff')
BETA_TESTER_ROLE = os.getenv('BETA_TESTER_ROLE', 'BetaTester')

# Configuration du kanban GitHub Projects
PROJECT_ID = os.getenv('PROJECT_ID', '')
PROJECT_FIELD_STATUS = os.getenv('PROJECT_FIELD_STATUS', 'Status')
PROJECT_STATUS_TODO = os.getenv('PROJECT_STATUS_TODO', 'Backlog')

# Bot configuration
try:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix='!', intents=intents)
except AttributeError:
    # Fallback for older discord.py versions
    bot = commands.Bot(command_prefix='!')

# Temporary storage for pending issues and channel messages
pending_issues: Dict[str, Dict[str, Any]] = {}
channel_messages: Dict[str, discord.Message] = {}  # Store channel messages by issue_id

class GitHubAPI:
    """Handles GitHub API interactions"""
    
    def __init__(self, token: str, owner: str, repo: str):
        self.token = token
        self.owner = owner
        self.repo = repo
        self.headers = {
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3+json'
        }
        # Headers spécifiques pour l'API GraphQL (Projects v2)
        self.graphql_headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
    
    async def upload_image_to_github(self, image_data: bytes, filename: str) -> Optional[str]:
        """Upload an image to GitHub and return the URL"""
        try:
            # Create unique filename
            timestamp = int(datetime.now().timestamp())
            safe_filename = f"{timestamp}_{filename}"
            
            # Upload via GitHub Contents API
            path = f'assets/discord-images/{safe_filename}'
            url = f'https://api.github.com/repos/{self.owner}/{self.repo}/contents/{path}'
            
            data = {
                'message': f'Upload image from Discord: {filename}',
                'content': base64.b64encode(image_data).decode('utf-8'),
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.put(url, json=data, headers=self.headers) as response:
                    if response.status in [200, 201]:
                        result = await response.json()
                        return result['content']['download_url']
                    else:
                        print(f"GitHub upload error: {response.status} - {await response.text()}")
                        return None
                        
        except Exception as e:
            print(f"Image upload error: {e}")
            return None
    
    def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        """Create an issue on GitHub"""
        url = f'https://api.github.com/repos/{self.owner}/{self.repo}/issues'
        data = {
            'title': title,
            'body': body,
            'labels': labels or []
        }
        
        response = requests.post(url, json=data, headers=self.headers)
        response.raise_for_status()
        return response.json()
    
    def get_issues(self, state: str = 'open', per_page: int = 30, page: int = 1) -> List[Dict[str, Any]]:
        """Get repository issues"""
        url = f'https://api.github.com/repos/{self.owner}/{self.repo}/issues'
        params = {
            'state': state,
            'per_page': per_page,
            'page': page,
            'sort': 'updated',
            'direction': 'desc'
        }
        
        response = requests.get(url, params=params, headers=self.headers)
        response.raise_for_status()
        return response.json()
    
    def get_issue(self, issue_number: int) -> Dict[str, Any]:
        """Get a specific issue by number"""
        url = f'https://api.github.com/repos/{self.owner}/{self.repo}/issues/{issue_number}'
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()
    
    async def get_project_info(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Récupère les informations du projet GitHub"""
        query = """
        query($projectId: ID!) {
            node(id: $projectId) {
                ... on ProjectV2 {
                    id
                    title
                    fields(first: 20) {
                        nodes {
                            ... on ProjectV2Field {
                                id
                                name
                            }
                            ... on ProjectV2SingleSelectField {
                                id
                                name
                                options {
                                    id
                                    name
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        
        variables = {"projectId": project_id}
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                'https://api.github.com/graphql',
                json={'query': query, 'variables': variables},
                headers=self.graphql_headers
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('data', {}).get('node')
                else:
                    print(f"Error getting project info: {response.status}")
                    return None

    async def add_issue_to_project(self, project_id: str, issue_id: str, status_field_id: str, status_option_id: str) -> bool:
        """Ajoute une issue au projet kanban"""
        try:
            # 1. D'abord, ajouter l'item au projet
            add_mutation = """
            mutation($projectId: ID!, $contentId: ID!) {
                addProjectV2ItemById(input: {
                    projectId: $projectId
                    contentId: $contentId
                }) {
                    item {
                        id
                    }
                }
            }
            """
            
            variables = {
                "projectId": project_id,
                "contentId": issue_id
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://api.github.com/graphql',
                    json={'query': add_mutation, 'variables': variables},
                    headers=self.graphql_headers
                ) as response:
                    if response.status != 200:
                        print(f"Error adding item to project: {response.status}")
                        return False
                    
                    result = await response.json()
                    if 'errors' in result:
                        print(f"GraphQL errors: {result['errors']}")
                        return False
                    
                    item_id = result.get('data', {}).get('addProjectV2ItemById', {}).get('item', {}).get('id')
                    
                    if not item_id:
                        print("No item ID returned")
                        return False
                    
                    # 2. Ensuite, définir le status
                    update_mutation = """
                    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
                        updateProjectV2ItemFieldValue(input: {
                            projectId: $projectId
                            itemId: $itemId
                            fieldId: $fieldId
                            value: $value
                        }) {
                            projectV2Item {
                                id
                            }
                        }
                    }
                    """
                    
                    update_variables = {
                        "projectId": project_id,
                        "itemId": item_id,
                        "fieldId": status_field_id,
                        "value": {
                            "singleSelectOptionId": status_option_id
                        }
                    }
                    
                    async with session.post(
                        'https://api.github.com/graphql',
                        json={'query': update_mutation, 'variables': update_variables},
                        headers=self.graphql_headers
                    ) as update_response:
                        if update_response.status == 200:
                            update_result = await update_response.json()
                            if 'errors' in update_result:
                                print(f"GraphQL errors in update: {update_result['errors']}")
                                return False
                            return True
                        else:
                            print(f"Error updating item status: {update_response.status}")
                            return False
        except Exception as e:
            print(f"Exception in add_issue_to_project: {e}")
            return False

    def get_issue_node_id(self, issue_number: int) -> Optional[str]:
        """Récupère l'ID GraphQL d'une issue"""
        try:
            issue = self.get_issue(issue_number)
            return issue.get('node_id')
        except Exception as e:
            print(f"Error getting issue node ID: {e}")
            return None

# GitHub API instance
github = GitHubAPI(GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO)

def is_staff_or_beta(member: discord.Member) -> bool:
    """Vérifie si le membre est staff ou beta-testeur"""
    return any(role.name in [STAFF_ROLE, BETA_TESTER_ROLE] for role in member.roles)

async def cleanup_old_issue_messages():
    """Remove old issue messages from the channel"""
    try:
        # Get all current issue_ids
        current_issue_ids = set(pending_issues.keys())
        
        # Find messages to delete (not in current pending issues)
        messages_to_delete = []
        for issue_id, message in list(channel_messages.items()):
            if issue_id not in current_issue_ids:
                messages_to_delete.append((issue_id, message))
        
        # Delete old messages
        for issue_id, message in messages_to_delete:
            try:
                await message.delete()
                del channel_messages[issue_id]
            except discord.errors.NotFound:
                # Message already deleted
                del channel_messages[issue_id]
            except Exception as e:
                print(f"Error deleting message for issue {issue_id}: {e}")
                
    except Exception as e:
        print(f"Error in cleanup_old_issue_messages: {e}")

@bot.event
async def on_ready():
    print(f'Bot {bot.user} is connected!')
    channel = bot.get_channel(ISSUES_CHANNEL_ID)
    if channel:
        print(f'Configured channel: #{channel.name} (ID: {ISSUES_CHANNEL_ID})')
    else:
        print(f'Channel with ID {ISSUES_CHANNEL_ID} not found!')

class IssueModal(discord.ui.Modal):
    """Modal for creating new issues"""
    
    def __init__(self, attachments: Optional[List[discord.Attachment]] = None, interaction_user=None):
        super().__init__(title='Create New Issue')
        self.attachments = attachments or []
        self.interaction_user = interaction_user
        
        # Title field
        self.title_input: discord.ui.TextInput = discord.ui.TextInput(
            label='Issue Title',
            placeholder='e.g., Bug in login button',
            max_length=100,
            required=True
        )
        self.add_item(self.title_input)
        
        # Description field
        self.description_input: discord.ui.TextInput = discord.ui.TextInput(
            label='Detailed Description',
            placeholder='Describe the problem, steps to reproduce, etc.',
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True
        )
        self.add_item(self.description_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            title = self.title_input.value.strip()
            description = self.description_input.value.strip()
            if not title or not description:
                await interaction.send(
                    'Title and description are required.', 
                    ephemeral=True
                )
                return
            # Clean up old messages before creating new one
            await cleanup_old_issue_messages()
            # Create unique ID for this issue
            issue_id = f"{interaction.user.id}_{int(datetime.now().timestamp())}"
            # Process initial attachments (images et vidéos)
            initial_uploaded_files = []
            for attachment in self.attachments:
                if attachment.content_type and (
                    attachment.content_type.startswith('image/') or attachment.content_type.startswith('video/')
                ):
                    try:
                        file_data = await attachment.read()
                        file_url = await github.upload_image_to_github(file_data, attachment.filename)
                        if file_url:
                            initial_uploaded_files.append({
                                'filename': attachment.filename,
                                'url': file_url,
                                'discord_url': attachment.url
                            })
                    except Exception as e:
                        print(f"Error processing initial attachment {attachment.filename}: {e}")
            # Store pending issue
            pending_issues[issue_id] = {
                'title': title,
                'description': description,
                'uploaded_images': initial_uploaded_files,
                'labels': []  # Ajouté pour éviter le bug
            }
            # Send to issues channel
            channel = interaction.client.get_channel(ISSUES_CHANNEL_ID)
            if channel and hasattr(channel, 'send'):
                view = ChannelIssueView(issue_id)
                embed = self._create_issue_embed(issue_id, interaction.user)
                channel_message = await channel.send(embed=embed, view=view)
                channel_messages[issue_id] = channel_message
                # Répondre sans message de confirmation visible
                await interaction.response.defer()
            else:
                await interaction.response.send_message(
                    'Error: Issues channel not found.', 
                    ephemeral=True
                )
        except Exception as e:
            print(f'Error creating issue via modal: {e}')
            await interaction.response.send_message(
                'Error creating issue.', 
                ephemeral=True
            )
    
    def _create_issue_embed(self, issue_id: str, user: discord.User | discord.Member) -> discord.Embed:
        """Create embed for issue display"""
        if issue_id not in pending_issues:
            return discord.Embed(
                title='Error: Issue data not found',
                color=discord.Color.red()
            )

        issue_data = pending_issues[issue_id]
        title = issue_data['title']
        description = issue_data['description']
        
        embed = discord.Embed(
            title='🔍 New Issue',
            color=0xffaa00,
            timestamp=datetime.now()
        )
        embed.add_field(name='Title', value=title, inline=False)
        
        desc_preview = description[:1024]
        embed.add_field(name='Description', value=desc_preview, inline=False)
        embed.add_field(name='Created by', value=user.mention, inline=True)
        
        # Display list of images
        images_info = issue_data.get('uploaded_images', [])
        if images_info:
            image_list_str = ""
            for i, img in enumerate(images_info):
                image_name = img.get('filename', f"Image {i+1}")
                image_url = img.get('discord_url') or img.get('url')
                if image_url:
                    image_list_str += f"• **[{image_name}]({image_url})**\n"
                else:
                    image_list_str += f"• **{image_name}**\n"
            
            embed.add_field(name='Attached Images', value=image_list_str, inline=False)
            
            if images_info[0].get('discord_url') or images_info[0].get('url'):
                embed.set_thumbnail(url=images_info[0].get('discord_url') or images_info[0].get('url'))
        else:
            embed.add_field(name='Attached Images', value='None', inline=False) 
        return embed

class ChannelIssueView(discord.ui.View):
    """View for issues posted in the channel with upload and validate buttons"""
    
    def __init__(self, issue_id: str):
        super().__init__(timeout=None)
        self.issue_id = issue_id
        self.upload_message = None  # Store the upload instruction message

    @discord.ui.button(label='📎 Upload Fichier (Image/Vidéo)', style=discord.ButtonStyle.secondary)
    async def upload_image_file(self, interaction: discord.Interaction, button: discord.ui.Button):

            
        if self.issue_id not in pending_issues:
            await interaction.response.send_message('This issue no longer exists.', ephemeral=True)
            return
        
        # Delete previous upload message if exists
        if self.upload_message:
            try:
                await self.upload_message.delete()
            except Exception as e:
                print(f"Error deleting previous upload message: {e}")
        
        # Send initial message
        await interaction.response.send_message(
            'Veuillez envoyer votre image ou vidéo dans un **nouveau message** sur ce salon.'
        )
        
        # Send the waiting message (ephemeral) and delete the initial message
        upload_message = await interaction.followup.send(  # type: ignore
            'En attente de votre image ou vidéo...', ephemeral=True
        )
        self.upload_message = upload_message  # type: ignore
        
        # Delete the initial message immediately after sending the ephemeral one
        try:
            await interaction.delete_original_response()
        except Exception as e:
            print(f"Error deleting original response: {e}")
        
        # Wait for image or video response
        def check(message):
            return (message.author.id == interaction.user.id and 
                   message.channel.id == interaction.channel_id and
                   message.attachments and 
                   any(att.content_type and (
                       att.content_type.startswith('image/') or att.content_type.startswith('video/')
                   ) for att in message.attachments))
        
        try:
            message = await interaction.client.wait_for('message', check=check, timeout=120)
            
            uploaded_count = 0
            for attachment in message.attachments:
                if attachment.content_type and (
                    attachment.content_type.startswith('image/') or attachment.content_type.startswith('video/')
                ):
                    try:
                        file_data = await attachment.read()
                        file_url = await github.upload_image_to_github(file_data, attachment.filename)
                        
                        if file_url:
                            pending_issues[self.issue_id]['uploaded_images'].append({
                                'filename': attachment.filename,
                                'url': file_url,
                                'discord_url': attachment.url
                            })
                            uploaded_count += 1
                    except Exception as e:
                        print(f'Error uploading {attachment.filename}: {e}')
            
            if uploaded_count > 0:
                await message.add_reaction('✅')
                # Update the embed
                original_message = channel_messages.get(self.issue_id)
                if original_message:
                    new_embed = IssueModal(attachments=[], interaction_user=interaction.user)._create_issue_embed(self.issue_id, interaction.user)
                    await original_message.edit(embed=new_embed)
                
                # Delete the upload instruction message first
                if self.upload_message:
                    try:
                        await self.upload_message.delete()
                        self.upload_message = None
                        print("Upload instruction message deleted successfully")
                    except Exception as e:
                        print(f"Error deleting upload instruction message: {e}")
                
                # Then delete the message with uploaded images to clean the channel
                try:
                    await asyncio.sleep(2)  # Attendre 2 secondes pour être sûr
                    await message.delete()
                    print(f"Image message deleted successfully (ID: {message.id})")
                except discord.errors.NotFound:
                    print("Message already deleted")
                except discord.errors.Forbidden:
                    print(f"Bot doesn't have permission to delete messages in channel {message.channel.name}")
                    # Essayer de supprimer après un délai plus long
                    try:
                        await asyncio.sleep(5)
                        await message.delete()
                        print("Image message deleted after retry")
                    except Exception as retry_e:
                        print(f"Retry failed: {retry_e}")
                except Exception as e:
                    print(f"Error deleting image message: {e}")
            else:
                await message.add_reaction('❌')
                # Delete upload instruction message on failure
                if self.upload_message:
                    try:
                        await self.upload_message.delete()
                        self.upload_message = None
                    except Exception as e:
                        print(f"Error deleting upload instruction message on failure: {e}")
            
        except asyncio.TimeoutError:
            # Delete the upload instruction message on timeout
            if self.upload_message:
                try:
                    await self.upload_message.delete()
                    self.upload_message = None
                except Exception as e:
                    print(f"Error deleting upload instruction message on timeout: {e}")

    @discord.ui.button(label='✅ Validate Issue', style=discord.ButtonStyle.success)
    async def validate_issue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff_or_beta(interaction.user):
            await interaction.response.send_message('Seuls les membres du staff ou les beta-testeurs peuvent valider les issues.', ephemeral=True)
            return
        
        if self.issue_id not in pending_issues:
            await interaction.response.send_message('This issue no longer exists.', ephemeral=True)
            return
        
        # Créer une vue pour choisir entre validation simple ou avec kanban
        if PROJECT_ID:
            view = ValidationChoiceView(self.issue_id, interaction)
            await interaction.response.send_message(
                "Validation de l'issue :", 
                view=view, 
                ephemeral=True
            )
        else:
            # Pas de kanban configuré, validation simple uniquement
            try:
                await self._create_github_issue(self.issue_id, interaction)
            except Exception as e:
                print(f'Error validating issue: {e}')
                await interaction.response.send_message('Error creating GitHub issue.', ephemeral=True)

    @discord.ui.button(label='❌ Reject', style=discord.ButtonStyle.danger)
    async def reject_issue(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff_or_beta(interaction.user):
            await interaction.response.send_message('Seuls les membres du staff ou les beta-testeurs peuvent rejeter les issues.', ephemeral=True)
            return
        
        if self.issue_id not in pending_issues:
            await interaction.response.send_message('This issue no longer exists.', ephemeral=True)
            return
        
        # Remove from pending issues
        del pending_issues[self.issue_id]
        
        # Delete the upload instruction message if exists
        if self.upload_message:
            try:
                await self.upload_message.delete()
            except Exception as e:
                print(f"Error deleting upload instruction message: {e}")
        
        # Edit the message to show rejection
        embed = discord.Embed(
            title='❌ Issue Rejected',
            color=0xff0000,
            timestamp=datetime.now()
        )
        embed.add_field(name='Status', value=f'Rejected by {interaction.user.mention}', inline=False)
        
        await interaction.response.edit_message(embed=embed, view=None)
        
        # Remove from channel_messages
        if self.issue_id in channel_messages:
            del channel_messages[self.issue_id]
    
    async def _create_github_issue(self, issue_id: str, interaction: discord.Interaction):
        """Create the GitHub issue"""
        if issue_id not in pending_issues:
            return
        
        issue_data = pending_issues[issue_id]
        
        try:
            # Build issue body with all images
            body = f"""**Description:**
{issue_data['description']}

"""
            
            # Add all images
            if issue_data.get('uploaded_images'):
                body += "**Attached Images:**\n\n"
                for img in issue_data['uploaded_images']:
                    image_url_for_github = img.get('url') 
                    
                    if image_url_for_github:
                        body += f"![{img.get('filename', 'Attached Image')}]({image_url_for_github})\n"
                        if img.get('description'):
                            body += f"*{img['description']}*\n\n"
                        else:
                            body += "\n"
                body += "---\n"
            
            # Create GitHub issue
            github_issue = github.create_issue(
                issue_data['title'], 
                body, 
                issue_data['labels']
            )
            
            # Delete the upload instruction message if exists
            if self.upload_message:
                try:
                    await self.upload_message.delete()
                except Exception as e:
                    print(f"Error deleting upload instruction message: {e}")
            
            # Edit the message to show success
            embed = discord.Embed(
                title='✅ New Issue Created',
                color=0x00ff00,
                timestamp=datetime.now()
            )
            embed.add_field(name='Title', value=issue_data['title'], inline=False)
            embed.add_field(name='GitHub Issue', value=f"[#{github_issue['number']} - View on GitHub]({github_issue['html_url']})", inline=False)
            embed.add_field(name='Validated by', value=interaction.user.mention, inline=True)
            
            await interaction.response.edit_message(embed=embed, view=None)
            
            # Remove from pending list and channel_messages
            del pending_issues[issue_id]
            if issue_id in channel_messages:
                del channel_messages[issue_id]
            
        except Exception as e:
            print(f'Error creating GitHub issue: {e}')
            await interaction.response.send_message('Error creating GitHub issue.', ephemeral=True)

class ValidationChoiceView(discord.ui.View):
    """Vue pour choisir le type de validation"""
    
    def __init__(self, issue_id: str, original_interaction: discord.Interaction):
        super().__init__(timeout=60)
        self.issue_id = issue_id
        self.original_interaction = original_interaction

    @discord.ui.button(label='Valider et envoyer au Kanban', style=discord.ButtonStyle.primary)
    async def validate_with_kanban(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        try:
            # Récupérer les infos du projet pour les colonnes
            project_info = await github.get_project_info(PROJECT_ID)
            
            if not project_info:
                await interaction.followup.send("Erreur: Impossible de récupérer les informations du projet kanban", ephemeral=True)
                return
            
            # Trouver le champ Status et ses options
            status_field = None
            for field in project_info.get('fields', {}).get('nodes', []):
                if field.get('name') == PROJECT_FIELD_STATUS:
                    status_field = field
                    break
            
            if not status_field or not status_field.get('options'):
                await interaction.followup.send("Erreur: Champ Status non trouvé dans le projet", ephemeral=True)
                return
            
            # Supprimer le message de choix
            try:
                await interaction.delete_original_response()
            except Exception as e:
                print(f"Error deleting choice message: {e}")
            
            # Créer la vue de sélection du kanban
            kanban_view = KanbanSelectView(self.issue_id, status_field, self.original_interaction)
            await interaction.followup.send("Choisissez ou envoyez l'issue :", view=kanban_view, ephemeral=True)

        except Exception as e:
            print(f'Erreur validation kanban: {e}')
            await interaction.followup.send("Erreur lors de la préparation du kanban", ephemeral=True)

    @discord.ui.button(label='Valider seulement', style=discord.ButtonStyle.secondary)
    async def validate_only(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        try:
            # Supprimer le message de choix
            try:
                await interaction.delete_original_response()
            except Exception as e:
                print(f"Error deleting choice message: {e}")
                
            github_issue = await self._create_github_issue()
            if github_issue:
                await self._update_success_message(github_issue, False)
            else:
                await interaction.followup.send("Erreur lors de la création de l'issue", ephemeral=True)
        except Exception as e:
            print(f'Erreur validation simple: {e}')
            await interaction.followup.send("Erreur lors de la validation", ephemeral=True)

    async def _create_github_issue(self) -> Optional[Dict[str, Any]]:
        """Crée l'issue GitHub"""
        if self.issue_id not in pending_issues:
            return None
        
        issue_data = pending_issues[self.issue_id]
        
        # Construire le body avec images
        body = f"**Description:**\n{issue_data['description']}\n\n"
        
        if issue_data.get('uploaded_images'):
            body += "**Images attachées:**\n\n"
            for img in issue_data['uploaded_images']:
                image_url = img.get('url')
                if image_url:
                    body += f"![{img.get('filename', 'Image')}]({image_url})\n"
            body += "---\n"
        
        # Créer l'issue
        return github.create_issue(
            issue_data['title'], 
            body, 
            issue_data.get('labels', [])
        )

    async def _update_success_message(self, github_issue: Dict[str, Any], added_to_kanban: bool, kanban_column: str = "", error_msg: str = ""):
        """Met à jour le message d'origine avec le succès"""
        embed = discord.Embed(
            title='✅ Issue créée avec succès',
            color=0x00ff00,
            timestamp=datetime.now()
        )
        
        embed.add_field(
            name='Issue GitHub', 
            value=f"[#{github_issue['number']} - {github_issue['title']}]({github_issue['html_url']})", 
            inline=False
        )
        
        if added_to_kanban and kanban_column:
            embed.add_field(name='Kanban', value=f'✅ Ajoutée dans la colonne **{kanban_column}**', inline=False)
        elif error_msg:
            embed.add_field(name='Kanban', value=f'❌ {error_msg}', inline=False)
        
        embed.add_field(name='Validée par', value=self.original_interaction.user.mention, inline=True)
        
        # Récupérer le message original
        original_message = channel_messages.get(self.issue_id)
        if original_message:
            await original_message.edit(embed=embed, view=None)
        
        # Nettoyer
        if self.issue_id in pending_issues:
            del pending_issues[self.issue_id]
        if self.issue_id in channel_messages:
            del channel_messages[self.issue_id]

class KanbanSelectView(discord.ui.View):
    """Vue pour sélectionner la colonne du kanban"""
    
    def __init__(self, issue_id: str, status_field: Dict[str, Any], original_interaction: discord.Interaction):
        super().__init__(timeout=60)
        self.issue_id = issue_id
        self.status_field = status_field
        self.original_interaction = original_interaction
        
        # Créer le select menu avec les options
        options = []
        for option in status_field.get('options', []):
            options.append(discord.SelectOption(
                label=option['name'],
                value=option['id'],
                description=f"Envoyer l'issue dans {option['name']}"
            ))
        
        select = discord.ui.Select(
            placeholder="Choix de colonne...",
            options=options,
            custom_id="kanban_column_select"
        )
        select.callback = self.column_selected
        self.add_item(select)
    

    
    async def column_selected(self, interaction: discord.Interaction):
        """Callback quand une colonne est sélectionnée"""
        await interaction.response.defer()
        
        try:
            selected_option_id = interaction.data['values'][0]
            selected_column_name = None
            
            # Trouver le nom de la colonne sélectionnée
            for option in self.status_field.get('options', []):
                if option['id'] == selected_option_id:
                    selected_column_name = option['name']
                    break
            
            if not selected_column_name:
                await interaction.followup.send("Erreur: Colonne non trouvée", ephemeral=True)
                return
            
            # Créer l'issue GitHub
            validation_view = ValidationChoiceView(self.issue_id, self.original_interaction)
            github_issue = await validation_view._create_github_issue()
            
            if not github_issue:
                await interaction.followup.send("Erreur lors de la création de l'issue GitHub", ephemeral=True)
                return
            
            # Récupérer l'ID GraphQL de l'issue
            issue_node_id = github.get_issue_node_id(github_issue['number'])
            
            if not issue_node_id:
                await validation_view._update_success_message(
                    github_issue, False, "", 
                    "Issue créée mais impossible de récupérer l'ID pour le kanban"
                )
                await interaction.followup.send("Issue créée mais erreur lors de l'ajout au kanban", ephemeral=True)
                return
            
            # Ajouter au kanban
            success = await github.add_issue_to_project(
                PROJECT_ID, 
                issue_node_id, 
                self.status_field['id'], 
                selected_option_id
            )
            
            if success:
                await validation_view._update_success_message(github_issue, True, selected_column_name)
                # Supprimer le message de sélection de colonne
                try:
                    await interaction.delete_original_response()
                except Exception as e:
                    print(f"Error deleting column selection message: {e}")
                # Pas de message de confirmation supplémentaire - tout est dans l'embed principal
            else:
                await validation_view._update_success_message(
                    github_issue, False, "", 
                    "Issue créée mais erreur lors de l'ajout au kanban"
                )
                # Supprimer le message de sélection même en cas d'erreur
                try:
                    await interaction.delete_original_response()
                except Exception as e:
                    print(f"Error deleting column selection message on error: {e}")
                await interaction.followup.send("Issue créée mais erreur lors de l'ajout au kanban", ephemeral=True, delete_after=5)
                
        except Exception as e:
            print(f'Erreur sélection colonne: {e}')
            await interaction.followup.send("Erreur lors de l'ajout au kanban", ephemeral=True)

class IssueFormView(discord.ui.View):
    """View to open the issue creation form"""
    
    def __init__(self, attachments: Optional[List[discord.Attachment]] = None, ctx=None):
        super().__init__(timeout=300)
        self.attachments = attachments or []
        self.ctx = ctx
        self.message_to_delete = None
        # Stocke l'auteur du message pour la vérification
        self.author_id = ctx.author.id if ctx and ctx.author else None

    @discord.ui.button(label='Create Issue', style=discord.ButtonStyle.primary)
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Vérifie que seul l'auteur peut cliquer
        if self.author_id and interaction.user.id != self.author_id:
            await interaction.response.send_message("Seul l'auteur du message peut créer l'issue.", ephemeral=True)
            return
        # Delete the "click button" message
        if self.message_to_delete:
            try:
                await self.message_to_delete.delete()
            except Exception as e:
                print(f"Error deleting button message: {e}")
        modal = IssueModal(self.attachments, interaction.user)
        await interaction.response.send_modal(modal)

@bot.event
async def on_message(message):
    """Handle incoming messages"""
    if message.author == bot.user:
        return
    
    await bot.process_commands(message)

@bot.command(name='new-issue', aliases=['newissue'])
async def create_issue(ctx):
    """Create a new issue with a form
    Usage: !new-issue
    Add image attachments to your message to include them
    """
    try:
        # Delete the command message
        try:
            await ctx.message.delete()
        except Exception as e:
            print(f"Error deleting command message: {e}")
        
        attachments = ctx.message.attachments
        
        embed = discord.Embed(
            description='Click the button below to open the issue creation form.',
            color=0x0099ff
        )
        
        view = IssueFormView(attachments, ctx)
        message = await ctx.send(embed=embed, view=view)
        
        # Store the message reference for later deletion
        view.message_to_delete = message
        
    except Exception as e:
        print(f'Error creating issue: {e}')
        await ctx.send('Error opening issue creation form.', delete_after=5)

@bot.command(name='issues')  # type: ignore
async def list_github_issues(ctx, state: str = 'open', page: int = 1):
    """List GitHub repository issues
    Usage: !issues [open/closed/all] [page]
    Examples: !issues, !issues closed, !issues open 2
    """
    try:
        if state not in ['open', 'closed', 'all']:
            state = 'open'
        
        if page < 1:
            page = 1
        
        issues_data = github.get_issues(state=state, per_page=10, page=page)
        
        if not issues_data:
            await ctx.reply(f'No {state} issues found.')
            return
        
        colors = {'open': 0x28a745, 'closed': 0x6f42c1, 'all': 0x0366d6}
        
        embed = discord.Embed(
            title=f'{state.title()} Issues',
            color=colors[state],
            timestamp=datetime.now()
        )
        
        for issue in issues_data:
            status_emoji = '🟢' if issue['state'] == 'open' else '🔴'
            
            # Labels
            labels_text = ''
            if issue.get('labels'):
                labels = [f"`{label['name']}`" for label in issue['labels'][:3]]
                labels_text = ' ' + ' '.join(labels)
                if len(issue['labels']) > 3:
                    labels_text += f' +{len(issue["labels"]) - 3}'
            
            title = issue['title']
            if len(title) > 60:
                title = title[:57] + '...'
            
            field_name = f'{status_emoji} #{issue["number"]} {title}'
            field_value = f'[View on GitHub]({issue["html_url"]}){labels_text}'
            
            embed.add_field(
                name=field_name,
                value=field_value,
                inline=False
            )
        
        nav_text = f'Page {page}'
        if len(issues_data) == 10:
            nav_text += f' | `!issues {state} {page + 1}` for next page'
        
        embed.set_footer(text=nav_text)
        await ctx.reply(embed=embed)
        
    except requests.exceptions.RequestException as e:
        print(f'GitHub API error: {e}')
        await ctx.reply('Error fetching issues from GitHub.')
    except Exception as e:
        print(f'Error listing issues: {e}')
        await ctx.reply('An error occurred while fetching issues.')


# Run the bot
bot.run(DISCORD_TOKEN)