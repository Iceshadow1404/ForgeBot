import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import time
import json
import asyncio
import requests

# Import necessary functions from skyblock.py
from skyblock import get_uuid, format_uuid, get_player_profiles, find_profile_by_name, uuid_to_username

# Define file paths for persistent storage
REGISTRATION_FILE = 'registrations.json'
CLOCK_USAGE_FILE = 'clock_usage.json'
NOTIFICATIONS_FILE = 'forge_notifications.json'

# Define a constant for the Enchanted Clock reduction (1 hour in milliseconds)
ENCHANTED_CLOCK_REDUCTION_MS = 60 * 60 * 1000

# --- Helper Functions ---

def format_time_difference(milliseconds: float) -> str:
    """
    Formats a time difference in milliseconds into a human-readable string.
    Ignores seconds if duration is 1 hour or more.
    """
    if milliseconds <= 0:
        return "Finished"

    total_seconds = int(milliseconds // 1000)

    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)

    parts = []
    if days > 0:
        parts.append(f"{days}d")

    if milliseconds >= 3_600_000:
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
    else:
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if seconds > 0 or not parts:
            parts.append(f"{seconds}s")

    if not parts and milliseconds > 0:
        return "<1s"
    elif not parts and milliseconds <= 0:
        return "Finished"

    return " ".join(parts)


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
        print(f"Warning: Unexpected forge_time_level: {level}. Returning 0%.")
        return 0.0


def format_active_forge_items(forge_processes_data: dict, forge_items_config: dict, time_reduction_percent: float,
                              clock_is_actively_buffing: bool) -> list[str]:
    """
    Formats the active forge items with remaining times, applying buffs.
    Returns a list of formatted strings, one for each active item.
    """
    forge_items_output = []
    current_time_ms = time.time() * 1000

    if not isinstance(forge_processes_data, dict) or not forge_processes_data:
        return []

    for forge_type_key in sorted(forge_processes_data.keys()):
        slots_data = forge_processes_data.get(forge_type_key)

        if not isinstance(slots_data, dict):
            continue

        sorted_slots = sorted(slots_data.keys(), key=lambda x: int(x) if str(x).isdigit() else float('inf'))

        for slot in sorted_slots:
            item_data = slots_data.get(slot)

            if not isinstance(item_data, dict) or item_data.get("startTime") is None:
                continue

            item_id = item_data.get("id", "Unknown Item")
            start_time_ms = item_data.get("startTime")

            item_name = item_id
            remaining_time_str = "Time unknown"

            forge_item_info = forge_items_config.get(item_id)

            if forge_item_info and start_time_ms is not None:
                item_name = forge_item_info.get("name", item_id)
                base_duration_ms = forge_item_info.get("duration")

                if base_duration_ms is not None and isinstance(base_duration_ms, (int, float)):
                    effective_duration_ms = base_duration_ms * (1 - time_reduction_percent / 100)
                    end_time_ms = start_time_ms + effective_duration_ms
                    remaining_time_ms = end_time_ms - current_time_ms

                    if clock_is_actively_buffing:
                        remaining_time_ms = max(0, remaining_time_ms - ENCHANTED_CLOCK_REDUCTION_MS)

                    remaining_time_str = format_time_difference(remaining_time_ms)
                else:
                    remaining_time_str = "Duration unknown (JSON)"

            elif start_time_ms is None:
                remaining_time_str = "Start time unknown (API)"
            else:
                remaining_time_str = "Duration unknown (Item data missing)"

            forge_items_output.append(
                f"Slot {slot}: {item_name} - Remaining: {remaining_time_str}")

    return forge_items_output


def create_forge_embed(profile_data: dict, formatted_items_string: str, page_number: int | None = None,
                       total_pages: int | None = None) -> discord.Embed:
    """Creates a discord.Embed for a single profile's active forge items."""
    items_description = formatted_items_string if formatted_items_string else "No active items in Forge slots."

    embed = discord.Embed(
        title=f"Forge Items for {profile_data.get('username', 'Unknown User')} on {profile_data.get('profile_name', 'Unknown Profile')}",
        description=items_description,
        color=discord.Color.blue()
    )

    if profile_data.get('perk_message'):
        embed.add_field(name="Perk", value=profile_data['perk_message'].strip(), inline=False)

    if page_number is not None and total_pages is not None:
        embed.set_footer(text=f"Profile {page_number + 1}/{total_pages}")
    return embed


# --- Discord UI Views ---

