import os, base64, re, sys
os.environ.setdefault("GMAIL_TOKEN_PATH", "/data/credentials/gmail_token.json")
os.environ.setdefault("GMAIL_CREDENTIALS_PATH", "/data/credentials/gmail_credentials.json")
sys.path.insert(0, "/app")
from main import get_gmail_service, _get_email_body_text

service = get_gmail_service()
msg = service.users().messages().get(userId="me", id="19cb35c944648d2c", format="full").execute()

# Show what _get_email_body_text returns
body = _get_email_body_text(msg["payload"])
print("=== _get_email_body_text output ===")
print(body[:500])
print()

# Show all MIME parts
def show_parts(payload, depth=0):
    mime = payload.get("mimeType", "?")
    fname = payload.get("filename", "")
    data_len = len(payload.get("body", {}).get("data", ""))
    print("  " * depth + mime + " data=" + str(data_len) + (" file=" + fname if fname else ""))
    for p in payload.get("parts", []):
        show_parts(p, depth + 1)

print("=== MIME structure ===")
show_parts(msg["payload"])

# Try getting HTML part directly
def get_html_body(payload):
    if payload.get("mimeType") == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    for p in payload.get("parts", []):
        result = get_html_body(p)
        if result:
            return result
    return ""

html = get_html_body(msg["payload"])
clean = re.sub(r"<[^>]+>", " ", html)
clean = re.sub(r"&[a-z]+;", " ", clean)
clean = re.sub(r"\s+", " ", clean).strip()
idx = clean.lower().find("aksmisele")
if idx >= 0:
    print()
    print("=== Found in HTML body ===")
    print(clean[max(0,idx-30):idx+150])
else:
    print()
    print("=== Maksmisele NOT in HTML body either ===")
    print("HTML clean (first 500):", clean[:500])
