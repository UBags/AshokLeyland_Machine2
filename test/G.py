import redis



redisConn = redis.Redis("localhost", 6379, retry_on_timeout=True)
data = {}
data["message"] = "message"
resp = redisConn.xadd("Q", data)


def getData():
    last_id = 0
    resp = redisConn.xread({"Q": last_id}, count=1, block=5000)
    newData = {}
    if resp:
        key, messages = resp[0]
        last_id, data = messages[0]
        keys = data.keys()
        for key in keys:
            value = data[key]
            newData[key.decode("utf-8")] = value.decode("utf-8")
        # redisConnectionLeftCameraPicture.xdel("Q", last_id)
    print(newData)

getData()
getData()
getData()

numberOfEntries = redisConn.xlen("Q")
print(numberOfEntries)

redisConn.xtrim("Q", 0)
numberOfEntries = redisConn.xlen("Q")
print(numberOfEntries)
