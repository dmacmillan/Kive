"""
Unit tests for Shipyard method models.
"""

import filecmp
import hashlib
import os.path
import re
import shutil
import tempfile
import copy
import unittest

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.contenttypes.management import update_all_contenttypes
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.management import call_command
from django.core.urlresolvers import resolve

from django.test import TestCase, TransactionTestCase
from rest_framework import status
from rest_framework.reverse import reverse
from rest_framework.test import force_authenticate

from constants import datatypes
import file_access_utils
from kive.tests import BaseTestCases
import librarian.models
from metadata.models import CompoundDatatype, Datatype, everyone_group, kive_user
import metadata.tests
from method.models import CodeResource, CodeResourceDependency, \
    CodeResourceRevision, Method, MethodFamily
from method.serializers import CodeResourceRevisionSerializer, MethodSerializer
import portal.models
import kive.testing_utils as tools
from fleet.workers import Manager
from transformation.models import Transformation


# This was previously defined here but has been moved to metadata.tests.
samplecode_path = metadata.tests.samplecode_path


class FileAccessTests(TransactionTestCase):
    # fixtures = ["initial_groups", "initial_user", "initial_data"]

    def setUp(self):
        tools.fd_count("FDs (start)")

        # Since these fixtures touch ContentType and Permission, loading them in the
        # 'fixtures' attribute doesn't work.
        # update_all_contenttypes(verbosity=0)
        # call_command("flush", interactive=False)
        # auth_app_config = django_apps.get_app_config("auth")
        # create_permissions(auth_app_config, verbosity=0)
        call_command("loaddata", "initial_groups", verbosity=0)
        call_command("loaddata", "initial_user", verbosity=0)
        call_command("loaddata", "initial_data", verbosity=0)

        # A typical user.
        self.user_randy = User.objects.create_user("Randy", "theotherrford@deco.ca", "hat")
        self.user_randy.save()
        self.user_randy.groups.add(everyone_group())
        self.user_randy.save()

        # Define comp_cr
        self.test_cr = CodeResource(
            name="Test CodeResource",
            description="A test CodeResource to play with file access",
            filename="complement.py",
            user=self.user_randy)
        self.test_cr.save()

        # Define compv1_crRev for comp_cr
        self.fn = "complement.py"

    def tearDown(self):
        tools.clean_up_all_files()
        tools.fd_count("FDs (end)")
        update_all_contenttypes(verbosity=0)

    def test_close_save(self):
        with open(os.path.join(samplecode_path, self.fn), "rb") as f:
            tools.fd_count("!close->save")

            test_crr = CodeResourceRevision(
                coderesource=self.test_cr,
                revision_name="v1",
                revision_desc="First version",
                content_file=File(f),
                user=self.user_randy)

        self.assertRaises(ValueError, test_crr.save)

    def test_access_close_save(self):
        with open(os.path.join(samplecode_path, self.fn), "rb") as f:
            test_crr = CodeResourceRevision(
                coderesource=self.test_cr,
                revision_name="v1",
                revision_desc="First version",
                content_file=File(f),
                user=self.user_randy)

            tools.fd_count("!access->close->save")
            test_crr.content_file.read()
            tools.fd_count("access-!>close->save")
        tools.fd_count("access->close-!>save")
        self.assertRaises(ValueError, test_crr.save)
        tools.fd_count("access->close->save!")

    def test_close_access_save(self):
        with open(os.path.join(samplecode_path, self.fn), "rb") as f:
            test_crr = CodeResourceRevision(
                coderesource=self.test_cr,
                revision_name="v1",
                revision_desc="First version",
                content_file=File(f),
                user=self.user_randy)

        self.assertRaises(ValueError, test_crr.content_file.read)
        self.assertRaises(ValueError, test_crr.save)

    def test_save_close_access(self):
        with open(os.path.join(samplecode_path, self.fn), "rb") as f:
            test_crr = CodeResourceRevision(
                coderesource=self.test_cr,
                revision_name="v1",
                revision_desc="First version",
                content_file=File(f),
                user=self.user_randy)
            test_crr.save()

        test_crr.content_file.read()
        tools.fd_count("save->close->access")

    def test_save_close_access_close(self):
        with open(os.path.join(samplecode_path, self.fn), "rb") as f:
            tools.fd_count("open-!>File->save->close->access->close")
            test_crr = CodeResourceRevision(
                coderesource=self.test_cr,
                revision_name="v1",
                revision_desc="First version",
                content_file=File(f),
                user=self.user_randy)
            tools.fd_count("open->File-!>save->close->access->close")
            test_crr.save()
            tools.fd_count("open->File->save-!>close->access->close")

        tools.fd_count("open->File->save->close-!>access->close")
        test_crr.content_file.read()
        tools.fd_count("open->File->save->close->access-!>close")
        test_crr.content_file.close()
        tools.fd_count("open->File->save->close->access->close!")

    def test_save_close_clean_close(self):
        with open(os.path.join(samplecode_path, self.fn), "rb") as f:
            # Compute the reference MD5
            md5gen = hashlib.md5()
            md5gen.update(f.read())
            f_checksum = md5gen.hexdigest()
            f.seek(0)

            tools.fd_count("open-!>File->save->close->clean->close")
            test_crr = CodeResourceRevision(
                coderesource=self.test_cr,
                revision_name="v1",
                revision_desc="First version",
                content_file=File(f),
                MD5_checksum = f_checksum,
                user=self.user_randy)


            tools.fd_count("open->File-!>save->close->clean->close")
            test_crr.save()
            tools.fd_count("open->File->save-!>close->clean->close")

        tools.fd_count("open->File->save->close-!>clean->close")
        test_crr.clean()
        tools.fd_count("open->File->save->close->clean-!>close")
        test_crr.content_file.close()
        tools.fd_count("open->File->save->close->clean->close!")

    def test_clean_save_close(self):
        with open(os.path.join(samplecode_path, self.fn), "rb") as f:
            tools.fd_count("open-!>File->clean->save->close")
            test_crr = CodeResourceRevision(
                coderesource=self.test_cr,
                revision_name="v1",
                revision_desc="First version",
                content_file=File(f),
                user=self.user_randy)
            tools.fd_count("open->File-!>clean->save->close")
            test_crr.clean()
            tools.fd_count("open->File->clean-!>save->close")
            test_crr.save()
            tools.fd_count("open->File->clean->save-!>close")
        tools.fd_count("open->File->clean->save->close!")

    def test_clean_save_close_clean_close(self):
        with open(os.path.join(samplecode_path, self.fn), "rb") as f:

            tools.fd_count("open-!>File->clean->save->close->clean->close")
            test_crr = CodeResourceRevision(
                coderesource=self.test_cr,
                revision_name="v1",
                revision_desc="First version",
                content_file=File(f),
                user=self.user_randy)
            tools.fd_count("open->File-!>clean->save->close->clean->close")
            tools.fd_count_logger.debug("FieldFile is open: {}".format(not test_crr.content_file.closed))
            test_crr.clean()
            tools.fd_count("open->File->clean-!>save->close->clean->close")
            tools.fd_count_logger.debug("FieldFile is open: {}".format(not test_crr.content_file.closed))
            test_crr.save()
            tools.fd_count("open->File->clean->save-!>close->clean->close")
            tools.fd_count_logger.debug("FieldFile is open: {}".format(not test_crr.content_file.closed))

        tools.fd_count("open->File->clean->save->close-!>clean->close")
        tools.fd_count_logger.debug("FieldFile is open: {}".format(not test_crr.content_file.closed))
        test_crr.clean()
        tools.fd_count("open->File->clean->save->close->clean-!>close")
        tools.fd_count_logger.debug("FieldFile is open: {}".format(not test_crr.content_file.closed))
        test_crr.content_file.close()
        tools.fd_count("open->File->clean->save->close->clean->close!")
        tools.fd_count_logger.debug("FieldFile is open: {}".format(not test_crr.content_file.closed))


class MethodTestCase(TestCase):
    """
    Set up a database state for unit testing.
    
    This sets up all the stuff used in the Metadata tests, as well as some of the Datatypes
    and CDTs we use here.
    """
    fixtures = ["initial_data", "initial_groups", "initial_user"]

    def setUp(self):
        """Set up default database state for Method unit testing."""
        tools.create_method_test_environment(self)

    def tearDown(self):
        tools.destroy_method_test_environment(self)


class CodeResourceTests(MethodTestCase):
     
    def test_unicode(self):
        """
        unicode should return the codeResource name.
        """
        self.assertEquals(unicode(self.comp_cr), "complement")
  
    def test_valid_name_clean_good(self):
        """
        Clean passes when codeResource name is file-system valid
        """
        valid_cr = CodeResource(name="name", filename="validName", description="desc", user=self.myUser)
        valid_cr.save()
        self.assertIsNone(valid_cr.clean())

    def test_valid_name_with_special_symbols_clean_good(self):
        """
        Clean passes when codeResource name is file-system valid
        """
        valid_cr = CodeResource(name="anotherName", filename="valid.Name with-spaces_and_underscores().py",
                                description="desc", user=self.myUser)
        valid_cr.save()
        self.assertIsNone(valid_cr.clean())

    def test_invalid_name_doubledot_clean_bad(self):
        """
        Clean fails when CodeResource name isn't file-system valid
        """

        invalid_cr = CodeResource(name="test", filename="../test.py", description="desc", user=self.myUser)
        invalid_cr.save()
        self.assertRaisesRegexp(ValidationError, "Invalid code resource filename", invalid_cr.clean_fields)

    def test_invalid_name_starting_space_clean_bad(self):
        """  
        Clean fails when CodeResource name isn't file-system valid
        """
        invalid_cr = CodeResource(name="test", filename=" test.py", description="desc", user=self.myUser)
        invalid_cr.save()
        self.assertRaisesRegexp(ValidationError, "Invalid code resource filename", invalid_cr.clean_fields)

    def test_invalid_name_invalid_symbol_clean_bad(self):
        """  
        Clean fails when CodeResource name isn't file-system valid
        """
        invalid_cr = CodeResource(name="name", filename="test$.py", description="desc", user=self.myUser)
        invalid_cr.save()
        self.assertRaisesRegexp(ValidationError, "Invalid code resource filename", invalid_cr.clean_fields)

    def test_invalid_name_trailing_space_clean_bad(self):
        """  
        Clean fails when CodeResource name isn't file-system valid
        """
        invalid_cr = CodeResource(name="name", filename="test.py ", description="desc", user=self.myUser)
        invalid_cr.save()
        self.assertRaisesRegexp(ValidationError, "Invalid code resource filename", invalid_cr.clean_fields)


