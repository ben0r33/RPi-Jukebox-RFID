#!/usr/bin/env python3

import datetime
import os
import re
import ssl
import subprocess
import time
from threading import *

import inotify.adapters
import paho.mqtt.client as mqtt

# ----------------------------------------------------------
#  Prerequisites
# ----------------------------------------------------------
# pip3 install paho-mqtt inotify


# ----------------------------------------------------------
#  SETTINGS
# ----------------------------------------------------------
config = {
    "DEBUG": False,
    "mqttBaseTopic": "phoniebox",  # MQTT base topic
    "mqttClientId": "phoniebox",  # MQTT client ID
    "mqttHostname": "openHAB",  # MQTT server hostname
    "mqttPort": 8883,  # MQTT server port (typically 1883 for unencrypted, 8883 for encrypted)
    "mqttUsername": "",  # username for user/pass based authentication
    "mqttPassword": "",  # password for user/pass based authentication
    "mqttCA": "/home/pi/MQTT/mqtt-ca.crt",  # path to server certificate for certificate-based authentication
    "mqttCert": "/home/pi/MQTT/mqtt-client-phoniebox.crt",  # path to client certificate for certificate-based authentication
    "mqttKey": "/home/pi/MQTT/mqtt-client-phoniebox.key",  # path to client keyfile for certificate-based authentication
    "mqttConnectionTimeout": 60,  # in seconds; timeout for MQTT connection
    "refreshIntervalPlaying": 5,  # in seconds; how often should the status be sent to MQTT (while playing)
    "refreshIntervalIdle": 30,  # in seconds; how often should the status be sent to MQTT (when NOT playing)
}


# ----------------------------------------------------------
#  DO NOT CHANGE BELOW
# ----------------------------------------------------------

# absolute script path
path = os.path.dirname(os.path.realpath(__file__))

# internal refresh interval
refreshInterval = config.get("refreshIntervalPlaying")

# list of available commands and attributes
arAvailableCommands = [
    "volumeup",
    "volumedown",
    "mute",
    "playerplay",
    "playerpause",
    "playernext",
    "playerprev",
    "playerstop",
    "playerrewind",
    "playershuffle",
    "playerreplay",
    "scan",
    "shutdown",
    "shutdownsilent",
    "reboot",
    "disablewifi",
]
arAvailableCommandsWithParam = [
    "setvolume",
    "setvolstep",
    "setmaxvolume",
    "setidletime",
    "playerseek",
    "shutdownafter",
    "shutdownvolumereduction",
    "playerstopafter",
    "playerrepeat",
    "rfid",
    "gpio",
    "swipecard",
    "playfolder",
    "playfolderrecursive",
]
arAvailableAttributes = [
    "volume",
    "mute",
    "repeat",
    "repeat_mode",
    "random",
    "state",
    "file",
    "artist",
    "albumartist",
    "title",
    "album",
    "track",
    "elapsed",
    "duration",
    "trackdate",
    "last_card",
    "maxvolume",
    "volstep",
    "idletime",
    "rfid",
    "gpio",
    "remaining_stopafter",
    "remaining_shutdownafter",
    "remaining_shutdownvolumereduction",
    "remaining_idle",
    "throttling",
    "temperature",
]


def watchForNewCard():
    i = inotify.adapters.Inotify()
    i.add_watch(path + "/../settings/Latest_RFID")

    # wait for inotify events
    for event in i.event_gen(yield_nones=False):
        if event is not None:
            # fetch event attributes
            (e_header, e_type_names, e_path, e_filename) = event

            # file was closed and written => a new card was swiped
            if "IN_CLOSE_WRITE" in e_type_names:
                # fetch card ID
                cardid = readfile(path + "/../settings/Latest_RFID")

                # publish event "card_swiped"
                client.publish(
                    config.get("mqttBaseTopic") + "/event/card_swiped", payload=cardid
                )
                print(" --> Publishing event card_swiped = " + cardid)

                # process all attributes
                processGet("all")


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connection established.")

        # retrieve server version and edition
        version = readfile(path + "/../settings/version")
        edition = readfile(path + "/../settings/edition")

        # check disk space
        disk_total, disk_avail = disk_stats()

        # publish general server info
        client.publish(
            config.get("mqttBaseTopic") + "/state", payload="online", qos=1, retain=True
        )
        client.publish(
            config.get("mqttBaseTopic") + "/version",
            payload=version,
            qos=1,
            retain=True,
        )
        client.publish(
            config.get("mqttBaseTopic") + "/edition",
            payload=edition,
            qos=1,
            retain=True,
        )
        client.publish(
            config.get("mqttBaseTopic") + "/disk_total",
            payload=disk_total,
            qos=1,
            retain=True,
        )
        client.publish(
            config.get("mqttBaseTopic") + "/disk_avail",
            payload=disk_avail,
            qos=1,
            retain=True,
        )

    else:
        print("Connection could NOT be established. Return-Code:", rc)


