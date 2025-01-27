# This file overrides some of the defaults to make the testing output quieter,
# while still using PostgreSQL for thoroughness.
# Use it by running ./manage.py test --settings=kive.settings_test_pg

import os

from kive.settings import *  # @UnusedWildImport

# Avoid overwriting developer data files
MEDIA_ROOT = os.path.join(MEDIA_ROOT, 'Testing')


# Disable logging to console so test output isn't polluted.
LOGGING['handlers']['console']['level'] = 'CRITICAL'

# Speed up short runs during tests.
DEFAULT_SLURM_CHECK_INTERVAL = 0.5

# fail any slurm job that reports a NODE_FAIL for longer than this time (in seconds)
NODE_FAIL_TIME_OUT_SECS = 5

FLEET_POLLING_INTERVAL = 0.1
CONFIRM_COPY_RETRIES = 5
CONFIRM_COPY_WAIT_MIN = 0.1
CONFIRM_COPY_WAIT_MAX = 0.15
CONFIRM_FILE_CREATED_RETRIES = 5
CONFIRM_FILE_CREATED_WAIT_MIN = 0.01
CONFIRM_FILE_CREATED_WAIT_MAX = 0.02

# An alternate settings file for the fleet to use.
FLEET_SETTINGS = "kive.settings_test_fleet_pg"


SANDBOX_SETUP_PREAMBLE = ""

SANDBOX_DRIVER_PREAMBLE = ""

SANDBOX_BOOKKEEPING_PREAMBLE = ""

SANDBOX_CABLE_PREAMBLE = ""
