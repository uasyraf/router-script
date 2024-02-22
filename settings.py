import os
from os.path import join, dirname
from dotenv import load_dotenv


dotenv_path = join(dirname(__file__), ".env")
load_dotenv(dotenv_path)

ROUTER_USERNAME = os.environ.get("ROUTER_USERNAME")
ROUTER_PASSWORD = os.environ.get("ROUTER_PASSWORD")
NEW_ROUTER_PASSWORD = os.environ.get("NEW_ROUTER_PASSWORD")
