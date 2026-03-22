"""
Run this on your LOCAL machine (not the server) — it needs a browser.

Steps:
  1. Copy client_secret.json to your local machine
  2. Install deps:  pip install google-auth-oauthlib
  3. Run:           python auth.py
  4. Browser opens → log in → allow access → token.json is created
  5. Copy token.json back to the server:
       scp token.json user@your-server:/path/to/bot/token.json
"""

import os
from google_auth_oauthlib.flow import InstalledAppFlow
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

print("\nOpening browser for Google authentication...")
print("If the browser does not open, copy the URL printed below manually.\n")

flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
creds = flow.run_local_server(port=0, open_browser=True)

with open(TOKEN_FILE, "w") as f:
    f.write(creds.to_json())

print(f"\n✓ token.json saved.")
print(f"\nNow copy it to your server:")
print(f"  scp {TOKEN_FILE} user@your-server:/path/to/bot/token.json\n")
