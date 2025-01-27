"""
Tests on the Stopwatch functionality used in Run, RunAtomic, etc.
"""
import re

from django.test import TestCase, skipIfDBFeature
from django.utils import timezone
from django.core.exceptions import ValidationError

import kive.testing_utils as tools
from pipeline.models import PipelineFamily


@skipIfDBFeature('is_mocked')
class StopwatchTests(TestCase):
    fixtures = ["em_sandbox_test_environment"]

    # The fixture creates self.pE_run, which is a
    # Stopwatch.  We'll use this as our Stopwatch.
    def setUp(self):
        self.pf = PipelineFamily.objects.get(name="Pipeline_family")
        self.pE = self.pf.members.get(revision_name="pE_name")
        self.pE_run = self.pE.pipeline_instances.first()

    def tearDown(self):
        tools.clean_up_all_files()

    def test_neither_set(self):
        """
        Neither start nor end time is set.  Stopwatch should be clean.
        """
        self.assertIsNone(self.pE_run.clean())

    def test_start_set_end_not_set(self):
        """
        start_time set, end_time not set.  This is fine.
        """
        self.pE_run.start(clean=False)
        self.assertIsNone(self.pE_run.clean())

    def test_clean_neither_set(self):
        """
        Neither start nor end time is set.  Stopwatch should be clean.
        """
        self.assertIsNone(self.pE_run.clean())

    def test_clean_start_set_end_unset(self):
        """
        start_time set, end_time not set.  This is fine.
        """
        self.pE_run.start(clean=False)
        self.assertIsNone(self.pE_run.clean())

    def test_clean_start_set_end_set(self):
        """
        start_time set, end_time set afterwards.  This is fine.
        """
        self.pE_run.start(clean=False)
        self.pE_run.stop(clean=False)
        self.assertIsNone(self.pE_run.clean())

    def test_clean_start_unset_end_set(self):
        """
        end_time set and start_time unset.  This is not coherent.
        """
        self.pE_run.end_time = timezone.now()
        self.assertRaisesRegexp(
            ValidationError,
            re.escape('Stopwatch "{}" does not have a start time but it has an end time'.format(self.pE_run)),
            self.pE_run.clean
        )

    def test_has_started_true(self):
        """
        start_time is set.
        """
        self.pE_run.start_time = timezone.now()
        self.assertTrue(self.pE_run.has_started())

    def test_has_started_false(self):
        """
        start_time is unset.
        """
        self.assertFalse(self.pE_run.has_started())

    def test_has_ended_true(self):
        """
        end_time is set.
        """
        self.pE_run.start_time = timezone.now()
        self.pE_run.end_time = timezone.now()
        self.assertTrue(self.pE_run.has_ended())

    def test_has_ended_false(self):
        """
        end_time is unset.
        """
        # First, the neither-set case.
        self.assertFalse(self.pE_run.has_ended())

        # Now, the started-but-not-stopped case
        self.pE_run.start_time = timezone.now()
        self.assertFalse(self.pE_run.has_ended())

    def test_start(self):
        """
        start() sets start_time.
        """
        self.assertFalse(self.pE_run.has_started())
        self.pE_run.start(clean=False)
        self.assertTrue(self.pE_run.has_started())

    def test_stop(self):
        """
        stop() sets end_time.
        """
        self.assertFalse(self.pE_run.has_ended())
        self.pE_run.start(clean=False)
        self.assertFalse(self.pE_run.has_ended())
        self.pE_run.stop(clean=False)
        self.assertTrue(self.pE_run.has_ended())