import sys, os
# Ensure repo root is on sys.path
sys.path.insert(0, os.path.dirname(__file__))
# Import the Flask app defined in run.py and expose it as `application`
from run import app as application
