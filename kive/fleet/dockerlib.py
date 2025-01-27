# a low level interface to docker via the 'docker' command via Popen

import logging
import os
# noinspection PyProtectedMember
from pipes import quote
import stat
import tempfile
import subprocess as sp

from django.conf import settings

from fleet.slurmlib import multi_check_output


logger = logging.getLogger("fleet.dockerlib")


DOCKER_COMMAND = settings.DOCK_DOCKER_COMMAND
BZIP2_COMMAND = settings.DOCK_BZIP2_COMMAND

# CHECK_OUTPUT = sp.check_output
CHECK_OUTPUT = multi_check_output


class BaseDockerHandler(object):
    """A class that interfaces to the docker run time"""

    # These are strings used in the keys of dicts returned by docker_images
    DOCKER_IMG_REPO_TAG = "REPOSITORY:TAG"
    DOCKER_IMG_IMAGE_ID = "IMAGE ID"
    DOCKER_IMG_CREATED_AT = "CREATED AT"
    DOCKER_IMG_CREATED_SINCE = "CREATED"
    DOCKER_IMG_SIZE = "SIZE"
    DOCKER_IMG_DIGEST = "DIGEST"
    DOCKER_IMG_SET = frozenset([DOCKER_IMG_REPO_TAG, DOCKER_IMG_IMAGE_ID,
                                DOCKER_IMG_CREATED_AT, DOCKER_IMG_CREATED_SINCE,
                                DOCKER_IMG_SIZE, DOCKER_IMG_DIGEST])

    @classmethod
    def docker_is_alive(cls):
        """Return True if the docker configuration is adequate for Kive's purposes."""
        raise NotImplementedError

    @classmethod
    def docker_ident(cls):
        """Return a string with some pertinent information about the docker configuration."""
        raise NotImplementedError

    @staticmethod
    def docker_images(repotag_name=None):
        """Perform a 'docker images' command with an optional repotag name.
        If none is provided, information about all images is returned.
        Return a possibly empty list of dicts on success.
        The keys of the dicts are defined as DOCKER_IMG_* above.
        The values are all strings as returned by the docker images command.
        """
        raise NotImplementedError

    @classmethod
    def generate_launch_args(cls,
                             host_step_dir,
                             input_file_paths,
                             output_file_paths,
                             driver_name,
                             dependency_paths,
                             image_id,
                             container_file=None):
        """ Generate the launch command for a method.

        :param host_step_dir: step folder under sandbox folder on host computer
        :param input_file_paths: list of input file paths on host computer
        :param output_file_paths: list of output file paths on host computer
        :param driver_name: file name of driver script that will be saved in
            the sandbox folder, or None to use the image's entry point.
        :param dependency_paths: relative paths of dependency files that will
            be saved under the sandbox folder
        :param image_id: docker image id
        :param container_file: singularity container file name
        """
        raise NotImplementedError


class DummyDockerHandler(BaseDockerHandler):
    """A dummy docker handler that does not requires docker to be installed"""
    _is_alive = False

    @classmethod
    def docker_is_alive(cls):
        if not cls._is_alive:
            cls._is_alive = True
        return cls._is_alive

    @classmethod
    def docker_ident(cls):
        if not cls._is_alive:
            raise RuntimeError("Must call docker_is_alive before docker_ident")
        return "DummyDockerHandler"

    @staticmethod
    def docker_images(repotag_name=None):
        return []

    @classmethod
    def generate_launch_args(cls,
                             host_step_dir,
                             input_file_paths,
                             output_file_paths,
                             driver_name,
                             dependency_paths,
                             image_id,
                             container_file=None):
        """ Generate the launch command for a method.

        This version just launches it on the host computer.
        :param host_step_dir: step folder under sandbox folder on host computer
        :param input_file_paths: list of input file paths on host computer
        :param output_file_paths: list of output file paths on host computer
        :param driver_name: file name of driver script that will be saved in
            the sandbox folder, or None to use the image's entry point.
        :param dependency_paths: relative paths of dependency files that will
            be saved under the sandbox folder
        :param image_id: docker image id
        :param container_file: singularity container file name
        """
        if driver_name is None:
            raise NotImplementedError()
        wrapper_template = """\
#! /usr/bin/env bash
cd {}
{} {}
"""
        wrapper = wrapper_template.format(
            quote(host_step_dir),
            quote(os.path.join(host_step_dir, driver_name)),
            " ".join(quote(name)
                     for name in input_file_paths + output_file_paths))
        with tempfile.NamedTemporaryFile('w',
                                         prefix=driver_name,
                                         suffix='.sh',
                                         dir=host_step_dir,
                                         delete=False) as wrapper_file:
            wrapper_file.write(wrapper)
            mode = os.fstat(wrapper_file.fileno()).st_mode
            mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH  # execute bits
            os.fchmod(wrapper_file.fileno(), stat.S_IMODE(mode))

        return [wrapper_file.name]


