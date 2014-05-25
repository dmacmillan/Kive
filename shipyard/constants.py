# Constants and odds and ends that are hard-coded into the system.

# Primary keys for Datatypes and CDTs that are pre-defined for the user.
class Datatypes:
    pass

datatypes = Datatypes()
datatypes.STR_PK = 1
datatypes.BOOL_PK = 2
datatypes.FLOAT_PK = 3
datatypes.INT_PK = 4
datatypes.NATURALNUMBER_PK = 5
datatypes.NUMERIC_BUILTIN_PKS = set([datatypes.INT_PK, datatypes.FLOAT_PK])

class CDTs:
    pass

CDTs = CDTs()
CDTs.VERIF_IN_PK = 1
CDTs.VERIF_OUT_PK = 2
CDTs.PROTOTYPE_PK = 3

class DirectoryNames:
    pass

dirnames = DirectoryNames()
dirnames.IN_DIR = "input_data"
dirnames.OUT_DIR = "output_data"
dirnames.LOG_DIR = "logs"

class Extensions:
    pass

extensions = Extensions()
extensions.CSV = "csv"
extensions.RAW = "raw"
