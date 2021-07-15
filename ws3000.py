#
#
"""Classes and functions for interfacing with an Ambient Weather WS-3000
station.

The following references were useful for developing this driver. More than simply useful,
in fact, since a lot of material has been directly reused:
    
From Matthew Wall:
  https://github.com/matthewwall/weewx-hp3000

From Tom Keffer, the WMR100 driver for weewx
  http://www.weewx.com

Many thanks to the following contributers:
- RistoK for helping with troubleshooting and testing on RPi

NOTE: the HP3000 driver developed by Matthew Wall should also be working
for the WS-3000 station. But various issues led me to rewrite a new driver
on the model of the one for the WMR100. One benefit is that this driver will
work with the "default" version of weewx and doesn't require the usb branch.

NOTE: since the station doesn't distinguish between loop and archive packets,
only genLoopPackets is implemented. It doesn't make sense to implement the other
AbstractDevice methods such as genArchiveRecords.
As a result, weewx should be configured with record_generation = software

NOTE: it seems that when packets are generated (data is fetch from the console) too quickly, errors can
occur, causing the station to 'hang' and potentially crashing weewx (error handling in this
driver is not the best!). Make sure that the loop interval is large enough to avoid any
potential issue.

NOTE for Raspberry Pi: if the usb read/write timeout is too small (100ms), errors
might occur when sending/fetching data from the console. It has been increased to 1000 by default,
but if this is still not sufficient futher increase the timeout in the weewx configuration file.

The comments below are taken directly from Matthew Wall's driver. They are included here for reference:

The HP-3000 supports up to 8 remote temperature/humidity sensors.  The console
has a 3"x4" TFT color display, with graph and room for 5 sensor displays.  The
sensor display regions can cycle through different sensors when more than 5
sensors are connected.
Every configuration option in the console can be modified via software.  These
options includes:
 - archive interval
 - date/time
 - date/time format
 - timezone
 - which sensors should be displayed in the 5 display regions
 - horizontal graph axis scaling
 - vertical graph axis
 - calibration for temperature/humidity from each sensor
 - alarms
 - historical data
Historical data are saved to the (optional) microSD card.  If no card is
installed, no data are retained.
Each sensor has its own display of temperature and humidity.
Each sensor is identified by channel number.  The channel number is set using
DIP switches on each sensor.  The DIP switches also determine which units will
be displayed in each sensor.
There are 4 two-position DIP switches.  DIP 4 determines units: 0=F 1=C
DIP 1-3 determine which of 8 channels is selected.
Each sensor uses 2 AA batteries.  Nominal battery life is 1 year.
The console uses 5V DC from an AC/DC transformer.
Data from sensors are received every 60 seconds.
Dewpoint and heatindex are calculated within the console.
Temperature sensors measure to +/- 2 degree F
Humidity sensors measure to +/- 5 %
Calibrations are applied in the console, so the values received from the
console are calibrated.  Calculations in the console are performed in degree C.
The console has a radio controlled clock.  During RCC reception, no data will
be transmitted.  If no RCC is received, attempt will be made every two hours
until successful.
This driver was developed without any assistance from Ambient Weather (the
vendor) or Fine Offset (the manufacturer).
===============================================================================
Messages from console
The console sends data in 64-byte chunks.  It looks like the console reuses a
buffer, because each message shorter than the previous contains bytes from the
previous message.  The byte sequence 0x40 0x7d indicates end of data within a
buffer.
Many of the console messages correspond with the control messages sent from
the host.

<...>

current data (27 bytes)
00 7b
01 00 ch1 temp MSB
02 eb ch1 temp LSB    t1 = (signed short(MSB,LSB)) / 10.0 - NB: modified to handle negative values
03 25 ch1 hum         h1 = hum
04 7f ch2 temp MSB
05 ff ch2 temp LSB
06 ff ch2 hum
07 7f ch3 temp MSB
08 ff ch3 temp LSB
09 ff ch3 hum
0a 7f ch4 temp MSB
0b ff ch4 temp LSB
0c ff ch4 hum
0d 7f ch5 temp MSB
0e ff ch5 temp LSB
0f ff ch5 hum
10 7f ch6 temp MSB
11 ff ch6 temp LSB
12 ff ch6 hum
13 7f ch7 temp MSB
14 ff ch7 temp LSB
15 ff ch7 hum
16 7f ch8 temp MSB
17 ff ch8 temp LSB
18 ff ch8 hum
19 40
1a 7dweewx.drivers.WS3000

Change log:

v0.2
- fixed values for negative temperatures
- improved support for Raspberry
- usb timeout can be changed via the configuration
- fixed some issues with the retries/error handling

v0.1 - Initial release

<...>

"""

