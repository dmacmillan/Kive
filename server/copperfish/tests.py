"""
Unit tests for Shipyard (Copperfish)
"""

from django.test import TestCase;
from copperfish.models import *;
from django.core.files import File;
from django.core.exceptions import ValidationError;

from tests_old import *;

class CopperfishExecRecordTests_setup(TestCase):

    def setUp(self):
        """
        Setup scenario to test ExecRecords.

        Method A     Inputs: Raw-A1_rawin
                     Outputs: Doublet-A1_out

        Method B     Inputs: Doublet-B1_in, Singlet-B2_in
                     Outputs: Triplet-B1_out

        Method C     Inputs: Triplet-C1_in, Doublet-C2_in
                     OutputS: Singlet-C1_out, Raw-C2_rawout, Raw-C3_rawout

        Pipeline D   Inputs: Doublet-D1_in, Singlet-D2_in
                     Outputs: Triplet-D1_out (< 5 rows)
                     Sequence: Method D

        Pipeline E   Inputs: Triplet-E1_in, Singlet-E2_in, Raw-E3_rawin
                     Outputs: Triplet-E1_out, Singlet-E2_out, Raw-E3_rawout
                     Sequence: Method A, Pipeline D, Method C
        """

        # Datatypes and CDTs
        with open(os.path.join(samplecode_path, "stringUT.py"), "rb") as f:
            self.string_dt = Datatype(name="string",description="string desc",verification_script=File(f),Python_type="str");
            self.string_dt.save()
        self.singlet_cdt = CompoundDatatype()
        self.singlet_cdt.save()
        self.singlet_cdt.members.create(datatype=self.string_dt,column_name="k",column_idx=1)
        self.doublet_cdt = CompoundDatatype()
        self.doublet_cdt.save()
        self.doublet_cdt.members.create(datatype=self.string_dt,column_name="x",column_idx=1)
        self.doublet_cdt.members.create(datatype=self.string_dt,column_name="y",column_idx=2)
        self.triplet_cdt = CompoundDatatype()
        self.triplet_cdt.save()
        self.triplet_cdt.members.create(datatype=self.string_dt,column_name="a",column_idx=1)
        self.triplet_cdt.members.create(datatype=self.string_dt,column_name="b",column_idx=2)
        self.triplet_cdt.members.create(datatype=self.string_dt,column_name="c",column_idx=3)

        # CRs and CRRs
        self.generic_cr = CodeResource(name="genericCR",description="Just a CR",filename="complement.py")
        self.generic_cr.save()
        with open(os.path.join(samplecode_path, "generic_script.py"), "rb") as f:
            self.generic_crRev = CodeResourceRevision(coderesource=self.generic_cr,revision_name="v1",revision_desc="desc",content_file=File(f))
            self.generic_crRev.save()

        # Method family, methods, and their input/outputs
        self.mf = MethodFamily(name="method_family",description="Holds methods A/B/C"); self.mf.save()
        self.mA = Method(revision_name="mA_name",revision_desc="A_desc",family = self.mf,driver = self.generic_crRev); self.mA.save()
        self.A1_rawin = self.mA.create_input(dataset_name="A1_rawin", dataset_idx=1)
        self.A1_out = self.mA.create_output(compounddatatype=self.doublet_cdt,dataset_name="A1_out",dataset_idx=1)

        self.mB = Method(revision_name="mB_name",revision_desc="B_desc",family = self.mf,driver = self.generic_crRev); self.mB.save()
        self.B1_in = self.mB.create_input(compounddatatype=self.doublet_cdt,dataset_name="B1_in",dataset_idx=1)
        self.B2_in = self.mB.create_input(compounddatatype=self.singlet_cdt,dataset_name="B2_in",dataset_idx=2)
        self.B1_out = self.mB.create_output(compounddatatype=self.triplet_cdt,dataset_name="B1_out",dataset_idx=1,max_row=5)

        self.mC = Method(revision_name="mC_name",revision_desc="C_desc",family = self.mf,driver = self.generic_crRev); self.mC.save()
        self.C1_in = self.mC.create_input(compounddatatype=self.triplet_cdt,dataset_name="C1_in",dataset_idx=1)
        self.C2_in = self.mC.create_input(compounddatatype=self.doublet_cdt,dataset_name="C2_in",dataset_idx=2)
        self.C1_out = self.mC.create_output(compounddatatype=self.singlet_cdt,dataset_name="C1_out",dataset_idx=1)
        self.C2_rawout = self.mC.create_output(dataset_name="C2_rawout",dataset_idx=2)
        self.C3_rawout = self.mC.create_output(dataset_name="C3_rawout",dataset_idx=3)

        # Pipeline family, pipelines, and their input/outputs
        self.pf = PipelineFamily(name="Pipeline_family", description="PF desc"); self.pf.save()
        self.pD = Pipeline(family=self.pf, revision_name="pD_name",revision_desc="D"); self.pD.save()
        self.D1_in = self.pD.create_input(compounddatatype=self.doublet_cdt,dataset_name="D1_in",dataset_idx=1)
        self.D2_in = self.pD.create_input(compounddatatype=self.singlet_cdt,dataset_name="D2_in",dataset_idx=2)
        self.pE = Pipeline(family=self.pf, revision_name="pE_name",revision_desc="E"); self.pE.save()
        self.E1_in = self.pE.create_input(compounddatatype=self.triplet_cdt,dataset_name="E1_in",dataset_idx=1)
        self.E2_in = self.pE.create_input(compounddatatype=self.singlet_cdt,dataset_name="E2_in",dataset_idx=2,min_row=10)
        self.E3_rawin = self.pE.create_input(dataset_name="E3_rawin",dataset_idx=3)

        # Pipeline steps
        self.step_D1 = self.pD.steps.create(transformation=self.mB,step_num=1)
        self.step_E1 = self.pE.steps.create(transformation=self.mA,step_num=1)
        self.step_E2 = self.pE.steps.create(transformation=self.pD,step_num=2)
        self.step_E3 = self.pE.steps.create(transformation=self.mC,step_num=3)

        # Pipeline cables and outcables
        self.D01_11 = self.step_D1.cables_in.create(transf_input=self.B1_in,step_providing_input=0,provider_output=self.D1_in)
        self.D02_12 = self.step_D1.cables_in.create(transf_input=self.B2_in,step_providing_input=0,provider_output=self.D2_in)
        self.D11_21 = self.pD.outcables.create(output_name="D1_out",output_idx=1,output_cdt=self.triplet_cdt,step_providing_output=1,provider_output=self.B1_out)
        self.pD.create_outputs()

        self.E03_11 = self.step_E1.cables_in.create(transf_input=self.A1_rawin,step_providing_input=0,provider_output=self.E3_rawin)
        self.E01_21 = self.step_E2.cables_in.create(transf_input=self.D1_in,step_providing_input=0,provider_output=self.E1_in)
        self.E01_22 = self.step_E2.cables_in.create(transf_input=self.D2_in,step_providing_input=0,provider_output=self.E2_in)
        self.E11_32 = self.step_E3.cables_in.create(transf_input=self.C2_in,step_providing_input=1,provider_output=self.A1_out)
        self.E21_31 = self.step_E3.cables_in.create(transf_input=self.C1_in,step_providing_input=2,provider_output=self.step_E2.transformation.outputs.get(dataset_name="D1_out"))
        self.E21_41 = self.pE.outcables.create(output_name="E1_out",output_idx=1,output_cdt=self.doublet_cdt,step_providing_output=2,provider_output=self.step_E2.transformation.outputs.get(dataset_name="D1_out"))
        self.E31_42 = self.pE.outcables.create(output_name="E2_out",output_idx=2,output_cdt=self.singlet_cdt,step_providing_output=3,provider_output=self.C1_out)
        self.E33_43 = self.pE.outcables.create(output_name="E3_rawout",output_idx=3,output_cdt=None,step_providing_output=3,provider_output=self.C3_rawout)
        self.pE.create_outputs()

        # Custom wiring/outwiring
        self.E01_21_wire1 = self.E01_21.custom_wires.create(source_pin=self.triplet_cdt.members.all()[0],dest_pin=self.doublet_cdt.members.all()[1])
        self.E01_21_wire2 = self.E01_21.custom_wires.create(source_pin=self.triplet_cdt.members.all()[2],dest_pin=self.doublet_cdt.members.all()[0])
        self.E11_32_wire1 = self.E11_32.custom_wires.create(source_pin=self.doublet_cdt.members.all()[0],dest_pin=self.doublet_cdt.members.all()[1])
        self.E11_32_wire2 = self.E11_32.custom_wires.create(source_pin=self.doublet_cdt.members.all()[1],dest_pin=self.doublet_cdt.members.all()[0])
        self.E21_41_wire1 = self.E21_41.custom_outwires.create(source_pin=self.triplet_cdt.members.all()[1],dest_pin=self.doublet_cdt.members.all()[0])
        self.E21_41_wire2 = self.E21_41.custom_outwires.create(source_pin=self.triplet_cdt.members.all()[2],dest_pin=self.doublet_cdt.members.all()[1])
        self.pE.clean()

        # Define a user
        self.myUser = User.objects.create_user('john', 'lennon@thebeatles.com', 'johnpassword')
        self.myUser.save()

        # Define uploaded datasets
        self.triplet_symDS = SymbolicDataset()
        self.triplet_symDS.save()
        self.triplet_DS = None
        with open(os.path.join(samplecode_path, "step_0_triplet.csv"), "rb") as f:
            self.triplet_DS = Dataset(user=self.myUser,name="triplet",description="lol",dataset_file=File(f),symbolicdataset=self.triplet_symDS)
            self.triplet_DS.save()
        self.triplet_DS_structure = DatasetStructure(dataset=self.triplet_DS,compounddatatype=self.triplet_cdt)
        self.triplet_DS_structure.save()
        self.triplet_DS.clean()

        self.singlet_symDS = SymbolicDataset()
        self.singlet_symDS.save()
        self.singlet_DS = None
        with open(os.path.join(samplecode_path, "step_0_singlet.csv"), "rb") as f:
            self.singlet_DS = Dataset(user=self.myUser,name="singlet",description="lol",dataset_file=File(f),symbolicdataset=self.singlet_symDS)
            self.singlet_DS.save()
        self.singlet_DS_structure = DatasetStructure(dataset=self.singlet_DS,compounddatatype=self.singlet_cdt)
        self.singlet_DS_structure.save()
        self.singlet_DS.clean()

        self.raw_symDS = SymbolicDataset()
        self.raw_symDS.save()
        self.raw_DS = None
        with open(os.path.join(samplecode_path, "step_0_raw.fasta"), "rb") as f:
            self.raw_DS = Dataset(user=self.myUser,name="raw",description="lol",dataset_file=File(f),symbolicdataset=self.raw_symDS)
            self.raw_DS.save()
        self.raw_DS.clean()

        self.triplet_3_rows_symDS = SymbolicDataset()
        self.triplet_3_rows_symDS.save()
        self.triplet_3_rows_DS = None
        with open(os.path.join(samplecode_path, "step_0_triplet_3_rows.csv"), "rb") as f:
            self.triplet_3_rows_DS = Dataset(user=self.myUser,name="triplet",description="lol",dataset_file=File(f),symbolicdataset=self.triplet_3_rows_symDS)
            self.triplet_3_rows_DS.save()
        self.triplet_3_rows_DS_structure = DatasetStructure(dataset=self.triplet_3_rows_DS,compounddatatype=self.triplet_cdt)
        self.triplet_3_rows_DS_structure.save()
        self.triplet_3_rows_DS.clean()

    def tearDown(self):
        """ Clear CodeResources, Datasets, and VerificationScripts folders"""

        for crr in CodeResourceRevision.objects.all():
            if crr.coderesource.filename != "":
                crr.content_file.delete()
                
        for ds in Datatype.objects.all():
            ds.verification_script.delete()

        for dataset in Dataset.objects.all():
            dataset.dataset_file.delete()

