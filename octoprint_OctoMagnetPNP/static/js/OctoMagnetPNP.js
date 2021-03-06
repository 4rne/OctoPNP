$(function() {
    function OctoMagnetPNPViewModel(parameters) {
        var self = this;

        self.settings = parameters[0];
        self.control = parameters[1];
        self.connection = parameters[2];

        var _smdTray = {};
        var _smdTrayCanvas = document.getElementById('trayCanvas');

        self.stateString = ko.observable("No file loaded");
        self.currentOperation = ko.observable("");
        self.debugvar = ko.observable("");
        //white placeholder images

        // This will get called before the ViewModel gets bound to the DOM, but after its depedencies have
        // already been initialized. It is especially guaranteed that this method gets called _after_ the settings
        // have been retrieved from the OctoPrint backend and thus the SettingsViewModel been properly populated.
        self.onBeforeBinding = function() {
            self.traySettings = self.settings.settings.plugins.OctoMagnetPNP.tray;
            _smdTray = new smdTray(self.traySettings.columns(), self.traySettings.rows(), self.traySettings.boxsize(), _smdTrayCanvas, self.traySettings.boxconfiguration());
            _smdTrayCanvas.addEventListener("click", self.onSmdTrayClick, false); //"click, dblclick"
            _smdTrayCanvas.addEventListener("dblclick", self.onSmdTrayDblclick, false); //"click, dblclick"
        }

        // catch mouseclicks at the tray for interactive part handling
        self.onSmdTrayClick = function(event) {
            console.log("click")
            var rect = _smdTrayCanvas.getBoundingClientRect();
            var x = Math.floor(event.clientX - rect.left);
            var y = Math.floor(event.clientY - rect.top);
            return _smdTray.selectPart(x, y);
        }

        self.onSmdTrayDblclick = function(event) {
            // highlight part on tray and find partId
            var partId = self.onSmdTrayClick(event);

            // execute pick&place operation
            if(partId) {
                // printer connected and not printing?
                if(self.connection.isOperational() || self.connection.isReady()) {
                    self.control.sendCustomCommand({ command: "M361 P" + partId});
                }
            }
        }



        self.onDataUpdaterPluginMessage = function (plugin, data) {
            if(plugin == "OctoMagnetPNP") {
                if (data.event == "FILE") {
                    if(data.data.hasOwnProperty("partCount")) {
                        self.stateString("Loaded file with " + data.data.partCount + " nuts");
                        //initialize the tray
                        _smdTray.erase();

						//extract part information
                        if( data.data.hasOwnProperty("parts") ) {
							var parts = data.data.parts;
							for(var i=0; i < parts.length; i++) {
								_smdTray.addPart(parts[i]);
							}
						}
                    }else{
                        self.stateString("No nuts part in this file!");
                    }
                }
                else if(data.event == "OPERATION") {
                    self.currentOperation(data.data.type + " part nr " + data.data.part);
                }
                else if(data.event == "ERROR") {
                    self.stateString("ERROR: \"" + data.data.type + "\"");
                    if(data.data.hasOwnProperty("part")) {
                        self.stateString(self.StateString + "appeared while processing part nr " + data.data.part);
                    }
                }
                else if(data.event == "INFO") {
                    self.stateString("INFO: \"" + data.data.type + "\"");
                }
                //self.debugvar("Plugin = OctoMagnetPNP");
            }
        };
    }

    // This is how our plugin registers itself with the application, by adding some configuration information to
    // the global variable ADDITIONAL_VIEWMODELS
    ADDITIONAL_VIEWMODELS.push([
        // This is the constructor to call for instantiating the plugin
        OctoMagnetPNPViewModel,

        // This is a list of dependencies to inject into the plugin, the order which you request here is the order
        // in which the dependencies will be injected into your view model upon instantiation via the parameters
        // argument
        ["settingsViewModel", "controlViewModel", "connectionViewModel"],

        // Finally, this is the list of all elements we want this view model to be bound to.
        "#tab_plugin_OctoMagnetPNP"
    ]);
});
