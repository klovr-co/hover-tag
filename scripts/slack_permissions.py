"""Actionable permission failures; recovery never changes Slack permissions."""
import re
import webbrowser

try:
    import setup_ui as ui
except ImportError:
    from scripts import setup_ui as ui


class MissingScope(RuntimeError):
    def __init__(self, method, needed=""):
        self.method = method
        scopes = [s.strip() for s in str(needed).split(",")]
        self.needed = ", ".join(s for s in scopes if re.fullmatch(r"[a-z_]+(?::[a-z_]+)+", s))
        reasons = {
            "bots.info": "confirm the bot belongs to the selected app",
            "apps.connections.open": "connect the app through Socket Mode",
            "conversations.list": "show available channels for selection",
            "conversations.join": "add Tag to the public channels you selected",
            "conversations.info": "check the selected channel and bot membership",
            "conversations.history": "validate the separate Slack-history credential",
        }
        reason = reasons.get(method, "complete this Slack check")
        super().__init__(f"Slack permission needed · {method}\nMissing: {self.needed or 'not specified by Slack'}\nTag needs this to {reason}.")


def settings_url(app_id):
    return f"https://api.slack.com/apps/{app_id}" if re.fullmatch(r"A[A-Z0-9]+", app_id or "") else "https://api.slack.com/apps"


def open_settings(app_id):
    url = settings_url(app_id)
    if not webbrowser.open(url):
        ui.message(f"Open in your browser: {url}")


def guidance(error, app_id):
    ui.message("Setup is paused. Please fix this permission in Slack, or ask your workspace admin.")
    if error.method == "apps.connections.open":
        ui.message("In Basic Information → App-Level Tokens, review the Socket Mode token's connections:write scope.")
    elif error.method == "conversations.history":
        ui.message("Review the missing scopes on your separate Slack-history credential and reauthorize its app.")
        ui.message("Changing Tag's bot token does not fix a separate history credential.")
    else:
        ui.message("In OAuth & Permissions → Bot Token Scopes, review the missing scopes above, then reinstall the app.")
    ui.message(f"App settings: {settings_url(app_id)}")
    ui.message("Tag will not change permissions. Check again uses the same credential.")
    ui.message("If Slack issues a new token, save and exit, then update it privately in Tag settings before retrying.")


def recover(check, app_id):
    while True:
        try:
            return check()
        except MissingScope as error:
            print()
            ui.message(str(error))
            guidance(error, app_id)
            while True:
                choice = ui.choose("Permission check paused", ["Open app settings", "Check again", "Save and exit"])
                if choice == 0:
                    open_settings(app_id)
                elif choice == 1:
                    break
                else:
                    raise ui.Paused()