class CodeResourceRevisionTests(MethodTestCase):

    def test_unicode(self):
        """
        CodeResourceRevision.unicode() should return it's code resource
        revision name.

        Or, if no CodeResource has been linked, should display a placeholder.
        """
        # Valid crRev should return it's cr.name and crRev.revision_name
        self.assertEquals(unicode(self.compv1_crRev), "complement:1 (v1)")

        # Define a crRev without a linking cr, or a revision_name
        no_cr_set = CodeResourceRevision()
        self.assertEquals(unicode(no_cr_set), "[no revision name]")

        # Define a crRev without a linking cr, with a revision_name of foo
        no_cr_set.revision_name = "foo"
        self.assertEquals(unicode(no_cr_set), "foo")

    # Tests of has_circular_dependence and clean
    def test_has_circular_dependence_nodep(self):
        """A CRR with no dependencies should not have any circular dependence."""
        self.assertEquals(self.test_cr_1_rev1.has_circular_dependence(),
                          False)
        self.assertEquals(self.test_cr_1_rev1.clean(), None)
        self.test_cr_1_rev1.content_file.close()

    def test_has_circular_dependence_single_self_direct_dep(self):
        """A CRR has itself as its lone dependency."""
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_1_rev1,
            depPath=".",
            depFileName="foo")
        self.assertEquals(self.test_cr_1_rev1.has_circular_dependence(), True)
        self.assertRaisesRegexp(ValidationError,
                                "Self-referential dependency",
                                self.test_cr_1_rev1.clean)
        self.test_cr_1_rev1.content_file.close()

    def test_has_circular_dependence_single_other_direct_dep(self):
        """A CRR has a lone dependency (non-self)."""
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath=".",
            depFileName="foo")
        self.assertEquals(self.test_cr_1_rev1.has_circular_dependence(),
                          False)
        self.assertEquals(self.test_cr_1_rev1.clean(), None)
        self.test_cr_1_rev1.content_file.close()

    def test_has_circular_dependence_several_direct_dep_noself(self):
        """A CRR with several direct dependencies (none are itself)."""
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath=".",
            depFileName="foo")
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath=".")
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_4_rev1,
            depPath=".")
        self.assertEquals(self.test_cr_1_rev1.has_circular_dependence(),
                          False)
        self.assertEquals(self.test_cr_1_rev1.clean(), None)
        self.test_cr_1_rev1.content_file.close()

    def test_has_circular_dependence_several_direct_dep_self_1(self):
        """A CRR with several dependencies has itself as the first dependency."""
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_1_rev1,
            depPath=".",
            depFileName="foo")
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath=".")
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath=".")
        self.assertEquals(self.test_cr_1_rev1.has_circular_dependence(),
                          True)

        self.assertRaisesRegexp(ValidationError,
                                "Self-referential dependency",
                                self.test_cr_1_rev1.clean)
        self.test_cr_1_rev1.content_file.close()
        
    def test_has_circular_dependence_several_direct_dep_self_2(self):
        """A CRR with several dependencies has itself as the second dependency."""
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath=".")
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_1_rev1,
            depPath=".",
            depFileName="foo")
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath=".")
        self.assertEquals(self.test_cr_1_rev1.has_circular_dependence(),
                          True)
        self.assertRaisesRegexp(ValidationError,
                                "Self-referential dependency",
                                self.test_cr_1_rev1.clean)
        
    def test_has_circular_dependence_several_direct_dep_self_3(self):
        """A CRR with several dependencies has itself as the last dependency."""
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath=".")
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath=".")
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_1_rev1,
            depPath=".",
            depFileName="foo")
        self.assertEquals(self.test_cr_1_rev1.has_circular_dependence(),
                          True)
        self.assertRaisesRegexp(ValidationError,
                                "Self-referential dependency",
                                self.test_cr_1_rev1.clean)

    def test_has_circular_dependence_several_nested_dep_noself(self):
        """A CRR with several dependencies including a nested one."""
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath=".")
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath=".")
        self.test_cr_3_rev1.dependencies.create(
            requirement=self.test_cr_4_rev1,
            depPath=".")
        self.assertEquals(self.test_cr_1_rev1.has_circular_dependence(),
                          False)
        self.assertEquals(self.test_cr_1_rev1.clean(), None)
        
    def test_has_circular_dependence_several_nested_dep_selfnested(self):
        """A CRR with several dependencies including itself as a nested one."""
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath=".")
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath=".")
        self.test_cr_3_rev1.dependencies.create(
            requirement=self.test_cr_1_rev1,
            depPath=".")
        self.assertEquals(self.test_cr_1_rev1.has_circular_dependence(),
                          True)
        self.assertEquals(self.test_cr_2_rev1.has_circular_dependence(),
                          False)
        # Note that test_cr_3_rev1 *is* circular, as it depends on 1 and
        # 1 has a circular dependence.
        self.assertEquals(self.test_cr_3_rev1.has_circular_dependence(),
                          True)
        self.assertRaisesRegexp(ValidationError,
                                "Self-referential dependency",
                                self.test_cr_1_rev1.clean)
        
    def test_has_circular_dependence_nested_dep_has_circ(self):
        """A nested dependency is circular."""
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath=".")
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath=".")
        self.test_cr_2_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath=".")
        self.assertEquals(self.test_cr_1_rev1.has_circular_dependence(),
                          True)
        self.assertRaisesRegexp(ValidationError,
                                "Self-referential dependency",
                                self.test_cr_1_rev1.clean)
        self.assertEquals(self.test_cr_2_rev1.has_circular_dependence(),
                          True)
        self.assertRaisesRegexp(ValidationError,
                                "Self-referential dependency",
                                self.test_cr_2_rev1.clean)
        
    def test_metapackage_cannot_have_file_bad_clean(self):
        """
        A CRR with a content file should have a filename associated with
        its parent CodeResource.
        """
        cr = CodeResource(
            name="test_complement",
            filename="",
            description="Complement DNA/RNA nucleotide sequences",
            user=self.myUser)
        cr.save()

        # So it's revision does not have a content_file
        with open(os.path.join(samplecode_path, "complement.py"), "rb") as f:
            cr_rev_v1 = CodeResourceRevision(
                coderesource=cr,
                revision_name="v1",
                revision_desc="First version",
                content_file=File(f),
                user=self.myUser)

        self.assertRaisesRegexp(
            ValidationError,
            "If content file exists, it must have a file name",
            cr_rev_v1.clean)

    def test_non_metapackage_must_have_file_bad_clean(self):
        """
        A CRR with no content file should not have a filename associated with
        its parent CodeResource.
        """
        cr = CodeResource(
            name="nonmetapackage",
            filename="foo",
            description="Associated CRRs should have a content file",
            user=self.myUser)
        cr.save()

        # Create a revision without a content_file.
        cr_rev_v1 = CodeResourceRevision(
            coderesource=cr,
            revision_name="v1",
            revision_desc="Has no content file!",
            user=self.myUser)

        self.assertRaisesRegexp(
            ValidationError,
            "Cannot have a filename specified in the absence of a content file",
            cr_rev_v1.clean)

    def test_clean_blank_MD5_on_codeResourceRevision_without_file(self):
        """
        If no file is specified, MD5 should be empty string.
        """
        cr = CodeResource(name="foo",
                          filename="",
                          description="Some metapackage",
                          user=self.myUser)
        cr.save()
        
        # Create crRev with a codeResource but no file contents
        no_file_crRev = CodeResourceRevision(
            coderesource=cr,
            revision_name="foo",
            revision_desc="foo",
            user=self.myUser)
  
        no_file_crRev.clean()

        # After clean(), MD5 checksum should be the empty string
        self.assertEquals(no_file_crRev.MD5_checksum, "")

    def test_clean_valid_MD5_on_codeResourceRevision_with_file(self):
        """
        If file contents are associated with a crRev, an MD5 should exist.
        """

        # Compute the reference MD5
        md5gen = hashlib.md5()
        with open(os.path.join(samplecode_path, "complement.py"), "rb") as f:
            md5gen.update(f.read())

        # Revision should have the correct MD5 checksum
        self.assertEquals(md5gen.hexdigest(), self.comp_cr.revisions.get(revision_name="v1").MD5_checksum)

    def test_dependency_depends_on_nothing_clean_good (self):
        self.assertEqual(self.test_cr_1_rev1.clean(), None)

    def test_dependency_current_folder_same_name_clean_bad(self):
        """
        A depends on B - current folder, same name
        """

        # test_cr_1_rev1 is needed by test_cr_2_rev1
        # It will have the same file name as test_cr_1
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="",
            depFileName=self.test_cr_1.filename)

        self.assertRaisesRegexp(ValidationError,
                                "Conflicting dependencies",
                                self.test_cr_1_rev1.clean)

    def test_dependency_current_folder_different_name_clean_good(self):
        """
        1 depends on 2 - current folder, different name
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="",
            depFileName="differentName.py")

        self.assertEqual(self.test_cr_1_rev1.clean(), None)

    def test_dependency_inner_folder_same_name_clean_good(self):
        """
        1 depends on 2 - different folder, same name
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="innerFolder/",
            depFileName=self.test_cr_1.filename)

        self.assertEqual(self.test_cr_1_rev1.clean(), None)

    def test_dependency_inner_folder_different_name_clean_good(self):
        """
        1 depends on 2 - different folder, different name
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="innerFolder/",
            depFileName="differentName.py")

        self.assertEqual(self.test_cr_1_rev1.clean(), None)

    def test_dependency_A_depends_BC_same_folder_no_conflicts_clean_good(self):
        """
        A depends on B, A depends on C
        BC in same folder as A
        Nothing conflicts
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="",
            depFileName="name1.py")

        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="",
            depFileName="name2.py")

        self.assertEqual(self.test_cr_1_rev1.clean(), None)

    def test_dependency_A_depends_BC_same_folder_B_conflicts_with_A_clean_bad(self):
        """
        A depends on B, A depends on C
        BC in same folder as A, B conflicts with A
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="",
            depFileName="name1.py")

        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="",
            depFileName=self.test_cr_1.filename)

        self.assertRaisesRegexp(
            ValidationError,
            "Conflicting dependencies",
            self.test_cr_1_rev1.clean)

    def test_dependency_A_depends_BC_same_folder_C_conflicts_with_A_clean_bad(self):
        """
        A depends on B, A depends on C
        BC in same folder as A, C conflicts with A
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="",
            depFileName=self.test_cr_1.filename)

        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="",
            depFileName="notConflicting.py")

        self.assertRaisesRegexp(
            ValidationError,
            "Conflicting dependencies",
            self.test_cr_1_rev1.clean)

    def test_dependency_A_depends_BC_same_folder_B_conflicts_with_C_clean_bad(self):
        """
        A depends on B, A depends on C
        BC in same folder as A, BC conflict
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="",
            depFileName="colliding_name.py")

        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="",
            depFileName="colliding_name.py")

        self.assertRaisesRegexp(
            ValidationError,
            "Conflicting dependencies",
            self.test_cr_1_rev1.clean)

    def test_dependency_A_depends_BC_B_in_same_folder_no_conflicts_clean_good(self):
        """
        BC in same folder as A, B conflicts with A
        B in same folder, C in different folder, nothing conflicts
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="",
            depFileName="no_collision.py")

        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="diffFolder",
            depFileName="differentName.py")

        self.assertEqual(self.test_cr_1_rev1.clean(), None)

    def test_dependency_A_depends_BC_B_in_same_folder_B_conflicts_A_clean_bad(self):
        """
        A depends on B, A depends on C
        B in same folder, C in different folder, B conflicts with A
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="",
            depFileName=self.test_cr_1.filename)

        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="diffFolder",
            depFileName="differentName.py")

        self.assertRaisesRegexp(
            ValidationError,
            "Conflicting dependencies",
            self.test_cr_1_rev1.clean)

    def test_dependency_A_depends_BC_C_in_same_folder_no_conflict_clean_good(self):
        """
        A depends on B, A depends on C
        B in different folder, C in same folder, nothing conflicts
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="diffFolder",
            depFileName=self.test_cr_1.filename)

        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="",
            depFileName="differentName.py")

        self.assertEqual(self.test_cr_1_rev1.clean(), None)

    def test_dependency_A_depends_BC_C_in_same_folder_C_conflicts_with_A_clean_bad(self):
        """
        A depends on B, A depends on C
        B in different folder, C in same folder, C conflicts with A
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="diffFolder",
            depFileName=self.test_cr_1.filename)

        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="",
            depFileName=self.test_cr_1.filename)

        self.assertRaisesRegexp(
            ValidationError,
            "Conflicting dependencies",
            self.test_cr_1_rev1.clean)

    def test_dependency_A_depends_B_B_depends_C_all_same_folder_no_conflict_clean_good(self):
        """
        A depends on B, B depends on C
        ABC in same folder - no conflicts
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="",
            depFileName="differentName.py")

        self.test_cr_2_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="",
            depFileName="differetName2.py")

        self.assertEqual(self.test_cr_1_rev1.clean(), None)

    def test_dependency_A_depends_B_B_depends_C_all_same_folder_A_conflicts_C_clean_bad(self):
        """
        A depends on B, B depends on C
        ABC in same folder - A conflicts with C
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="",
            depFileName="differentName.py")

        self.test_cr_2_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="",
            depFileName=self.test_cr_1.filename)

        self.assertRaisesRegexp(
            ValidationError,
            "Conflicting dependencies",
            self.test_cr_1_rev1.clean)

    def test_dependency_A_depends_B_B_depends_C_all_same_folder_B_conflicts_C_clean_bad(self):
        """
        A depends on B, B depends on C
        ABC in same folder - B conflicts with C
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="",
            depFileName=self.test_cr_1.filename)

        self.test_cr_2_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="",
            depFileName="differentName.py")

        self.assertRaisesRegexp(
            ValidationError,
            "Conflicting dependencies",
            self.test_cr_1_rev1.clean)

    def test_dependency_A_depends_B_B_depends_C_BC_is_nested_no_conflicts_clean_good(self):
        """
        A depends on B, B depends on C
        BC in nested folder - no conflicts
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="nestedFolder",
            depFileName=self.test_cr_1.name)

        self.test_cr_2_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="",
            depFileName="differentName.py")

        self.assertEqual(self.test_cr_1_rev1.clean(), None)

    def test_dependency_A_depends_B_B_depends_C_BC_is_nested_B_conflicts_C_clean_bad(self):
        """
        A depends on B, B depends on C
        BC in nested folder - B conflicts with C
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="nestedFolder",
            depFileName="conflicting.py")

        self.test_cr_2_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="",
            depFileName="conflicting.py")

        self.assertRaisesRegexp(
            ValidationError,
            "Conflicting dependencies",
            self.test_cr_1_rev1.clean)

    def test_dependency_A_depends_B_B_depends_C_double_nested_clean_good(self):
        """
        A depends on B, B depends on C
        B in nested folder, C in double nested folder - no conflicts
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="nestedFolder",
            depFileName="conflicting.py")

        self.test_cr_2_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="nestedFolder",
            depFileName="conflicting.py")

        self.assertEqual(self.test_cr_1_rev1.clean(), None)

    def test_dependency_A_depends_B1B2B3_B1_depends_C_all_same_folder_no_conflicts_clean_good(self):
        """
        A depends on B1/B2/B3, B1 depends on C
        A/B1B2B3/C in same folder - no conflicts
        """
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="",
            depFileName="1.py")

        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="",
            depFileName="2.py")

        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="",
            depFileName="3.py")

        self.test_cr_2_rev1.dependencies.create(
            requirement=self.test_cr_4_rev1,
            depPath="",
            depFileName="4.py")

        self.assertEqual(self.test_cr_1_rev1.clean(), None)

    def test_dependency_A_depends_B1B2B3_B2_depends_C_B1B2B3C_in_nested_B3_conflicts_C_clean_bad(self):
        """
        A depends on B1/B2/B3, B2 depends on C
        B1B2B3C in nested folder - B3 conflicts with C
        """

        # A depends on B1
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="nested",
            depFileName="1.py")

        # A depends on B2
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="nested",
            depFileName="2.py")

        # A depends on B3***
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="nested",
            depFileName="conflict.py")

        # B2 depends on C
        self.test_cr_3_rev1.dependencies.create(
            requirement=self.test_cr_4_rev1,
            depPath="",
            depFileName="conflict.py")

        self.assertRaisesRegexp(
            ValidationError,
            "Conflicting dependencies",
            self.test_cr_1_rev1.clean)

    def test_dependency_A_depends_B1B2B3_B3_depends_C_B2B3C_in_nested_B2_conflicts_B3_clean_bad(self):
        """
        A depends on B1/B2/B3, B3 depends on C
        B2B3 in nested folder - B2 conflicts with B3
        """

        # A depends on B1
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="",
            depFileName="1.py")

        # A depends on B2
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="nested",
            depFileName="conflict.py")

        # A depends on B3
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="nested",
            depFileName="conflict.py")

        # B3 depends on C
        self.test_cr_3_rev1.dependencies.create(
            requirement=self.test_cr_4_rev1,
            depPath="",
            depFileName="4.py")

        self.assertRaisesRegexp(
            ValidationError,
            "Conflicting dependencies",
            self.test_cr_1_rev1.clean)

    def test_dependency_list_all_filepaths_recursive_case_1 (self):
        """
        Ensure list_all_filepaths generates the correct list
        A depends on B1/B2, B1 depends on C
        B1 is nested, B2 is not nested, C is nested wrt B1
        """

        # A depends on B1 (Which is nested)
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="B1_nested",
            depFileName="B1.py")

        # A depends on B2 (Which is not nested)
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="",
            depFileName="B2.py")

        # B1 depends on C (Nested wrt B1)
        self.test_cr_2_rev1.dependencies.create(
            requirement=self.test_cr_4_rev1,
            depPath="C_nested",
            depFileName="C.py")

        self.assertSetEqual(
            set(self.test_cr_1_rev1.list_all_filepaths()),
            {u'test_cr_1.py', u'B1_nested/B1.py', u'B1_nested/C_nested/C.py', u'B2.py'}
        )

    def test_dependency_list_all_filepaths_recursive_case_2 (self):
        """
        Ensure list_all_filepaths generates the correct list
        A depends on B1/B2, B2 depends on C
        B1 is nested, B2 is not nested, C is nested wrt B2
        """
        # A depends on B1 (Which is nested)
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="B1_nested",
            depFileName="B1.py")

        # A depends on B2 (Which is not nested)
        self.test_cr_1_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="",
            depFileName="B2.py")

        # B2 depends on C (Nested wrt B2)
        self.test_cr_3_rev1.dependencies.create(
            requirement=self.test_cr_4_rev1,
            depPath="C_nested",
            depFileName="C.py")

        self.assertSetEqual(
            set(self.test_cr_1_rev1.list_all_filepaths()),
            {u'test_cr_1.py', u'B1_nested/B1.py', u'B2.py', u'C_nested/C.py'}
        )

    def test_dependency_list_all_filepaths_with_metapackage(self):

        # Define a code with a blank filename (metapackage)
        # Give it dependencies
        # Give one more dependency a nested dependency

        # The following is for testing code resource dependencies
        test_cr_6 = CodeResource(name="test_cr_6",
                                 filename="",
                                 description="CR6",
                                 user=self.myUser)
        test_cr_6.save()

        # The revision has no content_file because it's a metapackage
        test_cr_6_rev1 = CodeResourceRevision(coderesource=test_cr_6,
                                              revision_name="v1_metapackage",
                                              revision_desc="CR6-rev1",
                                              user=self.myUser)
        test_cr_6_rev1.save()

        # Current-folder dependencies
        test_cr_6_rev1.dependencies.create(
            requirement=self.test_cr_2_rev1,
            depPath="",
            depFileName="B.py")

        # Sub-folder dependencies
        test_cr_6_rev1.dependencies.create(
            requirement=self.test_cr_3_rev1,
            depPath="nestedFolder",
            depFileName="C.py")

        # Nested dependencies
        self.test_cr_3_rev1.dependencies.create(
            requirement=self.test_cr_4_rev1,
            depPath="deeperNestedFolder",
            depFileName="D.py")

        self.assertSetEqual(
            set(test_cr_6_rev1.list_all_filepaths()),
            {u'B.py', u'nestedFolder/C.py', u'nestedFolder/deeperNestedFolder/D.py'}
        )

        # FIXME
        # test_cr_6_rev1.content_file.delete()
        # test_cr_6_rev1.delete()

    def test_dependency_list_all_filepaths_single_unnested_dep_blank_depFileName(self):
        """List all filepaths when dependency has no depFileName set and is not nested.
        """
        self.test_cr_1_rev1.dependencies.create(
                requirement=self.test_cr_2_rev1,
                depPath="")
        self.assertEqual(self.test_cr_1_rev1.list_all_filepaths(),
                         [u'test_cr_1.py', u'test_cr_2.py'])

    def test_dependency_list_all_filepaths_single_nested_dep_blank_depFileName(self):
        """List all filepaths when dependency has no depFileName set and is nested.
        """
        self.test_cr_1_rev1.dependencies.create(
                requirement=self.test_cr_2_rev1,
                depPath="nest_folder")
        self.assertEqual(self.test_cr_1_rev1.list_all_filepaths(),
                         [u'test_cr_1.py', u'nest_folder/test_cr_2.py'])

    def test_find_update_not_found(self):
        update = self.compv2_crRev.find_update()
        
        self.assertEqual(update, None)

    def test_find_update(self):
        update = self.compv1_crRev.find_update()
        
        self.assertEqual(update, self.compv2_crRev)


class CodeResourceDependencyTests(MethodTestCase):

    def test_unicode(self):
        """
        Unicode of CodeResourceDependency should return:
        <self.crRev> requires <referenced crRev> as <filePath>
        """

        # v1 is a revision of comp_cr such that revision_name = v1
        v1 = self.comp_cr.revisions.get(revision_name="v1")
        v2 = self.comp_cr.revisions.get(revision_name="v2")

        # Define a fake dependency where v1 requires v2 in subdir/foo.py
        test_crd = CodeResourceDependency(coderesourcerevision=v1,
                                          requirement=v2,
                                          depPath="subdir",
                                          depFileName="foo.py")

        # Display unicode for this dependency under valid conditions
        self.assertEquals(
            unicode(test_crd),
            "complement complement:1 (v1) requires complement complement:2 (v2) as subdir/foo.py")

    def test_invalid_dotdot_path_clean(self):
        """
        Dependency tries to go into a path outside its sandbox.
        """
        v1 = self.comp_cr.revisions.get(revision_name="v1")
        v2 = self.comp_cr.revisions.get(revision_name="v2")

        bad_crd = CodeResourceDependency(coderesourcerevision=v1,
                                         requirement=v2,
                                         depPath="..",
                                         depFileName="foo.py")
        self.assertRaisesRegexp(
            ValidationError,
            "depPath cannot reference \.\./",
            bad_crd.clean)

        bad_crd_2 = CodeResourceDependency(coderesourcerevision=v1,
                                           requirement=v2,
                                           depPath="../test",
                                           depFileName="foo.py")
        self.assertRaisesRegexp(
            ValidationError,
            "depPath cannot reference \.\./",
            bad_crd_2.clean)
        
    def test_valid_path_with_dotdot_clean(self):
        """
        Dependency goes into a path with a directory containing ".." in the name.
        """
        v1 = self.comp_cr.revisions.get(revision_name="v1")
        v2 = self.comp_cr.revisions.get(revision_name="v2")

        good_crd = CodeResourceDependency(coderesourcerevision=v1,
                                          requirement=v2,
                                          depPath="..bar",
                                          depFileName="foo.py")
        self.assertEquals(good_crd.clean(), None)
        
        good_crd_2 = CodeResourceDependency(coderesourcerevision=v1,
                                            requirement=v2,
                                            depPath="bar..",
                                            depFileName="foo.py")
        self.assertEquals(good_crd_2.clean(), None)

        good_crd_3 = CodeResourceDependency(coderesourcerevision=v1,
                                            requirement=v2,
                                            depPath="baz/bar..",
                                            depFileName="foo.py")
        self.assertEquals(good_crd_3.clean(), None)

        good_crd_4 = CodeResourceDependency(coderesourcerevision=v1,
                                            requirement=v2,
                                            depPath="baz/..bar",
                                            depFileName="foo.py")
        self.assertEquals(good_crd_4.clean(), None)

        good_crd_5 = CodeResourceDependency(coderesourcerevision=v1,
                                            requirement=v2,
                                            depPath="baz/..bar..",
                                            depFileName="foo.py")
        self.assertEquals(good_crd_5.clean(), None)

        good_crd_6 = CodeResourceDependency(coderesourcerevision=v1,
                                            requirement=v2,
                                            depPath="..baz/bar..",
                                            depFileName="foo.py")
        self.assertEquals(good_crd_6.clean(), None)

        # This case works because the ".." doesn't take us out of the sandbox
        good_crd_7 = CodeResourceDependency(coderesourcerevision=v1,
                                            requirement=v2,
                                            depPath="baz/../bar",
                                            depFileName="foo.py")
        self.assertEquals(good_crd_7.clean(), None)

        good_crd_8 = CodeResourceDependency(coderesourcerevision=v1,
                                            requirement=v2,
                                            depPath="baz/..bar../blah",
                                            depFileName="foo.py")
        self.assertEquals(good_crd_8.clean(), None)
        
    def test_cr_with_filename_dependency_with_good_path_and_filename_clean(self):
        """
        Check
        """
        # cr_no_filename has name="complement" and filename="complement.py"
        cr = CodeResource(
                name="testing_complement",
                filename="complement.py",
                description="Complement DNA/RNA nucleotide sequences",
                user=self.myUser)
        cr.save()

        # Define cr_rev_v1 for cr
        with open(os.path.join(samplecode_path, "complement.py"), "rb") as f:
            cr_rev_v1 = CodeResourceRevision(
                    coderesource=cr,
                    revision_name="v1",
                    revision_desc="First version",
                    content_file=File(f),
                    user=self.myUser)
            cr_rev_v1.full_clean()
            cr_rev_v1.save()

        # Define cr_rev_v2 for cr
        with open(os.path.join(samplecode_path, "complement.py"), "rb") as f:
            cr_rev_v2 = CodeResourceRevision(
                    coderesource=cr,
                    revision_name="v2",
                    revision_desc="Second version",
                    content_file=File(f),
                    user=self.myUser)
            cr_rev_v2.full_clean()
            cr_rev_v2.save()

        # Define a code resource dependency for cr_rev_v1 with good paths and filenames
        good_crd = CodeResourceDependency(coderesourcerevision=cr_rev_v1,
                                          requirement=cr_rev_v2,
                                          depPath="testFolder/anotherFolder",
                                          depFileName="foo.py")

        self.assertEqual(good_crd.clean(), None)
        
    def test_metapackage_cannot_have_file_names_bad_clean(self):

        # Define a standard code resource
        cr = CodeResource(
                name="test_complement",
                filename="test.py",
                description="Complement DNA/RNA nucleotide sequences",
                user=self.myUser)
        cr.save()

        # Give it a file
        with open(os.path.join(samplecode_path, "complement.py"), "rb") as f:
            cr_rev_v1 = CodeResourceRevision(
                coderesource=cr,
                revision_name="v1",
                revision_desc="First version",
                content_file=File(f),
                user=self.myUser)
            cr_rev_v1.full_clean()
            cr_rev_v1.save()
        
        # Define a metapackage code resource (no file name)
        cr_meta = CodeResource(
                name="test2_complement",
                filename="",
                description="Complement DNA/RNA nucleotide sequences",
                user=self.myUser)
        cr_meta.save()

        # Do not give it a file
        cr_meta_rev_v1 = CodeResourceRevision(
            coderesource=cr_meta,
            revision_name="v1",
            revision_desc="First version",
            user=self.myUser)
        cr_meta_rev_v1.full_clean()
        cr_meta_rev_v1.save()

        # Add metapackage as a dependency to cr_rev_v1, but invalidly give it a depFileName
        bad_crd = CodeResourceDependency(coderesourcerevision=cr_rev_v1,
                                         requirement=cr_meta_rev_v1,
                                         depPath="testFolder/anotherFolder",
                                         depFileName="foo.py")

        self.assertRaisesRegexp(
            ValidationError,
            "Metapackage dependencies cannot have a depFileName",
            bad_crd.clean)

    def test_metapackage_good_clean(self):

        # Define a standard code resource
        cr = CodeResource(
                name="test_complement",
                filename="test.py",
                description="Complement DNA/RNA nucleotide sequences",
                user=self.myUser)
        cr.save()

        # Give it a file
        with open(os.path.join(samplecode_path, "complement.py"), "rb") as f:
            cr_rev_v1 = CodeResourceRevision(
                coderesource=cr,
                revision_name="v1",
                revision_desc="First version",
                content_file=File(f),
                user=self.myUser)
            cr_rev_v1.full_clean()
            cr_rev_v1.save()
        
        # Define a metapackage code resource (no file name)
        cr_meta = CodeResource(
                name="test2_complement",
                filename="",
                description="Complement DNA/RNA nucleotide sequences",
                user=self.myUser)
        cr_meta.save()

        # Do not give it a file
        cr_meta_rev_v1 = CodeResourceRevision(
            coderesource=cr_meta,
            revision_name="v1",
            revision_desc="First version",
            user=self.myUser)
        cr_meta_rev_v1.full_clean()
        cr_meta_rev_v1.save()

        # Add metapackage as a dependency to cr_rev_v1
        good_crd = CodeResourceDependency(coderesourcerevision=cr_rev_v1,
                                         requirement=cr_meta_rev_v1,
                                         depPath="testFolder/anotherFolder",
                                         depFileName="")

        self.assertEqual(good_crd.clean(), None)


class CodeResourceRevisionInstallTests(MethodTestCase):
    """Tests of the install function of CodeResourceRevision."""
    def test_base_case(self):
        """
        Test of base case -- installing a CRR with no dependencies.
        """
        test_path = tempfile.mkdtemp(prefix="test_base_case")

        self.compv1_crRev.install(test_path)
        self.assertTrue(os.path.exists(os.path.join(test_path, "complement.py")))

        shutil.rmtree(test_path)

    def test_second_revision(self):
        """
        Test of base case -- installing a CRR that is a second revision.
        """
        test_path = tempfile.mkdtemp(prefix="test_base_case")

        self.compv2_crRev.install(test_path)
        self.assertTrue(os.path.exists(os.path.join(test_path, "complement.py")))

        shutil.rmtree(test_path)

    def test_dependency_same_dir_dot(self):
        """
        Test of installing a CRR with a dependency in the same directory, specified using a dot.
        """
        test_path = tempfile.mkdtemp(prefix="test_dependency_same_dir_dot")

        self.compv1_crRev.dependencies.create(requirement=self.test_cr_1_rev1, depPath=".")
        self.compv1_crRev.install(test_path)
        self.assertTrue(os.path.exists(os.path.join(test_path, "complement.py")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "test_cr_1.py")))

        shutil.rmtree(test_path)

    def test_dependency_same_dir_blank(self):
        """
        Test of installing a CRR with a dependency in the same directory, specified using a blank.
        """
        test_path = tempfile.mkdtemp(prefix="test_dependency_same_dir_blank")

        self.compv1_crRev.dependencies.create(requirement=self.test_cr_1_rev1, depPath="")
        self.compv1_crRev.install(test_path)
        self.assertTrue(os.path.exists(os.path.join(test_path, "complement.py")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "test_cr_1.py")))

        shutil.rmtree(test_path)

    def test_dependency_override_dep_filename(self):
        """
        Test of installing a CRR with a dependency whose filename is overridden.
        """
        test_path = tempfile.mkdtemp(prefix="test_dependency_override_dep_filename")

        self.compv1_crRev.dependencies.create(requirement=self.test_cr_1_rev1, depPath="",
                                              depFileName="foo.py")
        self.compv1_crRev.install(test_path)
        self.assertTrue(os.path.exists(os.path.join(test_path, "complement.py")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "foo.py")))
        self.assertFalse(os.path.exists(os.path.join(test_path, "test_cr_1.py")))

        shutil.rmtree(test_path)

    def test_dependency_in_subdirectory(self):
        """
        Test of installing a CRR with a dependency in a subdirectory.
        """
        test_path = tempfile.mkdtemp(prefix="test_dependency_in_subdirectory")

        self.compv1_crRev.dependencies.create(requirement=self.test_cr_1_rev1, depPath="modules")
        self.compv1_crRev.install(test_path)
        self.assertTrue(os.path.exists(os.path.join(test_path, "complement.py")))
        self.assertTrue(os.path.isdir(os.path.join(test_path, "modules")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "modules", "test_cr_1.py")))

        shutil.rmtree(test_path)

    def test_dependencies_in_same_subdirectory(self):
        """
        Test of installing a CRR with several dependencies in the same subdirectory.
        """
        test_path = tempfile.mkdtemp(prefix="test_dependencies_in_same_subdirectory")

        self.compv1_crRev.dependencies.create(requirement=self.test_cr_1_rev1, depPath="modules")
        self.compv1_crRev.dependencies.create(requirement=self.test_cr_2_rev1, depPath="modules")
        self.compv1_crRev.install(test_path)
        self.assertTrue(os.path.exists(os.path.join(test_path, "complement.py")))
        self.assertTrue(os.path.isdir(os.path.join(test_path, "modules")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "modules", "test_cr_1.py")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "modules", "test_cr_2.py")))

        shutil.rmtree(test_path)

    def test_dependencies_in_same_directory(self):
        """
        Test of installing a CRR with several dependencies in the base directory.
        """
        test_path = tempfile.mkdtemp(prefix="test_dependencies_in_same_directory")

        self.compv1_crRev.dependencies.create(requirement=self.test_cr_1_rev1, depPath="")
        self.compv1_crRev.dependencies.create(requirement=self.test_cr_2_rev1, depPath="")
        self.compv1_crRev.install(test_path)
        self.assertTrue(os.path.exists(os.path.join(test_path, "complement.py")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "test_cr_1.py")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "test_cr_2.py")))

        shutil.rmtree(test_path)

    def test_dependencies_in_subsub_directory(self):
        """
        Test of installing a CRR with dependencies in sub-sub-directories.
        """
        test_path = tempfile.mkdtemp(prefix="test_dependencies_in_subsub_directory")

        self.compv1_crRev.dependencies.create(requirement=self.test_cr_1_rev1, depPath="modules/foo1")
        self.compv1_crRev.dependencies.create(requirement=self.test_cr_2_rev1, depPath="modules/foo2")
        self.compv1_crRev.install(test_path)
        self.assertTrue(os.path.exists(os.path.join(test_path, "complement.py")))
        self.assertTrue(os.path.isdir(os.path.join(test_path, "modules/foo1")))
        self.assertTrue(os.path.isdir(os.path.join(test_path, "modules/foo2")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "modules", "foo1", "test_cr_1.py")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "modules", "foo2", "test_cr_2.py")))

        shutil.rmtree(test_path)

    def test_dependencies_from_same_coderesource_same_dir(self):
        """
        Test of installing a CRR with a dependency having the same CodeResource in the same directory.
        """
        test_path = tempfile.mkdtemp(prefix="test_dependencies_from_same_coderesource_same_dir")

        self.compv1_crRev.dependencies.create(requirement=self.compv2_crRev, depPath="", depFileName="foo.py")
        self.compv1_crRev.install(test_path)
        self.assertTrue(os.path.exists(os.path.join(test_path, "complement.py")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "foo.py")))
        # Test that the right files are in the right places.
        self.assertTrue(
            filecmp.cmp(os.path.join(samplecode_path, "complement.py"),
                        os.path.join(test_path, "complement.py"))
        )
        self.assertTrue(
            filecmp.cmp(os.path.join(samplecode_path, "complement_v2.py"),
                        os.path.join(test_path, "foo.py"))
        )

        shutil.rmtree(test_path)

    def test_dependencies_in_various_places(self):
        """
        Test of installing a CRR with dependencies in several places.
        """
        test_path = tempfile.mkdtemp(prefix="test_dependencies_in_various_places")

        self.compv1_crRev.dependencies.create(requirement=self.test_cr_1_rev1, depPath="modules")
        self.compv1_crRev.dependencies.create(requirement=self.test_cr_2_rev1, depPath="moremodules")
        self.compv1_crRev.dependencies.create(requirement=self.test_cr_3_rev1, depPath="modules/foo")
        self.compv1_crRev.install(test_path)
        self.assertTrue(os.path.exists(os.path.join(test_path, "complement.py")))
        self.assertTrue(os.path.isdir(os.path.join(test_path, "modules")))
        self.assertTrue(os.path.isdir(os.path.join(test_path, "moremodules")))
        self.assertTrue(os.path.isdir(os.path.join(test_path, "modules", "foo")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "modules", "test_cr_1.py")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "moremodules", "test_cr_2.py")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "modules", "foo", "test_cr_3.py")))

        shutil.rmtree(test_path)

    def test_nested_dependencies(self):
        """
        Test of installing a CRR with dependencies that have their own dependencies.
        """
        test_path = tempfile.mkdtemp(prefix="test_nested_dependencies")

        # Make test_cr_1_rev1 have its own dependencies.
        self.test_cr_1_rev1.dependencies.create(requirement=self.script_1_crRev, depPath=".")
        self.test_cr_1_rev1.dependencies.create(requirement=self.script_2_crRev, depPath="cr1mods")

        self.test_cr_2_rev1.dependencies.create(requirement=self.script_3_crRev, depPath="cr2mods")
        self.test_cr_2_rev1.dependencies.create(requirement=self.script_4_1_CRR, depPath="cr2mods/foo")

        self.compv1_crRev.dependencies.create(requirement=self.test_cr_1_rev1, depPath="")
        self.compv1_crRev.dependencies.create(requirement=self.test_cr_2_rev1, depPath="basemods")
        self.compv1_crRev.install(test_path)

        self.assertTrue(os.path.exists(os.path.join(test_path, "complement.py")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "test_cr_1.py")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "script_1_sum_and_products.py")))
        self.assertTrue(os.path.isdir(os.path.join(test_path, "cr1mods")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "cr1mods", "script_2_square_and_means.py")))

        self.assertTrue(os.path.isdir(os.path.join(test_path, "basemods")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "basemods", "test_cr_2.py")))
        self.assertTrue(os.path.isdir(os.path.join(test_path, "basemods", "cr2mods")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "basemods", "cr2mods", "script_3_product.py")))
        self.assertTrue(os.path.isdir(os.path.join(test_path, "basemods", "cr2mods", "foo")))
        self.assertTrue(
            os.path.exists(os.path.join(test_path, "basemods", "cr2mods", "foo",
                                        "script_4_raw_in_CSV_out.py")))

        shutil.rmtree(test_path)

    def _setup_metapackage(self):
        """Helper that sets up a metapackage."""
        # Define comp_cr
        self.metapackage = CodeResource(
            name="metapackage",
            description="Collection of modules",
            filename="",
            user=self.myUser)
        self.metapackage.save()

        self.metapackage_r1 = CodeResourceRevision(
            coderesource=self.metapackage,
            revision_name="v1",
            revision_desc="First version",
            user=self.myUser
        )
        self.metapackage_r1.save()

        # Add dependencies.
        self.metapackage_r1.dependencies.create(requirement=self.script_1_crRev, depPath=".")
        self.metapackage_r1.dependencies.create(requirement=self.script_2_crRev, depPath=".")
        self.metapackage_r1.dependencies.create(requirement=self.script_3_crRev, depPath="metamodules")
        self.metapackage_r1.dependencies.create(requirement=self.script_4_1_CRR, depPath="metamodules/foo")

    def test_metapackage(self):
        """
        Test of installing a metapackage CRR.
        """
        test_path = tempfile.mkdtemp(prefix="test_install_metapackage")
        self._setup_metapackage()

        self.metapackage_r1.install(test_path)
        self.assertTrue(os.path.exists(os.path.join(test_path, "script_1_sum_and_products.py")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "script_2_square_and_means.py")))
        self.assertTrue(os.path.isdir(os.path.join(test_path, "metamodules")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "metamodules", "script_3_product.py")))
        self.assertTrue(os.path.isdir(os.path.join(test_path, "metamodules", "foo")))
        self.assertTrue(os.path.exists(os.path.join(test_path, "metamodules", "foo", "script_4_raw_in_CSV_out.py")))

        shutil.rmtree(test_path)

    def test_dependency_is_metapackage(self):
        """
        Test of installing a CRR with a metapackage dependency.
        """
        test_path = tempfile.mkdtemp(prefix="test_dependency_is_metapackage")
        self._setup_metapackage()

        self.compv1_crRev.dependencies.create(requirement=self.metapackage_r1, depPath="modules")

        self.compv1_crRev.install(test_path)
        self.assertTrue(os.path.exists(os.path.join(test_path, "complement.py")))

        metapackage_path = os.path.join(test_path, "modules")
        self.assertTrue(os.path.isdir(metapackage_path))
        self.assertTrue(os.path.exists(os.path.join(metapackage_path, "script_1_sum_and_products.py")))
        self.assertTrue(os.path.exists(os.path.join(metapackage_path, "script_2_square_and_means.py")))
        self.assertTrue(os.path.isdir(os.path.join(metapackage_path, "metamodules")))
        self.assertTrue(os.path.exists(os.path.join(metapackage_path, "metamodules", "script_3_product.py")))
        self.assertTrue(os.path.isdir(os.path.join(metapackage_path, "metamodules", "foo")))
        self.assertTrue(os.path.exists(os.path.join(metapackage_path, "metamodules", "foo",
                                                    "script_4_raw_in_CSV_out.py")))
        shutil.rmtree(test_path)


class MethodTests(MethodTestCase):

    def test_with_family_unicode(self):
        """
        unicode() for method should return "Method revisionName and family name"
        """

        # DNAcompv1_m has method family DNAcomplement
        self.assertEqual(unicode(self.DNAcompv1_m),
                         "DNAcomplement:1 (v1)")

    def test_without_family_unicode(self):
        """
        unicode() for Test unicode representation when family is unset.
        """
        nofamily = Method(revision_name="foo")

        self.assertEqual(unicode(nofamily),
                         "[family unset]:None (foo)")
        
    def test_display_name(self):
        method = Method(revision_number=1, revision_name='Example')
        
        self.assertEqual(method.display_name, '1: Example')
        
    def test_display_name_without_revision_name(self):
        method = Method(revision_number=1)
        
        self.assertEqual(method.display_name, '1: ')

    def test_no_inputs_checkInputIndices_good(self):
        """
        Method with no inputs defined should have
        check_input_indices() return with no exception.
        """

        # Create Method with valid family, revision_name, description, driver
        foo = Method(family=self.DNAcomp_mf, revision_name="foo",
                     revision_desc="Foo version", driver=self.compv1_crRev, user=self.myUser)
        foo.save()

        # check_input_indices() should not raise a ValidationError
        self.assertEquals(foo.check_input_indices(), None)
        self.assertEquals(foo.clean(), None)

    def test_single_valid_input_checkInputIndices_good(self):
        """
        Method with a single, 1-indexed input should have
        check_input_indices() return with no exception.
        """

        # Create Method with valid family, revision_name, description, driver
        foo = Method(family=self.DNAcomp_mf, revision_name="foo",
                     revision_desc="Foo version", 
                     driver=self.compv1_crRev, user=self.myUser)
        foo.save()

        # Add one valid input cdt at index 1 named "oneinput" to transformation
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="oneinput", dataset_idx=1)

        # check_input_indices() should not raise a ValidationError
        self.assertEquals(foo.check_input_indices(), None)
        self.assertEquals(foo.clean(), None)

    def test_many_ordered_valid_inputs_checkInputIndices_good (self):
        """
        Test check_input_indices on a method with several inputs,
        correctly indexed and in order.
        """

        # Create Method with valid family, revision_name, description, driver
        foo = Method(family=self.DNAcomp_mf, revision_name="foo",
                     revision_desc="Foo version", 
                     driver=self.compv1_crRev, user=self.myUser)
        foo.save()

        # Add several input cdts that together are valid
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="oneinput", dataset_idx=1)
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="twoinput", dataset_idx=2)
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="threeinput", dataset_idx=3)

        # No ValidationErrors should be raised
        self.assertEquals(foo.check_input_indices(), None)
        self.assertEquals(foo.clean(), None)

    def test_many_valid_inputs_scrambled_checkInputIndices_good (self):
        """
        Test check_input_indices on a method with several inputs,
        correctly indexed and in scrambled order.
        """

        # Create Method with valid family, revision_name, description, driver
        foo = Method(family=self.DNAcomp_mf, revision_name="foo",
                     revision_desc="Foo version", 
                     driver=self.compv1_crRev, user=self.myUser)
        foo.save()

        # Add several input cdts that together are valid
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="oneinput", dataset_idx=3)
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="twoinput", dataset_idx=1)
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="threeinput", dataset_idx=2)

        # No ValidationErrors should be raised
        self.assertEquals(foo.check_input_indices(), None)
        self.assertEquals(foo.clean(), None)

    def test_one_invalid_input_checkInputIndices_bad(self):
        """
        Test input index check, one badly-indexed input case.
        """

        # Create Method with valid family, revision_name, description, driver
        foo = Method(family=self.DNAcomp_mf, revision_name="foo",
                     revision_desc="Foo version", 
                     driver=self.compv1_crRev, user=self.myUser)
        foo.save()

        # Add one invalid input cdt at index 4 named "oneinput"
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="oneinput", dataset_idx=4)

        # check_input_indices() should raise a ValidationError
        self.assertRaisesRegexp(
            ValidationError,
            "Inputs are not consecutively numbered starting from 1",
            foo.check_input_indices)

        self.assertRaisesRegexp(
            ValidationError,
            "Inputs are not consecutively numbered starting from 1",
            foo.clean)

    def test_many_nonconsective_inputs_scrambled_checkInputIndices_bad(self):
        """Test input index check, badly-indexed multi-input case."""
        foo = Method(family=self.DNAcomp_mf, revision_name="foo", revision_desc="Foo version", 
                     driver=self.compv1_crRev, user=self.myUser)
        foo.save()
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="oneinput", dataset_idx=2)
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="twoinput", dataset_idx=6)
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="threeinput", dataset_idx=1)
        self.assertRaisesRegexp(
            ValidationError,
            "Inputs are not consecutively numbered starting from 1",
            foo.check_input_indices)

        self.assertRaisesRegexp(
            ValidationError,
            "Inputs are not consecutively numbered starting from 1",
            foo.clean)

    def test_no_outputs_checkOutputIndices_good(self):
        """Test output index check, one well-indexed output case."""
        foo = Method(family=self.DNAcomp_mf, revision_name="foo", revision_desc="Foo version", 
                     driver=self.compv1_crRev, user=self.myUser)
        foo.save()
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="oneinput", dataset_idx=1)

        self.assertEquals(foo.check_output_indices(), None)
        self.assertEquals(foo.clean(), None)

    def test_one_valid_output_checkOutputIndices_good(self):
        """Test output index check, one well-indexed output case."""
        foo = Method(family=self.DNAcomp_mf, revision_name="foo", revision_desc="Foo version", 
                     driver=self.compv1_crRev, user=self.myUser)
        foo.save()
        foo.create_output(compounddatatype=self.DNAoutput_cdt,
                          dataset_name="oneoutput", dataset_idx=1)
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="oneinput", dataset_idx=1)
        self.assertEquals(foo.check_output_indices(), None)
        self.assertEquals(foo.clean(), None)

    def test_many_valid_outputs_scrambled_checkOutputIndices_good (self):
        """Test output index check, well-indexed multi-output (scrambled order) case."""
        foo = Method(family=self.DNAcomp_mf, revision_name="foo", revision_desc="Foo version", 
                     driver=self.compv1_crRev, user=self.myUser)
        foo.save()
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="oneinput", dataset_idx=1)
        foo.create_output(compounddatatype=self.DNAoutput_cdt,
                          dataset_name="oneoutput", dataset_idx=3)
        foo.create_output(compounddatatype=self.DNAoutput_cdt,
                          dataset_name="twooutput", dataset_idx=1)
        foo.create_output(compounddatatype=self.DNAoutput_cdt,
                          dataset_name="threeoutput", dataset_idx=2)
        self.assertEquals(foo.check_output_indices(), None)
        self.assertEquals(foo.clean(), None)

    def test_one_invalid_output_checkOutputIndices_bad (self):
        """Test output index check, one badly-indexed output case."""
        foo = Method(family=self.DNAcomp_mf, revision_name="foo", revision_desc="Foo version", 
                     driver=self.compv1_crRev, user=self.myUser)
        foo.save()
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="oneinput", dataset_idx=1)
        foo.create_output(compounddatatype=self.DNAoutput_cdt,
                          dataset_name="oneoutput", dataset_idx=4)
        self.assertRaisesRegexp(
            ValidationError,
            "Outputs are not consecutively numbered starting from 1",
            foo.check_output_indices)

        self.assertRaisesRegexp(
            ValidationError,
            "Outputs are not consecutively numbered starting from 1",
            foo.clean)

    def test_many_invalid_outputs_scrambled_checkOutputIndices_bad(self):
        """Test output index check, badly-indexed multi-output case."""
        foo = Method(family=self.DNAcomp_mf, revision_name="foo", revision_desc="Foo version", 
                     driver=self.compv1_crRev, user=self.myUser)
        foo.save()
        
        foo.create_input(compounddatatype=self.DNAinput_cdt,
                         dataset_name="oneinput", dataset_idx=1)
        foo.create_output(compounddatatype=self.DNAoutput_cdt,
                          dataset_name="oneoutput", dataset_idx=2)
        foo.create_output(compounddatatype=self.DNAoutput_cdt,
                          dataset_name="twooutput", dataset_idx=6)
        foo.create_output(compounddatatype=self.DNAoutput_cdt,
                          dataset_name="threeoutput", dataset_idx=1)
        self.assertRaisesRegexp(
            ValidationError,
            "Outputs are not consecutively numbered starting from 1",
            foo.check_output_indices)

        self.assertRaisesRegexp(
            ValidationError,
            "Outputs are not consecutively numbered starting from 1",
            foo.clean)

    def test_no_copied_parent_parameters_save(self):
        """Test save when no method revision parent is specified."""

        # Define new Method with no parent
        foo = Method(family=self.DNAcomp_mf, revision_name="foo", revision_desc="Foo version", 
                     driver=self.compv1_crRev, user=self.myUser)
        foo.save()

        # There should be no inputs
        self.assertEqual(foo.inputs.count(), 0)
        self.assertEqual(foo.outputs.count(), 0)

        # DNAcompv1_m also has no parents as it is the first revision
        self.DNAcompv1_m.save()

        # DNAcompv1_m was defined to have 1 input and 1 output
        self.assertEqual(self.DNAcompv1_m.inputs.count(), 1)
        self.assertEqual(self.DNAcompv1_m.inputs.all()[0],
                         self.DNAinput_ti)

        self.assertEqual(self.DNAcompv1_m.outputs.count(), 1)
        self.assertEqual(self.DNAcompv1_m.outputs.all()[0],
                         self.DNAoutput_to)

        # Test the multiple-input and multiple-output cases, using
        # script_2_method and script_3_method respectively.  Neither
        # of these have parents.
        self.script_2_method.save()
        # Script 2 has input:
        # compounddatatype = self.triplet_cdt
        # dataset_name = "a_b_c"
        # dataset_idx = 1
        curr_in = self.script_2_method.inputs.all()[0]
        self.assertEqual(curr_in.dataset_name, "a_b_c")
        self.assertEqual(curr_in.dataset_idx, 1)
        self.assertEqual(curr_in.get_cdt(), self.triplet_cdt)
        self.assertEqual(curr_in.get_min_row(), None)
        self.assertEqual(curr_in.get_max_row(), None)
        # Outputs:
        # self.triplet_cdt, "a_b_c_squared", 1
        # self.singlet_cdt, "a_b_c_mean", 2
        curr_out_1 = self.script_2_method.outputs.get(dataset_idx=1)
        curr_out_2 = self.script_2_method.outputs.get(dataset_idx=2)
        self.assertEqual(curr_out_1.dataset_name, "a_b_c_squared")
        self.assertEqual(curr_out_1.dataset_idx, 1)
        self.assertEqual(curr_out_1.get_cdt(), self.triplet_cdt)
        self.assertEqual(curr_out_1.get_min_row(), None)
        self.assertEqual(curr_out_1.get_max_row(), None)
        self.assertEqual(curr_out_2.dataset_name, "a_b_c_mean")
        self.assertEqual(curr_out_2.dataset_idx, 2)
        self.assertEqual(curr_out_2.get_cdt(), self.singlet_cdt)
        self.assertEqual(curr_out_2.get_min_row(), None)
        self.assertEqual(curr_out_2.get_max_row(), None)

        self.script_3_method.save()
        # Script 3 has inputs:
        # self.singlet_cdt, "k", 1
        # self.singlet_cdt, "r", 2, min_row = max_row = 1
        curr_in_1 = self.script_3_method.inputs.get(dataset_idx=1)
        curr_in_2 = self.script_3_method.inputs.get(dataset_idx=2)
        self.assertEqual(curr_in_1.dataset_name, "k")
        self.assertEqual(curr_in_1.dataset_idx, 1)
        self.assertEqual(curr_in_1.get_cdt(), self.singlet_cdt)
        self.assertEqual(curr_in_1.get_min_row(), None)
        self.assertEqual(curr_in_1.get_max_row(), None)
        self.assertEqual(curr_in_2.dataset_name, "r")
        self.assertEqual(curr_in_2.dataset_idx, 2)
        self.assertEqual(curr_in_2.get_cdt(), self.singlet_cdt)
        self.assertEqual(curr_in_2.get_min_row(), 1)
        self.assertEqual(curr_in_2.get_max_row(), 1)
        # Outputs:
        # self.singlet_cdt, "kr", 1
        curr_out = self.script_3_method.outputs.get(dataset_idx=1)
        self.assertEqual(curr_out.dataset_name, "kr")
        self.assertEqual(curr_out.dataset_idx, 1)
        self.assertEqual(curr_out.get_cdt(), self.singlet_cdt)
        self.assertEqual(curr_out.get_min_row(), None)
        self.assertEqual(curr_out.get_max_row(), None)

    def test_copy_io_from_parent(self):
        """Test save when revision parent is specified."""

        # DNAcompv2_m should have 1 input, copied from DNAcompv1
        self.assertEqual(self.DNAcompv2_m.inputs.count(), 1)
        curr_in = self.DNAcompv2_m.inputs.get(dataset_idx=1)
        self.assertEqual(curr_in.dataset_name,
                         self.DNAinput_ti.dataset_name)
        self.assertEqual(curr_in.dataset_idx,
                         self.DNAinput_ti.dataset_idx)
        self.assertEqual(curr_in.get_cdt(),
                         self.DNAinput_ti.get_cdt())

        self.assertEqual(self.DNAcompv2_m.outputs.count(), 1)
        curr_out = self.DNAcompv2_m.outputs.get(dataset_idx=1)
        self.assertEqual(curr_out.dataset_name,
                         self.DNAoutput_to.dataset_name)
        self.assertEqual(curr_out.dataset_idx,
                         self.DNAoutput_to.dataset_idx)
        self.assertEqual(curr_out.get_cdt(),
                         self.DNAoutput_to.get_cdt())

        # Multiple output case (using script_2_method).
        foo = Method(family=self.test_mf, driver=self.script_2_crRev,
                     revision_parent=self.script_2_method, user=self.myUser)
        foo.save()
        foo.copy_io_from_parent()
        # Check that it has the same input as script_2_method:
        # self.triplet_cdt, "a_b_c", 1
        curr_in = foo.inputs.get(dataset_idx=1)
        self.assertEqual(curr_in.dataset_name, "a_b_c")
        self.assertEqual(curr_in.dataset_idx, 1)
        self.assertEqual(curr_in.get_cdt(), self.triplet_cdt)
        self.assertEqual(curr_in.get_min_row(), None)
        self.assertEqual(curr_in.get_max_row(), None)
        # Outputs:
        # self.triplet_cdt, "a_b_c_squared", 1
        # self.singlet_cdt, "a_b_c_mean", 2
        curr_out_1 = foo.outputs.get(dataset_idx=1)
        curr_out_2 = foo.outputs.get(dataset_idx=2)
        self.assertEqual(curr_out_1.get_cdt(), self.triplet_cdt)
        self.assertEqual(curr_out_1.dataset_name, "a_b_c_squared")
        self.assertEqual(curr_out_1.dataset_idx, 1)
        self.assertEqual(curr_out_1.get_min_row(), None)
        self.assertEqual(curr_out_1.get_max_row(), None)
        self.assertEqual(curr_out_2.get_cdt(), self.singlet_cdt)
        self.assertEqual(curr_out_2.dataset_name, "a_b_c_mean")
        self.assertEqual(curr_out_2.dataset_idx, 2)
        self.assertEqual(curr_out_2.get_min_row(), None)
        self.assertEqual(curr_out_2.get_max_row(), None)

        # Multiple input case (using script_3_method).
        bar = Method(family=self.test_mf, driver=self.script_3_crRev,
                     revision_parent=self.script_3_method, user=self.myUser)
        bar.save()
        bar.copy_io_from_parent()
        # Check that the outputs match script_3_method:
        # self.singlet_cdt, "k", 1
        # self.singlet_cdt, "r", 2, min_row = max_row = 1
        curr_in_1 = bar.inputs.get(dataset_idx=1)
        curr_in_2 = bar.inputs.get(dataset_idx=2)
        self.assertEqual(curr_in_1.get_cdt(), self.singlet_cdt)
        self.assertEqual(curr_in_1.dataset_name, "k")
        self.assertEqual(curr_in_1.dataset_idx, 1)
        self.assertEqual(curr_in_1.get_min_row(), None)
        self.assertEqual(curr_in_1.get_max_row(), None)
        self.assertEqual(curr_in_2.get_cdt(), self.singlet_cdt)
        self.assertEqual(curr_in_2.dataset_name, "r")
        self.assertEqual(curr_in_2.dataset_idx, 2)
        self.assertEqual(curr_in_2.get_min_row(), 1)
        self.assertEqual(curr_in_2.get_max_row(), 1)
        # Outputs:
        # self.singlet_cdt, "kr", 1
        curr_out = bar.outputs.get(dataset_idx=1)
        self.assertEqual(curr_out.get_cdt(), self.singlet_cdt)
        self.assertEqual(curr_out.dataset_name, "kr")
        self.assertEqual(curr_out.dataset_idx, 1)
        self.assertEqual(curr_out.get_min_row(), None)
        self.assertEqual(curr_out.get_max_row(), None)


        # If there are already inputs and outputs specified, then
        # they should not be overwritten.

        old_cdt = self.DNAinput_ti.get_cdt()
        old_name = self.DNAinput_ti.dataset_name
        old_idx = self.DNAinput_ti.dataset_idx

        self.DNAcompv1_m.revision_parent = self.RNAcompv2_m
        self.DNAcompv1_m.save()
        self.DNAcompv1_m.copy_io_from_parent()
        self.assertEqual(self.DNAcompv1_m.inputs.count(), 1)
        curr_in = self.DNAcompv1_m.inputs.get(dataset_idx=1)
        self.assertEqual(curr_in.get_cdt(), old_cdt)
        self.assertEqual(curr_in.dataset_name, old_name)
        self.assertEqual(curr_in.dataset_idx, old_idx)

        old_cdt = self.DNAoutput_to.get_cdt()
        old_name = self.DNAoutput_to.dataset_name
        old_idx = self.DNAoutput_to.dataset_idx

        self.assertEqual(self.DNAcompv2_m.outputs.count(), 1)
        curr_out = self.DNAcompv2_m.outputs.get(dataset_idx=1)
        self.assertEqual(curr_out.get_cdt(), old_cdt)
        self.assertEqual(curr_out.dataset_name, old_name)
        self.assertEqual(curr_out.dataset_idx, old_idx)

        # Only inputs specified.
        bar.outputs.all().delete()
        bar.save()
        bar.copy_io_from_parent()
        self.assertEqual(bar.inputs.count(), 2)
        self.assertEqual(bar.outputs.count(), 0)
        curr_in_1 = bar.inputs.get(dataset_idx=1)
        curr_in_2 = bar.inputs.get(dataset_idx=2)
        self.assertEqual(curr_in_1.get_cdt(), self.singlet_cdt)
        self.assertEqual(curr_in_1.dataset_name, "k")
        self.assertEqual(curr_in_1.dataset_idx, 1)
        self.assertEqual(curr_in_1.get_min_row(), None)
        self.assertEqual(curr_in_1.get_max_row(), None)
        self.assertEqual(curr_in_2.get_cdt(), self.singlet_cdt)
        self.assertEqual(curr_in_2.dataset_name, "r")
        self.assertEqual(curr_in_2.dataset_idx, 2)
        self.assertEqual(curr_in_2.get_min_row(), 1)
        self.assertEqual(curr_in_2.get_max_row(), 1)

        # Only outputs specified.
        foo.inputs.all().delete()
        foo.save()
        foo.copy_io_from_parent()
        self.assertEqual(foo.inputs.count(), 0)
        self.assertEqual(foo.outputs.count(), 2)
        curr_out_1 = foo.outputs.get(dataset_idx=1)
        curr_out_2 = foo.outputs.get(dataset_idx=2)
        self.assertEqual(curr_out_1.get_cdt(), self.triplet_cdt)
        self.assertEqual(curr_out_1.dataset_name, "a_b_c_squared")
        self.assertEqual(curr_out_1.dataset_idx, 1)
        self.assertEqual(curr_out_1.get_min_row(), None)
        self.assertEqual(curr_out_1.get_max_row(), None)
        self.assertEqual(curr_out_2.get_cdt(), self.singlet_cdt)
        self.assertEqual(curr_out_2.dataset_name, "a_b_c_mean")
        self.assertEqual(curr_out_2.dataset_idx, 2)
        self.assertEqual(curr_out_2.get_min_row(), None)
        self.assertEqual(curr_out_2.get_max_row(), None)

    def test_driver_is_metapackage(self):
        """
        A metapackage cannot be a driver for a Method.
        """
        # Create a CodeResourceRevision with no content file (ie. a Metapackage).
        res = CodeResource(user=self.myUser); res.save()
        rev = CodeResourceRevision(coderesource=res, content_file=None, user=self.myUser); rev.clean(); rev.save()
        f = MethodFamily(user=self.myUser); f.save()
        m = Method(family=f, driver=rev, user=self.myUser)
        m.save()
        m.create_input(compounddatatype = self.singlet_cdt,
            dataset_name = "input",
            dataset_idx = 1)
        self.assertRaisesRegexp(ValidationError,
                                re.escape('Method "{}" cannot have CodeResourceRevision "{}" as a driver, because it '
                                          'has no content file.'.format(m, rev)),
                                m.clean)

    def test_invoke_code_nooutput(self):
        """
        Invoke a no-output method (which just prints to stdout).
        """
        empty_dir = tempfile.mkdtemp(
            dir=file_access_utils.sandbox_base_path()
        )
        file_access_utils.configure_sandbox_permissions(empty_dir)

        proc = self.noop_method.invoke_code(empty_dir, [self.noop_infile], [])
        proc_out, _ = proc.communicate()

        self.assertEqual(proc_out, self.noop_indata)

        shutil.rmtree(empty_dir)

    def test_invoke_code_dir_not_empty(self):
        """
        Trying to invoke code in a non-empty directory should fail.
        """
        self.assertRaisesRegexp(ValueError,
            "Directory .* nonempty; contains file .*",
            lambda : self.noop_method.invoke_code(self.scratch_dir, [self.noop_infile], []))

    def test_delete_method(self):
        """Deleting a method is possible."""
        self.assertIsNone(Method.objects.first().delete())

    def test_identical_self(self):
        """A Method should be identical to itself."""
        m = Method.objects.first()
        self.assertTrue(m.is_identical(m))

    def test_identical_different_names(self):
        """Two methods differing only in names are identical."""
        m1 = Method.objects.filter(inputs__isnull=False, outputs__isnull=False).first()
        m2 = Method(revision_name="x" + m1.revision_name, driver=m1.driver, family=MethodFamily.objects.first(),
                    user=self.myUser)
        m2.save()
        for input in m1.inputs.order_by("dataset_idx"):
            m2.create_input("x" + input.dataset_name, 
                    compounddatatype=input.compounddatatype,
                    min_row=input.get_min_row(), 
                    max_row=input.get_max_row())
        for output in m1.outputs.order_by("dataset_idx"):
            m2.create_output("x" + output.dataset_name, 
                    compounddatatype=output.compounddatatype,
                    min_row=output.get_min_row(), 
                    max_row=output.get_max_row())
        self.assertFalse(m1.revision_name == m2.revision_name)
        self.assertFalse(m1.inputs.first().dataset_name == m2.inputs.first().dataset_name)
        self.assertFalse(m1.outputs.first().dataset_name == m2.outputs.first().dataset_name)
        self.assertTrue(m1.is_identical(m2))

    def test_identical_different_drivers(self):
        """Two methods with identical IO, but different drivers, are not identical."""
        m1 = Method.objects.filter(inputs__isnull=False, outputs__isnull=False).first()
        driver = CodeResourceRevision.objects.exclude(pk=m1.driver.pk).first()
        m2 = Method(revision_name=m1.revision_name, driver=driver, family=m1.family, user=self.myUser)
        m2.save()
        for input in m1.inputs.order_by("dataset_idx"):
            m2.create_input("x" + input.dataset_name, 
                    compounddatatype=input.compounddatatype,
                    min_row=input.get_min_row(), 
                    max_row=input.get_max_row())
        for output in m1.outputs.order_by("dataset_idx"):
            m2.create_output("x" + output.dataset_name, 
                    compounddatatype=output.compounddatatype,
                    min_row=output.get_min_row(), 
                    max_row=output.get_max_row())
        self.assertTrue(super(Method, m1).is_identical(super(Method, m2)))
        self.assertFalse(m1.driver.pk == m2.driver.pk)
        self.assertFalse(m1.is_identical(m2))

    def test_create(self):
        """Create a new Method by the constructor."""
        names = ["a", "b"]
        cdts = CompoundDatatype.objects.all()[:2]
        family = MethodFamily.objects.first()
        driver = CodeResourceRevision.objects.first()
        m = Method.create(names, compounddatatypes=cdts, num_inputs=1, family=family, driver=driver, user=self.myUser)
        self.assertIsNone(m.complete_clean())

    def test_find_update_not_found(self):
        update = self.RNAcompv2_m.find_update()
        
        self.assertEqual(update, None)

    def test_find_update(self):
        update = self.RNAcompv1_m.find_update()
        
        self.assertEqual(update, self.RNAcompv2_m)

    def test_find_update_not_found_from_transformation(self):
        transformation = Transformation.objects.get(pk=self.RNAcompv2_m.pk)
        update = transformation.find_update()
        
        self.assertEqual(update, None)


class MethodFamilyTests(MethodTestCase):

    def test_unicode(self):
        """
        unicode() for MethodFamily should display it's name.
        """
        
        self.assertEqual(unicode(self.DNAcomp_mf), "DNAcomplement")


class NonReusableMethodTests(TransactionTestCase):
    # fixtures = ["initial_data", "initial_groups", "initial_user"]

    def setUp(self):
        # Loading the fixtures using the 'fixtures' attribute doesn't work due to
        # subtleties in how Django's tests run.
        call_command("loaddata", "initial_groups", verbosity=0)
        call_command("loaddata", "initial_user", verbosity=0)
        call_command("loaddata", "initial_data", verbosity=0)

        # An unpredictable, non-reusable user.
        self.user_rob = User.objects.create_user('rob', 'rford@toronto.ca', 'football')
        self.user_rob.save()
        self.user_rob.groups.add(everyone_group())
        self.user_rob.save()

        # A piece of code that is non-reusable.
        self.rng = tools.make_first_revision(
            "rng", "Generates a random number", "rng.py",
            """#! /usr/bin/env python

