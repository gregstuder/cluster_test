"""Provisioning on local network, AWS or other cloud provider."""

import datetime
import time
import json

import boto.ec2 as ec2
from boto.s3.connection import S3Connection
from boto.s3.key import Key

TAG_KEY = 'purpose'
TAG_VALUE = 'cluster_test_framework'
TEST_NAME_KEY = 'test_name'
MACHINE_TYPE_KEY = 'machine_type'
COUNTER_KEY = 'machine_counter'
BUCKET_NAME = 'cluster-test-metadata'
USER_DATA_FILE = 'aws_setup_host.sh'

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

class MachineOptions(object):
    """Abstract options about a machine.

    Attributes:
        machine_type: (str) The type of AWS instance which defines the AMI.
        region: (str) The region of AWS instance.
        ami: (str) AWS AMI.
        staging: (boolean) Use setup bash script.
    """
    def __init__(self, machine_type=TYPE_LARGE, region=REGION_US_EAST_1,
                 ami=None, staging=True):
        self.machine_type = machine_type
        self.region = region
        if ami is not None:
            self.ami = ami
        else:
            # Use default AMI.
            self.ami = AMI_TYPES[region][machine_type]
        self.staging = staging

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
                 key_name,
                 access_key_id = None,
                 secret_access_key = None):
        self.test_name = test_name
        self._s3_url = ('http://cluster-test-metadata.s3.amazonaws.com/'
                        + test_name)
        self._key_name = key_name
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        # Counter of instances.
        self._machine_counter = 0
        self._ts = str(datetime.datetime.now())
        self._used_regions = set()

    def get_machines(self, options, number=1):
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
        self._used_regions.add(options.region)
        print "Checking existing instances on AWS..."
        existing_instances = self._get_instances(options.region)
        ips = None
        if existing_instances:
            # Reuse existing instances.
            all_matched = []
            machine_counter = self._machine_counter
            for i in range(number):
                # Increase the sequence number.
                machine_counter += 1
                matched = None
                for instance in existing_instances:
                    if (instance.tags.get(COUNTER_KEY) ==
                            str(machine_counter)):
                        if (instance.tags.get(MACHINE_TYPE_KEY) ==
                                options.machine_type):
                            matched = instance
                            break
                        else:
                            # Unmatched existing instance. Cannot reuse
                            # instances.
                            print ("Warning: There are existing AWS instances "
                                   "with the same name, but they do not match "
                                   "your need.")
                            print ("You can terminate them or change the test "
                                  "name.")
                            raise ProvisioningError("Unmatched instances exist")

                if matched is not None:
                    all_matched.append(matched)
                    self._machine_counter = machine_counter
                else:
                    break # Stop trying to reuse instances.

            if len(all_matched) == number:
                # all matched.
                print "Using %d existing AWS instances." % number
                all_matched = self._wait_pending_instances(options.region,
                        all_matched)
                self._register_instances(all_matched, options)
                ips = [i.public_dns_name for i in all_matched]
            elif all_matched:
                # Some instances are satisfied but not enough.
                # Unmatched existing instance. Cannot reuse instances.
                print ("Warning: There are existing AWS instances "
                       "with the same name, but they do not match "
                       "your need.")
                print "You can terminate them or change a test name."
                raise ProvisioningError("Unmatched instances exist.")

        if ips is None:
            # Get new instances from AWS.
            instances = self._run_instances(options, number)
            # Register instances on S3 document.
            self._register_instances(instances, options)
            ips = [i.public_dns_name for i in instances]
        return [Machine(ip, 'ec2-user', options) for ip in ips]

    def _get_instances(self, region):
        """Get all instances used for cluster test.

        Parameters:
            region: (str) AWS region of instances defined above.
        Return:
            (list of boto.ec2.instance)
        """
        conn = ec2.connect_to_region(region,
                       aws_access_key_id=self._access_key_id,
                       aws_secret_access_key=self._secret_access_key)
        instances = []
        f = {'tag:'+TEST_NAME_KEY: self.test_name, 'tag:'+TAG_KEY: TAG_VALUE}
        for r in conn.get_all_instances(filters=f):
            for i in r.instances:
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
        reservation = conn.run_instances(
            machine_options.ami,
            min_count=number,
            max_count=number,
            key_name=self._key_name,
            user_data=user_data,
            instance_type=INSTANCE_TYPES[machine_options.machine_type])

        # Add tags to mark the instance for cluster test use.
        tags = {}
        tags[TAG_KEY] = TAG_VALUE
        tags[TEST_NAME_KEY] = self.test_name
        tags[MACHINE_TYPE_KEY] = machine_options.machine_type
        ids = [i.id for i in reservation.instances]
        conn.create_tags(ids, tags)
        for i in reservation.instances:
            self._machine_counter += 1
            i.add_tag(COUNTER_KEY, str(self._machine_counter))

        print "done"

        # Wait until instance is running.
        instances = self._wait_pending_instances(machine_options.region,
                reservation.instances)

        for i in instances:
            print i.state, i.public_dns_name
        return instances

    def _wait_pending_instances(self, region, instances):
        """Wait until instance is running.

        Parameters:
            region: (str) AWS region of instances defined above.

        Return:
            (list of boto.ec2.instance) Instances with up-to-date status.
        """
        conn = ec2.connect_to_region(region,
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
                    print ("waiting for %d instance(s) to start..."
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
        reservation_doc['machine_type'] = machine_options.machine_type
        reservation_doc['region'] = machine_options.region
        machine_docs = []
        # Record every instance.
        for i in instances:
            d = {}
            d['id'] = i.id
            d['public_dns_name'] = i.public_dns_name
            d['counter'] = i.tags.get(COUNTER_KEY)
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
        if not self._used_regions:
            print "no running instance with test name."
            return

        for region in self._used_regions:
            instances = self._get_instances(region)
            print ("Will terminate %d instance(s) in %s... " %
                       (len(instances), region)),
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
        k.generate_url(60*60*24*365*100, query_auth=False,
                             force_http=True)

    def _delete_s3_content(self, key):
        """Delete content in S3.

        Parameters:
            key: (str)
        """
        s3_conn = S3Connection(self._access_key_id, self._secret_access_key)
        bucket = s3_conn.get_bucket(BUCKET_NAME)
        bucket.delete_key(key)