class CopperfishExecRecordTests(CopperfishExecRecordTests_setup):

    def test_ER_links_POC_so_ERI_must_link_TO_that_POC_gets_output_from(self):
        # ER links POC: ERI must link to the TO that the POC gets output from
        myER = self.E21_41.execrecords.create(tainted=False)

        myERI_bad = myER.execrecordins.create(symbolicdataset=self.singlet_symDS,generic_input=self.C1_out)
        self.assertRaisesRegexp(ValidationError,"ExecRecordIn \".*\" does not denote the TO that feeds the parent ExecRecord POC",myERI_bad.clean)

    def test_ER_doesnt_link_POC_so_ERI_musnt_link_TO(self):
        # ER doesn't link POC (So, method/pipeline): ERI must not link a TO (which would imply ER should link POC)
        myER = self.mA.execrecords.create(tainted=False)
        myERI_bad = myER.execrecordins.create(symbolicdataset=self.singlet_symDS,generic_input=self.C1_out)
        self.assertRaisesRegexp(ValidationError,"ExecRecordIn \".*\" denotes a PipelineOutputCable but parent ExecRecord does not",myERI_bad.clean)

    def test_ERI_linking_TI_must_be_member_of_pipeline_linked_by_ER(self):
        # ERI links TI: TI must be a member of the ER's pipeline
        myER = self.pE.execrecords.create(tainted=False)
        myERI_good = myER.execrecordins.create(symbolicdataset=self.triplet_symDS,generic_input=self.pE.inputs.get(dataset_name="E1_in"))
        self.assertEqual(myERI_good.clean(), None)
        
        myERI_bad = myER.execrecordins.create(symbolicdataset=self.singlet_symDS,generic_input=self.pD.inputs.get(dataset_name="D2_in"))
        self.assertRaisesRegexp(ValidationError,"Input \".*\" does not belong to Pipeline of ExecRecord \".*\"",myERI_bad.clean)

    def test_ER_links_pipelinemethod_so_ERI_must_link_cable_with_destination_TI_belonging_to_transformation(self):
        # ERI links PSIC (so input feeds a pipeline step) - destination TI of cable must belong to TI of that transformation
        myER = self.pD.execrecords.create(tainted=False)
        myERI_good = myER.execrecordins.create(symbolicdataset=self.triplet_symDS,generic_input=self.E01_21)
        self.assertEqual(myERI_good.clean(), None)
        
        myERI_bad = myER.execrecordins.create(symbolicdataset=self.triplet_symDS,generic_input=self.E21_31)
        self.assertRaisesRegexp(ValidationError,"Cable \".*\" does not feed Method/Pipeline of ExecRecord \".*\"",myERI_bad.clean)

    def test_ERI_dataset_must_match_rawunraw_state_of_generic_input_it_was_fed_into(self):
        # ERI has a dataset: it's raw/unraw state must match the raw/unraw state of the generic_input it was fed into

        myER_C = self.mC.execrecords.create(tainted=False)

        myERI_unraw_unraw = myER_C.execrecordins.create(symbolicdataset=self.triplet_symDS,generic_input=self.E21_31)
        self.assertEqual(myERI_unraw_unraw.clean(), None)

        myERI_raw_unraw_BAD = myER_C.execrecordins.create(symbolicdataset=self.raw_symDS,generic_input=self.E11_32)
        self.assertRaisesRegexp(ValidationError,"Dataset \".*\" cannot feed source \".*\"",myERI_raw_unraw_BAD.clean)

        myER_A = self.mA.execrecords.create(tainted=False)
        myERI_unraw_raw_BAD = myER_A.execrecordins.create(symbolicdataset=self.triplet_symDS,generic_input=self.E03_11)
        self.assertRaisesRegexp(ValidationError,"Dataset \".*\" cannot feed source \".*\"",myERI_unraw_raw_BAD.clean)
        myERI_unraw_raw_BAD.delete()
    
        myERI_raw_raw = myER_A.execrecordins.create(symbolicdataset=self.raw_symDS,generic_input=self.E03_11)
        myERI_raw_raw.clean()

    def test_ER_links_POC_ERI_links_TO_which_constrains_input_dataset_CDT(self):
        # ERI links with a TO (For a POC leading from source TO), the input dataset CDT is constrained by the source TO
        myER = self.E21_41.execrecords.create(tainted=False)

        # We annotate that triplet was fed into D1_out which was connected by E21_41
        myERI_wrong_CDT = myER.execrecordins.create(symbolicdataset=self.singlet_symDS,generic_input=self.pD.outputs.get(dataset_name="D1_out"))
        self.assertRaisesRegexp(ValidationError,"Dataset \".*\" is not of the expected CDT",myERI_wrong_CDT.clean)
        myERI_wrong_CDT.delete()

        # Right CDT but wrong number of rows (It needs < 5, we have 10)
        myERI_too_many_rows = myER.execrecordins.create(symbolicdataset=self.triplet_symDS,generic_input=self.pD.outputs.get(dataset_name="D1_out"))
        self.assertRaisesRegexp(ValidationError,"Dataset \".*\" has too many rows to have come from TransformationOutput \".*\"",myERI_too_many_rows.clean)

    def test_ER_links_pipeline_ERI_links_TI_which_constrains_input_dataset_CDT(self):
        # ERI links with a TI (for pipeline inputs) - the dataset is constrained by the pipeline TI CDT

        myER = self.pE.execrecords.create(tainted=False)
        myERI_wrong_CDT = myER.execrecordins.create(symbolicdataset=self.singlet_symDS,generic_input=self.E1_in)
        self.assertRaisesRegexp(ValidationError,"Dataset \".*\" is not of the expected CDT",myERI_wrong_CDT.clean)
        myERI_wrong_CDT.delete()

        myERI_too_few_rows = myER.execrecordins.create(symbolicdataset=self.singlet_symDS,generic_input=self.E2_in)
        self.assertRaisesRegexp(ValidationError,"Dataset \".*\" has too few rows for TransformationInput \".*\"",myERI_too_few_rows.clean)

        # Define dataset of correct CDT (singlet) with > 10 rows
        self.triplet_large_symDS = SymbolicDataset()
        self.triplet_large_symDS.save()
        self.triplet_large_DS = None
        with open(os.path.join(samplecode_path, "triplet_cdt_large.csv"), "rb") as f:
            self.triplet_large_DS = Dataset(user=self.myUser,name="triplet",description="lol",dataset_file=File(f),symbolicdataset=self.triplet_large_symDS)
            self.triplet_large_DS.save()
        self.triplet_large_DS_structure = DatasetStructure(dataset=self.triplet_large_DS,compounddatatype=self.triplet_cdt)
        self.triplet_large_DS_structure.save()
        self.triplet_large_DS.clean()

        myERI_right_CDT = myER.execrecordins.create(symbolicdataset=self.triplet_large_symDS,generic_input=self.E1_in)
        self.assertEqual(myERI_right_CDT.clean(), None)

    def test_ER_links_pipelinestep_ERI_links_PSIC_provider_output_constrains_input_CDT(self):
        # The provider_output of a cable (PSIC) constrains the dataset of when the ER links with a method
        
        myER = self.mC.execrecords.create(tainted=False)
        myERI_wrong_CDT = myER.execrecordins.create(symbolicdataset=self.singlet_symDS,generic_input=self.E11_32)
        self.assertRaisesRegexp(ValidationError,"Dataset \".*\" is not of the expected CDT",myERI_wrong_CDT.clean)
        myERI_wrong_CDT.delete()

        # Define dataset with correct CDT (doublet)
        self.doublet_symDS = SymbolicDataset()
        self.doublet_symDS.save()
        self.doublet_DS = None
        with open(os.path.join(samplecode_path, "doublet_cdt.csv"), "rb") as f:
            self.doublet_DS = Dataset(user=self.myUser,name="doublet",description="lol",dataset_file=File(f),symbolicdataset=self.doublet_symDS)
            self.doublet_DS.save()
        self.doublet_DS_structure = DatasetStructure(dataset=self.doublet_DS,compounddatatype=self.doublet_cdt)
        self.doublet_DS_structure.save()
        self.doublet_DS.clean()

        myERI_right_CDT = myER.execrecordins.create(symbolicdataset=self.doublet_symDS,generic_input=self.E11_32)
        self.assertEqual(myERI_right_CDT.clean(), None)

    def test_ER_links_with_POC_ERO_TO_must_belong_to_same_pipeline_as_ER_POC(self):
        # If the parent ER is linked with a POC, the ERO TO must belong to that pipeline

        # E21_41 belongs to pipeline E
        myER = self.E21_41.execrecords.create(tainted=False)

        # This ERO has a TO that belongs to this pipeline
        myERO_good = myER.execrecordouts.create(symbolicdataset=self.triplet_3_rows_symDS,output=self.pE.outputs.get(dataset_name="E1_out"))
        self.assertEqual(myERO_good.clean(), None)
        myERO_good.delete()

        # This ERO has a TO that does NOT belong to this pipeline
        myERO_bad = myER.execrecordouts.create(symbolicdataset=self.triplet_3_rows_symDS,output=self.pD.outputs.get(dataset_name="D1_out"))
        self.assertRaisesRegexp(ValidationError,"ExecRecordOut \".*\" does not belong to the same pipeline as its parent ExecRecord POC", myERO_bad.clean)

    def test_ER_links_with_POC_and_POC_output_name_must_match_pipeline_TO_name(self):
        # The TO must have the same name as the POC which supposedly created it

        # Make ER for POC E21_41 which defines pipeline E's TO "E1_out"
        myER = self.E21_41.execrecords.create(tainted=False)

        # Define ERO with a TO that is part of pipeline E but with the wrong name from the POC
        myERO_bad = myER.execrecordouts.create(symbolicdataset=self.triplet_3_rows_symDS,output=self.pE.outputs.get(dataset_name="E2_out"))
        self.assertRaisesRegexp(ValidationError,"ExecRecordOut \".*\" does not represent the same output as its parent ExecRecord POC", myERO_bad.clean)

    def test_ER_if_dataset_is_undeleted_it_must_be_coherent_with_output(self):
        # 1) If the data is raw, the ERO output TO must also be raw
        myER = self.mC.execrecords.create(tainted=False)

        myERO_rawDS_rawTO = myER.execrecordouts.create(symbolicdataset=self.raw_symDS,output=self.C3_rawout)
        self.assertEqual(myERO_rawDS_rawTO.clean(), None)
        myERO_rawDS_rawTO.delete()

        myERO_rawDS_nonrawTO = myER.execrecordouts.create(symbolicdataset=self.raw_symDS,output=self.C1_out)
        self.assertRaisesRegexp(ValidationError,"Dataset \"raw .*\" cannot have come from output \".*\"", myERO_rawDS_nonrawTO.clean)
        myERO_rawDS_nonrawTO.delete()

        myERO_DS_rawTO = myER.execrecordouts.create(symbolicdataset=self.singlet_symDS,output=self.C3_rawout)
        self.assertRaisesRegexp(ValidationError,"Dataset \".*\" cannot have come from output \".*\"", myERO_DS_rawTO.clean)
        myERO_DS_rawTO.delete()

        myERO_DS_TO = myER.execrecordouts.create(symbolicdataset=self.singlet_symDS,output=self.C1_out)
        self.assertEqual(myERO_DS_TO.clean(), None)
        myERO_DS_TO.delete()
        
        # 2) Dataset must have the same CDT of the producing TO
        myERO_invalid_CDT = myER.execrecordouts.create(symbolicdataset=self.triplet_symDS,output=self.C1_out)
        self.assertRaisesRegexp(ValidationError,"Dataset \".*\" cannot have come from output \".*\"", myERO_DS_rawTO.clean)
        myERO_invalid_CDT.delete()

        # Dataset must have num rows within the row constraints of the producing TO
        myER_2 = self.mB.execrecords.create(tainted=False)
        myERO_too_many_rows = myER_2.execrecordouts.create(symbolicdataset=self.triplet_symDS,output=self.B1_out)
        self.assertRaisesRegexp(ValidationError,"Dataset \".*\" was produced by TransformationOutput \".*\" but has too many rows", myERO_too_many_rows.clean)
        myERO_too_many_rows.delete()

