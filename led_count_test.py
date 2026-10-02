"""Empirically find the number of addressable LEDs on the connected strip."""

import time

import serial
import serial.tools.list_ports


BAUD = 115200
MAX_PROBE_LEDS = 400


def find_arduino_port():
    for port in serial.tools.list_ports.comports():
        description = (port.description or "").lower()
        device = port.device.lower()

        if "arduino" in description or "usbmodem" in device:
            return port.device

    return None


def ask_visible(ser, index):
    ser.write(f"T,{index}\n".encode("ascii"))
    ser.flush()
    time.sleep(0.12)

    while True:
        answer = input(
            f"Is one dim white LED lit at candidate index {index}? [y/n/q] "
        ).strip().lower()

        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        if answer in ("q", "quit"):
            raise KeyboardInterrupt


def main():
    port = find_arduino_port()

    if port is None:
        raise RuntimeError("Arduino not found")

    print(f"Connecting to: {port}")
    print("Each step lights one candidate pixel at low brightness.")
    print("Look over the entire strip before answering; LED 0 is nearest the data input.")

    with serial.Serial(port, BAUD, timeout=0.2) as ser:
        # Opening an Uno's serial port resets it.
        time.sleep(2.0)
        ser.reset_input_buffer()

        try:
            # A monotonic binary search: if LED n exists, every lower index
            # must exist too.  The result is the first nonexistent index,
            # which is also the physical LED count.
            low = 0
            high = MAX_PROBE_LEDS

            if ask_visible(ser, MAX_PROBE_LEDS - 1):
                print(
                    f"The strip has at least {MAX_PROBE_LEDS} LEDs. "
                    "Increase MAX_PROBE_LEDS in both files and test again."
                )
                return

            high = MAX_PROBE_LEDS - 1

            if not ask_visible(ser, 0):
                print("LED 0 did not light. Check power, data direction, and pin 6 wiring.")
                return

            low = 1

            while low < high:
                candidate = (low + high) // 2

                if ask_visible(ser, candidate):
                    low = candidate + 1
                else:
                    high = candidate

            count = low
            print(f"\nEmpirical result: {count} LEDs (indices 0 through {count - 1}).")

            if count == 300:
                print("The existing NUM_LEDS 300 setting is correct.")
            else:
                print(f"Change NUM_LEDS in arduino.ino from 300 to {count}.")

        except KeyboardInterrupt:
            print("\nTest cancelled.")
        finally:
            ser.write(b"T,-1\n")
            ser.flush()
            time.sleep(0.1)


if __name__ == "__main__":
    main()
