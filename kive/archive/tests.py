"""
Kive archive application unit tests.
"""

import os
import re
import tempfile
import json

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.files.base import ContentFile
from django.utils import timezone
from django.test import TestCase, TransactionTestCase
from django.core.urlresolvers import reverse, resolve
from rest_framework import status
from rest_framework.test import force_authenticate

from archive.models import Dataset, ExecLog, MethodOutput, Run, RunComponent,\
    RunOutputCable, RunStep, RunSIC
from datachecking.models import BadData
from file_access_utils import compute_md5
from librarian.models import ExecRecord, Dataset, DatasetStructure

from kive.tests import BaseTestCases, install_fixture_files, restore_production_files
from method.models import Method, MethodFamily, CodeResource
from pipeline.models import Pipeline, PipelineStep, PipelineFamily

import sandbox.execute
import kive.testing_utils as tools

# Rather than define everyone_group here, we import this function to prevent compile-time
# database access.
from metadata.models import kive_user, everyone_group, CompoundDatatype

from constants import groups


class ArchiveTestCaseHelpers:
    def __init__(self):
        pass

    def make_complete_non_reused(self, record, input_SDs, output_SDs):
        """
        Helper function to do everything necessary to make a RunStep,
        RunOutputCable, or RunStepInputCable complete, when it has not
        reused an ExecRecord (ie. make a new ExecRecord).

        """
        self.make_execlog_and_mark_non_reused_runcomponent(record)

        execrecord = ExecRecord.create(record.log, record.component, input_SDs, output_SDs)
        record.execrecord = execrecord
        record.save()

    def make_execlog_and_mark_non_reused_runcomponent(self, record):
        """Attaches a good ExecLog to a RunComponent."""
        record.reused = False
        record.save()

        execlog = ExecLog(record=record, invoking_record=record, start_time=timezone.now(), end_time=timezone.now())
        execlog.save()
        if record.is_step:
            MethodOutput(execlog=execlog, return_code=0).save()

    def make_complete_reused(self, record, input_SDs, output_SDs, other_parent):
        """
        Helper function to do everything necessary to make a RunStep,
        RunOutputCable, or RunStepInputCable complete, when it _has_
        reused an ExecRecord (ie. make an ExecRecord for it to resue).
        """
        record_type = record.__class__.__name__

        new_record = record.__class__.create(record.component, other_parent)

        execlog = ExecLog(record=new_record, invoking_record=new_record, start_time=timezone.now(),
                          end_time=timezone.now())
        execlog.save()
        if record_type == "RunStep":
            MethodOutput(execlog=execlog, return_code=0).save()
        execrecord = ExecRecord.create(execlog, record.component, input_SDs, output_SDs)

        record.execrecord = execrecord
        record.reused = True
        record.save()

    def complete_RSICs(self, runstep, input_SDs, output_SDs):
        """
        Helper function to create and complete all the RunSIC's needed for
        a given RunStep. input_SDs and output_SDs are lists of the input and
        output datasets for each cable, in order.
        """
        for i, cable in enumerate(runstep.pipelinestep.cables_in.order_by("dest__dataset_idx")):
            rsic = cable.psic_instances.create(runstep=runstep)
            self.make_complete_non_reused(rsic, [input_SDs[i]], [output_SDs[i]])

    def step_through_runstep_creation(self, bp):
        """
        Helper function to step through creation of a RunStep, breaking
        at a certain point (see the code for what these points are).
        """
        if bp == "empty_runs":
            return

        self.step_E1_RS = self.step_E1.pipelinestep_instances.create(run=self.pE_run)
        if bp == "first_runstep":
            return

        self.E03_11_RSIC = self.E03_11.psic_instances.create(runstep=self.step_E1_RS)
        self.make_complete_non_reused(self.E03_11_RSIC, [self.raw_dataset], [self.raw_dataset])
        self.raw_dataset.integrity_checks.create(execlog=self.E03_11_RSIC.log, user=self.myUser)
        if bp == "first_rsic":
            return

        self.make_complete_non_reused(self.step_E1_RS, [self.raw_dataset], [self.doublet_dataset])
        step1_in_ccl = self.doublet_dataset.content_checks.first()
        step1_in_ccl.execlog = self.step_E1_RS.log
        step1_in_ccl.save()
        self.doublet_dataset.file_source = self.step_E1_RS
        self.doublet_dataset.save()
        if bp == "first_runstep_complete":
            return

        self.step_E2_RS = self.step_E2.pipelinestep_instances.create(run=self.pE_run)
        if bp == "second_runstep":
            return

        self.complete_RSICs(self.step_E2_RS,
                            [self.triplet_dataset, self.singlet_dataset],
                            [self.D1_in_dataset, self.singlet_dataset])
        self.E01_21_RSIC = self.step_E2_RS.RSICs.filter(PSIC=self.E01_21).first()
        self.E02_22_RSIC = self.step_E2_RS.RSICs.filter(PSIC=self.E02_22).first()

        D1_in_ccl = self.D1_in_dataset.content_checks.first()
        D1_in_ccl.execlog = self.E01_21_RSIC.log
        D1_in_ccl.save()

        self.singlet_dataset.integrity_checks.create(execlog=self.E02_22_RSIC.log, user=self.myUser)
        if bp == "second_runstep_complete":
            return

        # Associate and complete sub-Pipeline.
        self.pD_run.parent_runstep = self.step_E2_RS
        self.pD_run.save()
        self.step_D1_RS = self.step_D1.pipelinestep_instances.create(run=self.pD_run)
        self.complete_RSICs(self.step_D1_RS,
                            [self.D1_in_dataset, self.singlet_dataset],
                            [self.D1_in_dataset, self.singlet_dataset])
        self.D01_11_RSIC = self.step_D1_RS.RSICs.filter(PSIC=self.D01_11).first()
        self.D02_12_RSIC = self.step_D1_RS.RSICs.filter(PSIC=self.D02_12).first()
        self.D1_in_dataset.integrity_checks.create(execlog=self.D01_11_RSIC.log, user=self.myUser)
        self.singlet_dataset.integrity_checks.create(execlog=self.D02_12_RSIC.log, user=self.myUser)

        self.make_complete_non_reused(self.step_D1_RS, [self.D1_in_dataset, self.singlet_dataset], [self.C1_in_dataset])
        C1_ccl = self.C1_in_dataset.content_checks.first()
        C1_ccl.execlog = self.step_D1_RS.log
        C1_ccl.save()
        self.C1_in_dataset.file_source = self.step_D1_RS
        self.C1_in_dataset.save()

        pD_ROC = self.pD.outcables.first().poc_instances.create(run=self.pD_run)
        self.make_complete_non_reused(pD_ROC, [self.C1_in_dataset], [self.C1_in_dataset])
        self.C1_in_dataset.integrity_checks.create(execlog=pD_ROC.log, user=self.myUser)

        if bp == "sub_pipeline":
            return

    def step_through_run_creation(self, bp):
        """
        Helper function to step through creation of a Run. bp is a
        breakpoint - these are defined throughout (see the code).
        """
        if not hasattr(self, 'myUser'):
            self.myUser = User.objects.get(username='john')
            self.mB = Method.objects.get(revision_name="mB_name")
            self.pD = Pipeline.objects.get(revision_name="pD_name")
            self.pE = Pipeline.objects.get(revision_name="pE_name")
            self.B1_in = self.mB.inputs.get(dataset_name="B1_in")
            self.B2_in = self.mB.inputs.get(dataset_name="B2_in")
            self.D1_in = self.pD.inputs.get(dataset_name="D1_in")
            self.D2_in = self.pD.inputs.get(dataset_name="D2_in")
            self.step_D1 = PipelineStep.objects.get(
                pipeline=self.pD,
                step_num=1)
            self.step_E1 = PipelineStep.objects.get(
                pipeline=self.pE,
                step_num=1)
            self.step_E2 = PipelineStep.objects.get(
                pipeline=self.pE,
                step_num=2)
            self.step_E3 = PipelineStep.objects.get(
                pipeline=self.pE,
                step_num=3)
            self.pE_run = Run.objects.get(pipeline=self.pE,
                                          name='pE_run')
            self.raw_dataset = Dataset.objects.get(name='raw_DS')
            self.singlet_dataset = Dataset.objects.get(
                dataset_file__endswith='singlet_cdt_large.csv')
            self.doublet_dataset = Dataset.objects.get(name='doublet')
            self.triplet_dataset = Dataset.objects.get(
                dataset_file__endswith='step_0_triplet.csv')
            self.C1_in_dataset = Dataset.objects.get(name='C1_in_triplet')
            self.C1_out_dataset = Dataset.objects.get(
                dataset_file__endswith='step_0_singlet.csv')
            self.C2_in_dataset = Dataset.objects.get(
                dataset_file__endswith='E11_32_output.csv')
            self.C2_out_dataset = Dataset.objects.get(name='C2_out')
            self.C3_out_dataset = Dataset.objects.get(name='C3_out')
            self.E1_out_dataset = Dataset.objects.get(name='E1_out')
            self.D1_in_dataset = Dataset.objects.get(
                structure__compounddatatype=self.doublet_dataset.structure.compounddatatype,
                structure__num_rows=10)
            self.D01_11 = self.step_D1.cables_in.get(dest__dataset_idx=1)
            self.D02_12 = self.step_D1.cables_in.get(dest__dataset_idx=2)
            self.E01_21 = self.step_E2.cables_in.get(dest__dataset_idx=1)
            self.E02_22 = self.step_E2.cables_in.get(dest__dataset_idx=2)
            self.E21_31 = self.step_E3.cables_in.get(dest__dataset_idx=1)
            self.E11_32 = self.step_E3.cables_in.get(dest__dataset_idx=2)

        # Changed May 14, 2014 to add CCLs/ICLs where appropriate.
        # Empty Runs.
        self.pD_run = self.pD.pipeline_instances.create(user=self.myUser)
        self.pD_run.grant_everyone_access()
        if bp == "empty_runs":
            return

        # First RunStep associated.
        self.step_E1_RS = self.step_E1.pipelinestep_instances.create(run=self.pE_run)
        if bp == "first_step":
            return

        # First RunSIC associated and completed.
        step_E1_RSIC = self.step_E1.cables_in.first().psic_instances.create(runstep=self.step_E1_RS)
        if bp == "first_cable_created":
            return

        self.make_complete_non_reused(step_E1_RSIC, [self.raw_dataset], [self.raw_dataset])
        icl = self.raw_dataset.integrity_checks.create(execlog=step_E1_RSIC.log, user=self.myUser)
        icl.start(save=False)
        icl.stop(save=False)
        icl.save()
        if bp == "first_cable":
            return

        # First RunStep completed.
        self.make_complete_non_reused(self.step_E1_RS, [self.raw_dataset], [self.doublet_dataset])
        step1_in_ccl = self.doublet_dataset.content_checks.first()
        step1_in_ccl.execlog = self.step_E1_RS.log
        step1_in_ccl.save()
        self.doublet_dataset.file_source = self.step_E1_RS
        self.doublet_dataset.save()
        if bp == "first_step_complete":
            return

        # Second RunStep associated.
        self.step_E2_RS = self.step_E2.pipelinestep_instances.create(run=self.pE_run)
        if bp == "second_step":
            return

        # Sub-pipeline for step 2 - reset step_E2_RS.
        self.step_E2_RS.delete()
        self.step_E2_RS = self.step_E2.pipelinestep_instances.create(run=self.pE_run, reused=None)
        self.complete_RSICs(self.step_E2_RS, [self.triplet_dataset, self.singlet_dataset],
                                             [self.D1_in_dataset, self.singlet_dataset])

        self.E01_21_RSIC = self.step_E2_RS.RSICs.filter(PSIC=self.E01_21).first()
        self.E02_22_RSIC = self.step_E2_RS.RSICs.filter(PSIC=self.E02_22).first()

        D1_in_ccl = self.D1_in_dataset.content_checks.first()
        D1_in_ccl.execlog = self.E01_21_RSIC.log
        D1_in_ccl.save()

        icl = self.singlet_dataset.integrity_checks.create(execlog=self.E02_22_RSIC.log, user=self.myUser)
        icl.start(save=False)
        icl.stop(save=False)
        icl.save()

        self.pD_run.parent_runstep = self.step_E2_RS
        self.pD_run.save()
        if bp == "sub_pipeline":
            return

        # Complete sub-Pipeline.
        self.step_D1_RS = self.step_D1.pipelinestep_instances.create(run=self.pD_run)
        self.complete_RSICs(self.step_D1_RS, [self.D1_in_dataset, self.singlet_dataset],
                                             [self.D1_in_dataset, self.singlet_dataset])

        self.D01_11_RSIC = self.step_D1_RS.RSICs.filter(PSIC=self.D01_11).first()
        self.D02_12_RSIC = self.step_D1_RS.RSICs.filter(PSIC=self.D02_12).first()
        icl = self.D1_in_dataset.integrity_checks.create(execlog=self.D01_11_RSIC.log, user=self.myUser)
        icl.start(save=False)
        icl.stop(save=False)
        icl.save()
        icl = self.singlet_dataset.integrity_checks.create(execlog=self.D02_12_RSIC.log, user=self.myUser)
        icl.start(save=False)
        icl.stop(save=False)
        icl.save()

        self.make_complete_non_reused(self.step_D1_RS, [self.D1_in_dataset, self.singlet_dataset], [self.C1_in_dataset])
        C1_ccl = self.C1_in_dataset.content_checks.first()
        C1_ccl.execlog = self.step_D1_RS.log
        C1_ccl.save()
        self.C1_in_dataset.file_source = self.step_D1_RS
        self.C1_in_dataset.save()

        pD_ROC = self.pD.outcables.first().poc_instances.create(run=self.pD_run)
        self.make_complete_non_reused(pD_ROC, [self.C1_in_dataset], [self.C1_in_dataset])
        icl = self.C1_in_dataset.integrity_checks.create(execlog=pD_ROC.log, user=self.myUser)
        icl.start(save=False)
        icl.stop(save=False)
        icl.save()
        if bp == "sub_pipeline_complete":
            return

        # Third RunStep associated.
        self.step_E3_RS = self.step_E3.pipelinestep_instances.create(run=self.pE_run)
        if bp == "third_step":
            return

        # Third RunStep completed.
        self.complete_RSICs(self.step_E3_RS, [self.C1_in_dataset, self.doublet_dataset],
                                             [self.C1_in_dataset, self.C2_in_dataset])

        self.E21_31_RSIC = self.step_E3_RS.RSICs.filter(PSIC=self.E21_31).first()
        self.E11_32_RSIC = self.step_E3_RS.RSICs.filter(PSIC=self.E11_32).first()
        icl = self.C1_in_dataset.integrity_checks.create(execlog=self.E21_31_RSIC.log, user=self.myUser)
        icl.start(save=False)
        icl.stop(save=False)
        icl.save()

        # C2_in_dataset was created here so we associate its CCL with cable
        # E11_32.
        C2_in_ccl = self.C2_in_dataset.content_checks.first()
        C2_in_ccl.execlog = self.E11_32_RSIC.log
        C2_in_ccl.save()

        if bp == "third_step_cables_done":
            return

        step3_outs = [self.C1_out_dataset, self.C2_out_dataset, self.C3_out_dataset]
        self.make_complete_non_reused(self.step_E3_RS, [self.C1_in_dataset, self.C2_in_dataset], step3_outs)
        # All of these were first created here, so associate the CCL of C1_out_dataset to step_E3_RS.
        # The others are raw and don't have CCLs.
        C1_out_ccl = self.C1_out_dataset.content_checks.first()
        C1_out_ccl.execlog = self.step_E3_RS.log
        C1_out_ccl.save()

        if bp == "third_step_complete":
            return

        # Outcables associated.
        roc1 = self.pE.outcables.get(output_idx=1).poc_instances.create(run=self.pE_run)
        self.make_complete_non_reused(roc1, [self.C1_in_dataset], [self.E1_out_dataset])
        # This was first created here, so associate the CCL appropriately.
        E1_out_ccl = self.E1_out_dataset.content_checks.first()
        E1_out_ccl.execlog = roc1.log
        E1_out_ccl.save()
        self.E1_out_dataset.file_source = roc1
        self.E1_out_dataset.save()

        if bp == "first_outcable":
            return

        roc2 = self.pE.outcables.get(output_idx=2).poc_instances.create(run=self.pE_run)
        self.make_complete_non_reused(roc2, [self.C1_out_dataset], [self.C1_out_dataset])
        roc3 = self.pE.outcables.get(output_idx=3).poc_instances.create(run=self.pE_run)
        self.make_complete_non_reused(roc3, [self.C3_out_dataset], [self.C3_out_dataset])

        # roc2 and roc3 are trivial cables, so we associate integrity checks with C1_out_dataset
        # and C3_out_dataset.
        icl = self.C1_out_dataset.integrity_checks.create(execlog=roc2.log, user=self.myUser)
        icl.start(save=False)
        icl.stop(save=False)
        icl.save()
        icl = self.C3_out_dataset.integrity_checks.create(execlog=roc3.log, user=self.myUser)
        icl.start(save=False)
        icl.stop(save=False)
        icl.save()

        if bp == "outcables_done":
            return

    def step_through_runsic_creation(self, bp):
        """
        Helper function to step through creating an RSIC, breaking at a
        certain point (see the code).
        """
        self.step_E3_RS = self.step_E3.pipelinestep_instances.create(run=self.pE_run)
        if bp == "runstep":
            return

        self.E11_32_RSIC = self.E11_32.psic_instances.create(runstep=self.step_E3_RS)
        if bp == "rsic_created":
            return

        self.make_complete_non_reused(self.E11_32_RSIC, [self.doublet_dataset], [self.C2_in_dataset])
        # C2_in_dataset is created by this cable so associate a CCL appropriately.
        C2_ccl = self.C2_in_dataset.content_checks.first()
        C2_ccl.execlog = self.E11_32_RSIC.log
        C2_ccl.save()
        if bp == "rsic_completed":
            return

        self.E21_31_RSIC = self.E21_31.psic_instances.create(runstep=self.step_E3_RS)
        self.make_complete_non_reused(self.E21_31_RSIC, [self.C1_in_dataset], [self.C1_in_dataset])
        # C1_in_dataset is not created by this RSIC, so associate an ICL.
        self.C1_in_dataset.integrity_checks.create(execlog=self.E21_31_RSIC.log, user=self.myUser)
        self.make_complete_non_reused(self.step_E3_RS,
                                      [self.C1_in_dataset, self.C2_in_dataset],
                                      [self.C1_out_dataset, self.C2_out_dataset, self.C3_out_dataset])
        # Associate the CCL of C1_out_dataset with step_E3_RS.
        C1_out_ccl = self.C1_out_dataset.content_checks.first()
        C1_out_ccl.execlog = self.step_E3_RS.log
        C1_out_ccl.save()
        if bp == "runstep_completed":
            return

    def step_through_roc_creation(self, bp):
        """Break at an intermediate stage of ROC creation."""
        self.E31_42_ROC = self.E31_42.poc_instances.create(run=self.pE_run)
        self.E21_41_ROC = self.E21_41.poc_instances.create(run=self.pE_run)
        if bp == "roc_created":
            return

        self.make_complete_non_reused(self.E31_42_ROC, [self.singlet_dataset], [self.singlet_dataset])
        if bp == "trivial_roc_completed":
            return

        self.make_complete_non_reused(self.E21_41_ROC, [self.C1_in_dataset], [self.doublet_dataset])
        self.doublet_dataset.file_source = self.E21_41_ROC
        self.doublet_dataset.save()
        if bp == "custom_roc_completed":
            return

        self.step_E2_RS = self.step_E2.pipelinestep_instances.create(run=self.pE_run)
        self.step_E2_RS.start(save=True)
        self.step_E2_RS.save()
        self.pD_run = self.pD.pipeline_instances.create(user=self.myUser)
        self.pD_run.parent_runstep = self.step_E2_RS
        self.pD_run.grant_everyone_access()
        self.pD_run.save()
        self.D11_21_ROC = self.D11_21.poc_instances.create(run=self.pD_run)
        self.D11_21_ROC.start(save=False)
        # Define some custom wiring for D11_21: swap the first two columns.
        pin1, pin2, _ = (m for m in self.triplet_cdt.members.all())
        self.D11_21.custom_wires.create(source_pin=pin1, dest_pin=pin2)
        self.D11_21.custom_wires.create(source_pin=pin2, dest_pin=pin1)
        if bp == "subrun":
            return

        self.make_complete_non_reused(self.D11_21_ROC, [self.C1_in_dataset], [self.C1_in_dataset])
        self.C1_in_dataset.file_source = self.D11_21_ROC
        self.C1_in_dataset.save()
        self.C1_in_dataset.content_checks.create(execlog=self.D11_21_ROC.log, start_time=timezone.now(),
                                               end_time=timezone.now(), user=self.myUser)
        self.D11_21_ROC.stop(save=True)
        if bp == "subrun_complete":
            return

    def _setup_deep_nested_run(self, user):
        """Set up a pipeline with sub-sub-pipelines to test recursion."""
        # Everything in this pipeline will be a no-op, so all can be linked together
        # without remorse.
        p_basic = tools.make_first_pipeline("p_basic", "innermost pipeline", user)
        tools.create_linear_pipeline(p_basic, [self.method_noop, self.method_noop], "basic_in", "basic_out")
        p_basic.family.grant_everyone_access()
        p_basic.grant_everyone_access()
        p_basic.create_outputs()
        p_basic.save()

        p_sub = tools.make_first_pipeline("p_sub", "second-level pipeline", user)
        tools.create_linear_pipeline(p_sub, [p_basic, p_basic], "sub_in", "sub_out")
        p_sub.family.grant_everyone_access()
        p_sub.grant_everyone_access()
        p_sub.create_outputs()
        p_sub.save()

        p_top = tools.make_first_pipeline("p_top", "top-level pipeline", user)
        tools.create_linear_pipeline(p_top, [p_sub, p_sub, p_sub], "top_in", "top_out")
        p_top.family.grant_everyone_access()
        p_top.grant_everyone_access()
        p_top.create_outputs()
        p_top.save()

        # Set up a dataset with words in it called self.dataset_words.
        tools.make_words_dataset(self)

        run_sandbox = sandbox.execute.Sandbox(self.user_bob, p_top, [self.dataset_words],
                                              groups_allowed=[everyone_group()])
        run_sandbox.execute_pipeline()
        self.deep_nested_run = run_sandbox.run


