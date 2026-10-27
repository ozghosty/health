"""One-time login for Withings and Google Health (Fitbit), saving the tokens as
GitHub Actions secrets so the scheduled workflow can fetch data.

    python scripts/setup_auth.py                 # both services
    python scripts/setup_auth.py --only withings
    python scripts/setup_auth.py --only google

Client IDs/secrets are read from the environment (WITHINGS_CLIENT_ID, ...) or asked
for. Secrets are written with the GitHub CLI, which must be logged in as the repo
owner. Standard library only.
"""

import argparse
import base64
import getpass
import hashlib
import http.server
import json
import os
import re
import secrets
import subprocess
import sys
import urllib.parse
import urllib.request
import webbrowser

PORT = 8765
REDIRECT_URI = f"http://localhost:{PORT}/callback"
# Withings only accepts an https callback at registration. Nothing listens there:
# the browser shows a connection error and the code is pasted from the address bar.
WITHINGS_REDIRECT_URI = "https://localhost/withings/callback"

WITHINGS = {
    "name": "Withings",
    "prefix": "WITHINGS",
    "authorize_url": "https://account.withings.com/oauth2_user/authorize2",
    "scope": "user.metrics",  # comma-separated if more are ever needed; a space grants nothing
    "app_help": ("Create a NEW app at https://developer.withings.com/dashboard/ for this repo. Don't reuse\n"
                 "  another project's app: refresh tokens are single-use, so two repos sharing one would\n"
                 "  keep invalidating each other."),
}
GOOGLE = {
    "name": "Google Health (Fitbit)",
    "prefix": "GOOGLE",
    "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
    "scope": " ".join(
        "https://www.googleapis.com/auth/googlehealth." + s
        for s in ("health_metrics_and_measurements.readonly", "sleep.readonly", "activity_and_fitness.readonly")
    ),
    "app_help": (
        "Follow https://developers.google.com/health/setup: enable health.googleapis.com, create a\n"
        "  'Web application' OAuth client, add yourself as a test user, then set the app to\n"
        "  'In production' (testing-mode refresh tokens expire after 7 days)."
    ),
}


def fail(msg):
    sys.exit(f"\nERROR: {msg}")


def post_form(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        fail(f"{url} returned {e.code}: {e.read().decode(errors='replace')}")


def credential(prefix, kind, secret=False):
    var = f"{prefix}_CLIENT_{kind}"
    value = os.environ.get(var) or (getpass.getpass if secret else input)(f"  {var}: ").strip()
    if not value:
        fail(f"{var} is required")
    return value


def wait_for_code(auth_url, state):
    """Open the consent page and catch the redirect on localhost."""
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({k: v[0] for k, v in q.items()})
            ok = "code" in result and result.get("state") == state
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Done. You can close this tab." if ok else "Something went wrong; check the terminal."
            self.wfile.write(f"<p style='font:16px system-ui'>{msg}</p>".encode())

        def log_message(self, *args):
            pass

    with http.server.HTTPServer(("localhost", PORT), Handler) as server:
        server.timeout = 300
        print(f"  Opening your browser. If it doesn't open, visit:\n  {auth_url}\n")
        webbrowser.open(auth_url)
        while not result:
            server.handle_request()
            if not result:
                fail("timed out waiting for the browser login")

    if "error" in result:
        fail(f"login refused: {result['error']} {result.get('error_description', '')}")
    if result.get("state") != state:
        fail("state mismatch in the redirect; start again")
    return result["code"]


def paste_code(auth_url, state):
    """For providers whose callback can't be caught locally: read the code off the
    redirected URL the user pastes in."""
    print(f"  Opening your browser. If it doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)
    print("  After you approve, the browser fails to load the callback page. That's expected.")
    pasted = input("  Paste the whole address from the address bar: ").strip()
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query).items()}
    if "error" in q:
        fail(f"login refused: {q['error']}")
    if "code" not in q:
        fail("no ?code= in that address")
    if q.get("state") != state:
        fail("state mismatch in the pasted address; start again")
    return q["code"]


