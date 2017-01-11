
# a low level interface to slurm using the calls to sbatch, scancel and sacct via Popen.

import os.path
import logging

import multiprocessing as mp
import Queue
import re
import subprocess as sp
from datetime import datetime


logger = logging.getLogger("fleet.slurmlib")


class SlurmJobHandle:
    def __init__(self, job_id, slurm_sched_class):
        assert isinstance(job_id, str), "job_id must be a string!"
        self.job_id = job_id
        self.slurm_sched_class = slurm_sched_class

    def get_state(self):
        """ Get the current state of this job.
        The 'jobstate': value can be one of the predefined constants
        defined in SlurmScheduler:

        NOTE: If you want the states of many jobhandles at the same time, it is more
        efficient to use SlurmScheduler.get_accounting_info() directly.
        """
        rdct = self.slurm_sched_class.get_accounting_info([self])[self.job_id]
        return rdct[BaseSlurmScheduler.ACC_STATE]

    def __str__(self):
        return "slurm job_id {}".format(self.job_id)


class BaseSlurmScheduler:
    # All possible run states we expose to the outside. In fact, these are states as
    # reported by sacct.
    # These states will be reported by SlurmJobHandle.getstate() and
    # SlurmScheduler.get_accounting_info()
    # RUNNING, RESIZING, SUSPENDED, COMPLETED, CANCELLED, FAILED, TIMEOUT,
    # PREEMPTED, BOOT_FAIL, DEADLINE or NODE_FAIL
    BOOT_FAIL = "BOOT_FAIL"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    # CONFIGURING = "CONFIGURING"
    COMPLETING = "COMPLETING"
    DEADLINE = "DEADLINE"
    FAILED = "FAILED"
    NODE_FAIL = "NODE_FAIL"
    # PENDING = "PENDING"
    PREEMPTED = "PREEMPTED"
    RUNNING = "RUNNING"
    RESIZING = "RESIZING"
    SUSPENDED = "SUSPENDED"
    TIMEOUT = "TIMEOUT"
    PENDING = "PENDING"
    WAITING = 'WAITING'
    # include an unknown state (no accounting information available)
    UNKNOWN = 'UNKNOWN'

    RUNNING_STATES = set([PENDING, WAITING, RUNNING, COMPLETING, PREEMPTED, RESIZING, SUSPENDED])
    CANCELLED_STATES = set([CANCELLED, BOOT_FAIL, DEADLINE, NODE_FAIL, TIMEOUT])
    FAILED_STATES = set([FAILED])
    SUCCESS_STATES = set([COMPLETED])

    ALL_STATES = RUNNING_STATES | CANCELLED_STATES | FAILED_STATES | SUCCESS_STATES | set([UNKNOWN])

    STOPPED_SET = ALL_STATES - RUNNING_STATES - set([UNKNOWN])

    FINISHED_SET = FAILED_STATES | SUCCESS_STATES

    # We define three priority levels when submitting jobs and reporting priorities
    # with get_accounting_info
    PRIO_LOW = 'LOW_PRIO'
    PRIO_MEDIUM = 'MEDIUM_PRIO'
    PRIO_HIGH = 'HIGH_PRIO'
    PRIO_SET = frozenset([PRIO_LOW, PRIO_MEDIUM, PRIO_HIGH])

    # get_accounting_info() returns a dictionary containing the following keys.
    ACC_JOB_NAME = 'job_name'
    ACC_START_TIME = 'start_time'
    ACC_END_TIME = 'end_time'
    ACC_SUBMIT_TIME = 'submit_time'
    ACC_RETURN_CODE = 'return_code'
    ACC_STATE = 'state'
    ACC_SIGNAL = 'signal'
    ACC_JOB_ID = 'job_id'
    ACC_PRIORITY = 'priority'
    ACC_SET = frozenset([ACC_JOB_NAME, ACC_START_TIME, ACC_END_TIME,
                         ACC_SUBMIT_TIME, ACC_RETURN_CODE, ACC_STATE,
                         ACC_SIGNAL, ACC_JOB_ID, ACC_PRIORITY])

    @classmethod
    def _empty_info_dct(cls, jobid):
        """Return an accounting dict for a job for which we have no accounting information."""
        rdct = {cls.ACC_JOB_NAME: "",
                cls.ACC_START_TIME: None,
                cls.ACC_END_TIME: None,
                cls.ACC_SUBMIT_TIME: None,
                cls.ACC_RETURN_CODE: None,
                cls.ACC_STATE: BaseSlurmScheduler.UNKNOWN,
                cls.ACC_SIGNAL: None,
                cls.ACC_JOB_ID: jobid,
                cls.ACC_PRIORITY: None}
        assert set(rdct.keys()) == cls.ACC_SET, "messed up empty_info_dct"
        return rdct

    @classmethod
    def slurm_is_alive(cls):
        """Return True if the slurm configuration is adequate for Kive's purposes."""
        raise NotImplementedError

    @classmethod
    def slurm_ident(cls):
        """Return a string with some pertinent information about the slurm configuration."""
        raise NotImplementedError

    @classmethod
    def shutdown(cls):
        """This routine should be called by the Manager when it exits the main loop."""
        pass

    @classmethod
    def submit_job(cls,
                   workingdir,
                   driver_name,
                   driver_arglst,
                   user_id,
                   group_id,
                   prio_level,
                   num_cpus,
                   stdoutfile,
                   stderrfile,
                   after_okay=None,
                   after_any=None,
                   job_name=None):
        """ Submit a job to the slurm queue.
        The executable submitted will be of the form:

        workingdir/driver_name arglst[0] arglst[1] ...

        workingdir (string): directory name of the job. slurm will set this to the
        'current directory' when the job is run.
        driver_name (string): name of the command to execute as the main job script.
        driver_arglst (list of strings): arguments to the driver_name executable.
        user_id, group_id (integers): the unix user under whose account the jobs will
        be executed .
        prio_level: one of the three elements in PRIO_SET.
        num_cpus: the number of CPU's (in a slurm sense) to reserve for this job.

        stdoutfile, stderrfile (strings or None): file names into which the job's
        std out and err streams will be written.

        after_okay: (list of jobhandles): the jobhandles on whose success this job depends.
        All of these previously submitted jobs must complete successfully before slurm
        will start this one.
        If a job on which this one is cancelled, this job will also be cancelled by slurm.

        after_any: (list of jobhandles): job handles which must all complete, successfully
        or not, before this job runs.

        If a list is provided for both after-any and after_ok, then the two conditions are
        combined using a logical AND operator, i.e. the currently submitted job will only run
        if both conditions are met.

        This method returns a slurmjobhandle on success, or raises a
        subprocess.CalledProcessError exception on an error.
        """
        raise NotImplementedError

    @classmethod
    def job_cancel(cls, jobhandle):
        """Cancel a given job given its jobhandle.
        Raise an exception if an error occurs, otherwise return nothing.
        """
        raise NotImplementedError

    @classmethod
    def get_accounting_info(cls, job_handle_iter=None):
        """
        Get detailed information via sacct, on the specified job(s).

        job_id_iter is an iterable that must contain job handles of previously
        submitted jobs.
        If this list is None, or empty, information about all jobs on the
        accounting system is returned.
        Note that, under slurm, when a job A that is dependent on a pending job B,
        in encountered, no accounting information for job A is available.

        Returns a dictionary which maps job IDs to a dictionary containing
        the following fields defined above:
        - ACC_JOB_NAME (string)
        - ACC_JOB_ID (string)
        - ACC_START_TIME (datetime object)
        - ACC_END_TIME (datetime object)
        - ACC_SUBMIT_TIME (datetime object)
        - ACC_RETURN_CODE (int)
        - ACC_STATE (string, must be contained in ALL_STATES defined above)
        - ACC_SIGNAL (int: the signal number that caused termination of this step, or 0 if
        it ended normally)
        - ACC_PRIORITY (string, must be contained in PRIO_SET define above)

        All of these keys will be defined in all dictionaries returned.
        Note that if no accounting information is available for a jobid,
        then the values of the dict will be as follows:
        a) ACC_STATE will map to BaseSlurmScheduler.UNKNOWN
        b) ACC_JOB_ID will contain the job_id
        c) all other entries will map to None or "".
        See _empty_info_dct() above for details.
        """
        raise NotImplementedError

    @classmethod
    def set_job_priority(cls, jobhandle_lst, priority):
        """Set the priority of the specified jobs."""
        raise NotImplementedError


