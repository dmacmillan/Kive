from unittest import skip, skipIf

from django.test import TestCase, skipIfDBFeature
from django.utils import timezone
from django.contrib.auth.models import User
from django.conf import settings

import tempfile
import shutil
import os.path
import time

from librarian.models import Dataset
import kive.testing_utils as tools
from pipeline.models import Pipeline, PipelineFamily
from kive.tests import install_fixture_files, remove_fixture_files, BaseTestCases
from method.models import Method
from fleet.workers import Manager
from archive.models import Run
from fleet.slurmlib import DummySlurmScheduler
from fleet.dockerlib import DummyDockerHandler, DockerHandler
import file_access_utils


@skipIfDBFeature('is_mocked')
class SandboxRMTestCase(BaseTestCases.SlurmExecutionTestCase):
    def setUp(self):
        tools.create_sandbox_testing_tools_environment(self)

    def tearDown(self):
        tools.destroy_sandbox_testing_tools_environment(self)


@skipIfDBFeature('is_mocked')
class ExecuteResultTestsRM(TestCase):
    """
    Tests on the results of executing Pipelines.
    """
    fixtures = ["execute_result_tests_rm"]

    def setUp(self):
        install_fixture_files("execute_result_tests_rm")
        self.method_complement = Method.objects.get(
            family__name="DNA complement",
            revision_name="v1"
        )

        self.pipeline_complement = Pipeline.objects.get(
            family__name="DNA complement",
            revision_name="v1"
        )
        self.pipeline_reverse = Pipeline.objects.get(
            family__name="DNA reverse",
            revision_name="v1"
        )
        self.pipeline_revcomp = Pipeline.objects.get(
            family__name="DNA revcomp",
            revision_name="v1"
        )

        self.user_alice = User.objects.get(username="alice")

        self.comp_run = self.pipeline_complement.pipeline_instances.order_by("start_time").first()
        self.comp_run_2 = self.pipeline_complement.pipeline_instances.order_by("start_time").last()
        self.reverse_run = self.pipeline_reverse.pipeline_instances.first()
        self.revcomp_run = self.pipeline_revcomp.pipeline_instances.first()

        self.dataset_labdata = Dataset.objects.get(
            name="lab data",
            user=self.user_alice
        )

        # Tracking down CDTs is a pain....
        self.cdt_record = self.method_complement.inputs.first().structure.compounddatatype

    def tearDown(self):
        tools.clean_up_all_files()
        remove_fixture_files()

    def test_execute_pipeline_run(self):
        """
        Check the coherence of Runs created when a pipeline is executed the first time.
        """
        run = self.comp_run
        self.assertEqual(run.user, self.user_alice)
        self.assertEqual(run.start_time < timezone.now(), True)
        self.assertEqual(run.is_complete(), True)
        self.assertEqual(run.parent_runstep, None)
        self.assertEqual(run.complete_clean(), None)

    def test_execute_pipeline_runstep(self):
        """
        Check the coherence of a RunStep created when a Pipeline is executed the first time.
        """
        run = self.comp_run
        # sandbox_complement has only one step, so this is OK.
        runstep = run.runsteps.first()

        self.assertEqual(runstep.run, run)
        self.assertTrue(runstep.start_time < timezone.now())
        self.assertFalse(runstep.reused)
        self.assertTrue(runstep.is_complete())
        self.assertEqual(runstep.complete_clean(), None)
        self.assertFalse(hasattr(runstep, "child_run"))
        self.assertTrue(runstep.is_successful())
        self.assertEqual(runstep.outputs.count(), 1)

    def test_execute_pipeline_dataset_contents(self):
        """
        Test that the content checks, which take place as part of Pipeline
        execution, pass in the ordinary Pipeline execution case.
        """
        run = self.comp_run
        runstep = run.runsteps.first()
        execrecord = runstep.execrecord
        dataset = execrecord.execrecordouts.first().dataset
        check = dataset.content_checks.first()

        self.assertEqual(dataset.content_checks.count(), 1)  # should have been checked once
        self.assertEqual(check.dataset, dataset)
        self.assertEqual(check.end_time is None, False)
        self.assertEqual(check.start_time <= check.end_time, True)
        self.assertEqual(check.start_time.date(), check.end_time.date())
        self.assertEqual(check.is_fail(), False)

    @skip(reason="Fails because Method.submit_code has been removed for docker"
                 " support. Needs to be fixed or removed.")
    def test_execute_pipeline_dataset(self):
        """
        Test the integrity of a Dataset output by a PipelineStep in
        the middle of a Pipeline.
        NOTE: 2017-10-03: this tests fails because Method.submit_code has been removed
        for docker support.
        """
        # Figure out the MD5 of the output file created when the complement method
        # is run on Alice's data to check against the result of the run.
        tmpdir = tempfile.mkdtemp(dir=file_access_utils.sandbox_base_path())
        file_access_utils.configure_sandbox_permissions(tmpdir)
        outfile = os.path.join(tmpdir, "output")
        stdout_path = os.path.join(tmpdir, "stdout.txt")
        stderr_path = os.path.join(tmpdir, "stderr.txt")

        self.method_complement.install(tmpdir)

        # Set up the dummy scheduler.
        slurm_sched_class = DummySlurmScheduler
        slurm_sched_class.slurm_is_alive()

        complement_job_handle = self.method_complement.submit_code(
            tmpdir,
            [self.dataset_labdata.dataset_file.file.name],
            [outfile],
            stdout_path,
            stderr_path,
            slurm_sched_class=slurm_sched_class
        )

        is_done = False
        while not is_done:
            time.sleep(settings.DEFAULT_SLURM_CHECK_INTERVAL)
            accounting_info = DummySlurmScheduler.get_accounting_info([complement_job_handle])
            if len(accounting_info) > 0:
                curr_state = accounting_info[complement_job_handle.job_id]["state"]
                is_done = curr_state == DummySlurmScheduler.COMPLETED

        slurm_sched_class.shutdown()

        labdata_compd_md5 = file_access_utils.compute_md5(open(outfile))
        shutil.rmtree(tmpdir)

        run = self.comp_run
        runstep = run.runsteps.first()
        execrecord = runstep.execrecord
        dataset = execrecord.execrecordouts.first().dataset
        ds = runstep.outputs.first()

        self.assertEqual(dataset.MD5_checksum, labdata_compd_md5)
        self.assertEqual(dataset, ds)
        self.assertEqual(hasattr(dataset, "usurps"), False)
        self.assertEqual(dataset.has_data(), True)
        self.assertEqual(dataset.num_rows(), 10)
        self.assertEqual(dataset.is_raw(), False)
        self.assertEqual(dataset.get_cdt(), self.cdt_record)
        self.assertEqual(dataset.structure.compounddatatype, self.cdt_record)
        self.assertEqual(dataset.structure.num_rows, 10)
        self.assertEqual(dataset.is_OK(), True)

    def test_execute_pipeline_runstep_execrecordout(self):
        """
        Check the coherence of a RunStep's ExecRecord's ExecRecordOut, created
        when a Pipeline is executed the first time.
        """
        run = self.comp_run

        pipelinestep = self.pipeline_complement.steps.first()  # 1 step
        runstep = run.runsteps.first()
        dataset_out = runstep.outputs.first()
        execlog = runstep.log
        execrecord = runstep.execrecord
        execrecordout = execrecord.execrecordouts.first()

        self.assertEqual(execrecordout is None, False)
        self.assertEqual(execrecordout.execrecord, execrecord)
        self.assertEqual(execrecordout.dataset, dataset_out)
        self.assertEqual(execrecordout.generic_output.definite, pipelinestep.transformation.outputs.first())
        self.assertEqual(execrecordout.has_data(), True)
        self.assertEqual(execrecordout.is_OK(), True)
        self.assertNotEqual(None, execlog)

    def test_execute_pipeline_runstep_execrecord(self):
        """
        Check the coherence of a RunStep's ExecRecord, created when a Pipeline
        is executed the first time.
        """
        run = self.comp_run
        runstep = run.runsteps.first()
        execlog = runstep.log
        execrecord = runstep.execrecord
        outputs = self.method_complement.outputs.all()

        self.assertEqual(execrecord.generator, execlog)
        self.assertEqual(execrecord.complete_clean(), None)
        self.assertEqual(execrecord.general_transf(), runstep.pipelinestep.transformation.method)
        self.assertEqual(execrecord.provides_outputs(outputs), True)
        self.assertEqual(execrecord.outputs_OK(), True)

    def test_execute_pipeline_reuse(self):
        """
        An identical pipeline, run in a different sandbox, should reuse an ExecRecord
        and not create an ExecLog.
        """
        step1 = self.comp_run.runsteps.first()
        step2 = self.comp_run_2.runsteps.first()

        self.assertEqual(step1.reused, False)
        self.assertEqual(step2.reused, True)
        self.assertFalse(step2.has_log())
        self.assertEqual(step1.execrecord, step2.execrecord)

    def test_execute_pipeline_fill_in_ER(self):
        """
        Running an identical Pipeline where we did not keep the data around the first time
        should fill in an existing ExecRecord, but also create a new ExecLog.
        """
        step1 = self.comp_run.runsteps.first()
        step2 = self.comp_run_2.runsteps.first()

        self.assertEqual(step1.reused, False)
        self.assertEqual(step2.reused, True)
        self.assertTrue(step1.has_log())
        self.assertFalse(step2.has_log())
        self.assertEqual(step1.execrecord, step2.execrecord)

    def test_execute_pipeline_reuse_within_different_pipeline(self):
        """
        Running the same dataset through the same Method, in two different
        pipelines, should reuse an ExecRecord.
        """
        step1 = self.reverse_run.runsteps.first()  # 1 step
        step2 = self.revcomp_run.runsteps.get(pipelinestep__step_num=1)

        self.assertEqual(step1.reused, False)
        self.assertEqual(step2.reused, True)
        self.assertFalse(step2.has_log())
        self.assertEqual(step1.execrecord, step2.execrecord)

    def test_execute_pipeline_output_dataset(self):
        """
        A Pipeline with no deleted outputs should have a Dataset as an output.
        """
        output = self.comp_run.runoutputcables.first()
        output_dataset = output.execrecord.execrecordouts.first().dataset
        self.assertEqual(output_dataset is not None, True)

    def test_trivial_cable_num_rows(self):
        """
        A trivial cable should have the same dataset all the way through.
        """
        step = self.comp_run.runsteps.first()
        step_output_dataset = step.execrecord.execrecordouts.first().dataset

        outcable = self.comp_run.runoutputcables.first()
        outcable_input_dataset = outcable.execrecord.execrecordins.first().dataset
        outcable_output_dataset = outcable.execrecord.execrecordouts.first().dataset

        self.assertEqual(step_output_dataset, outcable_input_dataset)
        self.assertEqual(outcable_input_dataset, outcable_output_dataset)
        self.assertEqual(step_output_dataset.num_rows(), outcable_input_dataset.num_rows())
        self.assertEqual(outcable_input_dataset.num_rows(), outcable_output_dataset.num_rows())

    def test_execute_pipeline_num_rows(self):
        """
        A pipeline which does not change the number of rows in a dataset,
        should have the same number of rows in all datasets along the way.
        """
        incable = self.comp_run.runsteps.first().RSICs.first()
        incable_input_dataset = incable.execrecord.execrecordins.first().dataset
        incable_output_dataset = incable.execrecord.execrecordins.first().dataset

        step = self.comp_run.runsteps.first()
        step_input_dataset = step.execrecord.execrecordins.first().dataset
        step_output_dataset = step.execrecord.execrecordouts.first().dataset

        outcable = self.comp_run.runoutputcables.first()
        outcable_input_dataset = outcable.execrecord.execrecordins.first().dataset
        outcable_output_dataset = outcable.execrecord.execrecordouts.first().dataset

        self.assertEqual(incable_input_dataset.num_rows(), self.dataset_labdata.num_rows())
        self.assertEqual(incable_input_dataset.num_rows(), incable_output_dataset.num_rows())
        self.assertEqual(incable_output_dataset.num_rows(), step_input_dataset.num_rows())
        self.assertEqual(step_input_dataset.num_rows(), step_output_dataset.num_rows())
        self.assertEqual(step_output_dataset.num_rows(), outcable_input_dataset.num_rows())
        self.assertEqual(outcable_input_dataset.num_rows(), outcable_output_dataset.num_rows())


