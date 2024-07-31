import statistics

from .model import Model
from .model import ModelInitialised, ConnectionAlert


class SlidingWindowModel(Model):

    # According to Bluetooth specification, minimal interval for low duty cycle advertising is 20 ms
    BLE_LowDutyCycle_MinInterval = 20
    # Note: High duty cycle advertising has Advertising Interval =< 3.75 ms

    def __init__(self):
        self.windowSize = 11
        self.initCnt = self.windowSize  # Counter of elements for initialization of the model
        self.window = []
        self.lastSeen = 0
        super().__init__()

    def isReady(self):
        return self.initCnt <= 0

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

        # We focus on IoT Low Duty Cycle devices, so we consider all intervals shorter than minimal (defined in
        # Bluetooth Core as 20 ms) to be mistakes and ignore them
        if silenceDuration < self.BLE_LowDutyCycle_MinInterval:
            return

        if not self.isReady():  # Still initialising
            self.window.append(silenceDuration)
            self.initCnt -= 1
            if self.isReady():
                self._initState = str(self.window) + ", " + str(statistics.median(self.window))
                raise ModelInitialised()
            return

        # We suppose that the median value will be approximately the Advertising Interval
        windowMean = statistics.mean(self.window)

        # We calculate the standard deviation to allow for fluctuations of the intervals when checking for connection
        windowStdDev = statistics.stdev(self.window)

        # Two missed Advertising messages mean the whole Advertising Event was skipped,
        # so we consider it a connection. We include standard deviation to take fluctuations into account.
        if silenceDuration > 2 * windowMean + windowStdDev:
            raise ConnectionAlert(timestamp, silenceDuration)

        # Update the window
        self.window.pop(0)
        self.window.append(silenceDuration)

    def headerStr(self):
        return "lastTimestamp,window,median,std_deviation"

    def __str__(self):
        windowMean = None
        windowStdDev = None
        try:
            windowMean = statistics.mean(self.window)
            windowStdDev = statistics.stdev(self.window)
        except statistics.StatisticsError:
            pass
        return str(self.lastSeen) + "," + str(self.window) + "," + str(windowMean) + "," + str(windowStdDev)