class SlurmScheduler(BaseSlurmScheduler):

    _qnames = None

    @classmethod
    def submit_job(cls,
                   workingdir,
                   driver_name,
                   driver_arglst,
                   user_id,
                   group_id,
                   prio_level,
                   num_cpus,
                   stdoutfile,
                   stderrfile,
                   after_okay=None,
                   after_any=None,
                   job_name=None):
        job_name = job_name or driver_name
        if prio_level not in cls.PRIO_SET:
            raise RuntimeError("prio_level not a valid priority")
        if cls._qnames is None:
            raise RuntimeError("Must call slurm_is_alive before submitting jobs")
        partname = cls._qnames[prio_level]
        cmd_lst = ["sbatch", "-D", workingdir, "--gid={}".format(group_id),
                   "-J", re.escape(job_name), "-p", partname,
                   "-s", "--uid={}".format(user_id),
                   "-c", str(num_cpus),
                   "--export=PYTHONPATH={}".format(workingdir)]
        # "--get-user-env",
        if stdoutfile:
            cmd_lst.append("--output=%s" % stdoutfile)
        if stderrfile:
            cmd_lst.append("--error=%s" % stderrfile)
        # handle dependencies. Note that sbatch can only have one --dependency option, or the second
        # one will overwrite the first one...
        # Note that here, multiple dependencies are always combined using an AND Boolean logic
        # (concatenation with a comma not a question mark) . See the sbatch man page for details.
        after_okay = after_okay if after_okay is not None else []
        after_any = after_any if after_any is not None else []
        if (len(after_okay) > 0) or (len(after_any) > 0):
            sdeplst = ["%s:%s" % (lstr, ":".join([jh.job_id for jh in lst])) for lst, lstr
                       in [(after_okay, 'afterok'), (after_any, 'afterany')] if len(lst) > 0]
            cmd_lst.extend(["--dependency=%s" % ",".join(sdeplst),
                            "--kill-on-invalid-dep=yes"])
        cmd_lst.append(os.path.join(workingdir, driver_name))
        cmd_lst.extend(driver_arglst)
        logger.debug(" ".join(cmd_lst))
        try:
            out_str = sp.check_output(cmd_lst)
        except sp.CalledProcessError as e:
            logger.error("sbatch returned an error code '%d'", e.returncode)
            logger.error("sbatch wrote this: '%s' ", e.output)
            raise
        if out_str.startswith("Submitted"):
            cl = out_str.split()
            try:
                job_id = cl[3]
            except:
                logger.error("sbatch completed with '%s'", out_str)
                raise RuntimeError("cannot parse sbatch output")
        else:
            logger.error("sbatch completed with '%s'", out_str)
            raise RuntimeError("cannot parse sbatch output")
        return SlurmJobHandle(job_id, cls)

    @classmethod
    def job_cancel(cls, jobhandle):
        """Cancel a given job given its jobhandle.
        Raise an exception if an error occurs, otherwise return nothing.
        """
        cmd_lst = ["scancel", "{}".format(jobhandle.job_id)]
        try:
            sp.check_output(cmd_lst)
        except sp.CalledProcessError as e:
            logger.error("scancel returned an error code '%s'", e.returncode)
            logger.error("scancel wrote this: '%s' ", e.output)
            raise

    @classmethod
    def slurm_is_alive(cls):
        """Return True if the slurm configuration is adequate for Kive's purposes.
        We have two requirements:
        a) slurm control daemon can be reached (fur submitting jobs).
           This is tested by running 'squeue' and checking for exceptions.
        b) There are three partitions of differing priorities that we can use.
           This is checked by running 'sinfo' and checking its state.
        c) slurm accounting is configured properly.
           This is tested by running 'sacct' and checking for exceptions.
        """
        is_alive = True
        try:
            cls._do_squeue()
        except sp.CalledProcessError:
            logger.exception("_do_squeue")
            is_alive = False
        logger.info("squeue passed: %s" % is_alive)
        if is_alive:
            try:
                is_alive = cls._partitions_are_ok()
            except:
                is_alive = False
                logger.exception("partitions_are_ok")
            logger.info("sinfo (checking partitions) passed: %s" % is_alive)
        if is_alive:
            try:
                cls.get_accounting_info()
            except:
                is_alive = False
                logger.exception("get_accounting_info")
            logger.info("sacct passed: %s" % is_alive)
        return is_alive

    @classmethod
    def _call_to_dict(cls, cmd_lst, splitchar=None):
        """ Helper routine:
        Call a slurm command provided in cmd_lst and parse the tabular output, returning
        a list of dictionaries.
        The first lines of the output should be the table headings, which are used
        as the dictionary keys.
        """
        logger.debug(" ".join(cmd_lst))
        try:
            out_str = sp.check_output(cmd_lst)
        except sp.CalledProcessError as e:
            logger.error("%s returned an error code '%s'", cmd_lst[0], e.returncode)
            logger.error("it wrote this: '%s' ", e.output)
            raise
        # NOTE: sinfo et al add an empty line to the end of its output. Remove that here.
        lns = [ln for ln in out_str.split('\n') if ln]
        logger.debug("read %d lines" % len(lns))
        nametup = tuple([s.strip() for s in lns[0].split(splitchar)])
        return [dict(zip(nametup, [s.strip() for s in ln.split(splitchar)])) for ln in lns[1:]]

    @classmethod
    def _partitions_are_ok(cls):
        """Determine whether we can call sinfo and that we have three
        partitions of different priorities.
        If there are exactly three partitions defined in the 'up' state, kive will check their
        priorities and use them.
        If more than three are defined, kive will chose those partitions whose names
        start with 'kive'. There must be exactly three of these that are 'up'
        of we raise an exception.
        NOTE: set the cls._qnames dictionary iff we return True here
        """
        cmd_lst = ['sinfo', '-a', '-O', 'available,partitionname,priority']
        dictlst = cls._call_to_dict(cmd_lst)
        logger.debug("got information of %d partitions" % len(dictlst))
        # NOTE: the keys are 'AVAIL', 'PARTITION' and 'PRIORITY'
        uplst = [dct for dct in dictlst if dct['AVAIL'] == 'up']
        logger.debug("found %d partitions in 'up' state" % len(uplst))
        if len(uplst) < 3:
            logger.error("slurm partition config error: want 3, but have %d 'up' partitions" % len(uplst))
            return False
        if len(uplst) > 3:
            # choose only those beginning with 'kive'
            kivelst = [dct for dct in uplst if dct['PARTITION'].startswith('kive')]
        else:
            kivelst = uplst
        # now we must have three remaining entries of differing priorities.
        if len(kivelst) != 3:
            logger.error('slurm partition config error: want 3, but have %d partitions' % len(kivelst))
            return False
        priolst = [(int(dct['PRIORITY']), dct['PARTITION']) for dct in kivelst]
        prioset = set([p for p, n in priolst])
        if len(prioset) != 3:
            logger.error('slurm partition config error: must have 3 different prio levels, but got %d' % len(prioset))
            return False
        partnames = [n for p, n in sorted(priolst, key=lambda a: a[0])]
        priokeys = [cls.PRIO_LOW, cls.PRIO_MEDIUM, cls.PRIO_HIGH]
        dd = cls._qnames = dict(zip(priokeys, partnames))
        logger.info('prio mapping: ', " ".join(["%s:%s" % (pk, dd[pk]) for pk in priokeys]))
        # create a reverse lookup table: qnames -> priovalues
        cls._revlookup = dict(zip(partnames, priokeys))
        return True

    @classmethod
    def slurm_ident(cls):
        """Return a string with some pertinent information about the slurm configuration."""
        if cls._qnames is None:
            raise RuntimeError("Must call slurm_is_alive before slurm_ident")
        priokeys = [cls.PRIO_LOW, cls.PRIO_MEDIUM, cls.PRIO_HIGH]
        dd = cls._qnames
        info_str = ", ".join(["%s:%s" % (pk, dd[pk]) for pk in priokeys])
        return 'Real Slurm: ' + info_str

    @classmethod
    def _do_squeue(cls, opts=None, job_id_iter=None):
        """Get the status of jobs currently on the queue.
        NOTE: this is an internal helper routine, the user probably wants to
        use SlurmScheduler.get_accounting_info() to get states of a number of previously
        submitted slurm jobs.

        job_id_iter is an iterable that must contain job ids (strings) of previously
        submitted jobs.
        If this list is None, or empty, information about all jobs on the
        queue is returned.

        This routine returns a dict. of which the
        key: jobid (integer) and
        value :
        a dict containing a row from squeue output. The keys of this dict are
          the squeue column table headers:
          JOBID, PARTITION, NAME, USER, ST, TIME, NODES and 'NODELIST(REASON)'
          The values are the values from the respective row.
          NOTE: all of these values are returned 'as is', i.e. as strings, except for
          the 'JOBID' value, which is converted to an integer.

        See the squeue man pages for more information about these entries.
        """
        cmd_lst = ["squeue"]
        if opts is not None:
            cmd_lst.extend(opts)
        has_jlst = job_id_iter is not None and len(job_id_iter) > 0
        if has_jlst:
            cmd_lst.extend(["-j", ",".join(job_id_iter)])
        return cls._call_to_dict(cmd_lst, splitchar=' ')

    @classmethod
    def get_accounting_info(cls, job_handle_iter=None):
        """ NOTE: sacct only provides information about jobs that are running or have completed.
        It does not provide information about jobs waiting in the queues.
        We therefore call squeue to get any pending jobs first, and then access sacct
        for all other information.
        """
        if cls._qnames is None:
            raise RuntimeError("Must call slurm_is_alive before get_accounting_info")
        have_job_handles = job_handle_iter is not None and len(job_handle_iter) > 0
        idlst = [jh.job_id for jh in job_handle_iter] if have_job_handles else None
        rdctlst = cls._do_squeue(opts=['--format=%i %j %P %V',
                                       '-p', ",".join(cls._qnames.values())],
                                 job_id_iter=idlst)
        accounting_info = {}
        # Create proper DateTime objects with the following format string.
        date_format = "%Y-%m-%dT%H:%M:%S"
        # keys are: JOBID NAME PARTITION SUBMIT_TIME
        for rdct in rdctlst:
            priority = cls._revlookup.get(rdct["PARTITION"], None)
            if priority is not None:
                job_id = rdct["JOBID"]
                accdct = cls._empty_info_dct(job_id)
                accdct[cls.ACC_PRIORITY] = priority
                sub_time = rdct["SUBMIT_TIME"]
                accdct[cls.ACC_SUBMIT_TIME] = datetime.strptime(sub_time, date_format) \
                    if sub_time != 'Unknown' else None
                accdct[cls.ACC_JOB_NAME] = rdct["NAME"]
                accdct[cls.ACC_STATE] = BaseSlurmScheduler.WAITING
                accounting_info[job_id] = accdct
        # now get accounting information
        # The --parsable2 option creates parsable output: fields are separated by a pipe, with
        # no trailing pipe (the difference between --parsable2 and --parsable).
        cmd_lst = ["sacct", "--parsable2", "--format",
                   "JobID,JobName,Start,End,State,Partition,Submit,ExitCode"]
        if have_job_handles:
            cmd_lst.extend(["-j", ",".join(idlst)])
        for raw_job_dict in cls._call_to_dict(cmd_lst, splitchar='|'):
            # Pre-process the fields.
            # There might be non-kive slurm partitions. Only report those jobs in partitions
            # we know about, as defined in the list of partition names.
            priority = cls._revlookup.get(raw_job_dict["Partition"], None)
            if priority is not None:
                job_id = raw_job_dict["JobID"]
                tdct = {}
                for field_name, field_val in [(fn, raw_job_dict[fn]) for fn in ["Start", "End", "Submit"]]:
                    tdct[field_name] = datetime.strptime(field_val, date_format) if field_val != 'Unknown' else None
                # Split sacct's ExitCode field, which looks like "[return code]:[signal]".
                return_code, signal = (int(x) for x in raw_job_dict["ExitCode"].split(":"))

                curstate = raw_job_dict["State"]
                if curstate not in cls.ALL_STATES:
                    raise RuntimeError("received undefined state from sacct '%s'" % curstate)
                accounting_info[job_id] = {
                    cls.ACC_JOB_NAME: raw_job_dict["JobName"],
                    cls.ACC_START_TIME: tdct["Start"],
                    cls.ACC_END_TIME: tdct["End"],
                    cls.ACC_SUBMIT_TIME: tdct["Submit"],
                    cls.ACC_RETURN_CODE: return_code,
                    cls.ACC_STATE: curstate,
                    cls.ACC_SIGNAL: signal,
                    cls.ACC_JOB_ID: job_id,
                    cls.ACC_PRIORITY: priority
                }
        # make sure all requested job handles have an entry...
        if have_job_handles:
            needset = set(idlst)
            gotset = set(accounting_info.keys())
            for missing_pid in needset - gotset:
                accounting_info[missing_pid] = cls._empty_info_dct(missing_pid)
        return accounting_info

    @classmethod
    def set_job_priority(cls, jobhandle_lst, priority):
        """Attempt to set the priority of the specified jobs.
        If a job is already running (rather than still pending) or has completed,
        this routine silently fails.
        """
        if cls._qnames is None:
            raise RuntimeError("Must call slurm_is_alive before setting job priority")
        if priority not in cls.PRIO_SET:
            raise RuntimeError("Illegal priority '%s' " % priority)
        if jobhandle_lst is None or len(jobhandle_lst) == 0:
            raise RuntimeError("no jobhandles provided")
        cmd_list = ["scontrol", "update", "job",
                    ",".join([jh.job_id for jh in jobhandle_lst]),
                    "Partition={}".format(cls._qnames[priority])]
        logger.debug(" ".join(cmd_list))
        try:
            sp.check_output(cmd_list)
        except sp.CalledProcessError as e:
            logger.debug("scontrol returned an error code '%s'", e.returncode)
            logger.debug("scontrol wrote this: '%s' ", e.output)
            # scontrol returns 1 if a job is already running or has completed
            # catch this case silently, but raise an exception in all other cases.
            if e.returncode != 1:
                raise