import random
import csv
import sys

outfile = sys.argv[1]

with open(outfile, "wb") as f:
    my_writer = csv.writer(f)
    my_writer.writerow(("random number",))
    my_writer.writerow((random.random(),))
""",
            self.user_rob
        )

        self.rng_out_cdt = CompoundDatatype(user=self.user_rob)
        self.rng_out_cdt.save()
        self.rng_out_cdt.members.create(
            column_name="random number", column_idx=1,
            datatype=Datatype.objects.get(pk=datatypes.FLOAT_PK)
        )
        self.rng_out_cdt.grant_everyone_access()

        self.rng_method = tools.make_first_method("rng", "Generate a random number", self.rng,
                                                  self.user_rob)
        self.rng_method.create_output(dataset_name="random_number", dataset_idx=1, compounddatatype=self.rng_out_cdt,
                                      min_row=1, max_row=1)
        self.rng_method.reusable = Method.NON_REUSABLE
        self.rng_method.save()

        self.increment = tools.make_first_revision(
            "increment", "Increments all numbers in its first input file by the number in its second",
            "increment.py",
            """#! /usr/bin/env python

import csv
import sys

numbers_file = sys.argv[1]
increment_file = sys.argv[2]
outfile = sys.argv[3]

incrementor = 0
with open(increment_file, "rb") as f:
    inc_reader = csv.DictReader(f)
    for row in inc_reader:
        incrementor = float(row["incrementor"])
        break

