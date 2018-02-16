import shutil
from argparse import ArgumentParser, FileType, ArgumentDefaultsHelpFormatter
import errno
from collections import namedtuple
from functools import partial
from glob import glob
from logging import basicConfig, getLogger, DEBUG
import os
from multiprocessing import Pool
from random import shuffle
from subprocess import check_output, STDOUT, CalledProcessError

from itertools import islice

import signal

basicConfig(level=DEBUG,
            format="%(asctime)s[%(levelname)s]%(name)s:%(message)s")
logger = getLogger(__name__)


def parse_args():
    parser = ArgumentParser(
        description='Try copying large files and waiting for completion.',
        formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument('source_pattern',
                        help='source files to copy (wildcards allowed)')
    parser.add_argument('target_dir')
    parser.add_argument('--min_size',
                        type=int,
                        default=0,
                        help='Minimum size in MB')
    parser.add_argument('--max_size',
                        type=int,
                        default=1000000,
                        help='Maximum size in MB')
    parser.add_argument('-n',
                        '--num_files',
                        type=int,
                        default=100,
                        help='Number of files to copy')
    parser.add_argument('-p',
                        '--processes',
                        type=int,
                        default=16,
                        help='Number processes to run at the same time')
    parser.add_argument('--plot',
                        type=FileType('w'),
                        help='file name to plot file sizes instead of copying')
    return parser.parse_args()


SourceFile = namedtuple('SourceFile', 'path size is_link')


def find_files(source_pattern,
               min_size,
               max_size):
    file_names = glob(source_pattern)
    shuffle(file_names)
    logger.info('Found %d source files.', len(file_names))
    for i, file_name in enumerate(file_names):
        if i % 1000 == 0:
            logger.debug('Scanned %d files.', i)
        is_link = os.path.islink(file_name)
        file_size = os.path.getsize(file_name)
        if min_size <= file_size <= max_size:
            yield SourceFile(file_name, file_size, is_link)
    logger.debug('Finished scanning.')


def copy_file(args, file_info):
    file_number, source_file = file_info
    try:
        logger.debug('%s, %s', source_file.is_link, source_file.path)
        file_name = os.path.basename(source_file.path)
        file_name = '{:04}-{}'.format(file_number, file_name)
        target_file = os.path.join(args.target_dir, file_name)
        shutil.copyfile(source_file.path, target_file)
        python_source = """\
from gzip import GzipFile
with GzipFile('/mnt/input/in.fastq.gz') as f:
    i = 0
    for i, line in enumerate(f):
        pass
    print(i, 'lines')
"""
        try:
            report = check_output(["docker_wrap.py",
                                   "python:3",
                                   "--sudo",
                                   "--quiet",
                                   "--inputs",
                                   target_file + ":in.fastq.gz",
                                   "--",
                                   file_name,
                                   "python",
                                   "-c",
                                   python_source],
                                  stderr=STDOUT)
            report = report.strip() + ' ' + file_name
            # sleep(20)
            # with GzipFile(target_file) as zip_file:
            #     while True:
            #         chunk = zip_file.read(1024)
            #         if chunk == '':
            #             break
        except CalledProcessError as ex:
            report = 'Copy failed for ' + source_file.path + '\n'
            report += ex.output
        os.remove(target_file)
        return report
    except Exception:
        logger.error('Copy failed.', exc_info=True)
        raise


def init_worker():
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def main():
    args = parse_args()
    logger.info('Scanning %r.', args.source_pattern)
    # noinspection PyBroadException
    try:
        try:
            os.makedirs(args.target_dir)
        except OSError as ex:
            if ex.errno != errno.EEXIST:
                raise
        megabyte_size = 1024*1024
        source_files = find_files(args.source_pattern,
                                  args.min_size * megabyte_size,
                                  args.max_size * megabyte_size)
        if args.plot is not None:
            import pandas as pd
            import seaborn as sns

            source_data = pd.DataFrame(source_files)
            ax = sns.distplot(source_data['size'],
                              kde_kws=dict(cut=0))
            figure = ax.get_figure()
            figure.savefig(args.plot)
            logger.info('Plotted %d files.', source_data.size)
        else:
            pool = Pool(args.processes, init_worker)
            copy_func = partial(copy_file, args)
            for report in pool.imap_unordered(copy_func,
                                              enumerate(islice(source_files,
                                                               args.num_files))):
                logger.debug(report)

        logger.info('Done.')
    except Exception:
        logger.error('Failed.', exc_info=True)


main()
