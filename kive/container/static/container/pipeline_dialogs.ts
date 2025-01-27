import {MethodNode, OutputNode, RawNode} from "./canvas/drydock_objects";
import {CanvasState} from "./canvas/drydock";
import {Container} from "@container/io/PipelineApi";

/**
 * Mini jQuery plugin to make dialogs draggable.
 */
$.fn.extend({
    draggable: function(opt) {
        opt = $.extend({ handle: '', cursor: 'normal' }, opt);
        var $el = opt.handle === '' ? this : this.find(opt.handle);
        
        $el.find('input, select, textarea').on('mousedown', function(e) {
            e.stopPropagation();
        });
        
        $el.css('cursor', opt.cursor).on("mousedown", function(e) {
            var $drag = $(this);
            if (opt.handle === '') {
                $drag.addClass('draggable');
            } else {
                $drag.addClass('active-handle').parent().addClass('draggable');
            }
            
            if (typeof opt.start === 'function') {
                opt.start(this);
            }
            
            $drag.data('z', $drag.data('z') || $drag.css('z-index'));
            
            var z = $drag.data('z'),
                pos = $drag.offset(),
                pos_y = pos.top - e.pageY,
                pos_x = pos.left - e.pageX;
            
            $drag.css('z-index', 1000).parents().off('mousemove mouseup').on("mousemove", function(e) {
                $('.draggable').offset({
                    top:  e.pageY + pos_y,
                    left: e.pageX + pos_x
                });
            }).on("mouseup", function() {
                $(this).removeClass('draggable').css('z-index', z);
            });
            
            e.preventDefault(); // disable selection
        }).on("mouseup", function() {
            if (opt.handle === "") {
                $(this).removeClass('draggable');
            } else {
                $(this).removeClass('active-handle').parent().removeClass('draggable');
            }
            if (typeof opt.stop === 'function') {
                opt.stop(this);
            }
        });
        
        return $el;
    }
});


/**
 * Base class for UI dialogs on the pipeline assembly canvas.
 * UI is used to set pipeline metadata, add new nodes, and access other controls.
 */
export class Dialog {

    private visible = false;

    /**
     * @param jqueryRef
     *      The root element of the dialog as a jQuery object.
     * @param activator
     *      The primary UI control for activating the dialog.
     */
    constructor(public jqueryRef, public activator) {
        activator.click( e => {
            // open this one
            this.show();
            // do not bubble up (which would hit document.click again)
            e.stopPropagation();
            e.preventDefault();
        });
        // capture mouse/key events
        jqueryRef.on('click mousedown keydown', e => e.stopPropagation() );
        // esc closes the dialog
        jqueryRef.on('keydown', e => {
            if (e.which === 27) { // esc
                this.cancel();
            }
        });
        // hide this menu if it's visible
        $(document).click( () => { this.visible && this.cancel(); } );
    }

    /**
     * Opens the dialog
     */
    show() {
        // close all other menus
        $(document).click();
        this.activator.addClass('clicked');
        this.jqueryRef.show().css('left', this.activator.offset().left);
        this.focusFirstEmptyInput();
        this.visible = true;
    }
    
    /**
     * Closes the dialog
     */
    hide() {
        this.activator.removeClass('clicked');
        this.jqueryRef.hide();
        this.visible = false;
    }
    
    /**
     * Focuses the first unfilled input field
     */
    focusFirstEmptyInput() {
        this.jqueryRef.find('input, select').each(function() {
            if (this.value === '') {
                $(this).focus();
                return false; // break;
            }
        });
    }
    
    /**
     * Clears all inputs
     * Child classes should extend this functionality.
     */
    reset() {
        this.jqueryRef.find('input[type="text"], textarea, select').val('');
    }
    
    /**
     * Hides by default - child classes may choose to have this reset the dialog as well.
     */
    cancel() {
        this.hide();
    }

