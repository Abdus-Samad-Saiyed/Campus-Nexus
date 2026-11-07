import os
import sys

sys.path.insert(0, os.path.abspath('S:/Campus-Project-Postgres'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'campus.settings.dev'
os.environ['DJANGO_DEBUG'] = 'True'
os.environ['DATABASE_URL'] = 'postgres://campus_admin:CampusPass123!@127.0.0.1:5432/campus_nexus'

import django
django.setup()

from django.core.management import call_command
from django.db import connection

print("Flushing PostgreSQL database...")
call_command('flush', '--no-input', '--database=default')

print("Loading data from sqlite_data_dump_utf8.json into PostgreSQL...")
call_command('loaddata', 'S:/Campus-Project/sqlite_data_dump_utf8.json', '--database=default')

print("Done loading!")
