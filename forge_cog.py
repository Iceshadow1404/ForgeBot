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
# Define the path for the persistent clock usage data
CLOCK_USAGE_FILE = 'clock_usage.json'


# Define the time reduction for the Enchanted Clock (1 hour in milliseconds)
ENCHANTED_CLOCK_REDUCTION_MS = 60 * 60 * 1000

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
        # Only show seconds if > 0 or if duration was <1 minute
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

# Function to generate Embed for a specific profile's forge data
# Takes formatted_items directly now
def create_forge_embed(profile_data, formatted_items, page_number=None, total_pages=None):
    """Creates a discord.Embed for a single profile's active forge items."""
    items_description = formatted_items if formatted_items else "No active items in Forge slots."

    embed = discord.Embed(
        # Use the username and profile name from the provided profile_data
        title=f"Forge Items for '{profile_data['profile_name']}' on '{profile_data['username']}'",
        description=items_description,
        color=discord.Color.blue() # You can choose a different color
    )

    if profile_data['perk_message']:
         embed.add_field(name="Perk", value=profile_data['perk_message'].strip(), inline=False)

    if page_number is not None and total_pages is not None:
        embed.set_footer(text=f"Profile {page_number + 1}/{total_pages}")

    return embed

# Define the pagination view for the forge list (no arguments case)
class ForgePaginationView(discord.ui.View):
    def __init__(self, forge_data_list: list, interaction: discord.Interaction, forge_items_config: dict, clock_usage_cog_ref, timeout=180):
        super().__init__(timeout=timeout)
        self.forge_data_list = forge_data_list # List of dictionaries with profile forge data
        self.current_page = 0
        self.interaction = interaction # Store the original interaction to update the message
        self.forge_items_config = forge_items_config # Pass forge items data for recalculation
        self.clock_usage_cog_ref = clock_usage_cog_ref # Reference to the cog to access/update clock usage data

        # Pre-generate embeds
        self.embeds = [
            create_forge_embed(data, data.get("formatted_items"), i, len(self.forge_data_list))
            for i, data in enumerate(self.forge_data_list)
        ]

        # Disable buttons if there's only one page
        if len(self.embeds) <= 1:
            for button in self.children:
                if button.label in ["Prev", "Next"]:
                    button.disabled = True
        else:
             self.update_buttons()

        # Initially check and disable clock button based on persistent state
        self.update_clock_button_state()


    def update_buttons(self):
        # Disable 'Prev' button on the first page, 'Next' on the last
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == len(self.embeds) - 1
        # Update clock button state based on the current page and persistent state
        self.update_clock_button_state()


    def update_clock_button_state(self):
         # Check if the current profile has active forge items to apply the clock to
         current_profile_data = self.forge_data_list[self.current_page]
         # Check if there's any raw forge data with items that have a start time
         has_active_items = False
         raw_forge_processes = current_profile_data.get("items_raw", {})
         if raw_forge_processes:
              for forge_type_key in raw_forge_processes.keys():
                 slots_data = raw_forge_processes[forge_type_key]
                 for slot_data in slots_data.values():
                     if slot_data.get("startTime") is not None:
                         has_active_items = True
                         break
                 if has_active_items: break

         # Get the internal profile ID for the current profile
         profile_internal_id = current_profile_data.get("profile_id")
         profile_uuid = current_profile_data.get("uuid")
         if profile_internal_id is None or profile_uuid is None:
              # Should not happen if data is correctly populated, but as a safeguard
              self.enchanted_clock_button.disabled = True
              return

         # Disable clock if no active items or if marked as used and not expired in persistent storage
         self.enchanted_clock_button.disabled = not has_active_items or self.clock_usage_cog_ref.is_clock_used(profile_uuid, profile_internal_id)


    @discord.ui.button(label="Prev", style=discord.ButtonStyle.blurple)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Only the user who invoked the command can interact with buttons
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("You can only interact with your own forge view.", ephemeral=True)
            return

        await interaction.response.defer() # Defer the interaction
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            await self.interaction.edit_original_response(embed=self.embeds[self.current_page], view=self)


    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Only the user who invoked the command can interact with buttons
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("You can only interact with your own forge view.", ephemeral=True)
            return

        await interaction.response.defer() # Defer the interaction
        if self.current_page < len(self.embeds) - 1:
            self.current_page += 1
            self.update_buttons()
            await self.interaction.edit_original_response(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(label="Enchanted Clock", style=discord.ButtonStyle.green)
    async def enchanted_clock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Only the user who invoked the command can interact with buttons
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("You can only interact with your own forge view.", ephemeral=True)
            return

        await interaction.response.defer() # Defer the interaction

        current_profile_index = self.current_page
        profile_data = self.forge_data_list[current_profile_index]

        # Get the internal profile ID for the current profile
        profile_internal_id = profile_data.get("profile_id")
        profile_uuid = profile_data.get("uuid")
        if profile_internal_id is None or profile_uuid is None:
             await interaction.followup.send("Could not identify profile for clock application.", ephemeral=True)
             return


        # Check persistent storage if the clock was already used for this profile and is not expired
        if self.clock_usage_cog_ref.is_clock_used(profile_uuid, profile_internal_id):
             await interaction.followup.send("The Enchanted Clock has already been used for this profile.", ephemeral=True)
             return

        raw_forge_processes = profile_data.get("items_raw")
        time_reduction_percent = profile_data.get("time_reduction_percent", 0.0)

        # Re-check if there are currently active items before applying
        has_active_items_now = False
        if raw_forge_processes:
             for forge_type_key in raw_forge_processes.keys():
                slots_data = raw_forge_processes[forge_type_key]
                for slot_data in slots_data.values():
                    if slot_data.get("startTime") is not None:
                        has_active_items_now = True
                        break
                if has_active_items_now: break

        if not has_active_items_now:
             await interaction.followup.send("No active items in the Forge for this profile to apply the clock to.", ephemeral=True)
             return


        current_time_ms = time.time() * 1000
        updated_formatted_items = []
        clock_applied_to_items = False # Track if at least one item's time was reduced

        # Apply the persistent clock buff and recalculate remaining times
        # Mark clock as used *before* recalculating and updating Embed
        self.clock_usage_cog_ref.mark_clock_used(profile_uuid, profile_internal_id, profile_data.get("profile_name", "Unknown Profile"))
        clock_applied_to_items = True # Clock is considered applied if button is clickable and forge had items

        for forge_type_key in sorted(raw_forge_processes.keys()):
            slots_data = raw_forge_processes[forge_type_key]
            sorted_slots = sorted(slots_data.keys(), key=lambda x: int(x) if x.isdigit() else float('inf'))

            for slot in sorted_slots:
                item_data = slots_data.get(slot)
                if not item_data: continue

                item_id = item_data.get("id", "Unknown Item")
                start_time_ms = item_data.get("startTime")

                if start_time_ms is not None: # Only apply to active items
                    item_name = item_id
                    remaining_time_str = "Time unknown"

                    forge_item_info = self.forge_items_config.get(item_id) # FIX: Corrected attribute name here

                    if forge_item_info:
                        item_name = forge_item_info.get("name", item_id)
                        base_duration_ms = forge_item_info.get("duration")

                        if base_duration_ms is not None:
                            effective_duration_ms = base_duration_ms * (1 - time_reduction_percent / 100)
                            end_time_ms = start_time_ms + effective_duration_ms
                            remaining_time_ms = end_time_ms - current_time_ms

                            # Apply the clock buff
                            remaining_time_ms = max(0, remaining_time_ms - ENCHANTED_CLOCK_REDUCTION_MS)


                            remaining_time_str = format_time_difference(remaining_time_ms)
                        else:
                            remaining_time_str = "Duration unknown (JSON)"

                    elif start_time_ms is None:
                         remaining_time_str = "Start time unknown (API)"
                    else: # No forge_item_info found for the item_id
                         remaining_time_str = "Duration unknown (Item data missing)"

                    updated_formatted_items.append(f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Remaining: {remaining_time_str}")


        if clock_applied_to_items:
             # Update the stored formatted items for this page
             self.forge_data_list[current_profile_index]["formatted_items"] = "\n".join(updated_formatted_items)

             # Re-create the Embed for the current page with updated formatted items
             self.embeds[current_profile_index] = create_forge_embed(
                 self.forge_data_list[current_profile_index],
                 self.forge_data_list[current_profile_index]["formatted_items"],
                 current_profile_index,
                 len(self.forge_data_list)
             )

             # Update button state (disabling the clock button for this profile)
             self.update_clock_button_state()

             await self.interaction.edit_original_response(embed=self.embeds[self.current_page], view=self)
             # Optional: Send a confirmation message ephemerally
             # await interaction.followup.send("Enchanted Clock applied!", ephemeral=True)
        else:
             await interaction.followup.send("Failed to apply Enchanted Clock.", ephemeral=True)


    async def on_timeout(self):
        # Disable all buttons on timeout
        for button in self.children:
            button.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
        except discord.NotFound:
             pass # Interaction message might have been deleted
        except Exception as e:
             print(f"Error updating view on timeout: {e}")

# Define a single profile view (for /forge username or /forge profile_name)
class SingleForgeView(discord.ui.View):
    def __init__(self, profile_data: dict, interaction: discord.Interaction, forge_items_config: dict, clock_usage_cog_ref, formatted_items: str, timeout=180):
        super().__init__(timeout=timeout)
        self.profile_data = profile_data # Dictionary with profile forge data
        self.interaction = interaction # Store the original interaction
        self.forge_items_config = forge_items_config
        self.clock_usage_cog_ref = clock_usage_cog_ref
        self.formatted_items = formatted_items # Store initially formatted items

        # Initially check and disable clock button based on persistent state
        self.update_clock_button_state()


    def update_clock_button_state(self):
         # Check if the profile has active forge items
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

         # Get the internal profile ID for the profile
         profile_internal_id = self.profile_data.get("profile_id")
         profile_uuid = self.profile_data.get("uuid")
         if profile_internal_id is None or profile_uuid is None:
              self.enchanted_clock_button.disabled = True
              return

         # Disable clock if no active items or if marked as used and not expired
         self.enchanted_clock_button.disabled = not has_active_items or self.clock_usage_cog_ref.is_clock_used(profile_uuid, profile_internal_id)


    @discord.ui.button(label="Enchanted Clock", style=discord.ButtonStyle.green)
    async def enchanted_clock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Only the user who invoked the command can interact with buttons
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("You can only interact with your own forge view.", ephemeral=True)
            return

        await interaction.response.defer()

        profile_data = self.profile_data

        # Get the internal profile ID for the profile
        profile_internal_id = profile_data.get("profile_id")
        profile_uuid = profile_data.get("uuid")
        if profile_internal_id is None or profile_uuid is None:
             await interaction.followup.send("Could not identify profile for clock application.", ephemeral=True)
             return

        # Check persistent storage if the clock was already used for this profile and is not expired
        if self.clock_usage_cog_ref.is_clock_used(profile_uuid, profile_internal_id):
             await interaction.followup.send("The Enchanted Clock has already been used for this profile.", ephemeral=True)
             return

        raw_forge_processes = profile_data.get("items_raw")
        time_reduction_percent = profile_data.get("time_reduction_percent", 0.0)

        # Re-check if there are currently active items before applying
        has_active_items_now = False
        if raw_forge_processes:
             for forge_type_key in raw_forge_processes.keys():
                slots_data = raw_forge_processes[forge_type_key]
                for slot_data in slots_data.values():
                    if slot_data.get("startTime") is not None:
                        has_active_items_now = True
                        break
                if has_active_items_now: break


        if not has_active_items_now:
             await interaction.followup.send("No active items in the Forge for this profile to apply the clock to.", ephemeral=True)
             return


        current_time_ms = time.time() * 1000
        updated_formatted_items = []
        clock_applied_to_items = False

        # Apply the persistent clock buff and recalculate remaining times
        # Mark clock as used *before* recalculating and updating Embed
        self.clock_usage_cog_ref.mark_clock_used(profile_uuid, profile_internal_id, profile_data.get("profile_name", "Unknown Profile"))
        clock_applied_to_items = True # Clock is considered applied if button is clickable and forge had items


        for forge_type_key in sorted(raw_forge_processes.keys()):
            slots_data = raw_forge_processes[forge_type_key]
            sorted_slots = sorted(slots_data.keys(), key=lambda x: int(x) if x.isdigit() else float('inf'))

            for slot in sorted_slots:
                item_data = slots_data.get(slot)
                if not item_data: continue

                item_id = item_data.get("id", "Unknown Item")
                start_time_ms = item_data.get("startTime")

                if start_time_ms is not None:
                    item_name = item_id
                    remaining_time_str = "Time unknown"

                    forge_item_info = self.forge_items_config.get(item_id) # FIX: Corrected attribute name here

                    if forge_item_info:
                        item_name = forge_item_info.get("name", item_id)
                        base_duration_ms = forge_item_info.get("duration")

                        if base_duration_ms is not None:
                            effective_duration_ms = base_duration_ms * (1 - time_reduction_percent / 100)
                            end_time_ms = start_time_ms + effective_duration_ms
                            remaining_time_ms = end_time_ms - current_time_ms

                            # Apply the clock buff
                            remaining_time_ms = max(0, remaining_time_ms - ENCHANTED_CLOCK_REDUCTION_MS)

                            remaining_time_str = format_time_difference(remaining_time_ms)
                        else:
                            remaining_time_str = "Duration unknown (JSON)"

                    elif start_time_ms is None:
                         remaining_time_str = "Start time unknown (API)"
                    else: # No forge_item_info found for the item_id
                         remaining_time_str = "Duration unknown (Item data missing)"

                    updated_formatted_items.append(f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Remaining: {remaining_time_str}")


        if clock_applied_to_items:
             # Update the stored formatted items for this page
             self.formatted_items = "\n".join(updated_formatted_items)

             # Re-create the Embed with updated formatted items
             embed = create_forge_embed(
                 profile_data,
                 self.formatted_items, # Use updated formatted items
                 None, None # No pagination footer for single view
             )

             # Add a note to the single Embed since the clock buff is now applied
             clock_note = "\n*Enchanted Clock buff applied.*"
             embed.description = embed.description + clock_note

             # Update button state (disabling the clock button for this profile)
             self.update_clock_button_state()

             await self.interaction.edit_original_response(embed=embed, view=self)
             # Optional: Send a confirmation message ephemerally
             # await interaction.followup.send("Enchanted Clock applied!", ephemeral=True)
        else:
             await interaction.followup.send("Failed to apply Enchanted Clock.", ephemeral=True)


    async def on_timeout(self):
        # Disable all buttons on timeout
        for button in self.children:
            button.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
        except discord.NotFound:
             pass
        except Exception as e:
             print(f"Error updating view on timeout: {e}")


class ForgeCog(commands.Cog, name="Forge Functions"):
    """
    This cog handles commands related to the Skyblock Forge.
    Manages persistent Enchanted Clock usage data using internal profile IDs.
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

        # Load registration data (read-only for most operations)
        self.registrations = self.load_registrations()

        # Load persistent clock usage data
        self.clock_usage = self.load_clock_usage()


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

    def load_clock_usage(self):
        """Loads persistent clock usage data from the JSON file."""
        if not os.path.exists(CLOCK_USAGE_FILE):
            print(f"Clock usage file not found: {CLOCK_USAGE_FILE}. Starting with empty data.")
            return {}
        try:
            with open(CLOCK_USAGE_FILE, 'r', encoding='utf-8') as f:
                # Ensure loaded data is a dictionary
                data = json.load(f)
                if not isinstance(data, dict):
                    print(f"ERROR: {CLOCK_USAGE_FILE} content is not a dictionary. Starting with empty data.")
                    return {}
                # Ensure the nested structure is also dictionaries and has required keys
                uuids_to_delete = []
                for uuid, profiles in list(data.items()): # Use list() to iterate safely while modifying
                     if not isinstance(profiles, dict):
                          print(f"ERROR: Data for UUID {uuid} in {CLOCK_USAGE_FILE} is not a dictionary. Clearing data for this UUID.")
                          uuids_to_delete.append(uuid)
                          continue
                     profile_ids_to_delete = []
                     for profile_id, profile_data in list(profiles.items()): # Use list() here too
                          # Check for required keys and correct types
                          if not isinstance(profile_data, dict) or "end_timestamp" not in profile_data or not isinstance(profile_data.get("end_timestamp"), (int, float)) or "profile_name" not in profile_data or not isinstance(profile_data.get("profile_name"), str):
                               print(f"ERROR: Data for profile {profile_id} under UUID {uuid} is corrupt. Clearing this profile entry.")
                               profile_ids_to_delete.append(profile_id)

                     for profile_id in profile_ids_to_delete:
                          del profiles[profile_id]

                     # If the UUID entry is now empty, mark it for deletion
                     if not profiles:
                          uuids_to_delete.append(uuid)

                for uuid in uuids_to_delete:
                    del data[uuid]

                return data
        except json.JSONDecodeError:
            print(f"ERROR: Could not decode {CLOCK_USAGE_FILE}. File might be corrupt. Starting with empty data.")
            return {}
        except Exception as e:
            print(f"An unexpected error occurred loading {CLOCK_USAGE_FILE}: {e}")
            return {}


    def save_clock_usage(self):
        """Saves persistent clock usage data to the JSON file."""
        try:
            # Ensure directory exists if saving for the first time
            os.makedirs(os.path.dirname(CLOCK_USAGE_FILE) or '.', exist_ok=True)
            temp_file = CLOCK_USAGE_FILE + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self.clock_usage, f, indent=4)
            os.replace(temp_file, CLOCK_USAGE_FILE)
            # print("Clock usage data saved.") # Optional log
        except Exception as e:
            print(f"ERROR: Could not save {CLOCK_USAGE_FILE}: {e}")

    def is_clock_used(self, uuid: str, profile_internal_id: str) -> bool:
        """Checks if the clock is marked as used and not expired for a profile in persistent storage."""
        profile_data = self.clock_usage.get(uuid, {}).get(profile_internal_id)
        if profile_data:
            end_timestamp = profile_data.get("end_timestamp")
            if end_timestamp is not None:
                current_time_ms = time.time() * 1000
                return current_time_ms < end_timestamp
        return False # Not found or timestamp missing/expired

    def mark_clock_used(self, uuid: str, profile_internal_id: str, profile_cute_name: str):
        """Marks the clock as used for a profile with an expiry timestamp and saves."""
        current_time_ms = time.time() * 1000
        end_timestamp = current_time_ms + ENCHANTED_CLOCK_REDUCTION_MS # Buff lasts for 1 hour from now

        if uuid not in self.clock_usage:
             self.clock_usage[uuid] = {}

        self.clock_usage[uuid][profile_internal_id] = {
            "profile_name": profile_cute_name,
            "end_timestamp": end_timestamp
        }
        self.save_clock_usage()

    def reset_clock_usage(self, uuid: str, profile_internal_id: str):
        """Resets the clock usage state for a profile by removing the entry and saves."""
        if uuid in self.clock_usage and profile_internal_id in self.clock_usage.get(uuid, {}): # Use .get for safety
            del self.clock_usage[uuid][profile_internal_id]
            # If the UUID now has no profiles with clock usage, remove the UUID entry
            if not self.clock_usage[uuid]:
                 del self.clock_usage[uuid]
            self.save_clock_usage()


    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.__class__.__name__} Cog loaded and ready.")
        # Reload data on ready
        self.registrations = self.load_registrations()
        self.clock_usage = self.load_clock_usage()
        # Clean up expired entries on startup
        self.cleanup_expired_clock_entries()


    def cleanup_expired_clock_entries(self):
        """Removes expired clock usage entries from storage."""
        current_time_ms = time.time() * 1000
        uuids_to_delete = []
        modified = False # Flag to indicate if clock_usage was modified

        # Create a list of UUIDs to process to avoid issues with deleting keys during iteration
        for uuid in list(self.clock_usage.keys()):
             profiles = self.clock_usage.get(uuid, {}) # Use .get for safety
             if not isinstance(profiles, dict): # Should be handled by load, but extra check
                  uuids_to_delete.append(uuid)
                  modified = True
                  continue

             profile_ids_to_delete = []
             # Create a list of profile IDs to process for the current UUID
             for profile_id in list(profiles.keys()):
                  profile_data = profiles.get(profile_id) # Use .get for safety
                  if not isinstance(profile_data, dict) or "end_timestamp" not in profile_data or not isinstance(profile_data.get("end_timestamp"), (int, float)):
                       profile_ids_to_delete.append(profile_id)
                       modified = True
                       continue # Go to next profile_id

                  end_timestamp = profile_data.get("end_timestamp")
                  if current_time_ms >= end_timestamp:
                       profile_ids_to_delete.append(profile_id)
                       modified = True

             for profile_id in profile_ids_to_delete:
                  del profiles[profile_id]

             # If the UUID entry is now empty, mark it for deletion
             if not profiles:
                  uuids_to_delete.append(uuid)
                  modified = True


        for uuid in uuids_to_delete:
             if uuid in self.clock_usage: # Check again for safety
                 del self.clock_usage[uuid]


        if modified:
             self.save_clock_usage()
             # print("Cleaned up expired clock usage entries.") # Optional log


    @app_commands.command(name="forge", description="Shows the items currently in your registered or a specified player's Skyblock Forge.")
    @app_commands.describe(username="Optional: The Minecraft name of the player. Defaults to your first registered account if omitted.")
    @app_commands.describe(profile_name="Optional: A specific Skyblock profile name. Defaults to the last played profile for the targeted user.")
    async def forge_command(self, interaction: discord.Interaction, username: str = None, profile_name: str = None):
        """
        Fetches and displays the items currently in the player's Skyblock forge.
        - If no username or profile_name is provided, lists all active forges across registered accounts in an interactive Embed view.
        - If username is provided, targets that player. Defaults to their latest profile if no profile_name.
        - If profile_name is provided (with or without username), targets that specific profile.
        Includes persistent Enchanted Clock usage tracking based on internal profile ID.
        """
        if not self.hypixel_api_key:
            await interaction.response.send_message(
                "The Hypixel API key is not configured. Please inform the bot owner.",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        # Clean up expired entries at the start of the command
        self.cleanup_expired_clock_entries()


        # --- Case 1: No arguments (list all active forges in Embeds with persistent clock) ---
        if username is None and profile_name is None:
            discord_user_id = str(interaction.user.id)
            self.registrations = self.load_registrations() # Reload registrations

            user_accounts = self.registrations.get(discord_user_id)

            if not user_accounts:
                 await interaction.followup.send("You have no registered Minecraft accounts. Use `/register` to add one.")
                 return

            active_forge_profiles_data = [] # List to store data for profiles with active forges

            await interaction.followup.send("Checking registered accounts for active forges...", ephemeral=False)

            # Iterate through all registered accounts
            for account in user_accounts:
                current_uuid = account['uuid']
                uuid_dashed = format_uuid(current_uuid)

                # Fetch profiles for the current account
                profiles_data = get_player_profiles(self.hypixel_api_key, uuid_dashed)

                if not profiles_data or not profiles_data.get("success", False):
                    print(f"Warning: Could not fetch profiles for registered UUID {current_uuid}. Reason: {profiles_data.get('cause', 'Unknown')}")
                    continue # Skip to the next registered account

                profiles = profiles_data.get("profiles", [])
                if not profiles:
                    continue # Skip if no profiles found for this account

                # Attempt to get the player's current display name for this UUID from profile data
                current_username_display = f"UUID: `{current_uuid}`"
                if profiles:
                    # Take member data from the first profile found to get displayname
                    sample_profile = profiles[0]
                    member_data_check_display = sample_profile.get("members", {}).get(current_uuid, {})
                    player_name_in_profile = member_data_check_display.get("displayname")
                    if player_name_in_profile:
                         current_username_display = player_name_in_profile


                # Check each profile of this account for active forge items
                for profile in profiles:
                     profile_cute_name = profile.get("cute_name", "Unknown Profile")
                     profile_internal_id = profile.get("profile_id") # Get the stable internal profile ID

                     if profile_internal_id is None:
                          print(f"Warning: Profile {profile_cute_name} for UUID {current_uuid} is missing 'profile_id'. Skipping for clock tracking.")
                          continue # Cannot reliably track clock usage without internal ID


                     member_data = profile.get("members", {}).get(current_uuid, {})
                     forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})

                     # Check if there are actually any items with a start time in the forge
                     has_any_active_items = False
                     if forge_processes_data:
                          for forge_type_key in forge_processes_data.keys():
                             slots_data = forge_processes_data[forge_type_key]
                             for slot_data in slots_data.values():
                                 if slot_data.get("startTime") is not None:
                                     has_any_active_items = True
                                     break
                             if has_any_active_items: break


                     # Only include profiles in the list if they *currently* have active forge items
                     if has_any_active_items:
                         # Prepare data for the Embed and potential recalculation
                         forge_time_level = member_data.get("mining_core", {}).get("nodes", {}).get("forge_time")
                         time_reduction_percent = calculate_quick_forge_reduction(forge_time_level)
                         perk_applied_message = ""
                         if time_reduction_percent > 0:
                              perk_applied_message = f" (Quick Forge: -{time_reduction_percent:.1f}%)"

                         active_forge_profiles_data.append({
                             "uuid": current_uuid,
                             "profile_id": profile_internal_id, # Store internal ID
                             "username": current_username_display, # Use the display name
                             "profile_name": profile_cute_name, # Store cute name for display
                             "perk_message": perk_applied_message,
                             "items_raw": forge_processes_data, # Store raw data for recalculation
                             "time_reduction_percent": time_reduction_percent,
                             "formatted_items": [] # Placeholder
                         })


            # After checking all accounts and profiles, create and send the paginated Embeds
            if active_forge_profiles_data:
                 # Generate initial formatted items for each profile's data, applying persistent clock buff
                 current_time_ms = time.time() * 1000
                 for profile_data in active_forge_profiles_data:
                      formatted_items = []
                      raw_forge_processes = profile_data["items_raw"]
                      time_reduction_percent = profile_data["time_reduction_percent"]
                      profile_uuid = profile_data["uuid"]
                      profile_internal_id = profile_data["profile_id"]


                      # Check if clock was used for this profile and is not expired
                      clock_is_actively_buffing = self.is_clock_used(profile_uuid, profile_internal_id)


                      for forge_type_key in sorted(raw_forge_processes.keys()):
                           slots_data = raw_forge_processes[forge_type_key]
                           sorted_slots = sorted(slots_data.keys(), key=lambda x: int(x) if x.isdigit() else float('inf'))

                           for slot in sorted_slots:
                                item_data = slots_data.get(slot)
                                if not item_data: continue

                                item_id = item_data.get("id", "Unknown Item")
                                start_time_ms = item_data.get("startTime")

                                if start_time_ms is not None: # Only format active items
                                     item_name = item_id
                                     remaining_time_str = "Time unknown"

                                     forge_item_info = self.forge_items_data.get(item_id)

                                     if forge_item_info:
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
                                     else:
                                          remaining_time_str = "Duration unknown (Item data missing)"

                                     formatted_items.append(f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Remaining: {remaining_time_str}")

                      profile_data["formatted_items"] = "\n".join(formatted_items)


                 view = ForgePaginationView(
                     forge_data_list=active_forge_profiles_data,
                     interaction=interaction,
                     forge_items_config=self.forge_items_data, # Passed to the view
                     clock_usage_cog_ref=self # Pass reference to the cog
                     )

                 # Edit the initial "Checking..." message with the first embed and the view
                 await interaction.edit_original_response(content="", embed=view.embeds[0], view=view)

            else:
                 await interaction.followup.send("No active items found in the Forge across your registered accounts.")

            return # Exit the command after handling the no-arguments case
        # --- END Case 1 ---


        # --- Cases 2 and 3: Username or Profile Name IS provided (Single Profile View) ---
        # This block handles both:
        # Case 2: username provided, profile_name is None (defaults to latest)
        # Case 3: profile_name is provided (with or without username)

        target_uuid = None
        target_username_display = None # Variable to store the name for the final message


        # 1. Determine the target UUID
        if username:
            target_username_display = username
            target_uuid = get_uuid(username)
            if not target_uuid:
                await interaction.followup.send(f"Could not find Minecraft player '{username}'. Please check the username.")
                return
        else:
             # This block is reached if profile_name is NOT None but username IS None.
             discord_user_id = str(interaction.user.id)
             self.registrations = self.load_registrations()

             user_accounts = self.registrations.get(discord_user_id)

             if not user_accounts:
                  await interaction.followup.send("Please provide a Minecraft username or register your account using `/register`.")
                  return

             # Use the first registered account's UUID if only profile_name was provided
             first_registered_account = user_accounts[0]
             target_uuid = first_registered_account['uuid']
             target_username_display = f"Registered UUID: `{target_uuid}`"


        uuid_dashed = format_uuid(target_uuid)

        # 2. Get Skyblock profiles for the target UUID
        profiles_data_full = get_player_profiles(self.hypixel_api_key, uuid_dashed)

        if not profiles_data_full or not profiles_data_full.get("success", False):
            error_message = "Failed to retrieve Skyblock profiles."
            if profiles_data_full and profiles_data_full.get("cause"):
                 error_message += f" Reason: {profiles_data_full['cause']}"
            await interaction.followup.send(f"{error_message} for {target_username_display}.")
            return

        profiles = profiles_data_full.get("profiles", [])
        if not profiles:
            await interaction.followup.send(f"No Skyblock profiles found for {target_username_display}.")
            return

        # 3. Find the target profile based on profile_name or default to latest
        target_profile = None
        profile_cute_name = None
        profile_internal_id = None # Get the internal ID here too

        if profile_name:
            # Case 3: Profile name was provided, find that specific profile
            target_profile = find_profile_by_name(profiles_data_full, profile_name)
            if not target_profile:
                await interaction.followup.send(f"Profile '{profile_name}' not found for {target_username_display}.")
                return
        else:
            # Case 2: No profile name provided, find the last played profile for this UUID
            last_save_timestamp = 0
            for profile in profiles:
                 member_data_check = profile.get("members", {}).get(target_uuid, {})
                 if profile.get("selected", False): # Prioritize 'selected' flag
                     target_profile = profile
                     break
                 current_last_save = member_data_check.get("last_save", 0)
                 if current_last_save > last_save_timestamp:
                      last_save_timestamp = current_last_save
                      target_profile = profile

            if not target_profile:
                 await interaction.followup.send(f"Could not determine the last played profile for {target_username_display}.")
                 return

        profile_cute_name = target_profile.get("cute_name", "Unknown Profile")
        profile_internal_id = target_profile.get("profile_id")

        # Update display name if found in the targeted profile data
        member_data_check_display_targeted = target_profile.get("members", {}).get(target_uuid, {})
        player_name_in_profile_targeted = member_data_check_display_targeted.get("displayname")
        if player_name_in_profile_targeted:
             target_username_display = player_name_in_profile_targeted


        if profile_internal_id is None:
             # If we couldn't get the internal ID for the targeted profile, can't offer clock buff
             # Still show the forge data, just without the clock button functionality
             print(f"Warning: Could not get internal profile ID for '{profile_cute_name}' ({target_uuid}). Cannot offer Enchanted Clock buff for this view.")


        # 4. Get Quick Forge Perk Level and calculate reduction
        member_data = target_profile.get("members", {}).get(target_uuid, {})
        forge_time_level = member_data.get("mining_core", {}).get("nodes", {}).get("forge_time")

        time_reduction_percent = calculate_quick_forge_reduction(forge_time_level)
        perk_applied_message = ""
        if time_reduction_percent > 0:
             perk_applied_message = f" (Quick Forge: -{time_reduction_percent:.1f}%)"

        # 5. Extract and format Forge Data for the single profile view
        try:
            forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})

            forge_items_output = []
            current_time_ms = time.time() * 1000

            # Determine if clock was used for this specific profile and is not expired
            # Only check if we have the internal profile ID
            clock_is_actively_buffing_single = False
            if profile_internal_id:
                 clock_is_actively_buffing_single = self.is_clock_used(target_uuid, profile_internal_id)


            # Check if there are any active items to potentially show the clock button for
            has_any_active_items_single = False
            if forge_processes_data:
                 for forge_type_key in forge_processes_data.keys():
                    slots_data = forge_processes_data[forge_type_key]
                    for slot_data in slots_data.values():
                        if slot_data.get("startTime") is not None:
                            has_any_active_items_single = True
                            break
                    if has_any_active_items_single: break


            if not forge_processes_data:
                 # If no forge data at all, send basic message without view
                await interaction.followup.send(f"No active items found in the Forge on profile '{profile_cute_name}' of '{target_username_display}'{perk_applied_message}.")
                return

            # Format the items, applying the persistent clock buff if active
            for forge_type_key in sorted(forge_processes_data.keys()):
                 slots_data = forge_processes_data[forge_type_key]
                 sorted_slots = sorted(slots_data.keys(), key=lambda x: int(x) if x.isdigit() else float('inf'))

                 for slot in sorted_slots:
                    item_data = slots_data.get(slot)
                    if not item_data: continue

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

                            # Apply the persistent clock buff if active
                            if clock_is_actively_buffing_single:
                                 remaining_time_ms = max(0, remaining_time_ms - ENCHANTED_CLOCK_REDUCTION_MS)

                            remaining_time_str = format_time_difference(remaining_time_ms)
                        else:
                             remaining_time_str = "Duration unknown (JSON)"

                    elif start_time_ms is None:
                         remaining_time_str = "Start time unknown (API)"
                    else: # No forge_item_info found for the item_id
                         remaining_time_str = "Duration unknown (Item data missing)"

                    if start_time_ms is not None: # Only add items that are actually active
                         forge_items_output.append(f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Remaining: {remaining_time_str}")


            # Create profile data dictionary for creating the embed/view
            single_profile_data = {
                "uuid": target_uuid,
                "profile_id": profile_internal_id, # Include internal ID
                "username": target_username_display,
                "profile_name": profile_cute_name,
                "perk_message": perk_applied_message,
                "items_raw": forge_processes_data, # Include raw data for recalculation
                "time_reduction_percent": time_reduction_percent,
            }

            # Create the Embed for the single view using the initial formatted items
            embed = create_forge_embed(
                 single_profile_data,
                 "\n".join(forge_items_output) if forge_items_output else "No active items found in the Forge.",
                 None, None # No pagination footer
             )

            # Add a note to the single Embed if the clock buff is applied
            if clock_is_actively_buffing_single:
                 clock_note = "\n*Enchanted Clock buff applied.*"
                 embed.description = embed.description + clock_note

            # --- Always use SingleForgeView if a profile was successfully found ---
            # The view will handle enabling/disabling the button based on data
            view = SingleForgeView(
                 profile_data=single_profile_data,
                 interaction=interaction,
                 forge_items_config=self.forge_items_data, # Passed to the view
                 clock_usage_cog_ref=self,
                 formatted_items="\n".join(forge_items_output) if forge_items_output else "" # Pass initial formatted items
             )

            await interaction.followup.send(embed=embed, view=view)


        except Exception as e:
            print(f"Error processing forge data for {target_username_display} on profile {profile_cute_name}: {e}")
            await interaction.followup.send(f"An internal error occurred while retrieving or processing Forge data.")


async def setup(bot: commands.Bot):
    """Adds the ForgeCog to the bot."""
    await bot.add_cog(ForgeCog(bot))