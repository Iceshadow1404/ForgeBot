# forge_cog.py

import discord
from discord import app_commands
from discord.ext import commands
import os
import time
import json
import asyncio

# Import functions from skyblock.py
# Ensure skyblock.py is accessible (e.g., in the same directory or a configured Python path)
from skyblock import get_uuid, format_uuid, get_player_profiles, find_profile_by_name

# Helper function to format time difference
def format_time_difference(milliseconds):
    """
    Formats a time difference in milliseconds into a human-readable string.
    Ignores seconds if the duration is 1 hour or more.
    """
    if milliseconds <= 0:
        return "Fertig"

    seconds = milliseconds // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)

    parts = []
    if days > 0:
        parts.append(f"{days}t")

    if hours > 0:
        parts.append(f"{hours}h")

    # Include minutes if there are any hours or days, or if less than an hour
    if minutes > 0 or (hours == 0 and days == 0):
         parts.append(f"{minutes}m")

    # Include seconds only if the total time is less than 1 hour
    if hours == 0 and days == 0 and seconds > 0:
        parts.append(f"{seconds}s")
    # If total time is less than a minute but > 0, still show seconds
    elif not parts and seconds > 0:
         parts.append(f"{seconds}s")

    if not parts: # Handle cases where time is exactly 0 seconds or less
         return "Fertig"


    return " ".join(parts)


class ForgeCog(commands.Cog, name="Forge Funktionen"):
    """
    Dieser Cog bündelt alle Forge-spezifischen Befehle und Funktionen.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.hypixel_api_key = os.getenv("HYPIXEL_API_KEY")
        if not self.hypixel_api_key:
            print("WARNING: HYPIXEL_API_KEY not found in environment variables.")
            print("Forge API commands will not work.")

        # Load forge item durations from JSON
        self.forge_items_data = {}
        try:
            with open('forge_items.json', 'r') as f:
                self.forge_items_data = json.load(f)
            print("forge_items.json successfully loaded.")
        except FileNotFoundError:
            print("WARNING: forge_items.json not found. Forge duration calculation will not work.")
        except json.JSONDecodeError:
            print("ERROR: Could not decode forge_items.json. Check the file for syntax errors.")
        except Exception as e:
            print(f"An unexpected error occurred loading forge_items.json: {e}")


    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.__class__.__name__} Cog wurde geladen und ist bereit.")


    @app_commands.command(name="forge", description="Zeigt die aktuellen Items in der Skyblock Forge eines Spielers an.")
    @app_commands.describe(username="Der Minecraft-Name des Spielers.")
    @app_commands.describe(profile_name="Optional: Der Name des Skyblock-Profils (z.B. 'Apple'). Wenn nicht angegeben, wird das zuletzt gespielte Profil verwendet.")
    async def forge_command(self, interaction: discord.Interaction, username: str, profile_name: str = None):
        """
        Fetches and displays the items currently in the player's Skyblock forge.
        Includes remaining time calculation if forge_items.json is available.
        """
        if not self.hypixel_api_key:
            await interaction.response.send_message(
                "Der API-Schlüssel für Hypixel ist nicht konfiguriert. Bitte informiere den Bot-Betreiber.",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        # 1. Get the player's UUID
        uuid = get_uuid(username)
        if not uuid:
            await interaction.followup.send(f"Konnte den Minecraft-Spieler '{username}' nicht finden.")
            return

        uuid_dashed = format_uuid(uuid)

        # 2. Get Skyblock profiles
        profiles_data = get_player_profiles(self.hypixel_api_key, uuid_dashed)

        if not profiles_data or not profiles_data.get("success", False):
            await interaction.followup.send(f"Fehler beim Abrufen der Skyblock-Profile für '{username}'.")
            return

        profiles = profiles_data.get("profiles", [])
        if not profiles:
            await interaction.followup.send(f"Keine Skyblock-Profile für '{username}' gefunden.")
            return

        # 3. Find the target profile
        target_profile = None
        if profile_name:
            target_profile = find_profile_by_name(profiles_data, profile_name)
            if not target_profile:
                await interaction.followup.send(f"Profil '{profile_name}' für '{username}' nicht gefunden.")
                return
        else:
            for profile in profiles:
                 if profile.get("selected", False):
                     target_profile = profile
                     break
            if not target_profile and profiles:
                 target_profile = max(profiles, key=lambda p: p.get("members", {}).get(uuid, {}).get("last_save", 0))

            if not target_profile:
                 await interaction.followup.send(f"Konnte das zuletzt gespielte Profil für '{username}' nicht bestimmen.")
                 return

        profile_id = target_profile.get("profile_id")
        profile_cute_name = target_profile.get("cute_name", "Unbekanntes Profil")

        # 4. Extract Forge Data
        try:
            member_data = target_profile.get("members", {}).get(uuid, {})
            # Adjusting to correctly access forge processes under 'forge_1', 'forge_2', etc.
            # The structure implies there could be multiple forge types (forge_1, forge_2 etc.)
            # Let's iterate through all of them found under 'forge_processes'
            forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})

            forge_items_output = []
            current_time_ms = time.time() * 1000 # Get current time in milliseconds

            # Iterate through different forge types (forge_1, forge_2 etc.)
            for forge_type_key, slots_data in forge_processes_data.items():
                 # Iterate through slots within each forge type
                 for slot, item_data in slots_data.items():
                    item_id = item_data.get("id", "Unbekannter Gegenstand")
                    # item_type = item_data.get("type", "Unbekannter Typ") # Removed as requested
                    start_time_ms = item_data.get("startTime")

                    item_name = item_id # Default to ID if name not found
                    remaining_time_str = "Zeit unbekannt" # Default if duration data is missing

                    # Look up item duration and name from forge_items_data
                    forge_item_info = self.forge_items_data.get(item_id)

                    if forge_item_info and start_time_ms is not None:
                        item_name = forge_item_info.get("name", item_id)
                        duration_ms = forge_item_info.get("duration")

                        if duration_ms is not None:
                            end_time_ms = start_time_ms + duration_ms
                            remaining_time_ms = end_time_ms - current_time_ms
                            remaining_time_str = format_time_difference(remaining_time_ms)
                        else:
                             remaining_time_str = "Dauer unbekannt (JSON)" # Duration not found in JSON

                    elif start_time_ms is None:
                         remaining_time_str = "Startzeit unbekannt (API)" # Start time missing from API

                    # Changed output format to remove item type
                    forge_items_output.append(f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Noch {remaining_time_str}")


            if not forge_items_output:
                 await interaction.followup.send(f"Keine aktiven Gegenstände in der Forge auf Profil '{profile_cute_name}' von '{username}'.")
                 return


            response_message = f"Aktuelle Items in der Forge auf Profil '{profile_cute_name}' von '{username}':\n"
            response_message += "\n".join(forge_items_output)

            await interaction.followup.send(response_message)

        except Exception as e:
            print(f"Error processing forge data for {username}: {e}")
            await interaction.followup.send(f"Ein interner Fehler ist beim Abrufen oder Verarbeiten der Forge-Daten aufgetreten.")


async def setup(bot: commands.Bot):
    """Adds the ForgeCog to the bot."""
    await bot.add_cog(ForgeCog(bot))