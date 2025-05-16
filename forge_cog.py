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

# Define the pagination view for the forge list
class ForgePaginationView(discord.ui.View):
    def __init__(self, embeds: list[discord.Embed], interaction: discord.Interaction, timeout=180):
        super().__init__(timeout=timeout)
        self.embeds = embeds
        self.current_page = 0
        self.interaction = interaction # Store the original interaction to update the message

        # Disable buttons if there's only one page
        if len(self.embeds) <= 1:
            for button in self.children:
                button.disabled = True
        else:
             self.update_buttons()


    def update_buttons(self):
        # Disable 'Prev' button on the first page, 'Next' on the last
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == len(self.embeds) - 1

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.blurple)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)
        else:
             await interaction.response.defer() # Ignore button press if already on first page


    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < len(self.embeds) - 1:
            self.current_page += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)
        else:
             await interaction.response.defer() # Ignore button press if already on last page

    async def on_timeout(self):
        # Disable all buttons on timeout
        for button in self.children:
            button.disabled = True
        try:
            # Attempt to update the message with disabled buttons
            await self.interaction.edit_original_response(view=self)
        except discord.NotFound:
             # Interaction message might have been deleted
             pass
        except Exception as e:
             print(f"Error updating view on timeout: {e}")


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


    @app_commands.command(name="forge", description="Shows the items currently in your registered or a specified player's Skyblock Forge.")
    @app_commands.describe(username="Optional: The Minecraft name of the player. Defaults to your first registered account if omitted.")
    @app_commands.describe(profile_name="Optional: A specific Skyblock profile name. Defaults to the last played profile for the targeted user.")
    async def forge_command(self, interaction: discord.Interaction, username: str = None, profile_name: str = None):
        """
        Fetches and displays the items currently in the player's Skyblock forge.
        - If no username or profile_name is provided, lists all active forges across registered accounts in an interactive Embed view.
        - If username is provided, targets that player. Defaults to their latest profile if no profile_name.
        - If profile_name is provided (with or without username), targets that specific profile.
        """
        if not self.hypixel_api_key:
            await interaction.response.send_message(
                "The Hypixel API key is not configured. Please inform the bot owner.",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        # --- NEW LOGIC: Handle case with no arguments (list all active forges in Embeds) ---
        if username is None and profile_name is None:
            discord_user_id = str(interaction.user.id)
            self.registrations = self.load_registrations() # Reload registrations

            user_accounts = self.registrations.get(discord_user_id)

            if not user_accounts:
                 await interaction.followup.send("You have no registered Minecraft accounts. Use `/register` to add one.")
                 return

            active_forge_profiles_data = [] # List to store data for profiles with active forges

            # Indicate that the bot is checking
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

                # Attempt to get the player's current display name for this UUID
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
                     member_data = profile.get("members", {}).get(current_uuid, {})
                     forge_processes_data = member_data.get("forge", {}).get("forge_processes", {})

                     # Check if there are actually items with start times in this profile's forge data
                     has_active_items = False
                     if forge_processes_data:
                          for forge_type_key in forge_processes_data.keys():
                             slots_data = forge_processes_data[forge_type_key]
                             for slot_data in slots_data.values():
                                 if slot_data.get("startTime") is not None:
                                     has_active_items = True
                                     break
                             if has_active_items: break # Found at least one active item in this profile

                     if has_active_items:
                         # Found active forge items in this profile, collect data for Embed
                         profile_forge_items_formatted = []
                         current_time_ms = time.time() * 1000

                         # Get Quick Forge Perk Level for this profile
                         forge_time_level = member_data.get("mining_core", {}).get("nodes", {}).get("forge_time")
                         time_reduction_percent = calculate_quick_forge_reduction(forge_time_level)
                         perk_applied_message = ""
                         if time_reduction_percent > 0:
                              perk_applied_message = f" (Quick Forge: -{time_reduction_percent:.1f}%)"

                         sorted_forge_types = sorted(forge_processes_data.keys())

                         for forge_type_key in sorted_forge_types:
                             slots_data = forge_processes_data[forge_type_key]
                             # Sort slots numerically
                             sorted_slots = sorted(slots_data.keys(), key=lambda x: int(x) if x.isdigit() else float('inf'))

                             for slot in sorted_slots:
                                 item_data = slots_data.get(slot)
                                 if not item_data: continue

                                 item_id = item_data.get("id", "Unknown Item")
                                 start_time_ms = item_data.get("startTime")

                                 if start_time_ms is not None: # Only list items that are actually active
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
                                             remaining_time_str = format_time_difference(remaining_time_ms)
                                         else:
                                              remaining_time_str = "Duration unknown (JSON)"
                                     else:
                                          remaining_time_str = "Duration unknown (Item data missing)"

                                     profile_forge_items_formatted.append(f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Remaining: {remaining_time_str}")

                         # Store the data needed for the Embed for this profile
                         active_forge_profiles_data.append({
                             "uuid": current_uuid,
                             "username": current_username_display,
                             "profile_name": profile_cute_name,
                             "perk_message": perk_applied_message,
                             "items": profile_forge_items_formatted
                         })


            # After checking all accounts and profiles, create and send the paginated Embeds
            if active_forge_profiles_data:
                 embed_list = []
                 for profile_data in active_forge_profiles_data:
                     embed = discord.Embed(
                         title=f"Forge Items for '{profile_data['profile_name']}' on '{profile_data['username']}'",
                         description="\n".join(profile_data['items']) or "No active items in Forge slots.", # Use items or a fallback
                         color=discord.Color.blue() # You can choose a different color
                     )
                     if profile_data['perk_message']:
                          embed.add_field(name="Perk", value=profile_data['perk_message'].strip(), inline=False)

                     # Optional: Add a footer with page number
                     # embed.set_footer(text=f"Profile {len(embed_list) + 1}/{len(active_forge_profiles_data)}")

                     embed_list.append(embed)

                 if embed_list:
                      view = ForgePaginationView(embeds=embed_list, interaction=interaction)
                      # Edit the initial "Checking..." message with the first embed and the view
                      await interaction.edit_original_response(content="", embed=embed_list[0], view=view)
                 else:
                      # This case should ideally not be reached if active_forge_profiles_data is not empty
                      await interaction.followup.send("No active items found in the Forge across your registered accounts.")

            else:
                 await interaction.followup.send("No active items found in the Forge across your registered accounts.")

            return # Exit the command after handling the no-arguments case
        # --- END NEW LOGIC ---


        # --- EXISTING LOGIC: Handle cases where username or profile_name IS provided ---
        # This part remains largely the same as the previous version,
        # targeting a specific user/profile and sending a single message (can be an Embed too if desired).

        target_uuid = None
        target_username_display = None

        # 1. Determine the target UUID
        if username:
            target_username_display = username
            target_uuid = get_uuid(username)
            if not target_uuid:
                await interaction.followup.send(f"Could not find Minecraft player '{username}'. Please check the username.")
                return
        else:
             # This else block is reached if profile_name is NOT None but username IS None.
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
        profiles_data = get_player_profiles(self.hypixel_api_key, uuid_dashed)

        if not profiles_data or not profiles_data.get("success", False):
            error_message = "Failed to retrieve Skyblock profiles."
            if profiles_data and profiles_data.get("cause"):
                 error_message += f" Reason: {profiles_data['cause']}"
            await interaction.followup.send(f"{error_message} for {target_username_display}.")
            return

        profiles = profiles_data.get("profiles", [])
        if not profiles:
            await interaction.followup.send(f"No Skyblock profiles found for {target_username_display}.")
            return

        # 3. Find the target profile based on profile_name or default to latest
        target_profile = None
        profile_cute_name = None

        if profile_name:
            # Profile name was provided, find that specific profile
            target_profile = find_profile_by_name(profiles_data, profile_name)
            if not target_profile:
                await interaction.followup.send(f"Profile '{profile_name}' not found for {target_username_display}.")
                return
            profile_cute_name = target_profile.get("cute_name", profile_name)
        else:
            # No profile name provided, find the last played profile for this UUID
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

        # Attempt to get the player's current display name from the profile data for the specific targeted profile
        member_data_check_display_targeted = target_profile.get("members", {}).get(target_uuid, {})
        player_name_in_profile_targeted = member_data_check_display_targeted.get("displayname")
        if player_name_in_profile_targeted:
             target_username_display = player_name_in_profile_targeted


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

            if not forge_processes_data:
                await interaction.followup.send(f"No active items found in the Forge on profile '{profile_cute_name}' of '{target_username_display}'{perk_applied_message}.")
                return

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
                            remaining_time_str = format_time_difference(remaining_time_ms)
                        else:
                             remaining_time_str = "Duration unknown (JSON)"

                    elif start_time_ms is None:
                         remaining_time_str = "Start time unknown (API)"
                    else: # No forge_item_info found for the item_id
                         remaining_time_str = "Duration unknown (Item data missing)"


                    if start_time_ms is not None: # Only add items that are actually active
                         forge_items_output.append(f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Remaining: {remaining_time_str}")

            if not forge_items_output:
                 await interaction.followup.send(f"No active items found in the Forge on profile '{profile_cute_name}' of '{target_username_display}'{perk_applied_message}.")
                 return

            # Send a single Embed for the specific user/profile case
            embed = discord.Embed(
                title=f"Current items in the Forge",
                description=f"Profile: '{profile_cute_name}' of '{target_username_display}'{perk_applied_message}\n\n" + "\n".join(forge_items_output),
                color=discord.Color.green() # Different color for single profile view
            )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            print(f"Error processing forge data for {target_username_display} on profile {profile_cute_name}: {e}")
            await interaction.followup.send(f"An internal error occurred while retrieving or processing Forge data.")


async def setup(bot: commands.Bot):
    """Adds the ForgeCog to the bot."""
    await bot.add_cog(ForgeCog(bot))