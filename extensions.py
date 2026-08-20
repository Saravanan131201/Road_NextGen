from flask_pymongo import PyMongo
from flask_socketio import SocketIO
from authlib.integrations.flask_client import OAuth
from flask_mail import Mail

mongo = PyMongo()
socketio = SocketIO()
oauth = OAuth()
mail = Mail()