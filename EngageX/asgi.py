"""
ASGI config for EngageX project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import streaming.routing

# from django.urls import path, re_path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "EngageX.settings")
django_app = get_asgi_application()


application = ProtocolTypeRouter(
    {
        "http": django_app,
        "websocket": AuthMiddlewareStack(
            URLRouter(streaming.routing.websocket_urlpatterns)
        ),
    }
)

# from streaming.consumers import sio as socketio_app
# import socketio

# application = socketio.ASGIApp(socketio_app, django_app)


# async def application(scope, receive, send):
#     if scope["type"] == "http":
#         # Handle regular HTTP requests with Django
#         await django_app(scope, receive, send)
#     elif scope["type"] == "websocket":
#         # Handle WebSocket (Socket.IO) connections
#         await socketio_app(scope, receive, send)