class ForgePaginationView(discord.ui.View):
    """Handles pagination for multiple forge profiles."""
    def __init__(self, forge_data_list: list, interaction: discord.Interaction, forge_items_config: dict,
                 clock_usage_cog_ref, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.forge_data_list = forge_data_list
        self.current_page = 0
        self.interaction = interaction
        self.forge_items_config = forge_items_config
        self.clock_usage_cog_ref = clock_usage_cog_ref

        self.embeds = [
            create_forge_embed(data, data.get("formatted_items"), i, len(self.forge_data_list))
            for i, data in enumerate(self.forge_data_list)
        ]

        if len(self.embeds) <= 1:
            for item in self.children:
                if isinstance(item, discord.ui.Button) and hasattr(item, 'label') and item.label in ["Prev", "Next"]:
                    item.disabled = True

        self.update_buttons()

    def update_buttons(self):
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == len(self.embeds) - 1
        self.update_clock_button_state()

    def update_clock_button_state(self):
        current_profile_data = self.forge_data_list[self.current_page]
        profile_internal_id = current_profile_data.get("profile_id")
        profile_uuid = current_profile_data.get("uuid")

        if profile_internal_id is None or profile_uuid is None:
            self.enchanted_clock_button.disabled = True
            return

        raw_forge_processes = current_profile_data.get("items_raw", {})
        has_active_items = any(
            isinstance(slots_data, dict) and isinstance(slot_data, dict) and slot_data.get("startTime") is not None
            for forge_type_key, slots_data in (raw_forge_processes or {}).items()
            for slot_data in (slots_data or {}).values()
        )

        self.enchanted_clock_button.disabled = not has_active_items or self.clock_usage_cog_ref.is_clock_used(
            profile_uuid, profile_internal_id)


    @discord.ui.button(label="Prev", style=discord.ButtonStyle.blurple)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("You can only interact with your own forge view.", ephemeral=True)
            return

        await interaction.response.defer()

        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            await self.interaction.edit_original_response(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("You can only interact with your own forge view.", ephemeral=True)
            return

        await interaction.response.defer()

        if self.current_page < len(self.embeds) - 1:
            self.current_page += 1
            self.update_buttons()
            await self.interaction.edit_original_response(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(label="Enchanted Clock", style=discord.ButtonStyle.green)
    async def enchanted_clock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("You can only interact with your own forge view.", ephemeral=True)
            return

        await interaction.response.defer()

        current_profile_index = self.current_page
        profile_data = self.forge_data_list[current_profile_index]
        profile_internal_id = profile_data.get("profile_id")
        profile_uuid = profile_data.get("uuid")

        if profile_internal_id is None or profile_uuid is None:
            await interaction.followup.send("Could not identify profile for clock application.", ephemeral=True)
            return

        if self.clock_usage_cog_ref.is_clock_used(profile_uuid, profile_internal_id):
            await interaction.followup.send("The Enchanted Clock has already been used for this profile today.",
                                            ephemeral=True)
            return

        raw_forge_processes = profile_data.get("items_raw")
        time_reduction_percent = profile_data.get("time_reduction_percent", 0.0)

        has_active_items_now = any(
            isinstance(slots_data, dict) and isinstance(slot_data, dict) and slot_data.get("startTime") is not None
            for forge_type_key, slots_data in (raw_forge_processes or {}).items()
            for slot_data in (slots_data or {}).values()
        )

        if not has_active_items_now:
            await interaction.followup.send("No active items in the Forge for this profile to apply the clock to.",
                                            ephemeral=True)
            return

        self.clock_usage_cog_ref.mark_clock_used(profile_uuid, profile_internal_id,
                                                 profile_data.get("profile_name", "Unknown Profile"))

        updated_formatted_items = []
        current_time_ms = time.time() * 1000

        for forge_type_key in sorted((raw_forge_processes or {}).keys()):
            slots_data = (raw_forge_processes or {}).get(forge_type_key, {})
            if not isinstance(slots_data, dict): continue

            sorted_slots = sorted(slots_data.keys(), key=lambda x: int(x) if str(x).isdigit() else float('inf'))

            for slot in sorted_slots:
                item_data = slots_data.get(slot)
                if not isinstance(item_data, dict) or item_data.get("startTime") is None:
                    continue

                item_id = item_data.get("id", "Unknown Item")
                start_time_ms = item_data.get("startTime")
                item_name = item_id
                remaining_time_str = "Time unknown"

                forge_item_info = self.forge_items_config.get(item_id)

                if forge_item_info and start_time_ms is not None:
                    item_name = forge_item_info.get("name", item_id)
                    base_duration_ms = forge_item_info.get("duration")

                    if base_duration_ms is not None and isinstance(base_duration_ms, (int, float)):
                        effective_duration_ms = base_duration_ms * (1 - time_reduction_percent / 100)
                        end_time_ms = start_time_ms + effective_duration_ms
                        remaining_time_ms = end_time_ms - current_time_ms
                        remaining_time_ms = max(0, remaining_time_ms - ENCHANTED_CLOCK_REDUCTION_MS)
                        remaining_time_str = format_time_difference(remaining_time_ms)
                    else:
                        remaining_time_str = "Duration unknown (JSON)"
                elif start_time_ms is None:
                    remaining_time_str = "Start time unknown (API)"
                else:
                    remaining_time_str = "Duration unknown (Item data missing)"

                updated_formatted_items.append(
                    f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Remaining: {remaining_time_str}")

        formatted_items_string = "\n".join(updated_formatted_items)
        self.forge_data_list[current_profile_index]["formatted_items"] = formatted_items_string

        self.embeds[current_profile_index] = create_forge_embed(
            self.forge_data_list[current_profile_index],
            formatted_items_string,
            current_profile_index,
            len(self.forge_data_list)
        )
        clock_note = "\n*Enchanted Clock buff applied.*"
        self.embeds[current_profile_index].description = (self.embeds[current_profile_index].description or "") + clock_note


        self.update_clock_button_state()
        await self.interaction.edit_original_response(embed=self.embeds[self.current_page], view=self)

    async def on_timeout(self):
        """Disables all items in the view when the timeout occurs."""
        for item in self.children:
            item.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"Error updating view on timeout: {e}")


class SingleForgeView(discord.ui.View):
    """Handles displaying a single profile's forge data."""
    def __init__(self, profile_data: dict, interaction: discord.Interaction, forge_items_config: dict,
                 clock_usage_cog_ref, formatted_items_string: str, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.profile_data = profile_data
        self.interaction = interaction
        self.forge_items_config = forge_items_config
        self.clock_usage_cog_ref = clock_usage_cog_ref
        self.formatted_items = formatted_items_string
        self.update_clock_button_state()

    def update_clock_button_state(self):
        has_active_items = False
        raw_forge_processes = self.profile_data.get("items_raw", {})

        if isinstance(raw_forge_processes, dict) and raw_forge_processes:
            for forge_type_key, slots_data in raw_forge_processes.items():
                 if isinstance(slots_data, dict):
                    for slot_data in (slots_data or {}).values():
                        if isinstance(slot_data, dict) and slot_data.get("startTime") is not None:
                            has_active_items = True
                            break
                    if has_active_items: break

        profile_internal_id = self.profile_data.get("profile_id")
        profile_uuid = self.profile_data.get("uuid")

        self.enchanted_clock_button.disabled = (
            not has_active_items or
            profile_internal_id is None or
            profile_uuid is None or
            self.clock_usage_cog_ref.is_clock_used(profile_uuid, profile_internal_id)
        )


    @discord.ui.button(label="Enchanted Clock", style=discord.ButtonStyle.green)
    async def enchanted_clock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("You can only interact with your own forge view.", ephemeral=True)
            return

        await interaction.response.defer()

        profile_data = self.profile_data
        profile_internal_id = profile_data.get("profile_id")
        profile_uuid = profile_data.get("uuid")

        if profile_internal_id is None or profile_uuid is None:
            await interaction.followup.send("Could not identify profile for clock application.", ephemeral=True)
            return

        if self.clock_usage_cog_ref.is_clock_used(profile_uuid, profile_internal_id):
            await interaction.followup.send("The Enchanted Clock has already been used for this profile today.",
                                            ephemeral=True)
            return

        raw_forge_processes = profile_data.get("items_raw")
        time_reduction_percent = profile_data.get("time_reduction_percent", 0.0)

        has_active_items_now = any(
            isinstance(slots_data, dict) and isinstance(slot_data, dict) and slot_data.get("startTime") is not None
            for forge_type_key, slots_data in (raw_forge_processes or {}).items()
            for slot_data in (slots_data or {}).values()
        )

        if not has_active_items_now:
            await interaction.followup.send("No active items in the Forge for this profile to apply the clock to.",
                                            ephemeral=True)
            return

        self.clock_usage_cog_ref.mark_clock_used(profile_uuid, profile_internal_id,
                                                 profile_data.get("profile_name", "Unknown Profile"))

        current_time_ms = time.time() * 1000
        updated_formatted_items = []

        for forge_type_key in sorted((raw_forge_processes or {}).keys()):
            slots_data = (raw_forge_processes or {}).get(forge_type_key, {})
            if not isinstance(slots_data, dict): continue

            sorted_slots = sorted(slots_data.keys(), key=lambda x: int(x) if str(x).isdigit() else float('inf'))
            for slot in sorted_slots:
                item_data = slots_data.get(slot)
                if not isinstance(item_data, dict) or item_data.get("startTime") is None:
                    continue

                item_id = item_data.get("id", "Unknown Item")
                start_time_ms = item_data.get("startTime")
                item_name = item_id
                remaining_time_str = "Time unknown"

                forge_item_info = self.forge_items_config.get(item_id)

                if forge_item_info and start_time_ms is not None:
                    item_name = forge_item_info.get("name", item_id)
                    base_duration_ms = forge_item_info.get("duration")

                    if base_duration_ms is not None and isinstance(base_duration_ms, (int, float)):
                        effective_duration_ms = base_duration_ms * (1 - time_reduction_percent / 100)
                        end_time_ms = start_time_ms + effective_duration_ms
                        remaining_time_ms = end_time_ms - current_time_ms
                        remaining_time_ms = max(0, remaining_time_ms - ENCHANTED_CLOCK_REDUCTION_MS)
                        remaining_time_str = format_time_difference(remaining_time_ms)
                    else:
                        remaining_time_str = "Duration unknown (JSON)"
                elif start_time_ms is None:
                    remaining_time_str = "Start time unknown (API)"
                else:
                    remaining_time_str = "Duration unknown (Item data missing)"

                updated_formatted_items.append(
                    f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Remaining: {remaining_time_str}")

        self.formatted_items = "\n".join(updated_formatted_items)

        embed = create_forge_embed(
            profile_data,
            self.formatted_items,
            None, None
        )
        clock_note = "\n*Enchanted Clock buff applied.*"
        embed.description = (embed.description or "") + clock_note

        self.update_clock_button_state()
        await self.interaction.edit_original_response(embed=embed, view=self)

    async def on_timeout(self):
        """Disables all items in the view when the timeout occurs."""
        for item in self.children:
            item.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"Error updating view on timeout: {e}")


# --- Main Cog Class ---

class ForgeCog(commands.Cog, name="Forge Functions"):
    """
    A Discord Bot Cog for Skyblock Forge related commands and notifications.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.hypixel_api_key = os.getenv("HYPIXEL_API_KEY")
        if not self.hypixel_api_key:
            print("WARNING: HYPIXEL_API_KEY not found. Forge commands requiring API access will not work.")

        self.forge_items_data = self.load_forge_items_data()
        self.registrations = self.load_registrations()
        self.clock_usage = self.load_clock_usage()

        # --- Notifications Setup ---
        self.webhook_url = os.getenv("WEBHOOK_URL")
        if not self.webhook_url:
            print("WARNING: WEBHOOK_URL not found. Forge notifications will not be sent.")
        self.notifications_status = self.load_notifications_status()
        self.check_forge_completions.start()
        # --- End Notifications Setup ---

    # --- Data Loading and Saving ---

    def load_forge_items_data(self) -> dict:
        """Loads forge item configuration data from 'forge_items.json'."""
        try:
            with open('forge_items.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
            print("forge_items.json loaded successfully.")
            return data
        except FileNotFoundError:
            print("WARNING: forge_items.json not found. Forge duration calculation may be inaccurate.")
            return {}
        except json.JSONDecodeError:
            print("ERROR: Could not decode forge_items.json. Check the file for syntax errors.")
            return {}
        except Exception as e:
            print(f"An unexpected error occurred loading forge_items.json: {e}")
            return {}

    def load_registrations(self) -> dict:
        """Loads user registration data from REGISTRATION_FILE."""
        if not os.path.exists(REGISTRATION_FILE):
            return {}
        try:
            with open(REGISTRATION_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            cleaned_data = {}
            for user_id, accounts in data.items():
                if isinstance(user_id, str) and isinstance(accounts, list):
                    cleaned_accounts = []
                    for account in accounts:
                        if isinstance(account, dict) and account.get('uuid') is not None:
                            cleaned_accounts.append(account)
                        else:
                            print(f"Warning: Invalid account entry found for user {user_id}: {account}. Skipping.")
                    if cleaned_accounts:
                        cleaned_data[user_id] = cleaned_accounts
                else:
                     print(f"Warning: Invalid registration format for user {user_id}: {accounts}. Skipping.")

            return cleaned_data
        except (json.JSONDecodeError, Exception) as e:
            print(f"ERROR: Could not load {REGISTRATION_FILE}: {e}. Assuming empty registrations.")
            return {}

    def load_clock_usage(self) -> dict:
        """Loads Enchanted Clock usage tracking data from CLOCK_USAGE_FILE."""
        if not os.path.exists(CLOCK_USAGE_FILE):
            print(f"Clock usage file not found: {CLOCK_USAGE_FILE}. Starting with empty data.")
            return {}
        try:
            with open(CLOCK_USAGE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, dict):
                print(f"Warning: {CLOCK_USAGE_FILE} is not a valid dictionary. Starting fresh.")
                return {}

            cleaned_data = {}
            for uuid, profiles in data.items():
                if isinstance(uuid, str) and isinstance(profiles, dict):
                    cleaned_profiles = {}
                    for profile_id, p_data in profiles.items():
                        if (isinstance(profile_id, str) and isinstance(p_data, dict) and
                            p_data.get("end_timestamp") is not None and isinstance(p_data.get("end_timestamp"), (int, float)) and
                            p_data.get("profile_name") is not None and isinstance(p_data.get("profile_name"), str)):
                            cleaned_profiles[profile_id] = p_data
                        else:
                             print(f"Warning: Invalid clock usage entry found for UUID {uuid}, Profile ID {profile_id}. Skipping.")

                    if cleaned_profiles:
                        cleaned_data[uuid] = cleaned_profiles
                else:
                     print(f"Warning: Invalid clock usage entry found for key {uuid}. Skipping.")

            return cleaned_data
        except Exception as e:
            print(f"An unexpected error occurred loading {CLOCK_USAGE_FILE}: {e}")
            return {}

    def save_clock_usage(self):
        """Saves the current Enchanted Clock usage tracking data."""
        try:
            os.makedirs(os.path.dirname(CLOCK_USAGE_FILE) or '.', exist_ok=True)
            temp_file = CLOCK_USAGE_FILE + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self.clock_usage, f, indent=4)
            os.replace(temp_file, CLOCK_USAGE_FILE)
        except Exception as e:
            print(f"ERROR: Could not save {CLOCK_USAGE_FILE}: {e}")

    # --- Clock Usage Logic ---

    def is_clock_used(self, uuid: str, profile_internal_id: str) -> bool:
        """Checks if the Enchanted Clock buff is active for a profile."""
        profile_data = self.clock_usage.get(uuid, {}).get(profile_internal_id)
        if isinstance(profile_data, dict) and profile_data.get("end_timestamp") is not None and isinstance(profile_data.get("end_timestamp"), (int, float)):
             return time.time() * 1000 < profile_data["end_timestamp"]
        return False

    def mark_clock_used(self, uuid: str, profile_internal_id: str, profile_cute_name: str):
        """Marks the Enchanted Clock as used for a profile."""
        current_time_ms = time.time() * 1000
        end_timestamp = current_time_ms + ENCHANTED_CLOCK_REDUCTION_MS
        if uuid not in self.clock_usage:
            self.clock_usage[uuid] = {}
        self.clock_usage[uuid][profile_internal_id] = {
            "profile_name": profile_cute_name,
            "end_timestamp": end_timestamp
        }
        self.save_clock_usage()

    def reset_clock_usage(self, uuid: str, profile_internal_id: str):
        """Resets the Enchanted Clock usage status for a profile."""
        if uuid in self.clock_usage and profile_internal_id in self.clock_usage.get(uuid, {}):
            del self.clock_usage[uuid][profile_internal_id]
            if not self.clock_usage[uuid]:
                del self.clock_usage[uuid]
            self.save_clock_usage()

    def cleanup_expired_clock_entries(self):
        """Removes expired and invalid clock usage entries."""
        current_time_ms = time.time() * 1000
        modified = False

        for uuid in list(self.clock_usage.keys()):
            profiles = self.clock_usage.get(uuid)

            if not isinstance(profiles, dict):
                print(f"Warning: Found invalid clock usage data for UUID {uuid}. Removing entry.")
                if uuid in self.clock_usage:
                    del self.clock_usage[uuid]
                    modified = True
                continue

            profile_ids_to_delete = []
            for profile_id, pdata in profiles.items():
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
                    if is_expired:
                         print(f"Cleaning up expired clock entry for profile '{pdata.get('profile_name', 'Unknown')}' ({profile_id}) for UUID {uuid}.")


            for profile_id in profile_ids_to_delete:
                if profile_id in profiles:
                    del profiles[profile_id]
                    modified = True

            if not profiles and uuid in self.clock_usage:
                del self.clock_usage[uuid]
                modified = True

        if modified:
            self.save_clock_usage()

    # --- Notification Logic ---

    def load_notifications_status(self) -> dict:
        """Loads forge notification status from NOTIFICATIONS_FILE."""
        if not os.path.exists(NOTIFICATIONS_FILE):
            print(f"Notification status file not found: {NOTIFICATIONS_FILE}. Starting with empty status.")
            return {}
        try:
            data = json.load(open(NOTIFICATIONS_FILE, 'r', encoding='utf-8'))
            if not isinstance(data, dict):
                print(f"Warning: {NOTIFICATIONS_FILE} is not a valid dictionary. Starting fresh.")
                return {}
            return data
        except (json.JSONDecodeError, Exception) as e:
            print(f"ERROR: Could not load {NOTIFICATIONS_FILE}: {e}. Assuming empty status.")
            return {}

    def save_notifications_status(self):
        """Saves forge notification status to NOTIFICATIONS_FILE."""
        try:
            os.makedirs(os.path.dirname(NOTIFICATIONS_FILE) or '.', exist_ok=True)
            temp_file = NOTIFICATIONS_FILE + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self.notifications_status, f, indent=4)
            os.replace(temp_file, NOTIFICATIONS_FILE)
        except Exception as e:
            print(f"ERROR: Could not save {NOTIFICATIONS_FILE}: {e}")

    async def send_forge_webhook(self, notification_data: dict):
        """Sends a combined notification to the configured webhook URL."""
        if not self.webhook_url:
            print("Webhook URL not configured. Skipping notification.")
            return

        message_content = notification_data.get("message", "A forge item is ready!")
        discord_user_id = notification_data.get("discord_user_id")

        webhook_payload = {
            "content": message_content,
            "allowed_mentions": {
                "parse": ["users"],
                 "replied_user": False
            }
        }

        if not webhook_payload.get("content") and not webhook_payload.get("embeds"):
            print(f"Warning: Webhook payload is empty for user {discord_user_id}. Not sending.")
            return

        headers = {'Content-Type': 'application/json'}

        try:
            response = await asyncio.to_thread(
                requests.post,
                self.webhook_url,
                json=webhook_payload,
                headers=headers,
                timeout=10
            )

            if 200 <= response.status_code < 300:
                print(f"Successfully sent combined webhook notification for user {discord_user_id}.")
            else:
                print(f"Error sending combined webhook for user {discord_user_id}: {response.status_code} - {response.text}")

        except requests.exceptions.Timeout:
            print(f"Timeout error sending combined webhook for user {discord_user_id}.")
        except requests.exceptions.RequestException as e:
            print(f"Request exception sending combined webhook for user {discord_user_id}: {e}")
        except Exception as e:
            print(f"Unexpected exception sending combined webhook for user {discord_user_id}: {e}")


    @tasks.loop(minutes=5)
    async def check_forge_completions(self):
        """Periodically checks for completed forge items and sends combined notifications."""
        if not self.hypixel_api_key or not self.webhook_url:
            if not self.hypixel_api_key: print("Notification Task: Hypixel API key missing.")
            if not self.webhook_url: print("Notification Task: Webhook URL missing.")
            return

        await self.bot.wait_until_ready()

        current_time_ms = time.time() * 1000
        self.registrations = self.load_registrations()
        self.notifications_status = self.load_notifications_status()

        if not self.registrations:
             return

        notifications_status_changed = False

        for discord_user_id_str, user_accounts in list(self.registrations.items()):
            if not user_accounts: continue

            try:
                discord_user_id = int(discord_user_id_str)
                mention_string = f"<@{discord_user_id}>"
            except ValueError:
                print(f"Notification Task: Invalid Discord User ID in registrations: {discord_user_id_str}. Skipping user.")
                continue

            all_ready_items_for_user = []

            for account in list(user_accounts):
                mc_uuid = account.get('uuid')
                if not mc_uuid: continue

                uuid_dashed = format_uuid(mc_uuid)
                profiles_data_full = get_player_profiles(self.hypixel_api_key, uuid_dashed)

                if not profiles_data_full or not profiles_data_full.get("success", False):
                    print(f"Notification Task: Could not retrieve profiles for {mc_uuid} for user {discord_user_id_str}")
                    continue

                profiles = profiles_data_full.get("profiles", [])

                for profile in list(profiles):
                    profile_cute_name = profile.get("cute_name", "Unknown Profile")
                    profile_internal_id = profile.get("profile_id")
                    if profile_internal_id is None: continue

                    member_data = profile.get("members", {}).get(mc_uuid, {})
                    forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})

                    forge_time_level = member_data.get("mining_core", {}).get("nodes", {}).get("forge_time")
                    time_reduction_percent = calculate_quick_forge_reduction(forge_time_level)
                    clock_is_active = self.is_clock_used(mc_uuid, profile_internal_id)

                    if discord_user_id_str not in self.notifications_status:
                        self.notifications_status[discord_user_id_str] = {}
                        notifications_status_changed = True
                    if mc_uuid not in self.notifications_status[discord_user_id_str]:
                        self.notifications_status[discord_user_id_str][mc_uuid] = {}
                        notifications_status_changed = True
                    if profile_internal_id not in self.notifications_status[discord_user_id_str][mc_uuid]:
                        self.notifications_status[discord_user_id_str][mc_uuid][profile_internal_id] = {
                            "profile_name": profile_cute_name, "items": []}
                        notifications_status_changed = True

                    profile_notif_data = self.notifications_status[discord_user_id_str][mc_uuid][profile_internal_id]
                    if "items" not in profile_notif_data or not isinstance(profile_notif_data["items"], list):
                         profile_notif_data["items"] = []
                         notifications_status_changed = True


                    current_items_in_api_forge_identifiers = set()
                    for forge_type_key, slots_data in (forge_processes_data or {}).items():
                         if isinstance(slots_data, dict):
                            for slot_key, item_api_data in (slots_data or {}).items():
                                if isinstance(item_api_data, dict) and item_api_data.get("startTime") is not None and item_api_data.get("id") is not None:
                                     identifier = f"{forge_type_key}_{slot_key}_{item_api_data['id']}"
                                     current_items_in_api_forge_identifiers.add(identifier)

                    items_to_keep = []
                    for tracked_item in list(profile_notif_data.get("items", [])):
                         if isinstance(tracked_item, dict) and tracked_item.get("slot_identifier") and tracked_item.get("item_id"):
                             tracked_identifier = f"{tracked_item.get('slot_type', 'unknown')}_{tracked_item.get('slot_number', 'unknown')}_{tracked_item.get('item_id', 'unknown')}"

                             if tracked_identifier in current_items_in_api_forge_identifiers:
                                items_to_keep.append(tracked_item)
                             else:
                                notifications_status_changed = True

                         else:
                             notifications_status_changed = True

                    profile_notif_data["items"] = items_to_keep


                    for forge_type_key, slots_data in (forge_processes_data or {}).items():
                        if isinstance(slots_data, dict):
                            for slot_key, item_api_data in (slots_data or {}).items():
                                if isinstance(item_api_data, dict) and item_api_data.get("startTime") is not None and item_api_data.get("id") is not None:
                                    item_id_api = item_api_data.get("id")
                                    start_time_ms_api = item_api_data.get("startTime")

                                    forge_item_details = self.forge_items_data.get(item_id_api)

                                    if not isinstance(forge_item_details, dict) or forge_item_details.get("duration") is None or not isinstance(forge_item_details.get("duration"), (int, float)):
                                        if item_id_api != "Unknown Item":
                                            print(f"Notification Task: Skipping item {item_id_api} in {profile_cute_name} due to missing duration.")
                                        continue

                                    item_name_display = forge_item_details.get("name", item_id_api)
                                    base_duration_ms = forge_item_details["duration"]

                                    effective_duration_ms = base_duration_ms * (1 - time_reduction_percent / 100)
                                    adjusted_duration_ms = max(0, effective_duration_ms)

                                    adjusted_end_time_ms = start_time_ms_api + adjusted_duration_ms
                                    if clock_is_active:
                                        adjusted_end_time_ms = start_time_ms_api + max(0, effective_duration_ms - ENCHANTED_CLOCK_REDUCTION_MS)


                                    slot_identifier = f"{forge_type_key}_{slot_key}"

                                    already_notified = any(
                                        isinstance(entry, dict) and
                                        entry.get("slot_identifier") == slot_identifier and
                                        entry.get("item_id") == item_id_api and
                                        entry.get("notified", False)
                                        for entry in profile_notif_data.get("items", [])
                                    )

                                    if current_time_ms >= adjusted_end_time_ms and not already_notified:
                                        print(
                                            f"Item '{item_name_display}' in profile '{profile_cute_name}' ({mc_uuid}) ready for user {discord_user_id_str}. Adding to combined list.")

                                        all_ready_items_for_user.append({
                                            "profile_name": profile_cute_name,
                                            "item_name": item_name_display,
                                            "slot_type": forge_type_key,
                                            "slot_number": slot_key,
                                            "start_time_ms": start_time_ms_api,
                                            "adjusted_end_time_ms": adjusted_end_time_ms,
                                            "slot_identifier": slot_identifier,
                                            "item_id": item_id_api
                                        })

                                        found_entry_in_status = next(
                                            (entry for entry in profile_notif_data.get("items", [])
                                             if isinstance(entry, dict) and entry.get("slot_identifier") == slot_identifier and entry.get("item_id") == item_id_api),
                                            None
                                        )

                                        if found_entry_in_status:
                                             found_entry_in_status["notified"] = True
                                             found_entry_in_status["notification_timestamp"] = current_time_ms
                                        else:
                                             profile_notif_data["items"].append({
                                                "slot_identifier": slot_identifier,
                                                "slot_type": forge_type_key,
                                                "slot_number": int(slot_key) if str(slot_key).isdigit() else slot_key,
                                                "item_id": item_id_api,
                                                "notification_timestamp": current_time_ms,
                                                "notified": True
                                            })
                                        notifications_status_changed = True


            if all_ready_items_for_user:
                print(f"User {discord_user_id_str}: {len(all_ready_items_for_user)} items ready. Preparing combined notification.")

                all_ready_items_for_user.sort(key=lambda x: (x['profile_name'], int(x['slot_number']) if str(x['slot_number']).isdigit() else str(x['slot_number'])))

                message_lines = [f"{mention_string}\n"]
                message_lines.append("Your forge items are ready:")

                for item_info in all_ready_items_for_user:
                    ready_timestamp_unix = int(item_info["adjusted_end_time_ms"] / 1000)
                    started_timestamp_unix = int(item_info["start_time_ms"] / 1000)

                    ready_since_discord_format = f"<t:{ready_timestamp_unix}:R>"
                    started_ago_discord_format = f"<t:{started_timestamp_unix}:R>"

                    message_lines.append(
                        f"- Your **{item_info['item_name']}** on {item_info['profile_name']} was ready {ready_since_discord_format} (started {started_ago_discord_format})"
                    )

                combined_message = "\n".join(message_lines)

                combined_notification_data = {
                    "message": combined_message,
                    "discord_user_id": discord_user_id_str,
                }

                await self.send_forge_webhook(combined_notification_data)

        if notifications_status_changed:
             self.save_notifications_status()


    @check_forge_completions.before_loop
    async def before_check_forge_completions(self):
        """Ensures the bot is ready before starting the loop."""
        await self.bot.wait_until_ready()


    # --- Discord Event Listeners ---

    @commands.Cog.listener()
    async def on_ready(self):
        """Event handler for when the cog is loaded and bot is ready."""
        print(f"{self.__class__.__name__} Cog loaded and ready.")
        self.registrations = self.load_registrations()
        self.clock_usage = self.load_clock_usage()
        self.cleanup_expired_clock_entries()
        self.notifications_status = self.load_notifications_status()

        if not self.webhook_url:
            print("WARNING: WEBHOOK_URL not set. Forge completion notifications will be disabled.")


    # --- Discord Commands ---

    @app_commands.command(name="forge",
                          description="Shows active items in your or a specified player's Skyblock Forge.")
    @app_commands.describe(username="Optional: Minecraft name. Defaults to your first registered account.")
    @app_commands.describe(profile_name="Optional: Specific Skyblock profile name. Defaults to the latest played.")
    async def forge_command(self, interaction: discord.Interaction, username: str = None, profile_name: str = None):
        """
        Discord command to display active forge items.
        """
        if not self.hypixel_api_key:
            await interaction.response.send_message("Hypixel API key not configured.", ephemeral=True)
            return

        await interaction.response.defer()
        self.cleanup_expired_clock_entries()

        # --- Handle Case: No username or profile specified (Show registered accounts with pagination) ---
        if username is None and profile_name is None:
            discord_user_id = str(interaction.user.id)
            self.registrations = self.load_registrations()
            user_accounts = self.registrations.get(discord_user_id)

            if not user_accounts:
                await interaction.followup.send("No registered accounts. Use `/register`.", ephemeral=True)
                return

            active_forge_profiles_data = []

            for account in user_accounts:
                current_uuid = account.get('uuid')
                if not current_uuid: continue

                uuid_dashed = format_uuid(current_uuid)
                profiles_data = get_player_profiles(self.hypixel_api_key, uuid_dashed)

                if not profiles_data or not profiles_data.get("success", False):
                    print(f"Warning: Could not retrieve profiles for UUID {current_uuid} for user {discord_user_id}.")
                    continue

                profiles = profiles_data.get("profiles", [])
                if not profiles: continue

                # --- MODIFICATION START: Get username using uuid_to_username ---
                # Attempt to get the username using the UUID
                current_username_display = uuid_to_username(current_uuid)
                 # Fallback to UUID display if username lookup fails or returns None
                if not current_username_display:
                     print(f"Warning: Could not get username for UUID {current_uuid} using uuid_to_username. Using UUID display.")
                     current_username_display = f"UUID: {current_uuid[:8]}..."
                # --- MODIFICATION END ---


                for profile in profiles:
                    profile_cute_name = profile.get("cute_name", "Unknown Profile")
                    profile_internal_id = profile.get("profile_id")

                    if profile_internal_id is None:
                         print(f"Warning: Skipping profile '{profile_cute_name}' with missing internal ID for UUID {current_uuid}.")
                         continue

                    member_data = profile.get("members", {}).get(current_uuid, {})
                    forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})

                    has_any_active_items = False
                    if isinstance(forge_processes_data, dict):
                         has_any_active_items = any(
                            isinstance(slots_data, dict) and isinstance(item_data, dict) and item_data.get("startTime") is not None
                            for forge_type_key, slots_data in forge_processes_data.items()
                            for slot_data in (slots_data or {}).values()
                            for item_data in (slot_data,) if isinstance(item_data, dict)
                        )


                    if has_any_active_items:
                        forge_time_level = member_data.get("mining_core", {}).get("nodes", {}).get("forge_time")
                        time_reduction_percent = calculate_quick_forge_reduction(forge_time_level)
                        perk_message = f" (Quick Forge: -{time_reduction_percent:.1f}%)" if time_reduction_percent > 0 else ""

                        clock_is_actively_buffing = self.is_clock_used(current_uuid, profile_internal_id)

                        formatted_items_list = format_active_forge_items(
                            forge_processes_data, self.forge_items_data,
                            time_reduction_percent, clock_is_actively_buffing
                        )

                        active_forge_profiles_data.append({
                            "uuid": current_uuid,
                            "profile_id": profile_internal_id,
                            "username": current_username_display, # Use the fetched username
                            "profile_name": profile_cute_name,
                            "perk_message": perk_message,
                            "items_raw": forge_processes_data,
                            "time_reduction_percent": time_reduction_percent,
                            "formatted_items": "\n".join(formatted_items_list)
                        })

            if active_forge_profiles_data:
                view = ForgePaginationView(active_forge_profiles_data, interaction, self.forge_items_data, self)
                await interaction.edit_original_response(content="", embed=view.embeds[0], view=view)
            else:
                await interaction.followup.send("No active items found in Forge across your registered accounts.", ephemeral=True)

            return

        # --- Handle Case: Username and/or Profile specified (Show single profile data) ---
        # This section already handles getting the display name from the API,
        # so it should already show the IGN if available in the Hypixel data.
        # No major changes needed here for IGN display, but keeping the username display
        # variable consistent for clarity.

        target_uuid = None
        target_username_display = None # This will hold the IGN

        if username:
            target_username_display = username # Start with provided username
            target_uuid = get_uuid(username)

            if not target_uuid:
                await interaction.followup.send(f"Could not find player '{username}'. Please double-check the spelling.", ephemeral=True)
                return
        else:
            discord_user_id = str(interaction.user.id)
            self.registrations = self.load_registrations()
            user_accounts = self.registrations.get(discord_user_id)

            if not user_accounts:
                await interaction.followup.send("Please provide a Minecraft username to check, or register your account first.", ephemeral=True)
                return

            first_registered_account = user_accounts[0]
            target_uuid = first_registered_account.get('uuid')
            if not target_uuid:
                 await interaction.followup.send("Could not retrieve UUID for your first registered account. Please check your registration.", ephemeral=True)
                 return

            # --- MODIFICATION START: Get username using uuid_to_username for the default registered account ---
            # Attempt to get the username using the UUID from the first registered account
            target_username_display = await asyncio.to_thread(get_uuid, target_uuid, reverse=True) # Assuming get_uuid can do reverse lookup or you have a separate one
            # If username lookup fails, fallback to a temporary display
            if not target_username_display:
                 print(f"Warning: Could not get username for UUID {target_uuid} using uuid_to_username. Using UUID display.")
                 target_username_display = f"Registered Account (UUID: {target_uuid[:8]}...)"
            # --- MODIFICATION END ---


        uuid_dashed = format_uuid(target_uuid)
        profiles_data_full = get_player_profiles(self.hypixel_api_key, uuid_dashed)

        if not profiles_data_full or not profiles_data_full.get("success", False):
            await interaction.followup.send(f"Failed to retrieve Skyblock profiles for '{target_username_display}'.", ephemeral=True)
            return

        profiles = profiles_data_full.get("profiles", [])

        if not profiles:
            await interaction.followup.send(
                f"No Skyblock profiles found for '{target_username_display}'.", ephemeral=True)
            return

        target_profile = None
        if profile_name:
            target_profile = find_profile_by_name(profiles_data_full, profile_name)
            if not target_profile:
                await interaction.followup.send(f"Profile '{profile_name}' not found for '{target_username_display}'.", ephemeral=True)
                return
        else:
            target_profile = next((p for p in profiles if p.get("selected")), None)
            if not target_profile and profiles:
                profiles.sort(key=lambda p: p.get("members", {}).get(target_uuid, {}).get("last_save", 0), reverse=True)
                target_profile = profiles[0]
            if not target_profile:
                await interaction.followup.send(
                    f"Could not determine a suitable profile for '{target_username_display}'. Please specify a profile name.", ephemeral=True)
                return

        profile_cute_name = target_profile.get("cute_name", "Unknown Profile")
        profile_internal_id = target_profile.get("profile_id")

        # This part is already good - it attempts to get the displayname from the profile data
        # and updates target_username_display, which will be used in create_forge_embed.
        # This ensures the latest IGN from the API is used if available in the profile data.
        member_data_check_display = target_profile.get("members", {}).get(target_uuid, {})
        player_name_final = member_data_check_display.get("displayname")
        if player_name_final:
            target_username_display = player_name_final


        if profile_internal_id is None:
            print(
                f"Warning: Could not get internal profile ID for '{profile_cute_name}' ({target_uuid}). Clock buff/notifications may not work correctly.")

        member_data = target_profile.get("members", {}).get(target_uuid, {})
        forge_time_level = member_data.get("mining_core", {}).get("nodes", {}).get("forge_time")
        time_reduction_percent = calculate_quick_forge_reduction(forge_time_level)
        perk_message = f" (Quick Forge: -{time_reduction_percent:.1f}%)" if time_reduction_percent > 0 else ""
        forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})

        clock_is_actively_buffing_single = False
        if profile_internal_id:
            clock_is_actively_buffing_single = self.is_clock_used(target_uuid, profile_internal_id)

        has_any_active_items_single = False
        if isinstance(forge_processes_data, dict):
             has_any_active_items_single = any(
                isinstance(slots_data, dict) and isinstance(item_data, dict) and item_data.get("startTime") is not None
                for forge_type_key, slots_data in forge_processes_data.items()
                for slot_data in (slots_data or {}).values()
                 for item_data in (slot_data,) if isinstance(item_data, dict)
            )

        if not has_any_active_items_single:
            await interaction.followup.send(
                f"No active items found in Forge on profile '{profile_cute_name}' of '{target_username_display}'{perk_message}.", ephemeral=True)
            return

        formatted_items_list_single = format_active_forge_items(
            forge_processes_data, self.forge_items_data,
            time_reduction_percent, clock_is_actively_buffing_single
        )

        single_profile_data = {
            "uuid": target_uuid,
            "profile_id": profile_internal_id,
            "username": target_username_display, # Use the fetched IGN here
            "profile_name": profile_cute_name,
            "perk_message": perk_message,
            "items_raw": forge_processes_data,
            "time_reduction_percent": time_reduction_percent,
        }

        embed = create_forge_embed(
            single_profile_data,
            "\n".join(formatted_items_list_single) if formatted_items_list_single else "No active items found."
        )

        if clock_is_actively_buffing_single:
            clock_note = "\n*Enchanted Clock buff applied.*"
            embed.description = (embed.description or "") + clock_note

        view = SingleForgeView(
            single_profile_data,
            interaction,
            self.forge_items_data,
            self,
            "\n".join(formatted_items_list_single)
        )

        await interaction.followup.send(embed=embed, view=view)


# --- Cog Setup Function ---

async def setup(bot: commands.Bot):
    """Sets up the ForgeCog and adds it to the bot."""
    await bot.add_cog(ForgeCog(bot))