    /**
     * Ensures that all DOM fixtures have been successfully loaded.
     */
    validateInitialization() {
        for (let propertyName in this) {
            if (propertyName[0] === "$" && this[propertyName]['constructor'] === $) {
                if (this[propertyName]['length'] === 0) {
                    throw "Error in dialog: " + propertyName + " is empty.";
                }
            }
        }
    }
}

/**
 * Currently no functionality added on top of normal dialog. This may change in the future.
 */
// class PipelineFamilyDialog extends Dialog {
//
// }

/**
 * Currently no functionality added on top of normal dialog. This may change in the future.
 */
// class PipelineDialog extends Dialog {
//
// }

/**
 * Middle-base class for dialogs incorporating a Node preview canvas.
 */
abstract class NodePreviewDialog extends Dialog {
    protected preview_canvas: HTMLCanvasElement;
    protected is_modal = true;

    /**
     * NodePreviewDialogs have a <canvas> element and are draggable.
     * @param jqueryRef
     *      The root element of the dialog as a jQuery object.
     * @param activator
     *      The primary UI control for activating the dialog.
     */
    constructor(jqueryRef, activator) {
        super(jqueryRef, activator);
        if (jqueryRef.draggable) {
            jqueryRef.draggable();
        }
        this.preview_canvas = <HTMLCanvasElement> $('canvas', jqueryRef)[0];
        this.preview_canvas.width = jqueryRef.width();
        this.preview_canvas.height = 60;
    }

    /**
     * Converts the coords of the preview canvas to the coords of another CanvasState.
     * @param otherCanvasState
     *      the other canvas to translate the node's coords to.
     * @returns {{left: number, top: number}}
     *      the coords that should give an identical page position on otherCanvasState.
     */
    protected translateToOtherCanvas(otherCanvasState: CanvasState) {
        let pos: {left: number, top: number} = $(this.preview_canvas).offset();
        if (this.preview_canvas && pos) {
            pos.left += this.preview_canvas.width  / 2 - otherCanvasState.canvas.offsetLeft;
            pos.top  += this.preview_canvas.height / 2 - otherCanvasState.canvas.offsetTop;
        } else {
            pos.left = 100;
            pos.top  = 200 + Math.round(50 * Math.random());
        }
        return pos;
    }
    
    /**
     * Sync the preview canvas with dialog inputs.
     * Child classes must implement.
     */
    protected abstract triggerPreviewRefresh(): void;
    
    /**
     * Show the dialog.
     * NodePreviewDialogs are modal, so they are not positioned relative to their activator.
     */
    show() {
        super.show();
        this.jqueryRef.css({
            top: 300,
            left: 300
        });
        this.triggerPreviewRefresh();
    }
    
    /**
     * Reset the preview canvas and clear all inputs (via super)
     */
    reset() {
        super.reset();
        // this has the side-effect of clearing the canvas.
        this.clearPreview();
    }
    
    /**
     * Hide and reset all in one.
     */
    cancel() {
        this.hide();
        this.reset();
    }
    
    /**
     * Clear canvas
     */
    protected clearPreview() {
        this.preview_canvas.width = this.preview_canvas.width;
    }
}

/**
 * Dialog for adding new InputNodes to canvas.
 * Includes name and compound datatype.
 */
export class InputDialog extends NodePreviewDialog {
    private $error;
    private $input_name;
    private paired_node: RawNode;
    
    /**
     * In addition to the NodePreviewDialog functionality,
     * InputDialog will wire up all the necessary template elements.
     * @param jqueryRef
     *      The root element of the dialog as a jQuery object.
     * @param activator
     *      The primary UI control for activating the dialog.
     */
    constructor(jqueryRef, activator) {
        super(jqueryRef, activator);
        this.$error = $('#id_dt_error');
        this.$input_name = $('#id_input_name');
        this.paired_node = null;
        this.drawPreviewCanvas();
    }

    /**
     * Draws the node on the preview canvas.
     */
    private drawPreviewCanvas(): void {
        let ctx = this.preview_canvas.getContext('2d');
        let w = this.preview_canvas.width;
        let h = this.preview_canvas.height;
        let node = new RawNode(w / 2, h / 2, '');
        ctx.clearRect(0, 0, w, h);
        node.draw(ctx);
    }

