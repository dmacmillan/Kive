"""
archive.models

Shipyard data models relating to archiving information: Run, RunStep,
Dataset, etc.
"""

from django.db import models
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes import generic
from django.core.exceptions import ValidationError

import hashlib

import file_access_utils

import method.models
import pipeline.models
import archive.models
import librarian.models
import transformation.models

class Run(models.Model):
    """
    Stores data associated with an execution of a pipeline.

    Related to :model:`pipeline.models.Pipeline`
    Related to :model:`archive.models.RunStep`
    Related to :model:`archive.models.Dataset`
    """
    user = models.ForeignKey(User, help_text="User who performed this run")
    start_time = models.DateTimeField("start time", auto_now_add=True,
                                      help_text="Time at start of run")
    pipeline = models.ForeignKey(
        pipeline.models.Pipeline,
        related_name="pipeline_instances",
        help_text="Pipeline used in this run")

    name = models.CharField("Run name", max_length=256)
    description = models.TextField("Run description", blank=True)
    
    # If run was spawned within another run, parent_runstep denotes
    # the run step that initiated it
    parent_runstep = models.OneToOneField(
        "RunStep",
        related_name="child_run",
        null=True,
        blank=True,
        help_text="Step of parent run initiating this one as a sub-run")

    execrecord = models.ForeignKey(
        "librarian.ExecRecord",
        null=True,
        blank=True,
        related_name="runs",
        help_text="Record of this run");

    reused = models.NullBooleanField(
        help_text="Indicates whether this run uses the record of a previous execution",
        default=None);

    def clean(self):
        """
        Checks coherence of the run (possibly in an incomplete state).

        The procedure:
         - if parent_runstep is not None, then pipeline should be
           consistent with it
         - if reused is None, then execrecord should not be set and
           there should be no RunSteps or RunOutputCables associated,
           and exit
        (from here on reused is assumed to be set)
         - check RSs; no RS should be associated without the previous
           ones being complete
         - if not all RSs are complete, no ROCs should be associated,
           ER should not be set
          (from here on all RSs are assumed to be complete)
           - clean all associated ROCs
           - if not all ROCs are complete, ER should not be set
          (from here on all ROCs are assumed to be complete)
         - if reused is True:
           - any associated RSs, RSICs, or ROCs must also be reused
        (from here on execrecord is assumed to be set)
         - check that execrecord is clean and complete
         - check that execrecord is consistent with pipeline
         - check that all EROs have a corresponding ROC
        """
        if (self.parent_runstep != None and
                self.pipeline != self.parent_runstep.pipelinestep.transformation):
            raise ValidationError(
                "Pipeline of Run \"{}\" is not consistent with its parent RunStep".
                format(self))
        
        if self.reused == None:
            if self.runsteps.all().exists():
                raise ValidationError(
                    "Run \"{}\" has not decided whether or not to reuse an ER yet, so there should be no associated RunSteps".
                    format(self))

            if self.runoutputcables.all().exists():
                raise ValidationError(
                    "Run \"{}\" has not decided whether or not to reuse an ER yet, so there should be no associated RunOutputCables".
                    format(self))

            if self.execrecord != None:
                raise ValidationError(
                    "Run \"{}\" has not decided whether or not to reuse an ER yet, so execrecord should not be set")
            
            return

        # From here on reused is assumed to be set.
        
        # Go through whatever steps are registered.
        most_recent_step = None
        steps_associated = None
        if self.runsteps.all().exists():
    
            # Check that steps are proceeding in order.  (Multiple quenching
            # of steps is taken care of already.)
            steps_associated = sorted(
                [rs.pipelinestep.step_num for rs in self.runsteps.all()])
    
            if steps_associated != range(1, len(steps_associated)+1):
                raise ValidationError(
                    "RunSteps of Run \"{}\" are not consecutively numbered starting from 1".
                    format(self))
    
            # All steps prior to the last registered one must be complete.
            for curr_step_num in steps_associated[:-1]:
                self.runsteps.get(pipelinestep__step_num=curr_step_num).complete_clean()
    
            # The most recent step should be clean.
            most_recent_step = self.runsteps.get(
                pipelinestep__step_num=steps_associated[-1])
            most_recent_step.clean()

        # If all steps are not complete, then no ROCs should be
        # associated.
        if (not self.runsteps.all().exists() or
                steps_associated[-1] < self.pipeline.steps.count() or 
                not most_recent_step.is_complete()):
            if self.runoutputcables.all().exists():
                raise ValidationError(
                    "Run \"{}\" has not completed all of its RunSteps, so there should be no associated RunOutputCables".
                    format(self))
            
            if self.execrecord != None:
                raise ValidationError(
                    "Run \"{}\" has not completed all of its RunSteps, so execrecord should not be set".
                    format(self))
            
            return
        
        # From this point on, all RunSteps are assumed to be complete.
        
        # Run clean on all of its outcables.
        for run_outcable in self.runoutputcables.all():
            run_outcable.clean()

        # If not all ROCs are complete, there should be no ER set.
        all_outcables_complete = True
        for outcable in self.pipeline.outcables.all():
            corresp_roc = self.runoutputcables.filter(pipelineoutputcable=outcable)
            if not corresp_roc.exists() or not corresp_roc[0].is_complete():
                all_outcables_complete = False
                break

        if not all_outcables_complete and self.execrecord != None:
            raise ValidationError(
                "Run \"{}\" has not completed all of its RunOutputCables, so execrecord should not be set".
                format(self))

        # From this point on, all RunSteps and ROCs are assumed to be
        # complete.
    
        if self.reused:
            # Check that all sub-RunSteps, RSICs, ROCs are reused.
            for step in self.runsteps.all():
                if not step.reused:
                    raise ValidationError(
                        "Run \"{}\" reused an ExecRecord so all of its RunSteps should also have reused ExecRecords".
                        format(self))

                for RSIC in step.RSICs.all():
                    if not RSIC.reused:
                        raise ValidationError(
                            "Run \"{}\" reused an ExecRecord so all of its RunSICs should also have reused ExecRecords".
                            format(self))

            for ROC in self.runoutputcables.all():
                if not ROC.reused:
                    raise ValidationError(
                        "Run \"{}\" reused an ExecRecord so all of its RunOutputCables should also have reused ExecRecords".
                        format(self))

        if self.execrecord == None:
            return

        # From this point on, execrecord is assumed to be set.
        self.execrecord.complete_clean()

        # The ER must point to the same pipeline that this run points to
        if self.pipeline != self.execrecord.general_transf:
            raise ValidationError(
                "Run \"{}\" points to pipeline \"{}\" but corresponding ER does not".
                format(self, self.pipeline))

        # Check that every ERO has a corresponding RunOutputCable (we
        # know it to be clean by checking above).
        for ero in self.execrecord.execrecordouts.all():
            curr_output = ero.generic_output
            corresp_roc = self.runoutputcables.filter(
                pipelineoutputcable__output_name=curr_output.dataset_name)

            if (corresp_roc[0].execrecord.execrecordouts.all()[0].
                symbolicdataset != ero.symbolicdataset):
                raise ValidationError(
                    "ExecRecordOut \"{}\" of Run \"{}\" does not match the corresponding RunOutputCable".
                    format(ero, self))
    
    def is_complete(self):
        """True if this run is complete; false otherwise."""
        return self.execrecord != None
            
    def complete_clean(self):
        """Checks completeness and coherence of a run."""
        self.clean()
        if not self.is_complete():
            raise ValidationError(
                "Run \"{}\" has no ExecRecord".format(self))


