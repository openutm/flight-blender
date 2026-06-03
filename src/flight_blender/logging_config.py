import os
import sys

from dotenv import find_dotenv, load_dotenv
from loguru import logger

ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)


def setup_loguru():
    DISABLE_JSON_LOGGING = os.getenv("DISABLE_JSON_LOGGING", 0)
    if DISABLE_JSON_LOGGING:
        logger.remove()
        logger.add(sys.stdout, serialize=False)
    else:
        logger.remove()
        logger.add(sys.stdout, serialize=True)


setup_loguru()
