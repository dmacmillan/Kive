"""
copperfish.models
"""

from django.db import models;
from django.contrib.auth.models import User;
from django.contrib.contenttypes.models import ContentType;
from django.contrib.contenttypes import generic;
from django.db.models.signals import pre_save, post_save;
from django.dispatch import receiver;
from django.core.exceptions import ValidationError;
from django.core.validators import MinValueValidator;

import operator;		# Python math functions
import hashlib;			# To calculate MD5 hash

class Datatype(models.Model):
	"""
	Abstract definition of a semantically atomic type of data.
	Related to :model:`copperfish.CompoundDatatype`
	"""

	# Implicitly defined
	#   restricted_by (self/ManyToMany)
	#   compoundDatatypeMember_set (ForeignKey)

	name = models.CharField(max_length=64)

	# auto_now_add: set date_created to now on instantiation (editable=False)
	date_created = models.DateTimeField('Date created',
										auto_now_add = True)

	description = models.TextField()

	# Datatypes aren't necesarilly generated using python but are VALIDATED with python
	Python_type = models.CharField(	'Python variable type',
									max_length=64,
									help_text="Python type, such as String, Int, Date");

	# Todo: Check for circularly defined restrictions
	restricts = models.ManyToManyField(	'self',
										symmetrical=False,
										related_name="restricted_by",
										null=True,
										blank=True,
										help_text="Captures hierarchical is-a classifications among Datatypes");

	verification_script = models.FileField(	"Verification script",
											upload_to='VerificationScripts',
											help_text="Validates inputs labelled as a DataType in actually being that DataType")

	def __unicode__(self):
		"""Describe a Datatype by it's name"""
		return self.name;

class CompoundDatatypeMember(models.Model):
	"""
	An actual data type member of a particular CompoundDatatype.
	Related to :model:`copperfish.Dataset`
	Related to :model:`copperfish.CompoundDatatype`
	"""

	compounddatatype = models.ForeignKey(	"CompoundDatatype",
											related_name="members",
											help_text="Links this DataType member to a particular CompoundDataType");

	datatype = models.ForeignKey(	Datatype,
									help_text="Specifies which DataType this member actually is");

	column_name = models.CharField(	"Column name",
									max_length=128,
									help_text="Gives datatype a name - removes the need for collumn indices");

	# MinValueValidator(1) constrains column_idx to be >= 1
	column_idx = models.PositiveIntegerField(	validators=[MinValueValidator(1)]
												help_text="The column number of this DataType");

	# Define database indexing rules to ensure tuple uniqueness
	# A compoundDataType cannot have 2 member definitions with the same column name or column number
	class Meta:
		unique_together = 	(("compounddatatype", "column_name"),
							("compounddatatype", "column_idx"));

	def __unicode__(self):
		"""Describe a CompoundDatatypeMember with it's column number, datatype name, and column name"""
		return u"{}: <{}> [{}]".format(	self.column_idx,
										unicode(self.datatype),
										self.column_name);

