import os
import re
import sys
import tempfile
import shutil

from mock import call, patch

from django.contrib.auth.models import User
from django.core.files import File
from django.core.files.base import ContentFile
from django.test import TestCase, skipIfDBFeature
from django.utils import timezone
from django.conf import settings

from archive.models import Run, RunComponent
from constants import datatypes
from datachecking.models import IntegrityCheckLog, MD5Conflict
from kive.testing_utils import clean_up_all_files
from kive.tests import install_fixture_files, remove_fixture_files, BaseTestCases
from librarian.models import Dataset, DatasetStructure, ExternalFileDirectory, ExecRecord
from metadata.models import Datatype, CompoundDatatype, everyone_group
from method.models import CodeResource, CodeResourceRevision, Method, MethodFamily
from method.tests import samplecode_path
from pipeline.models import Pipeline, PipelineFamily
from sandbox.execute import Sandbox, RunPlan, StepPlan, DatasetPlan
from fleet.workers import Manager
import file_access_utils


def execute_tests_environment_setup(case):
    print("FOOOOOOBAR {}".format(settings.MEDIA_ROOT))
    # Users + method/pipeline families
    case.myUser = User.objects.create_user('john', 'lennon@thebeatles.com', 'johnpassword')
    case.myUser.save()
    case.myUser.groups.add(everyone_group())
    case.myUser.save()

    case.mf = MethodFamily(name="self.mf", description="self.mf desc", user=case.myUser)
    case.mf.save()
    case.pf = PipelineFamily(name="self.pf", description="self.pf desc", user=case.myUser)
    case.pf.save()

    # Code on file system
    case.mA_cr = CodeResource(name="mA_CR", description="self.mA_cr desc", filename="mA.py", user=case.myUser)
    case.mA_cr.save()
    case.mA_crr = CodeResourceRevision(coderesource=case.mA_cr, revision_name="v1", revision_desc="desc",
                                       user=case.myUser)
    with open(os.path.join(samplecode_path, "generic_script.py"), "rb") as f:
        new_file_MD5 = file_access_utils.compute_md5(f)
        f.seek(0)
        case.mA_crr.content_file.save("generic_script.py", File(f))
        case.mA_crr.MD5_checksum = new_file_MD5

    case.mA_crr.save()

    # Basic DTs
    case.string_dt = Datatype.objects.get(pk=datatypes.STR_PK)
    case.int_dt = Datatype.objects.get(pk=datatypes.INT_PK)

    # Basic CDTs
    case.pX_in_cdt = CompoundDatatype(user=case.myUser)
    case.pX_in_cdt.save()
    case.pX_in_cdtm_1 = case.pX_in_cdt.members.create(datatype=case.int_dt, column_name="pX_a", column_idx=1)
    case.pX_in_cdtm_2 = case.pX_in_cdt.members.create(datatype=case.int_dt, column_name="pX_b", column_idx=2)
    case.pX_in_cdtm_3 = case.pX_in_cdt.members.create(datatype=case.string_dt, column_name="pX_c", column_idx=3)

    case.mA_in_cdt = CompoundDatatype(user=case.myUser)
    case.mA_in_cdt.save()
    case.mA_in_cdtm_1 = case.mA_in_cdt.members.create(datatype=case.string_dt, column_name="a", column_idx=1)
    case.mA_in_cdtm_2 = case.mA_in_cdt.members.create(datatype=case.int_dt, column_name="b", column_idx=2)

    case.mA_out_cdt = CompoundDatatype(user=case.myUser)
    case.mA_out_cdt.save()
    case.mA_out_cdtm_1 = case.mA_out_cdt.members.create(datatype=case.int_dt, column_name="c", column_idx=1)
    case.mA_out_cdtm_2 = case.mA_out_cdt.members.create(datatype=case.string_dt, column_name="d", column_idx=2)

    case.dataset = Dataset.create_dataset(
        os.path.join(samplecode_path, "input_for_test_C_twostep_with_subpipeline.csv"),
        user=case.myUser,
        cdt=case.pX_in_cdt,
        name="pX_in_dataset",
        description="input to pipeline pX"
    )
    case.raw_dataset = Dataset.create_dataset(
        os.path.join(samplecode_path, "input_for_test_C_twostep_with_subpipeline.csv"),
        user=case.myUser,
        cdt=None,
        name="pX_in_dataset",
        description="input to pipeline pX"
    )

    # Method + input/outputs
    case.mA = Method(revision_name="mA", revision_desc="mA_desc", family=case.mf, driver=case.mA_crr,
                     user=case.myUser)
    case.mA.save()
    case.mA_in = case.mA.create_input(compounddatatype=case.mA_in_cdt, dataset_name="mA_in", dataset_idx=1)
    case.mA_out = case.mA.create_output(compounddatatype=case.mA_out_cdt, dataset_name="mA_out", dataset_idx=1)

    # Define pipeline containing the method, and its input + outcables
    case.pX = Pipeline(family=case.pf, revision_name="pX_revision", revision_desc="X",
                       user=case.myUser)
    case.pX.save()
    case.X1_in = case.pX.create_input(compounddatatype=case.pX_in_cdt, dataset_name="pX_in", dataset_idx=1)
    case.step_X1 = case.pX.steps.create(transformation=case.mA, step_num=1)

    # Custom cable from pipeline input to method
    case.cable_X1_A1 = case.step_X1.cables_in.create(dest=case.mA_in, source_step=0, source=case.X1_in)
    case.wire1 = case.cable_X1_A1.custom_wires.create(source_pin=case.pX_in_cdtm_2, dest_pin=case.mA_in_cdtm_2)
    case.wire2 = case.cable_X1_A1.custom_wires.create(source_pin=case.pX_in_cdtm_3, dest_pin=case.mA_in_cdtm_1)

    # Pipeline outcables
    case.X1_outcable = case.pX.create_outcable(output_name="pX_out", output_idx=1, source_step=1,
                                               source=case.mA_out)
    case.pX.create_outputs()

    # Pipeline with raw input.
    pX_raw = Pipeline(family=case.pf, revision_name="pX_raw", revision_desc="X", user=case.myUser)
    pX_raw.save()
    mA_raw = Method(revision_name="mA_raw", revision_desc="mA_desc", family=case.mf, driver=case.mA_crr,
                    user=case.myUser)
    mA_raw.save()
    mA_in_raw = mA_raw.create_input(compounddatatype=None, dataset_name="mA_in", dataset_idx=1)
    mA_out_raw = mA_raw.create_output(compounddatatype=case.mA_out_cdt, dataset_name="mA_out", dataset_idx=1)
    X1_in_raw = pX_raw.create_input(compounddatatype=None, dataset_name="pX_in", dataset_idx=1)
    step_X1_raw = pX_raw.steps.create(transformation=mA_raw, step_num=1)
    step_X1_raw.cables_in.create(dest=mA_in_raw, source_step=0, source=X1_in_raw)
    pX_raw.create_outcable(output_name="pX_out", output_idx=1, source_step=1, source=mA_out_raw)
    pX_raw.create_outputs()


def execute_tests_environment_load(case):
    case.myUser = User.objects.get(is_staff=False)
    case.mf = MethodFamily.objects.get()
    case.pf = PipelineFamily.objects.get()
    case.mA_cr = CodeResource.objects.get()
    case.mA_crr = CodeResourceRevision.objects.get()
    case.string_dt = Datatype.objects.get(pk=datatypes.STR_PK)
    case.int_dt = Datatype.objects.get(pk=datatypes.INT_PK)
    case.pX_in_cdt = CompoundDatatype.objects.get(members__column_name='pX_a')
    case.pX_in_cdtm_1 = case.pX_in_cdt.members.get(column_name="pX_a")
    case.pX_in_cdtm_2 = case.pX_in_cdt.members.get(column_name="pX_b")
    case.pX_in_cdtm_3 = case.pX_in_cdt.members.get(column_name="pX_c")

    case.mA_in_cdt = CompoundDatatype.objects.get(members__column_name='a')
    case.mA_in_cdtm_1 = case.mA_in_cdt.members.get(column_name="a")
    case.mA_in_cdtm_2 = case.mA_in_cdt.members.get(column_name="b")

    case.mA_out_cdt = CompoundDatatype.objects.get(members__column_name='c')
    case.mA_out_cdtm_1 = case.mA_out_cdt.members.get(column_name="c")
    case.mA_out_cdtm_2 = case.mA_out_cdt.members.get(column_name="d")

    case.dataset = Dataset.objects.get(
        structure__compounddatatype=case.pX_in_cdt)
    case.raw_dataset = Dataset.objects.get(structure__isnull=True)
    case.mA = Method.objects.get(revision_name="mA")
    case.mA_in = case.mA.inputs.get()
    case.mA_out = case.mA.outputs.get()

    case.pX = Pipeline.objects.get(revision_name="pX_revision")
    case.X1_in = case.pX.inputs.get()
    case.step_X1 = case.pX.steps.get()

    case.cable_X1_A1 = case.step_X1.cables_in.get()
    case.wire1 = case.cable_X1_A1.custom_wires.get(source_pin=case.pX_in_cdtm_2)
    case.wire2 = case.cable_X1_A1.custom_wires.get(source_pin=case.pX_in_cdtm_3)

    case.X1_outcable = case.pX.outcables.get()
    case.pX_raw = Pipeline.objects.get(revision_name="pX_raw")


