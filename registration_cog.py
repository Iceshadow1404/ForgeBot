# registration_cog.py

import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import asyncio # For potential async file operations or locks later

# Import necessary functions from skyblock.py
# Ensure skyblock.py is accessible
from skyblock import get_uuid, format_uuid, get_player_profiles, find_profile_by_name

# Define the path to the registration data file
from forge_cog import REGISTRATION_FILE

class RegistrationCog(commands.Cog, name="Registration Functions"):
    """
    This cog handles user registration of Minecraft accounts and Skyblock profiles.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Load registration data when the cog initializes
        self.registrations = self.load_registrations()
        # Optionally load the API key here if needed by registration commands (e.g., verifying profiles)
        self.hypixel_api_key = os.getenv("HYPIXEL_API_KEY")
        if not self.hypixel_api_key:
            print("WARNING: HYPIXEL_API_KEY not found. Profile verification in registration may be limited.")


    def load_registrations(self):
        """Loads registration data from the JSON file."""
        if not os.path.exists(REGISTRATION_FILE):
            print(f"Registration file not found: {REGISTRATION_FILE}. Starting with empty registrations.")
            return {}
        try:
            with open(REGISTRATION_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"ERROR: Could not decode {REGISTRATION_FILE}. File might be corrupt. Starting with empty registrations.")
            return {}
        except Exception as e:
            print(f"An unexpected error occurred loading {REGISTRATION_FILE}: {e}")
            return {}

    def save_registrations(self):
        """Saves registration data to the JSON file."""
        try:
            # Use a temporary file and rename to prevent data loss on write errors
            temp_file = REGISTRATION_FILE + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self.registrations, f, indent=4)
            os.replace(temp_file, REGISTRATION_FILE)
            # print("Registrations saved.") # Optional: log saves
        except Exception as e:
            print(f"ERROR: Could not save {REGISTRATION_FILE}: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.__class__.__name__} Cog loaded and ready.")

    @app_commands.command(name="register", description="Registers a Minecraft account and optional Skyblock profile with your Discord account.")
    @app_commands.describe(minecraft_username="The Minecraft username to register.")
    @app_commands.describe(profile_name="Optional: A specific Skyblock profile name (e.g., 'Apple') to register.")
    async def register_command(self, interaction: discord.Interaction, minecraft_username: str, profile_name: str = None):
        """Registers a Minecraft account and optional Skyblock profile."""
        await interaction.response.defer(ephemeral=True) # Defer ephemerally as this is user-specific

        discord_user_id = str(interaction.user.id)

        # 1. Get UUID from username
        uuid = get_uuid(minecraft_username)
        if not uuid:
            await interaction.followup.send(f"Could not find Minecraft player '{minecraft_username}'. Please check the username.")
            return

        uuid_dashed = format_uuid(uuid)

        # 2. Verify profile if name is provided
        if profile_name and self.hypixel_api_key:
             profiles_data = get_player_profiles(self.hypixel_api_key, uuid_dashed)
             if not profiles_data or not profiles_data.get("success", False):
                 await interaction.followup.send(f"Could not retrieve Skyblock profiles for '{minecraft_username}' to verify profile '{profile_name}'. Please check the username or API key configuration.")
                 return

             target_profile = find_profile_by_name(profiles_data, profile_name)
             if not target_profile:
                 await interaction.followup.send(f"Profile '{profile_name}' not found for '{minecraft_username}'. Please check the profile name.")
                 return
             # Optional: You could fetch all profile names and suggest correct spelling

        # 3. Update registration data
        # Ensure the user's entry exists
        if discord_user_id not in self.registrations:
            self.registrations[discord_user_id] = []

        user_registrations = self.registrations[discord_user_id]

        # Check if the UUID is already registered for this user
        existing_account = next((acc for acc in user_registrations if acc['uuid'] == uuid), None)

        if existing_account:
            # UUID already registered
            if profile_name:
                # Check if the profile is already registered for this account
                if profile_name not in existing_account.get('profiles', []):
                    # Add the new profile name
                    if 'profiles' not in existing_account:
                        existing_account['profiles'] = [] # Ensure profiles list exists
                    existing_account['profiles'].append(profile_name)
                    message = f"Successfully registered profile '{profile_name}' for Minecraft account '{minecraft_username}'."
                else:
                    message = f"Profile '{profile_name}' is already registered for Minecraft account '{minecraft_username}'."
            else:
                 # Username registered, but no specific profile requested
                 # Ensure profiles list is empty if user wants to default
                 if existing_account.get('profiles'):
                      existing_account['profiles'] = [] # Clear profiles if user wants to default
                      message = f"Successfully updated registration for '{minecraft_username}' to use the last played profile."
                 else:
                      message = f"Minecraft account '{minecraft_username}' is already registered (using last played profile)."
        else:
            # New UUID for this user
            new_account_entry = {
                "uuid": uuid,
                "profiles": [profile_name] if profile_name else [] # Add profile if provided, else empty list
            }
            user_registrations.append(new_account_entry)
            message = f"Successfully registered Minecraft account '{minecraft_username}'."
            if profile_name:
                message += f" and profile '{profile_name}'."

        # 4. Save changes
        self.save_registrations()

        await interaction.followup.send(message)


    @app_commands.command(name="unregister", description="Unregisters a Minecraft account or a specific Skyblock profile.")
    @app_commands.describe(minecraft_username="Optional: The Minecraft username to unregister.")
    @app_commands.describe(profile_name="Optional: A specific Skyblock profile name to unregister from the account.")
    async def unregister_command(self, interaction: discord.Interaction, minecraft_username: str = None, profile_name: str = None):
        """Unregisters a Minecraft account or profile."""
        await interaction.response.defer(ephemeral=True)

        discord_user_id = str(interaction.user.id)

        if discord_user_id not in self.registrations or not self.registrations[discord_user_id]:
            await interaction.followup.send("You have no registered Minecraft accounts.")
            return

        user_registrations = self.registrations[discord_user_id]

        if minecraft_username is None:
            # Unregister all accounts for the user
            # Add confirmation step
            # For simplicity here, we'll just unregister without explicit confirmation.
            # In a real bot, you'd ask for confirmation before clearing everything.
            # For now, let's implement the non-username case as clearing all.
            # A safer default might be to require username unless profile_name is also None.
            # Let's refine: if username is None, user must want to clear ALL.
            if profile_name is not None:
                 await interaction.followup.send("You must provide a Minecraft username to unregister a specific profile.")
                 return

            # Clear all registrations for this user
            self.registrations[discord_user_id] = []
            self.save_registrations()
            await interaction.followup.send("Successfully unregistered all your Minecraft accounts.")
            return

        # If username is provided, unregister specific account/profile
        uuid_to_unregister = get_uuid(minecraft_username)
        if not uuid_to_unregister:
            await interaction.followup.send(f"Could not find Minecraft player '{minecraft_username}'. Please check the username.")
            return

        # Find the account entry for the user
        account_to_modify = next((acc for acc in user_registrations if acc['uuid'] == uuid_to_unregister), None)

        if not account_to_modify:
            await interaction.followup.send(f"Minecraft account '{minecraft_username}' is not registered to your Discord account.")
            return

        # Modify the account entry
        if profile_name:
            # Unregister a specific profile
            if profile_name in account_to_modify.get('profiles', []):
                account_to_modify['profiles'].remove(profile_name)
                message = f"Successfully unregistered profile '{profile_name}' from Minecraft account '{minecraft_username}'."
                # Optional: If profiles list becomes empty, maybe remove the account entry?
                # Decide on desired behavior. Keeping the empty list means defaulting to last played.
            else:
                message = f"Profile '{profile_name}' was not registered for Minecraft account '{minecraft_username}'."
        else:
            # Unregister the entire Minecraft account
            self.registrations[discord_user_id].remove(account_to_modify)
            message = f"Successfully unregistered Minecraft account '{minecraft_username}'."

        # Save changes
        self.save_registrations()

        await interaction.followup.send(message)


    @app_commands.command(name="listregistered", description="Lists your registered Minecraft accounts and Skyblock profiles.")
    async def listregistered_command(self, interaction: discord.Interaction):
        """Lists registered accounts and profiles for the user."""
        await interaction.response.defer(ephemeral=True)

        discord_user_id = str(interaction.user.id)

        if discord_user_id not in self.registrations or not self.registrations[discord_user_id]:
            await interaction.followup.send("You have no registered Minecraft accounts.")
            return

        user_registrations = self.registrations[discord_user_id]

        response_message = "Your Registered Minecraft Accounts and Profiles:\n"

        if not user_registrations:
             response_message += "  (None)\n"
        else:
            for account in user_registrations:
                # Fetch current username for UUID for better display
                current_username = "Unknown User"
                uuid_dashed = format_uuid(account['uuid'])
                # This would require an API call to Mojang's API to get username from UUID history
                # For simplicity, we'll just display the UUID for now, or you can implement that lookup
                # print(f"Attempting to get username for UUID: {account['uuid']}") # Debug
                # try:
                #     # Example (requires a function in skyblock.py or similar)
                #     # from skyblock import get_username_from_uuid # You'd need to add this function
                #     # username_data = get_username_from_uuid(account['uuid'])
                #     # if username_data:
                #     #     current_username = username_data # Assuming it returns the latest username
                #     pass # Skipping username lookup for now to keep it simple
                # except Exception as e:
                #      print(f"Could not get username for UUID {account['uuid']}: {e}")
                #      current_username = f"UUID: {account['uuid']}" # Fallback to UUID

                # For a quick display, let's just show the UUID or implement a simple lookup later
                response_message += f"- Account UUID: `{account['uuid']}`\n" # Display UUID directly for now

                profiles = account.get('profiles') # Use .get for safety
                if profiles:
                    response_message += "  Registered Profiles: " + ", ".join(profiles) + "\n"
                else:
                    response_message += "  Registered Profiles: None (Using last played profile)\n"

        await interaction.followup.send(response_message)


async def setup(bot: commands.Bot):
    """Adds the RegistrationCog to the bot."""
    # Ensure Hypixel API key is available before loading cog if verification is needed
    # key_check = os.getenv("HYPIXEL_API_KEY")
    # if not key_check:
    #     print("WARNING: HYPIXEL_API_KEY not set. Registration commands involving API verification may not work.")
    await bot.add_cog(RegistrationCog(bot))