#!/usr/bin/env python

import argparse
import csv
import pathlib
import sys

from models import SimpleStatisticsModel, SlidingWindowModel
from models import ModelInitialised, ConnectionAlert

if __name__ == "__main__":
    _parser = argparse.ArgumentParser(
        description='Run selected detector on given capture',
    )
    _parser.add_argument("capture")
    _parser.add_argument('-d', '--detector',
                         dest='detectorID',
                         help="Set the detector to be used [simple_statistics, sliding_window].",
                         default='simple_statistics')
    _parser.add_argument('-o', '--output',
                         dest='outputFolder',
                         help="Set the output folder for analysis result files")
    _args = _parser.parse_args()

    capturePath = pathlib.Path(_args.capture)
    outputPath = pathlib.Path(_args.outputFolder) if _args.outputFolder else pathlib.Path()
    if _args.detectorID == 'simple_statistics':
        modelFactory = SimpleStatisticsModel
    elif _args.detectorID == 'sliding_window':
        modelFactory = SlidingWindowModel
    else:
        print(f"Unknown detector {_args.detectorID}.", file=sys.stderr)
        raise SystemExit(1)

    measurementName = capturePath.stem
    modelLogPath = outputPath / f"{measurementName}.model.csv"
    alertLogPath = outputPath / f"{measurementName}.alerts.csv"

    outputPath.mkdir(parents=True, exist_ok=True)

    with (capturePath.open('r') as captureFile,
          modelLogPath.open('w') as modelLogFile,
          alertLogPath.open('w') as alertLogFile):

        capture = csv.DictReader(captureFile)
        alertLog = csv.DictWriter(alertLogFile, fieldnames=['Address', 'Timestamp', 'Duration'])
        alertLog.writeheader()
        modelLogFile.write('bdaddr,' + modelFactory().headerStr() + '\n')

        models = {}

        for advertisement in capture:
            address = advertisement['Address']
            timestamp = advertisement['Timestamp']

            try:
                model = models[address]
            except KeyError:
                model = modelFactory()
                models[address] = model

            try:
                model.processAdv(timestamp)
            except ModelInitialised:
                pass
#                print(f"Model for {address} was initialised as: {model.initState()}")
            except ConnectionAlert as alert:
                alertLog.writerow({
                    'Address': address,
                    'Timestamp': alert.timestamp,
                    'Duration': alert.duration
                })
            except (RuntimeWarning, RuntimeError):
                print(f"Error occurred while processing {address} at {timestamp}.")
            finally:
                modelLogFile.write(f"{address},{str(model)}\n")
