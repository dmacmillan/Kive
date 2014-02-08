"""
metadata.models

Shipyard data models relating to metadata: Datatypes and their related
paraphernalia, CompoundDatatypes, etc.

FIXME get all the models pointing at each other correctly!
"""

from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator

import operator
import re
import csv
import os
import traceback
from datetime import datetime

from file_access_utils import set_up_directory
from constants import CDTs, error_messages
from datachecking.models import VerificationLog

class Datatype(models.Model):
    """
    Abstract definition of a semantically atomic type of data.
    Related to :model:`copperfish.CompoundDatatype`
    """
    name = models.CharField(
        "Datatype name",
        max_length=64,
        help_text="The name for this Datatype");

    # auto_now_add: set to now on instantiation (editable=False)
    date_created = models.DateTimeField(
        'Date created',
        auto_now_add = True,
        help_text="Date Datatype was defined");

    description = models.TextField(
        "Datatype description",
        help_text="A description for this Datatype");

    # Admissible Python types.
    INT = "int"
    STR = "str"
    FLOAT = "float"
    BOOL = "bool"

    PYTHON_TYPE_CHOICES = (
        (INT, 'int'),
        (STR, 'str'),
        (FLOAT, 'float'),
        (BOOL, 'bool')
    )

    Python_type = models.CharField(
        'Python variable type',
        max_length=64,
        default = STR,
        choices=PYTHON_TYPE_CHOICES,
        help_text="Python type (int|str|float|bool)");

    restricts = models.ManyToManyField(
        'self',
        symmetrical=False,
        related_name="restricted_by",
        null=True,
        blank=True,
        help_text="Captures hierarchical is-a classifications among Datatypes");

    prototype = models.OneToOneField(
        "archive.Dataset",
        null=True,
        blank=True,
        related_name="datatype_modelled")

    def is_restricted_by(self, possible_restrictor_datatype):
        """
        Determine if this datatype is ever *properly* restricted, directly or indirectly,
        by a given datatype.
        
        PRE: there is no circular restriction in the possible restrictor
        datatype (this would cause an infinite recursion).
        """
        # The default is that self is not restricted by
        # possible_restrictor_datatype; toggle to True if it turns out
        # that it is.
        is_restricted = False
        restrictions = possible_restrictor_datatype.restricts.all()

        for restrictedDataType in restrictions:

            # Case 1: If restrictions restrict self, return true
            if restrictedDataType == self:
                is_restricted = True

            # Case 2: Check if any restricted Datatypes themselves restrict self
            else:
                theValue = self.is_restricted_by(restrictedDataType)

                # If any restricted Datatypes themselves restrict self, propagate
                # this information to the parent Datatype as restricting self
                if theValue == True:
                    is_restricted = True

        # Return False if Case 1 is never encountered
        return is_restricted

    def is_restriction(self, possible_restricted_datatype):
        """
        True if this Datatype restricts the parameter, directly or indirectly.

        This induces a partial ordering A <= B if A is a restriction of B.
        For example, a DNA sequence is a restriction of a string.
        """
        return (self == possible_restricted_datatype or
                possible_restricted_datatype.is_restricted_by(self))

    def get_effective_min_val(self):
        """
        Retrieves the 'effective' MIN_VAL restriction for this instance.

        That is, the most restrictive MIN_VAL-type BasicConstraint acting on this Datatype or its supertypes.

        PRE: all of this instance's supertypes are clean (in the Django
        sense), and this instance has at most one MIN_VAL BasicConstraint (and it is clean).
        If this instance has a MIN_VAL BasicConstraint, it exceeds the maximum of those of its
        supertypes.
        """
        # Because self is clean, we know there to be exactly one MIN_VAL.
        my_min_val = self.basic_constraints.filter(ruletype=BasicConstraint.MIN_VAL)

        effective_min = -float("inf")
        effective_BC = None

        # Base case: self has a MIN_VAL defined.  Since we know self to be clean,
        # if it has a MIN_VAL then it is larger than all its supertypes'.
        if my_min_val.exists():
            effective_min = float(my_min_val.first().rule)
            effective_BC = my_min_val.first()
        else:
            # Base case 2: self has no supertypes; in which case MIN_VAL is -\infty.

            for supertype in self.restricts.all():
                # Recursive case: self has no MIN_VAL.  Go through all of the supertypes and
                # take the maximum.
                supertype_BC = supertype.get_effective_min_val()
                supertype_min_val = float(supertype_BC.rule)
                if supertype_min_val > effective_min:
                    effective_min = supertype_min_val
                    effective_BC = supertype_BC

        return effective_BC

    def get_effective_max_val(self):
        """
        Retrieves the 'effective' MAX_VAL restriction for this instance.

        This is analogous to get_effective_min_val.

        PRE: similarly as for get_effective_min_val.
        """
        # This is all exactly just the converse of get_effective_min_val.
        my_max_val = self.basic_constraints.filter(ruletype=BasicConstraint.MAX_VAL)

        effective_max = float("inf")
        effective_BC = None

        if my_max_val.exists():
            effective_max = float(my_max_val.first().rule)
            effective_BC = my_max_val.first()
        else:
            for supertype in self.restricts.all():
                supertype_BC = supertype.get_effective_max_val()
                supertype_max_val = float(supertype_BC.rule)
                if supertype_max_val < effective_max:
                    effective_max = supertype_max_val
                    effective_BC = supertype_BC

        return effective_BC

    def get_effective_min_length(self):
        """
        Retrieves the 'effective' MIN_LENGTH restriction for this instance,
        i.e. the maximum of all of its supertypes' MIN_LENGTHs and of its own.

        PRE: as for get_effective_min_val.
        """
        # This is all completely modified from get_effective_min_val.
        my_min_length = self.basic_constraints.filter(ruletype=BasicConstraint.MIN_LENGTH)

        effective_min = 0
        effective_BC = None

        if my_min_length.exists():
            effective_min = float(my_min_length.first().rule)
            effective_BC = my_min_length.first()
        else:
            for supertype in self.restricts.all():
                supertype_BC = supertype.get_effective_min_length()
                supertype_min_length = int(supertype_BC.rule)
                if supertype_min_length > effective_min:
                    effective_min = supertype_min_length
                    effective_BC = supertype_BC

        return effective_BC

    def get_effective_max_length(self):
        """
        Retrieves the 'effective' MAX_LENGTH restriction for this instance,
        i.e. the minimum of all of its supertypes' MIN_LENGTHs and of its own.

        PRE: similarly to get_effective_min_length
        """
        # This is all completely modified from get_effective_min_length.
        my_max_length = self.basic_constraints.filter(ruletype=BasicConstraint.MAX_LENGTH)

        effective_max = float("inf")
        effective_BC = None

        if my_max_length.exists():
            effective_max = float(my_max_length.first().rule)
            effective_BC = my_max_length.first()
        else:
            for supertype in self.restricts.all():
                supertype_BC = supertype.get_effective_max_length()
                supertype_max_length = int(supertype_BC.rule)
                if supertype_max_length < effective_max:
                    effective_max = supertype_max_length
                    effective_BC = supertype_BC

        return effective_BC

    def get_all_regexps(self):
        """
        Retrieves all of the REGEXP BasicConstraints acting on this instance.

        PRE: all of this instance's supertypes are clean (in the Django
        sense), as are this instance's REGEXP BasicConstraints.
        """
        all_regexp_BCs = []

        for regexp_BC in self.basic_constraints.filter(ruletype=BasicConstraint.REGEXP):
            all_regexp_BCs.append(regexp_BC)

        for supertype in self.restricts.all():
            for regexp_BC in supertype.basic_constraints.filter(ruletype=BasicConstraint.REGEXP):
                all_regexp_BCs.append(regexp_BC)

        return all_regexp_BCs

    def get_effective_datetimeformat(self):
        """
        Retrieves the date-time format string effective for this instance.

        There can only be one such format string acting on this or its supertypes.

        PRE: this instance has at most one DATETIMEFORMAT BasicConstraint (and it is clean if it exists),
        and all its supertypes are clean (in the Django sense).
        """
        my_dtf = self.basic_constraints.filter(ruletype=BasicConstraint.DATETIMEFORMAT)
        if my_dtf.exists():
            return my_dtf.first()

        for supertype in self.restricts.all():
            curr_dtf = supertype.basic_constraints.filter(ruletype=BasicConstraint.DATETIMEFORMAT)
            if curr_dtf.exists():
                return curr_dtf.first()

        # If we reach this point, there is no effective datetimeformat constraint.
        return None


    # Clean: If prototype is specified, it must have a CDT with
    # 2 columns: column 1 is a string "example" field,
    # column 2 is a bool "valid" field.  This CDT will be hard-coded
    # and loaded into the database on creation.

    # FIXME: when we get execution working, we'll have to also
    # check that the first column of prototype yields the second
    # column of prototype after checking all constraints.

    # NOTE: we are going to assume that each Datatype has its own
    # well-defined constraints; we aren't going to check data
    # against all of its Datatype's supertypes.  But we *will* check
    # prototype against its supertypes' constraints.
    def clean(self):
        if hasattr(self, "restricts") and self.is_restricted_by(self):
            raise ValidationError(error_messages["DT_circular_restriction"].format(self))

        if self.prototype is not None:
            if self.prototype.symbolicdataset.is_raw():
                raise ValidationError(error_messages["DT_prototype_raw"].format(self))

            PROTOTYPE_CDT = CompoundDatatype.objects.get(pk=CDTs.PROTOTYPE_PK)

            if not self.prototype.symbolicdataset.get_cdt().is_identical(PROTOTYPE_CDT):
                raise ValidationError(error_messages["DT_prototype_wrong_CDT"].format(self))

        # Check that restrictions are sensible against the Python type: if x <= y means "x can restrict y",
        # INT <= FLOAT <= STR
        # BOOL <= STR
        admissible_Python_restrictions = {
            Datatype.INT: [Datatype.INT, Datatype.FLOAT, Datatype.STR],
            Datatype.FLOAT: [Datatype.FLOAT, Datatype.STR],
            Datatype.STR: [Datatype.STR],
            Datatype.BOOL: [Datatype.BOOl, Datatype.STR]
            }

        for supertype in self.restricts.all():
            if supertype.Python_type not in admissible_Python_restrictions[self.Python_type]:
                raise ValidationError(
                    error_messages["DT_bad_type_restriction"].format(self, self.Python_type, supertype.Python_type)
                )

        # Clean all BasicConstraints.
        for bc in self.basic_constraints.all():
            bc.clean()

        for constr_type in [BasicConstraint.MIN_VAL, BasicConstraint.MAX_VAL,
                            BasicConstraint.MIN_LENGTH, BasicConstraint.MAX_LENGTH,
                            BasicConstraint.DATETIMEFORMAT]:
            all_curr_type = self.basic_constraints.filter(ruletype=constr_type)
            if all_curr_type.count() > 1:
                # Check that there is at most one BasicConstraint of types (MIN|MAX)_(VAL|LENGTH)
                # or DATETIMEFORMAT directly associated to this instance.
                raise ValidationError(error_messages["DT_several_same_constraint"].format(self, constr_type))

            elif all_curr_type.count() == 1:
                if constr_type == BasicConstraint.MIN_VAL:
                    my_min_val = float(all_curr_type.first().rule)
                    supertypes_min = max(supertype.get_effective_min_val() for supertype in self.restricts.all())
                    if my_min_val <= supertypes_min:
                        raise ValidationError(error_messages["DT_min_val_smaller_than_supertypes"].format(self))

                elif constr_type == BasicConstraint.MAX_VAL:
                    my_max_val = float(all_curr_type.first().rule)
                    supertypes_max = min(supertype.get_effective_max_val() for supertype in self.restricts.all())
                    if my_max_val >= supertypes_max:
                        raise ValidationError(error_messages["DT_max_val_larger_than_supertypes"].format(self))

                elif constr_type == BasicConstraint.MIN_LENGTH:
                    my_min_length = int(all_curr_type.first().rule)
                    supertypes_min = max(supertype.get_effective_min_length() for supertype in self.restricts.all())
                    if my_min_length <= supertypes_min:
                        raise ValidationError(error_messages["DT_min_length_smaller_than_supertypes"].format(self))

                elif constr_type == BasicConstraint.MAX_LENGTH:
                    my_max_length = int(all_curr_type.first().rule)
                    supertypes_max = min(supertype.get_effective_max_length() for supertype in self.restricts.all())
                    if my_max_length >= supertypes_max:
                        raise ValidationError(error_messages["DT_max_length_larger_than_supertypes"].format(self))

        # Check that there is only one DATETIMEFORMAT between this Datatype and all of its supertypes.
        my_dtf = self.basic_constraints.filter(ruletype=BasicConstraint.DATETIMEFORMAT)
        supertype_dtfs = [supertype.basic_constraints.filter(
            ruletype=BasicConstraint.DATETIMEFORMAT).first() for supertype in self.restricts.all()]
        if my_dtf.count() + len(supertype_dtfs) > 1:
            raise ValidationError(error_messages["DT_too_many_datetimeformats"].format(self))

        # Check that effective min_length <= max_length, min_val <= max_val.
        if self.get_effective_min_val() > self.get_effective_max_val():
            raise ValidationError(error_messages["DT_min_val_exceeds_max_val"].format(self))
        if self.get_effective_min_length() > self.get_effective_max_length():
            raise ValidationError(error_messages["DT_min_length_exceeds_max_length"].format(self))

        # Clean the CustomConstraint if it exists.
        if self.has_custom_constraint():
            self.custom_constraint.clean()

    def get_absolute_url(self):
        return '/datatypes/%i' % self.id

    def __unicode__(self):
        """Describe Datatype by name"""
        return self.name

    def has_custom_constraint(self):
        """Tells whether this Datatype has a CustomConstraint."""
        return hasattr(self, "custom_constraint")

    def check_basic_constraints(self, string_to_check):
        """
        Check the specified string against basic constraints.

        This includes both whether or not the string can be 
        interpreted as the appropriate Python type, but also
        whether it then checks out against all BasicConstraints.

        If it fails against even the simplest casting test (i.e.  the
        string could not be cast to the appropriate Python type),
        return a list containing a describing string.  If not, return
        a list of BasicConstraints that it failed (hopefully it's
        empty!).

        PRE: this Datatype and by extension all of its BasicConstraints
        are clean.  That means that only the appropriate BasicConstraints
        for this Datatype's Python_type are applied.
        """
        ####
        # First, try to cast it to the appropriate Python type.
        # Then, check it against any type-specific BasicConstraints
        # (MIN|MAX_LENGTH or DATETIMEFORMAT for strings,
        # MIN|MAX_VAL for numerical types).
        constraints_failed = []
        if self.Python_type == Datatype.STR:
            # string_to_check is, by definition, a string, so we skip
            # to checking the BasicConstraints.
            eff_min_length_BC = self.get_effective_min_length()
            if eff_min_length_BC is not None and len(string_to_check) < int(eff_min_length_BC.rule):
                constraints_failed.append(eff_min_length_BC)
            else:
                eff_max_length_BC = self.get_effective_max_length()
                if eff_max_length_BC is not None and len(string_to_check) > int(eff_max_length_BC.rule):
                    constraints_failed.append(eff_max_length_BC)

            eff_dtf_BC = self.get_effective_datetimeformat()
            # Attempt to make a datetime object using this format
            # string.
            try:
                datetime.strptime(string_to_check, eff_dtf_BC.rule)
            except:
                constraints_failed.append(eff_dtf_BC)


        elif self.Python_type == Datatype.INT:
            try:
                int(string_to_check)
            except ValueError:
                return ["Was not integer"]

            # Check the numeric-type BasicConstraints.
            eff_min_val_BC = self.get_effective_min_val()
            if eff_min_val_BC is not None and int(string_to_check) < float(eff_min_val_BC.rule):
                constraints_failed.append(eff_min_val_BC)
            else:
                eff_max_val_BC = self.get_effective_max_val()
                if eff_max_val_BC is not None and int(string_to_check) > float(eff_max_val_BC.rule):
                    constraints_failed.append(eff_max_val_BC)

        elif self.Python_type == Datatype.FLOAT:
            try:
                float(string_to_check)
            except ValueError:
                return ["Was not float"]

            # Same as for the int case.
            eff_min_val_BC = self.get_effective_min_val()
            if eff_min_val_BC is not None and float(string_to_check) < float(eff_min_val_BC.rule):
                constraints_failed.append(eff_min_val_BC)
            else:
                eff_max_val_BC = self.get_effective_max_val()
                if eff_max_val_BC is not None and float(string_to_check) > float(eff_max_val_BC.rule):
                    constraints_failed.append(eff_max_val_BC)

        elif self.Python_type == Datatype.BOOL:
            bool_RE = re.compile("^(True)|(False)|(true)|(false)|(TRUE)|(FALSE)|T|F|t|f|0|1$")
            if not bool_RE.match(string_to_check):
                return ["Was not boolean"]

        ####
        # Check all REGEXP-type BasicConstraints.
        constraints_failed = []

        for re_BC in self.get_all_regexps():
            constraint_re = re.compile(re_BC.rule)
            if not constraint_re.match(string_to_check):
                constraints_failed.append(re_BC)

        return constraints_failed

    # Note that checking the CustomConstraint requires a place to run.
    # As such, it's debatable whether to put it here or as a Sandbox
    # method.  We'll probably put it here.