class CompoundDatatype(models.Model):
	"""
	A structured collection of datatypes generated by, or necessary for, a Transformation.
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
		# Can we do this? members_with_indices = [ (all_members[i].column_idx, all_members[i]) for i in range(len(all_members))];

		# Sort members using column index as a basis (operator.itemgetter(0))
		members_with_indices = sorted(	members_with_indices,
										key=operator.itemgetter(0));

		# Add sorted Datatype members to the string representation
		for i, colIdx_and_member in enumerate(members_with_indices):
			colIdx, member = colIdx_and_member;
			string_rep += unicode(member);

			# Add comma if not at the end of member list
			if i != len(members_with_indices) - 1:
				string_rep += ", ";

		string_rep += ")";
		return string_rep;

	# clean() is executed prior to save() to perform model validation
	def clean(self):
		"""Check if Datatype members have consecutive indices from 1 to n"""
		column_indices = [];

		# += is shorthand for extend() - concatenate a list with another list
		for member in self.members.all():
			column_indices += [member.column_idx];

		# Check if the sorted list is exactly a sequence from 1 to n
		if sorted(column_indices) != range(1, self.members.count()+1):
			raise ValidationError("Column indices are not consecutive starting from 1");


class Dataset(models.Model):
	"""
	Datasets uploaded by users, to be used as inputs for transformations.
	Related to :model:`copperfish.PipelineStep`
	Related to :model:`copperfish.CompoundDatatype`
	"""

	# Implicitly defined
	#   descendent_datasets (self/ManyToMany)

	# Activating admin panel creates a Users model
	user = models.ForeignKey(	User,
								help_text="User that uploaded this dataset.");

	name = models.CharField(	"Dataset name",
								max_length=128,
								help_text="Description of this dataset.");

	description = models.TextField("Dataset description");

	date_created = models.DateTimeField(	auto_now_add=True,
											help_text="Date of dataset upload.");


	# Pipeline step this Dataset come from (Null if Dataset was manually uploaded)
	pipeline_step = models.ForeignKey(	"Pipeline Step",
									  	related_name="data_produced",
									  	null=True,
										blank=True);

	# Output 'hole' within a pipeline the Dataset comes from
	pipeline_step_output_name = models.CharField(	"Output hole",
													max_length=128,
													blank=True,
													help_text="The output 'hole' this dataset comes from (If applicable)");

	# Parent datasets this dataset is derived from
	parent_datasets = models.ManyToManyField(	'self',
												related_name="descendent_datasets",
												null=True,
												blank=True);

	# Datasets are restricted by a compound data type
	compounddatatype = models.ForeignKey(	CompoundDatatype,
											related_name="conforming_datasets");

	dataset_file = models.FileField(upload_to="Datasets",
									help_text="File path where datasets are stored");

	MD5_checksum = models.CharField(	max_length=64,
										help_text="Used to check dataset file integrity");


	def __unicode__(self):
		"""Display the Dataset name, user, and date created."""
		return "{} (created by {} on {})".format(	self.name,
												 	unicode(self.user),
												 	self.date_created);

	# Before completing a save(), generate the MD5 hash
	def clean(self):
		"""If a file specified, populate the MD5 checksum."""

		try:
			md5gen = hashlib.md5();
			md5gen.update(self.dataset_file.read());
			self.MD5_checksum = md5gen.hexdigest();

		except ValueError as e:
			print(e);
			print("No file found; setting MD5 checksum to the empty string.");
			self.MD5_checksum = "";
	

class CodeResource(models.Model):
	"""
	A CodeResource is any file tracked by ShipYard.
	Related to :model:`copperfish.CodeResourceRevision`
	"""

	# Implicitly defined
	#   revisions (codeResourceRevision/ForeignKey)

	name = models.CharField("Resource name",
							max_length=128,
							help_text="A name for this resource");

	description = models.TextField("Resource description");

	def __unicode__(self):
		return self.name;

class CodeResourceRevision(models.Model):
	"""
	A particular revision of a code resource.

	Related to :model:`copperfish.CodeResource`
	Related to :model:`copperfish.CodeResourceDependency`
	Related to :model:`copperfish.Method`
	"""

	# Implicitly defined
	#   descendents (self/ForeignKey)
	#   dependencies (CodeResourceDependency/ForeignKey)
	#   needed_by (CodeResourceDependency/ForeignKey)
	#   method_set (Method/ForeignKey) ???

	coderesource = models.ForeignKey(	CodeResource,
										related_name="revisions");	
		
	revision_name = models.CharField(
			max_length=128,
			help_text="A name to differentiate revisions of a CodeResource");

	revision_DateTime = models.DateTimeField(
			auto_now_add=True,
			help_text="Date this resource revision was uploaded");

	revision_parent = models.ForeignKey('self',
										related_name="descendants",
										null=True,
										blank=True);

	revision_desc = models.TextField(
			"Revision description",
			help_text="A description for this particular resource revision");

	content_file = models.FileField(
			"File contents",
			upload_to="CodeResources",
			null=True,
			blank=True,
			help_text="File contents of this code resource revision");

	MD5_checksum = models.CharField(
			max_length=64,
			blank=True,
			help_text="Used to validate file contents of this resource revision");

	def __unicode__(self):
		"""Represent a resource revision by it's CodeResource name and revision name"""
		
		# The admin can create a CodeResource without save()ing to the database
		# and allow a corresponding CodeResource Revision to be created in memory
		# Thus in MEMORY, a revision can temporarily have no corresponding CodeResource
		if not hasattr(self, "coderesource"):
			return u"[no code resource set] {}".format(self.revision_name);
		
		string_rep = self.coderesource.name + u" " + self.revision_name;
		return string_rep;

	# A CodeResource can simply be a collection of dependencies, and not actually
	# contain a file - thus, an MD5 hash may not need to exist
	def clean(self):
		"""If there is a file specified, fill in the MD5 checksum."""

		try:
			md5gen = hashlib.md5();
			md5gen.update(self.content_file.read());
			self.MD5_checksum = md5gen.hexdigest();

		except ValueError as e:
			self.MD5_checksum = "";

