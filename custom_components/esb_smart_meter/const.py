DOMAIN = "esb_smart_meter"
NAME = "ESB Smart Meter"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_MPRN = "mprn"
CONF_UPDATE_INTERVAL = "update_interval"
DEFAULT_UPDATE_INTERVAL_HOURS = 6
MIN_UPDATE_INTERVAL_HOURS = 6
MAX_UPDATE_INTERVAL_HOURS = 48

# Bump to force a one-off clear-and-rewrite of the long-term statistics, e.g. after
# a change in how readings are mapped to hours. 2: Irish time + interval start.
STATISTICS_VERSION = 2
CONF_STATISTICS_VERSION = "statistics_version"

# Raise a repair issue once updates have been failing for this long.
FAILURE_ISSUE_AFTER_HOURS = 24