class ArchiveTestCase(TestCase, ArchiveTestCaseHelpers):
    fixtures = ["archive_test_environment"]

    def setUp(self):
        install_fixture_files("archive_test_environment")
        tools.load_archive_test_environment(self)

    def tearDown(self):
        restore_production_files()


class ArchiveTransactionTestCase(TransactionTestCase, ArchiveTestCaseHelpers):
    fixtures = ["archive_test_environment"]

    def setUp(self):
        install_fixture_files("archive_test_environment")
        tools.load_archive_test_environment(self)

    def tearDown(self):
        restore_production_files()


class RunComponentTests(ArchiveTestCase):
    """Tests of functionality shared by all RunComponents."""

    def test_clean_execlogs_invoked_logs_cleaned(self):
        """Test that _clean_execlogs properly calls clean on its invoked logs."""
        self.step_through_run_creation("outcables_done")

        # For every RunComponent invoked during this run, break each of its invoked_logs and see if it appears.
        atomicrunsteps = []
        for runstep in RunStep.objects.all():
            if runstep.transformation.is_method:
                atomicrunsteps.append(runstep)
        runcomponents = (atomicrunsteps + list(RunSIC.objects.all()) + list(RunOutputCable.objects.all()))

        for runcomponent in runcomponents:
            # Skip RunComponents that are not part of this Run.
            if runcomponent.top_level_run != self.pE_run:
                continue

            for invoked_log in runcomponent.invoked_logs.all():
                original_el_start_time = invoked_log.start_time
                invoked_log.start_time = None
                invoked_log.save()
                self.assertRaisesRegexp(
                    ValidationError,
                    'Stopwatch "{}" does not have a start time but it has an end time'.format(invoked_log),
                    runcomponent.clean)
                invoked_log.start_time = original_el_start_time
                invoked_log.save()

    def test_clean_execlogs_invoked_logs_ICLs_CCLs_cleaned(self):
        """Test that ICLs and CCLs are appropriately cleaned in _clean_execlogs."""
        self.step_through_run_creation("outcables_done")

        # For every RunComponent invoked during this run, break each of its ICLs/CCLs and see if it appears.
        atomicrunsteps = []
        for runstep in RunStep.objects.all():
            if runstep.transformation.is_method:
                atomicrunsteps.append(runstep)
        runcomponents = (atomicrunsteps + list(RunSIC.objects.all()) + list(RunOutputCable.objects.all()))

        for runcomponent in runcomponents:
            # Skip RunComponents that are not part of this Run.
            if runcomponent.top_level_run != self.pE_run:
                continue

            for invoked_log in runcomponent.invoked_logs.all():
                for checklog in (list(invoked_log.integrity_checks.all()) + list(invoked_log.content_checks.all())):
                    original_checklog_start_time = checklog.start_time
                    checklog.start_time = None
                    checklog.save()
                    self.assertRaisesRegexp(
                        ValidationError,
                        'Stopwatch "{}" does not have a start time but it has an end time'.format(checklog),
                        runcomponent.clean)
                    checklog.start_time = original_checklog_start_time
                    checklog.save()

    def test_clean_execlogs_log_not_among_invoked_logs(self):
        """A RunComponent's log should be among its invoked_logs if any invoked_logs exist."""
        self.step_through_run_creation("outcables_done")

        # Imagine that step 3 invokes step 1 but not itself.  Note that this would break the Run overall
        # but we're only looking to check for errors local to a single RunComponent.
        step_3_el = self.step_E3_RS.log
        step_1_el = self.step_E1_RS.log

        step_1_el.invoking_record = self.step_E3_RS
        step_1_el.save()

        step_3_el.invoking_record = self.pE_run.runoutputcables.get(pipelineoutputcable=self.E21_41)
        step_3_el.save()

        self.assertRaisesRegexp(ValidationError,
                                re.escape('ExecLog of {} "{}" is not included with its invoked ExecLogs'.format(
                                    self.step_E3_RS.__class__.__name__, self.step_E3_RS
                                )),
                                self.step_E3_RS.clean)

    def test_clean_execlogs_log_set_before_invoked_ExecLogs_complete(self):
        """A RunComponent's log should not be set before all invoked_logs are complete."""
        self.step_through_run_creation("third_step_complete")

        # Imagine that step 3 invokes step 1 and itself.  Note that this would break the Run overall
        # but we're only looking to check for errors local to a single RunComponent.
        step_1_el = self.step_E1_RS.log
        step_1_el.invoking_record = self.step_E3_RS
        step_1_el.save()

        # Make step_1_el incomplete.
        step_1_el.end_time = None
        step_1_el.save()

        self.assertRaisesRegexp(ValidationError,
                                re.escape('ExecLog of {} "{}" is set before all invoked ExecLogs are complete'.format(
                                    self.step_E3_RS.__class__.__name__, self.step_E3_RS
                                )),
                                self.step_E3_RS.clean)

    def test_clean_execlogs_log_set_before_invoked_ExecLogs_finish_checks(self):
        """A RunComponent's log should not be set before all invoked_logs finish their checks."""
        self.step_through_run_creation("third_step_complete")

        # Imagine that step 3 invokes step 1 and itself.  Note that this would break the Run overall
        # but we're only looking to check for errors local to a single RunComponent.
        step_1_el = self.step_E1_RS.log
        step_1_el.invoking_record = self.step_E3_RS
        step_1_el.save()

        # Remove step_1_el's ContentCheckLog.
        step_1_el.content_checks.first().delete()

        self.assertRaisesRegexp(
            ValidationError,
            re.escape('Invoked ExecLogs preceding log of {} "{}" did not successfully pass all of their checks'.format(
                self.step_E3_RS.__class__.__name__, self.step_E3_RS
            )),
            self.step_E3_RS.clean)

    def test_clean_execlogs_runcomponent_invokes_previous_runcomponent(self):
        """Testing clean on a RunComponent which invoked a previous RunComponent in the correct fashion."""
        self.step_through_run_creation("third_step_complete")

        # Imagine that step 3 invokes step 1 and itself.  Note that this would break the Run overall
        # but we're only looking to check for errors local to a single RunComponent.
        step_1_el = self.step_E1_RS.log
        step_1_el.invoking_record = self.step_E3_RS
        step_1_el.save()

        self.assertIsNone(self.step_E3_RS.clean())

    # def test_clean_execlogs_runcomponent_invoked_by_subsequent_runcomponent(self):
    #     """
    #     Testing clean on a RunComponent whose ExecLog was invoked by a subsequent RunComponent.
    #     """
    #     # Run two pipelines, where the second reuses parts from the first.
    #     # self.run_pipelines_recovering_reused_step()
    #
    #     # The ExecLog of the first RunStep in sandbox_two's run should have been invoked by
    #     # the transformation of step 2.
    #     run_two_step_one = self.sandbox_two.run.runsteps.get(pipelinestep__step_num=1)
    #     run_two_step_two = self.sandbox_two.run.runsteps.get(pipelinestep__step_num=2)
    #
    #     self.assertEquals(run_two_step_one.log.invoking_record.definite, run_two_step_two)
    #     self.assertIsNone(run_two_step_one.clean())
    #     self.assertIsNone(run_two_step_two.clean())

    def test_clean_undecided_reused_invoked_logs_exist(self):
        """A RunComponent that has not decided on reuse should not have any invoked logs."""
        self.step_through_run_creation("third_step_cables_done")
        step_one_el = self.step_E1_RS.log
        step_one_el.invoking_record = self.step_E3_RS
        step_one_el.save()

        self.assertRaisesRegexp(
            ValidationError,
            re.escape('{} "{}" has not decided whether or not to reuse an ExecRecord'
                      "; no steps or cables should have been invoked".format("RunStep", self.step_E3_RS)),
            self.step_E3_RS.clean
        )

    def test_clean_reused_invoked_logs_exist(self):
        """A RunComponent that reuses an ExecRecord should not have any invoked logs."""
        self.step_through_runstep_creation("first_runstep_complete")
        self.step_E1_RS.reused = True
        for curr_output in self.step_E1_RS.outputs.all():
            curr_output.file_source = None
            curr_output.save()

        self.assertRaisesRegexp(
            ValidationError,
            re.escape('{} "{}" reused an ExecRecord; no steps or cables should have been invoked'.format(
                "RunStep", self.step_E1_RS)),
            self.step_E1_RS.clean
        )

    def test_clean_not_reused_runcomponent_log_invoked_elsewhere(self):
        """Testing clean on a RunComponent whose log was invoked elsewhere by a subsequent RunComponent."""
        self.step_through_run_creation("third_step_complete")

        # Imagine that step 3 invokes step 1 and itself.  Note that this would break the Run overall
        # but we're only looking to check for errors local to a single RunComponent.
        step_1_el = self.step_E1_RS.log
        step_1_el.invoking_record = self.step_E3_RS
        step_1_el.save()

        self.assertRaisesRegexp(
            ValidationError,
            re.escape('{} "{}" is not reused and has not completed its own ExecLog but does have an ExecRecord'.format(
                "RunStep", self.step_E1_RS
            )),
            self.step_E1_RS.clean)

    def test_clean_has_execlog_no_execrecord_yet_has_checks(self):
        """Testing clean on a RunComponent that has an ExecLog but no ExecRecord yet also has data checks."""
        self.step_through_run_creation("outcables_done")

        for rc in RunComponent.objects.all():
            if rc.definite.top_level_run == self.pE_run:
                # Unset its ExecRecord and clean it.
                rc.execrecord = None
                self.assertRaisesRegexp(
                    ValidationError,
                    '{} "{}" does not have an ExecRecord so should not have any data checks'.format(
                        rc.definite.__class__.__name__, rc.definite
                    ),
                    rc.definite.clean())


class RunComponentInvokedBySubsequentTests(TestCase):
    fixtures = ["run_pipelines_recovering_reused_step"]

    def setUp(self):
        install_fixture_files("run_pipelines_recovering_reused_step")

    def tearDown(self):
        restore_production_files()

    def test_clean_execlogs_runcomponent_invoked_by_subsequent_runcomponent(self):
        """
        Testing clean on a RunComponent whose ExecLog was invoked by a subsequent RunComponent.
        """
        # In the fixture, we ran two pipelines, where the second reused parts from the first.
        pipeline_two = Pipeline.objects.get(family__name="p_two", revision_name="v1")
        run_two = pipeline_two.pipeline_instances.first()

        # The ExecLog of the first RunStep in sandbox_two's run should have been invoked by
        # the transformation of step 2.
        run_two_step_one = run_two.runsteps.get(pipelinestep__step_num=1)
        run_two_step_two = run_two.runsteps.get(pipelinestep__step_num=2)

        self.assertEquals(run_two_step_one.log.invoking_record.definite, run_two_step_two)
        self.assertIsNone(run_two_step_one.clean())
        self.assertIsNone(run_two_step_two.clean())