import time
#import syslog
import logging
import usb.core
import usb.util
import sys
import traceback
import struct

import usb

import weewx.drivers
import weewx.wxformulas

DRIVER_NAME = 'WS3000'
DRIVER_VERSION = "0.2"


def loader(config_dict, engine):
    return WS3000(**config_dict[DRIVER_NAME])


def confeditor_loader():
    return WS3000ConfEditor()


log = logging.getLogger(__name__)


def logmsg(level, msg):
    # syslog.syslog(level, 'ws3000: %s' % msg)
    log.debug(msg)


def logdbg(msg):
    # logmsg(syslog.LOG_DEBUG, msg)
    log.debug(msg)


def loginf(msg):
    # logmsg(syslog.LOG_INFO, msg)
    log.info(msg)


def logerr(msg):
    # logmsg(syslog.LOG_ERR, msg)
    log.error(msg)


def tohex(buf):
    """Helper function used to print a byte array in hex format"""
    if buf:
        return "%s (len=%s)" % (' '.join(["%02x" % x for x in buf]), len(buf))
    return ''


class WS3000(weewx.drivers.AbstractDevice):
    """Driver for the WS3000 station."""

    DEFAULT_MAP = {
        'extraTemp1': 't_1',
        'extraTemp2': 't_2',
        'extraTemp3': 't_3',
        'extraTemp4': 't_4',
        'extraTemp5': 't_5',
        'extraTemp6': 't_6',
        'extraTemp7': 't_7',
        'extraTemp8': 't_8',
        'extraHumid1': 'h_1',
        'extraHumid2': 'h_2',
        'extraHumid3': 'h_3',
        'extraHumid4': 'h_4',
        'extraHumid5': 'h_5',
        'extraHumid6': 'h_6',
        'extraHumid7': 'h_7',
        'extraHumid8': 'h_8'}

    COMMANDS = {
        'sensor_values': 0x03,
        'calibration_values': 0x05,
        'interval_value': 0x41,
        'unknown': 0x06,
        'temp_alarm_configuration': 0x08,
        'humidity_alarm_configuration': 0x09,
        'device_configuration': 0x04
    }

    def __init__(self, **stn_dict):
        """Initialize an object of type WS3000.
        
        NAMED ARGUMENTS:
        
        model: Which station model is this?
        [Optional. Default is 'WS3000']

        timeout: How long to wait, in seconds, before giving up on a response
        from the USB port.
        [Optional. Default is 1000 milliseconds]
        
        wait_before_retry: How long to wait before retrying.
        [Optional. Default is 5 seconds]

        max_tries: How many times to try before giving up.
        [Optional. Default is 3]
        
        vendor_id: The USB vendor ID for the WS3000
        [Optional. Default is 0x0483]
        
        product_id: The USB product ID for the WS3000
        [Optional. Default is 0xca01]
        
        interface: The USB interface
        [Optional. Default is 0]
        
        loop_interval: The time (in seconds) between emitting LOOP packets.
        [Optional. Default is 10]
        
        packet_size: The size of the data fetched from the WS3000 during each read.
        [Optional. Default is 64 (0x40)]
        
        mode: Can be 'simulation' or 'hardware'.
        [Optional. Default is hardware]
        
        """

        # The following variables will in fact be fetched from the device itself.
        # There are anyway declared here with the usual values for the WS3000.
        self.IN_ep = 0x82
        self.OUT_ep = 0x1

        loginf('driver version is %s' % DRIVER_VERSION)
        self.model = stn_dict.get('model', 'WS3000')
        self.record_generation = stn_dict.get('record_generation', 'software')
        self.timeout = int(stn_dict.get('timeout', 1000))
        self.wait_before_retry = float(stn_dict.get('wait_before_retry', 5.0))
        self.max_tries = int(stn_dict.get('max_tries', 3))
        self.loop_interval = int(stn_dict.get('loop_interval', 10))
        self.vendor_id = int(stn_dict.get('vendor_id', '0x0483'), 0)
        self.product_id = int(stn_dict.get('product_id', '0x5750'), 0)
        self.interface = int(stn_dict.get('interface', 0))
        self.packet_size = int(stn_dict.get('packet_size', 64))  # 0x40
        self.mode = stn_dict.get('mode', 'hardware')
        self.sensor_map = dict(self.DEFAULT_MAP)

        if 'sensor_map' in stn_dict:
            self.sensor_map.update(stn_dict['sensor_map'])
        loginf('sensor map is %s' % self.sensor_map)
        self.device = None
        self.observations = {}
        self.open_port()

    def open_port(self):
        """Establish a connection to the WS3000"""

        loginf("Starting initialization of the WS-3000 driver")

        if self.mode == 'simulation':
            from weewx.drivers.simulator import Observation
            from random import uniform
            start = time.time()
            for key in self.sensor_map:
                if "temp" in key.lower():
                    self.observations[key] = Observation(magnitude=uniform(4, 8),
                                                         average=uniform(18, 22),
                                                         period=uniform(22, 26),
                                                         phase_lag=uniform(6, 18),
                                                         start=start)
                elif "humid" in key.lower():
                    self.observations[key] = Observation(magnitude=uniform(2, 20),
                                                         average=uniform(40, 60),
                                                         period=uniform(22, 26),
                                                         phase_lag=uniform(6, 18),
                                                         start=start)
            return

        # try to find the device using the vend and product id
        self.device = self._find_device()

        # TODO: review this piece of code...
        # this is very poorly coded: at first the interface is an 'int', hardcoded to 0, but
        # it is then later assigned the result of usb.util.find_descriptor()... Beside,
        # this requires a re-initialization back to an 'int' if a commucation retry occurs.
        self.interface = 0
        if not self.device:
            logerr("Unable to find USB device (0x%04x, 0x%04x)" %
                   (self.vendor_id, self.product_id))
            raise weewx.WeeWxIOError("Unable to find USB device")
        for line in str(self.device).splitlines():
            logdbg(line)

        # reset device, required if it was previously left in a 'bad' state
        self.device.reset()

        # Detach any interfaces claimed by the kernel
        # if self.device.is_kernel_driver_active(self.interface):
        #    print("Detaching kernel driver")
        #    self.device.detach_kernel_driver(self.interface)
        # FIX: is_kernel_driver_active is not working on all systems, the solution
        # below should work in those cases.
        try:
            self.device.detach_kernel_driver(self.interface)
        except usb.core.USBError:
            pass

        # get the interface and IN and OUT end points
        self.device.set_configuration()
        configuration = self.device.get_active_configuration()
        self.interface = usb.util.find_descriptor(
             configuration, bInterfaceNumber=self.interface
        ) # following this call, the interface is no longer an int...
        self.OUT_ep = usb.util.find_descriptor(
            self.interface,
            # match the first OUT endpoint
            custom_match=lambda eo: \
            usb.util.endpoint_direction(eo.bEndpointAddress) == usb.util.ENDPOINT_OUT)
        self.IN_ep = usb.util.find_descriptor(
            self.interface,
            # match the first OUT endpoint
            custom_match=lambda ei: \
            usb.util.endpoint_direction(ei.bEndpointAddress) == \
            usb.util.ENDPOINT_IN)

        # The following is normally not required... could be removed?
        try:
            usb.util.claim_interface(self.device, self.interface)
        except usb.USBError, e:
            self.closePort()
            logerr("Unable to claim USB interface: %s" % e)
            raise weewx.WeeWxIOError(e)

        loginf("WS-3000 initialization complete")

    def closePort(self):
        """Tries to ensure that the device will be properly 'unclaimed' by the driver"""

        if self.mode == 'simulation':
            return

        try:
            usb.util.dispose_resources(self.device)
        except usb.USBError:
            try:
                self.device.reset()
            except usb.USBError:
                pass

    def get_current_values(self):
        """Function that only returns the current sensors data.
        Should be used by a data service that will add temperature data to an existing packet, for
        example, since a single measurement would be required in such a case."""

        if self.mode == 'simulation':
            current_time = time.time() + 0.5
            new_packet = {'dateTime': int(current_time), 'usUnits': weewx.METRICWX}
            for x in self.observations:
                new_packet[x] = self.observations[x].value_at(current_time)
            return new_packet

        nberrors = 0
        while nberrors < self.max_tries:
            # Get a stream of raw packets, then convert them
            try:
                read_sensors_command = self.COMMANDS['sensor_values']
                raw_data = self._get_raw_data(read_sensors_command)
                #
                if not raw_data:  # empty record
                    raise weewx.WeeWxIOError("Failed to get any data from the station")
                formatted_data = self._raw_to_data(raw_data, read_sensors_command)
                logdbg('data: %s' % formatted_data)
                new_packet = self._data_to_wxpacket(formatted_data)
                logdbg('packet: %s' % new_packet)
                return new_packet
            except (usb.USBError, weewx.WeeWxIOError) as e:
                exc_traceback = traceback.format_exc()
                logerr("WS-3000: An error occurred while generating loop packets")
                logerr(exc_traceback)
                nberrors += 1
                # The driver seem to 'loose' connectivity with the station from time to time.
                # Trying to close/reopen the USB port to fix the problem.
                self.closePort()
                self.open_port()
                time.sleep(self.wait_before_retry)
        logerr("Max retries exceeded while fetching USB reports")
        traceback.print_exc(file=sys.stdout)
        raise weewx.RetriesExceeded("Max retries exceeded while fetching USB reports")

    def genLoopPackets(self):
        """Generator function that continuously returns loop packets"""
        try:
            while True:
                loop_packet = self.get_current_values()
                yield loop_packet
                time.sleep(self.loop_interval)
        except GeneratorExit:
            pass

    @property
    def hardware_name(self):
        return self.model
        
    # ===============================================================================
    #                         USB functions
    # ===============================================================================

    def _find_device(self):
        """Find the given vendor and product IDs on the USB bus"""
        device = usb.core.find(idVendor=self.vendor_id, idProduct=self.product_id)
        return device

    def _write_usb(self, buf):
        logdbg("write: %s - timeout: %d" % (tohex(buf), self.timeout))
        # NB: timeout increased from 100 to 1000 to avoid failure on RPi
        return self.device.write(self.OUT_ep, data=buf, timeout=self.timeout)

    def _read_usb(self):
        logdbg("reading " + str(self.packet_size) + " bytes")
        buf = self.device.read(self.IN_ep, self.packet_size, timeout=self.timeout)
        if not buf:
            return None
        logdbg("read: %s" % tohex(buf))
        if len(buf) != 64:
            logdbg('read: bad buffer length: %s != 64' % len(buf))
            return None
        if buf[0] != 0x7b:
            logdbg('read: bad first byte: 0x%02x != 0x7b' % buf[0])
            return None
        idx = None
        for i in range(0, len(buf) - 1):
            if buf[i] == 0x40 and buf[i + 1] == 0x7d:
                idx = i
                break
        if idx is None:
            logdbg('read: no terminating bytes in buffer: %s' % tohex(buf))
            return None
        return buf[0: idx + 2]

    # =========================================================================
    # LOOP packet related functions
    # ==========================================================================

    def _get_cmd_name(self, hex_command):
        return self.COMMANDS.keys()[self.COMMANDS.values().index(hex_command)]

    def _get_raw_data(self, hex_command=COMMANDS['sensor_values']):
        """Get a sequence of bytes from the console."""
        sequence = [0x7b, hex_command, 0x40, 0x7d]
        try:
            logdbg("sending request for " + self._get_cmd_name(hex_command))
            self._write_usb(sequence)
            logdbg("reading results...")
            buf = self._read_usb()
            return buf
        except Exception:
            exc_traceback = traceback.format_exc()
            logerr("WS-3000: An error occurred while fetching data")
            logerr(exc_traceback)
            traceback.print_exc(file=sys.stdout)
            raise weewx.WeeWxIOError("Error while fetching " + self._get_cmd_name(hex_command))

    def _raw_to_data(self, buf, hex_command=COMMANDS['sensor_values']):
        """Convert the raw bytes sent by the console to human readable values."""
        logdbg("extracting values for " + self._get_cmd_name(hex_command))
        logdbg("raw: %s" % buf)
        record = dict()
        if not buf:
            return record
        if hex_command == self.COMMANDS['sensor_values']:
            if len(buf) != 27:
                raise weewx.WeeWxIOError("Incorrect buffer length, failed to read " + self._get_cmd_name(hex_command))
            record['type'] = self._get_cmd_name(hex_command)
            for ch in range(8):
                idx = 1 + ch * 3
                if buf[idx] != 0x7f and buf[idx + 1] != 0xff:
                    # The forluma below has been changed compared to the original code
                    # to properly handle negative temperature values.
                    # The station seems to provide the temperature as an unsigned short (2 bytes),
                    # so struct.unpack is used for the conversion to decimal.
                    # record['t_%s' % (ch + 1)] = (buf[idx] * 256 + buf[idx + 1]) / 10.0 # this doesn't handle negative values correctly
                    record['t_%s' % (ch + 1)] = struct.unpack('>h', buf[idx:idx+2])[0] / 10.0
                if buf[idx + 2] != 0xff:
                    record['h_%s' % (ch + 1)] = buf[idx + 2]
        else:
            logdbg("unknown data: %s" % tohex(buf))
        return record

    def _data_to_wxpacket(self, station_data):
        # prepare the packet for weewx (map sensor data to database fields)
        new_packet = {'dateTime': int(time.time() + 0.5), 'usUnits': weewx.METRICWX}
        for x in self.sensor_map:
            if self.sensor_map[x] in station_data:
                new_packet[x] = station_data[self.sensor_map[x]]
        return new_packet


