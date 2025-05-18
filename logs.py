import logging
import colorlog

# Define the log format with color placeholders
LOG_FORMAT = "%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s%(reset)s"
COLOR_FORMATTER = colorlog.ColoredFormatter(
    LOG_FORMAT,
    datefmt='%Y-%m-%d %H:%M:%S',
    log_colors={
        'DEBUG':    'cyan',
        'INFO':     'green',
        'WARNING':  'yellow',
        'ERROR':    'red',
        'CRITICAL': 'red,bg_white',
    }
)

# Get the root logger
logger = logging.getLogger()
# Set the minimum logging level (e.g., DEBUG, INFO, WARNING, ERROR)
logger.setLevel(logging.INFO) # You can change this to logging.DEBUG for more verbose output

# Create a console handler
console_handler = logging.StreamHandler()
# Set the formatter for the handler
console_handler.setFormatter(COLOR_FORMATTER)

# Add the handler to the logger
# Prevent adding multiple handlers if the cog is reloaded
if not logger.handlers:
    logger.addHandler(console_handler)

# Now get a logger for this module (optional but good practice)
logger = logging.getLogger(__name__)