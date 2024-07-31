class ModelInitialised(Exception):
  pass

class ConnectionAlert(Exception):
  def __init__(self, timestamp, duration):
    self.timestamp = timestamp
    self.duration  = duration
    super().__init__(str(timestamp) + " - A connection of " + str(duration) + " ms has been detected.")

class Model:
  def __init__(self):
    self._initState = "Uninitialised"

  def processAdv(self, timestamp):
    pass

  def isReady(self):
    pass

  def headerStr(self):
    return "Model state header"

  def initState(self):
    return self._initState

  def __str__(self):
    return "Current state of the model"