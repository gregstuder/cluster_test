"""Provisioning on local network, AWS or other cloud provider."""

import datetime
import time
import json
import os
import sys

import boto.ec2 as ec2
from boto.s3.connection import S3Connection
from boto.s3.key import Key

CLUSTER_TEST_KEY = 'purpose'
CLUSTER_TEST_VALUE = 'cluster_test_framework'
TEST_NAME_KEY = 'test_name'
MACHINE_TYPE_KEY = 'machine_type'
COUNTER_KEY = 'machine_counter'
BUCKET_NAME = 'cluster-test-metadata'
USER_DATA_FILE = os.path.join(os.path.dirname(__file__), 'aws_setup_host.sh')

# Regions
REGION_US_EAST_1 = "us-east-1" # N. Virginia
REGION_US_WEST_2 = "us-west-2" # Oregon
REGION_US_WEST_1 = "us-west-1" # N. California
REGION_EU_WEST_1 = "eu-west-1" # Ireland
REGION_AP_SOUTHEAST_1 = "ap-southeast-1" # Singapore
REGION_AP_NORTHEAST_1 = "ap-northeast-1" # Tokyo
REGION_SA_EAST_1 = "sa-east-1" # Sao Paolo

# Types
TYPE_SMALL_32 = "small32" # 32bit
TYPE_SMALL = "small" # 64bit
TYPE_LARGE = "large"

# AMI types, instance store
AMI_TYPES = {
        REGION_US_EAST_1 : {
            TYPE_SMALL_32: "ami-b6cd60df", # 32bit
            TYPE_SMALL: "ami-94cd60fd", # 64bit
            TYPE_LARGE: "ami-94cd60fd"
        },
        REGION_US_WEST_2 : {
            TYPE_SMALL_32: "ami-b0da5580", # 32bit
            TYPE_SMALL: "ami-bada558a", # 64bit
            TYPE_LARGE: "ami-bada558a"
        },
        REGION_US_WEST_1 : {
            TYPE_SMALL_32: "ami-7b4c693e", # 32bit
            TYPE_SMALL: "ami-074c6942", # 64bit
            TYPE_LARGE: "ami-074c6942"
        },
        REGION_EU_WEST_1 : {
            TYPE_SMALL_32: "ami-6b55511f", # 32bit
            TYPE_SMALL: "ami-53555127", # 64bit
            TYPE_LARGE: "ami-53555127"
        },
        REGION_AP_SOUTHEAST_1 : {
            TYPE_SMALL_32: "ami-2a0b4a78", # 32bit
            TYPE_SMALL: "ami-d40b4a86", # 64bit
            TYPE_LARGE: "ami-d40b4a86"
        },
        REGION_AP_NORTHEAST_1 : {
            TYPE_SMALL_32: "ami-3019aa31", # 32bit
            TYPE_SMALL: "ami-3419aa35", # 64bit
            TYPE_LARGE: "ami-3419aa35"
        },
        REGION_SA_EAST_1 : {
            TYPE_SMALL_32: "ami-e036e8fd", # 32bit
            TYPE_SMALL: "ami-f236e8ef", # 64bit
            TYPE_LARGE: "ami-f236e8ef"
        }
}

INSTANCE_TYPES = {
    TYPE_SMALL_32: "m1.small",
    TYPE_SMALL: "m1.small",
    TYPE_LARGE: "m1.large"
}

class Machine(object):
    """Represent a machine on AWS or locally.

    Attributes:
        host: (str) The IP or DNS hostname.
        user_name: (str) User name for SSH.
        machine_options: (MachineOptions)
            Description of the machine's location and type, maybe None.
    """
    def __init__(self, host, user_name, options=None):
        self.host = host
        self.user_name = user_name
        self.machine_options = options

def get_ami_user(ami):
    
    if ami in ['ami-3c994355']:
        return 'ubuntu'
        
    else:
        for k, v in AMI_TYPES.items():
            for k2, v2 in AMI_TYPES[k].items():
                if ami == v2: return 'ec2-user'
    
    # Make sure we know our user for the ami
    assert False
    return None

