#!/usr/bin/env python

import argparse
import configparser
import csv
import pathlib
import serial
import struct
import sys
import time
import threading
import typing

from datetime import datetime
from scapy.layers.bluetooth import HCI_Hdr, HCI_PHDR_Hdr
from scapy.utils import PcapWriter

__CONFIG_NAME__ = "collector.ini"
__DEFAULT_BAUD__ = 115200


write_lock = threading.Lock()
start_cond = threading.Condition()


def esp_init(conn: serial.Serial) -> None:
    """
    Reset the ESP and wait for the main loop to start
    """
    name = threading.current_thread().name
    
    # Toggle Data Terminal Ready to reset the ESP chip for synchronization
    conn.dtr = False
    conn.dtr = True
    conn.reset_input_buffer()   # Drop pre-reset messages

    # Wait for the main loop
    try:
        while True:
            message = conn.readline()
            if message.startswith(b'entry '):    # entry 0xhex denotes the start of the main loop
                break
    except OSError as e:
        with write_lock:
            print(f'{name}: Error ({e})', flush=True, file=sys.stderr)


def log_timing_info(conn: serial.Serial, writer: csv.DictWriter) -> None:
    name = threading.current_thread().name
    start_time = 0  # Timestamp when the timing started. (Received starting message, adjusted with device time.)
    last_device_time = 0    # Last timing info received from the listening device.
    last_collect_time = 0   # Timestamp of the last received message on the collector.
    last_device_timestamp = 0   # Timestamp of the last message from the listening device.

    with start_cond:
        start_cond.wait()

    esp_init(conn)

    with write_lock:
        print(f'- Timing from {name} started', flush=True)

    try:
        while True:
            try:
                message_raw = conn.readline()
                timestamp = time.time_ns() // 1000000   # Nanoseconds precision to milliseconds

                message = message_raw.decode('utf-8', errors='replace').strip()

                if message.startswith('Timestamp:'):
                    info = {
                        'Device': name,
                        'Collector Timestamp': timestamp,
                        'Collector Timestamp Delta': timestamp - last_collect_time
                    }
                    last_collect_time = timestamp

                    info['Device Timing'] = device_time = int(message[11:])
                    info['Device Timing Delta'] = device_time - last_device_time
                    last_device_time = device_time

                    info['Device Timestamp'] = device_timestamp = start_time + device_time
                    info['Device Timestamp Delta'] = device_timestamp - last_device_timestamp
                    last_device_timestamp = device_timestamp

                    info['Time Difference'] = timestamp - device_timestamp

                    with write_lock:
                        writer.writerow(info)

                elif message.startswith('Timing started at:'):
                    last_device_time = int(message[19:])
                    last_collect_time = time.time_ns() // 1000000   # Nanoseconds precision to milliseconds
                    start_time = last_device_timestamp = last_collect_time - last_device_time
                    with write_lock:
                        print(f'{name}: {message}', flush=True)
                else:
                    with write_lock:
                        print(f'{name}: {message}', flush=True, file=sys.stderr)
            except ValueError as e:
                with write_lock:
                    print(f'{name}: Error ({e})', flush=True, file=sys.stderr)
    except OSError as e:
        with write_lock:
            print(f'{name}: Error ({e})', flush=True, file=sys.stderr)


def log_advertising_info(conn: serial.Serial, writer: csv.DictWriter) -> None:
    name = threading.current_thread().name
    channel = 0
    start_time = 0  # Timestamp when the timing started. (Received starting message, adjusted with device time.)

    with start_cond:
        start_cond.wait()
    
    esp_init(conn)

    # Initialisation phase
    while True:
        message_raw = conn.readline()
        timestamp = time.time_ns() // 1000  # Nanoseconds precision to microseconds
        if message_raw.startswith(b'Capture started at:'):
            device_start_time = int(message_raw[20:])
            start_time = timestamp - device_start_time
        elif message_raw.startswith(b'Locked to channel:'):
            channel = int(message_raw[19:])
            break

    with write_lock:
        if channel == 0:
            print(f'- {name}: Capture started', flush=True)
        else:
            print(f'- {name}: Capture of channel {channel} started', flush=True)

    # Capture phase
    try:
        while True:
            try:
                msg_start = conn.read(4)
                if msg_start == b'Adv:':
                    advertising_info = get_advertising_info_from_serial(conn)
                    advertising_info['Timestamp'] = datetime.fromtimestamp(
                        (start_time + advertising_info['Timestamp'])
                        / 1000000  # Timestamp shall be in seconds
                    ).isoformat()
                    with write_lock:
                        writer.writerow(advertising_info)
                else:   # Transmission error, no start sequence present
                    raise ValueError(f"Message starts with 0x{msg_start.hex()}")
            except ValueError as e:
                with write_lock:
                    print(f'{name}: Error ({e})', flush=True, file=sys.stderr)
                
                # Find the start sequence
                while True:
                    if conn.read(1) == b'A':
                        if conn.read(1) == b'd':
                            if conn.read(1) == b'v':
                                if conn.read(1) == b':':
                                    break
                # Process the packet
                advertising_info = get_advertising_info_from_serial(conn)
                advertising_info['Timestamp'] = datetime.fromtimestamp(
                    (start_time + advertising_info['Timestamp'])
                    / 1000000  # Timestamp shall be in seconds
                )
                with write_lock:
                    writer.writerow(advertising_info)
    except OSError as e:
        with write_lock:
            print(f'{name}: Error ({e})', flush=True, file=sys.stderr)


