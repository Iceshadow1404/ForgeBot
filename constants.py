import os
from dotenv import load_dotenv

load_dotenv()

BasePath = os.getenv("CONFIG_PATH", './')

# Define file paths for persistent storage
REGISTRATION_FILE = os.path.join(BasePath, "registrations.json")
CLOCK_USAGE_FILE = os.path.join(BasePath, 'clock_usage.json')
NOTIFICATIONS_FILE = os.path.join(BasePath, 'forge_notifications.json')

ENCHANTED_CLOCK_REDUCTION_MS = 60 * 60 * 1000