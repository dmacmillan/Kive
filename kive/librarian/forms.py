"""
Generate an HTML form to create a new DataSet object
"""
from django import forms
from django.forms.widgets import ClearableFileInput
from django.utils.translation import ugettext_lazy as _, ungettext_lazy
from django.contrib.auth.models import User, Group

import logging
from datetime import datetime

from metadata.forms import PermissionsForm
from metadata.models import CompoundDatatype
from librarian.models import Dataset
import metadata.forms

import zipfile
import tarfile
import six
from zipfile import ZipFile

from constants import maxlengths

LOGGER = logging.getLogger(__name__)


class DatasetDetailsForm(PermissionsForm):
    """
    Handles just the Dataset details.
    """
    class Meta:
        model = Dataset
        fields = ("name", "description", "permissions")

    def _post_clean(self):
        pass


class DatasetForm(forms.ModelForm):
    """
    User-entered single dataset.
    """
    permissions = metadata.forms.PermissionsField(
        label="Users and groups allowed",
        help_text="Which users and groups are allowed access to this Dataset?",
        required=False
    )

    dataset_file = forms.FileField(
        required=False,
        allow_empty_file=True,
        max_length=maxlengths.MAX_FILENAME_LENGTH
    )

    RAW_CDT_CHOICE = (CompoundDatatype.RAW_ID, CompoundDatatype.RAW_VERBOSE_NAME)
    compound_datatype_choices = [RAW_CDT_CHOICE]
    compound_datatype = forms.ChoiceField(choices=compound_datatype_choices)

    save_in_db = forms.BooleanField(
        label="Keep a copy in Kive",
        required=False
    )

    # external_path = forms.CharField(
    #     widget=forms.Select(
    #         choices=[
    #             ('', '--- choose an external file directory ---')
    #         ]
    #     )
    # )

    class Meta:
        model = Dataset
        fields = (
            'name',
            'description',
            'dataset_file',
            'externalfiledirectory',
            'external_path',
            'save_in_db',
            'permissions',
            'compound_datatype'
        )

    def __init__(self, data=None, files=None, users_allowed=None, groups_allowed=None, user=None, *args, **kwargs):
        super(DatasetForm, self).__init__(data, files, *args, **kwargs)
        users_allowed = users_allowed or User.objects.all()
        groups_allowed = groups_allowed or Group.objects.all()
        self.fields["permissions"].set_users_groups_allowed(users_allowed, groups_allowed)

        user_specific_choices = ([DatasetForm.RAW_CDT_CHOICE] +
                                 CompoundDatatype.choices(user))
        self.fields["compound_datatype"].choices = user_specific_choices

    def clean(self):
        """
        Some quick sanity checks on the input.

        Note: external_path and externalfiledirectory must both or neither be specified,
        which is handled by model validation *but* because we have overridden _post_clean
        we need to do it here explicitly.
        """
        cleaned_data = super(DatasetForm, self).clean()
        dataset_file = cleaned_data.get("dataset_file")
        externalfiledirectory = cleaned_data.get("externalfiledirectory")
        external_path = cleaned_data.get("external_path")

        errors = []
        if dataset_file and external_path:
            errors.append("A file and an external path should not both be specified.")
        if dataset_file and externalfiledirectory:
            errors.append("A file and an external file directory should not both be specified.")

        if not (externalfiledirectory and external_path
                or not externalfiledirectory and not external_path):
            errors.append("Both external file directory and external path should be set or "
                          "neither should be set.")

        if errors:
            raise forms.ValidationError(errors)

    def _post_clean(self):
        """
        Special override for DatasetForm that doesn't validate the Dataset.
        """
        pass


