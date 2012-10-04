"""This module provides the API that remote command config could use."""

import datetime
import httplib
import json
import time
import os

import pymongo

import console
import provisioning

# Provisioning
_provisioner = None

# Test environment config
_remote_commands = []
_bin_path = 'bin/'
_log_path = 'logs/'
_phase_checkers = []
_key_file = None
_remote_resource_downloads = []
_remote_binaries = {}
_num_setup_scripts = 0
_setup_scripts = {}

BASH_SETUP_SCRIPT_PREFIX = \
"""
#!/bin/bash
# Bash setup script
            
"""

MONGOSHELL_SETUP_SCRIPT_PREFIX = \
"""

// Mongo shell script
            
"""

def SetProvisioner(provisioner):
    """Provisioner setter. So that the console could know which provisioner is
    in use.
    """
    global _provisioner
    _provisioner = provisioner

def AddCommandToProcMgr(proc_mgr, *args, **kwargs):
    """Add command to Console for given process manager."""
    # _remote_commands is a global variable.
    _remote_commands.append(console.RemoteCommand(
        proc_mgr.host, proc_mgr.port, proc_mgr.machine.user_name,
        *args, **kwargs))

def AddWaitStaging(pm):
    """Add command that will be waiting for staging to finish.

    Staging script is uploaded as user-data when instances are created and AWS
    will run the bash script automatically after start. But we don't know when
    the script finishes, which typically costs 10 minutes.

    The staging bash script will make a new file /logs/iostat.log at last. So
    we check whether it exists to tell when staging finishes. It is a dirty
    workaround, but good enough until we adopt a better solution.

    Alternative approaches are
    1. Using pre-defined AMI that is ready for test and setting staging=False
    in MachineOptions' constructor.
    2. Using staging tools like Chef to do staging for us.
    """
    cmd = "bash -c 'while [ ! -e ./process_manager.pid ]; do sleep 1; done'"
    AddCommandToProcMgr(pm, cmd, 'wait_cloud_init', phase=0, wait=True)

def RemoteResourcePath(path):
    """Set the folder from which the binaries are sent to remote machines."""
    if not path: return
    if path[-1] != os.sep:
        path += os.sep  # Send all files in bin folder, not the folder itself.
    global _bin_path
    _bin_path = os.path.abspath(path)

def RemoteResourceDownload(url, rel_path):
    """Set a URL and target folder which downloadable are installed into"""
    global _remote_resource_downloads
    _remote_resource_downloads.append((url, rel_path))

def RemoteBinaryPath(ex, version, arch, rel_path):
    """Set a custom binary version installed in a particular location"""
    global _remote_binaries
    _remote_binaries[(ex, version, arch)] = rel_path

def KeyFile(path):
    """Change key file."""
    global _key_file
    _key_file = path

def LogPath(path):
    """Change log path."""
    global _log_path
    _log_path = path

def AddPhaseChecker(*args):
    """Add a phase checker to global list."""
    _phase_checkers.append(PhaseChecker(*args))

def AddRemoteScript(pm, snippet, lang, isFile=False, runAtStart=False):
    """Add a script snippet to rsync on setup"""
    global _setup_scripts
    global _num_setup_scripts
    
    if not pm.host in _setup_scripts:
        _setup_scripts[pm.host] = []
    
    name = "snippet%d" % _num_setup_scripts
    _num_setup_scripts = _num_setup_scripts + 1
    
    if not isFile:
        if lang == 'bash':
            snippet = BASH_SETUP_SCRIPT_PREFIX + "\n" + snippet
            
        if lang == 'mongoshell':
            snippet = MONGOSHELL_SETUP_SCRIPT_PREFIX + "\n" + snippet
    
    _setup_scripts[pm.host].append((name, snippet, lang, isFile, runAtStart))
    
    return name

def AddSetupScript(pm, snippet):
    """Add a script snippet to run on setup"""
    AddRemoteScript(pm, snippet, 'bash', False, True)

def _phase_check(phase):
    """This function is a part of the run() progress. It will run after
    every phase and can be override in command config file.

    Phases should be same with that defined in AddCommand.

    Args:
        phase: (int) The phase just done.
    """
    success = True
    for pc in _phase_checkers:
        if not pc.check(phase):
            success = False
            break
    return success