numbers = []
with open(numbers_file, "rb") as f:
    number_reader = csv.DictReader(f)
    for row in number_reader:
        numbers.append(float(row["number"]))

with open(outfile, "wb") as f:
    out_writer = csv.writer(f)
    out_writer.writerow(("incremented number",))
    for number in numbers:
        out_writer.writerow((number + incrementor,))
""",
            self.user_rob
        )

        self.increment_in_1_cdt = CompoundDatatype(user=self.user_rob)
        self.increment_in_1_cdt.save()
        self.increment_in_1_cdt.members.create(
            column_name="number", column_idx=1,
            datatype=Datatype.objects.get(pk=datatypes.FLOAT_PK)
        )
        self.increment_in_1_cdt.grant_everyone_access()

        self.increment_in_2_cdt = CompoundDatatype(user=self.user_rob)
        self.increment_in_2_cdt.save()
        self.increment_in_2_cdt.members.create(
            column_name="incrementor", column_idx=1,
            datatype=Datatype.objects.get(pk=datatypes.FLOAT_PK)
        )
        self.increment_in_2_cdt.grant_everyone_access()

        self.increment_out_cdt = CompoundDatatype(user=self.user_rob)
        self.increment_out_cdt.save()
        self.increment_out_cdt.members.create(
            column_name="incremented number", column_idx=1,
            datatype=Datatype.objects.get(pk=datatypes.FLOAT_PK)
        )
        self.increment_out_cdt.grant_everyone_access()

        self.inc_method = tools.make_first_method(
            "increment", "Increments all numbers in its first input file by the number in its second",
            self.increment, self.user_rob)
        self.inc_method.create_input(dataset_name="numbers", dataset_idx=1, compounddatatype=self.increment_in_1_cdt)
        self.inc_method.create_input(dataset_name="incrementor", dataset_idx=2,
                                     compounddatatype=self.increment_in_2_cdt,
                                     min_row=1, max_row=1)
        self.inc_method.create_output(dataset_name="incremented_numbers", dataset_idx=1,
                                      compounddatatype=self.increment_out_cdt)

        self.test_nonreusable = tools.make_first_pipeline("Non-Reusable", "Pipeline with a non-reusable step",
                                                          self.user_rob)
        self.test_nonreusable.create_input(dataset_name="numbers", dataset_idx=1,
                                           compounddatatype=self.increment_in_1_cdt)
        _step1 = self.test_nonreusable.steps.create(
            step_num=1,
            transformation=self.rng_method,
            name="source of randomness"
        )

        step2 = self.test_nonreusable.steps.create(
            step_num=2,
            transformation=self.inc_method,
            name="incrementor"
        )
        step2.cables_in.create(
            dest=self.inc_method.inputs.get(dataset_name="numbers"),
            source_step=0,
            source=self.test_nonreusable.inputs.get(dataset_name="numbers")
        )
        connecting_cable = step2.cables_in.create(
            dest=self.inc_method.inputs.get(dataset_name="incrementor"),
            source_step=1,
            source=self.rng_method.outputs.get(dataset_name="random_number")
        )
        connecting_cable.custom_wires.create(
            source_pin=self.rng_out_cdt.members.get(column_name="random number"),
            dest_pin=self.increment_in_2_cdt.members.get(column_name="incrementor")
        )

        self.test_nonreusable.create_outcable(
            output_name="incremented_numbers",
            output_idx=1,
            source_step=2,
            source=self.inc_method.outputs.get(dataset_name="incremented_numbers")
        )

        self.test_nonreusable.create_outputs()

        # A data file to add to the database.
        self.numbers = "number\n1\n2\n3\n4\n"
        datafile = tempfile.NamedTemporaryFile(delete=False)
        datafile.write(self.numbers)
        datafile.close()

        # Alice uploads the data to the system.
        self.numbers_dataset = librarian.models.Dataset.create_dataset(
            datafile.name,
            user=self.user_rob,
            groups_allowed=[everyone_group()],
            cdt=self.increment_in_1_cdt, keep_file=True,
            name="numbers", description="1-2-3-4"
        )

    def tearDown(self):
        # Our tests fail post-teardown without this.
        update_all_contenttypes(verbosity=0)

    def test_find_compatible_ER_non_reusable_method(self):
        """
        The ExecRecord of a non-reusable Method should not be found compatible.
        """
        Manager.execute_pipeline(self.user_rob, self.test_nonreusable, [self.numbers_dataset])

        rng_step = self.test_nonreusable.steps.get(step_num=1)
        runstep = rng_step.pipelinestep_instances.first()
        self.assertListEqual(list(runstep.find_compatible_ERs([])), [])

    def test_execute_does_not_reuse(self):
        """
        Running a non-reusable Method twice does not reuse an ExecRecord, and
        subsequent steps and cables in the same Pipeline will have different ExecRecords also.
        """
        run = Manager.execute_pipeline(self.user_rob, self.test_nonreusable, [self.numbers_dataset]).get_last_run()
        first_step_1 = run.runsteps.get(pipelinestep__step_num=1)
        second_step_1 = run.runsteps.get(pipelinestep__step_num=2)
        joining_cable_1 = second_step_1.RSICs.get(PSIC__dest=self.inc_method.inputs.get(dataset_name="incrementor"))

        run2 = Manager.execute_pipeline(self.user_rob, self.test_nonreusable, [self.numbers_dataset]).get_last_run()
        first_step_2 = run2.runsteps.get(pipelinestep__step_num=1)
        second_step_2 = run2.runsteps.get(pipelinestep__step_num=2)
        joining_cable_2 = second_step_2.RSICs.get(PSIC__dest=self.inc_method.inputs.get(dataset_name="incrementor"))

        self.assertNotEqual(first_step_1.execrecord, first_step_2.execrecord)
        self.assertNotEqual(second_step_1.execrecord, second_step_2.execrecord)
        self.assertNotEqual(joining_cable_1.execrecord, joining_cable_2.execrecord)


class MethodFamilyApiTests(BaseTestCases.ApiTestCase):
    fixtures = ['demo']
    
    def setUp(self):
        super(MethodFamilyApiTests, self).setUp()

        self.list_path = reverse("methodfamily-list")
        self.detail_pk = 2
        self.detail_path = reverse("methodfamily-detail",
                                   kwargs={'pk': self.detail_pk})
        self.removal_path = reverse("methodfamily-removal-plan",
                                    kwargs={'pk': self.detail_pk})

        # This should equal metadata.ajax.CompoundDatatypeViewSet.as_view({"get": "list"}).
        self.list_view, _, _ = resolve(self.list_path)
        self.detail_view, _, _ = resolve(self.detail_path)
        self.removal_view, _, _ = resolve(self.removal_path)

    def test_list(self):
        """
        Test the CompoundDatatype API list view.
        """
        request = self.factory.get(self.list_path)
        force_authenticate(request, user=self.kive_user)
        response = self.list_view(request, pk=None)

        # There are four CDTs loaded into the Database by default.
        self.assertEquals(len(response.data), 7)
        self.assertEquals(response.data[6]['name'], 'sums and products')

    def test_detail(self):
        request = self.factory.get(self.detail_path)
        force_authenticate(request, user=self.kive_user)
        response = self.detail_view(request, pk=self.detail_pk)
        self.assertEquals(response.data['name'], 'sums and products')

    def test_removal_plan(self):
        request = self.factory.get(self.removal_path)
        force_authenticate(request, user=self.kive_user)
        response = self.removal_view(request, pk=self.detail_pk)
        self.assertEquals(response.data['MethodFamilies'], 1)

    def test_removal(self):
        start_count = MethodFamily.objects.all().count()
        
        request = self.factory.delete(self.detail_path)
        force_authenticate(request, user=self.kive_user)
        response = self.detail_view(request, pk=self.detail_pk)
        self.assertEquals(response.status_code, status.HTTP_204_NO_CONTENT)

        end_count = MethodFamily.objects.all().count()
        self.assertEquals(end_count, start_count - 1)


class CodeResourceApiTests(BaseTestCases.ApiTestCase):
    fixtures = ["removal"]

    def setUp(self):
        super(CodeResourceApiTests, self).setUp()

        self.list_path = reverse("coderesource-list")
        self.list_view, _, _ = resolve(self.list_path)

        # This user is defined in the removal fixture.
        self.remover = User.objects.get(pk=2)
        self.noop_cr = CodeResource.objects.get(name="Noop")

        self.detail_path = reverse("coderesource-detail", kwargs={"pk": self.noop_cr.pk})
        self.detail_view, _, _ = resolve(self.detail_path)
        self.removal_plan = self.noop_cr.build_removal_plan()

    def test_list_url(self):
        """
        Test that the API list URL is correctly defined.
        """
        # Check that the URL is correctly defined.
        self.assertEquals(self.list_path, "/api/coderesources/")

    def test_list(self):
        request = self.factory.get(self.list_path)
        force_authenticate(request, user=self.remover)
        response = self.list_view(request, pk=None)

        self.assertSetEqual(
            set([x["name"] for x in response.data]),
            set([x.name for x in CodeResource.objects.filter(user=self.remover)])
        )

    def test_detail(self):
        self.assertEquals(self.detail_path, "/api/coderesources/{}/".format(self.noop_cr.pk))

        request = self.factory.get(self.detail_path)
        force_authenticate(request, user=self.remover)
        response = self.detail_view(request, pk=self.noop_cr.pk)
        detail = response.data

        self.assertEquals(detail["id"], self.noop_cr.pk)
        self.assertEquals(detail["num_revisions"], self.noop_cr.num_revisions)
        self.assertRegexpMatches(
            detail["absolute_url"],
            "/resource_revisions/{}/?".format(self.noop_cr.pk)
        )

    def test_removal_plan(self):
        cr_removal_path = reverse("coderesource-removal-plan", kwargs={'pk': self.noop_cr.pk})
        cr_removal_view, _, _ = resolve(cr_removal_path)

        request = self.factory.get(cr_removal_path)
        force_authenticate(request, user=self.remover)
        response = cr_removal_view(request, pk=self.noop_cr.pk)

        for key in self.removal_plan:
            self.assertEquals(response.data[key], len(self.removal_plan[key]))
        self.assertEquals(response.data['CodeResources'], 1)

        # Noop is a dependency of Pass Through, so:
        self.assertEquals(response.data["CodeResourceRevisions"], 2)

    def test_removal(self):
        start_count = CodeResource.objects.count()
        start_crr_count = CodeResourceRevision.objects.count()

        request = self.factory.delete(self.detail_path)
        force_authenticate(request, user=self.kive_user)
        response = self.detail_view(request, pk=self.noop_cr.pk)
        self.assertEquals(response.status_code, status.HTTP_204_NO_CONTENT)

        end_count = CodeResource.objects.count()
        end_crr_count = CodeResourceRevision.objects.count()
        self.assertEquals(end_count, start_count - len(self.removal_plan["CodeResources"]))
        # Noop is a dependency of Pass Through, so it should also take out the other CodeResourceRevision.
        self.assertEquals(end_crr_count, start_crr_count - len(self.removal_plan["CodeResourceRevisions"]))

    def test_revisions(self):
        cr_revisions_path = reverse("coderesource-revisions", kwargs={"pk": self.noop_cr.pk})
        cr_revisions_view, _, _ = resolve(cr_revisions_path)

        request = self.factory.get(cr_revisions_path)
        force_authenticate(request, user=self.remover)
        response = cr_revisions_view(request, pk=self.noop_cr.pk)

        self.assertSetEqual(set([x.revision_number for x in self.noop_cr.revisions.all()]),
                             set([x["revision_number"] for x in response.data]))


def crr_test_setup(case):
    """
    A helper for CodeResourceRevisionApiTests and CodeResourceRevisionSerializerTests.
    """
    # An innocent bystander.
    case.innocent_bystander = User.objects.create_user(
        "InnocentBystander", "innocent_bystander_1@aol.net", password="WhoMe?")

    # A mock request that we pass as context to our serializer.
    class DuckRequest(object):
        pass

    case.duck_request = DuckRequest()
    case.duck_request.user = kive_user()
    case.duck_context = {"request": case.duck_request}

    case.cr_name = "Deserialization Test Family"
    case.cr = CodeResource(name=case.cr_name,
                           filename="HelloWorld.py",
                           description="Hello World",
                           user=kive_user())
    case.cr.save()
    case.cr.grant_everyone_access()

    with tempfile.TemporaryFile() as f:
        f.write("""#!/bin/bash

