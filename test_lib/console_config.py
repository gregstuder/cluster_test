"""This module provides the API that remote command config could use."""

import datetime
import httplib
import json
import time
import os

import pymongo
import hashlib

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
_remote_resource_downloads = {}
_local_resource_syncs = {}
_remote_binaries = {}
_num_setup_scripts = 0
_setup_scripts = {}

_stats_server = None

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

def RemoteResourceDownload(url, rel_path, pm=None):
    """Set a URL and target folder which downloadable are installed into"""
    global _remote_resource_downloads
    
    host = ""
    if pm != None: host = pm.host
    
    # Check that we haven't already added the resource for download
    for host in ["", host]:
        if host in _remote_resource_downloads:
            for dl_url, dl_rel_path in _remote_resource_downloads[host]:
                if dl_url == url: return
    
    if not host in _remote_resource_downloads:
        _remote_resource_downloads[host] = []
    
    _remote_resource_downloads[host].append((url, rel_path))

def LocalResourceSync(local_path, rel_path, pm=None, to_abs_path=True):
    """Set a local path which local files are sync'd from"""
    global _local_resource_syncs
    
    if to_abs_path:
        local_path = os.path.abspath(local_path)
    
    host = ""
    if pm != None: host = pm.host
        
    # Check that we haven't already added the resource for download
    for host in ["", host]:
        if host in _local_resource_syncs:
            for sync_local_path, sync_rel_path in _local_resource_syncs[host]:
                if sync_local_path == local_path: return
    
    if not host in _local_resource_syncs:
        _local_resource_syncs[host] = []
    
    _local_resource_syncs[host].append((local_path, rel_path))
    

def RemoteBinaryPath(ex, version, arch, rel_path, pm=None):
    """Set a custom binary version installed in a particular location"""
    global _remote_binaries
    
    host = ""
    if pm != None: host = pm.host
        
    if not host in _remote_binaries:
        _remote_binaries[host] = {}
        
    _remote_binaries[host][(ex, version, arch)] = rel_path

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
    
    else:
        snippet = os.path.abspath(snippet)
        name = name + "-" + os.path.split(snippet)[1]
    
    _setup_scripts[pm.host].append((name, snippet, lang, isFile, runAtStart))
    
    return name

def AddSetupScript(pm, snippet):
    """Add a script snippet to run on setup"""
    AddRemoteScript(pm, snippet, 'bash', False, True)

def SetStatsServer(stats_script=None):
    """Set the default statistics server"""
    
    global _stats_server
    
    assert _stats_server == None
    
    global _provisioner
    assert _provisioner != None
    
    # Need a different (ubuntu) machine for the gtk support
    options = provisioning.MachineOptions(ami='ami-3c994355')
    stats_machine = _provisioner.get_machine(options)
    stats_pm = ProcMgr(stats_machine)
    
    # Stats server needs mongodb 2.0.7
    for version in [ '2.0.7' ]:
        
        RemoteResourceDownload('http://downloads.mongodb.org/linux/mongodb-linux-x86_64-%s.tgz' % \
                               version, 'mongo-%s' % version, pm=stats_pm)
        
    for version in [ '2.0.7' ]:
        for ex in [ 'mongod', 'mongos', 'mongo', 'mongostat', 'mongodump', 'mongorestore' ]:
            bin_path = 'mongo-%s/mongodb-linux-x86_64-%s/bin/%s' % (version, version, ex)
            RemoteBinaryPath(ex, version, 'x86_64', bin_path, pm=stats_pm)
    
    _stats_server = StatsServer(stats_pm, 'default_stats_server', 27017, version='2.0.7', stats_script=stats_script)
    _stats_server.gen_command(1)

