"""
WSGI config for EngageX project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/wsgi/
"""

import os


def application(environ, start_response):
    if 'CONTENT_LENGTH' not in environ:
        environ['CONTENT_LENGTH'] = '0'  # Set a default value

    from EngageX.wsgi import application as _application
    return _application(environ, start_response)


print("CONTENT_LENGTH:", os.environ.get('CONTENT_LENGTH'))

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'EngageX.settings')

application = get_wsgi_application()
