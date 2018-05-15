import logging
import yaml
import boto3
from os import path
from datetime import datetime
from dateutil.relativedelta import relativedelta

logging.basicConfig(level=logging.INFO, filename="shutter.log",
                    format="%(asctime)s - %(name)s [%(levelname)s] - %(message)s",
                    datefmt='%m/%d/%Y %H:%M:%S')

log = logging.getLogger(__name__)


class Instance(dict):

    def __init__(self, instance, *args, **kwargs):
        self.instance = instance
        super(Instance, self).__init__(*args, **kwargs)

    @property
    def name(self):
        return self.tags["Name"]

    @property
    def tags(self):
        return {t["Key"]: t["Value"] for t in self.instance.tags}

    def getVolume(self, volume):
        """
        Queries EC2 for the root device (/dev/sda1 by default) of an instance

        :type device: string
        :param device: The root device if not the default of /dev/sda1

        :rtype: ec2.Volume
        :return: The root volume, or None
        """
        q = list(self.instance.volumes.filter(
            Filters=[
                {"Name": "attachment.device",
                 "Values": [volume]}
            ]
        ))
        return q[0] if len(q) else None

    def getVolumeSnapshots(self, volume, status=None):
        """
        Queries EC2 for a list of snapshots for a given device

        :type device: ec2.Volume
        :param device: The EC2 volume to get the snapshots for
        :type status: boolean
        :param status: Optional snapshot status. One of ["pending", "completed"]

        :rtype: list
        :return: a list of snapshots for a given device
        """
        if isinstance(volume, str):
            volume = self.getVolume(volume)
        if not volume:
            return []
        if status:
            return list(volume.snapshots.filter(
                Filters=[
                    {"Name": "status",
                     "Values": [status]}
                ]
            ))
        else:
            return list(volume.snapshots.all())