@skipIfDBFeature('is_mocked')
class ExecuteDiscardedIntermediateTests(BaseTestCases.SlurmExecutionTestCase):
    fixtures = ["execute_discarded_intermediate_tests_rm"]

    def setUp(self):
        install_fixture_files("execute_discarded_intermediate_tests_rm")
        self.revcomp_pf = PipelineFamily.objects.get(name="DNA revcomp")
        self.pipeline_revcomp_v2 = self.revcomp_pf.members.get(revision_name="2")
        self.pipeline_revcomp_v3 = self.revcomp_pf.members.get(revision_name="3")

        self.user_alice = User.objects.get(username="alice")

        self.revcomp_v2_run = self.pipeline_revcomp_v2.pipeline_instances.first()  # only one exists

        self.dataset_labdata = Dataset.objects.get(
            name="lab data",
            user=self.user_alice
        )

    def tearDown(self):
        tools.clean_up_all_files()
        remove_fixture_files()

    def test_discard_intermediate_file(self):
        """
        A Pipeline which indicates one of its intermediate outputs should not be kept,
        should not create any datasets for that output.
        """
        runstep = self.revcomp_v2_run.runsteps.get(pipelinestep__step_num=1)
        output = runstep.execrecord.execrecordouts.first().dataset
        step = self.pipeline_revcomp_v2.steps.get(step_num=1)
        self.assertEqual(runstep.pipelinestep.outputs_to_retain(), [])
        self.assertEqual(output.has_data(), False)
        self.assertNotEqual(None, step)

    def test_recover_intermediate_dataset(self):
        """
        Test recovery of an intermediate dataset.
        """
        # In the fixture, we already ran self.pipeline_revcomp_v2, which discards the intermediate
        # output.  We now run v3, which will recover it.
        run = Manager.execute_pipeline(
            self.user_alice,
            self.pipeline_revcomp_v3,
            [self.dataset_labdata],
            docker_handler_class=DummyDockerHandler
        ).get_last_run()

        self.assertTrue(run.is_successful())