class DockerHandler(BaseDockerHandler):

    _is_alive = False
    docker_wrap_path = None

    @staticmethod
    def _run_shell_command(cmd_lst):
        """ Helper routine:
        Call a shell command provided in cmd_lst
        Any exception encountered will be raised, otherwise the resulting
        stdout is returned.
        """
        assert isinstance(cmd_lst, list), "list expected"
        logger.debug(" ".join(cmd_lst))
        out_str = None
        stderr_fd, stderr_path = tempfile.mkstemp()
        try:
            with os.fdopen(stderr_fd, "w") as f:
                out_str = CHECK_OUTPUT(cmd_lst, stderr=f)
        except OSError:
            # typically happens if the executable cannot execute at all (e.g. not installed)
            status_report = "failed to execute '%s'" % " ".join(cmd_lst)
            logger.warning(status_report, exc_info=True)
            raise
        except sp.CalledProcessError as e:
            # typically happens if the executable did run, but returned an error
            status_report = "%s returned an error code '%s'\n\nCommand list:\n%s\n\nOutput:\n%s\n\n%s"
            try:
                with open(stderr_path, "r") as f:
                    stderr_str = "stderr:\n{}".format(f.read())
            except IOError as e:
                stderr_str = "The stderr log appears to have been lost!"
            logger.debug(status_report, cmd_lst[0], e.returncode, cmd_lst, e.output, stderr_str,
                         exc_info=True)
            raise
        finally:
            # Clean up the stderr log file.
            try:
                os.remove(stderr_path)
            except OSError:
                pass
        return out_str

    @staticmethod
    def _run_twopipe_command(cmd_lst1, cmd_lst2):
        """Run the equivalent of 'cmd1 | cmd2' and return the
        stdoutput of cmd 2.
        Raise an exception if an error occurs.
        """
        try:
            with open(os.devnull, 'w') as devnull:
                p1 = sp.Popen(cmd_lst1, stdout=sp.PIPE, stderr=devnull)
                return sp.check_output(cmd_lst2,
                                       stdin=p1.stdout,
                                       stderr=devnull, universal_newlines=True)
        except OSError as e:
            # Typically happens if the executable wasn't found.
            e.strerror += ': {} | {}'.format(" ".join(cmd_lst1), " ".join(cmd_lst2))
            raise

    @staticmethod
    def _run_shell_command_to_dict(cmd_lst, splitchar=None):
        out_str = DockerHandler._run_shell_command(cmd_lst)
        lns = [ln for ln in out_str.split('\n') if ln]
        logger.debug("read %d lines" % len(lns))
        nametup = tuple([s.strip() for s in lns[0].split(splitchar)])
        return [dict(zip(nametup, [s.strip() for s in ln.split(splitchar)])) for ln in lns[1:]]

    @staticmethod
    def check_is_alive():
        """Check that the docker configuration is adequate for Kive's purposes.
        This routine does not return anything; if it runs through without
        an exception, this is considered a pass.
        This is done by
        1) calling bzip2 and checking for exceptions. bzip2 is needed when loading
        a docker image from a file.
        2) calling 'docker version' and checking for exceptions.
        """
        for cmd_lst in [[BZIP2_COMMAND, '-h'],
                        ['sudo', '-n', DOCKER_COMMAND, 'version']]:
            DockerHandler._run_shell_command(cmd_lst)
            logger.debug("%s passed.", " ".join(cmd_lst))

    @classmethod
    def _load_image_from_file(cls, image_filename):
        """Load a docker image from file into the local docker repository.
        the image_filename is a string, typically the name of a bzipped2 tarfile ('*.tar.bz2').

        Upon success, this routine returns a dictionary, as returned from docker_images(),
        containing information about the loaded image.
        Upon failure, this routine raises an exception.
        """
        if not cls._is_alive:
            raise RuntimeError("Must call docker_is_alive before load_image_from_file")
        cmd1_lst = [BZIP2_COMMAND, "-dc", image_filename]
        cmd2_lst = [DOCKER_COMMAND, "load"]
        out_str = cls._run_twopipe_command(cmd1_lst, cmd2_lst)
        # upon success, the output string will be of the form:
        # Loaded image: scottycfenet/runkive-c7:latest
        # If this is the case, use the tag name to get the ID str and return the info dict
        success_pattern = 'Loaded image:'
        if not out_str.startswith(success_pattern):
            raise RuntimeError("Failed to load docker image, output is '{}'".format(out_str))
        return cls._get_image_info(out_str[len(success_pattern):].strip())

    @staticmethod
    def docker_images(repotag_name=None):
        """Perform a 'docker images' command with an optional repotag name.
        If none is provided, all images are returned by 'docker images'.
        Return a possibly empty list of dicts on success.
        The keys of the dicts are defined as DOCKER_IMG_* above.
        The values are all strings as returned by the docker images command.
        """
        fmt_str = """table {{.Repository}}:{{.Tag}}|{{.ID}}|\
        {{.CreatedAt}}|{{.CreatedSince}}|{{.Digest}}|{{.Size}}"""
        cmd_lst = ['sudo', '-n', DOCKER_COMMAND, "images", "--format", fmt_str]
        if repotag_name is not None:
            cmd_lst.append(repotag_name)
        return DockerHandler._run_shell_command_to_dict(cmd_lst, splitchar="|")

    @staticmethod
    def _get_image_info(repotag_name):
        res_lst = DockerHandler.docker_images(repotag_name)
        if len(res_lst) != 1:
            raise RuntimeError("Failed to find image {}".format(repotag_name))
        return res_lst[0]

    @classmethod
    def docker_is_alive(cls):
        if not cls._is_alive:
            cls.check_is_alive()
            # Find docker_wrap.py on path, to match permissions in sudoers.
            res_str = sp.check_output(['which', 'docker_wrap.py'], universal_newlines=True)
            cls.docker_wrap_path = res_str.strip()
            cls._is_alive = True
        return True

    @classmethod
    def docker_ident(cls):
        """Return a string with some pertinent information about the docker configuration."""
        if not cls._is_alive:
            raise RuntimeError("Must call docker_is_alive before docker_ident")
        return DockerHandler._run_shell_command(['sudo',
                                                 '-n',
                                                 DOCKER_COMMAND,
                                                 "version"])

    @classmethod
    def generate_launch_args(cls,
                             host_step_dir,
                             input_file_paths,
                             output_file_paths,
                             driver_name,
                             dependency_paths,
                             image_id,
                             container_file=None):
        """ Generate the launch command for the docker wrapper.

        :param host_step_dir: step folder under sandbox folder on host computer
        :param input_file_paths: list of input file paths on host computer
        :param output_file_paths: list of output file paths on host computer
        :param driver_name: file name of driver script that will be saved in
            the sandbox folder, or None to use the image's entry point.
        :param dependency_paths: relative paths of dependency files that will
            be saved under the sandbox folder
        :param image_id: docker image id
        :param container_file: singularity container file name
        """
        if not cls._is_alive:
            raise RuntimeError("Must call docker_is_alive before generate_launch_args")
        # we want to run the code in the docker container under a standardised directory,
        # so we convert the argument path names.
        docker_input_path = "/mnt/input"
        docker_output_path = "/mnt/output"
        host_input_path = os.path.join(host_step_dir, 'input_data')
        host_output_path = os.path.join(host_step_dir, 'output_data')
        in_args = [os.path.join(docker_input_path,
                                os.path.relpath(file_path, host_input_path))
                   for file_path in input_file_paths]
        out_args = [os.path.join(docker_output_path,
                                 os.path.relpath(file_path, host_output_path))
                    for file_path in output_file_paths]
        step_name = os.path.basename(host_step_dir)
        sandbox_path = os.path.dirname(host_step_dir)
        sandbox_name = os.path.basename(sandbox_path)
        session_id = sandbox_name + '.' + step_name
        bin_files = [] if driver_name is None else [driver_name]
        bin_files += dependency_paths
        bin_files = [os.path.join(host_step_dir, file_path) + ':' + file_path
                     for file_path in bin_files]
        args = [cls.docker_wrap_path,
                image_id,
                "--sudo",
                "--quiet"]
        if bin_files:
            args.append("--bin_files")
            args.extend(bin_files)
            args.append("--workdir")
            args.append("/mnt/bin")

        args.append("--inputs")
        args.extend(input_file_paths)
        args.append("--output")
        args.append(host_output_path)
        args.append("--")
        args.append(session_id)
        if driver_name is not None:
            args.append(os.path.join("/mnt/bin", driver_name))
        args.extend(in_args)
        args.extend(out_args)
        return args