@skipIfDBFeature('is_mocked')
class ExecuteTestsBase(BaseTestCases.SlurmExecutionTestCase):
    fixtures = ['execute_tests']

    def setUp(self):
        install_fixture_files("execute_tests")
        execute_tests_environment_load(self)

    def tearDown(self):
        clean_up_all_files()
        remove_fixture_files()

    def find_raw_pipeline(self, user):
        """Find a Pipeline with a raw input."""
        for p in Pipeline.filter_by_user(user):
            for input in p.inputs.all():
                if input.is_raw():
                    return p

    def find_nonraw_pipeline(self, user):
        """Find a Pipeline with no raw input."""
        for p in Pipeline.filter_by_user(user):
            none_raw = True
            for input in p.inputs.all():
                if input.is_raw():
                    none_raw = False
                    break
            if none_raw:
                return p

    def find_inputs_for_pipeline(self, pipeline):
        """Find appropriate input Datasets for a Pipeline."""
        input_datasets = []
        pipeline_owner = pipeline.user
        for input in pipeline.inputs.all():
            if input.is_raw():
                candidate_datasets = Dataset.objects.filter(user=pipeline_owner)
                if candidate_datasets.exists():
                    for dataset in candidate_datasets:
                        if dataset.is_raw():
                            dataset = dataset
                            break
                else:
                    dataset = None
            else:
                datatype = input.structure.compounddatatype
                structure = DatasetStructure.objects.filter(
                    compounddatatype=datatype, dataset__user=pipeline_owner)
                if structure.exists():
                    dataset = structure.first().dataset
                else:
                    dataset = None
            input_datasets.append(dataset)
        return input_datasets


