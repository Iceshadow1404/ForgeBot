# forge_cog.py

import discord
from discord import app_commands
from discord.ext import commands, tasks
import time
import json
import asyncio
import requests
import os

from embed import create_forge_embed, ForgePaginationView, SingleForgeView
from skyblock import get_uuid, format_uuid, get_player_profiles, find_profile_by_name, uuid_to_username
from constants import *
from logs import logger
from utils import format_time_difference
# Import the new notification manager class
from forge_notifications import ForgeNotificationManager

# --- Helper Functions (Kept in forge_cog.py as they are used by the command) ---

def calculate_quick_forge_reduction(forge_time_level: int | None) -> float:
    """
    Calculates Quick Forge time reduction percentage based on tier level.
    """
    tier_percentages = [
        10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0,
        15.5, 16.0, 16.5, 17.0, 17.5, 18.0, 18.5, 19.0, 19.5
    ]
    max_reduction = 30.0

    if forge_time_level is None or forge_time_level < 1:
        return 0.0

    level = int(forge_time_level)

    if level >= 20:
        return max_reduction
    elif 1 <= level <= len(tier_percentages):
        return tier_percentages[level - 1]
    else:
        logger.warning(f"Unexpected forge_time_level: {level}. Returning 0%.")
        return 0.0

def format_active_forge_items(forge_processes_data: dict, forge_items_config: dict, time_reduction_percent: float,
                              clock_is_actively_buffing: bool) -> list[str]:
    """
    Formats the active forge items with remaining times, applying buffs.
    Returns a list of formatted strings, one for each active item.
    This version is for displaying in the /forge command.
    """
    logger.debug(f"Entering format_active_forge_items for command. Reduction: {time_reduction_percent}%, Clock Active: {clock_is_actively_buffing}")
    forge_items_output = []
    current_time_ms = time.time() * 1000

    if not isinstance(forge_processes_data, dict) or not forge_processes_data:
        logger.debug("No forge process data found or invalid. Returning empty list.")
        return []

    for forge_type_key in sorted(forge_processes_data.keys()):
        slots_data = forge_processes_data.get(forge_type_key)
        logger.debug(f"Processing forge type: {forge_type_key}")

        if not isinstance(slots_data, dict):
            logger.warning(f"Skipping invalid slots data for type {forge_type_key}.")
            continue

        sorted_slots = sorted(slots_data.keys(), key=lambda x: int(x) if str(x).isdigit() else float('inf'))
        logger.debug(f"Sorted slots for {forge_type_key}: {sorted_slots}")

        for slot in sorted_slots:
            item_data = slots_data.get(slot)
            logger.debug(f"Processing slot {slot} in {forge_type_key}")

            if not isinstance(item_data, dict) or item_data.get("startTime") is None:
                logger.debug(f"Skipping slot {slot} in {forge_type_key} due to missing data or start time.")
                continue

            item_id = item_data.get("id", "Unknown Item")
            start_time_ms = item_data.get("startTime")

            item_name = item_id
            remaining_time_str = "Time unknown"

            forge_item_info = forge_items_config.get(item_id)
            logger.debug(f"Item ID: {item_id}, Start Time: {start_time_ms}")

            if forge_item_info and start_time_ms is not None:
                item_name = forge_item_info.get("name", item_id)
                base_duration_ms = forge_item_info.get("duration")
                logger.debug(f"Found forge item info for {item_id}. Name: {item_name}, Base Duration: {base_duration_ms}")

                if base_duration_ms is not None and isinstance(base_duration_ms, (int, float)):
                    effective_duration_ms = base_duration_ms * (1 - time_reduction_percent / 100)
                    end_time_ms = start_time_ms + effective_duration_ms
                    remaining_time_ms = end_time_ms - current_time_ms

                    logger.debug(f"Base Duration: {base_duration_ms}, Effective Duration: {effective_duration_ms}, End Time: {end_time_ms}, Remaining Before Clock: {remaining_time_ms}")

                    if clock_is_actively_buffing:
                        logger.debug(f"Clock is active. Applying {ENCHANTED_CLOCK_REDUCTION_MS}ms reduction.")
                        remaining_time_ms = max(0, remaining_time_ms - ENCHANTED_CLOCK_REDUCTION_MS)
                        logger.debug(f"Remaining After Clock: {remaining_time_ms}")


                    remaining_time_str = format_time_difference(remaining_time_ms)
                    logger.debug(f"Formatted remaining time: {remaining_time_str}")
                else:
                    remaining_time_str = "Duration unknown (JSON)"
                    logger.warning(f"Forge item duration missing or invalid in JSON for item ID: {item_id}")


            elif start_time_ms is None:
                remaining_time_str = "Start time unknown (API)"
                logger.warning(f"Start time missing for item ID: {item_id} from API.")
            else:
                remaining_time_str = "Duration unknown (Item data missing)"
                logger.warning(f"Forge item info missing for item ID: {item_id}")


            forge_items_output.append(
                f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Remaining: {remaining_time_str}")
            logger.debug(f"Added formatted item: {forge_items_output[-1]}")


    logger.debug(f"Exiting format_active_forge_items for command. Returning {len(forge_items_output)} items.")
    return forge_items_output


# --- Main Cog Class ---
class ForgeCog(commands.Cog, name="Forge Functions"):
    """
    A Discord Bot Cog for Skyblock Forge related commands and notifications.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("Initializing ForgeCog.")

        self.hypixel_api_key = os.getenv("HYPIXEL_API_KEY")
        if not self.hypixel_api_key:
            logger.warning("HYPIXEL_API_KEY not found. Forge commands requiring API access will not work.")
        else:
            logger.info("HYPIXEL_API_KEY found.")

        self.webhook_url = os.getenv("WEBHOOK_URL") # Keep webhook_url here as it's needed for the manager init

        self.forge_items_data = self.load_forge_items_data()
        # Registrations will be loaded/managed by the Notification Manager for its task,
        # but we keep load_registrations here for the command's default behavior.
        self.registrations = self.load_registrations() # Load initial registrations for command


        self.clock_usage = self.load_clock_usage() # Keep clock usage in ForgeCog

        # Instantiate the Notification Manager
        self.notification_manager = ForgeNotificationManager(
            bot=self.bot,
            hypixel_api_key=self.hypixel_api_key,
            webhook_url=self.webhook_url,
            forge_items_data=self.forge_items_data,
            forge_cog_ref=self # Pass reference to self
        )

        logger.info("ForgeCog initialized.")


    # --- Data Loading and Saving ---
    # (Keep load_forge_items_data, load_registrations, load_clock_usage, save_clock_usage here)
    # load_notifications_status and save_notifications_status are moved to ForgeNotificationManager

    def load_forge_items_data(self) -> dict:
        """Loads forge item configuration data from 'forge_items.json'."""
        logger.debug("Loading forge_items.json...")
        try:
            with open('forge_items.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
            logger.info("forge_items.json loaded successfully.")
            logger.debug(f"Loaded data preview: {list(data.keys())[:5]}...") # Log a preview
            return data
        except FileNotFoundError:
            logger.warning("forge_items.json not found. Forge duration calculation may be inaccurate.")
            return {}
        except json.JSONDecodeError:
            logger.error("Could not decode forge_items.json. Check the file for syntax errors.", exc_info=True)
            return {}
        except Exception as e:
            logger.error(f"An unexpected error occurred loading forge_items.json: {e}", exc_info=True)
            return {}

    def load_registrations(self) -> dict:
        """Loads user registration data from REGISTRATION_FILE. Kept for command use."""
        logger.debug(f"Loading registrations from {REGISTRATION_FILE} for command...")
        if not os.path.exists(REGISTRATION_FILE):
            logger.info(f"Registration file not found: {REGISTRATION_FILE}. Starting with empty data for command.")
            return {}
        try:
            with open(REGISTRATION_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            cleaned_data = {}
            if not isinstance(data, dict):
                 logger.warning(f"Invalid data format in {REGISTRATION_FILE}. Expected dictionary for command. Starting fresh.")
                 return {}

            for user_id, accounts in data.items():
                if isinstance(user_id, str) and isinstance(accounts, list):
                     # Add debug log for each user loaded
                    logger.debug(f"Loaded user {user_id} from {REGISTRATION_FILE} for command.")
                    cleaned_accounts = []
                    for account in accounts:
                        if isinstance(account, dict) and account.get('uuid') is not None:
                             # Add debug log for each UUID loaded
                            logger.debug(f"Loaded UUID {account['uuid']} for user {user_id}.")
                            cleaned_accounts.append(account)
                        else:
                            logger.warning(f"Invalid account entry found for user {user_id} in registrations (command): {account}. Skipping.")
                    if cleaned_accounts:
                        cleaned_data[user_id] = cleaned_accounts
                else:
                     logger.warning(f"Invalid registration format for user {user_id} (command): {accounts}. Skipping.")

            logger.info(f"Registrations loaded successfully from {REGISTRATION_FILE} for command. Loaded {len(cleaned_data)} users.")
            return cleaned_data
        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"Could not load {REGISTRATION_FILE} for command: {e}. Assuming empty registrations.", exc_info=True)
            return {}


    def load_clock_usage(self) -> dict:
        """Loads Enchanted Clock usage tracking data from CLOCK_USAGE_FILE."""
        logger.debug(f"Loading clock usage from {CLOCK_USAGE_FILE}...")
        if not os.path.exists(CLOCK_USAGE_FILE):
            logger.info(f"Clock usage file not found: {CLOCK_USAGE_FILE}. Starting with empty data.")
            return {}
        try:
            with open(CLOCK_USAGE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, dict):
                logger.warning(f"Invalid data format in {CLOCK_USAGE_FILE}. Expected dictionary. Starting fresh.")
                return {}

            cleaned_data = {}
            for uuid, profiles in data.items():
                if isinstance(uuid, str) and isinstance(profiles, dict):
                    cleaned_profiles = {}
                    for profile_id, p_data in profiles.items():
                        if (isinstance(profile_id, str) and isinstance(p_data, dict) and
                            p_data.get("end_timestamp") is not None and isinstance(p_data.get("end_timestamp"), (int, float)) and
                            p_data.get("profile_name") is not None and isinstance(p_data.get("profile_name"), str)):
                             logger.debug(f"Loaded clock usage for UUID {uuid}, Profile {profile_id}.")
                             cleaned_profiles[profile_id] = p_data
                        else:
                             logger.warning(f"Invalid clock usage entry found for UUID {uuid}, Profile ID {profile_id}. Skipping.")

                    if cleaned_profiles:
                        cleaned_data[uuid] = cleaned_profiles
                else:
                     logger.warning(f"Invalid clock usage entry found for key {uuid}. Skipping.")

            logger.info(f"Clock usage data loaded successfully from {CLOCK_USAGE_FILE}. Loaded {len(cleaned_data)} UUIDs with entries.")
            return cleaned_data
        except Exception as e:
            logger.error(f"An unexpected error occurred loading {CLOCK_USAGE_FILE}: {e}", exc_info=True)
            return {}

    def save_clock_usage(self):
        """Saves the current Enchanted Clock usage tracking data."""
        logger.debug(f"Saving clock usage data to {CLOCK_USAGE_FILE}...")
        try:
            save_dir = os.path.dirname(CLOCK_USAGE_FILE)
            if save_dir and not os.path.exists(save_dir):
                logger.debug(f"Directory {save_dir} does not exist. Creating...")
                try:
                    os.makedirs(save_dir, exist_ok=True)
                    logger.debug(f"Directory {save_dir} created successfully.")
                except OSError as e:
                    logger.error(f"Could not create directory {save_dir}: {e}", exc_info=True)
                    return # Cannot save if directory creation fails

            temp_file = CLOCK_USAGE_FILE + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self.clock_usage, f, indent=4)
            os.replace(temp_file, CLOCK_USAGE_FILE)
            logger.info(f"Successfully saved clock usage data to {CLOCK_USAGE_FILE}")
        except PermissionError as pe:
            logger.error(f"Permission error saving {CLOCK_USAGE_FILE}: {pe}", exc_info=True)
        except FileNotFoundError as fnfe:
            # This case should be less likely with os.makedirs(exist_ok=True)
            logger.error(f"File not found error saving {CLOCK_USAGE_FILE}: {fnfe}", exc_info=True)
        except OSError as ose:
            logger.error(f"OS error saving {CLOCK_USAGE_FILE}: {ose}", exc_info=True)
        except json.JSONDecodeError as json_error:
            # This would happen if self.clock_usage somehow became non-serializable
            logger.error(f"JSON error saving {CLOCK_USAGE_FILE}: {json_error}", exc_info=True)
        except Exception as e:
            logger.error(f"Unexpected error saving {CLOCK_USAGE_FILE}: {e}", exc_info=True)


    # --- Clock Usage Logic (Keep in ForgeCog as it's used by the command and manager) ---

    def is_clock_used(self, uuid: str, profile_internal_id: str) -> bool:
        """Checks if the Enchanted Clock buff is active for a profile."""
        logger.debug(f"Checking if clock is used for UUID: {uuid}, Profile ID: {profile_internal_id}")
        profile_data = self.clock_usage.get(uuid, {}).get(profile_internal_id)
        if isinstance(profile_data, dict) and profile_data.get("end_timestamp") is not None and isinstance(profile_data.get("end_timestamp"), (int, float)):
             is_active = time.time() * 1000 < profile_data["end_timestamp"]
             logger.debug(f"Clock is active: {is_active} for UUID: {uuid}, Profile ID: {profile_internal_id}")
             return is_active
        logger.debug(f"Clock data not found or invalid for UUID: {uuid}, Profile ID: {profile_internal_id}. Returning False.")
        return False

    def mark_clock_used(self, uuid: str, profile_internal_id: str, profile_cute_name: str):
        """Marks the Enchanted Clock as used for a profile."""
        logger.info(f"Marking clock as used for UUID: {uuid}, Profile ID: {profile_internal_id}, Profile Name: {profile_cute_name}")
        current_time_ms = time.time() * 1000
        end_timestamp = current_time_ms + ENCHANTED_CLOCK_REDUCTION_MS
        if uuid not in self.clock_usage:
            self.clock_usage[uuid] = {}
            logger.debug(f"Created new UUID entry in clock usage for {uuid}")
        self.clock_usage[uuid][profile_internal_id] = {
            "profile_name": profile_cute_name,
            "end_timestamp": end_timestamp
        }
        logger.debug(f"Set end timestamp for clock usage: {end_timestamp} for Profile ID: {profile_internal_id}")
        self.save_clock_usage()

    def reset_clock_usage(self, uuid: str, profile_internal_id: str):
        """Resets the Enchanted Clock usage status for a profile."""
        logger.info(f"Attempting to reset clock usage for UUID: {uuid}, Profile ID: {profile_internal_id}")
        if uuid in self.clock_usage and profile_internal_id in self.clock_usage.get(uuid, {}):
            del self.clock_usage[uuid][profile_internal_id]
            logger.debug(f"Deleted clock usage entry for Profile ID: {profile_internal_id} under UUID: {uuid}")
            if not self.clock_usage[uuid]:
                del self.clock_usage[uuid]
                logger.debug(f"Deleted empty UUID entry in clock usage for {uuid}")
            self.save_clock_usage()
            logger.info(f"Clock usage reset for UUID: {uuid}, Profile ID: {profile_internal_id}")
        else:
            logger.debug(f"No active clock usage found for UUID: {uuid}, Profile ID: {profile_internal_id}. No reset needed.")


    def cleanup_expired_clock_entries(self):
        """Removes expired and invalid clock usage entries."""
        logger.debug("Running cleanup for expired clock entries.")
        current_time_ms = time.time() * 1000
        modified = False

        for uuid in list(self.clock_usage.keys()):
            profiles = self.clock_usage.get(uuid)

            if not isinstance(profiles, dict):
                logger.warning(f"Found invalid clock usage data for UUID {uuid}. Removing entry.")
                if uuid in self.clock_usage:
                    del self.clock_usage[uuid]
                    modified = True
                continue

            profile_ids_to_delete = []
            for profile_id, pdata in list(profiles.items()):
                is_invalid = (
                    not isinstance(pdata, dict) or
                    "end_timestamp" not in pdata or
                    not isinstance(pdata.get("end_timestamp"), (int, float)) or
                    "profile_name" not in pdata or
                    not isinstance(pdata.get("profile_name"), str)
                )
                is_expired = not is_invalid and current_time_ms >= pdata.get("end_timestamp", 0)

                if is_invalid or is_expired:
                    profile_ids_to_delete.append(profile_id)
                    if is_invalid:
                         logger.warning(f"Cleaning up invalid clock entry for UUID {uuid}, Profile ID {profile_id}.")
                    elif is_expired:
                         logger.info(f"Cleaning up expired clock entry for profile '{pdata.get('profile_name', 'Unknown')}' ({profile_id}) for UUID {uuid}.")


            for profile_id in profile_ids_to_delete:
                if profile_id in profiles:
                    del profiles[profile_id]
                    modified = True
                    logger.debug(f"Removed expired/invalid clock entry for Profile ID {profile_id} under UUID {uuid}.")


            if not profiles and uuid in self.clock_usage:
                del self.clock_usage[uuid]
                modified = True
                logger.debug(f"Removed empty UUID entry in clock usage for {uuid}")


        if modified:
            self.save_clock_usage()
            logger.debug("Clock usage data was modified during cleanup. Saved changes.")
        else:
            logger.debug("No clock usage data modified during cleanup.")


    # --- Discord Event Listeners ---

    @commands.Cog.listener()
    async def on_ready(self):
        """Event handler for when the cog is loaded and bot is ready."""
        logger.info(f"{self.__class__.__name__} Cog loaded and ready.")
        # Reload data on ready
        self.registrations = self.load_registrations() # Reload for command consistency
        self.clock_usage = self.load_clock_usage()
        self.cleanup_expired_clock_entries() # Keep clock cleanup here

        # The notification manager handles its own loading and task starting now.
        if not self.notification_manager.webhook_url:
            logger.warning("WEBHOOK_URL not set. Forge completion notifications will be disabled.")
        else:
             logger.info("Webhook URL found. Forge completion notifications are enabled.")


        # Start the notification manager's periodic task loop
        self.notification_manager.start_notifications_task()


    # --- Discord Commands ---

    @app_commands.command(name="forge",
                          description="Shows active items in your or a specified player's Skyblock Forge.")
    @app_commands.describe(username="Optional: Minecraft name. Defaults to your first registered account.")
    @app_commands.describe(profile_name="Optional: Specific Skyblock profile name. Defaults to the latest played.")
    async def forge_command(self, interaction: discord.Interaction, username: str = None, profile_name: str = None):
        """
        Discord command to display active forge items.
        """
        logger.info(f"Forge command triggered by {interaction.user.id} with username: {username}, profile: {profile_name}")

        if not self.hypixel_api_key:
            logger.warning("Forge command failed: Hypixel API key not configured.")
            await interaction.response.send_message("Hypixel API key not configured.", ephemeral=True)
            return

        await interaction.response.defer()
        logger.debug("Deferred interaction for forge command.")
        # Cleanup clock entries before potentially checking forge status
        self.cleanup_expired_clock_entries()
        logger.debug("Cleaned up expired clock entries before processing forge command.")


        # --- Handle Case: No username or profile specified (Show registered accounts with pagination) ---
        if username is None and profile_name is None:
            logger.debug("Processing forge command for registered accounts with pagination.")
            discord_user_id = str(interaction.user.id)
            # Reload registrations here to get the latest for the command
            self.registrations = self.load_registrations()
            user_accounts = self.registrations.get(discord_user_id)

            if not user_accounts:
                logger.info(f"User {discord_user_id} has no registered accounts.")
                await interaction.followup.send("No registered accounts. Use `/register`.", ephemeral=True)
                return

            active_forge_profiles_data = []
            logger.debug(f"User {discord_user_id} has {len(user_accounts)} registered accounts.")

            for account in user_accounts:
                current_uuid = account.get('uuid')
                logger.debug(f"Checking account with UUID: {current_uuid} for user {discord_user_id}")
                if not current_uuid:
                    logger.warning(f"Skipping account with missing UUID for user {discord_user_id}.")
                    continue

                uuid_dashed = format_uuid(current_uuid)
                profiles_data = get_player_profiles(self.hypixel_api_key, uuid_dashed)

                if not profiles_data or not profiles_data.get("success", False):
                    logger.error(f"Could not retrieve profiles for UUID {current_uuid} for user {discord_user_id}.")
                    continue

                profiles = profiles_data.get("profiles", [])
                if not profiles:
                    logger.debug(f"No Skyblock profiles found for UUID {current_uuid}.")
                    continue

                logger.debug(f"Found {len(profiles)} profiles for UUID {current_uuid}.")

                # Attempt to get the username using the UUID
                current_username_display = uuid_to_username(current_uuid)
                 # Fallback to UUID display if username lookup fails or returns None
                if not current_username_display:
                     logger.warning(f"Could not get username for UUID {current_uuid} using uuid_to_username. Using UUID display.")
                     current_username_display = f"UUID: {current_uuid[:8]}..."

                for profile in profiles:
                    profile_cute_name = profile.get("cute_name", "Unknown Profile")
                    profile_internal_id = profile.get("profile_id")
                    logger.debug(f"Processing profile '{profile_cute_name}' ({profile_internal_id}) for UUID {current_uuid}")


                    if profile_internal_id is None:
                         logger.warning(f"Skipping profile '{profile_cute_name}' with missing internal ID for UUID {current_uuid}.")
                         continue

                    member_data = profile.get("members", {}).get(current_uuid, {})
                    forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})
                    logger.debug(f"Retrieved forge processes data for profile {profile_internal_id}.")


                    has_any_active_items = False
                    if isinstance(forge_processes_data, dict):
                         has_any_active_items = any(
                            isinstance(slots_data, dict) and isinstance(item_data, dict) and item_data.get("startTime") is not None
                            for forge_type_key, slots_data in forge_processes_data.items()
                            for slot_data in (slots_data or {}).values()
                            for item_data in (slot_data,) if isinstance(item_data, dict)
                        )
                    logger.debug(f"Profile {profile_internal_id} has active items: {has_any_active_items}")


                    if has_any_active_items:
                        forge_time_level = member_data.get("mining_core", {}).get("nodes", {}).get("forge_time")
                        # Use the helper function from this file
                        time_reduction_percent = calculate_quick_forge_reduction(forge_time_level)
                        perk_message = f" (Quick Forge: -{time_reduction_percent:.1f}%)" if time_reduction_percent > 0 else ""
                        logger.debug(f"Profile {profile_internal_id}: Forge Time Level: {forge_time_level}, Reduction: {time_reduction_percent}%")

                        # Use the clock usage method from self (ForgeCog)
                        clock_is_actively_buffing = self.is_clock_used(current_uuid, profile_internal_id)
                        logger.debug(f"Profile {profile_internal_id}: Clock is actively buffing: {clock_is_actively_buffing}")

                        # Use the helper function from this file
                        formatted_items_list = format_active_forge_items(
                            forge_processes_data, self.forge_items_data,
                            time_reduction_percent, clock_is_actively_buffing
                        )
                        logger.debug(f"Formatted {len(formatted_items_list)} active items for profile {profile_internal_id}.")


                        active_forge_profiles_data.append({
                            "uuid": current_uuid,
                            "profile_id": profile_internal_id,
                            "username": current_username_display, # Use the fetched username
                            "profile_name": profile_cute_name,
                            "perk_message": perk_message,
                            "items_raw": forge_processes_data,
                            "time_reduction_percent": time_reduction_percent,
                            # formatted_items is generated here for the initial embed
                            "formatted_items": "\n".join(formatted_items_list)
                        })
                        logger.debug(f"Added profile {profile_internal_id} to active forge profiles list.")


            if active_forge_profiles_data:
                logger.info(f"Found {len(active_forge_profiles_data)} profiles with active forge items for user {discord_user_id}.")
                # Pass self to the view so it can access clock usage methods
                view = ForgePaginationView(active_forge_profiles_data, interaction, self.forge_items_data, self)
                await interaction.edit_original_response(content="", embed=view.embeds[0], view=view)
                logger.debug("Sent paginated response for registered accounts.")
            else:
                logger.info(f"No active items found across registered accounts for user {discord_user_id}.")
                await interaction.followup.send("No active items found in Forge across your registered accounts.", ephemeral=True)

            return

        # --- Handle Case: Username and/or Profile specified (Show single profile data) ---
        logger.debug(f"Processing forge command for specific user ({username}) and/or profile ({profile_name}).")
        target_uuid = None
        target_username_display = None # This will hold the IGN

        if username:
            target_username_display = username # Start with provided username
            logger.debug(f"Getting UUID for provided username: {username}")
            target_uuid = get_uuid(username)

            if not target_uuid:
                logger.warning(f"Could not find player '{username}'.")
                await interaction.followup.send(f"Could not find player '{username}'. Please double-check the spelling.", ephemeral=True)
                return
            logger.debug(f"Found UUID for username {username}: {target_uuid}")

        else:
            logger.debug("No username provided, defaulting to first registered account.")
            discord_user_id = str(interaction.user.id)
            # Reload registrations here for the command
            self.registrations = self.load_registrations()
            user_accounts = self.registrations.get(discord_user_id)

            if not user_accounts:
                logger.info(f"User {discord_user_id} has no registered accounts when trying to use default.")
                await interaction.followup.send("Please provide a Minecraft username to check, or register your account first.", ephemeral=True)
                return

            first_registered_account = user_accounts[0]
            target_uuid = first_registered_account.get('uuid')
            if not target_uuid:
                 logger.error(f"Could not retrieve UUID for first registered account for user {discord_user_id}.")
                 await interaction.followup.send("Could not retrieve UUID for your first registered account. Please check your registration.", ephemeral=True)
                 return

            # Attempt to get the username using the UUID from the first registered account
            logger.debug(f"Getting username for registered account UUID: {target_uuid}")
            target_username_display = uuid_to_username(target_uuid) # Use the function from skyblock

            # If username lookup fails, fallback to a temporary display
            if not target_username_display:
                 logger.warning(f"Could not get username for UUID {target_uuid} using uuid_to_username. Using UUID display.")
                 target_username_display = f"Registered Account (UUID: {target_uuid[:8]}...)"
            logger.debug(f"Resolved username for registered account UUID {target_uuid}: {target_username_display}")


        uuid_dashed = format_uuid(target_uuid)
        logger.debug(f"Fetching profiles for UUID: {uuid_dashed}")
        profiles_data_full = get_player_profiles(self.hypixel_api_key, uuid_dashed)

        if not profiles_data_full or not profiles_data_full.get("success", False):
            logger.error(f"Failed to retrieve Skyblock profiles for '{target_username_display}' (UUID: {target_uuid}).")
            await interaction.followup.send(f"Failed to retrieve Skyblock profiles for '{target_username_display}'.", ephemeral=True)
            return

        profiles = profiles_data_full.get("profiles", [])

        if not profiles:
            logger.info(f"No Skyblock profiles found for '{target_username_display}' (UUID: {target_uuid}).")
            await interaction.followup.send(
                f"No Skyblock profiles found for '{target_username_display}'.", ephemeral=True)
            return
        logger.debug(f"Found {len(profiles)} profiles for '{target_username_display}'.")


        target_profile = None
        if profile_name:
            logger.debug(f"Looking for specific profile named: {profile_name}")
            target_profile = find_profile_by_name(profiles_data_full, profile_name)
            if not target_profile:
                logger.warning(f"Profile '{profile_name}' not found for '{target_username_display}'.")
                await interaction.followup.send(f"Profile '{profile_name}' not found for '{target_username_display}'.", ephemeral=True)
                return
            logger.debug(f"Found target profile: {profile_name}")
        else:
            logger.debug("No profile name specified. Looking for selected or latest played profile.")
            target_profile = next((p for p in profiles if p.get("selected")), None)
            if not target_profile and profiles:
                profiles.sort(key=lambda p: p.get("members", {}).get(target_uuid, {}).get("last_save", 0), reverse=True)
                target_profile = profiles[0]
            if not target_profile:
                logger.warning(f"Could not determine a suitable profile for '{target_username_display}'.")
                await interaction.followup.send(
                    f"Could not determine a suitable profile for '{target_username_display}'. Please specify a profile name.", ephemeral=True)
                return
            logger.debug(f"Selected profile: {target_profile.get('cute_name', 'Unknown Profile')}")


        profile_cute_name = target_profile.get("cute_name", "Unknown Profile")
        profile_internal_id = target_profile.get("profile_id")
        logger.debug(f"Selected profile details: Name: {profile_cute_name}, Internal ID: {profile_internal_id}")


        # This part is already good - it attempts to get the displayname from the profile data
        # and updates target_username_display, which will be used in create_forge_embed.
        # This ensures the latest IGN from the API is used if available in the profile data.
        member_data_check_display = target_profile.get("members", {}).get(target_uuid, {})
        player_name_final = member_data_check_display.get("displayname")
        if player_name_final:
            target_username_display = player_name_final
            logger.debug(f"Updated display name to player's IGN from profile data: {target_username_display}")


        if profile_internal_id is None:
            logger.warning(
                f"Could not get internal profile ID for '{profile_cute_name}' ({target_uuid}). Clock buff/notifications may not work correctly.")

        member_data = target_profile.get("members", {}).get(target_uuid, {})
        forge_time_level = member_data.get("mining_core", {}).get("nodes", {}).get("forge_time")
        # Use the helper function from this file
        time_reduction_percent = calculate_quick_forge_reduction(forge_time_level)
        perk_message = f" (Quick Forge: -{time_reduction_percent:.1f}%)" if time_reduction_percent > 0 else ""
        forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})
        logger.debug(f"Single profile '{profile_cute_name}': Forge Time Level: {forge_time_level}, Reduction: {time_reduction_percent}%")

        # Use the clock usage method from self (ForgeCog)
        clock_is_actively_buffing_single = False
        if profile_internal_id:
            clock_is_actively_buffing_single = self.is_clock_used(target_uuid, profile_internal_id)
        logger.debug(f"Single profile '{profile_cute_name}': Clock is actively buffing: {clock_is_actively_buffing_single}")


        has_any_active_items_single = False
        if isinstance(forge_processes_data, dict):
             has_any_active_items_single = any(
                isinstance(slots_data, dict) and isinstance(item_data, dict) and item_data.get("startTime") is not None
                for forge_type_key, slots_data in forge_processes_data.items()
                for slot_data in (slots_data or {}).values()
                 for item_data in (slot_data,) if isinstance(item_data, dict)
            )
        logger.debug(f"Single profile '{profile_cute_name}' has active items: {has_any_active_items_single}")


        if not has_any_active_items_single:
            logger.info(f"No active items found in Forge on profile '{profile_cute_name}' of '{target_username_display}'.")
            await interaction.followup.send(
                f"No active items found in Forge on profile '{profile_cute_name}' of '{target_username_display}'{perk_message}.", ephemeral=True)
            return

        # Use the helper function from this file
        formatted_items_list_single = format_active_forge_items(
            forge_processes_data, self.forge_items_data,
            time_reduction_percent, clock_is_actively_buffing_single
        )
        logger.debug(f"Formatted {len(formatted_items_list_single)} active items for single profile '{profile_cute_name}'.")


        single_profile_data = {
            "uuid": target_uuid,
            "profile_id": profile_internal_id,
            "username": target_username_display, # Use the fetched IGN here
            "profile_name": profile_cute_name,
            "perk_message": perk_message,
            "items_raw": forge_processes_data,
            "time_reduction_percent": time_reduction_percent,
        }
        logger.debug("Prepared single profile data for embed.")


        embed = create_forge_embed(
            single_profile_data,
            "\n".join(formatted_items_list_single) if formatted_items_list_single else "No active items found."
        )

        if clock_is_actively_buffing_single:
            clock_note = "\n*Enchanted Clock buff applied.*"
            embed.description = (embed.description or "") + clock_note
            logger.debug("Added clock note to single embed description.")


        view = SingleForgeView(
            single_profile_data,
            interaction,
            self.forge_items_data,
            self, # Pass self (ForgeCog instance) to the view for clock usage
            "\n".join(formatted_items_list_single)
        )
        logger.debug("Created SingleForgeView.")


        await interaction.followup.send(embed=embed, view=view)
        logger.info(f"Sent single profile forge embed for '{target_username_display}' on profile '{profile_cute_name}'.")


# --- Cog Setup Function ---

async def setup(bot: commands.Bot):
    """Sets up the ForgeCog and adds it to the bot."""
    logger.info("Setting up ForgeCog.")
    await bot.add_cog(ForgeCog(bot))
    logger.info("ForgeCog added to bot.")