class RunStepTests(ArchiveTestCase):

    def test_RunStep_clean_wrong_pipeline(self):
        """
        A RunStep which has a PipelineStep from one Pipeline, but a Run
        for a different Pipeline, is not clean.
        """
        self.step_through_runstep_creation("empty_runs")
        runstep = self.step_D1.pipelinestep_instances.create(run=self.pE_run)
        self.assertRaisesRegexp(ValidationError,
                                re.escape('PipelineStep "{}" of RunStep "{}" does not belong to Pipeline "{}"'
                                          .format(self.step_D1, runstep, self.pE)),
                                runstep.clean)

    def test_RunStep_clean_child_run_for_method(self):
        """
        A RunStep which represents a Method should not have a child_run
        defined.
        """
        self.step_through_runstep_creation("first_runstep")
        self.pD_run.parent_runstep = self.step_E1_RS
        self.pD_run.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('PipelineStep of RunStep "{}" is not a Pipeline but a child run exists'
                                          .format(self.step_E1_RS)),
                                self.step_E1_RS.clean)

    def test_RunStep_clean_no_RunSICs(self):
        """
        A RunStep with no RunSIC's is clean.
        """
        self.step_through_runstep_creation("first_runstep")
        self.assertIsNone(self.step_E1_RS.clean())

    def test_RunStep_clean_incomplete_RunSIC(self):
        """
        A RunStep with an incomplete RunSIC is not clean.
        """
        self.step_through_runstep_creation("first_rsic")

        # Make this RunSIC incomplete by removing the ICL.
        icl_to_remove = self.raw_dataset.integrity_checks.filter(execlog=self.E03_11_RSIC.log).first()
        icl_to_remove.execlog = None
        icl_to_remove.save()

        self.assertRaisesRegexp(ValidationError,
                                re.escape('{} "{}" is not complete'.format("RunSIC", self.E03_11_RSIC)),
                                self.step_E1_RS.clean)

    def test_RunStep_complete_RunSIC(self):
        """
        A RunStep with a complete RunSIC is clean.
        """
        self.step_through_runstep_creation("first_rsic")
        self.assertIsNone(self.step_E1_RS.clean())

    def test_RunStep_inputs_unquenched_with_data(self):
        """
        A RunStep with unquenched input cables, but an associated
        Dataset, is not clean.
        """
        self.step_through_runstep_creation("first_runstep")
        self.doublet_dataset.file_source = self.step_E1_RS
        self.doublet_dataset.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" inputs not quenched; no data should have been generated'
                                          .format(self.step_E1_RS)),
                                self.step_E1_RS.clean)

    def test_RunStep_input_unquenched_with_reused(self):
        """
        A RunStep with unquenched input cables, but which has decided
        not to reuse an ExecRecord, is not clean.
        """
        self.step_through_runstep_creation("first_runstep")
        self.step_E1_RS.reused = False
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" inputs not quenched; reused and execrecord should not be set'
                                          .format(self.step_E1_RS)),
                                self.step_E1_RS.clean)

    def test_RunStep_input_unquenched_with_execrecord(self):
        """
        A RunStep with unquenched input cables, but which has an
        associated ExecRecord, is not clean.
        """
        self.step_through_runstep_creation("first_runstep_complete")
        self.E03_11_RSIC.delete()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" inputs not quenched; reused and execrecord should not be set'
                                          .format(self.step_E1_RS)),
                                self.step_E1_RS.clean)

    def test_RunStep_input_unquenched_with_log(self):
        """
        A RunStep with unquenched input cables, but which has an
        associated ExecLog, is not clean.
        """
        self.step_through_runstep_creation("first_runstep_complete")
        self.E03_11_RSIC.delete()
        self.step_E1_RS.execrecord = None
        self.step_E1_RS.reused = None
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" inputs not quenched; no log should have been generated'
                                          .format(self.step_E1_RS)),
                                self.step_E1_RS.clean)

    def test_RunStep_input_unquenched_invokes_other_RunComponents(self):
        """
        A RunStep with unquenched input cables should not have any invoked_logs.
        """
        # This is a broken setup: step 1 can't have been invoked by step 3.
        # Still, we're just checking step 3 here.
        self.step_through_run_creation("third_step")

        step_one_el = self.step_E1_RS.log
        step_one_el.invoking_record = self.step_E3_RS
        step_one_el.save()

        self.assertRaisesRegexp(
            ValidationError,
            re.escape('RunStep "{}" inputs not quenched; no other steps or cables should have been invoked'
                      .format(self.step_E3_RS)),
            self.step_E3_RS.clean)

    def test_RunStep_input_unquenched_with_child_run(self):
        """
        A RunStep with unquenched input cables, but which has a
        child_run, is not clean.
        """
        self.step_through_runstep_creation("second_runstep")
        self.pD_run.parent_runstep = self.step_E2_RS
        self.pD_run.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" inputs not quenched; child_run should not be set'
                                          .format(self.step_E2_RS)),
                                self.step_E2_RS.clean)

    def test_RunStep_clean_inputs_quenched(self):
        """
        A RunStep with all its inputs quenched is clean.
        """
        self.step_through_runstep_creation("second_runstep_complete")
        self.assertIsNone(self.step_E2_RS.clean())

    def test_RunStep_clean_undecided_reused_with_execrecord(self):
        """
        A RunStep which has not decided whether to reuse an ExecRecord,
        but which has one associated, is not clean.
        """
        self.step_through_runstep_creation("first_rsic")

        other_run = self.pE.pipeline_instances.create(user=self.myUser)
        other_run.grant_everyone_access()
        other_runstep = self.step_E1.pipelinestep_instances.create(run=other_run)
        rsic = self.E03_11.psic_instances.create(runstep=other_runstep)
        self.make_complete_non_reused(rsic, [self.raw_dataset], [self.raw_dataset])
        self.make_complete_non_reused(other_runstep, [self.raw_dataset], [self.doublet_dataset])

        self.step_E1_RS.execrecord = other_runstep.execrecord

        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" has not decided whether or not to reuse an ExecRecord; '
                                          'execrecord should not be set'.format(self.step_E1_RS)),
                                self.step_E1_RS.clean)

    def test_RunStep_clean_undecided_reused_with_execlog(self):
        """
        A RunStep which has not decided whether to reuse an ExecRecord,
        but which has an ExecLog, is not clean.
        """
        self.step_through_runstep_creation("first_runstep_complete")
        self.step_E1_RS.reused = None
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" has not decided whether or not to reuse an ExecRecord; '
                                          'no log should have been generated'.format(self.step_E1_RS)),
                                self.step_E1_RS.clean)

    def test_RunStep_clean_undecided_reused_with_data(self):
        """
        A RunStep which has not decided whether to reuse an ExecRecord,
        but which has output Datasets, is not clean.
        """
        # Give step_E1_RS a complete ExecLog.
        self.step_through_runstep_creation("first_runstep_complete")

        # To bypass the check for quenched inputs, we have to create
        # another ExecRecord which matches step_E1.
        other_run = self.pE.pipeline_instances.create(user=self.myUser)
        other_run.grant_everyone_access()
        other_runstep = self.step_E1.pipelinestep_instances.create(run=other_run)
        rsic = self.E03_11.psic_instances.create(runstep=other_runstep)
        self.make_complete_non_reused(rsic, [self.raw_dataset], [self.raw_dataset])
        self.make_complete_non_reused(other_runstep, [self.raw_dataset], [self.doublet_dataset])

        self.step_E1_RS.reused = None
        self.step_E1_RS.execrecord = other_runstep.execrecord

        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" has not decided whether or not to reuse an ExecRecord; '
                                          'no log should have been generated'.format(self.step_E1_RS)),
                                self.step_E1_RS.clean)

    def test_RunStep_clean_reused_with_data(self):
        """
        A RunStep which has decided to reuse an ExecRecord, but which
        has output Datasets, is not clean.
        """
        self.step_through_runstep_creation("first_runstep_complete")
        self.step_E1_RS.reused = True
        self.doublet_dataset.file_source = self.step_E1_RS
        self.doublet_dataset.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" reused an ExecRecord and should not have generated any Datasets'
                                          .format(self.step_E1_RS)),
                                self.step_E1_RS.clean)

    def test_RunStep_clean_subpipeline_with_execlog(self):
        """
        A RunStep which has a child run should have no ExecLog.
        """
        self.step_through_runstep_creation("sub_pipeline")
        ExecLog(record=self.step_E2_RS, invoking_record=self.step_E2_RS,
                start_time=timezone.now(), end_time=timezone.now()).save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" represents a sub-pipeline so no log should be associated'
                                          .format(self.step_E2_RS)),
                                self.step_E2_RS.clean)

    def test_RunStep_clean_subpipeline_with_dataset(self):
        """
        A RunStep which has a child run should have no associated output
        Datasets.
        """
        self.step_through_runstep_creation("second_runstep_complete")
        self.step_E2_RS.outputs.add(self.singlet_dataset)
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" represents a sub-pipeline and should not have generated any '
                                          'data'.format(self.step_E2_RS)),
                                self.step_E2_RS.clean)

    def test_RunStep_clean_subpipeline_with_reused(self):
        """
        A RunStep which has a child run should not have set reused.
        """
        self.step_through_runstep_creation("second_runstep_complete")
        self.step_E2_RS.reused = True
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" represents a sub-pipeline so reused should not be set'
                                          .format(self.step_E2_RS)),
                                self.step_E2_RS.clean)

    def test_RunStep_clean_subpipeline_with_execrecord(self):
        """
        A RunStep which has a child run should not have an execrecord.
        """
        self.step_through_runstep_creation("second_runstep_complete")
        other_run = self.pE.pipeline_instances.create(user=self.myUser)
        other_run.grant_everyone_access()
        other_runstep = self.step_E2.pipelinestep_instances.create(run=other_run)
        execlog = ExecLog(record=other_runstep, invoking_record=other_runstep,
                          start_time=timezone.now(), end_time=timezone.now())
        execlog.save()
        execrecord = ExecRecord(generator=execlog)
        execrecord.save()
        self.step_E2_RS.execrecord = execrecord
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" represents a sub-pipeline so execrecord should not be set'
                                          .format(self.step_E2_RS)),
                                self.step_E2_RS.clean)

    def test_RunStep_clean_subpipeline_good(self):
        """
        A RunStep representing a sub-pipeline, which has not set
        reused and has no ExecLog, is clean.
        """
        self.step_through_runstep_creation("second_runstep")
        self.assertIsNone(self.step_E2_RS.clean())

    def test_RunStep_clean_reused_no_execrecord(self):
        """
        A RunStep which has decided to reuse an ExecRecord, but doesn't
        have one associated yet, is clean.
        """
        # May 14, 2014: fixed this test to reflect the way things work now.
        self.step_through_runstep_creation("first_rsic")

        other_run = self.pE.pipeline_instances.create(user=self.myUser)
        other_run.grant_everyone_access()
        self.make_complete_reused(self.step_E1_RS, [self.raw_dataset], [self.doublet_dataset], other_run)

        self.step_E1_RS.execrecord = None
        self.step_E1_RS.outputs.clear()
        self.assertIsNone(self.step_E1_RS.clean())

    def test_RunStep_clean_non_reused_bad_data(self):
        """
        A RunStep which has decided not to reuse an ExecRecord,
        and has bad output data, is not clean.
        """
        self.step_through_runstep_creation("first_runstep_complete")
        self.step_E1_RS.reused = False
        self.doublet_dataset.file_source = self.step_E1_RS
        self.doublet_dataset.MD5_checksum = "foo"
        self.doublet_dataset.save()
        with open(self.doublet_dataset.dataset_file.path) as f:
            checksum = compute_md5(f)

        self.assertRaisesRegexp(ValidationError,
                                re.escape('File integrity of "{}" lost. Current checksum "{}" does not equal expected '
                                          'checksum "{}"'.format(self.doublet_dataset, checksum, "foo")),
                                self.step_E1_RS.clean)

    def test_RunStep_clean_non_reused_good_data(self):
        """
        A RunStep which has decided not to reuse an ExecRecord, and has
        clean output data, is clean.
        """
        self.step_through_runstep_creation("first_runstep_complete")
        self.doublet_dataset.MD5_checksum = self.doublet_dataset.compute_md5()
        self.doublet_dataset.save()
        self.assertIsNone(self.step_E1_RS.clean())

    def test_RunStep_clean_good_child_run(self):
        """
        A RunStep with a child_run which is clean, is also clean.
        """
        self.step_through_runstep_creation("sub_pipeline")
        self.assertIsNone(self.step_E2_RS.clean())

    def test_RunStep_clean_bad_child_run(self):
        """
        A RunStep with a child_run which is not clean, is also not
        clean. Note: this isn't quite the same as the original test,
        I'm having trouble hitting the "execrecord should not be set"
        error.
        """
        self.step_through_runstep_creation("sub_pipeline")

        self.step_D1_RS.reused = None
        self.step_D1_RS.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" has not decided whether or not to reuse an ExecRecord; '
                                          'no log should have been generated'.format(self.step_D1_RS)),
                                self.step_E2_RS.clean)

    def test_RunStep_clean_bad_execrecord(self):
        """
        A RunStep whose ExecRecord is not clean, is also not clean.
        """
        self.step_through_runstep_creation("first_runstep_complete")
        execrecord = self.step_E1_RS.execrecord
        execrecord.execrecordins.first().delete()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('Input(s) to ExecRecord "{}" are not quenched'.format(execrecord)),
                                self.step_E1_RS.clean)

    def test_RunStep_clean_good_execrecord(self):
        """
        A RunStep representing a Method with a clean ExecRecord and no
        other problems, is clean.
        """
        self.step_through_runstep_creation("first_runstep_complete")
        self.assertIsNone(self.step_E1_RS.clean())

    def test_RunStep_execrecord_wrong_transformation(self):
        """
        If a RunStep has an associated ExecRecord, it must point to
        the same transformation that the RunStep does.
        """
        self.step_through_runstep_creation("sub_pipeline")
        self.step_E1_RS.execrecord = self.step_D1_RS.execrecord
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" points to transformation "{}" but corresponding ExecRecord '
                                          'does not'.format(self.step_E1_RS, self.step_E1)),
                                self.step_E1_RS.clean)

    def test_RunStep_deleted_output_with_data(self):
        """
        A RunStep with an output marked for deletion, should not have
        any Datasets associated to that output.
        """
        self.step_through_runstep_creation("first_step_complete")
        self.step_E1.outputs_to_delete.add(self.mA.outputs.get(dataset_name="A1_out"))
        self.step_E1.save()
        output = self.step_E1.transformation.outputs.first()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('Output "{}" of RunStep "{}" is deleted; no data should be associated'
                                          .format(output, self.step_E1_RS)),
                                self.step_E1_RS.clean)

    def test_RunStep_missing_output_with_data(self):
        """
        A RunStep with a missing output, should not have any Datasets
        associated to that output.
        """
        self.step_through_runstep_creation("first_step_complete")

        content_check = self.step_E1_RS.log.content_checks.first()
        BadData(contentchecklog=content_check, missing_output=True).save()

        output = self.step_E1.transformation.outputs.first()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('Output "{}" of RunStep "{}" is missing; no data should be associated'
                                          .format(output, self.step_E1_RS)),
                                self.step_E1_RS.clean)

    def test_RunStep_kept_output_without_data(self):
        """
        A RunStep which keeps its outputs, but has an ExecRecordOut
        without data, is not clean.
        """
        self.step_through_runstep_creation("first_step_complete")
        ero = self.step_E1_RS.execrecord.execrecordouts.first()
        ero.dataset.dataset_file.delete(save=True)
        self.assertRaisesRegexp(ValidationError,
                                re.escape('ExecRecordOut "{}" of RunStep "{}" should reference existent data'
                                          .format(ero, self.step_E1_RS)),
                                self.step_E1_RS.clean)

    def test_RunStep_output_not_in_ExecRecord(self):
        """
        A RunStep with a Dataset not in its ExecRecord is not clean.
        """
        self.step_through_runstep_creation("first_step")
        self.triplet_dataset.file_source = self.step_E1_RS
        self.triplet_dataset.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" generated Dataset "{}" but it is not in its ExecRecord'
                                          .format(self.step_E1_RS, self.triplet_dataset)),
                                self.step_E1_RS.clean)

    def test_RunStep_subpipeline_complete(self):
        """
        A RunStep with a complete and clean child run is itself clean
        and complete.
        """
        self.step_through_runstep_creation("sub_pipeline")
        self.assertTrue(self.step_E2_RS.is_complete())
        self.assertIsNone(self.step_E2_RS.complete_clean())

    def test_RunStep_complete_clean_no_execrecord(self):
        """
        A RunStep with no ExecRecord is not complete.
        """
        self.step_through_runstep_creation("first_runstep")
        self.assertFalse(self.step_E1_RS.is_complete())
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" is not complete'.format(self.step_E1_RS)),
                                self.step_E1_RS.complete_clean)

    def test_RunStep_bad_clean_propagation(self):
        """
        A RunStep which is not clean, also should not pass
        complete_clean.
        """
        self.step_through_runstep_creation("first_runstep")
        self.doublet_dataset.file_source = self.step_E1_RS
        self.doublet_dataset.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" inputs not quenched; no data should have been generated'
                                          .format(self.step_E1_RS)),
                                self.step_E1_RS.complete_clean)

    def test_RunStep_keeps_output_true(self):
        """
        A RunStep with retained output.
        """
        self.step_through_runstep_creation("first_runstep")
        self.assertTrue(self.step_E1_RS.keeps_output(self.A1_out))

    def test_RunStep_keeps_output_false(self):
        """
        A RunStep with deleted output.
        """
        self.step_through_runstep_creation("first_runstep")
        self.step_E1.add_deletion(self.A1_out)
        self.assertFalse(self.step_E1_RS.keeps_output(self.A1_out))

    def test_RunStep_keeps_output_multiple_outputs(self):
        """
        A RunStep with several outputs, none deleted.
        """
        # This is copied from RunTests.step_through_run_creation.

        # Third RunStep associated.
        self.step_E3_RS = self.step_E3.pipelinestep_instances.create(run=self.pE_run)

        self.assertTrue(self.step_E3_RS.keeps_output(self.C1_out))
        self.assertTrue(self.step_E3_RS.keeps_output(self.C2_rawout))
        self.assertTrue(self.step_E3_RS.keeps_output(self.C3_rawout))

    def test_RunStep_keeps_output_multiple_outputs_some_deleted(self):
        """
        A RunStep with several outputs, some deleted.
        """
        # This is copied from RunTests.step_through_run_creation.

        # Third RunStep associated.
        self.step_E3_RS = self.step_E3.pipelinestep_instances.create(run=self.pE_run)
        self.step_E3.add_deletion(self.C1_out)
        self.step_E3.add_deletion(self.C3_rawout)

        self.assertFalse(self.step_E3_RS.keeps_output(self.C1_out))
        # The deletions shouldn't affect C2_rawout.
        self.assertTrue(self.step_E3_RS.keeps_output(self.C2_rawout))
        self.assertFalse(self.step_E3_RS.keeps_output(self.C3_rawout))


class RunComponentTooManyChecks(TestCase):
    """
    Tests that check clean() on the case where a RunComponent has too much datachecking.
    """
    fixtures = ["run_component_too_many_checks"]

    def setUp(self):
        # tools.create_word_reversal_environment(self)
        #
        # # Set up and run a Pipeline that throws away its intermediate data.
        # self.two_step_pl = tools.make_first_pipeline("Two-step pipeline",
        #                                              "Toy pipeline for testing data check cleaning of RunSteps.",
        #                                              self.user_bob)
        # tools.create_linear_pipeline(self.two_step_pl, [self.method_noop_wordbacks, self.method_noop_wordbacks],
        #                              "data", "samedata")
        # first_step = self.two_step_pl.steps.get(step_num=1)
        # first_step.add_deletion(self.method_noop_wordbacks.outputs.first())
        # first_step.save()
        #
        # self.two_step_sdbx = sandbox.execute.Sandbox(self.user_bob, self.two_step_pl, [self.dataset_wordbacks],
        #                                              groups_allowed=[everyone_group()])
        # self.two_step_sdbx.execute_pipeline()
        #
        # # The second one's second step will have to recover its first step.  (Its input cable is trivial
        # # and is able to reuse the input cable from the first Pipeline's second step.)
        # self.following_pl = tools.make_first_pipeline(
        #     "Pipeline that will follow the first",
        #     "Toy pipeline that will need to recover its first step when following the above.",
        #     self.user_bob
        # )
        # tools.create_linear_pipeline(self.following_pl, [self.method_noop_wordbacks, self.method_reverse],
        #                              "data", "reversed_data")
        # first_step = self.following_pl.steps.get(step_num=1)
        # first_step.add_deletion(self.method_noop_wordbacks.outputs.first())
        # first_step.save()
        #
        # self.following_sdbx = sandbox.execute.Sandbox(self.user_bob, self.following_pl, [self.dataset_wordbacks],
        #                                               groups_allowed=[everyone_group()])
        # self.following_sdbx.execute_pipeline()
        # second_step = self.following_sdbx.run.runsteps.get(pipelinestep__step_num=2)
        # assert(second_step.invoked_logs.count() == 2)
        install_fixture_files("run_component_too_many_checks")
        self.user_bob = User.objects.get(username="bob")

    def tearDown(self):
        restore_production_files()

    def test_RunStep_clean_too_many_integrity_checks(self):
        """RunStep should have <=1 integrity check for each output."""
        runstep = None
        for runstep in RunStep.objects.all():
            if (runstep.execrecord is not None and
                    runstep.execrecord.execrecordouts.count() > 0 and
                    runstep.has_log):
                break
        log = runstep.log
        sd = runstep.execrecord.execrecordouts.first().dataset
        log.integrity_checks.create(dataset=sd, user=self.user_bob)
        log.integrity_checks.create(dataset=sd, user=self.user_bob)
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" has multiple IntegrityCheckLogs for output '
                                          'Dataset {} of ExecLog "{}"'.format(runstep, sd, log)),
                                runstep.clean)

    def test_RunStep_clean_too_many_integrity_checks_invoked(self):
        """Invoked logs of RunStep should have <=1 integrity check for each output."""
        runstep = None
        for runstep in RunStep.objects.all():
            if (runstep.execrecord is not None and
                    runstep.execrecord.execrecordouts.count() > 0 and
                    runstep.has_log and
                    runstep.invoked_logs.count() > 1):
                break
        for log in runstep.invoked_logs.all():
            sd = log.record.execrecord.execrecordouts.first().dataset
            extra_check_1 = log.integrity_checks.create(dataset=sd, user=self.user_bob)
            extra_check_2 = log.integrity_checks.create(dataset=sd, user=self.user_bob)
            self.assertRaisesRegexp(ValidationError,
                                    re.escape('RunStep "{}" has multiple IntegrityCheckLogs for output '
                                              'Dataset {} of ExecLog "{}"'.format(runstep, sd, log)),
                                    runstep.clean)
            extra_check_1.delete()
            extra_check_2.delete()

    def test_RunStep_clean_too_many_content_checks(self):
        """RunStep should have <=1 content check for each output."""
        runstep = None
        for runstep in RunStep.objects.all():
            if (runstep.execrecord is not None and
                    runstep.execrecord.execrecordouts.count() > 0 and
                    runstep.has_log):
                break
        log = runstep.log
        sd = runstep.execrecord.execrecordouts.first().dataset
        log.content_checks.create(dataset=sd, user=self.user_bob)
        log.content_checks.create(dataset=sd, user=self.user_bob)
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" has multiple ContentCheckLogs for output '
                                          'Dataset {} of ExecLog "{}"'.format(runstep, sd, log)),
                                runstep.clean)

    def test_RunStep_clean_too_many_content_checks_invoked(self):
        """RunStep should have <=1 content check for each output."""
        runstep = None
        for runstep in RunStep.objects.all():
            if (runstep.execrecord is not None and
                    runstep.execrecord.execrecordouts.count() > 0 and
                    runstep.has_log and
                    runstep.invoked_logs.count() > 1):
                break
        for log in runstep.invoked_logs.all():
            sd = runstep.execrecord.execrecordouts.first().dataset
            extra_check_1 = log.content_checks.create(dataset=sd, user=self.user_bob)
            extra_check_2 = log.content_checks.create(dataset=sd, user=self.user_bob)
            self.assertRaisesRegexp(ValidationError,
                                    re.escape('RunStep "{}" has multiple ContentCheckLogs for output '
                                              'Dataset {} of ExecLog "{}"'.format(runstep, sd, log)),
                                    runstep.clean)
            extra_check_1.delete()
            extra_check_2.delete()