class BasicConstraint(models.Model):
    """
    Basic (level 1) constraint on a Datatype.

    The admissible constraints are:
     - (min|max)len (string)
     - (min|max)val (numeric)
     - (min|max)prec (float)
     - regexp (this will work on anything)
     - datetimeformat (string -- this is a special case)
    """
    datatype = models.ForeignKey(
        Datatype,
        related_name="basic_constraints")

    # Define the choices for rule type.
    MIN_LENGTH = "minlen"
    MAX_LENGTH = "maxlen"
    MIN_VAL = "minval"
    MAX_VAL = "maxval"
    # MIN_PREC = "minprec"
    # MAX_PREC = "maxprec"
    REGEXP = "regexp"
    DATETIMEFORMAT = "datetimeformat"
    
    CONSTRAINT_TYPES = (
        (MIN_LENGTH, "minimum string length"),
        (MAX_LENGTH, "maximum string length"),
        (MIN_VAL, "minimum numeric value"),
        (MAX_VAL, "maximum numeric value"),
        (REGEXP, "Perl regular expression"),
        (DATETIMEFORMAT, "date format string (1989 C standard)")
    )

    ruletype = models.CharField(
        "Type of rule",
        max_length=32,
        choices=CONSTRAINT_TYPES)

    rule = models.CharField(
        "Rule specification",
        max_length = 100)

    # TO DO: write a clean function handling the above.
    def clean(self):
        """
        Check coherence of the specified rule and rule type.

        The rule types must satisfy:
         - MIN_LENGTH: rule must be castable to a non-negative integer;
           parent DT must have Python type 'str'
         - MAX_LENGTH: rule must be castable to a positive integer;
           parent DT must have Python type 'str'
         - (MIN|MAX)_VAL: rule must be castable to a float; parent DT
           must have Python type 'float' or 'int'
         - REGEXP: rule must be a valid Perl-style RE
         - DATETIMEFORMAT: rule can be anything (note that it's up to you
           to define something *useful* here); parent DT must have Python 
           type 'str'
        """
        error_msg = ""
        is_error = False
        if self.ruletype == BasicConstraint.MIN_LENGTH:
            if self.datatype.Python_type != Datatype.STR:
                error_msg = ("Rule \"{}\" specifies a minimum string length but its parent Datatype \"{}\" is not a Python string".
                             format(self, self.datatype))
                is_error = True
            try:
                min_length = int(self.rule)
                if min_length < 0:
                    error_msg = ("Rule \"{}\" specifies a minimum string length but \"{}\" is negative".
                                 format(self, self.rule))
                    is_error = True
            except ValueError:
                error_msg = ("Rule \"{}\" specifies a minimum string length but \"{}\" does not specify an integer".
                             format(self, self.rule))
                is_error = True

        elif self.ruletype == BasicConstraint.MAX_LENGTH:
            if self.datatype.Python_type != Datatype.STR:
                error_msg = ("Rule \"{}\" specifies a maximum string length but its parent Datatype \"{}\" is not a Python string".
                             format(self, self.datatype))
                is_error = True
            try:
                max_length = int(self.rule)
                if max_length < 1:
                    error_msg = ("Rule \"{}\" specifies a maximum string length but \"{}\" is non-positive".
                                 format(self, self.rule))
                    is_error = True
            except ValueError:
                error_msg = ("Rule \"{}\" specifies a maximum string length but \"{}\" does not specify an integer".
                             format(self, self.rule))
                is_error = True

        elif self.ruletype in (BasicConstraint.MAX_VAL, 
                               BasicConstraint.MIN_VAL):
            if self.datatype.Python_type not in (Datatype.INT, Datatype.FLOAT):
                error_msg = ("Rule \"{}\" specifies a bound on a numeric value but its parent Datatype \"{}\" is not a number".
                             format(self, self.datatype))
                is_error = True
            try:
                val_bound = float(self.rule)
            except ValueError:
                error_msg = ("Rule \"{}\" specifies a bound on a numeric value but \"{}\" does not specify a numeric value".
                             format(self, self.rule))
                is_error = True
        
        elif self.ruletype == BasicConstraint.REGEXP:
            try:
                re.compile(self.rule)
            except re.error:
                error_msg = ("Rule \"{}\" specifies an invalid regular expression \"{}\"".
                             format(self, self.rule))
                is_error = True

        elif self.ruletype == BasicConstraint.DATETIMEFORMAT:
            if self.datatype.Python_type != Datatype.STR:
                error_msg = ("Rule \"{}\" specifies a date/time format but its parent Datatype \"{}\" is not a Python string".
                             format(self, self.datatype))
                is_error = True

        if is_error:
            raise ValidationError(error_msg)