class PhaseChecker(object):
    """Wrapper of check function and phase.

    Attributes:
        phase: (int)
    """
    def __init__(self, check_function, phase):
        self._fun = check_function
        self.phase = phase

    def check(self, phase):
        """Check given phase."""
        if phase == self.phase:
            return self._fun()
        return True

class ProcMgr(object):
    """Represent a process manager instance in configure.

    Attributes:
        machine: (provisioning.Machine) The machine on which process manager
            is running.
        host: (str) Hostname of the server.
        port: (str) The port that process manager listens on.
    """
    def __init__(self, machine, port=2900):
        self.machine = machine
        self.host = machine.host
        self.port = port

class RemoteRunnable(object):
    """Base class for class that can generate commands running on process
    manager.

    Attributes:
        proc_mgr: (ProcMgr) The process manager that commands runs on.
        alias: (str) The alias of process, which is also used in dbpath
            and log file name.
        program: (str) The name of program used in command.
    """
    def __init__(self, proc_mgr, alias, program, version=None, arch=None):
        self.proc_mgr = proc_mgr
        self.alias = alias
        self.program = program
        self.version = version
        self.arch = arch

    def resolve_program(self):
        global _remote_binaries
                
        if not (self.program, self.version, self.arch) in _remote_binaries: return self.program
        
        rel_path = _remote_binaries[(self.program, self.version, self.arch)]
        return "./remote_resources/%s" % rel_path

    def gen_command_for_pm(self, cmd, phase, alias=None, wait=False):
        """Add command to given process manager."""
        if alias is None:
            alias = self.alias
        AddCommandToProcMgr(self.proc_mgr, cmd, alias, phase, wait=wait)


class MongoD(RemoteRunnable):
    """Represent a mongod instance.

    Attributes:
        host: (str) Hostname of mongod server, copied from ProcMgr.
        port: (int) The port of mongod.
        replset: (str) The replset of mongod. If it is None, the mongod is not
            in a replica set.
        is_arbiter: (boolean) Whether mongod is an arbiter.
        is_configsvr: (boolean) Whether mongod is a config server.
        last_phase: (int) The last phase of the mongod's commands.
    """
    def __init__(self, proc_mgr, alias, port, replset=None,
                 is_arbiter=False, is_configsvr=False, version=None):
        super(MongoD, self).__init__(proc_mgr, alias, 'mongod', version, 'x86_64')
        self.host = proc_mgr.host
        self.port = port
        self.replset = replset
        self.is_arbiter = is_arbiter
        self.is_configsvr = is_configsvr
        self.last_phase = None

    def gen_command(self, start_phase):
        """Generate command based on its attributes."""
        self.gen_command_for_pm('mkdir -p data/%s' % self.alias, start_phase,
                               alias='mk_' + self.alias, wait=True)
        cmd_list = [self.resolve_program(), '--oplogSize 50', '-v'] # verbose
        cmd_list.append('--dbpath data/%s' % self.alias)
        cmd_list.append('--port %d' % self.port)
        cmd_list.append('--logpath %s.log' % self.alias)
        # Run in replica set.
        if self.replset:
            cmd_list.append('--replSet %s' % self.replset)
        if self.is_configsvr:
            cmd_list.append('--configsvr')
        cmd = ' '.join(cmd_list)
        # Generate commands.
        self.gen_command_for_pm(cmd, start_phase + 1)
        self.last_phase = start_phase + 1
        AddPhaseChecker(
            lambda : wait_for_connection(self.proc_mgr.host, self.port),
            self.last_phase)

    def host_str(self):
        """Get hostname:port."""
        return "%s:%d" % (self.host, self.port)

