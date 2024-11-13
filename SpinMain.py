from rpi_screen import LCD_1inch44
from machine import Pin, SPI, PWM
import utime
import asyncio
import aioble
import struct
import bluetooth

# Global variables for state
bt_connection_attempt_count = 0

bluetoothStats = None
previousSample = None
currentSample = None
hasWheel = False
hasCrank = False
startDistance = 0
wheelSize = 2.105  # Sample wheel size in meters, adjust as necessary
UINT16_MAX = 65535
UINT32_MAX = 4294967295

updateRatio = 0.1  # Smoothing factor

# Constants
SCAN_TIME_MS = 5000
SCAN_INTERVAL_US = 30000
SCAN_WINDOW_US = 30000
SLEEP_TIME_MS = 1000

# UUIDs for the bike's services and characteristics
_TARGET_DEVICE_NAME = "IC Bike"

# Cycling Speed and Cadence service
_BIKE_CYCLE_SPEED_CADENCE_UUID = bluetooth.UUID(0x1816)
_BIKE_CYCLE_SPEED_SENSOR_UUID = bluetooth.UUID(0x2A5B)  # Note: Ensure this UUID is correct
_CCCD_UUID = bluetooth.UUID(0x2902)

# Property constants
PROP_READ = 0x02
PROP_NOTIFY = 0x10


def diff_for_sample(current, previous, max_value):
    if current >= previous:
        return current - previous
    else:
        return (max_value - previous) + current


def calculate_stats():
    global previousSample, currentSample, bluetoothStats, startDistance

    if not previousSample:
        startDistance = currentSample['wheel'] * wheelSize / 1000 / 1000  # km
        return

    distance = cadence = speed = 0
    if hasWheel:
        wheel_time_diff = diff_for_sample(currentSample['wheelTime'], previousSample['wheelTime'],
                                          UINT16_MAX) / 1024  # Convert from ms to s
        wheel_diff = diff_for_sample(currentSample['wheel'], previousSample['wheel'], UINT32_MAX)

        sample_distance = wheel_diff * wheelSize / 1000  # distance in meters
        speed = 0 if wheel_time_diff == 0 else sample_distance / wheel_time_diff * 3.6  # km/hr

        distance = currentSample['wheel'] * wheelSize / 1000 / 1000  # km
        distance -= startDistance

    if hasCrank:
        crank_time_diff = diff_for_sample(currentSample['crankTime'], previousSample['crankTime'],
                                          UINT16_MAX) / 1024  # Convert from ms to s
        crank_diff = diff_for_sample(currentSample['crank'], previousSample['crank'], UINT16_MAX)

        cadence = 0 if crank_time_diff == 0 else (60 * crank_diff / crank_time_diff)  # RPM

    if bluetoothStats:
        bluetoothStats = {
            'cadence': bluetoothStats['cadence'] * (1 - updateRatio) + cadence * updateRatio,
            'distance': distance,
            'speed': bluetoothStats['speed'] * (1 - updateRatio) + speed * updateRatio
        }
    else:
        bluetoothStats = {
            'cadence': cadence,
            'distance': distance,
            'speed': speed
        }


async def find_spinbike():
    """Scan for the spin bike and return the device if found."""
    async with aioble.scan(SCAN_TIME_MS, interval_us=SCAN_INTERVAL_US, window_us=SCAN_WINDOW_US,
                           active=True) as scanner:
        async for result in scanner:
            if result.name() != None:
                print(f"{result} | {result.name()} | {result.services()}")
                if result.name() == _TARGET_DEVICE_NAME and (_BIKE_CYCLE_SPEED_CADENCE_UUID in result.services()):
                    print("Found Bike")
                    return result.device
    return None


def parse_buffer(buffer):
    global previousSample, currentSample, hasWheel, hasCrank

    if len(buffer) < 9:
        print("Buffer too small, length:", len(buffer))
        return None
    try:
        value = buffer
        flags = value[0]

        # Determine presence of wheel and crank data
        hasWheel = flags == 1 or flags == 3
        hasCrank = flags == 2 or flags == 3

        # Update samples
        previousSample = currentSample
        currentSample = {
            'wheel': struct.unpack_from('<I', value, 1)[0],  # 4 bytes from offset 1
            'wheelTime': struct.unpack_from('<H', value, 5)[0],  # 2 bytes from offset 5
            'crank': struct.unpack_from('<H', value, 7)[0],  # 2 bytes from offset 7
            'crankTime': struct.unpack_from('<H', value, 9)[0],  # 2 bytes from offset 9
        }

        # print(f'Previous Sample: {previousSample}, Current Sample: {currentSample}')

        # Calculate statistics
        calculate_stats()

        # Return structured data as needed
        return bluetoothStats

    except struct.error as e:
        print(f"Error parsing buffer: {e}")
        return None


