import logging
import yaml
import boto3
from os import path

# TODO: add logging config to config.yml
logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s - %(name)s [%(levelname)s] - %(message)s",
                    datefmt='%m/%d/%Y %H:%M:%S')

log = logging.getLogger(__name__)

class Shutter(object):

    ec2 = dict()

    def __init__(self, config='config.yml'):
        self.loadConfig(config)
        default_region = self.config.get("DefaultAWSRegion")
        self.profile = self.config.get("DefaultAWSProfile", "default")
        self.initRegion(default_region)


    def loadConfig(self, config):
        if not path.exists(config):
            log.error("{} not found".format(config))
            return False
        with open(config) as f:
            self.config = yaml.load(f.read())
        return True

    def initRegion(self, region):
        if self.ec2.get(region):
            log.info("Region {} has already been initialized".format(region))
            return
        s = boto3.Session(profile_name=self.profile)
        self.ec2[region] = s.resource('ec2', region_name=region)

    def getInstanceById(self, region, id):
        self.initRegion(region)
        q = list(self.ec2[region].instances.filter(
            Filters=[
                {"Name": "instance-id",
                 "Values": [id]}
            ]
        ))
        return q[0] if len(q) else None

    def getInstanceByName(self, region, name):
        self.initRegion(region)
        q = list(self.ec2[region].instances.filter(
            Filters=[
                {"Name": "tag:Name",
                 "Values": [name]}
            ]
        ))
        return q[0] if len(q) else None


