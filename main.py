import asyncio
import aioble
import struct
import bluetooth

# Global variables for state
previousSample = None
currentSample = None
hasWheel = False
hasCrank = False
startDistance = 0
wheelSize = 2.105  # Sample wheel size in meters, adjust as necessary
UINT16_MAX = 65535
UINT32_MAX = 4294967295

bluetoothStats = None
updateRatio = 0.1  # Smoothing factor


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


# Constants
SCAN_TIME_MS = 5000
SCAN_INTERVAL_US = 30000
SCAN_WINDOW_US = 30000
SLEEP_TIME_MS = 1000

# 1826 - 2AD2


# UUIDs for the bike's services and characteristics
_TARGET_DEVICE_NAME = "IC Bike"

# Cycling Speed and Cadence service
_BIKE_CYCLE_SPEED_CADENCE_UUID = bluetooth.UUID(0x1816)
_BIKE_CYCLE_SPEED_SENSOR_UUID = bluetooth.UUID(0x2A5B)  # Note: Ensure this UUID is correct
_CCCD_UUID = bluetooth.UUID(0x2902)

# Property constants
PROP_READ = 0x02
PROP_NOTIFY = 0x10


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

        print(f'Previous Sample: {previousSample}, Current Sample: {currentSample}')

        # Calculate statistics
        calculate_stats()

        # Return structured data as needed
        return bluetoothStats

    except struct.error as e:
        print(f"Error parsing buffer: {e}")
        return None


async def main():
    device = await find_spinbike()
    if not device:
        print(f"{_TARGET_DEVICE_NAME} not found.")
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
                        parse_buffer(data)
                        print(bluetoothStats)

                asyncio.create_task(notification_handler())

                print("Subscription request sent.")

                while connection.is_connected():
                    await asyncio.sleep(SLEEP_TIME_MS)

            elif properties & PROP_READ:
                print("Notifications not supported, attempting to read...")
                while connection.is_connected():
                    buffer = await cadence_characteristic.read()
                    print(f"Buffer received: {buffer}")
                    sensor_data = parse_buffer(buffer)
                    if sensor_data:
                        power, cadence, speed, heart_rate = sensor_data
                        print(f'Power: {power}, Cadence: {cadence}, Speed: {speed}, Heart Rate: {heart_rate}')
                    await asyncio.sleep(SLEEP_TIME_MS)
            else:
                print("Characteristic does not support notifications or read.")
        except asyncio.TimeoutError:
            print("Timeout discovering services/characteristics.")
        except Exception as e:
            print(f"An error occurred: {e}")


asyncio.run(main())



