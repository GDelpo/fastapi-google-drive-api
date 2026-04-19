"""Obtain a Google OAuth2 refresh token for Drive API access.

This is a ONE-TIME utility script.  Run it locally (NOT inside Docker)
on a machine with a web browser.  It opens a Google consent screen where
you authorise your personal account, then prints the refresh token.

Prerequisites:
  1. Go to Google Cloud Console → APIs & Services → Credentials.
  2. Create an OAuth 2.0 Client ID (type: Desktop app).
  3. Download the JSON and save it as:
       credentials/oauth_google_credentials.json

Usage:
  python refresh_token.py

After running:
  Copy the printed refresh token into your .env file:
    GOOGLE_OAUTH_CLIENT_ID=<printed client_id>
    GOOGLE_OAUTH_CLIENT_SECRET=<printed client_secret>
    GOOGLE_OAUTH_REFRESH_TOKEN=<printed refresh_token>

The refresh token does NOT expire unless you explicitly revoke it
or change the OAuth consent screen settings.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
]

flow = InstalledAppFlow.from_client_secrets_file(
    "credentials/oauth_google_credentials.json", SCOPES
)
creds = flow.run_local_server(port=0)

print()
print("=== OAuth2 Credentials ===")
print(f"GOOGLE_OAUTH_CLIENT_ID={creds.client_id}")
print(f"GOOGLE_OAUTH_CLIENT_SECRET={creds.client_secret}")
print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
print()
print("Copy the three lines above into your .env file.")