def on_disconnect(client, userdata, rc):
    print("Disconnecting. Return-Code:", str(rc))
    client.loop_stop()


def on_log(client, userdata, level, buf):
    print("   [LOG]", buf)


def on_message(client, userdata, message):
    print("")
    print("MQTT message incoming to subscriptions...")
    print(" - topic =", message.topic)
    print(" - value =", message.payload.decode("utf-8"))

    regex_extract = re.search(
        config.get("mqttBaseTopic") + "\/(.*)\/(.*)", message.topic
    )
    message_topic = regex_extract.group(1).lower()
    message_subtopic = regex_extract.group(2).lower()
    message_payload = message.payload.decode("utf-8")

    if message_topic == "cmd":
        processCmd(message_subtopic, message_payload)

    elif message_topic == "get":
        processGet(message_subtopic)


def processCmd(command, parameter):
    # list all commands
    if command == "help":
        availableCommands = ", ".join(arAvailableCommands)
        availableCommandsWithParam = ", ".join(arAvailableCommandsWithParam)
        client.publish(
            config.get("mqttBaseTopic") + "/available_commands",
            payload=availableCommands,
        )
        client.publish(
            config.get("mqttBaseTopic") + "/available_commands_with_params",
            payload=availableCommandsWithParam,
        )
        print(" --> Publishing response available_commands =", availableCommands)
        print(
            " --> Publishing response available_commands_with_params =",
            availableCommandsWithParam,
        )

    # toggle RFID reader daemon
    elif command == "rfid":
        parameter = parameter.lower()
        if parameter == "start" or parameter == "stop":
            subprocess.call(
                ["sudo /bin/systemctl " + parameter + " phoniebox-rfid-reader.service"],
                shell=True,
            )
        else:
            print(" --> Expecting parameter start or stop")

    # toggle GPIO button daemon
    elif command == "gpio":
        parameter = parameter.lower()
        if parameter == "start" or parameter == "stop":
            subprocess.call(
                [
                    "sudo /bin/systemctl "
                    + parameter
                    + " phoniebox-gpio-control.service"
                ],
                shell=True,
            )
        else:
            print(" --> Expecting parameter start or stop")

    # virtually swipe a RFID card
    elif command == "swipecard":
        print(" --> Virtually swiping card with ID", parameter)
        subprocess.call([path + "/rfid_trigger_play.sh -i=" + parameter], shell=True)

    # play folder
    elif command == "playfolder":
        print(" --> Playing folder", parameter)
        subprocess.call(
            [path + "/rfid_trigger_play.sh -d='" + parameter + "'"], shell=True
        )

    # play folder (recursive)
    elif command == "playfolderrecursive":
        print(" --> Playing folder " + parameter + " (recursive)")
        subprocess.call(
            [path + "/rfid_trigger_play.sh -d='" + parameter + "' -v=recursive"],
            shell=True,
        )

    # all the other known commands w/o param
    elif command in arAvailableCommands:
        print(" --> Sending command " + command + " to playout_controls.sh")
        subprocess.call([path + "/playout_controls.sh -c=" + command], shell=True)

    # all the other known commands /w param
    elif command in arAvailableCommandsWithParam:
        print(
            " --> Sending command "
            + command
            + " and value "
            + parameter
            + " to playout_controls.sh"
        )
        subprocess.call(
            [path + "/playout_controls.sh -c=" + command + " -v=" + parameter],
            shell=True,
        )

    # we don't know this command
    else:
        print(" --> Unknown command", command)
        return

    # this was a known command => refresh all attributes as they might have changed
    client.publish(config.get("mqttBaseTopic") + "/get/all", payload="")


def processGet(attribute):
    mpd_status = fetchData()

    # respond with all attributes
    if attribute == "all":
        for attribute in mpd_status:
            client.publish(
                config.get("mqttBaseTopic") + "/attribute/" + attribute,
                payload=mpd_status[attribute],
            )
            print(
                " --> Publishing response " + attribute + " = " + mpd_status[attribute]
            )

    # list all possible attributes
    elif attribute == "help":
        availableAttributes = ", ".join(arAvailableAttributes)
        client.publish(
            config.get("mqttBaseTopic") + "/available_attributes",
            payload=availableAttributes,
        )
        print(" --> Publishing response", availableAttributes)

    # all the other known attributes
    elif attribute in mpd_status:
        client.publish(
            config.get("mqttBaseTopic") + "/attribute/" + attribute,
            payload=mpd_status[attribute],
        )
        print(" --> Publishing response " + attribute + " = " + mpd_status[attribute])

    # we don't know this attribute
    else:
        print(" --> Could not retrieve attribute", attribute)