def GetNextPhase():
    """ Gets the next unused phase """
    
    global _remote_commands
    
    last_phase = 0
    for rc in _remote_commands:
         if rc.phase > last_phase: last_phase = rc.phase

    return last_phase + 1

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
        
        for host in [self.proc_mgr.host, ""]:
            
            remote_binaries = {}
            if host in _remote_binaries:
                _remote_binaries = _remote_binaries[host]
            
            if (self.program, self.version, self.arch) in _remote_binaries:
                rel_path = _remote_binaries[(self.program, self.version, self.arch)]
                return "./remote_resources/%s" % rel_path
        
        
        return self.program

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
        
        # Run in replica set.
        if self.replset:
            cmd_list.append('--replSet %s' % self.replset)
        if self.is_configsvr:
            cmd_list.append('--configsvr')
        
        global _stats_server
        if _stats_server != None:
            _stats_server.add_server_client(self.proc_mgr)
            cmd_list = _stats_server.cmd_with_syslog(self.proc_mgr, '%s.mongod.%s' % (self.alias, self.port), cmd_list)
        else:
            cmd_list.append('--logpath %s.log' % self.alias)
                        
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
        cmd_list.append('-vv') # verbose
        
        global _stats_server
        if _stats_server != None:
            _stats_server.add_server_client(self.proc_mgr)
            cmd_list = _stats_server.cmd_with_syslog(self.proc_mgr, '%s.mongos.%s' % (self.alias, self.port), cmd_list)
        else:
            cmd_list.append('--logpath %s.log' % self.alias)
        
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
        self.last_phase = None
                
    def gen_command(self, start_phase):
        """Generate command based on its attributes."""
                
        # Add command
        cmd_list = [self.resolve_program()]
        cmd_list.append(self.mongos.host_str())
                
        cmd_list.append('./base_remote_resources/scripts/%s' % self.script_name)
        opt = self.options
        
        global _stats_server
        if _stats_server != None:
            opt['statsServer'] = _stats_server.host_str()
        
        eval_cmd = 'inlineOptions = %s;' % json.dumps(opt)
        cmd_list.append('--eval %s' % console.escape(eval_cmd))
                        
        if _stats_server != None:
            _stats_server.add_server_client(self.proc_mgr)
            cmd_list = _stats_server.cmd_with_syslog(self.proc_mgr, '%s.mongo' % self.alias, cmd_list)
        
        cmd = ' '.join(cmd_list)
        self.last_phase = start_phase
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
    def __init__(self, proc_mgr, alias, cluster, credentials_dir):
        
        # TODO: Don't dl everywhere
        RemoteResourceDownload('https://mms.10gen.com/settings/10gen-mms-agent.tar.gz', 'mms-agent', proc_mgr)
        
        mms_path = "./remote_resources/mms-agent/mms-agent"
        
        # The cluster that MMS monitors.
        self._cluster = cluster
        self.api_key = None
        self.secret_key = None
        self.group_id = None
        self.load_credentials_from_file(credentials_dir)
        
        mms_setup = \
