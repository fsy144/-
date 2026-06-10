import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'inventory.db')
SQLALCHEMY_TRACK_MODIFICATIONS = False

SECRET_KEY = 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2'

DEBUG = False

MAX_CONTENT_LENGTH = 10 * 1024 * 1024