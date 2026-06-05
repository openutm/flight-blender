import sys

from loguru import logger

from flight_blender.config import settings


def setup_loguru():
    if settings.DISABLE_JSON_LOGGING:
        logger.remove()
        logger.add(sys.stdout, serialize=False)
    else:
        logger.remove()
        logger.add(sys.stdout, serialize=True)


setup_loguru()