class BulkDatasetUpdateForm(forms.Form):
    # dataset primary key
    id = forms.IntegerField(widget=forms.TextInput(attrs={'readonly': 'readonly'}), required=False)

    # dataset name
    name = forms.CharField(max_length=maxlengths.MAX_FILENAME_LENGTH, required=False)
    description = forms.CharField(widget=forms.Textarea, required=False)

    filesize = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'readonly': 'readonly',
        'class': 'display_only_input'
    }))

    md5 = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'readonly': 'readonly',
        'class': 'display_only_input'
    }))

    # The original name of the file uploaded by the user
    # Do not bother exposing the actual filename as it exists in the fileserver
    orig_filename = forms.CharField(widget=forms.TextInput(attrs={
        'readonly': 'readonly',
        'class': 'display_only_input'
    }), required=False)

    # Dataset instance
    # We don't use ModelForm because the formset.form.instance template property doesn't seem to work in django 1.6
    def __init__(self, *args, **kwargs):
        super(BulkDatasetUpdateForm, self).__init__(*args, **kwargs)
        self.dataset = Dataset()
        self.status = 0

    def update(self):
        if self.cleaned_data['id']:
            dataset = Dataset.objects.get(id=self.cleaned_data['id'])
            dataset.name = self.cleaned_data['name']
            dataset.description = self.cleaned_data['description']
            dataset.save()
            return dataset
        return None


class MultiFileField(forms.Field):
    """
    Django does not have a FileField that support selection of multiple files.
    This extends the FileField to allow multiple files.

    Make sure you assign this request.FILES.getlist[<name of MultiFileField>]
    instead of request.FILES[<name of MultiFileField>]
    """
    widget = ClearableFileInput(attrs={"multiple": "true"})
    default_error_messages = {
        'invalid': _("No file was submitted. Check the encoding type on the form."),
        'missing': _("No file was submitted."),
        'empty': _("The submitted file is empty."),
        'max_length': ungettext_lazy(
            'Ensure this filename has at most %(max)d character (it has %(length)d).',
            'Ensure this filename has at most %(max)d characters (it has %(length)d).',
            'max'),
        'contradiction': _('Please either submit a file or check the clear checkbox, not both.')
    }

    def __init__(self, *args, **kwargs):
        self.max_length = kwargs.pop('max_length', None)
        self.allow_empty_file = kwargs.pop('allow_empty_file', False)
        super(MultiFileField, self).__init__(*args, **kwargs)

    def clean(self, uploaded_file_list, initial=None):
        clean_data = []
        for upload_file in uploaded_file_list:
            filefield = forms.FileField(max_length=self.max_length,
                                        allow_empty_file=self.allow_empty_file)
            clean_data.append(filefield.clean(data=upload_file, initial=initial))
        return clean_data


class BaseMultiDatasetAddForm(metadata.forms.AccessControlForm):
    """
    Uploads multiple datasets at once.
    Appends the date and time to the name_prefix to make the dataset name unique.
    """

    name_prefix = forms.CharField(max_length=maxlengths.MAX_NAME_LENGTH, required=False,
                                  help_text="Prefix will be prepended with date and time to create unique " +
                                            "dataset name.")

    description = forms.CharField(widget=forms.Textarea, required=False,
                                  help_text="Description text that will be applied to all bulk added datasets. " +
                                            "If not supplied, a description will be autogenerated containing " +
                                            "the filename.")

    compound_datatype_choices = [DatasetForm.RAW_CDT_CHOICE]
    compound_datatype = forms.ChoiceField(choices=compound_datatype_choices)

    def __init__(self, data=None, files=None, user=None, *args, **kwargs):
        super(BaseMultiDatasetAddForm, self).__init__(data, files, *args, **kwargs)
        user_specific_choices = ([DatasetForm.RAW_CDT_CHOICE] +
                                 CompoundDatatype.choices(user))
        self.fields["compound_datatype"].choices = user_specific_choices


