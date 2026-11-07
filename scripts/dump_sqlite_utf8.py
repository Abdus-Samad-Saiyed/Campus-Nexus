import os
import sys
import io

sys.path.insert(0, os.path.abspath('.'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'campus.settings.dev'
os.environ['DJANGO_DEBUG'] = 'True'

import django
django.setup()

from django.core.management import call_command

with io.open('sqlite_data_dump_utf8.json', 'w', encoding='utf-8') as f:
    call_command('dumpdata', '--database=default', '--exclude=contenttypes', '--exclude=auth.permission', '--indent=2', stdout=f)

print('Dumped successfully with UTF-8!')
