import logging
import yaml
import boto3
from os import path
from collections import defaultdict

# TODO: add logging config to config.yml
logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s - %(name)s [%(levelname)s] - %(message)s",
                    datefmt='%m/%d/%Y %H:%M:%S')

log = logging.getLogger(__name__)


# TODO: make Instance object probabaly
class Shutter(object):

    ec2 = dict()

    def __init__(self, config_file='config.yml', instance_file='instances.yml'):
        self.loadConfig(config_file)
        default_region = self.config.get("DefaultAWSRegion")
        self.profile = self.config.get("DefaultAWSProfile", "default")
        self.initRegion(default_region)
        self.loadInstances(instance_file)

    def loadInstances(self, instance_file):
        self.instances = []
        if not path.exists(instance_file):
            log.error("{} not found".format(instance_file))
            return False
        with open(instance_file) as f:
            instances = yaml.load(f.read())
        for i in instances:
            name = i.keys()[0]
            d = defaultdict(lambda: None, i.values()[0])
            region = d.get('AWSRegion', self.config["DefaultAWSRegion"])
            if d['instanceId']:
                d['instance'] = self.getInstanceById(d['instanceId'], region)
            elif d['instanceName']:
                d['instance'] = self.getInstanceByName(d['instanceName'], region)
            else:
                log.error("{} does not have an identifier (name or id)".format(name))
                continue
            if not d['instance']:
                if d['instanceId']:
                    log.error("An instance with ID {} in region {} was not found".format(d['instanceId'], region))
                if d['instanceId']:
                    log.error("An instance with Name {} in region {} was not found".format(d['instanceName'], region))
                continue
            self.initRegion(region)
            self.instances.append(d)
        return True

    def loadConfig(self, config_file):
        if not path.exists(config_file):
            log.error("{} not found".format(config_file))
            return False
        with open(config_file) as f:
            self.config = yaml.load(f.read())
        return True

    def initRegion(self, region):
        if self.ec2.get(region, None):
            log.debug("Region {} has already been initialized".format(region))
            return
        s = boto3.Session(profile_name=self.profile)
        self.ec2[region] = s.resource('ec2', region_name=region)

    def getRootDevice(self, instance, device="/dev/sda1"):
        q = list(instance.volumes.filter(
            Filters=[
                {"Name": "attachment.device",
                 "Values": [device]}
            ]
        ))
        return q[0] if len(q) else None

    def getDriveSnapshots(self, device):
        return list(device.snapshots.filter(
            Filters=[
                {"Name": "status",
                 "Values": ["completed"]}
            ]
        ))

    def getInstanceRootVolumeSnapshots(self, instance):
        return self.getDriveSnapshots(self.getRootDevice(instance))

    def pruneSnapshots(self, instance):
        snapshots = self.getInstanceRootVolumeSnapshots(instance['instance'])
        snapshots.sort(key=lambda i: i.meta.data['StartTime'])
        histsize = instance["historySize"] if instance['historySize'] else  self.config['DefaultHistorySize']
        if len(snapshots) > histsize:
            to_delete = snapshots[:histsize]
            for snap in to_delete:
                snap.delete()


    def getInstanceById(self, id, region):
        self.initRegion(region)
        q = list(self.ec2[region].instances.filter(
            Filters=[
                {"Name": "instance-id",
                 "Values": [id]}
            ]
        ))
        return q[0] if len(q) else None

    def getInstanceByName(self, name, region):
        self.initRegion(region)
        q = list(self.ec2[region].instances.filter(
            Filters=[
                {"Name": "tag:Name",
                 "Values": [name]}
            ]
        ))
        return q[0] if len(q) else None