class RunStep(models.Model):
    """
    Annotates the execution of a pipeline step within a run.

    Related to :model:`archive.models.Run`
    Related to :model:`librarian.models.ExecRecord`
    Related to :model:`pipeline.models.PipelineStep`
    """
    run = models.ForeignKey(Run, related_name="runsteps")

    # If this RunStep has a child_run, then this execrecord may be null
    # (and you would look at child_run's execrecord).
    execrecord = models.ForeignKey(
        librarian.models.ExecRecord,
        null=True, blank=True,
        related_name="runsteps")
    reused = models.NullBooleanField(
        default=None,
        help_text="Denotes whether this run step reuses a previous execution")
    pipelinestep = models.ForeignKey(
        pipeline.models.PipelineStep,
        related_name="pipelinestep_instances")

    outputs = generic.GenericRelation("Dataset")

    class Meta:
        # Uniqueness constraint ensures you can't have multiple RunSteps for
        # a given PipelineStep within a Run.
        unique_together = ("run", "pipelinestep")

    def clean(self):
        """
        Check coherence of this RunStep.

        The checks we perform, in sequence:
         - pipelinestep is consistent with run
         - if pipelinestep is for a method, there should be no
           child_run
         - if any RSICs exist, check they are clean and complete.
         - if all RSICs are not quenched, reused, child_run, and
           execrecord should not be set, and no Datasets should be
           associated
        (from here on all RSICs are assumed to be quenched)
         - if we haven't decided whether or not to reuse an ER,
           child_run and execrecord should not be set, and no Datasets
           should be associated.
        (from here on, reused is assumed to be set)
         - if we are reusing an ER, check that:
           - there are no associated Datasets.
           - if there is a child_run, then it too is reused.
         - else if we are not reusing an ER:
           - clean any associated Datasets
           - clean child_run if it exists
           - if child_run exists, execrecord should not be set
        (from here on, child_run is assumed to be appropriately set
         or blank)
        (from here on, execrecord or child_run.execrecord is assumed
         to be set)
         - check that it is complete and clean
         - check that it's coherent with pipelinestep
         - if an output is marked for deletion, there should be no
           associated Dataset
         - else:
           - the corresponding ERO should have an associated Dataset.
         - if this RunStep was not reused, there should be at least
           one associated Dataset.
         - any associated Dataset belongs to an ERO (this checks for
           Datasets that have been wrongly assigned to this RunStep).
        
        Note: don't need to check inputs for multiple quenching due to uniqueness.
        Quenching of outputs is checked by ExecRecord.
        """
        # Does pipelinestep belong to run.pipeline?
        if not self.run.pipeline.steps.filter(pk=self.pipelinestep.pk).exists():
            raise ValidationError(
                "PipelineStep \"{}\" of RunStep \"{}\" does not belong to Pipeline \"{}\"".
                format(self.pipelinestep, self, self.run.pipeline))

        # If the PS stores a method, it should have no child_run.
        # (Should not act as a parent runstep)
        if (type(self.pipelinestep.transformation) == method.models.Method and
                hasattr(self,"child_run") == True):
            raise ValidationError(
                "PipelineStep of RunStep \"{}\" is not a Pipeline but a child run exists".
                format(self))
        
        for rsic in self.RSICs.all():
            rsic.complete_clean()

        if (self.pipelinestep.cables_in.count() != self.RSICs.count()):
            if (self.reused != None or self.execrecord != None):
                raise ValidationError(
                    "RunStep \"{}\" inputs not quenched; reused and execrecord should not be set".
                    format(self))
            if (type(self.pipelinestep.transformation) == pipeline.models.Pipeline and
                    hasattr(self, "child_run")):
                raise ValidationError(
                    "RunStep \"{}\" inputs not quenched; child_run should not be set".
                    format(self))
            if self.outputs.all().exists():
                raise ValidationError(
                    "RunStep \"{}\" inputs not quenched; no data should have been generated".
                    format(self))
            return

        # From here on, RSICs are assumed to be quenched.
        if self.reused == None:
            if self.outputs.all().exists():
                raise ValidationError(
                    "RunStep \"{}\" has not decided whether or not to reuse an ExecRecord; no data should have been generated".
                    format(self))
            if self.execrecord != None:
                raise ValidationError(
                    "RunStep \"{}\" has not decided whether or not to reuse an ExecRecord; execrecord should not be set".
                    format(self))
            if (type(self.pipelinestep.transformation) == pipeline.models.Pipeline and
                    hasattr(self, "child_run")):
                raise ValidationError(
                    "RunStep \"{}\" has not decided whether or not to reuse an ExecRecord; child_run should not be set".
                    format(self))
            return

        else:
            # From here on, reused is assumed to be set.  If there is
            # a child_run associated, clean it.
            if hasattr(self, "child_run"):
                self.child_run.clean()
    
            if self.reused:
                if self.outputs.all().exists():
                    raise ValidationError(
                        "RunStep \"{}\" reused an ExecRecord and should not have generated any data".
                        format(self))

                # Any child_run should itself be reused.
                if hasattr(self, "child_run") and not self.child_run.reused:
                    raise ValidationError(
                        "RunStep \"{}\" reused an ExecRecord but its child Run did not".
                        format(self))
    
            else:
                if hasattr(self, "child_run"):
                    if self.outputs.all().exists():
                        raise ValidationError(
                            "RunStep \"{}\" has a child run so should not have generated any data".
                            format(self))
                    
                    if self.execrecord != None:
                        raise ValidationError(
                            "RunStep \"{}\" has a child run so execrecord should not be set".
                            format(self))
                else:
                    for out_data in self.outputs.all():
                        out_data.clean()

        # From here on, child_run is assumed to be appropriately set or blank.
        step_er = self.execrecord
        if hasattr(self, "child_run"):
            step_er = self.child_run.execrecord

        if step_er == None:
            return

        # From here on, the appropriate ER is assumed to be set.
        step_er.complete_clean()

        # ER must point to the same transformation that this runstep points to
        if self.pipelinestep.transformation != step_er.general_transf:
            raise ValidationError(
                "RunStep \"{}\" points to transformation \"{}\" but corresponding ER does not".
                format(self, self.pipelinestep))

        # Go through all of the outputs.
        to_type = ContentType.objects.get_for_model(
            transformation.models.TransformationOutput)

        # Track whether there are any outputs not deleted.
        any_outputs_kept = False
        
        for to in self.pipelinestep.transformation.outputs.all():
            if self.pipelinestep.outputs_to_delete.filter(
                    dataset_name=to.dataset_name).exists():
                # This output is deleted; there should be no associated Dataset.
                # Get the associated ERO.
                corresp_ero = step_er.execrecordouts.get(
                    content_type=to_type, object_id=to.id)
                if self.outputs.filter(symbolicdataset=corresp_ero.symbolicdataset).exists():
                    raise ValidationError(
                        "Output \"{}\" of RunStep \"{}\" is deleted; no data should be associated".
                        format(to, self))
            else:
                # The output is not deleted.
                any_outputs_kept = True

                # The corresponding ERO should have existent data.
                corresp_ero = step_er.execrecordouts.get(
                    content_type=to_type, object_id=to.id)
                if not corresp_ero.symbolicdataset.has_data():
                    raise ValidationError(
                        "ExecRecordOut \"{}\" of RunStep \"{}\" should reference existent data".
                        format(corresp_ero, self))

        # If there are any outputs not deleted, this RunStep did not
        # reuse an ER, and did not have a child run, then there should
        # be at least one corresponding real Dataset.
        associated_datasets = self.outputs.all()
        if (any_outputs_kept and not self.reused and
                not hasattr(self, "child_run") and
                not associated_datasets.exists()):
            raise ValidationError(
                "RunStep \"{}\" did not reuse an ExecRecord, had no child run, and did not delete all of its outputs; a corresponding Dataset should be associated".
                format(self, to))

        # Check that any associated data belongs to an ERO of this ER
        for out_data in associated_datasets:
            if not step_er.execrecordouts.filter(
                    symbolicdataset=out_data.symbolicdataset).exists():
                raise ValidationError(
                    "RunStep \"{}\" generated Dataset \"{}\" but it is not in its ExecRecord".
                    format(self, out_data))

    def is_complete(self):
        """True if RunStep is complete; false otherwise."""
        step_er = self.execrecord
        if hasattr(self, "child_run"):
            step_er = self.child_run.execrecord
        return step_er != None
    
    def complete_clean(self):
        """Checks coherence and completeness of this RunStep."""
        self.clean()
        if not self.is_complete():
            raise ValidationError(
                "RunStep \"{}\" has no ExecRecord".format(self))

