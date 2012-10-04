"""
This config file defines a cluster test, and is run via the cluster test console.
"""

# ALL PATHS RELATIVE TO THE CONSOLE ROOT

# Set resource path - this information will get uploaded to all remote servers
RemoteResourcePath('./remote_resources')

# These downloadable resources will be attached to the remote resources at the subdirectories
# indicated
for version in [ '2.0.7', '2.2.0' ]:
    RemoteResourceDownload('http://downloads.mongodb.org/linux/mongodb-linux-x86_64-%s.tgz' % version, 'mongo-%s' % version)

# Define where certain binary versions are located
for version in [ '2.0.7', '2.2.0', 'cluster-test' ]:
    for ex in [ 'mongod', 'mongos', 'mongo', 'mongostat', 'mongodump', 'mongorestore' ]:
        
        bin_path = 'mongo-%s/mongodb-linux-x86_64-%s/bin/%s' % (version, version, ex)
        RemoteBinaryPath(ex, version, 'x86_64', bin_path)
        
local_version = '2.0.7'
local_mongo_path = 'mongo-%s/mongodb-linux-x86_64-%s/bin' % (local_version, local_version)
        

# Set log path - the directory in which all logs should be stored
LogPath('/tmp/logs/')

#
# PROVISIONING
#

# The first parameter is test name, which should be unique for a test. 
#
# The access credentials are the AWS test account of 10gen - they are stored in a separate
# directory with the filenames:
#
# - access-key
# - secret-access-key
# - <keypair name>.privatekey
#

provisioner = provisioning.AWS('cluster_test', credentials_dir='./aws_test')
SetProvisioner(provisioner)

# Setup default options, us-east-1, large, 64bit
options = provisioning.MachineOptions()

# Machines is the list of IPs of all instances.
machines = provisioner.get_machines(options, number=6)

# Setup process managers for all IPs
pms = [ProcMgr(m) for m in machines]

#
# SETUP MACHINES
#

iostat_script = \
"""

# SCRIPTS SHOULD BE IDEMPOTENT
# They get run at each set-up, as normal user with sudo privs

echo "Installing iostat..."
if [ -n "`command -v yum`" ]; then
    sudo yum install -y sysstat
else
    sudo apt-get install -y sysstat
fi

echo "Starting iostat monitoring..."
sudo mkdir -p /var/log
sudo killall iostat
sudo su -c "nohup /usr/bin/iostat -xmt 2 > /var/log/iostat.log 2>&1 &"
sleep 1
ps aux | grep iostat
echo "Iostat monitoring started."

"""

limits_script = \
"""

echo "Changing file and process hard limits..."
sudo echo -e "*  hard    nproc   20000\n*    soft    nproc   20000\n*    hard    nofile  20000\n*    soft    nofile  20000\n"\
 | sudo tee /etc/security/limits.conf > /dev/null

"""

keepalive_script = \
"""

echo "Changing keepalive to 300s..."
sudo echo 300 | sudo tee /proc/sys/net/ipv4/tcp_keepalive_time > /dev/null

"""

munin_script = \
"""

echo "Installing munin-node..."
if [ -n "`command -v yum`" ]; then
    sudo yum install -y munin-node
else
    sudo apt-get install -y munin-node
fi

sudo echo -e "\nallow .*\n" | sudo tee -a /etc/munin/munin-node.conf > /dev/null
sudo /etc/init.d/munin-node stop
sudo /etc/init.d/munin-node start

"""

misc_script = \
"""

echo "Setting up misc settings..."

echo "if [ -f /etc/bashrc ] ; then . /etc/bashrc; fi" > ../.bashrc
echo "export PATH=\"`pwd`/remote_resources/%s:$PATH\"" >> ../.bashrc

cd ..
rm -f .profile
ln -s .bashrc .profile

""" % (local_mongo_path)

for pm in pms:
    AddSetupScript(pm, iostat_script)
    AddSetupScript(pm, limits_script)
    AddSetupScript(pm, keepalive_script)
    AddSetupScript(pm, munin_script)
    AddSetupScript(pm, misc_script)
    
    # Make sure setup is complete
    AddWaitStaging(pm)

#
# SETUP CLUSTER
#

rs1 = Replset('rs1')
rs1_1 = MongoD(pms[0], 'rs1_1', 27017, version='2.0.7')
rs1_2 = MongoD(pms[1], 'rs1_2', 27017, version='2.0.7')
rs1_3 = MongoD(pms[1], 'rs1_3', 27018, version='2.0.7', is_arbiter=True)
rs1.add_member(rs1_1)
rs1.add_member(rs1_2)
rs1.add_member(rs1_3)

rs2 = Replset('rs2')
rs2_1 = MongoD(pms[2], 'rs2_1', 27017, version='2.0.7')
rs2_2 = MongoD(pms[3], 'rs2_2', 27017, version='2.0.7')
rs2_3 = MongoD(pms[3], 'rs2_3', 27018, version='2.0.7', is_arbiter=True)
rs2.add_member(rs2_1)
rs2.add_member(rs2_2)
rs2.add_member(rs2_3)

config_1 = MongoD(pms[4], 'config_1', 27017, version='2.0.7', is_configsvr=True)
cluster = Cluster()
cluster.add_shard(rs1)
cluster.add_shard(rs2)
cluster.add_config_server(config_1)

worker_pms = [ pms[5] ]

for i in range(1):
    
    pm = worker_pms[i % len(worker_pms)]

    # Mongos
    mongos = MongoS(pm, 'mongos_%d' % i, 27017 + i, cluster.config_servers, version='2.0.7')
    cluster.add_mongos(mongos)

# The last_phase is defined only after gen_command.
cluster.gen_command(1)

#
# SETUP BASE COLLECTION
#

shard_coll_script = \
"""

var coll = db.getMongo().getCollection("foo.bar");
var admin = db.getMongo().getDB("admin");

print( "Enabling sharding..." )

printjson( admin.runCommand({ enableSharding : coll.getDB() + "" }) )
printjson( admin.runCommand({ shardCollection : coll + "", key : { _id : 1 } }) )
 
print( "Sharding enabled." )

"""
init_shell = MongoShell(pms[5], 'shell_enable_sharding', cluster.mongoses[0], shard_coll_script, version='2.0.7')
init_shell.gen_command(cluster.last_phase + 1)


#
#worker_phase = cluster.last_phase + 1
#for worker in old_workers:
#    worker.gen_command(worker_phase)
#
## Mongostat
#mongostat = Mongostat(cluster.mongoses[0].proc_mgr, 'mongostat',
#                      cluster.mongoses[0])
#mongostat.gen_command(cluster.last_phase + 1)
#
## MMS Agent
#api_key = '3b37f6048268296c98592c48c41c5f64'
#mms = MMSAgent(pms[4], 'mms', api_key, cluster)
#mms.gen_command(cluster.last_phase + 1)
#
#worker_pms = pms[8:15]
## Wait for a while before new workers are added.
#AddPhaseChecker(lambda : waiting_for(10 * 60), worker_phase)
## More mongos and workers.
#more_worker_phase = worker_phase + 1
#new_mongoses = []
#new_workers = []
#for i in range(30):
#    pm = worker_pms[i % len(worker_pms)]
#    port = 22000 + i
#    mongos = Mongos(pm, 'mongos_%d' % port, port, cluster.config_servers)
#    mongos.gen_command(more_worker_phase)
#    new_mongoses.append(mongos)
#    worker = LoadTester(pm, 'worker_%d' % port, mongos, ['test.foo', 'test.bar'])
#    new_workers.append(worker)
#    worker.gen_command(more_worker_phase + 1)