class CodeResourceDependency(models.Model):
	"""
	Dependencies of a CodeResourceRevision - themselves also CodeResources.
	Related to :model:`copperfish.CodeResourceRevision`
	"""

	coderesourcerevision = models.ForeignKey(CodeResourceRevision,
											 related_name="dependencies");

	# Dependency is a codeResourceRevision
	requirement = models.ForeignKey(CodeResourceRevision,
	                                related_name="needed_by");

	# ???????????????????????????	
	# Where to store it (relative to the sandbox) FIXME: use a FilePathField?
	where = models.CharField(
			"Dependency location",
			max_length=100,
			help_text="Where a code resource dependency must exist - relative to the sandbox??? or the code resource?");

	def __unicode__(self):
		"""Represent as [codeResourceRevision] requires [dependency] as [dependencyLocation]."""
		return u"{} requires {} as {}".format(
				unicode(self.coderesourcerevision),
				unicode(self.requirement),
				self.where);

class TransformationFamily(models.Model):
	"""
	TransformationFamily is abstract and describes common
	parameters between MethodFamily and PipelineFamily.

	Extends :model:`copperfish.MethodFamily`
	Extends :model:`copperfish.PipelineFamily`
	"""

	name = models.CharField(
			"Transformation family name",
			max_length=128,
			help_text="The name given to a group of methods/pipelines");

	description = models.TextField(
			"Transformation family description",
			help_text="A description for this collection of methods/pipelines");

	def __unicode__(self):
		""" Describe transformation family by it's name """
		return self.name;

	class Meta:
		abstract = True;

class MethodFamily(TransformationFamily):
	"""
	MethodFamily groups revisions of Methods together.

	Inherits :model:`copperfish.TransformationFamily`
	Related to :model:`copperfish.Method`
	"""

	# Implicitly defined:
	#   members (Method/ForeignKey)

	pass

class PipelineFamily(TransformationFamily):
	"""
	PipelineFamily groups revisions of Pipelines together.

	Inherits :model:`copperfish.TransformationFamily`
	Related to :model:`copperfish.Pipeline`
	"""

	# Implicitly defined:
	#   members (Pipeline/ForeignKey)

	pass


class Transformation(models.Model):
	"""
	Abstract class that defines common parameters
	across Method revisions and Pipeline revisions.

	Extends :model:`copperfish.Method`
	Extends :model:`copperfish.Pipeline`
	Related to :model:`TransformationInput`
	Related to :model:`TransformationOutput`

	I DO NOT SEE THE CODE THAT RELATES THIS CLASS TO COMPOUND DATA TYPE
	"""

	revision_name = models.CharField(
			"Transformation revision name",
			max_length=128,
			help_text="The name of this transformation revision");

	revision_DateTime = models.DateTimeField(
			"Revision creation date",
			auto_now_add = True);

	revision_desc = models.TextField(
			"Transformation revision description",
			help_text="Description of this transformation revision");

	# inputs/outputs associated with transformations via GenericForeignKey
	# And can be accessed from within Transformations via GenericRelation
	inputs = generic.GenericRelation("TransformationInput");
	outputs = generic.GenericRelation("TransformationOutput");

	class Meta:
		abstract = True;

	def check_input_indices(self):
		"""Check that input indices are numbered consecutively from 1"""

		# Append each input index (hole number) to a list
		input_nums = [];
		for curr_input in self.inputs.all():
			input_nums += [curr_input.dataset_idx];

		# Indices must be consecutively numbered from 1 to n
		if sorted(input_nums) != range(1, self.inputs.count()+1):
			raise ValidationError(
					"Inputs are not consecutively numbered starting from 1");
		
	def check_output_indices(self):
		"""Check that output indices are numbered consecutively from 1"""

		# Append each output index (hole number) to a list
		output_nums = [];
		for curr_output in self.outputs.all():
			output_nums += [curr_output.dataset_idx];

		# Indices must be consecutively numbered from 1 to n
		if sorted(output_nums) != range(1, self.outputs.count()+1):
			raise ValidationError(
					"Outputs are not consecutively numbered starting from 1");

	def clean(self):
		"""Validate transformation inputs and outputs."""

		self.check_input_indices();
		self.check_output_indices();
		# FIXME: ALSO NEED TO CHECK WE DO NOT HAVE MULTIPLE INPUT/OUTPUTS OF THE SAME NAME