class Replset(object):
    """Represent a replica set.

    Attributes:
        name: (str) The name of replica set.
        members: (array of Mongod) Members of the replica set.
        last_phase: (int) The last phase of the replset's commands.
    """
    def __init__(self, name):
        self.name = name
        self.members = []
        self.last_phase = None

    def add_member(self, member):
        """Add member to replica set.

        Parameter:
            member: (Mongod) New member.
        """
        member.replset = self.name
        self.members.append(member)

    def initialize(self):
        """Initialize a replica set."""
        if not self.members:
            print "We need at least 1 member to start replica set."
            return

        # Prepare members for replSetInitiate config.
        rs_members = []
        for i in range(len(self.members)):
            mongod = self.members[i]
            m = { '_id' : i, 'host' : "%s:%d" % (mongod.host, mongod.port) }
            if mongod.is_arbiter:
                m['arbiterOnly'] = True
            rs_members.append(m)
        rs_config = { '_id': self.name, 'members': rs_members }

        rs_host = self.members[0].proc_mgr.host  # Use the external hostname.
        rs_port = self.members[0].port
        while True:
            # Connect to primary.
            conn = wait_for_connection(rs_host, rs_port)

            # Replset initiate.
            print 'Initializing replica set %s...' % self.name,
            try:
                conn.admin.command('replSetInitiate', rs_config)
                print 'done'
                break
            except pymongo.errors.OperationFailure:
                print 'failed. retry...'
            conn.close()
            time.sleep(1)
        return True

    def gen_command(self, start_phase):
        """Generate commands."""
        for m in self.members:
            m.gen_command(start_phase)
        self.last_phase = max([m.last_phase for m in self.members])
        AddPhaseChecker(self.initialize, self.last_phase)

    def host_str(self):
        """Get aggregated hostname like rs1/localhost:22000,localhost:22001"""
        members = ','.join([m.host_str() for m in self.members])
        return "%s/%s" % (self.name, members)


class MongoS(RemoteRunnable):
    """Represent a mongos.

    Attributes:
        host: (str) Hostname of mongos server, copied from ProcMgr.
        port: (int) The port of mongos.
        last_phase: (int) The last phase of the mongos's commands.
        config_servers: (array of Mongod) Config servers.
    """
    def __init__(self, proc_mgr, alias, port, config_servers, version=None):
        super(MongoS, self).__init__(proc_mgr, alias, 'mongos', version, 'x86_64')
        self.host = proc_mgr.host
        self.port = port
        self.config_servers = config_servers
        self.last_phase = None

    def gen_command(self, start_phase):
        """Generate command based on its attributes."""
        cmd_list = [self.resolve_program()]
        config_db_str = ','.join([c.host_str() for c in self.config_servers])
        cmd_list.append('--configdb %s' % config_db_str)
        cmd_list.append('--port %d' % self.port)
        cmd_list.append('--logpath %s.log' % self.alias)
        cmd_list.append('-vv') # verbose
        cmd = ' '.join(cmd_list)

        self.gen_command_for_pm(cmd, start_phase)
        self.last_phase = start_phase
        # Wait until I start.
        AddPhaseChecker(
            lambda : wait_for_connection(self.proc_mgr.host, self.port),
            self.last_phase)

    def enable_sharding(self, collection, key):
        """Enable sharding collection.

        Parameters:
            collecition: (str) Collection to be sharded, e.g. "test.foo".
            key: (dict of (key_field, direction)) Sharding key.
                For example: {"_id": 1}
        """
        db = collection[:collection.find(':')]
        c = pymongo.Connection(self.proc_mgr.host, self.port)
        admin = c.admin
        # Enable sharding.
        try:
            print "enable sharding result: %r" % admin.command(
                {"enablesharding": db})
        except pymongo.errors.OperationFailure:
            pass  # Already enabled.
        try:
            print "shard collection result: %r" % admin.command({
                    "shardcollection": collection,
                    "key": key
                })
        except pymongo.errors.OperationFailure:
            pass  # Already sharded.

    def host_str(self):
        """Get hostname:port."""
        return "%s:%d" % (self.host, self.port)