def disk_stats():
    statvfs = os.statvfs("/home/pi")
    size_total = statvfs.f_frsize * statvfs.f_blocks  # total
    # size_avail = statvfs.f_frsize * statvfs.f_bfree    # actual free
    size_avail = statvfs.f_frsize * statvfs.f_bavail  # free for non-root

    return round(size_total / 1073741824, 1), round(size_avail / 1073741824, 1)


def readfile(filepath):
    result = ""
    with open(filepath, "r") as f:
        result = f.read()
    return result.rstrip()


def isServiceRunning(svc):
    cmd = ["/bin/systemctl", "status", svc]
    status = subprocess.run(cmd, stdout=subprocess.PIPE).stdout.decode("utf-8").rstrip()
    if re.search("\n.*Active:.*running.*\n", status):
        return "true"
    else:
        return "false"


def linux_job_remaining(job_name):
    cmd = ["sudo", "atq", "-q", job_name]
    dtQueue = (
        subprocess.run(cmd, stdout=subprocess.PIPE).stdout.decode("utf-8").rstrip()
    )

    regex = re.search(
        "(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", dtQueue
    )
    if regex:
        dtNow = datetime.datetime.now()
        dtQueue = datetime.datetime.strptime(
            dtNow.strftime("%d.%m.%Y") + " " + regex.group(5), "%d.%m.%Y %H:%M:%S"
        )

        # subtract 1 day if queued for the next day
        if dtNow > dtQueue:
            dtNow = dtNow - datetime.timedelta(days=1)

        return int(round((dtQueue.timestamp() - dtNow.timestamp()) / 60, 0))
    else:
        return 0