sco_pid = 100
dummypriotable = {BaseSlurmScheduler.PRIO_LOW: 0,
                  BaseSlurmScheduler.PRIO_MEDIUM: 1,
                  BaseSlurmScheduler.PRIO_HIGH: 2}


def startit(wdir, dname, arglst, stdout, stderr):
    """ Start a process with a command.
    NOTE: shell MUST be False here, otherwise the popen.wait() will NOT wait
    for completion of the command.
    """
    act_cmdstr = "cd %s;  ./%s  %s" % (wdir,
                                       dname,
                                       " ".join(arglst))
    # act_cmdstr = "%s/%s %s" % (wdir, dname, " ".join(arglst))
    cclst = ["/bin/bash", "-c", '%s' % act_cmdstr]
    p = sp.Popen(cclst, shell=False, stdout=stdout, stderr=stderr)
    return p


def callit(wdir, dname, arglst, stdout, stderr):
    popen = startit(wdir, dname, arglst, stdout, stderr)
    popen.wait()
    return popen.returncode


class workerproc:

    def __init__(self, jdct):
        self._jdct = jdct
        global sco_pid
        self.sco_pid = "%d" % sco_pid
        sco_pid += 1
        self.sco_retcode = None
        self.submit_time = None
        self.start_time = None
        self.end_time = None
        self.set_runstate(BaseSlurmScheduler.PENDING)
        self.prio_name = pname = jdct["prio_level"]
        self.prio_num = dummypriotable[pname]
    
    def do_run(self):
        """Invoke the code described in the _jdct"""
        self.set_runstate(BaseSlurmScheduler.RUNNING)
        j = self._jdct

        stdout = open(j["stdoutfile"], "w")
        stderr = open(j["stderrfile"], "w")
        self.popen = startit(j["workingdir"], j["driver_name"],
                             j["driver_arglst"], stdout, stderr)

    def check_ready_state(self, findct):
        """ Return a 2 tuple of Boolean:
        is_ready_to run, will_never_run
        """
        j = self._jdct
        after_any = j["after_any"]
        after_okay = j["after_okay"]
        # catch the most common case first: there are no dependencies
        any_cond = after_any is None
        okay_cond = after_okay is None
        if any_cond and okay_cond:
            return True, False
        any_cancel = okay_cancel = False
        finset = set(findct.iterkeys())
        if not any_cond:
            checkset = set([jhandle.job_id for jhandle in after_any])
            common_set = checkset & finset
            # if any jobs in common set are cancelled, we will never run
            any_cancel = any((findct[jid].iscancelled() for jid in common_set))
            any_cond = checkset <= finset
        if not okay_cond:
            checkset = set([jhandle.job_id for jhandle in after_okay])
            stat_dct = {BaseSlurmScheduler.CANCELLED: set(),
                        BaseSlurmScheduler.COMPLETED: set(),
                        BaseSlurmScheduler.FAILED: set()}
            common_set = checkset & finset
            for pid in common_set:
                proc = findct[pid]
                stat_dct[proc.get_runstate()].add(proc.sco_pid)
            ok_set = stat_dct[BaseSlurmScheduler.COMPLETED]
            okay_cancel = (stat_dct[BaseSlurmScheduler.FAILED] != set()) or\
                          (stat_dct[BaseSlurmScheduler.CANCELLED] != set())
            okay_cond = checkset <= ok_set
        has_cancel = any_cancel or okay_cancel
        if has_cancel:
            is_ready = False
        else:
            is_ready = any_cond and okay_cond
        return is_ready, has_cancel

    def do_cancel(self):
        if hasattr(self, "popen"):
            self.popen.kill()
        self.end_time = self.start_time = datetime.now()
        self.set_runstate(BaseSlurmScheduler.CANCELLED)

    def is_finished(self):
        self.popen.poll()
        self.sco_retcode = self.popen.returncode
        if self.sco_retcode is not None:
            self.my_state = BaseSlurmScheduler.COMPLETED if self.sco_retcode == 0 else BaseSlurmScheduler.FAILED
            return True
        else:
            return False

    def iscancelled(self):
        return self.get_runstate() in BaseSlurmScheduler.CANCELLED_STATES

    def get_runstate(self):
        return self.my_state

    def set_runstate(self, newstate):
        assert newstate in BaseSlurmScheduler.ALL_STATES, "illegal state '%s'" % newstate
        self.my_state = newstate

    def get_state_dct(self):
        j = self._jdct
        cls = BaseSlurmScheduler
        rdct = {
            cls.ACC_JOB_NAME: j["job_name"],
            cls.ACC_START_TIME: self.start_time,
            cls.ACC_END_TIME: self.end_time,
            cls.ACC_SUBMIT_TIME: self.submit_time,
            cls.ACC_RETURN_CODE: self.sco_retcode,
            cls.ACC_STATE: self.get_runstate(),
            cls.ACC_SIGNAL: None,
            cls.ACC_JOB_ID: self.sco_pid,
            cls.ACC_PRIORITY: self.prio_name
        }
        assert set(rdct.keys()) == BaseSlurmScheduler.ACC_SET, "weird state keys"
        return rdct