class Method(Transformation):
	"""
	Methods are atomic transformations.

	Inherits from :model:`copperfish.Transformation`
	Related to :model:`copperfish.CodeResource`
	Related to :model:`copperfish.MethodFamily`
	"""

	# Implicitly defined:
	#   descendants (self/ForeignKey)

	family = models.ForeignKey(	MethodFamily,
								related_name="members");

	revision_parent = models.ForeignKey("self",
										related_name = "descendants",
										null=True,
										blank=True);

	# Unenforced constraint - code resource revision must be executable
	driver = models.ForeignKey(CodeResourceRevision);

	def __unicode__(self):
		"""Represent a method by it's revision name and method family"""
		string_rep = u"Method {} {}".format("{}", self.revision_name);

		# MethodFamily may not be temporally saved in DB if created by admin
		if hasattr(self, "family"):
			string_rep = string_rep.format(unicode(self.family));
		else:
			string_rep = string_rep.format("[family unset]");

		return string_rep;

	def save(self, *args, **kwargs):
		"""
		Create or update a method revision.

		If a method revision being created is derived from a parental
		method revision, copy the parent input/outputs.
		"""

		# Inputs/outputs cannot be stored in the database unless this
		# method revision has itself first been saved to the database
		super(Method, self).save(*args, **kwargs);

		# If no parent revision exists, there are no input/outputs to copy
		if self.revision_parent == None:
			return None;

		# If parent revision exists, and inputs/outputs haven't been registered,
		# copy all inputs and outputs from the parent revision to this revision
		if self.inputs.count() + self.outputs.count() == 0:

			for parent_input in self.revision_parent.inputs.all():
				self.inputs.create(
						compounddatatype = parent_input.compounddatatype,
						dataset_name = parent_input.dataset_name,
						dataset_idx = parent_input.dataset_idx,
						min_row = parent_input.min_row,
						max_row = parent_input.max_row);

			for parent_output in self.revision_parent.outputs.all():
				self.outputs.create(
						compounddatatype = parent_output.compounddatatype,
						dataset_name = parent_output.dataset_name,
						dataset_idx = parent_output.dataset_idx,
						min_row = parent_output.min_row,
						max_row = parent_output.max_row);
				

