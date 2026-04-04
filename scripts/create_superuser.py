#!/usr/bin/env python
"""Run with: venv/bin/python scripts/create_superuser.py"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from dotenv import load_dotenv
load_dotenv()

import django
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()
username = 'admin'
password = os.environ['DJANGO_ADMIN_PASSWORD']

if User.objects.filter(username=username).exists():
    print(f"Superuser '{username}' already exists.")
else:
    User.objects.create_superuser(username=username, email='', password=password)
    print(f"Superuser '{username}' created.")