class DummySlurmScheduler(BaseSlurmScheduler):

    mproc = None
    
    @staticmethod
    def _docancel(can_pid, waitdct, rundct, findct):
        """Cancel the job with the provided pid.
        return 0 iff successful.
        """
        if can_pid in waitdct:
            proc = waitdct.pop(can_pid)
            proc.do_cancel()
            findct[can_pid] = proc
            return 0
        if can_pid in rundct:
            proc = rundct.pop(can_pid)
            proc.do_cancel()
            findct[can_pid] = proc
            return 0
        if can_pid in findct:
            findct[can_pid].do_cancel()
            return 0
        return -1

    @staticmethod
    def _setprio(jidlst, prio_name, waitdct, rundct, findct):
        """ Set the priority levels of the jobs in jidlst.
        """
        prio_num = dummypriotable.get(prio_name, None)
        if prio_num is None:
            return -1
        jset = set(jidlst)
        for s, dct in [(set(dct.keys()) & jset, dct) for dct in [waitdct, rundct, findct]]:
            for jid in s:
                dd = dct[jid]
                dd.prio_name = prio_name
                dd.prio_num = prio_num
        # NOTE: we have not checked whether all elements in jidlst have been found.
        # ignore this for now
        return 0

    @staticmethod
    def masterproc(jobqueue, resultqueue):

        waitdct = {}
        rundct = {}
        findct = {}
        while True:
            try:
                jtup = jobqueue.get(block=False, timeout=1)
            except Queue.Empty:
                jtup = None
            if jtup is not None:
                assert isinstance(jtup, tuple), 'Tuple expected'
                assert len(jtup) == 2, 'tuple length 2 expected'
                cmd, payload = jtup
                if cmd == 'new':
                    # received a new submission
                    # create a worker process, but don't necessarily start it
                    newproc = workerproc(payload)
                    newproc.submit_time = datetime.now()
                    # return the job id of the submitted job
                    assert newproc.sco_pid is not None, "newproc pid is NONE"
                    waitdct[newproc.sco_pid] = newproc
                    resultqueue.put(newproc.sco_pid)
                elif cmd == 'query':
                    resultqueue.put(DummySlurmScheduler._getstates(payload, waitdct, rundct, findct))
                elif cmd == 'cancel':
                    resultqueue.put(DummySlurmScheduler._docancel(payload, waitdct, rundct, findct))
                elif cmd == 'prio':
                    jhlst, prio = payload
                    resultqueue.put(DummySlurmScheduler._setprio(jhlst, prio, waitdct, rundct, findct))
                else:
                    raise RuntimeError("masterproc: WEIRD request '%s'" % cmd)

            # lets update our worker dicts
            # first the waiting dct
            rdylst = []
            for pid, proc in waitdct.items():
                is_ready_to_run, will_never_run = proc.check_ready_state(findct)
                if will_never_run:
                    del waitdct[pid]
                    proc.do_cancel()
                    findct[pid] = proc
                if is_ready_to_run:
                    rdylst.append(proc)
            # start the procs in rdylst in order of priority (high first)
            for proc in sorted(rdylst, key=lambda p: p.prio_num, reverse=True):
                del waitdct[proc.sco_pid]
                proc.start_time = datetime.now()
                proc.do_run()
                rundct[proc.sco_pid] = proc
            # next, check the rundct
            for proc in [p for p in rundct.values() if p.is_finished()]:
                proc.end_time = datetime.now()
                del rundct[proc.sco_pid]
                findct[proc.sco_pid] = proc

    @staticmethod
    def _getstates(qset, waitdct, rundct, findct):
        """Given a query set of job_ids, return a dict of each job's state."""
        waitset = set(waitdct.keys())
        runset = set(rundct.keys())
        finset = set(findct.keys())
        if qset == set():
            qset = waitset | runset | finset
        wp_lst = []
        for s, dct in [(qset & waitset, waitdct),
                       (qset & runset, rundct),
                       (qset & finset, findct)]:
            wp_lst.extend([dct[pid] for pid in s])
        return dict([(wp.sco_pid, wp.get_state_dct()) for wp in wp_lst])

    @classmethod
    def _init_masterproc(cls):
        jq = cls._jobqueue = mp.Queue()
        rq = cls._resqueue = mp.Queue()
        cls.mproc = mp.Process(target=cls.masterproc, args=(jq, rq))
        cls.mproc.start()

    @classmethod
    def slurm_is_alive(cls):
        """Return True if the slurm configuration is adequate for Kive's purposes."""
        if cls.mproc is None:
            cls._init_masterproc()
        return True

    @classmethod
    def slurm_ident(cls):
        """Return a string with some pertinent information about the slurm configuration."""
        return "Dummy Slurm"

    @classmethod
    def submit_job(cls,
                   workingdir,
                   driver_name,
                   driver_arglst,
                   user_id,
                   group_id,
                   prio_level,
                   num_cpus,
                   stdoutfile,
                   stderrfile,
                   after_okay=None,
                   after_any=None,
                   job_name=None):

        if cls.mproc is None:
            cls._init_masterproc()
        if prio_level not in cls.PRIO_SET:
            raise RuntimeError("prio_level not a valid priority")
        # make sure the job script exists and is executable
        full_path = os.path.join(workingdir, driver_name)
        if not os.path.isfile(full_path):
            raise sp.CalledProcessError(cmd=full_path, output=None, returncode=-1)
        jdct = dict([('workingdir', workingdir),
                     ('driver_name', driver_name),
                     ('driver_arglst', driver_arglst),
                     ('user_id', user_id),
                     ('group_id', group_id),
                     ('prio_level', prio_level),
                     ('num_cpus', num_cpus),
                     ('stdoutfile', stdoutfile),
                     ('stderrfile', stderrfile),
                     ('after_okay', after_okay),
                     ('after_any', after_any),
                     ('job_name', job_name)])
        cls._jobqueue.put(('new', jdct))
        jid = cls._resqueue.get()
        return SlurmJobHandle(jid, cls)

    @classmethod
    def job_cancel(cls, jobhandle):
        """Cancel a given job given its jobhandle.
        Raise an exception if an error occurs, otherwise return nothing.
        """
        if cls.mproc is None:
            cls._init_masterproc()
        cls._jobqueue.put(('cancel', jobhandle.job_id))
        res = cls._resqueue.get()
        if res != 0:
            raise sp.CalledProcessError(returncode=res)

    @classmethod
    def get_accounting_info(cls, job_handle_iter=None):
        """
        Get detailed information, i.e. sacct, on the specified job(s).

        job_id_iter is an iterable that must contain job handles of previously
        submitted jobs.
        If this list is None, or empty, information about all jobs on the
        queue is returned.

        Returns a dictionary which maps job IDs to a dictionary containing
        the following fields:
          - job_name (string)
          - job_id (string)
          - start_time (datetime object)
          - end_time (datetime object)
          - submit_time (datetime object)
          - return_code (int)
          - state (string)
          - signal (int: the signal number that caused termination of this step, or 0 if
            it ended normally)
        """
        if cls.mproc is None:
            cls._init_masterproc()
        if job_handle_iter is not None and len(job_handle_iter) > 0:
            query_set = set((jh.job_id for jh in job_handle_iter))
        else:
            query_set = set()
        cls._jobqueue.put(('query', query_set))
        accounting_info = cls._resqueue.get()
        return accounting_info

    @classmethod
    def set_job_priority(cls, jobhandle_lst, priority):
        """Set the priority of the specified jobs."""
        if cls.mproc is None:
            cls._init_masterproc()
        if priority not in cls.PRIO_SET:
            raise RuntimeError("prio_level not a valid priority")
        cls._jobqueue.put(('prio', ([jh.job_id for jh in jobhandle_lst], priority)))
        res = cls._resqueue.get()
        if res != 0:
            raise sp.CalledProcessError(returncode=res)

    @classmethod
    def shutdown(cls):
        cls.mproc.terminate()
        cls.mproc = None