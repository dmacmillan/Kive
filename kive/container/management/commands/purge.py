import logging
import os
import shutil
from argparse import ArgumentDefaultsHelpFormatter
from collections import Counter
from datetime import timedelta, datetime
from itertools import chain

from django.contrib.humanize.templatetags.humanize import naturaltime
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import models
from django.db.models.aggregates import Sum
from django.db.models.expressions import Value, F
from django.db.models.functions import Now

from django.template.defaultfilters import filesizeformat, pluralize
from django.utils import timezone
from django.utils.dateparse import parse_duration

from container.models import ContainerRun
from librarian.models import Dataset
from portal.models import parse_file_size

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Scan through storage files, recording the size of new files, ' \
           'and purging old files if needed.'

    def add_arguments(self, parser):
        parser.formatter_class = ArgumentDefaultsHelpFormatter

        parser.add_argument('--start',
                            help='How much storage triggers a purge?',
                            default=settings.PURGE_START,
                            type=parse_file_size)
        parser.add_argument('--stop',
                            help='How much storage stops a purge?',
                            default=settings.PURGE_STOP,
                            type=parse_file_size)
        parser.add_argument('--dataset_aging',
                            help='How fast do datasets age, '
                                 'compared to other storage?',
                            default=settings.PURGE_DATASET_AGING,
                            type=float)
        parser.add_argument('--log_aging',
                            help='How fast do log files age, '
                                 'compared to other storage?',
                            default=settings.PURGE_LOG_AGING,
                            type=float)
        parser.add_argument('--sandbox_aging',
                            help='How fast do container sandboxes age, '
                                 'compared to other storage?',
                            default=settings.PURGE_SANDBOX_AGING,
                            type=float)
        parser.add_argument("--synch",
                            help="Synchronize the database and file system by "
                                 "purging any sandboxes, datasets, or log "
                                 "files that don't have a matching entry in "
                                 "the database. Skips the regular purging.",
                            action="store_true")
        parser.add_argument("--wait",
                            help="How long to wait before purging "
                                 "unsynchronized files.",
                            default=settings.PURGE_WAIT,
                            type=parse_duration)
        parser.add_argument("--batch_size",
                            help="Number of files to check at a time.",
                            default=settings.PURGE_BATCH_SIZE,
                            type=int)

    def handle(self,
               start=2000,
               stop=1000,
               dataset_aging=1.0,
               log_aging=1.0,
               sandbox_aging=1.0,
               synch=False,
               wait=timedelta(seconds=0),
               batch_size=100,
               **kwargs):
        if synch:
            # noinspection PyTypeChecker
            self.synch_model(ContainerRun, 'sandbox_path', wait, batch_size)
            # noinspection PyTypeChecker
            self.synch_model(Dataset, 'dataset_file', wait, batch_size)
            Dataset.external_file_check(batch_size=batch_size)
        else:
            runs_to_size = ContainerRun.objects.filter(
                end_time__isnull=False,
                sandbox_size__isnull=True,
                sandbox_purged=False).exclude(sandbox_path='')
            for run in runs_to_size:
                run.set_sandbox_size()
            sandbox_total = ContainerRun.objects.filter(
                sandbox_purged=False).aggregate(
                Sum('sandbox_size'))['sandbox_size__sum'] or 0

            Dataset.set_dataset_sizes()
            dataset_total = Dataset.objects.exclude(
                dataset_file='').exclude(  # Already purged.
                dataset_file=None).aggregate(  # External file.
                Sum('dataset_size'))['dataset_size__sum'] or 0

            total_storage = remaining_storage = sandbox_total + dataset_total
            if total_storage <= start:
                logger.debug(u"Nothing was purged.")
                return

            sandbox_ages = ContainerRun.find_unneeded().annotate(
                type=Value('r', models.CharField()),
                age=sandbox_aging * (Now() - F('end_time'))).values_list(
                'type',
                'id',
                'age')

            dataset_ages = Dataset.find_unneeded().annotate(
                type=Value('d', models.CharField()),
                age=dataset_aging * (Now() - F('date_created'))).values_list(
                'type',
                'id',
                'age')

            purge_counts = Counter()
            max_purge_dates = {}
            min_purge_dates = {}
            purge_entries = sandbox_ages.union(dataset_ages,
                                               all=True).order_by('-age')
            while remaining_storage > stop:
                entry_count = 0
                for entry_type, entry_id, age in purge_entries[:batch_size]:
                    entry_count += 1
                    if entry_type == 'r':
                        run = ContainerRun.objects.get(id=entry_id)
                        entry_size = run.sandbox_size
                        sandbox_total -= run.sandbox_size
                        entry_date = run.end_time
                        logger.debug("Purged container run %d containing %s.",
                                     run.pk,
                                     filesizeformat(entry_size))
                        try:
                            run.delete_sandbox()
                        except OSError:
                            logger.error(u"Failed to purge container run %d at %r.",
                                         run.id,
                                         run.sandbox_path,
                                         exc_info=True)
                        run.sandbox_purged = True  # Don't try to purge it again.
                        run.save()
                    else:
                        assert entry_type == 'd'
                        dataset = Dataset.objects.get(id=entry_id)
                        entry_size = dataset.dataset_size
                        dataset_total -= dataset.dataset_size
                        entry_date = dataset.date_created
                        logger.debug("Purged dataset %d containing %s.",
                                     dataset.pk,
                                     filesizeformat(entry_size))
                        dataset.dataset_file.delete()
                    purge_counts[entry_type] += 1
                    purge_counts[entry_type + ' bytes'] += entry_size
                    min_purge_dates[entry_type] = min(entry_date,
                                                      min_purge_dates.get(entry_type, entry_date))
                    max_purge_dates[entry_type] = max(entry_date,
                                                      max_purge_dates.get(entry_type, entry_date))
                    remaining_storage -= entry_size
                    if remaining_storage <= stop:
                        break
                if entry_count == 0:
                    break
            for entry_type, entry_name in (('r', 'container run'), ('d', 'dataset')):
                purged_count = purge_counts[entry_type]
                if not purged_count:
                    continue
                min_purge_date = min_purge_dates[entry_type]
                max_purge_date = max_purge_dates[entry_type]
                collective = entry_name + pluralize(purged_count)
                bytes_removed = purge_counts[entry_type + ' bytes']
                logger.info("Purged %d %s containing %s from %s to %s.",
                            purged_count,
                            collective,
                            filesizeformat(bytes_removed),
                            naturaltime(min_purge_date),
                            naturaltime(max_purge_date))
            if remaining_storage > stop:
                remainders = []
                for size, label in [(sandbox_total, 'container runs'),
                                    (dataset_total, 'datasets')]:
                    if size:
                        remainders.append(u'{} of {}'.format(
                            filesizeformat(size),
                            label))
                logger.error('Cannot reduce storage to %s: %s.',
                             filesizeformat(stop),
                             ', '.join(remainders))

    def synch_model(self, model, path_field_name, wait, batch_size):
        file_names = set()
        total_files = total_bytes = 0
        for file_name in chain(model.scan_file_names(), [None]):
            if file_name is not None:
                file_names.add(file_name)
            if len(file_names) >= batch_size or file_name is None:
                files_removed, bytes_removed = self.synch_model_files(
                    model,
                    path_field_name,
                    file_names,
                    wait)
                total_files += files_removed
                total_bytes += bytes_removed
                file_names.clear()
        if total_files:
            # noinspection PyProtectedMember
            logger.error(
                'Purged %d unregistered %s file%s containing %s.',
                total_files,
                model._meta.verbose_name,
                pluralize(total_files),
                filesizeformat(total_bytes))

    def synch_model_files(self, model, path_field_name, file_names, wait):
        remove_older_than = timezone.now() - wait
        values_list = model.objects.filter(
            **{path_field_name+'__in': file_names}).values_list(path_field_name)
        found_file_names = {file_name for file_name, in values_list}
        unknown_file_names = file_names - found_file_names
        bytes_removed = files_removed = 0
        for file_name in sorted(unknown_file_names):
            file_path = os.path.join(settings.MEDIA_ROOT, file_name)
            file_stat = os.stat(file_path)
            modification_time = datetime.fromtimestamp(
                file_stat.st_mtime,
                timezone.get_current_timezone())
            if modification_time > remove_older_than:
                continue
            if os.path.isdir(file_path):
                file_size = 0
                for child_path, _, content_names in os.walk(file_path):
                    for content_name in content_names:
                        content_path = os.path.join(child_path, content_name)
                        file_size += os.stat(content_path).st_size
                shutil.rmtree(file_path)
            else:
                file_size = file_stat.st_size
                os.remove(file_path)
            logger.warning(
                'Purged unregistered file %r containing %s.',
                file_name.encode('ascii'),
                filesizeformat(file_size))
            files_removed += 1
            bytes_removed += file_size
        return files_removed, bytes_removed