class CustomConstraint(models.Model):
    """
    More complex (level 2) verification of Datatypes.

    These will be specified in the form of Methods that
    take a CSV of strings (which is the parent of all
    Datatypes) and return T/F for 
    """
    datatype = models.OneToOneField(
        Datatype,
        related_name="custom_constraint")

    verification_method = models.ForeignKey(
        "method.Method",
        related_name="custom_constraints")

    # Clean: Methods which function as CustomConstraints must take in
    # a column of strings named "to_test" and returns a column of
    # positive integers named "failed_row".  We thus need to
    # hard-code in at least two Datatypes and two CDTs (string,
    # PositiveInteger (and probably int) so that PositiveInteger can
    # restrict it), and a CDT for each).  We'll probably need more
    # later anyway.  Such CDTs will be pre-loaded into the database.
    def clean(self):
        """
        Checks coherence of this CustomConstraint.

        The method used for verification must accept as input
        a CDT looking like (string to_test); it must return
        as output a CDT looking like (bool is_valid).
        """
        # Pre-defined CDTs that the verification method must use.
        VERIF_IN = CompoundDatatype.objects.get(pk=1)
        VERIF_OUT = CompoundDatatype.objects.get(pk=2)
        
        verif_method_in = self.verification_method.inputs.all()
        verif_method_out = self.verification_method.outputs.all()
        if verif_method_in.count() != 1 or verif_method_out.count() != 1:
            raise ValidationError("CustomConstraint \"{}\" verification method does not have exactly one input and one output".
                                  format(self))

        VERIF_IN = CompoundDatatype.objects.get(pk=CDTs.VERIF_IN_PK)
        VERIF_OUT = CompoundDatatype.objects.get(pk=CDTs.VERIF_OUT_PK)

        if not verif_method_in[0].get_cdt().is_identical(VERIF_IN):
            raise ValidationError(
                "CustomConstraint \"{}\" verification method does not have an input CDT identical to VERIF_IN".
                format(self))

        if not verif_method_out[0].get_cdt().is_identical(VERIF_OUT):
            raise ValidationError(
                "CustomConstraint \"{}\" verification method does not have an output CDT identical to VERIF_OUT".
                format(self))



