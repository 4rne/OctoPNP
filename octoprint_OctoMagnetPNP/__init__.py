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
            #"publicHost": None,
            #"publicPort": None,
            "tray": {
                "x": 0,
                "y": 0,
                "z": 0,
                "rows" : 5,
                "columns": 5,
                "boxsize": 10,
                "rimsize": 1.0
            },
            "vacnozzle": {
                "x": 0,
                "y": 0,
                "z_pressure": 0,
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
        vacuum_dest = [tray_offset[0]+part_offset[0]-float(self._settings.get(["vacnozzle", "x"])),\
                         tray_offset[1]+part_offset[1]-float(self._settings.get(["vacnozzle", "y"])),\
                         tray_offset[2]+self.smdparts.getPartHeight(partnr)-float(self._settings.get(["vacnozzle", "z_pressure"]))]

        # move vac nozzle to part and pick
        self._printer.commands("T" + str(self._settings.get(["vacnozzle", "extruder_nr"])))
        cmd = "G1 X" + str(vacuum_dest[0]) + " Y" + str(vacuum_dest[1]) + " F" + str(self.FEEDRATE)
        self._printer.commands(cmd)
        self._printer.commands("G1 Z" + str(vacuum_dest[2]+10))
        self._releaseVacuum()
        self._lowerVacuumNozzle()
        self._printer.commands("G1 Z" + str(vacuum_dest[2]) + "F1000")
        self._gripVacuum()
        self._printer.commands("G4 P500")
        self._printer.commands("G1 Z" + str(vacuum_dest[2]+5) + "F1000")

        # move to bed camera
        vacuum_dest = [float(self._settings.get(["camera", "bed", "x"]))-float(self._settings.get(["vacnozzle", "x"])),\
                       float(self._settings.get(["camera", "bed", "y"]))-float(self._settings.get(["vacnozzle", "y"])),\
                       float(self._settings.get(["camera", "bed", "z"]))+self.smdparts.getPartHeight(partnr)]

        self._printer.commands("G1 X" + str(vacuum_dest[0]) + " Y" + str(vacuum_dest[1]) + " F"  + str(self.FEEDRATE))
        self._printer.commands("G1 Z" + str(vacuum_dest[2]) + " F"  + str(self.FEEDRATE))

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

        # Double check whether orientation is now correct. Important on unreliable hardware...
        if(abs(orientation_offset) > 0.5):
            self._updateUI("INFO", "Incorrect alignment, correcting offset of " + str(-orientation_offset) + "°")
            self._logger.info("Incorrect alignment, correcting offset of " + str(-orientation_offset) + "°")
            self._printer.commands("G92 E0")
            self._printer.commands("G1 E" + str(-orientation_offset) + " F" + str(self.FEEDRATE))
            # wait a second to execute the rotation
            time.sleep(2)

        # move to destination
        dest_z = destination[2]+self.smdparts.getPartHeight(partnr)-float(self._settings.get(["vacnozzle", "z_pressure"]))
        cmd = "G1 X" + str(destination[0]-float(self._settings.get(["vacnozzle", "x"]))+displacement[0]) \
              + " Y" + str(destination[1]-float(self._settings.get(["vacnozzle", "y"]))+displacement[1]) \
              + " F" + str(self.FEEDRATE)
        self._logger.info("object destination: " + cmd)
        self._printer.commands("G1 Z" + str(dest_z+10) + " F" + str(self.FEEDRATE)) # lift printhead
        self._printer.commands(cmd)
        self._printer.commands("G1 Z" + str(dest_z))

        #release part
        self._releaseVacuum()
        self._printer.commands("G4 P500") #some extra time to make sure the part has released and the remaining vacuum is gone
        self._printer.commands("G1 Z" + str(dest_z+10) + " F" + str(self.FEEDRATE)) # lift printhead again
        self._liftVacuumNozzle()

    # get the position of the box (center of the box) containing part x relative to the [0,0] corner of the tray
    def _getTrayPosFromPartNr(self, partnr):
        partPos = self.smdparts.getPartPosition(partnr)
        row = (partPos-1)/int(self._settings.get(["tray", "columns"]))+1
        col = ((partPos-1)%int(self._settings.get(["tray", "columns"])))+1
        self._logger.info("Selected object: %d. Position: box %d, row %d, col %d", partnr, partPos, row, col)

        boxsize = float(self._settings.get(["tray", "boxsize"]))
        rimsize = float(self._settings.get(["tray", "rimsize"]))
        x = (col-1)*boxsize + boxsize/2 + col*rimsize + float(self._settings.get(["tray", "x"]))
        y = (row-1)*boxsize + boxsize/2 + row*rimsize + float(self._settings.get(["tray", "y"]))
        return [x, y, float(self._settings.get(["tray", "z"]))]

    def _gripVacuum(self):
        self._printer.commands("M400")
        self._printer.commands("M400")
        self._printer.commands("G4 P500")
        for line in self._settings.get(["vacnozzle", "grip_magnet_gcode"]).splitlines():
            self._printer.commands(line)
        self._printer.commands("G4 P500")

    def _releaseVacuum(self):
        self._printer.commands("M400")
        self._printer.commands("M400")
        self._printer.commands("G4 P500")
        for line in self._settings.get(["vacnozzle", "release_magnet_gcode"]).splitlines():
            self._printer.commands(line)
        self._printer.commands("G4 P500")

    def _lowerVacuumNozzle(self):
        self._printer.commands("M400")
        self._printer.commands("M400")
        self._printer.commands("G4 P500")
        for line in self._settings.get(["vacnozzle", "lower_nozzle_gcode"]).splitlines():
            self._printer.commands(line)
        self._printer.commands("G4 P500")

    def _liftVacuumNozzle(self):
        self._printer.commands("M400")
        self._printer.commands("M400")
        self._printer.commands("G4 P500")
        for line in self._settings.get(["vacnozzle", "lift_nozzle_gcode"]).splitlines():
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
                partArray = []
                for partId in partIds:
                    partArray.append(
                        dict(
                            id = partId,
                            name = self.smdparts.getPartName(partId),
                            partPosition = self.smdparts.getPartPosition(partId),
                            shape = self.smdparts.getPartShape(partId),
                            type = self.smdparts.getPartType(partId),
                            thread=self.smdparts.getPartThread(partId)
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