def get_advertising_info_from_serial(conn: serial.Serial):
    timestamp_raw = conn.read(8)
    timestamp = struct.unpack('<q', timestamp_raw)[0]

    bdaddr_raw = conn.read(6)  # BDADDR size is 6 bytes
    bdaddr = bdaddr_raw[::-1].hex(':')

    bdaddr_type_raw = conn.read(1)
    bdaddr_type = struct.unpack('<B', bdaddr_type_raw)[0]

    event_type_raw = conn.read(1)
    event_type = struct.unpack('<B', event_type_raw)[0]

    channel_raw = conn.read(1)
    channel = struct.unpack('<B', channel_raw)[0]

    rssi_raw = conn.read(1)
    rssi = struct.unpack('<b', rssi_raw)[0]

    name_len_raw = conn.read(1)
    name_len = struct.unpack('<B', name_len_raw)[0]

    name_raw = conn.read(name_len)
    name = name_raw.decode('utf8', errors='replace')  # Bluetooth Core Version 5.4 Vol. 4 Part E - 6.23

    return {
        'Timestamp': timestamp,
        'Address': bdaddr,
        'AddressType': bdaddr_type,
        'AdvertisingType': event_type,
        'Channel': channel,
        'RSSI': rssi,
        'DeviceName': name
    }


def log_raw_packets(conn: serial.Serial, out: typing.BinaryIO) -> None:
    name = threading.current_thread().name
    channel = 0
    start_time = 0  # Timestamp when the timing started. (Received starting message, adjusted with device time.)

    with start_cond:
        start_cond.wait()

    esp_init(conn)

    # Initialisation phase
    while True:
        message_raw = conn.readline()
        timestamp = time.time_ns() // 1000  # Nanoseconds precision to microseconds
        if message_raw.startswith(b'Capture started at:'):
            device_start_time = int(message_raw[20:])
            start_time = timestamp - device_start_time
        elif message_raw.startswith(b'Locked to channel:'):
            channel = int(message_raw[19:])
            break
    # Wait for the Scan Start message
    while True:
        msg_start = conn.read(4)
        if msg_start == b'BLE:':
            conn.read(8)  # Timestamp, not needed yet
            length_raw = conn.read(2)
            length = struct.unpack('<H', length_raw)[0]
            data = conn.read(length)
            if data == b'\x04\x0e\x04\x05\x0c\x20\x00':  # LE Set Scan Enable Complete
                break

    with write_lock:
        if channel == 0:
            print(f'- {name}: Capture started', flush=True)
        else:
            print(f'- {name}: Capture of channel {channel} started', flush=True)

    # Capture phase
    while True:
        msg_start = conn.read(4)
        if msg_start == b'BLE:':
            packet = get_packet_from_serial(conn)
            packet.time = (start_time + packet.time) / 1000000  # Timestamp shall be in seconds
            try:
                for report in packet.reports:
                    report.rssi = channel  # FIXME: Ugly hack, but found no other way to keep the channel info
            except AttributeError:
                # Channel info is lost
                pass
            out.write(packet)
        else:   # Transmission error, no start sequence present
            with write_lock:
                print(
                    f'{name}: Error in transmission, message starts with 0x{msg_start.hex()}',
                    flush=True,
                    file=sys.stderr
                )
            # Find the start sequence
            while True:
                if conn.read(1) == b'B':
                    if conn.read(1) == b'L':
                        if conn.read(1) == b'E':
                            if conn.read(1) == b':':
                                break
            # Process the packet
            packet = get_packet_from_serial(conn)
            packet.time = (start_time + packet.time) / 1000000  # Timestamp shall be in seconds
            try:
                for report in packet.reports:
                    report.rssi = channel  # FIXME: Ugly hack, but found no other way to keep the channel info
            except AttributeError:
                # The Channel info is lost
                pass
            out.write(packet)


