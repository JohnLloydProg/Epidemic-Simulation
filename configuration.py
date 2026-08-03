import json
import logging
import os

LOGGER = logging.getLogger('Simulation')
__DEFAULT_CONFIG_PATH = f'/firebase_cred/{os.environ['CONFIG_FILE_NAME']}' if (os.environ.get('CLOUD', 'False') == 'True') else os.environ['CONFIG_FILE_NAME']
__config: dict = {}

def init(config_path: str = __DEFAULT_CONFIG_PATH):
    global __config
    try:
        with open(config_path) as f:
            __config = json.load(f)
    except FileNotFoundError:
        __config = None
        LOGGER.error(f"Configuration file '{config_path}' not found! Simulation won't start.")

def get(key: str, default=None):
    global __config
    if (not __config):
        raise ValueError("Configuration file not loaded. Call 'init()' first.")
    return __config.get(key, default)

def put(key: str, value):
    global __config
    __config[key] = value
