// Draw pipeline inputs on the canvas.
function draw_inputs(pipeline) {
    var pipeline_inputs = pipeline['pipeline_inputs'];  // Array[]
    for (i = 0; i < pipeline_inputs.length; i++) {
        node = pipeline_inputs[i];
        if (node.CDT_pk === null) {
            canvasState.addShape(new RawNode(node.x * canvas.width, node.y * canvas.height, rawNodeWidth, rawNodeHeight, null, null, null, node.dataset_name));
        } else {
            canvasState.addShape(new CDtNode(node.CDT_pk, node.x * canvas.width, node.y * canvas.height, cdtNodeWidth, cdtNodeHeight, null, null, null, node.dataset_name));
        }
        canvasState.dragging = true;
        canvasState.selection = canvasState.shapes[canvasState.shapes.length-1];
        canvasState.doUp();
    }
    draw_steps(pipeline, pipeline_inputs.length);
}

// Draw pipeline steps on the canvas.
function draw_steps(pipeline, method_node_offset) {
    var pipeline_steps = pipeline['pipeline_steps'];
    for (i = 0; i < pipeline_steps.length; i++) {
        node = pipeline_steps[i];
        var inputs = pipeline_steps[i]["inputs"],
            outputs = pipeline_steps[i]["outputs"];
        
        var method_node = new MethodNode(node.transf_pk, node.family_pk, node.x * canvas.width, node.y * canvas.height, mNodeWidth,
                mNodeInset, mNodeSpacing, mNodeColour, node.name, mNodeOffset,
                inputs, outputs);

        canvasState.addShape(method_node);
        method_node.draw(canvasState.ctx);  // to update Magnet x and y

        // connect Method inputs
        cables = node['cables_in'];
        for (j = 0; j < cables.length; j++) {
            cable = cables[j];
            if (cable.source_step == 0) {
                // cable from pipeline input, identified by dataset_name
                source = null;
                for (k = 0; k < canvasState.shapes.length; k++) {
                    shape = canvasState.shapes[k];
                    if (shape.constructor !== MethodNode && shape.label === cable.source_dataset_name) {
                        source = shape;
                        break;
                    }
                }
                if (source === null) {
                    alert("Failed to redraw Pipeline: missing data node");
                    return;
                }

                // data nodes only have one out-magnet, so use 0-index
                connector = new Connector(null, null, source.out_magnets[0]);

                // connect other end of cable to the MethodNode
                magnet = method_node.in_magnets[j];
                connector.x = magnet.x;
                connector.y = magnet.y;
                connector.dest = magnet;

                source.out_magnets[0].connected.push(connector);
                method_node.in_magnets[j].connected.push(connector);
                canvasState.connectors.push(connector);
            } else {
                // cable from another MethodNode

                // this requires that pipeline_steps in JSON is sorted by step_num
                //  (adjust for 0-index)
                source = canvasState.shapes[method_node_offset + cable.source_step - 1];

                // find the correct out-magnet
                for (k = 0; k < source.out_magnets.length; k++) {
                    magnet = source.out_magnets[k];
                    if (magnet.label === cable.source_dataset_name) {
                        connector = new Connector(null, null, magnet);
                        magnet = method_node.in_magnets[j];
                        connector.x = magnet.x;
                        connector.y = magnet.y;
                        connector.dest = magnet;
                        
                        source.out_magnets[k].connected.push(connector);
                        method_node.in_magnets[j].connected.push(connector);
                        canvasState.connectors.push(connector);
                        break;
                    }
                }
            }
        }
        // done connecting input cables
    }
    draw_outputs(pipeline, method_node_offset);
}

// Draw pipeline outputs on the canvas.
function draw_outputs(pipeline, method_node_offset) {
    var pipeline_outputs = pipeline['pipeline_outputs'];
    for (i = 0; i < pipeline_outputs.length; i++) {
        this_output = pipeline_outputs[i];

        // identify source Method
        source = canvasState.shapes[method_node_offset + this_output.source_step - 1];

        // find the correct out-magnet
        for (k = 0; k < source.out_magnets.length; k++) {
            magnet = source.out_magnets[k];
            if (magnet.label === this_output.source_dataset_name) {
                connector = new Connector(null, null, magnet);
                output_node = new OutputNode(this_output.x * canvas.width, this_output.y * canvas.height, null, null, null, null, null, this_output.output_name);
                canvasState.addShape(output_node);

                connector.x = this_output.x * canvas.width;
                connector.y = this_output.y * canvas.height;

                connector.dest = output_node.in_magnets[0];
                connector.dest.connected = [ connector ];  // bind cable to output node
                connector.source = magnet;

                magnet.connected.push(connector);  // bind cable to source Method
                canvasState.connectors.push(connector);
                break;
            }
        }
    }
}

$(function() {
    // change pipeline revision drop-down triggers ajax to redraw canvas
    $('#id_pipeline_select').on('change', function () {
        $.ajax({
            type: "POST",
            url: "/get_pipeline/",
            data: { pipeline_id: $('#id_pipeline_select').val() },
            datatype: "json",
            success: function(result) {
                // prepare to redraw canvas
                $('#id_reset_button').click();
                submit_to_url = result['family_pk'];
                var i, j, k; // counters
                var node, cables, cable, connector, shape, source, magnet;
        
                draw_inputs(result);
                
                canvasState.testExecutionOrder();
        
                for (var i = 0; i < canvasState.shapes.length; i++) {
                    canvasState.detectCollisions(canvasState.shapes[i], 0.5);
                }

                $('#id_publish').val(
                    result['is_published_version']?
                    'Cancel publication' :
                    'Make published version'
                );
            }
        })
    }).change();

    $('#id_publish').on('click', function() {
        $.ajax({
            type: "POST",
            url: "/activate_pipeline/",
            data: { pipeline_id: $('#id_pipeline_select').val() },
            datatype: "json",
            success: function(result) {
                if (result['is_published']) {
                    $('#id_publish').val('Cancel publication');
                } else {
                    $('#id_publish').val('Make published version');
                }
            }
        })
    })
})