class BadRunTestsBase(object):
    """
    Foundations of tests for when things go wrong during Pipeline execution.

    We split this code out into an object (not a TestCase) so it can be
    reused in another test class.
    """
    def setUp(self):
        tools.create_grandpa_sandbox_environment(self)

    def tearDown(self):
        tools.destroy_grandpa_sandbox_environment(self)

    def cable_tester(self, runstep):
        for rsic in runstep.RSICs.all():
            self.assertTrue(rsic.is_successful())

    def test_method_fails(self,
                          slurm_sched_class=DummySlurmScheduler,
                          docker_handler_class=DummyDockerHandler):
        """Properly handle a failed method in a pipeline."""
        run = Manager.execute_pipeline(
            self.user_grandpa,
            self.pipeline_fubar,
            [self.dataset_grandpa],
            slurm_sched_class=slurm_sched_class,
            docker_handler_class=docker_handler_class
        ).get_last_run()

        self.assertTrue(run.is_failed())
        self.assertIsNone(run.complete_clean())

        runstep1 = run.runsteps.get(pipelinestep__step_num=1)
        self.cable_tester(runstep1)
        self.assertIsNone(runstep1.complete_clean())
        self.assertTrue(runstep1.is_successful())

        runstep2 = run.runsteps.get(pipelinestep__step_num=2)
        self.cable_tester(runstep2)
        self.assertIsNone(runstep2.complete_clean())
        self.assertTrue(runstep2.is_failed())

        log = runstep2.log

        self.assertFalse(log.is_successful())
        self.assertEqual(log.methodoutput.return_code, 1)
        self.assertEqual(log.missing_outputs(), [runstep2.execrecord.execrecordouts.first().dataset])


