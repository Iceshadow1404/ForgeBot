import time

import discord

from constants import ENCHANTED_CLOCK_REDUCTION_MS # Assuming this is in milliseconds
from logs import logger
from utils import format_time_difference # This function might become obsolete for the end time display

# --- Discord UI Views ---

def create_forge_embed(profile_data: dict, formatted_items_string: str, page_number: int | None = None,
                       total_pages: int | None = None) -> discord.Embed:
    """Creates a discord.Embed for a single profile's active forge items."""
    logger.debug(f"Creating forge embed for profile: {profile_data.get('profile_name', 'Unknown Profile')}")
    items_description = formatted_items_string if formatted_items_string else "No active items in Forge slots."

    embed = discord.Embed(
        title=f"Forge Items for {profile_data.get('username', 'Unknown User')} on {profile_data.get('profile_name', 'Unknown Profile')}",
        description=items_description,
        color=discord.Color.blue()
    )

    if profile_data.get('perk_message'):
        embed.add_field(name="Perk", value=profile_data['perk_message'].strip(), inline=False)
        logger.debug(f"Added perk message: {profile_data['perk_message'].strip()}")


    if page_number is not None and total_pages is not None:
        embed.set_footer(text=f"Profile {page_number + 1}/{total_pages}")
        logger.debug(f"Set footer for page {page_number + 1}/{total_pages}")

    logger.debug("Forge embed created.")
    return embed