class BulkAddDatasetForm (BaseMultiDatasetAddForm):
    """
    Uploads multiple datasets at once.
    Appends the date and time to the name_prefix to make the dataset name unique.
    """
    # multiselect files
    dataset_files = MultiFileField(allow_empty_file="False",  max_length=4096,
                                   help_text="Select the files to upload. " +
                                   "Multiple files can be selected by shift-click or control-click.")

    def __init__(self, data=None, files=None, user=None, *args, **kwargs):
        super(BulkAddDatasetForm, self).__init__(data, files, user, *args, **kwargs)

        if files:
            # Form validation expects that items are in dict form.
            # Create a dict where the value is the list of files uploaded by the user.
            # If we don't do this, then only the first file in the list is assigned to dataset_files
            self.files = {"dataset_files": files.getlist("dataset_files")}

    def clean_dataset_files(self):
        """ Return a list of two tuples (filesize, file) from a list of file"""
        return [(f.size, f) for f in self.cleaned_data["dataset_files"]]

    def create_datasets(self, user):
        """
        Creates the Datasets and the corresponding SymbolicDatasets in same order as cleaned_data["dataset_files"].
        Will still save successful Datasets to database even if some of the Datasets fail to create.

        :return:  the compound datatype object, and a list of the created Dataset objects in the same order as
            cleaned_data["dataset_files"].
            If a Dataset failed to create, then the list element contains a dict that can be used
            to inform the user about the file.
        """
        compound_datatype_obj = None
        if self.cleaned_data['compound_datatype'] != CompoundDatatype.RAW_ID:
            compound_datatype_obj = CompoundDatatype.objects.get(pk=self.cleaned_data['compound_datatype'])

        results = []
        for file_size, uploaded_file in self.cleaned_data['dataset_files']:
            # Note that uploaded_file should be seek'd to the beginning.  It was presumably
            # just opened so that should be OK but if this ever changes we will have to fix this.
            dataset = error_str = auto_name = None
            try:
                # TODO:  use correct unique constraints
                name_prefix = ""
                if self.cleaned_data["name_prefix"]:
                    name_prefix = self.cleaned_data["name_prefix"] + "_"
                auto_name = name_prefix + uploaded_file.name + "_" + datetime.now().strftime('%Y%m%d%H%M%S%f')

                if self.cleaned_data["description"]:
                    auto_description = self.cleaned_data["description"]
                else:
                    auto_description = "Bulk Uploaded File " + uploaded_file.name

                dataset = Dataset.create_dataset(
                    is_uploaded=True,
                    file_path=None,
                    user=user,
                    cdt=compound_datatype_obj,
                    keep_file=True,
                    name=auto_name,
                    description=auto_description,
                    file_source=None,
                    check=True,
                    file_handle=uploaded_file
                )
                dataset.grant_from_json(self.cleaned_data["permissions"])

            except Exception as e:
                error_str = str(e)
                LOGGER.exception("Error while creating Dataset for file with original file name=" +
                                 str(uploaded_file.name) +
                                 " and autogenerated Dataset name = " +
                                 str(auto_name))

            if dataset and error_str is None:
                results.append(dataset)
            elif error_str and dataset is None:
                results.append({"name": uploaded_file.name,
                                "errstr": error_str,
                                "size": file_size})
            else:
                raise ValueError("Invalid situation.  Must either have a dataset or error.  Can not have both or none.")

        return compound_datatype_obj, results


class ArchiveAddDatasetForm(metadata.forms.AccessControlForm):
    """
    Uploads multiple datasets at once.
    Appends the date and time to the name_prefix to make the dataset name unique.
    """
    # TODO: There's duplicated code between this class and the BulkAddDatasetForm. Refactor: Pull out common code to a
    # new class
    name_prefix = forms.CharField(max_length=maxlengths.MAX_NAME_LENGTH, required=False,
                                  help_text="Prefix will be prepended with date and time to create unique dataset " +
                                            "name.")

    description = forms.CharField(widget=forms.Textarea, required=False,
                                  help_text="Description text that will be applied to all added datasets. " +
                                            "If not supplied, a description will be autogenerated containing the " +
                                            "filename.")

    dataset_file = forms.FileField(allow_empty_file="False",  max_length=maxlengths.MAX_FILENAME_LENGTH,
                                   label='Archive file',
                                   help_text="A single Zip file or an optionally compressed Tar file " +
                                   "containing the datasets you want to upload.")

    compound_datatype_choices = [DatasetForm.RAW_CDT_CHOICE]
    compound_datatype = forms.ChoiceField(choices=compound_datatype_choices,
                                          help_text="All files added will be of the same data type " +
                                          "specified. Files of any other type will be ignored.")

    def __init__(self, data=None, files=None, user=None, *args, **kwargs):
        super(ArchiveAddDatasetForm, self).__init__(data, files, *args, **kwargs)

        user_specific_choices = ([DatasetForm.RAW_CDT_CHOICE] +
                                 CompoundDatatype.choices(user))
        self.fields["compound_datatype"].choices = user_specific_choices

    def clean_dataset_file(self):
        """Perform the cleaning of the dataset_file (the archive file specified by the user).
        This method returns information about the files in the archive in form a
        2-tuple (filesize, stream).

        The returned list will be accessible from the cleaned_data directory.
        """
        # First try to unzip the archive
        try:
            archive = ZipFile(self.cleaned_data["dataset_file"], allowZip64=True)

            def get_filestream(filename):
                f = archive.open(filename)
                size = archive.getinfo(filename).file_size
                streamable = six.BytesIO(f.read())
                streamable.name = f.name.replace('/', '_')