    /**
     * Update the preview canvas.
     */
    triggerPreviewRefresh() {
        // inputs all look the same: take no action
    }

    /**
     * Load an existing InputNode so that we can rename it.
     * @param node
     *      The OutputNode to edit
     */
    load(node: RawNode): void {
        this.reset();
        this.paired_node = node;
        this.$input_name.val(node.label).select(); // default value
    }

    /**
     * Align the dialog to a given coord. Anchor point is center of the dialog.
     * @param x
     *      The x-coordinate.
     * @param y
     *      The y-coordinate.
     */
    align(x: number, y: number): void {
        this.jqueryRef.css({
            left: x - this.jqueryRef.innerWidth()  / 2,
            top:  y - parseInt(this.jqueryRef.css('padding-top'), 10)
        });
    }

    /**
     * Adds a new node to canvasState based on the InputDialog state. Calculates the corresponding coordinate position,
     * checks for name uniqueness, and detects shape collisions. If successful the dialog is reset and closed.
     * @param canvasState
     */
    submit(canvasState: CanvasState) {
        let pos = this.translateToOtherCanvas(canvasState);
        
        // check for empty and duplicate names
        let node_label = this.$input_name.val();
        if (node_label === '') {
            // required field
            this.$error.text("Label is required.");
        } else if (this.paired_node !== null && node_label === this.paired_node.label) {
            /* No change */
            this.hide();
            this.reset();
        } else if (!CanvasState.isUniqueName(canvasState.getInputNodes(), node_label)) {
            this.$error.text('That name has already been used.');
        } else {
            if (this.paired_node !== null) {
                this.paired_node.label = node_label;
                canvasState.valid = false;
            } else {
                let shape = new RawNode(pos.left, pos.top, node_label);

                canvasState.addShape(shape);
                // Second arg: Upon collision, move new shape 0% and move existing objects 100%
                canvasState.detectCollisions(shape, 0);
            }

            this.reset(); // reset text field
            this.hide();
        }
    }
    
    /**
     * Clears the dialog state.
     */
    reset() {
        this.$input_name.val('');
        this.$error.empty();
        this.paired_node = null;
    }
}

/**
 * Singleton UI for picking a colour.
 */
var colourPickerFactory = (function() {
    /**
     * Template pasted here for convenience.
     
     <input #id_select_colour type="hidden">
     <div #colour_picker_menu>
     <div .colour_picker_colour style="background-color: #999;">
     <!-- ... more colours ... -->
     <div #colour_picker_pick .colour_picker_colour style="background-color: #999;">
     
     */
    // Private members
    var $hidden_input = $('#id_select_colour');
    var $pick = $('#colour_picker_pick').click( () => picker.show() );
    var $menu = $('#colour_picker_menu')
        .on('click', 'div', function() {
            picker.pick($(this).css('background-color'));
        });
    var callback: Function = () => {};

    // Exposed methods
    var picker = {
    
        /**
         * colourPicker.show
         * Displays the available choices.
         */
        show: function() {
            var pos = $pick.position();
            $menu.css({ top: pos.top + 20, left: pos.left }).show();
        },
    
        /**
         * colourPicker.pick
         * Sets the current colour choice.
         * @param colour
         *      the colour to choose as a hexadecimal string.
         */
        pick: function(colour) {
            $pick.css('background-color', colour);
            $hidden_input.val(colour);
            $menu.hide();
            callback(colour);
        },
    
        /**
         * colourPicker.val
         * @return the currently picked colour
         */
        val: () => $hidden_input.val(),
    
        /**
         * colourPicker.setCallback
         * set a function to execute when a colour is picked.
         */
        setCallback: (cb: Function) => { callback = cb; }
    };
    return picker;
});

