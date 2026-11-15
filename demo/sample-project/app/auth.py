USERS = {"ada": "lovelace"}


def login(username, password):
    # BUG: raises KeyError for unknown users instead of returning a clean error
    return USERS[username] == password
