from . import consumers, consumers1, consumers2
from django.urls import re_path


websocket_urlpatterns = [
    # re_path(r"ws/socket_server/$", consumers2.LiveSessionConsumer.as_asgi()),
    # re_path(r"ws/socket_server/$", consumers.LiveSessionConsumer.as_asgi()),
    re_path(r"ws/socket_server/$", consumers1.LiveSessionConsumer.as_asgi()),

]
