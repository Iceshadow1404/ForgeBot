# forge_cog.py

import discord
from discord import app_commands
from discord.ext import commands
import os
import time
import json
import asyncio # Keep asyncio if potentially needed for other operations

# Import necessary functions from skyblock.py
from skyblock import get_uuid, format_uuid, get_player_profiles, find_profile_by_name

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
        # Include minutes if there are hours or days, or if less than an hour
        if minutes > 0:
             parts.append(f"{minutes}m")
        # Seconds are ignored if time is 1 hour or more
    else: # Time is less than 1 hour, include minutes and seconds
        if hours > 0: # This case should not happen if milliseconds < 3.6M
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        # Only show seconds if less than 1 hour
        if seconds > 0 or not parts: # Include seconds if > 0 or if duration was <1 minute
             parts.append(f"{seconds}s")

    if not parts and milliseconds > 0: # Handle cases less than a second but > 0ms
         return "<1s" # Indicate time is less than 1 second
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
    # Tier percentages provided: 10.5, 11.0, 11.5, ..., 19.5 (for tiers 1-19), 30.0 (for tier 20+)
    # We can represent this as a list for tiers 1-19 and handle tier 20+ separately.
    tier_percentages_up_to_19 = [
        10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0,
        15.5, 16.0, 16.5, 17.0, 17.5, 18.0, 18.5, 19.0, 19.5
    ]
    max_reduction = 30.0

    if forge_time_level is None or forge_time_level < 1:
        return 0.0 # No reduction if level is invalid or below tier 1

    level = int(forge_time_level) # Ensure level is an integer

    if level >= 20:
        return max_reduction
    elif level >= 1 and level <= len(tier_percentages_up_to_19):
        # Index is level - 1 because lists are 0-indexed
        return tier_percentages_up_to_19[level - 1]
    else:
        # This case should not happen if API provides valid levels >= 1
        print(f"Warning: Unexpected forge_time_level: {level}") # Log unexpected levels
        return 0.0 # Return 0 reduction for unknown levels


