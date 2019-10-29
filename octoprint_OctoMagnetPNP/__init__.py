# -*- coding: utf-8 -*-
"""
    This file is part of OctoMagnetPNP

    OctoMagnetPNP is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    OctoMagnetPNP is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with OctoMagnetPNP.  If not, see <http://www.gnu.org/licenses/>.

    Main authors: Florens Wasserfall <wasserfall@kalanka.de> Arne Büngener <arne.buengener@googlemail.com>
"""

from __future__ import absolute_import


import octoprint.plugin
import re
from subprocess import call
import os
import time
import datetime
import base64
import shutil
import json

from .SmdParts import SmdParts

__plugin_name__ = "OctoMagnetPNP"

#instantiate plugin object and register hook for gcode injection
def __plugin_load__():

    octomagnetpnp = OctoMagnetPNP()

    global __plugin_implementation__
    __plugin_implementation__ = octomagnetpnp

    global __plugin_hooks__
    __plugin_hooks__ = {'octoprint.comm.protocol.gcode.sending': octomagnetpnp.hook_gcode_sending, 'octoprint.comm.protocol.gcode.queuing': octomagnetpnp.hook_gcode_queuing}



class OctoMagnetPNP(octoprint.plugin.StartupPlugin,
            octoprint.plugin.TemplatePlugin,
            octoprint.plugin.EventHandlerPlugin,
            octoprint.plugin.SettingsPlugin,
            octoprint.plugin.AssetPlugin,
            octoprint.plugin.SimpleApiPlugin,
            octoprint.plugin.BlueprintPlugin):

    STATE_NONE     = 0
    STATE_PICK     = 1
    STATE_ALIGN    = 2
    STATE_PLACE    = 3
    STATE_EXTERNAL = 9 # used if helper functions are called by external plugins

    FEEDRATE = 4000.000

    smdparts = SmdParts()
    partPositions = {}

    def __init__(self):
        self._state = self.STATE_NONE
        self._currentPart = 0
        self._helper_was_paused = False

        # store callback to send result of an image capture request back to caller
        self._helper_callback = None


    def on_after_startup(self):
        #used for communication to UI
        self._pluginManager = octoprint.plugin.plugin_manager()


    def get_settings_defaults(self):
        return {
            "tray": {
                "x": 0,
                "y": 0,
                "z": 0,
                "rows" : 5,
                "columns": 5,
                "boxsize": 10
            },
            "magnet": {
                "x": 0,
                "y": 0,
                "extruder_nr": 2,
                "grip_magnet_gcode": "M42 P48 S255",
                "release_magnet_gcode": "M42 P48 S0",
            }
        }

    def get_template_configs(self):
        return [
            dict(type="tab", template="OctoMagnetPNP_tab.jinja2", custom_bindings=True),
            dict(type="settings", template="OctoMagnetPNP_settings.jinja2", custom_bindings=True)
            #dict(type="settings", custom_bindings=True)
        ]

    def get_assets(self):
        return dict(
            js=["js/OctoMagnetPNP.js",
                "js/smdTray.js",
                "js/settings.js"]
        )

    # Use the on_event hook to extract XML data every time a new file has been loaded by the user
    def on_event(self, event, payload):
        #extraxt part informations from inline xmly
        if event == "FileSelected":
            self._currentPart = None
            xml = "";
            f = open(payload.get("file"), 'r')
            for line in f:
                expression = re.search("<.*>", line)
                if expression:
                    xml += expression.group() + "\n"
            if xml:
                #check for root node existence
                if not re.search("<object.*>", xml.splitlines()[0]):
                    xml = "<object name=\"defaultpart\">\n" + xml + "\n</object>"

                #parse xml data
                sane, msg = self.smdparts.load(xml)
                if sane:
                    #TODO: validate part informations against tray
                    self._logger.info("Extracted information on %d parts from gcode file %s", self.smdparts.getPartCount(), payload.get("file"))
                    self._updateUI("FILE", "")
                else:
                    self._logger.info("XML parsing error: " + msg)
                    self._updateUI("ERROR", "XML parsing error: " + msg)
            else:
                #gcode file contains no part information -> clear smdpart object
                self.smdparts.unload()
                self._updateUI("FILE", "")



    """
    Use the gcode hook to interrupt the printing job on custom M361 commands.
    """
    def hook_gcode_queuing(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        if "M361" in cmd:
            if self._state == self.STATE_NONE:
                self._state = self.STATE_PICK
                command = re.search("P\d*", cmd).group() #strip the M361
                self._currentPart = int(command[1:])

                self._logger.info( "Received M361 command to place part: " + str(self._currentPart))

                # pause running printjob to prevent octoprint from sending new commands from the gcode file during the interactive PnP process
                if self._printer.is_printing() or self._printer.is_resuming():
                    self._printer.pause_print()

                self._updateUI("OPERATION", "pick")

                self._printer.commands("M400")
                self._printer.commands("G4 P1")
                self._printer.commands("M400")
                for i in range(10):
                    self._printer.commands("G4 P1")

                self._printer.commands("M362 OctoMagnetPNP")


                return (None,) # suppress command
            else:
                self._logger.info( "ERROR, received M361 command while placing part: " + str(self._currentPart))

    """
    This hook is designed as some kind of a "state machine". The reason is,
    that we have to circumvent the buffered gcode execution in the printer.
    To take a picture, the buffer must be emptied to ensure that the printer has executed all previous moves
    and is now at the desired position. To achieve this, a M400 command is injected after the
    camera positioning command, followed by a M362. This causes the printer to send the
    next acknowledging ok not until the positioning is finished. Since the next command is a M362,
    octoprint will call the gcode hook again and we are back in the game, iterating to the next state.
    Since both, Octoprint and the printer firmware are using a queue, we inject some "G4 P1" commands
    as a "clearance buffer". Those commands simply cause the printer to wait for a millisecond.
    """
    # _pickPart --> _alignPart --> _placePart
    def hook_gcode_sending(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        if "M362 OctoMagnetPNP" in cmd:
            if self._state == self.STATE_PICK:
                self._state = self.STATE_ALIGN
                self._logger.info("Pick part " + str(self._currentPart))

                self._pickPart(self._currentPart)
                self._printer.commands("M400")
                self._printer.commands("G4 P1")
                self._printer.commands("M400")

                for i in range(10):
                    self._printer.commands("G4 P1")

                self._printer.commands("M362 OctoMagnetPNP")

                return (None,) # suppress command

            if self._state == self.STATE_ALIGN:
                self._state = self.STATE_PLACE
                self._logger.info("Align part " + str(self._currentPart))

                self._alignPart(self._currentPart)
                self._printer.commands("M400")
                self._printer.commands("G4 P1")
                self._printer.commands("M400")

                for i in range(10):
                    self._printer.commands("G4 P1")

                self._printer.commands("M362 OctoMagnetPNP")

                return (None,) # suppress command

            if self._state == self.STATE_PLACE:
                self._logger.info("Place part " + str(self._currentPart))

                self._placePart(self._currentPart)
                self._printer.commands("M400")
                self._printer.commands("G4 P1")
                self._printer.commands("M400")

                for i in range(10):
                    self._printer.commands("G4 P1")

                self._logger.info("Finished placing part " + str(self._currentPart))
                self._state = self.STATE_NONE

                # resume paused printjob into normal operation
                if self._printer.is_paused() or self._printer.is_pausing():
                    self._printer.resume_print()

                return (None,) # suppress command


    def _pickPart(self, partnr):
        part_offset = [0, 0]

        self._logger.info("PART OFFSET:" + str(part_offset))

        tray_offset = self._getTrayPosFromPartNr(partnr)
        vacuum_dest = [tray_offset[0]+part_offset[0]-float(self._settings.get(["magnet", "x"])),\
                         tray_offset[1]+part_offset[1]-float(self._settings.get(["magnet", "y"])),\
                         tray_offset[2]]

        # move magnet to part and pick
        self._printer.commands("T" + str(self._settings.get(["magnet", "extruder_nr"])))
        cmd = "G1 X" + str(vacuum_dest[0]) + " Y" + str(vacuum_dest[1]) + " F" + str(self.FEEDRATE)
        self._printer.commands(cmd)
        self._printer.commands("G1 Z" + str(vacuum_dest[2]+10))
        self._releaseMagnet()
        self._printer.commands("G1 Z" + str(vacuum_dest[2]) + "F1000")
        self._gripMagnet()
        self._printer.commands("G4 P500")
        self._printer.commands("G1 Z" + str(vacuum_dest[2]+5) + "F1000")

    def _alignPart(self, partnr):
        orientation_offset = 0

        # find destination at the object
        destination = self.smdparts.getPartDestination(partnr)

        #rotate object
        self._printer.commands("G92 E0")
        self._printer.commands("G1 E" + str(destination[3]-orientation_offset) + " F" + str(self.FEEDRATE))

    def _placePart(self, partnr):
        displacement = [0, 0]

        # find destination at the object
        destination = self.smdparts.getPartDestination(partnr)

        self._logger.info("displacement - x: " + str(displacement[0]) + " y: " + str(displacement[1]))

        # move to destination
        dest_z = destination[2]+self.smdparts.getPartHeight(partnr)
        cmd = "G1 X" + str(destination[0]-float(self._settings.get(["magnet", "x"]))+displacement[0]) \
              + " Y" + str(destination[1]-float(self._settings.get(["magnet", "y"]))+displacement[1]) \
              + " F" + str(self.FEEDRATE)
        self._logger.info("object destination: " + cmd)
        self._printer.commands("G1 Z" + str(dest_z+10) + " F" + str(self.FEEDRATE)) # lift printhead
        self._printer.commands(cmd)
        self._printer.commands("G1 Z" + str(dest_z))

        #release part
        self._releaseMagnet()
        self._printer.commands("G4 P500") #some extra time to make sure the part has released and the remaining vacuum is gone
        self._printer.commands("G1 Z" + str(dest_z+10) + " F" + str(self.FEEDRATE)) # lift printhead again

    # get the position of the box (center of the box) containing part x relative to the [0,0] corner of the tray
    def _getTrayPosFromPartNr(self, partnr):
        partPos = self.partPositions[partnr]
        row = partPos / int(self._settings.get(["tray", "columns"]))
        col = ((partPos) % int(self._settings.get(["tray", "columns"])))
        self._logger.info("Selected object: %d. Position: box %d, row %d, col %d", partnr, partPos, row, col)

        boxsize = float(self._settings.get(["tray", "boxsize"]))
        x = col * boxsize + float(self._settings.get(["tray", "x"]))
        y = row * boxsize + float(self._settings.get(["tray", "y"]))
        return [x, y, float(self._settings.get(["tray", "z"]))]

    def _gripMagnet(self):
        self._printer.commands("M400")
        self._printer.commands("M400")
        self._printer.commands("G4 P500")
        for line in self._settings.get(["magnet", "grip_magnet_gcode"]).splitlines():
            self._printer.commands(line)
        self._printer.commands("G4 P500")

    def _releaseMagnet(self):
        self._printer.commands("M400")
        self._printer.commands("M400")
        self._printer.commands("G4 P500")
        for line in self._settings.get(["magnet", "release_magnet_gcode"]).splitlines():
            self._printer.commands(line)
        self._printer.commands("G4 P500")

    def _updateUI(self, event, parameter):
        data = dict(
            info="dummy"
        )
        if event == "FILE":
            if self.smdparts.isFileLoaded():

                # compile part information
                partIds = self.smdparts.getPartIds()
                self.partPositions = {}
                partArray = []
                usedTrayPositions = []
                config = json.loads(self._settings.get(["tray", "boxconfiguration"]))
                for partId in partIds:
                    threadSize = self.smdparts.getPartThreadSize(partId)
                    trayPosition = None
                    # find empty tray position
                    for i, traybox in enumerate(config):
                        if(float(traybox.get("thread_size")) == float(threadSize) and
                           traybox.get("nut") == self.smdparts.getPartType(partId) and
                           i not in usedTrayPositions):
                            usedTrayPositions.append(i)
                            trayPosition = i
                            self.partPositions[partId] = i
                            break
                    if(trayPosition is None):
                        print("Error, no tray box for part no " + str(partId) + " left") # TODO Error handling
                        break
                    partArray.append(
                        dict(
                            id = partId,
                            name = self.smdparts.getPartName(partId),
                            partPosition = trayPosition,
                            shape = self.smdparts.getPartShape(partId),
                            type = self.smdparts.getPartType(partId),
                            threadSize = threadSize
                        )
                    )

                data = dict(
                    partCount = self.smdparts.getPartCount(),
                    parts = partArray
                )
        elif event == "OPERATION":
            data = dict(
                type = parameter,
                part = self._currentPart
            )
        elif event == "ERROR":
            data = dict(
                type = parameter,
            )
            if self._currentPart: data["part"] = self._currentPart
        elif event == "INFO":
            data = dict(
                type = parameter,
            )

        message = dict(
            event=event,
            data=data
        )
        self._pluginManager.send_plugin_message("OctoMagnetPNP", message)
