from .model import Model
from .model import ModelInitialised, ConnectionAlert


class SimpleStatisticsModel(Model):
    def __init__(self):
        self.currThreshold = 0
        self.initElements = 10
        self.lastSeen = 0
        self.silenceMidpoint = 0
        super().__init__()

    def isReady(self):
        return self.initElements <= 0

    def processAdv(self, timestamp):

        try:
            timestamp = int(timestamp)
        except ValueError:
            time = timestamp.split('T')[1]
            hours, minutes, sec = time.split(':')
            timestamp = int(float(sec) * 1000) + int(minutes) * 60000 + int(hours) * 3600000

        if timestamp == 0:
            raise RuntimeWarning("Invalid timestamp")

        if self.lastSeen == 0:  # First occurrence
            self.lastSeen = timestamp
            return

        silenceDuration = timestamp - self.lastSeen
        self.lastSeen = timestamp

        if self.silenceMidpoint == 0:
            self.silenceMidpoint = silenceDuration
            return

        silenceDelta = abs(self.silenceMidpoint - silenceDuration)

        if self.initElements > 0:  # initialisation phase
            self.silenceMidpoint = (self.silenceMidpoint + silenceDuration) / 2
            self.currThreshold = silenceDelta if (silenceDelta > self.currThreshold) else self.currThreshold

            self.initElements -= 1
            if self.initElements <= 0:
                self._initState = str(self.silenceMidpoint) + "," + str(self.currThreshold)
                raise ModelInitialised()

        else:
            if silenceDelta > 2 * self.currThreshold:
                raise ConnectionAlert(timestamp, silenceDuration)
            else:
                self.silenceMidpoint = (self.silenceMidpoint + silenceDuration) / 2
                self.currThreshold = silenceDelta if (silenceDelta > self.currThreshold) else self.currThreshold

    def headerStr(self):
        return "lastTimestamp,midpoint,threshold"

    def __str__(self):
        return str(self.lastSeen) + "," + str(self.silenceMidpoint) + "," + str(self.currThreshold)