export class MethodDialog extends NodePreviewDialog {
    private $submit_button;
    private $select_method;
    private $input_names;
    private $output_names;
    private $error;
    private container: Container;
    private colour_picker;
    private add_or_revise: string = "add";
    private editing_node: MethodNode;

    /**
     * In addition to the NodePreviewDialog functionality, MethodDialog will wire up all the necessary template
     * elements. It also sets event watchers on UI which is internal to the dialog. Finally, it initializes the method
     * revisions menu which is an asynchronous event.
     * @param jqueryRef
     *      The root element of the dialog as a jQuery object.
     * @param activator
     *      The primary UI control for activating the dialog.
     * @param container
     *      Definition of the container contents.
     */
    constructor(jqueryRef, activator, container: Container) {
        super(jqueryRef, activator);

        this.container = container;
        this.colour_picker = colourPickerFactory(); // not a class-based object - note no "new"
        this.$submit_button = $('#id_method_button');
        this.$select_method = $("#id_select_method");
        this.$input_names = $('#id_input_names');
        this.$output_names = $('#id_output_names');
        this.$error = $('#id_method_error');
        /*
        let option_elements = container.files.map(file_name =>
            $("<option>", {
                value: file_name,
                title: file_name
            }).text(file_name));
        */

        let option_elements = container.files.map(function(filename_driver_pair) {
            if (!filename_driver_pair[1]) {
                return null;
            } else {
                let file_name = filename_driver_pair[0];
                return $("<option>", {
                    value: file_name,
                    title: file_name
                }).text(file_name);
            }
        });

        this.$select_method.empty()
            .append(option_elements).show();

        this.$select_method.change(
            () => this.triggerPreviewRefresh()
        );
        this.colour_picker.setCallback(
            () => this.triggerPreviewRefresh()
        );
        this.$input_names.keyup(
            () => this.triggerPreviewRefresh());
        this.$output_names.keyup(
            () => this.triggerPreviewRefresh());
    }
    
    /**
     * Update the preview canvas based on the dialog state.
     */
    protected triggerPreviewRefresh() {
        this.refreshPreviewCanvasMagnets();
    }

    private refreshPreviewCanvasMagnets() {
        this.drawPreviewCanvas(this.colour_picker.val());
    }
    
    /**
     * Align the dialog to a given coord. Anchor point is top center.
     * @param x
     *      The x-coordinate.
     * @param y
     *      The y-coordinate.
     */
    align(x: number, y: number): void {
        this.jqueryRef.css({
            left: x - this.preview_canvas.width / 2,
            top: y
        });
    }
    
    /**
     * Loads a MethodNode into the MethodDialog so that the user may edit.
     * @param node
     *      The MethodNode to revise.
     */
    load(node: MethodNode): void {
        this.reset();
        this.colour_picker.pick(node.fill);
        this.setToRevise();
        this.editing_node = node;

        this.$select_method.val(node.label);
        this.$input_names.val(
            node.in_magnets.map(magnet => magnet.label).join(" "));
        this.$output_names.val(node.outputs.join(" "));
        this.triggerPreviewRefresh();
    }

    /**
     * Given data from the REST API, draw a MethodNode on the preview canvas.
     * @param colour
     */
    private drawPreviewCanvas (colour?: string): void {
        let driver_name = this.$select_method.val();
        let output_names = this.buildOutputNames();
        let input_cables = this.buildInputCables();
        let n_outputs = output_names.length * 8;
        let n_inputs  = input_cables.length * 8 + 14;

        this.clearPreview();

        this.preview_canvas.height = (n_outputs + n_inputs) / 2 + 55;

        let method = new MethodNode(
            // Ensures node is centred perfectly on the preview canvas
            // For this calculation to be accurate, method node draw params cannot change.
            this.preview_canvas.width / 2 -
            (
                Math.max(0, n_outputs - n_inputs + 48) -
                Math.max(0, n_outputs - n_inputs - 42)
            ) * 0.4330127, // x
            n_inputs / 2 + 20, // y
            colour,
            driver_name,
            input_cables,
            output_names);

        method.draw(this.preview_canvas.getContext('2d'));
    }

