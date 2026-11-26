import json

MAXIMUM_AGE_LIMIT = 32767
MAXIMUM_AGE_HELP = "Use 0 to 32767 days; 0 removes the age limit."

RULE_FIELDS = {
    "name": "rule name",
    "account": "email account",
    "folder": "source folder",
    "filter_attachment_filename_include": "attachment filename filter",
    "maximum_age": "maximum email age",
    "action_parameter": "destination folder",
}
ACCOUNT_FIELDS = {
    "name": "account name",
    "imap_server": "Bridge hostname",
    "imap_port": "Bridge port",
    "imap_security": "connection security",
    "username": "Bridge username",
    "password": "Bridge password",
    "character_set": "character set",
}


def bad_request_message(endpoint: str, data: bytes) -> str:
    rule = endpoint.startswith("mail_rules/")
    account = endpoint.startswith("mail_accounts/")
    fields = RULE_FIELDS if rule else ACCOUNT_FIELDS if account else {}
    try:
        errors = json.loads(data)
    except ValueError:
        errors = {}
    if isinstance(errors, dict):
        if rule and "maximum_age" in errors:
            return "Paperless rejected the maximum email age. " + MAXIMUM_AGE_HELP
        rejected = [label for key, label in fields.items() if key in errors]
        if rejected:
            return (
                "Paperless rejected these settings: "
                + ", ".join(rejected)
                + ". Check them and retry."
            )
    if rule:
        return (
            "Paperless rejected the import rule. Check its folders, attachment filter "
            "and maximum email age."
        )
    if endpoint == "mail_accounts/test/":
        return (
            "Paperless rejected the Bridge connection test. Check the mail settings, Bridge "
            "certificate and PAPERLESS_EMAIL_ALLOW_INTERNAL_HOSTS in Paperless."
        )
    if account:
        return "Paperless rejected the email account settings. Check them and retry."
    return "Paperless rejected the request."


class PaperlessError(ValueError):
    def __init__(self, status: int, endpoint: str, data: bytes):
        self.status = status
        messages = {
            401: "Paperless rejected the API token.",
            403: "The API token needs permission to manage mail accounts and rules.",
            404: "Paperless could not find or access the requested item. "
            "Check the URL, item owner and permissions.",
            429: "Paperless is limiting requests. Wait briefly and try again.",
        }
        if status == 400:
            message = bad_request_message(endpoint, data)
        elif status == 403 and endpoint == "ui_settings/":
            message = (
                "The API token's user needs permission to view UI settings in Paperless "
                "so the app can verify who will own imported documents."
            )
        elif 300 <= status < 400:
            message = (
                "Paperless redirected the request. Enter its final URL, including "
                "HTTPS and any path prefix. Redirects are not followed."
            )
        elif status >= 500:
            message = (
                "Paperless reported a server error. Check its container logs. "
                "For a Bridge test, check the reachable Bridge address and the mounted "
                "CA file configured in PAPERLESS_EMAIL_CERTIFICATE_LOCATION."
            )
        else:
            message = messages.get(status, "Paperless rejected the request.")
        super().__init__(f"{message} (HTTP {status})")
