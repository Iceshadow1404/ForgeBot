# forge_cog.py

import discord
from discord import app_commands
from discord.ext import commands
import os
import time
import json
import asyncio

# Import necessary functions from skyblock.py
from skyblock import get_uuid, format_uuid, get_player_profiles, find_profile_by_name

# Define the path to the registration data file (used for reading)
REGISTRATION_FILE = 'registrations.json'

# Helper function to format time difference
def format_time_difference(milliseconds):
    """
    Formats a time difference in milliseconds into a human-readable string.
    Ignores seconds if the duration is 1 hour (3,600,000 ms) or more.
    """
    if milliseconds <= 0:
        return "Finished"

    seconds = milliseconds // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)

    parts = []
    if days > 0:
        parts.append(f"{days}d")

    # Check if total time is 1 hour or more (in milliseconds)
    if milliseconds >= 3_600_000:
        if hours > 0:
             parts.append(f"{hours}h")
        if minutes > 0: # Include minutes if there are hours or days
             parts.append(f"{minutes}m")
        # Seconds are ignored if time is 1 hour or more
    else: # Time is less than 1 hour, include minutes and seconds
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        # Only show seconds if less than 1 hour
        if seconds > 0 or not parts: # Include seconds if > 0 or if duration was <1 minute
             parts.append(f"{seconds}s")

    if not parts and milliseconds > 0:
         return "<1s"
    elif not parts and milliseconds <= 0:
        return "Finished"

    return " ".join(parts)

# Function to calculate Quick Forge time reduction percentage based on tiers
def calculate_quick_forge_reduction(forge_time_level):
    """
    Calculates the Quick Forge time reduction percentage based on the tier level.
    Uses the provided tier percentages.
    Returns the percentage (e.g., 25.5 for 25.5% reduction).
    """
    tier_percentages_up_to_19 = [
        10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0,
        15.5, 16.0, 16.5, 17.0, 17.5, 18.0, 18.5, 19.0, 19.5
    ]
    max_reduction = 30.0

    if forge_time_level is None or forge_time_level < 1:
        return 0.0

    level = int(forge_time_level)

    if level >= 20:
        return max_reduction
    elif level >= 1 and level <= len(tier_percentages_up_to_19):
        return tier_percentages_up_to_19[level - 1]
    else:
        print(f"Warning: Unexpected forge_time_level: {level}")
        return 0.0