class RunTests(ArchiveTestCase):

    def test_Run_is_subrun_True(self):
        """
        A Run which has a parent RunStep should register as being a subrun.
        """
        self.step_through_run_creation("sub_pipeline")
        self.assertTrue(self.pD_run.is_subrun())

    def test_Run_is_subrun_False(self):
        """
        A Run which has no parent RunStep should not be a subrun.
        """
        self.step_through_run_creation("empty_runs")
        self.assertFalse(self.pE_run.is_subrun())

    def test_Run_clean_not_started(self):
        """
        A Run which has been created, but nothing in it has executed
        yet, is clean.
        """
        self.step_through_run_creation("empty_runs")
        self.assertIsNone(self.pE_run.clean())

    def test_Run_clean_inconsistent_parent_runstep(self):
        """
        A sub-Run whose parent RunStep does not match its Pipeline is
        not clean.
        """
        self.step_through_run_creation("second_step")
        self.pD_run.parent_runstep = self.step_E1_RS
        self.assertRaisesRegexp(ValidationError,
                                re.escape('Pipeline of Run "{}" is not consistent with its parent RunStep'
                                          .format(self.pD_run)),
                                self.pD_run.clean)

    def test_Run_clean_consistent_parent_runstep(self):
        """
        A sub-Run whose parent RunStep matches its Pipeline is clean.
        """
        self.step_through_run_creation("sub_pipeline")
        self.assertIsNone(self.pD_run.clean())

    def test_Run_clean_first_runstep_incomplete(self):
        """
        A Run whose first RunStep is associated and incomplete, and
        nothing else is, is clean.
        """
        self.step_through_run_creation("first_step")
        self.assertIsNone(self.pE_run.clean())

    def test_Run_clean_first_runstep_complete(self):
        """
        A Run whose first RunStep is associated and complete, and
        nothing else is, is clean.
        """
        self.step_through_run_creation("first_step_complete")
        self.assertIsNone(self.pE_run.clean())

    def test_Run_clean_second_runstep_incomplete(self):
        """
        A Run whose second RunStep is associated and incomplete, and
        whose first RunStep is complete, is clean.
        """
        self.step_through_run_creation("second_step")
        self.assertIsNone(self.pE_run.clean())

    def test_Run_clean_previous_incomplete_runstep(self):
        """
        A Run whose second RunStep is associated, but whose first step
        is not complete, is clean.

        TODO: when we add the check to RunStep.clean that the
        Datasets feeding it are present, this will fail by
        propagation. The test setup will need to be modified to put in
        place the inputs to steps 1 and 2.
        """
        self.step_through_run_creation("second_step")
        self.step_E1_RS.execrecord = None
        self.step_E1_RS.reused = None
        self.step_E1_RS.log.delete()
        self.step_E1_RS.save()
        self.assertIsNone(self.pE_run.clean())

    def test_Run_clean_badly_numbered_steps(self):
        """
        A Run with steps not consecutively numbered from 1 to n is not
        clean.
        """
        self.step_through_run_creation("second_step")
        self.step_E1_RS.delete()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunSteps of Run "{}" are not consecutively numbered starting from 1'
                                          .format(self.pE_run)),
                                self.pE_run.clean)

    def test_Run_clean_properly_numbered_steps(self):
        """
        A Run with steps consecutively numbered from 1 to n is clean.
        """
        self.step_through_run_creation("second_step")
        self.assertIsNone(self.pE_run.clean())

    def test_Run_bad_first_RunStep_propagation(self):
        """
        A Run whose first RunStep is not clean, and no other RunSteps
        are associated, is not clean.
        """
        self.step_through_run_creation("first_step_complete")
        self.step_E1_RS.reused = None
        self.step_E1_RS.RSICs.first().delete()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" inputs not quenched; reused and execrecord should not be set'
                                          .format(self.step_E1_RS)),
                                self.pE_run.clean)

    def test_Run_bad_second_RunStep_propagation(self):
        """
        A Run whose first RunStep is clean, but whose second RunStep
        is not, is not clean.
        """
        self.step_through_run_creation("second_step")
        self.step_E2_RS.reused = True
        self.step_E2_RS.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunStep "{}" represents a sub-pipeline so reused should not be set'
                                          .format(self.step_E2_RS)),
                                self.pE_run.clean)

    def test_Run_clean_RunOutputCable_no_RunStep(self):
        """
        A Run with a RunOutputCable from a non-associated RunStep is not
        clean.
        """
        self.step_through_run_creation("sub_pipeline")
        self.E31_42.poc_instances.create(run=self.pE_run)
        self.assertRaisesRegexp(ValidationError,
                                re.escape('Run "{}" has a RunOutputCable from step {}, but no corresponding RunStep'
                                          .format(self.pE_run, 3)),
                                self.pE_run.clean)

    def test_Run_clean_outcable_incomplete_last_RunStep(self):
        """
        A Run with a RunOutputCable, and a final RunStep which is clean
        but not complete, is clean.
        """
        self.step_through_run_creation("third_step")
        self.pE.outcables.first().poc_instances.create(run=self.pE_run)
        self.assertIsNone(self.pE_run.clean())

    def test_Run_clean_two_complete_RunSteps(self):
        """
        A three step Run with two complete RunSteps, but no third
        RunStep, is clean.
        """
        self.step_through_run_creation("sub_pipeline")
        self.assertIsNone(self.pE_run.clean())

    def test_Run_clean_all_RunSteps_complete_no_outcables(self):
        """
        A Run which has all its steps complete, but no RunOutputCables,
        is clean.
        """
        self.step_through_run_creation("third_step_complete")
        self.assertIsNone(self.pE_run.clean())

    def test_Run_clean_bad_RunOutputCable_propagation(self):
        """
        A Run with an bad RunOutputCable is not clean (not quite the
        same as the old test case, I can't make it work).
        """
        self.step_through_run_creation("outcables_done")
        cable1 = self.pE_run.runoutputcables.first()
        cable1.reused = None
        cable1.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('{} "{}" has not decided whether or not to reuse an ExecRecord; '
                                          'no log should have been generated'.format("RunOutputCable", cable1)),
                                self.pE_run.clean)

    def test_Run_clean_one_complete_RunOutputCable(self):
        """
        A Run with one good RunOutputCable, and no others, is clean.
        """
        self.step_through_run_creation("first_outcable")
        self.assertIsNone(self.pE_run.clean())

    def test_Run_clean_all_complete_RunOutputCables(self):
        """
        A Run with all RunOutputCables complete and clean, is clean.
        """
        self.step_through_run_creation("outcables_done")
        self.assertIsNone(self.pE_run.clean())
        self.assertTrue(self.pE_run.is_complete())
        self.assertIsNone(self.pE_run.complete_clean())


class RunSICTests(ArchiveTestCase):

    def test_RunSIC_clean_wrong_pipelinestep(self):
        """
        A RunSIC whose PipelineStepInputCable does not belong to its
        RunStep's PipelineStep, is not clean.
        """
        self.step_through_runsic_creation("runstep")
        rsic = self.E01_21.psic_instances.create(runstep=self.step_E3_RS)
        self.assertRaisesRegexp(ValidationError,
                                re.escape('PSIC "{}" does not belong to PipelineStep "{}"'
                                          .format(self.E01_21, self.step_E3)),
                                rsic.clean)

    def test_RunSIC_clean_unset_reused(self):
        """
        A RunSIC whose PipelineStepInputCable and RunStep are
        consistent, but which has not set reused yet, is clean.
        """
        self.step_through_runsic_creation("rsic_created")
        self.assertIsNone(self.E11_32_RSIC.clean())

    def test_RunSIC_clean_unset_reused_with_data(self):
        """
        A RunSIC which has not decided whether to reuse an ExecRecord,
        but which has associated data, is not clean.
        """
        self.step_through_runsic_creation("rsic_created")
        self.doublet_dataset.file_source = self.E11_32_RSIC
        self.doublet_dataset.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunSIC "{}" has not decided whether or not to reuse an ExecRecord; '
                                          'no Datasets should be associated'.format(self.E11_32_RSIC)),
                                self.E11_32_RSIC.clean)

    def test_RunSIC_clean_unset_reused_with_execrecord(self):
        """
        A RunSIC which has not decided whether to reuse an ExecRecord,
        but which has one associated, is not clean.
        """
        self.step_through_runsic_creation("rsic_created")
        self.E11_32_RSIC.reused = None

        other_run = self.pE.pipeline_instances.create(user=self.myUser)
        other_run.grant_everyone_access()
        other_runstep = self.step_E3.pipelinestep_instances.create(run=other_run)
        other_rsic = self.E11_32.psic_instances.create(runstep=other_runstep)
        self.make_complete_non_reused(other_rsic, [self.doublet_dataset], [self.C2_in_dataset])
        self.E11_32_RSIC.execrecord = other_rsic.execrecord

        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunSIC "{}" has not decided whether or not to reuse an ExecRecord; '
                                          'execrecord should not be set yet'.format(self.E11_32_RSIC)),
                                self.E11_32_RSIC.clean)

    def test_RunSIC_clean_reused_with_data(self):
        """
        A RunSIC which has reused an existing ExecRecord, but has an
        associated Dataset, is not clean.
        """
        self.step_through_runsic_creation("rsic_completed")
        self.E11_32_RSIC.reused = True
        self.doublet_dataset.file_source = self.E11_32_RSIC
        self.doublet_dataset.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunSIC "{}" reused an ExecRecord and should not have generated any Datasets'
                                          .format(self.E11_32_RSIC)),
                                self.E11_32_RSIC.clean)

    def test_RunSIC_clean_reused_bad_execrecord(self):
        """
        A RunSIC whose ExecRecord is not clean, is not itself clean.
        """
        self.step_through_runsic_creation("rsic_created")
        other_run = self.pE.pipeline_instances.create(user=self.myUser)
        other_run.grant_everyone_access()
        other_runstep = self.step_E3.pipelinestep_instances.create(run=other_run)
        self.make_complete_reused(self.E11_32_RSIC, [self.doublet_dataset], [self.C2_in_dataset], other_runstep)

        ero = self.E11_32_RSIC.execrecord.execrecordouts.first()
        self.C1_in.execrecordouts_referencing.add(ero)
        self.assertRaisesRegexp(
            ValidationError,
            re.escape('CDT of Dataset "{}" is not a restriction of the CDT of the fed TransformationInput "{}"'
                      .format(ero.dataset, ero.generic_output.definite)),
            self.E11_32_RSIC.clean)

    def test_RunSIC_clean_reused_incompatible_execrecord(self):
        """
        A RunSIC which is reusing an ExecRecord for an incompatible
        PipelineStepInputCable is not clean.
        """
        self.step_through_runsic_creation("rsic_created")

        # Create an incompatible RunSIC.
        runsic = self.E21_31.psic_instances.create(runstep=self.step_E3_RS)
        self.make_complete_non_reused(runsic, [self.C1_in_dataset], [self.C1_in_dataset])

        run = self.pE.pipeline_instances.create(user=self.myUser)
        run.grant_everyone_access()
        runstep = self.step_E3.pipelinestep_instances.create(run=run)
        self.make_complete_reused(self.E11_32_RSIC, [self.doublet_dataset], [self.C2_in_dataset], runstep)

        self.E11_32_RSIC.execrecord = runsic.execrecord
        self.assertRaisesRegexp(
            ValidationError,
            re.escape('PipelineStepInputCable of RunSIC "{}" is incompatible with the cable of its ExecRecord'.format(
                self.E11_32_RSIC)),
            self.E11_32_RSIC.clean)

    def test_RunSIC_clean_reused_execrecord_wrong_object(self):
        """
        A RunSIC's ExecRecord must be for a PipelineStepInputCable and
        not some other pipeline component (reused case).
        """
        self.step_through_runsic_creation("rsic_created")
        other_run = self.pE.pipeline_instances.create(user=self.myUser)
        other_run.grant_everyone_access()
        other_runstep = self.step_E3.pipelinestep_instances.create(run=other_run)

        self.make_complete_reused(self.E11_32_RSIC, [self.doublet_dataset], [self.C2_in_dataset], other_runstep)
        self.E21_31_RSIC = self.E21_31.psic_instances.create(runstep=self.step_E3_RS)
        self.make_complete_non_reused(self.E21_31_RSIC, [self.C1_in_dataset], [self.C1_in_dataset])
        self.make_complete_non_reused(self.step_E3_RS,
                                      [self.C1_in_dataset, self.C2_in_dataset],
                                      [self.C1_out_dataset, self.C2_out_dataset, self.C3_out_dataset])

        self.E11_32_RSIC.execrecord = self.step_E3_RS.execrecord
        self.assertRaisesRegexp(ValidationError,
                                re.escape('ExecRecord of RunSIC "{}" does not represent a PipelineCable'
                                          .format(self.E11_32_RSIC)),
                                self.E11_32_RSIC.clean)
        # Check of propagation:
        self.assertRaisesRegexp(ValidationError,
                                re.escape('ExecRecord of RunSIC "{}" does not represent a PipelineCable'
                                          .format(self.E11_32_RSIC)),
                                self.E11_32_RSIC.complete_clean)

    def test_RunSIC_clean_reused_psic_keeps_output_no_data(self):
        """
        A RunSIC reusing an ExecRecord, whose PipelineStepInputCable
        keeps its output, should have data in its ExecRecordOut.
        """
        self.step_through_runsic_creation("rsic_created")
        run = self.pE.pipeline_instances.create(user=self.myUser)
        run.grant_everyone_access()
        runstep = self.step_E3.pipelinestep_instances.create(run=run)
        self.E11_32.keep_output = True
        self.E11_32.save()
        self.make_complete_reused(self.E11_32_RSIC, [self.doublet_dataset], [self.C2_in_dataset], runstep)
        ero = self.E11_32_RSIC.execrecord.execrecordouts.first()

        self.assertTrue(self.E11_32_RSIC.keeps_output())
        # Removed May 12, 2014: a reused RunSIC has no log.
        # self.assertListEqual(self.E11_32_RSIC.log.missing_outputs(), [])
        self.assertFalse(ero.has_data())
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunSIC "{}" keeps its output; ExecRecordOut "{}" should reference existent '
                                          'data'.format(self.E11_32_RSIC, ero)),
                                self.E11_32_RSIC.clean)

    def test_RunSIC_clean_reused_psic_keeps_output_with_data(self):
        """
        A RunSIC reusing an ExecRecord, whose PipelineStepInputCable
        keeps its output, should have data in its ExecRecordOut.
        """
        self.step_through_runsic_creation("rsic_created")

        # Make another RSIC which is reused by E11_32_RSIC.
        other_run = self.pE.pipeline_instances.create(user=self.myUser)
        other_run.grant_everyone_access()
        other_run.save()
        other_RS = self.step_E3.pipelinestep_instances.create(run=other_run)

        self.make_complete_reused(self.E11_32_RSIC, [self.doublet_dataset], [self.C2_in_dataset], other_RS)

        self.assertIsNone(self.E11_32_RSIC.clean())

    def test_RunSIC_clean_reused_complete_RSIC(self):
        """
        A RunSIC reusing an ExecRecord, whose PipelineStepInputCable
        keeps its output, having data in its ExecRecordOut, is complete
        and clean.
        """
        self.step_through_runsic_creation("rsic_created")
        other_run = self.pE.pipeline_instances.create(user=self.myUser)
        other_run.grant_everyone_access()
        other_runstep = self.step_E3.pipelinestep_instances.create(run=other_run)

        self.make_complete_reused(self.E11_32_RSIC, [self.doublet_dataset], [self.E11_32_output_dataset],
                                  other_runstep)
        self.E11_32.keep_output = True
        ero = self.E11_32_RSIC.execrecord.execrecordouts.first()

        self.assertTrue(self.E11_32_RSIC.is_complete())
        self.assertIsNone(self.E11_32_RSIC.complete_clean())

    def test_RunSIC_complete_reused_no_execrecord(self):
        """
        A RunSIC reusing an ExecRecord, which doesn't have one
        associated, is not complete.
        """
        self.step_through_runsic_creation("rsic_created")

        other_run = self.pE.pipeline_instances.create(user=self.myUser)
        other_run.grant_everyone_access()
        other_RS = self.step_E3.pipelinestep_instances.create(run=other_run)
        self.make_complete_reused(self.E11_32_RSIC, [self.doublet_dataset], [self.C2_in_dataset],
                                  other_RS)

        self.E11_32.keep_output = True
        self.E11_32_RSIC.execrecord = None

        self.assertFalse(self.E11_32_RSIC.is_complete())
        self.assertRaisesRegexp(ValidationError,
                                re.escape('{} "{}" is not complete'.format("RunSIC", self.E11_32_RSIC)),
                                self.E11_32_RSIC.complete_clean)

    def test_RunSIC_clean_not_reused_no_execrecord(self):
        """
        A RunSIC which has decided not to reuse an ExecRecord, but
        which doesn't have one yet, is clean.
        """
        self.step_through_runsic_creation("rsic_created")
        self.E11_32_RSIC.reused = False
        self.assertIsNone(self.E11_32_RSIC.clean())

    def test_RunSIC_clean_not_reused_bad_execrecord(self):
        """
        A RunSIC which is not reusing an ExecRecord, but has a bad
        ExecRecord associated, is not clean.
        """
        self.step_through_runsic_creation("rsic_completed")
        self.E11_32_RSIC.reused = False
        ero = self.E11_32_RSIC.execrecord.execrecordouts.first()
        self.C1_in.execrecordouts_referencing.add(ero)
        self.assertRaisesRegexp(
            ValidationError,
            re.escape(
                'CDT of Dataset "{}" is not a restriction of the '
                'CDT of the fed TransformationInput "{}"'.format(
                    ero.dataset, ero.generic_output.definite)),
            self.E11_32_RSIC.clean)

    def test_RunSIC_clean_not_reused_incompatible_execrecord(self):
        """
        A RunSIC which is not reusing an ExecRecord, and has an
        ExecRecord for an incompatible PipelineStepInputCable, is
        not clean.
        """
        self.step_through_runsic_creation("rsic_complete")
        self.E11_32_RSIC.reused = False

        # Create an incompatible RunSIC.
        runstep = self.step_E2.pipelinestep_instances.create(run=self.pE_run)
        runsic = self.E02_22.psic_instances.create(runstep=runstep)
        self.make_complete_non_reused(runsic, [self.singlet_dataset], [self.singlet_dataset])
        self.E11_32_RSIC.execrecord = runsic.execrecord
        self.assertRaisesRegexp(
            ValidationError,
            re.escape('PipelineStepInputCable of RunSIC "{}" is incompatible with the cable of its ExecRecord'.format(
                self.E11_32_RSIC)),
            self.E11_32_RSIC.clean)

    def test_RunSIC_clean_not_reused_execrecord_wrong_object(self):
        """
        A RunSIC's ExecRecord must be for a PipelineStepInputCable and
        not some other pipeline component (non-reused case).
        """
        self.step_through_runsic_creation("runstep_completed")
        self.E11_32_RSIC.reused = False

        self.E11_32_RSIC.execrecord = self.step_E3_RS.execrecord
        self.assertRaisesRegexp(ValidationError,
                                re.escape('ExecRecord of RunSIC "{}" does not represent a PipelineCable'
                                          .format(self.E11_32_RSIC)),
                                self.E11_32_RSIC.clean)
        # Check of propagation:
        self.assertRaisesRegexp(ValidationError,
                                re.escape('ExecRecord of RunSIC "{}" does not represent a PipelineCable'
                                          .format(self.E11_32_RSIC)),
                                self.E11_32_RSIC.complete_clean)

    def test_RunSIC_clean_not_reused_psic_discards_output_no_data(self):
        """
        A RunSIC not reusing an ExecRecord, whose PipelineStepInputCable
        does not keep its output, should not have data in its ExecRecordOut.
        """
        self.step_through_runsic_creation("rsic_completed")
        self.E11_32_RSIC.reused = False
        self.assertIsNone(self.E11_32_RSIC.clean())

    def test_RunSIC_clean_not_reused_psic_discards_output_with_data(self):
        """
        A RunSIC not reusing an ExecRecord, whose PipelineStepInputCable
        does not keep its output, should not have data in its ExecRecordOut.
        """
        self.step_through_runsic_creation("rsic_completed")
        self.E11_32_RSIC.reused = False
        self.E11_32_RSIC.outputs.add(self.E11_32_output_dataset)
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunSIC "{}" does not keep its output but a dataset was registered'
                                          .format(self.E11_32_RSIC)),
                                self.E11_32_RSIC.clean)

    def test_RunSIC_clean_not_reused_psic_keeps_output_no_data(self):
        """
        A RunSIC not reusing an ExecRecord, whose PipelineStepInputCable
        keeps its output, should have data in its ExecRecordOut.
        """
        self.step_through_runsic_creation("rsic_completed")
        self.E11_32_RSIC.reused = False
        self.E11_32.keep_output = True
        self.E11_32_RSIC.save()
        ero = self.E11_32_RSIC.execrecord.execrecordouts.first()
        self.assertRaisesRegexp(
            ValidationError,
            re.escape('RunSIC "{}" keeps its output; ExecRecordOut "{}" should reference existent '
                      'data'.format(self.E11_32_RSIC, ero)),
            self.E11_32_RSIC.clean
        )

    def test_RunSIC_clean_not_reused_psic_keeps_output_with_data(self):
        """
        A RunSIC not reusing an ExecRecord, whose PipelineStepInputCable
        keeps its output, should have data in its ExecRecordOut, and it should
        also be among its own outputs.
        """
        self.step_through_runsic_creation("rsic_completed")
        self.E11_32_RSIC.reused = False
        self.E11_32.keep_output = True
        ero = self.E11_32_RSIC.execrecord.execrecordouts.first()

        fake_data = "x,y\nHello,World"
        ero.dataset.dataset_file.save("FakeData.csv", ContentFile(fake_data))
        ero.dataset.MD5_checksum = ero.dataset.compute_md5()

        ero.dataset.MD5_checksum = ero.dataset.compute_md5()
        ero.dataset.save()
        self.E11_32_RSIC.outputs.add(ero.dataset)
        self.assertIsNone(self.E11_32_RSIC.clean())

    def test_RunSIC_clean_not_reused_nontrivial_no_data(self):
        """
        A RunSIC which is not reused, non-trivial, and is for a
        PipelineStepInputCable which keeps its output, must have
        produced data.
        """
        self.step_through_runsic_creation("rsic_completed")
        self.E11_32_RSIC.reused = False
        self.E11_32.keep_output = True
        ero = self.E11_32_RSIC.execrecord.execrecordouts.first()
        ero.dataset = self.E11_32_output_dataset
        ero.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunSIC "{}" was not reused, trivial, or deleted; it should have produced '
                                          'data'.format(self.E11_32_RSIC)),
                                self.E11_32_RSIC.clean)

    def test_RunSIC_clean_not_reused_nontrivial_wrong_data(self):
        """
        A RunSIC which is nontrivial, is not reusing an ExecRecord, and
        is for a PipelineStepInputCable which keeps its output, must
        have produced the same Dataset as is recorded in its
        ExecRecordOut.
        """
        self.step_through_runsic_creation("rsic_completed")
        self.E11_32_RSIC.reused = False
        self.E11_32.keep_output = True

        # Associate different datasets to RSIC and associated ERO.
        # First, the interloper.
        self.doublet_dataset.file_source = self.E11_32_RSIC
        self.doublet_dataset.save()

        # Swap out the proper output Dataset for one that retains its data, since
        # we've made the PSIC keep its output.
        ero = self.E11_32_RSIC.execrecord.execrecordouts.first()
        orig_ero_dataset = ero.dataset
        orig_ero_dataset.file_source = None
        orig_ero_dataset.save()

        ero.dataset = self.E11_32_output_dataset
        ero.dataset.file_source = None
        ero.dataset.save()
        ero.save()

        self.E11_32_output_dataset = ero.dataset
        self.E11_32_output_dataset.save()

        self.assertRaisesRegexp(ValidationError,
                                re.escape('Dataset "{}" was produced by RunSIC "{}" but is not in an ERO of '
                                          'ExecRecord "{}"'.format(self.doublet_dataset, self.E11_32_RSIC,
                                                                   self.E11_32_RSIC.execrecord)),
                                self.E11_32_RSIC.clean)

    def test_RunSIC_complete_not_reused(self):
        """
        A RunSIC which is nontrivial, is not reusing an ExecRecord, and
        is for a PipelineStepInputCable which keeps its output, which
        has produced the same Dataset as is recorded in its
        ExecRecordOut, is clean and complete.
        """
        self.step_through_runsic_creation("rsic_completed")

        # Swap the output of E11_32_RSIC (i.e. C2_in_dataset) for E11_32_output_dataset,
        # which has data.
        self.E11_32_RSIC.reused = False
        self.E11_32.keep_output = True
        self.E11_32_output_dataset.file_source = self.E11_32_RSIC
        self.E11_32_output_dataset.save()

        ero_to_change = self.E11_32_RSIC.execrecord.execrecordouts.first()
        ero_to_change.dataset = self.E11_32_output_dataset
        ero_to_change.save()

        # Point the CCL of E11_32_output_dataset to the ExecLog of E11_32_RSIC.
        ccl = self.E11_32_output_dataset.content_checks.first()
        ccl.execlog = self.E11_32_RSIC.log
        ccl.save()

        self.assertTrue(self.E11_32_RSIC.is_complete())
        self.assertIsNone(self.E11_32_RSIC.complete_clean())

    def test_RunSIC_incomplete_not_reused(self):
        """
        A RunSIC which is not reusing an ExecRecord, but which does not
        have an ExecRecord, is not complete.
        """
        self.step_through_runsic_creation("rsic_completed")
        self.E11_32_RSIC.reused = False

        # May 14, 2014: we now make it incomplete by removing the CCL, not by
        # removing the ExecRecord.
        C2_ccl = self.C2_in_dataset.content_checks.first()
        C2_ccl.execlog = None
        C2_ccl.save()

        self.assertFalse(self.E11_32_RSIC.is_complete())
        self.assertRaisesRegexp(ValidationError,
                                re.escape('{} "{}" is not complete'.format("RunSIC", self.E11_32_RSIC)),
                                self.E11_32_RSIC.complete_clean)

    ####
    # keeps_output tests added March 26, 2014 -- RL.
    def test_RunSIC_keeps_output_trivial(self):
        """
        A trivial RunSIC should have keeps_output() return False regardless of the keep_output setting.
        """
        self.step_through_runsic_creation("runstep_completed")
        self.E21_31.keep_output = True
        self.assertFalse(self.E11_32_RSIC.keeps_output())
        self.E21_31.keep_output = False
        self.assertFalse(self.E11_32_RSIC.keeps_output())

    def test_RunSIC_keeps_output_true(self):
        """
        A RunSIC that keeps its output should have keeps_output() return True.
        """
        self.step_through_runsic_creation("rsic_completed")
        self.E11_32.keep_output = True
        self.assertTrue(self.E11_32_RSIC.keeps_output())

    def test_RunSIC_keeps_output_false(self):
        """
        A RunSIC that discards its output should have keeps_output() return False.
        """
        self.step_through_runsic_creation("rsic_completed")
        self.E11_32.keep_output = False
        self.assertFalse(self.E11_32_RSIC.keeps_output())


