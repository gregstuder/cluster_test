"""This config file will be executed in console_config module run-time
envirenment.
"""

# Set bin path
# You can put binaies that will run on remote servers in this folder.
# e.g. mongod, mongos, mongostat, mms_agent/ (folder)
BinPath('/Users/syzhou/code/execbin_linux')
LogPath('/Users/syzhou/code/logs/')

# The first parameter is test name, which should be unique in all tests. The
# access credentials are the AWS test account of 10gen. You can pass them here,
# but it is recommended to store them in Evironment Variables.
# See http://boto.s3.amazonaws.com/s3_tut.html
# key_name should match the key file you give below, which is used in SSH.
provisioner = provisioning.AWS("cluster_test", key_name = 'greg')
SetProvisioner(provisioner)
KeyFile('/Users/syzhou/code/remote/aws-test/ssh-key-greg.key')

# Default options, us-east-1, large, 64bit
options = provisioning.MachineOptions()
# machines is the list of IPs of all instances.
machines = provisioner.get_machines(options, number=15)

# Process managers
pms = [ProcMgr(m) for m in machines]

# Staging
for pm in pms:
    AddWaitStaging(pm)

rs1 = Replset('rs1')
rs1_1 = Mongod(pms[0], 'rs1_1', 27017)
rs1_2 = Mongod(pms[1], 'rs1_2', 27017)
rs1_3 = Mongod(pms[1], 'rs1_3', 27018, is_arbiter=True)
rs1.add_member(rs1_1)
rs1.add_member(rs1_2)
rs1.add_member(rs1_3)

rs2 = Replset('rs2')
rs2_1 = Mongod(pms[2], 'rs2_1', 27017)
rs2_2 = Mongod(pms[3], 'rs2_2', 27017)
rs2_3 = Mongod(pms[3], 'rs2_3', 27018, is_arbiter=True)
rs2.add_member(rs2_1)
rs2.add_member(rs2_2)
rs2.add_member(rs2_3)

config_1 = Mongod(pms[4], 'config_1', 27017, is_configsvr=True)
cluster = Cluster()
cluster.add_shard(rs1)
cluster.add_shard(rs2)
cluster.add_config_server(config_1)

old_workers = []

worker_pms = pms[5:8]
for i in range(30):
    pm = worker_pms[i % len(worker_pms)]

    # Mongos
    mongos = Mongos(pm, 'mongos_%d' % i, 27017 + i, cluster.config_servers)
    cluster.add_mongos(mongos)

    # Workers
    worker = LoadTester(pm, 'worker_%d' % i, mongos, ['test.foo', 'test.bar'],
                        max_load_sleep=0.1)
    old_workers.append(worker)

# The last_phase is defined only after gen_command.
cluster.gen_command(1)

worker_phase = cluster.last_phase+1
for worker in old_workers:
    worker.gen_command(worker_phase)

# Mongostat
mongostat = Mongostat(cluster.mongoses[0].proc_mgr, 'mongostat',
                      cluster.mongoses[0])
mongostat.gen_command(cluster.last_phase+1)

# MMS Agent
api_key = '3b37f6048268296c98592c48c41c5f64'
mms = MMSAgent(pms[4], 'mms', api_key, cluster)
mms.gen_command(cluster.last_phase+1)

worker_pms = pms[8:15]
# Wait for a while before new workers are added.
AddPhaseChecker(lambda : waiting_for(10 * 60), worker_phase)
# More mongos and workers.
more_worker_phase = worker_phase + 1
new_mongoses = []
new_workers = []
for i in range(30):
    pm = worker_pms[i % len(worker_pms)]
    port = 22000 + i
    mongos = Mongos(pm, 'mongos_%d' % port, port, cluster.config_servers)
    mongos.gen_command(more_worker_phase)
    new_mongoses.append(mongos)
    worker = LoadTester(pm, 'worker_%d' % port, mongos, ['test.foo', 'test.bar'])
    new_workers.append(worker)
    worker.gen_command(more_worker_phase+1)