class RunSIC(models.Model):
    """
    Annotates the action of a PipelineStepInputCable within a RunStep.

    Related to :model:`archive.models.RunStep`
    Related to :model:`librarian.models.ExecRecord`
    Related to :model:`pipeline.models.PipelineStepInputCable`
    """
    runstep = models.ForeignKey(RunStep, related_name="RSICs")
    execrecord = models.ForeignKey(
        librarian.models.ExecRecord,
        null=True,
        blank=True,
        related_name="RSICs")
    reused = models.NullBooleanField(
        help_text="Denotes whether this run reused the action of an output cable",
        default=None)
    PSIC = models.ForeignKey(
        pipeline.models.PipelineStepInputCable,
        related_name="psic_instances")
    
    output = generic.GenericRelation("Dataset")

    class Meta:
        # Uniqueness constraint ensures that no POC is multiply-represented
        # within a run step.
        unique_together = ("runstep", "PSIC")

    def clean(self):
        """
        Check coherence of this RunSIC.

        In sequence, the checks we perform:
         - PSIC belongs to runstep.pipelinestep
         - if reused is None (no decision on reusing has been made), no data
           should be associated, and execrecord should not be set
         - else if reused is True, no data should be associated.
         - else if reused is False:
           - if the cable is trivial, there should be no associated Dataset
           - otherwise, make sure there is at most one Dataset, and clean it
             if it exists
        (from here on execrecord is assumed to be set)
         - it must be complete and clean
         - PSIC is the same as (or compatible to) self.execrecord.general_transf
         - if this RunSIC does not keep its output, there should be no existent
           data associated.
         - else if this RunSIC keeps its output:
           - the corresponding ERO should have existent data associated
           - if the PSIC is not trivial and this RunSIC does not reuse an ER,
             then there should be existent data associated and it should also
             be associated to the corresponding ERO.
        """
        if (not self.runstep.pipelinestep.cables_in.
                filter(pk=self.PSIC.pk).exists()):
            raise ValidationError(
                "PSIC \"{}\" does not belong to PipelineStep \"{}\"".
                format(self.PSIC, self.runstep.pipelinestep))

        if self.reused == None:
            if self.has_data():
                raise ValidationError(
                    "RunSIC \"{}\" has not decided whether or not to reuse an ExecRecord; no Datasets should be associated".
                    format(self))
            if self.execrecord != None:
                raise ValidationError(
                    "RunSIC \"{}\" has not decided whether or not to reuse an ExecRecord; execrecord should not be set yet".
                    format(self))

        elif self.reused:
            if self.has_data():
                raise ValidationError(
                    "RunSIC \"{}\" reused an ExecRecord and should not have generated any Datasets".
                    format(self))

        else:
            # If this cable is trivial, there should be no data
            # associated.
            if self.PSIC.is_trivial() and self.has_data():
                raise ValidationError(
                    "RunSIC \"{}\" is trivial and should not have generated any Datasets".
                    format(self))

            # Otherwise, check that there is at most one Dataset
            # attached, and clean it.
            elif self.has_data():
                if self.output.count() > 1:
                    raise ValidationError(
                        "RunSIC \"{}\" should generate at most one Dataset".
                        format(self))
                self.output.all()[0].clean()
        
        # If there is no execrecord defined, then exit.
        if self.execrecord == None:
            return
        
        # At this point there must be an associated ER; check that it is
        # clean and complete.
        self.execrecord.complete_clean()

        # Check that PSIC and execrecord.general_transf are compatible
        # given that the SymbolicDataset represented in the ERI is the
        # input to both.  (This must be true because our Pipeline was
        # well-defined.)
        if (type(self.execrecord.general_transf) !=
                pipeline.models.PipelineStepInputCable):
            raise ValidationError(
                "ExecRecord of RunSIC \"{}\" does not represent a PSIC".
                format(self.PSIC))
        
        elif not self.PSIC.is_compatible_given_input(
                self.execrecord.general_transf):
            raise ValidationError(
                "PSIC of RunSIC \"{}\" is incompatible with that of its ExecRecord".
                format(self.PSIC))

        # If the output of this PSIC is not marked to keep, there should be
        # no data associated.
        if not self.PSIC.keep_output:
            if self.has_data():
                raise ValidationError(
                    "RunSIC \"{}\" does not keep its output; no data should be produced".
                    format(self))
        else:
            # The corresponding ERO should have existent data.
            corresp_ero = self.execrecord.execrecordouts.all()[0]
            if not corresp_ero.has_data():
                raise ValidationError(
                    "RunSIC \"{}\" keeps its output; ExecRecordOut \"{}\" should reference existent data".
                    format(self, corresp_ero))

            # If reused == False and the cable is not trivial,
            # there should be associated data, and it should match that
            # of corresp_ero.
            if not self.reused and not self.PSIC.is_trivial():
                if not self.has_data():
                    raise ValidationError(
                        "RunSIC \"{}\" was not reused, trivial, or deleted; it should have produced data".
                        format(self))

                if corresp_ero.symbolicdataset.dataset != self.output.all()[0]:
                    raise ValidationError(
                        "Dataset \"{}\" was produced by RunSIC \"{}\" but is not in an ERO of ExecRecord \"{}\"".
                        format(self.output.all()[0], self, self.execrecord))

    def is_complete(self):
        """True if RunSIC is complete; false otherwise."""
        return self.execrecord != None

    def complete_clean(self):
        """Check completeness and coherence of this RunSIC."""
        self.clean()
        if not self.is_complete():
            raise ValidationError(
                "RunSIC \"{}\" has no ExecRecord".format(self))

    def has_data(self):
        """True if associated output exists; False if not."""
        return self.output.all().exists()