    private buildOutputNames() {
        return this.$output_names.val().trim().split(/\s+/);
    }

    private buildInputCables() {
        let input_names = this.$input_names.val().trim().split(/\s+/);
        return input_names.map(
            name => ({
                dataset_name: name
            }));
    }

    /**
     * Adds the MethodNode represented by the current state to the supplied CanvasState.
     * @param canvasState
     *      The CanvasState to add the MethodNode to.
     */
    submit(canvasState: CanvasState) {
        let node_label = this.$select_method.val();
        let input_names = this.$input_names.val();
        let output_names = this.$output_names.val();
        let pos = this.translateToOtherCanvas(canvasState);
        let is_unique = CanvasState.isUniqueName(canvasState.getInputNodes(), node_label);

        if (node_label && is_unique && input_names && output_names) {
            // user selected valid Method Revision
            let method = new MethodNode(
                pos.left,
                pos.top,
                this.colour_picker.val(),
                node_label,
                this.buildInputCables(),
                this.buildOutputNames()
            );
            if (this.add_or_revise === 'add') {
                // create new MethodNode
                canvasState.addShape(method);
            } else {
                // replace the selected MethodNode
                // draw new node over old node
                canvasState.replaceMethod(this.editing_node, method);
                canvasState.selection = [ method ];
            }
            this.hide();
            this.reset();
        } else if (!node_label) {
            // required field
            this.$error.text("Method is required.");
            this.$select_method.focus();
        } else if (!is_unique) {
            this.$error.text('That name has already been used.');
            this.$select_method.focus();
        } else if (!input_names) {
            this.$error.text('Input names are required.');
            this.$input_names.focus();
        } else {
            this.$error.text('Output names are required.');
            this.$output_names.focus();
        }
    }
    
    /**
     * Clears all fields and private variables for future use.
     */
    reset() {
        super.reset();
        this.$error.text('');
        this.setToAdd();
        this.editing_node = null;
    }
    
    /**
     * Sets the dialog to add a new method on submit.
     */
    private setToAdd() {
        this.$submit_button.val('Add Method');
        this.add_or_revise = "add";
    }
    
    /**
     * Sets the dialog to replace a method on submit rather than create a new one.
     */
    private setToRevise() {
        this.$submit_button.val('Revise Method');
        this.add_or_revise = "revise";
    }
}

export class OutputDialog extends NodePreviewDialog {
    /*
     * Shorthand HTML Template pasted here for convenience.
     * It is not guaranteed to be current.
     * Indentation takes the place of closing tags.

    <div #id_output_ctrl .ctrl_menu>
        <canvas>
        <h3>Outputs</h3>
        <form>
            <input #id_output_name type="text">
            <input #id_output_button type="submit" value="OK">
            <div #id_output_error .errortext>
     */
    private $error;
    private $output_name;
    private paired_node: OutputNode;

    /**
     * In addition to the NodePreviewDialog functionality, OutputDialog will wire up all the necessary template
     * elements.
     * @param jqueryRef
     *      The root element of the dialog as a jQuery object.
     * @param activator
     *      The primary UI control for activating the dialog.
     */
    constructor(jqueryRef, activator) {
        super(jqueryRef, activator);
        this.$error = $("#id_output_error");
        this.$output_name = $('#id_output_name');
        this.drawPreviewCanvas();
    }

    /* The following is a hack to get around inconvenient document.click event timing. */
    cancel_: any;
    makeImmune() {
        this.cancel_ = this.cancel;
        this.cancel = function() {
            this.cancel = this.cancel_;
        };
    }
    
    /**
     * Draws an OutputNode on the preview canvas. No need to check the details, all OutputNodes look the same.
     */
    drawPreviewCanvas(): void {
        let ctx = this.preview_canvas.getContext('2d');
        let w = this.preview_canvas.width;
        let h = this.preview_canvas.height;
        let node = new OutputNode(w / 2, h / 2, '');
        ctx.clearRect(0, 0, w, h);
        node.draw(ctx);
    }