class ExecuteTests(ExecuteTestsBase):

    def test_pipeline_execute_A_simple_onestep_pipeline(self):
        """Execution of a one-step pipeline."""

        # Execute pipeline
        pipeline = self.pX
        inputs = [self.dataset]
        run = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()

        self.check_run_OK(run)

    def test_pipeline_execute_B_twostep_pipeline_with_recycling(self):
        """Two step pipeline with second step identical to the first"""

        # Define pipeline containing two steps with the same method + pipeline input
        self.pX = Pipeline(family=self.pf, revision_name="pX_revision", revision_desc="X", user=self.myUser)
        self.pX.save()
        self.X1_in = self.pX.create_input(compounddatatype=self.pX_in_cdt, dataset_name="pX_in", dataset_idx=1)
        self.step_X1 = self.pX.steps.create(transformation=self.mA, step_num=1)
        self.step_X2 = self.pX.steps.create(transformation=self.mA, step_num=2)

        # Use the SAME custom cable from pipeline input to steps 1 and 2
        self.cable_X1_A1 = self.step_X1.cables_in.create(dest=self.mA_in, source_step=0, source=self.X1_in)
        self.wire1 = self.cable_X1_A1.custom_wires.create(source_pin=self.pX_in_cdtm_2, dest_pin=self.mA_in_cdtm_2)
        self.wire2 = self.cable_X1_A1.custom_wires.create(source_pin=self.pX_in_cdtm_3, dest_pin=self.mA_in_cdtm_1)
        self.cable_X1_A2 = self.step_X2.cables_in.create(dest=self.mA_in, source_step=0, source=self.X1_in)
        self.wire3 = self.cable_X1_A2.custom_wires.create(source_pin=self.pX_in_cdtm_2, dest_pin=self.mA_in_cdtm_2)
        self.wire4 = self.cable_X1_A2.custom_wires.create(source_pin=self.pX_in_cdtm_3, dest_pin=self.mA_in_cdtm_1)

        # POCs: one is trivial, the second uses custom outwires
        # Note: by default, create_outcables assumes the POC has the CDT of the source (IE, this is a TRIVIAL cable)
        self.outcable_1 = self.pX.create_outcable(output_name="pX_out_1",
                                                  output_idx=1,
                                                  source_step=1,
                                                  source=self.mA_out)
        self.outcable_2 = self.pX.create_outcable(output_name="pX_out_2",
                                                  output_idx=2,
                                                  source_step=2,
                                                  source=self.mA_out)

        # Define CDT for the second output (first output is defined by a trivial cable)
        self.pipeline_out2_cdt = CompoundDatatype(user=self.myUser)
        self.pipeline_out2_cdt.save()
        self.out2_cdtm_1 = self.pipeline_out2_cdt.members.create(column_name="c", column_idx=1, datatype=self.int_dt)
        self.out2_cdtm_2 = self.pipeline_out2_cdt.members.create(column_name="d", column_idx=2, datatype=self.string_dt)
        self.out2_cdtm_3 = self.pipeline_out2_cdt.members.create(column_name="e", column_idx=3, datatype=self.string_dt)

        # Second cable is not a trivial - we assign the new CDT to it
        self.outcable_2.output_cdt = self.pipeline_out2_cdt
        self.outcable_2.save()

        # Define custom outwires to the second output (Wire twice from cdtm 2)
        self.outwire1 = self.outcable_2.custom_wires.create(source_pin=self.mA_out_cdtm_1, dest_pin=self.out2_cdtm_1)
        self.outwire2 = self.outcable_2.custom_wires.create(source_pin=self.mA_out_cdtm_2, dest_pin=self.out2_cdtm_2)
        self.outwire3 = self.outcable_2.custom_wires.create(source_pin=self.mA_out_cdtm_2, dest_pin=self.out2_cdtm_3)

        # Have the cables define the TOs of the pipeline
        self.pX.create_outputs()

        # Execute pipeline
        pipeline = self.pX
        inputs = [self.dataset]
        run = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()

        self.check_run_OK(run)

    def test_pipeline_execute_C_twostep_pipeline_with_subpipeline(self):
        """Two step pipeline with second step identical to the first"""

        # Define 2 member input and 1 member output CDTs for inner pipeline pY
        self.pY_in_cdt = CompoundDatatype(user=self.myUser)
        self.pY_in_cdt.save()
        self.pY_in_cdtm_1 = self.pY_in_cdt.members.create(column_name="pYA", column_idx=1, datatype=self.int_dt)
        self.pY_in_cdtm_2 = self.pY_in_cdt.members.create(column_name="pYB", column_idx=2, datatype=self.string_dt)

        self.pY_out_cdt = CompoundDatatype(user=self.myUser)
        self.pY_out_cdt.save()
        self.pY_out_cdt_cdtm_1 = self.pY_out_cdt.members.create(column_name="pYC", column_idx=1, datatype=self.int_dt)

        # Define 1-step inner pipeline pY
        self.pY = Pipeline(family=self.pf, revision_name="pY_revision", revision_desc="Y", user=self.myUser)
        self.pY.save()
        self.pY_in = self.pY.create_input(compounddatatype=self.pY_in_cdt, dataset_name="pY_in", dataset_idx=1)

        self.pY_step_1 = self.pY.steps.create(transformation=self.mA, step_num=1)
        self.pY_cable_in = self.pY_step_1.cables_in.create(dest=self.mA_in, source_step=0, source=self.pY_in)
        self.pY_cable_in.custom_wires.create(source_pin=self.pY_in_cdtm_1, dest_pin=self.mA_in_cdtm_2)
        self.pY_cable_in.custom_wires.create(source_pin=self.pY_in_cdtm_2, dest_pin=self.mA_in_cdtm_1)

        self.pY_cable_out = self.pY.outcables.create(
            output_name="pY_out", output_idx=1, source_step=1,
            source=self.mA_out, output_cdt=self.pY_out_cdt
        )
        self.pY_outwire1 = self.pY_cable_out.custom_wires.create(source_pin=self.mA_out_cdtm_1,
                                                                 dest_pin=self.pY_out_cdt_cdtm_1)
        self.pY.create_outputs()

        # Define CDTs for the output of pX
        self.pX_out_cdt_1 = CompoundDatatype(user=self.myUser)
        self.pX_out_cdt_1.save()
        self.pX_out_cdt_1_cdtm_1 = self.pX_out_cdt_1.members.create(column_name="pXq", column_idx=1,
                                                                    datatype=self.int_dt)

        self.pX_out_cdt_2 = CompoundDatatype(user=self.myUser)
        self.pX_out_cdt_2.save()
        self.pX_out_cdt_2_cdtm_1 = self.pX_out_cdt_2.members.create(
            column_name="pXr", column_idx=1, datatype=self.string_dt
        )

        # Define outer 2-step pipeline with mA at step 1 and pY at step 2
        self.pX = Pipeline(family=self.pf, revision_name="pX_revision", revision_desc="X", user=self.myUser)
        self.pX.save()
        self.X1_in = self.pX.create_input(compounddatatype=self.pX_in_cdt, dataset_name="pX_in", dataset_idx=1)
        self.pX_step_1 = self.pX.steps.create(transformation=self.mA, step_num=1)
        self.pX_step_2 = self.pX.steps.create(transformation=self.pY, step_num=2)

        self.pX_step_1_cable = self.pX_step_1.cables_in.create(dest=self.mA_in, source_step=0, source=self.X1_in)
        self.pX_step_1_cable.custom_wires.create(source_pin=self.pX_in_cdtm_2, dest_pin=self.mA_in_cdtm_2)
        self.pX_step_1_cable.custom_wires.create(source_pin=self.pX_in_cdtm_3, dest_pin=self.mA_in_cdtm_1)

        self.pX_step_2_cable = self.pX_step_2.cables_in.create(dest=self.pY_in, source_step=1, source=self.mA_out)
        self.pX_step_2_cable.custom_wires.create(source_pin=self.mA_out_cdtm_1, dest_pin=self.pY_in_cdtm_1)
        self.pX_step_2_cable.custom_wires.create(source_pin=self.mA_out_cdtm_2, dest_pin=self.pY_in_cdtm_2)

        self.pX_outcable_1 = self.pX.outcables.create(
            output_name="pX_out_1", output_idx=1, source_step=1,
            source=self.mA_out, output_cdt=self.pX_out_cdt_2
        )
        self.pX_outcable_1.custom_wires.create(source_pin=self.mA_out_cdtm_2, dest_pin=self.pX_out_cdt_2_cdtm_1)

        self.pX_outcable_2 = self.pX.outcables.create(
            output_name="pX_out_2", output_idx=2, source_step=2,
            source=self.pY.outputs.get(dataset_name="pY_out"), output_cdt=self.pX_out_cdt_1)
        self.pX_outcable_2.custom_wires.create(
            source_pin=self.pY.outputs.get(dataset_name="pY_out").get_cdt().members.get(column_name="pYC"),
            dest_pin=self.pX_out_cdt_1_cdtm_1
        )

        self.pX.create_outputs()

        # Dataset for input during execution of pipeline
        input_dataset = Dataset.create_dataset(
            os.path.join(samplecode_path, "input_for_test_C_twostep_with_subpipeline.csv"),
            user=self.myUser,
            cdt=self.pX_in_cdt,
            keep_file=True,
            name="input_dataset",
            description="dataset description"
        )

        # Execute pipeline
        pipeline = self.pX
        inputs = [input_dataset]
        run = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()
        self.check_run_OK(run)

    def test_pipeline_all_inputs_OK_nonraw(self):
        """Execute a Pipeline with OK non-raw inputs."""
        pipeline = self.find_nonraw_pipeline(self.myUser)
        inputs = self.find_inputs_for_pipeline(pipeline)
        self.assertTrue(all(i.is_OK() for i in inputs))
        self.assertFalse(all(i.is_raw() for i in inputs))
        run = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()

        self.check_run_OK(run)

        # Check the full is_complete and is_successful.
        self.assertTrue(run.is_complete())
        self.assertTrue(run.is_successful())

        self.assertIsNone(run.clean())
        self.assertIsNone(run.complete_clean())

    def test_pipeline_all_inputs_OK_raw(self):
        """Execute a Pipeline with OK raw inputs."""
        # Find a Pipeline with a raw input.
        pipeline = self.find_raw_pipeline(self.myUser)
        self.assertIsNotNone(pipeline)
        inputs = self.find_inputs_for_pipeline(pipeline)
        self.assertTrue(all(i.is_OK() for i in inputs))
        self.assertTrue(any(i.is_raw() for i in inputs))
        run = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()

        self.check_run_OK(run)

        self.assertTrue(run.is_complete())
        self.assertTrue(run.is_successful())

        self.assertIsNone(run.clean())
        self.assertIsNone(run.complete_clean())

    def test_pipeline_all_inputs_initially_OK(self):
        """Execute a Pipeline with inputs that were initially OK but have a failed integrity check."""
        pipeline = self.find_nonraw_pipeline(self.myUser)
        inputs = self.find_inputs_for_pipeline(pipeline)
        self.assertTrue(all(i.is_OK() for i in inputs))
        self.assertFalse(all(i.is_raw() for i in inputs))

        # Spoil one of the inputs with a bad integrity check.
        now = timezone.now()
        for i, dataset in enumerate(inputs, start=1):
            bad_input = dataset
            bad_icl = IntegrityCheckLog(dataset=dataset,
                                        user=self.myUser,
                                        start_time=now,
                                        end_time=now)
            bad_icl.save()
            MD5Conflict(integritychecklog=bad_icl, conflicting_dataset=dataset).save()
            break
        self.assertFalse(bad_input.is_OK())

        run = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()

        self.check_run_OK(run)

        # Check the full is_complete and is_successful.
        self.assertTrue(run.is_complete())
        self.assertTrue(run.is_successful())

        self.assertIsNone(run.clean())
        self.assertIsNone(run.complete_clean())

        # The spoiled input should have been re-validated.
        bad_input.refresh_from_db()
        self.assertTrue(bad_input.is_OK())

    def test_pipeline_inputs_not_initially_OK(self):
        """Can't execute a Pipeline with non-OK non-raw inputs."""
        pipeline = self.find_nonraw_pipeline(self.myUser)
        inputs = self.find_inputs_for_pipeline(pipeline)
        self.assertTrue(all(i.is_OK() for i in inputs))
        self.assertFalse(all(i.is_raw() for i in inputs))

        for i, dataset in enumerate(inputs, start=1):
            if not dataset.is_raw() and dataset.content_checks.count() == 1:
                bad_input, bad_index = dataset, i
                orig_ccl = dataset.content_checks.first()
                orig_ccl.add_missing_output()
                break
        self.assertFalse(bad_input.initially_OK())
        self.assertFalse(bad_input.is_OK())

        self.assertRaisesRegexp(
            ValueError,
            re.escape('Dataset {} passed as input {} to Pipeline "{}" was not initially OK'
                      .format(bad_input, bad_index, pipeline)),
            lambda: Manager.execute_pipeline(self.myUser, pipeline, inputs)
        )

    def test_crr_corrupted(self):
        """
        Test that a Run fails if a CodeResource has been corrupted.
        """
        self.mA_crr.content_file.save("NowCorrupted.dat", ContentFile("CORRUPTED"))
        self.mA_crr.save()

        run = Manager.execute_pipeline(self.myUser, self.pX, [self.dataset]).get_last_run()

        # This Run should have failed right away.
        rs = run.runsteps.first()

        self.assertFalse(rs.log.methodoutput.are_checksums_OK)

        self.assertTrue(rs.is_failed())

        self.assertTrue(run.is_failed())

        for cancelled_rs in run.runsteps.exclude(pk=rs.pk):
            self.assertTrue(cancelled_rs.is_cancelled())

    # FIXME this test revealed issues #534 and #535; when we fix these, revisit this test.
    # def test_filling_in_execrecord_with_incomplete_content_check(self):
    #     """Execution that fills in an ExecRecord that doesn't have a complete content check."""
    #
    #     # Execute pipeline
    #     pipeline = self.pX
    #     inputs = [self.dataset]
    #     run = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()
    #
    #     # This was one step, so we go into that first step and fiddle with the ContentCheckLog.
    #     rs = run.runsteps.first()
    #     ccl_to_alter = rs.execrecord.execrecordouts.first().dataset.content_checks.first()
    #     ccl_to_alter.end_time = None
    #     ccl_to_alter.start_time = None
    #     ccl_to_alter.save()
    #
    #     # Now execute the pipeline again.
    #     run2 = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()
    #     r2s = run2.runsteps.first()
    #     # It should have filled in the same execrecord.
    #     self.assertEquals(r2s.execrecord, rs.execrecord)
    #     # There should be an integrity check and a content check both associated to r2s' log.
    #     self.assertEquals(r2s.log.integrity_check.count(), 1)
    #     self.assertEquals(r2s.log.content_check.count(), 1)

    def test_filling_in_execrecord_with_incomplete_content_check(self):
        """Execution that fills in an ExecRecord that doesn't have a complete content check."""

        # Execute pipeline
        pipeline = self.pX_raw
        inputs = [self.raw_dataset]
        run = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()

        # This was one step, so we go into that first step and fiddle with the ContentCheckLog.
        rs = run.runsteps.first()
        ccl_to_alter = rs.execrecord.execrecordouts.first().dataset.content_checks.first()
        ccl_to_alter.end_time = None
        ccl_to_alter.start_time = None
        ccl_to_alter.save()

        # Now we dummy it up to look like the RunStep never finished, so no RunOutputCable was run
        # and rs is not marked complete.
        roc = run.runoutputcables.first()
        roc.delete()
        rs._complete = False
        rs.save()

        # Now execute the pipeline again.
        run2 = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()
        r2s = run2.runsteps.first()
        # It should have filled in the same execrecord.
        self.assertEquals(r2s.execrecord, rs.execrecord)
        # There should be an integrity check and a content check both associated to r2s' log.
        self.assertEquals(r2s.log.integrity_checks.count(), 1)
        self.assertEquals(r2s.log.content_checks.count(), 1)

    @patch.object(Dataset, "attempt_to_decontaminate_runcomponents_using_as_output", autospec=True,
                  side_effect=Dataset.attempt_to_decontaminate_runcomponents_using_as_output)
    @patch.object(Dataset, "quarantine_runcomponents_using_as_output", autospec=True,
                  side_effect=Dataset.quarantine_runcomponents_using_as_output)
    @patch.object(ExecRecord, "attempt_decontamination", autospec=True,
                  side_effect=ExecRecord.attempt_decontamination)
    @patch.object(ExecRecord, "quarantine_runcomponents", autospec=True,
                  side_effect=ExecRecord.quarantine_runcomponents)
    @patch.object(Run, "attempt_decontamination", autospec=True, side_effect=Run.attempt_decontamination)
    @patch.object(RunComponent, "decontaminate", autospec=True, side_effect=RunComponent.decontaminate)
    @patch.object(Run, "quarantine", autospec=True, side_effect=Run.quarantine)
    @patch.object(Run, "mark_failure", autospec=True, side_effect=Run.mark_failure)
    @patch.object(Run, "stop", autospec=True, side_effect=Run.stop)
    @patch.object(Run, "start", autospec=True, side_effect=Run.start)
    @patch.object(RunComponent, "cancel_running", autospec=True, side_effect=RunComponent.cancel_running)
    @patch.object(RunComponent, "cancel_pending", autospec=True, side_effect=RunComponent.cancel_pending)
    @patch.object(RunComponent, "begin_recovery", autospec=True, side_effect=RunComponent.begin_recovery)
    @patch.object(RunComponent, "quarantine", autospec=True, side_effect=RunComponent.quarantine)
    @patch.object(RunComponent, "finish_failure", autospec=True, side_effect=RunComponent.finish_failure)
    @patch.object(RunComponent, "finish_successfully", autospec=True, side_effect=RunComponent.finish_successfully)
    @patch.object(RunComponent, "start", autospec=True, side_effect=RunComponent.start)
    def test_execution_decontaminates_quarantined_runsteps(
            self,
            mock_start,
            mock_finish_successfully,
            mock_finish_failure,
            mock_quarantine,
            mock_begin_recovery,
            mock_cancel_pending,
            mock_cancel_running,
            mock_run_start,
            mock_run_stop,
            mock_run_mark_failure,
            mock_run_quarantine,
            mock_decontaminate,
            mock_run_attempt_decontamination,
            mock_execrecord_quarantine_runcomponents,
            mock_execrecord_attempt_decontamination,
            mock_dataset_quarantine_rcs,
            mock_dataset_attempt_decontamination
    ):
        """
        Executing a pipeline with a quarantined component properly re-validates it.
        """
        # Start by executing a simple one-step pipeline.
        pipeline = self.pX_raw
        inputs = [self.raw_dataset]
        run1 = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()
        self.check_run_OK(run1)

        run1_step1 = run1.runsteps.get(pipelinestep__step_num=1)
        run1_outcable = run1.runoutputcables.first()

        # All of the RunComponents should have been started.
        mock_start.assert_has_calls([
            call(run1_step1),
            call(run1_step1.RSICs.first()),
            call(run1_outcable)
        ])
        self.assertEquals(mock_start.call_count, 3)

        # All of them should have been finished successfully without event.
        mock_finish_successfully.assert_has_calls([
            call(run1_step1.RSICs.first(), save=True),
            call(run1_step1, save=True),
            call(run1_outcable, save=True)
        ])
        self.assertEquals(mock_finish_successfully.call_count, 3)

        # These were not called, so have not been mocked yet.
        self.assertFalse(hasattr(mock_finish_failure, "assert_not_called"))
        self.assertFalse(hasattr(mock_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_begin_recovery, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_pending, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_running, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_decontaminate, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_quarantine_runcomponents, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_dataset_quarantine_rcs, "assert_not_called"))
        self.assertFalse(hasattr(mock_dataset_attempt_decontamination, "assert_not_called"))

        mock_run_start.assert_called_once_with(run1, save=True)
        mock_run_stop.assert_called_once_with(run1, save=True)
        self.assertFalse(hasattr(mock_run_mark_failure, "assert_not_called"))

        mock_run_start.reset_mock()
        mock_run_stop.reset_mock()
        mock_start.reset_mock()
        mock_finish_successfully.reset_mock()

        # Now, let's invalidate the output of the first step.
        run1_rs = run1.runsteps.first()
        run1_outcable = run1.runoutputcables.first()
        step1_orig_output = run1_rs.outputs.first()

        corrupted_contents = "Corrupted file"
        _, temp_file_path = tempfile.mkstemp()
        with open(temp_file_path, "wb") as f:
            f.write(corrupted_contents)
        step1_orig_output.check_integrity(temp_file_path, self.myUser, notify_all=True)
        os.remove(temp_file_path)

        # This should have quarantined run1_rs and run1.
        run1.refresh_from_db()
        run1_rs.refresh_from_db()
        run1_outcable.refresh_from_db()
        self.assertTrue(run1.is_quarantined())
        self.assertTrue(run1_rs.is_quarantined())
        self.assertTrue(run1_outcable.is_quarantined())

        # Check that all the calls were made correctly.
        mock_dataset_quarantine_rcs.assert_called_once_with(step1_orig_output)
        mock_execrecord_quarantine_runcomponents.assert_has_calls(
            [
                call(run1_rs.execrecord),
                call(run1_outcable.execrecord)
            ],
            any_order=True
        )

        mock_quarantine.assert_has_calls(
            [
                call(RunComponent.objects.get(pk=run1_rs.pk), recurse_upward=True, save=True),
                call(RunComponent.objects.get(pk=run1_outcable.pk), recurse_upward=True, save=True)
            ],
            any_order=True
        )
        self.assertEquals(mock_quarantine.call_count, 2)
        mock_run_quarantine.assert_called_once_with(run1, recurse_upward=True, save=True)

        # These still haven't been mocked yet after being reset.
        self.assertFalse(hasattr(mock_start, "assert_not_called"))
        self.assertFalse(hasattr(mock_finish_successfully, "assert_not_called"))
        self.assertFalse(hasattr(mock_finish_failure, "assert_not_called"))
        self.assertFalse(hasattr(mock_begin_recovery, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_pending, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_running, "assert_not_called"))
        self.assertFalse(hasattr(mock_decontaminate, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_dataset_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_start, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_stop, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_mark_failure, "assert_not_called"))

        mock_dataset_quarantine_rcs.reset_mock()
        mock_execrecord_quarantine_runcomponents.reset_mock()
        mock_quarantine.reset_mock()
        mock_run_quarantine.reset_mock()

        # Now, we try it again.
        run2 = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()
        # It should have re-validated run1 and run1_rs.
        run1.refresh_from_db()
        run1_rs.refresh_from_db()
        run1_outcable.refresh_from_db()
        self.assertTrue(run1.is_successful())
        self.assertTrue(run1_rs.is_successful())
        self.assertTrue(run1_outcable.is_successful())

        self.check_run_OK(run2)
        run2_step1 = run2.runsteps.first()
        run2_outcable = run2.runoutputcables.first()

        # Check all calls.
        mock_start.assert_has_calls([
            call(run2_step1),
            call(run2_step1.RSICs.first()),
            call(run2_outcable)
        ])
        self.assertEquals(mock_start.call_count, 3)

        # All of them should have been finished successfully without event.
        mock_finish_successfully.assert_has_calls([
            call(run2_step1.RSICs.first(), save=True),
            call(run2_step1, save=True),
            call(run2_outcable, save=True)
        ])
        self.assertEquals(mock_finish_successfully.call_count, 3)

        # Test the decontamination calls.
        mock_dataset_attempt_decontamination.assert_called_once_with(step1_orig_output)
        mock_execrecord_attempt_decontamination.assert_has_calls(
            [
                call(run1_step1.execrecord, step1_orig_output),
                call(run1_outcable.execrecord, step1_orig_output)
            ],
            any_order=True
        )
        self.assertEquals(mock_execrecord_attempt_decontamination.call_count, 2)

        mock_decontaminate.assert_has_calls(
            [
                call(RunComponent.objects.get(pk=run1_step1.pk), recurse_upward=True, save=True),
                call(RunComponent.objects.get(pk=run1_outcable.pk), recurse_upward=True, save=True)
            ],
            any_order=True
        )
        self.assertEquals(mock_decontaminate.call_count, 2)
        # run1 attempts to decontaminate itself twice; the first time, it doesn't do anything
        # because there's still another RunComponent that's contaminated.
        mock_run_attempt_decontamination.assert_has_calls(
            [
                call(run1, recurse_upward=True, save=True),
                call(run1, recurse_upward=True, save=True)
            ],
            any_order=True
        )
        self.assertEquals(mock_run_attempt_decontamination.call_count, 2)

        # These haven't been mocked.
        self.assertFalse(hasattr(mock_dataset_quarantine_rcs, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_quarantine_runcomponents, "assert_not_called"))
        self.assertFalse(hasattr(mock_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_finish_failure, "assert_not_called"))
        self.assertFalse(hasattr(mock_begin_recovery, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_pending, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_running, "assert_not_called"))

    @patch.object(Dataset, "attempt_to_decontaminate_runcomponents_using_as_output", autospec=True,
                  side_effect=Dataset.attempt_to_decontaminate_runcomponents_using_as_output)
    @patch.object(Dataset, "quarantine_runcomponents_using_as_output", autospec=True,
                  side_effect=Dataset.quarantine_runcomponents_using_as_output)
    @patch.object(ExecRecord, "attempt_decontamination", autospec=True,
                  side_effect=ExecRecord.attempt_decontamination)
    @patch.object(ExecRecord, "quarantine_runcomponents", autospec=True,
                  side_effect=ExecRecord.quarantine_runcomponents)
    @patch.object(Run, "attempt_decontamination", autospec=True, side_effect=Run.attempt_decontamination)
    @patch.object(RunComponent, "decontaminate", autospec=True, side_effect=RunComponent.decontaminate)
    @patch.object(Run, "quarantine", autospec=True, side_effect=Run.quarantine)
    @patch.object(Run, "mark_failure", autospec=True, side_effect=Run.mark_failure)
    @patch.object(Run, "stop", autospec=True, side_effect=Run.stop)
    @patch.object(Run, "start", autospec=True, side_effect=Run.start)
    @patch.object(RunComponent, "cancel_running", autospec=True, side_effect=RunComponent.cancel_running)
    @patch.object(RunComponent, "cancel_pending", autospec=True, side_effect=RunComponent.cancel_pending)
    @patch.object(RunComponent, "begin_recovery", autospec=True, side_effect=RunComponent.begin_recovery)
    @patch.object(RunComponent, "quarantine", autospec=True, side_effect=RunComponent.quarantine)
    @patch.object(RunComponent, "finish_failure", autospec=True, side_effect=RunComponent.finish_failure)
    @patch.object(RunComponent, "finish_successfully", autospec=True, side_effect=RunComponent.finish_successfully)
    @patch.object(RunComponent, "start", autospec=True, side_effect=RunComponent.start)
    def test_execution_decontaminates_quarantined_runcables(
            self,
            mock_start,
            mock_finish_successfully,
            mock_finish_failure,
            mock_quarantine,
            mock_begin_recovery,
            mock_cancel_pending,
            mock_cancel_running,
            mock_run_start,
            mock_run_stop,
            mock_run_mark_failure,
            mock_run_quarantine,
            mock_decontaminate,
            mock_run_attempt_decontamination,
            mock_execrecord_quarantine_runcomponents,
            mock_execrecord_attempt_decontamination,
            mock_dataset_quarantine_rcs,
            mock_dataset_attempt_decontamination
    ):
        """
        Executing a pipeline with a quarantined runcable properly re-validates it.
        """
        # Start by executing a simple one-step pipeline.
        pipeline = self.pX
        inputs = [self.dataset]
        run1 = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()
        self.check_run_OK(run1)

        run1_rs = run1.runsteps.first()
        run1_rs_incable = run1_rs.RSICs.first()
        run1_outcable = run1.runoutputcables.first()

        # All of the RunComponents should have been started.
        mock_start.assert_has_calls([
            call(run1_rs),
            call(run1_rs_incable),
            call(run1_outcable)
        ])
        self.assertEquals(mock_start.call_count, 3)

        # All of them should have been finished successfully without event.
        mock_finish_successfully.assert_has_calls([
            call(run1_rs_incable, save=True),
            call(run1_rs, save=True),
            call(run1_outcable, save=True)
        ])
        self.assertEquals(mock_finish_successfully.call_count, 3)

        # These were not called, so have not been mocked yet.
        self.assertFalse(hasattr(mock_finish_failure, "assert_not_called"))
        self.assertFalse(hasattr(mock_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_begin_recovery, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_pending, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_running, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_decontaminate, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_quarantine_runcomponents, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_dataset_quarantine_rcs, "assert_not_called"))
        self.assertFalse(hasattr(mock_dataset_attempt_decontamination, "assert_not_called"))

        mock_run_start.assert_called_once_with(run1, save=True)
        mock_run_stop.assert_called_once_with(run1, save=True)
        self.assertFalse(hasattr(mock_run_mark_failure, "assert_not_called"))

        mock_run_start.reset_mock()
        mock_run_stop.reset_mock()
        mock_start.reset_mock()
        mock_finish_successfully.reset_mock()

        # Now, let's invalidate the output of the first cable, which is non-trivial.
        incable_orig_output = run1_rs_incable.outputs.first()

        corrupted_contents = "Corrupted file"
        _, temp_file_path = tempfile.mkstemp()
        with open(temp_file_path, "wb") as f:
            f.write(corrupted_contents)
        incable_orig_output.check_integrity(temp_file_path, self.myUser, notify_all=True)
        os.remove(temp_file_path)

        # This should have quarantined run1_rs_incable, and run1, but not run1_rs.
        run1.refresh_from_db()
        run1_rs.refresh_from_db()
        run1_rs_incable.refresh_from_db()
        run1_outcable.refresh_from_db()
        self.assertTrue(run1.is_quarantined())
        self.assertTrue(run1_rs.is_successful())
        self.assertTrue(run1_rs_incable.is_quarantined())

        # Check that all the calls were made correctly.
        mock_dataset_quarantine_rcs.assert_called_once_with(incable_orig_output)
        mock_execrecord_quarantine_runcomponents.assert_called_once_with(run1_rs_incable.execrecord)

        mock_quarantine.assert_called_once_with(RunComponent.objects.get(pk=run1_rs_incable.pk),
                                                recurse_upward=True, save=True)
        mock_run_quarantine.assert_called_once_with(run1, recurse_upward=True, save=True)

        # These still haven't been mocked yet after being reset.
        self.assertFalse(hasattr(mock_start, "assert_not_called"))
        self.assertFalse(hasattr(mock_finish_successfully, "assert_not_called"))
        self.assertFalse(hasattr(mock_finish_failure, "assert_not_called"))
        self.assertFalse(hasattr(mock_begin_recovery, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_pending, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_running, "assert_not_called"))
        self.assertFalse(hasattr(mock_decontaminate, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_dataset_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_start, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_stop, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_mark_failure, "assert_not_called"))

        mock_dataset_quarantine_rcs.reset_mock()
        mock_execrecord_quarantine_runcomponents.reset_mock()
        mock_quarantine.reset_mock()
        mock_run_quarantine.reset_mock()

        # Now, we try it again.
        run2 = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()
        # It should have re-validated run1 and run1_rs.
        run1.refresh_from_db()
        run1_rs.refresh_from_db()
        run1_rs_incable.refresh_from_db()
        self.assertTrue(run1.is_successful())
        self.assertTrue(run1_rs.is_successful())
        self.assertTrue(run1_rs_incable.is_successful())

        # FIXME note that this is affected by issues #534 and #535; the method does not
        # find a suitable ExecRecord.
        self.check_run_OK(run2)
        run2_step1 = run2.runsteps.first()
        run2_outcable = run2.runoutputcables.first()

        # Check all calls.
        mock_start.assert_has_calls([
            call(run2_step1),
            call(run2_step1.RSICs.first()),
            call(run2_outcable)
        ])
        self.assertEquals(mock_start.call_count, 3)

        # All of them should have been finished successfully without event.
        mock_finish_successfully.assert_has_calls([
            call(run2_step1.RSICs.first(), save=True),
            call(run2_step1, save=True),
            call(run2_outcable, save=True)
        ])
        self.assertEquals(mock_finish_successfully.call_count, 3)

        # Test the decontamination calls.
        mock_dataset_attempt_decontamination.assert_called_once_with(incable_orig_output)
        mock_execrecord_attempt_decontamination.assert_called_once_with(run1_rs_incable.execrecord,
                                                                        incable_orig_output)

        mock_decontaminate.assert_called_once_with(RunComponent.objects.get(pk=run1_rs_incable.pk),
                                                   recurse_upward=True, save=True)
        # run1 attempts to decontaminate itself once because there's only
        # one RunComponent contaminated.
        mock_run_attempt_decontamination.assert_called_once_with(run1, recurse_upward=True, save=True)

        # These haven't been mocked.
        self.assertFalse(hasattr(mock_dataset_quarantine_rcs, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_quarantine_runcomponents, "assert_not_called"))
        self.assertFalse(hasattr(mock_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_finish_failure, "assert_not_called"))
        self.assertFalse(hasattr(mock_begin_recovery, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_pending, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_running, "assert_not_called"))

    @patch.object(Dataset, "attempt_to_decontaminate_runcomponents_using_as_output", autospec=True,
                  side_effect=Dataset.attempt_to_decontaminate_runcomponents_using_as_output)
    @patch.object(Dataset, "quarantine_runcomponents_using_as_output", autospec=True,
                  side_effect=Dataset.quarantine_runcomponents_using_as_output)
    @patch.object(ExecRecord, "attempt_decontamination", autospec=True,
                  side_effect=ExecRecord.attempt_decontamination)
    @patch.object(ExecRecord, "quarantine_runcomponents", autospec=True,
                  side_effect=ExecRecord.quarantine_runcomponents)
    @patch.object(Run, "attempt_decontamination", autospec=True, side_effect=Run.attempt_decontamination)
    @patch.object(RunComponent, "decontaminate", autospec=True, side_effect=RunComponent.decontaminate)
    @patch.object(Run, "quarantine", autospec=True, side_effect=Run.quarantine)
    @patch.object(Run, "mark_failure", autospec=True, side_effect=Run.mark_failure)
    @patch.object(Run, "stop", autospec=True, side_effect=Run.stop)
    @patch.object(Run, "start", autospec=True, side_effect=Run.start)
    @patch.object(RunComponent, "cancel_running", autospec=True, side_effect=RunComponent.cancel_running)
    @patch.object(RunComponent, "cancel_pending", autospec=True, side_effect=RunComponent.cancel_pending)
    @patch.object(RunComponent, "begin_recovery", autospec=True, side_effect=RunComponent.begin_recovery)
    @patch.object(RunComponent, "quarantine", autospec=True, side_effect=RunComponent.quarantine)
    @patch.object(RunComponent, "finish_failure", autospec=True, side_effect=RunComponent.finish_failure)
    @patch.object(RunComponent, "finish_successfully", autospec=True, side_effect=RunComponent.finish_successfully)
    @patch.object(RunComponent, "start", autospec=True, side_effect=RunComponent.start)
    def test_execution_external_file_decontaminates_quarantined_runcables(
            self,
            mock_start,
            mock_finish_successfully,
            mock_finish_failure,
            mock_quarantine,
            mock_begin_recovery,
            mock_cancel_pending,
            mock_cancel_running,
            mock_run_start,
            mock_run_stop,
            mock_run_mark_failure,
            mock_run_quarantine,
            mock_decontaminate,
            mock_run_attempt_decontamination,
            mock_execrecord_quarantine_runcomponents,
            mock_execrecord_attempt_decontamination,
            mock_dataset_quarantine_rcs,
            mock_dataset_attempt_decontamination
    ):
        """
        Executing a pipeline on externally-backed data with a quarantined component properly re-validates a RunSIC.
        """
        # Set up a working directory and an externally-backed Dataset.
        self.working_dir = tempfile.mkdtemp()
        self.efd = ExternalFileDirectory(
            name="ExecuteTestsEFD",
            path=self.working_dir
        )
        self.efd.save()
        self.ext_path = "ext.txt"
        self.full_ext_path = os.path.join(self.working_dir, self.ext_path)

        # Copy the contents of self.dataset to an external file and link the Dataset.
        self.raw_dataset.dataset_file.open()
        with self.raw_dataset.dataset_file:
            with open(self.full_ext_path, "wb") as f:
                f.write(self.raw_dataset.dataset_file.read())

        # Create a new externally-backed Dataset.
        externally_backed_ds = Dataset.create_dataset(
            self.full_ext_path,
            user=self.myUser,
            keep_file=False,
            name="ExternallyBackedDS",
            description="Dataset with external data and no internal data",
            externalfiledirectory=self.efd
        )

        # Start by executing a simple one-step pipeline.
        pipeline = self.pX_raw
        inputs = [externally_backed_ds]
        run1 = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()
        self.check_run_OK(run1)

        run1_rs = run1.runsteps.first()
        run1_rs_incable = run1_rs.RSICs.first()
        run1_outcable = run1.runoutputcables.first()

        # All of the RunComponents should have been started.
        mock_start.assert_has_calls([
            call(run1_rs),
            call(run1_rs_incable),
            call(run1_outcable)
        ])
        self.assertEquals(mock_start.call_count, 3)

        # All of them should have been finished successfully without event.
        mock_finish_successfully.assert_has_calls([
            call(run1_rs_incable, save=True),
            call(run1_rs, save=True),
            call(run1_outcable, save=True)
        ])
        self.assertEquals(mock_finish_successfully.call_count, 3)

        # These were not called, so have not been mocked yet.
        self.assertFalse(hasattr(mock_finish_failure, "assert_not_called"))
        self.assertFalse(hasattr(mock_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_begin_recovery, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_pending, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_running, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_decontaminate, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_quarantine_runcomponents, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_dataset_quarantine_rcs, "assert_not_called"))
        self.assertFalse(hasattr(mock_dataset_attempt_decontamination, "assert_not_called"))

        mock_run_start.assert_called_once_with(run1, save=True)
        mock_run_stop.assert_called_once_with(run1, save=True)
        self.assertFalse(hasattr(mock_run_mark_failure, "assert_not_called"))

        mock_run_start.reset_mock()
        mock_run_stop.reset_mock()
        mock_start.reset_mock()
        mock_finish_successfully.reset_mock()

        # Now, let's corrupt the input.
        corrupted_contents = "Corrupted file"
        with open(self.full_ext_path, "wb") as f:
            f.write(corrupted_contents)
        externally_backed_ds.check_integrity(self.full_ext_path, self.myUser, notify_all=True)

        # This should have quarantined run1_rs_incable and run1 but not run1_rs.
        run1.refresh_from_db()
        run1_rs.refresh_from_db()
        run1_rs_incable.refresh_from_db()
        self.assertTrue(run1.is_quarantined())
        self.assertTrue(run1_rs.is_successful())
        self.assertTrue(run1_rs_incable.is_quarantined())

        # Check that all the calls were made correctly.
        mock_dataset_quarantine_rcs.assert_called_once_with(externally_backed_ds)
        mock_execrecord_quarantine_runcomponents.assert_called_once_with(run1_rs_incable.execrecord)

        mock_quarantine.assert_called_once_with(RunComponent.objects.get(pk=run1_rs_incable.pk),
                                                recurse_upward=True, save=True)
        mock_run_quarantine.assert_called_once_with(run1, recurse_upward=True, save=True)

        # These still haven't been mocked yet after being reset.
        self.assertFalse(hasattr(mock_start, "assert_not_called"))
        self.assertFalse(hasattr(mock_finish_successfully, "assert_not_called"))
        self.assertFalse(hasattr(mock_finish_failure, "assert_not_called"))
        self.assertFalse(hasattr(mock_begin_recovery, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_pending, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_running, "assert_not_called"))
        self.assertFalse(hasattr(mock_decontaminate, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_dataset_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_start, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_stop, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_mark_failure, "assert_not_called"))

        mock_dataset_quarantine_rcs.reset_mock()
        mock_execrecord_quarantine_runcomponents.reset_mock()
        mock_quarantine.reset_mock()
        mock_run_quarantine.reset_mock()

        # Now we fix the contents of the Dataset.
        self.raw_dataset.dataset_file.open()
        with self.raw_dataset.dataset_file:
            with open(self.full_ext_path, "wb") as f:
                f.write(self.raw_dataset.dataset_file.read())

        # Now, we try it again.
        run2 = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()
        # It should have re-validated run1 and run1_rs.
        run1.refresh_from_db()
        run1_rs.refresh_from_db()
        run1_rs_incable.refresh_from_db()
        self.assertTrue(run1.is_successful())
        self.assertTrue(run1_rs.is_successful())
        self.assertTrue(run1_rs_incable.is_successful())

        self.check_run_OK(run2)
        run2_step1 = run2.runsteps.first()
        run2_outcable = run2.runoutputcables.first()

        # Check all calls.
        mock_start.assert_has_calls([
            call(run2_step1),
            call(run2_step1.RSICs.first()),
            call(run2_outcable)
        ])
        self.assertEquals(mock_start.call_count, 3)

        # All of them should have been finished successfully without event.
        mock_finish_successfully.assert_has_calls([
            call(run2_step1.RSICs.first(), save=True),
            call(run2_step1, save=True),
            call(run2_outcable, save=True)
        ])
        self.assertEquals(mock_finish_successfully.call_count, 3)

        # Test the decontamination calls.
        mock_dataset_attempt_decontamination.assert_called_once_with(externally_backed_ds)
        mock_execrecord_attempt_decontamination.assert_called_once_with(run1_rs_incable.execrecord,
                                                                        externally_backed_ds)

        mock_decontaminate.assert_called_once_with(RunComponent.objects.get(pk=run1_rs_incable.pk),
                                                   recurse_upward=True, save=True)
        # run1 attempts to decontaminate itself once because there's only
        # one RunComponent contaminated.
        mock_run_attempt_decontamination.assert_called_once_with(run1, recurse_upward=True, save=True)

        # These haven't been mocked.
        self.assertFalse(hasattr(mock_dataset_quarantine_rcs, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_quarantine_runcomponents, "assert_not_called"))
        self.assertFalse(hasattr(mock_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_finish_failure, "assert_not_called"))
        self.assertFalse(hasattr(mock_begin_recovery, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_pending, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_running, "assert_not_called"))

        shutil.rmtree(self.working_dir)

    @patch.object(Dataset, "attempt_to_decontaminate_runcomponents_using_as_output", autospec=True,
                  side_effect=Dataset.attempt_to_decontaminate_runcomponents_using_as_output)
    @patch.object(Dataset, "quarantine_runcomponents_using_as_output", autospec=True,
                  side_effect=Dataset.quarantine_runcomponents_using_as_output)
    @patch.object(ExecRecord, "attempt_decontamination", autospec=True,
                  side_effect=ExecRecord.attempt_decontamination)
    @patch.object(ExecRecord, "quarantine_runcomponents", autospec=True,
                  side_effect=ExecRecord.quarantine_runcomponents)
    @patch.object(Run, "attempt_decontamination", autospec=True, side_effect=Run.attempt_decontamination)
    @patch.object(RunComponent, "decontaminate", autospec=True, side_effect=RunComponent.decontaminate)
    @patch.object(Run, "quarantine", autospec=True, side_effect=Run.quarantine)
    @patch.object(Run, "mark_failure", autospec=True, side_effect=Run.mark_failure)
    @patch.object(Run, "stop", autospec=True, side_effect=Run.stop)
    @patch.object(Run, "start", autospec=True, side_effect=Run.start)
    @patch.object(RunComponent, "cancel_running", autospec=True, side_effect=RunComponent.cancel_running)
    @patch.object(RunComponent, "cancel_pending", autospec=True, side_effect=RunComponent.cancel_pending)
    @patch.object(RunComponent, "begin_recovery", autospec=True, side_effect=RunComponent.begin_recovery)
    @patch.object(RunComponent, "quarantine", autospec=True, side_effect=RunComponent.quarantine)
    @patch.object(RunComponent, "finish_failure", autospec=True, side_effect=RunComponent.finish_failure)
    @patch.object(RunComponent, "finish_successfully", autospec=True, side_effect=RunComponent.finish_successfully)
    @patch.object(RunComponent, "start", autospec=True, side_effect=RunComponent.start)
    def test_execution_external_file_decontaminates_quarantined_runsteps(
            self,
            mock_start,
            mock_finish_successfully,
            mock_finish_failure,
            mock_quarantine,
            mock_begin_recovery,
            mock_cancel_pending,
            mock_cancel_running,
            mock_run_start,
            mock_run_stop,
            mock_run_mark_failure,
            mock_run_quarantine,
            mock_decontaminate,
            mock_run_attempt_decontamination,
            mock_execrecord_quarantine_runcomponents,
            mock_execrecord_attempt_decontamination,
            mock_dataset_quarantine_rcs,
            mock_dataset_attempt_decontamination
    ):
        """
        Executing a pipeline on externally-backed data with a quarantined component properly re-validates RunSteps.
        """
        # Set up a working directory and an externally-backed Dataset.
        self.working_dir = tempfile.mkdtemp()
        self.efd = ExternalFileDirectory(
            name="ExecuteTestsEFD",
            path=self.working_dir
        )
        self.efd.save()
        self.ext_path = "ext.txt"
        self.full_ext_path = os.path.join(self.working_dir, self.ext_path)

        # Copy the contents of self.dataset to an external file and link the Dataset.
        self.raw_dataset.dataset_file.open()
        with self.raw_dataset.dataset_file:
            with open(self.full_ext_path, "wb") as f:
                f.write(self.raw_dataset.dataset_file.read())

        # Create a new externally-backed Dataset.
        externally_backed_ds = Dataset.create_dataset(
            self.full_ext_path,
            user=self.myUser,
            keep_file=False,
            name="ExternallyBackedDS",
            description="Dataset with external data and no internal data",
            externalfiledirectory=self.efd
        )

        # Start by executing a simple one-step pipeline.
        pipeline = self.pX_raw
        inputs = [externally_backed_ds]
        run1 = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()
        self.check_run_OK(run1)

        run1_rs = run1.runsteps.first()
        run1_rs_incable = run1_rs.RSICs.first()
        run1_outcable = run1.runoutputcables.first()

        # All of the RunComponents should have been started.
        mock_start.assert_has_calls([
            call(run1_rs),
            call(run1_rs_incable),
            call(run1_outcable)
        ])
        self.assertEquals(mock_start.call_count, 3)

        # All of them should have been finished successfully without event.
        mock_finish_successfully.assert_has_calls([
            call(run1_rs_incable, save=True),
            call(run1_rs, save=True),
            call(run1_outcable, save=True)
        ])
        self.assertEquals(mock_finish_successfully.call_count, 3)

        # These were not called, so have not been mocked yet.
        self.assertFalse(hasattr(mock_finish_failure, "assert_not_called"))
        self.assertFalse(hasattr(mock_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_begin_recovery, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_pending, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_running, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_decontaminate, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_quarantine_runcomponents, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_dataset_quarantine_rcs, "assert_not_called"))
        self.assertFalse(hasattr(mock_dataset_attempt_decontamination, "assert_not_called"))

        mock_run_start.assert_called_once_with(run1, save=True)
        mock_run_stop.assert_called_once_with(run1, save=True)
        self.assertFalse(hasattr(mock_run_mark_failure, "assert_not_called"))

        mock_run_start.reset_mock()
        mock_run_stop.reset_mock()
        mock_start.reset_mock()
        mock_finish_successfully.reset_mock()

        # Now, let's corrupt the input.
        corrupted_contents = "Corrupted file"
        with open(self.full_ext_path, "wb") as f:
            f.write(corrupted_contents)

        # We also delete the output of the step so that it's forced to run.
        output_ds = run1_rs.outputs.first()
        output_ds.dataset_file.delete(save=True)

        # We do another run.
        run2 = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()

        # This should have quarantined run1_rs_incable and run1 but not run1_rs.
        run1.refresh_from_db()
        run1_rs.refresh_from_db()
        run1_rs_incable.refresh_from_db()
        self.assertTrue(run1.is_quarantined())
        self.assertTrue(run1_rs.is_successful())
        self.assertTrue(run1_rs_incable.is_quarantined())

        run2_rs = run2.runsteps.first()
        run2_rs_incable = run2_rs.RSICs.first()
        self.assertTrue(run2.is_failed())
        self.assertTrue(run2_rs.is_cancelled())
        self.assertTrue(run2_rs_incable.is_cancelled())

        # The step and incable should have been started.
        mock_start.assert_has_calls([
            call(run2_rs),
            call(run2_rs_incable),
        ])
        self.assertEquals(mock_start.call_count, 2)

        # Both are cancelled.
        mock_cancel_running.assert_has_calls([
            call(run2_rs_incable, save=True),
            call(run2_rs, save=True)
        ])
        self.assertEquals(mock_cancel_running.call_count, 2)

        # The run started, was marked a failure, and stopped.
        mock_run_start.assert_called_once_with(run2, save=True)
        mock_run_mark_failure.assert_called_once_with(run2, save=True)
        mock_run_stop.assert_called_once_with(run2, save=True)

        # The input dataset should have called quarantine.
        mock_dataset_quarantine_rcs.assert_called_once_with(externally_backed_ds)
        mock_execrecord_quarantine_runcomponents.assert_called_once_with(run2_rs_incable.execrecord)
        mock_quarantine.assert_called_once_with(
            RunComponent.objects.get(pk=run1_rs_incable.pk), recurse_upward=True, save=True
        )
        mock_run_quarantine.assert_called_once_with(run1, recurse_upward=True, save=True)

        # These were not called, so have not been mocked yet.
        self.assertFalse(hasattr(mock_finish_successfully, "assert_not_called"))
        self.assertFalse(hasattr(mock_finish_failure, "assert_not_called"))
        self.assertFalse(hasattr(mock_begin_recovery, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_pending, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_decontaminate, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_attempt_decontamination, "assert_not_called"))
        self.assertFalse(hasattr(mock_dataset_attempt_decontamination, "assert_not_called"))

        mock_run_start.reset_mock()
        mock_run_stop.reset_mock()
        mock_run_mark_failure.reset_mock()
        mock_start.reset_mock()
        mock_finish_successfully.reset_mock()
        mock_dataset_quarantine_rcs.reset_mock()
        mock_execrecord_quarantine_runcomponents.reset_mock()
        mock_quarantine.reset_mock()
        mock_run_quarantine.reset_mock()

        # Now we fix the contents of the Dataset.
        self.raw_dataset.dataset_file.open()
        with self.raw_dataset.dataset_file:
            with open(self.full_ext_path, "wb") as f:
                f.write(self.raw_dataset.dataset_file.read())

        # Now, we try it again.
        run3 = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()
        # It should have re-validated run1 and run1_rs.
        run1.refresh_from_db()
        run1_rs.refresh_from_db()
        run1_rs_incable.refresh_from_db()
        self.assertTrue(run1.is_successful())
        self.assertTrue(run1_rs.is_successful())
        self.assertTrue(run1_rs_incable.is_successful())

        # The stuff from run2 should be left alone.
        run2.refresh_from_db()
        run2_rs.refresh_from_db()
        run2_rs_incable.refresh_from_db()
        self.assertTrue(run2.is_failed())
        self.assertTrue(run2_rs.is_cancelled())
        self.assertTrue(run2_rs_incable.is_cancelled())

        self.check_run_OK(run3)
        run3_rs = run3.runsteps.first()
        run3_rs_incable = run3_rs.RSICs.first()
        run3_outcable = run3.runoutputcables.first()

        # Check all calls.
        mock_start.assert_has_calls([
            call(run3_rs),
            call(run3_rs_incable),
            call(run3_outcable)
        ])
        self.assertEquals(mock_start.call_count, 3)

        # All of them should have been finished successfully without event.
        mock_finish_successfully.assert_has_calls([
            call(run3_rs_incable, save=True),
            call(run3_rs, save=True),
            call(run3_outcable, save=True)
        ])
        self.assertEquals(mock_finish_successfully.call_count, 3)

        # Test the decontamination calls.
        mock_dataset_attempt_decontamination.assert_called_once_with(externally_backed_ds)
        mock_execrecord_attempt_decontamination.assert_called_once_with(run1_rs_incable.execrecord,
                                                                        externally_backed_ds)

        mock_decontaminate.assert_called_once_with(RunComponent.objects.get(pk=run1_rs_incable.pk),
                                                   recurse_upward=True, save=True)
        # run1 attempts to decontaminate itself once.
        mock_run_attempt_decontamination.assert_called_once_with(run1, recurse_upward=True, save=True)

        # These haven't been mocked.
        self.assertFalse(hasattr(mock_dataset_quarantine_rcs, "assert_not_called"))
        self.assertFalse(hasattr(mock_execrecord_quarantine_runcomponents, "assert_not_called"))
        self.assertFalse(hasattr(mock_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_run_quarantine, "assert_not_called"))
        self.assertFalse(hasattr(mock_finish_failure, "assert_not_called"))
        self.assertFalse(hasattr(mock_begin_recovery, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_pending, "assert_not_called"))
        self.assertFalse(hasattr(mock_cancel_running, "assert_not_called"))

        shutil.rmtree(self.working_dir)


