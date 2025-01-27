import { CanvasState } from "../canvas/drydock";
import { Geometry } from "../canvas/geometry";
import { CdtNode, RawNode, MethodNode, Magnet, OutputNode } from "../canvas/drydock_objects";
import { PipelineForApi, ApiXputData, StepForApi, ApiCableData, OutcableForApi } from "./PipelineApi";

/**
 * This method serializes the pipeline into an object that can be
 * fed to the backend REST API.
 *
 * Will throw errors if there are any. Contain this method in a
 * try/catch block.
 *
 * See: /api/pipelines/
 *
 * @param canvasState: the state object of the pipeline canvas
 * @param metadata: starting data that all of the pipeline details
 * will be added to. Object format matches the JSON structure of the
 * API.
 *
 * form_data will be merged with existing this.metadata if setMetadata()
 * is used first.
 */
export function serializePipeline (canvasState: CanvasState, metadata?: PipelineForApi) {

    let pipeline_outputs = canvasState.getOutputNodes();
    let pipeline_inputs = canvasState.getInputNodes();
    let pipeline_steps = canvasState.getSteps();
    let canvas_dimensions = canvasState.getAspectRatio();

    // This is a trivial modification until we hit a non trivial
    // @todo: This variable is not used. Why?
    let is_trivial = true;

    // Check graph integrity
    // Warning: This will throw errors if pipeline is not complete.
    // serialize() should be wrapped in a try/catch block to account for this.
    canvasState.assertIntegrity();

    // Sort inputs and outputs by their isometric position, left-to-right, top-to-bottom
    // (sort of like reading order if you tilt your screen 30° clockwise).
    pipeline_inputs.sort(Geometry.isometricSort);
    pipeline_outputs.sort(Geometry.isometricSort);

    let form_data = metadata || <PipelineForApi> {};
    form_data.steps = serializeSteps(pipeline_steps, canvas_dimensions);
    form_data.inputs = serializeInputs(pipeline_inputs, canvas_dimensions);
    form_data.outcables = serializeOutcables(pipeline_outputs, pipeline_steps, canvas_dimensions);

    // this code written on Signal Hill, St. John's, Newfoundland
    // May 2, 2014 - afyp

    // this code modified at my desk
    // June 18, 2014 -- RL

    // I code at my desk too.
    // July 30, 2014 - JN

    // How did I even computer?
    // April 28, 2048 - Cat

    return form_data;
}

function serializeInputs(pipeline_inputs: (CdtNode|RawNode)[], canvas_dimensions: [ number, number ]): ApiXputData[] {
    let serialized_inputs = [];
    let [ x_ratio, y_ratio ] = canvas_dimensions;

    // Construct the input updates
    for (let i = 0; i < pipeline_inputs.length; i++) {
        let input = pipeline_inputs[i];
        let structure = null;

        // Set up the compound datatype
        if (CanvasState.isCdtNode(input)) {
            structure = {
                compounddatatype: input.pk,
                min_row: null,
                max_row: null
            };
        }

        // Slap this input into the form data
        serialized_inputs[i] = {
            structure: structure,
            dataset_name: input.label,
            dataset_idx: i + 1,
            x: input.x / x_ratio,
            y: input.y / y_ratio
        };
    }

    return serialized_inputs;
}

function serializeSteps(pipeline_steps: MethodNode[], canvas_dimensions: [ number, number ]): StepForApi[] {
    let serialized_steps = [];
    let [ x_ratio, y_ratio ] = canvas_dimensions;

    // Add arguments for input cabling
    for (let i = 0; i < pipeline_steps.length; i++) {
        // TODO: Make this work for nested pipelines

        let step = pipeline_steps[i];

        // Put the method in the form data
        serialized_steps[i] = {
            transformation: step.pk,  // to retrieve Method
            transformation_type: "Method",
            step_num: i + 1,  // 1-index (pipeline inputs are index 0)
            x: step.x / x_ratio,
            y: step.y / y_ratio,
            name: step.label,
            fill_colour: step.fill,
            new_code_resource_revision_id: (
                step.new_code_resource_revision ?
                    step.new_code_resource_revision.id :
                    null),
            new_outputs_to_delete_names: step.outputs_to_delete
        };

        if (step.new_dependencies && step.new_dependencies.length) {
            serialized_steps[i].new_dependency_ids =
                step.new_dependencies.map(dependency => dependency.id);
        }

        // retrieve Connectors
        serialized_steps[i].cables_in = serializeInMagnets(step.in_magnets, pipeline_steps);
    }

    return serialized_steps;
}

function serializeInMagnets(in_magnets: Magnet[], pipeline_steps: MethodNode[]): ApiCableData[] {
    let serialized_cables = [];

    // retrieve Connectors
    for (let j = 0; j < in_magnets.length; j++) {
        let magnet = in_magnets[j];

        if (magnet.connected.length === 0) {
            continue;
        }

        let connector = magnet.connected[0];
        let source = magnet.connected[0].source.parent;

        serialized_cables[j] = {
            source_dataset_name: connector.source.label,
            dest_dataset_name: connector.dest.label,
            source_step: CanvasState.isMethodNode(source) ? pipeline_steps.indexOf(source) + 1 : 0,
            keep_output: false, // in the future this can be more flexible
            custom_wires: [] // no wires for a raw cable
        };
    }

    return serialized_cables;
}

function serializeOutcables(
    pipeline_outputs: OutputNode[],
    pipeline_steps: MethodNode[],
    canvas_dimensions: [ number, number ]
): OutcableForApi[] {
    let serialized_outputs = [];
    let [ x_ratio, y_ratio ] = canvas_dimensions;

    // Construct outputs
    for (let i = 0; i < pipeline_outputs.length; i++) {
        let output = pipeline_outputs[i];
        let connector = output.in_magnets[0].connected[0];
        let source_step = connector.source.parent;

        serialized_outputs[i] = {
            output_name: output.label,
            output_idx: i + 1,
            output_cdt: connector.source.cdt,
            source: source_step.pk,
            source_step: pipeline_steps.indexOf(source_step) + 1, // 1-index
            source_dataset_name: connector.source.label, // magnet label
            x: output.x / x_ratio,
            y: output.y / y_ratio,
            custom_wires: [] // in the future we might have this
        };
    }
    return serialized_outputs;
}