class Pipeline(Transformation):
	"""
	A particular pipeline revision.

	Inherits from :model:`copperfish.Transformation`
	Related to :model:`copperfish.PipelineFamily`
	Related to :model:`copperfish.PipelineStep`
	Related to :model:`copperfish.PipelineOutputMapping`
	"""

	# Implicitly defined
	#   steps (PipelineStep/ForeignKey)
	#   descendants (self/ForeignKey)
	#   outmap (PipelineOutputMapping/ForeignKey)

	family = models.ForeignKey(	PipelineFamily,
								related_name="members");	

	revision_parent = models.ForeignKey("self",
										related_name = "descendants",
										null=True,
										blank=True);

	# When defining a pipeline, we don't define the outputs; we define
	# outmap instead and during the clean stage the outputs are created. (?????)
	
	# outmap describes where a given terminal pipeline output of a pipeline comes from...
	# with respect to its own steps' outputs

	def __unicode__(self):
		"""Represent pipeline by revision name and pipeline family"""

		string_rep = u"Pipeline {} {}".format("{}", self.revision_name);

		# If family isn't set (if created from family admin page)
		if hasattr(self, "family"):
			string_rep = string_rep.format(unicode(self.family));
		else:
			string_rep = string_rep.format("[family unset]");

		return string_rep;

	def clean(self):
		"""
		Validate pipeline revision inputs/outputs

		1) Pipeline STEPS must be consecutively starting from 1
		2) Pipeline INPUTS must be consecutively numbered from 1
		3) Inputs are available at a needed step and of the type expected
		4) Outputs of the pipeline will be mapped to outputs generated by its steps (???)
		"""

		# Check that inputs are numbered consecutively from 1 (???)
		# We don't care about the outputs, but if they are set, check them (???)



		# Transformation.clean() - check for consecutive numbering of
		# input/outputs for this pipeline as a whole
		super(Pipeline, self).clean();


		# Internal pipeline STEP numbers must be consecutive from 1 to n
		all_steps = self.steps.all();
		step_nums = [];

		for step in all_steps:
			step_nums += [step.step_num];

		if sorted(step_nums) != range(1, len(all_steps)+1):
			raise ValidationError(
					"Steps are not consecutively numbered starting from 1");


		# Check that steps are coherent with each other
		#
		# Are inputs at each step...
		#	A) Available? (Produced by a previous step + not deleted, OR an absolute input)
		#	B) Of the correct CompoundDatatype? And, not have contrary min/max row constraints?

		###### ARE WE CHECKING THAT ONLY A SINGLE WIRE LEADS TO A DESTINATION INPUT? ##########

		# For each Pipeline step
 		for step in all_steps:

			# Extract wiring parameters (PipelineStepInput) for each input
			for curr_in in step.inputs.all():
				input_requested = curr_in.provider_output_name;		# Output hole where source data originates
				requested_from = curr_in.step_providing_input;		# Pipeline step of wiring destination
				feed_to_input = curr_in.transf_input_name;			# Input hole of wiring destination

				# Find the requested input; raise ValidationError on failure (???)
				req_input = None;

				# If this pipeline step's input is from step 0, it doesn't come from previous steps
				if requested_from == 0:

					# Get pipeline inputs of self (Pipeline, a transformation)
					# Look for pipeline inputs that match the desired wiring source output name
					try:
						req_input = self.inputs.get(
								dataset_name=input_requested);

					except TransformationInput.DoesNotExist as e:
						raise ValidationError(
								"Pipeline does not have input \"{}\"".
								format(input_requested));	

				# If not from step 0, input derives from the output of a pipeline steps
				else:

					# Look at the pipeline step referenced by the wiring parameter
					providing_step = all_steps[requested_from-1];

					# Do any outputs at this pipeline step/transformation have the name requested?
					try:
						req_input = providing_step.transformation.outputs.get(
								dataset_name=input_requested);

					except TransformationOutput.DoesNotExist as e:
						raise ValidationError(
								"Transformation at step {} does not produce output \"{}\"".
								format(requested_from, input_requested));
						
					# Was the data from this step's transformation output deleted?
					if providing_step.outputs_to_delete.filter(
							dataset_to_delete=input_requested).count() != 0:

						# Identify wiring source output name + step number, and desired step availability
						raise ValidationError(
								"Input \"{}\" from step {} to step {} is deleted prior to request".
								format(input_requested, requested_from,
									   step.step_num));

				# Get the input from this step's transformation that has an input hole
				# name matching the wiring destination input hole name

				# That is to say, check that the wiring-requested input matches the prototype

				# Don't check for ValidationError because this was checked in the clean() of PipelineStep.
				transf_input = step.transformation.inputs.get(dataset_name=feed_to_input);

				# FIXME: we're just going to enforce that transf_input
				# and req_input have the same CompoundDatatype, rather
				# than making sure that their CompoundDatatypes match;
				# is this too restrictive?

				# For this (input,step) wiring, a matching output (req_input) as determined by
				# output hole name (dataset_name) was found at the requested step, but we still
				# need to check that their compounddatatypes match
				if req_input.compounddatatype != transf_input.compounddatatype:
					raise ValidationError(
							"Data fed to input \"{}\" of step {} does not have the expected CompoundDatatype".
							format(feed_to_input, step.step_num));

				provided_min_row = 0;
				required_min_row = 0;

				# Source output row constraint
				if req_input.min_row != None:
					providing_min_row = req_input.min_row;

				# Destination input row constraint
				if transf_input.min_row != None:
					required_min_row = transf_input.min_row;

				# Check for contradictory min row constraints
				if (provided_min_row < required_min_row):
					raise ValidationError(
							"Data fed to input \"{}\" of step {} may have too few rows".
							format(feed_to_input, step.step_num));
				
				provided_max_row = float("inf");
				required_max_row = float("inf");

				if req_input.max_row != None:
					providing_max_row = req_input.max_row;

				if transf_input.max_row != None:
					required_max_row = transf_input.max_row;

				# Check for contradictory max row constraints
				if (provided_max_row > required_max_row):
					raise ValidationError(
							"Data fed to input \"{}\" of step {} may have too many rows".
							format(feed_to_input, step.step_num));

		# Check pipeline output wiring for coherence
		output_indices = [];

		for mapping in self.outmap.all():
			output_requested = mapping.provider_output_name;
			requested_from = mapping.step_providing_output;
			connect_to_output = mapping.output_name;
			output_indices += [mapping.output_idx];

			# Source step number must be in range
			if requested_from > len(all_steps):
				raise ValidationError(
						"Output requested from a non-existent step");	
			
			# Given it is valid, access that step for deeper inspection
			providing_step = all_steps[requested_from-1];
			req_output = None;

			# Try to find an output hole with a matching name
			try:
				req_output = providing_step.transformation.outputs.get(
						dataset_name=output_requested);
			except TransformationOutput.DoesNotExist as e:
				raise ValidationError(
						"Transformation at step {} does not produce output \"{}\"".
						format(requested_from, output_requested));

			# Also determine if output was deleted by the step producing it
			if providing_step.outputs_to_delete.filter(
					dataset_to_delete=output_requested).count() != 0:
				raise ValidationError(
						"Output \"{}\" from step {} is deleted prior to request".
						format(output_requested, requested_from));

		# Also check if pipeline outputs are numbered consecutively
		if sorted(output_indices) != range(1, self.outmap.count()+1):
			raise ValidationError(
					"Outputs are not consecutively numbered starting from 1");


	def save(self, *args, **kwargs):
		"""
		When saving, a pipline, set up outputs as specified.

		This must be done after saving, because otherwise the manager for
		the calling instance's outputs will not have been set up. (???)
		"""

		# Call Transformation's save() first
		super(Pipeline, self).save(*args, **kwargs);

		# Delete existing pipeline outputs
		# Be careful if customizing delete() of TransformationOutput

		self.outputs.all().delete();

		# Then query all steps and regenerate outputs
		all_steps = self.steps.all();

		# outmap is derived from (PipelineOutputMapping/ForeignKey)
		# For each wiring, extract the wiring parameters
 		for mapping in self.outmap.all():
			output_requested = mapping.provider_output_name;
			requested_from = mapping.step_providing_output;
			connect_to_output = mapping.output_name;

			# Access the referenced step and check outputs
			# for a matching output hole name
			providing_step = all_steps[requested_from-1];
			req_output = providing_step.transformation.outputs.get(
					dataset_name=output_requested);

			# If it matches, save the pipeline output
			self.outputs.create(compounddatatype=req_output.compounddatatype,
								dataset_name=connect_to_output,
								dataset_idx=mapping.output_idx,
								min_row=req_output.min_row,
								max_row=req_output.max_row);

 			