def metric_on_screen(data):

    write_metric("Cadence", data['cadence'])

    if (key0.value() == 0):
        write_metric("Cadence", data['cadence'])

    if (key1.value() == 0):
        write_metric("Speed", data['speed'])

    if (key2.value() == 0):
        write_metric("Distance", data['distance'])

    if (key3.value() == 0):
        LCD.fill(LCD.BLACK)
        LCD.write_text("Bluetooth", x=5, y=5, size=1, color=LCD.WHITE)
        LCD.write_text("connected?", x=5, y=25, size=1, color=LCD.WHITE)
        LCD.write_text(str(bluetooth_connected), x=30, y=60, size=2, color=LCD.WHITE)


def write_metric(title, metric, title_size=2, metric_size=5):
    LCD.fill(LCD.BLACK)
    LCD.write_text(title, x=5, y=5, size=title_size, color=LCD.WHITE)
    LCD.write_text(str(metric), x=5, y=60, size=metric_size, color=LCD.WHITE)
    LCD.show()


async def main():
    global bt_connection_attempt_count
    device = await find_spinbike()
    if not device:
        LCD.fill(LCD.BLACK)
        LCD.write_text(f"{_TARGET_DEVICE_NAME}", x=0, y=0, size=1, color=LCD.WHITE)
        LCD.write_text(f" not found.", x=0, y=10, size=1, color=LCD.WHITE)
        LCD.write_text(f" Searched {bt_connection_attempt_count} times", x=0, y=40, size=1, color=LCD.WHITE)
        print(f"{_TARGET_DEVICE_NAME} not found.")
        bt_connection_attempt_count += 1
        return

    try:
        print(f"Connecting to {device}")
        connection = await device.connect()
    except asyncio.TimeoutError:
        print("Timeout during connection")
        return

    async with connection:
        try:
            cyclespeedcadence_service = await connection.service(_BIKE_CYCLE_SPEED_CADENCE_UUID)
            print(f"Discovered service: {_BIKE_CYCLE_SPEED_CADENCE_UUID}")
            cadence_characteristic = await cyclespeedcadence_service.characteristic(_BIKE_CYCLE_SPEED_SENSOR_UUID)
            print(f"Discovered characteristic: {_BIKE_CYCLE_SPEED_SENSOR_UUID}")

            properties = cadence_characteristic.properties
            print(f"Characteristic properties: {properties:08b}")

            if properties & PROP_NOTIFY:
                print("Subscribing to notifications...")

                cccd = await cadence_characteristic.descriptor(_CCCD_UUID)
                await cccd.write(b'\x01\x00')  # Enable notifications

                async def notification_handler():
                    while True:
                        data = await cadence_characteristic.notified()
                        print(f"Buffer received: {data}")
                        sensor_data = parse_buffer(data)
                        if sensor_data:
                            cadence, distance, speed = sensor_data
                            metric_on_screen(sensor_data)

                            print(f'Cadence: {cadence}, Speed: {speed}, distance: {distance}')

                        print(bluetoothStats)

                asyncio.create_task(notification_handler())

                print("Subscription request sent.")

                while connection.is_connected():
                    bluetooth_connected = True
                    await asyncio.sleep(SLEEP_TIME_MS)

            elif properties & PROP_READ:
                print("Notifications not supported, attempting to read...")
                while connection.is_connected():
                    buffer = await cadence_characteristic.read()
                    print(f"Buffer received: {buffer}")
                    sensor_data = parse_buffer(buffer)
                    if sensor_data:
                        cadence, distance, speed = sensor_data.values()

                        print(f' Cadence: {cadence}, Speed: {speed}, distance: {distance}')
                    await asyncio.sleep(SLEEP_TIME_MS)
            else:
                print("Characteristic does not support notifications or read.")
        except asyncio.TimeoutError:
            print("Timeout discovering services/characteristics.")
        except Exception as e:
            print(f"An error occurred: {e}")


if __name__ == '__main__':

    # Init Screen
    LCD = LCD_1inch44()
    # color BRG
    LCD.fill(LCD.BLACK)
    LCD.text("IC4-RPM", 17, 42, LCD.WHITE)
    LCD.text("Tim Dawson", 17, 60, LCD.WHITE)
    LCD.show()
    utime.sleep(1)

    bluetooth_connected = False
    display_metric = "Cadence"

    # Init Buttons
    key0 = Pin(15, Pin.IN, Pin.PULL_UP)
    key1 = Pin(17, Pin.IN, Pin.PULL_UP)
    key2 = Pin(2, Pin.IN, Pin.PULL_UP)
    key3 = Pin(3, Pin.IN, Pin.PULL_UP)


    LCD.show()
    asyncio.run(main())
    utime.sleep(1)
    LCD.fill(0xFFFF)