def login_withings():
    cfg = WITHINGS
    print(f"\n== {cfg['name']}\n  {cfg['app_help']}\n  Callback URL to register: {WITHINGS_REDIRECT_URI}")
    client_id = credential(cfg["prefix"], "ID")
    client_secret = credential(cfg["prefix"], "SECRET", secret=True)
    state = secrets.token_urlsafe(16)
    url = cfg["authorize_url"] + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "scope": cfg["scope"],
        "redirect_uri": WITHINGS_REDIRECT_URI, "state": state,
    })
    code = paste_code(url, state)
    # Withings reports failure inside an HTTP 200; `status` is the real result
    resp = post_form("https://wbsapi.withings.net/v2/oauth2", {
        "action": "requesttoken", "grant_type": "authorization_code", "client_id": client_id,
        "client_secret": client_secret, "code": code, "redirect_uri": WITHINGS_REDIRECT_URI,
    })
    if resp.get("status") != 0:
        fail(f"Withings refused the token request (status {resp.get('status')}): {resp.get('error')}")
    print("  Withings login OK.")
    return {"WITHINGS_CLIENT_ID": client_id, "WITHINGS_CLIENT_SECRET": client_secret,
            "WITHINGS_REFRESH_TOKEN": resp["body"]["refresh_token"]}


def login_google():
    cfg = GOOGLE
    print(f"\n== {cfg['name']}\n  {cfg['app_help']}\n  Authorized redirect URI to register: {REDIRECT_URI}")
    client_id = credential(cfg["prefix"], "ID")
    client_secret = credential(cfg["prefix"], "SECRET", secret=True)
    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = cfg["authorize_url"] + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "scope": cfg["scope"],
        "redirect_uri": REDIRECT_URI, "state": state, "access_type": "offline",
        "prompt": "consent",  # always issue a refresh token
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    code = wait_for_code(url, state)
    resp = post_form("https://oauth2.googleapis.com/token", {
        "grant_type": "authorization_code", "client_id": client_id, "client_secret": client_secret,
        "code": code, "redirect_uri": REDIRECT_URI, "code_verifier": verifier,
    })
    if "refresh_token" not in resp:
        fail(f"Google didn't return a refresh token: {resp}")

    # The Google account must be linked to a Google Health / Fitbit profile
    req = urllib.request.Request("https://health.googleapis.com/v4/users/me/identity",
                                 headers={"Authorization": f"Bearer {resp['access_token']}"})
    try:
        urllib.request.urlopen(req, timeout=30).close()
    except urllib.error.HTTPError as e:
        fail(f"Google Health identity check returned {e.code}: {e.read().decode(errors='replace')}\n"
             "  Sign in to the Google Health app with this Google account and migrate your Fitbit account first.")
    print("  Google Health login OK.")
    return {"GOOGLE_CLIENT_ID": client_id, "GOOGLE_CLIENT_SECRET": client_secret,
            "GOOGLE_REFRESH_TOKEN": resp["refresh_token"]}


def gh(*args, stdin=None):
    try:
        r = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    except FileNotFoundError:
        fail("the GitHub CLI (gh) isn't installed: https://cli.github.com/")
    if r.returncode:
        fail(f"gh {' '.join(args[:2])} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def target_repo(explicit):
    if explicit:
        return explicit
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True).stdout
    m = re.search(r"github\.com[/:]([^/]+/[^/.\s]+)", url)
    if not m:
        fail("couldn't work out the GitHub repo from `git remote`; pass --repo owner/name")
    return m.group(1)


def check_gh_account(repo):
    """Secrets must be written by the repo owner's own account, never another identity."""
    owner = repo.split("/")[0]
    login = gh("api", "user", "--jq", ".login")
    if login.lower() != owner.lower():
        fail(f"the GitHub CLI is logged in as '{login}', but this repo belongs to '{owner}'.\n"
             f"  Run:  gh auth login   (as {owner}),  then  gh auth switch -u {owner}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["withings", "google"])
    ap.add_argument("--repo", help="owner/name; defaults to the origin remote")
    args = ap.parse_args()

    repo = target_repo(args.repo)
    check_gh_account(repo)  # before any login, so a wrong account wastes nothing
    print(f"Saving secrets to {repo}")

    found = {}
    if args.only in (None, "withings"):
        found.update(login_withings())
    if args.only in (None, "google"):
        found.update(login_google())

    for name, value in found.items():
        gh("secret", "set", name, "--repo", repo, stdin=value)  # value via stdin, never argv
        print(f"  set {name}")
    print(f"\nSaved {len(found)} secret(s). Check with: gh secret list --repo {repo}")


if __name__ == "__main__":
    main()