    /**
     * Implements abstract method
     */
    triggerPreviewRefresh(): void {
        // outputs all look the same: take no action
    }
    
    /**
     * Load an existing OutputNode so that we can rename it.
     * @param node
     *      The OutputNode to edit
     */
    load(node: OutputNode): void {
        this.reset();
        this.paired_node = node;
        this.$output_name.val(node.label).select(); // default value
    }
    
    /**
     * Align the dialog to a given coord. Anchor point is center of the dialog.
     * @param x
     *      The x-coordinate.
     * @param y
     *      The y-coordinate.
     */
    align(x: number, y: number): void {
        this.jqueryRef.css({
            left: x - this.jqueryRef.innerWidth()  / 2,
            top:  y - parseInt(this.jqueryRef.css('padding-top'), 10)
        });
    }
    
    /**
     * Renames the OutputNode.
     * @param canvasState
     *      The CanvasState to operate on (currently only used to trigger a redraw)
     */
    submit(canvasState: CanvasState) {
        var label = this.$output_name.val();

        if (this.paired_node) {
            if (this.paired_node.label === label) {
                /* No change */
                this.hide();
                this.reset();
            } else if (CanvasState.isUniqueName(canvasState.getOutputNodes(), label)) {
                /* Name is changed and valid */
                this.paired_node.setLabel(label);
                canvasState.valid = false;
                this.hide();
                this.reset();
            } else {
                /* Non-unique name entered */
                this.$error.html('<img src="/static/pipeline/img/warning_icon.png"> That name has already been used.');
            }
        } else {
            let pos = this.translateToOtherCanvas(canvasState);

            // check for empty and duplicate names
            if (label === '') {
                // required field
                this.$error.text("Label is required.");
            } else if (!CanvasState.isUniqueName(canvasState.getOutputNodes(), label)) {
                this.$error.html('<img src="/static/pipeline/img/warning_icon.png"> That name has already been used.');
            } else {
                let shape = new OutputNode(pos.left, pos.top, label);
                canvasState.addShape(shape);
                // Second arg: Upon collision, move new shape 0% and move existing objects 100%
                canvasState.detectCollisions(shape, 0);
        
                this.reset(); // reset text field
                this.hide();
            }
        }
    }
    
    /**
     * Clears all inputs and private members for future use.
     */
    reset() {
        this.$output_name.val('');
        this.$error.empty();
        this.paired_node = null;
    }
    
    /**
     * Closes the dialog and removes the working from the CanvasState.
     * (Assumes the user got here by dragging an Connector into the canvasState's OutputZone.)
     * @param canvasState
     */
    cancel(canvasState?: CanvasState) {
        super.cancel();
        if (this.paired_node && canvasState) {
            canvasState.connectors.pop();
            canvasState.valid = false;
        }
    }
}

/**
 * Dialog controlling how to arrange and display Nodes on the canvasState.
 */
export class ViewDialog extends Dialog {
    
    private static execOrderDisplayOptions = { always: true, never: false, ambiguous: undefined };
    
    /**
     * Change whether canvasState shows order numbers on MethodNodes.
     * @param canvasState
     * @param value One of 3 configuration options for canvasState.force_show_exec_order.
     */
    static changeExecOrderDisplayOption (canvasState: CanvasState, value: "always"|"never"|"ambiguous") {
        if (ViewDialog.execOrderDisplayOptions.hasOwnProperty(value)) {
            canvasState.force_show_exec_order = ViewDialog.execOrderDisplayOptions[value];
            canvasState.valid = false;
        }
    }
    
    /**
     * Align nodes along an axis.
     * @param canvasState
     * @param axis A string from "x"|"y"|"iso_x"|"iso_y"
     */
    static alignCanvasSelection (canvasState: CanvasState, axis: "x"|"y"|"iso_x"|"iso_y") {
        canvasState.alignSelection(axis);
    }
    
}