class RunOutputCableTests(ArchiveTestCase):

    def test_ROC_clean_correct_parent_run(self):
        """PipelineOutputCable belongs to parent Run's Pipeline.

        A RunOutputCable's PipelineOutputCable must belong to the
        Pipeline of its parent Run.
        """
        self.step_through_roc_creation("roc_created")
        self.assertIsNone(self.E31_42_ROC.clean())

    def test_ROC_clean_wrong_parent_run(self):
        """PipelineOutputCable is for Pipeline not of parent Run.

        A RunOutputCable's PipelineOutputCable must belong to the
        Pipeline of its parent Run.
        """
        self.step_through_roc_creation("roc_created")
        pD_run = self.pD.pipeline_instances.create(user=self.myUser)
        self.E31_42_ROC.run = pD_run
        self.assertRaisesRegexp(ValidationError,
                                re.escape('POC "{}" does not belong to Pipeline "{}"'.format(self.E31_42, self.pD)),
                                self.E31_42_ROC.clean)

    def test_ROC_clean_unset_reused_with_data(self):
        """Reused is not set but data is associated.

        A RunOutputCable which has not decided whether to reuse an
        ExecRecord should not have generated any data.
        """
        self.step_through_roc_creation("roc_created")
        self.C1_out_dataset.file_source = self.E31_42_ROC
        self.C1_out_dataset.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunOutputCable "{}" has not decided whether or not to reuse an ExecRecord; '
                                          'no Datasets should be associated'.format(self.E31_42_ROC)),
                                self.E31_42_ROC.clean)

    def test_ROC_clean_unset_reused_with_execrecord(self):
        """Reused is not set but an ExecRecord is associated.

        A RunOutputCable which has not decided whether to reuse an
        ExecRecord should not have one associated.
        """
        self.step_through_roc_creation("roc_created")

        # Create a compatible ExecRecord to associate.
        other_run = self.pE.pipeline_instances.create(user=self.myUser)
        other_run.grant_everyone_access()
        other_roc = self.E31_42.poc_instances.create(run=other_run)
        self.make_complete_non_reused(other_roc, [self.C1_out_dataset], [self.C1_out_dataset])
        self.E31_42_ROC.execrecord = other_roc.execrecord

        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunOutputCable "{}" has not decided whether or not to reuse an ExecRecord; '
                                          'execrecord should not be set yet'.format(self.E31_42_ROC)),
                                self.E31_42_ROC.clean)

    def test_ROC_clean_reused_with_data(self):
        """Reused is True but data is associated.

        A RunOutputCable which is reusing an ExecRecord should not have
        generated any Datasets.
        """
        self.step_through_roc_creation("roc_created")
        self.E31_42_ROC.reused = True
        self.singlet_dataset.file_source = self.E31_42_ROC
        self.singlet_dataset.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunOutputCable "{}" reused an ExecRecord and should not have generated any '
                                          'Datasets'.format(self.E31_42_ROC)),
                                self.E31_42_ROC.clean)

    def test_ROC_clean_not_reused_trivial_no_data(self):
        """Reused is False, cable is trivial, no data associated.

        A RunOutputCable which is not reusing an ExecRecord, but which
        is trivial, should not have generated any Datasets.
        """
        self.step_through_roc_creation("roc_created")
        self.E31_42_ROC.reused = False
        self.assertIsNone(self.E31_42_ROC.clean())

    def test_ROC_clean_not_reused_trivial_with_data(self):
        """Reused is False, cable is trivial, data associated.

        A RunOutputCable which is not reusing an ExecRecord, but which
        is trivial, should not have generated any Datasets.
        """
        self.step_through_roc_creation("roc_created")
        self.E31_42_ROC.reused = False

        cable_log = ExecLog(record=self.E31_42_ROC, invoking_record=self.E31_42_ROC,
                            start_time=timezone.now(), end_time=timezone.now())
        cable_log.save()

        self.singlet_dataset.file_source = self.E31_42_ROC
        self.singlet_dataset.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunOutputCable "{}" is trivial and should not have generated any Datasets'
                                          .format(self.E31_42_ROC)),
                                self.E31_42_ROC.clean)

    def test_ROC_clean_not_reused_nontrivial_with_data(self):
        """Non-trivial cable, good data attached.

        A RunOutputCable which is non-trivial, is not reusing an
        ExecRecord, and has data attached, is clean.
        """
        self.step_through_roc_creation("custom_roc_completed")
        self.assertIsNone(self.E21_41_ROC.clean())

    def test_ROC_clean_not_reused_nontrivial_multiple_datasets(self):
        """Non-trivial cable, multiple datasets attached.

        A RunOutputCable which is non-trivial, is not reusing an
        ExecRecord, should generate at most one Dataset.
        """
        self.step_through_roc_creation("custom_roc_completed")
        self.E1_out_dataset.file_source = self.E21_41_ROC
        self.E1_out_dataset.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunOutputCable "{}" should generate at most one Dataset'
                                          .format(self.E21_41_ROC)),
                                self.E21_41_ROC.clean)

    def test_ROC_clean_not_reused_nontrivial_bad_data(self):
        """Propagation: bad data attached to RunOutputCable.

        A RunOutputCable which produced a bad Dataset is not clean.
        """
        self.step_through_roc_creation("custom_roc_completed")
        old_checksum = self.doublet_dataset.MD5_checksum
        self.doublet_dataset.MD5_checksum = "foo"
        self.doublet_dataset.save()

        self.assertFalse(self.E21_41_ROC.reused)
        self.assertFalse(self.E21_41_ROC.component.is_trivial())
        self.assertRaisesRegexp(ValidationError,
                                re.escape('File integrity of "{}" lost. Current checksum "{}" does not equal expected '
                                          'checksum "{}"'.format(self.doublet_dataset, old_checksum, "foo")),
                                self.E21_41_ROC.clean)

    def test_ROC_clean_not_reused_incomplete_execrecord(self):
        """Propagation: ExecRecord is not complete and clean.

        A trivial RunOutputCable which has an ExecRecord without the
        appropriate ExecRecordIns is not clean.
        """
        self.step_through_roc_creation("trivial_roc_completed")
        self.E31_42_ROC.execrecord.execrecordins.first().delete()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('Input to ExecRecord "{}" is not quenched'
                                          .format(self.E31_42_ROC.execrecord)),
                                self.E31_42_ROC.clean)

    def test_ROC_clean_incompatible_execrecord(self):
        """RunOutputCable has ExecRecord for wrong PipelineOuputCable.

        The ExecRecord of a RunOutputCable must correspond to the same
        PipelineOutputCable that the RunOutputCable corresponds to.
        """
        self.step_through_roc_creation("custom_roc_completed")
        self.E31_42_ROC.execrecord = self.E21_41_ROC.execrecord
        err_msg = 'PipelineOutputCable of RunOutputCable "{}" is incompatible with the cable of its ExecRecord'
        self.assertRaisesRegexp(ValidationError, re.escape(err_msg.format(self.E31_42_ROC)), self.E31_42_ROC.clean)

    def test_ROC_clean_wrong_object_execrecord(self):
        """RunOutputCable has ExecRecord for PipelineStep.

        A RunOutputCable's ExecRecord must be for a PipelineOutputCable.
        """
        self.step_through_roc_creation("custom_roc_completed")
        runstep = self.step_E1.pipelinestep_instances.create(run=self.pE_run)
        self.complete_RSICs(runstep, [self.raw_dataset], [self.raw_dataset])
        self.make_complete_non_reused(runstep, [self.raw_dataset], [self.doublet_dataset])
        self.E31_42_ROC.execrecord = runstep.execrecord
        self.assertRaisesRegexp(ValidationError,
                                re.escape('ExecRecord of RunOutputCable "{}" does not represent a PipelineCable'
                                          .format(self.E31_42_ROC)),
                                self.E31_42_ROC.clean)

    def test_ROC_clean_deleted_output_no_data(self):
        """RunOutputCable with output marked for deletion has no data.

        A RunOutputCable from a subrun, where the PipelineStep has
        marked the relevant output for deletion, should not have
        any associated Datasets."""
        self.step_through_roc_creation("subrun")
        self.step_E2.outputs_to_delete.add(self.pD.outputs.get(dataset_name="D1_out"))
        self.assertIsNone(self.D11_21_ROC.clean())

    def test_ROC_clean_deleted_output_with_data(self):
        """RunOutputCable with output marked for deletion has data.

        A RunOutputCable from a subrun, where the PipelineStep has
        marked the relevant output for deletion, should not have
        any associated Datasets."""

        self.step_through_roc_creation("subrun_complete")
        self.step_E2.outputs_to_delete.add(self.pD.outputs.get(dataset_name="D1_out"))

        self.assertFalse(self.D11_21_ROC.keeps_output())
        self.assertTrue(self.D11_21_ROC.outputs.exists())
        self.assertRaisesRegexp(ValidationError,
                                re.escape('{} "{}" does not keep its output but a dataset was registered'
                                          .format("RunOutputCable", self.D11_21_ROC)),
                                self.D11_21_ROC.clean)

    def test_ROC_clean_kept_output_no_data(self):
        """RunOutputCable which should keep its output has no data.

        A RunOutputCable from a subrun, where the PipelineStep has
        not marked the relevant output for deletion, should have a
        Dataset in its ExecRecordOut.
        """
        self.step_through_roc_creation("subrun_complete")

        # May 12, 2014: this caused the test to fail.  We just want the ERO to not have
        # data.
        # self.triplet_3_rows_dataset.file_source = self.D11_21_ROC
        # self.triplet_3_rows_dataset.save()

        self.C1_in_dataset.dataset_file.delete()
        ero = self.D11_21_ROC.execrecord.execrecordouts.first()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('ExecRecordOut "{}" should reference existent data'.format(ero)),
                                self.D11_21_ROC.clean)

    def test_ROC_clean_kept_output_reused_no_data(self):
        """Reused RunOutputCable should produce no data.

        A RunOutputCable from a subrun, where the PipelineStep has not
        marked the relevant output for deletion, should have no data if
        it is reusing an ExecRecord.
        """
        self.step_through_roc_creation("subrun")
        self.D11_21_ROC.reused = True
        self.assertFalse(self.D11_21_ROC.outputs.exists())
        self.assertIsNone(self.D11_21_ROC.clean())

    def test_ROC_clean_kept_output_trivial_no_data(self):
        """Non-reused, trivial RunOutputCable should produce no data.

        A RunOutputCable from a subrun where the PipelineStep has not
        marked the relevant output for deletion, and which is not
        reusing an ExecRecord, should still have no data associated if
        it is a trivial cable.
        """
        self.step_through_roc_creation("subrun")
        self.D11_21.custom_wires.all().delete()
        self.assertFalse(self.D11_21_ROC.outputs.exists())
        self.assertIsNone(self.D11_21_ROC.clean())

    def test_ROC_clean_kept_output_nontrivial_no_data(self):
        """Non-reused, nontrivial RunOutputCable with no data.

        A nontrivial RunOutputCable from a subrun which is not reusing
        an ExecRecord, where the PipelineStep has not marked the output
        for deletion, should produce data.
        """
        self.step_through_roc_creation("subrun")
        self.make_complete_non_reused(self.D11_21_ROC, [self.C1_in_dataset], [self.C1_in_dataset])

        self.assertTrue(self.D11_21_ROC.keeps_output())
        self.assertListEqual(self.D11_21_ROC.log.missing_outputs(), [])
        self.assertFalse(self.D11_21_ROC.outputs.exists())
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunOutputCable "{}" was not reused, trivial, or deleted; it should have '
                                          'produced data'.format(self.D11_21_ROC)),
                                self.D11_21_ROC.clean)

    def test_ROC_clean_wrong_data(self):
        """Non-reused, nontrival RunOutputCable with the wrong Dataset.

        A RunOutputCable with a Dataset different from that in its
        ExecRecordOut is not clean.
        """
        self.step_through_roc_creation("subrun")
        self.make_complete_non_reused(self.D11_21_ROC, [self.C1_in_dataset], [self.C1_in_dataset])
        self.triplet_3_rows_dataset.file_source = self.D11_21_ROC
        self.triplet_3_rows_dataset.save()

        self.assertFalse(self.D11_21_ROC.component.is_trivial())
        self.assertFalse(self.D11_21_ROC.reused)
        self.assertNotEqual(self.triplet_3_rows_dataset,
                            self.D11_21_ROC.execrecord.execrecordouts.first().dataset)

        self.assertRaisesRegexp(ValidationError,
                                re.escape('Dataset "{}" was produced by RunOutputCable "{}" but is not in an ERO of '
                                          'ExecRecord "{}"'.format(self.triplet_3_rows_dataset, self.D11_21_ROC,
                                                                   self.D11_21_ROC.execrecord)),
                                self.D11_21_ROC.clean)

    def test_ROC_clean_correct_data(self):
        """Non-reused, nontrivial RunOutputCable with correct Dataset.

        A RunOutputCable with the same Dataset in its ExecRecordOut as
        in its output, is clean.
        """
        self.step_through_roc_creation("subrun_complete")
        self.D11_21_ROC.outputs.add(self.C1_in_dataset)
        self.assertIsNone(self.D11_21_ROC.clean())

    def test_ROC_clean_trivial_with_data(self):
        """Trivial top-level cable with associated data.

        A trivial RunOutputCable not for a subrun, which has an output
        Dataset associated, is not clean.
        """
        self.step_through_roc_creation("trivial_roc_completed")
        self.singlet_dataset.file_source = self.E31_42_ROC
        self.singlet_dataset.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunOutputCable "{}" is trivial and should not have generated any Datasets'
                                          .format(self.E31_42_ROC)),
                                self.E31_42_ROC.clean)

    def test_ROC_clean_trivial_no_data(self):
        """Trivial top-level cable with no associated data.

        A trivial RunOutputCable not for a subrun, which has no output
        Dataset associated, is clean.
        """
        self.step_through_roc_creation("trivial_roc_completed")
        self.assertFalse(self.E31_42_ROC.has_data())
        self.assertIsNone(self.E31_42_ROC.clean())

    def test_ROC_clean_nontrivial_good_data(self):
        """Nontrivial top-level cable with correct associated data.

        A RunOutputCable with custom wires, which has associated output
        data matching its ExecRecordOut, is clean.
        """
        self.step_through_roc_creation("custom_roc_completed")
        self.E21_41_ROC.outputs.add(self.doublet_dataset)
        self.assertTrue(self.E21_41_ROC.has_data())
        self.assertIsNone(self.E21_41_ROC.clean())

    def test_ROC_clean_nontrivial_no_data(self):
        """Nontrivial top-level cable with no data associated.

        A nontrivial, non-reused RunOutputCable not for a subrun must
        have produced output data, otherwise it is not clean.
        """
        self.step_through_roc_creation("custom_roc_completed")
        self.doublet_dataset.file_source = None
        self.doublet_dataset.save()
        self.assertRaisesRegexp(ValidationError,
                                re.escape('RunOutputCable "{}" was not reused, trivial, or deleted; it should have '
                                          'produced data'.format(self.E21_41_ROC)),
                                self.E21_41_ROC.clean)

    def test_ROC_subrun_complete(self):
        """Clean and complete RunOutputCable for subrun.

        A RunOutputCable for a subrun where the output of the
        sub-pipeline kept, which has the correct associated Dataset,
        is clean and complete.
        """
        self.step_through_roc_creation("subrun_complete")
        self.C1_in_dataset.file_source = self.D11_21_ROC
        self.C1_in_dataset.save()

        self.assertIsNone(self.D11_21_ROC.clean())
        self.assertIsNotNone(self.D11_21_ROC.execrecord)
        self.assertFalse(self.D11_21_ROC.reused)

        self.assertTrue(self.D11_21_ROC.is_complete())
        self.assertIsNone(self.D11_21_ROC.complete_clean())

    def test_ROC_subrun_no_execrecord(self):
        """RunOutputCable with no ExecRecord.

        A nontrivial RunOutputCable for a subrun which has no ExecRecord
        is not complete yet.
        """
        self.step_through_roc_creation("subrun")
        self.D11_21_ROC.execrecord = None
        self.assertFalse(self.D11_21_ROC.is_complete())
        self.assertRaisesRegexp(ValidationError,
                                re.escape('{} "{}" is not complete'.format("RunOutputCable", self.D11_21_ROC)),
                                self.D11_21_ROC.complete_clean)

    ####
    # keeps_output tests added March 26, 2014 -- RL.
    def test_ROC_keeps_output_top_level_trivial(self):
        """
        A top-level trivial RunSIC should have keeps_output() return False.
        """
        self.step_through_roc_creation("trivial_roc_completed")
        self.assertFalse(self.E31_42_ROC.keeps_output())

    def test_ROC_keeps_output_top_level_custom(self):
        """
        A top-level custom RunSIC should have keeps_output() return True.
        """
        self.step_through_roc_creation("custom_roc_completed")
        self.assertTrue(self.E21_41_ROC.keeps_output())

    def test_ROC_keeps_output_top_level_trivial_incomplete(self):
        """
        A top-level trivial incomplete RunSIC should have keeps_output() return False.
        """
        self.step_through_roc_creation("roc_created")
        self.assertFalse(self.E31_42_ROC.keeps_output())

    def test_ROC_keeps_output_top_level_custom_incomplete(self):
        """
        A top-level custom incomplete RunSIC should have keeps_output() return True.
        """
        self.step_through_roc_creation("roc_created")
        self.assertTrue(self.E21_41_ROC.keeps_output())

    def test_ROC_keeps_output_subrun_trivial_true(self):
        """
        A trivial POC of a sub-run that doesn't discard its output should have keeps_output() return False.
        """
        self.step_through_roc_creation("subrun_complete")
        self.D11_21.custom_wires.all().delete()
        self.assertFalse(self.D11_21_ROC.keeps_output())

    def test_ROC_keeps_output_subrun_custom_true(self):
        """
        A custom POC of a sub-run that doesn't discard its output should have keeps_output() return True.
        """
        self.step_through_roc_creation("subrun_complete")
        self.assertTrue(self.D11_21_ROC.keeps_output())

    def test_ROC_keeps_output_subrun_custom_false(self):
        """
        A custom POC of a sub-run that does discard its output should have keeps_output() return False.
        """
        self.step_through_roc_creation("subrun_complete")
        self.step_E2.add_deletion(self.D1_out)
        self.assertFalse(self.D11_21_ROC.keeps_output())

    def test_ROC_keeps_output_incomplete_subrun_trivial_true(self):
        """
        A trivial POC of an incomplete sub-run that doesn't discard its output should have keeps_output() return False.
        """
        self.step_through_roc_creation("subrun")
        self.D11_21.custom_wires.all().delete()
        self.assertFalse(self.D11_21_ROC.keeps_output())

    def test_ROC_keeps_output_incomplete_subrun_custom_true(self):
        """
        A custom cable of an incomplete sub-run that doesn't discard its output should have keeps_output() return True.
        """
        self.step_through_roc_creation("subrun")
        self.assertTrue(self.D11_21_ROC.keeps_output())

    def test_ROC_keeps_output_incomplete_subrun_custom_false(self):
        """
        A custom cable of an incomplete sub-run that does discard its output should have keeps_output() return False.
        """
        self.step_through_roc_creation("subrun")
        self.step_E2.add_deletion(self.D1_out)
        self.assertFalse(self.D11_21_ROC.keeps_output())