class MachineOptions(object):
    """Abstract options about a machine.

    Attributes:
        machine_type: (str) The type of AWS instance which defines the AMI.
        region: (str) The region of AWS instance.
        ami: (str) AWS AMI.
        staging: (boolean) Use setup bash script.
    """
    def __init__(self, machine_type=TYPE_LARGE, region=REGION_US_EAST_1,
                 ami=None, staging=True, user_name=None):
        self.machine_type = machine_type
        self.region = region
        if ami is not None:
            self.ami = ami
        else:
            # Use default AMI.
            self.ami = AMI_TYPES[region][machine_type]
        
        if user_name == None:
            self.user_name = get_ami_user(self.ami)
        else:
            self.user_name = user_name
            
        self.staging = staging
        
    def add_to_reservation(self, doc):
        doc['machine_type'] = self.machine_type
        doc['region'] = self.region
        doc['ami'] = self.ami
        
    def __hash__(self):
        return (str(self.machine_type) + ':' + str(self.region) + ":" + str(self.ami)).__hash__() 
    
class ProvisioningError(Exception):
    """Error in provisioning."""
    def __init__(self, msg):
        super(ProvisioningError, self).__init__(msg)
        self.msg = msg

    def __str__(self):
        return repr(self.msg)

class AWS(object):
    
    """Provisioning on AWS."""
    def __init__(self,
                 test_name,
                 key_name=None,
                 credentials_dir=None,
                 access_key_id=None,
                 secret_access_key=None):
        
        self.test_name = test_name
        self._s3_url = ('http://cluster-test-metadata.s3.amazonaws.com/'
                        + test_name)
        
        self._key_name = key_name
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        
        if(credentials_dir != None):
            self.load_credentials_from_file(credentials_dir)
        
        # Counter of instances.
        self._machine_counter = 0
        self._ts = str(datetime.datetime.now())
        
        self._used_options = set()
        self._all_instances = self._get_all_instances()
        self._used_instances = {}

    def load_credentials_from_file(self, credentials_dir):
        
        access_key_file = os.path.join(credentials_dir, "access-key.apiuser")
        secret_access_key_file = os.path.join(credentials_dir, "access-key.apikey")
        
        ssh_key_file = None
        for filename in os.listdir(credentials_dir):
            if os.path.splitext(filename)[1] == ".privatekey":
                ssh_key_file = filename
                break
            
        assert ssh_key_file
        self._key_name = os.path.splitext(ssh_key_file)[0]
        self._key_file = os.path.realpath(os.path.join(credentials_dir, ssh_key_file))
        
        with open(access_key_file, 'r') as f:
            self._access_key_id = f.readline().strip()
            
        with open(secret_access_key_file, 'r') as f:
            self._secret_access_key = f.readline().strip()
            
        #print(self._access_key_id)
        #print(self._secret_access_key)
        #print(self._key_name)
        #print(self._key_file)
        
        # HACK - TODO: Make this better
        if sys.modules["console_config"] != None:
            sys.modules["console_config"]._key_file = self._key_file
        
    def get_machine(self, options):
        return self.get_machines(options, number=1)[0]

    def get_machines(self, machine_options, number=1):
        """Reuse existing instances if possible or run new instances.

        This function is a part of the public interface of provisioner, and
        should be supported in provisioners for other cloud provider, like
        Azure.

        Parameters:
            options: (MachineOptions) Options of machine.
            number: (int) The number of machine.

        Return:
            (list of Machine) new existing machines.
        """
        
        print "Checking existing instances on AWS..."
        
        existing_instances = self._get_instances(machine_options)
            
        used_instances = []    
        if machine_options in self._used_instances:
            used_instances = self._used_instances[machine_options]
        else:
            self._used_instances[machine_options] = used_instances
        
        existing_instances = \
            [ i for i in existing_instances if i.id not in used_instances ]
        
        ips = None
        if len(existing_instances) < number:
            
            # Get new instances from AWS.
            new_instances = self._run_instances(machine_options, number - len(existing_instances))
            self._register_instances(new_instances, machine_options)
            self._all_instances.extend(new_instances)
            
            existing_instances.extend(new_instances)
                
        assert len(existing_instances) >= number
                
        self._wait_pending_instances(machine_options, existing_instances)
        
        self._used_instances[machine_options].extend(existing_instances)
        
        print "Now using %d instances out of %d." % \
            (len(self._get_all_used_instances()), len(self._all_instances))
        
        ips = [i.public_dns_name for i in existing_instances]
        return [Machine(ip, machine_options.user_name, machine_options) for ip in ips]
    
    

    def _get_all_instances(self, group_by_region=False):
        """ Get full set of instances used in cluster test """
        
        regional_instances = {}
        
        doc = self._get_s3_content(self.test_name)
                        
        
        reservation_docs = doc['reservations'] if doc != None and 'reservations' in doc else []
        
        for reservation_doc in reservation_docs:
            
            machine_docs = reservation_doc['machines'] if 'machines' in reservation_doc else []
            
            for machine_doc in machine_docs:
                
                # DEFAULT FOR LEGACY
                region = REGION_US_EAST_1
                if 'region' in machine_doc: region = machine_doc['region']
                                
                if not region in regional_instances:
                    regional_instances[region] = []
                
                regional_instances[region].append(machine_doc)
        
        instances = []
        if group_by_region: instances = {}
        
        for region, machine_docs in regional_instances.items():
            
            conn = ec2.connect_to_region(region,
                                         aws_access_key_id=self._access_key_id,
                                         aws_secret_access_key=self._secret_access_key)
                    
            filters = {'tag:' + CLUSTER_TEST_KEY: CLUSTER_TEST_VALUE,
                       'tag:' + TEST_NAME_KEY: self.test_name}
            
            if group_by_region: instances[region] = []
            
            for reservations in conn.get_all_instances(filters=filters):
                for i in reservations.instances:
                    if i.state == 'pending' or i.state == 'running':
                        
                        if group_by_region:
                            instances[region].append(i)
                        else:
                            instances.append(i)
        
        return instances

    def _get_all_used_instances(self):
        """ Get the full set of used instances """
        
        used_instances = []        
        for machine_options, instances in self._used_instances.items():
            used_instances.extend(instances)
            
        return used_instances

    def _get_instances(self, machine_options):
        """Get all instances used for cluster test.

        Parameters:
            region: (str) AWS region of instances defined above.
        Return:
            (list of boto.ec2.instance)
        """
                
        conn = ec2.connect_to_region(machine_options.region,
                                     aws_access_key_id=self._access_key_id,
                                     aws_secret_access_key=self._secret_access_key)
                
        filters = {'tag:' + CLUSTER_TEST_KEY: CLUSTER_TEST_VALUE,
                   'tag:' + TEST_NAME_KEY: self.test_name,
                   'tag:' + MACHINE_TYPE_KEY: machine_options.machine_type,
                   'image_id' : machine_options.ami }
        
        instances = []
        for reservations in conn.get_all_instances(filters=filters):
            for i in reservations.instances:
                if i.state == 'pending' or i.state == 'running':
                    instances.append(i)
        
        return instances

    def _run_instances(self, machine_options, number=1):
        """Run instances.

        Parameters:
            machine_options: (MachineOptions) The options of instance.
            number: (int) The number of instance.

        Return:
            list of instances.
        """
        conn = ec2.connect_to_region(machine_options.region,
                                     aws_access_key_id=self._access_key_id,
                                     aws_secret_access_key=self._secret_access_key)

        # Read setup bash script.
        if machine_options.staging and USER_DATA_FILE is not None:
            with open(USER_DATA_FILE, 'r') as f:
                user_data = f.read()
        else:
            user_data = None

        print "Creating instances on AWS...(don't interrupt now)...",

        # Run instance on AWS.
        reservation = conn.run_instances(machine_options.ami,
                                         min_count=number,
                                         max_count=number,
                                         key_name=self._key_name,
                                         user_data=user_data,
                                         instance_type=INSTANCE_TYPES[machine_options.machine_type])

        # Add tags to mark the instance for cluster test use.
        tags = {CLUSTER_TEST_KEY: CLUSTER_TEST_VALUE,
                TEST_NAME_KEY: self.test_name,
                MACHINE_TYPE_KEY: machine_options.machine_type}
        
        ids = [i.id for i in reservation.instances]
        conn.create_tags(ids, tags)
        
        for i in reservation.instances:
            self._machine_counter += 1
            i.add_tag(COUNTER_KEY, str(self._machine_counter))

        print "done"

        # Wait until instance is running.
        instances = self._wait_pending_instances(machine_options,
                                                 reservation.instances)

        for i in instances:
            print i.state, i.public_dns_name
        
        return instances

    def _wait_pending_instances(self, machine_options, instances):
        """Wait until instance is running.

        Parameters:
            region: (str) AWS region of instances defined above.

        Return:
            (list of boto.ec2.instance) Instances with up-to-date status.
        """
        conn = ec2.connect_to_region(machine_options.region,
                                     aws_access_key_id=self._access_key_id,
                                     aws_secret_access_key=self._secret_access_key)

        done = False
        first_time = True
        ids = [i.id for i in instances]
        
        while not done:
            
            all_instances = []
            for r in conn.get_all_instances(ids):
                all_instances.extend(r.instances)
                
            done = all(i.state == 'running' for i in all_instances)
            if not done:
                if first_time:
                    print ("Waiting for %d instance(s) to start..."
                                % len(ids)),
                    first_time = False
                time.sleep(1)
                
        if not first_time:
            print 'done'
            
        return all_instances

    def _register_instances(self, instances, machine_options):
        """Register new instances into S3 document.

        Parameters:
            instances: (list of boto.ec2.instance)
                The instances used in cluster test.
            machine_options: (MachineOptions)
                Options of instances.
        """
        
        reservation_doc = {}
                
        machine_options.add_to_reservation(reservation_doc)
        
        machine_docs = []
        # Record every instance.
        for i in instances:
            d = {'id' : i.id,
                 'public_dns_name' : i.public_dns_name,
                 'counter' : i.tags.get(COUNTER_KEY),
                 'region' : machine_options.region}
            
            machine_docs.append(d)
                    
        reservation_doc['machines'] = machine_docs
        
        doc = self._get_s3_content(self.test_name)
        
        if doc is None or doc['ts'] != self._ts:
            # The first time to access the document.
            doc = {}
            doc['ts'] = self._ts
            doc['reservations'] = [reservation_doc]
        else:
            doc['reservations'].append(reservation_doc)
            
        # Write to S3
        self._set_s3_content(self.test_name, doc)
        print "See %s" % self._s3_url

    def terminate_all(self):
        """Terminate all instances for this cluster test.

        This function is a part of the public interface of provisioner.
        """
        
        regional_instances = self._get_all_instances(group_by_region=True)
        
        if len(regional_instances.items()) == 0:
            print("No running instances to terminate.")
            return
        
        for region, instances in regional_instances.items():
            
            if len(instances) == 0:
                print "No running instances for test in region %s." % region
                continue
            
            print ("Will terminate %d instance(s) in %s... " % (len(instances), region)),
            
            conn = ec2.connect_to_region(region,
                                         aws_access_key_id=self._access_key_id,
                                         aws_secret_access_key=self._secret_access_key)
            
            conn.terminate_instances([i.id for i in instances])
            print "done."
        
        self._delete_s3_content(self.test_name)

    def _get_s3_content(self, key):
        """Get json content for key from S3 and deserialize it.

        Parameters:
            key: (str)

        Return:
            (dict) The deserialized json document.
        """
        s3_conn = S3Connection(self._access_key_id, self._secret_access_key)
        bucket = s3_conn.get_bucket(BUCKET_NAME)
        k = Key(bucket)
        k.key = key
        if k.exists():
            json_content = k.get_contents_as_string()
            obj = json.loads(json_content)
        else:
            obj = None
        return obj

    def _set_s3_content(self, key, obj):
        """Serialize object to json and write to S3.

        Parameters:
            key: (str)
            obj: (dict) Used to be serialization.
        """
        s3_conn = S3Connection(self._access_key_id, self._secret_access_key)
        bucket = s3_conn.get_bucket(BUCKET_NAME)
        k = Key(bucket)
        k.key = key
        json_content = json.dumps(obj)
        k.set_contents_from_string(json_content,
                headers={'Content-Type': 'application/json'})
        k.make_public()
        print "Set S3 content for", key
        # Generate public URL, so that self.S3_url is available.
        k.generate_url(60 * 60 * 24 * 365 * 100, query_auth=False,
                             force_http=True)

    def _delete_s3_content(self, key):
        """Delete content in S3.

        Parameters:
            key: (str)
        """
        s3_conn = S3Connection(self._access_key_id, self._secret_access_key)
        bucket = s3_conn.get_bucket(BUCKET_NAME)
        bucket.delete_key(key)