@skipIfDBFeature('is_mocked')
class BadRunTests(BaseTestCases.SlurmExecutionTestCase, BadRunTestsBase):
    """
    Tests for when things go wrong during Pipeline execution.
    """
    def setUp(self):
        BaseTestCases.SlurmExecutionTestCase.setUp(self)
        BadRunTestsBase.setUp(self)

    def tearDown(self):
        BaseTestCases.SlurmExecutionTestCase.tearDown(self)
        BadRunTestsBase.tearDown(self)

    pass


@skipIfDBFeature('is_mocked')
class FindDatasetTests(BaseTestCases.SlurmExecutionTestCase):
    """
    Tests for first_generator_of_dataset.
    """
    fixtures = ['find_datasets']

    def setUp(self):
        install_fixture_files('find_datasets')

    def tearDown(self):
        remove_fixture_files()

    def test_find_dataset_pipeline_input_and_step_output(self):
        """
        Finding a Dataset which was input to a Pipeline should return None
        as the generator, and the top-level run as the run.

        Finding a Dataset which was output from a step, and also input
        to a cable, should return the step (and in particular, not the cable).
        """
        self.pipeline_noop = Pipeline.objects.get(family__name="simple pipeline")
        self.dataset_words = Dataset.objects.get(name='blahblah')
        self.user_bob = User.objects.get(username='bob')

        mgr = Manager.execute_pipeline(self.user_bob,
                                       self.pipeline_noop,
                                       [self.dataset_words],
                                       docker_handler_class=DummyDockerHandler)
        x = mgr.history_queue.pop()
        self.assertIsNone(x.run.complete_clean())
        self.assertTrue(x.run.is_successful())

        run, gen = x.first_generator_of_dataset(self.dataset_words)
        self.assertEqual(run, x.run)
        self.assertEqual(gen, None)

        dataset_out_intermediate = x.run.runsteps.first().execrecord.execrecordouts.first().dataset
        run_2, gen_2 = x.first_generator_of_dataset(dataset_out_intermediate)
        self.assertEqual(run_2, x.run)
        self.assertEqual(gen_2, self.pipeline_noop.steps.first())

    def test_find_dataset_pipeline_input_and_intermediate_custom_wire(self):
        """
        Finding a Dataset which was passed through a custom wire to a
        Pipeline should return the cable as the generator, and the top-level
        run as the run.

        Finding a Dataset which was produced by a custom wire as an
        intermediate step should return the cable as the generator, and the
        top-level run as the run.
        """
        self.pipeline_twostep = Pipeline.objects.get(family__name="two-step pipeline")
        self.dataset_backwords = Dataset.objects.get(name='backwords')
        self.user_bob = User.objects.get(username='bob')

        mgr = Manager.execute_pipeline(self.user_bob,
                                       self.pipeline_twostep,
                                       [self.dataset_backwords],
                                       docker_handler_class=DummyDockerHandler)
        sandbox = mgr.history_queue.pop()
        self.assertIsNone(sandbox.run.complete_clean())
        self.assertTrue(sandbox.run.is_successful())

        runcable = sandbox.run.runsteps.get(pipelinestep__step_num=1).RSICs.first()
        dataset_to_find = runcable.execrecord.execrecordouts.first().dataset

        run, gen = sandbox.first_generator_of_dataset(dataset_to_find)
        self.assertEqual(run, sandbox.run)
        self.assertEqual(gen, runcable.PSIC)

        # Testing on an intermediate Dataset.
        runcable_2 = sandbox.run.runsteps.get(pipelinestep__step_num=2).RSICs.first()
        dataset_to_find_2 = runcable_2.execrecord.execrecordouts.first().dataset

        run_2, gen_2 = sandbox.first_generator_of_dataset(dataset_to_find_2)
        self.assertEqual(run_2, sandbox.run)
        self.assertEqual(gen_2, runcable_2.PSIC)

    def test_find_dataset_subpipeline_input_and_intermediate(self):
        """
        Find a dataset in a sub-pipeline, which is output from a step.

        Find a dataset in a sub-pipeline, which is input to the sub-pipeline
        on a custom cable.
        """
        self.pipeline_nested = Pipeline.objects.get(family__name="nested pipeline")
        self.dataset_backwords = Dataset.objects.get(name='backwords')
        self.user_bob = User.objects.get(username='bob')

        mgr = Manager.execute_pipeline(self.user_bob,
                                       self.pipeline_nested,
                                       [self.dataset_backwords],
                                       docker_handler_class=DummyDockerHandler)
        sandbox = mgr.history_queue.pop()
        self.assertIsNone(sandbox.run.complete_clean())
        self.assertTrue(sandbox.run.is_successful())

        subpipeline_step = sandbox.run.runsteps.get(pipelinestep__step_num=2)
        subrun = subpipeline_step.child_run
        runstep = subrun.runsteps.first()
        outrecord = runstep.execrecord.execrecordouts.first()
        dataset_to_find = outrecord.dataset

        run, gen = sandbox.first_generator_of_dataset(dataset_to_find)
        self.assertEqual(run, subrun)
        self.assertEqual(gen, runstep.pipelinestep)

        cable = runstep.RSICs.first()
        dataset_to_find_2 = runstep.execrecord.execrecordins.first().dataset

        run_2, gen_2 = sandbox.first_generator_of_dataset(dataset_to_find_2)
        self.assertEqual(run_2, subrun)
        self.assertEqual(gen_2, cable.PSIC)