class PipelineStep(models.Model):
	"""
	A step within a Pipeline representing a single transformation
	operating on inputs that are either pre-loaded (Pipeline inputs)
	or derived from previous pipeline steps within the same pipeline.

	Related to :mode;:`copperfish.Dataset`
	Related to :model:`copperfish.Pipeline`
	Related to :model:`copperfish.Transformation`
	Related to :model:`copperfish.PipelineStepInput`
	Related to :model:`copperfish.PipelineStepDelete`
	"""

	# Implicitly defined
	#   inputs (PipelineStepInput/ForeignKey)
	#   outputs_to_delete: from PipelineStepDelete

	pipeline = models.ForeignKey(	Pipeline,
									related_name="steps");

	# Pipeline steps are associated with a method or pipeline (WHY???) [names must be lower-case]
	content_type = models.ForeignKey(	ContentType,
										limit_choices_to = {"model__in": ("method", "pipeline")});

	object_id = models.PositiveIntegerField();
	transformation = generic.GenericForeignKey("content_type", "object_id");
	step_num = models.PositiveIntegerField(validators=[MinValueValidator(1)]);
	
	def __unicode__(self):
		""" Represent with the pipeline and step number """

		pipeline_name = "[no pipeline assigned]";	
		if hasattr(self, "pipeline"):
			pipeline_name = unicode(self.pipeline);
		return "{} step {}".format(pipeline_name, self.step_num);

	def recursive_pipeline_check(self, pipeline):
		"""Check if the specified pipeline occurs within this step."""

		contains_pipeline = False;

		# Base case 1: the transformation is a method.
		if type(self.transformation) == Method:
			contains_pipeline = False;

		# Base case 2: the transformation equals the pipeline.

		# WHY ARE WE NOT CALLING TYPE() HERE???
		elif self.transformation == pipeline:
			contains_pipeline = True;

		# Recursive case: go through all of the pipeline steps.
		else:
			transf_steps = self.transformation.steps.all();
			for step in transf_steps:
				step_contains_pipeline = step.recursive_pipeline_check(pipeline);
				if step_contains_pipeline:
					contains_pipeline = True;
		return contains_pipeline;

	def clean(self):
		"""
		Check coherence of this step of the pipeline.

		1) Do inputs come from prior steps?
		2) Do inputs map correctly to the transformation at this step?
		3) Do outputs marked for deletion come from this transformation?
		4) Does the transformation at this step contain the parent pipeline?
		"""

		# Check recursively to see if this step's transformation contains
		# the specified pipeline at all.
		if self.recursive_pipeline_check(self.pipeline):
			raise ValidationError("Step {} contains the parent pipeline".
								  format(self.step_num));
 			
		for curr_in in self.inputs.all():
			input_requested = curr_in.provider_output_name;
			requested_from = curr_in.step_providing_input;
			feed_to_input = curr_in.transf_input_name;
				
			# Does this input come from a step prior to this one?
			if requested_from >= self.step_num:
				raise ValidationError(
						"Input \"{}\" to step {} does not come from a prior step".
						format(input_requested, self.step_num));

			# Does the transformation at this step have an input named
			# feed_to_input?
			try:
				self.transformation.inputs.get(dataset_name=feed_to_input);
			except TransformationInput.DoesNotExist as e:
				raise ValidationError ("Transformation at step {} has no input named \"{}\"".
						format(self.step_num, feed_to_input));
 
		for curr_del in self.outputs_to_delete.all():
			to_del = curr_del.dataset_to_delete;

			# Check that to_del is one of the outputs of the current step's
			# Transformation.
			if self.transformation.outputs.\
				filter(dataset_name=to_del).count() == 0:
				raise ValidationError(
						"Transformation at step {} has no output named \"{}\"".
						format(self.step_num, to_del));