class ExecLogTests(ArchiveTestCase):
    def test_delete_exec_log(self):
        """Can delete an ExecLog."""
        step_E1_RS = self.step_E1.pipelinestep_instances.create(run=self.pE_run)
        execlog = ExecLog(record=step_E1_RS, invoking_record=step_E1_RS)
        execlog.save()
        self.assertIsNone(execlog.delete())

    def test_clean_record_not_RunComponent(self):
        """record of ExecLog should be a RunComponent."""
        self.step_through_run_creation("outcables_done")
        # Retrieve an ExecLog and point it at a subrun.

        for el in ExecLog.objects.all():
            original_record = el.record
            el.record = self.step_E2_RS
            self.assertRaisesRegexp(ValidationError,
                                    'ExecLog "{}" does not correspond to a Method or cable'.format(el),
                                    el.clean)
            el.record = original_record
            el.save()

    def test_clean_record_and_invoked_records_different_Run(self):
        """ExecLog's record and invoked_records should belong to the same top-level Run."""
        self.step_through_runstep_creation("first_rsic")

        other_run = self.pE.pipeline_instances.create(user=self.myUser)
        other_run.grant_everyone_access()
        self.make_complete_reused(self.step_E1_RS, [self.raw_dataset], [self.doublet_dataset], other_run)

        # Now step_E1_RS is marked as reused, and there is a dummy record of a RunStep belonging to
        # other_run.  Let's retrieve that ExecLog and mis-wire it so that its record belongs to self.pE_run
        # while its invoking record remains other_run and that should trigger an error in ExecLog.clean().
        el_to_mess_up = other_run.runsteps.get(pipelinestep__step_num=1).log

        el_to_mess_up.record = self.step_E1_RS

        self.assertRaisesRegexp(
            ValidationError,
            'ExecLog "{}" belongs to a different Run than its invoking RunStep/RSIC/ROC'.format(el_to_mess_up),
            el_to_mess_up.clean)

    def test_clean_invoking_record_precedes_record_different_coords(self):
        """An ExecLog should not have been invoked before its own record."""
        self.step_through_run_creation("outcables_done")
        # Get all ExecLogs and change their invoking record to things preceding it in the run.

        for el in ExecLog.objects.all():
            # Skip if this ExecLog doesn't have top-level run pE_run.
            if el.record.definite.top_level_run is not self.pE_run:
                continue

            # Skip if this ExecLog has coordinates (1,).
            if el.record.definite.get_coordinates() == (1,):
                continue

            # Change invoking_record to step 1.
            original_invoking_record = el.invoking_record
            el.invoking_record = self.step_E1_RS
            self.assertRaisesRegexp(
                ValidationError,
                'ExecLog "{}" is invoked earlier than the RunStep/RSIC/ROC it belongs to'.format(el),
                el.clean)
            el.invoking_record = original_invoking_record
            el.save()

    def test_clean_invoking_record_precedes_record_RSIC_of_RS(self):
        """The ExecLog of a RunStep should not be invoked by its own RSICs."""
        self.step_through_run_creation("outcables_done")

        # Retrieve all ExecLogs of steps.
        step_ELs = []
        for candidate_EL in ExecLog.objects.all():
            if candidate_EL.record.is_step:
                step_ELs.append(candidate_EL)

        for el in step_ELs:
            original_invoking_record = el.invoking_record
            for rsic in el.record.runstep.RSICs.all():
                el.invoking_record = rsic
                self.assertRaisesRegexp(
                    ValidationError,
                    'ExecLog "{}" is invoked earlier than the RunStep it belongs to'.format(el),
                    el.clean)
            el.invoking_record = original_invoking_record
            el.save()

    def test_clean_invoking_record_precedes_record_anything_precedes_ROC(self):
        """The ExecLog of a RunOutputCable should not be invoked by anything before it in its run."""
        self.step_through_run_creation("outcables_done")

        # Retrieve all ExecLogs of ROCs.
        ROC_ELs = []
        for candidate_EL in ExecLog.objects.all():
            if candidate_EL.record.is_outcable:
                ROC_ELs.append(candidate_EL)

        for el in ROC_ELs:
            # Get its containing run.
            containing_run = el.record.runoutputcable.run

            original_invoking_record = el.invoking_record

            # Change its invoking record to all steps and RSICs prior to it.
            for step in containing_run.runsteps.all():

                # Only use this step if it isn't a sub-pipeline.
                if not step.has_subrun():
                    el.invoking_record = step
                    self.assertRaisesRegexp(
                        ValidationError,
                        'ExecLog "{}" is invoked earlier than the ROC it belongs to'.format(el),
                        el.clean)

                for rsic in step.RSICs.all():
                    el.invoking_record = rsic
                    self.assertRaisesRegexp(
                        ValidationError,
                        'ExecLog "{}" is invoked earlier than the ROC it belongs to'.format(el),
                        el.clean)
            el.invoking_record = original_invoking_record
            el.save()

    def test_clean_good_ExecLog(self):
        """
        Test that clean doesn't barf on good ExecLogs.
        """
        self.step_through_run_creation("outcables_done")
        for el in ExecLog.objects.all():
            self.assertIsNone(el.clean())

    def test_clean_good_ExecLog_invoked_later(self):
        """
        ExecLogs can be invoked later in the same pipeline than their own record.

        Note that this case causes the containing Run and such to be inconsistent,
        but we're only checking ExecLog right now.
        """
        self.step_through_run_creation("outcables_done")
        el_to_mess_with = self.step_E1_RS.log

        el_to_mess_with.invoking_record = self.step_E3_RS

        self.assertIsNone(el_to_mess_with.clean())

    def test_is_successful_methodoutput_unset(self):
        """
        An ExecLog with no MethodOutput should still be successful.
        """
        self.step_through_runstep_creation("first_rsic")
        execlog = ExecLog(record=self.step_E1_RS, invoking_record=self.step_E1_RS,
                          start_time=timezone.now(), end_time=None)
        execlog.save()
        self.assertTrue(execlog.is_successful())

    def test_is_successful_methodoutput_return_code_unset(self):
        """
        An ExecLog whose MethodOutput return_code has not been set yet should still be successful.
        """
        self.step_through_runstep_creation("first_rsic")
        execlog = ExecLog(record=self.step_E1_RS, invoking_record=self.step_E1_RS,
                          start_time=timezone.now(), end_time=None)
        execlog.save()
        mo = MethodOutput(execlog=execlog)
        mo.save()
        self.assertTrue(execlog.is_successful())

    def test_is_successful_methodoutput_good(self):
        """
        An ExecLog whose MethodOutput return_code is 0 should be successful.
        """
        self.step_through_runstep_creation("first_rsic")
        execlog = ExecLog(record=self.step_E1_RS, invoking_record=self.step_E1_RS,
                          start_time=timezone.now(), end_time=None)
        execlog.save()
        mo = MethodOutput(execlog=execlog, return_code=0)
        mo.save()
        self.assertTrue(execlog.is_successful())

    def test_is_successful_methodoutput_bad(self):
        """
        An ExecLog whose MethodOutput return_code is not 0 should be successful.
        """
        self.step_through_runstep_creation("first_rsic")
        execlog = ExecLog(record=self.step_E1_RS, invoking_record=self.step_E1_RS,
                          start_time=timezone.now(), end_time=None)
        execlog.save()
        mo = MethodOutput(execlog=execlog, return_code=1)
        mo.save()
        self.assertFalse(execlog.is_successful())


class GetCoordinatesTests(TestCase, ArchiveTestCaseHelpers):
    fixtures = ['archive_test_environment']
    """Tests of the get_coordinates functions of all Run and RunComponent classes."""

    def test_get_coordinates_top_level_run(self):
        """Coordinates of a top-level run should be an empty tuple."""
        self.step_through_run_creation("outcables_done")
        top_level_runs = Run.objects.filter(parent_runstep=None)
        for run in top_level_runs:
            self.assertEquals(run.get_coordinates(), ())

    def test_get_coordinates_subrun(self):
        """Coordinates of a sub-run should match that of their parent runstep."""
        self.step_through_run_creation("outcables_done")
        # pD_run is the second step of its containing top-level run.
        self.assertEquals(self.pD_run.get_coordinates(), (2,))
        self.assertEquals(self.pD_run.get_coordinates(), self.step_E2_RS.get_coordinates())

    def test_get_coordinates_top_level_step(self):
        """Coordinates of a top-level step should be a one-entry tuple with its step number as the entry."""
        self.step_through_run_creation("outcables_done")

        top_level_steps = []
        for runstep in RunStep.objects.all():
            if runstep.run.parent_runstep is None:
                top_level_steps.append(runstep)

        for top_level_step in top_level_steps:
            self.assertEquals(top_level_step.get_coordinates(),
                              (top_level_step.pipelinestep.step_num,))

    def test_get_coordinates_subrun_step(self):
        """Coordinates of a subrun step should be a tuple lexicographically giving its location."""
        self.step_through_run_creation("outcables_done")

        # step_D1_RS (as defined by Eric) is at position (2,1).
        self.assertEquals(self.step_D1_RS.get_coordinates(), (2, 1))

    def test_get_coordinates_top_level_rsic(self):
        """Coordinates of top-level RSICs should be one-entry tuples matching their parent RSs."""
        self.step_through_run_creation("outcables_done")

        for runstep in RunStep.objects.all():
            if runstep.run.parent_runstep is None:
                # Examine the input cables.
                for rsic in runstep.RSICs.all():
                    self.assertEquals(rsic.get_coordinates(), (runstep.pipelinestep.step_num,))
                    self.assertEquals(rsic.get_coordinates(), runstep.get_coordinates())

    def test_get_coordinates_subrun_rsic(self):
        """Coordinates of sub-run RSICs should match that of their parent runstep."""
        self.step_through_run_creation("outcables_done")

        # step_D1_RS (as defined by Eric) is at position (2,1).
        for rsic in self.step_D1_RS.RSICs.all():
            self.assertEquals(rsic.get_coordinates(), (2, 1))
            self.assertEquals(rsic.get_coordinates(), self.step_D1_RS.get_coordinates())

    def test_get_coordinates_top_level_roc(self):
        """Coordinates of top-level ROCs should be empty tuples."""
        self.step_through_run_creation("outcables_done")

        for roc in RunOutputCable.objects.all():
            if roc.run.parent_runstep is None:
                # Examine the cable.
                self.assertEquals(roc.get_coordinates(), ())

    def test_get_coordinates_subrun_roc(self):
        """Coordinates of a subrun ROC should be the same as its parent run."""
        self.step_through_run_creation("outcables_done")

        # The second step is a sub-run.
        for roc in self.pD_run.runoutputcables.all():
            self.assertEquals(roc.get_coordinates(), (2,))


class GetCoordinatesOnDeepNestedRunTests(TestCase):
    fixtures = ['deep_nested_run']

    def test_get_coordinates_nested_runs(self):
        """Test get_coordinates for a deeper-nested sub-run."""
        top_level_run = Run.objects.get(pipeline__family__name="p_top")

        self.assertEquals(top_level_run.get_coordinates(), ())

        # Check all second-level and third-level runs.
        for step in top_level_run.runsteps.all():
            first_lvl_step_num = step.pipelinestep.step_num
            subrun = step.child_run

            self.assertEquals(subrun.get_coordinates(), (first_lvl_step_num,))

            for substep in subrun.runsteps.all():
                second_lvl_step_num = substep.pipelinestep.step_num
                basic_run = substep.child_run
                self.assertEqual(basic_run.get_coordinates(), (first_lvl_step_num, second_lvl_step_num))

    def test_get_coordinates_nested_runstep(self):
        """Test get_coordinates for deeper-nested RunSteps."""
        top_level_run = Run.objects.get(pipeline__family__name="p_top")

        # Check all RunSteps of the top-level run and also their child and grandchild runs.
        for step in top_level_run.runsteps.all():
            first_lvl_step_num = step.pipelinestep.step_num
            self.assertEquals(step.get_coordinates(), (first_lvl_step_num,))

            subrun = step.child_run
            for substep in subrun.runsteps.all():
                second_lvl_step_num = substep.pipelinestep.step_num
                self.assertEqual(substep.get_coordinates(), (first_lvl_step_num, second_lvl_step_num))

                basic_run = substep.child_run
                for basic_step in basic_run.runsteps.all():
                    third_lvl_step_num = basic_step.pipelinestep.step_num
                    self.assertEqual(basic_step.get_coordinates(),
                                     (first_lvl_step_num, second_lvl_step_num, third_lvl_step_num))

    def test_get_coordinates_nested_rsic(self):
        """Test get_coordinates for deeper-nested RSICs."""
        top_level_run = Run.objects.get(pipeline__family__name="p_top")

        # Check all RunSteps of the top-level run and also their child and grandchild runs.
        for step in top_level_run.runsteps.all():
            first_lvl_step_num = step.pipelinestep.step_num

            for rsic in step.RSICs.all():
                self.assertEquals(rsic.get_coordinates(), (first_lvl_step_num,))

            subrun = step.child_run
            for substep in subrun.runsteps.all():
                second_lvl_step_num = substep.pipelinestep.step_num

                for subrsic in substep.RSICs.all():
                    self.assertEqual(subrsic.get_coordinates(), (first_lvl_step_num, second_lvl_step_num))

                basic_run = substep.child_run
                for basic_step in basic_run.runsteps.all():
                    third_lvl_step_num = basic_step.pipelinestep.step_num

                    for basic_rsic in basic_step.RSICs.all():
                        self.assertEqual(basic_rsic.get_coordinates(),
                                         (first_lvl_step_num, second_lvl_step_num, third_lvl_step_num))

    def test_get_coordinates_nested_roc(self):
        """Test get_coordinates for deeper-nested sub-ROCs."""

        top_level_run = Run.objects.get(pipeline__family__name="p_top")

        for roc in top_level_run.runoutputcables.all():
            self.assertEquals(roc.get_coordinates(), ())

        # Check all second-level and third-level runs.
        for step in top_level_run.runsteps.all():
            first_lvl_step_num = step.pipelinestep.step_num
            subrun = step.child_run

            for subroc in subrun.runoutputcables.all():
                self.assertEquals(subroc.get_coordinates(), (first_lvl_step_num,))

            for substep in subrun.runsteps.all():
                second_lvl_step_num = substep.pipelinestep.step_num
                basic_run = substep.child_run

                for basic_roc in basic_run.runoutputcables.all():
                    self.assertEqual(basic_roc.get_coordinates(), (first_lvl_step_num, second_lvl_step_num))


