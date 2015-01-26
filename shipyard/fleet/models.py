import sys
import threading

from django.db import models, transaction
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError

import archive.models
import librarian.models
import pipeline.models

# This is an experimental replacement for the runfleet admin command.
# Disable it by setting worker_count to 0.
worker_count = 0

if worker_count > 0 and sys.argv[-1] == "runserver":
    # import here, because it causes problems when OpenMPI isn't loaded
    import fleet.workers
    
    manage_script = sys.argv[0]
    manager = fleet.workers.Manager(worker_count, manage_script)
    manager_thread = threading.Thread(target=manager.main_procedure)
    manager_thread.daemon = True
    manager_thread.start()


# Create your models here.
class RunToProcess(models.Model):
    """
    Represents a run that is ready to be processed.

    This table in the database functions as a queue of work to perform, which will
    then be served by the Manager of the fleet.  The required information to start
    a run are:
     - user
     - pipeline
     - inputs
     - sandbox path (default None)
    We also need to track the time that these are created, so we can do them in order.

    We also track some metadata to allow tracking of the queue of pipelines to run.
    Occasionally we should reap this table to remove stuff that's finished.
    """
    # The information needed to perform a run:
    # - user
    # - pipeline
    # - inputs
    # - sandbox_path (default is None)
    user = models.ForeignKey(User)
    pipeline = models.ForeignKey(pipeline.models.Pipeline)
    sandbox_path = models.CharField(max_length=256, default="", blank=True, null=False)
    time_queued = models.DateTimeField(auto_now_add=True)
    run = models.ForeignKey(archive.models.Run, null=True)

    def clean(self):
        if hasattr(self, "not_enough_CPUs"):
            self.not_enough_CPUs.clean()

    @property
    @transaction.atomic
    def started(self):
        return (self.run is not None) or hasattr(self, "not_enough_CPUs")

    @property
    @transaction.atomic
    def running(self):
        return self.started and not self.run.is_complete()

    @property
    @transaction.atomic
    def finished(self):
        return (self.started and self.run.is_complete()) or hasattr(self, "not_enough_CPUs")

    @transaction.atomic
    def get_run_progress(self):
        """
        Return a string describing the Run's current state.
        """
        if hasattr(self, "not_enough_CPUs"):
            esc = self.not_enough_CPUs
            return "Terminated: requested too many threads ({} requested, {} available)".format(
                esc.threads_requested, esc.max_available
            )
        
        input_name = None
        for run_input in self.inputs.all():
            input_name = run_input.symbolicdataset.dataset.name
            break

        if not self.started:
            status = "?"
            if input_name:
                status += "-" + input_name
            return status

        run = self.run

        status = ""

        # One of the steps is in progress?
        total_steps = run.pipeline.steps.count()
        for step in run.runsteps.order_by("pipelinestep__step_num"):
            if not step.is_complete():
                status += ":"
            elif not step.successful_execution():
                status += "!"
            else:
                status += "+"

        # Just finished a step, but didn't start the next one?
        status += "." * (total_steps - run.runsteps.count())
        
        status += "-"
        
        # One of the outcables is in progress?
        total_cables = run.pipeline.outcables.count()
        for cable in run.runoutputcables.order_by("pipelineoutputcable__output_idx"):
            status += "+" if cable.is_complete() else ":"

        status += "." * (total_cables - run.runoutputcables.count())
        
        if input_name:
            status += "-" + input_name

        return status


class RunToProcessInput(models.Model):
    """
    Represents an input to a run to process.
    """
    runtoprocess = models.ForeignKey(RunToProcess, related_name="inputs")
    symbolicdataset = models.ForeignKey(librarian.models.SymbolicDataset)
    index = models.PositiveIntegerField()


class ExceedsSystemCapabilities(models.Model):
    """
    Denotes a RunToProcess that could not be run due to requesting too much from the system.
    """
    runtoprocess = models.OneToOneField(RunToProcess, related_name="not_enough_CPUs")
    threads_requested = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    max_available = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    def clean(self):
        if self.threads_requested <= self.max_available:
            raise ValidationError("Threads requested ({}) does not exceed maximum available ({})".format(
                self.threads_requested, self.max_available
            ))