class CompoundDatatypeMember(models.Model):
    """
    A data type member of a particular CompoundDatatype.
    Related to :model:`archive.models.Dataset`
    Related to :model:`metadata.models.CompoundDatatype`
    """

    compounddatatype = models.ForeignKey(
        "CompoundDatatype",
        related_name="members",
        help_text="Links this DataType member to a particular CompoundDataType");

    datatype = models.ForeignKey(
        Datatype,
        help_text="Specifies which DataType this member is");

    column_name = models.CharField(
        "Column name",
        blank=False,
        max_length=128,
        help_text="Gives datatype a 'column name' as an alternative to column index");

    # MinValueValidator(1) constrains column_idx to be >= 1
    column_idx = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="The column number of this DataType");

    # Define database indexing rules to ensure tuple uniqueness
    # A compoundDataType cannot have 2 member definitions with the same column name or column number
    class Meta:
        unique_together = (("compounddatatype", "column_name"),
                           ("compounddatatype", "column_idx"));

    def __unicode__(self):
        """Describe a CompoundDatatypeMember with it's column number, datatype name, and column name"""

        returnString = u"{}: <{}> [{}]".format(self.column_idx,
                                               unicode(self.datatype),
                                               self.column_name);

        return returnString

class CompoundDatatype(models.Model):
    """
    A definition of a structured collection of datatypes,
    the resultant data structure serving as inputs or outputs
    for a Transformation.

    Related to :model:`copperfish.CompoundDatatypeMember`
    Related to :model:`copperfish.Dataset`
    """

    # Implicitly defined:
    #   members (CompoundDatatypeMember/ForeignKey)
    #   Conforming_datasets (Dataset/ForeignKey)

    def __unicode__(self):
        """ Represent CompoundDatatype with a list of it's members """

        string_rep = u"(";

        # Get the members for this compound data type
        all_members = self.members.all();

        # A) Get the column index for each member
        member_indices = [member.column_idx for member in all_members];

        # B) Get the column index of each Datatype member, along with the Datatype member itself
        members_with_indices = [ (member_indices[i], all_members[i]) for i in range(len(all_members))];
        # Can we do this?
        # members_with_indices = [ (all_members[i].column_idx, all_members[i])
        #                          for i in range(len(all_members))];

        # Sort members using column index as a basis (operator.itemgetter(0))
        members_with_indices = sorted(  members_with_indices,
                                        key=operator.itemgetter(0));

        # Add sorted Datatype members to the string representation
        for i, colIdx_and_member in enumerate(members_with_indices):
            colIdx, member = colIdx_and_member;
            string_rep += unicode(member);

            # Add comma if not at the end of member list
            if i != len(members_with_indices) - 1:
                string_rep += ", ";

        string_rep += ")";

        if string_rep == "()":
            string_rep = "[empty CompoundDatatype]";

        return string_rep;

    # clean() is executed prior to save() to perform model validation
    def clean(self):
        """Check if Datatype members have consecutive indices from 1 to n"""
        column_indices = [];

        # += is shorthand for extend() - concatenate a list with another list
        for member in self.members.all():
            member.full_clean()
            column_indices += [member.column_idx]

        # Check if the sorted list is exactly a sequence from 1 to n
        if sorted(column_indices) != range(1, self.members.count()+1):
            raise ValidationError("Column indices are not consecutive starting from 1");

    def is_restriction(self, other_CDT):
        """
        True if this CDT is a column-wise restriction of its parameter.

        This is trivially true if they are the same CDT; otherwise
        the column names must be exactly the same and each column
        of this CDT is a restriction of the corresponding column
        of the parameter CDT.

        Note that this induces a partial order on CDTs.

        PRE: this CDT and other_CDT are clean.
        """
        if self == other_CDT:
            return True
        
        # Make sure they have the same number of columns.
        if self.members.count() != other_CDT.members.count():
            return False

        # Since they have the same number of columns at this point,
        # and we have enforced that the numbering of members is
        # consecutive starting from one, we can go through all of this
        # CDT's members and look for the matching one.
        for member in self.members.all():
            counterpart = other_CDT.members.get(
                column_idx=member.column_idx)
            if (member.column_name != counterpart.column_name or
                    not member.datatype.is_restriction(
                        counterpart.datatype)):
                return False
        
        # Having reached this point, this CDT must be a restriction
        # of other_CDT.
        return True

    def is_identical(self, other_CDT):
        """
        True if this CDT is identical with its parameter; False otherwise.
        
        This is trivially true if they are the same CDT; otherwise
        the column names and column types must be exactly the same.

        PRE: this CDT and other_CDT are clean.
        """
        return (self.is_restriction(other_CDT) and
                other_CDT.is_restriction(self))

    
    def summarize_CSV(self, file_to_check, summary_path, content_check_log=None):
        """
        Give metadata on the CSV: number of rows, and any deviations
        from the CDT (defects).

        file_to_check: open file object set to the beginning.

        content_check_log: if summarize_CSV is called as part of a content check
        on a SymbolicDataset, this is the log of that check. If this is unset, we
        are supposed to create a 

        OUTPUT: a dict containing metadata about the CSV

        - bad_num_cols: set if header has wrong number of columns;
          if so, returns number of columns in the header.
    
        - bad_col_indices: set if header has improperly named columns;
          if so, returns list of indices of bad columns
    
        - num_rows: number of rows
        
        - failing_cells: dict of non-conforming cells in the file.
          Entries keyed by (rownum, colnum) contain list of tests failed.
        """
        import inspect, logging
        fn = "{}.{}()".format(self.__class__.__name__, inspect.stack()[0][3])
        summary = {}
        
        # A CSV reader which we will use to check individual 
        # cells in the file, as well as creating external CSVs
        # for columns whose DT has a CustomConstraint.
        data_csv = csv.DictReader(file_to_check)
        if data_csv.fieldnames is None:
          logging.debug("{}: file is empty")
          return summary
    
        # Counter for the number of rows.
        num_rows = 0

        ####
        # CHECK HEADER
        header = data_csv.fieldnames
        summary["header"] = header
        cdt_members = self.members.all()
        if len(header) != cdt_members.count():
            summary["bad_num_cols"] = len(header)
            logging.debug("{}: number of CSV columns must match number of CDT members")
            return summary
    
        # The ith cdt member must have the same name as the ith CSV header.
        bad_col_indices = []
        for cdtm in cdt_members:
            if cdtm.column_name != header[cdtm.column_idx-1]:
                bad_col_indices.append(cdtm.column_idx)
                logging.debug("{}: Incorrect header for column {}".format(fn, cdtm.column_idx))

        if len(bad_col_indices) != 0:
            summary["bad_col_indices"] = bad_col_indices
            return summary
        # FINISH CHECKING HEADER
        ####



        ####
        # CHECK CONSTRAINTS
    
        # A dict of failing entries.
        failing_cells = {}
    
        # Check if any columns have CustomConstraints.  We will use this
        # lookup table while we're reading through the CSV file to see
        # which columns need to be copied out for checking against
        # CustomConstraints.
    
        try:
            # Keyed by column index, maps to (path to file, file handle)
            cols_with_cc = {}
            for cdtm in cdt_members:
                if cdtm.datatype.has_custom_constraint():
                    # This column is going to require running a verification
                    # method, so we set up a place within summary_path to do
                    # so.
                    column_test_path = os.path.join(
                        summary_path, "col{}".format(cdtm.column_idx))
    
                    # Set up the paths
                    # [testing path]/col[colnum]/
                    # [testing path]/col[colnum]/input_data/
                    # [testing path]/col[colnum]/output_data/
                    # [testing path]/col[colnum]/logs/
                    
                    # We will use the first to actually run the script;
                    # the input file will go into the second; the output
                    # will go into the third; output and error logs go
                    # into the fourth.

                    input_data = os.path.join(column_test_path, "input_data")
                    set_up_directory(input_data)
                    output_data = os.path.join(column_test_path, "output_data")
                    set_up_directory(output_data)
                    logs = os.path.join(column_test_path, "logs")
                    for workdir in [input_data, output_data, logs]:
                        set_up_directory(workdir)
    
                    input_file_path = os.path.join(column_test_path,
                                                   "input_data",
                                                   "to_test.csv")
                    
                    cols_with_cc[cdtm.column_idx] = {
                        "testpath": column_test_path,
                        "infilepath": input_file_path,
                        "infilehandle": open(os.path.join(input_file_path), "wb")
                    }
    
                    # Write a CSV header.
                    cols_with_cc[cdtm.column_idx]["infilehandle"].write("to_test\n")
    
    
            ####
            # CHECK BASIC CONSTRAINTS AND COUNT ROWS
                    
            # Now we can actually check the data.
            for i, row in enumerate(data_csv):
                # Note that i is 0-based, but our rows should be 1-based.
                rownum = i + 1
    
                # Increment the row count.
                num_rows += 1
                
                for cdtm in cdt_members:
                    curr_cell_value = row[cdtm.column_name]
                    test_result = cdtm.datatype.check_basic_constraints(
                        curr_cell_value)
                    
                    if len(test_result) != 0:
                        failing_cells[(rownum, cdtm.column_idx)] = test_result
    
                    if cdtm.column_idx in cols_with_cc:
                        cols_with_cc[cdtm.column_idx]["infilehandle"].write(
                            curr_cell_value + "\n")
    
            summary["num_rows"] = num_rows
    
            # FINISHED CHECKING BASIC CONSTRAINTS AND COUNTING ROWS
            ####
    
        finally:
            for col in cols_with_cc:
                cols_with_cc[col]["infilehandle"].close()
    
        ####
        # CHECK CUSTOM CONSTRAINTS
        
        # Now: any column that had a CustomConstraint must be checked 
        # using the specified verification method.
        for col in cols_with_cc:
            # We need to invoke the verification method using run_code.
            # All of our inputs are in place.
            corresp_DTM = cdt_members.get(column_idx=col)
            corresp_DT = corresp_DTM.datatype
            verif_method = corresp_DT.custom_constraint.verification_method
    
            input_path = cols_with_cc[col]["infilepath"]
            dir_to_run = cols_with_cc[col]["testpath"]
            output_path = os.path.join(dir_to_run, "output_data", "is_valid.csv")
    
            stdout_path = os.path.join(dir_to_run, "logs", "stdout.txt")
            stderr_path = os.path.join(dir_to_run, "logs", "stderr.txt")

            # TODO: There is still a bit of duplication here, namely in filling
            # out the log. Perhaps put it into run_code_with_streams.
            with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
                if content_check_log is not None:
                    verif_log = VerificationLog(contentchecklog=content_check_log,
                            CDTM = corresp_DTM)
                    verif_log.save()
                return_code = verif_method.run_code_with_streams(dir_to_run, 
                        [input_path], [output_path], 
                        [out, sys.stdout], [err, sys.stderr])
                if content_check_log is not None:
                    verif_log.end_time = timezone.now()
                    verif_log.return_code = return_code
                    verif_log.output_log.save(stdout_path, File(out))
                    verif_log.error_log.save(stderr_path, File(err))
                    verif_log.clean()

            # Now: open the resulting file, which is at output_path, and
            # make sure it's OK.  We're going to have to call
            # summarize_CSV on this resulting file, but that's OK because
            # it must have a CDT (NaturalNumber failed_row), and we
            # will define NaturalNumber to have no CustomConstraint, so
            # that no deeper recursion will happen.
            output_summary = None
            VERIF_OUT = CompoundDatatype.objects.get(pk=2)
            with open(output_path, "rb") as test_out:
                output_summary = summarize_CSV(
                    test_out, VERIF_OUT,
                    os.path.join(summary_path, "SHOULDNEVERBEWRITTENTO"))
    
            if output_summary.has_key("bad_num_cols"):
                raise ValueError(
                    "Output of verification method for Datatype \"{}\" had the wrong number of columns".
                    format(corresp_DT))
    
            if output_summary.has_key("bad_col_indices"):
                raise ValueError(
                    "Output of verification method for Datatype \"{}\" had a malformed header".
                    format(corresp_DT))
    
            if output_summary.has_key("failing_cells"):
                raise ValueError(
                    "Output of verification method for Datatype \"{}\" had malformed entries".
                    format(corresp_DT))
    
            # This should really never happen.
            if os.path.exists(os.path.join(
                    summary_path, "SHOULDNEVERBEWRITTENTO")):
                raise ValueError(
                    "Verification output CDT \"{}\" has been corrupted".
                    format(VERIF_OUT))
    
            # Collect the row numbers of incorrect entries in this column.
            with open(output_path, "rb") as test_out:
                test_out_csv = csv.DictReader(test_out)
                for row in test_out_csv:
                    if (row["rownum"], col) in failing_cells:
                        failing_cells[(row["rownum"], col)].append(
                            corresp_DT.custom_constraint)
                    else:
                        failing_cells[(row["rownum"], col)] = [
                            corresp_DT.custom_constraint
                        ]
    
        # FINISHED CHECKING CUSTOM CONSTRAINTS
        ####
    
        # If there are any failing cells, then add the dict to summary.
        if len(failing_cells) != 0:
            summary["failing_cells"] = failing_cells
    
        return summary

    def count_conforming_datasets (self):
        """
        Returns the number of Datasets that conform to this CompoundDatatype.
        Is this even possible?
        """
        return 0

    num_conforming_datasets = property(count_conforming_datasets)