class PipelineStepInput(models.Model):
	"""
	Represents the "wires" feeding into the transformation of a
	particular pipeline step, specifically:

	A) Destination of wire (transf_input_name) - step implicitly defined
	B) Source of the wire (step_providing_input, provider_output_name)

	Related to :model:`copperfish.PipelineStep`
	"""

	# The step where we are defining wires
	# RECALL: a pipeline step involves a single transformation
	pipelinestep = models.ForeignKey(	PipelineStep,
										related_name = "inputs");

	# Input hole (TransformationInput.dataset_name) of the step's
	# transformation to which the wire leads

	transf_input_name = models.CharField(	"Transformation input name",
											max_length=128,
											help_text="Wiring destination input hole name");


	# The tuple (step_providing_input, provider_output_name) unambiguously defines
	# the source of the wire

	step_providing_input = models.PositiveIntegerField(	"Step providing input",
														help_text="Wiring source step");

	provider_output_name = models.CharField("Provider output name",
											max_length=128,
											help_text="Wiring source output hole name");

	# FIXME: Refactor transf_input_name and provider_output_name as ForeignKeys to
	# TransformationInput and TransformationOutput objects

	# CONDITIONS: step_providing_input must be PRIOR to this pipeline step (Time moves forward)
	# Coherence of data here will be enforced at the Python level
	# IE, does this refer to a Dataset produced by the Transformation at the specified step?

	def __unicode__(self):
		"""Represent PipelineStepInput with the pipeline step, and the wiring destination input name"""
		step_str = "[no pipeline step set]";
		if self.pipelinestep != None:
			step_str = unicode(self.pipelinestep);
		return "{}:{}".format(step_str, self.transf_input_name);	


