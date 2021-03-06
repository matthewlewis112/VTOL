'''Automous tools for VTOL'''
import time
from math import radians
from dronekit import VehicleMode, Vehicle, LocationGlobalRelative
from pymavlink import mavutil
from coms import Coms
from util import get_distance_metres, to_quaternion

class VTOL(Vehicle):
    ''' VTOL basic state isolated'''
    def __init__(self, *args): #pylint: disable=useless-super-delegation
        super(VTOL, self).__init__(*args)

    def setup(self):
        '''vtol specific steps needed before flight'''
        print('Initializing Coms')
        self.coms = Coms(self.configs, self.coms_callback)


    # State, updated by XBee callback function
    configs = None
    start_mission = False  # takeoff
    pause_mission = False  # vehicle will hover
    stop_mission = False  # return to start and land

    # Global status, updated by various functions
    status = "ready"
    MISSION_COMPLETED = False
    coms = None
    land_mode = 'LAND'

    # pylint: disable=no-self-use
    def coms_callback(self, command):
        '''callback for radio messages'''

        #tuple of commands that can be executed
        valid_commands = ("takeoff", "land", "go_to", "set_altitude")
        #gives us the specific command we want the drone to executre

        #checking for valid command
        if command["Type"] not in valid_commands:
            raise Exception("Error: Unsupported status for vehicle")

        #executes takeoff command to drone
        if command["Type"] == 'takeoff':
            self.takeoff()
        #executes land command to drone
        elif command["Type"] == 'land':
            self.land()
        elif command["Type"] == 'go_to':
            self.go_to(LocationGlobalRelative(command["Body"]["Lat"], \
                command["Body"]["Lon"], command["Body"]["Alt"]))
        elif command["Type"] == 'set_altitude':
            self.set_altitude(command["Body"]["Alt"])


    def start_auto_mission(self):
        '''Arms and starts an AUTO mission loaded onto the vehicle'''
        while not self.is_armable:
            print(" Waiting for vehicle to initialise...")
            time.sleep(1)

        self.mode = VehicleMode("GUIDED")
        self.armed = True

        while not self.armed:
            print(" Waiting for arming...")
            time.sleep(1)

        self.commands.next = 0
        self.mode = VehicleMode("AUTO")

        msg = self.message_factory.command_long_encode(
            0, 0,    # target_system, target_component
            mavutil.mavlink.MAV_CMD_MISSION_START, #command
            0, #confirmation
            0, 0, 0, 0, 0, 0, 0)    # param 1 ~ 7 not used
        # send command to vehicle
        self.send_mavlink(msg)

        self.commands.next = 0


    def takeoff(self):
        '''Commands drone to take off by arming vehicle and flying to altitude'''
        print("Pre-arm checks")
        while not self.is_armable:
            print("Waiting for vehicle to initialize")
            time.sleep(1)

        print("Arming motors")
        # Vehicle should arm in GUIDED mode
        self.mode = VehicleMode("GUIDED")
        self.armed = True

        while not self.armed:
            print("Waiting to arm vehicle")
            time.sleep(1)

        print("Taking off")

        altitude = self.configs['altitude']
        self.simple_takeoff(altitude)  # take off to altitude

        # Wait until vehicle reaches minimum altitude
        while self.location.global_relative_frame.alt < altitude * 0.95:
            print("Altitude: " + str(self.location.global_relative_frame.alt))
            time.sleep(1)

        print("Reached target altitude")

    def go_to(self, point):
        '''Commands drone to fly to a specified point perform a simple_goto '''

        self.simple_goto(point, self.configs["air_speed"])

        while True:
            distance = get_distance_metres(self.location.global_relative_frame, point)
            if distance > self.configs['waypoint_tolerance']:
                print("Distance remaining:", distance)
                time.sleep(1)
            else:
                break
        print("Target reached")


    def land(self):
        '''Commands vehicle to land'''
        self.mode = VehicleMode(self.land_mode)

        print("Landing...")

        while self.location.global_relative_frame.alt > 0:
            print("Altitude: " + str(self.location.global_relative_frame.alt))
            time.sleep(1)

        print("Landed")

        print("Sleeping...")
        time.sleep(5)

    def set_altitude(self, alt):
        '''Sets altitude of quadcopter using an "alt" parameter'''
        print("Setting altitude:")
        destination = LocationGlobalRelative(self.location.global_relative_frame.lat, \
            self.location.global_relative_frame.lon, alt)
        self.go_to(destination)
        print("Altitude reached")

    def change_status(self, new_status):
        ''':param new_status: new vehicle status to change to (refer to GCS formatting)'''
        if new_status not in ("ready", "running", "waiting", "paused", "error"):
            raise Exception("Error: Unsupported status for vehicle")
        self.status = new_status


    def send_attitude_target(
            self,
            roll_angle=0.0,
            pitch_angle=0.0,
            yaw_angle=None,
            yaw_rate=0.0,
            use_yaw_rate=False,
            thrust=0.5,
    ):
        '''
        use_yaw_rate: the yaw can be controlled using yaw_angle OR yaw_rate.
                    When one is used, the other is ignored by Ardupilot.
        thrust: 0 <= thrust <= 1, as a fraction of maximum vertical thrust.
                Note that as of Copter 3.5, thrust = 0.5 triggers a special case in
                the code for maintaining current altitude.
        '''
        if yaw_angle is None:
            # this value may be unused by the vehicle, depending on use_yaw_rate
            yaw_angle = self.attitude.yaw
        # Thrust >  0.5: Ascend
        # Thrust == 0.5: Hold the altitude
        # Thrust <  0.5: Descend
        msg = self.message_factory.set_attitude_target_encode(
            0,  # time_boot_ms
            1,  # Target system
            1,  # Target component
            0b00000000 if use_yaw_rate else 0b00000100,
            to_quaternion(roll_angle, pitch_angle, yaw_angle),  # Quaternion
            0,  # Body roll rate in radian
            0,  # Body pitch rate in radian
            radians(yaw_rate),  # Body yaw rate in radian/second
            thrust,  # Thrust
        )
        self.send_mavlink(msg)


    def set_attitude(
            self,
            roll_angle=0.0,
            pitch_angle=0.0,
            yaw_angle=None,
            yaw_rate=0.0,
            use_yaw_rate=False,
            thrust=0.5,
            duration=0,
    ):
        '''
        Note that from AC3.3 the message should be re-sent more often than every
        second, as an ATTITUDE_TARGET order has a timeout of 1s.
        In AC3.2.1 and earlier the specified attitude persists until it is canceled.
        The code below should work on either version.
        Sending the message multiple times is the recommended way.
        '''
        self.send_attitude_target(
            roll_angle, pitch_angle, yaw_angle, yaw_rate, use_yaw_rate, thrust
        )
        start = time.time()
        while time.time() - start < duration:
            self.send_attitude_target(
                roll_angle, pitch_angle, yaw_angle, yaw_rate, use_yaw_rate, thrust
            )
            time.sleep(0.1)
        # Reset attitude, or it will persist for 1s more due to the timeout
        self.send_attitude_target(0, 0, 0, 0, True, thrust)


    def update_thread(self, address):
        ''':param vehicle: vehicle object that represents drone
        :param vehicle_type: vehicle type from configs file'''
        print("Starting update thread\n")

        while not self.MISSION_COMPLETED:
            location = self.location.global_frame
            # Comply with format of 0 - 1 and check that battery level is not null
            battery_level = self.battery.level / 100.0 if self.battery.level else 0.0
            update_message = {
                "type": "update",
                "time": round(time.clock() - self.coms.con_timestamp) + self.coms.gcs_timestamp,
                "sid": self.configs["vehicle_id"],
                "tid": 0, # the ID of the GCS is 0
                "id": self.coms.new_msg_id(),

                "vehicleType": "VTOL",
                "lat": location.lat,
                "lon": location.lon,
                "status": self.status,
                # TODO heading
                "battery": battery_level
            }

            self.coms.send_till_ack(address, update_message, update_message['id'])
            time.sleep(1)
        self.change_status("ready")
