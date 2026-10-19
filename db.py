"""One shared MongoDB connection. PyMongo is imported lazily so tests can swap in a stand-in."""
import config

_db = None


def get_db():
    global _db
    if _db is None:
        from pymongo import MongoClient
        client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=4000)
        _db = client[config.DB_NAME]
    return _db


def set_db(database):
    """Used only by the tests."""
    global _db
    _db = database


def col(name):
    """Shortcut: col("students") -> the students collection."""
    return get_db()[name]