class PipelineStepDelete(models.Model):
	"""
	PipelineStepDelete defines what output datasets can be immediately deleted.
	(Recall, each pipeline step involves a transformation that generates outputs)

	Related to :model:`copperfish.PipelineStep`
	"""
	pipelinestep = models.ForeignKey(PipelineStep,
	                                 related_name="outputs_to_delete");

	# Again, coherence of data will be enforced at the Python level
	# (i.e. does this actually refer to a Dataset that will be produced
	# by the Transformation at this step)

	# dataset_name of TransformationOutput for the transformation at this step
	dataset_to_delete = models.CharField(	"Dataset to delete",
											max_length=128,
											help_text="");


class PipelineOutputMapping(models.Model):
	"""
	Defines which outputs of internal PipelineSteps are mapped to
	end-point Pipeline outputs once internal execution is complete.

	Thus, a definition of wires leading to external pipeline outputs.

	Related to :model:`copperfish.Pipeline`
	Related to :model:`copperfish.TransformationOutput` (Refactoring needed)
	"""

	pipeline = models.ForeignKey(	Pipeline,
									related_name="outmap");

	output_name = models.CharField(	"Destination output hole",
									max_length=128,
									help_text="");

	# WHY DO WE NEED BOTH OUTPUT_NAME AND OUTPUT_IDX???????
	# ISNT THE NAME<->INDEX MAPPING HANDLED ELSEWHERE????
	output_idx = models.PositiveIntegerField(
			"",
			validators=[MinValueValidator(1)],
			help_text="");

	# FIXME: Refactor (output_name, output_idx) as a TransformationOutput


	# PRE: step_providing_output is an actual step of the pipeline
	# and provider_output_name actually refers to one of the outputs
	# at that step
	# The coherence of the data here will be enforced at the Python level

	step_providing_output = models.PositiveIntegerField(
			"Step providing output",
			validators=[MinValueValidator(1)],
			help_text="Source step at which output comes from");

	provider_output_name = models.CharField(
			"Provider output name",
			max_length=128,
			help_text="Source output hole name");

	def __unicode__(self):
		""" Represent with the pipeline name, output index, and output name (???) """
		pipeline_name = "[no pipeline set]";
		if self.pipeline != None:
			pipeline_name = unicode(self.pipeline);

		return "{}:{} ({})".format(pipeline_name, self.output_idx,
								   self.output_name);


class TransformationXput(models.Model):
	"""
	Describes parameters common to all inputs and outputs
	of transformations - the "holes"

	Extends :model:`copperfish.TransformationInput`
	Extends :model:`copperfish.TransformationOutput`
	"""

	# TransformationXput describes the input/outputs of transformations
	# So this class can only be associated with method and pipeline
	content_type = models.ForeignKey(
			ContentType,
			limit_choices_to = {"model__in": ("method", "pipeline")});
	object_id = models.PositiveIntegerField();
	transformation = generic.GenericForeignKey("content_type", "object_id");

	# The expected compounddatatype of the input/output
	compounddatatype = models.ForeignKey(CompoundDatatype);

	# The name of the "input/output" hole
	dataset_name = models.CharField("Input/output name",
									max_length=128,
									help_text="A name for this input/output as an alternative to input/output index");

	# Input/output index on the transformation
	dataset_idx = models.PositiveIntegerField(validators=[MinValueValidator(1)]);
	
	# Nullable fields indicating that this dataset has
	# restrictions on how many rows it can have
	min_row = models.PositiveIntegerField(null=True, blank=True);
	max_row = models.PositiveIntegerField(null=True, blank=True);

	class Meta:
		abstract = True;

		unique_together = (("content_type", "object_id", "dataset_name"),
						   ("content_type", "object_id", "dataset_idx"));

	def __unicode__(self):
		return u"[{}]:{} {} {}".format(unicode(self.transformation),
									   self.dataset_idx,
									   unicode(self.compounddatatype),
									   self.dataset_name);

class TransformationInput(TransformationXput):
	"""
	Inherits from :model:`copperfish.TransformationXput`
	"""

	# Implicitly defined:
	#   transformations (MapTransformationToInput - ?????????)
	pass

class TransformationOutput(TransformationXput):
	"""
	Inherits from :model:`copperfish.TransformationXput`
	"""

	# Implicitly defined:
	#   transformations (MapTransformationToOutput - ?????????)
	pass
