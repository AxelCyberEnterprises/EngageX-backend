from .consumers import app as socketio_app

# The Socket.IO app will be mounted at the root URL
urlpatterns = []  # Empty since we're using Socket.IO's built-in routing 