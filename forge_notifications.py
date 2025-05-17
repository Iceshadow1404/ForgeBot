# forge_notifications.py

import discord
from discord.ext import commands, tasks
import time
import json
import asyncio
import requests
import os
import datetime
from collections import defaultdict # Added for easier structure

from skyblock import get_uuid, format_uuid, get_player_profiles, find_profile_by_name, uuid_to_username
from constants import * # Make sure constants.py defines FORGE_CHECK_INTERVAL_MINUTES, REGISTRATION_FILE, ENCHANTED_CLOCK_REDUCTION_MS
from logs import logger
from utils import format_time_difference
import math # Import math for ceil

# --- Constants for History ---
HISTORY_FILE = "./notification_history.json"
HISTORY_CLEANUP_DAYS = 7 # Clean up history entries older than 7 days

# --- Helper Functions (Used internally by the notification manager) ---

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

# --- Notification Manager Class ---

class ForgeNotificationManager:
    """
    Manages forge notifications for registered users.
    Simplified version without detailed status tracking.
    Includes history to prevent duplicate notifications.
    """
    def __init__(self, bot: commands.Bot, hypixel_api_key: str | None, webhook_url: str | None,
                 forge_items_data: dict, forge_cog_ref):
        self.bot = bot
        self.hypixel_api_key = hypixel_api_key
        self.webhook_url = webhook_url
        self.forge_items_data = forge_items_data
        self.forge_cog_ref = forge_cog_ref # Reference to the ForgeCog for clock usage

        logger.info("Initializing ForgeNotificationManager (Simplified with History).")

        if not self.hypixel_api_key:
            logger.warning("HYPIXEL_API_KEY not found. Forge notifications will not work.")
        if not self.webhook_url:
            logger.warning("WEBHOOK_URL not found. Forge notifications will not be sent.")

        # Registrations will be loaded per check from the file
        self.registrations = {} # Initial empty, loaded in task

        # History of notified items: set of (discord_user_id_str, profile_internal_id, start_time_ms, adjusted_end_time_ms)
        self.notified_items_history = set()
        self.load_history() # Load history on initialization

        logger.info("ForgeNotificationManager (Simplified with History) initialized.")

    def load_registrations(self) -> dict:
        """Loads user registration data from REGISTRATION_FILE for the notification task."""
        logger.debug(f"Loading registrations from {REGISTRATION_FILE} for notification task...")
        if not os.path.exists(REGISTRATION_FILE):
            logger.info(f"Registration file not found: {REGISTRATION_FILE}. Starting with empty data for notification task.")
            return {}
        try:
            with open(REGISTRATION_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            cleaned_data = {}
            if not isinstance(data, dict):
                 logger.warning(f"Invalid data format in {REGISTRATION_FILE}. Expected dictionary for notifications. Starting fresh.")
                 return {}

            for user_id, accounts in data.items():
                if isinstance(user_id, str) and isinstance(accounts, list):
                    # Add debug log for each user loaded for the task
                    logger.debug(f"Loaded user {user_id} from {REGISTRATION_FILE} for notification task.")
                    cleaned_accounts = []
                    for account in accounts:
                        if isinstance(account, dict) and account.get('uuid') is not None:
                            # Add debug log for each UUID loaded for the task
                            logger.debug(f"Loaded UUID {account['uuid']} for user {user_id} for notification task.")
                            cleaned_accounts.append(account)
                        else:
                            logger.warning(f"Invalid account entry found for user {user_id} in registrations (notification task): {account}. Skipping.")
                    if cleaned_accounts:
                        cleaned_data[user_id] = cleaned_accounts
                else:
                     logger.warning(f"Invalid registration format for user {user_id} (notification task): {accounts}. Skipping.")

            logger.info(f"Registrations loaded successfully from {REGISTRATION_FILE} for notification task. Loaded {len(cleaned_data)} users.")
            return cleaned_data
        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"Could not load {REGISTRATION_FILE}: {e}. Assuming empty registrations.", exc_info=True)
            return {}

    def load_history(self):
        """Loads notification history from HISTORY_FILE."""
        logger.debug(f"Loading notification history from {HISTORY_FILE}...")
        if not os.path.exists(HISTORY_FILE):
            logger.info(f"History file not found: {HISTORY_FILE}. Starting with empty history.")
            self.notified_items_history = set()
            return

        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                # JSON loads into a list, convert to set
                history_list = json.load(f)
                if isinstance(history_list, list):
                    # Convert list of lists or tuples to set of tuples
                    self.notified_items_history = set(tuple(item) for item in history_list)
                    logger.info(f"Notification history loaded successfully. Loaded {len(self.notified_items_history)} entries.")
                else:
                    logger.warning(f"Invalid data format in {HISTORY_FILE}. Expected list. Starting with empty history.")
                    self.notified_items_history = set()
        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"Could not load {HISTORY_FILE}: {e}. Starting with empty history.", exc_info=True)
            self.notified_items_history = set()

    def save_history(self):
        """Saves notification history to HISTORY_FILE."""
        logger.debug(f"Saving notification history to {HISTORY_FILE}...")
        try:
            # Convert set of tuples to list of lists for JSON serialization
            history_list = [list(item) for item in self.notified_items_history]
            with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(history_list, f, indent=4)
            logger.debug(f"Notification history saved successfully with {len(self.notified_items_history)} entries.")
        except Exception as e:
            logger.error(f"Could not save {HISTORY_FILE}: {e}", exc_info=True)

    def cleanup_history(self):
        """Removes old entries from the notification history."""
        if not self.notified_items_history:
            logger.debug("History is empty, no cleanup needed.")
            return

        logger.debug("Cleaning up notification history...")
        current_time_ms = time.time() * 1000
        cleanup_threshold_ms = current_time_ms - (HISTORY_CLEANUP_DAYS * 24 * 60 * 60 * 1000)

        original_count = len(self.notified_items_history)
        # Keep entries whose adjusted_end_time_ms is within the cleanup threshold
        self.notified_items_history = {
            item_tuple for item_tuple in self.notified_items_history
            if item_tuple[3] >= cleanup_threshold_ms # item_tuple[3] is adjusted_end_time_ms
        }

        removed_count = original_count - len(self.notified_items_history)
        if removed_count > 0:
            logger.info(f"Removed {removed_count} old entries from notification history.")
            self.save_history() # Save history after cleanup
        else:
             logger.debug("No old entries found in history to remove.")


    async def send_forge_webhook(self, notification_data: dict):
        """Sends a combined notification to the configured webhook URL."""
        logger.debug("Attempting to send forge webhook.")
        if not self.webhook_url:
            logger.warning("Webhook URL not configured. Skipping notification.")
            return

        message_content = notification_data.get("message", "A forge item is ready!")
        discord_user_id = notification_data.get("discord_user_id")
        # ready_items_sent is used to update history *after* successful send
        ready_items_sent = notification_data.get("ready_items_sent", [])

        if not message_content:
             logger.warning(f"Webhook message content is empty for user {discord_user_id}. Skipping.")
             return

        webhook_payload = {
            "content": message_content,
            "allowed_mentions": {
                "parse": ["users"],
                 "replied_user": False
            }
        }

        headers = {'Content-Type': 'application/json'}
        logger.debug(f"Sending webhook for user {discord_user_id} with payload: {webhook_payload}")

        try:
            response = await asyncio.to_thread(
                requests.post,
                self.webhook_url,
                json=webhook_payload,
                headers=headers,
                timeout=10
            )

            if 200 <= response.status_code < 300:
                logger.info(f"Successfully sent combined webhook notification for user {discord_user_id}.")
                # Add successfully notified items to history
                for item_info in ready_items_sent:
                    item_identifier = (
                        notification_data["discord_user_id_str"], # Use the string ID stored earlier
                        item_info["profile_internal_id"],
                        item_info["start_time_ms"],
                        item_info["adjusted_end_time_ms"]
                    )
                    self.notified_items_history.add(item_identifier)
                if ready_items_sent:
                     self.save_history() # Save history after adding new entries
                     logger.debug(f"Added {len(ready_items_sent)} items to history for user {discord_user_id}.")

            else:
                logger.error(f"Error sending combined webhook for user {discord_user_id}: {response.status_code} - {response.text}")

        except requests.exceptions.Timeout:
            logger.error(f"Timeout error sending combined webhook for user {discord_user_id}.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Request exception sending combined webhook for user {discord_user_id}: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Unexpected exception sending combined webhook for user {discord_user_id}: {e}", exc_info=True)


    @tasks.loop(minutes=FORGE_CHECK_INTERVAL_MINUTES) # Define FORGE_CHECK_INTERVAL_MINUTES in constants.py
    async def check_forge_completions(self):
        """Periodically checks for completed forge items and sends combined notifications."""
        # Add print statement for the start of the check
        print(f"\n--- Running Forge Notification Check ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
        logger.debug(f"Forge completion check task started. Configured interval: {FORGE_CHECK_INTERVAL_MINUTES} minutes.")


        if not self.hypixel_api_key or not self.webhook_url:
            if not self.hypixel_api_key: logger.warning("Notification Task: Hypixel API key missing. Skipping check.")
            if not self.webhook_url: logger.warning("Notification Task: Webhook URL missing. Skipping check.")
            logger.debug("Notification check task skipped due to missing keys.")
            # Print next check time even if skipped
            print(f"--- Next Forge Notification Check in {FORGE_CHECK_INTERVAL_MINUTES} minutes ---")
            return

        await self.bot.wait_until_ready()
        logger.debug("Bot is ready for notification task.")

        current_time_ms = time.time() * 1000
        # Load registrations fresh each time
        self.registrations = self.load_registrations()
        # Clean up expired clock entries before checking forge (relies on forge_cog_ref)
        if self.forge_cog_ref and hasattr(self.forge_cog_ref, 'cleanup_expired_clock_entries'):
             self.forge_cog_ref.cleanup_expired_clock_entries()
        else:
             logger.warning("ForgeCog reference or cleanup_expired_clock_entries method missing. Clock cleanup skipped during notification task.")

        # Clean up history before checking
        self.cleanup_history()


        logger.debug(f"Reloaded registrations ({len(self.registrations)} users) for check.")

        if not self.registrations:
             logger.debug("No registrations found. Skipping notification check.")
             print("No users registered for notifications.")
             # Print next check time even if no users are registered
             print(f"--- Next Forge Notification Check in {FORGE_CHECK_INTERVAL_MINUTES} minutes ---")
             return

        # Dictionary to store items ready *now* for notification, keyed by discord_user_id_str
        items_ready_now_for_notification = {} # Items that are READY and will trigger a notification

        # Dictionary to store details of all active forge items for the "Next Potential Notifications" output, grouped by user
        user_active_forge_items = defaultdict(list)


        for discord_user_id_str, user_accounts in list(self.registrations.items()):
            logger.debug(f"Processing accounts for Discord user ID: {discord_user_id_str}")
            if not user_accounts:
                 logger.debug(f"No accounts registered for user {discord_user_id_str}. Skipping.")
                 continue

            try:
                discord_user_id = int(discord_user_id_str)
                mention_string = f"<@{discord_user_id}>" # Needed for potential webhook
                 # Get a representative Minecraft username for the console output (using the first account)
                representative_mc_uuid = user_accounts[0].get('uuid')
                representative_mc_username = "Unknown User"
                if representative_mc_uuid:
                     name_lookup_result = uuid_to_username(representative_mc_uuid)
                     if name_lookup_result:
                          representative_mc_username = name_lookup_result
                     else:
                          representative_mc_username = f"UUID: {representative_mc_uuid[:8]}..."
                logger.debug(f"Representative username for user {discord_user_id_str}: {representative_mc_username}")


            except ValueError:
                logger.error(f"Notification Task: Invalid Discord User ID in registrations: {discord_user_id_str}. Skipping user.")
                continue

            user_ready_items_now = [] # Items ready *right now* for this user across all profiles


            for account in list(user_accounts):
                mc_uuid = account.get('uuid')
                logger.debug(f"Checking account with UUID: {mc_uuid} for user {discord_user_id_str}")
                if not mc_uuid:
                    logger.warning(f"Account missing UUID for user {discord_user_id_str}. Skipping account.")
                    continue

                uuid_dashed = format_uuid(mc_uuid)
                profiles_data_full = get_player_profiles(self.hypixel_api_key, uuid_dashed)

                if not profiles_data_full or not profiles_data_full.get("success", False):
                    logger.error(f"Notification Task: Could not retrieve profiles for {mc_uuid} for user {discord_user_id_str}")
                    continue

                profiles = profiles_data_full.get("profiles", [])
                if not profiles:
                    logger.debug(f"No Skyblock profiles found for UUID {mc_uuid}.")
                    continue

                for profile in list(profiles):
                    profile_cute_name = profile.get("cute_name", "Unknown Profile")
                    profile_internal_id = profile.get("profile_id")
                    logger.debug(f"Checking profile '{profile_cute_name}' ({profile_internal_id}) for UUID {mc_uuid}")

                    if profile_internal_id is None:
                         logger.warning(f"Skipping profile '{profile_cute_name}' with missing internal ID for UUID {mc_uuid}.")
                         continue

                    member_data = profile.get("members", {}).get(mc_uuid, {})
                    forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})

                    forge_time_level = member_data.get("mining_core", {}).get("nodes", {}).get("forge_time")
                    time_reduction_percent = calculate_quick_forge_reduction(forge_time_level)

                    # Use the forge_cog_ref to check clock usage
                    clock_is_active = False
                    if self.forge_cog_ref and hasattr(self.forge_cog_ref, 'is_clock_used'):
                         clock_is_active = self.forge_cog_ref.is_clock_used(mc_uuid, profile_internal_id)
                    else:
                         logger.warning("ForgeCog reference or is_clock_used method missing. Cannot check clock usage for notifications.")

                    logger.debug(f"Profile '{profile_cute_name}': Quick Forge Reduction: {time_reduction_percent}%, Clock Active: {clock_is_active}")


                    # Iterate through forge items to find earliest completion and items ready now
                    for forge_type_key, slots_data in (forge_processes_data or {}).items():
                        if isinstance(slots_data, dict):
                            for slot_key, item_api_data in (slots_data or {}).items():
                                if isinstance(item_api_data, dict) and item_api_data.get("startTime") is not None and item_api_data.get("id") is not None:
                                    item_id_api = item_api_data.get("id")
                                    start_time_ms_api = item_api_data.get("startTime")

                                    forge_item_details = self.forge_items_data.get(item_id_api)

                                    if not isinstance(forge_item_details, dict) or forge_item_details.get("duration") is None or not isinstance(forge_item_details.get("duration"), (int, float)):
                                        if item_id_api != "Unknown Item":
                                            logger.warning(f"Notification Task: Skipping item {item_id_api} in {profile_cute_name} due to missing duration in forge_items.json.")
                                        continue

                                    item_name_display = forge_item_details.get("name", item_id_api)
                                    base_duration_ms = forge_item_details["duration"]

                                    effective_duration_ms = base_duration_ms * (1 - time_reduction_percent / 100)

                                    # Calculate end time with Quick Forge AND Enchanted Clock
                                    adjusted_end_time_ms = start_time_ms_api + effective_duration_ms
                                    if clock_is_active:
                                         adjusted_end_time_ms = start_time_ms_api + max(0, effective_duration_ms - ENCHANTED_CLOCK_REDUCTION_MS)

                                    logger.debug(f"Item {item_name_display} (Start: {start_time_ms_api}) in {profile_cute_name}: Effective Duration (Quick Forge): {effective_duration_ms}, Adjusted End Time (with buffs): {adjusted_end_time_ms}, Current Time: {current_time_ms}")


                                    # Identifier for history and "ready now" check
                                    item_identifier = (discord_user_id_str, profile_internal_id, start_time_ms_api, adjusted_end_time_ms)

                                    # Check if this item is ready NOW for notification AND hasn't been notified before
                                    if current_time_ms >= adjusted_end_time_ms:
                                        if item_identifier not in self.notified_items_history:
                                            logger.info(
                                                f"Item '{item_name_display}' (Start: {start_time_ms_api}) in profile '{profile_cute_name}' ({mc_uuid}) ready for user {discord_user_id_str}. Adding to combined list for notification.")

                                            user_ready_items_now.append({
                                                "profile_name": profile_cute_name,
                                                "profile_internal_id": profile_internal_id, # Added for history identifier
                                                "item_name": item_name_display,
                                                "slot_type": forge_type_key,
                                                "slot_number": slot_key,
                                                "start_time_ms": start_time_ms_api,
                                                "adjusted_end_time_ms": adjusted_end_time_ms,
                                                "item_id": item_id_api
                                            })
                                        else:
                                            logger.debug(f"Item '{item_name_display}' (Start: {start_time_ms_api}) in profile '{profile_cute_name}' ({mc_uuid}) already notified for user {discord_user_id_str}. Skipping notification.")

                                    # Check if this item is a FUTURE item for "Next Potential Notifications" list
                                    elif adjusted_end_time_ms > current_time_ms:
                                         user_active_forge_items[(discord_user_id_str, representative_mc_username)].append({
                                             "profile_name": profile_cute_name,
                                             "item_name": item_name_display,
                                             "estimated_completion_time_ms": adjusted_end_time_ms
                                         })
                                         logger.debug(f"Found active future item: {item_name_display} in {profile_cute_name} for user {discord_user_id_str}. Estimated completion: {adjusted_end_time_ms}")


            if user_ready_items_now:
                # Store items ready *now* for this user to send a combined notification later
                items_ready_now_for_notification[discord_user_id_str] = {
                    "ready_items": user_ready_items_now,
                    "mention_string": mention_string,
                    "discord_user_id_str": discord_user_id_str
                }
                logger.debug(f"Found {len(user_ready_items_now)} items ready now for user {discord_user_id_str}. Will send notification.")


        # --- Print Next Potential Forge Notifications to Console (Compacted) ---
        logger.info("\n--- Next Potential Forge Notifications ---")
        if not user_active_forge_items:
            logger.info("No users have active forge items.")
        else:
            # Sort users by their Discord ID (or another stable key if preferred)
            sorted_users_with_active_items = sorted(user_active_forge_items.keys())

            for user_key in sorted_users_with_active_items:
                 user_id_str, username = user_key
                 items = user_active_forge_items[user_key]

                 logger.info(f"User {user_id_str} ({username}):")

                 # Sort items for this user by estimated completion time
                 items.sort(key=lambda item: item['estimated_completion_time_ms'])

                 # Compact items for display
                 compacted_items_display = defaultdict(int)
                 for item in items:
                     remaining_time_ms = item['estimated_completion_time_ms'] - current_time_ms
                     # Use floor to group items finishing within the same minute
                     remaining_time_formatted = format_time_difference(max(0, remaining_time_ms))

                     # Create a display string as the key for compaction
                     display_key = f"{item['item_name']} on {item['profile_name']} (Ready in {remaining_time_formatted})"
                     compacted_items_display[display_key] += 1

                 # Print compacted items
                 for display_string, count in compacted_items_display.items():
                     if count > 1:
                         logger.info(f"  - x{count} {display_string}")
                     else:
                         logger.info(f"  - {display_string}")


        # --- Send Notifications for Items Ready NOW ---
        if items_ready_now_for_notification:
            logger.info(f"Processing notifications for {len(items_ready_now_for_notification)} users with ready items.")
            for discord_user_id_str, notification_data in items_ready_now_for_notification.items():
                ready_items = notification_data["ready_items"]
                mention_string = notification_data["mention_string"]

                if ready_items:
                     ready_items.sort(key=lambda x: (x['profile_name'], int(x['slot_number']) if str(x['slot_number']).isdigit() else str(x['slot_number'])))

                     message_lines = [f"{mention_string}\n"]
                     message_lines.append("Your forge items are ready:")

                     for item_info in ready_items:
                         ready_timestamp_unix = int(item_info["adjusted_end_time_ms"] / 1000)
                         started_timestamp_unix = int(item_info["start_time_ms"] / 1000)

                         # Use Relative Timestamp format for "Ready since" and "Started"
                         # This requires Discord Client to interpret
                         ready_since_discord_format = f"<t:{ready_timestamp_unix}:R>"
                         started_ago_discord_format = f"<t:{started_timestamp_unix}:R>"

                         message_lines.append(
                             f"- Your **{item_info['item_name']}** on {item_info['profile_name']} was ready {ready_since_discord_format} (started {started_ago_discord_format})"
                         )

                     combined_message = "\n".join(message_lines)
                     logger.debug(f"Combined notification message for user {discord_user_id_str}:\n{combined_message}")

                     # Pass ready_items to send_forge_webhook for history update
                     combined_notification_data = {
                         "message": combined_message,
                         "discord_user_id": discord_user_id_str,
                         "discord_user_id_str": discord_user_id_str,
                         "ready_items_sent": ready_items # Pass the list of items being sent
                     }

                     await self.send_forge_webhook(combined_notification_data)
                     # History update and save now happen inside send_forge_webhook upon success
                else:
                     logger.debug(f"No ready items found for user {discord_user_id_str} after filtering (might be due to history). Skipping notification.")


        # Print the next check time at the very end
        print(f"\n--- Forge Notification Check finished. Next check in {FORGE_CHECK_INTERVAL_MINUTES} minutes ---")
        logger.debug("Forge completion check task finished.")


    @check_forge_completions.before_loop
    async def before_check_forge_completions(self):
        """Ensures the bot is ready before starting the loop."""
        logger.debug("Waiting for bot to be ready before starting forge completion check loop.")
        await self.bot.wait_until_ready()
        logger.debug("Bot is ready. Starting forge completion check loop.")

    def start_notifications_task(self):
        """Starts the periodic notification check task."""
        logger.info("Starting periodic forge completion check task from manager.")
        # Correct way to check if the task is running and start it
        if not self.check_forge_completions.is_running():
             self.check_forge_completions.start() # Start the loop via the method
             logger.info(f"Forge notification task started with interval {FORGE_CHECK_INTERVAL_MINUTES} minutes.")
        else:
             logger.info("Notification task is already running.")

    def stop_notifications_task(self):
        """Stops the periodic notification check task."""
        logger.info("Stopping periodic forge completion check task.")
        # Correct way to cancel the task
        if self.check_forge_completions.is_running():
             self.check_forge_completions.cancel() # Cancel the loop via the method
             logger.info("Notification task cancelled.")
        else:
             logger.info("Notification task was not running.")