def get_packet_from_serial(conn: serial.Serial) -> HCI_Hdr:
    timestamp_raw = conn.read(8)
    timestamp = struct.unpack('<q', timestamp_raw)[0]

    length_raw = conn.read(2)
    length = struct.unpack('<H', length_raw)[0]

    data = conn.read(length)

    packet = HCI_PHDR_Hdr(direction=0)
    packet /= HCI_Hdr(data)
    packet.time = timestamp

    return packet


if __name__ == "__main__":
    _parser = argparse.ArgumentParser(
        description='Bluetooth Low Energy Advertising Collector',
    )
    _parser.add_argument('-c', '--config', metavar='CONF',
                         help='Configuration file'
                              ' [Default: ' + __CONFIG_NAME__ + ']',
                         default=__CONFIG_NAME__
                         )
    _parser.add_argument('-o', '--output', metavar='OUT',
                         help='File where the collected data will be stored. Will be overwritten.'
                              ' [Default: capture/YYYY-mm-dd_HH-MM.csv]',
                         default=pathlib.Path(
                             'capture',
                             time.strftime('%Y-%m-%d_%H-%M', time.gmtime()) + '.csv'
                             ),
                         )
    _parser.add_argument('-r', '--raw',
                         action='store_true',
                         help='Captures raw packets into a pcap file. (ESP modules have to be preloaded with the collector-raw code.)'
                         )
    _parser.add_argument('-t', '--timing',
                         action='store_true',
                         help='Perform only timing testing. (ESP modules have to be preloaded with the beeper code.)'
                         )
    _args = _parser.parse_args()

    _config = configparser.ConfigParser()
    _config.read(_args.config)

    threads = []

    # Prepare the output file
    _out_path = pathlib.Path(_args.output)
    if _args.raw:
        _out_path = _out_path.with_suffix('.pcap')
    _out_path.parent.mkdir(parents=True, exist_ok=True)

    if _args.raw:
        _out_file = _out_path.open('wb')
    else:
        _out_file = _out_path.open('w', buffering=1, newline='')

    _target_fn = None
    _writer = None

    if _args.timing:
        print("Performing ESP Timing Testing")
        _target_fn = log_timing_info
        _writer = csv.DictWriter(_out_file, fieldnames=[
            'Collector Timestamp', 'Collector Timestamp Delta', 'Device', 'Device Timing', 'Device Timing Delta',
            'Device Timestamp', 'Device Timestamp Delta', 'Time Difference'
        ])
        _writer.writeheader()
    elif _args.raw:
        print("Raw BLE Advertising Collection")
        _target_fn = log_raw_packets
        _writer = PcapWriter(_out_file, sync=True)
    else:
        print("BLE Advertising Collection")
        _target_fn = log_advertising_info
        _writer = csv.DictWriter(_out_file, fieldnames=[
            'Timestamp', 'Address', 'AddressType', 'AdvertisingType', 'RSSI', 'Channel', 'DeviceName'
        ])
        _writer.writeheader()

    for section in _config.sections():
        enabled = _config.getboolean(section, "enabled", fallback=True)
        if not enabled:
            continue

        _conn = serial.Serial(
            _config.get(section, "path"),
            _config.getint(section, "baud", fallback=__DEFAULT_BAUD__)
        )

        thread = threading.Thread(
            name=section,
            target=_target_fn,
            args=(
                _conn,
                _writer
            )
        )
        thread.daemon = True
        threads.append(thread)

    # Start the threads
    for thread in threads:
        thread.start()

    # Unblock all the threads at once (to minimise the delay caused by threads initialisation)
    with start_cond:
        start_cond.notify_all()

    # Endless loop until explicitly stopped
    try:
        for thread in threads:
            if thread.is_alive():
                thread.join()
        _out_file.flush()   # As the threads are always-running, this should never happen
        _out_file.close()
    except KeyboardInterrupt:
        print()  # Insert end of line (after the ^C)
        _out_file.flush()   # Probably redundant, but make sure the buffer gets written to the disk
        _out_file.close()
        if _args.timing:
            print("Stopped the ESP Timing Testing")
        else:
            print("Stopped the BLE AD Collection")