class IsCompleteSuccessfulExecutionTests(ArchiveTestCase):
    """
    Tests the is_complete/successful_execution functions of Run, RunComponent, RunStep, ExecLog.

    These functions are heavily dependent on each other, so we share the setups and test
    both functions at the same time.
    """

    def test_execlog_good_cases(self):
        """
        Testing that all ExecLogs are complete and successful after a (simulated) good run.
        """
        self.step_through_run_creation("outcables_done")

        for el in ExecLog.objects.all():
            if el.record.definite.top_level_run == self.pE_run:
                self.assertTrue(el.is_complete())
                self.assertTrue(el.is_successful())

    def test_execlog_has_not_ended_yet(self):
        """
        Test on ExecLogs where has_ended() is False.
        """
        self.step_through_run_creation("outcables_done")

        # Artificially change the logs' end_time to None.
        for el in ExecLog.objects.all():
            if el.record.definite.top_level_run == self.pE_run:
                orig_end_time = el.end_time
                el.end_time = None
                self.assertFalse(el.is_complete())
                self.assertTrue(el.is_successful())
                el.end_time = orig_end_time

    def test_execlog_cable_incomplete_successful(self):
        """
        An incomplete cable's ExecLog should still be successful.
        """
        self.step_through_run_creation("first_cable_created")
        # No checks have been done yet, so the ExecLog is done but the RSIC is not.
        step_E1_RSIC = self.step_E1_RS.RSICs.first()
        self.make_complete_non_reused(step_E1_RSIC, [self.raw_dataset], [self.raw_dataset])

        self.assertTrue(step_E1_RSIC.log.is_complete())
        self.assertTrue(step_E1_RSIC.log.is_successful())

    def test_execlog_of_runstep_has_no_methodoutput(self):
        """Test on ExecLogs for a RunStep that has no MethodOutput."""
        self.step_through_run_creation("first_cable")

        execlog = ExecLog(record=self.step_E1_RS, invoking_record=self.step_E1_RS,
                          start_time=timezone.now(), end_time=timezone.now())
        execlog.save()
        # There is no MethodOutput defined.

        self.assertFalse(execlog.is_complete())
        self.assertTrue(execlog.is_successful())

    def test_execlog_step_returncode_not_zero(self):
        """Testing on an ExecLog of a RunStep whose Method has returned with code != 0."""
        self.step_through_run_creation("first_cable")
        # Complete the RunStep...
        self.make_complete_non_reused(self.step_E1_RS, [self.raw_dataset], [self.doublet_dataset])
        # ... and break it.
        el_to_break = self.step_E1_RS.log
        el_to_break.methodoutput.return_code = 1
        el_to_break.methodoutput.save()
        self.assertTrue(self.step_E1_RS.log.is_complete())
        self.assertFalse(self.step_E1_RS.log.is_successful())

    def test_runcomponent_successful_run(self):
        """
        Quick test of good cases coming out of a (simulated) good run.
        """
        self.step_through_run_creation("outcables_done")

        atomicrunsteps = []
        for runstep in RunStep.objects.all():
            if runstep.transformation.is_method:
                atomicrunsteps.append(runstep)
        runcomponents = (atomicrunsteps + list(RunSIC.objects.all()) + list(RunOutputCable.objects.all()))

        for runcomponent in runcomponents:
            # Skip RunComponents that are not part of this Run.
            if runcomponent.top_level_run != self.pE_run:
                continue

            self.assertTrue(runcomponent.is_complete())
            self.assertTrue(runcomponent.is_successful())

    def test_runcomponent_successful_no_execrecord(self):
        """Testing of a RunComponent (RunSIC) that is successful but has no ExecRecord yet."""
        self.step_through_run_creation("first_cable_created")

        incomplete_cable = self.step_E1_RS.RSICs.get(PSIC=self.step_E1.cables_in.first())

        self.make_execlog_and_mark_non_reused_runcomponent(incomplete_cable)
        self.assertFalse(incomplete_cable.is_complete())
        self.assertTrue(incomplete_cable.successful_execution())

    def test_runcomponent_successful_has_execrecord_reused(self):
        """Testing of a RunComponent which has an ExecRecord and is reused (so is done)"""
        self.step_through_run_creation("first_cable_created")

        incomplete_cable = self.step_E1_RS.RSICs.get(PSIC=self.step_E1.cables_in.first())

        # Create another run.
        other_run = self.pE.pipeline_instances.create(user=self.myUser)
        other_run.grant_everyone_access()
        other_step1 = self.step_E1.pipelinestep_instances.create(run=other_run)
        self.make_complete_reused(incomplete_cable, [self.raw_dataset], [self.raw_dataset], other_step1)
        other_cable = other_step1.RSICs.first()
        icl = self.raw_dataset.integrity_checks.create(execlog=other_cable.log, user=self.myUser)
        icl.start(save=False)
        icl.stop(save=False)
        icl.save()

        self.assertTrue(incomplete_cable.is_complete())
        self.assertTrue(incomplete_cable.successful_reuse())

    def test_runcomponent_successful_checks_not_passed(self):
        """Testing of a RunComponent (RunSIC) that is successful but has no ExecRecord yet."""
        self.step_through_run_creation("first_cable_created")

        incomplete_cable = self.step_E1_RS.RSICs.get(PSIC=self.step_E1.cables_in.first())
        self.make_complete_non_reused(incomplete_cable, [self.raw_dataset], [self.raw_dataset])

        self.assertFalse(incomplete_cable.is_complete())
        self.assertTrue(incomplete_cable.successful_execution())

    def test_runcomponent_successful_checks_passed(self):
        """Testing of a RunComponent (RunSIC) that is successful and all checks pass."""
        self.step_through_run_creation("first_cable_created")

        incomplete_cable = self.step_E1_RS.RSICs.get(PSIC=self.step_E1.cables_in.first())
        self.make_complete_non_reused(incomplete_cable, [self.raw_dataset], [self.raw_dataset])

        icl = self.raw_dataset.integrity_checks.create(execlog=incomplete_cable.log, user=self.myUser)
        icl.start(save=False)
        icl.stop(save=False)
        icl.save()

        self.assertTrue(incomplete_cable.is_complete())
        self.assertTrue(incomplete_cable.successful_execution())

    def test_runcomponent_unsuccessful_failed_execlog(self):
        """Testing of a RunComponent (RunStep) which fails at the ExecLog stage."""
        self.step_through_run_creation("first_step_complete")

        step_1_log = self.step_E1_RS.log

        ccl_to_wipe = step_1_log.content_checks.first()
        ccl_to_wipe.execlog = None
        ccl_to_wipe.save()

        mo_to_change = step_1_log.methodoutput
        mo_to_change.return_code = 1
        mo_to_change.save()

        self.assertTrue(self.step_E1_RS.is_complete())
        self.assertFalse(self.step_E1_RS.successful_execution())

    def test_runcomponent_unsuccessful_failed_content_check(self):
        """Testing of a RunComponent (RunStep) which failed at the content check stage."""
        self.step_through_run_creation("first_step_complete")

        step_1_log = self.step_E1_RS.log

        ccl_to_fail = step_1_log.content_checks.first()
        ccl_to_fail.add_bad_header()

        self.assertTrue(self.step_E1_RS.is_complete())
        self.assertFalse(self.step_E1_RS.successful_execution())

    def test_runcomponent_unsuccessful_failed_integrity_check(self):
        """Testing of a RunComponent (RunSIC) which failed at the integrity check stage."""
        self.step_through_run_creation("first_cable_created")
        step_E1_RSIC = self.step_E1_RS.RSICs.first()
        self.make_complete_non_reused(step_E1_RSIC, [self.raw_dataset], [self.raw_dataset])

        # Make a bad ICL.
        conflicting_datafile = tempfile.NamedTemporaryFile()
        conflicting_datafile.write("THIS IS A FAILURE")

        # The output of this first cable is self.raw_dataset.  This creates a bad ICL.
        self.raw_dataset.check_integrity(conflicting_datafile.name, self.pE_run.user, step_E1_RSIC.log)
        self.assertTrue(step_E1_RSIC.is_complete())
        self.assertFalse(step_E1_RSIC.successful_execution())
        conflicting_datafile.close()

    def test_runcomponent_unsuccessful_failed_invoked_log(self):
        """Testing of a RunComponent which has a failed invoked_log and never gets to its own execution."""
        # Run two pipelines, the second of which reuses parts of the first, but the method has been
        # screwed with in between.
        p_one = tools.make_first_pipeline("p_one", "two no-ops", self.user_bob)
        tools.create_linear_pipeline(p_one, [self.method_noop, self.method_noop], "p_one_in", "p_one_out")
        p_one.create_outputs()
        p_one.save()
        # Mark the output of step 1 as not retained.
        p_one.steps.get(step_num=1).add_deletion(self.method_noop.outputs.first())

        # Set up a words dataset.
        tools.make_words_dataset(self)

        self.sandbox_one = sandbox.execute.Sandbox(self.user_bob, p_one, [self.dataset_words],
                                                   groups_allowed=[everyone_group()])
        self.sandbox_one.execute_pipeline()

        # Oops!  Between runs, self.method_noop gets screwed with.
        with tempfile.TemporaryFile() as f:
            f.write("#!/bin/bash\n exit 1")
            os.remove(self.coderev_noop.content_file.path)
            self.coderev_noop.content_file = File(f)
            self.coderev_noop.save()

        p_two = tools.make_first_pipeline("p_two", "one no-op then one trivial", self.user_bob)
        tools.create_linear_pipeline(p_two, [self.method_noop, self.method_trivial], "p_two_in", "p_two_out")
        p_two.create_outputs()
        p_two.save()
        # We also delete the output of step 1 so that it reuses the existing ER we'll have
        # create for p_one.
        p_two.steps.get(step_num=1).add_deletion(self.method_noop.outputs.first())

        self.sandbox_two = sandbox.execute.Sandbox(self.user_bob, p_two, [self.dataset_words],
                                                   groups_allowed=[everyone_group()])
        self.sandbox_two.execute_pipeline()

        # In the second run: the transformation of the second step should have tried to invoke the log of step 1 and
        # failed.
        run2_step1 = self.sandbox_two.run.runsteps.get(pipelinestep__step_num=1)
        run2_step2 = self.sandbox_two.run.runsteps.get(pipelinestep__step_num=2)

        self.assertFalse(run2_step2.has_log)
        self.assertEquals(run2_step2.invoked_logs.count(), 1)
        self.assertEquals(run2_step2.invoked_logs.first(), run2_step1.log)

        self.assertFalse(run2_step1.log.is_successful())
        self.assertTrue(run2_step2.is_complete())
        self.assertFalse(run2_step2.successful_execution())

    def test_long_output(self):
        """Should handle lots of output to stdout or stderr without deadlocking."""
        iteration_count = 100000
        python_code = """\
#! /usr/bin/python
import sys

for i in range(%d):
    print i
""" % iteration_count
        expected_output = '\n'.join(map(str, range(iteration_count))) + '\n'

        code_revision = tools.make_first_revision(
            "long_out",
            "a script with lots of output",
            "long_out.py",
            python_code,
            self.user_bob)

        # A Method telling Shipyard how to use the noop code on string data.
        method = tools.make_first_method(
            "string long_out",
            "a method with lots of output",
            code_revision,
            self.user_bob)
        tools.simple_method_io(method, self.cdt_string, "strings", "expected")
        pipeline = tools.make_first_pipeline("pipe", "noisy", self.user_bob)
        tools.create_linear_pipeline(pipeline, [method], "in", "out")
        pipeline.create_outputs()
        pipeline.save()

        # Set up a words dataset.
        tools.make_words_dataset(self)

        active_sandbox = sandbox.execute.Sandbox(self.user_bob, pipeline, [self.dataset_words],
                                                 groups_allowed=[everyone_group()])
        active_sandbox.execute_pipeline()

        run_step = active_sandbox.run.runsteps.get(pipelinestep__step_num=1)
        stdout_file = run_step.log.methodoutput.output_log
        stdout_file.open()
        try:
            stdout_content = stdout_file.read()
        finally:
            stdout_file.close()

        self.assertTrue(run_step.is_complete())
        self.assertTrue(run_step.log.is_successful())
        self.assertEqual(len(stdout_content), len(expected_output))
        self.assertEqual(stdout_content, expected_output)

    def setup_incorrectly_random_method(self):
        """
        Helper that sets up a CodeResource and Method that spits out the current time.
        """
        python_code = """\
#! /usr/bin/env python
import sys
import datetime
import csv

with open(sys.argv[2], "wb") as f:
    dt_writer = csv.writer(f)
    dt_writer.writerow(("year", "month", "day", "hour", "minute", "second", "microsecond"))

    curr_time = datetime.datetime.now()
    dt_writer.writerow((curr_time.year, curr_time.month, curr_time.day,
                        curr_time.hour, curr_time.minute, curr_time.second,
                        curr_time.microsecond))

"""
        self.curr_time_crr = tools.make_first_revision(
            "CurrentTime",
            "Gives the current time",
            "CurrentTime.py",
            python_code,
            self.user_bob)

        self.curr_time_CDT = CompoundDatatype(user=self.user_bob)
        self.curr_time_CDT.save()
        self.curr_time_CDT.grant_everyone_access()
        self.curr_time_CDT.members.create(
            datatype=self.INT,
            column_name="year",
            column_idx=1
        )
        self.curr_time_CDT.members.create(
            datatype=self.INT,
            column_name="month",
            column_idx=2
        )
        self.curr_time_CDT.members.create(
            datatype=self.INT,
            column_name="day",
            column_idx=3
        )
        self.curr_time_CDT.members.create(
            datatype=self.INT,
            column_name="hour",
            column_idx=4
        )
        self.curr_time_CDT.members.create(
            datatype=self.INT,
            column_name="minute",
            column_idx=5
        )
        self.curr_time_CDT.members.create(
            datatype=self.INT,
            column_name="second",
            column_idx=6
        )
        self.curr_time_CDT.members.create(
            datatype=self.INT,
            column_name="microsecond",
            column_idx=7
        )

        # Note that this is incorrectly marked as deterministic!  (This is on purpose for the
        # test.)
        self.curr_time_method = tools.make_first_method(
            "CurrentTime",
            "Gives the current time -- incorrectly marked as deterministic",
            self.curr_time_crr,
            self.user_bob
        )
        tools.simple_method_io(self.curr_time_method, self.curr_time_CDT, "ignored_input", "curr_time")

        self.time_noop = tools.make_first_method(
            "TimeNoop",
            "Noop on curr_time_CDT",
            self.coderev_noop,
            self.user_bob
        )
        tools.simple_method_io(self.time_noop, self.curr_time_CDT, "input", "unchanged_output")

        self.time_trivial = tools.make_first_method(
            "TimeTrivial",
            "Also a noop on curr_time_CDT",
            self.coderev_noop,
            self.user_bob
        )
        tools.simple_method_io(self.time_trivial, self.curr_time_CDT, "input", "unchanged_output")

        self.time_SD = tools.make_dataset(
            """\
year,month,day,hour,minute,second,microsecond
1969,1,1,0,0,0,0
""",
            self.curr_time_CDT,
            True,
            self.user_bob,
            "EpochTime",
            "12AM, January 1, 1969",
            None,
            True
        )

    def test_runcomponent_unsuccessful_failed_integrity_check_during_recovery(self):
        """Testing of a RunComponent which has a failed integrity check during recovery."""
        # Run two pipelines, the second of which reuses parts of the first, but the first step's output
        # is different now.
        self.setup_incorrectly_random_method()

        p_one = tools.make_first_pipeline("p_one", "time then noop", self.user_bob)
        tools.create_linear_pipeline(p_one, [self.curr_time_method, self.time_noop], "p_one_in", "p_one_out")
        p_one.create_outputs()
        p_one.save()
        # Mark the output of step 1 as not retained.
        p_one.steps.get(step_num=1).add_deletion(self.curr_time_method.outputs.first())

        self.sandbox_one = sandbox.execute.Sandbox(self.user_bob, p_one, [self.time_SD])
        self.sandbox_one.execute_pipeline()

        # Oops!  The first step should not have been marked as deterministic.
        p_two = tools.make_first_pipeline("p_two", "time then trivial", self.user_bob)
        tools.create_linear_pipeline(p_two, [self.curr_time_method, self.time_trivial], "p_two_in", "p_two_out")
        p_two.create_outputs()
        p_two.save()
        # We also delete the output of step 1 so that it reuses the existing ER we'll have
        # create for p_one.
        p_two.steps.get(step_num=1).add_deletion(self.curr_time_method.outputs.first())

        self.sandbox_two = sandbox.execute.Sandbox(self.user_bob, p_two, [self.time_SD])
        self.sandbox_two.execute_pipeline()

        # In the second run: the transformation of the second step should have tried to invoke the log of step 1 and
        # failed.
        run2_step1 = self.sandbox_two.run.runsteps.get(pipelinestep__step_num=1)
        run2_step2 = self.sandbox_two.run.runsteps.get(pipelinestep__step_num=2)

        self.assertFalse(run2_step2.has_log)
        self.assertEquals(run2_step2.invoked_logs.count(), 1)
        self.assertEquals(run2_step2.invoked_logs.first(), run2_step1.log)

        self.assertTrue(run2_step1.log.is_successful())
        self.assertFalse(run2_step1.log.all_checks_passed())
        self.assertTrue(run2_step2.is_complete())
        self.assertFalse(run2_step2.successful_execution())

    def test_runstep_subpipeline_not_complete(self):
        """Testing on a RunStep containing a sub-pipeline that is not complete."""
        self.step_through_run_creation("sub_pipeline")
        self.assertFalse(self.step_E2_RS.is_complete())
        self.assertTrue(self.step_E2_RS.successful_execution())

    def test_runstep_subpipeline_complete(self):
        """Testing on a RunStep containing a sub-pipeline that is complete."""
        self.step_through_run_creation("sub_pipeline_complete")
        self.assertTrue(self.step_E2_RS.is_complete())
        self.assertTrue(self.step_E2_RS.successful_execution())

    def test_runstep_no_cables_yet(self):
        """Testing on a RunStep with no RSICs yet."""
        self.step_through_run_creation("first_step")
        self.assertFalse(self.step_E1_RS.is_complete())
        self.assertTrue(self.step_E1_RS.successful_execution())

    def test_runstep_cable_just_started(self):
        """Testing on a RunStep with a just-started RSIC."""
        self.step_through_run_creation("first_cable_created")
        self.assertFalse(self.step_E1_RS.is_complete())
        self.assertTrue(self.step_E1_RS.successful_execution())

    def test_runstep_cable_complete(self):
        """Testing on a RunStep with a RSIC that has run but has not done data checking yet."""
        self.step_through_run_creation("first_cable")
        self.assertFalse(self.step_E1_RS.is_complete())
        self.assertTrue(self.step_E1_RS.successful_execution())

    def test_runstep_cable_failed(self):
        """Testing on a RunStep with a RSIC that has run and failed in data checking."""
        self.step_through_run_creation("first_cable_created")
        step_E1_RSIC = self.step_E1_RS.RSICs.first()

        # Let's tamper with self.raw_dataset.
        with tempfile.NamedTemporaryFile() as f:
            f.write("This is a tampered-with file.")
            os.remove(self.raw_dataset.dataset_file.path)
            self.raw_dataset.dataset_file = File(f, name="tampered")
            self.raw_dataset.save()

            self.make_complete_non_reused(step_E1_RSIC, [self.raw_dataset], [self.raw_dataset])

            # Check the integrity of self.raw_dataset -- this should fail.
            self.raw_dataset.check_integrity(f.name, self.myUser, step_E1_RSIC.log)

        self.assertTrue(self.step_E1_RS.is_complete())
        self.assertFalse(self.step_E1_RS.successful_execution())

    def test_runstep_failed_subrun(self):
        """Testing on a RunStep with a child_run that fails."""
        self.step_through_run_creation("sub_pipeline")
        # Fail the sub-pipeline.
        self.step_D1_RS = self.step_D1.pipelinestep_instances.create(run=self.pD_run)
        self.complete_RSICs(self.step_D1_RS, [self.D1_in_dataset, self.singlet_dataset],
                                             [self.D1_in_dataset, self.singlet_dataset])

        self.D01_11_RSIC = self.step_D1_RS.RSICs.filter(PSIC=self.D01_11).first()
        self.D02_12_RSIC = self.step_D1_RS.RSICs.filter(PSIC=self.D02_12).first()
        icl = self.D1_in_dataset.integrity_checks.create(execlog=self.D01_11_RSIC.log, user=self.myUser)
        icl.start(save=False)
        icl.stop(save=False)
        icl.save()
        icl = self.singlet_dataset.integrity_checks.create(execlog=self.D02_12_RSIC.log, user=self.myUser)
        icl.start(save=False)
        icl.stop(save=False)
        icl.save()

        self.make_complete_non_reused(self.step_D1_RS, [self.D1_in_dataset, self.singlet_dataset], [self.C1_in_dataset])
        # Mark step_D1_RS as having failed on execution.
        step_D1_mo = self.step_D1_RS.log.methodoutput
        step_D1_mo.return_code = 1
        step_D1_mo.save()

        self.assertTrue(self.step_D1_RS.is_complete())
        self.assertFalse(self.step_D1_RS.successful_execution())

        self.assertTrue(self.step_E2_RS.is_complete())
        self.assertFalse(self.step_E2_RS.successful_execution())

    def test_run_no_steps_yet(self):
        """Test on a Run with nothing started yet."""
        self.step_through_run_creation("empty_runs")
        self.assertFalse(self.pE_run.is_complete())
        self.assertTrue(self.pE_run.successful_execution())

    def test_run_incomplete_step(self):
        """Test on a Run with nothing started yet."""
        self.step_through_run_creation("third_step_cables_done")
        self.assertFalse(self.pE_run.is_complete())
        self.assertTrue(self.pE_run.successful_execution())

    def test_run_step_failed(self):
        """Test on a Run with a failed and complete step."""
        # Setup copied from test_runcomponent_unsuccessful_failed_execlog.
        self.step_through_run_creation("first_step_complete")

        step_1_log = self.step_E1_RS.log

        ccl_to_wipe = step_1_log.content_checks.first()
        ccl_to_wipe.execlog = None
        ccl_to_wipe.save()

        mo_to_change = step_1_log.methodoutput
        mo_to_change.return_code = 1
        mo_to_change.save()

        self.assertTrue(self.pE_run.is_complete())
        self.assertFalse(self.pE_run.successful_execution())

    def test_run_one_failed_step_one_incomplete_step(self):
        """Test on a Run with one failed and one incomplete step."""
        self.step_through_run_creation("second_step")

        # Make the first step a failure by making self.doublet_dataset (its output) fail its check.
        step1_out_ccl = self.doublet_dataset.content_checks.first()
        step1_out_ccl.add_bad_header()
        step1_out_ccl.save()

        # Start the second step.
        step2 = self.pE_run.runsteps.get(pipelinestep__step_num=2)
        step2.start()

        self.assertFalse(self.pE_run.is_complete())
        self.assertFalse(self.pE_run.successful_execution())

    def test_run_no_output_cables(self):
        """Test on a Run with no output cables yet."""
        self.step_through_run_creation("third_step_complete")
        self.assertFalse(self.pE_run.is_complete())
        self.assertTrue(self.pE_run.successful_execution())

    def test_run_incomplete_output_cable(self):
        """Test on a Run having an incomplete output cable."""
        self.step_through_run_creation("third_step_complete")

        # Add but do not complete an output cable.
        roc1 = self.pE.outcables.get(output_idx=1).poc_instances.create(run=self.pE_run)
        self.make_complete_non_reused(roc1, [self.C1_in_dataset], [self.E1_out_dataset])
        # Note that this isn't actually complete -- it doesn't have data checks yet.
        self.assertFalse(self.pE_run.is_complete())
        self.assertTrue(self.pE_run.successful_execution())

    def test_run_failed_output_cable(self):
        """Test on a Run having a failed output cable."""
        self.step_through_run_creation("third_step_complete")

        # Add but do not complete an output cable.
        roc1 = self.pE.outcables.get(output_idx=1).poc_instances.create(run=self.pE_run)
        self.make_complete_non_reused(roc1, [self.C1_in_dataset], [self.E1_out_dataset])

        # Break the data.
        E1_out_ccl = self.E1_out_dataset.content_checks.first()
        E1_out_ccl.execlog = roc1.log
        E1_out_ccl.add_bad_header()
        E1_out_ccl.save()
        self.E1_out_dataset.file_source = roc1
        self.E1_out_dataset.save()

        self.assertTrue(self.pE_run.is_complete())
        self.assertFalse(self.pE_run.successful_execution())

    def test_run_one_failed_output_cable_one_incomplete_output_cable(self):
        """Test on a Run having one failed output cable and one incomplete one."""
        self.step_through_run_creation("third_step_complete")

        # Add a complete output cable.
        roc1 = self.pE.outcables.get(output_idx=1).poc_instances.create(run=self.pE_run)
        self.make_complete_non_reused(roc1, [self.C1_in_dataset], [self.E1_out_dataset])

        # Break the data.
        E1_out_ccl = self.E1_out_dataset.content_checks.first()
        E1_out_ccl.execlog = roc1.log
        E1_out_ccl.add_bad_header()
        E1_out_ccl.save()

        roc2 = self.pE.outcables.get(output_idx=2).poc_instances.create(run=self.pE_run)
        roc2.start()

        self.assertFalse(self.pE_run.is_complete())
        self.assertFalse(self.pE_run.successful_execution())

    def test_run_missing_output_cables(self):
        """Test on a Run having missing output cables."""
        self.step_through_run_creation("first_outcable")
        self.assertFalse(self.pE_run.is_complete())
        self.assertTrue(self.pE_run.successful_execution())

    def test_run_all_steps_and_cables_done(self):
        """Test on a Run that's completely done."""
        self.step_through_run_creation("outcables_done")
        self.assertTrue(self.pE_run.is_complete())
        self.assertTrue(self.pE_run.successful_execution())