class RunOutputCable(models.Model):
    """
    Annotates the action of a PipelineOutputCable within a run.

    Related to :model:`archive.models.Run`
    Related to :model:`librarian.models.ExecRecord`
    Related to :model:`pipeline.models.PipelineOutputCable`
    """
    run = models.ForeignKey(Run, related_name="runoutputcables")
    execrecord = models.ForeignKey(
        librarian.models.ExecRecord,
        null=True, blank=True,
        related_name="runoutputcables")
    reused = models.NullBooleanField(
        help_text="Denotes whether this run reused the action of an output cable",
        default=None)
    pipelineoutputcable = models.ForeignKey(
        pipeline.models.PipelineOutputCable,
        related_name="poc_instances")
    
    output = generic.GenericRelation("Dataset")

    class Meta:
        # Uniqueness constraint ensures that no POC is multiply-represented
        # within a run.
        unique_together = ("run", "pipelineoutputcable")

    def clean(self):
        """
        Check coherence of this RunOutputCable.

        In sequence, the checks we perform are:
         - pipelineoutputcable belongs to run.pipeline
         - if it has been decided not to reuse an ER:
           - if this cable is trivial, there should be no associated dataset
           - otherwise, clean any associated dataset
         - else if it has been decided to reuse an ER, check that there
           are no associated datasets
         - else if no decision has been made, check that no data has
           been associated, and that ER is unset
        (after this point it is assumed that ER is set)
         - check that it is complete and clean
         - check that it's coherent with pipelineoutputcable
         - if this ROC was not reused, any associated dataset should belong
           to the corresponding ERO
         - if this ROC's output was not marked for deletion, the corresponding
           ERO should have existent data associated
         - if the POC's output was not marked for deletion, the POC is not trivial,
           and this ROC did not reuse an ER, then this ROC should have existent
           data associated
        """
        if (not self.run.pipeline.outcables.
                filter(pk=self.pipelineoutputcable.pk).exists()):
            raise ValidationError(
                "POC \"{}\" does not belong to Pipeline \"{}\"".
                format(self.pipelineoutputcable, self.run.pipeline))

        if self.reused == None:
            if self.has_data():
                raise ValidationError(
                    "RunOutputCable \"{}\" has not decided whether or not to reuse an ExecRecord; no Datasets should be associated".
                    format(self))

            if self.execrecord != None:
                raise ValidationError(
                    "RunOutputCable \"{}\" has not decided whether or not to reuse an ExecRecord; execrecord should not be set yet".
                    format(self))

        elif self.reused:
            if self.has_data():
                raise ValidationError(
                    "RunOutputCable \"{}\" reused an ExecRecord and should not have generated any Datasets".
                    format(self))
        else:
            # If this cable is trivial, there should be no data associated.
            if self.pipelineoutputcable.is_trivial():
                if self.has_data():
                    raise ValidationError(
                        "RunOutputCable \"{}\" is trivial and should not have generated any Datasets".
                        format(self))

            # Otherwise, check that there is at most one Dataset attached, and
            # clean it.
            elif self.has_data():
                if self.output.count() > 1:
                    raise ValidationError(
                        "RunOutputCable \"{}\" should generate at most one Dataset".
                        format(self))
                self.output.all()[0].clean()

        if self.execrecord == None:
            return

        # self.execrecord is set, so complete_clean it.
        self.execrecord.complete_clean()

        # The ER must point to a cable that is compatible with the one
        # this RunOutputCable points to.
        if type(self.execrecord.general_transf) != pipeline.models.PipelineOutputCable:
            raise ValidationError(
                "ExecRecord of RunOutputCable \"{}\" does not represent a POC".
                format(self))
        
        elif not self.pipelineoutputcable.is_compatible(self.execrecord.general_transf):
            raise ValidationError(
                "POC of RunOutputCable \"{}\" is incompatible with that of its ExecRecord".
                format(self))

        is_deleted = False
        if self.run.parent_runstep != None:
            is_deleted = self.run.parent_runstep.pipelinestep.outputs_to_delete.filter(
                dataset_name=self.pipelineoutputcable.output_name).exists()

        # If the output of this ROC is marked for deletion, there should be no data
        # associated.
        if is_deleted:
            if self.has_data():
                raise ValidationError(
                    "RunOutputCable \"{}\" is marked for deletion; no data should be produced".
                    format(self))
        # If it isn't marked for deletion:
        else:
            # The corresponding ERO should have existent data.
            corresp_ero = self.execrecord.execrecordouts.get(execrecord=self.execrecord)
            if not corresp_ero.has_data():
                raise ValidationError(
                    "RunOutputCable \"{}\" was not deleted; ExecRecordOut \"{}\" should reference existent data".
                    format(self, corresp_ero))

            # If the step was not reused and the cable was not
            # trivial, there should be data associated to this ROC.
            if not self.reused and not self.pipelineoutputcable.is_trivial():
                if not self.has_data():
                    raise ValidationError(
                        "RunOutputCable \"{}\" was not reused, trivial, or deleted; it should have produced data".
                        format(self))

                # The associated data should belong to the ERO of
                # self.execrecord (which has already been checked for
                # completeness and cleanliness).
                if not self.execrecord.execrecordouts.filter(
                        symbolicdataset=self.output.all()[0].symbolicdataset).exists():
                    raise ValidationError(
                        "Dataset \"{}\" was produced by RunOutputCable \"{}\" but is not in an ERO of ExecRecord \"{}\"".
                        format(self.output.all()[0], self, self.execrecord))

            

    def is_complete(self):
        """True if ROC is finished running; false otherwise."""
        return self.execrecord != None

    def complete_clean(self):
        """Check completeness and coherence of this RunOutputCable."""
        self.clean()
        if not self.is_complete():
            raise ValidationError(
                "RunOutputCable \"{}\" has no ExecRecord".format(self))

    def has_data(self):
        """True if associated output exists; False if not."""
        return self.output.all().exists()