class WS3000ConfEditor(weewx.drivers.AbstractConfEditor):

    @property
    def default_stanza(self):
        return """
[WS3000]
    # This section is for the Ambient Weather WS3000

    # The driver to use
    driver = user.ws3000

    # [Optional] Fetch data from the console or generate it
    # Useful to test without a console plugged in
    # Values are: 'hardware' or 'simulation'
    # mode = simulation

    # The station model, e.g., WS3000, WS3000-X3, WS3000-X5 (all the same...)
    model = WS3000
    
    # [Optional] The interval at which loop packets should be generated by the driver
    # Default is 10
    loop_interval = 30
    
    # [Optional] USB vendor ID and product ID, as returned by lsusb. Only required if the device
    # cannot be found with the default values
    # Defaults are 0x0483 and 0x5750
    vendor_id =  0x0483
    product_id = 0x5750
    
    # [Optional] USB read/write timeout (helps on Raspberry Pi)
    # Default is 1000
    timeout =  1000
    
    # [Optional] By default, all the sensor values are stored in the extraTemp or extraHumid columns. 
    # The assumption here is that the WS3000 is used as a secondary station used 
    # to enhance another existing station with additional temperature sensors, 
    # and that the usual inTemp, outTemp, etc. are already used by the primary station.
    # NOTE: of course, the database schema must be modified to include the missing columns.
    [[sensor_map]]
        extraTemp1 = t_1
        extraTemp2 = t_2
        extraTemp3 = t_3
        extraTemp4 = t_4
        extraTemp5 = t_5
        extraTemp6 = t_6
        extraTemp7 = t_7
        extraTemp8 = t_8
        extraHumid1 = h_1
        extraHumid2 = h_2
        extraHumid3 = h_3
        extraHumid4 = h_4
        extraHumid5 = h_5
        extraHumid6 = h_6
        extraHumid7 = h_7
        extraHumid8 = h_8
"""

    def modify_config(self, config_dict):
        print """
Changing the schema to include extraTemp and extraHumid colums """
        config_dict['DataBindings']['wx_binding']['schema'] = 'user.ws3000Extensions.ws3000Schema'


