from . import consumers
from . import consumers1
from django.urls import re_path


websocket_urlpatterns = [
    re_path(r"ws/socket_server/$", consumers.LiveSessionConsumer.as_asgi()),
    re_path(r"ws/socket_server1/$", consumers1.LiveSessionConsumer.as_asgi()),
]
