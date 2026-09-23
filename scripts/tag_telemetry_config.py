"""Release-time configuration for Tag's dedicated CLI telemetry project.

These values intentionally remain empty in source checkouts. Release automation
stamps the approved dedicated project token, EU host, and privacy-notice URL
into published artifacts; the PostHog project token is public client
configuration rather than an administrative credential.
"""

POSTHOG_HOST = ""
POSTHOG_PROJECT_TOKEN = ""
PRIVACY_NOTICE_URL = ""