class ExecuteSandboxPathWithSpacesTests(ExecuteTestsBase):

    def setUp(self):
        ExecuteTestsBase.setUp(self)
        self.sandbox_path_original = settings.SANDBOX_PATH
        settings.SANDBOX_PATH = "Sandbox path with spaces"

    def tearDown(self):
        ExecuteTestsBase.tearDown(self)
        settings.SANDBOX_PATH = self.sandbox_path_original

    def test_pipeline_sandbox_path_has_spaces(self):
        """Execute a Pipeline in a sandbox that has spaces in the path."""
        pipeline = self.find_nonraw_pipeline(self.myUser)
        inputs = self.find_inputs_for_pipeline(pipeline)
        self.assertTrue(all(i.is_OK() for i in inputs))
        self.assertFalse(all(i.is_raw() for i in inputs))
        run = Manager.execute_pipeline(self.myUser, pipeline, inputs).get_last_run()

        self.check_run_OK(run)

        # Check the full is_complete and is_successful.
        self.assertTrue(run.is_complete())
        self.assertTrue(run.is_successful())

        self.assertIsNone(run.clean())
        self.assertIsNone(run.complete_clean())


class SandboxTests(ExecuteTestsBase):

    def test_sandbox_no_input(self):
        """
        A Sandbox cannot be created if the pipeline has inputs but none are supplied.
        """
        p = Pipeline(family=self.pf, revision_name="blah", revision_desc="blah blah", user=self.myUser)
        p.save()
        p.create_input(compounddatatype=self.pX_in_cdt, dataset_name="in", dataset_idx=1)
        self.assertRaisesRegexp(ValueError,
                                re.escape('Pipeline "{}" expects 1 inputs, but 0 were supplied'.format(p)),
                                lambda: Manager.execute_pipeline(self.myUser, p, []))

    def test_sandbox_too_many_inputs(self):
        """
        A Sandbox cannot be created if the pipeline has fewer inputs than are supplied.
        """
        p = Pipeline(family=self.pf, revision_name="blah", revision_desc="blah blah", user=self.myUser)
        p.save()
        self.assertRaisesRegexp(ValueError,
                                re.escape('Pipeline "{}" expects 0 inputs, but 1 were supplied'.format(p)),
                                lambda: Manager.execute_pipeline(self.myUser, p, [self.dataset]))

    def test_sandbox_correct_inputs(self):
        """
        We can create a Sandbox if the supplied inputs match the pipeline inputs.
        """
        p = Pipeline(family=self.pf, revision_name="blah", revision_desc="blah blah", user=self.myUser)
        p.save()
        p.create_input(compounddatatype=self.pX_in_cdt,
                       dataset_name="in",
                       dataset_idx=1,
                       min_row=8,
                       max_row=12)
        # Assert no ValueError raised.
        Manager.execute_pipeline(self.myUser, p, [self.dataset])

    def test_sandbox_raw_expected_nonraw_supplied(self):
        """
        Can't create a Sandbox if the pipeline expects raw input and we give it nonraw.
        """
        p = Pipeline(family=self.pf, revision_name="blah", revision_desc="blah blah", user=self.myUser)
        p.save()
        p.create_input(dataset_name="in", dataset_idx=1)
        self.assertRaisesRegexp(
            ValueError,
            re.escape('Pipeline "{}" expected input {} to be raw, but got one with '
                      'CompoundDatatype "{}"'.format(p, 1, self.dataset.structure.compounddatatype)),
            lambda: Manager.execute_pipeline(self.myUser, p, [self.dataset])
        )

    def test_sandbox_nonraw_expected_raw_supplied(self):
        """
        Can't create a Sandbox if the pipeline expects non-raw input and we give it raw.
        """
        p = Pipeline(family=self.pf, revision_name="blah", revision_desc="blah blah", user=self.myUser)
        p.save()
        p.create_input(compounddatatype=self.pX_in_cdt, dataset_name="in", dataset_idx=1)
        tf = tempfile.NamedTemporaryFile(delete=False)
        tf.write("foo")
        tf.close()
        raw_dataset = Dataset.create_dataset(
            tf.name,
            user=self.myUser,
            name="foo",
            description="bar"
        )
        self.assertRaisesRegexp(
            ValueError,
            re.escape('Pipeline "{}" expected input {} to be of CompoundDatatype "{}", but got raw'
                      .format(p, 1, self.pX_in_cdt)),
            lambda: Manager.execute_pipeline(self.myUser, p, [raw_dataset])
        )
        os.remove(tf.name)

    def test_sandbox_cdt_mismatch(self):
        """
        Can't create a Sandbox if the pipeline expects an input with one CDT
        and we give it the wrong one.
        """
        p = Pipeline(family=self.pf, revision_name="blah", revision_desc="blah blah", user=self.myUser)
        p.save()
        p.create_input(compounddatatype=self.mA_in_cdt, dataset_name="in", dataset_idx=1)
        self.assertRaisesRegexp(ValueError,
                                re.escape('Pipeline "{}" expected input {} to be of CompoundDatatype "{}", but got one '
                                          'with CompoundDatatype "{}"'
                                          .format(p, 1, self.mA_in_cdt, self.dataset.structure.compounddatatype)),
                                lambda: Manager.execute_pipeline(self.myUser, p, [self.dataset]))

    def test_sandbox_too_many_rows(self):
        """
        Can't create a Sandbox if the pipeline expects an input with one CDT
        and we give it the wrong one.
        """
        p = Pipeline(family=self.pf, revision_name="blah", revision_desc="blah blah", user=self.myUser)
        p.save()
        p.create_input(compounddatatype=self.pX_in_cdt,
                       dataset_name="in",
                       dataset_idx=1,
                       min_row=2,
                       max_row=4)
        expected_message = (
            'Pipeline "{}" expected input {} to have between {} and {} rows, '
            'but got one with {}'.format(p, 1, 2, 4, self.dataset.num_rows()))
        self.assertRaisesRegexp(ValueError,
                                re.escape(expected_message),
                                lambda: Manager.execute_pipeline(self.myUser, p, [self.dataset]))

    def test_sandbox_too_few_rows(self):
        """
        Can't create a Sandbox if the pipeline expects an input with one CDT
        and we give it the wrong one.
        """
        p = Pipeline(family=self.pf, revision_name="blah", revision_desc="blah blah", user=self.myUser)
        p.save()
        p.create_input(compounddatatype=self.pX_in_cdt,
                       dataset_name="in",
                       dataset_idx=1,
                       min_row=20)
        expected_message = (
            'Pipeline "{}" expected input {} to have between {} and {} rows, '
            'but got one with {}'.format(p, 1, 20, sys.maxint, self.dataset.num_rows()))
        self.assertRaisesRegexp(ValueError,
                                re.escape(expected_message),
                                lambda: Manager.execute_pipeline(self.myUser, p, [self.dataset]))