#                streamable.name = f.name.split('/')[-1]
                f.close()
                return size, streamable

            def should_include(filename):
                # Bail on directories
                if filename.endswith("/"):
                    return False

                # And on hidden files
                if filename.split("/")[-1].startswith("."):
                    return False

                return True

            files = [get_filestream(file_name) for file_name in archive.namelist() if should_include(file_name)]

        except zipfile.BadZipfile:
            # Bad zip? Try tar why not
            try:
                self.cleaned_data["dataset_file"].seek(0)  # Reset the file so we can read it again
                archive = tarfile.open(name=None, mode='r', fileobj=self.cleaned_data["dataset_file"])

                def get_filestream(archive_member):
                    xfile = archive.extractfile(archive_member)
                    if xfile is not None:
                        xfile.name = xfile.name[2:].replace('/', '_')
                        return xfile.size, xfile
                    else:
                        return 0, None

                def should_include(name):
                    name = name[2:]
                    if name.endswith("/"):
                        return False

                    # And on hidden files
                    if name.split("/")[-1].startswith("."):
                        return False
                    return True

                files = [get_filestream(member) for member in archive.getmembers() if should_include(member.name)]
                files = filter(lambda x: x is not None, files)

            except tarfile.TarError:
                raise forms.ValidationError(_('Not a valid archive file. We currently accept Zip and Tar files.'),
                                            code='invalid')
        return files

    def create_datasets(self, user):
        """
        Creates the Datasets and the corresponding SymbolicDatasets in same order as cleaned_data["dataset_files"].
        Will still save successful Datasets to database even if some of the Datasets fail to create.

        :return:  CDT object and a list of the created Dataset objects in the same order
            as cleaned_data["dataset_files"].
            If particular Dataset failed to create, then the list element contains a dict that can be
        used to inform the user about the file.
        """
        compound_datatype_obj = None
        if self.cleaned_data['compound_datatype'] != CompoundDatatype.RAW_ID:
            compound_datatype_obj = CompoundDatatype.objects.get(pk=self.cleaned_data['compound_datatype'])

        results = []
        for file_size, uploaded_file in self.cleaned_data['dataset_file']:
            # Note that uploaded_file should be seek'd to the beginning.  It was presumably
            # just opened so that should be OK but if this ever changes we will have to fix this.
            dataset = error_str = auto_name = None
            try:
                # TODO:  use correct unique constraints
                name_prefix = ""
                if self.cleaned_data["name_prefix"]:
                    name_prefix = self.cleaned_data["name_prefix"] + "_"
                auto_name = name_prefix + uploaded_file.name + "_" + datetime.now().strftime('%Y%m%d%H%M%S%f')

                if self.cleaned_data["description"]:
                    auto_description = self.cleaned_data["description"]
                else:
                    auto_description = "Bulk Uploaded File " + uploaded_file.name

                dataset = Dataset.create_dataset(
                    is_uploaded=True,
                    file_path=None,
                    user=user,
                    cdt=compound_datatype_obj,
                    keep_file=True,
                    name=auto_name,
                    description=auto_description,
                    file_source=None,
                    check=True,
                    file_handle=uploaded_file
                )
                dataset.grant_from_json(self.cleaned_data["permissions"])

            except Exception as e:
                error_str = str(e)
                LOGGER.exception("Error while creating Dataset for file with original file name=" +
                                 str(uploaded_file.name) +
                                 " and autogenerated Dataset name = " +
                                 str(auto_name))

            if dataset and error_str is None:
                results.append(dataset)
            elif error_str and dataset is None:
                results.append({"name": uploaded_file.name,
                                "errstr": error_str,
                                "size": file_size})
            else:
                raise ValueError("Invalid situation.  Must either have a dataset or error.  Can not have both or none.")

        return compound_datatype_obj, results
