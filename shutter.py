import logging
import yaml
import boto3
import re
from os import path
from datetime import datetime
from dateutil.relativedelta import relativedelta
from requests.utils import CaseInsensitiveDict

SETTING_TAG = "Shutter-"

logging.basicConfig(level=logging.INFO, filename="shutter.log",
                    format="%(asctime)s - %(name)s [%(levelname)s] - %(message)s",
                    datefmt='%m/%d/%Y %H:%M:%S')

log = logging.getLogger(__name__)


class Instance(CaseInsensitiveDict):

    def __init__(self, instance, region, defaults={}):
        super(Instance, self).__init__(defaults)
        self.region = region
        self.instance = instance

        conf_tags = { k[k.startswith(SETTING_TAG) and len(SETTING_TAG):]: v.lower() for k, v in self.tags.items() if re.match(SETTING_TAG+"*", k) }
        self.update(conf_tags)

    @property
    def name(self):
        return self.tags["Name"]

    @property
    def tags(self):
        return {t["Key"]: t["Value"] for t in self.instance.tags}

    def __repr__(self):
        return "<{} in {}>".format((self.name or self.instance.id), self.region)

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

    def getRootVolumeSnapshots(self):
        """
        Retrieves and sorts device snapshots for an instance

        :rtype: list
        :return: list of snapshots for the root volume of the given EC2 instance
        """
        devname = self.get('rootdevice')
        s = self.getVolumeSnapshots(devname)
        s.sort(key=lambda i: i.meta.data["StartTime"])
        return s


class Shutter(object):
    """
    The shutter object gets configs and instances from files and provides
    snapshot management tools based on those configs and instances.

    :type config_file: string
    :param config_file: the path to the config file if different than config.yml
    :type instance_file: string
    :param instance_file: the path to the instance file if different than config.yml
    """
    def __init__(self, config_file):

        if not config_file:
            raise Exception("No config file specified.")

        self.ec2 = dict()
        self.loadConfig(config_file)

        # Default log level is INFO (from above)
        loglevel = getattr(logging, self.config.get("loglevel", "INFO").upper())
        if isinstance(loglevel, int):
            log.setLevel(loglevel)
        else:
            log.warning("LogLevel config option ({}) is invalid, defaulting to INFO".format(self.config.get("LogLevel")))

        regions = self.config.get("Regions")
        self.profile = self.config.get("AWSProfile", "default")
        self.session = boto3.Session(profile_name=self.profile)
        for r in regions:
            self.initRegion(r)
        self.populateInstances()

    def populateInstances(self):
        self.instances = []
        filt = lambda x: x['Key'] == SETTING_TAG+"Enable" and x['Value'].lower() in ['true', 'yes']
        for region, session in self.ec2.items():
            instances = list(session.instances.filter(Filters=[{"Name": "tag:{}Enable".format(SETTING_TAG), "Values": ["*"]}]))
            for i in instances:
                if filter(filt, i.tags):
                    self.instances.append(Instance(i, region, self.config["Default"]))

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
        self.ec2[region] = self.session.resource('ec2', region_name=region)

    def pruneSnapshots(self, instance):
        """
        Identifies and deletes old snapshots based on a history size. Only deletes
        snapshots managed by Shutter

        :type instance: ec2.Instance
        :param instance: The EC2 instance to prune the snapshots of
        """
        snapshots = instance.getRootVolumeSnapshots()
        snapshots.sort(key=lambda s: s.meta.data['StartTime'])
        histsize = int(instance.get("historysize"))
        if len(snapshots) > histsize:
            to_delete = snapshots[:histsize-1]
            for snap in to_delete:
                log.debug("Deleting snapshot " + snap.id)
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
        prune = instance.get("deleteoldsnapshots")
        if prune:
            self.pruneSnapshots(instance)

    def snapshotInstance(self, instance):
        """
        Snapshots the given instance's root volume

        :type instance: dict
        :param instance: Contains the ec2.Instance object as well as config data
                         from the instances file
        """
        devname = instance.get('rootdevice')
        desc = "Shutter automatically managed snapshot of {} ({})".format(instance.name, instance.instance.id)
        volume = instance.getVolume(devname)
        if not volume:
            log.error("Volume {} not found for instance").format(devname, instance.name)
        else:
            volume.create_snapshot(Description=desc)

    @staticmethod
    def _timeWithinFrequency(time, frequency, jitter_minutes=10):
        frequency = frequency.lower()
        if frequency == 'daily':
            time += relativedelta(days=1)
        elif frequency == 'weekly':
            time += relativedelta(weeks=1)
        elif frequency == 'monthly':
            time += relativedelta(months=1)
        else:
            log.error("Frequency of {} is invalid!".format(frequency))
            return False
        return datetime.now().replace(tzinfo=time.tzinfo) >= time+relativedelta(minutes=-jitter_minutes)

    def snapshotInstanceWithFrequency(self, instance):
        """
        Snapshots the given instance's root volume if it needs to be snapshotted
        based on the configured frequency of daily, weekly, or monthly

        :type instance: dict
        :param instance: Contains the ec2.Instance object as well as config data
                         from the instances file
        """
        freq = instance.get("frequency")
        histsize = instance.get("historysize")
        snaps = instance.getRootVolumeSnapshots()

        # If there are snaps then get the latest one, if not then just take one
        # as long as the history size isn't 0
        snaps.sort(key=lambda s: s.meta.data['StartTime'], reverse=True)
        if len(snaps):
            latest = snaps[0]
        elif int(histsize) > 0:
            log.debug("Snapshotting " + instance.name)
            self.snapshotInstance(instance)
            return

        bt = latest.meta.data['StartTime']

        if Shutter._timeWithinFrequency(bt, freq):
            log.debug("Snapshotting " + instance.name)
            self.snapshotInstance(instance)
        else:
            log.debug("Not snaphotting {} ({}) last snapshot too new with frequency {}".format(instance.name, instance.instance.id, freq))

    def _getInstanceById(self, id, region):
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

    def _getInstanceByName(self, name, region):
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

    def copySnapshot(self, snap, source, dest):
        client = self.session.client('ec2', region_name=dest)
        client.copy_snapshot(SourceSnapshotId=snap.id, SourceRegion=source, Description=snap.description)