#
# *******************************************************************
#
# define a main entry point for basic testing of the station.
# Invoke this as follows from the weewx root dir:
#
# PYTHONPATH=bin python bin/user/ws3000.py

if __name__ == '__main__':

    import optparse

    usage = """%prog [options] [--debug] [--help]"""

#    syslog.openlog('ws3000', syslog.LOG_PID | syslog.LOG_CONS)
#    syslog.setlogmask(syslog.LOG_UPTO(syslog.LOG_INFO))
    parser = optparse.OptionParser(usage=usage)
    parser.add_option('--version', action='store_true',
                      help='display driver version')
    parser.add_option('--debug', action='store_true',
                      help='display diagnostic information while running')
    parser.add_option('--test', default='station',
                      help='what to test: station or driver')
    (options, args) = parser.parse_args()

    if options.version:
        print "driver version %s" % DRIVER_VERSION
        exit(1)

#    if options.debug:
#        syslog.setlogmask(syslog.LOG_UPTO(syslog.LOG_DEBUG))

    if options.test == 'driver':
        driver = WS3000()
        try:
            for p in driver.genLoopPackets():
                print p
        finally:
            driver.closePort()
    else:
        station = WS3000()
        while True:
            command = station.COMMANDS["sensor_values"]
            raw = station._get_raw_data(command)
            data = station._raw_to_data(raw, command)
            print('data: %s' % data)
            packet = station._data_to_wxpacket(data)
            print('packet: %s' % packet)