class Dataset(models.Model):
    """
    Data files uploaded by users or created by transformations.

    Related to :model:`archive.models.RunStep`
    Related to :model:`archive.models.RunOutputCable`
    Related to :model:`librarian.models.SymbolicDataset`

    The clean() function should be used when a pipeline is executed to
    confirm that the dataset structure is consistent with what's
    expected from the pipeline definition.
    
    Pipeline.clean() checks that the pipeline is well-defined in theory,
    while Dataset.clean() ensures the Pipeline produces what is expected.
    """
    user = models.ForeignKey(
        User,
        help_text="User that uploaded this Dataset.")
    
    name = models.CharField(
        max_length=128,
        help_text="Description of this Dataset.")
    
    description = models.TextField()
    
    date_created = models.DateTimeField(
        "Date created",
        auto_now_add=True,
        help_text="Date of Dataset creation.")

    # Four cases from which Datasets can originate:
    #
    # Case 1: uploaded
    # Case 2: from the transformation of a RunStep
    # Case 3: from the execution of a POC (i.e. from a ROC)
    # Case 4: from the execution of a PSIC (i.e. from a RunSIC)
    content_type = models.ForeignKey(
        ContentType,
        limit_choices_to = {
            "model__in": ("RunStep", "RunOutputCable",
                          "RunSIC")
        },
        null=True,
        blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    created_by = generic.GenericForeignKey("content_type", "object_id")
    
    # Datasets are stored in the "Datasets" folder
    dataset_file = models.FileField(
        upload_to="Datasets",
        help_text="Physical path where datasets are stored",
        null=False)

    # Datasets always have a referring SymbolicDataset
    symbolicdataset = models.OneToOneField(
        librarian.models.SymbolicDataset,
        related_name="dataset")

    def __unicode__(self):
        """
        Unicode representation of this Dataset.
        
        This looks like "[name] (created by [user] on [date])"
        """
        return "{} (created by {} on {})".format(
            self.name, self.user, self.date_created)


    def clean(self):
        """
        Check file integrity of this Dataset.
        """
        if not self.check_md5():
            raise ValidationError(
                "File integrity of \"{}\" lost.  Current checksum \"{}\" does not equal expected checksum \"{}\"".
                format(self, self.compute_md5(),
                       self.symbolicdataset.MD5_checksum))
            
    def compute_md5(self):
        """Computes the MD5 checksum of the Dataset."""
        md5gen = hashlib.md5()
        md5 = None
        try:
            self.dataset_file.open()
            md5 = file_access_utils.compute_md5(self.dataset_file.file)
        finally:
            self.dataset_file.close()
        
        return md5
            
    def check_md5(self):
        """
        Checks the MD5 checksum of the Dataset against its stored value.

        The stored value is in the Dataset's associated
        SymbolicDataset.  This will be used when regenerating data
        that once existed, as a coherence check.
        """
        # Recompute the MD5, see if it equals what is already stored
        return self.symbolicdataset.MD5_checksum == self.compute_md5()
    
class MethodLog(models.Model):
    """
    Logs of method execution.

    Output and error logs, i.e. the stdout and stderr produced by
    the RunStep.
    """
    runstep = models.OneToOneField(
        RunStep,
        help_text="RunStep producing these logs")
    
    output_log = models.FileField(
        "output log",
        upload_to="Logs",
        help_text="Terminal output of the RunStep Method, i.e. stdout.")
    
    error_log = models.FileField(
        "error log",
        upload_to="Logs",
        help_text="Terminal error output of the RunStep Method, i.e. stderr.")

    def clean(self):
        """Checks that the RunStep is for a Method."""
        if (type(self.runstep.pipelinestep.transformation) ==
                method.models.Method):
            raise ValidationError(
                "MethodLog \"{}\" does not correspond to a Method".
                format(self))