@skipIfDBFeature('is_mocked')
class RestoreReusableDatasetTest(TestCase):
    """
    Scenario where an output is marked as reusable, and it needs to be restored.

    There are three methods:
    * sums_and_products - take each row of two integers, calculate sum and
    product, then shuffle all the result rows. This makes it reusable, but not
    deterministic.
    * total_sums - copy the first row, then one more row with the sum of all
    the sums from the remaining rows.
    * total_products - copy the first row, then one more row with the sum of all
    the products from the remaining rows.

    """
    fixtures = ["restore_reusable_dataset"]

    def setUp(self):
        install_fixture_files("restore_reusable_dataset")

    def tearDown(self):
        remove_fixture_files()

    def test_load_run_plan(self):
        pipeline = Pipeline.objects.get(revision_name='sums only')
        dataset = Dataset.objects.get(name='pairs')

        run = Run(user=pipeline.user, pipeline=pipeline)
        run.save()
        run.inputs.create(dataset=dataset, index=1)

        sandbox = Sandbox(run)
        run_plan = RunPlan()
        run_plan.load(sandbox.run, sandbox.inputs)
        step_plans = run_plan.step_plans

        self.assertEqual(sandbox.run, run_plan.run)
        self.assertEqual([StepPlan(1), StepPlan(2)], step_plans)
        self.assertEqual([DatasetPlan(dataset)],
                         step_plans[0].inputs)
        self.assertEqual([DatasetPlan(step_num=1, output_num=1)],
                         step_plans[0].outputs)
        self.assertEqual(step_plans[0].outputs, step_plans[1].inputs)
        self.assertIs(step_plans[0].outputs[0], step_plans[1].inputs[0])

        self.assertEqual([DatasetPlan(dataset)], run_plan.inputs)
        self.assertEqual([DatasetPlan(step_num=2, output_num=1)],
                         run_plan.outputs)

    def test_find_consistent_execution_for_rerun(self):
        pipeline = Pipeline.objects.get(revision_name='sums only')
        input_dataset = Dataset.objects.get(name='pairs')
        step1_output_dataset = Dataset.objects.get(id=2)
        step2_output_dataset = Dataset.objects.get(id=3)

        run = Run(user=pipeline.user, pipeline=pipeline)
        run.save()
        run.inputs.create(dataset=input_dataset, index=1)

        sandbox = Sandbox(run)
        run_plan = RunPlan()
        run_plan.load(sandbox.run, sandbox.inputs)

        run_plan.find_consistent_execution()
        step_plans = run_plan.step_plans

        self.assertEqual([DatasetPlan(step1_output_dataset)],
                         step_plans[0].outputs)
        self.assertEqual([DatasetPlan(step2_output_dataset)],
                         step_plans[1].outputs)

    def test_find_consistent_execution_for_new_run(self):
        pipeline = Pipeline.objects.get(revision_name='sums and products')
        input_dataset = Dataset.objects.get(name='pairs')

        run = Run(user=pipeline.user, pipeline=pipeline)
        run.save()
        run.inputs.create(dataset=input_dataset, index=1)

        sandbox = Sandbox(run)

        run_plan = RunPlan()
        run_plan.load(sandbox.run, sandbox.inputs)

        run_plan.find_consistent_execution()
        step_plans = run_plan.step_plans

        self.assertEqual([DatasetPlan(step_num=1, output_num=1)],
                         step_plans[0].outputs)
        self.assertEqual([DatasetPlan(step_num=2, output_num=1)],
                         step_plans[1].outputs)
        self.assertEqual([DatasetPlan(step_num=3, output_num=1)],
                         step_plans[2].outputs)

    def test_find_consistent_execution_with_missing_output(self):
        pipeline = Pipeline.objects.get(revision_name='sums only')
        pipeline.steps.get(step_num=1).outputs_to_delete.clear()

        input_dataset = Dataset.objects.get(name='pairs')

        run = Run(user=pipeline.user, pipeline=pipeline)
        run.save()
        run.inputs.create(dataset=input_dataset, index=1)

        sandbox = Sandbox(run)

        run_plan = RunPlan()
        run_plan.load(sandbox.run, sandbox.inputs)

        run_plan.find_consistent_execution()
        step_plans = run_plan.step_plans

        self.assertIsNone(step_plans[0].execrecord)

    def test_find_consistent_execution_with_missing_output_of_deterministic_method(self):
        pipeline = Pipeline.objects.get(revision_name='sums only')
        pipeline_step = pipeline.steps.get(step_num=1)
        pipeline_step.outputs_to_delete.clear()
        method = pipeline_step.transformation.definite
        method.reusable = Method.DETERMINISTIC
        method.save()
        method.clean()

        input_dataset = Dataset.objects.get(name='pairs')

        run = Run(user=pipeline.user, pipeline=pipeline)
        run.save()
        run.inputs.create(dataset=input_dataset, index=1)

        sandbox = Sandbox(run)

        run_plan = RunPlan()
        run_plan.load(sandbox.run, sandbox.inputs)

        run_plan.find_consistent_execution()
        step_plans = run_plan.step_plans

        self.assertIsNotNone(step_plans[0].execrecord)


