function smdTray(cols, rows, boxSize, canvas, config) {
	var self = this;

	var _cols = cols;
    var _rows= rows;
    var _trayBoxSize = boxSize;
    var _trayCanvas = canvas;
    var _config = JSON.parse(config);
    var _parts = {};

    self.erase = function() {
        _parts = {};
        _drawTray();
    }

    self.addPart = function(part) {
        // sanitiy checks!?
        // add part to dict
        _parts[part.id] = part;

        _parts[part.id].row = parseInt(((part.partPosition-1) / _cols)) + 1;
        _parts[part.id].col = (part.partPosition-1) % _cols+1;

        // and draw to canvas
        _drawPart(part.id, part.thread, part.type, "#aaa");
    }

    self.selectPart = function(x, y) {
        var canvasBoxSize = _getCanvasBoxSize();
        col = Math.floor(x/(canvasBoxSize+1)) + 1;
        row = Math.floor(((_rows*canvasBoxSize)-y)/(canvasBoxSize-1)) + 1;

        for (var id in _parts) {
            _drawPart(id, parts[part.id].thread, parts[part.id].type, "#aaa");
        }

        var partId = _getPartId(col, row);
        if(partId) {
            _drawPart(partId, parts[part.id].thread, parts[part.id].type, "red");
        }
        return partId;
    }


	function _drawTray () {
		if (_trayCanvas && _trayCanvas.getContext) {
            var ctx = _trayCanvas.getContext("2d");
            if (ctx) {
                var size_x = ctx.canvas.width;
                var size_y = ctx.canvas.height;
                var canvasBoxSize = _getCanvasBoxSize();

                //initialize white tray
                ctx.strokeStyle = "black";
                ctx.fillStyle = "white";
                ctx.lineWidth = 1;
                ctx.fillRect(0,0,size_x,size_y);
                ctx.strokeRect (0,0,size_x,size_y);

				for(var x=0; x<_cols; x++) {
                    for(var y=0; y<_rows; y++) {
                        _drawTrayBox(x + 1, y + 1, canvasBoxSize, _config[parseInt(x) * parseInt(_rows) + parseInt(y)].thread);
                    }
                }
            }
        }
	}
	
	
	//draw a part into a tray box
    function _drawPart(partID, thread, type, color) {
        part = _parts[partID];

		//clear old box
        var canvasBoxSize = _getCanvasBoxSize();
        _drawTrayBox(part.col, part.row, canvasBoxSize, _config[(parseInt(part.col) - 1) * parseInt(_rows) + parseInt(part.row) - 1].thread);

		if (_trayCanvas && _trayCanvas.getContext) {
            var ctx = _trayCanvas.getContext("2d");
            var scale = canvasBoxSize/_trayBoxSize;
            if (ctx) {
                var col_offset = part.col*canvasBoxSize-canvasBoxSize+4;
                var row_offset = _rows*canvasBoxSize-part.row*canvasBoxSize+4;

                //print part names
				ctx.font = "10px Verdana";
				ctx.fillStyle = "#000000";
				ctx.textBaseline = "top";
				ctx.fillText(part.name, col_offset, row_offset);

                let size = parseFloat(thread) * 5;
                x = (part.col - 1) * canvasBoxSize + 4 / 2 + canvasBoxSize / 2;
                y = (_rows) * canvasBoxSize - (part.row - 1) * canvasBoxSize + 4 / 2 - canvasBoxSize / 2;

                ctx.fillStyle = color;
                ctx.beginPath();
                if (type === "hexnut") {
                    for (let i = 0; i < 360; i += 60) {
                        ctx.lineTo(x + Math.sin(i * Math.PI / 180) * size * 0.45, y + Math.cos(i * Math.PI / 180) * size * 0.45);
                    }
                }
                else if (type === "squarenut") {
                    ctx.lineTo(x - size / 2, y -  size / 2);
                    ctx.lineTo(x +  size / 2,y - size / 2);
                    ctx.lineTo(x + size / 2,y + size / 2);
                    ctx.lineTo(x - size / 2, y + size / 2);
                }
                ctx.closePath();
                ctx.fill();

                ctx.beginPath();
                ctx.fillStyle = "white";
                ctx.arc(x, y, size / 7.0, 0, 2 * Math.PI);
                ctx.fill();
            }
        }
    }

    // draw a single tray box
    function _drawTrayBox(col, row, size, partSize) {
        col -=1;
        row -=1;
        if (_trayCanvas && _trayCanvas.getContext) {
            var ctx = _trayCanvas.getContext("2d");
            if (ctx) {
                ctx.lineWidth = 4;
                ctx.strokeStyle = "black";
                ctx.fillStyle = "white";
                ctx.strokeRect (col*size+ctx.lineWidth/2,(_rows-1)*size-row*size+ctx.lineWidth/2,size-ctx.lineWidth/2,size-ctx.lineWidth/2);
                ctx.fillRect (col*size+ctx.lineWidth,(_rows-1)*size-row*size+ctx.lineWidth,size-ctx.lineWidth,size-ctx.lineWidth);
                x = col * size + ctx.lineWidth / 2 + size / 2;
                y = (_rows - 1) * size - row * size + ctx.lineWidth / 2 + size / 2;
                ctx.fillStyle = '#000';

                partBoxSize = partSize * 5 + 2
                nutShape = _config[(parseInt(col)) * parseInt(_rows) + parseInt(row)].nut
                ctx.beginPath();
                if (nutShape === "hex") {
                    for (let i = 0; i < 360; i += 60) {
                        ctx.lineTo(x + Math.sin(i * Math.PI / 180) * partBoxSize * 0.45, y + Math.cos(i * Math.PI / 180) * partBoxSize * 0.45);
                    }
                } else if (nutShape === "square") {

                    ctx.lineTo(x - partBoxSize / 2, y - partBoxSize / 2);
                    ctx.lineTo(x + partBoxSize / 2, y - partBoxSize / 2);
                    ctx.lineTo(x + partBoxSize / 2, y + partBoxSize / 2);
                    ctx.lineTo(x - partBoxSize / 2, y + partBoxSize / 2);
                }
                ctx.closePath();
                ctx.lineWidth = 1;
                ctx.stroke();
            }
        }
    }

    // returns the box size to use the available canvas-space in an optimal way
    function _getCanvasBoxSize() {
        var boxSize = 0;
        if (_trayCanvas && _trayCanvas.getContext) {
            var ctx = _trayCanvas.getContext("2d");
            if (ctx) {
                var size_x = ctx.canvas.width;
                var size_y = ctx.canvas.height;
                boxSize = Math.min((size_x-4)/_cols, (size_y-4)/_rows);
            }
        }
        return Math.floor(boxSize);
    }

    // select partId from col/row
    function _getPartId(col, row) {
        var result = false;
        for (var id in _parts) {
            if((_parts[id].col == col) && (_parts[id].row == row)) {
                result = id;
                break;
            }
        }
        return result;
    }
}
