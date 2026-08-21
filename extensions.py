from flask_pymongo import PyMongo
from flask_socketio import SocketIO
from authlib.integrations.flask_client import OAuth


mongo = PyMongo()
socketio = SocketIO()
oauth = OAuth()