class ExecuteExternalInputTests(ExecuteTestsBase):

    def setUp(self):
        super(ExecuteExternalInputTests, self).setUp()

        self.working_dir = tempfile.mkdtemp()
        self.efd = ExternalFileDirectory(
            name="ExecuteTestsEFD",
            path=self.working_dir
        )
        self.efd.save()
        self.ext_path = "ext.txt"
        self.full_ext_path = os.path.join(self.working_dir, self.ext_path)

    def tearDown(self):
        super(ExecuteExternalInputTests, self).tearDown()
        shutil.rmtree(self.working_dir)

    def test_pipeline_external_file_input(self):
        """Execution of a pipeline whose input is externally-backed."""

        # Copy the contents of self.dataset to an external file and link the Dataset.
        self.raw_dataset.dataset_file.open()
        with self.raw_dataset.dataset_file:
            with open(self.full_ext_path, "wb") as f:
                f.write(self.raw_dataset.dataset_file.read())

        # Create a new externally-backed Dataset.
        external_ds = Dataset.create_dataset(
            self.full_ext_path,
            user=self.myUser,
            keep_file=False,
            name="ExternalDS",
            description="Dataset with external data and no internal data",
            externalfiledirectory=self.efd
        )

        # Execute pipeline
        run = Manager.execute_pipeline(self.myUser, self.pX_raw, [external_ds]).get_last_run()

        self.check_run_OK(run)

    def test_pipeline_external_file_input_deleted(self):
        """Execution of a pipeline whose input is missing."""

        # Copy the contents of self.dataset to an external file and link the Dataset.
        self.raw_dataset.dataset_file.open()
        with self.raw_dataset.dataset_file:
            with open(self.full_ext_path, "wb") as f:
                f.write(self.raw_dataset.dataset_file.read())

        # Create a new externally-backed Dataset.
        external_missing_ds = Dataset.create_dataset(
            self.full_ext_path,
            user=self.myUser,
            keep_file=False,
            name="ExternalMissingDS",
            description="Dataset with missing external data and no internal data",
            externalfiledirectory=self.efd
        )
        # Remove the external file.
        os.remove(self.full_ext_path)

        # Execute pipeline
        run = Manager.execute_pipeline(self.myUser, self.pX_raw, [external_missing_ds]).get_last_run()
        # 2017-07-11, issue #661: the Manager no longer attempts to run a pipeline with a missing
        # input -- get_last_run() will return None in this case.
        self.assertTrue(run is None)
        # The run should be cancelled by the first cable.
        # self.assertTrue(run.is_cancelled())
        # rsic = run.runsteps.get(pipelinestep__step_num=1).RSICs.first()
        # self.assertTrue(rsic.is_cancelled())
        # self.assertTrue(hasattr(rsic, "input_integrity_check"))
        # self.assertTrue(rsic.input_integrity_check.read_failed)

    def test_pipeline_external_file_input_corrupted(self):
        """Execution of a pipeline whose input is corrupted."""

        # Copy the contents of self.dataset to an external file and link the Dataset.
        self.raw_dataset.dataset_file.open()
        with self.raw_dataset.dataset_file:
            with open(self.full_ext_path, "wb") as f:
                f.write(self.raw_dataset.dataset_file.read())

        # Create a new externally-backed Dataset.
        external_corrupted_ds = Dataset.create_dataset(
            self.full_ext_path,
            user=self.myUser,
            keep_file=False,
            name="ExternalCorruptedDS",
            description="Dataset with corrupted external data and no internal data",
            externalfiledirectory=self.efd
        )
        # Tamper with the external file.
        with open(self.full_ext_path, "wb") as f:
            f.write("Corrupted")

        # Execute pipeline
        run = Manager.execute_pipeline(self.myUser, self.pX_raw, [external_corrupted_ds]).get_last_run()

        # The run should fail on the first cable.
        self.assertFalse(run.is_successful())
        rsic = run.runsteps.get(pipelinestep__step_num=1).RSICs.first()
        self.assertFalse(rsic.is_successful())
        self.assertTrue(hasattr(rsic, "input_integrity_check"))
        self.assertTrue(rsic.input_integrity_check.is_md5_conflict())