"""

# MMS PREREQS
echo "Setting up MMS environment..."

echo "Installing pymongo..."
if [ -n "`command -v yum`" ]; then
    sudo yum install -y python-devel
    sudo yum install -y python-setuptools
    sudo yum install -y gcc
else
    sudo apt-get install -y python-dev
    sudo apt-get install -y python-setuptools
    sudo apt-get install -y gcc
fi

sudo easy_install -U setuptools
sudo easy_install pip
sudo pip install pymongo      

echo "Setting up mms settings..."

MMS_PATH="%s"

mv $MMS_PATH/settings.py $MMS_PATH/settings-empty.py
sed 's/@API_KEY@/%s/' $MMS_PATH/settings-empty.py | sed 's/@SECRET_KEY@/%s/' > $MMS_PATH/settings.py

""" % (mms_path, self.api_key, self.secret_key)
        
        AddSetupScript(proc_mgr, mms_setup)
                
        super(MMSAgent, self).__init__(proc_mgr, alias, 'python %s/agent.py' % mms_path)
    
    def load_credentials_from_file(self, credentials_dir):
        
        api_key_file = os.path.join(credentials_dir, "mms.apiuser")
        secret_key_file = os.path.join(credentials_dir, "mms.apikey")
        group_id_file = os.path.join(credentials_dir, "mms.groupid")
        
        with open(api_key_file, 'r') as f:
            self.api_key = f.readline().strip()
            
        with open(secret_key_file, 'r') as f:
            self.secret_key = f.readline().strip()
            
        with open(group_id_file, 'r') as f:
            self.group_id = f.readline().strip()
    
    def gen_command(self, start_phase):
        """Generate command."""
        cmd = self.program
        AddCommandToProcMgr(self.proc_mgr, cmd, self.alias, start_phase)
        AddPhaseChecker(self._remove_all_hosts_from_group, start_phase)
        AddPhaseChecker(self._add_hosts_in_cluster, start_phase)

    def _remove_all_hosts_from_group(self):
        """Removes all existing hosts from an mms group"""
        conn = httplib.HTTPSConnection("mms.10gen.com")
        
        request_str = "/host/v2/hosts/%s" % self.api_key
        
        print "Request: ", "https://mms.10gen.com%s" % request_str
        
        conn.request("GET", request_str)
        
        r = conn.getresponse()
        
        response = json.loads(r.read())
        print "List hosts", response['status']
        
        host_list = response['hosts']
        
        for host_info in host_list:
            
            host_id = host_info['id']
            
            request_str = "/host/v1/delete/%s/%s" % (self.api_key, host_id)
            
            print "Request: ", "https://mms.10gen.com%s" % request_str
            
            conn.request("GET", request_str)
            
            r = conn.getresponse()
            
            response = json.loads(r.read())
            print "Remove host (%s)" % (host_id), response['status']
            
        return True

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
        conn = httplib.HTTPSConnection("mms.10gen.com")
        
        print "Request: ", "https://mms.10gen.com/host/v1/addHost/%s?hostname=%s&port=%d" % (self.api_key, host, port)
        
        conn.request("GET", "/host/v1/addHost/%s?hostname=%s&port=%d"
                % (self.api_key, host, port))
                              
        r = conn.getresponse()
        response = json.loads(r.read())
        print "Add host (%s, %d)" % (host, port), response['status']
                
        # Used in MMS:
        #final StringBuilder id = new StringBuilder((pHostname.length() + 31));
        #id.append(pGroupId.toString()).append(HOST_ID_SEP).append(pHostname).append(HOST_ID_SEP).append(pPort);
        #return CodecUtils.md5Hex(id.toString());
        #HOST_ID_SEP == '-'
        #       https: // mms.10gen.com / host / v1 / editHostAlias / API_KEY?hostId = XXX & userAlias = YYYY
        
        md5 = hashlib.md5()
        md5.update("%s-%s-%s" % (self.group_id, host, port))
        host_id = md5.hexdigest()
        
        alias = "host-%s:%s" % (host, port)
        
        print "Request: ", "https://mms.10gen.com/host/v1/editHostAlias/%s?hostId=%s&userAlias=%s" % (self.api_key, host_id, alias)
        
        conn.request("GET", "/host/v1/editHostAlias/%s?hostId=%s&userAlias=%s" % (self.api_key, host_id, alias))
                
        r = conn.getresponse()
        response = json.loads(r.read())
        print "Change host alias (%s, %d)" % (host, port), response['status']
        


class MongoStat(RemoteRunnable):
    """Mongostat instance.

    Attributes:
        target: (Mongos/Mongod) The mongos/mongod that mongostat monitors.
        interval: (float) The max time between two loads.
    """
    def __init__(self, proc_mgr, alias, target, interval=2):
        super(MongoStat, self).__init__(proc_mgr, alias, 'mongostat')
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


class StatsServer(MongoD):
    """rsyslog and mongodb stats server"""
    
    def __init__(self, proc_mgr, alias, port, version=None, stats_script=None):
        
        rsyslog_setup = \