class Shutter(object):
    """
    The shutter object gets configs and instances from files and provides
    snapshot management tools based on those configs and instances.

    :type config_file: string
    :param config_file: the path to the config file if different than config.yml
    :type instance_file: string
    :param instance_file: the path to the instance file if different than config.yml
    """
    def __init__(self, config_file='config.yml', instance_file='instances.yml'):
        self.ec2 = dict()
        self.loadConfig(config_file)

        # Default log level is INFO (from above)
        loglevel = getattr(logging, self.config.get("LogLevel", "INFO").upper())
        if isinstance(loglevel, int):
            log.setLevel(loglevel)
        else:
            log.error("LogLevel config option ({}) is invalid, defaulting to INFO".format(self.config.get("LogLevel")))

        default_region = self.config.get("DefaultAWSRegion")
        self.profile = self.config.get("DefaultAWSProfile", "default")
        self.initRegion(default_region)
        self.loadInstances(instance_file)

    def loadInstances(self, instance_file):
        """
        Uses the yaml parser to import instances into the object

        :type instance_file: string
        :param instance_file: The yaml file containing instance details

        :rtype: boolean
        :return: False if the file does not exist, True otherwise
        """
        self.instances = []
        if not path.exists(instance_file):
            log.error("{} not found".format(instance_file))
            return False
        with open(instance_file) as f:
            instances = yaml.load(f.read())

        # get instance by id or Name tag
        for name, values in instances.items():
            region = values.get('AWSRegion', self.config["DefaultAWSRegion"])
            if 'instanceId' in values:
                instance = Instance(self.getInstanceById(values['instanceId'], region), values)
            elif 'instanceName' in values:
                instance = Instance(self.getInstanceByName(values['instanceName'], region), values)
            else:
                log.error("{} does not have an identifier (name or id)".format(name))
                continue
            if not instance.instance:
                if 'instanceId' in values:
                    log.error("An instance with ID {} in region {} was not found".format(values['instanceId'], region))
                elif 'instanceName' in values:
                    log.error("An instance with Name {} in region {} was not found".format(values['instanceName'], region))
                continue

            self.initRegion(region)
            self.instances.append(instance)
        return True

    def loadConfig(self, config_file):
        """
        Uses the yaml parser to import configuration options into the object

        :type instance_file: string
        :param instance_file: The yaml file containing configuration details

        :rtype: boolean
        :return: False if the file does not exist, True otherwise
        """
        if not path.exists(config_file):
            log.error("{} not found".format(config_file))
            return False
        with open(config_file) as f:
            self.config = yaml.load(f.read())
        return True

    def initRegion(self, region):
        """
        Initialize an EC2 region object and add it to the region list.
        More often than not this list will of be of size one, but this way
        shutter can be run across multiple regions by specifying in the instance
        configuration

        :type region: string
        :param region: the region name to initialize
        """
        if self.ec2.get(region, None):
            log.debug("Region {} has already been initialized".format(region))
            return
        s = boto3.Session(profile_name=self.profile)
        self.ec2[region] = s.resource('ec2', region_name=region)

    def getInstanceRootVolumeSnapshots(self, instance, shutter_only=False):
        """
        Retrieves and sorts device snapshots for an instance

        :type instance: ec2.Instance
        :param instance: The EC2 instance object to get snapshots for
        :type shutter_only: boolean
        :param shutter_only: Enable a check to retrieve shutter snapshots only.
                             Needed for automatic snapshot management without
                             deleting non-shutter managed snapshots

        :rtype: list
        :return: list of snapshots for the root volume of the given EC2 instance
        """
        devname = instance.get('rootDevice', self.config.get("DefaultRootDevice"))
        s = instance.getVolumeSnapshots(devname)
        if shutter_only:
            s = [i for i in s if "Shutter" in i.description]
        s.sort(key=lambda i: i.meta.data["StartTime"])
        return s

    def pruneSnapshots(self, instance):
        """
        Identifies and deletes old snapshots based on a history size. Only deletes
        snapshots managed by Shutter

        :type instance: ec2.Instance
        :param instance: The EC2 instance to prune the snapshots of
        """
        snapshots = self.getInstanceRootVolumeSnapshots(instance, True)
        snapshots.sort(key=lambda s: s.meta.data['StartTime'])
        histsize = instance.get("historySize", self.config['DefaultHistorySize'])
        if len(snapshots) > histsize:
            to_delete = snapshots[:histsize-1]
            for snap in to_delete:
                snap.delete()

    def run(self):
        """
        For all valid instances from the instances file, check if a new snapshot
        needs to be created and also prune old snapshots if required
        """
        for i in self.instances:
            self.runOne(i)

    def runOne(self, instance):
        """
        For a single instance in the instances file, check if a new snapshot
        needs to be created and also prune old snapshots if required

        :type instance: dict
        :param instance: Contains the ec2.Instance object as well as config data
                         from the instances file
        """
        self.snapshotInstanceWithFrequency(instance)
        prune = instance.get("deleteOldSnapshots", self.config["DefaultDeleteOldSnapshots"])
        if prune:
            self.pruneSnapshots(instance)

    def snapshotInstance(self, instance):
        """
        Snapshots the given instance's root volume

        :type instance: dict
        :param instance: Contains the ec2.Instance object as well as config data
                         from the instances file
        """
        devname = instance.get('rootDevice', self.config.get("DefaultRootDevice"))
        desc = "Shutter automatically managed snapshot of {} ({})".format(instance.name, instance.instance.id)
        volume = instance.getVolume(devname)
        if not volume:
            log.error("Volume {} not found for instance").format(devname, instance.name)
        else:
            volume.create_snapshot(Description=desc)

    def snapshotInstanceWithFrequency(self, instance):
        """
        Snapshots the given instance's root volume if it needs to be snapshotted
        based on the configured frequency of daily, weekly, or monthly

        :type instance: dict
        :param instance: Contains the ec2.Instance object as well as config data
                         from the instances file
        """
        freq = instance.get("frequency", self.config['DefaultFrequency'])
        histsize = instance.get("historySize", self.config['DefaultHistorySize'])
        snaps = self.getInstanceRootVolumeSnapshots(instance, True)

        # If there are snaps then get the latest one, if not then just take one
        # as long as the history size isn't 0
        snaps.sort(key=lambda s: s.meta.data['StartTime'], reverse=True)
        if len(snaps):
            latest = snaps[0]
        elif histsize:
            self.snapshotInstance(instance)
            return

        bt = latest.meta.data['StartTime']
        if freq == 'daily':
            bt += relativedelta(days=1)
        elif freq == 'weekly':
            bt += relativedelta(weeks=1)
        elif freq == 'monthly':
            bt += relativedelta(months=1)
        else:
            return

        # provide a 10 minute time buffer
        if datetime.now().replace(tzinfo=bt.tzinfo) >= bt+relativedelta(minutes=-10):
            self.snapshotInstance(instance)
        else:
            log.debug("Not snaphotting {} ({}) last snapshot too new with frequency {}".format(instance.name, instance.instance.id, freq))

    def getInstanceById(self, id, region):
        """
        Gets an EC2 instance by its id

        :type id: string
        :param id: The id of the instance to get
        :type region: string
        :param region: The region to look for the instance in

        :rtype: ec2.Instance
        :return: The EC2 instance with the given id or None
        """
        self.initRegion(region)
        q = list(self.ec2[region].instances.filter(
            Filters=[
                {"Name": "instance-id",
                 "Values": [id]}
            ]
        ))
        return q[0] if len(q) else None

    def getInstanceByName(self, name, region):
        """
        Gets an EC2 instance by its name tag

        :type name: string
        :param name: The name of the instance to get
        :type region: string
        :param region: The region to look for the instance in

        :rtype: ec2.Instance
        :return: The EC2 instance with the given name or None
        """
        self.initRegion(region)
        q = list(self.ec2[region].instances.filter(
            Filters=[
                {"Name": "tag:Name",
                 "Values": [name]}
            ]
        ))
        return q[0] if len(q) else None
