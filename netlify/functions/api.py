import sys
import os

# Ensure the project root is in the Python path so we can import app.py
# Netlify bundles included_files at their original relative paths
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import serverless_wsgi
from app import app

def handler(event, context):
    return serverless_wsgi.handle_request(app, event, context)
