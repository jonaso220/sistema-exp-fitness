"""
Netlify serverless function handler.

Wraps the Flask WSGI application so it can run as an AWS Lambda function
behind Netlify's routing layer.  All HTTP requests that don't resolve to a
static file are rewritten here via the [[redirects]] rule in netlify.toml.

Dependencies and application source files are copied into this directory
by build_netlify.sh at deploy time.
"""

import base64
import sys
import os
from io import BytesIO
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# Make sure this directory is first on sys.path so that the copies of app.py,
# translations.py, and the pip-installed packages living here are found.
# ---------------------------------------------------------------------------
_this_dir = os.path.dirname(os.path.abspath(__file__))
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)

from app import app as flask_app  # noqa: E402

# Force HTTPS so url_for() generates correct links on Netlify
flask_app.config["PREFERRED_URL_SCHEME"] = "https"


# ---------------------------------------------------------------------------
# Minimal WSGI ↔ Lambda adapter
#
# Converts the event dict that Netlify (API-Gateway compatible) sends into a
# WSGI environ, invokes Flask, and packs the response back into the dict
# format Lambda expects.  This avoids pulling in serverless-wsgi / aws-wsgi
# as extra dependencies.
# ---------------------------------------------------------------------------

def _build_environ(event):
    """Convert a Netlify/Lambda proxy event into a PEP-3333 WSGI environ."""

    method = event.get("httpMethod", "GET")
    path = event.get("path", "/")
    headers = event.get("headers") or {}
    body = event.get("body") or ""
    is_base64 = event.get("isBase64Encoded", False)

    if is_base64 and body:
        body = base64.b64decode(body)
    elif isinstance(body, str):
        body = body.encode("utf-8")

    # Query string — prefer multi-value variant when available
    multi_qs = event.get("multiValueQueryStringParameters")
    if multi_qs:
        parts = []
        for key, values in multi_qs.items():
            for v in values:
                parts.append(f"{key}={v}")
        query_string = "&".join(parts)
    else:
        qs_params = event.get("queryStringParameters") or {}
        query_string = urlencode(qs_params)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "CONTENT_LENGTH": str(len(body)),
        "SERVER_NAME": headers.get("host", "localhost"),
        "SERVER_PORT": headers.get("x-forwarded-port", "443"),
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": headers.get("x-forwarded-proto", "https"),
        "wsgi.input": BytesIO(body),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }

    content_type = headers.get("content-type") or headers.get("Content-Type", "")
    if content_type:
        environ["CONTENT_TYPE"] = content_type

    # Map HTTP headers → CGI-style variables
    for key, value in headers.items():
        cgi_key = "HTTP_" + key.upper().replace("-", "_")
        environ[cgi_key] = value

    return environ


def handler(event, context):
    """Netlify Functions entry-point (AWS Lambda compatible)."""

    environ = _build_environ(event)

    # ---- Invoke Flask ----
    response_started = []

    def start_response(status, response_headers, exc_info=None):
        response_started.append((status, response_headers))

    result = flask_app(environ, start_response)

    status_str, response_headers = response_started[0]
    status_code = int(status_str.split(" ", 1)[0])

    # Collect response body
    body_parts = []
    try:
        for chunk in result:
            body_parts.append(chunk)
    finally:
        if hasattr(result, "close"):
            result.close()

    body_bytes = b"".join(body_parts)

    # Decide whether to base64-encode (binary content)
    headers_dict = {k: v for k, v in response_headers}
    ct = headers_dict.get("Content-Type", "")
    is_binary = not (
        ct.startswith("text/")
        or "json" in ct
        or "xml" in ct
        or "javascript" in ct
        or "css" in ct
    )

    body_str = (
        base64.b64encode(body_bytes).decode("utf-8")
        if is_binary
        else body_bytes.decode("utf-8", errors="replace")
    )

    # Build multi-value headers (important for Set-Cookie / CSRF tokens)
    multi_value_headers = {}
    for hdr_name, hdr_value in response_headers:
        lname = hdr_name.lower()
        multi_value_headers.setdefault(lname, []).append(hdr_value)

    return {
        "statusCode": status_code,
        "multiValueHeaders": multi_value_headers,
        "body": body_str,
        "isBase64Encoded": is_binary,
    }