class ForgePaginationView(discord.ui.View):
    """Handles pagination for multiple forge profiles."""
    def __init__(self, forge_data_list: list, interaction: discord.Interaction, forge_items_config: dict,
                 clock_usage_cog_ref, timeout: int = 180):
        super().__init__(timeout=timeout)
        logger.debug(f"Initializing ForgePaginationView with {len(forge_data_list)} profiles.")
        self.forge_data_list = forge_data_list
        self.current_page = 0
        self.interaction = interaction
        self.forge_items_config = forge_items_config
        self.clock_usage_cog_ref = clock_usage_cog_ref

        # Assuming formatted_items is already in the desired timestamp format here
        self.embeds = [
            create_forge_embed(data, data.get("formatted_items"), i, len(self.forge_data_list))
            for i, data in enumerate(self.forge_data_list)
        ]
        logger.debug(f"Created {len(self.embeds)} embeds for pagination.")


        if len(self.embeds) <= 1:
            logger.debug("Only one embed, disabling pagination buttons.")
            for item in self.children:
                if isinstance(item, discord.ui.Button) and hasattr(item, 'label') and item.label in ["Prev", "Next"]:
                    item.disabled = True

        self.update_buttons()
        logger.debug("ForgePaginationView initialized.")


    def update_buttons(self):
        logger.debug(f"Updating pagination buttons. Current page: {self.current_page}/{len(self.embeds) - 1}")
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == len(self.embeds) - 1
        self.update_clock_button_state()
        logger.debug("Pagination buttons updated.")


    def update_clock_button_state(self):
        logger.debug("Updating clock button state.")
        current_profile_data = self.forge_data_list[self.current_page]
        profile_internal_id = current_profile_data.get("profile_id")
        profile_uuid = current_profile_data.get("uuid")
        logger.debug(f"Clock button state for Profile ID: {profile_internal_id}, UUID: {profile_uuid}")

        if profile_internal_id is None or profile_uuid is None:
            self.enchanted_clock_button.disabled = True
            logger.debug("Profile ID or UUID is None. Disabling clock button.")
            return

        raw_forge_processes = current_profile_data.get("items_raw", {})
        has_active_items = any(
            isinstance(slots_data, dict) and isinstance(slot_data, dict) and slot_data.get("startTime") is not None
            for forge_type_key, slots_data in (raw_forge_processes or {}).items()
            for slot_data in (slots_data or {}).values()
        )
        logger.debug(f"Profile has active forge items: {has_active_items}")


        is_clock_used_today = self.clock_usage_cog_ref.is_clock_used(
            profile_uuid, profile_internal_id)
        logger.debug(f"Clock used for this profile today: {is_clock_used_today}")


        self.enchanted_clock_button.disabled = not has_active_items or is_clock_used_today
        logger.debug(f"Clock button disabled: {self.enchanted_clock_button.disabled}")


    @discord.ui.button(label="Prev", style=discord.ButtonStyle.blurple)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        logger.debug(f"Prev button clicked by user {interaction.user.id}")
        if interaction.user != self.interaction.user:
            logger.warning(f"Unauthorized interaction on prev button by user {interaction.user.id}")
            await interaction.response.send_message("You can only interact with your own forge view.", ephemeral=True)
            return

        await interaction.response.defer()
        logger.debug("Deferred interaction for prev button.")

        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            logger.debug(f"Moving to previous page: {self.current_page}")
            await self.interaction.edit_original_response(embed=self.embeds[self.current_page], view=self)
            logger.debug("Edited original response with previous page embed.")

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        logger.debug(f"Next button clicked by user {interaction.user.id}")
        if interaction.user != self.interaction.user:
            logger.warning(f"Unauthorized interaction on next button by user {interaction.user.id}")
            await interaction.response.send_message("You can only interact with your own forge view.", ephemeral=True)
            return

        await interaction.response.defer()
        logger.debug("Deferred interaction for next button.")

        if self.current_page < len(self.embeds) - 1:
            self.current_page += 1
            self.update_buttons()
            logger.debug(f"Moving to next page: {self.current_page}")
            await self.interaction.edit_original_response(embed=self.embeds[self.current_page], view=self)
            logger.debug("Edited original response with next page embed.")

    @discord.ui.button(label="Enchanted Clock", style=discord.ButtonStyle.green)
    async def enchanted_clock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        logger.debug(f"Enchanted Clock button clicked by user {interaction.user.id}")
        if interaction.user != self.interaction.user:
            logger.warning(f"Unauthorized interaction on clock button by user {interaction.user.id}")
            await interaction.response.send_message("You can only interact with your own forge view.", ephemeral=True)
            return

        await interaction.response.defer()
        logger.debug("Deferred interaction for clock button.")


        current_profile_index = self.current_page
        profile_data = self.forge_data_list[current_profile_index]
        profile_internal_id = profile_data.get("profile_id")
        profile_uuid = profile_data.get("uuid")
        profile_name_display = profile_data.get("profile_name", "Unknown Profile")
        logger.debug(f"Applying clock to Profile ID: {profile_internal_id}, UUID: {profile_uuid}, Name: {profile_name_display}")


        if profile_internal_id is None or profile_uuid is None:
            logger.error("Could not identify profile for clock application.")
            await interaction.followup.send("Could not identify profile for clock application.", ephemeral=True)
            return

        if self.clock_usage_cog_ref.is_clock_used(profile_uuid, profile_internal_id):
            logger.info(f"Enchanted Clock already used for profile {profile_internal_id} ({profile_name_display}) today.")
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
            logger.info(f"No active items found to apply clock to for profile {profile_internal_id} ({profile_name_display}).")
            await interaction.followup.send("No active items in the Forge for this profile to apply the clock to.",
                                            ephemeral=True)
            return

        self.clock_usage_cog_ref.mark_clock_used(profile_uuid, profile_internal_id,
                                                 profile_data.get("profile_name", "Unknown Profile"))
        logger.info(f"Marked clock as used for profile {profile_internal_id} ({profile_name_display}).")


        updated_formatted_items = []
        current_time_ms = time.time() * 1000
        logger.debug("Recalculating forge times after clock application.")


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
                end_time_display = "Time unknown" # Default display string

                forge_item_info = self.forge_items_config.get(item_id)

                if forge_item_info and start_time_ms is not None:
                    item_name = forge_item_info.get("name", item_id)
                    base_duration_ms = forge_item_info.get("duration")

                    if base_duration_ms is not None and isinstance(base_duration_ms, (int, float)):
                        effective_duration_ms = base_duration_ms * (1 - time_reduction_percent / 100)
                        # Calculate the end time in milliseconds
                        end_time_ms = start_time_ms + effective_duration_ms
                        # Apply clock reduction to the end time
                        final_end_time_ms = end_time_ms - ENCHANTED_CLOCK_REDUCTION_MS
                        # Ensure end time is not in the past
                        final_end_time_ms = max(current_time_ms, final_end_time_ms)

                        # Convert end time to seconds for Discord timestamp
                        end_timestamp_seconds = int(final_end_time_ms / 1000)

                        # Format as Discord Unix Timestamp for local time display
                        end_time_display = f"<t:{end_timestamp_seconds}:t>"
                        logger.debug(f"Recalculated end time for {item_name} in slot {slot}: {end_time_display}")
                    else:
                        end_time_display = "Duration unknown (JSON)"
                        logger.warning(f"Recalculation: Forge item duration missing or invalid in JSON for item ID: {item_id}")

                elif start_time_ms is None:
                    end_time_display = "Start time unknown (API)"
                    logger.warning(f"Recalculation: Start time missing for item ID: {item_id} from API.")

                else:
                    end_time_display = "Duration unknown (Item data missing)"
                    logger.warning(f"Recalculation: Forge item info missing for item ID: {item_id}")


                # Update the formatted string to use the end time timestamp
                updated_formatted_items.append(
                    f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Ends at: <t:{end_timestamp_seconds}:t>")


        formatted_items_string = "\n".join(updated_formatted_items)
        self.forge_data_list[current_profile_index]["formatted_items"] = formatted_items_string
        logger.debug(f"Updated formatted items for profile {profile_internal_id}.")


        self.embeds[current_profile_index] = create_forge_embed(
            self.forge_data_list[current_profile_index],
            formatted_items_string,
            current_profile_index,
            len(self.forge_data_list)
        )
        clock_note = "\n*Enchanted Clock buff applied.*"
        self.embeds[current_profile_index].description = (self.embeds[current_profile_index].description or "") + clock_note
        logger.debug("Added clock note to embed description.")


        self.update_clock_button_state()
        await self.interaction.edit_original_response(embed=self.embeds[self.current_page], view=self)
        logger.debug("Edited original response after clock application.")


    async def on_timeout(self):
        """Disables all items in the view when the timeout occurs."""
        logger.debug("View timed out. Disabling buttons.")
        for item in self.children:
            item.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
            logger.debug("Edited original response to disable buttons on timeout.")
        except discord.NotFound:
            logger.warning("Interaction message not found when trying to disable view on timeout.")
            pass
        except Exception as e:
            logger.error(f"Error updating view on timeout: {e}", exc_info=True)


class SingleForgeView(discord.ui.View):
    """Handles displaying a single profile's forge data."""
    def __init__(self, profile_data: dict, interaction: discord.Interaction, forge_items_config: dict,
                 clock_usage_cog_ref, formatted_items_string: str, timeout: int = 180):
        super().__init__(timeout=timeout)
        logger.debug(f"Initializing SingleForgeView for profile: {profile_data.get('profile_name', 'Unknown Profile')}")
        self.profile_data = profile_data
        self.interaction = interaction
        self.forge_items_config = forge_items_config
        self.clock_usage_cog_ref = clock_usage_cog_ref
        # Assuming formatted_items is already in the desired timestamp format here
        self.formatted_items = formatted_items_string
        self.update_clock_button_state()
        logger.debug("SingleForgeView initialized.")


    def update_clock_button_state(self):
        logger.debug("Updating clock button state for SingleForgeView.")
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

        logger.debug(f"Single profile has active forge items: {has_active_items}")

        profile_internal_id = self.profile_data.get("profile_id")
        profile_uuid = self.profile_data.get("uuid")
        logger.debug(f"Single profile Clock button state for Profile ID: {profile_internal_id}, UUID: {profile_uuid}")


        is_clock_used_today = False
        if profile_internal_id and profile_uuid:
             is_clock_used_today = self.clock_usage_cog_ref.is_clock_used(profile_uuid, profile_internal_id)

        logger.debug(f"Clock used for this single profile today: {is_clock_used_today}")


        self.enchanted_clock_button.disabled = (
            not has_active_items or
            profile_internal_id is None or
            profile_uuid is None or
            is_clock_used_today
        )
        logger.debug(f"Single profile Clock button disabled: {self.enchanted_clock_button.disabled}")


    @discord.ui.button(label="Enchanted Clock", style=discord.ButtonStyle.green)
    async def enchanted_clock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        logger.debug(f"Single Forge View Enchanted Clock button clicked by user {interaction.user.id}")
        if interaction.user != self.interaction.user:
            logger.warning(f"Unauthorized interaction on single view clock button by user {interaction.user.id}")
            await interaction.response.send_message("You can only interact with your own forge view.", ephemeral=True)
            return

        await interaction.response.defer()
        logger.debug("Deferred interaction for single view clock button.")


        profile_data = self.profile_data
        profile_internal_id = profile_data.get("profile_id")
        profile_uuid = profile_data.get("uuid")
        profile_name_display = profile_data.get("profile_name", "Unknown Profile")
        logger.debug(f"Applying clock to single Profile ID: {profile_internal_id}, UUID: {profile_uuid}, Name: {profile_name_display}")


        if profile_internal_id is None or profile_uuid is None:
            logger.error("Could not identify single profile for clock application.")
            await interaction.followup.send("Could not identify profile for clock application.", ephemeral=True)
            return

        if self.clock_usage_cog_ref.is_clock_used(profile_uuid, profile_internal_id):
            logger.info(f"Enchanted Clock already used for single profile {profile_internal_id} ({profile_name_display}) today.")
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
            logger.info(f"No active items found to apply clock to for single profile {profile_internal_id} ({profile_name_display}).")
            await interaction.followup.send("No active items in the Forge for this profile to apply the clock to.",
                                            ephemeral=True)
            return

        self.clock_usage_cog_ref.mark_clock_used(profile_uuid, profile_internal_id,
                                                 profile_data.get("profile_name", "Unknown Profile"))
        logger.info(f"Marked clock as used for single profile {profile_internal_id} ({profile_name_display}).")


        current_time_ms = time.time() * 1000
        updated_formatted_items = []
        logger.debug("Recalculating single forge times after clock application.")


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
                end_time_display = "Time unknown" # Default display string


                forge_item_info = self.forge_items_config.get(item_id)

                if forge_item_info and start_time_ms is not None:
                    item_name = forge_item_info.get("name", item_id)
                    base_duration_ms = forge_item_info.get("duration")

                    if base_duration_ms is not None and isinstance(base_duration_ms, (int, float)):
                        effective_duration_ms = base_duration_ms * (1 - time_reduction_percent / 100)
                        # Calculate the end time in milliseconds
                        end_time_ms = start_time_ms + effective_duration_ms
                        # Apply clock reduction to the end time
                        final_end_time_ms = end_time_ms - ENCHANTED_CLOCK_REDUCTION_MS
                        # Ensure end time is not in the past
                        final_end_time_ms = max(current_time_ms, final_end_time_ms)


                        # Convert end time to seconds for Discord timestamp
                        end_timestamp_seconds = int(final_end_time_ms / 1000)

                        # Format as Discord Unix Timestamp for local time display
                        end_time_display = f"<t:{end_timestamp_seconds}:t>"
                        logger.debug(f"Recalculated end time for {item_name} in slot {slot} (single view): {end_time_display}")

                    else:
                        end_time_display = "Duration unknown (JSON)"
                        logger.warning(f"Recalculation (single view): Forge item duration missing or invalid in JSON for item ID: {item_id}")

                elif start_time_ms is None:
                    end_time_display = "Start time unknown (API)"
                    logger.warning(f"Recalculation (single view): Start time missing for item ID: {item_id} from API.")

                else:
                    end_time_display = "Duration unknown (Item data missing)"
                    logger.warning(f"Recalculation (single view): Forge item info missing for item ID: {item_id}")


                # Update the formatted string to use the end time timestamp
                updated_formatted_items.append(
                    f"Slot {slot} ({forge_type_key.replace('_', ' ').title()}): {item_name} - Ends at: <t:{end_timestamp_seconds}:t>")


        self.formatted_items = "\n".join(updated_formatted_items)
        logger.debug(f"Updated formatted items for single profile {profile_internal_id}.")


        embed = create_forge_embed(
            profile_data,
            self.formatted_items,
            None, None
        )
        clock_note = "\n*Enchanted Clock buff applied.*"
        embed.description = (embed.description or "") + clock_note
        logger.debug("Added clock note to single view embed description.")


        self.update_clock_button_state()
        await self.interaction.edit_original_response(embed=embed, view=self)
        logger.debug("Edited original response after clock application (single view).")


    async def on_timeout(self):
        """Disables all items in the view when the timeout occurs."""
        logger.debug("Single View timed out. Disabling buttons.")
        for item in self.children:
            item.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
            logger.debug("Edited original response to disable buttons on timeout (single view).")
        except discord.NotFound:
            logger.warning("Interaction message not found when trying to disable single view on timeout.")
            pass
        except Exception as e:
            logger.error(f"Error updating single view on timeout: {e}", exc_info=True)