class ForgeCog(commands.Cog, name="Forge Functions"):
    """
    This cog handles commands related to the Skyblock Forge.
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
            with open('forge_items.json', 'r', encoding='utf-8') as f:
                self.forge_items_data = json.load(f)
            print("forge_items.json successfully loaded.")
        except FileNotFoundError:
            print("WARNING: forge_items.json not found. Forge duration calculation may be inaccurate.")
        except json.JSONDecodeError:
            print("ERROR: Could not decode forge_items.json. Check the file for syntax errors.")
            self.forge_items_data = {}
        except Exception as e:
            print(f"An unexpected error occurred loading forge_items.json: {e}")
            self.forge_items_data = {}

        # Load registration data (read-only for this cog)
        self.registrations = self.load_registrations()


    def load_registrations(self):
        """Loads registration data from the JSON file (read-only)."""
        if not os.path.exists(REGISTRATION_FILE):
            return {}
        try:
            with open(REGISTRATION_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            print(f"ERROR: Could not load {REGISTRATION_FILE} in ForgeCog: {e}. Assuming empty registrations.")
            return {}

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.__class__.__name__} Cog loaded and ready.")
        # Reload registrations on ready in case they changed while the bot was off
        self.registrations = self.load_registrations()


    # Modify the forge command to accept optional username
    @app_commands.command(name="forge", description="Shows the items currently in your registered or a specified player's Skyblock Forge.")
    @app_commands.describe(username="Optional: The Minecraft name of the player. Defaults to your first registered account if omitted.")
    @app_commands.describe(profile_name="Optional: A specific Skyblock profile name. Defaults to your registered profile or last played.")
    async def forge_command(self, interaction: discord.Interaction, username: str = None, profile_name: str = None):
        """
        Fetches and displays the items currently in the player's Skyblock forge.
        Uses registered account if username is not provided.
        Includes remaining time calculation with Quick Forge perk if applicable.
        """
        if not self.hypixel_api_key:
            await interaction.response.send_message(
                "The Hypixel API key is not configured. Please inform the bot owner.",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        target_uuid = None
        target_username = None
        target_profile_name = profile_name # Use provided profile_name first

        # Determine target user and profile based on input and registration
        if username:
            # Username provided, use it
            target_username = username
            # 1. Get the player's UUID using the provided username
            target_uuid = get_uuid(target_username)
            if not target_uuid:
                await interaction.followup.send(f"Could not find Minecraft player '{target_username}'. Please check the username.")
                return
        else:
            # No username provided, try to use registered account
            discord_user_id = str(interaction.user.id)
            self.registrations = self.load_registrations() # Reload registrations to get latest

            if discord_user_id in self.registrations and self.registrations[discord_user_id]:
                 # User has registered accounts, use the first one
                 first_registered_account = self.registrations[discord_user_id][0]
                 target_uuid = first_registered_account['uuid']
                 # Try to get current username for display (optional, requires Mojang API lookup)
                 # Skipping username lookup for now, will display UUID if needed

                 # Use the first registered profile for this account if profile_name was not provided
                 if target_profile_name is None:
                      registered_profiles = first_registered_account.get('profiles')
                      if registered_profiles and len(registered_profiles) > 0:
                           target_profile_name = registered_profiles[0] # Use the first registered profile

                 # Get the latest username for the registered UUID for a better message
                 # This would require a separate Mojang API call by UUID (less common endpoint)
                 # For simplicity, we'll proceed with UUID and profile name/last played logic

            else:
                 # User is not registered and didn't provide a username
                 await interaction.followup.send("Please provide a Minecraft username or register your account using `/register`.")
                 return

        # At this point, we have target_uuid and potentially target_profile_name

        # Get a username for the UUID for display purposes (optional but good)
        # Mojang API for UUID to username is less straightforward for history,
        # but we can sometimes infer it from the Skyblock profile data later.
        # For now, we might just display the UUID if username wasn't provided.

        uuid_dashed = format_uuid(target_uuid)

        # 2. Get Skyblock profiles
        profiles_data = get_player_profiles(self.hypixel_api_key, uuid_dashed)

        if not profiles_data or not profiles_data.get("success", False):
            error_message = "Failed to retrieve Skyblock profiles."
            if profiles_data and profiles_data.get("cause"):
                 error_message += f" Reason: {profiles_data['cause']}"
            # If username was provided, use it in the message, else use the UUID
            user_identifier = target_username if target_username else f"UUID `{target_uuid}`"
            await interaction.followup.send(f"{error_message} for {user_identifier}.")
            return

        profiles = profiles_data.get("profiles", [])
        if not profiles:
            user_identifier = target_username if target_username else f"UUID `{target_uuid}`"
            await interaction.followup.send(f"No Skyblock profiles found for {user_identifier}.")
            return

        # 3. Find the target profile
        target_profile = None
        if target_profile_name:
            # Find profile by name
            target_profile = find_profile_by_name(profiles_data, target_profile_name)
            if not target_profile:
                user_identifier = target_username if target_username else f"UUID `{target_uuid}`"
                await interaction.followup.send(f"Profile '{target_profile_name}' not found for {user_identifier}.")
                return
        else:
            # Use the last played profile if no name is given
            last_save_timestamp = 0
            for profile in profiles:
                 # The API usually marks the last played profile with 'selected': true
                 # Fallback to the profile with the latest 'last_save' timestamp
                 member_data_check = profile.get("members", {}).get(target_uuid, {})
                 if profile.get("selected", False): # Prioritize 'selected' flag
                     target_profile = profile
                     break
                 # Fallback check: find profile with latest last_save
                 current_last_save = member_data_check.get("last_save", 0)
                 if current_last_save > last_save_timestamp:
                      last_save_timestamp = current_last_save
                      target_profile = profile # This will be the profile with the latest save if no 'selected' found

            if not target_profile:
                 user_identifier = target_username if target_username else f"UUID `{target_uuid}`"
                 await interaction.followup.send(f"Could not determine the last played profile for {user_identifier}.")
                 return

        profile_id = target_profile.get("profile_id") # Keep profile_id if needed elsewhere
        profile_cute_name = target_profile.get("cute_name", "Unknown Profile")

        # Try to get the current username from the profile data if username wasn't provided initially
        if target_username is None:
             member_data_check = target_profile.get("members", {}).get(target_uuid, {})
             # Hypixel profile data sometimes includes the player's name
             player_name_in_profile = member_data_check.get("displayname") # Or similar key
             if player_name_in_profile:
                  target_username = player_name_in_profile
             else:
                  target_username = f"UUID: `{target_uuid}`" # Fallback if name not in profile

        # 4. Get Quick Forge Perk Level and calculate reduction
        member_data = target_profile.get("members", {}).get(target_uuid, {})
        forge_time_level = member_data.get("mining_core", {}).get("nodes", {}).get("forge_time")

        time_reduction_percent = calculate_quick_forge_reduction(forge_time_level)
        perk_applied_message = ""
        if time_reduction_percent > 0:
             perk_applied_message = f" (Quick Forge: -{time_reduction_percent:.1f}%)"

        # 5. Extract Forge Data
        try:
            forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})

            forge_items_output = []
            current_time_ms = time.time() * 1000

            for forge_type_key in sorted(forge_processes_data.keys()):
                 slots_data = forge_processes_data[forge_type_key]
                 for slot in sorted(slots_data.keys(), key=int):
                    item_data = slots_data[slot]
                    item_id = item_data.get("id", "Unknown Item")
                    start_time_ms = item_data.get("startTime")

                    item_name = item_id
                    remaining_time_str = "Time unknown"

                    forge_item_info = self.forge_items_data.get(item_id)

                    if forge_item_info and start_time_ms is not None:
                        item_name = forge_item_info.get("name", item_id)
                        base_duration_ms = forge_item_info.get("duration")

                        if base_duration_ms is not None:
                            effective_duration_ms = base_duration_ms * (1 - time_reduction_percent / 100)
                            end_time_ms = start_time_ms + effective_duration_ms
                            remaining_time_ms = end_time_ms - current_time_ms
                            remaining_time_str = format_time_difference(remaining_time_ms)
                        else:
                             remaining_time_str = "Duration unknown (JSON)"

                    elif start_time_ms is None:
                         remaining_time_str = "Start time unknown (API)"

                    forge_items_output.append(f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Remaining: {remaining_time_str}")

            if not forge_items_output:
                 await interaction.followup.send(f"No active items found in the Forge on profile '{profile_cute_name}' of '{target_username}'{perk_applied_message}.")
                 return

            response_message = f"Current items in the Forge on profile '{profile_cute_name}' of '{target_username}'{perk_applied_message}:\n"
            response_message += "\n".join(forge_items_output)

            await interaction.followup.send(response_message)

        except Exception as e:
            print(f"Error processing forge data for {target_username} on profile {profile_cute_name}: {e}")
            await interaction.followup.send(f"An internal error occurred while retrieving or processing Forge data.")


async def setup(bot: commands.Bot):
    """Adds the ForgeCog to the bot."""
    await bot.add_cog(ForgeCog(bot))