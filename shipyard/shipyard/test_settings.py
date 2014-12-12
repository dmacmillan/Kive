# This file overrides some of the defaults to make tests run faster.
# Use it by running ./manage.py test --settings=shipyard.test_settings

from settings import *  # @UnusedWildImport

# Run with an in-memory database: about twice as fast as PostgreSQL
DATABASES['default'] = {'ENGINE': 'django.db.backends.sqlite3'}

# Disable logging so test output isn't polluted.
LOGGING['root']['handlers'] = ['file']
LOGGING['root']['level'] = 'CRITICAL'
LOGGING['loggers'] = {}