class TopLevelRunTests(TestCase, ArchiveTestCaseHelpers):
    fixtures = ['archive_test_environment']

    def test_usual_run(self):
        """Test on all elements of a simulated run."""
        self.step_through_run_creation("outcables_done")

        self.assertEquals(self.pE_run, self.pE_run.top_level_run)

        # All elements of both pE_run and pD_run should have top_level_run equal
        # to pE_run.

        for curr_run in (self.pE_run, self.pD_run):
            self.assertEquals(self.pE_run, curr_run.top_level_run)

            for runstep in curr_run.runsteps.all():
                self.assertEquals(self.pE_run, runstep.top_level_run)

                for cable_in in runstep.RSICs.all():
                    self.assertEquals(self.pE_run, cable_in.top_level_run)

            for roc in curr_run.runoutputcables.all():
                self.assertEquals(self.pE_run, roc.top_level_run)


class TopLevelRunOnDeepNestedRunTests(TestCase):
    fixtures = ['deep_nested_run']

    def test_deep_nested_run(self):
        """Test on all elements of a deep-nested run."""
        self.deep_nested_run = Run.objects.get(
            pipeline__family__name='p_top')

        # Recurse down all elements of this run and make sure that they all have
        # top_level_run equal to self.deep_nested_run.
        top_level_runs = Run.objects.filter(pipeline__family__name="p_top")
        second_level_runs = Run.objects.filter(pipeline__family__name="p_sub")
        third_level_runs = Run.objects.filter(pipeline__family__name="p_basic")

        for curr_run in list(top_level_runs) + list(second_level_runs) + list(third_level_runs):
            self.assertEquals(self.deep_nested_run, curr_run.top_level_run)

            for runstep in curr_run.runsteps.all():
                self.assertEquals(self.deep_nested_run, runstep.top_level_run)

                for cable_in in runstep.RSICs.all():
                    self.assertEquals(self.deep_nested_run, cable_in.top_level_run)

            for roc in curr_run.runoutputcables.all():
                self.assertEquals(self.deep_nested_run, roc.top_level_run)


class RunStepReuseFailedExecRecordTests(TestCase):
    # fixtures = ["initial_data", "initial_groups", "initial_user"]

    def setUp(self):
        tools.create_grandpa_sandbox_environment(self)
        tools.make_words_dataset(self)

    def tearDown(self):
        tools.destroy_grandpa_sandbox_environment(self)

    def test_reuse_failed_ER_can_have_missing_outputs(self):
        """
        A RunStep that reuses a failed ExecRecord does not care if its required outputs are not in the ExecRecord.
        """
        # The environment provides a method that always fails called method_fubar, which takes in data
        # with CDT cdt_string (string: "word"), and puts out data with the same CDT in principle.

        failing_pipeline = tools.make_first_pipeline("failing pipeline", "a pipeline which always fails",
                                                     self.user_grandpa)
        tools.create_linear_pipeline(
            failing_pipeline,
            [self.method_fubar, self.method_noop], "indata", "outdata"
        )
        failing_pipeline.create_outputs()

        first_step = failing_pipeline.steps.get(step_num=1)
        first_step.add_deletion(self.method_fubar.outputs.first())

        # This Pipeline is identical to the first but doesn't discard output.
        failing_pl_2 = tools.make_first_pipeline("failing pipeline 2", "another pipeline which always fails",
                                                 self.user_grandpa)
        tools.create_linear_pipeline(
            failing_pl_2,
            [self.method_fubar, self.method_noop], "indata", "outdata"
        )
        failing_pl_2.create_outputs()

        # The first Pipeline should fail.  The second will reuse the first step's ExecRecord, and will not
        # throw an exception, even though the ExecRecord doesn't provide the necessary output.
        run_1 = sandbox.execute.Sandbox(self.user_grandpa, failing_pipeline, [self.dataset_words],
                                        groups_allowed=[everyone_group()]).execute_pipeline()
        run_2 = sandbox.execute.Sandbox(self.user_grandpa, failing_pl_2, [self.dataset_words],
                                        groups_allowed=[everyone_group()]).execute_pipeline()

        self.assertEquals(run_1.runsteps.get(pipelinestep__step_num=1).execrecord,
                          run_2.runsteps.get(pipelinestep__step_num=1).execrecord)


class MethodOutputApiTests(BaseTestCases.ApiTestCase):
    fixtures = ['simple_run']

    def setUp(self):
        super(MethodOutputApiTests, self).setUp()

        self.list_path = reverse("methodoutput-list")
        self.detail_pk = 2
        self.detail_path = reverse("methodoutput-detail",
                                   kwargs={'pk': self.detail_pk})
        self.output_redaction_path = reverse("methodoutput-output-redaction-plan",
                                             kwargs={'pk': self.detail_pk})
        self.error_redaction_path = reverse("methodoutput-error-redaction-plan",
                                            kwargs={'pk': self.detail_pk})
        self.code_redaction_path = reverse("methodoutput-code-redaction-plan",
                                           kwargs={'pk': self.detail_pk})

        # This should equal metadata.ajax.CompoundDatatypeViewSet.as_view({"get": "list"}).
        self.list_view, _, _ = resolve(self.list_path)
        self.detail_view, _, _ = resolve(self.detail_path)
        self.output_redaction_view, _, _ = resolve(self.output_redaction_path)
        self.error_redaction_view, _, _ = resolve(self.error_redaction_path)
        self.code_redaction_view, _, _ = resolve(self.code_redaction_path)

    def test_list(self):
        """
        Test the CompoundDatatype API list view.
        """
        request = self.factory.get(self.list_path)
        force_authenticate(request, user=self.kive_user)
        response = self.list_view(request, pk=None)

        # There are four CDTs loaded into the Database by default.
        self.assertEquals(len(response.data), 2)
        self.assertEquals(response.data[0]['output_redacted'], False)

    def test_detail(self):
        request = self.factory.get(self.detail_path)
        force_authenticate(request, user=self.kive_user)
        response = self.detail_view(request, pk=self.detail_pk)
        self.assertEquals(response.data['error_redacted'], False)

    def test_output_redaction_plan(self):
        request = self.factory.get(self.output_redaction_path)
        force_authenticate(request, user=self.kive_user)
        response = self.output_redaction_view(request, pk=self.detail_pk)
        self.assertEquals(response.data['OutputLogs'], 1)

    def test_error_redaction_plan(self):
        request = self.factory.get(self.error_redaction_path)
        force_authenticate(request, user=self.kive_user)
        response = self.error_redaction_view(request, pk=self.detail_pk)
        self.assertEquals(response.data['ErrorLogs'], 1)

    def test_code_redaction_plan(self):
        request = self.factory.get(self.code_redaction_path)
        force_authenticate(request, user=self.kive_user)
        response = self.code_redaction_view(request, pk=self.detail_pk)
        self.assertEquals(response.data['ReturnCodes'], 1)

    def test_redaction(self):
        request = self.factory.patch(self.detail_path,
                                     {'output_redacted': "true"})
        force_authenticate(request, user=self.kive_user)
        response = self.detail_view(request, pk=self.detail_pk)
        self.assertEquals(response.status_code, status.HTTP_200_OK)

        method_output = MethodOutput.objects.get(pk=self.detail_pk)
        self.assertTrue(method_output.is_output_redacted())


class RunIncreasePermissionsNestedRunTests(TestCase):
    """
    Tests of Run.increase_permissions_from_json with nested Runs.
    """
    fixtures = ["deep_nested_run"]

    def setUp(self):
        self.john = User.objects.get(username="john")
        self.ringo = User.objects.get(username="ringo")
        self.bob = User.objects.get(username="bob")

        # This Run has nesting two layers deep, and
        # belongs to self.bob, with permissions granted to Everyone.
        self.run = Run.objects.get(pipeline__family__name="p_top")

        # Let's sweep through and remove all extra permissions on anything self.bob ever produced.
        for ds in Dataset.objects.filter(user=self.bob):
            ds.groups_allowed.remove(everyone_group())

        for run in Run.objects.filter(user=self.bob):
            run.groups_allowed.remove(everyone_group())

    def test_increase_permissions(self):
        """
        Test granting permissions from a JSON input.
        """
        perms_to_add = [[self.john.pk, self.ringo.pk], [groups.DEVELOPERS_PK]]

        self.run.increase_permissions_from_json(json.dumps(perms_to_add))

        # Sweep through and make sure all outputs and Runs have had the appropriate
        # permissions added.
        for run in Run.objects.filter(user=self.bob):
            if run.top_level_run == self.run:
                self.assertTrue(run.users_allowed.filter(pk=self.john.pk).exists())
                self.assertTrue(run.users_allowed.filter(pk=self.ringo.pk).exists())
                self.assertTrue(run.groups_allowed.filter(pk=groups.DEVELOPERS_PK))

        for ds in Dataset.objects.filter(user=self.bob, file_source__isnull=False):
            if ds.file_source.top_level_run == self.run:
                self.assertTrue(ds.users_allowed.filter(pk=self.john.pk).exists())
                self.assertTrue(ds.users_allowed.filter(pk=self.ringo.pk).exists())
                self.assertTrue(ds.groups_allowed.filter(pk=groups.DEVELOPERS_PK))


class RunIncreasePermissionsCustomCableTests(TestCase):
    """
    Tests of Run.increase_permissions_from_json with custom cables.
    """
    fixtures = ["run_api_tests"]

    def setUp(self):
        self.john = User.objects.get(username="john")

        # We want the inputs and the Pipeline to have appropriate permissions
        # for us to be able to freely grant permissions on the Run.
        for cdt in CompoundDatatype.objects.all():
            cdt.grant_everyone_access()

        for cr in CodeResource.objects.all():
            cr.grant_everyone_access()
            for crr in cr.revisions.all():
                crr.grant_everyone_access()

        for mf in MethodFamily.objects.all():
            mf.grant_everyone_access()
            for method in mf.members.all():
                method.grant_everyone_access()

        for pf in PipelineFamily.objects.all():
            pf.grant_everyone_access()
            for pl in pf.members.all():
                pl.grant_everyone_access()

        self.pf = PipelineFamily.objects.get(name="self.pf")
        self.pl = self.pf.members.get(revision_name="pX_revision_2")

        # This is the input to the run.
        ds_structure = DatasetStructure.objects.get(dataset__name="pX_in_dataset", dataset__user=self.john)
        input_ds = ds_structure.dataset
        input_ds.grant_everyone_access()

        # This Run has nesting two layers deep, and
        # belongs to self.bob, with permissions granted to Everyone.
        self.run = Run.objects.get(pipeline__pk=self.pl.pk)

    def test_increase_permissions(self):
        """
        Test granting permissions from a JSON input.
        """
        perms_to_add = [[kive_user().pk], [groups.DEVELOPERS_PK]]

        self.run.increase_permissions_from_json(json.dumps(perms_to_add))

        # Sweep through and make sure all outputs and Runs have had the appropriate
        # permissions added.
        for run in Run.objects.filter(user=self.john):
            if run.top_level_run == self.run:
                self.assertTrue(run.users_allowed.filter(pk=kive_user().pk).exists())
                self.assertTrue(run.groups_allowed.filter(pk=groups.DEVELOPERS_PK))

        for ds in Dataset.objects.filter(user=self.john, file_source__isnull=False):
            if ds.file_source.top_level_run == self.run:
                self.assertTrue(ds.users_allowed.filter(pk=kive_user().pk).exists())
                self.assertTrue(ds.groups_allowed.filter(pk=groups.DEVELOPERS_PK))


class GetAllAtomicRunComponentsTests(TestCase):
    """
    Tests of Run.get_all_atomic_runcomponents.
    """
    fixtures = ["deep_nested_run"]

    def setUp(self):
        self.bob = User.objects.get(username="bob")
        self.method_noop = Method.objects.get(family__name="string noop", revision_name="v1")

        # This Run has nesting two layers deep, and
        # belongs to self.bob, with permissions granted to Everyone.
        # It looks like:
        # p_top
        # - p_sub
        #   - p_basic
        #     - method_noop
        #     - method_noop
        #   - p_basic
        # - p_sub
        # - p_sub
        # with single trivial cables connecting everything.
        self.run = Run.objects.get(pipeline__family__name="p_top")

        # Let's sweep through and remove all extra permissions on anything self.bob ever produced.
        for ds in Dataset.objects.filter(user=self.bob):
            ds.groups_allowed.remove(everyone_group())

        for run in Run.objects.filter(user=self.bob):
            run.groups_allowed.remove(everyone_group())

    def test_get_all_atomic_run_components(self):
        """
        Test on a run with a fair amount of nesting.
        """
        all_rcs = self.run.get_all_atomic_runcomponents()

        # The stuff that should be in all_rcs:
        atomics = []
        # Look at each step of p_top.
        for top_step_num in (1,2,3):
            curr_top_step = self.run.runsteps.get(pipelinestep__step_num=top_step_num)
            atomics += list(curr_top_step.RSICs.all())

            # Descend into p_sub.
            curr_p_sub_run = curr_top_step.child_run
            for sub_step_num in (1,2):
                curr_sub_step = curr_p_sub_run.runsteps.get(pipelinestep__step_num=sub_step_num)
                atomics += list(curr_sub_step.RSICs.all())

                # Descend into p_basic.
                curr_p_basic_run = curr_sub_step.child_run
                for third_lvl_step_num in (1,2):
                    curr_basic_step = curr_p_basic_run.runsteps.get(pipelinestep__step_num=third_lvl_step_num)
                    atomics += list(curr_basic_step.RSICs.all())
                    atomics.append(curr_basic_step)
                atomics += list(curr_p_basic_run.runoutputcables.all())

            atomics += list(curr_p_sub_run.runoutputcables.all())

        atomics += list(self.run.runoutputcables.all())

        self.assertSetEqual(set(atomics), set(all_rcs))