class CopperfishDatasetAndDatasetStructureTests(CopperfishExecRecordTests_setup):

    def test_Dataset_sourced_from_runstep_but_corresponding_ERO_doesnt_exist(self):
        # If a dataset comes from a runstep, an ER should exist, with an ERO referring to it

        # A run cannot exist without an ER
        # An ER cannot exist without a transformation
        # 

        # Define a run for pipeline D
        self.pD.pipeline_instances.create(user=self.myUser,

        # Define a runstep for this run

        # Define a dataset generated by a runstep
        self.runstep_symDS = SymbolicDataset()
        self.runstep_symDS.save()
        self.runstep_DS = None
        with open(os.path.join(samplecode_path, "step_0_triplet_3_rows.csv"), "rb") as f:
            self.runstep_DS = Dataset(user=self.myUser,name="triplet",description="lol",dataset_file=File(f),runstep=????????,symbolicdataset=self.runstep_symDS)
            self.runstep_DS.save()
        self.runstep_DS_structure = DatasetStructure(dataset=self.runstep_DS,compounddatatype=self.triplet_cdt)
        self.runstep_DS_structure.save()

        # No ERO points to it
        errorMessage = "Dataset \".*\" comes from runstep \".*\", but has no corresponding ERO"
        self.assertRaisesRegexp(ValidationError,errorMessage, self.runstep_DS.clean)

    def test_Dataset_sourced_from_runstep_and_ERO_exists_but_corresponding_ER_points_to_POC(self):
        # If a dataset comes from a runstep, an ER should exist, with an ERO referring to it
        # The ER must point to either a method or a pipeline, as a POCs don't relate to a runsteps

        # Define a dataset generated by a runstep
        self.runstep_symDS = SymbolicDataset()
        self.runstep_symDS.save()
        self.runstep_DS = None
        with open(os.path.join(samplecode_path, "step_0_triplet_3_rows.csv"), "rb") as f:
            self.runstep_DS = Dataset(user=self.myUser,name="triplet",description="lol",dataset_file=File(f),symbolicdataset=self.runstep_symDS)
            self.runstep_DS.save()
        self.runstep_DS_structure = DatasetStructure(dataset=self.runstep_DS,compounddatatype=self.triplet_cdt)
        self.runstep_DS_structure.save()

        # Erroneously define an ER for POC D11_21 of pipeline D with the ERO to the symbolicdataset
        D11_21_ER = self.D11_21.execrecords.create(tainted = False)
        D11_21_ER.execrecordouts.create(symbolicdataset = self.runstep_symDS, output=self.step_E2.transformation.outputs.get(dataset_name="D1_out"))

        errorMessage = "Dataset \".*\" comes from runstep \".*\", but corresponding ERO links with a POC"
        self.assertRaisesRegexp(ValidationError,errorMessage, self.runstep_DS.clean)

    def test_Dataset_sourced_from_run_so_but_corresponding_ERO_doesnt_exist(self):
        # If a dataset comes from a run, an ER should exist, with an ERO referring to it

        # Define a dataset generated by a run
        self.run_symDS = SymbolicDataset()
        self.run_symDS.save()
        self.run_DS = None
        with open(os.path.join(samplecode_path, "step_0_triplet_3_rows.csv"), "rb") as f:
            self.run_DS = Dataset(user=self.myUser,name="triplet",description="lol",dataset_file=File(f),symbolicdataset=self.run_symDS)
            self.run_DS.save()
        self.run_DS_structure = DatasetStructure(dataset=self.run_DS,compounddatatype=self.triplet_cdt)
        self.run_DS_structure.save()

        # No ERO points to it
        errorMessage = "Dataset \".*\" comes from run .*, but has no corresponding ERO"
        self.assertRaisesRegexp(ValidationError,errorMessage, self.runstep_DS.clean)

    def test_Dataset_sourced_from_run_and_ERO_exists_but_corresponding_ER_points_to_method_or_pipeline(self):
        # If a dataset comes from a run, an ER should exist, with an ERO referring to it
        # The ER must point to a POC, as method/pipelines have to do with runsteps, not runs

        # Define a dataset generated by a runstep
        self.runstep_symDS = SymbolicDataset()
        self.runstep_symDS.save()
        self.runstep_DS = None
        with open(os.path.join(samplecode_path, "step_0_triplet_3_rows.csv"), "rb") as f:
            self.runstep_DS = Dataset(user=self.myUser,name="triplet",description="lol",dataset_file=File(f),symbolicdataset=self.runstep_symDS)
            self.runstep_DS.save()
        self.runstep_DS_structure = DatasetStructure(dataset=self.runstep_DS,compounddatatype=self.triplet_cdt)
        self.runstep_DS_structure.save()

        # Erroneously define an ER for POC D11_21 of pipeline D with the ERO to the symbolicdataset
        D11_21_ER = self.D11_21.execrecords.create(tainted = False)
        D11_21_ER.execrecordouts.create(symbolicdataset = self.runstep_symDS, output=self.step_E2.transformation.outputs.get(dataset_name="D1_out"))

        errorMessage = "Dataset \".*\" comes from runstep \".*\", but corresponding ERO links with a POC"
        self.assertRaisesRegexp(ValidationError,errorMessage, self.runstep_DS.clean)



    def test_Dataset_clean_must_be_coherent_with_structure_if_applicable(self):

        # Valid dataset - raw (No structure defined)
        self.doublet_symDS = SymbolicDataset()
        self.doublet_symDS.save()
        self.doublet_DS = None
        with open(os.path.join(samplecode_path, "doublet_cdt.csv"), "rb") as f:
            self.doublet_DS = Dataset(user=self.myUser,name="doublet",description="lol",dataset_file=File(f),symbolicdataset=self.doublet_symDS)
            self.doublet_DS.save()
        self.assertEqual(self.doublet_DS.clean(), None)

        # Valid dataset - doublet
        self.doublet_DS_structure_valid = DatasetStructure(dataset=self.doublet_DS,compounddatatype=self.doublet_cdt)
        self.doublet_DS_structure_valid.save()
        self.assertEqual(self.doublet_DS.clean(), None)
        self.assertEqual(self.doublet_DS_structure_valid.clean(), None)
        self.doublet_DS_structure_valid.delete()

        # Invalid: Wrong number of columns
        self.doublet_DS_structure = DatasetStructure(dataset=self.doublet_DS,compounddatatype=self.triplet_cdt)
        self.doublet_DS_structure.save()
        errorMessage = "Dataset \".*\" does not have the same number of columns as its CDT"
        self.assertRaisesRegexp(ValidationError,errorMessage, self.doublet_DS.clean)
        self.assertRaisesRegexp(ValidationError,errorMessage, self.doublet_DS_structure.clean)
        
        # Invalid: Incorrect column header
        self.doublet_wrong_header_symDS = SymbolicDataset()
        self.doublet_wrong_header_symDS.save()
        self.doublet_wrong_header_DS = None
        with open(os.path.join(samplecode_path, "doublet_cdt_incorrect_header.csv"), "rb") as f:
            self.doublet_wrong_header_DS = Dataset(user=self.myUser,name="doublet",description="lol",dataset_file=File(f),symbolicdataset=self.doublet_wrong_header_symDS)
            self.doublet_wrong_header_DS.save()
        self.doublet_wrong_header_DS_structure = DatasetStructure(dataset=self.doublet_wrong_header_DS,compounddatatype=self.doublet_cdt)
        errorMessage = "Column .* of Dataset \".*\" is named .*, not .* as specified by its CDT"
        self.assertRaisesRegexp(ValidationError,errorMessage, self.doublet_wrong_header_DS.clean)
        self.assertRaisesRegexp(ValidationError,errorMessage, self.doublet_wrong_header_DS_structure.clean)

    def test_Dataset_check_MD5(self):
        # MD5 is now stored in symbolic dataset - even after the dataset was deleted
        self.assertEqual(self.raw_DS.compute_md5(), "7dc85e11b5c02e434af5bd3b3da9938e")

        # Initially, no change to the raw dataset has occured, so the md5 check will pass
        self.assertEqual(self.raw_DS.clean(), None)

        # The contents of the file are changed, disrupting file integrity
        self.raw_DS.dataset_file.close()
        self.raw_DS.dataset_file.open(mode='w')
        self.raw_DS.dataset_file.close()
        errorMessage = "File integrity of \".*\" lost. Current checksum \".*\" does not equal expected checksum \".*\""
        self.assertRaisesRegexp(ValidationError,errorMessage, self.raw_DS.clean)

    def test_Dataset_is_raw(self):
        self.assertEqual(self.triplet_DS.is_raw(), False)
        self.assertEqual(self.raw_DS.is_raw(), True)
        
    def test_DatasetStructure_clean_check_CSV(self):

        # triplet_DS has CSV format conforming to it's CDT
        self.triplet_DS.structure.clean()

        # Define a dataset, but with the wrong number of headers
        symDS = SymbolicDataset()
        symDS.save()
        DS1 = None
        with open(os.path.join(samplecode_path, "step_0_triplet_3_rows.csv"), "rb") as f:
            DS1 = Dataset(user=self.myUser,name="DS1",description="DS1 desc",dataset_file=File(f),symbolicdataset=symDS)
            DS1.save()
        structure = DatasetStructure(dataset=DS1,compounddatatype=self.doublet_cdt)

        errorMessage = "Dataset \".*\" does not have the same number of columns as its CDT"
        self.assertRaisesRegexp(ValidationError,errorMessage, structure.clean)

        # Define a dataset with the right number of header columns, but the wrong column names
        symDS2 = SymbolicDataset()
        symDS2.save()
        DS2 = None
        with open(os.path.join(samplecode_path, "three_random_columns.csv"), "rb") as f:
            DS2 = Dataset(user=self.myUser,name="DS2",description="DS2 desc",dataset_file=File(f),symbolicdataset=symDS2)
            DS2.save()
        structure2 = DatasetStructure(dataset=DS2,compounddatatype=self.triplet_cdt)

        errorMessage = "Column 1 of Dataset \".*\" is named .*, not .* as specified by its CDT"
        self.assertRaisesRegexp(ValidationError,errorMessage, structure2.clean)

    def test_Dataset_num_rows(self):
        self.assertEqual(self.triplet_3_rows_DS.num_rows(), 3)
        self.assertEqual(self.triplet_3_rows_DS.structure.num_rows(), 3)

