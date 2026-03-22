"""
Run this once on the server to authenticate with your Google account.
It will print a URL — open it in any browser, approve access, paste the code back.
After this, token.json is saved and the bot uses it automatically forever
(it auto-refreshes, so you rarely need to re-run this).
"""

import os
from google_auth_oauthlib.flow import Flow
from dotenv import load_dotenv

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CLIENT_SECRET_FILE = os.getenv("GDRIVE_CLIENT_SECRET_FILE", "client_secret.json")
TOKEN_FILE = os.getenv("GDRIVE_TOKEN_FILE", "token.json")

if not os.path.exists(CLIENT_SECRET_FILE):
    print(f"\n[ERROR] {CLIENT_SECRET_FILE} not found.")
    print("Download it from Google Cloud Console:")
    print("  APIs & Services → Credentials → OAuth 2.0 Client IDs → Download JSON")
    print(f"  Save it as: {CLIENT_SECRET_FILE}\n")
    raise SystemExit(1)

flow = Flow.from_client_secrets_file(
    CLIENT_SECRET_FILE,
    scopes=SCOPES,
    redirect_uri="urn:ietf:wg:oauth:2.0:oob",
)

auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")

print("\n" + "─" * 60)
print("  Open this URL in your browser:")
print("─" * 60)
print(f"\n{auth_url}\n")
print("─" * 60)
print("  Log in with your Google account, allow access,")
print("  then copy the code shown and paste it below.")
print("─" * 60 + "\n")

code = input("Paste authorization code here: ").strip()
flow.fetch_token(code=code)
creds = flow.credentials

with open(TOKEN_FILE, "w") as f:
    f.write(creds.to_json())

print(f"\n✓ Authentication successful! Token saved to {TOKEN_FILE}")
print("You can now start the bot.\n")
