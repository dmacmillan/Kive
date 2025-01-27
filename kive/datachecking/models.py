"""
datachecking.models

Shipyard models pertaining to verification of correctness of data.
"""
from __future__ import unicode_literals

from django.db import models
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes import fields
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.utils.encoding import python_2_unicode_compatible
from django.contrib.auth.models import User

import stopwatch.models


@python_2_unicode_compatible
class ContentCheckLog(stopwatch.models.Stopwatch):
    """
    Denotes a check performed on a Dataset's contents.

    One of these should be created basically every time the contents
    of the Dataset are verified, whether that be during the
    execution of a Pipeline (i.e. a Run), on the uploading of data,
    or on a manually-specified check.
    """
    dataset = models.ForeignKey("librarian.Dataset", related_name="content_checks")

    # The execution during which this check occurred, if applicable.
    execlog = models.ForeignKey("archive.ExecLog", null=True, related_name="content_checks")

    # The user performing the check.
    user = models.ForeignKey(User)

    # Implicit through inheritance: start_time, end_time.

    def __str__(self):
        if self.is_fail():
            return str(self.baddata)
        return "OK"

    def add_missing_output(self):
        """Add a BadData for missing output."""
        baddata = BadData(contentchecklog=self, missing_output=True)
        baddata.clean()
        baddata.save()

    def add_file_not_stable(self):
        """Add a BadData for a file with unstable file size."""
        baddata = BadData(contentchecklog=self, file_not_stable=True)
        baddata.clean()
        baddata.save()

    def add_bad_num_rows(self):
        """Add a BadData for bad number of rows."""
        baddata = BadData(contentchecklog=self, bad_num_rows=True)
        baddata.clean()
        baddata.save()

    def add_bad_header(self):
        """Add a BadData for bad number of rows."""
        baddata = BadData(contentchecklog=self, bad_header=True)
        baddata.clean()
        baddata.save()

    def clean(self):
        """
        Check coherence of this ContentCheckLog.

        First, this calls clean on any BadData associated to it.  Second,
        it checks that end_time is later than start_time.  Last, it checks that
        the user has access to the parent Dataset.
        """
        if self.is_fail():
            self.baddata.clean()

        stopwatch.models.Stopwatch.clean(self)

        if not self.dataset.can_be_accessed(self.user):
            raise ValidationError('User "{}" does not have access to Dataset "{}"'.
                                  format(self.user, self.dataset))

    def is_complete(self):
        """
        Checks if this ContentCheckLog is finished; that is, if the end_time is set.
        """
        return self.end_time is not None

    def is_fail(self):
        """
        True if this content check is a failure.
        """
        try:
            self.baddata
        except ObjectDoesNotExist:
            return False
        return True


@python_2_unicode_compatible
class BadData(models.Model):
    """
    Denotes a failed result from a content check.
    """
    contentchecklog = models.OneToOneField(ContentCheckLog, related_name="baddata")
    # In decreasing order of severity....
    missing_output = models.BooleanField(default=False)
    bad_header = models.NullBooleanField()
    bad_num_rows = models.NullBooleanField()
    # Set this if the file was created but the file size never properly
    # stabilized.  This would likely be due to issues with the filesystem.
    file_not_stable = models.NullBooleanField()

    def __str__(self):
        if self.missing_output:
            return "missing output"
        elif self.file_not_stable:
            return "file not stable"
        elif self.bad_header:
            return "malformed header"
        elif self.bad_num_rows:
            return "bad number of rows"
        else:
            return "cell error"

    def clean(self):
        """
        Checks coherence of this BadData record.

        If the output is missing, then bad_num_rows and bad_header
        must be null, and there should be no associated CellErrors; if
        bad_header is set then bad_num_rows must be null and there
        should be no associated CellErrors.

        The existence of associated CellErrors will be tested by
        cleaning them.
        """
        if self.missing_output:
            if self.bad_header is not None:
                raise ValidationError(
                    "BadData \"{}\" represents missing output; bad_header should not be set".
                    format(self))
            
            if self.bad_num_rows is not None:
                raise ValidationError(
                    "BadData \"{}\" represents missing output; bad_num_rows should not be set".
                    format(self))

            return
                
        if self.bad_header is not None:
            if self.bad_num_rows is not None:
                raise ValidationError(
                    "BadData \"{}\" has a malformed header; bad_num_rows should not be set".
                    format(self))

        [c.clean() for c in self.cell_errors.all()]