SINGULARITY_COMMAND = 'singularity'


class SingularityDockerHandler(DockerHandler):
    """A Docker Handler that uses singularity to launch docker containers"""

    _is_alive = False
    singularity_cmd_path = None
    sing_version = None

    @classmethod
    def docker_is_alive(cls):
        """Make sure the docker/singularity environment is properly set up.
        a) run the normal docker tests (docker and bzip2 executables). We do NOT need docker_wrap.py
        b) check for existence of singularity executable; determine its path and version
        """
        if not cls._is_alive:
            cls.check_is_alive()
            # singularity existence and version
            cmd_lst = [SINGULARITY_COMMAND, '--version']
            cls.sing_version = DockerHandler._run_shell_command(cmd_lst)
            logger.debug("%s passed.", " ".join(cmd_lst))
            res_str = sp.check_output(['which', SINGULARITY_COMMAND], universal_newlines=True)
            cls.singularity_cmd_path = res_str.strip()
            if cls.singularity_cmd_path is None:
                raise RuntimeError('Cannot determine singularity command path')
            logger.debug("singularity command path: '{}'".format(cls.singularity_cmd_path))
            cls._is_alive = True
        return True

    @classmethod
    def docker_ident(cls):
        """Return a string with some pertinent information about the docker configuration."""
        if not cls._is_alive:
            raise RuntimeError("Must call docker_is_alive before docker_ident")
        dock_str = DockerHandler._run_shell_command(['sudo',
                                                     '-n',
                                                     DOCKER_COMMAND,
                                                     "version"])
        return "Singularity version {}\n{}".format(cls.sing_version, dock_str)

    @classmethod
    def generate_launch_args(cls,
                             host_step_dir,
                             input_file_paths,
                             output_file_paths,
                             driver_name,
                             dependency_paths,
                             image_id,
                             container_file=None):
        """ Generate the launch command for a method.

        This version uses 'singularity exec' or 'singularity run' to run the driver
        within a provided singularity container.

        :param host_step_dir: step folder under sandbox folder on host computer
        :param input_file_paths: list of input file paths on host computer
        :param output_file_paths: list of output file paths on host computer
        :param driver_name: file name of driver script that will be saved in
            the sandbox folder, or None to use the image's entry point.
        :param dependency_paths: relative paths of dependency files that will
            be saved under the sandbox folder
        :param image_id: docker image id
        :param container_file: singularity container file name
        """
        if not cls._is_alive:
            raise RuntimeError("Must call docker_is_alive before generate_launch_args")
        if cls.singularity_cmd_path is None:
            raise RuntimeError('Cannot determine singularity command path')
        if container_file is None:
            raise RuntimeError('No singularity container selected, is '
                               'DEFAULT_CONTAINER configured?')
        if not os.path.exists(container_file):
            raise RuntimeError('Container file not found: ' + container_file)
        # we want to run the code in the docker container under a standardised directory,
        # so we convert the argument path names.
        docker_input_path = "/mnt/input"
        docker_output_path = "/mnt/output"
        docker_bin_path = "/mnt/bin"
        host_input_path = os.path.join(host_step_dir, 'input_data')
        host_output_path = os.path.join(host_step_dir, 'output_data')
        docker_in_args = [os.path.join(docker_input_path,
                                       os.path.relpath(file_path, host_input_path))
                          for file_path in input_file_paths]
        docker_out_args = [os.path.join(docker_output_path,
                                        os.path.relpath(file_path, host_output_path))
                           for file_path in output_file_paths]

        # NOTE: the driver program is run in the /mnt/bin directory in the container.
        # some drivers will want to write temporary files, therefore we must mount
        # it rw.
        opt_string = "--contain -B {}:{}:rw,{}:{}:rw,{}:{}:rw --pwd {}".format(
            host_input_path,
            docker_input_path,
            host_output_path,
            docker_output_path,
            host_step_dir,
            docker_bin_path,
            docker_bin_path)
        # NOTE: if the driver_name is given, we must use 'singularity exec' otherwise
        # 'singularity run' (which runs the image's entry point
        if driver_name is None:
            launch_command = "{} run {} {}".format(cls.singularity_cmd_path,
                                                   opt_string,
                                                   container_file)
            prefix = 'launch'
        else:
            launch_command = "{} exec {} {} ./{}".format(cls.singularity_cmd_path,
                                                         opt_string,
                                                         container_file,
                                                         driver_name)
            prefix = driver_name
        wrapper_template = """\
#!/usr/bin/env bash
cd {}
{} {}
"""
        arg_string = " ".join((quote(name) for name in docker_in_args + docker_out_args))
        wrapper = wrapper_template.format(
            quote(host_step_dir),
            launch_command, arg_string)
        with tempfile.NamedTemporaryFile('w',
                                         prefix=prefix,
                                         suffix='.sh',
                                         dir=host_step_dir,
                                         delete=False) as wrapper_file:
            wrapper_file.write(wrapper)
            mode = os.fstat(wrapper_file.fileno()).st_mode
            mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH  # execute bits
            os.fchmod(wrapper_file.fileno(), stat.S_IMODE(mode))

        return [wrapper_file.name]
