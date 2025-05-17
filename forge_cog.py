# forge_cog.py

import discord
from discord import app_commands
from discord.ext import commands, tasks  # Added tasks
import os
import time
import json
import asyncio
import requests

# Import necessary functions from skyblock.py
from skyblock import get_uuid, format_uuid, get_player_profiles, find_profile_by_name

# Define file paths
REGISTRATION_FILE = 'registrations.json'
CLOCK_USAGE_FILE = 'clock_usage.json'
NOTIFICATIONS_FILE = 'forge_notifications.json'  # Already defined

# Define constant for Enchanted Clock reduction (1 hour in milliseconds)
ENCHANTED_CLOCK_REDUCTION_MS = 60 * 60 * 1000


# Helper function to format time difference
def format_time_difference(milliseconds: float) -> str:
    """
    Formats a time difference in milliseconds into a human-readable string.
    Ignores seconds if duration is 1 hour or more.
    """
    if milliseconds <= 0:
        return "Finished"

    seconds = int(milliseconds // 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)

    parts = []
    if days > 0:
        parts.append(f"{days}d")

    if milliseconds >= 3_600_000:  # If 1 hour or more
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        # Seconds are ignored
    else:  # Less than 1 hour
        if hours > 0:  # Should not be more than 0 for <1 hour, but included for completeness
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if seconds > 0 or not parts:  # Include seconds if > 0 or if total time was <1 minute
            parts.append(f"{seconds}s")

    if not parts and milliseconds > 0:
        return "<1s"
    elif not parts and milliseconds <= 0:
        return "Finished"

    return " ".join(parts)


# Function to calculate Quick Forge time reduction percentage based on tiers
def calculate_quick_forge_reduction(forge_time_level: int | None) -> float:
    """
    Calculates Quick Forge time reduction percentage based on tier level.
    """
    tier_percentages = [  # Up to tier 19
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
        print(f"Warning: Unexpected forge_time_level: {level}")
        return 0.0


# Function to format active forge items for display
def format_active_forge_items(forge_processes_data: dict, forge_items_config: dict, time_reduction_percent: float,
                              clock_is_actively_buffing: bool) -> list[str]:
    """
    Formats the active forge items with remaining times, applying buffs.
    Returns a list of formatted strings, one for each active item.
    """
    forge_items_output = []
    current_time_ms = time.time() * 1000

    if not forge_processes_data:
        return []

    for forge_type_key in sorted(forge_processes_data.keys()):
        slots_data = forge_processes_data[forge_type_key]
        # Sort slots numerically
        sorted_slots = sorted(slots_data.keys(), key=lambda x: int(x) if x.isdigit() else float('inf'))

        for slot in sorted_slots:
            item_data = slots_data.get(slot)
            if not item_data or item_data.get("startTime") is None:
                continue  # Skip if slot data is missing or item is not active

            item_id = item_data.get("id", "Unknown Item")
            start_time_ms = item_data.get("startTime")

            item_name = item_id
            remaining_time_str = "Time unknown"

            forge_item_info = forge_items_config.get(item_id)

            if forge_item_info and start_time_ms is not None:
                item_name = forge_item_info.get("name", item_id)
                base_duration_ms = forge_item_info.get("duration")

                if base_duration_ms is not None:
                    effective_duration_ms = base_duration_ms * (1 - time_reduction_percent / 100)
                    end_time_ms = start_time_ms + effective_duration_ms
                    remaining_time_ms = end_time_ms - current_time_ms

                    # Apply the persistent clock buff if active
                    if clock_is_actively_buffing:
                        remaining_time_ms = max(0, remaining_time_ms - ENCHANTED_CLOCK_REDUCTION_MS)

                    remaining_time_str = format_time_difference(remaining_time_ms)
                else:
                    remaining_time_str = "Duration unknown (JSON)"

            elif start_time_ms is None:
                remaining_time_str = "Start time unknown (API)"
            else:  # No forge_item_info found for the item_id
                remaining_time_str = "Duration unknown (Item data missing)"

            forge_items_output.append(
                f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Remaining: {remaining_time_str}")

    return forge_items_output


# Function to generate Embed for a specific profile's forge data
def create_forge_embed(profile_data: dict, formatted_items: str, page_number: int | None = None,
                       total_pages: int | None = None) -> discord.Embed:
    """Creates a discord.Embed for a single profile's active forge items."""
    items_description = formatted_items if formatted_items else "No active items in Forge slots."

    embed = discord.Embed(
        title=f"Forge Items for '{profile_data.get('profile_name', 'Unknown Profile')}' on '{profile_data.get('username', 'Unknown User')}'",
        description=items_description,
        color=discord.Color.blue()  # You can choose a different color
    )

    if profile_data.get('perk_message'):
        embed.add_field(name="Perk", value=profile_data['perk_message'].strip(), inline=False)

    if page_number is not None and total_pages is not None:
        embed.set_footer(text=f"Profile {page_number + 1}/{total_pages}")
    return embed


# Define the pagination view for the forge list (no arguments case)
class ForgePaginationView(discord.ui.View):
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
            for button in self.children:
                if hasattr(button, 'label') and button.label in ["Prev", "Next"]:  # Check if button has label
                    button.disabled = True
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
            slot_data.get("startTime") is not None
            for forge_type_key, slots_data in raw_forge_processes.items()
            for slot_data in slots_data.values()
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
            await interaction.followup.send("The Enchanted Clock has already been used for this profile.",
                                            ephemeral=True)
            return
        raw_forge_processes = profile_data.get("items_raw")
        time_reduction_percent = profile_data.get("time_reduction_percent", 0.0)
        has_active_items_now = any(
            slot_data.get("startTime") is not None
            for forge_type_key, slots_data in (raw_forge_processes or {}).items()
            for slot_data in slots_data.values()
        )
        if not has_active_items_now:
            await interaction.followup.send("No active items in the Forge for this profile to apply the clock to.",
                                            ephemeral=True)
            return
        self.clock_usage_cog_ref.mark_clock_used(profile_uuid, profile_internal_id,
                                                 profile_data.get("profile_name", "Unknown Profile"))
        updated_formatted_items = []
        current_time_ms = time.time() * 1000
        for forge_type_key in sorted(raw_forge_processes.keys()):
            slots_data = raw_forge_processes[forge_type_key]
            sorted_slots = sorted(slots_data.keys(), key=lambda x: int(x) if x.isdigit() else float('inf'))
            for slot in sorted_slots:
                item_data = slots_data.get(slot)
                if not item_data or item_data.get("startTime") is None:
                    continue
                item_id = item_data.get("id", "Unknown Item")
                start_time_ms = item_data.get("startTime")
                item_name = item_id
                remaining_time_str = "Time unknown"
                forge_item_info = self.forge_items_config.get(item_id)
                if forge_item_info and start_time_ms is not None:
                    item_name = forge_item_info.get("name", item_id)
                    base_duration_ms = forge_item_info.get("duration")
                    if base_duration_ms is not None:
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
        self.forge_data_list[current_profile_index]["formatted_items"] = "\n".join(updated_formatted_items)
        self.embeds[current_profile_index] = create_forge_embed(
            self.forge_data_list[current_profile_index],
            self.forge_data_list[current_profile_index]["formatted_items"],
            current_profile_index,
            len(self.forge_data_list)
        )
        self.update_clock_button_state()
        await self.interaction.edit_original_response(embed=self.embeds[self.current_page], view=self)

    async def on_timeout(self):
        for item in self.children:  # Changed from button to item to catch all ui elements
            item.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"Error updating view on timeout: {e}")


# Define a single profile view
class SingleForgeView(discord.ui.View):
    def __init__(self, profile_data: dict, interaction: discord.Interaction, forge_items_config: dict,
                 clock_usage_cog_ref, formatted_items: str, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.profile_data = profile_data
        self.interaction = interaction
        self.forge_items_config = forge_items_config
        self.clock_usage_cog_ref = clock_usage_cog_ref
        self.formatted_items = formatted_items
        self.update_clock_button_state()

    def update_clock_button_state(self):
        has_active_items = False
        raw_forge_processes = self.profile_data.get("items_raw", {})
        if raw_forge_processes:
            for forge_type_key in raw_forge_processes.keys():
                slots_data = raw_forge_processes[forge_type_key]
                for slot_data in slots_data.values():
                    if slot_data.get("startTime") is not None:
                        has_active_items = True
                        break
                if has_active_items: break
        profile_internal_id = self.profile_data.get("profile_id")
        profile_uuid = self.profile_data.get("uuid")
        if profile_internal_id is None or profile_uuid is None:
            self.enchanted_clock_button.disabled = True
            return
        self.enchanted_clock_button.disabled = not has_active_items or self.clock_usage_cog_ref.is_clock_used(
            profile_uuid, profile_internal_id)

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
            await interaction.followup.send("The Enchanted Clock has already been used for this profile.",
                                            ephemeral=True)
            return
        raw_forge_processes = profile_data.get("items_raw")
        time_reduction_percent = profile_data.get("time_reduction_percent", 0.0)
        has_active_items_now = any(
            slot_data.get("startTime") is not None
            for forge_type_key, slots_data in (raw_forge_processes or {}).items()
            for slot_data in slots_data.values()
        )
        if not has_active_items_now:
            await interaction.followup.send("No active items in the Forge for this profile to apply the clock to.",
                                            ephemeral=True)
            return
        self.clock_usage_cog_ref.mark_clock_used(profile_uuid, profile_internal_id,
                                                 profile_data.get("profile_name", "Unknown Profile"))
        current_time_ms = time.time() * 1000
        updated_formatted_items = []
        for forge_type_key in sorted(raw_forge_processes.keys()):
            slots_data = raw_forge_processes[forge_type_key]
            sorted_slots = sorted(slots_data.keys(), key=lambda x: int(x) if x.isdigit() else float('inf'))
            for slot in sorted_slots:
                item_data = slots_data.get(slot)
                if not item_data or item_data.get(
                        "startTime") is None:  # Corrected: was item_data.get("startTime") is not None
                    continue
                item_id = item_data.get("id", "Unknown Item")
                start_time_ms = item_data.get("startTime")
                item_name = item_id
                remaining_time_str = "Time unknown"
                forge_item_info = self.forge_items_config.get(item_id)
                if forge_item_info:
                    item_name = forge_item_info.get("name", item_id)
                    base_duration_ms = forge_item_info.get("duration")
                    if base_duration_ms is not None:
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
        embed.description = (embed.description or "") + clock_note  # Ensure description is not None
        self.update_clock_button_state()
        await self.interaction.edit_original_response(embed=embed, view=self)

    async def on_timeout(self):
        for item in self.children:  # Changed from button to item
            item.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"Error updating view on timeout: {e}")


class ForgeCog(commands.Cog, name="Forge Functions"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.hypixel_api_key = os.getenv("HYPIXEL_API_KEY")
        if not self.hypixel_api_key:
            print("WARNING: HYPIXEL_API_KEY not found. Forge commands will not work.")

        self.forge_items_data = self.load_forge_items_data()
        self.registrations = self.load_registrations()
        self.clock_usage = self.load_clock_usage()

        # --- START: Added for Notifications ---
        self.webhook_url = os.getenv("WEBHOOK_URL")
        if not self.webhook_url:
            print("WARNING: WEBHOOK_URL not found in .env. Forge notifications will not be sent.")
        self.notifications_status = self.load_notifications_status()
        self.check_forge_completions.start()  # Start the background task
        # --- END: Added for Notifications ---

    def load_forge_items_data(self) -> dict:
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
        if not os.path.exists(REGISTRATION_FILE):
            return {}
        try:
            with open(REGISTRATION_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            print(f"ERROR: Could not load {REGISTRATION_FILE} in ForgeCog: {e}. Assuming empty registrations.")
            return {}

    def load_clock_usage(self) -> dict:
        if not os.path.exists(CLOCK_USAGE_FILE):
            print(f"Clock usage file not found: {CLOCK_USAGE_FILE}. Starting with empty data.")
            return {}
        try:
            with open(CLOCK_USAGE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Basic validation
                if not isinstance(data, dict): return {}
                for uuid, profiles in list(data.items()):
                    if not isinstance(profiles, dict): del data[uuid]; continue
                    for profile_id, p_data in list(profiles.items()):
                        if not isinstance(p_data,
                                          dict) or "end_timestamp" not in p_data or "profile_name" not in p_data:
                            del profiles[profile_id]
                    if not profiles: del data[uuid]
                return data
        except Exception as e:
            print(f"An unexpected error occurred loading {CLOCK_USAGE_FILE}: {e}")
            return {}

    def save_clock_usage(self):
        try:
            os.makedirs(os.path.dirname(CLOCK_USAGE_FILE) or '.', exist_ok=True)
            temp_file = CLOCK_USAGE_FILE + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self.clock_usage, f, indent=4)
            os.replace(temp_file, CLOCK_USAGE_FILE)
        except Exception as e:
            print(f"ERROR: Could not save {CLOCK_USAGE_FILE}: {e}")

    def is_clock_used(self, uuid: str, profile_internal_id: str) -> bool:
        profile_data = self.clock_usage.get(uuid, {}).get(profile_internal_id)
        if profile_data:
            end_timestamp = profile_data.get("end_timestamp")
            if end_timestamp is not None:
                return time.time() * 1000 < end_timestamp
        return False

    def mark_clock_used(self, uuid: str, profile_internal_id: str, profile_cute_name: str):
        current_time_ms = time.time() * 1000
        end_timestamp = current_time_ms + ENCHANTED_CLOCK_REDUCTION_MS
        if uuid not in self.clock_usage: self.clock_usage[uuid] = {}
        self.clock_usage[uuid][profile_internal_id] = {"profile_name": profile_cute_name,
                                                       "end_timestamp": end_timestamp}
        self.save_clock_usage()

    def reset_clock_usage(self, uuid: str, profile_internal_id: str):
        if uuid in self.clock_usage and profile_internal_id in self.clock_usage.get(uuid, {}):
            del self.clock_usage[uuid][profile_internal_id]
            if not self.clock_usage[uuid]: del self.clock_usage[uuid]
            self.save_clock_usage()

    # --- START: Methods for Notifications ---
    def load_notifications_status(self) -> dict:
        """Loads forge notification status from NOTIFICATIONS_FILE."""
        if not os.path.exists(NOTIFICATIONS_FILE):
            return {}
        try:
            with open(NOTIFICATIONS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # Perform basic validation if necessary, similar to load_clock_usage
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
        """
        Sends a notification to the configured webhook URL using synchronous requests in a thread.
        Constructs the Discord-specific payload from the provided notification data.
        Includes allowed_mentions to ensure user pings work.
        """
        if not self.webhook_url:
            print("Webhook URL not configured. Skipping notification.")
            return

        # Extrahieren Sie die notwendigen Daten aus dem übergebenen Dictionary
        # 'message' enthält die gesamte formatierte Nachricht inkl. Ping und Item-Liste
        message_content = notification_data.get("message", f"Ein Gegenstand ist fertig!")
        # Holen Sie die Discord Benutzer-ID für allowed_mentions
        discord_user_id = notification_data.get("discord_user_id")


        # Konstruieren Sie das tatsächliche Payload für den Discord-Webhook
        webhook_payload = {
            "content": message_content, # Verwenden Sie das 'message'-Feld als Inhalt der Nachricht
            # Fügen Sie allowed_mentions hinzu, um spezifische Benutzer-Pings zu erlauben
            "allowed_mentions": {
                "parse": ["users"], # Erlauben Sie das Parsen von Benutzer-Erwähnungen im 'content' Feld.
                # Optional: Sie können auch eine Liste spezifischer User-IDs übergeben, z.B.:
                # "users": [discord_user_id] if discord_user_id else [],
                 "replied_user": False # Deaktiviert standardmäßig die Erwähnung des Benutzers, auf dessen Nachricht geantwortet wird
            }
            # Optional: Fügen Sie Embeds für eine reichere Nachricht hinzu (siehe vorherige Beispiele)
            # Wenn Sie Embeds verwenden, sollten Sie wahrscheinlich den 'content' leer lassen
            # und die Informationen in die Embeds packen, aber der Ping muss im 'content' sein oder Reply-Funktion nutzen.
        }

        # Stellen Sie sicher, dass das Payload nicht komplett leer ist (mindestens Content oder Embeds)
        if not webhook_payload.get("content") and not webhook_payload.get("embeds"):
            print(f"Warning: Webhook payload is empty. Not sending.")
            return

        headers = {'Content-Type': 'application/json'}
        try:
            # Führen Sie den synchronen requests.post Aufruf in einem separaten Thread aus.
            # Annahme: asyncio.to_thread ist verfügbar (Python 3.9+)
            response = await asyncio.to_thread(
                requests.post,
                self.webhook_url,
                json=webhook_payload, # Senden Sie das korrekt strukturierte Payload
                headers=headers,
                timeout=10 # Timeout ist wichtig
            )

            # Verarbeiten Sie das synchrone Response-Objekt von requests
            if 200 <= response.status_code < 300:
                # Protokollieren Sie, dass eine kombinierte Nachricht gesendet wurde
                print(f"Successfully sent combined webhook notification for user {discord_user_id}.")
            else:
                # Geben Sie den Statuscode und die Fehlermeldung von Discord aus
                print(f"Error sending combined webhook for user {discord_user_id}: {response.status_code} - {response.text}")

        except requests.exceptions.Timeout:
            print(f"Timeout error sending combined webhook for user {discord_user_id}.")
        except requests.exceptions.RequestException as e:
            print(f"Request exception sending combined webhook for user {discord_user_id}: {e}")
        except Exception as e:
            print(f"Unexpected exception sending combined webhook for user {discord_user_id}: {e}")

    @tasks.loop(minutes=1)  # Check every 1 minute, adjust as needed
    async def check_forge_completions(self):
        """Periodically checks for completed forge items and sends one combined notification per user in the desired style."""
        # Sicherstellen, dass API-Schlüssel und Webhook-URL vorhanden sind
        if not self.hypixel_api_key or not self.webhook_url:
            if not self.hypixel_api_key: print("Notification Task: Hypixel API key missing.")
            if not self.webhook_url: print("Notification Task: Webhook URL missing.")
            return # Task nicht ausführen, wenn Voraussetzungen fehlen

        await self.bot.wait_until_ready() # Warten, bis der Bot bereit ist

        current_time_ms = time.time() * 1000
        self.registrations = self.load_registrations()  # Ensure up-to-date registrations
        if not self.registrations: return

        notifications_status_changed = False # Flag, um zu verfolgen, ob wir den Status speichern müssen

        # Durchlaufen Sie jeden registrierten Discord-Benutzer
        # Nutze list() für sichere Iteration bei möglicher Änderung
        for discord_user_id_str, user_accounts in list(self.registrations.items()):
            if not user_accounts: continue

            try:
                discord_user_id = int(discord_user_id_str)
                # Versuche, den Discord-Benutzer zu holen, um ihn zu erwähnen
                # Erstelle den Erwähnungs-String. Nutze den Benutzer-ID-String als Fallback.
                # Dieser String wird am Anfang der Nachricht stehen und den Ping versuchen.
                mention_string = f"<@{discord_user_id}>"
            except ValueError:
                print(f"Invalid Discord User ID in registrations: {discord_user_id_str}")
                continue

            # Liste, um ALLE fertigen Gegenstände für diesen Benutzer über alle Profile hinweg zu sammeln
            all_ready_items_for_user = []

            # Durchlaufen Sie jedes Minecraft-Konto des Benutzers
            for account in list(user_accounts):
                mc_uuid = account['uuid']
                uuid_dashed = format_uuid(mc_uuid)
                profiles_data_full = get_player_profiles(self.hypixel_api_key, uuid_dashed)

                if not profiles_data_full or not profiles_data_full.get("success", False):
                    continue # Konto überspringen, wenn Profile nicht abgerufen werden konnten

                profiles = profiles_data_full.get("profiles", [])
                # Profile ohne Schmiedeprozesse werden immer noch durchlaufen, um den Benachrichtigungsstatus zu bereinigen

                # Durchlaufen Sie jedes Profil des Kontos
                for profile in list(profiles):
                    profile_cute_name = profile.get("cute_name", "Unknown Profile")
                    profile_internal_id = profile.get("profile_id")
                    if not profile_internal_id: continue # Profile ohne ID können nicht getrackt werden

                    member_data = profile.get("members", {}).get(mc_uuid, {})
                    forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})
                    # forge_processes_data kann leer sein, wenn keine Items in der Schmiede sind

                    forge_time_level = member_data.get("mining_core", {}).get("nodes", {}).get("forge_time")
                    time_reduction_percent = calculate_quick_forge_reduction(forge_time_level)
                    clock_is_active = self.is_clock_used(mc_uuid, profile_internal_id)

                    # Sicherstellen, dass die Benachrichtigungsstatusstruktur existiert
                    if discord_user_id_str not in self.notifications_status: self.notifications_status[discord_user_id_str] = {}
                    if mc_uuid not in self.notifications_status[discord_user_id_str]:
                        self.notifications_status[discord_user_id_str][mc_uuid] = {}
                    if profile_internal_id not in self.notifications_status[discord_user_id_str][mc_uuid]:
                        self.notifications_status[discord_user_id_str][mc_uuid][profile_internal_id] = {
                            "profile_name": profile_cute_name, "items": []}

                    profile_notif_data = self.notifications_status[discord_user_id_str][mc_uuid][profile_internal_id]

                    # --- Benachrichtigungsstatus-Bereinigung ---
                    # Erstelle ein Set mit eindeutigen Identifikatoren der AKTUELL in forge_processes_data enthaltenen Gegenstände für dieses Profil
                    current_items_in_api_forge = set()
                    for forge_type_key, slots_data in (forge_processes_data or {}).items():
                         if isinstance(slots_data, dict):
                            for slot_key, item_api_data in slots_data.items():
                                if isinstance(item_api_data, dict) and item_api_data.get("startTime") is not None and item_api_data.get("id") is not None:
                                     # Identifikator: slot_type_slot_number_item_id
                                     identifier = f"{forge_type_key}_{slot_key}_{item_api_data['id']}"
                                     current_items_in_api_forge.add(identifier)

                    # Bereinige getrackte Gegenstände im Status, die nicht mehr in den aktuellen API forge_processes_data gefunden wurden
                    items_to_keep = []
                    for tracked_item in profile_notif_data.get("items", []):
                         if isinstance(tracked_item, dict) and tracked_item.get("slot_identifier") and tracked_item.get("item_id"):
                             # Identifikator basierend auf gespeicherten Daten
                             tracked_identifier = f"{tracked_item.get('slot_type', 'unknown')}_{tracked_item.get('slot_number', 'unknown')}_{tracked_item.get('item_id', 'unknown')}"

                             # Wenn der getrackte Gegenstand noch in den aktuellen API forge_processes_data auftaucht, behalte ihn
                             if tracked_identifier in current_items_in_api_forge:
                                items_to_keep.append(tracked_item)
                             else:
                                # Gegenstand wurde eingesammelt (oder ist aus den API-Daten verschwunden), entferne ihn aus dem Tracking
                                # print(f"Cleaning up tracked item: {tracked_item.get('item_name', tracked_item.get('item_id', 'Unknown'))} from {profile_cute_name} for user {discord_user_id_str}")
                                notifications_status_changed = True

                    profile_notif_data["items"] = items_to_keep
                    # --- Ende Bereinigung ---


                    # --- Nach Fertigstellung suchen und Gegenstände sammeln ---
                    # Durchlaufen Sie nur die Schmiedeschächte, die in den aktuellen API-Daten vorhanden sind
                    for forge_type_key, slots_data in (forge_processes_data or {}).items():
                        if isinstance(slots_data, dict):
                            for slot_key, item_api_data in slots_data.items():
                                # Nur aktive Schächte mit Item-ID und Startzeit verarbeiten
                                if isinstance(item_api_data, dict) and item_api_data.get("startTime") is not None and item_api_data.get("id") is not None:
                                    item_id_api = item_api_data.get("id")
                                    start_time_ms_api = item_api_data.get("startTime")

                                    forge_item_details = self.forge_items_data.get(item_id_api)
                                    if not forge_item_details or forge_item_details.get("duration") is None:
                                        continue # Kann die Fertigstellungszeit ohne Dauer nicht berechnen

                                    item_name_display = forge_item_details.get("name", item_id_api)
                                    base_duration_ms = forge_item_details.get("duration")

                                    effective_duration_ms = base_duration_ms * (1 - time_reduction_percent / 100)
                                    adjusted_duration_ms = max(0, effective_duration_ms)

                                    # Berücksichtigung des Enchanted Clock Buffs für die Endzeit der Benachrichtigung
                                    # Dies ist die Zeit, zu der der Gegenstand FÜR DIE BENACHRICHTIGUNG als fertig gilt
                                    adjusted_end_time_ms = start_time_ms_api + adjusted_duration_ms
                                    if clock_is_active:
                                        adjusted_end_time_ms = start_time_ms_api + max(0, effective_duration_ms - ENCHANTED_CLOCK_REDUCTION_MS)


                                    slot_identifier = f"{forge_type_key}_{slot_key}"

                                    # Überprüfen, ob diese spezifische Gegenstandsinstanz bereits benachrichtigt wurde
                                    # Suche in der aktuellen profile_notif_data["items"] Liste
                                    already_notified = any(entry.get("slot_identifier") == slot_identifier and entry.get("item_id") == item_id_api and entry.get("notified", False) for entry in profile_notif_data["items"])

                                    # Wenn Gegenstand fertig ist UND noch nicht benachrichtigt wurde
                                    if current_time_ms >= adjusted_end_time_ms and not already_notified:
                                        print(
                                            f"Item {item_name_display} in {profile_cute_name} ({mc_uuid}) ready for user {discord_user_id_str}. Adding to combined list.")

                                        # Füge Details zur kombinierten Liste des Benutzers hinzu, inkl. Zeiten
                                        all_ready_items_for_user.append({
                                            "profile_name": profile_cute_name,
                                            "item_name": item_name_display,
                                            "slot_type": forge_type_key, # Speichere den rohen Typ für den Identifier
                                            "slot_number": slot_key,
                                            "start_time_ms": start_time_ms_api,
                                            "adjusted_end_time_ms": adjusted_end_time_ms, # Endzeit für Benachrichtigungszwecke
                                            # Speichere die Identifier für das spätere Markieren als benachrichtigt
                                            "slot_identifier": slot_identifier,
                                            "item_id": item_id_api
                                        })

                                        # Markiere diesen Gegenstand sofort im Status-Tracking für dieses Profil als benachrichtigt
                                        # Suche den Eintrag in der aktuellen profile_notif_data["items"] Liste
                                        found_entry_in_status = next((entry for entry in profile_notif_data["items"] if entry.get("slot_identifier") == slot_identifier and entry.get("item_id") == item_id_api), None)

                                        if found_entry_in_status:
                                             found_entry_in_status["notified"] = True
                                             found_entry_in_status["notification_timestamp"] = current_time_ms # Speichern, wann es benachrichtigt wurde
                                        else:
                                             # Dieser Fall sollte seltener auftreten, wenn die Bereinigung korrekt ist,
                                             # aber als Fallback, falls ein neues Item direkt fertig ist und noch nicht getrackt.
                                             profile_notif_data["items"].append({
                                                "slot_identifier": slot_identifier,
                                                "slot_type": forge_type_key,
                                                "slot_number": int(slot_key) if slot_key.isdigit() else slot_key,
                                                "item_id": item_id_api,
                                                "notification_timestamp": current_time_ms,
                                                "notified": True
                                            })
                                        notifications_status_changed = True # Markiere, dass wir speichern müssen
                    # --- Ende Suche und Sammeln ---

            # --- Nachdem alle Profile für den Benutzer überprüft wurden, senden Sie eine einzige kombinierte Benachrichtigung, falls Gegenstände fertig sind ---
            if all_ready_items_for_user:
                print(f"Sending combined notification for user {discord_user_id_str} with {len(all_ready_items_for_user)} ready items.")

                # Sortiere Gegenstände nach Profilname und dann Slot-Nummer für eine konsistente Reihenfolge
                all_ready_items_for_user.sort(key=lambda x: (x['profile_name'], int(x['slot_number']) if str(x['slot_number']).isdigit() else x['slot_number']))

                # Konstruiere den kombinierten Nachrichteninhalt im gewünschten Stil
                # Starte mit der Benutzererwähnung, gefolgt von zwei Zeilenumbrüchen
                # Füge den Erwähnungsstring am Anfang hinzu, um den Ping zu gewährleisten.
                message_lines = [f"{mention_string}\n"]

                # Durchlaufen Sie die gesammelten fertigen Gegenstände
                for item_info in all_ready_items_for_user:
                    # Berechnen Sie die Unix-Zeitstempel in Sekunden
                    # Verwenden Sie die "adjusted_end_time_ms" für die "fertig seit" Zeit
                    ready_timestamp_unix = int(item_info["adjusted_end_time_ms"] / 1000)
                    # Verwenden Sie die "start_time_ms" für die "begonnen vor" Zeit
                    started_timestamp_unix = int(item_info["start_time_ms"] / 1000)

                    # Formatieren Sie die Zeiten mit dem Discord-relativen Zeitstempel-Markdown
                    # Style 'R' für relative Zeit (z.B. "vor 5 Stunden")
                    ready_since_discord_format = f"<t:{ready_timestamp_unix}:R>"
                    started_ago_discord_format = f"<t:{started_timestamp_unix}:R>"

                    # Konstruieren Sie die formatierte Zeile für diesen Gegenstand
                    # Passen Sie die Formulierung an das relative Zeitformat an.
                    message_lines.append(
                        f"Your **{item_info['item_name']}** on {item_info['profile_name']} was ready {ready_since_discord_format} (started {started_ago_discord_format})"
                        # "was ready <t:...:R>" passt besser zum relativen Format als "is ready since".
                    )

                # Fügen Sie optional einen abschließenden Satz hinzu
                # message_lines.append("\nVergiss nicht, sie abzuholen!")

                # Verbinden Sie alle Zeilen zu einer einzigen Nachricht
                combined_message = "\n".join(message_lines)

                # Konstruiere die Benachrichtigungsdaten für die Webhook-Funktion
                combined_notification_data = {
                    "message": combined_message, # Dies ist der Hauptinhalt, der den Ping und die formatierte Liste enthält
                    # Zusätzliche Daten, die send_forge_webhook eventuell für die Protokollierung oder allowed_mentions benötigt
                    "discord_user_id": discord_user_id_str, # Füge die Benutzer-ID hinzu
                    "discord_user_mention": mention_string # Füge den Erwähnungsstring hinzu (redundant in 'message', aber zur Sicherheit)
                    # Weitere Dummy-Informationen können bei Bedarf hinzugefügt werden
                }
                await self.send_forge_webhook(combined_notification_data)


        # Nachdem alle Benutzer überprüft wurden, speichern Sie den Benachrichtigungsstatus, falls sich etwas geändert hat
        if notifications_status_changed:
             self.save_notifications_status()


    @check_forge_completions.before_loop
    async def before_check_forge_completions(self):
        """Ensures the bot is ready before starting the loop."""
        await self.bot.wait_until_ready()
        # Keine aiohttp-Sitzungsprüfung/-Initialisierung mehr hier notwendig

    # --- END: Methods for Notifications ---

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.__class__.__name__} Cog loaded and ready.")
        self.registrations = self.load_registrations()
        self.clock_usage = self.load_clock_usage()
        self.cleanup_expired_clock_entries()
        # --- Added for Notifications ---
        self.notifications_status = self.load_notifications_status()
        if not self.webhook_url:
            print("WARNING: WEBHOOK_URL not set. Forge completion notifications will be disabled.")
        # Keine aiohttp-Sitzungsinitialisierung mehr hier notwendig
        # --- End Added for Notifications ---

    def cleanup_expired_clock_entries(self):
        current_time_ms = time.time() * 1000
        modified = False
        for uuid in list(self.clock_usage.keys()):
            profiles = self.clock_usage.get(uuid, {})
            if not isinstance(profiles, dict):
                if uuid in self.clock_usage: del self.clock_usage[uuid]; modified = True
                continue
            profile_ids_to_delete = [pid for pid, pdata in profiles.items() if
                                     not isinstance(pdata, dict) or "end_timestamp" not in pdata or not isinstance(
                                         pdata.get("end_timestamp"), (int, float)) or current_time_ms >= pdata.get(
                                         "end_timestamp", 0)]
            for profile_id in profile_ids_to_delete:
                if profile_id in profiles: del profiles[profile_id]; modified = True
            if not profiles and uuid in self.clock_usage: del self.clock_usage[uuid]; modified = True
        if modified: self.save_clock_usage()

    @app_commands.command(name="forge",
                          description="Shows active items in your or a specified player's Skyblock Forge.")
    @app_commands.describe(username="Optional: Minecraft name. Defaults to first registered account.")
    @app_commands.describe(profile_name="Optional: Specific Skyblock profile name. Defaults to latest played.")
    async def forge_command(self, interaction: discord.Interaction, username: str = None, profile_name: str = None):
        if not self.hypixel_api_key:
            await interaction.response.send_message("Hypixel API key not configured.", ephemeral=True)
            return
        await interaction.response.defer()
        self.cleanup_expired_clock_entries()

        if username is None and profile_name is None:
            discord_user_id = str(interaction.user.id)
            self.registrations = self.load_registrations()
            user_accounts = self.registrations.get(discord_user_id)
            if not user_accounts:
                await interaction.followup.send("No registered accounts. Use `/register`.")
                return
            active_forge_profiles_data = []
            # Message removed as the notification task handles ongoing checks.
            # await interaction.followup.send("Checking registered accounts...", ephemeral=False)
            for account in user_accounts:
                current_uuid = account['uuid']
                uuid_dashed = format_uuid(current_uuid)
                profiles_data = get_player_profiles(self.hypixel_api_key, uuid_dashed)
                if not profiles_data or not profiles_data.get("success", False): continue
                profiles = profiles_data.get("profiles", [])
                if not profiles: continue
                current_username_display = f"UUID: `{current_uuid}`"  # Default
                sample_profile = profiles[0]  # Get displayname from one of the profiles
                member_data_check_displayname = sample_profile.get("members", {}).get(current_uuid, {})
                player_name_display = member_data_check_displayname.get("displayname")
                if player_name_display: current_username_display = player_name_display

                for profile in profiles:
                    profile_cute_name = profile.get("cute_name", "Unknown Profile")
                    profile_internal_id = profile.get("profile_id")
                    if profile_internal_id is None: continue
                    member_data = profile.get("members", {}).get(current_uuid, {})
                    forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})
                    has_any_active_items = any(
                        slot_data.get("startTime") is not None
                        for forge_type_key, slots_data in (forge_processes_data or {}).items()
                        for slot_data in slots_data.values()
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
                            "uuid": current_uuid, "profile_id": profile_internal_id,
                            "username": current_username_display, "profile_name": profile_cute_name,
                            "perk_message": perk_message, "items_raw": forge_processes_data,
                            "time_reduction_percent": time_reduction_percent,
                            "formatted_items": "\n".join(formatted_items_list)
                        })
            if active_forge_profiles_data:
                view = ForgePaginationView(active_forge_profiles_data, interaction, self.forge_items_data, self)
                await interaction.edit_original_response(content="", embed=view.embeds[0],
                                                         view=view)  # Use edit_original_response
            else:
                await interaction.followup.send("No active items found in Forge across your registered accounts.")
            return

        target_uuid = None
        target_username_display = None
        if username:
            target_username_display = username
            target_uuid = get_uuid(username)
            if not target_uuid:
                await interaction.followup.send(f"Could not find player '{username}'. Check username.")
                return
        else:
            discord_user_id = str(interaction.user.id)
            self.registrations = self.load_registrations()
            user_accounts = self.registrations.get(discord_user_id)
            if not user_accounts:
                await interaction.followup.send("Provide Minecraft username or register.")
                return
            first_registered_account = user_accounts[0]
            target_uuid = first_registered_account['uuid']
            # Try to get display name for registered user
            temp_profiles_data = get_player_profiles(self.hypixel_api_key, format_uuid(target_uuid))
            target_username_display = f"Registered Account ({target_uuid[:8]}...)"  # Default
            if temp_profiles_data and temp_profiles_data.get("success") and temp_profiles_data.get("profiles"):
                sample_profile = temp_profiles_data["profiles"][0]
                member_data_check_dn = sample_profile.get("members", {}).get(target_uuid, {})
                player_name_dn = member_data_check_dn.get("displayname")
                if player_name_dn: target_username_display = player_name_dn

        uuid_dashed = format_uuid(target_uuid)
        profiles_data_full = get_player_profiles(self.hypixel_api_key, uuid_dashed)
        if not profiles_data_full or not profiles_data_full.get("success", False):
            await interaction.followup.send(f"Failed to retrieve Skyblock profiles for {target_username_display}.")
            return
        profiles = profiles_data_full.get("profiles", [])
        if not profiles:
            await interaction.followup.send(
                f"No Skyblock profiles found for {target_username_display}.")  # Changed from target_uuid
            return

        target_profile = None
        if profile_name:
            target_profile = find_profile_by_name(profiles_data_full, profile_name)
            if not target_profile:
                await interaction.followup.send(f"Profile '{profile_name}' not found for {target_username_display}.")
                return
        else:  # Find last played or selected
            target_profile = next((p for p in profiles if p.get("selected")), None)
            if not target_profile and profiles:  # Fallback to most recent save if no "selected"
                profiles.sort(key=lambda p: p.get("members", {}).get(target_uuid, {}).get("last_save", 0), reverse=True)
                target_profile = profiles[0]
            if not target_profile:  # Should not happen if profiles list is not empty
                await interaction.followup.send(
                    f"Could not determine a suitable profile for {target_username_display}.")
                return

        profile_cute_name = target_profile.get("cute_name", "Unknown Profile")
        profile_internal_id = target_profile.get("profile_id")
        member_data_check_display = target_profile.get("members", {}).get(target_uuid,
                                                                          {})  # Re-check display name from actual target profile
        player_name_final = member_data_check_display.get("displayname")
        if player_name_final: target_username_display = player_name_final

        if profile_internal_id is None:
            print(
                f"Warning: Could not get internal profile ID for '{profile_cute_name}' ({target_uuid}). Clock buff/notifications may not work correctly.")

        member_data = target_profile.get("members", {}).get(target_uuid, {})
        forge_time_level = member_data.get("mining_core", {}).get("nodes", {}).get("forge_time")
        time_reduction_percent = calculate_quick_forge_reduction(forge_time_level)
        perk_message = f" (Quick Forge: -{time_reduction_percent:.1f}%)" if time_reduction_percent > 0 else ""
        forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})

        clock_is_actively_buffing_single = False
        if profile_internal_id: clock_is_actively_buffing_single = self.is_clock_used(target_uuid, profile_internal_id)

        if not forge_processes_data or not any(
                s.get("startTime") is not None for slots in forge_processes_data.values() for s in
                slots.values()):  # Check if any item is active
            await interaction.followup.send(
                f"No active items found in Forge on profile '{profile_cute_name}' of '{target_username_display}'{perk_message}.")
            return

        formatted_items_list_single = format_active_forge_items(
            forge_processes_data, self.forge_items_data,
            time_reduction_percent, clock_is_actively_buffing_single
        )
        single_profile_data = {
            "uuid": target_uuid, "profile_id": profile_internal_id,
            "username": target_username_display, "profile_name": profile_cute_name,
            "perk_message": perk_message, "items_raw": forge_processes_data,
            "time_reduction_percent": time_reduction_percent,
        }
        embed = create_forge_embed(single_profile_data, "\n".join(
            formatted_items_list_single) if formatted_items_list_single else "No active items found.")
        if clock_is_actively_buffing_single:
            clock_note = "\n*Enchanted Clock buff applied.*"
            embed.description = (embed.description or "") + clock_note
        view = SingleForgeView(single_profile_data, interaction, self.forge_items_data, self,
                               "\n".join(formatted_items_list_single))
        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(ForgeCog(bot))