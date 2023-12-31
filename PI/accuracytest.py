import sys
import time
from typing import Optional
import evdev
from evdev import ecodes
import serial

def get_board_device() -> Optional[evdev.InputDevice]:
    """ Return the Wii Balance Board device. """
    devices = [
        path
        for path in evdev.list_devices()
        if evdev.InputDevice(path).name == "Nintendo Wii Remote Balance Board"
    ]
    if not devices:
        return None

    board = evdev.InputDevice(
        devices[0],
    )
    return board


def get_raw_measurement(device: evdev.InputDevice):
    """Read one measurement from the board."""
    data = [None] * 4
    length = 228
    width = 433
    while True:
        event = device.read_one()
        if event is None:
            continue
        # Measurements are in decigrams, so we convert them to kilograms here.
        if event.code == ecodes.ABS_HAT1X:
            # Top left.
            data[0] = event.value
        elif event.code == ecodes.ABS_HAT0X:
            # Top right.
            data[1] = event.value
        elif event.code == ecodes.ABS_HAT0Y:
            # Bottom left.
            data[2] = event.value
        elif event.code == ecodes.ABS_HAT1Y:
            # Bottom right.
            data[3] = event.value
        elif event.code == ecodes.BTN_A:
            sys.exit("ERROR: User pressed board button while measuring, aborting.")
        elif event.code == ecodes.SYN_DROPPED:
            pass
        elif event.code == ecodes.SYN_REPORT and event.value == 3:
            pass
        elif event.code == ecodes.SYN_REPORT and event.value == 0:
            # TODO: optimise cpu usage when no event is received
            if None in data:
                # This measurement failed to read one of the sensors, try again.
                data = [None] * 4
                continue
            else:
                return data
        else:
            print(f"ERROR: Got unexpected event: {evdev.categorize(event)}")


if __name__ == "__main__":
    boardfound = None
    while not boardfound:
        boardfound = get_board_device()
        if boardfound:
            print("\aBalance board found, please step on.")
            break
        time.sleep(0.5)
    while True:
        try:
            print("Opening serial port...")
            with serial.Serial('/dev/ttyGS0', 9600, timeout=1) as ser:
                print(f"Serial port {ser.name} opened.")
                while True:
                    data = get_raw_measurement(boardfound)
                    ser.reset_output_buffer()
                    ser.write(f"{str(data)}\n".encode())
                    time.sleep(0.01)

        except serial.SerialException as e:
            print(f"An error occurred: {e}, trying to reconnect...")
            time.sleep(1)
