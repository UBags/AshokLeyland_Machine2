from Configuration import *
from redis import *
from utils.RedisUtils import *

trialRedisConnection = Redis(
    CosThetaConfigurator.getInstance().getRedisHost(),
    CosThetaConfigurator.getInstance().getRedisPort(),
    retry_on_timeout=True
)

sendAbortFromIOServerToQRCodeServer(trialRedisConnection)