class CellError(models.Model):
    """
    Represents a cell that fails validation within a BadData object.
    """
    baddata = models.ForeignKey(BadData, related_name="cell_errors")
    row_num = models.PositiveIntegerField()
    column = models.ForeignKey("metadata.CompoundDatatypeMember")

    content_type = models.ForeignKey(
        ContentType,
        limit_choices_to = {
            "model__in": ("BasicConstraint", "CustomConstraint")
        },
        null=True,
        blank=True)
    object_id = models.PositiveIntegerField(null=True)
    # This shows which constraint failed; if it's null that means that
    # the parent BadData object failed the basic type-based check.
    constraint_failed = fields.GenericForeignKey("content_type", "object_id")

    def clean(self):
        """
        Checks coherence of this CellError.

        If the parent BadData object has missing output, is raw, or has
        a bad header, this should not exist.

        The column must be a CDTM belonging to the associated dataset's
        CDT.

        If the associated dataset has num_rows != -1, then row_num must be
        less than or equal to that.

        The constraint failed, if it is not null, must belong to
        that CDTM's Datatype.
        """
        try:
            self.blank.clean()

            if self.constraint_failed is not None:
                raise ValidationError(
                    'CellError "{}" represents a blank and a data constraint failure'.format(self)
                )
        except ObjectDoesNotExist:
            pass

        bad_dataset = self.baddata.contentchecklog.dataset

        if self.baddata.missing_output:
            raise ValidationError(
                "Parent of CellError \"{}\" has missing output, so it should not exist".
                format(self))

        if self.baddata.contentchecklog.dataset.is_raw():
            raise ValidationError(
                "Parent of CellError \"{}\" is raw, so it should not exist".
                format(self))

        if self.baddata.bad_header:
            raise ValidationError(
                "Parent of CellError \"{}\" has a malformed header, so it should not exist".
                format(self))

        if not bad_dataset.structure.compounddatatype.members.filter(
                pk=self.column.pk).exists():
            raise ValidationError(
                "Column of CellError \"{}\" is not one of the columns of its associated Dataset".
                format(self))

        if bad_dataset.structure.num_rows != -1:
            if self.row_num > bad_dataset.structure.num_rows:
                raise ValidationError(
                    "CellError \"{}\" refers to a row that does not exist".
                    format(self))

        # February 7, 2014: the constraints must belong to either the
        # Datatype in question or to one of its supertypes.
        # We could make this test more explicit (and look for the constraint as being
        # exactly the effective one of its type for this Datatype) but this should
        # be enough if both Datatypes are clean.
        if self.constraint_failed.__class__.__name__ == "BasicConstraint":
            # Note that A.is_restriction(B) is like A <= B, whereas B.is_restricted_by(A)
            # is like B > A; we want the possible equality.
            if not self.column.datatype.is_restriction(self.constraint_failed.datatype):
                raise ValidationError(
                    ('CellError "{}" refers to a BasicConstraint that does ' +
                     'not apply to the associated column').format(self))

        elif self.constraint_failed.__class__.__name__ == "CustomConstraint":
            if not self.column.datatype.is_restriction(self.constraint_failed.datatype):
                raise ValidationError(
                    ('CellError "{}" refers to a CustomConstraint that does ' +
                     'not apply to the associated column').format(self))

    def has_blank_error(self):
        try:
            self.blank
        except ObjectDoesNotExist:
            return False
        return True