class RawTests(SandboxRMTestCase):

    def setUp(self):
        super(RawTests, self).setUp()

        self.addTypeEqualityFunc(str, self.assertMultiLineEqual)
        self.pipeline_raw = tools.make_first_pipeline(
            "raw noop", "a pipeline to do nothing to raw data",
            self.user_bob)
        tools.create_linear_pipeline(self.pipeline_raw, [self.method_noop_raw], "raw_in", "raw_out")
        self.pipeline_raw.create_outputs()

        self.dataset_raw = Dataset.create_dataset(
            "/usr/share/dict/words",
            user=self.user_bob,
            cdt=None,
            keep_file=True,
            name="raw",
            description="some raw data"
        )

    def test_execute_pipeline_raw(self):
        """Execute a raw Pipeline."""
        run = Manager.execute_pipeline(self.user_bob,
                                       self.pipeline_raw,
                                       [self.dataset_raw],
                                       docker_handler_class=DummyDockerHandler).get_last_run()
        run.refresh_from_db()
        self.assertTrue(run.is_successful())

    def test_execute_pipeline_raw_twice(self):
        """Execute a raw Pipeline and reuse an ExecRecord."""
        run = Manager.execute_pipeline(self.user_bob,
                                       self.pipeline_raw,
                                       [self.dataset_raw],
                                       docker_handler_class=DummyDockerHandler).get_last_run()
        run = Run.objects.get(pk=run.pk)
        self.assertTrue(run.is_successful())

        run2 = Manager.execute_pipeline(self.user_bob,
                                        self.pipeline_raw,
                                        [self.dataset_raw],
                                        docker_handler_class=DummyDockerHandler).get_last_run()
        run2 = Run.objects.get(pk=run2.pk)
        self.assertTrue(run2.is_successful())

    @skipIf(not settings.RUN_DOCKER_TESTS, "Docker tests disabled.")
    def test_execute_pipeline_raw_with_docker(self):
        """Execute a raw Pipeline."""
        self.maxDiff = None
        run = Manager.execute_pipeline(self.user_bob,
                                       self.pipeline_raw,
                                       [self.dataset_raw],
                                       docker_handler_class=DockerHandler).get_last_run()
        run.refresh_from_db()
        stderr_path = os.path.join(run.sandbox_path,
                                   "step1",
                                   "logs",
                                   "step1_stderr_slurmID0_node0.txt")
        with open(stderr_path, 'rU') as f:
            stderr_text = f.read()
        self.assertEqual("", stderr_text)
        self.assertTrue(run.is_successful())

    def tearDown(self):
        super(RawTests, self).tearDown()
