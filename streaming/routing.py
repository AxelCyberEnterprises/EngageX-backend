from . import consumers
from . import consumers
from django.urls import re_path


websocket_urlpatterns = [
    re_path(r"ws/socket_server/$", consumers.LiveSessionConsumer.as_asgi()),

]
