"""Code that is responsible for the execution of Pipelines."""

from django.core.files import File
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from librarian.models import ExecRecordIn

# Import our Shipyard models module.
import file_access_utils, logging_utils
import os.path
import shutil
import logging, sys, time
import tempfile
import archive.models, librarian.models, metadata.models, pipeline.models, transformation.models
import datachecking.models

class Sandbox:
    """
    A Sandbox is the environment in which a Pipeline is run. It contains
    all the information necessary to run the Pipeline, including the code
    for all steps of the Pipeline, and the data to feed in. The Sandbox keeps
    track of a single Run of the Pipeline it was created with. 
    
    Note that Sandboxes are single-use; that is, a Pipeline may only be run
    once in a Sandbox. To run the same Pipeline again, you must create a new
    Sandbox.
    """

    # sd_fs_map: maps SymDS to a FS path: the path where a data
    # file would be if it were created (Whether or not it is there)
    # If the path is None, the SD is on the DB.

    # socket_map: maps (generator, socket) to SDs.
    # A generator is a cable, or a pipeline step. A socket is a TI/TO.
    # If the generator is none, the socket is a pipeline input.
    # This will be used to look up inputs when running a pipeline.
    
    # ps_map: maps PS to (path, RunStep of PS): the path tells you
    # the directory that the PS would have been run in
    # (whether or not it was): the RunStep tells you what inputs are
    # needed (Which in turn will lead back to an sd_fs_map lookup),
    # and allows you to fill it in on recovery.
        
    # cable_map maps cables to ROC/RSIC.

    def __init__(self, user, pipeline, inputs, sandbox_path=None):
        """
        Sets up a sandbox environment to run a Pipeline: space on
        the file system, along with sd_fs_map/socket_map/etc.

        INPUTS
        user          User running the pipeline.
        pipeline      Pipeline to run.
        inputs        List of SDs to feed into the pipeline.
        sandbox_path  Where on the filesystem to execute.

        PRECONDITIONS
        1) Inputs must have real data (For top level runs), OR
        2) Inputs be in sd_fs_map (sub-runs)
        """
        self.logger = logging.getLogger(self.__class__.__name__)

        self.user = user
        self.pipeline = pipeline
        self.inputs = inputs
        self.sd_fs_map = {}
        self.socket_map = {}
        self.cable_map = {}
        self.ps_map = {}
        self.check_inputs()

        self.logger.debug("initializing maps")
        self.run = pipeline.pipeline_instances.create(start_time=timezone.now(), user=self.user)
        for i, pipeline_input in enumerate(inputs, start=1):
            corresp_pipeline_input = pipeline.inputs.get(dataset_idx=i)
            self.socket_map[(self.run, None, corresp_pipeline_input)] = pipeline_input
            self.sd_fs_map[pipeline_input] = None

        # Determine a sandbox path.
        self.sandbox_path = sandbox_path
        if sandbox_path == None:
            self.sandbox_path = tempfile.mkdtemp(prefix="user{}_run{}_".format(self.user, self.run.pk))

        # Make the sandbox directory.
        self.logger.debug("file_access_utils.set_up_directory({})".format(self.sandbox_path))
        file_access_utils.set_up_directory(self.sandbox_path)

    def check_inputs(self):
        """
        Are the supplied inputs are appropriate for the supplied pipeline?

        We check if the input CDT's are restrictions of the pipeline's expected
        input CDT's, and that the number of rows is in the range that the pipeline
        expects. We don't rearrange inputs that are in the wrong order.
        """
        # First quick check that the number of inputs are the same.
        if len(self.inputs) != self.pipeline.inputs.count():
            raise ValueError('Pipeline "{}" expects {} inputs, but {} were supplied'
                             .format(self.pipeline, self.pipeline.inputs.count(), len(self.inputs)))
        
        # Check each individual input.
        for i, supplied_input in enumerate(self.inputs, start=1):
            pipeline_input = self.pipeline.inputs.get(dataset_idx=i)
            pipeline_raw = pipeline_input.is_raw()
            supplied_raw = supplied_input.is_raw()

            if pipeline_raw != supplied_raw:
                if pipeline_raw:
                    raise ValueError('Pipeline "{}" expected input {} to be raw, but got one with CompoundDatatype '
                                     '"{}"'.format(self.pipeline, i, supplied_input.get_cdt()))
                raise ValueError('Pipeline "{}" expected input {} to be of CompoundDatatype "{}", but got raw'
                                 .format(self.pipeline, i, pipeline_input.get_cdt()))

            # Neither is raw.
            supplied_cdt = supplied_input.get_cdt()
            pipeline_cdt = pipeline_input.get_cdt()

            if not supplied_cdt.is_restriction(pipeline_cdt):
                raise ValueError('Pipeline "{}" expected input {} to be of CompoundDatatype "{}", but got one with '
                                 'CompoundDatatype "{}"'.format(self.pipeline, i, pipeline_cdt, supplied_cdt))

            # The CDT's match. Is the number of rows okay?
            minrows = pipeline_input.get_min_row() or 0
            maxrows = pipeline_input.get_max_row() 
            maxrows = maxrows if maxrows is not None else sys.maxint

            if not minrows <= supplied_input.num_rows() <= maxrows:
                raise ValueError('Pipeline "{}" expected input {} to have between {} and {} rows, but got one with {}'
                                 .format(self.pipeline, i, minrows, maxrows, supplied_input.num_rows()))

    def register_symbolicdataset(self, symbolicdataset, location):
        """Set the location of a SymbolicDataset on the file system.

        If the SymbolicDataset is already in the Sandbox (ie. it is in
        sd_fs_map), do not update the existing location.

        INPUTS
        symbolicdataset     SymbolicDataset to register
        location            file path of symbolicdataset in the Sandbox
        """
        try:
            self.sd_fs_map[symbolicdataset] = self.sd_fs_map[symbolicdataset] or location
        except KeyError:
            self.sd_fs_map[symbolicdataset] = location

    def find_symbolicdataset(self, symbolicdataset):
        """Find the location of a SymbolicDataset on the file system.

        Preferentially, we return the SymbolicDataset's location in the
        Sandbox. If it's not there, we try to return the path to the 
        Dataset file in the databale.

        INPUTS
        symbolicdataset     SymbolicDataset to locate

        OUTPUTS
        location            the path of symbolicdataset in either the
                            Sandbox or in the Database 
        """
        try:
            location = self.sd_fs_map[symbolicdataset]
            self.logger.debug("Dataset {} file exists in the Sandbox".format(symbolicdataset))
        except KeyError:
            self.logger.debug("Dataset {} is not in the Sandbox".format(symbolicdataset))
            location = None

        if location is None:
            try:
                location = symbolicdataset.dataset.dataset_file.name
                self.logger.debug("Dataset {} has real data".format(symbolicdataset))
            except librarian.models.Dataset.DoesNotExist:
                pass

        return (location if location and os.access(location, os.R_OK) else None)
        
    # TODO: not sure where to put this
    def sanitize_save(self, obj):
        """Clean, save, and complete_clean an object."""
        obj.clean()
        obj.save()
        obj.complete_clean()
        return obj

    def _find_cable_execrecord(self, runcable, input_SD):
        """Find an ExecRecord which may be reused by a RunCable

        If an ExecRecord with real data exists, it will be returned
        preferentially over those without data.

        INPUTS
        runcable        RunCable trying to reuse an ExecRecord
        input_SD        SymbolicDataset to feed the cable

        OUTPUTS
        execrecord      ExecRecord which may be reused, or None if no
                        ExecRecord exists
        """
        execrecord = None
        cable = runcable.component

        # Get the content type (RSIC/ROC) of the ExecRecord's ExecLog
        record_contenttype = ContentType.objects.get_for_model(type(runcable))
        self.logger.debug("Searching for reusable cable ER - (linked to an '{}' ExecLog)".format(record_contenttype))

        # Look at ERIs linked to the same cable type, with matching input SD
        candidate_ERIs = ExecRecordIn.objects.filter(execrecord__generator__content_type=record_contenttype, 
                                                     symbolicdataset=input_SD)

        for candidate_ERI in candidate_ERIs:
            self.logger.debug("Considering ERI {} for ER reuse or update".format(candidate_ERI))
            candidate_execrecord = candidate_ERI.execrecord
            candidate_cable = candidate_execrecord.general_transf()

            if type(cable).__name__ == "PipelineOutputCable":
                compatible = cable.is_compatible(candidate_cable)
            else:
                compatible = cable.is_compatible(candidate_cable, input_SD.structure.compounddatatype)

            if compatible:
                self.logger.debug("Compatible ER found")
                if candidate_execrecord.execrecordouts.first().has_data():
                    return candidate_execrecord

                # No data, so we keep it in mind and continue searching.
                execrecord = candidate_execrecord

        return execrecord

    # TODO: this needs testing
    # TODO: member function of Pipeline*Cable?
    def _find_cable_compounddatatype(self, cable):
        """Find a CompoundDatatype for the output of a cable.

        INPUTS
        cable       the cable we want a CompoundDatatype for

        OUTPUTS
        output_CDT  a compatible CompoundDatatype for the cable's 
                    output, or None if one doesn't exist

        PRE
        cable is neither raw nor trivial
        """
        if cable.is_incable:
            wires = cable.custom_wires.all()
        else:
            wires = cable.custom_outwires.all()

        # Use wires to determine the CDT of the output of this cable
        all_members = metadata.models.CompoundDatatypeMember.objects # shorthand
        compatible_CDTs = None
        for wire in wires:
            # Find all CompoundDatatypes with correct members.
            candidate_members = all_members.filter(datatype=wire.source_pin.datatype,
                                                   column_name=wire.dest_pin.column_name,
                                                   column_idx=wire.dest_pin.column_idx)
            candidate_CDTs = set([m.compounddatatype for m in candidate_members])
            if compatible_CDTs is None:
                compatible_CDTs = candidate_CDTs
            else:
                compatible_CDTs &= candidate_CDTs # intersection
            if not compatible_CDTs:
                return None

        for output_CDT in compatible_CDTs:
            if output_CDT.members.count() == len(wires):
                return output_CDT

        return None

    # TODO: member function of Pipeline*Cable?
    def _create_cable_compounddatatype(self, cable):
        """Create a CompoundDatatype for the output of a cable.

        INPUTS
        cable       the cable we want a CompoundDatatype for

        OUTPUTS
        output_CDT  a new CompoundDatatype for the cable's output

        PRE
        cable is neither raw nor trivial
        """

        output_CDT = metadata.models.CompoundDatatype()
        if type(cable) == pipeline.models.PipelineStepInputCable:
            wires = cable.custom_wires.all()
        else:
            wires = cable.custom_outwires.all()

        # Use wires to determine the CDT of the output of this cable
        for wire in wires:
            self.logger.debug("Adding CDTM: {} {}".format(wire.dest_pin.column_name, wire.dest_pin.column_idx))
            output_SD_CDT.members.create(datatype=wire.source_pin.datatype,
                                         column_name=wire.dest_pin.column_name,
                                         column_idx=wire.dest_pin.column_idx)

        output_CDT.clean()
        output_CDT.save()
        return output_CDT

    def _create_cable_dataset(self, runcable, output_SD, output_path):
        """Create a Dataset for cable output.
        
        INPUTS
        runcable        RunCable responsible for cable execution
        output_SD       SymbolicDataset output by cable
        output_path     where the cable wrote its output
        """
        self.logger.debug("Cable keeps output for nontrivial cable: creating dataset")
        dataset_name = "{} {} {}".format(self.run.name, type(runcable.component).__name__, runcable.pk)

        with open(output_path, "rb") as f:
            archive.models.Dataset(created_by=runcable, dataset_file = File(f), name=dataset_name,
                                   symbolicdataset=output_SD, user=self.run.user).save()

    def _create_cable_execrecord(self, cable, execlog, input_SD, output_SD):
        """Create an ExecRecord for a cable execution."""
        execrecord = execlog.execrecords.create()
        execrecord.execrecordins.create(generic_input=cable.source, symbolicdataset=input_SD)
        execrecord.execrecordouts.create(generic_output=cable.dest, symbolicdataset=output_SD)
        execrecord.complete_clean()
        return execrecord

    def _create_symbolicdataset(self, compounddatatype):
        """Create an empty SymbolicDataset."""
        symbolicdataset = librarian.models.SymbolicDataset(MD5_checksum="")
        symbolicdataset.save()

        if compounddatatype is not None:
            # Add structure to the generated SD
            self.logger.debug("Registering SD's CDT structure")
            symbolicdataset.create_structure(compounddatatype)
        
        symbolicdataset.clean()
        return symbolicdataset

    def _hash_symbolicdataset(self, symbolicdataset, path):
        """Compute the MD5 hash for a SymbolicDataset."""
        with open(path, "rb") as f:
            symbolicdataset.MD5_checksum = file_access_utils.compute_md5(f)
            self.logger.debug("Computed MD5 for file: {}".format(symbolicdataset.MD5_checksum))
            symbolicdataset.save()

    # TODO: should be a member function of RunAtomic?
    def _finalize_runatomic(self, runatomic, execrecord, reused):
        """Complete a RunAtomic by setting execrecord and reused."""
        runatomic.reused = reused
        runatomic.execrecord = execrecord
        runatomic.end_time = timezone.now()
        runatomic.save()
        runatomic.complete_clean()

    def _update_cable_maps(self, runcable, output_SD, output_path):
        """Update maps after cable execution.
        
        INPUTS
        runcable        RunCable created for cable execution
        output_SD       SymbolicDataset output by cable
        output_path     where the cable wrote its output
        """
        self.register_symbolicdataset(output_SD, output_path)
        cable = runcable.component
        self.socket_map[(runcable.parent_run, cable, cable.dest)] = output_SD
        self.cable_map[cable] = runcable

    def _register_missing_output(self, output_SD, execlog, start_time):
        """Create a failed ContentCheckLog for missing cable output
        
        INPUTS
        output_SD       SymbolicDataset cable was supposed to output
        execlog         ExecLog for cable execution which didn't produce
                        output
        start_time      time when we started checking for missing output
        """
        self.logger.error("File doesn't exist - creating CCL with BadData")
        ccl = output_SD.content_checks.create(start_time=start_time, end_time=timezone.now(), execlog=execlog)
        ccl.baddata.create(missing_output=True)

    def recover_cable(self, cable, invoking_record):
        """Execute cable in recovery mode.

        INPUTS
        cable               cable to execute in recovery mode
        invoking_record     RunAtomic which initiated this recovery

        NOTES
        Recovering is to re-compute something reused to recover the data
        """

        self.logger.debug("STARTING EXECUTING {} '{}' IN RECOVERY MODE".format(type(cable).__name__, cable))
        self.logger.debug("Recovering - will update old ER")
        
        # Retrieve appropriate RSIC/ROC
        curr_record = self.cable_map[cable]

        # Retrieve input_SD and output_path from maps
        curr_ER = curr_record.execrecord
        input_SD = curr_ER.execrecordins.first().symbolicdataset
        output_SD = curr_ER.execrecordouts.first().symbolicdataset
        output_path = self.find_symbolicdataset(output_SD)
        dataset_path = self.find_symbolicdataset(input_SD)

        # Is input on the file system / does input have actual data? No.
        if dataset_path is None:

            # Recover dataset.
            self.logger.debug("Symbolic only: running recover({})".format(input_SD))

            # Success? No.
            if not self.recover(input_SD, curr_record):

                # End. Return incomplete curr_record.
                self.logger.warn("Recovery failed - returning incomplete RSIC/ROC (missing ExecLog)")
                return self.sanitize_save(curr_record)

            # Success? Yes.
            dataset_path = self.find_symbolicdataset(input_SD)
            self.logger.debug("Dataset recovered: running run_cable({})".format(dataset_path))

        # Create ExecLog invoked by the recovering RunAtomic.
        curr_log = archive.models.ExecLog.create(curr_record, invoking_record)

        # Run Cable (this completes EL).
        cable.run_cable(dataset_path, output_path, curr_record, curr_log)

        # Register ExecLog with RSIC/ROC.
        curr_record.log.add(curr_log)

        self.logger.debug("Validating file created by execute_cable")
        start_time = timezone.now()

        # File created? No.
        if not os.access(output_path, os.R_OK):

            # Make BadData (Missing output).
            self._register_missing_output(output_SD, curr_log, start_time)

            # End. Return curr_record.
            return self.sanitize_save(curr_record)

        # File created? Yes.
        # Perform integrity check.
        self.logger.debug("Performing integrity check of previously generated dataset")
        output_SD.check_integrity(output_path, curr_log, output_SD.MD5_checksum)

        self.logger.debug("This was a recovery - not linking RSIC/RunOutputCable to ExecRecord")
        self.logger.debug("DONE EXECUTING {} '{}' IN RECOVERY MODE".format(type(cable).__name__, cable))

        # End. Return curr_record.
        return self.sanitize_save(curr_record)
            
    def execute_cable(self, cable, input_SD, output_path, parent_record):
        """Execute cable on the input, not in recovery mode.

        INPUTS
        input_SD        SD fed into the PSIC/POC.
        output_path     Where the output file should be written.
        parent_record   The RS for this PSIC / run for this POC.

        OUTPUTS
        curr_record     RSIC/ROC that describes this execution.

        NOTES
        output_path is recovered using the maps.
        sd_fs_map and cable_map will have been updated.

        PRECONDITIONS
        1) input_SD has an appropriate CDT for feeding this cable.
        2) All the _maps are "up to date" for this step
        3) input_SD is clean and not sour
        """
        # TODO: add assertion for precondition 1
        assert input_SD.clean() is None
        assert input_SD.is_OK()

        self.logger.debug("STARTING EXECUTING CABLE")

        # Create new RSIC/ROC
        curr_record = archive.models.RunCable.create(cable, parent_record)
        self.logger.debug("Not recovering - created {}".format(curr_record.__class__.__name__))
        self.logger.debug("Cable keeps output? {}".format(curr_record.keeps_output()))

        curr_ER = self._find_cable_execrecord(curr_record, input_SD)

        # ER with compatible cable exists? Yes.
        if curr_ER:
            output_SD = curr_ER.execrecordouts.first().symbolicdataset

            # ER is completely reusable? Yes.
            if not curr_record.keeps_output() or output_SD.has_data():
                self.logger.debug("Reusing ER {}".format(curr_ER))
                self._update_cable_maps(curr_record, output_SD, output_path)
                self._finalize_runatomic(curr_record, curr_ER, True)
                return curr_record

        # ER with compatible cable exists and completely reusable? No.
        curr_record.reused = False
        self.logger.debug("No ER to completely reuse - committed to executing cable")

        # Get or create CDT for cable output (Evaluate cable wiring)
        if cable.is_trivial():
            output_SD_CDT = input_SD.get_cdt()
        else:
            output_SD_CDT = self._find_cable_compounddatatype(cable) or self._create_cable_compounddatatype(cable) 

        dataset_path = self.find_symbolicdataset(input_SD)

        # Is input in the sandbox, or does input have actual data? No.
        if dataset_path is None:

            # Recover dataset.
            self.logger.debug("Symbolic only: running recover({})".format(input_SD))

            # Success? No.
            if not self.recover(input_SD, curr_record):

                # End. Return incomplete curr_record.
                self.logger.warn("Recovery failed - returning incomplete RSIC/ROC (missing ExecLog)")
                return self.sanitize_save(curr_record)

            # Success? Yes.
            dataset_path = self.find_symbolicdataset(input_SD)
            self.logger.debug("Dataset recovered: running run_cable({})".format(dataset_path))

        # Create ExecLog invoked by this RunCable.
        curr_log = archive.models.ExecLog.create(curr_record, curr_record)

        # Run cable (this completes EL).
        cable.run_cable(dataset_path, output_path, curr_record, curr_log)

        had_ER_at_beginning = curr_ER is not None

        # Creating a new ER, or filling one in? Creating new.
        if not had_ER_at_beginning:
            self.logger.debug("No ER already in use - creating fresh cable ER + ERI/ERO")

            # Create SymbolicDataset for cable output.
            if cable.is_trivial():
                output_SD = input_SD
            else:
                output_SD = self._create_symbolicdataset(output_SD_CDT)

            # Make ER, linking it to the EL.
            curr_ER = self._create_cable_execrecord(cable, curr_log, input_SD, output_SD)

        # Link ER to RunCable.
        curr_record.execrecord = curr_ER

        self.logger.debug("Validating file created by execute_cable")
        start_time = timezone.now()

        # File created? No.
        if not os.access(output_path, os.R_OK):

            # Make BadData (Missing output).
            self._register_missing_output(output_SD, curr_log, start_time)

            # End. Return curr_record.
            return self.sanitize_save(curr_record)
            
        # File created? Yes.
        # Set symbolic dataset's MD5 if this is the first time the file
        # was generated, and the cable is non-trivial.
        if not cable.is_trivial() and not had_ER_at_beginning:
            self._hash_symbolicdataset(output_SD, output_path)

        # If cable keeps output, register dataset with SD + RSIC/ROC
        if curr_record.keeps_output() and not cable.is_trivial():
            self._create_cable_dataset(curr_record, output_SD, output_path)
        else:
            self.logger.debug("Cable doesn't keep output or cable is trivial: not creating a dataset")

        # Did ER already exist, or is cable trivial? Yes.
        if had_ER_at_beginning or cable.is_trivial():
            if cable.is_trivial():
                self.logger.debug("Performing integrity check of trivial output")
            else:
                self.logger.debug("Performing integrity check of previously generated dataset")

            # Perform integrity check.
            output_SD.check_integrity(output_path, curr_log, output_SD.MD5_checksum)

        # Did ER already exist, or is cable trivial? No.
        else:
            self.logger.debug("Performing content check for output generated for the first time")
            summary_path = "{}_summary".format(output_path)

            # Perform content check.
            output_SD.check_file_contents(output_path, summary_path, cable.min_rows_out, cable.max_rows_out, curr_log)

        # Check OK? Yes.
        if output_SD.is_OK():

            # Success! Update sd_fs/socket/cable_map.
            self._update_cable_maps(curr_record, output_SD, output_path)

        self.logger.debug("DONE EXECUTING {} '{}'".format(type(cable).__name__, cable))

        # End. Return curr_record.
        return self.sanitize_save(curr_record)

    def execute_step(self, curr_run, pipelinestep, inputs, step_run_dir=None, recover=False, invoking_record=None):
        """
        Execute the PipelineStep on the inputs.

        * If code is run, outputs go to paths specified in output_paths.
        * Requisite code is placed in step_run_dir.
        * If step_run_dir is None, default is [sandbox path]/step[stepnum].
        * If recovering, doesn't create new RS/ER; fills in old RS with a new EL
          In this case, step_run_dir is ignored and retrieved using the maps.

        Inputs written to:  [step run dir]/input_data/step[step num]_[input name]
        Outputs written to: [step run dir]/output_data/step[step num]_[output name]
        Logs written to:    [step run dir]/logs/step[step num]_std(out|err).txt
        """

        self.logger.debug("STARTING EXECUTION OF STEP")

        curr_ER = None
        output_paths = []
        inputs_after_cable = []
        had_ER_at_beginning = False

        if step_run_dir is None:
            step_run_dir = os.path.join(self.sandbox_path, "step{}".format(pipelinestep.step_num))

        log_dir = os.path.join(step_run_dir, "logs")
        out_dir = os.path.join(step_run_dir, "output_data")
        in_dir = os.path.join(step_run_dir, "input_data")

        if not recover:
            self.logger.debug("Not recovering - creating new RunStep")
            curr_RS = pipelinestep.pipelinestep_instances.create(start_time=timezone.now(), run=curr_run)

            if not invoking_record:
                invoking_record = curr_RS

            self.logger.debug("Preparing file system for sandbox")
            for workdir in [step_run_dir, in_dir, out_dir, log_dir]:
                file_access_utils.set_up_directory(workdir)

            for curr_output in pipelinestep.transformation.outputs.all().order_by("dataset_idx"):
                file_suffix = "raw" if curr_output.is_raw() else "csv"
                file_name = "step{}_{}.{}".format(pipelinestep.step_num, curr_output.dataset_name,file_suffix)
                output_path = os.path.join(out_dir,file_name)
                output_paths.append(output_path)

            self.logger.debug("Running step's input PSICs")
            for curr_input in pipelinestep.transformation.inputs.all().order_by("dataset_idx"):
                corresp_cable = pipelinestep.cables_in.get(dest=curr_input)
                cable_dir = os.path.join(in_dir,"step{}_{}".format(pipelinestep.step_num,curr_input.dataset_name))
                self.logger.debug("execute_cable('{}','{}','{}','{}')".format(corresp_cable, inputs[curr_input.dataset_idx-1], cable_dir, curr_RS))
                curr_RSIC = self.execute_cable(corresp_cable, inputs[curr_input.dataset_idx-1],cable_dir,curr_RS)
                inputs_after_cable.append(curr_RSIC.execrecord.execrecordouts.all()[0].symbolicdataset)

            curr_RS.clean()

            # FIXME: SD generated from the previous step wasn't checked and so cannot be used
            self.logger.debug("Looking for ER with same transformation + input SDs")
            if type(pipelinestep.transformation).__name__ != "Pipeline":
                curr_ER = pipelinestep.transformation.find_compatible_ER(inputs_after_cable)

            if curr_ER != None:
                self.logger.debug("Found ER, checking it provides outputs needed")
                had_ER_at_beginning = True
                outputs_needed = pipelinestep.outputs_to_retain()

                if curr_ER.provides_outputs(outputs_needed):
                    self.logger.debug("Completely reusing ER {} - updating maps".format(curr_ER))
                    curr_RS.reused = True
                    curr_RS.execrecord = curr_ER
                    curr_RS.clean()
                    curr_RS.save()

                    # This step would have been executed at step_run_dir with curr_RS
                    self.ps_map[pipelinestep] = (step_run_dir, curr_RS)

                    for step_output in pipelinestep.transformation.outputs.all():
                        corresp_ero = curr_ER.execrecordouts.get(
                                content_type=ContentType.objects.get_for_model(type(step_output)),
                                object_id=step_output.pk)
                        corresp_SD = corresp_ero.symbolicdataset
                        corresp_path = output_paths[step_output.dataset_idx-1]

                        if corresp_SD not in self.sd_fs_map:
                            self.sd_fs_map[corresp_SD] = corresp_path

                        # This pipeline step, with the downstream TI, maps to corresp_SD
                        self.socket_map[(curr_run, pipelinestep, step_output)] = corresp_SD

                    self.logger.debug("Finished completely reusing ER")
                    curr_RS.complete_clean()
                    return curr_RS

                self.logger.debug("Found ER, but need to perform computation to fill it in")
                curr_RS.reused = False
            else:
                self.logger.debug("No compatible ER found - will create fresh ER")
                curr_RS.reused = False

        else:
            self.logger.debug("Recovering step")
            step_run_dir, curr_RS = self.ps_map[pipelinestep]
            curr_ER = curr_RS.execrecord
            had_ER_at_beginning = True

            for curr_output in pipelinestep.transformation.outputs.all().order_by("dataset_idx"):
                corresp_SD = self.socket_map[(curr_run, pipelinestep, curr_output)]
                output_paths.append(self.sd_fs_map[corresp_SD])

            # Retrieve the input SDs from the ER.
            for curr_input in pipelinestep.transformation.inputs.all().order_by("dataset_idx"):
                corresp_ERI = curr_ER.execrecordins.get(
                        content_type=ContentType.objects.get_for_model(transformation.models.TransformationInput),
                        object_id=curr_input.pk)
                inputs_after_cable.append(corresp_ERI.symbolicdataset)

        self.logger.debug("Checking required datasets are on the FS for running code")
        for curr_in_SD in inputs_after_cable:
            curr_path = self.sd_fs_map[curr_in_SD]
            if not os.access(curr_path, os.F_OK):
                self.logger.debug("File {} not on FS: recovering".format(curr_path))
                successful_recovery = self.recover(curr_in_SD, curr_RS)

                if not successful_recovery:
                    self.logger.debug("Failed to recover: quitting without creating ER")
                    return curr_record

        self.logger.debug("Finished putting datasets into place: running code for this step")

        if type(pipelinestep.transformation) == pipeline.models.Pipeline:
            self.logger.debug("EXECUTING SUB-PIPELINE STEP")
            self.execute_pipeline(
                    pipeline=pipelinestep.transformation,
                    input_SDs=inputs_after_cable,
                    sandbox_path=step_run_dir,
                    parent_runstep=curr_RS)

            self.logger.debug("FINISHED EXECUTING SUB-PIPELINE STEP")
            return curr_RS

        curr_log = archive.models.ExecLog(record=curr_RS, invoking_record=invoking_record)
        curr_log.save()
        curr_mo = archive.models.MethodOutput(execlog=curr_log)
        curr_mo.save()
        self.logger.debug("Created ExecLog for method execution at {}".format(curr_log))
        stdout_path = os.path.join(log_dir, "step{}_stdout.txt".format(pipelinestep.step_num))
        stderr_path = os.path.join(log_dir, "step{}_stderr.txt".format(pipelinestep.step_num))

        self.logger.debug("Running code")
        input_paths = [self.sd_fs_map[x] for x in inputs_after_cable]

        with open(stdout_path, "w+") as outwrite, open(stderr_path, "w+") as errwrite:
            pipelinestep.transformation.run_code_with_streams(step_run_dir, input_paths,
                    output_paths, [outwrite, sys.stdout], [errwrite, sys.stderr],
                    curr_log, curr_mo)

        self.logger.debug("Method execution complete, ExecLog saved (started = {}, ended = {})".
                format(curr_log.start_time, curr_log.end_time))

        if curr_ER is None:
            self.logger.debug("Creating fresh ER")
            curr_ER = librarian.models.ExecRecord(generator=curr_log)
            curr_ER.save()
            curr_RS.execrecord = curr_ER
            curr_RS.save()

            self.logger.debug("Annotating ERIs for ER '{}'".format(curr_ER))
            for curr_input in pipelinestep.transformation.inputs.all():
                corresp_input_SD = inputs_after_cable[curr_input.dataset_idx-1]
                curr_ER.execrecordins.create(
                        generic_input=curr_input,
                        symbolicdataset=corresp_input_SD)

            self.logger.debug("Creating new SDs + EROs")
            for curr_output in pipelinestep.transformation.outputs.all():
                corresp_output_SD = librarian.models.SymbolicDataset(MD5_checksum="")
                corresp_output_SD.save()

                if not curr_output.is_raw():
                    curr_structure = librarian.models.DatasetStructure(
                            symbolicdataset = corresp_output_SD,
                            compounddatatype=curr_output.get_cdt(),
                            num_rows=-1)
                    curr_structure.save()

                corresp_output_SD.clean()
                curr_ER.execrecordouts.create(generic_output=curr_output, symbolicdataset=corresp_output_SD)
            curr_ER.complete_clean()
        self.logger.debug("Finished creating fresh ER, proceeding to check outputs")

        # had_output_found indicates we have detected problems with the output.
        bad_output_found = False
        for curr_output in pipelinestep.transformation.outputs.all():
            output_path = output_paths[curr_output.dataset_idx-1]
            output_ERO = curr_ER.execrecordouts.get(
                    content_type=ContentType.objects.get_for_model(transformation.models.TransformationOutput),
                    object_id=curr_output.pk)
            output_SD = output_ERO.symbolicdataset
        
            # Check that the file exists, as we did for cables.
            start_time = timezone.now()
            if not os.access(output_path, os.R_OK):
                ccl = output_SD.content_checks.create(start_time=start_time, end_time=timezone.now(), execlog=curr_log)
                baddata = datachecking.models.BadData(contentchecklog=ccl, missing_output=True)
                baddata.save()
                ccl.baddata = baddata
                bad_output_found = True
                continue

            with open(output_path, "rb") as f:
                output_md5 = file_access_utils.compute_md5(f)

            num_rows = -1
            if not curr_output.is_raw():
                with open(output_path, "rb") as f:
                    num_rows = file_access_utils.count_rows(f)

            if not had_ER_at_beginning:
                output_SD.MD5_checksum = output_md5
                output_SD.structure.num_rows = num_rows
                output_SD.structure.save()
                output_SD.save()
                self.logger.debug("First time seeing file: saving md5 {}".format(output_md5))
                if not curr_output.is_raw():
                    self.logger.debug("First time seeing file: saving row count {}".format(num_rows))

            if not pipelinestep.outputs_to_delete.filter(pk=curr_output.pk).exists() and not output_ERO.has_data():

                self.logger.debug("Retaining output: creating Dataset")

                desc = ("run: {}\nuser: {}\nstep: {}\nmethod: {}\noutput: {}"
                        .format(self.run.name, self.user, pipelinestep.step_num, pipelinestep.transformation,
                                curr_output.dataset_name))

                name = "run:{}__step:{}__output:{}".format(self.run.name, pipelinestep.step_num,
                                                           curr_output.dataset_name)

                new_DS = archive.models.Dataset(user=self.user, name=name, description=desc, symbolicdataset=output_SD,
                                                created_by=curr_RS)

                # dataset_idx is 1-based, and output_paths is 0-based.
                with open(output_path, "rb") as f:
                    new_DS.dataset_file.save(os.path.basename(output_path), File(f))
                new_DS.clean()
                new_DS.save()

            if not had_ER_at_beginning:
                self.logger.debug("New data - performing content check")
                summary_path = "{}_summary".format(output_path)

                # CCL is generated
                ccl = output_SD.check_file_contents(output_path, summary_path, curr_output.get_min_row(),
                                                    curr_output.get_max_row(), curr_log)

                if ccl.is_fail():
                    self.logger.warn("content check failed for {}".format(output_path))
                    bad_output_found = True
                else:
                    self.logger.debug("content check passed for {}".format(output_path))

            elif had_ER_at_beginning:
                self.logger.debug("SD has been computed before, checking integrity of {}".format(output_SD))
                icl = output_SD.check_integrity(output_path,curr_log,output_md5)

                if icl.is_fail():
                    bad_output_found = True
                    
        self.logger.debug("Finished checking outputs")
        curr_ER.complete_clean()

        if not recover:
            self.logger.debug("Not recovering: finishing bookkeeping")
            curr_RS.execrecord = curr_ER
            curr_RS.clean()
            curr_RS.save()

            # Since reused=False, step_run_dir represents where the step *actually is*
            self.logger.debug("Updating maps")
            self.ps_map[pipelinestep] = (step_run_dir, curr_RS)
    
            for step_output in pipelinestep.transformation.outputs.all():
                corresp_ero = curr_ER.execrecordouts.get(
                        content_type=ContentType.objects.get_for_model(type(step_output)),
                        object_id=step_output.pk)
    
                corresp_SD = corresp_ero.symbolicdataset
                corresp_path = output_paths[step_output.dataset_idx-1]
    
                if corresp_SD not in self.sd_fs_map:
                    self.sd_fs_map[corresp_SD] = corresp_path

                self.socket_map[(curr_run, pipelinestep, step_output)] = corresp_SD

        curr_RS.complete_clean()
        return curr_RS

    def execute_pipeline(self,pipeline=None,input_SDs=None,sandbox_path=None,parent_runstep=None):
        """
        Execute the specified Pipeline with the given inputs.

        INPUTS
        If a top level pipeline, pipeline, input_SDs, sandbox_path,
        and parent_runstep are all None.

        Outputs written to: [sandbox_path]/output_data/run[run PK]_[output name].(csv|raw)
        """

        is_set = (pipeline != None,input_SDs != None,sandbox_path != None,parent_runstep != None)
        if any(is_set) and not all(is_set):
            raise ValueError("Either none or all parameters must be None")

        pipeline = pipeline or self.pipeline
        sandbox_path = sandbox_path or self.sandbox_path

        curr_run = self.run

        if (curr_run.is_complete()):
            self.logger.warn('A Pipeline has already been run in Sandbox "{}", returning the previous Run'.format(self))
            return curr_run

        if parent_runstep is not None:
            self.logger.debug("executing a sub-pipeline with input_SD {}".format(input_SDs))
            curr_run = pipeline.pipeline_instances.create(
                    user=self.user,
                    parent_runstep=parent_runstep)


        self.logger.debug("Setting up output directory")
        out_dir = os.path.join(sandbox_path, "output_data")
        file_access_utils.set_up_directory(out_dir)

        for step in pipeline.steps.all().order_by("step_num"):
            self.logger.debug("Executing step {} - looking for cables feeding into this step".format(step))

            step_inputs = []
            run_dir = os.path.join(sandbox_path,"step{}".format(step.step_num))

            # Before executing a step, we need to know what input SDs to feed into the step for execution

            # Because pipeline steps includes the cable execution prior to the transformation,
            # the SDs we need are upstream of the *PSIC* leading to this step

            # For each PSIC leading to this step
            for psic in step.cables_in.all().order_by("dest__dataset_idx"):

                # The socket is upstream of that PSIC
                socket = psic.source

                run_to_query = curr_run

                # If the PSIC comes from another step, the generator is the source pipeline step,
                # or the output cable if it's a sub-pipeline
                if psic.source_step != 0:
                    generator = pipeline.steps.get(step_num=psic.source_step)
                    if socket.transformation.__class__.__name__ == "Pipeline":
                        run_to_query = curr_run.runsteps.get(pipelinestep=generator).child_run
                        generator = generator.transformation.outcables.get(output_idx=socket.dataset_idx)

                # Otherwise, the psic comes from step 0
                else:

                    # If this step is not a subpipeline, the dataset was uploaded
                    generator = None

                    # If this step is a subpipeline...
                    if parent_runstep is not None:

                        # Then the run we are interested in is the parent run
                        run_to_query = parent_runstep.run

                        # Get cables in the outer pipeline step leading to this subrun
                        cables_into_subpipeline = parent_runstep.pipelinestep.cables_in

                        # Find the particular cable leading to this PSIC's source
                        generator = cables_into_subpipeline.get(dest=psic.source)

                step_inputs.append(self.socket_map[(run_to_query, generator, socket)])

            curr_RS = self.execute_step(curr_run, step, step_inputs, step_run_dir=run_dir)
            self.logger.debug("DONE EXECUTING STEP {}".format(step))

            if not curr_RS.is_complete() or not curr_RS.successful_execution():
                self.logger.debug("Step failed to execute: returning the run")
                curr_run.clean()
                return curr_run

        self.logger.debug("Finished executing steps, executing POCs")
        for outcable in pipeline.outcables.all():

            generator = None
            run_to_query = curr_run

            # Consider the source step of this POC
            source_step = pipeline.steps.get(step_num=outcable.source_step)

            # By default, the socket is the TO from a pipeline step
            socket = outcable.source

            # The generator of interest is usually just the source pipeline step
            if type(source_step.transformation).__name__ != "Pipeline":
                generator = source_step

            # But if this step contains a subpipeline
            else:

                # The generator is the subpipeline's output cable
                generator = source_step.transformation.outcables.get(output_idx = outcable.source.dataset_idx)

                # And we need the subrun (Get the RS in this run linked to the PS linked by this POCs source)
                runstep_containing_subrun = curr_run.runsteps.get(pipelinestep__step_num=outcable.source_step)

                # Get the run with the above runstep as it's parent
                run_to_query = archive.models.Run.objects.all().get(parent_runstep=runstep_containing_subrun)

            source_SD = self.socket_map[(run_to_query, generator, socket)]
            file_suffix = "raw" if outcable.is_raw() else "csv"
            out_file_name = "run{}_{}.{}".format(curr_run.pk, outcable.output_name,file_suffix)
            output_path = os.path.join(out_dir,out_file_name)
            curr_ROC = self.execute_cable(outcable, source_SD,output_path, curr_run)

            if not curr_ROC.is_complete() or not curr_RS.successful_execution():
                curr_run.clean()
                self.logger.debug("Execution failed")
                return curr_run

        self.logger.debug("Finished executing output cables")
        curr_run.save()
        curr_run.complete_clean()
        self.logger.debug("DONE EXECUTING PIPELINE - Run is complete, clean, and saved")

        return curr_run

    def first_generator_of_SD(self, SD_to_find, curr_run=None):
        """
        Find the (run, generator) pair which first produced a SymbolicDataset.
        If generator is None, it indicates the socket is a Pipeline input. If
        both generator and run are None, it means the SD wasn't found in the
        Pipeline.
        """
        if curr_run is None:
            curr_run = self.run

        pipeline = curr_run.pipeline

        # First check if the SD we're looking for is a Pipeline input.
        for socket in pipeline.inputs.order_by("dataset_idx"):
            key = (curr_run, None, socket)
            if key in self.socket_map and self.socket_map[key] == SD_to_find:
                return (curr_run, None)

        # If it's not a pipeline input, check all the steps.
        steps = curr_run.runsteps.all()
        steps = sorted(steps, key = lambda step: step.pipelinestep.step_num)

        for step in steps:
            # First check if the SD is an input to this step. In that case, it
            # had to come in from a nontrivial cable (since we're checking the
            # steps in order, and we already checked the inputs).
            pipelinestep = step.pipelinestep
            for socket in pipelinestep.transformation.inputs.order_by("dataset_idx"):
                generator = pipelinestep.cables_in.get(dest=socket)
                key = (curr_run, generator, socket)
                if key in self.socket_map and self.socket_map[key] == SD_to_find:
                    return (curr_run, generator)

            # If it wasn't an input to this step, but this step is a sub-pipeline,
            # it might be somewhere within the sub-pipeline. Search recursively.
            if hasattr(step, "child_run") and step.child_run is not None:
                run, generator = self.first_generator_of_SD(SD_to_find, step.child_run)
                if run is not None: 

                    # Did we find it somewhere inside the sub-Pipeline?
                    if generator is not None:
                        return (run, generator)

                    # If it was an input to the sub-Pipeline, we need the cable leading in.
                    else:
                        generator = pipelinestep.cables_in.get(dest=socket)
                        return (curr_run, generator)

            # Now check if it's an output from this step.
            generator = pipelinestep
            for socket in pipelinestep.transformation.outputs.order_by("dataset_idx"):
                key = (curr_run, generator, socket)
                if key in self.socket_map and self.socket_map[key] == SD_to_find:
                    return (curr_run, generator)

        # Finally, check if it's at the end of a nontrivial Pipeline output cable.
        for outcable in pipeline.outcables.order_by("output_idx"):
            socket = cable.dest
            key = (curr_run, outcable, socket)
            if key in self.socket_map and socket_map[key] == SD_to_find:
                return (curr_run, outcable)

        # If we're here, we didn't find it.
        return (None, None)

    def recover(self, SD_to_recover, invoking_record):
        """
        Writes SD_to_recover to the file system.

        INPUTS
        SD_to_recover   The symbolic dataset we want to recover.
        invoking_record RunAtomic initiating the recovery

        OUTPUTS
        Returns True if successful - otherwise False.

        PRE
        SD_to_recover is in the maps but no corresponding file is on the file system.
        """
        curr_run = invoking_record.parent_run

        if SD_to_recover.has_data():
            self.logger.debug("Dataset is in the DB - writing it to the file system")
            location = self.sd_fs_map[SD_to_recover]
            saved_data = SD_to_recover.dataset
            try:
                saved_data.dataset_file.open()
                shutil.copyfile(saved_data.dataset_file.name, location)
            except IOError:
                self.logger.error("could not copy file {} to file {}.".format(saved_data.dataset_file, location))
                return False
            finally:
                saved_data.dataset_file.close()
            return True

        self.logger.debug("Performing computation to create missing Dataset")

        # Search for the generator of the SD in the Pipeline.
        curr_run, generator = self.first_generator_of_SD(SD_to_recover)

        if curr_run is None:
            raise ValueError('SymbolicDataset "{}" was not found in Pipeline "{}" and cannot be recovered'
                             .format(SD_to_recover, self.pipeline))
        elif generator is None:
            raise ValueError('SymbolicDataset "{}" is an input to Pipeline "{}" and cannot be recovered'
                             .format(SD_to_recover, self.pipeline))

        curr_record = None
        self.logger.debug('Executing {} "{}" in recovery mode'.format(generator.__class__.__name__, generator))
        if type(generator) == pipeline.models.PipelineStep:
            curr_record = self.execute_step(curr_run,generator,None,recover=True,invoking_record=invoking_record)
        elif type(generator) == pipeline.models.PipelineOutputCable:
            curr_record = self.recover_cable(generator, invoking_record)
        elif type(generator) == pipeline.models.PipelineStepInputCable:
            parent_record = curr_run.runsteps.get(pipelinestep=generator.pipelinestep)
            curr_record = self.execute_cable(generator, invoking_record)

        return curr_record.is_complete() and curr_record.successful_execution()
