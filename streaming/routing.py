from . import consumers2
from django.urls import re_path


websocket_urlpatterns = [
    re_path(r"ws/socket_server/$", consumers2.LiveSessionConsumer.as_asgi()),
]