class ForgeCog(commands.Cog, name="Forge Functions"):
    """
    This cog bundles all Forge-specific commands and functionalities.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Load the Hypixel API Key from environment variables
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
            print("WARNING: forge_items.json not found. Forge duration calculation may be inaccurate.")
        except json.JSONDecodeError:
            print("ERROR: Could not decode forge_items.json. Check the file for syntax errors.")
            self.forge_items_data = {} # Clear data if decoding fails
        except Exception as e:
            print(f"An unexpected error occurred loading forge_items.json: {e}")
            self.forge_items_data = {} # Clear data on unexpected errors


    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.__class__.__name__} Cog loaded and ready.")


    @app_commands.command(name="forge", description="Shows the items currently in a player's Skyblock Forge.")
    @app_commands.describe(username="The Minecraft name of the player.")
    @app_commands.describe(profile_name="Optional: The name of the Skyblock profile (e.g., 'Apple'). If not specified, the last played profile is used.")
    async def forge_command(self, interaction: discord.Interaction, username: str, profile_name: str = None):
        """
        Fetches and displays the items currently in the player's Skyblock forge.
        Includes remaining time calculation with Quick Forge perk if applicable.
        """
        # Check if API key is available
        if not self.hypixel_api_key:
            await interaction.response.send_message(
                "The Hypixel API key is not configured. Please inform the bot owner.",
                ephemeral=True # Only visible to the user who used the command
            )
            return

        # Defer the response as API calls can take time
        await interaction.response.defer()

        # 1. Get the player's UUID
        uuid = get_uuid(username)
        if not uuid:
            await interaction.followup.send(f"Could not find Minecraft player '{username}'.")
            return

        uuid_dashed = format_uuid(uuid)

        # 2. Get Skyblock profiles
        profiles_data = get_player_profiles(self.hypixel_api_key, uuid_dashed)

        # Check if profiles were retrieved successfully
        if not profiles_data or not profiles_data.get("success", False):
            # Provide more specific error if possible
            error_message = "Failed to retrieve Skyblock profiles."
            if profiles_data and profiles_data.get("cause"):
                 error_message += f" Reason: {profiles_data['cause']}"
            await interaction.followup.send(f"{error_message} for '{username}'.")
            return

        profiles = profiles_data.get("profiles", [])
        if not profiles:
            await interaction.followup.send(f"No Skyblock profiles found for '{username}'.")
            return

        # 3. Find the target profile
        target_profile = None
        if profile_name:
            # Find profile by name
            target_profile = find_profile_by_name(profiles_data, profile_name)
            if not target_profile:
                await interaction.followup.send(f"Profile '{profile_name}' not found for '{username}'.")
                return
        else:
            # Use the last played profile if no name is given
            # The API often marks the last played profile with 'selected': true
            # Fallback to the profile with the latest 'last_save' timestamp
            last_save_timestamp = 0
            for profile in profiles:
                 member_data_check = profile.get("members", {}).get(uuid, {})
                 if profile.get("selected", False): # Prioritize 'selected' flag
                     target_profile = profile
                     break
                 # Fallback check: find profile with latest last_save
                 current_last_save = member_data_check.get("last_save", 0)
                 if current_last_save > last_save_timestamp:
                      last_save_timestamp = current_last_save
                      target_profile = profile # This will be the profile with the latest save if no 'selected' found


            if not target_profile:
                 await interaction.followup.send(f"Could not determine the last played profile for '{username}'.")
                 return

        profile_id = target_profile.get("profile_id") # Keep profile_id if needed elsewhere
        profile_cute_name = target_profile.get("cute_name", "Unknown Profile")

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
            # Forge processes are nested under 'forge_processes' and then keys like 'forge_1', 'forge_2', etc.
            forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})

            forge_items_output = []
            current_time_ms = time.time() * 1000 # Get current time in milliseconds

            # Iterate through different forge types (forge_1, forge_2 etc.)
            # Use sorted to process forge types in a consistent order if multiple exist
            for forge_type_key in sorted(forge_processes_data.keys()):
                 slots_data = forge_processes_data[forge_type_key]
                 # Iterate through slots within each forge type
                 # Sort slots numerically for consistent output order
                 for slot in sorted(slots_data.keys(), key=int):
                    item_data = slots_data[slot] # Get item_data for the sorted slot key
                    item_id = item_data.get("id", "Unknown Item")
                    start_time_ms = item_data.get("startTime")

                    item_name = item_id # Default to ID if name not found
                    remaining_time_str = "Time unknown" # Default if duration data is missing

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

                            # Calculate remaining time
                            end_time_ms = start_time_ms + effective_duration_ms
                            remaining_time_ms = end_time_ms - current_time_ms
                            remaining_time_str = format_time_difference(remaining_time_ms)
                        else:
                             remaining_time_str = "Duration unknown (JSON)" # Duration not found in JSON

                    elif start_time_ms is None:
                         remaining_time_str = "Start time unknown (API)" # Start time missing from API

                    # Add item details to the output list
                    # Use title case for forge type key for better readability (e.g., "Forge 1")
                    forge_items_output.append(f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Remaining: {remaining_time_str}")

            # Check if any active forge items were found across all forge types
            if not forge_items_output:
                 await interaction.followup.send(f"No active items found in the Forge on profile '{profile_cute_name}' of '{username}'.")
                 return

            # Construct the final response message
            response_message = f"Current items in the Forge on profile '{profile_cute_name}' of '{username}'{perk_applied_message}:\n"
            response_message += "\n".join(forge_items_output)

            # Send the response
            await interaction.followup.send(response_message)

        except Exception as e:
            # Log the error for debugging
            print(f"Error processing forge data for {username} on profile {profile_cute_name}: {e}")
            # Send a generic error message to the user
            await interaction.followup.send(f"An internal error occurred while retrieving or processing Forge data.")


async def setup(bot: commands.Bot):
    """Adds the ForgeCog to the bot."""
    await bot.add_cog(ForgeCog(bot))