def getOsThrottling():
    codes = {
        0: "under-voltage detected",
        1: "arm frequency capped",
        2: "currently throttled",
        3: "soft temperature limit active",
        16: "under-voltage has occurred",
        17: "arm frequency capped has occurred",
        18: "throttling has occurred",
        19: "soft temperature limit has occurred",
    }

    p = subprocess.Popen(
        ["vcgencmd", "get_throttled"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    throttling, err = p.communicate()
    codeHex = throttling.rstrip().split("0x")[1]

    # code is zero => no issue
    if codeHex == "0":
        return "OK"

    # analyse returned code
    result = []
    codeBinary = ""
    for fourbits in codeHex:
        codeBinary = codeBinary + bin(int(fourbits, 16))[2:].zfill(4)
    codeBinary = codeBinary[::-1]
    for bitNumber in range(len(codeBinary)):
        if codeBinary[bitNumber] == "1":
            result.append(codes[bitNumber])
    return "WARNING: " + ", ".join(result)


def getOsTemperature():
    p = subprocess.Popen(
        ["vcgencmd", "measure_temp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    temperature, err = p.communicate()
    temperature = temperature.rstrip().split("=")[1]
    return temperature


def normalizeTrueFalse(s):
    if s == "0":
        return "false"
    else:
        return "true"


def regex(needle, hay, exception="-"):
    regex_extract = re.search(needle, hay)
    if regex_extract:
        return regex_extract.group(1)
    else:
        return exception


def getDuration(status):
    """ Find the duration of the track in the output from mpd status"""

    # try to get the duration value
    duration = regex("\nduration: (.*)\n", status)

    if duration == "-":
        # if the duration attribute is missing try to get the time
        # this attribute value is split into two parts by ":"
        # first is the elapsed time and the second part is the duration
        duration = regex("\ntime: .*:(.*)\n", status, "0")

    return int(float(duration))


REPEAT_MODE_OFF = "off"
REPEAT_MODE_SINGLE = "single"
REPEAT_MODE_PLAYLIST = "playlist"


def get_repeat_mode(repeat, status):
    """ Returns the repeat mode that is selected in mpd """

    if repeat == "false":
        return REPEAT_MODE_OFF

    single = regex("\nsingle: (.*)\n", status)
    if single == "0":
        return REPEAT_MODE_PLAYLIST

    return REPEAT_MODE_SINGLE


def fetchData():
    # use global refreshInterval as this function is run as a thread through the paho-mqtt loop
    global refreshInterval

    result = {}

    # fetch status from MPD
    cmd = ["nc", "-w", "1", "localhost", "6600"]
    input = "status\ncurrentsong\nclose".encode("utf-8")
    status = subprocess.run(cmd, stdout=subprocess.PIPE, input=input).stdout.decode(
        "utf-8"
    )

    # interpret status
    result["state"] = regex("\nstate: (.*)\n", status).lower()
    result["volume"] = regex("\nvolume: (.*)\n", status)
    result["repeat"] = normalizeTrueFalse(regex("\nrepeat: (.*)\n", status))
    result["repeat_mode"] = get_repeat_mode(result["repeat"], status)
    result["random"] = normalizeTrueFalse(regex("\nrandom: (.*)\n", status))

    # interpret mute state based on volume
    if result["volume"] == "0":
        result["mute"] = "true"
    else:
        result["mute"] = "false"

    # interpret metadata when in play/pause mode
    if result["state"] != "stop":

        result["file"] = regex("\nfile: (.*)\n", status)
        result["artist"] = regex("\nArtist: (.*)\n", status)
        result["albumartist"] = regex("\nAlbumArtist: (.*)\n", status)
        result["title"] = regex("\nTitle: (.*)\n", status)
        result["album"] = regex("\nAlbum: (.*)\n", status)
        result["track"] = regex("\nTrack: (.*)\n", status, "0")
        result["trackdate"] = regex("\nDate: (.*)\n", status)

        if result["title"] == "-":
            result["title"] = result["file"]

        elapsed = int(float(regex("\nelapsed: (.*)\n", status, "0")))
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        result["elapsed"] = "{:02}:{:02}:{:02}".format(
            int(hours), int(minutes), int(seconds)
        )

        duration = getDuration(status)
        hours, remainder = divmod(duration, 3600)
        minutes, seconds = divmod(remainder, 60)
        result["duration"] = "{:02}:{:02}:{:02}".format(
            int(hours), int(minutes), int(seconds)
        )

    # fetch some more data from global.conf (via playout_controls.sh)
    result["maxvolume"] = (
        subprocess.run(
            [path + "/playout_controls.sh", "-c=getmaxvolume"], stdout=subprocess.PIPE
        )
        .stdout.decode("utf-8")
        .rstrip()
    )
    result["volstep"] = (
        subprocess.run(
            [path + "/playout_controls.sh", "-c=getvolstep"], stdout=subprocess.PIPE
        )
        .stdout.decode("utf-8")
        .rstrip()
    )
    result["idletime"] = (
        subprocess.run(
            [path + "/playout_controls.sh", "-c=getidletime"], stdout=subprocess.PIPE
        )
        .stdout.decode("utf-8")
        .rstrip()
    )

    # fetch last card
    result["last_card"] = readfile(path + "/../settings/Latest_RFID")

    # fetch service states
    result["rfid"] = isServiceRunning("phoniebox-rfid-reader.service")
    result["gpio"] = isServiceRunning("phoniebox-gpio-control.service")

    # fetch linux jobs
    result["remaining_stopafter"] = str(linux_job_remaining("s"))
    result["remaining_shutdownafter"] = str(linux_job_remaining("t"))
    result["remaining_shutdownvolumereduction"] = str(linux_job_remaining("q"))
    result["remaining_idle"] = str(linux_job_remaining("i"))

    # fetch OS information
    result["throttling"] = getOsThrottling()
    result["temperature"] = getOsTemperature()

    # modify refresh rate depending on play state
    if result["state"] == "play":
        refreshInterval = config.get("refreshIntervalPlaying")
    else:
        refreshInterval = config.get("refreshIntervalIdle")

    return result


# create client instance
client = mqtt.Client(config.get("mqttClientId"))

# configure authentication
if config.get("mqttUsername") and config.get("mqttPassword"):
    client.username_pw_set(
        username=config.get("mqttUsername"), password=config.get("mqttPassword")
    )

if config.get("mqttCert") and config.get("mqttKey"):
    if config.get("mqttCA"):
        client.tls_set(
            ca_certs=config.get("mqttCA"),
            certfile=config.get("mqttCert"),
            keyfile=config.get("mqttKey"),
        )
    else:
        client.tls_set(certfile=config.get("mqttCert"), keyfile=config.get("mqttKey"))
elif config.get("mqttCA"):
    client.tls_set(ca_certs=config.get("mqttCA"))

# attach event handlers
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message
if config.get("DEBUG") is True:
    client.on_log = on_log

# define last will
client.will_set(
    config.get("mqttBaseTopic") + "/state", payload="offline", qos=1, retain=True
)

# connect to MQTT server
print(
    "Connecting to "
    + config.get("mqttHostname")
    + " on port "
    + str(config.get("mqttPort"))
)
client.connect(
    config.get("mqttHostname"),
    config.get("mqttPort"),
    config.get("mqttConnectionTimeout"),
)

# subscribe to topics
print("Subscribing to " + config.get("mqttBaseTopic") + "/cmd/#")
client.subscribe(config.get("mqttBaseTopic") + "/cmd/#")
print("Subscribing to " + config.get("mqttBaseTopic") + "/get/#")
client.subscribe(config.get("mqttBaseTopic") + "/get/#")

# register thread for watchForNewCard
tWatchForNewCard = Thread(target=watchForNewCard)
tWatchForNewCard.setDaemon(True)
tWatchForNewCard.start()

# start endless loop
client.loop_start()
while True:
    processGet("all")
    time.sleep(refreshInterval)