class IntegrityCheckLog(stopwatch.models.Stopwatch):
    """
    Denotes an integrity check performed on a Dataset.

    One of these should be created basically every time the MD5
    checksum of the dataset is confirmed, be it during the execution
    of a Pipeline (i.e. a Run) or on a manual check.
    """
    dataset = models.ForeignKey("librarian.Dataset", related_name="integrity_checks")

    # The execution during which this check occurred, if applicable.
    execlog = models.ForeignKey(
        "archive.ExecLog",
        null=True,
        related_name="integrity_checks",
        help_text="Log of the execution where dataset was produced as an output"
    )

    runcomponent = models.OneToOneField(
        "archive.RunComponent",
        null=True,
        help_text="RunComponent responsible for copying dataset to a sandbox",
        related_name="input_integrity_check"
    )

    # The user performing the check.
    user = models.ForeignKey(User)

    # Flag indicating a failed attempt to read (or otherwise access) the file.
    read_failed = models.BooleanField(default=False)

    # Implicit through inheritance: start_time, end_time.

    def __str__(self):
        status = "OK"
        if self.is_md5_conflict():
            status = "MD5 conflict"
        elif self.read_failed:
            status = "Read failed"
        return status

    def clean(self):
        """
        Checks coherence of this IntegrityCheckLog.

        Calls clean on its child MD5Conflict, if it exists.  Checks if
        end_time is later than start_time.
        """
        if self.is_md5_conflict():
            self.usurper.clean()

        stopwatch.models.Stopwatch.clean(self)

        if not self.dataset.can_be_accessed(self.user):
            raise ValidationError('User "{}" does not have access to Dataset "{}"'.
                                  format(self.user, self.dataset))

    def is_complete(self):
        """
        Checks if this IntegrityCheckLog is finished; that is, if the end_time is set.
        """
        return self.end_time is not None

    def is_md5_conflict(self):
        """
        True if this integrity check represents an MD5 conflict.
        """
        try:
            self.usurper
        except ObjectDoesNotExist:
            return False
        return self.usurper.pk is not None

    def is_fail(self):
        """True if this integrity check is a failure."""
        return self.read_failed or self.is_md5_conflict()


class VerificationLog(stopwatch.models.Stopwatch):
    """
    A record of running a verification Method to check CustomConstraints
    on a Dataset.
    """
    UPLOAD_DIR = "VerificationLogs"

    # The log of the content check where we performed this verification.
    contentchecklog = models.ForeignKey("datachecking.ContentCheckLog", related_name="verification_logs")
    # The compound datatype member which was verified.
    CDTM = models.ForeignKey("metadata.CompoundDatatypeMember")
    # The return code from the Method's driver. Null indicates it hasn't
    # completed yet.
    return_code = models.IntegerField(null=True)
    # The verification method's standard output and standard error.
    output_log = models.FileField(upload_to=UPLOAD_DIR)
    error_log = models.FileField(upload_to=UPLOAD_DIR)

    # Implicit through inheritance: start_time, end_time.

    def clean(self):
        """
        Checks coherence of this VerificationLog.

        The start time must be before the end time.
        """
        stopwatch.models.Stopwatch.clean(self)

    def is_complete(self):
        """
        Check if this VerificationLog has completed yet (end_time and return_code
        are set).
        """
        return self.has_ended() and self.return_code is not None

    def complete_clean(self):
        """
        Checks that the verification log is coherent and the execution which it
        is logging has completed.
        """
        self.clean()
        if self.return_code is None or self.end_time is None:
            raise ValidationError('VerificationLog "{}" is not complete.'.format(self))


class MD5Conflict(models.Model):
    """
    Denotes an MD5 conflict found during an integrity check.
    """
    integritychecklog = models.OneToOneField(IntegrityCheckLog, related_name="usurper")
    conflicting_dataset = models.OneToOneField("librarian.Dataset", related_name="usurps",
                                               null=True, on_delete=models.SET_NULL)

    objects = None  # Filled in later by Django.


class BlankCell(models.Model):
    """
    Denotes a CellError that represents a cell that was blank on a non-blankable
    column.
    """
    cellerror = models.OneToOneField(CellError, related_name="blank")

    def clean(self):
        dataset = self.cellerror.baddata.contentchecklog.dataset
        if self.cellerror.column.blankable:
            raise ValidationError(
                'Entry ({},{}) of Dataset "{}" is blankable'.format(
                    self.cellerror.row_num, self.cellerror.column.column_idx, dataset
                )
            )