class Cluster(object):
    """Cluster of several shards.

    Attributes:
        shards: (array of Replset or Mongod) Shards in the cluster.
        mongoses: (array of Mongos) Mongos in the cluster.
        config_servers: (array of Mongod) config servers in the cluster.
        last_phase: (int) The last phase of the cluster's commands.
    """
    def __init__(self):
        self.shards = []
        self.config_servers = []
        self.mongoses = []
        self.last_phase = None

    def add_shard(self, shard):
        """Add a mongod or a replset into cluster.
        TODO(siyuan): Test on mongod.

        Parameters:
            shard: A mongod or a replset.
        """
        self.shards.append(shard)

    def add_mongos(self, mongos):
        """Add a mongod into cluster.

        Parameters:
            mongos: (Mongos)
        """
        self.mongoses.append(mongos)

    def add_config_server(self, server):
        """Add a config server into cluster."""
        self.config_servers.append(server)

    def gen_command(self, start_phase):
        """Generate command to run the cluster."""
        for shard in self.shards:
            shard.gen_command(start_phase)
        shard_phase = max([s.last_phase for s in self.shards])
        for conf in self.config_servers:
            conf.gen_command(shard_phase + 1)

        configsvr_phase = max([s.last_phase for s in self.config_servers])
        self.last_phase = configsvr_phase

        if self.mongoses:
            for m in self.mongoses:
                m.gen_command(configsvr_phase + 1)
            mongos_phase = max([m.last_phase for m in self.mongoses])
            self.last_phase = mongos_phase
            AddPhaseChecker(self.add_shards_to_cluster, mongos_phase)

    def add_shards_to_cluster(self):
        """Add mongod or replsets to cluster."""
        print "Connecting to mongos..."
        mongos = self.mongoses[0]
        conn = wait_for_connection(mongos.proc_mgr.host, mongos.port)

        print "Adding shards..."
        # Waiting for shards.
        for shard in self.shards:
            if isinstance(shard, Replset):
                m = shard.members[0]
                wait_for_primary(m.proc_mgr.host, m.port)
            else:
                wait_for_connection(shard.proc_mgr.host, shard.port).close()

        for s in self.shards:
            try:
                conn.admin.command('addShard', s.host_str())
                print 'shard have been added', s.host_str()
            except pymongo.errors.OperationFailure as e:
                print e
                return False
        return True

class MongoShell(RemoteRunnable):
    """Use mongo with javascript file to generate load.

    Attributes:
        mongos: (Mongos) The mongos under test.
        sharded_collection: (array of str) Sharded collection used by tester.
        max_load_sleep: (float) The max interval between two period of load.
    """
    def __init__(self, proc_mgr, alias, mongos, script, options={}, isFile=False, version=None):
        super(MongoShell, self).__init__(proc_mgr, alias, 'mongo', version, 'x86_64')
        self.mongos = mongos
        self.script = script
        self.isFile = isFile
        self.options = options
        self.script_name = AddRemoteScript(proc_mgr, script, 'mongoshell', isFile, False)
                
    def gen_command(self, start_phase):
        """Generate command based on its attributes."""
                
        # Add command
        cmd_list = [self.resolve_program()]
        cmd_list.append(self.mongos.host_str())
                
        cmd_list.append('./base_remote_resources/scripts/%s' % self.script_name)
        opt = self.options
        eval_cmd = 'inlineOptions = %s;' % json.dumps(opt)
        cmd_list.append('--eval %s' % console.escape(eval_cmd))
        cmd = ' '.join(cmd_list)
        AddCommandToProcMgr(self.proc_mgr, cmd, self.alias, start_phase)

class LoadTester(RemoteRunnable):
    """Use mongo with javascript file to generate load.

    Attributes:
        mongos: (Mongos) The mongos under test.
        sharded_collection: (array of str) Sharded collection used by tester.
        max_load_sleep: (float) The max interval between two period of load.
    """
    def __init__(self, proc_mgr, alias, mongos, sharded_collection,
                 max_load_sleep=1, version=None):
        super(LoadTester, self).__init__(proc_mgr, alias, 'mongo', version, 'x86_64')
        self.mongos = mongos
        self.sharded_collection = sharded_collection
        self.max_load_sleep = max_load_sleep

    def gen_command(self, start_phase):
        """Generate command based on its attributes."""
        # Enable sharding after mongos starts.
        AddPhaseChecker(self._enable_sharding, self.mongos.last_phase)
        # Add command
        cmd_list = [self.program]
        cmd_list.append(self.mongos.host_str())
        cmd_list.append('load-test-actions.js')
        opt = { 'scriptMode' : 'load',
                'shardedColls' : self.sharded_collection,
                'maxLoadSleep' : self.max_load_sleep }
        eval_cmd = 'inlineOptions = %s;' % json.dumps(opt)
        cmd_list.append('--eval %s' % console.escape(eval_cmd))
        cmd = ' '.join(cmd_list)
        AddCommandToProcMgr(self.proc_mgr, cmd, self.alias, start_phase)

    def _enable_sharding(self):
        """Enable sharding for target collections."""
        for coll in self.sharded_collection:
            self.mongos.enable_sharding(coll, {'_id':1})
        return True


