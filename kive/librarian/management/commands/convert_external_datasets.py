from __future__ import print_function

import re
from argparse import ArgumentDefaultsHelpFormatter
from glob import glob

from django.core.management.base import BaseCommand

import os

import file_access_utils
from librarian.models import ExternalFileDirectory, Dataset


class Command(BaseCommand):
    help = 'Converts uploaded datasets to external datasets.'

    def add_arguments(self, parser):
        parser.formatter_class = ArgumentDefaultsHelpFormatter
        parser.add_argument("-x",
                            "--extension",
                            help="file name extension",
                            default=".fastq.gz")
        parser.add_argument("-p",
                            "--pattern",
                            help="description pattern",
                            default=r'.* from MiSeq run (.*)$')
        parser.add_argument("-d",
                            "--dry_run",
                            action='store_true',
                            help="don't make any changes")
        parser.add_argument(
            "-t",
            "--template",
            help="folder template",
            default=r'~/data/RAW_DATA/MiSeq/runs/\1*/Data/Intensities/BaseCalls')

    def handle(self, *args, **options):
        description_pattern = options['pattern']
        folder_template = options['template']
        external_directories = ExternalFileDirectory.objects.order_by('-path')
        missing_folders = set()
        changed_files = set()

        datasets = Dataset.objects.filter(
            externalfiledirectory__isnull=True,  # not already external
            file_source__isnull=True,  # not an output
            usurps__isnull=True,  # not generated by MD5 conflict
            name__endswith=options['extension'])
        print('Uploaded datasets:', datasets.count())
        for dataset in datasets:
            match = re.match(description_pattern, dataset.description)
            if not match:
                print('No pattern match:', dataset.description)
                continue
            folder_path = os.path.expanduser(match.expand(folder_template))
            expected_path = os.path.join(folder_path, dataset.name)
            files = glob(expected_path)
            if len(files) == 0:
                if len(glob(folder_path)) == 0:
                    missing_folders.add(folder_path)
                else:
                    print('Missing file:', expected_path)
                continue
            if len(files) > 1:
                print('Too many matches:', expected_path)
                continue
            found_file, = files
            for external_directory in external_directories:
                if found_file.startswith(external_directory.path):
                    file_path = os.path.relpath(found_file,
                                                external_directory.path)
                    if not self.is_md5_changed(dataset,
                                               found_file,
                                               changed_files):
                        if not options['dry_run']:
                            dataset.externalfiledirectory = external_directory
                            dataset.external_path = file_path
                            dataset.dataset_file.delete(save=True)
                        print('.', end='')
                    break
            else:
                print('Not under any external directory:', expected_path)
        for folder in sorted(missing_folders):
            print('Missing folder:', folder)

    def is_md5_changed(self, dataset, found_file, changed_files):
        old_md5 = dataset.MD5_checksum
        with open(found_file, "rb") as f:
            new_md5 = file_access_utils.compute_md5(f)
        is_changed = new_md5 != old_md5
        if is_changed:
            if found_file not in changed_files:
                print('MD5 changed:',
                      old_md5,
                      'to',
                      new_md5,
                      found_file)
                changed_files.add(found_file)
        return is_changed