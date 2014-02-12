# Constants and odds and ends that are hard-coded into the system.

error_messages = {
    "header_mismatch": 
        'File "{}" should have the header "{}", but it has "{}"',
    "empty_file": 'File "{}" is empty.',
    "driver_metapackage": 
        'Method "{}" cannot have CodeResourceRevision "{}" as a driver, because it has no content file.',
    "transf_noinput": 'Transformation "{}" has no inputs.',
    "method_bad_inputcount": 
        'Method "{}" expects {} inputs and {} outputs, but {} inputs and {} outputs were supplied',
    "pipeline_bad_inputcount":
        'Pipeline "{}" expects {} inputs, but {} were supplied',
    "pipeline_expected_raw":
        'Pipeline "{}" expected input {} to be raw, but got one with compound datatype "{}"',
    "pipeline_expected_nonraw":
        'Pipeline "{}" expected input {} to be of compound datatype "{}", but got raw',
    "pipeline_cdt_mismatch":
        'Pipeline "{}" expected input {} to be of compound datatype "{}", but got one with compound datatype "{}"',
    "pipeline_bad_numrows":
        'Pipeline "{}" expected input {} to have between {} and {} rows, but got a one with {}',
    "dataset_bad_type":
        'Expected source to be either a Dataset or a string, got {}',
    "execlog_swapped_times":
        'The end time of ExecLog "{}" is before its start time.',
    "ccl_swapped_times":
        'The end time of ContentCheckLog "{}" is before its start time.',
    "SD_not_in_pipeline":
        'SymbolicDataset "{}" was not found in Pipeline "{}" and cannot be recovered',
    "SD_pipeline_input":
        'SymbolicDataset "{}" is an input to Pipeline "{}" and cannot be recovered',
    "bad_constraint_checker":
        'Constraint checking method "{}" crashed',
    "ER_cable_wiring_DT_mismatch":
        'ExecRecord \"{}\" represents a cable but Datatype of destination Dataset column {} does not match its source',
    "DT_circular_restriction":
        "Datatype \"{}\" has a circular restriction",
    "DT_prototype_raw":
        "Prototype Dataset for Datatype \"{}\" is raw",
    "DT_prototype_wrong_CDT":
        "Prototype Dataset for Datatype \"{}\" should have CDT identical to PROTOTYPE",
    "DT_several_same_constraint":
        "Datatype \"{}\" has more than one constraint of type \"{}\"",
    "DT_min_val_smaller_than_supertypes":
        "Datatype \"{}\" MIN_VAL does not strictly exceed the maximum of its supertypes'",
    "DT_max_val_larger_than_supertypes":
        "Datatype \"{}\" MAX_VAL is not strictly smaller than the minimum of its supertypes'",
    "DT_min_length_smaller_than_supertypes":
        "Datatype \"{}\" MIN_LENGTH does not strictly exceed the maximum of its supertypes'",
    "DT_max_length_larger_than_supertypes":
        "Datatype \"{}\" MAX_LENGTH is not strictly smaller than the minimum of its supertypes'",
    "DT_too_many_datetimeformats":
        "Datatype \"{}\" has too many DATETIMEFORMAT restrictions acting on it",
    "DT_min_val_exceeds_max_val":
        "Datatype \"{}\" effective MIN_VAL exceeds effective MAX_VAL",
    "DT_min_length_exceeds_max_length":
        "Datatype \"{}\" effective MIN_LENGTH exceeds effective MAX_LENGTH",
    "DT_bad_type_restriction":
        "Datatype \"{}\" has Python type {} and cannot restrict a supertype of Python type {}",
    "CellError_bad_BC":
        "CellError \"{}\" refers to a BasicConstraint that does not apply to the associated column",
    "CellError_bad_CC":
        "CellError \"{}\" refers to a CustomConstraint that does not apply to the associated column",
    "BC_DT_not_complete":
        "Parent Datatype \"{}\" of BasicConstraint \"{}\" is not complete",
    "BC_min_length_on_non_string":
        ("BasicConstraint \"{}\" specifies a minimum string length but its parent Datatype \"{}\" " +
         "is numeric or Boolean"),
    "BC_min_length_negative":
        "BasicConstraint \"{}\" specifies a minimum string length but \"{}\" is negative",
    "BC_min_length_non_integer":
        "BasicConstraint \"{}\" specifies a minimum string length but \"{}\" does not specify an integer",
    "BC_max_length_on_non_string":
        ("BasicConstraint \"{}\" specifies a maximum string length but its parent Datatype \"{}\" " +
         "is numeric or Boolean"),
    "BC_max_length_non_positive":
        "BasicConstraint \"{}\" specifies a maximum string length but \"{}\" is non-positive",
    "BC_max_length_non_integer":
        "BasicConstraint \"{}\" specifies a maximum string length but \"{}\" does not specify an integer",
    "BC_val_constraint_parent_non_numeric":
        "BasicConstraint \"{}\" specifies a bound on a numeric value but its parent Datatype \"{}\" is not a number",
    "BC_val_constraint_rule_non_numeric":
        "BasicConstraint \"{}\" specifies a bound on a numeric value but \"{}\" does not specify a numeric value",
    "BC_bad_RE":
        "BasicConstraint \"{}\" specifies an invalid regular expression \"{}\"",
    "BC_datetimeformat_non_string":
        "BasicConstraint \"{}\" specifies a date/time format but its parent Datatype \"{}\" is numeric or Boolean",
    "DT_does_not_restrict_atomic":
        "Datatype \"{}\" does not restrict any of Shipyard atomics STR, INT, FLOAT, or BOOL"
}

warning_messages = {
    "pipeline_already_run": 
        "A pipeline has already been run in Sandbox {}, returning the previous Run"
}


# Primary keys for Datatypes and CDTs that are pre-defined for the user.
class Datatypes:
    pass

datatypes = Datatypes()
datatypes.STR_PK = 1
datatypes.BOOL_PK = 2
datatypes.FLOAT_PK = 3
datatypes.INT_PK = 4
datatypes.NATURALNUMBER_PK = 5

class CDTs:
    pass

CDTs = CDTs()
CDTs.VERIF_IN_PK = 1
CDTs.VERIF_OUT_PK = 2
CDTs.PROTOTYPE_PK = 3