echo "Hello World"
""")
        f.seek(0)
        case.staged_file = portal.models.StagedFile(
            uploaded_file=File(f),
            user=kive_user()
        )
        case.staged_file.save()

    case.crr_data = {
        "coderesource": case.cr_name,
        "revision_name": "v1",
        "revision_desc": "First version",
        "staged_file": case.staged_file.pk,
        "groups_allowed": [everyone_group().name]
    }

    with tempfile.TemporaryFile() as f:
        f.write("""language = ENG""")
        f.seek(0)
        case.crd = CodeResourceRevision(
            coderesource=case.cr,
            revision_name="dependency",
            revision_desc="Dependency",
            user=kive_user(),
            content_file=File(f))
        case.crd.clean()
        case.crd.save()
        case.crd.grant_everyone_access()

    case.crr_data_with_dep = copy.deepcopy(case.crr_data)
    case.crr_data_with_dep["dependencies"] = [
        {
            "requirement": case.crd.pk,
            "depFileName": "config.dat"
        },
        {
            "requirement": case.crd.pk,
            "depPath": "configuration.dat",
            "depFileName": "config_2.dat"
        }
    ]


class CodeResourceRevisionSerializerTests(TestCase):
    fixtures = ["removal"]

    def setUp(self):
        # This user is defined in the removal fixture.
        self.remover = User.objects.get(pk=2)
        crr_test_setup(self)

    def tearDown(self):
        tools.clean_up_all_files()

    # Note: all validation tests are redundant.  There is no customized validation code anymore.
    def test_validate_nodep(self):
        """
        Test validation of a CodeResourceRevision with no dependencies.
        """
        crr_s = CodeResourceRevisionSerializer(
            data=self.crr_data,
            context={"request": self.duck_request}
        )
        self.assertTrue(crr_s.is_valid())

    def test_create_nodep(self):
        """
        Test creation of a CodeResourceRevision with no dependencies.
        """
        staged_file_pk = self.staged_file.pk

        crr_s = CodeResourceRevisionSerializer(
            data=self.crr_data,
            context={"request": self.duck_request}
        )
        crr_s.is_valid()
        crr_s.save()

        # Inspect the revision we just added.
        new_crr = self.cr.revisions.get(revision_name="v1")
        self.assertEquals(new_crr.revision_desc, "First version")
        self.assertEquals(new_crr.dependencies.count(), 0)

        # Make sure the staged file was removed.
        self.assertFalse(portal.models.StagedFile.objects.filter(pk=staged_file_pk).exists())

    def test_validate_with_dep(self):
        """
        Test validation of a CodeResourceRevision with no dependencies.
        """
        crr_s = CodeResourceRevisionSerializer(
            data=self.crr_data_with_dep,
            context={"request": self.duck_request}
        )
        self.assertTrue(crr_s.is_valid())

    def test_create_with_dep(self):
        """
        Test creation of a CodeResourceRevision with dependencies.
        """
        crr_s = CodeResourceRevisionSerializer(
            data=self.crr_data_with_dep,
            context={"request": self.duck_request}
        )
        crr_s.is_valid()
        crr_s.save()

        # Inspect the revision we just added.
        new_crr = self.cr.revisions.get(revision_name="v1")
        self.assertEquals(new_crr.revision_desc, "First version")
        self.assertEquals(new_crr.dependencies.count(), 2)

        new_dep = new_crr.dependencies.get(depFileName="config.dat")
        self.assertEquals(new_dep.requirement, self.crd)
        self.assertEquals(new_dep.depPath, "")

        new_dep = new_crr.dependencies.get(depFileName="config_2.dat")
        self.assertEquals(new_dep.requirement, self.crd)
        self.assertEquals(new_dep.depPath, "configuration.dat")

    def test_validate_not_your_staged_file(self):
        """
        If the StagedFile specified is not the user's, then complain.
        """
        self.duck_request.user = self.remover
        crr_s = CodeResourceRevisionSerializer(
            data=self.crr_data_with_dep,
            context={"request": self.duck_request}
        )
        self.cr.grant_everyone_access()

        self.assertFalse(crr_s.is_valid())
        self.assertTrue("staged_file" in crr_s.errors)


class CodeResourceRevisionApiTests(BaseTestCases.ApiTestCase):
    fixtures = ["removal"]

    def setUp(self):
        # This user is defined in the removal fixture.
        self.remover = User.objects.get(username="Rem Over")
        super(CodeResourceRevisionApiTests, self).setUp()

        self.list_path = reverse("coderesourcerevision-list")
        self.list_view, _, _ = resolve(self.list_path)

        self.noop_cr = CodeResource.objects.get(name="Noop")
        self.noop_crr = self.noop_cr.revisions.get(revision_number=1)

        self.detail_path = reverse("coderesourcerevision-detail", kwargs={"pk": self.noop_crr.pk})
        self.detail_view, _, _ = resolve(self.detail_path)

        self.removal_plan = self.noop_crr.build_removal_plan()

        self.download_path = reverse("coderesourcerevision-download", kwargs={"pk": self.noop_crr.pk})
        self.download_view, _, _ = resolve(self.download_path)

        crr_test_setup(self)

    def tearDown(self):
        tools.clean_up_all_files()

    def test_list(self):
        request = self.factory.get(self.list_path)
        force_authenticate(request, user=self.remover)
        response = self.list_view(request, pk=None)

        self.assertItemsEqual(
            [x.pk for x in CodeResourceRevision.filter_by_user(user=self.remover)],
            [x["id"] for x in response.data]
        )

    def test_detail(self):
        request = self.factory.get(self.detail_path)
        force_authenticate(request, user=self.remover)
        response = self.detail_view(request, pk=self.noop_crr.pk)
        detail = response.data

        self.assertEquals(detail["id"], self.noop_crr.pk)
        self.assertRegexpMatches(
            detail["absolute_url"],
            "/resource_revision_add/{}/?".format(self.noop_crr.pk)
        )
        self.assertEquals(detail["revision_name"], self.noop_crr.revision_name)

    def test_removal_plan(self):
        crr_removal_path = reverse("coderesourcerevision-removal-plan", kwargs={'pk': self.noop_crr.pk})
        crr_removal_view, _, _ = resolve(crr_removal_path)

        request = self.factory.get(crr_removal_path)
        force_authenticate(request, user=self.remover)
        response = crr_removal_view(request, pk=self.noop_crr.pk)

        for key in self.removal_plan:
            self.assertEquals(response.data[key], len(self.removal_plan[key]))
        # This CRR is a dependency of another one, so:
        self.assertEquals(response.data["CodeResourceRevisions"], 2)

    def test_removal(self):
        start_count = CodeResourceRevision.objects.count()

        request = self.factory.delete(self.detail_path)
        force_authenticate(request, user=self.kive_user)
        response = self.detail_view(request, pk=self.noop_crr.pk)
        self.assertEquals(response.status_code, status.HTTP_204_NO_CONTENT)

        end_count = CodeResourceRevision.objects.count()
        # In the above we confirmed this length is 2.
        self.assertEquals(end_count, start_count - len(self.removal_plan["CodeResourceRevisions"]))

    def test_create(self):
        """
        Test creation of a new CodeResourceRevision via the API.
        """
        staged_file_pk = self.staged_file.pk
        request = self.factory.post(self.list_path, self.crr_data)
        force_authenticate(request, user=kive_user())
        self.list_view(request)

        # Inspect the revision we just added.
        new_crr = self.cr.revisions.get(revision_name="v1")
        self.assertEquals(new_crr.revision_desc, "First version")
        self.assertEquals(new_crr.dependencies.count(), 0)

        # Make sure the staged file was removed.
        self.assertFalse(portal.models.StagedFile.objects.filter(pk=staged_file_pk).exists())

    def test_create_clean_fails(self):
        """
        Test that clean is being called during creation.
        """
        # Add a bad dependency to crr_data.
        self.crr_data["dependencies"] = [
            {
                "requirement": self.crd.pk,
                "depPath": "../../jailbroken"
            }
        ]

        request = self.factory.post(self.list_path, self.crr_data, format="json")
        force_authenticate(request, user=kive_user())
        response = self.list_view(request)
        
        self.assertDictEqual(
            response.data,
            { 'non_field_errors': "depPath cannot reference ../" })

    def test_download(self):
        request = self.factory.get(self.download_path)
        force_authenticate(request, user=self.remover)
        response = self.download_view(request, pk=self.noop_crr.pk)

        self.assertIn("Content-Disposition", response)
        self.assertTrue(response["Content-Disposition"].startswith("attachment; filename="))


def method_test_setup(case):
    """
    Helper to set up MethodSerializerTests and MethodApiTests.
    """
    crr_test_setup(case)

    # We need a CodeResourceRevision to create a Method from.
    crr_s = CodeResourceRevisionSerializer(data=case.crr_data, context=case.duck_context)
    crr_s.is_valid()
    case.crr = crr_s.save()

    # We need a MethodFamily to add the Method to.
    case.dtf_mf = MethodFamily(
        name="Deserialization Test Family Methods",
        description="For testing the Method serializer.",
        user=kive_user()
    )
    case.dtf_mf.save()
    case.dtf_mf.users_allowed.add(case.innocent_bystander)
    case.dtf_mf.grant_everyone_access()
    case.dtf_mf.save()

    case.method_data = {
        "family": case.dtf_mf.name,
        "revision_name": "v1",
        "revision_desc": "First version",
        "users_allowed": [case.innocent_bystander.username],
        "groups_allowed": [everyone_group().name],
        "driver": case.crr.pk,
        "inputs": [
            {
                "dataset_name": "ignored_input",
                "dataset_idx": 1,
                "x": 0.1,
                "y": 0.1
            },
            {
                "dataset_name": "another_ignored_input",
                "dataset_idx": 2,
                "x": 0.1,
                "y": 0.2
            }
        ],
        "outputs": [
            {
                "dataset_name": "empty_output",
                "dataset_idx": 1
            }
        ]
    }


class MethodSerializerTests(TestCase):
    fixtures = ["removal"]

    def setUp(self):
        method_test_setup(self)

    def tearDown(self):
        tools.clean_up_all_files()

    def test_create(self):
        method_s = MethodSerializer(data=self.method_data, context=self.duck_context)
        self.assertTrue(method_s.is_valid())
        new_method = method_s.save()

        # Probe the new method to see that it got created correctly.
        self.assertEquals(new_method.inputs.count(), 2)
        in_1 = new_method.inputs.get(dataset_idx=1)
        in_2 = new_method.inputs.get(dataset_idx=2)

        self.assertEquals(in_1.dataset_name, "ignored_input")
        self.assertEquals(in_2.dataset_name, "another_ignored_input")

        self.assertEquals(new_method.outputs.count(), 1)
        self.assertEquals(new_method.outputs.first().dataset_name, "empty_output")


class MethodApiTests(BaseTestCases.ApiTestCase):
    fixtures = ['simple_run']

    def setUp(self):
        super(MethodApiTests, self).setUp()

        self.list_path = reverse("method-list")
        self.detail_pk = 2
        self.detail_path = reverse("method-detail",
                                   kwargs={'pk': self.detail_pk})
        self.removal_path = reverse("method-removal-plan",
                                    kwargs={'pk': self.detail_pk})

        self.list_view, _, _ = resolve(self.list_path)
        self.detail_view, _, _ = resolve(self.detail_path)
        self.removal_view, _, _ = resolve(self.removal_path)

        method_test_setup(self)

    def test_list(self):
        """
        Test the API list view.
        """
        request = self.factory.get(self.list_path)
        force_authenticate(request, user=self.kive_user)
        response = self.list_view(request, pk=None)

        self.assertEquals(len(response.data), 6)
        self.assertEquals(response.data[0]['revision_name'], 'mC_name')

    def test_detail(self):
        request = self.factory.get(self.detail_path)
        force_authenticate(request, user=self.kive_user)
        response = self.detail_view(request, pk=self.detail_pk)
        self.assertEquals(response.data['revision_name'], 'mB_name')

    def test_removal_plan(self):
        request = self.factory.get(self.removal_path)
        force_authenticate(request, user=self.kive_user)
        response = self.removal_view(request, pk=self.detail_pk)
        self.assertEquals(response.data['Methods'], 1)

    def test_removal(self):
        start_count = Method.objects.all().count()

        request = self.factory.delete(self.detail_path)
        force_authenticate(request, user=self.kive_user)
        response = self.detail_view(request, pk=self.detail_pk)

        self.assertEquals(response.status_code, status.HTTP_204_NO_CONTENT)

        end_count = Method.objects.all().count()
        self.assertEquals(end_count, start_count - 1)

    def test_create(self):
        request = self.factory.post(self.list_path, self.method_data, format="json")
        force_authenticate(request, user=self.kive_user)
        self.list_view(request)

        # Probe the resulting method.
        new_method = self.dtf_mf.members.get(revision_name=self.method_data["revision_name"])

        self.assertEquals(new_method.inputs.count(), 2)
        self.assertEquals(new_method.outputs.count(), 1)


class InvokeCodeTests(TestCase):
    """
    Tests of Method.invoke_code with and without using an SSH user.
    """
    def setUp(self):
        # A simple pass-through method.
        resource = CodeResource(name="passthrough", filename="passthrough.sh", user=kive_user())
        resource.save()

        with tempfile.NamedTemporaryFile() as f:
            f.write("#!/bin/bash\ncat $1 > $2")
            revision = CodeResourceRevision(coderesource = resource, content_file=File(f),
                                            user=kive_user())
            revision.clean()
            revision.save()

        self.passthrough_mf = MethodFamily(name="passthrough", user=kive_user())
        self.passthrough_mf.save()

        self.passthrough_method = Method(
            family=self.passthrough_mf, driver=revision,
            revision_name="v1", revision_desc="First version",
            user=kive_user())
        self.passthrough_method.save()
        self.passthrough_method.create_input(
            compounddatatype=None, dataset_name="initial_data", dataset_idx=1)
        self.passthrough_method.create_output(
            compounddatatype=None, dataset_name="passthrough_data", dataset_idx=1)
        self.passthrough_method.full_clean()

        # Stake out a place on the filesystem for this file.
        try:
            fd, self.passthrough_input_name = tempfile.mkstemp(dir=file_access_utils.sandbox_base_path())
        finally:
            os.close(fd)

        # Write to this file.
        with open(self.passthrough_input_name, "w") as f:
            f.write("fooooooo")
        file_access_utils.configure_sandbox_permissions(self.passthrough_input_name)

        self.empty_dir = tempfile.mkdtemp(
            dir=file_access_utils.sandbox_base_path()
        )
        file_access_utils.configure_sandbox_permissions(self.empty_dir)

    def tearDown(self):
        tools.clean_up_all_files()
        # if os.path.exists(self.passthrough_input_name):
        #     os.remove(self.passthrough_input_name)
        # shutil.rmtree(self.empty_dir)

    def test_invoke_code(self, ssh_sandbox_worker_account=None):
        """
        Invoke a method's code.  By default this works as the normal user (i.e. without using SSH).
        """
        empty_dir = tempfile.mkdtemp(
            dir=file_access_utils.sandbox_base_path()
        )
        file_access_utils.configure_sandbox_permissions(empty_dir)

        output_filename = os.path.join(self.empty_dir, "test_output.dat")
        passthrough_popen = self.passthrough_method.invoke_code(
            self.empty_dir,
            [self.passthrough_input_name],
            [output_filename],
            ssh_sandbox_worker_account=ssh_sandbox_worker_account
        )
        passthrough_popen.communicate()

        # Check that everything worked OK.
        self.assertEqual(passthrough_popen.returncode, 0)
        with open(self.passthrough_input_name, "r") as f, open(output_filename, "r") as g:
            self.assertEqual(f.read(), g.read())

    @unittest.skipIf(
        not settings.KIVE_SANDBOX_WORKER_ACCOUNT,
        "Kive is not configured to run sandboxes under another account via SSH"
    )
    def test_invoke_code_with_SSH(self):
        """
        Invoke a method as the normal user (i.e. without using SSH).
        """
        self.test_invoke_code(ssh_sandbox_worker_account=settings.KIVE_SANDBOX_WORKER_ACCOUNT)
