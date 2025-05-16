# forge_cog.py

import discord
from discord import app_commands
from discord.ext import commands
import os
import time
import json
import asyncio
from skyblock import get_uuid, format_uuid, get_player_profiles, find_profile_by_name
# math is no longer strictly needed for the new calculation but doesn't hurt
# import math

# Helper function to format time difference
def format_time_difference(milliseconds):
    """
    Formats a time difference in milliseconds into a human-readable string.
    Ignores seconds if the duration is 1 hour (3,600,000 ms) or more.
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

    # Check if total time is 1 hour or more (in milliseconds)
    if milliseconds >= 3_600_000:
        if hours > 0:
             parts.append(f"{hours}h")
        # Include minutes if there are hours or days, or if less than an hour
        if minutes > 0 or (hours == 0 and days == 0 and minutes == 0): # Add minutes if >= 1 hour or less than a minute
             parts.append(f"{minutes}m")
    else: # Time is less than 1 hour, include minutes and seconds
        if hours > 0: # Should not happen if milliseconds < 3.6M, but keep for safety
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        # Only show seconds if less than 1 hour
        if seconds > 0: # Removed 'or not parts' as minutes/hours handle the less than a minute case
             parts.append(f"{seconds}s")


    if not parts and milliseconds > 0: # Handle cases less than a second but > 0ms, or exactly 0s
         return "Fertig" # Or return something like "<1s" if preferred

    return " ".join(parts)

# Function to calculate Quick Forge time reduction percentage based on tiers
def calculate_quick_forge_reduction(forge_time_level):
    """
    Calculates the Quick Forge time reduction percentage based on the tier level.
    Uses the provided tier percentages.
    Returns the percentage (e.g., 25.5 for 25.5% reduction).
    """
    if forge_time_level is None or forge_time_level < 1:
        return 0.0 # No reduction if level is invalid or not found/less than tier 1

    # Define the percentages based on tiers
    # Assuming level 1 is the first tier, level 2 the second, etc.
    # This list should have the percentage for tier 1, tier 2, ..., tier 19
    tier_percentages_up_to_19 = [
        10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0,
        15.5, 16.0, 16.5, 17.0, 17.5, 18.0, 18.5, 19.0, 19.5
    ]
    max_reduction = 30.0

    level = int(forge_time_level) # Ensure level is an integer

    if level >= 20:
        return max_reduction
    elif level >= 1 and level <= len(tier_percentages_up_to_19):
        # Index is level - 1 because lists are 0-indexed
        return tier_percentages_up_to_19[level - 1]
    else:
        # This case should ideally not happen if levels are 1+, but handle it
        return 0.0 # Unknown level


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
        Includes remaining time calculation with Quick Forge perk if applicable.
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

        # 4. Get Quick Forge Perk Level and calculate reduction
        member_data = target_profile.get("members", {}).get(uuid, {})
        # Accessing 'forge_time' level from the correct path
        forge_time_level = member_data.get("mining_core", {}).get("nodes", {}).get("forge_time")

        time_reduction_percent = calculate_quick_forge_reduction(forge_time_level)
        perk_applied_message = ""
        # Only show the message if there is any reduction
        if time_reduction_percent > 0:
             perk_applied_message = f" (Quick Forge: -{time_reduction_percent:.1f}%)" # Format to 1 decimal place

        # 5. Extract Forge Data
        try:
            forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})

            forge_items_output = []
            current_time_ms = time.time() * 1000 # Get current time in milliseconds

            # Iterate through different forge types (forge_1, forge_2 etc.)
            for forge_type_key, slots_data in forge_processes_data.items():
                 # Iterate through slots within each forge type
                 # Sort slots numerically for consistent output order
                 for slot in sorted(slots_data.keys(), key=int):
                    item_data = slots_data[slot] # Get item_data for the sorted slot key
                    item_id = item_data.get("id", "Unbekannter Gegenstand")
                    start_time_ms = item_data.get("startTime")

                    item_name = item_id # Default to ID if name not found
                    remaining_time_str = "Zeit unbekannt" # Default if duration data is missing

                    # Look up item duration and name from forge_items_data
                    forge_item_info = self.forge_items_data.get(item_id)

                    if forge_item_info and start_time_ms is not None:
                        item_name = forge_item_info.get("name", item_id)
                        base_duration_ms = forge_item_info.get("duration")

                        if base_duration_ms is not None:
                            # Apply Quick Forge reduction if > 0
                            if time_reduction_percent > 0:
                                effective_duration_ms = base_duration_ms * (1 - time_reduction_percent / 100)
                            else:
                                effective_duration_ms = base_duration_ms


                            end_time_ms = start_time_ms + effective_duration_ms
                            remaining_time_ms = end_time_ms - current_time_ms
                            remaining_time_str = format_time_difference(remaining_time_ms)
                        else:
                             remaining_time_str = "Dauer unbekannt (JSON)"

                    elif start_time_ms is None:
                         remaining_time_str = "Startzeit unbekannt (API)"

                    # Updated output format
                    forge_items_output.append(f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Noch {remaining_time_str}")


            if not forge_items_output:
                 await interaction.followup.send(f"Keine aktiven Gegenstände in der Forge auf Profil '{profile_cute_name}' von '{username}'.")
                 return


            response_message = f"Aktuelle Items in der Forge auf Profil '{profile_cute_name}' von '{username}'{perk_applied_message}:\n"
            response_message += "\n".join(forge_items_output)

            await interaction.followup.send(response_message)

        except Exception as e:
            print(f"Error processing forge data for {username}: {e}")
            await interaction.followup.send(f"Ein interner Fehler ist beim Abrufen oder Verarbeiten der Forge-Daten aufgetreten.")


async def setup(bot: commands.Bot):
    """Adds the ForgeCog to the bot."""
    await bot.add_cog(ForgeCog(bot))