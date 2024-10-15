import asyncio
import aioble
import struct
import bluetooth

# Disable E402 (module level import not at top of file)
# ruff: noqa: E402
# Constants
SCAN_TIME_MS = 5000
SCAN_INTERVAL_US = 30000
SCAN_WINDOW_US = 30000
SLEEP_TIME_MS = 1000
# UUIDs for the bike's services and characteristics
# 1816 - 2A5B CSC Measurement (Notify)
# 1826 - 2AD2 Fitness Machine (Notify)
# 1826 - 2ACC Fitness Machine (Read)
_TARGET_DEVICE_NAME = "IC Bike"
_BIKE_CYCLE_SPEED_CADENCE_UUID = bluetooth.UUID(0x1816)
_BIKE_CYCLE_SPEED_SENSOR_UUID = bluetooth.UUID(0x2A5B)  # Note: Ensure this UUID is correct
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
    """Parse the buffer and extract sensor data."""
    # Log the actual received data for debugging purposes
    print(f"Received buffer (length: {len(buffer)}): {buffer}")
    if len(buffer) < 9:
        print("Buffer too small, length:", len(buffer))
        return None
    try:
        power = struct.unpack_from('<h', buffer, 6)[0]  # 16-bit signed integer at offset 6
        cadence = struct.unpack_from('<H', buffer, 4)[0] / 2  # 16-bit unsigned integer at offset 4
        speed = struct.unpack_from('<H', buffer, 2)[0] / 100  # 16-bit unsigned integer at offset 2
        heart_rate = struct.unpack_from('<b', buffer, 8)[0]  # 8-bit signed integer at offset 8
        return power, cadence, speed, heart_rate
    except struct.error as e:
        print(f"Error parsing buffer: {e}")
        return None


async def main():
    """Main function to find, connect, and read data from the spin bike."""
    device = await find_spinbike()
    if not device:
        print(f"{_TARGET_DEVICE_NAME} not found")
        return
    try:
        print(f"Connecting to {device.address_string()}")
        connection = await device.connect()
    except asyncio.TimeoutError:
        print("Timeout during connection")
        return
    async with connection:
        try:
            # Discover the relevant service and characteristic
            cyclespeedcadence_service = None
            cadence_characteristic = None
            if _BIKE_CYCLE_SPEED_CADENCE_UUID:
                cyclespeedcadence_service = await connection.service(_BIKE_CYCLE_SPEED_CADENCE_UUID)
                if cyclespeedcadence_service:
                    print(f"Discovered service: {_BIKE_CYCLE_SPEED_CADENCE_UUID}")
                    cadence_characteristic = await cyclespeedcadence_service.characteristic(
                        _BIKE_CYCLE_SPEED_SENSOR_UUID)
                    print(f"Discovered characteristic: {_BIKE_CYCLE_SPEED_SENSOR_UUID}")

            if not cadence_characteristic:
                print("Failed to find the required characteristic.")
                return
            # Print characteristic properties
            properties = cadence_characteristic.properties
            print(f"Characteristic properties: {properties:08b}")  # Print in binary for clarity
            # If the characteristic supports NOTIFY, listen for notifications
            if properties & PROP_NOTIFY:
                print("Subscribing to notifications...")

                def notification_handler(data):
                    buffer = data
                    print(f"Buffer received: {buffer}")
                    sensor_data = parse_buffer(buffer)
                    if sensor_data:
                        power, cadence, speed, heart_rate = sensor_data
                        print(f'Power: {power}, Cadence: {cadence}, Speed: {speed}, Heart Rate: {heart_rate}')

                # Register notification handler
                cadence_characteristic.on_notify(notification_handler)
                await cadence_characteristic.subscribe(True)
                # Keep the connection alive to receive notifications
                while connection.is_connected():
                    await asyncio.sleep_ms(SLEEP_TIME_MS)
            elif properties & PROP_READ:
                # Fall back to reading if notifications are not supported
                print("Notifications not supported, attempting to read...")
                while connection.is_connected():
                    buffer = await cadence_characteristic.read()
                    print(f"Buffer received: {buffer}")
                    sensor_data = parse_buffer(buffer)
                    if sensor_data:
                        power, cadence, speed, heart_rate = sensor_data
                        print(f'Power: {power}, Cadence: {cadence}, Speed: {speed}, Heart Rate: {heart_rate}')
                    await asyncio.sleep_ms(SLEEP_TIME_MS)
            else:
                print("Characteristic does not support notifications or read.")
        except asyncio.TimeoutError:
            print("Timeout discovering services/characteristics")


asyncio.run(main())

