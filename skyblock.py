# skyblock.py

import requests
import json
import os
import time # Import time for rate limit handling

# Helper function to get a player's UUID from their username
def get_uuid(username):
    """Fetches the player's Mojang UUID."""
    try:
        response = requests.get(f"https://api.mojang.com/users/profiles/minecraft/{username}")
        response.raise_for_status() # Raise an HTTPError for bad responses (4XX or 5XX)
        return response.json()["id"]
    except requests.exceptions.RequestException as e:
        print(f"Error fetching UUID for {username}: {e}")
        return None
    except (KeyError, json.JSONDecodeError) as e:
        print(f"Error parsing UUID response for {username}: {e}")
        return None

def uuid_to_username(uuid):
    url = f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid.replace('-', '')}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json().get("name")
    else:
        return None

# Helper function to format UUID with dashes (for Hypixel API)
def format_uuid(uuid_str):
    """Formats a 32-character UUID string with dashes."""
    if not uuid_str or len(uuid_str) != 32:
        return uuid_str  # Return as is if not a valid non-dashed UUID
    return f"{uuid_str[0:8]}-{uuid_str[8:12]}-{uuid_str[12:16]}-{uuid_str[16:20]}-{uuid_str[20:32]}"

# Function to get player's SkyBlock profile data
def get_player_profiles(api_key, player_uuid_dashed):
    """Fetches SkyBlock profiles using the Hypixel API."""
    try:
        response = requests.get(
            "https://api.hypixel.net/v2/skyblock/profiles",
            params={"key": api_key, "uuid": player_uuid_dashed}
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429: # Rate limit
            print("Rate limit hit for player profiles. Waiting 60 seconds...")
            time.sleep(60)
            # Retry the request - Be cautious with potentially infinite loops
            # Consider adding a retry counter in a real-world scenario
            return get_player_profiles(api_key, player_uuid_dashed)
        print(f"Error fetching profiles for UUID {player_uuid_dashed}: {e.response.status_code} - {e.response.text}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Exception fetching profiles for UUID {player_uuid_dashed}: {str(e)}")
        return None
    except json.JSONDecodeError as e:
        print(f"Exception decoding profile JSON for UUID {player_uuid_dashed}: {str(e)}")
        return None

# New helper function to find a profile by name
def find_profile_by_name(profiles_data, profile_name):
    """Finds a specific SkyBlock profile by its cute_name."""
    if not profiles_data or not profiles_data.get("success", False):
        return None

    profiles = profiles_data.get("profiles", [])
    if not profiles:
        return None

    for profile in profiles:
        if profile.get("cute_name", "").lower() == profile_name.lower():
            # Return the full profile data for this profile
            return profile

    return None # Profile not found