"""
    
echo "Setting up rsyslogd for remote log collection..."
    
read -d '' LOG_SERVER_MODS <<"EOF"
# provides support for local system logging
$ModLoad imuxsock 

# provides kernel logging support (previously done by rklogd)
$ModLoad imklog

# provides UDP syslog reception. For TCP, load imtcp.
$ModLoad imudp

# For TCP, InputServerRun 514
$UDPServerRun 514

# This one is the template to generate the log filename dynamically, depending on the client's IP address.
$template FILENAME,"/var/log/%fromhost%/syslog.log"

# Log all messages to the dynamically formed file. Now each clients log (192.168.1.2, 192.168.1.3,etc...), will be under a separate directory which is formed by the template FILENAME.
*.* ?FILENAME
EOF
    
sudo echo "$LOG_SERVER_MODS" | sudo tee -a /etc/rsyslog.conf > /dev/null    

sudo service rsyslog restart

"""
        AddSetupScript(proc_mgr, rsyslog_setup)
        
        pystats_setup = \
"""

echo "Setting up stats server for SciPy statistics..."

if [ -n "`command -v yum`" ]; then
    echo "STATS SERVER MUST BE UBUNTU FOR CORRECT X INTEGRATION..."
else

    echo "Allowing ssh X forwarding..." 

    echo "Installing XAuth..."
    sudo apt-get update
    sudo apt-get install -y xauth

    echo "X11Forwarding yes" | sudo tee -a /etc/ssh/sshd_config

    sudo service ssh restart
fi

echo "Installing numpy and matplotlib..."
if [ -n "`command -v yum`" ]; then
    sudo yum install -y numpy
    sudo yum install -y python-matplotlib
else
    sudo apt-get install -y python-numpy
    sudo apt-get install -y python-matplotlib
fi

echo "Installing pymongo..."
if [ -n "`command -v yum`" ]; then
    sudo yum install -y python-devel
    sudo yum install -y python-setuptools
    sudo yum install -y gcc
else
    sudo apt-get install -y python-dev
    sudo apt-get install -y python-setuptools
    sudo apt-get install -y gcc
fi

sudo easy_install -U setuptools
sudo easy_install pip
sudo pip install pymongo

"""
        AddSetupScript(proc_mgr, pystats_setup)
        
        super(StatsServer, self).__init__(proc_mgr, alias, port, version=version)
        
        LocalResourceSync('./test_lib/stats', './base_remote_resources', pm=proc_mgr, to_abs_path=False)
        
        self.stats_script = os.path.abspath(stats_script)
        self.clients = {}
    
    def add_server_client(self, client_pm):
        
        if client_pm.host in self.clients: return
        if client_pm.host == self.proc_mgr.host: return
        
        client_setup = \
"""

echo "Setting up client for remote rsyslogd log collection..."

read -d '' LOG_CLIENT_MODS <<"EOF"
$ModLoad imuxsock

$ModLoad imklog

# Provides UDP forwarding. The IP is the server's IP address
*.* @%s:514 

# Provides TCP forwarding. But the current server runs on UDP
# *.* @@%s:514
EOF

sudo echo "$LOG_CLIENT_MODS" | sudo tee -a /etc/rsyslog.conf > /dev/null

sudo service rsyslog restart

""" % (self.proc_mgr.host, self.proc_mgr.host)

        AddSetupScript(client_pm, client_setup)
        self.clients[client_pm.host] = True
        
        
    def cmd_with_syslog(self, client_pm, tag, cmd_list):
        
        syslog_script = \
"""

# Running command via syslog

"$@" | logger -t "%s" 2>&1

""" % tag

        syslog_script_name = AddRemoteScript(client_pm, syslog_script, 'bash')
        
        new_cmd_list = [ 'bash', './base_remote_resources/scripts/%s' % syslog_script_name ]
        new_cmd_list.extend(cmd_list)
        
        return new_cmd_list   

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