class MMSAgent(RemoteRunnable):
    """MMS agent.

    Attributes:
        api_key: (str) API key of MMS group.
        cluster: (Cluster) The cluster under monitor.
    """
    def __init__(self, proc_mgr, alias, api_key, cluster):
        super(MMSAgent, self).__init__(proc_mgr, alias, 'python mms-agent/agent.py')
        self.api_key = api_key
        # The cluster that MMS monitors.
        self._cluster = cluster

    def gen_command(self, start_phase):
        """Generate command."""
        cmd = self.program
        AddCommandToProcMgr(self.proc_mgr, cmd, self.alias, start_phase)
        AddPhaseChecker(self._add_hosts_in_cluster, start_phase)

    def _add_hosts_in_cluster(self):
        """The callback in phase check to add whole cluster into MMS."""
        for shard in self._cluster.shards:
            if isinstance(shard, Replset):
                for m in shard.members:
                    self.add_host(m.host, m.port)
            else:
                self.add_host(shard.host, shard.port)
        for cs in self._cluster.config_servers:
            self.add_host(cs.host, cs.port)
        for s in self._cluster.mongoses:
            self.add_host(s.host, s.port)

        return True

    def add_host(self, host, port):
        """Add host to MMS."""
        # api_key = '3b37f6048268296c98592c48c41c5f64'
        conn = httplib.HTTPSConnection("mms.10gen.com")
        conn.request("GET", "/host/v1/addHost/%s?hostname=%s&port=%d"
                % (self.api_key, host, port))
        r = conn.getresponse()
        response = json.loads(r.read())
        print "Add host (%s, %d)" % (host, port), response['status']


class Mongostat(RemoteRunnable):
    """Mongostat instance.

    Attributes:
        target: (Mongos/Mongod) The mongos/mongod that mongostat monitors.
        interval: (float) The max time between two loads.
    """
    def __init__(self, proc_mgr, alias, target, interval=2):
        super(Mongostat, self).__init__(proc_mgr, alias, 'mongostat')
        self.target = target
        self.interval = interval

    def gen_command(self, start_phase):
        """Generate command."""
        cmd_list = [self.program, '--host', self.target.host, '--port', str(self.target.port)]
        if isinstance(self.target, MongoS):
            cmd_list.append('--discover')
        cmd_list.append(str(self.interval))
        cmd = ' '.join(cmd_list)
        AddCommandToProcMgr(self.proc_mgr, cmd, self.alias, start_phase)


########################### Global Functions ###########################

def wait_for_connection(server, port):
    """wait until server starts.

    Returns:
        The connection to server.
    """
    try:
        # Don't print message at the first time.
        return pymongo.Connection(server, port)
    except pymongo.errors.AutoReconnect:
        print "Waiting for", server, port, 'to connect...',
        while True:
            try:
                conn = pymongo.Connection(server, port)
            except pymongo.errors.AutoReconnect as err:
                print "\nError: " + str(err)
                time.sleep(1)
            else:
                print 'done'
                return conn

def wait_for_primary(server, port):
    """Wait until primary has been elected."""
    wait_for_connection(server, port)
    print "Waiting for replset", server, port, 'to start...',
    conn = pymongo.Connection(server, port)
    while True:
        resp = conn.admin.command('isMaster')
        if 'primary' in resp:
            print 'done'
            return
        time.sleep(1)

def waiting_for(sec):
    """Wait for a given seconds."""
    print "Now:", str(datetime.datetime.now())
    print "Waiting for", sec, "seconds..."
    time.sleep(sec)
    print "Now:", str(datetime.datetime.now())
    return True

