// place in global namespace to access from other files
var canvas;
var canvasState;
var submit_to_url = 'pipeline_add';

var rawNodeWidth = 20,
    rawNodeColour = "#88DD88",
    rawNodeInset = 10,
    rawNodeOffset = 25;

var cdtNodeWidth = 40,
    cdtNodeColour = '#8888DD',
    cdtNodeInset = 10,
    cdtNodeOffset = 10;

var mNodeWidth = 80,
    mNodeInset = 10,
    mNodeSpacing = 20,
    mNodeColour = '#999999',
    mNodeOffset = 10;


$(document).ready(function(){ // wait for page to finish loading before executing jQuery code
    // initialize animated canvas
    canvas = document.getElementById('pipeline_canvas');
    var canvasWidth = window.innerWidth;
    var canvasHeight= window.innerHeight - 180;
    canvas.width = canvasWidth;
    canvas.height = canvasHeight;

    // TODO: can canvas be dynamically redrawn to fit window when it is resized?
    //    $(window).resize(function() {    });

    canvasState = new CanvasState(canvas);


    // trigger ajax on CR drop-down to populate revision select
    $(document).ajaxSend(function(event, xhr, settings) {
        /*
            from https://docs.djangoproject.com/en/1.3/ref/contrib/csrf/#csrf-ajax
            On each XMLHttpRequest, set a custom X-CSRFToken header to the value of the CSRF token.
            ajaxSend is a function to be executed before an Ajax request is sent.
        */
        //console.log('ajaxSend triggered');

        function getCookie(name) {
            var cookieValue = null;
            if (document.cookie && document.cookie != '') {
                var cookies = document.cookie.split(';');
                for (var i = 0; i < cookies.length; i++) {
                    var cookie = jQuery.trim(cookies[i]);
                    // Does this cookie string begin with the name we want?
                    if (cookie.substring(0, name.length + 1) == (name + '=')) {
                        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                        break;
                    }
                }
            }
            return cookieValue;
        }
        function sameOrigin(url) {
            // url could be relative or scheme relative or absolute
            var host = document.location.host; // host + port
            var protocol = document.location.protocol;
            var sr_origin = '//' + host;
            var origin = protocol + sr_origin;
            // Allow absolute or scheme relative URLs to same origin
            return url == origin || url.slice(0, origin.length + 1) == origin + '/' ||
                url == sr_origin || url.slice(0, sr_origin.length + 1) == sr_origin + '/' ||
                // or any other URL that isn't scheme relative or absolute i.e relative.
                !(/^(\/\/|http:|https:).*/.test(url));
        }
        function safeMethod(method) {
            return [ 'GET', 'HEAD', 'OPTIONS', 'TRACE' ].indexOf(method) > -1;
        }

        if (!safeMethod(settings.type) && sameOrigin(settings.url)) {
            xhr.setRequestHeader("X-CSRFToken", getCookie('csrftoken'));
        }
    });

    // update method drop-down
    $("#id_select_method_family").on('change',
        function() {
            mf_id = this.value;// DOMElement.value is much faster than jQueryObject.val().
            if (mf_id != "") {
                $.ajax({
                    type: "POST",
                    url: "get_method_revisions/",
                    data: {mf_id: mf_id}, // specify data as an object
                    datatype: "json", // type of data expected back from server
                    success: function(result) {
                        var options = [];
                        var arr = JSON.parse(result)
                        $.each(arr, function(index,value) {
                            options.push('<option value="', value.pk, '" title="', value.fields.filename, '">', value.fields.method_number, ': ', value.fields.method_name, '</option>');
                        });
                        $("#id_select_method").show().html(options.join('')).change();
                    }
                });
                
                $('#id_method_revision_field').show().focus();
            }
            else {
                $("#id_method_revision_field").hide();
            }
        }
    ).change(); // trigger on load

    // Use option TITLE as default Method node name
    $('#id_select_method').on('change', function() {
        var filename = $('#id_select_method option:selected')[0].title;
        $('#id_method_name').val(filename);
    });

    // Pack help text into an unobtrusive icon
    $('.helptext', 'form').each(function() {
        $(this).wrapInner('<span class="fulltext"></span>').prepend('<a rel="ctrl">?</a>');
    });
    
    // Labels go within their input fields until they are filled in
    $('input, textarea', '#pipeline_ctrl').each(function() {
    
        var lbl = $('label[for="#' + this.id +'"]', '#pipeline_ctrl');
        
        if (lbl.length) {
            $(this).on('focus', function() {
            
                if (this.value == lbl.html()) {
                    $(this).removeClass('input-label').val('');
                }
                
            }).on('blur', function() {
            
                if (this.value === '') {
                    $(this).addClass('input-label').val(lbl.html());
                }
                
            }).data('label', lbl.html()).addClass('input-label').val(lbl.html());
            lbl.remove();
        }
        
    });
    
    $('li', 'ul#id_ctrl_nav').on('click', function(e) {
        var $this = $(this),
            menu = $($this.data('rel'));

        $('li', 'ul#id_ctrl_nav').not(this).removeClass('clicked');
        $this.addClass('clicked');
        $('.ctrl_menu', '#pipeline_ctrl').hide();
        menu.show().css('left', $this.offset().left);
//            .find('input').eq(0).focus(); // INCOMPLETE: Focus on the first input field when menu opens. Needs to be a bit different... first empty field maybe?
        e.stopPropagation();
    });

    // FIXME: What does this do?
    $('a[rel="ctrl"]').on('click', function (e) {
        $(this).siblings('.fulltext').show().css({ top: e.pageY, left: e.pageX });
        setTimeout("$('.fulltext').fadeOut(300);", 2000);
    });

    // Handle jQuery-UI Dialog spawned for output cable
    $('form', '#dialog-form').on('submit', function(e) {
        // override ENTER key, click Create output button on form
        e.preventDefault();
        var dialog = $('#dialog-form');
        var buttons = dialog.dialog('option', 'buttons');
        buttons['Create output'].apply(dialog);
    });

    // Handle 'Inputs' menu
    $('form','#id_input_ctrl').on('submit', function(e) {
        e.preventDefault(); // stop default form submission behaviour
        
        var choice = $('#id_select_cdt option:selected', this);
        var node_label = $('#id_datatype_name', this).val();

        if (node_label === '' || node_label === "Label") {
            // required field
            $('#id_dt_error', this)[0].innerHTML = "Label is required";
        }
        else {
            $('#id_dt_error', this)[0].innerHTML = "";
            var this_pk = choice.val(); // primary key
            if (this_pk == ""){
                canvasState.addShape(new RawNode(100, 200 + Math.round(50 * Math.random()),
                    rawNodeWidth, rawNodeColour, rawNodeInset, rawNodeOffset, node_label
                ))
            } else {
                canvasState.addShape(new CDtNode(this_pk, 100, 200 + Math.round(50 * Math.random()),
                    cdtNodeWidth, cdtNodeColour, cdtNodeInset, cdtNodeOffset, node_label
                ));
            }
            $('#id_datatype_name').val("");  // reset text field
        }
    });

    // Handle 'Methods' menu
    $('form', '#id_method_ctrl').on('submit', function(e) {
        e.preventDefault(); // stop default form submission behaviour
        
        var method_name = $('#id_method_name', this),
            method_error = $('#id_method_error', this),
            method_family = $('#id_select_method_family', this),
            method = $('#id_select_method', this);
        var mid = method.val(); // pk of method

        if (mid === undefined || method_family.val() == '') {
            method_error[0].innerHTML = "Select a Method";
            
            if (method.is(':visible')) {
                method.focus();
            } else {
                method_family.focus();
            }
            
        } else {
            // user selected valid Method Revision
            var node_label = method_name.val();

            if (node_label === '' || node_label === 'Label') {
                // required field
                method_error[0].innerHTML = "Label is required";
                method_name.focus();
            }
            else {
                method_error[0].innerHTML = '';

                // use AJAX to retrieve Revision inputs and outputs
                $.ajax({
                    type: "POST",
                    url: "get_method_io/",
                    data: { mid: mid }, // specify data as an object
                    datatype: "json", // type of data expected back from server
                    success: function(result) {
                        var inputs = result['inputs'],
                            outputs = result['outputs'];

                        if ($('#id_method_button').val() === 'Add Method') {
                            // create new MethodNode
                            canvasState.addShape(new MethodNode(mid, 200, 200 + Math.round(50 * Math.random()),
                                mNodeWidth, mNodeInset, mNodeSpacing, mNodeColour, node_label, mNodeOffset,
                                inputs, outputs));
                        } else {
                            // replace the selected MethodNode
                            // if user clicks anywhere else, MethodNode is deselected
                            // and Methods menu closes
                            var old_node = canvasState.selection;
                            var idx;

                            // draw new node over old node
                            var new_node = new MethodNode(mid, old_node.x, old_node.y,
                                mNodeWidth, mNodeInset, mNodeSpacing, mNodeColour, node_label, mNodeOffset,
                                inputs, outputs);


                            // check if we can re-use any Connectors
                            var new_xput, old_xput, connector;

                            for (idx in old_node.inputs) {
                                old_xput = old_node.inputs[idx];
                                if (inputs.hasOwnProperty(idx)) {
                                    new_xput = inputs[idx];
                                    if (new_xput.cdt_pk === old_xput.cdt_pk) {
                                        // re-attach Connector
                                        connector = old_node.in_magnets[idx-1].connected.pop();
                                        connector.dest = new_node.in_magnets[idx-1];
                                        new_node.in_magnets[idx-1].connected.push(connector);
                                    }
                                }
                            }

                            for (idx in old_node.outputs) {
                                old_xput = old_node.outputs[idx];
                                if (outputs.hasOwnProperty(idx)) {
                                    new_xput = outputs[idx];
                                    if (new_xput.cdt_pk === old_xput.cdt_pk) {
                                        // re-attach all Connectors - note this reverses order
                                        for (var i = 0; i < old_node.out_magnets[idx-1].connected.length; i++) {
                                            connector = old_node.out_magnets[idx-1].connected.pop();
                                            connector.source = new_node.out_magnets[idx-1];
                                            new_node.out_magnets[idx-1].connected.push(connector);
                                        }
                                    }
                                }
                            }

                            canvasState.deleteObject();  // delete selected (old Method)
                            canvasState.addShape(new_node);
                        }

                    }
                });

                method_name.val('');
            }
        }
    });

    $('#id_example_button').on('click', function () {
        /*
        Populate canvasState with objects for an example pipeline.
         */
        canvasState.clear();
        canvasState.shapes = [];
        canvasState.connectors = [];

        // TODO: need MethodNode that can tie these together, or pick different examples
        canvasState.addShape(
            new RawNode(100, 250, 20, '#88DD88', 10, 25, 'Unstructured data')
        );
        canvasState.addShape(
            new CDtNode(1, 100, 150, 40, '#8888DD', 10, 10, 'Strings')
        );
    });

    $('#id_reset_button').on('click', function() {
        // remove all objects from canvas
        canvasState.clear();
        // reset containers to reflect canvas
        canvasState.shapes = [];
        canvasState.connectors = [];
    });

    $('#id_delete_button').on('click', function() {
        // remove selected object from canvas
        canvasState.deleteObject();
    });

    $(document).on('keydown', function(e) {
        // backspace or delete key also removes selected object
        if ([8,46].indexOf(e.which) > -1 && !$(e.target).is("input, textarea")) {
            // prevent backspace from triggering browser to navigate back one page
            e.preventDefault();
            canvasState.deleteObject();
        }
        // escape key closes menus
        // TODO: also should deselect any selected objects
        else if (e.which == 27) {
            $('li', 'ul#id_ctrl_nav').removeClass('clicked');
            $('#id_meta_ctrl, #id_method_ctrl, #id_input_ctrl', '#pipeline_ctrl').hide();
        }
    })
    
    $('#id_revision_desc').on('keydown', function() {
        var getHappierEachXChars = 12,
            happy = -Math.min(15, Math.floor(this.value.length / getHappierEachXChars)) * 32;
    
        $('.happy_indicator').css('background-position', happy + 'px 0px');
    })
        .trigger('keydown')
        .wrap('<div id="description_wrap">')
        .after('<div class="happy_indicator">')
        .after('<div class="happy_indicator_label">Keep typing to make me happy!</div>')
        .on('focus keyup', function() {
            var desc_length = this.value.length,
                wrap = $(this).parent();
            
            if (desc_length == 0 || $(this).hasClass('input-label')) {
                $('.happy_indicator, .happy_indicator_label', wrap).hide();
            }
            else if (desc_length > 20) {
                $('.happy_indicator', wrap).show();
                $('.happy_indicator_label', wrap).hide();
            }
            else {
                $('.happy_indicator, .happy_indicator_label', wrap).show();
            }
        }).on('blur', function() {
            $(this).siblings('.happy_indicator, .happy_indicator_label').hide();
        }).blur()
    ;

    
    $('body').on('click', function(e) {
        var menus = $('.ctrl_menu');
        if (menus.is(':visible') && $(e.target).closest(menus).length === 0) {
            menus.hide();
            $('li', 'ul#id_ctrl_nav').add(menus).removeClass('clicked');
        }
    });


    // draw dialog on trigger
    $( "#dialog-form" ).dialog({
        autoOpen: false,
        height: 120,
        width: 350,
        modal: true,
        buttons: {
            "Create output": function() {
                var connector = $('#dialog-form').data().sender;
                connector.dest = $('#output_name').val();
                connector.draw(canvasState.ctx);
                $( this ).dialog( "close" );
            },
            Cancel: function() {
                $( this ).dialog( "close" );
            }
        },
        close: function() {
        }
    });

    /*
        Submit form
    */
    $('#id_pipeline_form').submit(function(e) {
        /*
        Trigger AJAX transaction on submitting form.
         */

        e.preventDefault(); // override form submit action

        // Since a field contains its label on pageload, a field's label as its value is treated as blank
        $('input, textarea', this).each(function() {
            var $this = $(this);
            if ($this.val() == $this.data('label'))
                $this.val('');
        });
        
        var submit_error = $('#id_submit_error')[0];

        var shapes = canvasState.shapes;

        // check graph integrity
        var this_shape;
        var magnets;
        var this_magnet;
        var i, j;
        var pipeline_inputs = [];  // collect data nodes
        var method_nodes = [];
        var num_connections;

        submit_error.innerHTML = '';

        for (i = 0; i < shapes.length; i++) {
            this_shape = shapes[i];
            if (this_shape.constructor !== MethodNode) {
                // this is a data node
                pipeline_inputs.push(this_shape);

                // all CDtNodes or RawNodes (inputs) should feed into a MethodNode
                magnets = this_shape.out_magnets;
                this_magnet = magnets[0];  // data nodes only ever have one magnet

                // is this magnet connected?
                if (this_magnet.connected.length == 0) {
                    // unconnected input in graph, exit
                    submit_error.innerHTML = 'Unconnected input node';
                    return;
                }
            }
            else {
                method_nodes.push(this_shape);

                // at least one out-magnet must be occupied
                magnets = this_shape.out_magnets;
                num_connections = 0;
                for (j = 0; j < magnets.length; j++) {
                    this_magnet = magnets[j];
                    num_connections += this_magnet.connected.length;
                }
                if (num_connections === 0) {
                    submit_error.innerHTML = 'MethodNode with unused outputs';
                    return;
                }
            }
        }

        // at least one Connector must terminate as pipeline output
        var connectors = canvasState.connectors;
        var this_connector;
        var pipeline_has_output = false;
        for (i = 0; i < connectors.length; i++) {
            this_connector = connectors[i];
            if (this_connector.dest.constructor === String) {
                pipeline_has_output = true;
            }
        }

        if (!pipeline_has_output) {
            submit_error.innerHTML = 'Pipeline has no output';
            return;
        }


        var is_revision = false;
        if ($('#id_pipeline_select').length > 0) {
            is_revision = true;
        }

        // arguments to initialize new Pipeline Family
        var family_name = $('#id_family_name').val(),  // hidden input if revision
            family_desc = $('#id_family_desc').val(),
            revision_name = $('#id_revision_name').val(),
            revision_desc = $('#id_revision_desc').val();


        // Form validation
        if (!is_revision) {
            if (family_name === '') {
                // FIXME: is there a better way to do this trigger?
                $('li', 'ul#id_ctrl_nav')[0].click();
                $('#id_family_name').css({'background-color': '#FFFFCC'}).focus();
                submit_error.innerHTML = 'Pipeline family must be named';
                return;
            }
            $('#id_family_name').css({'background-color': '#FFFFFF'});

            if (family_desc === '') {
                $('li', 'ul#id_ctrl_nav')[0].click();
                $('#id_family_desc').css({'background-color': '#FFFFCC'}).focus();
                submit_error.innerHTML = 'Pipeline family must have a description';
                return;
            }
            $('#id_family_desc').css({'background-color': '#FFFFFF'});
        }

        // FIXME: This is fragile if we add a menu to either page
        var meta_menu_index = (is_revision ? 0 : 1);

        if (revision_name === '') {
            $('li', 'ul#id_ctrl_nav')[meta_menu_index].click();
            $('#id_revision_name').css({'background-color': '#FFFFCC'}).focus();
            submit_error.innerHTML = 'Pipeline must be named';
            return;
        }
        $('#id_revision_name').css({'background-color': '#FFFFFF'});

        if (revision_desc === '') {
            $('li', 'ul#id_ctrl_nav')[meta_menu_index].click();
            $('#id_revision_desc').css({'background-color': '#FFFFCC'}).focus();
            submit_error.innerHTML = 'Pipeline must have a description';
            return;
        }
        $('#id_revision_desc').css({'background-color': '#FFFFFF'});


        // Now we're ready to start
        var form_data = {};

        // There is no PipelineFamily yet; we're going to create one.
        form_data["family_pk"] = null;
        form_data['family_name'] = family_name;
        form_data['family_desc'] = family_desc;

        // arguments to add first pipeline revision
        form_data['revision_name'] = revision_name;
        form_data['revision_desc'] = revision_desc;

        if (is_revision) {
            form_data['revision_parent_pk'] = $('#id_pipeline_select').val();
        } else {
            // no parent, creating first revision of new Pipeline Family
            form_data["revision_parent_pk"] = null;
        }


        // Canvas information to store in the Pipeline object.
        form_data["canvas_width"] = canvas.width;
        form_data["canvas_height"] = canvas.height;

        // sort pipeline inputs by their Y-position on canvas (top to bottom)
        function sortByYpos (a, b) {
            var ay = a.y;
            var by = b.y;
            return ((ay < by) ? -1 : ((ay > by) ? 1 : 0));
        }
        pipeline_inputs.sort(sortByYpos);

        // update form data with inputs
        var this_input;
        form_data['pipeline_inputs'] = [];
        for (i = 0; i < pipeline_inputs.length; i++) {
            this_input = pipeline_inputs[i];
            form_data['pipeline_inputs'][i] = {
                'CDT_pk': (this_input.constructor===CDtNode) ? this_input.pk : null,
                'dataset_name': this_input.label,
                'dataset_idx': i+1,
                'x': this_input.x,
                'y': this_input.y,
                "min_row": null, // in the future these can be more detailed
                "max_row": null
            }
        }

        // append MethodNodes to sorted_elements Array in dependency order
        // see http://en.wikipedia.org/wiki/Topological_sorting#Algorithms
        var sorted_elements = [];
        var method_node;
        var this_parent;
        var okay_to_add;
        i = 0;
        while (method_nodes.length > 0) {
            for (j = 0; j < method_nodes.length; j++) {
                method_node = method_nodes[j];
                magnets = method_node.in_magnets;
                okay_to_add = true;

                for (var k = 0; k < magnets.length; k++) {
                    this_magnet = magnets[k];
                    if (this_magnet.connected.length == 0) {
                        // unconnected in-magnet, still okay to add this MethodNode
                        continue;
                    }
                    // trace up the Connector
                    this_parent = this_magnet.connected[0].source.parent;  // in-magnets only have 1 connector
                    if (this_parent.constructor === MethodNode) {  // ignore connections from data nodes
                        if ($.inArray(this_parent, sorted_elements) < 0) {
                            // dependency not cleared
                            okay_to_add = false;
                            break;
                        }
                    }
                }

                if (okay_to_add) {
                    // either MethodNode has no dependencies
                    // or all dependencies already in sorted_elements
                    sorted_elements.push(method_nodes.splice(j, 1)[0]);
                }
            }
            i += 1;
            if (i > 5*shapes.length) {
                console.log('DEBUG: topological sort routine failed')
                return;
            }
        }

        // sort output cables by y-position (top to bottom)
        var output_cables = [];
        for (i = 0; i < connectors.length; i++) {
            this_connector = connectors[i];
            if (this_connector.dest.constructor === String) {
                // connector terminates in output end-zone
                output_cables.push(this_connector);
            }
        }
        output_cables.sort(sortByYpos);

        // add arguments for input cabling
        var this_step;
        var this_source;

        form_data['pipeline_steps'] = [];

        for (i = 0; i < sorted_elements.length; i++) {
            this_step = sorted_elements[i];

            form_data['pipeline_steps'][i] = {
                'transf_pk': this_step.pk,  // to retrieve Method
                "transf_type": "Method", // in the future we can make this take Pipelines as well
                'step_num': i+1,  // 1-index (pipeline inputs are index 0)
                'x': this_step.x,
                'y': this_step.y,
                'name': this_step.label
            };

            // retrieve Connectors
            magnets = this_step.in_magnets;
            form_data['pipeline_steps'][i]['cables_in'] = [];
            form_data['pipeline_steps'][i]['outputs_to_delete'] = [];  // not yet implemented

            for (j = 0; j < magnets.length; j++) {
                this_magnet = magnets[j];
                if (this_magnet.connected.length == 0) {
                    continue;
                }
                this_connector = this_magnet.connected[0];
                this_source = this_connector.source.parent;

                if (this_source.constructor === MethodNode) {
                    form_data['pipeline_steps'][i]['cables_in'][j] = {
                        //'source_type': 'Method',
                        //'source_pk': this_source.pk,
                        'source_dataset_name': this_connector.source.label,
                        'source_step': sorted_elements.indexOf(this_source)+1,
                        'dest_dataset_name': this_connector.dest.label,
                        "keep_output": false, // in the future this can be more flexible
                        "wires": [] // in the future we can specify custom wires here
                    };
                }
                else {
                    // sourced by pipeline input
                    form_data['pipeline_steps'][i]['cables_in'][j] = {
                        //'source_type': this_source.constructor === RawNode ? 'raw' : 'CDT',
                        //'source_pk': this_source.constructor === RawNode ? '' : this_source.pk,
                        'source_dataset_name': this_connector.source.label,
                        'source_step': 0,
                        'dest_dataset_name': this_connector.dest.label,
                        "keep_output": false, // in the future this can be more flexible
                        "wires": [] // no wires for a raw cable
                    };
                }
            }
        }

        form_data["pipeline_output_cables"] = [];

        for (j = 0; j < output_cables.length; j++) {
            this_connector = output_cables[j];
            var this_source_step = this_connector.source.parent;

            form_data["pipeline_output_cables"][j] = {
                'output_idx': j+1,
                'output_name': this_connector.dest,
                "output_CDT_pk": this_connector.source.cdt,
                'source': this_source_step.pk,
                'source_step': sorted_elements.indexOf(this_step) + 1, // 1-index
                'source_dataset_name': this_connector.source.label,  // magnet label
                'x': this_connector.x,
                'y': this_connector.y,
                "wires": [] // in the future we might have this
            };
        }

        // this code written on Signal Hill, St. John's, Newfoundland
        // May 2, 2014 - afyp

        // this code modified at my desk
        // June 18, 2014 -- RL

        // do AJAX transaction
        $.ajax({
            type: 'POST',
            url: submit_to_url,
            data: JSON.stringify(form_data),
            datatype: 'json',
            success: function(result) {
                console.log(result);
                if (result['status'] == 'failure') {
                    submit_error.innerHTML = result['error_msg'];
                }
            }
        })
    })
});

