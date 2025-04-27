from . import consumers, consumers1, consumers2, consumers3
from django.urls import re_path


websocket_urlpatterns = [
    re_path(r"ws/socket_server/$", consumers.LiveSessionConsumer.as_asgi()),
    re_path(r"ws/socket_server1/$", consumers1.LiveSessionConsumer.as_asgi()),
    re_path(r"ws/socket_server2/$", consumers2.LiveSessionConsumer.as_asgi()),
    re_path(r"ws/socket_server3/$", consumers3.LiveSessionConsumer.as_asgi()),
]
