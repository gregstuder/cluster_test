"""
This config file defines a cluster test, and is run via the cluster test console.
"""

# ALL PATHS RELATIVE TO THE CONSOLE ROOT

# Set resource path - this information will get uploaded to all remote servers
RemoteResourcePath('./remote_resources')

# These downloadable resources will be attached to the remote resources at the subdirectories
# indicated
for version in [ '2.0.7' ]:
    RemoteResourceDownload('http://downloads.mongodb.org/linux/mongodb-linux-x86_64-%s.tgz' % version, 'mongo-%s' % version)

# Define where certain binary versions are located
for version in [ '2.0.7' ]:
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

test_name = os.path.split(os.getcwd())[1]
provisioner = provisioning.AWS(test_name, credentials_dir='./aws_test')
SetProvisioner(provisioner)

# Setup default options, us-east-1, large, 64bit
options = provisioning.MachineOptions()

# Machines is the list of IPs of all instances.
machines = provisioner.get_machines(options, number=5)

# Setup process managers for all IPs
pms = [ProcMgr(m) for m in machines]

#
# SETUP MACHINES
#

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
    AddSetupScript(pm, misc_script)
    
    # Make sure setup is complete
    AddWaitStaging(pm)

#
# SETUP LOGGING
#

SetStatsServer(pms[4])

#
# SETUP CLUSTER
#


shard_0 = MongoD(pms[0], 'shard0', 27017, version='2.0.7')
shard_1 = MongoD(pms[1], 'shard1', 27017, version='2.0.7')

config_0 = MongoD(pms[2], 'config_0', 27017, version='2.0.7', is_configsvr=True)

cluster = Cluster()
cluster.add_shard(shard_0)
cluster.add_shard(shard_1)
cluster.add_config_server(config_0)

mongos = MongoS(pms[3], 'mongos_0', 27017, cluster.config_servers, version='2.0.7')
cluster.add_mongos(mongos)

# The last_phase is defined only after gen_command.
cluster.gen_command(GetNextPhase())

load_shell = MongoShell(pms[3], 'shell_start_load', cluster.mongoses[0], \
                        './driver.js', isFile=True, version='2.0.7')

load_shell.gen_command(cluster.last_phase + 1)


